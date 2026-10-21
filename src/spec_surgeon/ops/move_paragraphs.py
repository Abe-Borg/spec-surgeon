"""Op: move_paragraphs — move one unbroken stretch of paragraphs (the tables between them with them) to before or after
one anchor paragraph in the same place (docs/BUILTIN_OPS_BATCH6_DESIGN.md §10). Untracked, the blocks move exactly as
they are. Tracked (the default: the reviewer must see the move), they are deleted where they were, as delete_paragraphs
deletes, and a copy is inserted where they go, as insert_paragraph inserts — Word shows a deletion and an insertion; its
move markup (w:moveFrom/w:moveTo) is not used (the design's decision 4).

Definitions shared with the verifier (verify/moving.py re-implements them):

* **Container**: a w:body, w:tc, w:hdr, w:ftr, w:footnote, w:endnote or w:comment. The chosen paragraphs' container is
  the parent of the shallowest chosen paragraph; every chosen paragraph must lie in it (``move-container``). The
  **range** is the container's children from its first to its last chosen child paragraph: every w:p child between
  them is chosen (``move-gap``, naming the first one that is not), every other chosen paragraph lies inside a table or
  content control in the range (``move-gap``). A range may hold paragraphs, tables, content controls (untracked; not
  data-bound) and the marks between blocks (bookmarks, comment and permission ranges, proofing marks); it never holds a
  section break (``section-break``) or a text box (``textbox``).
* **Anchor**: the one paragraph of the same part ``to`` chooses (none: ``not-found``; several: ``ambiguous``), a child
  of the same container outside the range (``move-anchor``). **Destination**: right after the anchor (``after``) or
  right before it (``before``). A destination with nothing but marks between it and the range is where the range
  already is: nothing moves (a note).
* **Containers stay valid** (rule 0.5): once the range has left, the container must not end in a table nor hold two
  tables with only marks between them (``move-table-edge``). The range starts and ends with a paragraph, so where it
  goes it never makes either.
* **Split ranges**: a bookmark, comment (its range marks and reference), editing permission, pending Word move range or
  complex field (its begin, separate and end) with one end in the range and the other outside it refuses
  (``move-split-range``): moving would cut it. The destination never lies inside a complex field (``move-anchor``): what lands in a table of
  contents' result Word replaces when it updates fields.
* **Untracked**: the range's elements are taken out and put at the destination, in order, unchanged.
* **Tracked**: the range holds only paragraphs and the bookmarks and proofing marks between them; each paragraph only
  runs (text, tabs, breaks, hyphens, symbols), bookmarks and proofing marks (``move-tracked-content``) and no pending
  revision (``existing-revisions``). Where the range was, its bookmarks are taken out, each paragraph's mark gets a
  w:del and each unbroken sequence of its runs one w:del around them (their w:t become w:delText). At the destination
  goes a copy of the range as it was, bookmarks included (they travel with the text, so accepting the move leaves every
  cross-reference pointing at it), each copied paragraph without w14:paraId/w14:textId (Word gives each paragraph its
  own), its mark given a w:ins and each unbroken sequence of its runs one w:ins.

Automatic numbers (list and heading numbering, footnote marks) follow the new position: Word renumbers, no number is
retyped (flagged).
"""
from __future__ import annotations

import copy
import getpass
import os
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from lxml import etree

from ..ooxml import (RPR_ORDER, RevisionStamp, block_full_text, count_revisions, file_uses_date_utc, insert_in_order,
                     mark_rpr, max_revision_id, paragraphs, to_del_text)
from ..package import W, DocxPackage
from . import prose, register
from .base import BlockDiff, JobException, Op, PlanFragment
from .fields import fields_of
from .selectors import SCOPE_KINDS, StyleSheet, check_paragraph_selector, is_targeted, paragraph_selected, scope_parts
from .slots import nearest_p

W14 = "{http://schemas.microsoft.com/office/word/2010/wordml}"

CONTAINERS = {W + t for t in ("body", "tc", "hdr", "ftr", "footnote", "endnote", "comment")}
BOOKMARKS = {W + "bookmarkStart", W + "bookmarkEnd"}
MARKS = BOOKMARKS | {W + t for t in ("commentRangeStart", "commentRangeEnd", "permStart", "permEnd", "proofErr")}
# container children that are neither blocks nor marks: a cell's properties, the body's last section
PROPERTIES = {W + "tcPr", W + "sectPr"}
# the paired marks a move must take whole: (family, id) for every end
PAIRED = {W + "bookmarkStart": "bookmark", W + "bookmarkEnd": "bookmark", W + "commentRangeStart": "comment",
          W + "commentRangeEnd": "comment", W + "commentReference": "comment", W + "permStart": "permission",
          W + "permEnd": "permission", W + "moveFromRangeStart": "pending move (from)",
          W + "moveFromRangeEnd": "pending move (from)", W + "moveToRangeStart": "pending move (to)",
          W + "moveToRangeEnd": "pending move (to)"}
