"""The docked toolbar: a no-focus, always-on-top strip styled like Claude's quick entry pop-up.

Colours and metrics were sampled from a 200% screen grab of the pop-up (spikes/out/popup.png):
56 DIP tall, 16 DIP corner radius, a top-to-bottom warm grey gradient, a 1 px lighter hairline
border and #a6a39b placeholder grey. Everything is painted with QPainter (no bitmaps, no
QGraphicsEffect), so it stays crisp at any display scaling.
"""

from __future__ import annotations

import ctypes
import math
from ctypes import wintypes as wt

from PySide6.QtCore import (QAbstractAnimation, QByteArray, QEasingCurve, QPointF, QRectF, Qt,
                            QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import (QColor, QCursor, QFont, QFontMetrics, QGuiApplication,
                           QLinearGradient, QPainter, QPainterPath, QPen)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QWidget

from .win import user32
user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, wt.UINT]
GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = -20, 0x08000000, 0x80
SWP_NOSIZE, SWP_NOACTIVATE, HWND_TOPMOST = 0x1, 0x10, -1

# sampled from the pop-up
BAR_H, RADIUS, GAP = 56, 16, 8                       # DIP
BG_TOP, BG_BOTTOM = QColor(0x3e, 0x3d, 0x3c, 242), QColor(0x2a, 0x26, 0x24, 242)  # ~5% see-through
EDGE_TOP, EDGE_BOTTOM = QColor("#646362"), QColor("#53504f")
ICON, ICON_HOVER, ACCENT = QColor("#a6a39b"), QColor("#e8e6e3"), QColor("#d97757")
# not in the pop-up (it has no buttons): small lifts of its own surface
HOVER_BG, PRESSED_BG = QColor(255, 255, 255, 18), QColor(255, 255, 255, 30)
ACTIVE_BG = QColor(217, 119, 87, 36)
BUTTON, ICON_PX, PAD, SPACING, SHADOW = 32, 18, 12, 4, 14  # DIP

# Lucide icons (ISC licence, lucide.dev), 24-unit grid, drawn as strokes in the given colour
ICONS = {
    "screenshot": '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9'
                  'a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/>',
    "window": '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M10 4v4"/>'
              '<path d="M2 8h20"/><path d="M6 4v4"/>',
    "history": '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/>'
               '<path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>',
    "voice": '<path d="M12 19v3"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/>'
             '<rect x="9" y="2" width="6" height="13" rx="3"/>',
}
_svg_cache: dict[tuple[str, str], QSvgRenderer] = {}


def svg(name: str, color: QColor) -> QSvgRenderer:
    key = (name, color.name())
    if key not in _svg_cache:
        _svg_cache[key] = QSvgRenderer(QByteArray(
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color.name()}" stroke-width="1.75" stroke-linecap="round" '
            f'stroke-linejoin="round">{ICONS[name]}</svg>'.encode()))
    return _svg_cache[key]


user_animations = True  # the "animations" setting; app.py keeps it current


def animations_enabled() -> bool:
    """The setting, and Windows' Accessibility > Visual effects > Animation effects."""
    if not user_animations:
        return False
    on = wt.BOOL(True)
    user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0)  # SPI_GETCLIENTAREAANIMATION
    return bool(on.value)


class Tween(QVariantAnimation):
    """A number that eases from where it is to a target, calling on_change each frame; with
    animations off it jumps there (finished still fires)."""

    def __init__(self, parent, on_change, value: float = 0.0):
        super().__init__(parent)
        self.value, self.on_change = value, on_change
        self.valueChanged.connect(self._set)

    def _set(self, v) -> None:
        self.value = v
        self.on_change(v)

    def to(self, end: float, ms: int, curve=QEasingCurve.Type.OutCubic) -> None:
        self.stop()
        if not animations_enabled():
            self._set(float(end))
            self.finished.emit()
            return
        self.setStartValue(float(self.value))
        self.setEndValue(float(end))
        self.setDuration(ms)
        self.setEasingCurve(curve)
        self.start()


def no_activate(widget: QWidget) -> None:
    hwnd = int(widget.winId())
    ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)


