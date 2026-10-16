"""V10 kind ``image_swaps`` (swap_image) — verifier side, independent of ops/ (rule 1): its own picture-file reader,
its own fitted size, its own picture detection and matching, its own reading of every relationship and content type.
Definition: CLAUDE.md "Pictures" and docs/BUILTIN_OPS_BATCH1_DESIGN.md §8.
"""
from __future__ import annotations

import copy
import hashlib
import posixpath
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from .reader import REL, W, VPackage
from .props import Ctx, mask_picture_sizes, sig

_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_PIC = "{http://schemas.openxmlformats.org/drawingml/2006/picture}"
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_V = "{urn:schemas-microsoft-com:vml}"
_O = "{urn:schemas-microsoft-com:office:office}"
_IMAGE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "gif": "image/gif"}
_EXTS = {"png": {"png"}, "jpeg": {"jpeg", "jpg", "jpe"}, "gif": {"gif"}}


# ---- the picture file (this module's own reader) -----------------------------------------------------------------

def _u(data: bytes, at: int, n: int, big: bool) -> int:
    if at < 0 or at + n > len(data):
        raise ValueError("cut short")
    return int.from_bytes(data[at:at + n], "big" if big else "little")


def _v_orientation(app1: bytes) -> Optional[int]:
    t = app1[6:]
    if t[:2] == b"II":
        big = False
    elif t[:2] == b"MM":
        big = True
    else:
        return None
    try:
        if _u(t, 2, 2, big) != 42:
            return None
        ifd = _u(t, 4, 4, big)
        for k in range(_u(t, ifd, 2, big)):
            e = ifd + 2 + 12 * k
            if _u(t, e, 2, big) == 0x0112:
                if _u(t, e + 2, 2, big) != 3 or _u(t, e + 4, 4, big) != 1:
                    return None
                return _u(t, e + 8, 2, big)
    except ValueError:
        return None
    return None


def _v_jpeg_size(data: bytes) -> Tuple[int, int, Optional[int]]:
    pos, orient = 2, None
    while True:
        if pos >= len(data):
            raise ValueError("no frame header")
        if data[pos] != 0xFF:
            raise ValueError(f"no marker at byte {pos}")
        while pos < len(data) and data[pos] == 0xFF:
            pos += 1
        if pos >= len(data):
            raise ValueError("no frame header")
        marker = data[pos]
        pos += 1
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:
            continue
        if marker in (0xD9, 0xDA):
            raise ValueError("picture data before the frame size")
        n = _u(data, pos, 2, True)
        if n < 2 or pos + n > len(data):
            raise ValueError("a segment runs past the end")
        body = data[pos + 2:pos + n]
        if marker == 0xE1 and body.startswith(b"Exif\x00\x00") and orient is None:
            orient = _v_orientation(body)
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            return _u(body, 3, 2, True), _u(body, 1, 2, True), orient
        pos += n


