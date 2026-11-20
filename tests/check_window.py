"""Self-check for the window button: capture, the screen-grab fallback, and the refusals.

Uses the real Claude main window as the target (it is always open while testing). Delivery is
mocked, so nothing is pasted anywhere.
Run: .venv\\Scripts\\python tests\\check_window.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from qeplus import app as qapp  # noqa: E402
from qeplus.capture import frame_rect  # noqa: E402
from qeplus.win import user32  # noqa: E402

qt = QApplication([])
a = qapp.App()
got, toasts = [], []
a.deliverer.start = lambda image, draft: got.append(image)
a.toast.show_message = toasts.append
# our own window as the target, so the check never depends on what else is open or minimised
shown = QLabel("Quick Entry Plus window check")
shown.setWindowTitle("QE+ capture target")
shown.setStyleSheet("background: #2a6f97; color: white; font-size: 24px; padding: 40px;")
shown.resize(480, 260)
shown.show()
for _ in range(30):
    qt.processEvents()
    time.sleep(0.02)
target = int(shown.winId())
left, top, right, bottom = frame_rect(target)


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qt.processEvents()


# 1. PrintWindow path: an image the size of the visible frame
a.watcher.before_popup = target
a.attach_window()
assert got and (got[0].width(), got[0].height()) == (right - left, bottom - top), got
a.busy = False

# 2. fallback path: PrintWindow draws nothing -> screen grab of the same frame, but only when
#    the window is really on top (otherwise the grab would capture whatever covers it)
got.clear()
real, real_on_top = qapp.window_image, qapp.on_top
qapp.window_image = lambda hwnd: None
qapp.on_top = lambda hwnd: True
a.attach_window()
pump(0.5)
assert got and (got[0].width(), got[0].height()) == (right - left, bottom - top), got
a.busy = False

# 2b. covered window: no screen grab, a message, and the app isn't left busy
got.clear()
qapp.on_top = lambda hwnd: False
a.attach_window()
pump(0.5)
assert not got and not a.busy and "covered" in toasts[-1], (got, toasts)
qapp.window_image, qapp.on_top = real, real_on_top

# 3. no target: refuse with a toast, nothing captured
got.clear()
a.watcher.before_popup = 0
a.attach_window()
assert not got and toasts and "No window" in toasts[-1], toasts

# 4. label shown in the tooltip
assert qapp.window_name(target) == "QE+ capture target", qapp.window_name(target)

a.watcher.stop()
print("window button: all checks passed")
