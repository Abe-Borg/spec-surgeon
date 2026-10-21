"""Pictures in a document — the transform side's shared core for insert_image, remove_images and resize_images (built-in
batch 6, docs/BUILTIN_OPS_BATCH6_DESIGN.md §5). The verifier reads all of it again with its own code (verify/pictures.py).

Definitions (both sides; the picture and its ``match`` are swap_image's, ops/swap_image.py):

* a **picture** is a ``w:drawing`` whose ``wp:inline`` / ``wp:anchor`` holds ``a:graphic/a:graphicData/pic:pic`` with
  ``pic:blipFill/a:blip``; its **ordinal** is its place among every ``w:drawing`` of the part (document order), its
  **paragraph** the nearest ``w:p`` around it;
* the **references** of a part are its ``a:blip`` and ``v:imagedata`` elements; a reference inside a **watermark** (a
  ``w:drawing`` whose ``wp:docPr`` name, or a VML ``v:shape`` whose id, starts ``PowerPlusWaterMarkObject`` or
  ``WordPictureWatermark``) is never matched — watermarks are remove_watermark's (noted);
* **match**: ``{"image": file}`` / ``{"md5": …}`` — the media the blip's ``r:embed`` relationship targets has that md5 (a
  linked picture never matches); ``{"name_regex": …}`` — searched in ``wp:docPr`` name, descr and title (VML: the
  imagedata's ``o:title`` and its shape's ``alt``); ``"all"`` — every reference;
* a matched reference that is not a picture's own (VML, a shape's picture fill, a picture inside a group or canvas) is
  refused (``structure``: "change this one in Word"), and so is a matched picture inside ``mc:AlternateContent``
  (``structure``) or in a text box — a paragraph inside another one (``textbox``);
* a picture lies **in a field** when it sits inside a ``w:fldSimple`` or between a complex field's ``begin`` and its
  ``end`` (``w:fldChar`` elements of the part in document order, nested fields counted).

The rest is each op's own (its module docstring).
"""
from __future__ import annotations

import hashlib
import posixpath
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from lxml import etree

from ..ooxml import paragraphs
from ..package import CT, CT_PART, DocxPackage
from . import prose
from .base import JobException
from .images import file_md5
from .swap_image import A, MC, R, V, W, WP, enclosing, names_of, picture_of, ref_id

WATERMARK_NAMES = ("PowerPlusWaterMarkObject", "WordPictureWatermark")
SCOPES = ("document", "headers", "footers")
DEFAULT_SCOPE = ["document"]
REVISION_TAGS = (W + "ins", W + "del", W + "moveFrom", W + "moveTo")


def match_rule(raw: Any, what: str) -> Dict[str, Any]:
    """The match as the plan pins it: {"how": "md5", "md5", "file"?} | {"how": "name", "name_regex"} | {"how": "all"}."""
    if raw == "all":
        return {"how": "all"}
    if isinstance(raw, dict) and len(raw) == 1:
        (k, v), = raw.items()
        if k == "image" and isinstance(v, str) and v.strip():
            return {"how": "md5", "md5": file_md5(v, f"{what}.match.image"), "file": v}
        if k == "md5" and isinstance(v, str) and re.fullmatch(r"[0-9a-fA-F]{32}", v):
            return {"how": "md5", "md5": v.lower()}
        if k == "name_regex" and isinstance(v, str) and v:
            try:
                re.compile(v)
            except re.error as e:
                raise JobException(f"{what}.match.name_regex: bad pattern ({e})", "bad-params")
            return {"how": "name", "name_regex": v}
    raise JobException("'match' must be {'image': '<the picture file>'}, {'md5': '<32 hex digits>'}, "
                       "{'name_regex': '<regex on the picture's name/description>'} or 'all'", "bad-params")


def given_scope(select: Dict[str, Any], params: Dict[str, Any]) -> Any:
    """The scope as the user gave it (``select.scope`` or ``params.scope``), None when neither does — what the V10
    assertion carries, so the verifier applies the default itself."""
    return select.get("scope") or params.get("scope") or None


