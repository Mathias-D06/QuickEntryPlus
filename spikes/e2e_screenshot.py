"""End-to-end test of the screenshot button (python -m qeplus must be running, logging to
spikes/out/app.log). Drives real mouse/keyboard input:

open pop-up -> type a draft -> click the screenshot button -> drag a region -> expect a new chat
with the draft typed and the image pasted -> grab it (e2e-chat.png) -> go back to the last Code
session -> reopen the pop-up and check the draft was cleared (e2e-popup.png) -> Esc.
Run: .venv\\Scripts\\python spikes\\e2e_screenshot.py
"""

from __future__ import annotations

import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(1, os.path.join(ROOT, "spikes"))
from paste_probe import grab, open_popup, pump  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from qeplus import uia  # noqa: E402
from qeplus.toolbar import BAR_H, BUTTON, PAD, SHADOW  # noqa: E402
from qeplus.win import (VK_ESCAPE, find_windows, is_main_window, is_popup, press,  # noqa: E402
                        type_text, user32)

OUT = os.path.join(ROOT, "spikes", "out")
DRAFT = "qe+ e2e draft"


def mouse(x, y, flags=0):
    user32.SetCursorPos(x, y)
    pump(0.05)
    if flags:
        user32.mouse_event(flags, 0, 0, 0, 0)
        pump(0.08)


def main():
    popup = find_windows(is_popup)[0]
    dpr = QGuiApplication.primaryScreen().devicePixelRatio()
    cursor = QGuiApplication.primaryScreen().geometry().center()

    open_popup(popup)
    assert type_text(DRAFT)
    pump(0.5)
    x, y = map(int, re.findall(r"toolbar at \((-?\d+), (-?\d+)\)",
                               open(os.path.join(OUT, "app.log"), encoding="utf-8").read())[-1])
    bx, by = x + round((SHADOW + PAD + BUTTON / 2) * dpr), y + round((SHADOW + BAR_H / 2) * dpr)
    mouse(bx, by, 0x2)
    mouse(bx, by, 0x4)  # click the screenshot button
    pump(0.6)
    print("after click: pop-up visible", bool(user32.IsWindowVisible(popup)))

    mouse(400, 300, 0x2)  # drag a region in the top-left of the screen
    for i in range(1, 11):
        mouse(400 + 50 * i, 300 + 30 * i)
    mouse(900, 600, 0x4)
    t = time.monotonic()
    while time.monotonic() - t < 8 and not uia.chat_box_focused():
        pump(0.1)
    pump(1.5)
    fg = user32.GetForegroundWindow()
    print(f"new chat focused: {uia.chat_box_focused()} url={uia.focused_textbox_url()!r} "
          f"main window foreground={is_main_window(fg)} text={uia.focused_text()!r}")
    QGuiApplication.primaryScreen().grabWindow(0).save(os.path.join(OUT, "e2e-chat.png"))

    os.startfile("claude://code/continue?session=last&source=desktop_action")
    pump(2.0)
    open_popup(popup)
    pump(0.6)
    print("pop-up text after reopen:", repr(uia.focused_text()))
    grab(popup, "e2e-popup")
    if user32.GetForegroundWindow() == popup:
        press(VK_ESCAPE)
    user32.SetCursorPos(round(cursor.x() * dpr), round(cursor.y() * dpr))


if __name__ == "__main__":
    main()
