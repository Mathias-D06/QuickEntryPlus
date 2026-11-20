"""Step 1b proof: the qeplus toolbar docks to Claude's quick entry pop-up without taking focus.

Pop-up (from inspect_popup.py): claude.exe, Chrome_WidgetWin_1, title "Claude", WS_EX_TOPMOST,
no caption, not WS_EX_NOACTIVATE (a small Claude notification window shares the rest).
On SHOW the toolbar fades in beside the pop-up's visible bar, follows LOCATION, hides on HIDE.
Clicks are logged; the mic button cycles idle -> recording -> transcribing (1.5 s) -> idle.

Run: .venv\\Scripts\\python spikes\\dock_probe.py    (close with Ctrl+C in the console)
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "spikes")]
from inspect_popup import WinEventProc, describe, exe_of, out as log, user32  # noqa: E402
from qeplus.toolbar import Toolbar  # noqa: E402

GWL_STYLE, GWL_EXSTYLE, GA_ROOT = -16, -20, 2
WS_CAPTION, WS_EX_TOPMOST, WS_EX_NOACTIVATE = 0xC00000, 0x8, 0x08000000


def is_popup(hwnd) -> bool:
    cls, title = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, cls, 64)
    user32.GetWindowTextW(hwnd, title, 64)
    style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
    ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    return (cls.value == "Chrome_WidgetWin_1" and title.value == "Claude"
            and bool(ex & WS_EX_TOPMOST) and not ex & WS_EX_NOACTIVATE and not style & WS_CAPTION
            and os.path.basename(exe_of(hwnd)[1]).lower() == "claude.exe")


class Probe:
    def __init__(self):
        self.bar = Toolbar()
        self.bar.triggered.connect(self.clicked)
        self.popup = None

    def clicked(self, name):
        log(f"[dock] click {name}: pop-up visible={bool(user32.IsWindowVisible(self.popup))}"
            f" foreground is pop-up={user32.GetForegroundWindow() == self.popup}")
        if name == "voice":
            state = self.bar.buttons["voice"].state
            if state == "idle":
                self.bar.set_state("voice", "active")
            elif state == "active":
                self.bar.set_state("voice", "busy")
                QTimer.singleShot(1500, lambda: self.bar.set_state("voice", "idle"))

    def dock(self, animate=False):
        r = wt.RECT()
        user32.GetWindowRect(self.popup, ctypes.byref(r))
        mon = self.bar.screen().geometry()
        screen_right = round((mon.x() + mon.width()) * self.bar.devicePixelRatioF())
        self.bar.dock(r.left, r.top, r.right, user32.GetDpiForWindow(self.popup), screen_right,
                      animate)
        log(f"[dock] toolbar target={self.bar.target} logical size={self.bar.width()}x"
            f"{self.bar.height()}")

    def on_event(self, event, hwnd):
        if event == 0x8003 and hwnd == self.popup:
            self.bar.hide()
            log("[dock] pop-up HIDE -> toolbar hidden")
        elif event == 0x800B and hwnd == self.popup and self.bar.isVisible():
            self.dock()
        elif event == 0x8002 and is_popup(hwnd):
            self.popup = hwnd
            log(f"[dock] pop-up SHOW {describe(hwnd)}")
            self.dock(animate=True)


def main() -> None:
    app = QApplication(sys.argv[:1])
    probe = Probe()

    @WinEventProc
    def on_event(_hook, event, hwnd, id_object, id_child, _thread, _time):
        if hwnd and id_object == 0 and id_child == 0 and user32.GetAncestor(hwnd, GA_ROOT) == hwnd:
            probe.on_event(event, hwnd)

    # out-of-context hooks are delivered through this thread's message loop, i.e. Qt's
    hooks = [user32.SetWinEventHook(lo, hi, None, on_event, 0, 0, 0x2)
             for lo, hi in ((0x8002, 0x8003), (0x800B, 0x800B))]
    assert all(hooks)
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    tick = QTimer(interval=500)  # lets Python see Ctrl+C; the app proper won't need this
    tick.timeout.connect(lambda: None)
    tick.start()
    log("[dock] waiting for the pop-up (Ctrl+Alt+Space)...")
    app.exec()
    for h in hooks:
        user32.UnhookWinEvent(h)


if __name__ == "__main__":
    main()
