# Element types and their traps — [unproven]

Everything in this file was added when the method was generalized (2026-10-13) from general OOXML knowledge, not from a job that paid for it. The proven lessons are in `ooxml-lessons.md`. Treat each item here as a hypothesis the verification suite must confirm the first time a job touches that element; when an item survives a real job, move it to `ooxml-lessons.md` and drop the mark. When an item is wrong, fix it and say so in the report.

The general principle behind every entry: Word stores one logical thing in several places (a reference in `document.xml`, a definition in another part, an entry in `[Content_Types].xml` or a `.rels`, sometimes a cache). An edit that updates one place and not the others produces a file that opens with a repair prompt or silently reverts on save. Before editing any element below, enumerate every place it lives, and put every one of them into the V1 intended-parts set.

## Text that is not in `w:t`

The proven V2/V3 checks collect `w:t`, `w:delText`, and count `w:tab`/`w:br`/`w:noBreakHyphen`. A general-purpose verifier should widen both lists, because these also carry or render as characters:

* `w:instrText` / `w:delInstrText` — field instructions (` PAGE `, ` REF _Ref123 \h `, ` HYPERLINK "..." `). A `w:t`-only check cannot see a field instruction change.
* `m:t` — text inside equations (`m:oMath`), a different namespace entirely.
* `w:sym` (`w:font`, `w:char`) — a symbol character with no text node.
* `w:cr`, `w:softHyphen`, `w:ptab` (positional tab), `w:footnoteReference`, `w:endnoteReference`, `w:commentReference`, `w:drawing`, `w:fldChar` — render as something or anchor something; count them per block like `w:tab`.

Collect text per block as the concatenation of all of these in document order, and count the non-text ones; assert both pre vs post.

## Fields

* Two forms. Complex: `w:fldChar begin` run → `w:instrText` run(s) → `w:fldChar separate` → cached-result runs → `w:fldChar end`. Simple: `<w:fldSimple w:instr="...">` wrapping the result runs. A complex field can span paragraphs (TOC does) and nest (`IF`, `INCLUDETEXT`).
* The cached result is a cache. Editing it is editing a cache Word will overwrite on the next update; editing the instruction is a content edit. To ask Word to refresh a specific field on open, set `w:dirty="true"` on the `begin` `fldChar` (or on `w:fldSimple`); `w:updateFields` in `settings.xml` refreshes all fields and makes Word prompt the user on open — do not set it without saying so.
* `PAGE`/`NUMPAGES` results are whatever LibreOffice last rendered; a render difference in them is not a defect.
* Hyperlinks: `w:hyperlink` wraps runs and points at `r:id` → rels entry with `TargetMode="External"`, or at `w:anchor` for an internal bookmark. Changing the display text is a run edit; changing the target is a rels edit (V1 intended parts must include `document.xml.rels`). A `HYPERLINK` complex field is the other spelling of the same thing.
* `REF`/`SEQ`/`TOC` stale-cache behavior and orphaned bookmarks: see `ooxml-lessons.md` §6 — flag, don't edit.

## Content controls (`w:sdt`)

* Structure: `w:sdt` → `w:sdtPr` (alias, tag, id, lock, placeholder, dataBinding, type element) + `w:sdtContent` (the paragraphs or runs). Block-, run-, row-, and cell-level controls all exist; which one you are in decides what `w:sdtContent` may contain.
* `w:showingPlcHdr` in `w:sdtPr` marks the content as placeholder text (greyed, replaced when the user types). Filling a control with real text means removing that element too, or Word keeps treating the fill as a placeholder.
* `w:dataBinding` makes the control a view of a node in a `customXml/itemN.xml` part; Word repopulates the content from that node on open. Editing `w:sdtContent` of a bound control is undone silently. Edit the customXml node instead (and include that part in V1).
* `w:lock` (`sdtLocked`, `contentLocked`, `sdtContentLocked`) is advisory to the Word UI, not to a script — respect it as a signal that the author did not want the content changed; ask.
* Tracked changes inside `w:sdtContent` are legal; a control's own insertion/deletion is not tracked at the control level, so a deleted control disappears from the reject view — flag.

## Comments

* A comment lives in up to four parts: `comments.xml` (the text, `w:comment w:id/w:author/w:date`), `commentsExtended.xml` (`w15:commentEx` — done flag, reply threading via `paraIdParent`), `commentsIds.xml` (`w16cid` durable ids), `commentsExtensible.xml` (`w16cex`). The anchor in `document.xml` is `w:commentRangeStart`/`w:commentRangeEnd` plus a run holding `w:commentReference`, all sharing the `w:id`. Older files have only the first part.
* Deleting a comment means removing the anchor triple and the entry in every comments part; an orphaned extended entry is a repair trigger. Adding one means adding to every part the file already has, with ids that collide nowhere, and the `.rels` + content-type overrides if a part is new.
* A comment's text is not a tracked change. Editing it is a content edit under the contract (approved words only) even though Word shows no revision for it; say so in the report.
* The docx skill's `comment.py` exists for adding comments; resolve it fresh and prefer it to hand-building the four parts.

