"""Self-check for the shortcut review fixes: Claude's own quick entry pop-up and the Plus bar
must never get mixed up. Keys are mocked (nothing is pressed) and PowerToys' settings are read
from temporary copies.
Run: .venv\\Scripts\\python tests\\check_shortcuts.py
"""

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from qeplus import app as qapp  # noqa: E402
from qeplus import hotkeys, keyhook, settings  # noqa: E402

qt = QApplication([])


def pump(s=0.1):
    end = time.monotonic() + s
    while time.monotonic() < end:
        qt.processEvents()
        time.sleep(0.01)


# 1. no global hotkey is registered until settings.json has been read (the defaults could hold
# a combo Claude uses on this PC)
a = qapp.App()
assert not a.ask_key.active, "registered the default hotkeys before reading settings"
a.apply_settings()
assert a.ask_key.active
a.keyhook.stop()
pressed, toasts = [], []
qapp.press = lambda *keys: pressed.append(keys)
a.toast.show_message = toasts.append

# 2 and 8. keys are sent only once the shortcut's modifiers are up, and Win+H only into the bar
held = {"down": True}
real_down = hotkeys.modifiers_down
hotkeys.modifiers_down = lambda: held["down"]
ran = []
hotkeys.when_released(lambda: ran.append(1))
pump(0.1)
assert ran == [], "ran while Alt was still held"
held["down"] = False
pump(0.1)
assert ran == [1]
held["down"] = True
hotkeys.when_released(lambda: ran.append(2), timeout=0.1)
pump(0.3)
assert ran == [1], "a key held for good must drop the action, not run it"
held["down"] = False
a.settings.values["voice_engine"] = "windows"
a.ask_attach("voice")  # Alt+M in the bar
pump(0.4)
if a.ask.isActiveWindow():
    assert pressed == [(qapp.VK_LWIN, ord("H"))], pressed
else:
    print("   (the bar didn't get focus here: checked the not-focused case only)")
pressed.clear()
a.ask.hide()
a.voice_typing_in_bar()
assert pressed == [], "Win+H sent although the bar had gone"

# 7. a hotkey waiting for keys that never come up is dropped, not stuck forever
h = a.ask_key
held["down"] = True
h.pending, h.deadline = "ask", time.monotonic() - 1
h.release.start()
h._wait_for_release()
assert h.pending == "" and not h.release.isActive()
held["down"] = False
hotkeys.modifiers_down = real_down

# 3. captures and transcripts don't share a target
a.settings.values["voice_engine"] = "whisper"
a.target = "ask"
a.recorder.start = lambda: (_ for _ in ()).throw(OSError("no mic"))
a.start_recording()
assert a.target == "popup", "a failed dictation left the bar as the target"
delivered = []
a.deliverer.start = lambda image, draft: delivered.append(image)
a.capture_target = "ask"
a.target = "ask"  # a dictation for the bar is under way
a.start_capture()  # ...and a screenshot is started from Claude's pop-up toolbar
a.snipper.cancel()
assert a.capture_target == "popup" and a.target == "ask"
from PySide6.QtGui import QImage  # noqa: E402

a.busy = True
a.on_captured(QImage(4, 4, QImage.Format.Format_RGB32))
assert delivered and not a.ask.attachments and a.target == "ask", "the capture went to the bar"
a.target = "popup"

# 4. opening the bar from Claude's pop-up never makes the pop-up the window to attach
real_user32, real_is_popup = qapp.user32, qapp.is_popup
real_app_window, real_own = qapp.is_app_window, qapp.is_own_window


class User32:
    def __getattr__(self, name):
        return getattr(real_user32, name)

    @staticmethod
    def GetForegroundWindow():
        return 0x1234  # Claude's pop-up


qapp.user32, qapp.is_popup = User32(), lambda h: h == 0x1234
qapp.is_app_window, qapp.is_own_window = lambda h: True, lambda h: False
a.watcher.before_popup, a.before_ask = 0x5678, 0
a.claude.start = a.claude.check_account = lambda: None
a.open_ask()
assert a.before_ask == 0x5678, hex(a.before_ask)
qapp.user32, qapp.is_popup = real_user32, real_is_popup
qapp.is_app_window, qapp.is_own_window = real_app_window, real_own
a.close_ask()

