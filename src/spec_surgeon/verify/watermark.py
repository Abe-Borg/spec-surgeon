"""V10 kinds ``added_watermark`` and ``removed_watermark`` — built-in batch 2, verifier side
(docs/BUILTIN_OPS_BATCH2_DESIGN.md §7; CLAUDE.md "Watermarks"), independent of ops/watermark.py (rule 1): this module's
own reading of which headers a document shows, which run is a watermark, what Word's text watermark looks like (its own
template, written as XML text), and how large it may be.

* ``added_watermark``: the header parts some section shows (any type) and each one's first direct paragraph, derived
  here; the new run is that paragraph's first child after ``w:pPr``; its ids are read from the output and must have the
  right form, be absent from the source package and occur once in the output; the expected ``w:pict`` is built here from
  the user's params (this module's defaults) and the asserted box (the shapetype only when the part has none outside the
  replaced watermarks); the paragraph's items must be the source's (replaced watermark runs dropped) with that one run
  item first; the box must lie inside every showing section's text area and, ``auto``, fill it as the rule says.
* ``removed_watermark``: every watermark of every header part (with ``text``, only those showing those words) found
  here; each paragraph holding one must be the source's with exactly those runs gone (the paragraph stays); the runs
  found must be the plan's.
The box's proportions come from the transform's font metrics and are not re-derived (a wrong proportion distorts the
letters, which the render shows); everything else is.
"""
from __future__ import annotations

import math
import re
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

from lxml import etree

from .edits import Item, part_revision_ids, v_items
from .markup import check_paragraph, drop, insert, rpr_signature
from .props import Ctx, sect_prs, sig
from .reader import VPackage, W

_V_NS = "urn:schemas-microsoft-com:vml"
_O_NS = "urn:schemas-microsoft-com:office:office"
_V, _O = "{%s}" % _V_NS, "{%s}" % _O_NS
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
_HEADER_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/header"
_NAMES = ("PowerPlusWaterMarkObject", "WordPictureWatermark")
_HOLDERS = {W + "pict", W + "drawing", _MC + "AlternateContent"}
_TRACKED = {W + "ins", W + "del", W + "moveFrom", W + "moveTo"}
_TYPE_ID = "_x0000_t136"
_DEFAULTS = {"font": "Calibri", "color": "C0C0C0", "opacity": 0.5, "layout": "diagonal", "size": "auto"}
_EM = 1.55
_UNSEEN = {"Cc", "Cf", "Co", "Cs", "Cn", "Zl", "Zp"}

# Word's text watermark, as this module expects it — {placeholders} filled by _v_pict
_V_TYPE = (
    '<v:shapetype xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office" '
    'id="_x0000_t136" coordsize="21600,21600" o:spt="136" adj="10800" path="m@7,l@8,m@5,21600l@6,21600e">'
    '<v:formulas><v:f eqn="sum #0 0 10800"/><v:f eqn="prod #0 2 1"/><v:f eqn="sum 21600 0 @1"/>'
    '<v:f eqn="sum 0 0 @2"/><v:f eqn="sum 21600 0 @3"/><v:f eqn="if @0 @3 0"/><v:f eqn="if @0 21600 @1"/>'
    '<v:f eqn="if @0 0 @2"/><v:f eqn="if @0 @4 21600"/><v:f eqn="mid @5 @6"/><v:f eqn="mid @8 @5"/>'
    '<v:f eqn="mid @7 @8"/><v:f eqn="mid @6 @7"/><v:f eqn="sum @6 0 @5"/></v:formulas>'
    '<v:path textpathok="t" o:connecttype="custom" o:connectlocs="@9,0;@10,10800;@11,21600;@12,10800" '
    'o:connectangles="270,180,90,0"/><v:textpath on="t" fitshape="t"/>'
    '<v:handles><v:h position="#0,bottomRight" xrange="6629,14971"/></v:handles>'
    '<o:lock v:ext="edit" text="t" shapetype="t"/></v:shapetype>')
_V_SHAPE = (
    '<v:shape xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office" '
    'xmlns:w10="urn:schemas-microsoft-com:office:word" id="{sid}" o:spid="{spid}" type="#_x0000_t136" '
    'style="position:absolute;margin-left:0;margin-top:0;width:{w}pt;height:{h}pt;{rot}z-index:-251654144;'
    'mso-position-horizontal:center;mso-position-horizontal-relative:margin;mso-position-vertical:center;'
    'mso-position-vertical-relative:margin" o:allowincell="f" fillcolor="#{color}" stroked="f">'
    '<v:fill opacity="{opacity}"/><v:textpath style="font-family:&quot;{font}&quot;;font-size:1pt" string="{text}"/>'
    '<w10:wrap anchorx="margin" anchory="margin"/></v:shape>')


