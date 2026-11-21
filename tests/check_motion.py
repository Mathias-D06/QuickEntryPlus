"""Self-check for dragging the answer bar and its animations: remembered positions per monitor,
clamping to the work area, the card opening above the bar when there's no room below, the
open/close stills, the card growing smoothly, the thinking dots, chips, button states, and
everything turning instant when animations are off. Windows' move loop is simulated (moves
plus its enter/exit messages); no input is sent.
Run: .venv\\Scripts\\python tests\\check_motion.py
"""

import ctypes
import json
import os
import sys
import tempfile
import time
from ctypes import wintypes as wt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtCore import QAbstractAnimation, QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from qeplus import askbar, settings, toolbar  # noqa: E402

qt = QApplication([])
assert toolbar.animations_enabled(), "turn on Windows' Animation effects to run this check"


def pump(s=0.1):
    end = time.monotonic() + s
    while time.monotonic() < end:
        qt.processEvents()
        time.sleep(0.005)


def native(w, message):
    """Deliver a Windows message to the window's native event handler, as Windows would."""
    msg = wt.MSG(int(w.winId()), message, 0, 0)
    return w.nativeEvent(b"windows_generic_MSG", ctypes.addressof(msg))


folder = tempfile.mkdtemp(prefix="qeplus-motion-")
path = os.path.join(folder, "positions.json")
w = askbar.AskWindow({}, positions_path=path)
screen = qt.primaryScreen()
area = screen.availableGeometry()

