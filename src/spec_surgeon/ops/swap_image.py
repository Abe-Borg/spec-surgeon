"""Op: swap_image — swap a picture (a logo) in every header for a new one, fitted to the same space (design note §8).

Definition (CLAUDE.md "Pictures", both sides; the picture-file reader and the fitted size are in ops/images.py):

* a **picture** is a ``w:drawing`` of a part in scope whose ``wp:inline``/``wp:anchor`` holds
  ``a:graphic/a:graphicData/pic:pic`` with ``pic:blipFill/a:blip``; its **ordinal** is its position among every
  ``w:drawing`` of the part (document order); its **media** is the member the blip's ``r:embed`` relationship targets;
* every other image reference in a part in scope — an ``a:blip`` that is not a picture's own (a shape's picture fill, a
  picture inside a group or canvas) and every VML ``v:imagedata`` — is matched too, and refused when it matches;
* **match**: ``{"image": file}`` / ``{"md5": …}`` — the media's bytes have that md5 (a linked picture has no media and
  never matches); ``{"name_regex": …}`` — searched in the drawing's ``wp:docPr`` name, descr and title (VML: the
  imagedata's ``o:title`` and its shape's ``alt``); ``"all"`` — every reference;
* a matched picture is **refused** (``structure``) when it lies inside ``mc:AlternateContent``, its blip is linked
  (``r:link``) or has no ``r:embed``, it carries an SVG (``asvg:svgBlip``), it is cropped (``a:srcRect`` with a
  non-zero side), turned (``a:xfrm/@rot`` not 0), lacks ``wp:extent`` or ``a:xfrm/a:ext``, or the two sizes differ; its
  relationship must be internal and its media a member; every reference to that relationship in the part must be a
  matched picture's ``r:embed`` (else ``structure``: swapping it would change something else too);
* a matched picture whose media already has the new picture's bytes is left alone (noted);
* the swap: ONE new media member (``word/media/image<N>.<png|jpeg|gif>``, the smallest free N) holding the picture
  file's bytes; each swapped relationship keeps its id and type and targets it; ``wp:extent`` and the picture's
  ``a:xfrm/a:ext`` take the fitted size; an old media member that no relationship targets any more is removed.
  Nothing else changes (alt text included — flagged).

No picture matched in any part in scope → ``not-found`` unless ``allow_missing``. The verifier derives all of it again
(verify/images.py, kind ``image_swaps``).
"""
from __future__ import annotations

import hashlib
import posixpath
import re
from typing import Any, Dict, List, Optional, Tuple

from ..ooxml import paragraphs
from ..package import CT_PART, REL, DocxPackage
from . import prose, register
from .base import JobException, Op, PlanFragment
from .patterns import compile_pattern
from .images import CONTENT_TYPES, FITS, check_unchanged, file_md5, fitted, image_params
from .replace_table import resolve_scope
from .selectors import scope_parts

NAME_TEXT = "a picture's name or description"   # what name_regex is searched on

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
V_NS = "urn:schemas-microsoft-com:vml"
O_NS = "urn:schemas-microsoft-com:office:office"
W, R, WP, A, PIC, MC, V, O = ("{%s}" % n for n in (W_NS, R_NS, WP_NS, A_NS, PIC_NS, MC_NS, V_NS, O_NS))
IMAGE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
DEFAULT_SCOPE = ["headers"]


def match_rule(raw: Any) -> Dict[str, Any]:
    """The match as the plan pins it: {"how": "md5", "md5", "file"?} | {"how": "name", "name_regex"} | {"how": "all"}."""
    if raw == "all":
        return {"how": "all"}
    if isinstance(raw, dict) and len(raw) == 1:
        (k, v), = raw.items()
        if k == "image" and isinstance(v, str) and v.strip():
            return {"how": "md5", "md5": file_md5(v, "swap_image.match.image"), "file": v}
        if k == "md5" and isinstance(v, str) and re.fullmatch(r"[0-9a-fA-F]{32}", v):
            return {"how": "md5", "md5": v.lower()}
        if k == "name_regex" and isinstance(v, str) and v:
            try:
                compile_pattern(v, where=NAME_TEXT)
            except JobException as e:
                raise JobException(f"swap_image.match.name_regex: {e.reason}", e.code, e.details) from None
            return {"how": "name", "name_regex": v}
    raise JobException("'match' must be {'image': '<the old picture file>'}, {'md5': '<32 hex digits>'}, "
                       "{'name_regex': '<regex on the picture's name/description>'} or 'all'", "bad-params")