def _v_num(x: float) -> str:
    """This module's way of writing a number: 2 decimals at most, nothing trailing."""
    d = Decimal(repr(round(float(x), 2))).normalize()
    s = format(d, "f")
    return "0" if s in ("-0", "") else s


def _v_params(a: Dict[str, Any]) -> Dict[str, Any]:
    """The user's params as the op must read them: this module's defaults and rules (ValueError: the op must refuse)."""
    import unicodedata
    text = a.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > 100 or \
            any(unicodedata.category(c) in _UNSEEN for c in text):
        raise ValueError(f"the watermark text {text!r} is not one line of 1–100 visible characters")
    font = str(a.get("font", _DEFAULTS["font"])).strip()
    color = str(a.get("color", _DEFAULTS["color"])).strip()
    if color.startswith("#"):
        color = color[1:]
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", color):
        raise ValueError(f"colour {color!r} is not 6 hex digits")
    opacity = a.get("opacity", _DEFAULTS["opacity"])
    if isinstance(opacity, bool) or not isinstance(opacity, (int, float)) or not 0.05 <= float(opacity) <= 1:
        raise ValueError(f"opacity {opacity!r} out of range")
    layout = a.get("layout", _DEFAULTS["layout"])
    if layout not in ("diagonal", "horizontal"):
        raise ValueError(f"layout {layout!r}")
    size = a.get("size", _DEFAULTS["size"])
    if size != "auto" and (isinstance(size, bool) or not isinstance(size, (int, float)) or size <= 0):
        raise ValueError(f"size {size!r}")
    return {"text": text, "font": font, "color": color.upper(), "opacity": float(opacity), "layout": layout,
            "size": size, "replace": a.get("replace", False) is True}


def _v_pict(want: Dict[str, Any], box_: Tuple[float, float], sid: str, spid: str, with_type: bool):
    """The w:pict this module expects (a fresh tree; compared by props.sig)."""
    esc = lambda s: (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")       # noqa: E731
                     .replace('"', "&quot;"))
    shape = _V_SHAPE.format(sid=esc(sid), spid=esc(spid), w=_v_num(box_[0]), h=_v_num(box_[1]),
                            rot="rotation:315;" if want["layout"] == "diagonal" else "", color=want["color"],
                            opacity=_v_num(want["opacity"]), font=esc(want["font"]), text=esc(want["text"]))
    pict = etree.Element(W + "pict")
    if with_type:
        pict.append(etree.fromstring(_V_TYPE))
    pict.append(etree.fromstring(shape))
    return pict


def _v_noproof_rpr():
    rpr = etree.Element(W + "rPr")
    etree.SubElement(rpr, W + "noProof")
    return rpr


# ---- what the document shows -----------------------------------------------------------------------------------------

def _v_shows(src: VPackage) -> Tuple[List[str], Dict[str, List[int]]]:
    """The header parts some section shows (own references first, else the previous section's, per type), in the order
    they are first shown, and the sections showing each."""
    main = src.main_part()
    rels = src.rels_targets(main)
    order: List[str] = []
    showing: Dict[str, List[int]] = {}
    current: Dict[str, Optional[str]] = {}
    for i, sp in enumerate(sect_prs(src.xml(main))):
        for ref in sp:
            if ref.tag == W + "headerReference":
                rid = ref.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
                r = rels.get(rid)
                current[ref.get(W + "type", "default")] = r["target"] if r and r["type"] == _HEADER_REL else None
        for typ in ("default", "first", "even"):
            part = current.get(typ)
            if part and part in src.names:
                if part not in showing:
                    order.append(part)
                    showing[part] = []
                if i not in showing[part]:
                    showing[part].append(i)
    return order, showing


def _v_area(sp) -> Optional[Tuple[float, float]]:
    size, mar = sp.find(W + "pgSz"), sp.find(W + "pgMar")
    if size is None or mar is None:
        return None
    try:
        val = lambda el, k: int(el.get(W + k)) if el.get(W + k) not in (None, "") else 0       # noqa: E731
        w = (val(size, "w") - val(mar, "left") - val(mar, "right") - val(mar, "gutter")) / 20.0
        h = (val(size, "h") - abs(val(mar, "top")) - abs(val(mar, "bottom"))) / 20.0
    except ValueError:
        return None
    return (w, h) if w > 0 and h > 0 else None


