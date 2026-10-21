"""Effective ("as rendered") paragraph and run properties — transform side, for the templating op's pinning.

The verifier has its own resolver (verify/template.py); the two implement the definition below, which
CLAUDE.md lists among the definitions that must stay identical on both sides.

Pinnable properties
  paragraph: spacing before/after/line/lineRule, indentation left/right/first (firstLine = +v, hanging = −v;
             start/end are aliases of left/right), jc (start/end aliases of left/right), tab stops
  run (and paragraph mark): fonts ascii/hAnsi/cs (theme references resolved through the theme part), sz, szCs,
             color (val, themeColor, themeShade, themeTint), and the toggles b, i, caps, smallCaps

Levels, lowest first: docDefaults → table style chain (paragraph inside a table: its tblStyle, else the default
table style) → paragraph style chain (pStyle, else the default paragraph style) → [runs only] character style
chain (rStyle, else the default character style) → direct formatting. Within a style chain the style nearest the
paragraph/run wins; per attribute for spacing and indentation; tab stops fold (a ``clear`` tab removes an
inherited stop at its position).

Toggle properties: direct formatting wins. Among styles (docDefaults included) a toggle specified exactly once
takes that value; specified nowhere → off; specified more than once → AmbiguousToggle (the XOR rules of
§17.7.3 are not modelled — the op refuses instead of guessing).

Unspecified values take Word's defaults: spacing 0/0/240/auto, indentation 0, jc left, sz/szCs 20, font
"Times New Roman", color ("auto", None, None, None).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from ..package import W, DocxPackage, REL_TYPE_BASE
from .selectors import StyleSheet

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
THEME_REL = REL_TYPE_BASE + "theme"
TOGGLES = ("b", "i", "caps", "smallCaps")
SLOTS = ("ascii", "hAnsi", "cs")
THEME_ATTR = {"ascii": "asciiTheme", "hAnsi": "hAnsiTheme", "cs": "cstheme"}
PARA_DEFAULTS = {"before": "0", "after": "0", "line": "240", "lineRule": "auto", "left": "0", "right": "0",
                 "first": "0", "jc": "left", "tabs": ()}
RUN_DEFAULTS = {"ascii": "Times New Roman", "hAnsi": "Times New Roman", "cs": "Times New Roman", "sz": "20",
                "szCs": "20", "color": ("auto", None, None, None), "b": False, "i": False, "caps": False,
                "smallCaps": False}
FALSE = ("0", "false", "off")


class AmbiguousToggle(Exception):
    pass


def theme_part(pkg: DocxPackage) -> Optional[str]:
    """The package's theme part (resolved via the main part's rels), or None — what ``theme_fonts`` reads, so an
    op whose prediction depends on the theme can declare it as a read (``(part, "*", "verify")``)."""
    main = pkg.main_part()
    return next((r.resolved for r in pkg.rels(main).values() if r.type == THEME_REL and r.resolved
                 and pkg.has(r.resolved)), None)


def theme_fonts(pkg: DocxPackage) -> Dict[str, str]:
    """{"major:latin": "Calibri Light", "minor:cs": "", …} from the package's theme part (resolved via rels)."""
    part = theme_part(pkg)
    out: Dict[str, str] = {}
    if part is None:
        return out
    root = pkg.xml(part)
    for which in ("major", "minor"):
        f = root.find(f".//{A}{which}Font")
        if f is None:
            continue
        for script in ("latin", "ea", "cs"):
            e = f.find(A + script)
            if e is not None:
                out[f"{which}:{script}"] = e.get("typeface", "")
    return out


def text_width(sect_pr) -> Optional[int]:
    """Text column width of a section in twips: page width − left − right − gutter."""
    sz, mar = sect_pr.find(W + "pgSz"), sect_pr.find(W + "pgMar")
    if sz is None or mar is None or sz.get(W + "w") is None:
        return None
    g = lambda k: int(mar.get(W + k) or 0)
    return int(sz.get(W + "w")) - g("left") - g("right") - g("gutter")


