"""What a renumbering leaves stale — shared by the numbering ops' flags (built-in batch 5: convert_numbering,
set_list_level, audit_numbering's fix; docs/BUILTIN_OPS_BATCH5_DESIGN.md §1, §3, §4).

When a paragraph's shown number changes, two things in the document still say the old one, and neither is edited (a
field's cached result is Word's to update; a written reference is content):

* **cross-reference fields** — a ``REF`` / ``PAGEREF`` / ``NOTEREF`` field (``fields.fields_of``: complex and simple)
  whose bookmark (its instruction's second word) lies in such a paragraph (``bookmark_paragraph``: the paragraph holding
  the ``w:bookmarkStart``, else — a bookmark between blocks — the next paragraph after it); its shown number is a cached
  result that goes stale until fields are updated (``stale_fields``);
* **written references** — every occurrence, in ANOTHER paragraph's run text, of the paragraph's OLD number as a whole
  token (``number_rx``: not inside a longer number or word) — "as specified in 1.05" (``written_references``).

Both are flagged with the wording convert_numbering established (``stale_fields_flag``, ``written_references_flag``).
Advisory: these change nothing and have no verifier twin.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from ..package import W
from .fields import fields_of
from .numbering_convert import shown
from .textedit import run_text

REF_FIELDS = ("REF", "PAGEREF", "NOTEREF")


def bookmark_paragraph(root, name: str):
    """The paragraph a bookmark named ``name`` starts in (a bookmark between blocks: the next paragraph), else None."""
    for bs in root.iter(W + "bookmarkStart"):
        if bs.get(W + "name") != name:
            continue
        q = bs.getparent()
        while q is not None and q.tag != W + "p":
            q = q.getparent()
        if q is not None:
            return q
        for sib in bs.itersiblings():
            if sib.tag == W + "p":
                return sib
            inner = next(sib.iter(W + "p"), None)
            if inner is not None:
                return inner
        return None
    return None


def number_rx(number: str):
    """An old rendered number as a written reference: not inside a longer number or word."""
    return re.compile(r"(?<![\w.])" + re.escape(number) + r"(?!\w)(?!\.\w)")


def stale_fields(root, pars: Sequence, changed: Iterable[int]) -> List[str]:
    """One entry per REF/PAGEREF/NOTEREF field whose bookmark lies in a paragraph of ``changed`` (ordinals in ``pars``)."""
    targets: Set[int] = set(changed)
    if not targets:
        return []
    where = {id(p): i for i, p in enumerate(pars)}
    out: List[str] = []
    for f in fields_of(root):
        if f.type not in REF_FIELDS:
            continue
        words = f.instruction.split()
        if len(words) < 2:
            continue
        target = bookmark_paragraph(root, words[1])
        i = where.get(id(target)) if target is not None else None
        if i in targets:
            holder = f.paragraph()
            out.append(f"{f.type} {words[1]} → ‘{shown(run_text(pars[i]), 30)}’"
                       + (f" in ‘{shown(run_text(holder), 30)}’" if holder is not None else ""))
    return out


def written_references(pars: Sequence, changes: Iterable[Tuple[int, Optional[str], Optional[str]]]) -> List[str]:
    """One entry per changed paragraph (``(ordinal, old number, new number)``) whose OLD number another paragraph's run
    text writes."""
    texts = [run_text(p) for p in pars]
    out: List[str] = []
    for index, old, new in changes:
        old = (old or "").strip()
        if not old or old == (new or "").strip():
            continue
        rx = number_rx(old)
        hits = [i for i, t in enumerate(texts) if i != index and rx.search(t)]
        if hits:
            out.append(f"{old!r} (now {new!r}) in " + ", ".join(f"‘{shown(texts[i], 40)}’" for i in hits[:3])
                       + (" …" if len(hits) > 3 else ""))
    return out


def stale_fields_flag(entries: Sequence[str], what: str = "converted paragraphs") -> Optional[str]:
    if not entries:
        return None
    return (f"{len(entries)} cross-reference field(s) point at {what}; their shown number is a cached result that goes "
            f"stale until fields are updated in Word: " + "; ".join(entries[:6]) + (" …" if len(entries) > 6 else ""))


def written_references_flag(entries: Sequence[str]) -> Optional[str]:
    if not entries:
        return None
    return ("written references to old numbers that change (content: flagged, never edited): "
            + "; ".join(entries[:8]) + (" …" if len(entries) > 8 else ""))