def scope_of(select: Dict[str, Any], params: Dict[str, Any], what: str) -> List[str]:
    """The kinds of part in scope: ``select.scope`` or ``params.scope`` (the two must agree), the document body by
    default."""
    if select.get("scope") == [] or params.get("scope") == []:
        raise JobException("scope is an empty list; leave it out for the document body, or name the parts", "bad-params")
    if select.get("scope") and params.get("scope") and list(select["scope"]) != list(params["scope"]):
        raise JobException("select.scope and params.scope disagree", "bad-params")
    scope = given_scope(select, params) or DEFAULT_SCOPE
    if isinstance(scope, str):
        scope = [scope]
    if not isinstance(scope, list) or not all(s in SCOPES for s in scope):
        raise JobException(f"{what}'s scope is a list of {list(SCOPES)}", "bad-params")
    return list(dict.fromkeys(scope))


def in_watermark(ref) -> bool:
    """Whether an image reference belongs to a watermark (module docstring)."""
    q = ref
    while q is not None:
        if q.tag == V + "shape" and (q.get("id") or "").startswith(WATERMARK_NAMES):
            return True
        if q.tag == W + "drawing":
            for frame in q:
                dp = frame.find(WP + "docPr") if frame.tag in (WP + "inline", WP + "anchor") else None
                if dp is not None and (dp.get("name") or "").startswith(WATERMARK_NAMES):
                    return True
        q = q.getparent()
    return False


def nearest_paragraph(el):
    return enclosing(el, W + "p")


def field_drawings(root) -> Set[int]:
    """id()s of the w:drawing elements of ``root`` that lie in a field (module docstring). The caller keeps the drawings
    alive (lxml's proxies, and so their ids, live as long as someone holds them)."""
    out: Set[int] = set()
    depth = 0
    for el in root.iter(W + "fldChar", W + "drawing"):
        if el.tag == W + "fldChar":
            kind = el.get(W + "fldCharType")
            if kind == "begin":
                depth += 1
            elif kind == "end" and depth:
                depth -= 1
        elif depth or enclosing(el, W + "fldSimple") is not None:
            out.add(id(el))
    return out


def in_revision(el, p) -> bool:
    """Whether ``el`` lies inside a pending tracked change below its paragraph ``p``."""
    q = el.getparent()
    while q is not None and q is not p:
        if q.tag in REVISION_TAGS:
            return True
        q = q.getparent()
    return False


class Found:
    """A matched picture of a part: its drawing, own blip, ordinal, paragraph and that paragraph's index."""
    __slots__ = ("drawing", "blip", "ordinal", "paragraph", "block")

    def __init__(self, drawing, blip, ordinal: int, paragraph, block: int):
        self.drawing, self.blip, self.ordinal, self.paragraph, self.block = drawing, blip, ordinal, paragraph, block


def media_md5s(pkg: DocxPackage) -> Callable[[Optional[str]], Optional[str]]:
    """md5 of a media member by name (None for a target that is not a member), each member read once."""
    seen: Dict[str, Optional[str]] = {}

    def md5_of(target: Optional[str]) -> Optional[str]:
        if target is None or not pkg.has(target):
            return None
        if target not in seen:
            seen[target] = hashlib.md5(pkg.read(target)).hexdigest()
        return seen[target]
    return md5_of


