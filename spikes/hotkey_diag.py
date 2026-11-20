"""Diagnose the answer-card hotkey: send Ctrl+Alt+Shift+Space, then report the app's windows
(visible? titled "Quick Entry Plus"?) and which window has the foreground. Types nothing.
Run from PowerShell: .venv\\Scripts\\python spikes\\hotkey_diag.py
"""

import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qeplus.win import VK_ESCAPE, _send, exe_name, find_windows, press, text_of, user32  # noqa: E402

APPS = ("quickentryplus.exe", "python.exe", "pythonw.exe")


def ours():
    return [(hex(h), text_of(h, user32.GetWindowTextW), bool(user32.IsWindowVisible(h)))
            for h in find_windows(lambda h: exe_name(h) in APPS
                                  and text_of(h, user32.GetWindowTextW) == "Quick Entry Plus")]


def key_states():
    return {name: bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
            for name, vk in (("shift", 0x10), ("ctrl", 0x11), ("alt", 0x12), ("lwin", 0x5B),
                             ("rwin", 0x5C))}


print("before:", ours(), "| modifiers held:", {k for k, v in key_states().items() if v})
_send([(0x11, 0, 0), (0x12, 0, 0), (0x10, 0, 0), (0x20, 0, 0),
       (0x20, 0, 2), (0x10, 0, 2), (0x12, 0, 2), (0x11, 0, 2)])
time.sleep(1.5)
fg = user32.GetForegroundWindow()
print("after: ", ours(), "| foreground:", exe_name(fg), repr(text_of(fg, user32.GetWindowTextW)))
print("modifiers held now:", {k for k, v in key_states().items() if v})
if exe_name(fg) in APPS:
    press(VK_ESCAPE)
