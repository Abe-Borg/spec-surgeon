"""Independent original-to-final verification of immutable document effects.

Methods and generated plans are deliberately absent from this API. Targets,
literal replacement spans, formatting permissions and section changes are all
derived from the approved contract and ORIGINAL package. Checked permissions are
then removed from private trees before a complete residual comparison.
"""
from __future__ import annotations

import copy
import hashlib
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from lxml import etree

from .edits import (V_CONTAINERS, V_REVISION_TAGS, check_deleted_paragraph, check_edited_paragraph,
                    part_revision_ids, v_deletable, v_items, v_run_text)
from .goals import _hash_json, _new_marker, _revision_ids, _visible_text
from .outcomes import _conditional
from .props import (Styles, Theme, _check_effective_para, _check_effective_run, _check_ppr_change,
                    _check_rpr_change, _v_inherit_ppr, _v_inherit_rpr, _v_run_elements,
                    effective_fonts, effective_size, first_difference, sect_prs, sig, symbolic)
from .reader import STYLES_REL, THEME_REL, VPackage, W, default_author
from .template import _v_field_runs
from .goal_operations import (TYPES as OPERATION_EFFECTS, validate as validate_operation,
                              verify_operations, normalize_operations, paragraph_selector)


SCOPES = {"document", "headers", "footers", "footnotes", "endnotes", "comments"}
RUN_KEYS = {"b", "i", "caps", "smallCaps", "strike"}
PARA_KEYS = {"keepNext", "keepLines", "widowControl", "pageBreakBefore"}
MARGIN_KEYS = {"top", "bottom", "left", "right", "header", "footer", "gutter"}
PRESERVE_FONTS = {name.casefold() for name in (
    "Symbol", "Wingdings", "Wingdings 2", "Wingdings 3", "Webdings", "Marlett", "MT Extra",
    "ZapfDingbats", "Zapf Dingbats", "Bookshelf Symbol 7", "MS Reference Specialty", "MS Outlook")}
FONT_ATTRS = {W + name for name in ("ascii", "hAnsi", "eastAsia", "cs", "asciiTheme", "hAnsiTheme",
                                   "eastAsiaTheme", "cstheme")}
ROLE_MARKER = re.compile(r"SECTION\s+(?P<number>[0-9]{2}\s?[0-9]{2}\s?[0-9]{2})"
                         r"(?:\s*[-–—:]\s*(?P<title>.*))?")
TYPES = {"format", "replace_text", "page_setup", "insert_paragraph", "delete_paragraphs",
         "header_footer_template", "remove_header_footer"}
TYPES |= OPERATION_EFFECTS
PACKAGE_EFFECTS = {"page_setup", "header_footer_template", "remove_header_footer"} | OPERATION_EFFECTS


def _keys(value, allowed, where):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError(f"{where}: invalid object or unsupported keys")


