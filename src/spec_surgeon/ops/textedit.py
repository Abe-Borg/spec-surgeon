"""In-paragraph text edits — the core every word-changing op of built-in batch 1 shares (docs/BUILTIN_OPS_BATCH1_DESIGN.md
§0.1; CLAUDE.md "Run text and in-paragraph edits"). The verifier re-implements this definition in verify/edits.py and
checks every edited paragraph item by item: keep the two identical.

**Run text** of a paragraph: its runs (``w:r`` whose every ancestor below the paragraph is a container — ``w:ins``,
``w:del``, ``w:moveFrom``, ``w:moveTo``, ``w:hyperlink``, ``w:smartTag``, ``w:customXml``, ``w:sdt``, ``w:sdtContent``,
``w:fldSimple``, ``w:dir``, ``w:bdo``), in document order, skipping runs inside ``w:del``/``w:moveFrom``; for each run
child in order: ``w:t`` → its characters;
``w:tab``/``w:ptab`` → ``\\t``; ``w:br``/``w:cr`` → ``\\n``; ``w:noBreakHyphen`` → ``-``; ``w:softHyphen`` and
``w:lastRenderedPageBreak`` → nothing (transparent); ``w:rPr``, XML comments and processing instructions → nothing; any
other child (field characters, field code, drawings, symbols, note references …) → ``\\x00``.

An **edit** ``(start, end, text, drop_highlight)`` replaces run-text positions [start, end) by ``text``. It obeys (else
``check_edits`` refuses): start < end, no overlap with another edit of the paragraph; every position in the range is a
character of a ``w:t`` (``structure``); no run holding one of them lies in a field (``field-result``); no other
text-bearing element of the block (``w:delText``, ``w:instrText``, ``m:t`` …) lies between the range's first and last
character (``structure``); tracked: every such run has the same parent — the paragraph, or a ``w:ins`` whose parent is
the paragraph (``nested-container``) —, none carries a ``w:rPrChange`` (``tracked-format``); ``drop_highlight``: every
run holding a character of the range holds only ``w:rPr`` and ``w:t``, and nothing of the paragraph's run text outside it
(``slot-structure``). A transparent element strictly inside the range stays where it is (untracked) or goes into the
``w:del`` with the deleted text around it (tracked).

**Untracked result**: the characters leave their ``w:t``; ``text`` is written where the first one was (that run, its
properties; ``w:highlight`` removed when ``drop_highlight``). A ``w:t`` emptied by the edit, and a run left holding only
its properties, are removed; containers never are. No run is split.

**Tracked result**: every run holding a character of the range is split at the range's edges; the piece inside goes
into its own ``w:del`` (``w:t`` → ``w:delText``); a non-empty ``text`` follows the last of them as one run — a copy of
the first such run's attributes and properties (highlight removed when asked) — in one ``w:ins``. When the runs sit in
someone's pending insertion (their common parent is a ``w:ins`` whose parent is the paragraph), the deletions stay
inside that insertion (Word's grammar: reject-all still removes all of it) and the new ``w:ins`` goes right after it;
whatever followed the range inside it moves to a second ``w:ins`` with the same attributes and a fresh id, after the new
one — only when text is placed (a pure deletion leaves the insertion whole). Revision delta: ``del`` +1 per run the range
touches, ``ins`` +1 per edit that places text, +1 more for each such second insertion.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from lxml import etree

from ..ooxml import FULL_TEXT_TAGS, RevisionStamp, clone_run_shell, paragraphs, set_text, to_del_text
from ..package import W
from .base import BlockDiff, JobException, PlanFragment

OPAQUE = "\x00"
SHOWN = {W + "tab": "\t", W + "ptab": "\t", W + "br": "\n", W + "cr": "\n", W + "noBreakHyphen": "-"}
TRANSPARENT = (W + "softHyphen", W + "lastRenderedPageBreak")
WHAT = {"\t": "a tab", "\n": "a line break", "-": "a non-breaking hyphen",
        OPAQUE: "a field, picture, symbol or note mark"}


CONTAINERS = {W + t for t in ("ins", "del", "moveFrom", "moveTo", "hyperlink", "smartTag", "customXml", "sdt",
                              "sdtContent", "fldSimple", "dir", "bdo")}


def nearest_p(el) -> Optional[Any]:
    q = el.getparent()
    while q is not None and q.tag != W + "p":
        q = q.getparent()
    return q


def _contained(r, p) -> bool:
    """Every ancestor of ``r`` below ``p`` is a container (so ``p`` is its paragraph and it sits in no other run, no
    mc:AlternateContent, no math)."""
    q = r.getparent()
    while q is not None and q is not p:
        if q.tag not in CONTAINERS:
            return False
        q = q.getparent()
    return q is p


def _hidden(r, p) -> bool:
    """Inside a w:del or w:moveFrom below the paragraph (not live text)."""
    q = r.getparent()
    while q is not None and q is not p:
        if q.tag in (W + "del", W + "moveFrom"):
            return True
        q = q.getparent()
    return False


def edit_runs(p) -> List[Any]:
    """The paragraph's runs in document order: every ancestor below ``p`` is a container."""
    return [r for r in p.iter(W + "r") if _contained(r, p)]


