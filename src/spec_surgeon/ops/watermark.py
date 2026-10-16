"""Ops: add_watermark — the user's words ("DRAFT", "NOT FOR CONSTRUCTION") drawn faintly behind every page, the way
Word's Design → Watermark writes them — and remove_watermark (design note §7). The verifier re-derives all of it with
its own code (verify/watermark.py, kinds ``added_watermark`` and ``removed_watermark``).

Definitions (CLAUDE.md "Watermarks", both sides):

* **A watermark** is a run of a header part whose one content element (any child but ``w:rPr``) is a ``w:pict``, a
  ``w:drawing`` or an ``mc:AlternateContent`` holding a shape named like Word's watermarks: a VML ``v:shape`` whose
  ``id`` starts with ``PowerPlusWaterMarkObject`` (text) or ``WordPictureWatermark`` (picture), or a DrawingML
  ``wp:docPr`` whose ``name`` does. Its **text**: the ``string`` of the first ``v:textpath`` that has one (a shape
  definition's ``v:textpath`` has none); else the ``w:t`` of its first ``w:txbxContent``; a picture has none. A
  watermark shape in a run holding anything else, outside a run, in a text box paragraph, or inside a pending tracked
  change is refused (``structure`` / ``existing-revisions``).
* **The headers** (add): every header part some section shows, any type (``hf_common.effective_refs``). A section page
  type that shows no header gets no watermark (flagged); no header part at all → ``no-header`` (rule 10: a header is
  never created). **The anchor**: the part's first ``w:p`` whose every container below ``w:hdr`` is a content control
  (``w:sdt``/``w:sdtContent``: Word wraps the paragraph it makes for a watermark in one) — never a table's or a text
  box's; the new run is its first child after ``w:pPr``. Taking a watermark out never takes out the only definition of a
  VML shapetype another shape of the part still uses (``structure``).
* **The run**: ``<w:r><w:rPr><w:noProof/></w:rPr><w:pict>`` + the ``v:shapetype`` ``_x0000_t136`` (only when the part
  has none outside the watermarks being replaced) + the ``v:shape`` Word writes for a text watermark (``build_run``):
  id ``PowerPlusWaterMarkObject<N>`` (N from the md5 of part and text, then the next number no member of the package
  holds), ``o:spid`` ``_x0000_s<M>`` (above every ``_x0000_s`` number in the package, from 2049, one per part),
  absolutely positioned, centred on the margins, behind the text, the words verbatim in ``v:textpath/@string``.
* **The size** (``box``): proportions h / w = 1.55 / the text's advance width in em (``WIDTHS``: Calibri, Arial, Times
  New Roman; another font measures with Calibri's widths and a character outside ASCII with the font's average — both
  flagged). The **text area** of a section: page width − left − right − gutter by page height − |top| − |bottom|
  (``text_area``, points). ``auto`` + diagonal: (w + h) / √2 = 0.98 × the shorter side of the smallest text area among
  the sections showing that header; ``auto`` + horizontal: w = min(0.9 × the narrowest width, 0.98 × the lowest height /
  the proportion); a size in points: w = advance × size, h = 1.55 × size, refused (``watermark-size`` — the file's page
  decides it, so it is the file's refusal, never the spec's) when it does not fit ((w + h) / √2 ≤ the shorter side;
  horizontal w ≤ the width, h ≤ the height). Every number is rounded to 2 decimals and written without trailing zeros
  (``number``).

``remove_watermark`` removes every watermark (or those whose text is ``text``, compared casefolded with whitespace
collapsed) from every header part of the document; the paragraph and any "Watermarks" content control around it stay, so
the header's layout does not move. ``allow_missing`` defaults to true: a file without one is a no-op.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from ..ooxml import block_full_text, block_struct_counts, paragraphs
from ..package import HEADER_REL, DocxPackage, W
from . import register
from .base import BlockDiff, JobException, Op, PlanFragment
from .hf_common import TYPES, effective_refs

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
V_NS = "urn:schemas-microsoft-com:vml"
O_NS = "urn:schemas-microsoft-com:office:office"
W10_NS = "urn:schemas-microsoft-com:office:word"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
V, O, W10, MC, WP = ("{%s}" % n for n in (V_NS, O_NS, W10_NS, MC_NS, WP_NS))
NSMAP = {"w": W_NS, "v": V_NS, "o": O_NS, "w10": W10_NS}

PREFIXES = ("PowerPlusWaterMarkObject", "WordPictureWatermark")
CONTENT_TAGS = (W + "pict", W + "drawing", MC + "AlternateContent")
REVISION_TAGS = (W + "ins", W + "del", W + "moveFrom", W + "moveTo")
SHAPETYPE = "_x0000_t136"
EM_HEIGHT = 1.55             # the box's height per em of font size (Word's own text watermarks: 1.5–1.6)
DIAGONAL_FILL = 0.98         # (w + h) / √2 as a share of the text area's shorter side
HORIZONTAL_FILL = 0.9        # w as a share of the text area's width
HEIGHT_FILL = 0.98
MAX_CHARS = 100
MAX_SIZE = 1000
LAYOUTS = ("diagonal", "horizontal")
DEFAULTS = {"font": "Calibri", "color": "C0C0C0", "opacity": 0.5, "layout": "diagonal", "size": "auto"}
Z_INDEX = "-251654144"       # behind the text, as Word places header shapes
FIRST_SPID = 2049
UNSEEN = ("Cc", "Cf", "Co", "Cs", "Cn", "Zl", "Zp")
FONT_FORBIDDEN = set('"\';<>&')

# Advance widths of ASCII 32–126 in 1/1000 em, from the metric-compatible fonts (Carlito for Calibri, Liberation Sans
# for Arial, Liberation Serif for Times New Roman) — measured once with PyMuPDF and kept here as data: nothing reads a
# font file at run time.
WIDTHS: Dict[str, Tuple[int, ...]] = {
    "calibri": (
        226, 326, 401, 498, 507, 715, 682, 221, 303, 303, 498, 498, 250, 306, 252, 386, 507, 507, 507,
        507, 507, 507, 507, 507, 507, 507, 268, 268, 498, 498, 498, 463, 894, 579, 544, 533, 615, 488,
        459, 631, 623, 252, 319, 520, 420, 855, 646, 662, 517, 673, 543, 459, 487, 642, 567, 890, 519,
        487, 468, 307, 386, 307, 498, 498, 291, 479, 525, 423, 525, 498, 305, 471, 525, 229, 239, 455,
        229, 799, 525, 527, 525, 525, 349, 391, 335, 525, 452, 715, 433, 453, 395, 314, 460, 314, 498,
    ),
    "arial": (
        278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556,
        556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667,
        611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667,
        667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500,
        222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584,
    ),
    "times new roman": (
        250, 333, 408, 500, 500, 833, 778, 180, 333, 333, 500, 564, 250, 333, 250, 278, 500, 500, 500,
        500, 500, 500, 500, 500, 500, 500, 278, 278, 564, 564, 564, 444, 921, 722, 667, 667, 722, 611,
        556, 722, 722, 333, 389, 722, 611, 889, 722, 722, 556, 722, 667, 556, 611, 722, 722, 944, 722,
        722, 611, 333, 278, 333, 469, 500, 333, 444, 500, 444, 500, 444, 333, 500, 500, 278, 278, 500,
        278, 778, 500, 500, 500, 500, 333, 389, 278, 500, 500, 722, 500, 500, 444, 480, 200, 480, 541,
    ),
}


# ---- the numbers -----------------------------------------------------------------------------------------------------

def number(x: float) -> str:
    """A length or an opacity as written: rounded to 2 decimals, no trailing zeros (412.4, 247.45, 100, 0.5)."""
    s = f"{round(float(x), 2):.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def advance(text: str, font: str) -> Tuple[float, List[str]]:
    """The text's advance width in em and what had to be estimated (an unknown font, characters outside ASCII)."""
    table = WIDTHS.get(font.casefold())
    notes = []
    if table is None:
        table = WIDTHS["calibri"]
        notes.append(f"the font {font!r} is measured with Calibri's widths")
    avg = sum(table) / len(table)
    total, odd = 0.0, []
    for ch in text:
        if 32 <= ord(ch) <= 126:
            total += table[ord(ch) - 32]
        else:
            total += avg
            odd.append(ch)
    if odd:
        notes.append(f"{''.join(dict.fromkeys(odd))!r} measured with the font's average width")
    return total / 1000.0, notes


