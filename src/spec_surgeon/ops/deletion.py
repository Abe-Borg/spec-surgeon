"""Deleting what a paragraph selection covers — transform side (docs/BUILTIN_OPS_BATCH6_DESIGN.md §0.5, §0.6, §1, §2):
``delete_paragraphs`` and ``remove_footnotes`` decide here. The verifier derives every decision again with its own code
(verify/deletion.py): keep the two identical.

**Chosen.** In each part the op works in, the selected paragraphs (never one of a footnote or endnote separator). A
``w:tbl`` every ``w:p`` of which is selected (it has one) is *wholly selected*; the outermost wholly selected tables are
deleted whole. A selected paragraph in a text box, or holding one, is refused (``textbox``).

**Containers and kept paragraphs.** A container is the parent of a selected paragraph outside the deleted tables, or of
a deleted table (a body, a cell, a header, a footer, a note, a comment, a block content control); its blocks are its
``w:p``, ``w:tbl``, ``w:sdt``, ``w:customXml`` and ``w:altChunk`` children, the chosen ones its selected paragraphs and
deleted tables. Kept, in this order: (1) between a table that stays and the next block that stays, when that is a
table too and chosen blocks lie between them, the first chosen paragraph there (none: ``tables-join``); (2) tracked,
the last block when it is a chosen paragraph (Word cannot delete a container's final paragraph mark); (3) when the last
block is chosen and what stays would be nothing or would end in something other than a paragraph, the last chosen
paragraph not kept yet (none, or what stays still ends in something else: ``structure``); (4) in the body, the *last
section* — the blocks after the last paragraph that carries a section break (``w:pPr/w:sectPr``) and stays — keeps a
paragraph: when every one of its blocks is chosen and none of them is kept yet, its last chosen paragraph (none:
``structure``); flagged either way (the section shows a blank page). A kept paragraph is emptied and flagged; every
other chosen block is deleted.

**Comment threads.** A deleted paragraph that is the last paragraph of a comment some entry of the comments' side
parts names (a ``w15:commentEx`` by its ``w15:paraId`` or ``w15:paraIdParent``, a ``w16cid:commentId`` by its
``w16cid:paraId``, one of the comment's paragraph ids) is refused (``comment-last-paragraph``) unless the comment
itself goes (untracked: all of its marks are removed): Word names a thread by its comment's last paragraph.

**Tracked** (the default). The *runs* of a paragraph — its ``w:r`` directly in it or inside a hyperlink, smart tag,
run-level content control, custom XML element, ``w:dir`` or ``w:bdo`` — are deleted, each group of consecutive sibling
runs in one new ``w:del``, their ``w:t`` becoming ``w:delText`` (edge spaces preserved) and ``w:instrText``
``w:delInstrText``; every paragraph of a deleted table and every other deleted paragraph gets a new ``w:del`` on its mark,
a kept one keeps its mark; each row of a deleted table, nested tables' rows too, gets a new ``w:trPr/w:del`` (the
``w:trPr`` created right after the row's ``w:tblPrEx``, first otherwise). Nothing else changes: comments, notes,
bookmarks and section breaks stay until the deletion is accepted (flagged). Refused: a pending revision in what is
deleted (``existing-revisions``), a simple field (``field-result``), paragraph content other than runs, those
containers, bookmarks, permission ranges, proofing marks and comment range marks (``structure``), a row of a deleted
table that is not its table's own child (``table-structure``), a complex field some of whose runs (begin to end, in
document order) are deleted and some not, or one that never ends and begins in them (``field-crossing``).

**Untracked.** The deleted paragraphs and tables leave the tree; a kept paragraph keeps its ``w:pPr`` alone. What they
held decides the rest (``Removal``), in this order:

1. *notes* — a footnote (endnote) all of whose references in the text parts other than the notes parts are removed
   goes: its entry leaves the first footnotes (endnotes) part;
2. *section breaks* — a removed paragraph's ``w:sectPr`` goes with it and the content before it joins the next section
   (flagged); a header, footer or printer-settings relationship a removed ``w:sectPr`` names, none of whose uses
   remains, goes, and the part it targets, when no relationship left targets it, leaves the package with its own
   relationships (images and hyperlinks only, else ``structure``) and its content;
3. *comments* — a comment all of whose marks (range start, range end, reference; in every text part but the comments
   part) are removed goes with every reply below it, its entry and its side-part entries as ``remove_comments`` removes
   them; a comment some of whose marks are removed and some not, or a reply going with its parent whose marks are not
   all removed, is refused (``comment-crossing``);
4. *range marks* — a bookmark (permission, move or custom-XML range: marks paired by kind and ``w:id``) all of whose
   marks are removed goes; one with only some of them removed keeps the others, and each removed one takes the place of
   the outermost removed element it was in, in document order — that element being a note's or a comment's entry:
   ``structure``. A REF, PAGEREF or NOTEREF field, or a hyperlink, naming a bookmark that goes is flagged;
5. *fields* — a complex field some of whose runs are removed and some not, or one that never ends and begins in what is
   removed: ``field-crossing``;
6. *relationships* — in a changed part, a relationship none of whose uses (``r:`` attributes) remains: an image (any
   media) or hyperlink one leaves the part's relationships, one step 2 decides as there, any other (a chart, a
   diagram, an embedded object, a control) is refused (``embedded-object``); media no relationship left targets leaves
   the package.

Every revision in what goes is dropped (flagged).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from lxml import etree

from ..ooxml import (RPR_ORDER, TRPR_ORDER, RevisionStamp, block_full_text, block_struct_counts, block_text_without,
                     count_revisions, insert_in_order, mark_rpr, paragraphs, to_del_text)
from ..package import CT_PART, FOOTER_REL, HEADER_REL, MEDIA_REL_TYPES, NS, R, W, DocxPackage
from .base import BlockDiff, JobException, PlanFragment
from .comments import EXTENDED_REL, IDS_REL, MARK_TAGS, W14, W15, W16CID, CommentBook, side_removals
from .fields import fields_of
from .hf_common import HYPERLINK_REL, check_part_relationships, refs
from .set_table_props import row_properties

BLOCK_TAGS = (W + "p", W + "tbl", W + "sdt", W + "customXml", W + "altChunk")
SEPARATOR_TYPES = ("separator", "continuationSeparator", "continuationNotice")
NOTE_KINDS = ("footnotes", "endnotes")
NOTE_ENTRY = {"footnotes": W + "footnote", "endnotes": W + "endnote"}
NOTE_REFERENCE = {"footnotes": W + "footnoteReference", "endnotes": W + "endnoteReference"}
ENTRY_TAGS = (W + "footnote", W + "endnote", W + "comment")
# tracked: runs inside these are deleted where they stand; the second set are those containers' own properties
RUN_CONTAINERS = (W + "hyperlink", W + "smartTag", W + "customXml", W + "dir", W + "bdo", W + "sdt", W + "sdtContent")
CONTAINER_PROPERTIES = (W + "smartTagPr", W + "customXmlPr", W + "sdtPr", W + "sdtEndPr")
INERT_MARKS = (W + "bookmarkStart", W + "bookmarkEnd", W + "proofErr", W + "commentRangeStart", W + "commentRangeEnd",
               W + "permStart", W + "permEnd")
RANGE_FAMILY = {W + "bookmarkStart": "bookmark", W + "bookmarkEnd": "bookmark",
                W + "permStart": "permission", W + "permEnd": "permission",
                W + "moveFromRangeStart": "moveFrom", W + "moveFromRangeEnd": "moveFrom",
                W + "moveToRangeStart": "moveTo", W + "moveToRangeEnd": "moveTo",
                W + "customXmlInsRangeStart": "customXmlIns", W + "customXmlInsRangeEnd": "customXmlIns",
                W + "customXmlDelRangeStart": "customXmlDel", W + "customXmlDelRangeEnd": "customXmlDel",
                W + "customXmlMoveFromRangeStart": "customXmlMoveFrom",
                W + "customXmlMoveFromRangeEnd": "customXmlMoveFrom",
                W + "customXmlMoveToRangeStart": "customXmlMoveTo", W + "customXmlMoveToRangeEnd": "customXmlMoveTo"}
REMOVABLE_RELS = set(MEDIA_REL_TYPES) | {HYPERLINK_REL}
HF_RELS = (HEADER_REL, FOOTER_REL)
BOOKMARK_FIELDS = ("REF", "PAGEREF", "NOTEREF")
CONTAINER_WORDS = {W + "body": "the document body", W + "tc": "a table cell", W + "hdr": "a header",
                   W + "ftr": "a footer", W + "footnote": "a footnote", W + "endnote": "an endnote",
                   W + "comment": "a comment", W + "sdtContent": "a content control", W + "customXml": "a custom XML block"}
# a removed sectPr's references to other parts: its headers and footers, its printer settings (Word on Windows writes one)
SECTION_PART_REFS = (W + "headerReference", W + "footerReference", W + "printerSettings")
# what a deleted paragraph may show besides text, counted for the flag: each outermost one once
PICTURE_TAGS = (W + "drawing", W + "pict", W + "object", "{%s}AlternateContent" % NS["mc"])


def nearest_p(el) -> Optional[Any]:
    q = el.getparent()
    while q is not None and q.tag != W + "p":
        q = q.getparent()
    return q


def in_separator(p) -> bool:
    """The paragraph belongs to a footnote or endnote separator (never deleted)."""
    for a in p.iterancestors(W + "footnote", W + "endnote"):
        return (a.get(W + "type") or "normal") in SEPARATOR_TYPES
    return False


def shown(p, limit: int = 60) -> str:
    text = " ".join(block_full_text(p).split())
    return f"«{text[:limit] + '…' if len(text) > limit else text}»" if text else "an empty paragraph"


def blocks_of(container) -> List[Any]:
    return [c for c in container if c.tag in BLOCK_TAGS]


# ---- which blocks go, which paragraphs stay ----------------------------------------------------------------------

@dataclass
class Choice:
    """One part's decisions: the deleted tables (outermost), the selected paragraphs deleted outside them, the
    selected paragraphs kept (emptied) and the paragraphs inside the deleted tables."""
    part: str
    root: Any
    selected: List[Any] = field(default_factory=list)
    tables: List[Any] = field(default_factory=list)
    deleted: List[Any] = field(default_factory=list)
    kept: List[Any] = field(default_factory=list)
    in_tables: List[Any] = field(default_factory=list)
    last_section: List[Any] = field(default_factory=list)     # the kept paragraphs of an emptied last section


def keep_rules(blocks: Sequence[Any], chosen: Set[Any], tracked: bool, where: str) -> List[Any]:
    """The chosen paragraphs of one container that stay (the module docstring's rules 1–3)."""
    keep: List[Any] = []
    n = len(blocks)
    for i, b in enumerate(blocks):
        if b.tag != W + "tbl" or b in chosen:
            continue
        j = i + 1
        while j < n and blocks[j] in chosen:
            j += 1
        if i + 1 < j < n and blocks[j].tag == W + "tbl":
            first = next((x for x in blocks[i + 1:j] if x.tag == W + "p"), None)
            if first is None:
                raise JobException(f"{where}: the deletion would leave two tables with nothing between them (Word would "
                                   f"join them into one) and no paragraph between them can stay — keep a paragraph "
                                   f"between them, or delete one of the tables too", "tables-join", {"where": where})
            if first not in keep:
                keep.append(first)
    if n and blocks[-1] in chosen:
        if tracked and blocks[-1].tag == W + "p" and blocks[-1] not in keep:
            keep.append(blocks[-1])
        rest = [b for b in blocks if b not in chosen or b in keep]
        if not rest or rest[-1].tag != W + "p":
            last = next((x for x in reversed(blocks) if x in chosen and x.tag == W + "p" and x not in keep), None)
            if last is not None:
                keep.append(last)
                rest = [b for b in blocks if b not in chosen or b in keep]
            if last is None or rest[-1].tag != W + "p":
                raise JobException(f"{where}: the deletion would leave it without a final paragraph and none of the "
                                   f"selected paragraphs there can stay", "structure", {"where": where})
    return keep


def last_section_keep(blocks: Sequence[Any], chosen: Set[Any], keep: List[Any], where: str) -> List[Any]:
    """The body's last section keeps a paragraph (the module docstring's rule 4): when every block after the last
    staying paragraph with a section break is chosen, its kept paragraphs — its last chosen paragraph added to ``keep``
    when none is kept yet. [] when the last section keeps something of its own, or no section break stays."""
    stays = [b for b in blocks if b not in chosen or b in keep]
    breaks = [b for b in stays if b.tag == W + "p" and b.find(f"{W}pPr/{W}sectPr") is not None]
    if not breaks:
        return []
    tail = list(blocks)[list(blocks).index(breaks[-1]) + 1:]
    if not tail or any(b not in chosen for b in tail):
        return []
    held = [b for b in tail if b in keep]
    if held:
        return held
    last = next((b for b in reversed(tail) if b.tag == W + "p"), None)
    if last is None:
        raise JobException(f"{where}: the deletion would empty the last section and none of its blocks is a paragraph "
                           f"that can stay", "structure", {"where": where})
    keep.append(last)
    return [last]


def held_objects(pars: Iterable[Any]) -> Tuple[int, int]:
    """(pictures, embedded objects) in ``pars``: each outermost drawing, VML picture or Office choice of them
    (``PICTURE_TAGS``) once, an embedded object when it is or holds a w:object."""
    pictures = objects = 0
    for p in pars:
        for el in p.iter(*PICTURE_TAGS):
            q = el.getparent()
            while q is not None and q is not p and q.tag not in PICTURE_TAGS:
                q = q.getparent()
            if q is not None and q is not p:
                continue                                       # inside another one: counted with it
            if el.tag == W + "object" or next(el.iter(W + "object"), None) is not None:
                objects += 1
            else:
                pictures += 1
    return pictures, objects


def thread_ends(pkg: DocxPackage, root) -> Dict[Any, str]:
    """{the last paragraph of each comment of ``root`` (a tree of the comments part) that an entry of the comments' side
    parts names: its comment id} — the module docstring's comment threads."""
    rels = pkg.rels(pkg.main_part())
    named: Set[str] = set()
    for rel_type, tag, attrs in ((EXTENDED_REL, W15 + "commentEx", (W15 + "paraId", W15 + "paraIdParent")),
                                 (IDS_REL, W16CID + "commentId", (W16CID + "paraId",))):
        member = next((r.resolved for r in rels.values() if r.type == rel_type and r.resolved and pkg.has(r.resolved)),
                      None)
        if member is not None:
            for e in pkg.xml(member).iter(tag):
                named.update(e.get(a) for a in attrs if e.get(a))
    out: Dict[Any, str] = {}
    for c in root.findall(W + "comment"):
        pars = paragraphs(c)
        if pars and any(p.get(W14 + "paraId") in named for p in pars):
            out[pars[-1]] = c.get(W + "id")
    return out


def refuse_thread_ends(pkg: DocxPackage, part: str, root, deleted: Iterable[Any], comments_gone: Iterable[str],
                       what: str = "delete") -> None:
    """``comment-last-paragraph`` for a paragraph of ``deleted`` (of ``root``, a comments part) that ends a comment the
    side parts name, unless that comment is one of ``comments_gone``."""
    if root.tag != W + "comments":
        return
    ends = thread_ends(pkg, root)
    gone = set(comments_gone)
    for p in deleted:
        cid = ends.get(p)
        if cid is not None and cid not in gone:
            raise JobException(f"{part}: the paragraph to {what} ({shown(p)}) is the last paragraph of comment {cid}, "
                               f"which the comment's thread entry names (its replies and resolved state point to it); "
                               f"take the whole comment, leave its last paragraph where it is, or edit the comment in "
                               f"Word", "comment-last-paragraph", {"part": part, "comment": cid})


def choose(part: str, root, selected: Sequence[Any], tracked: bool) -> Choice:
    """The module docstring's chosen blocks and kept paragraphs for ``selected`` (paragraphs of ``root``)."""
    ch = Choice(part=part, root=root, selected=list(selected))
    pars = paragraphs(root)
    index = {p: i for i, p in enumerate(pars)}
    for p in selected:
        if nearest_p(p) is not None or any(q is not p for q in p.iter(W + "p")):
            raise JobException(f"{part} paragraph {index[p]} ({shown(p)}) is in, or holds, a text box (Word keeps two "
                               f"copies of a text box); delete it in Word", "textbox",
                               {"part": part, "paragraph": index[p]})
    sel = set(selected)
    whole = [t for t in root.iter(W + "tbl") if any(True for _ in t.iter(W + "p")) and all(q in sel for q in t.iter(W + "p"))]
    ws = set(whole)
    ch.tables = [t for t in whole if not any(a in ws for a in t.iterancestors(W + "tbl"))]
    inside = {q for t in ch.tables for q in t.iter(W + "p")}
    ch.in_tables = [p for p in pars if p in inside]
    containers: List[Any] = []
    for el in [p for p in selected if p not in inside] + ch.tables:
        c = el.getparent()
        if c is not None and c not in containers:
            containers.append(c)
    chosen = set(ch.tables) | {p for p in selected if p not in inside}
    kept: Set[Any] = set()
    for c in containers:
        where = f"{part}: {CONTAINER_WORDS.get(c.tag, 'a ' + etree.QName(c).localname)}"
        blocks = blocks_of(c)
        keep = list(keep_rules(blocks, chosen, tracked, where))
        if c.tag == W + "body":
            ch.last_section = last_section_keep(blocks, chosen, keep, where)
        kept.update(keep)
    ch.kept = [p for p in pars if p in kept]
    ch.deleted = [p for p in pars if p in chosen and p not in kept]
    return ch


# ---- tracked deletion --------------------------------------------------------------------------------------------

def tracked_runs(p, where: str) -> List[Any]:
    """The runs a tracked deletion of ``p`` deletes, in document order (refusals: the module docstring)."""
    out: List[Any] = []

    def walk(el) -> None:
        for c in el:
            if not isinstance(c.tag, str) or (el is p and c.tag == W + "pPr"):
                continue
            if c.tag == W + "r":
                out.append(c)
            elif c.tag in INERT_MARKS or (el is not p and c.tag in CONTAINER_PROPERTIES):
                continue
            elif c.tag in RUN_CONTAINERS:
                walk(c)
            elif c.tag == W + "fldSimple":
                raise JobException(f"{where} holds a simple field ({(c.get(W + 'instr') or '').split()[:1]}); a simple "
                                   f"field cannot be deleted as a tracked change — delete untracked, or in Word",
                                   "field-result", {"where": where})
            else:
                raise JobException(f"{where} holds {etree.QName(c).localname} (an equation, a revision or other "
                                   f"content a tracked deletion is not written over); delete untracked, or in Word",
                                   "structure", {"where": where})
    walk(p)
    return out


def run_groups(runs: Sequence[Any]) -> List[List[Any]]:
    """The runs grouped as one ``w:del`` each: consecutive sibling runs (no node between them) of the chosen ones."""
    chosen = set(runs)
    done: Set[Any] = set()
    groups: List[List[Any]] = []
    for r in runs:
        if r in done:
            continue
        group = [r]
        done.add(r)
        nxt = r.getnext()
        while nxt is not None and nxt in chosen:
            group.append(nxt)
            done.add(nxt)
            nxt = nxt.getnext()
        groups.append(group)
    return groups


def refuse_crossing_fields(part: str, root, gone, what: str) -> None:
    """``field-crossing`` for a complex field of ``root`` some of whose runs are ``gone`` (a predicate) and some not."""
    runs = list(root.iter(W + "r"))
    at = {r: k for k, r in enumerate(runs)}
    for f in fields_of(root):
        if f.simple is not None or f.begin is None:
            continue
        b = at.get(f.begin.getparent())
        e = at.get(f.end.getparent()) if f.end is not None else None
        if b is None:
            continue
        span = runs[b:e + 1] if e is not None and e >= b else None
        cut = (gone(runs[b]) if span is None else len({bool(gone(r)) for r in span}) > 1)
        if cut:
            raise JobException(f"{part}: the paragraphs to {what} hold part of a field ({f.type or 'a field'}) that goes "
                               f"on outside them (a table of contents, an index, a field over several paragraphs); "
                               f"select every paragraph the field covers, or none of them", "field-crossing",
                               {"part": part, "field": f.type})


def delete_runs(runs: Sequence[Any], stamp: RevisionStamp, next_id: List[int]) -> None:
    """Each group of ``runs`` (``run_groups``) into a new ``w:del``; text and field code become deleted text."""
    for group in run_groups(runs):
        d = etree.Element(W + "del")
        stamp.apply(d, next_id[0])
        next_id[0] += 1
        group[0].addprevious(d)
        for r in group:
            to_del_text(r)
            for it in r.findall(W + "instrText"):
                it.tag = W + "delInstrText"
            d.append(r)


def delete_mark(p, stamp: RevisionStamp, next_id: List[int]) -> None:
    mark = etree.Element(W + "del")
    stamp.apply(mark, next_id[0])
    next_id[0] += 1
    insert_in_order(mark_rpr(p, create=True), mark, RPR_ORDER)


def delete_row(tr, stamp: RevisionStamp, next_id: List[int]) -> None:
    d = etree.Element(W + "del")
    stamp.apply(d, next_id[0])
    next_id[0] += 1
    insert_in_order(row_properties(tr), d, TRPR_ORDER)


def table_rows(root, tables: Sequence[Any], part: str) -> List[Tuple[int, int]]:
    """(table ordinal in ``root.iter(w:tbl)``, row index among that table's own rows) of every row in ``tables``,
    nested tables' rows included, in document order; a row that is not its table's own child: ``table-structure``."""
    ordinal = {t: k for k, t in enumerate(root.iter(W + "tbl"))}
    out: List[Tuple[int, int]] = []
    for t in tables:
        for tr in t.iter(W + "tr"):
            owner = tr.getparent()
            if owner is None or owner.tag != W + "tbl":
                raise JobException(f"{part}: a row of a table to delete is wrapped in a content control or custom XML; "
                                   f"its deletion cannot be tracked row by row — delete untracked, or in Word",
                                   "table-structure", {"part": part})
            out.append((ordinal[owner], [c for c in owner if c.tag == W + "tr"].index(tr)))
    return out


def row_at(root, k: int, r: int):
    t = list(root.iter(W + "tbl"))[k]
    return [c for c in t if c.tag == W + "tr"][r]


# ---- untracked removal and what goes with it -----------------------------------------------------------------------

def rel_uses(els: Iterable[Any]) -> Dict[str, int]:
    """r:-attribute values (relationship ids) used in the subtrees of ``els``, with their counts."""
    out: Dict[str, int] = {}
    for el in els:
        for x in el.iter():
            if isinstance(x.tag, str):
                for k, v in x.attrib.items():
                    if k.startswith(R):
                        out[v] = out.get(v, 0) + 1
    return out


class Removal:
    """An untracked removal over one package: the elements that leave each part (``remove``), then everything they take
    with them (``resolve``: the module docstring's steps 1–6). Every tree is read once and kept, so the decisions
    name elements of those trees; ``apply_to`` performs them on the package the trees were read from."""

    def __init__(self, pkg: DocxPackage):
        self.pkg = pkg
        self.main = pkg.main_part()
        self.kinds = pkg.parts_by_kind()
        self.roots: Dict[str, Any] = {}
        self._tops: Dict[str, Set[Any]] = {}          # part → removed elements (none inside another)
        self.tops: Dict[str, List[Any]] = {}          # the same in document order (``resolve``)
        self.emptied: Dict[str, List[Any]] = {}       # part → kept paragraphs whose content is removed
        self.notes: Dict[str, List[str]] = {}         # notes part → ids of the notes that go
        self.sections: List[int] = []                 # sectPr ordinals that go
        self.parts_gone: List[str] = []               # header/footer parts that leave the package
        self.printers: List[str] = []                 # printer settings parts that leave the package
        self.comments: List[str] = []                 # comment ids that go
        self.side: Dict[str, Tuple[Any, List[Any]]] = {}
        self.moved: Dict[str, Dict[Any, List[Any]]] = {}   # part → {removed element: marks taking its place}
        self.bookmarks_gone: List[str] = []
        self.rels: Dict[str, List[str]] = {}          # owner part → relationship ids that go
        self.media: List[str] = []
        self.members: List[str] = []
        self.flags: List[str] = []

    def root(self, part: str):
        if part not in self.roots:
            self.roots[part] = self.pkg.xml(part)
        return self.roots[part]

    def remove(self, part: str, el) -> None:
        if self.gone(part, el):
            return
        tops = self._tops.setdefault(part, set())
        tops.difference_update([d for d in el.iter() if d in tops])
        tops.add(el)

    def gone(self, part: str, el) -> bool:
        """``el`` leaves: its part leaves the package, or it is (in) a removed element."""
        if part in self.parts_gone:
            return True
        tops = self._tops.get(part)
        return bool(tops) and (el in tops or any(a in tops for a in el.iterancestors()))

    def text_parts(self) -> List[str]:
        return self.pkg.text_parts()

    def notes_part(self, kind: str) -> Optional[str]:
        found = self.kinds.get(kind) or []
        return found[0] if found else None

    # ---- the cascade ----

    def resolve(self) -> None:
        self._notes()
        self._sections()
        self._comments()
        for part in list(self._tops):
            self._range_marks(part)
            refuse_crossing_fields(part, self.root(part), lambda r, part=part: self.gone(part, r), "delete")
        self._relationships()
        self.tops = {part: [el for el in self.root(part).iter() if el in tops]
                     for part, tops in self._tops.items() if tops}

    def _notes(self) -> None:
        notes_parts = [p for p in (self.notes_part(k) for k in NOTE_KINDS) if p]
        for kind in NOTE_KINDS:
            part = self.notes_part(kind)
            if part is None:
                continue
            refs_by_id: Dict[str, List[bool]] = {}
            for tp in self.text_parts():
                if tp in notes_parts:
                    continue
                for ref in self.root(tp).iter(NOTE_REFERENCE[kind]):
                    refs_by_id.setdefault(ref.get(W + "id"), []).append(self.gone(tp, ref))
            seen: Set[str] = set()
            for e in self.root(part).findall(NOTE_ENTRY[kind]):
                nid = e.get(W + "id")
                gone = refs_by_id.get(nid)
                if not gone or not all(gone):
                    continue
                if nid in seen:
                    raise JobException(f"{part}: two notes share the id {nid}; the reference to delete cannot be told "
                                       f"apart", "structure", {"part": part, "id": nid})
                seen.add(nid)
                self.notes.setdefault(part, []).append(nid)
                self.remove(part, e)

    def _sections(self) -> None:
        if not self._tops.get(self.main):
            return
        root = self.root(self.main)
        secs = DocxPackage.sect_prs(root)
        removed = [i for i, sp in enumerate(secs) if self.gone(self.main, sp)]
        self.sections = removed
        if not removed:
            return
        rels = self.pkg.rels(self.main)
        uses_before = rel_uses([root])
        uses_gone = rel_uses(self._tops[self.main])
        named = {el.get(R + "id"): el.tag for i in removed for el in secs[i]
                 if el.tag in SECTION_PART_REFS and el.get(R + "id")}
        gone_rids = sorted(rid for rid in named if rid in rels and uses_gone.get(rid, 0) == uses_before.get(rid, 0))
        if gone_rids:
            self.rels.setdefault(self.main, []).extend(gone_rids)
        targeted = {r.resolved for owner, r in self.pkg.relationships(source=True)
                    if r.resolved and not (owner == self.main and r.rid in gone_rids)}
        for rid in gone_rids:
            target = rels[rid].resolved
            if target and self.pkg.has(target) and target not in targeted and target not in self.parts_gone \
                    and target not in self.printers:
                check_part_relationships(self.pkg, target)
                # a printer settings part is a binary member, not a text part: it leaves, nothing in it is read
                (self.printers if named[rid] == W + "printerSettings" else self.parts_gone).append(target)

    def _comments(self) -> None:
        try:
            book = CommentBook(self.pkg)
        except JobException:                           # comments that cannot be told apart: refused only when one goes
            cpart = next(iter(self.kinds.get("comments") or []), None)
            if any(self.gone(tp, m) for tp in self.text_parts() if tp != cpart for m in self.root(tp).iter(*MARK_TAGS)):
                raise
            return
        if book.part is None or not book.comments:
            return
        ids = {c.id for c in book.comments}
        marks: Dict[str, List[bool]] = {}
        for tp in self.text_parts():
            if tp == book.part:
                continue
            for m in self.root(tp).iter(*MARK_TAGS):
                if m.get(W + "id") in ids:
                    marks.setdefault(m.get(W + "id"), []).append(self.gone(tp, m))
        crossing = sorted(cid for cid, g in marks.items() if any(g) and not all(g))
        chosen = {cid for cid, g in marks.items() if g and all(g)}
        grew = True
        while grew:
            grew = False
            for c in book.comments:
                up = book.parent(c)
                if c.id not in chosen and up is not None and up.id in chosen:
                    chosen.add(c.id)
                    grew = True
                    if marks.get(c.id) and not all(marks[c.id]):
                        crossing.append(c.id)
        if crossing:
            raise JobException(f"comment {crossing[0]}'s marked text runs past the edge of the deletion (or it replies "
                               f"to a comment that goes, from outside it); remove the comment first, or select all of "
                               f"its text", "comment-crossing", {"comments": sorted(set(crossing))})
        if not chosen:
            return
        self.comments = [c.id for c in book.comments if c.id in chosen]
        croot = self.root(book.part)
        by_id = {c.get(W + "id"): c for c in croot.findall(W + "comment")}
        for cid in self.comments:
            self.remove(book.part, by_id[cid])
        self.side = side_removals(self.pkg, book, self.comments)

    def _range_marks(self, part: str) -> None:
        if part in self.parts_gone:
            return
        root = self.root(part)
        pairs: Dict[Tuple[str, str], List[Any]] = {}
        for el in root.iter(*RANGE_FAMILY):
            pairs.setdefault((RANGE_FAMILY[el.tag], el.get(W + "id") or ""), []).append(el)
        tops = self._tops.get(part, set())
        moved: Dict[Any, List[Any]] = {}
        for (family, _), els in pairs.items():
            gone = [self.gone(part, e) for e in els]
            if all(gone):
                if family == "bookmark":
                    self.bookmarks_gone += [e.get(W + "name") for e in els if e.get(W + "name")]
                continue
            for e, g in zip(els, gone):
                if not g:
                    continue
                top = next(a for a in [e, *e.iterancestors()] if a in tops)
                if top.tag in ENTRY_TAGS:
                    raise JobException(f"{part}: a {family} runs from a note or comment that goes into text that stays; "
                                       f"remove it in Word", "structure", {"part": part})
                moved.setdefault(top, []).append(e)
        if moved:
            order = {el: k for k, el in enumerate(root.iter())}
            self.moved[part] = {t: sorted(ms, key=lambda m: order[m]) for t, ms in moved.items()}

    def _relationships(self) -> None:
        changed = [p for p, tops in self._tops.items() if tops and p not in self.parts_gone]
        for part in changed:
            root = self.root(part)
            before, gone = rel_uses([root]), rel_uses(self._tops[part])
            rels = self.pkg.rels(part)
            for rid in sorted(gone):
                if gone[rid] != before.get(rid) or rid not in rels or rid in self.rels.get(part, []):
                    continue
                rel = rels[rid]
                if rel.type in HF_RELS and part == self.main:
                    continue                                   # step 2 decided it
                if rel.type not in REMOVABLE_RELS:
                    raise JobException(f"{part}: the content to delete holds a chart, diagram, embedded object or "
                                       f"control ({rel.type.rsplit('/', 1)[-1]}), which lives in its own part of the "
                                       f"file; delete it in Word", "embedded-object",
                                       {"part": part, "relationship": rid, "type": rel.type})
                self.rels.setdefault(part, []).append(rid)
        removed = {(owner, rid) for owner, rids in self.rels.items() for rid in rids}
        candidates = []
        for owner, r in self.pkg.relationships(source=True):
            if r.type in MEDIA_REL_TYPES and r.resolved and ((owner, r.rid) in removed or owner in self.parts_gone):
                candidates.append(r.resolved)
        remaining = {r.resolved for owner, r in self.pkg.relationships(source=True)
                     if r.resolved and owner not in self.parts_gone and (owner, r.rid) not in removed}
        self.media = sorted({m for m in candidates if self.pkg.has(m) and m not in remaining})
        parts = self.parts_gone + self.printers
        members = parts + [self.pkg.rels_path_for(p) for p in parts if self.pkg.has(self.pkg.rels_path_for(p))] + self.media
        order = {n: i for i, n in enumerate(self.pkg.member_names)}
        self.members = sorted(set(members), key=lambda n: order[n])

    # ---- predictions ----

    def predict(self, frag: PlanFragment, note: str) -> None:
        """Every prediction of the removal (V1 members, V2 blocks, V3 structure, V4 revisions), its touches and flags."""
        dropped: Dict[str, int] = {}
        for part in list(self.tops) + [p for p in self.parts_gone if p not in self.tops]:
            root = self.root(part)
            pars = paragraphs(root)
            partly: Dict[Any, List[Any]] = {}          # a paragraph that stays → what leaves it
            if part not in self.parts_gone:
                for top in self.tops.get(part, ()):
                    p = nearest_p(top)
                    if p is not None and not self.gone(part, p):
                        partly.setdefault(p, []).append(top)
            emptied = set(self.emptied.get(part, ()))
            for bi, p in enumerate(pars):
                if self.gone(part, p):
                    frag.diffs.append(BlockDiff(part=part, block_index=bi, before=block_full_text(p), after=None,
                                                note="part removed" if part in self.parts_gone else note))
                elif p in partly:
                    before, after = block_full_text(p), block_text_without(p, partly[p])
                    s_before, s_after = block_struct_counts(p), block_struct_counts(p, partly[p])
                    if before != after or s_before != s_after:
                        frag.diffs.append(BlockDiff(part=part, block_index=bi, before=before, after=after,
                                                    struct_after=s_after if s_before != s_after else None,
                                                    note="kept paragraph emptied" if p in emptied else note))
            for el in ([root] if part in self.parts_gone else self.tops.get(part, ())):
                for k, v in count_revisions(el).items():
                    dropped[k] = dropped.get(k, 0) + v
            if part in self.parts_gone:
                continue
            if part not in frag.intended_parts:
                frag.intended_parts.append(part)
            frag.touches.append((part, "*", "text"))
        for k, v in dropped.items():
            frag.rev_delta[k] = frag.rev_delta.get(k, 0) - v
        if dropped:
            frag.flags.append(f"pending tracked changes {dropped} lay in what is deleted; they go with it")
        for member, _ in self.side.items():
            frag.intended_parts.append(member)
            frag.touches.append((member, "*", "remove"))
        for owner in self.rels:
            rp = self.pkg.rels_path_for(owner)
            if rp not in frag.intended_parts:
                frag.intended_parts.append(rp)
            frag.touches.append((rp, "*", "rels"))
        frag.members_removed = list(self.members)
        frag.touches += [(m, "*", "remove") for m in self.members]
        _, overrides = self.pkg.content_types()
        if any(m in overrides for m in self.members):
            frag.intended_parts.append(CT_PART)
            frag.touches.append((CT_PART, "*", "ct"))
        frag.flags += self.flags

    def summary(self) -> Dict[str, Any]:
        """What the removal takes besides the op's own blocks — the verifier derives it again and compares."""
        return {"notes": {p: list(v) for p, v in self.notes.items()}, "sections": list(self.sections),
                "parts": list(self.parts_gone), "comments": list(self.comments),
                "rels": {p: sorted(v) for p, v in self.rels.items()}, "media": list(self.media),
                "members": list(self.members)}

    # ---- the transform ----

    def apply_to(self) -> None:
        """Perform the removal on ``self.pkg`` (the package the trees were read from)."""
        for part, tops in self.tops.items():
            if part in self.parts_gone:
                continue
            moved = self.moved.get(part, {})
            for top in tops:
                parent = top.getparent()
                at = parent.index(top)
                for k, m in enumerate(moved.get(top, ())):
                    m.tail = None
                    parent.insert(at + k, m)
                parent.remove(top)
            self.pkg.write_xml(part, self.root(part))
        for member, (root, entries) in self.side.items():
            for e in entries:
                root.remove(e)
            self.pkg.write_xml(member, root)
        for owner, rids in self.rels.items():
            if owner != self.main:
                self.pkg.edit_rels(owner, remove=list(rids))
        from .hf_common import apply_removal
        apply_removal(self.pkg, self.main, self.rels.get(self.main, []), self.members, self.media)


def remove_deletion(removal: Removal, ch: Choice) -> None:
    """``ch``'s blocks into ``removal``: the deleted tables and paragraphs, and each kept paragraph's content."""
    for el in ch.tables + ch.deleted:
        removal.remove(ch.part, el)
    for p in ch.kept:
        removal.emptied.setdefault(ch.part, []).append(p)
        for c in p:
            if c.tag != W + "pPr":
                removal.remove(ch.part, c)


# ---- flags the reader needs ---------------------------------------------------------------------------------------

def section_flags(pkg: DocxPackage, root, removed: Sequence[int], tracked: bool) -> List[str]:
    """What removing (tracked: deleting) the section breaks ``removed`` (sectPr ordinals) does to the pages."""
    if not removed:
        return []
    secs = DocxPackage.sect_prs(root)
    out = []
    for i in removed:
        nxt = next((j for j in range(i + 1, len(secs)) if j not in removed), len(secs) - 1)
        head = ("once the deletion is accepted, " if tracked else "") + f"the section break at the end of section {i + 1}"
        out.append(f"{head} goes: the pages of section {i + 1} join section {nxt + 1} and take its page size, margins, "
                   f"columns, page numbering, headers and footers (what it inherits included)")
    if not tracked:
        rels = pkg.rels(pkg.main_part())
        part_of = lambda rid: rels[rid].resolved if rid in rels else None

        def shown_refs(sections):
            res, prev = [], {}
            for sp in sections:
                cur = dict(prev)
                for kind, typ, rid, _ in refs(sp):
                    cur[(kind, typ)] = part_of(rid)
                res.append(cur)
                prev = cur
            return res
        before = shown_refs(secs)
        kept = [i for i in range(len(secs)) if i not in removed]
        after = shown_refs([secs[i] for i in kept])
        for k, i in enumerate(kept):
            for key in sorted(set(before[i]) | set(after[k])):
                if before[i].get(key) != after[k].get(key):
                    out.append(f"section {i + 1} inherited its {key[1]} {key[0]} from a section that goes: it now shows "
                               f"{after[k].get(key) or 'none'} instead of {before[i].get(key)}")
    return out


def dangling_flags(pkg: DocxPackage, removal: Removal) -> List[str]:
    """A REF / PAGEREF / NOTEREF field or a hyperlink that stays and names a bookmark that goes."""
    names = set(removal.bookmarks_gone)
    if not names:
        return []
    out = []
    for part in pkg.text_parts():
        if part in removal.parts_gone:
            continue
        root = removal.root(part)
        for f in fields_of(root):
            words = f.instruction.split()
            if not words or (f.begin is not None and removal.gone(part, f.begin)) or \
                    (f.simple is not None and removal.gone(part, f.simple)):
                continue
            target = words[1] if words[0].upper() in BOOKMARK_FIELDS and len(words) > 1 else \
                words[0] if words[0] in names else None
            if target in names:
                out.append(f"{part}: a {words[0].upper()} field still points to the bookmark {target!r}, which goes "
                           f"with the deleted text (it will show an error when Word updates fields)")
        for h in root.iter(W + "hyperlink"):
            if h.get(W + "anchor") in names and not removal.gone(part, h):
                out.append(f"{part}: a link still points to the bookmark {h.get(W + 'anchor')!r}, which goes with the "
                           f"deleted text")
    return out


def tracked_flags(pkg: DocxPackage, part: str, runs: Iterable[Any]) -> List[str]:
    """Tracked: the comments and notes whose marks lie in the deleted runs stay until the deletion is accepted."""
    runs = list(runs)
    out = []
    comments = sorted({m.get(W + "id") for r in runs for m in r.iter(W + "commentReference")})
    if comments:
        out.append(f"{part}: comment(s) {', '.join(comments)} stay, anchored to deleted text, until the deletion is "
                   f"accepted or rejected in Word")
    notes = sum(1 for r in runs for _ in r.iter(W + "footnoteReference", W + "endnoteReference"))
    if notes:
        out.append(f"{part}: {notes} footnote/endnote reference(s) are deleted; Word removes their notes when the "
                   f"deletion is accepted")
    return out


NOTE_TEXT_JOIN = "\n"


def note_text(entry) -> str:
    """A note's own text: its paragraphs' block texts, one per line."""
    return NOTE_TEXT_JOIN.join(block_full_text(p) for p in entry.iter(W + "p"))


def regex(pattern: Any, name: str):
    if not isinstance(pattern, str):
        raise JobException(f"{name} must be a regular expression", "bad-params")
    try:
        return re.compile(pattern)
    except re.error as e:
        raise JobException(f"bad {name}: {e}", "bad-params")
