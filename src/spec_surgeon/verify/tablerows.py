"""The row selector and the row geometry — the verifier's own reading (built-in batch 6,
docs/BUILTIN_OPS_BATCH6_DESIGN.md §6). The transform side is ops/table_rows.py; nothing here comes from it (rule 1).

* **Own rows**: the ``w:tr`` children of the ``w:tbl`` (``tableselect.v_rows``), numbered from 0.
* ``index_range [a, b]``: a ≤ row ≤ b; ``text_regex``: ``re.search`` over the row's text — the cells whose closest row
  is it, joined by a tab, each the block texts (``textcheck.blocks``) of the paragraphs whose closest cell is it, joined
  by a space; ``header``: the row's own ``w:trPr/w:tblHeader`` (on unless 0/false/off) when it has one, else the first
  level of the table style chain (nearest first) whose ``w:trPr`` holds one, else not a header row. All given keys hold.
* **Header run** (``v_header_top``): the own rows from the first that are header rows, up to the first that is not —
  what Word and LibreOffice repeat on each page.
* **Grid**: a cell's span ``w:gridSpan`` (≥ 1, default 1), its column the row's ``w:gridBefore`` plus the spans before
  it; **merged**: a span over 1, a ``w:vMerge`` or a ``w:hMerge``. A **vertical merge** runs down one column from a
  ``restart`` cell (or a continuing cell with none open above) through the continuing cells (``w:vMerge`` without
  ``w:val`` or ``continue``) below it; a deletion **cuts** it when it takes the first row and keeps another.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

from .reader import W
from .tableselect import v_cells, v_rows

_V_KEYS = {"index_range", "text_regex", "header"}
_V_OFF = {"0", "false", "off"}


def v_check_rows(pred: Any) -> None:
    """ValueError for a row selector the op must refuse (bad-params); None and 'all' are fine."""
    if pred in (None, "all"):
        return
    if not isinstance(pred, dict) or set(pred) - _V_KEYS:
        raise ValueError(f"bad row selector {pred!r} (the op must refuse)")
    r = pred.get("index_range", [0, 0])
    if not (isinstance(r, list) and len(r) == 2 and all(type(x) is int for x in r) and 0 <= r[0] <= r[1]):
        raise ValueError(f"bad rows.index_range {r!r} (the op must refuse)")
    if "text_regex" in pred:
        if not isinstance(pred["text_regex"], str):
            raise ValueError("bad rows.text_regex (the op must refuse)")
        try:
            re.compile(pred["text_regex"])
        except re.error as e:
            raise ValueError(f"bad rows.text_regex: {e} (the op must refuse)")
    if "header" in pred and not isinstance(pred["header"], bool):
        raise ValueError("bad rows.header (the op must refuse)")


def _v_up(el, tag: str):
    for anc in el.iterancestors(tag):
        return anc
    return None


def v_row_cells(tr) -> List[Any]:
    return [tc for tc in tr.iter(W + "tc") if _v_up(tc, W + "tr") is tr]


def v_cell_paragraphs(tc) -> List[Any]:
    return [p for p in tc.iter(W + "p") if _v_up(p, W + "tc") is tc]


def v_row_text(tr, text_of: Dict[Any, str]) -> str:
    """The row's text; ``text_of`` maps each w:p to its block text."""
    return "\t".join(" ".join(text_of[p] for p in v_cell_paragraphs(tc)) for tc in v_row_cells(tr))


def v_header_row(tr, chain: List[Any]) -> bool:
    pr = tr.find(W + "trPr")
    own = pr.find(W + "tblHeader") if pr is not None else None
    if own is None:
        own = next((e for st in chain for e in [st.find(f"{W}trPr/{W}tblHeader")] if e is not None), None)
    return own is not None and own.get(W + "val") not in _V_OFF


def v_header_top(tbl, chain: List[Any]) -> int:
    """The number of own rows, counted from the first, that are header rows without a break — the rows a page repeats."""
    rows = v_rows(tbl)
    k = 0
    while k < len(rows) and v_header_row(rows[k], chain):
        k += 1
    return k


def v_rows_chosen(tbl, pred: Any, chain: List[Any], text_of: Dict[Any, str]) -> List[int]:
    """The own rows of the table the selector chooses."""
    out = []
    for r, tr in enumerate(v_rows(tbl)):
        if pred == "all" or pred == {}:
            out.append(r)
            continue
        if "index_range" in pred and not pred["index_range"][0] <= r <= pred["index_range"][1]:
            continue
        if "header" in pred and v_header_row(tr, chain) is not pred["header"]:
            continue
        if "text_regex" in pred and re.search(pred["text_regex"], v_row_text(tr, text_of)) is None:
            continue
        out.append(r)
    return out


def _v_int(el) -> Optional[int]:
    try:
        return int(el.get(W + "val")) if el is not None else None
    except (TypeError, ValueError):
        return None


def v_span(tc) -> int:
    pr = tc.find(W + "tcPr")
    n = _v_int(pr.find(W + "gridSpan")) if pr is not None else None
    return n if n is not None and n >= 1 else 1


def v_merged(tc) -> bool:
    pr = tc.find(W + "tcPr")
    return v_span(tc) > 1 or (pr is not None and (pr.find(W + "vMerge") is not None or pr.find(W + "hMerge") is not None))


def _v_columns(tr) -> Dict[int, Any]:
    """grid column → the own cell starting there."""
    pr = tr.find(W + "trPr")
    at = (_v_int(pr.find(W + "gridBefore")) if pr is not None else None) or 0
    out = {}
    for tc in v_cells(tr):
        out[at] = tc
        at += v_span(tc)
    return out


def _v_vmerge(tc) -> Optional[str]:
    pr = tc.find(W + "tcPr")
    vm = pr.find(W + "vMerge") if pr is not None else None
    if vm is None:
        return None
    return "restart" if vm.get(W + "val") == "restart" else "continue"


def v_merges(tbl) -> List[List[int]]:
    """Every vertical merge of the table: the own rows it covers, top first."""
    found: List[List[int]] = []
    running: Dict[int, List[int]] = {}
    for r, tr in enumerate(v_rows(tbl)):
        here = {c: _v_vmerge(tc) for c, tc in _v_columns(tr).items()}
        running = {c: g for c, g in running.items() if here.get(c) == "continue"}
        for c in sorted(here):
            if here[c] is None:
                continue
            if here[c] == "continue" and c in running:
                running[c].append(r)
            else:
                running[c] = [r]
                found.append(running[c])
    return found


def v_cuts(tbl, gone: Set[int]) -> List[int]:
    """The first rows of the vertical merges that deleting the own rows ``gone`` would cut."""
    return [m[0] for m in v_merges(tbl) if m[0] in gone and not set(m) <= gone]
