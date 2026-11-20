"""End-to-end test of the window button (python -m qeplus must be running, logging to
spikes/out/app.log). Real input:

bring the given app's window to the front -> open the pop-up -> hover the window button and
grab its tooltip (e2e-window-tip.png) -> click it -> expect a new chat with the window's image
pasted (e2e-window-chat.png) -> go back to the last Code session.
Run: .venv\\Scripts\\python spikes\\e2e_window.py notion.exe
"""

from __future__ import annotations

import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "spikes")]
from paste_probe import open_popup, pump  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from qeplus import uia  # noqa: E402
from qeplus.toolbar import BAR_H, BUTTON, PAD, SHADOW, SPACING  # noqa: E402
from qeplus.win import (VK_MENU, exe_name, find_windows, is_main_window, is_popup,  # noqa: E402
                        press, user32)

OUT = os.path.join(ROOT, "spikes", "out")


def main():
    exe = sys.argv[1]
    target = next(h for h in find_windows(lambda h: exe_name(h) == exe)
                  if user32.IsWindowVisible(h) and user32.GetWindowTextLengthW(h))
    press(VK_MENU)  # a tap of Alt lets this process move the foreground
    user32.SetForegroundWindow(target)
    pump(0.6)
    assert user32.GetForegroundWindow() == target, "couldn't bring the target forward"

    popup = find_windows(is_popup)[0]
    open_popup(popup)
    pump(0.5)
    log = open(os.path.join(OUT, "app.log"), encoding="utf-8").read()
    over, x, y = re.findall(r"shown over (\S+); toolbar at \((-?\d+), (-?\d+)\)", log)[-1]
    print("pop-up opened over:", over)
    dpr = QGuiApplication.primaryScreen().devicePixelRatio()
    bx = int(x) + round((SHADOW + PAD + BUTTON + SPACING + BUTTON / 2) * dpr)
    by = int(y) + round((SHADOW + BAR_H / 2) * dpr)
    user32.SetCursorPos(bx, by)
    pump(1.0)
    screen = QGuiApplication.primaryScreen()
    screen.grabWindow(0, round(bx / dpr) - 260, round(by / dpr) - 60, 360, 130).save(
        os.path.join(OUT, "e2e-window-tip.png"))
    user32.mouse_event(2, 0, 0, 0, 0)
    pump(0.08)
    user32.mouse_event(4, 0, 0, 0, 0)

    t = time.monotonic()
    while time.monotonic() - t < 8 and "pasted screenshot" not in open(
            os.path.join(OUT, "app.log"), encoding="utf-8").read().split("shown over")[-1]:
        pump(0.1)
    pump(1.5)
    print(f"new chat focused: {uia.chat_box_focused()} "
          f"main window foreground={is_main_window(user32.GetForegroundWindow())}")
    img = screen.grabWindow(0)
    img.scaled(img.width() // 2, img.height() // 2).save(os.path.join(OUT, "e2e-window-chat.png"))
    os.startfile("claude://code/continue?session=last&source=desktop_action")
    pump(1.5)


if __name__ == "__main__":
    main()
