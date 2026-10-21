"""Op: remove_footnotes — remove footnotes and/or endnotes: all of them, or those whose own text matches a pattern
(docs/BUILTIN_OPS_BATCH6_DESIGN.md §2). The verifier derives every decision again with its own code (verify/footnotes.py,
kind ``removed_footnotes``).

**Notes** (``which``: footnotes, endnotes or both): a note is an entry of the first footnotes (endnotes) part of the main
part's relationships; its *references* are the ``w:footnoteReference`` (``w:endnoteReference``) with its ``w:id`` in the
text parts other than the notes parts. **Chosen**: every note with a reference in the scope's parts (default: the
document body) whose own text — its paragraphs' block texts, one per line — matches ``text_regex`` (``re.search``; no
pattern: every one). Refused: a reference in a text box (``textbox``), a custom reference mark (``w:customMarkFollows``
on: ``custom-mark``), a reference to a separator or to no note, a note referenced twice, two notes sharing an id, a
note holding a text box (``structure``).

**Untracked**: the reference goes — its run when the run holds nothing else but its ``w:rPr`` — and with it the note's
entry, and what the entry held, as an untracked deletion removes it (ops/deletion.py ``Removal``: comments anchored in
it, bookmarks, relationships and media only it used). Separators stay; Word renumbers the notes that stay.
**Tracked** (the default): the reference's run is deleted (it must hold nothing else: ``structure``) and every run and
paragraph mark of the note is deleted (ops/deletion.py's tracked rules and refusals) — Word removes the note when the
deletion is accepted.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..ooxml import RevisionStamp, count_revisions, file_uses_date_utc, max_revision_id, paragraphs
from ..package import DocxPackage, W
from . import prose, register
from .base import JobException, Op, PlanFragment
from .deletion import (NOTE_ENTRY, NOTE_KINDS, NOTE_REFERENCE, SEPARATOR_TYPES, Removal, dangling_flags, delete_mark,
                       delete_runs, nearest_p, note_text, refuse_crossing_fields, regex, run_groups, tracked_runs)
from .removal import plan_run_removal
from .replace_table import default_author
from .selectors import scope_parts

WHICH = {"footnotes": ("footnotes",), "endnotes": ("endnotes",), "both": NOTE_KINDS}
REFERENCE_SCOPES = ("document", "headers", "footers", "comments")
ON = ("1", "true", "on")


def _in_text_box(el) -> bool:
    p = nearest_p(el)
    return (p is not None and nearest_p(p) is not None) or any(a.tag == W + "txbxContent" for a in el.iterancestors())


@register
class RemoveFootnotes(Op):
    name = "remove_footnotes"
    mode = "content"
    description = ("Remove footnotes and/or endnotes — all of them, or those whose own text matches a pattern: the "
                   "reference mark in the text and the note itself, as a tracked deletion (default) or untracked. "
                   "Separators stay; Word renumbers the notes that stay.")
    params_schema = {"type": "object", "properties": {
        "which": {"type": "string", "enum": list(WHICH), "default": "both",
                  "description": "footnotes | endnotes | both"},
        "text_regex": {"type": "string", "description": "only the notes whose own text matches this pattern "
                                                        "(re.search; default: every note)"},
        "tracked": {"type": "boolean", "default": True, "description": "false = remove outright"},
        "author": {"type": "string", "description": "tracked-change author (default: SPEC_SURGEON_AUTHOR or OS user)"},
        "date_convention": {"type": "string", "enum": ["word-local-z", "utc"], "default": "word-local-z"},
        "allow_missing": {"type": "boolean", "default": True,
                          "description": "default true: a file with no such note is left as it is; false sets it aside"},
    }}
    select_schema = {"type": "object", "properties": {
        "scope": {"type": "array", "items": {"type": "string", "enum": list(REFERENCE_SCOPES)},
                  "description": "the parts whose references count (default: the document body)"}}}
    verification_profile = {
        "V1": "untracked: media only the removed notes used leave the package",
        "V2": "untracked: every paragraph of a removed note deleted; a paragraph losing its reference a structure-only "
              "change; tracked: block text unchanged",
        "V3": "untracked: the paragraph that held a reference declares one footnoteReference/endnoteReference fewer",
        "V4": "tracked: +1 del per reference run and per run group of the note, +1 del_mark per note paragraph",
        "V8": "tracked: every note paragraph's accepted view is empty",
        "V10": "removed_footnotes re-derives the notes chosen and everything their removal takes, and checks every "
               "changed part whole (untracked) or every changed paragraph exactly (tracked)",
    }

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        which = params.get("which") if params.get("which") in WHICH else "both"
        what = {"footnotes": "footnote", "endnotes": "endnote", "both": "footnote and endnote"}[which]
        head = f"Remove every {what}"
        if isinstance(params.get("text_regex"), str) and params["text_regex"]:
            head += f" whose text matches the pattern {prose.quote(params['text_regex'])}"
        where = prose.scope_words(select.get("scope"))
        head += f" referenced {where}" if where else ""
        head += ", with its reference mark in the text"
        if params.get("tracked", True) is False:
            head += ", removing them outright without tracked changes"
        else:
            head += ", shown as tracked changes" + (f" by {prose.quote(params['author'])}" if params.get("author") else "")
        remarks = ["the separators stay and Word renumbers the notes that stay"]
        if params.get("allow_missing", True) is False:
            remarks.append("a file with no such note is set aside rather than passed through")
        return prose.sentence(head) + " " + prose.sentence("; ".join(remarks))

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select = dict(select or {})
        if set(select) - {"scope"}:
            raise JobException(f"remove_footnotes selects by scope only; unknown {sorted(set(select) - {'scope'})}",
                               "bad-params")
        which = params.get("which", "both")
        if which not in WHICH:
            raise JobException(f"'which' must be one of {list(WHICH)}", "bad-params")
        rx = regex(params["text_regex"], "text_regex") if params.get("text_regex") is not None else None
        tracked = params.get("tracked", True)
        allow_missing = params.get("allow_missing", True)
        if not isinstance(tracked, bool) or not isinstance(allow_missing, bool):
            raise JobException("tracked and allow_missing must be true or false", "bad-params")
        if params.get("date_convention", "word-local-z") not in ("word-local-z", "utc"):
            raise JobException("date_convention must be 'word-local-z' or 'utc'", "bad-params")
        scope = select.get("scope") or ["document"]
        if isinstance(scope, str):
            scope = [scope]
        if set(scope) - set(REFERENCE_SCOPES):
            raise JobException(f"references are looked for in {list(REFERENCE_SCOPES)}; got {scope}", "bad-params")
        author = params.get("author") or default_author()
        frag = self.fragment({"scope": scope}, {**params, "which": which, "tracked": tracked, "author": author},
                             mode="content" if tracked else "untracked")
        frag.untracked_diffs_expected = not tracked
        removal = None if tracked else Removal(pkg)
        roots: Dict[str, Any] = {}

        def root_of(part: str):
            if removal is not None:
                return removal.root(part)
            if part not in roots:
                roots[part] = pkg.xml(part)
            return roots[part]
        parts = scope_parts(pkg, scope)
        kinds = pkg.parts_by_kind()
        notes_parts = [kinds[k][0] for k in NOTE_KINDS if kinds[k]]
        chosen: List[Tuple[str, str, Any, str, Any]] = []      # (kind, id, entry, referencing part, reference)
        for kind in WHICH[which]:
            if not kinds[kind]:
                continue
            npart = kinds[kind][0]
            frag.touches.append((npart, "scope", "verify"))
            entries: Dict[str, List[Any]] = {}
            for e in root_of(npart).findall(NOTE_ENTRY[kind]):
                entries.setdefault(e.get(W + "id"), []).append(e)
            uses: Dict[str, int] = {}
            for tp in pkg.text_parts():
                if tp not in notes_parts:
                    for ref in root_of(tp).iter(NOTE_REFERENCE[kind]):
                        uses[ref.get(W + "id")] = uses.get(ref.get(W + "id"), 0) + 1
            for part in parts:
                for ref in root_of(part).iter(NOTE_REFERENCE[kind]):
                    nid = ref.get(W + "id")
                    where = f"{part}: the {kind[:-1]} reference {nid}"
                    found = entries.get(nid) or []
                    if len(found) != 1 or (found[0].get(W + "type") or "normal") in SEPARATOR_TYPES:
                        raise JobException(f"{where} names {'no note' if not found else 'a separator' if len(found) == 1 else 'two notes'} "
                                           f"of {npart}", "structure", {"part": part, "id": nid})
                    entry = found[0]
                    if rx is not None and not rx.search(note_text(entry)):
                        continue
                    if _in_text_box(ref):
                        raise JobException(f"{where} sits in a text box (Word keeps two copies of it); remove that "
                                           f"note in Word", "textbox", {"part": part, "id": nid})
                    if (ref.get(W + "customMarkFollows") or "").lower() in ON:
                        raise JobException(f"{where} uses a custom reference mark (a symbol typed after it instead "
                                           f"of a number); remove that note in Word", "custom-mark",
                                           {"part": part, "id": nid})
                    if uses.get(nid, 0) != 1:
                        raise JobException(f"{where}: the note is referenced {uses.get(nid, 0)} times; remove it in "
                                           f"Word", "structure", {"part": part, "id": nid})
                    if any(q is not p for p in entry.iter(W + "p") for q in p.iter(W + "p")) or \
                            any(True for _ in entry.iter(W + "txbxContent")):
                        raise JobException(f"{where}: the note holds a text box; remove it in Word", "textbox",
                                           {"part": part, "id": nid})
                    chosen.append((kind, nid, entry, part, ref))
        assertion = {"kind": "removed_footnotes", "asked_which": params.get("which"),
                     "asked_text_regex": params.get("text_regex"), "asked_tracked": params.get("tracked"),
                     "scope": select.get("scope"), "author": author,
                     "expected": {k: [nid for kk, nid, *_ in chosen if kk == k] for k in WHICH[which]}}
        if not chosen:
            why = f"no {'note' if which == 'both' else which[:-1]} to remove" + (" matches the pattern" if rx else "")
            if allow_missing:
                frag.notes.append(f"remove_footnotes ({which}): {why} — nothing to do.")
                frag.property_assertions.append(assertion)
                return frag
            raise JobException(f"remove_footnotes ({which}): {why}", "not-found", {"which": which})
        if tracked:
            self._plan_tracked(pkg, frag, chosen, root_of)
        else:
            self._plan_untracked(pkg, frag, removal, chosen)
            assertion["expected_removal"] = removal.summary()
        frag.property_assertions.append(assertion)
        frag.geometry_reference = "none"
        frag.geometry_reason = "removing notes changes the page's note area and the numbers of the notes that stay"
        frag.transform_data.update({"tracked": tracked, "author": author, "use_date_utc": file_uses_date_utc(pkg),
                                    "first_rev_id": frag.rev_id_base,
                                    "date_convention": params.get("date_convention", "word-local-z")})
        frag.notes.append(f"remove_footnotes ({which}, {'tracked' if tracked else 'untracked'}): "
                          + ", ".join(f"{sum(1 for c in chosen if c[0] == k)} {k}" for k in WHICH[which]) + " removed.")
        return frag

    def _plan_tracked(self, pkg: DocxPackage, frag: PlanFragment, chosen, root_of) -> None:
        kinds = pkg.parts_by_kind()
        refs: Dict[str, List[List[int]]] = {}
        notes: Dict[str, List[str]] = {}
        d_del = d_mark = 0
        ref_runs: Dict[str, List[Any]] = {}
        for kind, nid, entry, part, ref in chosen:
            run = ref.getparent()
            where = f"{part}: the {kind[:-1]} reference {nid}"
            if run is None or run.tag != W + "r" or any(isinstance(c.tag, str) and c.tag not in (W + "rPr",) and c is not ref
                                                         for c in run):
                raise JobException(f"{where} shares its run with other content; remove that note in Word, or "
                                   f"untracked", "structure", {"part": part, "id": nid})
            p = nearest_p(run)
            if count_revisions(run) or any(a.tag in (W + "ins", W + "del", W + "moveFrom", W + "moveTo")
                                           for a in run.iterancestors()):
                raise JobException(f"{where} lies in a pending tracked change; accept or reject it first, or remove "
                                   f"untracked", "existing-revisions", {"part": part, "id": nid})
            pars = paragraphs(root_of(part))
            bi = pars.index(p)
            own = [r for r in p.iter(W + "r") if nearest_p(r) is p]
            refs.setdefault(part, []).append([bi, own.index(run)])
            ref_runs.setdefault(part, []).append(run)
            plan_run_removal(frag, part, bi, p, [run], True)
            npart = kinds[kind][0]
            if count_revisions(entry):
                raise JobException(f"{npart}: the note {nid} already carries pending revisions; accept or reject them "
                                   f"first, or remove untracked", "existing-revisions", {"part": npart, "id": nid})
            npars = paragraphs(root_of(npart))
            for q in entry.iter(W + "p"):
                runs = tracked_runs(q, f"{npart} note {nid}")
                d_del += len(run_groups(runs))
                d_mark += 1
                frag.accepted_views.append({"part": npart, "block_index": npars.index(q), "accepted_view": ""})
                frag.touches.append((npart, str(npars.index(q)), "text"))
            notes.setdefault(npart, []).append(nid)
            if npart not in frag.intended_parts:
                frag.intended_parts.append(npart)
        for part, runs in ref_runs.items():
            gone = set(runs)
            refuse_crossing_fields(part, root_of(part), lambda r: r in gone, "remove")
        for npart, ids in notes.items():
            root = root_of(npart)
            gone = {r for e in root if e.get(W + "id") in ids for q in e.iter(W + "p")
                    for r in tracked_runs(q, npart)}
            refuse_crossing_fields(npart, root, lambda r: r in gone, "remove")
        frag.rev_delta["del"] = frag.rev_delta.get("del", 0) + d_del
        frag.rev_delta["del_mark"] = frag.rev_delta.get("del_mark", 0) + d_mark
        frag.rev_id_base = max_revision_id(pkg) + 1
        frag.rev_ids_needed = sum(len(v) for v in refs.values()) + d_del + d_mark
        frag.transform_data["targets"] = {"refs": refs, "notes": notes}
        frag.flags.append("the notes are shown as deleted; Word removes them when the deletion is accepted")

    def _plan_untracked(self, pkg: DocxPackage, frag: PlanFragment, removal: Removal, chosen) -> None:
        refs: Dict[str, List[List[int]]] = {}
        for kind, nid, entry, part, ref in chosen:
            run = ref.getparent()
            whole = run is not None and run.tag == W + "r" and all(
                not isinstance(c.tag, str) or c.tag == W + "rPr" or c is ref for c in run)
            removal.remove(part, run if whole else ref)
            p = nearest_p(ref)
            refs.setdefault(part, []).append([paragraphs(removal.root(part)).index(p),
                                              list(p.iter(NOTE_REFERENCE[kind])).index(ref), kind])
        removal.resolve()
        want = {pkg.parts_by_kind()[k][0]: sorted(nid for kk, nid, *_ in chosen if kk == k)
                for k in NOTE_KINDS if any(c[0] == k for c in chosen)}
        if {p: sorted(ids) for p, ids in removal.notes.items()} != want:
            raise JobException(f"internal: the removal takes notes {removal.notes}, the op chose {want}", "internal")
        removal.predict(frag, "note removed")
        if removal.comments:
            frag.flags.append(f"comment(s) {', '.join(removal.comments)} were anchored in the removed notes; they go "
                              f"with them")
        frag.flags += dangling_flags(pkg, removal)
        frag.flags.append(f"UNTRACKED removal of {len(chosen)} note(s) at the user's request.")
        frag.transform_data["targets"] = {"refs": refs}
        frag.transform_data["removal"] = removal.summary()

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td or not td.get("targets"):
            return
        if not td["tracked"]:
            removal = Removal(pkg)
            for part, items in td["targets"]["refs"].items():
                pars = paragraphs(removal.root(part))
                for bi, k, kind in items:
                    ref = list(pars[bi].iter(NOTE_REFERENCE[kind]))[k]
                    run = ref.getparent()
                    whole = run is not None and run.tag == W + "r" and all(
                        not isinstance(c.tag, str) or c.tag == W + "rPr" or c is ref for c in run)
                    removal.remove(part, run if whole else ref)
            removal.resolve()
            if removal.summary() != td["removal"]:
                raise JobException(f"internal: the removal resolves to {removal.summary()} at apply time, the plan "
                                   f"predicted {td['removal']}", "internal")
            removal.apply_to()
            return
        stamp = RevisionStamp.now(td["author"], td["use_date_utc"], td.get("date_convention", "word-local-z"))
        next_id = [td.get("first_rev_id", 1)]
        roots: Dict[str, Any] = {}
        work: List[Tuple[Any, bool, List[Any]]] = []
        for part, items in td["targets"]["refs"].items():
            root = roots.setdefault(part, pkg.xml(part))
            pars = paragraphs(root)
            for bi, k in items:
                p = pars[bi]
                work.append((p, False, [[r for r in p.iter(W + "r") if nearest_p(r) is p][k]]))
        for npart, ids in td["targets"]["notes"].items():
            root = roots.setdefault(npart, pkg.xml(npart))
            for e in root:
                if e.get(W + "id") in ids and e.tag in NOTE_ENTRY.values():
                    work += [(q, True, tracked_runs(q, npart)) for q in e.iter(W + "p")]
        for p, mark, runs in work:
            if mark:
                delete_mark(p, stamp, next_id)
            delete_runs(runs, stamp, next_id)
        for part, root in roots.items():
            pkg.write_xml(part, root)
