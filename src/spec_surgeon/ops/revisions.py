"""Op: resolve_tracked_changes — accept or reject tracked changes: all of them, by author, by date (docs/
BUILTIN_OPS_BATCH2_DESIGN.md §4; CLAUDE.md "Resolving tracked changes", both sides). It is the one operation that
accepts or rejects tracked changes, and only when the user asks for it by name (the method's rule, §0). The verifier
resolves the SOURCE again with its own code (verify/revisions.py, kind ``resolved_changes``) and compares every part
whole.

**Revision elements** of a part: every ``w:ins``/``w:del``/``w:moveFrom``/``w:moveTo``, property change (``rPrChange``,
``pPrChange``, ``sectPrChange``, ``tblPrChange``, ``trPrChange``, ``tcPrChange``, ``tblGridChange``, ``numberingChange``)
and cell revision (``cellIns``, ``cellDel``, ``cellMerge``) that does not lie inside a property change (its snapshot is
history, not a revision of its own). Where it sits decides what it is: ins/del/moveFrom/moveTo under a ``w:rPr`` are a
paragraph mark's when that rPr is a ``w:pPr``'s (else an equation's: refused), under ``w:trPr`` a table row's (ins/del;
a moved row is refused) and under ``w:numPr`` a numbering marker's (refused); anywhere else they wrap content.

**Selected**: the author listed (casefolded, whitespace collapsed) — or no list — and the wall-clock ``w:date`` (its
date and time, before a ``Z`` or an offset; never shifted) on or after ``on_or_after`` and before ``before``; under a
date filter a revision without a readable date is not selected (flagged). **Moves**: scanning in document order, each
moveFrom/moveTo piece belongs to the innermost open move range of its kind (``moveFromRangeStart``…``End`` /
``moveToRangeStart``…``End``, paired by ``w:id``); a move is the ranges sharing a ``w:name`` with the pieces inside
them, selected whole or refused (``partial-move``); its range marks go with it. A piece outside any range is a plain
revision.

**Resolution**, in this order: (0) rows (built-in batch 6, docs/BUILTIN_OPS_BATCH6_DESIGN.md §6) — accepting a row's
deletion or rejecting its insertion takes the row (a ``w:tr`` directly in its ``w:tbl``; one in a content control is
refused, ``table-structure``) with everything in it, whatever is pending there; accepting an insertion or rejecting a
deletion takes only the marker (an emptied ``w:trPr`` goes); a table every own row of which goes goes whole, unless that
would leave its container without a closing paragraph or set two tables side by side (``table_edit``'s rule,
``table-structure``); what a row that goes holds must take no anchor away, as removed content (below); (1) content —
accept unwraps ins/moveTo and removes del/moveFrom with their content,
reject removes ins/moveTo with their content and unwraps del/moveFrom (a revision inside removed content goes with it) —
content that goes may hold no footnote, endnote or comment mark, no single end of a bookmark, permission or move range
(Word's ``_GoBack`` bookmark goes whole instead) and no part of a complex field (``marks-in-change``); (2) every ``w:delText``/``w:delInstrText`` that was inside an unwrapped deletion and now lies in no ``w:del``/
``w:moveFrom`` becomes ``w:t``/``w:instrText``; (3) the range marks of resolved moves go; (4) property changes — accept
removes them; reject restores ``rPrChange`` (the ``w:rPr`` becomes its own paragraph-mark revision markers, in order,
then the snapshot's children) and ``pPrChange`` (the ``w:pPr`` becomes the snapshot's children, then its own ``w:rPr``
and ``w:sectPr``) — rejecting any other kind is refused (``revision-kind``); (5) paragraph marks — accept ins/moveTo and
reject del/moveFrom remove the marker; accept del/moveFrom and reject ins/moveTo remove the paragraph, only when,
resolved, it holds nothing but ``w:pPr``, runs of ``w:rPr``/``w:lastRenderedPageBreak``, proofing marks, revision
wrappers holding nothing else and Word's ``_GoBack`` bookmark (both its ends go, wherever the other one is), a paragraph
follows it in its container (block-level marks aside) and it carries no section break — otherwise two paragraphs would
be joined and Word decides whose formatting the joined one keeps (``paragraph-merge``, ``section-break``); (6) a
``w:rPr``, then a ``w:pPr``, that steps 4–5 left empty goes.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from ..ooxml import block_full_text, block_struct_counts, block_text_without, paragraphs, revision_kind
from ..package import DocxPackage, W, REL_TYPE_BASE
from . import prose, register
from .base import BlockDiff, JobException, Op, PlanFragment
from .fields import fields_of
from .replace_table import resolve_scope
from .selectors import SCOPE_KINDS, scope_parts
from .table_edit import table_removal_problem
from .table_select import own_rows

CONTENT_TAGS = tuple(W + t for t in ("ins", "del", "moveFrom", "moveTo"))
PROPERTY_CHANGE_TAGS = tuple(W + t for t in ("rPrChange", "pPrChange", "sectPrChange", "tblPrChange", "trPrChange",
                                             "tcPrChange", "tblGridChange", "numberingChange"))
CELL_TAGS = tuple(W + t for t in ("cellIns", "cellDel", "cellMerge"))
REVISION_TAGS = CONTENT_TAGS + PROPERTY_CHANGE_TAGS + CELL_TAGS
RANGE_START = {W + "moveFromRangeStart": "from", W + "moveToRangeStart": "to"}
RANGE_END = {W + "moveFromRangeEnd": "from", W + "moveToRangeEnd": "to"}
DELETIONS = (W + "del", W + "moveFrom")
# block-level marks, not blocks: passed over when looking for the paragraph that follows a paragraph that goes
NON_BLOCK = {W + t for t in ("bookmarkStart", "bookmarkEnd", "commentRangeStart", "commentRangeEnd", "proofErr",
                             "permStart", "permEnd", "moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart",
                             "moveToRangeEnd", "customXmlInsRangeStart", "customXmlInsRangeEnd",
                             "customXmlDelRangeStart", "customXmlDelRangeEnd", "customXmlMoveFromRangeStart",
                             "customXmlMoveFromRangeEnd", "customXmlMoveToRangeStart", "customXmlMoveToRangeEnd")}
GOBACK = "_GoBack"
DEFAULT_SCOPE = list(SCOPE_KINDS)              # every text part: body, headers, footers, footnotes, endnotes, comments
USER_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2})(?::(\d{2}))?)?$")
WORD_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?)?(?:Z|[+-]\d{2}:?\d{2})?$")
LABEL = {("content", "ins"): "insertion", ("content", "del"): "deletion", ("content", "moveFrom"): "moved text (from)",
         ("content", "moveTo"): "moved text (to)", ("mark", "ins"): "inserted paragraph mark",
         ("mark", "del"): "deleted paragraph mark", ("mark", "moveFrom"): "moved paragraph mark (from)",
         ("mark", "moveTo"): "moved paragraph mark (to)", ("rPrChange", "rPrChange"): "formatting change",
         ("pPrChange", "pPrChange"): "paragraph formatting change"}
WHAT = {"row": "a tracked move of a table row", "numbering": "a tracked numbering marker",
        "math": "a tracked change inside an equation", "cell": "a tracked table cell insertion, deletion or merge",
        "property": "a tracked change of section or table properties"}


def _local(el) -> str:
    return el.tag[len(W):]


def author_key(name: Optional[str]) -> str:
    return " ".join((name or "").split()).casefold()


def user_date(value: Any, name: str) -> Optional[datetime]:
    if value is None:
        return None
    m = USER_DATE.match(value) if isinstance(value, str) else None
    if not m:
        raise JobException(f"{name} must be a date YYYY-MM-DD (optionally THH:MM or THH:MM:SS), got {value!r}",
                           "bad-params")
    try:
        return datetime(*(int(g) for g in m.groups(default="0")))
    except ValueError:
        raise JobException(f"{name}: {value!r} is not a date on the calendar", "bad-params")


def wall_clock(value: Optional[str]) -> Optional[datetime]:
    """A revision's w:date as Word shows it: its date and time, the Z or offset ignored; None when unreadable."""
    m = WORD_DATE.match((value or "").strip())
    if not m:
        return None
    try:
        return datetime(*(int(g) for g in m.groups(default="0")))
    except ValueError:
        return None


