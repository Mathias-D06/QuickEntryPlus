"""Claude Code in headless mode (`claude -p`), on the user's own Pro/Max sign-in. No API key.

One warm process per conversation, fed messages as stream-json on stdin, so an answer starts
about 1 s after Enter instead of 3 s. Images travel inside the message and the process runs
with no tools at all (`--tools ""`), no settings sources and no MCP servers, in a scratch folder.
Every process is put in a Windows job object that kills it (and anything it starts) when this app
exits, so nothing is ever orphaned, even after a crash.
"""

from __future__ import annotations

import base64
import glob
import json
import logging
import os
import re
import shutil
import subprocess
import threading
from collections import deque

import pywintypes
import win32api
import win32con
import win32job
from PySide6.QtCore import QObject, Signal

from .settings import DIR

WORKSPACE = os.path.join(DIR, "workspace")
ATTACHMENTS = os.path.join(WORKSPACE, "attachments")
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
RESUME_LOST = "That conversation couldn't be resumed: continuing with its text as context."
SYSTEM_PROMPT = ("You are a quick desktop assistant. Answer concisely; the user is in the middle "
                 "of other work.")
log = logging.getLogger(__name__)


def valid_session_id(value) -> bool:
    """A session id goes on a command line after --resume: letters, digits, - and _ only."""
    return isinstance(value, str) and SESSION_ID.fullmatch(value) is not None


def find_cli() -> str | None:
    """`claude.exe` on PATH (a standalone install), else the newest copy bundled with Claude
    Desktop. Never a claude.cmd/.bat shim: cmd.exe would re-parse our arguments."""
    on_path = shutil.which("claude.exe")
    if on_path:
        return on_path
    bundled = glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Claude-Data", "*",
                                     "claude-code", "*", "*", "claude.exe"))
    return max(bundled, key=os.path.getmtime) if bundled else None


def _kill_on_close_job():
    job = win32job.CreateJobObject(None, "")
    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
    return job


_JOB = None  # one job for the app's lifetime: closing its handle (at exit) kills every member


def _contain(proc: subprocess.Popen) -> None:
    global _JOB
    if _JOB is None:
        _JOB = _kill_on_close_job()
    handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE,
                                  False, proc.pid)
    try:
        win32job.AssignProcessToJobObject(_JOB, handle)
    finally:
        win32api.CloseHandle(handle)


def friendly_error(text: str, cli: str | None) -> str:
    """One line telling the user what to do."""
    if cli is None:
        return ("Claude Code isn't installed. Install Claude Desktop (it includes it) "
                "or Claude Code.")
    if "not logged in" in text.lower() or "/login" in text:
        return f'Claude Code isn\'t signed in. Run once in a terminal: & "{cli}" auth login'
    if text.strip():
        return text.strip().splitlines()[0][:300]
    return "Claude Code stopped unexpectedly."


