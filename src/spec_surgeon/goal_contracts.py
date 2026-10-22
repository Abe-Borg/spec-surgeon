"""Composable approved effects and source bindings, independent of execution recipes.

Locators in a target are approved alternatives whose candidate sets are unioned. A
style hint helps explain evidence; only an explicit style_id locator restricts it.
Bindings freeze the actual source paragraphs and requested cardinality. A different
editing method must still satisfy verify.effects against the original bytes.
"""
from __future__ import annotations

import copy
import math
import re
from pathlib import Path
from typing import Any, Dict

from .goals import GoalError, NeedsInput, _digest, _visible_text
from .goal_operations import (OPERATIONS, operation_catalog, operation_spec,
                              source_operation_count, validate_operation_effect)
from .ooxml import block_full_text, paragraphs
from .ops.selectors import StyleSheet
from .package import DocxPackage, W


KIND = "document_effects"
SCOPES = ("document", "headers", "footers", "footnotes", "endnotes", "comments")
EFFECTS = ("format", "replace_text", "page_setup", "insert_paragraph", "delete_paragraphs",
           "header_footer_template", "remove_header_footer", *OPERATIONS)
RUN = {"b", "i", "caps", "smallCaps", "strike"}
PARAGRAPH = {"keepNext", "keepLines", "widowControl", "pageBreakBefore"}
LOCATORS = {"exact_text", "contains_text", "text_regex", "style_id", "role"}
ANCHORS = {"after_text", "before_text", "section_number", "style_hint", "in_table"}
MARGINS = {"top", "bottom", "left", "right", "header", "footer", "gutter"}
TEMPLATE_PARAMS = {"template_sections", "kinds", "slot_fills", "slot_markers", "highlight_colors",
                   "rescale_tabs", "align_header_footer_distance", "align_title_page", "import_missing_styles", "tracked"}
SECTION = re.compile(r"^SECTION\s+([0-9]{2}\s?[0-9]{2}\s?[0-9]{2})(?:\s*[-–—:]\s*(.*))?$")


class GoalMethodRequired(GoalError):
    """The approved effect is verifiable but needs another concrete editing method."""

    def __init__(self, reason, binding=None):
        super().__init__(reason, "goal-method")
        self.binding = copy.deepcopy(binding)


def _object(value, allowed, where):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise GoalError(f"{where} must be an object with only {sorted(allowed)}")


