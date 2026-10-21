"""Op: resize_images — the pictures a match chooses get a new size (built-in batch 6, docs/BUILTIN_OPS_BATCH6_DESIGN.md
§5). Word cannot track a picture's size: formatting, untracked only. The verifier derives all of it again
(verify/pictures.py, kind ``resized_images``).

Definition (both sides; the pictures, the match and the refusals every picture op shares are ops/pictures.py's):

* **the pictures**: those the match chooses in the scope (the document body by default; headers, footers), inline or
  floating. A matched picture is refused (``structure``) when it is turned (``a:xfrm/@rot`` not 0), lacks ``wp:extent``
  or ``pic:spPr/a:xfrm/a:ext``, its two sizes differ or are not whole positive EMU, or its size follows the page
  (``wp14:sizeRelH`` / ``wp14:sizeRelV``). A cropped picture is resized like any other (the crop scales with it).
* **the size** of a picture now ``w`` × ``h`` EMU: ``width`` and ``height`` (inches) → those; one of them → it, and the
  other ``round(other · given / its own)`` (proportions kept, halves up); ``scale`` (percent) → ``round(w · s / 100)`` ×
  ``round(h · s / 100)``. Every length from its decimal spelling (ops/images.py). A side that rounds to 0, or beyond
  the largest size a drawing may state, refuses (``image-size``).
* **the change**: ``wp:extent`` and ``a:ext`` take the new size; nothing else changes (position offsets, wrap, the
  effect extent stay). A picture already that size is left alone (noted).

No picture matched in any part in scope → ``not-found`` unless ``allow_missing``.
"""
from __future__ import annotations

import math
import re
from fractions import Fraction
from typing import Any, Dict, List, Optional, Tuple

from ..package import DocxPackage
from . import prose, register
from .base import JobException, Op, PlanFragment
from .images import MAX_EMU, check_unchanged, half_up, inches_emu, length_param
from .pictures import (given_scope, length_words, match_rule, match_words, matched_pictures, media_md5s, scope_of,
                       scope_words)
from .selectors import scope_parts
from .swap_image import A, PIC, W, WP, frame_of

PARAMS = ("match", "scope", "width", "height", "scale", "tracked", "allow_missing")
WP14 = "{http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing}"
MAX_SCALE = 1000


def size_elements(drawing) -> Tuple[Any, Any]:
    frame = frame_of(drawing)
    return (frame.find(WP + "extent"),
            frame.find(f"{A}graphic/{A}graphicData/{PIC}pic/{PIC}spPr/{A}xfrm/{A}ext"))


def size_problem(drawing) -> Optional[str]:
    """Why a matched picture cannot be resized, or None."""
    frame = frame_of(drawing)
    xfrm = frame.find(f"{A}graphic/{A}graphicData/{PIC}pic/{PIC}spPr/{A}xfrm")
    if xfrm is not None and (xfrm.get("rot") or "0") != "0":
        return "it is turned"
    if frame.find(WP14 + "sizeRelH") is not None or frame.find(WP14 + "sizeRelV") is not None:
        return "its size follows the page"
    ext, aext = size_elements(drawing)
    if ext is None or aext is None:
        return "its size is not stated (wp:extent and a:xfrm/a:ext)"
    values = [ext.get("cx"), ext.get("cy"), aext.get("cx"), aext.get("cy")]
    if not all(v is not None and re.fullmatch(r"[0-9]+", v) and int(v) > 0 for v in values):
        return f"its size is not a whole positive number of EMU ({values})"
    if values[:2] != values[2:]:
        return f"its frame ({values[0]} × {values[1]} EMU) and its picture ({values[2]} × {values[3]} EMU) differ"
    return None


def new_size(box: Tuple[int, int], width: Any, height: Any, scale: Any) -> Tuple[int, int]:
    """The picture's new size in EMU (module docstring)."""
    w0, h0 = box
    if scale is not None:
        s = Fraction(str(scale)) / 100
        w, h = half_up(w0 * s), half_up(h0 * s)
    elif width is not None and height is not None:
        w, h = inches_emu(width), inches_emu(height)
    elif width is not None:
        w = inches_emu(width)
        h = half_up(Fraction(w * h0, w0))
    else:
        h = inches_emu(height)
        w = half_up(Fraction(h * w0, h0))
    if w <= 0 or h <= 0 or w > MAX_EMU or h > MAX_EMU:
        raise JobException(f"a {w0} × {h0} EMU picture would be {w} × {h} EMU", "image-size", {"size": [w, h]})
    return w, h


