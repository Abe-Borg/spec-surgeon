"""Make the Windows icon from the app's SVG (the source of truth): packaging/windows/SpecSurgeon.ico for the EXEs and
the installer, and src/spec_surgeon/ui/resources/icon-256.png (the window's fallback when SVG icons are missing).

    python packaging/windows/make_icon.py            (needs PySide6: pip install -e ".[gui]")

Small sizes are 32-bit DIBs (what every Windows icon API reads), 256 px is PNG-compressed (Windows Vista and later).
Run it again only when icon.svg changes; both outputs are committed.
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SVG = ROOT / "src" / "spec_surgeon" / "ui" / "resources" / "icon.svg"
ICO = ROOT / "packaging" / "windows" / "SpecSurgeon.ico"
PNG = ROOT / "src" / "spec_surgeon" / "ui" / "resources" / "icon-256.png"
SIZES = (16, 20, 24, 32, 40, 48, 64, 256)


def render(size: int):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    QSvgRenderer(str(SVG)).render(p)
    p.end()
    return img


def png_bytes(img) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(data)


def dib_bytes(img) -> bytes:
    """BITMAPINFOHEADER + bottom-up BGRA rows + an all-zero AND mask (the alpha channel carries transparency)."""
    w, h = img.width(), img.height()
    raw = bytes(img.constBits())[: img.sizeInBytes()]
    stride = img.bytesPerLine()
    xor = b"".join(raw[y * stride: y * stride + w * 4] for y in reversed(range(h)))   # ARGB32 = B,G,R,A in memory
    and_row = ((w + 31) // 32) * 4
    mask = b"\x00" * (and_row * h)
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, len(xor) + len(mask), 0, 0, 0, 0)
    return header + xor + mask


def build_ico(images) -> bytes:
    entries, blobs = [], []
    offset = 6 + 16 * len(images)
    for size, img in images:
        blob = png_bytes(img) if size >= 256 else dib_bytes(img)
        dim = 0 if size >= 256 else size
        entries.append(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset))
        blobs.append(blob)
        offset += len(blob)
    return struct.pack("<HHH", 0, 1, len(images)) + b"".join(entries) + b"".join(blobs)


def read_ico_sizes(data: bytes):
    """[(width, height, bytes)] from an .ico — used by tests/test_packaging.py."""
    _, kind, count = struct.unpack_from("<HHH", data, 0)
    assert kind == 1, "not an icon file"
    out = []
    for i in range(count):
        w, h, _, _, _, _, size, off = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        assert off + size <= len(data), "icon entry runs past the end of the file"
        out.append((w or 256, h or 256, size))
    return out


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])  # noqa: F841 — QImage text/SVG need an app
    images = [(s, render(s)) for s in SIZES]
    ICO.write_bytes(build_ico(images))
    PNG.write_bytes(png_bytes(images[-1][1]))
    print(f"wrote {ICO} ({ICO.stat().st_size} bytes, sizes {SIZES}) and {PNG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
