"""Source-derived proofs for approved style, language, table and list outcomes.

No execution plan is accepted. Assertions are derived analytically here from the
original package and approved parameters, then checked by the independent V10
readers. Only properties those checks proved may leave the original/final residual.
"""
from __future__ import annotations

import copy
import hashlib
import re
from pathlib import Path

from .props import Ctx, Styles, PartView, para_matches, _residual_styles, _strip_table_props, KINDS
from .reader import STYLES_REL, VPackage, W
from .clearing import v_cleared_sets, v_decide
from .styleowned import v_style_owned_decision, v_style_ownership
from .restyle import v_resolve_style, _v_pairs, _v_map, _v_holders, _v_rstyle, _v_tblstyle
from .styleimport import NUMBERING_REL, v_type, v_derive_import, v_used_ids
from .tableselect import VTables, v_parse_select, v_rows, v_cells, v_table_style, v_in_text_box, v_style_chain
from . import tableprops, applytablestyle, language

TYPES = {"paragraph_borders", "paragraph_style", "style_mapping", "proofing_language", "table_format", "table_style",
         "numbering", "typed_numbering", "review_comments"}
PARAMS = {
    "review_comments": {"text", "source", "author", "initials", "allow_missing", "expected_count"},
    "paragraph_borders": {"borders", "shading", "tracked", "author"},
    "paragraph_style": {"style", "direct_formatting", "existing", "tracked", "author"},
    "style_mapping": {"map", "existing", "allow_missing", "tracked", "author"},
    "proofing_language": {"language", "east_asian", "complex_script", "replace_only", "tracked"},
    "table_format": {"borders", "cell_margins", "width", "repeat_header_rows", "cant_split", "clear_cell_overrides", "tracked"},
    "table_style": {"style", "direct_formatting", "existing", "tracked"},
    "numbering": {"levels", "from", "level_map", "existing", "direct_formatting", "tracked", "author", "allow_missing"},
    "typed_numbering": {"levels", "existing", "direct_formatting", "tracked", "author", "allow_missing"},
}
SELECT = {"paragraph_borders": {"scope", "paragraphs"}, "paragraph_style": {"scope", "paragraphs"}, "style_mapping": {"scope"},
          "review_comments": {"scope", "paragraphs"},
          "proofing_language": set(), "table_format": {"scope", "tables"}, "table_style": {"scope", "tables"},
          "numbering": set(), "typed_numbering": {"paragraphs", "include_tables"}}
SCOPES = {"document", "headers", "footers", "footnotes", "endnotes", "comments"}


def _object(value, allowed, where):
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError(f"{where}: unsupported keys or non-object")


def paragraph_selector(value):
    if value is None or value == "all":
        return
    _object(value, {"style_in", "text_regex", "in_table", "is_empty", "index_range", "has_bold", "is_all_caps",
                    "after_heading_regex", "between_paragraphs"}, "paragraph selector")
    for key in ("text_regex", "after_heading_regex"):
        if key in value:
            if not isinstance(value[key], str):
                raise ValueError("paragraph regex must be literal text")
            re.compile(value[key])
    for key in ("in_table", "is_empty", "has_bold", "is_all_caps"):
        if key in value and type(value[key]) is not bool:
            raise ValueError(key + " must be boolean")
    if "style_in" in value and (not isinstance(value["style_in"], list) or not value["style_in"]
            or any(not isinstance(s, str) or not s for s in value["style_in"])):
        raise ValueError("style_in must be a nonempty list of style ids")
    if "index_range" in value:
        r = value["index_range"]
        if not isinstance(r, list) or len(r) != 2 or any(type(i) is not int for i in r) or not 0 <= r[0] <= r[1]:
            raise ValueError("invalid paragraph index_range")
    if "between_paragraphs" in value:
        b = value["between_paragraphs"]
        _object(b, {"start_regex", "end_regex", "include_bounds", "every"}, "between_paragraphs")
        if not isinstance(b.get("start_regex"), str):
            raise ValueError("between_paragraphs needs start_regex")
        if "every" in b and type(b["every"]) is not bool:
            raise ValueError("every must be boolean")
        re.compile(b["start_regex"])
        if b.get("end_regex") is not None:
            re.compile(b["end_regex"])
        if "include_bounds" in b and type(b["include_bounds"]) is not bool:
            raise ValueError("include_bounds must be boolean")