def _literal(value, where, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError(f"{where}: expected {'literal' if empty else 'non-empty literal'} text")


def _target_schema(target):
    _keys(target, {"scope", "all", "locators", "cardinality"}, "target")
    scopes = target.get("scope", ["document"])
    if (not isinstance(scopes, list) or not scopes or any(not isinstance(s, str) or s not in SCOPES for s in scopes)
            or len(scopes) != len(set(scopes))):
        raise ValueError("target.scope: expected distinct supported scope kinds")
    cardinality = target.get("cardinality", "all" if target.get("all") is True else 1)
    if cardinality != "all" and (type(cardinality) is not int or cardinality <= 0):
        raise ValueError("target.cardinality: expected a positive integer or 'all'")
    if target.get("all") is True:
        if "locators" in target:
            raise ValueError("a global target cannot also have locators")
        return
    if "all" in target:
        raise ValueError("target.all is supported only as true")
    locators = target.get("locators")
    if not isinstance(locators, list) or not locators:
        raise ValueError("target needs approved locators or all:true")
    primary = {"exact_text", "contains_text", "text_regex", "style_id", "role"}
    for locator in locators:
        _keys(locator, primary | {"after_text", "before_text", "section_number", "in_table", "style_hint"}, "locator")
        if len(set(locator) & primary) != 1:
            raise ValueError("locator needs exactly one text, style, or role condition")
        for key, value in locator.items():
            if key == "in_table":
                if type(value) is not bool:
                    raise ValueError("locator.in_table must be boolean")
            else:
                _literal(value, "locator." + key)
        if "role" in locator and locator["role"] != "section_title":
            raise ValueError("unsupported semantic role")
        if "text_regex" in locator:
            re.compile(locator["text_regex"])


def _enum_values(values, allowed, where):
    if (not isinstance(values, list) or not values or any(not isinstance(value, str) for value in values)
            or len(values) != len(set(values)) or not set(values) <= set(allowed)):
        raise ValueError(f"{where}: expected distinct supported values")


def _fill_schema(rule):
    """Validate the complete approved extraction grammar, including nested fallbacks."""
    if not isinstance(rule, dict):
        raise ValueError("template fill must be an object")
    kind = rule.get("rule")
    common = {"rule", "in_template"}
    if kind == "first_match":
        _keys(rule, common | {"rules"}, "fallback fill")
        children = rule.get("rules")
        if not isinstance(children, list) or not children:
            raise ValueError("fallback fill needs non-empty rules")
        for child in children:
            _fill_schema(child)
    elif kind == "literal":
        _keys(rule, common | {"value", "strip"}, "literal fill")
        _literal(rule.get("value"), "literal fill.value")
        if rule.get("strip", True) and rule["value"] != rule["value"].strip():
            raise ValueError("literal fill must preserve approved whitespace; specify strip:false")
    elif kind in {"regex", "paragraph_after"}:
        pattern = "pattern" if kind == "regex" else "anchor_regex"
        _keys(rule, common | {pattern, "source", "section", "occurrence", "strip", "flexible_whitespace"},
              "extracted fill")
        _literal(rule.get(pattern), "fill." + pattern)
        re.compile(rule[pattern])
        if rule.get("source", "document") not in {
                "document", "header.default", "header.first", "header.even",
                "footer.default", "footer.first", "footer.even"}:
            raise ValueError("unsupported template fill source")
        if "section" in rule and (type(rule["section"]) is not int or rule["section"] < 0):
            raise ValueError("fill.section requires a non-negative index")
        if rule.get("occurrence", "all") not in {"all", "first"}:
            raise ValueError("fill.occurrence requires all or first")
    else:
        raise ValueError("unsupported template fill rule")
    for flag in ("strip", "flexible_whitespace"):
        if flag in rule and type(rule[flag]) is not bool:
            raise ValueError("fill." + flag + " must be boolean")
    if "in_template" in rule:
        _literal(rule["in_template"], "fill.in_template")


def _template_schema(effect):
    _literal(effect.get("template"), "template")
    if not Path(effect["template"]).is_absolute():
        raise ValueError("template path must be absolute")
    if not isinstance(effect.get("template_sha256"), str) or re.fullmatch("[0-9a-f]{64}", effect["template_sha256"]) is None:
        raise ValueError("template effect requires its approved SHA256")
    if effect.get("sections", "all") != "all":
        raise ValueError("template effects apply to all sections")
    params = effect.get("params")
    flags = {"rescale_tabs", "align_header_footer_distance", "align_title_page", "import_missing_styles", "tracked"}
    _keys(params, flags | {"template_sections", "kinds", "slot_fills", "slot_markers", "highlight_colors"},
          "template params")
    sections = params.get("template_sections")
    if (not isinstance(sections, dict) or set(sections) not in ({"all"}, {"cover"}, {"cover", "body"})
            or any(type(index) is not int or index < 0 for index in sections.values())):
        raise ValueError("template_sections requires all or cover/body source indices")
    fills = params.get("slot_fills")
    if not isinstance(fills, dict) or any(not isinstance(name, str) or not name for name in fills):
        raise ValueError("slot_fills requires named explicit fill rules")
    for rule in fills.values():
        _fill_schema(rule)
    _enum_values(params.get("kinds", ["header", "footer"]), {"header", "footer"}, "template kinds")
    markers = params.get("slot_markers", ["highlight", "braces"])
    _enum_values(markers, {"highlight", "braces", "reference"}, "slot_markers")
    if "reference" in markers and markers != ["reference"]:
        raise ValueError("reference slot detection must be used alone")
    if "highlight_colors" in params and (not isinstance(params["highlight_colors"], list)
            or not params["highlight_colors"]
            or any(not isinstance(value, str) or not value for value in params["highlight_colors"])):
        raise ValueError("highlight_colors requires non-empty color names")
    for flag in flags:
        if flag in params and type(params[flag]) is not bool:
            raise ValueError("template " + flag + " must be boolean")
    if params.get("tracked", False):
        raise ValueError("template replacement is untracked")


def _v_disjoint_style_intervals(a, b):
    if any(effect["type"] != "paragraph_style" for effect in (a, b)):
        return False
    intervals, scopes = [], []
    for effect in (a, b):
        select = effect.get("select", {})
        predicate = select.get("paragraphs")
        if not isinstance(predicate, dict) or list(predicate) != ["index_range"]:
            return False
        intervals.append(predicate["index_range"])
        scope = select.get("scope", ["document"])
        scopes.append([scope] if isinstance(scope, str) else scope)
    share_part_scope = any(scope in scopes[1] for scope in scopes[0])
    if not share_part_scope:
        return True
    return max(intervals[0][0], intervals[1][0]) >= min(intervals[0][1], intervals[1][1])


def _validate(contract):
    _keys(contract, {"kind", "effects", "request", "author"}, "contract")
    if contract.get("kind") != "document_effects":
        raise ValueError("expected a document_effects contract")
    for key in ("request", "author"):
        if key in contract:
            _literal(contract[key], key)
    effects = contract.get("effects")
    if not isinstance(effects, list) or not effects:
        raise ValueError("effects must be a non-empty list")
    seen = set()
    common = {"id", "type", "author", "tracked"}
    extra = {"format": {"target", "font", "run", "paragraph"},
             "replace_text": {"target", "find", "replace", "expected_matches"},
             "page_setup": {"sections", "margins", "size", "orientation", "different_first_page"},
             "insert_paragraph": {"target", "text", "position", "formatting", "style"},
             "delete_paragraphs": {"target"},
             "header_footer_template": {"template", "template_sha256", "params", "sections"},
             "remove_header_footer": {"sections", "kinds", "types"}}
    extra.update({kind: {"select", "params", "expected_count", "template", "template_sha256"}
                  for kind in OPERATION_EFFECTS})
    for effect in effects:
        if not isinstance(effect, dict) or effect.get("type") not in TYPES:
            raise ValueError("unsupported effect type")
        kind = effect["type"]
        _keys(effect, ({"id", "type"} if kind in PACKAGE_EFFECTS else common) | extra[kind], "effect")
        _literal(effect.get("id"), "effect.id")
        if effect["id"] in seen:
            raise ValueError("effect ids must be unique")
        seen.add(effect["id"])
        if "author" in effect:
            _literal(effect["author"], "effect.author")
        if "tracked" in effect and type(effect["tracked"]) is not bool:
            raise ValueError("effect.tracked must be boolean")
        if kind not in PACKAGE_EFFECTS:
            _target_schema(effect.get("target"))
        if kind in OPERATION_EFFECTS:
            validate_operation(effect)
        elif kind == "format":
            if not any(effect.get(key) for key in ("font", "run", "paragraph")):
                raise ValueError("format needs at least one property")
            for key, allowed in (("run", RUN_KEYS), ("paragraph", PARA_KEYS)):
                if key in effect:
                    _keys(effect[key], allowed, key)
                    if not effect[key] or any(type(v) is not bool for v in effect[key].values()):
                        raise ValueError(key + " requires supported boolean properties")
            if effect.get("run", {}).get("caps") and effect.get("run", {}).get("smallCaps"):
                raise ValueError("caps and smallCaps cannot both be true")
            if "font" in effect:
                font = effect["font"]
                _keys(font, {"name", "size"}, "font")
                if not font:
                    raise ValueError("font cannot be empty")
                if "name" in font:
                    _literal(font["name"], "font.name")
                if "size" in font:
                    size = font["size"]
                    if (type(size) not in (int, float) or not math.isfinite(size) or not 1 <= size <= 1638
                            or size * 2 != int(size * 2)):
                        raise ValueError("font.size requires positive half-point increments up to 1638")
        elif kind == "replace_text":
            _literal(effect.get("find"), "find")
            _literal(effect.get("replace"), "replace", empty=True)
            if type(effect.get("expected_matches")) is not int or effect["expected_matches"] < 1:
                raise ValueError("replacement needs an explicit positive expected_matches count")
            if any(ord(c) < 32 or ord(c) == 127 for c in effect["find"] + effect["replace"]):
                raise ValueError("literal replacements cannot contain control characters")
        elif kind == "insert_paragraph":
            _literal(effect.get("text"), "inserted text")
            if any(ord(char) < 32 or ord(char) == 127 for char in effect["text"]):
                raise ValueError("inserted wording cannot contain control characters")
            if effect.get("position", "after") not in ("before", "after"):
                raise ValueError("insertion position must be before or after")
            if effect.get("formatting", "inherit") not in ("inherit", "none"):
                raise ValueError("unsupported insertion formatting")
            if "style" in effect and effect["style"] is not None:
                _literal(effect["style"], "insertion style")
        elif kind == "page_setup":
            if effect.get("tracked", False):
                raise ValueError("tracked page setup is unsupported")
            sections = effect.get("sections", "all")
            if isinstance(sections, dict):
                _keys(sections, {"index_range", "with_tables", "containing"}, "page sections")
                if not sections:
                    raise ValueError("section selection cannot be empty")
                if "with_tables" in sections and type(sections["with_tables"]) is not bool:
                    raise ValueError("sections.with_tables must be boolean")
                if "containing" in sections:
                    paragraph_selector(sections["containing"])
                    if not isinstance(sections["containing"], dict) or not sections["containing"]:
                        raise ValueError("sections.containing needs a paragraph predicate")
                if "index_range" in sections:
                    paragraph_selector({"index_range": sections["index_range"]})
            elif sections != "all" and (not isinstance(sections, list) or not sections
                    or any(type(s) is not int or s < 0 for s in sections) or len(sections) != len(set(sections))):
                raise ValueError("invalid page section indices")
            if "different_first_page" in effect and type(effect["different_first_page"]) is not bool:
                raise ValueError("different_first_page must be boolean")
            if not any(effect.get(key) for key in ("margins", "size", "orientation")) and "different_first_page" not in effect:
                raise ValueError("page setup needs margins, size, orientation or different_first_page")
            for key, allowed in (("margins", MARGIN_KEYS), ("size", {"w", "h"})):
                if key in effect:
                    _keys(effect[key], allowed, key)
                    if not effect[key] or (key == "size" and set(effect[key]) != {"w", "h"}):
                        raise ValueError("size requires w/h; margins cannot be empty")
                    for name, value in effect[key].items():
                        if type(value) is not int or (key == "size" and value <= 0) or (
                                key == "margins" and name not in ("top", "bottom") and value < 0):
                            raise ValueError("page dimensions and margins require valid integer twips")
            if "orientation" in effect and effect["orientation"] not in ("portrait", "landscape"):
                raise ValueError("invalid page orientation")
        elif kind == "header_footer_template":
            _template_schema(effect)
        if kind in {"header_footer_template", "remove_header_footer"}:
            selected = effect.get("sections", "all")
            if selected != "all" and (not isinstance(selected, list) or not selected
                    or any(type(i) is not int or i < 0 for i in selected) or len(selected) != len(set(selected))):
                raise ValueError("invalid header/footer section selection")
            if kind == "remove_header_footer":
                for key, allowed in (("kinds", {"header", "footer"}), ("types", {"default", "first", "even"})):
                    _enum_values(effect.get(key, list(allowed)), allowed, "header/footer " + key)
    if any(effect["type"] == "review_comments" for effect in effects) and len(effects) != 1:
        raise ValueError("review comments require a standalone approved contract with whole-part preservation proof")
    if any(effect["type"] in OPERATION_EFFECTS for effect in effects) and any(
            effect["type"] in {"insert_paragraph", "delete_paragraphs", "replace_text", "header_footer_template",
                               "remove_header_footer"} for effect in effects):
        raise ValueError("operation outcomes need stable source structure; separate wording/paragraph/header graph effects")
    if any(effect["type"] == "table_style" for effect in effects) and any(
            effect["type"] == "table_format" and set(effect["params"]) &
            {"width", "borders", "cell_margins", "clear_cell_overrides"} for effect in effects):
        raise ValueError("table style and table-level properties need separate approved goals for independent table-property proofs; row-only table formatting may compose")
    if any(effect["type"] == "proofing_language" for effect in effects) and any(
            effect["type"] in {"paragraph_style", "style_mapping", "table_style", "numbering", "typed_numbering"}
            or (effect["type"] == "format" and (effect.get("font") or effect.get("run"))) for effect in effects):
        raise ValueError("proofing language and run/style formatting need separate approved goals because their independent proofs inspect complete property containers")
    for effect in effects:
        if effect["type"] in OPERATION_EFFECTS and effect["params"].get("direct_formatting") in {"clear", "replace_style_owned"} and any(
                other is not effect and other["type"] != "page_setup" and not _v_disjoint_style_intervals(effect, other)
                for other in effects):
            raise ValueError("clearing direct formatting needs a separate approved goal; only page setup or paragraph "
                             "styles selected by provably disjoint exact index_range intervals may compose")
    graph = [effect for effect in effects if effect["type"] in {"header_footer_template", "remove_header_footer"}]
    if len(graph) > 1:
        raise ValueError("only one approved header/footer graph effect may be composed")
    if graph:
        effect = graph[0]
        template = effect["type"] == "header_footer_template"
        params = effect["params"] if template else effect
        overwritten = {kind + "s" for kind in params.get("kinds", ["header", "footer"])}
        for other in effects:
            if set(other.get("target", {}).get("scope", ["document"])) & overwritten:
                raise ValueError("paragraph effects cannot also target replaced or removed header/footer kinds")
            if (template and params.get("align_header_footer_distance", True) and other["type"] == "page_setup"
                    and set(other.get("margins", {})) & {"header", "footer"}):
                raise ValueError("template distance alignment conflicts with approved page header/footer margins")


def _own_runs(paragraph):
    return [run for run in paragraph.iter(W + "r")
            if next((p for p in run.iterancestors() if p.tag == W + "p"), None) is paragraph]


def _role_titles(paragraphs, texts):
    found = {}
    for index, text in enumerate(texts):
        marker = ROLE_MARKER.fullmatch(text.strip())
        if marker is None:
            continue
        if (marker.group("title") or "").strip():
            found[index] = marker.group("number")
            continue
        for next_index in range(index + 1, len(texts)):
            if texts[next_index].strip():
                if ROLE_MARKER.fullmatch(texts[next_index].strip()) is None:
                    found[next_index] = marker.group("number")
                break
    return found


def _resolve(pkg, roots, styles, target, source_hash):
    result, visited = [], set()
    for scope in target.get("scope", ["document"]):
        for part in pkg.part_of_kind(scope):
            if part in visited:
                continue
            visited.add(part)
            paragraphs = list(roots[part].iter(W + "p"))
            texts = [_visible_text(p) for p in paragraphs]
            roles = _role_titles(paragraphs, texts)
            latest_section = None
            for index, (paragraph, text) in enumerate(zip(paragraphs, texts)):
                if text.startswith("SECTION "):
                    latest_section = text[len("SECTION "):]
                style_el = paragraph.find(W + "pPr/" + W + "pStyle")
                style = style_el.get(W + "val") if style_el is not None else styles.default.get("paragraph")
                table = any(p.tag == W + "tc" for p in paragraph.iterancestors())
                matches = []
                for ordinal, locator in enumerate(target.get("locators", [])):
                    if "exact_text" in locator and text != locator["exact_text"]:
                        continue
                    if "contains_text" in locator and locator["contains_text"] not in text:
                        continue
                    if "text_regex" in locator and re.search(locator["text_regex"], text) is None:
                        continue
                    if "style_id" in locator and style != locator["style_id"]:
                        continue
                    if "role" in locator and index not in roles:
                        continue
                    if "in_table" in locator and locator["in_table"] != table:
                        continue
                    section = roles.get(index) if "role" in locator else latest_section
                    if "section_number" in locator and section != locator["section_number"]:
                        continue
                    if "after_text" in locator and locator["after_text"] not in texts[:index]:
                        continue
                    if "before_text" in locator and locator["before_text"] not in texts[index + 1:]:
                        continue
                    matches.append(ordinal)
                if target.get("all") is True or matches:
                    identity = {"source_sha256": source_hash, "part": part, "paragraph_index": index, "text": text}
                    result.append({"candidate_id": _hash_json(identity), "part": part, "scope": scope,
                                   "paragraph_index": index, "text": text, "style_id": style,
                                   "in_table": table, "matched_locators": matches})
    return result


def _bound_targets(source, roots, styles, contract, binding, source_hash):
    _keys(binding, {"source_sha256", "contract_sha256", "effects"}, "binding")
    if binding.get("source_sha256") != source_hash or binding.get("contract_sha256") != _hash_json(contract):
        raise ValueError("source or approved contract changed since binding")
    expected_ids = {effect["id"] for effect in contract["effects"]}
    if not isinstance(binding.get("effects"), dict) or set(binding["effects"]) != expected_ids:
        raise ValueError("binding must cover every approved effect exactly")
    resolved = {}
    for effect in contract["effects"]:
        targets = [] if effect["type"] in PACKAGE_EFFECTS else _resolve(
            source, roots, styles, effect["target"], source_hash)
        entry = binding["effects"][effect["id"]]
        entry_keys = {"targets", "choice", "source_count"} if effect["type"] in OPERATION_EFFECTS else {"targets", "choice"}
        if not isinstance(entry, dict) or set(entry) != entry_keys or entry.get("choice") not in ("unique", "user"):
            raise ValueError("invalid effect binding")
        if effect["type"] in OPERATION_EFFECTS and (type(entry["source_count"]) is not int or entry["source_count"] < 0):
            raise ValueError("operation binding needs a nonnegative source_count")
        selected = entry["targets"]
        target = effect.get("target", {})
        if not target and entry["choice"] != "unique":
            raise ValueError("package effects require a unique empty target binding")
        cardinality = target.get("cardinality", "all" if target.get("all") is True else 1) if target else 0
        count = len(targets) if cardinality == "all" else cardinality
        empty_global_format = effect["type"] == "format" and target.get("all") is True
        if (not isinstance(selected, list) or len(selected) != count or (target and not selected and not empty_global_format)
                or len({_hash_json(c) for c in selected}) != len(selected)):
            raise ValueError(f"{effect['id']}: source target choice violates the approved cardinality")
        allowed = {_hash_json(c) for c in targets}
        chosen = {_hash_json(c) for c in selected}
        if not chosen <= allowed or ((entry["choice"] == "unique" or target.get("all") or cardinality == "all")
                                     and chosen != allowed):
            raise ValueError(f"{effect['id']}: bound targets differ from the independently resolved source evidence")
        resolved[effect["id"]] = [c for c in targets if _hash_json(c) in chosen]
    return resolved


def _merge_values(dest, values, where):
    for key, value in values.items():
        if key in dest and dest[key] != value:
            raise ValueError(f"incompatible approved effects for {where}.{key}")
        dest[key] = value


def _programs(roots, contract, targets):
    formats, replacements, insertions, deletions, pages = {}, {}, defaultdict(list), {}, {}
    pars = {part: list(root.iter(W + "p")) for part, root in roots.items()}
    for effect in contract["effects"]:
        kind = effect["type"]
        author = effect.get("author") or contract.get("author") or default_author()
        tracked = effect.get("tracked", kind in {"replace_text", "insert_paragraph", "delete_paragraphs"})
        count = 0
        for target in targets[effect["id"]]:
            key = target["part"], target["paragraph_index"]
            if kind == "format":
                fmt = formats.setdefault(key, {"run": {}, "paragraph": {}, "font": {}})
                for family in ("font", "run", "paragraph"):
                    _merge_values(fmt[family], effect.get(family, {}), str(key) + "." + family)
                    if effect.get(family):
                        tracking = "paragraph_tracking" if family == "paragraph" else "run_tracking"
                        setting = tracked, author if tracked else None
                        if tracking in fmt and fmt[tracking] != setting:
                            raise ValueError("overlapping formatting effects use incompatible tracking/authors")
                        fmt[tracking] = setting
            elif kind == "replace_text":
                text = v_run_text(v_items(pars[key[0]][key[1]]))
                found = [(match.start(), match.end(), effect["replace"], False)
                         for match in re.finditer(re.escape(effect["find"]), text)]
                count += len(found)
                edits = [edit for edit in found if text[edit[0]:edit[1]] != edit[2]]
                if edits:
                    replacement = replacements.setdefault(key, {"edits": [], "tracked": tracked, "author": author})
                    if (replacement["tracked"], replacement["author"]) != (tracked, author):
                        raise ValueError("replacements in one paragraph require the same tracking and author")
                    replacement["edits"].extend(edits)
            elif kind == "insert_paragraph":
                insertions[key].append({**effect, "tracked": tracked, "author": author})
            elif kind == "delete_paragraphs":
                if key in deletions:
                    raise ValueError("a paragraph cannot be deleted twice")
                deletions[key] = {"tracked": tracked, "author": author}
        if kind == "replace_text" and count != effect["expected_matches"]:
            raise ValueError(f"{effect['id']}: found {count} exact source matches, expected {effect['expected_matches']}")
        if kind == "page_setup":
            pages[effect["id"]] = effect
    for fmt in formats.values():
        if fmt["run"].get("caps") and fmt["run"].get("smallCaps"):
            raise ValueError("composed effects cannot enable caps and smallCaps together")
    for key, replacement in replacements.items():
        edits = sorted(replacement["edits"])
        if any(b[0] < a[1] for a, b in zip(edits, edits[1:])):
            raise ValueError("approved source replacement spans overlap")
        fmt = formats.get(key, {})
        if fmt.get("run_tracking", (False,))[0]:
            raise ValueError("tracked formatting and text edits on the same paragraph need independent snapshot provenance")
    for key in deletions:
        if key in formats or key in replacements or key in insertions:
            raise ValueError("a deleted paragraph is also targeted by another approved effect")
    return formats, replacements, insertions, deletions, pages


def _empty_cleanup(element):
    if element is not None and not len(element) and not element.attrib and not (element.text or "").strip():
        parent = element.getparent()
        if parent is not None:
            parent.remove(element)


def _property_shape(container, names, where, errors):
    if container is None:
        return
    for name in names:
        children = container.findall(W + name)
        if len(children) > 1:
            errors.append(f"{where}: duplicate w:{name} properties")
        for child in children:
            if len(child) or (child.text or "").strip():
                errors.append(f"{where}: malformed w:{name} property")
            if name not in ("rFonts", "sz") and (set(child.attrib) - {W + "val"}
                    or child.get(W + "val", "1") not in {"0", "1", "true", "false", "on", "off"}):
                errors.append(f"{where}: invalid boolean property attributes")
            if name == "sz" and (set(child.attrib) != {W + "val"}
                    or not (child.get(W + "val") or "").isdigit()):
                errors.append(f"{where}: invalid font size attributes")


def _symbol(run, rpr, paragraph, styles):
    return symbolic(rpr, paragraph, styles, PRESERVE_FONTS) or (
        run is not None and run.find(W + "sym") is not None)


def _mask_run(parent, paragraph, styles, fmt, run=None):
    rpr = parent.find(W + "rPr") if parent is not None else None
    if rpr is None:
        return
    names = set(_v_run_elements(fmt["run"]))
    if not _symbol(run, rpr, paragraph, styles):
        if "size" in fmt["font"]:
            names.add("sz")
        if "name" in fmt["font"]:
            rf = rpr.find(W + "rFonts")
            if rf is not None:
                for attr in FONT_ATTRS:
                    rf.attrib.pop(attr, None)
                _empty_cleanup(rf)
    for child in list(rpr):
        if child.tag in {W + name for name in names}:
            rpr.remove(child)
    _empty_cleanup(rpr)


def _check_font(rpr, paragraph, styles, theme, fmt, where, errors):
    if "name" in fmt:
        values = effective_fonts(rpr, paragraph, styles, theme)
        if any(values[slot] != fmt["name"] for slot in ("ascii", "hAnsi", "eastAsia", "cs")):
            errors.append(f"{where}: effective font {values} does not satisfy {fmt['name']!r} in all four slots")
    if "size" in fmt and effective_size(rpr, paragraph, styles) != int(fmt["size"] * 2):
        errors.append(f"{where}: effective size does not satisfy {fmt['size']} points")


def _normalize_format(source_p, output_p, fmt, source_styles, output_styles, output_theme,
                      used_ids, where, errors):
    """Check the final semantics before masking only the approved property families."""
    asked_run = _v_run_elements(fmt["run"])
    requested = set(asked_run) | ({"rFonts"} if "name" in fmt["font"] else set()) | (
        {"sz"} if "size" in fmt["font"] else set())
    conditional = requested | ({"caps", "smallCaps"} if asked_run.get("caps") or asked_run.get("smallCaps") else set())
    if _conditional(output_styles, output_p, conditional, fmt["paragraph"]):
        errors.append(f"{where}: conditional table-style properties cannot be independently resolved")
    source_runs, output_runs = _own_runs(source_p), _own_runs(output_p)
    run_tracking, run_author = fmt.get("run_tracking", (False, None))
    para_tracking, para_author = fmt.get("paragraph_tracking", (False, None))
    if any(len(p.findall(W + "pPr")) > 1 for p in (source_p, output_p)):
        errors.append(f"{where}: duplicate paragraph property containers")
    source_ppr, output_ppr = source_p.find(W + "pPr"), output_p.find(W + "pPr")
    if requested:
        for ri, run in enumerate(output_runs + [None]):
            parent = run if run is not None else output_ppr
            rpr = parent.find(W + "rPr") if parent is not None else None
            label = f"{where} run {ri}" if run is not None else f"{where} paragraph mark"
            if parent is not None and len(parent.findall(W + "rPr")) > 1:
                errors.append(f"{label}: duplicate run property containers")
            _check_effective_run(rpr, output_p, output_styles, asked_run, False, label, errors)
            font = {} if _symbol(run, rpr, output_p, output_styles) else fmt["font"]
            _check_font(rpr, output_p, output_styles, output_theme, font, label, errors)
            _property_shape(rpr, set(asked_run) | ({"rFonts"} if "name" in font else set())
                            | ({"sz"} if "size" in font else set()), label, errors)
        if run_tracking:
            if len(source_runs) != len(output_runs):
                errors.append(f"{where}: tracked formatting changed the run structure")
            for ri, (before_parent, after_parent) in enumerate(zip(source_runs + [source_ppr], output_runs + [output_ppr])):
                before = before_parent.find(W + "rPr") if before_parent is not None else None
                after = after_parent.find(W + "rPr") if after_parent is not None else None
                if sig(before) != sig(after):
                    label = f"{where} tracked run/mark {ri}"
                    if after is None:
                        errors.append(f"{label}: tracked properties disappeared")
                    else:
                        _check_rpr_change(before, after, True, run_author, label, errors)
                        _new_marker(before, after, "rPrChange", True, label, errors, used_ids)
                        for change in after.findall(W + "rPrChange"):
                            after.remove(change)
    _check_effective_para(output_p, output_styles, fmt["paragraph"], where, errors)
    _property_shape(output_ppr, fmt["paragraph"], where, errors)
    if fmt["paragraph"] and para_tracking:
        # rPr is governed separately; pPrChange snapshots intentionally omit it.
        before = [sig(c) for c in source_ppr if c.tag != W + "rPr"] if source_ppr is not None else []
        after = [sig(c) for c in output_ppr if c.tag != W + "rPr"] if output_ppr is not None else []
        if before != after:
            if output_ppr is None:
                errors.append(f"{where}: tracked paragraph properties disappeared")
            else:
                _check_ppr_change(source_ppr, output_ppr, True, para_author, where, errors)
                _new_marker(source_ppr, output_ppr, "pPrChange", True, where, errors, used_ids)
                for change in output_ppr.findall(W + "pPrChange"):
                    output_ppr.remove(change)
    for paragraph, runs, styles, ppr in ((source_p, source_runs, source_styles, source_ppr),
                                         (output_p, output_runs, output_styles, output_ppr)):
        if requested:
            for run in runs:
                _mask_run(run, paragraph, styles, fmt, run)
            _mask_run(ppr, paragraph, styles, fmt)
        if ppr is not None:
            for child in list(ppr):
                if child.tag in {W + key for key in fmt["paragraph"]}:
                    ppr.remove(child)
            _empty_cleanup(ppr)


def _page_permissions(source_main, effects, source=None, styles=None):
    sections = sect_prs(source_main)
    permissions = {}
    for effect in effects.values():
        from .props import _v_resolve_sections
        selected = (_v_resolve_sections(source, effect.get("sections", "all"), styles) if source is not None else
                    list(range(len(sections))) if effect.get("sections", "all") == "all" else effect["sections"])
        if not selected or any(index >= len(sections) for index in selected):
            raise ValueError("page setup selects a missing source section")
        for index in selected:
            spec = permissions.setdefault(index, {})
            if "different_first_page" in effect:
                _merge_values(spec.setdefault("titlePg", {}), {"enabled": effect["different_first_page"]}, f"section {index} titlePg")
            if effect.get("margins"):
                _merge_values(spec.setdefault("pgMar", {}), effect["margins"], f"section {index} margins")
            if effect.get("size") or effect.get("orientation"):
                source_size = sections[index].find(W + "pgSz")
                size = effect.get("size", {})
                width = size.get("w", int(source_size.get(W + "w")) if source_size is not None else None)
                height = size.get("h", int(source_size.get(W + "h")) if source_size is not None else None)
                if width is None or height is None:
                    raise ValueError("page orientation requires known source dimensions or explicit size")
                attrs = {}
                orientation = effect.get("orientation")
                if orientation:
                    if (orientation == "landscape") != (width > height):
                        width, height = height, width
                    attrs["orient"] = orientation
                attrs.update(w=width, h=height)
                _merge_values(spec.setdefault("pgSz", {}), attrs, f"section {index} size")
    return permissions


def _normalize_pages(source_root, output_root, permissions, errors):
    source_sections, output_sections = sect_prs(source_root), sect_prs(output_root)
    if len(source_sections) != len(output_sections):
        errors.append("page-setup effect changed the number of sections")
        return
    for index, elements in permissions.items():
        for name, attrs in elements.items():
            source_element = source_sections[index].find(W + name)
            output_element = output_sections[index].find(W + name)
            if name == "titlePg":
                duplicates = len(output_sections[index].findall(W + name))
                enabled = output_element is not None and output_element.get(W + "val", "true") not in {"0", "false", "off"}
                if duplicates > 1 or len(source_sections[index].findall(W + name)) > 1 or enabled != attrs["enabled"]:
                    errors.append(f"section {index}: different_first_page is not {attrs['enabled']}")
                if output_element is not None and (set(output_element.attrib) - {W + "val"} or len(output_element)
                        or (output_element.text or "").strip()
                        or output_element.get(W + "val", "true") not in {"1", "true", "on", "0", "false", "off"}):
                    errors.append(f"section {index}: unsupported titlePg contents")
                for parent in (source_sections[index], output_sections[index]):
                    for element in list(parent.findall(W + name)):
                        parent.remove(element)
                continue
            if output_element is None or len(output_sections[index].findall(W + name)) != 1:
                errors.append(f"section {index}: missing or duplicate {name}")
                continue
            for key, want in attrs.items():
                value = output_element.get(W + key, "portrait" if key == "orient" else None)
                if value != str(want):
                    errors.append(f"section {index} {name}.{key}: {value!r}, expected {want!r}")
                output_element.attrib.pop(W + key, None)
                if source_element is not None:
                    source_element.attrib.pop(W + key, None)
            _empty_cleanup(source_element)
            _empty_cleanup(output_element)


def _new_revision(element, author, used_ids, where, errors):
    if element is None:
        errors.append(f"{where}: required tracked revision is missing")
        return
    value = element.get(W + "id", "")
    if (element.get(W + "author") != author or not element.get(W + "date")
            or not value.isascii() or not value.isdigit() or int(value) in used_ids):
        errors.append(f"{where}: revision needs the approved author, timestamp and a globally fresh numeric id")
    else:
        used_ids.add(int(value))
    if set(element.attrib) - {W + "id", W + "author", W + "date",
            "{http://schemas.microsoft.com/office/word/2023/wordml/word16du}dateUtc"}:
        errors.append(f"{where}: new revision carries unapproved attributes")


def _check_inserted(anchor, paragraph, effect, fmt, source_styles, output_styles, output_theme,
                    used_ids, where, errors):
    if any(p.tag == W + "p" for p in anchor.iterancestors()) or any(p is not anchor for p in anchor.iter(W + "p")):
        errors.append(f"{where}: insertion around nested paragraph structures is unsupported")
        return
    if effect.get("position", "after") == "after" and anchor.find(W + "pPr/" + W + "sectPr") is not None:
        errors.append(f"{where}: insertion after a section-break paragraph is unsupported")
    if any(ord(c) < 32 or ord(c) == 127 for c in effect["text"]):
        errors.append(f"{where}: inserted wording contains control characters")
    expected = etree.Element(W + "p")
    inherit = effect.get("formatting", "inherit") == "inherit"
    ppr = _v_inherit_ppr(anchor) if inherit else None
    rpr = _v_inherit_rpr(anchor, _own_runs(anchor)) if inherit else None
    style_element = anchor.find(W + "pPr/" + W + "pStyle")
    style = effect.get("style")
    if style in (None, "inherit"):
        style = style_element.get(W + "val") if style_element is not None else None
    if style is not None:
        if ppr is None:
            ppr = etree.Element(W + "pPr")
        old = ppr.find(W + "pStyle")
        if old is None:
            old = etree.Element(W + "pStyle")
            ppr.insert(0, old)
        old.set(W + "val", style)
    if ppr is not None:
        expected.append(ppr)
    run = etree.SubElement(expected, W + "r")
    if rpr is not None:
        run.append(rpr)
    text = etree.SubElement(run, W + "t")
    text.text = effect["text"]
    if effect["text"] != effect["text"].strip():
        text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    actual = copy.deepcopy(paragraph)
    if effect["tracked"]:
        mark = actual.find(W + "pPr/" + W + "rPr/" + W + "ins")
        wrapper = actual.find(W + "ins")
        _new_revision(mark, effect["author"], used_ids, where + " paragraph mark", errors)
        _new_revision(wrapper, effect["author"], used_ids, where + " insertion", errors)
        if mark is not None:
            if len(mark) or (mark.text or "").strip():
                errors.append(f"{where}: inserted paragraph mark has unexpected content")
            parent = mark.getparent()
            parent.remove(mark)
            _empty_cleanup(parent)
            _empty_cleanup(actual.find(W + "pPr"))
        if wrapper is not None:
            position = actual.index(wrapper)
            for child in list(wrapper):
                actual.insert(position, child)
                position += 1
            actual.remove(wrapper)
    if fmt and inherit:
        inherited_fmt = {**fmt, "run_tracking": (False, None), "paragraph_tracking": (False, None)}
        _normalize_format(expected, actual, inherited_fmt, source_styles, output_styles,
                          output_theme, used_ids, where + " inherited formatting", errors)
    # xml:space="preserve" is equivalent for wording with no edge whitespace.
    if effect["text"] == effect["text"].strip():
        for node in actual.iter(W + "t"):
            if node.get("{http://www.w3.org/XML/1998/namespace}space") == "preserve":
                node.attrib.pop("{http://www.w3.org/XML/1998/namespace}space")
    difference = first_difference(expected, actual)
    if difference:
        errors.append(f"{where}: inserted paragraph differs from approved wording/source inheritance: {difference}")


def _structural_pairs(source_roots, output_roots, formats, insertions, deletions,
                      source_styles, output_styles, output_theme, used_ids, errors):
    """Bind source paragraphs to final positions using only approved structural effects."""
    pairs, removed, inserted = {}, [], defaultdict(lambda: {"before": [], "after": []})
    old_ids = {str(value) for value in used_ids}
    for part, source_root in source_roots.items():
        if part not in output_roots:
            continue
        source_paragraphs = list(source_root.iter(W + "p"))
        output_paragraphs = list(output_roots[part].iter(W + "p"))
        sequence = []
        for index, paragraph in enumerate(source_paragraphs):
            key = part, index
            for effect in insertions.get(key, []):
                if effect.get("position", "after") == "before":
                    sequence.append(("insert", key, effect))
            deletion = deletions.get(key)
            if deletion is None or deletion["tracked"]:
                sequence.append(("original", key, deletion))
            else:
                why = v_deletable(paragraph, False)
                if why:
                    errors.append(f"{part} paragraph {index}: deletion unsupported because {why}")
                removed.append(paragraph)
            for effect in insertions.get(key, []):
                if effect.get("position", "after") == "after":
                    sequence.append(("insert", key, effect))
        if len(sequence) != len(output_paragraphs):
            errors.append(f"{part}: final paragraph count {len(output_paragraphs)}, independently expected {len(sequence)}")
            continue
        for token, output_paragraph in zip(sequence, output_paragraphs):
            kind, key, effect = token
            source_paragraph = source_paragraphs[key[1]]
            where = f"{part} original paragraph {key[1]}"
            if kind == "insert":
                _check_inserted(source_paragraph, output_paragraph, effect, formats.get(key),
                                source_styles, output_styles, output_theme, used_ids, where, errors)
                inserted[key][effect.get("position", "after")].append(output_paragraph)
                removed.append(output_paragraph)
            elif effect is not None:
                check_deleted_paragraph(source_paragraph, output_paragraph, effect["author"], old_ids, where, errors)
                _check_edit_envelope(source_paragraph, output_paragraph, old_ids, where, errors, deleting=True)
                # The item check proves the entire paragraph's tracked deletion;
                # replace it for the remaining whole-tree identity comparison.
                output_paragraph.getparent().replace(output_paragraph, copy.deepcopy(source_paragraph))
            else:
                pairs[key] = source_paragraph, output_paragraph
    for key, positions in inserted.items():
        if key not in pairs:
            errors.append(f"{key}: inserted paragraph has no retained source anchor")
            continue
        anchor = pairs[key][1]
        siblings = list(anchor.getparent())
        index = siblings.index(anchor)
        before, after = positions["before"], positions["after"]
        if (siblings[max(0, index - len(before)):index] != before
                or siblings[index + 1:index + 1 + len(after)] != after):
            errors.append(f"{key}: inserted paragraphs must be immediate siblings of their approved source anchor")
    return pairs, removed


def _revision_integrity(source, output, errors):
    """Original revisions may survive; newly reused ids are never accepted."""
    def counts(pkg):
        result = Counter()
        for part in pkg.text_parts():
            for element in pkg.xml(part).iter():
                if element.tag in V_REVISION_TAGS and element.get(W + "id") is not None:
                    value = element.get(W + "id")
                    normalized = str(int(value)) if value.isascii() and value.isdigit() else value
                    result[normalized] += 1
        return result
    before, after = counts(source), counts(output)
    for revision_id, count in after.items():
        if revision_id not in before and (not revision_id.isascii() or not revision_id.isdigit()):
            errors.append(f"new revision needs a non-negative numeric id: {revision_id!r}")
        if count > max(1, before[revision_id]):
            errors.append(f"new revision-id duplication: {revision_id} occurs {count} times (source {before[revision_id]})")


def _check_edit_envelope(before, after, old_ids, where, errors, deleting=False):
    """Close metadata not represented by the mature character-item comparison."""
    space = "{http://www.w3.org/XML/1998/namespace}space"
    original_edge_cases = Counter()
    for element in before.iter(W + "t", W + "delText"):
        if (element.text or "") != (element.text or "").strip() and element.get(space) != "preserve":
            original_edge_cases[sig(element)] += 1
    for root in (before, after):
        if len(root.findall(W + "pPr")) > 1 or any(len(run.findall(W + "rPr")) > 1 for run in _own_runs(root)):
            errors.append(f"{where}: duplicate paragraph or run property containers")
        for element in root.iter(W + "t", W + "delText"):
            if len(element) or set(element.attrib) - {space} or element.get(space, "default") not in {"default", "preserve"}:
                errors.append(f"{where}: edited text has unsupported character metadata")
    for element in after.iter(W + "t", W + "delText"):
        if (element.text or "") != (element.text or "").strip() and element.get(space) != "preserve":
            comparison = copy.deepcopy(element)
            if deleting and comparison.tag == W + "delText":
                comparison.tag = W + "t"
            signature = sig(comparison)
            if original_edge_cases[signature]:
                original_edge_cases[signature] -= 1
            else:
                errors.append(f"{where}: split or inserted text lost xml:space preservation")
    source_wrappers = list(before.iter(W + "ins", W + "del", W + "moveFrom", W + "moveTo"))
    source_items = v_items(before)
    actual_items = v_items(after)
    observed = {container for item in actual_items for container in item.chain_els}
    source_runs_with_items = {item.run for item in source_items if item.run is not None}
    output_runs_with_items = {item.run for item in actual_items if item.run is not None}
    expected_empty_runs = []
    for run in _own_runs(before):
        if run not in source_runs_with_items:
            expected = copy.deepcopy(run)
            if deleting:
                for element in expected.iter(W + "t"):
                    element.tag = W + "delText"
            expected_empty_runs.append(sig(expected))
    actual_empty_runs = [sig(run) for run in _own_runs(after) if run not in output_runs_with_items]
    if expected_empty_runs != actual_empty_runs:
        errors.append(f"{where}: empty source runs were changed, added, removed or reordered")

    def containers(root, output=False):
        result = []
        for container in root.iter():
            if container.tag not in V_CONTAINERS:
                continue
            if container.getparent() is not None and container.getparent().tag == W + "rPr":
                continue
            if output and container.tag in V_REVISION_TAGS and container.get(W + "id") not in old_ids:
                continue
            result.append((container.tag, tuple(sorted(container.attrib.items()))))
        return result
    if containers(before) != containers(after, output=True):
        errors.append(f"{where}: original content containers were changed, added, removed or reordered")
    standard = {W + "id", W + "author", W + "date",
                "{http://schemas.microsoft.com/office/word/2023/wordml/word16du}dateUtc"}
    old_numeric = {int(value) for value in old_ids if value.isascii() and value.isdigit()}
    marks = after.findall(W + "pPr/" + W + "rPr/" + W + "del")
    if deleting and len(marks) != 1:
        errors.append(f"{where}: tracked paragraph deletion requires exactly one deleted paragraph mark")
    for wrapper in after.iter(W + "ins", W + "del", W + "moveFrom", W + "moveTo"):
        if wrapper.get(W + "id") in old_ids:
            continue  # Original wrapper metadata is compared by the item model.
        value = wrapper.get(W + "id", "")
        if value.isascii() and value.isdigit() and int(value) in old_numeric:
            errors.append(f"{where}: new revision reuses a source id with different numeric spelling")
        mark = deleting and wrapper in marks and not len(wrapper) and not (wrapper.text or "").strip()
        empty_deletion = (deleting and wrapper.tag == W + "del" and len(wrapper) > 0
                          and all(child.tag == W + "r" and child not in output_runs_with_items for child in wrapper))
        if wrapper not in observed and not mark and not empty_deletion:
            errors.append(f"{where}: new revision wrapper has no approved content")
        if not wrapper.get(W + "date") or not wrapper.get(W + "author"):
            errors.append(f"{where}: new revision requires its author and timestamp")
        if set(wrapper.attrib) - standard:
            # A split pending insertion retains the source metadata exactly.
            attributes = {key: value for key, value in wrapper.attrib.items() if key != W + "id"}
            split = wrapper.tag == W + "ins" and any(old.tag == wrapper.tag and attributes == {
                key: value for key, value in old.attrib.items() if key != W + "id"} for old in source_wrappers)
            if not split:
                errors.append(f"{where}: new revision has unapproved attributes")


def _apply_section_masks(source_main, output_main, masks):
    for root in (source_main, output_main):
        sections = sect_prs(root)
        for index, elements in masks.items():
            if int(index) >= len(sections):
                raise ValueError("validated template mask names a missing section")
            for name, attrs in elements.items():
                for element in list(sections[int(index)].findall(W + name)):
                    if attrs is None:
                        sections[int(index)].remove(element)
                    else:
                        for attr in attrs:
                            element.attrib.pop(W + attr, None)
                        _empty_cleanup(element)


def verify_effects(original: bytes | Path, candidate: bytes | Path, contract: dict, binding: dict) -> dict:
    """Verify all approved effects and every original-to-final preservation boundary."""
    checks, errors, opened = [], [], []
    phase = "E1"

    def record(label, start, success):
        checks.append({"id": label, "status": "FAIL" if len(errors) > start else "PASS",
                       "summary": "; ".join(errors[start:]) if len(errors) > start else success})

    try:
        _validate(contract)
        record(phase, 0, "approved effects use the independently supported closed grammar")
        phase = "E2"
        source_data = original if isinstance(original, bytes) else Path(original).read_bytes()
        output_data = candidate if isinstance(candidate, bytes) else Path(candidate).read_bytes()
        source, output = VPackage(source_data), VPackage(output_data)
        opened.extend((source, output))
        if source.main_part() != output.main_part():
            raise ValueError("the main document relationship changed")
        if len(source.names) != len(set(source.names)) or len(output.names) != len(set(output.names)):
            raise ValueError("duplicate package-member names are unsupported")
        source_roots = {part: source.xml(part) for part in source.text_parts()}
        output_roots = {part: output.xml(part) for part in output.text_parts()}
        main = source.main_part()
        if source_roots[main].tag != W + "document" or output_roots[main].tag != W + "document":
            raise ValueError("effects require Transitional WordprocessingML documents")
        source_style_part, output_style_part = source.related(STYLES_REL), output.related(STYLES_REL)
        if source_style_part != output_style_part:
            raise ValueError("the styles relationship changed")
        source_styles = Styles(source.xml(source_style_part) if source_style_part else None)
        output_styles = Styles(output.xml(output_style_part) if output_style_part else None)
        theme_part = output.related(THEME_REL)
        output_theme = Theme(output.xml(theme_part) if theme_part else None)
        targets = _bound_targets(source, source_roots, source_styles, contract, binding,
                                 hashlib.sha256(source_data).hexdigest())
        programs = _programs(source_roots, contract, targets)
        formats, replacements, insertions, deletions, pages = programs
        page_permissions = _page_permissions(source_roots[main], pages, source, source_styles)
        record(phase, len(errors), "every approved effect is independently bound to the original source")
        phase = "E3"
        start = len(errors)
        allowed_added, allowed_removed, allowed_changed, masks, added_styles = set(), set(), set(), [], set()
        package_effects = [effect for effect in contract["effects"]
                           if effect["type"] in {"header_footer_template", "remove_header_footer"}]
        if len(package_effects) > 1:
            raise ValueError("multiple header/footer graph effects require one combined approved graph contract")
        for effect in package_effects:
            if effect["type"] == "header_footer_template":
                from .goal_templates import verify_template_effect
                result = verify_template_effect(source, output, effect)
            else:
                from .goal_headers import verify_header_removal
                result = verify_header_removal(source, output, effect)
            errors.extend(result["errors"])
            allowed_added.update(result.get("allowed_added", []))
            allowed_removed.update(result.get("allowed_removed", []))
            allowed_changed.update(result.get("allowed_changed", []))
            masks.append(result.get("section_masks", {}))
            added_styles.update(result.get("style_ids", []))
        for effect in contract["effects"]:
            for target in targets[effect["id"]]:
                if target["part"] in allowed_removed:
                    raise ValueError("an effect targets a header/footer replaced or removed by another approved effect")
        operation_effects = [effect for effect in contract["effects"] if effect["type"] in OPERATION_EFFECTS]
        operation_context = None
        if operation_effects:
            operation_context = verify_operations(source, output, operation_effects,
                                                  contract.get("author") or default_author(), errors, binding)
            allowed_changed.update(operation_context.verified_members)
            if operation_effects[0]["type"] == "review_comments":
                # Only the standalone comment proof can account for added text
                # parts. It checked their entire expected trees against the
                # original source and approved literal wording above.
                allowed_added.update(operation_context.review_comment_additions)
                for part in operation_context.verified_parts:
                    if part in source_roots:
                        output_roots[part] = copy.deepcopy(source_roots[part])
                    else:
                        output_roots.pop(part, None)
            if operation_context.styles_part:
                allowed_changed.add(operation_context.styles_part)
        used_ids = _revision_ids(source)
        if operation_context is not None:
            from .clearing import v_cleared_sets, v_decide
            from .typednumbers import _v_cleared_copy
            envelope_ids = set().union(*(part_revision_ids(root) for root in source_roots.values()))
            for assertion in operation_context.plan["property_assertions"]:
                if assertion["kind"] != "typed_numbers":
                    continue
                part = assertion["part"]
                sv, ov = operation_context.view(part)
                clear = assertion["params"].get("direct_formatting", "keep") == "clear"
                rn, pn = v_cleared_sets() if clear else (set(), set())
                for index, *_ in assertion["converted"]:
                    before = sv.pars[index]
                    if clear:
                        clearing = v_decide(before, sv.runs[index], source_styles, rn, pn)
                        if clearing.runs:
                            before = _v_cleared_copy(before, sv.runs[index], clearing.runs)
                    _check_edit_envelope(before, ov.pars[index], envelope_ids,
                                         f"{part} original paragraph {index}", errors)
        pairs, removed_nodes = _structural_pairs(source_roots, output_roots, formats, insertions, deletions,
                                                 source_styles, output_styles, output_theme, used_ids, errors)
        record(phase, start, "approved paragraph and package-graph effects independently checked")
        phase = "E4"
        start = len(errors)
        for key, fmt in formats.items():
            if key not in pairs:
                errors.append(f"{key}: bound formatting target has no final counterpart")
                continue
            _normalize_format(*pairs[key], fmt, source_styles, output_styles, output_theme,
                              used_ids, f"{key[0]} original paragraph {key[1]}", errors)
        if operation_context is not None and operation_effects[0]["type"] != "review_comments":
            normalize_operations(operation_context, source_roots, output_roots)
        _normalize_pages(source_roots[main], output_roots[main], page_permissions, errors)
        for mask in masks:
            _apply_section_masks(source_roots[main], output_roots[main], mask)
        record(phase, start, "all requested effective formatting and page properties hold in the final document")
        phase = "E5"
        start = len(errors)
        fields = {part: _v_field_runs(root) for part, root in source_roots.items()}
        old_ids = set().union(*(part_revision_ids(root) for root in source_roots.values()))
        for key, replacement in replacements.items():
            if key not in pairs:
                errors.append(f"{key}: bound wording target has no final counterpart")
                continue
            before, after = pairs[key]
            if any(p.tag == W + "p" for p in before.iterancestors()):
                errors.append(f"{key}: text replacement inside a textbox is unsupported")
                continue
            check_edited_paragraph(before, after, replacement["edits"], replacement["tracked"], replacement["author"],
                                   fields[key[0]], old_ids, f"{key[0]} original paragraph {key[1]}", errors)
            _check_edit_envelope(before, after, old_ids, f"{key[0]} original paragraph {key[1]}", errors)
            # Restore the independently proven paragraph rather than dropping
            # both trees' copies: dropping them would conceal relocation into a
            # different table cell or container with the same global p order.
            if after.getparent() is not None:
                after.getparent().replace(after, copy.deepcopy(before))
        _revision_integrity(source, output, errors)
        record(phase, start, "approved literal edits preserve every other content item and revision context")
        phase = "E6"
        start = len(errors)
        for node in removed_nodes:
            if node.getparent() is not None:
                node.getparent().remove(node)
        source_names, output_names = set(source.names), set(output.names)
        if output_names - source_names != allowed_added or source_names - output_names != allowed_removed:
            errors.append("package additions/removals differ from the independently approved member graph")
        if [name for name in source.names if name in output_names] != [name for name in output.names if name in source_names]:
            errors.append("existing package member order changed")
        for part in source_names & output_names:
            if part in source_roots and part in output_roots:
                difference = first_difference(source_roots[part], output_roots[part])
                if difference:
                    errors.append(f"{part}: unapproved residual content/structure change: {difference}")
            elif part == source_style_part and added_styles:
                after = output.xml(part)
                for style in list(after.findall(W + "style")):
                    if style.get(W + "styleId") in added_styles:
                        after.remove(style)
                difference = first_difference(source.xml(part), after)
                if difference:
                    errors.append("original style definitions changed: " + difference)
            elif part not in allowed_changed and source.read(part) != output.read(part):
                errors.append(f"unapproved package-member change: {part}")
        record(phase, start, "all content, structures and package data outside approved effects are preserved")
    except Exception as exc:
        errors.append(f"{phase}: {type(exc).__name__}: {exc}")
        record(phase, len(errors) - 1, "")
    finally:
        for package in opened:
            package.zip.close()
    return {"passed": not errors, "checks": checks, "errors": errors}
