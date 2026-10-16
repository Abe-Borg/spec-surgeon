# Batch 2 notes — remove_hidden_text, remove_notes, remove_empty_paragraphs (builder C)

Files: `src/spec_surgeon/ops/hidden.py` (remove_hidden_text, remove_notes and their shared core: `keep_rules`,
`FieldSpans`, `apply_field_rules`, `describe`), `src/spec_surgeon/ops/empty_paragraphs.py`, `src/spec_surgeon/verify/removal.py`
(`REMOVAL_KINDS`), `tests/fx_removal.py`, `tests/test_hidden_notes.py`, `tests/test_empty_paragraphs.py`,
`examples/specs/remove-hidden-text.json`, `remove-specifier-notes.json`, `collapse-blank-lines.json`.
Shared file changed (its own commit): `src/spec_surgeon/verify/textcheck.py` — `diff_blocks` (see (g) 1).

## (a) README text

### Remove hidden text (`remove_hidden_text`)

Hidden text is text with Word's *Hidden* font setting — set on the words themselves or by a style. The operation removes
every hidden run in the body, headers, footers, footnotes and endnotes, and deletes a paragraph whose words **and**
paragraph mark are all hidden (an empty paragraph whose mark is hidden counts: it shows nothing either). **Every removed
piece is listed in the preview** — the part, the paragraph and the words, a tab as ⇥, a field by its code in braces
(`«{XE "Sprinkler"}»`).

- Tracked by default (the reviewer sees each deletion; the deleted words stay hidden), untracked on request.
- A hidden index entry or other field goes whole when every part of it is hidden; hidden text inside a field that is
  otherwise shown is refused (`field-result`) — Word would put the field's result back when it updates fields. A hidden
  simple field goes whole untracked and is refused tracked (a simple field cannot be deleted as a tracked change).
