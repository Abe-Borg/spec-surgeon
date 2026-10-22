# Built-in operations, batch 2 — design note

2026-10-16. Abraham's request, the second chunk of prebuilt operations (items 11–21 of his list; ★ = he starred it):

| # | Asked for | Built as | Mode |
|---|---|---|---|
| 11 ★ | Remove comments: all, resolved only, or by author; text unchanged | `remove_comments` | formatting |
| 12 ★ | Remove highlighting: all, or only certain colours | `remove_highlight` | formatting (tracked on request) |
| 13 | Accept or reject tracked changes: all, by author, or by date | `resolve_tracked_changes` | untracked |
| 14 | Remove hidden text, listing every removed piece in the preview | `remove_hidden_text` | content (tracked) / untracked |
| 15 | Remove specifier or editor notes ("Retain… / Delete…" paragraphs in note styles, or blue or hidden text) | `remove_notes` | content / untracked |
| 16 | Add or remove a watermark ("DRAFT", "NOT FOR CONSTRUCTION"); the wording is the user's | `add_watermark`, `remove_watermark` | untracked / formatting |
| 17 | Remove empty paragraphs, or collapse runs of blank lines | `remove_empty_paragraphs` | content / untracked |
| 18 | Remove hyperlinks but keep the words | `remove_hyperlinks` | formatting |
| 19 | Convert fields such as DOCPROPERTY to plain text; page numbers never | `unlink_fields` | formatting |
| 20 | Set document properties (title, company) or clear author names | `set_document_properties` | untracked / formatting |
| 21 | Turn off the track-changes setting, or mark the file as final | `set_document_state` | formatting |

Status: **built** (see "Built: departures from this note" at the end). Decisions below were taken on Abraham's behalf, as in batch 1, and
can each be changed. Reading order: CLAUDE.md hard rules 1–4 and 8–13, "Run text and in-paragraph edits" (the item
model), `docs/BUILTIN_OPS_BATCH1_DESIGN.md` §0.

## 0. Three rules of the method change — at Abraham's request, and only this far

Items 13, 20 and 21 were each marked "(rule)" in the list: the method (`skill/SKILL.md`, `references/element-types.md`)
said *the model never accepts or rejects tracked changes — the user does, in Word*, *docProps are Word's: do not update
them*, and *`w:trackRevisions` is the user's choice — never toggle it*. Abraham asked for the three operations anyway. The
rules now read: **never on the model's own initiative; only when the user asks for it by name**, through a built-in
operation whose preview says exactly what it does (`resolve_tracked_changes` lists every revision it accepts or rejects,
by kind and author, as flags; `set_document_properties` names every property and value; `set_document_state` says
"track changes turned off" / "marked as final"), and whose output is verified like every other — the accept/reject
result against the verifier's **own** resolution of the source, the properties and settings member for member. A custom
transform (codegen) still can never do any of the three (`codegen.hard_limits` is unchanged: no revision delta, no part
outside the text parts and styles). Nothing else in the method changes.

## 1. What the batch shares

### 1.1 Structure-only text-block declarations (V2/V3)

