"""Picture files for swap_image (design note §8) — the transform side's reader. The verifier has its own
(verify/images.py); a test compares the two on crafted and real files.

Definition (CLAUDE.md "Pictures", both sides): a picture file is PNG (signature, then IHDR: width and height big-endian
at bytes 16–24), GIF (``GIF87a``/``GIF89a``: the logical screen's width and height little-endian at bytes 6–10) or JPEG
(``FF D8``; markers walked from byte 2 — fill bytes ``FF`` skipped, ``D0``–``D8`` and ``01`` stand alone, every other
marker carries a big-endian length — up to the first SOF marker ``C0``–``CF`` other than ``C4``, ``C8``, ``CC``: height
and width big-endian at its bytes 1–5; an ``APP1`` segment starting ``Exif\\0\\0`` before it gives the Exif orientation,
tag 0x0112 of IFD0). Anything else — SVG included — is refused, and so are a width or height of 0 and a JPEG whose Exif
orientation (5–8) turns it a quarter turn (its stored width and height are not what a viewer shows). The proportions are
the stored pixel width and height.

The **fitted size** of a picture of ``pw`` × ``ph`` pixels in a box of ``W`` × ``H`` EMU: ``stretch`` → the box;
``contain`` → when ``W·ph ≤ H·pw`` the width is ``W`` and the height ``round(W·ph/pw)``, else the height is ``H`` and the
width ``round(H·pw/ph)`` (integers, halves rounded up); a side that rounds to 0 is refused.
"""
from __future__ import annotations

import hashlib
import struct
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .base import JobException

CONTENT_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "gif": "image/gif"}
FITS = ("contain", "stretch")
QUARTER_TURNS = {5, 6, 7, 8}


def _exif_orientation(seg: bytes) -> Optional[int]:
    """The Exif orientation of an APP1 segment's data (starting ``Exif\\0\\0``), or None."""
    tiff = seg[6:]
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return None
    e = "<" if tiff[:2] == b"II" else ">"
    if struct.unpack(e + "H", tiff[2:4])[0] != 42:
        return None
    ifd = struct.unpack(e + "I", tiff[4:8])[0]
    if ifd + 2 > len(tiff):
        return None
    n = struct.unpack(e + "H", tiff[ifd:ifd + 2])[0]
    for k in range(n):
        at = ifd + 2 + 12 * k
        if at + 12 > len(tiff):
            return None
        tag, typ, count = struct.unpack(e + "HHI", tiff[at:at + 8])
        if tag == 0x0112:
            if typ != 3 or count != 1:
                return None
            return struct.unpack(e + "H", tiff[at + 8:at + 10])[0]
    return None


def _jpeg(data: bytes) -> Tuple[int, int, Optional[int]]:
    i, n, orientation = 2, len(data), None
    while i < n:
        if data[i] != 0xFF:
            raise ValueError(f"no JPEG marker at byte {i}")
        while i < n and data[i] == 0xFF:
            i += 1
        if i >= n:
            break
        m = data[i]
        i += 1
        if m == 0x01 or 0xD0 <= m <= 0xD8:
            continue
        if m in (0xD9, 0xDA):
            raise ValueError("the JPEG ends (or its picture data starts) before its frame size")
        if i + 2 > n:
            break
        length = struct.unpack(">H", data[i:i + 2])[0]
        if length < 2 or i + length > n:
            raise ValueError(f"a JPEG segment runs past the end of the file (byte {i})")
        seg = data[i + 2:i + length]
        if m == 0xE1 and seg[:6] == b"Exif\x00\x00" and orientation is None:
            orientation = _exif_orientation(seg)
        if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
            if len(seg) < 5:
                raise ValueError("the JPEG frame header is cut short")
            h, w = struct.unpack(">HH", seg[1:5])
            return w, h, orientation
        i += length
    raise ValueError("no JPEG frame header")


