# Built-in operations, batch 6 — design note: full control

2026-10-21. Abraham's direction (docs/PLAN-2026-10-21-full-control.md): the user needs precise and broad control, from
one word to whole tables, sections and pictures; the engine's refusals were policy more than necessity. This batch
loosens what was policy and adds the structural operations a specification writer reaches for in Word.

| Item | Asked for | Built as | Mode |
| --- | --- | --- | --- |
| F2 | Delete anything a paragraph selection covers | **`delete_paragraphs` grows** (§1): "all", whole tables, fields, section breaks, kept last paragraphs | tracked by default |
| F2 | Delete footnotes and endnotes | **new op `remove_footnotes`** (§2) | tracked by default |
| F3 | Superscript, subscript, hidden, highlight, character spacing, double strike | **`set_properties` grows** (§3) | formatting |
| F3 | Several paragraphs, styles, tabs, line breaks, bold/italic segments | **`insert_paragraph` grows** (§4); `replace_text`'s new text may hold tabs and line breaks (§4.3) | tracked by default |
| F4 | Insert, delete and resize pictures | **new ops `insert_image`, `remove_images`, `resize_images`** (§5) | tracked by default / formatting |
| F5 | Delete tables and rows, insert rows and tables | **new ops `delete_tables`, `delete_table_rows`, `insert_table_rows`, `insert_table`** (§6); `resolve_tracked_changes` resolves row revisions | tracked by default |
| F6 | Delete columns, cell shading/alignment/widths, merge cells | **new ops `delete_table_columns`, `set_table_cells`, `merge_table_cells`** (§7) | untracked only |
| F7 | Insert and remove section breaks; columns, line numbers, vertical alignment, start type | **new ops `insert_section_break`, `remove_section_break`; `set_page_setup` grows** (§8) | untracked only |
| F8 | Page numbers and other fields; reply to and resolve comments | **new ops `insert_field`, `reply_to_comments`, `resolve_comments`** (§9) | tracked by default / untracked |
| F9 | Move content | **new op `move_paragraphs`** (§10) | untracked, or tracked as deletion + insertion |

Reading order: CLAUDE.md (invariants, "How to add an op"), docs/BUILTIN_OPS_BATCH2_DESIGN.md (whole-part
verification, `verified_parts`, `expected_removed`), docs/BUILTIN_OPS_BATCH5_DESIGN.md §5 (the table selector), and the
op each section names as its nearest relative.

## 0. Rules for the whole batch

1. **Predict first, verify independently.** Each op's V10 kind is written first, in its own `verify/` module exporting
   a `*_KINDS` dict imported at the end of `verify/props.py`. It re-derives the selection and every decision from the
   SOURCE with its own code and checks the output "exactly this change"; the residual identity covers the rest.
   Structural kinds may build the expected part tree themselves and compare it whole (`ctx.verified_parts`).
2. **Refuse the unpredictable, not the large.** Size is never a refusal. A refusal names what cannot be predicted
   (code in `report.FAILURE_EXPLANATIONS`) and, where it helps, "change this one in Word". Tier 3 objects (shapes,
   SmartArt, charts, equations, embedded objects, data-bound content controls) and text boxes are refused.
3. **Zero generated characters.** Structural ops add no text. New text is the user's (typed in the spec, traced by
   `compiler.placed_words`, or read from a file they attached through `inputs.py`).
4. **Tracked like Word tracks it** (plan principle 4). Content-adding and content-removing ops are tracked by default,
   `tracked: false` on request. Untracked-only ops accept only `tracked: false` (as `apply_header_footer_template`).
5. **Containers stay valid.** A body, table cell, header, footer, note, comment or block content control always ends
   with a paragraph; two tables never become adjacent; `w:body/w:sectPr` is never touched by a deletion.
6. **Parts stay whole.** A member no longer referenced (a header part, a picture, a footnotes entry's media) leaves the
   package with its relationship and content-type entry, predicted in `members_removed`; V1's orphan check holds.
7. **Fixtures in their own module** (`tests/fx_<area>.py`), describe cases in their own `tests/describe_cases/<area>.py`.

## 1. `delete_paragraphs` grows (F2)

Nearest relatives: `remove_empty_paragraphs` (the keep rules), `remove_notes` (fields inside deleted paragraphs),
`remove_header_footer` / `hf_common` (removing parts).

* `select.paragraphs` may be `"all"` (and any predicate; the "targeted" requirement goes). A selector is still
  required: a deletion names what it deletes. Paragraphs of a footnote/endnote separator are never chosen.
* **Tables.** A table every paragraph of which is selected (nested tables included) is deleted whole: untracked, the
  `w:tbl` goes; tracked, each row is marked deleted (`w:trPr/w:del`) and its content deleted as Word records it. A
  table partly selected keeps every cell's last paragraph (emptied: untracked its runs go, tracked they are deleted
  and the mark stays).
* **Fields.** A paragraph holding fields is deletable when every field lies wholly inside the deleted paragraphs:
  untracked, gone; tracked, the field's runs deleted (`w:instrText` → `w:delInstrText`). A field crossing the edge is
  refused.
