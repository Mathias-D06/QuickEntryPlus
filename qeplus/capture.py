"""Window and screen capture with GDI, in physical pixels.

window_image() asks the window to draw itself (PrintWindow + PW_RENDERFULLCONTENT, which works
for GPU-rendered apps and windows behind others), cropped to the visible frame DWM reports, so
the invisible resize border and a maximised window's overhang are left out.
screen_image() copies what is on screen, for windows that draw nothing for PrintWindow.
"""

from __future__ import annotations

import ctypes
from collections import Counter
from ctypes import wintypes as wt

from PySide6.QtGui import QImage

from .win import user32

gdi32 = ctypes.WinDLL("gdi32")
dwmapi = ctypes.WinDLL("dwmapi")
for name, res in (("CreateCompatibleDC", wt.HDC), ("CreateDIBSection", wt.HBITMAP),
                  ("SelectObject", wt.HGDIOBJ)):
    getattr(gdi32, name).restype = res
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HDC,
                         ctypes.c_int, ctypes.c_int, wt.DWORD]
user32.GetDC.restype = wt.HDC
user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]

PW_RENDERFULLCONTENT, SRCCOPY, CAPTUREBLT = 0x2, 0x00CC0020, 0x40000000
DWMWA_EXTENDED_FRAME_BOUNDS = 9


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


def _draw(width: int, height: int, paint) -> QImage | None:
    """Run paint(memory_dc) on a 32-bit top-down bitmap and return it as a QImage."""
    header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0)
    bits = ctypes.c_void_p()
    screen = user32.GetDC(None)
    dc = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateDIBSection(dc, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
    try:
        if not bmp:
            return None
        old = gdi32.SelectObject(dc, bmp)
        ok = paint(dc, screen)
        gdi32.SelectObject(dc, old)
        if not ok:
            return None
        data = ctypes.string_at(bits, width * height * 4)
        # RGB32 ignores the alpha byte, which PrintWindow often leaves at 0
        return QImage(data, width, height, width * 4, QImage.Format.Format_RGB32).copy()
    finally:
        if bmp:
            gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(dc)
        user32.ReleaseDC(None, screen)


def frame_rect(hwnd) -> tuple[int, int, int, int]:
    """Visible bounds (left, top, right, bottom); falls back to GetWindowRect."""
    r = wt.RECT()
    if dwmapi.DwmGetWindowAttribute(wt.HWND(hwnd), DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r),
                                    ctypes.sizeof(r)) != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def is_blank(image: QImage) -> bool:
    """True if a 16x16 sample grid is all one colour: what a failed PrintWindow gives.
    Deliberately strict: a mostly empty window (a blank editor) is real content, and
    treating it as blank would send it to the screen-grab fallback."""
    w, h = image.width(), image.height()
    samples = Counter(image.pixel(x * (w - 1) // 15, y * (h - 1) // 15)
                      for x in range(16) for y in range(16))
    return len(samples) == 1


def window_image(hwnd) -> QImage | None:
    """The window drawn by itself, cropped to its visible frame; None if it drew nothing."""
    wr = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(wr))
    w, h = wr.right - wr.left, wr.bottom - wr.top
    if w <= 0 or h <= 0:
        return None
    image = _draw(w, h, lambda dc, _s: user32.PrintWindow(hwnd, dc, PW_RENDERFULLCONTENT))
    if image is None:
        return None
    left, top, right, bottom = frame_rect(hwnd)
    image = image.copy(left - wr.left, top - wr.top, right - left, bottom - top)
    return None if image.isNull() or is_blank(image) else image


def screen_image(left: int, top: int, right: int, bottom: int) -> QImage | None:
    """What is on screen in that rectangle (physical px, virtual-desktop coordinates)."""
    w, h = right - left, bottom - top
    if w <= 0 or h <= 0:
        return None
    return _draw(w, h, lambda dc, screen: gdi32.BitBlt(dc, 0, 0, w, h, screen, left, top,
                                                       SRCCOPY | CAPTUREBLT))
