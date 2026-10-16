"""Op: set_properties — bold, italic, caps, small caps, strikethrough, underline and text colour on selected runs and
their paragraph marks, or in style definitions; keep-with-next, keep lines together, widow control and page break
before on selected paragraphs, or in paragraph styles. Design: docs/SET_PROPERTIES_DESIGN.md.

Formatting mode: zero text diffs — "all caps" is w:caps, a display property; the letters stay exactly as typed.

Two target families, one per op call:

* **direct formatting** — ``select.scope`` / ``paragraphs`` / ``runs`` (set_font's grammar): ``run`` properties go on
  every selected run of the selected paragraphs and on their paragraph marks (``include_paragraph_marks``),
  ``paragraph`` properties on each selected paragraph's pPr. Bold and italic are the pairs Word itself writes (w:b +
  w:bCs, w:i + w:iCs): a run marked complex-script (w:cs) draws its weight and slant from the Cs twin. Caps and small
  caps never touch a symbol run (set_font's definition: ``preserve_fonts``, w:sym) — case-mapping a dingbat changes the
  glyph; the other run properties do apply to it. A target that already shows the asked value — the effective value,
  resolved through its styles by ``effective.PropResolver`` — is left alone and not counted, so tracked mode records a
  change only where Word itself would have. An ambiguous toggle (specified at two style levels) counts as needing the
  change: direct formatting wins, so the result is certain either way.
* **style definitions** — ``select.styles: {"ids": [...]}`` (paragraph and character styles; ``paragraph`` properties on
  paragraph styles only): the style's rPr/pPr in styles.xml, never tracked. The plan resolves every run, mark and
  paragraph of every text part whose style chain includes the style, after the change (``PropResolver`` with an
  override) and with its provenance: a value from the style itself is affected (styles based on it inherit the change
  — noted); a nearer style's own value is normal inheritance (noted, never refused); a disagreeing direct value refuses
  ``conflict-property``; a toggle another style level specifies too refuses ``toggle-ambiguous`` (Word XORs them — not
  modelled, as the pinnable resolvers decide today); caps/smallCaps reaching a symbol run from the style refuse
  ``conflict-property``.

Tracked or plain (decision §14.1): plain for whole-document selections, tracked — one w:rPrChange per changed run or
mark, one w:pPrChange per changed paragraph, the snapshot the properties before — when the selector names particular
paragraphs; ``tracked`` overrides. A target already carrying someone's rPrChange/pPrChange is refused when tracked (two
snapshots cannot be expressed) and changed-and-flagged when plain, as set_font and set_paragraph_props do.

Verified by V10's ``properties`` kind (verify/props.py), which re-derives all of the above on its own.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from ..package import W, DocxPackage
from ..ooxml import (PPR_ORDER, RPR_ORDER, STYLE_ORDER, RevisionStamp, block_full_text, file_uses_date_utc, get_or_add,
                     mark_rpr, max_revision_id, paragraph_ppr, paragraphs, property_change, run_rpr)
from . import register
from .base import JobException, Op, PlanFragment
from .effective import (CASE_ELEMENTS, PARA_FLAGS, REFLOW_ELEMENTS, UNDERLINES, PropResolver, para_elements,
                        run_elements)
from .replace_text import default_author
from .selectors import (DEFAULT_PRESERVE_FONTS, StyleSheet, check_paragraph_selector, check_run_selector,
                        in_revision, is_symbol_rpr, is_targeted, mark_in_revision, paragraph_selected, rpr_selected,
                        runs_by_paragraph, scope_parts)

BOOL_RUN = ("b", "i", "caps", "smallCaps", "strike")
HEX_COLOR = re.compile(r"[0-9A-Fa-f]{6}")
REFLOW_REASON = ("bold, italic and caps change glyph widths and the paragraph flags move paragraphs between pages; "
                 "text reflows, so the source render is no layout reference")
SCOPE_SCHEMA = {"type": "array", "items": {"type": "string", "enum": ["document", "headers", "footers", "footnotes",
                                                                       "endnotes", "comments"]}, "default": ["document"]}


def clean_params(params: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, bool]]:
    """(run properties, paragraph properties) as asked — user-facing names, validated, the colour upper-cased."""
    run, para = params.get("run"), params.get("paragraph")
    if run is None and para is None:
        raise JobException("nothing to set: give 'run' and/or 'paragraph' properties", "bad-params")
    run_out: Dict[str, Any] = {}
    if run is not None:
        if not isinstance(run, dict) or not run:
            raise JobException("'run' must be a non-empty object of run properties", "bad-params")
        for k, v in run.items():
            if k in BOOL_RUN:
                if not isinstance(v, bool):
                    raise JobException(f"run.{k} must be true or false", "bad-params")
                run_out[k] = v
            elif k == "u":
                if v not in UNDERLINES:
                    raise JobException(f"run.u must be one of {UNDERLINES}", "bad-params")
                run_out[k] = v
            elif k == "color":
                if not isinstance(v, str) or not (v == "auto" or HEX_COLOR.fullmatch(v)):
                    raise JobException("run.color must be 6 hex digits (RRGGBB) or 'auto'", "bad-params")
                run_out[k] = v if v == "auto" else v.upper()
            else:
                raise JobException(f"unknown run property {k!r}; known: {BOOL_RUN + ('u', 'color')}", "bad-params")
    if run_out.get("caps") and run_out.get("smallCaps"):
        raise JobException("run.caps and run.smallCaps are mutually exclusive — Word shows all caps and ignores small "
                           "caps when both are in effect; ask for one of them true (and the other false where it is in "
                           "effect)", "bad-params")
    para_out: Dict[str, bool] = {}
    if para is not None:
        if not isinstance(para, dict) or not para:
            raise JobException("'paragraph' must be a non-empty object of paragraph properties", "bad-params")
        for k, v in para.items():
            if k not in PARA_FLAGS:
                raise JobException(f"unknown paragraph property {k!r}; known: {PARA_FLAGS}", "bad-params")
            if not isinstance(v, bool):
                raise JobException(f"paragraph.{k} must be true or false", "bad-params")
            para_out[k] = v
    return run_out, para_out


def caps_conflict(asked: Dict[str, Any], effective) -> bool:
    """Word shows all caps and ignores small caps when both are in effect: True when the op asks caps or smallCaps on
    and, after the change, both would be in effect on this run or mark. ``effective(element)`` gives the value of an
    element the op does not ask (the source's for a direct target, the simulated one for a style target); an
    ambiguous toggle (None) counts as possibly in effect."""
    if not (asked.get("caps") is True or asked.get("smallCaps") is True):
        return False
    after = {e: (asked[e] if e in asked else effective(e)) for e in ("caps", "smallCaps")}
    return after["caps"] is not False and after["smallCaps"] is not False


CAPS_CONFLICT = ("w:caps and w:smallCaps would both be in effect — Word shows all caps and ignores small caps; set the "
                 "other one false in this op")


def write_element(container, element: str, value, order: List[str]) -> None:
    """Set one property element (created in schema order when absent): toggles and flags on → no attributes, off →
    w:val 0; w:color → exactly w:val (theme attributes gone); w:u → w:val set, its other attributes kept."""
    el = get_or_add(container, element, order)
    if element == "u":
        el.set(W + "val", value)
        return
    el.attrib.clear()
    if element == "color":
        el.set(W + "val", value[0])
    elif not value:
        el.set(W + "val", "0")


def _write_rpr(rpr, elements: List[str], asked: Dict[str, Any], stamp: Optional[RevisionStamp], next_id: int) -> int:
    chg = None
    if stamp is not None:
        snapshot = [c for c in rpr if c.tag != W + "rPrChange"]
        chg = property_change("rPrChange", "rPr", snapshot, stamp, next_id)
        next_id += 1
    for e in elements:
        write_element(rpr, e, asked[e], RPR_ORDER)
    if chg is not None:
        rpr.append(chg)
    return next_id


def _describe(run_set: Dict[str, Any], para_set: Dict[str, bool]) -> str:
    parts = []
    if run_set:
        parts.append(", ".join(f"{k} {v}" for k, v in run_set.items()))
    if para_set:
        parts.append(", ".join(f"{k} {v}" for k, v in para_set.items()))
    return "; ".join(parts)


@register
class SetProperties(Op):
    name = "set_properties"
    mode = "formatting"
    description = ("Set bold, italic, caps, small caps, strikethrough, underline and text colour on selected text (runs "
                   "and their paragraph marks) or in style definitions, and keep-with-next, keep lines together, widow "
                   "control and page break before on selected paragraphs or paragraph styles. Adds no characters (caps "
                   "is a display property: the letters stay as typed); a target that already shows the asked value is "
                   "left alone.")
    params_schema = {"type": "object", "properties": {
        "run": {"type": "object",
                "description": "text properties: b, i, caps, smallCaps, strike (true = on, false = explicitly off), "
                               "u (single|double|words|dotted|dash|wave|none), color (RRGGBB hex or 'auto'). b and i "
                               "are written as the pairs Word writes (w:b + w:bCs, w:i + w:iCs)",
                "properties": {"b": {"type": "boolean"}, "i": {"type": "boolean"}, "caps": {"type": "boolean"},
                               "smallCaps": {"type": "boolean"}, "strike": {"type": "boolean"},
                               "u": {"type": "string", "enum": list(UNDERLINES)},
                               "color": {"type": "string", "description": "6 hex digits (RRGGBB) or 'auto'; theme "
                                                                          "colour attributes are removed"}}},
        "paragraph": {"type": "object",
                      "description": "paragraph properties: keepNext, keepLines, widowControl, pageBreakBefore (true/false)",
                      "properties": {k: {"type": "boolean"} for k in PARA_FLAGS}},
        "skip_sym_runs": {"type": "boolean", "default": True,
                          "description": "a run holding a w:sym is a symbol run (caps/smallCaps never touch it)"},
        "preserve_fonts": {"type": "array", "items": {"type": "string"},
                           "description": "symbol/dingbat fonts whose runs never get caps/smallCaps (default: Symbol, "
                                          "Wingdings…); the other run properties do apply to them"},
        "in_revisions": {"type": "string", "enum": ["include", "exclude"], "default": "include",
                         "description": "runs inside pending w:ins/w:del: change them too, or leave them"},
        "include_paragraph_marks": {"type": "boolean", "default": True},
        "tracked": {"type": "boolean", "description": "w:rPrChange / w:pPrChange per change; default: plain for "
                                                      "whole-document selections, tracked when paragraphs are targeted; "
                                                      "never for style definitions"},
        "author": {"type": "string"}}}
    select_schema = {"type": "object", "properties": {
        "scope": SCOPE_SCHEMA,
        "runs": {"description": "'all' or {has_direct_font, font_in, not_in_revisions}"},
        "paragraphs": {"description": "'all' or {style_in, text_regex, in_table, is_empty, index_range, has_bold, "
                                      "is_all_caps, after_heading_regex, between_paragraphs}"},
        "styles": {"description": "{ids: [styleId, …]} — change these style definitions instead of formatting text "
                                  "directly; alone (no scope/runs/paragraphs); paragraph and character styles"}}}
    verification_profile = {
        "V2": "zero text diffs", "V4": "tracked: +1 w:rPrChange per run/mark changed, +1 w:pPrChange per paragraph",
        "V10": "properties: selection and the elements each target gets re-derived (the effective value through the "
               "styles), counts matched, each target's whole rPr/pPr = source + exactly the asked elements, the "
               "asked value shown by every selected target — and by everything that uses a changed style — in the "
               "output; residual identical",
        "V7": "none when bold/italic/caps or a paragraph flag is set (the text reflows); source for colour, underline "
              "and strikethrough alone",
    }

    # ---- plan ----------------------------------------------------------------------------------------------------

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select = dict(select or {})
        unknown = set(select) - {"scope", "runs", "paragraphs", "styles"}
        if unknown:
            raise JobException(f"set_properties: unknown selector key(s) {sorted(unknown)}", "bad-params")
        run_set, para_set = clean_params(params)
        preserve = list(params.get("preserve_fonts", DEFAULT_PRESERVE_FONTS))
        skip_sym = bool(params.get("skip_sym_runs", True))
        sheet = StyleSheet(pkg)
        if "styles" in select:
            if set(select) != {"styles"}:
                raise JobException("set_properties: 'styles' is a target of its own — give it alone, without scope, "
                                   "runs or paragraphs (direct formatting is a second op)", "bad-params")
            return self._plan_styles(pkg, sheet, select, params, run_set, para_set, preserve, skip_sym)
        return self._plan_direct(pkg, sheet, select, params, run_set, para_set, preserve, skip_sym)

    def _plan_direct(self, pkg, sheet, select, params, run_set, para_set, preserve, skip_sym) -> PlanFragment:
        runs_pred, para_pred = select.get("runs", "all"), select.get("paragraphs")
        check_run_selector(runs_pred)
        check_paragraph_selector(para_pred)
        scope = select.get("scope", ["document"])
        parts = scope_parts(pkg, scope)
        in_revs = params.get("in_revisions", "include")
        if in_revs not in ("include", "exclude"):
            raise JobException("in_revisions must be 'include' or 'exclude'", "bad-params")
        marks_too = bool(params.get("include_paragraph_marks", True))
        tracked = bool(params.get("tracked", is_targeted(select)))
        author = params.get("author") or default_author()
        run_asked, para_asked = run_elements(run_set), para_elements(para_set)
        resolver = PropResolver(sheet)
        frag = self.fragment(select, {**params, "tracked": tracked, "author": author})
        targets: Dict[str, Dict[str, list]] = {}
        n_runs = n_marks = n_paras = 0
        already = Counter()
        older_r = older_p = 0
        for part in parts:
            root = pkg.xml(part)
            pars = paragraphs(root)
            groups = runs_by_paragraph(root, pars)
            t_runs: List[list] = []          # [block, run, [elements]]
            t_marks: List[list] = []         # [block, [elements]]
            t_paras: List[list] = []         # [block, [flags]]
            for bi, p in enumerate(pars):
                if not paragraph_selected(para_pred, p, bi, block_full_text(p), sheet):
                    continue
                if run_asked:
                    for ri, r in enumerate(groups[bi]):
                        rpr = r.find(W + "rPr")
                        revised = in_revision(r, p)
                        if (in_revs == "exclude" and revised) or not rpr_selected(runs_pred, rpr, revised):
                            continue
                        sym = is_symbol_rpr(rpr, p, sheet, preserve) or (skip_sym and r.find(W + "sym") is not None)
                        gets = resolver.gets_run(rpr, p, run_asked, sym)
                        if not sym and caps_conflict(run_asked, lambda e, rpr=rpr, p=p: resolver.run_prop(rpr, p, e)[0]):
                            raise JobException(f"{part} paragraph {bi} run {ri}: {CAPS_CONFLICT}", "conflict-property",
                                               {"part": part, "paragraph": bi})
                        if gets:
                            t_runs.append([bi, ri, sorted(gets)])
                        else:
                            already["runs"] += 1
                    if marks_too:
                        mrpr = p.find(W + "pPr/" + W + "rPr")
                        revised = mark_in_revision(p)
                        if not (in_revs == "exclude" and revised) and rpr_selected(runs_pred, mrpr, revised):
                            msym = is_symbol_rpr(mrpr, p, sheet, preserve)
                            gets = resolver.gets_run(mrpr, p, run_asked, msym)
                            if not msym and caps_conflict(run_asked, lambda e, mrpr=mrpr, p=p: resolver.run_prop(mrpr, p, e)[0]):
                                raise JobException(f"{part} paragraph {bi} paragraph mark: {CAPS_CONFLICT}",
                                                   "conflict-property", {"part": part, "paragraph": bi})
                            if gets:
                                if tracked and revised:
                                    raise JobException(
                                        f"{part}: a selected paragraph mark is itself a pending revision; a tracked "
                                        f"formatting change on it would copy that revision into the snapshot",
                                        "tracked-format", {"part": part, "paragraph": bi})
                                t_marks.append([bi, sorted(gets)])
                            else:
                                already["marks"] += 1
                if para_asked:
                    gets = resolver.gets_para(p, para_asked)
                    if gets:
                        t_paras.append([bi, sorted(gets)])
                    else:
                        already["paragraphs"] += 1
            for bi, ri, _ in t_runs:
                rpr = groups[bi][ri].find(W + "rPr")
                if rpr is not None and rpr.find(W + "rPrChange") is not None:
                    if tracked:
                        raise JobException(f"{part}: a selected run already carries a pending tracked formatting "
                                           f"change (w:rPrChange); a second tracked change cannot be expressed",
                                           "tracked-format", {"part": part, "paragraph": bi})
                    older_r += 1
            for bi, _ in t_marks:
                mrpr = pars[bi].find(W + "pPr/" + W + "rPr")
                if mrpr is not None and mrpr.find(W + "rPrChange") is not None:
                    if tracked:
                        raise JobException(f"{part}: a selected paragraph mark already carries a pending tracked "
                                           f"formatting change (w:rPrChange)", "tracked-format",
                                           {"part": part, "paragraph": bi})
                    older_r += 1
            for bi, _ in t_paras:
                ppr = pars[bi].find(W + "pPr")
                if ppr is not None and ppr.find(W + "pPrChange") is not None:
                    if tracked:
                        raise JobException(f"{part} paragraph {bi} already carries a pending tracked paragraph "
                                           f"change (w:pPrChange)", "tracked-format", {"part": part, "paragraph": bi})
                    older_p += 1
            if t_runs or t_marks or t_paras:
                targets[part] = {"runs": t_runs, "marks": t_marks, "paras": t_paras}
                frag.intended_parts.append(part)
                frag.touches += [(part, str(bi), "rPr") for bi in sorted({b for b, _, _ in t_runs})]
                frag.touches += [(part, str(bi), "markRPr") for bi, _ in t_marks]
                frag.touches += [(part, str(bi), "pPr") for bi, _ in t_paras]
            frag.touches.append((part, "scope", "verify"))      # V10 re-resolves the selection over this part
            n_runs += len(t_runs)
            n_marks += len(t_marks)
            n_paras += len(t_paras)
        frag.property_assertions.append({
            "kind": "properties", "target": "direct", "scope": [scope] if isinstance(scope, str) else list(scope),
            "parts": parts, "select": {"runs": runs_pred, "paragraphs": para_pred},
            "set": {"run": run_set, "paragraph": para_set}, "preserve_fonts": preserve, "skip_sym_runs": skip_sym,
            "in_revisions": in_revs, "include_paragraph_marks": marks_too, "tracked": tracked, "author": author,
            "expected_runs": n_runs, "expected_marks": n_marks, "expected_paragraphs": n_paras})
        frag.transform_data = {"target": "direct", "targets": targets, "set": {"run": run_set, "paragraph": para_set},
                               "tracked": tracked, "author": author}
        n = n_runs + n_marks + n_paras
        if tracked and n:
            if n_runs + n_marks:
                frag.rev_delta["rPrChange"] = n_runs + n_marks
            if n_paras:
                frag.rev_delta["pPrChange"] = n_paras
            frag.rev_ids_needed = n
            frag.rev_id_base = max_revision_id(pkg) + 1
            frag.transform_data["use_date_utc"] = file_uses_date_utc(pkg)
        self._geometry(frag, run_asked, para_asked, bool(targets))
        frag.notes.append(f"{n_runs} run(s), {n_marks} paragraph mark(s) and {n_paras} paragraph(s) → "
                          f"{_describe(run_set, para_set)} across {len(targets)} part(s); "
                          f"{'tracked (w:rPrChange / w:pPrChange)' if tracked else 'plain'} — "
                          + ("explicit 'tracked'" if "tracked" in params else
                             "default for " + ("targeted" if tracked else "whole-document") + " formatting (§14.1)"))
        if already:
            frag.notes.append("already as asked, left alone: " + ", ".join(f"{v} {k}" for k, v in sorted(already.items())))
        if run_asked and set(run_asked) & CASE_ELEMENTS:
            frag.notes.append(f"caps/smallCaps never touch symbol runs (fonts {preserve}, w:sym runs): case-mapping a "
                              f"dingbat changes the glyph; the other run properties apply to them")
        if older_r or older_p:
            frag.flags.append(f"{older_r} changed run(s)/mark(s) and {older_p} paragraph(s) already carried a pending "
                              f"tracked formatting change (w:rPrChange / w:pPrChange) from an earlier reviewer; "
                              f"reject-all would restore that older snapshot.")
        return frag

    def _plan_styles(self, pkg, sheet, select, params, run_set, para_set, preserve, skip_sym) -> PlanFragment:
        sel = select["styles"]
        if not (isinstance(sel, dict) and set(sel) == {"ids"} and isinstance(sel["ids"], list) and sel["ids"]
                and all(isinstance(x, str) for x in sel["ids"])):
            raise JobException(f"styles selector must be {{'ids': [styleId, …]}}, got {sel!r}", "bad-params")
        if params.get("tracked"):
            raise JobException("style-definition changes are not tracked (Word does not track them either); omit "
                               "'tracked' or set it false", "bad-params")
        for k in ("in_revisions", "include_paragraph_marks"):
            if k in params:
                raise JobException(f"'{k}' applies to direct formatting, not to style definitions", "bad-params")
        if sheet.part is None:
            raise JobException("document has no styles part", "structure")
        ids = list(dict.fromkeys(sel["ids"]))
        missing = [i for i in ids if i not in sheet.styles]
        if missing:
            raise JobException(f"style id(s) {missing} not in this document", "not-found", {"missing": missing})
        types = {sid: sheet.styles[sid].get(W + "type", "paragraph") for sid in ids}
        bad = [sid for sid, t in types.items() if t not in ("paragraph", "character")]
        if bad:
            raise JobException(f"style(s) {bad} are not paragraph or character styles", "bad-params")
        if para_set:
            bad = [sid for sid, t in types.items() if t != "paragraph"]
            if bad:
                raise JobException(f"paragraph properties cannot go on character style(s) {bad}", "bad-params")
        run_asked, para_asked = run_elements(run_set), para_elements(para_set)
        changes: Dict[str, Dict[str, List[str]]] = {}
        for sid in ids:
            st = sheet.styles[sid]
            gets_r = PropResolver.style_gets(st, "rPr", run_asked)
            gets_p = PropResolver.style_gets(st, "pPr", para_asked) if types[sid] == "paragraph" else {}
            if gets_r or gets_p:
                changes[sid] = {"rPr": sorted(gets_r), "pPr": sorted(gets_p)}
        # certainty: resolve everything that uses a selected style, after the change, with its provenance
        override: Dict[Tuple[str, str], Any] = {}
        for sid in ids:
            override.update({(sid, e): v for e, v in run_asked.items()})
            if types[sid] == "paragraph":
                override.update({(sid, e): v for e, v in para_asked.items()})
        sim = PropResolver(sheet, override)
        affected, inherits, nearer, direct_ok = Counter(), Counter(), Counter(), Counter()
        refusals: List[Tuple[str, str]] = []

        def judge(val, prov, v, sid, e, sym, own_style, where):
            if prov == f"style:{sid}":
                if sym and e in CASE_ELEMENTS and v:
                    refusals.append(("conflict-property", f"{where}: w:{e} from style {sid} would case-map a symbol "
                                                          f"run (the glyph would change) — set it as direct formatting "
                                                          f"instead, which leaves symbol runs alone"))
                elif own_style is not None and own_style != sid:
                    inherits[(sid, own_style, e)] += 1
                else:
                    affected[(sid, e)] += 1
            elif prov == "ambiguous":
                refusals.append(("toggle-ambiguous", f"{where}: w:{e} would be specified at several style levels "
                                                     f"(Word XORs them — not modelled); set it as direct formatting "
                                                     f"instead"))
            elif prov == "direct":
                if val != v:
                    refusals.append(("conflict-property", f"{where}: direct formatting (w:{e} {val!r}) would keep "
                                                          f"its own value over style {sid}; set it as direct "
                                                          f"formatting on these paragraphs instead, or clear it in Word"))
                else:
                    direct_ok[(sid, e)] += 1
            else:
                nearer[(sid, e, prov)] += 1

        for part in pkg.text_parts():
            root = pkg.xml(part)
            pars = paragraphs(root)
            groups = runs_by_paragraph(root, pars)
            for bi, p in enumerate(pars):
                pstyle = sheet.paragraph_style_id(p)
                pchain = [st.get(W + "styleId") for st in sheet.lineage(pstyle)]
                holders = [(r.find(W + "rPr"), f"run {ri}", r.find(W + "sym") is not None)
                           for ri, r in enumerate(groups[bi])]
                holders.append((p.find(W + "pPr/" + W + "rPr"), "paragraph mark", False))
                for sid in ids:
                    for rpr, label, has_sym in holders:
                        if types[sid] == "paragraph":
                            uses, own = sid in pchain, pstyle
                        else:
                            rs = rpr.find(W + "rStyle") if rpr is not None else None
                            rsid = rs.get(W + "val") if rs is not None else sheet.defaults.get("character")
                            uses, own = sid in [st.get(W + "styleId") for st in sheet.lineage(rsid)], rsid
                        if not uses:
                            continue
                        sym = is_symbol_rpr(rpr, p, sheet, preserve) or (skip_sym and has_sym)
                        for e, v in run_asked.items():
                            val, prov = sim.run_prop(rpr, p, e)
                            judge(val, prov, v, sid, e, sym, own, f"{part} paragraph {bi} {label}")
                        if not sym and caps_conflict(run_asked, lambda e, rpr=rpr, p=p: sim.run_prop(rpr, p, e)[0]):
                            refusals.append(("conflict-property", f"{part} paragraph {bi} {label}: {CAPS_CONFLICT}"))
                    if types[sid] == "paragraph" and sid in pchain:
                        for e, v in para_asked.items():
                            val, prov = sim.para_prop(p, e)
                            judge(val, prov, v, sid, e, False, pstyle, f"{part} paragraph {bi}")
        if refusals:
            code, reason = refusals[0]
            raise JobException(f"style change not certain: {reason}" + (f" (+{len(refusals) - 1} more)"
                                                                        if len(refusals) > 1 else ""),
                               code, {"problems": [r for _, r in refusals[:20]], "styles": ids})
        frag = self.fragment(select, params)
        frag.property_assertions.append({
            "kind": "properties", "target": "styles", "styles_part": sheet.part, "select": {"ids": ids},
            "set": {"run": run_set, "paragraph": para_set}, "preserve_fonts": preserve, "skip_sym_runs": skip_sym,
            "expected_ids": list(changes), "tracked": False})
        frag.transform_data = {"target": "styles", "styles_part": sheet.part, "changes": changes,
                               "set": {"run": run_set, "paragraph": para_set}}
        if changes:
            frag.intended_parts = [sheet.part]
            for sid, ch in changes.items():
                if ch["rPr"]:
                    frag.touches.append((sheet.part, f"style:{sid}", "rPr"))
                if ch["pPr"]:
                    frag.touches.append((sheet.part, f"style:{sid}", "pPr"))
        self._geometry(frag, run_asked, para_asked, bool(changes))
        frag.notes.append(f"style(s) {list(changes) or 'none'} → {_describe(run_set, para_set)} (definitions only; "
                          f"not tracked — Word does not track style changes)"
                          + (f"; already as asked: {[s for s in ids if s not in changes]}"
                             if any(s not in changes for s in ids) else ""))
        for sid in ids:
            n = sum(c for (s, _), c in affected.items() if s == sid)
            frag.notes.append(f"{sid}: {n} run(s)/mark(s)/paragraph(s) take the value from the style")
        for (sid, own, e), c in sorted(inherits.items()):
            frag.notes.append(f"{own} (based on {sid}) inherits the change to {e}: {c} run(s)/mark(s)/paragraph(s)")
        for (sid, e, prov), c in sorted(nearer.items()):
            frag.notes.append(f"{prov[6:] if prov.startswith('style:') else prov} keeps its own {e} over {sid}: "
                              f"{c} run(s)/mark(s)/paragraph(s) do not change")
        for (sid, e), c in sorted(direct_ok.items()):
            frag.notes.append(f"{c} run(s)/mark(s)/paragraph(s) already show {e} as asked by direct formatting")
        if pkg.has("word/stylesWithEffects.xml"):
            frag.flags.append("word/stylesWithEffects.xml (Word 2010's compatibility copy of the styles) is not "
                              "updated; Word 2013+ ignores it.")
        return frag

    @staticmethod
    def _geometry(frag: PlanFragment, run_asked: Dict[str, Any], para_asked: Dict[str, bool], changes: bool) -> None:
        if changes and (set(run_asked) & REFLOW_ELEMENTS or para_asked):
            frag.geometry_reference, frag.geometry_reason = "none", REFLOW_REASON

    # ---- apply ---------------------------------------------------------------------------------------------------

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        run_asked, para_asked = run_elements(td["set"]["run"]), para_elements(td["set"]["paragraph"])
        if td["target"] == "styles":
            if not td["changes"]:
                return
            root = pkg.xml(td["styles_part"])
            by_id = {st.get(W + "styleId"): st for st in root.findall(W + "style")}
            for sid, ch in td["changes"].items():
                if ch["rPr"]:
                    rpr = get_or_add(by_id[sid], "rPr", STYLE_ORDER)
                    for e in ch["rPr"]:
                        write_element(rpr, e, run_asked[e], RPR_ORDER)
                if ch["pPr"]:
                    ppr = get_or_add(by_id[sid], "pPr", STYLE_ORDER)
                    for e in ch["pPr"]:
                        write_element(ppr, e, para_asked[e], PPR_ORDER)
            pkg.write_xml(td["styles_part"], root)
            return
        stamp = RevisionStamp.now(td["author"], td.get("use_date_utc", False)) if td["tracked"] else None
        next_id = td.get("first_rev_id", 0)
        for part, t in td["targets"].items():
            root = pkg.xml(part)
            pars = paragraphs(root)
            groups = runs_by_paragraph(root, pars)
            for bi, ri, elements in t["runs"]:
                next_id = _write_rpr(run_rpr(groups[bi][ri]), elements, run_asked, stamp, next_id)
            for bi, elements in t["marks"]:
                next_id = _write_rpr(mark_rpr(pars[bi]), elements, run_asked, stamp, next_id)
            for bi, flags in t["paras"]:
                ppr = paragraph_ppr(pars[bi])
                chg = None
                if stamp is not None:
                    snap = [c for c in ppr if c.tag not in (W + "rPr", W + "sectPr", W + "pPrChange")]
                    chg = property_change("pPrChange", "pPr", snap, stamp, next_id)
                    next_id += 1
                for e in flags:
                    write_element(ppr, e, para_asked[e], PPR_ORDER)
                if chg is not None:
                    ppr.append(chg)
            pkg.write_xml(part, root)
