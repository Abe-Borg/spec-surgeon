"""Op: set_borders_shading — paragraph borders and shading (built-in batch 4, docs/BUILTIN_OPS_BATCH4_DESIGN.md §1).

Formatting mode: zero text diffs. Two targets, one per call (as set_properties): **direct** formatting
(``select.scope`` / ``paragraphs`` — the paragraph's own ``w:pPr/w:pBdr`` / ``w:shd``) or **paragraph styles**
(``select.styles: {"ids": [...]}``, alone — the style's own ``w:pPr``; never tracked; a style of another type in this
file is ``style-mismatch``).

Parameters: ``borders`` — ``"none"`` or an object of sides (``top | left | bottom | right | between | bar``), each a
border ``{style, size, space, color}`` (defaults ``single``, 4 eighths of a point, 1 point, ``auto``); **the asked object
is the paragraph's whole border box** — a side not named has no border. ``shading`` — ``"none"`` or ``{fill, pattern,
color}`` (``fill`` ``auto`` or RRGGBB; ``pattern`` default ``clear``; ``color`` default ``auto``). At least one of them.

Definitions (CLAUDE.md lists them among the definitions that must stay identical on both sides; the verifier's reading
is verify/borders.py):

* **Levels below a direct target** (``paragraph_levels``): docDefaults ``pPr`` → table style chain (the paragraph's
  table: its ``tblStyle``, else the default table style) → paragraph style chain (``pStyle``, else the default paragraph
  style), each chain root-most first. **Below a style target** (``style_levels``): docDefaults ``pPr`` → the style's
  ``basedOn`` ancestors (not the style itself), root-most first.
* **A visible side** (``side_visible``): a side element whose ``w:val`` is not ``nil`` or ``none`` (absent counts as
  visible). **Visible shading** (``shading_visible``): a ``w:shd`` that is not ``w:val="nil"`` and not (``w:val="clear"``
  with ``w:fill`` absent or ``auto`` and no ``w:themeFill``).
* **Expected ``w:pBdr``** (``expected_borders``): the sides in the order top, left, bottom, right, between, bar — an asked
  side exactly ``{w:val, w:sz, w:space, w:color}``; a side not asked that ANY lower level specifies visibly as
  ``{w:val="nil"}``; else absent. No side → no ``w:pBdr``.
* **Expected ``w:shd``** (``expected_shading``): asked → exactly ``{w:val, w:color, w:fill}``; ``"none"`` →
  ``{clear, auto, auto}`` when the nearest lower level specifying a ``w:shd`` shows visible shading, else absent.
* **Already** (``same_element``): the target's own element is exactly the expected one (both absent counts). A target with
  nothing to change is left alone and not counted.
* **Writing** (``write_elements``): the old element goes, the expected one is inserted in ``PPR_ORDER``; a paragraph's
  (plain) or a style's pPr left empty goes.

Tracked or plain (§14.1) and an existing ``w:pPrChange`` exactly as set_paragraph_props (direct only). Verified by V10's
``borders_shading`` kind (verify/borders.py), which re-derives all of the above on its own.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from ..package import W, DocxPackage
from ..ooxml import (PPR_ORDER, STYLE_ORDER, RevisionStamp, block_full_text, file_uses_date_utc, get_or_add,
                     insert_in_order, max_revision_id, paragraph_ppr, paragraphs, property_change)
from . import register
from .base import JobException, Op, PlanFragment
from .effective import Resolver
from .replace_text import default_author
from .selectors import (StyleSheet, chain_reads, check_paragraph_selector, is_targeted, paragraph_selected, scope_parts,
                        table_ordinals)

SIDES = ("top", "left", "bottom", "right", "between", "bar")
BORDER_STYLES = ("single", "thick", "double", "dotted", "dashed", "dotDash", "dotDotDash", "triple",
                 "thinThickSmallGap", "thickThinSmallGap", "wave", "doubleWave", "dashSmallGap", "threeDEmboss",
                 "threeDEngrave", "outset", "inset")
PATTERNS = ("clear", "solid", "pct5", "pct10", "pct12", "pct15", "pct20", "pct25", "pct30", "pct35", "pct37", "pct40",
            "pct45", "pct50", "pct55", "pct60", "pct62", "pct65", "pct70", "pct75", "pct80", "pct85", "pct87", "pct90",
            "pct95", "horzStripe", "vertStripe", "diagStripe", "reverseDiagStripe", "horzCross", "diagCross")
HEX_COLOR = re.compile(r"[0-9A-Fa-f]{6}")
NOT_VISIBLE = ("nil", "none")
ELEMENTS = ("pBdr", "shd")
PARAM_KEYS = {"borders", "shading", "tracked", "author"}
BORDER_REASON = "a paragraph border takes space and moves the text after it by design"
SCOPE_SCHEMA = {"type": "array", "items": {"type": "string", "enum": ["document", "headers", "footers", "footnotes",
                                                                       "endnotes", "comments"]}, "default": ["document"]}
BORDER_SCHEMA = {"type": "object", "properties": {
    "style": {"type": "string", "enum": list(BORDER_STYLES), "default": "single"},
    "size": {"type": "integer", "description": "eighths of a point, 2–96", "default": 4},
    "space": {"type": "integer", "description": "points between the border and the text, 0–31", "default": 1},
    "color": {"type": "string", "description": "'auto' or 6 hex digits (RRGGBB)", "default": "auto"}}}

Attrs = List[Tuple[str, str]]              # (attribute local name, value), in the order they are written
Box = List[Tuple[str, Attrs]]              # (side, its attributes), sides in SIDES order


# ---- parameters ---------------------------------------------------------------------------------------------------

def _color(v: Any, where: str) -> str:
    if v == "auto":
        return "auto"
    if isinstance(v, str) and HEX_COLOR.fullmatch(v):
        return v.upper()
    raise JobException(f"{where} must be 'auto' or 6 hex digits (RRGGBB), got {v!r}", "bad-params")


def _whole(v: Any, lo: int, hi: int, where: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise JobException(f"{where} must be a whole number from {lo} to {hi}, got {v!r}", "bad-params")
    return v


def clean_params(params: Dict[str, Any]) -> Tuple[Optional[Dict[str, Attrs]], Any]:
    """(the asked border box — None when borders are not asked, {} for "none", else side → its attributes; the asked
    shading — None when not asked, "none", or its attributes) — validated, colours upper-cased."""
    unknown = set(params) - PARAM_KEYS
    if unknown:
        raise JobException(f"set_borders_shading: unknown parameter(s) {sorted(unknown)}; known: {sorted(PARAM_KEYS)}",
                           "bad-params")
    if "tracked" in params and not isinstance(params["tracked"], bool):
        raise JobException("tracked must be true or false", "bad-params")
    raw_b, raw_s = params.get("borders"), params.get("shading")
    if raw_b is None and raw_s is None:
        raise JobException("nothing to set: give 'borders' and/or 'shading'", "bad-params")
    borders: Optional[Dict[str, Attrs]] = None
    if raw_b is not None:
        if raw_b == "none":
            borders = {}
        elif isinstance(raw_b, dict) and raw_b:
            unknown = set(raw_b) - set(SIDES)
            if unknown:
                raise JobException(f"unknown border side(s) {sorted(unknown)}; known: {list(SIDES)}", "bad-params")
            borders = {}
            for side in SIDES:
                if side not in raw_b:
                    continue
                b = raw_b[side]
                if not isinstance(b, dict) or set(b) - {"style", "size", "space", "color"}:
                    raise JobException(f"borders.{side} must be an object {{style, size, space, color}}", "bad-params")
                style = b.get("style", "single")
                if style not in BORDER_STYLES:
                    raise JobException(f"borders.{side}.style must be one of {list(BORDER_STYLES)}", "bad-params")
                borders[side] = [("val", style), ("sz", str(_whole(b.get("size", 4), 2, 96, f"borders.{side}.size"))),
                                 ("space", str(_whole(b.get("space", 1), 0, 31, f"borders.{side}.space"))),
                                 ("color", _color(b.get("color", "auto"), f"borders.{side}.color"))]
        else:
            raise JobException("borders must be 'none' or an object of sides (top, left, bottom, right, between, bar) — "
                               "the paragraph's whole border box; 'none' removes every border", "bad-params")
    shading: Any = None
    if raw_s is not None:
        if raw_s == "none":
            shading = "none"
        elif isinstance(raw_s, dict) and "fill" in raw_s and not set(raw_s) - {"fill", "pattern", "color"}:
            pattern = raw_s.get("pattern", "clear")
            if pattern not in PATTERNS:
                raise JobException(f"shading.pattern must be one of {list(PATTERNS)}", "bad-params")
            shading = [("val", pattern), ("color", _color(raw_s.get("color", "auto"), "shading.color")),
                       ("fill", _color(raw_s["fill"], "shading.fill"))]
        else:
            raise JobException("shading must be 'none' or an object {fill, pattern?, color?}", "bad-params")
    return borders, shading


# ---- the definitions ----------------------------------------------------------------------------------------------

def paragraph_levels(res: Resolver, p) -> List[Tuple[str, Any]]:
    """(label, pPr) of every level below a paragraph's direct formatting, lowest first: docDefaults, the table style
    chain, the paragraph style chain — each chain root-most first."""
    out: List[Tuple[str, Any]] = [("docDefaults", res.dd_ppr)]
    for chain in res._style_levels(p):
        out += [(f"style:{st.get(W + 'styleId')}", st.find(W + "pPr")) for st in chain]
    return out


def style_levels(res: Resolver, sid: str) -> List[Tuple[str, Any]]:
    """(label, pPr) of every level below a style's own definition, lowest first: docDefaults, then its basedOn
    ancestors root-most first (the style itself left out)."""
    out: List[Tuple[str, Any]] = [("docDefaults", res.dd_ppr)]
    out += [(f"style:{st.get(W + 'styleId')}", st.find(W + "pPr")) for st in res.chain(sid)[:-1]]
    return out


def side_visible(el) -> bool:
    return el is not None and el.get(W + "val") not in NOT_VISIBLE


def shading_visible(shd) -> bool:
    if shd is None:
        return False
    val = shd.get(W + "val")
    if val == "nil":
        return False
    return not (val == "clear" and shd.get(W + "fill") in (None, "auto") and shd.get(W + "themeFill") is None)


def expected_borders(asked: Dict[str, Attrs], lower: List[Any]) -> Box:
    """The expected w:pBdr as (side, attributes): asked sides exactly, a side any lower level shows visibly as nil."""
    shown = set()
    for ppr in lower:
        bdr = ppr.find(W + "pBdr") if ppr is not None else None
        if bdr is None:
            continue
        shown |= {side for side in SIDES if side_visible(bdr.find(W + side))}
    out: Box = []
    for side in SIDES:
        if side in asked:
            out.append((side, list(asked[side])))
        elif side in shown:
            out.append((side, [("val", "nil")]))
    return out


def expected_shading(asked: Any, lower: List[Any]) -> Optional[Attrs]:
    """The expected w:shd attributes, or None for no element."""
    if asked != "none":
        return list(asked)
    nearest = None
    for ppr in lower:
        shd = ppr.find(W + "shd") if ppr is not None else None
        if shd is not None:
            nearest = shd
    return [("val", "clear"), ("color", "auto"), ("fill", "auto")] if shading_visible(nearest) else None


def build_pbdr(box: Box):
    if not box:
        return None
    el = etree.Element(W + "pBdr")
    for side, attrs in box:
        s = etree.SubElement(el, W + side)
        for k, v in attrs:
            s.set(W + k, v)
    return el


def build_shd(attrs: Optional[Attrs]):
    if attrs is None:
        return None
    el = etree.Element(W + "shd")
    for k, v in attrs:
        el.set(W + k, v)
    return el


def _sig(el) -> Any:
    if el is None:
        return None
    if not isinstance(el.tag, str):
        return ("#node", (el.text or "").strip())
    return (el.tag, tuple(sorted(el.attrib.items())), (el.text or "").strip(), tuple(_sig(c) for c in el))


def same_element(own, expected) -> bool:
    """The already rule: the target's own element is exactly the expected one (both absent counts)."""
    return _sig(own) == _sig(expected)