- A paragraph the deletion rules keep — the last paragraph of a table cell, of the document or of a header (Word cannot
  remove it), the paragraph between two tables (Word would join them) — or one that cannot be deleted (it carries a
  section break, holds a field, or — tracked — holds a hyperlink or someone's pending change) keeps its paragraph mark
  and loses its hidden words; the preview says so.
- Refused: hidden text in a text box (`textbox`), a run whose *hidden* is set at two style levels (`toggle-ambiguous`:
  Word combines them in a way the engine does not model), hidden text in something other than a hyperlink, content
  control or tracked change (`structure`).
- A file with no hidden text is left as it is (a note says "nothing to do"); `allow_missing: false` sets such a file aside.
- How it is verified: the kind `removed_hidden_text` works out again, with the verifier's own reading of *hidden*, which
  runs and paragraphs go, compares that with the plan, and checks every changed paragraph item by item (each removed
  run gone, or wrapped in its own new deletion; nothing else in the paragraph moved) and every deleted paragraph (a
  tracked deletion of exactly that paragraph, or gone with every other paragraph unchanged).

### Remove specifier or editor notes (`remove_notes`)

A note is described by **rules**: a paragraph is a note when any rule matches, and within one rule every test it gives
must hold:

| test | holds when |
|---|---|
| `style_in` | the paragraph's style is one of these style ids (no style = the default paragraph style) |
| `text_regex` | the paragraph's text matches (e.g. `^(Retain|Delete|Revise|Coordinate)\b`) |
| `color_in` | every run with visible words has one of these colours — `0000FF`, a theme colour (`accent1` …) or `auto` |
| `hidden` | every run with content is hidden |

Note paragraphs are deleted. `runs` (`color_in` and/or `hidden`) picks **inline notes** — runs of that colour inside the
paragraphs that stay — and removes them; hyperlink text (often blue) and pictures are never taken. Every removed note is
listed in the preview. Tracked by default, the body by default. A note that is the only paragraph of a table cell (or
ends the document, or stands between two tables) leaves an empty paragraph rather than removing it. A note paragraph
that cannot be deleted — a section break, a field, a text box, or (tracked) a hyperlink or someone's pending change — sets
the file aside. A file without notes is left as it is (`allow_missing: false` sets it aside). Bracketed inline notes in
plain black text (`[Note to specifier: …]`) are not notes by colour or style: use `replace_table` with a regex pair and
an empty replacement. Verified by the kind `removed_notes` (the same checks as hidden text, with the verifier's own
style, text, colour and hidden readings).

### Remove blank paragraphs (`remove_empty_paragraphs`)

`collapse` (the default) leaves one blank line where several stand together; `all` removes every blank paragraph. A
paragraph is blank when it holds nothing but spaces (or no-break spaces) — a numbered or bulleted empty line, one with a
border or a frame, a bookmark, a tab, a break, a field, a picture, a comment mark or someone's pending change is **not**
blank. What always stays: one paragraph between two tables (Word would join the tables), the last paragraph of a table
cell, of the document and of a header — untracked only when it is needed (a cell or document must end with a
paragraph), tracked always (Word cannot delete the final paragraph mark). The body by default (`scope` adds headers,
footers …); tracked by default. A file with nothing to remove is left as it is (`allow_missing: false` sets it aside).
Verified by the kind `removed_empty_paragraphs`: the verifier finds the blank paragraphs, the runs and the kept ones
again with its own code and checks each deletion.

## (b) CLAUDE.md lines (both-sides definitions)

- **Hidden text, notes and blank paragraphs (built-in batch 2, items 14, 15, 17).** *Content runs* of a paragraph: its
  runs (nearest `w:p`) that are live (no `w:del`/`w:moveFrom` below it) and hold something besides `w:rPr`. *Hidden
  paragraph*: mark hidden (`Look.mark_hidden` / `v_mark_hidden`) and every content run hidden (none counts); hidden runs:
  `Look.hidden` / `v_hidden`, ambiguous → `toggle-ambiguous` (evaluated on every content run; the mark only when every
  content run is hidden). *Note rules*: OR of rules, AND within a rule — `style_in` (pStyle, else the default paragraph
  style), `text_regex` (batch-1 run text), `color_in` (a content run with visible text — a `w:t` with a non-space — exists
  and every such run's effective colour is listed), `hidden` (a content run exists and all are hidden); evaluated in that
  order. Inline notes (`runs`): content runs of a listed colour and/or hidden, never inside a `w:hyperlink`, never holding
  `w:drawing`/`w:pict`/`w:object`. *Blank*: not in a text box, no `w:sectPr`, no revision element, no numbering (the
  first `w:numPr/w:numId` of own pPr → style chain → docDefaults decides; 0 = off), no `w:pBdr` with a child and no
  `w:framePr` on that chain, only `w:pPr`, `w:proofErr` and runs of `w:rPr`/`w:lastRenderedPageBreak`/`w:t` of spaces and
  NBSPs; collapse = every blank whose previous block is blank. *Keep rules* (per container = the paragraph's parent, over
  its blocks `w:p`/`w:tbl`/`w:sdt`/`w:customXml`/`w:altChunk`): the first block between two tables the deletions would
  join; the last block when tracked, or untracked when the rest would leave the container empty or ending in a
  non-paragraph. Hidden text and notes: a kept paragraph (and, hidden text only, one `deletable` refuses for anything but
  a text box) loses its content runs to remove instead; notes refuse what `deletable` refuses. *Fields*: a run to remove
  in a complex field's span (begin→end over the part's runs) goes only with the whole outermost field (local, every
  content run of the span chosen and live, no `w:fldSimple` in it); in a `w:fldSimple` only with the whole outermost
  simple field (untracked; tracked refused); not reachable through containers → `structure`; holding or inside a text box
  → `textbox`. Transform: `ops/hidden.py` (`content_runs`, `RemoveHiddenText.choose`, `RemoveNotes.choose/_matches`,
  `keep_rules`, `FieldSpans`, `apply_field_rules`), `ops/empty_paragraphs.py` (`is_empty`, `numbered`,
  `bordered_or_framed`, `blank_paragraphs`); verifier: `verify/removal.py` (`_v_content`, `_v_choose_hidden`,
  `_v_choose_note`, `_v_note_rule`, `_v_keep`, `_VSpans`, `_v_field_rules`, `_v_blank`, `_v_numbered`, `_v_boxed`,
  `_v_blank_decisions`). The plan's assertion carries the user's raw note rules (the verifier reads them itself) and
  `expected` per part (`delete`, `runs` — own-run indices —, `simple` — own simple-field indices); the kinds compare their
  own decisions with it, check changed paragraphs by the item model (`drop` / `tracked_delete`) and register them in
  `verified_blocks`; deleted paragraphs by `check_deleted_paragraph` (tracked) or `deleted_src_blocks` + the paragraph
  count (untracked). `tests/test_hidden_notes.py` and `tests/test_empty_paragraphs.py` plant shared defects in the hidden
  rule, the note colour rule, the hyperlink exclusion, the keep rules, the field rule, the blank rule and the collapse.
- **The batch-2 clean-up ops default `allow_missing` to true** (`ops/hidden.CLEANUP_PARAMS`): a file with nothing to
  remove plans as a no-op with a note; `allow_missing: false` keeps the `not-found` refusal.
- **V2's pairing** (verifier only, `textcheck.diff_blocks`): the plan's predicted untracked block deletions and insertions
  are set aside before the text alignment (reported as deletions/insertions); the rest is aligned on text. A predicted
  deletion that did not happen leaves its block in the output, reported as an unpredicted insertion.

## (c) FAILURE_EXPLANATIONS

No new codes (toggle-ambiguous, field-result, textbox, structure, section-break, existing-revisions, not-found,
bad-params all exist). Suggested wording updates so the explanations fit the new ops:
- `toggle-ambiguous`: title "A Toggle (Bold, Italic, Caps, Hidden) Set at Several Style Levels"; text: "Whether the text
  is bold, italic, in capitals or hidden depends on that setting appearing at several style levels, which Word combines
  by a rule the engine does not model; the file needs a human look. For a style change, set the formatting directly on
  the selected paragraphs instead (direct formatting always wins)."
- `field-result`: add "— or text to remove is part of a field that is not removed as a whole: Word would show the field's
  result again when it updates fields."

## (d) COMMON_ASSUMPTIONS

- `remove_hidden_text`: "hidden text is text with Word's Hidden font setting (directly or by a style) — white or tiny
  text is not hidden"; "the body, headers, footers, footnotes and endnotes are searched; every removed piece is listed in
  the preview"; "a paragraph whose words and mark are all hidden is deleted; one that must stay (the last of a cell or of
  the document, between two tables, with a section break) keeps its mark and loses its hidden words"; "a hidden field
  goes whole; hidden text inside a field that is otherwise shown, or in a text box, sets the file aside"; "tracked unless
  the user asked for clean copies; a file with no hidden text is left as it is".
- `remove_notes`: "a note is what the rules say — fitted to these files: their note style (CMT, SpecifierNote …), the
  words notes start with (Retain, Delete, Revise, Coordinate …), their colour; check the samples"; "inline notes are runs
  of the note colour inside paragraphs that stay; hyperlink text and pictures are never taken"; "a note that is a table
  cell's only paragraph leaves the cell empty"; "a note paragraph with a section break, a field or a text box sets the
  file aside"; "tracked unless the user asked for clean copies; a file without notes is left as it is".
- `remove_empty_paragraphs`: "a blank paragraph holds nothing but spaces — a numbered, bordered or bookmarked empty line,
  or one with a tab or a page break, is not blank"; "collapse (default) leaves one blank line where several stand
  together; all removes every blank paragraph"; "a cell and the document keep their last paragraph and two tables are
  never joined"; "the body only unless headers/footers are asked for; tracked unless the user asked for clean copies; a
  file with nothing to remove is left as it is".

## (e) Words placed

None (all three only remove).

## (f) SYSTEM-prompt hints for Claude

- "Hidden text" is `remove_hidden_text` (no params needed). "Specifier notes / editor's notes" is `remove_notes`: read the
  profiles' `styles_in_use` for a note style (CMT, SpecifierNote, EditorNote, a blue `color` or a `color_theme`) and the
  sample paragraphs for the words notes start with; write one rule per way a note is marked (`{"style_in": ["CMT"]}`,
  `{"text_regex": "^(Retain|Delete)\\b", "color_in": ["0000FF"]}`), and `runs` only when the user mentions inline notes
  or blue words inside paragraphs. Colours are hex (`0000FF`) or theme names (`accent1`) exactly as the profile shows
  them. Never use a bare `color_in` rule for black/auto text. State the rules in assumptions.
- "Remove blank lines / empty paragraphs": `remove_empty_paragraphs`, `mode: "collapse"` when the user speaks of double
  or extra blank lines, `"all"` when they want none at all.
- All three: tracked unless the user asked for clean copies; a file with nothing to remove is fine (no-op).

## (g) Departures from the design note

1. **V2 pairing fix (shared, own commit).** `verify/textcheck.diff_blocks` now sets the predicted untracked deletions and
   insertions aside before aligning, instead of only inside an unequal-length region: a deleted blank paragraph tied with
   another blank paragraph elsewhere (one the same op emptied) threw every block between them out of alignment and V2
   failed a correct output (reproduced: `tests/test_empty_paragraphs.py::test_v2_a_deleted_blank_never_pairs_with_another_
   blank_far_away` — the old code reports every block between as deleted and re-inserted). Not weaker: a set-aside block is
   reported as deleted whatever the output holds, so a predicted deletion that did not happen leaves an unpredicted
   insertion (`test_v2_a_predicted_deletion_that_did_not_happen_still_fails`).
2. `allow_missing` defaults to **true** for all three ops (coordinator's decision).
3. The keep rules (two tables, the container's last block) apply to hidden text and notes too, both modes (§8 had them
   for blank paragraphs only); a kept hidden or note paragraph loses its content to remove and keeps its mark (flagged)
   instead of being refused.
4. Hidden text: a hidden paragraph `deletable` refuses for anything but a text box (section break, field, tracked: pending
   revisions or non-run content) is handled the same way (its hidden runs go, the mark stays, flagged) rather than
   refused. Notes keep refusing those (the user asked to delete the note).
5. An empty paragraph whose mark is hidden is a hidden paragraph (deleted) — it shows nothing.
6. Inline notes never take a run holding a picture (§6 named only hyperlinks).
7. Within `color_in`, runs with only spaces are ignored (a note paragraph's trailing space run in black does not stop it).
8. The plan lists every removed piece as a flag (`describe`: words, ⇥, ⏎, `{field code}`, `[picture]`).

Pre-existing, outside this work: `tests/test_naming.py::test_the_convention_from_the_users_example` compares the output's
bytes with a second `build_batch1()`; the two builds differ when they straddle a 2-second zip-timestamp boundary, so the
test fails now and then (the verification itself passes).
