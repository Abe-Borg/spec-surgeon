"""Op: insert_table_rows — add rows holding the user's words to the chosen tables (built-in batch 6, F5 —
docs/BUILTIN_OPS_BATCH6_DESIGN.md §6).

Parameters: ``rows`` — ``[[cell text, …], …]``, the user's words verbatim, one text per cell of the reference row (an
empty text is an empty cell; no tab, line break or other control character — one plain paragraph per cell);
``position`` — ``after`` (default) or ``before`` the one row the row selector (``select.rows``) chooses in each table,
or ``at_end`` (after the table's last own row; ``select.rows`` is then not given); ``repeat_header`` (default false) —
the new rows repeat at the top of each page as header rows; ``tracked`` (default true), ``author``, ``allow_missing``,
``date_convention``.

Each new row copies the **reference row** (the chosen row, or the last row at the end): its row, cell, first paragraph
and first run formatting (``table_edit.new_row``), never its header-row marking unless ``repeat_header``. Tracked, as
Word records an inserted row: the row's ``w:trPr`` carries a ``w:ins``, each cell paragraph's mark a ``w:ins`` and its
text a ``w:ins`` run.

Per selected table (in this order): a table in a text box is left alone under an every-table selection (flagged),
refused when named (``textbox``); rows in a content control or custom XML, a table without rows, a reference row with
cells in a content control or a cell without a paragraph → ``table-structure``; no row chosen → the table is left alone
(noted); more than one row chosen → ``ambiguous`` (say which one); a reference row with merged cells (a cell spanning
columns, or merged across rows) or a typed row whose number of texts is not the reference row's number of cells →
``table-shape``; the **header rows** (``header-rows``) — Word and LibreOffice repeat only the header rows that run on
from the table's first row (``table_rows.header_run``, h rows) — the new rows land after ``i`` own rows (before row r:
r; after it: r + 1; at the end: every row): without ``repeat_header`` they may not land before or inside that run
(i < h: they would split it), with it they must land at or inside it or right after its last row (i ≤ h: they join it;
further down Word ignores the marking). Nothing to insert anywhere → ``not-found`` unless ``allow_missing``.

Verified by V10's ``inserted_table_rows`` kind (verify/tablestructure.py), which builds the new rows itself from the
source and compares each changed part whole.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..ooxml import RevisionStamp, file_uses_date_utc, max_revision_id
from ..package import W, DocxPackage
from . import prose, register
from .base import BlockDiff, JobException, Op, PlanFragment
from .delete_table_rows import row_words, table_place
from .delete_tables import SCOPE_SCHEMA, TABLES_SCHEMA, TRACKING_PARAMS, tracking
from .selectors import StyleSheet, scope_parts
from .table_edit import cell_text, insertion_counts, new_row
from .table_rows import (ROWS_SCHEMA, cell_paragraphs, check_row_selector, header_run, is_merged,
                         row_selection_reads, selected_rows)
from .table_select import (cells_wrapped, check_table_selector, in_text_box, own_cells, own_rows, rows_wrapped,
                           selection_reads, selects_all, style_chain, style_chain_reads, table_selected)

POSITIONS = ("after", "before", "at_end")
PARAMS = {
    "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}},
             "description": "the new rows, each a list of its cells' texts — the user's words verbatim, one per cell of "
                            "the row they copy ('' for an empty cell)"},
    "position": {"type": "string", "enum": list(POSITIONS), "default": "after",
                 "description": "after / before the one row select.rows chooses in each table, or at_end"},
    "repeat_header": {"type": "boolean", "default": False,
                      "description": "true: the new rows repeat at the top of each page as header rows"},
    **TRACKING_PARAMS,
}
REFLOW_REASON = "new table rows reflow the content after them"


def typed_rows(raw: Any, code: str = "bad-params") -> List[List[str]]:
    """The rows as typed: a non-empty list of non-empty lists of cell texts (``table_edit.cell_text``)."""
    if not isinstance(raw, list) or not raw or not all(isinstance(r, list) and r for r in raw):
        raise JobException("rows must be a non-empty list of rows, each a non-empty list of cell texts", code)
    return [[cell_text(t, f"row {i + 1} cell {k + 1}", code) for k, t in enumerate(r)] for i, r in enumerate(raw)]


def cells_words(cells: List[Any]) -> str:
    """``the cells “FP-2” and “Jockey pump”``; an empty text reads as an empty cell."""
    shown = [prose.quote(c) if isinstance(c, str) and c else "an empty cell" for c in cells]
    return ("the cell " if len(shown) == 1 else "the cells ") + prose.join(shown)


def rows_words(rows: Any) -> str:
    """``a row with the cells “A” and “B”`` / ``2 rows, with the cells “A” and “B”; and the cells “C” and “D”,``."""
    if not isinstance(rows, list) or not rows or not all(isinstance(r, list) for r in rows):
        return "rows with the approved wording"
    if len(rows) == 1:
        return "a row with " + cells_words(rows[0])
    listed = [cells_words(r) for r in rows[:3]]
    if len(rows) > 3:
        listed.append(f"{len(rows) - 3} more")
    return f"{len(rows)} rows, with " + "; ".join(listed[:-1]) + "; and " + listed[-1] + ","


def insertion_point(index: Dict[Any, int], ref, position: str) -> int:
    """How many of the part's source paragraphs come before the new rows."""
    pars = list(ref.iter(W + "p"))
    return index[pars[0]] if position == "before" else index[pars[-1]] + 1