def decide(own_ppr, lower: List[Any], borders: Optional[Dict[str, Attrs]], shading: Any) -> Dict[str, Any]:
    """The asked elements that change, each → its expected data (a Box / attributes, or None for "no element")."""
    gets: Dict[str, Any] = {}
    if borders is not None:
        box = expected_borders(borders, lower)
        own = own_ppr.find(W + "pBdr") if own_ppr is not None else None
        if not same_element(own, build_pbdr(box)):
            gets["pBdr"] = [[side, [list(a) for a in attrs]] for side, attrs in box] or None
    if shading is not None:
        attrs = expected_shading(shading, lower)
        own = own_ppr.find(W + "shd") if own_ppr is not None else None
        if not same_element(own, build_shd(attrs)):
            gets["shd"] = [list(a) for a in attrs] if attrs is not None else None
    return gets


def write_elements(ppr, gets: Dict[str, Any]) -> None:
    """The old element goes; the expected one is inserted in schema order (none → nothing inserted)."""
    for name in ELEMENTS:
        if name not in gets:
            continue
        for old in ppr.findall(W + name):
            ppr.remove(old)
        data = gets[name]
        if not data:
            continue
        el = build_pbdr([(s, [tuple(a) for a in attrs]) for s, attrs in data]) if name == "pBdr" else \
            build_shd([tuple(a) for a in data])
        insert_in_order(ppr, el, PPR_ORDER)


