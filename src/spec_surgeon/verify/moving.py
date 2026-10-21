"""V10 kind ``moved_paragraphs`` — move_paragraphs, verifier side (docs/BUILTIN_OPS_BATCH6_DESIGN.md §10), independent of
ops/move_paragraphs.py (rule 1). This module's own reading of the op:

* **The holder** of the paragraphs to move is a body, table cell, header, footer, note or comment that is the parent of
  one of them and holds every one of them; **the stretch** is its children from the first of them it holds directly to
  the last. Every paragraph directly in the stretch is chosen, every chosen paragraph lies in the stretch; the stretch
  holds paragraphs, tables, content controls (untracked, not bound to data) and the marks between blocks — no text box,
  no section break; no bookmark, comment, permission, pending move range or complex field (verify/fields.py's) has one
  end in the stretch and the other outside.
* **The anchor** is the one paragraph of the part ``to`` matches, a child of the holder outside the stretch; **the
  destination** is the place right after it and the range-end marks (bookmark, comment range, permission, pending move
  range) right after it (``after``), or right before it and the range-start marks right before it (``before``). When
  only marks lie between the destination and the stretch, nothing moves. Otherwise the holder, the stretch gone, must
  not end in a table nor have a table follow a table with only marks between them; a body's last section (after its
  last section-break paragraph) must not lose all its blocks to a destination before it; in a comment a thread entry
  names, the comment's last paragraph must stay last (not in the stretch, nothing landing after it); no complex field
  may be open at the destination (a walk in document order: a begin opens one, an end closes one), nor a range the
  stretch is not in already (begun before the destination, ended at or after it).
* **Untracked**, the stretch's nodes are taken out and inserted at the destination, unchanged.
* **Tracked**, the stretch holds only paragraphs, bookmarks and proofing marks, every paragraph only its properties,
  runs of text, tabs, breaks, hyphens and symbols, bookmarks and proofing marks, and no tracked change. Each original
  loses its bookmarks (the stretch's own too) and becomes a paragraph whose mark carries a NEW w:del (first in the mark's
  w:rPr; a w:rPr made for it goes where the schema puts it, a w:pPr made for it first) and whose runs, each unbroken
  sequence of them in a NEW w:del, have their w:t as w:delText. At the destination goes a copy of every node of the
  stretch as it was, each paragraph without w14:paraId/w14:textId, its mark carrying a NEW w:ins and each unbroken
  sequence of its runs (bookmarks and proofing marks break one) in a NEW w:ins.

It BUILDS the part the move must give from the SOURCE — a copy, the stretch taken out and inserted again, or the
originals rebuilt node by node and the copies inserted — and compares it WHOLE with the output part (``verified_parts``),
after checking every NEW revision (an id no source text part holds: a number, used once; the author asked; a Word date;
a w16du:dateUtc exactly when the source's revisions carry one) and masking its id and dates. The plan's diffs and
accepted views for the part (what V2 and V8 hold the output to) and its move record must be what this module finds:
the moved paragraphs' indices are read off the expected part. Any condition under which the op must refuse is an error
here.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from lxml import etree

from .deletion import v_thread_ends
from .edits import V_REVISION_TAGS, part_revision_ids
from .fields import v_fields
from .markup import tree_difference
from .props import Ctx, PartView, element_path, para_matches, resolve_path
from .reader import W

W14 = "{http://schemas.microsoft.com/office/word/2010/wordml}"
DATE_UTC = "{http://schemas.microsoft.com/office/word/2023/wordml/word16du}dateUtc"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
NEW = "#new"

_HOLDERS = {"body", "tc", "hdr", "ftr", "footnote", "endnote", "comment"}
_MARKS = {"bookmarkStart", "bookmarkEnd", "commentRangeStart", "commentRangeEnd", "permStart", "permEnd", "proofErr"}
_NOT_BLOCKS = _MARKS | {"tcPr", "sectPr"}
_ENDS = {"bookmarkStart": "bookmark", "bookmarkEnd": "bookmark", "commentRangeStart": "comment",
         "commentRangeEnd": "comment", "commentReference": "comment", "permStart": "permission", "permEnd": "permission",
         "moveFromRangeStart": "move-from range", "moveFromRangeEnd": "move-from range",
         "moveToRangeStart": "move-to range", "moveToRangeEnd": "move-to range"}
_RANGE_START = {"bookmarkStart", "commentRangeStart", "permStart", "moveFromRangeStart", "moveToRangeStart"}
_RANGE_END = {"bookmarkEnd", "commentRangeEnd", "permEnd", "moveFromRangeEnd", "moveToRangeEnd"}
_BOOKMARK = {"bookmarkStart", "bookmarkEnd"}
_TRACKED_BETWEEN = _BOOKMARK | {"proofErr"}
_TRACKED_IN_PARAGRAPH = {"pPr", "r", "proofErr"} | _BOOKMARK
_TRACKED_IN_RUN = {"rPr", "t", "tab", "br", "cr", "noBreakHyphen", "softHyphen", "sym", "ptab", "lastRenderedPageBreak"}
# CT_PPrBase, in the schema's order: what a w:pPr holds before its paragraph-mark w:rPr
_PPR_BEFORE_RPR = ("pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr", "widowControl", "numPr",
                   "suppressLineNumbers", "pBdr", "shd", "tabs", "suppressAutoHyphens", "kinsoku", "wordWrap",
                   "overflowPunct", "topLinePunct", "autoSpaceDE", "autoSpaceDN", "bidi", "adjustRightInd", "snapToGrid",
                   "spacing", "ind", "contextualSpacing", "mirrorIndents", "suppressOverlap", "jc", "textDirection",
                   "textAlignment", "textboxTightWrap", "outlineLvl", "divId", "cnfStyle")
_WORD_DATE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


class _Refuse(Exception):
    """A condition under which the op must refuse the part."""


def _name(el) -> Optional[str]:
    """The local name of a w: element, else None (other namespaces, comments, processing instructions)."""
    if isinstance(el.tag, str) and el.tag.startswith(W):
        return el.tag[len(W):]
    return None


def _block(el) -> bool:
    return isinstance(el.tag, str) and _name(el) not in _NOT_BLOCKS


def _holds(outer, el) -> bool:
    return any(a is outer for a in el.iterancestors())


def _open_at(root, before, holder) -> int:
    """How many complex fields are open at a place between blocks: right before ``before``, or (None) right after the
    holder's last node — counted in one document-order walk, a begin opening one and an end closing one."""
    last = None if before is not None else list(holder.iter())[-1]
    depth = 0
    for el in root.iter():
        if el is before:
            return depth
        if _name(el) == "fldChar":
            kind = el.get(W + "fldCharType")
            depth += 1 if kind == "begin" else -1 if kind == "end" and depth else 0
        if el is last:
            return depth
    return depth