TRACKED_PARAGRAPH_CHILDREN = {W + "pPr", W + "r", W + "proofErr"} | BOOKMARKS
TRACKED_RUN_CHILDREN = {W + t for t in ("rPr", "t", "tab", "br", "cr", "noBreakHyphen", "softHyphen", "sym", "ptab",
                                        "lastRenderedPageBreak")}
COPY_DROPS = (W14 + "paraId", W14 + "textId")


def default_author() -> str:
    return os.environ.get("SPEC_SURGEON_AUTHOR") or getpass.getuser()


def _local(el) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else "#node"


def _is_block(el) -> bool:
    return isinstance(el.tag, str) and el.tag not in MARKS and el.tag not in PROPERTIES


def _path(el) -> List[int]:
    out = []
    while el.getparent() is not None:
        out.append(el.getparent().index(el))
        el = el.getparent()
    return list(reversed(out))


def _at(root, path: Sequence[int]):
    el = root
    for i in path:
        el = el[i]
    return el


def _quoted(text: str) -> str:
    return prose.quote(text if len(text) <= 60 else text[:59] + "…")


def run_groups(p, transparent: Set[str] = frozenset()) -> List[List[Any]]:
    """The paragraph's unbroken sequences of direct w:r children; a child whose tag is in ``transparent`` does not
    break a sequence (the origin of a tracked move loses its bookmarks first), any other one does."""
    groups: List[List[Any]] = []
    current: Optional[List[Any]] = None
    for child in p:
        if child.tag == W + "r":
            if current is None:
                current = []
                groups.append(current)
            current.append(child)
        elif child.tag not in transparent:
            current = None
    return groups


def destination(kids: Sequence[Any], anchor, position: str) -> int:
    """Where the range goes among the container's children (``kids``, the container as it is): right after or right
    before the anchor."""
    return kids.index(anchor) + (1 if position == "after" else 0)


def place(elements: Sequence[Any], anchor, position: str) -> None:
    """Put ``elements``, in order, right after (or right before) ``anchor``."""
    if position == "after":
        ref = anchor
        for el in elements:
            ref.addnext(el)
            ref = el
    else:
        for el in elements:
            anchor.addprevious(el)


def _numbered(p, sheet: StyleSheet) -> bool:
    """Whether Word numbers the paragraph automatically: its own w:numPr (numId other than 0), else its style chain's."""
    own = p.find(f"{W}pPr/{W}numPr")
    for numpr in [own] + [st.find(f"{W}pPr/{W}numPr") for st in sheet.lineage(sheet.paragraph_style_id(p))]:
        if numpr is not None:
            ni = numpr.find(W + "numId")
            return ni is None or ni.get(W + "val") != "0"
    return False


def _section_before(kids: Sequence[Any], index: int) -> int:
    """The body section a child at ``index`` belongs to: the number of section breaks before it."""
    return sum(1 for k in kids[:index] if k.tag == W + "p" and k.find(f"{W}pPr/{W}sectPr") is not None)


class _Move:
    """One part's move, as plan() decided it."""

    def __init__(self, part: str, container, first: int, last: int, anchor, anchor_index: int, position: str):
        self.part, self.container, self.first, self.last = part, container, first, last
        self.anchor, self.anchor_index, self.position = anchor, anchor_index, position
        self.kids = list(container)
        self.range = self.kids[self.first:self.last + 1]
        self.pars = [p for el in self.range if isinstance(el.tag, str) for p in el.iter(W + "p")]
        self.at = destination(self.kids, anchor, position)
        between = (self.kids[self.at:self.first] if self.at <= self.first else self.kids[self.last + 1:self.at])
        self.noop = not any(_is_block(k) for k in between)


