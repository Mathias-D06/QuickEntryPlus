"""Our own quick-entry bar plus an answer card that streams Claude's reply below it.

Looks like Claude's pop-up (same panel painting as the toolbar). The bar takes keyboard focus
when opened; the card only updates its text, so a streaming answer never pulls focus back from
another window. Esc (or the hotkey again) closes both.
"""

from __future__ import annotations

import html
import re
import secrets

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QColor, QCursor, QDesktopServices, QFont, QGuiApplication,
                           QKeySequence, QPainter, QPen, QShortcut, QTextDocument)
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser,
                               QToolButton, QVBoxLayout, QWidget)

from .claude_cli import discard_attachment
from .toolbar import ACCENT, BAR_H, SHADOW, IconButton, Tip, panel

WIDTH, GAP, MAX_SCREEN_FRACTION = 640, 8, 0.6  # DIP; card height cap as a share of the screen
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


class Panel(QWidget):
    """A child area painted like the pop-up; the shadow lives in the window's margin."""

    def __init__(self, top=None, bottom=None):
        super().__init__()
        self.colors = (top, bottom)

    def paintEvent(self, _):
        p = QPainter(self)
        top, bottom = self.colors
        args = (top, bottom) if top else ()
        panel(p, QRectF(self.rect()), self.devicePixelRatioF(), *args)


class AskWindow(QWidget):
    submitted = Signal(str)       # Enter with text (or with attachments only)
    attach = Signal(str)          # "screenshot" | "window" | "voice"
    new_requested = Signal()
    recent_requested = Signal()   # the Recent button: the app shows the list
    clipboard_accepted = Signal()  # the Attach clipboard chip was clicked
    open_in_claude = Signal()
    closed = Signal()             # Esc or the hotkey again
    copied = Signal(str)          # a code block's text, for the app to put on the clipboard

    def __init__(self, shortcuts: dict[str, str]):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Quick Entry Plus")
        self.turns: list[list[str]] = []  # [question, answer, error]
        self.rendered: list[tuple] = []  # per turn: (inputs, html, code blocks)
        self.nonce = secrets.token_hex(8)  # Copy links carry it, so answer text can't forge one
        self.attachments: list[str] = []
        self.busy = False
        self.thinking_dots = 0
        self.tip = Tip()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW, SHADOW, SHADOW, SHADOW + 4)
        outer.setSpacing(GAP)

        # --- the bar -------------------------------------------------------------------
        self.bar = Panel()
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
        foot = QHBoxLayout()
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
        self.dots = QTimer(self, interval=400)
        self.dots.timeout.connect(self._tick_thinking)

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
        """Show at the top third of the screen under the cursor and take keyboard focus."""
        screen = (QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen())
        if screen is not None and not self.isVisible():  # no screen: show where it was
            area = screen.availableGeometry()
            self.move(area.center().x() - self.width() // 2, area.top() + area.height() // 4)
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

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
        self.chip.show()

    def set_context(self, text: str, label: str) -> None:
        """Text sent along with the next question (shown as a removable chip)."""
        self.context, self.context_label = text, label
        self.context_chip.setText(f"{label}  ✕")
        preview = text if len(text) <= 300 else text[:300] + "…"
        # always rich text with the content escaped: copied text is never interpreted as HTML
        self.context_chip.setToolTip(f'<p style="white-space:pre-wrap">{html.escape(preview)}'
                                     f'<br><br>Click to remove</p>')
        self.context_chip.show()

    def clear_context(self) -> None:
        self.context, self.context_label = "", ""
        self.context_chip.hide()

    def offer_clipboard(self, kind: str) -> None:
        self.clip_chip.setText("Attach copied image" if kind == "image" else "Attach clipboard")
        self.clip_chip.show()
        self.clip_x.show()

    def hide_clipboard_offer(self) -> None:
        self.clip_chip.hide()
        self.clip_x.hide()

    def _accept_clipboard(self) -> None:
        self.hide_clipboard_offer()
        self.clipboard_accepted.emit()

    def clear_attachments(self, delete: bool = True) -> None:
        """Drop the attachments; their PNGs are deleted unless they're about to be sent."""
        if delete:
            for path in self.attachments:
                discard_attachment(path)
        self.attachments = []
        self.chip.hide()

    # --- the conversation ----------------------------------------------------------------

    def begin_turn(self, question: str, images: int, extra: str = "") -> None:
        label = question + (f"  ·  {images} image{'s' if images > 1 else ''}" if images else "")
        label += f"  ·  {extra}" if extra else ""
        self.turns.append([label, "", ""])
        self.busy = True
        self.card.show()
        self.dots.start()
        self._render()

    def add_text(self, piece: str) -> None:
        if self.turns:
            self.turns[-1][1] += piece
            self.dots.stop()
            if not self.render_timer.isActive():
                long = len(self.turns[-1][1]) > LONG_ANSWER
                self.render_timer.start(100 if long else 40)

    def finish_turn(self) -> None:
        self.busy = False
        self.dots.stop()
        self._render()

    def show_error(self, message: str) -> None:
        if not self.turns:
            self.turns.append(["", "", ""])
        self.turns[-1][2] = message
        self.card.show()
        self.finish_turn()

    def load_conversation(self, turns: list[list[str]]) -> None:
        """Show a saved conversation (from Recent) in the card, ready for a follow-up."""
        self.turns, self.busy = [list(t) for t in turns], False
        self.card.show()
        self._render()
        self.input.setFocus()

    def clear_conversation(self) -> None:
        self.turns, self.busy = [], False
        self.dots.stop()
        self.card.hide()
        self.adjustSize()
        self.input.setFocus()

    def transcript(self) -> str:
        """The conversation as plain Markdown, for Open in Claude."""
        return "\n\n".join(f"**Me:** {q}\n\n**Claude:** {a}" for q, a, _ in self.turns if q)

    def _tick_thinking(self) -> None:
        self.thinking_dots = (self.thinking_dots + 1) % 4
        self._render()

    def _render(self) -> None:
        bar = self.view.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        del self.rendered[len(self.turns):]
        for i, (q, a, err) in enumerate(self.turns):
            thinking = not a and not err and self.busy and i == len(self.turns) - 1
            key = (q, a, err, thinking, self.thinking_dots if thinking else 0)
            if i < len(self.rendered) and self.rendered[i][0] == key:
                continue  # unchanged: only the streaming turn is converted again
            parts, codes = [], []
            if q:  # a follow-up gets space above it: the muted question marks the new turn
                gap = ' style="margin-top:18px;"' if i else ""
                parts.append(f'<p class="q"{gap}>{html.escape(q)}</p>')
            if a:
                parts.append(markdown_html(a, codes, f"copy-{self.nonce}:{i}:"))
            elif thinking:
                parts.append(f'<p class="thinking">Thinking{"." * self.thinking_dots}</p>')
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

    def _fit(self) -> None:
        """Grow the card with its content up to ~60% of the screen; scroll beyond that."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return  # no screen at all (between monitor changes): fit on the next render
        screen = screen.availableGeometry()
        doc = self.view.document()
        doc.setTextWidth(self.view.viewport().width() or WIDTH - 40)
        cap = int(screen.height() * MAX_SCREEN_FRACTION) - BAR_H - GAP
        self.view.setFixedHeight(max(60, min(int(doc.size().height()) + 8, cap - 60)))
        self.adjustSize()

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

