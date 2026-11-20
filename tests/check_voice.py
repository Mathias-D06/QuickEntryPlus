"""Self-check for the voice button. Records 1 s from the real microphone; Gemini and all key
presses are mocked, so nothing is sent anywhere and nothing is typed.
Run: .venv\\Scripts\\python tests\\check_voice.py
"""

import os
import sys
import time
import wave
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication  # noqa: E402

from qeplus import app as qapp  # noqa: E402
from qeplus import voice  # noqa: E402

qt = QApplication([])


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qt.processEvents()
        time.sleep(0.01)


# 1. the real microphone: about 1 s of 16 kHz mono 16-bit WAV
r = voice.Recorder()
r.start()
pump(1.0)
wav = r.stop()
with wave.open(BytesIO(wav)) as w:
    assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
    assert 0.8 < w.getnframes() / 16000 < 1.3, w.getnframes()
assert not r.recording

# 2. no key: transcribe() refuses before any network call
real_api_key = voice.api_key
voice.api_key = lambda: ""  # independent of any key set on this machine
try:
    voice.transcribe(wav)
    raise AssertionError("expected a refusal without a key")
except RuntimeError as e:
    assert "GEMINI_API_KEY" in str(e)

# app-level flow with input mocked
a = qapp.App()
pressed, typed, toasts = [], [], []
qapp.press = lambda *keys: pressed.append(keys)
qapp.type_text = lambda text: typed.append(text)
a.toast.show_message = toasts.append
a.watcher.focused = lambda: True
draft = {"text": ""}
qapp.uia.focused_text = lambda: draft["text"]

# 3a. default engine: Windows voice typing straight away, no recording, no toast
assert a.settings["voice_engine"] == "windows"
a.on_button("voice")
assert pressed == [(qapp.VK_LWIN, ord("H"))] and not toasts and not a.recorder.recording, pressed
pressed.clear()
a.settings.values["voice_engine"] = "gemini"  # the rest tests the Gemini engine

# 3b. no key -> Windows voice typing (Win+H) in the focused pop-up, with a toast
a.on_button("voice")
assert pressed == [(qapp.VK_LWIN, ord("H"))] and "GEMINI_API_KEY" in toasts[-1], (pressed, toasts)
assert a.toolbar.buttons["voice"].state == "idle"

# 4. with a key: click starts recording, click again transcribes (mocked) and types the text
voice.api_key = lambda: "test-key"
voice_calls = []
a.transcriber.start = lambda wav, *args: (voice_calls.append(len(wav)),
                                          a.transcriber.finished.emit("Hello there.\nNew line."))
a.on_button("voice")
assert a.toolbar.buttons["voice"].state == "active" and a.recorder.recording
a.on_button("screenshot")  # ignored while recording
assert not a.busy
pump(0.6)
draft["text"] = "Existing draft"
a.on_button("voice")
assert voice_calls and typed == [" Hello there. New line."], typed  # leading space, one line
assert a.toolbar.buttons["voice"].state == "idle"

# 5. Gemini fails -> Windows voice typing, with a toast asking to say it again
pressed.clear()
a.on_transcribe_failed("boom")
assert pressed == [(qapp.VK_LWIN, ord("H"))] and "say it again" in toasts[-1], (pressed, toasts)

# 6. empty transcript -> nothing typed, a toast
typed.clear()
a.on_transcript("  ")
assert typed == [] and "Didn't catch" in toasts[-1]

a.watcher.stop()

# 7. key lookup: process environment first
voice.api_key = real_api_key
os.environ["GEMINI_API_KEY"] = " env-key "
assert voice.api_key() == "env-key"
os.environ.pop("GEMINI_API_KEY")

# 8. model fallback: overloaded primary -> fallback model; a bad key is not retried
from google import genai  # noqa: E402
from google.genai import errors  # noqa: E402

calls = []


class FakeModels:
    def __init__(self, fail):
        self.fail = fail

    def generate_content(self, model, contents, config):
        calls.append(model)
        if model in self.fail:
            raise self.fail[model]
        return type("R", (), {"text": " Clean text. "})()


def fake_client(fail):
    return lambda **kw: type("C", (), {"models": FakeModels(fail)})()


voice.api_key = lambda: "test-key"
real_client = genai.Client
busy = errors.ServerError(503, {"error": {"message": "busy"}})
genai.Client = fake_client({voice.MODEL: busy})
assert voice.transcribe(wav) == "Clean text.", calls
assert calls == [voice.MODEL, voice.FALLBACK_MODEL], calls
calls.clear()
bad_key = errors.ClientError(400, {"error": {"message": "bad key"}})
genai.Client = fake_client({voice.MODEL: bad_key})
try:
    voice.transcribe(wav)
    raise AssertionError("a bad key must not fall back")
except errors.ClientError:
    assert calls == [voice.MODEL], calls
genai.Client = real_client
voice.api_key = real_api_key
print("voice: all checks passed")