@dataclass(frozen=True)
class Edit:
    start: int
    end: int
    text: str
    drop_highlight: bool = False


@dataclass
class Position:
    run: Any
    child: Any
    offset: int            # the character's offset in the child's text (a w:t); -1 for an element position


class RunText:
    """The run text of one paragraph and what stands at each of its positions."""

    def __init__(self, p):
        self.p = p
        chars: List[str] = []
        self.positions: List[Position] = []
        self.runs: List[Any] = []
        self.transparent: List[Tuple[int, Any, Any]] = []      # (run-text index it stands before, run, element)
        for r in edit_runs(p):
            if _hidden(r, p):
                continue
            self.runs.append(r)
            for c in r:
                if not isinstance(c.tag, str) or c.tag == W + "rPr":
                    continue
                if c.tag == W + "t":
                    for k, ch in enumerate(c.text or ""):
                        chars.append(ch)
                        self.positions.append(Position(r, c, k))
                elif c.tag in TRANSPARENT:
                    self.transparent.append((len(chars), r, c))
                else:
                    chars.append(SHOWN.get(c.tag, OPAQUE))
                    self.positions.append(Position(r, c, -1))
        self.text = "".join(chars)

    def runs_in(self, start: int, end: int) -> List[Any]:
        out: List[Any] = []
        for ps in self.positions[start:end]:
            if not out or out[-1] is not ps.run:
                if ps.run not in out:
                    out.append(ps.run)
        return out


def run_text(p) -> str:
    return RunText(p).text


def shown(text: str) -> str:
    """Run text for a message: a field/picture mark as ¤, whitespace collapsed."""
    return " ".join(text.replace(OPAQUE, "¤").split())


def _text_map(p, accepted: bool) -> Tuple[str, Dict[int, int]]:
    """The block text (CLAUDE.md: w:t, w:delText, w:instrText, w:delInstrText, m:t whose nearest w:p is ``p``, document
    order) — or, ``accepted``, its accept-all view (nothing inside w:del / w:moveFrom) — and each element's start."""
    parts: List[str] = []
    starts: Dict[int, int] = {}
    pos = 0
    for el in p.iter(*FULL_TEXT_TAGS):
        if nearest_p(el) is not p:
            continue
        if accepted and _hidden(el, p):
            continue
        s = el.text or ""
        starts[id(el)] = pos
        parts.append(s)
        pos += len(s)
    return "".join(parts), starts


def block_text_map(p) -> Tuple[str, Dict[int, int]]:
    return _text_map(p, False)


def accept_text_map(p) -> Tuple[str, Dict[int, int]]:
    return _text_map(p, True)


def _has_highlight(rpr) -> bool:
    return rpr is not None and rpr.find(W + "highlight") is not None


