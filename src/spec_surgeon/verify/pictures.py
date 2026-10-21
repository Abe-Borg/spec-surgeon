"""V10 kinds ``inserted_images``, ``removed_images`` and ``resized_images`` (built-in batch 6,
docs/BUILTIN_OPS_BATCH6_DESIGN.md §5) — verifier side, independent of ops/ (rule 1): its own picture-file reader
(verify/images.py), its own reading of the pictures of a part, the match, the refusals, the sizes, the sections' text
widths, the relationships and the content types; a new picture's paragraph is built here again from this module's own
template, written as XML text.

* ``inserted_images``: the anchors re-resolved over the SOURCE (this package's paragraph selector), the placed size from
  the picture file read here (its resolution, the section's text width), the drawing ids counted up from the source's
  highest; each new paragraph must be the anchor's sibling and equal to the template (its revision marks new, by the
  plan's author); the part's rels must be the source's plus exactly one image relationship to the one new media member,
  which holds the file's bytes; ``[Content_Types].xml`` the source's plus exactly the Default (or Override) the extension
  needs; the drawing ids unique in the output.
* ``removed_images``: the matches derived here; every paragraph holding a removed picture must be the source's with
  exactly those drawings gone (untracked) or inside a new ``w:del`` (tracked), item by item, and equal to the paragraph
  built here from the source's by the rule (the runs cut, emptied runs gone); a run carrying a pending formatting change
  (``w:rPrChange``) that the removal would split (tracked) or empty (untracked) must have been refused; untracked, the
  rels must lose exactly the relationships only removed drawings named, the package exactly the members nothing
  targets any more, and ``[Content_Types].xml`` exactly their Overrides; tracked, nothing but the parts may change.
* ``resized_images``: the matches and the new sizes derived here; every drawing of a part identical but for the resized
  pictures' two sizes, which the residual then masks.
"""
from __future__ import annotations

import copy
import hashlib
import posixpath
import re
from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from lxml import etree

from .edits import Item, part_revision_ids, v_items
from .images import _labels, _own_picture, _rels_of, v_picture_info
from .markup import check_content_types, check_paragraph, check_relationships, drop
from .props import Ctx, first_difference, mask_picture_sizes, para_matches, sect_prs, sig
from .reader import CT, REL, VPackage, W

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_R = "{%s}" % _R_NS
_WP = "{%s}" % _WP_NS
_A = "{%s}" % _A_NS
_PIC = "{%s}" % _PIC_NS
_WP14 = "{http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing}"
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_V = "{urn:schemas-microsoft-com:vml}"
_O = "{urn:schemas-microsoft-com:office:office}"
_W16DU = "{http://schemas.microsoft.com/office/word/2023/wordml/word16du}"
_IMAGE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
_WATERMARKS = ("PowerPlusWaterMarkObject", "WordPictureWatermark")
_REVISIONS = {W + "ins", W + "del", W + "moveFrom", W + "moveTo"}
_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "gif": "image/gif", "bmp": "image/bmp"}
_PER_INCH = 914400
_PER_TWIP = 635
_LARGEST = 27273042316900                # the schema's largest drawing size (ST_PositiveCoordinate)
_SCOPES = ("document", "headers", "footers")

# the new picture's paragraph, as this module expects it — {placeholders} filled by _v_new_paragraph
_V_DRAWING = (
    '<w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{cx}" cy="{cy}"/>'
    '<wp:effectExtent l="0" t="0" r="0" b="0"/><wp:docPr id="{id}" name="Picture {id}"/>'
    '<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>'
    '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic>'
    '<pic:nvPicPr><pic:cNvPr id="0" name="Picture {id}"/><pic:cNvPicPr/></pic:nvPicPr>'
    '<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
    '<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
    '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline>'
    '</w:drawing>')
_V_PARAGRAPH = (f'<w:p xmlns:w="{_W_NS}" xmlns:wp="{_WP_NS}" xmlns:a="{_A_NS}" xmlns:pic="{_PIC_NS}" '
                f'xmlns:r="{_R_NS}">{{ppr}}{{body}}</w:p>')


# ---- small readings ------------------------------------------------------------------------------------------------

def _v_round(x: Fraction) -> int:
    """Halves up, for a number at or above 0."""
    return (2 * x.numerator + x.denominator) // (2 * x.denominator)


def _v_emu(inches: Any) -> int:
    return _v_round(Fraction(str(inches)) * _PER_INCH)


