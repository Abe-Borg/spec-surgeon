"""V10 kinds ``deleted_table_columns``, ``table_cells`` and ``merged_table_cells`` — the table grid and cell ops of
built-in batch 6 (F6, docs/BUILTIN_OPS_BATCH6_DESIGN.md §7), verified.

This module's own reading of the ops' definitions (transform side: ops/table_columns.py, ops/delete_table_columns.py,
ops/set_table_cells.py, ops/merge_table_cells.py). Nothing here comes from ops/ (rule 1). Each kind re-derives from the
SOURCE, with its own code, the tables (``tableselect.VTables``), the rows (``tablerows``), the columns and the grid
(``tablecolumns``) and every refusal, then:

* ``deleted_table_columns`` BUILDS the expected part — the chosen cells deleted by F5's untracked rules
  (``tablestructure._v_take``: what a deletion may hold, bookmark ends kept in the cell's place), each cell over a
  chosen and a kept column narrowed (``w:gridSpan`` less the chosen columns, gone at 1; a ``dxa`` ``w:tcW`` less their
  widths), each row's ``w:gridBefore``/``w:gridAfter`` less the chosen columns it counts (gone with its
  ``w:wBefore``/``w:wAfter`` at 0, an emptied ``w:trPr`` with them; else a ``dxa`` width less theirs), the chosen
  ``w:gridCol`` gone and a fixed (``dxa`` above 0) ``w:tblW`` less their widths — and compares it whole
  (``ctx.verified_parts``);
* ``merged_table_cells`` BUILDS the expected part — the anchor (the block's first covered cell) given every covered
  cell's content but its ``w:tcPr`` in reading order (empty cells' dropped, an empty anchor's too, all kept when all
  are empty), each row's first covered cell kept with ``w:gridSpan`` the block's width, ``w:tcW`` the covered cells'
  same-typed ``dxa``/``pct`` widths summed, ``w:vMerge`` restart / continue over several rows (none over one), each
  later row's cell holding one new paragraph with its first paragraph's cleaned properties (``tablestructure._v_cell_ppr``)
  — and compares it whole;
* ``table_cells`` checks each changed cell's ``w:tcPr``, row's ``w:trPr``, table's ``w:tblPr`` and the grid against the
  source's with exactly the expected elements written in the bundled schema's order (``tableprops.v_order``), and — a
  post-condition on the OUTPUT — that every chosen cell's own elements are the expected ones; only what it checked
  leaves the residual (``strip_tc``, ``strip_tr``, ``strip_tbl``, ``strip_grid``).

Fails when a refusal applies (the op must refuse), when the tables, columns, cells or blocks that change or their counts
differ from the plan's, or when an output part, element or grid is not exactly the expected one.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from lxml import etree

from .fields import v_fields
from .props import Ctx, sig
from .reader import W
from .tablecolumns import (v_check_columns, v_columns_chosen, v_empty, v_every_column, v_grid, v_hmerged, v_layout,
                           v_end, v_pending, v_unnumbered, v_vmerge, v_vmerges, v_width)
from .tableprops import _show, _v_with, v_order, v_put
from .tablerows import v_cell_paragraphs, v_check_rows, v_rows_chosen
from .tablestructure import _v_cell_ppr, _v_compare, _v_old_ids, _v_take
from .tableselect import VTables, v_cells, v_check, v_in_text_box, v_rows, v_style_chain

_V_HEX = re.compile(r"[0-9A-Fa-f]{6}")
_V_OFF = {"0", "false", "off"}
_V_RANGES = {W + t: t.replace("Start", "").replace("End", "")
             for t in ("bookmarkStart", "bookmarkEnd", "permStart", "permEnd", "moveFromRangeStart", "moveFromRangeEnd",
                       "moveToRangeStart", "moveToRangeEnd", "customXmlInsRangeStart", "customXmlInsRangeEnd",
                       "customXmlDelRangeStart", "customXmlDelRangeEnd", "customXmlMoveFromRangeStart",
                       "customXmlMoveFromRangeEnd", "customXmlMoveToRangeStart", "customXmlMoveToRangeEnd")}
_V_RANGES.update({W + t: "comment" for t in ("commentRangeStart", "commentRangeEnd", "commentReference")})


# ---- shared readings --------------------------------------------------------------------------------------------------

def _v_select(sel: Any, keys: Sequence[str]) -> Tuple[List[str], Any, Any, Any]:
    """(scope kinds, tables predicate, rows predicate, columns predicate) — ValueError for one the op must refuse."""
    if not isinstance(sel, dict) or set(sel) - {"scope", *keys}:
        raise ValueError(f"unknown selector key(s) in {sel!r} (the op must refuse)")
    scope = sel.get("scope", ["document"])
    pred = sel.get("tables", "all")
    v_check(pred)
    rows, cols = sel.get("rows"), sel.get("columns")
    v_check_rows(rows)
    v_check_columns(cols)
    return ([scope] if isinstance(scope, str) else list(scope)), pred, rows, cols


def _v_every(pred: Any) -> bool:
    return pred in (None, "all") or pred == {}


def _v_untracked(a: Dict[str, Any]) -> None:
    params = a.get("params")
    if params is not None and params.get("tracked", False) is not False:
        raise ValueError("this change is never tracked (the op must refuse: bad-params)")


def _v_table_ok(tbl, pred: Any, where: str, grid: bool = True) -> bool:
    """False for a table in a text box under an every-table selection (left alone); ValueError where the op must
    refuse it — a text box under a selection naming tables, rows, cells or a grid it cannot number."""
    if v_in_text_box(tbl):
        if _v_every(pred):
            return False
        raise ValueError(f"{where} lies in a text box (the op must refuse: textbox)")
    why = v_unnumbered(tbl, grid)
    if why:
        raise ValueError(f"{where}: {why} (the op must refuse)")
    return True


def _v_whole_int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _v_check_counts(kind: str, mine: Dict[str, Any], a: Dict[str, Any], counts: Dict[str, int],
                    errs: List[str]) -> None:
    for key, n in counts.items():
        if n != a.get(key, -1):
            errs.append(f"{kind}: {n} here ({key}), the plan predicted {a.get(key)}")
    if mine != (a.get("changed") or {}):
        errs.append(f"{kind}: the changes here {mine} are not the plan's {a.get('changed')}")


# ---- deleted_table_columns --------------------------------------------------------------------------------------------

def _v_deletion(tbl, cols: List[int], where: str) -> Dict[str, Any]:
    """What deleting the grid columns ``cols`` does to the table: the cells that go, the changes to the others — or
    ValueError where the op must refuse."""
    grid = v_grid(tbl)
    out: Dict[str, Any] = {"gone": [], "narrow": [], "edges": [], "grid": [grid[g] for g in cols], "width": None}

    def minus(el, columns, what: str) -> Optional[int]:
        if el is None or el.get(W + "type") != "dxa" or _v_whole_int(el.get(W + "w")) is None:
            return None
        lost = 0
        for g in columns:
            if g in cols:
                if v_width(grid[g]) is None:
                    raise ValueError(f"{where}: column {g} has no width to take off {what} (the op must refuse: "
                                     f"table-grid)")
                lost += v_width(grid[g])
        if int(el.get(W + "w")) - lost <= 0:
            raise ValueError(f"{where}: {what} would not stay above 0 (the op must refuse: table-grid)")
        return int(el.get(W + "w")) - lost

    for r, tr in enumerate(v_rows(tbl)):
        lay = v_layout(tr)
        before, cells, after = lay
        stays = 0
        for start, span, tc in cells:
            cover = list(range(start, start + span))
            taken = [g for g in cover if g in cols]
            if len(taken) == span:
                out["gone"].append(tc)
                continue
            stays += 1
            if taken:
                pr = tc.find(W + "tcPr")
                out["narrow"].append((tc, span - len(taken), minus(pr.find(W + "tcW"), cover, f"a cell of row {r}")))
        if not stays:
            raise ValueError(f"{where}: row {r} would keep no cell (the op must refuse: table-grid)")
        for side, n, first in (("Before", before, 0), ("After", after, v_end(lay))):
            span_ = list(range(first, first + n))
            taken = [g for g in span_ if g in cols]
            if taken:
                pr = tr.find(W + "trPr")
                width = None if len(taken) == n else minus(pr.find(W + "w" + side), span_, f"row {r}'s {side} space")
                out["edges"].append((tr, side, n - len(taken), width))
    gone = set(out["gone"])
    rows = v_rows(tbl)
    for c, rs in v_vmerges(tbl):
        fates = {[tc for start, _s, tc in v_layout(rows[r])[1] if start == c][0] in gone for r in rs}
        if len(fates) != 1:
            raise ValueError(f"{where}: a vertical merge would be split (the op must refuse: vertical-merge)")
    pr = tbl.find(W + "tblPr")
    tw = pr.find(W + "tblW") if pr is not None else None
    if tw is not None and tw.get(W + "type") == "dxa" and (_v_whole_int(tw.get(W + "w")) or 0) > 0:
        out["width"] = minus(tw, list(range(len(grid))), "the table's width")
    return out


def _v_narrow(tbl, change: Dict[str, Any]) -> None:
    for tc, span, width in change["narrow"]:
        pr = tc.find(W + "tcPr")
        gs = pr.find(W + "gridSpan")
        if span > 1:
            gs.set(W + "val", str(span))
        else:
            pr.remove(gs)
        if width is not None:
            pr.find(W + "tcW").set(W + "w", str(width))
    for tr, side, n, width in change["edges"]:
        pr = tr.find(W + "trPr")
        if n:
            pr.find(W + "grid" + side).set(W + "val", str(n))
            if width is not None:
                pr.find(W + "w" + side).set(W + "w", str(width))
            continue
        for name in ("grid" + side, "w" + side):
            for el in pr.findall(W + name):
                pr.remove(el)
        if not len(pr) and not pr.attrib:
            tr.remove(pr)
    for gc in change["grid"]:
        gc.getparent().remove(gc)
    if change["width"] is not None:
        tbl.find(W + "tblPr").find(W + "tblW").set(W + "w", str(change["width"]))


def _deleted_table_columns(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    scope, pred, _rows, cols_pred = _v_select(a.get("select") or {}, ("tables", "columns"))
    if cols_pred is None:
        raise ValueError("no column selection (the op must refuse: bad-params)")
    _v_untracked(a)
    parts = ctx.scope_parts(scope)
    if sorted(parts) != sorted(a.get("parts") or []):
        errs.append(f"deleted_table_columns: the scope is {sorted(parts)} here, the plan says "
                    f"{sorted(a.get('parts') or [])}")
    old, taken = _v_old_ids(ctx), set()
    mine: Dict[str, List[list]] = {}
    for part in parts:
        if part not in ctx.both:
            errs.append(f"deleted_table_columns: {part} is missing from the output")
            continue
        root = ctx.src.xml(part)
        vt = VTables(root)
        going: Set[Any] = set()
        items, changes = [], []
        for k, tbl in vt.selected(pred, ctx.src_styles):
            if any(x in going for x in tbl.iterancestors(W + "tc")):
                continue                                      # inside a cell that goes
            where = f"{part} table {k}"
            if not _v_table_ok(tbl, pred, where):
                continue
            cols = v_columns_chosen(tbl, cols_pred, vt.text)
            if not cols:
                continue
            if len(cols) == len(v_grid(tbl)):
                raise ValueError(f"{where}: every column chosen (the op must refuse: every-column)")
            if v_hmerged([tc for tr in v_rows(tbl) for tc in v_cells(tr)]):
                raise ValueError(f"{where}: a legacy horizontal merge (the op must refuse: table-grid)")
            if v_pending(tbl):
                raise ValueError(f"{where}: pending structure changes {v_pending(tbl)} (the op must refuse: "
                                 f"existing-revisions)")
            change = _v_deletion(tbl, cols, where)
            going |= set(change["gone"])
            items.append([k, cols])
            changes.append((tbl, change))
        if not items:
            continue
        _v_take(root, [tc for _t, ch in changes for tc in ch["gone"]], [], False, "", f"{part} table(s) "
                f"{[k for k, _ in items]}")
        for tbl, change in changes:
            _v_narrow(tbl, change)
        _v_compare(ctx, part, root, old, taken, f"the columns {items} deleted", errs)
        mine[part] = items
    n_cols = sum(len(c) for v in mine.values() for _, c in v)
    _v_check_counts("deleted_table_columns", mine, a, {"expected_tables": sum(len(v) for v in mine.values()),
                                                        "expected_columns": n_cols}, errs)
    return f"deleted_table_columns: {n_cols} column(s), each changed part compared whole"


# ---- table_cells --------------------------------------------------------------------------------------------------------

def _v_cells_asked(params: Dict[str, Any]) -> Dict[str, Any]:
    if set(params) - {"shading", "vertical_alignment", "no_wrap", "width", "tracked"}:
        raise ValueError(f"unknown parameter(s) in {params!r} (the op must refuse)")
    if params.get("tracked", False) is not False:
        raise ValueError("cell formatting is never tracked (the op must refuse)")
    out: Dict[str, Any] = {"shd": None, "vAlign": None, "noWrap": None, "width": None}
    s = params.get("shading")
    if s is not None:
        if s != "none" and not (isinstance(s, str) and _V_HEX.fullmatch(s)):
            raise ValueError(f"bad shading {s!r} (the op must refuse)")
        out["shd"] = s if s == "none" else s.upper()
    v = params.get("vertical_alignment")
    if v is not None:
        if v not in ("top", "center", "bottom"):
            raise ValueError(f"bad vertical_alignment {v!r} (the op must refuse)")
        out["vAlign"] = v
    n = params.get("no_wrap")
    if n is not None:
        if not isinstance(n, bool):
            raise ValueError("bad no_wrap (the op must refuse)")
        out["noWrap"] = n
    w = params.get("width")
    if w is not None:
        if type(w) not in (int, float) or not 0 < w * 1440 <= 31680:
            raise ValueError(f"bad width {w!r} (the op must refuse)")
        out["width"] = max(1, round(w * 1440))
    if all(v is None for v in out.values()):
        raise ValueError("nothing asked (the op must refuse)")
    return out


def _v_shows(shd) -> bool:
    if shd is None or shd.get(W + "val") == "nil":
        return False
    return not (shd.get(W + "val") == "clear" and shd.get(W + "fill", "auto") == "auto"
                and shd.get(W + "themeFill") is None)


def _v_cell_want(asked: Dict[str, Any], chain: List[Any], width: Optional[int]) -> Dict[str, Any]:
    """The expected cell elements (None for "no element") of every asked name."""
    want: Dict[str, Any] = {}
    if asked["width"] is not None and width is not None:
        want["tcW"] = etree.Element(W + "tcW", {W + "w": str(width), W + "type": "dxa"})
    if asked["shd"] is not None:
        if asked["shd"] != "none":
            want["shd"] = etree.Element(W + "shd", {W + "val": "clear", W + "color": "auto", W + "fill": asked["shd"]})
        else:
            near = next((e for st in chain for e in [st.find(f"{W}tcPr/{W}shd")] if e is not None), None)
            want["shd"] = etree.Element(W + "shd", {W + "val": "clear", W + "color": "auto", W + "fill": "auto"}) \
                if _v_shows(near) else None
    if asked["noWrap"] is not None:
        lit = any(e.get(W + "val") not in _V_OFF for st in chain for e in st.findall(f"{W}tcPr/{W}noWrap"))
        want["noWrap"] = etree.Element(W + "noWrap") if asked["noWrap"] else (
            etree.Element(W + "noWrap", {W + "val": "0"}) if lit else None)
    if asked["vAlign"] is not None:
        want["vAlign"] = etree.Element(W + "vAlign", {W + "val": asked["vAlign"]})
    return want


def _v_same(parent, name: str, want) -> bool:
    own = [sig(e) for e in parent.findall(W + name)] if parent is not None else []
    return own == ([sig(want)] if want is not None else [])


def _v_widths(tbl, cols: List[int], twips: int, where: str) -> Dict[str, Any]:
    """What a column width does: grid columns that change, every cell's and row edge's width, the table's."""
    grid = v_grid(tbl)

    def total(columns, what: str) -> int:
        s = 0
        for g in columns:
            w = twips if g in cols else v_width(grid[g])
            if w is None:
                raise ValueError(f"{where}: column {g} has no width for {what} (the op must refuse: table-grid)")
            s += w
        return s

    out: Dict[str, Any] = {"grid": [g for g in cols if v_width(grid[g]) != twips], "cells": {}, "edges": [],
                           "table": None}
    for r, tr in enumerate(v_rows(tbl)):
        lay = v_layout(tr)
        for start, span, tc in lay[1]:
            if set(range(start, start + span)) & set(cols):
                out["cells"][tc] = total(range(start, start + span), f"a cell of row {r}")
        pr = tr.find(W + "trPr")
        for side, n, first in (("Before", lay[0], 0), ("After", lay[2], v_end(lay))):
            if set(range(first, first + n)) & set(cols) and pr is not None and pr.find(W + "w" + side) is not None:
                out["edges"].append((r, "w" + side, total(range(first, first + n), f"row {r}'s {side} space")))
    pr = tbl.find(W + "tblPr")
    tw = pr.find(W + "tblW") if pr is not None else None
    if tw is not None and tw.get(W + "type") == "dxa" and (_v_whole_int(tw.get(W + "w")) or 0) > 0:
        if any(v_width(grid[g]) is None for g in cols):
            raise ValueError(f"{where}: a chosen column has no width (the op must refuse: table-grid)")
        new = int(tw.get(W + "w")) + sum(twips - v_width(grid[g]) for g in cols)
        if new <= 0:
            raise ValueError(f"{where}: the table's width would not stay above 0 (the op must refuse: table-grid)")
        if new != int(tw.get(W + "w")):
            out["table"] = new
    return out


