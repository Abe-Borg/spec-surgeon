"""V10 kind ``resolved_changes`` — resolve_tracked_changes, verifier side (docs/BUILTIN_OPS_BATCH2_DESIGN.md §4; CLAUDE.md
"Resolving tracked changes"), independent of ops/revisions.py (rule 1): this module's own reading of which tracked
changes are chosen, how moves group, what accepting or rejecting does and when a paragraph may go. It REBUILDS every
SOURCE part in scope from those decisions (a new tree, node by node — the transform edits its tree in place) and compares
the rebuilt part whole with the output part; the revision counts that vanish (per V4 kind) and the number chosen must be
the plan's. Any condition under which the op must refuse is an error here.

The definition, as this module reads it: a revision element (ins, del, moveFrom, moveTo, the property changes, the cell
revisions) outside any property change's snapshot; ins/del/moveFrom/moveTo under a w:rPr are a paragraph mark's when the
rPr belongs to a w:pPr (an equation's otherwise), under w:trPr a row's, under w:numPr a numbering marker's, anywhere else
content. Chosen: author in the list (casefolded, whitespace collapsed) and wall-clock date in range (no readable date
under a date filter: not chosen). A move piece takes the choice of the innermost open move range of its kind (scanned in
document order, ranges paired by id); the ranges sharing a name and their pieces are chosen whole or the op must refuse.
Accept keeps ins/moveTo content and drops del/moveFrom with theirs; reject the reverse, an unwrapped deletion's deleted
text becoming live text where no deletion is left around it. Marks: kept-side markers go; the other side removes the
paragraph — only when its resolved content is nothing but empty runs (w:rPr, w:lastRenderedPageBreak), proofing marks,
wrappers holding only those and Word's _GoBack bookmark (dropped, both ends), a paragraph follows it (block-level marks
aside) and it has no section break. Property changes: accept drops them; reject restores rPrChange (the paragraph mark's
own markers, then the snapshot) and pPrChange (the snapshot, then the w:rPr and w:sectPr); a w:rPr / w:pPr emptied by
that, or by a marker going, goes.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from lxml import etree

from .reader import W
from .props import Ctx
from .fields import v_fields
from .markup import tree_difference

_WRAPS = {W + "ins": "ins", W + "del": "del", W + "moveFrom": "moveFrom", W + "moveTo": "moveTo"}
_CHANGES = {W + t: t for t in ("rPrChange", "pPrChange", "sectPrChange", "tblPrChange", "trPrChange", "tcPrChange",
                               "tblGridChange", "numberingChange")}
_CELLS = {W + t: t for t in ("cellIns", "cellDel", "cellMerge")}
_ALL = {**_WRAPS, **_CHANGES, **_CELLS}
_HIDING = {W + "del", W + "moveFrom"}
_FROM_START, _FROM_END = W + "moveFromRangeStart", W + "moveFromRangeEnd"
_TO_START, _TO_END = W + "moveToRangeStart", W + "moveToRangeEnd"
_MARKS_NOT_BLOCKS = {W + t for t in ("bookmarkStart", "bookmarkEnd", "commentRangeStart", "commentRangeEnd", "proofErr",
                                     "permStart", "permEnd", "moveFromRangeStart", "moveFromRangeEnd",
                                     "moveToRangeStart", "moveToRangeEnd", "customXmlInsRangeStart",
                                     "customXmlInsRangeEnd", "customXmlDelRangeStart", "customXmlDelRangeEnd",
                                     "customXmlMoveFromRangeStart", "customXmlMoveFromRangeEnd",
                                     "customXmlMoveToRangeStart", "customXmlMoveToRangeEnd")}
_NOTE_MARKS = (W + "footnoteReference", W + "endnoteReference", W + "commentReference", W + "commentRangeStart",
               W + "commentRangeEnd")
_PAIRS = {W + "bookmarkStart": "bookmark", W + "bookmarkEnd": "bookmark", W + "permStart": "permission",
          W + "permEnd": "permission", _FROM_START: "moveFrom range", _FROM_END: "moveFrom range",
          _TO_START: "moveTo range", _TO_END: "moveTo range"}
# the scope when the user names none: every text part
_V_EVERY_PART = ("document", "headers", "footers", "footnotes", "endnotes", "comments")
_DAY = r"(\d{4})-(\d{2})-(\d{2})"
_V_USER = re.compile(_DAY + r"(?:T(\d{2}):(\d{2})(?::(\d{2}))?)?")
_V_WORD = re.compile(_DAY + r"(?:T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?)?(?:Z|[+-]\d{2}:?\d{2})?")


def _v_when(text: Optional[str], pattern) -> Optional[datetime]:
    m = pattern.fullmatch((text or "").strip())
    if m is None:
        return None
    try:
        return datetime(*[int(x) if x is not None else 0 for x in m.groups()])
    except ValueError:
        return None


def _v_who(name: Optional[str]) -> str:
    return " ".join((name or "").split()).casefold()


def _v4_kind(el) -> Optional[str]:
    """V4's kind of an element (verify/suite._rev_kind's reading): a wrapper under a w:rPr is a mark."""
    if not isinstance(el.tag, str) or el.tag not in _ALL:
        return None
    name = _ALL[el.tag]
    if el.tag in _WRAPS and el.getparent() is not None and el.getparent().tag == W + "rPr":
        return name + "_mark"
    return name


def _v_count(root) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for el in root.iter():
        k = _v4_kind(el)
        if k:
            out[k] = out.get(k, 0) + 1
    return out


def _v_class(el) -> str:
    if el.tag in _WRAPS:
        up = el.getparent()
        if up.tag == W + "rPr":
            return "mark" if up.getparent() is not None and up.getparent().tag == W + "pPr" else "math"
        return {W + "trPr": "row", W + "numPr": "numbering"}.get(up.tag, "content")
    if el.tag in (W + "rPrChange", W + "pPrChange"):
        return _CHANGES[el.tag]
    return "property" if el.tag in _CHANGES else "cell"


class _Decisions:
    """Every decision about one SOURCE part, keyed by the source elements' ids (the elements are held in ``keep``)."""

    def __init__(self):
        self.keep: List[Any] = []                    # holds every element whose id is used below
        self.drop: Set[int] = set()                  # vanish with their content
        self.unwrap: Set[int] = set()                # their content takes their place
        self.touched: Set[int] = set()               # a w:rPr / w:pPr that loses a marker or a property change
        self.rpr_restore: Dict[int, Any] = {}        # w:rPr → its rejected rPrChange
        self.ppr_restore: Dict[int, Any] = {}        # w:pPr → its rejected pPrChange
        self.chosen = 0
        self.refusals: List[str] = []

    def mark(self, which: Set[int], el) -> None:
        self.keep.append(el)
        which.add(id(el))


