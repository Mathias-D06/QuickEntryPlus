"""Self-check for the answer card: rendering, the window's behaviour and its wiring in the app.
Claude Code, key presses and the new-chat link are mocked; nothing is sent anywhere.
Run: .venv\\Scripts\\python tests\\check_askbar.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtCore import QEvent, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from qeplus import app as qapp  # noqa: E402
from qeplus import askbar  # noqa: E402

qt = QApplication([])


def pump(s=0.1):
    end = time.monotonic() + s
    while time.monotonic() < end:
        qt.processEvents()
        time.sleep(0.01)


# 1. Markdown: prose through Qt's parser, fenced code as a block with a Copy link
codes: list[str] = []
out = askbar.markdown_html("Use **this**:\n\n```python\nprint('hi')\n```\n\nDone.", codes)
assert codes == ["print('hi')"] and 'href="copy:0"' in out, out
assert "font-weight:700" in out.replace(" ", "") or "<b>" in out.lower(), "bold lost"
codes = []
askbar.markdown_html("partial:\n```js\nconst a = 1", codes)  # fence still open mid-stream
assert codes == ["const a = 1"], codes

# app with the backend mocked
a = qapp.App()
calls = []


class FakeClaude:
    busy = False

    def start(self):
        calls.append("start")

    def check_account(self):
        pass

    def stop(self):
        calls.append("stop")

    def reset(self):
        calls.append("reset")

    def ask(self, text, images):
        calls.append(("ask", text, list(images)))


a.claude = FakeClaude()
toasts, opened = [], []
a.toast.show_message = toasts.append
qapp.os.startfile = opened.append
w = a.ask

# 2. the hotkey opens the bar with focus and warms Claude
a.toggle_ask()
pump(0.3)
assert w.isVisible() and calls == ["start"], calls
assert not w.card.isVisible()

# 3. Enter sends the question with the attachments, which are then cleared
img = QImage(40, 30, QImage.Format.Format_RGB32)
img.fill(QColor("red"))
a.capture_target = "ask"
a.on_captured(img)  # a screenshot made from the bar is attached, not delivered
assert a.capture_target == "popup" and len(w.attachments) == 1 and os.path.exists(w.attachments[0])
assert w.chip.isVisible() and w.chip.text().startswith("1 image")
path = w.attachments[0]
w.input.setText("What is this?")
w.input.returnPressed.emit()
assert calls[-1] == ("ask", "What is this?", [path]), calls
pump(0.3)  # the chip collapses
assert w.attachments == [] and not w.chip.isVisible() and w.input.text() == ""

# 4. thinking (pulsing dots) until the first text, then streamed Markdown; Enter ignored while busy
assert w.card.isVisible() and w.thinking.isVisible()
w.input.setText("too soon")
w.input.returnPressed.emit()
assert calls[-1][1] == "What is this?", "sent while an answer was still streaming"
for piece in ("A red ", "square.\n\n```bash\necho ", "hi\n```"):
    w.add_text(piece)
pump(0.2)
w.finish_turn()
pump(0.3)
text = w.view.toPlainText()
assert "A red square." in text and "echo hi" in text and not w.thinking.isVisible(), text

# 5. Copy on a code block puts that code on the clipboard
turn = next(i for i, r in enumerate(w.rendered) if r[2])  # the turn with the code block
w._link(QUrl(f"copy-{w.nonce}:{turn}:0"))
assert QGuiApplication.clipboard().text() == "echo hi" and toasts[-1] == "Code copied."

# 6. the card grows, but never past ~60% of the screen; then it scrolls
w.input.setText("long")
w.input.returnPressed.emit()
w.add_text("\n\n".join(f"Paragraph {i}." for i in range(300)))
w.finish_turn()
pump(0.2)
cap = w.screen().availableGeometry().height() * askbar.MAX_SCREEN_FRACTION
assert w.view.height() <= cap and w.view.verticalScrollBar().maximum() > 0, w.view.height()

# 7. errors show as one line in the card
w.input.setText("x")
w.input.returnPressed.emit()
w.show_error("Claude Code isn't signed in. Run once in a terminal: & \"c.exe\" auth login")
assert "auth login" in w.view.toPlainText() and not w.busy

# 8. Open in Claude copies the conversation and opens a new chat
a.ask_open_in_claude()
assert opened == [qapp.NEW_CHAT] and "**Me:** What is this?" in QGuiApplication.clipboard().text()
assert not w.isVisible() and calls[-1] == "stop"

# 9. Esc ends the process and hides; reopening keeps the conversation (it resumes)
a.toggle_ask()
pump(0.2)
w.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
assert not w.isVisible() and calls[-1] == "stop"
a.toggle_ask()
pump(0.2)
assert w.turns and w.card.isVisible()

# 10. New: a fresh conversation, the card goes away, old captures are deleted
a.ask_new()
pump(0.3)  # the card folds away
assert calls[-1] == "reset" and not w.turns and not w.card.isVisible() and not os.path.exists(path)

# 11. dictation from the bar goes into the bar (Gemini engine), and auto-sends if set
a.settings.values["voice_engine"] = "gemini"
a.target = "ask"
a.settings.values["voice_autosend"] = False
w.input.setText("Explain")
a.on_transcript("this error please")
assert w.input.text() == "Explain this error please"
a.target = "ask"
a.settings.values["voice_autosend"] = True
w.input.clear()
a.on_transcript("What time is it in Tokyo?")
assert calls[-1] == ("ask", "What time is it in Tokyo?", []), calls

a.close_ask()
a.ask_key.disable()
a.watcher.stop()
print("answer card: all checks passed")
