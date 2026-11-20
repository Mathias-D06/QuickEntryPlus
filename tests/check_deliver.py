"""Self-check for the paste guard: never types or pastes unless a chat box is confirmed.

Run: .venv\\Scripts\\python tests\\check_deliver.py   (no Claude needed; input is mocked)
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from qeplus import deliver  # noqa: E402

app = QApplication([])
sent = []
deliver.os.startfile = lambda url: sent.append(("open", url))
deliver.type_text = lambda text: sent.append(("type", text))
deliver.press = lambda *keys: sent.append(("press", keys))
deliver.FIND_TIMEOUT = 0.3


def run(guard_answers, draft):
    answers = iter(guard_answers)
    deliver.uia.chat_box_focused = lambda: next(answers, guard_answers[-1])
    sent.clear()
    result = []
    d = deliver.Deliverer()
    d.done.connect(lambda ok, msg: result.append((ok, msg)))
    d.start(QImage(4, 4, QImage.Format.Format_RGB32), draft)
    end = time.monotonic() + 2
    while not result and time.monotonic() < end:
        app.processEvents()
    return result[0], [s for s in sent if s[0] != "open"]


# no chat box ever: nothing typed or pasted, user told the image is on the clipboard
(ok, msg), actions = run([False], "hello")
assert not ok and actions == [] and "clipboard" in msg, (ok, msg, actions)

# chat box found: draft typed (newlines flattened), then Ctrl+V
(ok, _), actions = run([True], "hello\nworld")
assert ok and actions == [("type", "hello world "), ("press", (0x11, ord("V")))], actions

# focus lost after typing: no paste
(ok, msg), actions = run([True, False], "hi")
assert not ok and actions == [("type", "hi ")] and "Focus" in msg, (ok, msg, actions)

print("deliver guard: all checks passed")
