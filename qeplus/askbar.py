"""Our own quick-entry bar plus an answer card that streams Claude's reply below it (or above
it, when there's no room below).

Looks like Claude's pop-up (same panel painting as the toolbar). The bar takes keyboard focus
when opened; the card only updates its text, so a streaming answer never pulls focus back from
another window. Esc (or the hotkey again) closes both. Drag an empty part of the bar to move
both; where it was dropped is remembered per monitor in positions.json.
"""

from __future__ import annotations

import html
import json
import logging
import math
import os
import re
import secrets
from ctypes import wintypes as wt

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QPoint, QPointF, QRect, QRectF,
                            QSize, Qt, QTimer, QUrl, QVariantAnimation, Signal)
from PySide6.QtGui import (QColor, QCursor, QDesktopServices, QFont, QGuiApplication,
                           QKeySequence, QPainter, QPen, QPixmap, QRegion, QScreen, QShortcut,
                           QTextDocument)
from PySide6.QtWidgets import (QApplication, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QTextBrowser, QToolButton, QVBoxLayout,
                               QWidget)

from .claude_cli import discard_attachment
from .sessions import write_json
from .settings import DIR
from .toolbar import (ACCENT, BAR_H, BG_BOTTOM, BG_TOP, SHADOW, IconButton, Tip, Tween,
                      animations_enabled, floating, no_activate, panel, shadow)

WIDTH, GAP, MAX_SCREEN_FRACTION = 640, 8, 0.6  # DIP; card height cap as a share of the screen
POSITIONS = os.path.join(DIR, "positions.json")  # where the bar was dropped, per monitor
WM_ENTERSIZEMOVE, WM_EXITSIZEMOVE = 0x231, 0x232
QWIDGETSIZE_MAX = 16777215
IN, OUT = QEasingCurve.Type.OutCubic, QEasingCurve.Type.InCubic  # entering, leaving
log = logging.getLogger(__name__)
LONG_ANSWER = 8_000  # characters: past this, re-render the streaming answer less often
TEXT, MUTED, CODE_BG = "#e8e6e3", "#a6a39b", "#1f1d1b"
# opaque, unlike the bar: the card is for reading, and text behind it would show through
CARD_TOP, CARD_BOTTOM = QColor(0x33, 0x30, 0x2e), QColor(0x2a, 0x26, 0x24)
FENCE = re.compile(r"```([^\n`]*)\n(.*?)(?:\n```|\Z)", re.S)  # an unclosed fence = still streaming
STYLE = f"""
  a {{ color: {ACCENT.name()}; text-decoration: none; }}
  code {{ font-family: Consolas, 'Cascadia Mono', monospace; background-color: {CODE_BG}; }}
  pre {{ font-family: Consolas, 'Cascadia Mono', monospace; margin: 0; }}
  .q {{ color: {MUTED}; }}
  .err {{ color: #e8a08a; }}
  .thinking {{ color: {MUTED}; }}
"""


def markdown_html(md: str, codes: list[str], link: str = "copy:") -> str:
    """Markdown -> Qt rich text. Prose goes through Qt's own Markdown parser; fenced code becomes
    a dark block with a Copy link (link + N, N = index into codes)."""
    out, pos = [], 0
    for m in FENCE.finditer(md):
        out.append(_prose(md[pos:m.start()]))
        codes.append(m.group(2))
        lang = html.escape(m.group(1).strip())
        out.append(
            f'<table width="100%" cellpadding="10" cellspacing="0" bgcolor="{CODE_BG}" '
            f'style="margin-top:6px; margin-bottom:6px;"><tr><td>'
            f'<span style="color:{MUTED}; font-size:11px;">{lang or "code"}</span>'
            f'&nbsp;&nbsp;<a href="{link}{len(codes) - 1}" style="font-size:11px;">Copy</a>'
            f'<pre>{html.escape(m.group(2))}</pre></td></tr></table>')
        pos = m.end()
    out.append(_prose(md[pos:]))
    return "".join(out)


def _prose(md: str) -> str:
    if not md.strip():
        return ""
    doc = QTextDocument()
    doc.setMarkdown(md)
    body = re.search(r"<body[^>]*>(.*)</body>", doc.toHtml(), re.S)
    if not body:
        return html.escape(md)
    # Qt writes Courier New into inline code itself, which the stylesheet can't override
    return body.group(1).replace("font-family:'Courier New'", "font-family:'Consolas'")


class SafeTextBrowser(QTextBrowser):
    """Never loads anything an answer refers to (images, file: or remote resources)."""

    def loadResource(self, kind, name):
        return None