@register
class InsertTableRows(Op):
    name = "insert_table_rows"
    mode = "content"
    description = ("Add rows holding the user's words verbatim (one text per cell) after or before the one row the "
                   "row selector chooses in each chosen table, or at its end; each new row copies that row's row, cell, "
                   "paragraph and text formatting (never its header-row marking unless asked). Tracked by default "
                   "(rows and their text inserted, as Word records it), or untracked with tracked false.")
    params_schema = {"type": "object", "properties": PARAMS, "required": ["rows"]}
    select_schema = {"type": "object", "properties": {"scope": SCOPE_SCHEMA, "tables": TABLES_SCHEMA,
                                                      "rows": ROWS_SCHEMA}}
    verification_profile = {
        "V2": "one inserted block per new cell paragraph (before=None, after=its text) at its output position",
        "V4": "tracked: +1 ins per row, +1 ins_mark per cell paragraph, +1 ins per cell with text; untracked: 0",
        "V5b": "tracked: reject-all view equals the source (the inserted marks merge the empty rejected paragraphs "
               "away); untracked: enumerated-list mode",
        "V8": "tracked: the accept-all view holds every new cell's text at its exact output position",
        "V10": "inserted_table_rows: the tables, the reference rows and the new rows' every element re-derived from "
               "the source; each changed part compared whole",
    }

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        position = params.get("position") if params.get("position") in POSITIONS else "after"
        if position == "at_end":
            where, like = f"at the end {table_place(select)}", "its last row"
        else:
            where, like = f"{position} {row_words(select.get('rows'), one=True)} {table_place(select)}", "that row"
        clauses = [f"formatted like {like}"]
        if params.get("repeat_header") is True:
            clauses.append("repeated at the top of each page as header rows")
        if params.get("tracked", True) is False:
            clauses.append("without tracked changes")
        else:
            clauses.append("shown as a tracked change" + (f" by {prose.quote(params['author'])}"
                                                          if params.get("author") else ""))
        first = f"Add {rows_words(params.get('rows'))} {where}, " + ", ".join(clauses) + "."
        second = "Files with no such table are left unchanged." if params.get("allow_missing") else ""
        return prose.sentence(first, second)

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        raw_select, params = dict(select or {}), dict(params or {})
        unknown = set(raw_select) - {"scope", "tables", "rows"}
        if unknown:
            raise JobException(f"insert_table_rows: unknown selector key(s) {sorted(unknown)}", "bad-params")
        tracked, author, allow, convention = tracking({k: v for k, v in params.items()
                                                       if k not in ("rows", "position", "repeat_header")},
                                                      set(TRACKING_PARAMS), self.name)
        texts = typed_rows(params.get("rows"))
        position = params.get("position", "after")
        if position not in POSITIONS:
            raise JobException(f"position must be one of {list(POSITIONS)}, got {position!r}", "bad-params")
        repeat = params.get("repeat_header", False)
        if not isinstance(repeat, bool):
            raise JobException("repeat_header must be true or false", "bad-params")
        pred = raw_select.get("tables", "all")
        check_table_selector(pred)
        rows_pred = raw_select.get("rows")
        if position == "at_end":
            if rows_pred is not None:
                raise JobException("at_end adds the rows after each table's last row: leave select.rows out", "bad-params")
        else:
            if rows_pred is None:
                raise JobException(f"say which row the new rows go {position} (select.rows), or use position at_end",
                                   "bad-params")
            check_row_selector(rows_pred)
        parts = scope_parts(pkg, raw_select.get("scope", ["document"]))
        sheet = StyleSheet(pkg)
        frag = self.fragment(raw_select, {**params, "position": position, "repeat_header": repeat,
                                          "tracked": tracked, "author": author},
                             mode="content" if tracked else "untracked")
        frag.untracked_diffs_expected = not tracked
        targets: Dict[str, List[List[int]]] = {}
        boxed: List[str] = []
        unmatched: List[str] = []
        total = 0
        for part in parts:
            root = pkg.xml(part)
            frag.touches += sorted(selection_reads(part, root, pred))
            frag.touches.append((part, "scope", "verify"))
            index = {p: i for i, p in enumerate(root.iter(W + "p"))}
            points: List[Tuple[int, int, int, int]] = []          # (paragraphs before, depth, table, reference row)
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
                rows = own_rows(tbl)
                if rows_wrapped(tbl) or not rows:
                    raise JobException(f"{where} has " + ("rows inside a content control or custom XML; its rows cannot "
                                       "be numbered safely" if rows else "no rows"), "table-structure", {"table": where})
                chain, _missing = style_chain(tbl, sheet)
                frag.touches += sorted(style_chain_reads(sheet, tbl))      # the header-row rule reads the chain
                if position == "at_end":
                    r = len(rows) - 1
                else:
                    frag.touches += sorted(row_selection_reads(part, k, tbl, rows_pred, index))
                    chosen = selected_rows(tbl, rows_pred, chain)
                    if not chosen:
                        unmatched.append(where)
                        continue
                    if len(chosen) > 1:
                        raise JobException(f"{where}: the row selection chooses {len(chosen)} rows ({chosen}); the new "
                                           f"rows go {position} one row — narrow the selection to one row per table",
                                           "ambiguous", {"table": where, "rows": chosen})
                    r = chosen[0]
                ref = rows[r]
                cells = own_cells(ref)
                if cells_wrapped(ref) or not cells or any(not cell_paragraphs(tc) for tc in cells):
                    raise JobException(f"{where} row {r} has cells inside a content control or custom XML, no cells, or "
                                       f"a cell without a paragraph", "table-structure", {"table": where, "row": r})
                if any(is_merged(tc) for tc in cells):
                    raise JobException(f"{where} row {r} has merged cells (a cell spanning columns or merged across rows); "
                                       f"new rows cannot copy it — choose a row without merged cells", "table-shape",
                                       {"table": where, "row": r})
                bad = [i + 1 for i, row in enumerate(texts) if len(row) != len(cells)]
                if bad:
                    raise JobException(f"{where} row {r} has {len(cells)} cell(s); new row(s) {bad} give "
                                       f"{[len(texts[i - 1]) for i in bad]} text(s) — give one text per cell",
                                       "table-shape", {"table": where, "cells": len(cells)})
                head = header_run(tbl, chain)                  # read: each row's marking down to the first body row
                frag.touches += [(part, f"tr:{k}:{i}", "verify-props:tblHeader")
                                 for i in range(min(head + 1, len(rows)))]
                lands = r if position == "before" else r + 1   # own rows before the new ones
                if not repeat and lands < head:
                    raise JobException(f"{where}: the new rows would split the header rows that repeat on every page "
                                       f"(its first {head} row(s)): ask for them as header rows (repeat_header), or "
                                       f"put them after the header rows", "header-rows",
                                       {"table": where, "header_rows": head})
                if repeat and lands > head:
                    top = f"its first {head} row(s)" if head else "it has none"
                    raise JobException(f"{where}: the new rows would not join the header rows at the top of the table "
                                       f"({top}), and Word repeats only those — put them at the top or right after the "
                                       f"header rows, or leave repeat_header out", "header-rows",
                                       {"table": where, "header_rows": head})
                depth = sum(1 for _ in tbl.iterancestors(W + "tbl"))
                before = insertion_point(index, ref, "before" if position == "before" else "after")
                points.append((before, depth if position == "before" else -depth, k, r))
            if not points:
                continue
            points.sort()
            offset = 0
            for before, _depth, k, r in points:
                at = before + offset
                for row in texts:
                    for text in row:
                        frag.diffs.append(BlockDiff(part=part, block_index=at, before=None, after=text,
                                                    accepted_view=text if tracked else None,
                                                    note=f"new row {position} table {k} row {r}"))
                        at += 1
                offset += sum(len(row) for row in texts)
            targets[part] = [[k, r] for _, _, k, r in points]
            total += len(points)
            frag.intended_parts.append(part)
            frag.touches.append((part, "*", "text"))
        frag.property_assertions.append({
            "kind": "inserted_table_rows", "parts": parts, "select": raw_select,
            "params": {"rows": texts, "position": position, "repeat_header": repeat}, "tracked": tracked,
            "author": author, "expected_tables": total, "changed": targets})
        if boxed:
            frag.flags.append(f"{len(boxed)} table(s) in a text box left alone (Word keeps two copies of a text box): "
                              f"{boxed}")
        if unmatched:
            frag.notes.append(f"no row chosen, left alone: {unmatched}")
        if not total:
            if allow:
                frag.notes.append("insert_table_rows: no table to add rows to in this file; no change.")
                return frag
            raise JobException(f"no table {pred!r} has a row {rows_pred!r} to add rows {position}"
                               if position != "at_end" else f"no table matches {pred!r}", "not-found",
                               {"tables": pred, "rows": rows_pred})
        rows_n, pars_n, filled = insertion_counts(texts)
        if tracked:
            frag.rev_delta["ins"] = frag.rev_delta.get("ins", 0) + total * (rows_n + filled)
            frag.rev_delta["ins_mark"] = frag.rev_delta.get("ins_mark", 0) + total * pars_n
            frag.rev_id_base = max_revision_id(pkg) + 1
            frag.rev_ids_needed = total * (rows_n + pars_n + filled)
        else:
            frag.flags.append(f"UNTRACKED insertion of {total * rows_n} table row(s) at the user's request.")
        frag.geometry_reference, frag.geometry_reason = "none", REFLOW_REASON
        frag.transform_data = {"targets": targets, "rows": texts, "position": position, "repeat_header": repeat,
                               "tracked": tracked, "author": author, "use_date_utc": file_uses_date_utc(pkg),
                               "first_rev_id": frag.rev_id_base, "date_convention": convention}
        frag.notes.append(f"insert_table_rows: {rows_n} row(s) {position.replace('_', ' ')} in {total} table(s) "
                          f"({'tracked' if tracked else 'untracked'})")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td or not td.get("targets"):
            return
        tracked = td["tracked"]
        stamp = RevisionStamp.now(td["author"], td["use_date_utc"], td.get("date_convention", "word-local-z"))
        next_id = [td["first_rev_id"]]
        sheet = StyleSheet(pkg)
        for part, items in td["targets"].items():
            root = pkg.xml(part)
            tables = list(root.iter(W + "tbl"))
            work = []
            for k, r in items:                                   # build every new row from the part as planned first
                tbl = tables[k]
                ref = own_rows(tbl)[r]
                chain, _missing = style_chain(tbl, sheet)
                work.append((ref, [new_row(ref, row, chain, td["repeat_header"], tracked, stamp, next_id)
                                   for row in td["rows"]]))
            for ref, new in work:
                if td["position"] == "before":
                    for tr in new:
                        ref.addprevious(tr)
                else:
                    at = ref
                    for tr in new:
                        at.addnext(tr)
                        at = tr
            pkg.write_xml(part, root)
