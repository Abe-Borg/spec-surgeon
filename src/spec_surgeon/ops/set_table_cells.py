"""Op: set_table_cells — cell shading, vertical alignment, text wrapping and column widths (built-in batch 6, F6 —
docs/BUILTIN_OPS_BATCH6_DESIGN.md §7).

Formatting mode: zero text diffs, never tracked (``tracked`` accepts only false; ``w:tcPrChange`` is not written). **The
cells** are those of the chosen tables (the table selector: ``select.scope``, ``select.tables``), in the own rows the
row selector chooses (``select.rows``, F5's, default every row), that cover a grid column the column selector chooses
(``select.columns``, ``ops/table_columns.py``; default every column — then every own cell of the row, whatever its grid).
Parameters (at least one of the first four):

* ``shading`` — 6 hex digits (RRGGBB) or ``"none"``. A colour: the cell's own ``w:shd`` is exactly ``{w:val="clear",
  w:color="auto", w:fill}``, as Word writes a cell's fill. ``"none"``: ``{clear, auto, auto}`` when the nearest level of
  the table style chain (``table_select.style_chain``) whose ``w:tcPr`` holds a ``w:shd`` shows shading
  (``visible_shading``: not ``w:val="nil"``, and not ``clear`` with no fill, an ``auto`` fill and no theme fill), else no
  ``w:shd``;
* ``vertical_alignment`` — ``top`` | ``center`` | ``bottom``: the cell's own ``w:vAlign`` with that value;
* ``no_wrap`` — true: ``<w:noWrap/>``; false: ``<w:noWrap w:val="0"/>`` when any level of the chain's ``w:tcPr`` turns
  it on (present, ``w:val`` not 0/false/off), else no ``w:noWrap``;
* ``width`` — inches (above 0, at most 22) for the chosen COLUMNS, only with every row (a row selection with a width →
  ``bad-params``; a column's width is the whole column's). Each chosen ``w:gridCol`` gets it; every own cell covering a
  chosen column gets ``w:tcW`` ``{w:w, w:type="dxa"}``, its columns' widths summed (the new width for a chosen column,
  the grid's for another); a row whose ``w:gridBefore`` / ``w:gridAfter`` counts a chosen column and that has a
  ``w:wBefore`` / ``w:wAfter`` gets it the same way; a fixed table width (``w:tblW`` of type ``dxa`` above 0) changes by
  the chosen columns' change. A width needed from a column without one, or a table width that would not stay above 0
  → ``table-grid``. A table width in percent stays (flagged: Word may scale the columns to it).

**Already** (``set_table_props.already``): a cell, row, grid column or table whose own element is exactly the expected
one is left alone and not counted. **Writing** (``set_table_props.write_element``): the old elements of the name go, the
expected one is inserted in schema order (``ooxml.TCPR_ORDER``, ``TRPR_ORDER``, ``TBLPR_ORDER``; a cell without
``w:tcPr`` gets one first); a grid column's ``w:w`` is set in place.

Left alone and flagged: a table in a text box under an every-table selection (named → ``textbox``). Refused: rows or
cells that cannot be numbered (``table-structure``) and, when columns or a width are asked, a grid that cannot
(``table-grid``) — ``table_columns.check_numbered``. Flagged, never refused: a table style's conditional formatting
(``w:tblStylePr``) with cell shading or no-wrap of its own when the cells are left without theirs (not modelled), a
table's shading (``w:tblPr/w:shd``, its own or its style's) behind cells left without shading, and a pending tracked
property change on a changed cell, row, grid or table (reject-all would restore that older snapshot). Nothing chosen:
a note, no change.

Verified by V10's ``table_cells`` kind (verify/tablegrid.py), which re-derives all of the above on its own.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from lxml import etree

from ..ooxml import TBLPR_ORDER, TCPR_ORDER, TRPR_ORDER
from ..package import W, DocxPackage
from . import prose, register
from .base import JobException, Op, PlanFragment
from .delete_table_rows import row_words
from .delete_table_columns import column_words
from .delete_tables import SCOPE_SCHEMA, TABLES_SCHEMA
from .selectors import StyleSheet, scope_parts
from .set_table_props import already, el_data, el_from, row_properties, write_element
from .table_columns import (COLUMNS_SCHEMA, check_column_selector, check_numbered, column_selection_reads, column_width,
                            covers, grid_columns_of, row_layout, selected_columns, selects_every_column)
from .table_rows import ROWS_SCHEMA, check_row_selector, row_selection_reads, selected_rows, selects_every_row
from .table_select import (build, check_table_selector, in_text_box, own_rows, selection_reads, selects_all, style_chain,
                           style_chain_reads, table_selected)

ALIGNMENTS = ("top", "center", "bottom")
HEX_COLOR = re.compile(r"[0-9A-Fa-f]{6}")
OFF = ("0", "false", "off")
PARAM_KEYS = {"shading", "vertical_alignment", "no_wrap", "width", "tracked"}
MAX_TWIPS = 31680
CELL_ELEMENTS = ("tcW", "shd", "noWrap", "vAlign")          # schema order
LAYOUT_REASON = "cell shading, alignment and column widths change the table's look and layout by design"


@dataclass
class Asked:
    shading: Optional[str] = None             # RRGGBB (upper case), "none", or None (not asked)
    valign: Optional[str] = None
    no_wrap: Optional[bool] = None
    width: Optional[int] = None               # twips

    def names(self) -> List[str]:
        """The cell elements asked, in schema order."""
        return [n for n, v in (("tcW", self.width), ("shd", self.shading), ("noWrap", self.no_wrap),
                               ("vAlign", self.valign)) if v is not None]

    @property
    def reads_style(self) -> bool:
        """The expected elements depend on the table style chain."""
        return self.shading == "none" or self.no_wrap is False


def clean_params(params: Dict[str, Any]) -> Asked:
    unknown = set(params) - PARAM_KEYS
    if unknown:
        raise JobException(f"set_table_cells: unknown parameter(s) {sorted(unknown)}; known: {sorted(PARAM_KEYS)}",
                           "bad-params")
    if params.get("tracked", False) is not False:
        raise JobException("cell formatting is never tracked: omit 'tracked' or set it false", "bad-params")
    a = Asked()
    s = params.get("shading")
    if s is not None:
        if s == "none":
            a.shading = "none"
        elif isinstance(s, str) and HEX_COLOR.fullmatch(s):
            a.shading = s.upper()
        else:
            raise JobException(f"shading must be 6 hex digits (RRGGBB) or 'none', got {s!r}", "bad-params")
    v = params.get("vertical_alignment")
    if v is not None:
        if v not in ALIGNMENTS:
            raise JobException(f"vertical_alignment must be one of {list(ALIGNMENTS)}, got {v!r}", "bad-params")
        a.valign = v
    n = params.get("no_wrap")
    if n is not None:
        if not isinstance(n, bool):
            raise JobException("no_wrap must be true or false", "bad-params")
        a.no_wrap = n
    w = params.get("width")
    if w is not None:
        if isinstance(w, bool) or not isinstance(w, (int, float)) or not 0 < w * 1440 <= MAX_TWIPS:
            raise JobException(f"width must be a number of inches above 0 and at most 22, got {w!r}", "bad-params")
        a.width = max(1, round(w * 1440))
    if a.shading is None and a.valign is None and a.no_wrap is None and a.width is None:
        raise JobException("nothing to set: give shading, vertical_alignment, no_wrap and/or width", "bad-params")
    return a


# ---- the definitions ------------------------------------------------------------------------------------------------

def visible_shading(shd) -> bool:
    if shd is None or shd.get(W + "val") == "nil":
        return False
    return not (shd.get(W + "val") == "clear" and (shd.get(W + "fill") or "auto") == "auto"
                and shd.get(W + "themeFill") is None)


def expected_shading(asked: str, chain: Sequence[Any]):
    if asked != "none":
        return build("shd", [("val", "clear"), ("color", "auto"), ("fill", asked)])
    near = next((st.find(f"{W}tcPr/{W}shd") for st in chain if st.find(f"{W}tcPr/{W}shd") is not None), None)
    return build("shd", [("val", "clear"), ("color", "auto"), ("fill", "auto")]) if visible_shading(near) else None


def expected_no_wrap(on: bool, chain: Sequence[Any]):
    if on:
        return build("noWrap", [])
    lit = any(e.get(W + "val") not in OFF for st in chain for e in st.findall(f"{W}tcPr/{W}noWrap"))
    return build("noWrap", [("val", "0")]) if lit else None


def dxa(width: int):
    return build("tcW", [("w", str(width)), ("type", "dxa")])


@dataclass
class Widths:
    """What a column width does to a table: the grid columns that change, each own cell's and row edge's new width."""
    grid: List[Tuple[int, int]]                         # (grid column, twips) of the columns that change
    cells: Dict[Any, int]                               # w:tc → its new dxa width (every cell over a chosen column)
    edges: List[Tuple[int, str, int]]                   # (row, wBefore / wAfter, twips)
    table: Optional[int] = None                         # the table's new fixed width
    pct_table: bool = False


def widths(tbl, cols: Sequence[int], twips: int, where: str) -> Widths:
    grid = grid_columns_of(tbl)
    chosen = set(cols)

    def width_of(rng, what: str) -> int:
        total = 0
        for g in rng:
            w = twips if g in chosen else column_width(grid[g])
            if w is None:
                raise JobException(f"{where}: grid column {g} has no width, so the width of {what} cannot be summed — "
                                   f"fix the table in Word", "table-grid", {"table": where})
            total += w
        return total

    out = Widths(grid=[(g, twips) for g in sorted(chosen) if column_width(grid[g]) != twips], cells={}, edges=[])
    for r, tr in enumerate(own_rows(tbl)):
        lay = row_layout(tr)
        for start, span, tc in lay.cells:
            if any(g in chosen for g in covers(start, span)):
                out.cells[tc] = width_of(covers(start, span), f"the cell at row {r}, column {start}")
        for side, n, first in (("Before", lay.before, 0), ("After", lay.after, lay.end)):
            rng = range(first, first + n)
            if any(g in chosen for g in rng) and tr.find(f"{W}trPr/{W}w{side}") is not None:
                out.edges.append((r, "w" + side, width_of(rng, f"the space {side.lower()} row {r}'s cells")))
    tblw = tbl.find(f"{W}tblPr/{W}tblW")
    if tblw is not None and tblw.get(W + "type") == "pct":
        out.pct_table = True
    elif tblw is not None and tblw.get(W + "type") == "dxa":
        try:
            w = int(tblw.get(W + "w"))
        except (TypeError, ValueError):
            w = 0
        if w > 0:
            if any(column_width(grid[g]) is None for g in chosen):
                raise JobException(f"{where}: a chosen grid column has no width, so the table's fixed width cannot "
                                   f"follow it — fix the table in Word", "table-grid", {"table": where})
            change = sum(twips - column_width(grid[g]) for g in chosen)
            if w + change <= 0:
                raise JobException(f"{where}: its fixed width ({w} twips) would not stay above 0", "table-grid",
                                   {"table": where})
            if change:
                out.table = w + change
    return out


def chosen_cells(tbl, rows: Sequence[int], cols: Optional[Sequence[int]]) -> List[Tuple[int, int, Any]]:
    """(row, cell, w:tc) of every own cell of the chosen rows covering a chosen column (``cols`` None: every one)."""
    out = []
    chosen = set(cols) if cols is not None else None
    trs = own_rows(tbl)
    for r in rows:
        for c, (start, span, tc) in enumerate(row_layout(trs[r]).cells):
            if chosen is None or any(g in chosen for g in covers(start, span)):
                out.append((r, c, tc))
    return out


def cell_gets(tc, chain: Sequence[Any], asked: Asked, width: Optional[int]) -> Dict[str, Any]:
    """The cell's own elements that change → the expected element (None for "no element")."""
    want: Dict[str, Any] = {}
    if asked.width is not None and width is not None:
        want["tcW"] = dxa(width)
    if asked.shading is not None:
        want["shd"] = expected_shading(asked.shading, chain)
    if asked.no_wrap is not None:
        want["noWrap"] = expected_no_wrap(asked.no_wrap, chain)
    if asked.valign is not None:
        want["vAlign"] = build("vAlign", [("val", asked.valign)])
    tcpr = tc.find(W + "tcPr")
    return {n: e for n, e in want.items() if not already(tcpr, n, e)}


