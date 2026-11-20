"""Live end-to-end test of the answer card (the app must be running). Real input and the real,
signed-in Claude Code:

Ctrl+Alt+Shift+Space -> bar has focus -> type a question -> Enter -> grab the "thinking" state ->
wait for the answer -> grab it -> follow-up -> Esc -> check no Claude Code process is left.
Run from PowerShell: .venv\\Scripts\\python spikes\\e2e_ask.py
"""

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtGui import QGuiApplication  # noqa: E402

from qeplus.win import VK_ESCAPE, _send, exe_name, press, type_text, user32  # noqa: E402

qt = QGuiApplication([])
LOG = os.path.join(os.environ["APPDATA"], "QuickEntryPlus", "qeplus.log")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")


def pump(s):
    end = time.monotonic() + s
    while time.monotonic() < end:
        qt.processEvents()
        time.sleep(0.02)


def claude_children():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq claude.exe", "/V", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if "workspace" in line.lower()]


def grab(name):
    scr = qt.primaryScreen()
    g = scr.availableGeometry()
    img = scr.grabWindow(0, g.center().x() - 380, g.top() + g.height() // 4 - 30, 760,
                         int(g.height() * 0.7))
    img.save(os.path.join(OUT, f"{name}.png"))


def wait_log(marker, start, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if marker in open(LOG, encoding="utf-8").read()[start:]:
            return True
        pump(0.2)
    return False


def fg_is_bar():
    fg = user32.GetForegroundWindow()
    return exe_name(fg) in ("python.exe", "quickentryplus.exe")


hotkey = [(0x11, 0, 0), (0x12, 0, 0), (0x10, 0, 0), (0x20, 0, 0),
          (0x20, 0, 2), (0x10, 0, 2), (0x12, 0, 2), (0x11, 0, 2)]
_send(hotkey)
pump(1.2)
print("bar opened with focus:", fg_is_bar())
assert fg_is_bar(), "the bar didn't take focus; stopping before typing anything"
type_text("In two short sentences: what does the Python walrus operator do? Include a tiny code example.")
pump(0.2)
t0 = time.monotonic()
press(0x0D)  # Enter
pump(0.6)
grab("ask-thinking")
start = len(open(LOG, encoding="utf-8").read())
pump(12)
grab("ask-answer")
print(f"answer screenshot after {time.monotonic() - t0:.0f} s")

assert fg_is_bar(), "focus moved away; stopping before typing the follow-up"
type_text("And what Python version added it?")
press(0x0D)
pump(10)
grab("ask-followup")
print("Claude Code processes for the card before Esc:", len(claude_children()))
press(VK_ESCAPE)
pump(3)
print("after Esc: Claude Code processes left:", len(claude_children()),
      "| bar still focused:", fg_is_bar())