def validate(effect):
    kind = effect["type"]
    _object(effect, {"id", "type", "select", "params", "expected_count", "template", "template_sha256"}, "operation effect")
    if effect.get("expected_count") != "all" and (type(effect.get("expected_count")) is not int or effect["expected_count"] < 0):
        raise ValueError("operation effect needs an explicit nonnegative expected_count")
    params, sel = effect.get("params"), effect.get("select", {})
    _object(params, PARAMS[kind], kind + " params")
    _object(sel, SELECT[kind], kind + " select")
    for key in ("tracked", "allow_missing", "cant_split", "clear_cell_overrides"):
        if key in params and type(params[key]) is not bool:
            raise ValueError(key + " must be boolean")
    if "include_tables" in sel and type(sel["include_tables"]) is not bool:
        raise ValueError("include_tables must be boolean")
    if "author" in params and (not isinstance(params["author"], str) or not params["author"].strip()):
        raise ValueError("author must be nonempty literal text")
    if "scope" in sel:
        s = sel["scope"]
        s = [s] if isinstance(s, str) else s
        if not isinstance(s, list) or not s or any(not isinstance(x, str) or x not in SCOPES for x in s) or len(s) != len(set(s)):
            raise ValueError("unsupported or duplicate scope")
    if "paragraphs" in sel:
        paragraph_selector(sel["paragraphs"])
    if kind == "review_comments":
        from .comment_additions import validate_request
        validate_request(sel, params)
        if "template" in effect or "template_sha256" in effect:
            raise ValueError("review comments do not use templates")
    direct_rules = {"keep", "clear", "replace_style_owned"} if kind == "paragraph_style" else {"keep", "clear"}
    if "direct_formatting" in params and params["direct_formatting"] not in direct_rules:
        raise ValueError("invalid direct_formatting")
    if params.get("existing", "keep") not in ("keep", "replace"):
        raise ValueError("invalid existing style rule")
    if kind in {"paragraph_style", "table_style"} and (not isinstance(params.get("style"), str) or not params["style"].strip()):
        raise ValueError("style must be literal text")
    if kind == "style_mapping":
        _v_map(params)
    if kind == "proofing_language":
        language._v_asked(params)
    if kind == "table_format":
        tableprops._v_asked(params)
    if kind == "paragraph_borders":
        from .borders import _v_asked
        _v_asked(params)
    if kind in {"table_format", "table_style"}:
        v_parse_select(sel)
        if params.get("tracked", False):
            raise ValueError("table outcomes are untracked")
    if kind in {"numbering", "typed_numbering"}:
        from .numberingconvert import _v_scheme_params, _v_from, _v_level_map
        levels, _, _ = _v_scheme_params({**params, **({"template": effect["template"]} if "template" in effect else {})})
        if kind == "numbering":
            _v_from(params.get("from", "main"))
            _v_level_map(params.get("level_map"), len(levels))
    if ("template" in effect) != ("template_sha256" in effect):
        raise ValueError("template path and SHA256 must be approved together")
    if "template" in effect:
        if kind in {"paragraph_borders", "table_format", "proofing_language"}:
            raise ValueError("this outcome does not use a template")
        if not isinstance(effect["template"], str) or not Path(effect["template"]).is_absolute():
            raise ValueError("template path must be absolute")
        if not isinstance(effect["template_sha256"], str) or not re.fullmatch("[0-9a-f]{64}", effect["template_sha256"]):
            raise ValueError("template requires approved SHA256")
    if params.get("existing") == "replace" and "template" not in effect:
        raise ValueError("replacing style definitions requires a pinned template")


