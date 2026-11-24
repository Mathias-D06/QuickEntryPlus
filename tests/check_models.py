"""Self-check for switching the answer card between Claude and Gemini 4 Hydrogen: the Gemini
session (against tests/fake_agy.py: no network, no sign-in, no key), the logo and its
shortcut, separate sessions per model with the conversation handed over on a switch, the
remembered model, and the locked-down agy command line.
Run: .venv\\Scripts\\python tests\\check_models.py
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from qeplus import agy_cli, askbar, claude_cli, settings, toolbar  # noqa: E402
from qeplus import app as qapp  # noqa: E402

qt = QApplication([])
tmp = tempfile.mkdtemp(prefix="qeplus-models-")
LOG = os.path.join(tmp, "agy.log")
os.environ["FAKE_AGY_LOG"], os.environ["FAKE_AGY_MODE"] = LOG, ""
claude_cli.WORKSPACE = os.path.join(tmp, "workspace")
claude_cli.ATTACHMENTS = os.path.join(claude_cli.WORKSPACE, "attachments")
os.makedirs(claude_cli.ATTACHMENTS)
real_popen = subprocess.Popen


def fake_popen(args, **kw):
    assert args[0] == r"C:\fake\agy.exe", args
    return real_popen([sys.executable, os.path.join(ROOT, "tests", "fake_agy.py")] + args[1:],
                      **kw)


# only agy_cli's view of subprocess: other libraries (Whisper's) need the real Popen
agy_cli.subprocess = types.SimpleNamespace(Popen=fake_popen, PIPE=subprocess.PIPE,
                                           CREATE_NO_WINDOW=subprocess.CREATE_NO_WINDOW)
agy_cli.find_agy = lambda: r"C:\fake\agy.exe"


def pump(s=0.1, until=None):
    end = time.monotonic() + s
    while time.monotonic() < end and not (until and until()):
        qt.processEvents()
        time.sleep(0.01)


def logged():
    with open(LOG, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def messages():
    return [e["msg"]["message"]["content"][0]["text"] for e in logged() if "msg" in e]


# 1. the Gemini session: locked-down command line, no key in its environment, streaming
os.environ.update({"ANTHROPIC_API_KEY": "sk-fake", "ANTHROPIC_AUTH_TOKEN": "t",
                   "GEMINI_API_KEY": "g", "GOOGLE_API_KEY": "k"})
g = agy_cli.AgySession(lambda: "medium")
texts, done, errors, notes = [], [], [], []
g.text.connect(texts.append)
g.finished.connect(lambda: done.append(1))
g.failed.connect(errors.append)
g.notice.connect(notes.append)
image = os.path.join(claude_cli.ATTACHMENTS, "capture-1.png")
open(image, "wb").write(b"\x89PNG fake")
g.ask("What is this?", [image])
pump(10, until=lambda: done)
for v in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
    os.environ.pop(v)
start = logged()[0]
args, env = start["args"], start["env"]
assert args[args.index("--model") + 1] == "gemini-4-hydrogen-medium", args
assert "--sandbox" in args and "--disable-slash-commands" in args and "-p=" in args, args
deny = json.loads(args[args.index("--settings") + 1])["permissions"]["deny"]
for rule in ("command(*)", "write_file(*)", "edit_file(*)", "mcp(*)", "browser(*)",
             "subagent(*)", "web(*)", "read_url(*)"):
    assert rule in deny, (rule, deny)
assert not [r for r in deny if r.startswith("read_file")], "reads stay allowed (images)"
assert not {k.upper() for k in env} & set(claude_cli.KEY_VARIABLES), "a key reached agy"
assert "".join(texts) == "hello world and an image" and not errors, (texts, errors)
first = messages()[0]
assert first.startswith(agy_cli.BRIEF) and "@attachments/capture-1.png" in first, first
assert not os.path.exists(image), "the attachment wasn't deleted after the answer"
assert claude_cli.valid_session_id(g.session_id)
sid = g.session_id

# a follow-up in the same process: no second brief
texts.clear()
done.clear()
g.ask("And now?")
pump(10, until=lambda: done)
assert messages()[1] == "And now?" and "".join(texts) == "hello world"

# 2. resuming in a new process uses --conversation; a conversation agy lost starts afresh
# with the history as context (agy doesn't fail, it quietly makes a new one)
g.stop()
assert g.last is None, "stop() must forget the last message (never re-sent by itself)"
g.fallback = "**Me:** first question\n\n**Assistant:** first answer"
os.environ["FAKE_AGY_MODE"] = "lost"
open(LOG, "w").close()
done.clear()
g.start()
pump(0.5)  # warmed: agy reported the new conversation before anything was asked
g.ask("Follow-up after a restart")
pump(10, until=lambda: done)
starts = [e["args"] for e in logged() if "args" in e]
assert starts[0][starts[0].index("--conversation") + 1] == sid, starts
sent = messages()[-1]
assert "first question" in sent and "Follow-up after a restart" in sent, sent
assert notes and notes[-1] == claude_cli.RESUME_LOST, notes
assert g.session_id != sid

# asked straight away (before agy reports the conversation): asked again once, with the brief
# and the history once each, and the screenshot kept until that answer
g.stop()
g.session_id, g.briefed = sid, False
open(LOG, "w").close()
notes.clear()
done.clear()
shot = os.path.join(claude_cli.ATTACHMENTS, "capture-2.png")
open(shot, "wb").write(b"\x89PNG fake")
g.ask("Question with a screenshot", [shot])
assert g.last is not None, "the message must be recorded at once, not when the worker reports"
pump(10, until=lambda: done)
final = messages()[-1]
assert final.count(agy_cli.BRIEF) <= 1 and final.count("first question") == 1, final
assert "@attachments/capture-2.png" in final and "Question with a screenshot" in final, final
assert notes.count(claude_cli.RESUME_LOST) == 1, notes
assert not os.path.exists(shot), "deleted only once the retried question was answered"
os.environ["FAKE_AGY_MODE"] = ""

# 3. a failed turn shows as an error; no answer is saved for it
os.environ["FAKE_AGY_MODE"] = "error"
g.stop()
errors.clear()
g.ask("break")
pump(10, until=lambda: errors)
assert errors and "something broke" in errors[-1], errors
g.stop()
os.environ["FAKE_AGY_MODE"] = ""

# 4. no agy at all: a clear message, nothing started, and that question's screenshot deleted
agy_cli.find_agy = lambda: None
errors.clear()
g2 = agy_cli.AgySession()
g2.failed.connect(errors.append)
orphan = os.path.join(claude_cli.ATTACHMENTS, "capture-3.png")
open(orphan, "wb").write(b"\x89PNG fake")
g2.ask("hello?", [orphan])
assert errors and "Antigravity" in errors[-1], errors
assert not os.path.exists(orphan), "a screenshot was left behind"
agy_cli.find_agy = lambda: r"C:\fake\agy.exe"
# a link in the attachments folder is never sent as an attachment
link = os.path.join(claude_cli.ATTACHMENTS, "capture-link.png")
try:
    os.symlink(os.path.join(tmp, "elsewhere.png"), link)
    errors.clear()
    g2.ask("look", [link])
    pump(10, until=lambda: errors)
    assert errors and "Couldn't send" in errors[-1], errors
    g2.stop()
except OSError:
    print("   (no symlink rights here: skipped the link check)")

# --- the app ------------------------------------------------------------------------------
qapp.STATE = os.path.join(tmp, "state.json")
a = qapp.App()
a.keyhook.stop()
asked, toasts, saved = [], [], []
a.toast.show_message = toasts.append
for name, session in (("claude", a.claude), ("gemini", a.gemini)):
    session.start = lambda: None
    session.stop = lambda: None
    session.ask = lambda text, images, n=name: asked.append((n, text))
qapp.sessions.save = lambda sid, turns, backend="claude", path=None: saved.append(backend)
a.apply_settings()
w = a.ask
assert a.model == "claude" and w.logo.model == "claude" and a.backend is a.claude

# 5. clicking the logo switches (and never starts dragging the bar); the accent follows
toolbar.user_animations = True
w.open()
pump(0.4)
QTest.mouseClick(w.logo, Qt.MouseButton.LeftButton)
assert a.model == "gemini" and a.backend is a.gemini and w.bar.pressed_at is None
assert w.logo.flip.value < 1, "the logo should be mid-flip"
pump(0.4)
assert w.logo.flip.value == 1 and w.card.accent.name() == askbar.GEMINI_ACCENT.name()
assert w.input.placeholderText() == "Ask Gemini…"
assert "Gemini 4 Hydrogen · click to switch to Claude" == w.logo.label()
assert json.load(open(qapp.STATE)) == {"model": "gemini"}
assert qapp.load_state() == {"model": "gemini"}

# 6. each model keeps its own session; switching mid-conversation hands the conversation over
w.input.setText("first question")
w.input.returnPressed.emit()
assert asked[-1] == ("gemini", "first question"), asked
w.add_text("an answer from Gemini")
w.finish_turn()
a.gemini.session_id = "gem-1"
a.gemini.finished.emit()
assert saved[-1] == "gemini", saved
a.claude.session_id = "cla-1"
shortcut = next(s for s in w.shortcuts if s.key().toString() == "Alt+G")
shortcut.activated.emit()  # Alt+G in the bar
assert a.model == "claude" and a.claude.handover and "first question" in a.claude.fallback
assert "**Assistant:** an answer from Gemini" in a.claude.fallback, a.claude.fallback
assert a.gemini.session_id == "gem-1" and a.claude.session_id == "cla-1", "sessions mixed up"
a.toggle_model()  # straight back: Gemini answered everything visible, so no hand-over
assert a.model == "gemini" and not a.gemini.handover
a.toggle_model()
assert a.model == "claude"
# no agy: the switch is refused with a message, nothing goes into the conversation
agy_cli.find_agy = qapp.find_agy = lambda: None
turns_before = [list(t) for t in w.turns]
a.toggle_model()
assert a.model == "claude" and w.turns == turns_before and "Antigravity" in toasts[-1]
agy_cli.find_agy = qapp.find_agy = lambda: r"C:\fake\agy.exe"

# while an answer streams, the switch waits
w.busy = True
a.toggle_model()
assert a.model == "claude" and "Wait" in toasts[-1]
w.busy = False

# 7. Recent: a Gemini conversation reopens with Gemini, without a hand-over, and Claude's old
# conversation is forgotten (switching later hands this one over, and never saves over it)
a.resume_session({"id": "gem-2", "backend": "gemini", "title": "t", "turns": [["q", "a", ""]]})
assert a.model == "gemini" and a.gemini.session_id == "gem-2" and not a.gemini.handover
assert w.logo.model == "gemini" and a.gemini.briefed and a.gemini.seen == 1
assert a.claude.session_id is None, "Claude kept an unrelated conversation"
a.toggle_model()
assert a.model == "claude" and a.claude.handover and a.claude.session_id is None
a.toggle_model()

# 8. New starts a new conversation for both models
a.gemini.reset = lambda start=True: setattr(a.gemini, "session_id", None)
a.claude.reset = lambda start=True: setattr(a.claude, "session_id", None)
a.ask_new()
assert a.claude.session_id is None and a.gemini.session_id is None

# 9. animations off: the switch is instant
toolbar.user_animations = False
a.toggle_model()
assert w.logo.flip.value == 1 and w.card.accent.name() == askbar.ACCENT.name()
toolbar.user_animations = True
a.close_ask()

# 10. settings: the level, the shortcut (bar-only, checked against the others), state file
assert settings.DEFAULTS["model_toggle_key"] == "Alt+G"
values, problems = settings.validate({"hydrogen_level": "mega"})
assert values["hydrogen_level"] == "medium" and problems
assert not settings.validate({"model_toggle_key": ""})[1]
problems = settings.validate({"model_toggle_key": "Alt+S"})[1]
assert any("hotkeys.screenshot" in p or "model_toggle_key" in p for p in problems), problems
open(qapp.STATE, "w").write("not json")
assert qapp.load_state() == {}
a.set_model("bogus")
assert a.model in ("claude", "gemini")
# a duplicate key inside the bar: the first one keeps it, so it still works
w.set_shortcuts({"screenshot": "Alt+S", "window": "Alt+W", "voice": "Alt+M"}, "Alt+S")
assert [s.key().toString() for s in w.shortcuts].count("Alt+S") == 1
# a new hydrogen_level restarts an idle warm agy with it
stopped = []
a.gemini.proc, a.gemini.busy, a.gemini.started_level = object(), False, "medium"
a.gemini.stop = lambda: stopped.append(1)
a.settings.values["hydrogen_level"] = "high"
a.apply_settings()
assert stopped, "kept running on the old level"
a.gemini.proc = None

a.ask_key.disable()
a.watcher.stop()
print("Claude / Gemini switching: all checks passed")