def _locate(part: str, root, pars: List[Any], chosen: List[int], to: Dict[str, Any], position: str, tracked: bool,
            sheet: StyleSheet) -> _Move:
    """The range, the anchor and the destination in one part (module docstring), or the refusal."""
    els = [pars[i] for i in chosen]
    index = {id(p): i for i, p in enumerate(pars)}
    for p in els:
        if nearest_p(p) is not None:
            raise JobException(f"{part} paragraph {index[id(p)]} to move is inside a text box; move it in Word",
                               "textbox", {"part": part, "paragraph": index[id(p)]})
    top = min(els, key=lambda p: sum(1 for _ in p.iterancestors()))
    box = top.getparent()
    if box.tag not in CONTAINERS:
        raise JobException(f"{part}: the paragraphs to move start inside a {_local(box)}, which this op does not move "
                           f"paragraphs within", "move-container", {"part": part, "container": _local(box)})
    for p in els:
        if not any(a is box for a in p.iterancestors()):
            raise JobException(f"{part}: the paragraphs to move are in more than one place (paragraph "
                               f"{index[id(top)]} is in a {_local(box)}, paragraph {index[id(p)]} elsewhere); move "
                               f"each stretch on its own", "move-container",
                               {"part": part, "paragraphs": [index[id(top)], index[id(p)]]})
    kids = list(box)
    where = {id(k): i for i, k in enumerate(kids)}           # the list keeps every proxy alive: ids are stable
    own = [p for p in els if p.getparent() is box]
    first, last = where[id(own[0])], where[id(own[-1])]
    chosen_ids = {id(p) for p in els}
    for p in els:
        holder = p
        while holder.getparent() is not box:
            holder = holder.getparent()
        if not first <= where[id(holder)] <= last:
            raise JobException(f"{part} paragraph {index[id(p)]} ({_quoted(block_full_text(p))}) is chosen but lies in "
                               f"a {_local(holder)} outside the stretch from paragraph {index[id(own[0])]} to "
                               f"{index[id(own[-1])]}", "move-gap", {"part": part, "paragraph": index[id(p)]})
    for el in kids[first:last + 1]:
        if el.tag == W + "p" and id(el) not in chosen_ids:
            raise JobException(f"{part}: the paragraphs to move are not one unbroken stretch — paragraph "
                               f"{index[id(el)]} ({_quoted(block_full_text(el))}) between them is not chosen",
                               "move-gap", {"part": part, "paragraph": index[id(el)]})
    move_range = kids[first:last + 1]
    for el in move_range:
        if not isinstance(el.tag, str):
            continue
        if el.tag == W + "sdt" and tracked:
            raise JobException(f"{part}: a content control lies among the paragraphs to move; a tracked move cannot "
                               f"copy it — move them untracked", "move-tracked-content", {"part": part})
        if el.tag == W + "sdt" and el.find(f"{W}sdtPr/{W}dataBinding") is not None:
            raise JobException(f"{part}: a content control bound to data lies among the paragraphs to move; move it "
                               f"in Word", "structure", {"part": part})
        if el.tag not in MARKS and el.tag not in (W + "p", W + "tbl", W + "sdt"):
            raise JobException(f"{part}: a {_local(el)} lies among the paragraphs to move; this op does not move it",
                               "structure", {"part": part, "element": _local(el)})
        if el.find(f".//{W}txbxContent") is not None:
            raise JobException(f"{part}: the paragraphs to move hold a text box; move them in Word", "textbox",
                               {"part": part})
        for p in el.iter(W + "p"):
            if p.find(f"{W}pPr/{W}sectPr") is not None:
                raise JobException(f"{part} paragraph {index[id(p)]} to move carries a section break (w:sectPr); "
                                   f"leave it out of the stretch", "section-break",
                                   {"part": part, "paragraph": index[id(p)]})
    if tracked:
        _check_tracked(part, move_range, index)
    _check_split(part, root, move_range)
    _check_field_cut(part, root, move_range)
    hits =[i for i, p in enumerate(pars) if paragraph_selected(to, p, i, block_full_text(p), sheet, all_pars=pars)]
    if not hits:
        raise JobException(f"{part}: no paragraph matches the destination {to}", "not-found",
                           {"part": part, "to": to})
    if len(hits) > 1:
        raise JobException(f"{part}: {len(hits)} paragraphs match the destination {to} (paragraphs {hits[:5]}); it "
                           f"must name exactly one", "ambiguous", {"part": part, "to": to, "count": len(hits)})
    anchor = pars[hits[0]]
    if any(p is anchor for el in move_range if isinstance(el.tag, str) for p in el.iter(W + "p")):
        raise JobException(f"{part}: the destination paragraph {hits[0]} ({_quoted(block_full_text(anchor))}) is one "
                           f"of the paragraphs to move", "move-anchor", {"part": part, "paragraph": hits[0]})
    if anchor.getparent() is not box:
        raise JobException(f"{part}: the destination paragraph {hits[0]} ({_quoted(block_full_text(anchor))}) is not in "
                           f"the same place as the paragraphs to move (it is in a {_local(anchor.getparent())}, they "
                           f"are in a {_local(box)})", "move-anchor", {"part": part, "paragraph": hits[0]})
    move = _Move(part, box, first, last, anchor, hits[0], position)
    if not move.noop:
        before = [k for k in kids[:first] if _is_block(k)]
        after = [k for k in kids[last + 1:] if _is_block(k)]
        prev = before[-1] if before else None
        nxt = after[0] if after else None
        if prev is not None and prev.tag == W + "tbl" and (nxt is None or nxt.tag == W + "tbl"):
            what = "two tables would touch" if nxt is not None else f"the {_local(box)} would end in a table"
            raise JobException(f"{part}: once the paragraphs leave, {what}; include the table in the move or leave a "
                               f"paragraph beside it", "move-table-edge", {"part": part})
        _check_field_destination(part, root, move)
    return move