def cell_properties(tc):
    """The cell's w:tcPr, created as its first child when it has none."""
    tcpr = tc.find(W + "tcPr")
    if tcpr is None:
        tcpr = etree.Element(W + "tcPr")
        tc.insert(0, tcpr)
    return tcpr


def _conditional(chain: Sequence[Any], name: str) -> bool:
    return any(c.find(f"{W}tcPr/{W}{name}") is not None for st in chain for c in st.findall(W + "tblStylePr"))


def _table_shaded(tbl, chain: Sequence[Any]) -> bool:
    levels = [tbl.find(W + "tblPr")] + [st.find(W + "tblPr") for st in chain]
    return any(visible_shading(pr.find(W + "shd")) for pr in levels if pr is not None)


@register
class SetTableCells(Op):
    name = "set_table_cells"
    mode = "formatting"
    description = ("Set the shading, vertical alignment and text wrapping of table cells chosen by table, rows and "
                   "columns, and the width of chosen columns (with every row: the grid column and every cell's width "
                   "in it). Adds no characters, never tracked; a cell already as asked is left alone.")
    params_schema = {"type": "object", "properties": {
        "shading": {"type": "string", "description": "6 hex digits (RRGGBB) for the cell fill, or 'none'"},
        "vertical_alignment": {"type": "string", "enum": list(ALIGNMENTS)},
        "no_wrap": {"type": "boolean", "description": "true: the text does not wrap in the cell; false: it wraps"},
        "width": {"type": "number", "description": "inches, for the chosen columns (every row only)"},
        "tracked": {"type": "boolean", "enum": [False], "description": "never tracked: false only"}}}
    select_schema = {"type": "object", "properties": {"scope": SCOPE_SCHEMA, "tables": TABLES_SCHEMA,
                                                      "rows": ROWS_SCHEMA, "columns": COLUMNS_SCHEMA}}
    verification_profile = {
        "V2": "zero text diffs", "V4": "no revision delta (never tracked)",
        "V10": "table_cells: the tables, rows, columns, cells, the style chain, every expected element and 'already' "
               "re-derived from the source; each changed cell's w:tcPr, row's w:trPr, table's w:tblPr and grid exactly "
               "as expected; every chosen cell shows what was asked in the output; residual identical",
        "V7": "none — shading, alignment and widths change the look and layout"}

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        rows, cols = select.get("rows"), select.get("columns")
        chosen = [row_words(rows)] if rows not in (None, "all", {}) else []
        chosen += [column_words(cols)] if cols not in (None, "all", {}) else []
        place = prose.where({k: v for k, v in select.items() if k not in ("rows", "columns")},
                            default="every table")[3:]
        subject = ("the cells in " + " and ".join(chosen) if chosen else "every cell") + " of " + place
        heads: List[str] = []
        if params.get("shading") == "none":
            heads.append("remove their shading")
        elif params.get("shading") is not None:
            heads.append(f"shade them {prose.colour(params['shading'])}")
        v = params.get("vertical_alignment")
        if v in ALIGNMENTS:
            heads.append(f"align their text to the {'middle' if v == 'center' else v} of the cell")
        if params.get("no_wrap") is True:
            heads.append("keep their text on one line")
        elif params.get("no_wrap") is False:
            heads.append("let their text wrap")
        w = params.get("width")
        if isinstance(w, (int, float)) and not isinstance(w, bool) and w > 0:
            heads.append(f"make {'every column' if cols in (None, 'all', {}) else 'those columns'} "
                         f"{prose.inches(round(w * 1440))} wide")
        return prose.sentence(f"For {subject}: {prose.join(heads) if heads else 'set the cell formatting'}, without "
                              f"tracked changes")

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        raw_select = dict(select or {})
        unknown = set(raw_select) - {"scope", "tables", "rows", "columns"}
        if unknown:
            raise JobException(f"set_table_cells: unknown selector key(s) {sorted(unknown)}", "bad-params")
        asked = clean_params(dict(params or {}))
        pred = raw_select.get("tables", "all")
        check_table_selector(pred)
        rows_pred, cols_pred = raw_select.get("rows", "all"), raw_select.get("columns", "all")
        check_row_selector(rows_pred)
        check_column_selector(cols_pred)
        if asked.width is not None and not selects_every_row(rows_pred):
            raise JobException("a column width is the whole column's: give width with every row (no select.rows), or "
                               "set the cells' other formatting for the chosen rows", "bad-params")
        parts = scope_parts(pkg, raw_select.get("scope", ["document"]))
        sheet = StyleSheet(pkg)
        frag = self.fragment(raw_select, params)
        needs_grid = asked.width is not None or not selects_every_column(cols_pred)
        targets: Dict[str, List[list]] = {}
        changed: Dict[str, Dict[str, list]] = {}
        n_tables = n_cells = n_cols = selected_n = already_n = 0
        boxed: List[str] = []
        pct_tables: List[str] = []
        conditional = shaded_behind = older = 0
        for part in parts:
            root = pkg.xml(part)
            frag.touches += sorted(selection_reads(part, root, pred))
            frag.touches.append((part, "scope", "verify"))     # V10 re-resolves the selection over this part
            index = {p: i for i, p in enumerate(root.iter(W + "p"))}
            items: List[list] = []
            record = {"cells": [], "rows": [], "tables": [], "grids": []}
            for k, tbl in enumerate(root.iter(W + "tbl")):
                if not table_selected(pred, tbl, k, sheet):
                    continue
                where = f"{part} table {k}"
                if in_text_box(tbl):
                    if selects_all(pred):                       # every table: this one is left alone and said so
                        boxed.append(where)
                        continue
                    raise JobException(f"{where} lies in a text box (Word keeps two copies of a text box)", "textbox",
                                       {"table": where})
                check_numbered(tbl, where, grid=needs_grid)
                selected_n += 1
                chain, _missing = style_chain(tbl, sheet)
                # what the prediction and V10's post-condition read (rule 9): the rows and columns chosen by, the table's
                # style and its chain when it decides the expected element, and — soft, changed or not — the asked
                # elements of every chosen cell
                frag.touches += sorted(row_selection_reads(part, k, tbl, rows_pred, index))
                if (isinstance(rows_pred, dict) and "header" in rows_pred) or asked.reads_style:
                    frag.touches += sorted(style_chain_reads(sheet, tbl))
                if asked.reads_style:
                    frag.touches.append((part, f"tbl:{k}", "verify-props:tblPr"))
                frag.touches += sorted(column_selection_reads(part, tbl, cols_pred, index))
                rs = selected_rows(tbl, rows_pred, chain)
                cols = selected_columns(tbl, cols_pred) if needs_grid else None
                cells = chosen_cells(tbl, rs, cols)
                for r, c, _tc in cells:
                    frag.touches += [(part, f"tc:{k}:{r}:{c}", f"verify-props:{n}") for n in asked.names()]
                wd = widths(tbl, cols, asked.width, where) if asked.width is not None and cols else None
                if asked.width is not None:
                    frag.touches.append((part, f"tbl:{k}", "verify-props:tblW"))
                gets = [(r, c, g) for r, c, tc in cells
                        for g in [cell_gets(tc, chain, asked, wd.cells.get(tc) if wd else None)] if g]
                row_gets: List[Tuple[int, Dict[str, Any]]] = []
                tbl_gets: Dict[str, Any] = {}
                grid_gets: List[Tuple[int, int]] = []
                if wd is not None:
                    trs = own_rows(tbl)
                    for r, name, w in wd.edges:
                        el = build(name, [("w", str(w)), ("type", "dxa")])
                        if not already(trs[r].find(W + "trPr"), name, el):
                            row_gets.append((r, {name: el}))
                    if wd.table is not None:
                        tbl_gets["tblW"] = build("tblW", [("w", str(wd.table)), ("type", "dxa")])
                    grid_gets = wd.grid
                    if wd.pct_table:
                        pct_tables.append(where)
                if asked.shading == "none" and _conditional(chain, "shd") or asked.no_wrap is False \
                        and _conditional(chain, "noWrap"):
                    conditional += 1
                if asked.shading == "none" and _table_shaded(tbl, chain):
                    shaded_behind += 1
                if not (gets or row_gets or tbl_gets or grid_gets):
                    already_n += 1
                    continue
                n_tables += 1
                n_cells += len(gets)
                n_cols += len(grid_gets)
                trs = own_rows(tbl)
                for r, c, g in gets:
                    frag.touches.append((part, f"tc:{k}:{r}:{c}", "tcPr"))
                    frag.touches += [(part, f"tc:{k}:{r}:{c}", n) for n in g]
                    older += int(row_layout(trs[r]).cells[c][2].find(f"{W}tcPr/{W}tcPrChange") is not None)
                for r, g in row_gets:
                    frag.touches.append((part, f"tr:{k}:{r}", "trPr"))
                    frag.touches += [(part, f"tr:{k}:{r}", n) for n in g]
                    older += int(trs[r].find(f"{W}trPr/{W}trPrChange") is not None)
                if tbl_gets:
                    frag.touches += [(part, f"tbl:{k}", "tblProps"), (part, f"tbl:{k}", "tblW")]
                    older += int(tbl.find(f"{W}tblPr/{W}tblPrChange") is not None)
                if grid_gets:
                    frag.touches.append((part, f"tbl:{k}", "tblGrid"))
                    older += int(tbl.find(f"{W}tblGrid/{W}tblGridChange") is not None)
                items.append([k, [[r, c, {n: el_data(e) for n, e in g.items()}] for r, c, g in gets],
                              [[r, {n: el_data(e) for n, e in g.items()}] for r, g in row_gets],
                              {n: el_data(e) for n, e in tbl_gets.items()}, [[g, w] for g, w in grid_gets]])
                record["cells"] += [[k, r, c, sorted(g)] for r, c, g in gets]
                record["rows"] += [[k, r, sorted(g)] for r, g in row_gets]
                if tbl_gets:
                    record["tables"].append([k, sorted(tbl_gets)])
                if grid_gets:
                    record["grids"].append([k, [g for g, _ in grid_gets]])
            if items:
                targets[part] = items
                changed[part] = record
                frag.intended_parts.append(part)
        frag.property_assertions.append({
            "kind": "table_cells", "parts": parts, "select": raw_select,
            "params": {k: params[k] for k in sorted(PARAM_KEYS) if k in params},
            "expected_tables": n_tables, "expected_cells": n_cells, "expected_columns": n_cols, "changed": changed})
        frag.transform_data = {"targets": targets}
        if targets:
            frag.geometry_reference, frag.geometry_reason = "none", LAYOUT_REASON
        frag.notes.append(f"set_table_cells: {n_cells} cell(s) of {n_tables} of {selected_n} selected table(s) change"
                          + (f", {n_cols} column width(s)" if asked.width is not None else "") + " — never tracked")
        if already_n:
            frag.notes.append(f"already as asked, left alone: {already_n} table(s)")
        if not selected_n and not boxed:
            frag.notes.append("no table in this file matches the selection: nothing to change")
        if boxed:
            frag.flags.append(f"{len(boxed)} table(s) in a text box left alone (Word keeps two copies of a text box): "
                              f"{boxed}")
        if pct_tables:
            frag.flags.append(f"{len(pct_tables)} table(s) keep their width in percent of the text; Word may scale the "
                              f"new column widths to fit it: {pct_tables}")
        if conditional:
            frag.flags.append(f"{conditional} table(s) use a table style whose conditional formatting (first row, "
                              f"banding …) sets cell shading or no-wrap of its own — not modelled: cells left without "
                              f"their own may still show it.")
        if shaded_behind:
            frag.flags.append(f"{shaded_behind} table(s) are shaded as a whole (their own or their style's table "
                              f"shading), which still shows behind cells without shading of their own.")
        if older:
            frag.flags.append(f"{older} changed cell(s), row(s) or table(s) already carry a pending tracked property "
                              f"change; reject-all would restore that older snapshot.")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        for part, items in frag.transform_data["targets"].items():
            root = pkg.xml(part)
            tables = list(root.iter(W + "tbl"))
            for k, gets, row_gets, tbl_gets, grid_gets in items:
                tbl = tables[k]
                trs = own_rows(tbl)
                for r, c, g in gets:
                    tcpr = cell_properties(row_layout(trs[r]).cells[c][2])
                    for name in CELL_ELEMENTS:
                        if name in g:
                            write_element(tcpr, name, el_from(g[name]), TCPR_ORDER)
                    if not len(tcpr) and not tcpr.attrib:
                        tcpr.getparent().remove(tcpr)
                for r, g in row_gets:
                    trpr = row_properties(trs[r])
                    for name, data in g.items():
                        write_element(trpr, name, el_from(data), TRPR_ORDER)
                if tbl_gets:
                    write_element(tbl.find(W + "tblPr"), "tblW", el_from(tbl_gets["tblW"]), TBLPR_ORDER)
                grid = grid_columns_of(tbl)
                for g, w in grid_gets:
                    grid[g].set(W + "w", str(w))
            pkg.write_xml(part, root)
