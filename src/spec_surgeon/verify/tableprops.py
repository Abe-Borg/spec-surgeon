"""V10 kind ``table_props`` — set_table_props, verified (built-in batch 5, item 37 — docs/BUILTIN_OPS_BATCH5_DESIGN.md
§5.2).

This module's own reading of the op's definitions (transform side: ops/set_table_props.py). Nothing here comes from ops/
(rule 1):

* the asked changes are read from the user's own parameters with this module's defaults (``_v_asked``: border
  ``single``, 4 eighths of a point, space 0, ``auto``; ``clear_cell_overrides`` false; never tracked);
* the selection (``tableselect.VTables``), the table style chain (``tableselect.v_style_chain``);
* the expected ``w:tblBorders`` (``_v_box``: asked sides exactly, ``nil`` for a side any chain level shows), the merged
  ``w:tblCellMar`` (``_v_margins``), ``w:tblW``, the row elements (``_v_row_el``: on for an asked row, an explicit off
  only where the chain's ``w:trPr`` turns the element on) and the cells' cleared elements;
* "already" (``_v_same``: the own elements of that name are exactly the expected one);
* the schema order every element is written in — read from the bundled wml.xsd itself (``v_order``: a complexType's
  elements following its extension base, groups, sequences and choices, in the order they are listed), not from a table;
* the refusals (text box, no ``w:tblPr``, start/end sides, rows or cells in containers, too many header rows).

Fails when the tables, rows or cells that change (or their counts) differ from the plan's; when a changed table's
``w:tblPr``, a changed row's ``w:trPr`` (and where it stands) or a changed cell's ``w:tcPr`` is not exactly the source's
with the expected elements written; and — a post-condition on the OUTPUT, for every selected table, changed or not —
when the table does not show the asked borders (under both readings of border inheritance: the nearest level holding a
``w:tblBorders`` decides every side, or each side is decided by the nearest level naming it), the asked margins and width
(nearest level), the asked header rows and row splitting (own row element, else the nearest style level, else off), or a
cell keeps an override ``clear_cell_overrides`` removes. Only the checked elements leave the residual (``strip_tbl``,
``strip_tr``, ``strip_tc``).
"""
from __future__ import annotations

import copy
import functools
import re
from typing import Any, Dict, List, Sequence, Tuple

from lxml import etree

from .reader import W
from .props import Ctx, _plain, sig
from .tableselect import (VTables, v_cells, v_cells_wrapped, v_in_text_box, v_parse_select, v_rows, v_rows_wrapped,
                          v_style_chain)
from .xsd import SCHEMA_DIR

_V_SIDES = ("top", "left", "bottom", "right", "insideH", "insideV")
_V_MARGINS = ("top", "left", "bottom", "right")
_V_HIDDEN = {"nil", "none"}
_V_OFF = {"0", "false", "off"}
_V_STYLES = {"single", "thick", "double", "dotted", "dashed", "dotDash", "dotDotDash", "triple", "thinThickSmallGap",
             "thickThinSmallGap", "wave", "doubleWave", "dashSmallGap", "threeDEmboss", "threeDEngrave", "outset",
             "inset"}
_V_HEX = re.compile(r"[0-9A-Fa-f]{6}")
_V_TWIPS = 31680
_XS = "{http://www.w3.org/2001/XMLSchema}"
_WML = "ISO-IEC29500-4_2016/wml.xsd"


# ---- the schema order, read from the bundled schema --------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _v_schema():
    root = etree.parse(str(SCHEMA_DIR / _WML)).getroot()
    types = {c.get("name"): c for c in root.findall(_XS + "complexType")}
    groups = {g.get("name"): g for g in root.findall(_XS + "group")}
    return types, groups


def _v_unprefix(name: str) -> str:
    return name.split(":", 1)[1] if ":" in name else name