@dataclass
class Selection:
    authors: Optional[Set[str]]
    on_or_after: Optional[datetime]
    before: Optional[datetime]

    @property
    def dated(self) -> bool:
        return self.on_or_after is not None or self.before is not None

    def picks(self, el) -> Tuple[bool, bool]:
        """(selected, left out for want of a readable date)."""
        if self.authors is not None and author_key(el.get(W + "author")) not in self.authors:
            return False, False
        if not self.dated:
            return True, False
        when = wall_clock(el.get(W + "date"))
        if when is None:
            return False, True
        if self.on_or_after is not None and when < self.on_or_after:
            return False, False
        if self.before is not None and when >= self.before:
            return False, False
        return True, False


@dataclass
class Revision:
    el: Any
    tag: str                     # local name
    cls: str                     # content | mark | row | numbering | math | rPrChange | pPrChange | property | cell
    selected: bool = False
    undated: bool = False
    move: Optional[str] = None   # the move (range name) a moveFrom/moveTo piece belongs to

    @property
    def author(self) -> str:
        return self.el.get(W + "author") or "(no author)"

    @property
    def label(self) -> str:
        return LABEL.get((self.cls, self.tag)) or {"property": "section/table property change"}.get(self.cls, self.tag)


def classify(el) -> str:
    tag = el.tag
    if tag in CONTENT_TAGS:
        parent = el.getparent()
        if parent.tag == W + "rPr":
            gp = parent.getparent()
            return "mark" if gp is not None and gp.tag == W + "pPr" else "math"
        if parent.tag == W + "trPr":
            return "row"
        if parent.tag == W + "numPr":
            return "numbering"
        return "content"
    if tag in (W + "rPrChange", W + "pPrChange"):
        return _local(el)
    if tag in PROPERTY_CHANGE_TAGS:
        return "property"
    return "cell"


def _in_property_change(el) -> bool:
    return any(a.tag in PROPERTY_CHANGE_TAGS for a in el.iterancestors())


