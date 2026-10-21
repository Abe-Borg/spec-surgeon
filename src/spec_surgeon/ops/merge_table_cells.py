"""Op: merge_table_cells — merge one block of cells per chosen table into a single cell (built-in batch 6, F6 —
docs/BUILTIN_OPS_BATCH6_DESIGN.md §7).

The tables are chosen by the table selector (``select.scope``, ``select.tables``); in each, the **block** is the own rows
the row selector chooses (``select.rows``, F5's: an index range, the rows' text, header rows) by the grid columns the
column selector chooses (``select.columns``, ``ops/table_columns.py``) — both required, each an unbroken run (rows a … b,
columns c … d), else ``table-shape``. A table where either chooses nothing is left alone. Untracked only: Word cannot
track a merge (``tracked`` accepts only false).

**The merge** (``merge_work``, ``carry``). In each row of the block, the **covered cells** are the own cells covering a
column of c … d; the block's first row's first covered cell is the **anchor**.

* Content: every covered cell's content (its children but ``w:tcPr``: paragraphs, nested tables, range marks) in reading
  order — row by row, left to right — ends up in the anchor, after the anchor's own; an empty cell's paragraphs
  (``table_columns.cell_is_empty``) are dropped, an empty anchor's too. When every covered cell is empty the anchor keeps
  its own content and the others' goes.
* Horizontally: in each row the first covered cell stays with its own ``w:tcPr`` (the others go): ``w:gridSpan`` the
  block's width (none at 1), its ``w:tcW`` the row's covered cells' widths summed when they all have one of the same type
  (``dxa`` or ``pct``) with a whole width — else its own stays (flagged when the widths differ in kind).
* Vertically (more than one row): the anchor's ``w:vMerge`` is ``restart`` and each later row's cell ``<w:vMerge/>``
  (continue), its content one new paragraph with the properties of its first paragraph as a new row's cell takes them
  (``table_edit.cell_ppr``); one row: no ``w:vMerge``. Every element in schema order (``ooxml.TCPR_ORDER``).

A block that is already one cell (one row and one covered cell spanning exactly c … d, or exactly one vertical merge of
such cells over a … b) is left alone with a note. Refused, per table (in this order): rows or cells or a grid that cannot
be numbered (``table-structure`` / ``table-grid``, ``table_columns.check_numbered``); a block that is not one unbroken run
of rows and of columns (``table-shape``); a legacy horizontal merge (``w:hMerge``) in a row of the block (``table-grid``);
pending tracked changes of the table's structure or properties, or a row of the block inserted or deleted as a tracked
change (``existing-revisions``); a cell crossing the block's edge (``merge-overlap``); a column of the block without a
cell in some row (``table-shape``); a vertical merge partly inside the block, or a cell below it that would continue it
(``merge-overlap``); a covered cell without a paragraph (``table-structure``); a bookmark, comment, permission, move or
custom-XML range or a field one part of which moves with the content while the other stays between the anchor and it —
the merge would put it out of order (``table-anchor``). Nothing chosen anywhere → ``not-found`` unless ``allow_missing``.

V2: an empty paragraph dropped is a deletion, the new paragraph of a continuing cell an insertion, and a moved paragraph
that passes a paragraph staying where it was (one in a cell beside a block of several rows) a deletion at its place and
an insertion at its new one (``paragraph_order``). Verified by V10's
``merged_table_cells`` kind (verify/tablegrid.py), which re-derives every decision from the source with its own code,
merges the cells itself and compares each changed part whole.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from lxml import etree

from ..ooxml import TCPR_ORDER, block_full_text
from ..package import W, DocxPackage
from . import prose, register
from .base import BlockDiff, JobException, Op, PlanFragment
from .delete_table_columns import UNTRACKED_PARAMS, column_words, untracked_only
from .delete_table_rows import row_words
from .delete_tables import SCOPE_SCHEMA, TABLES_SCHEMA
from .fields import fields_of
from .selectors import StyleSheet, scope_parts
from .set_table_cells import cell_properties
from .set_table_props import write_element
from .table_columns import (COLUMNS_SCHEMA, cell_is_empty, check_column_selector, check_numbered,
                            column_selection_reads, legacy_merges, pending_table_changes, row_layout, selected_columns,
                            vertical_merges)
from .table_edit import COMMENT_MARKS, RANGE_FAMILIES, RANGE_KEY, cell_ppr
from .table_rows import (ROWS_SCHEMA, cell_paragraphs, check_row_selector, row_selection_reads, selected_rows,
                         vertical_merge)
from .table_select import (build, check_table_selector, in_text_box, own_rows, selection_reads, selects_all,
                           style_chain, style_chain_reads, table_selected)

LAYOUT_REASON = "merging table cells changes the table's layout by design"
SUMMED = ("dxa", "pct")


@dataclass
class MergeWork:
    """What merging the block rows a … b × grid columns c … d of a table does, decided from the table as it is."""
    tbl: Any
    rows: Tuple[int, int]
    cols: Tuple[int, int]
    covered: List[List[Any]]                                    # per row of the block, its covered cells left to right
    empty: Set[Any] = field(default_factory=set)                # the covered cells that are empty
    widths: List[Optional[Tuple[str, int]]] = field(default_factory=list)   # per row: the summed (type, width)
    firsts: List[Any] = field(default_factory=list)             # per later row: the new paragraph's w:pPr (or None)
    unsummed: int = 0                                           # rows whose widths could not be summed
    jumped: Optional[int] = None                                # where the first paragraph the content passes stands

    @property
    def cells(self) -> List[Any]:
        return [tc for row in self.covered for tc in row]

    @property
    def all_empty(self) -> bool:
        return all(tc in self.empty for tc in self.cells)

    def movers(self) -> List[Any]:
        """The covered cells whose content moves into the anchor."""
        return [] if self.all_empty else [tc for tc in self.cells[1:] if tc not in self.empty]


def runs(selected: Sequence[int]) -> bool:
    return list(selected) == list(range(selected[0], selected[-1] + 1))


def _int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def summed_width(cells: Sequence[Any]) -> Tuple[Optional[Tuple[str, int]], bool]:
    """(the type and sum of the cells' own w:tcW when every one has one of a single type, dxa or pct, with a whole width
    — None: the first cell keeps its own; whether that leaves widths of different kinds unsummed)."""
    if len(cells) < 2:
        return None, False
    found = []
    for tc in cells:
        w = tc.find(f"{W}tcPr/{W}tcW")
        found.append(None if w is None else (w.get(W + "type"), _int(w.get(W + "w"))))
    kinds = {f[0] if f else None for f in found}
    if len(kinds) == 1 and next(iter(kinds)) in SUMMED and all(f[1] is not None for f in found):
        return (found[0][0], sum(f[1] for f in found)), False
    return None, any(f is not None and f[0] not in ("auto", "nil") for f in found)


def _content(tc) -> List[Any]:
    return [c for c in tc if c.tag != W + "tcPr"]


def _order_problem(root, work: MergeWork, pos: Dict[Any, int]) -> Optional[str]:
    """What the merge would put out of order: a range or field with one part in content that moves and another that
    stays between the anchor's end and it."""
    movers = work.movers()
    if not movers:
        return None
    moved = {el for tc in movers for el in tc.iter()}
    end = max(pos[el] for el in work.cells[0].iter())

    def flips(els) -> bool:
        there = [pos[e] for e in els if e in moved]
        here = [pos[e] for e in els if e not in moved]
        return any(end < y < x for x in there for y in here)
    groups: Dict[Tuple[str, str], List[Any]] = {}
    for el in root.iter(*RANGE_FAMILIES, *COMMENT_MARKS):
        groups.setdefault((RANGE_KEY.get(el.tag, "comment"), el.get(W + "id") or ""), []).append(el)
    for (key, _rid), els in groups.items():
        if flips(els):
            what = "comment" if key == "comment" else RANGE_FAMILIES[els[0].tag]
            return f"a {what} with one end in a cell that merges and the other in a cell beside the block"
    for f in fields_of(root):
        if f.simple is None and flips([m for m in (f.begin, f.separate, f.end) if m is not None]):
            return f"a {f.type or 'field'} field with one part in a cell that merges and another beside the block"
    return None


