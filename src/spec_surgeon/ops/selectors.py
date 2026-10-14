"""Selector resolution for ops (transform side). The verifier re-resolves every selector with its own code in
verify/props.py; the two must implement the same semantics (README → "Selector grammar"), and a test proves a
divergence is caught.

Grammar:

* ``scope``       part kinds: document | headers | footers | footnotes | endnotes | comments (resolved via rels)
* ``paragraphs``  "all" or a dict, all keys AND-ed:
                  style_in [styleId…]  (no pStyle → the default paragraph style)
                  text_regex "…"       (re.search on the block text: w:t + w:delText + w:instrText + … — CLAUDE.md)
                  in_table bool        (has a w:tc ancestor)
                  is_empty bool        (block text == "")
                  index_range [a, b]   (a ≤ block index < b, over the part's w:p in document order)
                  has_bold bool        (has a non-empty live run with effective bold on)
                  is_all_caps bool     (live display text is all uppercase letters or has w:caps on)
                  after_heading_regex "…" (comes after the first paragraph matching the regex)
                  between_paragraphs {"start_regex": "…", "end_regex": "…", "include_bounds": bool}
* ``runs``        "all" or a dict: has_direct_font bool | font_in [names] | not_in_revisions bool
* ``sections``    "all" or [sectPr ordinals]
* ``styles``      "all" (paragraph, character, table) | "all-paragraph-and-character" | {"ids": […]} | {"types": […]}

A run is a *symbol run* — never changed by a font op — when any explicit font name (any slot) in its direct rFonts,
its character-style chain, or its paragraph's style chain is in ``preserve_fonts`` (case-insensitive), or it holds
a w:sym (unless ``skip_sym_runs`` is false). Theme font references are never symbol fonts.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from lxml import etree

from ..ooxml import block_full_text
from ..package import W, DocxPackage, REL_TYPE_BASE
from .base import JobException

STYLES_REL = REL_TYPE_BASE + "styles"
SCOPE_KINDS = ("document", "headers", "footers", "footnotes", "endnotes", "comments")
PARAGRAPH_KEYS = ("style_in", "text_regex", "in_table", "is_empty", "index_range",
                  "has_bold", "is_all_caps", "after_heading_regex", "between_paragraphs")
RUN_KEYS = ("has_direct_font", "font_in", "not_in_revisions")
REVISION_WRAPPERS = (W + "ins", W + "del", W + "moveFrom", W + "moveTo")
FALSY = ("0", "false", "off")

# Fonts whose glyphs are pictures, not letters: changing them changes what the reader sees.
DEFAULT_PRESERVE_FONTS = ["Symbol", "Wingdings", "Wingdings 2", "Wingdings 3", "Webdings", "Marlett", "MT Extra",
                          "ZapfDingbats", "Zapf Dingbats", "Bookshelf Symbol 7", "MS Reference Specialty",
                          "MS Outlook"]
FONT_ATTRS = ("ascii", "hAnsi", "eastAsia", "cs", "asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme")


def scope_parts(pkg: DocxPackage, scope) -> List[str]:
    if isinstance(scope, str):
        scope = [scope]
    kinds = pkg.parts_by_kind()
    parts: List[str] = []
    for k in scope or ["document"]:
        if k not in SCOPE_KINDS:
            raise JobException(f"unknown scope {k!r}; known: {list(SCOPE_KINDS)}", "bad-params")
        for p in kinds[k]:
            if p not in parts:
                parts.append(p)
    return parts


def is_targeted(select: Dict[str, Any]) -> bool:
    """Decision §14.1: formatting is plain for whole-document edits and tracked for targeted ones. "Targeted"
    means the selector narrows to particular paragraphs."""
    p = (select or {}).get("paragraphs")
    return p not in (None, "all", {})


def check_paragraph_selector(pred) -> None:
    if pred in (None, "all"):
        return
    if not isinstance(pred, dict):
        raise JobException(f"paragraph selector must be 'all' or an object, got {pred!r}", "bad-params")
    unknown = set(pred) - set(PARAGRAPH_KEYS)
    if unknown:
        raise JobException(f"unknown paragraph selector key(s) {sorted(unknown)}; known: {list(PARAGRAPH_KEYS)}",
                           "bad-params")
    for rx_key in ("text_regex", "after_heading_regex"):
        if rx_key in pred:
            if not isinstance(pred[rx_key], str):
                raise JobException(f"{rx_key} must be a regex string", "bad-params")
            try:
                re.compile(pred[rx_key])
            except re.error as e:
                raise JobException(f"bad {rx_key}: {e}", "bad-params")
    if "index_range" in pred and (not isinstance(pred["index_range"], (list, tuple)) or len(pred["index_range"]) != 2):
        raise JobException("index_range must be [start, end)", "bad-params")
    if "has_bold" in pred and not isinstance(pred["has_bold"], bool):
        raise JobException("has_bold must be true or false", "bad-params")
    if "is_all_caps" in pred and not isinstance(pred["is_all_caps"], bool):
        raise JobException("is_all_caps must be true or false", "bad-params")
    if "between_paragraphs" in pred:
        bp = pred["between_paragraphs"]
        if not isinstance(bp, dict) or "start_regex" not in bp or not isinstance(bp["start_regex"], str):
            raise JobException("between_paragraphs must be an object with 'start_regex'", "bad-params")
        extra = set(bp) - {"start_regex", "end_regex", "include_bounds"}
        if extra:
            raise JobException(f"unknown between_paragraphs key(s) {sorted(extra)}", "bad-params")
        try:
            re.compile(bp["start_regex"])
            if bp.get("end_regex") is not None:
                if not isinstance(bp["end_regex"], str):
                    raise JobException("between_paragraphs.end_regex must be a string", "bad-params")
                re.compile(bp["end_regex"])
        except re.error as e:
            raise JobException(f"bad between_paragraphs regex: {e}", "bad-params")


def check_run_selector(pred) -> None:
    if pred in (None, "all"):
        return
    if not isinstance(pred, dict):
        raise JobException(f"run selector must be 'all' or an object, got {pred!r}", "bad-params")
    unknown = set(pred) - set(RUN_KEYS)
    if unknown:
        raise JobException(f"unknown run selector key(s) {sorted(unknown)}; known: {list(RUN_KEYS)}", "bad-params")


class StyleSheet:
    """styles.xml as the transform side sees it (resolved via rels)."""

    def __init__(self, pkg: DocxPackage):
        self.part: Optional[str] = None
        main = pkg.main_part()
        for r in pkg.rels(main).values():
            if r.type == STYLES_REL and r.resolved and pkg.has(r.resolved):
                self.part = r.resolved
                break
        self.root = pkg.xml(self.part) if self.part else None
        self.styles: Dict[str, etree._Element] = {}
        self.defaults: Dict[str, str] = {}
        if self.root is not None:
            for st in self.root.iter(W + "style"):
                if st.getparent() is not self.root:
                    continue
                sid = st.get(W + "styleId")
                if sid is None:
                    continue
                self.styles[sid] = st
                if st.get(W + "default") in ("1", "true", "on") and st.get(W + "type", "paragraph") not in self.defaults:
                    self.defaults[st.get(W + "type", "paragraph")] = sid

    def lineage(self, sid: Optional[str]) -> List[etree._Element]:
        seen, out = set(), []
        while sid and sid not in seen and sid in self.styles:
            seen.add(sid)
            out.append(self.styles[sid])
            based = self.styles[sid].find(W + "basedOn")
            sid = based.get(W + "val") if based is not None else None
        return out

    def paragraph_style_id(self, p: etree._Element) -> Optional[str]:
        ps = p.find(W + "pPr/" + W + "pStyle")
        return ps.get(W + "val") if ps is not None else self.defaults.get("paragraph")


def explicit_font_names(rpr: Optional[etree._Element]) -> List[str]:
    if rpr is None:
        return []
    rf = rpr.find(W + "rFonts")
    if rf is None:
        return []
    return [v for v in (rf.get(W + s) for s in ("ascii", "hAnsi", "eastAsia", "cs")) if v]


def has_direct_font(rpr: Optional[etree._Element]) -> bool:
    if rpr is None:
        return False
    rf = rpr.find(W + "rFonts")
    return rf is not None and any(rf.get(W + a) for a in FONT_ATTRS)


def is_symbol_rpr(rpr: Optional[etree._Element], p: Optional[etree._Element], sheet: StyleSheet,
                  preserve: List[str]) -> bool:
    wanted = {f.casefold() for f in preserve}
    names = explicit_font_names(rpr)
    rs = rpr.find(W + "rStyle") if rpr is not None else None
    for st in sheet.lineage(rs.get(W + "val") if rs is not None else None):
        names += explicit_font_names(st.find(W + "rPr"))
    if p is not None:
        for st in sheet.lineage(sheet.paragraph_style_id(p)):
            names += explicit_font_names(st.find(W + "rPr"))
    return any(n.casefold() in wanted for n in names)


def is_symbol_style(style: etree._Element, sheet: StyleSheet, preserve: List[str]) -> bool:
    wanted = {f.casefold() for f in preserve}
    return any(n.casefold() in wanted
               for st in sheet.lineage(style.get(W + "styleId"))
               for n in explicit_font_names(st.find(W + "rPr")))


def in_revision(el: etree._Element, p: etree._Element) -> bool:
    q = el.getparent()
    while q is not None and q is not p:
        if q.tag in REVISION_WRAPPERS:
            return True
        q = q.getparent()
    return False


def mark_in_revision(p: etree._Element) -> bool:
    rpr = p.find(W + "pPr/" + W + "rPr")
    return rpr is not None and any(rpr.find(t) is not None for t in REVISION_WRAPPERS)


def _live_runs(p: etree._Element) -> List[etree._Element]:
    out = []
    for r in p.iter(W + "r"):
        q = r.getparent()
        skip = False
        while q is not None and q is not p:
            if q.tag in (W + "p", W + "del", W + "moveFrom"):
                skip = True
                break
            q = q.getparent()
        if not skip:
            out.append(r)
    return out


def _toggle_on(rpr: Optional[etree._Element], p: etree._Element, sheet: StyleSheet, tag: str) -> bool:
    if rpr is not None:
        d = rpr.find(W + tag)
        if d is not None:
            return d.get(W + "val") not in FALSY
        rs = rpr.find(W + "rStyle")
        for st in sheet.lineage(rs.get(W + "val") if rs is not None else None):
            sr = st.find(W + "rPr")
            if sr is not None and sr.find(W + tag) is not None:
                return sr.find(W + tag).get(W + "val") not in FALSY
    for st in sheet.lineage(sheet.paragraph_style_id(p)):
        sr = st.find(W + "rPr")
        if sr is not None and sr.find(W + tag) is not None:
            return sr.find(W + tag).get(W + "val") not in FALSY
    return False


def _paragraph_has_bold(p: etree._Element, sheet: StyleSheet) -> bool:
    for r in _live_runs(p):
        txt = "".join(t.text or "" for t in r.findall(W + "t"))
        if txt.strip() and _toggle_on(r.find(W + "rPr"), p, sheet, "b"):
            return True
    return False


def _paragraph_is_all_caps(p: etree._Element, sheet: StyleSheet) -> bool:
    runs = _live_runs(p)
    live_txt = "".join(t.text or "" for r in runs for t in r.findall(W + "t")).strip()
    if not live_txt or not any(c.isalpha() for c in live_txt):
        return False
    if live_txt == live_txt.upper():
        return True
    return any("".join(t.text or "" for t in r.findall(W + "t")).strip()
               and _toggle_on(r.find(W + "rPr"), p, sheet, "caps") for r in runs)


def _block_text_with_tabs(p: etree._Element) -> str:
    from ..ooxml import FULL_TEXT_TAGS
    parts = []
    for el in p.iter(*FULL_TEXT_TAGS, W + "tab"):
        if el.tag == W + "tab" and (el.getparent() is None or el.getparent().tag != W + "r"):
            continue
        q = el.getparent()
        nested = False
        while q is not None and q is not p:
            if q.tag == W + "p":
                nested = True
                break
            q = q.getparent()
        if not nested:
            parts.append("\t" if el.tag == W + "tab" else (el.text or ""))
    return "".join(parts).replace("\u00a0", " ")


def _rx_matches(pattern: str, raw_text: str, p: etree._Element) -> bool:
    rx = re.compile(pattern)
    if rx.search(raw_text) is not None:
        return True
    return rx.search(_block_text_with_tabs(p)) is not None

def _part_texts(p: etree._Element, all_pars: Optional[Sequence[etree._Element]]) -> List[str]:
    if all_pars is None:
        root = p
        while root.getparent() is not None:
            root = root.getparent()
        all_pars = list(root.iter(W + "p"))
    return [block_full_text(x) for x in all_pars]


def paragraph_selected(pred, p: etree._Element, index: int, text: str, sheet: StyleSheet,
                       all_pars: Optional[Sequence[etree._Element]] = None) -> bool:
    if pred in (None, "all") or pred == {}:
        return True
    if "style_in" in pred and sheet.paragraph_style_id(p) not in set(pred["style_in"]):
        return False
    if "text_regex" in pred and not _rx_matches(pred["text_regex"], text, p):
        return False
    if "in_table" in pred:
        cell = p.getparent()
        while cell is not None and cell.tag != W + "tc":
            cell = cell.getparent()
        if (cell is not None) != bool(pred["in_table"]):
            return False
    if "is_empty" in pred and (text == "") != bool(pred["is_empty"]):
        return False
    if "index_range" in pred:
        lo, hi = pred["index_range"]
        if index < lo or index >= hi:
            return False
    if "has_bold" in pred and _paragraph_has_bold(p, sheet) != bool(pred["has_bold"]):
        return False
    if "is_all_caps" in pred and _paragraph_is_all_caps(p, sheet) != bool(pred["is_all_caps"]):
        return False
    if "after_heading_regex" in pred:
        texts = _part_texts(p, all_pars)
        rx = re.compile(pred["after_heading_regex"])
        hits = [i for i, t in enumerate(texts) if rx.search(t)]
        if not hits or index <= hits[0]:
            return False
    if "between_paragraphs" in pred:
        bp = pred["between_paragraphs"]
        texts = _part_texts(p, all_pars)
        srx = re.compile(bp["start_regex"])
        starts = [i for i, t in enumerate(texts) if srx.search(t)]
        if not starts:
            return False
        s_idx = starts[0]
        e_idx = len(texts)
        if bp.get("end_regex") is not None:
            erx = re.compile(bp["end_regex"])
            ends = [i for i in range(s_idx + 1, len(texts)) if erx.search(texts[i])]
            if ends:
                e_idx = ends[0]
        inc = bool(bp.get("include_bounds", False))
        if inc:
            if not (s_idx <= index <= (e_idx if e_idx < len(texts) else len(texts) - 1)):
                return False
        else:
            if not (s_idx < index < e_idx):
                return False
    return True


def rpr_selected(pred, rpr: Optional[etree._Element], revised: bool) -> bool:
    if pred in (None, "all") or pred == {}:
        return True
    if "has_direct_font" in pred and has_direct_font(rpr) != bool(pred["has_direct_font"]):
        return True if False else False
    if "font_in" in pred:
        wanted = {f.casefold() for f in pred["font_in"]}
        if not any(n.casefold() in wanted for n in explicit_font_names(rpr)):
            return False
    if pred.get("not_in_revisions") and revised:
        return False
    return True


def runs_by_paragraph(root: etree._Element, pars: List[etree._Element]) -> List[List[etree._Element]]:
    """Runs grouped under their nearest enclosing w:p (textbox runs belong to the textbox paragraph)."""
    where = {id(p): i for i, p in enumerate(pars)}
    groups: List[List[etree._Element]] = [[] for _ in pars]
    for r in root.iter(W + "r"):
        q = r.getparent()
        while q is not None and q.tag != W + "p":
            q = q.getparent()
        if q is not None:
            groups[where[id(q)]].append(r)
    return groups


def resolve_sections(pkg: DocxPackage, sections) -> List[int]:
    n = len(pkg.sections())
    if sections in (None, "all"):
        return list(range(n))
    if not isinstance(sections, (list, tuple)) or not all(isinstance(i, int) for i in sections):
        raise JobException(f"sections must be 'all' or a list of section indices, got {sections!r}", "bad-params")
    bad = [i for i in sections if not 0 <= i < n]
    if bad:
        raise JobException(f"no section(s) {bad}; the document has {n}", "bad-params", {"sections": n})
    return sorted(set(sections))


# ---- shared parameter parsing for the font ops ------------------------------------------------------

FONT_PARAM_DOC = {
    "font": {"type": "string", "description": "shorthand: sets ascii, hAnsi and cs (eastAsia untouched)"},
    "ascii": {"type": ["string", "null"]}, "hAnsi": {"type": ["string", "null"]},
    "cs": {"type": ["string", "null"]},
    "eastAsia": {"type": ["string", "null"], "description": "null/absent = leave the East Asian slot alone"},
    "clear_theme_fonts": {"type": "boolean", "default": True,
                          "description": "remove the theme attribute (asciiTheme…) of every slot being set — "
                                         "Word lets a theme attribute override an explicit name"},
    "preserve_fonts": {"type": "array", "items": {"type": "string"},
                       "description": "symbol/dingbat fonts never changed (default: Symbol, Wingdings…)"},
}


def font_values(params: Dict[str, Any]) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if params.get("font") is not None:
        if not isinstance(params["font"], str) or not params["font"].strip():
            raise JobException("'font' must be a non-empty font name", "bad-params")
        values = {"ascii": params["font"], "hAnsi": params["font"], "cs": params["font"]}
    for slot in ("ascii", "hAnsi", "eastAsia", "cs"):
        if params.get(slot) is not None:
            if not isinstance(params[slot], str) or not params[slot].strip():
                raise JobException(f"'{slot}' must be a non-empty font name or null", "bad-params")
            values[slot] = params[slot]
    if not values:
        raise JobException("no font given: set 'font' or one of ascii/hAnsi/cs/eastAsia", "bad-params")
    preserve = {f.casefold() for f in params.get("preserve_fonts", DEFAULT_PRESERVE_FONTS)}
    bad = [v for v in values.values() if v.casefold() in preserve]
    if bad:
        raise JobException(f"refusing to set a preserved symbol font {bad} as a text font", "bad-params")
    return values
