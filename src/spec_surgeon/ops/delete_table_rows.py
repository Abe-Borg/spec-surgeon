"""Op: delete_table_rows — delete the rows the row selector chooses in the chosen tables (built-in batch 6, F5 —
docs/BUILTIN_OPS_BATCH6_DESIGN.md §6).

The tables are chosen by the table selector (``select.scope``, ``select.tables``; default every table of the body) and
their rows by the row selector (``select.rows``, required — ``ops/table_rows.py``: index range, the row's text, header
rows). A table whose every own row is chosen goes whole (as delete_tables deletes it). A table no row of which is chosen
is left alone.

* **Tracked** (default, content mode): each chosen row is marked deleted (``w:trPr/w:del``), its paragraphs' marks and
  runs deleted (``table_edit.mark_deleted``).
* **Untracked** (``tracked: false``): the rows go (``table_edit.remove``).

Refused, per table: rows in a content control or custom XML (``table-structure``: they cannot be numbered); a deletion
that takes the first row of a vertical merge and keeps another of its rows (``vertical-merge``: the merged cell's
content is in its first row; ``table_rows.merge_crossings``); and ``table_edit``'s rules over what the rows hold. A
table in a text box is left alone under an every-table selection (flagged), refused when named (``textbox``). Nothing
chosen anywhere → ``not-found`` unless ``allow_missing``.

Verified by V10's ``deleted_table_rows`` kind (verify/tablestructure.py), which re-derives the tables, the rows and every
decision from the source with its own code and compares each changed part whole.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..ooxml import RevisionStamp, file_uses_date_utc, max_revision_id
from ..package import W, DocxPackage
from . import prose, register
from .base import BlockDiff, JobException, Op, PlanFragment
from .delete_tables import SCOPE_SCHEMA, TABLES_SCHEMA, TRACKING_PARAMS, tracked_words, tracking
from .selectors import StyleSheet, scope_parts
from .table_edit import (deletion_counts, mark_deleted, removal, removal_revisions, remove, removed_paragraphs,
                         table_removal_problem)
from .table_rows import ROWS_SCHEMA, check_row_selector, merge_crossings, row_selection_reads, selected_rows
from .table_select import (check_table_selector, in_text_box, own_rows, rows_wrapped, selection_reads, selects_all,
                           style_chain, style_chain_reads, table_selected)

REFLOW_REASON = "deleting table rows reflows the content after them"


def row_words(rows: Any, one: bool = False) -> str:
    """``every row``, ``row 2``, ``rows 2 through 4``, ``the header rows whose text matches the pattern “x”`` — with
    ``one``, the words for the single row an insertion is placed by (``the row whose text matches …``)."""
    if rows in (None, "all", {}) or not isinstance(rows, dict):
        return "every row" if rows in ("all", {}) else ("the chosen row" if one else "the chosen rows")
    noun = "row" if one else "rows"
    head = ""
    if rows.get("header") is True:
        head = f"the header {noun}"
    elif rows.get("header") is False:
        head = "the row that is not a header row" if one else "the rows that are not header rows"
    span = ""
    r = rows.get("index_range")
    if isinstance(r, list) and len(r) == 2 and all(isinstance(x, int) for x in r):
        span = f"row {r[0] + 1}" if r[0] == r[1] else f"rows {r[0] + 1} through {r[1] + 1}"
    text = (" whose text matches the pattern " + prose.quote(rows["text_regex"])) if rows.get("text_regex") else ""
    if head and span:
        return f"{head} among {span}{text}"
    if head or span:
        return (head or span) + text
    return (f"the {noun}" + text) if text else f"the chosen {noun}"


def table_place(select: Dict[str, Any]) -> str:
    """``of every table``, ``of tables whose first row … in the headers``."""
    return "of " + prose.where({k: v for k, v in select.items() if k != "rows"}, default="every table")[3:]


@register
class DeleteTableRows(Op):
    name = "delete_table_rows"
    mode = "content"
    description = ("Delete the rows the row selector chooses (by position, by their text, header rows) in the chosen "
                   "tables — tracked by default (rows marked deleted with their content, as Word records it), or "
                   "removed outright with tracked false. Deleting every row deletes the table; a deletion that cuts a "
                   "vertically merged cell is refused.")
    params_schema = {"type": "object", "properties": dict(TRACKING_PARAMS)}
    select_schema = {"type": "object", "properties": {"scope": SCOPE_SCHEMA, "tables": TABLES_SCHEMA,
                                                      "rows": ROWS_SCHEMA}, "required": ["rows"]}
    verification_profile = {
        "V2": "untracked: every paragraph of a deleted row listed as deleted; tracked: no text change (w:delText)",
        "V4": "tracked: +1 del per row, +1 del_mark per paragraph, +1 del per run group; untracked: −(revisions inside)",
        "V5b": "tracked: reject-all view equals the source; untracked: enumerated-list mode",
        "V8": "tracked: the accept-all view of every paragraph of a deleted row is empty",
        "V10": "deleted_table_rows: the tables, the rows and every decision re-derived from the source; each changed "
               "part compared whole with the source as the verifier deletes the rows itself",
    }

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        first = f"Delete {row_words(select.get('rows'))} {table_place(select)}, {tracked_words(params)}."
        extra = ["A table losing every row is deleted whole"]
        if params.get("allow_missing"):
            extra.append("files with no such row are left unchanged")
        return prose.sentence(first, "; ".join(extra))

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        raw_select = dict(select or {})
        unknown = set(raw_select) - {"scope", "tables", "rows"}
        if unknown:
            raise JobException(f"delete_table_rows: unknown selector key(s) {sorted(unknown)}", "bad-params")
        tracked, author, allow, convention = tracking(dict(params or {}), set(TRACKING_PARAMS), self.name)
        pred = raw_select.get("tables", "all")
        check_table_selector(pred)
        rows_pred = raw_select.get("rows")
        if rows_pred is None:
            raise JobException("say which rows to delete (select.rows: an index range, their text, header rows, or "
                               "'all'); delete_tables deletes whole tables", "bad-params")
        check_row_selector(rows_pred)
        parts = scope_parts(pkg, raw_select.get("scope", ["document"]))
        sheet = StyleSheet(pkg)
        frag = self.fragment(raw_select, {**params, "tracked": tracked, "author": author},
                             mode="content" if tracked else "untracked")
        frag.untracked_diffs_expected = not tracked
        targets: Dict[str, List[list]] = {}
        boxed: List[str] = []
        n_rows = n_tables = whole = rows_marked = pars = groups = pictures = 0
        references: List[str] = []
        for part in parts:
            root = pkg.xml(part)
            frag.touches += sorted(selection_reads(part, root, pred))
            frag.touches.append((part, "scope", "verify"))
            index = {p: i for i, p in enumerate(root.iter(W + "p"))}
            tables = list(root.iter(W + "tbl"))
            chosen: List[tuple] = []
            for k, tbl in enumerate(tables):
                if not table_selected(pred, tbl, k, sheet):
                    continue
                where = f"{part} table {k}"
                if in_text_box(tbl):
                    if selects_all(pred):
                        boxed.append(where)
                        continue
                    raise JobException(f"{where} lies in a text box (Word keeps two copies of a text box)", "textbox",
                                       {"table": where})
                if rows_wrapped(tbl):
                    raise JobException(f"{where} has rows inside a content control or custom XML; its rows cannot be "
                                       f"numbered safely", "table-structure", {"table": where})
                chain, _missing = style_chain(tbl, sheet)
                frag.touches += sorted(row_selection_reads(part, k, tbl, rows_pred, index))
                if isinstance(rows_pred, dict) and "header" in rows_pred:
                    frag.touches += sorted(style_chain_reads(sheet, tbl))
                rs = selected_rows(tbl, rows_pred, chain)
                if rs:
                    chosen.append((k, rs))
            # a table inside a row (or table) that goes goes with it: only the outermost deletions count
            going_rows = {own_rows(tables[k])[r] for k, rs in chosen for r in rs}
            going_tables = {tables[k] for k, rs in chosen if len(rs) == len(own_rows(tables[k]))}
            held = going_rows | going_tables
            items: List[list] = []
            gone: List[Any] = []
            for k, rs in chosen:
                tbl = tables[k]
                if any(a in held for a in tbl.iterancestors()):
                    continue
                cut = merge_crossings(tbl, rs)
                if cut:
                    raise JobException(f"{part} table {k}: deleting rows {rs} would cut the vertically merged cell that "
                                       f"starts in row {cut[0]} (its content is in that row and the merge goes on below) "
                                       f"— delete the whole merged block, or unmerge it in Word first", "vertical-merge",
                                       {"table": f"{part} table {k}", "rows": rs, "merge_start": cut[0]})
                every = tbl in going_tables
                items.append([k, rs, every])
                gone += [tbl] if every else [own_rows(tbl)[r] for r in rs]
            if not items:
                continue
            where = f"{part} table(s) {[k for k, _, _ in items]}"
            rem = removal(root, gone, tracked, where)
            if not tracked:
                wholes = [tables[k] for k, _, every in items if every]
                for container in {t.getparent() for t in wholes}:
                    why = table_removal_problem(container, {t for t in wholes if t.getparent() is container})
                    if why:
                        raise JobException(f"{part}: deleting every row of a table there would delete it, which cannot "
                                           f"be done safely — {why}; delete it in Word", "table-structure",
                                           {"part": part})
            if tracked:
                r, p, g = deletion_counts(gone)
                rows_marked, pars, groups = rows_marked + r, pars + p, groups + g
                frag.accepted_views += [{"part": part, "block_index": bi, "accepted_view": ""}
                                        for bi, _ in removed_paragraphs(gone, index)]
            else:
                for bi, text in removed_paragraphs(gone, index):
                    frag.diffs.append(BlockDiff(part=part, block_index=bi, before=text, after=None,
                                                note="paragraph of a deleted table row"))
                for kind, v in removal_revisions(rem).items():
                    frag.rev_delta[kind] = frag.rev_delta.get(kind, 0) - v
                references += rem.references
            pictures += rem.pictures
            n_tables += len(items)
            n_rows += sum(len(rs) for _, rs, _ in items)
            whole += sum(1 for _, _, every in items if every)
            targets[part] = items
            frag.intended_parts.append(part)
            frag.touches.append((part, "*", "text"))
        frag.property_assertions.append({
            "kind": "deleted_table_rows", "parts": parts, "select": raw_select, "tracked": tracked, "author": author,
            "expected_tables": n_tables, "expected_rows": n_rows, "changed": targets})
        if boxed:
            frag.flags.append(f"{len(boxed)} table(s) in a text box left alone (Word keeps two copies of a text box): "
                              f"{boxed}")
        if not n_rows:
            if allow:
                frag.notes.append("delete_table_rows: no row to delete in this file; no change.")
                return frag
            raise JobException(f"no row matches {rows_pred!r} in the tables {pred!r}", "not-found",
                               {"tables": pred, "rows": rows_pred})
        if tracked:
            frag.rev_delta["del"] = frag.rev_delta.get("del", 0) + rows_marked + groups
            frag.rev_delta["del_mark"] = frag.rev_delta.get("del_mark", 0) + pars
            frag.rev_id_base = max_revision_id(pkg) + 1
            frag.rev_ids_needed = rows_marked + pars + groups
        else:
            frag.flags.append(f"UNTRACKED deletion of {n_rows} table row(s) at the user's request.")
            if pictures:
                frag.flags.append(f"{pictures} picture(s) go with the deleted rows; their image files stay in the "
                                  f"package until Word next saves it.")
            if references:
                frag.flags.append(f"a cross-reference elsewhere points at a bookmark inside a deleted row "
                                  f"({references}); Word shows an error there when it updates fields.")
        if whole:
            frag.flags.append(f"{whole} table(s) lose every row and are deleted whole.")
        frag.geometry_reference, frag.geometry_reason = "none", REFLOW_REASON
        frag.transform_data = {"targets": targets, "tracked": tracked, "author": author,
                               "use_date_utc": file_uses_date_utc(pkg), "first_rev_id": frag.rev_id_base,
                               "date_convention": convention}
        frag.notes.append(f"delete_table_rows: {n_rows} row(s) of {n_tables} table(s) "
                          f"({'tracked' if tracked else 'untracked'})")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td or not td.get("targets"):
            return
        stamp = RevisionStamp.now(td["author"], td["use_date_utc"], td.get("date_convention", "word-local-z"))
        next_id = [td["first_rev_id"]]
        for part, items in td["targets"].items():
            root = pkg.xml(part)
            tables = list(root.iter(W + "tbl"))
            gone = []
            for k, rs, every in items:
                gone += [tables[k]] if every else [own_rows(tables[k])[r] for r in rs]
            if td["tracked"]:
                mark_deleted(gone, stamp, next_id)
            else:
                remove(removal(root, gone, False, f"{part} table(s) {[k for k, _, _ in items]}"))
            pkg.write_xml(part, root)