def enclosing(el, tag: str):
    q = el.getparent()
    while q is not None and q.tag != tag:
        q = q.getparent()
    return q


def picture_of(blip):
    """The w:drawing whose own picture this blip is, else None."""
    fill = blip.getparent()
    pic = fill.getparent() if fill is not None else None
    gd = pic.getparent() if pic is not None else None
    graphic = gd.getparent() if gd is not None else None
    frame = graphic.getparent() if graphic is not None else None
    drawing = frame.getparent() if frame is not None else None
    if (fill is not None and fill.tag == PIC + "blipFill" and pic is not None and pic.tag == PIC + "pic"
            and gd is not None and gd.tag == A + "graphicData" and graphic is not None and graphic.tag == A + "graphic"
            and frame is not None and frame.tag in (WP + "inline", WP + "anchor")
            and drawing is not None and drawing.tag == W + "drawing"):
        return drawing
    return None


def names_of(ref) -> List[str]:
    """What name_regex is searched in: docPr name/descr/title (DrawingML) or o:title and the shape's alt (VML)."""
    if ref.tag == V + "imagedata":
        shape = ref.getparent()
        return [x for x in (ref.get(O + "title"), shape.get("alt") if shape is not None else None) if x]
    drawing = enclosing(ref, W + "drawing")
    if drawing is None:
        return []
    out = []
    for frame in drawing:
        if frame.tag in (WP + "inline", WP + "anchor"):
            dp = frame.find(WP + "docPr")
            if dp is not None:
                out += [x for x in (dp.get("name"), dp.get("descr"), dp.get("title")) if x]
    return out


def ref_id(ref) -> Tuple[Optional[str], bool]:
    """(relationship id, linked) of an image reference."""
    if ref.tag == V + "imagedata":
        return ref.get(R + "id") or ref.get(O + "relid"), False
    if ref.get(R + "embed") is not None:
        return ref.get(R + "embed"), False
    return ref.get(R + "link"), True


def frame_of(drawing):
    return next(c for c in drawing if c.tag in (WP + "inline", WP + "anchor"))


def picture_problem(drawing, blip) -> Optional[str]:
    """Why a matched picture cannot be swapped, or None."""
    if enclosing(blip, MC + "AlternateContent") is not None:
        return "it lies inside mc:AlternateContent (Word keeps a second copy of it)"
    if blip.get(R + "link") is not None or blip.get(R + "embed") is None:
        return "it is a linked picture (its file is outside the document)"
    if any(e.tag.endswith("}svgBlip") for e in blip.iter() if isinstance(e.tag, str)):
        return "it is an SVG picture (Word shows the SVG, not the PNG copy beside it)"
    frame = frame_of(drawing)
    pic = blip.getparent().getparent()
    src = blip.getparent().find(A + "srcRect")
    if src is not None and any((src.get(k) or "0") != "0" for k in ("l", "t", "r", "b")):
        return "it is cropped (a new picture would be cropped the same way)"
    xfrm = pic.find(PIC + "spPr/" + A + "xfrm")
    if xfrm is not None and (xfrm.get("rot") or "0") != "0":
        return "it is turned"
    ext = frame.find(WP + "extent")
    aext = xfrm.find(A + "ext") if xfrm is not None else None
    if ext is None or aext is None:
        return "its size is not stated (wp:extent and a:xfrm/a:ext)"
    try:
        size = (int(ext.get("cx")), int(ext.get("cy")))
        inner = (int(aext.get("cx")), int(aext.get("cy")))
    except (TypeError, ValueError):
        return "its size is not a whole number of EMU"
    if size[0] <= 0 or size[1] <= 0:
        return f"its size is {size[0]} × {size[1]} EMU"
    if size != inner:
        return f"its frame ({size[0]} × {size[1]} EMU) and its picture ({inner[0]} × {inner[1]} EMU) differ"
    return None


def shared_use(root, rids, own) -> Optional[Tuple[Any, str]]:
    """The first element of the part that refers to one of ``rids`` other than a swapped picture's own ``r:embed``."""
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for k, v in el.attrib.items():
            if k.startswith(R) and v in rids and not (k == R + "embed" and id(el) in own):
                return el, v
    return None


def unused_after(pkg: DocxPackage, retargeted, olds) -> List[str]:
    """The old media members no relationship targets once ``retargeted`` ((part, rid) pairs) point elsewhere."""
    still = {r.resolved for owner, r in pkg.relationships() if r.resolved and (owner, r.rid) not in retargeted}
    return [t for t in sorted(olds) if t not in still]


