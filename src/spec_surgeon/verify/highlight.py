"""V10 kind ``removed_highlight`` — built-in batch 2, verifier side (docs/BUILTIN_OPS_BATCH2_DESIGN.md §3), independent of
ops/highlight.py (rule 1).

The targets, re-selected from the SOURCE with this module's own reading: every run of every paragraph in scope (the
part's runs by nearest paragraph — ``PartView.runs``; deleted runs included) and every paragraph mark whose own
``w:rPr`` has a ``w:highlight`` child whose ``w:val`` is one of the asserted colours. For each: the output ``w:rPr`` must
be the source's with that highlight gone and every other child as it was (an rPr left empty may be gone); the change
marker as tracked/plain demand (``props._check_rpr_change``: plain — unchanged; tracked — a new ``w:rPrChange`` ending
the rPr whose snapshot is the source rPr). Those rPr are then stripped from the residual (``strip_runs``/``strip_marks``),
which proves everything else in the part unchanged. Counts must equal the plan's.
"""
from __future__ import annotations

from typing import Any, Dict, List

from lxml import etree

from .reader import W
from .props import Ctx, _check_rpr_change, sig

_V_COLORS = {"yellow", "green", "cyan", "magenta", "blue", "red", "darkBlue", "darkCyan", "darkGreen", "darkMagenta",
             "darkRed", "darkYellow", "darkGray", "lightGray", "black", "white"}
_V_DEFAULT_SCOPE = ["document", "headers", "footers", "footnotes", "endnotes"]


def _v_colors(asked) -> set:
    """The colours the user asked for, read here: none asked → every colour; else Word's names, any case."""
    if asked is None:
        return set(_V_COLORS)
    fold = {c.casefold(): c for c in _V_COLORS}
    if not isinstance(asked, list) or not asked or any(not isinstance(c, str) or c.strip().casefold() not in fold
                                                       for c in asked):
        raise ValueError(f"{asked!r} is not a list of Word highlight colours — the op must refuse")
    return {fold[c.strip().casefold()] for c in asked}


def _v_lit(rpr, colors) -> bool:
    if rpr is None:
        return False
    h = rpr.find(W + "highlight")
    return h is not None and h.get(W + "val") in colors


def _v_check(S, O, tracked: bool, author, where: str, errs: List[str]) -> None:
    """``O`` is ``S`` without its highlight (every other child as it was, in order); the rPrChange per tracked/plain."""
    want = [sig(c) for c in S if isinstance(c.tag, str) and c.tag not in (W + "highlight", W + "rPrChange")]
    got = [sig(c) for c in (O if O is not None else []) if isinstance(c.tag, str) and c.tag != W + "rPrChange"]
    if O is not None and O.find(W + "highlight") is not None:
        errs.append(f"{where}: still highlighted")
    elif want != got:
        errs.append(f"{where}: run properties other than the highlight changed")
    if O is not None and dict(O.attrib) != dict(S.attrib):
        errs.append(f"{where}: the w:rPr's attributes changed")
    _check_rpr_change(S, O if O is not None else etree.Element(W + "rPr"), tracked, author, where, errs)


def _removed_highlight(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    colors = _v_colors(a.get("asked_colors"))
    if colors != set(a.get("colors") or []):
        errs.append(f"removed_highlight: the asked colours are {sorted(colors)} here, the plan read "
                    f"{sorted(a.get('colors') or [])}")
    tracked, author = bool(a.get("tracked", False)), a.get("author")
    parts = ctx.scope_parts(a.get("asked_scope") or _V_DEFAULT_SCOPE)
    if sorted(parts) != sorted(a.get("parts") or []):
        errs.append(f"removed_highlight: the scope resolves to {sorted(parts)} here, the plan says {a.get('parts')}")
    n_runs = n_marks = 0
    for part in parts:
        if part not in ctx.both:
            errs.append(f"removed_highlight: {part} is missing from the output")
            continue
        sv, ov = ctx.view(part)
        amap = ctx.maps[part]
        for bi, p in enumerate(sv.pars):
            runs = [ri for ri, r in enumerate(sv.runs[bi]) if _v_lit(r.find(W + "rPr"), colors)]
            mrpr = p.find(W + "pPr/" + W + "rPr")
            mark = _v_lit(mrpr, colors)
            if not runs and not mark:
                continue
            if bi in ctx.text_changed.get(part, set()):
                errs.append(f"removed_highlight: {part} block {bi} also has predicted text changes")
                continue
            oi = amap.get(bi)
            if oi is None or len(ov.runs[oi]) != len(sv.runs[bi]):
                errs.append(f"removed_highlight: {part} block {bi}: the run structure changed")
                continue
            for ri in runs:
                n_runs += 1
                ctx.strip_runs.setdefault(part, set()).add((bi, ri))
                _v_check(sv.runs[bi][ri].find(W + "rPr"), ov.runs[oi][ri].find(W + "rPr"), tracked, author,
                         f"removed_highlight {part} block {bi} run {ri}", errs)
            if mark:
                n_marks += 1
                ctx.strip_marks.setdefault(part, set()).add(bi)
                _v_check(mrpr, ov.pars[oi].find(W + "pPr/" + W + "rPr"), tracked, author,
                         f"removed_highlight {part} block {bi} paragraph mark", errs)
    if n_runs != a.get("expected_runs", -1):
        errs.append(f"removed_highlight: {n_runs} highlighted run(s) here, the plan predicted {a.get('expected_runs')}")
    if n_marks != a.get("expected_marks", -1):
        errs.append(f"removed_highlight: {n_marks} highlighted paragraph mark(s) here, the plan predicted "
                    f"{a.get('expected_marks')}")
    return (f"removed_highlight: {n_runs} run(s), {n_marks} paragraph mark(s) "
            f"({'tracked' if tracked else 'plain'}; colours {sorted(colors)})")


HIGHLIGHT_KINDS: Dict[str, Any] = {"removed_highlight": _removed_highlight}
