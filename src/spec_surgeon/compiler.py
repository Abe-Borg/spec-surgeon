"""The compiler — a plain-English request → an EditSpec, once per batch (docs/NL_SURGICAL_EDITOR_PLAN.md §6,
Appendix B; Phase 4).

Claude reads the request, the batch's per-pattern profiles (profile.py), the op catalog (catalog.py), the
template's profile when one is attached, project memory (``PROJECT.json`` beside the specs), and any prior pass's
set-aside failure diagnostics, and answers by calling ``submit_editspec`` — or ``ask_user`` when the request is
ambiguous. It can look closer with read-only tools (``inspect_profile``, ``list_styles``, ``sample_paragraphs``,
``show_part_text``, ``read_template``). It never edits a file and never sees XML; the engine plans, applies and
verifies the spec per file. Files are shown and addressed by label (``file_labels``): the file name, or — for files
that share a name in different folders — the name with as many parent folders as tell them apart (``a/Spec.docx``).

A submitted spec is checked before it is accepted:
* ``validate_spec`` (unknown keys/ops/params, mode, assertions) — open ``questions`` or ``unsupported`` are
  outcomes, not errors;
* **traceable words**: every character an op would place (``replace_text.replace``, ``insert_paragraph.text``, a
  ``literal`` template fill) must appear verbatim in the request, the user's answers or project memory — otherwise
  the model must ask;
* a **dry plan** on one representative file per structure pattern: fatal spec-level failures (``spec``,
  ``spec-mode``, ``geometry``, ``conflict``, ``bad-params``, ``unknown-slot``, ``slot-overlap``, ``template-slot``)
  are always compile errors; every other refusal (``unfilled-slot``, ``expect``, ``not-found``, ``spans-runs``, ``tracked-format``,
  ``section-break`` … — anything a particular file's structure can cause) is accepted as long as at least one
  pattern in the batch plans cleanly, allowing Pass 1 to process the clean majority and set aside the outliers
  for the next pass; a spec that plans on no file at all (or a batch with no files) is rejected.
A rejected spec goes back with its errors while investigation makes progress; repeated equivalent failures
without new evidence stop the turn, and configurable resource limits bound the investigation.

The client is injectable (``client=``) so the loop is tested without the API; the default needs the
``anthropic`` package and credentials (``ANTHROPIC_API_KEY`` or an ``ant auth login`` profile).

Diagnostics: ``CompileResult.transcript`` is the whole exchange (``conversation.Conversation.exchange``, also in
``conversation.json``) — what the model was given, every call's text and tool calls (a submitted spec in full), every
tool result and rejection with its reasons, each call's stop reason, serving model, tokens, time and request id; page
images never go in. An API call that fails (no network, a bad key, an overloaded service, Claude not installed) ends
the TURN as ``failed`` with the error, its HTTP status and request id, and the exchange so far; anything else is a
defect and propagates (``llm.is_api_error`` decides; ``is_api_error``, ``api_error_text`` and ``block_dict`` are
re-exported here). ``spec-surgeon do`` and the window write it to ``compile.json``.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from .catalog import op_catalog
from .editspec import SpecError, plan_spec, validate_spec
from .fingerprint import fingerprint
from .ops import JobException
from .ooxml import paragraphs
from .ops.selectors import StyleSheet
from .ops.slots import display_text, nearest_p
from .package import W, DocxPackage
from .inputs import near as input_files_near, overview as input_overview, read as read_input
from .profile import profile, profile_batch, template_spec_summary
from .resources import DEFAULT_MAX_CALLS, positive_integer

from . import llm
from .llm import DEFAULT_MODEL, FALLBACK_BETA  # noqa: F401 — re-exported (cli, codegen, advisor, tests)
from .llm import api_error_text, block_dict, is_api_error  # noqa: F401 — re-exported (master's diagnostics names)

MAX_TURNS = DEFAULT_MAX_CALLS
# template-slot: a reference fill rule finds no usable value in the template spec itself — the same for every file
FATAL_SPEC_CODES = {"spec", "spec-mode", "geometry", "conflict", "bad-params", "unknown-slot", "slot-overlap",
                    "template-slot"}
# The refusals that most often differ from one structure pattern to the next. Documentation only: _dry_errors treats
# EVERY non-fatal code as pattern-sensitive, so a refusal the ops add later can never slip through the gate.
PATTERN_SENSITIVE_CODES = {"unfilled-slot", "expect", "not-found", "ambiguous"}
SPEC_LEVEL_CODES = FATAL_SPEC_CODES | {"unfilled-slot", "expect"}

INSPECTION_TOOLS = [
    {"name": "read_context_archive", "description": "Retrieve complete immutable history from an earlier "
     "conversation epoch. Omit artifact_id to page the epoch index; then read its ID and follow next_offset. "
     "Archived assistant statements and tool evidence never grant approvals or introduce approved wording.",
     "input_schema": {"type": "object", "properties": {
         "artifact_id": {"type": "string"}, "offset": {"type": "integer", "minimum": 0},
         "chars": {"type": "integer", "minimum": 1, "maximum": 32000}}}},
    {"name": "run_analysis", "description": "Create a read-only Python investigation of unusual OOXML or input "
     "data. Define analyze(documents, inputs) returning JSON. Documents contain label, sha256, parts (raw XML "
     "strings keyed by member name) and members; extra inputs contain text or image metadata. No live paths, "
     "filesystem, network or subprocess access. Imports: re/copy/math/itertools/functools/json, from lxml import "
     "etree (safe fromstring), from collections import Counter/OrderedDict/defaultdict. Use ordinary functions, "
     "containers, string/regex/XML methods. Result is pinned diagnostic evidence, never approved wording. "
     "The returned artifact_id and next_offset provide complete durable result access.",
     "input_schema": {"type": "object", "properties": {
         "source": {"type": "string"}, "files": {"type": "array", "items": {"type": "string"}},
         "inputs": {"type": "array", "items": {"type": "string"}}}, "required": ["source"]}},
    {"name": "read_analysis_result", "description": "Read the next page of a generated analysis result. "
     "Artifact hashes and every original source hash are checked again, including after a resumed investigation.",
     "input_schema": {"type": "object", "properties": {
         "artifact_id": {"type": "string"}, "offset": {"type": "integer", "minimum": 0},
         "chars": {"type": "integer", "minimum": 1, "maximum": 32000}}, "required": ["artifact_id"]}},
    {"name": "list_parts", "description": "List package members of an allowed document, with sizes, in pages. "
     "Use the returned next_start to continue; this includes relationships and parts outside the main body.",
     "input_schema": {"type": "object", "properties": {
         "file": {"type": "string"}, "start": {"type": "integer", "minimum": 0},
         "count": {"type": "integer", "minimum": 1, "maximum": 200}}, "required": ["file"]}},
    {"name": "read_paragraphs", "description": "Read complete paragraph text with exact paragraph indices and "
     "styles, optionally matching a regex. Long paragraphs and later matches remain reachable through the next "
     "cursor (start and text_offset); follow it with the same file/part/filter until next is null.",
     "input_schema": {"type": "object", "properties": {
         "file": {"type": "string"}, "part": {"type": "string"},
         "start": {"type": "integer", "minimum": 0}, "text_offset": {"type": "integer", "minimum": 0},
         "count": {"type": "integer", "minimum": 1, "maximum": 200},
         "chars": {"type": "integer", "minimum": 1, "maximum": 16000},
         "regex": {"type": "string"}, "style": {"type": "string"}}, "required": ["file"]}},
    {"name": "read_xml_page", "description": "Read an XML inspection view of any package XML/relationships part "
     "or one paragraph, without truncating inaccessible tails. Continue using next_offset with the same arguments. "
     "Offsets are characters in the inspection view, not source-byte edit locations.",
     "input_schema": {"type": "object", "properties": {
         "file": {"type": "string"}, "part": {"type": "string"},
         "paragraph_index": {"type": "integer", "minimum": 0},
         "offset": {"type": "integer", "minimum": 0},
         "chars": {"type": "integer", "minimum": 1, "maximum": 16000}}, "required": ["file"]}},
]

TOOLS = INSPECTION_TOOLS + [
    {"name": "inspect_profile", "description": "The full profile of one structure pattern of the batch (sections, "
     "what each header/footer shows, slots, styles in use with fonts, counts, hygiene).",
     "input_schema": {"type": "object", "properties": {"pattern_id": {"type": "string"}}, "required": ["pattern_id"]}},
    {"name": "list_styles", "description": "Every style defined in one batch file: id, type, name, basedOn, and the "
     "fonts/size it sets directly.",
     "input_schema": {"type": "object", "properties": {"file": {"type": "string", "description": "file name from the profiles"}},
                      "required": ["file"]}},
    {"name": "sample_paragraphs", "description": "Body paragraphs of one file (display text), optionally only those of "
     "a paragraph style and/or matching a Python regex.",
     "input_schema": {"type": "object", "properties": {
         "file": {"type": "string"}, "style": {"type": "string"}, "regex": {"type": "string"},
         "n": {"type": "integer", "minimum": 1, "maximum": 40}}, "required": ["file"]}},
    {"name": "show_part_text", "description": "The paragraph texts of one part of one file (e.g. word/footer2.xml).",
     "input_schema": {"type": "object", "properties": {"file": {"type": "string"}, "part": {"type": "string"}},
                      "required": ["file", "part"]}},
    {"name": "read_template", "description": "The template file the user attached: its profile, and what it offers "
     "as a template (template_spec: prepared slots, or — an ordinary spec from another firm — its own section "
     "number/title and where its headers/footers show them; its body text font/size).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "read_input_file", "description": "One of the INPUT FILES listed beside the documents (a table the user "
     "keeps — .json, .csv, .xlsx — or a picture): a sheet's or a CSV's rows (the first is the headers), a JSON file's "
     "text, a picture's format, size and md5. Only a listed file, by the exact path the listing shows.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "the exact path from INPUT FILES"},
         "sheet": {"type": "string", "description": "an .xlsx sheet's name (default: the first)"},
         "rows": {"type": "integer", "minimum": 1, "maximum": 40}}, "required": ["path"]}},
    {"name": "ask_user", "description": "Ask the user before anything runs; their reply comes back as this tool's "
     "result. Use it when the request is ambiguous, needs words the user has not given, or no operation does exactly "
     "what was asked (then say what you understood, what is missing, and offer choices).",
     "input_schema": {"type": "object", "properties": {
         "understanding": {"type": "string", "description": "one sentence: what you understood the user wants"},
         "message": {"type": "string", "description": "what you need to tell the user (e.g. what no operation can "
                                                      "do, and why) — plain words, no XML"},
         "questions": {"type": "array", "items": {"type": "string"}},
         "choices": {"type": "array", "items": {"type": "object", "properties": {
             "label": {"type": "string", "description": "what picking this means, in plain words; for an "
                                                       "alternative, say exactly how it differs from what was asked"},
             "kind": {"type": "string", "enum": ["clarify", "alternative", "rephrase"]},
             "wording": {"type": "string", "description": "ONLY when picking this choice would place words in the "
                                                         "documents: those exact words (the user's pick approves "
                                                         "them; nothing else does)"},
             "detail": {"type": "string"}}, "required": ["label", "kind"]}}}}},
    {"name": "submit_editspec", "description": "Submit the EditSpec. It is validated and dry-planned; errors come "
     "back to fix. A spec that says 'unsupported' must also judge custom_transform.",
     "input_schema": {"type": "object", "properties": {
         "spec": {"type": "object"},
         "custom_transform": {"type": "string", "enum": ["possible", "outside_limits"],
                              "description": "for an unsupported spec only: could a one-off custom transform do it "
                                             "within the catalog's custom_transform_limits?"},
         "custom_transform_reason": {"type": "string"}}, "required": ["spec"]}},
]

SYSTEM = """You compile a user's request about editing Word (.docx) specification files into an EditSpec — a JSON \
program over a fixed library of deterministic operations. A separate engine plans, applies and verifies the spec on \
every file; your inspection tools can read source text and XML, but never edit files.