def check_edits(rt: RunText, edits: Sequence[Edit], tracked: bool, in_field: Set[Any], where: str) -> List[Edit]:
    """The edits of one paragraph, sorted, after every rule of the module docstring (JobException otherwise)."""
    out = sorted(edits, key=lambda e: (e.start, e.end))
    for a, b in zip(out, out[1:]):
        if b.start < a.end:
            raise JobException(f"{where}: two changes overlap in {shown(rt.text[a.start:max(a.end, b.end)])!r}; "
                               f"say which one applies", "ambiguous", {"where": where, "text": shown(rt.text)})
    _, starts = block_text_map(rt.p)
    for e in out:
        if not isinstance(e.text, str) or any(ord(c) < 32 for c in e.text) or "\x7f" in e.text:
            raise JobException(f"{where}: the new text {e.text!r} holds a control character (a tab or line break "
                               f"cannot be typed into a line of text)", "bad-params", {"where": where})
        if not 0 <= e.start < e.end <= len(rt.text):
            raise JobException(f"{where}: an edit outside the paragraph's text ({e.start}, {e.end})", "internal")
        piece = rt.text[e.start:e.end]
        bad = [i for i in range(e.start, e.end) if rt.positions[i].offset < 0]
        if bad:
            raise JobException(f"{where}: {WHAT[rt.text[bad[0]]]} sits inside {shown(piece)!r}; only plain text is "
                               f"changed — change this one in Word", "structure",
                               {"where": where, "text": shown(rt.text)})
        touched = rt.runs_in(e.start, e.end)
        if any(r in in_field for r in touched):
            raise JobException(f"{where}: {shown(piece)!r} is shown by a field (a cross-reference, a document "
                               f"property …); Word would put the field's own result back when it updates fields",
                               "field-result", {"where": where, "text": shown(rt.text)})
        first, last = rt.positions[e.start], rt.positions[e.end - 1]
        b0 = starts[id(first.child)] + first.offset
        b1 = starts[id(last.child)] + last.offset + 1
        if b1 - b0 != e.end - e.start:
            raise JobException(f"{where}: deleted text or a field code sits inside {shown(piece)!r} (a pending "
                               f"tracked change in the middle of it); accept or reject it first", "structure",
                               {"where": where, "text": shown(rt.text)})
        if tracked:
            parent = touched[0].getparent()
            plain = parent is rt.p or (parent.tag == W + "ins" and parent.getparent() is rt.p)
            for r in touched:
                if r.getparent() is not parent or not plain:
                    raise JobException(f"{where}: {shown(piece)!r} runs into {etree.QName(r.getparent()).localname} (a "
                                       f"hyperlink, a content control, a moved block or the edge of a pending tracked "
                                       f"change); a tracked change is written only in plain runs, or inside one pending "
                                       f"insertion", "nested-container", {"where": where, "text": shown(rt.text)})
                if r.find(W + "rPr/" + W + "rPrChange") is not None:
                    raise JobException(f"{where}: {shown(piece)!r} carries a pending tracked formatting change; "
                                       f"splitting it would duplicate that revision — accept or reject it first",
                                       "tracked-format", {"where": where, "text": shown(rt.text)})
        if e.drop_highlight:
            for r in touched:
                if any(not (e.start <= i < e.end) for i, ps in enumerate(rt.positions) if ps.run is r) or any(
                        isinstance(c.tag, str) and c.tag not in (W + "rPr", W + "t") for c in r):
                    raise JobException(f"{where}: the highlighted run also holds something besides "
                                       f"{shown(piece)!r}", "slot-structure", {"where": where, "text": shown(rt.text)})
    return out


def pending_insertion(run, p) -> Optional[Any]:
    """The run's parent when it is someone's pending insertion directly in the paragraph."""
    parent = run.getparent()
    return parent if parent is not None and parent.tag == W + "ins" and parent.getparent() is p else None


def tail_after(last: Position) -> bool:
    """Something of the pending insertion follows the range's last character: text after it in its w:t, a node after
    that w:t in its run, or a node after its run in the insertion."""
    return (last.offset + 1 < len(last.child.text or "") or last.child.getnext() is not None
            or last.run.getnext() is not None)


def predict(rt: RunText, edits: Sequence[Edit], tracked: bool) -> Tuple[str, str, Optional[str], Dict[str, int]]:
    """(block text before, block text after, accepted view after (tracked) or None, revision delta) — computed from the
    SOURCE paragraph, without running the edit. ``edits`` must have passed ``check_edits``."""
    before, bstarts = block_text_map(rt.p)
    acc, astarts = accept_text_map(rt.p)
    after, accepted = before, acc
    delta = {"ins": 0, "del": 0}
    for e in sorted(edits, key=lambda e: -e.start):
        first, last = rt.positions[e.start], rt.positions[e.end - 1]
        b0, b1 = bstarts[id(first.child)] + first.offset, bstarts[id(last.child)] + last.offset + 1
        a0, a1 = astarts[id(first.child)] + first.offset, astarts[id(last.child)] + last.offset + 1
        if tracked:
            after = after[:b1] + e.text + after[b1:]          # the old text stays (as w:delText); the new follows it
            accepted = accepted[:a0] + e.text + accepted[a1:]
            delta["del"] += len(rt.runs_in(e.start, e.end))
            delta["ins"] += 1 if e.text else 0
            if e.text and pending_insertion(first.run, rt.p) is not None and tail_after(last):
                delta["ins"] += 1                           # the pending insertion's rest, after the new text
        else:
            after = after[:b0] + e.text + after[b1:]
    return before, after, (accepted if tracked else None), delta


