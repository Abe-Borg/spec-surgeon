"""The column selector and the grid — transform side (built-in batch 6, F6 — docs/BUILTIN_OPS_BATCH6_DESIGN.md §7). The
verifier's own reading is verify/tablecolumns.py (rule 1: nothing shared); delete_table_columns, set_table_cells and
merge_table_cells number grid columns with these definitions, over F5's rows and spans (ops/table_rows.py).

**The grid.** A table's grid columns are the ``w:gridCol`` children of its one ``w:tblGrid``, numbered from 0; a column's
**width** is its ``w:w`` (a whole number of twips ≥ 0; absent or unreadable: unknown). A row's **layout**
(``row_layout``): ``w:trPr/w:gridBefore`` grid columns before its first cell (absent → 0), then its own cells, each
starting at the next grid column and covering ``table_rows.cell_span`` of them, then ``w:gridAfter`` grid columns
(absent → 0); grid columns past those hold nothing in that row. A cell **covers** the grid columns from its start through
its span. A table cannot have its columns numbered (``grid_problem``; a column op refuses it, ``table-grid``) when it has
no ``w:tblGrid`` or more than one, no grid column, a ``w:gridBefore``/``w:gridAfter`` that is not a whole number ≥ 0, or
a row whose before, cells and after run past its last grid column.

``select.columns`` is ``"all"`` or an object whose given keys must ALL hold, for each grid column:

* ``index: [g, …]`` — the column's number is listed (whole numbers ≥ 0, distinct, at least one);
* ``header_regex`` — ``re.search`` on the column's **header**: the text of the cell of the table's first own row that
  covers it (its paragraphs' block texts joined by a space — a cell's text in ``table_rows.row_text``); a column no cell
  of the first row covers has no header and is never chosen by it. A header cell spanning several grid columns brings
  them all.

Unknown keys or a wrong shape → ``bad-params``.

**Vertical merges by column** (``vertical_merges``): F5's ``table_rows.merge_groups`` with the grid column each runs down
— (column, own rows top first). **An empty cell** (``cell_is_empty``): every child but its ``w:tcPr`` is a paragraph
holding nothing but its properties and runs of nothing but run properties (or an empty ``w:t``), with no section break
and no tracked change anywhere in it (``w:ins``, ``w:del``, ``w:moveFrom``, ``w:moveTo``, ``w:rPrChange``,
``w:pPrChange``). A merge drops an empty cell's paragraphs.

**What the ops refuse alike**: rows and cells that cannot be numbered and, when columns matter, a grid that cannot
(``check_numbered``: ``table-structure`` / ``table-grid``); for a deletion or a merge, pending tracked changes of the
table's own structure or properties (``pending_table_changes``) and Word's legacy horizontal merge (``legacy_merges``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from ..ooxml import block_full_text
from ..package import W
from .base import JobException, Touch
from .patterns import compile_pattern
from .table_rows import cell_paragraphs, cell_span, vertical_merge
from .table_select import cells_wrapped, own_cells, own_rows, rows_wrapped

HEADER_TEXT = "a column header's text"   # what columns.header_regex is searched on

COLUMN_KEYS = ("index", "header_regex")
COLUMNS_SCHEMA = {"description": "'all', or {index: [grid column numbers, 0-based], header_regex (the text of the "
                                 "first row's cell over the column; a cell spanning columns brings them all)} — every "
                                 "given key must hold"}
CHANGE_MARKS = tuple(W + t for t in ("ins", "del", "moveFrom", "moveTo", "rPrChange", "pPrChange", "sectPr"))


def check_column_selector(pred: Any, where: str = "select.columns") -> None:
    """``bad-params`` for a column selector the grammar does not allow (``None`` is allowed: the op decides whether
    columns must be named)."""
    if pred in (None, "all"):
        return
    if not isinstance(pred, dict):
        raise JobException(f"{where} must be 'all' or an object with {list(COLUMN_KEYS)}, got {pred!r}", "bad-params")
    unknown = set(pred) - set(COLUMN_KEYS)
    if unknown:
        raise JobException(f"unknown column selector key(s) {sorted(unknown)}; known: {list(COLUMN_KEYS)}",
                           "bad-params")
    if "index" in pred:
        ix = pred["index"]
        if not (isinstance(ix, list) and ix and all(isinstance(g, int) and not isinstance(g, bool) and g >= 0 for g in ix)
                and len(set(ix)) == len(ix)):
            raise JobException(f"columns.index must be a list of distinct grid column numbers (whole numbers ≥ 0, "
                               f"0-based), got {ix!r}", "bad-params")
    if "header_regex" in pred:
        if not isinstance(pred["header_regex"], str):
            raise JobException("columns.header_regex must be a regular expression (text)", "bad-params")
        try:
            compile_pattern(pred["header_regex"], where=HEADER_TEXT)
        except JobException as e:
            raise JobException(f"columns.header_regex: {e.reason}", e.code, e.details) from None


def selects_every_column(pred: Any) -> bool:
    return pred in (None, "all") or pred == {}


# ---- the grid --------------------------------------------------------------------------------------------------------

def grid_columns_of(tbl) -> Optional[List[Any]]:
    """The table's w:gridCol elements, or None when it has no w:tblGrid or more than one."""
    grids = tbl.findall(W + "tblGrid")
    if len(grids) != 1:
        return None
    return grids[0].findall(W + "gridCol")


