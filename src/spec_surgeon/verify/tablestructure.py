"""V10 kinds ``deleted_tables``, ``deleted_table_rows``, ``inserted_table_rows`` and ``inserted_tables`` — the table
structure ops of built-in batch 6 (F5, docs/BUILTIN_OPS_BATCH6_DESIGN.md §6), verified.

This module's own reading of the ops' definitions (transform side: ops/table_edit.py, ops/delete_tables.py,
ops/delete_table_rows.py, ops/insert_table_rows.py, ops/insert_table.py). Nothing here comes from ops/ (rule 1). Each
kind re-derives from the SOURCE, with its own code, the selection (``tableselect.VTables``, ``tablerows``, the paragraph
selector ``props.para_matches``), every refusal (in the op's order), the table file's rows (``tables.table_grid``), the
widths and the style, then BUILDS the expected part — the source with exactly that deletion or insertion done — and
compares it whole with the output (``ctx.verified_parts``: the residual leaves the part to it).

* **What a deletion may take** (``_v_take``): no paragraph with a section break; no comment mark; untracked, no footnote
  or endnote mark; no complex field nor permission, move or custom-XML range with one part in and one out; tracked, no
  pending change (insertion, deletion, move, cell change), only properties, runs, bookmarks and proofing marks in a
  paragraph, plain text in a run (no ``w:t`` below another run child) and no text box. A bookmark end whose partner lies
  outside everything deleted in the part is kept: untracked it takes the deleted element's place.
* **Untracked**: the elements go, kept bookmark ends in their place. A table going whole may not leave its container
  (its ``w:p``/``w:tbl``/``w:sdt``/``w:customXml``/``w:altChunk`` children) empty, ending in something other than a
  paragraph where a removed table was last, or with two tables side by side that were not.
* **Tracked**: in document order, every ``w:tr`` gets a NEW ``w:del`` in its ``w:trPr`` (created after ``w:tblPrEx``,
  first otherwise), every ``w:p`` a NEW ``w:del`` in its mark's ``w:rPr``, and each run of consecutive ``w:r`` children
  a NEW ``w:del`` around it, ``w:t``/``w:instrText`` renamed ``w:delText``/``w:delInstrText`` (``xml:space="preserve"``
  added to edge whitespace). A NEW marker matches an output element of that tag by the plan's author, with a Word date
  and a ``w:id`` no revision of the source has and no other new one (``_v_settle``); every element is in the schema
  order of the bundled wml.xsd (``tableprops.v_order``).
* **A new row** copies its reference row: ``w:tblPrEx`` less ``w:tblPrExChange``; ``w:trPr`` less ``w:cnfStyle``,
  ``w:ins``, ``w:del``, ``w:trPrChange``, ``w:tblHeader``, then ``<w:tblHeader/>`` (repeat_header) or ``w:val="0"`` where
  any style level's ``w:trPr`` turns it on, then the NEW ``w:ins``; per cell ``w:tcPr`` less ``w:cnfStyle``,
  ``w:cellIns``, ``w:cellDel``, ``w:cellMerge``, ``w:tcPrChange``, and one paragraph: the cell's first paragraph's
  ``w:pPr`` less ``w:cnfStyle``, ``w:sectPr``, ``w:pPrChange`` and its mark's revision marks and ``w:rPrChange``; the run's
  ``w:rPr`` that paragraph's first live text run's less ``w:rPrChange`` (none when it has none) or, with no such run,
  the cleaned mark's. No attributes; an emptied element is left out.
* **A new table**: ``w:tblStyle``; ``w:tblW`` pct 5000 (or dxa, the widths' sum); without a style the six sides single
  4/0/auto; ``w:tblLook`` (header rows: firstRow 1, val 0420; else 0 and 0400; lastRow, firstColumn, lastColumn, noHBand
  0, noVBand 1); the grid (the widths, or the section's ``pgSz@w`` less ``pgMar@left`` and ``@right`` cut into n
  columns ⌊W(k+1)/n⌋ − ⌊Wk/n⌋); rows with ``<w:tblHeader/>`` for the header rows and the NEW ``w:ins``; cells with
  ``w:tcW`` dxa and a bare paragraph. Placement: not in a text box or a table, not beside a table, not after the body's
  last paragraph, not after a section break.
* **A new paragraph**: tracked a NEW ``w:ins`` in its mark; a text in one run (in a NEW ``w:ins``), its ``w:t`` with
  ``xml:space="preserve"`` when the text has edge whitespace, a no-break space or two spaces in a row.

Fails when a refusal applies (the op must refuse), when the tables, rows or anchors that change or their counts differ
from the plan's, or when an output part is not exactly the expected one.
"""
from __future__ import annotations