def _ranges_open(root, before, holder) -> Set[Tuple[str, str]]:
    """The ranges — bookmark, comment, permission, pending move: (family, id), each with both marks, its start first —
    open at a place between blocks: right before ``before``, or (None) right after the holder's last node; begun before
    the place and ended at or after it."""
    last = None if before is not None else list(holder.iter())[-1]
    starts: Dict[Tuple[str, str], int] = {}
    ends: Dict[Tuple[str, str], int] = {}
    place = None
    for k, el in enumerate(root.iter()):
        if el is before:
            place = k
        name = _name(el)
        if name in _RANGE_START or name in _RANGE_END:
            family = name.replace("Range", "").replace("Start", "").replace("End", "")
            (starts if name in _RANGE_START else ends).setdefault((family, el.get(W + "id") or ""), k)
        if el is last:
            place = k + 1
    return {key for key, s in starts.items()
            if key in ends and s < ends[key] and place is not None and s < place <= ends[key]}


def _decide(root, view: PartView, chosen: List[int], to, position: str, tracked: bool, styles,
            ends: Optional[Dict[Any, str]] = None) -> Dict[str, Any]:
    """What the op must do in one part (module docstring), or ``_Refuse``. ``ends``: the last paragraph of each comment
    of the part a thread entry names (``deletion.v_thread_ends``)."""
    pars = view.pars
    picked = [pars[i] for i in chosen]
    picked_ids = {id(p) for p in picked}
    if any(any(_name(a) == "p" for a in p.iterancestors()) for p in picked):
        raise _Refuse("a paragraph to move is inside a text box")
    holder = None
    for p in picked:
        h = p.getparent()
        if _name(h) in _HOLDERS and all(q.getparent() is h or _holds(h, q) for q in picked):
            holder = h
            break
    if holder is None:
        raise _Refuse("the paragraphs to move are not all in one body, cell, header, footer, note or comment that holds "
                      "one of them directly")
    kids = list(holder)
    pos = {id(k): i for i, k in enumerate(kids)}
    direct = [pos[id(p)] for p in picked if p.getparent() is holder]
    lo, hi = min(direct), max(direct)
    for p in picked:
        top = p
        while top.getparent() is not holder:
            top = top.getparent()
        if not lo <= pos[id(top)] <= hi:
            raise _Refuse(f"chosen paragraph {pars.index(p)} lies outside the stretch")
    stretch = kids[lo:hi + 1]
    for k in stretch:
        if _name(k) == "p" and id(k) not in picked_ids:
            raise _Refuse(f"paragraph {pars.index(k)} in the stretch is not chosen (a gap)")
    for k in stretch:
        if not isinstance(k.tag, str):
            continue
        kind = _name(k)
        if kind == "sdt" and (tracked or k.find(f"{W}sdtPr/{W}dataBinding") is not None):
            raise _Refuse("a content control in the stretch" + ("" if tracked else " is bound to data"))
        if kind not in _MARKS and kind not in ("p", "tbl", "sdt"):
            raise _Refuse(f"a {etree.QName(k).localname} in the stretch")
        if any(True for _ in k.iter(W + "txbxContent")):
            raise _Refuse("a text box in the stretch")
        if any(q.find(f"{W}pPr/{W}sectPr") is not None for q in k.iter(W + "p")):
            raise _Refuse("a section break in the stretch")
    if tracked:
        for k in stretch:
            if not isinstance(k.tag, str):
                continue
            if _name(k) not in _TRACKED_BETWEEN and _name(k) != "p":
                raise _Refuse(f"a {etree.QName(k).localname} in a tracked move")
            if _name(k) != "p":
                continue
            if any(isinstance(x.tag, str) and x.tag in V_REVISION_TAGS for x in k.iter()):
                raise _Refuse(f"paragraph {pars.index(k)} holds a tracked change")
            for c in k:
                if isinstance(c.tag, str) and _name(c) not in _TRACKED_IN_PARAGRAPH:
                    raise _Refuse(f"paragraph {pars.index(k)} holds a {etree.QName(c).localname} (tracked move)")
                if _name(c) == "r" and any(not isinstance(g.tag, str) or _name(g) not in _TRACKED_IN_RUN for g in c):
                    raise _Refuse(f"paragraph {pars.index(k)} holds a run the tracked move cannot copy")
    ends_in = [x for k in stretch if isinstance(k.tag, str) for x in k.iter() if _name(x) in _ENDS]
    inside = {id(x) for x in ends_in}                       # the list keeps the proxies alive: their ids hold
    sides: Dict[Tuple[str, str], Set[bool]] = {}
    for x in root.iter():
        if _name(x) in _ENDS:
            sides.setdefault((_ENDS[_name(x)], x.get(W + "id") or ""), set()).add(id(x) in inside)
    cut = sorted(k for k, v in sides.items() if len(v) > 1)
    if cut:
        raise _Refuse(f"the stretch holds one end of a {cut[0][0]} ({cut[0][1]})")
    fields = [f for f in v_fields(root) if f.begin is not None]
    chars_in = [x for k in stretch if isinstance(k.tag, str) for x in k.iter(W + "fldChar")]
    held = {id(x) for x in chars_in}
    if any(len({id(m) in held for m in (f.begin, f.separate, f.end) if m is not None}) > 1 for f in fields):
        raise _Refuse("the stretch holds one end of a complex field")
    moved = [q for k in stretch if isinstance(k.tag, str) for q in k.iter(W + "p")]
    targets = [i for i, p in enumerate(pars) if para_matches(to, p, i, view, styles)]
    if len(targets) != 1:
        raise _Refuse(f"{len(targets)} paragraphs match the destination")
    anchor = pars[targets[0]]
    if any(q is anchor for q in moved):
        raise _Refuse("the destination is one of the paragraphs to move")
    if anchor.getparent() is not holder:
        raise _Refuse("the destination is not in the holder of the paragraphs to move")
    gap_at = pos[id(anchor)]
    if position == "after":                         # past the range ends right after the anchor
        gap_at += 1
        while gap_at < len(kids) and _name(kids[gap_at]) in _RANGE_END:
            gap_at += 1
    else:                                           # before the range starts right before it
        while gap_at and _name(kids[gap_at - 1]) in _RANGE_START:
            gap_at -= 1
    between = kids[gap_at:lo] if gap_at <= lo else kids[hi + 1:gap_at]
    still = not any(_block(k) for k in between)
    if not still:
        left = [k for k in kids[:lo] if _block(k)]
        right = [k for k in kids[hi + 1:] if _block(k)]
        if left and _name(left[-1]) == "tbl" and (not right or _name(right[0]) == "tbl"):
            raise _Refuse("a table would end the holder or touch another table once the stretch leaves")
        if _name(holder) == "body":
            carriers = [i for i, k in enumerate(kids) if _name(k) == "p" and k.find(f"{W}pPr/{W}sectPr") is not None]
            if carriers and lo > carriers[-1] >= gap_at and \
                    not any(_block(k) for k in kids[carriers[-1] + 1:lo] + kids[hi + 1:]):
                raise _Refuse("the stretch is all of the last section and leaves it (an empty last page)")
        if _name(holder) == "comment" and ends:
            last = list(holder.iter(W + "p"))[-1]
            if last in ends:
                top = last
                while top.getparent() is not holder:
                    top = top.getparent()
                if any(q is last for q in moved) or gap_at > pos[id(top)]:
                    raise _Refuse(f"the move changes the last paragraph of comment {ends[last]}, which its thread "
                                  f"entry names")
        if fields and _open_at(root, kids[gap_at] if gap_at < len(kids) else None, holder):
            raise _Refuse("the destination lies inside a complex field")
        inside = _ranges_open(root, stretch[0], holder)
        landing = _ranges_open(root, kids[gap_at] if gap_at < len(kids) else None, holder)
        if landing - inside:
            raise _Refuse(f"the destination lies inside a {sorted(landing - inside)[0][0]} the stretch is not in")
    return {"holder": element_path(holder), "lo": lo, "hi": hi, "gap_at": gap_at, "still": still,
            "first": pars.index(moved[0]), "moved": moved, "anchor": targets[0],
            "tables": sum(1 for k in stretch if _name(k) == "tbl")}