def rescale_tabs(tabs: Tuple, template_width: Optional[int], target_width: Optional[int]) -> Tuple:
    """Right/end stops keep their distance from the right margin; center stops scale with the width; other stops
    stay. Positions are integers: center → floor(pos × target / template + 0.5)."""
    if not template_width or not target_width or template_width == target_width:
        return tuple(tabs)
    out = []
    for pos, val, leader in tabs:
        if val in ("right", "end"):
            pos = pos + (target_width - template_width)
        elif val == "center":
            pos = int(pos * target_width / template_width + 0.5)
        out.append((pos, val, leader))
    return tuple(sorted(out))


class Resolver:
    def __init__(self, sheet: StyleSheet, fonts: Dict[str, str], extra_styles: Optional[Dict[str, Any]] = None):
        self.sheet = sheet
        self.fonts = fonts
        self.styles = dict(sheet.styles)
        self.defaults = dict(sheet.defaults)
        for sid, el in (extra_styles or {}).items():       # imported styles join the target's sheet
            self.styles[sid] = el
        root = sheet.root
        self.dd_ppr = root.find(f"{W}docDefaults/{W}pPrDefault/{W}pPr") if root is not None else None
        self.dd_rpr = root.find(f"{W}docDefaults/{W}rPrDefault/{W}rPr") if root is not None else None

    # ---- chains ----
    def chain(self, sid: Optional[str]) -> List[Any]:
        """Root-most style first, the named style last (cycle-safe)."""
        seen, out = set(), []
        while sid and sid not in seen and sid in self.styles:
            # MS-OI29500 §2.1.242(a): these IDs ignore all child elements.
            if sid in {"DefaultParagraphFont", "TableNormal", "NoList"}:
                break
            seen.add(sid)
            out.append(self.styles[sid])
            b = self.styles[sid].find(W + "basedOn")
            sid = b.get(W + "val") if b is not None else None
        return list(reversed(out))

    def _table_style(self, p) -> Optional[str]:
        q = p.getparent()
        while q is not None and q.tag != W + "tbl":
            q = q.getparent()
        if q is None:
            return None
        ts = q.find(f"{W}tblPr/{W}tblStyle")
        return ts.get(W + "val") if ts is not None else self.defaults.get("table")

    def _pstyle(self, p) -> Optional[str]:
        ps = p.find(f"{W}pPr/{W}pStyle")
        return ps.get(W + "val") if ps is not None else self.defaults.get("paragraph")

    def _style_levels(self, p, rpr=None, for_run: bool = False) -> List[List[Any]]:
        """Style levels (each a chain, root first): table, paragraph, [character]."""
        levels = []
        ts = self._table_style(p)
        if ts:
            levels.append(self.chain(ts))
        levels.append(self.chain(self._pstyle(p)))
        if for_run:
            rs = rpr.find(W + "rStyle") if rpr is not None else None
            levels.append(self.chain(rs.get(W + "val") if rs is not None else self.defaults.get("character")))
        return levels

    # ---- paragraph ----
    def paragraph(self, p, direct=True) -> Dict[str, Any]:
        """Effective paragraph properties; ``direct=False`` resolves the style levels only (for tab pinning)."""
        pprs = [self.dd_ppr]
        for chain in self._style_levels(p):
            pprs += [st.find(W + "pPr") for st in chain]
        if direct:
            pprs.append(p.find(W + "pPr"))
        out = dict(PARA_DEFAULTS)
        tabs: Dict[int, Tuple[str, Optional[str]]] = {}
        for ppr in pprs:
            if ppr is None:
                continue
            sp = ppr.find(W + "spacing")
            if sp is not None:
                for k in ("before", "after", "line", "lineRule"):
                    if sp.get(W + k) is not None:
                        out[k] = sp.get(W + k)
            ind = ppr.find(W + "ind")
            if ind is not None:
                for k, alias in (("left", "start"), ("right", "end")):
                    v = ind.get(W + k) if ind.get(W + k) is not None else ind.get(W + alias)
                    if v is not None:
                        out[k] = v
                if ind.get(W + "firstLine") is not None:
                    out["first"] = str(int(ind.get(W + "firstLine")))
                if ind.get(W + "hanging") is not None:
                    out["first"] = str(-int(ind.get(W + "hanging")))
            jc = ppr.find(W + "jc")
            if jc is not None and jc.get(W + "val"):
                out["jc"] = {"start": "left", "end": "right"}.get(jc.get(W + "val"), jc.get(W + "val"))
            tb = ppr.find(W + "tabs")
            if tb is not None:
                for t in tb.findall(W + "tab"):
                    pos = int(t.get(W + "pos"))
                    if t.get(W + "val") == "clear":
                        tabs.pop(pos, None)
                    else:
                        tabs[pos] = (t.get(W + "val"), t.get(W + "leader"))
        out["tabs"] = tuple(sorted((pos, v, l) for pos, (v, l) in tabs.items()))
        return out

    # ---- run ----
    def run(self, rpr, p) -> Dict[str, Any]:
        levels = self._style_levels(p, rpr, for_run=True)
        style_rprs = [self.dd_rpr] + [st.find(W + "rPr") for chain in levels for st in chain]
        out = dict(RUN_DEFAULTS)
        for r in style_rprs + [rpr]:
            if r is None:
                continue
            rf = r.find(W + "rFonts")
            if rf is not None:
                for s in SLOTS:
                    th = rf.get(W + THEME_ATTR[s])
                    if th:
                        out[s] = self._theme(th)
                    elif rf.get(W + s):
                        out[s] = rf.get(W + s)
            for k in ("sz", "szCs"):
                e = r.find(W + k)
                if e is not None and e.get(W + "val"):
                    out[k] = e.get(W + "val")
            c = r.find(W + "color")
            if c is not None:
                out["color"] = (c.get(W + "val"), c.get(W + "themeColor"), c.get(W + "themeShade"), c.get(W + "themeTint"))
        for t in TOGGLES:
            specs = [r.find(W + t) for r in style_rprs if r is not None and r.find(W + t) is not None]
            if len(specs) > 1:
                raise AmbiguousToggle(f"toggle w:{t} is specified at {len(specs)} style levels; Word XORs them "
                                      f"(§17.7.3) — not modelled")
            if specs:
                out[t] = specs[0].get(W + "val") not in FALSE
            d = rpr.find(W + t) if rpr is not None else None
            if d is not None:
                out[t] = d.get(W + "val") not in FALSE
        return out

    def _theme(self, value: str) -> str:
        m = re.match(r"(major|minor)(HAnsi|Ascii|EastAsia|Bidi)$", value or "")
        if not m:
            return f"theme:{value}"
        script = {"HAnsi": "latin", "Ascii": "latin", "EastAsia": "ea", "Bidi": "cs"}[m.group(2)]
        return self.fonts.get(f"{m.group(1)}:{script}", f"theme:{value}")