def column_width(gc) -> Optional[int]:
    try:
        w = int(gc.get(W + "w"))
    except (TypeError, ValueError):
        return None
    return w if w >= 0 else None


def _count(tr, name: str) -> Optional[int]:
    """A row's w:gridBefore / w:gridAfter count: absent → 0; not a whole number ≥ 0 → None."""
    el = tr.find(f"{W}trPr/{W}{name}")
    if el is None:
        return 0
    try:
        n = int(el.get(W + "val"))
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


@dataclass
class Layout:
    before: int
    cells: List[Tuple[int, int, Any]] = field(default_factory=list)   # (first grid column, span, w:tc)
    after: int = 0

    @property
    def end(self) -> int:
        """The grid column after the row's last cell."""
        return self.cells[-1][0] + self.cells[-1][1] if self.cells else self.before


def row_layout(tr) -> Layout:
    out = Layout(before=_count(tr, "gridBefore") or 0, after=_count(tr, "gridAfter") or 0)
    at = out.before
    for tc in own_cells(tr):
        span = cell_span(tc)
        out.cells.append((at, span, tc))
        at += span
    return out


def grid_problem(tbl) -> Optional[str]:
    """Why the table's columns cannot be numbered (module docstring), or None."""
    cols = grid_columns_of(tbl)
    if cols is None:
        return "it has no column grid (w:tblGrid), or more than one"
    if not cols:
        return "its column grid has no columns"
    for r, tr in enumerate(own_rows(tbl)):
        if _count(tr, "gridBefore") is None or _count(tr, "gridAfter") is None:
            return f"row {r} gives a number of grid columns before or after its cells that is not a whole number"
        lay = row_layout(tr)
        if lay.end + lay.after > len(cols):
            return f"row {r} runs past the {len(cols)} column(s) of its grid"
    return None


def covers(start: int, span: int) -> range:
    return range(start, start + span)


def header_texts(tbl, n: int) -> List[Optional[str]]:
    """Each grid column's header: the text of the first own row's cell covering it (None: no cell there)."""
    out: List[Optional[str]] = [None] * n
    rows = own_rows(tbl)
    if not rows:
        return out
    for start, span, tc in row_layout(rows[0]).cells:
        text = " ".join(block_full_text(p) for p in cell_paragraphs(tc))
        for g in covers(start, span):
            if g < n:
                out[g] = text
    return out


def selected_columns(tbl, pred: Any) -> List[int]:
    """The grid columns the selector chooses, in order (the grid must be readable: ``grid_problem`` is None)."""
    n = len(grid_columns_of(tbl))
    if selects_every_column(pred):
        return list(range(n))
    heads = header_texts(tbl, n) if "header_regex" in pred else [None] * n
    out = []
    for g in range(n):
        if "index" in pred and g not in pred["index"]:
            continue
        if "header_regex" in pred and (heads[g] is None
                                       or not compile_pattern(pred["header_regex"], where=HEADER_TEXT).search(heads[g])):
            continue
        out.append(g)
    return out