def box_of(drawing) -> Tuple[int, int]:
    ext = frame_of(drawing).find(WP + "extent")
    return int(ext.get("cx")), int(ext.get("cy"))


def free_media_name(pkg: DocxPackage, fmt: str) -> str:
    taken = {n.casefold() for n in pkg.member_names}
    base = posixpath.join(posixpath.dirname(pkg.main_part()), "media")
    n = 1
    while posixpath.join(base, f"image{n}.{fmt}").casefold() in taken:
        n += 1
    return posixpath.join(base, f"image{n}.{fmt}")


def match_words(match: Any) -> str:
    """The old picture as the plan names it: ``every picture that shows the file “old.png”``, ``every picture whose
    name or description matches the pattern “logo”``, ``every picture``."""
    if match == "all":
        return "every picture"
    if isinstance(match, dict) and len(match) == 1:
        (k, v), = match.items()
        if k == "image":
            return f"every picture that shows the file {prose.quote(v)}"
        if k == "md5":
            return f"every picture whose image has the checksum {prose.quote(v)}"
        if k == "name_regex":
            return f"every picture whose name or description matches the pattern {prose.quote(v)}"
    return "the matching picture"


def _scope_words(scope: Any) -> str:
    """``in the headers`` (the default), ``in the document body``, ``in the headers and footers``."""
    kinds = [scope] if isinstance(scope, str) else list(scope or DEFAULT_SCOPE)
    return "in the document body" if kinds == ["document"] else prose.scope_words(kinds)