# ---- set_properties: the effective value of one property, with its provenance -----------------------------------
# The op's own definition (docs/SET_PROPERTIES_DESIGN.md §3, docs/BUILTIN_OPS_BATCH6_DESIGN.md §3), apart from the
# pinnable resolver above: the verifier re-implements it in verify/props.py (_v_run_prop / _v_para_prop) — CLAUDE.md
# lists the two among the definitions that must stay identical. Toggles (b, bCs, i, iCs, caps, smallCaps, strike,
# dstrike, vanish): direct formatting wins; specified at exactly one style level (docDefaults, table, paragraph and
# character style chains) → that value; nowhere → off; more than one → ambiguous (None; Word XORs them — not modelled).
# w:dstrike is no toggle in ECMA-376's list, but it is read as one here (as numbering_convert's look does): specified at
# two style levels it is set directly rather than trusted to either reading. w:u, w:color, w:vertAlign, w:highlight,
# the character w:spacing and the paragraph flags: the nearest level that specifies them, else the default.
# Provenance: "direct", "style:<id>", "docDefaults" or "default".

PROP_PAIRS = {"b": ("b", "bCs"), "i": ("i", "iCs"), "caps": ("caps",), "smallCaps": ("smallCaps",),
              "strike": ("strike",), "dstrike": ("dstrike",), "vanish": ("vanish",)}