import copy
import re
import unicodedata
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from lxml import etree

from .reader import W
from .edits import part_revision_ids
from .fields import v_fields
from .markup import tree_difference
from .props import Ctx, PartView, para_matches
from .restyle import v_resolve_style
from .styleimport import v_type
from .tablerows import (v_cell_paragraphs, v_check_rows, v_cuts, v_merged, v_rows_chosen)
from .tables import table_grid, table_md5
from .tableprops import v_insert, v_order
from .tableselect import (VTables, v_cells, v_cells_wrapped, v_check, v_in_text_box, v_rows, v_rows_wrapped,
                          v_style_chain)

_V_NEW = "(new)"                                # an id and date no output element can carry
_V_WORD_DATE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_V_DATE_UTC = "{http://schemas.microsoft.com/office/word/2023/wordml/word16du}dateUtc"
_V_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_V_COMMENTS = {W + t for t in ("commentRangeStart", "commentRangeEnd", "commentReference")}
_V_NOTES = {W + "footnoteReference", W + "endnoteReference"}
_V_PAIRED = {W + t: t.replace("Start", "").replace("End", "")
             for t in ("bookmarkStart", "bookmarkEnd", "permStart", "permEnd", "moveFromRangeStart", "moveFromRangeEnd",
                       "moveToRangeStart", "moveToRangeEnd", "customXmlInsRangeStart", "customXmlInsRangeEnd",
                       "customXmlDelRangeStart", "customXmlDelRangeEnd", "customXmlMoveFromRangeStart",
                       "customXmlMoveFromRangeEnd", "customXmlMoveToRangeStart", "customXmlMoveToRangeEnd")}
_V_PENDING = {W + t for t in ("ins", "del", "moveFrom", "moveTo", "cellIns", "cellDel", "cellMerge")}
_V_PLAIN = {W + t for t in ("pPr", "r", "bookmarkStart", "bookmarkEnd", "proofErr")}
_V_BLOCKS = {W + t for t in ("p", "tbl", "sdt", "customXml", "altChunk")}
_V_MARK_GONE = {W + t for t in ("ins", "del", "moveFrom", "moveTo", "rPrChange")}
_V_HIDDEN = {W + "del", W + "moveFrom"}
_V_UNSEEN = {"Cc", "Cf", "Co", "Cs", "Cn", "Zl", "Zp"}
_V_SIDES = ("top", "left", "bottom", "right", "insideH", "insideV")


# ---- shared readings --------------------------------------------------------------------------------------------------

def _v_tracking(a: Dict[str, Any]) -> Tuple[bool, str]:
    tracked = a.get("tracked")
    if not isinstance(tracked, bool) or not isinstance(a.get("author"), str):
        raise ValueError("the assertion names no tracking mode or author")
    return tracked, a["author"]


def _v_select(sel: Any, keys: Sequence[str]) -> Tuple[List[str], Any, Any]:
    """(scope kinds, tables predicate, rows predicate) — ValueError for a key the op must refuse."""
    if not isinstance(sel, dict) or set(sel) - {"scope", *keys}:
        raise ValueError(f"unknown selector key(s) in {sel!r} (the op must refuse)")
    scope = sel.get("scope", ["document"])
    pred = sel.get("tables", "all")
    v_check(pred)
    rows = sel.get("rows")
    v_check_rows(rows)
    return ([scope] if isinstance(scope, str) else list(scope)), pred, rows


def _v_every(pred: Any) -> bool:
    return pred in (None, "all") or pred == {}


def _v_old_ids(ctx: Ctx) -> Set[str]:
    return set().union(*(part_revision_ids(ctx.src.xml(p)) for p in ctx.src_tp))


def _v_marker(tag: str, author: str):
    return etree.Element(W + tag, {W + "id": _V_NEW, W + "author": author, W + "date": _V_NEW})


def _v_settle(expected, output, old: Set[str], taken: Set[str], where: str, errs: List[str]) -> None:
    """Pair the expected NEW markers with the output's (a parallel walk while tags agree) and take over their id and date
    when they are as they must be; a marker that is not stays as it is, so the whole comparison fails there too."""
    def walk(e, o):
        if not isinstance(e.tag, str) or e.tag != o.tag:
            return
        if e.get(W + "id") == _V_NEW:
            got = dict(o.attrib)
            rid = got.get(W + "id") or ""
            wrong = [why for why, bad in (
                ("its author", got.get(W + "author") != e.get(W + "author")),
                ("its date", not _V_WORD_DATE.fullmatch(got.get(W + "date") or "")),
                ("its id (not new or used twice)", not rid.isdigit() or rid in old or rid in taken),
                ("its attributes", bool(set(got) - {W + "id", W + "author", W + "date", _V_DATE_UTC}))) if bad]
            if wrong:
                errs.append(f"{where}: a new w:{etree.QName(e).localname} differs in " + ", ".join(wrong)
                            + f" ({ {etree.QName(k).localname: v for k, v in got.items()} })")
            else:
                taken.add(rid)
                for k in (W + "id", W + "date", _V_DATE_UTC):
                    if k in got:
                        e.set(k, got[k])
        for ce, co in zip(e, o):
            walk(ce, co)
    walk(expected, output)


