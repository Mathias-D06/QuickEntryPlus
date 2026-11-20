"""Win32 helpers: finding Claude's quick entry pop-up and sending keys with SendInput."""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes as wt
from dataclasses import dataclass

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
# argtypes for every call that takes a handle: without them ctypes passes a Python int as a
# 32-bit C int, and a handle value above 0x7FFFFFFF raises OverflowError
for _name, _args, _res in (
        ("GetWindowLongPtrW", [wt.HWND, ctypes.c_int], ctypes.c_ssize_t),
        ("GetForegroundWindow", [], wt.HWND),
        ("GetAncestor", [wt.HWND, wt.UINT], wt.HWND),
        ("GetClassNameW", [wt.HWND, wt.LPWSTR, ctypes.c_int], ctypes.c_int),
        ("GetWindowTextW", [wt.HWND, wt.LPWSTR, ctypes.c_int], ctypes.c_int),
        ("GetWindowThreadProcessId", [wt.HWND, ctypes.POINTER(wt.DWORD)], wt.DWORD),
        ("IsWindow", [wt.HWND], wt.BOOL), ("IsWindowVisible", [wt.HWND], wt.BOOL),
        ("IsIconic", [wt.HWND], wt.BOOL),
        ("GetWindowRect", [wt.HWND, ctypes.POINTER(wt.RECT)], wt.BOOL),
        ("GetDpiForWindow", [wt.HWND], wt.UINT),
        ("MonitorFromWindow", [wt.HWND, wt.DWORD], wt.HANDLE),
        ("GetMonitorInfoW", [wt.HANDLE, ctypes.c_void_p], wt.BOOL),
        ("WindowFromPoint", [wt.POINT], wt.HWND),
        ("UnhookWinEvent", [wt.HANDLE], wt.BOOL),
        ("ShowWindow", [wt.HWND, ctypes.c_int], wt.BOOL),
        ("SetForegroundWindow", [wt.HWND], wt.BOOL),
        ("GetClipboardSequenceNumber", [], wt.DWORD),
        ("GetWindow", [wt.HWND, wt.UINT], wt.HWND),
        ("GetWindowPlacement", [wt.HWND, ctypes.c_void_p], wt.BOOL),
        ("GetAsyncKeyState", [ctypes.c_int], ctypes.c_short),
        ("RegisterClipboardFormatW", [wt.LPCWSTR], wt.UINT),
        ("IsClipboardFormatAvailable", [wt.UINT], wt.BOOL),
        ("OpenClipboard", [wt.HWND], wt.BOOL), ("CloseClipboard", [], wt.BOOL),
        ("EmptyClipboard", [], wt.BOOL), ("EnumClipboardFormats", [wt.UINT], wt.UINT),
        ("GetClipboardData", [wt.UINT], wt.HANDLE),
        ("SetClipboardData", [wt.UINT, wt.HANDLE], wt.HANDLE),
        ("OpenInputDesktop", [wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
        ("CloseDesktop", [wt.HANDLE], wt.BOOL)):
    _f = getattr(user32, _name)
    _f.argtypes, _f.restype = _args, _res
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR,
                                                ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
for _name, _args, _res in (("GlobalAlloc", [wt.UINT, ctypes.c_size_t], wt.HANDLE),
                           ("GlobalLock", [wt.HANDLE], ctypes.c_void_p),
                           ("GlobalUnlock", [wt.HANDLE], wt.BOOL),
                           ("GlobalSize", [wt.HANDLE], ctypes.c_size_t),
                           ("GlobalFree", [wt.HANDLE], wt.HANDLE)):
    _f = getattr(kernel32, _name)
    _f.argtypes, _f.restype = _args, _res

GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_CAPTION, WS_EX_TOPMOST, WS_EX_NOACTIVATE = 0xC00000, 0x8, 0x08000000
VK_BACK, VK_CONTROL, VK_MENU, VK_SPACE, VK_ESCAPE, VK_LWIN = 0x08, 0x11, 0x12, 0x20, 0x1B, 0x5B
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x2, 0x4
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
# marks this app's own SendInput events, so its keyboard hook can always tell them apart
QEPLUS_INPUT_TAG = 0x51455053  # "QEPS"


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):  # only here so the union has its real (largest) size
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


def _send(events: list[tuple[int, int, int]]) -> bool:
    """events: (vk, scan, flags). Returns True if Windows accepted every event."""
    arr = (INPUT * len(events))(*[INPUT(type=1, ki=KEYBDINPUT(vk, scan, flags, 0,
                                                              QEPLUS_INPUT_TAG))
                                  for vk, scan, flags in events])
    return user32.SendInput(len(events), arr, ctypes.sizeof(INPUT)) == len(events)


def press(*vks: int) -> bool:
    """Press keys in order and release them in reverse, e.g. press(VK_CONTROL, ord('V'))."""
    return _send([(vk, 0, 0) for vk in vks] + [(vk, 0, KEYEVENTF_KEYUP) for vk in reversed(vks)])


def type_text(text: str) -> bool:
    events = []
    for unit in text.encode("utf-16-le").hex(" ", 2).split():  # UTF-16 units: emoji are 2
        code = int(unit[2:] + unit[:2], 16)
        events += [(0, code, KEYEVENTF_UNICODE), (0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)]
    return _send(events)


def exe_name(hwnd) -> str:
    return os.path.basename(exe_path(hwnd)).lower()


def exe_path(hwnd) -> str:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return ""
    buf, size = ctypes.create_unicode_buffer(1024), wt.DWORD(1024)
    ok = kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
    kernel32.CloseHandle(h)
    return buf.value if ok else ""


def input_desktop_ok() -> bool:
    """False while the lock screen or a UAC prompt (the secure desktop) has the keyboard."""
    desk = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
    if not desk:
        return False
    user32.CloseDesktop(desk)
    return True


def is_elevated(hwnd) -> bool:
    """True if UIPI shields hwnd from this (medium-integrity) app: its process is elevated, has
    UIAccess (On-Screen Keyboard, Narrator), runs above medium integrity, or its token can't be
    read. Our keyboard hook doesn't see keys typed into such windows, nor can we send them."""
    import pywintypes
    import win32api
    import win32security

    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return False  # no foreground window (focus is moving): nothing to judge
    proc = token = None
    try:
        proc = win32api.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        token = win32security.OpenProcessToken(proc, win32security.TOKEN_QUERY)
        info = win32security.GetTokenInformation
        sid, _ = info(token, win32security.TokenIntegrityLevel)
        level = sid.GetSubAuthority(sid.GetSubAuthorityCount() - 1)
        return (bool(info(token, win32security.TokenElevation))
                or bool(info(token, win32security.TokenUIAccess))
                or level > 0x2000)  # SECURITY_MANDATORY_MEDIUM_RID
    except pywintypes.error:
        return True
    finally:
        for handle in (token, proc):
            if handle is not None:
                win32api.CloseHandle(handle)


def text_of(hwnd, getter) -> str:
    buf = ctypes.create_unicode_buffer(256)
    getter(hwnd, buf, 256)
    return buf.value


def is_popup(hwnd) -> bool:
    """Claude's quick entry pop-up: claude.exe, Chrome_WidgetWin_1, title "Claude", topmost,
    no caption, and not no-activate (a small Claude notification window shares the rest)."""
    style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
    ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    return (bool(ex & WS_EX_TOPMOST) and not ex & WS_EX_NOACTIVATE and not style & WS_CAPTION
            and text_of(hwnd, user32.GetClassNameW) == "Chrome_WidgetWin_1"
            and text_of(hwnd, user32.GetWindowTextW) == "Claude" and exe_name(hwnd) == "claude.exe")


def is_main_window(hwnd) -> bool:
    return (bool(user32.GetWindowLongPtrW(hwnd, GWL_STYLE) & WS_CAPTION)
            and text_of(hwnd, user32.GetClassNameW) == "Chrome_WidgetWin_1"
            and exe_name(hwnd) == "claude.exe")


def find_windows(pred) -> list[int]:
    found = []
    cb = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(
        lambda h, _: (found.append(h) if pred(h) else None, True)[1])
    user32.EnumWindows(cb, 0)
    return found


def rect(hwnd) -> tuple[int, int, int, int]:
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT),
                ("dwFlags", wt.DWORD)]


