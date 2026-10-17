"""V10 kind ``borders_shading`` — set_borders_shading, verified (built-in batch 4, docs/BUILTIN_OPS_BATCH4_DESIGN.md §1).

This module's own reading of the op's definitions (transform side: ops/set_borders_shading.py; CLAUDE.md lists them
among the definitions that must stay identical on both sides). Nothing here comes from ops/ (rule 1):

* the asked box and shading are read from the user's own parameters with this module's defaults (``_v_asked``: border
  ``single``, 4 eighths of a point, 1 point, ``auto``; pattern ``clear``, colour ``auto``; tracked: as asked, else when
  ``paragraphs`` narrows the selection);
* the levels below a paragraph (``_v_below_paragraph``: docDefaults pPr, the table style chain, the paragraph style
  chain, root-most first) and below a style (``_v_below_style``: docDefaults pPr, the basedOn ancestors root-most first);
* a visible side (``_v_side_shows``), visible shading (``_v_shd_shows``), the expected ``w:pBdr`` (``_v_box``) and
  ``w:shd`` (``_v_shade``) and the already rule (``_v_gets``: the own element's signature equal to the expected one).

Fails when the changed targets (or their count) differ from the plan's; when a changed target's output ``w:pBdr`` /
``w:shd`` is not exactly the expected element; when any other pPr child of it changed; when the ``w:pPrChange`` breaks the
paragraph-property rules; and — a post-condition on the OUTPUT, for every selected target — when the box or the shading
it shows is not the asked one, under either reading of border inheritance (the nearest level holding a ``w:pBdr``
decides every side, or each side is decided by the nearest level naming it). Only the changed elements (and, for
paragraphs, ``pPrChange``) leave the residual.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from .reader import W
from .props import Ctx, Styles, _check_ppr_change, _local, _plain, para_matches, sig

V_SIDES = ("top", "left", "bottom", "right", "between", "bar")
V_STYLES = {"single", "thick", "double", "dotted", "dashed", "dotDash", "dotDotDash", "triple", "thinThickSmallGap",
            "thickThinSmallGap", "wave", "doubleWave", "dashSmallGap", "threeDEmboss", "threeDEngrave", "outset",
            "inset"}
V_PATTERNS = {"clear", "solid", "horzStripe", "vertStripe", "diagStripe", "reverseDiagStripe", "horzCross",
              "diagCross"} | {f"pct{n}" for n in (5, 10, 12, 15, 20, 25, 30, 35, 37, 40, 45, 50, 55, 60, 62, 65, 70, 75,
                                                    80, 85, 87, 90, 95)}
_V_HEX = re.compile(r"[0-9A-Fa-f]{6}")
_V_HIDDEN = {"nil", "none"}


def _v_color(v: Any) -> str:
    if v == "auto":
        return "auto"
    if isinstance(v, str) and _V_HEX.fullmatch(v):
        return v.upper()
    raise ValueError(f"bad colour {v!r}")


def _v_int(v: Any, lo: int, hi: int) -> str:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError(f"bad number {v!r} (expected {lo}–{hi})")
    return str(v)


def _v_asked(params: Dict[str, Any]) -> Tuple[Optional[Dict[str, Dict[str, str]]], Any]:
    """(the asked box: None not asked, {} for "none", else side → attributes; the asked shading: None, "none" or its
    attributes) — the user's own words with this module's defaults; ValueError where the op must refuse."""
    b, s = params.get("borders"), params.get("shading")
    if b is None and s is None:
        raise ValueError("neither borders nor shading asked (the op must refuse)")
    borders: Optional[Dict[str, Dict[str, str]]] = None
    if b is not None:
        if b == "none":
            borders = {}
        elif isinstance(b, dict) and b and not set(b) - set(V_SIDES):
            borders = {}
            for side, v in b.items():
                if not isinstance(v, dict) or set(v) - {"style", "size", "space", "color"}:
                    raise ValueError(f"bad border {side}: {v!r}")
                style = v.get("style", "single")
                if style not in V_STYLES:
                    raise ValueError(f"bad border style {style!r}")
                borders[side] = {W + "val": style, W + "sz": _v_int(v.get("size", 4), 2, 96),
                                 W + "space": _v_int(v.get("space", 1), 0, 31), W + "color": _v_color(v.get("color", "auto"))}
        else:
            raise ValueError(f"bad borders {b!r}")
    shading: Any = None
    if s is not None:
        if s == "none":
            shading = "none"
        elif isinstance(s, dict) and "fill" in s and not set(s) - {"fill", "pattern", "color"}:
            pattern = s.get("pattern", "clear")
            if pattern not in V_PATTERNS:
                raise ValueError(f"bad pattern {pattern!r}")
            shading = {W + "val": pattern, W + "color": _v_color(s.get("color", "auto")), W + "fill": _v_color(s["fill"])}
        else:
            raise ValueError(f"bad shading {s!r}")
    return borders, shading


def _v_table_chain_id(p, styles: Styles) -> Optional[str]:
    for anc in p.iterancestors(W + "tbl"):
        ts = anc.find(W + "tblPr/" + W + "tblStyle")
        return ts.get(W + "val") if ts is not None else styles.default.get("table")
    return None


def _v_paragraph_style_id(p, styles: Styles) -> Optional[str]:
    """The paragraph style a paragraph's levels come from: its w:pStyle's value — an empty or missing value names no
    style —, else (no w:pStyle) the default paragraph style. template.Eff's reading (the agreed counterpart of the
    transform's effective.Resolver), not the selector's ``style_in`` one."""
    ps = p.find(W + "pPr/" + W + "pStyle")
    return ps.get(W + "val") if ps is not None else styles.default.get("paragraph")


def _v_below_paragraph(p, styles: Styles) -> List[Any]:
    """The pPr of every level below a paragraph's direct formatting, lowest first: docDefaults, the table style chain
    (its table's tblStyle, else the default table style), the paragraph style chain (``_v_paragraph_style_id``) — each
    chain root-most first."""
    out = [styles.doc_defaults_ppr()]
    tsid = _v_table_chain_id(p, styles)
    if tsid:
        out += [st.find(W + "pPr") for st in reversed(styles.chain(tsid))]
    out += [st.find(W + "pPr") for st in reversed(styles.chain(_v_paragraph_style_id(p, styles)))]
    return out


def _v_below_style(st, styles: Styles) -> List[Any]:
    """The pPr of every level below a style's own definition: docDefaults, then its basedOn ancestors root-most first."""
    ancestors = styles.chain(st.get(W + "styleId"))[1:]
    return [styles.doc_defaults_ppr()] + [s.find(W + "pPr") for s in reversed(ancestors)]


def _v_side_shows(el) -> bool:
    return el is not None and el.get(W + "val") not in _V_HIDDEN


def _v_shd_shows(el) -> bool:
    if el is None or el.get(W + "val") == "nil":
        return False
    return not (el.get(W + "val") == "clear" and el.get(W + "fill") in (None, "auto") and el.get(W + "themeFill") is None)


def _v_box(asked: Dict[str, Dict[str, str]], below: List[Any]):
    """The expected w:pBdr element (None for none)."""
    lit = set()
    for ppr in below:
        bdr = ppr.find(W + "pBdr") if ppr is not None else None
        if bdr is not None:
            lit |= {side for side in V_SIDES if _v_side_shows(bdr.find(W + side))}
    box = None
    for side in V_SIDES:
        if side in asked:
            attrs = asked[side]
        elif side in lit:
            attrs = {W + "val": "nil"}
        else:
            continue
        if box is None:
            box = etree.Element(W + "pBdr")
        etree.SubElement(box, W + side, attrs)
    return box


def _v_shade(asked: Any, below: List[Any]):
    """The expected w:shd element (None for none)."""
    if asked != "none":
        return etree.Element(W + "shd", asked)
    nearest = None
    for ppr in below:
        if ppr is not None and ppr.find(W + "shd") is not None:
            nearest = ppr.find(W + "shd")
    return etree.Element(W + "shd", {W + "val": "clear", W + "color": "auto", W + "fill": "auto"}) \
        if _v_shd_shows(nearest) else None


def _v_gets(own_ppr, below: List[Any], borders, shading) -> Dict[str, Any]:
    """Element name → its expected element (None = absent), for the asked elements that are not already as expected."""
    out: Dict[str, Any] = {}
    if borders is not None:
        want = _v_box(borders, below)
        if sig(own_ppr.find(W + "pBdr") if own_ppr is not None else None) != sig(want):
            out["pBdr"] = want
    if shading is not None:
        want = _v_shade(shading, below)
        if sig(own_ppr.find(W + "shd") if own_ppr is not None else None) != sig(want):
            out["shd"] = want
    return out


def _describe(el) -> Any:
    if el is None:
        return None
    if _local(el) == "pBdr":
        return {_local(c): _plain(dict(c.attrib)) for c in el}
    return _plain(dict(el.attrib))


def _check_elements(S, O, gets: Dict[str, Any], skip: set, where: str, errs: List[str]) -> None:
    """The output pPr ``O`` holds exactly the expected element for each name in ``gets`` (absent when None), and every
    other child but ``skip`` as the source pPr ``S`` had it, in order."""
    for name, want in gets.items():
        found = O.findall(W + name) if O is not None else []
        got = found[0] if found else None
        if len(found) > 1 or sig(got) != sig(want):
            errs.append(f"{where}: w:{name} is {_describe(got)}" + (f" (+{len(found) - 1} more)" if len(found) > 1 else "")
                        + f", expected {_describe(want)}")
    leave = set(gets) | skip
    others_s = [sig(c) for c in (S if S is not None else []) if _local(c) not in leave]
    others_o = [sig(c) for c in (O if O is not None else []) if _local(c) not in leave]
    if others_s != others_o:
        errs.append(f"{where}: paragraph properties other than {sorted(gets)} changed")


def _check_shown(levels: List[Any], borders, shading, where: str, errs: List[str]) -> None:
    """Post-condition on the OUTPUT: ``levels`` are the pPr lowest first, the target's own last. The asked sides show
    exactly as asked and no other side shows — under both readings of border inheritance —, and the shading is the asked
    one (or, for "none", none shows)."""
    if borders is not None:
        holder = next((ppr.find(W + "pBdr") for ppr in reversed(levels)
                       if ppr is not None and ppr.find(W + "pBdr") is not None), None)
        for side in V_SIDES:
            want = borders.get(side)
            el = holder.find(W + side) if holder is not None else None
            whole = dict(el.attrib) if _v_side_shows(el) else None
            own = next((ppr.find(W + "pBdr/" + W + side) for ppr in reversed(levels)
                        if ppr is not None and ppr.find(W + "pBdr/" + W + side) is not None), None)
            by_side = dict(own.attrib) if _v_side_shows(own) else None
            if whole != want or by_side != want:
                errs.append(f"{where}: the {side} border shows {_plain(whole)} (nearest border box) / {_plain(by_side)} "
                            f"(nearest {side} side) in the output, asked {_plain(want)}")
    if shading is not None:
        nearest = next((ppr.find(W + "shd") for ppr in reversed(levels)
                        if ppr is not None and ppr.find(W + "shd") is not None), None)
        if shading == "none":
            if _v_shd_shows(nearest):
                errs.append(f"{where}: shading {_plain(dict(nearest.attrib))} still shows in the output (asked none)")
        elif nearest is None or dict(nearest.attrib) != shading:
            errs.append(f"{where}: the shading is {_describe(nearest)} in the output, asked {_plain(shading)}")


def _direct(ctx: Ctx, a: Dict[str, Any], borders, shading, errs: List[str]) -> str:
    sel = a.get("select") or {}
    if set(sel) - {"scope", "paragraphs"}:
        raise ValueError(f"unknown selector key(s) {sorted(set(sel) - {'scope', 'paragraphs'})}")
    scope = sel.get("scope", ["document"])
    parts = ctx.scope_parts([scope] if isinstance(scope, str) else scope)
    if sorted(parts) != sorted(a.get("parts", [])):
        errs.append(f"borders_shading: scope resolves to {sorted(parts)} here, plan says {sorted(a.get('parts', []))}")
    params = a.get("params") or {}
    pred = sel.get("paragraphs", "all")
    tracked = bool(params["tracked"]) if "tracked" in params else pred not in (None, "all", {})
    author = a.get("author")
    changed: Dict[str, List[Tuple[int, Tuple[str, ...]]]] = {}
    for part in parts:
        if part not in ctx.both:
            errs.append(f"borders_shading: {part} missing from the output")
            continue
        sv, ov = ctx.view(part)
        for bi, p in enumerate(sv.pars):
            if not para_matches(pred, p, bi, sv, ctx.src_styles):
                continue
            where = f"{part} block {bi}"
            S = p.find(W + "pPr")
            gets = _v_gets(S, _v_below_paragraph(p, ctx.src_styles), borders, shading)
            oi = ctx.maps[part].get(bi)
            if gets:
                changed.setdefault(part, []).append((bi, tuple(sorted(gets))))
                if bi in ctx.text_changed.get(part, set()):
                    errs.append(f"borders_shading: {where} is selected but also has predicted text changes")
                    continue
                if oi is None:
                    errs.append(f"borders_shading: {where} has no counterpart in the output")
                    continue
                ctx.strip_ppr.setdefault(part, {}).setdefault(bi, set()).update(set(gets) | {"pPrChange"})
                O = ov.pars[oi].find(W + "pPr")
                _check_elements(S, O, gets, {"pPrChange", "rPr", "sectPr"}, where, errs)
                _check_ppr_change(S, O if O is not None else etree.Element(W + "pPr"), tracked, author, where, errs)
            if oi is None:
                continue
            o_p = ov.pars[oi]
            _check_shown(_v_below_paragraph(o_p, ctx.out_styles) + [o_p.find(W + "pPr")], borders, shading, where, errs)
    n = sum(len(v) for v in changed.values())
    if n != a.get("expected_paragraphs", -1):
        errs.append(f"borders_shading: {n} paragraph(s) change here, plan predicted {a.get('expected_paragraphs')}")
    planned = {k: sorted((int(bi), tuple(sorted(e))) for bi, e in v) for k, v in (a.get("changed") or {}).items() if v}
    mine = {k: sorted(v) for k, v in changed.items()}
    if mine != planned:
        errs.append(f"borders_shading: the paragraphs that change here {mine} are not the plan's {planned}")
    return f"borders_shading: {n} paragraph(s) → {params.get('borders', '—')} / {params.get('shading', '—')}" + \
        (" (tracked)" if tracked else "")


def _styles(ctx: Ctx, a: Dict[str, Any], borders, shading, errs: List[str]) -> str:
    if ctx.styles_part is None:
        errs.append("borders_shading: no common styles part")
        return "borders_shading: no styles part"
    params = a.get("params") or {}
    if params.get("tracked"):
        errs.append("borders_shading: style definitions are never tracked (the op must refuse)")
    ids = list((a.get("select") or {}).get("ids") or [])
    changed: Dict[str, Tuple[str, ...]] = {}
    for sid in ids:
        st = ctx.src_styles.by_id.get(sid)
        if st is None:
            errs.append(f"borders_shading: style {sid} is not in the source")
            continue
        if st.get(W + "type", "paragraph") != "paragraph":
            errs.append(f"borders_shading: {sid} is a {st.get(W + 'type')} style (the op must refuse)")
            continue
        o = ctx.out_styles.by_id.get(sid)
        if o is None:
            errs.append(f"borders_shading: style {sid} missing from the output")
            continue
        S = st.find(W + "pPr")
        gets = _v_gets(S, _v_below_style(st, ctx.src_styles), borders, shading)
        where = f"style {sid}"
        if gets:
            changed[sid] = tuple(sorted(gets))
            ctx.strip_style_ppr.setdefault(sid, set()).update(gets)
            _check_elements(S, o.find(W + "pPr"), gets, set(), where, errs)
        _check_shown(_v_below_style(o, ctx.out_styles) + [o.find(W + "pPr")], borders, shading, where, errs)
    if sorted(changed) != sorted(a.get("expected_ids", [])):
        errs.append(f"borders_shading: style(s) {sorted(changed)} change here, plan predicted "
                    f"{sorted(a.get('expected_ids', []))}")
    planned = {k: tuple(sorted(v)) for k, v in (a.get("changed") or {}).items()}
    if changed != planned:
        errs.append(f"borders_shading: the elements that change here {changed} are not the plan's {planned}")
    return f"borders_shading: style(s) {sorted(changed)} → {params.get('borders', '—')} / {params.get('shading', '—')}"


def _borders_shading(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    borders, shading = _v_asked(a.get("params") or {})
    target = a.get("target")
    if target == "direct":
        return _direct(ctx, a, borders, shading, errs)
    if target == "styles":
        return _styles(ctx, a, borders, shading, errs)
    raise ValueError(f"unknown target {target!r}")


BORDER_KINDS = {"borders_shading": _borders_shading}
