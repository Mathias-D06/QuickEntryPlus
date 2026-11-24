"""Self-check for settings, the tray helpers and pause. Works in a temp folder: never touches
your real settings file or Startup folder.
Run: .venv\\Scripts\\python tests\\check_settings.py
"""

import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from PySide6.QtWidgets import QApplication  # noqa: E402

from qeplus import settings as st  # noqa: E402
from qeplus import tray  # noqa: E402

qt = QApplication([])
tmp = tempfile.mkdtemp(prefix="qeplus-check-")


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qt.processEvents()
        time.sleep(0.01)


# 1. validation
values, problems = st.validate({"toolbar_position": "below", "transcript_cleanup": False,
                                "hotkeys": {"voice": "Ctrl+Shift+F9", "window": ""}})
assert values["toolbar_position"] == "below" and values["transcript_cleanup"] is False
assert values["hotkeys"] == {"screenshot": "Alt+S", "window": "", "voice": "Ctrl+Shift+F9"}
assert problems == [], problems
values, problems = st.validate({"toolbar_position": "left", "transcript_cleanup": "yes",
                                "whisper_model": " ", "hotkeys": {"voice": "Hyper+Q", "x": "A"},
                                "unknown_key": 1})
assert values == st.DEFAULTS, values
assert len(problems) == 5, problems

# 2. a missing file is created with the defaults
path = os.path.join(tmp, "sub", "settings.json")
s = st.Settings(path)
seen_problems, changes = [], []
s.problem.connect(seen_problems.append)
s.changed.connect(lambda: changes.append(dict(s.values)))
s.start()
assert json.load(open(path)) == st.DEFAULTS and s.values == st.DEFAULTS and not seen_problems

# 3. saving the file reloads it
with open(path, "w") as f:
    json.dump({"voice_engine": "windows"}, f)
pump(1.0)
assert s["voice_engine"] == "windows" and changes, changes

# 4. an editor that replaces the file (write a temp file, then rename over it) still reloads
changes.clear()
with open(path + ".tmp", "w") as f:
    json.dump({"voice_engine": "windows", "toolbar_position": "right"}, f)
for _ in range(20):  # a virus scanner can briefly lock a file that was just written
    try:
        os.replace(path + ".tmp", path)
        break
    except PermissionError:
        time.sleep(0.05)
pump(1.0)
assert s["toolbar_position"] == "right" and changes, changes

# 5. broken JSON: defaults used, user told, file left exactly as it was
with open(path, "w") as f:
    f.write('{"voice_engine": "gemini",')
pump(1.0)
assert s.values == st.DEFAULTS and seen_problems and "error" in seen_problems[-1], seen_problems
assert open(path).read() == '{"voice_engine": "gemini",'

# 6. start-with-Windows shortcut (temp folder, not the real Startup folder)
startup = os.path.join(tmp, "Startup")
os.makedirs(startup)
assert not tray.starts_with_windows(startup)
tray.set_starts_with_windows(True, startup)
assert tray.starts_with_windows(startup)
import win32com.client  # noqa: E402

link = win32com.client.Dispatch("WScript.Shell").CreateShortcut(
    os.path.join(startup, tray.SHORTCUT))
assert link.TargetPath.lower().endswith("pythonw.exe") and link.Arguments == "-m qeplus", (
    link.TargetPath, link.Arguments)
assert os.path.samefile(link.WorkingDirectory, ROOT)
tray.set_starts_with_windows(False, startup)
assert not tray.starts_with_windows(startup)

# 7. single instance: a second process sees the first one's mutex
name = f"QuickEntryPlus.Check.{os.getpid()}"
assert not tray.already_running(name)
code = f"import sys; sys.path.insert(0, {ROOT!r}); from qeplus.tray import already_running; " \
       f"print(already_running({name!r}))"
out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout.strip()
assert out == "True", out

# 8. the tray icon draws at every size, active and paused
for paused in (False, True):
    assert len(tray.icon(paused).availableSizes()) == 7

# 9. pause: the toolbar doesn't appear and hotkeys aren't taken while paused
from qeplus import app as qapp  # noqa: E402

a = qapp.App(st.Settings(os.path.join(tmp, "app-settings.json")))
docked = []
a.dock = lambda animate=False: docked.append(animate)
a.set_paused(True)
a.on_shown()
assert docked == [] and not a.hotkeys.active
a.set_paused(False)
a.on_shown()
assert docked and a.hotkeys.active
a.hotkeys.disable()
a.watcher.stop()

print("settings and tray: all checks passed")