def column_selection_reads(part: str, tbl, pred: Any, index: Dict[Any, int]) -> Set[Touch]:
    """What the column selection read (rule 9): ``header_regex`` the text of the first own row's paragraphs
    (``(part, "<bi>", "verify:text")``, ``index``: w:p → its ordinal)."""
    if not isinstance(pred, dict) or "header_regex" not in pred:
        return set()
    rows = own_rows(tbl)
    if not rows:
        return set()
    return {(part, str(index[p]), "verify:text") for tc in own_cells(rows[0]) for p in cell_paragraphs(tc)}


def vertical_merges(tbl) -> List[Tuple[int, List[int]]]:
    """The table's vertical merges: (grid column, the own rows it covers, top first) — ``table_rows.merge_groups``'s
    reading, with the column."""
    groups: List[Tuple[int, List[int]]] = []
    open_at: Dict[int, List[int]] = {}
    for r, tr in enumerate(own_rows(tbl)):
        here = {start: vertical_merge(tc) for start, _span, tc in row_layout(tr).cells}
        for c in list(open_at):
            if here.get(c) != "continue":
                del open_at[c]
        for c, vm in sorted((c, vm) for c, vm in here.items() if vm is not None):
            if vm == "continue" and c in open_at:
                open_at[c].append(r)
            else:
                open_at[c] = [r]
                groups.append((c, open_at[c]))
    return groups


def _bare_run(r) -> bool:
    return all(not isinstance(x.tag, str) or x.tag == W + "rPr" or (x.tag == W + "t" and not x.text) for x in r)


def cell_is_empty(tc) -> bool:
    for c in tc:
        if not isinstance(c.tag, str) or c.tag == W + "tcPr":
            continue
        if c.tag != W + "p" or next(c.iter(*CHANGE_MARKS), None) is not None:
            return False
        if any(isinstance(k.tag, str) and k.tag != W + "pPr" and not (k.tag == W + "r" and _bare_run(k)) for k in c):
            return False
    return True


def check_numbered(tbl, where: str, grid: bool = True) -> None:
    """``table-structure`` for a table whose rows and cells cannot be numbered — no own row, rows or cells in a content
    control or custom XML, a row without a cell —; with ``grid``, ``table-grid`` for one whose columns cannot
    (``grid_problem``)."""
    rows = own_rows(tbl)
    if not rows:
        raise JobException(f"{where} has no rows", "table-structure", {"table": where})
    if rows_wrapped(tbl) or any(cells_wrapped(tr) for tr in rows):
        raise JobException(f"{where} has rows or cells inside a content control or custom XML; they cannot be "
                           f"numbered safely", "table-structure", {"table": where})
    empty = [r for r, tr in enumerate(rows) if not own_cells(tr)]
    if empty:
        raise JobException(f"{where}: row {empty[0]} has no cell", "table-structure", {"table": where})
    if grid:
        why = grid_problem(tbl)
        if why:
            raise JobException(f"{where}: its columns cannot be numbered — {why}; fix the table in Word", "table-grid",
                               {"table": where})


# ---- what a grid change may not meet --------------------------------------------------------------------------------

TABLE_CHANGES = ("tblPrChange", "tblGridChange", "trPrChange", "tblPrExChange", "tcPrChange", "cellIns", "cellDel",
                 "cellMerge")


def pending_table_changes(tbl) -> List[str]:
    """The pending tracked changes of the table's own structure and properties (its w:tblPr, w:tblGrid, own rows'
    w:trPr and w:tblPrEx, own cells' w:tcPr) — a column deletion or a merge reshapes what they would restore."""
    holders = [tbl.find(W + "tblPr"), tbl.find(W + "tblGrid")]
    for tr in own_rows(tbl):
        holders += [tr.find(W + "trPr"), tr.find(W + "tblPrEx")]
        holders += [tc.find(W + "tcPr") for tc in own_cells(tr)]
    found = {c.tag[len(W):] for h in holders if h is not None for c in h
             if isinstance(c.tag, str) and c.tag.startswith(W) and c.tag[len(W):] in TABLE_CHANGES}
    return sorted(found)


def legacy_merges(cells: Sequence[Any]) -> bool:
    """A cell among these is part of Word's legacy horizontal merge (w:hMerge)."""
    return any(tc.find(f"{W}tcPr/{W}hMerge") is not None for tc in cells)
