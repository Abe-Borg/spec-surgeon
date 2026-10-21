"""Picture files for swap_image (design note §8) and insert_image (built-in batch 6) — the transform side's reader. The
verifier has its own (verify/images.py); tests compare the two on crafted and real files.

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

Pictures to insert (built-in batch 6, docs/BUILTIN_OPS_BATCH6_DESIGN.md §5; ``read_picture``, both sides): the formats
above and **BMP** (``BM``; the DIB header's size, a little-endian 32-bit number at byte 14, is 12 — width and height
16-bit at bytes 18 and 20 — or at least 40 — width and height signed 32-bit at bytes 18 and 22, a negative height read
as its magnitude, the pixels per metre signed 32-bit at bytes 38 and 42). The **resolution**, in EMU per pixel across
and down: PNG — the first ``pHYs`` chunk before ``IDAT`` (chunks walked from byte 8: length, type, data, CRC) with unit 1,
36 000 000 / pixels per metre; JPEG — the first ``APP0`` ``JFIF`` segment before the frame header with units 1 (914 400 /
dots per inch) or 2 (360 000 / dots per cm), else Exif IFD0's XResolution / YResolution (rationals) in ResolutionUnit 2
(inches, the default) or 3 (cm); BMP — 36 000 000 / pixels per metre; GIF — none. A resolution counts only when both of
its numbers are positive; without one a picture has 96 dpi (9 525 EMU per pixel). Reading a resolution never refuses a
picture: a damaged one is no resolution. The **natural size** is the pixel size times the resolution, exactly
(fractions); the **placed size** (``placed_size``): width and height given → those (inches × 914 400, halves rounded
up); one given → it, and the other in the natural proportions (rounded); neither → the natural size rounded, and when
the natural width exceeds the text width, the text width and the height in proportion. A side that rounds to 0, or
beyond the largest size a drawing may state (``MAX_EMU``), is refused.
"""
from __future__ import annotations

import hashlib
import math
import struct
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, NamedTuple, Optional, Tuple

from .base import JobException

CONTENT_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "gif": "image/gif"}
INSERT_TYPES = {**CONTENT_TYPES, "bmp": "image/bmp"}       # what insert_image places (built-in batch 6)
FITS = ("contain", "stretch")
QUARTER_TURNS = {5, 6, 7, 8}
EMU_PER_INCH = 914400
EMU_PER_TWIP = 635
DEFAULT_EMU_PER_PIXEL = Fraction(9525)                   # 96 dpi
MAX_INCHES = 100                                         # a width or height asked for beyond this is a typing slip
MAX_EMU = 27273042316900                                 # ST_PositiveCoordinate: the largest size a drawing may state


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


# ---- pictures to insert (built-in batch 6): BMP, the resolution, the placed size ------------------------------------

class Picture(NamedTuple):
    format: str                 # png | jpeg | gif | bmp
    width: int                  # pixels
    height: int
    emu_x: Fraction             # EMU per pixel across
    emu_y: Fraction             # … and down


def _bmp(data: bytes) -> Tuple[int, int, Optional[Tuple[Fraction, Fraction]]]:
    if len(data) < 26:
        raise ValueError("the BMP is cut short")
    dib = struct.unpack("<I", data[14:18])[0]
    if dib == 12:
        w, h = struct.unpack("<HH", data[18:22])
        return w, h, None
    if dib < 40 or len(data) < 54:
        raise ValueError(f"its BMP header ({dib} bytes) is not one Word reads")
    w, h, xppm, yppm = (struct.unpack("<i", data[18:22])[0], struct.unpack("<i", data[22:26])[0],
                        struct.unpack("<i", data[38:42])[0], struct.unpack("<i", data[42:46])[0])
    return w, abs(h), ((Fraction(36000000, xppm), Fraction(36000000, yppm)) if xppm > 0 and yppm > 0 else None)


def _png_resolution(data: bytes) -> Optional[Tuple[Fraction, Fraction]]:
    at = 8
    while at + 8 <= len(data):
        length = struct.unpack(">I", data[at:at + 4])[0]
        kind = data[at + 4:at + 8]
        if kind in (b"IDAT", b"IEND"):
            return None
        if kind == b"pHYs":
            body = data[at + 8:at + 8 + length]
            if length != 9 or len(body) != 9:
                return None
            x, y, unit = struct.unpack(">IIB", body)
            return (Fraction(36000000, x), Fraction(36000000, y)) if unit == 1 and x > 0 and y > 0 else None
        at += 12 + length
    return None


def _exif_resolution(seg: bytes) -> Optional[Tuple[Fraction, Fraction]]:
    """XResolution / YResolution of an APP1 segment's IFD0 (starting ``Exif\\0\\0``) as EMU per pixel, or None."""
    tiff = seg[6:]
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return None
    e = "<" if tiff[:2] == b"II" else ">"
    if struct.unpack(e + "H", tiff[2:4])[0] != 42:
        return None
    ifd = struct.unpack(e + "I", tiff[4:8])[0]
    if ifd + 2 > len(tiff):
        return None
    found: Dict[int, Any] = {}
    for k in range(struct.unpack(e + "H", tiff[ifd:ifd + 2])[0]):
        at = ifd + 2 + 12 * k
        if at + 12 > len(tiff):
            return None
        tag, typ, count = struct.unpack(e + "HHI", tiff[at:at + 8])
        if tag in (0x011A, 0x011B) and typ == 5 and count == 1:
            off = struct.unpack(e + "I", tiff[at + 8:at + 12])[0]
            if off + 8 > len(tiff):
                return None
            found[tag] = struct.unpack(e + "II", tiff[off:off + 8])
        elif tag == 0x0128 and typ == 3 and count == 1:
            found[tag] = struct.unpack(e + "H", tiff[at + 8:at + 10])[0]
    per = {2: EMU_PER_INCH, 3: 360000}.get(found.get(0x0128, 2))
    xr, yr = found.get(0x011A), found.get(0x011B)
    if per is None or xr is None or yr is None or min(xr + yr) <= 0:
        return None
    return Fraction(per * xr[1], xr[0]), Fraction(per * yr[1], yr[0])