@register
class ResizeImages(Op):
    name = "resize_images"
    mode = "formatting"
    description = ("Resize the pictures a match chooses — by their file (match {'image': pic.png}), md5, name/alt text "
                   "(name_regex) or 'all' — in the document body (default), headers and/or footers: 'width' and/or "
                   "'height' in inches (one given keeps the proportions) or 'scale' in percent; inline and floating. "
                   "Word cannot track a picture's size: always untracked. Turned pictures are refused.")
    params_schema = {
        "type": "object",
        "properties": {
            "match": {"description": "{image: the picture file} | {md5} | {name_regex} | 'all'"},
            "scope": {"type": "array", "items": {"type": "string", "enum": ["document", "headers", "footers"]},
                      "default": ["document"]},
            "width": {"type": "number", "description": "inches"},
            "height": {"type": "number", "description": "inches"},
            "scale": {"type": "number", "description": "percent of the picture's present size (instead of a width "
                                                       "or height)"},
            "tracked": {"type": "boolean", "enum": [False],
                        "description": "only false: Word cannot track a picture's size"},
            "allow_missing": {"type": "boolean", "default": False,
                              "description": "a file without a matching picture passes unchanged instead of being "
                                             "set aside"},
        },
        "required": ["match"],
    }
    select_schema = {"type": "object", "properties": {"scope": {"type": "array"}}}
    verification_profile = {
        "V1": "only the parts holding resized pictures",
        "V10": "resized_images re-derives the matches and the new sizes (its own reading of the present size and of the "
               "rule); every drawing of the part is identical but for the resized pictures' two sizes; the residual "
               "masks exactly those",
    }

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        scope = select.get("scope") or params.get("scope")
        number = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)        # noqa: E731
        width, height, scale = params.get("width"), params.get("height"), params.get("scale")
        if number(scale):
            to = f"to {scale:g}% of its present size"
        elif number(width) and number(height):
            to = f"to {length_words(width)} wide and {length_words(height)} high"
        elif number(width):
            to = f"to {length_words(width)} wide, keeping its proportions"
        elif number(height):
            to = f"to {length_words(height)} high, keeping its proportions"
        else:
            to = "to the size asked for"
        head = prose.sentence(f"Resize {match_words(params.get('match'))} {scope_words(scope)} {to}",
                              "(a picture's size is never a tracked change)")
        if params.get("allow_missing") is True:
            return head + " " + prose.sentence("files with no such picture are left unchanged")
        return head

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select, params = dict(select or {}), dict(params or {})
        if set(select) - {"scope"}:
            raise JobException(f"resize_images' selector takes only 'scope' (got {sorted(select)})", "bad-params")
        unknown = set(params) - set(PARAMS)
        if unknown:
            raise JobException(f"resize_images has no parameter(s) {sorted(unknown)}", "bad-params")
        if params.get("tracked", False) is not False:
            raise JobException("Word cannot track a picture's size: resize_images is untracked only (tracked: false)",
                               "bad-params")
        match = match_rule(params.get("match"), "resize_images")
        width, height = length_param(params.get("width"), "width"), length_param(params.get("height"), "height")
        scale = params.get("scale")
        if scale is not None:
            if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(scale) \
                    or not 0 < scale <= MAX_SCALE:
                raise JobException(f"'scale' must be a percentage above 0, up to {MAX_SCALE}", "bad-params")
            if width is not None or height is not None:
                raise JobException("give 'scale' or a width/height, not both", "bad-params")
        elif width is None and height is None:
            raise JobException("give 'width' and/or 'height' in inches, or 'scale' in percent", "bad-params")
        allow_missing = params.get("allow_missing", False)
        if not isinstance(allow_missing, bool):
            raise JobException("'allow_missing' must be true or false", "bad-params")
        scope = scope_of(select, params, "resize_images")
        parts = scope_parts(pkg, scope)
        md5_of = media_md5s(pkg)
        frag = self.fragment({"scope": scope} if given_scope(select, params) else {}, params)
        frag.geometry_reference = "none"
        frag.geometry_reason = "a picture of another size moves what follows it"
        expected: Dict[str, Dict[str, List[int]]] = {}
        already, skipped = 0, 0
        notes: List[str] = []
        for part in parts:
            frag.touches.append((part, "scope", "verify"))
            if match["how"] == "md5":
                frag.touches.append((pkg.rels_path_for(part), "*", "verify"))
            root = pkg.xml(part)
            found, n_skipped, _drawings = matched_pictures(pkg, part, root, match, md5_of)
            skipped += n_skipped
            sizes: Dict[str, List[int]] = {}
            blocks = set()
            for f in found:
                why = size_problem(f.drawing)
                if why:
                    raise JobException(f"{part}: picture {f.ordinal} cannot be resized: {why} — change it in Word",
                                       "structure", {"part": part, "picture": f.ordinal})
                ext, _ = size_elements(f.drawing)
                box = (int(ext.get("cx")), int(ext.get("cy")))
                size = new_size(box, width, height, scale)
                if size == box:
                    already += 1
                    continue
                sizes[str(f.ordinal)] = list(size)
                blocks.add(f.block)
                notes.append(f"{part} picture {f.ordinal}: {box[0]} × {box[1]} → {size[0]} × {size[1]} EMU")
            if sizes:
                expected[part] = sizes
                frag.intended_parts.append(part)
                frag.touches += [(part, str(bi), "drawing") for bi in sorted(blocks)]
        if skipped:
            frag.notes.append(f"resize_images: {skipped} watermark picture(s) left alone — a watermark is "
                              f"remove_watermark's")
        frag.property_assertions.append({
            "kind": "resized_images", "match": params.get("match"),
            "match_md5": match["md5"] if match.get("file") else None, "scope": given_scope(select, params),
            "width": width, "height": height, "scale": scale, "allow_missing": allow_missing, "expected": expected,
            "already": already, "watermarks": skipped})
        if not expected:
            if not already and not allow_missing:
                raise JobException(f"no picture in the {', '.join(scope)} matches — nothing to resize", "not-found",
                                   {"scope": scope})
            frag.notes.append("resize_images: " + (f"{already} picture(s) already that size; nothing to resize"
                                                   if already else "no matching picture in this file (allowed)"))
            return frag
        frag.notes.append(f"resize_images: {len(notes)} picture(s): " + "; ".join(notes)
                          + (f"; {already} already that size" if already else ""))
        frag.transform_data = {"parts": expected, "match": match}
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td:
            return
        if td["match"].get("file"):
            check_unchanged(td["match"]["file"], td["match"]["md5"], "resize_images.match.image")
        for part, sizes in td["parts"].items():
            root = pkg.xml(part)
            drawings = list(root.iter(W + "drawing"))
            for k, (cx, cy) in sizes.items():
                for el in size_elements(drawings[int(k)]):
                    el.set("cx", str(cx))
                    el.set("cy", str(cy))
            pkg.write_xml(part, root)
