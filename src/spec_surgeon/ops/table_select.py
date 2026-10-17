"""The table selector — transform side (built-in batch 5, docs/BUILTIN_OPS_BATCH5_DESIGN.md §5.1). The verifier's own
reading is verify/tableselect.py (rule 1: nothing shared); CLAUDE.md lists the definition among those that must stay
identical on both sides.

``select.scope`` (default ``["document"]``; headers, footers, footnotes, endnotes, comments allowed) names the parts;
``select.tables`` is ``"all"`` (default) or an object whose given keys must ALL hold:

* ``index_range: [a, b]`` — the table's **ordinal** (its index among ``root.iter(w:tbl)`` of its part: nested tables
  included, document order) lies in ``a … b``, both inclusive, 0-based (whole numbers, ``0 ≤ a ≤ b``);
* ``style_in: [ids]`` — the table's **style** (``table_style_id``: ``w:tblPr/w:tblStyle/@w:val`` — an empty or missing
  value names no style —, else, without a ``w:tblStyle``, the default table style) is one of the ids;
* ``first_row_regex`` — ``re.search`` on the **first row's text**: the first ``w:tr`` whose nearest ``w:tbl`` is the
  table; its cells (``w:tc`` whose nearest ``w:tr`` is that row) in document order joined by a tab, each cell's text its
  paragraphs' (``w:p`` whose nearest ``w:tc`` is the cell) block texts (CLAUDE.md's block-text definition) joined by a
  space; no row → "";
* ``text_regex`` — ``re.search`` on the block text of ANY paragraph whose nearest ``w:tbl`` is the table;
* ``nested: bool`` — the table has a ``w:tbl`` ancestor (inside a cell) or not.

Unknown keys or a wrong shape → ``bad-params``. A table op names a table ``(part, "tbl:<ordinal>")`` (batch 4's
convention, ``selectors.table_ordinals``), its rows ``tr:<ordinal>:<r>`` and cells ``tc:<ordinal>:<r>:<c>`` — ``r`` the
row's index among the table's OWN rows (``w:tr`` children of the ``w:tbl``), ``c`` the cell's index among the row's own
cells (``w:tc`` children of the ``w:tr``). A row or cell wrapped in a content control or custom XML is not an own one:
``rows_wrapped`` / ``cells_wrapped`` say so, and an op that numbers rows or cells refuses such a table
(``table-structure``).
"""
from __future__ import annotations

import re
from typing import Any, List, Optional, Sequence, Set, Tuple

from lxml import etree

from ..ooxml import block_full_text
from ..package import W
from .base import JobException, Touch
from .selectors import StyleSheet

TABLE_KEYS = ("index_range", "style_in", "first_row_regex", "text_regex", "nested")
TEXT_BOX = W + "txbxContent"


def check_table_selector(pred: Any) -> None:
    """``bad-params`` for a ``select.tables`` the grammar does not allow."""
    if pred in (None, "all"):
        return
    if not isinstance(pred, dict):
        raise JobException(f"select.tables must be 'all' or an object with {list(TABLE_KEYS)}, got {pred!r}",
                           "bad-params")
    unknown = set(pred) - set(TABLE_KEYS)
    if unknown:
        raise JobException(f"unknown table selector key(s) {sorted(unknown)}; known: {list(TABLE_KEYS)}", "bad-params")
    if "index_range" in pred:
        r = pred["index_range"]
        if not (isinstance(r, list) and len(r) == 2 and all(isinstance(x, int) and not isinstance(x, bool) for x in r)
                and 0 <= r[0] <= r[1]):
            raise JobException(f"tables.index_range must be [a, b] with whole numbers 0 ≤ a ≤ b (both inclusive), got "
                               f"{r!r}", "bad-params")
    if "style_in" in pred:
        s = pred["style_in"]
        if not (isinstance(s, list) and s and all(isinstance(x, str) and x for x in s)):
            raise JobException(f"tables.style_in must be a non-empty list of table style ids, got {s!r}", "bad-params")
    for key in ("first_row_regex", "text_regex"):
        if key in pred:
            if not isinstance(pred[key], str):
                raise JobException(f"tables.{key} must be a regular expression (text)", "bad-params")
            try:
                re.compile(pred[key])
            except re.error as e:
                raise JobException(f"tables.{key} is not a valid regular expression: {e}", "bad-params")
    if "nested" in pred and not isinstance(pred["nested"], bool):
        raise JobException("tables.nested must be true or false", "bad-params")