def matched_pictures(pkg: DocxPackage, part: str, root, match: Dict[str, Any],
                     md5_of: Callable[[Optional[str]], Optional[str]]) -> Tuple[List[Found], int, List[Any]]:
    """(the pictures of the part the match selects in ordinal order, how many watermark references it skipped, the
    part's drawings — keep them while the Found are used). Refusals as the module docstring says."""
    rels = pkg.rels(part)
    drawings = list(root.iter(W + "drawing"))
    ordinal = {id(d): k for k, d in enumerate(drawings)}
    pars = paragraphs(root)
    index = {id(p): k for k, p in enumerate(pars)}
    rx = re.compile(match["name_regex"]) if match["how"] == "name" else None
    found: Dict[int, Found] = {}
    skipped = 0
    for ref in list(root.iter(A + "blip", V + "imagedata")):
        rid, linked = ref_id(ref)
        rel = rels.get(rid) if rid else None
        target = rel.resolved if rel is not None and rel.target_mode == "Internal" else None
        if match["how"] == "md5":
            hit = not linked and md5_of(target) == match["md5"]
        elif match["how"] == "name":
            hit = any(rx.search(n) for n in names_of(ref))
        else:
            hit = True
        if not hit:
            continue
        if in_watermark(ref):
            skipped += 1
            continue
        drawing = picture_of(ref) if ref.tag == A + "blip" else None
        if drawing is None:
            what = "a VML picture" if ref.tag == V + "imagedata" else "a picture inside a shape, group or canvas"
            raise JobException(f"{part}: a matching picture is {what} — change this one in Word, or match the "
                               f"pictures by name or file", "structure", {"part": part})
        k = ordinal[id(drawing)]
        if enclosing(ref, MC + "AlternateContent") is not None:
            raise JobException(f"{part}: picture {k} lies inside mc:AlternateContent (Word keeps a second copy of it) "
                               f"— change this one in Word", "structure", {"part": part, "picture": k})
        p = nearest_paragraph(drawing)
        if p is None or nearest_paragraph(p) is not None:
            raise JobException(f"{part}: picture {k} is in a text box — change this one in Word", "textbox",
                               {"part": part, "picture": k})
        found[k] = Found(drawing, ref, k, p, index[id(p)])
    return [found[k] for k in sorted(found)], skipped, drawings


def r_values(el) -> Set[str]:
    """Every value of an attribute in the relationships namespace on ``el`` and below it."""
    return {v for e in el.iter() if isinstance(e.tag, str) for k, v in e.attrib.items() if k.startswith(R)}


def free_media_name(pkg: DocxPackage, ext: str) -> str:
    """``word/media/image<N>.<ext>`` (beside the main part) with the smallest N that no member and no content-type
    override holds (any case)."""
    _, overrides = pkg.content_types()
    taken = {n.casefold() for n in pkg.member_names} | {n.casefold() for n in overrides}
    base = posixpath.join(posixpath.dirname(pkg.main_part()), "media")
    n = 1
    while posixpath.join(base, f"image{n}.{ext}").casefold() in taken:
        n += 1
    return posixpath.join(base, f"image{n}.{ext}")


def max_docpr_id(pkg: DocxPackage) -> int:
    """The highest ``wp:docPr`` id of every text part (0 when there is none; an id that is not a number is skipped)."""
    top = 0
    for part in pkg.text_parts():
        for e in pkg.xml(part).iter(WP + "docPr"):
            try:
                top = max(top, int(e.get("id")))
            except (TypeError, ValueError):
                pass
    return top


def next_rid(pkg: DocxPackage, part: str) -> str:
    """The id ``DocxPackage.edit_rels`` gives the next relationship of ``part``."""
    nums = [int(rid[3:]) for rid in pkg.rels(part) if rid.startswith("rId") and rid[3:].isdigit()]
    return f"rId{max(nums, default=0) + 1}"


def ct_change(pkg: DocxPackage, ext: str, content_type: str) -> Optional[str]:
    """How [Content_Types].xml must change for a new member with this extension: None, "default" or "override"."""
    defaults, _ = pkg.content_types()
    if defaults.get(ext) == content_type:
        return None
    return "default" if ext not in defaults else "override"


def add_default(pkg: DocxPackage, ext: str, content_type: str) -> None:
    """A ``Default`` for the extension, after the last Default of [Content_Types].xml."""
    root = pkg.xml(CT_PART)
    el = etree.Element(CT + "Default")
    el.set("Extension", ext)
    el.set("ContentType", content_type)
    last = root.findall(CT + "Default")
    if last:
        last[-1].addnext(el)
    else:
        root.insert(0, el)
    pkg.write_xml(CT_PART, root)


def length_words(inches: Any) -> str:
    """A length the user gave in inches, in the plan's words."""
    return prose.inches(float(inches) * prose.TWIPS_PER_INCH)


def match_words(match: Any) -> str:
    """The pictures the match chooses, in the plan's words."""
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
    return "the matching pictures"


def scope_words(scope: Any) -> str:
    """``in the document body`` (the default), ``in the headers``, ``in the document body and the footers``."""
    kinds = [scope] if isinstance(scope, str) else list(scope or DEFAULT_SCOPE)
    return "in the document body" if kinds == ["document"] else prose.scope_words(kinds)
