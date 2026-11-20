"""Does a mouse click reach a button in a Qt window that never takes focus?

Variants: A = Qt WindowDoesNotAcceptFocus flag, B = WS_EX_NOACTIVATE set by hand only,
C = A plus WA_TranslucentBackground. Each window is shown, clicked with real mouse input, and
we log the native messages and Qt press events it got. No pop-up needed.
Run: .venv\\Scripts\\python spikes\\click_probe.py
"""

from __future__ import annotations

import ctypes
import sys
import time

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QHBoxLayout, QToolButton, QWidget

user32 = ctypes.WinDLL("user32")
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
NAMES = {0x21: "WM_MOUSEACTIVATE", 0x201: "WM_LBUTTONDOWN", 0x202: "WM_LBUTTONUP",
         0x84: "WM_NCHITTEST"}


class Win(QWidget):
    def __init__(self, qt_flag, translucent):
        flags = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint \
            | Qt.WindowType.WindowStaysOnTopHint
        if qt_flag:
            flags |= Qt.WindowType.WindowDoesNotAcceptFocus
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        if translucent:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.seen = []
        self.button = QToolButton(text="click")
        self.button.setFixedSize(120, 60)
        self.button.setStyleSheet("background:#444;color:white")
        self.button.pressed.connect(lambda: self.seen.append("pressed"))
        self.button.clicked.connect(lambda: self.seen.append("clicked"))
        QHBoxLayout(self).addWidget(self.button)

    def nativeEvent(self, event_type, message):
        msg = ctypes.wintypes.MSG.from_address(int(message))
        if msg.message in NAMES and msg.message != 0x84:
            self.seen.append(NAMES[msg.message])
        return False, 0


class Filter(QObject):
    def __init__(self, sink):
        super().__init__()
        self.sink = sink

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.MouseButtonPress:
            self.sink.append(f"Qt press on {type(obj).__name__}")
        return False


def pump(app, s):
    end = time.monotonic() + s
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def run(app, label, qt_flag, translucent, set_style):
    w = Win(qt_flag, translucent)
    f = Filter(w.seen)
    w.button.installEventFilter(f)
    hwnd = int(w.winId())
    if set_style:
        ex = user32.GetWindowLongPtrW(hwnd, -20)
        user32.SetWindowLongPtrW(hwnd, -20, ex | 0x08000000 | 0x80)
    w.move(200, 200)
    fg = user32.GetForegroundWindow()
    w.show()
    pump(app, 0.4)
    g = w.button.mapToGlobal(w.button.rect().center())
    dpr = w.devicePixelRatioF()
    user32.SetCursorPos(round(g.x() * dpr), round(g.y() * dpr))
    pump(app, 0.2)
    user32.mouse_event(2, 0, 0, 0, 0)
    pump(app, 0.08)
    user32.mouse_event(4, 0, 0, 0, 0)
    pump(app, 0.4)
    ex = user32.GetWindowLongPtrW(hwnd, -20) & 0xFFFFFFFF
    print(f"{label}: exstyle={ex:#010x} foreground unchanged={user32.GetForegroundWindow() == fg}"
          f" seen={w.seen}")
    w.close()


def main():
    import ctypes.wintypes  # noqa: F401
    app = QApplication(sys.argv[:1])
    pos = ctypes.wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pos))
    run(app, "A qt flag", True, False, False)
    run(app, "B style only", False, False, True)
    run(app, "C qt flag+translucent", True, True, False)
    run(app, "D as dock_probe", True, True, True)
    user32.SetCursorPos(pos.x, pos.y)


if __name__ == "__main__":
    main()
