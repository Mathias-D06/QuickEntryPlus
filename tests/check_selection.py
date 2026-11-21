"""Self-check for Open full Claude, Ask about selection and the clipboard chip, plus the new
global hotkeys' registration and conflict reporting. Live: it copies a selection from its own
test window, focuses Claude's main window, and sets the clipboard from a second process.
Run from PowerShell: .venv\\Scripts\\python tests\\check_selection.py
"""

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit  # noqa: E402

from qeplus import app as qapp  # noqa: E402
from qeplus.win import is_main_window, user32  # noqa: E402

qt = QApplication([])


def pump(s=0.1):
    end = time.monotonic() + s
    while time.monotonic() < end:
        qt.processEvents()
        time.sleep(0.01)


def taken_elsewhere(mods, vk):
    """Is this combo registered by someone (from a separate process's point of view)?"""
    code = (f"import ctypes; u = ctypes.windll.user32; ok = u.RegisterHotKey(None, 5, {mods}, {vk});"
            "print(not ok); ok and u.UnregisterHotKey(None, 5)")
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True
                          ).stdout.strip() == "True"


def set_clipboard_from_another_process(text):
    code = ("import win32clipboard as c; c.OpenClipboard(); c.EmptyClipboard();"
            f"c.SetClipboardText({text!r}, c.CF_UNICODETEXT); c.CloseClipboard()")
    subprocess.run([sys.executable, "-c", code], check=True)


# 0. a combo another program already holds is detected and reported (Ctrl+Alt+Shift+Q here)
blocker = subprocess.Popen([sys.executable, "-c",
                            "import ctypes, time; ctypes.windll.user32.RegisterHotKey("
                            "None, 9, 0x4007, 0x51); print('held', flush=True); time.sleep(30)"],
                           stdout=subprocess.PIPE, text=True)
blocker.stdout.readline()
a = qapp.App()
a.keyhook.stop()
toasts = []
a.toast.show_message = toasts.append
a.warned_conflicts.clear()  # it already warned once at start-up, before toasts were captured
a.ask_key.disable()
a.ask_key.enable()
assert any("Ctrl+Alt+Shift+Q" in t for t in toasts), toasts
blocker.kill()
blocker.wait()  # its hotkey is released only once it has really exited
time.sleep(0.2)
a.ask_key.disable()
a.ask_key.enable()

# 1. the new global hotkeys are registered by the app (others can't take them)
assert taken_elsewhere(0x4007, 0x43), "Ctrl+Alt+Shift+C not registered"   # Ctrl+Alt+Shift+C
assert taken_elsewhere(0x4007, 0x51), "Ctrl+Alt+Shift+Q not registered"   # Ctrl+Alt+Shift+Q
a.settings.values["open_claude_hotkey"] = ""
a.ask_key.set_bindings(a.global_bindings())
assert not taken_elsewhere(0x4007, 0x43), "an empty setting should leave the hotkey free"
a.settings.values["open_claude_hotkey"] = "Ctrl+Alt+Shift+C"
a.ask_key.set_bindings(a.global_bindings())

# 2. Open full Claude: Claude's main window comes to the front, restored if minimised
a.focus_claude()
pump(0.6)
assert is_main_window(user32.GetForegroundWindow()), "Claude's main window isn't in front"
assert not user32.IsIconic(user32.GetForegroundWindow())
launched = []
real_find, real_start = qapp.find_windows, qapp.os.startfile
qapp.find_windows = lambda pred: []  # as if Claude weren't running
qapp.os.startfile = launched.append
a.focus_claude()
assert launched == [qapp.CLAUDE_APP], launched
qapp.find_windows, qapp.os.startfile = real_find, real_start

# 3. Ask about selection: the selection becomes a chip; your clipboard is put back as it was
src = QLineEdit("the mitochondria is the powerhouse of the cell")
src.setWindowTitle("selection source")
src.show()
src.activateWindow()
src.setFocus()
src.selectAll()
pump(0.4)
QGuiApplication.clipboard().setText("MY ORIGINAL CLIPBOARD")
pump(0.1)
if user32.GetForegroundWindow() == int(src.winId()):
    a.ask_about_selection()
    pump(1.2)
    assert a.ask.context == "the mitochondria is the powerhouse of the cell", a.ask.context
    assert a.ask.context_chip.isVisible() and a.ask.context_chip.text().startswith("Selected text")
    assert QGuiApplication.clipboard().text() == "MY ORIGINAL CLIPBOARD", "clipboard not restored"
    sent = []
    a.claude.ask = lambda text, images: sent.append(text)
    a.ask.input.setText("Is this right?")
    a.ask.input.returnPressed.emit()
    assert sent and sent[0].startswith("Is this right?") and "powerhouse" in sent[0], sent
    pump(0.3)  # the chip collapses
    assert not a.ask.context_chip.isVisible() and "selected text" in a.ask.turns[-1][0]
    a.close_ask()
    a.ask.clear_conversation()
    print("   selection copied, sent and the clipboard restored (live)")
else:
    print("   (skipped the live selection test: couldn't focus the test field)")
src.close()
# in a terminal, Ctrl+Insert is used instead of Ctrl+C (which would interrupt a program)
pressed = []
real_press, real_text_of, real_sel = qapp.press, qapp.text_of, qapp.uia.focused_selection
qapp.press = lambda *k: pressed.append(k)
qapp.text_of = lambda h, getter: "CASCADIA_HOSTING_WINDOW_CLASS"
qapp.uia.focused_selection = lambda: (None, "")  # as if the terminal exposed no selection
a.ask_about_selection()
qapp.press, qapp.text_of, qapp.uia.focused_selection = real_press, real_text_of, real_sel
assert pressed[0] == (qapp.VK_CONTROL, 0x2D), pressed
pump(1.0)
a.close_ask()
a.ask.clear_context()

# 4. clipboard chip: offered for something copied elsewhere in the last 30 s, then attachable
set_clipboard_from_another_process("copied in another app")
pump(0.4)
assert a.clip_changed, "didn't notice another app's copy"
a.open_ask()
pump(0.2)
assert a.ask.clip_chip.isVisible() and a.ask.clip_chip.text() == "Attach clipboard"
a.ask.clip_chip.click()
pump(0.3)  # the offer collapses
assert a.ask.context == "copied in another app" and not a.ask.clip_chip.isVisible()
assert a.ask.context_chip.text().startswith("Clipboard text")
a.close_ask()
a.ask.clear_context()
set_clipboard_from_another_process("old news")
pump(0.4)
a.clip_changed -= 31  # copied more than 30 s ago: no offer
a.open_ask()
pump(0.2)
assert not a.ask.clip_chip.isVisible()
a.close_ask()
set_clipboard_from_another_process("again")
pump(0.4)
a.settings.values["clipboard_chip"] = False
a.open_ask()
pump(0.2)
assert not a.ask.clip_chip.isVisible(), "offered with clipboard_chip off"
a.close_ask()
QGuiApplication.clipboard().setText("our own write")
pump(0.3)
a.settings.values["clipboard_chip"] = True
a.clip_changed = 0.0
QGuiApplication.clipboard().setText("our own write 2")
pump(0.3)
assert a.clip_changed == 0.0, "the app's own clipboard writes must not count"

a.ask_key.disable()
a.watcher.stop()
print("open Claude, selection and clipboard chip: all checks passed")