def v_picture(data: bytes) -> Tuple[str, int, int]:
    """(format, width, height), or ValueError — the same definition as ops/images.sniff, written again."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        if data[12:16] != b"IHDR":
            raise ValueError("PNG without IHDR")
        kind, w, h = "png", _u(data, 16, 4, True), _u(data, 20, 4, True)
    elif data[:6] == b"GIF87a" or data[:6] == b"GIF89a":
        kind, w, h = "gif", _u(data, 6, 2, False), _u(data, 8, 2, False)
    elif data[:2] == b"\xff\xd8":
        w, h, o = _v_jpeg_size(data)
        if o in (5, 6, 7, 8):
            raise ValueError(f"Exif orientation {o}: a quarter turn")
        kind = "jpeg"
    else:
        raise ValueError("not a PNG, JPEG or GIF picture")
    if not (w > 0 and h > 0):
        raise ValueError(f"{w} × {h} pixels")
    return kind, w, h


def v_fit(box: Tuple[int, int], px: Tuple[int, int], fit: str) -> Tuple[int, int]:
    W_, H_ = box
    pw, ph = px
    if fit == "stretch":
        return W_, H_
    if fit != "contain":
        raise ValueError(f"unknown fit {fit!r}")
    # compare the proportions exactly: the box is relatively narrower (or equal) → the width decides
    if W_ * ph <= H_ * pw:
        num, den = W_ * ph, pw
        out = (W_, num // den + (1 if 2 * (num % den) >= den else 0))
    else:
        num, den = H_ * pw, ph
        out = (num // den + (1 if 2 * (num % den) >= den else 0), H_)
    if min(out) < 1:
        raise ValueError("a side rounds to 0 EMU")
    return out


# ---- pictures in a part --------------------------------------------------------------------------------------------

def _rels_of(pkg: VPackage, part: str) -> Dict[str, Dict[str, Optional[str]]]:
    """Every relationship of ``part`` (internal and external) as id → {type, target (as written), mode, resolved}."""
    d, b = posixpath.split(part)
    rp = posixpath.join(d, "_rels", b + ".rels")
    if rp not in pkg.names:
        return {}
    out = {}
    for rel in pkg.xml(rp).findall(REL + "Relationship"):
        mode = rel.get("TargetMode") or "Internal"
        t = rel.get("Target") or ""
        res = None
        if mode == "Internal":
            res = t[1:] if t.startswith("/") else posixpath.normpath(posixpath.join(d, t))
        out[rel.get("Id")] = {"type": rel.get("Type"), "target": t, "mode": mode, "resolved": res}
    return out


def _own_picture(blip) -> Optional[Any]:
    """The w:drawing this blip is the own picture of (drawing → inline|anchor → graphic → graphicData → pic → blipFill)."""
    chain = []
    q = blip
    for _ in range(6):
        q = q.getparent()
        if q is None:
            return None
        chain.append(q.tag)
    want = [_PIC + "blipFill", _PIC + "pic", _A + "graphicData", _A + "graphic"]
    if chain[:4] != want or chain[4] not in (_WP + "inline", _WP + "anchor") or chain[5] != W + "drawing":
        return None
    return q


def _ancestor(el, tag):
    for q in el.iterancestors():
        if q.tag == tag:
            return q
    return None


def _labels(ref) -> List[str]:
    if ref.tag == _V + "imagedata":
        shape = ref.getparent()
        return [v for v in (ref.get(_O + "title"), None if shape is None else shape.get("alt")) if v]
    d = _ancestor(ref, W + "drawing")
    out: List[str] = []
    if d is not None:
        for fr in d:
            if fr.tag in (_WP + "inline", _WP + "anchor") and fr.find(_WP + "docPr") is not None:
                dp = fr.find(_WP + "docPr")
                out.extend(v for v in (dp.get("name"), dp.get("descr"), dp.get("title")) if v)
    return out


def _frame(drawing):
    for c in drawing:
        if c.tag in (_WP + "inline", _WP + "anchor"):
            return c
    return None


def _sizes(drawing) -> Tuple[Optional[Tuple[str, str]], Optional[Tuple[str, str]]]:
    fr = _frame(drawing)
    ext = fr.find(_WP + "extent") if fr is not None else None
    aext = fr.find(f"{_A}graphic/{_A}graphicData/{_PIC}pic/{_PIC}spPr/{_A}xfrm/{_A}ext") if fr is not None else None
    s = lambda e: None if e is None else (e.get("cx"), e.get("cy"))
    return s(ext), s(aext)


def _why_not(drawing, blip) -> Optional[str]:
    if _ancestor(blip, _MC + "AlternateContent") is not None:
        return "inside mc:AlternateContent"
    if blip.get(_R + "embed") is None or blip.get(_R + "link") is not None:
        return "linked"
    for e in blip.iter():
        if isinstance(e.tag, str) and etree.QName(e).localname == "svgBlip":
            return "SVG"
    src = blip.getparent().find(_A + "srcRect")
    if src is not None and any(src.get(k) not in (None, "0") for k in ("l", "t", "r", "b")):
        return "cropped"
    xfrm = blip.getparent().getparent().find(f"{_PIC}spPr/{_A}xfrm")
    if xfrm is not None and xfrm.get("rot") not in (None, "0"):
        return "turned"
    outer, inner = _sizes(drawing)
    if outer is None or inner is None:
        return "no size"
    if not all(re.fullmatch(r"\d+", v or "") for v in outer + inner):
        return "size not a whole number"
    if outer != inner or int(outer[0]) == 0 or int(outer[1]) == 0:
        return "sizes differ or are 0"
    return None


def _scan(pkg: VPackage, part: str, how: Dict[str, Any], new_md5: str):
    """(drawings, swapped {ordinal: (box, rid)}, already, problems) of one part, by this module's own reading."""
    root = pkg.xml(part)
    rels = _rels_of(pkg, part)
    drawings = list(root.iter(W + "drawing"))
    index = {id(d): k for k, d in enumerate(drawings)}
    rx = re.compile(how["name_regex"]) if how.get("how") == "name" else None
    digest: Dict[str, Optional[str]] = {}

    def md5_of(rid):
        r = rels.get(rid)
        if r is None or r["mode"] != "Internal" or r["resolved"] not in pkg.names:
            return None
        if r["resolved"] not in digest:
            digest[r["resolved"]] = hashlib.md5(pkg.read(r["resolved"])).hexdigest()
        return digest[r["resolved"]]

    swapped: Dict[int, Tuple[Tuple[int, int], str]] = {}
    own_blips = []
    already, problems = 0, []
    refs = [e for e in root.iter() if e.tag in (_A + "blip", _V + "imagedata")]
    for ref in refs:
        if ref.tag == _V + "imagedata":
            rid, linked = ref.get(_R + "id") or ref.get(_O + "relid"), False
        else:
            rid = ref.get(_R + "embed") if ref.get(_R + "embed") is not None else ref.get(_R + "link")
            linked = ref.get(_R + "embed") is None
        if how["how"] == "md5":
            hit = (not linked) and md5_of(rid) == how["md5"]
        elif how["how"] == "name":
            hit = any(rx.search(t) for t in _labels(ref))
        else:
            hit = True
        if not hit:
            continue
        d = _own_picture(ref) if ref.tag == _A + "blip" else None
        if d is None:
            problems.append(f"{part}: a matching image reference that is not a picture ({etree.QName(ref).localname})")
            continue
        why = _why_not(d, ref)
        r = rels.get(rid)
        if why is None and (r is None or r["mode"] != "Internal" or r["type"] != _IMAGE_REL
                            or r["resolved"] not in pkg.names):
            why = "its relationship is not an image inside the file"
        if why:
            problems.append(f"{part} picture {index[id(d)]}: matches but cannot be swapped ({why})")
            continue
        if md5_of(rid) == new_md5:
            already += 1
            continue
        outer, _ = _sizes(d)
        swapped[index[id(d)]] = ((int(outer[0]), int(outer[1])), rid)
        own_blips.append(ref)
    if swapped:
        own = {id(b) for b in own_blips}
        used = {rid for _, rid in swapped.values()}
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            for k, v in el.attrib.items():
                if k.startswith(_R) and v in used and not (k == _R + "embed" and id(el) in own):
                    problems.append(f"{part}: relationship {v} of a swapped picture is also used by "
                                    f"{etree.QName(el).localname}")
    return root, drawings, swapped, already, problems, refs, own_blips