def _jpeg_resolution(data: bytes) -> Optional[Tuple[Fraction, Fraction]]:
    i, n, jfif, exif = 2, len(data), None, None
    seen_jfif = seen_exif = False
    while i < n:
        if data[i] != 0xFF:
            return None
        while i < n and data[i] == 0xFF:
            i += 1
        if i >= n:
            return None
        m = data[i]
        i += 1
        if m == 0x01 or 0xD0 <= m <= 0xD8:
            continue
        if m in (0xD9, 0xDA) or (0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC)) or i + 2 > n:
            break
        length = struct.unpack(">H", data[i:i + 2])[0]
        if length < 2 or i + length > n:
            return None
        seg = data[i + 2:i + length]
        if m == 0xE0 and seg[:5] == b"JFIF\x00" and not seen_jfif:
            seen_jfif = True
            if len(seg) >= 12:
                units, x, y = seg[7], struct.unpack(">H", seg[8:10])[0], struct.unpack(">H", seg[10:12])[0]
                per = {1: EMU_PER_INCH, 2: 360000}.get(units)
                if per is not None and x > 0 and y > 0:
                    jfif = Fraction(per, x), Fraction(per, y)
        if m == 0xE1 and seg[:6] == b"Exif\x00\x00" and not seen_exif:
            seen_exif = True
            exif = _exif_resolution(seg)
        i += length
    return jfif or exif


def read_picture(data: bytes) -> Picture:
    """The picture file as insert_image reads it (module docstring), or ValueError saying why it cannot be used."""
    if data[:2] == b"BM":
        try:
            w, h, res = _bmp(data)
        except struct.error:
            raise ValueError("the BMP is cut short")
        if w <= 0 or h <= 0:
            raise ValueError(f"its size is {w} × {h} pixels")
        fmt = "bmp"
    else:
        fmt, w, h = sniff(data)
        try:
            res = _png_resolution(data) if fmt == "png" else _jpeg_resolution(data) if fmt == "jpeg" else None
        except struct.error:
            res = None
    ex, ey = res or (DEFAULT_EMU_PER_PIXEL, DEFAULT_EMU_PER_PIXEL)
    return Picture(fmt, w, h, ex, ey)


def load_picture(spec: Any, what: str) -> Tuple[Path, bytes, str, Picture]:
    """(path, bytes, md5, picture) of the picture file to insert — ``not-found`` when it cannot be read,
    ``image-format`` when it is not a PNG, JPEG, GIF or BMP picture Word shows as it is."""
    if not isinstance(spec, str) or not spec.strip():
        raise JobException(f"{what}: give the picture as a path to a .png, .jpg, .gif or .bmp file", "bad-params")
    path = Path(spec)
    try:
        data = path.read_bytes()
    except OSError as e:
        raise JobException(f"{what}: the picture {path} cannot be read ({e})", "not-found", {"file": str(path)})
    try:
        pic = read_picture(data)
    except (ValueError, struct.error) as e:
        why = str(e).replace("not a PNG, JPEG or GIF picture", "not a PNG, JPEG, GIF or BMP picture")
        raise JobException(f"{what}: {path.name} cannot be inserted: {why}", "image-format", {"file": str(path)})
    return path, data, hashlib.md5(data).hexdigest(), pic


def half_up(x: Fraction) -> int:
    """A non-negative exact number rounded to a whole number, halves up."""
    return math.floor(x + Fraction(1, 2))


def inches_emu(value: Any) -> int:
    """A length in inches as the user gave it (a number) in EMU, from its decimal spelling."""
    return half_up(Fraction(str(value)) * EMU_PER_INCH)


def length_param(value: Any, what: str) -> Optional[Any]:
    """A width or height in inches: absent, or a positive number up to MAX_INCHES."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= MAX_INCHES \
            or not math.isfinite(value):
        raise JobException(f"'{what}' must be a length in inches (a positive number up to {MAX_INCHES})", "bad-params")
    return value


def placed_size(pic: Picture, width: Any, height: Any, text_width: Optional[int]) -> Tuple[int, int]:
    """The inserted picture's size in EMU (module docstring); ``text_width`` (EMU) is read only when neither side is
    given."""
    nat_w, nat_h = pic.width * pic.emu_x, pic.height * pic.emu_y
    if width is not None and height is not None:
        w, h = inches_emu(width), inches_emu(height)
    elif width is not None:
        w = inches_emu(width)
        h = half_up(w * nat_h / nat_w)
    elif height is not None:
        h = inches_emu(height)
        w = half_up(h * nat_w / nat_h)
    else:
        if text_width is None:
            raise JobException("internal: the picture's own size needs the text width", "internal")
        if nat_w > text_width:
            w, h = text_width, half_up(text_width * nat_h / nat_w)
        else:
            w, h = half_up(nat_w), half_up(nat_h)
    if w <= 0 or h <= 0 or w > MAX_EMU or h > MAX_EMU:
        raise JobException(f"the picture ({pic.width} × {pic.height} px) would be {w} × {h} EMU — give another "
                           f"width or height", "image-size", {"size": [w, h]})
    return w, h
