# Built-in operations, batch 6 — design note: full control

2026-10-21. Abraham's direction: the user needs precise and broad control, from
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
* **Over someone's pending revisions** (added 2026-10-22): text in a
  pending insertion is deleted inside it (the new `w:del` nested in their `w:ins`), text already deleted stays as it
  is, a paragraph mark someone inserted gets the new `w:del` beside their `w:ins`, property changes stay. Moved text,
  an inserted or deleted row, a cell change, and a kept paragraph whose mark is already deleted are refused
  (`existing-revisions`).
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
`highlight` is text-only: with `select.styles` it is `bad-params` ("Word does not show highlighting set in a style;
highlight the text directly") — Word's styles carry no highlighting (LibreOffice writes a style's background as
shading), so a style's `w:highlight` would very likely show nothing in Word. `vanish: true` reaches a paragraph mark only
when, after the change, every content run of its paragraph (a run holding more than its properties) is hidden —
selected, or hidden already: Word joins a paragraph whose mark is hidden to the next one, so a mark beside text that
stays visible is left as it is and flagged (counted in the assertion, `expected_shown_marks`); `vanish: false` on a mark
is unchanged.

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
  picture's own size at its resolution, 96 dpi when it has none, shrunk in proportion to fit the anchor section's text
  area, width and height — in a header or footer the narrowest and shortest of every section's, in a fixed-width table
  cell the cell less its margins); `align`; `description` (alt text, the user's words); the new
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
  `after` a section break (`section-break`); a column narrower than 0.1 inch (144 twips), equal or given
  (`bad-params`, naming the count that fits the text width — Word's dialogs refuse such a column, and a table of 100
  columns renders without its text). Tracked by default. V10 kind `inserted_tables`.
* **`resolve_tracked_changes`** accepts and rejects tracked row insertions and deletions (accept an inserted row: the
  marker goes, an emptied `w:trPr` with it; accept a deleted row: the row goes with all it holds; reject the
  reverse), both sides. A row that goes is content that goes for its anchor rules (a bookmark end inside whose
  partner stays → `marks-in-change`, unlike the deletion ops' keep rule); a table whose every own row goes goes whole
  under the container rule (`table-structure`); a row in a content control (`table-structure`) and a moved row, cell
  insertions/deletions/merges (`revision-kind`) stay refused.

## 7. Table grid and cells (F6) — after F5 (the row selector)

Nearest relatives: `set_table_props` (cell elements written and checked in schema order, "already", the residual
strips), F5's row ops (the row selector, the grid, `table_edit.removal` for what deleted cells may hold). As built:
column selector and grid `ops/table_columns.py` ↔ `verify/tablecolumns.py`, V10 kinds in `verify/tablegrid.py`.
Untracked only (`tracked` accepts only false): Word cannot track a column, a cell's properties or a merge.

* **Grid** (both sides). The grid columns are the `w:gridCol` of the table's one `w:tblGrid`, numbered from 0, a
  column's width its `w:w`. A row lays out `w:gridBefore` columns, its own cells over their spans (F5's), then
  `w:gridAfter`; a cell **covers** its columns. A table whose rows, cells or grid cannot be numbered is refused before
  anything is chosen (`table_columns.check_numbered`): no own row, rows or cells in a content control, a row without a
  cell (`table-structure`); no single `w:tblGrid`, no grid column, a before/after count that is not a whole number, a
  row running past its grid (`table-grid`) — the grid only when columns or a width are asked.
* **Column selector** `select.columns`: `"all"` or `{index: [grid column numbers, distinct], header_regex}` — every
  given key must hold. `index` is a LIST of columns, not a range (`[1, 3]` is columns 1 and 3). `header_regex` reads
  the cell of the table's first own row covering the column (its paragraphs joined by a space); a column no first-row
  cell covers has no header and is never chosen by it, and a header cell spanning columns brings them all.
* **`delete_table_columns`** (`select.columns` required). A cell over chosen columns only goes with its content (V2: its
  paragraphs deleted; a nested table goes with it and is not looked at); a cell over chosen and kept columns keeps its
  content, its `w:gridSpan` less the chosen columns (gone at 1) and a `dxa` `w:tcW` less their widths; a row's
  `w:gridBefore`/`w:gridAfter` less the chosen columns it counts (gone with `w:wBefore`/`w:wAfter` at 0, an emptied
  `w:trPr` with them; else a `dxa` width less theirs); the chosen `w:gridCol` go; a fixed table width (`w:tblW` `dxa`
  above 0) shrinks by their widths. Widths in percent stay (flagged: Word refits; a table in percent keeps its width,
  so the columns left widen). Refused: every grid column (`every-column` — delete the table), Word's legacy
  `w:hMerge` (`table-grid`), pending tracked changes of the table's structure or properties (`w:tblPrChange`,
  `w:tblGridChange`, `w:trPrChange`, `w:tblPrExChange`, `w:tcPrChange`, `w:cellIns`/`cellDel`/`cellMerge` —
  `existing-revisions`), a row left without a cell (`table-grid`: its other columns are gridBefore/After space — delete
  the row first), a vertical merge some of whose cells go while others stay (`vertical-merge`), a width that would not
  stay above 0 or a missing column width where one must be taken off (`table-grid`); over the deleted cells,
  `table_edit.removal`'s untracked rules (a crossing bookmark keeps its outside end; its inside end takes the cell's
  place in the row). Pending revisions inside deleted cells go with them (V4 negative delta). V10 builds the part
  (F5's `_v_take` for the cells) and compares it whole.
* **`set_table_cells`** (formatting, no text diffs). Cells: the chosen tables' own cells in the chosen rows (default
  every row) that cover a chosen column (default every column) — a cell spanning a chosen column counts. `shading`
  (RRGGBB → exactly `w:shd` `{clear, auto, fill}`; `"none"` → `{clear, auto, auto}` where the nearest table-style
  level whose `w:tcPr` holds a `w:shd` shows shading, else no `w:shd`), `vertical_alignment` (always an explicit
  `w:vAlign`), `no_wrap` (true `<w:noWrap/>`; false `w:val="0"` only where the style's `w:tcPr` turns it on, else
  none), `width` (inches, with every row only — a row selection with a width is `bad-params`): each chosen `w:gridCol`
  gets it; every cell over a chosen column gets `w:tcW` dxa = its columns' widths summed (new for chosen, the grid's
  for others); a row's `w:wBefore`/`w:wAfter` over a chosen column likewise when present; **a fixed `w:tblW` follows
  by the chosen columns' change** (decision added: otherwise Word would scale the asked widths to the old table
  width); a table in percent keeps it (flagged). "Already as asked" per element. Unlike the other two it does not
  claim the part: it touches each changed cell (`tc:k:r:c` `tcPr` + names), row (`trPr`), table (`tblProps`, `tblW`)
  and grid (`tblGrid`), reads the style chain only for `none`/`no_wrap false`, and V10 checks each changed element
  whole in schema order, then strips it from the residual — the grid by a new `ctx.strip_grid`.
