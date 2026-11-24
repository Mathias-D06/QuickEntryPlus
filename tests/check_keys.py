"""Self-check for push-to-talk and double-tap: the key logic, the real hook (typing must pass
through untouched while it is installed) and the app flow with the mic and Gemini mocked.
The hook ignores synthetic input by design, so a physical key test is manual (see README).
Run: .venv\\Scripts\\python tests\\check_keys.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication, QLineEdit  # noqa: E402

from qeplus import app as qapp  # noqa: E402
from qeplus import keyhook  # noqa: E402
from qeplus.win import type_text, user32  # noqa: E402

qt = QApplication([])
CAPS, LCTRL, RCTRL, LSHIFT, C, LALT = 0x14, 0xA2, 0xA3, 0xA0, ord("C"), 0xA4


def pump(s=0.1):
    end = time.monotonic() + s
    while time.monotonic() < end:
        qt.processEvents()
        time.sleep(0.01)


def run(logic, events):
    """events: (vk, down, t[, injected]) -> list of (swallow, signal)"""
    return [logic.event(vk, down, inj[0] if inj else False, t) for vk, down, t, *inj in events]


# 1. push-to-talk: down/up swallowed and signalled; auto-repeat swallowed; Shift+Caps passes
L = keyhook.KeyLogic(ptt=CAPS)
assert run(L, [(CAPS, True, 0), (CAPS, True, 0.03), (CAPS, False, 1.2)]) == [
    (True, "ptt_down"), (True, None), (True, "ptt_up")]
assert run(L, [(LSHIFT, True, 2), (CAPS, True, 2.1), (CAPS, False, 2.2), (LSHIFT, False, 2.3)]) \
    == [(False, None)] * 4, "Shift+Caps Lock must toggle Caps Lock as normal"
assert run(L, [(CAPS, True, 3, True), (CAPS, False, 3.1, True)]) == [(False, None)] * 2, \
    "synthetic Caps Lock must pass through"
assert run(keyhook.KeyLogic(), [(CAPS, True, 0), (CAPS, False, 0.1)]) == [(False, None)] * 2

# 2. double-tap: two quick clean taps (left or right Ctrl) fire once
T = lambda: keyhook.KeyLogic(tap=keyhook.TAP_KEYS["ctrl"])  # noqa: E731
sig = [s for _, s in run(T(), [(LCTRL, True, 0), (LCTRL, False, 0.08),
                               (RCTRL, True, 0.25), (RCTRL, False, 0.32)])]
assert sig == [None, None, None, "double_tap"], sig
# too slow between taps, held too long, or part of a shortcut: nothing
for name, evs in {
    "slow": [(LCTRL, True, 0), (LCTRL, False, 0.1), (LCTRL, True, 0.6), (LCTRL, False, 0.7)],
    "held": [(LCTRL, True, 0), (LCTRL, False, 0.5), (LCTRL, True, 0.6), (LCTRL, False, 0.65)],
    "Ctrl+C then Ctrl": [(LCTRL, True, 0), (C, True, 0.05), (C, False, 0.08), (LCTRL, False, 0.1),
                         (LCTRL, True, 0.2), (LCTRL, False, 0.25)],
    "typing between": [(LCTRL, True, 0), (LCTRL, False, 0.05), (C, True, 0.1), (C, False, 0.12),
                       (LCTRL, True, 0.2), (LCTRL, False, 0.25)],
    "synthetic": [(LCTRL, True, 0, True), (LCTRL, False, 0.05, True),
                  (LCTRL, True, 0.1, True), (LCTRL, False, 0.15, True)],
}.items():
    assert "double_tap" not in [s for _, s in run(T(), evs)], name
# a triple tap fires once, not twice
sig = [s for _, s in run(T(), [(LCTRL, True, 0), (LCTRL, False, 0.05), (LCTRL, True, 0.1),
                               (LCTRL, False, 0.15), (LCTRL, True, 0.2), (LCTRL, False, 0.25)])]
assert sig.count("double_tap") == 1, sig
# Alt works the same way, and nothing is ever swallowed by the tap logic
A = keyhook.KeyLogic(tap=keyhook.TAP_KEYS["alt"])
out = run(A, [(LALT, True, 0), (LALT, False, 0.05), (LALT, True, 0.1), (LALT, False, 0.15)])
assert out[-1] == (False, "double_tap") and not any(sw for sw, _ in out)

# 3. the real hook: installed and removed on demand; typing passes through untouched
hook = keyhook.KeyHook()
field = QLineEdit()
field.show()
field.activateWindow()
field.setFocus()
pump(0.3)
hook.configure(CAPS, keyhook.TAP_KEYS["ctrl"])
assert hook.thread is not None, "hook not installed"
text = "The quick brown fox jumps over the lazy dog 0123456789 " * 4
if user32.GetForegroundWindow() == int(field.winId()):
    t0 = time.monotonic()
    type_text(text)
    pump(1.0)
    assert field.text() == text, (len(field.text()), len(text))
    print(f"   {len(text)} keystrokes typed through the hook intact")
else:
    print("   (skipped the typing test: couldn't focus the test field)")
hook.configure(None, set())
assert hook.thread is None, "hook not removed when both features are off"
field.close()

# 4. app flow: push-to-talk records, transcribes and asks the answer card; a tap does nothing
a = qapp.App()
a.keyhook.stop()
calls, toasts = [], []
a.toast.show_message = toasts.append


class FakeRecorder:
    recording = False
    seconds = 2.0

    def start(self):
        self.recording = True

    def stop(self):
        self.recording = False
        return b"\0" * (44 + int(self.seconds * 32000))


a.recorder = FakeRecorder()
a.transcriber.start = lambda wav, *args: calls.append(("transcribe", len(wav)))


class FakeClaude:
    busy = False

    def start(self):
        pass

    def check_account(self):
        pass

    def stop(self):
        pass

    def ask(self, text, images):
        calls.append(("ask", text))


a.claude = FakeClaude()
a.ptt_start()  # no key of any kind: Whisper runs on this PC
assert a.recorder.recording and a.mic.isVisible() and a.target == "ptt"
a.ptt_stop()
assert calls == [("transcribe", 44 + 64000)] and a.mic.state == "transcribing", calls
a.on_transcript("What is a monad?")
pump(0.2)
assert not a.mic.isVisible() and a.ask.isVisible() and calls[-1] == ("ask", "What is a monad?")
a.close_ask()
a.ask.clear_conversation()
calls.clear()
a.recorder.seconds = 0.1  # a tap of the key: no transcription, indicator gone
a.ptt_start()
a.ptt_stop()
assert calls == [] and not a.mic.isVisible() and a.target == "popup"

# 5. double-tap opens the bar; pause removes the hook; settings switch features off
a.keyhook.double_tap.emit()
pump(0.2)
assert a.ask.isVisible()
a.close_ask()
a.settings.values["push_to_talk"] = True
a.configure_keyhook()
assert a.keyhook.thread is not None
a.set_paused(True)
assert a.keyhook.thread is None, "hook still installed while paused"
a.set_paused(False)
a.settings.values["push_to_talk"] = a.settings.values["double_tap"] = False
a.configure_keyhook()
assert a.keyhook.thread is None, "hook installed with both features off"

a.keyhook.stop()
a.ask_key.disable()
a.watcher.stop()
print("push-to-talk and double-tap: all checks passed")