def sniff(data: bytes) -> Tuple[str, int, int]:
    """(format, width, height) of a picture file — format ``png`` | ``jpeg`` | ``gif`` — or ValueError saying why
    it cannot be used."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        if len(data) < 24 or data[12:16] != b"IHDR":
            raise ValueError("the PNG has no IHDR header")
        fmt, (w, h) = "png", struct.unpack(">II", data[16:24])
    elif data[:6] in (b"GIF87a", b"GIF89a"):
        if len(data) < 10:
            raise ValueError("the GIF is cut short")
        fmt, (w, h) = "gif", struct.unpack("<HH", data[6:10])
    elif data[:2] == b"\xff\xd8":
        w, h, orientation = _jpeg(data)
        if orientation in QUARTER_TURNS:
            raise ValueError(f"its Exif orientation ({orientation}) turns it a quarter turn — save it upright first")
        fmt = "jpeg"
    else:
        head = data[:256].lstrip().lower()
        if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:2048].lower()):
            raise ValueError("an SVG picture needs the PNG copy Word makes itself — insert it once in Word, or use "
                             "a PNG")
        raise ValueError("it is not a PNG, JPEG or GIF picture")
    if w <= 0 or h <= 0:
        raise ValueError(f"its size is {w} × {h} pixels")
    return fmt, w, h


def load_image(spec: Any, what: str) -> Tuple[Path, bytes, str, str, int, int]:
    """(path, bytes, md5, format, width, height) of the picture file ``spec`` (a path) — refused when missing or not
    a usable picture."""
    if not isinstance(spec, str) or not spec.strip():
        raise JobException(f"{what}: give the picture as a path to a .png, .jpg or .gif file", "bad-params")
    path = Path(spec)
    try:
        data = path.read_bytes()
    except OSError as e:
        raise JobException(f"{what}: the picture {path} cannot be read ({e})", "not-found", {"file": str(path)})
    try:
        fmt, w, h = sniff(data)
    except (ValueError, struct.error) as e:
        raise JobException(f"{what}: {path.name} cannot be used: {e}", "bad-params", {"file": str(path)})
    return path, data, hashlib.md5(data).hexdigest(), fmt, w, h


def file_md5(path: str, what: str) -> str:
    try:
        return hashlib.md5(Path(path).read_bytes()).hexdigest()
    except OSError as e:
        raise JobException(f"{what}: the file {path} cannot be read ({e})", "not-found", {"file": path})


def check_unchanged(path: str, md5: str, what: str) -> bytes:
    """The file's bytes, refused when it changed since the plan read it (rule 12)."""
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        raise JobException(f"{what}: {path} cannot be read any more ({e})", "not-found", {"file": path})
    if hashlib.md5(data).hexdigest() != md5:
        raise JobException(f"{what}: {Path(path).name} changed since the plan was made; plan again", "image-changed",
                           {"file": path})
    return data


def fitted(box: Tuple[int, int], pixels: Tuple[int, int], fit: str) -> Tuple[int, int]:
    """The new picture's size in EMU (see the module docstring)."""
    (bw, bh), (pw, ph) = box, pixels
    if fit == "stretch":
        return bw, bh
    if fit != "contain":
        raise JobException(f"'fit' must be one of {list(FITS)}", "bad-params")
    if bw * ph <= bh * pw:
        w, h = bw, (2 * bw * ph + pw) // (2 * pw)
    else:
        w, h = (2 * bh * pw + ph) // (2 * ph), bh
    if w <= 0 or h <= 0:
        raise JobException(f"the new picture ({pw} × {ph} px) would be 0 EMU high or wide in a {bw} × {bh} EMU box",
                           "bad-params")
    return w, h


def image_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """The picture spec as the plan pins it: path, md5, format, pixel size."""
    path, data, md5, fmt, w, h = load_image(params.get("image"), "swap_image.image")
    return {"file": str(path), "md5": md5, "format": fmt, "width": w, "height": h, "size": len(data)}
