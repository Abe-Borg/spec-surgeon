"""Op: delete_tables — delete whole tables (built-in batch 6, F5 — docs/BUILTIN_OPS_BATCH6_DESIGN.md §6).

The tables are chosen by the table selector (``ops/table_select.py``: ``select.scope``, default the body, and
``select.tables``, default every table). A table nested in another chosen table goes with it.

* **Tracked** (default, content mode): every row of the table (nested tables' rows too) is marked deleted, every
  paragraph's mark deleted and its runs deleted — as Word records a tracked table deletion (``table_edit.mark_deleted``);
  the table goes when the deletion is accepted.
* **Untracked** (``tracked: false``): the ``w:tbl`` goes (``table_edit.remove``).

What the deleted content may hold, what a crossing bookmark does, and when a table cannot leave its container are
``table_edit``'s rules (refusals: ``section-break``, ``table-anchor``, ``existing-revisions``, ``structure``,
``textbox``, ``table-structure``). A table in a text box is left alone and flagged when the selection is every table,
refused (``textbox``) when the selection names tables. No table matching → ``not-found`` unless ``allow_missing``.

Verified by V10's ``deleted_tables`` kind (verify/tablestructure.py), which re-derives the selection and every decision
from the source with its own code and compares each changed part whole.
"""
from __future__ import annotations

import getpass
import os
from typing import Any, Dict, List, Optional

from ..ooxml import file_uses_date_utc, max_revision_id
from ..ooxml import RevisionStamp
from ..package import W, DocxPackage
from . import prose, register
from .base import BlockDiff, JobException, Op, PlanFragment
from .selectors import StyleSheet, scope_parts
from .table_edit import (deletion_counts, mark_deleted, removal, removal_revisions, remove, removed_paragraphs,
                         table_removal_problem)
from .table_select import check_table_selector, in_text_box, selection_reads, selects_all, table_selected

SCOPE_SCHEMA = {"type": "array", "items": {"type": "string", "enum": ["document", "headers", "footers", "footnotes",
                                                                       "endnotes", "comments"]}, "default": ["document"]}
TABLES_SCHEMA = {"description": "'all' (default) or {index_range: [a, b] (table ordinals in the part, both inclusive, "
                                "0-based, nested tables counted), style_in: [table style ids], first_row_regex, "
                                "text_regex, nested: bool} — every given key must hold"}
TRACKING_PARAMS = {
    "tracked": {"type": "boolean", "default": True,
                "description": "true = tracked deletion, as Word records it (content mode); false = removed outright"},
    "author": {"type": "string", "description": "Tracked-change author (default: SPEC_SURGEON_AUTHOR or the OS user)."},
    "allow_missing": {"type": "boolean", "default": False,
                      "description": "true = a file with nothing to change is left as it is instead of set aside"},
    "date_convention": {"type": "string", "enum": ["word-local-z", "utc"], "default": "word-local-z"},
}
REFLOW_REASON = "deleting tables reflows the content after them"


def default_author() -> str:
    return os.environ.get("SPEC_SURGEON_AUTHOR") or getpass.getuser()


def tracking(params: Dict[str, Any], known: set, name: str):
    """(tracked, author, allow_missing, date convention) — ``bad-params`` for an unknown or wrong one."""
    unknown = set(params) - known
    if unknown:
        raise JobException(f"{name}: unknown parameter(s) {sorted(unknown)}; known: {sorted(known)}", "bad-params")
    tracked = params.get("tracked", True)
    if not isinstance(tracked, bool):
        raise JobException("'tracked' must be true or false", "bad-params")
    allow = params.get("allow_missing", False)
    if not isinstance(allow, bool):
        raise JobException("allow_missing must be true or false", "bad-params")
    convention = params.get("date_convention", "word-local-z")
    if convention not in ("word-local-z", "utc"):
        raise JobException("date_convention must be 'word-local-z' or 'utc'", "bad-params")
    author = params.get("author") or default_author()
    if not isinstance(author, str) or not author.strip():
        raise JobException("author must be a name", "bad-params")
    return tracked, author, allow, convention


def tracked_words(params: Dict[str, Any], what: str = "shown as tracked changes") -> str:
    if params.get("tracked", True) is False:
        return "removing them outright without tracked changes"
    return what + (f" by {prose.quote(params['author'])}" if params.get("author") else "")