@functools.lru_cache(maxsize=16)
def v_order(type_name: str) -> Tuple[str, ...]:
    """The local names of a complexType's child elements in the order the schema lists them: its extension base's first,
    then its own (sequences, choices and group references followed)."""
    types, groups = _v_schema()

    def listed(node) -> List[str]:
        out: List[str] = []
        for c in node:
            if not isinstance(c.tag, str):
                continue
            if c.tag == _XS + "element":
                out.append(c.get("name") or _v_unprefix(c.get("ref")))
            elif c.tag in (_XS + "sequence", _XS + "choice"):
                out += listed(c)
            elif c.tag == _XS + "group":
                out += listed(groups[_v_unprefix(c.get("ref"))])
        return out

    ct = types[type_name]
    ext = ct.find(f"{_XS}complexContent/{_XS}extension")
    if ext is not None:
        return v_order(_v_unprefix(ext.get("base"))) + tuple(listed(ext))
    return tuple(listed(ct))


def _v_rank(el, order: Sequence[str]) -> float:
    if not isinstance(el.tag, str) or not el.tag.startswith(W):
        return len(order) - 1.5              # comments, extension elements: before the last (…Change) child
    local = el.tag[len(W):]
    return order.index(local) if local in order else len(order) - 1.5


def v_insert(parent, el, order: Sequence[str]) -> None:
    """``el`` goes before the first child of ``parent`` that ranks after it in ``order``."""
    r = _v_rank(el, order)
    for i, c in enumerate(parent):
        if _v_rank(c, order) > r:
            parent.insert(i, el)
            return
    parent.append(el)


def v_put(parent, name: str, el, order: Sequence[str]) -> None:
    """Every ``name`` child of ``parent`` removed, then ``el`` (when not None) inserted in order."""
    for old in parent.findall(W + name):
        parent.remove(old)
    if el is not None:
        v_insert(parent, el, order)


# ---- the asked changes -------------------------------------------------------------------------------------------------

def _v_int(v: Any, lo: int, hi: int) -> int:
    if type(v) is not int or not lo <= v <= hi:
        raise ValueError(f"bad number {v!r} (expected {lo}–{hi}; the op must refuse)")
    return v


def _v_color(v: Any) -> str:
    if v == "auto":
        return "auto"
    if isinstance(v, str) and _V_HEX.fullmatch(v):
        return v.upper()
    raise ValueError(f"bad colour {v!r} (the op must refuse)")


def _v_asked(params: Dict[str, Any]) -> Dict[str, Any]:
    """The user's own parameters with this module's defaults — ValueError where the op must refuse."""
    known = {"borders", "cell_margins", "width", "repeat_header_rows", "cant_split", "clear_cell_overrides", "tracked"}
    if set(params) - known:
        raise ValueError(f"unknown parameter(s) {sorted(set(params) - known)} (the op must refuse)")
    if params.get("tracked"):
        raise ValueError("table property changes are never tracked (the op must refuse)")
    out: Dict[str, Any] = {"borders": None, "margins": None, "width": None, "header": None, "split": None}
    b = params.get("borders")
    if b == "none":
        out["borders"] = {}
    elif isinstance(b, dict) and b and not set(b) - set(_V_SIDES):
        out["borders"] = {}
        for side, v in b.items():
            if not isinstance(v, dict) or set(v) - {"style", "size", "space", "color"} or \
                    v.get("style", "single") not in _V_STYLES:
                raise ValueError(f"bad border {side}: {v!r} (the op must refuse)")
            out["borders"][side] = {W + "val": v.get("style", "single"), W + "sz": str(_v_int(v.get("size", 4), 2, 96)),
                                    W + "space": str(_v_int(v.get("space", 0), 0, 31)),
                                    W + "color": _v_color(v.get("color", "auto"))}
    elif b is not None:
        raise ValueError(f"bad borders {b!r} (the op must refuse)")
    m = params.get("cell_margins")
    if m is not None:
        if not isinstance(m, dict) or not m or set(m) - set(_V_MARGINS):
            raise ValueError(f"bad cell_margins {m!r} (the op must refuse)")
        out["margins"] = {s: {W + "w": str(_v_int(v, 0, _V_TWIPS)), W + "type": "dxa"} for s, v in m.items()}
    w = params.get("width")
    if w == "auto":
        out["width"] = {W + "w": "0", W + "type": "auto"}
    elif isinstance(w, dict) and list(w) == ["pct"]:
        out["width"] = {W + "w": str(_v_int(w["pct"], 1, 100) * 50), W + "type": "pct"}
    elif isinstance(w, dict) and list(w) == ["twips"]:
        out["width"] = {W + "w": str(_v_int(w["twips"], 1, _V_TWIPS)), W + "type": "dxa"}
    elif w is not None:
        raise ValueError(f"bad width {w!r} (the op must refuse)")
    if params.get("repeat_header_rows") is not None:
        out["header"] = _v_int(params["repeat_header_rows"], 0, 10 ** 6)
    if params.get("cant_split") is not None:
        if not isinstance(params["cant_split"], bool):
            raise ValueError("bad cant_split (the op must refuse)")
        out["split"] = params["cant_split"]
    clear = params.get("clear_cell_overrides", False)
    if not isinstance(clear, bool) or (clear and out["borders"] is None and out["margins"] is None):
        raise ValueError("clear_cell_overrides needs borders or cell_margins (the op must refuse)")
    out["clear"] = clear
    if all(out[k] is None for k in ("borders", "margins", "width", "header", "split")):
        raise ValueError("nothing asked (the op must refuse)")
    return out


