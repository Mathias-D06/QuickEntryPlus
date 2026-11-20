"""Self-check for the hotkeys: parsing, firing only while enabled, waiting for the modifier to be
released, and conflict reporting. Uses Ctrl+Alt+Shift+F13 (no keyboard has F13, so nothing
else reacts to it) instead of the real Alt+S/W/M.
Run: .venv\\Scripts\\python tests\\check_hotkeys.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from qeplus import hotkeys  # noqa: E402
from qeplus.win import VK_CONTROL, VK_MENU, _send  # noqa: E402

qt = QApplication([])


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qt.processEvents()
        time.sleep(0.01)


# 1. parsing
assert hotkeys.parse("Alt+S") == (hotkeys.MOD_ALT, ord("S"))
assert hotkeys.parse("ctrl + shift + f13") == (hotkeys.MOD_CONTROL | hotkeys.MOD_SHIFT, 0x7C)
assert hotkeys.parse("Win+5") == (hotkeys.MOD_WIN, ord("5"))
for bad in ("Hyper+S", "Alt+PageUp", "Alt+F25", "S", "Space", "Alt+ß"):
    try:
        hotkeys.parse(bad)
        raise AssertionError(f"{bad} should not parse")
    except ValueError:
        pass

window = QWidget()
COMBO, SHIFT, F13 = "Ctrl+Alt+Shift+F13", 0x10, 0x7C
hk = hotkeys.Hotkeys(int(window.winId()), {"screenshot": COMBO})
fired, conflicts = [], []
hk.triggered.connect(fired.append)
hk.conflict.connect(conflicts.append)


def hold_and_tap(release_after: float):
    """Hold Ctrl+Alt+Shift, tap F13, keep holding, then release."""
    _send([(VK_CONTROL, 0, 0), (VK_MENU, 0, 0), (SHIFT, 0, 0), (F13, 0, 0), (F13, 0, 2)])
    pump(release_after)
    held = list(fired)
    _send([(SHIFT, 0, 2), (VK_MENU, 0, 2), (VK_CONTROL, 0, 2)])
    pump(0.2)
    return held


# 2. disabled: nothing fires
assert hold_and_tap(0.2) == [] and fired == [], fired

# 3. enabled: fires, but only after the modifiers are released
hk.enable()
assert hold_and_tap(0.3) == [], "fired while Ctrl/Alt/Shift were still held"
assert fired == ["screenshot"], fired

# 4. a second registration of the same combo is reported as a conflict
window2 = QWidget()  # kept referenced: a collected widget's hwnd is invalid (error 1400)
other = hotkeys.Hotkeys(int(window2.winId()), {"window": COMBO})
other.conflict.connect(conflicts.append)
other.enable()
assert conflicts == [COMBO], conflicts
other.disable()

# 5. disabled again: nothing fires
hk.disable()
fired.clear()
hold_and_tap(0.2)
assert fired == [], fired

print("hotkeys: all checks passed")
