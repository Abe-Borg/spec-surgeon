"""Whole runs and whole paragraphs taken out of a document — transform side (docs/BUILTIN_OPS_BATCH2_DESIGN.md §1.4).
The verifier checks the results item by item (verify/markup.py, verify/edits.py).

**Whole-run removal**: untracked — the run is removed from wherever it sits (a container it leaves empty stays);
tracked — the run is wrapped in a ``w:del`` of its own (new id, author, date) where it stands, its ``w:t`` become
``w:delText`` and its ``w:instrText`` ``w:delInstrText``. Only live runs (in no ``w:del``/``w:moveFrom``) are ever
removed. Revision delta (tracked): ``del`` +1 per run.

**Paragraph deletion** (``filter_tagged_paragraphs``' rules, shared): tracked — every run that is a direct child of the
paragraph goes into its own ``w:del``, the paragraph mark gets a ``w:del``; bookmarks and proofing marks stay where they
are; untracked — the paragraph is removed. ``deletable`` says why one may not be deleted.
"""
from __future__ import annotations

from typing import Any, List, Optional, Sequence

from lxml import etree

from ..ooxml import (RPR_ORDER, RevisionStamp, block_full_text, block_struct_counts, block_text_without,
                     count_revisions, insert_in_order, mark_rpr, to_del_text)
from ..package import W
from .base import BlockDiff, JobException, PlanFragment
from .delete_paragraphs import INERT_P_CHILDREN

HIDING = (W + "del", W + "moveFrom")


def deletable(p, part: str, bi: int, tracked: bool) -> None:
    """delete_paragraphs' refusals, for a paragraph an op would delete."""
    where = f"{part} paragraph {bi}"
    if p.find(f"{W}pPr/{W}sectPr") is not None:
        raise JobException(f"{where} carries a section break; it is not deleted", "section-break", {"where": where})
    if p.find(f".//{W}fldChar") is not None or p.find(f".//{W}fldSimple") is not None:
        raise JobException(f"{where} holds a field; it is not deleted", "field-result", {"where": where})
    if p.find(f".//{W}p") is not None:
        raise JobException(f"{where} holds a text box; it is not deleted", "textbox", {"where": where})
    if tracked:
        if count_revisions(p):
            raise JobException(f"{where} already carries pending revisions; accept or reject them first, or delete "
                               f"untracked", "existing-revisions", {"where": where})
        other = [c for c in p if isinstance(c.tag, str) and c.tag not in (W + "pPr", W + "r") + INERT_P_CHILDREN]
        if other:
            raise JobException(f"{where} holds {[etree.QName(c).localname for c in other]} (a hyperlink, a content "
                               f"control, a comment anchor …); a tracked deletion is written only over plain runs",
                               "structure", {"where": where})


def plan_paragraph_deletion(frag: PlanFragment, part: str, bi: int, p, tracked: bool, note: str) -> None:
    """The predictions of deleting one paragraph (``deletable`` must have passed): tracked — its accepted view is
    empty, ``del`` +1 per direct run, ``del_mark`` +1, the touch; untracked — the block deleted (V2), its revisions
    gone (V4, flagged). The caller adds the whole-part touch for untracked deletions."""
    if tracked:
        runs = [c for c in p if isinstance(c.tag, str) and c.tag == W + "r"]
        frag.rev_delta["del"] = frag.rev_delta.get("del", 0) + len(runs)
        frag.rev_delta["del_mark"] = frag.rev_delta.get("del_mark", 0) + 1
        frag.accepted_views.append({"part": part, "block_index": bi, "accepted_view": ""})
        frag.touches.append((part, str(bi), "text"))
    else:
        for k, v in count_revisions(p).items():
            frag.rev_delta[k] = frag.rev_delta.get(k, 0) - v
            frag.flags.append(f"{part} paragraph {bi}: its pending revisions ({k} ×{v}) go with the deleted paragraph")
        frag.diffs.append(BlockDiff(part=part, block_index=bi, before=block_full_text(p), after=None, note=note))
    if part not in frag.intended_parts:
        frag.intended_parts.append(part)


