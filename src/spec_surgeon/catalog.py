"""The op catalog and the EditSpec JSON schema — generated from the op classes, so the compiler, the GUI and a
human writing a spec by hand all read the same contract (docs/history/NL_SURGICAL_EDITOR_PLAN.md §6, Appendix A;
Phase 3). ``spec-surgeon ops --catalog`` prints it as JSON.

The schema is documentation and compiler material; ``editspec.validate_spec`` remains the authority (it also
checks things a JSON schema cannot: mode vs ops, expect vs plan, geometry reference vs ops).
"""
from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any, Dict, List

from .editspec import EXPECT_CHECKS, MODES, SPEC_ASSERTION_KINDS
from .ops import OPS

# The GUI needs a concrete directory; wheels and frozen builds both extract these
# package resources beside this module. The compiler reads the same resources below.
EXAMPLES_DIR = Path(__file__).resolve().parent / "examples" / "specs"

# What a compiled spec usually has to state about each op (the user sees these in the preview and can veto them).
# Request-only operations: accepting/rejecting tracked changes, editing
# the document properties, changing the Track Changes setting / Mark as Final — never on a model's own initiative, only
# when the user asks for exactly that. The advisor never adds one to a respec (advisor._respec_errors).
REQUEST_ONLY_OPS = ("resolve_tracked_changes", "set_document_properties", "set_document_state", "add_review_comments")

