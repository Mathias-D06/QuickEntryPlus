"""Step 2 probe, part 2: which clipboard format (if any) does the pop-up accept as an image?

V1: "PNG" + CF_DIB set with win32clipboard (what browsers write).
V2: CF_HDROP pointing at a PNG file (what Explorer's Copy writes).
For each: open pop-up, Ctrl+V, wait 2.5 s, grab -> paste-V1.png / paste-V2.png, close.
Run: .venv\\Scripts\\python spikes\\paste_probe2.py
"""

from __future__ import annotations

import os
import struct
import sys

import win32clipboard as cb
from PySide6.QtCore import QBuffer, QIODevice

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paste_probe import (OUT_DIR, VK_CONTROL, close_popup, find_windows, grab,  # noqa: E402
                         is_popup, keys, open_popup, pump, user32)
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter  # noqa: E402


def test_image(label):
    img = QImage(480, 160, QImage.Format.Format_RGB32)
    img.fill(QColor("#d97757"))
    p = QPainter(img)
    f = QFont("Segoe UI Variable Text")
    f.setPixelSize(40)
    p.setFont(f)
    p.setPen(QColor("white"))
    p.drawText(img.rect(), Qt.AlignmentFlag.AlignCenter, label)
    p.end()
    return img


def encode(img, fmt):
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, fmt)
    return bytes(buf.data())


def set_png_and_dib(img):
    bmp = encode(img, "BMP")
    cb.OpenClipboard()
    cb.EmptyClipboard()
    cb.SetClipboardData(cb.RegisterClipboardFormat("PNG"), encode(img, "PNG"))
    cb.SetClipboardData(cb.CF_DIB, bmp[14:])  # a DIB is a BMP file without its 14-byte header
    cb.CloseClipboard()


def set_file(path):
    # DROPFILES header (20 bytes, wide chars) followed by a double-NUL-terminated path list
    data = struct.pack("<IiiII", 20, 0, 0, 0, 1) + (path + "\0\0").encode("utf-16-le")
    cb.OpenClipboard()
    cb.EmptyClipboard()
    cb.SetClipboardData(cb.CF_HDROP, data)
    cb.CloseClipboard()


def attempt(popup, name):
    pump(0.3)
    open_popup(popup)
    keys(VK_CONTROL, 0x56)
    pump(2.5)
    grab(popup, name)
    if user32.GetForegroundWindow() == popup:
        close_popup(popup)


def main():
    popup = find_windows(is_popup)[0]
    set_png_and_dib(test_image("V1 PNG + DIB"))
    attempt(popup, "paste-V1")
    path = os.path.join(OUT_DIR, "paste-test.png")
    test_image("V2 file").save(path)
    set_file(path)
    attempt(popup, "paste-V2")


if __name__ == "__main__":
    main()
