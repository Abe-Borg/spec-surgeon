"""Op: set_table_props — table borders, cell margins, width, repeated header rows and rows that do not break across
pages (built-in batch 5, item 37 — docs/BUILTIN_OPS_BATCH5_DESIGN.md §5.2).

Formatting mode: zero text diffs, never tracked (``tracked: true`` → ``bad-params``; ``w:tblPrChange`` /
``w:trPrChange`` are not written). The tables are chosen by the table selector (``ops/table_select.py``: ``select.scope``
and ``select.tables``).

Parameters (at least one of the first five):

* ``borders`` — ``"none"``, or an object of sides ``top | left | bottom | right | insideH | insideV``, each a border
  ``{style, size, space, color}`` (batch 4's border object: ``style`` default ``single``, ``size`` eighths of a point 2–96
  default 4, ``space`` points 0–31 default **0** — Word's value for table borders —, ``color`` ``auto`` or RRGGBB
  default ``auto``). **The table's whole border set**: a side not named has no border;
* ``cell_margins`` — ``{top, left, bottom, right}`` in twips (0–31680), merged into the table's own ``w:tblCellMar``;
* ``width`` — ``{"pct": 1–100}``, ``{"twips": 1–31680}`` or ``"auto"``;
* ``repeat_header_rows`` — n ≥ 0: the first n rows repeat at the top of every page, no later row does;
* ``cant_split`` — true / false: every row is kept on one page / may break across pages;
* ``clear_cell_overrides`` (default false; with ``borders`` or ``cell_margins``) — the sides of every own cell's
  ``w:tcBorders`` / ``w:tcMar`` that compete with what was asked go, so the table's show (``cell_sides``: with borders —
  always the whole set — every side but the diagonals ``tl2br``/``tr2bl``; with cell margins only the asked sides, each
  with its start/end spelling); an emptied ``w:tcBorders`` / ``w:tcMar``, then an emptied ``w:tcPr``, goes.

Definitions (CLAUDE.md lists them among the definitions that must stay identical on both sides; the verifier's reading
is verify/tableprops.py):

* **The table style chain** (``table_select.style_chain``): the table's style (``w:tblStyle``, else the default table
  style), then its ``basedOn`` ancestors.
* **A visible side** (``side_visible``): a side element whose ``w:val`` is not ``nil`` or ``none``.
* **Expected ``w:tblBorders``** (``expected_borders``): the sides in the order top, left, bottom, right, insideH,
  insideV — an asked side exactly ``{w:val, w:sz, w:space, w:color}``; a side not asked that ANY level of the table style
  chain shows visibly in its ``w:tblPr/w:tblBorders`` as ``{w:val="nil"}``; else absent. No side → no ``w:tblBorders``.
* **Expected ``w:tblCellMar``** (``expected_margins``): the table's own ``w:tblCellMar`` (an empty one when it has none)
  with each asked side's old elements removed and ``{w:w, w:type="dxa"}`` inserted in schema order; the others kept.
* **Expected ``w:tblW``**: ``{w:w, w:type}`` — pct ``w:w`` = pct × 50, ``dxa`` the twips, ``auto`` ``w:w="0"``.
* **Expected row elements** (``expected_row_element``): row r < n → ``<w:tblHeader/>``; r ≥ n → ``<w:tblHeader
  w:val="0"/>`` when ANY level of the table style chain's ``w:trPr`` turns ``w:tblHeader`` on (present, ``w:val`` not
  0/false/off), else none; ``cant_split`` true → ``<w:cantSplit/>``, false → ``<w:cantSplit w:val="0"/>`` when the chain
  turns it on, else none.
* **Already** (``already``): the own elements of that name (direct children) are exactly the expected one (none and
  none counts). A table, row or cell with nothing to change is left alone and not counted.
* **Writing** (``write_element``): every old element of the name goes; the expected one is inserted in schema order
  (``ooxml.TBLPR_ORDER``, ``TRPR_ORDER``; tblW, tblBorders, tblCellMar in that order; cantSplit before tblHeader); a row
  without a ``w:trPr`` gets one right after its ``w:tblPrEx`` (first otherwise); an emptied ``w:trPr`` / ``w:tcPr`` goes.

Left alone and flagged (in this order, checked before any refusal of that table): a table in a text box when the
selection is every table (``table_select.selects_all``; a selection that names tables refuses it, ``textbox``); a table
with fewer own rows than the header rows asked (after the rows-in-a-container refusal). Refused: ``tracked: true``
(``bad-params``); a table without ``w:tblPr``, rows or cells wrapped in a content control or custom XML when rows or
cells are set (``table-structure``); a table that runs right to left (``w:bidiVisual`` on in its own ``w:tblPr`` or any
level of its style chain) when borders or cell margins are asked, a table whose own ``w:tblBorders`` (borders asked) or
``w:tblCellMar`` (cell margins asked), or whose style chain's ``w:tblBorders`` (borders asked), spells a side
``start``/``end`` (``conflict-property``: which side Word means depends on the table's direction — say left/right in
Word first). Flagged, never refused: cells that keep their own borders / margins (without ``clear_cell_overrides``) and
rows whose ``w:tblPrEx`` overrides the table — every row and cell of the table counted, those in a content control
too —, a table style's conditional formatting (``w:tblStylePr``) or cell-level ``w:tcPr`` with borders, or
``w:tblStylePr`` setting header rows / row splitting (not modelled), header rows asked of a floating (``w:tblpPr``) or
nested table (Word repeats header rows only for tables in the text flow), and a pending tracked table/row/cell property
change on a changed table, row or cell (reject-all would restore that older snapshot).

Verified by V10's ``table_props`` kind (verify/tableprops.py), which re-derives all of the above on its own.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from lxml import etree

from ..ooxml import TBL_CELLMAR_ORDER, TBLPR_ORDER, TRPR_ORDER, insert_in_order
from ..package import W, DocxPackage
from . import prose, register
from .base import JobException, Op, PlanFragment
from .selectors import StyleSheet, scope_parts
from .set_borders_shading import BORDER_STYLE_WORDS
from .table_select import (all_cells, all_rows, build, cells_wrapped, check_table_selector, element_sig, in_text_box,
                           is_nested, own_cells, own_rows, right_to_left, rows_wrapped, selection_reads, selects_all,
                           style_chain, style_chain_reads, table_selected)

TABLE_SIDES = ("top", "left", "bottom", "right", "insideH", "insideV")
MARGIN_SIDES = ("top", "left", "bottom", "right")
ALIASES = ("start", "end")
CELL_BORDER_SIDES = ("top", "start", "left", "bottom", "end", "right", "insideH", "insideV")   # never the diagonals
MARGIN_ALIAS = {"left": "start", "right": "end"}
NOT_VISIBLE = ("nil", "none")
OFF = ("0", "false", "off")
TBL_ELEMENTS = ("tblW", "tblBorders", "tblCellMar")         # written in this order (schema order)
ROW_ELEMENTS = ("cantSplit", "tblHeader")                   # written in this order (Word's order)
BORDER_STYLES = ("single", "thick", "double", "dotted", "dashed", "dotDash", "dotDotDash", "triple",
                 "thinThickSmallGap", "thickThinSmallGap", "wave", "doubleWave", "dashSmallGap", "threeDEmboss",
                 "threeDEngrave", "outset", "inset")
HEX_COLOR = re.compile(r"[0-9A-Fa-f]{6}")
PARAM_KEYS = {"borders", "cell_margins", "width", "repeat_header_rows", "cant_split", "clear_cell_overrides", "tracked"}
MAX_TWIPS = 31680
LAYOUT_REASON = "table borders, cell margins, widths and row breaks change the layout by design"
SCOPE_SCHEMA = {"type": "array", "items": {"type": "string", "enum": ["document", "headers", "footers", "footnotes",
                                                                       "endnotes", "comments"]}, "default": ["document"]}
TABLES_SCHEMA = {"description": "'all' (default) or {index_range: [a, b] (table ordinals in the part, both inclusive, "
                                "0-based, nested tables counted), style_in: [table style ids], first_row_regex, "
                                "text_regex, nested: bool} — every given key must hold"}
BORDER_SCHEMA = {"type": "object", "properties": {
    "style": {"type": "string", "enum": list(BORDER_STYLES), "default": "single"},
    "size": {"type": "integer", "description": "eighths of a point, 2–96", "default": 4},
    "space": {"type": "integer", "description": "points, 0–31 (Word writes 0 for tables)", "default": 0},
    "color": {"type": "string", "description": "'auto' or 6 hex digits (RRGGBB)", "default": "auto"}}}

Attrs = List[Tuple[str, str]]


# ---- parameters -----------------------------------------------------------------------------------------------------

@dataclass
class Asked:
    borders: Optional[Dict[str, Attrs]] = None       # None: not asked; {}: "none"; else side → attributes
    margins: Optional[Dict[str, Attrs]] = None       # side → [("w", twips), ("type", "dxa")]
    width: Optional[Attrs] = None                    # [("w", …), ("type", …)]
    header_rows: Optional[int] = None
    cant_split: Optional[bool] = None
    clear_cells: bool = False

    @property
    def rows(self) -> bool:
        return self.header_rows is not None or self.cant_split is not None

    def cell_names(self) -> List[str]:
        """The cell elements the asked table elements compete with (cleared with clear_cell_overrides, else flagged)."""
        return [n for n, v in (("tcBorders", self.borders), ("tcMar", self.margins)) if v is not None]


def _whole(v: Any, lo: int, hi: int, where: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise JobException(f"{where} must be a whole number from {lo} to {hi}, got {v!r}", "bad-params")
    return v


def _color(v: Any, where: str) -> str:
    if v == "auto":
        return "auto"
    if isinstance(v, str) and HEX_COLOR.fullmatch(v):
        return v.upper()
    raise JobException(f"{where} must be 'auto' or 6 hex digits (RRGGBB), got {v!r}", "bad-params")


def clean_params(params: Dict[str, Any]) -> Asked:
    """The asked changes, validated (colours upper-cased) — ``bad-params`` for anything the grammar does not allow."""
    unknown = set(params) - PARAM_KEYS
    if unknown:
        raise JobException(f"set_table_props: unknown parameter(s) {sorted(unknown)}; known: {sorted(PARAM_KEYS)}",
                           "bad-params")
    if params.get("tracked") not in (None, False):
        raise JobException("table property changes are never tracked (Word's tracked table-property changes are not "
                           "written); omit 'tracked' or set it false", "bad-params")
    a = Asked()
    b = params.get("borders")
    if b is not None:
        if b == "none":
            a.borders = {}
        elif isinstance(b, dict) and b:
            unknown = set(b) - set(TABLE_SIDES)
            if unknown:
                raise JobException(f"unknown table border side(s) {sorted(unknown)}; known: {list(TABLE_SIDES)} "
                                   f"(say left/right, not start/end)", "bad-params")
            a.borders = {}
            for side in TABLE_SIDES:
                if side not in b:
                    continue
                v = b[side]
                if not isinstance(v, dict) or set(v) - {"style", "size", "space", "color"}:
                    raise JobException(f"borders.{side} must be an object {{style, size, space, color}}", "bad-params")
                style = v.get("style", "single")
                if style not in BORDER_STYLES:
                    raise JobException(f"borders.{side}.style must be one of {list(BORDER_STYLES)}", "bad-params")
                a.borders[side] = [("val", style), ("sz", str(_whole(v.get("size", 4), 2, 96, f"borders.{side}.size"))),
                                   ("space", str(_whole(v.get("space", 0), 0, 31, f"borders.{side}.space"))),
                                   ("color", _color(v.get("color", "auto"), f"borders.{side}.color"))]
        else:
            raise JobException("borders must be 'none' or an object of sides (top, left, bottom, right, insideH, "
                               "insideV) — the table's whole border set; 'none' removes every border", "bad-params")
    m = params.get("cell_margins")
    if m is not None:
        if not isinstance(m, dict) or not m or set(m) - set(MARGIN_SIDES):
            raise JobException(f"cell_margins must be an object of {list(MARGIN_SIDES)} in twips (say left/right, not "
                               f"start/end)", "bad-params")
        a.margins = {side: [("w", str(_whole(m[side], 0, MAX_TWIPS, f"cell_margins.{side}"))), ("type", "dxa")]
                     for side in MARGIN_SIDES if side in m}
    w = params.get("width")
    if w is not None:
        if w == "auto":
            a.width = [("w", "0"), ("type", "auto")]
        elif isinstance(w, dict) and set(w) == {"pct"}:
            a.width = [("w", str(_whole(w["pct"], 1, 100, "width.pct") * 50)), ("type", "pct")]
        elif isinstance(w, dict) and set(w) == {"twips"}:
            a.width = [("w", str(_whole(w["twips"], 1, MAX_TWIPS, "width.twips"))), ("type", "dxa")]
        else:
            raise JobException("width must be {'pct': 1–100}, {'twips': n} or 'auto'", "bad-params")
    n = params.get("repeat_header_rows")
    if n is not None:
        a.header_rows = _whole(n, 0, 10 ** 6, "repeat_header_rows")
    c = params.get("cant_split")
    if c is not None:
        if not isinstance(c, bool):
            raise JobException("cant_split must be true or false", "bad-params")
        a.cant_split = c
    clear = params.get("clear_cell_overrides", False)
    if not isinstance(clear, bool):
        raise JobException("clear_cell_overrides must be true or false", "bad-params")
    if clear and a.borders is None and a.margins is None:
        raise JobException("clear_cell_overrides clears the cells' own borders (with 'borders') or margins (with "
                           "'cell_margins'); give one of them", "bad-params")
    a.clear_cells = clear
    if a.borders is None and a.margins is None and a.width is None and not a.rows:
        raise JobException("nothing to set: give borders, cell_margins, width, repeat_header_rows and/or cant_split",
                           "bad-params")
    return a


# ---- the definitions ------------------------------------------------------------------------------------------------

def side_visible(el) -> bool:
    return el is not None and el.get(W + "val") not in NOT_VISIBLE


def is_on(el) -> bool:
    return el is not None and el.get(W + "val") not in OFF


def expected_borders(asked: Dict[str, Attrs], chain: Sequence[Any]):
    """The expected w:tblBorders (None for no element): asked sides exactly, a side any style level shows as nil."""
    shown = set()
    for st in chain:
        bdr = st.find(f"{W}tblPr/{W}tblBorders")
        if bdr is not None:
            shown |= {side for side in TABLE_SIDES if side_visible(bdr.find(W + side))}
    kids = []
    for side in TABLE_SIDES:
        if side in asked:
            kids.append(build(side, asked[side]))
        elif side in shown:
            kids.append(build(side, [("val", "nil")]))
    return build("tblBorders", [], kids) if kids else None


def expected_margins(asked: Dict[str, Attrs], own):
    """The table's own w:tblCellMar with the asked sides set (the others kept)."""
    el = copy.deepcopy(own) if own is not None else etree.Element(W + "tblCellMar")
    for side in MARGIN_SIDES:
        if side in asked:
            for old in el.findall(W + side):
                el.remove(old)
            insert_in_order(el, build(side, asked[side]), TBL_CELLMAR_ORDER)
    return el