def _v_cells_of(tbl, rows: List[int], cols: Optional[List[int]]) -> List[Tuple[int, int, Any]]:
    out = []
    trs = v_rows(tbl)
    for r in rows:
        for c, (start, span, tc) in enumerate(v_layout(trs[r])[1]):
            if cols is None or set(range(start, start + span)) & set(cols):
                out.append((r, c, tc))
    return out


def _table_cells(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    asked = _v_cells_asked(a.get("params") or {})
    scope, pred, rows_pred, cols_pred = _v_select(a.get("select") or {}, ("tables", "rows", "columns"))
    rows_pred = "all" if rows_pred is None else rows_pred
    if asked["width"] is not None and not (rows_pred == "all" or rows_pred == {}):
        raise ValueError("a width with a row selection (the op must refuse: bad-params)")
    by_grid = asked["width"] is not None or not v_every_column(cols_pred)
    parts = ctx.scope_parts(scope)
    if sorted(parts) != sorted(a.get("parts") or []):
        errs.append(f"table_cells: the scope is {sorted(parts)} here, the plan says {sorted(a.get('parts') or [])}")
    names = [n for n in ("tcW", "shd", "noWrap", "vAlign") if (asked["width"] if n == "tcW" else asked[n]) is not None]
    mine: Dict[str, Dict[str, list]] = {}
    n_tables = n_cells = n_cols = 0
    for part in parts:
        if part not in ctx.both:
            errs.append(f"table_cells: {part} is missing from the output")
            continue
        s_t, o_t = VTables(ctx.src.xml(part)), VTables(ctx.out.xml(part))
        if len(s_t.tables) != len(o_t.tables):
            errs.append(f"table_cells: {part} has {len(s_t.tables)} table(s) in the source, {len(o_t.tables)} in the "
                        f"output")
            continue
        rec: Dict[str, list] = {"cells": [], "rows": [], "tables": [], "grids": []}
        for k, tbl in s_t.selected(pred, ctx.src_styles):
            where = f"{part} table {k}"
            if not _v_table_ok(tbl, pred, where, grid=by_grid):
                continue
            chain = v_style_chain(tbl, ctx.src_styles)
            rs = v_rows_chosen(tbl, rows_pred, chain, s_t.text)
            cols = v_columns_chosen(tbl, cols_pred, s_t.text) if by_grid else None
            cells = _v_cells_of(tbl, rs, cols)
            wd = _v_widths(tbl, cols, asked["width"], where) if asked["width"] is not None and cols else None
            o_tbl = o_t.tables[k]
            s_rows, o_rows = v_rows(tbl), v_rows(o_tbl)
            if len(s_rows) != len(o_rows) or any(len(v_cells(x)) != len(v_cells(y)) for x, y in zip(s_rows, o_rows)):
                errs.append(f"{where}: its rows or cells changed in number")
                continue
            changed = False
            for r, c, tc in cells:
                want = _v_cell_want(asked, chain, wd["cells"].get(tc) if wd else None)
                s_pr = tc.find(W + "tcPr")
                gets = {n: e for n, e in want.items() if not _v_same(s_pr, n, e)}
                o_tc = v_cells(o_rows[r])[c]
                o_pr = o_tc.find(W + "tcPr")
                for n, e in want.items():                            # every chosen cell shows what was asked
                    if not _v_same(o_pr, n, e):
                        errs.append(f"{where} row {r} cell {c}: its own w:{n} in the output is "
                                    f"{[_show(x) for x in (o_pr.findall(W + n) if o_pr is not None else [])]}, "
                                    f"expected {_show(e)}")
                if not gets:
                    continue
                changed = True
                rec["cells"].append([k, r, c, sorted(gets)])
                expected = _v_with(s_pr, gets, "tcPr", "CT_TcPr")
                if sig(o_pr) != sig(expected):
                    errs.append(f"{where} row {r} cell {c}: w:tcPr is {_show(o_pr)}, expected {_show(expected)}")
                elif o_pr is not None and next(x for x in o_tc if isinstance(x.tag, str)) is not o_pr:
                    errs.append(f"{where} row {r} cell {c}: w:tcPr is not the cell's first element")
                ctx.strip_tc.setdefault(part, {}).setdefault((k, r, c), set()).update(gets)
            if wd is not None:
                for r, name, w in wd["edges"]:
                    el = etree.Element(W + name, {W + "w": str(w), W + "type": "dxa"})
                    s_pr = s_rows[r].find(W + "trPr")
                    if _v_same(s_pr, name, el):
                        continue
                    changed = True
                    rec["rows"].append([k, r, [name]])
                    expected = _v_with(s_pr, {name: el}, "trPr", "CT_TrPr")
                    if sig(o_rows[r].find(W + "trPr")) != sig(expected):
                        errs.append(f"{where} row {r}: w:trPr is {_show(o_rows[r].find(W + 'trPr'))}, expected "
                                    f"{_show(expected)}")
                    ctx.strip_tr.setdefault(part, {}).setdefault((k, r), set()).add(name)
                if wd["table"] is not None:
                    changed = True
                    rec["tables"].append([k, ["tblW"]])
                    el = etree.Element(W + "tblW", {W + "w": str(wd["table"]), W + "type": "dxa"})
                    expected = _v_with(tbl.find(W + "tblPr"), {"tblW": el}, "tblPr", "CT_TblPr")
                    if sig(o_tbl.find(W + "tblPr")) != sig(expected):
                        errs.append(f"{where}: w:tblPr is {_show(o_tbl.find(W + 'tblPr'))}, expected {_show(expected)}")
                    ctx.strip_tbl.setdefault(part, {}).setdefault(k, set()).add("tblW")
                if wd["grid"]:
                    changed = True
                    n_cols += len(wd["grid"])
                    rec["grids"].append([k, list(wd["grid"])])
                    expected = copy.deepcopy(tbl.find(W + "tblGrid"))
                    cols_now = [x for x in expected if x.tag == W + "gridCol"]
                    for g in wd["grid"]:
                        cols_now[g].set(W + "w", str(asked["width"]))
                    if sig(o_tbl.find(W + "tblGrid")) != sig(expected):
                        errs.append(f"{where}: w:tblGrid is {_show(o_tbl.find(W + 'tblGrid'))}, expected "
                                    f"{_show(expected)}")
                    ctx.strip_grid.setdefault(part, set()).add(k)
            if changed:
                n_tables += 1
        n_cells += len(rec["cells"])
        if any(rec.values()):
            mine[part] = rec

    def norm(d):
        return {p: {key: sorted(tuple(x[:-1]) + (tuple(x[-1]),) for x in v) for key, v in r.items() if v}
                for p, r in (d or {}).items()}
    for key, n in (("expected_tables", n_tables), ("expected_cells", n_cells), ("expected_columns", n_cols)):
        if n != a.get(key, -1):
            errs.append(f"table_cells: {n} here ({key}), the plan predicted {a.get(key)}")
    if norm(mine) != norm(a.get("changed")):
        errs.append(f"table_cells: the cells, rows, tables and grids that change here {norm(mine)} are not the plan's "
                    f"{norm(a.get('changed'))}")
    return f"table_cells: {n_cells} cell(s), {n_cols} column width(s) in {n_tables} table(s) changed ({names})"


# ---- merged_table_cells -------------------------------------------------------------------------------------------------

def _v_unbroken(xs: List[int]) -> bool:
    return xs == list(range(xs[0], xs[-1] + 1))


def _v_order_broken(root, anchor, movers: List[Any], pos: Dict[Any, int]) -> bool:
    if not movers:
        return False
    moving = {el for tc in movers for el in tc.iter()}
    end = max(pos[el] for el in anchor.iter())
    groups: Dict[Tuple[str, str], List[Any]] = {}
    for el in root.iter(*_V_RANGES):
        groups.setdefault((_V_RANGES[el.tag], el.get(W + "id") or ""), []).append(el)
    sets = list(groups.values()) + [[m for m in (f.begin, f.separate, f.end) if m is not None]
                                    for f in v_fields(root) if f.simple is None]
    for els in sets:
        if any(end < pos[y] < pos[x] for x in els if x in moving for y in els if y not in moving):
            return True
    return False


def _v_sum(cells: List[Any]) -> Optional[Tuple[str, int]]:
    if len(cells) < 2:
        return None
    ws = []
    for tc in cells:
        pr = tc.find(W + "tcPr")
        w = pr.find(W + "tcW") if pr is not None else None
        if w is None or w.get(W + "type") not in ("dxa", "pct") or _v_whole_int(w.get(W + "w")) is None:
            return None
        ws.append((w.get(W + "type"), int(w.get(W + "w"))))
    return (ws[0][0], sum(x for _, x in ws)) if len({t for t, _ in ws}) == 1 else None


def _v_merge(root, tbl, rs: List[int], cols: List[int], where: str, pos: Dict[Any, int]) -> Optional[Dict[str, Any]]:
    """The merge to make, or None when the block is one cell already; ValueError where the op must refuse."""
    if not _v_unbroken(rs) or not _v_unbroken(cols):
        raise ValueError(f"{where}: the block is not unbroken (the op must refuse: table-shape)")
    a, b, c, d = rs[0], rs[-1], cols[0], cols[-1]
    trs = v_rows(tbl)
    lays = [v_layout(trs[r]) for r in range(a, b + 1)]
    covered = [[(s, n, tc) for s, n, tc in lay[1] if s + n - 1 >= c and s <= d] for lay in lays]
    merges = v_vmerges(tbl)
    if all(len(x) == 1 and x[0][0] == c and x[0][1] == d - c + 1 for x in covered) and \
            (a == b or (c, list(range(a, b + 1))) in merges):
        return None
    if v_hmerged([tc for lay in lays for _s, _n, tc in lay[1]]):
        raise ValueError(f"{where}: a legacy horizontal merge in the block's rows (the op must refuse: table-grid)")
    if v_pending(tbl) or any(trs[r].find(W + "trPr") is not None and (
            trs[r].find(W + "trPr").find(W + "ins") is not None or trs[r].find(W + "trPr").find(W + "del") is not None)
            for r in range(a, b + 1)):
        raise ValueError(f"{where}: pending structure or row changes (the op must refuse: existing-revisions)")
    for x in covered:
        if any(s < c or s + n - 1 > d for s, n, _tc in x):
            raise ValueError(f"{where}: a cell crosses the block's edge (the op must refuse: merge-overlap)")
        if sum(n for _s, n, _tc in x) != d - c + 1:
            raise ValueError(f"{where}: part of the block has no cell (the op must refuse: table-shape)")
    for col, rows in merges:
        if c <= col <= d and set(rows) & set(range(a, b + 1)) and not set(rows) <= set(range(a, b + 1)):
            raise ValueError(f"{where}: a vertical merge lies partly inside the block (the op must refuse: "
                             f"merge-overlap)")
    if b > a and b + 1 < len(trs):
        under = [tc for s, _n, tc in v_layout(trs[b + 1])[1] if s == c]
        if under and v_vmerge(under[0]) == "continue":
            raise ValueError(f"{where}: the cell below would continue the merge (the op must refuse: merge-overlap)")
    rows_cells = [[tc for _s, _n, tc in x] for x in covered]
    every = [tc for x in rows_cells for tc in x]
    if any(not v_cell_paragraphs(tc) for tc in every):
        raise ValueError(f"{where}: a cell without a paragraph (the op must refuse: table-structure)")
    empty = {tc for tc in every if v_empty(tc)}
    movers = [] if len(empty) == len(every) else [tc for tc in every[1:] if tc not in empty]
    if _v_order_broken(root, every[0], movers, pos):
        raise ValueError(f"{where}: a range or field would be put out of order (the op must refuse: table-anchor)")
    return {"tbl": tbl, "rows": rows_cells, "every": every, "empty": empty, "movers": movers, "span": d - c + 1,
            "widths": [_v_sum(x) for x in rows_cells],
            "pprs": [_v_cell_ppr(v_cell_paragraphs(x[0])[0]) for x in rows_cells[1:]], "item": [a, b], "cols": [c, d]}


def _v_tcpr(tc):
    pr = tc.find(W + "tcPr")
    if pr is None:
        pr = etree.Element(W + "tcPr")
        tc.insert(0, pr)
    return pr


def _v_do_merge(m: Dict[str, Any]) -> None:
    anchor = m["every"][0]
    if m["movers"]:                                   # some content moves: an empty anchor's own goes first
        if anchor in m["empty"]:
            for x in [x for x in anchor if x.tag != W + "tcPr"]:
                anchor.remove(x)
        for tc in m["movers"]:
            for x in [x for x in tc if x.tag != W + "tcPr"]:
                anchor.append(x)
    order = v_order("CT_TcPr")
    for i, cells in enumerate(m["rows"]):
        first = cells[0]
        for tc in cells[1:]:
            first.getparent().remove(tc)
        pr = _v_tcpr(first)
        v_put(pr, "gridSpan", etree.Element(W + "gridSpan", {W + "val": str(m["span"])}) if m["span"] > 1 else None,
              order)
        if m["widths"][i] is not None:
            pr.find(W + "tcW").set(W + "w", str(m["widths"][i][1]))
        if len(m["rows"]) == 1:
            v_put(pr, "vMerge", None, order)
        else:
            v_put(pr, "vMerge", etree.Element(W + "vMerge", {W + "val": "restart"}) if i == 0
                  else etree.Element(W + "vMerge"), order)
        if i:
            for x in [x for x in first if x.tag != W + "tcPr"]:
                first.remove(x)
            p = etree.SubElement(first, W + "p")
            if m["pprs"][i - 1] is not None:
                p.append(copy.deepcopy(m["pprs"][i - 1]))


def _merged_table_cells(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    scope, pred, rows_pred, cols_pred = _v_select(a.get("select") or {}, ("tables", "rows", "columns"))
    if rows_pred is None or cols_pred is None:
        raise ValueError("no row or column selection (the op must refuse: bad-params)")
    _v_untracked(a)
    parts = ctx.scope_parts(scope)
    if sorted(parts) != sorted(a.get("parts") or []):
        errs.append(f"merged_table_cells: the scope is {sorted(parts)} here, the plan says "
                    f"{sorted(a.get('parts') or [])}")
    old, taken = _v_old_ids(ctx), set()
    mine: Dict[str, List[list]] = {}
    for part in parts:
        if part not in ctx.both:
            errs.append(f"merged_table_cells: {part} is missing from the output")
            continue
        root = ctx.src.xml(part)
        vt = VTables(root)
        pos = {el: i for i, el in enumerate(root.iter())}
        merges, items = [], []
        for k, tbl in vt.selected(pred, ctx.src_styles):
            where = f"{part} table {k}"
            if not _v_table_ok(tbl, pred, where):
                continue
            rs = v_rows_chosen(tbl, rows_pred, v_style_chain(tbl, ctx.src_styles), vt.text)
            cols = v_columns_chosen(tbl, cols_pred, vt.text)
            if not rs or not cols:
                continue
            m = _v_merge(root, tbl, rs, cols, where, pos)
            if m is None:
                continue
            merges.append(m)
            items.append([k, m["item"], m["cols"]])
        if not merges:
            continue
        for m in sorted(merges, key=lambda m: -len(list(m["tbl"].iterancestors(W + "tbl")))):
            _v_do_merge(m)
        _v_compare(ctx, part, root, old, taken, f"the cells {items} merged", errs)
        mine[part] = items
    _v_check_counts("merged_table_cells", mine, a, {"expected_tables": sum(len(v) for v in mine.values())}, errs)
    return f"merged_table_cells: {sum(len(v) for v in mine.values())} block(s) merged, each changed part compared whole"


TABLE_GRID_KINDS = {"deleted_table_columns": _deleted_table_columns, "table_cells": _table_cells,
                    "merged_table_cells": _merged_table_cells}


# the contract a custom method's writer reads (``props.KIND_CONTRACTS``; the verifier owns the text)
from .props import KIND_CONTRACTS, KIND_EXAMPLES  # noqa: E402

KIND_CONTRACTS["table_cells"] = (
    'Shading, width, vertical alignment and no-wrap of table cells (set_table_cells). Keys: "select": {"scope": a '
    'part kind or a list (default ["document"]), "tables": "all" or an object with any of index_range, style_in, '
    'first_row_regex, text_regex, nested (as table_props), "rows": absent or "all", or {index_range: [first, '
    'past-the-last], text_regex, header: boolean}, "columns": absent or "all", or {index: [grid column indices], '
    'header_regex: a regex over the header cell\'s text}}; "parts": what the scope resolves to; "params": the '
    'user\'s own words — "shading": "none" or six hex digits (w:shd clear / auto / that fill); '
    '"vertical_alignment": top | center | bottom (w:vAlign); "no_wrap": boolean (w:noWrap); "width": inches, '
    '0 < width ≤ 22 (w:tcW in twips, round(width × 1440), type dxa; the grid column and a fixed w:tblW follow); at '
    'least one asked; never tracked; "expected_tables", "expected_cells", "expected_columns": how many tables '
    'change, cells change, grid columns are resized; "changed": {part name: {"cells": [[table index, row index, '
    'cell index, [element names written: tcW, shd, noWrap, vAlign]]], "rows": [[table index, row index, [one '
    'element]]], "tables": [[table index, ["tblW"]]], "grids": [[table index, [every gridCol width after]]]}} — the '
    'verifier derives its own and they must agree (a cell already as asked is left alone and not listed). '
    'Compared: each changed cell\'s w:tcPr, row\'s w:trPr, table\'s w:tblPr and the grid are the source\'s with '
    'exactly the expected elements in the schema\'s order, and — on the OUTPUT — every chosen cell\'s own elements '
    'are the expected ones. Refused: a width with a row selection, a bad selector, the table refusals of '
    'table_props. Only what it checked leaves the residual.')
KIND_EXAMPLES["table_cells"] = {
    "kind": "table_cells", "parts": ["word/document.xml"],
    "select": {"scope": "document", "tables": {"first_row_regex": "^Item"}, "rows": {"header": True}},
    "params": {"shading": "D9D9D9", "vertical_alignment": "center"},
    "expected_tables": 1, "expected_cells": 3, "expected_columns": 0,
    "changed": {"word/document.xml": {"cells": [[0, 0, 0, ["shd", "vAlign"]], [0, 0, 1, ["shd", "vAlign"]],
                                                [0, 0, 2, ["shd", "vAlign"]]]}}}
