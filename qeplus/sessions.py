"""Recent answer-card conversations in %APPDATA%\\QuickEntryPlus\\sessions.json (the last 10).

Each entry keeps the CLI's session id (so it can be resumed), the turns shown in the card, which
backend answered, and when. Writes are atomic (a unique temp file, flushed to disk, then renamed),
entries that don't match the expected shape are dropped on load, and a file that can't be read
is set aside as sessions.json.bad instead of being overwritten.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import time

from .claude_cli import valid_session_id
from .settings import DIR

PATH = os.path.join(DIR, "sessions.json")
KEEP = 10
MAX_TEXT = 200_000  # characters kept per question, answer or error
MAX_TURNS = 200
log = logging.getLogger(__name__)


def _time(v) -> bool:
    """A real Unix time: not a bool, NaN, infinity, negative or absurdly far ahead."""
    return (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
            and 0 <= v < 4e9)


def _valid(s) -> bool:
    return (isinstance(s, dict) and valid_session_id(s.get("id"))
            and isinstance(s.get("title"), str)
            and _time(s.get("created")) and _time(s.get("updated"))
            and isinstance(s.get("turns"), list) and 0 < len(s["turns"]) <= MAX_TURNS
            and all(isinstance(t, list) and len(t) == 3
                    and all(isinstance(x, str) and len(x) <= MAX_TEXT for x in t)
                    for t in s["turns"]))


def load(path: str | None = None) -> list[dict]:
    path = path or PATH
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("not a list")
        return [s for s in data if _valid(s)]
    except (OSError, ValueError) as e:
        log.warning("sessions.json unreadable (%s); keeping it as sessions.json.bad", e)
        try:
            os.replace(path, path + ".bad")
        except OSError:
            pass
        return []


def save(session_id: str, turns: list[list[str]], backend: str = "claude",
         path: str | None = None) -> None:
    """Add or update a conversation and keep the 10 most recently used."""
    path = path or PATH
    if not valid_session_id(session_id) or not turns:
        return
    existing = load(path)
    sessions = [s for s in existing if s["id"] != session_id]
    old = next((s for s in existing if s["id"] == session_id), {})
    turns = [[str(x)[:MAX_TEXT] for x in t] for t in turns[-MAX_TURNS:]]
    title = next((q for q, _, _ in turns if q), "Untitled")
    now = time.time()
    sessions.insert(0, {"id": session_id, "title": title[:80], "backend": backend,
                        "created": old.get("created", now), "updated": now, "turns": turns})
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="sessions-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(sessions[:KEEP], f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def when(ts: float, now: float | None = None) -> str:
    """'just now', '5 min ago', '3 h ago', 'yesterday', or a date."""
    age = (now or time.time()) - ts
    if age < 60:
        return "just now"
    if age < 3600:
        return f"{int(age // 60)} min ago"
    if age < 86400:
        return f"{int(age // 3600)} h ago"
    if age < 2 * 86400:
        return "yesterday"
    return time.strftime("%d %b", time.localtime(ts))