def chain_turns_on(chain: Sequence[Any], name: str) -> bool:
    """ANY level of the table style chain's w:trPr turns ``name`` on."""
    return any(is_on(e) for st in chain for e in st.findall(f"{W}trPr/{W}{name}"))


def expected_row_element(name: str, want_on: bool, chain: Sequence[Any]):
    if want_on:
        return build(name, [])
    return build(name, [("val", "0")]) if chain_turns_on(chain, name) else None


def already(parent, name: str, expected) -> bool:
    own = [element_sig(e) for e in parent.findall(W + name)] if parent is not None else []
    return own == ([element_sig(expected)] if expected is not None else [])


def decide_table(tblpr, chain: Sequence[Any], asked: Asked) -> Dict[str, Any]:
    """The table's own elements that change → the expected element (None for "no element")."""
    gets: Dict[str, Any] = {}
    want: Dict[str, Any] = {}
    if asked.width is not None:
        want["tblW"] = build("tblW", asked.width)
    if asked.borders is not None:
        want["tblBorders"] = expected_borders(asked.borders, chain)
    if asked.margins is not None:
        want["tblCellMar"] = expected_margins(asked.margins, tblpr.find(W + "tblCellMar"))
    for name, el in want.items():
        if not already(tblpr, name, el):
            gets[name] = el
    return gets