@register
class SwapImage(Op):
    name = "swap_image"
    mode = "formatting"
    description = ("Swap a picture (a logo) for a new picture file in every header (or footers/body), fitted to the "
                   "same space: 'contain' (default) keeps the new picture's proportions inside the old picture's box, "
                   "'stretch' fills the box. The old picture is found by its file (match {'image': old.png}), its md5, "
                   "its name/alt text (name_regex) or 'all'. Linked, SVG, cropped or turned pictures are refused.")
    params_schema = {
        "type": "object",
        "properties": {
            "image": {"type": "string", "description": "the new picture: a .png, .jpg or .gif file"},
            "match": {"description": "{image: the old picture file} | {md5} | {name_regex} | 'all'"},
            "scope": {"type": "array", "items": {"type": "string", "enum": ["document", "headers", "footers"]},
                      "default": DEFAULT_SCOPE},
            "fit": {"type": "string", "enum": list(FITS), "default": "contain"},
            "allow_missing": {"type": "boolean", "default": False,
                              "description": "a file without a matching picture passes unchanged instead of being "
                                             "set aside"},
        },
        "required": ["image", "match"],
    }
    select_schema = {"type": "object", "properties": {"scope": {"type": "array"}}}
    verification_profile = {
        "V1": "one media member added (the picture file's bytes), unreferenced old media removed; the parts, their "
              "rels and [Content_Types].xml when an override changes",
        "V10": "image_swaps re-derives the matches (its own md5 of every media member, its own picture reader), the "
               "fitted sizes, every relationship and content type; the residual masks only the swapped sizes",
    }

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        new = params.get("image")
        new_words = f"the picture {prose.quote(new)}" if new else "the new picture"
        scope = select.get("scope") or params.get("scope") or DEFAULT_SCOPE
        fit = ("stretched to fill the old picture's space" if params.get("fit") == "stretch"
               else "fitted inside the old picture's space with its proportions kept")
        first = prose.sentence("swap", match_words(params.get("match")), "for", new_words,
                               _scope_words(scope) + ",", fit)
        if params.get("allow_missing") is not True:
            return first
        return first + " " + prose.sentence("files with no such picture are left unchanged")

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        unknown = set(select) - {"scope"}
        if unknown:
            raise JobException(f"swap_image's selector takes only 'scope' (got {sorted(unknown)})", "bad-params")
        new = image_params(params)
        match = match_rule(params.get("match"))
        if match.get("md5") == new["md5"]:
            raise JobException("the new picture is the one the match finds — nothing would change", "bad-params")
        fit = params.get("fit", "contain")
        if fit not in FITS:
            raise JobException(f"'fit' must be one of {list(FITS)}", "bad-params")
        allow_missing = params.get("allow_missing", False)
        if not isinstance(allow_missing, bool):
            raise JobException("'allow_missing' must be true or false", "bad-params")
        scope = resolve_scope(select, params, DEFAULT_SCOPE)
        if set(scope) - {"document", "headers", "footers"}:
            raise JobException("swap_image's scope is document, headers and/or footers", "bad-params")
        parts = scope_parts(pkg, scope)
        name_rx = compile_pattern(match["name_regex"], where=NAME_TEXT) if match["how"] == "name" else None
        md5_of: Dict[str, str] = {}

        def media_md5(target: Optional[str]) -> Optional[str]:
            if target is None or not pkg.has(target):
                return None
            if target not in md5_of:
                md5_of[target] = hashlib.md5(pkg.read(target)).hexdigest()
            return md5_of[target]

        swaps: Dict[str, Dict[str, Any]] = {}
        already = 0
        flags: List[str] = []
        for part in parts:
            root = pkg.xml(part)
            rels = pkg.rels(part)
            drawings = list(root.iter(W + "drawing"))     # kept alive: lxml keeps one proxy per live element, so id()s hold
            ordinal = {id(d): k for k, d in enumerate(drawings)}
            block = {}
            for bi, p in enumerate(paragraphs(root)):
                for d in p.iter(W + "drawing"):
                    block[id(d)] = bi                      # a nested paragraph comes later: the nearest wins
            chosen: Dict[int, Tuple[Any, Any]] = {}        # ordinal → (drawing, its blip)
            rids: Dict[str, List[int]] = {}
            refs = list(root.iter(A + "blip", V + "imagedata"))
            for ref in refs:
                rid, linked = ref_id(ref)
                rel = rels.get(rid) if rid else None
                target = rel.resolved if rel is not None and rel.target_mode == "Internal" else None
                if match["how"] == "md5":
                    hit = not linked and media_md5(target) == match["md5"]
                elif match["how"] == "name":
                    hit = any(name_rx.search(n) for n in names_of(ref))
                else:
                    hit = True
                if not hit:
                    continue
                drawing = picture_of(ref) if ref.tag == A + "blip" else None
                where = f"{part}: picture {ordinal.get(id(drawing), '?')}" if drawing is not None else f"{part}"
                if drawing is None:
                    what = "a VML picture" if ref.tag == V + "imagedata" else "a picture inside a shape, group or canvas"
                    raise JobException(f"{part}: a matching picture is {what} — it cannot be swapped and fitted",
                                       "structure", {"part": part})
                why = picture_problem(drawing, ref)
                if why:
                    raise JobException(f"{where}: a matching picture cannot be swapped: {why}", "structure",
                                       {"part": part, "picture": ordinal[id(drawing)]})
                if rel is None or rel.target_mode != "Internal" or rel.type != IMAGE_REL or not pkg.has(target):
                    raise JobException(f"{where}: the picture's relationship {rid} is not an image inside the file",
                                       "structure", {"part": part, "rid": rid})
                if media_md5(target) == new["md5"]:
                    already += 1
                    continue
                chosen[ordinal[id(drawing)]] = (drawing, ref)
                rids.setdefault(rid, []).append(ordinal[id(drawing)])
            if not chosen:
                continue
            hit = shared_use(root, set(rids), {id(blip) for _, blip in chosen.values()})
            if hit is not None:
                raise JobException(f"{part}: the picture relationship {hit[1]} is also used by "
                                   f"{hit[0].tag.rsplit('}', 1)[-1]} — swapping it would change that too",
                                   "structure", {"part": part, "rid": hit[1]})
            sizes = {}
            for k, (d, _) in sorted(chosen.items()):
                box = box_of(d)
                sizes[str(k)] = list(fitted(box, (new["width"], new["height"]), fit))
                dp = frame_of(d).find(WP + "docPr")
                alt = " / ".join(x for x in ((dp.get("descr"), dp.get("title")) if dp is not None else ()) if x)
                if alt:
                    flags.append(f"swap_image: {part} picture {k} keeps its alt text, which may describe the old "
                                 f"picture: {alt!r}")
            swaps[part] = {"rids": sorted(rids), "sizes": sizes,
                           "blocks": sorted({block[id(d)] for d, _ in chosen.values()}),
                           "boxes": {str(k): list(box_of(d)) for k, (d, _) in chosen.items()},
                           "old": {rid: rels[rid].resolved for rid in rids}}
        frag = self.fragment({"scope": scope} if select.get("scope") else {}, params)
        frag.geometry_reference = "none"
        frag.geometry_reason = "another picture, in another size, changes what the header shows"
        for part in parts:
            frag.touches.append((part, "scope", "verify"))          # V10 re-derives the matches over every part in scope
        pinned_match = dict(match)
        # the verifier reads the user's match itself (only the old picture file's md5 is pinned: rule 12)
        assertion = {"kind": "image_swaps", "image": new, "match": params.get("match"),
                     "match_md5": match["md5"] if match.get("file") else None, "scope": scope, "fit": fit,
                     "allow_missing": allow_missing, "expected": {p: s["sizes"] for p, s in swaps.items()},
                     "already": already}
        if not swaps:
            if not already and not allow_missing:
                raise JobException(f"no picture in the {', '.join(scope)} matches — nothing to swap", "not-found",
                                   {"scope": scope})
            frag.notes.append("swap_image: " + (f"{already} picture(s) already show the new picture; nothing to swap"
                                                if already else "no matching picture in this file (allowed)"))
            assertion.update(new_member=None, removed=[])
            frag.property_assertions.append(assertion)
            return frag
        new_member = free_media_name(pkg, new["format"])
        retargeted = {(part, rid) for part, s in swaps.items() for rid in s["rids"]}
        removed = unused_after(pkg, retargeted, {t for s in swaps.values() for t in s["old"].values()})
        ct = CONTENT_TYPES[new["format"]]
        defaults, overrides = pkg.content_types()
        ct_changes = defaults.get(new["format"]) != ct or any(t in overrides for t in removed)
        for part, s in swaps.items():
            frag.intended_parts += [part, pkg.rels_path_for(part)]
            frag.touches += [(part, str(bi), "drawing") for bi in s["blocks"]]
            frag.touches.append((pkg.rels_path_for(part), "*", "rels"))
        if ct_changes:
            frag.intended_parts.append(CT_PART)
            frag.touches.append((CT_PART, "*", "ct"))
        frag.members_added = [new_member]
        frag.members_removed = removed
        frag.touches += [(new_member, "*", "add")] + [(m, "*", "remove") for m in removed]
        assertion.update(new_member=new_member, removed=removed)
        frag.property_assertions.append(assertion)
        n = sum(len(s["sizes"]) for s in swaps.values())
        frag.notes.append(f"swap_image: {n} picture(s) in {len(swaps)} part(s) → {new['file']} "
                          f"({new['width']} × {new['height']} px, fit {fit}): "
                          + "; ".join(f"{p} picture {k}: {s['boxes'][k][0]} × {s['boxes'][k][1]} → {v[0]} × {v[1]} EMU"
                                      for p, s in swaps.items() for k, v in s["sizes"].items())
                          + (f"; {already} already show it" if already else "")
                          + (f"; old picture(s) removed: {removed}" if removed else ""))
        frag.flags.extend(flags)
        frag.transform_data = {
            "image": {"file": new["file"], "md5": new["md5"]}, "match": pinned_match, "new_member": new_member,
            "content_type": ct, "removed": removed,
            "parts": {p: {"retarget": {rid: posixpath.relpath(new_member, posixpath.dirname(p)) for rid in s["rids"]},
                          "sizes": s["sizes"]} for p, s in swaps.items()}}
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td:
            return
        data = check_unchanged(td["image"]["file"], td["image"]["md5"], "swap_image.image")
        if td["match"].get("file"):
            check_unchanged(td["match"]["file"], td["match"]["md5"], "swap_image.match.image")
        pkg.add_member(td["new_member"], data, td["content_type"])
        for part, d in td["parts"].items():
            root = pkg.xml(part)
            drawings = list(root.iter(W + "drawing"))
            for k, (cx, cy) in d["sizes"].items():
                frame = frame_of(drawings[int(k)])
                frame.find(WP + "extent").set("cx", str(cx))
                frame.find(WP + "extent").set("cy", str(cy))
                ext = frame.find(A + "graphic/" + A + "graphicData/" + PIC + "pic/" + PIC + "spPr/" + A + "xfrm/"
                                 + A + "ext")
                ext.set("cx", str(cx))
                ext.set("cy", str(cy))
            pkg.write_xml(part, root)
            rp = pkg.rels_path_for(part)
            rels = pkg.xml(rp)
            for rel in rels.findall(REL + "Relationship"):
                if rel.get("Id") in d["retarget"]:
                    rel.set("Target", d["retarget"][rel.get("Id")])
            pkg.write_xml(rp, rels)
        for m in td["removed"]:
            pkg.remove_member(m)