def _v_compare(ctx: Ctx, part: str, expected, old: Set[str], taken: Set[str], what: str, errs: List[str]) -> None:
    output = ctx.out.xml(part)
    _v_settle(expected, output, old, taken, f"{part}", errs)
    diff = tree_difference(expected, output)
    if diff:
        errs.append(f"{part} is not the source with {what}: {diff}")
    ctx.verified_parts.add(part)                     # compared whole: the residual leaves it out


def _v_within(el, held: Set[Any]) -> bool:
    q = el
    while q is not None:
        if q in held:
            return True
        q = q.getparent()
    return False


def _v_text(text: Any, what: str) -> str:
    if not isinstance(text, str) or any(unicodedata.category(c) in _V_UNSEEN for c in text):
        raise ValueError(f"{what}: {text!r} is not a plain text (the op must refuse)")
    return text


# ---- deleting -----------------------------------------------------------------------------------------------------------

def v_container_problem(container, going: Set[Any]) -> bool:
    blocks = [c for c in container if isinstance(c.tag, str) and c.tag in _V_BLOCKS]
    left = [b for b in blocks if b not in going]
    if not left or (blocks[-1] in going and left[-1].tag != W + "p"):
        return True
    place = {b: i for i, b in enumerate(blocks)}
    return any(x.tag == y.tag == W + "tbl" and place[y] - place[x] > 1 for x, y in zip(left, left[1:]))


def _v_take(root, gone: List[Any], wholes: List[Any], tracked: bool, author: str, where: str) -> None:
    """Delete ``gone`` from ``root`` (the source's copy) as the op must, or ValueError where it must refuse."""
    held = set(gone)
    inside = [el for g in gone for el in g.iter() if isinstance(el.tag, str)]
    if any(el.tag == W + "sectPr" and el.getparent() is not None and el.getparent().tag == W + "pPr" for el in inside):
        raise ValueError(f"{where}: a paragraph in it carries a section break (the op must refuse: section-break)")
    if any(el.tag in _V_COMMENTS for el in inside):
        raise ValueError(f"{where}: it holds a comment mark (the op must refuse: table-anchor)")
    if not tracked and any(el.tag in _V_NOTES for el in inside):
        raise ValueError(f"{where}: it holds a note mark and the deletion is untracked (the op must refuse: "
                         f"table-anchor)")
    for f in v_fields(root):
        if f.simple is None:
            ends = {_v_within(m, held) for m in (f.begin, f.separate, f.end) if m is not None}
            if len(ends) > 1:
                raise ValueError(f"{where}: a field lies partly inside it (the op must refuse: table-anchor)")
    pairs: Dict[Tuple[str, str], List[Any]] = {}
    for el in root.iter(*_V_PAIRED):
        pairs.setdefault((_V_PAIRED[el.tag], el.get(W + "id") or ""), []).append(el)
    keep: Set[Any] = set()
    for (family, _rid), els in pairs.items():
        flags = [_v_within(e, held) for e in els]
        if any(flags) and not all(flags):
            if family != "bookmark":
                raise ValueError(f"{where}: one end of a {family} lies inside it (the op must refuse: table-anchor)")
            keep |= {e for e, f in zip(els, flags) if f}
    if tracked:
        if any(el.tag in _V_PENDING for el in inside):
            raise ValueError(f"{where}: it carries pending tracked changes (the op must refuse: existing-revisions)")
        if any(el.tag == W + "txbxContent" for el in inside):
            raise ValueError(f"{where}: it holds a text box (the op must refuse: textbox)")
        for p in (el for el in inside if el.tag == W + "p"):
            for c in p:
                if isinstance(c.tag, str) and c.tag not in _V_PLAIN:
                    raise ValueError(f"{where}: a paragraph holds w:{etree.QName(c).localname} (the op must refuse: "
                                     f"structure)")
                if c.tag == W + "r" and any(t.getparent() is not c for t in c.iter(W + "t", W + "instrText")):
                    raise ValueError(f"{where}: a run holds text below another of its elements (the op must refuse: "
                                     f"structure)")
    else:
        for container in {t.getparent() for t in wholes}:
            if v_container_problem(container, {t for t in wholes if t.getparent() is container}):
                raise ValueError(f"{where}: a table going whole cannot leave its container (the op must refuse: "
                                 f"table-structure)")
    if not tracked:
        for g in gone:
            parent = g.getparent()
            at = parent.index(g)
            for m in [m for m in g.iter(W + "bookmarkStart", W + "bookmarkEnd") if m in keep]:
                tail = m.tail
                m.getparent().remove(m)
                m.tail = tail
                parent.insert(at, m)
                at += 1
            parent.remove(g)
        return
    rows_order, ppr_order, mark_order = v_order("CT_TrPr"), v_order("CT_PPr"), v_order("CT_ParaRPr")
    for g in gone:
        for el in [e for e in g.iter(W + "tr", W + "p")]:
            if el.tag == W + "tr":
                pr = el.find(W + "trPr")
                if pr is None:
                    pr = etree.Element(W + "trPr")
                    ex = [i for i, c in enumerate(el) if c.tag == W + "tblPrEx"]
                    el.insert(ex[-1] + 1 if ex else 0, pr)
                v_insert(pr, _v_marker("del", author), rows_order)
                continue
            ppr = el.find(W + "pPr")
            if ppr is None:
                ppr = etree.Element(W + "pPr")
                el.insert(0, ppr)
            mark = ppr.find(W + "rPr")
            if mark is None:
                mark = etree.Element(W + "rPr")
                v_insert(ppr, mark, ppr_order)
            v_insert(mark, _v_marker("del", author), mark_order)
            kids = list(el)
            group = None
            for c in kids:
                if c.tag != W + "r":
                    group = None
                    continue
                if group is None:
                    group = _v_marker("del", author)
                    c.addprevious(group)
                group.append(c)
                for t in c:
                    if t.tag in (W + "t", W + "instrText"):
                        t.tag = W + ("delText" if t.tag == W + "t" else "delInstrText")
                        if t.text and t.text.strip() != t.text:
                            t.set(_V_SPACE, "preserve")


