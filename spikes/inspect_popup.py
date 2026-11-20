"""Step 1 inspector: print every top-level Claude window and its show/hide/foreground/move events.

Lists the Claude windows that already exist (hidden ones too), then watches with SetWinEventHook.
Foreground changes are printed for every app, so we also see what was in front before the pop-up.
Open and close the quick entry pop-up a few times (Ctrl+Alt+Space), move it if it can be moved,
then press Ctrl+C. Output also goes to spikes/out/inspect.log.

Run: .venv\\Scripts\\python spikes\\inspect_popup.py [seconds]
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

EVENTS = {0x0003: "FOREGROUND", 0x8002: "SHOW", 0x8003: "HIDE", 0x800B: "LOCATION",
          0x8000: "CREATE", 0x8001: "DESTROY", 0x0016: "MINIMIZE", 0x0017: "RESTORE"}
WINEVENT_OUTOFCONTEXT, WINEVENT_SKIPOWNPROCESS = 0x0000, 0x0002
GWL_STYLE, GWL_EXSTYLE, GW_OWNER, GA_ROOT = -16, -20, 4, 2
WM_QUIT, PROCESS_QUERY_LIMITED_INFORMATION = 0x0012, 0x1000

WinEventProc = ctypes.WINFUNCTYPE(None, wt.HANDLE, wt.DWORD, wt.HWND, wt.LONG, wt.LONG,
                                  wt.DWORD, wt.DWORD)
user32.SetWinEventHook.restype = wt.HANDLE
user32.SetWinEventHook.argtypes = [wt.DWORD, wt.DWORD, wt.HMODULE, WinEventProc, wt.DWORD,
                                   wt.DWORD, wt.DWORD]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindow.restype = wt.HWND
user32.GetAncestor.restype = wt.HWND
kernel32.OpenProcess.restype = wt.HANDLE

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # redirected output is cp1252
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT_DIR, exist_ok=True)
LOG = open(os.path.join(OUT_DIR, "inspect.log"), "a", encoding="utf-8")
_exe_cache: dict[int, str] = {}
_last_rect: dict[int, tuple] = {}


def out(line: str) -> None:
    line = f"{time.strftime('%H:%M:%S')}.{int(time.time() * 1000) % 1000:03d} {line}"
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


def exe_of(hwnd) -> tuple[int, str]:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value not in _exe_cache:
        name = "?"
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if h:
            buf, size = ctypes.create_unicode_buffer(1024), wt.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                name = buf.value
            kernel32.CloseHandle(h)
        _exe_cache[pid.value] = name
    return pid.value, _exe_cache[pid.value]


def describe(hwnd) -> str:
    cls, title = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    user32.GetWindowTextW(hwnd, title, 256)
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    pid, exe = exe_of(hwnd)
    style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE) & 0xFFFFFFFF
    ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
    owner = user32.GetWindow(hwnd, GW_OWNER) or 0
    return (f"hwnd={hwnd:#x} pid={pid} exe={os.path.basename(exe)} class={cls.value!r} "
            f"title={title.value!r} pos=({r.left},{r.top}) size={r.right - r.left}x"
            f"{r.bottom - r.top} visible={bool(user32.IsWindowVisible(hwnd))} "
            f"style={style:#010x} exstyle={ex:#010x} owner={owner:#x} "
            f"dpi={user32.GetDpiForWindow(hwnd)}")


def is_claude(hwnd) -> bool:
    return "claude" in exe_of(hwnd)[1].lower()


@WinEventProc
def on_event(_hook, event, hwnd, id_object, id_child, _thread, _time):
    if not hwnd or id_object != 0 or id_child != 0 or user32.GetAncestor(hwnd, GA_ROOT) != hwnd:
        return  # only whole top-level windows, not carets/cursors/child controls
    claude = is_claude(hwnd)
    if event == 0x800B:
        if not claude:
            return
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        rect = (r.left, r.top, r.right, r.bottom)
        if _last_rect.get(hwnd) == rect:
            return
        _last_rect[hwnd] = rect
    if claude or event == 0x0003:
        out(f"{EVENTS[event]:<10} {'CLAUDE ' if claude else ''}{describe(hwnd)}")


def main() -> None:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2: physical pixels
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 0

    out("existing Claude top-level windows:")
    found = []
    enum_proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(
        lambda h, _: (found.append(h) if is_claude(h) else None, True)[1])
    user32.EnumWindows(enum_proc, 0)
    for h in found:
        out("  " + describe(h))
    out(f"foreground now: {describe(user32.GetForegroundWindow())}")

    hooks = [user32.SetWinEventHook(lo, hi, None, on_event, 0, 0, WINEVENT_OUTOFCONTEXT |
                                    WINEVENT_SKIPOWNPROCESS)
             for lo, hi in ((0x0003, 0x0003), (0x0016, 0x0017), (0x8000, 0x8003),
                            (0x800B, 0x800B))]
    assert all(hooks), f"SetWinEventHook failed: {ctypes.get_last_error()}"

    tid = kernel32.GetCurrentThreadId()
    handler = ctypes.WINFUNCTYPE(wt.BOOL, wt.DWORD)(
        lambda _: bool(user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)))
    kernel32.SetConsoleCtrlHandler(handler, True)  # Ctrl+C ends GetMessage cleanly
    if seconds:
        user32.SetTimer(None, 0, int(seconds * 1000), None)  # WM_TIMER with no proc: see below
    out("watching... open/close the pop-up, then Ctrl+C")

    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == 0x0113:  # WM_TIMER from the duration timer
            break
        user32.DispatchMessageW(ctypes.byref(msg))
    for h in hooks:
        user32.UnhookWinEvent(h)
    out("done")


if __name__ == "__main__":
    main()
