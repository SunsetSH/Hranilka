"""Рендер assets/icon.svg -> PNG (набор размеров) + мультиразмерный icon.ico."""
import os
import struct
from PySide6.QtCore import Qt, QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

HERE = os.path.dirname(os.path.abspath(__file__))
SVG = os.path.join(HERE, "icon.svg")
PNG_SIZES = [16, 32, 48, 64, 128, 256, 512]
ICO_SIZES = [16, 32, 48, 64, 128, 256]


def render(size: int) -> QImage:
    r = QSvgRenderer(QByteArray(open(SVG, "rb").read()))
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    r.render(p)
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def write_ico(path, entries):
    # entries: list of (size, png_bytes) — PNG-сжатые записи (Vista+)
    n = len(entries)
    header = struct.pack("<HHH", 0, 1, n)
    offset = 6 + n * 16
    dir_entries, data = b"", b""
    for size, data_bytes in entries:
        w = h = 0 if size >= 256 else size
        dir_entries += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32,
                                    len(data_bytes), offset)
        data += data_bytes
        offset += len(data_bytes)
    with open(path, "wb") as f:
        f.write(header + dir_entries + data)


def main():
    QGuiApplication([])
    for s in PNG_SIZES:
        render(s).save(os.path.join(HERE, f"icon_{s}.png"))
    render(512).save(os.path.join(HERE, "icon.png"))
    entries = [(s, png_bytes(render(s))) for s in ICO_SIZES]
    write_ico(os.path.join(HERE, "icon.ico"), entries)
    print("done:", [s for s in PNG_SIZES], "+ icon.ico")


if __name__ == "__main__":
    main()