* **`merge_table_cells`** (`select.rows` and `select.columns` both required; any row and column selector, but what
  they choose in a table must be one unbroken block a…b × c…d, else `table-shape`). The covered cells (each row's own
  cells over c…d) must lie wholly inside (`merge-overlap`) and cover it (`table-shape`); a vertical merge partly inside,
  or a cell just below a several-row block that continues a merge and would join it, is `merge-overlap`. Content: every
  covered cell's children but `w:tcPr` (paragraphs, nested tables, range marks) in reading order into the anchor (the
  first covered cell); an **empty** cell (only paragraphs of properties and bare runs, no tracked change) adds nothing,
  an empty anchor's own paragraphs go too; when all are empty the anchor keeps its own. Each row keeps its first
  covered cell with its `w:tcPr`: `w:gridSpan` the block's width, `w:tcW` the row's widths summed when they are all of
  one type (`dxa` or `pct`; else the first cell's stays, flagged), `w:vMerge` restart/continue over several rows (none
  over one); a continuing cell holds one new paragraph with its first paragraph's properties cleaned as F5's new rows
  take them. A block that is already one cell (1×1 included) is left alone with a note. Also refused: `w:hMerge`
  (`table-grid`), pending structure changes or a row of the block inserted/deleted as a tracked change
  (`existing-revisions`), a covered cell without a paragraph (`table-structure`), and a range or field with one part
  moving with the content and another staying between the anchor and it (`table-anchor`: the merge would put it out of
  order). V2: dropped empty paragraphs are deletions, each continuing cell's paragraph an insertion, and a moved
  paragraph that passes a paragraph staying put (a cell beside a several-row block) a deletion plus an insertion. The
  op's mode is always `untracked` (a spec's mode must be predictable from its ops). V10 merges the cells itself and
  compares the part whole.

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

