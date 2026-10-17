"""V10 kind ``language`` — set_language, verified (built-in batch 4, item 29 — docs/BUILTIN_OPS_BATCH4_DESIGN.md §4).

This module's own reading of the op's definitions (transform side: ops/set_language.py; CLAUDE.md lists them among the
definitions that must stay identical on both sides). Nothing here comes from ops/ (rule 1):

* the asked attributes and ``replace_only`` are read from the user's own parameters (``_v_asked``: language → w:val,
  east_asian → w:eastAsia, complex_script → w:bidi; the tag pattern; replace_only casefolded; ``tracked: true`` is what
  the op must refuse);
* the levels (``_v_levels``: docDefaults' rPrDefault/rPr, every style's own rPr — keyed by w:styleId, the last of a
  repeated id —, every run's and paragraph mark's own rPr in every text part) and what each gets (``_v_gets``: an own
  w:lang carrying the attribute with another value, listed with replace_only; docDefaults without replace_only also
  when absent) are re-derived from the SOURCE;
* the **effective language** (``v_effective_lang``) — per attribute, the first of direct → character style chain
  (rStyle, else the default character style) → paragraph style chain (pStyle, else the default) → table style chain
  (the table's tblStyle, else the default table style) → docDefaults whose w:lang carries the attribute; else none —
  exists only here (the op never needs it).

Fails when the changed levels (and the attributes each gets) differ from the plan's; when a changed level's output rPr is
not the source's with exactly the asked attributes set on its w:lang (the lang's other attributes kept; a created lang —
docDefaults only — in schema order; a created docDefaults first in w:styles, a created rPrDefault before pPrDefault) and
every other child identical, in order; and — a post-condition on the OUTPUT — when a run or mark of any text part does
not resolve to the asked value (replace_only: when one that resolved to a listed value in the source does not resolve to
it, or any resolves to a listed value other than it). The checked rPr leave the residual: runs (``strip_runs``), marks
(``strip_marks``), styles (``strip_styles``), docDefaults (``strip_doc_defaults``).
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from lxml import etree

from .reader import W
from .props import Ctx, _local, _pstyle, _rstyle, _v_table_style_id, sig

V_LANG_PARAMS = {"language": "val", "east_asian": "eastAsia", "complex_script": "bidi"}
V_TAG = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8})*")
# CT_RPr children in schema order (this module's own table): where a created w:lang goes
V_RPR_ORDER = ("ins", "del", "moveFrom", "moveTo", "rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps",
               "strike", "dstrike", "outline", "shadow", "emboss", "imprint", "noProof", "snapToGrid", "vanish",
               "webHidden", "color", "spacing", "w", "kern", "position", "sz", "szCs", "highlight", "u", "effect", "bdr",
               "shd", "fitText", "vertAlign", "rtl", "cs", "em", "lang", "eastAsianLayout", "specVanish", "oMath",
               "rPrChange")


def _v_asked(params: Dict[str, Any]) -> Tuple[Dict[str, str], Optional[Set[str]]]:
    if params.get("tracked"):
        raise ValueError("a language change is never tracked (the op must refuse tracked: true)")
    asked: Dict[str, str] = {}
    for k, attr in V_LANG_PARAMS.items():
        v = params.get(k)
        if v is None:
            continue
        if not isinstance(v, str) or not V_TAG.fullmatch(v):
            raise ValueError(f"{k} {v!r} is not a language tag (the op must refuse)")
        asked[attr] = v
    if not asked:
        raise ValueError("nothing asked")
    listed = None
    if "replace_only" in params:
        ro = params["replace_only"]
        if not isinstance(ro, list) or not ro or not all(isinstance(x, str) and V_TAG.fullmatch(x) for x in ro):
            raise ValueError(f"replace_only {ro!r} is not a list of language tags (the op must refuse)")
        listed = {x.casefold() for x in ro}
    return asked, listed


def _v_lang_of(rpr):
    return rpr.find(W + "lang") if rpr is not None else None


def _v_gets(rpr, asked: Dict[str, str], listed: Optional[Set[str]], doc_defaults: bool = False) -> Dict[str, str]:
    lang = _v_lang_of(rpr)
    out = {}
    for attr, v in asked.items():
        cur = lang.get(W + attr) if lang is not None else None
        if cur is None:
            if doc_defaults and listed is None:
                out[attr] = v
        elif cur != v and (listed is None or cur.casefold() in listed):
            out[attr] = v
    return out


def _v_dd_rpr(styles_root):
    if styles_root is None:
        return None
    return styles_root.find(W + "docDefaults/" + W + "rPrDefault/" + W + "rPr")


def _v_levels(ctx: Ctx):
    """("docDefaults", rpr), ("style", id, rpr), ("run", part, block, run, rpr), ("mark", part, block, rpr) of the
    SOURCE."""
    out: List[tuple] = [("docDefaults", _v_dd_rpr(ctx.src_styles_root))]
    for sid, st in ctx.src_styles.by_id.items():
        out.append(("style", sid, st.find(W + "rPr")))
    for part in ctx.src_tp:
        sv = ctx.view(part)[0] if part in ctx.both else None
        if sv is None:
            continue
        for bi, p in enumerate(sv.pars):
            for ri, r in enumerate(sv.runs[bi]):
                out.append(("run", part, bi, ri, r.find(W + "rPr")))
            out.append(("mark", part, bi, p.find(W + "pPr/" + W + "rPr")))
    return out


def v_effective_lang(rpr, p, styles, attr: str) -> Optional[str]:
    """The language attribute ``attr`` (val, eastAsia, bidi) a run or mark resolves to: the first level, nearest first,
    whose w:lang carries it."""
    levels = [rpr]
    for sid in (_rstyle(rpr) or styles.default.get("character"), _pstyle(p) or styles.default.get("paragraph"),
                _v_table_style_id(p, styles)):
        levels += [st.find(W + "rPr") for st in styles.chain(sid)]
    levels.append(styles.doc_defaults_rpr())
    for L in levels:
        lang = _v_lang_of(L)
        if lang is not None and lang.get(W + attr) is not None:
            return lang.get(W + attr)
    return None


def _v_rank(el) -> float:
    loc = _local(el)
    if not loc:
        return len(V_RPR_ORDER) - 1.5          # a foreign element: after every w: child but the change marker
    return V_RPR_ORDER.index(loc) if loc in V_RPR_ORDER else len(V_RPR_ORDER) - 1.5


def _check_lang_rpr(S, O, gets: Dict[str, str], where: str, errs: List[str]) -> None:
    """The output rPr ``O`` = the source rPr ``S`` with exactly ``gets`` set on its w:lang (created in schema order when
    absent), every other child identical, in order."""
    if O is None:
        errs.append(f"{where}: no rPr in the output")
        return
    s_kids = list(S) if S is not None else []
    s_lang = _v_lang_of(S)
    want = copy.deepcopy(s_lang) if s_lang is not None else etree.Element(W + "lang")
    for attr, v in gets.items():
        want.set(W + attr, v)
    if s_lang is not None:
        exp = [sig(want) if c is s_lang else sig(c) for c in s_kids]
    else:
        rank = V_RPR_ORDER.index("lang")
        at = next((i for i, c in enumerate(s_kids) if _v_rank(c) > rank), len(s_kids))
        exp = [sig(c) for c in s_kids[:at]] + [sig(want)] + [sig(c) for c in s_kids[at:]]
    got = [sig(c) for c in O]
    if got != exp:
        o_lang = _v_lang_of(O)
        if o_lang is not None and sig(o_lang) != sig(want):
            errs.append(f"{where}: w:lang is {dict((etree.QName(k).localname, v) for k, v in o_lang.attrib.items())}, "
                        f"expected {dict((etree.QName(k).localname, v) for k, v in want.attrib.items())}")
        else:
            errs.append(f"{where}: run properties other than w:lang changed (or w:lang out of place): "
                        f"{[_local(c) or str(c.tag) for c in O]} vs {[_local(c) or str(c.tag) for c in s_kids]}")
    if dict(O.attrib) != (dict(S.attrib) if S is not None else {}):
        errs.append(f"{where}: rPr attributes changed")


def _v_elements(parent) -> List[Any]:
    return [c for c in parent if isinstance(c.tag, str)] if parent is not None else []


def _check_doc_defaults_frame(ctx: Ctx, errs: List[str]) -> None:
    """The containers around docDefaults' rPr in their places: w:docDefaults where the source had it (created: the first
    element of w:styles), w:rPrDefault where the source had it (created: before w:pPrDefault), holding what the source's
    held besides its rPr (created: only its rPr); attributes unchanged. The residual compares everything else of
    docDefaults but cannot see a container it drops as empty."""
    s_root, o_root = ctx.src_styles_root, ctx.out_styles_root
    s_dd, o_dd = s_root.find(W + "docDefaults"), o_root.find(W + "docDefaults")
    if o_dd is None:
        errs.append("docDefaults: missing from the output")
        return
    at = _v_elements(s_root).index(s_dd) if s_dd is not None else 0
    if _v_elements(o_root).index(o_dd) != at:
        errs.append(f"docDefaults: not element {at} of w:styles in the output (the source's place; created: the first "
                    f"element, schema order)")
    if dict(o_dd.attrib) != (dict(s_dd.attrib) if s_dd is not None else {}):
        errs.append("docDefaults: attributes changed")
    s_rpd = s_dd.find(W + "rPrDefault") if s_dd is not None else None
    o_rpd = o_dd.find(W + "rPrDefault")
    at = _v_elements(s_dd).index(s_rpd) if s_rpd is not None else 0
    if o_rpd is None or _v_elements(o_dd).index(o_rpd) != at:
        errs.append(f"docDefaults: w:rPrDefault missing or not element {at} of w:docDefaults in the output (the "
                    f"source's place; created: before w:pPrDefault)")
        return
    if dict(o_rpd.attrib) != (dict(s_rpd.attrib) if s_rpd is not None else {}):
        errs.append("docDefaults: w:rPrDefault attributes changed")

    def shape(rpd):
        return [("rPr",) if _local(c) == "rPr" else sig(c) for c in _v_elements(rpd)]

    if s_rpd is None:
        want = [("rPr",)]                                  # created: only its rPr
    elif s_rpd.find(W + "rPr") is None:
        want = shape(s_rpd) + [("rPr",)]                   # the rPr appended to the source's rPrDefault
    else:
        want = shape(s_rpd)
    if shape(o_rpd) != want:
        errs.append("docDefaults: w:rPrDefault holds something else than the source's (created: only its w:rPr)")


def _key(level) -> tuple:
    return tuple(level[:-1])


def _language(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    params = a.get("params") or {}
    asked, listed = _v_asked(params)
    if ctx.styles_part is None:
        errs.append("language: no common styles part (the op must refuse a file without one)")
        return "language: no styles part"
    exp = a.get("expected") or {}
    planned: Dict[tuple, Dict[str, str]] = {}
    if exp.get("doc_defaults"):
        planned[("docDefaults",)] = dict(exp["doc_defaults"])
    for sid, g in (exp.get("styles") or {}).items():
        planned[("style", sid)] = dict(g)
    for part, t in (exp.get("parts") or {}).items():
        for bi, ri, g in t.get("runs", []):
            planned[("run", part, int(bi), int(ri))] = dict(g)
        for bi, g in t.get("marks", []):
            planned[("mark", part, int(bi))] = dict(g)
    mine: Dict[tuple, Dict[str, str]] = {}
    n_runs = n_marks = 0
    for level in _v_levels(ctx):
        kind, rpr = level[0], level[-1]
        gets = _v_gets(rpr, asked, listed, doc_defaults=(kind == "docDefaults"))
        if not gets:
            continue
        mine[_key(level)] = gets
        if kind == "docDefaults":
            ctx.strip_doc_defaults = True
            _check_lang_rpr(rpr, _v_dd_rpr(ctx.out_styles_root), gets, "docDefaults/rPrDefault", errs)
            _check_doc_defaults_frame(ctx, errs)
        elif kind == "style":
            sid = level[1]
            o = ctx.out_styles.by_id.get(sid)
            if o is None:
                errs.append(f"language: style {sid} missing from the output")
                continue
            ctx.strip_styles.add(sid)
            _check_lang_rpr(rpr, o.find(W + "rPr"), gets, f"style {sid}", errs)
        else:
            part, bi = level[1], level[2]
            where = f"{part} block {bi}" + (f" run {level[3]}" if kind == "run" else " paragraph mark")
            if bi in ctx.text_changed.get(part, set()):
                errs.append(f"language: {where} changes language but also has predicted text changes")
                continue
            sv, ov = ctx.view(part)
            oi = ctx.maps[part].get(bi)
            if oi is None or len(ov.runs[oi]) != len(sv.runs[bi]):
                errs.append(f"language: {where}: run structure changed")
                continue
            if kind == "run":
                n_runs += 1
                ctx.strip_runs.setdefault(part, set()).add((bi, level[3]))
                _check_lang_rpr(rpr, ov.runs[oi][level[3]].find(W + "rPr"), gets, where, errs)
            else:
                n_marks += 1
                ctx.strip_marks.setdefault(part, set()).add(bi)
                _check_lang_rpr(rpr, ov.pars[oi].find(W + "pPr/" + W + "rPr"), gets, where, errs)
    if mine != planned:
        extra = sorted(str(k) for k in set(mine) - set(planned))[:5]
        missing = sorted(str(k) for k in set(planned) - set(mine))[:5]
        differ = sorted(str(k) for k in set(mine) & set(planned) if mine[k] != planned[k])[:5]
        errs.append(f"language: the levels that change here are not the plan's — here only {extra}, plan only "
                    f"{missing}, other attributes {differ}")
    for n, key, what in ((n_runs, "expected_runs", "run(s)"), (n_marks, "expected_marks", "paragraph mark(s)")):
        if n != a.get(key, -1):
            errs.append(f"language: {n} {what} change here, plan predicted {a.get(key)}")
    # post-condition: every run and mark of every text part of the OUTPUT resolves to the asked language
    bad = 0
    others = None if listed is None else {attr: listed - {v.casefold()} for attr, v in asked.items()}
    for part in ctx.both:
        sv, ov = ctx.view(part)
        amap = ctx.maps[part]
        back = {oi: bi for bi, oi in amap.items()}
        for oi, op in enumerate(ov.pars):
            holders = [(r.find(W + "rPr"), f"run {k}", k) for k, r in enumerate(ov.runs[oi])]
            holders.append((op.find(W + "pPr/" + W + "rPr"), "paragraph mark", None))
            bi = back.get(oi)
            same_runs = bi is not None and len(sv.runs[bi]) == len(ov.runs[oi])
            for rpr, label, k in holders:
                for attr, v in asked.items():
                    got = v_effective_lang(rpr, op, ctx.out_styles, attr)
                    problem = None
                    if listed is None:
                        if got != v:
                            problem = f"resolves to {got!r}, asked {v!r}"
                    else:
                        if got is not None and got.casefold() in others[attr]:
                            problem = f"still resolves to {got!r} (listed in replace_only)"
                        elif bi is not None and (k is None or same_runs):
                            s_rpr = (sv.runs[bi][k].find(W + "rPr") if k is not None
                                     else sv.pars[bi].find(W + "pPr/" + W + "rPr"))
                            was = v_effective_lang(s_rpr, sv.pars[bi], ctx.src_styles, attr)
                            if was is not None and was.casefold() in listed and got != v:
                                problem = f"resolved to {was!r} in the source and resolves to {got!r} now, asked {v!r}"
                    if problem:
                        bad += 1
                        if bad <= 20:
                            errs.append(f"language: {part} block {oi} {label}: w:lang {attr} {problem}")
    if bad > 20:
        errs.append(f"language: … {bad - 20} more run(s)/mark(s) not in the asked language")
    what = ", ".join(f"{attr} {v}" for attr, v in asked.items())
    return (f"language: {n_runs} run(s), {n_marks} mark(s), "
            f"{sum(1 for k in mine if k[0] == 'style')} style(s)"
            + (" + docDefaults" if ("docDefaults",) in mine else "") + f" → {what}"
            + (f" (replacing only {sorted(listed)})" if listed is not None else ""))


LANGUAGE_KINDS = {"language": _language}