def _check_tracked(part: str, move_range: Sequence[Any], index: Dict[int, int]) -> None:
    for el in move_range:
        if not isinstance(el.tag, str):
            continue
        if el.tag != W + "p" and el.tag not in BOOKMARKS and el.tag != W + "proofErr":
            raise JobException(f"{part}: a {_local(el)} lies among the paragraphs to move; a tracked move cannot copy "
                               f"it — move them untracked", "move-tracked-content", {"part": part, "element": _local(el)})
        if el.tag != W + "p":
            continue
        bi = index[id(el)]
        if count_revisions(el):
            raise JobException(f"{part} paragraph {bi} to move already carries pending revisions; accept or reject "
                               f"them first, or move untracked", "existing-revisions", {"part": part, "paragraph": bi})
        odd = sorted({_local(c) for c in el if isinstance(c.tag, str) and c.tag not in TRACKED_PARAGRAPH_CHILDREN}
                     | {_local(k) for c in el if c.tag == W + "r" for k in c
                        if not isinstance(k.tag, str) or k.tag not in TRACKED_RUN_CHILDREN})
        if odd:
            raise JobException(f"{part} paragraph {bi} to move holds {odd}; a tracked move copies only text, tabs, "
                               f"breaks and bookmarks — move it untracked", "move-tracked-content",
                               {"part": part, "paragraph": bi, "elements": odd})


def _check_split(part: str, root, move_range: Sequence[Any]) -> None:
    held = [x for el in move_range if isinstance(el.tag, str) for x in el.iter(*PAIRED)]
    inside = {id(x) for x in held}                          # ``held`` keeps every proxy alive: ids are stable
    ends: Dict[Tuple[str, str], List[bool]] = {}
    for x in root.iter(*PAIRED):
        ends.setdefault((PAIRED[x.tag], x.get(W + "id") or ""), []).append(id(x) in inside)
    for (family, rid), flags in ends.items():
        if any(flags) and not all(flags):
            raise JobException(f"{part}: a {family} (id {rid}) starts or ends among the paragraphs to move and has its "
                               f"other end outside them; moving would cut it", "move-split-range",
                               {"part": part, "family": family, "id": rid})


def _check_field_cut(part: str, root, move_range: Sequence[Any]) -> None:
    """A complex field (ops/fields.py's reading) lies wholly in the stretch or wholly outside it."""
    held = [x for el in move_range if isinstance(el.tag, str) for x in el.iter(W + "fldChar")]
    if not held:
        return
    inside = {id(x) for x in held}                          # ``held`` keeps every proxy alive: ids are stable
    for f in fields_of(root):
        if len({id(m) in inside for m in (f.begin, f.separate, f.end) if m is not None}) > 1:
            raise JobException(f"{part}: a {f.type or 'complex'} field starts or ends among the paragraphs to move and "
                               f"has its other end outside them; moving would cut it", "move-split-range",
                               {"part": part, "family": "field", "field": f.type})


