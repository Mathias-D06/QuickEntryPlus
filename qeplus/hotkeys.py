"""Global hotkeys that exist only while the pop-up is open (RegisterHotKey on show, released on
hide), so Alt+S and friends keep working normally in every other app.

A hotkey fires only after its modifier keys are released: the actions send keys of their own
(Win+H, Ctrl+V), which would otherwise combine with the Alt still held down. If they're still
down after RELEASE_TIMEOUT (a key Windows thinks is stuck), the hotkey is dropped rather than
blocking every hotkey for good.
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes as wt

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, QTimer, Signal

from .win import user32

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
MODIFIERS = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "control": MOD_CONTROL, "shift": MOD_SHIFT,
             "win": MOD_WIN}
MODIFIER_VKS = (0x10, 0x11, 0x12, 0x5B, 0x5C)  # Shift, Ctrl, Alt, left/right Win
NAMED_KEYS = {"space": 0x20, "tab": 0x09, "enter": 0x0D}
QT_NAMES = {"win": "Meta", "control": "Ctrl"}  # Qt's spelling, for QKeySequence
RELEASE_TIMEOUT = 2.0  # seconds
user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
log = logging.getLogger(__name__)


def modifiers_down() -> bool:
    return any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in MODIFIER_VKS)


def when_released(action, timeout: float = RELEASE_TIMEOUT) -> None:
    """Run action once no modifier key is held (polled every 20 ms), so keys it sends don't
    combine with a shortcut's Alt or Shift; after timeout it's dropped instead."""
    end = time.monotonic() + timeout

    def check():
        if not modifiers_down():
            action()
        elif time.monotonic() < end:
            QTimer.singleShot(20, check)
        else:
            log.warning("modifier keys still held after %.1f s: skipped sending keys", timeout)

    check()


def qt_sequence(combo: str) -> str:
    """'Win+Control+S' -> 'Meta+Ctrl+S': QKeySequence reads Win and Control as nothing."""
    return "+".join(QT_NAMES.get(p.strip().lower(), p.strip()) for p in combo.split("+"))


def parse(combo: str) -> tuple[int, int]:
    """'Alt+S' -> (MOD_ALT, ord('S')). Keys: A-Z, 0-9, Space, Tab, Enter (these need a
    modifier) and F1-F24. Raises ValueError otherwise."""
    *mods, key = [part.strip().lower() for part in combo.split("+")]
    flags = 0
    for m in mods:
        if m not in MODIFIERS:
            raise ValueError(f"unknown modifier {m!r} in {combo!r}")
        flags |= MODIFIERS[m]
    if key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        return flags, 0x70 + int(key[1:]) - 1
    if len(key) == 1 and key.isascii() and key.isalnum():  # "ß".upper() is "SS"
        vk = ord(key.upper())
    elif key in NAMED_KEYS:
        vk = NAMED_KEYS[key]
    else:
        raise ValueError(f"unsupported key {key!r} in {combo!r}")
    if not flags:
        raise ValueError(f"{combo!r} needs a modifier (Alt, Ctrl, Shift or Win); on its own "
                         "that key would stop working for typing")
    return flags, vk


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, on_hotkey):
        super().__init__()
        self.on_hotkey = on_hotkey

    def nativeEventFilter(self, event_type, message):
        msg = wt.MSG.from_address(int(message))
        if msg.message == WM_HOTKEY:
            self.on_hotkey(msg.hWnd, msg.wParam)
        return False, 0


class Hotkeys(QObject):
    triggered = Signal(str)   # action name, once the modifiers are released
    conflict = Signal(str)    # a combo another app already owns

    def __init__(self, hwnd: int, bindings: dict[str, str]):
        super().__init__()
        self.hwnd = hwnd
        self.active = False
        self.bindings: dict[int, tuple] = {}
        self.set_bindings(bindings)
        self.pending = ""
        self.deadline = 0.0
        self.release = QTimer(self, interval=20)
        self.release.timeout.connect(self._wait_for_release)
        self._filter = _Filter(self._on_hotkey)  # keep a reference: Qt doesn't own it
        QCoreApplication.instance().installNativeEventFilter(self._filter)

    def set_bindings(self, bindings: dict[str, str]) -> None:
        """{action: "Alt+S"}; an empty combo leaves that action without a hotkey."""
        was_active = self.active
        if was_active:
            self.disable()
        self.bindings = {i + 1: (name, combo, *parse(combo))
                         for i, (name, combo) in enumerate(bindings.items()) if combo}
        if was_active:
            self.enable()

    def enable(self) -> None:
        if self.active:
            return
        self.active = True
        for hid, (name, combo, mods, vk) in self.bindings.items():
            if not user32.RegisterHotKey(self.hwnd, hid, mods | MOD_NOREPEAT, vk):
                log.warning("couldn't register %s for %s (error %d)", combo, name,
                            ctypes.get_last_error())
                self.conflict.emit(combo)

    def disable(self) -> None:
        self.release.stop()
        self.pending = ""
        if not self.active:
            return
        self.active = False
        for hid in self.bindings:
            user32.UnregisterHotKey(self.hwnd, hid)

    def _on_hotkey(self, hwnd, hid) -> None:
        if hwnd == self.hwnd and hid in self.bindings and not self.pending:
            self.pending = self.bindings[hid][0]
            self.deadline = time.monotonic() + RELEASE_TIMEOUT
            self.release.start()

    def _wait_for_release(self) -> None:
        if modifiers_down():
            if time.monotonic() > self.deadline:  # a stuck key mustn't block hotkeys for good
                log.warning("modifier keys still held: dropped the %s hotkey", self.pending)
                self.release.stop()
                self.pending = ""
            return
        self.release.stop()
        name, self.pending = self.pending, ""
        self.triggered.emit(name)