def floating(widget: QWidget) -> None:
    widget.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                          | Qt.WindowType.WindowStaysOnTopHint
                          | Qt.WindowType.WindowDoesNotAcceptFocus)
    widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)


def rounded(painter: QPainter, rect: QRectF, radius: float, fill, edge_top=None, edge_bottom=None):
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    painter.fillPath(path, fill)
    if edge_top:
        grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        grad.setColorAt(0, edge_top)
        grad.setColorAt(1, edge_bottom)
        pen = QPen(grad, 1)
        pen.setCosmetic(True)  # one device pixel at every scale, like the pop-up's hairline
        painter.strokePath(path, pen)


class Tip(QWidget):
    """Small dark tooltip with a dimmer shortcut, shown under a button. Never takes focus."""

    def __init__(self):
        super().__init__()
        floating(self)
        self.label, self.shortcut = "", ""
        self.font_ = QFont("Segoe UI Variable Text")
        self.font_.setPixelSize(12)
        no_activate(self)

    def show_for(self, button: QWidget, label: str, shortcut: str):
        self.label, self.shortcut = label, shortcut
        m = QFontMetrics(self.font_)
        w = m.horizontalAdvance(label) + (m.horizontalAdvance("  " + shortcut) if shortcut else 0)
        self.resize(w + 20, m.height() + 10)
        g = button.mapToGlobal(button.rect().center())
        self.move(g.x() - self.width() // 2, button.mapToGlobal(button.rect().bottomLeft()).y()
                  + BAR_H // 2 - BUTTON // 2 + 6)
        self.show()
        self.update()

    def place(self) -> None:
        """Bottom-centre of the screen under the cursor (left where it was if there's none)."""
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(area.center().x() - self.width() // 2, area.bottom() - self.height() - 24)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        rounded(p, r, 6, QColor("#2f2c2a"), QColor("#55524f"), QColor("#4a4745"))
        p.setFont(self.font_)
        p.setPen(QColor("#e8e6e3"))
        text_rect = self.rect().adjusted(10, 0, -10, 0)
        p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   self.label)
        if self.shortcut:
            p.setPen(ICON)
            p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       self.shortcut)


class Toast(Tip):
    """A Tip used for messages: bottom-centre of the screen under the cursor, gone after 5 s.
    Emit `message` (from any thread) or call show_message (Qt thread only)."""

    message = Signal(str)

    def __init__(self):
        super().__init__()
        self.message.connect(self.show_message)
        self.timer = QTimer(self, singleShot=True, interval=5000)
        self.timer.timeout.connect(self.hide)

    def show_message(self, text: str) -> None:
        self.label, self.shortcut = text, ""
        m = QFontMetrics(self.font_)
        self.resize(m.horizontalAdvance(text) + 28, m.height() + 16)
        self.place()
        self.show()
        self.update()
        self.timer.start()


class MicIndicator(Tip):
    """Push-to-talk feedback: a pill at the bottom of the screen with a pulsing clay dot while
    listening, a spinner while transcribing. Never takes focus."""

    def __init__(self):
        super().__init__()
        self.state = "listening"
        self.phase = 0.0
        self.ticker = QVariantAnimation(self, startValue=0.0, endValue=1.0, loopCount=-1,
                                        duration=1000)
        self.ticker.valueChanged.connect(self._tick)

    def _tick(self, v):
        self.phase = v
        self.update()

    def show_state(self, state: str) -> None:
        self.state = state
        self.label = ("Listening… release to send" if state == "listening" else "Transcribing…")
        m = QFontMetrics(self.font_)
        self.resize(m.horizontalAdvance(self.label) + 48, m.height() + 18)
        self.place()
        self.ticker.start()
        self.show()
        self.update()

    def hideEvent(self, e):
        self.ticker.stop()
        super().hideEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        rounded(p, r, r.height() / 2, QColor("#2f2c2a"), QColor("#55524f"), QColor("#4a4745"))
        dot = QPointF(19, r.center().y())
        if self.state == "listening":
            c = QColor(ACCENT)
            c.setAlphaF(0.35 + 0.65 * abs(1 - 2 * self.phase))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawEllipse(dot, 4.5, 4.5)
        else:
            pen = QPen(ICON, 1.75)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(QRectF(dot.x() - 6, dot.y() - 6, 12, 12), int(-self.phase * 360 * 16),
                      270 * 16)
        p.setFont(self.font_)
        p.setPen(QColor("#e8e6e3"))
        p.drawText(self.rect().adjusted(34, 0, -12, 0),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.label)