def decide_rows(tbl, chain: Sequence[Any], asked: Asked) -> List[Tuple[int, Dict[str, Any]]]:
    """(row index, {element: expected}) of every own row whose header/split elements change."""
    out = []
    if not asked.rows:
        return out
    for r, tr in enumerate(own_rows(tbl)):
        trpr = tr.find(W + "trPr")
        gets: Dict[str, Any] = {}
        if asked.cant_split is not None:
            want = expected_row_element("cantSplit", asked.cant_split, chain)
            if not already(trpr, "cantSplit", want):
                gets["cantSplit"] = want
        if asked.header_rows is not None:
            want = expected_row_element("tblHeader", r < asked.header_rows, chain)
            if not already(trpr, "tblHeader", want):
                gets["tblHeader"] = want
        if gets:
            out.append((r, gets))
    return out


def cell_sides(asked: Asked) -> Dict[str, List[str]]:
    """The sides of a cell's own ``w:tcBorders`` / ``w:tcMar`` that compete with what was asked: with borders (always
    the whole border set) every side but the diagonals (``tl2br``/``tr2bl`` have no table-level counterpart); with cell
    margins only the asked sides — each with its start/end spelling (a right-to-left table is refused)."""
    out: Dict[str, List[str]] = {}
    if asked.borders is not None:
        out["tcBorders"] = list(CELL_BORDER_SIDES)
    if asked.margins is not None:
        out["tcMar"] = [s for side in MARGIN_SIDES if side in asked.margins for s in (side, MARGIN_ALIAS.get(side)) if s]
    return out


