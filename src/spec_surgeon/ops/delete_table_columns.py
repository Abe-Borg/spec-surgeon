"""Op: delete_table_columns — delete the grid columns the column selector chooses in the chosen tables (built-in batch
6, F6 — docs/BUILTIN_OPS_BATCH6_DESIGN.md §7).

The tables are chosen by the table selector (``select.scope``, ``select.tables``; default every table of the body) and
their columns by the column selector (``select.columns``, required — ``ops/table_columns.py``: grid column numbers, the
header text). A table no column of which is chosen is left alone. Untracked only: Word cannot track a column deletion
(``tracked`` accepts only false).

**The deletion** (``deletion``), over the table's grid (``table_columns.row_layout``):

* every chosen ``w:gridCol`` goes;
* in each own row, a cell covering only chosen columns goes with all it holds (its text deleted — V2 lists every
  paragraph in it; a table nested in it goes with it); a cell covering chosen and kept columns stays with its content,
  its ``w:gridSpan`` reduced by the chosen columns it covers (gone at 1) and a ``w:tcW`` of type ``dxa`` by their
  widths; a ``w:tcW`` of another type stays (one in percent is flagged: Word refits it);
* a row's ``w:gridBefore`` / ``w:gridAfter`` loses the chosen columns it counts — at 0 it goes with its ``w:wBefore`` /
  ``w:wAfter`` (an emptied ``w:trPr`` goes); otherwise a ``w:wBefore`` / ``w:wAfter`` of type ``dxa`` loses their widths;
* a fixed table width (``w:tblW`` of type ``dxa`` above 0) shrinks by the chosen columns' widths; a width in percent
  stays (flagged: Word widens the columns left to fill it); an automatic one stays.

A width that would not stay above 0, or a chosen column with no width where its width must be taken off → ``table-grid``.

Refused, per table (in this order; a table in a text box is left alone under an every-table selection, flagged, and
refused when named, ``textbox``; a table nested in a cell that goes goes with it and is not looked at): rows or cells
that cannot be numbered (``table-structure``) or a grid that cannot (``table-grid``) — ``table_columns.check_numbered``;
then, when columns are chosen: every grid column (``every-column``: delete the table); a legacy horizontal merge
(``w:hMerge``) in an own cell (``table-grid``); pending tracked changes of the table's structure or properties
(``existing-revisions``, ``table_columns.pending_table_changes``); a row left without a cell (``table-grid``: delete the
row first); a vertical merge some of whose cells go while others stay (``vertical-merge``). Over everything the deleted
cells hold, ``table_edit.removal``'s untracked rules (``section-break``, ``table-anchor``); a bookmark crossing the edge
of the deleted cells keeps its outside end, the inside end taking the cell's place in the row. Nothing chosen anywhere →
``not-found`` unless ``allow_missing``.

Verified by V10's ``deleted_table_columns`` kind (verify/tablegrid.py), which re-derives the tables, the columns and every
decision from the source with its own code and compares each changed part whole.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from ..package import W, DocxPackage
from . import prose, register
from .base import BlockDiff, JobException, Op, PlanFragment
from .delete_tables import SCOPE_SCHEMA, TABLES_SCHEMA
from .selectors import StyleSheet, scope_parts
from .table_columns import (COLUMNS_SCHEMA, check_column_selector, check_numbered, column_selection_reads, column_width,
                            covers, grid_columns_of, legacy_merges, pending_table_changes, row_layout, selected_columns,
                            vertical_merges)
from .table_edit import removal, removal_revisions, remove, removed_paragraphs
from .table_select import (check_table_selector, in_text_box, own_cells, own_rows, selection_reads, selects_all,
                           table_selected)

REFLOW_REASON = "deleting table columns changes the table's layout and reflows the content after it"
UNTRACKED_PARAMS = {
    "tracked": {"type": "boolean", "enum": [False],
                "description": "not tracked — Word cannot track a change to a table's columns (only false is accepted)"},
    "allow_missing": {"type": "boolean", "default": False,
                      "description": "true = a file with nothing to change is left as it is instead of set aside"},
}


def untracked_only(params: Dict[str, Any], name: str, what: str) -> bool:
    """``allow_missing`` — ``bad-params`` for an unknown parameter or ``tracked`` other than false."""
    unknown = set(params) - set(UNTRACKED_PARAMS)
    if unknown:
        raise JobException(f"{name}: unknown parameter(s) {sorted(unknown)}; known: {sorted(UNTRACKED_PARAMS)}",
                           "bad-params")
    if params.get("tracked", False) is not False:
        raise JobException(f"{what} is not tracked (Word cannot track it): only tracked false is accepted",
                           "bad-params")
    allow = params.get("allow_missing", False)
    if not isinstance(allow, bool):
        raise JobException("allow_missing must be true or false", "bad-params")
    return allow


def column_words(columns: Any, one: bool = False) -> str:
    """``column 2``, ``columns 1 and 3``, ``the columns headed by the pattern “x”`` (``one``: the block a merge covers)."""
    if columns in (None, "all", {}) or not isinstance(columns, dict):
        return "every column" if columns in ("all", {}) else "the chosen columns"
    ix = columns.get("index")
    span = ""
    if isinstance(ix, list) and ix and all(isinstance(g, int) and not isinstance(g, bool) for g in ix):
        nums = sorted(ix)
        if one and len(nums) > 1 and nums == list(range(nums[0], nums[-1] + 1)):
            span = f"columns {nums[0] + 1} through {nums[-1] + 1}"
        else:
            span = (f"column {nums[0] + 1}" if len(nums) == 1 else "columns " + prose.join(str(g + 1) for g in nums))
    head = ("the columns headed by the pattern " + prose.quote(columns["header_regex"])) \
        if columns.get("header_regex") else ""
    if span and head:
        return f"{head} among {span}"
    return span or head or "the chosen columns"


def _int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


@dataclass
class ColumnDeletion:
    """What deleting the grid columns ``cols`` of a table does, decided from the table as it is."""
    tbl: Any
    cols: List[int]
    gone: List[Any] = field(default_factory=list)                                 # the w:tc that go
    spans: List[Tuple[Any, int, Optional[int]]] = field(default_factory=list)     # (w:tc, its span, its dxa width)
    edges: List[Tuple[Any, str, int, Optional[int]]] = field(default_factory=list)  # (w:tr, Before/After, count, width)
    width: Optional[int] = None                                                   # the table's new fixed width
    pct_cells: int = 0                                                            # cell / row widths in percent kept
    pct_table: bool = False                                                       # the table's width is in percent


def deletion(tbl, cols: Sequence[int], where: str) -> ColumnDeletion:
    """The deletion of the grid columns ``cols`` of ``tbl`` (module docstring); ``JobException`` for what must be done
    in Word."""
    grid = grid_columns_of(tbl)
    chosen = set(cols)
    d = ColumnDeletion(tbl=tbl, cols=sorted(chosen))

    def lost(rng, what: str) -> int:
        total = 0
        for g in rng:
            if g in chosen:
                w = column_width(grid[g])
                if w is None:
                    raise JobException(f"{where}: grid column {g} has no width, so the width of {what} cannot be "
                                       f"reduced by it — fix the table in Word", "table-grid", {"table": where})
                total += w
        return total

    def less(el, rng, what: str) -> Optional[int]:
        """The new dxa width of ``el`` (a w:tcW, w:wBefore or w:wAfter) once the chosen columns of ``rng`` go; None
        when it stays as it is."""
        if el is None:
            return None
        if el.get(W + "type") == "pct":
            d.pct_cells += 1
            return None
        w = _int(el.get(W + "w"))
        if el.get(W + "type") != "dxa" or w is None:
            return None
        left = w - lost(rng, what)
        if left <= 0:
            raise JobException(f"{where}: the width of {what} ({w} twips) does not cover the columns that go, so it "
                               f"would not stay above 0 — fix the table's widths in Word", "table-grid", {"table": where})
        return left

    for r, tr in enumerate(own_rows(tbl)):
        lay = row_layout(tr)
        kept = 0
        for start, span, tc in lay.cells:
            k = sum(1 for g in covers(start, span) if g in chosen)
            if k == span:
                d.gone.append(tc)
                continue
            kept += 1
            if k:
                d.spans.append((tc, span - k, less(tc.find(f"{W}tcPr/{W}tcW"), covers(start, span),
                                                   f"the cell at row {r}, column {start}")))
        if not kept:
            raise JobException(f"{where}: deleting columns {d.cols} would leave row {r} without a cell (its other "
                               f"columns are empty space) — delete the row first", "table-grid", {"table": where})
        for side, n, first in (("Before", lay.before, 0), ("After", lay.after, lay.end)):
            rng = range(first, first + n)
            k = sum(1 for g in rng if g in chosen)
            if k:
                width = None if k == n else less(tr.find(f"{W}trPr/{W}w{side}"), rng, f"the space {side.lower()} row "
                                                                                       f"{r}'s cells")
                d.edges.append((tr, side, n - k, width))
    gone = set(d.gone)
    rows = own_rows(tbl)
    for c, rs in vertical_merges(tbl):
        fates = {next(tc for start, _s, tc in row_layout(rows[r]).cells if start == c) in gone for r in rs}
        if len(fates) > 1:
            raise JobException(f"{where}: deleting columns {d.cols} would take some cells of the cell merged down from "
                               f"row {rs[0]} and keep others — unmerge it in Word first", "vertical-merge",
                               {"table": where, "merge_start": rs[0]})
    tblw = tbl.find(f"{W}tblPr/{W}tblW")
    if tblw is not None and tblw.get(W + "type") == "pct":
        d.pct_table = True
    elif tblw is not None and tblw.get(W + "type") == "dxa" and (_int(tblw.get(W + "w")) or 0) > 0:
        d.width = less(tblw, range(len(grid)), "the table")
    return d


def reshape(d: ColumnDeletion) -> None:
    """The deletion's spans, row edges, grid and table width written (the cells that go are ``table_edit.remove``'s)."""
    for tc, span, width in d.spans:
        gs = tc.find(f"{W}tcPr/{W}gridSpan")
        if span == 1:
            gs.getparent().remove(gs)
        else:
            gs.set(W + "val", str(span))
        if width is not None:
            tc.find(f"{W}tcPr/{W}tcW").set(W + "w", str(width))
    for tr, side, n, width in d.edges:
        trpr = tr.find(W + "trPr")
        count, w = trpr.find(W + "grid" + side), trpr.find(W + "w" + side)
        if n == 0:
            trpr.remove(count)
            if w is not None:
                trpr.remove(w)
            if not len(trpr) and not trpr.attrib:
                tr.remove(trpr)
            continue
        count.set(W + "val", str(n))
        if width is not None:
            w.set(W + "w", str(width))
    grid = grid_columns_of(d.tbl)
    for g in d.cols:
        grid[g].getparent().remove(grid[g])
    if d.width is not None:
        d.tbl.find(f"{W}tblPr/{W}tblW").set(W + "w", str(d.width))


@register
class DeleteTableColumns(Op):
    name = "delete_table_columns"
    mode = "untracked"
    description = ("Delete the grid columns the column selector chooses (by number, or by the header text of the first "
                   "row) in the chosen tables: their cells go with their text, a cell spanning a deleted and a kept "
                   "column narrows, and a fixed table width shrinks by the deleted columns. Untracked only (Word cannot "
                   "track it); deleting every column is refused — delete the table instead.")
    params_schema = {"type": "object", "properties": dict(UNTRACKED_PARAMS)}
    select_schema = {"type": "object", "properties": {"scope": SCOPE_SCHEMA, "tables": TABLES_SCHEMA,
                                                      "columns": COLUMNS_SCHEMA}, "required": ["columns"]}
    verification_profile = {
        "V2": "every paragraph of a deleted cell listed as deleted",
        "V4": "−(the revisions inside the deleted cells)",
        "V5b": "enumerated-list mode (untracked)",
        "V10": "deleted_table_columns: the tables, the columns and every decision re-derived from the source; each "
               "changed part compared whole with the source as the verifier deletes the columns itself",
        "V7": "none — the table's layout changes by design",
    }

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        place = "of " + prose.where({k: v for k, v in select.items() if k not in ("rows", "columns")},
                                    default="every table")[3:]
        first = (f"Delete {column_words(select.get('columns'))} {place}, with their cells and text, without tracked "
                 f"changes.")
        extra = ["A cell spanning a deleted column narrows instead, and a fixed table width shrinks with the columns"]
        if params.get("allow_missing"):
            extra.append("files with no such column are left unchanged")
        return prose.sentence(first, "; ".join(extra))

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        raw_select = dict(select or {})
        unknown = set(raw_select) - {"scope", "tables", "columns"}
        if unknown:
            raise JobException(f"delete_table_columns: unknown selector key(s) {sorted(unknown)}", "bad-params")
        allow = untracked_only(dict(params or {}), self.name, "deleting table columns")
        pred = raw_select.get("tables", "all")
        check_table_selector(pred)
        cols_pred = raw_select.get("columns")
        if cols_pred is None:
            raise JobException("say which columns to delete (select.columns: their numbers or their header text); "
                               "delete_tables deletes whole tables", "bad-params")
        check_column_selector(cols_pred)
        parts = scope_parts(pkg, raw_select.get("scope", ["document"]))
        sheet = StyleSheet(pkg)
        frag = self.fragment(raw_select, {**params, "tracked": False})
        frag.untracked_diffs_expected = True
        targets: Dict[str, List[list]] = {}
        boxed: List[str] = []
        n_tables = n_cols = pictures = pct_cells = 0
        pct_tables: List[str] = []
        references: List[str] = []
        for part in parts:
            root = pkg.xml(part)
            frag.touches += sorted(selection_reads(part, root, pred))
            frag.touches.append((part, "scope", "verify"))
            index = {p: i for i, p in enumerate(root.iter(W + "p"))}
            items: List[list] = []
            work: List[ColumnDeletion] = []
            going: Set[Any] = set()
            for k, tbl in enumerate(root.iter(W + "tbl")):
                if not table_selected(pred, tbl, k, sheet):
                    continue
                if any(a in going for a in tbl.iterancestors(W + "tc")):
                    continue                                  # goes with the cell around it
                where = f"{part} table {k}"
                if in_text_box(tbl):
                    if selects_all(pred):
                        boxed.append(where)
                        continue
                    raise JobException(f"{where} lies in a text box (Word keeps two copies of a text box)", "textbox",
                                       {"table": where})
                check_numbered(tbl, where)
                frag.touches += sorted(column_selection_reads(part, tbl, cols_pred, index))
                cols = selected_columns(tbl, cols_pred)
                if not cols:
                    continue
                if len(cols) == len(grid_columns_of(tbl)):
                    raise JobException(f"{where}: every column of the table is chosen — delete the table instead "
                                       f"(delete_tables)", "every-column", {"table": where})
                if legacy_merges([tc for tr in own_rows(tbl) for tc in own_cells(tr)]):
                    raise JobException(f"{where} has cells joined by Word's legacy horizontal merge (w:hMerge); merge "
                                       f"them again in Word first", "table-grid", {"table": where})
                pending = pending_table_changes(tbl)
                if pending:
                    raise JobException(f"{where} carries pending tracked changes of its structure or properties "
                                       f"({', '.join('w:' + n for n in pending)}); accept or reject them first",
                                       "existing-revisions", {"table": where})
                d = deletion(tbl, cols, where)
                going |= set(d.gone)
                items.append([k, d.cols])
                work.append(d)
                pct_cells += d.pct_cells
                if d.pct_table:
                    pct_tables.append(where)
            if not items:
                continue
            gone = [tc for d in work for tc in d.gone]
            rem = removal(root, gone, False, f"{part} table(s) {[k for k, _ in items]}")
            for bi, text in removed_paragraphs(gone, index):
                frag.diffs.append(BlockDiff(part=part, block_index=bi, before=text, after=None,
                                            note="paragraph of a deleted table column's cell"))
            for kind, v in removal_revisions(rem).items():
                frag.rev_delta[kind] = frag.rev_delta.get(kind, 0) - v
            references += rem.references
            pictures += rem.pictures
            n_tables += len(items)
            n_cols += sum(len(cols) for _, cols in items)
            targets[part] = items
            frag.intended_parts.append(part)
            frag.touches.append((part, "*", "text"))
        frag.property_assertions.append({
            "kind": "deleted_table_columns", "parts": parts, "select": raw_select,
            "params": {k: params[k] for k in sorted(UNTRACKED_PARAMS) if k in params}, "expected_tables": n_tables,
            "expected_columns": n_cols, "changed": targets})
        if boxed:
            frag.flags.append(f"{len(boxed)} table(s) in a text box left alone (Word keeps two copies of a text box): "
                              f"{boxed}")
        if not n_cols:
            if allow:
                frag.notes.append("delete_table_columns: no column to delete in this file; no change.")
                return frag
            raise JobException(f"no column matches {cols_pred!r} in the tables {pred!r}", "not-found",
                               {"tables": pred, "columns": cols_pred})
        frag.flags.append(f"UNTRACKED deletion of {n_cols} table column(s) (Word cannot track it).")
        if pct_tables:
            frag.flags.append(f"{len(pct_tables)} table(s) keep their width in percent of the text; Word widens the "
                              f"columns left to fill it: {pct_tables}")
        if pct_cells:
            frag.flags.append(f"{pct_cells} cell or row width(s) given in percent are left as they are; Word refits "
                              f"them.")
        if pictures:
            frag.flags.append(f"{pictures} picture(s) go with the deleted cells; their image files stay in the package "
                              f"until Word next saves it.")
        if references:
            frag.flags.append(f"a cross-reference elsewhere points at a bookmark inside a deleted cell ({references}); "
                              f"Word shows an error there when it updates fields.")
        frag.geometry_reference, frag.geometry_reason = "none", REFLOW_REASON
        frag.transform_data = {"targets": targets}
        frag.notes.append(f"delete_table_columns: {n_cols} column(s) of {n_tables} table(s) (untracked)")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        for part, items in (frag.transform_data or {}).get("targets", {}).items():
            root = pkg.xml(part)
            tables = list(root.iter(W + "tbl"))
            work = [deletion(tables[k], cols, f"{part} table {k}") for k, cols in items]
            rem = removal(root, [tc for d in work for tc in d.gone], False, f"{part} table(s) {[k for k, _ in items]}")
            for d in work:
                reshape(d)
            remove(rem)
            pkg.write_xml(part, root)
