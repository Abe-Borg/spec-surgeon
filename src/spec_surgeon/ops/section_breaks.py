"""What insert_section_break and remove_section_break share (transform side; the verifier's own reading is
verify/sections.py) — docs/BUILTIN_OPS_BATCH6_DESIGN.md §8.

A section's sectPr holds two kinds of thing:

* its START — ``w:type`` (how the section begins after the one before it; absent: on a new page) and the page-number
  restart ``w:pgNumType/@w:start``: what happens where the section begins;
* its SETUP — everything else (page size, margins, columns, line numbers, vertical alignment, page borders, note
  settings, the header/footer references, the different first page): what applies to every page of it.

Inserting breaks splits a section into parts that all keep its setup (two sections may show the same header part); the
START stays with the first part, and every later part starts with the asked break and no restart. Removing the break at
the end of a run of sections joins them to the section after it: the joined section keeps the following section's
setup (Word's behaviour), takes the START of the first joined section (where it now begins), and each header/footer
reference the following section inherited from a joined one moves into it — so the pages after the removed break show
what they showed. Removing a break inserted here gives the source back.

A body paragraph's *previous block* is its nearest preceding sibling that is not a range mark (bookmarks, comment ranges,
permissions, proofing marks, move and custom-XML range marks): what Word lays out before it.
"""
from __future__ import annotations

import copy
from typing import Any, Optional, Tuple

from lxml import etree

from ..ooxml import SECTPR_ORDER, insert_in_order
from ..package import W
from .set_page_setup import START_TYPES, START_WORDS

RANGE_MARKS = frozenset(W + t for t in (
    "bookmarkStart", "bookmarkEnd", "commentRangeStart", "commentRangeEnd", "permStart", "permEnd", "proofErr",
    "moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd", "customXmlInsRangeStart",
    "customXmlInsRangeEnd", "customXmlDelRangeStart", "customXmlDelRangeEnd", "customXmlMoveFromRangeStart",
    "customXmlMoveFromRangeEnd", "customXmlMoveToRangeStart", "customXmlMoveToRangeEnd"))
MARK_REVISIONS = (W + "ins", W + "del", W + "moveFrom", W + "moveTo")
BLOCK_NAMES = {W + "tbl": "a table", W + "sdt": "a content control", W + "customXml": "a custom XML block",
               W + "altChunk": "an imported document"}


def previous_block(el: etree._Element) -> Optional[etree._Element]:
    """The nearest preceding sibling of ``el`` that is not a range mark (or a comment), else None."""
    q = el.getprevious()
    while q is not None and (not isinstance(q.tag, str) or q.tag in RANGE_MARKS):
        q = q.getprevious()
    return q


def block_name(el: etree._Element) -> str:
    """``a table``, ``a content control`` … — a body block as the refusal names it."""
    return BLOCK_NAMES.get(el.tag, f"a {etree.QName(el).localname} element")


def mark_revised(p: etree._Element) -> bool:
    """The paragraph's mark is a pending tracked insertion, deletion or move."""
    rpr = p.find(f"{W}pPr/{W}rPr")
    return rpr is not None and any(rpr.find(t) is not None for t in MARK_REVISIONS)


def holder(sp: etree._Element) -> Optional[etree._Element]:
    """The paragraph whose properties hold ``sp`` (None for the body's last sectPr)."""
    ppr = sp.getparent()
    return ppr.getparent() if ppr is not None and ppr.tag == W + "pPr" else None


def section_start(sp: etree._Element) -> Tuple[Optional[etree._Element], Optional[str]]:
    """The section's START: (its w:type element or None, its page-number restart or None)."""
    pg = sp.find(W + "pgNumType")
    return sp.find(W + "type"), (pg.get(W + "start") if pg is not None else None)


def set_start(sp: etree._Element, type_el: Optional[etree._Element], restart: Optional[str]) -> None:
    """``sp``'s START made (``type_el``, ``restart``): its w:type replaced where it stands by a copy of ``type_el`` (one
    inserted in schema order where it has none; none: removed); ``w:pgNumType/@w:start`` written (the element inserted
    in schema order where missing) or removed (a w:pgNumType left with nothing goes)."""
    old = sp.find(W + "type")
    new = None
    if type_el is not None:
        new = copy.deepcopy(type_el)
        new.tail = None
    if old is not None and new is not None:
        sp.replace(old, new)
    elif old is not None:
        sp.remove(old)
    elif new is not None:
        insert_in_order(sp, new, SECTPR_ORDER)
    pg = sp.find(W + "pgNumType")
    if restart is not None:
        if pg is None:
            pg = insert_in_order(sp, etree.Element(W + "pgNumType"), SECTPR_ORDER)
        pg.set(W + "start", restart)
    elif pg is not None and pg.get(W + "start") is not None:
        del pg.attrib[W + "start"]
        if not pg.attrib and not len(pg):
            sp.remove(pg)


def break_type(asked: str) -> Optional[etree._Element]:
    """The w:type a break of the asked kind writes (none for a next-page break: the schema's default)."""
    val = START_TYPES[asked]
    if val == "nextPage":
        return None
    el = etree.Element(W + "type")
    el.set(W + "val", val)
    return el


def start_words(sp: Any) -> str:
    """How the section starts, in words: ``a new page``, ``the same page (continuous)`` …"""
    t = sp.find(W + "type")
    val = t.get(W + "val") if t is not None else "nextPage"
    by_val = {v: k for k, v in START_TYPES.items()}
    return START_WORDS[by_val[val]] if val in by_val else f"a {val} break"