def competing(tc, name: str, sides: Sequence[str]) -> List[str]:
    """The sides (local names, sorted) a cell's own ``name`` elements hold among ``sides``."""
    found = set()
    for holder in tc.findall(f"{W}tcPr/{W}{name}"):
        found |= {etree.QName(c).localname for c in holder if isinstance(c.tag, str) and c.tag.startswith(W)
                  and etree.QName(c).localname in sides}
    return sorted(found)


def decide_cells(tbl, asked: Asked) -> List[Tuple[int, int, Dict[str, List[str]]]]:
    """(row, cell, {element: [sides that go]}) of every own cell whose own borders / margins compete with what was
    asked (clear_cell_overrides)."""
    out = []
    if not asked.clear_cells:
        return out
    wanted = cell_sides(asked)
    for r, tr in enumerate(own_rows(tbl)):
        for c, tc in enumerate(own_cells(tr)):
            gone = {n: s for n, s in ((n, competing(tc, n, sides)) for n, sides in wanted.items()) if s}
            if gone:
                out.append((r, c, gone))
    return out


def clear_cell(tc, gone: Dict[str, List[str]]) -> None:
    """The listed sides leave the cell's own elements; an emptied element, then an emptied w:tcPr, goes."""
    tcpr = tc.find(W + "tcPr")
    for name, sides in gone.items():
        for holder in tcpr.findall(W + name):
            for c in list(holder):
                if isinstance(c.tag, str) and c.tag in {W + s for s in sides}:
                    holder.remove(c)
            _drop_if_empty(holder)
    _drop_if_empty(tcpr)


def el_data(el) -> Any:
    """An element as JSON data ([Clark tag, [[Clark attribute, value] …], [children …]]), None for none. Comments and
    processing instructions are not carried (V10 compares the element whole: one inside fails, never passes)."""
    if el is None:
        return None
    return [el.tag, [[k, v] for k, v in el.attrib.items()], [el_data(c) for c in el if isinstance(c.tag, str)]]


def el_from(data: Any):
    if data is None:
        return None
    tag, attrs, kids = data
    el = etree.Element(tag)
    for k, v in attrs:
        el.set(k, v)
    for kid in kids:
        el.append(el_from(kid))
    return el


def write_element(parent, name: str, el, order: Sequence[str]) -> None:
    """Every old element ``name`` goes; ``el`` (when not None) is inserted in schema order."""
    for old in parent.findall(W + name):
        parent.remove(old)
    if el is not None:
        insert_in_order(parent, el, list(order))


def row_properties(tr):
    """The row's w:trPr, created right after its w:tblPrEx (first otherwise) when it has none."""
    trpr = tr.find(W + "trPr")
    if trpr is None:
        trpr = etree.Element(W + "trPr")
        ex = [i for i, c in enumerate(tr) if c.tag == W + "tblPrEx"]
        tr.insert(ex[-1] + 1 if ex else 0, trpr)
    return trpr


def _drop_if_empty(el) -> None:
    if el is not None and not len(el) and not el.attrib and el.getparent() is not None:
        el.getparent().remove(el)


