"""V10 kind ``list_level`` — set_list_level, verified (built-in batch 5, item 35 — docs/BUILTIN_OPS_BATCH5_DESIGN.md §3).

This module's own reading of the op's definitions (transform side: ops/set_list_level.py). Nothing here comes from ops/
(rule 1): the list definitions, a paragraph's numbering and the count are verify/lists.py's, the style resolution
verify/restyle.py's, CT_Lvl's child order is read from the bundled schema itself (``xsd.sequence_order``), and from the
user's own parameters (``_v_asked``: ``allow_missing`` true by default) this module re-derives

* the level (``_v_target``): the style (id, else the one name — whitespace collapsed, casefolded), its numbering by the
  style chain alone, ``level`` replacing the ilvl, the list's abstractNum;
* every refusal the op must have raised (a ValueError here): a style of another type, an unreadable or unsupported list,
  no such level in the abstractNum, an unsupported level, a FULL override level for it in any num of the abstractNum, a
  start or restart on an abstractNum that numbers counted paragraphs through more than one num, a restart after a level
  not below it, a letter or roman level starting below 1, a change after which a paragraph evaluated before is not;
* what changes (``_v_changes``: the format — the zero-pad rule: decimal ↔ decimalZero —, the start (default 0), the
  restart rule as Word reads it, written in its normal form: none, r ≥ 1 equal to the level (after the nearest higher
  level) or above it (ignored) is "after any higher level", no element) and the expected
  ``w:lvl`` (``_v_expected_lvl``: the source level with each changed element replaced by a new one carrying only
  ``w:val``, in the schema's order).

It fails when the derived change differs from the plan's (``changed``); when the output's numbering part is not EXACTLY
the source's with that one level replaced (the whole member compared — numbering.xml is under no residual — and registered
in ``verified_members``; with nothing to change: exactly the source's); and, post-condition, when the verifier's own
prediction of every counted paragraph (the source's main part counted over the expected definitions) differs from the
plan's ``predicted`` rows, when its count of the OUTPUT differs from that prediction, or — a format change alone — when a
counter moved.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from .reader import W
from .props import Ctx, first_difference, sig
from .restyle import v_resolve_style
from .lists import VListDefs, v_count, v_list_defs, v_style_numbering, V_NUMBERING_REL
from .xsd import sequence_order

V_FORMATS = ("decimal", "decimalZero", "upperLetter", "lowerLetter", "upperRoman", "lowerRoman")
V_FROM_ONE = {"upperLetter", "lowerLetter", "upperRoman", "lowerRoman"}
V_TAG = {"fmt": "numFmt", "start": "start", "restart": "lvlRestart"}
V_KNOWN = {"style", "level", "format", "zero_pad", "start", "restart", "allow_missing", "tracked"}


def _v_lvl_order() -> List[str]:
    return [etree.QName(t).localname for t in sequence_order("ISO-IEC29500-4_2016/wml.xsd", "CT_Lvl")]


def _v_whole_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _v_asked(params: Dict[str, Any]) -> Dict[str, Any]:
    """The user's words with this module's defaults; ValueError where the op must refuse (bad-params)."""
    if set(params) - V_KNOWN:
        raise ValueError(f"unknown parameter(s) {sorted(set(params) - V_KNOWN)} (the op must refuse: bad-params)")
    if params.get("tracked"):
        raise ValueError("list definitions are never tracked (the op must refuse: bad-params)")
    if not isinstance(params.get("style"), str) or not params["style"].strip():
        raise ValueError("no style named (the op must refuse: bad-params)")
    level = params.get("level")
    if level is not None and (not _v_whole_int(level) or level not in range(9)):
        raise ValueError(f"level {level!r} (the op must refuse: bad-params)")
    if not set(params) & {"format", "zero_pad", "start", "restart"}:
        raise ValueError("no change asked (the op must refuse: bad-params)")
    if "format" in params and params["format"] not in V_FORMATS:
        raise ValueError(f"format {params['format']!r} (the op must refuse: bad-params)")
    if "zero_pad" in params and ("format" in params or not isinstance(params["zero_pad"], bool)):
        raise ValueError("zero_pad must be a boolean, without format (the op must refuse: bad-params)")
    if "start" in params:
        s = params["start"]
        if not _v_whole_int(s) or s < 0 or (s == 0 and params.get("format") in V_FROM_ONE):
            raise ValueError(f"start {s!r} (the op must refuse: bad-params)")
    restart: Any = None
    if "restart" in params:
        r = params["restart"]
        if r == "after_higher":
            restart = "higher"
        elif r == "never":
            restart = 0
        elif isinstance(r, dict) and list(r) == ["after_level"] and _v_whole_int(r["after_level"]) \
                and r["after_level"] in range(8) and (level is None or r["after_level"] < level):
            restart = r["after_level"] + 1
        else:
            raise ValueError(f"restart {r!r} (the op must refuse: bad-params)")
    allow = params.get("allow_missing", True)
    if not isinstance(allow, bool):
        raise ValueError("allow_missing must be a boolean (the op must refuse: bad-params)")
    return {"style": params["style"], "level": level, "format": params.get("format"),
            "zero_pad": params.get("zero_pad"), "start": params.get("start"), "restart": restart,
            "allow_missing": allow}


def _v_target(ctx: Ctx, defs: VListDefs, asked: Dict[str, Any]) -> Tuple[Optional[Tuple[str, int, int, int]], str]:
    """((style id, numId, ilvl, abstractNumId), "") or (None, why not — the op's not-found); ValueError: must refuse."""
    styles = ctx.src_styles
    sid = v_resolve_style(styles, asked["style"])
    if sid is None:
        return None, f"no style {asked['style']!r}"
    if (styles.by_id[sid].get(W + "type") or "paragraph") != "paragraph":
        raise ValueError(f"{sid} is not a paragraph style (the op must refuse: style-mismatch)")
    nb = v_style_numbering(sid, styles, defs)
    if nb is None or nb.dangling:
        return None, f"style {sid} is not numbered by a list this file defines"
    if nb.unreadable:
        raise ValueError(f"style {sid}: {nb.unreadable} (the op must refuse: numbering-unsupported)")
    trouble = defs.problem(nb.num_id)
    if trouble is not None:
        raise ValueError(f"{trouble[1]} (the op must refuse: numbering-unsupported)")
    ilvl = nb.ilvl if asked["level"] is None else asked["level"]
    return (sid, nb.num_id, ilvl, defs.abstract_of(nb.num_id)), ""


def _v_in_effect(restart: Optional[int], ilvl: int) -> Optional[int]:
    """The restart rule as Word reads it, normal form: a value 1 ≤ r below the level restarts after level r − 1 only; 0
    never; none, r equal to the level (after the nearest higher level = after any) and r above it (ignored) → None."""
    if restart is not None and 1 <= restart < ilvl:
        return restart
    return 0 if restart == 0 else None


def _v_changes(lv, asked: Dict[str, Any], ilvl: int) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if asked["format"] is not None and asked["format"] != lv.fmt:
        out["fmt"] = asked["format"]
    if asked["zero_pad"] is True and lv.fmt == "decimal":
        out["fmt"] = "decimalZero"
    if asked["zero_pad"] is False and lv.fmt == "decimalZero":
        out["fmt"] = "decimal"
    if asked["start"] is not None and asked["start"] != lv.start:
        out["start"] = asked["start"]
    if asked["restart"] is not None:
        want = _v_in_effect(None if asked["restart"] == "higher" else asked["restart"], ilvl)
        if want != _v_in_effect(lv.restart, ilvl):
            out["restart"] = want
    return out


def _v_level_el(root, aid: int, ilvl: int):
    def num(v):
        try:
            return int(v) if v is not None and v.lstrip("-").isdigit() else None
        except ValueError:
            return None
    hits = [lvl for a in root.findall(W + "abstractNum") if num(a.get(W + "abstractNumId")) == aid
            for lvl in a.findall(W + "lvl") if num(lvl.get(W + "ilvl")) == ilvl]
    return hits[0] if len(hits) == 1 else None


def _v_expected_lvl(lvl, changes: Dict[str, Any]):
    order = _v_lvl_order()
    rank = {name: k for k, name in enumerate(order)}
    out = copy.deepcopy(lvl)
    for key, tag in V_TAG.items():
        if key not in changes:
            continue
        if len(out.findall(W + tag)) > 1:
            raise ValueError(f"the level carries w:{tag} more than once (the op must refuse: structure)")
        for e in out.findall(W + tag):
            out.remove(e)
        if changes[key] is None:
            continue
        new = etree.Element(W + tag)
        new.set(W + "val", str(changes[key]))
        kids = list(out)
        at = next((i for i, c in enumerate(kids) if rank.get(etree.QName(c).localname if isinstance(c.tag, str)
                                                             else "", len(order)) > rank[tag]), len(kids))
        out.insert(at, new)
    return out


def _v_rows(rows) -> List[List[Any]]:
    return [[r.index, r.num_id, r.ilvl, list(r.counters) if r.counters is not None else None, r.text, r.issue]
            for r in rows]


def _v_compare_numbering(ctx: Ctx, want, o_part: str, tag: str, errs: List[str]) -> None:
    got = ctx.out.xml(o_part)
    if sig(want) != sig(got):
        errs.append(f"{tag}: {o_part} is not the source's with exactly the asked level change: "
                    f"{first_difference(want, got)}")
    ctx.verified_members.add(o_part)


def _list_level(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    tag = "list_level"
    asked = _v_asked(a.get("params") or {})
    s_part, o_part = ctx.src.related(V_NUMBERING_REL), ctx.out.related(V_NUMBERING_REL)
    if s_part != o_part or s_part != a.get("numbering_part"):
        errs.append(f"{tag}: numbering part {s_part!r} → {o_part!r} (plan: {a.get('numbering_part')!r})")
        return f"{tag}: numbering part mismatch"
    defs = v_list_defs(ctx.src)
    target, why = _v_target(ctx, defs, asked)
    planned = sorted(a.get("changed") or [])
    if target is None:
        if not asked["allow_missing"]:
            errs.append(f"{tag}: {why} (the op must refuse: not-found)")
        if planned:
            errs.append(f"{tag}: {why} here, but the plan changes {planned}")
        if s_part is not None:
            _v_compare_numbering(ctx, ctx.src.xml(s_part), o_part, tag, errs)
        return f"{tag}: {why}; nothing changes"
    sid, nid, ilvl, aid = target
    if (sid, nid, ilvl, aid) != (a.get("style_id"), a.get("num_id"), a.get("ilvl"), a.get("abstract_id")):
        errs.append(f"{tag}: the level is style {sid} → list {nid} level {ilvl} (abstractNum {aid}) here, the plan "
                    f"says style {a.get('style_id')} → list {a.get('num_id')} level {a.get('ilvl')} "
                    f"(abstractNum {a.get('abstract_id')})")
    where = f"{tag}: level {ilvl} of list {nid}"
    over = [n for n in defs.nums_of_abstract(aid) if defs.is_override(n, ilvl)]
    if over:
        raise ValueError(f"lists {over} override level {ilvl} with a full level (the op must refuse: "
                         f"numbering-override)")
    lv = defs.abstracts[aid]["levels"].get(ilvl)
    if lv is None or lv.unsupported is not None:
        raise ValueError(f"level {ilvl} of abstractNum {aid} is undefined or unsupported (the op must refuse: "
                         f"numbering-unsupported)")
    main = ctx.src.main_part()
    s_main, o_main = ctx.src.xml(main), ctx.out.xml(main)
    before = v_count(s_main, ctx.src_styles, defs)
    users = {r.num_id for r in before if r.abstract_id == aid and r.num_id is not None}
    if (asked["start"] is not None or asked["restart"] is not None) and len(users) > 1:
        raise ValueError(f"abstractNum {aid} numbers paragraphs through lists {sorted(users)} (the op must refuse: "
                         f"numbering-unsupported)")
    if asked["restart"] not in (None, "higher", 0) and asked["restart"] - 1 >= ilvl:
        raise ValueError(f"a restart after level {asked['restart'] - 1} at level {ilvl} (the op must refuse: "
                         f"list-level)")
    changes = _v_changes(lv, asked, ilvl)
    if changes.get("fmt", lv.fmt) in V_FROM_ONE and changes.get("start", lv.start) < 1:
        raise ValueError(f"a {changes.get('fmt', lv.fmt)} level starting at {changes.get('start', lv.start)} (the op "
                         f"must refuse: list-level)")
    mine = sorted(V_TAG[k] for k in changes)
    if mine != planned:
        errs.append(f"{where}: the elements that change here {mine} are not the plan's {planned}")
    s_root = ctx.src.xml(s_part)
    if not changes:
        _v_compare_numbering(ctx, s_root, o_part, tag, errs)
        return f"{where}: already as asked; numbering unchanged"
    lvl = _v_level_el(s_root, aid, ilvl)
    if lvl is None:
        raise ValueError(f"abstractNum {aid} level {ilvl} not found once in {s_part}")
    want = copy.deepcopy(s_root)
    old = _v_level_el(want, aid, ilvl)
    old.getparent().replace(old, _v_expected_lvl(lvl, changes))
    _v_compare_numbering(ctx, want, o_part, tag, errs)

    # ---- the numbers: the verifier's own prediction, the plan's, the output's ----
    expect = v_count(s_main, ctx.src_styles, VListDefs(want))
    lost = [r for r in expect if r.not_evaluated is not None
            and any(b.index == r.index and b.not_evaluated is None for b in before)]
    if lost:
        raise ValueError(f"{len(lost)} paragraph(s) evaluated before are not after the change (the op must refuse: "
                         f"list-level)")
    own = _v_rows(expect)
    if own != (a.get("predicted") or []):
        diff = next(((x, y) for x, y in zip(own, a.get("predicted") or []) if x != y), None)
        errs.append(f"{where}: the plan's predicted numbers are not the verifier's — first difference {diff}"
                    + (f" (counts {len(own)} / {len(a.get('predicted') or [])})" if diff is None else ""))
    out_rows = _v_rows(v_count(o_main, ctx.out_styles, v_list_defs(ctx.out)))
    if out_rows != own:
        diff = next(((x, y) for x, y in zip(own, out_rows) if x != y), None)
        errs.append(f"{where}: the output's numbers are not the predicted ones — first difference (predicted, output) "
                    f"{diff}" + (f" (counts {len(own)} / {len(out_rows)})" if diff is None else ""))
    if "start" not in changes and "restart" not in changes:
        was = {r.index: r.counters for r in before}
        moved = [r.index for r in expect if r.index in was and r.counters != was[r.index]]
        if moved:
            errs.append(f"{where}: a format change moved the counters of paragraph(s) {moved[:5]}")
    shown_before = {b.index: b.text for b in before}
    n = sum(1 for x in own if x[0] in shown_before and shown_before[x[0]] != x[4])
    return f"{where}: {mine} changed; {n} number(s) shown differently, every counted paragraph recounted"


LIST_LEVEL_KINDS = {"list_level": _list_level}
