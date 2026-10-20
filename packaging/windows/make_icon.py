"""Make the Windows icon from the app's SVG (the source of truth): packaging/windows/SpecSurgeon.ico for the EXEs and
the installer, and src/spec_surgeon/web/icons/icon-256.png (the raster copy of the same mark).

    python packaging/windows/make_icon.py            (needs PyMuPDF, a core dependency: pip install -e .)

The SVG is rendered by MuPDF (PyMuPDF opens SVG files as one-page documents). Small sizes are 32-bit DIBs (what
every Windows icon API reads), 256 px is PNG-compressed (Windows Vista and later). Run it again only when icon.svg
changes; both outputs are committed.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SVG = ROOT / "src" / "spec_surgeon" / "web" / "icons" / "icon.svg"
ICO = ROOT / "packaging" / "windows" / "SpecSurgeon.ico"
PNG = ROOT / "src" / "spec_surgeon" / "web" / "icons" / "icon-256.png"
SIZES = (16, 20, 24, 32, 40, 48, 64, 256)


def render(size: int):
    """The SVG as a ``size`` × ``size`` RGBA pixmap (the SVG's own box is 256 × 256, so every size is exact)."""
    import pymupdf
    with pymupdf.open(str(SVG)) as doc:
        page = doc[0]
        box = page.rect
        pix = page.get_pixmap(matrix=pymupdf.Matrix(size / box.width, size / box.height), alpha=True)
    assert (pix.width, pix.height) == (size, size), (pix.width, pix.height, size)
    assert pix.n == 4, pix.n                                   # RGBA
    return pix


def png_bytes(pix) -> bytes:
    return pix.tobytes("png")


def dib_bytes(pix) -> bytes:
    """BITMAPINFOHEADER + bottom-up BGRA rows + an all-zero AND mask (the alpha channel carries transparency)."""
    w, h, stride = pix.width, pix.height, pix.stride
    raw = bytes(pix.samples)
    rows = []
    for y in reversed(range(h)):
        row = bytearray(raw[y * stride: y * stride + w * 4])   # RGBA → BGRA
        row[0::4], row[2::4] = row[2::4], row[0::4]
        rows.append(bytes(row))
    xor = b"".join(rows)
    and_row = ((w + 31) // 32) * 4
    mask = b"\x00" * (and_row * h)
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, len(xor) + len(mask), 0, 0, 0, 0)
    return header + xor + mask


def build_ico(images) -> bytes:
    entries, blobs = [], []
    offset = 6 + 16 * len(images)
    for size, pix in images:
        blob = png_bytes(pix) if size >= 256 else dib_bytes(pix)
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
    images = [(s, render(s)) for s in SIZES]
    ICO.write_bytes(build_ico(images))
    PNG.write_bytes(png_bytes(images[-1][1]))
    print(f"wrote {ICO} ({ICO.stat().st_size} bytes, sizes {SIZES}) and {PNG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
