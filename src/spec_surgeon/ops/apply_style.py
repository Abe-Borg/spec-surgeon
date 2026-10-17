"""Op: apply_style — give selected paragraphs a paragraph style (built-in batch 4, item 30 ★ —
docs/BUILTIN_OPS_BATCH4_DESIGN.md §6).

Formatting mode: zero text diffs. The paragraphs are chosen with the whole paragraph selector grammar — by appearance
(``has_bold``, ``is_all_caps``), by pattern (``text_regex``, ``after_heading_regex``, ``between_paragraphs``), by their
current style (``style_in``) … — and get ``w:pStyle`` = the asked style.

Parameters: ``style`` — the style's id or name (resolved by ``style_import.resolve_style``: the exact ``w:styleId``,
else the one style whose ``w:name`` matches, casefolded; two → ``style-mismatch``); ``template`` — where ``style`` comes
from when the document lacks it: imported with its closure and numbering (``style_import``, design §5), the template an
input pinned by md5 (rule 12); ``existing`` (``keep`` | ``replace``, with ``template``): a style the document already
has keeps the document's definition, or takes the template's; ``direct_formatting``: ``keep`` (default) — the
paragraphs' own formatting stays (it wins over the style, as in Word) — or ``clear``: the paragraphs' direct paragraph
formatting and their runs' and marks' direct run formatting are cleared by clear_direct_formatting's DEFAULT rules
(``clear_formatting.ClearSettings()``), so the paragraphs follow the new style completely; ``tracked`` / ``author``.

Definitions (the verifier's own reading is verify/applystyle.py):

* the style must be a paragraph style (``style-mismatch`` in the document; ``bad-params`` when the template's is not, or
  the template lacks it); not found and no template → ``missing-style``;
* a paragraph's **style** = its ``w:pStyle``, else the default paragraph style; it is **restyled** when that is not the
  asked style; with ``clear`` its targets are ``clear_formatting.paragraph_clearing`` under the default settings;
* a paragraph neither restyled nor (with ``clear``) cleared is left alone and not counted ("already as asked");
* a restyled paragraph's ``w:pStyle`` is set (inserted first in its pPr; a pPr is created first in the paragraph);
  the cleared children go (``clear_formatting.clear_paragraph``, the pStyle written through its ``ppr_edit``);
* tracked (§14.1: targeted → tracked): one ``w:pPrChange`` per changed paragraph (snapshot = the source pPr without rPr,
  sectPr, pPrChange), one ``w:rPrChange`` per cleared run or mark; existing change markers as clear_direct_formatting
  (tracked → ``tracked-format``, plain → flagged). A paragraph with its own ``w:numPr`` keeps it (flagged: it overrides
  the style's numbering).

Verified by V10's ``applied_style`` kind (verify/applystyle.py) — and, when the style is imported, by
``styles_imported`` (verify/styleimport.py).
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from ..package import W, DocxPackage
from ..ooxml import RevisionStamp, block_full_text, file_uses_date_utc, max_revision_id, paragraphs
from . import register
from .base import JobException, Op, PlanFragment
from .clear_formatting import (ClearSettings, ParagraphClearing, changes_needed, check_markers, clear_paragraph,
                               paragraph_clearing)
from .replace_text import default_author
from .selectors import StyleSheet, check_paragraph_selector, is_targeted, paragraph_selected, runs_by_paragraph, scope_parts
from .style_import import (TemplateStyles, apply_import, check_template_unchanged, plan_import, resolve_style,
                           style_type)

SCOPE_SCHEMA = {"type": "array", "items": {"type": "string", "enum": ["document", "headers", "footers", "footnotes",
                                                                       "endnotes", "comments"]}, "default": ["document"]}
PARAM_KEYS = {"style", "template", "existing", "direct_formatting", "tracked", "author"}
REFLOW_REASON = "a new paragraph style changes the paragraphs' fonts, spacing and indents; text reflows by design"


def _clearing_data(d: ParagraphClearing) -> Dict[str, Any]:
    return {"runs": [[ri, list(n)] for ri, n in d.runs], "mark": list(d.mark) if d.mark else None, "ppr": list(d.ppr)}


def _clearing_from(data: Dict[str, Any]) -> ParagraphClearing:
    return ParagraphClearing(runs=[(ri, list(n)) for ri, n in data["runs"]], mark=data["mark"], ppr=list(data["ppr"]))


def set_pstyle(style_id: str):
    """A ``ppr_edit`` for ``clear_paragraph``: w:pStyle = ``style_id``, first in the pPr."""
    def edit(ppr) -> None:
        ps = ppr.find(W + "pStyle")
        if ps is None:
            from lxml import etree
            ps = etree.Element(W + "pStyle")
            ppr.insert(0, ps)
        ps.attrib.clear()
        ps.set(W + "val", style_id)
    return edit


@register
class ApplyStyle(Op):
    name = "apply_style"
    mode = "formatting"
    description = ("Give the selected paragraphs a paragraph style — chosen by appearance (bold, all caps), by pattern "
                   "(text, 'after a heading', between two paragraphs) or by their current style; the style from the "
                   "document or imported from a template (with its numbering). direct_formatting 'clear' also removes "
                   "the paragraphs' own formatting so they follow the style completely. Adds no characters.")
    params_schema = {"type": "object", "required": ["style"], "properties": {
        "style": {"type": "string", "description": "the paragraph style's id or name (as the profiles show it)"},
        "template": {"type": "string", "description": "a .docx/.dotx holding the style when the document lacks it: "
                                                      "imported with the styles it is based on, links to and is "
                                                      "followed by, and its numbering"},
        "existing": {"type": "string", "enum": ["keep", "replace"], "default": "keep",
                     "description": "with template: a style the document already has keeps the document's definition "
                                    "(keep) or takes the template's (replace); the style must be in the template"},
        "direct_formatting": {"type": "string", "enum": ["keep", "clear"], "default": "keep",
                              "description": "keep the paragraphs' own formatting (it wins over the style), or clear "
                                             "it by clear_direct_formatting's default rules"},
        "tracked": {"type": "boolean", "description": "w:pPrChange per paragraph (w:rPrChange per cleared run); "
                                                      "default: plain for whole-document selections, tracked when "
                                                      "paragraphs are targeted"},
        "author": {"type": "string"}}}
    select_schema = {"type": "object", "properties": {
        "scope": SCOPE_SCHEMA,
        "paragraphs": {"description": "'all' or {style_in, text_regex, in_table, is_empty, index_range, has_bold, "
                                      "is_all_caps, after_heading_regex, between_paragraphs}"}}}
    verification_profile = {
        "V2": "zero text diffs", "V4": "tracked: +1 w:pPrChange per changed paragraph, +1 w:rPrChange per cleared run/mark",
        "V10": "applied_style: selection, the style and each paragraph's change re-derived from the source; each changed "
               "paragraph's pPr = the source's with w:pStyle set (and, with clear, the cleared children gone), cleared "
               "runs and marks exactly; styles_imported when the style comes from a template; residual identical",
        "V7": "none — a new style reflows the paragraphs"}

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select = dict(select or {})
        if set(select) - {"scope", "paragraphs"}:
            raise JobException(f"apply_style: unknown selector key(s) {sorted(set(select) - {'scope', 'paragraphs'})}",
                               "bad-params")
        unknown = set(params) - PARAM_KEYS
        if unknown:
            raise JobException(f"apply_style: unknown parameter(s) {sorted(unknown)}; known: {sorted(PARAM_KEYS)}",
                               "bad-params")
        ref = params.get("style")
        if not isinstance(ref, str) or not ref.strip():
            raise JobException("apply_style needs 'style': a paragraph style's id or name", "bad-params")
        direct = params.get("direct_formatting", "keep")
        if direct not in ("keep", "clear"):
            raise JobException("direct_formatting must be 'keep' or 'clear'", "bad-params")
        existing = params.get("existing", "keep")
        if existing not in ("keep", "replace"):
            raise JobException("existing must be 'keep' or 'replace'", "bad-params")
        template = params.get("template")
        if template is not None and (not isinstance(template, str) or not template.strip()):
            raise JobException("template must be the path of a .docx/.dotx file", "bad-params")
        if "existing" in params and template is None:
            raise JobException("'existing' applies only with a template", "bad-params")
        if "tracked" in params and not isinstance(params["tracked"], bool):
            raise JobException("tracked must be true or false", "bad-params")
        pred = select.get("paragraphs", "all")
        check_paragraph_selector(pred)
        scope = select.get("scope", ["document"])
        parts = scope_parts(pkg, scope)
        tracked = bool(params.get("tracked", is_targeted(select)))
        author = params.get("author") or default_author()
        sheet = StyleSheet(pkg)
        if sheet.part is None:
            raise JobException("this file has no styles part; no paragraph style can be applied", "structure")
        frag = self.fragment(select, {**params, "tracked": tracked, "author": author})

        # ---- the style: the document's, or imported from the template (map_styles' resolution: with keep the file's
        # own style when it has one, with replace always the template's) ----
        did = resolve_style(sheet.styles, ref, "this file")
        tpl = TemplateStyles.load(template) if template is not None else None
        tid = None
        if tpl is not None:
            tid = resolve_style(tpl.sheet.styles, ref, "the template")
            if tid is None:
                raise JobException(f"the template has no style {ref!r}", "bad-params", {"style": ref})
            if style_type(tpl.sheet.styles[tid]) != "paragraph":
                raise JobException(f"style {ref!r} of the template is a {style_type(tpl.sheet.styles[tid])} style; "
                                   f"apply_style gives paragraphs a paragraph style", "bad-params", {"style": ref})
        if existing == "replace" or (did is None and tid is not None):
            sid, requested = tid, [tid]
        elif did is not None:
            sid, requested = did, []
        else:
            raise JobException(f"this file has no style {ref!r} (by id or name); give the template that holds it",
                               "missing-style", {"style": ref})
        if sid in sheet.styles and style_type(sheet.styles[sid]) != "paragraph" and not requested:
            raise JobException(f"style {ref!r} is a {style_type(sheet.styles[sid])} style in this file; apply_style "
                               f"gives paragraphs a paragraph style", "style-mismatch", {"style": sid})
        imp = plan_import(pkg, tpl, requested, existing) if requested else None
        if sid in sheet.styles and not requested:
            frag.touches.append((sheet.part, f"style:{sid}", "verify"))   # applied by name: a removal beside conflicts

        # ---- the paragraphs ----
        settings = ClearSettings() if direct == "clear" else None
        targets: Dict[str, List[list]] = {}
        already = 0
        n_paras = n_restyled = n_runs = n_marks = rev_ids = 0
        older_r = older_p = own_numbering = 0
        uses: set = set()
        for part in parts:
            root = pkg.xml(part)
            pars = paragraphs(root)
            groups = runs_by_paragraph(root, pars)
            chosen: List[list] = []
            for bi, p in enumerate(pars):
                if not paragraph_selected(pred, p, bi, block_full_text(p), sheet, all_pars=pars):
                    continue
                old = sheet.paragraph_style_id(p)
                restyle = old != sid
                d = paragraph_clearing(p, groups[bi], sheet, settings) if settings else ParagraphClearing()
                # which paragraphs name which style (rule 9, batch 4): this one gains a use of the style and, restyled,
                # its old style loses one — mapping the style away (map_styles, uses-) or a selection by either style
                # beside it is a plan-time conflict; an already-styled paragraph is a read (another op restyling it)
                uses.add((sheet.part, f"style:{sid}", "uses+"))
                if restyle and old:
                    uses.add((sheet.part, f"style:{old}", "uses-"))
                if not restyle:
                    frag.touches.append((part, str(bi), "verify-props:pStyle"))
                if not restyle and not d.changed:
                    already += 1
                    continue
                where = f"{part} paragraph {bi}"
                o_r, o_p = check_markers(p, groups[bi], d, tracked, where,
                                         ppr_changes=restyle or bool(d.ppr))
                older_r, older_p = older_r + o_r, older_p + o_p
                if restyle and p.find(f"{W}pPr/{W}numPr") is not None:
                    own_numbering += 1
                chosen.append([bi, restyle, _clearing_data(d)])
                rev_ids += changes_needed(d, ppr_changes=restyle)
                n_paras += int(restyle or bool(d.ppr))
                n_restyled += int(restyle)
                n_runs += len(d.runs)
                n_marks += int(bool(d.mark))
                if restyle or d.ppr:
                    frag.touches.append((part, str(bi), "pPr"))
                if restyle:
                    frag.touches.append((part, str(bi), "pStyle"))
                frag.touches += [(part, str(bi), name) for name in d.ppr]     # per element: family reads conflict
                if d.runs:
                    frag.touches.append((part, str(bi), "rPr"))
                if d.mark:
                    frag.touches.append((part, str(bi), "markRPr"))
            if chosen:
                targets[part] = chosen
                frag.intended_parts.append(part)
            frag.touches.append((part, "scope", "verify"))          # V10 re-resolves the selection over this part
        frag.touches += sorted(uses)

        # ---- the record ----
        frag.property_assertions.append({
            "kind": "applied_style", "parts": parts, "select": select,
            "params": {k: params[k] for k in ("style", "template", "existing", "direct_formatting", "tracked")
                       if k in params},
            "style_id": sid, "template": tpl.path if tpl is not None else None,
            "template_md5": tpl.md5 if tpl is not None else None, "requested": requested,
            "author": author, "expected_paragraphs": n_paras, "expected_restyled": n_restyled,
            "expected_runs": n_runs, "expected_marks": n_marks,
            "changed": {part: [[bi, restyle] for bi, restyle, _ in v] for part, v in targets.items()}})
        frag.transform_data = {"targets": targets, "style_id": sid, "tracked": tracked, "author": author,
                               "import": imp.transform_data() if imp is not None and imp.changes else None,
                               "template": tpl.path if tpl is not None else None,
                               "template_md5": tpl.md5 if tpl is not None else None}
        if imp is not None and imp.changes:
            frag.intended_parts += [p for p in imp.intended_parts() if p not in frag.intended_parts]
            frag.touches += imp.touches()
            frag.property_assertions.append(imp.assertion())
            frag.notes += imp.notes
            frag.flags += imp.flags
        if tracked and rev_ids:
            if n_paras:
                frag.rev_delta["pPrChange"] = n_paras
            if n_runs + n_marks:
                frag.rev_delta["rPrChange"] = n_runs + n_marks
            frag.rev_ids_needed = rev_ids
            frag.rev_id_base = max_revision_id(pkg) + 1
            frag.transform_data["use_date_utc"] = file_uses_date_utc(pkg)
        if targets or (imp is not None and imp.changes):
            frag.geometry_reference, frag.geometry_reason = "none", REFLOW_REASON
        frag.notes.append(f"{n_restyled} paragraph(s) get style {sid}"
                          + (f"; direct formatting cleared on {n_paras} paragraph(s), {n_runs} run(s), {n_marks} mark(s) "
                             f"(clear_direct_formatting's default rules)" if settings else
                             "; their own direct formatting stays (it wins over the style)")
                          + f"; {'tracked' if tracked else 'plain'} — "
                          + ("explicit 'tracked'" if "tracked" in params else
                             "default for " + ("targeted" if tracked else "whole-document") + " formatting (§14.1)"))
        if already:
            frag.notes.append(f"already as asked, left alone: {already} paragraph(s)")
        if own_numbering:
            frag.flags.append(f"{own_numbering} restyled paragraph(s) carry their own numbering (direct w:numPr), which "
                              f"overrides the style's numbering; clear it in Word if the style's list should number "
                              f"them.")
        if older_r or older_p:
            frag.flags.append(f"{older_r} changed run(s)/mark(s) and {older_p} paragraph(s) already carried a pending "
                              f"tracked formatting change from an earlier reviewer; reject-all would restore that older "
                              f"snapshot.")
        if not targets and not (imp is not None and imp.changes):
            frag.notes.append("nothing to change in this file")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if td.get("template") and not td.get("import"):
            # rule 12: the plan read the template (the style's resolution) even when nothing is copied from it
            check_template_unchanged(td["template"], td["template_md5"])
        apply_import(pkg, td.get("import"))
        stamp = RevisionStamp.now(td["author"], td.get("use_date_utc", False)) if td["tracked"] else None
        next_id = td.get("first_rev_id", 0)
        edit = set_pstyle(td["style_id"])
        for part, chosen in td["targets"].items():
            root = pkg.xml(part)
            pars = paragraphs(root)
            groups = runs_by_paragraph(root, pars)
            for bi, restyle, data in chosen:
                next_id = clear_paragraph(pars[bi], groups[bi], _clearing_from(data), stamp, next_id,
                                          ppr_edit=edit if restyle else None)
            pkg.write_xml(part, root)