# ---- the definitions ---------------------------------------------------------------------------------------------------

def _v_shows(el) -> bool:
    return el is not None and el.get(W + "val") not in _V_HIDDEN


def _v_on(el) -> bool:
    return el is not None and el.get(W + "val") not in _V_OFF


def _v_box(asked: Dict[str, Dict[str, str]], chain: List[Any]):
    lit = set()
    for st in chain:
        bdr = st.find(f"{W}tblPr/{W}tblBorders")
        if bdr is not None:
            lit |= {s for s in _V_SIDES if _v_shows(bdr.find(W + s))}
    box = etree.Element(W + "tblBorders")
    for side in _V_SIDES:
        if side in asked:
            etree.SubElement(box, W + side, asked[side])
        elif side in lit:
            etree.SubElement(box, W + side, {W + "val": "nil"})
    return box if len(box) else None


def _v_margins(asked: Dict[str, Dict[str, str]], own):
    el = copy.deepcopy(own) if own is not None else etree.Element(W + "tblCellMar")
    for side in _V_MARGINS:
        if side in asked:
            v_put(el, side, etree.Element(W + side, asked[side]), v_order("CT_TblCellMar"))
    return el


def _v_row_el(name: str, on: bool, chain: List[Any]):
    if on:
        return etree.Element(W + name)
    lit = any(_v_on(e) for st in chain for e in st.findall(f"{W}trPr/{W}{name}"))
    return etree.Element(W + name, {W + "val": "0"}) if lit else None


def _v_same(parent, name: str, want) -> bool:
    own = [sig(e) for e in parent.findall(W + name)] if parent is not None else []
    return own == ([sig(want)] if want is not None else [])


def _v_table_gets(pr, chain: List[Any], asked: Dict[str, Any]) -> Dict[str, Any]:
    want: Dict[str, Any] = {}
    if asked["borders"] is not None:
        want["tblBorders"] = _v_box(asked["borders"], chain)
    if asked["margins"] is not None:
        want["tblCellMar"] = _v_margins(asked["margins"], pr.find(W + "tblCellMar"))
    if asked["width"] is not None:
        want["tblW"] = etree.Element(W + "tblW", asked["width"])
    return {n: e for n, e in want.items() if not _v_same(pr, n, e)}


