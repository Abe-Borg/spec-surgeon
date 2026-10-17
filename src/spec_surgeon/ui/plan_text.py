"""Plain-language plan review, shared by desktop display and its independent tests (no Qt)."""
from __future__ import annotations

import json


def _json(value):
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def _inches(twips):
    return f"{twips / 1440:g} " + ("inch" if twips == 1440 else "inches")


def _formatting(effect):
    lines = []
    font = effect.get("font") or {}
    if "name" in font:
        lines.append("Font: " + str(font["name"]))
    if "size" in font:
        lines.append("Font size: " + str(font["size"]) + " pt")
    labels = {"b": "Bold", "i": "Italic", "caps": "All capitals", "smallCaps": "Small capitals",
              "strike": "Strikethrough", "keepNext": "Keep with next paragraph",
              "keepLines": "Keep paragraph lines together", "widowControl": "Widow/orphan control",
              "pageBreakBefore": "Page break before paragraph"}
    for group in ("run", "paragraph"):
        for name, value in (effect.get(group) or {}).items():
            lines.append(labels.get(name, name) + ": " + ("on" if value is True else "off" if value is False else str(value)))
    return lines


def _target(target):
    names = {"document": "document body", "headers": "headers", "footers": "footers",
             "footnotes": "footnotes", "endnotes": "endnotes", "comments": "comments"}
    scopes = ", ".join(names.get(name, name) for name in target.get("scope", ["document"]))
    if target.get("all"):
        return ["Where it applies: all paragraphs in " + scopes + "."]
    count = target.get("cardinality", 1)
    lines = ["Where it applies: " + scopes + ".",
             "Target count: " + ("every matching paragraph" if count == "all" else str(count) + " paragraph(s)")]
    for locator in target.get("locators") or []:
        rules = []
        for key, label in (("exact_text", "matches text"), ("contains_text", "contains text"),
                           ("text_regex", "matches regular expression"), ("style_id", "uses style"),
                           ("after_text", "after text"), ("before_text", "before text"),
                           ("section_number", "section number"), ("style_hint", "style hint")):
            if key in locator:
                rules.append(label + " " + _json(locator[key]))
        if locator.get("role") == "section_title":
            rules.insert(0, "section title identified from this document's SECTION marker")
        if "in_table" in locator:
            rules.append("inside a table" if locator["in_table"] else "outside tables")
        lines.append("  • " + "; ".join(rules))
    if len(target.get("locators") or []) > 1:
        lines.append("A paragraph matching any one of these alternatives is in scope.")
    return lines


def _built_in_effect(effect):
    """Keep scope, cardinality and chosen inputs readable before approving a built-in outcome."""
    kind = effect["type"]
    params = effect.get("params") or {}
    count = effect.get("expected_count", "not specified")
    units = {"paragraph_style": "paragraphs", "style_mapping": "style usages", "proofing_language": "documents",
             "table_format": "tables", "table_style": "tables", "numbering": "paragraphs",
             "typed_numbering": "paragraphs"}
    lines = ["Required scope: " + ("all matching " if count == "all" else str(count) + " ") + units[kind],
             "Selection rules: " + _json(effect.get("select") or {})]
    if effect.get("template"):
        lines += ["Template file: " + str(effect["template"]),
                  "Pinned template SHA-256: " + str(effect.get("template_sha256", ""))]
    if kind == "style_mapping":
        for mapping in params.get("map") or []:
            lines.append("Map style " + _json(mapping.get("from")) + " to " + _json(mapping.get("to")))
    elif kind in {"paragraph_style", "table_style"}:
        lines.append("Apply style: " + _json(params.get("style", "not specified")))
    elif kind == "proofing_language":
        for key, label in (("language", "Proofing language"), ("east_asian", "East Asian language"),
                           ("complex_script", "Complex script language")):
            if key in params:
                lines.append(label + ": " + str(params[key]))
    elif kind in {"numbering", "typed_numbering"}:
        lines.append("Numbering level styles: " + _json(params.get("levels", [])))
        if kind == "typed_numbering":
            lines.append("Remove typed number prefixes only when the automatic numbering preserves the visible numbers.")
    lines.append("Requested settings: " + _json(params))
    return lines


