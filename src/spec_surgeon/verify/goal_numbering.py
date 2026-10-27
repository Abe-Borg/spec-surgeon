"""Source-derived assertions for approved numbering outcomes.

Only the immutable source and the approved template determine these assertions.
The candidate output and its transform plan are never consulted. Existing
independent numbering checkers subsequently compare the actual output with them.
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path

from .clearing import v_cleared_sets, v_decide
from .edits import v_items, v_paragraph_text
from .lists import (VListDefs, v_count, v_counted, v_list_defs, v_main_abstract,
                    v_paragraph_numbering, v_paragraph_style, v_resolve_scheme, v_typed_number)
from .numberingconvert import (V_LEVEL_KNOWN, V_NUMPR_REVISIONS, _v_from, _v_level_map,
                              _v_scheme_params, v_destination, v_row, v_visible)
from .props import Ctx, PartView, Styles, _has_ancestor, para_matches
from .reader import STYLES_REL, W, default_author
from .restyle import v_resolve_style
from .styleimport import NUMBERING_REL, v_derive_import, v_used_ids
from .template import _template
from .typednumbers import _v_display


def _imports(ctx, effect, params):
    """Derive the exact style-import authority before resolving the destination."""
    levels, template, existing = _v_scheme_params(params)
    if template is None:
        return [], None
    data = Path(template).read_bytes()
    if hashlib.sha256(data).hexdigest() != effect.get("template_sha256"):
        raise ValueError("approved numbering template changed")
    md5, tpl = _template(template)
    if md5 != hashlib.md5(data).hexdigest():
        raise ValueError("approved numbering template changed while being read")
    tsp, tnp = tpl.related(STYLES_REL), tpl.related(NUMBERING_REL)
    tstyles = Styles(tpl.xml(tsp) if tsp else None)
    tnum = tpl.xml(tnp) if tnp else None
    scheme = v_resolve_scheme(levels, tstyles, VListDefs(tnum))
    requested = [scheme.levels[k].style for k, ref in enumerate(levels)
                 if existing == "replace" or v_resolve_style(ctx.src_styles, ref) is None]
    if not requested:
        return [], md5
    snp = ctx.src.related(NUMBERING_REL)
    snum = ctx.src.xml(snp) if snp else None
    derived = v_derive_import(ctx.src_styles, snum, tstyles, tnum, requested, existing,
                              used=v_used_ids(ctx.src, snum))
    assertion = {"kind": "styles_imported", "op_index": effect["id"],
                 "styles_part": ctx.src.related(STYLES_REL), "numbering_part": snp,
                 "template": template, "template_md5": md5,
                 "requested": requested, "existing": existing}
    for key in ("imported", "replaced", "num_map", "abs_map"):
        assertion[key] = copy.deepcopy(derived[key])
    return [assertion], md5


def _revision_refusals(p, styles, dest, tracked, where):
    numpr = p.find(W + "pPr/" + W + "numPr")
    if numpr is not None and any(el.tag in V_NUMPR_REVISIONS for el in numpr.iter()):
        raise ValueError(where + ": tracked changes in paragraph numbering are unsupported")
    if tracked:
        for style in styles.chain(v_paragraph_style(p, styles)):
            sid = style.get(W + "styleId")
            if sid in dest.replaced:
                raise ValueError(where + ": tracked conversion cannot replace its original style " + sid)


def _numbered(view, styles, source_count, dest, params):
    """Choose source list members and analytically predict every resulting number."""
    frm = _v_from(params.get("from", "main"))
    level_map = _v_level_map(params.get("level_map"), len(dest.scheme.levels))
    if frm == "main":
        main = v_main_abstract(source_count)
        chosen = [(item, item.ilvl) for item in source_count
                  if main is not None and item.abstract_id == main]
    else:
        level_of = {}
        for level, refs in enumerate(frm["styles"]):
            for ref in refs:
                sid = v_resolve_style(styles, ref)
                if sid is None:
                    continue
                if sid in level_of:
                    raise ValueError("from.styles names the same style at multiple levels")
                level_of[sid] = level
        chosen = [(item, level_of[v_paragraph_style(view.pars[item.index], styles)])
                  for item in source_count if v_paragraph_style(view.pars[item.index], styles) in level_of]
    picked = {item.index for item, _ in chosen}
    converted = []
    for item, source_level in chosen:
        where = f"paragraph {item.index}"
        if item.not_evaluated is not None and (
                item.issue not in V_LEVEL_KNOWN or item.ilvl is None or item.num_id is None
                or any(other.index not in picked for other in source_count if other.num_id == item.num_id)):
            raise ValueError(where + ": source numbering cannot be evaluated: " + item.not_evaluated)
        _revision_refusals(view.pars[item.index], styles, dest, params.get("tracked", False), where)
        level = level_map.get(source_level, source_level)
        if level is None or not 0 <= level < len(dest.scheme.levels):
            raise ValueError(where + ": level is outside the approved numbering scheme")
        converted.append((item, level))
    override = {item.index: (dest.scheme.num_id, level) for item, level in converted}
    predicted = v_count(view.root, dest.styles, dest.defs, override)
    by_predicted = {item.index: item for item in predicted}
    for item, level in converted:
        output = by_predicted.get(item.index)
        if (output is not None and item.not_evaluated is not None and output.not_evaluated is not None
                and item.issue in V_LEVEL_KNOWN and output.issue == "skipped" and item.ilvl == level):
            continue
        if output is None or output.not_evaluated is not None:
            raise ValueError(f"paragraph {item.index}: converted number cannot be evaluated")
    for item in source_count:
        output = by_predicted.get(item.index)
        if item.not_evaluated is None and output is not None and output.not_evaluated is not None:
            raise ValueError(f"paragraph {item.index}: conversion makes an evaluated number unevaluable")
    before = {item.index: item for item in source_count}
    for index in sorted(set(before) | set(by_predicted)):
        if index in picked:
            continue
        original, output = before.get(index), by_predicted.get(index)
        old = None if original is None else (original.text, original.suff, original.issue)
        new = None if output is None else (output.text, output.suff, output.issue)
        if old != new and not (output is not None and output.num_id == dest.scheme.num_id):
            raise ValueError(f"paragraph {index}: conversion changes an unrelated list's number")
    return [[item.index, level] for item, level in converted], predicted


def _typed(view, styles, definitions, source_count, dest, params, select):
    """Identify literal prefixes, then prove the proposed automatic list reads identically."""
    texts = [v_paragraph_text(p) for p in view.pars]
    converted = []
    for index, paragraph in enumerate(view.pars):
        if not v_counted(paragraph):
            continue
        number = v_paragraph_numbering(paragraph, styles, definitions)
        if number is not None and not number.dangling:
            continue
        if not select.get("include_tables", False) and _has_ancestor(paragraph, {W + "tc"}, None):
            continue
        if not para_matches(select.get("paragraphs", "all"), paragraph, index, view, styles):
            continue
        typed = v_typed_number(texts[index], dest.scheme)
        if typed is None:
            continue
        if typed.ambiguous:
            raise ValueError(f"paragraph {index}: ambiguous typed number {typed.token!r}")
        if dest.scheme.levels[typed.level].level.suff == "nothing":
            raise ValueError(f"paragraph {index}: destination number has no suffix")
        prefix_runs, token_runs = [], []
        start, end = typed.token_start, typed.token_start + len(typed.token)
        for item in v_items(paragraph):
            if item.pos is None or item.run is None:
                continue
            if item.pos < typed.end and all(item.run is not run for run in prefix_runs):
                prefix_runs.append(item.run)
            if start <= item.pos < end and all(item.run is not run for run in token_runs):
                token_runs.append(item.run)
        why = _v_display(paragraph, prefix_runs, token_runs, typed.token, styles, dest.styles,
                         dest.sids[typed.level], dest.scheme, typed.level, dest.level_rprs.get(typed.level))
        if why:
            raise ValueError(f"paragraph {index}: {why}")
        _revision_refusals(paragraph, styles, dest, params.get("tracked", True), f"paragraph {index}")
        converted.append([index, typed.level, typed.end])
    override = {index: (dest.scheme.num_id, level) for index, level, _ in converted}
    predicted = v_count(view.root, dest.styles, dest.defs, override)
    by_source = {item.index: item for item in source_count}
    by_predicted = {item.index: item for item in predicted}
    ends = {index: end for index, _, end in converted}
    for index, paragraph in enumerate(view.pars):
        if not v_counted(paragraph):
            continue
        before = v_visible(by_source.get(index), texts[index])
        after = v_visible(by_predicted.get(index), texts[index][ends.get(index, 0):])
        if before != after:
            raise ValueError(f"paragraph {index}: automatic numbering would change the visible line "
                             f"from {before!r} to {after!r}")
    return converted, predicted


def derive(ctx: Ctx, effect: dict) -> tuple[list[dict], int]:
    """Return independent property assertions and selected paragraph cardinality.

    ``Ctx(src, src, ...)`` below deliberately isolates the destination helper from
    the candidate output. Its source-preservation bookkeeping can only read the
    source twice; it supplies no evidence about the output's correctness.
    """
    typed = effect["type"] == "typed_numbering"
    if effect["type"] not in {"numbering", "typed_numbering"}:
        raise ValueError("not a numbering outcome")
    params = copy.deepcopy(effect["params"])
    if "template" in params:
        raise ValueError("numbering templates must be pinned at effect level")
    if effect.get("template") is not None:
        params["template"] = effect["template"]
    keys = {"template", "levels", "existing", "direct_formatting", "tracked", "author", "allow_missing"}
    if not typed:
        keys.update({"from", "level_map"})
    if set(params) - keys:
        raise ValueError("unknown numbering parameters: " + str(sorted(set(params) - keys)))
    for key in ("tracked", "allow_missing"):
        if key in params and not isinstance(params[key], bool):
            raise ValueError(key + " must be boolean")
    direct = params.get("direct_formatting", "keep")
    if direct not in {"keep", "clear"}:
        raise ValueError("direct_formatting must be keep or clear")
    if typed and params.get("tracked", True) and direct == "clear":
        raise ValueError("tracked typed-number conversion cannot clear formatting")
    select = copy.deepcopy(effect.get("select", {}))
    if (not typed and select) or (typed and set(select) - {"paragraphs", "include_tables"}):
        raise ValueError("unsupported numbering selector")
    if "include_tables" in select and not isinstance(select["include_tables"], bool):
        raise ValueError("include_tables must be boolean")
    if ctx.src.related(STYLES_REL) is None:
        raise ValueError("numbering conversion requires a source styles part")
    imports, md5 = _imports(ctx, effect, params)
    assertion = {"kind": "typed_numbers" if typed else "converted_numbering",
                 "op_index": effect["id"], "part": ctx.src.main_part(), "params": params,
                 "template": params.get("template"), "template_md5": md5,
                 "author": (params.get("author") or effect.get("_assertion", {}).get("author")
                            or effect.get("author") or default_author())}
    if typed:
        assertion["select"] = select
    source_ctx = Ctx(ctx.src, ctx.src, {"property_assertions": imports})
    view = PartView(ctx.src.xml(assertion["part"]))
    definitions = v_list_defs(ctx.src)
    source_count = v_count(view.root, ctx.src_styles, definitions)
    errors = []
    dest = v_destination(source_ctx, assertion, params, source_count, errors, assertion["kind"])
    if dest is None or errors:
        raise ValueError("; ".join(errors) or "cannot resolve approved numbering destination")
    if typed:
        converted, predicted = _typed(view, ctx.src_styles, definitions, source_count, dest, params, select)
    else:
        converted, predicted = _numbered(view, ctx.src_styles, source_count, dest, params)
    if not converted and not params.get("allow_missing", True):
        raise ValueError("no paragraphs match the approved numbering conversion")
    changed = []
    counts = {"expected_paragraphs": 0, "expected_restyled": 0, "expected_runs": 0, "expected_marks": 0}
    run_names, paragraph_names = v_cleared_sets() if direct == "clear" else (set(), set())
    for row in converted:
        index, level = row[:2]
        paragraph = view.pars[index]
        pstyle = paragraph.find(W + "pPr/" + W + "pStyle")
        current = pstyle.get(W + "val") if pstyle is not None else ctx.src_styles.default.get("paragraph")
        restyle = current != dest.sids[level]
        own = paragraph.find(W + "pPr/" + W + "numPr") is not None
        clearing = (v_decide(paragraph, view.runs[index], ctx.src_styles, run_names, paragraph_names)
                    if direct == "clear" else None)
        if not typed and not restyle and not own and not (clearing is not None and clearing.changed):
            continue
        changed.append([index, level, restyle, own])
        counts["expected_paragraphs"] += int(restyle or own or bool(clearing is not None and clearing.ppr))
        counts["expected_restyled"] += int(restyle)
        if clearing is not None:
            counts["expected_runs"] += len(clearing.runs)
            counts["expected_marks"] += int(clearing.mark is not None)
    assertion.update(counts)
    assertion.update(dest_num=dest.scheme.num_id, styles=list(dest.sids), requested=list(dest.requested),
                     converted=converted, changed=changed, predicted=[v_row(item) for item in predicted])
    return [assertion, *imports], len(converted)