def _revision(kind: str, author: Optional[str]):
    el = etree.Element(W + kind)
    el.set(W + "id", NEW)
    el.set(W + "author", author or "")
    el.set(W + "date", NEW)
    return el


def _marked(ppr, kind: str, author: Optional[str]):
    """A paragraph's w:pPr with a NEW ``kind`` mark first in its w:rPr."""
    if ppr is None:
        out = etree.Element(W + "pPr")
        rpr = etree.SubElement(out, W + "rPr")
    else:
        out = copy.deepcopy(ppr)
        rpr = out.find(W + "rPr")
        if rpr is None:
            rpr = etree.Element(W + "rPr")
            out.insert(next((i for i, c in enumerate(out) if _name(c) not in _PPR_BEFORE_RPR), len(out)), rpr)
    rpr.insert(0, _revision(kind, author))
    return out


def _rebuilt(p, kind: str, author: Optional[str]):
    """The original (``del``) or the copy (``ins``) of a paragraph in a tracked move, built node by node."""
    attrib = {k: v for k, v in p.attrib.items() if kind == "del" or k not in (W14 + "paraId", W14 + "textId")}
    new = etree.Element(p.tag, attrib=attrib, nsmap=p.nsmap)
    new.text = p.text
    new.append(_marked(p.find(W + "pPr"), kind, author))
    wrapper = None
    for c in p:
        n = _name(c)
        if n == "pPr" or (kind == "del" and n in _BOOKMARK):
            continue
        if n != "r":
            wrapper = None
            new.append(copy.deepcopy(c))
            continue
        if wrapper is None:
            wrapper = _revision(kind, author)
            new.append(wrapper)
        run = copy.deepcopy(c)
        if kind == "del":
            for t in run:
                if _name(t) == "t":
                    t.tag = W + "delText"
                    if t.text and t.text != t.text.strip():
                        t.set(XML_SPACE, "preserve")
        wrapper.append(run)
    return new