# ---- the table's pieces ----------------------------------------------------------------------------------------------

def _nearest(el, tag: str):
    q = el.getparent()
    while q is not None:
        if q.tag == tag:
            return q
        q = q.getparent()
    return None


def own_rows(tbl) -> List[Any]:
    return [c for c in tbl if c.tag == W + "tr"]


def own_cells(tr) -> List[Any]:
    return [c for c in tr if c.tag == W + "tc"]


def rows_wrapped(tbl) -> bool:
    """A row of the table sits in a container (content control, custom XML) instead of directly in the w:tbl."""
    return any(_nearest(tr, W + "tbl") is tbl and tr.getparent() is not tbl for tr in tbl.iter(W + "tr"))


def cells_wrapped(tr) -> bool:
    return any(_nearest(tc, W + "tr") is tr and tc.getparent() is not tr for tc in tr.iter(W + "tc"))


def all_rows(tbl) -> List[Any]:
    """Every w:tr whose nearest w:tbl is this table — its own rows and rows inside a content control or custom XML."""
    return [tr for tr in tbl.iter(W + "tr") if _nearest(tr, W + "tbl") is tbl]


def all_cells(tr) -> List[Any]:
    """Every w:tc whose nearest w:tr is this row (wrapped ones included)."""
    return [tc for tc in tr.iter(W + "tc") if _nearest(tc, W + "tr") is tr]


def is_nested(tbl) -> bool:
    return _nearest(tbl, W + "tbl") is not None


def in_text_box(tbl) -> bool:
    return _nearest(tbl, TEXT_BOX) is not None


def selects_all(pred: Any) -> bool:
    """The selection is every table (``"all"``, absent, or an empty object): a table in a text box is then skipped and
    flagged rather than refusing the file; a selection that names tables refuses one."""
    return pred in (None, "all") or pred == {}


def right_to_left(tbl, chain: Sequence[Any]) -> bool:
    """The table's own ``w:tblPr`` or any level of its style chain turns ``w:bidiVisual`` on (present, ``w:val`` not
    0/false/off): its columns run right to left, so which side 'left' names is not certain."""
    levels = [tbl.find(W + "tblPr")] + [st.find(W + "tblPr") for st in chain]
    return any(e.get(W + "val") not in ("0", "false", "off")
               for pr in levels if pr is not None for e in pr.findall(W + "bidiVisual"))


def table_style_id(tbl, sheet: StyleSheet) -> Optional[str]:
    """The table's style: its w:tblStyle's value (an empty or missing value names no style), else — no w:tblStyle — the
    default table style."""
    ts = tbl.find(f"{W}tblPr/{W}tblStyle")
    if ts is not None:
        return ts.get(W + "val") or None
    return sheet.defaults.get("table")


def table_paragraphs(tbl) -> List[Any]:
    """Every w:p whose nearest w:tbl is this table, in document order."""
    return [p for p in tbl.iter(W + "p") if _nearest(p, W + "tbl") is tbl]


def first_row_paragraphs(tbl) -> List[Any]:
    row = next((tr for tr in tbl.iter(W + "tr") if _nearest(tr, W + "tbl") is tbl), None)
    if row is None:
        return []
    out = []
    for tc in row.iter(W + "tc"):
        if _nearest(tc, W + "tr") is row:
            out += [p for p in tc.iter(W + "p") if _nearest(p, W + "tc") is tc]
    return out


def first_row_text(tbl) -> str:
    row = next((tr for tr in tbl.iter(W + "tr") if _nearest(tr, W + "tbl") is tbl), None)
    if row is None:
        return ""
    cells = []
    for tc in row.iter(W + "tc"):
        if _nearest(tc, W + "tr") is row:
            cells.append(" ".join(block_full_text(p) for p in tc.iter(W + "p") if _nearest(p, W + "tc") is tc))
    return "\t".join(cells)


