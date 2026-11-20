"""Diagnose a delivery: bring an app forward, open the pop-up, press Alt+W, then every 200 ms for
7 s record the foreground window, whether it is Claude's main window, and the focused page URL.
The installed exe must be running. Run: .venv\\Scripts\\python spikes\\trace_delivery.py windowsterminal.exe
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtGui import QGuiApplication  # noqa: E402

from qeplus import uia  # noqa: E402
from qeplus.win import (VK_MENU, _send, exe_name, find_windows, is_main_window,  # noqa: E402
                        is_popup, press, text_of, user32)

qt = QGuiApplication([])
popup = find_windows(is_popup)[0]


def pump(s):
    end = time.monotonic() + s
    while time.monotonic() < end:
        qt.processEvents()
        time.sleep(0.01)


target = next(h for h in find_windows(lambda h: exe_name(h) == sys.argv[1])
              if user32.IsWindowVisible(h) and user32.GetWindowTextLengthW(h))
press(VK_MENU)
user32.SetForegroundWindow(target)
pump(0.6)
_send([(0x11, 0, 0), (VK_MENU, 0, 0), (0x20, 0, 0), (0x20, 0, 2), (VK_MENU, 0, 2), (0x11, 0, 2)])
pump(1.0)
assert user32.IsWindowVisible(popup) and user32.GetForegroundWindow() == popup, "pop-up didn't open"
_send([(VK_MENU, 0, 0), (ord("W"), 0, 0), (ord("W"), 0, 2)])
pump(0.15)
_send([(VK_MENU, 0, 2)])
t0, last = time.monotonic(), None
while time.monotonic() - t0 < 7:
    fg = user32.GetForegroundWindow()
    try:
        url = uia.focused_textbox_url()
    except Exception as e:
        url = f"<{type(e).__name__}>"
    row = (exe_name(fg), text_of(fg, user32.GetClassNameW)[:24], is_main_window(fg),
           fg == popup, bool(user32.IsWindowVisible(popup)), url)
    if row != last:
        print(f"{time.monotonic() - t0:5.1f}s fg={row[0]} class={row[1]} main={row[2]} "
              f"popup_fg={row[3]} popup_visible={row[4]} url={row[5]!r}")
        last = row
    pump(0.2)
os.startfile("claude://code/continue?session=last&source=desktop_action")