def _expected(src_root, d: Dict[str, Any], position: str, tracked: bool, author: Optional[str]):
    """(the part the move must give, the output paragraphs that are the moved ones / their copies, in order)."""
    exp = etree.fromstring(etree.tostring(src_root))
    holder = resolve_path(exp, d["holder"])
    kids = list(holder)
    stretch = kids[d["lo"]:d["hi"] + 1]
    beside = kids[d["gap_at"]] if d["gap_at"] < len(kids) else None      # what the stretch goes right before
    if d["still"]:
        return exp, []
    if tracked:
        going = [_rebuilt(k, "ins", author) if _name(k) == "p" else copy.deepcopy(k) for k in stretch]
        for k in stretch:
            if _name(k) in _BOOKMARK:
                holder.remove(k)
            elif _name(k) == "p":
                holder.replace(k, _rebuilt(k, "del", author))
    else:
        going = stretch
        for k in going:
            holder.remove(k)
    at = holder.index(beside) if beside is not None else len(holder)
    for j, k in enumerate(going):
        holder.insert(at + j, k)
    return exp, [q for k in going if isinstance(k.tag, str) for q in k.iter(W + "p")]


def _masked(out_root, old: Set[str], author: Optional[str], utc: bool, where: str, errs: List[str]):
    """A copy of the output part with every NEW w:ins/w:del checked and its id and dates masked."""
    out = etree.fromstring(etree.tostring(out_root))
    seen: Set[str] = set()
    for el in out.iter(W + "ins", W + "del"):
        rid = el.get(W + "id")
        if rid is not None and rid in old:
            continue
        wrong = []
        if rid is None or not rid.isdigit():
            wrong.append(f"id {rid!r}")
        elif rid in seen:
            wrong.append(f"id {rid} used twice")
        seen.add(rid or "")
        if el.get(W + "author") != author:
            wrong.append(f"author {el.get(W + 'author')!r}")
        if not _WORD_DATE.fullmatch(el.get(W + "date") or ""):
            wrong.append(f"date {el.get(W + 'date')!r}")
        stamp = el.get(DATE_UTC)
        if (stamp is not None) != utc or (stamp is not None and not _WORD_DATE.fullmatch(stamp)):
            wrong.append(f"dateUtc {stamp!r} (the source's revisions {'carry' if utc else 'carry no'} one)")
        if wrong:
            errs.append(f"{where}: a new w:{etree.QName(el).localname} has {', '.join(wrong)}")
        for key in (W + "id", W + "date", DATE_UTC):
            el.attrib.pop(key, None)
        el.set(W + "id", NEW)
        el.set(W + "date", NEW)
    return out