def _text(value, where, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise GoalError(f"{where} must be {'literal' if empty else 'non-empty literal'} text")


def _boolean_map(value, allowed, where):
    _object(value, allowed, where)
    if not value or any(type(v) is not bool for v in value.values()):
        raise GoalError(f"{where} needs explicit boolean properties")


def _sections(sections, *, content=False):
    if content and isinstance(sections, dict):
        from .ops import JobException
        from .ops.selectors import check_section_selector
        try:
            check_section_selector(sections)
        except JobException as exc:
            raise GoalError(str(exc)) from exc
        return
    if sections != "all" and (not isinstance(sections, list) or not sections
            or any(type(x) is not int or x < 0 for x in sections) or len(set(sections)) != len(sections)):
        raise GoalError("sections must be all or distinct non-negative section indices")


def _enum_list(values, allowed, where):
    if (not isinstance(values, list) or not values or any(not isinstance(v, str) for v in values)
            or len(set(values)) != len(values) or set(values) - set(allowed)):
        raise GoalError(f"{where} must be distinct values from {sorted(allowed)}")


def _fill_rule(rule):
    if not isinstance(rule, dict):
        raise GoalError("template fill rule must be an object")
    kind = rule.get("rule")
    common = {"rule", "in_template"}
    if kind == "first_match":
        _object(rule, common | {"rules"}, "first_match fill")
        if not isinstance(rule.get("rules"), list) or not rule["rules"]:
            raise GoalError("first_match fill needs non-empty rules")
        for child in rule["rules"]:
            _fill_rule(child)
    elif kind in {"literal", "regex", "paragraph_after"}:
        allowed = common | ({"value", "strip"} if kind == "literal" else
                            {"pattern" if kind == "regex" else "anchor_regex", "source", "section",
                             "occurrence", "strip", "flexible_whitespace"})
        _object(rule, allowed, "template fill")
        for key in ("strip", "flexible_whitespace"):
            if key in rule and type(rule[key]) is not bool:
                raise GoalError(f"fill {key} must be boolean")
        if kind == "literal":
            _text(rule.get("value"), "literal fill.value")
            if rule.get("strip", True) and rule["value"] != rule["value"].strip():
                raise GoalError("literal fill must preserve exact approved wording; use strip:false for surrounding whitespace")
        else:
            key = "pattern" if kind == "regex" else "anchor_regex"
            _text(rule.get(key), key)
            try:
                re.compile(rule[key])
            except re.error as exc:
                raise GoalError(f"invalid template fill regex: {exc}") from exc
            if rule.get("source", "document") not in {"document", "header.default", "header.first", "header.even",
                                                        "footer.default", "footer.first", "footer.even"}:
                raise GoalError("unsupported template fill source")
            if "section" in rule and (type(rule["section"]) is not int or rule["section"] < 0):
                raise GoalError("fill section must be a non-negative index")
            if rule.get("occurrence", "all") not in {"all", "first"}:
                raise GoalError("fill occurrence must be all or first")
    else:
        raise GoalError("fill rule must be literal, regex, paragraph_after or first_match")
    if "in_template" in rule:
        _text(rule["in_template"], "fill.in_template")


def _template(effect):
    path = effect.get("template")
    _text(path, "template")
    if not Path(path).is_absolute():
        raise GoalError("approved template must have an absolute path")
    digest = effect.get("template_sha256")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise GoalError("template_sha256 must pin the approved template bytes")
    if effect.get("sections", "all") != "all":
        raise GoalError("header_footer_template applies to all sections")
    params = effect.get("params")
    _object(params, TEMPLATE_PARAMS, "template params")
    sections = params.get("template_sections")
    if (not isinstance(sections, dict) or set(sections) not in ({"all"}, {"cover"}, {"cover", "body"})
            or any(type(v) is not int or v < 0 for v in sections.values())):
        raise GoalError("template_sections needs all or cover/body non-negative indices")
    fills = params.get("slot_fills")
    if not isinstance(fills, dict) or any(not isinstance(k, str) or not k for k in fills):
        raise GoalError("slot_fills must map slot names to explicit fill rules")
    for rule in fills.values():
        _fill_rule(rule)
    _enum_list(params.get("kinds", ["header", "footer"]), {"header", "footer"}, "template kinds")
    markers = params.get("slot_markers", ["highlight", "braces"])
    _enum_list(markers, {"highlight", "braces", "reference"}, "slot_markers")
    if "reference" in markers and markers != ["reference"]:
        raise GoalError("reference slot marker must be used alone")
    if "highlight_colors" in params:
        colors = params["highlight_colors"]
        if not isinstance(colors, list) or not colors or any(not isinstance(c, str) or not c for c in colors):
            raise GoalError("highlight_colors needs non-empty color strings")
    for key in ("rescale_tabs", "align_header_footer_distance", "align_title_page", "import_missing_styles", "tracked"):
        if key in params and type(params[key]) is not bool:
            raise GoalError(f"template {key} must be boolean")
    if params.get("tracked", False):
        raise GoalError("template replacement is untracked")


def _target(target):
    _object(target, {"scope", "all", "locators", "cardinality"}, "target")
    scope = target.get("scope", ["document"])
    if (not isinstance(scope, list) or not scope or any(not isinstance(s, str) or s not in SCOPES for s in scope)
            or len(set(scope)) != len(scope)):
        raise GoalError(f"target.scope must be distinct values from {list(SCOPES)}")
    cardinality = target.get("cardinality", 1)
    if cardinality != "all" and (type(cardinality) is not int or cardinality < 1):
        raise GoalError("target.cardinality must be a positive exact count or 'all'")
    if "all" in target:
        if target["all"] is not True or "locators" in target:
            raise GoalError("target.all must be true and cannot be combined with locators")
        return
    locators = target.get("locators")
    if not isinstance(locators, list) or not locators:
        raise GoalError("target needs all:true or a non-empty locators list")
    for locator in locators:
        _object(locator, LOCATORS | ANCHORS, "locator")
        if len(set(locator) & LOCATORS) != 1:
            raise GoalError("each locator needs exactly one exact_text, contains_text, text_regex, style_id or role")
        for key, value in locator.items():
            if key == "in_table":
                if type(value) is not bool:
                    raise GoalError("locator.in_table must be boolean")
            else:
                _text(value, "locator." + key)
        if "role" in locator and locator["role"] != "section_title":
            raise GoalError("the supported semantic role is section_title")
        if "text_regex" in locator:
            try:
                re.compile(locator["text_regex"])
            except re.error as exc:
                raise GoalError(f"invalid approved text_regex: {exc}") from exc


def _disjoint_style_ranges(first, second):
    """A narrow composition proof: distinct original paragraph intervals only."""
    if first["type"] != "paragraph_style" or second["type"] != "paragraph_style":
        return False
    selections = [effect.get("select", {}) for effect in (first, second)]
    predicates = [selection.get("paragraphs") for selection in selections]
    if any(not isinstance(pred, dict) or set(pred) != {"index_range"} for pred in predicates):
        return False
    scopes = []
    for selection in selections:
        scope = selection.get("scope", ["document"])
        scopes.append({scope} if isinstance(scope, str) else set(scope))
    if not scopes[0] & scopes[1]:
        return True
    left, right = [pred["index_range"] for pred in predicates]
    return left[1] <= right[0] or right[1] <= left[0]


def validate_contract(contract):
    _object(contract, {"kind", "effects", "request", "author"}, "goal")
    if contract.get("kind") != KIND:
        raise GoalError("expected document_effects goal")
    for key in ("request", "author"):
        if key in contract:
            _text(contract[key], key)
    effects = contract.get("effects")
    if not isinstance(effects, list) or not effects:
        raise GoalError("effects must be a non-empty list")
    ids = set()
    for effect in effects:
        if not isinstance(effect, dict):
            raise GoalError("every effect must be an object")
        _text(effect.get("id"), "effect.id")
        if effect["id"] in ids:
            raise GoalError("effect IDs must be unique")
        ids.add(effect["id"])
        kind = effect.get("type")
        if kind not in EFFECTS:
            raise GoalError(f"unsupported effect type {kind!r}; supported: {list(EFFECTS)}")
        if kind in OPERATIONS:
            validate_operation_effect(effect)
            continue
        fields = {"format": {"target", "font", "run", "paragraph", "tracked", "author"},
                  "replace_text": {"target", "find", "replace", "expected_matches", "tracked", "author"},
                  "page_setup": {"sections", "margins", "size", "orientation", "different_first_page"},
                  "insert_paragraph": {"target", "text", "position", "formatting", "style", "tracked", "author"},
                  "delete_paragraphs": {"target", "tracked", "author"},
                  "header_footer_template": {"template", "template_sha256", "params", "sections"},
                  "remove_header_footer": {"sections", "kinds", "types"}}[kind]
        _object(effect, fields | {"id", "type"}, "effect")
        if kind not in {"page_setup", "header_footer_template", "remove_header_footer"}:
            _target(effect.get("target"))
        if "tracked" in effect and type(effect["tracked"]) is not bool:
            raise GoalError("effect.tracked must be boolean")
        if "author" in effect:
            _text(effect["author"], "effect.author")
        if kind == "format":
            if not any(effect.get(k) for k in ("font", "run", "paragraph")):
                raise GoalError("format needs explicit font, run or paragraph properties")
            if "font" in effect:
                font = effect["font"]
                _object(font, {"name", "size"}, "font")
                if not font:
                    raise GoalError("font cannot be empty")
                if "name" in font:
                    _text(font["name"], "font.name")
                if "size" in font:
                    size = font["size"]
                    if (isinstance(size, bool) or not isinstance(size, (int, float)) or not math.isfinite(size)
                            or not 1 <= size <= 1638 or size * 2 != int(size * 2)):
                        raise GoalError("font.size must be whole or half points between 1 and 1638")
            for key, values in (("run", RUN), ("paragraph", PARAGRAPH)):
                if key in effect:
                    _boolean_map(effect[key], values, key)
            if effect.get("run", {}).get("caps") is True and effect.get("run", {}).get("smallCaps") is True:
                raise GoalError("caps and smallCaps cannot both be true")
        elif kind == "replace_text":
            _text(effect.get("find"), "find")
            _text(effect.get("replace"), "replace", empty=True)
            if type(effect.get("expected_matches")) is not int or effect["expected_matches"] < 1:
                raise GoalError("replace_text.expected_matches must be a positive exact count")
            if any(ord(c) < 32 or ord(c) == 127 for c in effect["find"] + effect["replace"]):
                raise GoalError("a text replacement cannot contain control characters")
        elif kind == "insert_paragraph":
            _text(effect.get("text"), "inserted text")
            if any(ord(c) < 32 or ord(c) == 127 for c in effect["text"]):
                raise GoalError("inserted paragraph wording cannot contain control characters")
            if effect.get("position", "after") not in ("before", "after"):
                raise GoalError("insert position must be before or after")
            if effect.get("formatting", "inherit") not in ("inherit", "none"):
                raise GoalError("insert formatting must be inherit or none")
            if "style" in effect and effect["style"] is not None:
                _text(effect["style"], "insert style")
        elif kind == "page_setup":
            _sections(effect.get("sections", "all"), content=True)
            if not any(effect.get(k) for k in ("margins", "size", "orientation")) and "different_first_page" not in effect:
                raise GoalError("page_setup needs margins, size, orientation or different_first_page")
            if "different_first_page" in effect and type(effect["different_first_page"]) is not bool:
                raise GoalError("different_first_page must be boolean")
            for key, allowed in (("margins", MARGINS), ("size", {"w", "h"})):
                if key not in effect:
                    continue
                _object(effect[key], allowed, key)
                if not effect[key] or (key == "size" and set(effect[key]) != {"w", "h"}):
                    raise GoalError("margins cannot be empty; size needs w and h")
                for name, value in effect[key].items():
                    if (type(value) is not int or (key == "size" and value <= 0)
                            or (key == "margins" and name not in {"top", "bottom"} and value < 0)):
                        raise GoalError(f"{key}.{name} needs valid integer twips")
            if effect.get("orientation", "portrait") not in ("portrait", "landscape"):
                raise GoalError("orientation must be portrait or landscape")
        elif kind == "header_footer_template":
            _template(effect)
        elif kind == "remove_header_footer":
            _sections(effect.get("sections", "all"))
            _enum_list(effect.get("kinds", ["header", "footer"]), {"header", "footer"}, "kinds")
            _enum_list(effect.get("types", ["default", "first", "even"]), {"default", "first", "even"}, "types")
    if any(e["type"] == "review_comments" for e in effects) and len(effects) != 1:
        raise GoalError("review comments require their own approved contract after other edits; their independent proof compares whole document/comment parts")
    if any(e["type"] in OPERATIONS for e in effects) and any(e["type"] in {
            "replace_text", "insert_paragraph", "delete_paragraphs", "header_footer_template", "remove_header_footer"
            } for e in effects):
        raise GoalError("style, language, table and numbering effects require stable paragraph and package structure; "
                        "approve wording, paragraph insertion/deletion and header/footer graph effects separately")
    if any(e["type"] == "table_style" for e in effects) and any(e["type"] == "table_format"
            and set(e["params"]) & {"borders", "cell_margins", "width", "clear_cell_overrides"} for e in effects):
        raise GoalError("table style plus table borders, margins, width or cell overrides requires separate approved goals; "
                        "the independent proof compares each original table property container whole. "
                        "Table style may combine with row-only repeat_header_rows/cant_split formatting")
    if any(e["type"] == "proofing_language" for e in effects) and any(
            e["type"] in {"paragraph_style", "style_mapping", "table_style", "numbering", "typed_numbering"}
            or (e["type"] == "format" and (e.get("font") or e.get("run"))) for e in effects):
        raise GoalError("proofing language and run/font formatting or style changes require separate approved goals; "
                        "the independent language proof compares each original run/style property container whole")
    for effect in effects:
        if effect["type"] in OPERATIONS and effect["params"].get("direct_formatting") in {"clear", "replace_style_owned"} and any(
                other["id"] != effect["id"] and other["type"] != "page_setup"
                and not _disjoint_style_ranges(effect, other) for other in effects):
            raise GoalError("clearing direct formatting and other formatting/style/language/numbering effects require "
                            "separate approved goals; only page setup and paragraph styles with provably disjoint "
                            "exact index_range selections may compose")
    hf = [e for e in effects if e["type"] in {"header_footer_template", "remove_header_footer"}]
    if len(hf) > 1:
        raise GoalError("use one header/footer graph effect per approved contract")
    if hf:
        graph = hf[0]
        kinds = graph.get("params", {}).get("kinds", ["header", "footer"]) if graph["type"] == "header_footer_template" \
            else graph.get("kinds", ["header", "footer"])
        overwritten = {kind + "s" for kind in kinds}
        for effect in effects:
            if set(effect.get("target", {}).get("scope", ["document"])) & overwritten:
                raise GoalError("header/footer graph replacement cannot overlap paragraph effects in the same contract")
            if (graph["type"] == "header_footer_template" and graph["params"].get("align_header_footer_distance", True)
                    and effect["type"] == "page_setup" and set(effect.get("margins", {})) & {"header", "footer"}):
                raise GoalError("template distance alignment conflicts with explicit page header/footer margins")


def _role_indices(texts):
    result = set()
    for i, text in enumerate(texts):
        match = SECTION.fullmatch(text.strip())
        if not match:
            continue
        if match.group(2) and match.group(2).strip():
            result.add(i)
        else:
            for j in range(i + 1, len(texts)):
                if texts[j].strip():
                    if not SECTION.fullmatch(texts[j].strip()):
                        result.add(j)
                    break
    return result


def _matches(locator, index, texts, style, in_table, role_indices):
    text = texts[index]
    if "exact_text" in locator and text != locator["exact_text"]:
        return False
    if "contains_text" in locator and locator["contains_text"] not in text:
        return False
    if "text_regex" in locator and re.search(locator["text_regex"], text) is None:
        return False
    if "style_id" in locator and style != locator["style_id"]:
        return False
    if locator.get("role") == "section_title" and index not in role_indices:
        return False
    if "in_table" in locator and locator["in_table"] != in_table:
        return False
    if "after_text" in locator and locator["after_text"] not in texts[:index]:
        return False
    if "before_text" in locator and locator["before_text"] not in texts[index + 1:]:
        return False
    if "section_number" in locator:
        if locator.get("role") == "section_title":
            preceding = [m for t in texts[:index + 1] if (m := SECTION.fullmatch(t.strip()))]
            if not preceding or preceding[-1].group(1) != locator["section_number"]:
                return False
        else:
            preceding = [t for t in texts[:index + 1] if t.startswith("SECTION ")]
            if not preceding or preceding[-1] != "SECTION " + locator["section_number"]:
                return False
    return True


def _candidates(pkg, source_hash, target):
    sheet, kinds = StyleSheet(pkg), pkg.parts_by_kind()
    result = []
    for scope in target.get("scope", ["document"]):
        for part in kinds[scope]:
            pars = paragraphs(pkg.xml(part))
            texts = [_visible_text(p) for p in pars]
            roles = _role_indices(texts)
            for index, paragraph in enumerate(pars):
                style = sheet.paragraph_style_id(paragraph)
                in_table = any(p.tag == W + "tc" for p in paragraph.iterancestors())
                matches = [i for i, loc in enumerate(target.get("locators", []))
                           if _matches(loc, index, texts, style, in_table, roles)]
                if not target.get("all") and not matches:
                    continue
                identity = {"source_sha256": source_hash, "part": part, "paragraph_index": index, "text": texts[index]}
                result.append({"candidate_id": _digest(identity), "part": part, "scope": scope,
                               "paragraph_index": index, "text": texts[index], "style_id": style,
                               "in_table": in_table, "matched_locators": matches})
    return result


def resolve_goal(source, contract):
    import hashlib
    validate_contract(contract)
    _check_templates(contract)
    source = Path(source)
    data = source.read_bytes()
    source_hash = hashlib.sha256(data).hexdigest()
    pkg = DocxPackage(data, name=str(source))
    binding = {"source_sha256": source_hash, "contract_sha256": _digest(contract), "effects": {}}
    resolutions = {}
    try:
        for effect in contract["effects"]:
            if effect["type"] in OPERATIONS:
                from .ops import JobException
                try:
                    count = source_operation_count(pkg, effect, author=contract.get("author", "Abraham Borg"))
                except JobException as exc:
                    raise GoalMethodRequired(f"cannot bind original {effect['type']} targets: {exc}") from exc
                expected_count = effect["expected_count"]
                resolved = expected_count == "all" or count == expected_count
                reason = (f"bound all {count} original eligible target(s)" if resolved else
                          f"expected {expected_count} original target(s), found {count}; revise the approved scope or count")
                resolutions[effect["id"]] = {"status": "resolved" if resolved else "needs_input", "reason": reason,
                                             "candidates": [], "expected": 0, "source_count": count}
                if resolved:
                    binding["effects"][effect["id"]] = {"targets": [], "choice": "unique", "source_count": count}
                continue
            target = effect.get("target")
            candidates = _candidates(pkg, source_hash, target) if target else []
            expected = (len(candidates) if target and (target.get("cardinality") == "all"
                         or (target.get("all") and "cardinality" not in target))
                        else target.get("cardinality", 1) if target else 0)
            absent_optional_format = effect["type"] == "format" and target and target.get("all") is True
            resolved = len(candidates) == expected and (bool(candidates) or target is None or absent_optional_format)
            reason = (f"resolved {len(candidates)} original target(s)" if resolved else
                      f"expected {expected} target(s), found {len(candidates)}; explicit target choice or clarification required")
            resolutions[effect["id"]] = {"status": "resolved" if resolved else "needs_input", "reason": reason,
                                         "candidates": candidates, "expected": expected}
            if resolved:
                binding["effects"][effect["id"]] = {"targets": candidates, "choice": "unique"}
    finally:
        pkg.close()
    complete = len(binding["effects"]) == len(contract["effects"])
    return {"status": "resolved" if complete else "needs_input", "reason": "all approved effects resolved" if complete
            else "one or more effects need clarification of the approved scope, count or source-bound target choice", "source": str(source.resolve()),
            "source_sha256": source_hash, "contract_sha256": _digest(contract), "effects": resolutions,
            "candidates": [{**candidate, "effect_id": eid} for eid, row in resolutions.items() for candidate in row["candidates"]],
            "proposed_binding": copy.deepcopy(binding), "binding": binding if complete else None}


def _checked_binding(resolution, contract, binding, require_complete=True):
    result = copy.deepcopy(resolution["proposed_binding"])
    if binding is not None:
        _object(binding, {"source_sha256", "contract_sha256", "effects"}, "binding")
        if any(binding.get(k) != resolution[k] for k in ("source_sha256", "contract_sha256")):
            raise GoalError("source or approved goal changed since binding", "stale-binding")
        if not isinstance(binding.get("effects"), dict) or set(binding["effects"]) - set(resolution["effects"]):
            raise GoalError("binding has unknown or invalid effects", "stale-binding")
        effects = {e["id"]: e for e in contract["effects"]}
        for eid, choice in binding["effects"].items():
            extended = effects[eid]["type"] in OPERATIONS
            _object(choice, {"targets", "choice"} | ({"source_count"} if extended else set()), "effect binding")
            row = resolution["effects"][eid]
            if extended and (choice.get("choice") != "unique" or type(choice.get("source_count")) is not int
                             or choice["source_count"] != row["source_count"] or row["status"] != "resolved"):
                raise GoalError("bound operation count differs from the current source or approved count", "stale-binding")
            if choice.get("choice") not in ("unique", "user") or not isinstance(choice.get("targets"), list):
                raise GoalError("invalid effect target choice", "stale-binding")
            selected = choice["targets"]
            if len(selected) != row["expected"] or len({_digest(c) for c in selected}) != len(selected):
                raise GoalError("selected targets do not preserve approved cardinality", "goal-target")
            allowed = {_digest(c) for c in row["candidates"]}
            if any(_digest(c) not in allowed for c in selected):
                raise GoalError("bound target evidence differs from the current source", "stale-binding")
            target = effects[eid].get("target", {})
            if (choice["choice"] == "unique" or target.get("all") or target.get("cardinality") == "all") \
                    and {_digest(c) for c in selected} != allowed:
                raise GoalError("this effect requires its entire original target set", "goal-target")
            result["effects"][eid] = copy.deepcopy(choice)
    if require_complete and len(result["effects"]) != len(contract["effects"]):
        raise NeedsInput(resolution["reason"], resolution["candidates"], resolution=resolution)
    return result


def choose_goal(source, contract, candidate_ids, *, effect_id=None, binding=None):
    resolution = resolve_goal(source, contract)
    result = _checked_binding(resolution, contract, binding, require_complete=False)
    if effect_id is None:
        unresolved = [eid for eid in resolution["effects"] if eid not in result["effects"]]
        if len(unresolved) != 1:
            raise GoalError("specify the effect_id for this target choice", "goal-target")
        effect_id = unresolved[0]
    if effect_id not in resolution["effects"]:
        raise GoalError("unknown effect_id", "goal-target")
    if next(e for e in contract["effects"] if e["id"] == effect_id)["type"] in OPERATIONS:
        raise GoalError("operation target counts cannot be changed by choosing paragraphs; revise the reviewed scope or count", "goal-target")
    ids = [candidate_ids] if isinstance(candidate_ids, str) else candidate_ids
    if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
        raise GoalError("candidate_ids must be distinct source candidate identifiers", "goal-target")
    candidates = resolution["effects"][effect_id]["candidates"]
    selected = [c for c in candidates if c["candidate_id"] in ids]
    if len(selected) != len(ids):
        raise GoalError("selected candidate is not in the current source", "stale-binding")
    result["effects"][effect_id] = {"targets": selected, "choice": "user"}
    return _checked_binding(resolution, contract, result, require_complete=False)


def _selection(pkg, candidate):
    index, scope = candidate["paragraph_index"], candidate["scope"]
    raw = block_full_text(paragraphs(pkg.xml(candidate["part"]))[index])
    collisions = []
    for part in pkg.parts_by_kind()[scope]:
        pars = paragraphs(pkg.xml(part))
        if index < len(pars) and block_full_text(pars[index]) == raw:
            collisions.append(part)
    if collisions != [candidate["part"]]:
        raise GoalMethodRequired("the built-in scope selector cannot isolate this exact package part; use a bound custom method")
    return {"scope": [scope], "paragraphs": {"index_range": [index, index + 1],
                                              "text_regex": r"\A" + re.escape(raw) + r"\Z"}}


def _run_selection(pkg, run):
    """One selection for a run of consecutive paragraphs of one part (``run``: candidates of one deletion effect,
    their indices consecutive): their index range, and only their exact texts within it. Each paragraph must be
    isolable on its own (``_selection``), and no other part of the scope may hold a paragraph the selection would
    reach. One stage instead of one per paragraph (the 1.8.0 live run deleted 29 paragraphs of one file in 29
    stages, each verified and rendered on its own)."""
    from .ops.selectors import _rx_matches
    for candidate in run:
        _selection(pkg, candidate)
    part, scope = run[0]["part"], run[0]["scope"]
    first = min(c["paragraph_index"] for c in run)
    last = max(c["paragraph_index"] for c in run)
    texts = [block_full_text(p) for p in paragraphs(pkg.xml(part))[first:last + 1]]
    pattern = r"\A(?:" + "|".join(re.escape(t) for t in dict.fromkeys(texts)) + r")\Z"
    for other in pkg.parts_by_kind()[scope]:
        if other != part and any(_rx_matches(pattern, block_full_text(p), p)
                                 for p in paragraphs(pkg.xml(other))[first:last + 1]):
            raise GoalMethodRequired("the built-in scope selector cannot isolate these exact paragraphs; use a bound "
                                     "custom method")
    return {"scope": [scope], "paragraphs": {"index_range": [first, last + 1], "text_regex": pattern}}


def _structural_runs(rows):
    """``rows`` (effect, target, author), last paragraph first: consecutive deletions of one effect in one part are
    one run; every insertion is a run of its own."""
    runs = []
    for effect, target, author in rows:
        last = runs[-1] if runs else None
        if (last is not None and effect["type"] == "delete_paragraphs" and last[0] is effect
                and last[1][-1]["part"] == target["part"]
                and last[1][-1]["paragraph_index"] == target["paragraph_index"] + 1):
            last[1].append(target)
        else:
            runs.append((effect, [target], author))
    return runs


def compile_goal_candidate(source, contract, binding=None):
    """Build reviewed source-bound stages; a method gap never changes the approved goal."""
    import hashlib
    from .editspec import plan_spec, validate_spec
    from .ops import JobException
    from .ops.textedit import RunText
    resolution = resolve_goal(source, contract)
    bound = _checked_binding(resolution, contract, binding)
    data = Path(source).read_bytes()
    if hashlib.sha256(data).hexdigest() != bound["source_sha256"]:
        raise GoalError("source changed during goal compilation", "stale-binding")
    pkg = DocxPackage(data, name=str(source))
    stages = []
    effects = sorted(enumerate(contract["effects"]), key=lambda pair: (
        {"page_setup": 0, "header_footer_template": 1, "remove_header_footer": 1,
         **{kind: 2 for kind in OPERATIONS}, "format": 3, "replace_text": 4,
         "delete_paragraphs": 5, "insert_paragraph": 5}[pair[1]["type"]], pair[0]))
    touched_text, structural = set(), []
    request = contract.get("request", "Carry out the explicitly approved document effects")

    def add(effect, op, select, params):
        spec = {"request": request, "summary": f"Approved effect {effect['id']}: {effect['type']}",
                "mode": "untracked" if op == "apply_header_footer_template" and _literal_fills(params) else
                        "formatting" if op in {"set_font", "set_properties", "set_page_setup", "apply_header_footer_template", "remove_header_footer"}
                        else "content" if params.get("tracked", True) else "untracked",
                "ops": [{"op": op, "select": select, "params": params}]}
        try:
            validate_spec(spec)
            plan_spec(pkg, spec)
        except (JobException, ValueError) as exc:
            raise GoalMethodRequired(f"built-in {op} cannot realize effect {effect['id']}: {exc}", bound) from exc
        stages.append({"name": effect["id"] + " — " + op, "spec": spec})

    try:
        for _, effect in effects:
            kind = effect["type"]
            author = effect.get("author", contract.get("author", "Abraham Borg"))
            targets = bound["effects"][effect["id"]]["targets"]
            if kind in OPERATIONS:
                spec = operation_spec(effect, request, contract.get("author", "Abraham Borg"))
                try:
                    validate_spec(spec)
                    plan_spec(pkg, spec)
                except (JobException, ValueError) as exc:
                    raise GoalMethodRequired(f"built-in {OPERATIONS[kind]} cannot realize effect {effect['id']}: {exc}", bound) from exc
                stages.append({"name": effect["id"] + " — " + OPERATIONS[kind], "spec": spec})
                continue
            if kind == "page_setup":
                add(effect, "set_page_setup", {"sections": effect.get("sections", "all")},
                    {k: copy.deepcopy(effect[k]) for k in ("margins", "size", "orientation", "different_first_page") if k in effect})
                continue
            if kind == "header_footer_template":
                add(effect, "apply_header_footer_template", {"sections": "all"},
                    {"template": effect["template"], **copy.deepcopy(effect["params"])})
                continue
            if kind == "remove_header_footer":
                add(effect, "remove_header_footer", {k: copy.deepcopy(effect[k]) for k in ("sections", "kinds", "types") if k in effect}, {})
                continue
            if kind in {"delete_paragraphs", "insert_paragraph"}:
                structural.extend((effect, target, author) for target in targets)
                continue
            if kind == "replace_text":
                count = sum(len(list(re.finditer(re.escape(effect["find"]),
                                 RunText(paragraphs(pkg.xml(t["part"]))[t["paragraph_index"]]).text))) for t in targets)
                if count != effect["expected_matches"]:
                    raise NeedsInput(f"effect {effect['id']} expected {effect['expected_matches']} replacements, found {count}",
                                     resolution=resolution, code="goal-count")
            # A true global selection needs one stage, not one stage for each of thousands of paragraphs.
            selections = [(None, {"scope": effect["target"].get("scope", ["document"])})] if effect["target"].get("all") \
                else [(t, _selection(pkg, t)) for t in targets]
            for target, select in selections:
                tracked = effect.get("tracked", kind != "format")
                if kind == "format":
                    if effect.get("font"):
                        if tracked and (effect.get("run") or effect.get("paragraph")):
                            raise GoalMethodRequired("combined tracked font/properties need one source-based revision method", bound)
                        font = effect["font"]
                        params = {"tracked": tracked, "author": author, "add_missing_size": True}
                        if "name" in font:
                            params["font"] = font["name"]
                            params["eastAsia"] = font["name"]
                        if "size" in font:
                            params["size"] = font["size"]
                        add(effect, "set_font", select, params)
                    if effect.get("run") or effect.get("paragraph"):
                        params = {k: copy.deepcopy(effect[k]) for k in ("run", "paragraph") if k in effect}
                        params.update(tracked=tracked, author=author, preserve_fonts=[], skip_sym_runs=False)
                        add(effect, "set_properties", select, params)
                else:
                    keys = {(t["part"], t["paragraph_index"]) for t in (targets if target is None else [target])}
                    if touched_text & keys:
                        raise GoalMethodRequired("multiple content effects on the same paragraph need one source-span method", bound)
                    touched_text |= keys
                    params = {"pairs": [{"find": effect["find"], "replace": effect["replace"], "whole_word": False}],
                              "tracked": tracked, "author": author, "allow_missing": True}
                    add(effect, "replace_table", select, params)
        seen_structure = set()
        ordered = sorted(structural, key=lambda row: (row[1]["part"], -row[1]["paragraph_index"]))
        for effect, target, author in ordered:
            key = target["part"], target["paragraph_index"]
            if key in touched_text or key in seen_structure:
                raise GoalMethodRequired("overlapping structural effects need a single source-bound method", bound)
            seen_structure.add(key)
        for effect, run, author in _structural_runs(ordered):
            params = {"tracked": effect.get("tracked", True), "author": author}
            if effect["type"] == "insert_paragraph":
                params.update({k: copy.deepcopy(effect[k]) for k in ("text", "position", "formatting", "style") if k in effect})
            add(effect, effect["type"], _selection(pkg, run[0]) if len(run) == 1 else _run_selection(pkg, run), params)
    finally:
        pkg.close()
    if not stages:
        raise GoalMethodRequired("this source has no executable effect stages", bound)
    if len(stages) == 1:
        return {"kind": "spec", "spec": stages[0]["spec"]}
    return {"kind": "chain", "chain": {"name": "Approved document effects", "stages": stages}}


def approved_goal_words(contract):
    validate_contract(contract)
    result = [effect["replace"] if effect["type"] == "replace_text" else effect["text"]
            for effect in contract["effects"] if effect["type"] in {"replace_text", "insert_paragraph"}
            and (effect.get("replace") if effect["type"] == "replace_text" else effect.get("text"))]
    for effect in contract["effects"]:
        if effect["type"] == "review_comments":
            result.extend(effect["params"][key] for key in ("text", "source") if key in effect["params"])
        if effect["type"] == "header_footer_template":
            result.extend(_literal_fills(effect["params"]))
    return result


def _literal_fills(params):
    def walk(rule):
        if rule["rule"] == "literal":
            return [rule["value"]]
        return [word for child in rule.get("rules", []) for word in walk(child)]
    return [word for rule in params.get("slot_fills", {}).values() for word in walk(rule)]


def goal_templates(contract):
    validate_contract(contract)
    return list(dict.fromkeys(Path(e["template"]).resolve() for e in contract["effects"] if "template" in e))


def _check_templates(contract):
    import hashlib
    for effect in contract["effects"]:
        if "template" not in effect:
            continue
        try:
            actual = hashlib.sha256(Path(effect["template"]).read_bytes()).hexdigest()
        except OSError as exc:
            raise GoalError(f"approved template is unavailable: {exc}", "stale-template") from exc
        if actual != effect["template_sha256"]:
            raise GoalError("approved template bytes have changed", "stale-template")


def goal_catalog():
    return {"kind": KIND, "effects": {
        "format": {"fields": ["target", "font{name,size}", "run", "paragraph", "tracked", "author"],
                   "run": sorted(RUN), "paragraph": sorted(PARAGRAPH), "tracked_default": False,
                   "font": "name sets effective ascii/hAnsi/eastAsia/cs slots; size sets effective normal w:sz in whole/half points; symbol fonts preserved; w:szCs unchanged"},
        "replace_text": {"fields": ["target", "find", "replace", "expected_matches", "tracked", "author"],
                         "matching": "exact case-sensitive literal, every source occurrence", "tracked_default": True},
        "page_setup": {"fields": ["sections", "margins", "size{w,h}", "orientation", "different_first_page"],
                       "sections": "all, distinct section indices, or {containing: paragraph predicate, with_tables: boolean, index_range:[first,last]}; all supplied conditions apply",
                       "units": "twips", "tracked": False},
        "insert_paragraph": {"fields": ["target", "text", "position", "formatting", "style", "tracked", "author"],
                             "position_default": "after", "formatting_default": "inherit", "tracked_default": True},
        "delete_paragraphs": {"fields": ["target", "tracked", "author"], "tracked_default": True},
        "header_footer_template": {"fields": ["template", "template_sha256", "params", "sections"],
            "template": "absolute file path; exact SHA256 required in the proposal before approval",
            "sections": "all", "params_required": ["template_sections", "slot_fills"],
            "params_allowed": sorted(TEMPLATE_PARAMS),
            "template_sections": "{all: index} or {cover: index, body: index}",
            "slot_fills": "{name: {rule: literal, value} | {rule: regex, pattern, source?} | {rule: paragraph_after, anchor_regex, source?} | {rule: first_match, rules: [...]}}",
            "composition": "body effects allowed; effects targeting overwritten headers/footers must be separate goals"},
        "remove_header_footer": {"fields": ["sections", "kinds", "types"], "sections_default": "all",
            "kinds_default": ["header", "footer"], "types_default": ["default", "first", "even"]},
        **operation_catalog()},
        "composition": "style/language/table/numbering effects require separate approval from wording, paragraph insertion/deletion and header/footer graph effects. Page setup may accompany each family. Paragraph style mapping with kept direct formatting may combine with run formatting; table_style with kept direct formatting may combine with table_format row properties only. Proofing language requires separate approval from run/font formatting and style-changing effects; it may combine with paragraph-only formatting and table_format. An effect that clears direct formatting may combine only with page setup. Table borders/margins/width/cell overrides require separate approval from table_style. Other overlapping property changes fail closed",
        "target": {"scopes": list(SCOPES), "all": "all:true binds every original scoped paragraph",
                   "locators": sorted(LOCATORS), "anchors": sorted(ANCHORS),
                   "cardinality": "positive exact count (default 1), or 'all' for all original matches",
                   "alternatives": "locators are an approved union; extra matches require a deliberate choice",
                   "style_hint": "evidence only, never an implicit restriction",
                   "section_title": "title following a SECTION six-digit marker, or the inline marker/title paragraph"},
        "wording": "Only replace_text.replace, insert_paragraph.text, review_comments.params.text/source and literal template fill.value are approved new wording; preserve exact text",
        "example": {"kind": KIND, "effects": [{"id": "titles", "type": "format",
                    "target": {"locators": [{"role": "section_title"}], "cardinality": "all"}, "run": {"b": True}}]}}