def _v_row_gets(tbl, chain: List[Any], asked: Dict[str, Any]) -> List[Tuple[int, Dict[str, Any]]]:
    out = []
    if asked["header"] is None and asked["split"] is None:
        return out
    for r, tr in enumerate(v_rows(tbl)):
        pr = tr.find(W + "trPr")
        gets = {}
        if asked["header"] is not None:
            want = _v_row_el("tblHeader", r < asked["header"], chain)
            if not _v_same(pr, "tblHeader", want):
                gets["tblHeader"] = want
        if asked["split"] is not None:
            want = _v_row_el("cantSplit", asked["split"], chain)
            if not _v_same(pr, "cantSplit", want):
                gets["cantSplit"] = want
        if gets:
            out.append((r, gets))
    return out


def _v_cell_names(asked: Dict[str, Any]) -> List[str]:
    return [n for n, k in (("tcBorders", "borders"), ("tcMar", "margins")) if asked[k] is not None]


def _v_cell_gets(tbl, asked: Dict[str, Any]) -> List[Tuple[int, int, List[str]]]:
    out = []
    if not asked["clear"]:
        return out
    for r, tr in enumerate(v_rows(tbl)):
        for c, tc in enumerate(v_cells(tr)):
            names = [n for n in _v_cell_names(asked) if tc.find(f"{W}tcPr/{W}{n}") is not None]
            if names:
                out.append((r, c, names))
    return out


def _v_refusal(tbl, chain: List[Any], asked: Dict[str, Any], where: str) -> None:
    """ValueError where the op must refuse this table."""
    def aliased(el) -> bool:
        return el is not None and (el.find(W + "start") is not None or el.find(W + "end") is not None)
    pr = tbl.find(W + "tblPr")
    if v_in_text_box(tbl):
        raise ValueError(f"{where} lies in a text box (the op must refuse: textbox)")
    if pr is None:
        raise ValueError(f"{where} has no w:tblPr (the op must refuse: table-structure)")
    if asked["borders"] is not None and (aliased(pr.find(W + "tblBorders"))
                                         or any(aliased(st.find(f"{W}tblPr/{W}tblBorders")) for st in chain)):
        raise ValueError(f"{where}: a border side is spelled start/end (the op must refuse: conflict-property)")
    if asked["margins"] is not None and aliased(pr.find(W + "tblCellMar")):
        raise ValueError(f"{where}: a cell margin side is spelled start/end (the op must refuse: conflict-property)")
    rows = v_rows(tbl)
    if (asked["header"] is not None or asked["split"] is not None or asked["clear"]) and v_rows_wrapped(tbl):
        raise ValueError(f"{where}: rows inside a container (the op must refuse: table-structure)")
    if asked["clear"] and any(v_cells_wrapped(tr) for tr in rows):
        raise ValueError(f"{where}: cells inside a container (the op must refuse: table-structure)")
    if asked["header"] is not None and asked["header"] > len(rows):
        raise ValueError(f"{where}: {asked['header']} header rows asked of {len(rows)} (the op must refuse: "
                         f"table-structure)")


# ---- the checks --------------------------------------------------------------------------------------------------------

def _v_with(src, gets: Dict[str, Any], container: str, order_type: str):
    """``src`` (or a new empty ``container``) with each name in ``gets`` written, in schema order; None when empty."""
    el = copy.deepcopy(src) if src is not None else etree.Element(W + container)
    order = v_order(order_type)
    for name in sorted(gets, key=lambda n: order.index(n)):
        v_put(el, name, copy.deepcopy(gets[name]) if gets[name] is not None else None, order)
    return el if (len(el) or el.attrib) else None


def _v_without(src, names: Sequence[str]):
    el = copy.deepcopy(src)
    for c in list(el):
        if isinstance(c.tag, str) and c.tag in {W + n for n in names}:
            el.remove(c)
    return el if (len(el) or el.attrib) else None


def _show(el) -> Any:
    return None if el is None else etree.tostring(el, encoding="unicode").replace(f' xmlns:w="{W[1:-1]}"', "")[:300]