def merge_work(root, tbl, rows: Sequence[int], cols: Sequence[int], where: str,
               pos: Dict[Any, int]) -> Optional[MergeWork]:
    """The merge of the block the selections chose (module docstring); None when it is already one cell;
    ``JobException`` for what must be done in Word."""
    if not runs(rows) or not runs(cols):
        raise JobException(f"{where}: the rows {list(rows)} and columns {list(cols)} chosen are not one unbroken block; "
                           f"say one block of rows and columns to merge", "table-shape", {"table": where})
    a, b, c, d = rows[0], rows[-1], cols[0], cols[-1]
    trs = own_rows(tbl)
    layouts = [row_layout(trs[r]) for r in range(a, b + 1)]
    covered = [[(start, span, tc) for start, span, tc in lay.cells if start <= d and start + span - 1 >= c]
               for lay in layouts]
    if all(len(row) == 1 and row[0][0] == c and row[0][1] == d - c + 1 for row in covered) and \
            (a == b or (c, list(range(a, b + 1))) in vertical_merges(tbl)):
        return None
    if legacy_merges([tc for lay in layouts for _s, _n, tc in lay.cells]):
        raise JobException(f"{where}: a row of the block has cells joined by Word's legacy horizontal merge (w:hMerge); "
                           f"merge them in Word", "table-grid", {"table": where})
    pending = pending_table_changes(tbl)
    pending += sorted({f"{n} (row)" for r in range(a, b + 1) for n in ("ins", "del")
                       if trs[r].find(f"{W}trPr/{W}{n}") is not None})
    if pending:
        raise JobException(f"{where} carries pending tracked changes of its structure ({', '.join(pending)}); accept or "
                           f"reject them first", "existing-revisions", {"table": where})
    for r, row in zip(range(a, b + 1), covered):
        if any(start < c or start + span - 1 > d for start, span, _tc in row):
            raise JobException(f"{where}: in row {r} a cell crosses the edge of the block (columns {c} through {d}) — "
                               f"choose a block that takes merged cells whole", "merge-overlap", {"table": where})
        if sum(span for _s, span, _tc in row) != d - c + 1:
            raise JobException(f"{where}: in row {r} part of columns {c} through {d} has no cell", "table-shape",
                               {"table": where})
    inside = set(range(a, b + 1))
    for col, rs in vertical_merges(tbl):
        if c <= col <= d and inside & set(rs) and not set(rs) <= inside:
            raise JobException(f"{where}: the cell merged down from row {rs[0]} lies partly inside the block — choose "
                               f"a block that takes it whole", "merge-overlap", {"table": where, "merge_start": rs[0]})
    if b > a and b + 1 < len(trs):
        below = next((tc for start, _s, tc in row_layout(trs[b + 1]).cells if start == c), None)
        if below is not None and vertical_merge(below) == "continue":
            raise JobException(f"{where}: the cell below the block (row {b + 1}) continues a merge and would join this "
                               f"one — unmerge it in Word first", "merge-overlap", {"table": where})
    cells = [[tc for _s, _n, tc in row] for row in covered]
    if any(not cell_paragraphs(tc) for row in cells for tc in row):
        raise JobException(f"{where}: a cell of the block has no paragraph", "table-structure", {"table": where})
    work = MergeWork(tbl=tbl, rows=(a, b), cols=(c, d), covered=cells)
    work.empty = {tc for tc in work.cells if cell_is_empty(tc)}
    for row in cells:
        total, odd = summed_width(row)
        work.widths.append(total)
        work.unsummed += int(odd)
    work.firsts = [cell_ppr(cell_paragraphs(row[0])[0]) for row in cells[1:]]
    why = _order_problem(root, work, pos)
    if why:
        raise JobException(f"{where}: {why}; merging would put it out of order — merge them in Word", "table-anchor",
                           {"table": where})
    movers = work.movers()
    if movers:
        held = set(work.cells)
        end = max(pos[el] for el in work.cells[0].iter())
        last = max(pos[el] for tc in movers for el in tc.iter())
        passed = [pos[p] for r in range(a, b + 1) for p in trs[r].iter(W + "p")
                  if end < pos[p] < last and not any(x in held for x in p.iterancestors(W + "tc"))]
        work.jumped = min(passed) if passed else None
    return work