def _attached(el, root) -> bool:
    q = el
    while q.getparent() is not None:
        q = q.getparent()
    return q is root


def _describe(el) -> str:
    local = el.tag.split("}")[-1]
    if el.tag == W + "bookmarkStart":
        return f"a bookmark ({el.get(W + 'name')!r})"
    if el.tag in (W + "t", W + "delText"):
        return f"text {(el.text or '')[:30]!r}"
    return {"hyperlink": "a hyperlink", "sdt": "a content control", "commentRangeStart": "a comment mark",
            "commentRangeEnd": "a comment mark", "commentReference": "a comment mark", "drawing": "a picture",
            "fldChar": "a field", "fldSimple": "a field", "tab": "a tab", "br": "a break"}.get(local, f"w:{local}")


@dataclass
class Resolution:
    """What resolving one part does: every decision, element by element (the plan predicts from it, apply performs it)."""
    part: str
    action: str
    revisions: List[Revision] = field(default_factory=list)
    removed: List[Any] = field(default_factory=list)          # content wrappers removed with their content
    unwrapped: List[Any] = field(default_factory=list)        # content wrappers unwrapped
    del_texts: List[Any] = field(default_factory=list)        # deleted text inside unwrapped deletions (step 2)
    range_marks: List[Any] = field(default_factory=list)      # move range marks of resolved moves
    properties: List[Any] = field(default_factory=list)       # property changes accepted (removed) or restored
    markers: List[Any] = field(default_factory=list)          # paragraph-mark markers removed
    gone_paragraphs: List[Any] = field(default_factory=list)  # paragraphs removed (their mark accepted/rejected away)
    goback: List[Any] = field(default_factory=list)           # _GoBack bookmark ends removed with them
    gone_rows: List[Any] = field(default_factory=list)        # rows (or whole tables) removed with everything in them
    row_markers: List[Any] = field(default_factory=list)      # row markers removed (the row stays)

    @property
    def changes(self) -> bool:
        return bool(self.removed or self.unwrapped or self.range_marks or self.properties or self.markers
                    or self.gone_paragraphs or self.gone_rows or self.row_markers)

    def selected(self) -> List[Revision]:
        return [r for r in self.revisions if r.selected]


def _moves(root, revisions: List[Revision], sel: Selection, part: str) -> Tuple[Set[str], List[Any]]:
    """(names of the moves selected, their range marks); each moveFrom/moveTo piece gets its move's name."""
    by_el = {id(r.el): r for r in revisions if r.tag in ("moveFrom", "moveTo")}
    open_ranges: Dict[str, List[Tuple[str, str, Any]]] = {"from": [], "to": []}
    starts: Dict[str, List[Any]] = {}
    ends: Dict[Tuple[str, str], Any] = {}
    for el in root.iter(*RANGE_START, *RANGE_END, W + "moveFrom", W + "moveTo"):
        if el.tag in RANGE_START:
            kind = RANGE_START[el.tag]
            name = el.get(W + "name") or ""
            open_ranges[kind].append((el.get(W + "id") or "", name, el))
            starts.setdefault(name, []).append(el)
        elif el.tag in RANGE_END:
            kind = RANGE_END[el.tag]
            for k in range(len(open_ranges[kind]) - 1, -1, -1):
                if open_ranges[kind][k][0] == (el.get(W + "id") or ""):
                    ends[(kind, open_ranges[kind][k][0])] = el
                    del open_ranges[kind][k]
                    break
        elif id(el) in by_el:
            kind = "from" if el.tag == W + "moveFrom" else "to"
            if open_ranges[kind]:
                by_el[id(el)].move = open_ranges[kind][-1][1]
    chosen: Set[str] = set()
    marks: List[Any] = []
    for name, range_starts in starts.items():
        flags = [sel.picks(s)[0] for s in range_starts] + [r.selected for r in revisions if r.move == name]
        if any(flags) and not all(flags):
            raise JobException(f"{part}: the move {name!r} is only partly chosen by the authors/dates asked (its two "
                               f"ends or its pieces differ) — a move is accepted or rejected whole: resolve it in Word, "
                               f"or ask for all of it", "partial-move", {"part": part, "move": name})
        if flags and all(flags):
            chosen.add(name)
            for s in range_starts:
                marks.append(s)
                kind = RANGE_START[s.tag]
                end = ends.get((kind, s.get(W + "id") or ""))
                if end is not None:
                    marks.append(end)
    return chosen, marks


def _leftover(container, p, res: Resolution, removed: Set[int], goback_ids: Set[str],
              range_marks: Set[int]) -> List[str]:
    """What would remain of paragraph ``p`` (walking ``container``, p itself first) once its content is resolved."""
    out: List[str] = []
    for c in container:
        if not isinstance(c.tag, str) or (container is p and c.tag == W + "pPr") or id(c) in removed:
            continue
        if c.tag in CONTENT_TAGS:                        # unwrapped, or still pending: what it holds counts
            out += _leftover(c, p, res, removed, goback_ids, range_marks)
        elif c.tag == W + "r":
            out += [_describe(k) for k in c if isinstance(k.tag, str)
                    and k.tag not in (W + "rPr", W + "lastRenderedPageBreak")]
        elif c.tag == W + "proofErr" or id(c) in range_marks:
            continue
        elif c.tag == W + "bookmarkStart" and c.get(W + "name") == GOBACK:
            continue
        elif c.tag == W + "bookmarkEnd" and (c.get(W + "id") or "") in goback_ids:
            continue
        else:
            out.append(_describe(c))
    return out


