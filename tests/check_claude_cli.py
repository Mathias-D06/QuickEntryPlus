"""Self-check for the Claude Code backend, against the real signed-in CLI (uses a few small
messages of your Pro/Max allowance). Run from PowerShell, not inside Claude's container:
.venv\\Scripts\\python tests\\check_claude_cli.py
"""

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from PySide6.QtCore import QCoreApplication  # noqa: E402

from qeplus import claude_cli  # noqa: E402

qt = QCoreApplication([])


def pump_until(pred, seconds=60):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not pred():
        qt.processEvents()
        time.sleep(0.02)
    return pred()


def alive(pid):
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True,
                         text=True).stdout
    return str(pid) in out


s = claude_cli.ClaudeSession()
got, done, errors = [], [], []
s.text.connect(got.append)
s.finished.connect(lambda: done.append(1))
s.failed.connect(errors.append)

# 1. a warm process streams an answer
s.start()
assert s.proc and s.proc.poll() is None, "no warm process"
s.ask("Reply with exactly the word: apricot")
assert pump_until(lambda: done or errors), "no answer within 60 s"
assert not errors and "apricot" in "".join(got).lower() and len(got) >= 1, (errors, got)

# 2. a follow-up on the same process remembers
got.clear()
done.clear()
s.ask("What single word did you just reply with? Reply with that word only.")
assert pump_until(lambda: done or errors)
assert "apricot" in "".join(got).lower(), got

# 3. Esc: the process ends; the next question resumes the conversation in a new process
old = s.proc.pid
s.stop()  # returns at once; the process is ended on a background thread
assert s.proc is None
assert pump_until(lambda: not alive(old), 5), "process survived stop()"
got.clear()
done.clear()
s.ask("And the word before that one? Reply with the same word again only.")
assert pump_until(lambda: done or errors)
assert s.proc.pid != old and "apricot" in "".join(got).lower(), got

# 4. New: a fresh conversation forgets it
got.clear()
done.clear()
s.reset()
s.ask("Did I mention a fruit earlier in this conversation? Answer yes or no only.")
assert pump_until(lambda: done or errors)
assert "no" in "".join(got).lower(), got
s.stop()

# 5. if this app dies abruptly, the job object kills Claude Code too: no orphans
code = (f"import sys, time, os; sys.path.insert(0, {ROOT!r});"
        "from PySide6.QtCore import QCoreApplication; a = QCoreApplication([]);"
        "from qeplus.claude_cli import ClaudeSession; s = ClaudeSession(); s.start();"
        "print(s.proc.pid, flush=True); time.sleep(1); os._exit(1)")
out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
child = int(out.stdout.split()[0])
time.sleep(1)
assert not alive(child), f"Claude Code (pid {child}) outlived its parent"

# 6. a missing CLI gives a one-line message, not a crash
real = claude_cli.find_cli
claude_cli.find_cli = lambda: None
errors.clear()
claude_cli.ClaudeSession().start()
s2 = claude_cli.ClaudeSession()
s2.failed.connect(errors.append)
s2.start()
assert errors and "isn't installed" in errors[0], errors
claude_cli.find_cli = real
assert "auth login" in claude_cli.friendly_error("Not logged in · Please run /login", "c.exe")

print("claude cli: all checks passed")