def text_area(sect_pr) -> Optional[Tuple[float, float]]:
    """The section's text area in points: (page width − left − right − gutter, page height − |top| − |bottom|)."""
    sz, mar = sect_pr.find(W + "pgSz"), sect_pr.find(W + "pgMar")
    if sz is None or mar is None:
        return None
    try:
        g = lambda el, k: int(el.get(W + k) or 0)                       # noqa: E731
        width = g(sz, "w") - g(mar, "left") - g(mar, "right") - g(mar, "gutter")
        height = g(sz, "h") - abs(g(mar, "top")) - abs(g(mar, "bottom"))
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    return width / 20.0, height / 20.0


def box(text: str, font: str, layout: str, size: Any, area: Tuple[float, float]) -> Tuple[float, float, List[str]]:
    """(w, h, notes): the watermark's box in points for the smallest text area ``area`` (width, height)."""
    em, notes = advance(text, font)
    ratio = EM_HEIGHT / em
    aw, ah = area
    if size == "auto":
        if layout == "diagonal":
            w = DIAGONAL_FILL * min(aw, ah) * math.sqrt(2) / (1 + ratio)
        else:
            w = min(HORIZONTAL_FILL * aw, HEIGHT_FILL * ah / ratio)
        h = w * ratio
    else:
        w, h = em * float(size), EM_HEIGHT * float(size)
        fits = (w + h) / math.sqrt(2) <= min(aw, ah) if layout == "diagonal" else (w <= aw and h <= ah)
        if not fits:       # this file's page decides it, not the params: a file-specific refusal
            raise JobException(f"a {number(size)} pt watermark {text!r} ({number(w)} × {number(h)} pt) does not fit "
                               f"this file's text area of {number(aw)} × {number(ah)} pt — use a smaller size or "
                               f"'auto'", "watermark-size", {"size": size, "box": [round(w, 2), round(h, 2)]})
    return round(w, 2), round(h, 2), notes