def _v_fits(size: Tuple[int, int]) -> bool:
    """A size a drawing may state: each side at least 1 EMU and at most the schema's largest."""
    return all(1 <= v <= _LARGEST for v in size)


def _v_scope(raw: Any) -> List[str]:
    kinds = [raw] if isinstance(raw, str) else list(raw or ["document"])
    if not kinds or any(k not in _SCOPES for k in kinds):
        raise ValueError(f"scope {raw!r} is not document/headers/footers")
    return list(dict.fromkeys(kinds))


def _v_match(raw: Any, pinned: Optional[str], kind: str, errs: List[str]) -> Dict[str, Any]:
    """The user's match, read here; an old picture file is hashed again and must be the one the plan read."""
    if raw == "all":
        return {"how": "all"}
    if isinstance(raw, dict) and len(raw) == 1:
        key, val = next(iter(raw.items()))
        if key == "image" and isinstance(val, str):
            try:
                got = hashlib.md5(Path(val).read_bytes()).hexdigest()
            except OSError as e:
                raise ValueError(f"the picture file to match cannot be read here ({e})")
            if got != pinned:
                errs.append(f"{kind}: the picture file to match {val} changed since planning")
            return {"how": "md5", "md5": got}
        if key == "md5" and isinstance(val, str) and re.fullmatch(r"[0-9a-fA-F]{32}", val):
            return {"how": "md5", "md5": val.lower()}
        if key == "name_regex" and isinstance(val, str) and val:
            return {"how": "name", "rx": re.compile(val)}
    raise ValueError(f"unknown match {raw!r}")


def _v_up(el, tag: str):
    """The nearest ancestor of ``el`` with ``tag``, or None."""
    for q in el.iterancestors():
        if q.tag == tag:
            return q
    return None


def _v_up_any(el, tags: Set[str], stop) -> bool:
    """Whether an ancestor of ``el`` below ``stop`` has one of ``tags``."""
    q = el.getparent()
    while q is not None and q is not stop:
        if q.tag in tags:
            return True
        q = q.getparent()
    return False


def _v_watermark(ref) -> bool:
    for q in [ref, *ref.iterancestors()]:
        if q.tag == _V + "shape" and (q.get("id") or "").startswith(_WATERMARKS):
            return True
        if q.tag == W + "drawing" and any(
                fr.tag in (_WP + "inline", _WP + "anchor") and fr.find(_WP + "docPr") is not None
                and (fr.find(_WP + "docPr").get("name") or "").startswith(_WATERMARKS) for fr in q):
            return True
    return False


def _v_in_field(root) -> Set[Any]:
    """The w:drawing elements inside a w:fldSimple or between a complex field's begin and end."""
    out, open_fields = set(), 0
    for el in root.iter():
        if el.tag == W + "fldChar":
            t = el.get(W + "fldCharType")
            open_fields = open_fields + 1 if t == "begin" else max(open_fields - 1, 0) if t == "end" else open_fields
        elif el.tag == W + "drawing" and (open_fields > 0 or _v_up(el, W + "fldSimple") is not None):
            out.add(el)
    return out


def _v_pictures(pkg: VPackage, part: str, root, how: Dict[str, Any], kind: str, errs: List[str]):
    """(the matched pictures as (ordinal, drawing, paragraph) in ordinal order, the watermark references left alone)
    of one part, by this module's own reading; a match the op must refuse goes to ``errs``."""
    rels = _rels_of(pkg, part)
    drawings = list(root.iter(W + "drawing"))
    digest: Dict[str, Optional[str]] = {}

    def md5_of(rid):
        r = rels.get(rid)
        if r is None or r["mode"] != "Internal" or r["resolved"] not in pkg.names:
            return None
        if r["resolved"] not in digest:
            digest[r["resolved"]] = hashlib.md5(pkg.read(r["resolved"])).hexdigest()
        return digest[r["resolved"]]

    place = {x: i for i, x in enumerate(drawings)}
    hits: Dict[int, Tuple[int, Any, Any]] = {}
    watermarks = 0
    for ref in [e for e in root.iter() if e.tag in (_A + "blip", _V + "imagedata")]:
        if ref.tag == _V + "imagedata":
            rid, linked = ref.get(_R + "id") or ref.get(_O + "relid"), False
        else:
            linked = ref.get(_R + "embed") is None
            rid = ref.get(_R + "link") if linked else ref.get(_R + "embed")
        if how["how"] == "md5":
            hit = not linked and md5_of(rid) == how["md5"]
        elif how["how"] == "name":
            hit = any(how["rx"].search(t) for t in _labels(ref))
        else:
            hit = True
        if not hit:
            continue
        if _v_watermark(ref):
            watermarks += 1
            continue
        d = _own_picture(ref) if ref.tag == _A + "blip" else None
        if d is None:
            errs.append(f"{kind}: {part}: a matching {etree.QName(ref).localname} is not a picture of its own — the op "
                        f"must refuse")
            continue
        k = place[d]
        p = _v_up(d, W + "p")
        if _v_up(ref, _MC + "AlternateContent") is not None:
            errs.append(f"{kind}: {part} picture {k} is inside mc:AlternateContent — the op must refuse")
        elif p is None or _v_up(p, W + "p") is not None:
            errs.append(f"{kind}: {part} picture {k} is in a text box — the op must refuse")
        else:
            hits[k] = (k, d, p)
    return [hits[k] for k in sorted(hits)], watermarks