class IconButton(QAbstractButton):
    """32 DIP square button: idle / hover (fades in) / pressed (shrinks to 95%), plus 'active'
    (a pulsing clay ring: recording) and 'busy' (a spinner in place of the icon)."""

    def __init__(self, name: str, label: str, shortcut: str, tip: Tip):
        super().__init__()
        self.name, self.label, self.shortcut, self.tip = name, label, shortcut, tip
        self.state = "idle"
        self.setFixedSize(BUTTON, BUTTON)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAccessibleName(f"{label} ({shortcut})" if shortcut else label)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.phase = 0.0  # drives the pulse and the spinner
        self.ticker = QVariantAnimation(self, startValue=0.0, endValue=1.0, loopCount=-1)
        self.ticker.valueChanged.connect(self._tick)
        self.hover = Tween(self, lambda _: self.update())
        self.press = Tween(self, lambda _: self.update())
        self.tip_timer = QTimer(self, singleShot=True, interval=500)
        self.tip_timer.timeout.connect(
            lambda: self.tip.show_for(self, self.label, self.shortcut))

    def set_label(self, label: str) -> None:
        self.label = label
        self.setAccessibleName(f"{label} ({self.shortcut})" if self.shortcut else label)

    def set_state(self, state: str) -> None:
        self.state = state
        self.ticker.stop()
        # the spinner always turns (it shows work is happening); the ring only pulses with
        # animations on
        if state == "busy" or (state == "active" and animations_enabled()):
            self.ticker.setDuration(1400 if state == "active" else 800)
            self.ticker.start()
        self.update()

    def _tick(self, v):
        self.phase = v
        self.update()

    def enterEvent(self, e):
        self.tip_timer.start()
        self.hover.to(1, 100)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.tip_timer.stop()
        self.tip.hide()
        self.hover.to(0, 140, QEasingCurve.Type.InCubic)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        self.tip_timer.stop()
        self.tip.hide()
        if animations_enabled():  # the shrink is motion: none with animations off
            self.press.to(1, 80)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self.press.to(0, 120)
        super().mouseReleaseEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        if self.press.value:  # pressed: scale about the centre, 100% → 95%
            s = 1 - 0.05 * self.press.value
            p.translate(r.center())
            p.scale(s, s)
            p.translate(-r.center())
        if self.state == "active":
            rounded(p, r, 8, ACTIVE_BG)
        elif self.isDown():
            rounded(p, r, 8, PRESSED_BG)
        elif self.hover.value:
            bg = QColor(HOVER_BG)
            bg.setAlphaF(HOVER_BG.alphaF() * self.hover.value)
            rounded(p, r, 8, bg)

        icon_rect = QRectF((BUTTON - ICON_PX) / 2, (BUTTON - ICON_PX) / 2, ICON_PX, ICON_PX)
        if self.state == "busy":
            pen = QPen(ICON, 1.75)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(icon_rect.adjusted(2, 2, -2, -2), int(-self.phase * 360 * 16), 270 * 16)
            return
        if self.state == "active":
            svg(self.name, ACCENT).render(p, icon_rect)
            # 0.25 → 0.8 → 0.25 each cycle, a steady ring when animations are off
            running = self.ticker.state() == QAbstractAnimation.State.Running
            pulse = 0.5 - 0.5 * math.cos(2 * math.pi * self.phase) if running else 0.6
            ring = QColor(ACCENT)
            ring.setAlphaF(0.25 + 0.55 * pulse)
            pen = QPen(ring, 1.5)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(0.75, 0.75, -0.75, -0.75), 8, 8)
            return
        svg(self.name, ICON).render(p, icon_rect)
        if self.hover.value:  # the light icon fades in over the grey one
            p.setOpacity(self.hover.value)
            svg(self.name, ICON_HOVER).render(p, icon_rect)