def auth_status(cli: str) -> dict:
    """`claude auth status` as a dict (loggedIn, authMethod, subscriptionType, ...)."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CLAUDE", "ANTHROPIC"))}
    out = subprocess.run([cli, "auth", "status"], env=env, capture_output=True, text=True,
                         timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    return json.loads(out.stdout)


def discard_attachment(path: str) -> None:
    """Delete an attachment once it has been sent; only real files in our attachments folder."""
    def folder(p):
        return os.path.normcase(os.path.realpath(p))

    if os.path.islink(path) or folder(os.path.dirname(os.path.abspath(path))) != folder(
            ATTACHMENTS):
        return
    try:
        os.remove(path)
    except OSError:
        pass


def _kill(proc: subprocess.Popen) -> None:
    """Kill and reap it, so no zombie handle is left."""
    try:
        proc.kill()
        proc.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _end(proc: subprocess.Popen) -> None:
    """Off the Qt thread: ask the process to finish, then kill it if it doesn't. Its stdout and
    stderr are closed by the threads reading them, once they reach the end."""
    try:
        proc.stdin.close()
        proc.wait(timeout=2)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        _kill(proc)
    log.info("Claude Code process stopped")


class ClaudeSession(QObject):
    """A conversation. start() warms a process; ask() sends a message; text arrives via signals.

    Worker threads report through _event with the generation they started in; stop() moves to a
    new generation, so anything still queued from an old process or message is dropped."""

    text = Signal(str)        # a streamed piece of the answer
    finished = Signal()       # the answer is complete
    failed = Signal(str)      # a one-line, user-facing error
    notice = Signal(str)      # a one-line note for a toast
    _event = Signal(int, str, object)  # (generation, kind, payload) from worker threads

    def __init__(self):
        super().__init__()
        self.proc: subprocess.Popen | None = None
        self.session_id: str | None = None  # kept across processes: follow-ups resume it
        self.busy = False
        self.cli: str | None = None
        self.gen = 0
        self.resumed = False     # the current process was started with --resume
        self.fallback = ""       # the conversation as text, sent if --resume finds nothing
        self.last: list | None = None  # the last message sent, for that one retry
        self.resume_fallback_pending = False  # resume failed while warming: context goes next
        self._event.connect(self._relay)

    def start(self) -> None:
        """Start a warm process now (idempotent), resuming the conversation if there is one."""
        if self.proc and self.proc.poll() is None:
            return
        cli = find_cli()
        if cli is None:
            self.failed.emit(friendly_error("", None))
            return
        if self.session_id and not valid_session_id(self.session_id):
            log.warning("ignoring a malformed session id")
            self.session_id = None
        os.makedirs(WORKSPACE, exist_ok=True)
        args = [cli, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
                "--verbose", "--include-partial-messages", "--tools", "",
                "--setting-sources", "", "--strict-mcp-config", "--system-prompt", SYSTEM_PROMPT]
        if self.session_id:
            args += ["--resume", self.session_id]
        # its own environment, not ours: never inherit a host Claude session's variables
        env = {k: v for k, v in os.environ.items()
               if not k.upper().startswith(("CLAUDE", "ANTHROPIC"))}
        try:
            proc = subprocess.Popen(args, cwd=WORKSPACE, env=env, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError as e:
            log.error("couldn't start Claude Code: %s", e)
            self.failed.emit(friendly_error(str(e), cli))
            return
        try:
            _contain(proc)
        except (OSError, pywintypes.error) as e:  # never run it outside the kill-on-close job
            log.error("couldn't contain Claude Code: %s", e)
            _kill(proc)
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                pipe.close()
            self.failed.emit("Couldn't start Claude Code safely. Try again.")
            return
        self.proc, self.cli, self.resumed = proc, cli, bool(self.session_id)
        self.gen += 1
        stderr: deque[str] = deque(maxlen=64)  # drained all the time, so the pipe never fills
        drain = threading.Thread(target=self._drain, args=(proc, stderr), daemon=True)
        drain.start()
        threading.Thread(target=self._read, args=(proc, self.gen, drain, stderr),
                         daemon=True).start()
        log.info("Claude Code warm process started (resume=%s)", self.resumed)

    def ask(self, text: str, images: list[str] = ()) -> None:
        """Send a message: text plus PNG files (sent inline, so Claude needs no file tools).
        Reading, encoding and writing happen on a worker thread; the PNGs are deleted after."""
        self.start()
        if not self.proc:
            return
        blocks = [{"type": "text", "text": text}]
        if self.resume_fallback_pending:  # the resumed session turned out to be gone
            self.resume_fallback_pending = False
            if self.fallback:
                blocks.insert(0, self._context_block())
            self.notice.emit(RESUME_LOST)
        self.busy = True
        threading.Thread(target=self._write, daemon=True,
                         args=(self.proc, self.gen, list(images), blocks)).start()

    def _context_block(self) -> dict:
        return {"type": "text",
                "text": "Our earlier conversation, for context:\n\n" + self.fallback}

    def _write(self, proc: subprocess.Popen, gen: int, images: list[str],
               blocks: list[dict]) -> None:
        try:
            content = []
            for path in images:
                with open(path, "rb") as f:
                    data = base64.b64encode(f.read()).decode()
                content.append({"type": "image", "source": {
                    "type": "base64", "media_type": "image/png", "data": data}})
            content += blocks
            self._event.emit(gen, "sent", content)
            line = json.dumps({"type": "user", "message": {"role": "user", "content": content}})
            proc.stdin.write((line + "\n").encode())
            proc.stdin.flush()
            for path in images:  # only once it's really sent: a failed send keeps them
                discard_attachment(path)
        except (OSError, ValueError) as e:  # an unreadable attachment, or the process died
            log.error("couldn't send to Claude Code: %s", e)
            self._event.emit(gen, "send_failed", "Couldn't send that to Claude Code. Try again.")

    @staticmethod
    def _drain(proc: subprocess.Popen, into: deque) -> None:
        try:
            for raw in proc.stderr:
                into.append(raw.decode(errors="replace").rstrip())
        except (OSError, ValueError):
            pass
        finally:
            proc.stderr.close()

    def _read(self, proc: subprocess.Popen, gen: int, drain: threading.Thread,
              stderr: deque) -> None:
        """Worker thread: turn stream-json events into _event emissions (queued onto Qt)."""
        sid = None
        for raw in proc.stdout:
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(ev, dict):
                continue
            if ev.get("session_id") != sid and valid_session_id(ev.get("session_id")):
                sid = ev["session_id"]
                self._event.emit(gen, "session", sid)
            kind = ev.get("type")
            if kind == "stream_event":
                event = ev.get("event")
                delta = event.get("delta") if isinstance(event, dict) else None
                if (isinstance(delta, dict) and delta.get("type") == "text_delta"
                        and isinstance(delta.get("text"), str)):
                    self._event.emit(gen, "text", delta["text"])
            elif kind == "result":
                if ev.get("is_error"):
                    errors = ev.get("errors") if isinstance(ev.get("errors"), list) else []
                    detail = str(ev.get("result") or "") or " ".join(map(str, errors))
                    self._event.emit(gen, "error", detail)
                else:
                    self._event.emit(gen, "done", None)
        proc.stdout.close()
        drain.join(1)
        self._event.emit(gen, "exit", "\n".join(stderr))

    def _relay(self, gen: int, kind: str, payload) -> None:
        """On the Qt thread: pass on events from the current generation only."""
        if gen != self.gen:
            return  # a stopped process or message, still draining
        if kind == "session":
            self.session_id = payload
        elif kind == "sent":
            self.last = payload
        elif kind == "text":
            self.text.emit(payload)
        elif kind == "done":
            self.busy, self.last = False, None
            self.finished.emit()
        elif (kind in ("error", "exit") and self.resumed
              and "no conversation found" in str(payload).lower()):
            if self.last is not None:
                self._resume_lost()
                return
            # gone before anything was asked (warming after Recent): no error on the history;
            # the next question starts afresh with the conversation as context
            log.info("the resumed session no longer exists; continuing in a new one")
            self.stop()
            self.session_id, self.resumed, self.resume_fallback_pending = None, False, True
        elif kind == "error":
            self.busy, self.last = False, None
            log.warning("Claude Code error: %s", payload[:500])
            self.failed.emit(friendly_error(payload, self.cli))
        elif kind == "send_failed":
            self.busy, self.last = False, None
            self.failed.emit(payload)
        elif kind == "exit":
            self.proc, self.last = None, None
            if self.busy:
                self.busy = False
                log.error("Claude Code exited mid-answer: %s", payload.strip()[:500])
                self.failed.emit(friendly_error(payload, self.cli))

    def _resume_lost(self) -> None:
        """The saved session is gone (expired or deleted): start afresh with it as context."""
        content, self.last = self.last, None
        self.stop()
        self.session_id = None
        if self.fallback:
            content = [self._context_block()] + content
        self.notice.emit(RESUME_LOST)
        self.start()
        if self.proc:
            self.busy = True
            threading.Thread(target=self._write, args=(self.proc, self.gen, [], content),
                             daemon=True).start()

    def stop(self) -> None:
        """End the process (Esc or quit) without blocking the UI. The session id is kept, so
        the next ask() resumes."""
        proc, self.proc, self.busy = self.proc, None, False
        self.gen += 1
        if proc and proc.poll() is None:
            threading.Thread(target=_end, args=(proc,), daemon=True).start()

    def reset(self) -> None:
        """New conversation: forget the session and warm a fresh process."""
        self.stop()
        self.session_id, self.fallback, self.last = None, "", None
        self.resume_fallback_pending = False
        self.start()