def _v_ct_children(pkg: VPackage) -> List[Tuple[str, Tuple[Tuple[str, str], ...]]]:
    return sorted((e.tag, tuple(sorted(e.attrib.items()))) for e in pkg.xml("[Content_Types].xml")
                  if isinstance(e.tag, str))


def _v_type_of(pkg: VPackage, name: str) -> Optional[str]:
    defaults, overrides = pkg.content_types()
    return overrides.get(name) or defaults.get(posixpath.splitext(name)[1].lstrip(".").lower())


# ---- inserted_images -----------------------------------------------------------------------------------------------

def _v_text_width(sp) -> Optional[int]:
    """EMU between the margins: pgSz w − pgMar left, right, gutter (whole twips; a missing margin is 0)."""
    size, mar = sp.find(W + "pgSz"), sp.find(W + "pgMar")
    if size is None or mar is None:
        return None
    raw = [size.get(W + "w")] + [mar.get(W + k, "0") for k in ("left", "right", "gutter")]
    if any(v is None or not re.fullmatch(r"-?[0-9]+", v) for v in raw):
        return None
    left = int(raw[0]) - sum(int(v) for v in raw[1:])
    return left * _PER_TWIP if left > 0 else None


def _v_section_index(main_root) -> List[int]:
    """Per paragraph of the main part, by index: the section it belongs to (the next sectPr at or after it)."""
    out, seen = [], 0
    for p in main_root.iter(W + "p"):
        out.append(seen)
        if p.find(W + "pPr/" + W + "sectPr") is not None:
            seen += 1
    return out


def _v_placed(pw: int, ph: int, ex: Fraction, ey: Fraction, width: Any, height: Any,
              limit: Optional[int]) -> Tuple[int, int]:
    nw, nh = pw * ex, ph * ey
    if width is not None and height is not None:
        return _v_emu(width), _v_emu(height)
    if width is not None:
        w = _v_emu(width)
        return w, _v_round(w * nh / nw)
    if height is not None:
        h = _v_emu(height)
        return _v_round(h * nw / nh), h
    if nw > limit:
        return limit, _v_round(limit * nh / nw)
    return _v_round(nw), _v_round(nh)


def _v_new_paragraph(cx: int, cy: int, docpr: int, rid: str, a: Dict[str, Any]):
    style, align, tracked = a.get("style"), a.get("align"), bool(a.get("tracked", True))
    ppr = ""
    if style or align or tracked:
        ppr = ("<w:pPr>" + ('<w:pStyle w:val=""/>' if style else "") + ('<w:jc w:val=""/>' if align else "")
               + ('<w:rPr><w:ins/></w:rPr>' if tracked else "") + "</w:pPr>")
    run = "<w:r><w:rPr><w:noProof/></w:rPr>" + _V_DRAWING.format(cx=cx, cy=cy, id=docpr, rid=rid) + "</w:r>"
    p = etree.fromstring(_V_PARAGRAPH.format(ppr=ppr, body=f"<w:ins>{run}</w:ins>" if tracked else run))
    if style:
        p.find(f"{W}pPr/{W}pStyle").set(W + "val", style)
    if align:
        p.find(f"{W}pPr/{W}jc").set(W + "val", align)
    if a.get("description") is not None:
        next(p.iter(_WP + "docPr")).set("descr", a["description"])
    for ins in p.iter(W + "ins"):
        ins.set(W + "author", a.get("author") or "")
    return p