class Toolbar(QWidget):
    """Emits triggered(name) on click. dock() places it beside the pop-up's visible bar."""

    triggered = Signal(str)

    def __init__(self, buttons=(("screenshot", "Screenshot", "Alt+S"),
                                ("window", "Attach window", "Alt+W"),
                                ("voice", "Dictate", "Alt+M"))):
        super().__init__()
        floating(self)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)
        self.tip = Tip()
        self.buttons: dict[str, IconButton] = {}
        row = QHBoxLayout(self)
        row.setContentsMargins(SHADOW + PAD, SHADOW + (BAR_H - BUTTON) // 2,
                               SHADOW + PAD, SHADOW + (BAR_H - BUTTON) // 2)
        row.setSpacing(SPACING)
        for name, label, shortcut in buttons:
            b = IconButton(name, label, shortcut, self.tip)
            b.clicked.connect(lambda _=False, n=name: self.triggered.emit(n))
            row.addWidget(b)
            self.buttons[name] = b
        self.adjustSize()
        no_activate(self)
        self.fade = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=120,
                                      easingCurve=QEasingCurve.Type.OutCubic)
        self.fade.valueChanged.connect(self._fade_step)
        self.target = (0, 0)

    def set_state(self, name: str, state: str) -> None:
        self.buttons[name].set_state(state)

    def dock(self, left: int, top: int, right: int, dpi: int, screen_right: int,
             animate: bool = False, position: str = "auto") -> None:
        """Pop-up bar edges in physical px. position: "right" of the bar, "below" it
        (right-aligned), or "auto" = right if it fits on the screen, else below."""
        # scale of the pop-up's monitor: devicePixelRatioF() is still the old monitor's
        # until the toolbar has moved there
        scale = dpi / 96
        win_w, shadow = round(self.width() * scale), round(SHADOW * scale)
        bar_w = win_w - 2 * shadow
        fits_right = right + round(GAP * scale) + bar_w <= screen_right
        if position == "right" or (position == "auto" and fits_right):
            x, y = right + round(GAP * scale), top
        else:
            x, y = right - bar_w, top + round((BAR_H + GAP) * scale)
        self.target = (x - shadow, y - shadow)
        if animate and animations_enabled():
            self.setWindowOpacity(0)
            self.show()
            self.fade.start()
        else:
            self.fade.stop()
            self.setWindowOpacity(1)
            self._place(0)
            self.show()

    def _fade_step(self, v):
        self.setWindowOpacity(v)
        self._place(round((1 - v) * 6 * self.devicePixelRatioF()))

    def _place(self, dx):
        user32.SetWindowPos(int(self.winId()), HWND_TOPMOST, self.target[0] - dx, self.target[1],
                            0, 0, SWP_NOSIZE | SWP_NOACTIVATE)

    def hideEvent(self, e):
        self.tip.hide()
        super().hideEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        panel(p, QRectF(self.rect()).adjusted(SHADOW, SHADOW, -SHADOW, -SHADOW),
              self.devicePixelRatioF())


def shadow(p: QPainter, rect: QRectF, lift: float = 0.0) -> None:
    """Soft shadow around rect: stacked rounded rects, faint and slightly lower than the panel.
    lift (0..1) deepens it, for a panel being dragged. Needs SHADOW px of margin around rect."""
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    drop = 3 + 2 * lift
    for i in range(SHADOW, 0, -2):
        shade = QColor(0, 0, 0, round((28 + 26 * lift) * (1 - i / SHADOW) ** 2) + 2)
        rounded(p, rect.adjusted(-i, -i + drop, i, i + drop), RADIUS + i, shade)


def panel(p: QPainter, rect: QRectF, dpr: float, top: QColor = BG_TOP,
          bottom: QColor = BG_BOTTOM, with_shadow: bool = True) -> None:
    """The pop-up's look: soft shadow, warm gradient, 1 px hairline. rect excludes the shadow,
    which needs SHADOW px of transparent margin around it."""
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if with_shadow:
        shadow(p, rect)
    grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    grad.setColorAt(0, top)
    grad.setColorAt(1, bottom)
    half = 0.5 / dpr  # keep the hairline on a device pixel
    rounded(p, rect.adjusted(half, half, -half, -half), RADIUS, grad, EDGE_TOP, EDGE_BOTTOM)
