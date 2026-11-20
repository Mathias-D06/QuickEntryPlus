"""Ask gemini-4-argon for a security review and a feature test plan of Quick Entry Plus.

Sends the source (qeplus/, tests/, packaging/, run.py, README.md) with line numbers. No logs,
settings or keys are sent. The answer is saved to spikes/out/argon-review.md.
Run: .venv\\Scripts\\python spikes\\consult_argon.py
"""

import glob
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

from qeplus.voice import api_key  # noqa: E402

MODEL = "gemini-4-argon"
FILES = sorted(glob.glob(os.path.join(ROOT, "qeplus", "*.py"))
               + glob.glob(os.path.join(ROOT, "tests", "*.py"))
               + glob.glob(os.path.join(ROOT, "packaging", "*.*"))
               + [os.path.join(ROOT, "run.py"), os.path.join(ROOT, "README.md"),
                  os.path.join(ROOT, "requirements.txt")])

PROMPT = """You are reviewing a small Windows 11 desktop app, Quick Entry Plus (Python 3.12,
PySide6, ctypes/pywin32, comtypes UI Automation, google-genai). It docks a no-focus toolbar
next to Claude Desktop's quick entry pop-up and adds screenshot, window-capture and voice
dictation. The README explains the design. Every file is below with line numbers.

Threat model: it runs as the normal user, starts with Windows, and is always running. Untrusted
inputs include: other apps' window titles and class names, any foreground window (captured with
PrintWindow), UI Automation text from Claude's windows, the clipboard, settings.json (user
editable), the GEMINI_API_KEY environment variable, Gemini responses (typed into the pop-up with
SendInput), and microphone audio (anyone near the mic). It sends keystrokes (SendInput) and opens
claude:// links. It must never paste or type into the wrong window, leak screenshots or audio,
or crash.

Give two sections, in Markdown.

## 1. Security and safety findings
Only real, specific issues in THIS code. For each: an id (S1, S2...), severity
(high/medium/low), file:line, what is wrong, a concrete scenario showing it happen (inputs or
state -> bad outcome), and the smallest fix. Include race conditions that could send keys to the
wrong window, data exposure (screenshots, audio, transcripts, logs), unsafe handling of
settings or responses, packaging or startup risks, and denial of service. If something looks
risky but is actually handled, say so in one line under "Checked and fine". Do not pad with
generic advice.

## 2. Feature test plan
A table of the tests that matter most for each feature (pop-up detection and docking, toolbar
focus behaviour, screenshot, window capture, delivery guard, voice with both engines, hotkeys,
settings reload, tray, pause, start with Windows, single instance, packaging). Columns: feature,
test, expected result, automated or manual, priority (P1-P3). Mark tests the existing tests/
already cover as "covered" and focus on the gaps.
"""


def numbered(path: str) -> str:
    rel = os.path.relpath(path, ROOT).replace("\\", "/")
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    return f"=== {rel} ===\n" + "\n".join(f"{i:4} {line}" for i, line in enumerate(lines, 1))


def main():
    source = "\n\n".join(numbered(p) for p in FILES if os.path.exists(p))
    print(f"sending {len(FILES)} files, {len(source):,} characters to {MODEL}")
    client = genai.Client(api_key=api_key(), http_options=types.HttpOptions(timeout=600_000))
    for attempt in range(1, 5):
        t = time.monotonic()
        try:
            response = client.models.generate_content(
                model=MODEL, contents=[PROMPT, source],
                config=types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_level="high")))
            break
        except Exception as e:
            print(f"attempt {attempt}: {getattr(e, 'code', type(e).__name__)} after "
                  f"{time.monotonic() - t:.0f}s {str(e)[:120]}")
            if attempt == 4 or getattr(e, "code", 0) not in (429, 500, 503, 504):
                raise
            time.sleep(10 * attempt)
    out = os.path.join(ROOT, "spikes", "out", "argon-review.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(response.text or "")
    usage = response.usage_metadata
    print(f"saved {out} in {time.monotonic() - t:.0f}s; tokens in {usage.prompt_token_count}, "
          f"out {usage.candidates_token_count}")


if __name__ == "__main__":
    main()