def plan_text(plan, documents):
    """Show the complete proposed effect contract; no model-written summary can hide an effect."""
    if not plan:
        return "No proposed plan yet. Describe the desired outcome and choose Investigate and propose a plan."
    lines = ["PROPOSED DOCUMENT CHANGES", "", "Status: " + str(plan.get("status", "Unknown")),
             "Request: " + str(plan.get("request", "")), "", "Documents in this plan:"]
    for doc_id in plan.get("document_ids") or []:
        document = documents.get(doc_id) or {}
        lines.append("  • " + str(document.get("source", doc_id)))
    lines += ["", "Why this approach:", _text(plan.get("rationale")), "", "Risks and preservation concerns:",
              _text(plan.get("risks")), "", "Evidence used:", _text(plan.get("evidence"))]
    if plan.get("questions"):
        lines += ["", "Questions to resolve before approval:", _text(plan["questions"])]
    contract = plan.get("contract") or {}
    lines += ["", "Every proposed effect:"]
    if contract.get("kind") == "format_section_title":
        lines += ["Format the exact section title " + _json(contract.get("target", {}).get("title", "")),
                  "Target restrictions: " + _json(contract.get("target", {}))]
        lines += _formatting(contract)
    for index, effect in enumerate(contract.get("effects") or [], 1):
        kind = effect.get("type", "unknown")
        lines += ["", f"{index}. {kind.replace('_', ' ').capitalize()}"]
        if kind == "replace_text":
            lines += ["Exact text before: " + _json(effect.get("find", "")),
                      "Exact text after:  " + _json(effect.get("replace", "")),
                      "Required matches: " + str(effect.get("expected_matches", "not specified"))]
        elif kind == "insert_paragraph":
            lines += ["Exact inserted wording: " + _json(effect.get("text", "")),
                      "Position: " + str(effect.get("position", "after")) + " the selected paragraph"]
        elif kind == "delete_paragraphs":
            lines.append("Delete the complete paragraphs selected by these target rules.")
        elif kind == "format":
            lines += _formatting(effect)
        elif kind == "page_setup":
            lines.append("Sections: " + str(effect.get("sections", "all")))
            for name, value in (effect.get("margins") or {}).items():
                lines.append(name.capitalize() + " margin: " + _inches(value))
            for name, value in (effect.get("size") or {}).items():
                lines.append("Page " + {"w": "width", "h": "height"}.get(name, name) + ": "
                             + _inches(value))
            if "orientation" in effect:
                lines.append("Orientation: " + str(effect["orientation"]))
            if "different_first_page" in effect:
                lines.append("Different first page: " + ("on" if effect["different_first_page"] else "off"))
        elif kind == "header_footer_template":
            lines += ["Template file: " + str(effect.get("template", "")),
                      "Pinned template SHA-256: " + str(effect.get("template_sha256", "")),
                      "Replace header/footer content using the pinned template and these fill rules."]
            for slot, rule in (effect.get("params", {}).get("slot_fills") or {}).items():
                for wording in _literal_words(rule):
                    lines.append("Exact approved wording for slot " + _json(slot) + ": " + _json(wording))
        elif kind == "remove_header_footer":
            lines.append("Remove the selected header/footer references and their unused parts.")
        elif kind in {"paragraph_style", "style_mapping", "proofing_language", "table_format", "table_style",
                      "numbering", "typed_numbering"}:
            lines += _built_in_effect(effect)
        if "target" in effect:
            lines += _target(effect["target"])
        if kind in {"format", "replace_text", "insert_paragraph", "delete_paragraphs"}:
            tracked = effect.get("tracked", kind != "format")
            lines.append("Tracked change: " + ("yes" if tracked else "no"))
    # This record is deliberately complete: counts, metadata, defaults and future effect fields
    # remain reviewable even when the descriptive presentation above has not learned their labels.
    lines += ["", "Complete effect definition:", _json(contract), "",
              "Approval applies only to these documents, source versions, project context and effects.",
              "Approval does not export documents. Execution must still pass independent verification.",
              "Plan reference: " + str(plan.get("id", ""))]
    return "\n".join(lines)


def _literal_words(rule):
    if isinstance(rule, dict):
        if rule.get("rule") == "literal":
            yield rule.get("value", "")
        for child in rule.get("rules") or []:
            yield from _literal_words(child)


def _text(value):
    if not value:
        return "None recorded."
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return "\n".join("  • " + item for item in value)
    return _json(value)
