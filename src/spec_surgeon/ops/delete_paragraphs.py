"""Op: delete_paragraphs — delete the paragraphs a selection covers ("all", or any paragraph predicate), with the tables
every paragraph of which is selected and what lies wholly inside them: fields, comments, notes, bookmarks and, untracked,
section breaks (docs/BUILTIN_OPS_BATCH6_DESIGN.md §1). A tracked deletion by default (runs in ``w:del``, the paragraph
marks and a deleted table's rows marked deleted), untracked on request. Every rule is ops/deletion.py's; the verifier
derives every decision again with its own code (verify/deletion.py, kind ``deleted_paragraphs``).
"""
from __future__ import annotations

import getpass
import os
from typing import Any, Dict, List, Optional

from ..ooxml import RevisionStamp, block_full_text, count_revisions, file_uses_date_utc, max_revision_id, paragraphs
from ..package import W, DocxPackage
from . import prose, register
from .base import JobException, Op, PlanFragment
from .deletion import (Choice, Removal, choose, dangling_flags, delete_mark, delete_row, delete_runs, held_objects,
                       in_separator, refuse_crossing_fields, refuse_thread_ends, remove_deletion, row_at, run_groups,
                       section_flags, shown, table_rows, tracked_flags, tracked_runs)
from .selectors import SCOPE_KINDS, StyleSheet, check_paragraph_selector, paragraph_selected, scope_parts


def default_author() -> str:
    return os.environ.get("SPEC_SURGEON_AUTHOR") or getpass.getuser()


def objects_flag(choices: List[Choice], tracked: bool) -> Optional[str]:
    """The pictures, embedded objects and whole tables the deletion takes, in plain words (None when there are none):
    deleting paragraphs that look empty must not take a logo or a table of pictures unremarked."""
    pictures, objects = held_objects(p for ch in choices for p in ch.deleted + ch.kept + ch.in_tables)
    tables = sum(len(ch.tables) for ch in choices)
    words = [prose.count(n, what) for n, what in ((pictures, "picture"), (objects, "embedded object"), (tables, "table"))
             if n]
    if not words:
        return None
    one = pictures + objects + tables == 1
    if tracked:
        return (f"{prose.join(words)} {'is' if one else 'are'} deleted with the paragraphs (tracked: "
                f"{'it goes' if one else 'they go'} when the deletion is accepted)")
    return f"{prose.join(words)} {'goes' if one else 'go'} with the deleted paragraphs"


# Paragraph children that carry no content and may stay in place (outside the w:del wrapper) when the paragraph's
# runs are tracked-deleted: bookmark anchors (Word's _GoBack is in most saved files) and proofing markers.
INERT_P_CHILDREN = (W + "bookmarkStart", W + "bookmarkEnd", W + "proofErr")


