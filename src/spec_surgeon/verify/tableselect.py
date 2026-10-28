"""The table selector — the verifier's own reading (built-in batch 5, docs/BUILTIN_OPS_BATCH5_DESIGN.md §5.1). The
transform side is ops/table_select.py; nothing here comes from it (rule 1).

* A table's **ordinal**: its index among every ``w:tbl`` of the part in document order (nested ones counted).
* Its **style** (``v_table_style``): the ``w:tblPr/w:tblStyle`` value — an empty or missing value names no style —, else,
  when the table has no ``w:tblStyle``, the default table style (``Styles.default["table"]``).
* ``index_range [a, b]``: a ≤ ordinal ≤ b; ``style_in``: the style is listed; ``nested``: the table lies inside another
  ``w:tbl``; ``first_row_regex``: ``re.search`` over the first row of the table (the first ``w:tr`` whose closest table
  ancestor is this one) — its cells (``w:tc`` whose closest row is it) joined by a tab, each the block texts of its
  paragraphs (``w:p`` whose closest cell is it) joined by a space; ``text_regex``: ``re.search`` over the block text of
  any paragraph whose closest table is this one. Block text is CLAUDE.md's (``textcheck.blocks``). All given keys hold.
* **Own rows** / **own cells**: the ``w:tr`` children of the ``w:tbl`` / the ``w:tc`` children of the ``w:tr``.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .reader import W
from .textcheck import blocks
from .patterns import v_compile, v_search

_V_TABLE_TEXT = {"first_row_regex": "a table's first row", "text_regex": "a table paragraph's text"}

_V_KEYS = {"index_range", "style_in", "first_row_regex", "text_regex", "nested"}


def _v_up(el, tag: str):
    """The closest ancestor of ``el`` with ``tag``, or None."""
    for anc in el.iterancestors(tag):
        return anc
    return None


def v_rows(tbl) -> List[Any]:
    return [c for c in tbl if c.tag == W + "tr"]


def v_cells(tr) -> List[Any]:
    return [c for c in tr if c.tag == W + "tc"]


def v_rows_wrapped(tbl) -> bool:
    return any(_v_up(tr, W + "tbl") is tbl and tr.getparent() is not tbl for tr in tbl.iter(W + "tr"))


def v_cells_wrapped(tr) -> bool:
    return any(_v_up(tc, W + "tr") is tr and tc.getparent() is not tr for tc in tr.iter(W + "tc"))


def v_in_text_box(tbl) -> bool:
    return _v_up(tbl, W + "txbxContent") is not None


def v_table_style(tbl, styles) -> Optional[str]:
    pr = tbl.find(W + "tblPr")
    ts = pr.find(W + "tblStyle") if pr is not None else None
    if ts is None:
        return styles.default.get("table")
    return ts.get(W + "val") or None


def v_check(pred: Any) -> None:
    """ValueError for a selector the op must refuse (bad-params)."""
    if pred in (None, "all"):
        return
    if not isinstance(pred, dict) or set(pred) - _V_KEYS:
        raise ValueError(f"bad table selector {pred!r} (the op must refuse)")
    r = pred.get("index_range", [0, 0])
    if not (isinstance(r, list) and len(r) == 2 and all(type(x) is int for x in r) and 0 <= r[0] <= r[1]):
        raise ValueError(f"bad index_range {r!r} (the op must refuse)")
    s = pred.get("style_in", ["x"])
    if not (isinstance(s, list) and s and all(isinstance(x, str) and x for x in s)):
        raise ValueError(f"bad style_in {s!r} (the op must refuse)")
    for key in ("first_row_regex", "text_regex"):
        if key in pred:
            v_compile(pred[key], where=_V_TABLE_TEXT[key])        # a ValueError where the op must refuse
    if "nested" in pred and not isinstance(pred["nested"], bool):
        raise ValueError("bad nested (the op must refuse)")


class VTables:
    """One part's tables, with the block text of every paragraph (by identity)."""

    def __init__(self, root):
        self.root = root
        self.tables = list(root.iter(W + "tbl"))
        pars = list(root.iter(W + "p"))
        self.index = {p: i for i, p in enumerate(pars)}
        self.text = {p: b.text for p, b in zip(pars, blocks(root))}

    def first_row(self, tbl) -> str:
        row = next((tr for tr in tbl.iter(W + "tr") if _v_up(tr, W + "tbl") is tbl), None)
        if row is None:
            return ""
        cells = [tc for tc in row.iter(W + "tc") if _v_up(tc, W + "tr") is row]
        return "\t".join(" ".join(self.text[p] for p in tc.iter(W + "p") if _v_up(p, W + "tc") is tc) for tc in cells)

    def any_paragraph(self, tbl, pattern: str) -> bool:
        rx = v_compile(pattern, where=_V_TABLE_TEXT["text_regex"])
        return any(rx.search(self.text[p]) for p in tbl.iter(W + "p") if _v_up(p, W + "tbl") is tbl)

    def matches(self, pred: Any, k: int, styles) -> bool:
        if pred in (None, "all"):
            return True
        tbl = self.tables[k]
        if "index_range" in pred and not pred["index_range"][0] <= k <= pred["index_range"][1]:
            return False
        if "style_in" in pred and v_table_style(tbl, styles) not in pred["style_in"]:
            return False
        if "nested" in pred and (_v_up(tbl, W + "tbl") is not None) != pred["nested"]:
            return False
        if "first_row_regex" in pred and v_search(pred["first_row_regex"], self.first_row(tbl),
                                                  where=_V_TABLE_TEXT["first_row_regex"]) is None:
            return False
        if "text_regex" in pred and not self.any_paragraph(tbl, pred["text_regex"]):
            return False
        return True

    def selected(self, pred: Any, styles) -> List[Tuple[int, Any]]:
        return [(k, t) for k, t in enumerate(self.tables) if self.matches(pred, k, styles)]


def v_style_chain(tbl, styles) -> List[Any]:
    """The table style chain, nearest first (``Styles.chain``: the table's style and its basedOn ancestors)."""
    return styles.chain(v_table_style(tbl, styles))


def v_parse_select(sel: Dict[str, Any]) -> Tuple[List[str], Any]:
    """(the scope kinds, the tables predicate) of a table op's select, with this module's defaults."""
    if not isinstance(sel, dict) or set(sel) - {"scope", "tables"}:
        raise ValueError(f"unknown selector key(s) in {sel!r}")
    scope = sel.get("scope", ["document"])
    pred = sel.get("tables", "all")
    v_check(pred)
    return ([scope] if isinstance(scope, str) else list(scope)), pred
