"""V10 kind ``applied_style`` — apply_style, verified (built-in batch 4, item 30 — docs/BUILTIN_OPS_BATCH4_DESIGN.md §6).

This module's own reading of the op's definitions (transform side: ops/apply_style.py). Nothing here comes from ops/
(rule 1):

* the parameters are read from the user's own words with this module's defaults (direct_formatting ``keep``, existing
  ``keep``; tracked: as asked, else when ``paragraphs`` narrows the selection);
* the style is resolved with the verifier's own resolver (``restyle.v_resolve_style``: the exact ``w:styleId``, else the
  one style whose ``w:name`` matches, casefolded) — map_styles' rule: with ``existing: "keep"`` the SOURCE's own style
  when it has one, else the template's (read by ``template._template``, md5 checked); with ``"replace"`` always the
  template's; it must be a paragraph style; the import it needs (the template style, or none) must be exactly what
  this op's ``styles_imported`` assertion carries (``styleimport.check_requested``);
* the selection (``props.para_matches``), each paragraph's style (pStyle, else the default paragraph style) and, with
  ``clear``, its cleared targets under clear_direct_formatting's DEFAULT rules (``clearing.v_decide`` with
  ``clearing.v_cleared_sets()``) are re-derived from the SOURCE.
* ``replace_style_owned`` derives destination ownership from the source/template graph independently through
  ``styleowned``. Exact retained attributes are proved alongside removals; projected transform XML is never evidence.

Fails when the changed paragraphs (and which are restyled) or the counts differ from the plan's; when a changed
paragraph's output pPr is not the source's with ``w:pStyle`` = the style first (restyled) and the cleared children gone
(``clearing.v_expected_ppr`` / ``v_with_pstyle`` / ``check_cleared_ppr``: the w:pPrChange by the shared rules); when a
cleared run or mark is not exactly the source's minus the cleared children (``clearing.check_runs_and_mark``). An
imported or replaced style is ``styles_imported``'s (verify/styleimport.py); this kind checks only that the applied id
is defined in the output and is a paragraph style there. ``pStyle``, ``pPrChange`` and the cleared pPr children of the
changed paragraphs, and the checked runs and marks, leave the residual.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from .reader import STYLES_REL, W
from .props import Ctx, Styles, para_matches
from .clearing import (check_cleared_ppr, check_runs_and_mark, v_cleared_sets, v_decide, v_expected_ppr,
                       v_with_pstyle)
from .restyle import v_resolve_style
from .styleimport import check_requested, v_type
from .styleowned import v_style_owned_decision, v_style_ownership


def _v_template_styles(path: str, md5: Optional[str], errs: List[str]) -> Optional[Styles]:
    from .template import _template
    got, tpl = _template(path)
    if md5 is not None and got != md5:
        errs.append(f"applied_style: the template changed since planning (md5 {got} ≠ {md5})")
        return None
    tsp = tpl.related(STYLES_REL)
    return Styles(tpl.xml(tsp) if tsp else None)


def _applied_style(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    sel = a.get("select") or {}
    if set(sel) - {"scope", "paragraphs"}:
        raise ValueError(f"unknown selector key(s) {sorted(set(sel) - {'scope', 'paragraphs'})}")
    scope = sel.get("scope", ["document"])
    parts = ctx.scope_parts([scope] if isinstance(scope, str) else scope)
    if sorted(parts) != sorted(a.get("parts", [])):
        errs.append(f"applied_style: scope resolves to {sorted(parts)} here, plan says {sorted(a.get('parts', []))}")
    params = a.get("params") or {}
    ref = params.get("style")
    if not isinstance(ref, str) or not ref.strip():
        raise ValueError("no style asked")
    direct = params.get("direct_formatting", "keep")
    existing = params.get("existing", "keep")
    if direct not in ("keep", "clear", "replace_style_owned") or existing not in ("keep", "replace"):
        raise ValueError(f"bad direct_formatting/existing {direct!r}/{existing!r}")
    pred = sel.get("paragraphs", "all")
    tracked = bool(params["tracked"]) if "tracked" in params else pred not in (None, "all", {})
    author = a.get("author")

    # ---- the style (map_styles' resolution: with keep the file's own style when it has one, with replace always the
    # template's) and the import it needs ----
    did = v_resolve_style(ctx.src_styles, ref)
    tid = None
    tstyles = None
    template = params.get("template")
    if template is not None:
        tstyles = _v_template_styles(a.get("template") or template, a.get("template_md5"), errs)
        if tstyles is None:
            return "applied_style: template changed"
        tid = v_resolve_style(tstyles, ref)
        if tid is None or v_type(tstyles.by_id[tid]) != "paragraph":
            raise ValueError(f"the template has no paragraph style {ref!r} (the op must refuse: bad-params)")
    if existing == "replace" and template is None:
        raise ValueError("existing 'replace' without a template (the op must refuse)")
    if existing == "replace" or (did is None and tid is not None):
        sid, requested = tid, [tid]
    elif did is not None:
        sid, requested = did, []
    else:
        raise ValueError(f"style {ref!r} is in neither the document nor a template (the op must refuse: missing-style)")
    check_requested(ctx, a, requested, existing, a.get("template") or template, errs, "applied_style")
    if sid != a.get("style_id"):
        errs.append(f"applied_style: the style resolves to {sid!r} here, the plan applies {a.get('style_id')!r}")
    for label, styles in (("source", ctx.src_styles), ("output", ctx.out_styles)):
        st = styles.by_id.get(sid)
        if st is None:
            if label == "output":
                errs.append(f"applied_style: style {sid!r} is not defined in the output")
            continue
        if label == "source" and requested:
            continue                                   # a replaced definition: styles_imported compares it
        if v_type(st) != "paragraph":
            errs.append(f"applied_style: style {sid!r} is a {st.get(W + 'type')} style in the {label}")

    # ---- the paragraphs ----
    run_names, para_names = v_cleared_sets() if direct == "clear" else (set(), set())
    ownership = v_style_ownership(ctx.src_styles, tstyles, sid, bool(requested)) if direct == "replace_style_owned" else None
    changed: Dict[str, List[List[Any]]] = {}
    n_paras = n_restyled = n_runs = n_marks = 0
    for part in parts:
        if part not in ctx.both:
            errs.append(f"applied_style: {part} missing from the output")
            continue
        sv, ov = ctx.view(part)
        amap = ctx.maps[part]
        for bi, p in enumerate(sv.pars):
            if not para_matches(pred, p, bi, sv, ctx.src_styles):
                continue
            ps = p.find(W + "pPr/" + W + "pStyle")
            current = ps.get(W + "val") if ps is not None else ctx.src_styles.default.get("paragraph")
            restyle = current != sid
            d = v_decide(p, sv.runs[bi], ctx.src_styles, run_names, para_names) if direct == "clear" else None
            if ownership is not None:
                d = v_style_owned_decision(p, sv.runs[bi], ctx.src_styles, ownership)
            cleared = d is not None and d.changed
            if not restyle and not cleared:
                continue
            changed.setdefault(part, []).append([bi, restyle])
            n_restyled += int(restyle)
            where = f"{part} block {bi}"
            if bi in ctx.text_changed.get(part, set()):
                errs.append(f"applied_style: {where} is selected but also has predicted text changes")
                continue
            oi = amap.get(bi)
            if oi is None or len(ov.runs[oi]) != len(sv.runs[bi]):
                errs.append(f"applied_style: {where}: run structure changed")
                continue
            mark_stays = sv.pars[bi].find(W + "pPr/" + W + "rPr") is not None
            gone: Set[str] = set()
            if d is not None:
                nr, nm, mark_stays = check_runs_and_mark(ctx, part, bi, oi, d, tracked, author, errs)
                n_runs, n_marks = n_runs + nr, n_marks + nm
                gone = set(d.ppr)
            S, O = p.find(W + "pPr"), ov.pars[oi].find(W + "pPr")
            # the mark's rPr only when this op changes the mark: another op of the spec changing it (set_font, set_language)
            # is checked by its own kind, and the residual compares the rest (review D2)
            mark = d is not None and d.mark is not None
            if restyle or gone or mark:
                expected = v_expected_ppr(S, gone, mark_stays, mark=mark,
                                          replacements=d.ppr_replacements if d is not None else None)
                if restyle:
                    expected = v_with_pstyle(expected, sid)
                check_cleared_ppr(S, O, expected, tracked, author, where, errs, changed=restyle or bool(gone), mark=mark)
            if restyle or gone:
                n_paras += 1
                ctx.strip_ppr.setdefault(part, {}).setdefault(bi, set()).update(gone | {"pStyle", "pPrChange"})
    for n, key, what in ((n_paras, "expected_paragraphs", "paragraph(s) changed"),
                         (n_restyled, "expected_restyled", "paragraph(s) restyled"),
                         (n_runs, "expected_runs", "run(s) cleared"), (n_marks, "expected_marks", "mark(s) cleared")):
        if n != a.get(key, -1):
            errs.append(f"applied_style: {n} {what} here, plan predicted {a.get(key)}")
    planned = {k: sorted(map(tuple, v)) for k, v in (a.get("changed") or {}).items() if v}
    if {k: sorted(map(tuple, v)) for k, v in changed.items()} != planned:
        errs.append(f"applied_style: the paragraphs that change here {changed} are not the plan's {planned}")
    return (f"applied_style: {n_restyled} paragraph(s) → style {sid}"
            + (f", direct formatting cleared on {n_paras} paragraph(s), {n_runs} run(s), {n_marks} mark(s)"
               if direct != "keep" else "") + (" (tracked)" if tracked else ""))


APPLY_KINDS = {"applied_style": _applied_style}