COMMON_ASSUMPTIONS: Dict[str, List[str]] = {
    "normalize_whitespace": ["ordinary spaces only; tabs, line breaks and non-breaking spaces stay", "tracked unless clean copies requested"],
    "normalize_punctuation": ["choose the quote, dash and section-number styles explicitly; numeric inch/foot marks are retained"],
    "protect_nonbreaking_spaces": ["default units: psi; spaced section numbers included; no words change"],
    "capitalize_defined_terms": ["one source-bound user pick per occurrence; a broad request authorizes no occurrence"],
    "remove_header_footer": [
        "'all' means every section, including first-page and even-page variants",
        "page margins stay as they are (the header/footer space collapses)",
        "pending tracked changes inside removed headers/footers are discarded and flagged by author",
    ],
    "set_font": [
        "Symbol/Wingdings runs (bullets, special characters) are left alone — changing them changes the glyphs",
        "font shorthand changes ascii/hAnsi/cs; eastAsia stays unless east_asia:true or that slot is explicitly requested",
        "used body list-level fonts follow whole-document selections by default; numbering_levels:false keeps them, "
        "numbering_levels:true opts in for a targeted selection; symbol/picture labels and label sizes stay",
        "font size is unchanged unless a size was asked; a size goes only on runs that set their own size (the rest "
        "follows its styles), and with size_from only on the listed sizes — e.g. 9 pt text becomes 10 pt while 8 pt "
        "headers and a 12 pt note keep their size; the complex-script size is left alone",
        "runs inside pending tracked changes are included unless 'leave revisions alone' was asked",
    ],
    "set_style_font": ["only styles that already name a font (or set a size) change; the others inherit it",
                       "styles all also updates used body list-level fonts; numbering_levels:false opts out"],
    "set_doc_defaults_font": ["the document default font (and size) changes, so inheriting text follows; with size_from "
                              "its size changes only when it is one of those sizes"],
    "set_paragraph_props": [
        "values merge into existing spacing/indents; unspecified attributes stay",
        "with select.styles the values go into the paragraph style definitions instead (never tracked): every paragraph "
        "of that style follows unless it sets its own value (flagged); a style already as asked is left alone; spacing "
        "the style (or a style it is based on, or the document defaults) gives in lines, or as 'auto', sets the file "
        "aside rather than being silently ignored by Word",
    ],
    "set_borders_shading": [
        "the asked borders are the paragraph's whole border box: a side not named has no border, so a side its style "
        "shows is cancelled with a 'nil' side (the style keeps its definition); 'none' removes every border (or the "
        "shading)",
        "border size is in eighths of a point (4 = ½ pt, Word's default; 2–96), the space between border and text in "
        "points (1 by default); colours are 6 hex digits or 'auto'",
        "a paragraph or style that already looks as asked is left alone (no change, no tracked-change marker)",
        "direct formatting on the selected paragraphs by default (tracked when they are targeted, so the reviewer sees "
        "'Formatted: Border'); with select.styles the paragraph style definitions change instead (never tracked) — "
        "paragraphs with their own direct borders/shading and styles based on it that define their own keep theirs "
        "(flagged)",
    ],
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
        "text chosen by its colour (runs color_in) is every run whose colour — set directly or by its style — is one of "
        "the listed colours (hex or a theme colour, as the profiles show them); other colours, hyperlinks included, stay; "
        "'black' is Word's Automatic colour (auto) unless an explicit black was asked",
        "'each PART on a new page' puts a page break before every PART heading after the first, so PART 1 stays under "
        "the section title; widow/orphan control is on by default in Word, so turning it on changes only text that "
        "turned it off",
    ],
    "set_tab_stops": [
        "positions are measured from the left margin (1440 twips = 1 inch); 'right_margin' and 'center' are each file's "
        "own text width (and half of it), measured per section — a header or footer shown by sections with different "
        "margins sets the file aside",
        "a paragraph that already has the asked stops is left alone; the paragraph's own stops and its style's both "
        "count, and a stop inherited from the style is cleared with a clear entry, as Word does",
        "clear_others leaves exactly the asked stops; on a numbered paragraph the list definition's own tab (after the "
        "number) is not read and stays (flagged)",
        "direct formatting on the selected paragraphs, tracked when they are targeted; the style definitions are not "
        "changed",
    ],
    "clear_direct_formatting": [
        "clearing direct formatting makes the text follow its styles again: fonts, sizes, bold/italic/underline, colour, "
        "caps and the like go from the selected runs and paragraph marks; spacing, indents, alignment, borders, shading, "
        "tab stops and keep/page-break flags from the selected paragraphs — the styles themselves are not changed",
        "hidden text stays hidden, list numbers (numPr), character and paragraph styles and the proofing language are "
        "never touched; highlight, superscript/subscript (vertAlign, position) and outline levels stay unless asked "
        "('also')",
        "symbol and Wingdings runs (and w:sym runs) keep their font and caps — their glyphs depend on them; their other "
        "direct formatting is cleared",
        "'keep' and 'also' take element names: bold is b and bCs, italic i and iCs, size sz and szCs — name both twins "
        "to keep bold, italic or the size; shd and spacing name the run's and the paragraph's alike",
        "a run or paragraph with nothing to clear is left alone; plain for the whole document, tracked (Formatted: …) "
        "when particular paragraphs are targeted",
    ],
    "set_language": [
        "the proofing language changes on every style, run and paragraph mark that names a language, and on the "
        "document default; text that names none inherits the default — nothing else changes and no text is retyped",
        "replace_only changes only the listed languages (e.g. British English to US English, French quotations stay "
        "French); without it every named language becomes the asked one",
        "language is the Latin-text language (w:val); east_asian and complex_script set the East Asian and "
        "complex-script languages only when asked",
        "never tracked (Word does not track a language change); beside another operation that changes run properties "
        "or styles it runs as its own ordered step",
    ],
    "map_styles": [
        "every use of the style changes: paragraphs whose style it is (with the default paragraph style — Normal — also "
        "the paragraphs that name no style), runs and paragraph marks in a character style, tables in a table style, in "
        "the body, headers, footers, footnotes, endnotes and comments unless a scope is given",
        "your styles come from the template file: a style this file lacks is imported with the styles it is based on, "
        "linked to and followed by, and its list numbering (copied with new list ids); a style this file already has "
        "keeps this file's definition unless existing is 'replace'",
        "nothing else changes: the architect's style definitions stay (removing unused styles is its own operation, a "
        "second stage), and field codes that name a style (a TOC \\t switch, STYLEREF) are flagged, not edited",
        "a style the file lacks, or that nothing uses, is skipped and noted (allow_missing); a 'to' of another type, or a "
        "name two styles share, sets the file aside",
        "plain formatting (a whole-document change); tracked on request — one Formatted: Style revision per paragraph, "
        "run or mark (a table's style cannot be tracked)",
    ],
    "apply_style": [
        "the paragraphs are chosen by how they look (bold, all caps), by a text pattern, by what comes before them or by "
        "their current style — the selection rules say which; a paragraph that already has the style is left alone",
        "the style is named by its id or name as the profiles show it; a style this file lacks comes from the template "
        "file with the styles it is based on, links to and is followed by, and its numbering (existing 'replace' takes "
        "the template's definition even where this file has one)",
        "direct_formatting 'keep' (the default) leaves the paragraphs' own formatting, which wins over the style; "
        "'clear' removes it by clear_direct_formatting's default rules (hidden text, list numbers, superscripts, "
        "highlight and symbol fonts stay) so the paragraphs look exactly like the style",
        "tracked (Formatted: Style) when particular paragraphs are chosen, plain for the whole document; a paragraph "
        "numbered by its own list keeps that numbering (flagged)",
        "direct_formatting 'replace_style_owned' removes only eligible direct properties supplied by the destination "
        "style and its basedOn chain; unrelated properties and unsupported partial overrides are preserved. "
        "The preview names retained overrides; symbol fonts, hidden text and superscripts stay protected",
    ],
    "add_review_comments": [
        "adds exactly the user's approved text and optional literal source as Word review comments; never invents "
        "a reason or inserts comments automatically",
        "requires an explicit author and paragraph selection; anchors are outside tracked-change wrappers. "
        "Ambiguous overlapping comment ranges and unsupported structures are refused",
        "existing comments, document wording and revisions are preserved; every new comment, anchor, relationship "
        "and package part is predicted and independently checked",
        "comment additions are an untracked annotation, not acceptance of the document's tracked revisions",
    ],
    "remove_unused_styles": [
        "a style is used when a paragraph, run or table names it (body, headers, footers, footnotes, endnotes, comments — "
        "pending tracked changes included, a reject-all would bring them back), when the numbering or the settings name "
        "it, when a field instruction names it (STYLEREF, TOC \\t — by id, name or alias, any case), or when it is a "
        "default style",
        "a style that a staying style is based on, followed by (next) or linked to stays too",
        "a TOC, INDEX or TOA field keeps the TOC 1–9 / Table of Figures, Index 1–9 / Index Heading, Table of Authorities / "
        "TOA Heading styles a field update would apply",
        "which: all removes unused Word built-in styles too (Word brings back its own definition when one is used later); "
        "which: custom removes only the document's own (customStyle) styles — say which in the assumptions",
        "'keep' names styles never removed (ids or names, any case); styles the user mentions as theirs go there",
        "used is judged on the file before this spec: after mapping or applying styles, remove unused styles as a chain's "
        "last stage",
        "styles.xml only, never tracked; a file with nothing unused is left as it is",
    ],
    # built-in batch 5 (tables, docs/BUILTIN_OPS_BATCH5_DESIGN.md §5)
    "set_table_props": [
        "the tables are every table of the body unless a selection narrows them (by position, table style, the first "
        "row's text, any text in the table, nested or not); tables in headers and footers only when asked",
        "the asked borders are the table's whole border set: a side not named has no border, so a side its table style "
        "shows is cancelled with a 'nil' side (the style keeps its definition); 'none' removes every border; size in "
        "eighths of a point (4 = ½ pt), space 0",
        "cell margins are in twips (1440 = 1 inch) and merge into the table's own (sides not named stay); width is a "
        "percentage of the text width, twips or auto",
        "repeat_header_rows n makes the first n rows repeat at the top of each page and no later row (a table with "
        "fewer rows is left alone, flagged; Word repeats header rows only for tables in the text flow, not floating or "
        "nested ones — flagged); cant_split keeps every row on one page (true) or lets rows break (false)",
        "cells with their own borders or margins keep them (flagged) unless clear_cell_overrides, which takes only the "
        "cell sides that compete with what is asked (never a cell's diagonal lines, never a margin side not asked); a "
        "table already as asked is left alone, a table in a text box too (flagged); a right-to-left table is set aside "
        "for borders and margins; never tracked",
    ],
    "apply_table_style": [
        "every table of the body gets the style unless a selection narrows them; a table already in the style is left "
        "alone",
        "the style is named by its id or name as the profiles show it; a style this file lacks comes from the template "
        "file with the styles it is based on (existing 'replace' takes the template's definition even where this file "
        "has one)",
        "direct_formatting 'keep' (the default) leaves the tables' own borders, shading and cell margins, which win over "
        "the style; 'clear' removes them, the rows' own exceptions and the cells' own, so the style shows; which "
        "conditional parts show (first row, banding — w:tblLook) stays as it is",
        "the template's style comes in only when a selected table takes it: a selection that restyles nothing changes "
        "no style definition (existing 'replace' included)",
        "a table in a text box is left alone (flagged); never tracked",
    ],
    # built-in batch 5 (list levels and the audit, docs/BUILTIN_OPS_BATCH5_DESIGN.md §3, §4)
    "set_list_level": [
        "the level is the one the named paragraph style numbers with (level names another level of the same list); "
        "the change is made in the list's definition, so every paragraph at that level follows — in every list that "
        "shares the definition (flagged)",
        "zero padding: true turns 1, 2 into 01, 02 (decimalZero) and '1.1' into '1.01'; false the reverse; a level "
        "numbered by letters or roman numerals is left alone",
        "'restart per PART' is restart 'after_higher' (a level restarts after any higher level — usually already so); "
        "when the PART headings are typed or numbered by another list the restart changes nothing (flagged): convert "
        "the numbering first",
        "a start or restart is refused on a list whose definition several lists share (Word and LibreOffice count them "
        "differently); LibreOffice ignores restart rules, so the proof PDFs may differ from Word (flagged)",
        "never tracked; a file whose style is missing or not numbered is left as it is and noted",
    ],
    "audit_numbering": [
        "report only: the findings are flagged for review and the file is not changed",
        "the main document only; numbers are those the engine predicts as Word shows them — a list it cannot predict "
        "is reported as not evaluated, with the reason",
        "typed numbers (PART 1, 1.1, A., 1., a., (a), II. …) at the start of unnumbered paragraphs outside tables are "
        "checked for gaps, duplicates and numbers in the wrong PART, with the main list's automatic numbers as their "
        "context; automatic numbers themselves are the engine's count and are not judged",
        "unnumber_empty turns the number off on each empty numbered paragraph (the paragraph and its spacing stay); the "
        "later numbers of its list move up (predicted and flagged); plain unless tracked",
    ],
    # built-in batch 5 (numbering conversions, docs/BUILTIN_OPS_BATCH5_DESIGN.md §1, §2)
    "convert_numbering": [
        "your scheme's levels are the numbered paragraph styles of your example file (level 0 first, as read_template "
        "shows them); a style this file lacks is imported with its list (new list ids) — the numbering text such as "
        "'PART %1 -' is always yours, never written by the engine",
        "the paragraphs converted are those of the list numbering the most paragraphs here, each at its own level "
        "(from 'main'), or the paragraphs of named styles per level (from.styles); level_map when the levels differ",
        "each converted paragraph gets its level's style and loses its own list numbering; its own indents and fonts "
        "stay unless direct_formatting is 'clear'",
        "every paragraph's new number is predicted and checked; numbers that shift (a glitch in the source corrected), "
        "cross-reference fields and written references such as 'as specified in 1.05' are flagged, never edited",
        "a list whose old numbers the engine cannot predict (Word's 'Restart numbering', shared list definitions, "
        "overridden levels) converts only WHOLE — every paragraph of that list — and is flagged; converting part of a "
        "list, or levels whose leftover paragraphs would renumber, sets the file aside: convert every level",
        "with an example file whose style ids this file already has but numbers differently, use existing 'replace' "
        "('keep' sets the file aside: the scheme would not apply); tracked with 'replace' is refused",
        "plain formatting (not tracked) unless asked",
    ],
    "convert_typed_numbers": [
        "your scheme's levels are the numbered paragraph styles of your example file (level 0 first); a typed number "
        "('PART 1 -', '1.1', 'A.', '1.', 'a.') at the start of a paragraph becomes that level's real list number",
        "the typed number and the space or tab after it are removed and nothing is written: the list shows the same "
        "number — only when every paragraph of the document then reads exactly as before; a typo (two 'B.'s, a skipped "
        "letter) or a list that would renumber other paragraphs sets the file aside (the audit shows where)",
        "typed numbers in table cells stay as typed unless include_tables; paragraphs already numbered are never "
        "touched (a typed number on one is flagged)",
        "the list number must LOOK like the typed one: a hidden or capitalised typed number ('a.' in All Caps), a "
        "paragraph mark that would make the number hidden, coloured, sized, raised, struck or in a symbol font sets the "
        "file aside; 'PART 1 -', '1.1', 'A.' in capitals are fine; a bold/italic/underline/font difference is flagged",
        "tracked by default (the removed numbers show as deletions); tracked false removes them outright; "
        "direct_formatting 'clear' needs tracked false; tracked with existing 'replace' over paragraphs already in "
        "those styles is refused",
        "beside another operation that changes text, styles or numbering of the body it runs as its own ordered step",
    ],
    "set_page_setup": [
        "margins are in twips (1440 = 1 inch); header/footer distances unchanged unless given",
        "the header's and footer's distance from the page edge are margins.header and margins.footer (twips)",
        "different_first_page true gives the section's first page its own first-page header/footer (none of its own: "
        "it shows the previous section's, or a blank one — flagged); false shows the ordinary ones there; a section "
        "already as asked is left alone",
        "landscape swaps the page's width and height and leaves the margins as they are (give margins in the same op "
        "to change them); a header/footer shared with portrait pages is flagged",
        "sections can be chosen by what they contain: {containing: <paragraph predicate>, with_tables: true, "
        "index_range: [first, last]}; allow_missing true when not every file has such a section",
    ],
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
    "move_paragraphs": [
        "the paragraphs to move are one unbroken stretch in one place — the body, one table cell, a header, a footer "
        "or a note — and the tables between them move with them; a stretch with a gap, or in two places, sets the file "
        "aside; a whole article is its heading through the paragraph before the next heading",
        "they go right before or right after the one destination paragraph 'to' names, in the same place; no such "
        "paragraph, or several, sets the file aside; a stretch already there is left as it is",
        "tracked by default: deleted where they were and inserted where they go (Word shows a deletion and an "
        "insertion, not 'Moved'); tables, fields, hyperlinks, content controls, comments, pictures, note references or "
        "pending tracked changes among them need an untracked move, which moves everything exactly as it is",
        "automatic list and heading numbers and footnote numbers follow the new order (Word renumbers; no number is "
        "retyped); a section break in the stretch sets the file aside; text landing in another section takes its page "
        "setup and headers (flagged)",
    ],
    "fix_page_numbering": ["body page numbering restarts at 1 after the cover"],
    "replace_table": [
        "every replacement is exactly the user's wording; all pairs are found at once in the original text (two words "
        "can swap), whole words and exact case unless the user says otherwise",
        "the body, headers and footers are searched; text inside fields, text boxes and pending deletions is never "
        "changed — an occurrence there (or split by a tab or line break) sets the file aside rather than being skipped",
        "tracked unless the user asked for clean copies; a file with none of the words is set aside unless "
        "allow_missing (say which)",
    ],
    "set_issue_stamp": [
        "the stamp is found wherever it sits — the cover, a header or a footer — by its current wording (the pattern or "
        "phrases); every occurrence becomes the user's new wording exactly",
        "a file without the stamp is set aside unless allow_missing; tracked unless the user asked for clean copies",
    ],
    "fill_variables": [
        "placeholders are the {{NAME}} markers and the yellow-highlighted text; each gets its value from the typed "
        "values or the user's table (the named site/project column) — never invented",
        "a placeholder with no value sets the file aside unless 'leave' was asked; highlights go from filled text",
        "tracked unless the user asked for clean copies (a master turned into a site set usually is untracked)",
    ],
    "filter_tagged_paragraphs": [
        "the tag is found by the stated pattern (fitted to how these files mark systems — check the samples); untagged "
        "paragraphs always stay",
        "a paragraph tagged for several systems ('WET/DRY') stays while one of its systems stays; the tag is taken off the "
        "paragraphs that stay unless asked to keep it",
        "a tagged paragraph carrying a section break, a field or a text box sets the file aside; tracked deletion (the "
        "default) also refuses one holding pending revisions or a hyperlink",
        "only tagged paragraphs are deleted — the untagged paragraphs of a system's article are not (delete those with "
        "delete_paragraphs and a between_paragraphs selector)",
    ],
    "ensure_end_of_section": [
        "the number is each file's own, read by the stated rule (the SECTION title line, or the footer) — never typed",
        "a last line that reads otherwise (another number, other capitals) is replaced by the user's line; a missing one "
        "is added after the last content — with the file's own end-line style when it has one, or the title line's "
        "formatting, else plain",
        "an END OF SECTION line with content after it sets the file aside",
        "tracked unless the user asked for clean copies",
    ],
    "resolve_options": [
        "an option group is [option] [option] … separated by spaces; for each group the first rule that matches one of "
        "its options decides; groups no rule matches stay as they are",
        "'pick' keeps the one matching option's own words and removes the brackets and the other options — nothing is "
        "retyped; 'drop' removes matching options (a whole optional [text] with its space)",
        "a nested group, a group shown by a field, or a pick that matches two options sets the file aside",
        "tracked unless the user asked for clean copies",
    ],
    "name_output": [
        "each part of the name is read from the file itself by the stated rule (the SECTION title line, a header or a "
        "footer) — never typed; fixed words and separators are in the pattern",
        "the document is not changed; only the edited copy gets the new name, so this never runs in place",
        "a transform (digits, upper, lower, title) changes only the file name, never the document",
        "two files that would get the same name, or a name Windows cannot hold, set the file(s) aside",
    ],
    "swap_image": [
        "the old logo is found by its picture file (its bytes), its md5, its name/alt text, or every picture in scope — "
        "say which; the headers by default",
        "'contain' (default) keeps the new logo's proportions inside the old picture's box, top-left; 'stretch' fills "
        "the box exactly",
        "the old picture's alt text stays as it was (flagged); a linked, SVG, cropped or turned picture sets the file "
        "aside, and so does a file without a matching picture unless allow_missing",
    ],
    "make_values_agree": [
        "the place the user picked is the truth; a place whose value has the same letters and digits (any case or "
        "spacing: '211319' and '21 13 19') already agrees and is left as it is — nothing is converted",
        "a value that disagrees is replaced by the truth's text exactly as the truth writes it; a disagreeing file name "
        "gives the edited copy the corrected name (never in place)",
        "a truth shown two different ways in one file, or a required place that is missing, sets the file aside",
        "tracked unless the user asked for clean copies",
    ],
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
    # ---- built-in batch 2 (docs/BUILTIN_OPS_BATCH2_DESIGN.md) ----
    "remove_comments": [
        "'all' removes every comment; 'resolved' only threads Word marked resolved (a file from Word 2010 or older keeps "
        "no resolved state, so it has none); 'authors' the named authors' comments — a removed comment takes its replies",
        "the document's text is unchanged: only the comments and their marks in the text go; the comment parts stay "
        "(emptied if every comment goes), and a picture only a removed comment used stays in the file, unused",
        "a file with no comment to remove is left as it is (allow_missing false sets it aside instead)",
    ],
    "remove_highlight": [
        "highlighting is Word's highlighter (w:highlight) — every colour unless the user named colours; paragraph and "
        "text shading are not highlighting and stay",
        "body, headers, footers, footnotes and endnotes; highlighted text inside pending tracked changes and text boxes "
        "is included; a style that highlights its text is not changed (flagged)",
        "plain formatting change unless the user asked for it tracked; a file without such highlighting is left as it is",
    ],
    "resolve_tracked_changes": [
        "only when the user asks to accept or reject tracked changes — never as a side effect of another request; "
        "everyone's changes unless authors or dates are named",
        "authors are matched by the name Word shows on each change (case and spacing ignored); dates by Word's own clock "
        "on each change, on_or_after inclusive and before exclusive; under a date filter a change without a date stays "
        "(flagged)",
        "accept: inserted and moved text stays as plain text, deleted text and paragraphs go, new formatting stays; "
        "reject: inserted text and paragraphs go, deleted text comes back, moved text returns, the old formatting comes "
        "back",
        "the body, headers, footers, footnotes, endnotes and comments are covered (scope narrows it); tracked changes "
        "inside style or list definitions stay as they are",
        "a file with no matching change is left as it is (allow_missing, the default); a table row/cell change, a change "
        "in an equation, an accept that would join two paragraphs or take a footnote/comment anchor away sets the file "
        "aside",
        "it runs as its own ordered step: it changes the parts it touches as a whole",
    ],
    "remove_hidden_text": [
        "hidden text is text with Word's Hidden font setting (directly or by a style) — white or tiny text is not hidden",
        "the body, headers, footers, footnotes and endnotes are searched; every removed piece is listed in the preview",
        "a paragraph whose words and mark are all hidden is deleted; one that must stay (the last of a cell or of the "
        "document, between two tables, with a section break) keeps its mark and loses its hidden words",
        "a hidden field goes whole; hidden text inside a field that is otherwise shown, or in a text box, sets the file "
        "aside",
        "tracked unless the user asked for clean copies; a file with no hidden text is left as it is",
    ],
    "remove_notes": [
        "a note is what the rules say — fitted to these files: their note style (CMT, SpecifierNote …), the words notes "
        "start with (Retain, Delete, Revise, Coordinate …), their colour; check the samples",
        "inline notes are runs of the note colour inside paragraphs that stay; hyperlink text and pictures are never "
        "taken",
        "a note that is a table cell's only paragraph leaves the cell empty",
        "a note paragraph with a section break, a field or a text box sets the file aside",
        "tracked unless the user asked for clean copies; a file without notes is left as it is",
    ],
    "add_watermark": [
        "the watermark's words are exactly the user's (one line); Word's look unless asked otherwise: diagonal, Calibri, "
        "silver (C0C0C0), half transparent, sized to fit the page",
        "it goes into every header the document shows (cover first-page header included), first in each header's first "
        "paragraph; pages without a header get none (flagged); a file without any header is set aside",
        "a file that already has a watermark is set aside unless the user asked to replace it (replace true)",
        "a watermark is not a tracked change in Word: the spec is untracked",
    ],
    "remove_watermark": [
        "every watermark in every header goes (text, picture or drawing), or only those showing the stated words",
        "the header's paragraphs stay, so nothing in the header moves",
        "a file without a watermark is left as it is (allow_missing defaults to true)",
    ],
    "remove_empty_paragraphs": [
        "a blank paragraph holds nothing but spaces — a numbered, bordered or bookmarked empty line, or one with a tab or "
        "a page break, is not blank",
        "collapse (default) leaves one blank line where several stand together; all removes every blank paragraph",
        "a cell and the document keep their last paragraph and two tables are never joined",
        "the body only unless headers/footers are asked for; tracked unless the user asked for clean copies; a file with "
        "nothing to remove is left as it is",
    ],
    "remove_hyperlinks": [
        "external links (web addresses, e-mail) only unless the user said internal links or all — links to a place in "
        "the document (bookmarks, a table of contents) stay",
        "the linked words stay exactly and lose the blue underlined Hyperlink style (keep_style keeps it); words with "
        "their own blue or underline keep it (flagged); a picture's click link stays",
        "an address no link uses any more is removed from the file",
        "a link in a text box, or a link field over two paragraphs or in a pending tracked change, sets the file aside; a "
        "file with no such link is left as it is",
    ],
    "unlink_fields": [
        "only the named field types (or all but the page numbers) become the text they show now, with its formatting; "
        "Word no longer updates that text",
        "page numbers (PAGE, NUMPAGES, SECTIONPAGES, PAGEREF) are never converted",
        "a field over two paragraphs (a table of contents), inside another field's code, a form field or one in a "
        "pending tracked change sets the file aside; a field that shows nothing is removed (flagged); a file with no "
        "such field is left as it is",
    ],
    "set_document_properties": [
        "only the named properties change, to the user's exact words; a property already as asked is left alone",
        "'clear the author names' means Author and Last Modified By; tracked changes and comments keep their authors' "
        "names (flagged when the file has any)",
        "fields that show a changed property (TITLE, DOCPROPERTY …) keep their saved value until Word updates fields "
        "(flagged); custom properties are matched by name without case; a file without custom properties gets them",
        "a custom property that holds a number, a date or yes/no in a file sets that file aside",
    ],
    "set_document_state": [
        "only on the user's request: track changes off/on is the setting the file opens with — pending tracked changes "
        "are neither accepted nor rejected; a file whose tracking is locked with a password is set aside",
        "Mark as Final sets the custom property _MarkAsFinal and the Status 'Final', as Word does; unmarking removes both "
        "(another Status stays)",
    ],
}

