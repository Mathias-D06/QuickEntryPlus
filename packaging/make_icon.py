"""Write assets/icon.ico with every size drawn natively (Qt's ICO writer stores only one).

ICO layout: a 6-byte header, a 16-byte entry per image, then the images as PNG data.
Run: .venv\\Scripts\\python packaging\\make_icon.py
"""

import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from qeplus.tray import draw  # noqa: E402

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def png(size: int) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    draw(size).save(buf, "PNG")
    return bytes(buf.data())


def main():
    app = QApplication([])  # noqa: F841  (QPixmap needs it)
    images = [png(s) for s in SIZES]
    offset = 6 + 16 * len(SIZES)
    out = struct.pack("<HHH", 0, 1, len(SIZES))
    for size, data in zip(SIZES, images):
        dim = size % 256  # 0 means 256 in an ICO entry
        out += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    path = os.path.join(ROOT, "assets", "icon.ico")
    with open(path, "wb") as f:
        f.write(out + b"".join(images))
    print(f"wrote {path} ({len(SIZES)} sizes)")


if __name__ == "__main__":
    main()