def _v_strip_marks(p, author, old_ids: Set[str], where: str, errs: List[str]):
    """A copy of the output paragraph with the revision stamps of its new w:ins taken out (each checked: by the plan's
    author, dated, an id no revision of the source has)."""
    c = copy.deepcopy(p)
    for ins in c.iter(W + "ins"):
        rid = ins.get(W + "id")
        if ins.get(W + "author") != author or ins.get(W + "date") is None or rid is None or rid in old_ids:
            errs.append(f"{where}: a w:ins is not a new revision by {author!r} (id {rid!r})")
        for k in (W + "id", W + "date", _W16DU + "dateUtc"):
            ins.attrib.pop(k, None)
    return c


def _inserted_images(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    img = a.get("image") or {}
    try:
        data = Path(img["file"]).read_bytes()
    except (OSError, KeyError, TypeError) as e:
        raise ValueError(f"the picture file cannot be read here ({type(e).__name__}: {e})")
    md5 = hashlib.md5(data).hexdigest()
    if md5 != img.get("md5"):
        errs.append(f"inserted_images: the picture {img.get('file')} changed since planning")
        return "inserted_images: picture changed"
    kind, pw, ph, ex, ey = v_picture_info(data)
    if (kind, pw, ph) != (img.get("format"), img.get("width"), img.get("height")):
        errs.append(f"inserted_images: the plan read the picture as {img.get('format')} {img.get('width')} × "
                    f"{img.get('height')} px; it is {kind} {pw} × {ph} px")
    parts = ctx.scope_parts(_v_scope(a.get("scope")))
    pred = (a.get("select") or {}).get("paragraphs")
    after = a.get("position", "after") != "before"
    width, height = a.get("width"), a.get("height")
    main = ctx.src.main_part()
    main_root = ctx.src.xml(main)
    secs = sect_prs(main_root)
    widths = [_v_text_width(sp) for sp in secs]
    section = _v_section_index(main_root)
    old_ids = set().union(*(part_revision_ids(ctx.src.xml(t)) for t in ctx.src_tp))
    top = 0
    for t in ctx.src_tp:
        for dp in ctx.src.xml(t).iter(_WP + "docPr"):
            if re.fullmatch(r"-?[0-9]+", dp.get("id") or ""):
                top = max(top, int(dp.get("id")))
    derived: Dict[str, List[Dict[str, Any]]] = {}
    for part in parts:
        if part not in ctx.both:
            errs.append(f"inserted_images: {part} is missing from the output")
            continue
        sv, _ = ctx.view(part)
        chosen = [bi for bi, p in enumerate(sv.pars) if para_matches(pred, p, bi, sv, ctx.src_styles)]
        rows = []
        for k, bi in enumerate(chosen):
            anchor = sv.pars[bi]
            where = f"inserted_images {part} anchor {bi}"
            if _v_up(anchor, W + "p") is not None or any(q is not anchor for q in anchor.iter(W + "p")):
                errs.append(f"{where}: in or holding a text box — the op must refuse")
                continue
            if after and anchor.find(W + "pPr/" + W + "sectPr") is not None:
                errs.append(f"{where}: carries a section break — the op must refuse 'after'")
                continue
            limit = None
            if width is None and height is None:
                if part == main:
                    ws = [widths[section[bi]] if section[bi] < len(widths) else None]
                else:                                   # a header or footer: every section's
                    ws = list(widths)
                if not ws or any(w is None for w in ws):
                    errs.append(f"{where}: no usable page size to fit the picture in — the op must refuse")
                    continue
                limit = min(ws)
            cx, cy = _v_placed(pw, ph, ex, ey, width, height, limit)
            if not _v_fits((cx, cy)):
                errs.append(f"{where}: the picture would be {cx} × {cy} EMU — the op must refuse")
                continue
            top += 1
            rows.append({"anchor": bi, "out": bi + 1 + k if after else bi + k, "docpr": top, "size": [cx, cy]})
        if rows:
            derived[part] = rows
    planned = a.get("expected") or {}
    if derived != planned:
        errs.append(f"inserted_images: the plan inserts {planned}; derived here {derived}")
    n = sum(len(v) for v in derived.values())
    if n != a.get("expected_anchors", -1):
        errs.append(f"inserted_images: {n} anchor(s) here, the plan predicted {a.get('expected_anchors')}")
    media = a.get("media")
    if not derived:
        if media:
            errs.append(f"inserted_images: the plan adds {media} but no picture is inserted")
        return "inserted_images: no anchor, nothing inserted"
    # the media member: new, the file's bytes, a name and a content type for its format
    if not media or media in ctx.src.names or media not in ctx.out.names:
        errs.append(f"inserted_images: {media} is not a new member of the output")
        return "inserted_images: no new media"
    if hashlib.md5(ctx.out.read(media)).hexdigest() != md5:
        errs.append(f"inserted_images: {media} does not hold the picture file's bytes")
    ext = posixpath.splitext(media)[1].lstrip(".").lower()
    if ext != kind or _v_type_of(ctx.out, media) != _TYPES[kind]:
        errs.append(f"inserted_images: {media} has the extension .{ext} and the content type "
                    f"{_v_type_of(ctx.out, media)!r} for a {kind} picture")
    s_def, _ = ctx.src.content_types()
    want = _v_ct_children(ctx.src)
    if s_def.get(ext) is None:
        want = sorted(want + [(CT + "Default", (("ContentType", _TYPES[kind]), ("Extension", ext)))])
    elif s_def[ext] != _TYPES[kind]:
        want = sorted(want + [(CT + "Override", (("ContentType", _TYPES[kind]), ("PartName", "/" + media)))])
    if _v_ct_children(ctx.out) != want:
        errs.append("inserted_images: [Content_Types].xml is not the source's with exactly the entry the picture's "
                    "extension needs")
    ids: Counter = Counter()
    for t in ctx.out.text_parts():
        ids.update(dp.get("id") for dp in ctx.out.xml(t).iter(_WP + "docPr"))
    for part, rows in derived.items():
        d, b = posixpath.split(part)
        rp = posixpath.join(d, "_rels", b + ".rels")
        target = posixpath.relpath(media, d)
        check_relationships(ctx.src, ctx.out, rp, [], [{"Type": _IMAGE_REL, "Target": target}],
                            f"inserted_images {part}", errs)
        o_rels = ctx.out.xml(rp).findall(REL + "Relationship") if rp in ctx.out.names else []
        rid = o_rels[-1].get("Id") if o_rels else None
        if rid != (a.get("rids") or {}).get(part):
            errs.append(f"inserted_images: {part}'s new relationship is {rid!r}, the plan said "
                        f"{(a.get('rids') or {}).get(part)!r}")
        sv, ov = ctx.view(part)
        for row in rows:
            oi, where = row["out"], f"inserted_images {part} block {row['out']}"
            if oi >= len(ov.pars):
                errs.append(f"{where}: out of range")
                continue
            ctx.inserted_out_blocks.setdefault(part, set()).add(oi)
            op = ov.pars[oi]
            ai = ctx.maps[part].get(row["anchor"])
            beside = op.getprevious() if after else op.getnext()
            if ai is None or ai >= len(ov.pars) or beside is not ov.pars[ai]:
                errs.append(f"{where}: the new paragraph is not right {'after' if after else 'before'} its anchor")
            want_p = _v_new_paragraph(row["size"][0], row["size"][1], row["docpr"], rid or "", a)
            got_p = _v_strip_marks(op, a.get("author"), old_ids, where, errs)
            if sig(want_p) != sig(got_p):
                errs.append(f"{where}: not the expected picture paragraph: "
                            f"{first_difference(want_p, got_p) or 'differs'}")
            if ids.get(str(row["docpr"])) != 1:
                errs.append(f"{where}: the drawing id {row['docpr']} is not unique in the output")
    return (f"inserted_images: {n} picture paragraph(s) in {len(derived)} part(s), {kind} {pw} × {ph} px as {media}, "
            f"each built again from the template")


# ---- removed_images ------------------------------------------------------------------------------------------------

def _v_cuts(run, gone: Set[Any]) -> List[Tuple[bool, List[Any]]]:
    """A run's content (its children but w:rPr) cut where it changes between removed drawings and the rest."""
    out: List[Tuple[bool, List[Any]]] = []
    for c in run:
        if c.tag == W + "rPr":
            continue
        hit = c in gone
        if not out or out[-1][0] != hit:
            out.append((hit, []))
        out[-1][1].append(c)
    return out


def _v_without(p, ds: List[Any], tracked: bool, author: Optional[str]):
    """The paragraph as the removal must leave it, built here from a copy of the source paragraph: untracked, each
    removed drawing gone and a run left with nothing but its w:rPr gone too; tracked, each run cut at its removed
    drawings (a run of its own per stretch, the run's attributes and w:rPr copied) and each stretch of them inside a
    w:del by ``author`` (stamped w:author only — the output's new stamps are taken out before the comparison)."""
    c = copy.deepcopy(p)
    mine = list(p.iter(W + "drawing"))
    theirs = list(c.iter(W + "drawing"))
    gone = {theirs[mine.index(d)] for d in ds}
    runs = list(dict.fromkeys(d.getparent() for d in gone))
    for run in runs:
        if not tracked:
            for d in [d for d in run if d in gone]:
                run.remove(d)
            if all(x.tag == W + "rPr" for x in run):
                run.getparent().remove(run)
            continue
        cuts = _v_cuts(run, gone)
        pieces = []
        for hit, children in cuts:
            if len(cuts) == 1:
                piece = run
            else:
                piece = etree.Element(W + "r", dict(run.attrib))
                if run.find(W + "rPr") is not None:
                    piece.append(copy.deepcopy(run.find(W + "rPr")))
                piece.extend(children)
            if hit:
                wrap = etree.Element(W + "del", {W + "author": author or ""})
                wrap.append(copy.deepcopy(piece) if piece is run else piece)
                piece = wrap
            pieces.append(piece)
        for piece in pieces:
            run.addprevious(piece)
        run.getparent().remove(run)
    return c


def _v_unstamped(p, author, old_ids: Set[str], where: str, errs: List[str]):
    """A copy of the output paragraph with the stamps of its new w:del taken out (each by ``author``, dated, an id no
    revision of the source has); the source's own deletions keep theirs."""
    c = copy.deepcopy(p)
    for d in c.iter(W + "del"):
        if d.getparent() is not None and d.getparent().tag == W + "rPr" or d.get(W + "id") in old_ids:
            continue
        if d.get(W + "author") != author or d.get(W + "date") is None or d.get(W + "id") is None:
            errs.append(f"{where}: a w:del is not a new revision by {author!r}")
        for k in (W + "id", W + "date", _W16DU + "dateUtc"):
            d.attrib.pop(k, None)
    return c


def _removed_images(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    how = _v_match(a.get("match"), a.get("match_md5"), "removed_images", errs)
    tracked = bool(a.get("tracked", True))
    author = a.get("author")
    parts = ctx.scope_parts(_v_scope(a.get("scope")))
    old_ids = set().union(*(part_revision_ids(ctx.src.xml(t)) for t in ctx.src_tp))
    derived: Dict[str, List[int]] = {}
    gone_rels: Dict[str, List[str]] = {}
    watermarks = 0
    for part in parts:
        if part not in ctx.both:
            errs.append(f"removed_images: {part} is missing from the output")
            continue
        sv, ov = ctx.view(part)
        found, n_wm = _v_pictures(ctx.src, part, sv.root, how, "removed_images", errs)
        watermarks += n_wm
        fields = _v_in_field(sv.root)
        ok = []
        for k, d, p in found:
            if d.getparent() is None or d.getparent().tag != W + "r":
                errs.append(f"removed_images: {part} picture {k} is not a run's child — the op must refuse")
            elif _v_up_any(d, _REVISIONS, p):
                errs.append(f"removed_images: {part} picture {k} is in a pending tracked change — the op must refuse")
            elif d in fields:
                errs.append(f"removed_images: {part} picture {k} is shown by a field — the op must refuse")
            else:
                ok.append((k, d, p))
        if not ok:
            continue
        derived[part] = [k for k, _, _ in ok]
        gone = {d for _, d, _ in ok}
        index = {p: i for i, p in enumerate(sv.pars)}
        by_p: Dict[Any, List[Any]] = {}
        for _, d, p in ok:
            by_p.setdefault(p, []).append(d)
        for p, ds in by_p.items():
            bi = index[p]
            oi = ctx.maps[part].get(bi)
            where = f"removed_images {part} paragraph {bi}"
            if oi is None or oi >= len(ov.pars):
                errs.append(f"{where}: missing from the output")
                continue
            items = v_items(p)
            if tracked:
                expected = [Item("x", it.value, (it.ctx[0], it.ctx[1], it.ctx[2] + (("NEW", W + "del", author),)),
                                 None, it.tag, it.run, it.el, False, it.chain_els + (None,))
                            if it.el in gone else it for it in items]
            else:
                expected = drop(items, elements=ds)
            for r in dict.fromkeys(d.getparent() for d in ds):
                if r.find(f"{W}rPr/{W}rPrChange") is None:
                    continue
                cuts = _v_cuts(r, gone)
                if tracked and len(cuts) > 1:
                    errs.append(f"{where}: a run to split carries a pending formatting change — the op must refuse")
                elif not tracked and all(hit for hit, _ in cuts):
                    errs.append(f"{where}: a run the removal empties carries a pending formatting change, which would go "
                                f"with it — the op must refuse")
            check_paragraph(p, ov.pars[oi], expected, old_ids, where, errs)
            want_p = _v_without(p, ds, tracked, author)
            got_p = _v_unstamped(ov.pars[oi], author, old_ids, where, errs) if tracked else ov.pars[oi]
            if sig(want_p) != sig(got_p):
                errs.append(f"{where}: not the paragraph the removal leaves "
                            f"({'its runs cut at the deletions' if tracked else 'the pictures and emptied runs gone'}): "
                            f"{first_difference(want_p, got_p) or 'differs'}")
            ctx.verified_blocks.setdefault(part, {})[bi] = oi
        if not tracked:
            inside = Counter(v for d in gone for e in d.iter() if isinstance(e.tag, str)
                             for k, v in e.attrib.items() if k.startswith(_R))
            whole = Counter(v for e in sv.root.iter() if isinstance(e.tag, str)
                            for k, v in e.attrib.items() if k.startswith(_R))
            rels = _rels_of(ctx.src, part)
            rids = sorted(v for v in inside if v in rels and inside[v] == whole[v])
            if rids:
                gone_rels[part] = rids
    planned = a.get("expected") or {}
    if derived != planned:
        errs.append(f"removed_images: the plan removes pictures {planned}; derived here {derived}")
    if watermarks != a.get("watermarks", 0):
        errs.append(f"removed_images: {watermarks} watermark picture(s) here, the plan said {a.get('watermarks', 0)}")
    if not derived and not a.get("allow_missing"):
        errs.append("removed_images: no picture matches in scope, and allow_missing was not given")
    # relationships, members, content types
    removed: List[str] = []
    for part in derived:
        d, b = posixpath.split(part)
        rp = posixpath.join(d, "_rels", b + ".rels")
        if rp in ctx.src.names:
            check_relationships(ctx.src, ctx.out, rp, gone_rels.get(part, []), [], f"removed_images {part}", errs)
    if gone_rels != (a.get("rels_removed") or {}):
        errs.append(f"removed_images: relationships no longer used here {gone_rels}, the plan removed "
                    f"{a.get('rels_removed')}")
    if gone_rels:
        targets = {_rels_of(ctx.src, part)[rid]["resolved"] for part, rids in gone_rels.items() for rid in rids
                   if _rels_of(ctx.src, part)[rid]["mode"] == "Internal"}
        used = {t for _, _, _, t in ctx.out.all_relationships() if t is not None}
        removed = sorted(t for t in targets if t in ctx.src.names and t not in used)
        for t in sorted(targets):
            if t in ctx.out.names and t in ctx.src.names and ctx.out.read(t) != ctx.src.read(t):
                errs.append(f"removed_images: {t} is still used but changed")
            if t in removed and t in ctx.out.names:
                errs.append(f"removed_images: {t} is no longer used but is still in the file")
            if t not in removed and t in ctx.src.names and t not in ctx.out.names:
                errs.append(f"removed_images: {t} went although a relationship still targets it")
    if removed != sorted(a.get("members_removed") or []):
        errs.append(f"removed_images: files no longer used {removed}, the plan removed {a.get('members_removed')}")
    if "[Content_Types].xml" in ctx.out.names:
        _, s_ov = ctx.src.content_types()
        check_content_types(ctx.src, ctx.out, [m for m in removed if m in s_ov], {}, "removed_images", errs)
    n = sum(len(v) for v in derived.values())
    return (f"removed_images: {n} picture(s) out of {len(derived)} part(s) ({'tracked' if tracked else 'untracked'}), "
            f"each paragraph checked item by item; {len(removed)} file(s) gone")


# ---- resized_images ------------------------------------------------------------------------------------------------

def _v_size_els(drawing):
    fr = next((c for c in drawing if c.tag in (_WP + "inline", _WP + "anchor")), None)
    if fr is None:
        return None, None, None
    return (fr, fr.find(_WP + "extent"),
            fr.find(f"{_A}graphic/{_A}graphicData/{_PIC}pic/{_PIC}spPr/{_A}xfrm/{_A}ext"))


def _v_unresizable(drawing) -> Optional[str]:
    fr, ext, aext = _v_size_els(drawing)
    xfrm = fr.find(f"{_A}graphic/{_A}graphicData/{_PIC}pic/{_PIC}spPr/{_A}xfrm") if fr is not None else None
    if xfrm is not None and xfrm.get("rot") not in (None, "", "0"):
        return "turned"
    if fr is not None and (fr.find(_WP14 + "sizeRelH") is not None or fr.find(_WP14 + "sizeRelV") is not None):
        return "sized relative to the page"
    if ext is None or aext is None:
        return "no size"
    raw = (ext.get("cx"), ext.get("cy"), aext.get("cx"), aext.get("cy"))
    if any(v is None or not re.fullmatch(r"[0-9]+", v) or int(v) == 0 for v in raw) or raw[:2] != raw[2:]:
        return f"sizes {raw}"
    return None


def _v_resized(box: Tuple[int, int], a: Dict[str, Any]) -> Tuple[int, int]:
    w0, h0 = box
    if a.get("scale") is not None:
        s = Fraction(str(a["scale"])) / 100
        return _v_round(w0 * s), _v_round(h0 * s)
    width, height = a.get("width"), a.get("height")
    if width is not None and height is not None:
        return _v_emu(width), _v_emu(height)
    if width is not None:
        w = _v_emu(width)
        return w, _v_round(Fraction(w * h0, w0))
    h = _v_emu(height)
    return _v_round(Fraction(h * w0, h0)), h


def _resized_images(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    how = _v_match(a.get("match"), a.get("match_md5"), "resized_images", errs)
    if a.get("scale") is None and a.get("width") is None and a.get("height") is None:
        raise ValueError("no width, height or scale")
    parts = ctx.scope_parts(_v_scope(a.get("scope")))
    derived: Dict[str, Dict[str, List[int]]] = {}
    already, watermarks = 0, 0
    for part in parts:
        if part not in ctx.both:
            errs.append(f"resized_images: {part} is missing from the output")
            continue
        s_root, o_root = ctx.src.xml(part), ctx.out.xml(part)
        found, n_wm = _v_pictures(ctx.src, part, s_root, how, "resized_images", errs)
        watermarks += n_wm
        sizes: Dict[str, List[int]] = {}
        for k, d, _ in found:
            why = _v_unresizable(d)
            if why:
                errs.append(f"resized_images: {part} picture {k} cannot be resized ({why}) — the op must refuse")
                continue
            _, ext, _ = _v_size_els(d)
            box = (int(ext.get("cx")), int(ext.get("cy")))
            new = _v_resized(box, a)
            if not _v_fits(new):
                errs.append(f"resized_images: {part} picture {k} would be {new} EMU — the op must refuse")
            elif new == box:
                already += 1
            else:
                sizes[str(k)] = list(new)
        s_d, o_d = list(s_root.iter(W + "drawing")), list(o_root.iter(W + "drawing"))
        if len(s_d) != len(o_d):
            errs.append(f"resized_images: {part} has {len(o_d)} drawing(s), the source {len(s_d)}")
            continue
        for k, (sd, od) in enumerate(zip(s_d, o_d)):
            if str(k) in sizes:
                want = tuple(str(v) for v in sizes[str(k)])
                _, e1, e2 = _v_size_els(od)
                got = [None if e is None else (e.get("cx"), e.get("cy")) for e in (e1, e2)]
                if got != [want, want]:
                    errs.append(f"resized_images: {part} picture {k}: size {got}, expected {want}")
                sm, om = copy.deepcopy(sd), copy.deepcopy(od)
                mask_picture_sizes(sm)
                mask_picture_sizes(om)
            else:
                sm, om = sd, od
            if sig(sm) != sig(om):
                errs.append(f"resized_images: {part} drawing {k} changed beyond its size")
        if sizes:
            derived[part] = sizes
            ctx.masked_extents.setdefault(part, set()).update(int(k) for k in sizes)
    if derived != (a.get("expected") or {}):
        errs.append(f"resized_images: the plan predicted sizes {a.get('expected')}; derived here {derived}")
    if already != a.get("already", 0):
        errs.append(f"resized_images: {already} picture(s) already that size here, the plan said {a.get('already', 0)}")
    if watermarks != a.get("watermarks", 0):
        errs.append(f"resized_images: {watermarks} watermark picture(s) here, the plan said {a.get('watermarks', 0)}")
    if not derived and not already and not a.get("allow_missing"):
        errs.append("resized_images: no picture matches in scope, and allow_missing was not given")
    n = sum(len(v) for v in derived.values())
    return f"resized_images: {n} picture(s) resized in {len(derived)} part(s), {already} already that size"


PICTURE_KINDS = {"inserted_images": _inserted_images, "removed_images": _removed_images,
                 "resized_images": _resized_images}