# How a template slot gets its value (apply_header_footer_template.slot_fills). Transform: ops/slots.py; verifier:
# verify/template.py — both implement exactly this.
FILL_RULES = {
    "literal": "{rule: 'literal', value: '<the user's exact approved words>'} — the only rule that places typed text; "
               "it makes the op untracked and the words must appear verbatim in the request or the user's answers",
    "regex": "{rule: 'regex', pattern: '<Python regex>', source?: 'document' | 'header.<default|first|even>' | "
             "'footer.<default|first|even>' (default 'document'), section?: <int>, occurrence?: 'all' | 'first' (default "
             "'all'), case_insensitive?: bool (default false), flexible_whitespace?: bool (default true), strip?: bool (default true)} — searched in the display "
             "text of every paragraph of the source; the fill is group 1 if the pattern has groups, else the whole match; "
             "with 'all' every match must agree (compared with whitespace collapsed), otherwise the file is set aside as "
             "'ambiguous'",
    "paragraph_after": "{rule: 'paragraph_after', anchor_regex: '<regex>', source?, section?, occurrence?, "
                       "case_insensitive?, flexible_whitespace?, strip?} — the display text of the first non-blank paragraph after a paragraph "
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
                  "own characters, including on the normalized matching path. Fills are stripped of surrounding whitespace and must "
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
                       "case_insensitive": True,
                       "pattern": r"^SECTION\s+(\d{2}[\s-]*\d{2}[\s-]*\d{2}(?:\.\d{2})?)\b"},
    "SECTION_TITLE": {"rule": "first_match", "rules": [
        {"rule": "paragraph_after", "source": "document", "case_insensitive": True,
         "anchor_regex": r"^SECTION\s+\d{2}[\s-]*\d{2}[\s-]*\d{2}(?:\.\d{2})?\s*$"},
        {"rule": "regex", "source": "document", "case_insensitive": True,
         "pattern": r"^SECTION\s+\d{2}[\s-]*\d{2}[\s-]*\d{2}(?:\.\d{2})?\s*[-–—:]\s*(.+)$"}]},
}