def _drop_highlight(rpr) -> None:
    if rpr is None:
        return
    for h in rpr.findall(W + "highlight"):
        rpr.remove(h)
    if not len(rpr) and not rpr.attrib and rpr.getparent() is not None:
        rpr.getparent().remove(rpr)


def apply_untracked(p, edits: Sequence[Edit]) -> None:
    rt = RunText(p)
    touched_t: List[Any] = []
    touched_r: List[Any] = []
    for e in sorted(edits, key=lambda e: -e.start):
        groups: List[List[Any]] = []                     # [w:t, lo, hi] in document order
        for ps in rt.positions[e.start:e.end]:
            if groups and groups[-1][0] is ps.child:
                groups[-1][2] = ps.offset + 1
            else:
                groups.append([ps.child, ps.offset, ps.offset + 1])
        for k in range(len(groups) - 1, -1, -1):
            t, lo, hi = groups[k]
            txt = t.text or ""
            set_text(t, txt[:lo] + (e.text if k == 0 else "") + txt[hi:])
            if t not in touched_t:
                touched_t.append(t)
        if e.drop_highlight:
            _drop_highlight(rt.positions[e.start].run.find(W + "rPr"))
        for r in rt.runs_in(e.start, e.end):
            if r not in touched_r:
                touched_r.append(r)
    for t in touched_t:
        if not (t.text or "") and t.getparent() is not None:
            t.getparent().remove(t)
    for r in touched_r:
        if r.getparent() is not None and all(not isinstance(c.tag, str) or c.tag == W + "rPr" for c in r):
            r.getparent().remove(r)


def _new_text(like, text: str):
    el = etree.Element(like.tag)
    set_text(el, text)
    return el


def _split_run(r, inside: List[Position], first_run: bool, last_run: bool) -> Tuple[Optional[Any], Any, Optional[Any]]:
    """(before, inside, after) runs of ``r`` cut at the edges of ``inside`` (its positions in the range, all w:t
    characters); each a copy of r's attributes and properties. A run the range enters from an earlier run has nothing
    before its first range character but transparent elements, which lie inside the range and go with it; likewise
    after the last range character of a run the range leaves for a later one."""
    first, last = inside[0], inside[-1]
    groups: Dict[str, List[Any]] = {"before": [], "inside": [], "after": []}
    state = "before"
    for c in [c for c in r if not (isinstance(c.tag, str) and c.tag == W + "rPr")]:
        if state == "before" and c is not first.child:
            groups["before" if first_run else "inside"].append(c)
            continue
        if state == "after":
            groups["after" if last_run else "inside"].append(c)
            continue
        txt = c.text or ""
        lo = first.offset if c is first.child else 0
        if c is last.child:
            if lo > 0:
                groups["before"].append(_new_text(c, txt[:lo]))
            groups["inside"].append(_new_text(c, txt[lo:last.offset + 1]))
            if last.offset + 1 < len(txt):
                groups["after"].append(_new_text(c, txt[last.offset + 1:]))
            state = "after"
        else:
            if lo > 0:
                groups["before"].append(_new_text(c, txt[:lo]))
            groups["inside"].append(_new_text(c, txt[lo:]) if lo > 0 else c)
            state = "inside"
    out = []
    for key in ("before", "inside", "after"):
        if not groups[key]:
            out.append(None)
            continue
        nr = clone_run_shell(r)
        for c in groups[key]:
            nr.append(c)
        out.append(nr)
    return out[0], out[1], out[2]