# ---- the params ------------------------------------------------------------------------------------------------------

def add_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """The add_watermark params, checked, with the defaults spelled out."""
    text = params.get("text")
    if not isinstance(text, str) or not text.strip():
        raise JobException("add_watermark needs 'text': the user's exact words for the watermark", "bad-params")
    bad = sorted({f"U+{ord(c):04X}" for c in text if unicodedata.category(c) in UNSEEN})
    if bad:
        raise JobException(f"the watermark text {text!r} holds a control or invisible character ({', '.join(bad)}) — "
                           f"one line of visible words", "bad-params")
    if len(text) > MAX_CHARS:
        raise JobException(f"the watermark text is {len(text)} characters long (at most {MAX_CHARS})", "bad-params")
    font = params.get("font", DEFAULTS["font"])
    if not isinstance(font, str) or not font.strip() or len(font) > 64 or \
            any(c in FONT_FORBIDDEN or unicodedata.category(c) in UNSEEN for c in font):
        raise JobException(f"'font' must be a font name (no quotes, semicolons or angle brackets), got {font!r}",
                           "bad-params")
    color = params.get("color", DEFAULTS["color"])
    if not isinstance(color, str) or not re.fullmatch(r"#?[0-9A-Fa-f]{6}", color.strip()):
        raise JobException(f"'color' must be 6 hex digits (C0C0C0 is silver), got {color!r}", "bad-params")
    opacity = params.get("opacity", DEFAULTS["opacity"])
    if isinstance(opacity, bool) or not isinstance(opacity, (int, float)) or not 0.05 <= opacity <= 1:
        raise JobException(f"'opacity' must be a number from 0.05 to 1, got {opacity!r}", "bad-params")
    layout = params.get("layout", DEFAULTS["layout"])
    if layout not in LAYOUTS:
        raise JobException(f"'layout' must be one of {list(LAYOUTS)}, got {layout!r}", "bad-params")
    size = params.get("size", DEFAULTS["size"])
    if size != "auto" and (isinstance(size, bool) or not isinstance(size, (int, float)) or not 0 < size <= MAX_SIZE):
        raise JobException(f"'size' must be 'auto' or a font size in points (up to {MAX_SIZE}), got {size!r}",
                           "bad-params")
    replace = params.get("replace", False)
    if not isinstance(replace, bool):
        raise JobException("'replace' must be true or false", "bad-params")
    return {"text": text, "font": font.strip(), "color": color.strip().lstrip("#").upper(), "opacity": float(opacity),
            "layout": layout, "size": size, "replace": replace}


