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

**Tabs** (built-in batch 5, ``convert_typed_numbers`` — docs/BUILTIN_OPS_BATCH5_DESIGN.md §2.3): behind the explicit keyword
``tabs=True`` (``check_edits``, ``plan_paragraph``, ``apply_part_edits``; off by default, so every other op keeps the strict
core) a PURE deletion (``text == ""``) may also cover ``w:tab`` run children — never a ``w:ptab``, a break or any other
element. The "no other text-bearing element inside" rule is then judged in document order between the range's first and
last position (a tab may stand at either edge): no ``w:t``/``w:delText``/``w:instrText``/``w:delInstrText``/``m:t`` of the
block other than the range's own ``w:t`` lies strictly between them. Untracked: the ``w:tab`` is removed with the
characters (its run removed when only ``w:rPr`` is left; V3: the plan declares the block's ``struct_after``); tracked: it
goes into the ``w:del`` with the deleted characters around it (a run holding only the tab gets its own ``w:del``).

**Tabs and line breaks in the new text** (built-in batch 6 — docs/BUILTIN_OPS_BATCH6_DESIGN.md §4; ``replace_text``
writes its replacement, ``insert_paragraph`` its new runs with ``text_children`` / ``place_text``): behind the explicit
keyword ``breaks=True`` (``check_edits``, ``predict``, ``plan_paragraph``, ``apply_untracked``, ``apply_tracked``,
``apply_part_edits``; off by default, so every other op on this core keeps refusing a control character) ``text`` may
hold ``\\t`` and ``\\n``: each is written as a ``w:tab`` / ``w:br`` run child where it stands among the characters, which
go into ``w:t`` elements of their own (``text_children``; an empty stretch is no element). They are structure, not block
text (``shown_text``): the block's text after the edit has them left out, and the block's ``struct_after`` declares
them (V3, ``placed_counts``). Any other control character is still refused.

**Untracked result**: the characters leave their ``w:t``; ``text`` is written where the first one was (that run, its
properties; ``w:highlight`` removed when ``drop_highlight``) — with ``breaks``, its tabs and line breaks follow that
``w:t`` in the same run, each stretch after one in a new ``w:t`` (the document's own characters after the range join
the last stretch; a ``w:t`` left empty goes). A ``w:t`` emptied by the edit, and a run left holding only its
properties, are removed; containers never are. No run is split.

**Tracked result**: every run holding a character of the range is split at the range's edges; the piece inside goes
into its own ``w:del`` (``w:t`` → ``w:delText``); a non-empty ``text`` follows the last of them as one run — a copy of
the first such run's attributes and properties (highlight removed when asked), its children ``text_children(text)`` —
in one ``w:ins``. When the runs sit in someone's pending insertion (their common parent is a ``w:ins`` whose parent is
the paragraph), the deletions stay inside that insertion (Word's grammar: reject-all still removes all of it) and the
new ``w:ins`` goes right after it; whatever followed the range inside it moves to a second ``w:ins`` with the same
attributes and a fresh id, after the new one — only when text is placed (a pure deletion leaves the insertion whole).
Revision delta: ``del`` +1 per run the range touches, ``ins`` +1 per edit that places text, +1 more for each such second
insertion.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from lxml import etree

from ..ooxml import (FULL_TEXT_TAGS, RevisionStamp, block_struct_counts, clone_run_shell, paragraphs, set_text,
                     to_del_text)
from ..package import W
from .base import BlockDiff, JobException, PlanFragment

OPAQUE = "\x00"
SHOWN = {W + "tab": "\t", W + "ptab": "\t", W + "br": "\n", W + "cr": "\n", W + "noBreakHyphen": "-"}
TRANSPARENT = (W + "softHyphen", W + "lastRenderedPageBreak")
WHAT = {"\t": "a tab", "\n": "a line break", "-": "a non-breaking hyphen",
        OPAQUE: "a field, picture, symbol or note mark"}
# the characters of a placed text that are written as run children (``breaks=True``): a tab, a line break
BREAKS = {"\t": "tab", "\n": "br"}


def text_children(text: str) -> List[Any]:
    """The run children that show ``text``: each stretch of characters a ``w:t`` (``set_text``: edge whitespace
    preserved), each ``\\t`` a ``w:tab``, each ``\\n`` a ``w:br`` (a line break in the same paragraph); an empty stretch
    is no element."""
    out: List[Any] = []
    for k, piece in enumerate(_stretches(text)):
        if k % 2:
            out.append(etree.Element(W + BREAKS[piece]))
        elif piece:
            t = etree.Element(W + "t")
            set_text(t, piece)
            out.append(t)
    return out


def _stretches(text: str) -> List[str]:
    """``text`` cut at its tabs and line breaks: stretch, break, stretch, … (always an odd count; stretches may be
    empty)."""
    out, cur = [], []
    for ch in text:
        if ch in BREAKS:
            out += ["".join(cur), ch]
            cur = []
        else:
            cur.append(ch)
    return out + ["".join(cur)]


def place_text(t, head: str, text: str, tail: str) -> None:
    """``t`` (a ``w:t``) shows ``head + text + tail``, the tabs and line breaks of ``text`` — never of ``head`` or ``tail``,
    the document's own characters — written as run children after it (``text_children``): ``t`` keeps ``head`` and the
    first stretch, ``tail`` joins the last one; a ``t`` left empty goes."""
    pieces = _stretches(text)
    if len(pieces) == 1:
        set_text(t, head + text + tail)
        return
    set_text(t, head + pieces[0])
    anchor = t
    for k in range(1, len(pieces)):
        if k % 2:
            el = etree.Element(W + BREAKS[pieces[k]])
        else:
            chars = pieces[k] + (tail if k == len(pieces) - 1 else "")
            if not chars:
                continue
            el = etree.Element(W + "t")
            set_text(el, chars)
        anchor.addnext(el)
        anchor = el
    if not (t.text or "") and t.getparent() is not None:
        t.getparent().remove(t)


def shown_text(text: str) -> str:
    """The block text a placed ``text`` adds (CLAUDE.md "Block-text definition"): its tabs and line breaks left out —
    they are structural children, counted by V3."""
    return "".join(ch for ch in text if ch not in BREAKS)


def placed_counts(text: str) -> Dict[str, int]:
    """The structural children a placed ``text`` adds (``breaks=True``): {"tab": n, "br": m}, zero counts left out."""
    out: Dict[str, int] = {}
    for ch in text:
        if ch in BREAKS:
            out[BREAKS[ch]] = out.get(BREAKS[ch], 0) + 1
    return out


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


def is_tab_position(ps: Position) -> bool:
    """A run-text position that is a ``w:tab`` run child (an element position — never a ``w:ptab``)."""
    return ps.offset < 0 and ps.child.tag == W + "tab"


def _between_ok(rt: RunText, e: Edit) -> bool:
    """With tabs: no text-bearing element of the block (``FULL_TEXT_TAGS`` whose nearest paragraph is the block) lies
    strictly between the range's first and last position in document order, other than the range's own ``w:t``."""
    order = {id(el): i for i, el in enumerate(rt.p.iter())}
    pos = rt.positions[e.start:e.end]
    lo, hi = order[id(pos[0].child)], order[id(pos[-1].child)]
    own = {id(ps.child) for ps in pos if ps.offset >= 0}
    for el in rt.p.iter(*FULL_TEXT_TAGS):
        if nearest_p(el) is rt.p and lo < order[id(el)] < hi and id(el) not in own:
            return False
    return True


def control_characters(text: str, breaks: bool = False) -> List[str]:
    """The control characters of a placed text that no run child shows: every one, or — ``breaks`` — every one but a
    tab and a line break (written as ``w:tab`` / ``w:br``)."""
    return [c for c in text if (ord(c) < 32 or c == "\x7f") and not (breaks and c in BREAKS)]


def check_edits(rt: RunText, edits: Sequence[Edit], tracked: bool, in_field: Set[Any], where: str,
                tabs: bool = False, breaks: bool = False) -> List[Edit]:
    """The edits of one paragraph, sorted, after every rule of the module docstring (JobException otherwise).
    ``tabs``: a pure deletion may cover ``w:tab`` run children; ``breaks``: the new text may hold tabs and line breaks
    (see the module docstring)."""
    out = sorted(edits, key=lambda e: (e.start, e.end))
    for a, b in zip(out, out[1:]):
        if b.start < a.end:
            raise JobException(f"{where}: two changes overlap in {shown(rt.text[a.start:max(a.end, b.end)])!r}; "
                               f"say which one applies", "ambiguous", {"where": where, "text": shown(rt.text)})
    _, starts = block_text_map(rt.p)
    for e in out:
        if not isinstance(e.text, str) or control_characters(e.text, breaks):
            raise JobException(f"{where}: the new text {e.text!r} holds a control character"
                               + (" other than a tab or a line break" if breaks else
                                  " (a tab or line break cannot be typed into a line of text)"),
                               "bad-params", {"where": where})
        if not 0 <= e.start < e.end <= len(rt.text):
            raise JobException(f"{where}: an edit outside the paragraph's text ({e.start}, {e.end})", "internal")
        piece = rt.text[e.start:e.end]
        bad = [i for i in range(e.start, e.end) if rt.positions[i].offset < 0
               and not (tabs and not e.text and is_tab_position(rt.positions[i]))]
        if bad:
            what = ("a positional tab (w:ptab)" if rt.positions[bad[0]].child.tag == W + "ptab"
                    else WHAT[rt.text[bad[0]]])
            raise JobException(f"{where}: {what} sits inside {shown(piece)!r}; only plain text is "
                               f"changed — change this one in Word", "structure",
                               {"where": where, "text": shown(rt.text)})
        touched = rt.runs_in(e.start, e.end)
        if any(r in in_field for r in touched):
            raise JobException(f"{where}: {shown(piece)!r} is shown by a field (a cross-reference, a document "
                               f"property …); Word would put the field's own result back when it updates fields",
                               "field-result", {"where": where, "text": shown(rt.text)})
        if any(ps.offset < 0 for ps in rt.positions[e.start:e.end]):
            inside_ok = _between_ok(rt, e)          # tabs=True: a tab may stand at an edge of the range
        else:
            first, last = rt.positions[e.start], rt.positions[e.end - 1]
            b0 = starts[id(first.child)] + first.offset
            b1 = starts[id(last.child)] + last.offset + 1
            inside_ok = b1 - b0 == e.end - e.start
        if not inside_ok:
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
    SOURCE paragraph, without running the edit. ``edits`` must have passed ``check_edits``. A tab or line break of a new
    text (``breaks=True``) is no block text (``shown_text``)."""
    before, bstarts = block_text_map(rt.p)
    acc, astarts = accept_text_map(rt.p)
    after, accepted = before, acc
    delta = {"ins": 0, "del": 0}
    for e in sorted(edits, key=lambda e: -e.start):
        chars = [ps for ps in rt.positions[e.start:e.end] if ps.offset >= 0]   # (tabs=True: a w:tab is no block text)
        if not chars:                               # only tabs: a pure deletion of no block text
            delta["del"] += len(rt.runs_in(e.start, e.end)) if tracked else 0
            continue
        first, last = chars[0], chars[-1]
        b0, b1 = bstarts[id(first.child)] + first.offset, bstarts[id(last.child)] + last.offset + 1
        a0, a1 = astarts[id(first.child)] + first.offset, astarts[id(last.child)] + last.offset + 1
        text = shown_text(e.text)
        if tracked:
            after = after[:b1] + text + after[b1:]            # the old text stays (as w:delText); the new follows it
            accepted = accepted[:a0] + text + accepted[a1:]
            delta["del"] += len(rt.runs_in(e.start, e.end))
            delta["ins"] += 1 if e.text else 0
            if e.text and pending_insertion(first.run, rt.p) is not None and tail_after(last):
                delta["ins"] += 1                           # the pending insertion's rest, after the new text
        else:
            after = after[:b0] + text + after[b1:]
    return before, after, (accepted if tracked else None), delta


def _drop_highlight(rpr) -> None:
    if rpr is None:
        return
    for h in rpr.findall(W + "highlight"):
        rpr.remove(h)
    if not len(rpr) and not rpr.attrib and rpr.getparent() is not None:
        rpr.getparent().remove(rpr)


def _no_elements(rt: RunText, e: Edit, tabs: bool) -> None:
    """Apply's own guard: an element position in a range is a ``w:tab`` of a pure deletion planned with ``tabs``."""
    for ps in rt.positions[e.start:e.end]:
        if ps.offset < 0 and not (tabs and not e.text and is_tab_position(ps)):
            raise JobException(f"an edit covers {etree.QName(ps.child).localname}, which this op did not plan to "
                               f"remove", "internal")


def _no_controls(e: Edit, breaks: bool) -> None:
    """Apply's own guard: a control character in a new text is a tab or a line break, planned with ``breaks``."""
    if control_characters(e.text, breaks):
        raise JobException(f"the new text {e.text!r} holds a control character this op did not plan to write",
                           "internal")


def apply_untracked(p, edits: Sequence[Edit], tabs: bool = False, breaks: bool = False) -> None:
    rt = RunText(p)
    touched_t: List[Any] = []
    touched_r: List[Any] = []
    gone_tabs: List[Any] = []
    for e in sorted(edits, key=lambda e: -e.start):
        _no_elements(rt, e, tabs)
        _no_controls(e, breaks)
        groups: List[List[Any]] = []                     # [w:t, lo, hi] in document order
        for ps in rt.positions[e.start:e.end]:
            if ps.offset < 0:                            # a w:tab (tabs=True): removed with the characters
                gone_tabs.append(ps.child)
                continue
            if groups and groups[-1][0] is ps.child:
                groups[-1][2] = ps.offset + 1
            else:
                groups.append([ps.child, ps.offset, ps.offset + 1])
        for k in range(len(groups) - 1, -1, -1):
            t, lo, hi = groups[k]
            txt = t.text or ""
            # the new text where the first character was (breaks=True: its tabs and line breaks after that w:t)
            place_text(t, txt[:lo], e.text if k == 0 else "", txt[hi:])
            if t not in touched_t:
                touched_t.append(t)
        if e.drop_highlight:
            _drop_highlight(rt.positions[e.start].run.find(W + "rPr"))
        for r in rt.runs_in(e.start, e.end):
            if r not in touched_r:
                touched_r.append(r)
    for tab in gone_tabs:
        if tab.getparent() is not None:
            tab.getparent().remove(tab)
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
        if c.tag != W + "t":
            # an element between the range's characters (a transparent mark), or — tabs=True — a w:tab position of the
            # range (it may be the first or the last): inside, whole
            groups["inside"].append(c)
            state = "after" if c is last.child else "inside"
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


def apply_tracked(p, edits: Sequence[Edit], stamp: RevisionStamp, next_id: List[int], tabs: bool = False,
                  breaks: bool = False) -> None:
    for e in sorted(edits, key=lambda e: -e.start):
        rt = RunText(p)                 # a later edit changed only positions after this one's start
        _no_elements(rt, e, tabs)
        _no_controls(e, breaks)
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
            for el in text_children(e.text):        # one w:t — breaks=True: with its tabs and line breaks
                shell.append(el)
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

def struct_after_edits(p, rt: RunText, edits: Sequence[Edit], tracked: bool) -> Optional[Dict[str, int]]:
    """The block's structural-children counts after the edits (V3), or None when they stay as they are: the tabs a
    pure deletion takes leave the count untracked (tracked, they stay inside the w:del); the tabs and line breaks a
    new text places (``breaks=True``) join it either way."""
    gone_tabs = [ps.child for e in edits for ps in rt.positions[e.start:e.end] if ps.offset < 0] if not tracked else []
    placed: Dict[str, int] = {}
    for e in edits:
        for k, n in placed_counts(e.text).items():
            placed[k] = placed.get(k, 0) + n
    if not gone_tabs and not placed:
        return None
    out = block_struct_counts(p, gone_tabs)
    for k, n in placed.items():
        out[k] = out.get(k, 0) + n
    return {k: v for k, v in out.items() if v}


def plan_paragraph(frag: PlanFragment, part: str, bi: int, p, edits: Sequence[Edit], tracked: bool,
                   in_field: Set[Any], where: str, note: str = "", tabs: bool = False,
                   author: Optional[str] = None, breaks: bool = False) -> List[Edit]:
    """Check the paragraph's edits and put their predictions in the fragment: the block's text diff (V2) and, tracked,
    its accepted view (V8), the revision delta (V4), the touch, the intended part and an independent item-preservation
    assertion (V10). Custom tracked methods supply ``author`` explicitly. Returns the checked edits.
    ``tabs``: pure deletions may cover ``w:tab`` run children; untracked, the tabs they remove are declared as the
    block's ``struct_after`` (V3). ``breaks``: a new text may hold tabs and line breaks; the ones it places are
    declared in ``struct_after`` too (tracked and untracked)."""
    rt = RunText(p)
    edits = [e for e in edits if e.drop_highlight or rt.text[e.start:e.end] != e.text]   # a no-op edit is no edit
    if not edits:
        return []
    edits = check_edits(rt, edits, tracked, in_field, where, tabs=tabs, breaks=breaks)
    before, after, accepted, delta = predict(rt, edits, tracked)
    struct_after = struct_after_edits(p, rt, edits, tracked)
    if tracked:
        if after != before or struct_after is not None:
            # (only a tab or a line break placed: a structure-only declaration — V2 leaves it out, V3 checks it)
            frag.diffs.append(BlockDiff(part=part, block_index=bi, before=before, after=after, accepted_view=accepted,
                                        struct_after=struct_after, note=note or f"{len(edits)} change(s), tracked"))
        if after == before:
            frag.accepted_views.append({"part": part, "block_index": bi, "accepted_view": accepted})
    elif after != before or struct_after is not None:
        frag.diffs.append(BlockDiff(part=part, block_index=bi, before=before, after=after, struct_after=struct_after,
                                    note=note or f"{len(edits)} change(s), untracked"))
    for k, v in delta.items():
        if v:
            frag.rev_delta[k] = frag.rev_delta.get(k, 0) + v
    frag.touches.append((part, str(bi), "text"))
    if part not in frag.intended_parts:
        frag.intended_parts.append(part)
    frag.property_assertions.append({
        "kind": "paragraph_edits", "part": part, "block_index": bi,
        "edits": [[e.start, e.end, e.text, e.drop_highlight] for e in edits],
        "tracked": tracked, "tabs": tabs, "author": author or frag.params.get("author"),
        **({"breaks": True} if breaks else {}),
    })
    return edits


def edits_to_data(edits: Sequence[Edit]) -> List[List[Any]]:
    return [[e.start, e.end, e.text, e.drop_highlight] for e in edits]


def edits_from_data(rows: Sequence[Sequence[Any]]) -> List[Edit]:
    return [Edit(int(a), int(b), str(t), bool(h)) for a, b, t, h in rows]


def apply_part_edits(root, by_block: Dict[str, List[List[Any]]], tracked: bool, stamp: Optional[RevisionStamp],
                     next_id: List[int], tabs: bool = False, breaks: bool = False) -> None:
    """Apply ``{block index: edits}`` (from the plan's transform data) to one part's tree, highest block first.
    ``tabs``: the edits were planned with ``tabs=True`` (pure deletions covering ``w:tab``); without it an element in a
    range is an internal error. ``breaks``: planned with ``breaks=True`` (tabs and line breaks in a new text); without
    it a control character in a new text is an internal error."""
    pars = paragraphs(root)
    for bi in sorted(by_block, key=int, reverse=True):
        p = pars[int(bi)]
        edits = edits_from_data(by_block[bi])
        # (the strict core's call is unchanged)
        more = {**({"tabs": True} if tabs else {}), **({"breaks": True} if breaks else {})}
        if tracked:
            apply_tracked(p, edits, stamp, next_id, **more)
        else:
            apply_untracked(p, edits, **more)
