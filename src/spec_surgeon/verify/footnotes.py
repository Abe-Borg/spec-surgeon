"""V10 kind ``removed_footnotes`` (built-in batch 6, docs/BUILTIN_OPS_BATCH6_DESIGN.md §2) — verifier side, independent
of ops/remove_footnotes.py (rule 1): this module's own reading of the notes, their references and which ones go.

* **Notes**: the entries of the first footnotes (endnotes) part of the main part's relationships; a note's references
  are the ``w:footnoteReference`` (``w:endnoteReference``) with its id in the text parts other than the notes parts.
  Chosen: every note referenced in the scope's parts (default the body) whose own text (its paragraphs' texts, one per
  line) matches ``text_regex`` (none: every one). The op must refuse a reference in a text box, a custom mark, a
  reference to a separator or to no note, a note referenced twice or holding a text box.
* **Untracked**: each reference (its run, when the run holds nothing else) leaves, then the deletion module's removal
  (verify/deletion.py ``VRemoval``) settles what goes with it — the notes themselves, the comments anchored in them,
  bookmarks, relationships, media — and every changed part is compared whole.
* **Tracked**: each reference's run (holding nothing else) in a NEW ``w:del`` of its own, every run and paragraph mark of
  each chosen note deleted (verify/deletion.py ``check_tracked_paragraph``); the residual does the rest.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .reader import W
from .props import Ctx
from .edits import V_REVISION_TAGS
from .deletion import (VRefuse, VRemoval, check_tracked_paragraph, v_field_cut, v_old_ids, v_par, v_tracked_runs,
                       v_uses_date_utc)

_V_KINDS = {"footnotes": (W + "footnote", W + "footnoteReference"), "endnotes": (W + "endnote", W + "endnoteReference")}
_V_WHICH = {"footnotes": ["footnotes"], "endnotes": ["endnotes"], "both": ["footnotes", "endnotes"]}
_V_SEPARATORS = {"separator", "continuationSeparator", "continuationNotice"}
_V_TEXT = (W + "t", W + "delText", W + "instrText", W + "delInstrText",
           "{http://schemas.openxmlformats.org/officeDocument/2006/math}t")
_V_REV_WRAPS = {W + "ins", W + "del", W + "moveFrom", W + "moveTo"}


def _v_note_text(entry) -> str:
    return "\n".join("".join(t.text or "" for t in p.iter(*_V_TEXT) if v_par(t) is p) for p in entry.iter(W + "p"))


def _v_boxed(el) -> bool:
    p = v_par(el)
    return (p is not None and v_par(p) is not None) or any(a.tag == W + "txbxContent" for a in el.iterancestors())


def _v_alone(run, ref) -> bool:
    return run is not None and run.tag == W + "r" and all(not isinstance(c.tag, str) or c.tag == W + "rPr" or c is ref
                                                         for c in run)


def _v_chosen(ctx: Ctx, a: Dict[str, Any], root_of) -> List[Tuple[str, str, Any, str, Any]]:
    """(kind, id, entry, referencing part, reference) of every note to remove, in order — or VRefuse."""
    which = a.get("asked_which") or "both"
    if which not in _V_WHICH:
        raise ValueError(f"unknown 'which' {which!r}")
    pattern = a.get("asked_text_regex")
    rx = re.compile(pattern) if pattern is not None else None
    scope = a.get("scope") or ["document"]
    parts = ctx.scope_parts([scope] if isinstance(scope, str) else scope)
    notes_parts = [ctx.src.part_of_kind(k)[0] for k in _V_KINDS if ctx.src.part_of_kind(k)]
    out = []
    for kind in _V_WHICH[which]:
        found = ctx.src.part_of_kind(kind)
        if not found:
            continue
        entry_tag, ref_tag = _V_KINDS[kind]
        entries: Dict[str, List[Any]] = {}
        for e in [x for x in root_of(found[0]) if x.tag == entry_tag]:
            entries.setdefault(e.get(W + "id"), []).append(e)
        uses: Dict[str, int] = {}
        for tp in ctx.src.text_parts():
            if tp not in notes_parts:
                for ref in root_of(tp).iter(ref_tag):
                    uses[ref.get(W + "id")] = uses.get(ref.get(W + "id"), 0) + 1
        for part in parts:
            for ref in root_of(part).iter(ref_tag):
                nid = ref.get(W + "id")
                es = entries.get(nid) or []
                if len(es) != 1 or (es[0].get(W + "type") or "normal") in _V_SEPARATORS:
                    raise VRefuse("structure", f"reference {nid} names no single note")
                if rx is not None and not rx.search(_v_note_text(es[0])):
                    continue
                if _v_boxed(ref):
                    raise VRefuse("textbox", f"reference {nid} sits in a text box")
                if (ref.get(W + "customMarkFollows") or "").lower() in ("1", "true", "on"):
                    raise VRefuse("custom-mark", f"reference {nid} uses a custom mark")
                if uses.get(nid) != 1:
                    raise VRefuse("structure", f"note {nid} is referenced {uses.get(nid, 0)} times")
                if any(q is not p for p in es[0].iter(W + "p") for q in p.iter(W + "p")) or \
                        any(True for _ in es[0].iter(W + "txbxContent")):
                    raise VRefuse("textbox", f"note {nid} holds a text box")
                out.append((kind, nid, es[0], part, ref))
    return out


def _removed_footnotes(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    t = a.get("asked_tracked")
    if t is not None and not isinstance(t, bool):
        raise ValueError(f"'tracked' {t!r} is not true or false — the op must refuse")
    tracked = True if t is None else t
    author = a.get("author")
    rm = None if tracked else VRemoval(ctx.src)
    views = {}

    def root_of(part):
        if rm is not None:
            return rm.root(part)
        if part not in views:
            views[part] = ctx.view(part)[0] if part in ctx.both else None
        return views[part].root if views[part] is not None else ctx.src.xml(part)
    try:
        chosen = _v_chosen(ctx, a, root_of)
    except VRefuse as e:
        errs.append(f"removed_footnotes: {e} — the op must refuse")
        return "removed_footnotes: the op must refuse"
    except re.error as e:
        errs.append(f"removed_footnotes: bad pattern ({e})")
        return "removed_footnotes: bad pattern"
    which = a.get("asked_which") or "both"
    derived = {k: [nid for kk, nid, *_ in chosen if kk == k] for k in _V_WHICH[which]}
    if derived != (a.get("expected") or {}):
        errs.append(f"removed_footnotes: notes {derived} go here, the plan removes {a.get('expected')}")
        return "removed_footnotes: decisions differ"
    if not chosen:
        return "removed_footnotes: nothing to remove"
    if rm is not None:
        try:
            for kind, nid, entry, part, ref in chosen:
                run = ref.getparent()
                rm.drop(part, run if _v_alone(run, ref) else ref)
            rm.settle()
        except VRefuse as e:
            errs.append(f"removed_footnotes: {e} — the op must refuse")
            return "removed_footnotes: the op must refuse"
        want = {}
        for kind, nid, *_ in chosen:
            want.setdefault(ctx.src.part_of_kind(kind)[0], []).append(nid)
        if {p: sorted(v) for p, v in rm.notes.items()} != {p: sorted(v) for p, v in want.items()}:
            errs.append(f"removed_footnotes: removing the references takes notes {rm.notes}, expected {want}")
        rm.check(ctx, "removed_footnotes", a.get("expected_removal"), errs)
        return f"removed_footnotes: {len(chosen)} note(s) and their references removed (untracked)"
    old_ids, utc = v_old_ids(ctx.src), v_uses_date_utc(ctx.src)
    refs_by_par: Dict[Tuple[str, Any], List[Any]] = {}
    notes: List[Tuple[str, Any, List[Any]]] = []
    try:
        for kind, nid, entry, part, ref in chosen:
            run = ref.getparent()
            if not _v_alone(run, ref):
                raise VRefuse("structure", f"reference {nid} shares its run with other content")
            if any(isinstance(x.tag, str) and x.tag in V_REVISION_TAGS for x in run.iter()) or \
                    any(q.tag in _V_REV_WRAPS for q in run.iterancestors()):
                raise VRefuse("existing-revisions", f"reference {nid} lies in a pending tracked change")
            refs_by_par.setdefault((part, v_par(run)), []).append(run)
            npart = ctx.src.part_of_kind(kind)[0]
            if any(isinstance(x.tag, str) and x.tag in V_REVISION_TAGS for x in entry.iter()):
                raise VRefuse("existing-revisions", f"note {nid} carries pending revisions")
            for q in entry.iter(W + "p"):
                notes.append((npart, q, v_tracked_runs(q)))
        for part in {p for p, _ in refs_by_par}:
            gone = {r for (pp, _), rs in refs_by_par.items() if pp == part for r in rs}
            v_field_cut(root_of(part), lambda r: r in gone)
        for npart in {p for p, _, _ in notes}:
            gone = {r for pp, _, rs in notes if pp == npart for r in rs}
            v_field_cut(root_of(npart), lambda r: r in gone)
    except VRefuse as e:
        errs.append(f"removed_footnotes: {e} — the op must refuse")
        return "removed_footnotes: the op must refuse"
    work = [(part, p, runs, False, True) for (part, p), runs in refs_by_par.items()] + \
        [(npart, q, runs, True, False) for npart, q, runs in notes]
    for part, p, runs, mark, own in work:
        sv, ov = ctx.view(part)
        bi = next(i for i, x in enumerate(sv.pars) if x is p)
        oi = ctx.maps[part].get(bi)
        if oi is None or oi >= len(ov.pars):
            errs.append(f"removed_footnotes: {part} block {bi} has no counterpart in the output")
            continue
        check_tracked_paragraph(p, ov.pars[oi], runs, mark, author, utc, old_ids, f"removed_footnotes {part} block {bi}",
                                errs, own_wrappers=own)
        ctx.verified_blocks.setdefault(part, {})[bi] = oi
    return f"removed_footnotes: {len(chosen)} note(s) and their references deleted (tracked)"


FOOTNOTE_KINDS: Dict[str, Any] = {"removed_footnotes": _removed_footnotes}
