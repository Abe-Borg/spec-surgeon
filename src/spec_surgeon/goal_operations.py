"""Source-side binding and compilation for approved style, table and list effects.

The public fields describe the desired change and original scope. The verifier has
its own grammar, selection, cardinality and original-to-final derivation; none of
the assertions produced here are accepted as evidence of the final outcome.
"""
from __future__ import annotations

import copy
from pathlib import Path

from .goals import GoalError
from .ooxml import block_full_text, paragraphs
from .ops import JobException, get_op
from .ops.selectors import (SCOPE_KINDS, StyleSheet, check_paragraph_selector,
                            paragraph_selected, scope_parts)


OPERATIONS = {
    "review_comments": "add_review_comments",
    "paragraph_borders": "set_borders_shading",
    "paragraph_style": "apply_style",
    "style_mapping": "map_styles",
    "proofing_language": "set_language",
    "table_format": "set_table_props",
    "table_style": "apply_table_style",
    "numbering": "convert_numbering",
    "typed_numbering": "convert_typed_numbers",
}
TEMPLATED = {"paragraph_style", "style_mapping", "table_style", "numbering", "typed_numbering"}
AUTHORED = {"paragraph_style", "style_mapping", "numbering", "typed_numbering", "paragraph_borders"}
COUNTS = {
    "review_comments": "selected original document paragraphs receiving literal approved comments",
    "paragraph_borders": "selected original paragraphs, including paragraphs already as requested",
    "paragraph_style": "selected original paragraphs, including paragraphs already in the requested style",
    "style_mapping": "original style usages changed: paragraphs, runs, paragraph marks and tables",
    "proofing_language": "one whole document, including its styles and all text parts",
    "table_format": "selected eligible original tables, including tables already as requested",
    "table_style": "selected eligible original tables, including tables already in the requested style",
    "numbering": "original automatically numbered paragraphs selected for conversion",
    "typed_numbering": "original paragraphs with a convertible typed number",
}


def operation_params(effect, author="Abraham Borg"):
    params = copy.deepcopy(effect["params"])
    if "template" in effect:
        params["template"] = effect["template"]
    if effect["type"] in AUTHORED:
        params.setdefault("author", author)
    if effect["type"] == "paragraph_borders":
        # Stable approval semantics independent of selector shape.
        params.setdefault("tracked", False)
    return params


def operation_spec(effect, request, author="Abraham Borg"):
    params = operation_params(effect, author)
    mode = "formatting"
    if effect["type"] == "review_comments":
        mode = "untracked"
    if effect["type"] == "typed_numbering":
        mode = "content" if params.get("tracked", True) else "untracked"
    return {"request": request, "summary": f"Approved effect {effect['id']}: {effect['type']}",
            "mode": mode, "ops": [{"op": OPERATIONS[effect["type"]],
            "select": copy.deepcopy(effect.get("select", {})), "params": params}]}


