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
  over them with each level's style id (ValueError: the op must refuse, ``scheme``); with a template, a level whose style
  the file holds and keeps must number like the template's level (``_v_level_sig``: its text, legal, the formats of the
  levels its text names — else ``scheme``, F6 of the batch-5 review); the destination list clean against the SOURCE
  count (``v_list_is_clean``; ValueError: ``numbering-unsupported``); each level's ``w:lvl/w:rPr`` in the destination's
  abstractNum (``VDest.level_rprs``, read by ``typed_numbers``' display rule) and the styles the import replaces
  (``VDest.replaced``).

**converted_numbering** re-derives from the SOURCE the converted paragraphs (``from: "main"`` — the counted paragraphs
numbered by any num of ``v_main_abstract``, at their ilvl —, or ``from.styles`` — the counted, numbered paragraphs whose
style is in level k's list, at k), the refusals the op must raise (not evaluated in the source unless its level is
known — ``V_LEVEL_KNOWN`` — and every counted paragraph of its num is converted, F8; a tracked change in its own numPr;
a level beyond the scheme after ``level_map``; a converted number the prediction cannot evaluate — unless it was not
evaluated before and follows a skipped level at the level it had —, or a formerly evaluated one; tracked over a style
chain holding a style the import replaces, F14; a paragraph NOT converted whose number — rendered text, suffix, issue
— changes outside the destination list, F7), each level's style, and each paragraph's change (restyled, own numPr
removed, with ``clear`` clear_direct_formatting's DEFAULT targets). It predicts every counted paragraph's number with
its OWN model (``v_count`` over the planned output with the converted paragraphs given the destination list at their levels), which
must equal the plan's; its own count of the OUTPUT must equal both (the paragraphs as many as the source's). Each
changed paragraph's pPr = the source's with ``w:pStyle`` set first (restyled), ``w:numPr`` gone (own) and the cleared
children gone, the ``w:pPrChange`` by the shared rules (``clearing.check_cleared_ppr``); cleared runs and marks exactly
(``clearing.check_runs_and_mark``). It strips ``pStyle``, ``pPrChange``, ``numPr`` (own) and the cleared names of the
changed paragraphs (``strip_ppr``) after comparing the whole pPr.

``select.include_tables`` false (true by default) leaves out of the chosen paragraphs every one with a ``w:tc`` among
its ancestors, before the refusals. ``counters: "keep"`` (``flag`` by default): the op must refuse a converted
paragraph whose number the source or the prediction does not evaluate (``numbering-unsupported``) and one whose
predicted counters are not its source counters, compared as tuples (``numbering-mismatch``).
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Set, Tuple

from .reader import STYLES_REL, W
from .props import Ctx, Styles
from .clearing import check_cleared_ppr, check_runs_and_mark, v_cleared_sets, v_decide, v_expected_ppr, v_with_pstyle
from .lists import (V_OFF, VListDefs, VNumbered, VScheme, _v_int, _v_placeholders, v_count, v_list_defs,
                    v_list_is_clean, v_main_abstract, v_paragraph_style, v_resolve_scheme, v_visible_line)
from .restyle import v_resolve_style
from .styleimport import NUMBERING_REL, check_requested, v_derive_import, v_used_ids

V_NUMPR_REVISIONS = {W + "ins", W + "del", W + "moveFrom", W + "moveTo", W + "numberingChange"}
V_LEVEL_KNOWN = {"shared", "start-override", "skipped", "override"}   # not evaluated, the level known


class VDest(NamedTuple):
    scheme: VScheme
    sids: List[str]
    styles: Styles               # the planned output's styles
    defs: VListDefs              # the planned output's lists
    requested: List[str]
    template: Optional[str]
    level_rprs: Dict[int, Any] = {}       # level → its w:lvl/w:rPr in the destination's abstractNum (None: none)
    replaced: List[str] = []              # the styles the import replaces (existing "replace")


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
    replaced: List[str] = []
    copied_abstracts: List[Any] = []
    new_nums: Set[str] = set()
    t_scheme = None
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
                replaced = list(d["replaced"])
                copied_abstracts = list(d["abstracts"])
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
    sids = [x.style for x in scheme.levels]
    if t_scheme is not None:
        held = [(k, sid) for k, sid in enumerate(sids) if sid not in requested
                and _v_level_sig(scheme.level_defs, k) != _v_level_sig(t_scheme.level_defs, k)]
        if held:
            raise ValueError(f"the file's own style(s) {[s for _, s in held]} number unlike the template's levels "
                             f"{[k for k, _ in held]} and existing 'keep' keeps them (the op must refuse: scheme)")
    why = v_list_is_clean(defs, scheme.num_id, src_count)
    if why:
        raise ValueError(f"destination list {scheme.num_id}: {why} (the op must refuse: numbering-unsupported)")
    abstract = None
    for a_el in list(s_num if s_num is not None else []) + copied_abstracts:
        if isinstance(a_el.tag, str) and a_el.tag == W + "abstractNum" and \
                _v_int(a_el.get(W + "abstractNumId")) == scheme.abstract_id:
            abstract = a_el
            break
    level_rprs: Dict[int, Any] = {k: None for k in range(len(sids))}
    for lvl in (abstract.findall(W + "lvl") if abstract is not None else []):
        k = _v_int(lvl.get(W + "ilvl"))
        if k in level_rprs and level_rprs[k] is None:
            level_rprs[k] = lvl.find(W + "rPr")
    return VDest(scheme, sids, styles, defs, requested, template, level_rprs, replaced)


def _v_level_sig(level_defs, k: int) -> Tuple[Any, ...]:
    """How level k numbers: its text, legal, the format of every level its text names (decimal when legal)."""
    lv = level_defs.get(k)
    if lv is None:
        return (None,)
    named = [x for kind, x in _v_placeholders(lv.text or "") if kind == "lvl"]
    return (lv.text, lv.legal, tuple((j, "decimal" if lv.legal else (level_defs[j].fmt if j in level_defs else None))
                                     for j in named))


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


def _v_in_cell(p) -> bool:
    """A w:tc among the paragraph's ancestors."""
    return any(anc.tag == W + "tc" for anc in p.iterancestors())


def _converted_numbering(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    tag = "converted_numbering"
    params = a.get("params") or {}
    sel = a.get("select") or {}
    if set(sel) - {"include_tables"} or not isinstance(sel.get("include_tables", True), bool):
        raise ValueError(f"select {sel!r} (the op must refuse: bad-params)")
    cells = sel.get("include_tables", True)
    keep = params.get("counters", "flag")
    if keep not in ("flag", "keep"):
        raise ValueError(f"counters {keep!r} (the op must refuse: bad-params)")
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
    if not cells:
        chosen = [(x, k) for x, k in chosen if not _v_in_cell(sv.pars[x.index])]
    converted: List[Tuple[VNumbered, int]] = []
    picked = {x.index for x, _ in chosen}
    for x, src_level in chosen:
        p = sv.pars[x.index]
        where = f"{main} paragraph {x.index}"
        if x.not_evaluated is not None:
            # a number the source cannot evaluate converts only when its level is known and its whole list goes
            if x.issue not in V_LEVEL_KNOWN or x.ilvl is None or x.num_id is None or any(
                    y.index not in picked for y in src_count if y.num_id == x.num_id):
                raise ValueError(f"{where}: {x.not_evaluated} (the op must refuse: numbering-unsupported)")
        numpr = p.find(W + "pPr/" + W + "numPr")
        if numpr is not None and any(c.tag in V_NUMPR_REVISIONS for c in numpr.iter()):
            raise ValueError(f"{where}: a tracked change in its own numPr (the op must refuse: existing-revisions)")
        if tracked:
            seen, s = set(), v_paragraph_style(p, ctx.src_styles)
            while s and s in ctx.src_styles.by_id and s not in seen:
                if s in dest.replaced:
                    raise ValueError(f"{where}: its style chain holds {s}, whose definition the import replaces "
                                     f"untracked (the op must refuse: tracked-format)")
                seen.add(s)
                b = ctx.src_styles.by_id[s].find(W + "basedOn")
                s = b.get(W + "val") if b is not None else None
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
    for x, lvl in converted:
        y = at.get(x.index)
        if y is not None and x.not_evaluated is not None and y.not_evaluated is not None and \
                x.issue in V_LEVEL_KNOWN and y.issue == "skipped" and x.ilvl == lvl:
            continue                                 # the outline's own skipped level, at the level it had
        if y is None or y.not_evaluated is not None:
            raise ValueError(f"{main} paragraph {x.index}: its converted number cannot be evaluated "
                             f"({y.not_evaluated if y else 'not numbered'}) (the op must refuse: numbering-unsupported)")
    for x in src_count:
        y = at.get(x.index)
        if x.not_evaluated is None and y is not None and y.not_evaluated is not None:
            raise ValueError(f"{main} paragraph {x.index}: evaluated in the source, not after the conversion "
                             f"({y.not_evaluated}) (the op must refuse: numbering-unsupported)")
    by_src = {x.index: x for x in src_count}
    done = {x.index for x, _ in converted}
    for i in sorted(set(by_src) | set(at)):
        if i in done:
            continue
        b, c = by_src.get(i), at.get(i)
        before = None if b is None else (b.text, b.suff, b.issue)
        after = None if c is None else (c.text, c.suff, c.issue)
        if before != after and not (c is not None and c.num_id == dest.scheme.num_id):
            raise ValueError(f"{main} paragraph {i} is not converted and its number changes {before} → {after} "
                             f"outside the destination list (the op must refuse: numbering-mismatch)")
    if keep == "keep":
        for x, _ in converted:
            y = at.get(x.index)
            if x.not_evaluated is not None or y is None or y.not_evaluated is not None:
                raise ValueError(f"{main} paragraph {x.index}: counters 'keep' with a number not evaluated before or "
                                 f"after (the op must refuse: numbering-unsupported)")
            if tuple(x.counters) != tuple(y.counters):
                raise ValueError(f"{main} paragraph {x.index}: counters 'keep', and they go {tuple(x.counters)} → "
                                 f"{tuple(y.counters)} (the op must refuse: numbering-mismatch)")
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
