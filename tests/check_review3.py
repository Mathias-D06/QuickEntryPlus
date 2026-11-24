"""Self-check for the third review's fixes, F-01 to F-21. Mocked: Claude Code is replaced by
tests/fake_claude.py (no network, no sign-in), keystrokes are never sent (press is stubbed),
and the microphone and Gemini are fakes. It does use the real clipboard (saved and put back at
the end) and briefly a second Python process to copy things the way another app would.
Run from PowerShell: .venv\\Scripts\\python tests\\check_review3.py
"""

import contextlib
import ctypes
import inspect
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes as wt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import comtypes  # noqa: E402
import pywintypes  # noqa: E402
from PySide6.QtCore import Qt, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QTextDocument  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from qeplus import app as qapp  # noqa: E402
from qeplus import (askbar, claude_cli, deliver, keyhook, sessions, settings, uia,  # noqa: E402
                    win)
from qeplus.win import (QEPLUS_INPUT_TAG, clipboard_is_private, restore_clipboard,  # noqa: E402
                        snapshot_clipboard, user32)

qt = QApplication([])
tmp = tempfile.mkdtemp(prefix="qeplus-review3-")
CAPS, ESC, LSHIFT, RSHIFT = 0x14, 0x1B, 0xA0, 0xA1


def pump(s=0.1):
    end = time.monotonic() + s
    while time.monotonic() < end:
        qt.processEvents()
        time.sleep(0.01)


def pump_until(pred, seconds=15):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not pred():
        qt.processEvents()
        time.sleep(0.01)
    return pred()


