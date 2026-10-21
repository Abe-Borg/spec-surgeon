"""Deleting and inserting table rows and tables — the transform side's shared core (built-in batch 6,
docs/BUILTIN_OPS_BATCH6_DESIGN.md §6): what delete_tables, delete_table_rows, insert_table_rows and insert_table share.
The verifier's own reading is verify/tablestructure.py (rule 1: nothing shared).

**What a deletion takes** (``removal``): the elements that go — whole tables, or own rows of a table — and everything in
them. Checked over that content, in this order (the first that applies refuses):

1. a paragraph carrying a section break (``w:pPr/w:sectPr``) → ``section-break``;
2. a comment's mark (``w:commentRangeStart``, ``w:commentRangeEnd``, ``w:commentReference``) → ``table-anchor``: the
   comment would stay without its place in the text (remove the comments first);
3. untracked, a footnote or endnote reference → ``table-anchor``: the note would stay without its mark (tracked, the
   reference run is deleted with the rest, as Word records it);
4. a complex field some of whose markers (begin, separate, end) lie inside and some outside, or a permission, move or
   custom-XML range with one end inside and one outside → ``table-anchor``;
5. tracked: a pending tracked change inside — an insertion, deletion or move of content, of a paragraph mark or of a
   row, or a cell insertion, deletion or merge — → ``existing-revisions``; a paragraph holding anything but its
   properties, runs, bookmarks and proofing marks (a hyperlink, content control, simple field, smart tag, equation …) →
   ``structure``; a text box (``w:txbxContent``) → ``textbox``.

Bookmarks never refuse. One wholly inside goes with it (untracked) or stays where it is (tracked). An end whose partner
(the ``w:bookmarkStart``/``w:bookmarkEnd`` with its ``w:id``) lies outside everything the op deletes in the part keeps its
place in the text: untracked, it takes the deleted element's place (bookmark marks are allowed between blocks and between
rows), with the others of that element in document order.

**Untracked** (``remove``): each deleted element is replaced by the bookmark ends it keeps. **A table going whole**
(``table_removal_problem``) is refused (``table-structure``) when, among its container's blocks (its ``w:p``, ``w:tbl``,
``w:sdt``, ``w:customXml`` and ``w:altChunk`` children — the body's ``w:sectPr`` and range marks are not blocks) without
the tables that go, nothing is left, the last block is not a paragraph where a table that goes was last, or two tables
stand side by side that did not before (Word would join them).

**Tracked** (``mark_deleted``), as Word records a tracked row or table deletion. Over the deleted rows in document order
(the rows of tables nested in them included): every ``w:tr`` gets a ``w:del`` in its ``w:trPr`` (created right after its
``w:tblPrEx``, first otherwise; the ``w:del`` in schema order); every ``w:p`` a ``w:del`` in its paragraph mark's
``w:rPr`` (``ooxml.mark_rpr``, in schema order), and every run of consecutive ``w:r`` children of it goes into a ``w:del``
of its own, its ``w:t`` becoming ``w:delText`` and its ``w:instrText`` ``w:delInstrText`` (``xml:space="preserve"`` added
to a text with edge whitespace). Revision ids in that order: a row's, a paragraph's mark's, then its run groups'.

**V4**: untracked, every revision element inside goes (a negative delta per kind, ``ooxml.revision_kind``); tracked, +1
``del`` per row, +1 ``del_mark`` per paragraph, +1 ``del`` per run group.

**A new row** (``new_row``, insert_table_rows) copies a reference row and carries no attributes (the source's rsids and
paragraph ids are its own): the reference row's ``w:tblPrEx`` without ``w:tblPrExChange``; its ``w:trPr`` without
``w:cnfStyle`` (Word's cache of the conditional formatting that applies), ``w:ins``, ``w:del``, ``w:trPrChange`` and
``w:tblHeader``, then ``w:tblHeader`` as asked — ``<w:tblHeader/>`` for a repeating header row, else
``<w:tblHeader w:val="0"/>`` when ANY level of the table style chain's ``w:trPr`` turns it on, else none
(``set_table_props.expected_row_element``) — and, tracked, a new ``w:ins``, each in schema order; then per own cell of
the reference row a ``w:tc`` with that cell's ``w:tcPr`` without ``w:cnfStyle``, ``w:cellIns``, ``w:cellDel``,
``w:cellMerge`` and ``w:tcPrChange``, and one paragraph (``new_paragraph``) whose ``w:pPr`` is the cell's first paragraph's
(``table_rows.cell_paragraphs``) without ``w:cnfStyle``, ``w:sectPr`` and ``w:pPrChange``, its mark's ``w:rPr`` without
revision marks and ``w:rPrChange`` (``cell_ppr``), and whose run has that paragraph's first live text run's ``w:rPr``
without ``w:rPrChange`` (``insert_paragraph.first_live_text_run``; none when that run has none) — or, when the paragraph
has no live text run, its mark's ``w:rPr`` as just cleaned (Word gives text typed there the mark's formatting)
(``cell_rpr``). An element left empty is left out.

**A new paragraph** (``new_paragraph``): its ``w:pPr`` (none, or as given); tracked, its mark carries a new ``w:ins``
(``ooxml.mark_rpr``, in schema order); a non-empty text is one run (``w:rPr`` as given, then one ``w:t`` written by
``ooxml.set_text``), tracked inside a new ``w:ins``; an empty text gives no run. Revision ids: the mark's, then the
run's. A row's ``w:ins`` comes before its cells'.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from lxml import etree

from ..ooxml import (RPR_ORDER, TRPR_ORDER, XML_SPACE, RevisionStamp, block_full_text, insert_in_order, mark_rpr,
                     revision_kind, set_text)
from ..package import W
from .base import JobException
from .fields import fields_of
from .insert_paragraph import first_live_text_run
from .set_table_props import expected_row_element, row_properties
from .table_rows import cell_paragraphs
from .table_select import own_cells

COMMENT_MARKS = (W + "commentRangeStart", W + "commentRangeEnd", W + "commentReference")
NOTE_REFERENCES = (W + "footnoteReference", W + "endnoteReference")
BOOKMARKS = (W + "bookmarkStart", W + "bookmarkEnd")
RANGE_FAMILIES = {W + "bookmarkStart": "bookmark", W + "bookmarkEnd": "bookmark",
                  W + "permStart": "permission range", W + "permEnd": "permission range",
                  W + "moveFromRangeStart": "move range", W + "moveFromRangeEnd": "move range",
                  W + "moveToRangeStart": "move range", W + "moveToRangeEnd": "move range",
                  W + "customXmlInsRangeStart": "custom XML range", W + "customXmlInsRangeEnd": "custom XML range",
                  W + "customXmlDelRangeStart": "custom XML range", W + "customXmlDelRangeEnd": "custom XML range",
                  W + "customXmlMoveFromRangeStart": "custom XML range",
                  W + "customXmlMoveFromRangeEnd": "custom XML range",
                  W + "customXmlMoveToRangeStart": "custom XML range",
                  W + "customXmlMoveToRangeEnd": "custom XML range"}
# the tags a range's two ends carry, by family and side, so a start pairs with its own end (moveFrom with moveFrom)
RANGE_KEY = {tag: tag[len(W):].replace("Start", "").replace("End", "") for tag in RANGE_FAMILIES}
PENDING_CHANGES = tuple(W + t for t in ("ins", "del", "moveFrom", "moveTo", "cellIns", "cellDel", "cellMerge"))
TRACKABLE_CHILDREN = (W + "pPr", W + "r", W + "bookmarkStart", W + "bookmarkEnd", W + "proofErr")
BLOCK_TAGS = (W + "p", W + "tbl", W + "sdt", W + "customXml", W + "altChunk")
PICTURES = (W + "drawing", W + "pict")
TEXT_BOX = W + "txbxContent"
MARK_REVISIONS = tuple(W + t for t in ("ins", "del", "moveFrom", "moveTo", "rPrChange"))


# ---- what a deletion takes ------------------------------------------------------------------------------------------

@dataclass
class Removal:
    """What deleting ``gone`` (whole tables, or rows) does, decided from the part as it is."""
    gone: List[Any]
    kept_marks: List[List[Any]] = field(default_factory=list)   # per gone element: bookmark ends that take its place
    pictures: int = 0
    bookmarks_gone: List[str] = field(default_factory=list)       # names of bookmarks wholly inside (untracked)
    references: List[str] = field(default_factory=list)            # of those, the ones a field elsewhere names


def _inside(el, gone: Set[Any]) -> bool:
    return el in gone or any(a in gone for a in el.iterancestors())


def removal(root, gone: Sequence[Any], tracked: bool, where: str) -> Removal:
    """The removal of ``gone`` from ``root`` (module docstring); ``JobException`` for what must be done in Word."""
    gone = list(gone)
    held = set(gone)
    for g in gone:
        for p in g.iter(W + "p"):
            if p.find(f"{W}pPr/{W}sectPr") is not None:
                raise JobException(f"{where}: a paragraph in it carries a section break", "section-break",
                                   {"where": where})
    for g in gone:
        if next(g.iter(*COMMENT_MARKS), None) is not None:
            raise JobException(f"{where} holds a comment's mark; the comment would stay without its place in the text "
                               f"— remove the comments first, or delete it in Word", "table-anchor", {"where": where})
    if not tracked:
        for g in gone:
            if next(g.iter(*NOTE_REFERENCES), None) is not None:
                raise JobException(f"{where} holds a footnote or endnote mark; deleting it outright would leave the note "
                                   f"without its mark — delete it as a tracked change, or in Word", "table-anchor",
                                   {"where": where})
    for f in fields_of(root):
        if f.simple is None:
            inside = [_inside(m, held) for m in (f.begin, f.separate, f.end) if m is not None]
            if any(inside) and not all(inside):
                raise JobException(f"{where} holds part of a {f.type or 'field'} field whose other part lies outside "
                                   f"it — delete it in Word", "table-anchor", {"where": where})
    ends: Dict[Tuple[str, str], List[Any]] = {}
    for el in root.iter(*RANGE_FAMILIES):
        ends.setdefault((RANGE_KEY[el.tag], el.get(W + "id") or ""), []).append(el)
    kept: Set[Any] = set()
    whole: List[Any] = []
    for (key, rid), els in ends.items():
        inside = [_inside(e, held) for e in els]
        if not any(inside):
            continue
        if all(inside):
            whole += [e for e in els if e.tag == W + "bookmarkStart"]
            continue
        if RANGE_FAMILIES[els[0].tag] != "bookmark":
            raise JobException(f"{where} holds one end of a {RANGE_FAMILIES[els[0].tag]} whose other end lies outside "
                               f"it — delete it in Word", "table-anchor", {"where": where})
        kept.update(e for e, i in zip(els, inside) if i)
    if tracked:
        for g in gone:
            for el in g.iter(*PENDING_CHANGES):
                raise JobException(f"{where} already carries tracked changes (w:{etree.QName(el).localname}); accept "
                                   f"or reject them first, or delete it untracked", "existing-revisions",
                                   {"where": where})
        for g in gone:
            if next(g.iter(TEXT_BOX), None) is not None:
                raise JobException(f"{where} holds a text box (Word keeps two copies of a text box); delete it untracked "
                                   f"or in Word", "textbox", {"where": where})
            for p in g.iter(W + "p"):
                odd = [etree.QName(c).localname for c in p if isinstance(c.tag, str) and c.tag not in TRACKABLE_CHILDREN]
                if odd:
                    raise JobException(f"{where}: a paragraph in it holds {sorted(set(odd))}, which a tracked deletion "
                                       f"does not cover (plain runs, bookmarks and proofing marks only) — delete it "
                                       f"untracked or in Word", "structure", {"where": where})
    out = Removal(gone=gone)
    out.kept_marks = [[m for m in g.iter(*BOOKMARKS) if m in kept] for g in gone] if not tracked else [[] for _ in gone]
    out.pictures = sum(1 for g in gone for _ in g.iter(*PICTURES))
    if not tracked:
        out.bookmarks_gone = sorted({b.get(W + "name") or "" for b in whole} - {"", "_GoBack"})
        named = set(out.bookmarks_gone)
        if named:
            instructions = [t.text or "" for t in root.iter(W + "instrText") if not _inside(t, held)]
            instructions += [s.get(W + "instr") or "" for s in root.iter(W + "fldSimple") if not _inside(s, held)]
            out.references = sorted(n for n in named if any(n in text.split() for text in instructions))
    return out


def removal_revisions(rem: Removal) -> Dict[str, int]:
    """Every revision element inside what an untracked removal takes, by V4 kind."""
    out: Dict[str, int] = {}
    for g in rem.gone:
        for el in g.iter():
            k = revision_kind(el)
            if k:
                out[k] = out.get(k, 0) + 1
    return out


def removed_paragraphs(gone: Sequence[Any], index: Dict[Any, int]) -> List[Tuple[int, str]]:
    """(block index, block text) of every paragraph inside ``gone`` (``index``: w:p → its ordinal), in document order."""
    return sorted((index[p], block_full_text(p)) for g in gone for p in g.iter(W + "p"))


def table_removal_problem(container, going: Set[Any]) -> Optional[str]:
    """Why the tables ``going`` (children of ``container``) cannot leave it whole, or None."""
    blocks = [c for c in container if isinstance(c.tag, str) and c.tag in BLOCK_TAGS]
    left = [b for b in blocks if b not in going]
    if not left:
        return "nothing would be left in its cell, section or part"
    if blocks[-1] in going and left[-1].tag != W + "p":
        return "its cell, section or part would end without a paragraph"
    at = {b: i for i, b in enumerate(blocks)}
    for x, y in zip(left, left[1:]):
        if x.tag == W + "tbl" and y.tag == W + "tbl" and at[y] != at[x] + 1:
            return "the tables before and after it would stand side by side and Word would join them"
    return None


def remove(rem: Removal) -> None:
    """Untracked: every deleted element goes, the bookmark ends it keeps taking its place."""
    for el, marks in zip(rem.gone, rem.kept_marks):
        parent = el.getparent()
        at = parent.index(el)
        for m in marks:
            tail = m.tail
            m.getparent().remove(m)
            m.tail = tail
            parent.insert(at, m)
            at += 1
        parent.remove(el)


def deletion_counts(gone: Sequence[Any]) -> Tuple[int, int, int]:
    """(rows, paragraphs, run groups) a tracked deletion of ``gone`` marks."""
    rows = pars = groups = 0
    for g in gone:
        for el in g.iter(W + "tr", W + "p"):
            if el.tag == W + "tr":
                rows += 1
                continue
            pars += 1
            prev = False
            for c in el:
                run = c.tag == W + "r"
                groups += int(run and not prev)
                prev = run
    return rows, pars, groups


def _delete_text(run) -> None:
    for t in run:
        if t.tag in (W + "t", W + "instrText"):
            t.tag = W + ("delText" if t.tag == W + "t" else "delInstrText")
            if t.text and t.text != t.text.strip():
                t.set(XML_SPACE, "preserve")


def mark_deleted(gone: Sequence[Any], stamp: RevisionStamp, next_id: List[int]) -> None:
    """Tracked: the rows (and every row and paragraph in them) marked deleted, as Word records it."""
    for g in gone:
        for el in list(g.iter(W + "tr", W + "p")):
            if el.tag == W + "tr":
                d = etree.Element(W + "del")
                stamp.apply(d, next_id[0]); next_id[0] += 1
                insert_in_order(row_properties(el), d, TRPR_ORDER)
                continue
            mark = etree.Element(W + "del")
            stamp.apply(mark, next_id[0]); next_id[0] += 1
            insert_in_order(mark_rpr(el, create=True), mark, RPR_ORDER)
            wrap = None
            for child in list(el):
                if child.tag != W + "r":
                    wrap = None
                    continue
                if wrap is None:
                    wrap = etree.Element(W + "del")
                    stamp.apply(wrap, next_id[0]); next_id[0] += 1
                    el.insert(el.index(child), wrap)
                el.remove(child)
                _delete_text(child)
                wrap.append(child)


# ---- new rows and paragraphs ----------------------------------------------------------------------------------------

def without(el, names: Sequence[str]):
    """A copy of ``el`` without its children of these local names; None for no element or one left empty."""
    if el is None:
        return None
    out = copy.deepcopy(el)
    drop = {W + n for n in names}
    for c in list(out):
        if isinstance(c.tag, str) and c.tag in drop:
            out.remove(c)
    return out if (len(out) or out.attrib) else None


def cell_ppr(p):
    """The paragraph properties a new cell's paragraph copies from ``p`` (module docstring)."""
    ppr = without(p.find(W + "pPr"), ("cnfStyle", "sectPr", "pPrChange"))
    if ppr is None:
        return None
    rpr = ppr.find(W + "rPr")
    if rpr is not None:
        for c in list(rpr):
            if c.tag in MARK_REVISIONS:
                rpr.remove(c)
        if not len(rpr) and not rpr.attrib:
            ppr.remove(rpr)
    return ppr if (len(ppr) or ppr.attrib) else None