class Glyph(QWidget):
    """The clay mark at the left of the bar (our own, not Claude's logo)."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(24, 24)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(ACCENT, 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        c = 12.0
        for dx, dy in ((7, 0), (0, 7), (5, 5), (5, -5)):  # an eight-point asterisk
            p.drawLine(QPointF(c - dx, c - dy), QPointF(c + dx, c + dy))


def load_positions(path: str = POSITIONS) -> dict[str, tuple[int, int]]:
    """{monitor name: (x, y) of the bar from that monitor's work-area corner}. A missing or
    malformed file, or entry, is ignored: the bar then opens at its default place."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: (v[0], v[1]) for k, v in data.items()
            if isinstance(k, str) and isinstance(v, list) and len(v) == 2
            and all(type(n) is int and abs(n) < 100_000 for n in v)}


class Panel(QWidget):
    """A child area painted like the pop-up; the window paints its shadow in its margin."""

    def __init__(self, top=BG_TOP, bottom=BG_BOTTOM):
        super().__init__()
        self.colors = (top, bottom)

    def paintEvent(self, _):
        p = QPainter(self)
        panel(p, QRectF(self.rect()), self.devicePixelRatioF(), *self.colors, with_shadow=False)


class Grip(QWidget):
    """Six dots at the bar's left edge, faded in while the pointer is over the bar. Dragging
    it moves the bar like any empty part of it; double-clicking puts the bar back."""

    reset = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setFixedSize(10, 20)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setAccessibleName("Move the bar (double-click for the default position)")
        self.alpha = Tween(self, lambda _: self.update())

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.reset.emit()

    def paintEvent(self, _):
        if not self.alpha.value:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(self.alpha.value)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(MUTED))
        for x in (3, 7):
            for y in (4, 10, 16):
                p.drawEllipse(QPointF(x, y), 1.25, 1.25)