SELECTOR_GRAMMAR = {
    "scope": "part kinds: document | headers | footers | footnotes | endnotes | comments (resolved via rels)",
    "paragraphs": "'all' or AND of: style_in [styleIds] (no pStyle = default paragraph style), text_regex (Python "
                  "re.search on the block text), in_table (bool), is_empty (bool), index_range [start, end), "
                  "has_bold (bool), is_all_caps (bool), after_heading_regex (str), "
                  "case_insensitive (bool, default false), flexible_whitespace (bool, default false; literal whitespace "
                  "outside regex character classes matches one or more whitespace characters), "
                  "between_paragraphs {start_regex, end_regex?, include_bounds?}",
    "runs": "'all' or {has_direct_font: bool, font_in: [names], not_in_revisions: bool, color_in: [colours]} — "
            "color_in: the run's effective colour (direct, else its character/paragraph style's) is one of the listed "
            "6-hex values, theme colour names (accent1, text2 …) or 'auto'",
    "sections": "'all', [section indices] (0-based), or (set_page_setup) by content: {containing: <paragraph "
                "predicate — a paragraph of the section matches>, with_tables: bool (the section holds a table), "
                "index_range: [first, last] (inclusive)} — every given key must hold",
    "styles": "'all' | 'all-paragraph-and-character' | {ids: [...]} | {types: [...]}",
    "symbol_runs": "never changed by font ops: a run whose explicit font (direct, character style or paragraph "
                   "style) is a symbol font, or that holds a w:sym",
}


