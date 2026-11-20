"""Screenshot + click test for the docked toolbar (dock_probe.py must be running).

Opens the pop-up with Ctrl+Alt+Space, saves <name>.png (pop-up + toolbar), hovers the middle
button and saves <name>-hover.png (tooltip after 1.2 s), clicks the first button with real mouse
input and reports whether the pop-up stayed open, then closes the pop-up with Esc only if the
pop-up is the foreground window. The cursor is put back where it was.

Run: .venv\\Scripts\\python spikes\\shoot.py before
"""

from __future__ import annotations

import ctypes
import os
import re
import sys
import time
from ctypes import wintypes as wt

from PySide6.QtGui import QGuiApplication

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from inspect_popup import OUT_DIR, user32  # noqa: E402

POPUP_TITLE, VK_CONTROL, VK_MENU, VK_SPACE, VK_ESCAPE = "Claude", 0x11, 0x12, 0x20, 0x1B
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, KEYEVENTF_KEYUP = 0x2, 0x4, 0x2


def keys(*vks):
    for vk in vks:
        user32.keybd_event(vk, 0, 0, 0)
    for vk in reversed(vks):
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def find_popup():
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def enum(h, _):
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(h, cls, 64)
        if cls.value == "Chrome_WidgetWin_1" and user32.GetWindowLongPtrW(h, -20) & 0x8:
            found.append(h)
        return True

    user32.EnumWindows(enum, 0)
    return found[0] if found else None


def toolbar_rect(dpr):
    """Physical rect of the toolbar window (shadow margin included), from the probe's log."""
    log = open(os.path.join(OUT_DIR, "inspect.log"), encoding="utf-8").read()
    x, y, w, h = map(int, re.findall(
        r"toolbar target=\((-?\d+), (-?\d+)\) logical size=(\d+)x(\d+)", log)[-1])
    return x, y, round(w * dpr), round(h * dpr)


def click(x, y):
    user32.SetCursorPos(x, y)
    time.sleep(0.15)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.08)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def grab(name, left, top, right, bottom):
    screen = QGuiApplication.primaryScreen()
    dpr = screen.devicePixelRatio()
    img = screen.grabWindow(0, round(left / dpr), round(top / dpr), round((right - left) / dpr),
                            round((bottom - top) / dpr))
    path = os.path.join(OUT_DIR, f"{name}.png")
    img.save(path)
    print("saved", path)


def main():
    name = sys.argv[1]
    app = QGuiApplication(sys.argv[:1])  # noqa: F841  (needed for QScreen)
    popup = find_popup()
    assert popup, "pop-up window not found"
    cursor = wt.POINT()
    user32.GetCursorPos(ctypes.byref(cursor))

    keys(VK_CONTROL, VK_MENU, VK_SPACE)
    for _ in range(40):
        if user32.IsWindowVisible(popup):
            break
        time.sleep(0.05)
    assert user32.IsWindowVisible(popup), "pop-up did not open"
    time.sleep(0.5)

    r = wt.RECT()
    user32.GetWindowRect(popup, ctypes.byref(r))
    dpr = QGuiApplication.primaryScreen().devicePixelRatio()
    tx, ty, tw, th = toolbar_rect(dpr)
    step = round(36 * dpr)  # button + spacing
    pad = 40
    box = (r.right - 500, r.top - pad, max(r.right, tx + tw) + pad, r.top + 112 + pad + 60)
    grab(name, *box)

    bx, by = tx + tw // 2, ty + th // 2  # middle button
    user32.SetCursorPos(bx, by)
    time.sleep(1.2)
    grab(f"{name}-hover", *box)

    click(bx - step, by)  # screenshot button: only logs in the probe
    time.sleep(0.5)
    if "--states" in sys.argv:
        click(bx + step, by)  # mic: idle -> recording
        user32.SetCursorPos(bx, by + round(120 * dpr))  # off the toolbar, so no hover/tooltip
        time.sleep(0.6)
        grab(f"{name}-recording", *box)
        click(bx + step, by)  # recording -> transcribing
        user32.SetCursorPos(bx, by + round(120 * dpr))
        time.sleep(0.3)
        grab(f"{name}-busy", *box)
        time.sleep(1.5)
    visible, fg = bool(user32.IsWindowVisible(popup)), user32.GetForegroundWindow() == popup
    print(f"after click: pop-up visible={visible} pop-up foreground={fg}")

    user32.SetCursorPos(cursor.x, cursor.y)
    if user32.GetForegroundWindow() == popup:  # never send Esc to another window
        keys(VK_ESCAPE)


if __name__ == "__main__":
    main()