def _v_build(el, d: _Decisions, in_deletion: bool = False, undeleted: bool = False) -> List[Any]:
    """The nodes that stand for ``el`` once the part is resolved (a fresh copy; the source is never touched)."""
    if not isinstance(el.tag, str):
        return [copy.deepcopy(el)]
    if id(el) in d.drop:
        return []
    if id(el) in d.unwrap:
        out: List[Any] = []
        for c in el:
            out += _v_build(c, d, in_deletion, undeleted or el.tag in _HIDING)
        return out
    tag = el.tag
    if tag in (W + "delText", W + "delInstrText") and undeleted and not in_deletion:
        tag = W + "t" if tag == W + "delText" else W + "instrText"
    new = etree.Element(tag, attrib=dict(el.attrib), nsmap=el.nsmap)
    new.text, new.tail = el.text, el.tail
    below_deletion = in_deletion or (el.tag in _HIDING and (el.getparent() is None or el.getparent().tag != W + "rPr"))
    if id(el) in d.rpr_restore:
        for c in el:
            if isinstance(c.tag, str) and c.tag in _WRAPS:
                new.extend(_v_build(c, d, below_deletion, undeleted))
        snapshot = d.rpr_restore[id(el)].find(W + "rPr")
        new.extend(copy.deepcopy(c) for c in snapshot)
    elif id(el) in d.ppr_restore:
        snapshot = d.ppr_restore[id(el)].find(W + "pPr")
        new.extend(copy.deepcopy(c) for c in snapshot)
        for c in el:
            if isinstance(c.tag, str) and c.tag in (W + "rPr", W + "sectPr"):
                new.extend(_v_build(c, d, below_deletion, undeleted))
    else:
        for c in el:
            new.extend(_v_build(c, d, below_deletion, undeleted))
    if id(el) in d.touched and len(new) == 0 and not new.attrib:
        return []
    return [new]