def _v_match(raw: Any, pinned: Optional[str], errs: List[str]) -> Dict[str, Any]:
    """The user's match, read here: the old picture file is hashed again (and must be the file the plan read)."""
    if raw == "all":
        return {"how": "all"}
    if isinstance(raw, dict) and len(raw) == 1:
        key, val = next(iter(raw.items()))
        if key == "image" and isinstance(val, str):
            try:
                got = hashlib.md5(Path(val).read_bytes()).hexdigest()
            except OSError as e:
                raise ValueError(f"the old picture file cannot be read here ({e})")
            if got != pinned:
                errs.append(f"image_swaps: the old picture file {val} changed since planning")
            return {"how": "md5", "md5": got}
        if key == "md5" and isinstance(val, str) and re.fullmatch(r"[0-9a-fA-F]{32}", val):
            return {"how": "md5", "md5": val.lower()}
        if key == "name_regex" and isinstance(val, str) and val:
            return {"how": "name", "name_regex": val}
    raise ValueError(f"unknown match {raw!r}")


def _ct(pkg: VPackage, name: str) -> Optional[str]:
    defaults, overrides = pkg.content_types()
    return overrides.get(name) or defaults.get(posixpath.splitext(name)[1].lstrip(".").lower())


def _image_swaps(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    img = a.get("image") or {}
    try:
        data = Path(img["file"]).read_bytes()
    except (OSError, KeyError, TypeError) as e:
        raise ValueError(f"the picture file cannot be read here ({type(e).__name__}: {e})")
    md5 = hashlib.md5(data).hexdigest()
    if md5 != img.get("md5"):
        errs.append(f"image_swaps: the picture {img.get('file')} changed since planning")
        return "image_swaps: picture changed"
    kind, pw, ph = v_picture(data)
    if (kind, pw, ph) != (img.get("format"), img.get("width"), img.get("height")):
        errs.append(f"image_swaps: the plan read the picture as {img.get('format')} {img.get('width')} × "
                    f"{img.get('height')} px; it is {kind} {pw} × {ph} px")
    how = _v_match(a.get("match"), a.get("match_md5"), errs)
    fit = a.get("fit", "contain")
    scope = a.get("scope") or ["headers"]
    parts = ctx.scope_parts(scope)
    derived: Dict[str, Dict[str, List[int]]] = {}
    retarget: Dict[str, Dict[str, str]] = {}
    already = 0
    for part in parts:
        if part not in ctx.out.names:
            errs.append(f"image_swaps: {part} is gone from the output")
            continue
        root, drawings, swapped, n_already, problems, _, _ = _scan(ctx.src, part, how, md5)
        already += n_already
        errs.extend(f"image_swaps: {p}" for p in problems)
        o_root = ctx.out.xml(part)
        o_drawings = list(o_root.iter(W + "drawing"))
        if len(o_drawings) != len(drawings):
            errs.append(f"image_swaps: {part} has {len(o_drawings)} drawing(s), the source {len(drawings)}")
            continue
        sizes = {}
        for k, (box, rid) in swapped.items():
            sizes[str(k)] = list(v_fit(box, (pw, ph), fit))
            retarget.setdefault(part, {})[rid] = ""
        if sizes:
            derived[part] = sizes
        # every drawing of the part: identical but for the swapped sizes, which must be the fitted ones
        for k, (sd, od) in enumerate(zip(drawings, o_drawings)):
            if str(k) in sizes:
                want = tuple(str(v) for v in sizes[str(k)])
                outer, inner = _sizes(od)
                if outer != want or inner != want:
                    errs.append(f"image_swaps: {part} picture {k}: size {outer} / {inner}, expected {want} "
                                f"(fit {fit} of {pw} × {ph} px in {swapped[k][0]})")
            sm, om = copy.deepcopy(sd), copy.deepcopy(od)
            if str(k) in sizes:
                mask_picture_sizes(sm)
                mask_picture_sizes(om)
            if sig(sm) != sig(om):
                errs.append(f"image_swaps: {part} drawing {k} changed beyond its size")
        ctx.masked_extents.setdefault(part, set()).update(int(k) for k in sizes)
    if derived != (a.get("expected") or {}):
        errs.append(f"image_swaps: the plan predicted sizes {a.get('expected')}; derived here {derived}")
    if already != a.get("already", 0):
        errs.append(f"image_swaps: {already} picture(s) already show the new picture; the plan said {a.get('already', 0)}")
    if not derived and not already and not a.get("allow_missing"):
        errs.append("image_swaps: no picture matches in scope, and allow_missing was not given")
    # relationships: in scope, identical but the swapped ones' target; out of scope, image relationships identical
    new_targets = set()
    olds = set()
    for rp_owner in sorted({p for p in parts if p in ctx.out.names} | {o for _, o, _, _ in ctx.src.all_relationships()
                                                                        if o and o in ctx.out.names}):
        s_rels, o_rels = _rels_of(ctx.src, rp_owner), _rels_of(ctx.out, rp_owner)
        mine = retarget.get(rp_owner, {})
        for rid in sorted(set(s_rels) | set(o_rels)):
            s, o = s_rels.get(rid), o_rels.get(rid)
            if rp_owner not in parts and (s or o or {}).get("type") != _IMAGE_REL:
                continue
            if rid in mine:
                if o is None or (o["type"], o["mode"]) != (s["type"], s["mode"]):
                    errs.append(f"image_swaps: {rp_owner} relationship {rid} lost its type or mode")
                    continue
                new_targets.add(o["resolved"])
                olds.add(s["resolved"])
            elif s != o:
                errs.append(f"image_swaps: {rp_owner} relationship {rid} changed ({s} → {o})")
    if derived:
        if len(new_targets) != 1:
            errs.append(f"image_swaps: the swapped relationships target {sorted(map(str, new_targets))}, not one new member")
        else:
            nm = next(iter(new_targets))
            if nm != a.get("new_member"):
                errs.append(f"image_swaps: the swapped pictures point at {nm}, the plan said {a.get('new_member')}")
            if nm in ctx.src.names or nm not in ctx.out.names:
                errs.append(f"image_swaps: {nm} is not a new member of the output")
            else:
                if hashlib.md5(ctx.out.read(nm)).hexdigest() != md5:
                    errs.append(f"image_swaps: {nm} does not hold the picture file's bytes")
                ext = posixpath.splitext(nm)[1].lstrip(".").lower()
                if ext not in _EXTS[kind] or _ct(ctx.out, nm) != _TYPES[kind]:
                    errs.append(f"image_swaps: {nm} has the extension .{ext} and content type {_ct(ctx.out, nm)!r} "
                                f"for a {kind} picture")
    elif a.get("new_member"):
        errs.append(f"image_swaps: the plan added {a.get('new_member')} but nothing is swapped")
    out_refd = {t for _, _, _, t in ctx.out.all_relationships() if t is not None}
    gone = sorted(t for t in olds if t not in out_refd)
    for t in sorted(olds):
        if t in out_refd and (t not in ctx.out.names or ctx.out.read(t) != ctx.src.read(t)):
            errs.append(f"image_swaps: {t} is still used but changed or went")
        if t not in out_refd and t in ctx.out.names:
            errs.append(f"image_swaps: {t} is no longer used but is still in the file")
    if gone != sorted(a.get("removed") or []):
        errs.append(f"image_swaps: unused old pictures {gone}; the plan removed {sorted(a.get('removed') or [])}")
    s_def, s_ov = ctx.src.content_types()
    o_def, o_ov = ctx.out.content_types()
    if s_def != o_def:
        errs.append("image_swaps: [Content_Types].xml defaults changed")
    for name, t in s_ov.items():
        if name in ctx.out.names and o_ov.get(name) != t:
            errs.append(f"image_swaps: the content type override of {name} changed")
        if name in gone and name in o_ov:
            errs.append(f"image_swaps: the override of the removed {name} is still there")
    n = sum(len(v) for v in derived.values())
    return f"image_swaps: {n} picture(s) swapped in {len(derived)} part(s), {already} already new"


IMAGE_KINDS = {"image_swaps": _image_swaps}