# What a one-off custom transform (codegen.py) can change and still be verified: each family maps to the V10 assertion
# kind that checks it (tests/test_conversation.py pins the mapping against verify/props.KINDS). Anything else —
# highlight, numbering and lists, tables, images, fields, comments, tracked changes, any change of words — has no
# verified check for a custom transform, so a custom transform cannot do it: it needs a built-in operation. Shown to
# Claude in the compiler's and the transform writer's instructions.
CUSTOM_TRANSFORM_LIMITS: Dict[str, Any] = {
    "scope": "Legacy custom transforms without an approved independent outcome contract. These limits do not "
             "describe contract-governed project recovery; that path permits the approved effects, including "
             "template header/footer graphs, subject to V1–V10 and independent original-to-final verification.",
    "families": {
        "run fonts and font sizes of selected text": "run_fonts",
        "fonts and sizes defined in styles": "style_fonts",
        "the document default font and size": "doc_defaults_fonts",
        "paragraph spacing, indents and alignment of paragraphs or of paragraph styles": "paragraph_props",
        "page size, margins, orientation and different first page of sections": "section_props",
        "which header/footer each section shows": "section_hf_refs",
        "bold, italic, caps, small caps, strikethrough, underline and text colour of selected text or of styles; "
        "keep-with-next, keep lines together, widow control and page break before of paragraphs or of paragraph "
        "styles": "properties",
        "tab stops of paragraphs (set, clear, clear the others)": "tab_stops",
        "paragraph borders and shading of paragraphs or of paragraph styles": "borders_shading",
    },
    "cannot": ["highlight", "numbering and lists",
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
                "sample_files": {"type": "integer", "minimum": 1,
                                 "description": "Explicit preview sample count. Omit for automatic selection: "
                                                "eight files, expanded when needed to cover every structural pattern."},
                "geometry_reference": {"enum": ["source", "template", "none"]}}},
            "ops": {"type": "array", "minItems": 1, "items": {"oneOf": op_items}},
            "assertions": {"type": "array", "items": {"type": "object", "properties": {
                "kind": {"enum": list(SPEC_ASSERTION_KINDS)}}, "required": ["kind"]}},
            "unsupported": {"type": "string"},
            "proposal": {"type": "string"},
            "bindings": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                "required": ["file", "md5", "slots"], "properties": {
                    "file": {"type": "string", "description": "Absolute source path from inspection"},
                    "md5": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
                    "slots": {"type": "object", "minProperties": 1, "additionalProperties": {
                        "type": "object", "additionalProperties": False, "required": ["value", "part", "paragraph"],
                        "properties": {"value": {"type": "string", "minLength": 1},
                                       "part": {"type": "string", "minLength": 1},
                                       "paragraph": {"type": "integer", "minimum": 0}}}}}}},
        },
    }


