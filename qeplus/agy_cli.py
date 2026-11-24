"""Gemini 4 Hydrogen in the answer card, through the Antigravity CLI (`agy -p`) on your own Google
sign-in. No API key: keys are stripped from its environment like Claude Code's.

agy is an agent with tools, so it runs locked down: --sandbox, and per-run permission rules
(never saved to agy's own settings) that deny commands, writing or editing files, the web, MCP
servers, the browser and sub-agents. Reading files stays allowed because that's how it sees a
screenshot (an @path in the message); it can read what you can, but can't change anything or
send it anywhere. It has no system-prompt flag, so the brief goes in the first message.

Everything else (warm process, kill-on-close job, generations, the resume fallback) is
ClaudeSession's: AgySession only changes how the process is started and how messages are
written and read.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
from collections import deque

from . import claude_cli
from .claude_cli import RESUME_LOST, ClaudeSession, discard_attachment, valid_session_id

LABEL = "Gemini 4 Hydrogen"
LEVELS = ("low", "medium", "high", "xhigh")  # reasoning levels: gemini-4-hydrogen-<level>
DENY = ["command(*)", "write_file(*)", "edit_file(*)", "read_url(*)", "url(*)", "http(*)",
        "web(*)", "mcp(*)", "browser(*)", "subagent(*)"]
BRIEF = ("You are a quick desktop assistant inside Quick Entry Plus. Answer concisely; the "
         "user is in the middle of other work. Don't use tools, except to look at an attached "
         "image file named in the message.")
log = logging.getLogger(__name__)


def find_agy() -> str | None:
    """agy.exe on PATH, else where its installer puts it."""
    on_path = shutil.which("agy.exe")
    if on_path:
        return on_path
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:  # never a relative path (the current folder)
        return None
    default = os.path.join(local, "agy", "bin", "agy.exe")
    return default if os.path.isfile(default) else None


def friendly_error(text: str, cli: str | None) -> str:
    if cli is None:
        return ("The Antigravity CLI (agy) isn't installed, so Gemini 4 Hydrogen can't answer. "
                "Install Antigravity, sign in once, or switch back to Claude.")
    if "log in" in text.lower() or "login" in text.lower() or "authenticat" in text.lower():
        return f'agy isn\'t signed in. Run once in a terminal: & "{cli}" and sign in with Google.'
    if text.strip():
        return text.strip().splitlines()[-1][:300]
    return "Gemini 4 Hydrogen stopped unexpectedly."


class AgySession(ClaudeSession):
    """Same signals and methods as ClaudeSession; level() gives the reasoning level to use."""

    NAME = "agy"

    def __init__(self, level=lambda: "medium"):
        super().__init__()
        self.level = level
        self.started_level = ""             # the level the running process was started with
        self.requested: str | None = None  # the conversation asked for with --conversation
        self.briefed = False                # the brief went out in this conversation
        self.files: list[str] = []          # attachments it may still be reading
        self.question: tuple[str, list[str]] = ("", [])  # the last one, for a retry

    def check_account(self) -> None:
        """agy has no non-interactive status command: a sign-in problem shows as an error."""

    def start(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        cli = find_agy()
        if cli is None:
            self.failed.emit(friendly_error("", None))
            return
        if self.session_id and not valid_session_id(self.session_id):
            self.session_id = None
        os.makedirs(claude_cli.WORKSPACE, exist_ok=True)
        level = self.level()
        args = [cli, "--model", f"gemini-4-hydrogen-{level}", "--sandbox",
                "--disable-slash-commands", "--settings",
                json.dumps({"permissions": {"deny": DENY}}),
                "-p=", "--input-format", "stream-json", "--output-format", "stream-json"]
        if self.session_id:
            args += ["--conversation", self.session_id]
        try:
            proc = subprocess.Popen(args, cwd=claude_cli.WORKSPACE, env=claude_cli.cli_env(),
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError as e:
            log.error("couldn't start agy: %s", e)
            self.failed.emit(friendly_error(str(e), cli))
            return
        try:
            claude_cli._contain(proc)
        except Exception as e:  # never run it outside the kill-on-close job
            log.error("couldn't contain agy: %s", e)
            claude_cli._kill(proc)
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                pipe.close()
            self.failed.emit("Couldn't start Gemini 4 Hydrogen safely. Try again.")
            return
        self.proc, self.cli, self.resumed = proc, cli, bool(self.session_id)
        self.requested, self.started_level = self.session_id, level
        self.briefed = self.briefed and self.resumed
        self.gen += 1
        stderr: deque[str] = deque(maxlen=64)
        drain = threading.Thread(target=self._drain, args=(proc, stderr), daemon=True)
        drain.start()
        threading.Thread(target=self._read, args=(proc, self.gen, drain, stderr),
                         daemon=True).start()
        log.info("agy (%s) warm process started (resume=%s)", self.level(), self.resumed)

    def ask(self, text: str, images: list[str] = ()) -> None:
        self.start()
        if not self.proc:  # couldn't start: nothing will read the screenshots
            for path in images:
                discard_attachment(path)
            return
        self.question = (text, list(images))  # as you asked it, for a retry
        self._send(self._blocks(text), list(images))

    def _blocks(self, text: str, history: bool = False) -> list[dict]:
        """The message: the brief (once per conversation), the conversation so far when this
        model needs it (a switch, a lost resume), then the question."""
        blocks = [{"type": "text", "text": text}]
        context = history or self.resume_fallback_pending or self.handover
        if self.resume_fallback_pending:
            self.notice.emit(RESUME_LOST)
        self.resume_fallback_pending = self.handover = False
        self.context_sent = bool(context and self.fallback)
        if self.context_sent:
            blocks.insert(0, self._context_block())
        if not self.briefed:
            blocks.insert(0, {"type": "text", "text": BRIEF})
            self.briefed = True
        return blocks

    def _send(self, blocks: list[dict], images: list[str]) -> None:
        # recorded now, not when the worker reports back: agy's start-up event can arrive
        # first, and a lost conversation must still be retried with this question
        self.busy, self.files, self.last = True, images, blocks
        threading.Thread(target=self._write, daemon=True,
                         args=(self.proc, self.gen, list(images), blocks)).start()

    def _write(self, proc: subprocess.Popen, gen: int, images: list[str],
               blocks: list[dict]) -> None:
        """One text message; images are named as @paths it opens itself. The files stay until
        the answer is done (it reads them while answering)."""
        try:
            text = "\n\n".join(b["text"] for b in blocks)
            refs = []
            for path in images:  # only real files in our attachments folder, never a link
                inside = os.path.normcase(os.path.realpath(os.path.dirname(path))) == \
                    os.path.normcase(os.path.realpath(claude_cli.ATTACHMENTS))
                rel = os.path.relpath(path, claude_cli.WORKSPACE).replace("\\", "/")
                if not inside or os.path.islink(path) or " " in rel:
                    raise ValueError(f"not an attachment: {path}")
                refs.append(f"@{rel}")
            if refs:
                plural = "s" if len(refs) > 1 else ""
                text += f"\n\nAttached image{plural}: " + " ".join(refs)
            content = [{"type": "text", "text": text}]
            self._event.emit(gen, "sent", content)
            line = json.dumps({"event": "user", "message": {"role": "user", "content": content}})
            proc.stdin.write((line + "\n").encode())
            proc.stdin.flush()
        except (OSError, ValueError) as e:
            log.error("couldn't send to agy: %s", e)
            self._event.emit(gen, "send_failed", "Couldn't send that to Gemini 4 Hydrogen. "
                                                 "Try again.")

    def _read(self, proc: subprocess.Popen, gen: int, drain: threading.Thread,
              stderr: deque) -> None:
        """Worker thread: agy's stream-json events (init, step_update, result) to _event."""
        for raw in proc.stdout:
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(ev, dict):
                continue
            kind = ev.get("event")
            if kind == "init" and valid_session_id(ev.get("conversation_id")):
                self._event.emit(gen, "session", ev["conversation_id"])
            elif kind == "step_update":
                step = ev.get("step_update")
                if (isinstance(step, dict) and step.get("step_type") == "agent_response"
                        and isinstance(step.get("text_delta"), str)):
                    self._event.emit(gen, "text", step["text_delta"])
            elif kind == "result":
                result = ev.get("result") if isinstance(ev.get("result"), dict) else {}
                if result.get("status") == "SUCCESS":
                    self._event.emit(gen, "done", None)
                else:
                    self._event.emit(gen, "error", str(result.get("error") or "")
                                     or "Gemini 4 Hydrogen couldn't answer that.")
        proc.stdout.close()
        drain.join(1)
        self._event.emit(gen, "exit", "\n".join(stderr))

    def _relay(self, gen: int, kind: str, payload) -> None:
        if gen != self.gen:
            return
        if kind in ("done", "error", "exit", "send_failed"):
            for path in self.files:  # it has finished reading them
                discard_attachment(path)
            self.files = []
        if kind == "session" and self.resumed and payload != self.requested:
            # agy doesn't fail on a conversation it no longer has: it starts a new one
            log.info("agy didn't have that conversation any more; continuing in a new one")
            self.resumed, self.briefed = False, False
            if self.last is not None:  # already asked: ask again, with the history
                self._resume_lost()
                return
            self.session_id, self.resume_fallback_pending = payload, True
            return
        if kind in ("error", "exit"):
            payload = str(payload)
            if kind == "error" or self.busy:
                log.warning("agy: %s", payload.strip()[:500])
            if kind == "error":
                self.busy, self.last = False, None
                self.failed.emit(friendly_error(payload, self.cli))
                return
            self.proc, self.last = None, None
            if self.busy:
                self.busy = False
                self.failed.emit(friendly_error(payload, self.cli))
            return
        super()._relay(gen, kind, payload)

    def _resume_lost(self) -> None:
        """Asked, then agy turned out not to have the conversation: ask again in a new one,
        once, with the brief, the history and your question (screenshots kept for it)."""
        text, images = self.question
        files, self.files = self.files, []  # stop() would delete them
        self.stop()
        self.session_id, self.briefed = None, False
        self.start()
        if not self.proc:
            for path in files:
                discard_attachment(path)
            return
        self.notice.emit(RESUME_LOST)
        self._send(self._blocks(text, history=True), files)

    def stop(self) -> None:
        for path in self.files:
            discard_attachment(path)
        self.files = []
        super().stop()
