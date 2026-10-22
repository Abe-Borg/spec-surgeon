"""Op: apply_table_style — give the selected tables a table style (built-in batch 5, item 38 —
docs/BUILTIN_OPS_BATCH5_DESIGN.md §5.3).

Formatting mode: zero text diffs, never tracked (``tracked: true`` → ``bad-params``). The tables are chosen by the table
selector (``ops/table_select.py``: ``select.scope`` and ``select.tables``; default every table of the body).

Parameters: ``style`` — the table style's id or name (resolved by ``style_import.resolve_style``: the exact
``w:styleId``, else the one style whose ``w:name`` matches, whitespace collapsed and casefolded; two → ``style-mismatch``);
``template`` — where ``style`` comes from when the document lacks it: imported with what it needs by batch 4's style
import (``style_import.plan_import`` — its basedOn/link/next closure), the template an input pinned by md5 (rule 12);
``existing`` (``keep`` | ``replace``, with ``template``) — apply_style's resolution: with ``keep`` the document's own style
when it has one, else the template's; with ``replace`` always the template's; ``direct_formatting``: ``keep`` (default)
or ``clear`` — the table's own ``w:tblBorders``, ``w:shd`` and ``w:tblCellMar``, the same three in every own row's
``w:tblPrEx`` (a row's exceptions to the table) and every own cell's ``w:tcBorders``, ``w:shd`` and ``w:tcMar`` go, so the
style shows. ``w:tblLook`` (which conditional parts show) is left as it is.

Definitions (the verifier's own reading is verify/applytablestyle.py):

* the style must be a table style (``style-mismatch`` in the document; ``bad-params`` when the template's is not, or the
  template lacks it); not found and no template → ``missing-style``;
* a table's **style** = ``table_select.table_style_id`` (its ``w:tblStyle``, else the default table style); it is
  **restyled** when that is not the asked style: ``w:tblStyle`` gets ``w:val`` = the style (its other attributes go; a
  new one is inserted first in the ``w:tblPr``);
* **the import** (the template's style, imported when the document lacks it or with ``existing: "replace"``) happens only
  when at least one selected table is restyled — as map_styles skips a pair nothing uses: a selection that restyles no
  table changes no style definition;
* with ``clear`` the cleared elements are the ones present (``table_clearing``); a table neither restyled nor cleared is
  left alone and not counted ("already as asked"); an emptied ``w:tblPrEx`` or ``w:tcPr`` goes.

Left alone and flagged: a table in a text box when the selection is every table (a selection naming tables refuses it,
``textbox``). Refused: a table without ``w:tblPr``, rows or cells in a content control or custom XML when cells are
cleared (``table-structure``). Flagged: with ``keep``, restyled tables whose own (or rows' or cells') borders, shading or
margins win over the style; a pending tracked table, row-exception or cell property change on a changed table, row or
cell (reject-all would restore that older snapshot).

Verified by V10's ``applied_table_style`` kind (verify/applytablestyle.py) — and, when the style is imported, by
``styles_imported`` (verify/styleimport.py). With ``clear`` the kind's post-condition reads every selected table, row and
cell in the OUTPUT (nothing cleared left), so the plan soft-reads those elements on every selected table, changed or not.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from ..package import W, DocxPackage
from . import prose, register
from .base import JobException, Op, PlanFragment
from .selectors import StyleSheet, scope_parts
from .style_import import (TemplateStyles, apply_import, check_template_unchanged, plan_import, resolve_style,
                           style_type)
from .table_select import (cells_wrapped, check_table_selector, in_text_box, own_cells, own_rows, rows_wrapped,
                           selection_reads, selects_all, table_selected, table_style_id)

PARAM_KEYS = {"style", "template", "existing", "direct_formatting", "tracked"}
CLEARED_TABLE = ("tblBorders", "shd", "tblCellMar")
CLEARED_ROW = ("tblBorders", "shd", "tblCellMar")        # in the row's w:tblPrEx
CLEARED_CELL = ("tcBorders", "shd", "tcMar")
REFLOW_REASON = "a table style changes the table's borders, shading, margins and its text's formatting by design"
SCOPE_SCHEMA = {"type": "array", "items": {"type": "string", "enum": ["document", "headers", "footers", "footnotes",
                                                                       "endnotes", "comments"]}, "default": ["document"]}
TABLES_SCHEMA = {"description": "'all' (default) or {index_range: [a, b] (table ordinals in the part, both inclusive, "
                                "0-based, nested tables counted), style_in: [table style ids], first_row_regex, "
                                "text_regex, nested: bool} — every given key must hold"}


def table_clearing(tbl) -> Tuple[List[str], List[Tuple[int, int, List[str]]], List[Tuple[int, List[str]]]]:
    """(the table's own cleared elements present, (row, cell, [names]) of every own cell with some, (row, [names]) of
    every own row whose w:tblPrEx holds some)."""
    pr = tbl.find(W + "tblPr")
    names = [n for n in CLEARED_TABLE if pr is not None and pr.find(W + n) is not None]
    cells, rows = [], []
    for r, tr in enumerate(own_rows(tbl)):
        ex = tr.find(W + "tblPrEx")
        gone = [n for n in CLEARED_ROW if ex is not None and ex.find(W + n) is not None]
        if gone:
            rows.append((r, gone))
        for c, tc in enumerate(own_cells(tr)):
            tcpr = tc.find(W + "tcPr")
            gone = [n for n in CLEARED_CELL if tcpr is not None and tcpr.find(W + n) is not None]
            if gone:
                cells.append((r, c, gone))
    return names, cells, rows


def set_table_style(tblpr, style_id: str) -> None:
    """w:tblStyle = ``style_id``: the existing one in its place (its other attributes go), else a new one first."""
    ts = tblpr.find(W + "tblStyle")
    if ts is None:
        ts = etree.Element(W + "tblStyle")
        tblpr.insert(0, ts)
    ts.attrib.clear()
    ts.set(W + "val", style_id)


def remove_children(holder, names) -> None:
    """Every child of ``holder`` named in ``names`` goes; an emptied ``holder`` goes too."""
    for n in names:
        for old in holder.findall(W + n):
            holder.remove(old)
    if not len(holder) and not holder.attrib and holder.getparent() is not None:
        holder.getparent().remove(holder)


@register
class ApplyTableStyle(Op):
    name = "apply_table_style"
    mode = "formatting"
    description = ("Give the selected tables (every table by default) a table style — from the document or imported "
                   "from a template file. direct_formatting 'clear' also removes the tables' (and their rows' and "
                   "cells') own borders, shading and margins so the style shows. Adds no characters, never tracked; a "
                   "table already in the style (with nothing to clear) is left alone.")
    params_schema = {"type": "object", "required": ["style"], "properties": {
        "style": {"type": "string", "description": "the table style's id or name (as the profiles show it)"},
        "template": {"type": "string", "description": "a .docx/.dotx holding the style when the document lacks it: "
                                                      "imported with the styles it is based on"},
        "existing": {"type": "string", "enum": ["keep", "replace"], "default": "keep",
                     "description": "with template: a style the document already has keeps the document's definition "
                                    "(keep) or takes the template's (replace) — when a selected table is restyled"},
        "direct_formatting": {"type": "string", "enum": ["keep", "clear"], "default": "keep",
                              "description": "keep the tables' own borders, shading and cell margins (they win over the "
                                             "style), or clear them (and the rows' and cells' own) so the style shows"},
        "tracked": {"type": "boolean", "description": "never tracked: false only"}}}
    select_schema = {"type": "object", "properties": {"scope": SCOPE_SCHEMA, "tables": TABLES_SCHEMA}}
    verification_profile = {
        "V2": "zero text diffs", "V4": "no revision delta (never tracked)",
        "V10": "applied_table_style: selection, the style and each table's change re-derived from the source; each "
               "changed table's w:tblPr = the source's with w:tblStyle set (and, with clear, the cleared children "
               "gone), cleared rows' w:tblPrEx and cells' w:tcPr exactly; with clear nothing cleared left on any "
               "selected table, row or cell of the output; styles_imported when the style comes from a template; "
               "residual identical",
        "V7": "none — a table style re-lays the table"}

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        style = params.get("style")
        what = f"the {prose.quote(style)} table style" if style else "a table style"
        where = prose.where(select, default="every table")
        subject = where[3:] if where.startswith("in ") else where
        template = params.get("template")
        source = ""
        if template:
            if params.get("existing") == "replace":
                source = (f", as the template {prose.quote(template)} defines it, in place of the document's own "
                          f"definition")
            else:
                source = f", imported from the template {prose.quote(template)} when the document lacks it"
        first = prose.sentence("give", subject, what + source)
        if params.get("direct_formatting") != "clear":
            return first
        return first + " " + prose.sentence("the tables' own borders, shading and cell margins, and their rows' and "
                                            "cells' own, are removed so the style shows")

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        raw_select = dict(select or {})
        if set(raw_select) - {"scope", "tables"}:
            raise JobException(f"apply_table_style: unknown selector key(s) {sorted(set(raw_select) - {'scope', 'tables'})}",
                               "bad-params")
        unknown = set(params) - PARAM_KEYS
        if unknown:
            raise JobException(f"apply_table_style: unknown parameter(s) {sorted(unknown)}; known: {sorted(PARAM_KEYS)}",
                               "bad-params")
        ref = params.get("style")
        if not isinstance(ref, str) or not ref.strip():
            raise JobException("apply_table_style needs 'style': a table style's id or name", "bad-params")
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
        if params.get("tracked") not in (None, False):
            raise JobException("a table style change is never tracked; omit 'tracked' or set it false", "bad-params")
        pred = raw_select.get("tables", "all")
        check_table_selector(pred)
        parts = scope_parts(pkg, raw_select.get("scope", ["document"]))
        sheet = StyleSheet(pkg)
        if sheet.part is None:
            raise JobException("this file has no styles part; no table style can be applied", "structure")
        frag = self.fragment(raw_select, params)

        # ---- the style: the document's, or the template's (apply_style's resolution) ----
        did = resolve_style(sheet.styles, ref, "this file")
        tpl = TemplateStyles.load(template) if template is not None else None
        tid = None
        if tpl is not None:
            tid = resolve_style(tpl.sheet.styles, ref, "the template")
            if tid is None:
                raise JobException(f"the template has no style {ref!r}", "bad-params", {"style": ref})
            if style_type(tpl.sheet.styles[tid]) != "table":
                raise JobException(f"style {ref!r} of the template is a {style_type(tpl.sheet.styles[tid])} style; "
                                   f"apply_table_style gives tables a table style", "bad-params", {"style": ref})
        if existing == "replace" or (did is None and tid is not None):
            sid, from_template = tid, True
        elif did is not None:
            sid, from_template = did, False
        else:
            raise JobException(f"this file has no style {ref!r} (by id or name); give the template that holds it",
                               "missing-style", {"style": ref})
        if sid in sheet.styles and not from_template and style_type(sheet.styles[sid]) != "table":
            raise JobException(f"style {ref!r} is a {style_type(sheet.styles[sid])} style in this file; "
                               f"apply_table_style gives tables a table style", "style-mismatch", {"style": sid})

        # ---- the tables ----
        clear = direct == "clear"
        targets: Dict[str, List[list]] = {}
        n_tables = n_restyled = n_cells = n_rows = already = selected_n = 0
        keeps = older = 0
        boxed: List[str] = []
        uses: set = set()
        for part in parts:
            root = pkg.xml(part)
            frag.touches += sorted(selection_reads(part, root, pred))
            chosen: List[list] = []
            for k, tbl in enumerate(root.iter(W + "tbl")):
                if not table_selected(pred, tbl, k, sheet):
                    continue
                where = f"{part} table {k}"
                if in_text_box(tbl):
                    if selects_all(pred):                   # every table: this one is left alone and said so
                        boxed.append(where)
                        continue
                    raise JobException(f"{where} lies in a text box (Word keeps two copies of a text box)", "textbox",
                                       {"part": part, "table": k})
                selected_n += 1
                tblpr = tbl.find(W + "tblPr")
                if tblpr is None:
                    raise JobException(f"{where} has no table properties (w:tblPr)", "table-structure",
                                       {"part": part, "table": k})
                if clear and (rows_wrapped(tbl) or any(cells_wrapped(tr) for tr in own_rows(tbl))):
                    raise JobException(f"{where} has rows or cells inside a content control or custom XML; its cells "
                                       f"cannot be cleared safely", "table-structure", {"part": part, "table": k})
                old = table_style_id(tbl, sheet)
                restyle = old != sid
                names, cells, rows = table_clearing(tbl)
                if not clear:
                    keeps += int(restyle and bool(names or cells or rows))
                    names, cells, rows = [], [], []
                else:
                    # V10's post-condition reads, on every selected table changed or not, that nothing it clears is
                    # left: another op giving this table, a row or a cell its own borders, shading or margins in the
                    # same spec is a plan-time conflict (soft reads: a text edit leaves them)
                    frag.touches += [(part, f"tbl:{k}", f"verify-props:{n}") for n in CLEARED_TABLE]
                    for r, tr in enumerate(own_rows(tbl)):
                        frag.touches.append((part, f"tr:{k}:{r}", "verify-props:tblPrEx"))
                        frag.touches += [(part, f"tc:{k}:{r}:{c}", f"verify-props:{n}")
                                         for c, _ in enumerate(own_cells(tr)) for n in CLEARED_CELL]
                # which tables name which style (rule 9, batch 4): this one gains a use of the style and, restyled, its
                # old style loses one; a table already in the style is read (another op restyling it)
                uses.add((sheet.part, f"style:{sid}", "uses+"))
                if restyle and old:
                    uses.add((sheet.part, f"style:{old}", "uses-"))
                if not restyle:
                    frag.touches.append((part, f"tbl:{k}", "verify-props:tblPr"))
                if not restyle and not names and not cells and not rows:
                    already += 1
                    continue
                n_tables += 1
                n_restyled += int(restyle)
                n_cells += len(cells)
                n_rows += len(rows)
                older += int(tblpr.find(W + "tblPrChange") is not None)
                if restyle:
                    frag.touches.append((part, f"tbl:{k}", "tblPr"))   # map_styles' aspect: paragraph ops in it read it
                frag.touches.append((part, f"tbl:{k}", "tblProps"))
                frag.touches += [(part, f"tbl:{k}", n) for n in names]
                own = own_rows(tbl)
                for r, gone in rows:
                    frag.touches.append((part, f"tr:{k}:{r}", "tblPrEx"))
                    older += int(own[r].find(f"{W}tblPrEx/{W}tblPrExChange") is not None)
                for r, c, gone in cells:
                    frag.touches.append((part, f"tc:{k}:{r}:{c}", "tcPr"))
                    frag.touches += [(part, f"tc:{k}:{r}:{c}", n) for n in gone]
                    older += int(own_cells(own[r])[c].find(f"{W}tcPr/{W}tcPrChange") is not None)
                chosen.append([k, restyle, names, [[r, c, gone] for r, c, gone in cells],
                               [[r, gone] for r, gone in rows]])
            if chosen:
                targets[part] = chosen
                frag.intended_parts.append(part)
            frag.touches.append((part, "scope", "verify"))          # V10 re-resolves the selection over this part
        frag.touches += sorted(uses)

        # ---- the import: only when a selected table is restyled (map_styles skips a pair nothing uses) ----
        requested = [tid] if from_template and n_restyled else []
        imp = plan_import(pkg, tpl, requested, existing) if requested else None
        if sid in sheet.styles and not requested:
            frag.touches.append((sheet.part, f"style:{sid}", "verify"))   # applied by name: a removal beside conflicts

        # ---- the record ----
        frag.property_assertions.append({
            "kind": "applied_table_style", "parts": parts, "select": raw_select,
            "params": {k: params[k] for k in sorted(PARAM_KEYS) if k in params},
            "style_id": sid, "template": tpl.path if tpl is not None else None,
            "template_md5": tpl.md5 if tpl is not None else None, "requested": requested,
            "expected_tables": n_tables, "expected_restyled": n_restyled, "expected_cells": n_cells,
            "expected_rows": n_rows,
            "changed": {part: [[k, restyle, sorted(names), [[r, c, sorted(g)] for r, c, g in cells],
                                [[r, sorted(g)] for r, g in rows]]
                               for k, restyle, names, cells, rows in v] for part, v in targets.items()}})
        frag.transform_data = {"targets": targets, "style_id": sid,
                               "import": imp.transform_data() if imp is not None and imp.changes else None,
                               "template": tpl.path if tpl is not None else None,
                               "template_md5": tpl.md5 if tpl is not None else None}
        if imp is not None and imp.changes:
            frag.intended_parts += [p for p in imp.intended_parts() if p not in frag.intended_parts]
            frag.touches += imp.touches()
            frag.property_assertions.append(imp.assertion())
            frag.notes += imp.notes
            frag.flags += imp.flags
        if targets or (imp is not None and imp.changes):
            frag.geometry_reference, frag.geometry_reason = "none", REFLOW_REASON
        frag.notes.append(f"{n_restyled} of {selected_n} selected table(s) get table style {sid}"
                          + (f"; the own borders, shading and cell margins cleared on {n_tables} table(s), {n_rows} "
                             f"row(s) and {n_cells} cell(s)" if clear else "; their own borders, shading and cell "
                                                                            "margins stay (they win over the style)")
                          + "; w:tblLook (which conditional parts show) is left as it is — never tracked")
        if from_template and not n_restyled:
            frag.notes.append(f"no selected table is restyled, so the template's {sid} is not "
                              + ("imported" if did is None else "taken over (existing 'replace')")
                              + ": no style definition changes")
        if already:
            frag.notes.append(f"already as asked, left alone: {already} table(s)")
        if not selected_n and not boxed:
            frag.notes.append("no table in this file matches the selection: nothing to change")
        if boxed:
            frag.flags.append(f"{len(boxed)} table(s) in a text box left alone (Word keeps two copies of a text box): "
                              f"{boxed}")
        if keeps:
            frag.flags.append(f"{keeps} restyled table(s) keep their own borders, shading or cell margins (or their "
                              f"rows or cells do), which win over the style; direct_formatting 'clear' removes them.")
        if older:
            frag.flags.append(f"{older} changed table(s), row(s) or cell(s) already carry a pending tracked property "
                              f"change; reject-all would restore that older snapshot.")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if td.get("template") and not td.get("import"):
            # rule 12: the plan read the template (the style's resolution) even when nothing is copied from it
            check_template_unchanged(td["template"], td["template_md5"])
        apply_import(pkg, td.get("import"))
        for part, chosen in td["targets"].items():
            root = pkg.xml(part)
            tables = list(root.iter(W + "tbl"))
            for k, restyle, names, cells, rows in chosen:
                tbl = tables[k]
                tblpr = tbl.find(W + "tblPr")
                if restyle:
                    set_table_style(tblpr, td["style_id"])
                for n in names:
                    for old in tblpr.findall(W + n):
                        tblpr.remove(old)
                own = own_rows(tbl)
                for r, gone in rows:
                    remove_children(own[r].find(W + "tblPrEx"), gone)
                for r, c, gone in cells:
                    remove_children(own_cells(own[r])[c].find(W + "tcPr"), gone)
            pkg.write_xml(part, root)