def _describe(asked: Asked) -> str:
    out = []
    if asked.borders is not None:
        out.append("borders " + (", ".join(f"{s} {dict(a)['val']} {dict(a)['sz']}/8 pt" for s, a in asked.borders.items())
                                 if asked.borders else "none") + " (the whole border set)")
    if asked.margins is not None:
        out.append("cell margins " + ", ".join(f"{s} {dict(a)['w']} twips" for s, a in asked.margins.items()))
    if asked.width is not None:
        w = dict(asked.width)
        out.append("width " + {"pct": f"{int(w['w']) // 50}%", "dxa": f"{w['w']} twips", "auto": "auto"}[w["type"]])
    if asked.header_rows is not None:
        out.append(f"{asked.header_rows} repeated header row(s)")
    if asked.cant_split is not None:
        out.append("rows kept on one page" if asked.cant_split else "rows may break across pages")
    return "; ".join(out)


def _alias_refusal(tbl, chain: Sequence[Any], asked: Asked, where: str) -> None:
    def spells(el) -> bool:
        return el is not None and any(el.find(W + s) is not None for s in ALIASES)
    tblpr = tbl.find(W + "tblPr")
    if (asked.borders is not None or asked.margins is not None) and right_to_left(tbl, chain):
        raise JobException(f"{where} runs right to left (w:bidiVisual), so which side 'left' and 'right' name is not "
                           f"certain — set its borders and margins in Word", "conflict-property", {"table": where})
    if asked.borders is not None:
        if spells(tblpr.find(W + "tblBorders")):
            raise JobException(f"{where}: its own borders name a side start/end; which side Word means depends on the "
                               f"table's direction — set them as left/right in Word first", "conflict-property",
                               {"table": where})
        bad = [st.get(W + "styleId") for st in chain if spells(st.find(f"{W}tblPr/{W}tblBorders"))]
        if bad:
            raise JobException(f"{where}: its table style {bad[0]!r} names a border side start/end; which side a 'nil' "
                               f"left/right cancels is not certain", "conflict-property", {"table": where, "style": bad[0]})
    if asked.margins is not None and spells(tblpr.find(W + "tblCellMar")):
        raise JobException(f"{where}: its own cell margins name a side start/end; which side Word means depends on the "
                           f"table's direction — set them as left/right in Word first", "conflict-property",
                           {"table": where})


def disposition(tbl, chain: Sequence[Any], asked: Asked, every: bool, where: str) -> Optional[str]:
    """Why a selected table is left alone ("in a text box" under an every-table selection; "too few rows" for the header
    rows), or None to plan it; ``JobException`` for a refusal — in this order: text box, no w:tblPr, rows in a container
    (rows or cells set), too few rows, right to left / start-end sides, cells in a container (cells cleared)."""
    if in_text_box(tbl):
        if every:
            return "in a text box"
        raise JobException(f"{where} lies in a text box (Word keeps two copies of a text box)", "textbox",
                           {"table": where})
    if tbl.find(W + "tblPr") is None:
        raise JobException(f"{where} has no table properties (w:tblPr)", "table-structure", {"table": where})
    rows = own_rows(tbl)
    if (asked.rows or asked.clear_cells) and rows_wrapped(tbl):
        raise JobException(f"{where} has rows inside a content control or custom XML; its rows cannot be numbered "
                           f"safely", "table-structure", {"table": where})
    if asked.header_rows is not None and asked.header_rows > len(rows):
        return "too few rows"
    _alias_refusal(tbl, chain, asked, where)
    if asked.clear_cells and any(cells_wrapped(tr) for tr in rows):
        raise JobException(f"{where} has cells inside a content control or custom XML", "table-structure",
                           {"table": where})
    return None


def _conditional_borders(chain: Sequence[Any]) -> bool:
    for st in chain:
        if st.find(f"{W}tcPr/{W}tcBorders") is not None:
            return True
        for c in st.findall(W + "tblStylePr"):
            if c.find(f"{W}tblPr/{W}tblBorders") is not None or c.find(f"{W}tcPr/{W}tcBorders") is not None:
                return True
    return False


def _conditional_rows(chain: Sequence[Any], names: Sequence[str]) -> bool:
    """A table style's conditional formatting (w:tblStylePr) sets one of the row elements asked (not modelled)."""
    return any(c.find(f"{W}trPr/{W}{n}") is not None for st in chain for c in st.findall(W + "tblStylePr") for n in names)


# ---- prose for the plan ---------------------------------------------------------------------------------------------

PLACE_WORDS = {"top": "at the top", "bottom": "at the bottom", "left": "on the left", "right": "on the right",
               "insideH": "between the rows", "insideV": "between the columns"}


def _listed(items: Sequence[str]) -> str:
    """``a, b and c``; with a serial comma (``a, b, and c``) when an item itself holds an ``and`` or a comma, so the
    reader can tell the items apart. Built here: ``prose.join`` has no serial-comma form."""
    items = [x for x in items if x]
    if len(items) < 2 or not any(" and " in x or ", " in x for x in items):
        return prose.join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


def _places(sides: Sequence[str]) -> str:
    """``all around``, ``at the top and bottom``, ``on the left and right``, ``between the rows and columns``: the
    edges first, then the inside lines."""
    edges = [s for s in ("top", "bottom", "left", "right") if s in sides]
    inner = [s for s in ("insideH", "insideV") if s in sides]
    words: List[str] = []
    if len(edges) == 4:
        words.append("all around")
    elif edges == ["top", "bottom"]:
        words.append("at the top and bottom")
    elif edges == ["left", "right"]:
        words.append("on the left and right")
    else:
        words += [PLACE_WORDS[s] for s in edges]
    if inner == ["insideH", "insideV"]:
        words.append("between the rows and columns")
    else:
        words += [PLACE_WORDS[s] for s in inner]
    return prose.join(words)