def _v_bare(nodes: List[Any], goback: Set[str]) -> List[str]:
    """What, among resolved paragraph content, is more than empty runs, proofing marks, wrappers of nothing else and
    Word's _GoBack bookmark."""
    out: List[str] = []
    for n in nodes:
        if not isinstance(n.tag, str):
            continue
        if n.tag in _WRAPS:
            out += _v_bare(list(n), goback)
        elif n.tag == W + "r":
            out += [etree.QName(k).localname for k in n if isinstance(k.tag, str)
                    and k.tag not in (W + "rPr", W + "lastRenderedPageBreak")]
        elif n.tag == W + "proofErr":
            continue
        elif n.tag == W + "bookmarkStart" and n.get(W + "name") == "_GoBack":
            continue
        elif n.tag == W + "bookmarkEnd" and (n.get(W + "id") or "") in goback:
            continue
        else:
            out.append(etree.QName(n).localname)
    return out


def _v_decide(root, part: str, action: str, chooses: Callable[[Any], Tuple[bool, bool]]) -> _Decisions:
    d = _Decisions()
    revs = [el for el in root.iter() if isinstance(el.tag, str) and el.tag in _ALL
            and not any(isinstance(a.tag, str) and a.tag in _CHANGES for a in el.iterancestors())]
    d.keep += revs
    chosen = {id(el): chooses(el)[0] for el in revs}
    kind = {id(el): _v_class(el) for el in revs}
    # moves: the innermost open range of a piece's kind, document order; ranges grouped by name
    open_from: List[Tuple[str, str]] = []
    open_to: List[Tuple[str, str]] = []
    names_of: Dict[int, str] = {}
    range_starts: Dict[str, List[Any]] = {}
    range_ends: Dict[Tuple[str, str], Any] = {}
    for el in root.iter(_FROM_START, _FROM_END, _TO_START, _TO_END, W + "moveFrom", W + "moveTo"):
        rid = el.get(W + "id") or ""
        if el.tag in (_FROM_START, _TO_START):
            (open_from if el.tag == _FROM_START else open_to).append((rid, el.get(W + "name") or ""))
            range_starts.setdefault(el.get(W + "name") or "", []).append(el)
        elif el.tag in (_FROM_END, _TO_END):
            stack = open_from if el.tag == _FROM_END else open_to
            hit = [i for i, (k, _) in enumerate(stack) if k == rid]
            if hit:
                stack.pop(hit[-1])
                range_ends[("from" if el.tag == _FROM_END else "to", rid)] = el
        elif id(el) in chosen:
            stack = open_from if el.tag == W + "moveFrom" else open_to
            if stack:
                names_of[id(el)] = stack[-1][1]
    move_chosen: Dict[str, bool] = {}
    for name, starts in range_starts.items():
        votes = [chooses(s)[0] for s in starts] + [chosen[i] for i, n in names_of.items() if n == name]
        if len(set(votes)) > 1:
            d.refusals.append(f"the move {name!r} is chosen only in part")
        move_chosen[name] = bool(votes) and all(votes)
        if move_chosen[name]:
            for s in starts:
                d.mark(d.drop, s)
                end = range_ends.get(("from" if s.tag == _FROM_START else "to", s.get(W + "id") or ""))
                if end is not None:
                    d.mark(d.drop, end)
    for i, name in names_of.items():
        chosen[i] = move_chosen.get(name, False)
    picked = [el for el in revs if chosen[id(el)]]
    d.chosen = len(picked)
    accept = action == "accept"
    keeps_content = {"ins", "moveTo"} if accept else {"del", "moveFrom"}
    for el in picked:
        if kind[id(el)] == "content":
            d.mark(d.unwrap if _WRAPS[el.tag] in keeps_content else d.drop, el)
    content_dropped = {id(el) for el in picked if kind[id(el)] == "content" and id(el) in d.drop}
    move_marks = set(d.drop) - content_dropped

    def inside(el, ids: Set[int]) -> bool:
        return any(id(a) in ids for a in el.iterancestors())
    goback_ids = {b.get(W + "id") or "" for b in root.iter(W + "bookmarkStart") if b.get(W + "name") == "_GoBack"}
    if content_dropped:
        # what goes with the content must take nothing an anchor elsewhere still needs
        for el in root.iter(*_NOTE_MARKS):
            if inside(el, content_dropped):
                d.refusals.append(f"{part}: content that goes holds a {etree.QName(el).localname}")
        ends: Dict[Tuple[str, str], List[Any]] = {}
        for el in root.iter(*_PAIRS):
            if id(el) not in move_marks:
                ends.setdefault((_PAIRS[el.tag], el.get(W + "id") or ""), []).append(el)
        for (family, rid), els in ends.items():
            flags = [inside(e, content_dropped) for e in els]
            if len(set(flags)) > 1:
                if family == "bookmark" and rid in goback_ids:
                    for e in els:
                        if not inside(e, content_dropped):
                            d.mark(d.drop, e)
                else:
                    d.refusals.append(f"{part}: content that goes holds one end of a {family} ({rid})")
        for f in v_fields(root):
            if f.simple is None:
                flags = {inside(m, content_dropped) for m in (f.begin, f.separate, f.end) if m is not None}
                if len(flags) > 1:
                    d.refusals.append(f"{part}: content that goes holds part of a {f.type or 'field'} field")
    # paragraph marks
    gone_paragraphs: List[Any] = []
    goback_starts = goback_ids
    for el in picked:
        if kind[id(el)] != "mark" or inside(el, content_dropped):
            continue
        rpr = el.getparent()
        ppr = rpr.getparent()
        p = ppr.getparent()
        if p is None or p.tag != W + "p":
            continue
        if _WRAPS[el.tag] in keeps_content:                 # the marker goes, the paragraph stays
            d.mark(d.drop, el)
            d.mark(d.touched, rpr)
            d.mark(d.touched, ppr)
            continue
        if any(q is p for q in gone_paragraphs):
            continue
        if ppr.find(W + "sectPr") is not None:
            d.refusals.append(f"{part}: a paragraph that would go carries a section break")
            continue
        following = None
        for s in p.itersiblings():
            if isinstance(s.tag, str) and s.tag not in _MARKS_NOT_BLOCKS:
                following = s
                break
        if following is None or following.tag != W + "p":
            d.refusals.append(f"{part}: a paragraph that would go has no paragraph after it to join")
            continue
        content = [n for c in p if not (isinstance(c.tag, str) and c.tag == W + "pPr") for n in _v_build(c, d)]
        rest = _v_bare(content, goback_starts)
        if rest:
            d.refusals.append(f"{part}: a paragraph that would go still holds {sorted(set(rest))}")
            continue
        gone_paragraphs.append(p)
    gone_ids = {id(p) for p in gone_paragraphs}
    for p in gone_paragraphs:
        d.mark(d.drop, p)
    gone = content_dropped | gone_ids
    # property changes and the kinds the op does not resolve
    for el in picked:
        if id(el) in gone or inside(el, gone):
            continue
        k = kind[id(el)]
        if k in ("row", "numbering", "math", "cell") or (k == "property" and not accept):
            d.refusals.append(f"{part}: a chosen {_ALL[el.tag]} ({k}) the op does not resolve")
            continue
        if k not in ("rPrChange", "pPrChange", "property"):
            continue
        parent = el.getparent()
        if parent.tag in (W + "rPr", W + "pPr"):               # only an emptied w:rPr / w:pPr goes, never another
            d.mark(d.touched, parent)
        if parent.tag == W + "rPr" and parent.getparent() is not None and parent.getparent().tag == W + "pPr":
            d.mark(d.touched, parent.getparent())
        if accept or k == "property":
            d.mark(d.drop, el)
            continue
        snapshot = el.find(W + ("rPr" if k == "rPrChange" else "pPr"))
        if snapshot is None or any(isinstance(x.tag, str) and x.tag in _ALL for x in snapshot.iter()):
            d.refusals.append(f"{part}: a rejected {k} without a usable snapshot")
            continue
        d.keep.append(parent)
        (d.rpr_restore if k == "rPrChange" else d.ppr_restore)[id(parent)] = el
    # Word's _GoBack bookmark goes (both ends) when a paragraph that goes holds a piece of it
    hit = set()
    for p in gone_paragraphs:
        for b in p.iter(W + "bookmarkStart", W + "bookmarkEnd"):
            if (b.tag == W + "bookmarkStart" and b.get(W + "name") == "_GoBack") or \
                    (b.tag == W + "bookmarkEnd" and (b.get(W + "id") or "") in goback_starts):
                hit.add(b.get(W + "id") or "")
    for b in root.iter(W + "bookmarkStart", W + "bookmarkEnd"):
        if (b.get(W + "id") or "") in hit and (b.tag == W + "bookmarkEnd" or b.get(W + "name") == "_GoBack"):
            d.mark(d.drop, b)
    return d


