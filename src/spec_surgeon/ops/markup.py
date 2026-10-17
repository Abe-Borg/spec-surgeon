"""Effective hidden and colour — transform side (docs/BUILTIN_OPS_BATCH2_DESIGN.md §1.6; CLAUDE.md, both sides). The
verifier's reading is verify/markup.py: keep the two identical.

Hidden (``w:vanish``) is a toggle over set_properties' levels (``PropResolver``: docDefaults, the table style chain, the
paragraph style chain, the character style chain): direct formatting wins; specified at exactly one style level → that
value; nowhere → off; more than one → None (ambiguous: Word XORs them — not modelled; the ops refuse with
``toggle-ambiguous``). A paragraph mark: its ``w:pPr/w:rPr`` read the same way. Colour: the nearest level that sets
``w:color`` (``PropResolver``'s rule), default ``("auto", None, None, None)``. A colour list matches a 6-digit hex
(``w:val``), a theme colour name (``w:themeColor``) or ``auto``, case-insensitive.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional, Set, Tuple

from ..package import W
from .effective import FALSE, PropResolver
from .selectors import StyleSheet


class Look:
    """How a run or a paragraph mark looks, as far as batch 2's ops need: hidden, colour."""

    def __init__(self, sheet: StyleSheet):
        self.pr = PropResolver(sheet)

    def toggle(self, rpr, p, element: str) -> Optional[bool]:
        d = rpr.find(W + element) if rpr is not None else None
        if d is not None:
            return d.get(W + "val") not in FALSE
        found = [c.find(W + element) for _, c in self.pr._levels_run(p, rpr)
                 if c is not None and c.find(W + element) is not None]
        if not found:
            return False
        return found[0].get(W + "val") not in FALSE if len(found) == 1 else None

    def hidden(self, rpr, p) -> Optional[bool]:
        return self.toggle(rpr, p, "vanish")

    def mark_hidden(self, p) -> Optional[bool]:
        ppr = p.find(W + "pPr")
        return self.hidden(ppr.find(W + "rPr") if ppr is not None else None, p)

    def color(self, rpr, p) -> Tuple[Any, ...]:
        return self.pr.run_prop(rpr, p, "color")[0]


def color_names(colors: Iterable[str]) -> Set[str]:
    return {str(c).strip().lstrip("#").casefold() for c in colors if str(c).strip()}


def color_in(color: Tuple[Any, ...], wanted: Set[str]) -> bool:
    val, theme = color[0] or "", color[1] or ""
    return bool(theme and theme.casefold() in wanted) or bool(val and val.casefold() in wanted)


def valid_color(c: Any) -> bool:
    """A colour a list may name: 6 hex digits (optionally after #), ``auto``, or a theme colour name."""
    s = str(c).strip().lstrip("#")
    if len(s) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in s):
        return True
    return s.casefold() in THEME_COLORS | {"auto"}


THEME_COLORS = {c.casefold() for c in ("dark1", "light1", "dark2", "light2", "accent1", "accent2", "accent3", "accent4",
                                       "accent5", "accent6", "hyperlink", "followedHyperlink", "background1",
                                       "text1", "background2", "text2")}
