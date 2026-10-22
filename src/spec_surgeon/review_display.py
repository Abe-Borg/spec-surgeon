"""Readable views of the actual edit and, for governed recovery, its existing outcome evidence."""
from __future__ import annotations

from pathlib import Path


EFFECT_NAMES = {
    "format": "Change text formatting", "format_section_title": "Format section titles",
    "replace_text": "Replace wording", "insert_paragraph": "Insert a paragraph",
    "delete_paragraphs": "Remove paragraphs", "page_setup": "Change page layout",
    "header_footer_template": "Match headers and footers to the template",
    "remove_header_footer": "Remove headers or footers", "paragraph_style": "Apply paragraph styles",
    "style_mapping": "Match document styles", "proofing_language": "Set proofing language",
    "table_format": "Format tables", "table_style": "Apply a table style",
    "numbering": "Change list numbering", "typed_numbering": "Convert typed numbers to automatic numbering",
    "paragraph_borders": "Change paragraph borders and shading", "review_comments": "Add review comments",
}
LABELS = {
    "b": "Bold", "i": "Italic", "caps": "All capitals", "smallCaps": "Small capitals",
    "strike": "Strikethrough", "dstrike": "Double strikethrough", "vertAlign": "Superscript or subscript",
    "vanish": "Hidden text", "highlight": "Highlight", "keepNext": "Keep with next paragraph", "keepLines": "Keep lines together",
    "widowControl": "Avoid isolated lines", "pageBreakBefore": "Page break before",
    "find": "Original wording", "replace": "Replacement wording", "text": "Exact wording",
    "run": "Text formatting", "paragraph": "Paragraph formatting", "params": "Settings",
    "select": "Where to apply", "target": "Where to apply", "locators": "Matching passages (any of these)",
    "exact_text": "Text is exactly", "contains_text": "Text contains", "text_regex": "Text matching pattern",
    "cardinality": "Passages per document", "expected_count": "Required matches per document",
    "expected_matches": "Required text replacements", "tracked": "Show changes in Word",
    "style_id": "Style", "style_in": "Styles", "index_range": "Paragraph range (zero-based)",
    "section_title": "section title", "document": "document body", "slot_fills": "Template field values",
    "template_sections": "Template sections (zero-based)", "size": "Size", "w": "Width", "h": "Height",
}

EDIT_NAMES = {
    "set_properties": "Change text and paragraph formatting", "set_font": "Change fonts",
    "set_doc_defaults_font": "Change the document's default font", "set_style_font": "Change style fonts",
    "replace_text": "Replace wording", "insert_paragraph": "Insert a paragraph",
    "delete_paragraphs": "Remove paragraphs", "apply_header_footer_template": "Match headers and footers to the template",
    "remove_header_footer": "Remove headers or footers", "set_page_setup": "Change page layout",
    "apply_style": "Apply paragraph styles", "map_styles": "Match document styles",
    "set_output_name": "Name the edited copies",
    # built-in batch 6 (docs/BUILTIN_OPS_BATCH6_DESIGN.md): the window says "picture", never "image"
    "insert_image": "Insert a picture", "remove_images": "Remove pictures", "resize_images": "Resize pictures",
    "delete_tables": "Delete tables", "delete_table_rows": "Delete table rows", "insert_table_rows": "Add table rows",
    "insert_table": "Insert a table", "delete_table_columns": "Delete table columns",
    "set_table_cells": "Format table cells", "merge_table_cells": "Merge table cells",
    "insert_section_break": "Insert section breaks", "remove_section_break": "Remove section breaks",
    "insert_field": "Insert a field", "reply_to_comments": "Reply to comments", "resolve_comments": "Resolve comments",
    "remove_footnotes": "Remove footnotes", "move_paragraphs": "Move paragraphs",
}


def _specs(spec, chain=None):
    from .editspec import chain_specs
    if spec:
        return chain_specs(spec)
    return [stage["spec"] for stage in (chain or {}).get("stages", []) if stage.get("spec")]


