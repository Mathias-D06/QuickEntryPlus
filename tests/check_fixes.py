"""Self-check for the review fixes (REVIEW.md S1-S7, G1-G10). Keys, typing, Claude and Gemini
are mocked; the screenshot overlay briefly dims the screen for test 6.
Run: .venv\\Scripts\\python tests\\check_fixes.py
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import win32clipboard as cb  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from qeplus import app as qapp  # noqa: E402
from qeplus import deliver, snip, uia, voice, watcher  # noqa: E402

qt = QApplication([])


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qt.processEvents()
        time.sleep(0.01)


def clipboard_formats():
    names, f = [], 0
    cb.OpenClipboard()
    try:
        while f := cb.EnumClipboardFormats(f):
            try:
                names.append(cb.GetClipboardFormatName(f))
            except Exception:
                pass  # standard formats have no registered name
    finally:
        cb.CloseClipboard()
    return names


img = QImage(8, 8, QImage.Format.Format_RGB32)
img.fill(QColor("red"))

# S1: captures and transcripts stay out of clipboard history and cloud sync
for kwargs in ({"image": img}, {"text": "a transcript"}):
    deliver.put_on_clipboard(**kwargs)
    pump(0.1)
    assert set(deliver.PRIVATE_FORMATS) <= set(clipboard_formats()), (kwargs, clipboard_formats())

# S2: only a new chat in Claude Desktop's own window qualifies
for url, ok in (("https://claude.ai/new?mode=chat", True), ("https://claude.ai/new", True),
                ("https://claude.ai/newsletter", False), ("https://claude.ai/chat/abc", False),
                ("http://claude.ai/new", False), ("https://evil.example/new", False), (None, False)):
    assert uia.is_new_chat_url(url) is ok, url
real_url, real_main = uia.focused_textbox_url, uia.is_main_window
uia.focused_textbox_url = lambda: "https://claude.ai/new"
uia.is_main_window = lambda hwnd: False  # e.g. a browser tab on claude.ai/new
assert not uia.chat_box_focused()
uia.is_main_window = lambda hwnd: True
assert uia.chat_box_focused()
uia.focused_textbox_url, uia.is_main_window = real_url, real_main

# G1: if anything is copied while Claude opens, nothing is pasted
sent = []
deliver.os.startfile = lambda url: sent.append(("open", url))
deliver.type_text = lambda text: sent.append(("type", text))
deliver.press = lambda *keys: sent.append(("press", keys))
deliver.uia.chat_box_focused = lambda: True
result = []
d = deliver.Deliverer()
d.done.connect(lambda ok, msg: result.append((ok, msg)))
d.start(img, "")
QGuiApplication.clipboard().setText("a password copied meanwhile")
pump(0.6)
assert result and not result[0][0] and "clipboard changed" in result[0][1], result
assert not any(s[0] == "press" for s in sent), sent

# empty capture: nothing opened, nothing pasted
sent.clear()
result.clear()
d.start(QImage(), "")
assert result and not result[0][0] and sent == [], (result, sent)

# app-level checks with input mocked
a = qapp.App()
pressed, typed, toasts = [], [], []
qapp.press = lambda *keys: pressed.append(keys)
qapp.type_text = lambda text: typed.append(text)
a.toast.show_message = toasts.append

# G3: a late transcript never reopens the pop-up; a late failure never turns the mic on
a.watcher.focused = lambda: False
a.on_transcript("Hello there.")
assert pressed == [] and typed == [] and "copied" in toasts[-1], (pressed, typed, toasts)
assert QGuiApplication.clipboard().text() == "Hello there."
a.on_transcribe_failed("503")
assert pressed == [] and "couldn't transcribe" in toasts[-1], (pressed, toasts)
a.watcher.focused = lambda: True
a.paused = True
a.on_transcript("While paused.")
assert typed == [] and QGuiApplication.clipboard().text() == "While paused."
a.paused = False

# S6: the pop-up is reopened with Claude's shortcut from settings
a.open_popup()
assert pressed[-1] == (0x11, 0x12, 0x20), pressed  # Ctrl, Alt, Space
a.settings.values["popup_shortcut"] = "Win+Shift+Q"
a.open_popup()
assert pressed[-1] == (0x10, qapp.VK_LWIN, ord("Q")), pressed

# G10: a hotkey pending when Pause is pressed doesn't fire; disable clears it
a.hotkeys.pending = "screenshot"
a.hotkeys.release.start()
a.set_paused(True)
assert a.hotkeys.pending == "" and not a.hotkeys.release.isActive()
started = []
a.start_capture = lambda: started.append(1)
a.watcher.visible = lambda: True
a.on_hotkey("screenshot")
assert started == []
a.set_paused(False)

# G5: a message emitted from a worker thread reaches the toast
shown = []
a.toast.message.disconnect()
a.toast.message.connect(shown.append)
threading.Thread(target=lambda: a.toast.message.emit("from a thread")).start()
pump(0.3)
assert shown == ["from a thread"], shown

# G8: docking uses the pop-up monitor's scale, not the toolbar's current one
for dpi, scale in ((96, 1.0), (192, 2.0)):
    a.toolbar.dock(0, 100, 1000, dpi, 99999)
    shadow = round(14 * scale)
    assert a.toolbar.target == (1000 + round(8 * scale) - shadow, 100 - shadow), (dpi, a.toolbar.target)
a.toolbar.hide()

# G4: a destroyed pop-up hides the toolbar; a reused handle isn't trusted
events = []
a.watcher.hidden.connect(lambda: events.append("hidden"))
a.watcher.shown.connect(lambda: events.append("shown"))
a.watcher.popup = 0x1234
a.watcher._on_event(None, watcher.EVENT_OBJECT_DESTROY, 0x1234, 0, 0, 0, 0)
assert events == ["hidden"] and a.watcher.popup == 0, events
real_is_popup = watcher.is_popup
watcher.is_popup = lambda hwnd: False  # the old handle now belongs to another app
a.watcher.popup = int(a.toolbar.winId())  # any real top-level window
a.watcher._on_event(None, watcher.EVENT_OBJECT_SHOW, a.watcher.popup, 0, 0, 0, 0)
assert "shown" not in events, events
watcher.is_popup = real_is_popup
a.watcher.stop()

# G6: a microphone that fails to start, or to stop, never leaves "recording" stuck
import sounddevice as sd  # noqa: E402


class BrokenStream:
    def __init__(self, fail_on, **_):
        self.fail_on = fail_on

    def start(self):
        if self.fail_on == "start":
            raise OSError("device unavailable")

    def stop(self):
        if self.fail_on == "stop":
            raise OSError("device unplugged")

    def close(self):
        pass


real_stream = sd.RawInputStream
r = voice.Recorder()
sd.RawInputStream = lambda **kw: BrokenStream("start", **kw)
try:
    r.start()
    raise AssertionError("start should have raised")
except OSError:
    assert not r.recording
sd.RawInputStream = lambda **kw: BrokenStream("stop", **kw)
r.start()
assert r.recording
r.stop()  # logs the failure, doesn't raise
assert not r.recording
sd.RawInputStream = real_stream

# G2: an overlay closed from outside (Alt+F4) cancels the capture instead of hanging
s = snip.Snipper()
cancelled = []
s.cancelled.connect(lambda: cancelled.append(1))
s.start()
pump(0.2)
s.overlays[0].close()  # what Alt+F4 does
pump(0.3)
assert cancelled == [1] and s.overlays == [], (cancelled, s.overlays)

print("review fixes: all checks passed")