# ---- watermarks found ----------------------------------------------------------------------------------------------

def nearest(el, tag: str):
    q = el.getparent()
    while q is not None and q.tag != tag:
        q = q.getparent()
    return q


def watermark_text(element) -> Optional[str]:
    """The words: the first ``v:textpath`` that carries a ``string`` (a shape definition's own ``v:textpath`` carries
    none), else the ``w:t`` of the first text box, else None (a picture)."""
    tp = next((t for t in element.iter(V + "textpath") if t.get("string") is not None), None)
    if tp is not None:
        return tp.get("string")
    box_ = next(element.iter(W + "txbxContent"), None)
    if box_ is not None:
        return "".join(t.text or "" for t in box_.iter(W + "t"))
    return None


def same_text(a: Optional[str], b: str) -> bool:
    return a is not None and " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def watermark_runs(root, part: str) -> List[Dict[str, Any]]:
    """Every watermark of a header part: {run, element, paragraph, text} in document order (refusals as the docstring
    says)."""
    found: List[Dict[str, Any]] = []
    seen = []
    for marker in root.iter(V + "shape", WP + "docPr"):
        name = marker.get("id") if marker.tag == V + "shape" else marker.get("name")
        if not name or not name.startswith(PREFIXES):
            continue
        run = nearest(marker, W + "r")
        if run is None:
            raise JobException(f"{part}: a watermark shape ({name}) that is not in a run", "structure",
                               {"part": part})
        if any(run is r for r in seen):
            continue
        content = [c for c in run if isinstance(c.tag, str) and c.tag != W + "rPr"]
        if len(content) != 1 or content[0].tag not in CONTENT_TAGS:
            raise JobException(f"{part}: the watermark {name} shares its run with other content — take it out in "
                               f"Word (Design → Watermark → Remove Watermark)", "structure", {"part": part})
        p = nearest(run, W + "p")
        if p is None or nearest(p, W + "p") is not None:
            raise JobException(f"{part}: the watermark {name} is not in a paragraph of the header itself", "structure",
                               {"part": part})
        q = run.getparent()
        while q is not None and q is not p:
            if q.tag in REVISION_TAGS:
                raise JobException(f"{part}: the watermark {name} is part of a pending tracked change — accept or "
                                   f"reject it first", "existing-revisions", {"part": part})
            q = q.getparent()
        seen.append(run)
        found.append({"run": run, "element": content[0], "paragraph": p, "text": watermark_text(content[0])})
    return found


def shapetype_left(root, removed_runs) -> bool:
    """The part keeps a ``_x0000_t136`` shapetype outside the runs taken out."""
    for st in root.iter(V + "shapetype"):
        if st.get("id") == SHAPETYPE and not any(_inside(st, r) for r in removed_runs):
            return True
    return False


def _inside(el, ancestor) -> bool:
    q = el
    while q is not None:
        if q is ancestor:
            return True
        q = q.getparent()
    return False


def check_shapetype_users(root, removed_runs, part: str, brings=()) -> None:
    """Refuse taking out the only definition of a VML shapetype (``v:shapetype``, by id) that a shape staying in the
    part still uses (``type="#<id>"``) — unless the op puts that definition back (``brings``)."""
    gone = {st.get("id") for r in removed_runs for st in r.iter(V + "shapetype") if st.get("id")} - set(brings)
    for sid in sorted(gone):
        if any(st.get("id") == sid and not any(_inside(st, r) for r in removed_runs)
               for st in root.iter(V + "shapetype")):
            continue
        for el in root.iter():
            if isinstance(el.tag, str) and el.get("type") == "#" + sid \
                    and not any(_inside(el, r) for r in removed_runs):
                raise JobException(f"{part}: another shape uses the shape definition ({sid}) a watermark carries; "
                                   f"taking the watermark out would break it — remove it in Word", "structure",
                                   {"part": part, "shapetype": sid})


def run_locator(paragraph_index: int, p, run) -> List[int]:
    """(paragraph index, ordinal of the run among the paragraph's runs) — found again in apply."""
    runs = [r for r in p.iter(W + "r") if nearest(r, W + "p") is p]
    return [paragraph_index, next(k for k, r in enumerate(runs) if r is run)]