def change_sentence(op):
    """What one operation does, in the words its own module writes (``Op.describe``): the plan reads like a
    colleague wrote it, and it cannot drift from the operation because the operation owns the sentence."""
    from .ops import get_op
    name = op.get("op", "")
    try:
        return get_op(name).describe(op.get("select") or {}, op.get("params") or {})
    except Exception:  # noqa: BLE001 — a description must never hide a plan; fall back to the plain name
        return EDIT_NAMES.get(name, readable(name).capitalize()) + "."


def spec_changes(spec, chain=None):
    """Describe the same specification the preview executes, without another model or authority: one sentence per
    operation from the operation itself, and every setting underneath for the reader who wants it all."""
    result = []
    for value in _specs(spec, chain):
        for op in value.get("ops", []):
            name = op["op"]
            title = EDIT_NAMES.get(name, readable(name).capitalize())
            details = fields({key: child for key, child in op.items() if key != "op"})
            result.append({"title": title, "summary": change_sentence(op), "details": details})
    return result


def spec_assumptions(spec, chain=None):
    """The assumptions the plan rests on, shown inline with the changes, never as a change of their own."""
    out = []
    for value in _specs(spec, chain):
        out.extend(str(a) for a in value.get("assumptions") or [])
    return list(dict.fromkeys(out))


def layout_changes(plan, routing=None):
    """A layout plan, one group per layout: its label, how many attached documents it covers, and its changes."""
    groups = []
    for layout in plan.get("layouts", []):
        files = (routing.files.get(layout["id"], []) if routing is not None else [])
        groups.append({"id": layout["id"], "label": layout.get("label") or f"Layout {layout['id']}",
                       "count": len(files), "files": [Path(f).name for f in files],
                       "changes": spec_changes(layout["spec"]), "assumptions": spec_assumptions(layout["spec"])})
    return groups


def spec_bindings(spec, chain=None):
    specs = _specs(spec, chain)
    rows = []
    for value in specs:
        for binding in value.get("bindings", []):
            details = [f"{readable(name)}: {slot['typed']} (your wording for this document)" if "typed" in slot
                       else f"{readable(name)}: {slot['value']} (from {slot['part']}, paragraph {slot['paragraph'] + 1})"
                       for name, slot in binding["slots"].items()]
            rows.append({"title": Path(binding["file"]).name, "source": binding["file"], "details": details})
    return rows


def readable(value):
    return LABELS.get(str(value), str(value).replace("_", " "))


def fields(value, prefix=""):
    """Render every material field, including unfamiliar future fields, without JSON or hidden truncation.

    Exact text values are never paraphrased. Nested lists preserve their grouping, including
    nested identifiers or types that can describe actual document properties.
    """
    lines = []
    if isinstance(value, dict):
        for key, child in value.items():
            title = LABELS.get(key, key.replace("_", " ").capitalize())
            lines.extend(fields(child, f"{prefix} · {title}" if prefix else title))
    elif isinstance(value, list):
        if not value:
            lines.append(f"{prefix}: none")
        for number, child in enumerate(value, 1):
            lines.extend(fields(child, f"{prefix} ({number})"))
    else:
        text = "yes" if value is True else "no" if value is False else "none" if value is None else str(value)
        lines.append(f"{prefix}: {text}")
    return lines


