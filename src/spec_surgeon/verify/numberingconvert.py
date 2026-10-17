"""V10 kind ``converted_numbering`` and the destination of a numbering conversion — verified (built-in batch 5, items 33
and 34 — docs/BUILTIN_OPS_BATCH5_DESIGN.md §0.3, §1).

This module's own reading of the definitions (transform side: ops/numbering_convert.py and ops/convert_numbering.py).
Nothing here comes from ops/ (rule 1): the list model is verify/lists.py, the style import verify/styleimport.py, the
clearing rules verify/clearing.py, the style resolution verify/restyle.py.

**The destination** (``v_destination``, shared with ``typed_numbers``), from the user's own parameters (``levels``,
``template``, ``existing`` — ``keep`` by default, only with a template):

* a template given: re-read and md5-pinned (``template._template``); ``v_resolve_scheme`` over ITS styles and lists
  (ValueError: the op must refuse, ``bad-params``) and its list a clean destination (``v_list_is_clean`` with no
  paragraphs); each level's style in the planned output — with ``keep`` the SOURCE's own when the reference resolves
  there (``v_resolve_style``), else the template's level style (requested); with ``replace`` always the template's;
* the import those requested styles need (``styleimport.v_derive_import``: closure, new list ids above every id the
  source defines or refers to, the expected copies) — and the op's ``styles_imported`` assertion must carry exactly
  those (``styleimport.check_requested``, which also compares the numbering part whole when nothing is imported);
* the planned output's styles (a copy of the source's styles part with the replaced copies in their places and the
  imported ones appended) and lists (the source's ``VListDefs`` with the copied abstractNum/num); ``v_resolve_scheme``
  over them with each level's style id (ValueError: the op must refuse, ``scheme``); the destination list clean against
  the SOURCE count (``v_list_is_clean``; ValueError: ``numbering-unsupported``).

**converted_numbering** re-derives from the SOURCE the converted paragraphs (``from: "main"`` — the counted paragraphs
numbered by any num of ``v_main_abstract``, at their ilvl —, or ``from.styles`` — the counted, numbered paragraphs whose
style is in level k's list, at k), the refusals the op must raise (not evaluated, an own numPr with only an ilvl, a
tracked change in it, a level beyond the scheme after ``level_map``, a converted or formerly evaluated number the
prediction cannot evaluate), each level's style, and each paragraph's change (restyled, own numPr removed, with
``clear`` clear_direct_formatting's DEFAULT targets). It predicts every counted paragraph's number with its OWN model
(``v_count`` over the planned output with the converted paragraphs given the destination list at their levels), which
must equal the plan's; its own count of the OUTPUT must equal both (the paragraphs as many as the source's). Each
changed paragraph's pPr = the source's with ``w:pStyle`` set first (restyled), ``w:numPr`` gone (own) and the cleared
children gone, the ``w:pPrChange`` by the shared rules (``clearing.check_cleared_ppr``); cleared runs and marks exactly
(``clearing.check_runs_and_mark``). It strips ``pStyle``, ``pPrChange``, ``numPr`` (own) and the cleared names of the
changed paragraphs (``strip_ppr``) after comparing the whole pPr.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Set, Tuple

from .reader import STYLES_REL, W
from .props import Ctx, Styles
from .clearing import check_cleared_ppr, check_runs_and_mark, v_cleared_sets, v_decide, v_expected_ppr, v_with_pstyle
from .lists import (V_OFF, VListDefs, VNumbered, VScheme, _v_int, v_count, v_list_defs, v_list_is_clean,
                    v_main_abstract, v_paragraph_style, v_resolve_scheme, v_visible_line)
from .restyle import v_resolve_style
from .styleimport import NUMBERING_REL, check_requested, v_derive_import, v_used_ids

V_NUMPR_REVISIONS = {W + "ins", W + "del", W + "moveFrom", W + "moveTo", W + "numberingChange"}


class VDest(NamedTuple):
    scheme: VScheme
    sids: List[str]
    styles: Styles               # the planned output's styles
    defs: VListDefs              # the planned output's lists
    requested: List[str]
    template: Optional[str]


def _v_scheme_params(params: Dict[str, Any]) -> Tuple[Any, Optional[str], str]:
    levels = params.get("levels")
    if not isinstance(levels, list) or len(levels) not in range(1, 10) or \
            any(not isinstance(x, str) or not x.strip() for x in levels) or len(set(levels)) != len(levels):
        raise ValueError(f"levels {levels!r} (the op must refuse: bad-params)")
    template = params.get("template")
    existing = params.get("existing", "keep")
    if existing not in ("keep", "replace") or ("existing" in params and template is None):
        raise ValueError(f"existing {existing!r} (the op must refuse: bad-params)")
    return levels, template, existing


def _v_levels_shown(numbering_root, abs_id: Optional[int], n: int) -> Optional[str]:
    """Why a level 0…n−1 of that abstractNum would not show its number as rendered: its ``w:lvl/w:rPr`` turns on
    vanish, caps or smallCaps (present, w:val not 0/false/off)."""
    if numbering_root is None:
        return None
    for a in numbering_root:
        if a.tag != W + "abstractNum" or _v_int(a.get(W + "abstractNumId")) != abs_id:
            continue
        for lvl in a:
            if lvl.tag != W + "lvl" or _v_int(lvl.get(W + "ilvl")) not in range(n):
                continue
            rpr = lvl.find(W + "rPr")
            for e in ("vanish", "caps", "smallCaps"):
                el = rpr.find(W + e) if rpr is not None else None
                if el is not None and el.get(W + "val") not in V_OFF:
                    return f"level {lvl.get(W + 'ilvl')}'s number is formatted {e} (w:lvl/w:rPr)"
    return None


def v_row(x: VNumbered) -> List[Any]:
    return [x.index, x.num_id, x.ilvl, None if x.counters is None else list(x.counters), x.text, x.suff, x.issue]


def v_visible(x: Optional[VNumbered], run_text: str) -> str:
    """A paragraph's visible line with that number (None: unnumbered); a number not evaluated equals only itself."""
    if x is None:
        return v_visible_line(None, run_text)
    if x.not_evaluated is not None:
        return "\x00" + f"{x.issue} list {x.num_id} level {x.ilvl}" + "\x00" + run_text
    return v_visible_line(x.text, run_text, x.suff)


