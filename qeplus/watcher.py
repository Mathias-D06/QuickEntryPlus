"""Watches Claude's quick entry pop-up with SetWinEventHook (no polling) and emits Qt signals."""

from __future__ import annotations

import ctypes
from ctypes import wintypes as wt

from PySide6.QtCore import QObject, Signal

from .win import exe_name, is_popup, text_of, user32

EVENT_SYSTEM_FOREGROUND, EVENT_OBJECT_SHOW, EVENT_OBJECT_HIDE = 0x0003, 0x8002, 0x8003
EVENT_OBJECT_DESTROY = 0x8001
EVENT_OBJECT_LOCATIONCHANGE, WINEVENT_SKIPOWNPROCESS, GA_ROOT = 0x800B, 0x2, 2

WinEventProc = ctypes.WINFUNCTYPE(None, wt.HANDLE, wt.DWORD, wt.HWND, wt.LONG, wt.LONG,
                                  wt.DWORD, wt.DWORD)
user32.SetWinEventHook.restype = wt.HANDLE
user32.SetWinEventHook.argtypes = [wt.DWORD, wt.DWORD, wt.HMODULE, WinEventProc, wt.DWORD,
                                   wt.DWORD, wt.DWORD]


class PopupWatcher(QObject):
    shown = Signal()
    moved = Signal()
    hidden = Signal()

    def __init__(self):
        super().__init__()
        self.popup = 0               # the pop-up's hwnd once seen (Claude reuses one window)
        self.last_foreground = 0     # last app window in front (never the pop-up or our own)
        self.before_popup = 0        # last_foreground as it was when the pop-up opened
        self._proc = WinEventProc(self._on_event)  # keep a reference: Windows calls into it
        # out-of-context hooks are delivered through this thread's message loop, i.e. Qt's
        self._hooks = [user32.SetWinEventHook(lo, hi, None, self._proc, 0, 0,
                                              WINEVENT_SKIPOWNPROCESS)
                       for lo, hi in ((EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND),
                                      (EVENT_OBJECT_DESTROY, EVENT_OBJECT_HIDE),
                                      (EVENT_OBJECT_LOCATIONCHANGE, EVENT_OBJECT_LOCATIONCHANGE))]
        if not all(self._hooks):
            raise OSError(f"SetWinEventHook failed: {ctypes.get_last_error()}")
        fg = user32.GetForegroundWindow()
        if fg and is_app_window(fg) and not is_popup(fg):
            self.last_foreground = fg  # so the first pop-up after start-up has a target too

    def visible(self) -> bool:
        return bool(self.popup and user32.IsWindowVisible(self.popup))

    def focused(self) -> bool:
        # visible too: a hidden pop-up can stay the foreground window, and keys typed into it
        # are lost when Claude shows it again
        return self.visible() and user32.GetForegroundWindow() == self.popup

    def stop(self) -> None:
        for h in self._hooks:
            user32.UnhookWinEvent(h)
        self._hooks = []

    def _on_event(self, _hook, event, hwnd, id_object, id_child, _thread, _time):
        if not hwnd or id_object != 0 or id_child != 0:
            return  # only whole windows, not carets, cursors or child objects
        if event == EVENT_OBJECT_LOCATIONCHANGE:
            if hwnd == self.popup and user32.IsWindowVisible(hwnd):
                self.moved.emit()
        elif event == EVENT_OBJECT_HIDE:
            if hwnd == self.popup:
                self.hidden.emit()
        elif event == EVENT_OBJECT_DESTROY:
            if hwnd == self.popup:  # Claude quit or crashed: no HIDE event comes
                self.popup = 0
                self.hidden.emit()
        elif event == EVENT_OBJECT_SHOW:
            # always re-check: a handle can be reused by another app once Claude restarts
            if user32.GetAncestor(hwnd, GA_ROOT) == hwnd and is_popup(hwnd):
                self.popup = hwnd
                # SHOW arrives before the pop-up's own FOREGROUND event, so this is still
                # the window the user was in when they opened it
                self.before_popup = self.last_foreground
                self.shown.emit()
        elif event == EVENT_SYSTEM_FOREGROUND and hwnd != self.popup and is_app_window(hwnd):
            self.last_foreground = hwnd


SHELL_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW"}
SHELL_EXES = {"searchhost.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe",
              "explorer.exe"}


def is_app_window(hwnd) -> bool:
    """A window worth attaching: not the taskbar, desktop, Start or Search."""
    if text_of(hwnd, user32.GetClassNameW) in SHELL_CLASSES:
        return False
    exe = exe_name(hwnd)
    # explorer.exe is the shell, but its File Explorer windows are real windows
    return exe != "" and (exe not in SHELL_EXES
                          or text_of(hwnd, user32.GetClassNameW) == "CabinetWClass")