def monitor_right(hwnd) -> int:
    """Right edge (physical px) of the monitor showing hwnd."""
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(info))  # NEAREST
    return info.rcMonitor.right


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", wt.UINT), ("flags", wt.UINT), ("showCmd", wt.UINT),
                ("ptMinPosition", wt.POINT), ("ptMaxPosition", wt.POINT),
                ("rcNormalPosition", wt.RECT)]


def normal_area(hwnd) -> int:
    """Window area in px, using its restored size when minimised (a minimised window's own
    rect is a tiny off-screen stub)."""
    if user32.IsIconic(hwnd):
        wp = WINDOWPLACEMENT(length=ctypes.sizeof(WINDOWPLACEMENT))
        if user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
            r = wp.rcNormalPosition
            return (r.right - r.left) * (r.bottom - r.top)
    left, top, right, bottom = rect(hwnd)
    return (right - left) * (bottom - top)


# --- clipboard: privacy formats, and a byte-exact snapshot/restore --------------------------

PRIVATE_NAMES = ("ExcludeClipboardContentFromMonitorProcessing", "CanIncludeInClipboardHistory",
                 "CanUploadToCloudClipboard")
# GDI-handle formats (not HGLOBAL memory): CF_BITMAP, METAFILEPICT, PALETTE, ENHMETAFILE, the
# display formats, and the private GDI range. Their content is also in CF_DIB/DIBV5 where it
# matters, so a snapshot skips them.
GDI_FORMATS = {2, 3, 9, 14, 0x80, 0x82, 0x83, 0x8E}
# formats whose presence is the message: kept even when they carry no readable data
MARKER_NAMES = ("ExcludeClipboardContentFromMonitorProcessing", "Clipboard Viewer Ignore")
SNAPSHOT_LIMIT = 64 * 1024 * 1024
SNAPSHOT_SECONDS = 0.3  # an app rendering lots of formats lazily: give up, don't freeze
GMEM_MOVEABLE_ZEROINIT = 0x0042