# 1. opening: the real window is shown and focused at once, a still plays over it, then
# the real one takes its place; the default spot is top-centre of the screen
w.open()
assert w.isVisible() and w.ghost.isVisible() and w.windowOpacity() == 0
pump(0.35)
assert not w.ghost.isVisible() and w.windowOpacity() == 1
default = QPoint(area.center().x() - askbar.WIDTH // 2, area.top() + area.height() // 4)
assert w.bar_pos == default and w.bar.mapToGlobal(QPoint(0, 0)) == default, w.bar_pos

# 2. the grip fades in with the pointer over the bar
assert w.bar.grip.alpha.value == 0
w.bar.enterEvent(None)
pump(0.25)
assert w.bar.grip.alpha.value == 1
w.bar.leaveEvent(None)
pump(0.25)
assert w.bar.grip.alpha.value == 0

# 3. a drag: Windows' move loop starts (the shadow deepens), the window moves, the loop ends;
# a drop partly off-screen is pulled back inside the work area and remembered for this monitor
native(w, askbar.WM_ENTERSIZEMOVE)
pump(0.2)
assert w.lift.value == 1
w.move(area.right() - 100, area.top() + 200)  # dropped hanging off the right edge
assert w.bar_pos.x() > area.right() - askbar.WIDTH
native(w, askbar.WM_EXITSIZEMOVE)
pump(0.35)
assert w.lift.value == 0
assert w.bar_pos.x() == area.right() + 1 - askbar.WIDTH and w.bar_pos.y() == area.top() + 200 \
    + askbar.SHADOW, w.bar_pos
saved = json.load(open(path))
assert saved == {screen.name(): [w.bar_pos.x() - area.left(), w.bar_pos.y() - area.top()]}, saved
# and it can't go under the taskbar either
native(w, askbar.WM_ENTERSIZEMOVE)
w.move(area.left() + 50, area.bottom() - 10)
native(w, askbar.WM_EXITSIZEMOVE)
pump(0.35)
assert w.bar_pos.y() + askbar.BAR_H - 1 <= area.bottom(), (w.bar_pos, area)
spot = QPoint(w.bar_pos)

# 4. closing: the window goes at once (a still plays the close), and reopens where it was left
w.dismiss()
assert not w.isVisible() and w.ghost.isVisible()
pump(0.3)
assert not w.ghost.isVisible()
w.open()
pump(0.35)
assert w.bar_pos == spot, (w.bar_pos, spot)
w.dismiss()
pump(0.3)

# 5. a saved file that's damaged, or entries that are, are ignored (the default is used)
open(path, "w").write('{"\\\\\\\\.\\\\DISPLAY9": [1, 2], "bad": ["x", 2], "big": [1e9, 0],'
                      ' "bool": [true, 1]}')
assert askbar.load_positions(path) == {"\\\\.\\DISPLAY9": (1, 2)}
open(path, "w").write("not json")
assert askbar.load_positions(path) == {}
w.positions = askbar.load_positions(path)
w.open()
pump(0.35)
assert w.bar_pos == default, w.bar_pos  # a monitor that's gone (or none saved): the default

# 6. reset (double-click the grip, or the tray item): forgets the spots and glides back
native(w, askbar.WM_ENTERSIZEMOVE)
w.move(area.left() + 40, area.top() + 40)
native(w, askbar.WM_EXITSIZEMOVE)
pump(0.35)
assert w.bar_pos != default and w.positions
w.bar.grip.reset.emit()
pump(0.35)
assert w.bar_pos == default and w.positions == {} and json.load(open(path)) == {}

# 7. the card grows smoothly as text streams in, and never jumps there
w.begin_turn("What is a monad?", 0)
assert w.card.isVisible() and w.thinking.isVisible() and not w.card_above
pump(0.3)
assert w.reveal.value == 1 and w.card.mask().isEmpty()  # fully revealed
w.add_text("\n\n".join(f"Paragraph {i}." for i in range(12)))
pump(0.06)  # one render in: on its way, not there yet
assert w.view.height() < w.view_target, (w.view.height(), w.view_target)
pump(0.4)
assert w.view.height() == w.view_target and w.text_in.value == 1
assert not w.thinking.isVisible(), "the dots should have faded out"
assert w.bar.mapToGlobal(QPoint(0, 0)) == default, "the bar moved while the card grew"
w.finish_turn()

# 8. near the bottom of the screen the card opens above the bar, and grows upwards
w.clear_conversation()
pump(0.3)
assert w.card.isHidden()
low = QPoint(default.x(), area.bottom() - 200)
w._place(low)
w.begin_turn("Long one", 0)
assert w.card_above
w.add_text("\n\n".join(f"Line {i}." for i in range(40)))
pump(0.6)
w.finish_turn()
pump(0.1)
bar_g, card_g = w.bar.geometry(), w.card.geometry()
assert card_g.bottom() < bar_g.top(), (card_g, bar_g)
assert w.bar.mapToGlobal(QPoint(0, 0)) == low, "the bar should stay put while the card grows up"
assert w.mapToGlobal(card_g.topLeft()).y() >= area.top(), "the card went off the top"
w.clear_conversation()
pump(0.3)
assert not w.card_above or w.card.isHidden()

# 9. chips slide and fade in, then collapse and go
w.add_attachment(os.path.join(folder, "nothing.png"))
assert w.chip.isVisible() and w.chip.graphicsEffect() is not None
pump(0.3)
assert w.chip.graphicsEffect() is None and w.chip.maximumWidth() == askbar.QWIDGETSIZE_MAX
w.clear_attachments(delete=False)
assert w.chip.isVisible()  # still collapsing
pump(0.3)
assert not w.chip.isVisible() and w.chip.maximumWidth() == askbar.QWIDGETSIZE_MAX

# 10. buttons: the hover fades, recording pulses a ring, transcribing spins
b = w.buttons["voice"]
b.hover.to(1, 100)
pump(0.2)
assert b.hover.value == 1
b.set_state("active")
assert b.ticker.state() == QAbstractAnimation.State.Running
b.set_state("busy")
assert b.ticker.state() == QAbstractAnimation.State.Running
b.set_state("idle")
w.dismiss()
pump(0.3)

# 11. with animations off (the setting, or Windows' Animation effects), changes are instant
toolbar.user_animations = False
assert not toolbar.animations_enabled()
w.open()
assert w.isVisible() and not w.ghost.isVisible() and w.windowOpacity() == 1
w.add_attachment(os.path.join(folder, "nothing.png"))
w.clear_attachments(delete=False)
assert not w.chip.isVisible()
w.begin_turn("q", 0)
assert w.reveal.value == 1
w.clear_conversation()
assert w.card.isHidden()
b.set_state("active")
assert b.ticker.state() != QAbstractAnimation.State.Running, "the ring should be steady"
b.set_state("busy")
assert b.ticker.state() == QAbstractAnimation.State.Running, "the spinner always turns"
b.set_state("idle")
w.dismiss()
assert not w.isVisible() and not w.ghost.isVisible()
toolbar.user_animations = True

# 12. the setting itself
values, problems = settings.validate({"animations": False})
assert values["animations"] is False and not problems
values, problems = settings.validate({"animations": "no"})
assert values["animations"] is True and problems

print("dragging and animations: all checks passed")
