"""Self-check for Recent sessions: the store, the menu, resuming in the app, and a real resume
through the signed-in Claude Code (a few small messages). Uses a temp sessions.json.
Run from PowerShell: .venv\\Scripts\\python tests\\check_sessions.py
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication  # noqa: E402

from qeplus import app as qapp  # noqa: E402
from qeplus import claude_cli, sessions  # noqa: E402

qt = QApplication([])
sessions.PATH = os.path.join(tempfile.mkdtemp(prefix="qeplus-sessions-"), "sessions.json")


def pump_until(pred, seconds=60):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not pred():
        qt.processEvents()
        time.sleep(0.02)
    return pred()


# 1. the store: newest first, at most 10, updates keep their creation time, bad files set aside
for i in range(12):
    sessions.save(f"id-{i}", [[f"question {i}", "answer", ""]])
data = sessions.load()
assert [s["id"] for s in data[:2]] == ["id-11", "id-10"] and len(data) == 10, [s["id"] for s in data]
created = data[-1]["created"]
time.sleep(0.01)
sessions.save("id-2", [["question 2", "answer", ""], ["more", "yes", ""]])
data = sessions.load()
assert data[0]["id"] == "id-2" and data[0]["created"] == created and len(data[0]["turns"]) == 2
assert data[0]["title"] == "question 2" and data[0]["backend"] == "claude"
open(sessions.PATH, "w").write("{broken")
assert sessions.load() == [] and os.path.exists(sessions.PATH + ".bad")
assert sessions.when(time.time() - 30) == "just now" and sessions.when(time.time() - 7200) == "2 h ago"

# 2. the app saves after each answer, lists sessions in the menu, and resumes one
a = qapp.App()
a.keyhook.stop()
listed, resumed = [], []


class FakeMenu(qapp.QMenu):
    def exec(self, *_):
        listed.extend(act.text() for act in self.actions())
        self.actions()[0].trigger()  # choose the newest


qapp.QMenu = FakeMenu
a.claude.session_id = "abc-123"
a.ask.turns = [["What is DNS?", "The internet's phone book.", ""]]
a.save_session()
assert sessions.load()[0]["id"] == "abc-123"
a.resume_session = resumed.append
a.show_recent()
assert listed and listed[0].startswith("What is DNS?\t") and resumed[0]["id"] == "abc-123", listed
a.settings.values["recent_sessions"] = False
a.claude.session_id = "not-saved"
a.save_session()
assert sessions.load()[0]["id"] == "abc-123", "saved while recent_sessions is off"
a.settings.values["recent_sessions"] = True
a.ask_key.disable()
a.watcher.stop()

# 3. for real: a conversation saved to sessions.json resumes with its memory, in a new process
s = claude_cli.ClaudeSession()
got, done = [], []
s.text.connect(got.append)
s.finished.connect(lambda: done.append(1))
s.failed.connect(lambda m: done.append(m))
s.ask("Remember the code word 'tangerine'. Reply with just: noted")
assert pump_until(lambda: done), "no answer"
sessions.save(s.session_id, [["Remember the code word", "".join(got), ""]])
s.reset()  # New: a different conversation
saved = sessions.load()[0]
s.stop()
s.session_id = saved["id"]  # what resume_session does
got.clear()
done.clear()
s.ask("What was the code word? One word.")
assert pump_until(lambda: done) and "tangerine" in "".join(got).lower(), got
s.stop()
print("recent sessions: all checks passed")
