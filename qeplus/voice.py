"""Dictation: record the microphone, then have Gemini turn it into a clean transcript.

The mic is only open while recording, so nothing runs while idle. Audio is 16 kHz mono 16-bit
WAV sent inline (32 KB/s; the 5-minute cap keeps a request under 10 MB of Gemini's 20 MB).
"""

from __future__ import annotations

import io
import logging
import os
import threading
import wave

from PySide6.QtCore import QObject, QTimer, Signal

RATE, MAX_SECONDS = 16000, 300
MODEL = "gemini-3.8-flash"  # current stable Flash model with audio input (ai.google.dev, Nov 2026)
# used when MODEL is overloaded. On 2026-11-19 every full Flash model (3.5-3.8) returned 503/504
# while 3.1 Flash-Lite answered in ~1.5 s and still removed filler words (3.5 Flash-Lite didn't)
FALLBACK_MODEL = "gemini-3.1-flash-lite"
RETRYABLE = {429, 500, 503, 504}  # rate-limited / overloaded / deadline: worth another model
CLEAN_PROMPT = (
    "Transcribe the speech in this audio. Return only the transcript, with correct punctuation "
    "and capitalisation. Remove filler words (um, uh, er, and 'like' or 'you know' used as "
    "filler), false starts and accidental repetitions. Do not answer, summarise, translate or "
    "add anything. If there is no speech, return an empty response.")
VERBATIM_PROMPT = (
    "Transcribe the speech in this audio word for word, with punctuation and capitalisation. "
    "Return only the transcript. If there is no speech, return an empty response.")
log = logging.getLogger(__name__)


def api_key() -> str:
    """GEMINI_API_KEY from this process's environment, else from the saved user/system
    environment, so a key set after this app started works without a restart."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    import winreg

    for root, path in ((winreg.HKEY_CURRENT_USER, "Environment"),
                       (winreg.HKEY_LOCAL_MACHINE,
                        r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
        try:
            with winreg.OpenKey(root, path) as k:
                value = str(winreg.QueryValueEx(k, "GEMINI_API_KEY")[0]).strip()
            if value:
                return value
        except OSError:
            continue
    return ""


class Recorder(QObject):
    """start() opens the default microphone; stop() closes it and returns WAV bytes."""

    limit_reached = Signal()

    def __init__(self):
        super().__init__()
        self.stream = None
        self.chunks: list[bytes] = []
        self.cap = QTimer(self, singleShot=True, interval=MAX_SECONDS * 1000)
        self.cap.timeout.connect(self.limit_reached)

    @property
    def recording(self) -> bool:
        return self.stream is not None

    def start(self) -> None:
        import sounddevice as sd  # imported here: loading PortAudio costs start-up time

        self.chunks = []
        stream = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16",
                                   callback=lambda data, *_: self.chunks.append(bytes(data)))
        try:
            stream.start()
        except Exception:
            stream.close()
            raise
        self.stream = stream  # only once it is really recording
        self.cap.start()

    def stop(self) -> bytes:
        self.cap.stop()
        stream, self.stream = self.stream, None  # cleared first: an unplugged mic can raise
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as e:
                log.warning("microphone stop failed: %s", e)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(b"".join(self.chunks))
        self.chunks = []
        return buf.getvalue()


def transcribe(wav: bytes, model: str = MODEL, cleanup: bool = True,
               fallback: str = FALLBACK_MODEL) -> str:
    """Blocking Gemini call. Tries `model`, then `fallback` if the first is overloaded or
    times out. Raises on any other failure (no key, bad key, network) or if both fail."""
    import httpx2  # the HTTP client google-genai 3.x uses
    from google import genai
    from google.genai import errors, types

    key = api_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    seconds = max(0, len(wav) - 44) / (RATE * 2)
    # also sent as the server deadline, which must be >= 10 s (the API rejects less); a short
    # clip takes ~2 s, so this only bites when a model is overloaded and hangs
    timeout_ms = int((10 + seconds / 4) * 1000)
    client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=timeout_ms))
    models = [model] + ([fallback] if fallback and fallback != model else [])
    for i, name in enumerate(models):
        try:
            response = client.models.generate_content(
                model=name,
                contents=[CLEAN_PROMPT if cleanup else VERBATIM_PROMPT,
                          types.Part.from_bytes(data=wav, mime_type="audio/wav")],
                # low: the lowest-latency level gemini-3.8-flash accepts ("minimal" errors)
                config=types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_level="low")))
            log.info("transcribed %.1f s of audio with %s", seconds, name)
            return (response.text or "").strip()
        except (errors.APIError, httpx2.TimeoutException) as e:
            busy = isinstance(e, httpx2.TimeoutException) or e.code in RETRYABLE
            if not busy or i == len(models) - 1:
                raise
            log.warning("%s unavailable (%s); trying %s", name,
                        getattr(e, "code", "timeout"), models[i + 1])
    raise AssertionError("unreachable")


class Transcriber(QObject):
    """Runs transcribe() on a worker thread; reports back on the Qt thread via signals."""

    finished = Signal(str)
    failed = Signal(str)

    def start(self, wav: bytes, model: str = MODEL, cleanup: bool = True,
              fallback: str = FALLBACK_MODEL) -> None:
        def work():
            try:
                self.finished.emit(transcribe(wav, model, cleanup, fallback))
            except Exception as e:  # any failure falls back to Windows voice typing
                log.warning("transcription failed: %s", e)
                self.failed.emit(str(e))

        threading.Thread(target=work, daemon=True).start()