def _v_para(el):
    q = el.getparent()
    while q is not None and q.tag != W + "p":
        q = q.getparent()
    return q


def _v_watermarks(root) -> List[Dict[str, Any]]:
    """Every watermark run of a header part, document order: {run, paragraph, text}. ValueError where the op must
    refuse (a shape in a run with other content, outside a run, in a text box paragraph, in a tracked change)."""
    out: List[Dict[str, Any]] = []
    runs: List[Any] = []
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if el.tag == _V + "shape":
            name = el.get("id") or ""
        elif el.tag == _WP + "docPr":
            name = el.get("name") or ""
        else:
            continue
        if not any(name.startswith(n) for n in _NAMES):
            continue
        run = el.getparent()
        while run is not None and run.tag != W + "r":
            run = run.getparent()
        if run is None:
            raise ValueError(f"a watermark shape {name} outside any run")
        if any(run is r for r in runs):
            continue
        kids = [c for c in run if isinstance(c.tag, str) and c.tag != W + "rPr"]
        if len(kids) != 1 or kids[0].tag not in _HOLDERS:
            raise ValueError(f"the watermark {name} shares its run with other content")
        p = _v_para(run)
        if p is None or _v_para(p) is not None:
            raise ValueError(f"the watermark {name} is not in a paragraph of the header itself")
        q = run.getparent()
        while q is not p:
            if q.tag in _TRACKED:
                raise ValueError(f"the watermark {name} lies in a pending tracked change")
            q = q.getparent()
        runs.append(run)
        out.append({"run": run, "paragraph": p, "text": _v_text(kids[0])})
    return out


def _v_text(holder) -> Optional[str]:
    for el in holder.iter(_V + "textpath"):
        if el.get("string") is not None:
            return el.get("string")
    for box_ in holder.iter(W + "txbxContent"):
        return "".join(t.text or "" for t in box_.iter(W + "t"))
    return None


def _v_same(a: Optional[str], b: str) -> bool:
    return a is not None and " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def _v_type_left(root, gone: List[Any]) -> bool:
    for el in root.iter(_V + "shapetype"):
        if el.get("id") == _TYPE_ID and not any(_v_within(el, r) for r in gone):
            return True
    return False


def _v_within(el, top) -> bool:
    while el is not None:
        if el is top:
            return True
        el = el.getparent()
    return False


def _v_definition_still_used(root, gone: List[Any], brought=()) -> Optional[str]:
    """The id of a VML shapetype whose only definition lies in ``gone`` while a shape left in the part still uses it
    (``brought``: definitions the op puts back), else None."""
    defined_in_gone = []
    for r in gone:
        for st in r.iter(_V + "shapetype"):
            if st.get("id") and st.get("id") not in defined_in_gone and st.get("id") not in brought:
                defined_in_gone.append(st.get("id"))
    for tid in defined_in_gone:
        elsewhere = [st for st in root.iter(_V + "shapetype")
                     if st.get("id") == tid and not any(_v_within(st, r) for r in gone)]
        if elsewhere:
            continue
        for el in root.iter():
            if isinstance(el.tag, str) and el.get("type") == "#" + tid and not any(_v_within(el, r) for r in gone):
                return tid
    return None


def _v_anchor(root):
    """The header's first paragraph whose every container below the header is a content control (w:sdt,
    w:sdtContent)."""
    for p in root.iter(W + "p"):
        q = p.getparent()
        while q is not root and q is not None and q.tag in (W + "sdt", W + "sdtContent"):
            q = q.getparent()
        if q is root:
            return p
    return None


def _v_locators(pars: List[Any], found: List[Dict[str, Any]]) -> List[List[int]]:
    out = []
    for f in found:
        k = next(i for i, p in enumerate(pars) if p is f["paragraph"])
        own = [r for r in f["paragraph"].iter(W + "r") if _v_para(r) is f["paragraph"]]
        out.append([k, next(i for i, r in enumerate(own) if r is f["run"])])
    return out


def _v_shift(pars: List[Any], found: List[Dict[str, Any]]) -> Callable[[int], int]:
    """Source block index → output block index, once the text-box paragraphs inside the removed runs are gone."""
    gone = sorted(i for i, p in enumerate(pars) if any(_v_within(p, f["run"]) for f in found))
    return lambda b: b - sum(1 for g in gone if g < b)


