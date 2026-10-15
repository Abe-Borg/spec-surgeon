"""Op: set_font — set w:rFonts (and/or the size, w:sz) on selected runs (and, by default, their paragraph marks).

Formatting mode: zero text diffs. Symbol runs (Wingdings bullets, w:sym characters, runs whose character or
paragraph style uses a symbol font) are never touched — changing their font changes the glyph. Runs inside
pending revisions are included unless ``in_revisions: "exclude"``.

Tracked or plain (decision §14.1): plain by default for whole-document selections, tracked (w:rPrChange holding a
snapshot of the old rPr) by default when the selector narrows to particular paragraphs; ``tracked`` overrides.

Size (``size``, points; ``size_from``): set only on selected runs/marks that carry their own w:sz (listed in
``size_from`` when given) — a run without one inherits its size from its styles, which set_style_font changes; adding
direct sizes would override them. ``only_where_defined`` does the same for the font: it is then set only on runs that
carry a direct font (``has_direct_font``), so one op can fix both direct fonts and direct sizes ("match the template's
font"). A run gets whichever of the two applies to it; one that gets neither is not selected.
A tracked change on a run that already carries someone's w:rPrChange is an exception (two snapshots cannot
be expressed); a plain change on such a run is made and flagged (reject-all would restore that older snapshot).
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ..package import W, DocxPackage
from ..ooxml import (RevisionStamp, THEME_FONT_ATTR, block_full_text, file_uses_date_utc, mark_rpr, max_revision_id,
                     own_size, paragraphs, property_change, run_rpr, set_rfonts, set_size)
from . import register
from .base import JobException, Op, PlanFragment
from .replace_text import default_author
from .selectors import (DEFAULT_PRESERVE_FONTS, FONT_PARAM_DOC, StyleSheet, check_paragraph_selector,
                        check_run_selector, font_values, has_direct_font, in_revision, is_symbol_rpr, is_targeted,
                        mark_in_revision,
                        paragraph_selected, rpr_selected, runs_by_paragraph, scope_parts, size_applies, size_values)

FONT_REFLOW = "a font change re-measures every glyph it touches; text reflows, so the source render is no layout reference"


@register
class SetFont(Op):
    name = "set_font"
    mode = "formatting"
    description = ("Set the font (w:rFonts) and/or size (w:sz, only where a run already sets one) of selected runs "
                   "and their paragraph marks; symbol/dingbat runs are left alone. Compose with set_style_font + "
                   "set_doc_defaults_font for 'change all fonts'.")
    params_schema = {
        "type": "object",
        "properties": {
            **FONT_PARAM_DOC,
            "skip_sym_runs": {"type": "boolean", "default": True, "description": "leave runs holding a w:sym alone"},
            "in_revisions": {"type": "string", "enum": ["include", "exclude"], "default": "include",
                             "description": "runs inside pending w:ins/w:del: change them too, or leave them"},
            "include_paragraph_marks": {"type": "boolean", "default": True},
            "only_where_defined": {"type": "boolean", "default": False,
                                   "description": "set the font only on selected runs that carry a direct font "
                                                  "(the size is always set only where a run carries its own)"},
            "tracked": {"type": "boolean", "description": "w:rPrChange per change; default: plain for whole-document "
                                                          "selections, tracked when paragraphs are targeted"},
            "author": {"type": "string"},
        },
    }
    select_schema = {"type": "object", "properties": {
        "scope": {"type": "array", "items": {"type": "string", "enum": ["document", "headers", "footers", "footnotes", "endnotes", "comments"]}, "default": ["document"]},
        "runs": {"description": "'all' or {has_direct_font, font_in, not_in_revisions}"},
        "paragraphs": {"description": "'all' or {style_in, text_regex, in_table, is_empty, index_range}"}}}
    verification_profile = {
        "V2": "zero text diffs", "V4": "tracked: +1 w:rPrChange per run/mark changed",
        "V10": "run_fonts: selection re-resolved, count matched, only rFonts (and w:sz where the run set one) changed, "
               "theme attrs cleared; residual identical",
        "V7": "none — a font change reflows the text",
    }

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select = dict(select or {})
        unknown = set(select) - {"scope", "runs", "paragraphs"}
        if unknown:
            raise JobException(f"set_font: unknown selector key(s) {sorted(unknown)}", "bad-params")
        values = font_values(params)
        size = size_values(params)
        runs_pred, para_pred = select.get("runs", "all"), select.get("paragraphs")
        check_run_selector(runs_pred)
        check_paragraph_selector(para_pred)
        scope = select.get("scope", ["document"])
        parts = scope_parts(pkg, scope)
        preserve = list(params.get("preserve_fonts", DEFAULT_PRESERVE_FONTS))
        clear = bool(params.get("clear_theme_fonts", True))
        skip_sym = bool(params.get("skip_sym_runs", True))
        in_revs = params.get("in_revisions", "include")
        if in_revs not in ("include", "exclude"):
            raise JobException("in_revisions must be 'include' or 'exclude'", "bad-params")
        marks_too = bool(params.get("include_paragraph_marks", True))
        only = bool(params.get("only_where_defined", False))
        tracked = bool(params.get("tracked", is_targeted(select)))
        author = params.get("author") or default_author()
        sheet = StyleSheet(pkg)

        frag = self.fragment(select, {**params, "tracked": tracked, "author": author})
        targets: Dict[str, Dict[str, List]] = {}
        n_runs = n_marks = n_sized = 0

        def changes(rpr) -> Tuple[bool, bool]:
            """(the font goes on this run/mark, the size does)."""
            return (bool(values) and (not only or has_direct_font(rpr)), size_applies(own_size(rpr), size))
        older_snapshots = 0
        theme_blocked: List[str] = []
        for part in parts:
            root = pkg.xml(part)
            pars = paragraphs(root)
            groups = runs_by_paragraph(root, pars)
            t_runs: List[Tuple[int, int]] = []
            t_marks: List[int] = []
            r_what: List[List[bool]] = []           # per target run / mark: [font, size]
            m_what: List[List[bool]] = []
            for bi, p in enumerate(pars):
                if not paragraph_selected(para_pred, p, bi, block_full_text(p), sheet):
                    continue
                for ri, r in enumerate(groups[bi]):
                    rpr = r.find(W + "rPr")
                    revised = in_revision(r, p)
                    if in_revs == "exclude" and revised:
                        continue
                    if skip_sym and r.find(W + "sym") is not None:
                        continue
                    if is_symbol_rpr(rpr, p, sheet, preserve) or not rpr_selected(runs_pred, rpr, revised):
                        continue
                    if not any(changes(rpr)):
                        continue                         # nothing of this op applies to it (it inherits both)
                    t_runs.append((bi, ri))
                    r_what.append(list(changes(rpr)))
                if marks_too:
                    mrpr = p.find(W + "pPr/" + W + "rPr")
                    revised = mark_in_revision(p)
                    if not (in_revs == "exclude" and revised) and not is_symbol_rpr(mrpr, p, sheet, preserve) \
                            and rpr_selected(runs_pred, mrpr, revised) and any(changes(mrpr)):
                        t_marks.append(bi)
                        m_what.append(list(changes(mrpr)))
            t_what = r_what + m_what                 # same order as the runs then the marks
            if tracked and any(mark_in_revision(pars[bi]) for bi in t_marks):
                raise JobException(f"{part}: a selected paragraph mark is itself a pending revision; a tracked "
                                   f"formatting change on it would copy that revision into the snapshot",
                                   "tracked-format", {"part": part})
            chosen = [groups[bi][ri].find(W + "rPr") for bi, ri in t_runs] + \
                     [pars[bi].find(W + "pPr/" + W + "rPr") for bi in t_marks]
            for rpr, (font_here, _) in zip(chosen, t_what):
                if rpr is None:
                    continue
                if rpr.find(W + "rPrChange") is not None:
                    if tracked:
                        raise JobException(f"{part}: a selected run already carries a pending tracked formatting "
                                           f"change (w:rPrChange); a second tracked change cannot be expressed",
                                           "tracked-format", {"part": part})
                    older_snapshots += 1
                rf = rpr.find(W + "rFonts")
                if font_here and not clear and rf is not None and any(rf.get(W + THEME_FONT_ATTR[s]) for s in values):
                    theme_blocked.append(part)
            n_sized += sum(z for _, z in t_what)
            if t_runs or t_marks:
                targets[part] = {"runs": t_runs, "marks": t_marks, "what": t_what}
                frag.intended_parts.append(part)
                frag.touches += [(part, str(bi), "rPr") for bi in sorted({b for b, _ in t_runs})]
                frag.touches += [(part, str(bi), "markRPr") for bi in t_marks]
            frag.touches.append((part, "scope", "verify"))      # V10 re-resolves the selection over this part
            n_runs += len(t_runs)
            n_marks += len(t_marks)
        if theme_blocked:
            raise JobException("selected runs carry theme font attributes that would override the explicit font; "
                               "set clear_theme_fonts (the default) or narrow the selection", "theme-font",
                               {"parts": sorted(set(theme_blocked))})

        frag.property_assertions.append({
            "kind": "run_fonts", "scope": list(scope) if not isinstance(scope, str) else [scope], "parts": parts,
            "select": {"runs": runs_pred, "paragraphs": para_pred}, "preserve_fonts": preserve,
            "skip_sym_runs": skip_sym, "in_revisions": in_revs, "set": values, "clear_theme": clear,
            "include_paragraph_marks": marks_too, "tracked": tracked, "author": author,
            "expected_runs": n_runs, "expected_marks": n_marks, "size": size, "expected_sized": n_sized,
            "only_where_defined": only})
        n = n_runs + n_marks
        frag.transform_data = {"targets": targets, "values": values, "clear_theme": clear, "tracked": tracked,
                               "author": author, "size": size}
        if tracked and n:
            frag.rev_delta["rPrChange"] = n
            frag.rev_ids_needed = n
            frag.rev_id_base = max_revision_id(pkg) + 1
            frag.transform_data["use_date_utc"] = file_uses_date_utc(pkg)
        if n:
            frag.geometry_reference, frag.geometry_reason = "none", FONT_REFLOW
        what = [str(values) + (" where a run sets its own font" if only else "")] if values else []
        if size:
            what.append(f"size {size['sz'] / 2:g} pt on the {n_sized} that set their own size"
                        + (f" ({', '.join(f'{h / 2:g}' for h in size['from'])} pt)" if size["from"] else ""))
        frag.notes.append(f"{n_runs} run(s) and {n_marks} paragraph mark(s) → {' and '.join(what)} across "
                          f"{len(targets)} part(s); "
                          f"{'tracked (w:rPrChange)' if tracked else 'plain'} — "
                          + ("explicit 'tracked'" if "tracked" in params else
                             "default for " + ("targeted" if tracked else "whole-document") + " formatting (§14.1)"))
        frag.notes.append(f"Symbol fonts preserved: {preserve}; runs inside revisions: {in_revs}; "
                          f"theme attributes {'cleared' if clear else 'kept'} on the slots set.")
        if older_snapshots:
            frag.flags.append(f"{older_snapshots} changed run(s) already carried a pending tracked formatting change "
                              f"(w:rPrChange) from an earlier reviewer; reject-all would restore that older snapshot.")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        stamp = None
        next_id = td.get("first_rev_id", 0)
        if td["tracked"]:
            stamp = RevisionStamp.now(td["author"], td.get("use_date_utc", False))
        for part, t in td["targets"].items():
            root = pkg.xml(part)
            pars = paragraphs(root)
            groups = runs_by_paragraph(root, pars)
            changed = [run_rpr(groups[bi][ri]) for bi, ri in t["runs"]] + [mark_rpr(pars[bi]) for bi in t["marks"]]
            for rpr, (font_here, size_here) in zip(changed, t["what"]):
                snapshot = [c for c in rpr if c.tag != W + "rPrChange"] if stamp else None
                chg = None
                if stamp:
                    chg = property_change("rPrChange", "rPr", snapshot, stamp, next_id)
                    next_id += 1
                if font_here:
                    set_rfonts(rpr, td["values"], td["clear_theme"])
                if size_here:
                    set_size(rpr, td["size"]["sz"])
                if chg is not None:
                    rpr.append(chg)
            pkg.write_xml(part, root)
