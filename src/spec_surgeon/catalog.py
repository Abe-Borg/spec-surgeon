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
        "font size is unchanged unless a size was asked; a size goes only on runs that set their own size (the rest "
        "follows its styles), and with size_from only on the listed sizes — e.g. 9 pt text becomes 10 pt while 8 pt "
        "headers and a 12 pt note keep their size; the complex-script size is left alone",
        "runs inside pending tracked changes are included unless 'leave revisions alone' was asked",
    ],
    "set_style_font": ["only styles that already name a font (or set a size) change; the others inherit it"],
    "set_doc_defaults_font": ["the document default font (and size) changes, so inheriting text follows; with size_from "
                              "its size changes only when it is one of those sizes"],
    "set_paragraph_props": ["values merge into existing spacing/indents; unspecified attributes stay"],
    "set_properties": [
        "'all caps' is the w:caps display property — the letters are not retyped; bold and italic are written as the "
        "pairs Word itself writes (w:b + w:bCs, w:i + w:iCs)",
        "text that already shows the asked value is left alone (no change, no tracked-change marker); caps and small "
        "caps never touch symbol/Wingdings runs (the glyph would change), the other properties do apply to them",
        "direct formatting on the selected paragraphs by default (tracked when they are targeted, so the reviewer sees "
        "'Formatted: Bold'); the style definition changes only when asked — then every paragraph of that style "
        "changes, a style based on it inherits the change, and a nearer style's own value keeps it",
        "a style change that Word's toggle rule, direct formatting or a symbol run would defeat is refused rather than "
        "applied half-way (set the formatting directly on the paragraphs instead)",
    ],
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
        "the new paragraph copies the anchor's paragraph formatting (style, indents, spacing, tabs, list numbering) "
        "and the font formatting of the anchor's first text run, so it looks like its neighbour — give the text "
        "without a manual number when the anchor is auto-numbered; formatting 'none' copies only the style",
        "an explicit style replaces the inherited one",
        "tracked insertion unless untracked was requested",
    ],
    "fix_page_numbering": ["body page numbering restarts at 1 after the cover"],
    "apply_header_footer_template": [
        "template slots are the yellow-highlighted placeholders and the {{NAME}} placeholders",
        "each fill is extracted from each document by a stated rule (or fallback rule chain), never typed",
        "header/footer distances and titlePg follow the template; page margins stay",
        "pending tracked changes inside the old headers/footers are discarded and flagged by author",
        "with a template SPEC (slot_markers ['reference']): the template spec's own section number and title — found "
        "by the same rules run on it — are replaced in its headers/footers by each spec's own; everything else there "
        "(firm, project, phase, logo, page numbers) is copied as it is",
        "a template spec's own number or title written another way in its headers/footers, or shown by a field, is "
        "refused rather than copied into every spec",
    ],
}

# How a template slot gets its value (apply_header_footer_template.slot_fills). Transform: ops/slots.py; verifier:
# verify/template.py — both implement exactly this.
FILL_RULES = {
    "literal": "{rule: 'literal', value: '<the user's exact approved words>'} — the only rule that places typed text; "
               "it makes the op untracked and the words must appear verbatim in the request or the user's answers",
    "regex": "{rule: 'regex', pattern: '<Python regex>', source?: 'document' | 'header.<default|first|even>' | "
             "'footer.<default|first|even>' (default 'document'), section?: <int>, occurrence?: 'all' | 'first' (default "
             "'all'), flexible_whitespace?: bool (default true), strip?: bool (default true)} — searched in the display "
             "text of every paragraph of the source; the fill is group 1 if the pattern has groups, else the whole match; "
             "with 'all' every match must agree (compared with whitespace collapsed), otherwise the file is set aside as "
             "'ambiguous'",
    "paragraph_after": "{rule: 'paragraph_after', anchor_regex: '<regex>', source?, section?, occurrence?, "
                       "flexible_whitespace?, strip?} — the display text of the first non-blank paragraph after a paragraph "
                       "matching anchor_regex (e.g. the title under a standalone 'SECTION 21 13 13' line); several anchors "
                       "are fine when the paragraphs that follow them agree, or occurrence is 'first'",
    "first_match": "{rule: 'first_match', rules: [<rule>, <rule>, …]} — tries the rules in order and uses the first that "
                   "extracts a non-empty fill. Use it when one batch mixes layouts: e.g. [{paragraph_after on "
                   "'^SECTION \\d{2} \\d{2} \\d{2}$'}, {regex '^SECTION\\s+\\d{2}\\s+\\d{2}\\s+\\d{2}\\s*[-–—:]\\s*(.+)$'}] "
                   "handles both a title on its own line and an inline 'SECTION 21 13 13 – TITLE' line. A syntax error in "
                   "any sub-rule is refused up front; a nested literal counts as a literal",
    "in_template": "slot_markers ['reference'] only: {…rule…, in_template: '<the template spec's own words>'} — the "
                   "text in the template spec's headers/footers this rule's fill replaces. Optional for extracting "
                   "rules (default: the rule run on the template spec itself); required for a literal, e.g. {rule: "
                   "'literal', value: 'ISSUED FOR PERMIT', in_template: '100% CONSTRUCTION DOCUMENTS'}. Every bounded, "
                   "case-insensitive occurrence of the value in the copied parts is replaced; the value must have at "
                   "least 3 letters/digits",
    "whitespace": "with flexible_whitespace (default true) a pattern or anchor that fails on the raw text is retried on a "
                  "view where tabs, non-breaking spaces and runs of spaces are one space; the fill keeps the document's "
                  "own characters whenever the raw match succeeded. Fills are stripped of surrounding whitespace and must "
                  "be non-empty; nothing is ever typed into a slot except a 'literal'",
}