def worked_examples() -> List[Dict[str, Any]]:
    """The same complete examples in a checkout, installed wheel and frozen app.

    Missing or corrupt package data is a packaging error, never a smaller prompt.
    """
    folder = files("spec_surgeon").joinpath("examples", "specs")
    examples = sorted((f for f in folder.iterdir() if f.name.endswith(".json")), key=lambda f: f.name)
    if not examples:
        raise FileNotFoundError("The packaged worked examples are missing; reinstall Spec Surgeon.")
    return [json.loads(f.read_text(encoding="utf-8")) for f in examples]


def op_catalog(examples: bool = True) -> Dict[str, Any]:
    ops = {}
    for name, cls in sorted(OPS.items()):
        ops[name] = {"mode": cls.mode, "description": cls.description, "select": cls.select_schema,
                     "params": cls.params_schema, "verification": cls.verification_profile,
                     "common_assumptions": COMMON_ASSUMPTIONS.get(name, [])}
    cat = {
        "ops": ops,
        "report_only_audits": {
            "placeholders": "Unresolved bracketed options, insert markers, XXXX, TBD and variable placeholders per section.",
            "cross_references": "Section existence, stated titles, duplicates and sections with no incoming references in the batch.",
            "standards": "NFPA, UL, FM, ASTM and ASME designations and editions as written; no edition updates.",
            "readiness": "Comments, tracked changes, highlights, hidden text and placeholders per file.",
            "project_identity": "Project name, number and date variants across files; optional expected identity values.",
            "duplicate_provisions": "Exact and near-duplicate sibling provisions; differing numeric tokens remain distinct; report only.",
            "internal_references": "Article and paragraph references against reliable specification hierarchy; uncertain regions stay unassessed.",
        },
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
            "ops that touch the same thing run as ordered steps the app composes itself; list them in the order they should take effect",
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


def op_sheet() -> Dict[str, Any]:
    """What the system prompt carries for every operation: mode, description, selectors and parameters, plus the
    grammar they share. Verification rules, common assumptions and worked examples stay behind ``describe_op``."""
    ops = {name: {"mode": cls.mode, "description": cls.description, "select": cls.select_schema,
                  "params": cls.params_schema} for name, cls in sorted(OPS.items())}
    return {"ops": ops, "selector_grammar": SELECTOR_GRAMMAR, "fill_rules": FILL_RULES,
            "reference_fills": REFERENCE_FILLS, "expect_checks": list(EXPECT_CHECKS), "expect_syntax": EXPECT_SYNTAX,
            "spec_assertions": list(SPEC_ASSERTION_KINDS), "modes": op_catalog(examples=False)["modes"],
            "rules": op_catalog(examples=False)["rules"]}


def op_index() -> Dict[str, Any]:
    """Small discovery index. Full schemas and examples are fetched with describe_op."""
    return {name: {"mode": cls.mode, "description": cls.description.split(". ", 1)[0][:180]}
            for name, cls in sorted(OPS.items())}


def describe_op(name: str) -> Dict[str, Any]:
    """Return current operation documentation, including only its relevant examples."""
    if name not in OPS:
        raise ValueError(f"Unknown operation {name!r}; choose a name from the operation index.")
    catalog = op_catalog(examples=False)
    return {"name": name, **catalog["ops"][name],
            **{key: catalog[key] for key in ("selector_grammar", "fill_rules", "reference_fills",
                                            "expect_checks", "expect_syntax", "spec_assertions")},
            "worked_examples": [example for example in worked_examples()
                                if any(op.get("op") == name for op in example.get("ops", []))]}