def _base(effect, author):
    params = copy.deepcopy(effect["params"])
    a = {"op_index": effect["id"], "select": copy.deepcopy(effect.get("select", {})), "params": params,
         "author": params.get("author", author)}
    if "template" in effect:
        data = Path(effect["template"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != effect["template_sha256"]:
            raise ValueError("approved template SHA256 changed")
        params["template"] = effect["template"]
        a.update(template=effect["template"], template_md5=hashlib.md5(data).hexdigest())
    return a


def _template_styles(a):
    if "template" not in a:
        return None
    package = VPackage(Path(a["template"]).read_bytes())
    try:
        part = package.related(STYLES_REL)
        return Styles(package.xml(part) if part else None)
    finally:
        package.zip.close()


def derive_import(ctx, a, requested):
    """An analytical import assertion, never a transform-produced permission."""
    if not requested:
        return []
    tpl = VPackage(Path(a["template"]).read_bytes())
    try:
        sp, sn = ctx.src.related(STYLES_REL), ctx.src.related(NUMBERING_REL)
        tp, tn = tpl.related(STYLES_REL), tpl.related(NUMBERING_REL)
        root = ctx.src.xml(sn) if sn else None
        d = v_derive_import(ctx.src_styles, root, Styles(tpl.xml(tp) if tp else None),
                            tpl.xml(tn) if tn else None, requested, a["params"].get("existing", "keep"),
                            used=v_used_ids(ctx.src, root))
        return [{"kind": "styles_imported", "op_index": a["op_index"], "template": a["template"],
                 "template_md5": a["template_md5"], "styles_part": sp, "numbering_part": sn,
                 "existing": a["params"].get("existing", "keep"), "requested": requested,
                 **{k: d[k] for k in ("imported", "replaced", "num_map", "abs_map")}}]
    finally:
        tpl.zip.close()


def _parts(ctx, a, default=("document",)):
    scope = a["select"].get("scope", list(default))
    return ctx.scope_parts([scope] if isinstance(scope, str) else scope)


def _style(ctx, a, kind):
    ref = a["params"]["style"]
    ts = _template_styles(a)
    did = v_resolve_style(ctx.src_styles, ref)
    tid = v_resolve_style(ts, ref) if ts is not None else None
    if ts is not None and (tid is None or v_type(ts.by_id[tid]) != kind):
        raise ValueError("template has no requested " + kind + " style")
    imported = a["params"].get("existing", "keep") == "replace" or (did is None and tid is not None)
    sid = tid if imported else did
    styles = ts if imported else ctx.src_styles
    if sid is None or v_type(styles.by_id[sid]) != kind:
        raise ValueError("requested style is missing or has the wrong type")
    return sid, imported


def _paragraph_borders(ctx, a):
    from .borders import _v_asked, _v_gets, _v_below_paragraph
    params = a["params"]
    params.setdefault("tracked", False)
    params.setdefault("author", a["author"])
    borders, shading = _v_asked(params)
    parts, changed, count = _parts(ctx, a), {}, 0
    for part in parts:
        sv = ctx.view(part)[0]
        for i, p in enumerate(sv.pars):
            if not para_matches(a["select"].get("paragraphs", "all"), p, i, sv, ctx.src_styles):
                continue
            count += 1
            gets = _v_gets(p.find(W + "pPr"), _v_below_paragraph(p, ctx.src_styles), borders, shading)
            if gets:
                changed.setdefault(part, []).append([i, sorted(gets)])
    a.update(kind="borders_shading", target="direct", parts=parts, changed=changed,
             expected_paragraphs=sum(len(v) for v in changed.values()))
    return [a], count


def _paragraph_style(ctx, a):
    sid, imported = _style(ctx, a, "paragraph")
    parts = _parts(ctx, a)
    a.update(kind="applied_style", parts=parts, style_id=sid, changed={}, expected_paragraphs=0,
             expected_restyled=0, expected_runs=0, expected_marks=0)
    clear = a["params"].get("direct_formatting", "keep") == "clear"
    selective = a["params"].get("direct_formatting") == "replace_style_owned"
    ownership = v_style_ownership(ctx.src_styles, _template_styles(a), sid, imported) if selective else None
    rn, pn = v_cleared_sets() if clear else (set(), set())
    count = 0
    for part in parts:
        sv = ctx.view(part)[0]
        for i, p in enumerate(sv.pars):
            if not para_matches(a["select"].get("paragraphs", "all"), p, i, sv, ctx.src_styles):
                continue
            count += 1
            ps = p.find(W + "pPr/" + W + "pStyle")
            current = ps.get(W + "val") if ps is not None else ctx.src_styles.default.get("paragraph")
            restyle = current != sid
            d = v_decide(p, sv.runs[i], ctx.src_styles, rn, pn) if clear else None
            if selective:
                d = v_style_owned_decision(p, sv.runs[i], ctx.src_styles, ownership)
            if not restyle and not (d and d.changed):
                continue
            a["changed"].setdefault(part, []).append([i, restyle])
            a["expected_restyled"] += int(restyle)
            a["expected_paragraphs"] += int(restyle or bool(d and d.ppr))
            if d:
                a["expected_runs"] += len(d.runs)
                a["expected_marks"] += int(d.mark is not None)
    return derive_import(ctx, a, [sid] if imported else []) + [a], count


def _mapping(ctx, a):
    parts = _parts(ctx, a, tuple(sorted(SCOPES)))
    rows = _v_pairs(ctx, _v_map(a["params"]), _template_styles(a), a["params"].get("existing", "keep"))
    live = [r for r in rows if r["status"] == "mapped"]
    maps = {kind: {r["from_id"]: r["to_id"] for r in live if r["type"] == kind}
            for kind in ("paragraph", "character", "table")}
    counts = {"paragraphs": 0, "runs": 0, "marks": 0, "tables": 0}
    changed, used = {}, set()
    for part in parts:
        sv = ctx.view(part)[0]
        found = {k: [] for k in counts}
        for i, p in enumerate(sv.pars):
            ps = p.find(W + "pPr/" + W + "pStyle")
            sid = ps.get(W + "val") if ps is not None else ctx.src_styles.default.get("paragraph")
            if sid in maps["paragraph"]:
                found["paragraphs"].append(i)
                used.add(sid)
            for kind, ri, rpr in _v_holders(p, sv.runs[i]):
                sid = _v_rstyle(rpr)
                if sid in maps["character"]:
                    found["marks" if kind == "mark" else "runs"].append(i if kind == "mark" else [i, ri])
                    used.add(sid)
        for k, tbl in enumerate(sv.root.iter(W + "tbl")):
            sid = _v_tblstyle(tbl)
            if sid in maps["table"]:
                found["tables"].append(k)
                used.add(sid)
        if any(found.values()):
            changed[part] = found
        for k in counts:
            counts[k] += len(found[k])
    for r in live:
        if r["from_id"] not in used:
            r["status"] = "unused"
    requested = list(dict.fromkeys(r["to_id"] for r in rows if r["status"] == "mapped" and r["source"] == "template"))
    a.update(kind="mapped_styles", parts=parts, pairs=rows, changed=changed, expected=counts)
    return derive_import(ctx, a, requested) + [a], sum(counts.values())


def _language(ctx, a):
    asked, listed = language._v_asked(a["params"])
    expected = {"doc_defaults": {}, "styles": {}, "parts": {}}
    nr = nm = 0
    for level in language._v_levels(ctx):
        kind = level[0]
        gets = language._v_gets(level[-1], asked, listed, doc_defaults=kind == "docDefaults")
        if not gets:
            continue
        if kind == "docDefaults":
            expected["doc_defaults"] = gets
        elif kind == "style":
            expected["styles"][level[1]] = gets
        else:
            part = expected["parts"].setdefault(level[1], {"runs": [], "marks": []})
            if kind == "run":
                part["runs"].append([level[2], level[3], gets])
                nr += 1
            else:
                part["marks"].append([level[2], gets])
                nm += 1
    a.update(kind="language", expected=expected, expected_runs=nr, expected_marks=nm)
    return [a], 1


def _table_format(ctx, a):
    asked = tableprops._v_asked(a["params"])
    scope, pred = v_parse_select(a["select"])
    parts, changed = ctx.scope_parts(scope), {}
    nt = nr = nc = count = 0
    for part in parts:
        rec = {"tables": [], "rows": [], "cells": []}
        for k, tbl in VTables(ctx.view(part)[0].root).selected(pred, ctx.src_styles):
            chain = v_style_chain(tbl, ctx.src_styles)
            if tableprops._v_skip_or_refuse(tbl, chain, asked, tableprops._v_every_table(pred), f"{part} table {k}"):
                continue
            count += 1
            tg = tableprops._v_table_gets(tbl.find(W + "tblPr"), chain, asked)
            rg, cg = tableprops._v_row_gets(tbl, chain, asked), tableprops._v_cell_gets(tbl, asked)
            if tg:
                rec["tables"].append([k, sorted(tg)])
            rec["rows"].extend([k, r, sorted(g)] for r, g in rg)
            rec["cells"].extend([k, r, c, sorted(g)] for r, c, g in cg)
            nt += int(bool(tg or rg or cg))
            nr += len(rg)
            nc += len(cg)
        if any(rec.values()):
            changed[part] = rec
    a.update(kind="table_props", parts=parts, changed=changed, expected_tables=nt, expected_rows=nr, expected_cells=nc)
    return [a], count


def _table_style(ctx, a):
    sid, imported = _style(ctx, a, "table")
    scope, pred = v_parse_select(a["select"])
    parts, changed = ctx.scope_parts(scope), {}
    clear = a["params"].get("direct_formatting", "keep") == "clear"
    nt = nr = nc = ns = count = 0
    for part in parts:
        for k, tbl in VTables(ctx.view(part)[0].root).selected(pred, ctx.src_styles):
            if v_in_text_box(tbl):
                if pred in (None, "all", {}):
                    continue
                raise ValueError("selected table lies in a text box")
            count += 1
            restyle = v_table_style(tbl, ctx.src_styles) != sid
            names = applytablestyle._v_held(tbl.find(W + "tblPr"), applytablestyle._V_TABLE) if clear else []
            rows = v_rows(tbl)
            ex = [(r, applytablestyle._v_held(tr.find(W + "tblPrEx"), applytablestyle._V_ROW))
                  for r, tr in enumerate(rows)] if clear else []
            cells = [(r, c, applytablestyle._v_held(tc.find(W + "tcPr"), applytablestyle._V_CELL))
                     for r, tr in enumerate(rows) for c, tc in enumerate(v_cells(tr))] if clear else []
            ex, cells = [(r, g) for r, g in ex if g], [(r, c, g) for r, c, g in cells if g]
            if not restyle and not names and not cells and not ex:
                continue
            changed.setdefault(part, []).append([k, restyle, sorted(names), [[r, c, sorted(g)] for r, c, g in cells],
                                                [[r, sorted(g)] for r, g in ex]])
            nt += 1
            ns += int(restyle)
            nr += len(ex)
            nc += len(cells)
    a.update(kind="applied_table_style", parts=parts, style_id=sid, changed=changed,
             expected_tables=nt, expected_restyled=ns, expected_cells=nc, expected_rows=nr)
    return derive_import(ctx, a, [sid] if imported and ns else []) + [a], count


def verify_operations(source, output, effects, author, errors, binding):
    """Return context containing only masks justified by independently checked outcomes."""
    if len(effects) == 1 and effects[0]["type"] == "review_comments":
        from .comment_additions import verify_review_effect
        validate(effects[0])
        return verify_review_effect(source, output, effects[0], errors, binding)
    source_context = Ctx(source, source, {"property_assertions": []})
    assertions = source_context.plan["property_assertions"]
    for effect in effects:
        validate(effect)
        a = _base(effect, author)
        kind = effect["type"]
        if kind in {"numbering", "typed_numbering"}:
            from .goal_numbering import derive
            derived, count = derive(source_context, {**effect, "_assertion": a})
        else:
            derived, count = {"paragraph_borders": _paragraph_borders,
                              "paragraph_style": _paragraph_style, "style_mapping": _mapping,
                              "proofing_language": _language, "table_format": _table_format,
                              "table_style": _table_style}[kind](source_context, a)
        if count != binding["effects"][effect["id"]]["source_count"]:
            raise ValueError(f"{effect['id']}: source binding count differs from independently selected {count}")
        if effect["expected_count"] != "all" and count != effect["expected_count"]:
            raise ValueError(f"{effect['id']}: independently selected {count}, approved expected_count {effect['expected_count']}")
        assertions.extend(derived)
    ctx = Ctx(source, output, {"property_assertions": assertions})
    # Position, rather than text similarity, binds original paragraphs to final
    # ones. A moved paragraph must remain visible to the outer tree residual.
    for part in ctx.both:
        sv, ov = ctx.view(part)
        if len(sv.pars) != len(ov.pars):
            raise ValueError("operation effects must preserve the source paragraph count")
        ctx.maps[part] = {i: i for i in range(len(sv.pars))}
    for a in assertions:
        KINDS[a["kind"]](ctx, a, errors)
    if ctx.styles_part:
        _residual_styles(ctx, errors)
    # Mature field checks compare the container's attributes and children. Close
    # its own text as well before removing that proved container from the residual.
    for part in ctx.both:
        sv, ov = ctx.view(part)
        holders = [(sv.runs[i][ri].find(W + "rPr"), ov.runs[i][ri].find(W + "rPr"))
                   for i, ri in ctx.strip_runs.get(part, set())]
        holders += [(sv.pars[i].find(W + "pPr/" + W + "rPr"), ov.pars[i].find(W + "pPr/" + W + "rPr"))
                    for i in ctx.strip_marks.get(part, set())]
        for before, after in holders:
            original = (before.text or "").strip() if before is not None else ""
            final = (after.text or "").strip() if after is not None else ""
            if original != final:
                errors.append(f"{part}: checked property container has unapproved significant text")
    style_holders = []
    if ctx.strip_doc_defaults:
        style_holders.append((language._v_dd_rpr(ctx.src_styles_root), language._v_dd_rpr(ctx.out_styles_root)))
    for sid in ctx.strip_styles:
        before, after = ctx.src_styles.by_id.get(sid), ctx.out_styles.by_id.get(sid)
        style_holders.append((before.find(W + "rPr") if before is not None else None,
                              after.find(W + "rPr") if after is not None else None))
    for before, after in style_holders:
        original = (before.text or "").strip() if before is not None else ""
        final = (after.text or "").strip() if after is not None else ""
        if original != final:
            errors.append("checked style/default property container has unapproved significant text")
    return ctx


def normalize_operations(ctx, source_roots, output_roots):
    """Mask exactly checked fields, preserving all container positions and text."""
    for part in ctx.both:
        if part not in source_roots or part not in output_roots:
            continue
        s, o = source_roots[part], output_roots[part]
        sp, op = list(s.iter(W + "p")), list(o.iter(W + "p"))
        if len(sp) != len(op):
            raise ValueError("operation normalization lost source paragraph cardinality")
        # Whole paragraph restoration only follows the typed-number item check.
        for i, oi in ctx.verified_blocks.get(part, {}).items():
            op[oi].getparent().replace(op[oi], copy.deepcopy(sp[i]))
        for root in (s, o):
            view = PartView(root)
            for i, ri in ctx.strip_runs.get(part, set()):
                run = view.runs[i][ri]
                rpr = run.find(W + "rPr")
                if rpr is not None:
                    run.remove(rpr)
            for i in ctx.strip_marks.get(part, set()):
                ppr = view.pars[i].find(W + "pPr")
                if ppr is not None:
                    rpr = ppr.find(W + "rPr")
                    if rpr is not None:
                        ppr.remove(rpr)
            for i, names in ctx.strip_ppr.get(part, {}).items():
                ppr = view.pars[i].find(W + "pPr")
                if ppr is not None:
                    for el in list(ppr):
                        if el.tag in {W + name for name in names}:
                            ppr.remove(el)
            for i in set(ctx.strip_marks.get(part, set())) | set(ctx.strip_ppr.get(part, {})):
                ppr = view.pars[i].find(W + "pPr")
                if ppr is not None and not len(ppr) and not ppr.attrib and not (ppr.text or ""):
                    ppr.getparent().remove(ppr)
            for k in ctx.strip_tbl_style.get(part, set()):
                tbl = list(root.iter(W + "tbl"))[k]
                pr = tbl.find(W + "tblPr")
                if pr is not None:
                    el = pr.find(W + "tblStyle")
                    if el is not None:
                        pr.remove(el)
        _strip_table_props(ctx, part, s, o)