* Review fix (2026-10-21): `insert_section_break` flags a next-page, even- or odd-page break whose carrier paragraph ends
  with a manual page break (`w:br w:type="page"` after the last thing it shows): the two together leave a blank page in
  Word (remove the page break, or use a continuous break). A note only — the output is unchanged. Test:
  `test_a_new_page_break_after_a_manual_page_break_is_flagged` (fixture `fx_sections.build_page_break`).

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
* `between_paragraphs` takes `include_bounds` "start" (an article from its heading up to, not including, the next)
  and `every: true` (every such stretch, from each start match to the next start or end match: every article's body,
  every PART 2 — a move needs one stretch, so it refuses the gap); the older idiom of `include_bounds` with a
  `text_regex` `^(?!<end>)`, which `describe()` reads as "through the one before", still works. Its bounds read the
  paragraph text without tabs.

Tests: `tests/test_move_paragraphs.py` (fixtures `tests/fx_moving.py`, describe cases `tests/describe_cases/moving.py`).
Still needs the owner's machine: a live request over real specifications, both modes, opened in Word (accept all,
reject all).

Review fixes (2026-10-21), both sides each:

* **Ranges at the destination**: `after` goes past the range-end marks (bookmark, comment range, permission, pending
  move range) right after the anchor, `before` before the range-start marks right before it — the stretch no longer
  lands inside a bookmark that closes with the anchor; a destination still inside a range the stretch is not wholly in
  already (an end mark at the start of the next paragraph, as Word ends a bookmark over whole paragraphs) is refused
  (`move-split-range`). The plan carries the destination (`at`), apply places beside it.
* **The last section**: a stretch that is all of the body's last section (after its last section-break paragraph),
  moved before that break, is refused (`move-last-section`): Word would show an empty last page.
* **Comment threads**: in a comment one of whose paragraph ids a commentsExtended or commentsIds entry names, a move
  that takes the comment's last paragraph or lands after it is refused (`comment-last-paragraph`).

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

* 2026-10-21, review fix: `insert_table_rows` keeps the repeating header rows whole — the run of header rows from row 0
  (`table_rows.header_run` ↔ `tablerows.v_header_top`); unmarked rows before or among them, or `repeat_header` rows that
  would not join them, refuse with the new code `header-rows`.

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

* Review fix (2026-10-21): hidden text no longer hides a paragraph mark whose paragraph keeps visible text (Word would
  join it to the next paragraph) — both sides re-derive which content runs stay visible (`set_properties.stays_visible`
  ↔ `verify/props._properties_direct`), the mark is left alone and flagged; highlight on a style is refused
  (`bad-params`; the kind refuses it too). Tests: `tests/test_text_control.py` on `fx_text.build_notes()` — end to end
  tracked and untracked, a mark hidden anyway and a mark left visible caught, three shared wrong rules caught. Needs the
  owner's machine: hiding red specifier notes in a real specification opened in Word (no paragraph joined).

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

* 2026-10-21, review fix: a picture at its own size fits the text height too (page height less the top and bottom
  margins; a header or footer: the shortest section), and in a table cell whose `w:tcW` is in twips the cell's width
  less its margins (`tcMar`, else `tblCellMar`, else 108 twips; another width is flagged); a size given is kept,
  flagged when larger.