Removing a comment's reference mark, a hidden tab or a field's markers changes a paragraph's structural children (V3)
without changing its text (V2). Until now a structural change could only be declared beside a text change. A
`BlockDiff` whose `before == after` (both not None) **with** `struct_after` is now a *structure-only declaration*: V2
leaves it out of the expected text changes (it is not one), and V3 accepts it only for that block (`block_index` equal
to the changed block's source index, a stricter match than the text-only match the older declarations use) with exactly
the declared counts; a declared change that did not happen fails V3. A declaration without `struct_after`, or one whose
block changes text after all, still fails. It never exempts the paragraph from V10's residual: the kind that made the
change checks the paragraph item by item (`verified_blocks`), or the residual compares it whole — so nothing else in that
paragraph can change unseen (Codex on #27). The `expect` counts (`text_diffs` …) and the plan summary leave
structure-only declarations out of "text diffs".

### 1.2 Verified parts, members and removed elements (V10)

* `Ctx.verified_parts` — text parts a kind compared WHOLE with its own expected tree (`resolve_tracked_changes` on every
  part it touches; `remove_comments` on the comments part). The residual skips them: the kind's comparison is the
  residual. Such an op touches the part as a whole member (rule 9), so no other op can have changed it.
* `Ctx.expected_removed` — elements outside paragraphs that a kind expects gone (a comment's range markers between two
  blocks): the residual removes exactly those from its copy of the SOURCE before comparing, so the output must lack them
  and nothing else may differ.
* `Ctx.verified_members` — non-text members a kind compared whole (`commentsExtended.xml`, a rels member a link removal
  edited, a created `docProps/custom.xml`, the package rels and `[Content_Types].xml` it gained an entry in).
* **The member residual** — `settings.xml` and `docProps/core.xml`, `app.xml`, `custom.xml` used to be untouchable (the
  method), so no kind looked at them. Now any change to one of them must be covered: a kind registers the keys it
  verified (`Ctx.member_strip`: a top-level child's qualified name, or `property:<name>` in `custom.xml`); the residual
  strips exactly those on both sides and compares the rest element for element. A changed member nobody registered
  fails — no op can touch them as a side effect.

### 1.3 The item model, again

Every in-paragraph change of this batch is checked like batch 1's text edits (`verify/edits.py`): the verifier derives
the paragraph's **expected items** from the SOURCE paragraph by its own reading of the rule, compares them one for one
with the output paragraph's items (and the `w:pPr`/attributes), and registers the block in `verified_blocks`. New
item transforms (`verify/markup.py`): *drop* items (a comment mark, a field's markers, a hidden run untracked), *unwrap*
a container from every chain below it (a hyperlink, a simple field), *restyle* items (the `w:rStyle` of the link style
gone from their run-property signature), *tracked whole-run deletion* (every item of the run gains a NEW `w:del`; `w:t`
→ deleted text, `w:instrText` → `w:delInstrText`), *insert* an item (the watermark run).

### 1.4 Whole runs and whole paragraphs

Hidden text, inline notes and blank paragraphs are removed by whole runs / whole paragraphs, not by the run-text edit core:

* **Whole-run removal** (`ops/removal.py`, verifier `verify/markup.py`): untracked — the run is removed from wherever it
  sits (containers stay); tracked — the run is wrapped in a `w:del` of its own (new id, author, date) in place, its
  `w:t` become `w:delText` and `w:instrText` become `w:delInstrText`. A run already inside `w:del`/`w:moveFrom` is not
  live and never selected. Delta: `del` +1 per run (tracked).
* **Paragraph deletion** — the rules of `delete_paragraphs`/`filter_tagged_paragraphs` (now `ops/removal.py`, which
  `filter_tagged` imports): tracked — every direct run in its own `w:del`, the mark gets a `w:del`; untracked — removed.
  Refused: a section break (`section-break`), a field (`field-result`), a text box (`textbox`); tracked also existing
  revisions (`existing-revisions`) and content other than runs, bookmarks and proofing marks (`structure`); untracked a
  table cell's last paragraph (`table-cell`).
* **Range marks an untracked removal keeps** (added 2026-10-21; `removal.kept_marks` ↔ `verify/removal.v_kept_marks`,
  batch 6's step 4 read for whole paragraphs and whole simple fields): a bookmark, permission, move or custom-XML range
  mark in a deleted paragraph or a removed simple field, some mark of whose pair lies in what stays, stays in the place
  of that paragraph or field (flagged), so the bookmark keeps both ends and a REF field to it still finds it; a pair
  wholly removed goes. The plan names the kept marks (`marks` / `expected_marks`: per paragraph, their indices among its
  range marks); the verifier derives them again, checks a removed field's paragraph item by item and has the residual
  put a deleted paragraph's kept marks in its place (`ctx.kept_marks`). Used by `remove_hidden_text`, `remove_notes` and
  `filter_tagged_paragraphs`; a blank paragraph holds no mark.

### 1.5 Fields (`ops/fields.py`, verifier `verify/fields.py`)

A both-sides definition. **Complex field**: scanning the part's runs in document order (every `w:r`, any depth, text
boxes included), `w:fldChar w:fldCharType="begin"` opens a field (nested fields push), `separate` moves the innermost open
field from its code to its result, `end` closes it. Its **instruction** is the text of the `w:instrText` (or
`w:delInstrText`) elements met while it is the innermost open field and in its code; a field that begins while another
is in its code is *nested in that field's code*. Its **type** is the instruction's first word, upper-cased (`=` counts).
**Simple field**: `w:fldSimple`, instruction `w:instr`. A field is **local** when its begin, separate (if any) and end
lie in one paragraph that is not inside a text box. **Unlinking** a field removes its `begin`, `separate` and `end`
`w:fldChar` and the `w:instrText` of its own code, then every run left holding nothing but `w:rPr`; a simple field is
replaced by its children. What the field showed (its result runs) stays, with its formatting. Refused for a selected
field: not local (`field-span`), a field nested in its code (`nested-field`), a form field (`form-field`: FORMTEXT,
FORMCHECKBOX, FORMDROPDOWN or a `w:ffData`), a marker or instruction inside a tracked change (`existing-revisions`).
**Page numbers are never unlinked**: PAGE, NUMPAGES, SECTIONPAGES, PAGEREF (refused when named: `bad-params`).

### 1.6 Effective hidden and colour (both sides)

Hidden (`w:vanish`) is a toggle: direct formatting wins; specified at exactly one style level (docDefaults, the table
style chain, the paragraph style chain, the character style chain) → that value; nowhere → off; more than one → refused
(`toggle-ambiguous`; Word XORs them, not modelled) — the order and rule of `set_properties`' toggles. A paragraph mark's
hidden: its `w:pPr/w:rPr` direct, else the paragraph style chain and docDefaults. Colour (`w:color`): the nearest level
that sets it, default `("auto", None, None, None)` — `set_properties`' colour rule. A colour list matches a 6-digit hex
(`w:val`, case-insensitive), a theme colour name (`w:themeColor`, case-insensitive) or `auto`. Transform:
`ops/effective.PropResolver` levels; verifier: `verify/props._v_run_levels`/`_v_para_levels` with this module's toggle.

### 1.7 A file with nothing to remove

The clean-up operations (`remove_comments`, `remove_highlight`, `resolve_tracked_changes`, `remove_hidden_text`,
`remove_notes`, `remove_empty_paragraphs`, `remove_hyperlinks`, `unlink_fields`, `remove_watermark`) run over whole
batches in which many files have nothing to remove. Their `allow_missing` defaults to **true**: such a file plans as a
no-op with a note saying so ("no comment matched: nothing to do") and is reported unchanged — never set aside as
`not-found`. `allow_missing: false` makes it an exception again, for a user who wants to hear about those files.
(Batch 1's ops keep `false`: a replacement that finds nothing usually means the wrong words.)

## 2. `remove_comments` (item 11)

`which`: `all` (default) | `resolved` | `authors` (+ `authors`: names, compared casefolded with whitespace collapsed);
`allow_missing`.

* A **comment** is a `w:comment` of the comments part (the main part's `comments` relationship); its paraIds are the
  `w14:paraId` of its paragraphs. **Threads** come from `commentsExtended.xml` (`w15:commentEx`, matched to a comment by
  any of its paraIds): `paraIdParent` makes a reply; a comment's root follows the parents (cycle-safe). **Resolved**: the
  root's `w15:done` is on (a whole thread is resolved or not). Selected: `all` → every comment; `resolved` → every
  comment of a resolved thread; `authors` → every comment by a listed author **and every reply below a selected
  comment** (a thread never loses its root and keeps its replies). Ids R.
* Removed: each `w:comment` in R; the `w15:commentEx` and `w16cid:commentId` whose paraId is one of R's paraIds; the
  `w16cex:commentExtensible` whose `durableId` one of those `commentId`s carried; in every other text part the
  `w:commentRangeStart`/`w:commentRangeEnd` with an id in R and every `w:commentReference` with an id in R (its run too
  when nothing but `w:rPr` is left). **Parts are never removed**, even when emptied (an empty `w:comments` is valid and
  Word opens it); relationships stay — a picture or link only a removed comment used stays in the package, unused, flagged.
* Refused: a mark of a removed comment inside a text box (`textbox`); two comments with one id (`structure`).
* Predictions: every block of a removed comment is deleted (V2, `after=None`); a paragraph that loses a reference mark is
  a structure-only declaration (V3); revisions inside removed comments go (V4, flagged). Kind `removed_comments`: R
  re-derived from the source with the verifier's own thread reading; the comments part and each side part compared whole
  with the source minus the entries (`verified_parts` / `verified_members`); each paragraph that held a mark by the item
  model; marks outside paragraphs through `expected_removed`.
* Touches: the comments part and each side part as whole members; `(part, block, "text")` for a paragraph with a mark;
  `(part, "comment-mark:<id>", "remove")` for a mark outside paragraphs. Layout reference `source`.

## 3. `remove_highlight` (item 12)

`colors` (default: every highlight colour — `w:highlight` values other than `none`; names are Word's: yellow, green,
cyan, magenta, blue, red, darkBlue, darkCyan, darkGreen, darkMagenta, darkRed, darkYellow, darkGray, lightGray, black,
white, any case); `tracked` (default false: whole-document formatting is plain, decision §14.1); `scope` (default body,
headers, footers, footnotes, endnotes).

* Targets: every run (any container, deleted runs too — a rejected deletion must not bring a highlight back) and every
  paragraph mark of the scope whose own `w:rPr` has a `w:highlight` in the list. Never: a `w:rPrChange` snapshot (that is
  history), a style definition (a style in use that highlights is flagged — change it in Word).
* Result: the `w:highlight` is removed; an `w:rPr` left empty is removed. Tracked: a `w:rPrChange` (new id) with a
  snapshot of the source `w:rPr` is added; a target already carrying one is refused (`tracked-format`).
* Kind `removed_highlight`: targets re-selected; each output `w:rPr` is the source's minus the highlight (and the
  change marker as tracked/plain demand); those `w:rPr` stripped from the residual (`strip_runs`/`strip_marks`).
  Touches `(part, block, "rPr")` / `"markRPr"`. Layout reference `source`.

## 4. `resolve_tracked_changes` (item 13)

`action`: `accept` | `reject` (required); `authors` (optional); `on_or_after`, `before` (optional dates
`YYYY-MM-DD[THH:MM[:SS]]`, compared with the wall-clock `w:date` — what Word shows); `scope` (default every text part);
`allow_missing`.

* **Selected**: a revision element whose author is listed (or no list) and whose date is in range (or no range). With a
  date filter, a revision without a readable date is not selected and flagged.
* **Moves**: a `w:moveFrom`/`w:moveTo` (wrapper or paragraph mark) belongs to the innermost open move range of its kind
  (`w:moveFromRangeStart`…`End` / `w:moveToRangeStart`…`End`, by id, document order). A move — its two ranges, by name,
  and every piece inside them — must be selected whole or not at all (`partial-move`); its range markers go with it. A
  piece outside any range is a plain revision.
* **Content wrappers** (`w:ins`, `w:del`, `w:moveFrom`, `w:moveTo` not inside `w:rPr`): accept unwraps `ins`/`moveTo`
  and removes `del`/`moveFrom` with their content; reject removes `ins`/`moveTo` with their content and unwraps
  `del`/`moveFrom`, turning their own `w:delText` into `w:t` and `w:delInstrText` into `w:instrText`. A revision inside
  removed content goes with it (counted).
* **Paragraph marks** (`w:pPr/w:rPr/{ins,del,moveFrom,moveTo}`): accept `ins`/`moveTo`, reject `del`/`moveFrom` → the
  marker goes (an emptied mark `w:rPr`, then an emptied `w:pPr`, too). Accept `del`/`moveFrom`, reject `ins`/`moveTo` →
  the paragraph goes — only when, once its content is resolved, it holds nothing but `w:pPr`, runs with nothing but
  `w:rPr`/`w:lastRenderedPageBreak`, proofing marks and Word's `_GoBack` bookmark (dropped, both ends); never the last
  paragraph of its container, never one with a section break. Otherwise two paragraphs would be joined and Word decides
  whose formatting the joined one keeps: refused (`paragraph-merge`, saying what is in the way) — resolve it in Word.
* **Property changes**: accept removes `rPrChange`, `pPrChange`, `sectPrChange`, `tblPrChange`, `trPrChange`,
  `tcPrChange`, `tblGridChange`, `numberingChange` (an emptied `w:rPr`/`w:pPr` too); reject restores `rPrChange` (the
  `w:rPr` becomes the paragraph mark's own revision markers, then the snapshot's children) and `pPrChange` (the `w:pPr`
  becomes the snapshot's children, then its own `w:rPr` and `w:sectPr`). Rejecting the others, table row and cell
  revisions (`w:trPr/w:ins|w:del`, `cellIns`, `cellDel`, `cellMerge`) and revisions inside equations: `revision-kind`.
* Predictions, analytic: each block's text after (its text elements whose ancestors hold no removed content; removed
  paragraphs `after=None`), structure counts, the revision delta of every kind (negative). Rejecting a deletion or
  accepting an insertion changes no block text (deleted text counts in the block text) — V4's delta and V10 see it.
* Kind `resolved_changes`: the verifier's **own** resolution of every part in scope (`verify/revisions.py`) compared whole
  with the output (`verified_parts`). Touches every changed part whole (`(part, "*", "text")`): accepting or rejecting is
  a spec of its own (or a chain stage). Mode untracked; layout reference `none` (the text reflows).
* Flags: every accepted/rejected revision counted by kind and author, revisions in `styles.xml` left as they are.

## 5. `remove_hidden_text` (item 14)

`tracked` (default true), `scope` (default body, headers, footers, footnotes, endnotes), `allow_missing`.

* A **hidden run**: live, holds content, effective hidden (§1.6). A **hidden paragraph**: its mark is hidden and every
  live run with content is hidden — deleted as a paragraph (§1.4). Other hidden runs: whole-run removal (§1.4).
* A hidden run inside a complex field goes only when the whole field is hidden, local, outside tracked changes — then
  every run of it goes; otherwise `field-result`. A simple field whose runs are all hidden goes whole (untracked) or is
  refused (tracked: `field-result` — a simple field cannot be deleted as a tracked change). Hidden text in a text box:
  `textbox`.
* **Every removed piece is listed** as a flag (part, paragraph, the text with tabs as ⇥), so the preview shows them.
* Kind `removed_hidden_text`: hidden runs and paragraphs re-derived (own toggle reading), item model per paragraph,
  deleted paragraphs by `check_deleted_paragraph` (tracked) or `deleted_src_blocks` (untracked).

## 6. `remove_notes` (item 15)

`rules`: a list of paragraph rules — a paragraph is a **note** when any rule matches; within a rule every given test
must hold: `style_in` (style ids; no pStyle = the default paragraph style), `text_regex` (Python regex, searched in the
run text), `color_in` (every live run with visible text has an effective colour in the list — §1.6), `hidden` (every
live run with content is hidden). `runs` (optional): `{color_in}` and/or `{hidden: true}` — inline notes: such runs are
removed from the paragraphs that stay (runs inside a hyperlink are never taken: link text is often blue). `tracked`
(default true), `scope` (default body), `allow_missing`.

* Note paragraphs are deleted (§1.4); inline note runs removed by whole runs (§1.4, with §5's field rule). Every removed
  note is listed as a flag. Kind `removed_notes` re-derives both. Layout reference `none`.
* MasterSpec's bracketed inline notes in plain text (`[Note: …]`) are not notes by colour or style: `replace_table` with
  a regex pair and an empty replacement does those.

## 7. `add_watermark` / `remove_watermark` (item 16)

`add_watermark`: `text` (the user's words, one line, ≤ 100 characters, placed verbatim), `font` (default Calibri),
`color` (hex, default `C0C0C0` silver), `opacity` (0.05–1, default 0.5), `layout` `diagonal` (default) | `horizontal`,
`size` `auto` (default) or points, `replace` (default false: a file that already has a watermark is refused,
`watermark-exists`; true removes the old one first, in the same op).

* Into every header part some section shows or references (any type), in the part's first paragraph that is a direct
  child of the header, as the first run: `w:r` → `w:rPr/w:noProof` + `w:pict` holding the VML shape Word itself writes
  for a text watermark (`v:shapetype` `_x0000_t136` when the part has none, `v:shape` id `PowerPlusWaterMarkObject<n>`,
  `o:spid` `_x0000_s<n>` unique in the package, absolutely positioned, centred on the margins, behind the text, the
  user's words in `v:textpath/@string`). No header part at all → `no-header` (a header is never created, rule 10).
  A section page type that shows no header (a first page without one) gets no watermark: flagged.
* Size `auto`: the box takes the text's proportions (advance widths of the font — Calibri, Arial or Times New Roman
  metrics; another font uses Calibri's, flagged — height 1.55 em) and is as large as fits the smallest text area of the
  sections showing that header (diagonal: (w + h)/√2 ≤ the shorter side). A size in points that does not fit this file's
  page is refused (`watermark-size` — it depends on the file, so it is not a spec mistake).
* `remove_watermark` (`text` optional — only watermarks with that text; `allow_missing`): every run in a header part whose
  one content element is a `w:pict`, `w:drawing` or `mc:AlternateContent` holding a shape named
  `PowerPlusWaterMarkObject…` (text) or `WordPictureWatermark…` (picture) is removed. The paragraph (and any
  "Watermarks" content control around it) stays: the header's layout does not move. A watermark sharing its run with
  other content: `structure`.
* Kinds `added_watermark` (the anchor paragraph's items = the source's with one run item first whose content is the
  verifier's own rendering of the shape from the asserted text/font/colour/size; ids checked unique; the box checked to
  fit every section's text area) and `removed_watermark` (the watermark runs' items gone). Words placed:
  `add_watermark.text` (`compiler.placed_words`). Layout reference `none` (drawn over every page).

## 8. `remove_empty_paragraphs` (item 17)

`mode`: `collapse` (default — each run of consecutive blank paragraphs keeps its first) | `all`; `tracked` (default
true); `scope` (default body); `allow_missing`.

* **Empty**: not in a text box, no section break, no numbering in effect (direct `w:numPr` with a numId other than 0, or
  the style chain's), no `w:pBdr` or `w:framePr` (direct or style), no revision of any kind, and holding nothing but
  `w:pPr`, proofing marks and runs of `w:rPr`, `w:lastRenderedPageBreak` and `w:t` of spaces and no-break spaces — a
  bookmark, a tab, a break, a field, a picture, a comment mark makes it not empty.
* Per container (body, cell, header, footer, note, comment, block content control) over its block children: `all` →
  every empty paragraph; `collapse` → every one after the first of a run of adjacent empties. Then kept again: the
  container's last block when it would leave the container empty or ending in a table (untracked), or — tracked — the
  container's last block always (Word cannot delete the final paragraph mark); one paragraph between two tables (else
  Word joins them). In the body, the last section (after the last section-break paragraph) keeps its last blank
  paragraph when all of it would go, flagged as a blank page (added 2026-10-21: batch 6's keep rule 4,
  `ops/deletion.last_section_keep` ↔ `verify/deletion._v_last_section`).
* Kind `removed_empty_paragraphs` re-derives all of it. Layout reference `none`.

## 9. `remove_hyperlinks` (item 18)

`which`: `external` (default: `w:hyperlink` with `r:id`; `HYPERLINK` fields without `\l`) | `internal` (`w:anchor`
links, `\l` fields — a table of contents' links among them) | `all`; `keep_style` (default false: the linked words lose
the Hyperlink/FollowedHyperlink character style — Word's built-in names — and look like the text around them; their
other formatting stays, a direct blue or underline is flagged); `scope` (default body, headers, footers, footnotes,
endnotes); `allow_missing`.

* A link element is unwrapped; a link field unlinked (§1.5). A relationship no element of the part uses any more is
  removed from the part's rels (the address leaves the file). Kind `removed_hyperlinks`: item model per paragraph (the
  hyperlink gone from every chain, the link style gone from the signatures, the field's markers gone); the rels member
  compared whole with the source minus exactly those relationships. Layout reference `source`.

## 10. `unlink_fields` (item 19)

`types`: field type names (`["DOCPROPERTY"]`, `["DOCPROPERTY", "REF", "DATE"]`) or `"all"` (every type but the page
numbers); `scope` (default body, headers, footers, footnotes, endnotes); `allow_missing`. §1.5 throughout; a field that
shows nothing is removed (flagged); a DATE/TIME field keeps the date it last showed (noted). Kind `unlinked_fields`.
Layout reference `none` (a renderer may recompute the source's fields).

## 11. `set_document_properties` (item 20)

`set`: `{title, subject, author, keywords, comments, category, status, last_modified_by, company, manager: text}`;
`clear`: a list of those names; `custom`: `{name: text}` (text custom properties, created when missing); `clear_custom`:
names. Core properties live in `docProps/core.xml` (`dc:title`, `dc:subject`, `dc:creator`, `cp:keywords`,
`dc:description`, `cp:category`, `cp:contentStatus`, `cp:lastModifiedBy`), company and manager in `docProps/app.xml`,
custom ones in `docProps/custom.xml` (`vt:lpwstr`, fmtid `{D5CDD505-2E9C-101B-9397-08002B2CF9AE}`, the next pid). A value
is the user's words: one line, no control or invisible character, ≤ 255 characters (`compiler.placed_words`). A missing
`core.xml`/`app.xml` is refused (`no-part`); a missing `custom.xml` is created with only the asked properties (rule 10's
third case: the op's purpose is the member). A custom property of another type in this file is refused (`property-type`). "Clear the
author names" = `clear: ["author", "last_modified_by"]`; tracked changes and comments keep their authors (flagged).
Fields showing a changed property keep their saved result until Word updates fields (flagged).
Kind `document_properties`; mode untracked when words are set, formatting for clears only.

## 12. `set_document_state` (item 21)

`track_changes`: `off` | `on`; `final`: true | false.

* Off: `w:trackRevisions` removed from `settings.xml` (or nothing to do). A file whose tracking is locked
  (`w:documentProtection w:edit="trackedChanges"` enforced) is refused (`protected`). On: `<w:trackRevisions/>` in schema
  order.
* Final: the custom property `_MarkAsFinal` = `true` (`vt:bool`) and `cp:contentStatus` = `Final` — what Word's *Mark as
  Final* writes; `docProps/custom.xml` created when missing (with its package relationship and content-type override).
  `final: false` removes `_MarkAsFinal` and a `Final` status.
* Kind `document_state`: settings, core and custom checked through the member residual (§1.2); a created part, the
  package rels and `[Content_Types].xml` compared whole with the source plus exactly the one entry.

## Decisions taken (each can be changed)

1. The three method rules change only as far as §0 says; codegen still cannot do any of them.
2. Comments: threads stay whole; parts are never removed, even emptied.
3. Highlight: plain by default; styles are flagged, not changed.
4. Accept/reject: paragraph joins refused (Word decides the joined formatting); table row/cell revisions and rejecting
   section/table property changes refused; moves resolved whole.
5. Hidden text and notes: tracked by default (decision 2 of batch 1); every removed piece listed.
6. Watermark: in the first paragraph of every header (no new paragraph, no content control); text watermarks only; VML as
   Word writes it.
7. Blank paragraphs: collapse by default; conservative "empty" (no bookmark, numbering, border, revision).
8. Hyperlinks: external by default; the link style comes off by default.
9. Fields: listed types or all-but-page-numbers; paragraph-spanning fields (a TOC) refused.
10. Properties: core, app and custom (text) only; custom.xml may be created.
11. State: track changes on/off; mark as final (Word's two markers).
12. Clean-up ops: a file with nothing to remove is a no-op, not an exception (`allow_missing` true by default, §1.7).

Not in this batch, recorded: picture watermarks (adding), comments in text boxes, joining paragraphs on accept/reject,
table row revisions, highlight in styles, removing a document's personal information on save (`w:removePersonalInformation`).

## Built: departures from this note

Built by six builders in parallel (one per group of items, each in its own worktree on the shared machinery of §1),
then integrated and reviewed. Every departure below is pinned by a test; README → "Built-in operations, batch 2" is the
user's view and CLAUDE.md lists every both-sides definition.

**Across the batch (integration):**

1. `allow_missing` defaults to **true** for every clean-up op (§1.7, decision 12), as the note says — the builders'
   sections were written before that decision and each now says so.
2. **An assertion carries the user's own words** (scope, `which`, `colors`, `tracked`, `mode`, note rules — absent = the
   default), and each V10 kind applies its own defaults: a wrong default shared by plan and apply would otherwise reach
   the check as the plan's resolved value. The comments, highlight and watermark kinds were built so; the links, fields,
   accept/reject, hidden-text, notes and blank-paragraph kinds were changed to it at integration.
3. **Scope reads are declared** (rule 9): `remove_highlight`, `resolve_tracked_changes`, `remove_hidden_text`,
   `remove_notes` and `remove_empty_paragraphs` touch `(part, "scope", "verify")` for every part in scope, as the links
   and fields ops did — beside a `remove_header_footer` they used to plan and then fail V10 on the missing header.
4. File-specific refusals are never `bad-params` (the compiler treats `bad-params` as a mistake of the spec for the whole
   batch): `watermark-size` and `property-type` are their own codes.
5. `tests/test_batch2_integration.py` scans the batch's operation modules (with `ast`, not a regular expression — a dict
   literal after a computed code was read as a code) and fails on a refusal code without a plain-words explanation.

**§2 Comments:** a mark inside `w:txbxContent` but outside any nested paragraph is `textbox` too; highlight colours in a
spec are Word's exact names (the schema's enum), `canonical_colors` folds case for direct callers.

**§3 Highlight:** a tracked removal on a paragraph mark that is itself a pending insertion/deletion/move is refused
(`tracked-format`) — the `rPrChange` snapshot would copy the mark's revision marker and V4 would see one id twice.

**§4 Accept/reject:** a new refusal `marks-in-change` (content that goes may hold no note/comment mark, no single end of a
bookmark/permission/move range — Word's `_GoBack` goes whole — and no part of a complex field); a paragraph that would go
with a section break is `section-break`; rejecting a `pPrChange` whose snapshot holds tracked changes is `revision-kind`;
"a paragraph follows it" skips block-level marks; an empty still-pending wrapper goes with its paragraph (counted in the
V4 delta).

**§5–§6, §8 Hidden text, notes, blank paragraphs:** the keep rules apply to hidden text and notes too (a kept paragraph
loses its content and keeps its mark, flagged); a hidden paragraph that cannot be deleted for any reason but a text box
is handled the same way (notes still refuse); in the body, once those are kept, the last section (after the last
section-break paragraph that stays) keeps its last chosen paragraph when all of it would go, flagged as a blank page, and
a note it keeps is emptied, not refused (added 2026-10-21: batch 6's keep rule 4, `ops/deletion.last_section_keep` ↔
`verify/deletion._v_last_section`); an empty paragraph whose mark is hidden is a hidden paragraph; inline notes
never take a run holding a picture; within `color_in` space-only runs are ignored; every removed piece is a flag. V2's
pairing changed (CLAUDE.md "V2's plan-assisted pairing"): the predicted untracked deletions/insertions are set aside
before the alignment, so a deleted blank paragraph cannot pair with another blank far away.

**§7 Watermarks:** the anchor is the first paragraph whose containers below `w:hdr` are only content controls (Word's
"Watermarks" control); the text is the first `v:textpath` that has a `string`; the shapetype check covers every VML
shapetype; a DrawingML watermark with a text box deletes its text-box paragraphs (predicted) and declares the holding
paragraph structure-only; a shape outside a run or in a text-box paragraph is `structure`, one in a pending tracked
change `existing-revisions`; `add_watermark` reads `pgSz`/`pgMar`/`hfref` of every showing section; the verifier checks
the box is the asserted one, inside every showing text area and filling it as the `auto` rule says, without
re-deriving the font proportions.

**§9–§10 Links and fields:** `nested-field` also when a selected field sits inside another field's code; `textbox` is
checked before the field's own refusal; links and fields are found again at apply by paragraph (place + identity), not
by whole-part ordinals (the two ops in one spec shift them); an unterminated quote in an instruction runs to the end;
links and fields inside what an unconverted field shows (a table of contents) are flagged; only hyperlink-type
relationships are removed.

**§11–§12 Properties and state:** the layout reference is `source`, but `none` for a file where a field shows a changed
property (LibreOffice draws it with the new value); an empty property element is already clear; a new code
`malformed-properties` (a property twice, two custom properties with one name, an unreadable pid, a custom part without
relationship, two `w:trackRevisions`); custom names matched casefolded, an existing property keeps its stored name and
value tag; setting `keywords` replaces any `cp:value` children; `set_document_state` checks the settings order itself
(V5a cannot — below) and refuses `off` on a locked file even when tracking is already off.

**V5a and `settings.xml`** (found at integration): libxml2 stops checking a sequence at the first child it does not
expect, so a source whose `w:trackRevisions` stands first never reports a later error (python-docx's
`<w:zoom w:val="bestFit"/>` without `w:percent`); "track changes off" removed the element, V5a reached the error and
failed a correct output as "new". The baseline now also validates the source with its children in schema order (it only
reorders: every error it shows is the source's own; a really new error still fails). **Accept/reject beside other ops**
(found at integration): `resolved_changes` claimed every part in scope as compared whole, so a `set_font` on a footer
the accept never touched planned clean and failed V10; it now claims only the parts it resolves and leaves the others to
the residual. **The advisor and the request-only ops** (found at integration): nothing kept the advisor from adding
`resolve_tracked_changes`, `set_document_properties` or `set_document_state` to a re-compiled spec for a set-aside file —
the model's own initiative, which §0 rules out; `catalog.REQUEST_ONLY_OPS` names the three and the advisor rejects a
respec that adds one the original spec lacks (before any other check) and says to recommend it instead.

**Recorded, not changed:** `verify/xsd.py` still drops every V5a message containing `hyphenationZone` (an old false
alarm), which also hides an out-of-order settings element before it (`set_document_state` checks the place of what it
adds itself). The clean-up ops' reads of the style chains (effective hidden and colour) are
not declared as reads, like `set_font`'s (CLAUDE.md "Plan-time conflicts for what an op reads" 5).