# 5. PowerToys Find My Mouse on "press Left Ctrl twice" is detected (read-only)
pt = tempfile.mkdtemp(prefix="qeplus-pt-")
os.makedirs(os.path.join(pt, "FindMyMouse"))


def powertoys(enabled, method):
    with open(os.path.join(pt, "settings.json"), "w", encoding="utf-8-sig") as f:
        json.dump({"enabled": {"FindMyMouse": enabled}}, f)
    with open(os.path.join(pt, "FindMyMouse", "settings.json"), "w", encoding="utf-8") as f:
        json.dump({"properties": {"activation_method": {"value": method}}}, f)


powertoys(True, 0)
assert qapp.find_my_mouse_on_ctrl(pt)
powertoys(True, 1)  # shake the mouse instead
assert not qapp.find_my_mouse_on_ctrl(pt)
powertoys(False, 0)
assert not qapp.find_my_mouse_on_ctrl(pt)
assert not qapp.find_my_mouse_on_ctrl(os.path.join(pt, "missing"))
real_fmm = qapp.find_my_mouse_on_ctrl
qapp.find_my_mouse_on_ctrl = lambda: True
a.warned_conflicts.clear()
a.settings.values["double_tap"], a.settings.values["double_tap_key"] = True, "Ctrl"
a.configure_keyhook()
assert any("Find My Mouse" in t for t in toasts), toasts
toasts.clear()
a.configure_keyhook()
assert not toasts, "warned twice"
qapp.find_my_mouse_on_ctrl = real_fmm
a.keyhook.stop()

# 6. double-tap Ctrl doesn't fire while Alt (or Shift, Win) is held through the taps
CTRL, ALT = 0xA2, 0xA4
logic = keyhook.KeyLogic(tap=keyhook.TAP_KEYS["ctrl"])


def taps(start):
    out = []
    for i, (vk, down) in enumerate([(CTRL, True), (CTRL, False), (CTRL, True), (CTRL, False)]):
        out.append(logic.event(vk, down, False, start + 0.05 * (i + 1))[1])
    return out


assert "double_tap" in taps(10.0), "a plain double tap must still work"
logic.event(ALT, True, False, 20.0)
assert "double_tap" not in taps(20.0), "fired with Alt held"
logic.event(ALT, False, False, 20.5)
assert "double_tap" in taps(21.0), "Alt was released"
logic.event(ALT, True, False, 30.0)  # its key-up never seen (a secure desktop, say)
assert "double_tap" in taps(35.0), "a key-up we missed must not block double-tap for good"

# 9. settings flag two shortcuts on one combo; popup_shortcut is never sent if it's ours
values, problems = settings.validate({"ask_hotkey": "Ctrl+Alt+Space"})  # = popup_shortcut
assert any("popup_shortcut and ask_hotkey" in p for p in problems), problems
assert not settings.validate({})[1], "the defaults clash"
assert not settings.validate({"ask_hotkey": "Ctrl+Alt+Space",
                              "popup_shortcut": "Ctrl+Alt+Shift+Space"})[1]
a.settings.values["popup_shortcut"] = a.settings.values["ask_hotkey"]
pressed.clear()
a.open_popup()
assert pressed == [], "sent our own hotkey to 'reopen Claude's pop-up'"
a.settings.values["popup_shortcut"] = "Ctrl+Alt+Space"
a.settings.values["ask_hotkey"] = "Ctrl+Alt+Shift+Space"
a.open_popup()
assert pressed == [(0x11, 0x12, 0x20)], pressed
assert hotkeys.qt_sequence("Win+Control+S") == "Meta+Ctrl+S"
assert QKeySequence(hotkeys.qt_sequence("Win+S")).toString() == "Meta+S"

a.ask_key.disable()
a.watcher.stop()
print("shortcut fixes: all checks passed")
