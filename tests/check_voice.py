"""Self-check for the voice button. Records 1 s from the real microphone, and transcribes a clip
spoken by Windows' own speech voice with the real local Whisper model (offline: the model must
already be downloaded, which the app does on first use). Key presses are mocked, so nothing is
typed, and nothing is sent anywhere.
Run: .venv\\Scripts\\python tests\\check_voice.py
"""

import os
import sys
import tempfile
import time
import wave
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication  # noqa: E402

from qeplus import app as qapp  # noqa: E402
from qeplus import settings, voice  # noqa: E402

qt = QApplication([])


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qt.processEvents()
        time.sleep(0.01)


def spoken(text):
    """WAV bytes (16 kHz mono 16-bit) of Windows' speech voice saying text."""
    import win32com.client

    path = os.path.join(tempfile.mkdtemp(prefix="qeplus-voice-"), "speech.wav")
    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
    fmt.Type = 18  # SAFT16kHz16BitMono
    stream.Format = fmt
    stream.Open(path, 3)  # SSFMCreateForWrite
    speaker = win32com.client.Dispatch("SAPI.SpVoice")
    speaker.AudioOutputStream = stream
    speaker.Speak(text)
    stream.Close()
    with open(path, "rb") as f:
        return f.read()


# 1. the real microphone: about 1 s of 16 kHz mono 16-bit WAV
r = voice.Recorder()
r.start()
pump(1.0)
wav = r.stop()
with wave.open(BytesIO(wav)) as w:
    assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
    assert 0.8 < w.getnframes() / 16000 < 1.3, w.getnframes()
assert not r.recording

# 2. local Whisper transcribes real speech, with no key and no network
assert voice.model_ready(voice.MODEL), "download it once: run the app and dictate, or load_model()"
started = time.perf_counter()
text = voice.transcribe(spoken("What is the capital of Australia?"))
print(f"   Whisper {voice.MODEL}: {text!r} in {time.perf_counter() - started:.1f} s")
assert "capital of australia" in text.lower(), text
silence = BytesIO()
with wave.open(silence, "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(16000)
    w.writeframes(bytes(2 * 16000))
assert voice.transcribe(silence.getvalue()) == "", "silence should give no words"

# 3. cleanup drops filler sounds and their commas, and leaves real words alone
cleaned = " ".join(voice.FILLERS.sub("", "Um, so I think, uh, we should, er, go. Hmm. "
                                         "Summer umbrellas, erm, help.").split())
assert cleaned == "so I think we should go. Summer umbrellas help.", cleaned

# 4. the setting: Whisper is the default, and the old "gemini" engine now means Whisper
assert settings.DEFAULTS["voice_engine"] == "whisper"
assert settings.validate({"voice_engine": "gemini"})[0]["voice_engine"] == "whisper"
values, problems = settings.validate({"whisper_model": "huge"})
assert values["whisper_model"] == "base" and problems

# 5. the first transcription with no downloaded model says it's downloading, once
notes = []
t = voice.Transcriber()
t.notice.connect(notes.append)
real_ready, real_transcribe = voice.model_ready, voice.transcribe
voice.model_ready = lambda name: False
voice.transcribe = lambda wav, model, cleanup: "ok"
t.start(b"")
pump(0.5)
assert notes and "Downloading" in notes[0], notes
voice.model_ready, voice.transcribe = real_ready, real_transcribe

# app-level flow with input mocked
a = qapp.App()
pressed, typed, toasts = [], [], []
qapp.press = lambda *keys: pressed.append(keys)
qapp.type_text = lambda text: typed.append(text)
a.toast.show_message = toasts.append
a.watcher.focused = lambda: True
draft = {"text": ""}
qapp.uia.focused_text = lambda: draft["text"]

# 6. the Windows engine: Windows voice typing straight away, no recording
a.settings.values["voice_engine"] = "windows"
a.on_button("voice")
assert pressed == [(qapp.VK_LWIN, ord("H"))] and not a.recorder.recording, pressed
pressed.clear()
a.settings.values["voice_engine"] = "whisper"

# 7. Whisper: click starts recording, click again transcribes (mocked here) and types the text
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
assert a.toolbar.buttons["voice"].state == "idle" and not pressed

# 8. Whisper fails -> Windows voice typing (the last fallback), asking to say it again
a.on_transcribe_failed("boom")
assert pressed == [(qapp.VK_LWIN, ord("H"))] and "say it again" in toasts[-1], (pressed, toasts)

# 9. empty transcript -> nothing typed, a toast
typed.clear()
a.on_transcript("  ")
assert typed == [] and "Didn't catch" in toasts[-1]

a.watcher.stop()
print("voice: all checks passed")