def _moved_paragraphs(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    pred = (a.get("select") or {}).get("paragraphs")
    to = a.get("to")
    position = a.get("position")
    tracked = a.get("tracked", True) is not False
    author = a.get("author")
    if position not in ("before", "after") or not isinstance(to, dict):
        errs.append(f"moved_paragraphs: destination {to!r} / position {position!r} unreadable")
        return "moved_paragraphs: unreadable"
    old = set().union(*(part_revision_ids(ctx.src.xml(name)) for name in ctx.src_tp))
    utc = any(el.get(DATE_UTC) is not None for name in ctx.src_tp
              for el in ctx.src.xml(name).iter(W + "ins", W + "del"))
    found: Dict[str, Dict[str, Any]] = {}
    total = 0
    for part in ctx.scope_parts(a.get("scope") or ["document"]):
        if part not in ctx.both:
            errs.append(f"moved_paragraphs: {part} missing from the output")
            continue
        src_root = ctx.src.xml(part)
        view = PartView(src_root)
        chosen = [i for i, p in enumerate(view.pars) if para_matches(pred, p, i, view, ctx.src_styles)]
        if not chosen:
            continue
        where = f"moved_paragraphs {part}"
        try:
            d = _decide(src_root, view, chosen, to, position, tracked, ctx.src_styles, v_thread_ends(ctx.src, src_root))
        except _Refuse as why:
            errs.append(f"{where}: {why} — the op must refuse")
            continue
        n = len(d["moved"])
        total += n
        found[part] = {"first": d["first"], "paragraphs": n, "tables": d["tables"], "anchor": d["anchor"],
                       "noop": d["still"]}
        exp, landed = _expected(src_root, d, position, tracked, author)
        got = _masked(ctx.out.xml(part), old, author, utc, where, errs) if tracked else ctx.out.xml(part)
        diff = tree_difference(exp, got)
        if diff:
            errs.append(f"{where}: the output is not the source with exactly this move "
                        f"({'tracked' if tracked else 'untracked'}): {diff}")
        ctx.verified_parts.add(part)
        # what V2 and V8 hold the output to: the plan's own diffs and accepted views for this part
        texts = [view.texts[d["first"] + k] for k in range(n)] if landed else []
        exp_pars = list(exp.iter(W + "p"))                  # kept alive while their ids are read
        out_index = {id(q): i for i, q in enumerate(exp_pars)}
        ends = [out_index[id(q)] for q in landed]
        if tracked:
            want = [(o, None, t, t) for o, t in zip(ends, texts)]
            want_acc = [(d["first"] + k, "") for k in range(len(texts))]
        else:
            want = ([(d["first"] + k, t, None, None) for k, t in enumerate(texts)]
                    + [(o, None, t, None) for o, t in zip(ends, texts)])
            want_acc = []
        have = [(x.get("block_index"), x.get("before"), x.get("after"), x.get("accepted_view"))
                for x in ctx.plan.get("diffs", []) if x.get("part") == part]
        have_acc = [(x.get("block_index"), x.get("accepted_view")) for x in ctx.plan.get("accepted_views", [])
                    if x.get("part") == part]
        if sorted(map(repr, have)) != sorted(map(repr, want)):
            errs.append(f"{where}: the plan's diffs for the part {sorted(have, key=repr)[:4]} are not the move's "
                        f"{sorted(want, key=repr)[:4]}")
        if sorted(map(repr, have_acc)) != sorted(map(repr, want_acc)):
            errs.append(f"{where}: the plan's accepted views for the part are not the move's (every original empty)")
    if found != (a.get("moves") or {}):
        errs.append(f"moved_paragraphs: moves found here {found}, the plan predicted {a.get('moves')}")
    if total != a.get("expected_paragraphs", -1):
        errs.append(f"moved_paragraphs: {total} paragraph(s) to move here, plan predicted {a.get('expected_paragraphs')}")
    return (f"moved_paragraphs: {total} paragraph(s) {position} their destination "
            f"({'tracked' if tracked else 'untracked'}) in {len(found)} part(s), each compared whole")


MOVE_KINDS = {"moved_paragraphs": _moved_paragraphs}
