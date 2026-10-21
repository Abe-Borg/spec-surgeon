# Spec Surgeon — Implementation plan: full control

2026-10-21. Abraham's direction: the program is named for precise control, and the user needs a lot of it, both
precise (one word, one property) and broad (whole tables, sections, pictures, the page structure). The engine's
policies were too restrictive; loosen them where it makes sense. Mass deletion is in reach but not a focus. Shapes,
SmartArt, charts, equations, embedded objects, data-bound content controls and the theme stay out (Tier 3).

## Where this round starts

| What | Finding (2026-10-21 assessment) |
| --- | --- |
| Words | Strong: find/replace, find/replace tables, insert and delete paragraphs, variables, options, notes, hygiene. A new text cannot hold a tab or a line break; one plain paragraph per insert |
| Character formatting | Font, size, bold, italic, underline, caps, small caps, strike, colour. No superscript/subscript, hidden, highlight (add), character spacing, double strike |
| Deletion | `delete_paragraphs` refuses an untargeted selection, any paragraph holding a field or a section break, and emptying a table cell; nothing deletes a table, a picture or a footnote. A delete-everything spec is refused on every real file |
| Tables | Table style and table properties only; no rows, columns, cell shading, widths or merges; no table can be inserted |
| Pictures | Swap a picture, add/remove a watermark; no insert, delete or resize |
| Sections | Margins, size, orientation, different first page; no section break can be inserted or removed; no columns, line numbers, vertical alignment |
| Fields, comments | Unlink fields, add/remove comments; no field can be inserted; no reply, no resolve |
| Order | Nothing moves a paragraph |
| Policy | CLAUDE.md invariant 5 read as "refuse when unsure"; `triage` refuses a document whose body has no text |

## Principles for this round

1. **Predict first, verify independently** (CLAUDE.md 1–2): every new op has its V10 kind written first, in its own
   `verify/` module, with its own selection and an "exactly this change" check; the residual covers everything else.
2. **Refuse the unpredictable, not the large** (CLAUDE.md 5): a deletion of a whole table, section or body is planned,
   previewed and verified like any edit. A refusal names what it cannot predict and how to do it in Word.
3. **The user's words only** (CLAUDE.md 6): new text comes from the request verbatim or from a file the user attached;
   `compiler.placed_words` traces every typed word. Structural ops add zero characters.
4. **Tracked like Word tracks it.** An op that adds or removes content (paragraphs, rows, tables, pictures) is tracked
   by default, as Word records it, with `tracked: false` on request; an op whose change Word cannot track (a column,
   a cell merge, a section break, a picture's size, cell properties) is untracked only and says so.
5. **Compose, don't special-case.** Wiping a document is `delete_paragraphs` over "all" beside the existing removals
   (headers/footers, comments, footnotes), composed in one request; no dedicated "nuke" op.

## Workstreams

| Item | Goal | Expected outcome |
| --- | --- | --- |
| F1 — policy and docs | The rules say what the user may do | CLAUDE.md rewritten and trimmed (invariant 5: refuse the unpredictable, not the large; Tier 3 named); `triage` takes a document with an empty body (a note, not a refusal); the compiler prompt teaches the new ops; README, release notes |
| F2 — deletion | Delete anything a paragraph selection covers | `delete_paragraphs` takes "all"; a table whose every paragraph is selected goes with them; paragraphs holding whole fields go; a section break in the selection merges the sections (untracked) or is marked deleted (tracked); a container keeps its last paragraph. New `remove_footnotes` (footnotes and endnotes: all, or by text) |
| F3 — text and character formatting | Every common run property; richer inserts | `set_properties` gains superscript/subscript, hidden, highlight, character spacing, double strike; `insert_paragraph` takes several paragraphs, styles, tabs, line breaks and bold/italic/underline segments; `replace_text`'s new text may hold tabs and line breaks |
| F4 — pictures | Insert, delete and resize pictures | New `insert_image` (an attached picture as its own paragraph before/after anchors), `remove_images`, `resize_images` |
| F5 — table rows and tables | Add and remove rows and whole tables | New `delete_tables`, `delete_table_rows`, `insert_table_rows`, `insert_table` (from an attached CSV/XLSX or typed rows); `resolve_tracked_changes` accepts and rejects tracked row insertions and deletions |
| F6 — table grid and cells | Columns, cells, merges | New `delete_table_columns`, `set_table_cells` (shading, vertical alignment, column widths), `merge_table_cells` |
| F7 — sections | Section breaks and section layout | New `insert_section_break`, `remove_section_break`; `set_page_setup` gains columns, line numbering, vertical alignment and the section start type |
| F8 — fields and comments | Page numbers and the review loop | New `insert_field` (PAGE, NUMPAGES, SECTIONPAGES, SECTION, DATE, SAVEDATE, FILENAME, STYLEREF, DOCPROPERTY) at the user's placeholder text or the end/start of paragraphs; new `reply_to_comments`, `resolve_comments` |
| F9 — moving | Reorder content | New `move_paragraphs`: a range (tables inside included) before/after an anchor paragraph; untracked, or tracked as a deletion and an insertion |

Design of every op: `docs/BUILTIN_OPS_BATCH6_DESIGN.md`.

## Acceptance

Each item: its ops end to end on fixtures (plan → merge → apply → `run_suite` passes), a wrong output and a shared
wrong implementation caught per rule, the describe cases, the catalog, and a LibreOffice render. On the owner's
machine: a live request per item over real specifications, opened in Word.

## Do not, in this round

- Build Tier 3 (shapes, SmartArt, charts, equations, embedded objects, data-bound content controls, the theme).
- Add a dedicated "nuke" op, a raw-XML op, or a `force` flag.
- Widen the custom transform's limits (`catalog.custom_transform_limits`); built-in ops are the way to more control.
- Deduplicate the verifier against the transform side.

## Status — agents update this at the end of every session

| Item | Status | Owner / branch | Last note |
| --- | --- | --- | --- |
| F1 — policy and docs | in progress | claude/vibrant-hopper-ru2375 | 2026-10-21: CLAUDE.md rewritten and trimmed; plan and design written. |
| F2 — deletion | planned | | |
| F3 — text and character formatting | planned | | |
| F4 — pictures | planned | | |
| F5 — table rows and tables | planned | | |
| F6 — table grid and cells | planned | | |
| F7 — sections | planned | | |
| F8 — fields and comments | planned | | |
| F9 — moving | planned | | |