* 2026-10-21, review fix: a new picture paragraph whose style chain (with its table's and docDefaults) gives exact line
  spacing gets `<w:spacing w:line="240" w:lineRule="auto"/>`, flagged (an exact line crops an inline picture).
* 2026-10-21, review fix: an untracked removal that would empty a run carrying a pending `w:rPrChange` refuses
  (`tracked-format`), as a tracked one that would split it does.

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
* **Review fix (2026-10-21): whole-word placeholders.** An `at_text` occurrence counts only when it is not part of a
  longer word — a word character (letter, digit, underscore) at the placeholder's start may not follow one, at its end
  may not be followed by one (`insert_field.placeholder_pattern` ↔ `verify/inserted_fields._v_whole_words`): "Page X
  of Y" no longer turns "EXTERIOR" into a page number, nor "UPDATE" into a date; `{{PAGE}}` is unaffected. A page or
  section number (PAGE, NUMPAGES, SECTIONPAGES, SECTION) put at a placeholder in the body is flagged (it belongs in a
  header or footer); the default scope stays body, headers and footers. Tests: `tests/test_insert_field.py` on
  `fx_fields.build_placeholders()` — whole words end to end tracked and untracked, the body flag, the two readings
  compared, a field inside a word caught, a shared substring search caught. Needs the owner's machine: "Page X of Y"
  asked in plain English over real specifications (only the footer's X and Y become fields).

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

Review fixes (2026-10-21), both sides each:

* **`is_empty`** (the selector, `ops/selectors.holds_object` ↔ `verify/props._v_is_empty`): a paragraph with no text
  is empty only when it holds no picture, drawing, Office choice (`mc:AlternateContent`), embedded object, text box or
  field (`w:fldSimple`, any `w:fldChar`) — deleting "empty paragraphs" took logos and tables of pictures. Every chosen
  picture or object and every whole table a deletion takes is flagged in plain words ("2 pictures and 1 table go with
  the deleted paragraphs"; tracked: "are deleted with the paragraphs").
* **The last section** (keep rule 4): in the body, when every block after the last staying section-break paragraph is
  chosen, its last chosen paragraph stays, emptied (tracked: runs deleted, mark kept), flagged "the last section is now
  empty and shows a blank page; remove the section break before it to drop the page". `remove_empty_paragraphs` keeps
  the same rule (`ops/empty_paragraphs.blank_paragraphs` calls `last_section_keep`, `verify/removal._v_blank_decisions`
  calls `_v_last_section`): with `mode` "all" a last section of blank paragraphs keeps its last one, flagged. So do
  `remove_notes` and `remove_hidden_text` (`ops/hidden._Removing._plan_part` calls `last_section_keep`,
  `verify/removal._v_decide` calls `_v_last_section`): after the hidden paragraphs that cannot be deleted are kept (a
  hidden section-break paragraph that stays counts as a break) and before a note that cannot be deleted is refused (the
  one it keeps is emptied instead).
* **Printer settings**: a printer-settings relationship only removed sectPrs named goes like a header's (the part, with
  its content-type entry, when nothing else targets it) instead of refusing the deletion as `embedded-object`.
* **Comment threads**: deleting the last paragraph of a comment a commentsExtended or commentsIds entry names, while the
  comment stays, is refused (`comment-last-paragraph`); the whole comment going with its anchored text is not.

### F6 — built (2026-10-21)

`delete_table_columns`, `set_table_cells`, `merge_table_cells` (§7 as refined above): the column selector and grid
(`ops/table_columns.py` ↔ `verify/tablecolumns.py`), the V10 kinds `deleted_table_columns`, `table_cells` and
`merged_table_cells` (`verify/tablegrid.py`), and `ctx.strip_grid` in `verify/props.py` (a table's `w:tblGrid` a kind
compared whole leaves the residual). New refusal codes `every-column`, `table-grid`, `merge-overlap`; `table-shape`,
`table-anchor` and `vertical-merge` explanations extended. Tests: `tests/test_table_grid.py` (fixtures
`tests/fx_table_grid.py`, describe cases `tests/describe_cases/table_grid.py`): both sides of the column selector and the
grid on every column and row of the fixture, each op end to end (fixed and percent widths, gridBefore/After, spanning
and merged-down cells, a nested table merged inside a merge, the header part), refusals with codes, wrong outputs and
shared wrong implementations caught, conflicts with `set_table_props`, `apply_table_style` and F5's row ops and their
composition into steps, the catalog, the pipeline and a LibreOffice render. Known limits: a row's own table width
(`w:tblPrEx/w:tblW`) is left as it is; Word's cached conditional formatting (`w:cnfStyle`) of cells that change column
is left for Word to refresh. Needs the owner's machine: a live request per op on real specifications, opened in Word
(a deleted Notes column, a shaded header row, column widths, a merged title row and a merged block down a column).