# The op-level 'expect' entries (validated by editspec.validate_spec, checked against every file's plan).
EXPECT_SYNTAX = {
    "checks": "text_diffs | text_diffs_outside_removed_parts | text_diffs_in_kept_parts | members_added | "
              "members_removed | rev_delta; any other key must hold a prose string (recorded, not checked)",
    "value": "an exact integer, or a range {'min': n} | {'max': n} | {'min': n, 'max': n} (non-negative, min ≤ max)",
    "rev_delta": "an object mapping revision kinds to the same value forms; kinds: ins, del, moveFrom, moveTo, "
                 "ins_mark, del_mark, moveFrom_mark, moveTo_mark, rPrChange, pPrChange, sectPrChange, tblPrChange, "
                 "trPrChange, tcPrChange, tblGridChange, numberingChange, cellIns, cellDel, cellMerge",
    "use": "use a range when the batch's files legitimately differ in how many places change (e.g. text_diffs "
           "{'min': 1}); a file whose plan falls outside the range is set aside with code 'expect', never edited",
}

# The usual identity of a CSI spec, as fill rules — for slot_markers ['reference'] the same rules give the template
# spec's own values (read_template shows them and where its headers/footers show them) and each spec's own.
REFERENCE_FILLS = {
    "SECTION_NUMBER": {"rule": "regex", "source": "document",
                       "pattern": r"^SECTION\s+(\d{2}\s?\d{2}\s?\d{2}(?:\.\d{2})?)\b"},
    "SECTION_TITLE": {"rule": "first_match", "rules": [
        {"rule": "paragraph_after", "source": "document",
         "anchor_regex": r"^SECTION\s+\d{2}\s?\d{2}\s?\d{2}(?:\.\d{2})?\s*$"},
        {"rule": "regex", "source": "document",
         "pattern": r"^SECTION\s+\d{2}\s?\d{2}\s?\d{2}(?:\.\d{2})?\s*[-–—:]\s*(.+)$"}]},
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


# What a one-off custom transform (codegen.py) can change and still be verified: each family maps to the V10 assertion
# kind that checks it (tests/test_conversation.py pins the mapping against verify/props.KINDS). Anything else —
# highlight, borders, shading, numbering and lists, tables, images, fields, comments, tracked changes, any change of
# words — has no verified check yet, so a custom transform cannot do it: it needs a new built-in operation (the next
# round). Shown to Claude in the compiler's and the transform writer's instructions.
CUSTOM_TRANSFORM_LIMITS: Dict[str, Any] = {
    "families": {
        "run fonts and font sizes of selected text": "run_fonts",
        "fonts and sizes defined in styles": "style_fonts",
        "the document default font and size": "doc_defaults_fonts",
        "paragraph spacing, indents and alignment": "paragraph_props",
        "page size, margins and orientation of sections": "section_props",
        "which header/footer each section shows": "section_hf_refs",
        "bold, italic, caps, small caps, strikethrough, underline and text colour of selected text or of styles; "
        "keep-with-next, keep lines together, widow control and page break before of paragraphs or of paragraph "
        "styles": "properties",
    },
    "cannot": ["highlight, borders, shading", "numbering and lists",
               "tables", "images", "fields", "comments", "tracked changes", "any change of words"],
    "rules": ["plain (untracked) formatting only", "only the document's text parts (body, headers, footers, footnotes, "
              "endnotes, comments) and its styles part may change", "edited copies only — never written over the "
              "user's files"],
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
        "fill_rules": FILL_RULES,
        "reference_fills": REFERENCE_FILLS,
        "expect_checks": list(EXPECT_CHECKS),
        "expect_syntax": EXPECT_SYNTAX,
        "spec_assertions": list(SPEC_ASSERTION_KINDS),
        "custom_transform_limits": CUSTOM_TRANSFORM_LIMITS,
        "rules": [
            "never invent document text: replacement wording and literal fills come verbatim from the user",
            "a batch that mixes layouts gets ONE spec: extract fills with a first_match chain, match wording with "
            "flexible_whitespace / ignore_formatting_noise, and use ranges in expect; files the spec cannot plan on are "
            "set aside for the next pass, never edited",
            "state every interpretation in 'assumptions'; ask in 'questions' when the request is ambiguous "
            "(nothing runs while questions are open)",
            "use only part names, style ids and section indices that appear in the profiles",
            "if no op can express the request exactly, never approximate silently: ask (understanding, message, "
            "choices — a closest built-in only as an 'alternative' that says how it differs); when nothing fits after "
            "the user's answers, return 'unsupported' and a 'proposal' instead of ops, judged against "
            "custom_transform_limits",
            "two ops must not touch the same thing (the engine refuses overlapping ops)",
            "a TEMPLATE SPEC (an ordinary spec from the architect or another firm — no highlights, no {{placeholders}}) "
            "is used with apply_header_footer_template slot_markers ['reference'] and reference_fills (adapted when "
            "read_template shows another layout); a prepared template keeps the default markers",
            "'match the template's font' is set_font {runs: 'all', only_where_defined: true, font, size, size_from} + "
            "set_style_font {styles: 'all', font, size, size_from} + set_doc_defaults_font {font, size, size_from} + "
            "an effective_font assertion {value, size, size_from}: font and size from the template spec's body_text, "
            "size_from the batch's body text sizes (profiles: fonts.body_text) — other sizes (headers, notes) stay",
        ],
        "editspec_schema": editspec_json_schema(),
    }
    if examples:
        cat["worked_examples"] = worked_examples()
    return cat