def carry(work: MergeWork) -> None:
    """The merge written (module docstring)."""
    anchor = work.cells[0]
    if not work.all_empty:
        if anchor in work.empty:
            for child in _content(anchor):
                anchor.remove(child)
        for tc in work.movers():
            for child in _content(tc):
                anchor.append(child)
    span = work.cols[1] - work.cols[0] + 1
    several = len(work.covered) > 1
    for i, row in enumerate(work.covered):
        keep = row[0]
        for tc in row[1:]:
            tc.getparent().remove(tc)
        tcpr = cell_properties(keep)
        write_element(tcpr, "gridSpan", build("gridSpan", [("val", str(span))]) if span > 1 else None, TCPR_ORDER)
        if work.widths[i] is not None:
            tcpr.find(W + "tcW").set(W + "w", str(work.widths[i][1]))
        merge = (build("vMerge", [("val", "restart")]) if i == 0 else build("vMerge", [])) if several else None
        write_element(tcpr, "vMerge", merge, TCPR_ORDER)
        if i:
            for child in _content(keep):
                keep.remove(child)
            p = etree.SubElement(keep, W + "p")
            if work.firsts[i - 1] is not None:
                p.append(copy.deepcopy(work.firsts[i - 1]))


# ---- where the paragraphs go (V2) -----------------------------------------------------------------------------------

