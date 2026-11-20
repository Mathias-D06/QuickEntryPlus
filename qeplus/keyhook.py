"""Push-to-talk and double-tap detection with a low-level keyboard hook (WH_KEYBOARD_LL).

A hook sees every keystroke on the system, so it is kept as small as possible: it runs on its
own thread, is only installed while one of the two features is on, swallows nothing except the
push-to-talk key (and that only without Shift, so Shift+Caps Lock still toggles Caps Lock) and
Esc while that key is held (to cancel), and ignores synthetic input (PowerToys remaps, and this
app's own keystrokes, which carry QEPLUS_INPUT_TAG) so those can't fire it. The callback does no
logging or other I/O: Windows drops an LL hook that is too slow.
The decisions live in KeyLogic, which is plain Python and tested without a hook.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes as wt

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Signal

from .win import QEPLUS_INPUT_TAG, VK_ESCAPE

WH_KEYBOARD_LL, WM_QUIT = 13, 0x0012
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
WM_WTSSESSION_CHANGE, WM_POWERBROADCAST = 0x02B1, 0x0218
# console/remote disconnect, lock, unlock
SESSION_EVENTS = {0x2, 0x4, 0x7, 0x8}
# suspend, resume (user), resume (automatic); not battery/AC or power-setting changes
POWER_EVENTS = {0x4, 0x7, 0x12}
LLKHF_INJECTED = 0x10
SHIFTS = {0x10, 0xA0, 0xA1}  # generic, left, right
TAP_KEYS = {"ctrl": {0x11, 0xA2, 0xA3}, "alt": {0x12, 0xA4, 0xA5}}
PTT_KEYS = {"capslock": 0x14, "scrolllock": 0x91, "pause": 0x13, "rightctrl": 0xA3,
            "rightalt": 0xA5, "insert": 0x2D, **{f"f{n}": 0x6F + n for n in range(1, 25)}}
TAP_MAX_HOLD, TAP_GAP = 0.3, 0.3  # seconds: a tap is a short press; the second within ~300 ms
log = logging.getLogger(__name__)


def ptt_vk(name: str) -> int:
    """'CapsLock' -> 0x14. Raises ValueError for an unsupported key."""
    key = name.replace(" ", "").lower()
    if key not in PTT_KEYS:
        raise ValueError(f"unsupported push-to-talk key {name!r} (try CapsLock, ScrollLock, "
                         "Pause, RightCtrl, RightAlt, Insert or F1-F24)")
    return PTT_KEYS[key]


class KeyLogic:
    """Decides, per key event, whether to swallow it and what it means. Pure: no Windows calls.
    A lock makes configure()/reset() from the Qt thread safe against the hook thread."""

    def __init__(self, ptt: int | None = None, tap: set[int] = frozenset()):
        self.lock = threading.Lock()
        self.ptt, self.tap = ptt, set(tap)
        self.reset()

    def reset(self) -> None:
        """Forget held keys and half-made taps (after lock, sleep, or a reconfigure)."""
        self.shifts: set[int] = set()   # left, right and generic Shift tracked separately
        self.ptt_active = False
        self.esc_swallowed = False
        self.tap_down: float | None = None  # when the tap key went down, while it's held
        self.tap_clean = False              # no other key pressed while it was held
        self.last_tap: float | None = None  # when the last clean tap was released

    def configure(self, ptt: int | None, tap: set[int]) -> None:
        with self.lock:
            self.ptt, self.tap = ptt, set(tap)
            self.reset()

    def event(self, vk: int, down: bool, injected: bool, t: float,
              extra: int = 0) -> tuple[bool, str | None]:
        """Returns (swallow, signal); signal is ptt_down, ptt_up, ptt_cancel, double_tap or
        None. Synthetic input (injected, or tagged as this app's own) is never acted on."""
        if injected or extra == QEPLUS_INPUT_TAG:
            return False, None
        with self.lock:
            return self._event(vk, down, t)

    def _event(self, vk: int, down: bool, t: float) -> tuple[bool, str | None]:
        if vk in SHIFTS:
            (self.shifts.add if down else self.shifts.discard)(vk)
        if self.ptt is not None and vk == self.ptt:
            if down:
                if self.ptt_active:
                    return True, None  # auto-repeat while held
                if self.shifts:
                    return False, None  # Shift+Caps Lock: the normal toggle
                self.ptt_active = True
                return True, "ptt_down"
            if self.ptt_active:
                self.ptt_active = False
                return True, "ptt_up"
            return False, None  # never swallow a key-up whose key-down we let through
        if vk == VK_ESCAPE and self.ptt_active and down:
            self.esc_swallowed = True
            return True, "ptt_cancel"  # Esc while holding the key: discard the recording
        if vk == VK_ESCAPE and not down and self.esc_swallowed:
            self.esc_swallowed = False
            return True, None
        if vk in self.tap:
            if down:
                if self.tap_down is None:  # not an auto-repeat
                    self.tap_down, self.tap_clean = t, True
                return False, None
            first_down, clean = self.tap_down, self.tap_clean
            self.tap_down = None
            if first_down is None or not clean or t - first_down > TAP_MAX_HOLD:
                self.last_tap = None
                return False, None
            if self.last_tap is not None and first_down - self.last_tap <= TAP_GAP:
                self.last_tap = None
                return False, "double_tap"
            self.last_tap = t
            return False, None
        if down:  # any other key: part of a shortcut, or typing between taps
            self.tap_clean = False
            self.last_tap = None
        return False, None


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HANDLE
user32.CallNextHookEx.argtypes = [wt.HANDLE, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = [wt.HANDLE]
user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
kernel32.GetModuleHandleW.restype = wt.HMODULE


class KeyHook(QObject):
    """Emits on the Qt thread. configure() installs or removes the hook as needed."""

    ptt_down = Signal()
    ptt_up = Signal()
    ptt_cancel = Signal()
    double_tap = Signal()

    def __init__(self):
        super().__init__()
        self.logic = KeyLogic()
        self.thread: threading.Thread | None = None
        self.thread_id = 0
        self.errors = 0  # counted, never logged, inside the hook
        self._proc = HOOKPROC(self._callback)  # kept referenced: Windows calls into it

    def configure(self, ptt: int | None, tap: set[int]) -> None:
        self.logic.configure(ptt, tap)
        if ptt is None and not tap:
            self.stop()
        elif not self.thread:
            ready = threading.Event()
            self.thread = threading.Thread(target=self._run, args=(ready,), daemon=True)
            self.thread.start()
            ready.wait(2)

    def reset(self) -> None:
        """Forget held keys: after lock, sleep or a secure desktop the hook missed key-ups."""
        with self.logic.lock:
            self.logic.reset()

    def stop(self) -> None:
        if self.thread:
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
            self.thread.join(2)
            self.thread = None
            log.info("keyboard hook removed")

    def _run(self, ready: threading.Event) -> None:
        self.thread_id = kernel32.GetCurrentThreadId()
        hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc,
                                        kernel32.GetModuleHandleW(None), 0)
        ready.set()
        if not hook:
            log.error("keyboard hook failed (error %d): push-to-talk and double-tap are off",
                      ctypes.get_last_error())
            self.thread = None
            return
        log.info("keyboard hook installed")
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass  # LL hooks are called from this loop
        user32.UnhookWindowsHookEx(hook)

    def _callback(self, code, wparam, lparam):
        if code == 0:  # HC_ACTION
            k = KBDLLHOOKSTRUCT.from_address(lparam)
            try:
                swallow, signal = self.logic.event(
                    k.vkCode, wparam in (WM_KEYDOWN, WM_SYSKEYDOWN),
                    bool(k.flags & LLKHF_INJECTED), time.monotonic(), k.dwExtraInfo)
            except Exception:  # never let a bug here block the keyboard; no I/O in here
                self.errors += 1
                swallow, signal = False, None
            if signal:
                getattr(self, signal).emit()
            if swallow:
                return 1
        return user32.CallNextHookEx(None, code, wparam, lparam)


class _SessionFilter(QAbstractNativeEventFilter):
    def __init__(self, hwnd: int, on_change):
        super().__init__()
        self.hwnd, self.on_change = hwnd, on_change

    def nativeEventFilter(self, event_type, message):
        msg = wt.MSG.from_address(int(message))
        # power broadcasts reach every top-level window: count only the one we registered
        if msg.hWnd == self.hwnd and (
                (msg.message == WM_WTSSESSION_CHANGE and msg.wParam in SESSION_EVENTS)
                or (msg.message == WM_POWERBROADCAST and msg.wParam in POWER_EVENTS)):
            self.on_change()
        return False, 0


class SessionWatch(QObject):
    """Emits changed on lock, unlock, disconnect, sleep and resume (sent to hwnd)."""

    changed = Signal()

    def __init__(self, hwnd: int):
        super().__init__()
        self.hwnd = hwnd
        self._filter = _SessionFilter(hwnd, self.changed.emit)
        QCoreApplication.instance().installNativeEventFilter(self._filter)
        self.wts = ctypes.WinDLL("wtsapi32", use_last_error=True)
        self.wts.WTSRegisterSessionNotification.argtypes = [wt.HWND, wt.DWORD]
        self.wts.WTSUnRegisterSessionNotification.argtypes = [wt.HWND]
        self.registered = bool(self.wts.WTSRegisterSessionNotification(hwnd, 0))  # this session
        if not self.registered:
            log.warning("no lock/unlock notifications (error %d)", ctypes.get_last_error())

    def stop(self) -> None:
        QCoreApplication.instance().removeNativeEventFilter(self._filter)
        if self.registered:
            self.wts.WTSUnRegisterSessionNotification(self.hwnd)
            self.registered = False