def _describe(borders, shading) -> str:
    parts = []
    if borders is not None:
        parts.append("borders " + (", ".join(f"{s} {dict(a)['val']} {dict(a)['sz']}/8 pt" for s, a in borders.items())
                                   if borders else "none") + " (the whole border box)")
    if shading is not None:
        parts.append("shading " + ("none" if shading == "none" else
                                   f"{dict(shading)['val']} fill {dict(shading)['fill']} colour {dict(shading)['color']}"))
    return "; ".join(parts)


@register
class SetBordersShading(Op):
    name = "set_borders_shading"
    mode = "formatting"
    description = ("Set paragraph borders (a box, a rule above or below …) and shading on selected paragraphs or in "
                   "paragraph styles. The asked borders are the paragraph's whole border box — a side not named has "
                   "none, so a border its style shows is cancelled; 'none' removes every border or the shading. Adds no "
                   "characters; a paragraph or style that already looks as asked is left alone.")
    params_schema = {"type": "object", "properties": {
        "borders": {"type": ["string", "object"],
                    "description": "'none', or the whole border box: {top, left, bottom, right, between, bar} each "
                                   "{style (single…), size (eighths of a point, 2–96), space (points, 0–31), color "
                                   "('auto' or RRGGBB)}; a side not named has no border",
                    "properties": {side: BORDER_SCHEMA for side in SIDES}},
        "shading": {"type": ["string", "object"],
                    "description": "'none', or {fill ('auto' or RRGGBB), pattern (clear by default; solid, pct10 …), "
                                   "color (the pattern's colour, 'auto' by default)}",
                    "properties": {"fill": {"type": "string"},
                                   "pattern": {"type": "string", "enum": list(PATTERNS), "default": "clear"},
                                   "color": {"type": "string", "default": "auto"}}},
        "tracked": {"type": "boolean", "description": "w:pPrChange; default: plain for whole-document selections, "
                                                      "tracked when paragraphs are targeted; never for styles"},
        "author": {"type": "string"}}}
    select_schema = {"type": "object", "properties": {
        "scope": SCOPE_SCHEMA,
        "paragraphs": {"description": "'all' or {style_in, text_regex, in_table, is_empty, index_range, has_bold, "
                                      "is_all_caps, after_heading_regex, between_paragraphs}"},
        "styles": {"description": "{ids: [styleId, …]} — change these paragraph style definitions instead of the "
                                  "paragraphs' direct formatting; alone (no scope/paragraphs)"}}}
    verification_profile = {
        "V2": "zero text diffs", "V4": "tracked: +1 w:pPrChange per changed paragraph",
        "V10": "borders_shading: selection, the levels below each target, the expected w:pBdr/w:shd and 'already' "
               "re-derived from the source; each changed target's elements exactly as expected and nothing else "
               "changed; the asked box and shading shown by every selected target in the output; residual identical",
        "V7": "none when a border changes (borders take space); source for shading alone"}

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        raw_select = dict(select or {})
        unknown = set(raw_select) - {"scope", "paragraphs", "styles"}
        if unknown:
            raise JobException(f"set_borders_shading: unknown selector key(s) {sorted(unknown)}", "bad-params")
        borders, shading = clean_params(params)
        sheet = StyleSheet(pkg)
        res = Resolver(sheet, {})
        if "styles" in raw_select:
            if set(raw_select) != {"styles"}:
                raise JobException("set_borders_shading: 'styles' is a target of its own — give it alone, without "
                                   "scope or paragraphs (direct formatting is a second op)", "bad-params")
            return self._plan_styles(pkg, sheet, res, raw_select, params, borders, shading)
        return self._plan_direct(pkg, sheet, res, raw_select, params, borders, shading)

    # ---- direct formatting -------------------------------------------------------------------------------------

    def _plan_direct(self, pkg, sheet, res, raw_select, params, borders, shading) -> PlanFragment:
        pred = raw_select.get("paragraphs", "all")
        check_paragraph_selector(pred)
        scope = raw_select.get("scope", ["document"])
        parts = scope_parts(pkg, scope)
        tracked = bool(params.get("tracked", is_targeted(raw_select)))
        author = params.get("author") or default_author()
        asked = [e for e, v in (("pBdr", borders), ("shd", shading)) if v is not None]
        frag = self.fragment(raw_select, {**params, "tracked": tracked, "author": author})
        targets: Dict[str, List[list]] = {}
        already = older = 0
        reads: set = set()
        for part in parts:
            root = pkg.xml(part)
            pars = paragraphs(root)
            tables = table_ordinals(root)
            chosen: List[list] = []
            for bi, p in enumerate(pars):
                if not paragraph_selected(pred, p, bi, block_full_text(p), sheet):
                    continue
                # the levels below come through this paragraph's style (and its table's); V10's post-condition reads
                # its own border box and shading in the output whether or not it changes: another op restyling it,
                # remapping its table's style, adding a style its chain names but the file lacks, or changing those
                # elements is a plan-time conflict, never a failure after apply (soft reads: a text edit leaves them)
                frag.touches.append((part, str(bi), "verify-props:pStyle"))
                frag.touches += [(part, str(bi), f"verify-props:{e}") for e in asked]
                frag.touches += sorted(chain_reads(sheet, part, p, tables))
                levels = paragraph_levels(res, p)
                reads.update(label for label, _ in levels)
                gets = decide(p.find(W + "pPr"), [ppr for _, ppr in levels], borders, shading)
                if not gets:
                    already += 1
                    continue
                ppr = p.find(W + "pPr")
                if ppr is not None and ppr.find(W + "pPrChange") is not None:
                    if tracked:
                        raise JobException(f"{part} paragraph {bi} already carries a pending tracked paragraph "
                                           f"change (w:pPrChange)", "tracked-format", {"part": part, "paragraph": bi})
                    older += 1
                chosen.append([bi, gets])
            if chosen:
                targets[part] = chosen
                frag.intended_parts.append(part)
                for bi, gets in chosen:
                    frag.touches.append((part, str(bi), "pPr"))
                    frag.touches += [(part, str(bi), e) for e in ELEMENTS if e in gets]
            frag.touches.append((part, "scope", "verify"))      # V10 re-resolves the selection over this part
        self._declare_style_reads(frag, sheet, reads)
        n = sum(len(v) for v in targets.values())
        borders_change = any("pBdr" in gets for v in targets.values() for _, gets in v)
        frag.property_assertions.append({
            "kind": "borders_shading", "target": "direct", "parts": parts, "select": raw_select,
            "params": {k: params[k] for k in ("borders", "shading", "tracked") if k in params},
            "author": author, "expected_paragraphs": n,
            "changed": {part: [[bi, sorted(gets)] for bi, gets in v] for part, v in targets.items()}})
        frag.transform_data = {"target": "direct", "targets": targets, "tracked": tracked, "author": author}
        if tracked and n:
            frag.rev_delta["pPrChange"] = n
            frag.rev_ids_needed = n
            frag.rev_id_base = max_revision_id(pkg) + 1
            frag.transform_data["use_date_utc"] = file_uses_date_utc(pkg)
        if borders_change:
            frag.geometry_reference, frag.geometry_reason = "none", BORDER_REASON
        frag.notes.append(f"{n} paragraph(s) → {_describe(borders, shading)}; "
                          f"{'tracked (w:pPrChange)' if tracked else 'plain'} — "
                          + ("explicit 'tracked'" if "tracked" in params else
                             "default for " + ("targeted" if tracked else "whole-document") + " formatting (§14.1)"))
        if already:
            frag.notes.append(f"already as asked, left alone: {already} paragraph(s)")
        if borders is not None:
            frag.notes.append("a side not asked that the paragraph's style shows is written as 'nil' (no border), so "
                              "the asked box is the whole box")
        if older:
            frag.flags.append(f"{older} changed paragraph(s) already carried a pending tracked paragraph change "
                              f"(w:pPrChange); reject-all would restore that older snapshot.")
        return frag

    # ---- style definitions -------------------------------------------------------------------------------------

    def _plan_styles(self, pkg, sheet, res, raw_select, params, borders, shading) -> PlanFragment:
        sel = raw_select["styles"]
        if not (isinstance(sel, dict) and set(sel) == {"ids"} and isinstance(sel["ids"], list) and sel["ids"]
                and all(isinstance(x, str) for x in sel["ids"])):
            raise JobException(f"styles selector must be {{'ids': [styleId, …]}}, got {sel!r}", "bad-params")
        if params.get("tracked"):
            raise JobException("style-definition changes are not tracked (Word does not track them either); omit "
                               "'tracked' or set it false", "bad-params")
        if sheet.part is None:
            raise JobException("document has no styles part", "structure")
        ids = list(dict.fromkeys(sel["ids"]))
        missing = [i for i in ids if i not in sheet.styles]
        if missing:
            raise JobException(f"style id(s) {missing} not in this document", "not-found", {"missing": missing})
        bad = [sid for sid in ids if sheet.styles[sid].get(W + "type", "paragraph") != "paragraph"]
        if bad:
            raise JobException(f"borders and shading go on paragraph styles only; {bad} are not paragraph styles in "
                               f"this file", "style-mismatch", {"styles": bad})
        changes: Dict[str, Dict[str, Any]] = {}
        reads: set = set()
        for sid in ids:
            levels = style_levels(res, sid)
            reads.update(label for label, _ in levels)
            gets = decide(sheet.styles[sid].find(W + "pPr"), [ppr for _, ppr in levels], borders, shading)
            if gets:
                changes[sid] = gets
        frag = self.fragment(raw_select, params)
        reads.update(f"style:{sid}" for sid in ids if sid not in changes)    # the post-condition reads them all
        self._declare_style_reads(frag, sheet, reads)
        if changes:
            frag.intended_parts = [sheet.part]
            frag.touches += [(sheet.part, f"style:{sid}", "pPr") for sid in changes]
        frag.property_assertions.append({
            "kind": "borders_shading", "target": "styles", "styles_part": sheet.part, "select": {"ids": ids},
            "params": {k: params[k] for k in ("borders", "shading", "tracked") if k in params},
            "expected_ids": list(changes), "changed": {sid: sorted(g) for sid, g in changes.items()}})
        frag.transform_data = {"target": "styles", "styles_part": sheet.part, "changes": changes}
        if any("pBdr" in g for g in changes.values()):
            frag.geometry_reference, frag.geometry_reason = "none", BORDER_REASON
        frag.notes.append(f"style(s) {list(changes) or 'none'} → {_describe(borders, shading)} (definitions only; not "
                          f"tracked — Word does not track style changes)"
                          + (f"; already as asked: {[s for s in ids if s not in changes]}"
                             if any(s not in changes for s in ids) else ""))
        self._flag_styles(frag, pkg, sheet, changes)
        if changes and pkg.has("word/stylesWithEffects.xml"):
            frag.flags.append("word/stylesWithEffects.xml (Word 2010's compatibility copy of the styles) is not "
                              "updated; Word 2013+ ignores it.")
        return frag

    @staticmethod
    def _declare_style_reads(frag: PlanFragment, sheet: StyleSheet, labels) -> None:
        """The expected elements are computed from the pPr of every level below the target (docDefaults and the
        styles): another op changing one of them is a plan-time conflict, never a stale box after apply."""
        if sheet.part is None:
            return
        for label in sorted(labels):
            frag.touches.append((sheet.part, label, "verify:pPr"))

    @staticmethod
    def _flag_styles(frag: PlanFragment, pkg: DocxPackage, sheet: StyleSheet, changes: Dict[str, Dict[str, Any]]) -> None:
        """Direct formatting wins over a style, and a style based on a changed one keeps its own definition: both are
        flagged (never refused)."""
        if not changes:
            return
        keep = Counter()
        for part in pkg.text_parts():
            for p in paragraphs(pkg.xml(part)):
                chain = [st.get(W + "styleId") for st in sheet.lineage(sheet.paragraph_style_id(p))]
                ppr = p.find(W + "pPr")
                for sid, gets in changes.items():
                    if sid in chain and ppr is not None and any(ppr.find(W + e) is not None for e in gets):
                        keep[sid] += 1
        for sid in changes:
            if keep[sid]:
                frag.flags.append(f"{keep[sid]} paragraph(s) using style {sid} (or a style based on it) keep their own "
                                  f"direct {' / '.join('borders' if e == 'pBdr' else 'shading' for e in changes[sid])} "
                                  f"— direct formatting wins over the style.")
            own = [other for other, st in sheet.styles.items() if other not in changes
                   and sid in [s.get(W + "styleId") for s in sheet.lineage(other)[1:]]
                   and st.find(W + "pPr") is not None
                   and any(st.find(W + "pPr/" + W + e) is not None for e in changes[sid])]
            if own:
                frag.flags.append(f"style(s) {sorted(own)} are based on {sid} and define their own "
                                  f"{' / '.join('borders' if e == 'pBdr' else 'shading' for e in changes[sid])}, "
                                  f"which they keep.")

    # ---- apply -------------------------------------------------------------------------------------------------

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if td["target"] == "styles":
            if not td["changes"]:
                return
            root = pkg.xml(td["styles_part"])
            by_id = {st.get(W + "styleId"): st for st in root.findall(W + "style")}
            for sid, gets in td["changes"].items():
                st = by_id[sid]
                ppr = get_or_add(st, "pPr", STYLE_ORDER)
                write_elements(ppr, gets)
                if not len(ppr) and not ppr.attrib:
                    st.remove(ppr)
            pkg.write_xml(td["styles_part"], root)
            return
        stamp = RevisionStamp.now(td["author"], td.get("use_date_utc", False)) if td["tracked"] else None
        next_id = td.get("first_rev_id", 0)
        for part, chosen in td["targets"].items():
            root = pkg.xml(part)
            pars = paragraphs(root)
            for bi, gets in chosen:
                p = pars[bi]
                ppr = paragraph_ppr(p)
                chg = None
                if stamp:
                    snap = [c for c in ppr if c.tag not in (W + "rPr", W + "sectPr", W + "pPrChange")]
                    chg = property_change("pPrChange", "pPr", snap, stamp, next_id)
                    next_id += 1
                write_elements(ppr, gets)
                if chg is not None:
                    ppr.append(chg)
                elif not len(ppr) and not ppr.attrib:
                    p.remove(ppr)                              # an emptied pPr goes (plain)
            pkg.write_xml(part, root)