def delete_paragraph(p, tracked: bool, stamp: Optional[RevisionStamp], next_id: List[int]) -> None:
    if not tracked:
        p.getparent().remove(p)
        return
    mark = etree.Element(W + "del")
    stamp.apply(mark, next_id[0])
    next_id[0] += 1
    insert_in_order(mark_rpr(p, create=True), mark, RPR_ORDER)
    for r in [c for c in p if isinstance(c.tag, str) and c.tag == W + "r"]:
        wrap_deleted(r, stamp, next_id)


def wrap_deleted(r, stamp: RevisionStamp, next_id: List[int]) -> None:
    """Delete one run as a tracked change, in place: a new ``w:del`` around it; its text and field code deleted."""
    d = etree.Element(W + "del")
    stamp.apply(d, next_id[0])
    next_id[0] += 1
    r.addprevious(d)
    to_del_text(r)
    for it in r.findall(W + "instrText"):
        it.tag = W + "delInstrText"
    d.append(r)


def live(r, p) -> bool:
    """The run is live text of its paragraph: no w:del / w:moveFrom around it below ``p``."""
    q = r.getparent()
    while q is not None and q is not p:
        if q.tag in HIDING:
            return False
        q = q.getparent()
    return True


def has_content(r) -> bool:
    return any(isinstance(c.tag, str) and c.tag != W + "rPr" for c in r)


def plan_run_removal(frag: PlanFragment, part: str, bi: int, p, runs: Sequence[Any], tracked: bool,
                     extra: Sequence[Any] = (), note: str = "") -> None:
    """Predict removing whole ``runs`` (and ``extra`` elements — e.g. a simple field taken out with its runs) from
    paragraph ``p``: untracked — the block text and structure without them (a structure-only declaration when only
    structure changes), their revisions gone; tracked — the block text unchanged (text becomes deleted text), the
    accepted view without them, ``del`` +1 per run."""
    gone = list(runs) + list(extra)
    if tracked:
        hidden_now = [q for q in p.iter(*HIDING) if q.getparent() is None or q.getparent().tag != W + "rPr"]
        accepted = block_text_without(p, hidden_now + gone)
        frag.accepted_views.append({"part": part, "block_index": bi, "accepted_view": accepted})
        frag.rev_delta["del"] = frag.rev_delta.get("del", 0) + len(runs)
    else:
        before, after = block_full_text(p), block_text_without(p, gone)
        s_before, s_after = block_struct_counts(p), block_struct_counts(p, gone)
        if before != after or s_before != s_after:
            frag.diffs.append(BlockDiff(part=part, block_index=bi, before=before, after=after,
                                        struct_after=s_after if s_before != s_after else None,
                                        note=note or f"{len(runs)} run(s) removed"))
        for el in gone:
            for k, v in count_revisions(el).items():
                frag.rev_delta[k] = frag.rev_delta.get(k, 0) - v
    frag.touches.append((part, str(bi), "text"))
    if part not in frag.intended_parts:
        frag.intended_parts.append(part)


def remove_runs(runs: Sequence[Any], tracked: bool, stamp: Optional[RevisionStamp], next_id: List[int]) -> None:
    for r in runs:
        if tracked:
            wrap_deleted(r, stamp, next_id)
        elif r.getparent() is not None:
            r.getparent().remove(r)


def shown_text(els: Sequence[Any], limit: int = 80) -> str:
    """The text of some runs as a reader sees it (tabs ⇥, breaks ⏎), clipped — for the plan's list of removed pieces."""
    out = []
    for r in els:
        for c in r.iter():
            if c.tag in (W + "t", W + "delText"):
                out.append(c.text or "")
            elif c.tag in (W + "tab", W + "ptab"):
                out.append("⇥")
            elif c.tag in (W + "br", W + "cr"):
                out.append("⏎")
    s = " ".join("".join(out).split())
    return s if len(s) <= limit else s[:limit - 1] + "…"