def locate(pars, loc) -> Any:
    p = pars[loc[0]]
    runs = [r for r in p.iter(W + "r") if nearest(r, W + "p") is p]
    return runs[loc[1]]


def plan_removals(frag: PlanFragment, part: str, root, found: List[Dict[str, Any]]) -> Tuple[List[List[int]], bool]:
    """Predict taking the ``found`` watermark runs out of one part: their own paragraphs' structure (a structure-only
    declaration when a w:drawing goes), the text-box paragraphs inside them (deleted blocks). Returns (locators,
    whether blocks go)."""
    pars = paragraphs(root)
    index = {id(p): k for k, p in enumerate(pars)}            # pars held: the proxies (and their ids) stay
    locs: List[List[int]] = []
    by_p: Dict[int, List[Any]] = {}
    blocks_go = False
    for f in found:
        bi = index[id(f["paragraph"])]
        locs.append(run_locator(bi, f["paragraph"], f["run"]))
        by_p.setdefault(bi, []).append(f["run"])
        for nested in f["run"].iter(W + "p"):
            blocks_go = True
            frag.diffs.append(BlockDiff(part=part, block_index=index[id(nested)], before=block_full_text(nested),
                                        after=None, note="text box paragraph of a removed watermark"))
    for bi, runs in by_p.items():
        p = pars[bi]
        before, after = block_struct_counts(p), block_struct_counts(p, runs)
        if before != after:
            text = block_full_text(p)
            frag.diffs.append(BlockDiff(part=part, block_index=bi, before=text, after=text, struct_after=after,
                                        note="watermark taken out (structure only)"))
        frag.touches.append((part, str(bi), "text"))
    if blocks_go:
        frag.touches.append((part, "*", "text"))
        frag.untracked_diffs_expected = True
    return locs, blocks_go


def remove_located(root, locs: List[List[int]]) -> None:
    pars = paragraphs(root)
    runs = [locate(pars, loc) for loc in locs]                 # all found before any is taken out
    for r in runs:
        if r.find(W + "pict") is None and r.find(W + "drawing") is None and r.find(MC + "AlternateContent") is None:
            raise JobException("internal: a planned watermark run is not where the plan found it", "internal")
        r.getparent().remove(r)


# ---- the run ---------------------------------------------------------------------------------------------------------

_FORMULAS = ("sum #0 0 10800", "prod #0 2 1", "sum 21600 0 @1", "sum 0 0 @2", "sum 21600 0 @3", "if @0 @3 0",
             "if @0 21600 @1", "if @0 0 @2", "if @0 @4 21600", "mid @5 @6", "mid @8 @5", "mid @7 @8", "mid @6 @7",
             "sum @6 0 @5")


def shape_style(w: float, h: float, layout: str) -> str:
    return ("position:absolute;margin-left:0;margin-top:0;" f"width:{number(w)}pt;height:{number(h)}pt;"
            + ("rotation:315;" if layout == "diagonal" else "")
            + f"z-index:{Z_INDEX};mso-position-horizontal:center;mso-position-horizontal-relative:margin;"
              "mso-position-vertical:center;mso-position-vertical-relative:margin")


def build_run(p: Dict[str, Any], w: float, h: float, shape_id: str, spid: str, with_shapetype: bool):
    """The watermark run (``p``: add_params)."""
    r = etree.Element(W + "r", nsmap=NSMAP)
    etree.SubElement(etree.SubElement(r, W + "rPr"), W + "noProof")
    pict = etree.SubElement(r, W + "pict")
    if with_shapetype:
        st = etree.SubElement(pict, V + "shapetype", {"id": SHAPETYPE, "coordsize": "21600,21600", O + "spt": "136",
                                                      "adj": "10800", "path": "m@7,l@8,m@5,21600l@6,21600e"})
        fs = etree.SubElement(st, V + "formulas")
        for eqn in _FORMULAS:
            etree.SubElement(fs, V + "f", {"eqn": eqn})
        etree.SubElement(st, V + "path", {"textpathok": "t", O + "connecttype": "custom",
                                          O + "connectlocs": "@9,0;@10,10800;@11,21600;@12,10800",
                                          O + "connectangles": "270,180,90,0"})
        etree.SubElement(st, V + "textpath", {"on": "t", "fitshape": "t"})
        hs = etree.SubElement(st, V + "handles")
        etree.SubElement(hs, V + "h", {"position": "#0,bottomRight", "xrange": "6629,14971"})
        etree.SubElement(st, O + "lock", {V + "ext": "edit", "text": "t", "shapetype": "t"})
    shape = etree.SubElement(pict, V + "shape", {"id": shape_id, O + "spid": spid, "type": "#" + SHAPETYPE,
                                                 "style": shape_style(w, h, p["layout"]), O + "allowincell": "f",
                                                 "fillcolor": "#" + p["color"], "stroked": "f"})
    etree.SubElement(shape, V + "fill", {"opacity": number(p["opacity"])})
    etree.SubElement(shape, V + "textpath", {"style": f'font-family:"{p["font"]}";font-size:1pt', "string": p["text"]})
    etree.SubElement(shape, W10 + "wrap", {"anchorx": "margin", "anchory": "margin"})
    return r