## Footnotes and endnotes

* Text is in `footnotes.xml`/`endnotes.xml`; the anchor is a run with `w:footnoteReference w:id`. Entries with `w:id="-1"` (`separator`) and `w:id="0"` (`continuationSeparator`) are structural — never delete or renumber them.
* Tracked deletion of a footnote = tracked deletion of its reference run; the `w:footnote` element stays so the reject view can restore it. Untracked deletion removes both.
* Footnote text is in V2 scope whenever `footnotes.xml` is in the package, whether or not the job "touches" footnotes — a transform that re-serializes the part can still alter it.

## Images and media

* An inline picture is `w:drawing` → `wp:inline` (or `wp:anchor` for floating) → `wp:extent cx cy` (display size, EMU: 914400/inch, 12700/pt) → `a:graphic` → `pic:pic` → `a:blip r:embed` → rels → `word/media/imageN.ext`, with the size repeated in `a:xfrm/a:ext`. `wp:docPr w:id` must be unique in the document.
* Replacing an image in place: overwrite the media binary under the same name and format, keep the rels entry, and update both `wp:extent` and `a:ext` if the aspect ratio changed — otherwise Word stretches the new image into the old box. A new format needs a `Default` entry for its extension in `[Content_Types].xml`.
* The media part is an intended change in V1; its new hash is recorded in the report. Nothing else should move.

## Tables

* `w:tbl` → `w:tblPr`, `w:tblGrid` (one `w:gridCol` per logical column), `w:tr` → optional `w:trPr`, `w:tc` → `w:tcPr` (`tcW`, `gridSpan`, `vMerge`, borders, shading, `vAlign` — schema order enforced like `pPr`) → block content. Every `w:tc` must end with a `w:p`; an empty cell is a cell with one empty paragraph. A cell with no paragraph is a repair prompt.
* Inserting a column means every row gets a cell at the right grid position AND `w:tblGrid` gets a `w:gridCol`; `gridSpan` and `vMerge` mean the cell count per row is not the column count — walk the grid, not the cell index.
* Tracked table changes have their own grammar: row insert/delete is `w:ins`/`w:del` inside `w:trPr`; cell changes are `w:cellIns`/`w:cellDel`/`w:cellMerge` inside `w:tcPr`; property changes are `tblPrChange`/`trPrChange`/`tcPrChange`. V4 must count these too, or a tracked row deletion looks like a silent deletion.
* Cell-paragraph spacing: see the LibreOffice pitch lesson in `ooxml-lessons.md` §6 — pin transplanted table-cell paragraphs explicitly.

## Sections

* `w:sectPr` lives in the `w:pPr` of the last paragraph of each section except the final section, whose `sectPr` is the last child of `w:body`. Removing a section-break paragraph merges its paragraphs into the following section and that section's page setup, headers, and footers apply to them.
* A section with no `headerReference`/`footerReference` of a given type (`default`, `first`, `even`) inherits that part from the previous section. Adding a section can therefore change which header a page shows without any header part changing — check inheritance in the render, and read `w:titlePg`/`w:evenAndOddHeaders` (lessons §1).
* `w:pgNumType w:start` restarts numbering; see `playbooks/page-numbering.md`.

## Bookmarks

* `w:bookmarkStart w:id w:name` / `w:bookmarkEnd w:id` must pair by id; names are unique per document; `_GoBack` and `_Toc…`/`_Ref…` names are Word's own. Moving text across a bookmark boundary silently changes what a `REF` points at; deleting a start without its end (or vice versa) is a repair prompt.

## Numbering definitions

* `numbering.xml`: `w:abstractNum` (the level definitions) ← `w:num w:numId` (an instance, optionally with `w:lvlOverride/w:startOverride` to restart). Paragraphs point at `w:numId` via `w:numPr`; `w:numId w:val="0"` means "no numbering" and is how a style's numbering is switched off on one paragraph. New instances need a `numId` that collides with nothing, including ids that exist only in `styles.xml`.

## Settings and properties — leave alone

* `settings.xml`: `w:trackRevisions` is the user's choice — never toggle it. `w:rsids`, `w:proofState`, compatibility settings: not yours.
* `docProps/core.xml` (`lastModifiedBy`, `modified`) and `docProps/app.xml` (page/word counts): Word recomputes them on save. Do not update them; V1 expects them unchanged.
* `customXml/`: data-binding sources and SharePoint/property metadata; untouched unless a bound control is the job.
