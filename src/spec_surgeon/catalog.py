"""The op catalog and the EditSpec JSON schema — generated from the op classes, so the compiler, the GUI and a
human writing a spec by hand all read the same contract (docs/NL_SURGICAL_EDITOR_PLAN.md §6, Appendix A;
Phase 3). ``spec-surgeon ops --catalog`` prints it as JSON.

The schema is documentation and compiler material; ``editspec.validate_spec`` remains the authority (it also
checks things a JSON schema cannot: mode vs ops, expect vs plan, geometry reference vs ops).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .editspec import EXPECT_CHECKS, MODES, SPEC_ASSERTION_KINDS
from .ops import OPS

EXAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "examples" / "specs"

# What a compiled spec usually has to state about each op (the user sees these in the preview and can veto them).
COMMON_ASSUMPTIONS: Dict[str, List[str]] = {
    "remove_header_footer": [
        "'all' means every section, including first-page and even-page variants",
        "page margins stay as they are (the header/footer space collapses)",
        "pending tracked changes inside removed headers/footers are discarded and flagged by author",
    ],
    "set_font": [
        "Symbol/Wingdings runs (bullets, special characters) are left alone — changing them changes the glyphs",
        "the eastAsia font slot is unchanged unless asked",
        "font size is unchanged",
        "runs inside pending tracked changes are included unless 'leave revisions alone' was asked",
    ],
    "set_style_font": ["only styles that already name a font change; the others inherit it"],
    "set_doc_defaults_font": ["the document default font changes, so inheriting text follows"],
    "set_paragraph_props": ["values merge into existing spacing/indents; unspecified attributes stay"],
    "set_page_setup": ["margins are in twips (1440 = 1 inch); header/footer distances unchanged unless given"],
    "replace_text": ["the replacement wording is exactly the user's", "text inside fields is never edited",
                     "tracked unless the user said otherwise"],
    "delete_paragraphs": [
        "only paragraphs matching the targeted selector are deleted",
        "tracked deletion wraps runs in w:del and marks the paragraph mark unless untracked was requested",
        "paragraphs carrying section breaks or fields are never deleted",
    ],
    "insert_paragraph": [
        "the inserted paragraph text is the user's exact approved wording verbatim",
        "inherits the anchor paragraph's style unless an explicit style was given",
        "tracked insertion unless untracked was requested",
    ],
    "fix_page_numbering": ["body page numbering restarts at 1 after the cover"],
    "apply_header_footer_template": [
        "template slots are the yellow-highlighted placeholders and the {{NAME}} placeholders",
        "each fill is extracted from each document by a stated rule (or fallback rule chain), never typed",
        "header/footer distances and titlePg follow the template; page margins stay",
        "pending tracked changes inside the old headers/footers are discarded and flagged by author",
    ],
}

SELECTOR_GRAMMAR = {
    "scope": "part kinds: document | headers | footers | footnotes | endnotes | comments (resolved via rels)",
    "paragraphs": "'all' or AND of: style_in [styleIds] (no pStyle = default paragraph style), text_regex (Python "
                  "re.search on the block text), in_table (bool), is_empty (bool), index_range [start, end), "
                  "has_bold (bool), is_all_caps (bool), after_heading_regex (str), "
                  "between_paragraphs {start_regex, end_regex?, include_bounds?}",
    "runs": "'all' or {has_direct_font: bool, font_in: [names], not_in_revisions: bool}",
    "sections": "'all' or [section indices]",
    "styles": "'all' | 'all-paragraph-and-character' | {ids: [...]} | {types: [...]}",
    "symbol_runs": "never changed by font ops: a run whose explicit font (direct, character style or paragraph "
                   "style) is a symbol font, or that holds a w:sym",
}


def editspec_json_schema() -> Dict[str, Any]:
    """Appendix A, with every op's own select/params schema attached (oneOf by op name)."""
    op_items = []
    for name, cls in sorted(OPS.items()):
        sel = dict(cls.select_schema or {})
        sel.setdefault("type", "object")
        if "properties" not in sel:
            sel["maxProperties"] = 0                       # the op takes no selector
        sel["additionalProperties"] = False              # unknown keys are refused (as validate_spec does)
        params = dict(cls.params_schema or {})
        params.setdefault("type", "object")
        params["additionalProperties"] = False
        op_items.append({"type": "object", "required": ["op"], "additionalProperties": False,
                         "properties": {"op": {"const": name}, "select": sel, "params": params,
                                        "expect": {"type": "object"}, "tracked": {"type": "boolean"}}})
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "EditSpec",
        "type": "object",
        "additionalProperties": False,
        "required": ["request", "summary", "ops", "mode"],
        "properties": {
            "request": {"type": "string"},
            "summary": {"type": "string"},
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "questions": {"type": "array", "items": {"type": "string"}},
            "applies_to": {"type": "object", "properties": {
                "pattern_ids": {"type": "array", "items": {"type": "string"}}, "or": {"const": "all"}}},
            "mode": {"enum": list(MODES)},
            "review": {"type": "object", "properties": {
                "sample_files": {"type": "integer", "minimum": 1, "maximum": 3},
                "geometry_reference": {"enum": ["source", "template", "none"]}}},
            "ops": {"type": "array", "minItems": 1, "items": {"oneOf": op_items}},
            "assertions": {"type": "array", "items": {"type": "object", "properties": {
                "kind": {"enum": list(SPEC_ASSERTION_KINDS)}}, "required": ["kind"]}},
            "unsupported": {"type": "string"},
            "proposal": {"type": "string"},
        },
    }


def worked_examples() -> List[Dict[str, Any]]:
    out = []
    if EXAMPLES_DIR.is_dir():
        for f in sorted(EXAMPLES_DIR.glob("*.json")):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except ValueError:
                pass
    return out


def op_catalog(examples: bool = True) -> Dict[str, Any]:
    ops = {}
    for name, cls in sorted(OPS.items()):
        ops[name] = {"mode": cls.mode, "description": cls.description, "select": cls.select_schema,
                     "params": cls.params_schema, "verification": cls.verification_profile,
                     "common_assumptions": COMMON_ASSUMPTIONS.get(name, [])}
    cat = {
        "ops": ops,
        "selector_grammar": SELECTOR_GRAMMAR,
        "modes": {"formatting": "no op writes characters", "content": "an op places the user's words, tracked",
                  "untracked": "an op places the user's words untracked (or a literal template fill)"},
        "expect_checks": list(EXPECT_CHECKS),
        "spec_assertions": list(SPEC_ASSERTION_KINDS),
        "rules": [
            "never invent document text: replacement wording and literal fills come verbatim from the user",
            "state every interpretation in 'assumptions'; ask in 'questions' when the request is ambiguous "
            "(nothing runs while questions are open)",
            "use only part names, style ids and section indices that appear in the profiles",
            "if no op can express the request, return 'unsupported' and a 'proposal' instead of ops",
            "two ops must not touch the same thing (the engine refuses overlapping ops)",
        ],
        "editspec_schema": editspec_json_schema(),
    }
    if examples:
        cat["worked_examples"] = worked_examples()
    return cat