RUN_TOGGLE_ELEMENTS = ("b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike", "vanish")
PARA_FLAGS = ("keepNext", "keepLines", "widowControl", "pageBreakBefore")
PARA_FLAG_DEFAULTS = {"keepNext": False, "keepLines": False, "widowControl": True, "pageBreakBefore": False}
CASE_ELEMENTS = {"caps", "smallCaps"}          # never on a symbol run: case-mapping a dingbat changes the glyph
# change glyph widths, positions or what is shown at all: the text reflows
REFLOW_ELEMENTS = {"b", "bCs", "i", "iCs", "caps", "smallCaps", "vanish", "vertAlign", "spacing"}
UNDERLINES = ("single", "double", "words", "dotted", "dash", "wave", "none")
VERT_ALIGNS = ("superscript", "subscript", "baseline")
COLOR_DEFAULT = ("auto", None, None, None)
# the default of each value property nothing specifies (w:spacing: twentieths of a point, as an integer)
VALUE_DEFAULTS = {"u": "none", "highlight": "none", "vertAlign": "baseline", "spacing": 0}
# single and double strikethrough exclude each other, as in Word: asking one on turns the other off
STRIKE_PARTNER = {"strike": "dstrike", "dstrike": "strike"}


def on_off(el) -> Optional[bool]:
    """A CT_OnOff element's value: None when absent, else on unless w:val is 0/false/off."""
    return None if el is None else el.get(W + "val") not in FALSE


def prop_read(element: str, el):
    """The value an element gives its property: on/off for toggles and paragraph flags, the (val, themeColor,
    themeShade, themeTint) tuple for w:color, w:val for w:u, w:vertAlign and w:highlight (None when the attribute is
    missing), w:spacing's w:val as an integer (its text when it is not one — a measure with a unit is never "already")."""
    if el is None:
        return None
    if element == "color":
        return (el.get(W + "val"), el.get(W + "themeColor"), el.get(W + "themeShade"), el.get(W + "themeTint"))
    if element in ("u", "vertAlign", "highlight"):
        return el.get(W + "val")
    if element == "spacing":
        val = el.get(W + "val")
        return int(val) if val is not None and re.fullmatch(r"-?[0-9]+", val) else val
    return on_off(el)


def twentieths(points: Any) -> int:
    """Character spacing in points (validated by the op: a multiple of 0.05 pt) as w:spacing's twentieths of a point."""
    return int(round(float(points) * 20))


def run_elements(run_set: Dict[str, Any]) -> Dict[str, Any]:
    """User-facing run properties (validated) → the asked value per element: b/i expand to the complex-script pairs
    Word itself writes (a w:cs run draws its weight and slant from w:bCs/w:iCs), color to the effective tuple, spacing
    to twentieths of a point; strike or dstrike on brings its partner off (unless asked too), as Word does."""
    out: Dict[str, Any] = {}
    for k, v in (run_set or {}).items():
        if k in PROP_PAIRS:
            for e in PROP_PAIRS[k]:
                out[e] = bool(v)
        elif k == "color":
            out["color"] = (v, None, None, None)
        elif k in ("u", "vertAlign", "highlight"):
            out[k] = v
        elif k == "spacing":
            out["spacing"] = twentieths(v)
        else:
            raise ValueError(f"unknown run property {k!r}")
    for k, partner in STRIKE_PARTNER.items():
        if out.get(k) is True and partner not in (run_set or {}):
            out[partner] = False
    return out