NOTE_MARKS = {W + "footnoteReference": "a footnote mark (the footnote would stay without its place in the text)",
              W + "endnoteReference": "an endnote mark (the endnote would stay without its place in the text)",
              W + "commentReference": "a comment mark (the comment would lose its anchor)",
              W + "commentRangeStart": "a comment mark (the comment would lose its anchor)",
              W + "commentRangeEnd": "a comment mark (the comment would lose its anchor)"}
PAIRED_MARKS = {W + "bookmarkStart": "bookmark", W + "bookmarkEnd": "bookmark", W + "permStart": "permission",
                W + "permEnd": "permission", W + "moveFromRangeStart": "moveFrom range",
                W + "moveFromRangeEnd": "moveFrom range", W + "moveToRangeStart": "moveTo range",
                W + "moveToRangeEnd": "moveTo range"}


def _marks_in_removed(root, part: str, action: str, gone, goback_ids: Set[str], range_ids: Set[int],
                      res: Resolution) -> None:
    """Content that goes must take nothing an anchor elsewhere still needs: no footnote/endnote/comment mark, no single
    end of a bookmark/permission/move range (Word's _GoBack bookmark goes whole instead), no part of a complex field
    (refused: ``marks-in-change``)."""
    def refuse(what: str) -> None:
        raise JobException(f"{part}: the tracked change {action}ed away holds {what} — resolve it in Word",
                           "marks-in-change", {"part": part})
    for el in root.iter(*NOTE_MARKS):
        if gone(el):
            refuse(NOTE_MARKS[el.tag])
    pairs: Dict[Tuple[str, str], List[Any]] = {}
    for el in root.iter(*PAIRED_MARKS):
        if id(el) not in range_ids:
            pairs.setdefault((PAIRED_MARKS[el.tag], el.get(W + "id") or ""), []).append(el)
    for (family, rid), els in pairs.items():
        inside = [gone(e) for e in els]
        if any(inside) and not all(inside):
            if family == "bookmark" and rid in goback_ids:
                res.goback += [e for e in els if not gone(e)]     # Word's own cursor bookmark goes whole
                continue
            name = next((e.get(W + "name") for e in els if e.get(W + "name")), rid)
            refuse(f"one end of the {family} {name!r} (the other end stays)")
    for f in fields_of(root):
        if f.simple is None:
            inside = [gone(m) for m in (f.begin, f.separate, f.end) if m is not None]
            if any(inside) and not all(inside):
                refuse(f"part of a field ({f.type or 'field'}: some of its markers stay)")


def _rows(res: Resolution, part: str, action: str) -> None:
    """Step 0 (module docstring): the rows that go — the outermost ones; a table every own row of which goes, whole —
    and the row markers that go."""
    going = []
    for r in res.revisions:
        if not r.selected or r.cls != "row" or r.tag not in ("ins", "del"):
            continue
        tr = r.el.getparent().getparent()
        if (r.tag == "del") == (action == "accept"):
            going.append(tr)
        else:
            res.row_markers.append(r.el)
    held = set(going)
    going = [tr for tr in going if not any(a in held for a in tr.iterancestors())]
    by_table: Dict[Any, List[Any]] = {}
    for tr in going:
        if tr.getparent() is None or tr.getparent().tag != W + "tbl":
            raise JobException(f"{part}: a tracked row to {action} sits in a content control or custom XML — resolve "
                               f"it in Word", "table-structure", {"part": part})
        by_table.setdefault(tr.getparent(), []).append(tr)
    whole = [tbl for tbl, trs in by_table.items() if len(trs) == len(own_rows(tbl))]
    for container in {t.getparent() for t in whole}:
        why = table_removal_problem(container, {t for t in whole if t.getparent() is container})
        if why:
            raise JobException(f"{part}: {action}ing its tracked rows would take a whole table away, and {why} — "
                               f"resolve it in Word", "table-structure", {"part": part})
    res.gone_rows = whole + [tr for tr in going if tr.getparent() not in whole]