class NewParagraph:
    """The new paragraph of a continuing cell (a stand-in in the paragraph order)."""

    def __init__(self, cell):
        self.cell = cell


def _owner(token, cells: Set[Any]):
    start = token.cell if isinstance(token, NewParagraph) else token
    if start in cells:
        return start
    return next((a for a in start.iterancestors(W + "tc") if a in cells), None)


def paragraph_order(root, works: Sequence[MergeWork], pos: Dict[Any, int]) -> Tuple[List[Any], Set[Any], Set[Any]]:
    """The part's paragraphs in the output's order (source w:p, and a ``NewParagraph`` per continuing cell), the source
    paragraphs dropped, and those moved past a paragraph that stays (one standing after the anchor and before them in
    the source, outside the block) — the merges of the tables nested deepest first."""
    order: List[Any] = list(root.iter(W + "p"))
    dropped: Set[Any] = set()
    moved: Set[Any] = set()
    for work in sorted(works, key=lambda w: -sum(1 for _ in w.tbl.iterancestors(W + "tbl"))):
        cells = set(work.cells)
        owners = [_owner(t, cells) for t in order]
        anchor = work.cells[0]
        last = max(i for i, o in enumerate(owners) if o is anchor)
        keep_anchor = work.all_empty or anchor not in work.empty
        movers = work.movers()
        carried = [t for tc in movers for t, o in zip(order, owners) if o is tc]
        continuing = {row[0] for row in work.covered[1:]}
        started: Set[Any] = set()
        out: List[Any] = []
        for i, (t, o) in enumerate(zip(order, owners)):
            if o is None:
                out.append(t)
            elif o is anchor:
                if keep_anchor:
                    out.append(t)
                else:
                    dropped.add(t)
                if i == last:
                    out += carried
                    if work.jumped is not None:
                        moved.update(x for x in carried if not isinstance(x, NewParagraph) and pos[x] > work.jumped)
            else:
                if o in continuing and o not in started:
                    started.add(o)
                    out.append(NewParagraph(o))
                if o in work.empty:
                    dropped.add(t)
        order = out
    return order, dropped, moved


