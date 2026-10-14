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


def theme_fonts(pkg: DocxPackage) -> Dict[str, str]:
    """{"major:latin": "Calibri Light", "minor:cs": "", …} from the package's theme part (resolved via rels)."""
    main = pkg.main_part()
    part = next((r.resolved for r in pkg.rels(main).values() if r.type == THEME_REL and r.resolved
                 and pkg.has(r.resolved)), None)
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