def cell_rpr(p, ppr):
    """The run properties a new cell's text copies: ``p``'s first live text run's, else the cleaned mark's."""
    run = first_live_text_run(p)
    if run is not None:
        return without(run.find(W + "rPr"), ("rPrChange",))
    mark = ppr.find(W + "rPr") if ppr is not None else None
    return copy.deepcopy(mark) if mark is not None else None


def stamped(tag: str, stamp: RevisionStamp, next_id: List[int]):
    el = etree.Element(W + tag)
    stamp.apply(el, next_id[0])
    next_id[0] += 1
    return el


def new_paragraph(ppr, rpr, text: str, tracked: bool, stamp: Optional[RevisionStamp], next_id: List[int]):
    p = etree.Element(W + "p")
    if ppr is not None:
        p.append(copy.deepcopy(ppr))
    if tracked:
        insert_in_order(mark_rpr(p, create=True), stamped("ins", stamp, next_id), RPR_ORDER)
    if text:
        holder = p
        if tracked:
            holder = stamped("ins", stamp, next_id)
            p.append(holder)
        r = etree.SubElement(holder, W + "r")
        if rpr is not None:
            r.append(copy.deepcopy(rpr))
        set_text(etree.SubElement(r, W + "t"), text)
    return p


def new_row(ref, texts: Sequence[str], chain: Sequence[Any], repeat_header: bool, tracked: bool,
            stamp: Optional[RevisionStamp], next_id: List[int]):
    """A new row copying the reference row ``ref`` (module docstring), one text per own cell."""
    tr = etree.Element(W + "tr")
    ex = without(ref.find(W + "tblPrEx"), ("tblPrExChange",))
    if ex is not None:
        tr.append(ex)
    trpr = without(ref.find(W + "trPr"), ("cnfStyle", "ins", "del", "trPrChange", "tblHeader"))
    if trpr is None:
        trpr = etree.Element(W + "trPr")
    head = expected_row_element("tblHeader", repeat_header, chain)
    if head is not None:
        insert_in_order(trpr, head, TRPR_ORDER)
    if tracked:
        insert_in_order(trpr, stamped("ins", stamp, next_id), TRPR_ORDER)
    if len(trpr) or trpr.attrib:
        tr.append(trpr)
    for tc_ref, text in zip(own_cells(ref), texts):
        tc = etree.SubElement(tr, W + "tc")
        tcpr = without(tc_ref.find(W + "tcPr"), ("cnfStyle", "cellIns", "cellDel", "cellMerge", "tcPrChange"))
        if tcpr is not None:
            tc.append(tcpr)
        first = cell_paragraphs(tc_ref)[0]
        ppr = cell_ppr(first)
        tc.append(new_paragraph(ppr, cell_rpr(first, ppr), text, tracked, stamp, next_id))
    return tr


def insertion_counts(texts: Sequence[Sequence[str]]) -> Tuple[int, int, int]:
    """(rows, paragraphs, non-empty cells) of new rows holding ``texts`` (one paragraph per cell)."""
    return len(texts), sum(len(row) for row in texts), sum(1 for row in texts for t in row if t)


# ---- the user's cell texts ------------------------------------------------------------------------------------------

UNSEEN_CATEGORIES = ("Cc", "Cf", "Co", "Cs", "Cn", "Zl", "Zp")


def cell_text(value: Any, where: str, code: str) -> str:
    """A cell's text as it may be placed: text (empty is an empty cell) with no control or invisible character — a
    tab or a line break included (one plain paragraph per cell)."""
    import unicodedata
    if not isinstance(value, str):
        raise JobException(f"{where}: a cell's text must be text, got {value!r}", code)
    bad = sorted({f"U+{ord(c):04X}" for c in value if unicodedata.category(c) in UNSEEN_CATEGORIES})
    if bad:
        raise JobException(f"{where}: the text {value!r} holds a control or invisible character ({', '.join(bad)}); a "
                           f"cell takes one plain paragraph", code)
    return value