@register
class MergeTableCells(Op):
    name = "merge_table_cells"
    mode = "untracked"
    description = ("Merge one block of cells per chosen table — the rows the row selector chooses by the columns the "
                   "column selector chooses, each an unbroken run — into a single cell: the cells' text is kept in "
                   "reading order (empty cells' paragraphs dropped), the first cell's formatting and summed widths "
                   "stay. Untracked only; a block that cuts an existing merged cell is refused.")
    params_schema = {"type": "object", "properties": dict(UNTRACKED_PARAMS)}
    select_schema = {"type": "object", "properties": {"scope": SCOPE_SCHEMA, "tables": TABLES_SCHEMA,
                                                      "rows": ROWS_SCHEMA, "columns": COLUMNS_SCHEMA},
                     "required": ["rows", "columns"]}
    verification_profile = {
        "V2": "dropped empty paragraphs listed as deleted, each continuing cell's new paragraph as inserted, and — when "
              "the content moves past a paragraph beside the block — every moved paragraph as deleted and inserted",
        "V4": "no revision delta",
        "V10": "merged_table_cells: the tables, the block and every decision re-derived from the source; each changed "
               "part compared whole with the source as the verifier merges the cells itself",
        "V7": "none — the table's layout changes by design",
    }

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        place = "of " + prose.where({k: v for k, v in select.items() if k not in ("rows", "columns")},
                                    default="every table")[3:]
        block = f"{row_words(select.get('rows'))} and {column_words(select.get('columns'), one=True)}"
        first = (f"Merge the cells in {block} {place} into one cell, keeping their text in reading order, without "
                 f"tracked changes.")
        extra = ["Empty cells add nothing and the first cell's formatting stays"]
        if params.get("allow_missing"):
            extra.append("files with no such cells are left unchanged")
        return prose.sentence(first, "; ".join(extra))

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        raw_select = dict(select or {})
        unknown = set(raw_select) - {"scope", "tables", "rows", "columns"}
        if unknown:
            raise JobException(f"merge_table_cells: unknown selector key(s) {sorted(unknown)}", "bad-params")
        allow = untracked_only(dict(params or {}), self.name, "merging table cells")
        pred = raw_select.get("tables", "all")
        check_table_selector(pred)
        rows_pred, cols_pred = raw_select.get("rows"), raw_select.get("columns")
        if rows_pred is None or cols_pred is None:
            raise JobException("say which block of cells to merge: select.rows (e.g. an index range) and "
                               "select.columns (e.g. column numbers)", "bad-params")
        check_row_selector(rows_pred)
        check_column_selector(cols_pred)
        parts = scope_parts(pkg, raw_select.get("scope", ["document"]))
        sheet = StyleSheet(pkg)
        frag = self.fragment(raw_select, {**params, "tracked": False})
        frag.untracked_diffs_expected = True        # untracked whether or not a paragraph moves (the spec's mode)
        targets: Dict[str, List[list]] = {}
        boxed: List[str] = []
        noops: List[str] = []
        alone: List[str] = []
        n_tables = unsummed = 0
        for part in parts:
            root = pkg.xml(part)
            frag.touches += sorted(selection_reads(part, root, pred))
            frag.touches.append((part, "scope", "verify"))
            index = {p: i for i, p in enumerate(root.iter(W + "p"))}
            pos: Dict[Any, int] = {}
            items: List[list] = []
            works: List[MergeWork] = []
            for k, tbl in enumerate(root.iter(W + "tbl")):
                if not table_selected(pred, tbl, k, sheet):
                    continue
                where = f"{part} table {k}"
                if in_text_box(tbl):
                    if selects_all(pred):
                        boxed.append(where)
                        continue
                    raise JobException(f"{where} lies in a text box (Word keeps two copies of a text box)", "textbox",
                                       {"table": where})
                check_numbered(tbl, where)
                chain, _missing = style_chain(tbl, sheet)
                frag.touches += sorted(row_selection_reads(part, k, tbl, rows_pred, index))
                if isinstance(rows_pred, dict) and "header" in rows_pred:
                    frag.touches += sorted(style_chain_reads(sheet, tbl))
                frag.touches += sorted(column_selection_reads(part, tbl, cols_pred, index))
                rs, cols = selected_rows(tbl, rows_pred, chain), selected_columns(tbl, cols_pred)
                if not rs or not cols:
                    alone.append(where)
                    continue
                pos = pos or {el: i for i, el in enumerate(root.iter())}
                work = merge_work(root, tbl, rs, cols, where, pos)
                if work is None:
                    noops.append(where)
                    continue
                works.append(work)
                items.append([k, list(work.rows), list(work.cols)])
                unsummed += work.unsummed
            if not works:
                continue
            order, dropped, moved = paragraph_order(root, works, pos)
            out = {id(t): i for i, t in enumerate(order)}
            for p in sorted(dropped, key=index.get):
                frag.diffs.append(BlockDiff(part=part, block_index=index[p], before=block_full_text(p), after=None,
                                            note="an empty paragraph of a merged cell"))
            for p in sorted(moved, key=index.get):
                text = block_full_text(p)
                frag.diffs.append(BlockDiff(part=part, block_index=index[p], before=text, after=None,
                                            note=f"moved into the merged cell, to block {out[id(p)]}"))
                frag.diffs.append(BlockDiff(part=part, block_index=out[id(p)], before=None, after=text,
                                            note=f"moved from block {index[p]} into the merged cell"))
            for t in order:
                if isinstance(t, NewParagraph):
                    frag.diffs.append(BlockDiff(part=part, block_index=out[id(t)], before=None, after="",
                                                note="the empty paragraph of a cell merged into the one above"))
            n_tables += len(items)
            targets[part] = items
            frag.intended_parts.append(part)
            frag.touches.append((part, "*", "text"))
        frag.property_assertions.append({
            "kind": "merged_table_cells", "parts": parts, "select": raw_select,
            "params": {k: params[k] for k in sorted(UNTRACKED_PARAMS) if k in params}, "expected_tables": n_tables,
            "changed": targets})
        if boxed:
            frag.flags.append(f"{len(boxed)} table(s) in a text box left alone (Word keeps two copies of a text box): "
                              f"{boxed}")
        if noops:
            frag.notes.append(f"merge_table_cells: the block is one cell already in {len(noops)} table(s), left "
                              f"alone: {noops}")
        if alone:
            frag.notes.append(f"merge_table_cells: no row or no column chosen in {len(alone)} table(s), left alone")
        if not n_tables:
            if allow or noops:
                frag.notes.append("merge_table_cells: no cells to merge in this file; no change.")
                return frag
            raise JobException(f"no block of cells matches the rows {rows_pred!r} and columns {cols_pred!r} in the "
                               f"tables {pred!r}", "not-found", {"tables": pred, "rows": rows_pred,
                                                                  "columns": cols_pred})
        frag.flags.append(f"UNTRACKED merge of cells in {n_tables} table(s) (Word cannot track it).")
        if unsummed:
            frag.flags.append(f"{unsummed} merged row(s) keep their first cell's width: the cells' widths are of "
                              f"different kinds and could not be summed; Word refits them.")
        frag.geometry_reference, frag.geometry_reason = "none", LAYOUT_REASON
        frag.transform_data = {"targets": targets}
        frag.notes.append(f"merge_table_cells: one block in each of {n_tables} table(s) (untracked); the merged cell "
                          f"keeps its first cell's formatting")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        for part, items in (frag.transform_data or {}).get("targets", {}).items():
            root = pkg.xml(part)
            tables = list(root.iter(W + "tbl"))
            pos = {el: i for i, el in enumerate(root.iter())}
            works = [merge_work(root, tables[k], list(range(a, b + 1)), list(range(c, d + 1)), f"{part} table {k}", pos)
                     for k, (a, b), (c, d) in items]
            for work in sorted(works, key=lambda w: -sum(1 for _ in w.tbl.iterancestors(W + "tbl"))):
                carry(work)
            pkg.write_xml(part, root)
