"""Step 2 probe: does the quick entry pop-up keep a draft, and does it accept a pasted image?

A: open pop-up, type a marker, close (Esc), reopen, grab -> paste-A.png; then clear it.
B: put a test image on the clipboard, open pop-up, Ctrl+V, grab -> paste-B.png;
   close, reopen, grab -> paste-B2.png (does the attachment survive a close?).
Keys other than the open shortcut are only sent while the pop-up is the foreground window.
Run: .venv\\Scripts\\python spikes\\paste_probe.py
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes as wt

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from qeplus.win import (VK_CONTROL, VK_ESCAPE, VK_MENU, VK_SPACE, find_windows,  # noqa: E402
                        is_popup, press as keys, type_text, user32)

OUT_DIR, VK_BACK = os.path.join(ROOT, "spikes", "out"), 0x08
app = QGuiApplication(sys.argv[:1])


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def wait(pred, seconds=2.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not pred():
        pump(0.05)
    return pred()


def open_popup(popup):
    if not user32.IsWindowVisible(popup):
        keys(VK_CONTROL, VK_MENU, VK_SPACE)
    assert wait(lambda: user32.GetForegroundWindow() == popup), "pop-up did not get focus"
    pump(0.3)


def close_popup(popup):
    assert user32.GetForegroundWindow() == popup
    keys(VK_ESCAPE)
    assert wait(lambda: not user32.IsWindowVisible(popup)), "pop-up did not close"
    pump(0.4)


def grab(popup, name):
    r = wt.RECT()
    user32.GetWindowRect(popup, ctypes.byref(r))
    screen = QGuiApplication.primaryScreen()
    dpr = screen.devicePixelRatio()
    screen.grabWindow(0, round(r.left / dpr), round(r.top / dpr), round((r.right - r.left) / dpr),
                      round((r.bottom - r.top) / dpr)).save(os.path.join(OUT_DIR, f"{name}.png"))
    print("saved", name)


def main():
    popup = (find_windows(is_popup) or [None])[0]
    assert popup, "pop-up window not found"

    open_popup(popup)
    assert type_text("qe+ draft test ✓")
    pump(0.3)
    close_popup(popup)
    open_popup(popup)
    grab(popup, "paste-A")
    keys(VK_CONTROL, 0x41)  # Ctrl+A
    keys(VK_BACK)
    pump(0.2)
    close_popup(popup)

    img = QImage(480, 160, QImage.Format.Format_RGB32)
    img.fill(QColor("#d97757"))
    p = QPainter(img)
    f = QFont("Segoe UI Variable Text")
    f.setPixelSize(40)
    p.setFont(f)
    p.setPen(QColor("white"))
    p.drawText(img.rect(), Qt.AlignmentFlag.AlignCenter, "QE+ paste test")
    p.end()
    QGuiApplication.clipboard().setImage(img)
    pump(0.3)

    open_popup(popup)
    keys(VK_CONTROL, 0x56)  # Ctrl+V
    pump(1.2)
    grab(popup, "paste-B")
    print("after paste: pop-up visible", bool(user32.IsWindowVisible(popup)),
          "foreground", user32.GetForegroundWindow() == popup)
    if user32.GetForegroundWindow() == popup:
        close_popup(popup)
        open_popup(popup)
        grab(popup, "paste-B2")
        close_popup(popup)


if __name__ == "__main__":
    main()