def analyze(root, part: str, action: str, sel: Selection) -> Resolution:
    """Every decision of resolving ``root`` (one part); JobException for what must be resolved in Word."""
    res = Resolution(part=part, action=action)
    for el in root.iter(*REVISION_TAGS):
        if _in_property_change(el):
            continue
        r = Revision(el, _local(el), classify(el))
        r.selected, r.undated = sel.picks(el)
        res.revisions.append(r)
    chosen, marks = _moves(root, res.revisions, sel, part)
    res.range_marks = marks
    for r in res.revisions:
        if r.move is not None:
            r.selected = r.move in chosen
    accept = action == "accept"
    _rows(res, part, action)
    rows_gone = {id(e) for e in res.gone_rows}
    for r in res.revisions:
        if not r.selected or r.cls != "content" or any(id(a) in rows_gone for a in r.el.iterancestors()):
            continue                                 # content in a row that goes goes with it
        keep = r.tag in ("ins", "moveTo") if accept else r.tag in ("del", "moveFrom")
        (res.unwrapped if keep else res.removed).append(r.el)
    removed = {id(e) for e in res.removed} | rows_gone

    def gone_by_content(el) -> bool:
        return id(el) in removed or any(id(a) in removed for a in el.iterancestors())
    for w in res.unwrapped:
        if w.tag in DELETIONS:
            res.del_texts += list(w.iter(W + "delText", W + "delInstrText"))
    goback_ids = {b.get(W + "id") or "" for b in root.iter(W + "bookmarkStart") if b.get(W + "name") == GOBACK}
    range_ids = {id(m) for m in res.range_marks}
    if res.removed or res.gone_rows:
        _marks_in_removed(root, part, action, gone_by_content, goback_ids, range_ids, res)
    for r in res.revisions:
        if not r.selected or r.cls != "mark" or gone_by_content(r.el):
            continue
        p = r.el.getparent().getparent().getparent()
        if p is None or p.tag != W + "p":
            continue
        stays = r.tag in ("ins", "moveTo") if accept else r.tag in ("del", "moveFrom")
        if stays:
            res.markers.append(r.el)
            continue
        if any(q is p for q in res.gone_paragraphs):
            continue
        where = f"{part} paragraph «{' '.join(block_full_text(p).split())[:60]}»"
        if p.find(f"{W}pPr/{W}sectPr") is not None:
            raise JobException(f"{where}: its paragraph mark ({r.label}, {r.author}) carries a section break; "
                               f"{action}ing it would join two sections — resolve it in Word", "section-break",
                               {"part": part, "where": where})
        nxt = next((s for s in p.itersiblings() if isinstance(s.tag, str) and s.tag not in NON_BLOCK), None)
        if nxt is None or nxt.tag != W + "p":
            what = "a table follows it" if nxt is not None and nxt.tag == W + "tbl" else \
                "it is the last paragraph of its cell, section or note" if nxt is None or nxt.tag == W + "sectPr" \
                else f"w:{_local(nxt)} follows it"
            raise JobException(f"{where}: {action}ing its paragraph mark ({r.label}, {r.author}) would join it to what "
                               f"follows, and {what} — resolve it in Word", "paragraph-merge",
                               {"part": part, "where": where})
        left = _leftover(p, p, res, removed, goback_ids, range_ids)
        if left:
            raise JobException(f"{where}: {action}ing its paragraph mark ({r.label}, {r.author}) would join it to the "
                               f"next paragraph, and it still holds {', '.join(dict.fromkeys(left))}; Word decides "
                               f"whose formatting the joined paragraph keeps — resolve it in Word", "paragraph-merge",
                               {"part": part, "where": where})
        res.gone_paragraphs.append(p)
    gone_ps = {id(p) for p in res.gone_paragraphs}

    def gone(el) -> bool:
        return gone_by_content(el) or id(el) in gone_ps or any(id(a) in gone_ps for a in el.iterancestors())
    res.markers = [m for m in res.markers if not gone(m)]
    res.row_markers = [m for m in res.row_markers if not gone(m)]
    for r in res.revisions:
        if not r.selected or gone(r.el):
            continue
        if r.cls == "row" and r.tag in ("ins", "del"):
            continue                                 # step 0 decided it
        if r.cls in WHAT and not (r.cls == "property" and accept):
            raise JobException(f"{part}: {WHAT[r.cls]} ({r.author}) — {action}ing it is done in Word", "revision-kind",
                               {"part": part, "kind": r.tag})
        if r.cls in ("rPrChange", "pPrChange", "property"):
            if not accept and r.cls in ("rPrChange", "pPrChange"):
                snap = r.el.find(W + ("rPr" if r.cls == "rPrChange" else "pPr"))
                if snap is None or any(e.tag in REVISION_TAGS for e in snap.iter()):
                    raise JobException(f"{part}: a tracked {r.label} ({r.author}) whose record of the earlier "
                                       f"formatting {'is missing' if snap is None else 'holds tracked changes itself'}"
                                       f" — reject it in Word", "revision-kind", {"part": part, "kind": r.tag})
            res.properties.append(r.el)
    if res.gone_paragraphs:
        hit = {b.get(W + "id") or "" for p in res.gone_paragraphs for b in p.iter(W + "bookmarkStart", W + "bookmarkEnd")
               if (b.tag == W + "bookmarkStart" and b.get(W + "name") == GOBACK)
               or (b.tag == W + "bookmarkEnd" and (b.get(W + "id") or "") in goback_ids)}
        held = {id(b) for b in res.goback}
        res.goback += [b for b in root.iter(W + "bookmarkStart", W + "bookmarkEnd")
                       if (b.get(W + "id") or "") in hit and (b.tag == W + "bookmarkEnd" or b.get(W + "name") == GOBACK)
                       and id(b) not in held]
    return res


# ---- the predictions (analytic, from the decisions — never from performing them) ------------------------------------

