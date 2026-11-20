"""Live smoke test of the installed exe (it must be running). Real input:

1. pop-up opens -> the exe logs "pop-up shown" and the toolbar appears
2. Alt+S -> selection overlay; Esc -> back in the pop-up
3. bring the given app forward, open the pop-up, Alt+W -> a new chat with that window pasted
4. back to the last Code session
Reads the exe's real log (%APPDATA%\\QuickEntryPlus\\qeplus.log), so run it from PowerShell, not
from inside Claude's container (see README, Troubleshooting).
Run: .venv\\Scripts\\python spikes\\smoke_exe.py notion.exe
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtGui import QGuiApplication  # noqa: E402

from qeplus import uia  # noqa: E402
from qeplus.win import (VK_ESCAPE, VK_MENU, _send, exe_name, find_windows,  # noqa: E402
                        is_main_window, is_popup, press, user32)

qt = QGuiApplication([])
LOG = os.path.join(os.environ["APPDATA"], "QuickEntryPlus", "qeplus.log")
popup = find_windows(is_popup)[0]


def pump(s):
    end = time.monotonic() + s
    while time.monotonic() < end:
        qt.processEvents()
        time.sleep(0.01)


def log_tail():
    return open(LOG, encoding="utf-8").read()[-3000:]


def focused():
    return user32.IsWindowVisible(popup) and user32.GetForegroundWindow() == popup


def open_popup():
    _send([(0x11, 0, 0), (VK_MENU, 0, 0), (0x20, 0, 0), (0x20, 0, 2), (VK_MENU, 0, 2), (0x11, 0, 2)])
    pump(1.0)
    assert focused(), "pop-up didn't open"


def alt(key):
    _send([(VK_MENU, 0, 0), (ord(key), 0, 0), (ord(key), 0, 2)])
    pump(0.15)
    _send([(VK_MENU, 0, 2)])


before = len(open(LOG, encoding="utf-8").read())
open_popup()
print("1. toolbar shown with pop-up:", "pop-up shown" in open(LOG, encoding="utf-8").read()[before:])

alt("S")
pump(0.8)
fg = user32.GetForegroundWindow()
print("2. Alt+S opened the overlay:", exe_name(fg) == "quickentryplus.exe")
if exe_name(fg) == "quickentryplus.exe":
    press(VK_ESCAPE)
    pump(1.2)
print("   Esc returned to the pop-up:", bool(focused()))
if focused():
    press(VK_ESCAPE)
    pump(0.6)

target = next(h for h in find_windows(lambda h: exe_name(h) == sys.argv[1])
              if user32.IsWindowVisible(h) and user32.GetWindowTextLengthW(h))
press(VK_MENU)  # a tap of Alt lets this process move the foreground
user32.SetForegroundWindow(target)
pump(0.6)
open_popup()
before = len(open(LOG, encoding="utf-8").read())
alt("W")
t = time.monotonic()
while time.monotonic() - t < 10 and "pasted screenshot" not in open(LOG, encoding="utf-8").read()[before:]:
    pump(0.2)
pump(1.0)
new = open(LOG, encoding="utf-8").read()[before:]
print("3. Alt+W pasted the window into a new chat:", "pasted screenshot" in new,
      "| new chat focused:", uia.chat_box_focused(),
      "| main window in front:", is_main_window(user32.GetForegroundWindow()))
img = qt.primaryScreen().grabWindow(0)
img.scaled(img.width() // 2, img.height() // 2).save(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "smoke-exe-chat.png"))
os.startfile("claude://code/continue?session=last&source=desktop_action")
pump(1.5)
print("4. back to the Code session; errors in the log:", "ERROR" in new)