@dataclass
class ClipboardSnapshot:
    items: list[tuple[int, bytes | None]]  # None: a marker format with no data
    private: bool                          # the original asked monitors to leave it alone


def _open_clipboard(hwnd=None, tries: int = 10) -> bool:
    for _ in range(tries):  # another app may hold it for a moment
        if user32.OpenClipboard(hwnd):
            return True
        kernel32.Sleep(20)
    return False


def clipboard_is_private() -> bool:
    """True if the clipboard's owner asked monitors to leave it alone, as password managers do:
    ExcludeClipboardContentFromMonitorProcessing or "Clipboard Viewer Ignore" present, or
    CanIncludeInClipboardHistory / CanUploadToCloudClipboard set to 0."""
    def fmt(name):
        return user32.RegisterClipboardFormatW(name)

    if any(user32.IsClipboardFormatAvailable(fmt(n)) for n in
           ("ExcludeClipboardContentFromMonitorProcessing", "Clipboard Viewer Ignore")):
        return True
    flags = [fmt(n) for n in PRIVATE_NAMES[1:] if user32.IsClipboardFormatAvailable(fmt(n))]
    if not flags:
        return False
    if not _open_clipboard():
        return True  # can't check: assume private rather than risk it
    try:
        for f in flags:
            h = user32.GetClipboardData(f)
            if not h or kernel32.GlobalSize(h) < 4:
                return True  # present but unreadable: assume private rather than risk it
            p = kernel32.GlobalLock(h)
            if not p:
                return True
            value = ctypes.c_uint32.from_address(p).value
            kernel32.GlobalUnlock(h)
            if value == 0:
                return True
        return False
    finally:
        user32.CloseClipboard()


def snapshot_clipboard() -> ClipboardSnapshot | None:
    """Every memory-based clipboard format as raw bytes (files, Preferred DropEffect, HTML, RTF,
    images, private app formats) plus the privacy markers, or None if it can't be read, is over
    64 MB or takes over 0.3 s. Reading makes apps that render lazily render now: unavoidable if
    the clipboard is to be put back."""
    private = clipboard_is_private()
    if not _open_clipboard():
        return None
    markers = {user32.RegisterClipboardFormatW(n) for n in MARKER_NAMES}
    items, total, fmt = [], 0, 0
    deadline = time.monotonic() + SNAPSHOT_SECONDS
    try:
        while fmt := user32.EnumClipboardFormats(fmt):
            if time.monotonic() > deadline:
                return None
            if fmt in GDI_FORMATS or 0x200 <= fmt <= 0x3FF:
                continue
            h = user32.GetClipboardData(fmt)
            p = kernel32.GlobalLock(h) if h else None
            if not p:
                if fmt in markers:
                    items.append((fmt, None))
                continue
            try:
                size = kernel32.GlobalSize(h)
                total += size
                if total > SNAPSHOT_LIMIT:
                    return None
                items.append((fmt, ctypes.string_at(p, size)))
            finally:
                kernel32.GlobalUnlock(h)
        return ClipboardSnapshot(items, private)
    finally:
        user32.CloseClipboard()


def restore_clipboard(snap: ClipboardSnapshot, hwnd=None) -> bool:
    """Put a snapshot back; False if any format couldn't be. An empty clipboard stays empty.
    Otherwise the history flags are set to 0, so the restored copy doesn't become a second
    Win+V or Cloud Clipboard entry; the monitor-exclusion marker comes back only if the original
    had it (third-party clipboard managers and RDP sync keep working)."""
    if not _open_clipboard(hwnd):
        return False
    try:
        if not user32.EmptyClipboard():
            return False
        if not snap.items:
            return True
        history = {user32.RegisterClipboardFormatW(n) for n in PRIVATE_NAMES[1:]}
        items = [(f, d) for f, d in snap.items if f not in history]
        items += [(f, b"\0\0\0\0") for f in history]
        ok = True
        for f, data in items:
            data = b"\0\0\0\0" if data is None else data  # a marker: only its presence counts
            mem = kernel32.GlobalAlloc(GMEM_MOVEABLE_ZEROINIT, max(len(data), 1))
            p = kernel32.GlobalLock(mem) if mem else None
            if not p:
                if mem:
                    kernel32.GlobalFree(mem)
                ok = False
                continue
            ctypes.memmove(p, data, len(data))
            kernel32.GlobalUnlock(mem)
            if not user32.SetClipboardData(f, mem):
                kernel32.GlobalFree(mem)  # still ours if SetClipboardData failed
                ok = False
        return ok
    finally:
        user32.CloseClipboard()