# ---- the package's ids and headers -----------------------------------------------------------------------------------

def package_ids(pkg: DocxPackage) -> Tuple[set, int]:
    """(every PowerPlusWaterMarkObject number in the package, the highest _x0000_s number or 2048)."""
    numbers, top = set(), FIRST_SPID - 1
    for name in pkg.member_names:
        if not name.endswith((".xml", ".rels")):
            continue
        data = pkg.read(name)
        numbers |= {int(m) for m in re.findall(rb"PowerPlusWaterMarkObject(\d+)", data)}
        top = max([top] + [int(m) for m in re.findall(rb"_x0000_s(\d+)", data)])
    return numbers, top


def first_paragraph(root) -> Optional[Any]:
    """The header's first ``w:p`` whose every container below the header is a content control (``w:sdt`` /
    ``w:sdtContent`` — Word wraps the paragraph it makes for a watermark in one): never a table's or a text box's."""
    for p in root.iter(W + "p"):
        q = p.getparent()
        while q is not None and q is not root and q.tag in (W + "sdt", W + "sdtContent"):
            q = q.getparent()
        if q is root:
            return p
    return None


def headers_shown(pkg: DocxPackage) -> Tuple[List[str], Dict[str, List[int]], List[str]]:
    """(header parts some section shows, in first-shown order; the sections showing each; what shows no header)."""
    main = pkg.main_part()
    secs = DocxPackage.sect_prs(pkg.xml(main))
    shown = effective_refs(pkg, secs)
    headers = {r.resolved for r in pkg.rels(main).values() if r.type == HEADER_REL and r.resolved}
    even_odd = pkg.settings_flag("evenAndOddHeaders")
    order: List[str] = []
    showing: Dict[str, List[int]] = {}
    gaps: List[str] = []
    for i, (sp, cur) in enumerate(zip(secs, shown)):
        tp = sp.find(W + "titlePg")
        title = tp is not None and tp.get(W + "val") not in ("0", "false", "off")
        for typ in TYPES:
            part = cur.get(("header", typ))
            if part is not None and part in headers and pkg.has(part):
                if part not in showing:
                    order.append(part)
                    showing[part] = []
                if i not in showing[part]:
                    showing[part].append(i)
            elif typ == "default" or (typ == "first" and title) or (typ == "even" and even_odd):
                what = {"default": "pages show", "first": "first page shows", "even": "even pages show"}[typ]
                gaps.append(f"section {i + 1}'s {what} no header: no watermark there (add a header in Word first)")
    return order, showing, gaps


# ---- the ops ---------------------------------------------------------------------------------------------------------

