"""The row selector and the row geometry — transform side (built-in batch 6, docs/BUILTIN_OPS_BATCH6_DESIGN.md §6). The
verifier's own reading is verify/tablerows.py (rule 1: nothing shared); the table ops of batch 6 (and F6's cells and
merges) number rows and grid columns with these definitions.

``select.rows`` is ``"all"`` or an object whose given keys must ALL hold, for each of a table's **own rows**
(``table_select.own_rows``: the ``w:tr`` children of the ``w:tbl``, numbered from 0 in document order):

* ``index_range: [a, b]`` — the row's index lies in a … b, both inclusive, 0-based (whole numbers, ``0 ≤ a ≤ b``);
* ``text_regex`` — ``re.search`` on the **row's text**: its cells (``w:tc`` whose nearest ``w:tr`` is the row) in
  document order joined by a tab, each cell's text its paragraphs' (``w:p`` whose nearest ``w:tc`` is the cell) block
  texts joined by a space — ``table_select.first_row_text``'s reading, for any row;
* ``header: bool`` — the row is (true) or is not (false) a **header row**: its own ``w:trPr/w:tblHeader`` decides when
  it has one (on unless ``w:val`` is 0/false/off); without one, the nearest level of the table style chain
  (``table_select.style_chain``, nearest first) whose ``w:trPr`` holds a ``w:tblHeader`` decides; none → not a header row.

Unknown keys or a wrong shape → ``bad-params``. A table whose rows sit in a content control or custom XML
(``table_select.rows_wrapped``) cannot have its rows numbered: a row op refuses it (``table-structure``).

**Grid geometry.** A cell's **span** is its ``w:tcPr/w:gridSpan/@w:val`` (a whole number ≥ 1; absent → 1); its **grid
column** is the row's ``w:trPr/w:gridBefore/@w:val`` (absent → 0) plus the spans of the row's own cells before it. A
cell is **merged** when it spans more than one column, holds a ``w:vMerge`` (any value: it takes part in a vertical
merge) or a ``w:hMerge`` (Word's legacy horizontal merge). A **vertical merge** (``merge_groups``) is a run of rows down
one grid column: it opens at a cell whose ``w:vMerge`` is ``w:val="restart"`` (or at a continuing cell with no merge
open above it), goes on through the cells below it whose ``w:vMerge`` has no ``w:val`` or ``w:val="continue"``, and
ends at the first row without one there. Its content is its first row's cell. A deletion **cuts** a merge
(``merge_crossings``) when it takes the merge's first row and keeps another of its rows; taking later rows only shortens
it.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, List, Optional, Sequence, Set

from ..ooxml import block_full_text
from ..package import W
from .base import JobException, Touch
from .table_select import own_cells, own_rows

ROW_KEYS = ("index_range", "text_regex", "header")
OFF = ("0", "false", "off")
ROWS_SCHEMA = {"description": "'all', or {index_range: [a, b] (the table's own rows, both inclusive, 0-based), "
                              "text_regex (the row's text: its cells joined by a tab), header: bool (rows marked "
                              "as repeating header rows, by the row or its table style)} — every given key must hold"}


def check_row_selector(pred: Any, where: str = "select.rows") -> None:
    """``bad-params`` for a row selector the grammar does not allow (``None`` is allowed: the op decides whether rows
    must be named)."""
    if pred in (None, "all"):
        return
    if not isinstance(pred, dict):
        raise JobException(f"{where} must be 'all' or an object with {list(ROW_KEYS)}, got {pred!r}", "bad-params")
    unknown = set(pred) - set(ROW_KEYS)
    if unknown:
        raise JobException(f"unknown row selector key(s) {sorted(unknown)}; known: {list(ROW_KEYS)}", "bad-params")
    if "index_range" in pred:
        r = pred["index_range"]
        if not (isinstance(r, list) and len(r) == 2 and all(isinstance(x, int) and not isinstance(x, bool) for x in r)
                and 0 <= r[0] <= r[1]):
            raise JobException(f"rows.index_range must be [a, b] with whole numbers 0 ≤ a ≤ b (both inclusive), got "
                               f"{r!r}", "bad-params")
    if "text_regex" in pred:
        if not isinstance(pred["text_regex"], str):
            raise JobException("rows.text_regex must be a regular expression (text)", "bad-params")
        try:
            re.compile(pred["text_regex"])
        except re.error as e:
            raise JobException(f"rows.text_regex is not a valid regular expression: {e}", "bad-params")
    if "header" in pred and not isinstance(pred["header"], bool):
        raise JobException("rows.header must be true or false", "bad-params")


def _nearest(el, tag: str):
    q = el.getparent()
    while q is not None:
        if q.tag == tag:
            return q
        q = q.getparent()
    return None


def row_cells(tr) -> List[Any]:
    """Every w:tc whose nearest w:tr is the row, in document order."""
    return [tc for tc in tr.iter(W + "tc") if _nearest(tc, W + "tr") is tr]


def cell_paragraphs(tc) -> List[Any]:
    """Every w:p whose nearest w:tc is the cell, in document order."""
    return [p for p in tc.iter(W + "p") if _nearest(p, W + "tc") is tc]


def row_paragraphs(tr) -> List[Any]:
    return [p for tc in row_cells(tr) for p in cell_paragraphs(tc)]


def row_text(tr) -> str:
    return "\t".join(" ".join(block_full_text(p) for p in cell_paragraphs(tc)) for tc in row_cells(tr))


def is_on(el) -> bool:
    return el is not None and el.get(W + "val") not in OFF


def is_header_row(tr, chain: Sequence[Any]) -> bool:
    own = tr.find(f"{W}trPr/{W}tblHeader")
    if own is not None:
        return is_on(own)
    for st in chain:
        e = st.find(f"{W}trPr/{W}tblHeader")
        if e is not None:
            return is_on(e)
    return False


def selects_every_row(pred: Any) -> bool:
    return pred == "all" or pred == {}


def row_selected(pred: Any, r: int, tr, chain: Sequence[Any]) -> bool:
    if selects_every_row(pred):
        return True
    if "index_range" in pred:
        a, b = pred["index_range"]
        if not a <= r <= b:
            return False
    if "header" in pred and is_header_row(tr, chain) != pred["header"]:
        return False
    if "text_regex" in pred and not re.search(pred["text_regex"], row_text(tr)):
        return False
    return True


def selected_rows(tbl, pred: Any, chain: Sequence[Any]) -> List[int]:
    """The indices of the table's own rows the selector chooses, in order."""
    return [r for r, tr in enumerate(own_rows(tbl)) if row_selected(pred, r, tr, chain)]