def _v_check_removals(ctx: Ctx, part: str, sv, ov, found: List[Dict[str, Any]], kind: str, errs: List[str],
                      skip: Optional[Any] = None) -> Callable[[int], int]:
    """Every paragraph holding a removed run (but ``skip``): exactly those runs gone, item by item."""
    shift = _v_shift(sv.pars, found)
    old_ids = part_revision_ids(sv.root)
    for k, p in enumerate(sv.pars):
        mine = [f["run"] for f in found if f["paragraph"] is p]
        if not mine or p is skip:
            continue
        oi = shift(k)
        if oi >= len(ov.pars):
            errs.append(f"{kind}: {part} paragraph {k} is missing from the output")
            continue
        check_paragraph(p, ov.pars[oi], drop(v_items(p), runs=mine), old_ids, f"{kind} {part} paragraph {k}", errs)
        ctx.verified_blocks.setdefault(part, {})[k] = oi
    return shift


def _v_values(pkg: VPackage) -> Dict[str, int]:
    """How often each value of a shape id (``id``) or a VML shape number (``o:spid``) occurs on any element of any XML
    member of the package — one pool: a new number must not repeat either."""
    out: Dict[str, int] = {}
    for name in pkg.names:
        if not name.endswith(".xml"):
            continue
        try:
            root = pkg.xml(name)
        except etree.XMLSyntaxError:
            continue
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            for attr in ("id", _O + "spid"):
                v = el.get(attr)
                if v is not None:
                    out[v] = out.get(v, 0) + 1
    return out


# ---- the kinds -------------------------------------------------------------------------------------------------------

def _fits(want: Dict[str, Any], box_: Tuple[float, float], area: Tuple[float, float]) -> Optional[str]:
    """Why the box breaks the sizing rule for the smallest text area ``area`` (None when it keeps it)."""
    bw, bh = box_
    aw, ah = area
    short = min(aw, ah)
    diag = (bw + bh) / math.sqrt(2)
    if want["layout"] == "diagonal" and diag > short + 0.01:
        return f"turned, it spans {_v_num(diag)} pt — more than the text area's {_v_num(short)} pt"
    if want["layout"] == "horizontal" and (bw > aw + 0.01 or bh > ah + 0.01):
        return f"{_v_num(bw)} × {_v_num(bh)} pt is larger than the text area {_v_num(aw)} × {_v_num(ah)} pt"
    if want["size"] == "auto":
        if want["layout"] == "diagonal" and abs(diag - 0.98 * short) > 0.02:
            return f"turned, it spans {_v_num(diag)} pt, not 0.98 × {_v_num(short)} pt"
        if want["layout"] == "horizontal" and not (
                bw <= 0.9 * aw + 0.01 and bh <= 0.98 * ah + 0.01
                and (abs(bw - 0.9 * aw) <= 0.02 or abs(bh - 0.98 * ah) <= 0.02)):
            return f"{_v_num(bw)} × {_v_num(bh)} pt does not fill 0.9 of the width (or 0.98 of the height)"
    elif abs(bh - _EM * float(want["size"])) > 0.01:
        return f"its height {_v_num(bh)} pt is not 1.55 × {want['size']} pt"
    return None