class Bar(Panel):
    """The input bar. Pressing an empty part of it (or the grip) and moving starts Windows' own
    move loop for the whole window; the text field, chips and buttons keep their clicks."""

    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.grip = Grip(self)
        self.pressed_at: QPoint | None = None

    def resizeEvent(self, e):
        self.grip.move(3, (self.height() - self.grip.height()) // 2)
        super().resizeEvent(e)

    def enterEvent(self, e):
        self.grip.alpha.to(1, 120)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.grip.alpha.to(0, 160, OUT)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.pressed_at = e.globalPosition().toPoint()

    def mouseMoveEvent(self, e):
        # only once it really moves, so a click or double-click stays a click
        if self.pressed_at is not None and (e.globalPosition().toPoint() - self.pressed_at
                                            ).manhattanLength() >= QApplication.startDragDistance():
            self.pressed_at = None
            self.window().windowHandle().startSystemMove()

    def mouseReleaseEvent(self, e):
        self.pressed_at = None


class Dots(QWidget):
    """Three pulsing dots while Claude thinks; they fade out as the first words fade in."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setFixedSize(40, 16)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAccessibleName("Thinking")
        self.phase = 0.0
        self.ticker = QVariantAnimation(self, startValue=0.0, endValue=1.0, loopCount=-1,
                                        duration=1200)
        self.ticker.valueChanged.connect(self._tick)
        self.alpha = Tween(self, lambda _: self.update())
        self.alpha.finished.connect(lambda: None if self.alpha.value else self.hide())
        self.hide()

    def _tick(self, v):
        self.phase = v
        self.update()

    def start(self) -> None:
        self.show()
        if animations_enabled():
            self.ticker.start()
        self.alpha.to(1, 160)

    def stop(self) -> None:
        self.ticker.stop()
        self.alpha.to(0, 160, OUT)  # hidden when it gets there

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(self.alpha.value)
        p.setPen(Qt.PenStyle.NoPen)
        running = self.ticker.state() == QAbstractAnimation.State.Running
        for i in range(3):  # a wave left to right; steady dots with animations off
            wave = 0.5 - 0.5 * math.cos(2 * math.pi * (self.phase - i * 0.15))
            c = QColor(MUTED)
            c.setAlphaF(0.3 + 0.7 * wave if running else 0.7)
            p.setBrush(c)
            p.drawEllipse(QPointF(5 + i * 10, 8), 3, 3)


class ChipReveal:
    """A chip slides open from nothing as it fades in, and collapses as it fades out."""

    def __init__(self, chip: QWidget):
        self.chip = chip
        self.t = Tween(chip, self._step)
        self.t.finished.connect(self._done)

    def show(self) -> None:
        if (not self.chip.isHidden() and self.t.value >= 1
                and self.t.state() != QAbstractAnimation.State.Running):
            return  # already open (its text may have changed: it just resizes)
        self.chip.show()
        self.t.to(1, 160)

    def hide(self) -> None:
        if not self.chip.isHidden():
            self.t.to(0, 120, OUT)

    def _step(self, v) -> None:
        self.chip.setMaximumWidth(round(self.chip.sizeHint().width() * v))
        fx = self.chip.graphicsEffect()
        if fx is None:  # only while animating: a tiny widget, removed again when done
            fx = QGraphicsOpacityEffect(self.chip)
            self.chip.setGraphicsEffect(fx)
        fx.setOpacity(v)

    def _done(self) -> None:
        self.chip.setGraphicsEffect(None)
        self.chip.setMaximumWidth(QWIDGETSIZE_MAX)
        if self.t.value <= 0:
            self.chip.hide()


class Ghost(QWidget):
    """A still of the bar and card, scaled 96% → 100%, faded and slid up 6 px while they open
    (the reverse while they close). A real window can't scale its contents, and laying them out
    again every frame would stutter. Never takes focus: the real bar underneath already has it."""

    def __init__(self):
        super().__init__()
        floating(self)
        no_activate(self)
        self.still = QPixmap()
        self.done = None
        self.t = Tween(self, lambda _: self.update())
        self.t.finished.connect(self._finish)

    def play(self, still: QPixmap, geometry: QRect, opening: bool, done=None) -> None:
        self.still, self.done = still, done
        self.setGeometry(geometry)
        self.t.stop()
        self.t.value = 0.0 if opening else 1.0
        self.show()
        self.raise_()
        if opening:
            self.t.to(1, 180, IN)
        else:
            self.t.to(0, 140, OUT)

    def cancel(self) -> None:
        self.t.stop()
        self.done = None
        self.hide()

    def _finish(self) -> None:
        done, self.done = self.done, None
        if done:
            done()
        self.hide()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        t = self.t.value
        p.setOpacity(t)
        r = QRectF(self.rect())
        c, s = r.center(), 0.96 + 0.04 * t
        p.translate(c.x(), c.y() + 6 * (1 - t))
        p.scale(s, s)
        p.translate(-c)
        p.drawPixmap(r, self.still, QRectF(self.still.rect()))


class AskWindow(QWidget):
    submitted = Signal(str)       # Enter with text (or with attachments only)
    attach = Signal(str)          # "screenshot" | "window" | "voice"
    new_requested = Signal()
    recent_requested = Signal()   # the Recent button: the app shows the list
    clipboard_accepted = Signal()  # the Attach clipboard chip was clicked
    open_in_claude = Signal()
    closed = Signal()             # Esc or the hotkey again
    copied = Signal(str)          # a code block's text, for the app to put on the clipboard

    def __init__(self, shortcuts: dict[str, str], positions_path: str = POSITIONS):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Quick Entry Plus")
        self.turns: list[list[str]] = []  # [question, answer, error]
        self.rendered: list[tuple] = []  # per turn: (inputs, html, code blocks)
        self.nonce = secrets.token_hex(8)  # Copy links carry it, so answer text can't forge one
        self.attachments: list[str] = []
        self.busy = False
        self.tip = Tip()
        # where the bar is (global, its top-left corner), and whether the card sits above it
        self.positions_path = positions_path
        self.positions = load_positions(positions_path)
        self.bar_pos: QPoint | None = None
        self.card_above = False
        self.placing = False     # moves we make ourselves don't count as the user's
        self.fresh_card = False  # the card just appeared: size it at once (it is revealed)
        self.view_target = 0
        self.ghost = Ghost()
        self.lift = Tween(self, lambda _: self.update())  # deeper shadow while dragged
        self.view_h = Tween(self, self._set_view_height)
        self.reveal = Tween(self, lambda _: self._apply_reveal(), 1.0)  # the card appearing
        self.reveal.finished.connect(self._reveal_done)
        self.glide = Tween(self, self._glide_step)
        self.glide_from = self.glide_to = QPoint()
        self.text_in = Tween(self, lambda _: self._render(), 1.0)  # the first words fading in

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW, SHADOW, SHADOW, SHADOW + 4)
        outer.setSpacing(GAP)

        # --- the bar -------------------------------------------------------------------
        self.bar = Bar()
        self.bar.grip.reset.connect(self.reset_position)
        self.bar.setFixedHeight(BAR_H)
        row = QHBoxLayout(self.bar)
        row.setContentsMargins(16, 0, 12, 0)
        row.setSpacing(6)
        row.addWidget(Glyph())
        self.input = QLineEdit(placeholderText="Ask Claude…")
        self.input.setFrame(False)
        font = QFont("Segoe UI Variable Text")
        font.setPixelSize(17)
        self.input.setFont(font)
        self.input.setStyleSheet(f"QLineEdit {{ background: transparent; color: {TEXT}; "
                                 f"selection-background-color: rgba(217,119,87,90); }}")
        palette = self.input.palette()
        palette.setColor(palette.ColorRole.PlaceholderText, QColor(MUTED))
        self.input.setPalette(palette)
        self.input.returnPressed.connect(self._submit)
        row.addWidget(self.input, 1)
        self.chip = QToolButton()
        self.chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chip.setToolTip("Remove attachments")
        self.chip.setStyleSheet(f"QToolButton {{ color: {TEXT}; background: rgba(255,255,255,18);"
                                f" border: none; border-radius: 10px; padding: 3px 9px; }}"
                                f"QToolButton:hover {{ background: rgba(255,255,255,30); }}")
        self.chip.clicked.connect(lambda: self.clear_attachments())
        self.chip.hide()
        row.addWidget(self.chip)
        # text that goes with the next question: "Selected text" or "Clipboard text"
        self.context, self.context_label = "", ""
        self.context_chip = QToolButton()
        self.context_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.context_chip.setStyleSheet(self.chip.styleSheet())
        self.context_chip.clicked.connect(self.clear_context)
        self.context_chip.hide()
        row.addWidget(self.context_chip)
        # offered when something was copied in the last 30 s: click attaches, x dismisses
        self.clip_chip = QToolButton(text="Attach clipboard")
        self.clip_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clip_chip.setStyleSheet(self.chip.styleSheet().replace(
            "rgba(255,255,255,18)", "rgba(217,119,87,40)"))
        self.clip_chip.clicked.connect(self._accept_clipboard)
        self.clip_x = QToolButton(text="✕")
        self.clip_x.setToolTip("Dismiss")
        self.clip_x.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clip_x.setStyleSheet(f"QToolButton {{ color: {MUTED}; background: transparent;"
                                  f" border: none; padding: 2px 4px; }}"
                                  f"QToolButton:hover {{ color: {TEXT}; }}")
        self.clip_x.clicked.connect(self.hide_clipboard_offer)
        for w in (self.clip_chip, self.clip_x):
            w.hide()
            row.addWidget(w)
        self.reveals = {w: ChipReveal(w) for w in (self.chip, self.context_chip, self.clip_chip,
                                                   self.clip_x)}
        self.buttons: dict[str, IconButton] = {}
        for name, label in (("screenshot", "Screenshot"), ("window", "Attach window"),
                            ("voice", "Dictate")):
            b = IconButton(name, label, "", self.tip)
            b.clicked.connect(lambda _=False, n=name: self.attach.emit(n))
            row.addWidget(b)
            self.buttons[name] = b
        self.shortcuts: list[QShortcut] = []
        self.set_shortcuts(shortcuts)
        self.recent = IconButton("history", "Recent conversations", "", self.tip)
        self.recent.clicked.connect(self.recent_requested.emit)
        row.addWidget(self.recent)
        outer.addWidget(self.bar)

        # --- the card ------------------------------------------------------------------
        self.card = Panel(CARD_TOP, CARD_BOTTOM)
        col = QVBoxLayout(self.card)
        col.setContentsMargins(18, 14, 10, 10)
        col.setSpacing(8)
        self.view = SafeTextBrowser()
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._link)
        self.view.highlighted.connect(self._hover_link)
        self.view.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.view.document().setDefaultStyleSheet(STYLE)
        body = QFont("Segoe UI Variable Text")
        body.setPixelSize(15)
        self.view.setFont(body)
        self.view.setStyleSheet(
            f"QTextBrowser {{ background: transparent; color: {TEXT}; "
            f"selection-background-color: rgba(217,119,87,90); }}"
            f"QScrollBar:vertical {{ background: transparent; width: 8px; }}"
            f"QScrollBar::handle:vertical {{ background: rgba(255,255,255,40); border-radius: 4px;"
            f" min-height: 24px; }}"
            f"QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}"
            f"QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}")
        col.addWidget(self.view)
        self.thinking = Dots(self.view.viewport())
        self.view.verticalScrollBar().valueChanged.connect(self._place_dots)
        self.foot = foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 8, 0)
        self.hint = QLabel("Esc to close")
        self.hint.setTextFormat(Qt.TextFormat.PlainText)  # hovered URLs are never parsed as HTML
        self.hint.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        self.hint.setMaximumWidth(WIDTH - 220)
        foot.addWidget(self.hint)
        foot.addStretch(1)
        for text, signal in (("New", self.new_requested), ("Open in Claude", self.open_in_claude)):
            btn = QPushButton(text)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(f"QPushButton {{ color: {TEXT}; background: rgba(255,255,255,14);"
                              f" border: 1px solid rgba(255,255,255,22); border-radius: 8px;"
                              f" padding: 4px 12px; font-size: 12px; }}"
                              f"QPushButton:hover {{ background: rgba(255,255,255,28); }}")
            btn.clicked.connect(signal.emit)
            foot.addWidget(btn)
        col.addLayout(foot)
        self.card.hide()
        outer.addWidget(self.card)

        self.setFixedWidth(WIDTH + 2 * SHADOW)
        self.render_timer = QTimer(self, singleShot=True, interval=40)  # at most ~25 renders/s
        self.render_timer.timeout.connect(self._render)

    def set_shortcuts(self, shortcuts: dict[str, str]) -> None:
        """(Re)bind the bar's local keys; called again whenever settings change."""
        for s in self.shortcuts:
            s.setEnabled(False)
            s.deleteLater()
        self.shortcuts = []
        for name, b in self.buttons.items():
            combo = shortcuts.get(name, "")
            b.shortcut = combo
            b.setAccessibleName(f"{b.label} ({combo})" if combo else b.label)
            if combo:  # local to this window: no global registration needed
                self.shortcuts.append(QShortcut(QKeySequence(combo), self,
                                                lambda n=name: self.attach.emit(n)))

    # --- opening and closing -------------------------------------------------------------

    def open(self) -> None:
        """Show at this monitor's remembered spot (or the default, top-centre of the screen
        under the cursor), with the opening animation, and take keyboard focus."""
        if not self.isVisible():
            screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
            if screen is not None:  # no screen: show where it was
                area = screen.availableGeometry()
                saved = self.positions.get(screen.name())
                pos = area.topLeft() + QPoint(*saved) if saved else self._default(area)
                self.bar_pos = self._clamp(pos, area)
                if not self.card.isHidden():
                    self._choose_side(area, self.bar_pos)
            self._fit(instant=True)
            self._appear()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def dismiss(self) -> None:
        """Hide with the closing animation: the opening one reversed, a little faster."""
        if not self.isVisible():
            return
        still = self.grab() if animations_enabled() else None
        geometry = self.geometry()
        self.hide()
        if still is not None:
            self.ghost.play(still, geometry, opening=False)

    def _appear(self) -> None:
        self.ghost.cancel()
        if not animations_enabled():
            self.setWindowOpacity(1)
            self.show()
            return
        # the real window is shown (and focused) at once, invisible, so typing works at once;
        # the still plays over it, then the real one takes its place
        self.setWindowOpacity(0)
        self.show()
        self.ghost.play(self.grab(), self.geometry(), opening=True,
                        done=lambda: self.setWindowOpacity(1))

    def hideEvent(self, e):
        self.ghost.cancel()  # (dismiss starts its own after this)
        self.setWindowOpacity(1)
        self.lift.stop()
        self.lift.value = 0.0
        self.tip.hide()
        super().hideEvent(e)

    def reset_position(self) -> None:
        """Forget every remembered spot. If the bar is open, glide it to the default one."""
        self.positions.clear()
        self._save_positions()
        screen = self._screen()
        if self.isVisible() and screen is not None and self.bar_pos is not None:
            area = screen.availableGeometry()
            target = self._default(area)
            if not self.card.isHidden():
                self._choose_side(area, target)
            self._fit()
            self._glide(target)

    # --- position --------------------------------------------------------------------------

    def _default(self, area: QRect) -> QPoint:
        return QPoint(area.center().x() - WIDTH // 2, area.top() + area.height() // 4)

    def _clamp(self, pos: QPoint, area: QRect) -> QPoint:
        """Keep the whole bar inside the work area: never off-screen or under the taskbar."""
        return QPoint(min(max(pos.x(), area.left()), area.right() + 1 - WIDTH),
                      min(max(pos.y(), area.top()), area.bottom() + 1 - BAR_H))

    def _screen(self) -> QScreen | None:
        """The monitor the bar is on (the nearest one if it's off every screen)."""
        if self.bar_pos is None:
            return self.screen() or QGuiApplication.primaryScreen()
        centre = self.bar_pos + QPoint(WIDTH // 2, BAR_H // 2)
        return QGuiApplication.screenAt(centre) or min(
            QGuiApplication.screens(), default=None,
            key=lambda s: (s.geometry().center() - centre).manhattanLength())

    def _rooms(self, area: QRect, pos: QPoint) -> tuple[int, int]:
        """Height free for the card (below, above) the bar at pos, shadows included."""
        below = area.bottom() + 1 - (pos.y() + BAR_H + GAP) - SHADOW - 4
        above = pos.y() - GAP - area.top() - SHADOW
        return below, above

    def _choose_side(self, area: QRect, pos: QPoint) -> None:
        """Below the bar, unless a full-height card wouldn't fit there and there's more room
        above. Decided when the card appears (and after a drag), never mid-answer."""
        full = int(area.height() * MAX_SCREEN_FRACTION) - BAR_H - GAP
        below, above = self._rooms(area, pos)
        above_now = below < full and above > below
        if above_now != self.card_above:
            self.card_above = above_now
            outer = self.layout()
            outer.removeWidget(self.card)
            outer.insertWidget(0 if above_now else 1, self.card)

    def _bar_top(self) -> int:
        """The bar's y inside the window."""
        if self.card_above and not self.card.isHidden():
            return SHADOW + self.card.sizeHint().height() + GAP
        return SHADOW

    def _place(self, bar_pos: QPoint | None = None) -> None:
        """Size the window to its content with the bar at bar_pos (default: where it is), so
        the card grows away from the bar whichever side it's on."""
        if bar_pos is not None:
            self.bar_pos = bar_pos
        # a child's new height reaches the card's and the window's size hints only on the next
        # event-loop pass; refresh them now, or the window keeps the old (larger) height
        for layout in (self.card.layout(), self.layout()):
            layout.invalidate()
            layout.activate()
        if self.bar_pos is None:
            self.adjustSize()
            return
        self.placing = True
        self.setGeometry(self.bar_pos.x() - SHADOW, self.bar_pos.y() - self._bar_top(),
                         self.width(), self.sizeHint().height())
        self.placing = False
        self._apply_reveal()

    def moveEvent(self, e):
        super().moveEvent(e)
        if not self.placing and self.bar_pos is not None:  # dragged (or moved by Windows)
            self.bar_pos = self.pos() + QPoint(SHADOW, self._bar_top())

    def nativeEvent(self, kind, message):
        msg = wt.MSG.from_address(int(message))
        if msg.message == WM_ENTERSIZEMOVE:  # Windows' move loop (from startSystemMove)
            self.tip.hide()
            self.glide.stop()
            self.lift.to(1, 120)
        elif msg.message == WM_EXITSIZEMOVE:
            self.lift.to(0, 220)
            self._settle()
        return False, 0

    def _settle(self) -> None:
        """After a drag: keep the bar inside the work area of the monitor it was dropped on,
        put the card on the side with room, and remember the spot for that monitor."""
        screen = self._screen()
        if screen is None or self.bar_pos is None:
            return
        area = screen.availableGeometry()
        target = self._clamp(self.bar_pos, area)
        if not self.card.isHidden():
            self._choose_side(area, target)
        self.positions[screen.name()] = (target.x() - area.left(), target.y() - area.top())
        self._save_positions()
        self._fit()
        self._glide(target)

    def _glide(self, target: QPoint) -> None:
        self.glide.stop()
        if target == self.bar_pos:
            return
        self.glide_from, self.glide_to = QPoint(self.bar_pos), target
        self.glide.value = 0.0
        self.glide.to(1, 180)

    def _glide_step(self, t) -> None:
        a, b = self.glide_from, self.glide_to
        self._place(QPoint(round(a.x() + (b.x() - a.x()) * t), round(a.y() + (b.y() - a.y()) * t)))

    def _save_positions(self) -> None:
        try:
            write_json(self.positions_path, {k: list(v) for k, v in self.positions.items()})
        except OSError as e:
            log.warning("couldn't save the bar's position: %s", e)

    def paintEvent(self, _):
        """The panels' shadows, in the window's margin (deeper while dragged)."""
        p = QPainter(self)
        shadow(p, QRectF(self.bar.geometry()), self.lift.value)
        if not self.card.isHidden():
            shadow(p, QRectF(self._card_shown()), self.lift.value)

    # --- the card appearing and growing ---------------------------------------------------

    def _card_shown(self) -> QRect:
        """The part of the card revealed so far (window coordinates): it opens from the bar."""
        g = self.card.geometry()
        h = round(g.height() * self.reveal.value)
        return QRect(g.x(), g.bottom() + 1 - h if self.card_above else g.y(), g.width(), h)

    def _apply_reveal(self) -> None:
        if self.reveal.value >= 1:
            self.card.clearMask()
        else:
            shown = self._card_shown().translated(-self.card.pos())
            self.card.setMask(QRegion(shown) if shown.height() > 0 else QRegion(0, 0, 1, 1))
        self.update()

    def _reveal_done(self) -> None:
        if self.reveal.value <= 0:  # closed: gone for good
            self.card.hide()
            self.reveal.value = 1.0
            self._place()
        self._apply_reveal()

    def _show_card(self) -> None:
        if not self.card.isHidden():
            closing = (self.reveal.state() == QAbstractAnimation.State.Running
                       and not self.reveal.endValue())
            if closing:  # open it again from where it got to
                self.reveal.to(1, 200, IN)
            return
        screen = self._screen()
        if screen is not None and self.bar_pos is not None:
            self._choose_side(screen.availableGeometry(), self.bar_pos)
        self.fresh_card = True
        self.card.show()
        if self.isVisible():
            self.reveal.value = 0.0
            self.reveal.to(1, 200, IN)

    def _hide_card(self) -> None:
        if self.card.isHidden():
            return
        if self.isVisible():
            self.reveal.to(0, 140, OUT)  # hidden when it gets there
        else:
            self.card.hide()
            self._place()

    def _set_view_height(self, v) -> None:
        self.view.setFixedHeight(round(v))
        self._place()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.closed.emit()
        else:
            super().keyPressEvent(e)

    def changeEvent(self, e):
        # clicked away before asking anything: behave like Claude's pop-up and go away;
        # once there's an answer the card stays on top until Esc
        if (e.type() == e.Type.ActivationChange and not self.isActiveWindow()
                and not self.turns and not self.attachments and not self.context
                and self.isVisible()):
            QTimer.singleShot(0, lambda: None if self.isActiveWindow() else self.closed.emit())
        super().changeEvent(e)

    # --- input ---------------------------------------------------------------------------

    def _submit(self) -> None:
        text = self.input.text().strip()
        if self.busy or not (text or self.attachments or self.context):
            return
        self.input.clear()
        default = "Explain this." if self.context else "What's in this image?"
        self.submitted.emit(text or default)

    def insert_text(self, text: str) -> None:
        current = self.input.text()
        self.input.insert(("" if not current or current.endswith(" ") else " ") + text)

    def add_attachment(self, path: str) -> None:
        self.attachments.append(path)
        n = len(self.attachments)
        self.chip.setText(f"{n} image{'s' if n > 1 else ''}  ✕")
        self.reveals[self.chip].show()

    def set_context(self, text: str, label: str) -> None:
        """Text sent along with the next question (shown as a removable chip)."""
        self.context, self.context_label = text, label
        self.context_chip.setText(f"{label}  ✕")
        preview = text if len(text) <= 300 else text[:300] + "…"
        # always rich text with the content escaped: copied text is never interpreted as HTML
        self.context_chip.setToolTip(f'<p style="white-space:pre-wrap">{html.escape(preview)}'
                                     f'<br><br>Click to remove</p>')
        self.reveals[self.context_chip].show()

    def clear_context(self) -> None:
        self.context, self.context_label = "", ""
        self.reveals[self.context_chip].hide()

    def offer_clipboard(self, kind: str) -> None:
        self.clip_chip.setText("Attach copied image" if kind == "image" else "Attach clipboard")
        self.reveals[self.clip_chip].show()
        self.reveals[self.clip_x].show()

    def hide_clipboard_offer(self) -> None:
        self.reveals[self.clip_chip].hide()
        self.reveals[self.clip_x].hide()

    def _accept_clipboard(self) -> None:
        self.hide_clipboard_offer()
        self.clipboard_accepted.emit()

    def clear_attachments(self, delete: bool = True) -> None:
        """Drop the attachments; their PNGs are deleted unless they're about to be sent."""
        if delete:
            for path in self.attachments:
                discard_attachment(path)
        self.attachments = []
        self.reveals[self.chip].hide()

    # --- the conversation ----------------------------------------------------------------

    def begin_turn(self, question: str, images: int, extra: str = "") -> None:
        label = question + (f"  ·  {images} image{'s' if images > 1 else ''}" if images else "")
        label += f"  ·  {extra}" if extra else ""
        self.turns.append([label, "", ""])
        self.busy = True
        self._show_card()
        self.thinking.start()
        self._render()

    def add_text(self, piece: str) -> None:
        if self.turns:
            first = piece and not self.turns[-1][1]
            self.turns[-1][1] += piece
            if first:  # cross-fade: the dots fade out as the first words fade in
                self.thinking.stop()
                self.text_in.value = 0.0
                self.text_in.to(1, 180, IN)
            if not self.render_timer.isActive():
                long = len(self.turns[-1][1]) > LONG_ANSWER
                self.render_timer.start(100 if long else 40)

    def finish_turn(self) -> None:
        self.busy = False
        self.thinking.stop()
        self._render()

    def show_error(self, message: str) -> None:
        if not self.turns:
            self.turns.append(["", "", ""])
        self.turns[-1][2] = message
        self._show_card()
        self.finish_turn()

    def load_conversation(self, turns: list[list[str]]) -> None:
        """Show a saved conversation (from Recent) in the card, ready for a follow-up."""
        self.turns, self.busy = [list(t) for t in turns], False
        self._show_card()
        self._render()
        self.input.setFocus()

    def clear_conversation(self) -> None:
        self.turns, self.busy = [], False
        self.thinking.stop()
        self._hide_card()
        self.input.setFocus()

    def transcript(self) -> str:
        """The conversation as plain Markdown, for Open in Claude."""
        return "\n\n".join(f"**Me:** {q}\n\n**Claude:** {a}" for q, a, _ in self.turns if q)

    def _render(self) -> None:
        bar = self.view.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        del self.rendered[len(self.turns):]
        last = len(self.turns) - 1
        for i, (q, a, err) in enumerate(self.turns):
            thinking = not a and not err and self.busy and i == last
            fade = round(self.text_in.value, 2) if i == last else 1.0
            key = (q, a, err, thinking, fade)
            if i < len(self.rendered) and self.rendered[i][0] == key:
                continue  # unchanged: only the streaming turn is converted again
            parts, codes = [], []
            if q:  # a follow-up gets space above it: the muted question marks the new turn
                gap = ' style="margin-top:18px;"' if i else ""
                parts.append(f'<p class="q"{gap}>{html.escape(q)}</p>')
            if a:
                body = markdown_html(a, codes, f"copy-{self.nonce}:{i}:")
                if fade < 1:  # the first words fading in
                    body = f'<div style="color:rgba(232,230,227,{fade})">{body}</div>'
                parts.append(body)
            elif thinking:  # a line held for the pulsing dots drawn over it
                parts.append('<p class="thinking">&nbsp;</p>')
            if err:
                parts.append(f'<p class="err">{html.escape(err)}</p>')
            entry = (key, "".join(parts), codes)
            if i < len(self.rendered):
                self.rendered[i] = entry
            else:
                self.rendered.append(entry)
        self.view.setHtml("".join(r[1] for r in self.rendered))
        self._fit()
        if at_bottom:
            bar.setValue(bar.maximum())
        self._place_dots()

    def _place_dots(self) -> None:
        """Over the held line at the end of the card, while there's no answer text yet."""
        if self.thinking.isHidden() or not self.turns or self.turns[-1][1]:
            return
        doc = self.view.document()
        r = doc.documentLayout().blockBoundingRect(doc.lastBlock())
        top = r.top() - self.view.verticalScrollBar().value()
        self.thinking.move(round(r.left()) + 2, round(top + (r.height() - self.thinking.height()) / 2))

    def _fit(self, instant: bool = False) -> None:
        """Grow the card with its content (smoothly), up to ~60% of the screen and the room on
        its side of the bar; scroll beyond that. Then size and place the window."""
        screen = self._screen()
        if screen is None:
            return  # no screen at all (between monitor changes): fit on the next render
        if not self.card.isHidden():
            self.layout().activate()  # a card that just appeared has no width yet to wrap to
            area = screen.availableGeometry()
            doc = self.view.document()
            doc.setTextWidth(self.view.viewport().width() or WIDTH - 40)
            cap = int(area.height() * MAX_SCREEN_FRACTION) - BAR_H - GAP
            if self.bar_pos is not None:
                cap = min(cap, self._rooms(area, self.bar_pos)[self.card_above])
            col = self.card.layout()
            m = col.contentsMargins()
            chrome = m.top() + m.bottom() + col.spacing() + self.foot.sizeHint().height()
            target = max(60, min(int(doc.size().height()) + 8, cap - chrome))
            if instant or self.fresh_card or not self.isVisible():
                self.fresh_card = False
                self.view_h.stop()
                self.view_h.value = target
                self.view.setFixedHeight(target)
            elif target != self.view_target:
                self.view_h.to(target, 160, IN)  # restarted as text streams: it follows smoothly
            self.view_target = target
        self._place()

    def _link(self, url: QUrl) -> None:
        if url.scheme() == f"copy-{self.nonce}":
            try:
                turn, index = (int(n) for n in url.path().split(":"))
            except ValueError:
                return
            if 0 <= turn < len(self.rendered) and 0 <= index < len(self.rendered[turn][2]):
                self.copied.emit(self.rendered[turn][2][index])
        elif url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)
        # anything else (file:, javascript:, custom schemes, forged copy links) is ignored

    def _hover_link(self, url: QUrl) -> None:
        """Show where a link goes before it's clicked."""
        if url.isEmpty():
            self.hint.setText("Esc to close")
        elif url.scheme() == f"copy-{self.nonce}":
            self.hint.setText("Copy code")
        else:
            text = url.toDisplayString()
            self.hint.setText(text if len(text) <= 70 else text[:70] + "…")

    def sizeHint(self) -> QSize:
        return QSize(WIDTH + 2 * SHADOW, super().sizeHint().height())

