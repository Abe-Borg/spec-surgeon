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

* `select.paragraphs` may be `"all"` (and any predicate; the "targeted" requirement goes).
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
* **Comments and bookmarks.** A comment whose whole range lies inside the deletion goes with it (its `comments.xml`
  entry and replies, as `remove_comments` removes them); a comment crossing the edge is refused. Bookmarks never
  cause a refusal: one wholly inside goes; one crossing the edge keeps its outside half and its inside half moves to
  the edge of the surviving neighbour. A `REF`/`PAGEREF` elsewhere pointing at a removed bookmark is flagged.
* **Kept paragraphs** (rule 0.5): when the selection would empty a container or leave it ending in a table, or join
  two tables, the last selected paragraph there stays, emptied; flagged.
* Text boxes stay refused.

## 2. `remove_footnotes` (F2)

Remove footnotes and/or endnotes: `which` (`footnotes`, `endnotes`, `both`), optional `text_regex` on the note's own
text, scope the parts that carry references. Untracked: the reference run and the note's entry go (separators stay;
Word renumbers). Tracked: the reference run is deleted and the note's content deleted, as Word records it. A custom
reference mark (`w:customMarkFollows`) is refused. V10 kind `removed_footnotes`.

## 3. `set_properties` grows (F3)

`params.run` gains `vertAlign` (`superscript` | `subscript` | `baseline`), `vanish` (hidden, true/false), `highlight`
(Word's highlight colour names, or `none`), `spacing` (character spacing in points, negative = condensed, 0 = normal)
and `dstrike` (double strikethrough; turning it on turns `strike` off and the reverse, as Word does). Same selection,
tracking and "already as asked" rules as the existing run properties; the `properties` V10 kind grows its twin.

## 4. Richer text (F3)

### 4.1 `insert_paragraph`: tabs and line breaks
`text` may hold `\t` (written `w:tab`) and `\n` (written `w:br`, a line break in the same paragraph).

### 4.2 `insert_paragraph`: several paragraphs and segments
`paragraphs: [{text | runs, style?}]` instead of `text` inserts several paragraphs in order at each anchor; `runs:
[{text, b?, i?, u?, vertAlign?}]` gives a paragraph bold/italic/underlined segments. Every text is the user's, verbatim,
and is in `compiler.placed_words`.

### 4.3 `replace_text`: tabs and line breaks in the new text
The replacement may hold `\t` and `\n`, written as `w:tab` / `w:br` inside the inserted run(s); V3's structural counts
are declared (`struct_after`). Both sides of the edit core (`ops/textedit.py` ↔ `verify/edits.py`).

## 5. Pictures (F4)

Nearest relative: `swap_image` (the picture model `ops/images.py` ↔ `verify/images.py`, its `match` grammar).

* **`insert_image`**: an attached picture (PNG, JPEG, GIF, BMP; SVG refused) as its own paragraph `before`/`after`
  each anchor paragraph (targeted selector); `width`/`height` in inches (one given keeps the proportions; neither: the
  picture's own size at its resolution, 96 dpi when it has none, shrunk to the section's text width); `align`;
  `description` (alt text, the user's words); the new paragraph's style (default: the document's default paragraph
  style). Media `word/media/imageN.ext`, a relationship, a content-type default when missing. Tracked by default
  (the paragraph is a tracked insertion). V10 kind `inserted_images`.
* **`remove_images`**: the pictures `match` selects (file, checksum, name/description pattern, `all`) in the scope;
  the run holding each goes (tracked: deleted); media and relationships no longer used leave the package. Watermarks
  are `remove_watermark`'s. The paragraph stays. V10 kind `removed_images`.
* **`resize_images`**: the pictures `match` selects get `width`/`height` in inches or `scale` in percent (proportions
  kept unless both are given); inline and floating; formatting, untracked. V10 kind `resized_images`.

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
* **`remove_section_break`**: the break at the end of each chosen section (index or content, `set_page_setup`'s
  selector) goes; its content joins the following section and takes its setup and headers (Word's behaviour); header/
  footer parts no longer referenced leave the package; the paragraph that held the break stays. The last section's
  properties cannot be removed. Untracked only. V10 kind `removed_section_breaks`.
* **`set_page_setup`** gains `columns` (`{count, space, separator}`; count 1 = one column), `line_numbering`
  (`{count_by, start, restart, distance}` or `none`), `vertical_alignment` (`top` | `center` | `both` | `bottom`) and
  `section_start` (`next_page` | `continuous` | `even_page` | `odd_page`).

## 9. Fields and comments (F8)

* **`insert_field`**: `field` one of `PAGE`, `NUMPAGES`, `SECTIONPAGES`, `SECTION`, `DATE`, `SAVEDATE`, `FILENAME`,
  `STYLEREF`, `DOCPROPERTY`, with its argument (`style`, `property`, `format` — the user's date picture or number
  format). Placement: `at_text` (every occurrence of the user's exact placeholder, which is removed) or `position`
  `end`/`start` of the selected paragraphs. A complex field with the neighbouring run's formatting and an EMPTY cached
  result (zero generated characters); fields Word does not recompute at layout are marked `w:dirty` so Word updates
  them when the file opens. Tracked by default (the field's runs inserted, a placeholder deleted). V10 kind
  `inserted_fields`.
* **`reply_to_comments`**: the user's words as a reply (author, initials) to the comments chosen (all, by author, by
  text pattern); the reply shares its parent's anchor and is linked in `commentsExtended.xml` (`w15:paraIdParent`),
  paragraph ids and the part added where missing. Untracked. V10 kind `comment_replies`.
* **`resolve_comments`**: mark the chosen threads done, or reopen them (`w15:done`), adding `commentsExtended.xml`
  and paragraph ids where missing. Untracked. V10 kind `resolved_comments`.

## 10. `move_paragraphs` (F9)

The selected paragraphs, which must form one contiguous range in one container (tables lying between them move too),
go `before`/`after` the one anchor paragraph `to` selects, outside the range and in the same part. Untracked: the
blocks move unchanged. Tracked: deleted where they were and inserted where they go (Word shows a deletion and an
insertion); tables, fields, hyperlinks and comment anchors in a tracked move are refused. A section break in the range
is refused. V10 kind `moved_paragraphs`.

## Decisions for Abraham (each with the recommendation taken)

1. **No dedicated "nuke".** Wiping a document is `delete_paragraphs` over "all" with `remove_header_footer`,
   `remove_comments` and `remove_footnotes` in one request; it is planned and previewed like any edit.
2. **Content ops tracked by default, as before**; structure Word cannot track is untracked only and says so.
3. **Inserted fields carry an empty result** and are updated by Word; the engine never writes a page number itself.
4. **A tracked move is a deletion plus an insertion**, not Word's move markup; it reads the same in Word's review pane
   except for the "Moved" label.
5. **Text boxes stay refused** with the rest of Tier 3.

## Built (fill in per item as it lands)

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