def _deleted_tables(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    scope, pred, _rows = _v_select(a.get("select") or {}, ("tables",))
    tracked, author = _v_tracking(a)
    parts = ctx.scope_parts(scope)
    if sorted(parts) != sorted(a.get("parts") or []):
        errs.append(f"deleted_tables: the scope is {sorted(parts)} here, the plan says {sorted(a.get('parts') or [])}")
    old, taken = _v_old_ids(ctx), set()
    mine: Dict[str, List[int]] = {}
    for part in parts:
        if part not in ctx.both:
            errs.append(f"deleted_tables: {part} is missing from the output")
            continue
        root = ctx.src.xml(part)
        vt = VTables(root)
        chosen = []
        for k, tbl in vt.selected(pred, ctx.src_styles):
            if v_in_text_box(tbl):
                if _v_every(pred):
                    continue
                raise ValueError(f"{part} table {k} lies in a text box (the op must refuse: textbox)")
            chosen.append(k)
        held = {vt.tables[k] for k in chosen}
        outer = [k for k in chosen if not any(t in held for t in vt.tables[k].iterancestors(W + "tbl"))]
        if not outer:
            continue
        gone = [vt.tables[k] for k in outer]
        _v_take(root, gone, gone, tracked, author, f"{part} table(s) {outer}")
        _v_compare(ctx, part, root, old, taken, f"tables {outer} deleted ({'tracked' if tracked else 'untracked'})",
                   errs)
        mine[part] = outer
    n = sum(len(v) for v in mine.values())
    if n != a.get("expected_tables", -1):
        errs.append(f"deleted_tables: {n} table(s) deleted here, the plan predicted {a.get('expected_tables')}")
    if mine != {p: list(v) for p, v in (a.get("changed") or {}).items()}:
        errs.append(f"deleted_tables: the tables deleted here {mine} are not the plan's {a.get('changed')}")
    return f"deleted_tables: {n} table(s) ({'tracked' if tracked else 'untracked'}), each changed part compared whole"


def _deleted_table_rows(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    scope, pred, rows_pred = _v_select(a.get("select") or {}, ("tables", "rows"))
    if rows_pred is None:
        raise ValueError("no row selection (the op must refuse: bad-params)")
    tracked, author = _v_tracking(a)
    parts = ctx.scope_parts(scope)
    if sorted(parts) != sorted(a.get("parts") or []):
        errs.append(f"deleted_table_rows: the scope is {sorted(parts)} here, the plan says {sorted(a.get('parts') or [])}")
    old, taken = _v_old_ids(ctx), set()
    mine: Dict[str, List[list]] = {}
    for part in parts:
        if part not in ctx.both:
            errs.append(f"deleted_table_rows: {part} is missing from the output")
            continue
        root = ctx.src.xml(part)
        vt = VTables(root)
        chosen = []
        for k, tbl in vt.selected(pred, ctx.src_styles):
            if v_in_text_box(tbl):
                if _v_every(pred):
                    continue
                raise ValueError(f"{part} table {k} lies in a text box (the op must refuse: textbox)")
            if v_rows_wrapped(tbl):
                raise ValueError(f"{part} table {k}: rows in a container (the op must refuse: table-structure)")
            rs = v_rows_chosen(tbl, rows_pred, v_style_chain(tbl, ctx.src_styles), vt.text)
            if rs:
                chosen.append((k, rs))
        rows_going = {v_rows(vt.tables[k])[r] for k, rs in chosen for r in rs}
        whole = {vt.tables[k] for k, rs in chosen if len(rs) == len(v_rows(vt.tables[k]))}
        items = []
        for k, rs in chosen:
            tbl = vt.tables[k]
            if any(x in rows_going or x in whole for x in tbl.iterancestors()):
                continue                                   # goes with the row or table around it
            if v_cuts(tbl, set(rs)):
                raise ValueError(f"{part} table {k}: deleting rows {rs} cuts a vertical merge (the op must refuse: "
                                 f"vertical-merge)")
            items.append([k, rs, tbl in whole])
        if not items:
            continue
        gone, wholes = [], []
        for k, rs, every in items:
            gone += [vt.tables[k]] if every else [v_rows(vt.tables[k])[r] for r in rs]
            wholes += [vt.tables[k]] if every else []
        _v_take(root, gone, wholes, tracked, author, f"{part} table(s) {[k for k, _, _ in items]}")
        _v_compare(ctx, part, root, old, taken, f"the rows {items} deleted ({'tracked' if tracked else 'untracked'})",
                   errs)
        mine[part] = items
    n = sum(len(rs) for v in mine.values() for _, rs, _ in v)
    if n != a.get("expected_rows", -1) or sum(len(v) for v in mine.values()) != a.get("expected_tables", -1):
        errs.append(f"deleted_table_rows: {n} row(s) of {sum(len(v) for v in mine.values())} table(s) deleted here, the "
                    f"plan predicted {a.get('expected_rows')} of {a.get('expected_tables')}")
    if mine != {p: [list(x) for x in v] for p, v in (a.get("changed") or {}).items()}:
        errs.append(f"deleted_table_rows: the rows deleted here {mine} are not the plan's {a.get('changed')}")
    return f"deleted_table_rows: {n} row(s) ({'tracked' if tracked else 'untracked'}), each changed part compared whole"


# ---- inserting ----------------------------------------------------------------------------------------------------------

def _v_preserve(t, text: str) -> None:
    t.text = text
    if text != text.strip() or " " in text or "  " in text:
        t.set(_V_SPACE, "preserve")


def _v_paragraph(ppr, rpr, text: str, tracked: bool, author: str):
    p = etree.Element(W + "p")
    if ppr is not None:
        p.append(copy.deepcopy(ppr))
    if tracked:
        pr = p.find(W + "pPr")
        if pr is None:
            pr = etree.Element(W + "pPr")
            p.insert(0, pr)
        mark = pr.find(W + "rPr")
        if mark is None:
            mark = etree.Element(W + "rPr")
            v_insert(pr, mark, v_order("CT_PPr"))
        v_insert(mark, _v_marker("ins", author), v_order("CT_ParaRPr"))
    if text:
        holder = p
        if tracked:
            holder = _v_marker("ins", author)
            p.append(holder)
        r = etree.SubElement(holder, W + "r")
        if rpr is not None:
            r.append(copy.deepcopy(rpr))
        _v_preserve(etree.SubElement(r, W + "t"), text)
    return p


def _v_less(el, names: Set[str]):
    if el is None:
        return None
    out = copy.deepcopy(el)
    for c in list(out):
        if isinstance(c.tag, str) and c.tag.startswith(W) and c.tag[len(W):] in names:
            out.remove(c)
    return out if (len(out) or out.attrib) else None


def _v_cell_ppr(p):
    ppr = _v_less(p.find(W + "pPr"), {"cnfStyle", "sectPr", "pPrChange"})
    if ppr is None:
        return None
    mark = ppr.find(W + "rPr")
    if mark is not None:
        for c in list(mark):
            if c.tag in _V_MARK_GONE:
                mark.remove(c)
        if not len(mark) and not mark.attrib:
            ppr.remove(mark)
    return ppr if (len(ppr) or ppr.attrib) else None


def _v_cell_rpr(p, ppr):
    for r in p.iter(W + "r"):
        if next(r.iterancestors(W + "p"), None) is not p or r.find(W + "t") is None:
            continue
        hidden = False
        for anc in r.iterancestors():
            if anc is p:
                break
            hidden = hidden or anc.tag in _V_HIDDEN
        if hidden:
            continue
        return _v_less(r.find(W + "rPr"), {"rPrChange"})
    mark = ppr.find(W + "rPr") if ppr is not None else None
    return copy.deepcopy(mark) if mark is not None else None


def _v_new_row(ref, texts: List[str], chain: List[Any], repeat: bool, tracked: bool, author: str):
    tr = etree.Element(W + "tr")
    ex = _v_less(ref.find(W + "tblPrEx"), {"tblPrExChange"})
    if ex is not None:
        tr.append(ex)
    pr = _v_less(ref.find(W + "trPr"), {"cnfStyle", "ins", "del", "trPrChange", "tblHeader"})
    if pr is None:
        pr = etree.Element(W + "trPr")
    order = v_order("CT_TrPr")
    if repeat:
        v_insert(pr, etree.Element(W + "tblHeader"), order)
    elif any(e.get(W + "val") not in ("0", "false", "off") for st in chain for e in st.findall(f"{W}trPr/{W}tblHeader")):
        v_insert(pr, etree.Element(W + "tblHeader", {W + "val": "0"}), order)
    if tracked:
        v_insert(pr, _v_marker("ins", author), order)
    if len(pr) or pr.attrib:
        tr.append(pr)
    for tc_ref, text in zip(v_cells(ref), texts):
        tc = etree.SubElement(tr, W + "tc")
        tcpr = _v_less(tc_ref.find(W + "tcPr"), {"cnfStyle", "cellIns", "cellDel", "cellMerge", "tcPrChange"})
        if tcpr is not None:
            tc.append(tcpr)
        first = v_cell_paragraphs(tc_ref)[0]
        ppr = _v_cell_ppr(first)
        tc.append(_v_paragraph(ppr, _v_cell_rpr(first, ppr), text, tracked, author))
    return tr


def _v_typed(rows: Any) -> List[List[str]]:
    if not isinstance(rows, list) or not rows or not all(isinstance(r, list) and r for r in rows):
        raise ValueError("the rows are not a list of rows of cell texts (the op must refuse: bad-params)")
    return [[_v_text(t, "a cell") for t in r] for r in rows]


def _inserted_table_rows(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    scope, pred, rows_pred = _v_select(a.get("select") or {}, ("tables", "rows"))
    tracked, author = _v_tracking(a)
    params = a.get("params") or {}
    texts = _v_typed(params.get("rows"))
    position, repeat = params.get("position", "after"), params.get("repeat_header", False)
    if position not in ("after", "before", "at_end") or not isinstance(repeat, bool) \
            or (position == "at_end") != (rows_pred is None):
        raise ValueError(f"position {position!r} with rows {rows_pred!r} (the op must refuse: bad-params)")
    parts = ctx.scope_parts(scope)
    if sorted(parts) != sorted(a.get("parts") or []):
        errs.append(f"inserted_table_rows: the scope is {sorted(parts)} here, the plan says "
                    f"{sorted(a.get('parts') or [])}")
    old, taken = _v_old_ids(ctx), set()
    mine: Dict[str, List[list]] = {}
    for part in parts:
        if part not in ctx.both:
            errs.append(f"inserted_table_rows: {part} is missing from the output")
            continue
        root = ctx.src.xml(part)
        vt = VTables(root)
        work = []
        for k, tbl in vt.selected(pred, ctx.src_styles):
            where = f"{part} table {k}"
            if v_in_text_box(tbl):
                if _v_every(pred):
                    continue
                raise ValueError(f"{where} lies in a text box (the op must refuse: textbox)")
            rows = v_rows(tbl)
            if v_rows_wrapped(tbl) or not rows:
                raise ValueError(f"{where}: rows in a container, or none (the op must refuse: table-structure)")
            chain = v_style_chain(tbl, ctx.src_styles)
            if position == "at_end":
                r = len(rows) - 1
            else:
                rs = v_rows_chosen(tbl, rows_pred, chain, vt.text)
                if not rs:
                    continue
                if len(rs) > 1:
                    raise ValueError(f"{where}: {len(rs)} rows chosen (the op must refuse: ambiguous)")
                r = rs[0]
            cells = v_cells(rows[r])
            if v_cells_wrapped(rows[r]) or not cells or any(not v_cell_paragraphs(tc) for tc in cells):
                raise ValueError(f"{where} row {r}: cells in a container, none, or without a paragraph (the op must "
                                 f"refuse: table-structure)")
            if any(v_merged(tc) for tc in cells) or any(len(row) != len(cells) for row in texts):
                raise ValueError(f"{where} row {r}: merged cells, or rows of another length (the op must refuse: "
                                 f"table-shape)")
            work.append((k, r, rows[r], [_v_new_row(rows[r], row, chain, repeat, tracked, author) for row in texts]))
        if not work:
            continue
        for _k, _r, ref, new in work:
            if position == "before":
                for tr in new:
                    ref.addprevious(tr)
            else:
                for tr in reversed(new):
                    ref.addnext(tr)
        _v_compare(ctx, part, root, old, taken, f"{len(texts)} new row(s) {position} the reference rows of tables "
                                                f"{[k for k, _, _, _ in work]}", errs)
        mine[part] = sorted([k, r] for k, r, _, _ in work)
    n = sum(len(v) for v in mine.values())
    if n != a.get("expected_tables", -1):
        errs.append(f"inserted_table_rows: rows go into {n} table(s) here, the plan predicted {a.get('expected_tables')}")
    if mine != {p: sorted(list(x) for x in v) for p, v in (a.get("changed") or {}).items()}:
        errs.append(f"inserted_table_rows: the tables and reference rows here {mine} are not the plan's "
                    f"{a.get('changed')}")
    return (f"inserted_table_rows: {len(texts)} row(s) in {n} table(s) ({'tracked' if tracked else 'untracked'}), "
            f"built from the reference rows and compared whole")


def _v_width(root, p) -> Optional[int]:
    body = root.find(W + "body")
    sect = None
    reached = False
    for q in body.iter(W + "p"):
        reached = reached or q is p
        pr = q.find(W + "pPr")
        if reached and pr is not None and pr.find(W + "sectPr") is not None:
            sect = pr.find(W + "sectPr")
            break
    sect = sect if sect is not None else body.find(W + "sectPr")
    try:
        size, mar = sect.find(W + "pgSz"), sect.find(W + "pgMar")
        width = int(size.get(W + "w")) - int(mar.get(W + "left")) - int(mar.get(W + "right"))
    except (AttributeError, TypeError, ValueError):
        return None
    return width if width > 0 else None


def _v_table(texts: List[List[str]], widths: List[int], fixed: bool, style: Optional[str], header_rows: int,
             tracked: bool, author: str):
    tbl = etree.Element(W + "tbl")
    pr = etree.SubElement(tbl, W + "tblPr")
    if style:
        etree.SubElement(pr, W + "tblStyle", {W + "val": style})
    etree.SubElement(pr, W + "tblW", {W + "w": str(sum(widths)) if fixed else "5000", W + "type": "dxa" if fixed else "pct"})
    if not style:
        box = etree.SubElement(pr, W + "tblBorders")
        for side in _V_SIDES:
            etree.SubElement(box, W + side, {W + "val": "single", W + "sz": "4", W + "space": "0", W + "color": "auto"})
    head = header_rows > 0
    etree.SubElement(pr, W + "tblLook", {W + "val": "0420" if head else "0400", W + "firstRow": "1" if head else "0",
                                        W + "lastRow": "0", W + "firstColumn": "0", W + "lastColumn": "0",
                                        W + "noHBand": "0", W + "noVBand": "1"})
    grid = etree.SubElement(tbl, W + "tblGrid")
    for w in widths:
        etree.SubElement(grid, W + "gridCol", {W + "w": str(w)})
    for i, row in enumerate(texts):
        tr = etree.SubElement(tbl, W + "tr")
        trpr = etree.Element(W + "trPr")
        if i < header_rows:
            etree.SubElement(trpr, W + "tblHeader")
        if tracked:
            trpr.append(_v_marker("ins", author))
        if len(trpr):
            tr.append(trpr)
        for text, w in zip(row, widths):
            tc = etree.SubElement(tr, W + "tc")
            etree.SubElement(etree.SubElement(tc, W + "tcPr"), W + "tcW", {W + "w": str(w), W + "type": "dxa"})
            tc.append(_v_paragraph(None, None, text, tracked, author))
    return tbl


def _v_placement(p, position: str) -> bool:
    """A new table placed ``position`` the paragraph would stand beside a table or end the body."""
    blocks = [c for c in p.getparent() if isinstance(c.tag, str) and c.tag in _V_BLOCKS]
    i = blocks.index(p)
    if position == "before":
        return i > 0 and blocks[i - 1].tag == W + "tbl"
    return i == len(blocks) - 1 or blocks[i + 1].tag == W + "tbl"


def _inserted_tables(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    tracked, author = _v_tracking(a)
    params = a.get("params") or {}
    sel = a.get("select") or {}
    pred = sel.get("paragraphs")
    if pred in (None, "all", {}) or set(sel) - {"paragraphs", "scope"} or sel.get("scope", ["document"]) != ["document"]:
        raise ValueError("the anchors must be a targeted selection of body paragraphs (the op must refuse: bad-params)")
    if (params.get("rows") is None) == (params.get("table") is None):
        raise ValueError("rows and a table file, or neither (the op must refuse: bad-params)")
    if params.get("rows") is not None:
        texts = _v_typed(params["rows"])
        if len({len(r) for r in texts}) != 1:
            raise ValueError("typed rows of different lengths (the op must refuse: bad-params)")
    else:
        spec = params["table"]
        if table_md5(spec["file"])[1] != spec.get("md5"):
            errs.append(f"inserted_tables: the table file {spec['file']} is not the one the plan read")
        texts = [[_v_text(t, "a cell of the table file") for t in row] for row in table_grid(spec)]
    position, n_head, ref = params.get("position", "after"), params.get("header_rows", 0), params.get("style")
    if position not in ("after", "before") or type(n_head) is not int or not 0 <= n_head <= len(texts):
        raise ValueError(f"position {position!r} or header_rows {n_head!r} (the op must refuse: bad-params)")
    asked = params.get("column_widths")
    fixed = None
    if asked is not None:
        if not isinstance(asked, list) or len(asked) != len(texts[0]) or not all(
                type(w) in (int, float) and 0 < w * 1440 <= 31680 for w in asked):
            raise ValueError(f"column_widths {asked!r} (the op must refuse: bad-params)")
        fixed = [max(1, round(w * 1440)) for w in asked]
    style = None
    if ref is not None:
        style = v_resolve_style(ctx.src_styles, ref)
        if style is None:
            raise ValueError(f"no style {ref!r} (the op must refuse: missing-style)")
        if v_type(ctx.src_styles.by_id[style]) != "table":
            raise ValueError(f"{ref!r} is not a table style (the op must refuse: style-mismatch)")
    part = ctx.src.main_part()
    if sorted(a.get("parts") or []) != [part]:
        errs.append(f"inserted_tables: the body is {part} here, the plan says {a.get('parts')}")
    root = ctx.src.xml(part)
    view = PartView(root)
    found: List[int] = []
    work = []
    for bi, p in enumerate(view.pars):
        if not para_matches(pred, p, bi, view, ctx.src_styles):
            continue
        where = f"{part} paragraph {bi}"
        if next(p.iterancestors(W + "p"), None) is not None:
            raise ValueError(f"{where} lies in a text box (the op must refuse: textbox)")
        if next(p.iterancestors(W + "tc"), None) is not None:
            raise ValueError(f"{where} lies in a table (the op must refuse: table-placement)")
        pr = p.find(W + "pPr")
        if position == "after" and pr is not None and pr.find(W + "sectPr") is not None:
            raise ValueError(f"{where} carries a section break (the op must refuse: section-break)")
        if _v_placement(p, position):
            raise ValueError(f"{where}: the table would stand beside a table or end the body (the op must refuse: "
                             f"table-placement)")
        width = _v_width(root, p)
        if fixed is None and width is None:
            raise ValueError(f"{where}: no text width in its section (the op must refuse: table-placement)")
        n = len(texts[0])
        widths = fixed or [width * (k + 1) // n - width * k // n for k in range(n)]
        found.append(bi)
        work.append((p, _v_table(texts, widths, fixed is not None, style, n_head, tracked, author)))
    for p, tbl in work:
        (p.addprevious if position == "before" else p.addnext)(tbl)
    if work:
        _v_compare(ctx, part, root, set(_v_old_ids(ctx)), set(), f"{len(work)} new table(s) {position} their anchors",
                   errs)
    here = {part: found} if found else {}
    if len(found) != a.get("expected_anchors", -1) or here != {p: list(v) for p, v in (a.get("anchors") or {}).items()}:
        errs.append(f"inserted_tables: anchors here {here}, the plan predicted {a.get('anchors')}")
    return (f"inserted_tables: {len(found)} table(s) of {len(texts)} row(s) ({'tracked' if tracked else 'untracked'}), "
            f"built from the source and compared whole")


TABLE_STRUCTURE_KINDS = {"deleted_tables": _deleted_tables, "deleted_table_rows": _deleted_table_rows,
                         "inserted_table_rows": _inserted_table_rows, "inserted_tables": _inserted_tables}