def table_border_words(borders: Dict[str, Any]) -> str:
    """``a 0.5 pt single-line border all around and between the rows and columns``: sides with one look are grouped,
    and because the asked borders are the table's whole set, the sides not named are said to have none. The border
    style words are the ones paragraph borders use (``set_borders_shading.BORDER_STYLE_WORDS``)."""
    groups: List[Tuple[Tuple[str, Any, Any, str], List[str]]] = []
    for side in ("top", "bottom", "left", "right", "insideH", "insideV"):
        if side not in borders:
            continue
        b = borders[side] if isinstance(borders[side], dict) else {}
        spec = (str(b.get("style", "single")), b.get("size", 4), b.get("space", 0), str(b.get("color", "auto")).upper())
        for known, sides in groups:
            if known == spec:
                sides.append(side)
                break
        else:
            groups.append((spec, [side]))
    phrases: List[str] = []
    for (style, size, space, color), sides in groups:
        words = BORDER_STYLE_WORDS.get(style, prose.quote(style))
        text = f"a {prose.points_from_eighths(size)} {words} border {_places(sides)}"
        if str(space) not in ("0", "0.0"):
            text += f", {prose.points(space)} from the text"
        if color != "AUTO":
            text += f", coloured {prose.colour(color)}"
        phrases.append(text)
    text = prose.join(phrases) if phrases else "no border"
    missing = [s for s in TABLE_SIDES if s not in borders]
    if missing and all(s in ("insideH", "insideV") for s in missing):
        text += " (none between the cells)" if len(missing) == 2 else f" (none {PLACE_WORDS[missing[0]]})"
    elif missing:
        text += " (none on the other sides)"
    return text


def cell_margin_words(margins: Dict[str, Any]) -> str:
    """``0.075 inch on the left and right and 0 inch at the top and bottom``: sides with one value are grouped."""
    groups: List[Tuple[Any, List[str]]] = []
    for side in ("top", "bottom", "left", "right"):
        if side not in margins:
            continue
        for known, sides in groups:
            if known == margins[side]:
                sides.append(side)
                break
        else:
            groups.append((margins[side], [side]))
    return _listed([f"{prose.inches(v)} {_places(sides)}" for v, sides in groups]) if groups else "the values given"


def width_words(width: Dict[str, Any]) -> str:
    """``100% of the text width`` or ``5 inches``."""
    if "pct" in width:
        return f"{width['pct']}% of the text width"
    if "twips" in width:
        return prose.inches(width["twips"])
    return "the width given"