def _added_watermark(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    try:
        want = _v_params(a)
    except ValueError as e:
        errs.append(f"added_watermark: {e} — the op must refuse")
        return "added_watermark: unusable params"
    main = ctx.src.main_part()
    order, showing = _v_shows(ctx.src)
    planned = a.get("parts") or {}
    if sorted(order) != sorted(planned):
        errs.append(f"added_watermark: the document shows the headers {order}, the plan put a watermark in "
                    f"{sorted(planned)}")
    if not order:
        errs.append("added_watermark: the document shows no header — the op must refuse")
    secs = sect_prs(ctx.src.xml(main))
    src_vals, out_vals = _v_values(ctx.src), _v_values(ctx.out)
    noproof = rpr_signature(_v_noproof_rpr())
    done = 0
    for part in order:
        where = f"added_watermark {part}"
        if part not in ctx.both:
            errs.append(f"{where}: missing from the output")
            continue
        sv, ov = ctx.view(part)
        try:
            found = _v_watermarks(sv.root)
        except ValueError as e:
            errs.append(f"{where}: {e} — the op must refuse")
            continue
        if found and not want["replace"]:
            errs.append(f"{where}: a watermark is already there — the op must refuse (replace was not asked)")
            continue
        gone = [f["run"] for f in found]
        used = _v_definition_still_used(sv.root, gone, brought=(_TYPE_ID,))
        if used:
            errs.append(f"{where}: another shape uses the definition {used} a replaced watermark carries — the op "
                        f"must refuse")
            continue
        anchor = _v_anchor(sv.root)
        if anchor is None:
            errs.append(f"{where}: no paragraph of its own — the op must refuse")
            continue
        bi = next(k for k, p in enumerate(sv.pars) if p is anchor)
        mine = planned.get(part) or {}
        if mine.get("anchor") != bi:
            errs.append(f"{where}: the watermark belongs in paragraph {bi}, the plan says {mine.get('anchor')}")
        box_ = mine.get("box")
        if not (isinstance(box_, list) and len(box_) == 2 and all(isinstance(x, (int, float)) for x in box_)):
            errs.append(f"{where}: the plan states no box")
            continue
        areas = [_v_area(secs[i]) for i in showing[part]]
        if any(x is None for x in areas):
            errs.append(f"{where}: a section showing it states no usable page size — the op must refuse")
            continue
        why = _fits(want, (float(box_[0]), float(box_[1])), (min(x[0] for x in areas), min(x[1] for x in areas)))
        if why:
            errs.append(f"{where}: the box breaks the sizing rule: {why}")
        shift = _v_check_removals(ctx, part, sv, ov, found, "added_watermark", errs, skip=anchor)
        oi = shift(bi)
        if oi >= len(ov.pars):
            errs.append(f"{where}: the paragraph is missing from the output")
            continue
        op = ov.pars[oi]
        first = next((c for c in op if isinstance(c.tag, str) and c.tag != W + "pPr"), None)
        shape = first.find(W + "pict/" + _V + "shape") if first is not None and first.tag == W + "r" else None
        if shape is None:
            errs.append(f"{where}: the paragraph does not start with a watermark run")
            continue
        sid, spid = shape.get("id") or "", shape.get(_O + "spid") or ""
        if not re.fullmatch(r"PowerPlusWaterMarkObject\d+", sid) or not re.fullmatch(r"_x0000_s\d+", spid):
            errs.append(f"{where}: the shape's ids {sid!r} / {spid!r} are not Word's watermark ids")
        if sid in src_vals or out_vals.get(sid) != 1 or spid in src_vals or out_vals.get(spid) != 1:
            errs.append(f"{where}: the shape's ids {sid!r} / {spid!r} are not new and unique in the package")
        expected = _v_pict(want, (float(box_[0]), float(box_[1])), sid, spid, not _v_type_left(sv.root, gone))
        items = drop(v_items(anchor), runs=[f["run"] for f in found if f["paragraph"] is anchor])
        new = Item("x", sig(expected), (noproof, (), ()), None, W + "pict", None, None, True, ())
        check_paragraph(anchor, op, insert(items, 0, [new]), part_revision_ids(sv.root), where, errs)
        ctx.verified_blocks.setdefault(part, {})[bi] = oi
        done += 1
    return (f"added_watermark: {want['text']!r} ({want['layout']}) in {done} header part(s), each checked against "
            f"Word's watermark shape and the page")


def _removed_watermark(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    text = a.get("text")
    derived: Dict[str, List[List[int]]] = {}
    for part in ctx.src.part_of_kind("headers"):
        where = f"removed_watermark {part}"
        try:
            found = _v_watermarks(ctx.src.xml(part))
        except ValueError as e:
            errs.append(f"{where}: {e} — the op must refuse")
            continue
        if text is not None:
            found = [f for f in found if _v_same(f["text"], text)]
        if not found:
            continue
        if part not in ctx.both:
            errs.append(f"{where}: missing from the output")
            continue
        sv, ov = ctx.view(part)
        found = _v_watermarks(sv.root)                       # the view's own tree: its runs are the ones compared
        if text is not None:
            found = [f for f in found if _v_same(f["text"], text)]
        used = _v_definition_still_used(sv.root, [f["run"] for f in found])
        if used:
            errs.append(f"{where}: another shape uses the definition {used} a removed watermark carries — the op "
                        f"must refuse")
            continue
        derived[part] = _v_locators(sv.pars, found)
        _v_check_removals(ctx, part, sv, ov, found, "removed_watermark", errs)
    planned = {k: [list(x) for x in v] for k, v in (a.get("expected") or {}).items()}
    if derived != planned:
        errs.append(f"removed_watermark: the watermarks here are {derived}, the plan removed {planned}")
    n = sum(len(v) for v in derived.values())
    return (f"removed_watermark: {n} watermark(s) out of {len(derived)} header part(s), each paragraph checked item by "
            f"item")


WATERMARK_KINDS: Dict[str, Callable[..., Any]] = {
    "added_watermark": _added_watermark,
    "removed_watermark": _removed_watermark,
}
