"""The column selector and the grid — the verifier's own reading (built-in batch 6, F6 — docs/BUILTIN_OPS_BATCH6_DESIGN.md
§7). The transform side is ops/table_columns.py; nothing here comes from it (rule 1).

* **Grid**: the ``w:gridCol`` children of the table's only ``w:tblGrid``, numbered from 0; a column's width is its
  ``w:w`` when that is a whole number ≥ 0 (else unknown). A row: ``w:gridBefore`` columns (absent → 0), its own cells
  from there, each over its span (``tablerows.v_span``), then ``w:gridAfter`` columns (absent → 0).
* **Not numbered** (``v_unnumbered``): no own row, rows or cells inside a container, a row without a cell
  (``table-structure``); with the grid asked, no ``w:tblGrid`` or more than one, no grid column, a before/after count
  that is not a whole number ≥ 0, or a row whose before, cells and after run past the last grid column (``table-grid``).
* ``index``: the column is listed; ``header_regex``: ``re.search`` over the text of the first own row's cell over the
  column (its paragraphs' block texts — ``textcheck.blocks`` — joined by a space); a column no first-row cell covers is
  never chosen by it. Every given key holds; ``"all"``, absent or ``{}`` is every column.
* **Vertical merges** with their column (``v_vmerges``); an **empty cell** (``v_empty``): nothing but its ``w:tcPr`` and
  paragraphs holding only their properties and runs of run properties or empty text, with no section break and no
  ``w:ins``/``w:del``/``w:moveFrom``/``w:moveTo``/``w:rPrChange``/``w:pPrChange`` inside.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .reader import W
from .patterns import v_compile, v_search

_V_HEADER_TEXT = "a column header's text"
from .tablerows import v_cell_paragraphs, v_span
from .tableselect import v_cells, v_cells_wrapped, v_rows, v_rows_wrapped

_V_KEYS = {"index", "header_regex"}
_V_CHANGES = {W + t for t in ("ins", "del", "moveFrom", "moveTo", "rPrChange", "pPrChange", "sectPr")}
_V_STRUCTURE_CHANGES = {"tblPrChange", "tblGridChange", "trPrChange", "tblPrExChange", "tcPrChange", "cellIns",
                        "cellDel", "cellMerge"}


def v_check_columns(pred: Any) -> None:
    """ValueError for a column selector the op must refuse (bad-params); None and 'all' are fine."""
    if pred in (None, "all"):
        return
    if not isinstance(pred, dict) or set(pred) - _V_KEYS:
        raise ValueError(f"bad column selector {pred!r} (the op must refuse)")
    ix = pred.get("index", [0])
    if not (isinstance(ix, list) and ix and all(type(g) is int and g >= 0 for g in ix) and len(set(ix)) == len(ix)):
        raise ValueError(f"bad columns.index {ix!r} (the op must refuse)")
    if "header_regex" in pred:
        if not isinstance(pred["header_regex"], str):
            raise ValueError("bad columns.header_regex (the op must refuse)")
        v_compile(pred["header_regex"], where=_V_HEADER_TEXT)      # a ValueError where the op must refuse


def v_every_column(pred: Any) -> bool:
    return pred in (None, "all") or pred == {}


def v_grid(tbl) -> Optional[List[Any]]:
    holders = [c for c in tbl if c.tag == W + "tblGrid"]
    return [c for c in holders[0] if c.tag == W + "gridCol"] if len(holders) == 1 else None


def v_width(gc) -> Optional[int]:
    try:
        w = int(gc.get(W + "w"))
    except (TypeError, ValueError):
        return None
    return w if w >= 0 else None


def _v_edge(tr, name: str) -> Optional[int]:
    pr = tr.find(W + "trPr")
    el = pr.find(W + name) if pr is not None else None
    if el is None:
        return 0
    try:
        n = int(el.get(W + "val"))
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def v_layout(tr) -> Tuple[int, List[Tuple[int, int, Any]], int]:
    """(columns before the first cell, [(first grid column, span, w:tc)], columns after the last)."""
    before = _v_edge(tr, "gridBefore") or 0
    cells, at = [], before
    for tc in v_cells(tr):
        cells.append((at, v_span(tc), tc))
        at += v_span(tc)
    return before, cells, _v_edge(tr, "gridAfter") or 0


def v_end(layout) -> int:
    before, cells, _after = layout
    return cells[-1][0] + cells[-1][1] if cells else before


def v_unnumbered(tbl, grid: bool = True) -> Optional[str]:
    """The refusal code (with the reason) for a table whose rows, cells or (``grid``) columns cannot be numbered."""
    rows = v_rows(tbl)
    if not rows or v_rows_wrapped(tbl) or any(v_cells_wrapped(tr) for tr in rows) or any(not v_cells(tr) for tr in rows):
        return "table-structure: no rows, rows or cells in a container, or a row without a cell"
    if not grid:
        return None
    cols = v_grid(tbl)
    if not cols:
        return "table-grid: no single w:tblGrid with columns"
    for tr in rows:
        if _v_edge(tr, "gridBefore") is None or _v_edge(tr, "gridAfter") is None:
            return "table-grid: a gridBefore/gridAfter that is not a whole number"
        lay = v_layout(tr)
        if v_end(lay) + lay[2] > len(cols):
            return "table-grid: a row runs past its grid"
    return None


def v_headers(tbl, n: int, text_of: Dict[Any, str]) -> List[Optional[str]]:
    heads: List[Optional[str]] = [None] * n
    rows = v_rows(tbl)
    if rows:
        for start, span, tc in v_layout(rows[0])[1]:
            text = " ".join(text_of[p] for p in v_cell_paragraphs(tc))
            for g in range(start, min(start + span, n)):
                heads[g] = text
    return heads


def v_columns_chosen(tbl, pred: Any, text_of: Dict[Any, str]) -> List[int]:
    n = len(v_grid(tbl))
    if v_every_column(pred):
        return list(range(n))
    heads = v_headers(tbl, n, text_of)
    return [g for g in range(n) if ("index" not in pred or g in pred["index"])
            and ("header_regex" not in pred or (heads[g] is not None
                                                and v_search(pred["header_regex"], heads[g], where=_V_HEADER_TEXT)))]


def _v_vm(tc) -> Optional[str]:
    pr = tc.find(W + "tcPr")
    vm = pr.find(W + "vMerge") if pr is not None else None
    if vm is None:
        return None
    return "restart" if vm.get(W + "val") == "restart" else "continue"


def v_vmerges(tbl) -> List[Tuple[int, List[int]]]:
    """Every vertical merge: (its grid column, the own rows it covers, top first)."""
    found: List[Tuple[int, List[int]]] = []
    running: Dict[int, List[int]] = {}
    for r, tr in enumerate(v_rows(tbl)):
        here = {start: _v_vm(tc) for start, _span, tc in v_layout(tr)[1]}
        running = {c: g for c, g in running.items() if here.get(c) == "continue"}
        for c in sorted(here):
            if here[c] is None:
                continue
            if here[c] == "continue" and c in running:
                running[c].append(r)
            else:
                running[c] = [r]
                found.append((c, running[c]))
    return found


def v_vmerge(tc) -> Optional[str]:
    return _v_vm(tc)


def v_empty(tc) -> bool:
    for c in tc:
        if not isinstance(c.tag, str) or c.tag == W + "tcPr":
            continue
        if c.tag != W + "p" or any(x.tag in _V_CHANGES for x in c.iter()):
            return False
        for k in c:
            if not isinstance(k.tag, str) or k.tag == W + "pPr":
                continue
            if k.tag != W + "r":
                return False
            for x in k:
                if isinstance(x.tag, str) and x.tag != W + "rPr" and not (x.tag == W + "t" and not x.text):
                    return False
    return True


def v_pending(tbl) -> List[str]:
    """The pending tracked changes of the table's own w:tblPr, w:tblGrid, own rows' w:trPr / w:tblPrEx and own cells'
    w:tcPr."""
    holders = [tbl.find(W + "tblPr"), tbl.find(W + "tblGrid")]
    for tr in v_rows(tbl):
        holders += [tr.find(W + "trPr"), tr.find(W + "tblPrEx")] + [tc.find(W + "tcPr") for tc in v_cells(tr)]
    return sorted({c.tag[len(W):] for h in holders if h is not None for c in h if isinstance(c.tag, str)
                   and c.tag.startswith(W) and c.tag[len(W):] in _V_STRUCTURE_CHANGES})


def v_hmerged(cells) -> bool:
    return any(tc.find(W + "tcPr") is not None and tc.find(W + "tcPr").find(W + "hMerge") is not None for tc in cells)