def gone_revisions(res: Resolution) -> Dict[int, str]:
    """id → V4 kind of every revision element the resolution takes out of the part (counted once each)."""
    out: Dict[int, str] = {}

    def subtree(el):
        for e in el.iter():
            k = revision_kind(e)
            if k:
                out[id(e)] = k
    for el in res.removed + res.gone_paragraphs + res.properties + res.gone_rows:
        subtree(el)
    for el in res.unwrapped + res.markers + res.row_markers:
        out[id(el)] = revision_kind(el)
    return out


def predict(frag: PlanFragment, root, res: Resolution) -> Dict[str, int]:
    """The part's block diffs (V2/V3) and revision delta (V4) into ``frag``; returns the delta of this part."""
    gone_ps = {id(p) for p in res.gone_paragraphs}
    removed = {id(e) for e in res.removed} | {id(e) for e in res.gone_rows}
    for bi, p in enumerate(paragraphs(root)):
        ancestors = [id(a) for a in p.iterancestors()]
        before = block_full_text(p)
        if id(p) in gone_ps or any(a in gone_ps or a in removed for a in ancestors):
            frag.diffs.append(BlockDiff(part=res.part, block_index=bi, before=before, after=None,
                                        note=f"paragraph goes ({res.action}ed)"))
            continue
        after = block_text_without(p, res.removed)
        s_before, s_after = block_struct_counts(p), block_struct_counts(p, res.removed)
        if after != before or s_after != s_before:
            frag.diffs.append(BlockDiff(part=res.part, block_index=bi, before=before, after=after,
                                        struct_after=s_after if s_after != s_before else None,
                                        note=f"tracked changes {res.action}ed"))
    delta: Dict[str, int] = {}
    for kind in gone_revisions(res).values():
        delta[kind] = delta.get(kind, 0) - 1
    return delta


# ---- the transform -----------------------------------------------------------------------------------------------

def _unwrap(el) -> None:
    parent = el.getparent()
    idx = parent.index(el)
    for c in list(el):
        parent.insert(idx, c)
        idx += 1
    parent.remove(el)


def _in_deletion(el) -> bool:
    return any(a.tag in DELETIONS for a in el.iterancestors())


def resolve(root, res: Resolution) -> None:
    """Perform the resolution on ``root`` (the part as planned)."""
    for el in res.gone_rows:                            # step 0: rows (and whole tables) go with what they hold
        el.getparent().remove(el)
    for m in res.row_markers:
        trpr = m.getparent()
        trpr.remove(m)
        if len(trpr) == 0 and not trpr.attrib:
            trpr.getparent().remove(trpr)
    removed = {id(e) for e in res.removed}
    unwrapped = {id(e) for e in res.unwrapped}          # the elements are held by ``res``: their ids stay theirs
    for el in [e for e in root.iter(*CONTENT_TAGS) if id(e) in removed or id(e) in unwrapped]:
        if not _attached(el, root):
            continue
        if id(el) in removed:
            el.getparent().remove(el)
        else:
            _unwrap(el)
    for t in res.del_texts:
        if _attached(t, root) and not _in_deletion(t):
            t.tag = W + ("t" if t.tag == W + "delText" else "instrText")
    for m in res.range_marks:
        if _attached(m, root):
            m.getparent().remove(m)
    touched_rpr: List[Any] = []
    touched_ppr: List[Any] = []
    for c in res.properties:
        if not _attached(c, root):
            continue
        parent = c.getparent()
        if res.action == "reject" and c.tag == W + "rPrChange":
            snap = c.find(W + "rPr")
            keep = [k for k in parent if isinstance(k.tag, str) and k.tag in CONTENT_TAGS]
            for k in list(parent):
                parent.remove(k)
            for k in keep:
                parent.append(k)
            for k in snap:
                parent.append(copy.deepcopy(k))
        elif res.action == "reject" and c.tag == W + "pPrChange":
            snap = c.find(W + "pPr")
            tail = [k for k in parent if isinstance(k.tag, str) and k.tag in (W + "rPr", W + "sectPr")]
            for k in list(parent):
                parent.remove(k)
            for k in snap:
                parent.append(copy.deepcopy(k))
            for k in tail:
                parent.append(k)
        else:
            parent.remove(c)
        if parent.tag == W + "rPr":
            touched_rpr.append(parent)
            if parent.getparent() is not None and parent.getparent().tag == W + "pPr":
                touched_ppr.append(parent.getparent())
        elif parent.tag == W + "pPr":
            touched_ppr.append(parent)
    for m in res.markers:
        if _attached(m, root):
            rpr = m.getparent()
            rpr.remove(m)
            touched_rpr.append(rpr)
            touched_ppr.append(rpr.getparent())
    for p in res.gone_paragraphs:
        if _attached(p, root):
            p.getparent().remove(p)
    for b in res.goback:
        if _attached(b, root):
            b.getparent().remove(b)
    for group in (touched_rpr, touched_ppr):
        for el in group:
            if _attached(el, root) and len(el) == 0 and not el.attrib:
                el.getparent().remove(el)


# ---- the op ------------------------------------------------------------------------------------------------------