def v_destination(ctx: Ctx, a: Dict[str, Any], params: Dict[str, Any], src_count: Sequence[VNumbered],
                  errs: List[str], tag: str) -> Optional[VDest]:
    """The planned output's scheme, styles and lists (module docstring); None (an error recorded) when the template
    changed since planning. ValueError where the op must refuse."""
    levels, template, existing = _v_scheme_params(params)
    s_npart = ctx.src.related(NUMBERING_REL)
    s_num = ctx.src.xml(s_npart) if s_npart else None
    src_defs = VListDefs(s_num)
    requested: List[str] = []
    new_nums: Set[str] = set()
    if template is not None:
        from .template import _template
        path = a.get("template") or template
        md5, tpl = _template(path)
        if a.get("template_md5") is not None and md5 != a.get("template_md5"):
            errs.append(f"{tag}: the template changed since planning (md5 {md5} ≠ {a.get('template_md5')})")
            return None
        tsp = tpl.related(STYLES_REL)
        t_styles = Styles(tpl.xml(tsp) if tsp else None)
        tnp = tpl.related(NUMBERING_REL)
        t_num = tpl.xml(tnp) if tnp else None
        t_defs = VListDefs(t_num)
        try:
            t_scheme = v_resolve_scheme(levels, t_styles, t_defs)
        except ValueError as e:
            raise ValueError(f"the template's levels are no scheme: {e} (the op must refuse: bad-params)")
        why = v_list_is_clean(t_defs, t_scheme.num_id, []) or _v_levels_shown(t_num, t_scheme.abstract_id, len(levels))
        if why:
            raise ValueError(f"the template's list cannot be a destination: {why} (the op must refuse: bad-params)")
        sids = []
        for k, ref in enumerate(levels):
            did = v_resolve_style(ctx.src_styles, ref)
            tid = t_scheme.levels[k].style
            if existing == "replace" or did is None:
                sids.append(tid)
                requested.append(tid)
            else:
                sids.append(did)
        check_requested(ctx, a, requested, existing, path, errs, tag)
        styles, defs = ctx.src_styles, src_defs
        if requested:
            d = v_derive_import(ctx.src_styles, s_num, t_styles, t_num, requested, existing,
                                used=v_used_ids(ctx.src, s_num))
            if d["imported"] or d["replaced"]:
                root = copy.deepcopy(ctx.src_styles_root)
                last = {}
                for st in root.findall(W + "style"):
                    last[st.get(W + "styleId")] = st
                for sid in d["replaced"]:
                    root.replace(last[sid], copy.deepcopy(d["styles"][sid]))
                for sid in d["imported"]:
                    root.append(copy.deepcopy(d["styles"][sid]))
                styles = Styles(root)
                defs = src_defs.with_extra([copy.deepcopy(x) for x in d["abstracts"]],
                                           [copy.deepcopy(x) for x in d["nums"]])
                new_nums = set(d["num_map"].values())
    else:
        sids = list(levels)
        check_requested(ctx, a, [], existing, None, errs, tag)
        styles, defs = ctx.src_styles, src_defs
    try:
        scheme = v_resolve_scheme(sids, styles, defs)
    except ValueError as e:
        raise ValueError(f"the levels are no scheme in the planned output: {e} (the op must refuse: scheme)")
    if str(scheme.num_id) not in new_nums:                 # the file's own list (an imported one is the template's)
        why = _v_levels_shown(s_num, scheme.abstract_id, len(sids))
        if why:
            raise ValueError(f"destination list {scheme.num_id}: {why} (the op must refuse: scheme)")
    why = v_list_is_clean(defs, scheme.num_id, src_count)
    if why:
        raise ValueError(f"destination list {scheme.num_id}: {why} (the op must refuse: numbering-unsupported)")
    return VDest(scheme, [x.style for x in scheme.levels], styles, defs, requested, template)