The contract you must keep (these files go to clients; silent loss or invented text is unacceptable):
- Formatting edits place zero characters. Content edits place only words the user gave verbatim (or a template's \
own words). Never invent, reword, or "improve" document text; if the user did not give the exact words, ask.
- Every interpretation you make goes into "assumptions" (the user sees them before anything runs). When the request \
is ambiguous in a way an assumption cannot honestly cover, call ask_user instead of guessing.
- Use only ops, selector keys and params from the catalog; use only part names, style ids and section indices that \
appear in the profiles or tool results.
- When no op can express the request exactly, do not give up and never silently approximate. If a clarification \
could make it expressible, or a built-in op could do something close, call ask_user with "understanding" (one \
sentence: what you understood), "message" (what no op can do, and why, in plain words) and "choices": kind \
"clarify" for a likely meaning an op can do; kind "alternative" for the closest built-in action, its label saying \
exactly how it differs from what was asked. A choice that would place words in the documents carries those exact \
words in "wording" — the user's pick approves them; your own text never does. When nothing fits even after the \
user's answers, submit {"request", "summary", "unsupported": "<why no op fits>", "proposal": "<what a one-off \
transform would do>"} with no ops, and beside the spec set custom_transform to "possible" only when a one-off \
transform could do it within the catalog's custom_transform_limits, else "outside_limits" with custom_transform_reason.
- The user may reply after your spec was previewed: that reply is a correction, and the app's notes before it say \
what the preview found. Submit a revised spec (or ask). The user's replies are approved words like the request.
- Two ops must not touch the same thing. "Change all fonts" is three ops (set_font on runs with a direct font, \
set_style_font, set_doc_defaults_font) plus an effective_font assertion.
- Bold, italic, caps, small caps, strikethrough, underline, text colour, keep-with-next, keep lines together, widow \
control and page break before are set_properties: one op per selection carrying all of them (it never conflicts with \
itself); "all caps" is a display property — no letters are retyped. Format the selected paragraphs directly by default \
(tracked when targeted, so the reviewer sees "Formatted: Bold"); change a style (select.styles) only when the user \
speaks of the style or wants every paragraph of that style to follow — the profiles' styles_in_use show bold, italic, \
caps and colour per style. A font change and set_properties on the same paragraphs are two specs.
- "Each PART on a new page": paragraph pageBreakBefore on every PART heading AFTER the first — the PART pattern (or \
style) plus after_heading_regex of the same pattern, e.g. {"text_regex": "^PART\\\\s+\\\\d", "after_heading_regex": \
"^PART\\\\s+\\\\d"} — so PART 1 stays under the section title. "No stranded article titles": keepNext on the article \
titles (their style id from styles_in_use, else a pattern for typed numbers such as "^\\\\d+\\\\.\\\\d+\\\\s"). Widow/orphan \
control is on by default in Word (widowControl true changes only what turned it off).
- Recolouring text by its colour ("the editor's blue text to black"): set_properties run.color with select.runs.color_in \
— the colours exactly as the profiles show them (markup.run_colors, styles_in_use: hex such as 0000FF or a theme name \
such as accent1); "black" is "auto" (Word's Automatic) unless the user asks for an explicit 000000. Name the colours in \
assumptions; never color_in black/auto text.
- Tab stops are set_tab_stops: set [{pos, align, leader}], clear [positions], clear_others (exactly the set stops \
remain); pos in twips from the left margin (1440 = 1 inch) or "right_margin" / "center" — each file's own text width, \
use them for "at the right margin". styles_in_use show each style's tab_stops. set_tab_stops, set_paragraph_props and \
set_properties' paragraph flags all change paragraph properties: on the same paragraphs they are separate specs (offer \
a chain).
- Paragraph borders and shading are set_borders_shading: borders is the paragraph's WHOLE border box — name every side \
that should show (top, left, bottom, right, between, bar; size in eighths of a point, 4 = ½ pt; space in points; colour \
6 hex digits or auto), a side not named is removed even where the style shows it, "none" removes every border; shading \
{fill} (D9D9D9 is Word's 15% grey) or "none". Spacing, indents and alignment IN A STYLE ("make the PR1 style 6 pt \
after") are set_paragraph_props with select {"styles": {"ids": [...]}} (alone, paragraph styles only, never tracked); \
set_borders_shading takes the same styles target ("the note style gets a box"). The profiles' styles_in_use show each \
style's borders, shading, spacing and alignment.
- "Clear the direct/manual formatting", "make the text follow its styles again" is clear_direct_formatting (scope, \
paragraphs, runs): run formatting of runs and marks and paragraph formatting go; styles, list numbers, hidden text and \
the language never change; highlight, superscripts (vertAlign, position) and outline levels only when the user asks \
(also). keep takes element names: "but keep the bold" is keep ["b", "bCs"]. Say in assumptions what stays.
- The proofing language ("set the language to US English") is set_language {language: "en-US"}; "British spelling to \
US" keeps other languages with replace_only ["en-GB"] — the profiles' markup.languages shows the languages in use. It \
changes run properties and styles everywhere: a spec of its own (offer a chain). Never tracked.
- Applying a style to paragraphs picked by appearance or pattern ("make every bold all-caps paragraph Heading 1", "the \
paragraphs after PART 2 become PR1") is apply_style: select.paragraphs with the selection rules (has_bold, is_all_caps, \
text_regex, after_heading_regex, between_paragraphs, style_in), style = the style's id or name; direct_formatting \
"clear" when the paragraphs should look exactly like the style (their own bold, sizes, spacing go — by the clear rules), \
"keep" (default) otherwise. Style mapping ("the architect's Heading 1 becomes our PRT everywhere") is map_styles with \
map [{from, to}] — from: the architect's style ids from styles_in_use / character_styles_in_use / table_styles_in_use. \
The user's own ("our office") styles come from the ATTACHED TEMPLATE: read_template lists its styles; put its exact path \
in template, and a style the file lacks is imported with the styles it is based on, linked to and followed by, and its \
list numbering. existing "keep" (default) leaves a style the file already has as the file defines it; "replace" only \
when the user says their definition must win. A default paragraph style as from (Normal) maps every paragraph without \
a style too. "Remove unused styles" is remove_unused_styles (which "all", or "custom" for the document's own styles; \
keep for styles to spare). Restyling a paragraph changes how all its properties resolve: apply_style or map_styles \
beside set_properties, set_tab_stops, set_paragraph_props or set_borders_shading on the same paragraphs, or beside a \
change of the style definitions they use, are separate specs; removing the architect's styles that mapping left unused \
is a chain's LAST stage (in the same spec it would see them still in use). Field codes naming a mapped style (TOC \\t, \
STYLEREF) are flagged, not edited — say so in assumptions.
- Table formatting is set_table_props (select.tables: "all" or {index_range, style_in, first_row_regex, text_regex, \
nested}; scope for tables in headers/footers): borders is the table's WHOLE border set (top, left, bottom, right, \
insideH, insideV — a side not named is removed, "none" removes every border; size in eighths of a point, 4 = ½ pt), \
cell_margins {top, left, bottom, right} in twips merge into the table's own, width {"pct": 100} | {"twips": n} | "auto", \
repeat_header_rows n ("repeat the header row" is 1), cant_split true ("rows don't break across pages"). Cells with \
their own borders or margins keep them unless clear_cell_overrides. A table style for every table is \
apply_table_style {style} (a style the file lacks comes from the template, like apply_style; direct_formatting "clear" \
so the style shows). The profiles' tables show each pattern's tables, their styles and header rows. Never tracked; \
the two on the same tables are separate specs.
- Numbering. The profiles' lists show each list's levels (format, styles, the first number shown) and which \
paragraphs the engine does not evaluate and why; numbered_styles show what a numbered style's first paragraph would \
show ("PART 1 -", "1.1", "A."); lists.typed_numbers how many paragraphs start with a typed number. "Convert to our \
PART / 1.1 / A. / 1. / a. scheme" with an example file is convert_numbering {template: its exact path, levels: the \
example file's numbered style ids level 0 first — from read_template's lists.numbered_styles, one list —}; the \
paragraphs converted are those of the list numbering the most paragraphs ("from": "main", the default) or of named \
styles per level (from.styles); level_map only when the architect's levels do not line up with yours (say so). Typed \
numbers ("A." and "1." typed at the start of paragraphs, the profile's typed_numbers) becoming real list numbers is \
convert_typed_numbers {template, levels} — tracked by default (the removed typed numbers show as deletions), tracked \
false for clean copies; it refuses a file unless every paragraph then reads exactly as before (a typo in the typed \
numbers sets the file aside — offer audit_numbering to find them). Neither writes numbering text: the scheme is the \
user's own. Both are specs of their own beside other ops on the body. "Restart the \
articles at every PART" is set_list_level {style: the article style, restart: "after_higher"} — only when the PARTs \
are numbered by a higher level of the same list (else it changes nothing: say so and offer converting the numbering \
first); "zero-pad the articles" (1.01 instead of 1.1) is set_list_level {style, zero_pad: true}; a level's format is \
format (decimal, decimalZero, upperLetter, lowerLetter, upperRoman, lowerRoman). "Check the numbering" (gaps, \
duplicates, empty numbered paragraphs) is audit_numbering — it changes nothing and reports; unnumber_empty true \
removes the number from empty numbered paragraphs (the later numbers move up — say so). List definitions are never \
tracked, and a numbering op beside an op that restyles paragraphs or changes the styles they use is a separate spec.
- Page setup is set_page_setup: margins (twips; the header's and footer's distance from the page edge are \
margins.header / margins.footer), size, orientation, different_first_page true/false (a first page with its own \
header/footer — the cover). Sections by index, or by what they contain: {"containing": {"text_regex": \
"^SCHEDULE"}}, {"with_tables": true}, {"index_range": [a, b]} — "landscape pages for the schedules" is orientation \
landscape on the sections containing them (only sections that exist: the engine never adds a section break — say so \
when the schedule shares a section with other text); margins are not rotated unless given.
- Tables and pictures beside the documents are listed as INPUT FILES (read_input_file shows one): put the exact path \
in fill_variables.table.file, replace_table.pairs_table.file or swap_image.image / match.image — what a table holds is \
the user's. Words you type into a spec yourself (replace_table pairs, fill_variables values, set_issue_stamp text, the \
END OF SECTION wording) must be the user's exact words, like any replacement.
- Text-changing ops are tracked unless the user asks for clean copies (tracked false). name_output, and \
make_values_agree with the file name among its places, rename the outputs: they write copies, never in place.
- Clean-up ops (remove_comments, remove_highlight, remove_hidden_text, remove_notes, remove_empty_paragraphs, \
remove_hyperlinks, unlink_fields, remove_watermark, resolve_tracked_changes) place no words; a file with nothing to \
remove is a no-op (allow_missing defaults true). The profiles' "markup" shows what a pattern carries: comments and \
their authors, tracked changes by author and the dates they span, highlight colours, hidden runs, run colours, \
hyperlinks, field types, a watermark, the document properties, whether it is marked final.
- resolve_tracked_changes accepts or rejects tracked changes ONLY when the user asks for exactly that (never to tidy a \
file on your own); name the authors/dates it covers in assumptions, from the profiles. It touches every part it \
resolves, so it is a spec of its own. set_document_state (track changes on/off, mark as final) and \
set_document_properties likewise only on request; a watermark's words and a property's value are the user's exact \
words (ask when they did not give them).
- remove_notes: build its rules from the profiles — the note style ids in styles_in_use, the colours in \
styles_in_use / markup.run_colors, the words the notes begin with ("Retain", "Delete", "Specifier Note") — and state \
them in assumptions. One rule per way a note is marked ({"style_in": ["CMT"]}, {"text_regex": "^(Retain|Delete)\\\\b", \
"color_in": ["0000FF"]}); colours as the profile shows them (hex 0000FF or a theme name like accent1); never a bare \
color_in rule for black or auto text; "runs" only when the user speaks of inline notes or blue words inside paragraphs \
that stay. Bracketed notes in plain text ("[Note to specifier: …]") are a replace_table regex pair with an empty \
replacement.
- remove_comments: which "all", "resolved" or "authors" (with authors as the profiles name them). remove_highlight: \
colors only when the user named colours (Word's names: yellow, green, cyan …); shading is not highlighting. \
remove_hyperlinks: which "external" (the default; web and e-mail), "internal" (bookmarks, a table of contents) or \
"all"; keep_style true only when the words should keep looking like links. unlink_fields: types from the profiles' \
markup.fields ("DOCPROPERTY", "REF" …) or "all" — page numbers never; a table of contents cannot be converted. \
remove_empty_paragraphs: mode "collapse" for double or extra blank lines, "all" when the user wants none. Tracked, \
the clean-up ops share one spec; untracked (clean copies), an op that deletes paragraphs (remove_hidden_text, \
remove_notes, remove_empty_paragraphs) restructures the part, so it cannot share a spec with another op over that part \
— offer a chain, one stage each.
- add_watermark: text = the user's exact words, Word's look unless the user describes another (color 6 hex digits, \
layout diagonal or horizontal, size in points or "auto"); "change the watermark to X" is add_watermark with replace \
true; "remove the watermark" is remove_watermark (text only when the user names which). A watermark is untracked: \
beside tracked word changes it is a second spec — offer a chain.
- set_document_properties: "clear the author names" is clear ["author", "last_modified_by"] (tracked changes and \
comments keep their authors); leave review.geometry_reference out (the op chooses it per file). Say in assumptions that \
a field showing a changed property keeps its saved value until Word updates fields, and that turning Track Changes off \
(set_document_state) accepts nothing. set_document_properties and set_document_state on the same property (the \
Status with Mark as Final) are two specs.
- The attached template may be a TEMPLATE SPEC: an ordinary spec from the architect or another firm (read_template \
says which). "Make the header/footer match the template spec" is apply_header_footer_template with slot_markers \
["reference"] and the catalog's reference_fills as slot_fills (adapt them when the profiles show another layout): \
its own section number and title are replaced by each spec's own and everything else is copied. "Match the \
template's font" follows the catalog rule: font and size from the template spec's body_text, size_from from the \
batch's body_text sizes. State the template's values and the sizes in assumptions.
- mode: "formatting" unless an op places characters; "content" for tracked wording changes; "untracked" when \
the user asked for untracked wording or a template has literal fills.
- Inspect as deeply as needed before deciding. read_paragraphs, list_parts and read_xml_page have continuation \
cursors: follow them when a sample omits relevant content. A short preview is not evidence that a target is absent. \
Use new evidence and different hypotheses to fix rejected candidates; do not repeat the same failed approach.
- Omit review.sample_files unless the user requests an explicit sample count. Automatic sampling covers every \
structural pattern and fills the shared allowance; do not copy a small sample count from an example by default.
- Finish each turn by calling submit_editspec once with the whole spec, or ask_user.
"""

CHAT_NOTE = """
- This is a conversation: plain text without a tool call is shown to the user as your message and waits for their \
reply.
- view_pages shows you rendered pages (of a batch file or the template) when what the user means depends on how the \
pages look; the profiles cannot show you lines, boxes, logos or layout.
- YOUR LIBRARY (in the first message; list_library for the rest) is the user's saved recipes — specs they previewed \
before — and operations — custom transforms that passed. When the request matches one (the same change, by its name, \
its description or the request it was saved from: "like last time"), read it with read_library_item and offer it as a \
choice {"kind": "recipe" or "operation", "name": <its exact name>}: the app builds that choice from the library and \
shows the user the exact words a recipe places; only the user's pick approves them. Never copy a recipe's words into a \
spec of your own before that pick — they are refused like any words the user did not give. Offer your own reading \
beside it when both fit. A picked recipe is previewed as saved and the user may then ask you for changes; a picked \
operation is tried on the sample files first.
- The library also holds snippets — saved paragraphs. When the user wants one of them inserted ("add our hydrant \
flow test paragraph"), offer it as a choice {"kind": "snippet", "name": <its exact name>}: the app shows its exact \
words, and the user's pick comes back to you as their reply with those words approved. Then submit insert_paragraph \
with text exactly those words (the snippet's style and formatting, allow_missing true when it goes in every file that \
has the anchor, expect text_diffs {"max": 1} when it goes in once per file) — asking which paragraph it goes after if \
that is not clear.
"""


VIEW_PAGES_TOOL = {
    "name": "view_pages", "description": "See pages of a batch file (or of the attached template) as rendered images "
    "— when the request is about how something looks (a line, a logo, a box, 'like the template') or you need the "
    "layout. Up to 3 pages per call; inspect the pages needed to resolve the uncertainty.",
    "input_schema": {"type": "object", "properties": {
        "file": {"type": "string", "description": "a file name from the profiles, or \"template\""},
        "pages": {"type": "array", "items": {"type": "integer", "minimum": 1}, "maxItems": 3}},
        "required": ["file"]}}
# The user's library in the conversation (chat only — the one-shot compile has nobody to pick a choice): an index of it
# in the first message (``library_index``), these two read-only tools, and three choice kinds Claude may NAME
# (``recipe``, ``operation``, ``snippet``) that the app builds from the library itself (conversation._library_choice;
# CLAUDE.md rule 13).
INDEX_LIMIT = 40                                   # items the first message's index shows; list_library has the rest
OFFERABLE_KINDS = ("recipe", "operation", "snippet")   # what Claude may name as a choice (conversation.NAMED_LIBRARY_KINDS);
                                                   # a chain (chain.py) runs from the window or the command line only
INDEX_BUDGET = 18000                               # characters of a list_library result (the tool-result cap is 20,000)
DETAIL_BUDGET = 18000                              # characters of a read_library_item result: the spec gives way first
ENTRY_OPS = 30                                     # op names an index entry lists before it counts the rest
DESCRIPTION_CHARS, REQUEST_CHARS, BRIEF_CHARS = 160, 200, 3000
PLACED_WORDS_RULE = ("these words count as the user's ONLY once the user picks the choice offering this recipe; a spec "
                     "of yours that places them before that pick is refused like any words the user did not give")
SNIPPET_WORDS_RULE = ("this text counts as the user's ONLY once the user picks the choice offering this snippet (their "
                      "reply then approves it); a spec of yours that places it before that pick is refused like any "
                      "words the user did not give — after it, place it verbatim with insert_paragraph")

LIBRARY_TOOLS = [
    {"name": "list_library", "description": "The user's library: every saved recipe (a spec they previewed before: its "
     "op names, mode, template files, how many words it places), operation (a custom transform that passed: its "
     "proposal) and snippet (a saved paragraph: how long it is, its style), each with its name, description and the "
     "request it was saved from. The first message carries the same index (up to 40 items); call this for the rest, "
     "or to look again.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "read_library_item", "description": "One library item in full. A recipe: its spec as saved, the exact "
     "words it places (they count as the user's ONLY if the user picks the choice that shows them), its template "
     "files and whether each exists, and a dry plan over this batch. An operation: its manifest (request, proposal, "
     "select/params, attempts, the conversation it came from) — never its code. A snippet: its text (it counts as the "
     "user's ONLY once the user picks the choice offering it), style and formatting. Offer the item as a choice "
     "{\"kind\": \"recipe\" | \"operation\" | \"snippet\", \"name\": <this exact name>} when it does what the "
     "user asks.",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "the item's exact name, from YOUR LIBRARY or list_library"}},
         "required": ["name"]}},
]


def _chat_ask_user() -> Dict[str, Any]:
    """ask_user as the conversation offers it: a choice may also NAME one of the user's library items (kinds ``recipe``
    and ``operation``, with ``name``); the app builds that choice from the library. The one-shot tool is unchanged."""
    tool = copy.deepcopy(next(t for t in TOOLS if t["name"] == "ask_user"))
    choice = tool["input_schema"]["properties"]["choices"]["items"]
    choice["properties"]["kind"]["enum"] = ["clarify", "alternative", "rephrase", "recipe", "operation", "snippet"]
    choice["properties"]["name"] = {
        "type": "string", "description": "for kind recipe, operation or snippet only: the exact name of the user's "
                                         "library item to offer (the app builds that choice from the library; your "
                                         "label and wording are not used for it)"}
    return tool


ASK_USER_CHAT = _chat_ask_user()
# the conversation's tools; the advisor and the transform writer never get these
CHAT_TOOLS = [ASK_USER_CHAT if t["name"] == "ask_user" else t for t in TOOLS] + [VIEW_PAGES_TOOL] + LIBRARY_TOOLS


def system_prompt(chat: bool = False) -> str:
    """The system prompt — built once per conversation and never changed during it."""
    return (SYSTEM + (CHAT_NOTE if chat else "")
            + "\nOP CATALOG (ops, selector grammar, common assumptions, custom_transform_limits, the EditSpec JSON schema, "
            "worked examples):\n"
            + json.dumps(op_catalog(), ensure_ascii=False))


@dataclass
class CompileResult:
    status: str                                   # ok | questions | unsupported | failed
    spec: Optional[Dict[str, Any]] = None
    questions: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    model: str = DEFAULT_MODEL
    usage: Dict[str, int] = field(default_factory=dict)
    served_models: List[str] = field(default_factory=list)   # what actually answered (a refusal fallback may differ)
    custom_transform: str = ""                    # unsupported only: "possible" | "outside_limits" | "" (not judged)
    custom_transform_reason: str = ""
    transcript: List[Dict[str, Any]] = field(default_factory=list)   # the model exchange (Conversation.exchange)
    seconds: float = 0.0                          # time with Claude and the tools, never waiting for the user

    def to_dict(self):
        return {"status": self.status, "spec": self.spec, "questions": self.questions, "errors": self.errors,
                "tool_calls": self.tool_calls, "model": self.model, "served_models": self.served_models,
                "usage": self.usage, "custom_transform": self.custom_transform,
                "custom_transform_reason": self.custom_transform_reason,
                "seconds": round(self.seconds, 2), "transcript": self.transcript}


def _client():
    """The Claude client every caller resolves at call time (tests replace this name with a scripted fake)."""
    return llm.client()


def load_project_memory(inputs: Sequence[Path]) -> Dict[str, Any]:
    """PROJECT.json beside the specs (§14.4: yes) — e.g. {"stamp": "...", "author": "...", "template": "...",
    "notes": [...]}. The first one found in an input folder or a file's folder wins."""
    seen = set()
    for p in inputs:
        p = Path(p)
        d = p if p.is_dir() else p.parent
        if d in seen:
            continue
        seen.add(d)
        f = d / "PROJECT.json"
        if f.is_file():
            try:
                mem = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(mem, dict):
                    return {**mem, "_path": str(f)}
            except ValueError:
                pass
    return {}


def memory_source(memory: Dict[str, Any]) -> str:
    """PROJECT.json as a source of approved words — without the path it was read from (a folder name is not wording
    the user approved for the documents)."""
    return json.dumps({k: v for k, v in (memory or {}).items() if k != "_path"}, ensure_ascii=False)


def _norm(s: str) -> str:
    return " ".join((s or "").split())


def _extract_literal_rules(rule: Any, out: List[str]) -> None:
    if isinstance(rule, dict):
        if rule.get("rule") == "literal" and isinstance(rule.get("value"), str) and rule["value"]:
            out.append(rule["value"])
        if isinstance(rule.get("rules"), list):
            for r in rule["rules"]:
                _extract_literal_rules(r, out)
    elif isinstance(rule, list):
        for r in rule:
            _extract_literal_rules(r, out)


END_OF_SECTION_DEFAULT = "END OF SECTION {number}"     # ops/end_of_section.DEFAULT_TEXT (a test pins them equal)


def placed_words(spec: Dict[str, Any]) -> List[str]:
    """Every string an op of the spec would place in a document as typed in the spec — never what a table file, a
    template, a snippet the user picked or the documents themselves give (agreeing values, kept options, numbers)."""
    out: List[str] = []
    for o in spec.get("ops") or []:
        if not isinstance(o, dict):
            continue
        params = o.get("params") if isinstance(o.get("params"), dict) else {}
        op = o.get("op")
        if op == "replace_text" and isinstance(params.get("replace"), str) and params["replace"]:
            out.append(params["replace"])
        if op == "insert_paragraph" and isinstance(params.get("text"), str) and params["text"]:
            out.append(params["text"])
        if op == "apply_header_footer_template":
            for rule in (params.get("slot_fills") or {}).values():
                _extract_literal_rules(rule, out)
        if op == "replace_table":
            for pair in params.get("pairs") or []:
                if isinstance(pair, dict) and isinstance(pair.get("replace"), str) and pair["replace"]:
                    out.append(pair["replace"])
        if op == "set_issue_stamp" and isinstance(params.get("text"), str) and params["text"]:
            out.append(params["text"])
        if op == "fill_variables" and isinstance(params.get("values"), dict):
            out += [v for v in params["values"].values() if isinstance(v, str) and v]
        if op == "ensure_end_of_section":
            text = params.get("text") if isinstance(params.get("text"), str) else END_OF_SECTION_DEFAULT
            out += [piece.strip() for piece in text.split("{number}") if piece.strip()]
        # built-in batch 2: a watermark's words and the document properties set are the user's words too
        if op == "add_watermark" and isinstance(params.get("text"), str) and params["text"]:
            out.append(params["text"])
        if op == "set_document_properties":
            for key in ("set", "custom"):
                if isinstance(params.get(key), dict):
                    out += [v for v in params[key].values() if isinstance(v, str) and v]
    return out


def untraceable(spec: Dict[str, Any], sources: Sequence[str]) -> List[str]:
    """The words the spec would place that appear verbatim (whitespace-normalised) in none of the sources. Each source
    is searched on its own: a phrase may not be stitched together from the end of one and the start of the next."""
    hay = [_norm(s) for s in sources if s]
    return [w for w in placed_words(spec) if not any(_norm(w) in h for h in hay)]


def file_labels(files: Sequence[Path]) -> Dict[str, str]:
    """One label per input file, unique across the batch — what Claude is shown and addresses files by. A file whose
    name no other input has is labelled by its name (a single-folder batch reads as before); files that share a name
    (two folders) are labelled by as many trailing folders as tell them apart (``a/Spec.docx``, ``b/Spec.docx``) — by
    name alone one would shadow the other and every tool would read the wrong document. Keyed by ``str(path)``."""
    paths = [Path(f) for f in files]
    by_name: Dict[str, List[Path]] = {}
    for f in paths:
        group = by_name.setdefault(f.name, [])
        if all(str(g) != str(f) for g in group):        # the same path listed twice is one file
            group.append(f)
    out: Dict[str, str] = {}
    for name, group in by_name.items():
        if len(group) == 1:
            out[str(group[0])] = name
            continue
        depth = max(len(g.parts) for g in group)
        for k in range(2, depth + 1):
            labels = ["/".join(g.parts[-k:]) for g in group]
            if len(set(labels)) == len(labels):
                break
        else:
            labels = [str(g) for g in group]
        out.update({str(g): lab for g, lab in zip(group, labels)})
    return out


class _Batch:
    """The batch as the tools see it: files by label (``file_labels`` — the name, or the name with the folders that
    tell two same-named files apart), profiles by pattern (the pattern's ``files`` are those labels)."""

    def __init__(self, files: Sequence[Path], template: Optional[Path]):
        paths = [Path(f) for f in files]
        self.labels = file_labels(paths)                       # str(path) → label
        self.files = {self.labels[str(f)]: f for f in paths}   # label → path
        self.prof = profile_batch(paths)
        self._relabel(paths)
        self.template = Path(template) if template else None
        self._pkgs: Dict[str, DocxPackage] = {}
        self.work_dir: Optional[Path] = None      # where a conversation keeps its page renders (chat only)
        self.artifact_dir: Optional[Path] = None  # stable investigation root across recovery passes
        self.analysis_limits = None              # host-configured investigation.AnalysisLimits
        self.context_dir: Optional[Path] = None
        self.context_state = {}
        self.max_image_tokens: Optional[int] = None  # None uses vision's shared generous default
        self.renderer = "auto"
        self.inputs = input_files_near(paths, self.template)   # tables and pictures beside them (inputs.py)
        self.source_pins = [{"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                            for path in list(dict.fromkeys(paths + self.inputs + ([self.template] if self.template else [])))
                            if path.is_file()]

    def label(self, path: Path) -> str:
        """The label a batch file is shown and addressed by (its name when it is not one of the batch's files)."""
        return self.labels.get(str(path), Path(path).name)

    def _relabel(self, paths: List[Path]) -> None:
        """The profiles name files by ``Path.name`` (profile.profile_batch); Claude must see the labels its tools take,
        so every file named in them is renamed to its label: a pattern's ``files`` (from its ``paths``), the
        representative profile's ``file``, the ``unprofiled`` notes and the ``unreadable`` entries. A note that does
        not start with the expected file's name is left as it is."""
        def swap(note: Any, path: str) -> Any:
            name = Path(path).name
            if isinstance(note, str) and note.startswith(name + ": "):
                return self.labels.get(path, name) + note[len(name):]
            return note
        for pat in self.prof.get("patterns", []):
            ps = [str(x) for x in pat.get("paths") or []]
            if not ps:
                continue
            pat["files"] = [self.labels.get(x, Path(x).name) for x in ps]
            prof = pat.get("profile")
            if isinstance(prof, dict) and "error" not in prof and isinstance(prof.get("file"), str):
                prof["file"] = self.labels.get(ps[0], prof["file"])
            # profile_batch lists the files that would not profile in the pattern's order, before the representative
            # that did (moved to the front of ``paths``); with none that profiled, ``paths`` is that order itself
            offset = 0 if isinstance(prof, dict) and "error" in prof else 1
            if pat.get("unprofiled"):
                pat["unprofiled"] = [swap(n, ps[i + offset]) if i + offset < len(ps) else n
                                     for i, n in enumerate(pat["unprofiled"])]
            if isinstance(prof, dict) and "error" in prof and isinstance(prof.get("files"), list):
                prof["files"] = [swap(n, ps[i]) if i < len(ps) else n for i, n in enumerate(prof["files"])]
        if self.prof.get("unreadable"):
            profiled = {str(x) for pat in self.prof.get("patterns", []) for x in pat.get("paths") or []}
            rest = [str(f) for f in paths if str(f) not in profiled]
            for e, path in zip(self.prof["unreadable"], rest):
                if isinstance(e, dict) and e.get("file") == Path(path).name:
                    e["file"] = self.labels.get(path, e["file"])

    def pkg(self, name: str) -> DocxPackage:
        if name not in self.files:
            raise KeyError(f"no file {name!r} in this batch; files: {sorted(self.files)[:12]}")
        self.check_source_pin(self.files[name])
        if name not in self._pkgs:
            self._pkgs[name] = DocxPackage.from_path(self.files[name])
        return self._pkgs[name]

    def representatives(self) -> List[Path]:
        return [Path(p["paths"][0]) for p in self.prof["patterns"]]

    def pattern_paths(self, pat: Dict[str, Any]) -> List[Path]:
        """The files of one profiled pattern, as paths (the pattern's ``paths``; its ``files`` are labels)."""
        return [Path(x) for x in pat.get("paths") or [self.files[n] for n in pat["files"] if n in self.files]]

    def summary(self) -> Dict[str, Any]:
        return {"files": self.prof["files"],
                "patterns": [{"pattern_id": p["pattern_id"], "count": p["count"], "files": p["files"][:10],
                              "profile": p["profile"]} for p in self.prof["patterns"][:4]]
                + [{"pattern_id": p["pattern_id"], "count": p["count"], "files": p["files"][:5],
                    "profile": "(call inspect_profile)"} for p in self.prof["patterns"][4:]]}

    # ---- tools ----
    def check_source_pin(self, path):
        pin = next((p for p in self.source_pins if p["path"] == str(Path(path).resolve())), None)
        if pin and hashlib.sha256(Path(path).read_bytes()).hexdigest() != pin["sha256"]:
            raise ValueError("investigation source changed; start a new source-bound investigation")

    def read_context_archive(self, artifact_id=None, offset: int = 0, chars: int = 16000):
        from .contexts import read_context_archive
        return read_context_archive(self, artifact_id, offset, chars)

    def run_analysis(self, source: str, files=None, inputs=None):
        from .investigation import run_analysis
        return run_analysis(self, source, files, inputs)

    def read_analysis_result(self, artifact_id: str, offset: int = 0, chars: int = 16000):
        from .investigation import read_analysis_result
        return read_analysis_result(self, artifact_id, offset, chars)

    @staticmethod
    def _offset(name: str, value: int) -> int:
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
        return value

    @staticmethod
    def _page_size(name: str, value: int, maximum: int) -> int:
        positive_integer(name, value)
        if value > maximum:
            raise ValueError(f"{name} must not exceed {maximum}; continue with the returned cursor")
        return value

    def list_parts(self, file: str, start: int = 0, count: int = 50):
        pkg = self.pkg(file)
        self._offset("start", start)
        self._page_size("count", count, 200)
        members = pkg.members
        end = min(start + count, len(members))
        return {"file": file, "source_sha256": hashlib.sha256(pkg.data).hexdigest(), "start": start,
                "total": len(members), "next_start": end if end < len(members) else None,
                "parts": [{"name": m.filename, "bytes": m.file_size,
                           "xml": m.filename.lower().endswith((".xml", ".rels"))}
                          for m in members[start:end]]}

    def read_paragraphs(self, file: str, part: str = "", start: int = 0, text_offset: int = 0,
                        count: int = 20, chars: int = 12000, regex: Optional[str] = None,
                        style: Optional[str] = None):
        """A resumable view of every w:p; indices also address show_xml/read_xml_page.

        The cursor names the next unread paragraph and character, so one very long
        paragraph cannot hide the rest of itself or prevent later matches being read.
        """
        self._offset("start", start)
        self._offset("text_offset", text_offset)
        self._page_size("count", count, 200)
        self._page_size("chars", chars, 16000)
        pkg = self.pkg(file)
        part = part or pkg.main_part()
        pars = paragraphs(pkg.xml(part))
        sheet = StyleSheet(pkg)
        rx = re.compile(regex) if regex else None
        rows, remaining, cursor = [], chars, None
        for index in range(start, len(pars)):
            p = pars[index]
            ps = p.find(f"{W}pPr/{W}pStyle")
            sid = ps.get(W + "val") if ps is not None else sheet.defaults.get("paragraph")
            content = display_text(p)
            if (style and sid != style) or (rx and not rx.search(content)):
                continue
            begin = text_offset if index == start else 0
            if begin > len(content):
                raise ValueError(f"text_offset exceeds paragraph {index}'s {len(content)} characters")
            if len(rows) >= count or remaining <= 0:
                cursor = {"start": index, "text_offset": begin}
                break
            end = min(begin + remaining, len(content))
            rows.append({"index": index, "style": sid, "text": content[begin:end],
                         "text_offset": begin, "text_length": len(content),
                         "nested": nearest_p(p) is not None})
            remaining -= end - begin
            if end < len(content):
                cursor = {"start": index, "text_offset": end}
                break
        return {"file": file, "part": part, "source_sha256": hashlib.sha256(pkg.data).hexdigest(),
                "paragraphs_in_part": len(pars), "paragraphs": rows, "next": cursor}

    def read_xml_page(self, file: str, part: str = "", offset: int = 0, chars: int = 12000,
                      paragraph_index: Optional[int] = None):
        from lxml import etree
        self._offset("offset", offset)
        self._page_size("chars", chars, 16000)
        pkg = self.pkg(file)
        part = part or pkg.main_part()
        root = pkg.xml(part)  # package members only; the existing parser never resolves external entities
        if paragraph_index is not None:
            self._offset("paragraph_index", paragraph_index)
            pars = paragraphs(root)
            if paragraph_index >= len(pars):
                raise ValueError(f"paragraph_index must be below {len(pars)}")
            root = pars[paragraph_index]
        content = etree.tostring(root, encoding="unicode")
        if offset > len(content):
            raise ValueError(f"offset exceeds this XML view's {len(content)} characters")
        end = min(offset + chars, len(content))
        return {"file": file, "part": part, "source_sha256": hashlib.sha256(pkg.data).hexdigest(),
                "paragraph_index": paragraph_index, "offset": offset, "total_chars": len(content),
                "next_offset": end if end < len(content) else None, "xml": content[offset:end]}

    def inspect_profile(self, pattern_id: str):
        for p in self.prof["patterns"]:
            if p["pattern_id"] == pattern_id:
                return p
        raise KeyError(f"no pattern {pattern_id!r}")

    def list_styles(self, file: str):
        sheet = StyleSheet(self.pkg(file))
        out = []
        for sid, st in sheet.styles.items():
            e = {"id": sid, "type": st.get(W + "type", "paragraph")}
            for tag in ("name", "basedOn"):
                el = st.find(W + tag)
                if el is not None:
                    e[tag] = el.get(W + "val")
            rf, sz = st.find(f"{W}rPr/{W}rFonts"), st.find(f"{W}rPr/{W}sz")
            if rf is not None:
                e["rFonts"] = {k.split("}")[1]: v for k, v in rf.attrib.items()}
            if sz is not None:
                e["size_pt"] = int(sz.get(W + "val")) / 2
            out.append(e)
        return out[:200]

    def sample_paragraphs(self, file: str, style: Optional[str] = None, regex: Optional[str] = None, n: int = 12):
        pkg = self.pkg(file)
        sheet = StyleSheet(pkg)
        rx = re.compile(regex) if regex else None
        out = []
        for i, p in enumerate(paragraphs(pkg.xml(pkg.main_part()))):
            if nearest_p(p) is not None:
                continue
            ps = p.find(f"{W}pPr/{W}pStyle")
            sid = ps.get(W + "val") if ps is not None else sheet.defaults.get("paragraph")
            t = display_text(p)
            if style and sid != style:
                continue
            if rx and not rx.search(t):
                continue
            out.append({"index": i, "style": sid, "text": t[:200]})
            if len(out) >= min(int(n or 12), 40):
                break
        return out

    def show_part_text(self, file: str, part: str):
        pkg = self.pkg(file)
        if not pkg.has(part):
            raise KeyError(f"{file} has no part {part!r}; text parts: {pkg.text_parts()}")
        return [display_text(p)[:200] for p in paragraphs(pkg.xml(part))][:80]

    def show_xml(self, file: str, part: str = "", start: int = 0, count: int = 10):
        """The transform writer's eyes (never the compiler's): pretty-printed XML of paragraphs [start, start+count) of
        one part — every w:p of the part in document order. Capped at 20 paragraphs / 30,000 characters."""
        from lxml import etree
        pkg = self.pkg(file)
        part = part or pkg.main_part()
        if not pkg.has(part):
            raise KeyError(f"{file} has no part {part!r}; text parts: {pkg.text_parts()}")
        pars = paragraphs(pkg.xml(part))
        start, count = max(0, int(start or 0)), min(max(1, int(count or 10)), 20)
        xml = "\n".join(f"<!-- paragraph {i} -->\n" + etree.tostring(p, pretty_print=True, encoding="unicode")
                        for i, p in enumerate(pars[start:start + count], start))
        return {"file": file, "part": part, "paragraphs_in_part": len(pars), "start": start, "xml": xml[:30000]}

    def find_paragraphs(self, file: str, regex: str, part: str = ""):
        pkg = self.pkg(file)
        part = part or pkg.main_part()
        if not pkg.has(part):
            raise KeyError(f"{file} has no part {part!r}; text parts: {pkg.text_parts()}")
        sheet = StyleSheet(pkg)
        rx = re.compile(regex)
        out = []
        for i, p in enumerate(paragraphs(pkg.xml(part))):
            t = display_text(p)
            if rx.search(t):
                ps = p.find(f"{W}pPr/{W}pStyle")
                out.append({"index": i, "style": ps.get(W + "val") if ps is not None else sheet.defaults.get("paragraph"),
                            "text": t[:200]})
                if len(out) >= 40:
                    break
        return out

    def view_pages(self, file: str, pages: Optional[Sequence[int]] = None):
        """Rendered pages as image blocks (the conversation sends them as the tool result). Budgeted per conversation."""
        from . import vision
        if self.work_dir is None:
            raise KeyError("page images are not available here")
        if file == "template":
            if not self.template:
                raise KeyError("no template was attached to this request")
            path = self.template
        else:
            if file not in self.files:
                raise KeyError(f"no file {file!r} in this batch; files: {sorted(self.files)[:12]}")
            path = self.files[file]
        wanted = sorted({int(n) for n in (pages or [1]) if int(n) >= 1})[:3] or [1]
        render_options = {"renderer": self.renderer} if self.renderer != "auto" else {}
        pngs = vision.page_pngs(path, wanted, Path(self.work_dir) / "pages", **render_options)
        if not pngs:
            raise ValueError(f"{file} has no page {wanted}")
        cost = sum(vision.est_tokens(p) for p in pngs)
        used = getattr(self, "_image_tokens", 0)
        allowance = self.max_image_tokens if self.max_image_tokens is not None else vision.VIEW_BUDGET_TOKENS
        if used + cost > allowance:
            raise ValueError(f"the page budget of this conversation is used up ({used} of "
                             f"{allowance} image tokens); {cost} more are needed for these pages. "
                             "Continue from the collected evidence or increase max_image_tokens to inspect more pages.")
        self._image_tokens = used + cost
        blocks: List[Dict[str, Any]] = []
        for png in pngs:
            blocks += [{"type": "text", "text": f"{file} — page {png.stem[1:]}"}, vision.image_block(png)]
        return {"_content_blocks": blocks, "_images": pngs}

    def read_template(self):
        if not self.template:
            raise KeyError("no template was attached to this request")
        pkg = DocxPackage.from_path(self.template)
        return {"path": str(self.template), "template_spec": template_spec_summary(pkg), "profile": profile(pkg)}

    def read_input_file(self, path: str, sheet: Optional[str] = None, rows: int = 40):
        """A table or picture listed beside the documents — only a listed one (inputs.read)."""
        return read_input(path, self.inputs, sheet, rows)

    def list_library(self):
        """The conversation's tool (chat only): the whole library, within the tool-result budget."""
        return library_index(limit=None)

    def read_library_item(self, name: str):
        """The conversation's tool (chat only): one item in full, a recipe dry-planned over this batch."""
        return library_item_detail(name, self)


def _cut(text: Any, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def library_entry(item: Any) -> Dict[str, Any]:
    """One library item as the index shows it to Claude: what it is and where it came from — never the words a recipe
    places (read_library_item shows those beside the rule that only the user's pick approves them, and the choice
    shows them to the user). An item that cannot be read is listed with its problem, never hidden."""
    from .editspec import load_spec, spec_input_files, spec_templates
    from .library import read_manifest, read_snippet, recipe_file
    e: Dict[str, Any] = {"kind": item.kind, "name": item.name, "description": _cut(item.description, DESCRIPTION_CHARS),
                         "saved": item.saved_at, "request": _cut(item.request, REQUEST_CHARS)}
    try:
        if item.kind == "snippet":
            sn = read_snippet(item)                       # never its words here (read_library_item, under the rule)
            e.update({"characters": len(sn["text"]), "style": sn["style"], "formatting": sn["formatting"]})
        elif item.kind == "recipe":
            spec = load_spec(recipe_file(item))           # every read goes through the library's containment check
            ops = [str(o.get("op")) for o in spec.get("ops") or [] if isinstance(o, dict)]
            e["ops"] = ops[:ENTRY_OPS] + ([f"… and {len(ops) - ENTRY_OPS} more"] if len(ops) > ENTRY_OPS else [])
            e["mode"] = spec.get("mode")
            e["places_words"] = len(placed_words(spec))
            e["templates"] = [{"path": str(t), "exists": t.is_file()} for t in spec_templates(spec)]
            others = [t for t in spec_input_files(spec) if t not in spec_templates(spec)]
            if others:
                e["input_files"] = [{"path": str(t), "exists": t.is_file()} for t in others]
        elif item.kind == "chain":
            from .library import load_chain
            chain = load_chain(item.name)
            e["stages"] = [str(st.get("name") or "") for st in chain.get("stages") or []]
            e["note"] = "a chain of recipes, run from the window or the command line; it cannot be offered as a choice"
        else:
            manifest = read_manifest(item)
            e["op"] = manifest.get("op_name")
            e["proposal"] = _cut(manifest.get("proposal"), DESCRIPTION_CHARS)
    except Exception as ex:  # noqa: BLE001 — a broken item is said, and never stops the index
        e["error"] = f"cannot be read ({type(ex).__name__}: {ex})"
    return e


def library_index(limit: Optional[int] = INDEX_LIMIT, budget: int = INDEX_BUDGET) -> Dict[str, Any]:
    """The user's library for Claude: ``items`` (``library_entry`` each, at most ``limit``, and within ``budget``
    characters of JSON) and ``more`` (how many were left out). The conversation's first message carries it (chat
    mode); ``list_library`` is the same without the item limit."""
    from .library import list_items
    items = [i for i in list_items() if i.kind in OFFERABLE_KINDS]   # a chain is never a choice: not shown
    shown: List[Dict[str, Any]] = []
    size = 2                                       # the list's brackets
    for item in (items if limit is None else items[:limit]):
        e = library_entry(item)
        n = len(json.dumps(e, ensure_ascii=False)) + 2
        if size + n > budget:                      # an entry that does not fit is left out; the ones after it still show
            continue
        shown.append(e)
        size += n
    return {"items": shown, "more": len(items) - len(shown)}


def dry_plan_summary(spec: Dict[str, Any], batch: "_Batch") -> Dict[str, Any]:
    """How a spec plans on this batch, for read_library_item: the gate's verdict (``_dry_errors``) and, per structure
    pattern, 'plans' or the refusal of the last file tried — the pattern's files are tried in order until one plans,
    as the gate does."""
    per = []
    for pat in batch.prof["patterns"]:
        verdict = "no file could be tried"
        for f in batch.pattern_paths(pat):
            try:
                pkg = DocxPackage.from_path(f)
                plan_spec(pkg, spec, fingerprint(pkg)["pattern_id"])
                verdict = "plans"
                break
            except JobException as e:
                verdict = f"{batch.label(f)}: [{e.code}] {e.reason}"
            except Exception as e:  # noqa: BLE001 — an unreadable file is said as such
                verdict = f"{batch.label(f)}: cannot be read ({type(e).__name__}: {e})"
        per.append({"pattern_id": pat["pattern_id"], "files": pat["count"], "result": verdict})
    errs = _dry_errors(spec, batch)
    return {"accepted": not errs, "errors": errs, "patterns": per}


def library_item_detail(name: str, batch: Optional["_Batch"] = None) -> Dict[str, Any]:
    """read_library_item: the item in full. A recipe: the spec exactly as saved, the words it places under
    ``PLACED_WORDS_RULE``, its templates with whether each exists, and (with a batch) ``dry_plan_summary``. An
    operation: its manifest without ``op.py`` — Claude has nothing to do with the code — the brief cut at
    ``BRIEF_CHARS``. A snippet: its text under ``SNIPPET_WORDS_RULE``, style and formatting. A name that is not in the
    library, a recipe that does not load or validate, a snippet that is not one plain paragraph: the tool's error."""
    from .editspec import load_spec, spec_input_files, spec_templates, validate_spec
    from .library import find, read_manifest, read_snippet, recipe_file
    item = find(name)
    out: Dict[str, Any] = {"kind": item.kind, "name": item.name, "description": item.description,
                           "saved": item.saved_at, "request": item.request}
    if item.kind == "snippet":
        sn = read_snippet(item)
        out.update({"text": sn["text"], "text_rule": SNIPPET_WORDS_RULE, "style": sn["style"],
                    "formatting": sn["formatting"], "source": sn["source"]})
        return _fit_detail(out, DETAIL_BUDGET)
    if item.kind == "recipe":
        spec = load_spec(recipe_file(item))           # every read goes through the library's containment check
        validate_spec(spec)
        out["spec"] = spec
        out["placed_words"] = placed_words(spec)
        out["placed_words_rule"] = PLACED_WORDS_RULE
        out["templates"] = [{"path": str(t), "exists": t.is_file()} for t in spec_templates(spec)]
        others = [t for t in spec_input_files(spec) if t not in spec_templates(spec)]
        if others:
            out["input_files"] = [{"path": str(t), "exists": t.is_file()} for t in others]
        if batch is not None:
            out["dry_plan"] = dry_plan_summary(spec, batch)
        return _fit_detail(out, DETAIL_BUDGET)
    if item.kind == "chain":
        from .library import load_chain
        chain = load_chain(item.name)
        out["stages"] = [{"stage": i, "name": st.get("name"), "recipe": st.get("recipe"),
                          "request": (st.get("spec") or {}).get("request"), "summary": (st.get("spec") or {}).get("summary"),
                          "ops": [str(o.get("op")) for o in (st.get("spec") or {}).get("ops") or [] if isinstance(o, dict)]}
                         for i, st in enumerate(chain.get("stages") or [], 1)]
        out["note"] = ("a chain of recipes, run one after another from the window (Chain recipes…) or the command line "
                       "(spec-surgeon chain); it cannot be offered as a choice — offer its recipes one at a time")
        return _fit_detail(out, DETAIL_BUDGET)
    manifest = read_manifest(item)
    out.update({k: manifest.get(k) for k in ("op_name", "proposal", "select", "params", "model", "attempts")})
    out["brief"] = _cut(manifest.get("brief"), BRIEF_CHARS)
    return _fit_detail(out, DETAIL_BUDGET)


def _fit_detail(out: Dict[str, Any], budget: int) -> Dict[str, Any]:
    """A read_library_item result that stays valid JSON within ``budget`` characters (``Conversation._dispatch`` cuts a
    longer result as a string, mid-JSON). What Claude needs to offer the item — the words it places, its templates,
    the dry plan — stays; the spec as saved gives way first (to an outline of its ops), then over-long words are cut
    and said to be (the choice shows them whole), then the outline and the brief."""
    def size() -> int:
        return len(json.dumps(out, ensure_ascii=False))
    if size() <= budget:
        return out
    if isinstance(out.get("spec"), dict):
        spec = out["spec"]
        out["spec"] = {"left_out": f"the spec as saved is {len(json.dumps(spec, ensure_ascii=False))} characters — too "
                                   f"long for this tool; this is its outline", "mode": spec.get("mode"),
                       "ops": [{"op": o.get("op"), "select": o.get("select"),
                                "params": sorted((o.get("params") or {}).keys()) if isinstance(o.get("params"), dict)
                                else []} for o in spec.get("ops") or [] if isinstance(o, dict)]}
        if size() <= budget:
            return out
    words = out.get("placed_words")
    if isinstance(words, list):
        out["placed_words"] = [w if not isinstance(w, str) or len(w) <= 200 else w[:200] + f"… ({len(w)} characters)"
                               for w in words]
        out["placed_words_cut"] = "long words are shown cut here; the choice the user sees shows them whole"
        if size() <= budget:
            return out
    if isinstance(out.get("spec"), dict):
        out["spec"]["ops"] = f"{len(out['spec'].get('ops') or [])} ops (left out)"
    if "brief" in out:
        out["brief"] = _cut(out["brief"], 500)
    return out


def _dry_errors(spec: Dict[str, Any], batch: _Batch) -> List[str]:
    """Spec-level failures of a dry plan on one file per pattern.

    True schema/contract mistakes (FATAL_SPEC_CODES) always reject the spec. Every other refusal is treated as
    pattern-sensitive (a particular file's structure caused it) and rejects the spec ONLY when zero patterns in
    the batch plan cleanly — in a mixed batch where at least one pattern plans cleanly, minority pattern refusals
    are left for the preview and set-aside sieve rather than blocking Pass 1. A spec that plans on no file at all
    is rejected with every file's refusal, so the model can fix the spec instead of the batch setting every file
    aside; a batch with no files is rejected outright.
    """
    if not batch.representatives():
        return ["the batch has no .docx files to plan on"]
    fatal_errs: List[str] = []
    pattern_errs: List[str] = []
    planned_ok = 0
    for pat in batch.prof["patterns"]:
        # The representative is one file of the pattern; a refusal that depends on the file's content (the phrase
        # is in the second file, not the first) must not reject a spec that plans on another file of the same
        # structure — try the pattern's other files before saying no file of it plans.
        tried: List[str] = []
        planned = False
        for f in batch.pattern_paths(pat):
            try:
                pkg = DocxPackage.from_path(f)
                plan_spec(pkg, spec, fingerprint(pkg)["pattern_id"])
                planned = True
                break
            except SpecError as e:
                return [f"[{e.code}] {e.reason}"]
            except JobException as e:
                if e.code in FATAL_SPEC_CODES:
                    fatal_errs.append(f"{batch.label(f)}: [{e.code}] {e.reason}")
                    break
                tried.append(f"{batch.label(f)}: [{e.code}] {e.reason}")
            except Exception as e:  # noqa: BLE001 — an unreadable file is not the spec's fault; try the next one
                tried.append(f"{batch.label(f)}: cannot be read ({type(e).__name__}: {e})")
        if planned:
            planned_ok += 1
        elif tried:
            pattern_errs.append(tried[0] + (f" (none of this pattern's {len(tried)} files plans; e.g. {tried[1]})"
                                            if len(tried) > 1 else ""))
    if fatal_errs:
        return fatal_errs
    if planned_ok == 0:
        return ["the spec plans cleanly on none of the batch's structure patterns:"] + pattern_errs
    return []


def check_submission(spec: Any, batch: _Batch, sources: Sequence[str]) -> List[str]:
    """[] when the spec is acceptable (or is a clean question/unsupported outcome)."""
    if not isinstance(spec, dict):
        return ["the spec must be a JSON object"]
    if spec.get("questions") or spec.get("unsupported"):
        return []
    try:
        validate_spec(spec)
    except SpecError as e:
        return [f"[{e.code}] {e.reason}"]
    bad = untraceable(spec, sources)
    if bad:
        return [f"these words would be placed in the documents but the user never gave them verbatim: {bad}. Use "
                f"the user's exact words, or call ask_user for them."]
    return _dry_errors(spec, batch)


def build_messages(request: str, batch: _Batch, answers: Sequence[str], memory: Dict[str, Any],
                   prior_failures: Optional[Sequence[Dict[str, Any]]] = None,
                   library: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """The first user message. ``library`` (``library_index()``; the conversation passes it in chat mode only) adds
    YOUR LIBRARY before the batch — an index, never the words a recipe places."""
    parts = [f"REQUEST:\n{request}"]
    if answers:
        parts.append("THE USER'S ANSWERS TO EARLIER QUESTIONS:\n" + "\n".join(f"- {a}" for a in answers))
    if memory:
        parts.append("PROJECT MEMORY (PROJECT.json):\n" + json.dumps({k: v for k, v in memory.items() if k != "_path"},
                                                                     ensure_ascii=False))
    if prior_failures:
        parts.append("PRIOR PASS SET-ASIDE DIAGNOSTICS (these files failed the previous pass for the reasons below; "
                     "inspect their structure and compile an EditSpec tailored to them):\n"
                     + json.dumps(list(prior_failures)[:20], ensure_ascii=False))
    if batch.template:
        parts.append(f"A TEMPLATE FILE IS ATTACHED: {batch.template} — a prepared header/footer template, a "
                     f"template spec (e.g. the architect's) or a file holding the user's own styles (apply_style, "
                     f"map_styles); call read_template to see which (it lists the template's styles), and put this exact "
                     f"path in the op's 'template' param")
    if batch.inputs:
        parts.append("INPUT FILES beside the documents — tables and pictures an op can read (read_input_file shows one; "
                     "put the exact path in the op's params):\n"
                     + json.dumps([input_overview(p) for p in batch.inputs], ensure_ascii=False))
    if library and library.get("items"):
        parts.append("YOUR LIBRARY — the user's saved recipes (specs they previewed before), operations (custom "
                     "transforms that passed) and snippets (saved paragraphs). When one does what the user asks, read "
                     "it (read_library_item) and offer it as a choice {\"kind\": \"recipe\" | \"operation\" | "
                     "\"snippet\", \"name\": <its exact name>}:\n"
                     + json.dumps(library["items"], ensure_ascii=False)
                     + (f"\n… and {library['more']} more — call list_library" if library.get("more") else ""))
    parts.append("THE BATCH (profiles per structure pattern):\n" + json.dumps(batch.summary(), ensure_ascii=False))
    return [{"role": "user", "content": "\n\n".join(parts)}]


def compile_request(request: str, files: Sequence[Path], template: Optional[Path] = None,
                    answers: Sequence[str] = (), model: Optional[str] = None, client=None,
                    memory: Optional[Dict[str, Any]] = None, log: Optional[Callable[[str], None]] = None,
                    prior_failures: Optional[Sequence[Dict[str, Any]]] = None,
                    max_calls: Optional[int] = None, max_attempts: Optional[int] = None,
                    max_output_tokens: Optional[int] = None, effort: Optional[str] = None,
                    max_context_tokens: Optional[int] = None, analysis_limits=None) -> CompileResult:
    """Request + batch → CompileResult, in one go (the command line without a terminal, the tests). ``status`` is
    ``ok`` (a validated, dry-planned spec), ``questions`` (ask the user, then call again with ``answers``),
    ``unsupported`` (no op fits; see spec["proposal"]) or ``failed``. The loop itself is conversation.Conversation."""
    from .conversation import Conversation
    policy = {key: value for key, value in (("max_calls", max_calls), ("max_attempts", max_attempts),
                                           ("max_output_tokens", max_output_tokens), ("effort", effort),
                                           ("max_context_tokens", max_context_tokens), ("analysis_limits", analysis_limits))
              if value is not None}
    conv = Conversation(request, files, template=template, client=client, model=model, memory=memory,
                        prior_failures=prior_failures, answers=answers, mode="compile", log=log, **policy)
    return conv.compile_result(conv.start())
