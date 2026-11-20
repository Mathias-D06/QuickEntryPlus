"""Step 2 proof: one warm `claude -p --input-format stream-json` process answers several messages.

Starts the process, waits (as if the bar had just opened), sends message 1 and times the first
text from the moment it is sent; sends message 2 on the same process (memory check); then message 3
with an inline PNG image block and *no tools at all* (--tools ""). Finally closes stdin and checks
the process exits by itself.
Run from PowerShell: .venv\\Scripts\\python spikes\\claude_warm_probe.py
"""

import base64
import glob
import json
import os
import queue
import subprocess
import threading
import time

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter

CLI = max(glob.glob(os.path.join(os.environ["LOCALAPPDATA"], "Claude-Data", "*", "claude-code",
                                 "*", "*", "claude.exe")), key=os.path.getmtime)
WORKSPACE = os.path.join(os.environ["APPDATA"], "QuickEntryPlus", "workspace")
SYSTEM = "You are a quick desktop assistant. Answer concisely; the user is in the middle of other work."
env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CLAUDE", "ANTHROPIC"))}

t_start = time.monotonic()
proc = subprocess.Popen(
    [CLI, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
     "--include-partial-messages", "--tools", "", "--setting-sources", "", "--strict-mcp-config",
     "--system-prompt", SYSTEM],
    cwd=WORKSPACE, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
events: queue.Queue = queue.Queue()
threading.Thread(target=lambda: [events.put(json.loads(l)) for l in proc.stdout if l.strip()],
                 daemon=True).start()


def send(content):
    msg = {"type": "user", "message": {"role": "user", "content": content}}
    proc.stdin.write((json.dumps(msg) + "\n").encode())
    proc.stdin.flush()
    t0, first, text = time.monotonic(), None, ""
    while True:
        ev = events.get(timeout=120)
        if ev.get("type") == "stream_event":
            d = ev["event"].get("delta", {})
            if d.get("type") == "text_delta":
                first = first or time.monotonic() - t0
                text += d["text"]
        elif ev.get("type") == "result":
            print(f"  first text {first and round(first, 1)} s after sending; done in "
                  f"{time.monotonic() - t0:.1f} s; error={ev.get('is_error')}; "
                  f"session {ev.get('session_id')}")
            print(f"  text: {text.strip()[:200]!r}")
            return ev


time.sleep(4)  # the bar has been open a moment before you press Enter
print(f"process alive after warm-up: {proc.poll() is None}")
print("message 1:")
send([{"type": "text", "text": "Name the largest planet in one word."}])
print("message 2 (same process):")
send([{"type": "text", "text": "What did I just ask you? Answer in under ten words."}])

app = QGuiApplication([])
img = QImage(360, 120, QImage.Format.Format_RGB32)
img.fill(QColor("#1e6fd9"))
p = QPainter(img)
f = QFont("Segoe UI")
f.setPixelSize(44)
p.setFont(f)
p.setPen(QColor("white"))
p.drawText(img.rect(), Qt.AlignmentFlag.AlignCenter, "BLUE 4271")
p.end()
buf = QBuffer()
buf.open(QIODevice.OpenModeFlag.WriteOnly)
img.save(buf, "PNG")
png = base64.b64encode(bytes(buf.data())).decode()
print("message 3 (inline image, no tools):")
send([{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": png}},
      {"type": "text", "text": "What number and colour are in this image? One line."}])

proc.stdin.close()
try:
    code = proc.wait(timeout=15)
    print(f"stdin closed -> process exited by itself with code {code}")
except subprocess.TimeoutExpired:
    proc.kill()
    print("stdin closed but the process kept running: had to kill it")
print(f"total {time.monotonic() - t_start:.1f} s")