def table_selected(pred: Any, tbl, ordinal: int, sheet: StyleSheet) -> bool:
    if pred in (None, "all"):
        return True
    if "index_range" in pred:
        a, b = pred["index_range"]
        if not a <= ordinal <= b:
            return False
    if "style_in" in pred and table_style_id(tbl, sheet) not in set(pred["style_in"]):
        return False
    if "nested" in pred and is_nested(tbl) != pred["nested"]:
        return False
    if "first_row_regex" in pred and not re.search(pred["first_row_regex"], first_row_text(tbl)):
        return False
    if "text_regex" in pred and not any(re.search(pred["text_regex"], block_full_text(p))
                                        for p in table_paragraphs(tbl)):
        return False
    return True


def selected_tables(root, pred: Any, sheet: StyleSheet) -> List[Tuple[int, Any]]:
    """(ordinal, w:tbl) of every selected table of a part, in document order."""
    return [(k, tbl) for k, tbl in enumerate(root.iter(W + "tbl")) if table_selected(pred, tbl, k, sheet)]


def selection_reads(part: str, root, pred: Any) -> Set[Touch]:
    """What the selection itself read over the whole part (rule 9): ``style_in`` every table's style
    (``(part, "tbl:<k>", "verify-props:tblPr")`` — a restyle of any table by another op changes the choice), the regexes
    the text of the paragraphs they searched (``(part, "<bi>", "verify:text")``)."""
    out: Set[Touch] = set()
    if not isinstance(pred, dict):
        return out
    tables = list(root.iter(W + "tbl"))
    if "style_in" in pred:
        out.update((part, f"tbl:{k}", "verify-props:tblPr") for k in range(len(tables)))
    if "first_row_regex" in pred or "text_regex" in pred:
        index = {p: i for i, p in enumerate(root.iter(W + "p"))}
        for tbl in tables:
            read = (table_paragraphs(tbl) if "text_regex" in pred else []) + \
                (first_row_paragraphs(tbl) if "first_row_regex" in pred else [])
            out.update((part, str(index[p]), "verify:text") for p in read)
    return out


def style_chain(tbl, sheet: StyleSheet) -> Tuple[List[Any], Optional[str]]:
    """(the table style chain's w:style elements nearest first — the table's style, then its basedOn ancestors,
    cycle-safe —, the id of a style the chain names that the file lacks, or None)."""
    sid = table_style_id(tbl, sheet)
    out, seen = [], set()
    while sid and sid not in seen:
        seen.add(sid)
        st = sheet.styles.get(sid)
        if st is None:
            return out, sid
        out.append(st)
        b = st.find(W + "basedOn")
        sid = b.get(W + "val") if b is not None else None
    return out, None


def style_chain_reads(sheet: StyleSheet, tbl) -> Set[Touch]:
    """What a prediction made from the table's style chain reads in the styles part (rule 9): ``verify:tblPr`` and
    ``verify:replace`` of every chain style (a removal or a replacing import changes them), ``verify:add`` of a style the
    chain names that the file lacks (an import adding it changes the chain)."""
    if sheet.part is None:
        return set()
    chain, missing = style_chain(tbl, sheet)
    out = {(sheet.part, f"style:{st.get(W + 'styleId')}", fam) for st in chain
           for fam in ("verify:tblPr", "verify:replace")}
    if missing:
        out.add((sheet.part, f"style:{missing}", "verify:add"))
    return out


def element_sig(el) -> Any:
    """A comparable signature of an element (tag, attributes order-free, stripped text, children in order) — None for
    no element."""
    if el is None:
        return None
    if not isinstance(el.tag, str):
        return ("#node", (el.text or "").strip())
    return (el.tag, tuple(sorted(el.attrib.items())), (el.text or "").strip(), tuple(element_sig(c) for c in el))


def build(tag: str, attrs: Sequence[Tuple[str, str]], children: Sequence[Any] = ()) -> Any:
    el = etree.Element(W + tag)
    for k, v in attrs:
        el.set(W + k, v)
    for c in children:
        el.append(c)
    return el