def v_first_difference(want: Sequence[Sequence[Any]], got: Sequence[Sequence[Any]]) -> Optional[str]:
    """Where two lists of number rows ([index, num, ilvl, counters, text, suff, issue]) first differ."""
    a = {r[0]: list(r) for r in want}
    b = {r[0]: list(r) for r in got}
    for i in sorted(set(a) | set(b)):
        if a.get(i) != b.get(i):
            return f"paragraph {i}: {a.get(i)} vs {b.get(i)}"
    if [list(r) for r in want] != [list(r) for r in got]:
        return "the same rows in another order"
    return None


def _v_from(frm: Any) -> Any:
    if frm == "main":
        return frm
    if not isinstance(frm, dict) or set(frm) != {"styles"} or not isinstance(frm["styles"], list) or \
            len(frm["styles"]) not in range(1, 10) or any(
                not isinstance(g, list) or not g or any(not isinstance(s, str) or not s.strip() for s in g)
                for g in frm["styles"]):
        raise ValueError(f"from {frm!r} (the op must refuse: bad-params)")
    flat = [s for g in frm["styles"] for s in g]
    if len(set(flat)) != len(flat):
        raise ValueError("from.styles names a style twice (the op must refuse: bad-params)")
    return frm


def _v_level_map(lm: Any, n: int) -> Dict[int, int]:
    if lm is None:
        return {}
    if not isinstance(lm, dict) or any(not isinstance(k, str) or re.fullmatch("[0-8]", k) is None for k in lm) or any(
            not isinstance(v, int) or isinstance(v, bool) or not 0 <= v < n for v in lm.values()):
        raise ValueError(f"level_map {lm!r} (the op must refuse: bad-params)")
    return {int(k): v for k, v in lm.items()}