@register
class DeleteTables(Op):
    name = "delete_tables"
    mode = "content"
    description = ("Delete whole tables chosen by the table selector — tracked by default (every row and its content "
                   "marked deleted, as Word records it), or removed outright with tracked false. A table nested in a "
                   "chosen one goes with it; bookmarks crossing the table's edge keep their place.")
    params_schema = {"type": "object", "properties": dict(TRACKING_PARAMS)}
    select_schema = {"type": "object", "properties": {"scope": SCOPE_SCHEMA, "tables": TABLES_SCHEMA}}
    verification_profile = {
        "V2": "untracked: every paragraph of a deleted table listed as deleted; tracked: no text change (w:delText)",
        "V4": "tracked: +1 del per row, +1 del_mark per paragraph, +1 del per run group; untracked: −(revisions inside)",
        "V5b": "tracked: reject-all view equals the source; untracked: enumerated-list mode",
        "V8": "tracked: the accept-all view of every paragraph of a deleted table is empty",
        "V10": "deleted_tables: the selection and every decision re-derived from the source; each changed part compared "
               "whole with the source as the verifier deletes the tables itself",
    }

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        subject = prose.where(select, default="every table")[3:]
        first = f"Delete {subject}, {tracked_words(params)}."
        second = "Files with no such table are left unchanged." if params.get("allow_missing") else ""
        return prose.sentence(first, second)

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        raw_select = dict(select or {})
        unknown = set(raw_select) - {"scope", "tables"}
        if unknown:
            raise JobException(f"delete_tables: unknown selector key(s) {sorted(unknown)}", "bad-params")
        tracked, author, allow, convention = tracking(dict(params or {}), set(TRACKING_PARAMS), self.name)
        pred = raw_select.get("tables", "all")
        check_table_selector(pred)
        parts = scope_parts(pkg, raw_select.get("scope", ["document"]))
        sheet = StyleSheet(pkg)
        frag = self.fragment(raw_select, {**params, "tracked": tracked, "author": author},
                             mode="content" if tracked else "untracked")
        frag.untracked_diffs_expected = not tracked
        targets: Dict[str, List[int]] = {}
        boxed: List[str] = []
        total = rows = pars = groups = pictures = 0
        references: List[str] = []
        for part in parts:
            root = pkg.xml(part)
            frag.touches += sorted(selection_reads(part, root, pred))
            frag.touches.append((part, "scope", "verify"))          # V10 re-resolves the selection over this part
            tables = list(root.iter(W + "tbl"))
            chosen = []
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
                chosen.append(k)
            held = {tables[k] for k in chosen}
            outer = [k for k in chosen if not any(a in held for a in tables[k].iterancestors(W + "tbl"))]
            if not outer:
                continue
            gone = [tables[k] for k in outer]
            where = f"{part} table(s) {outer}"
            rem = removal(root, gone, tracked, where)
            if not tracked:
                for container in {g.getparent() for g in gone}:
                    why = table_removal_problem(container, {g for g in gone if g.getparent() is container})
                    if why:
                        raise JobException(f"{part}: deleting a table there cannot be done safely — {why}; delete it in "
                                           f"Word", "table-structure", {"part": part, "tables": outer})
            index = {p: i for i, p in enumerate(root.iter(W + "p"))}
            if tracked:
                r, p, g = deletion_counts(gone)
                rows, pars, groups = rows + r, pars + p, groups + g
                frag.accepted_views += [{"part": part, "block_index": bi, "accepted_view": ""}
                                        for bi, _ in removed_paragraphs(gone, index)]
            else:
                for bi, text in removed_paragraphs(gone, index):
                    frag.diffs.append(BlockDiff(part=part, block_index=bi, before=text, after=None,
                                                note="paragraph of a deleted table"))
                for k, v in removal_revisions(rem).items():
                    frag.rev_delta[k] = frag.rev_delta.get(k, 0) - v
                references += rem.references
            pictures += rem.pictures
            total += len(outer)
            targets[part] = outer
            frag.intended_parts.append(part)
            frag.touches.append((part, "*", "text"))                # blocks leave (or are marked in) the part
        frag.property_assertions.append({
            "kind": "deleted_tables", "parts": parts, "select": raw_select, "tracked": tracked, "author": author,
            "expected_tables": total, "changed": targets})
        if boxed:
            frag.flags.append(f"{len(boxed)} table(s) in a text box left alone (Word keeps two copies of a text box): "
                              f"{boxed}")
        if not total:
            if allow:
                frag.notes.append(f"delete_tables: no table to delete in {raw_select.get('scope', ['document'])}; "
                                  f"no change.")
                return frag
            raise JobException(f"no table matches {pred!r} in {raw_select.get('scope', ['document'])}", "not-found",
                               {"tables": pred})
        if tracked:
            frag.rev_delta["del"] = frag.rev_delta.get("del", 0) + rows + groups
            frag.rev_delta["del_mark"] = frag.rev_delta.get("del_mark", 0) + pars
            frag.rev_id_base = max_revision_id(pkg) + 1
            frag.rev_ids_needed = rows + pars + groups
        else:
            frag.flags.append(f"UNTRACKED deletion of {total} table(s) at the user's request.")
            if pictures:
                frag.flags.append(f"{pictures} picture(s) go with the deleted tables; their image files stay in the "
                                  f"package until Word next saves it.")
            if references:
                frag.flags.append(f"a cross-reference elsewhere points at a bookmark inside a deleted table "
                                  f"({references}); Word shows an error there when it updates fields.")
        frag.geometry_reference, frag.geometry_reason = "none", REFLOW_REASON
        frag.transform_data = {"targets": targets, "tracked": tracked, "author": author,
                               "use_date_utc": file_uses_date_utc(pkg), "first_rev_id": frag.rev_id_base,
                               "date_convention": convention}
        frag.notes.append(f"delete_tables: {total} table(s) ({'tracked' if tracked else 'untracked'}) in "
                          f"{len(targets)} part(s)" + (f": {rows} row(s), {pars} paragraph(s) marked deleted"
                                                       if tracked else ""))
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td or not td.get("targets"):
            return
        stamp = RevisionStamp.now(td["author"], td["use_date_utc"], td.get("date_convention", "word-local-z"))
        next_id = [td["first_rev_id"]]
        for part, ordinals in td["targets"].items():
            root = pkg.xml(part)
            tables = list(root.iter(W + "tbl"))
            gone = [tables[k] for k in ordinals]
            if td["tracked"]:
                mark_deleted(gone, stamp, next_id)
            else:
                remove(removal(root, gone, False, f"{part} table(s) {ordinals}"))
            pkg.write_xml(part, root)