def _check_field_destination(part: str, root, move: "_Move") -> None:
    """The destination does not lie inside a complex field: what lands in a field's result (a table of contents) Word
    replaces when it updates fields."""
    fields = [f for f in fields_of(root) if f.begin is not None]
    if not fields:
        return
    everything = list(root.iter())
    order = {id(el): i for i, el in enumerate(everything)}
    point = (order[id(move.kids[move.at])] if move.at < len(move.kids)
             else order[id(list(move.container.iter())[-1])] + 1)          # right after the container's last node
    for f in fields:
        if order[id(f.begin)] < point and (f.end is None or order[id(f.end)] >= point):
            raise JobException(f"{part}: the destination lies inside a {f.type or 'complex'} field that runs over "
                               f"several paragraphs; Word would replace the moved paragraphs when it updates fields — "
                               f"choose a destination outside it", "move-anchor", {"part": part, "field": f.type})


def _deleted_in_place(p, stamp: RevisionStamp, next_id: List[int]) -> None:
    """The origin of a tracked move: bookmarks out, the mark deleted, each unbroken run sequence in a w:del."""
    for b in [c for c in p if c.tag in BOOKMARKS]:
        p.remove(b)
    mark = etree.Element(W + "del")
    stamp.apply(mark, next_id[0]); next_id[0] += 1
    insert_in_order(mark_rpr(p, create=True), mark, RPR_ORDER)
    for group in run_groups(p):
        wrap = etree.Element(W + "del")
        stamp.apply(wrap, next_id[0]); next_id[0] += 1
        p.insert(p.index(group[0]), wrap)
        for r in group:
            p.remove(r)
            to_del_text(r)
            wrap.append(r)


def _inserted_copy(el, stamp: RevisionStamp, next_id: List[int]):
    """The destination of a tracked move: a copy of the range element, a paragraph's mark and runs inserted."""
    c = copy.deepcopy(el)
    if c.tag != W + "p":
        return c
    for a in COPY_DROPS:
        c.attrib.pop(a, None)
    mark = etree.Element(W + "ins")
    stamp.apply(mark, next_id[0]); next_id[0] += 1
    insert_in_order(mark_rpr(c, create=True), mark, RPR_ORDER)
    for group in run_groups(c):
        wrap = etree.Element(W + "ins")
        stamp.apply(wrap, next_id[0]); next_id[0] += 1
        c.insert(c.index(group[0]), wrap)
        for r in group:
            c.remove(r)
            wrap.append(r)
    return c


