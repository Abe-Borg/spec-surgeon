"""V2/V3 — per-block text and structural-children extraction, plus accept/reject views (used by V5b/V8).

Block = w:p. A block's text is the concatenation, in document order, of every text-bearing element
whose nearest w:p ancestor is that paragraph: w:t, w:delText (tracked-deleted text counts),
w:instrText, w:delInstrText, m:t. Structural children are counted separately because they render
as characters but never appear in w:t.
"""
from __future__ import annotations

import copy
import difflib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from lxml import etree

from .reader import W, M

TEXT_TAGS = (W + "t", W + "delText", W + "instrText", W + "delInstrText", M + "t")
STRUCT_TAGS = ("tab", "br", "cr", "noBreakHyphen", "softHyphen", "sym", "ptab",
               "footnoteReference", "endnoteReference", "commentReference", "drawing", "fldChar")


@dataclass
class Block:
    index: int
    text: str
    struct: Dict[str, int] = field(default_factory=dict)
    mark_deleted: bool = False     # paragraph mark tracked-deleted (pPr/rPr/del)
    mark_inserted: bool = False    # paragraph mark tracked-inserted (pPr/rPr/ins)


def _nearest_p(el: etree._Element) -> Optional[etree._Element]:
    q = el.getparent()
    while q is not None:
        if q.tag == W + "p":
            return q
        q = q.getparent()
    return None


def blocks(root: etree._Element) -> List[Block]:
    pars = list(root.iter(W + "p"))
    index = {id(p): i for i, p in enumerate(pars)}
    texts: List[List[str]] = [[] for _ in pars]
    structs: List[Dict[str, int]] = [{} for _ in pars]
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if el.tag in TEXT_TAGS:
            p = _nearest_p(el)
            if p is not None:
                texts[index[id(p)]].append(el.text or "")
        elif el.tag.startswith(W):
            local = el.tag[len(W):]
            if local in STRUCT_TAGS:
                if local == "tab" and (el.getparent() is None or el.getparent().tag != W + "r"):
                    continue  # pPr/w:tabs tab-stop definitions share the element name
                p = _nearest_p(el)
                if p is not None:
                    d = structs[index[id(p)]]
                    d[local] = d.get(local, 0) + 1
    out = []
    for i, p in enumerate(pars):
        ppr = p.find(W + "pPr")
        rpr = ppr.find(W + "rPr") if ppr is not None else None
        out.append(Block(index=i, text="".join(texts[i]), struct=structs[i],
                         mark_deleted=rpr is not None and rpr.find(W + "del") is not None,
                         mark_inserted=rpr is not None and rpr.find(W + "ins") is not None))
    return out


@dataclass
class BlockChange:
    before_index: Optional[int]
    after_index: Optional[int]
    before: Optional[str]
    after: Optional[str]
    struct_before: Dict[str, int]
    struct_after: Dict[str, int]


def diff_blocks(before: List[Block], after: List[Block]) -> List[BlockChange]:
    """Align the two block lists on text and report every difference (text or structural counts)."""
    a = [b.text for b in before]
    b = [c.text for c in after]
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    changes: List[BlockChange] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                x, y = before[i1 + k], after[j1 + k]
                if x.struct != y.struct:
                    changes.append(BlockChange(x.index, y.index, x.text, y.text, x.struct, y.struct))
        elif op == "replace" and (i2 - i1) == (j2 - j1):
            for k in range(i2 - i1):
                x, y = before[i1 + k], after[j1 + k]
                changes.append(BlockChange(x.index, y.index, x.text, y.text, x.struct, y.struct))
        else:
            for x in before[i1:i2]:
                changes.append(BlockChange(x.index, None, x.text, None, x.struct, {}))
            for y in after[j1:j2]:
                changes.append(BlockChange(None, y.index, None, y.text, {}, y.struct))
    return changes


# ---- accept / reject views ----------------------------------------------------------------------

def _unwrap(el: etree._Element) -> None:
    parent = el.getparent()
    idx = parent.index(el)
    for child in list(el):
        parent.insert(idx, child)
        idx += 1
    parent.remove(el)


def accept_view(root: etree._Element) -> etree._Element:
    r = copy.deepcopy(root)
    for tag in (W + "del", W + "moveFrom"):
        for el in list(r.iter(tag)):
            if el.getparent() is not None and el.getparent().tag != W + "rPr":
                el.getparent().remove(el)
    for tag in (W + "ins", W + "moveTo"):
        for el in list(r.iter(tag)):
            if el.getparent() is not None and el.getparent().tag != W + "rPr":
                _unwrap(el)
    return r


def reject_view(root: etree._Element) -> etree._Element:
    r = copy.deepcopy(root)
    for tag in (W + "ins", W + "moveTo"):
        for el in list(r.iter(tag)):
            if el.getparent() is not None and el.getparent().tag != W + "rPr":
                el.getparent().remove(el)
    for tag in (W + "del", W + "moveFrom"):
        for el in list(r.iter(tag)):
            if el.getparent() is not None and el.getparent().tag != W + "rPr":
                for dt in el.iter(W + "delText"):
                    dt.tag = W + "t"
                for dt in el.iter(W + "delInstrText"):
                    dt.tag = W + "instrText"
                _unwrap(el)
    return r


def view_texts(view_root: etree._Element, which: str) -> List[str]:
    """Block texts of an accept/reject view, with paragraph-mark revisions applied: a block whose mark
    is deleted (accept) / inserted (reject) merges into the following block."""
    bl = blocks(view_root)
    out: List[str] = []
    carry = ""
    for b in bl:
        merged = (b.mark_deleted if which == "accept" else b.mark_inserted)
        if merged:
            carry += b.text
            continue
        out.append(carry + b.text)
        carry = ""
    if carry:
        out.append(carry)
    return out