def para_elements(para_set: Dict[str, Any]) -> Dict[str, bool]:
    out: Dict[str, bool] = {}
    for k, v in (para_set or {}).items():
        if k not in PARA_FLAGS:
            raise ValueError(f"unknown paragraph property {k!r}")
        out[k] = bool(v)
    return out


class PropResolver:
    """Effective value and provenance of set_properties' properties. ``override`` {(style id, element): value} stands in
    for a style's own element — the plan simulates a style change on the source with it."""

    def __init__(self, sheet: StyleSheet, override: Optional[Dict[Tuple[str, str], Any]] = None):
        self.res = Resolver(sheet, {})
        self.override = dict(override or {})

    def _levels_run(self, p, rpr) -> List[Tuple[str, Any]]:
        out: List[Tuple[str, Any]] = [("docDefaults", self.res.dd_rpr)]
        for chain in self.res._style_levels(p, rpr, for_run=True):
            out += [(f"style:{st.get(W + 'styleId')}", st.find(W + "rPr")) for st in chain]
        return out

    def _levels_para(self, p) -> List[Tuple[str, Any]]:
        out: List[Tuple[str, Any]] = [("docDefaults", self.res.dd_ppr)]
        for chain in self.res._style_levels(p):
            out += [(f"style:{st.get(W + 'styleId')}", st.find(W + "pPr")) for st in chain]
        return out

    def _spec(self, prov: str, container, element: str) -> Tuple[bool, Any]:
        """(specified at this level, its value); the override replaces a style's own element."""
        if prov.startswith("style:") and (prov[6:], element) in self.override:
            return True, self.override[(prov[6:], element)]
        el = container.find(W + element) if container is not None else None
        return el is not None, (prop_read(element, el) if el is not None else None)

    def _resolve(self, levels: List[Tuple[str, Any]], direct, element: str) -> Tuple[Any, str]:
        d = direct.find(W + element) if direct is not None else None
        if d is not None:
            return prop_read(element, d), "direct"
        if element in RUN_TOGGLE_ELEMENTS:
            specs = []
            for prov, c in levels:
                ok, v = self._spec(prov, c, element)
                if ok:
                    specs.append((v, prov))
            if len(specs) == 1:
                return specs[0]
            return (False, "default") if not specs else (None, "ambiguous")
        for prov, c in reversed(levels):
            ok, v = self._spec(prov, c, element)
            if ok:
                return v, prov
        if element == "color":
            return COLOR_DEFAULT, "default"
        if element in PARA_FLAGS:
            return PARA_FLAG_DEFAULTS[element], "default"
        return VALUE_DEFAULTS.get(element, "none"), "default"

    def run_prop(self, rpr, p, element: str) -> Tuple[Any, str]:
        return self._resolve(self._levels_run(p, rpr), rpr, element)

    def para_prop(self, p, element: str) -> Tuple[Any, str]:
        return self._resolve(self._levels_para(p), p.find(W + "pPr"), element)

    def gets_run(self, rpr, p, asked: Dict[str, Any], symbol: bool) -> Dict[str, Any]:
        """The asked elements a run or mark gets: those whose effective value is not the asked one (an ambiguous
        toggle gets it — direct formatting wins); caps/smallCaps never on a symbol run."""
        return {e: v for e, v in asked.items()
                if not (symbol and e in CASE_ELEMENTS) and self.run_prop(rpr, p, e)[0] != v}

    def gets_para(self, p, asked: Dict[str, bool]) -> Dict[str, bool]:
        return {e: v for e, v in asked.items() if self.para_prop(p, e)[0] != v}

    @staticmethod
    def style_gets(st, container: str, asked: Dict[str, Any]) -> Dict[str, Any]:
        """A style definition's own values (its rPr or pPr): the asked elements whose own value is not the asked one."""
        c = st.find(W + container)
        return {e: v for e, v in asked.items() if prop_read(e, c.find(W + e) if c is not None else None) != v}