@register
class DeleteParagraphs(Op):
    name = "delete_paragraphs"
    mode = "content"
    description = ("Delete the selected paragraphs ('all', or a paragraph selector) as a tracked change, or untracked "
                   "on request: a table whose every paragraph is selected goes whole, fields, comments, footnotes and "
                   "bookmarks wholly inside go with them, an untracked section break joins its pages to the next "
                   "section, and a cell, the body, a header, a note or a comment keeps its last paragraph, emptied.")
    params_schema = {
        "type": "object",
        "properties": {
            "tracked": {"type": "boolean", "default": True,
                        "description": "true = tracked deletion (content mode); false = untracked removal."},
            "author": {"type": "string", "description": "Tracked-change author (default: SPEC_SURGEON_AUTHOR or OS user)."},
            "allow_missing": {"type": "boolean", "default": False,
                              "description": "If true, matching 0 paragraphs is a no-op instead of an exception."},
            "date_convention": {"type": "string", "enum": ["word-local-z", "utc"], "default": "word-local-z"},
        },
    }
    select_schema = {
        "type": "object",
        "properties": {
            "scope": {"type": "array", "items": {"type": "string", "enum": list(SCOPE_KINDS)}, "default": ["document"]},
            "paragraphs": {"description": "required: 'all' (every paragraph of the scope) or a paragraph selector "
                                          "predicate"},
        },
        "required": ["paragraphs"],
    }
    verification_profile = {
        "V1": "untracked: header/footer parts a removed section break alone used, and media only removed content "
              "used, leave the package (members_removed exact)",
        "V2": "untracked: every removed paragraph listed with after=None, a kept paragraph emptied; tracked: block "
              "text unchanged (w:delText)",
        "V4": "tracked: +1 del per run group and per deleted table row, +1 del_mark per deleted paragraph mark; "
              "untracked: -(the revisions removed with the content)",
        "V5b": "tracked: reject-all view equals source; untracked: enumerated-list mode",
        "V8": "tracked: accept-all view of every deleted or kept paragraph is empty",
        "V10": "deleted_paragraphs re-derives the selection, the tables, the kept paragraphs and (untracked) the notes, "
               "section breaks, comments, bookmarks and relationships that go, and checks every changed part whole "
               "(untracked) or every changed paragraph and row exactly (tracked)",
    }

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select = dict(select or {})
        unknown = set(select) - {"scope", "paragraphs"}
        if unknown:
            raise JobException(f"unknown selector key(s) {sorted(unknown)}", "bad-params")
        if "paragraphs" not in select:
            raise JobException("delete_paragraphs needs select.paragraphs: 'all' for every paragraph of the scope, or "
                               "a paragraph selector", "bad-params")
        para_pred = select["paragraphs"]
        if para_pred in (None, {}):
            raise JobException("select.paragraphs is empty; give 'all' or a paragraph selector", "bad-params")
        check_paragraph_selector(para_pred)
        tracked = params.get("tracked", True)
        if not isinstance(tracked, bool):
            raise JobException("tracked must be true or false", "bad-params")
        if params.get("date_convention", "word-local-z") not in ("word-local-z", "utc"):
            raise JobException("date_convention must be 'word-local-z' or 'utc'", "bad-params")
        author = params.get("author") or default_author()
        parts = scope_parts(pkg, select.get("scope"))            # an empty list names no part: refused there
        scope = select.get("scope") or ["document"]
        if isinstance(scope, str):
            scope = [scope]
        sheet = StyleSheet(pkg)

        frag = self.fragment({"scope": scope, "paragraphs": para_pred},
                             {**params, "tracked": tracked, "author": author},
                             mode=("content" if tracked else "untracked"))
        frag.untracked_diffs_expected = not tracked
        removal = None if tracked else Removal(pkg)
        choices: List[Choice] = []
        total = 0
        for part in parts:
            frag.touches.append((part, "scope", "verify"))        # V10 re-resolves the selection over this part
            root = removal.root(part) if removal is not None else pkg.xml(part)
            pars = paragraphs(root)
            chosen = [p for bi, p in enumerate(pars) if not in_separator(p)
                      and paragraph_selected(para_pred, p, bi, block_full_text(p), sheet, all_pars=pars)]
            if chosen:
                total += len(chosen)
                choices.append(choose(part, root, chosen, tracked))
        assertion = {"kind": "deleted_paragraphs", "scope": select.get("scope"), "select": {"paragraphs": para_pred},
                     "asked_tracked": params.get("tracked"), "author": author, "expected_paragraphs": total}
        if total == 0:
            if params.get("allow_missing"):
                frag.notes.append(f"0 paragraphs matched in {scope}; no-op.")
                frag.property_assertions.append({**assertion, "expected": {}})
                return frag
            raise JobException(f"no paragraphs matched selector {para_pred} in {scope}", "not-found",
                               {"scope": scope, "paragraphs": para_pred})
        expected = {}
        for ch in choices:
            index = {p: i for i, p in enumerate(paragraphs(ch.root))}
            ordinal = {t: k for k, t in enumerate(ch.root.iter(W + "tbl"))}
            expected[ch.part] = {"delete": [index[p] for p in ch.deleted], "keep": [index[p] for p in ch.kept],
                                 "tables": [ordinal[t] for t in ch.tables]}
            stays = "keeps its paragraph mark and loses its text" if tracked else "stays, emptied"
            for p in ch.kept:
                if p not in ch.last_section:
                    frag.flags.append(f"{ch.part} paragraph {index[p]} ({shown(p)}) {stays} (a cell, a note or the "
                                      f"document keeps its last paragraph, and two tables must not join)")
            if ch.last_section:
                frag.flags.append(f"the last section is now empty and shows a blank page (paragraph "
                                  f"{prose.join(str(index[p]) for p in ch.last_section)} {stays}); remove the section "
                                  f"break before it to drop the page")
        shown_objects = objects_flag(choices, tracked)
        if shown_objects:
            frag.flags.append(shown_objects)
        if tracked:
            for ch in choices:
                refuse_thread_ends(pkg, ch.part, ch.root, ch.deleted + ch.in_tables, ())
            self._plan_tracked(pkg, frag, choices, expected)
        else:
            self._plan_untracked(pkg, frag, removal, choices, expected)
            assertion["expected_removal"] = removal.summary()
        frag.geometry_reference = "none"
        frag.geometry_reason = "deleting paragraph(s) reflows following content"
        frag.property_assertions.append({**assertion, "expected": expected})
        frag.transform_data.update({"tracked": tracked, "author": author, "use_date_utc": file_uses_date_utc(pkg),
                                    "first_rev_id": frag.rev_id_base,
                                    "date_convention": params.get("date_convention", "word-local-z")})
        n_tables = sum(len(ch.tables) for ch in choices)
        n_kept = sum(len(ch.kept) for ch in choices)
        frag.notes.append(f"delete_paragraphs: {total} paragraph(s) selected "
                          f"({'tracked' if tracked else 'untracked'}) across {len(choices)} part(s): "
                          f"{n_tables} whole table(s), {n_kept} paragraph(s) kept emptied.")
        return frag

    def _plan_tracked(self, pkg: DocxPackage, frag: PlanFragment, choices: List[Choice], expected) -> None:
        d_del = d_mark = 0
        targets: Dict[str, Any] = {}
        for ch in choices:
            pars = paragraphs(ch.root)
            index = {p: i for i, p in enumerate(pars)}
            marked, kept = set(ch.deleted) | set(ch.in_tables), set(ch.kept)
            changed = [p for p in pars if p in marked or p in kept]
            for t in ch.tables:
                if count_revisions(t):
                    raise JobException(f"{ch.part}: a table to delete already carries pending revisions; accept or "
                                       f"reject them first, or delete untracked", "existing-revisions",
                                       {"part": ch.part})
            runs_of = {}
            for p in changed:
                where = f"{ch.part} paragraph {index[p]} ({shown(p)})"
                if count_revisions(p):
                    raise JobException(f"{where} already carries pending revisions; accept or reject them first, or "
                                       f"delete untracked", "existing-revisions", {"part": ch.part,
                                                                                  "paragraph": index[p]})
                runs_of[p] = tracked_runs(p, where)
            gone = {r for runs in runs_of.values() for r in runs}
            refuse_crossing_fields(ch.part, ch.root, lambda r: r in gone, "delete")
            rows = table_rows(ch.root, ch.tables, ch.part)
            for p in changed:
                d_del += len(run_groups(runs_of[p]))
                d_mark += p in marked
                frag.accepted_views.append({"part": ch.part, "block_index": index[p], "accepted_view": ""})
                frag.touches.append((ch.part, str(index[p]), "text"))
            d_del += len(rows)
            frag.touches += [(ch.part, f"tr:{k}:{r}", "trPr") for k, r in rows]
            expected[ch.part]["rows"] = [[k, r] for k, r in rows]
            if ch.part not in frag.intended_parts:
                frag.intended_parts.append(ch.part)
            targets[ch.part] = {"paragraphs": {str(index[p]): p in marked for p in changed},
                                "rows": [[k, r] for k, r in rows]}
            frag.flags += tracked_flags(pkg, ch.part, gone)
            if ch.part == pkg.main_part():
                secs = DocxPackage.sect_prs(ch.root)
                frag.flags += section_flags(pkg, ch.root, [i for i, sp in enumerate(secs)
                                                           if sp.getparent().getparent() in marked], True)
        frag.rev_delta["del"] = frag.rev_delta.get("del", 0) + d_del
        if d_mark:
            frag.rev_delta["del_mark"] = frag.rev_delta.get("del_mark", 0) + d_mark
        frag.rev_id_base = max_revision_id(pkg) + 1
        frag.rev_ids_needed = d_del + d_mark
        frag.transform_data["targets"] = targets

    def _plan_untracked(self, pkg: DocxPackage, frag: PlanFragment, removal: Removal, choices: List[Choice],
                        expected) -> None:
        for ch in choices:
            remove_deletion(removal, ch)
        removal.resolve()
        for ch in choices:
            refuse_thread_ends(pkg, ch.part, ch.root, ch.deleted + ch.in_tables, removal.comments)
        removal.predict(frag, "untracked paragraph deletion")
        main = pkg.main_part()
        if removal.sections:
            frag.flags += section_flags(pkg, removal.root(main), removal.sections, False)
        if removal.parts_gone:
            frag.flags.append(f"header/footer part(s) {removal.parts_gone} were used only by the removed section "
                              f"break(s); they leave the package")
        if removal.printers:
            frag.flags.append(f"printer settings part(s) {removal.printers} were used only by the removed section "
                              f"break(s); they leave the package")
        if removal.comments:
            frag.flags.append(f"comment(s) {', '.join(removal.comments)} lay wholly in the deleted text; they go with "
                              f"it (replies included)")
        for part, ids in removal.notes.items():
            frag.flags.append(f"{len(ids)} note(s) of {part} were referenced only from the deleted text; they go with "
                              f"it (Word renumbers the rest)")
        moved = sum(len(ms) for by_top in removal.moved.values() for ms in by_top.values())
        if moved:
            frag.flags.append(f"{moved} bookmark or range mark(s) ran into the deleted text from outside; each moved to "
                              f"the edge of the text that stays")
        frag.flags += dangling_flags(pkg, removal)
        frag.flags.append(f"UNTRACKED deletion of {sum(len(ch.selected) for ch in choices)} selected paragraph(s) at "
                          f"the user's request.")
        frag.transform_data["targets"] = expected
        frag.transform_data["removal"] = removal.summary()

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td or not td.get("targets"):
            return
        if not td["tracked"]:
            removal = Removal(pkg)
            for part, d in td["targets"].items():
                root = removal.root(part)
                pars, tables = paragraphs(root), list(root.iter(W + "tbl"))
                remove_deletion(removal, Choice(part=part, root=root, tables=[tables[k] for k in d["tables"]],
                                                deleted=[pars[i] for i in d["delete"]],
                                                kept=[pars[i] for i in d["keep"]]))
            removal.resolve()
            if removal.summary() != td["removal"]:
                raise JobException(f"internal: the removal resolves to {removal.summary()} at apply time, the plan "
                                   f"predicted {td['removal']}", "internal")
            removal.apply_to()
            return
        stamp = RevisionStamp.now(td["author"], td["use_date_utc"], td.get("date_convention", "word-local-z"))
        next_id = [td.get("first_rev_id", 1)]
        for part, d in td["targets"].items():
            root = pkg.xml(part)
            pars = paragraphs(root)
            work = [(pars[int(bi)], mark) for bi, mark in sorted(d["paragraphs"].items(), key=lambda kv: int(kv[0]))]
            work = [(p, mark, tracked_runs(p, f"{part} paragraph")) for p, mark in work]
            rows = [row_at(root, k, r) for k, r in d["rows"]]
            for p, mark, runs in work:
                if mark:
                    delete_mark(p, stamp, next_id)
                delete_runs(runs, stamp, next_id)
            for tr in rows:
                delete_row(tr, stamp, next_id)
            pkg.write_xml(part, root)

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        pred = select.get("paragraphs")
        if pred == "all":
            head = "Delete every paragraph " + (prose.scope_words(select.get("scope")) or "in the document body")
        elif pred in (None, {}):
            head = "Delete the chosen paragraphs" + (" " + prose.scope_words(select.get("scope"))
                                                     if prose.scope_words(select.get("scope")) else "")
        else:
            head = "Delete all " + prose.where(select)[3:]
        both = ("a table all of whose paragraphs are chosen goes whole, and a cell, the document or a note keeps its last "
                "paragraph, emptied")
        if bool(params.get("tracked", True)):
            tail = "shown as tracked changes" + (f" by {prose.quote(params['author'])}" if params.get("author") else "")
            rest = both + "; comments, footnotes, bookmarks and section breaks in the deleted text stay until the " \
                          "deletion is accepted"
        else:
            tail = "removing them outright without tracked changes"
            rest = both + "; fields, comments, footnotes and bookmarks lying wholly inside go with them, and a deleted " \
                          "section break joins its pages to the next section"
        third = "Files with no such paragraph are left unchanged." if params.get("allow_missing") else ""
        return prose.sentence(f"{head}, {tail}") + " " + prose.sentence(rest) + (" " + third if third else "")