def _resolved_changes(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    action = a.get("action")
    if action not in ("accept", "reject"):
        errs.append(f"resolved_changes: unknown action {action!r}")
        return "resolved_changes: unknown action"
    authors = {_v_who(x) for x in a.get("authors") or []}
    since = _v_when(a.get("on_or_after"), _V_USER) if a.get("on_or_after") else None
    until = _v_when(a.get("before"), _V_USER) if a.get("before") else None
    if (a.get("on_or_after") and since is None) or (a.get("before") and until is None):
        errs.append("resolved_changes: a date filter this check cannot read")
        return "resolved_changes: unreadable date filter"

    def chooses(el) -> Tuple[bool, bool]:
        if authors and _v_who(el.get(W + "author")) not in authors:
            return False, False
        if since is None and until is None:
            return True, False
        when = _v_when(el.get(W + "date"), _V_WORD)
        if when is None:
            return False, True
        return (since is None or when >= since) and (until is None or when < until), False
    changed: List[str] = []
    gone: Dict[str, Dict[str, int]] = {}
    picked: Dict[str, int] = {}
    total = 0
    scope = a.get("asked_scope") or _V_EVERY_PART
    for part in ctx.scope_parts([scope] if isinstance(scope, str) else scope):
        if part not in ctx.out.names:
            errs.append(f"resolved_changes: {part} is missing from the output")
            continue
        src_root, out_root = ctx.src.xml(part), ctx.out.xml(part)
        d = _v_decide(src_root, part, action, chooses)
        for why in d.refusals:
            errs.append(f"resolved_changes: {why} — the op must refuse")
        built = _v_build(src_root, d)
        expected = built[0] if built else None
        diff = tree_difference(expected, out_root) if expected is not None else "the whole part would vanish"
        if diff:
            errs.append(f"resolved_changes: {part} is not the source with the chosen tracked changes {action}ed: {diff}")
        before, after = _v_count(src_root), _v_count(expected) if expected is not None else {}
        vanished = {k: n - after.get(k, 0) for k, n in before.items() if n - after.get(k, 0)}
        if any(v < 0 for v in vanished.values()) or any(k not in before for k in after):
            errs.append(f"resolved_changes: {part}: resolving would create revisions {vanished}")
        if d.chosen:
            picked[part] = d.chosen
            total += d.chosen
        if d.chosen or d.drop or d.unwrap or d.rpr_restore or d.ppr_restore:
            changed.append(part)
            gone[part] = dict(sorted(vanished.items()))
        ctx.verified_parts.add(part)
    if sorted(changed) != sorted(a.get("parts") or []):
        errs.append(f"resolved_changes: parts changed here {changed}, the plan predicted {a.get('parts')}")
    if gone != {p: dict(sorted(v.items())) for p, v in (a.get("expected_gone") or {}).items()}:
        errs.append(f"resolved_changes: revisions that go here {gone}, the plan predicted {a.get('expected_gone')}")
    planned = {p: n for p, n in (a.get("expected_selected") or {}).items() if n}
    if picked != planned:
        errs.append(f"resolved_changes: tracked changes chosen here {picked}, the plan predicted {planned}")
    return f"resolved_changes: {action} {total} chosen tracked change(s) in {len(changed)} part(s), each part compared whole"


REVISION_KINDS: Dict[str, Callable[..., Any]] = {"resolved_changes": _resolved_changes}
