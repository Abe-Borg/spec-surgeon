"""V10 kind ``applied_table_style`` — apply_table_style, verified (built-in batch 5, item 38 —
docs/BUILTIN_OPS_BATCH5_DESIGN.md §5.3).

This module's own reading of the op's definitions (transform side: ops/apply_table_style.py). Nothing here comes from
ops/ (rule 1):

* the parameters are read from the user's own words with this module's defaults (direct_formatting ``keep``, existing
  ``keep``; never tracked);
* the style is resolved with the verifier's own resolver (``restyle.v_resolve_style``) — apply_style's rule: with
  ``existing: "keep"`` the SOURCE's own style when it has one, else the template's (read by ``template._template``, md5
  checked); with ``"replace"`` always the template's; it must be a table style;
* the import it needs — the template's style when it comes from the template AND at least one selected table is
  restyled, else none — must be exactly what this op's ``styles_imported`` assertion carries
  (``styleimport.check_requested``);
* the selection (``tableselect.VTables``; a table in a text box left alone when the selection is every table, refused
  otherwise), each table's style (``tableselect.v_table_style``) and, with ``clear``, the table's own
  ``w:tblBorders``/``w:shd``/``w:tblCellMar``, the same three in every own row's ``w:tblPrEx`` and every own cell's
  ``w:tcBorders``/``w:shd``/``w:tcMar`` are re-derived from the SOURCE.

Fails when the changed tables (which are restyled, which elements, rows and cells are cleared) or the counts differ from
the plan's; when a changed table's output ``w:tblPr`` is not the source's with ``w:tblStyle`` = the style (in its place,
else first) and the cleared children gone; when a cleared row's ``w:tblPrEx`` or cell's ``w:tcPr`` is not the source's
without the cleared children (gone when empty); and — a post-condition on the OUTPUT, for every selected table, changed or
not — when the table is not in the style or, with ``clear``, when the table, an own row's ``w:tblPrEx`` or an own cell
still holds an element ``clear`` removes. ``tblStyle`` (``strip_tbl_style``), the cleared table elements (``strip_tbl``),
row-exception elements (``strip_tblprex``) and cell elements (``strip_tc``) of the changed tables leave the residual.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Sequence

from lxml import etree

from .reader import STYLES_REL, W
from .props import Ctx, Styles, sig
from .restyle import v_resolve_style
from .styleimport import check_requested, v_type
from .tableselect import VTables, v_cells, v_cells_wrapped, v_in_text_box, v_parse_select, v_rows, v_rows_wrapped, \
    v_table_style

_V_TABLE = ("tblBorders", "shd", "tblCellMar")
_V_ROW = ("tblBorders", "shd", "tblCellMar")
_V_CELL = ("tcBorders", "shd", "tcMar")


def _v_template_styles(path: str, md5: Optional[str], errs: List[str]) -> Optional[Styles]:
    from .template import _template
    got, tpl = _template(path)
    if md5 is not None and got != md5:
        errs.append(f"applied_table_style: the template changed since planning (md5 {got} ≠ {md5})")
        return None
    tsp = tpl.related(STYLES_REL)
    return Styles(tpl.xml(tsp) if tsp else None)


def _v_expected_tblpr(src, restyle: bool, sid: str, names: List[str]):
    el = copy.deepcopy(src)
    if restyle:
        ts = el.find(W + "tblStyle")
        if ts is None:
            ts = etree.Element(W + "tblStyle")
            el.insert(0, ts)
        for k in list(ts.attrib):
            del ts.attrib[k]
        ts.set(W + "val", sid)
    for c in list(el):
        if isinstance(c.tag, str) and c.tag in {W + n for n in names}:
            el.remove(c)
    return el


def _v_minus(src, names: Sequence[str]):
    """A copy of ``src`` without the children named; None when nothing is left."""
    el = copy.deepcopy(src)
    for c in list(el):
        if isinstance(c.tag, str) and c.tag in {W + n for n in names}:
            el.remove(c)
    return el if (len(el) or el.attrib) else None


def _v_held(holder, names: Sequence[str]) -> List[str]:
    return [n for n in names if holder is not None and holder.find(W + n) is not None]


def _v_post(o_tbl, sid: str, clear: bool, out_styles: Styles, where: str, errs: List[str]) -> None:
    """The output table is in the style and, with clear, holds nothing clear removes (every selected table)."""
    got = v_table_style(o_tbl, out_styles)
    if got != sid:
        errs.append(f"{where}: the table's style is {got!r} in the output, asked {sid!r}")
    if not clear:
        return
    left = _v_held(o_tbl.find(W + "tblPr"), _V_TABLE)
    if left:
        errs.append(f"{where}: keeps its own {left} in the output (direct_formatting clear)")
    for r, tr in enumerate(v_rows(o_tbl)):
        left = _v_held(tr.find(W + "tblPrEx"), _V_ROW)
        if left:
            errs.append(f"{where} row {r}: its w:tblPrEx keeps {left} in the output (direct_formatting clear)")
        for c, tc in enumerate(v_cells(tr)):
            left = _v_held(tc.find(W + "tcPr"), _V_CELL)
            if left:
                errs.append(f"{where} row {r} cell {c}: keeps its own {left} in the output (direct_formatting clear)")


def _applied_table_style(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    tag = "applied_table_style"
    scope, pred = v_parse_select(a.get("select") or {})
    every = pred in (None, "all") or pred == {}
    parts = ctx.scope_parts(scope)
    if sorted(parts) != sorted(a.get("parts", [])):
        errs.append(f"{tag}: scope resolves to {sorted(parts)} here, plan says {sorted(a.get('parts', []))}")
    params = a.get("params") or {}
    ref = params.get("style")
    if not isinstance(ref, str) or not ref.strip():
        raise ValueError("no style asked")
    direct = params.get("direct_formatting", "keep")
    existing = params.get("existing", "keep")
    if direct not in ("keep", "clear") or existing not in ("keep", "replace"):
        raise ValueError(f"bad direct_formatting/existing {direct!r}/{existing!r}")
    if params.get("tracked"):
        raise ValueError("a table style change is never tracked (the op must refuse)")
    clear = direct == "clear"

    # ---- the style ----
    did = v_resolve_style(ctx.src_styles, ref)
    tid = None
    template = params.get("template")
    if template is not None:
        tstyles = _v_template_styles(a.get("template") or template, a.get("template_md5"), errs)
        if tstyles is None:
            return f"{tag}: template changed"
        tid = v_resolve_style(tstyles, ref)
        if tid is None or v_type(tstyles.by_id[tid]) != "table":
            raise ValueError(f"the template has no table style {ref!r} (the op must refuse: bad-params)")
    if existing == "replace" and template is None:
        raise ValueError("existing 'replace' without a template (the op must refuse)")
    if existing == "replace" or (did is None and tid is not None):
        sid, from_template = tid, True
    elif did is not None:
        sid, from_template = did, False
    else:
        raise ValueError(f"style {ref!r} is in neither the document nor a template (the op must refuse: missing-style)")
    if sid != a.get("style_id"):
        errs.append(f"{tag}: the style resolves to {sid!r} here, the plan applies {a.get('style_id')!r}")
    if not from_template and v_type(ctx.src_styles.by_id[sid]) != "table":
        raise ValueError(f"{sid!r} is a {v_type(ctx.src_styles.by_id[sid])} style here (the op must refuse: "
                         f"style-mismatch)")

    # ---- the tables ----
    mine: Dict[str, List[Any]] = {}
    n_tables = n_restyled = n_cells = n_rows = 0
    for part in parts:
        if part not in ctx.both:
            errs.append(f"{tag}: {part} missing from the output")
            continue
        sv, ov = ctx.view(part)
        s_t, o_t = VTables(sv.root), VTables(ov.root)
        if len(s_t.tables) != len(o_t.tables):
            errs.append(f"{tag}: {part} has {len(s_t.tables)} table(s) in the source and {len(o_t.tables)} in the output")
            continue
        for k, tbl in s_t.selected(pred, ctx.src_styles):
            where = f"{part} table {k}"
            if v_in_text_box(tbl):
                if every:
                    continue                           # left alone (flagged): the residual compares it whole
                raise ValueError(f"{where} lies in a text box (the op must refuse: textbox)")
            s_pr = tbl.find(W + "tblPr")
            if s_pr is None:
                raise ValueError(f"{where} has no w:tblPr (the op must refuse: table-structure)")
            rows = v_rows(tbl)
            if clear and (v_rows_wrapped(tbl) or any(v_cells_wrapped(tr) for tr in rows)):
                raise ValueError(f"{where}: rows or cells in a container (the op must refuse: table-structure)")
            o_tbl = o_t.tables[k]
            _v_post(o_tbl, sid, clear, ctx.out_styles, where, errs)
            restyle = v_table_style(tbl, ctx.src_styles) != sid
            names = _v_held(s_pr, _V_TABLE) if clear else []
            row_ex = [(r, _v_held(tr.find(W + "tblPrEx"), _V_ROW)) for r, tr in enumerate(rows)] if clear else []
            row_ex = [x for x in row_ex if x[1]]
            cells = [(r, c, _v_held(tc.find(W + "tcPr"), _V_CELL))
                     for r, tr in enumerate(rows) for c, tc in enumerate(v_cells(tr))] if clear else []
            cells = [x for x in cells if x[2]]
            if not restyle and not names and not cells and not row_ex:
                continue
            n_tables += 1
            n_restyled += int(restyle)
            n_cells += len(cells)
            n_rows += len(row_ex)
            mine.setdefault(part, []).append([k, restyle, sorted(names), [[r, c, sorted(g)] for r, c, g in cells],
                                              [[r, sorted(g)] for r, g in row_ex]])
            o_pr = o_tbl.find(W + "tblPr")
            want = _v_expected_tblpr(s_pr, restyle, sid, names)
            if sig(o_pr) != sig(want):
                errs.append(f"{where}: w:tblPr is not the source's with w:tblStyle {sid!r} "
                            + ("in its place (else first)" if restyle else "kept") + (f" and {names} gone" if names else ""))
            if restyle:
                ctx.strip_tbl_style.setdefault(part, set()).add(k)
            if names:
                ctx.strip_tbl.setdefault(part, {}).setdefault(k, set()).update(names)
            o_rows = v_rows(o_tbl)
            if len(o_rows) != len(rows):
                errs.append(f"{where}: {len(rows)} row(s) in the source, {len(o_rows)} in the output")
                continue
            for r, g in row_ex:
                want_r = _v_minus(rows[r].find(W + "tblPrEx"), g)
                got = o_rows[r].find(W + "tblPrEx")
                if sig(got) != sig(want_r):
                    errs.append(f"{where} row {r}: w:tblPrEx is not the source's without {g}")
                ctx.strip_tblprex.setdefault(part, {}).setdefault((k, r), set()).update(g)
            for r, c, g in cells:
                o_cells = v_cells(o_rows[r])
                if c >= len(o_cells):
                    errs.append(f"{where} row {r} cell {c}: missing from the output")
                    continue
                want_c = _v_minus(v_cells(rows[r])[c].find(W + "tcPr"), g)
                if sig(o_cells[c].find(W + "tcPr")) != sig(want_c):
                    errs.append(f"{where} row {r} cell {c}: w:tcPr is not the source's without {g}")
                ctx.strip_tc.setdefault(part, {}).setdefault((k, r, c), set()).update(g)

    # ---- the import it needs: only when a selected table is restyled ----
    requested = [tid] if from_template and n_restyled else []
    check_requested(ctx, a, requested, existing, a.get("template") or template, errs, tag)
    if n_restyled:
        o_st = ctx.out_styles.by_id.get(sid)
        if o_st is None:
            errs.append(f"{tag}: style {sid!r} is not defined in the output")
        elif v_type(o_st) != "table":
            errs.append(f"{tag}: style {sid!r} is a {v_type(o_st)} style in the output")
    for n, key in ((n_tables, "expected_tables"), (n_restyled, "expected_restyled"), (n_cells, "expected_cells"),
                   (n_rows, "expected_rows")):
        if n != a.get(key, -1):
            errs.append(f"{tag}: {n} here ({key}), plan predicted {a.get(key)}")

    def norm(d):
        return {p: sorted((int(x[0]), bool(x[1]), tuple(x[2]), tuple((r, c, tuple(g)) for r, c, g in x[3]),
                           tuple((r, tuple(g)) for r, g in (x[4] if len(x) > 4 else [])))
                          for x in v) for p, v in (d or {}).items() if v}
    if norm(mine) != norm(a.get("changed")):
        errs.append(f"{tag}: the tables that change here {norm(mine)} are not the plan's {norm(a.get('changed'))}")
    return (f"{tag}: {n_restyled} table(s) → style {sid}"
            + (f", own borders/shading/margins cleared on {n_tables} table(s), {n_rows} row(s) and {n_cells} cell(s)"
               if clear else ""))


APPLY_TABLE_KINDS = {"applied_table_style": _applied_table_style}