def _converted_numbering(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    tag = "converted_numbering"
    params = a.get("params") or {}
    levels, template, existing = _v_scheme_params(params)
    direct = params.get("direct_formatting", "keep")
    if direct not in ("keep", "clear"):
        raise ValueError(f"direct_formatting {direct!r} (the op must refuse)")
    tracked = bool(params["tracked"]) if "tracked" in params else False
    frm = _v_from(params.get("from", "main"))
    level_map = _v_level_map(params.get("level_map"), len(levels))
    author = a.get("author")
    main = ctx.src.main_part()
    if main not in ctx.both:
        errs.append(f"{tag}: {main} missing from the output")
        return tag
    sv, ov = ctx.view(main)
    if len(sv.pars) != len(ov.pars):
        errs.append(f"{tag}: the output has {len(ov.pars)} paragraphs, the source {len(sv.pars)}")
        return tag
    src_count = v_count(sv.root, ctx.src_styles, v_list_defs(ctx.src))
    dest = v_destination(ctx, a, params, src_count, errs, tag)
    if dest is None:
        return f"{tag}: template changed"
    if dest.scheme.num_id != a.get("dest_num") or dest.sids != list(a.get("styles") or []):
        errs.append(f"{tag}: the destination is list {dest.scheme.num_id} with styles {dest.sids} here, the plan says "
                    f"list {a.get('dest_num')} with {a.get('styles')}")

    # ---- the converted paragraphs and their levels (from the SOURCE) ----
    chosen: List[Tuple[VNumbered, int]] = []
    if frm == "main":
        aid = v_main_abstract(src_count)
        chosen = [(x, x.ilvl) for x in src_count if aid is not None and x.abstract_id == aid]
    else:
        level_of: Dict[str, int] = {}
        for k, refs in enumerate(frm["styles"]):
            for ref in refs:
                sid = v_resolve_style(ctx.src_styles, ref)
                if sid is None:
                    continue
                if sid in level_of:
                    raise ValueError(f"from.styles: {sid} named for two levels (the op must refuse: ambiguous)")
                level_of[sid] = k
        numbered = {x.index: x for x in src_count}
        for i, x in sorted(numbered.items()):
            st = v_paragraph_style(sv.pars[i], ctx.src_styles)
            if st in level_of:
                chosen.append((x, level_of[st]))
    converted: List[Tuple[VNumbered, int]] = []
    for x, src_level in chosen:
        p = sv.pars[x.index]
        where = f"{main} paragraph {x.index}"
        if x.not_evaluated is not None:
            raise ValueError(f"{where}: {x.not_evaluated} (the op must refuse: numbering-unsupported)")
        numpr = p.find(W + "pPr/" + W + "numPr")
        if numpr is not None and numpr.find(W + "numId") is None and numpr.find(W + "ilvl") is not None:
            raise ValueError(f"{where}: its own numPr has only an ilvl (the op must refuse: numbering-unsupported)")
        if numpr is not None and any(c.tag in V_NUMPR_REVISIONS for c in numpr.iter()):
            raise ValueError(f"{where}: a tracked change in its own numPr (the op must refuse: existing-revisions)")
        lvl = level_map.get(src_level, src_level)
        if lvl >= len(dest.scheme.levels):
            raise ValueError(f"{where}: level {lvl} is beyond the scheme (the op must refuse: scheme-depth)")
        converted.append((x, lvl))
    if [[x.index, lvl] for x, lvl in converted] != [list(c) for c in a.get("converted") or []]:
        errs.append(f"{tag}: the paragraphs converted here {[[x.index, lvl] for x, lvl in converted][:12]} are not the "
                    f"plan's {(a.get('converted') or [])[:12]}")

    # ---- the numbers: own prediction = the plan's = own count of the output ----
    override = {x.index: (dest.scheme.num_id, lvl) for x, lvl in converted}
    predicted = v_count(sv.root, dest.styles, dest.defs, override)
    at = {y.index: y for y in predicted}
    for x, _ in converted:
        y = at.get(x.index)
        if y is None or y.not_evaluated is not None:
            raise ValueError(f"{main} paragraph {x.index}: its converted number cannot be evaluated "
                             f"({y.not_evaluated if y else 'not numbered'}) (the op must refuse: numbering-unsupported)")
    for x in src_count:
        y = at.get(x.index)
        if x.not_evaluated is None and y is not None and y.not_evaluated is not None:
            raise ValueError(f"{main} paragraph {x.index}: evaluated in the source, not after the conversion "
                             f"({y.not_evaluated}) (the op must refuse: numbering-unsupported)")
    mine = [v_row(y) for y in predicted]
    diff = v_first_difference(mine, a.get("predicted") or [])
    if diff:
        errs.append(f"{tag}: the plan's predicted numbers are not the verifier's — {diff}")
    out_count = [v_row(y) for y in v_count(ov.root, ctx.out_styles, v_list_defs(ctx.out))]
    diff = v_first_difference(mine, out_count)
    if diff:
        errs.append(f"{tag}: the output's numbers are not the predicted ones — {diff}")

    # ---- each changed paragraph ----
    run_names, para_names = v_cleared_sets() if direct == "clear" else (set(), set())
    amap = ctx.maps[main]
    changed: List[List[Any]] = []
    n_paras = n_restyled = n_runs = n_marks = 0
    for x, lvl in converted:
        bi = x.index
        p = sv.pars[bi]
        sid = dest.sids[lvl]
        ps = p.find(W + "pPr/" + W + "pStyle")
        current = ps.get(W + "val") if ps is not None else ctx.src_styles.default.get("paragraph")
        restyle = current != sid
        own = p.find(W + "pPr/" + W + "numPr") is not None
        d = v_decide(p, sv.runs[bi], ctx.src_styles, run_names, para_names) if direct == "clear" else None
        if not restyle and not own and not (d is not None and d.changed):
            continue
        changed.append([bi, lvl, restyle, own])
        where = f"{tag} {main} block {bi}"
        if bi in ctx.text_changed.get(main, set()):
            errs.append(f"{where}: converted but also has predicted text changes")
            continue
        oi = amap.get(bi)
        if oi != bi or len(ov.runs[oi]) != len(sv.runs[bi]):
            errs.append(f"{where}: the paragraph's place or run structure changed")
            continue
        gone: Set[str] = set()
        mark_stays = p.find(W + "pPr/" + W + "rPr") is not None
        if d is not None:
            nr, nm, mark_stays = check_runs_and_mark(ctx, main, bi, oi, d, tracked, author, errs)
            n_runs, n_marks = n_runs + nr, n_marks + nm
            gone = set(d.ppr)
        mark = d is not None and d.mark is not None
        S, O = p.find(W + "pPr"), ov.pars[oi].find(W + "pPr")
        ppr_change = restyle or own or bool(gone)
        if ppr_change or mark:
            expected = v_expected_ppr(S, gone | ({"numPr"} if own else set()), mark_stays, mark=mark)
            if restyle or own:
                expected = v_with_pstyle(expected, sid)
            check_cleared_ppr(S, O, expected, tracked, author, where, errs, changed=ppr_change, mark=mark)
        if ppr_change:
            n_paras += 1
            n_restyled += int(restyle)
            ctx.strip_ppr.setdefault(main, {}).setdefault(bi, set()).update(
                gone | {"pStyle", "pPrChange"} | ({"numPr"} if own else set()))
    for n, key, what in ((n_paras, "expected_paragraphs", "paragraph(s) changed"),
                         (n_restyled, "expected_restyled", "paragraph(s) restyled"),
                         (n_runs, "expected_runs", "run(s) cleared"), (n_marks, "expected_marks", "mark(s) cleared")):
        if n != a.get(key, -1):
            errs.append(f"{tag}: {n} {what} here, plan predicted {a.get(key)}")
    if changed != [list(c) for c in a.get("changed") or []]:
        errs.append(f"{tag}: the paragraphs that change here {changed[:12]} are not the plan's "
                    f"{(a.get('changed') or [])[:12]}")
    return (f"{tag}: {len(converted)} paragraph(s) → list {dest.scheme.num_id} ({', '.join(dest.sids)}); "
            f"{len(predicted)} counted number(s) predicted and found" + (" (tracked)" if tracked else ""))


CONVERT_KINDS = {"converted_numbering": _converted_numbering}
