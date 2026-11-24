"""Dictation: record the microphone, then transcribe it on this PC with Whisper (faster-whisper).

Free and offline: no account, no key, no network once the model is downloaded (from Hugging
Face, the first time it's needed). The mic is only open while recording, so nothing runs while
idle. Audio is 16 kHz mono 16-bit, which is what Whisper takes.
"""

from __future__ import annotations

import io
import logging
import re
import threading
import wave

from PySide6.QtCore import QObject, QTimer, Signal

RATE, MAX_SECONDS = 16000, 300
# base: ~145 MB, ~1 s for a short clip on a laptop CPU; small is more accurate but ~4x slower
MODEL = "base"
MODELS = ("tiny", "tiny.en", "base", "base.en", "small", "small.en")
# a filler sound with the commas around it: "think, uh, we" -> "think we"
FILLERS = re.compile(r"(?:,\s*)?\b(?:u+m+|u+h+|e+r+m*|h+m+|m{2,})\b[,.]?", re.I)
log = logging.getLogger(__name__)
_models: dict[str, object] = {}
_load_lock = threading.Lock()


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


def model_ready(name: str = MODEL) -> bool:
    """Is this model loaded, or already downloaded (so loading it needs no network)?"""
    if name in _models:
        return True
    from faster_whisper.utils import download_model

    try:
        download_model(name, local_files_only=True)
        return True
    except Exception:
        return False


def load_model(name: str = MODEL):
    """The Whisper model on the CPU (int8), loaded once and kept. The first time, it's
    downloaded from Hugging Face; after that it loads from disk with no network."""
    with _load_lock:
        if name not in _models:
            from faster_whisper import WhisperModel

            try:
                _models[name] = WhisperModel(name, device="cpu", compute_type="int8",
                                             local_files_only=True)
            except Exception:  # not downloaded yet
                log.info("downloading the Whisper %s model (once)", name)
                _models[name] = WhisperModel(name, device="cpu", compute_type="int8")
        return _models[name]


def transcribe(wav: bytes, model: str = MODEL, cleanup: bool = True) -> str:
    """Blocking: WAV bytes (16 kHz mono 16-bit) to text. cleanup drops filler sounds (um, uh,
    er, hmm); Whisper already leaves out most false starts. Silence gives ''."""
    import numpy as np

    with wave.open(io.BytesIO(wav)) as w:
        pcm = w.readframes(w.getnframes())
    audio = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768
    # vad_filter skips silence, where Whisper would otherwise invent words ("Thank you.")
    segments, info = load_model(model).transcribe(audio, beam_size=1, vad_filter=True,
                                                  condition_on_previous_text=False)
    text = " ".join(s.text.strip() for s in segments).strip()
    if cleanup:
        text = " ".join(FILLERS.sub("", text).split())
        text = text[:1].upper() + text[1:]
    log.info("transcribed %.1f s of audio with Whisper %s (%s)", len(audio) / RATE, model,
             info.language)
    return text


class Transcriber(QObject):
    """Runs transcribe() on a worker thread; reports back on the Qt thread via signals."""

    finished = Signal(str)
    failed = Signal(str)
    notice = Signal(str)  # e.g. the one-time model download

    def start(self, wav: bytes, model: str = MODEL, cleanup: bool = True) -> None:
        def work():
            try:
                if not model_ready(model):
                    self.notice.emit("Downloading the speech model (only this once)…")
                self.finished.emit(transcribe(wav, model, cleanup))
            except Exception as e:  # any failure falls back to Windows voice typing
                log.warning("transcription failed: %s", e)
                self.failed.emit(str(e))

        threading.Thread(target=work, daemon=True).start()

    def preload(self, model: str = MODEL) -> None:
        """Load (downloading if needed) in the background, so the first dictation is quick."""
        def work():
            try:
                load_model(model)
            except Exception as e:
                log.warning("couldn't load the Whisper %s model: %s", model, e)

        threading.Thread(target=work, daemon=True).start()