@contextlib.contextmanager
def patched(obj, **attrs):
    old = {k: getattr(obj, k) for k in attrs}
    for k, v in attrs.items():
        setattr(obj, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(obj, k, v)


OTHER_APP = """
import sys, time, win32clipboard as c
text, formats = eval(sys.argv[1])
for _ in range(100):  # someone (this app, reacting to the last change) may hold it briefly
    try:
        c.OpenClipboard()
        break
    except Exception:
        time.sleep(0.02)
else:
    sys.exit("couldn't open the clipboard")
try:
    c.EmptyClipboard()
    if text is not None:
        c.SetClipboardText(text, c.CF_UNICODETEXT)
    for name, data in formats.items():
        c.SetClipboardData(c.RegisterClipboardFormat(name), data)
finally:
    c.CloseClipboard()
"""


def other_app_sets(text, formats):
    """Set the clipboard from a separate process, as another app would: text (None for none)
    plus any registered formats ({name: bytes}; pywin32 can't set a zero-length one)."""
    subprocess.run([sys.executable, "-c", OTHER_APP, repr((text, formats))], check=True)
    pump(0.3)


def other_app_copies(text, html=None, private=False):
    formats = {"HTML Format": html} if html else {}
    if private:
        formats["ExcludeClipboardContentFromMonitorProcessing"] = b"\0\0\0\0"
    other_app_sets(text, formats)


def has_format(name):
    return bool(user32.IsClipboardFormatAvailable(user32.RegisterClipboardFormatW(name)))


def format_count():
    assert win._open_clipboard()
    try:
        n, fmt = 0, 0
        while fmt := user32.EnumClipboardFormats(fmt):
            n += 1
        return n
    finally:
        user32.CloseClipboard()


users_clipboard = snapshot_clipboard()  # put back at the end

# --- the Claude Code session, against the fake CLI ----------------------------------------
LOG = os.path.join(tmp, "fake.log")
os.environ["FAKE_CLAUDE_LOG"] = LOG
os.environ["FAKE_CLAUDE_MODE"] = ""
claude_cli.WORKSPACE = os.path.join(tmp, "workspace")
claude_cli.ATTACHMENTS = os.path.join(claude_cli.WORKSPACE, "attachments")
os.makedirs(claude_cli.ATTACHMENTS)
real_popen, real_find_cli = subprocess.Popen, claude_cli.find_cli
procs = []


def fake_popen(args, **kw):
    proc = real_popen([sys.executable, os.path.join(ROOT, "tests", "fake_claude.py")] + args[1:],
                      **kw)
    procs.append(proc)
    return proc


def logged():
    with open(LOG, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def new_session():
    s = claude_cli.ClaudeSession()
    got = {"text": [], "done": 0, "failed": [], "notice": []}
    s.text.connect(got["text"].append)
    s.finished.connect(lambda: got.__setitem__("done", got["done"] + 1))
    s.failed.connect(got["failed"].append)
    s.notice.connect(got["notice"].append)
    return s, got


# F-01: never a .cmd/.bat shim; session ids are validated before reaching a command line
asked = []
with patched(claude_cli.shutil, which=lambda name: asked.append(name) or (
        r"C:\x\claude.exe" if name == "claude.exe" else r"C:\x\claude.cmd")):
    assert real_find_cli() == r"C:\x\claude.exe" and asked == ["claude.exe"], asked
for good in ("0f0e0d0c-0000-4000-8000-000000000000", "abc_123", "a"):
    assert claude_cli.valid_session_id(good), good
for bad in ("", "-rf", "--resume", "a b", "a;b", "a\nb", "x" * 129, None, 5, "é"):
    assert not claude_cli.valid_session_id(bad), bad
# nothing on PATH: the newest copy bundled with Claude Desktop
bundle = os.path.join(tmp, "Claude-Data", "Claude_x", "claude-code", "2.1.0", "abc")
os.makedirs(bundle)
open(os.path.join(bundle, "claude.exe"), "wb").close()
old_local = os.environ["LOCALAPPDATA"]
os.environ["LOCALAPPDATA"] = tmp
with patched(claude_cli.shutil, which=lambda name: None):
    assert real_find_cli() == os.path.join(bundle, "claude.exe"), real_find_cli()
os.environ["LOCALAPPDATA"] = old_local
claude_cli.subprocess.Popen, claude_cli.find_cli = fake_popen, lambda: r"C:\fake\claude.exe"
os.environ["ANTHROPIC_API_KEY"], os.environ["CLAUDE_TEST"] = "sk-test-not-real", "1"
s, got = new_session()
s.session_id = "--evil x"
s.start()
assert pump_until(lambda: os.path.exists(LOG)), "the fake CLI didn't start"
assert s.session_id is None and "--resume" not in logged()[-1]["args"], logged()
for flag in ("--tools", "--setting-sources", "--strict-mcp-config"):  # sandbox flags kept
    assert flag in logged()[-1]["args"], flag
child_env = logged()[-1]["env"]
assert "ANTHROPIC_API_KEY" not in child_env and "CLAUDE_TEST" not in child_env, child_env
assert "FAKE_CLAUDE_LOG" in child_env  # stripped selectively, not wholesale
del os.environ["ANTHROPIC_API_KEY"], os.environ["CLAUDE_TEST"]
s.stop()
print("F-01 ok")

# F-13: malformed lines and events are skipped; bad session ids aren't adopted; stale events
# from an older generation are dropped
s, got = new_session()
s.ask("hi")
assert pump_until(lambda: got["done"]), got
assert "".join(got["text"]) == "hello world" and s.session_id == "fake-session-1", got
s._relay(s.gen - 1, "text", "stale")
assert "stale" not in got["text"]
s.ask("again", [os.path.join(tmp, "missing.png")])  # unreadable attachment: failed, no crash
assert pump_until(lambda: got["failed"]) and "Couldn't send" in got["failed"][0], got
s.stop()
print("F-13 ok")

# F-20 (part): attachments are deleted once read, but only from our own folder
inside = os.path.join(claude_cli.ATTACHMENTS, "capture.png")
outside = os.path.join(tmp, "elsewhere.png")
for path in (inside, outside):
    with open(path, "wb") as f:
        f.write(b"\x89PNG fake")
s, got = new_session()
s.ask("look", [inside, outside])
assert pump_until(lambda: got["done"]), got
content = logged()[-1]["msg"]["message"]["content"]
assert [c["type"] for c in content] == ["image", "image", "text"], content
assert not os.path.exists(inside) and os.path.exists(outside)
s.stop()
# N-07: a send that fails (dead pipe) keeps the screenshot
keep = os.path.join(claude_cli.ATTACHMENTS, "keep.png")
with open(keep, "wb") as f:
    f.write(b"\x89PNG fake")
s, got = new_session()
s.start()
s.proc.stdin.close()
s.ask("look", [keep])
assert pump_until(lambda: got["failed"]) and os.path.exists(keep), got
s.stop()
print("N-07 ok")

# F-03: stderr is drained (1 MB of it no longer deadlocks) and used in error reports
os.environ["FAKE_CLAUDE_MODE"] = "noisy"
s, got = new_session()
s.ask("hi")
assert pump_until(lambda: got["done"]), "stalled: stderr not drained"
s.stop()
os.environ["FAKE_CLAUDE_MODE"] = "crash"
s, got = new_session()
s.ask("hi")
assert pump_until(lambda: got["failed"]) and "signed in" in got["failed"][0], got
print("F-03 ok")

# F-14: stop() never blocks the UI; a process that can't be contained is killed, not used
os.environ["FAKE_CLAUDE_MODE"] = "stubborn"
s, got = new_session()
s.ask("hi")
assert pump_until(lambda: got["done"])
started = time.monotonic()
s.stop()
assert time.monotonic() - started < 0.3, "stop() blocked"
assert procs[-1].wait(5) is not None, "a stopped process never ended"
os.environ["FAKE_CLAUDE_MODE"] = ""


def no_job(proc):
    raise pywintypes.error(5, "AssignProcessToJobObject", "Access is denied.")


with patched(claude_cli, _contain=no_job):
    s, got = new_session()
    s.start()
assert s.proc is None and got["failed"] == ["Couldn't start Claude Code safely. Try again."]
assert procs[-1].wait(5) is not None, "uncontained process left running"
print("F-14 ok")

# F-18 (part): a resumed session that no longer exists continues with the transcript
s, got = new_session()
s.session_id, s.fallback = "dead-id", "**Me:** what's 2+2\n\n**Claude:** 4"
s.ask("and times 3?")
assert pump_until(lambda: got["done"]), got
assert got["notice"] and "couldn't be resumed" in got["notice"][0], got
assert s.session_id == "fake-session-1" and not got["failed"], (s.session_id, got)
sent = logged()[-1]["msg"]["message"]["content"]
assert "what's 2+2" in sent[0]["text"] and sent[-1]["text"] == "and times 3?", sent
assert "--resume" not in [e for e in logged() if "args" in e][-1]["args"]
s.stop()
# N-06: the session is gone already while warming (after Recent), reported as JSON or only on
# stderr: no error on the history, and the next question carries the transcript
for mode in ("dead_startup", "dead_stderr"):
    os.environ["FAKE_CLAUDE_MODE"] = mode
    s, got = new_session()
    s.session_id, s.fallback = "gone-1", "**Me:** an earlier question"
    s.start()
    assert pump_until(lambda: s.resume_fallback_pending, 5), mode
    assert s.session_id is None and not got["failed"], (mode, got)
    s.ask("follow-up")
    assert pump_until(lambda: got["done"]), (mode, got)
    sent = logged()[-1]["msg"]["message"]["content"]
    assert "an earlier question" in sent[0]["text"] and sent[-1]["text"] == "follow-up", sent
    assert got["notice"] and not got["failed"] and s.last is None, (mode, got)
    s.stop()
os.environ["FAKE_CLAUDE_MODE"] = ""
print("N-06 ok")
claude_cli.subprocess.Popen, claude_cli.find_cli = real_popen, real_find_cli

# F-18 (part): strict loading, size caps, unique temp files, invalid ids not saved
path = os.path.join(tmp, "sessions.json")
ok = {"id": "good-1", "title": "t", "backend": "claude", "created": 1.0, "updated": 2.0,
      "turns": [["q", "a", ""]]}
bad = [dict(ok, id="--evil"), dict(ok, id="a b"), dict(ok, id="x" * 129),
       dict(ok, turns=[["q", "a"]]), dict(ok, turns=[["q", 5, ""]]), dict(ok, turns=[]),
       dict(ok, created="yesterday"), dict(ok, title=None), "junk", None,
       # N-10: timestamps that would crash when(), and text over the cap
       dict(ok, created=float("nan")), dict(ok, updated=float("inf")), dict(ok, created=True),
       dict(ok, updated=1e20), dict(ok, created=-1),
       dict(ok, turns=[["q", "a" * (sessions.MAX_TEXT + 1), ""]])]
with open(path, "w", encoding="utf-8") as f:
    json.dump([ok] + bad, f)
assert [e["id"] for e in sessions.load(path)] == ["good-1"]
many = [[f"q{i}", "a", ""] for i in range(sessions.MAX_TURNS + 5)]
sessions.save("good-3", many, path=path)  # the oldest turns are dropped
kept = sessions.load(path)[0]["turns"]
assert len(kept) == sessions.MAX_TURNS and kept[0][0] == "q5", (len(kept), kept[0])
with open(path, "w", encoding="utf-8") as f:
    json.dump([ok], f)
sessions.save("good-2", [["Q" * 10, "A" * (sessions.MAX_TEXT + 50), ""]], path=path)
saved = sessions.load(path)
assert saved[0]["id"] == "good-2" and len(saved[0]["turns"][0][1]) == sessions.MAX_TEXT
sessions.save("bad id", [["q", "a", ""]], path=path)
assert [e["id"] for e in sessions.load(path)] == ["good-2", "good-1"]
assert not [n for n in os.listdir(tmp) if n.endswith(".tmp")], os.listdir(tmp)
print("F-18 ok (resume fallback, validation, caps, atomic write)")

# --- the key logic -----------------------------------------------------------------------
L = keyhook.KeyLogic(ptt=CAPS)
# F-11: Shift tracked per key; Caps key-up swallowed only when its key-down was
assert [L.event(*e) for e in ((LSHIFT, True, False, 0), (RSHIFT, True, False, 0),
                              (LSHIFT, False, False, 0), (CAPS, True, False, 0),
                              (RSHIFT, False, False, 0), (CAPS, False, False, 0))] \
    == [(False, None)] * 6, "right Shift still held: Caps Lock must toggle normally"
L.ptt_active = True
L.configure(CAPS, set())
assert not L.ptt_active and not L.shifts, "configure must reset state"
results = []  # the lock really serialises event() against the Qt thread
with L.lock:
    worker = threading.Thread(target=lambda: results.append(L.event(0x41, True, False, 0)))
    worker.start()
    worker.join(0.2)
    assert worker.is_alive() and not results, "event() ran while the lock was held"
worker.join(2)
assert results == [(False, None)], results
print("F-11 ok")
# N-01: only lock/unlock/disconnect and suspend/resume, sent to our window, reset the hook
fired = []
session_filter = keyhook._SessionFilter(1234, lambda: fired.append(1))


def deliver_msg(hwnd, message, wparam):
    m = wt.MSG()
    m.hWnd, m.message, m.wParam = hwnd, message, wparam
    session_filter.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(m))


deliver_msg(1234, keyhook.WM_POWERBROADCAST, 0x000A)  # PBT_APMPOWERSTATUSCHANGE (battery/AC)
deliver_msg(1234, keyhook.WM_POWERBROADCAST, 0x8013)  # PBT_POWERSETTINGCHANGE
deliver_msg(1234, keyhook.WM_WTSSESSION_CHANGE, 0x1)  # WTS_CONSOLE_CONNECT
assert fired == [], "fired on a power-status or connect event"
deliver_msg(1234, keyhook.WM_POWERBROADCAST, 0x4)  # PBT_APMSUSPEND
deliver_msg(1234, keyhook.WM_WTSSESSION_CHANGE, 0x7)  # WTS_SESSION_LOCK
assert fired == [1, 1], fired
deliver_msg(9999, keyhook.WM_POWERBROADCAST, 0x4)  # broadcast to another of our windows
deliver_msg(9999, keyhook.WM_WTSSESSION_CHANGE, 0x7)
assert fired == [1, 1], "fired for a window other than the registered one"
print("N-01 ok")
# F-10: Esc while holding cancels (down and up swallowed); Esc otherwise passes
assert [L.event(*e) for e in ((CAPS, True, False, 0), (ESC, True, False, 0.1),
                              (ESC, False, False, 0.2), (CAPS, False, False, 0.3))] \
    == [(True, "ptt_down"), (True, "ptt_cancel"), (True, None), (True, "ptt_up")]
assert L.event(ESC, True, False, 1) == (False, None) and L.event(ESC, False, False, 1) == (
    False, None)
assert settings.DEFAULTS["push_to_talk"] is False
# F-21: our own tagged input (and any injected input) is ignored; SendInput carries the tag
assert L.event(CAPS, True, False, 2, QEPLUS_INPUT_TAG) == (False, None)
assert L.event(CAPS, True, True, 2) == (False, None) and not L.ptt_active
assert "QEPLUS_INPUT_TAG" in inspect.getsource(win._send)
print("F-21 ok")
# F-07: no logging inside the hook callback; an exception is counted and the key passes
assert "log." not in inspect.getsource(keyhook.KeyHook._callback)
kh = keyhook.KeyHook()


def boom(*_):
    raise RuntimeError("bug")


kh.logic.event = boom
k = keyhook.KBDLLHOOKSTRUCT(0x41, 0, 0, 0, 0)
with patched(keyhook.user32, CallNextHookEx=lambda *args: 42):
    passed_on = kh._callback(0, keyhook.WM_KEYDOWN, ctypes.addressof(k))
assert kh.errors == 1 and passed_on == 42, "a key whose handling failed must be passed on"
print("F-07 ok")

# F-06: the default doesn't clash with AltGr; a clashing choice is kept but warned about
assert settings.DEFAULTS["ask_selection_hotkey"] == "Ctrl+Alt+Shift+Q"
assert settings.altgr_clash("Ctrl+Alt+Q") and settings.altgr_clash("Ctrl+Alt+7")
assert not settings.altgr_clash("Ctrl+Alt+Shift+Q") and not settings.altgr_clash("Ctrl+Alt+F5")
values, problems = settings.validate({"ask_selection_hotkey": "Ctrl+Alt+Q"})
assert values["ask_selection_hotkey"] == "Ctrl+Alt+Q" and "AltGr" in problems[0], problems
assert settings.validate(dict(settings.DEFAULTS))[1] == []
print("F-06 ok")
# N-11: a kept hotkey gets a warning, never "Using the default for those."
values, problems = settings.validate({"ask_selection_hotkey": "Ctrl+Alt+Q "})
assert values["ask_selection_hotkey"] == "Ctrl+Alt+Q ", values["ask_selection_hotkey"]
assert problems and all(isinstance(p, settings.Notice) for p in problems), problems
assert "is AltGr+Q on" in problems[0], problems
settings_path = os.path.join(tmp, "settings.json")
with open(settings_path, "w", encoding="utf-8") as f:
    json.dump({"ask_selection_hotkey": "Ctrl+Alt+Q "}, f)
st = settings.Settings(settings_path)
messages = []
st.problem.connect(messages.append)
st.start()
assert messages and not any("Using the default" in m for m in messages), messages
print("N-11 ok")

# --- the app ---------------------------------------------------------------------------
qapp.ATTACHMENTS = claude_cli.ATTACHMENTS
a = qapp.App()
a.keyhook.stop()
toasts, pressed, typed, saves = [], [], [], []
a.toast.show_message = toasts.append
qapp.press = lambda *keys: pressed.append(keys)
deliver.press = lambda *keys: pressed.append(keys)
qapp.type_text = typed.append

# F-18 (part): a session is saved after an answer, never after an error
with patched(qapp.sessions, save=lambda sid, turns, **kw: saves.append(sid)):
    a.claude.session_id = "abc-1"
    a.ask.turns = [["q", "a", ""]]
    a.claude.failed.emit("boom")
    assert saves == []
    a.claude.finished.emit()
    assert saves == ["abc-1"]
a.claude.stop()
a.ask.clear_conversation()


class FakeRecorder:
    recording = False

    def start(self):
        self.recording = True

    def stop(self):
        self.recording = False
        return b"\0" * (44 + 64000)


class FakeClaude:
    busy, session_id, fallback = False, None, ""

    def start(self):
        pass

    def check_account(self):
        pass

    def stop(self):
        pass

    def ask(self, text, images):
        asks.append(text)


transcribed, asks = [], []
a.recorder = FakeRecorder()
a.transcriber.start = lambda wav, *args: transcribed.append(len(wav))
a.claude = FakeClaude()

# F-02: secure desktop, admin window, lock/sleep and the 5-minute cap all discard
for checks in ({"input_desktop_ok": lambda: False},
               {"input_desktop_ok": lambda: True, "is_elevated": lambda h: True}):
    a.ptt_start()
    assert a.recorder.recording and a.ptt_watchdog.isActive()
    with patched(qapp, **checks):
        a.check_ptt()
    assert not a.recorder.recording and a.target == "popup" and not transcribed, checks
    assert "cancelled" in toasts[-1] and not a.ptt_watchdog.isActive()
a.ptt_start()
a.keyhook.logic.ptt_active = True
a.on_session_change()
assert not a.recorder.recording and not a.keyhook.logic.ptt_active and not transcribed
a.ptt_start()
a.keyhook.logic.ptt_active = True  # N-09: the key-up we never saw
a.on_recording_limit()
assert not a.recorder.recording and not transcribed and "5 minutes" in toasts[-1]
assert not a.keyhook.logic.ptt_active, "ptt_active left stuck after the 5-minute cap"
# N-09: turning push-to-talk off mid-hold ends the recording at once
a.settings.values["push_to_talk"] = True
a.configure_keyhook()
a.ptt_start()
assert a.recorder.recording
a.settings.values["push_to_talk"] = False
a.configure_keyhook()
a.keyhook.stop()
assert not a.recorder.recording and a.target == "popup" and not transcribed
print("F-02 ok (and N-09)")

# F-10: Esc cancels; clicking away keeps a push-to-talk recording; one transcription at a
# time; a dictated question never sends someone's draft, attachments or context with it
a.ptt_start()
a.keyhook.ptt_cancel.emit()
assert not a.recorder.recording and toasts[-1] == "Recording cancelled." and not transcribed
a.ptt_start()
a.on_hidden()
assert a.recorder.recording, "the pop-up hiding stopped a push-to-talk recording"
a.ptt_stop()
assert transcribed and a.transcribing
a.ptt_start()
assert not a.recorder.recording, "started a second recording while transcribing"
a.ask.input.setText("my draft")
a.on_transcript("and more")
assert not a.transcribing and asks == [] and a.ask.input.text() == "my draft and more"
assert "press Enter" in toasts[-1]
a.close_ask()
a.ask.input.clear()
a.target = "ptt"
a.on_transcript("What is a monad?")
assert asks == ["What is a monad?"], asks
a.close_ask()
a.ask.clear_conversation()
transcribed.clear()
asks.clear()
# N-04: nothing heard: the "Transcribing…" pill goes and push-to-talk is ready again
a.ptt_start()
a.ptt_stop()
assert a.mic.isVisible()
a.on_transcript("")
assert not a.mic.isVisible() and a.target == "popup" and not a.transcribing
transcribed.clear()
# attachments, a chip or an answer still streaming also mean "don't send for me"
for field, value in (("attachments", ["x.png"]), ("context", "ctx"), ("busy", True)):
    setattr(a.ask, field, value)
    a.target = "ptt"
    a.on_transcript("hello")
    assert asks == [] and "press Enter" in toasts[-1], field
    a.ask.attachments, a.ask.context, a.ask.busy = [], "", False
    a.ask.input.clear()
    a.close_ask()
# voice_autosend never sends over a draft, but does send when the bar was empty
a.settings.values["voice_autosend"] = True
a.open_ask()
a.ask.input.setText("draft")
a.target = "ask"
a.on_transcript("more")
assert asks == [] and a.ask.input.text() == "draft more", (asks, a.ask.input.text())
a.ask.input.clear()
a.target = "ask"
a.on_transcript("send me")
assert asks == ["send me"], asks
a.settings.values["voice_autosend"] = False
a.close_ask()
a.ask.clear_conversation()
asks.clear()
print("F-10 ok (and N-04)")

# F-15: Pause releases everything
a.target = "ask"
a.recorder.recording = True
a.deliverer.active = True  # as if waiting for Claude's chat box
a.set_paused(True)
a.keyhook.stop()
assert not a.recorder.recording and a.target == "popup" and not transcribed
assert not a.deliverer.active
a.deliverer._paste()
assert pressed == [], "pasted while paused"
opened = []
with patched(a, open_popup=lambda: opened.append(1)):
    a.busy = True
    a.on_cancelled()
assert not opened and not a.busy, "the snipper's cancel reopened the pop-up while paused"
a.on_transcript("late words")
assert typed == [] and asks == []
a.moved_draft = "draft"
a.clear_moved_draft()
assert pressed == []
# timers that fire after Pause: a finished capture, a screen grab, waiting for the pop-up
delivered, ran = [], []
with patched(a.deliverer, start=lambda *args: delivered.append(args)), \
        patched(a, open_popup=lambda: ran.append("open")):
    a.target = "popup"
    a.on_captured(QImage(4, 4, QImage.Format.Format_RGB32))
    a.grab_screen(1)
    a.with_popup(lambda: ran.append("action"), lambda: ran.append("give up"))
    pump(0.3)
assert delivered == [] and ran == [] and pressed == [] and not a.ask.attachments, (
    delivered, ran)
a.set_paused(False)
a.keyhook.stop()
a.ask.set_shortcuts({"screenshot": "Alt+X", "window": "", "voice": "Alt+V"})
assert [sc.key().toString() for sc in a.ask.shortcuts] == ["Alt+X", "Alt+V"]
assert a.ask.buttons["screenshot"].shortcut == "Alt+X" and a.ask.buttons["window"].shortcut == ""
a.apply_settings()  # settings changes reach the bar's local keys
a.keyhook.stop()
assert len(a.ask.shortcuts) == sum(1 for c in a.settings["hotkeys"].values() if c) + bool(
    a.settings["model_toggle_key"])  # plus the bar's Claude/Gemini switch
print("F-15 ok")

# F-19: the largest unowned window by normal size wins; minimised windows are restored;
# launch falls back to the running claude.exe; Open in Claude never raises
calls = []
sizes = {1: 50, 2: 900, 3: 400}  # 1 is an owned dialog in front, 2 is minimised but largest
with patched(qapp, find_windows=lambda pred: [1, 2, 3], normal_area=lambda h: sizes[h]), \
        patched(user32, IsWindowVisible=lambda h: True,
                GetWindow=lambda h, kind: 77 if h == 1 else 0,
                IsIconic=lambda h: h == 2,
                ShowWindow=lambda h, cmd: calls.append(("show", h, cmd)),
                SetForegroundWindow=lambda h: calls.append(("front", h))):
    a.focus_claude()
assert calls == [("show", 2, 9), ("front", 2)], calls


def no_start(target):
    raise OSError("not found")


launched = []
with patched(qapp, find_windows=lambda pred: [5], exe_path=lambda h: r"C:\Claude\claude.exe"), \
        patched(qapp.os, startfile=no_start), \
        patched(qapp.subprocess, Popen=lambda args, **kw: launched.append(args)), \
        patched(user32, IsWindowVisible=lambda h: False):
    a.focus_claude()
    assert launched == [[r"C:\Claude\claude.exe"]], launched
    a.ask.turns = [["q", "a", ""]]
    a.ask_open_in_claude()
    assert "Couldn't open Claude" in toasts[-1]
a.ask.clear_conversation()
print("F-19 ok")

# F-20 (part): removing attachments deletes their files; Recent titles are one plain line
png = os.path.join(claude_cli.ATTACHMENTS, "x.png")
open(png, "wb").close()
a.ask.add_attachment(png)
a.ask.chip.click()
assert not os.path.exists(png) and not a.ask.attachments
listed = []


class FakeMenu(qapp.QMenu):
    def exec(self, *_):
        listed.extend(act.text() for act in self.actions())


entry = {"id": "t-1", "title": "Tom & Jerry\tpart\n2", "updated": time.time(),
         "turns": [["q", "a", ""]]}
with patched(qapp, QMenu=FakeMenu), patched(qapp.sessions, load=lambda: [entry]):
    a.show_recent()
assert listed[0].startswith("Tom && Jerry part 2\t"), listed
print("F-20 ok")

# --- selection and the clipboard ------------------------------------------------------------
a.close_ask()
a.ask.clear_context()
pressed.clear()
fg_real = user32.GetForegroundWindow

# F-05: UIA selection first (no keys, clipboard untouched); passwords and risky terminals are
# refused; Ctrl+Insert in terminals that support it
with patched(qapp.uia, focused_selection=lambda: ("from UIA", "")):
    a.ask_about_selection()
assert pressed == [] and a.ask.context == "from UIA"
a.close_ask()
a.ask.clear_context()
with patched(qapp.uia, focused_selection=lambda: (None, "password")):
    a.ask_about_selection()
assert pressed == [] and "password" in toasts[-1]
with patched(qapp.uia, focused_selection=lambda: (None, "terminal")), \
        patched(qapp, exe_name=lambda h: "code.exe", text_of=lambda h, g: "Chrome_WidgetWin_1"):
    a.ask_about_selection()
assert pressed == [] and "terminal" in toasts[-1]
for exe, cls, kind, key in (("putty.exe", "PuTTY", "", None),
                            ("windowsterminal.exe", "CASCADIA_HOSTING_WINDOW_CLASS", "", 0x2D),
                            ("mintty.exe", "mintty", "", 0x2D),
                            ("notepad.exe", "Notepad", "", ord("C")),
                            ("code.exe", "Chrome_WidgetWin_1", "terminal", None),
                            ("notepad.exe", "Notepad", "unknown", None),
                            ("mstsc.exe", "TscShellContainerClass", "", None),
                            ("msrdc.exe", "RAIL_WINDOW", "", None),
                            ("vmconnect.exe", "WindowsForms10", "", None),
                            ("mobaxterm_personal_24.2.exe", "TMobaXtermForm", "", None)):
    with patched(qapp, exe_name=lambda h, e=exe: e, text_of=lambda h, g, c=cls: c):
        assert qapp.copy_key(1, kind) == key, exe
# N-05: an admin window is refused before UIA crosses into it
queried = []
with patched(qapp, is_elevated=lambda h: True), \
        patched(qapp.uia, focused_selection=lambda: queried.append(1) or (None, "")):
    a.ask_about_selection()
assert queried == [] and pressed == [] and "admin" in toasts[-1], (queried, toasts[-1])
# N-05: focused_selection itself, against a fake UI Automation tree
with patched(qapp.uia, focused_selection=lambda: ("   \n\t ", "")), \
        patched(qapp, copy_key=lambda h, kind="": None):  # stop before any copy starts
    a.ask_about_selection()
assert a.ask.context == "" and not a.copying_selection, "whitespace taken as a selection"


class FakeRange:
    def __init__(self, text):
        self.text, self.asked = text, []

    def GetText(self, limit):
        self.asked.append(limit)
        return self.text  # ignores the limit, as a careless provider might


class FakeRanges:
    def __init__(self, texts):
        self.items = [FakeRange(t) for t in texts]
        self.Length = len(texts)

    def GetElement(self, i):
        return self.items[i]


class FakePattern:
    def __init__(self, ranges):
        self.ranges = ranges

    def QueryInterface(self, iface):
        return self

    def GetSelection(self):
        return self.ranges


class FakeElement:
    def __init__(self, aid="", cls="", password=False, ranges=None, name=""):
        self.CurrentAutomationId, self.CurrentClassName = aid, cls
        self.CurrentIsPassword, self.CurrentName, self.ranges = password, name, ranges

    def GetCurrentPattern(self, pattern_id):
        return FakePattern(self.ranges)


class UnreadableElement:
    @property
    def CurrentIsPassword(self):
        raise comtypes.COMError(-2147220991, "element gone", None)


def selection_of(element):
    auto = type("FakeAuto", (), {"GetFocusedElement": lambda self: element})()
    with patched(uia, _automation=lambda: auto):
        return uia.focused_selection()


assert selection_of(FakeElement(password=True, ranges=FakeRanges(["pw"]))) == (None, "password")
assert selection_of(FakeElement(cls="xterm-helper-textarea")) == (None, "terminal")
assert selection_of(UnreadableElement()) == (None, "unknown")
big = FakeRanges(["x" * (uia.MAX_SELECTION + 500)])
text, kind = selection_of(FakeElement(ranges=big))
assert len(text) == uia.MAX_SELECTION and big.items[0].asked == [uia.MAX_SELECTION]
assert selection_of(FakeElement(ranges=FakeRanges([" \n\t "]))) == (None, "")
tab = FakeElement(name="terminal.py", aid="tab-3", cls="Chrome_RenderWidgetHostHWND",
                  ranges=FakeRanges(["print(1)"]))
assert selection_of(tab) == ("print(1)", ""), "an editor tab named terminal.py isn't a terminal"
print("F-05 ok (and N-05)")

# F-04: password managers' and our own clipboard writes are never offered; a chip offered for
# one clipboard never attaches a later one
deliver.put_on_clipboard(text="ours")
pump(0.2)
assert clipboard_is_private() and a.clip_changed == 0.0
other_app_copies("secret", private=True)
assert clipboard_is_private() and a.clip_changed == 0.0, "offered a password"
other_app_copies("plain text")
assert not clipboard_is_private() and a.clip_changed
a.open_ask()
pump(0.2)
assert a.ask.clip_chip.isVisible()
other_app_copies("swapped in later")
a.ask.clip_chip.click()
assert a.ask.context == "" and "changed" in toasts[-1], (a.ask.context, toasts[-1])
a.close_ask()
# each privacy flag on its own; a malformed (1-byte) flag fails closed
for formats, private in (({"Clipboard Viewer Ignore": b"\0"}, True),
                         ({"CanIncludeInClipboardHistory": b"\0\0\0\0"}, True),
                         ({"CanUploadToCloudClipboard": b"\0\0\0\0"}, True),
                         ({"CanIncludeInClipboardHistory": b"\1\0\0\0"}, False),
                         ({"CanIncludeInClipboardHistory": b"\1"}, True)):
    other_app_sets("x", formats)
    assert clipboard_is_private() is private, formats
print("F-04 ok (and N-03b)")

# F-09: the whole clipboard (HTML here) comes back after copying a selection, marked private
other_app_copies("ORIGINAL", html=b"Version:0.9\r\n<b>ORIGINAL</b>")
snap = snapshot_clipboard()
assert snap and has_format("HTML Format")
deliver.put_on_clipboard(text="overwritten")
pump(0.1)
assert restore_clipboard(snap)
assert QGuiApplication.clipboard().text() == "ORIGINAL" and has_format("HTML Format")
assert clipboard_is_private(), "a restored clipboard must not land in Win+V history again"
assert not has_format("ExcludeClipboardContentFromMonitorProcessing"), \
    "a normal clipboard was hidden from clipboard managers after restore"
# an empty clipboard comes back empty, with no privacy formats stamped on it
other_app_sets(None, {})
snap = snapshot_clipboard()
assert snap is not None and snap.items == [], snap
deliver.put_on_clipboard(text="overwritten")
pump(0.1)
assert restore_clipboard(snap) and format_count() == 0, format_count()
# a "Clipboard Viewer Ignore" marker survives
other_app_sets("hidden", {"Clipboard Viewer Ignore": b"\0"})
snap = snapshot_clipboard()
deliver.put_on_clipboard(text="overwritten")
pump(0.1)
assert restore_clipboard(snap) and has_format("Clipboard Viewer Ignore")
assert QGuiApplication.clipboard().text() == "hidden"
# too big to save: nothing is copied (and no keys are sent)
other_app_copies("big enough")
pressed.clear()
with patched(win, SNAPSHOT_LIMIT=1):
    assert snapshot_clipboard() is None
    with patched(qapp.uia, focused_selection=lambda: (None, "")), \
            patched(qapp, copy_key=lambda h, kind="": ord("C"), is_elevated=lambda h: False):
        a.ask_about_selection()
assert pressed == [] and "Couldn't read your clipboard" in toasts[-1], toasts[-1]
other_app_copies("ORIGINAL", html=b"Version:0.9\r\n<b>ORIGINAL</b>")
with patched(qapp.uia, focused_selection=lambda: (None, "")), \
        patched(qapp, copy_key=lambda h, kind="": ord("C"), is_elevated=lambda h: False,
                press=lambda *k: other_app_copies("SELECTED TEXT")):
    a.ask_about_selection()
    assert pump_until(lambda: a.ask.context == "SELECTED TEXT", 3), a.ask.context
assert QGuiApplication.clipboard().text() == "ORIGINAL" and has_format("HTML Format")
pump(0.2)
assert not a.copying_selection
a.close_ask()
a.ask.clear_context()
print("F-09 ok")

# F-12: no second copy while one is in flight; nothing sent if focus moves first; no restore
# over something copied after our copy
pressed.clear()
a.copying_selection = True
with patched(qapp.uia, focused_selection=lambda: (None, "")):
    a.ask_about_selection()
assert pressed == []
a.copying_selection = False
windows = iter([100, 200, 200, 200])
with patched(qapp.uia, focused_selection=lambda: (None, "")), \
        patched(qapp, copy_key=lambda h, kind="": ord("C"), is_elevated=lambda h: False), \
        patched(user32, GetForegroundWindow=lambda: next(windows)):
    a.ask_about_selection()
assert pressed == [] and not a.copying_selection, "copied after focus moved"
restored = []
seqs = iter([11, 12])  # the copy (11), then someone copies again (12) before we restore
with patched(qapp, restore_clipboard=lambda *args, **kw: restored.append(args)), \
        patched(user32, GetClipboardSequenceNumber=lambda: next(seqs, 12)):
    a.finish_selection(10, win.ClipboardSnapshot([], False))
assert restored == [], "restored over a newer copy"
a.close_ask()
a.ask.clear_context()
print("F-12 ok")

# --- the answer card -----------------------------------------------------------------------
w = a.ask
copied = []
w.copied.connect(copied.append)

# F-08: nothing an answer refers to is loaded; copied text in a tooltip is shown, not parsed
assert isinstance(w.view, askbar.SafeTextBrowser)
assert w.view.loadResource(QTextDocument.ResourceType.ImageResource,
                           QUrl("file:///C:/Windows/win.ini")) is None
w.set_context("<b>bold</b><img src='file:///C:/x.png'>", "Selected text")
tip = w.context_chip.toolTip()
assert "&lt;b&gt;bold&lt;/b&gt;&lt;img" in tip and "<img" not in tip, tip
w.clear_context()
print("F-08 ok")

# F-17: only this window's copy links work; malformed ones are ignored; hover shows the target
w.turns = [["q", "```py\nprint(1)\n```", ""]]
w.card.show()
w._render()
for url in ("copy:0", "copy-0000000000000000:0:0", f"copy-{w.nonce}:x:y", f"copy-{w.nonce}:9:9",
            f"copy-{w.nonce}:", "file:///C:/Windows/win.ini", "javascript:alert(1)"):
    w._link(QUrl(url))
assert copied == [], copied
w._link(QUrl(f"copy-{w.nonce}:0:0"))
assert copied == ["print(1)"], copied
w._hover_link(QUrl("https://example.com/a"))
assert w.hint.text() == "https://example.com/a"
w._hover_link(QUrl())
assert w.hint.text() == "Esc to close"
# N-02: a hovered URL with encoded HTML is shown as plain text, never parsed
assert w.hint.textFormat() == Qt.TextFormat.PlainText
w._hover_link(QUrl("https://a.co/%3Cimg%20src=//evil/a%3E"))
assert w.hint.textFormat() == Qt.TextFormat.PlainText and w.hint.text().startswith("https://a.co/")
w._hover_link(QUrl())
print("F-17 ok (and N-02)")

# F-16: only the streaming turn is converted again; long answers render less often; no screen
# at all doesn't crash
conversions = []
real_md = askbar.markdown_html
askbar.markdown_html = lambda md, codes, link="copy:": conversions.append(md) or real_md(
    md, codes, link)
w.turns = [["one", "first answer", ""], ["two", "second answer", ""]]
w._render()
conversions.clear()
w.turns[-1][1] += " grows"
w._render()
assert conversions == ["second answer grows"], conversions
askbar.markdown_html = real_md
w.busy = True
w.add_text("x" * (askbar.LONG_ANSWER + 1))
assert w.render_timer.interval() == 100
w.busy = False
w.render_timer.stop()
with patched(w, screen=lambda: None), \
        patched(askbar, QGuiApplication=type("NoScreens", (), {
            "primaryScreen": lambda: None, "screenAt": lambda p: None, "screens": lambda: []})):
    w._fit()
w.clear_conversation()
print("F-16 ok")

# README documents the changed defaults and the PowerToys hook order
readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
assert "Ctrl+Alt+Shift+Q" in readme and "PowerToys" in readme and '"push_to_talk": false' in readme

a.hotkeys.disable()
a.ask_key.disable()
a.watcher.stop()
if users_clipboard is not None:
    restore_clipboard(users_clipboard)
print("review 3 fixes F-01..F-21: all checks passed")