def _v_post(o_tbl, chain: List[Any], asked: Dict[str, Any], where: str, errs: List[str]) -> None:
    """The output table shows what was asked (every selected table, changed or not)."""
    levels = [o_tbl.find(W + "tblPr")] + [st.find(W + "tblPr") for st in chain]       # nearest first
    levels = [x for x in levels if x is not None]
    if asked["borders"] is not None:
        holder = next((x.find(W + "tblBorders") for x in levels if x.find(W + "tblBorders") is not None), None)
        for side in _V_SIDES:
            want = asked["borders"].get(side)
            el = holder.find(W + side) if holder is not None else None
            whole = dict(el.attrib) if _v_shows(el) else None
            near = next((x.find(f"{W}tblBorders/{W}{side}") for x in levels
                         if x.find(f"{W}tblBorders/{W}{side}") is not None), None)
            by_side = dict(near.attrib) if _v_shows(near) else None
            if whole != want or by_side != want:
                errs.append(f"{where}: the {side} border shows {_plain(whole)} (nearest border set) / {_plain(by_side)} "
                            f"(nearest {side} side) in the output, asked {_plain(want)}")
    for side, want in (asked["margins"] or {}).items():
        near = next((x.find(f"{W}tblCellMar/{W}{side}") for x in levels
                     if x.find(f"{W}tblCellMar/{W}{side}") is not None), None)
        if near is None or dict(near.attrib) != want:
            errs.append(f"{where}: the {side} cell margin is {_plain(dict(near.attrib)) if near is not None else None} "
                        f"in the output, asked {_plain(want)}")
    if asked["width"] is not None:
        near = next((x.find(W + "tblW") for x in levels if x.find(W + "tblW") is not None), None)
        if near is None or dict(near.attrib) != asked["width"]:
            errs.append(f"{where}: the width is {_plain(dict(near.attrib)) if near is not None else None} in the output, "
                        f"asked {_plain(asked['width'])}")
    rows = v_rows(o_tbl)
    for name, want_of in (("tblHeader", (lambda r: r < asked["header"]) if asked["header"] is not None else None),
                          ("cantSplit", (lambda r: asked["split"]) if asked["split"] is not None else None)):
        if want_of is None:
            continue
        for r, tr in enumerate(rows):
            own = tr.findall(f"{W}trPr/{W}{name}")
            if len(own) > 1:
                errs.append(f"{where} row {r}: w:{name} {len(own)} times in the output")
                continue
            if own:
                on = _v_on(own[0])
            else:
                near = next((e for st in chain for e in st.findall(f"{W}trPr/{W}{name}")), None)
                on = _v_on(near)
            if on != want_of(r):
                errs.append(f"{where} row {r}: w:{name} is {'on' if on else 'off'} in the output, asked "
                            f"{'on' if want_of(r) else 'off'}")
    if asked["clear"]:
        for r, tr in enumerate(rows):
            for c, tc in enumerate(v_cells(tr)):
                kept = [n for n in _v_cell_names(asked) if tc.find(f"{W}tcPr/{W}{n}") is not None]
                if kept:
                    errs.append(f"{where} row {r} cell {c}: keeps its own {kept} in the output (clear_cell_overrides)")


