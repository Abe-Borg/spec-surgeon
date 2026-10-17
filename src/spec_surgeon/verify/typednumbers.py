"""V10 kind ``typed_numbers`` — convert_typed_numbers, verified (built-in batch 5, item 34 — docs/BUILTIN_OPS_BATCH5_DESIGN.md
§2).

This module's own reading of the op's definitions (transform side: ops/convert_typed_numbers.py). Nothing here comes from
ops/ (rule 1): the destination is ``numberingconvert.v_destination``, the list model verify/lists.py, the run text and
the item model verify/edits.py, the clearing rules verify/clearing.py.

From the user's own words (``select``: ``paragraphs`` — all by default —, ``include_tables`` — false by default;
``params``: ``tracked`` — TRUE by default —, ``direct_formatting`` — ``keep`` —; a tracked ``clear`` is a refusal) it
re-derives from the SOURCE:

* the candidates — counted paragraphs of the main part (``v_counted``), unnumbered (``v_paragraph_numbering`` none or
  dangling), selected (``props.para_matches``), outside tables unless ``include_tables`` — and their typed numbers
  (``v_typed_number`` on ``v_paragraph_text``): level and prefix end, which must equal the plan's; the refusals (two
  levels → ``ambiguous``; a level with ``w:suff`` nothing → ``numbering-mismatch``; a tracked change in the own numPr);
* every counted paragraph's number over the planned output with its OWN model (``v_count``, the converted paragraphs given
  the destination list at their typed level) — it must equal the plan's prediction, and the planned output must read the
  same as the source (else the op had to refuse ``numbering-mismatch``);
* each converted paragraph: exactly the source with the prefix deleted (the item model, ``check_edited_paragraph`` with
  ``tabs=True``: a tab after the number goes with it — untracked removed, tracked inside the new ``w:del``; with
  ``clear`` the expected items come from the source with the cleared run properties gone), its pPr = the source's with
  ``w:pStyle`` set first (restyled), ``w:numPr`` gone (own) and the cleared children gone, the ``w:pPrChange`` by the
  shared rules, its mark exactly the source's (or, cleared, the source's minus the cleared names). The paragraph is then
  registered in ``verified_blocks`` (the residual leaves it out: everything in it was compared here).

**Post-condition on the OUTPUT, over every counted paragraph of the main part**: its visible line (the rendered number
by the verifier's own count of the output, its suffix, then the run text — whitespace runs collapsed, the ends stripped)
equals the source's; and the output's numbers equal the prediction.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Set, Tuple

from .reader import W
from .props import (FALSY as V_OFF_VALUES, Ctx, _explicit_names, _has_ancestor, _local, _rstyle, _v_run_levels,
                    _v_run_prop, _v_table_style_id, para_matches, sig, symbolic)
from .clearing import (V_DEFAULT_PRESERVE, check_cleared_ppr, check_cleared_rpr, v_cleared_sets, v_decide,
                       v_expected_ppr, v_with_pstyle)
from .edits import check_edited_paragraph, part_revision_ids, v_items, v_paragraph_text
from .markup import v_toggle
from .lists import VNumbered, v_count, v_counted, v_list_defs, v_paragraph_numbering, v_paragraph_style, \
    v_typed_number
from .numberingconvert import V_NUMPR_REVISIONS, v_destination, v_first_difference, v_row, v_visible
from .template import _v_field_runs


def _v_shows_lower(scheme, k: int) -> bool:
    """Level k's number can hold a lower-case letter: a lower-case literal of its text, or a %j of a lowerLetter /
    lowerRoman level (unless the level is legal)."""
    lv = scheme.level_defs[k]
    text = lv.text or ""
    i = 0
    while i < len(text):
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if text[i] == "%" and nxt and nxt in "123456789":
            ref = scheme.level_defs.get(ord(nxt) - ord("1"))
            if not lv.legal and ref is not None and ref.fmt in ("lowerLetter", "lowerRoman"):
                return True
            i += 2
            continue
        if text[i].islower():
            return True
        i += 1
    return False


V_LOOK_TOGGLES = ("vanish", "specVanish", "caps", "smallCaps", "strike", "dstrike", "b", "i")
V_LOOK_REFUSED = ("strike", "dstrike", "vertAlign", "position", "color", "sz")


def _v_tog(levels: List[Any], direct, top, element: str) -> Optional[bool]:
    """A toggle: ``top`` (the level's w:lvl/w:rPr), then ``direct`` win; one style level → that; none → off; more →
    None."""
    for c in (top, direct):
        el = c.find(W + element) if c is not None else None
        if el is not None:
            return el.get(W + "val") not in V_OFF_VALUES
    specs = [L.find(W + element) for L in levels if L is not None and L.find(W + element) is not None]
    if not specs:
        return False
    return specs[0].get(W + "val") not in V_OFF_VALUES if len(specs) == 1 else None


def _v_look(levels: List[Any], direct, top=None) -> Dict[str, Any]:
    """This module's own reading of how a run — or, with ``top``, a list number — looks: ``levels`` the style levels'
    rPr, lowest first. Toggles by ``_v_tog``; vertAlign / position / sz / u / color / the ascii font: the nearest of
    ``top``, ``direct``, the levels highest first (defaults baseline, 0, 20, none, black — auto counts as 000000)."""
    out: Dict[str, Any] = {e: _v_tog(levels, direct, top, e) for e in V_LOOK_TOGGLES}
    order = [c for c in [top, direct] + list(reversed(levels)) if c is not None]

    def near(e: str):
        for c in order:
            el = c.find(W + e)
            if el is not None:
                return el
        return None
    for e, dflt in (("vertAlign", "baseline"), ("position", "0"), ("sz", "20")):
        el = near(e)
        out[e] = (el.get(W + "val") if el is not None else None) or dflt
    el = near("u")
    out["u"] = "none" if el is None else (el.get(W + "val") or "single")
    el = near("color")
    if el is None:
        out["color"] = ("000000", None, None, None)
    else:
        v = (el.get(W + "val") or "auto").upper()
        out["color"] = ("000000" if v == "AUTO" else v, el.get(W + "themeColor"), el.get(W + "themeShade"),
                        el.get(W + "themeTint"))
    out["font"] = None
    for c in order:
        rf = c.find(W + "rFonts")
        if rf is not None and (rf.get(W + "ascii") or rf.get(W + "asciiTheme")):
            out["font"] = rf.get(W + "ascii") or "theme:" + rf.get(W + "asciiTheme")
            break
    return out


def _v_planned_levels(p, planned_styles, sid: str, rpr) -> List[Any]:
    """The rPr of every style level of a run (or the mark) of ``p`` in the planned output with ``sid`` as its paragraph
    style, lowest first: docDefaults, the table style chain, ``sid``'s chain, the character style chain (rStyle, else the
    default) — each root-most first."""
    levels = [planned_styles.doc_defaults_rpr()]
    for x in (_v_table_style_id(p, planned_styles), sid, _rstyle(rpr) or planned_styles.default.get("character")):
        levels += [st.find(W + "rPr") for st in reversed(planned_styles.chain(x))]
    return levels


def _v_hidden_any(rpr, p, styles) -> Optional[bool]:
    """vanish or specVanish (the run levels' toggle rule): on → True; else either ambiguous → None; else False."""
    levels = _v_run_levels(rpr, p, styles)
    a, b = v_toggle(levels, rpr, "vanish"), v_toggle(levels, rpr, "specVanish")
    if a is True or b is True:
        return True
    return None if (a is None or b is None) else False


def _v_number_symbolic(p, planned_styles, sid: str, level_rpr) -> bool:
    mrpr = p.find(W + "pPr/" + W + "rPr")
    names = list(_explicit_names(level_rpr)) + list(_explicit_names(mrpr))
    for x in (_rstyle(mrpr), sid):
        for st in planned_styles.chain(x):
            names += _explicit_names(st.find(W + "rPr"))
    return any(n.casefold() in V_DEFAULT_PRESERVE for n in names)


def _v_display(p, prefix_runs: List[Any], token_runs: List[Any], token: str, styles, planned_styles, sid: str, scheme,
               level: int, level_rpr) -> Optional[str]:
    """Why the typed number does not read as its run text, or its list number would not look like it (None: it does) —
    this module's own reading, in this order: a prefix run hidden (vanish or specVanish on or ambiguous); a run holding a
    character of the typed number in caps or smallCaps (on or ambiguous) when the typed number holds a lower-case
    letter; the mark hidden; in the planned output the number (the mark's rPr over ``_v_planned_levels``, the level's
    rPr on top) hidden, or in capitals where the level's number can hold a lower-case letter; the number in a symbol
    font, or a typed run in one (``props.symbolic``); a typed run and the number (both over the planned chain) differing
    in strike, dstrike, vertAlign, position, colour or size (an ambiguous toggle counts as a difference)."""
    for r in prefix_runs:
        if _v_hidden_any(r.find(W + "rPr"), p, styles) is not False:
            return "the typed number is hidden text"
    if any(ch.islower() for ch in token):
        for r in token_runs:
            for e in ("caps", "smallCaps"):
                if _v_run_prop(r.find(W + "rPr"), p, styles, e)[0] is not False:
                    return f"the typed number is shown in {e}"
    ppr = p.find(W + "pPr")
    if _v_hidden_any(ppr.find(W + "rPr") if ppr is not None else None, p, styles) is not False:
        return "the paragraph mark is hidden"
    mrpr = p.find(W + "pPr/" + W + "rPr")
    num = _v_look(_v_planned_levels(p, planned_styles, sid, mrpr), mrpr, level_rpr)
    if num["vanish"] is not False or num["specVanish"] is not False:
        return f"with style {sid} the list number is hidden"
    if _v_shows_lower(scheme, level) and (num["caps"] is not False or num["smallCaps"] is not False):
        return f"with style {sid} the list number is in capitals"
    if _v_number_symbolic(p, planned_styles, sid, level_rpr):
        return f"with style {sid} the list number is in a symbol font"
    for r in token_runs:
        rpr = r.find(W + "rPr")
        if symbolic(rpr, p, styles, V_DEFAULT_PRESERVE):
            return "the typed number is in a symbol font"
    for r in token_runs:
        rpr = r.find(W + "rPr")
        typed = _v_look(_v_planned_levels(p, planned_styles, sid, rpr), rpr)
        for e in V_LOOK_REFUSED:
            if typed[e] is None or typed[e] != num[e]:
                return f"the list number's {e} ({num[e]!r}) is not the typed number's ({typed[e]!r})"
    return None


def _v_cleared_copy(p, runs: List[Any], cleared: Dict[int, Set[str]]):
    """A copy of paragraph ``p`` whose runs ``cleared`` ({index among ``runs``: names}) lost those run properties (an
    emptied w:rPr removed — the plain rule)."""
    c = copy.deepcopy(p)
    mine = []
    for r in c.iter(W + "r"):
        q = r.getparent()
        while q is not None and q.tag != W + "p":
            q = q.getparent()
        if q is c:
            mine.append(r)
    if len(mine) != len(runs):
        raise ValueError("the paragraph's runs could not be matched in its copy")
    for ri, names in cleared.items():
        rpr = mine[ri].find(W + "rPr")
        if rpr is None:
            continue
        for x in list(rpr):
            if _local(x) and _local(x) in names:
                rpr.remove(x)
        if not len(rpr) and not rpr.attrib:
            mine[ri].remove(rpr)
    return c


def _typed_numbers(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    tag = "typed_numbers"
    params = a.get("params") or {}
    sel = a.get("select") or {}
    if set(sel) - {"paragraphs", "include_tables"}:
        raise ValueError(f"unknown selector key(s) {sorted(set(sel) - {'paragraphs', 'include_tables'})}")
    direct = params.get("direct_formatting", "keep")
    if direct not in ("keep", "clear"):
        raise ValueError(f"direct_formatting {direct!r} (the op must refuse)")
    tracked = bool(params["tracked"]) if "tracked" in params else True
    if tracked and direct == "clear":
        raise ValueError("a tracked conversion with direct_formatting 'clear' (the op must refuse: bad-params)")
    pred = sel.get("paragraphs", "all")
    tables = bool(sel.get("include_tables", False))
    author = a.get("author")
    main = ctx.src.main_part()
    if main not in ctx.both:
        errs.append(f"{tag}: {main} missing from the output")
        return tag
    sv, ov = ctx.view(main)
    if len(sv.pars) != len(ov.pars):
        errs.append(f"{tag}: the output has {len(ov.pars)} paragraphs, the source {len(sv.pars)}")
        return tag
    src_defs = v_list_defs(ctx.src)
    src_count = v_count(sv.root, ctx.src_styles, src_defs)
    dest = v_destination(ctx, a, params, src_count, errs, tag)
    if dest is None:
        return f"{tag}: template changed"
    if dest.scheme.num_id != a.get("dest_num") or dest.sids != list(a.get("styles") or []):
        errs.append(f"{tag}: the destination is list {dest.scheme.num_id} with styles {dest.sids} here, the plan says "
                    f"list {a.get('dest_num')} with {a.get('styles')}")
    texts = [v_paragraph_text(p) for p in sv.pars]

    # ---- candidates, typed numbers, levels (from the SOURCE) ----
    found: List[Tuple[int, Any]] = []
    for i, p in enumerate(sv.pars):
        if not v_counted(p):
            continue
        nb = v_paragraph_numbering(p, ctx.src_styles, src_defs)
        if nb is not None and not nb.dangling:
            continue
        if not tables and _has_ancestor(p, {W + "tc"}, None):
            continue
        if not para_matches(pred, p, i, sv, ctx.src_styles):
            continue
        tn = v_typed_number(texts[i], dest.scheme)
        if tn is None:
            continue
        if tn.ambiguous:
            raise ValueError(f"{main} paragraph {i}: {tn.token!r} fits levels {list(tn.levels)} (the op must refuse: "
                             f"ambiguous)")
        if dest.scheme.levels[tn.level].level.suff == "nothing":
            raise ValueError(f"{main} paragraph {i}: level {tn.level} has no suffix (the op must refuse: "
                             f"numbering-mismatch)")
        prefix_runs: List[Any] = []
        token_runs: List[Any] = []
        t0, t1 = tn.token_start, tn.token_start + len(tn.token)
        for it in v_items(p):
            if it.pos is None or it.run is None:
                continue
            if it.pos < tn.end and all(it.run is not r for r in prefix_runs):
                prefix_runs.append(it.run)
            if t0 <= it.pos < t1 and all(it.run is not r for r in token_runs):
                token_runs.append(it.run)
        why = _v_display(p, prefix_runs, token_runs, tn.token, ctx.src_styles, dest.styles, dest.sids[tn.level],
                         dest.scheme, tn.level, dest.level_rprs.get(tn.level))
        if why:
            raise ValueError(f"{main} paragraph {i}: {why} (the op must refuse: typed-format)")
        numpr = p.find(W + "pPr/" + W + "numPr")
        if numpr is not None and any(c.tag in V_NUMPR_REVISIONS for c in numpr.iter()):
            raise ValueError(f"{main} paragraph {i}: a tracked change in its own numPr (the op must refuse: "
                             f"existing-revisions)")
        if tracked:
            seen, sid0 = set(), v_paragraph_style(p, ctx.src_styles)
            while sid0 and sid0 in ctx.src_styles.by_id and sid0 not in seen:
                if sid0 in dest.replaced:
                    raise ValueError(f"{main} paragraph {i}: its style chain holds {sid0}, whose definition the import "
                                     f"replaces untracked (the op must refuse: tracked-format)")
                seen.add(sid0)
                b = ctx.src_styles.by_id[sid0].find(W + "basedOn")
                sid0 = b.get(W + "val") if b is not None else None
        found.append((i, tn))
    if [[i, tn.level, tn.end] for i, tn in found] != [list(c) for c in a.get("converted") or []]:
        errs.append(f"{tag}: the typed numbers found here {[[i, tn.level, tn.end] for i, tn in found][:12]} are not "
                    f"the plan's {(a.get('converted') or [])[:12]}")

    # ---- the prediction, the guarantee, the output ----
    override = {i: (dest.scheme.num_id, tn.level) for i, tn in found}
    predicted = v_count(sv.root, dest.styles, dest.defs, override)
    mine = [v_row(y) for y in predicted]
    diff = v_first_difference(mine, a.get("predicted") or [])
    if diff:
        errs.append(f"{tag}: the plan's predicted numbers are not the verifier's — {diff}")
    by_src = {x.index: x for x in src_count}
    by_pred = {y.index: y for y in predicted}
    ends = {i: tn.end for i, tn in found}
    for i, p in enumerate(sv.pars):
        if v_counted(p) and v_visible(by_src.get(i), texts[i]) != v_visible(
                by_pred.get(i), texts[i][ends[i]:] if i in ends else texts[i]):
            raise ValueError(f"{main} paragraph {i} would read {v_visible(by_pred.get(i), texts[i][ends.get(i, 0):])!r}, "
                             f"not {v_visible(by_src.get(i), texts[i])!r} (the op must refuse: numbering-mismatch)")
    out_count = v_count(ov.root, ctx.out_styles, v_list_defs(ctx.out))
    diff = v_first_difference(mine, [v_row(y) for y in out_count])
    if diff:
        errs.append(f"{tag}: the output's numbers are not the predicted ones — {diff}")
    by_out = {y.index: y for y in out_count}
    differ = []
    for i, p in enumerate(sv.pars):
        if v_counted(p) != v_counted(ov.pars[i]):
            differ.append(f"paragraph {i}: counted {v_counted(p)} → {v_counted(ov.pars[i])}")
            continue
        if not v_counted(p):
            continue
        before = v_visible(by_src.get(i), texts[i])
        after = v_visible(by_out.get(i), v_paragraph_text(ov.pars[i]))
        if before != after:
            differ.append(f"paragraph {i}: {before!r} → {after!r}")
    if differ:
        errs.append(f"{tag}: the output does not read like the source — {len(differ)} paragraph(s): {differ[:3]}")

    # ---- each converted paragraph ----
    in_field, old_ids = _v_field_runs(sv.root), part_revision_ids(sv.root)
    run_names, para_names = v_cleared_sets() if direct == "clear" else (set(), set())
    changed: List[List[Any]] = []
    n_paras = n_restyled = n_runs = n_marks = 0
    for i, tn in found:
        p, o = sv.pars[i], ov.pars[i]
        where = f"{tag} {main} block {i}"
        sid = dest.sids[tn.level]
        ps = p.find(W + "pPr/" + W + "pStyle")
        current = ps.get(W + "val") if ps is not None else ctx.src_styles.default.get("paragraph")
        restyle = current != sid
        own = p.find(W + "pPr/" + W + "numPr") is not None
        d = v_decide(p, sv.runs[i], ctx.src_styles, run_names, para_names) if direct == "clear" else None
        changed.append([i, tn.level, restyle, own])
        base = None
        if d is not None and d.runs:
            try:
                base = _v_cleared_copy(p, sv.runs[i], d.runs)
            except ValueError as e:
                errs.append(f"{where}: {e}")
                continue
            n_runs += len(d.runs)
        check_edited_paragraph(p, o, [(0, tn.end, "", False)], tracked, author, in_field, old_ids, where, errs,
                               tabs=True, ppr=False, base=base)
        gone: Set[str] = set(d.ppr) if d is not None else set()
        S, O = p.find(W + "pPr"), o.find(W + "pPr")
        s_mark = S.find(W + "rPr") if S is not None else None
        o_mark = O.find(W + "rPr") if O is not None else None
        if d is not None and d.mark is not None:
            n_marks += 1
            check_cleared_rpr(s_mark, o_mark, d.mark, tracked, author, f"{where} paragraph mark", errs)
            left = [x for x in s_mark if not (_local(x) and _local(x) in d.mark)]
            mark_stays = tracked or bool(left) or bool(s_mark.attrib)
        else:
            mark_stays = s_mark is not None
            if sig(s_mark) != sig(o_mark):
                errs.append(f"{where}: the paragraph mark's run properties changed")
        ppr_change = restyle or own or bool(gone)
        expected = v_expected_ppr(S, gone | ({"numPr"} if own else set()), mark_stays, mark=True)
        if restyle or own:
            expected = v_with_pstyle(expected, sid)
        check_cleared_ppr(S, O, expected, tracked, author, where, errs, changed=ppr_change, mark=True)
        n_paras += int(ppr_change)
        n_restyled += int(restyle)
        ctx.verified_blocks.setdefault(main, {})[i] = i
    for n, key, what in ((n_paras, "expected_paragraphs", "paragraph(s) changed"),
                         (n_restyled, "expected_restyled", "paragraph(s) restyled"),
                         (n_runs, "expected_runs", "run(s) cleared"), (n_marks, "expected_marks", "mark(s) cleared")):
        if n != a.get(key, -1):
            errs.append(f"{tag}: {n} {what} here, plan predicted {a.get(key)}")
    if changed != [list(c) for c in a.get("changed") or []]:
        errs.append(f"{tag}: the paragraphs that change here {changed[:12]} are not the plan's "
                    f"{(a.get('changed') or [])[:12]}")
    return (f"{tag}: {len(found)} typed number(s) → list {dest.scheme.num_id} ({', '.join(dest.sids)}); every counted "
            f"paragraph reads the same" + (" (tracked)" if tracked else " (untracked)"))


TYPED_KINDS = {"typed_numbers": _typed_numbers}