* **Section breaks.** A selected paragraph carrying `w:pPr/w:sectPr`: untracked, the paragraph and its `sectPr` go and
  the content before it joins the following section (Word's behaviour); header/footer parts no other section
  references leave the package. Tracked, the paragraph mark is marked deleted and the `sectPr` stays until the
  deletion is accepted. Flagged either way: the pages before the break take the next section's setup and headers.
* **Comments and bookmarks.** Untracked, a comment whose whole range lies inside the deletion goes with it (its
  `comments.xml` entry and replies, as `remove_comments` removes them); a comment crossing the edge is refused
  (`comment-crossing`). Tracked, comments stay anchored to the deleted text until the deletion is accepted (an
  untracked removal inside a tracked deletion would not come back on reject), flagged — so nothing crosses. Bookmarks
  never cause a refusal: one wholly inside goes; one crossing the edge keeps its outside half, and its inside half
  takes the place of the outermost element removed around it (between blocks, at the edge of what stays). Permission,
  move and custom-XML ranges are paired the same way. A `REF`/`PAGEREF`/`NOTEREF` field or a hyperlink elsewhere
  pointing at a removed bookmark is flagged.
* **Notes, pictures, links** (untracked; rule 0.6): a footnote or endnote referenced only from the deleted text goes
  with it; an image or hyperlink relationship no remaining content uses leaves its part's relationships, and media no
  relationship targets leaves the package. A chart, diagram, embedded object or control (its own part) in an untracked
  deletion is refused (`embedded-object`). Tracked, all of them stay until the deletion is accepted.
* **Kept paragraphs** (rule 0.5): when the selection would empty a container or leave it ending in a table, or join
  two tables, the last selected paragraph there stays, emptied; flagged. Tracked, a container's last paragraph keeps
  its mark whenever it is chosen (Word cannot delete a container's final paragraph mark). Two tables with nothing but
  chosen tables between them are refused (`tables-join`).
* **Tracked runs.** The runs inside a hyperlink, smart tag, run-level content control or custom XML element are
  deleted where they stand (a `w:del` inside the container), as Word records it; a simple field, an equation or other
  content a tracked deletion is not written over is refused.
* Text boxes stay refused.

## 2. `remove_footnotes` (F2)

Remove footnotes and/or endnotes: `which` (`footnotes`, `endnotes`, `both`; default both), optional `text_regex` on
the note's own text (its paragraphs, one per line), scope the parts that carry references (document — the default —,
headers, footers, comments). Untracked: the reference (its run, when the run holds nothing else) and the note's entry
go, with what only the note held (pictures, links, bookmarks, comments anchored in it) through the deletion's removal
(separators stay; Word renumbers). Tracked: each reference run is deleted (one `w:del` each; a reference sharing its run
with other content is refused) and every run and paragraph mark of the note is deleted, as Word records it. A custom
reference mark (`w:customMarkFollows`, `custom-mark`), a reference in a text box, a note referenced twice or holding a
text box are refused; a file with no such note is left alone by default (`allow_missing`). V10 kind
`removed_footnotes`.

## 3. `set_properties` grows (F3)

`params.run` gains `vertAlign` (`superscript` | `subscript` | `baseline`), `vanish` (hidden, true/false), `highlight`
(Word's highlight colour names, or `none`), `spacing` (character spacing in points, negative = condensed, 0 = normal)
and `dstrike` (double strikethrough; turning it on turns `strike` off and the reverse, as Word does). Same selection,
tracking and "already as asked" rules as the existing run properties; the `properties` V10 kind grows its twin.

As built: `vanish` is a toggle (ECMA-376 §17.7.3); `dstrike` is read as one too (as `numbering_convert`'s look does —
at two style levels it is ambiguous: set directly on text, refused on a style). The partner of `strike`/`dstrike` is an
asked element like any (off on text only where it shows; in a style unless the style already says off); both on is
`bad-params`. `vertAlign`, `highlight` and `spacing` are nearest-level values (defaults `baseline`, `none`, 0);
`highlight: none` writes `w:val="none"` (explicitly off — `remove_highlight` is the op that takes the element away);
`spacing` is a multiple of 0.05 pt within ±1584 pt, written in twentieths, and a measure with a unit (`"2pt"`) is never
"already". `vanish`, `vertAlign` and `spacing` reflow (layout reference `none`); `highlight` and `dstrike` do not.

## 4. Richer text (F3)

### 4.1 `insert_paragraph`: tabs and line breaks
`text` may hold `\t` (written `w:tab`) and `\n` (written `w:br`, a line break in the same paragraph).

### 4.2 `insert_paragraph`: several paragraphs and segments
`paragraphs: [{text | runs, style?}]` instead of `text` inserts several paragraphs in order at each anchor; `runs:
[{text, b?, i?, u?, vertAlign?}]` gives a paragraph bold/italic/underlined segments. Every text is the user's, verbatim,
and is in `compiler.placed_words`.

As built: exactly one of `text`, `runs`, `paragraphs`; no control character but `\t` and `\n`; every paragraph holds a
visible character. A segment's own properties are written as direct formatting over the inherited run properties
(set_properties' writer: `b`/`i` as their pairs, `u` keeping the inherited underline's other attributes), in schema
order. A paragraph's own `style` replaces the op's (`inherit`/null: the anchor's). Tracked, each new paragraph's mark
carries a `w:ins` and its runs sit in one `w:ins`. An anchor holding a text box takes paragraphs before it only
(`textbox`: its box's paragraphs would come between). Placed words: each segment on its own; a phrase holding a tab or
a line break traces when each stretch of words between them does (`compiler.untraceable`).

### 4.3 `replace_text`: tabs and line breaks in the new text
The replacement may hold `\t` and `\n`, written as `w:tab` / `w:br` inside the inserted run(s); V3's structural counts
are declared (`struct_after`). Both sides of the edit core (`ops/textedit.py` ↔ `verify/edits.py`).

As built: the core learns it behind an explicit `breaks=True` (off by default — every other op on the core still refuses
a control character); `replace_text` writes with the core's `text_children` / `place_text` (untracked: after the edited
`w:t` in its run, the document's own characters never re-read as structure) and `literal_replacement` verifies with
`breaks`; a `paragraph_edits` assertion carries `breaks: true`. A tracked replacement of only tabs or breaks declares a
structure-only change. Any other control character (`\r` included) is `bad-params`.

## 5. Pictures (F4)

Nearest relative: `swap_image` (the picture model `ops/images.py` ↔ `verify/images.py`, its `match` grammar).

* **`insert_image`**: an attached picture (PNG, JPEG, GIF, BMP; SVG refused) as its own paragraph `before`/`after`
  each anchor paragraph (targeted selector); `width`/`height` in inches (one given keeps the proportions; neither: the
  picture's own size at its resolution, 96 dpi when it has none, shrunk to the anchor section's text width — in a
  header or footer the narrowest of every section's); `align`; `description` (alt text, the user's words); the new
  paragraph's style (default: the document's default paragraph style). Media `word/media/imageN.ext`, a relationship
  per part (its rels created when missing), a content-type Default when missing. Tracked by default (the paragraph is
  a tracked insertion). An anchor in or holding a text box, and `after` a section break, are refused. V10 kind
  `inserted_images`.
* **`remove_images`**: the pictures `match` selects (file, checksum, name/description pattern, `all`) in the scope (the
  document body by default); tracked, the run holding each is deleted (split where it also holds text); untracked, the
  drawing goes, and media and relationships no longer used leave the package. Watermarks are `remove_watermark`'s.
  The paragraph stays. VML pictures, pictures in shapes, groups, text boxes or fields, and pictures in a pending
  change are refused. V10 kind `removed_images`.
* **`resize_images`**: the pictures `match` selects get `width`/`height` in inches or `scale` in percent (proportions
  kept unless both are given); inline and floating, cropped included; turned pictures and pictures sized relative to
  the page are refused; formatting, untracked. V10 kind `resized_images`.

## 6. Table rows and tables (F5)

Nearest relatives: `set_table_props` (the table selector), `delete_paragraphs` (tracked deletion), `replace_table`
(reading an attached table file through `inputs.py`). As built: shared core `ops/table_edit.py`, verifier
`verify/tablestructure.py` (each kind builds the changed part from the source and compares it whole,
`ctx.verified_parts`; every op claims the parts it changes whole, `(part, "*", "text")`, so another op on that part —
`set_table_props`, `apply_table_style` — is composed into a step of its own).

* **Row selector** (both sides: `ops/table_rows.py` ↔ `verify/tablerows.py`; F6 builds on it). `select.rows` is `"all"`
  or an object whose given keys must all hold, over the table's **own rows** (the `w:tr` children of the `w:tbl`,
  numbered from 0; a table whose rows sit in a content control or custom XML is refused, `table-structure`):
  `index_range: [a, b]` (whole numbers, 0 ≤ a ≤ b, both inclusive); `text_regex` (`re.search` on the row's text: its
  cells — `w:tc` whose nearest `w:tr` is the row — joined by a tab, each its paragraphs' — `w:p` whose nearest `w:tc` is
  the cell — block texts joined by a space; `table_select.first_row_text`'s reading for any row); `header: bool` (the
  row's own `w:trPr/w:tblHeader` decides when present, on unless 0/false/off; else the nearest level of the table
  style chain whose `w:trPr` holds one; else not a header row). Reads: `text_regex` every own row's paragraphs'
  `verify:text`, `header` every row's `(part, "tr:<k>:<r>", "verify-props:tblHeader")` and the style chain.
  **Grid** (same modules): a cell's span `w:gridSpan` (≥ 1, default 1), its grid column the row's `w:gridBefore` plus
  the spans before it (`grid_columns`); **merged**: span > 1, any `w:vMerge` or a `w:hMerge` (`is_merged`); a
  **vertical merge** (`merge_groups`) runs down one grid column from a `restart` cell (or a continuing cell with none
  open above) through the continuing cells below it; its content is its first row's cell.
* **`delete_tables`**: the tables the table selector chooses (a table nested in a chosen one goes with it); tracked
  (default: every `w:tr` in it, nested ones too, gets `w:trPr/w:del`, every paragraph's mark and runs are deleted —
  `w:instrText` becomes `w:delInstrText`) or untracked (the `w:tbl` goes). V10 kind `deleted_tables`.
* **What a deletion may take** (both deletion ops, `table_edit.removal`): a section break inside → `section-break`; a
  comment mark → `table-anchor` (remove the comments first); untracked, a footnote/endnote mark → `table-anchor`
  (tracked, the reference run is deleted as Word records it); a field, permission, move or custom-XML range with one
  part inside → `table-anchor`; tracked, pending tracked changes inside → `existing-revisions`, a text box → `textbox`,
  a paragraph holding more than runs, bookmarks and proofing marks → `structure`. **Bookmarks never refuse**: one
  wholly inside goes untracked (a cross-reference elsewhere is flagged); an end whose partner is outside keeps its
  place — untracked it takes the deleted element's place (allowed between blocks and between rows). **A table going
  whole untracked** may not leave its container (body, cell, header …) empty, ending in something other than a
  paragraph, or with two tables side by side that were not (`table-structure`). Pictures going with it leave their
  media in the package (still referenced by its relationship) until Word saves; flagged.
* **`delete_table_rows`**: `select.rows` (required) in the chosen tables; tracked (`w:trPr/w:del`, content deleted) or
  untracked. Deleting every own row deletes the table (as `delete_tables`); a nested table inside a row that goes
  goes with it. **Vertical merges** (decision changed from "crossing the edge"): only a deletion that takes a merge's
  first row and keeps another of its rows is refused (`vertical-merge`, `merge_crossings`); taking later rows only
  shortens the merge, taking it whole is fine. V10 kind `deleted_table_rows`.
* **`insert_table_rows`**: `rows: [[cell text, …], …]` (the user's words, `compiler.placed_words`; no control
  character) `before`/`after` the one row `select.rows` chooses per table (none: the table is left alone; more than
  one: `ambiguous`), or `at_end` (no `select.rows`); `repeat_header` (default false). Each new row copies the
  reference row, without attributes: `w:tblPrEx` less its change; `w:trPr` less `w:cnfStyle`, `w:ins`/`w:del`,
  `w:trPrChange` and `w:tblHeader`, then `<w:tblHeader/>` with `repeat_header`, else `w:val="0"` where the style
  chain turns it on (set_table_props' rule); each cell's `w:tcPr` less `w:cnfStyle` and cell revisions; the cell's
  first paragraph's `w:pPr` less `w:cnfStyle`, `w:sectPr`, `w:pPrChange` and its mark's revisions; the first live text
  run's `w:rPr` (no such run: the cleaned mark's — Word gives typed text the mark's formatting). A typed row of
  another cell count, or a reference row with merged cells → `table-shape`. Tracked (`w:trPr/w:ins`, marks and runs
  inserted) or untracked. V10 kind `inserted_table_rows`.
* **`insert_table`**: from typed `rows` (equal lengths) or an attached file `table: {file, sheet?, key?}` (a CSV, a
  sheet, or a JSON list of rows at `key`; trailing empty rows and columns left out, short rows padded; read by
  `tables.grid` ↔ `verify/tables.table_grid`, pinned by md5), `before`/`after` each anchor paragraph of the **body**
  (targeted selector). `header_rows` (`w:tblHeader`; `w:tblLook` firstRow on with them, firstColumn off); `style` (a
  table style the document has: `missing-style` / `style-mismatch`; none: the six border sides single ½ pt as direct
  borders); `w:tblW` 100 % (`pct` 5000) with `w:gridCol`/`w:tcW` of the section's text width (`pgSz@w` − left − right
  margins; the gutter is not counted) in equal columns ⌊W(k+1)/n⌋ − ⌊Wk/n⌋, or `column_widths` (inches; `w:tblW`
  their sum). Cells: one paragraph with no properties of its own. Refused: an anchor in a table, a new table beside
  another table or after the body's last paragraph, a section without page width and margins (`table-placement`);
  `after` a section break (`section-break`). Tracked by default. V10 kind `inserted_tables`.
* **`resolve_tracked_changes`** accepts and rejects tracked row insertions and deletions (accept an inserted row: the
  marker goes, an emptied `w:trPr` with it; accept a deleted row: the row goes with all it holds; reject the
  reverse), both sides. A row that goes is content that goes for its anchor rules (a bookmark end inside whose
  partner stays → `marks-in-change`, unlike the deletion ops' keep rule); a table whose every own row goes goes whole
  under the container rule (`table-structure`); a row in a content control (`table-structure`) and a moved row, cell
  insertions/deletions/merges (`revision-kind`) stay refused.

## 7. Table grid and cells (F6) — after F5 (the row selector)

* **Column selector** (both sides, new): `{index: [grid column indices]` or `header_regex` (the first row's cell text)}.
* **`delete_table_columns`**: the grid columns chosen go: `w:gridCol` removed, a cell covering only those columns
  removed, a spanning cell's `gridSpan` and width reduced; a fixed table width shrinks by the columns' widths. Deleting
  every column is refused (`delete_tables`). Untracked only. V10 kind `deleted_table_columns`.
* **`set_table_cells`**: the cells chosen by table, rows and columns get `shading` (fill colour or `none`),
  `vertical_alignment` (`top` | `center` | `bottom`), `no_wrap`, and their columns `width` (inches: the grid column
  and every cell's `w:tcW` in it). Untracked. V10 kind `table_cells`.
* **`merge_table_cells`**: the rectangle (rows a–b, columns c–d) becomes one cell (`gridSpan` across, `vMerge`
  down); its content is the covered cells' paragraphs in reading order, empty cells' paragraphs dropped. A rectangle
  cutting an existing merge is refused. Untracked. V10 kind `merged_table_cells`.

## 8. Sections (F7)

Nearest relatives: `set_page_setup` (the section selector), `remove_header_footer` / `hf_common`.

* **`insert_section_break`** before each selected top-level body paragraph; `type` `next_page` (default) |
  `continuous` | `even_page` | `odd_page`. The paragraph before the match gets a copy of the governing section's
  `sectPr` (same page setup and header/footer references); the governing section's start type becomes the asked one,
  and anything that must stay with the original section start (a page-number restart) stays with the copy. A new
  first page where the section shows a different first page is flagged. A match that is the first paragraph, follows a
  table, or already follows a break is refused. Untracked only. V10 kind `inserted_section_breaks`.
  *As built:* a section's START is its `w:type` and its page-number restart, the rest of its sectPr its setup; a section
  with k matches splits into k + 1 parts, the first keeping the source sectPr unchanged and every later one starting with
  the asked break and no restart. Also refused: a match in a table, text box or content control (`break-position`,
  `textbox`), a pending tracked change of the section's properties or of the carrying paragraph's mark
  (`tracked-section`). Flagged besides the first page: line or note numbers that restart per section, several columns,
  a vertical alignment.
* **`remove_section_break`**: the break at the end of each chosen section (index or content, `set_page_setup`'s
  selector) goes; its content joins the following section and takes its setup and headers (Word's behaviour); header/
  footer parts no longer referenced leave the package; the paragraph that held the break stays. The last section's
  properties cannot be removed. Untracked only. V10 kind `removed_section_breaks`.
  *As built:* `"all"` is every break; choosing the last section is `last-section`. So that nothing moves but the joined
  pages, the joined section keeps the START of the first joined section (where it now begins — a next-page cover stays
  on its own page, the body keeps its numbers), and each header/footer reference the following section inherited from
  a joined one moves into it (its pages and every later section's show what they showed); removing a break inserted by
  `insert_section_break` gives the source back. A printer-settings part only a removed break used leaves the package
  too. A pending tracked change of a removed sectPr or of its paragraph mark is refused (`tracked-section`).
* **`set_page_setup`** gains `columns` (`{count, space, separator}`; count 1 = one column), `line_numbering`
  (`{count_by, start, restart, distance}` or `none`), `vertical_alignment` (`top` | `center` | `both` | `bottom`) and
  `section_start` (`next_page` | `continuous` | `even_page` | `odd_page`).
  *As built:* each is written as Word writes it (a schema default is an absent attribute or element) and left alone
  where already in effect; columns are always equal (`w:equalWidth` off and the per-column `w:col` go); `start` is the
  first number shown, written as `w:start` minus one (Word and LibreOffice show the attribute plus one). V10's
  `section_props` gains the `exact` element spec.

## 9. Fields and comments (F8)

* **`insert_field`**: `field` one of `PAGE`, `NUMPAGES`, `SECTIONPAGES`, `SECTION`, `DATE`, `SAVEDATE`, `FILENAME`,
  `STYLEREF`, `DOCPROPERTY`, with its argument (`style`, `property`, `format` — the user's date picture or number
  format). Placement: `at_text` (every occurrence of the user's exact placeholder, which is removed; the field takes
  the placeholder's run formatting, placed by the edit core) or `position` `end`/`start` of the selected (targeted)
  paragraphs (the neighbouring run's formatting). A complex field with an EMPTY cached result (zero generated
  characters); fields Word does not recompute at layout are marked `w:dirty` so Word updates them when the file opens.
  Tracked by default (the field's runs inserted, a placeholder deleted). V10 kind `inserted_fields`.
* **`reply_to_comments`**: the user's words as a reply (author, initials) to the threads of the comments chosen (all,
  by author, by text pattern — a reply to a reply goes to its thread, as Word threads replies one level deep); the
  reply's marks go beside its thread's, as Word writes them, and it is linked in `commentsExtended.xml`
  (`w15:paraIdParent`), paragraph ids and the part added where missing. Untracked. V10 kind `comment_replies`.
* **`resolve_comments`**: mark the chosen threads done, or reopen them (`w15:done` on the thread's top comment; the
  replies' own entries are left as they are), adding `commentsExtended.xml` and paragraph ids where missing.
  Untracked. V10 kind `resolved_comments`.

## 10. `move_paragraphs` (F9)

The selected paragraphs, which must form one contiguous range in one container (tables lying between them move too),
go `before`/`after` the one anchor paragraph `to` selects, outside the range and in the same container (the body, the
same cell, header, footer, note or comment). Untracked: the blocks move unchanged. Tracked (the default): deleted where
they were and inserted where they go (Word shows a deletion and an insertion); a tracked move copies only paragraphs of
text runs, bookmarks and proofing marks — tables, fields, hyperlinks, content controls, comment anchors, pictures,
note references and pending revisions in it are refused. A section break in the range is refused; so is anything a move
would cut (a bookmark, comment, permission or field with one end inside), a destination inside a field's result (a
table of contents) and an origin that would leave two tables touching or a container ending in a table (refused, not
patched with a kept paragraph). V10 kind `moved_paragraphs`.

## Decisions for Abraham (each with the recommendation taken)

1. **No dedicated "nuke".** Wiping a document is `delete_paragraphs` over "all" with `remove_header_footer`,
   `remove_comments` and `remove_footnotes` in one request; it is planned and previewed like any edit.
2. **Content ops tracked by default, as before**; structure Word cannot track is untracked only and says so.
3. **Inserted fields carry an empty result** and are updated by Word; the engine never writes a page number itself.
4. **A tracked move is a deletion plus an insertion**, not Word's move markup; it reads the same in Word's review pane
   except for the "Moved" label.
5. **Text boxes stay refused** with the rest of Tier 3.

## Built (fill in per item as it lands)

### F7 — built (2026-10-21)

`insert_section_break` and `remove_section_break` (ops/insert_section_break.py, ops/remove_section_break.py, their
shared core ops/section_breaks.py) and `set_page_setup`'s `columns`, `line_numbering`, `vertical_alignment` and
`section_start`. V10: `inserted_section_breaks` and `removed_section_breaks` (verify/sections.py) rebuild the expected
main part from the source with their own code and compare it whole; the remove kind also re-derives the relationships,
content types and parts that go. Both ops change the main part as a whole for the ops beside them (`(main, "*",
"sections")`: section indices shift), so a page setup of the new section is a later step of the same request, chosen by
content. Refusal codes `break-position`, `tracked-section`, `last-section`. Tests: tests/test_sections.py (fixtures
tests/fx_sections.py; describe cases tests/describe_cases/sections.py and pages.py), LibreOffice renders of the pages a
break adds and removes and of two columns. Needs the owner's machine: a live request over real specifications, opened
in Word (a break before an appendix, a cover joined to the body, two columns).

### F9 — built (2026-10-21)

`move_paragraphs` (`ops/move_paragraphs.py`; V10 kind `moved_paragraphs` in `verify/moving.py`, which builds the expected
part from the source and compares it whole — `verified_parts` — after checking and masking the new revisions' ids and
dates, and holds the plan's diffs and accepted views to its own). `select.paragraphs` + `params.to` (one paragraph) +
`params.position` (`before`/`after`, required), `tracked` (default true), `author`, `allow_missing`. Decisions taken:

* **Container**: the parent of the shallowest chosen paragraph; the range is its children from the first to the last
  chosen child paragraph; the anchor must be a child of the same container (not merely the same part).
* **Validity**: an origin that would leave two tables touching or the container ending in a table is refused
  (`move-table-edge`), never patched with a kept paragraph; a stretch already in place is a noted no-op.
* **Bookmarks**: untracked they move with the blocks; tracked they travel with the inserted copy (the originals lose
  them), so accepting the move leaves every cross-reference pointing at the text — rejecting leaves them where the copy
  was (flagged). A bookmark, comment, permission, pending move range or complex field with one end in the range is
  refused (`move-split-range`); a destination inside a field's result (a table of contents) is refused (`move-anchor`).
* **Tracked markup**: the originals as `delete_paragraphs` deletes (one `w:del` per unbroken run sequence, the mark
  deleted); the copies as `insert_paragraph` inserts (one `w:ins` per unbroken run sequence, the mark inserted), each
  copy without `w14:paraId`/`w14:textId`. A stretch ending its container, or copies landing at its end, is flagged
  (Word keeps a container's last paragraph mark).
* **Numbering**: automatic list and heading numbers and footnote marks follow the new position (flagged); content
  controls (not data-bound), fields, comments and pending revisions move untracked as they are.
* Known limit: `between_paragraphs` has no "start included, end excluded" form; an article (its heading through the
  paragraph before the next heading) is `between_paragraphs` with `include_bounds` and a `text_regex` `^(?!<end>)`,
  which `describe()` reads as "through the one before". Its bounds read the paragraph text without tabs.

Tests: `tests/test_move_paragraphs.py` (fixtures `tests/fx_moving.py`, describe cases `tests/describe_cases/moving.py`).
Still needs the owner's machine: a live request over real specifications, both modes, opened in Word (accept all,
reject all).

### F5 — built (2026-10-21)

`delete_tables`, `delete_table_rows`, `insert_table_rows`, `insert_table` (§6 as refined above), the row selector and
grid (`ops/table_rows.py` ↔ `verify/tablerows.py`), the shared core `ops/table_edit.py` and the V10 kinds in
`verify/tablestructure.py`; `resolve_tracked_changes` resolves row insertions and deletions on both sides. New refusal
codes `table-anchor`, `vertical-merge`, `table-shape`, `table-placement`; `table-structure` and `revision-kind`
explanations updated. Tests: `tests/test_table_structure.py` (both sides of the row selector and the merge rule on every
row and every subset of rows of the fixture, each op tracked and untracked, refusals, wrong outputs and shared wrong
implementations, composition with `set_table_props`, the pipeline, LibreOffice) and `tests/test_table_rows_resolve.py`
(accepting the tracked output equals the untracked one, rejecting equals the source). Needs the owner's machine: a live
request per op on real specifications, opened in Word (the tracked table and row markup above all).

### F3 — built (2026-10-21)

`set_properties` takes `vertAlign`, `vanish`, `highlight`, `spacing` and `dstrike` on text and on style definitions
(`ops/effective.py` ↔ the `properties` kind in `verify/props.py`); `insert_paragraph` takes tabs and line breaks,
`runs` segments and several `paragraphs` (the `inserted_paragraphs` kind now compares each new paragraph's content
exactly: one new `w:ins`, each segment's run, its `w:t` / `w:tab` / `w:br` in order); `replace_text`'s replacement takes
tabs and line breaks through both sides of the edit core (`breaks=True`). The decisions the build settled are in §3,
§4.2 and §4.3 ("As built"). No new refusal code (`textbox` and `section-break` reused). Tests:
`tests/test_text_control.py` on `tests/fx_text.py` — end to end tracked and untracked, the "already as asked" no-ops,
wrong outputs and shared wrong implementations caught by V10, the two resolvers compared, `RPR_ORDER` pinned to the
bundled schema, parameters, placed words, the catalog, the batch pipeline and a LibreOffice render. Needs the owner's
machine: a live request over real specifications opened in Word (superscript, hidden text, highlight, condensed spacing,
double strikethrough; a numbered clause inserted with a tab after its number; a heading split by a line break).

### F4 — built (2026-10-21)

`insert_image`, `remove_images`, `resize_images` (ops/insert_image.py, ops/remove_images.py, ops/resize_images.py over
the shared ops/pictures.py; V10 kinds in verify/pictures.py). The picture readers (ops/images.py ↔ verify/images.py)
grow BMP and the resolution: PNG `pHYs`, JPEG JFIF density then Exif X/YResolution, BMP pixels per metre, 96 dpi
otherwise; swap_image still reads PNG, JPEG and GIF only. Settled in the build:

* every length from its decimal spelling, exact fractions, halves up; a size that rounds to 0 or exceeds the schema's
  largest drawing refuses (`image-size`, also a section without a usable page size); an unusable picture file refuses
  with `image-format` (SVG, a quarter-turned JPEG, anything that is not PNG, JPEG, GIF or BMP); a picture at its own
  size in a section with several text columns is flagged (it fills the whole text width);
* the new paragraph carries only `w:pStyle` (a given style; `missing-style` / `style-mismatch`) and `w:jc`; its run
  `w:noProof` and Word's inline picture; drawing ids count up from the package's highest (a `docPr` touch, so two
  insertions in one spec are composed into steps);
* a tracked removal splits a run that also holds text and refuses one carrying a pending `w:rPrChange`
  (`tracked-format`); an untracked one takes the relationships only the removed drawings name (a picture's hyperlink
  too) and the members nothing targets any more; a picture a field shows refuses (`field-result`); an emptied
  hyperlink or content control stays;
* `.bmp` joins the window's picture attachments (inputs.KINDS, the native dialog's filter); the browser picker's
  accept list in `frontend/src/lib/workbench.ts` still lists .png/.jpg/.jpeg/.gif.

Tested on fixtures (tests/test_pictures.py, tests/fx_pictures.py, tests/describe_cases/pictures.py): each op end to end,
tracked and untracked, every match, inline and floating, body, headers and footers; wrong outputs and shared wrong
implementations caught; refusals; conflicts and composed steps; the batch pipeline; a LibreOffice render. Needs the
owner's machine: a live request per op over real specifications, opened in Word (picture placement and the tracked
deletion's look in the review pane).

### F8 — built (2026-10-21)

`ops/insert_field.py` ↔ `verify/inserted_fields.py`, `ops/comment_threads.py` ↔ `verify/comment_threads.py`; tests
`tests/test_insert_field.py`, `tests/test_comment_threads.py` (fixtures `tests/fx_fields.py`, two builders added to
`tests/fx_comments.py`); describe cases `tests/describe_cases/fields_comments.py`.

* **`insert_field`.** The instruction is built from the request only: STYLEREF quotes the style's `w:name` (named by
  id or name) with `\l` / `\n`; DOCPROPERTY quotes a built-in property (Title, Subject, Author, Keywords, Comments,
  Category, Company, Manager, LastSavedBy) or a custom property the file has; FILENAME takes `\p` (`path`); a number
  format is `\* Arabic|roman|ROMAN|alphabetic|ALPHABETIC`, a date picture `\@ "…"` of d, M, y, H, h, m, s, AM/PM and
  the separators only. At a placeholder the edit core places one stand-in character, which is then replaced by the
  field's four runs, so the deletion, the place and the formatting are exactly the core's (tracked: in a new `w:ins`
  after the deletion, after someone's pending insertion when the placeholder is in one; untracked: the placeholder
  run's properties and attributes). Default scope: body, headers, footers (footnotes and endnotes on request). New
  refusal `field-argument` (no such style or property); a placeholder run with a pending formatting change is
  `tracked-format` in both modes; a paragraph edge inside a complex field (a table of contents) is `field-result`.
  `unlink_fields` now reads `(part, "fields", "verify")`, `insert_field` writes `(part, "fields", "add")`: both in one
  spec is a conflict, as the order would decide whether the new field stays.
* **`w:dirty`.** ECMA-376 Part 1 §17.16.18 (`fldChar/@dirty`): the field's result is stale and shall be recalculated
  when an application that can reads the document; set on the begin character. Word is reported to refresh such
  fields when it opens the file; the reports of Word's "update the fields in this document?" prompt trace it to
  `w:updateFields` in settings, which this op never sets. LibreOffice computes PAGE, NUMPAGES, DATE, FILENAME,
  STYLEREF and DOCPROPERTY with an empty result (rendered and checked); it shows nothing for SECTION, which Word
  computes.
* **Comment threads.** `which` is `all` | `authors` | `text_regex`; the threads are the roots of the chosen comments.
  A reply's range start goes after the thread's last one (when its range starts between paragraphs: at the start of
  the next paragraph — the same text), its end and reference run after the thread's last reference run, the run's
  properties copied; no date is written. New comment ids follow the largest in use; paragraph and durable ids are the
  smallest free from 1 (below 0x80000000) over the text and side parts. commentsIds / commentsExtensible gain the
  reply's entries when present; a top comment the comment ids leave out, dates without ids, or a side part with
  another root is `comment-metadata`; a comment without a single reference, marks in two parts or a range starting
  after its reference is `comment-anchor`. Both ops are untracked only (`tracked: false`), request-only
  (`catalog.REQUEST_ONLY_OPS`); a reply is the user's words (`compiler.placed_words`). Replies verify the comments
  part and side parts whole and each marked paragraph item by item, so they compose with untracked edits elsewhere
  (a tracked edit beside them is the usual mixed-mode refusal); resolving touches only commentsExtended (and the
  comments part when a paragraph id is added).
* **Needs the owner's machine:** whether Word, when it resolves a thread, also marks the replies' entries done (this
  machine has no Word and no Word-saved resolved thread; the build marks the top comment, as `remove_comments` reads a
  thread's state); that Word shows the replies in their threads; that it fills the dirty fields on open, and whether
  it asks first.

### F2 — built (2026-10-21)

`delete_paragraphs` (grown) and `remove_footnotes` (new) share one transform core, `ops/deletion.py` (the chosen blocks,
the keep rules, the tracked deletion and the untracked `Removal` cascade: notes → section breaks and header/footer parts
→ comments → range marks → fields → relationships and media); its twin is `verify/deletion.py` (kind
`deleted_paragraphs`, which replaces the old one in `verify/props.py`) with `verify/footnotes.py` (`removed_footnotes`).
Untracked, every changed part is rebuilt from the source and compared whole (`verified_parts`), the comment side parts
too, the relationships and `[Content_Types].xml` entry by entry; tracked, each changed paragraph is compared exactly
(NEW revisions matched by author, an id the source lacks and a date) and each deleted row's `w:trPr/w:del` checked,
the residual proving the rest (`strip_tr`). V4: a row's `w:trPr/w:del` counts as `del`. Untracked deletion claims each
changed part whole (beside another op on it, it runs as its own composed step); tracked keeps per-paragraph and per-row
touches. New refusal codes: `tables-join`, `field-crossing`, `comment-crossing`, `embedded-object`, `custom-mark`.
`resolve_tracked_changes` does not yet resolve these deletions: a deleted row is refused (`revision-kind`, F5's), a
deleted mark carrying a section break is refused on accept (`section-break`), and accepting deleted text that holds a
comment or note reference is refused (`marks-in-change`). Tests: `tests/test_deletion.py`,
`tests/test_remove_footnotes.py` (fixtures `tests/fx_deletion.py`). Real-file run: still the owner's.
