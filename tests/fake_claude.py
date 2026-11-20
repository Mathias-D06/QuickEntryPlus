"""A stand-in for `claude -p` stream-json, used by check_review3.py: no network, no sign-in.
Logs its arguments, its environment variable names and every message it receives (one JSON
object per line) to FAKE_CLAUDE_LOG.
FAKE_CLAUDE_MODE: "" answers normally; "noisy" writes 1 MB to stderr first (blocks unless the
caller drains stderr); "crash" exits mid-answer with a sign-in error on stderr; "stubborn"
ignores stdin closing for 5 s. --resume dead-id behaves like a session that no longer exists
once asked; with --resume, "dead_startup" reports that as JSON before reading anything and
"dead_stderr" only on stderr, both exiting at once (a fresh, unresumed process answers).
"""

import json
import os
import sys
import time

args = sys.argv[1:]
mode = os.environ.get("FAKE_CLAUDE_MODE", "")
sid = args[args.index("--resume") + 1] if "--resume" in args else "fake-session-1"


def log(entry):
    with open(os.environ["FAKE_CLAUDE_LOG"], "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def out(event):
    print(json.dumps(event), flush=True)


log({"args": args, "env": sorted(os.environ)})
gone = f"No conversation found with session ID: {sid}"
if "--resume" in args and mode == "dead_startup":
    out({"type": "result", "is_error": True, "session_id": sid, "errors": [gone]})
    sys.exit(1)
if "--resume" in args and mode == "dead_stderr":
    sys.stderr.write(gone + "\n")
    sys.exit(1)
if mode == "noisy":
    sys.stderr.write("x" * 1_000_000 + "\n")
    sys.stderr.flush()
for line in sys.stdin:
    log({"msg": json.loads(line)})
    if sid == "dead-id":
        out({"type": "result", "is_error": True, "session_id": sid, "errors": [gone]})
        sys.exit(1)
    if mode == "crash":
        sys.stderr.write("Error: not logged in. Please run /login\n")
        sys.exit(1)
    out({"type": "system", "session_id": sid})
    out(["not", "a", "dict"])
    print("half a line {", flush=True)
    out({"type": "stream_event", "session_id": sid, "event": "not-a-dict"})
    out({"type": "stream_event", "session_id": "bad id; rm", "event": {"delta": 5}})
    for piece in ("hello ", "world"):
        out({"type": "stream_event", "session_id": sid,
             "event": {"delta": {"type": "text_delta", "text": piece}}})
    out({"type": "result", "session_id": sid, "is_error": False})
if mode == "stubborn":
    time.sleep(5)