def apply_tracked(p, edits: Sequence[Edit], stamp: RevisionStamp, next_id: List[int]) -> None:
    for e in sorted(edits, key=lambda e: -e.start):
        rt = RunText(p)                 # a later edit changed only positions after this one's start
        pos = rt.positions[e.start:e.end]
        runs = rt.runs_in(e.start, e.end)
        pending = pending_insertion(runs[0], p)
        shell = clone_run_shell(runs[0])
        if e.drop_highlight:
            _drop_highlight(shell.find(W + "rPr"))
        last_del = None
        for n, r in enumerate(runs):
            before, inside, after = _split_run(r, [ps for ps in pos if ps.run is r], n == 0, n == len(runs) - 1)
            parent = r.getparent()
            idx = parent.index(r)
            parent.remove(r)
            d = etree.Element(W + "del")
            stamp.apply(d, next_id[0])
            next_id[0] += 1
            to_del_text(inside)
            d.append(inside)
            for k, el in enumerate([x for x in (before, d, after) if x is not None]):
                parent.insert(idx + k, el)
            last_del = d
        if e.text:
            ins = etree.Element(W + "ins")
            stamp.apply(ins, next_id[0])
            next_id[0] += 1
            t = etree.SubElement(shell, W + "t")
            set_text(t, e.text)
            ins.append(shell)
            if pending is None:
                last_del.addnext(ins)
                continue
            # inside someone's pending insertion: the deletions stay in it; the new text follows it, and what followed
            # the range in it moves to a second insertion with its attributes and a fresh id, after the new text
            tail, node = [], last_del.getnext()
            while node is not None:
                tail.append(node)
                node = node.getnext()
            if tail:
                rest = etree.Element(W + "ins")
                for k, v in pending.attrib.items():
                    rest.set(k, v)
                rest.set(W + "id", str(next_id[0]))
                next_id[0] += 1
                for node in tail:
                    rest.append(node)
                pending.addnext(rest)
            pending.addnext(ins)


# ---- what an op does with it ------------------------------------------------------------------------

def plan_paragraph(frag: PlanFragment, part: str, bi: int, p, edits: Sequence[Edit], tracked: bool,
                   in_field: Set[Any], where: str, note: str = "") -> List[Edit]:
    """Check the paragraph's edits and put their predictions in the fragment: the block's text diff (V2) and, tracked,
    its accepted view (V8), the revision delta (V4), the touch, the intended part. Returns the checked edits."""
    rt = RunText(p)
    edits = [e for e in edits if e.drop_highlight or rt.text[e.start:e.end] != e.text]   # a no-op edit is no edit
    if not edits:
        return []
    edits = check_edits(rt, edits, tracked, in_field, where)
    before, after, accepted, delta = predict(rt, edits, tracked)
    if tracked:
        if after != before:
            frag.diffs.append(BlockDiff(part=part, block_index=bi, before=before, after=after, accepted_view=accepted,
                                        note=note or f"{len(edits)} change(s), tracked"))
        else:
            frag.accepted_views.append({"part": part, "block_index": bi, "accepted_view": accepted})
    elif after != before:
        frag.diffs.append(BlockDiff(part=part, block_index=bi, before=before, after=after,
                                    note=note or f"{len(edits)} change(s), untracked"))
    for k, v in delta.items():
        if v:
            frag.rev_delta[k] = frag.rev_delta.get(k, 0) + v
    frag.touches.append((part, str(bi), "text"))
    if part not in frag.intended_parts:
        frag.intended_parts.append(part)
    return edits


def edits_to_data(edits: Sequence[Edit]) -> List[List[Any]]:
    return [[e.start, e.end, e.text, e.drop_highlight] for e in edits]


def edits_from_data(rows: Sequence[Sequence[Any]]) -> List[Edit]:
    return [Edit(int(a), int(b), str(t), bool(h)) for a, b, t, h in rows]


def apply_part_edits(root, by_block: Dict[str, List[List[Any]]], tracked: bool, stamp: Optional[RevisionStamp],
                     next_id: List[int]) -> None:
    """Apply ``{block index: edits}`` (from the plan's transform data) to one part's tree, highest block first."""
    pars = paragraphs(root)
    for bi in sorted(by_block, key=int, reverse=True):
        p = pars[int(bi)]
        edits = edits_from_data(by_block[bi])
        if tracked:
            apply_tracked(p, edits, stamp, next_id)
        else:
            apply_untracked(p, edits)