def row_selection_reads(part: str, k: int, tbl, pred: Any, index) -> Set[Touch]:
    """What the row selection read in table ``k`` of the part (rule 9): ``text_regex`` the text of every own row's
    paragraphs (``(part, "<bi>", "verify:text")``, ``index``: w:p → its ordinal), ``header`` every own row's header
    marking (``(part, "tr:<k>:<r>", "verify-props:tblHeader")``; the style chain's is the caller's
    ``style_chain_reads``)."""
    out: Set[Touch] = set()
    if not isinstance(pred, dict):
        return out
    for r, tr in enumerate(own_rows(tbl)):
        if "text_regex" in pred:
            out.update((part, str(index[p]), "verify:text") for p in row_paragraphs(tr))
        if "header" in pred:
            out.add((part, f"tr:{k}:{r}", "verify-props:tblHeader"))
    return out


# ---- the grid --------------------------------------------------------------------------------------------------------

def _whole(el, attr: str = "val") -> Optional[int]:
    if el is None:
        return None
    try:
        return int(el.get(W + attr))
    except (TypeError, ValueError):
        return None


def cell_span(tc) -> int:
    span = _whole(tc.find(f"{W}tcPr/{W}gridSpan"))
    return span if span is not None and span >= 1 else 1


def grid_columns(tr) -> List[int]:
    """The grid column each own cell of the row starts at."""
    at = _whole(tr.find(f"{W}trPr/{W}gridBefore")) or 0
    out = []
    for tc in own_cells(tr):
        out.append(at)
        at += cell_span(tc)
    return out


def vertical_merge(tc) -> Optional[str]:
    """``restart``, ``continue`` or None (the cell takes no part in a vertical merge)."""
    vm = tc.find(f"{W}tcPr/{W}vMerge")
    if vm is None:
        return None
    return "restart" if vm.get(W + "val") == "restart" else "continue"


def is_merged(tc) -> bool:
    return cell_span(tc) > 1 or tc.find(f"{W}tcPr/{W}vMerge") is not None or tc.find(f"{W}tcPr/{W}hMerge") is not None


def merge_groups(tbl) -> List[List[int]]:
    """The table's vertical merges, each the own rows it covers, top first: per grid column, a cell whose merge
    restarts opens one, a continuing cell extends the one open in its column (or opens one when none is), and a row
    without a merging cell there closes it."""
    groups: List[List[int]] = []
    open_at: dict = {}
    for r, tr in enumerate(own_rows(tbl)):
        here = {c: vertical_merge(tc) for c, tc in zip(grid_columns(tr), own_cells(tr))}
        for c in list(open_at):
            if here.get(c) != "continue":
                del open_at[c]
        for c, vm in sorted((c, vm) for c, vm in here.items() if vm is not None):
            if vm == "continue" and c in open_at:
                open_at[c].append(r)
            else:
                open_at[c] = [r]
                groups.append(open_at[c])
    return groups


def merge_crossings(tbl, gone: Iterable[int]) -> List[int]:
    """The first rows of the vertical merges a deletion of the own rows ``gone`` cuts: the merge's first row (which
    holds its content) goes while another of its rows stays. Rows below the first going alone just shorten the merge;
    a merge going whole goes."""
    gone = set(gone)
    return [g[0] for g in merge_groups(tbl) if g[0] in gone and any(r not in gone for r in g)]
