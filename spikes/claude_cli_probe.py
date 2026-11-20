"""Step 1 proof: Claude Code headless (`claude -p`) streams from Python and `--resume` continues.

Runs the Claude Code binary bundled with Claude Desktop, in %APPDATA%\\QuickEntryPlus\\workspace,
with every CLAUDE*/ANTHROPIC* variable removed (as when the app starts from the tray), no tools
except Read, no settings sources and no MCP. Turn 1 asks a question and streams it; turn 2 resumes
the session and asks what turn 1 was. Also asks whether any user instructions leaked in.
Run from PowerShell (not Claude's container): .venv\\Scripts\\python spikes\\claude_cli_probe.py
"""

import glob
import json
import os
import subprocess
import time

CLI = max(glob.glob(os.path.join(os.environ["LOCALAPPDATA"], "Claude-Data", "*", "claude-code",
                                 "*", "*", "claude.exe")), key=os.path.getmtime)
WORKSPACE = os.path.join(os.environ["APPDATA"], "QuickEntryPlus", "workspace")
SYSTEM = "You are a quick desktop assistant. Answer concisely; the user is in the middle of other work."
os.makedirs(WORKSPACE, exist_ok=True)
env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CLAUDE", "ANTHROPIC"))}
stripped = sorted(k for k in os.environ if k.upper().startswith(("CLAUDE", "ANTHROPIC")))


def ask(prompt, resume=None):
    args = [CLI, "-p", prompt, "--output-format", "stream-json", "--verbose",
            "--include-partial-messages", "--tools", "Read", "--setting-sources", "",
            "--strict-mcp-config", "--system-prompt", SYSTEM]
    if resume:
        args += ["--resume", resume]
    t0 = time.monotonic()
    proc = subprocess.Popen(args, cwd=WORKSPACE, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    first, text, session, kinds = None, "", None, {}
    for line in proc.stdout:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        kind = ev.get("type")
        sub = ev.get("event", {}).get("type") if kind == "stream_event" else ev.get("subtype")
        kinds[f"{kind}/{sub}"] = kinds.get(f"{kind}/{sub}", 0) + 1
        session = ev.get("session_id") or session
        if kind == "stream_event" and sub == "content_block_delta":
            delta = ev["event"].get("delta", {})
            if delta.get("type") == "text_delta":
                first = first or time.monotonic() - t0
                text += delta["text"]
        if kind == "result":
            result = ev
    proc.wait()
    err = proc.stderr.read().decode(errors="replace").strip()
    total = time.monotonic() - t0
    print(f"  exit {proc.returncode}; first text after {first and round(first, 1)} s; "
          f"done in {total:.1f} s; session {session}")
    print(f"  event types: {kinds}")
    if err:
        print(f"  stderr: {err[:300]}")
    print(f"  streamed text: {text.strip()[:300]!r}")
    if "result" in locals():
        print(f"  result: is_error={result.get('is_error')} cost=${result.get('total_cost_usd')} "
              f"turns={result.get('num_turns')}")
    return session


print(f"CLI: {CLI}\nworkspace: {WORKSPACE}\nremoved from env: {stripped}")
print("turn 1:")
sid = ask("In one sentence: what is the capital of Australia? Then, on a new line, say whether "
          "you were given any CLAUDE.md, user instructions, skills or hook output (yes/no).")
print("turn 2 (--resume):")
ask("What exactly did I ask you in my previous message? Quote it briefly.", resume=sid)