def effects(contract):
    if not contract:
        return []
    values = contract.get("effects", []) if contract.get("kind") == "document_effects" else [contract]
    result = []
    for effect in values:
        kind = effect.get("type", effect.get("kind", ""))
        # Only the effect's own discriminator/binding metadata is redundant with its title.
        lines = fields({key: value for key, value in effect.items()
                        if key not in {"id", "type", "kind", "template_sha256"}})
        target = effect.get("target")
        if target is not None and "scope" not in target:
            lines.append("Where to apply: document body")
        if kind in {"format", "replace_text", "insert_paragraph", "delete_paragraphs"} and "tracked" not in effect:
            lines.append("Show changes in Word: " + ("no" if kind == "format" else "yes"))
        if kind in {"format", "replace_text", "insert_paragraph", "delete_paragraphs"} and effect.get("tracked", kind != "format"):
            if "author" not in effect:
                lines.append("Change author: " + contract.get("author", "Abraham Borg"))
        if kind == "page_setup":
            lines.append("Page dimensions and margins are in twips (1,440 = 1 inch).")
        title = EFFECT_NAMES.get(kind, readable(kind).capitalize())
        summary = title
        if "params" in effect:
            # An effect that is an operation (page setup, headers and footers, tables, numbering, proofing
            # language…): the operation's own sentence, never a field dump.
            try:
                from .goal_operations import OPERATIONS, operation_spec
                if kind in OPERATIONS:
                    op = operation_spec(effect, contract.get("request", ""))["ops"][0]
                else:
                    name = {"page_setup": "set_page_setup", "header_footer_template": "apply_header_footer_template",
                            "remove_header_footer": "remove_header_footer"}[kind]
                    params = dict(effect["params"])
                    if effect.get("template"):
                        params.setdefault("template", effect["template"])
                    op = {"op": name, "select": dict(effect.get("select") or {}), "params": params}
                summary = change_sentence(op)
            except Exception:  # noqa: BLE001 — the field view below still shows every setting
                pass
        if kind == "format":
            changes = []
            for group in ("run", "paragraph"):
                for key, value in effect.get(group, {}).items():
                    changes.append(f"{readable(key).lower()} {'on' if value else 'off'}")
            font = effect.get("font", {})
            if font.get("name"):
                changes.append("font " + font["name"])
            if "size" in font:
                changes.append(f"{font['size']} pt")
            passages = [str(loc["exact_text"]) for loc in (target or {}).get("locators", []) if "exact_text" in loc]
            where = ", ".join(f'“{text}”' for text in passages) or "the selected passages"
            summary = f"Set {', '.join(changes)} for {where}."
        elif kind == "replace_text":
            summary = f'Replace “{effect.get("find", "")}” with “{effect.get("replace", "") }”.'
        elif kind == "insert_paragraph":
            summary = f'Insert {effect.get("position", "after")} the selected passage:\n{effect.get("text", "")}'
        elif kind == "header_footer_template":
            summary += " · " + Path(effect.get("template", "")).name
        elif kind == "proofing_language":
            summary += " · " + ", ".join(str(v) for k, v in effect.get("params", {}).items()
                                       if k in {"language", "east_asian", "complex_script"})
        result.append({"title": title, "summary": summary, "details": lines})
    return result


def targets(review):
    rows = []
    for document in (review or {}).get("documents", []):
        source = document["source"]
        resolution = document.get("resolution", {})
        binding = document.get("binding") or {}
        groups = resolution.get("effects", {"change": resolution})
        details = []
        for ident, effect in groups.items():
            bound = binding.get("effects", {}).get(ident, binding if "effects" not in resolution else {})
            chosen = bound.get("targets", effect.get("candidates", []))
            details.append(f"{len(chosen)} passage(s)" if chosen else str(effect.get("reason", "Whole-document change")))
            for target in chosen:
                location = readable(target.get("scope", "document"))
                number = target.get("paragraph_index")
                if number is not None:
                    location += f", paragraph {number + 1}"
                details.append(f"{location}: {target.get('text', '')}")
        rows.append({"title": Path(source).name, "source": source, "details": details})
    return rows



def recovery_targets(result):
    rows = []
    for file in result.files:
        binding = result.key.get("adaptive_bindings", {}).get(str(file.source), {})
        groups = binding.get("effects", {})
        resolution = {"effects": {ident: {"candidates": effect.get("targets", []),
                        "reason": file.reason} for ident, effect in groups.items()}}
        rows.append({"source": str(file.source), "binding": binding, "resolution": resolution})
    return targets({"documents": rows})