def _counts(revs: List[Revision]) -> str:
    by: Dict[str, Dict[str, int]] = {}
    for r in revs:
        by.setdefault(r.label, {}).setdefault(r.author, 0)
        by[r.label][r.author] += 1
    return "; ".join(f"{label} {sum(a.values())} (" + ", ".join(f"{k} {v}" for k, v in sorted(a.items())) + ")"
                     for label, a in sorted(by.items()))


MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")


def _date_words(text: Any) -> str:
    """A user date (YYYY-MM-DD, optionally THH:MM[:SS]) as ``1 January 2026`` / ``1 January 2026 at 09:30``; anything
    else is repeated as given."""
    m = USER_DATE.match(str(text)) if isinstance(text, str) else None
    if not m:
        return str(text)
    year, month, day, hour, minute, second = m.groups()
    if not 1 <= int(month) <= 12:
        return str(text)
    words = f"{int(day)} {MONTHS[int(month) - 1]} {year}"
    if hour is not None:
        words += f" at {hour}:{minute}" + (f":{second}" if second else "")
    return words


@register
class ResolveTrackedChanges(Op):
    name = "resolve_tracked_changes"
    mode = "untracked"
    description = ("Accept or reject tracked changes — all of them, or only those by the named authors and/or made in a "
                   "date range — in the body, headers, footers, notes and comments: insertions, deletions, moves, "
                   "inserted/deleted paragraphs, inserted/deleted table rows and formatting changes. Only when the user "
                   "asks to accept or reject. Refused (left for Word): table cell changes, changes inside equations, "
                   "rejecting section or table property changes, and an accept/reject that would join two "
                   "paragraphs.")
    params_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["accept", "reject"]},
            "authors": {"type": "array", "items": {"type": "string"},
                        "description": "only these authors' changes (as Word shows the name; case and spacing ignored)"},
            "on_or_after": {"type": "string", "description": "only changes made on or after this date "
                                                             "(YYYY-MM-DD or YYYY-MM-DDTHH:MM[:SS], Word's clock)"},
            "before": {"type": "string", "description": "only changes made before this date (same form)"},
            "allow_missing": {"type": "boolean", "default": True,
                              "description": "a file with no matching tracked change is left as it is (true, the "
                                             "default) or set aside (false)"},
        },
        "required": ["action"],
    }
    select_schema = {"type": "object", "properties": {
        "scope": {"type": "array", "items": {"type": "string", "enum": list(SCOPE_KINDS)},
                  "description": "part kinds (default: every text part — body, headers, footers, footnotes, endnotes, "
                                 "comments)"}}}
    verification_profile = {
        "V2": "every block whose text the resolution changes (accepted deletions, rejected insertions) and every "
              "paragraph that goes, predicted from the source",
        "V3": "structure counts that change (a deleted tab accepted away) declared per block",
        "V4": "a negative delta of every revision kind taken out — those inside removed content included",
        "V5b": "untracked: no revision added; V2 the complete account",
        "V10": "resolved_changes: the verifier resolves every part in scope again with its own code and compares it "
               "whole with the output",
    }


    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        action = params.get("action")
        verb = action if action in ("accept", "reject") else "accept or reject"
        which = ["every tracked change"]
        authors = params.get("authors")
        if isinstance(authors, list) and authors:
            which.append("by " + prose.join((prose.quote(a) for a in authors), "or"))
        since, until = params.get("on_or_after"), params.get("before")
        if since and until:
            which.append(f"made on or after {_date_words(since)} and before {_date_words(until)}")
        elif since:
            which.append(f"made on or after {_date_words(since)}")
        elif until:
            which.append(f"made before {_date_words(until)}")
        scope = select.get("scope") or params.get("scope")
        place = prose.scope_words(scope if scope else DEFAULT_SCOPE) or "in the document body"
        first = prose.sentence(verb, " ".join(which),
                               "— insertions, deletions, moved text and formatting changes —", place)
        extras: List[str] = []
        if action == "reject":
            extras.append("rejected deletions put their words back into the text")
        if params.get("allow_missing") is False:
            extras.append("a file with no such tracked change is set aside")
        return first if not extras else first + " " + prose.sentence("; ".join(extras))

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select = dict(select or {})
        if set(select) - {"scope"}:
            raise JobException("resolve_tracked_changes selects by scope only", "bad-params")
        action = params.get("action")
        if action not in ("accept", "reject"):
            raise JobException("say whether to 'accept' or 'reject' the tracked changes (action)", "bad-params")
        authors = params.get("authors")
        if authors is not None and (not isinstance(authors, list) or not authors
                                    or not all(isinstance(a, str) and a.strip() for a in authors)):
            raise JobException("authors must be a list of names", "bad-params")
        since, until = user_date(params.get("on_or_after"), "on_or_after"), user_date(params.get("before"), "before")
        if since is not None and until is not None and since >= until:
            raise JobException("on_or_after must come before 'before'", "bad-params")
        allow_missing = params.get("allow_missing", True)
        if not isinstance(allow_missing, bool):
            raise JobException("allow_missing must be true or false", "bad-params")
        sel = Selection({author_key(a) for a in authors} if authors else None, since, until)
        scope = resolve_scope(select, params, DEFAULT_SCOPE)
        parts = scope_parts(pkg, scope)
        frag = self.fragment({"scope": scope}, {**params, "allow_missing": allow_missing}, mode="untracked")
        frag.untracked_diffs_expected = True
        chosen: List[Revision] = []
        left_alone = undated = 0
        expected_gone: Dict[str, Dict[str, int]] = {}
        expected_selected: Dict[str, int] = {}
        changed: List[str] = []
        for part in parts:
            frag.touches.append((part, "scope", "verify"))          # V10 re-derives over every part in scope
            root = pkg.xml(part)
            res = analyze(root, part, action, sel)
            sel_revs = res.selected()
            chosen += sel_revs
            left_alone += sum(1 for r in res.revisions if not r.selected)
            undated += sum(1 for r in res.revisions if r.undated)
            if not res.changes:
                continue
            delta = predict(frag, root, res)
            for k, v in delta.items():
                frag.rev_delta[k] = frag.rev_delta.get(k, 0) + v
            expected_gone[part] = {k: -v for k, v in sorted(delta.items())}
            expected_selected[part] = len(sel_revs)
            changed.append(part)
            frag.intended_parts.append(part)
            frag.touches.append((part, "*", "text"))
            if res.goback:
                frag.flags.append(f"{part}: Word's _GoBack bookmark (where the cursor was last) lay in text that goes; "
                                  f"it goes whole (Word makes a new one on save)")
        filters = (([f"by {', '.join(authors)}"] if authors else [])
                   + ([f"on or after {params['on_or_after']}"] if since else [])
                   + ([f"before {params['before']}"] if until else []))
        what = "tracked change(s)" + (" " + " and ".join(filters) if filters else "")
        if undated:
            frag.flags.append(f"{undated} tracked change(s) carry no readable date: a date filter cannot choose them, "
                              f"so they stay as they are")
        for extra in _revisions_elsewhere(pkg):
            frag.flags.append(extra)
        if not changed:
            if allow_missing:
                frag.notes.append(f"resolve_tracked_changes: no {what} in {scope}: nothing to do"
                                  + (f" ({left_alone} other tracked change(s) stay as they are)" if left_alone else ""))
                return frag
            raise JobException(f"no {what} to {action} in {scope}", "not-found", {"scope": scope})
        verb = "ACCEPTS" if action == "accept" else "REJECTS"
        frag.flags.append(f"{verb} {len(chosen)} {what} — {_counts(chosen)}")
        if action == "reject" and any(r.cls == "content" and r.tag in ("del", "moveFrom") for r in chosen):
            frag.flags.append("rejected deletions bring their words back into the text")
        lost = sum(frag.rev_delta.values()) * -1 - len(chosen)
        if lost > 0:
            frag.notes.append(f"{lost} other revision element(s) lie inside text that goes (or are a resolved "
                              f"revision's own) and go with it")
        if left_alone:
            frag.notes.append(f"{left_alone} tracked change(s) not chosen stay as they are")
        frag.property_assertions.append({
            "kind": "resolved_changes", "action": action, "authors": list(authors or []),
            "on_or_after": params.get("on_or_after"), "before": params.get("before"),
            "asked_scope": select.get("scope") or params.get("scope"),          # as asked: the verifier's default if none
            "parts": changed, "expected_gone": expected_gone, "expected_selected": expected_selected})
        frag.geometry_reference = "none"
        frag.geometry_reason = "accepting or rejecting tracked changes reflows the text"
        frag.transform_data = {"action": action, "authors": list(authors or []),
                               "on_or_after": params.get("on_or_after"), "before": params.get("before"),
                               "parts": changed, "selected": expected_selected}
        frag.notes.append(f"resolve_tracked_changes: {action} {len(chosen)} {what} in {len(changed)} part(s)")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td or not td.get("parts"):
            return
        authors = td.get("authors") or []
        sel = Selection({author_key(a) for a in authors} if authors else None,
                        user_date(td.get("on_or_after"), "on_or_after"), user_date(td.get("before"), "before"))
        for part in td["parts"]:
            root = pkg.xml(part)
            res = analyze(root, part, td["action"], sel)
            if len(res.selected()) != td["selected"][part]:
                raise JobException(f"internal: {part} holds {len(res.selected())} chosen tracked change(s) at apply, "
                                   f"the plan counted {td['selected'][part]}", "internal")
            resolve(root, res)
            pkg.write_xml(part, root)


def _revisions_elsewhere(pkg: DocxPackage) -> List[str]:
    """Flags for tracked changes in styles.xml / numbering.xml: they are not resolved here."""
    out = []
    rels = pkg.rels(pkg.main_part())
    for kind in ("styles", "numbering"):
        for r in rels.values():
            if r.type == REL_TYPE_BASE + kind and r.resolved and pkg.has(r.resolved):
                n = sum(1 for e in pkg.xml(r.resolved).iter() if revision_kind(e))
                if n:
                    out.append(f"{r.resolved} carries {n} tracked change(s) of its own (a style or list definition); "
                               f"they stay as they are — resolve them in Word")
    return out
