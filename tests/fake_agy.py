"""A stand-in for `agy -p` stream-json, used by check_models.py: no network, no sign-in.
Logs its arguments, its environment variable names and every message it receives (one JSON
object per line) to FAKE_AGY_LOG. Answers "hello world" (plus "and an image" if the message
names an @attachments/ file).
FAKE_AGY_MODE: "" answers normally; "lost" acts like agy given a --conversation it no longer
has (a warning, then a new conversation id); "error" fails every turn.
"""

import json
import os
import sys
import uuid

args = sys.argv[1:]
mode = os.environ.get("FAKE_AGY_MODE", "")


def log(entry):
    with open(os.environ["FAKE_AGY_LOG"], "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def out(event):
    print(json.dumps(event), flush=True)


log({"args": args, "env": sorted(os.environ)})
asked = args[args.index("--conversation") + 1] if "--conversation" in args else None
cid = str(uuid.uuid4()) if mode == "lost" or not asked else asked
if mode == "lost" and asked:
    sys.stderr.write(f'warning: conversation "{asked}" not found\n')
out({"event": "init", "conversation_id": cid, "init": {"model": args[args.index("--model") + 1]}})
for line in sys.stdin:
    msg = json.loads(line)
    log({"msg": msg})
    text = msg["message"]["content"][0]["text"]
    if mode == "error":
        out({"event": "result", "result": {"conversation_id": cid, "status": "ERROR",
                                            "error": "something broke"}})
        continue
    out({"event": "step_update", "step_update": {"conversation_id": cid, "step_type": "user_input",
                                                 "state": "DONE"}})
    pieces = ["hello ", "world"] + ([" and an image"] if "@attachments/" in text else [])
    for piece in pieces:
        out({"event": "step_update", "step_update": {
            "conversation_id": cid, "step_type": "agent_response", "state": "ACTIVE",
            "text_delta": piece}})
    out({"event": "step_update", "step_update": {"conversation_id": cid, "step_type": "tool",
                                                 "tool_name": "read_file", "text_delta": "x"}})
    out({"event": "result", "result": {"conversation_id": cid, "status": "SUCCESS",
                                       "response": "".join(pieces)}})