@register
class SetTableProps(Op):
    name = "set_table_props"
    mode = "formatting"
    description = ("Set table borders, cell margins, width, repeated header rows and rows that do not break across "
                   "pages on the selected tables. The asked borders are the table's whole border set — a side not "
                   "named has none, so a border its table style shows is cancelled; cell margins merge into the "
                   "table's own. Adds no characters, never tracked; a table, row or cell already as asked is left "
                   "alone.")
    params_schema = {"type": "object", "properties": {
        "borders": {"type": ["string", "object"],
                    "description": "'none', or the whole border set: {top, left, bottom, right, insideH, insideV} each "
                                   "{style (single…), size (eighths of a point, 2–96), space (points, 0 by default), "
                                   "color ('auto' or RRGGBB)}; a side not named has no border",
                    "properties": {side: BORDER_SCHEMA for side in TABLE_SIDES}},
        "cell_margins": {"type": "object", "description": "{top, left, bottom, right} in twips (1440 = 1 inch), merged "
                                                          "into the table's own cell margins",
                         "properties": {side: {"type": "integer"} for side in MARGIN_SIDES}},
        "width": {"type": ["string", "object"], "description": "{'pct': 1–100} of the text width, {'twips': n} or 'auto'",
                  "properties": {"pct": {"type": "integer"}, "twips": {"type": "integer"}}},
        "repeat_header_rows": {"type": "integer", "description": "the first n rows repeat at the top of each page; "
                                                                 "later rows do not (0: none)"},
        "cant_split": {"type": "boolean", "description": "true: every row stays on one page; false: rows may break"},
        "clear_cell_overrides": {"type": "boolean", "default": False,
                                 "description": "with borders / cell_margins: remove every cell's own borders / "
                                                "margins so the table's show (else they are flagged)"},
        "tracked": {"type": "boolean", "description": "never tracked: false only"}}}
    select_schema = {"type": "object", "properties": {"scope": SCOPE_SCHEMA, "tables": TABLES_SCHEMA}}
    verification_profile = {
        "V2": "zero text diffs", "V4": "no revision delta (never tracked)",
        "V10": "table_props: the selection, the table style chain, each expected element and 'already' re-derived from "
               "the source; each changed table's w:tblPr, row's w:trPr and cell's w:tcPr exactly as expected; every "
               "selected table shows the asked borders, margins, width and rows in the output; residual identical",
        "V7": "none — borders, margins, widths and row breaks move the layout"}

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        heads: List[str] = []
        borders = params.get("borders")
        if isinstance(borders, dict) and borders:
            heads.append("set the borders to " + table_border_words(borders))
        elif borders == "none":
            heads.append("remove every border")
        margins = params.get("cell_margins")
        if isinstance(margins, dict) and margins:
            heads.append("set the cell margins to " + cell_margin_words(margins))
        width = params.get("width")
        if width == "auto":
            heads.append("let the width be set automatically")
        elif isinstance(width, dict) and width:
            heads.append("set the width to " + width_words(width))
        n = params.get("repeat_header_rows")
        if isinstance(n, int) and not isinstance(n, bool):
            if n <= 0:
                heads.append("let no row repeat at the top of each page")
            else:
                heads.append("repeat the first " + ("row" if n == 1 else f"{n} rows") + " at the top of every page")
        if params.get("cant_split") is True:
            heads.append("keep every row on one page")
        elif params.get("cant_split") is False:
            heads.append("let rows break across pages")
        first = prose.sentence(_listed(heads) if heads else "set the table properties",
                               prose.where(select, default="every table"))
        if params.get("clear_cell_overrides") is not True:
            return first
        own = [w for w, k in (("borders", "borders"), ("margins", "cell_margins")) if params.get(k) is not None]
        return first + " " + prose.sentence("the cells' own", prose.join(own) if own else "borders and margins",
                                            "are removed so the table's show")

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        raw_select = dict(select or {})
        unknown = set(raw_select) - {"scope", "tables"}
        if unknown:
            raise JobException(f"set_table_props: unknown selector key(s) {sorted(unknown)}", "bad-params")
        asked = clean_params(params)
        pred = raw_select.get("tables", "all")
        check_table_selector(pred)
        parts = scope_parts(pkg, raw_select.get("scope", ["document"]))
        sheet = StyleSheet(pkg)
        frag = self.fragment(raw_select, params)
        asked_tbl = [n for n, v in (("tblW", asked.width), ("tblBorders", asked.borders),
                                    ("tblCellMar", asked.margins)) if v is not None]
        asked_row = [n for n, v in (("cantSplit", asked.cant_split), ("tblHeader", asked.header_rows)) if v is not None]
        targets: Dict[str, List[list]] = {}
        changed: Dict[str, Dict[str, list]] = {}
        n_tables = n_rows = n_cells = already_n = selected_n = 0
        keep_cells = {"tcBorders": 0, "tcMar": 0}
        exceptions = conditional = conditional_rows = older_tbl = older_tr = older_tc = 0
        boxed: List[str] = []
        short: List[str] = []
        floating: List[str] = []
        wanted_sides = cell_sides(asked)
        for part in parts:
            root = pkg.xml(part)
            frag.touches += sorted(selection_reads(part, root, pred))
            items: List[list] = []
            record = {"tables": [], "rows": [], "cells": []}
            for k, tbl in enumerate(root.iter(W + "tbl")):
                if not table_selected(pred, tbl, k, sheet):
                    continue
                where = f"{part} table {k}"
                chain, _missing = style_chain(tbl, sheet)
                skip = disposition(tbl, chain, asked, selects_all(pred), where)
                if skip == "in a text box":                 # every table: this one is left alone and said so
                    boxed.append(where)
                    continue
                selected_n += 1
                rows = own_rows(tbl)
                if skip:                                    # too short for the header rows: left alone, said so
                    short.append(f"{where} ({len(rows)} row(s))")
                    continue
                tblpr = tbl.find(W + "tblPr")
                # what the prediction and V10's post-condition read (rule 9): the table's style (map_styles,
                # apply_table_style restyle it), its style chain's table properties, and — soft, whether or not they
                # change — the asked elements of the table, its rows and (clearing) its cells
                frag.touches.append((part, f"tbl:{k}", "verify-props:tblPr"))
                frag.touches += [(part, f"tbl:{k}", f"verify-props:{n}") for n in asked_tbl]
                frag.touches += sorted(style_chain_reads(sheet, tbl))
                for r, tr in enumerate(rows):
                    frag.touches += [(part, f"tr:{k}:{r}", f"verify-props:{n}") for n in asked_row]
                    if asked.clear_cells:
                        frag.touches += [(part, f"tc:{k}:{r}:{c}", f"verify-props:{n}")
                                         for c, _ in enumerate(own_cells(tr)) for n in asked.cell_names()]
                tbl_gets = decide_table(tblpr, chain, asked)
                row_gets = decide_rows(tbl, chain, asked)
                cell_gets = decide_cells(tbl, asked)
                if asked.borders is not None and _conditional_borders(chain):
                    conditional += 1
                if asked.rows and _conditional_rows(chain, asked_row):
                    conditional_rows += 1
                if asked.header_rows and (tblpr.find(W + "tblpPr") is not None or is_nested(tbl)):
                    floating.append(where)
                # the flags count every row and cell of the table, those inside a content control too
                every_row = all_rows(tbl)
                if asked_tbl:
                    exceptions += sum(1 for tr in every_row if any(
                        tr.find(f"{W}tblPrEx/{W}{n}") is not None for n in asked_tbl))
                if not asked.clear_cells:
                    for tr in every_row:
                        for tc in all_cells(tr):
                            for n, sides in wanted_sides.items():
                                keep_cells[n] += int(bool(competing(tc, n, sides)))
                if not (tbl_gets or row_gets or cell_gets):
                    already_n += 1
                    continue
                n_tables += 1
                n_rows += len(row_gets)
                n_cells += len(cell_gets)
                if tbl_gets:
                    frag.touches.append((part, f"tbl:{k}", "tblProps"))
                    frag.touches += [(part, f"tbl:{k}", n) for n in tbl_gets]
                    older_tbl += int(tblpr.find(W + "tblPrChange") is not None)
                for r, g in row_gets:
                    frag.touches.append((part, f"tr:{k}:{r}", "trPr"))
                    frag.touches += [(part, f"tr:{k}:{r}", n) for n in g]
                    older_tr += int(rows[r].find(f"{W}trPr/{W}trPrChange") is not None)
                for r, c, names in cell_gets:
                    frag.touches.append((part, f"tc:{k}:{r}:{c}", "tcPr"))
                    frag.touches += [(part, f"tc:{k}:{r}:{c}", n) for n in names]
                    older_tc += int(own_cells(rows[r])[c].find(f"{W}tcPr/{W}tcPrChange") is not None)
                items.append([k, {n: el_data(e) for n, e in tbl_gets.items()},
                              [[r, {n: el_data(e) for n, e in g.items()}] for r, g in row_gets],
                              [[r, c, gone] for r, c, gone in cell_gets]])
                if tbl_gets:
                    record["tables"].append([k, sorted(tbl_gets)])
                record["rows"] += [[k, r, sorted(g)] for r, g in row_gets]
                record["cells"] += [[k, r, c, sorted(names)] for r, c, names in cell_gets]
            if items:
                targets[part] = items
                changed[part] = record
                frag.intended_parts.append(part)
            frag.touches.append((part, "scope", "verify"))      # V10 re-resolves the selection over this part
        frag.property_assertions.append({
            "kind": "table_props", "parts": parts, "select": raw_select,
            "params": {k: params[k] for k in sorted(PARAM_KEYS) if k in params},
            "expected_tables": n_tables, "expected_rows": n_rows, "expected_cells": n_cells, "changed": changed})
        frag.transform_data = {"targets": targets}
        if targets:
            frag.geometry_reference, frag.geometry_reason = "none", LAYOUT_REASON
        frag.notes.append(f"{n_tables} of {selected_n} selected table(s) change → {_describe(asked)}"
                          + (f"; {n_rows} row(s)" if asked.rows else "")
                          + (f"; {n_cells} cell(s) lose their own borders/margins" if asked.clear_cells else "")
                          + " — never tracked")
        if already_n:
            frag.notes.append(f"already as asked, left alone: {already_n} table(s)")
        if not selected_n and not boxed and not short:
            frag.notes.append("no table in this file matches the selection: nothing to change")
        if boxed:
            frag.flags.append(f"{len(boxed)} table(s) in a text box left alone (Word keeps two copies of a text box): "
                              f"{boxed}")
        if short:
            frag.flags.append(f"{len(short)} table(s) have fewer rows than the {asked.header_rows} header row(s) asked "
                              f"and are left alone: {short}")
        if floating:
            frag.flags.append(f"{len(floating)} table(s) get header rows but are floating (w:tblpPr) or nested in "
                              f"another table: Word repeats header rows only for tables in the text flow — {floating}")
        if conditional_rows:
            frag.flags.append(f"{conditional_rows} table(s) use a table style whose conditional formatting (first "
                              f"row, banding …) sets header rows or row splitting of its own — not modelled.")
        if asked.borders is not None:
            frag.notes.append("a side not asked that the table's style shows is written as 'nil' (no border), so the "
                              "asked borders are the table's whole border set")
        for n, what in (("tcBorders", "borders"), ("tcMar", "margins")):
            if keep_cells[n]:
                frag.flags.append(f"{keep_cells[n]} cell(s) keep their own {what} (w:{n}), which win over the "
                                  f"table's; clear_cell_overrides removes them.")
        if exceptions:
            frag.flags.append(f"{exceptions} row(s) carry their own table-property exceptions (w:tblPrEx) for what is "
                              f"set here, which win over the table's for that row.")
        if conditional:
            frag.flags.append(f"{conditional} table(s) use a table style whose conditional formatting (first row, "
                              f"banding …) or cell formatting has borders of its own — not modelled: those rows or "
                              f"cells may still show them.")
        if older_tbl or older_tr or older_tc:
            frag.flags.append(f"{older_tbl} table(s), {older_tr} row(s) and {older_tc} cell(s) changed here already "
                              f"carry a pending tracked property change; reject-all would restore that older snapshot.")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        for part, items in frag.transform_data["targets"].items():
            root = pkg.xml(part)
            tables = list(root.iter(W + "tbl"))
            for k, tgets, rgets, cgets in items:
                tbl = tables[k]
                tblpr = tbl.find(W + "tblPr")
                for name in TBL_ELEMENTS:
                    if name in tgets:
                        write_element(tblpr, name, el_from(tgets[name]), TBLPR_ORDER)
                rows = own_rows(tbl)
                for r, g in rgets:
                    trpr = row_properties(rows[r])
                    for name in ROW_ELEMENTS:
                        if name in g:
                            write_element(trpr, name, el_from(g[name]), TRPR_ORDER)
                    _drop_if_empty(trpr)
                for r, c, gone in cgets:
                    clear_cell(own_cells(rows[r])[c], gone)
            pkg.write_xml(part, root)
