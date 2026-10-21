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
(reading an attached table file through `inputs.py`).

* **Row selector** (both sides, new): `{index_range: [a, b]` (0-based, inclusive, among the table's own rows),
  `text_regex` (the row's text, cells joined by a tab), `header: bool` (rows repeated as header rows)}; every given key
  must hold.
* **`delete_tables`**: the tables the table selector chooses; tracked (rows marked deleted, content deleted) or
  untracked (the `w:tbl` goes). V10 kind `deleted_tables`.
* **`delete_table_rows`**: the rows the row selector chooses in the chosen tables; tracked (`w:trPr/w:del`, content
  deleted) or untracked. A vertical merge crossing the edge of the deletion is refused; deleting every row deletes the
  table. V10 kind `deleted_table_rows`.
* **`insert_table_rows`**: `rows: [[cell text, …], …]` (the user's words) `before`/`after` the one row the row selector
  chooses per table, or `at_end`; each new row copies the reference row's row, cell, paragraph and first-run
  formatting; a row of the wrong cell count, or a reference row with merged cells, is refused. Tracked
  (`w:trPr/w:ins`, runs inserted) or untracked. V10 kind `inserted_table_rows`.
* **`insert_table`**: a table from an attached CSV/XLSX (`table`, `sheet`) or typed `rows`, `before`/`after` each
  anchor paragraph; `header_rows` repeated; `style` (a table style the document has; none: single-line borders);
  full text width, equal columns unless `column_widths` (inches). One paragraph per cell, the text verbatim. Tracked
  by default, as rows inserted. V10 kind `inserted_tables`.
* **`resolve_tracked_changes`** accepts and rejects tracked row insertions and deletions (accept an inserted row: the
  mark goes; accept a deleted row: the row goes; reject the reverse), both sides.

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