def validate_operation_effect(effect):
    from .editspec import validate_spec
    from .ops.table_select import check_table_selector

    allowed = {"id", "type", "select", "params", "expected_count"}
    if effect["type"] in TEMPLATED:
        allowed |= {"template", "template_sha256"}
    if set(effect) - allowed:
        raise GoalError(f"{effect['type']} has unsupported fields: {sorted(set(effect) - allowed)}")
    if not isinstance(effect.get("select", {}), dict) or not isinstance(effect.get("params"), dict):
        raise GoalError("operation effect select and params must be objects")
    if "template" in effect["params"]:
        raise GoalError("put an approved template and template_sha256 at effect level, never inside params")
    count = effect.get("expected_count")
    if count != "all" and (type(count) is not int or count < 0):
        raise GoalError("expected_count must be a non-negative exact count or 'all' original matches")
    if ("template" in effect) != ("template_sha256" in effect):
        raise GoalError("template and template_sha256 must be supplied together")
    if "template" in effect:
        import re
        if not isinstance(effect["template"], str) or not Path(effect["template"]).is_absolute():
            raise GoalError("approved template must have an absolute path")
        if not isinstance(effect["template_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", effect["template_sha256"]):
            raise GoalError("template_sha256 must pin the approved template bytes")
    try:
        validate_spec(operation_spec(effect, "Validate the approved document effect"))
        select, params = effect.get("select", {}), operation_params(effect)
        if "scope" in select:
            scope = select["scope"]
            scope = [scope] if isinstance(scope, str) else scope
            if not scope or any(s not in SCOPE_KINDS for s in scope) or len(scope) != len(set(scope)):
                raise GoalError("select.scope must name distinct supported text scopes")
        if "paragraphs" in select:
            check_paragraph_selector(select["paragraphs"])
        if "tables" in select:
            check_table_selector(select["tables"])
        if "author" in params and (not isinstance(params["author"], str) or not params["author"].strip()):
            raise GoalError("params.author must be non-empty literal text")
        kind = effect["type"]
        if kind == "review_comments":
            from .ops.add_comments import clean_request
            clean_request(select, params)
        elif kind == "paragraph_borders":
            from .ops.set_borders_shading import clean_params
            if set(select) - {"scope", "paragraphs"}:
                raise GoalError("paragraph_borders selects paragraphs, not style definitions")
            clean_params(params)
        elif kind == "proofing_language":
            from .ops.set_language import clean_params
            clean_params(params)
        elif kind == "table_format":
            from .ops.set_table_props import clean_params
            clean_params(params)
        elif kind == "style_mapping":
            from .ops.map_styles import clean_params
            clean_params(params)
        elif kind in {"numbering", "typed_numbering"}:
            from .ops.numbering_convert import scheme_params
            levels, _, _ = scheme_params(params)
            if kind == "numbering":
                from .ops.convert_numbering import clean_from, clean_level_map
                clean_from(params.get("from", "main"), len(levels))
                clean_level_map(params.get("level_map"), len(levels))
            elif params.get("tracked", True) and params.get("direct_formatting", "keep") == "clear":
                raise GoalError("tracked typed-number conversion cannot clear direct formatting")
        else:
            if not isinstance(params.get("style"), str) or not params["style"].strip():
                raise GoalError("params.style must identify the requested style")
            if "existing" in params and "template" not in effect:
                raise GoalError("params.existing requires an approved template")
    except JobException as exc:
        raise GoalError(str(exc)) from exc


def source_operation_count(pkg, effect, *, author="Abraham Borg"):
    """Count the original eligible targets analytically, without applying edits.

Planning is used only to establish source-side selection evidence for complex
mapping/list families. The independent verifier derives its own evidence.
"""
    kind, select = effect["type"], effect.get("select", {})
    if kind == "review_comments":
        from .ops.add_comments import derive
        return len(derive(pkg, select, operation_params(effect, author))["entries"])
    if kind == "proofing_language":
        return 1
    if kind in {"paragraph_style", "paragraph_borders"}:
        sheet, count = StyleSheet(pkg), 0
        for part in scope_parts(pkg, select.get("scope", ["document"])):
            pars = paragraphs(pkg.xml(part))
            count += sum(paragraph_selected(select.get("paragraphs", "all"), p, i, block_full_text(p), sheet, pars)
                         for i, p in enumerate(pars))
        return count
    if kind in {"table_format", "table_style"}:
        from .ops.table_select import in_text_box, selected_tables, selects_all, style_chain
        from .ops.set_table_props import clean_params, disposition
        params, sheet, count = operation_params(effect, author), StyleSheet(pkg), 0
        asked = clean_params(params) if kind == "table_format" else None
        pred = select.get("tables", "all")
        for part in scope_parts(pkg, select.get("scope", ["document"])):
            for ordinal, table in selected_tables(pkg.xml(part), pred, sheet):
                if kind == "table_format":
                    chain, _ = style_chain(table, sheet)
                    if disposition(table, chain, asked, selects_all(pred), f"{part} table {ordinal}"):
                        continue
                elif in_text_box(table):
                    if not selects_all(pred):
                        raise JobException("a selected table lies in a text box", "textbox")
                    continue
                count += 1
        return count
    fragment = get_op(OPERATIONS[kind]).plan(pkg, copy.deepcopy(select), operation_params(effect, author))
    wanted = {"style_mapping": "mapped_styles", "numbering": "converted_numbering", "typed_numbering": "typed_numbers"}[kind]
    assertion = next(a for a in fragment.property_assertions if a["kind"] == wanted)
    return sum(assertion["expected"].values()) if kind == "style_mapping" else len(assertion["converted"])


def operation_catalog():
    from .ops import OPS
    result = {}
    for kind, name in OPERATIONS.items():
        params = copy.deepcopy(OPS[name].params_schema)
        params.get("properties", {}).pop("template", None)
        result[kind] = {"fields": ["select", "params", "expected_count"] +
                       (["template", "template_sha256"] if kind in TEMPLATED else []),
                       "select": copy.deepcopy(OPS[name].select_schema), "params": params,
                       "expected_count": "non-negative exact count, or 'all' original matches; " + COUNTS[kind],
                       "binding": "original source SHA256 and exact source count; selections are independently re-resolved",
                       "template": "optional absolute path plus exact SHA256, never inside params" if kind in TEMPLATED else None}
    return result