@register
class MoveParagraphs(Op):
    name = "move_paragraphs"
    mode = "content"
    description = ("Move one unbroken stretch of selected paragraphs (with the tables between them) to before or after "
                   "the one paragraph 'to' names, in the same place: tracked (deleted where they were, inserted where "
                   "they go) by default, or untracked.")
    params_schema = {
        "type": "object",
        "properties": {
            "to": {"type": "object", "description": "Paragraph predicate naming exactly one destination paragraph, in "
                                                    "the same part and container as the paragraphs to move (required)."},
            "position": {"type": "string", "enum": ["before", "after"],
                         "description": "Put the paragraphs right 'before' or right 'after' that paragraph (required)."},
            "tracked": {"type": "boolean", "default": True,
                        "description": "true = deleted where they were and inserted where they go, as tracked changes; "
                                       "false = moved as they are, untracked."},
            "author": {"type": "string", "description": "Tracked-change author (default: SPEC_SURGEON_AUTHOR or OS user)."},
            "allow_missing": {"type": "boolean", "default": False,
                              "description": "If true, a file with no paragraph to move is left as it is instead of "
                                             "set aside."},
            "date_convention": {"type": "string", "enum": ["word-local-z", "utc"], "default": "word-local-z"},
        },
        "required": ["to", "position"],
    }
    select_schema = {
        "type": "object",
        "properties": {
            "scope": {"type": "array", "items": {"type": "string", "enum": list(SCOPE_KINDS)}, "default": ["document"]},
            "paragraphs": {"type": "object", "description": "Targeted paragraph predicate choosing one unbroken stretch "
                                                            "of paragraphs (between_paragraphs is the natural one)."},
        },
    }
    verification_profile = {
        "V2": "untracked: every moved paragraph deleted where it was (after=None) and inserted where it goes "
              "(before=None); tracked: the copies' insertions only (the originals keep their text as w:delText)",
        "V4": "tracked: +1 del_mark and +1 ins_mark per paragraph, +1 del and +1 ins per unbroken run sequence; "
              "untracked: 0",
        "V5b": "tracked: reject-all view equals source; untracked: enumerated-list mode",
        "V8": "tracked: accept-all view empties every original and shows each copy at its exact output index",
        "V10": "moved_paragraphs re-derives the range, the anchor, the destination and the tracked markup, builds the "
               "expected part from the source and compares it whole",
    }

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select = dict(select or {})
        unknown = set(select) - {"scope", "paragraphs"}
        if unknown:
            raise JobException(f"unknown selector key(s) {sorted(unknown)}", "bad-params")
        para_pred = select.get("paragraphs")
        check_paragraph_selector(para_pred)
        if not is_targeted(select):
            raise JobException("move_paragraphs needs a targeted 'paragraphs' selector naming the paragraphs to move",
                               "bad-params")
        to = params.get("to")
        if not isinstance(to, dict):
            raise JobException("'to' must be a paragraph predicate naming the destination paragraph", "bad-params")
        check_paragraph_selector(to)
        if not is_targeted({"paragraphs": to}):
            raise JobException("'to' must name one paragraph (text_regex, style_in …), not every paragraph", "bad-params")
        position = params.get("position")
        if position not in ("before", "after"):
            raise JobException(f"position must be 'before' or 'after', got {position!r}", "bad-params")
        tracked = params.get("tracked", True)
        for key in ("tracked", "allow_missing"):
            if key in params and not isinstance(params[key], bool):
                raise JobException(f"'{key}' must be true or false", "bad-params")
        if params.get("date_convention", "word-local-z") not in ("word-local-z", "utc"):
            raise JobException("date_convention must be 'word-local-z' or 'utc'", "bad-params")
        if "author" in params and (not isinstance(params["author"], str) or not params["author"].strip()):
            raise JobException("'author' must be a name", "bad-params")
        author = params.get("author") or default_author()
        scope = select.get("scope") or ["document"]
        if isinstance(scope, str):
            scope = [scope]
        parts = scope_parts(pkg, scope)
        sheet = StyleSheet(pkg)

        frag = self.fragment({"scope": scope, "paragraphs": para_pred},
                             {**params, "position": position, "tracked": tracked, "author": author},
                             mode=("content" if tracked else "untracked"))
        frag.untracked_diffs_expected = not tracked

        moves: List[Dict[str, Any]] = []
        record: Dict[str, Dict[str, Any]] = {}
        total = 0
        d_del = d_ins = 0
        for part in parts:
            root = pkg.xml(part)
            pars = paragraphs(root)
            chosen = [i for i, p in enumerate(pars)
                      if paragraph_selected(para_pred, p, i, block_full_text(p), sheet, all_pars=pars)]
            if not chosen:
                continue
            move = _locate(part, root, pars, chosen, to, position, tracked, sheet)
            index = {id(p): i for i, p in enumerate(pars)}
            m0, n = index[id(move.pars[0])], len(move.pars)
            tables = sum(1 for el in move.range if isinstance(el.tag, str) and el.tag == W + "tbl")
            record[part] = {"first": m0, "paragraphs": n, "tables": tables, "anchor": move.anchor_index,
                            "noop": move.noop}
            total += n
            if move.noop:
                frag.notes.append(f"move_paragraphs: {part}: the {n} paragraph(s) are already {position} the "
                                  f"destination paragraph {move.anchor_index}; nothing moves.")
                frag.touches.append((part, "*", "verify"))
                continue
            # where the destination is among the part's paragraphs: after the anchor and what it holds (a text box's)
            at_p = move.anchor_index + (len(list(move.anchor.iter(W + "p"))) if position == "after" else 0)
            texts = [block_full_text(p) for p in move.pars]
            if tracked:
                for k, text in enumerate(texts):
                    frag.diffs.append(BlockDiff(part=part, block_index=at_p + k, before=None, after=text,
                                                accepted_view=text, note=f"moved from block {m0 + k} (tracked copy)"))
                    frag.accepted_views.append({"part": part, "block_index": m0 + k, "accepted_view": ""})
                for p in move.pars:
                    d_del += len(run_groups(p, transparent=BOOKMARKS))
                    d_ins += len(run_groups(p))
            else:
                out0 = at_p if at_p <= m0 else at_p - n
                for k, text in enumerate(texts):
                    frag.diffs.append(BlockDiff(part=part, block_index=m0 + k, before=text, after=None,
                                                note=f"moved to block {out0 + k} (untracked)"))
                    frag.diffs.append(BlockDiff(part=part, block_index=out0 + k, before=None, after=text,
                                                note=f"moved from block {m0 + k} (untracked)"))
            moves.append({"part": part, "container": _path(move.container), "first": move.first, "last": move.last,
                          "anchor": move.kids.index(move.anchor)})
            frag.touches.append((part, "*", "text"))
            frag.intended_parts.append(part)
            self._flags(frag, part, move, sheet, tracked, index)

        if total == 0:
            if params.get("allow_missing"):
                frag.notes.append(f"0 paragraphs to move matched in {scope}; no-op.")
                return frag
            raise JobException(f"no paragraphs matched selector {para_pred} in {scope}", "not-found",
                               {"scope": scope, "paragraphs": para_pred})

        moved = sum(r["paragraphs"] for r in record.values() if not r["noop"])
        if tracked:
            frag.rev_delta["del"] = frag.rev_delta.get("del", 0) + d_del
            frag.rev_delta["ins"] = frag.rev_delta.get("ins", 0) + d_ins
            if moved:
                frag.rev_delta["del_mark"] = frag.rev_delta.get("del_mark", 0) + moved
                frag.rev_delta["ins_mark"] = frag.rev_delta.get("ins_mark", 0) + moved
            frag.rev_id_base = max_revision_id(pkg) + 1
            frag.rev_ids_needed = d_del + d_ins + 2 * moved
        elif moved:
            frag.flags.append(f"UNTRACKED move of {moved} paragraph(s) at the user's request.")
        if moved:
            frag.geometry_reference = "none"
            frag.geometry_reason = "moving paragraphs reflows the pages"
        frag.property_assertions.append({
            "kind": "moved_paragraphs",
            "scope": scope,
            "select": {"paragraphs": para_pred},
            "to": to,
            "position": position,
            "tracked": tracked,
            "author": author,
            "moves": record,
            "expected_paragraphs": total,
        })
        frag.transform_data = {
            "moves": moves,
            "position": position,
            "tracked": tracked,
            "author": author,
            "use_date_utc": file_uses_date_utc(pkg),
            "first_rev_id": frag.rev_id_base,
            "date_convention": params.get("date_convention", "word-local-z"),
        }
        frag.notes.append(f"move_paragraphs: {moved} paragraph(s) {position} their destination "
                          f"({'tracked' if tracked else 'untracked'}) in {len(frag.intended_parts)} part(s).")
        return frag

    @staticmethod
    def _flags(frag: PlanFragment, part: str, move: _Move, sheet: StyleSheet, tracked: bool,
               index: Dict[int, int]) -> None:
        tables = sum(1 for el in move.range if isinstance(el.tag, str) and el.tag == W + "tbl")
        frag.notes.append(f"move_paragraphs: {part}: paragraphs {index[id(move.pars[0])]}–"
                          f"{index[id(move.pars[-1])]} ({len(move.pars)}, {tables} table(s) among them) go "
                          f"{move.position} paragraph {move.anchor_index} "
                          f"({_quoted(block_full_text(move.anchor))}).")
        numbered = sum(1 for p in move.pars if _numbered(p, sheet))
        if numbered:
            frag.flags.append(f"{part}: {numbered} moved paragraph(s) are numbered automatically; their numbers, and "
                              f"those of the lists where they were and where they go, follow the new order (Word "
                              f"renumbers; no number is retyped).")
        if move.container.tag == W + "body":
            here, there = _section_before(move.kids, move.first), _section_before(move.kids, move.at)
            if here != there:
                frag.flags.append(f"{part}: the paragraphs move from section {here + 1} to section {there + 1} and take "
                                  f"its page setup, headers and footers.")
        held = [x for el in move.range if isinstance(el.tag, str) for x in el.iter()]
        if any(x.tag in (W + "footnoteReference", W + "endnoteReference") for x in held):
            frag.flags.append(f"{part}: footnote or endnote references move with the paragraphs; Word renumbers the "
                              f"notes in their new order.")
        if any(x.tag in (W + "fldChar", W + "fldSimple") for x in held):
            frag.flags.append(f"{part}: fields move with the paragraphs as they are; page numbers and "
                              f"cross-references they show update when Word updates fields.")
        if not tracked and any(count_revisions(el) for el in move.range if isinstance(el.tag, str)):
            frag.flags.append(f"{part}: pending tracked changes in the moved paragraphs move with them.")
        if tracked and any(x.tag == W + "bookmarkStart" for x in held):
            frag.flags.append(f"{part}: bookmarks among the moved paragraphs go with the inserted copy, so "
                              f"cross-references find the text where it goes; rejecting the move leaves them there.")
        # Word never deletes a container's last paragraph mark: accepting (or rejecting) a tracked change of it leaves
        # an empty paragraph
        if tracked and not any(_is_block(k) for k in move.kids[move.last + 1:]):
            frag.flags.append(f"{part}: the moved paragraphs end their {_local(move.container)}; Word keeps its last "
                              f"paragraph mark, so accepting the move leaves an empty paragraph where they were.")
        if tracked and not any(_is_block(k) for k in move.kids[move.at:]):
            frag.flags.append(f"{part}: the moved paragraphs land at the end of their {_local(move.container)}; "
                              f"rejecting the move leaves an empty paragraph there.")

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td or not td.get("moves"):
            return
        tracked = td["tracked"]
        position = td["position"]
        stamp = RevisionStamp.now(td["author"], td["use_date_utc"], td.get("date_convention", "word-local-z"))
        next_id = [td.get("first_rev_id", 1)]
        for move in td["moves"]:
            root = pkg.xml(move["part"])
            box = _at(root, move["container"])
            kids = list(box)
            elements = kids[move["first"]:move["last"] + 1]
            anchor = kids[move["anchor"]]
            if tracked:
                copies = [_inserted_copy(el, stamp, next_id) if isinstance(el.tag, str) else copy.deepcopy(el)
                          for el in elements]
                for el in elements:
                    if el.tag in BOOKMARKS:
                        box.remove(el)
                    elif el.tag == W + "p":
                        _deleted_in_place(el, stamp, next_id)
                place(copies, anchor, position)
            else:
                for el in elements:
                    box.remove(el)
                place(elements, anchor, position)
            pkg.write_xml(move["part"], root)

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        tracked = params.get("tracked", True) is not False
        what = _stretch_words(select.get("paragraphs"))
        if not tracked:
            what += ", together with any tables among them,"
        position = "before" if params.get("position") == "before" else "after"
        scope = prose.scope_words(select.get("scope"))
        where = f"to {position} {_anchor_words(params.get('to'))}" + (f" {scope}" if scope else "")
        if tracked:
            tail = ("shown as a tracked deletion where they are and a tracked insertion where they go"
                    + (f" by {prose.quote(params['author'])}" if params.get("author") else ""))
        else:
            tail = "moving them as they are without tracked changes"
        first = f"Move {what} {where}, {tail}."
        second = "Files with no such paragraphs are left unchanged." if params.get("allow_missing") else ""
        return prose.sentence(first, second)