def _table_props(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    asked = _v_asked(a.get("params") or {})
    scope, pred = v_parse_select(a.get("select") or {})
    parts = ctx.scope_parts(scope)
    if sorted(parts) != sorted(a.get("parts", [])):
        errs.append(f"table_props: scope resolves to {sorted(parts)} here, plan says {sorted(a.get('parts', []))}")
    mine: Dict[str, Dict[str, list]] = {}
    n_tables = n_rows = n_cells = 0
    for part in parts:
        if part not in ctx.both:
            errs.append(f"table_props: {part} missing from the output")
            continue
        sv, ov = ctx.view(part)
        s_t, o_t = VTables(sv.root), VTables(ov.root)
        if len(s_t.tables) != len(o_t.tables):
            errs.append(f"table_props: {part} has {len(s_t.tables)} table(s) in the source and {len(o_t.tables)} in the "
                        f"output")
            continue
        rec = {"tables": [], "rows": [], "cells": []}
        for k, tbl in s_t.selected(pred, ctx.src_styles):
            where = f"{part} table {k}"
            chain = v_style_chain(tbl, ctx.src_styles)
            _v_refusal(tbl, chain, asked, where)
            o_tbl = o_t.tables[k]
            s_pr, o_pr = tbl.find(W + "tblPr"), o_tbl.find(W + "tblPr")
            t_gets = _v_table_gets(s_pr, chain, asked)
            r_gets = _v_row_gets(tbl, chain, asked)
            c_gets = _v_cell_gets(tbl, asked)
            if t_gets:
                rec["tables"].append([k, sorted(t_gets)])
                want = _v_with(s_pr, t_gets, "tblPr", "CT_TblPr")
                if sig(o_pr) != sig(want):
                    errs.append(f"{where}: w:tblPr is {_show(o_pr)}, expected {_show(want)}")
                ctx.strip_tbl.setdefault(part, {}).setdefault(k, set()).update(t_gets)
            s_rows, o_rows = v_rows(tbl), v_rows(o_tbl)
            if len(s_rows) != len(o_rows):
                errs.append(f"{where}: {len(s_rows)} row(s) in the source, {len(o_rows)} in the output")
                continue
            for r, g in r_gets:
                rec["rows"].append([k, r, sorted(g)])
                want = _v_with(s_rows[r].find(W + "trPr"), g, "trPr", "CT_TrPr")
                got = o_rows[r].find(W + "trPr")
                if sig(got) != sig(want):
                    errs.append(f"{where} row {r}: w:trPr is {_show(got)}, expected {_show(want)}")
                elif got is not None:
                    kids = [c for c in o_rows[r] if isinstance(c.tag, str)]
                    at = kids.index(got)
                    if any(c.tag != W + "tblPrEx" for c in kids[:at]) or any(c.tag == W + "tblPrEx" for c in kids[at:]):
                        errs.append(f"{where} row {r}: w:trPr is not the row's first property element (right after "
                                    f"its w:tblPrEx)")
                ctx.strip_tr.setdefault(part, {}).setdefault((k, r), set()).update(g)
            for r, c, names in c_gets:
                rec["cells"].append([k, r, c, sorted(names)])
                s_cells, o_cells = v_cells(s_rows[r]), v_cells(o_rows[r])
                if len(s_cells) != len(o_cells):
                    errs.append(f"{where} row {r}: cells changed in number")
                    continue
                want = _v_without(s_cells[c].find(W + "tcPr"), names)
                got = o_cells[c].find(W + "tcPr")
                if sig(got) != sig(want):
                    errs.append(f"{where} row {r} cell {c}: w:tcPr is {_show(got)}, expected {_show(want)}")
                ctx.strip_tc.setdefault(part, {}).setdefault((k, r, c), set()).update(names)
            if t_gets or r_gets or c_gets:
                n_tables += 1
                n_rows += len(r_gets)
                n_cells += len(c_gets)
            _v_post(o_tbl, v_style_chain(o_tbl, ctx.out_styles), asked, where, errs)
        if rec["tables"] or rec["rows"] or rec["cells"]:
            mine[part] = rec
    for n, key in ((n_tables, "expected_tables"), (n_rows, "expected_rows"), (n_cells, "expected_cells")):
        if n != a.get(key, -1):
            errs.append(f"table_props: {n} change here ({key}), plan predicted {a.get(key)}")

    def norm(d):
        return {p: {k: sorted(tuple(x[:-1]) + (tuple(x[-1]),) for x in v) for k, v in r.items() if v}
                for p, r in (d or {}).items()}
    if norm(mine) != norm(a.get("changed")):
        errs.append(f"table_props: the tables, rows and cells that change here {norm(mine)} are not the plan's "
                    f"{norm(a.get('changed'))}")
    return f"table_props: {n_tables} table(s), {n_rows} row(s), {n_cells} cell(s) changed"


TABLE_PROP_KINDS = {"table_props": _table_props}