@register
class AddWatermark(Op):
    name = "add_watermark"
    mode = "untracked"
    description = ("Draw the user's words (\"DRAFT\", \"NOT FOR CONSTRUCTION\") faintly behind every page, as Word's "
                   "watermark: into every header the document shows, diagonal (default) or horizontal, silver and half "
                   "transparent unless asked otherwise, sized to fit the page. A file that already has a watermark is "
                   "refused unless replace is true. The words are placed exactly as given.")
    params_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "the user's exact words (one line, at most 100 characters)"},
            "font": {"type": "string", "default": DEFAULTS["font"]},
            "color": {"type": "string", "default": DEFAULTS["color"], "description": "6 hex digits (C0C0C0 = silver)"},
            "opacity": {"type": "number", "default": DEFAULTS["opacity"], "description": "0.05–1 (0.5 = half)"},
            "layout": {"type": "string", "enum": list(LAYOUTS), "default": DEFAULTS["layout"]},
            "size": {"type": ["string", "number"], "default": "auto",
                     "description": "'auto' (as large as fits the page) or a font size in points"},
            "replace": {"type": "boolean", "default": False,
                        "description": "take out a watermark the file already has (else the file is set aside)"},
        },
        "required": ["text"],
    }
    select_schema = {"type": "object", "description": "no selector: every header the document shows"}
    verification_profile = {
        "V1": "the header parts that get the watermark",
        "V2": "no text changes (the words are in the shape, not in the text); replaced DrawingML watermarks' text-box "
              "paragraphs listed as deleted",
        "V10": "added_watermark re-derives the headers and their first paragraphs, builds Word's watermark shape again "
               "from the asserted words, font, colour, opacity, layout and size with its own template, checks the ids "
               "new and unique and the box inside every section's text area, and each paragraph item by item",
    }

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        if select:
            raise JobException("add_watermark takes no selector: it goes into every header the document shows",
                               "bad-params")
        p = add_params(params)
        parts, showing, gaps = headers_shown(pkg)
        if not parts:
            raise JobException("this file has no header to carry a watermark; a header is never created — add one in "
                               "Word first", "no-header")
        main = pkg.main_part()
        secs = DocxPackage.sect_prs(pkg.xml(main))
        taken, top = package_ids(pkg)
        frag = self.fragment({}, params, mode="untracked")
        frag.untracked_diffs_expected = True
        frag.geometry_reference = "none"
        frag.geometry_reason = "the watermark is drawn over every page"
        frag.flags.extend(gaps)
        assertion: Dict[str, Any] = {"kind": "added_watermark", "text": params.get("text"),
                                     **{k: params[k] for k in ("font", "color", "opacity", "layout", "size", "replace")
                                        if k in params}, "parts": {}}
        td_parts: Dict[str, Any] = {}
        notes, replaced = [], 0
        for part in parts:
            root = pkg.xml(part)
            found = watermark_runs(root, part)
            if found and not p["replace"]:
                words = ", ".join(repr(f["text"]) if f["text"] is not None else "a picture" for f in found)
                raise JobException(f"{part} already has a watermark ({words}); say replace to swap it for "
                                   f"{p['text']!r}, or remove it first", "watermark-exists", {"part": part})
            anchor = first_paragraph(root)
            if anchor is None:
                raise JobException(f"{part} has no paragraph of its own to carry the watermark", "structure",
                                   {"part": part})
            areas = []
            for i in showing[part]:
                area = text_area(secs[i])
                if area is None:
                    raise JobException(f"section {i + 1} states no usable page size and margins; the watermark cannot "
                                       f"be sized", "structure", {"section": i})
                areas.append(area)
                frag.touches += [(main, f"sectPr:{i}", "verify:pgSz"), (main, f"sectPr:{i}", "verify:pgMar"),
                                 (main, f"sectPr:{i}", "verify:hfref")]
            smallest = (min(a[0] for a in areas), min(a[1] for a in areas))
            w, h, how = box(p["text"], p["font"], p["layout"], p["size"], smallest)
            notes += [n for n in how if n not in notes]
            runs_out = [f["run"] for f in found]
            check_shapetype_users(root, runs_out, part, brings=(SHAPETYPE,))
            with_type = not shapetype_left(root, runs_out)
            n = int(hashlib.md5(f"{part}\x00{p['text']}".encode("utf-8")).hexdigest()[:8], 16) % 900000000 + 100000000
            while n in taken:
                n += 1
            taken.add(n)
            top += 1
            locs, _ = plan_removals(frag, part, root, found) if found else ([], False)
            replaced += len(found)
            bi = next(k for k, q in enumerate(paragraphs(root)) if q is anchor)
            frag.touches.append((part, str(bi), "text"))
            frag.intended_parts.append(part)
            td_parts[part] = {"anchor": bi, "box": [w, h], "id": f"PowerPlusWaterMarkObject{n}",
                              "spid": f"_x0000_s{top}", "shapetype": with_type, "remove": locs}
            assertion["parts"][part] = {"anchor": bi, "box": [w, h], "shapetype": with_type, "replaced": len(found)}
        frag.property_assertions.append(assertion)
        frag.transform_data = {"params": p, "parts": td_parts}
        frag.notes.append(f"add_watermark: {p['text']!r} ({p['layout']}, {p['font']}, #{p['color']}, opacity "
                          f"{number(p['opacity'])}) in {len(parts)} header part(s): "
                          + "; ".join(f"{part} {number(d['box'][0])} × {number(d['box'][1])} pt"
                                      for part, d in td_parts.items())
                          + (f"; {replaced} old watermark(s) replaced" if replaced else ""))
        if notes:
            frag.flags.append("add_watermark: the size is estimated — " + "; ".join(notes))
        frag.flags.append(f"add_watermark places the words {p['text']!r} on every page (untracked: a watermark is not "
                          f"a tracked change).")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td:
            return
        p = td["params"]
        for part, d in td["parts"].items():
            root = pkg.xml(part)
            anchor = paragraphs(root)[d["anchor"]]
            remove_located(root, d["remove"])
            ppr = anchor.find(W + "pPr")
            at = list(anchor).index(ppr) + 1 if ppr is not None else 0
            anchor.insert(at, build_run(p, d["box"][0], d["box"][1], d["id"], d["spid"], d["shapetype"]))
            pkg.write_xml(part, root)