def _end_core(end: str) -> str:
    return end[1:] if end.startswith("^") else end


def _stretch_words(pred) -> str:
    """The paragraphs to move, in words: a between_paragraphs stretch by its bounds (the 'up to the one before' idiom —
    the end included and ruled out again by a text_regex ``^(?!<end>)`` — read as such), anything else as prose does."""
    if not isinstance(pred, dict) or not isinstance(pred.get("between_paragraphs"), dict):
        words = prose.where({"paragraphs": pred})[3:]
        return "the " + words if not words.startswith("every") else "the chosen paragraphs"
    bp = pred["between_paragraphs"]
    start, end = bp.get("start_regex", ""), bp.get("end_regex")
    rest = {k: v for k, v in pred.items() if k not in ("between_paragraphs", "case_insensitive", "flexible_whitespace")}
    before_end = (end is not None and bp.get("include_bounds")
                  and rest.get("text_regex") == f"^(?!{_end_core(end)})")
    if before_end:
        rest.pop("text_regex")
        words = (f"the paragraphs from the one matching {prose.quote(start)} through the one before the one matching "
                 f"{prose.quote(end)}")
    elif bp.get("include_bounds"):
        words = (f"the paragraphs from the one matching {prose.quote(start)} through "
                 + (f"the one matching {prose.quote(end)}" if end is not None else "the last one"))
    elif end is not None:
        words = f"the paragraphs between the one matching {prose.quote(start)} and the one matching {prose.quote(end)}"
    else:
        words = f"the paragraphs after the one matching {prose.quote(start)}"
    if pred.get("case_insensitive"):
        words += " (ignoring case)"
    if rest:
        words += ", only the " + prose.where({"paragraphs": rest})[3:]
    return words


def _anchor_words(to) -> str:
    if not isinstance(to, dict) or not to:
        return "the destination paragraph"
    if set(to) - {"case_insensitive", "flexible_whitespace"} == {"text_regex"}:
        return f"the paragraph matching {prose.quote(to['text_regex'])}" + (
            " (ignoring case)" if to.get("case_insensitive") else "")
    words = prose.where({"paragraphs": to})[3:]
    return "the one " + words.replace("paragraphs", "paragraph", 1)