@register
class RemoveWatermark(Op):
    name = "remove_watermark"
    mode = "formatting"
    description = ("Take the watermark (Word's Design → Watermark: text or picture) out of every header; with 'text', "
                   "only watermarks showing those words. The header's paragraphs stay, so nothing moves. A file "
                   "without a watermark is left as it is (allow_missing defaults to true).")
    params_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "only the watermarks showing these words (any case or spacing)"},
            "allow_missing": {"type": "boolean", "default": True,
                              "description": "true (default): a file without a watermark is a no-op; false: it is "
                                             "set aside"},
        },
    }
    select_schema = {"type": "object", "description": "no selector: every header part"}
    verification_profile = {
        "V2": "no text changes, unless a DrawingML watermark's text-box paragraphs go (listed as deleted)",
        "V3": "a structure-only declaration when a w:drawing goes",
        "V10": "removed_watermark finds every watermark again with its own reading and checks each paragraph item by "
               "item: exactly those runs gone",
    }

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        if select:
            raise JobException("remove_watermark takes no selector: it looks in every header part", "bad-params")
        text = params.get("text")
        if text is not None and (not isinstance(text, str) or not text.strip()):
            raise JobException("'text' must be the watermark's words", "bad-params")
        allow_missing = params.get("allow_missing", True)
        if not isinstance(allow_missing, bool):
            raise JobException("'allow_missing' must be true or false", "bad-params")
        frag = self.fragment({}, params)
        frag.geometry_reference = "none"
        frag.geometry_reason = "the removed watermark was drawn over every page"
        expected: Dict[str, List[List[int]]] = {}
        td: Dict[str, List[List[int]]] = {}
        texts: List[str] = []
        for part in pkg.parts_by_kind()["headers"]:
            root = pkg.xml(part)
            found = [f for f in watermark_runs(root, part) if text is None or same_text(f["text"], text)]
            if not found:
                continue
            check_shapetype_users(root, [f["run"] for f in found], part)
            locs, _ = plan_removals(frag, part, root, found)
            expected[part] = locs
            td[part] = locs
            texts += [repr(f["text"]) if f["text"] is not None else "a picture" for f in found]
            frag.intended_parts.append(part)
        frag.property_assertions.append({"kind": "removed_watermark", "text": text, "expected": expected})
        if not td:
            if not allow_missing:
                raise JobException("no watermark" + (f" showing {text!r}" if text else "") + " in any header",
                                   "not-found", {"text": text})
            frag.notes.append("remove_watermark: no watermark" + (f" showing {text!r}" if text else "")
                              + ": nothing to do")
            return frag
        frag.transform_data = {"parts": td}
        frag.notes.append(f"remove_watermark: {sum(len(v) for v in td.values())} watermark(s) taken out of "
                          f"{len(td)} header part(s): {', '.join(dict.fromkeys(texts))}")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td:
            return
        for part, locs in td["parts"].items():
            root = pkg.xml(part)
            remove_located(root, locs)
            pkg.write_xml(part, root)
