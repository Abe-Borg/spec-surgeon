# CLAUDE.md — Spec Surgeon

Working notes for any Claude session touching this repository. Read before editing.

## What this is

**Adaptive workspaces (source, 2026-10-17):** `inventory.py` and `campaign.py` add persistent,
project-scoped tasks above the existing engine. `recovery.py` persists resumable investigation
state separately from compact diagnostic transcripts; READY candidates survive further passes.
`resources.py` owns the ample configurable model allowances (50 attempts, 512 calls, $250 estimated
spend, 64k output, 20m estimated total tokens, 1m image-token allowance, max effort). The conversation no longer hands off
merely because two different candidate specs were rejected. `goal_contracts.py` resolves immutable
document effects into source-bound targets; `verify/effects.py` resolves and verifies them independently.
`campaign_planner.py` proposes reviewed contracts; `investigation.py`/`analysis_runner.py` run isolated
read-only analysis; `contexts.py` seals complete signed provider epochs without rewriting their history.
Legacy candidate-effect constraints remain authoritative for goals without independent coverage. Governed
custom methods may change their implementation, never the approved effect, target cardinality or literal wording.
See `docs/ADAPTIVE_WORKSPACES.md`. No persisted visual acknowledgement may authorize delivery.

A deterministic engine for surgical .docx edits (Tier 1 of the three-tier design), plus a thin optional
model layer. Since NL-editor Phase 1 the engine is an **operation library** (`ops/`) executed through
**EditSpecs** (`editspec.py`, `spec-surgeon run-spec`); the v0.1.0 jobs are thin aliases over two of the ops. Phase 2
added the header/footer templating op (`apply_header_footer_template`), the first op that adds members; v0.4.0 let
it take a **template spec** (an ordinary spec, e.g. the architect's: `slot_markers: ["reference"]`) and gave the three
font ops **sizes**. The method it implements is the `docx-surgical-edit` skill bundled at
`src/spec_surgeon/skill/` — read `SKILL.md` and `references/verification-checklist.md` there before
changing any job or check. Phase 8 turned the compile into a conversation (`conversation.py`), added Claude's visual
check of the preview (`vision.py`, advisory), custom transforms as an offer, and a personal library (`library.py`);
every Claude call goes through `llm.py`. Built-in operations batch 1 (2026-10-16) added nine ops over one in-paragraph
edit core (`ops/textedit.py` ↔ `verify/edits.py`), tables and pictures as inputs, and snippets as a library kind
(`docs/BUILTIN_OPS_BATCH1_DESIGN.md`). Built-in operations batch 3 (2026-10-16) added the run selector `color_in` and
`set_tab_stops` (`docs/BUILTIN_OPS_BATCH3_DESIGN.md`; items 22–23 were already `set_properties`). Built-in operations
batch 4 (2026-10-17) added paragraph borders and shading, spacing on styles, clearing direct formatting, the proofing
language, applying a style by appearance or pattern, style mapping over a shared style import (closure and list
numbering) and removing unused styles (`docs/BUILTIN_OPS_BATCH4_DESIGN.md`). Built-in operations batch 5 (2026-10-17)
added the engine's first model of list numbering, on both sides (`ops/lists.py` ↔ `verify/lists.py`: which number Word
shows on each paragraph), and over it converting a document's numbering to the user's scheme from an example file and
typed numbers to real list numbers (the document must read the same), a list level's format/zero padding/start/restart
and a numbering audit; table borders, cell margins, width, header rows and rows that do not split, applying a table
style; and `set_page_setup`'s different first page and sections chosen by what they contain
(`docs/BUILTIN_OPS_BATCH5_DESIGN.md`). The user (Abraham) is a
fire sprinkler designer; these files go to clients;
"I will lose my job" if content is silently lost or invented is the spec.

## Hard rules

**Items 41–49 (source additions):** `ops/hygiene.py` and independent `verify/hygiene.py`
implement space, punctuation, non-breaking-space and individually approved case changes.
`character_hygiene` verifies exact source occurrences; previews show every file's changes.
Capitalization choices are built from inspected proposals, and only an actual user pick adds
the source-bound approval marker. `spec_qa.py`, CLI `audit` and the desktop QA viewer are
report-only; never route an audit through a document transform or infer an adopted edition.
See `docs/TEXT_HYGIENE_AND_SPEC_QA.md` for interfaces and limits.

1. **Verifier independence.** Nothing under `src/spec_surgeon/verify/` may import `spec_surgeon.jobs`,
   `spec_surgeon.ops`, `spec_surgeon.editspec`, `spec_surgeon.ooxml`, `spec_surgeon.package`,
   `spec_surgeon.goals`, or `spec_surgeon.goal_contracts`
   (`tests/test_independence.py`). It has its own zip reader (`verify/reader.py`), its own text extraction
   (`verify/textcheck.py`), and its own selector resolution, style-chain walk and theme lookup
   (`verify/props.py`), and its own slot detection, fill extraction and effective-property resolver
   (`verify/template.py`), and (built-in batch 1) its own run text and item model (`verify/edits.py`), table readers
   (`verify/tables.py`), word-changing kinds (`verify/textops.py`) and picture reader, matching and fitted size
   (`verify/images.py`), and (built-in batch 2) its own field parser (`verify/fields.py`), effective hidden/colour and
   item-model edits (`verify/markup.py`), comment model (`verify/comments.py`), highlight targets
   (`verify/highlight.py`), resolution of tracked changes — a rebuild, never an in-place edit
   (`verify/revisions.py`) —, hidden/note/blank rules (`verify/removal.py`), watermark reader and shape template
   (`verify/watermark.py`), link readers (`verify/links.py`) and property tables and settings order
   (`verify/docprops.py`), and (built-in batch 3) its own colour-list reading for the run selector
   (`props._v_sel_colors`/`_v_sel_color_in`) and its own tab entries, section reader and tab-stop rule (`verify/tabs.py`),
   and (built-in batch 4) its own border levels, box and shading rules (`verify/borders.py`), cleared sets and symbol rule
   use (`verify/clearing.py`), effective language (`verify/language.py`), style resolution, closure, list-id allocation
   and copies (`verify/restyle.py`, `verify/styleimport.py`, `verify/applystyle.py`) and used-style scan
   (`verify/unused.py`), and (built-in batch 5) its own list model — numbering definitions, a paragraph's numbering,
   the count and the rendered number, the scheme, typed numbers, the visible line (`verify/lists.py`) —, conversion
   destination and import derivation (`verify/numberingconvert.py`), typed-number refusals (`verify/typednumbers.py`),
   list-level changes and the expected `w:lvl` (`verify/listlevel.py`), the audit's blank rule (`verify/numberingaudit.py`),
   table selection, levels and expected table/row/cell elements (`verify/tableselect.py`, `verify/tableprops.py`,
   `verify/applytablestyle.py`), the content form of the section selector (`props._v_resolve_sections`) and what the
   cover shows (`props._cover_unnumbered`).
   Do not "deduplicate" these into shared helpers — the duplication is the point: a
   transform-side bug must not be able to hide behind the same bug in the check.
2. **Predict first.** `Op.plan()` (and `Job.plan()`) must compute every prediction (diffs, parts, members
   removed/added, revision delta of every kind, structural changes, accepted views, property assertions and
   their target counts) from the SOURCE, analytically. Never implement a plan by running the transform
   on a copy and reading off what changed — that is confirmation, not verification.
3. **Never weaken a check to pass.** A failing check gets root-caused. Verifier bugs look exactly like
   transform bugs; read the actual strings before touching either side. A corrected check must be at
   least as strict as the one it replaces.
4. **No generated characters.** Formatting jobs add zero text. Content jobs place the user's approved
   wording verbatim. Any job that would need to invent text raises `JobException` instead.
5. **Repack by member substitution** (`DocxPackage.repack`). Never pretty-print, never regex-edit XML,
   never `zip -r`. Serialize with `serialize_xml()` only.
6. **The guard is not optional.** In-place delivery goes through `guard.write_back` and nothing else.
   No `force` flag exists and none should be added. `guard.drifted` compares size, mtime and md5; delivery hands the
   guard only the bytes the suite verified (`batch.verified_bytes` re-hashes the output against the worker's
   `output_md5`), in copies mode too.
7. **Scripts live in the repo, not in project folders.** Scratch work goes to a temp dir. Never name a
   module after a stdlib module (`inspect.py`, `types.py`, …) — a script in the working dir shadows
   stdlib imports inside third-party libraries.
8. **Everything an op changes is predicted or asserted.** V10 runs on every plan: after stripping what the
   `property_assertions` cover and the blocks with predicted text changes, the changed text parts and
   `styles.xml` must be identical. A new property family needs a new V10 assertion kind written in
   `verify/props.py` first (selection re-resolved independently, target count checked, "exactly this change and
   nothing else" on each target). Never add a kind that strips without checking. Spec-level `assertions` in an
   EditSpec are post-conditions only (`effective_font`) — a spec must never be able to exempt anything from V10.
   Since built-in batch 2 the residual also covers `settings.xml` and the document-properties parts (core, app, custom
   — the member residual, `props._residual_members`): any change there must be registered by a kind that checked it,
   and a part a kind compares whole (`ctx.verified_parts`) is skipped by the residual only because the kind compared
   ALL of it. A kind is handed the user's own words (scope, options, defaults absent) and applies its own defaults —
   never the transform's resolved copy (see "Built-in operations, batch 2"). `numbering.xml` is under no residual
   (batch 4): a kind whose op changes it compares the WHOLE member with its own expected tree and registers it in
   `verified_members` (`styles_imported`); a style definition a kind replaced is out of the styles residual only through
   `ctx.replaced_styles` after the kind compared it whole and checked its place, a removed one through
   `ctx.removed_styles` (the residual then requires it gone). Batch 5: `set_list_level`'s `list_level` compares the WHOLE
   numbering member with the source plus exactly its level's change; an op over the style import that imports nothing
   (`apply_style`, `map_styles`, `apply_table_style`) still requires the member byte-for-byte unchanged
   (`styleimport.check_numbering_unchanged`) unless another assertion owns it — a `styles_imported` copying lists, or a
   kind in `NUMBERING_OWNER_KINDS` (`list_level`), whose own whole comparison still fails any other change. Table
   properties are stripped from the text-part residual only through `ctx.strip_tbl` / `strip_tr` / `strip_tc` /
   `strip_tblprex` (part → table ordinal / (ordinal, row) / (ordinal, row, cell) → element names) by the kind that
   compared each element; an emptied `w:trPr`/`w:tcPr`/`w:tblPrEx` is dropped on both sides.
9. **Overlapping ops are refused, never ordered by guess.** `merge_fragments` raises `conflict` when two ops
   touch the same block's text, the same property family of the same target, or anything in a member another
   op removes/restructures. Ops declare what they touch in `touches`; keep that list honest — including a
   `(part, "scope", "verify")` touch for every part an op's V10 assertion re-resolves its selection over
   (`set_font`, `set_paragraph_props`: every part in scope, changed or not), so an op that removes that part is a
   plan-time conflict rather than a V10 failure after apply — and a `verify` touch for everything an op's prediction
   READS from the source that another op could change (2026-10-16; `base.verify_family` is the grammar): a whole
   member, `(member, "*", "verify")`, conflicts with any change inside it by another op; a family of a target,
   `(member, target, "verify:<family>")`, with a change of that family or of the target's `text`; a bare `verify` on a
   target with any change of it. `apply_header_footer_template` reads the styles part and the theme part whole (its
   pins are the diff of template-effective vs TARGET-effective properties, resolved over every style chain and
   docDefaults) and `verify:pgSz`/`verify:pgMar`/`verify:titlePg` of every section's sectPr (its tab rescale and
   distance alignment read the section's text width and distances; `hf_template_refs` predicts every titlePg);
   `fix_page_numbering`'s cover-unnumbered reads `verify:titlePg` of every section (which footer the cover shows,
   whether a later section shows it by inheritance). So a `set_style_font`, `set_doc_defaults_font`, `set_properties`
   on styles or `set_page_setup` beside the templating op is a plan-time `conflict` naming the op to split out (they
   used to plan and fail V10 after apply, and the compiler's dry plan accepted them), and a template flipping the
   cover's titlePg beside cover-unnumbered is one too (it used to PASS every check with the page number still on the
   cover: the edit landed on a footer the cover no longer showed, and V7 skips page 1 there). Two reads of one member
   or target never conflict, and an op's own change of what it reads is none. `fix_page_numbering continuous` beside
   the templating op still plans: `pgNumType` is neither read nor changed by the templating op, and both ops' V10
   assertions strip their own sectPr children before the residual compares the rest — decided right as is
   (`tests/test_read_conflicts.py`).
   Batch 1: the in-paragraph edit ops touch `(part, block, "text")`; `swap_image` touches `(part, block, "drawing")`
   for each paragraph holding a swapped picture (so a text edit of that paragraph conflicts: the item model would see
   the drawing change), the part's rels and `[Content_Types].xml` as whole members, every part in scope as `verify`;
   `name_output`/`make_values_agree` touch `naming.OUTPUT_NAME_TOUCH`, and `merge_fragments` refuses two ops that
   both name the output. What they READ is declared too (`slots.fill_reads`): `name_output`'s and
   `ensure_end_of_section`'s fill rules read `(part, block, "verify:text")` of every paragraph they take a value from,
   `make_values_agree` of every paragraph a place matched, `ensure_end_of_section` its `copy_from_regex` paragraph
   (`verify`) and the body's end (`(main, "end", "verify")`, so a paragraph added after its line by another op is a
   conflict), and `make_values_agree` with a file-name place reads `OUTPUT_NAME_TOUCH`'s member.
   Batch 2: every clean-up op whose V10 kind re-derives its decisions over every part in scope declares `(part,
   "scope", "verify")` for each (`remove_highlight`, `resolve_tracked_changes`, `remove_hidden_text`, `remove_notes`,
   `remove_empty_paragraphs`, `remove_hyperlinks`, `unlink_fields`) — so a `remove_header_footer` beside them is a
   plan-time `conflict` even where they have nothing to do in the removed part (they used to plan and fail V10 on the
   missing part; `tests/test_batch2_integration.py`). Changes: `(part, bi, "text")` per changed or deleted paragraph,
   `(part, "*", "text")` for a part with untracked paragraph deletions and for every part `resolve_tracked_changes`
   resolves (a spec of its own), `(part, bi, "rPr")`/`"markRPr"` for a highlight, `(member, "*", "remove")` for the
   comment parts and `(part, "comment-mark:<id>", "remove")` per comment mark between blocks, the rels member as
   `(rels, "*", "rels")` when an address goes, `(member, "property:<name>", "value")`, `(settings, "trackRevisions",
   "value")` and `(custom part, "pid", "add")` for the property ops (a created `custom.xml`, `_rels/.rels` and
   `[Content_Types].xml` as whole members); `add_watermark` reads `verify:pgSz`/`verify:pgMar`/`verify:hfref` of every
   section showing a header it sizes for. Not declared, as for `set_font` (see "Plan-time conflicts for what an op
   reads" 5): the clean-up ops' reads of the style chains (hidden, colour) — their kinds re-derive from the SOURCE.
   Batch 3: `set_tab_stops` touches `(part, bi, "pPr")` and `(part, bi, "tabs")` per changed paragraph (so
   `set_paragraph_props` or `set_properties` paragraph flags on the same paragraph are a `conflict`; its run properties are
   not), reads `(part, bi, "verify:tabs")` of EVERY selected paragraph — its post-condition checks the output's stops of
   an already-as-asked paragraph too, so a second tab op changing them is a plan-time conflict (Codex on #28: it used to
   plan and fail V10 after apply), while a spacing change there still plans — and `(part, "scope", "verify")` per part in
   scope; with a margin position (`right_margin`/`center`) it reads
   `(main, "sectPr:N", "verify:pgSz"/"verify:pgMar")` of every section it measured and `verify:hfref` of every section
   for a header/footer part (a `set_page_setup` beside it is a plan-time conflict). Not declared: its reads of the style
   chains' tabs and of numbering (no op changes either), and `color_in`'s read of the colour chain (as `set_font`'s
   symbol rule).
   Batch 4 (`docs/BUILTIN_OPS_BATCH4_DESIGN.md` §0.1 and "Built"; `base.check_conflicts`' docstring is the grammar):
   three more forms. A **soft read** `verify-props:<family>` (`base.soft_read`) is reached by a change of that family only,
   never by a `text` change of the target (the reading kind re-resolves its targets over the output — an in-paragraph
   edit beside `set_properties` plans, as it did before batch 4). A **whole-member family read** `(member, "*",
   "verify[-props]:<family>")` is reached by a change of that family anywhere in the member. **Incremental changes**
   `uses+`/`uses-` of `(styles, "style:<id>")` — which elements name the style (`base.incremental`): two of one direction
   commute, opposite directions collide (`base.changes_collide`); a bare `verify` read of a style (its definition) is not
   reached by them (`base.read_covers`), `verify:uses` is by both, `verify:uses-` only by a removal.
   A paragraph-style change (`apply_style`, `map_styles`) touches `(part, bi, "pPr")` and `(part, bi, "pStyle")`, a
   character-style change of a run or mark `(part, bi, "rStyle")` (`map_styles`), a table-style change `(part, "tbl:<k>",
   "tblPr")` (k = ordinal over `root.iter(w:tbl)`); `apply_style` declares `uses+` of the style it applies and `uses-` of each
   restyled paragraph's old style, and soft-reads `pStyle` of a selected paragraph already in it; `map_styles` declares
   `uses-` of every `from` and `uses+` of every `to` (also where nothing in scope uses the from). An op whose prediction or
   post-condition reads a paragraph's effective formatting declares a soft `pStyle` read of it plus `selectors.chain_reads`:
   the nearest table's `(part, "tbl:<k>", "verify-props:tblPr")`, `verify:replace` (an import swapping the definition) of
   every style of the table, paragraph and character chains, `verify:add` of a style the chain names that the file lacks —
   `set_properties` (direct; also `verify:props` of the chain styles, its own families, which `set_properties` on a style
   declares as `(styles, "style:<id>", "props")`, so direct-beside-style is a conflict while `set_style_font` beside it still
   plans), `set_tab_stops`, `set_borders_shading` (direct). `set_properties` (direct) also soft-reads `rPr`/`rStyle` of every
   paragraph whose runs it selects, `markRPr`/`rStyle` for marks and `pPr` for paragraph flags, an already-as-asked target
   included (its post-condition reads them in the output). A selection that reads every paragraph's chain declares
   whole-member soft reads per part it chose from (`selectors.selection_reads`, added by `editspec.plan_spec`): `style_in`
   → `pStyle`; `has_bold`, `is_all_caps`, runs `color_in`, `remove_hidden_text`, `remove_notes`' colour/hidden rules and
   inline runs → `pStyle`, `rStyle`, `tblPr`. `set_borders_shading` touches `(part, bi, "pPr")` + `(part, bi, "pBdr" |
   "shd")` per changed paragraph (only the elements that change), soft-reads `pBdr`/`shd` of every selected paragraph and
   reads `(styles, "style:<id>" | "docDefaults", "verify:pPr")` of every level below; its styles target touches `(styles,
   "style:<id>", "pPr")`. `set_paragraph_props` on styles touches `(styles, "style:<id>", "pPr")` and reads `verify:pPr` of
   every selected style (and, when its lines rule ran, of the ancestors and docDefaults — the direct path reads the
   paragraph style lineage under the same condition). `clear_direct_formatting` touches `(part, bi, "rPr" | "markRPr" |
   "pPr")` and `(part, bi, <name>)` for every pPr child it clears (so `verify:tabs`/`verify:pBdr` readers conflict).
   `set_language` touches every changed run/mark rPr, style rPr and docDefaults rPr, reads `(part, "scope", "verify")` of
   every text part, whole-member soft reads `pStyle`/`rStyle`/`tblPr` of each, and `(styles, "*", "verify")`.
   `map_styles` reads `(styles, "style:<id>", "verify")` of every `from` and every `to` the document holds; `apply_style`
   reads the style it applies; `insert_paragraph` / `ensure_end_of_section` read the style they name. The style import
   touches `(styles, "style:<id>", "add")` per imported style, `(styles, "name:<casefolded name>", "add")` per imported
   copy (two imports bringing one name from two templates collide), `(styles, "style:<id>", a)` for `a` in `replace, rPr,
   pPr, tblPr` per replaced one, `(numbering, "*", "add")` when lists are copied, and reads `(styles, "style:<id>",
   "verify")` of every style the document holds that a copy names (basedOn/link/next) or an imported list level names.
   `set_style_font` with `"all"` or a type selector reads `(styles, "*", "verify:add")` and `verify:replace` (an import
   beside it would leave a style with its own font). `remove_unused_styles` touches `(styles, "style:<id>", a)` for `a` in
   `remove, rPr, pPr, tblPr` per removed style and reads `(styles, "*", "verify:uses-")`: an op taking uses away beside it
   (mapping, applying another style) would make the order decide what is unused — a plan-time conflict, the removal runs as
   a chain's last stage; an op adding uses plans beside it.
   Batch 5 (`docs/BUILTIN_OPS_BATCH5_DESIGN.md` §6 and "Built"): an op whose prediction COUNTS numbers (the four
   numbering ops) declares `lists.count_reads` — the numbering part whole `(numbering, "*", "verify")`, whole-member soft
   reads `verify-props:pStyle` and `verify-props:numPr` of the main part, `(main, bi, "verify:numPr")` per counted numbered
   paragraph, and `(styles, "style:<id>", "verify:pPr")` along the chain of EVERY counted paragraph (+ `verify:add` of a
   chain style the file lacks): a style replaced or re-spaced beside it is a conflict, a font change of it is not.
   Changes: a paragraph's numbering `(main, bi, "numPr")` (+ `pPr`, `pStyle`); a list definition `(numbering, "*", "lvl")`
   (whole member: any other op on the numbering part conflicts); `convert_typed_numbers` also `(main, bi, "text")` and
   `(main, "*", "verify:text")` (its post-condition reads every paragraph's line). Tables: `set_table_props` touches
   `(part, "tbl:<k>", "tblProps")` + `tblBorders|tblCellMar|tblW`, `(part, "tr:<k>:<r>", "trPr")` + `tblHeader|cantSplit`,
   `(part, "tc:<k>:<r>:<c>", "tcPr")` + `tcBorders|tcMar`, and reads `(part, "tbl:<k>", "verify-props:tblPr")` (its style)
   plus soft reads of its asked elements on every selected table, row and cell; `apply_table_style` touches
   `(part, "tbl:<k>", "tblPr")` (map_styles' aspect for a table style: paragraph ops in the table, which read it through
   `chain_reads`, conflict) and `uses+`/`uses-`. Sections: `different_first_page` touches `(main, "sectPr:N", "titlePg")`
   and reads `verify:titlePg` of every selected section; the content form of `select.sections` reads `(main, "*",
   "verify:text")` only when its predicate reads words, plus the selection's soft family reads (never for `with_tables` or
   `index_range`: only a whole-member change moves tables or section breaks). `remove_header_footer` now reads
   `verify:titlePg` of every section (its inheritance rule reads them; beside `different_first_page` it planned and passed
   with a first page showing nothing).
10. **Never create a part to make an edit work.** E.g. `remove_header_footer` refuses (`inheritance`) rather
    than add an empty header part. Adding members is allowed only when the op's whole purpose is the added
    member and it is predicted in `members_added` — today `apply_header_footer_template`, whose new parts
    are copies of the template's parts (never synthesized), each with a V5a template baseline and a
    `template_parts` assertion, and `swap_image`, whose one new media member is the user's picture file byte for
    byte (`image_swaps` compares it with the file), and (built-in batch 2) `set_document_properties` /
    `set_document_state`, which may add **`docProps/custom.xml`** — only that member, only when the file has no
    custom-properties part, holding only the asked properties, with its package relationship and its content-type
    override, predicted in `members_added`; V10 compares the created part whole with the verifier's own expected part
    and the package rels and `[Content_Types].xml` with the source plus exactly those entries (`verified_members`). A
    `docProps/custom.xml` no relationship names is refused (`malformed-properties`), never overwritten. `add_watermark`
    adds runs to existing header parts and never a header (`no-header`). The style import (batch 4) adds styles to the
    existing styles part and list definitions to the existing numbering part, never a part: a document without a
    numbering part refuses a numbered style (`style-numbering`), one without a styles part refuses (`style-import` /
    `structure`). A new part's content type must match its
    relationship type (V1 checks it).
11. **Fills are extracted, never typed.** A template slot gets its value from the target by a declared rule
    (`regex`, `paragraph_after`) or is the user's approved wording (`literal` → the op is `untracked` and the
    plan flags the words). V10 re-extracts every fill from the SOURCE by the same rule, independently. A
    reference slot (template spec) is the template's OWN value: the same rule run on the TEMPLATE, or the rule's
    `in_template` text — V10 re-derives it the same way. Never convert a value (case, spacing, "087100" → "08 71
    00"): a value the template shows in another form is refused (`template-value-left`), not normalised. The same
    fill rules give `ensure_end_of_section`'s number and `name_output`'s values (`literal` refused there); a file NAME
    may take a named transform (`digits`, `upper`, `lower`, `title`) — a document never does; `make_values_agree`
    places the truth's text as written and leaves a value that agrees (letters and digits casefolded) alone.
12. **The template file is an input like the source.** Pinned by md5 in the plan; apply refuses a changed
    template, V10 fails on one; the preview's approval token (`preview.approval_token`) hashes every template
    the spec reads (path, size, mtime, content) beside the batch files, so a template edited after the preview
    refuses to run until it is previewed again. A batch skips input files that are a spec's template (same path
    or bytes). Tables and pictures (built-in batch 1) are inputs the same way: `editspec.spec_input_files` (every op's
    `template` + `INPUT_FILE_PARAMS`: `fill_variables.table.file`, `replace_table.pairs_table.file`,
    `swap_image.image` / `match.image`) is what the approval token and `approval_inputs` hash ("input file X: changed
    since the preview"), `load_spec` resolves them against the spec's folder, `save_recipe` stores them absolute; the
    ops pin their md5 (apply refuses `table-changed` / `image-changed`) and V10 re-reads them. `spec_templates` stays
    templates only (the batch filter and `set_aside.json`'s `template`). `apply_style` and `map_styles` (batch 4) read
    their styles template through the same `template` param: pinned by md5 (apply refuses a changed file, `internal`),
    re-read by V10 (`styles_imported`, `applied_style`, `mapped_styles` fail on an md5 mismatch), hashed by the token.
13. **Claude's words are never the user's** (Phase 8). The words an op may place (`compiler.untraceable` over
    `placed_words`) must come from `Conversation.sources`: the request, `--answer` lines, every reply the user typed,
    the `wording` of a choice the user picked, and `PROJECT.json` without `_path` — each source checked on its own.
    Never Claude's message, understanding, labels, summary, assumptions, the app's notes or the brief. Choices are
    built by code (`_choices_from`): only `clarify`/`alternative`/`rephrase` from Claude; `custom_transform` only by
    the app, only when the submission said `"possible"`; wording holding any character `conversation.invisible` flags
    is dropped — the categories Cc, Cf, Cs, Co, Cn, Zl, Zp (tab aside), every Default_Ignorable_Code_Point
    (`INVISIBLE_RANGES`: some are letters or marks, e.g. U+3164, U+FE0F, U+034F) and U+2800, U+1D159, U+16FE4 (blank
    without being ignorable); never the Zs spaces (U+00A0…: they show as a space, and spec text has them). The spec a
    conversation hands the app (`_spec_turn`: a deep copy, for ok, questions and unsupported turns, chat and compile
    mode) carries `Conversation.request` in `request`, never Claude's text there — the advisor and the next pass
    (`set_aside.json` → `retry`) count a spec's request as approved; the API history keeps Claude's call as made.
    `conversation.json` records the sources as `approved_words` (beside `answers`); a source is never empty (no memory
    entry without a non-empty PROJECT.json — `memory_source` of none is `"{}"`, which traced the words "{}" —, no empty
    request or answer). The advisor's sources are the original spec's `placed_words`, the request and the answers,
    never the whole spec JSON (its docstring and `ADVISOR_SYSTEM` say so); a respec it accepts carries the ORIGINAL
    spec's `request` (`_respec_errors` pins it beside `applies_to`), never Claude's: `advice/<name>.spec.json` is run
    and advised on again. **Library choices (2026-10-16):** Claude may NAME one of the user's library items (`kind`
    `recipe`/`operation` + `name`; chat mode only — `LIBRARY_CHOICE_KINDS`, part of `APP_CHOICE_KINDS`, never of
    `CHOICE_KINDS_FROM_CLAUDE`); `_library_choice` builds the choice from the library alone — exact name via
    `library.find`; a recipe loads, validates, keeps its templates, places only visible words and dry-plans on the batch
    (`compiler._dry_errors`); an operation loads with its code unchanged; else dropped and said in the errors, to the
    user (an app entry) and to Claude (a note) — with `words` = `compiler.placed_words` of the recipe (deduplicated),
    `item_hash` = `library.item_hash`, `snapshot` = the validated spec; Claude's `label`/`wording` never reach it.
    `take_library_choice` refuses an item whose hash changed (`LibraryChanged`: nothing approved), appends `Choice.words`
    to `sources` (each its own source — `reply()` does the same for every choice) and hands the app a deep copy of the
    snapshot with `placed_words == words` asserted and `Conversation.request` in `request`. **Snippets (built-in batch
    1):** `snippet` is a kind Claude may NAME too (`NAMED_LIBRARY_KINDS`, chat only), built by `_library_choice` from
    the library alone (`words` = [its text], `item_hash`, `snapshot` = the snippet as read; its text must pass
    `clean_wording`) — but it is NOT an app choice: picking it is a `reply()` that checks the hash first (`_check_item`:
    `LibraryChanged`, nothing approved or sent, the turn stays open), approves the words and tells Claude to write the
    `insert_paragraph`; `take_library_choice`/`take_app_choice` refuse it. `placed_words` also covers `replace_table`
    pairs' `replace`, `set_issue_stamp.text`, `fill_variables.values` and `ensure_end_of_section`'s text around
    `{number}` (its default `END OF SECTION {number}` too — `compiler.END_OF_SECTION_DEFAULT`, pinned equal); a value a
    table file gives, the document's own words and a picked snippet's words are never Claude's. Claude reads tables and
    pictures only through `read_input_file` over the files `inputs.near` listed (`_Batch.inputs`; never another
    path).
14. **The conversation is append-only and every tool call is answered** (Phase 8). Opus 5.5 keeps its thinking,
    so history is never rewritten. `conversation.check_pairing` runs before every request: the last message is the
    user's and its leading `tool_result` ids equal the previous assistant turn's `tool_use` ids. The user message
    is built in one step (pending results, then notes, then the user's text) and appended before the call; a failed
    call (API error — `llm.is_api_error` —, refusal, `max_tokens`, `pause_turn`) appends nothing — the next reply is a
    new user message. A non-API exception of the call is a defect: it propagates and appends nothing either (the
    caller saves `conversation.json` first). A tool's own exception is answered as an `is_error` tool result, and so
    is a submission whose `check_submission` raises (`REJECTED — fix and call submit_editspec again:` with `[spec] the
    spec could not be checked (<Type>: <message>) — fix its shape …`; `validate_spec._check_shapes` refuses wrong
    shapes as `spec` first). Claude's `questions`/`choices` of the wrong shape are dropped and said in the turn's
    errors (`_texts`, `_choices_from`), never raised; `reply()` sets `turn = None` before it advances, so an answered
    turn's choices cannot be picked after a failed call.
    Tool calls, text and `Turn.message` are read from `llm.history_content(resp)`, never `resp.content` (a fallback's
    abandoned tool calls are neither echoed nor acted on). `system` and `tools` are frozen per conversation. Never
    send `thinking`, `tool_choice` or an assistant prefill (400 on Opus 5.5).
15. **Claude's visual check can stop a run, never pass one** (Phase 8). `vision.review_samples` never raises (a
    problem is `skipped`; a non-API error keeps its traceback in `VisualReview.transcript`; every transcript append
    sits inside the never-raise guards), is never a `CheckResult`, never appears in a verification record, never says
    PASS. Every stage is guarded — preparing the pages (image blocks included), the client factory, the call, reading
    the answer (`_read_answer`: raises on an answer that is not a review's shape, which becomes "Claude's answer could
    not be read (…)") — and the caller's `log` is wrapped (`_quiet`). A `client_factory` error is classified like the
    call's (`_skip_for`: `llm.failure_text`, `llm.error_entry`, traceback unless an API error). A skipped review has
    no findings: summary and findings are set together, at the end. Pages are labelled, and findings validated, by
    `compiler.file_labels` over all the samples (two folders' `Spec.docx` are `a/Spec.docx`, `b/Spec.docx`) — the
    labels the preview text uses (`preview.sample_labels`).
    `Session.run_blocked_reason()` is the first statement of `Session.run` (spec and custom transform alike); the
    acknowledgement is bound to the preview token (`acknowledge_review`) and is never persisted.
16. **Custom transforms are plain formatting on text parts and the styles part** (Phase 8). `codegen.hard_limits`
    refuses any revision delta of any kind and any part outside `allowed_parts_of(docx)`; the tracked-change
    helpers stay out of `ALLOWED_FROM` (`EXCLUDED_FROM_EXAMPLES`). `catalog.CUSTOM_TRANSFORM_LIMITS` lists only
    families a V10 kind checks (a test maps each). A custom transform is offered, never automatic: the CLI without a
    terminal needs `--allow-codegen` AND Claude's `"possible"` verdict (any other verdict, none included, is exit 2 —
    `--allow-codegen` accepts an offer, never makes one). Edited copies only. Its approval token
    (`preview.generated_key(g)` + the files) is checked again before it runs — the window's Run, and the CLI after
    y/N/`--yes` (`preview.changed_since_preview`); a transform whose token went stale is tried again by
    `Session.make_preview` (`_prove_again`), never shown again as it was. A transform is approved for the files as
    they were BEFORE it was written: `approval_inputs({}, files)` is taken before `generate_op` and compared (files
    only, `explain_approval_change(…, since="the custom transform was started")`) with the inputs the token is taken
    over — the window then leaves the token empty (Run refuses; its reply's preview tries the transform again at
    once); `cli._do_codegen` (`_changed_while_writing`) also compares once the preview is made and refuses, exit 2,
    before the visual check. `_prove` takes its token before the trials.
17. **The library never trusts a name or an old operation** (Phase 8). Names go through `library.slug` (Windows
    device names refused), lookups are exact matches against the listing, `_inside` checks every path before a read
    or delete. A saved operation is re-tried on the current batch's samples before its preview — first, in a new
    session folder (`use_operation`: `_prove` before `end_conversation`/`_use_folder`/`_show_generated`; a failing one
    changes nothing) — and refused when `op.py`'s sha256 differs from its manifest or it fails the whitelist. Only a
    current preview is saved. A name belongs to one item: `replace` works within a kind, and a name the other kind
    holds is refused even with it (`library.other_kind_holding` in `_check_free`; `cli._save_as_refusal` before
    anything is written). An operation Claude offered and the user picked (2026-10-16) follows the same trial-first rule
    with the conversation kept: `Session._take_library_choice` → `_use_saved_operation(end_conversation=False)`
    (`use_operation` is the same with `True`); `_prove(…, exc=)` raises `TrialsFailed` / `OperationFailed`, and
    `OperationFailed` is the ONE failure Claude hears (as `TransformFailed`), a defect withdraws the promised outcome; at
    the terminal `cli._try_saved_operation` runs the trials inside `_talk` and a failure returns to the prompt. A recipe
    picked there is used as the choice's snapshot, never a re-read file (`Session.recipe_name` says so).
18. **One call site** (Phase 8). Every Claude call goes through `llm.call` (streamed, `MAX_TOKENS` 64000, effort
    `max`, `fallbacks="default"`, 5-minute cache). Callers resolve the client when the call is made, through
    `compiler._client()` (the advisor through `advisor._client`) or an injected `client` — never captured at import.
    Usage is summed per serving model (`llm.Usage`); a model missing from `PRICES` is "price unknown", never guessed.
    One classification and one record format for every caller, also in `llm`: `is_api_error`/`api_error_text`/
    `failure_text` (and `ClaudeUnavailable`, raised by `client()` without the `anthropic` package) decide what is an
    API error and how it is said; `response_entry`/`results_entry`/`error_entry`/`transcript_content`/`block_dict` build
    the exchange's records — total (a `record_error` entry, never an exception) and without image data. `compiler`
    re-exports `is_api_error`, `api_error_text` and `block_dict` (master's import path).
19. **Tests never call Claude.** `tests/conftest.py` (autouse) gives every test a temp `SPEC_SURGEON_CONFIG_DIR`,
    removes API keys, makes `llm.client` raise and `cli._interactive` false; cuts every test off from Windows
    Credential Manager (`seal_credential_manager`: `credentials._win32cred` replaced by a function returning None, so
    `available()` False, `load()` None, `save()` raises, `delete()` False — only `tests/test_credentials.py`
    (`REAL_CREDENTIALS_TEST`) keeps the real functions, and its round trip uses a throw-away target; `credentials.py`
    stays the only module that touches `win32cred`, an AST scan checks it); seals `SEALED_ENV` (`ANTHROPIC_API_KEY`,
    `ANTHROPIC_AUTH_TOKEN`, `SPEC_SURGEON_MODEL`: set then deleted, so teardown restores exactly what was there before
    the test — the machine's keys, or their absence —, also after a test or the window's Settings wrote a key into
    `os.environ`); pops `SPEC_SURGEON_MODEL` from `os.environ` when conftest loads, for the whole run (nothing puts it
    back; so each test's teardown restores its absence), and pins `DEFAULT_MODEL` to `PINNED_MODEL` (`claude-opus-5-5`)
    in every loaded `spec_surgeon` module (`pin_model`). It makes `llm.credentials_found` return True (the tests script
    their clients, so every surface takes its Claude path as before) and `llm._sdk_credentials` False (an `ant auth
    login` profile on the developer's machine is never seen); a keyless test patches `credentials_found` to False, and
    `tests/test_keyless.py` tests the real function (taken at collection, before any fixture patches it).
    `tests/fake_claude.py` is the scripted streaming client (its `messages.create` raises; `raise_on_enter`/`raise_on_final`/`api_error` script API errors).
    A new test that needs Claude scripts it there. `llm.client`'s error in the tests is a plain `RuntimeError` — NOT an
    API error — so a test that forgot its script fails loudly instead of recording a failed turn; keep it so.

## Block-text definition (must stay identical on both sides)

Block = `w:p`. Text = concatenation, in document order, of `w:t`, `w:delText`, `w:instrText`,
`w:delInstrText`, `m:t` whose nearest `w:p` ancestor is that paragraph. Structural children counted
separately: `tab` (only when its parent is `w:r`), `br`, `cr`, `noBreakHyphen`, `softHyphen`, `sym`,
`ptab`, `footnoteReference`, `endnoteReference`, `commentReference`, `drawing`, `fldChar`.
Transform side: `ooxml.block_full_text()` / `jobs/tracked_replace.full_text_map()`.
Verifier side: `verify/textcheck.blocks()`. If one changes, the other must change the same way, and a
test must prove plans still match.

Accept view = drop `w:del`/`w:moveFrom` content, unwrap `w:ins`/`w:moveTo`. Reject view = the inverse,
with `w:delText`→`w:t`. Plans express `accepted_view` in accept-view terms (so field instructions stay in).

## Other definitions that must stay identical on both sides

(Every one below has a transform-side and a verifier-side implementation. Change both or neither, and keep the
test that proves they agree.)

- **Revision kinds (V4).** `w:ins`/`w:del`/`w:moveFrom`/`w:moveTo` count as `ins`… when their parent is not a
  `w:rPr`, as `ins_mark`… when it is (paragraph-mark revisions); `rPrChange`, `pPrChange`, `sectPrChange`,
  `tblPrChange`, `trPrChange`, `tcPrChange`, `tblGridChange`, `numberingChange`, `cellIns`, `cellDel`,
  `cellMerge` by local name. Transform side: `ooxml.revision_kind()/count_revisions()`; verifier side:
  `verify/suite._rev_kind()`. Plans predict a delta for every kind they move (missing key = 0). V4 also fails on any
  revision id (every kind, every text part) that occurs more often in the output than in the source — a split run
  carrying its original `rPrChange` id on both halves is a defect even though that (part, kind, id) existed before.
- **Selector grammar and symbol runs.** `scope`, `paragraphs` (`style_in` with the default paragraph style
  when no pStyle, `text_regex` on the block text above, `in_table`, `is_empty`, `index_range`), `runs`
  (`has_direct_font`, `font_in`, `not_in_revisions`, `color_in` — batch 3, below), `sections`, `styles`. A symbol run: any explicit font
  name (any slot, casefolded) in direct rFonts, the rStyle chain or the paragraph's pStyle chain is in
  `preserve_fonts`, or it holds a `w:sym` (when `skip_sym_runs`). Runs belong to their nearest `w:p`.
  Transform side: `ops/selectors.py`; verifier side: `verify/props.py`. `tests/test_ops.py::
  test_set_font_selector_bug_shared_by_plan_and_apply_is_caught` proves a divergence is caught.
- The **effective font** resolver of the `effective_font` post-condition exists only on the verifier side
  (`verify/props.effective_fonts`); no op needs it, so keep it there.
- **Slots** (decision §14.2: both conventions). `highlight`: a maximal sequence of consecutive runs (paragraph
  run order) whose direct `w:highlight` is in `highlight_colors` (default yellow) and that hold a `w:t`; name =
  `NAME` when the stripped highlighted text is `{{NAME}}`, else the stripped text itself. `braces`: every
  `{{[A-Za-z0-9_]+}}` in the display text, across run boundaries. A braces placeholder inside a highlight span is
  the same slot only when the highlighted text is exactly that placeholder; other overlap is refused. **Display
  text** = `w:t` whose nearest `w:p` is the paragraph, outside `w:del`/`w:moveFrom`, in document order (not the
  block text above: no delText, no field instructions). Filling: split the runs a boundary cuts, collapse the
  slot's runs into the first (it keeps its rPr minus the highlight for highlight slots), text = the fill. A slot of
  any marker whose runs lie in a field (`field_runs` / `_v_field_runs`: between a complex field's begin and end over the
  whole part, or inside `w:fldSimple`) is refused on both sides (`slot-in-field`; the verifier raises).
  Transform: `ops/slots.py`; verifier: `verify/template.py` (`slots_of`, `display_of`, `_run_model`).
- **Reference slots** (`slot_markers: ["reference"]`, alone). Template value per fill rule: `in_template` (non-empty
  string, stripped) else `extract_fill` on the TEMPLATE (a rule holding a literal needs `in_template`); < 3
  letters/digits (`value_tokens`) or a NUL → refused (`template-slot` / `bad-params`). **Located text** = display text
  with `SEP` (`\x00`) for every child of a display run other than `w:rPr`/`w:t`, plus the display offset of each
  position. Occurrence = a match of the value's words joined by `\s+` (IGNORECASE) in the located text that is
  **bounded**: each neighbour is absent, whitespace, SEP, or a non-word mark whose own far neighbour is not a word
  char (word = `isalnum()` or `_`). Matches sorted; overlap → `slot-overlap`; a touched run (non-empty display text
  overlapping) in a field (between a complex field's begin and end over the whole part, or inside `w:fldSimple`) →
  `slot-in-field`. **Leftover**: in each located-text piece between the occurrences, the value's `value_tokens`
  (`\d+|[^\W\d_]+`, casefolded) concatenated appear in the piece's consecutive tokens concatenated, starting and
  ending at a token edge or inside a LETTER token (never inside a digit token) → `template-value-left`
  ("DOORHARDWARECONSULTANT" holds "DOOR HARDWARE"; "1087100-01" does not hold "08 71 00"). Touched runs may hold other
  children (no TEXT_ONLY rule for reference slots); all slots of a paragraph's touched runs must share one parent.
  **Cut** (`carve`, every marker): a run whose display text overlaps a slot becomes, in child order, one run per
  maximal stretch of non-slot content (text outside the slot + other children; empty `w:t` dropped) and one run per
  slot piece; the slot's first piece (the run where it starts) holds the fill, later pieces go. Verifier model:
  `_run_model` builds the same per-run groups and compares each output run's content items (consecutive `w:t`
  merged, other children by masked signature) **in order**. Transform: `ops/slots.located_text/value_pattern/bounded/
  value_tokens/value_left_in/field_runs/reference_values/reference_slots/carve`; verifier: `verify/template._v_located/
  _v_value_rx/_v_bounded/_v_tokens/_v_left_in/_v_field_runs/reference_values_of/slots_of/_run_model/_items`.
  `tests/test_reference_template.py` compares the two locators and the two leftover rules (crafted and random cases)
  and plants shared defects (footer missed, wrong template value, loose bounds, a leftover rule that never cuts a word).
- **Fill rules.** `literal` (value verbatim), `regex` (`pattern` searched in the display text of every paragraph
  of `source` = `document` | `header.<type>` | `footer.<type>` as shown by each section or by `section`; group 1
  if any groups; ≥1 match, all equal), `paragraph_after` (exactly one paragraph matches `anchor_regex`; value =
  display text of the next non-blank paragraph). `strip` default true; empty → refused. Transform:
  `ops/slots.extract_fill`; verifier: `verify/template.fill_value`. `in_template` is only for reference slots
  (refused otherwise) and is not part of extraction.
- **Font size** (`size` points → `w:sz` half-points, whole/half points 1–1638; `size_from` points list). A level's own
  size = its `w:sz/@w:val` when all digits, else none; docDefaults' own size defaults to 20. A level gets the size
  when it has its own size and there is no `size_from` or its own size is listed — or, with `add_where_missing`
  (`set_style_font` with `only_where_defined: false`) and no `size_from`, when it has none. `set_font`: runs/marks
  never add a size; font goes on every selected run, or only on `has_direct_font` runs with `only_where_defined`; a
  run that gets neither is not selected. `w:szCs` is never touched; "everything else unchanged" is checked per
  target. Transform: `ops/selectors.size_values/size_applies`, `ooxml.own_size/set_size`; verifier:
  `verify/props._v_own_sz/_v_size_to/_check_font_rpr`. The **effective size** of the `effective_font` post-condition
  (`size`, `size_from`; symbol runs and `w:sym` runs exempt) exists only on the verifier side
  (`verify/props.effective_size`). `tests/test_font_size.py` compares the rules and plants shared defects.
- **Effective pinnable properties.** Paragraph: spacing before/after/line/lineRule, ind left/right/first
  (firstLine +v, hanging −v; start/end aliases), jc (start/end → left/right), tab stops (fold; `clear` removes).
  Run and paragraph mark: ascii/hAnsi (theme refs via the theme part), sz, color (val, themeColor, themeShade,
  themeTint), toggles b/i/caps/smallCaps. Order: docDefaults → table style chain (tblStyle, else default table
  style) → paragraph style chain (pStyle, else default) → character style chain (rStyle, else default; runs
  only) → direct. Toggles: direct wins; specified at exactly one style level → that value; none → off; more than
  one → refuse (`toggle-ambiguous`; the transform raises `AmbiguousToggle`, the verifier `ValueError`). Defaults:
  spacing 0/0/240/auto, ind 0, jc left, sz 20, Times New Roman, color ("auto", None, None, None). Transform:
  `ops/effective.Resolver` (also computes cs/szCs, which are not pinned); verifier: `verify/template.Eff`.
  `tests/test_template.py::test_transform_and_verifier_effective_resolvers_agree` compares them on every
  paragraph, mark and run of three packages.
- **Inherited formatting of an inserted paragraph** (`insert_paragraph`, `formatting: "inherit"`, the default).
  Paragraph properties = a copy of the anchor's `w:pPr` without `w:sectPr` and `w:pPrChange`, and without the revision
  marks (`w:ins`/`w:del`/`w:moveFrom`/`w:moveTo`) and `w:rPrChange` inside its paragraph-mark `w:rPr` (an emptied
  `w:rPr` is dropped); an explicit `style` replaces (or adds, first) the `w:pStyle`. Run properties = a copy of the
  `w:rPr` of the anchor's first live text run (nearest `w:p` is the anchor, not inside `w:del`/`w:moveFrom`, holds a
  `w:t`) without `w:rPrChange`. The new paragraph holds exactly one run with one `w:t`. Transform:
  `ops/insert_paragraph.inherited_ppr/inherited_rpr`; verifier: `verify/props._v_inherit_ppr/_v_inherit_rpr` (the
  output's pPr is compared with its own `w:ins` mark removed). `tests/test_audit_fixes.py::
  test_insert_paragraph_inherits_anchor_formatting_and_v10_verifies_it` plants a wrong output and a wrong shared
  implementation.
- **Tab rescale and text width.** Width = pgSz w − pgMar left − right − gutter. right/end stops:
  `pos + (W_target − W_template)`; center: `floor(pos × W_target / W_template + 0.5)`; others unchanged; never in
  table paragraphs. Transform: `ops/effective.rescale_tabs/text_width`; verifier: `verify/template.rescaled/_width`.

- **Formatting properties (`set_properties`, 2026-10-16; design `docs/SET_PROPERTIES_DESIGN.md`).** User-facing `run`
  properties expand to elements — `b` → `b`+`bCs`, `i` → `i`+`iCs`, `caps`, `smallCaps`, `strike`, `u`, `color`;
  `paragraph`: `keepNext`, `keepLines`, `widowControl`, `pageBreakBefore`. **Effective value with provenance**: toggles
  (`b`, `bCs`, `i`, `iCs`, `caps`, `smallCaps`, `strike`) — direct wins; specified at exactly one style level (docDefaults,
  table chain, paragraph chain, character chain: the level order of `effective_fonts`) → that value; nowhere → off;
  more → ambiguous (`None`); `u`, `color` (the (val, themeColor, themeShade, themeTint) tuple) and the paragraph flags —
  nearest level wins; defaults `"none"`, `("auto", None, None, None)`, flags off except `widowControl` on. Provenance:
  `direct` | `style:<id>` | `docDefaults` | `default`. **Gets**: a direct target gets an element whose effective value in
  the SOURCE differs from the asked one (ambiguous → gets); a symbol run (set_font's definition, plus `w:sym`) never gets
  `caps`/`smallCaps`; a style target gets an element whose own value differs. **Expected element**: toggles and flags on →
  no attributes, off → `w:val="0"`; `color` → exactly `{val}`; `u` → the source element's attributes with `val` replaced.
  **Style-target partition** (plan refusals / V10 FAIL): provenance the selected style → affected; a nearer style → noted,
  left alone; `direct` disagreeing → `conflict-property`; ambiguous → `toggle-ambiguous`; a style turning `caps`/`smallCaps`
  ON for a symbol run → `conflict-property` (off is fine). **Exclusive pair**: Word shows all caps and ignores small caps
  when both are in effect — an op asking `caps` or `smallCaps` on must leave at most one of them in effect on every
  selected (direct) or using (style) run and mark after the change, an ambiguous toggle counting as in effect: plan
  `conflict-property` (`caps_conflict`) / V10 FAIL (`_check_caps_exclusive`); both asked on → `bad-params` /
  `ValueError`. Transform: `ops/effective.PropResolver` (+ `run_elements`/`para_elements`; an
  `override` simulates the style change), `ops/set_properties.py`; verifier: `verify/props._v_run_prop`/`_v_para_prop`/
  `_v_gets_run`/`_v_gets_para`/`_v_style_gets`/`_v_expected_attrs`/`_properties`. `tests/test_set_properties.py::
  test_transform_and_verifier_property_resolvers_agree` compares them on every run, mark and paragraph of four fixtures;
  the planted-defect tests there share a wrong pair table, "already" rule, symbol rule, certainty check and on/off writer
  between plan and apply.

Built-in operations batch 1 (2026-10-16; `docs/BUILTIN_OPS_BATCH1_DESIGN.md` — each module's docstring is the full
definition):

- **Run text and in-paragraph edits.** Run text = the paragraph's runs (every ancestor below the `w:p` a container:
  `ins`, `del`, `moveFrom`, `moveTo`, `hyperlink`, `smartTag`, `customXml`, `sdt`, `sdtContent`, `fldSimple`, `dir`,
  `bdo`), runs under `w:del`/`w:moveFrom` skipped; per run child: `w:t` → its characters, `tab`/`ptab` → `\t`,
  `br`/`cr` → `\n`, `noBreakHyphen` → `-`, `softHyphen`/`lastRenderedPageBreak` → nothing (transparent), `rPr` and
  comments → nothing, anything else → `\x00`. An edit `(start, end, text, drop_highlight)` covers only `w:t`
  characters (`structure`), no run in a field (`field-result`), no other text-bearing element inside (`structure`);
  tracked: one parent for the touched runs — the paragraph, or a `w:ins` under it (`nested-container`) —, no
  `rPrChange` (`tracked-format`). Untracked: the characters leave their runs, the text goes where the first one was, no
  split. Tracked: each touched run split at the edges, each inside piece in its own `w:del`, the text after them in one
  `w:ins` (the first touched run's properties); inside someone's pending `w:ins`: the deletions stay inside it, the new
  `w:ins` follows it, the rest moves to a second `w:ins` (same attributes, fresh id) — only when text is placed.
  Transparent elements inside the range stay (untracked) / go into the `w:del` (tracked). Delta: `del` +1 per touched
  run, `ins` +1 per edit with text, +1 per split rest. Transform: `ops/textedit.py`; verifier: `verify/edits.py`
  (`v_items` — the item model: one item per character / other run child / paragraph-level element, each with its rPr
  signature, run attributes and container chain; `expected_items`, `compare_items`, `check_edited_paragraph`,
  `check_deleted_paragraph`). The kinds register what they checked in `ctx.verified_blocks` (source → output block);
  the residual leaves out exactly those. `tests/test_text_edits.py` plants shared defects in the matcher, the
  whole-word rule and the core.
- **Replacement pairs** (`replace_table`, `set_issue_stamp`): all pairs searched at once in the SOURCE run text of every
  paragraph in scope; literal (`re.escape`; `flexible_whitespace`: spaces ↔ any run of spaces/tabs/NBSP, hyphens ↔ any
  dash, quotes straight or curly) or regex; `match_case` default true; `whole_word` default true (a match starting or
  ending with a word character must not touch another); overlap of two pairs → `ambiguous`; more matches in the reader
  view (tabs and breaks as spaces) than in run text → `spans-structure`. Transform: `ops/replace_table.find_all`;
  verifier: `verify/textops.v_find_pairs` (`_v_flexible`, `_v_hits`).
- **Table files.** JSON (the object at a dotted `key`; pairs: a list of `{find, replace}` / `[find, replace]` or an
  object), CSV (UTF-8, BOM allowed, first row headers), XLSX (the named sheet, else the first; shared/inline/formula
  strings as stored; a number only in General/"0" format as ≤ 11 digits, else unusable; booleans, errors refused when
  used); columns by header text (defaults: first, second); a used value must be non-empty and free of characters the
  user cannot see (`table-value`). Transform: `ops/tables.py`; verifier: `verify/tables.py`.
- **Slots for `fill_variables`**: the templating slots (`slots.find_slots(…, text_only=False)` /
  `template.slots_of`); a slot's range in run text from its display offsets; highlight slots must be whole text runs and
  lose `w:highlight`; names compared with whitespace collapsed; typed `values` win over the table.
- **Option groups** (`resolve_options`): option = `[` text without `[ ] \t \n \x00`, not blank `]`; group = options
  separated only by spaces/NBSP; nested when a `[` before it is still open; first rule whose `match` finds an option
  decides; `pick` (exactly one match, else `ambiguous`) keeps that option's text; `drop` removes matching options (all:
  the group plus the spaces after it, else before it; some: each with its separator after, or — after every kept option
  — before). Transform: `ops/resolve_options.py`; verifier: `verify/textops._v_groups/_v_resolve`.
- **Tagged paragraphs**: `tag_regex` in run text (group `tag`, else 1, else the match); systems split by
  `tag_separator`, casefolded, whitespace collapsed; `keep` → stays when one system is listed, `delete` → stays when
  one is not; stays → the whole match stripped (`strip_tags`); goes → tracked: each direct run in its own `w:del` +
  the mark's `w:del` (bookmarks/proofErr stay; revisions or other content refused), untracked: removed (a table cell's
  last paragraph refused); section break, field, text box refused either way. Transform: `ops/filter_tagged.py`;
  verifier: `verify/textops._tagged_paragraphs`.
- **End of section**: the number by a fill rule; the line `text` with `{number}` once; the last block = the body's last
  child (before its `sectPr`) that is not an empty paragraph (run text only spaces/tabs/breaks, no `sectPr`); a matching
  last paragraph is left / its stretch from the first to the last non-space character replaced; a matching paragraph
  before the end → `eos-not-last`; else a new paragraph after the last block (`style`, or `copy`: the
  `copy_from_regex` paragraph's pPr without `sectPr`, `numPr`, revisions + its first text run's rPr). Transform:
  `ops/end_of_section.py`; verifier: `verify/textops._end_of_section` (`_v_empty_p`, `_v_copy_ppr`).
- **Agreeing values and output names**: values agree when their `value_tokens` (letters/digits, casefolded) run
  together are equal; a group's value is its span with surrounding whitespace left out; the file name place is the
  source's stem (`PureWindowsPath`, `.docx` off); a name is refused for `\ / : * ? " < > |`, Cc/Cf/Co/Cs/Cn/Zl/Zp,
  a leading space, a trailing space or dot, a device name, > 200 characters, empty; transforms `digits`/`upper`/
  `lower`/`title` (title: each `[^\W\d_]+(?:['’][^\W\d_]+)*` capitalised, the rest lowered). Transform: `ops/agree.py`,
  `ops/naming.py`; verifier: `verify/textops._agreed_values/_v_agree/_v_span`, `_output_name/_v_transformed/
  _v_name_problem` (it compares with the name the output was written under: `Ctx.output_name`).
- **Pictures** (`swap_image`): PNG (IHDR), GIF (logical screen), JPEG (markers to the first SOF other than C4/C8/CC;
  the first Exif APP1's orientation — 5–8 refused); proportions = stored pixels; `contain`: `W·ph ≤ H·pw` → (W,
  round(W·ph/pw)) else (round(H·pw/ph), H), halves up, a side of 0 refused; `stretch`: the box. A picture = a
  `w:drawing` whose inline/anchor holds `a:graphic/a:graphicData/pic:pic/pic:blipFill/a:blip`; ordinal among all
  `w:drawing` of the part; every other `a:blip` and every `v:imagedata` matched too and refused when matched; match by
  the media's md5, docPr name/descr/title (VML: `o:title`, the shape's `alt`) or all; refusals: `mc:AlternateContent`,
  linked, `svgBlip`, a non-zero `srcRect`, `rot`, missing or unequal sizes, a relationship another element uses;
  already-new left alone. Transform: `ops/images.py`, `ops/swap_image.py`; verifier: `verify/images.py`
  (`v_picture`, `v_fit`, `_scan`, `_why_not`); the residual masks exactly the checked drawings' two sizes
  (`props.mask_picture_sizes`, `ctx.masked_extents` by ordinal). `tests/test_swap_image.py` compares the readers and
  the fitted sizes and plants shared defects.
- **V2's plan-assisted pairing** (verifier only, `textcheck.diff_blocks(…, deleted_hint, inserted_hint)`): the plan's
  predicted untracked block deletions (source indices) and insertions (output indices) are set aside FIRST — reported as
  the deletions and insertions they are, whatever the output holds — and the rest is aligned on text (since built-in
  batch 2: it used to happen only inside an unequal-length replace region, and a deleted blank paragraph could pair with
  another blank paragraph far away and throw every block between them out of alignment). Not weaker: a predicted
  deletion that did not happen leaves its block in the output, an insertion nobody predicted
  (`tests/test_empty_paragraphs.py::test_v2_a_predicted_deletion_that_did_not_happen_still_fails`).

Built-in operations batch 2 (2026-10-16; `docs/BUILTIN_OPS_BATCH2_DESIGN.md` — the shared machinery, §1):

- **Structure-only declarations** (V2/V3): a `BlockDiff` with `before == after` (not None) and `struct_after` declares a
  change of structural children only (a comment's reference mark, a hidden tab). V2 leaves it out of the text changes;
  V3 accepts it for its own block only (`block_index` = the changed block's source index) with exactly those counts, and
  FAILS one whose change did not happen. It never exempts its paragraph from V10 (`Ctx.text_changed` leaves it out):
  the kind that made the change checks the paragraph item by item and registers it in `verified_blocks`, or the
  residual compares it whole (Codex on #27: an attribute or bookmark change in the same paragraph must not hide behind
  a mark removal). Transform: `BlockDiff.structure_only`, `base.text_diffs` (the `expect` counts and
  `planner.summarize_plan` leave them out); verifier: `suite.structure_only`.
- **Block text and structure without some elements** (transform side of the block-text definition): `ooxml.
  block_text_without(p, gone)` / `block_struct_counts(p, gone)` — the block's text elements / structural children whose
  nearest `w:p` is `p` and that lie in none of `gone`; with `gone` empty they equal `textcheck.blocks` (a test compares
  them on three fixtures). Plans predict removals with them, never by applying.
- **Fields** (§1.5): complex fields by `w:fldChar` begin/separate/end in document order over the whole part (an open
  field nests the next; instruction = its own code's `w:instrText`/`w:delInstrText`; a field beginning in another's code
  is nested in that code), simple fields `w:fldSimple`; type = the instruction's first word upper-cased (`=` counts);
  local = begin, separate, end in one paragraph that is not in a text box. Unlinking removes the three `w:fldChar` and
  its own instruction elements (then runs left with only `w:rPr`), a simple field is replaced by its children. Refusals
  in this order: `field-span`, `nested-field`, `form-field`, `existing-revisions`; page numbers (PAGE, NUMPAGES,
  SECTIONPAGES, PAGEREF) never. Transform: `ops/fields.py` (`fields_of`, `Field.refusal`, `unlink`); verifier:
  `verify/fields.py` (`v_fields`, `VField.refusal`). `tests/test_batch2_infra.py` compares the two parsers.
- **Effective hidden and colour** (§1.6): `w:vanish` a toggle over set_properties' levels (direct wins; exactly one
  style level → that; none → off; more → None = ambiguous, refused `toggle-ambiguous`); a paragraph mark read through
  its `w:pPr/w:rPr`; colour the nearest level's `w:color`, default `("auto", None, None, None)`; a colour list matches
  a 6-hex `w:val`, a `w:themeColor` name or `auto`, casefolded. Transform: `ops/markup.Look`, `color_in`; verifier:
  `verify/markup.v_hidden`/`v_mark_hidden`/`v_color`/`v_color_in`. Compared on three fixtures.
- **Whole-run removal and paragraph deletion** (§1.4): untracked — the run removed where it sits (containers stay);
  tracked — a `w:del` of its own around it in place, `w:t` → `w:delText`, `w:instrText` → `w:delInstrText`; paragraph
  deletion is `filter_tagged_paragraphs`' (moved to `ops/removal.py`: `deletable`, `plan_paragraph_deletion`,
  `delete_paragraph`, `plan_run_removal`, `remove_runs`). Verifier: `verify/markup.tracked_delete`/`drop` over the item
  model, `verify/edits.check_deleted_paragraph`.
- **V10's batch-2 hooks** (verifier only, `verify/props.Ctx`): `verified_parts` (text parts a kind compared whole — the
  residual skips them), `expected_removed` (paths of elements OUTSIDE paragraphs a kind expects gone — resolved in the
  residual's source copy before any stripping, removed after; a path inside a paragraph is a failure; the residual runs
  for such a part even when its bytes did not change), `verified_members` (non-text members compared whole: side parts,
  rels, `[Content_Types].xml`, a created `docProps/custom.xml`), `member_strip` + `_residual_members`: **any change to
  `settings.xml` or the document-properties parts** (core, app, custom — `reader.protected_members`) must be covered —
  the keys a kind verified (`props.member_key`: a top-level child's Clark tag, `property:<name>` in custom.xml) are
  stripped on both sides and the rest compared; a changed member nobody registered fails, a created one nobody compared
  whole fails. Item-model transforms for the kinds: `verify/markup.drop`/`unwrap`/`restyle`/`tracked_delete`/`insert`/
  `check_paragraph`; whole comparisons: `tree_difference`, `check_relationships`, `check_content_types`.
- **V5a on the document properties**: `docProps/core.xml` validates against the vendored OPC schema whose Dublin Core
  imports resolve to `schemas/dublincore/` (local definitions to the shape of DCMI's files; `xsd.LOCAL_IMPORTS`) —
  validation never uses the network.

Batch 2's operations (each module's docstring is the full definition; `docs/BUILTIN_OPS_BATCH2_DESIGN.md` §2–§12):

- **Comments** (`remove_comments`). A comment = a `w:comment` child of the comments part (the main part's `comments`
  relationship); its paraIds = the `w14:paraId` of its paragraphs. Its entry = the first `w15:commentEx` (direct child of
  `commentsExtended.xml`'s root) whose `w15:paraId` is one of its paraIds; its parent = the comment owning the entry's
  `w15:paraIdParent` (none when no comment owns it, or itself); its root = parents followed, cycle-safe; resolved = the
  root's entry has `w15:done` in `1/true/on`. Selection: `all` | `resolved` (root resolved) | `authors` (casefolded,
  whitespace collapsed); then every reply below a selected comment, recursively. Removed: those `w:comment`; every
  `w15:commentEx` / `w16cid:commentId` whose paraId is a removed comment's; every `w16cex:commentExtensible` whose
  `durableId` such a `commentId` carried; in every other text part every `w:commentRangeStart`/`End`/`w:commentReference`
  with a removed id, and a reference's run when it then holds only `w:rPr`. Parts and relationships never removed.
  Refused: a mark in a text box (`textbox`); duplicate or missing comment ids, a paraId two comments share, a reference
  outside a run (`structure`). Transform: `ops/comments.CommentBook` (`select`, `parent`, `root_of`, `resolved`),
  `side_removals`, `marks_of`, `in_text_box`; verifier: `verify/comments._VComments`, `_v_side_expected`.
  `tests/test_comments.py` compares the two selections on both fixtures for every `which` and plants shared defects.
- **Highlight targets** (`remove_highlight`). Every `w:r` whose nearest paragraph is in scope (deleted runs, hyperlinks,
  text-box copies included) and every paragraph mark (`w:pPr/w:rPr`) whose OWN `w:rPr` has a direct `w:highlight` with
  `w:val` in the colours (default: Word's 16; `none` never). Result: that element removed; plain — an emptied run `w:rPr`
  removed, an emptied mark `w:rPr` removed and then an emptied `w:pPr`; tracked — `w:rPrChange` (snapshot = the source
  rPr) appended; a target already carrying one, or a mark rPr holding its own `ins`/`del`/`moveFrom`/`moveTo`, is refused
  (`tracked-format`: the snapshot would copy the marker and V4 would see one id twice). Styles are flagged, never changed.
  Transform: `ops/highlight.highlighted`, `strip_highlight`, `canonical_colors`; verifier: `verify/highlight._v_lit`,
  `_v_check`, `_v_colors`.
- **Resolving tracked changes** (`resolve_tracked_changes`). Revision elements: `ins`/`del`/`moveFrom`/`moveTo`, the
  property changes and the cell revisions, outside any property change's snapshot. By parent: under a `w:rPr` a paragraph
  mark's when that rPr is a `w:pPr`'s, else an equation's (refused); under `w:trPr` a row's, under `w:numPr` a numbering
  marker's (refused); else content. Chosen: author listed (casefolded, whitespace collapsed) and wall-clock `w:date` in
  range (`YYYY-MM-DD[THH:MM[:SS[.f]]]` then an optional `Z`/offset, ignored; user dates `YYYY-MM-DD[THH:MM[:SS]]`;
  `on_or_after` inclusive, `before` exclusive; an unreadable date under a date filter → not chosen). Moves: a piece
  belongs to the innermost open range of its kind (document order, paired by id); the ranges sharing a name and their
  pieces are chosen whole (`partial-move`), their range marks go with them. Order: content (accept unwraps ins/moveTo,
  drops del/moveFrom; reject the reverse), then deleted text left in no deletion becomes live (`delText`→`t`,
  `delInstrText`→`instrText`, only what was inside an unwrapped deletion), the move ranges, property changes (accept
  drops; reject: rPr = the mark's own markers then the snapshot, pPr = the snapshot then rPr and sectPr; any other kind
  refused, `revision-kind`), marks (kept side: the marker goes; other side: the paragraph goes when its resolved content
  is only empty runs (`rPr`/`lastRenderedPageBreak`), proofing marks, wrappers of those and `_GoBack`, a paragraph
  follows it — block-level marks skipped — and it has no sectPr; else `paragraph-merge` / `section-break`), then an
  emptied rPr then pPr goes. Content that goes may hold no note/comment mark, no single end of a bookmark/permission/move
  range (`_GoBack` goes whole) and no part of a complex field (`marks-in-change`). Transform: `ops/revisions.classify`,
  `Selection`, `_moves`, `_marks_in_removed`, `_leftover`, `analyze`, `predict`/`gone_revisions`, `resolve` (in place);
  verifier: `verify/revisions._v_class`, `_v_when`, `_v_decide`, `_v_bare`, `_v_build` (a REBUILD of the part from the
  decisions), `_resolved_changes` (every part it resolves compared WHOLE — `ctx.verified_parts` —, the revision counts
  per V4 kind and the number chosen equal to the plan's; a part in scope with nothing to resolve is left to the residual,
  never claimed: claiming it failed a `set_font` on a footer the accept never touched, after a clean plan). `apply` re-runs `analyze` on the working copy and refuses (`internal`)
  a different count. `tests/test_revisions.py` checks the outputs against pandoc's accept/reject and plants shared
  defects (selection, semantics, the wrong paragraph, a skipped join or anchor check, a wrong count).
- **Hidden text, notes and blank paragraphs** (`remove_hidden_text`, `remove_notes`, `remove_empty_paragraphs`).
  *Content runs* of a paragraph: its runs (nearest `w:p`) that are live (no `w:del`/`w:moveFrom` above them) and hold
  something besides `w:rPr`. *Hidden paragraph*: mark hidden and every content run hidden (none counts); hidden runs by
  the effective-hidden definition above, ambiguous → `toggle-ambiguous` (on every content run; the mark only when every
  content run is hidden). *Note rules*: OR of rules, AND within a rule — `style_in` (pStyle, else the default paragraph
  style), `text_regex` (batch-1 run text), `color_in` (a content run with visible text exists and every such run's
  effective colour is listed; space-only runs ignored), `hidden` (a content run exists and all are hidden); evaluated in
  that order. Inline notes (`runs`): content runs of a listed colour and/or hidden, never inside a `w:hyperlink`, never
  holding `w:drawing`/`w:pict`/`w:object`. *Blank*: not in a text box, no `w:sectPr`, no revision element, no numbering
  (the first `w:numPr/w:numId` of own pPr → style chain → docDefaults decides; 0 = off), no `w:pBdr` with a child and no
  `w:framePr` on that chain, only `w:pPr`, `w:proofErr` and runs of `w:rPr`/`w:lastRenderedPageBreak`/`w:t` of spaces
  and NBSPs; collapse = every blank whose previous block is blank. *Keep rules* (per container = the paragraph's parent,
  over its blocks `w:p`/`w:tbl`/`w:sdt`/`w:customXml`/`w:altChunk`): the first block between two tables the deletions
  would join; the last block when tracked, or untracked when the rest would leave the container empty or ending in a
  non-paragraph. Hidden text and notes: a kept paragraph (and, hidden text only, one `deletable` refuses for anything
  but a text box) loses its content runs instead; notes refuse what `deletable` refuses. *Fields*: a run to remove in a
  complex field's span (begin→end over the part's runs) goes only with the whole outermost field (local, every content
  run of the span chosen and live, no `w:fldSimple` in it); in a `w:fldSimple` only with the whole outermost simple field
  (untracked; tracked refused); not reachable through containers → `structure`; in a text box → `textbox`. Transform:
  `ops/hidden.py` (`content_runs`, `RemoveHiddenText.choose`, `RemoveNotes.choose/_matches`, `keep_rules`, `FieldSpans`,
  `apply_field_rules`), `ops/empty_paragraphs.py` (`is_empty`, `numbered`, `bordered_or_framed`, `blank_paragraphs`);
  verifier: `verify/removal.py` (`_v_content`, `_v_choose_hidden`, `_v_choose_note`, `_v_note_rule`, `_v_keep`, `_VSpans`,
  `_v_field_rules`, `_v_blank`, `_v_numbered`, `_v_boxed`, `_v_blank_decisions`). The assertion carries the user's raw
  note rules and `expected` per part (`delete`, `runs` — own-run indices —, `simple`); the kinds compare their own
  decisions with it, check changed paragraphs by the item model and register them in `verified_blocks`, deleted ones by
  `check_deleted_paragraph` (tracked) or `deleted_src_blocks` + the paragraph count (untracked).
  `tests/test_hidden_notes.py` and `tests/test_empty_paragraphs.py` plant shared defects in each rule.
- **Watermarks** (`add_watermark`, `remove_watermark`). A watermark is a run of a header part whose one content element
  (any child but `w:rPr`) is a `w:pict`, `w:drawing` or `mc:AlternateContent` holding a VML `v:shape` whose `id` starts
  with `PowerPlusWaterMarkObject` (text) or `WordPictureWatermark` (picture), or a DrawingML `wp:docPr` whose `name`
  does; its text is the `string` of the first `v:textpath` that has one, else the `w:t` of its first `w:txbxContent`,
  else none. Refused: a watermark shape outside a run, in a run with other content, in a text-box paragraph
  (`structure`) or under `w:ins`/`w:del`/`w:moveFrom`/`w:moveTo` (`existing-revisions`). Headers (add): every header
  part some section shows, any type. Anchor: the part's first `w:p` whose every container below `w:hdr` is
  `w:sdt`/`w:sdtContent`; the new run goes first after its `w:pPr`. The run: `w:rPr/w:noProof` + `w:pict` holding the
  `_x0000_t136` shapetype (only when the part keeps none outside the replaced watermarks) and Word's text-watermark
  `v:shape` (id `PowerPlusWaterMarkObject<N>` new to the package, `o:spid` `_x0000_s<M>` above every `_x0000_s` number
  of the package, from 2049). Text area: (pgSz w − left − right − gutter, pgSz h − |top| − |bottom|) in points. Size:
  h/w = 1.55 / the advance width in em (`ops/watermark.WIDTHS`, measured once from Carlito / Liberation Sans / Liberation
  Serif; not re-derived by the verifier — a wrong proportion distorts the letters, which the render shows); auto
  diagonal: (w + h)/√2 = 0.98 × the shorter side of the smallest text area of the showing sections; auto horizontal:
  w = min(0.9 × width, 0.98 × height / proportion); points: h = 1.55 × size, and it must fit (else `watermark-size`).
  Numbers rounded to 2 decimals, written without trailing zeros. Removing never takes out the only definition of a VML
  shapetype a shape staying in the part still uses (unless the op brings `_x0000_t136` back). Transform:
  `ops/watermark.watermark_runs/watermark_text/first_paragraph/headers_shown/text_area/box/number/build_run/
  check_shapetype_users`; verifier: `verify/watermark._v_watermarks/_v_text/_v_anchor/_v_shows/_v_area/_fits/_v_num/
  _v_pict/_v_definition_still_used`. `tests/test_watermark.py` compares the two readings (5,000 random numbers) and plants
  shared defects.
- **Links** (`remove_hyperlinks`). Link element = `w:hyperlink`: external with an `r:id`, internal without. Link field =
  a HYPERLINK field: internal when its instruction holds the switch `\l` — an unquoted token, any case; tokens are `"…"`
  (an unterminated quote runs to the end) or runs of other non-space characters. Link style = a character style whose
  `w:name` is Hyperlink or FollowedHyperlink (casefolded). The runs of a link element: its `w:r` whose nearest `w:p` is
  the link's paragraph; of a link field: its result runs (holding a child other than `w:rPr`, after `separate`, before
  `end`; a simple field: the runs in it). Removed addresses: hyperlink-type relationships a removed link element named
  that no other element of the part names (any attribute in the r: namespace). Transform: `ops/links.
  instruction_switches/field_is_internal/link_style_ids/link_runs/result_runs/addresses_to_remove`; verifier:
  `verify/links.v_switches/v_field_internal/v_link_styles/_v_link_runs/v_result_runs`. `tests/test_links.py` compares the
  switch readers on 600 random instructions and plants shared defects.
- **Unlinking fields** (`unlink_fields`, and `remove_hyperlinks` for link fields). `types` upper-cased, `"all"` = every
  type but PAGE/NUMPAGES/SECTIONPAGES/PAGEREF (never converted; refused when named, `bad-params`). A selected field is
  refused in a text box (`textbox`), then by `Field.refusal`/`VField.refusal`, then when it sits in another field's code
  (`nested-field`). Both ops store per paragraph the place of each chosen link and field with its identity (`r:id`/anchor,
  instruction); apply finds them again by paragraph and refuses (`internal`) if they moved — never whole-part ordinals
  (the other op in the same spec shifts them). Transform: `ops/links.normalize_types/type_selected/_field_refusal`,
  `ops/fields.unlink`; verifier: `verify/links.v_types/_v_refusal`.
- **Document properties and state** (`set_document_properties`, `set_document_state`). Core (the package's
  `core-properties` relationship): `title` dc:title, `subject` dc:subject, `author` dc:creator, `keywords` cp:keywords,
  `comments` dc:description, `category` cp:category, `status` cp:contentStatus, `last_modified_by` cp:lastModifiedBy; app
  (`extended-properties`): `company` Company, `manager` Manager. Set: the top-level element's children go, its text is
  the value, its attributes stay; a missing one is created at the end of the root; clear: removed — absent or empty is
  already clear. Custom (`custom-properties`): matched by name casefolded; an existing text property keeps its value tag;
  another type → `property-type`; a new one appended with fmtid `{D5CDD505-2E9C-101B-9397-08002B2CF9AE}`, pid = the
  source's largest pid (at least 1) + 1, +1 per earlier new one in the request's order, `vt:lpwstr`. Mark as Final =
  custom `_MarkAsFinal` `<vt:bool>true</vt:bool>` + `cp:contentStatus` `Final`; unmarking removes `_MarkAsFinal` and a
  `Final` status only. Track Changes: off removes `w:trackRevisions` (on unless w:val is 0/false/off); on inserts
  `<w:trackRevisions/>` in CT_Settings order or removes an off one's w:val; a lock (`w:documentProtection
  w:edit="trackedChanges"`, enforcement 1/true/on) refuses off (`protected`). Transform: `ops/docprops.PROPERTIES`,
  `Changes`, `CustomProps`, `SETTINGS_ORDER`; verifier: `verify/docprops._V_PROPERTIES`, `_Check`, `_settings_order()`
  (read from the bundled `wml.xsd` itself). `tests/test_docprops.py` pins the tables equal and plants shared defects.

Built-in operations batch 3 (2026-10-16; `docs/BUILTIN_OPS_BATCH3_DESIGN.md`):

- **The run selector `color_in`** (`set_properties`, `set_font`). A run or paragraph mark is selected when its effective
  colour — set_properties' colour rule: the nearest level setting `w:color` (direct → character style chain → paragraph
  style chain → table style chain → docDefaults), default `("auto", None, None, None)` — has a listed `w:themeColor` name or
  `w:val` (batch 2's colour-list definition: stripped, a leading `#` dropped, casefolded). Every entry must be 6 hex digits,
  `auto` or a theme colour name (`bad-params`). Read from the SOURCE. Transform: `ops/selectors.rpr_selected(pred, rpr,
  revised, p, sheet)` → `effective_color` (a `PropResolver` kept on the sheet, so the example op `set_font.py` imports
  nothing new — `codegen`'s example rule) + `markup.color_in`/`color_names`, `check_run_selector` (`markup.valid_color`);
  verifier: `verify/props.target_matches(…, color_of=)` with `_v_run_prop(…, "color")`, `_v_sel_colors`, `_v_sel_color_in`.
  `tests/test_color_selector.py` compares the two on every run and mark of two fixtures and plants matchers that forget the
  styles or the theme names.
- **Tab stops** (`set_tab_stops`). *Own entries*: the paragraph's `w:pPr/w:tabs/w:tab` `(pos, val, leader)` (leader absent =
  `none`; `clear` = a clear entry); a `w:tabs` child other than `w:tab`, an entry without a whole-number position or two at
  one position refuse (`structure`). *Inherited*: the "Effective pinnable properties" tab fold without the paragraph's own
  pPr (`effective.Resolver.paragraph(direct=False)` ↔ `template.Eff.para(with_direct=False)`). *In effect*: inherited
  folded with own. *Numbered*: batch 2's numbering definition (`empty_paragraphs.numbered` ↔ `removal._v_numbered`).
  *Margin positions*: `right_margin` = the text width W (the existing text-width definition), `center` = W // 2; a body
  paragraph's section = of its outermost paragraph, the number of paragraphs holding `w:pPr/w:sectPr` before it in
  `root.iter(w:p)` order (`set_tab_stops.paragraph_sections` ↔ `tabs._v_sections`); a header/footer part: every section
  showing it (`hf_common.effective_refs` ↔ `template._refs_shown`), all one W; else `tab-position`. *The rule*: S = the
  asked stops, C = the clear positions (+ with `clear_others` every position in effect not in S); X in S is already when
  (numbered: own entry at X == S[X]; else: in effect at X == S[X]), else own entries at X go and S[X] is written; X in C is
  already when (numbered: own entry at X is a clear entry; else: nothing in effect at X), else own entries at X go and a
  clear entry is written when a stop is inherited at X or the paragraph is numbered. A changed paragraph's entries = the
  untouched source entries + the new ones, sorted by position (stable); none → `w:tabs` removed; a new entry is `w:val`,
  `w:leader` unless none, `w:pos`. Values compared exactly (`start` ≠ `left`). Transform: `ops/set_tab_stops.decide`,
  `own_entries`, `inherited_stops`, `in_effect`, `Margins`, `write_tabs`; verifier: `verify/tabs._v_decide`, `_v_own`,
  `_v_inherited`, `_v_fold`, `_VMargins`, `_check_tabs_out` (+ the output post-condition: the asked stops in effect, no stop
  at a cleared position — numbered: an own clear entry —, with clear_others nothing else). `tests/test_tab_stops.py`
  compares the section readers on five fixtures and the rules on 3,000 random cases, and plants shared defects in each.

Built-in operations batch 4 (2026-10-17; `docs/BUILTIN_OPS_BATCH4_DESIGN.md` — each module's docstring is the full
definition):

- **Paragraph borders and shading** (`set_borders_shading`). *Levels below*: a direct target — docDefaults pPr → table
  style chain (tblStyle, else the default table style) → paragraph style chain (pStyle's value — an empty or missing
  value names no style, as in `template.Eff`/`effective.Resolver` —, else the default; `borders._v_paragraph_style_id`),
  each root-most first; a style target — docDefaults pPr → its basedOn ancestors root-most first (not itself); a style of
  another type is `style-mismatch`. *Visible side*: a side
  element whose `w:val` is not `nil`/`none` (absent = visible). *Visible shading*: a `w:shd` not `w:val="nil"` and not
  (`clear` with `w:fill` absent or `auto` and no `w:themeFill`). *Expected pBdr*: sides in the order top, left, bottom,
  right, between, bar — an asked side exactly `{val, sz, space, color}`, a side not asked that ANY lower level shows
  visibly `{val: nil}`, else absent; none → no pBdr. *Expected shd*: asked → exactly `{val, color, fill}`; `"none"` →
  `{clear, auto, auto}` when the NEAREST lower level specifying a `w:shd` shows visible shading, else absent. *Already*:
  the own element sig-equal to the expected one (both absent counts). Only the elements that differ are written (old
  removed, expected inserted in `PPR_ORDER`); an emptied paragraph pPr (plain) or style pPr goes. Post-condition (verifier
  only): every selected target's output shows the asked box under both inheritance readings (the nearest level holding a
  pBdr decides all sides / the nearest level naming a side decides it) and the asked shading (nearest `w:shd`; none
  visible for `"none"`). Transform: `ops/set_borders_shading.clean_params/paragraph_levels/style_levels/side_visible/
  shading_visible/expected_borders/expected_shading/same_element/decide/write_elements`; verifier: `verify/borders.
  _v_asked/_v_below_paragraph/_v_below_style/_v_side_shows/_v_shd_shows/_v_box/_v_shade/_v_gets/_check_elements/
  _check_shown`. `tests/test_borders_shading.py` compares the two decisions on every paragraph and paragraph style of two
  fixtures for four asks, and plants shared defects (levels without the document defaults or the table style, a box
  without nil sides, an attribute-blind already rule, clear-auto counted as shading, a style's levels including itself,
  a wrong default size, a wrong default scope).
- **Spacing, indents and alignment on styles** (`set_paragraph_props`, `select.styles`), and the refusals of both its
  targets. *Merge*: the asked attributes set, the others kept, the first line (`firstLine`/`firstLineChars`) drops the
  hanging indent (`hanging`/`hangingChars`) and vice versa, `jc` gets `w:val` — the paragraphs' merge
  (`set_paragraph_props.merged_attrs`/`write_values`, shared by the direct path) ↔ `verify/props._v_merged`. *Already*:
  every asked element present in the style's own pPr with the merged attributes equal to its own. *Levels*, nearest
  first: a paragraph — its own pPr, its paragraph style chain (pStyle's value, an empty one naming none, else the
  default), its table's style chain, docDefaults (`set_paragraph_props.paragraph_levels` ↔ `props._v_paragraph_levels`);
  a style — its own pPr, its basedOn ancestors, docDefaults (`style_levels` ↔ `_v_style_levels`). *Refusals*
  (`conflict-property`, `set_paragraph_props.inherited_refusal` ↔ `verify/props._v_inherited_refusal`; V10 fails "the
  op must refuse", direct path and styles target alike): `before`/`after` asked where the NEAREST level naming
  `…Autospacing` turns it on; `before`/`after` asked where ANY level sets `…Lines` (the lines not asked); an indentation
  side asked in twips (left/start, right/end, the first line: firstLine/hanging) where any level gives that side in
  characters (`leftChars`/`startChars`, `rightChars`/`endChars`, `firstLineChars`/`hangingChars` — Word's priority), the
  ones the merge removes from the target itself excepted, the side's Chars not asked; an `ind` alias whose other spelling
  the target's own `w:ind` uses. The styles assertion carries the user's own parameters (`params`, `select`); the kind
  reads them itself (`_v_ppr_values`) and fails `tracked: true`. A style of another type is `style-mismatch`.
  `tests/test_style_paragraph_props.py` plants a merge that keeps `hanging`, refusal rules that read only the target
  itself, forget Autospacing or forget the characters (both targets), a merge that writes nothing and a cleaning that
  adds a default `lineRule`.
- **Clearing direct formatting** (`clear_direct_formatting`; design `docs/BUILTIN_OPS_BATCH4_DESIGN.md` §3). Cleared run
  names (default) `rFonts b bCs i iCs caps smallCaps strike dstrike outline shadow emboss imprint color spacing w kern sz
  szCs u effect bdr shd fitText em`, opt-in `highlight vertAlign position`; cleared paragraph names `keepNext keepLines
  pageBreakBefore widowControl suppressLineNumbers pBdr shd tabs suppressAutoHyphens spacing ind contextualSpacing
  mirrorIndents jc textDirection textAlignment textboxTightWrap`, opt-in `outlineLvl framePr suppressOverlap`; never
  everything else (w: namespace only). The set for a target = (default ∪ also) − keep, per target type (`keep`/`also`
  from the cleared and opt-in lists only, none in both: `bad-params`). Symbol runs (set_font's definition + `w:sym` when
  `skip_sym_runs`) and symbol marks (without the w:sym rule) never lose `rFonts`/`caps`/`smallCaps`. Result: the source
  element minus the cleared children, in order; plain — an emptied run rPr removed, an emptied mark rPr removed and then
  an emptied pPr (one the op changed); tracked — rPrChange (snapshot the source rPr) / pPrChange (snapshot the source pPr
  without rPr, sectPr, pPrChange); existing markers refuse tracked (`tracked-format`), flagged plain; a tracked change on a
  mark that is a pending revision refuses. Transform: `ops/clear_formatting.RUN_CLEARED/RUN_OPT_IN/PARA_CLEARED/
  PARA_OPT_IN/SYMBOL_KEPT`, `cleared_names`, `gone_names`, `paragraph_clearing`, `check_markers`, `clear_rpr`,
  `clear_paragraph`; verifier: `verify/clearing.V_RUN_CLEARED/V_RUN_OPT_IN/V_PARA_CLEARED/V_PARA_OPT_IN/V_SYMBOL_KEPT/
  V_DEFAULT_PRESERVE`, `v_cleared_sets`, `_v_gone`, `v_decide`, `check_cleared_rpr`, `check_runs_and_mark`,
  `v_expected_ppr`, `check_cleared_ppr`, `check_empty_ppr` (the symbol rule through `props.symbolic`). The check matches
  what the op declares (review round): the pPr's other children are compared only when the op changed the pPr (it
  touches `pPr` + each cleared name), the mark's rPr only when it changed the mark (`markRPr`; `mark=` of
  `v_expected_ppr`/`v_ppr_sequence`/`check_cleared_ppr`, which `apply_style` passes too), and "an emptied pPr goes" is
  judged on the OUTPUT (an empty `w:pPr` left in a paragraph the op changed fails, whichever op ran first). A plain clear
  that empties a mark without touching the pPr's own children soft-reads those children (`emptying_reads`) when every one
  is a property some op may remove, so whether the pPr empties cannot depend on the order. `tests/test_clear_formatting.py::
  test_transform_and_verifier_decisions_agree` compares the two decisions on every paragraph of four fixtures under four
  settings and plants shared defects (vanish, vertAlign or numPr in the cleared sets; a symbol rule that forgets the
  paragraph style chain or w:sym; an empty default symbol-font list; marks skipped; emptied elements left).
- **Proofing language** (`set_language`; design §4). Levels: docDefaults' rPrDefault/rPr, every style's own rPr (keyed by
  styleId, the last of a repeated id; a style without one left alone), every run's and paragraph mark's own rPr in every
  text part; never rPrChange snapshots, numbering, sdtPr, tblStylePr. A level gets attribute A = V when its own w:lang
  carries A with a value ≠ V (exact; with `replace_only`, casefolded in the list); docDefaults without replace_only also
  when absent (docDefaults/rPrDefault/rPr/lang created in schema order; no styles part → `structure`); the lang's other
  attributes stay. Transform: `ops/set_language.clean_params`, `language_levels`, `level_gets`, `write_lang`,
  `doc_defaults_rpr`; verifier: `verify/language._v_asked`, `_v_levels`, `_v_gets`, `_check_lang_rpr`,
  `_check_doc_defaults_frame`. The **effective language** of the post-condition (per attribute: direct → character style
  chain (rStyle, else default) → paragraph style chain (pStyle, else default) → table style chain → docDefaults, the first
  whose w:lang carries it) exists only on the verifier side (`verify/language.v_effective_lang`), as the effective font.
  `tests/test_language.py` plants shared defects (docDefaults skipped, styles skipped, a case-sensitive replace_only, a
  writer that drops the lang's other attributes).
- **Style import** (built-in batch 4 §5; shared by `map_styles` and `apply_style`). Resolution of a style reference: exact
  `w:styleId`, else the one `w:name` equal by `name_key` (whitespace collapsed, casefolded; two → `style-mismatch`), else
  none. Copied = imported ∪ replaced.
  Closure: the requested template styles; for every COPIED closure style its `basedOn`/`link`/`next` targets and the
  `w:pStyle` of every level of its numbering definition (`abstractNum/lvl`, `num/lvlOverride/lvl`), to a fixpoint; a style
  the document holds and does not replace is in the closure (held) but not walked; a reference the template does not
  define is skipped. Imported = closure styles the document lacks (template order); replaced (`existing: "replace"`) = the
  requested styles the document holds; refusals `style-import` (no styles part, an imported template default, a replaced
  style that is a default on either side or of another type, a copy's `name_key` another style holds after the import,
  an extension element carrying text, `mc:AlternateContent` anywhere in a copy, a HELD style whose name differs from the
  template style of that id — another style under the same id —, naming both) and `style-numbering` (no numbering part, a
  numId not a whole number, num/abstractNum missing, `numStyleLink`/`styleLink`, `lvlPicBulletId` in a level or a
  `lvlOverride` level, `mc:AlternateContent` in a list, a copied list's level naming a held style the file numbers with its
  own list — the first numId on its basedOn chain; a held level style the file does not number is flagged). The template's
  numbering part is read only when a copied style is numbered (malformed → `bad-params`, fatal: the same for every file).
  New ids: copied styles in template order, each template numId first met → above every numId the document DEFINES OR
  REFERENCES (every `w:numId/@w:val` of the text, styles and numbering parts, `w:numIdMacAtCleanup`; none → 1), its
  abstractNum first met → above every abstractNumId defined or referenced (none → 0) — a dangling reference is never
  captured by an imported list. Copies: extension markup stripped, ids set. Places: abstractNums after the last
  abstractNum (else before the first num, else before `numIdMacAtCleanup`, else at the end), nums after the last num (else
  before `numIdMacAtCleanup`, else at the end); imported styles appended, replaced in place. Transform:
  `ops/style_import.name_key/resolve_style/closure_of/style_links/numbering_styles/own_num_id/check_definition/same_style/
  numbered_in/used_ids/next_ids/allocate_ids/stripped/build_copies/insert_numbering/plan_import/apply_import/
  check_template_unchanged`; verifier: `verify/restyle.v_resolve_style`, `verify/styleimport.v_derive_import/_v_refs/
  _v_levels/_v_num_id/_v_lists/_v_chain_list/_v_has_alternate/v_used_ids/v_expected_numbering/check_styles_imported/
  check_numbering_unchanged` — `numbering.xml` is compared whole by the import kinds even when no list is copied (by
  `styles_imported`, or by `check_requested` when the op imports nothing), unless another import of the plan copies lists
  and owns the comparison.
  The style copies use `apply_header_footer_template.strip_foreign` ↔ `template.strip_extensions`.
  `tests/test_style_import.py` compares the resolvers on every style of both fixtures, the derivations on 14 requests ×
  2 documents and the placements on 6 numbering shapes, and plants shared defects (closure without link/next, without list
  levels, walking held styles; an allocator reusing ids).
- **Style mapping** (`map_styles`, §7). Pairs: one `from` twice or a `to` that is another pair's `from` (as written) →
  `bad-params`; with a template every `to` resolves in the template (none → `bad-params`); per file `from` resolves in the
  file (none → `missing`), the style mapped to = the template's (replace) / the file's when `to` resolves there, else the
  template's, else `missing-style` (keep); `from` = it → `same`; one type of paragraph/character/table, no default character
  or table style as `from`, no two `from` resolving to one style, no style mapped to that is another's `from` →
  `style-mismatch`. Elements: paragraphs whose style (`w:pStyle/@w:val`, else the default paragraph style) is a paragraph
  `from`; runs (nearest paragraph) and paragraph marks whose `w:rStyle` is a character `from`; tables whose
  `w:tblPr/w:tblStyle` is a table `from`; a pair without elements is `unused` and imports nothing. The change: `w:val` set
  (a new `w:pStyle` first in the pPr, a pPr first in the paragraph); tracked: `pPrChange` / `rPrChange` with the source
  snapshot. A character `from` also used where the mapping cannot reach (`w:sdtPr/w:rPr`, `w:sdtEndPr/w:rPr`, `m:r/w:rPr`
  in the parts in scope, `w:lvl/w:rPr` in numbering) refuses `structure` — never left referenced while every use is
  claimed changed. The paragraph check compares the mark's rPr only when the kind restyled the mark; else the residual
  does. Transform: `ops/map_styles.clean_params/paragraph_style_of/run_style_of/table_style_of/mapped_holders/
  unmapped_uses/set_paragraph_style/set_run_style`; verifier: `verify/restyle._v_map/_v_pairs/_v_holders/_v_rstyle/
  _v_tblstyle/_v_unmapped/_check_paragraph/_check_rpr/_check_table/_mapped_styles`. `tests/test_map_styles.py` plants shared defects (resolution by
  id only, unstyled paragraphs forgotten for the default style, marks forgotten, tables forgotten, a tracked default, a
  default scope without the headers).
- **Applying a style** (`apply_style`, §6). The style resolved by the style import's resolution — map_styles' rule: with
  `existing: "keep"` the document's own when it has one, else the template's (imported); with `"replace"` always the
  template's (a template given must hold it as a paragraph style: `bad-params`). A paragraph is restyled when its style
  (pStyle, else the default paragraph style) is not the asked one; with `direct_formatting: "clear"` its targets are
  clear_direct_formatting's under the DEFAULT rules; neither → untouched and uncounted. The change: `w:pStyle` first in the
  pPr (a pPr first in the paragraph) and the cleared children gone, in ONE `w:pPrChange` when tracked (the snapshot taken
  before both). Transform: `ops/apply_style.ApplyStyle.plan/apply`, `set_pstyle` over `clear_formatting.paragraph_clearing/
  check_markers/changes_needed/clear_paragraph(ppr_edit=…)` and `style_import.resolve_style/TemplateStyles/plan_import/
  apply_import`; verifier: `verify/applystyle._applied_style` over `restyle.v_resolve_style`, `styleimport.check_requested`
  (the op's `styles_imported` assertion must carry exactly the template style the kind found it needs), `clearing.v_decide/
  check_runs_and_mark/v_expected_ppr/v_with_pstyle/check_cleared_ppr`. `tests/test_apply_style.py` plants shared defects
  (the default paragraph style forgotten, a clear set revealing hidden text, the pStyle written last, the import left out
  of the assertions).
- **Unused styles** (`remove_unused_styles`). Styles = the top-level `w:style` of the styles part; two with one
  `w:styleId` refuse (`structure`); type `w:type` (default paragraph); default/custom = `w:default`/`w:customStyle` on
  (1/true/on); a style's names = its id, `w:name/@w:val` and each comma-separated stripped entry of `w:aliases/@w:val`,
  casefolded, empty ones dropped. Scanned parts = every text part + every numbering and settings part (main part's
  relationships). Referenced = the `w:val` of every W-namespace `pStyle`, `rStyle`, `tblStyle`, `numStyleLink`,
  `styleLink`, `defaultTableStyle`, `clickAndTypeStyle` anywhere in a scanned part (snapshots included). Named by fields
  = per text part, the casefolded concatenation in document order of `w:instrText`/`w:delInstrText` texts and
  `w:fldSimple/@w:instr`: a style one of whose names is a substring; field-generated = a text part whose instruction
  text holds `toc` keeps the styles named `toc 1`…`toc 9`, `table of figures`; `index`: `index 1`…`index 9`,
  `index heading`; `toa`: `table of authorities`, `toa heading`. Keep = a style one of whose names equals an entry,
  stripped and casefolded. Used = every default ∪ referenced ∪ named ∪ field-generated ∪ kept. Eligible = has an id,
  type in `types` (default all four), custom when `which: custom`. Stays = used ∪ every non-eligible style (id-less
  included), closed under the `w:val` of `w:basedOn`/`w:next`/`w:link`. Removed = eligible styles not staying, in part
  order. Transform: `ops/remove_unused_styles.style_table/style_names/eligible/scanned_parts/references/instruction_text/
  named_in_fields/field_generated/kept_by/seed_styles/closure/analyze`; verifier: `verify/unused._v_styles/_v_names/
  _v_eligible/_v_parts/_v_refs/_v_instr/_v_named/_v_kept/_v_reach/_v_removable` (+ the output post-condition
  `_v_dangling`: every reference — scanned parts, every output style's basedOn/next/link, field names and field-generated
  names — to a style the SOURCE defined must find it in the output). The kind registers its OWN list in
  `ctx.removed_styles`, checks each is absent and every staying style present (the residual skips a byte-identical styles
  part). `tests/test_remove_unused_styles.py` compares the two on seven fixtures × five parameter sets and plants shared
  defects (snapshots skipped, fields ignored, case-sensitive names, no closure, a closure seeded by the used styles alone,
  numbering or settings not scanned, the field-update table forgotten, a default `which`, a case-sensitive keep).

Built-in operations batch 5 (2026-10-17; `docs/BUILTIN_OPS_BATCH5_DESIGN.md` — each module's docstring is the full
definition):

- **The list model** (§0; `ops/lists.py` ↔ `verify/lists.py`). Numbering part = the main part's `numbering` relationship.
  num / abstractNum by whole-number id (two with one id, a non-whole id/ilvl/start/lvlRestart/startOverride → unreadable).
  A paragraph's numbering: own `w:pPr/w:numPr` numId/ilvl when present, else the first style on its chain (pStyle — an
  empty value names none —, else the default paragraph style) whose own numPr holds a numId, with that style's ilvl, else
  the **linked level** (the lowest level whose `w:pStyle` names the style), else 0; numId 0 → unnumbered; a numId no
  `w:num` defines → dangling; when the two readings of numPr inheritance — as a unit (the numId-supplying level's own
  ilvl) and element by element (the nearest ilvl along direct → chain; the link at the numId-supplying style counts as
  its own level) — give different levels → not evaluated (`ambiguous-level`; `element_ilvl` ↔ `_v_by_element`);
  `Numbering.linked` says the level came from a link (LibreOffice shows those at level 0 — flagged). Level = the abstractNum's `w:lvl` (start default 0, numFmt default decimal, lvlText, lvlRestart,
  isLgl, suff default tab, pStyle). **Counting** over the main part's paragraphs in `root.iter(w:p)` order, text boxes and
  paragraphs whose mark is a pending deletion left out, hidden paragraphs counted (Word; LibreOffice does not — flagged),
  per numId: deeper levels restart after level L (lvlRestart none → yes; 0 → never; r → when L ≤ r−1, ignored when r−1 ≥
  k; LibreOffice ignores lvlRestart — `has_restart_rule`, flagged), the level's value = its start (the num's startOverride,
  else `w:start`) or the previous + 1; **rendered number** = lvlText with `%j` → level j−1's value in its format (decimal
  when legal). Formats: decimal, decimalZero (`0`+n for 0–9), upper/lowerLetter (27 → AA), upper/lowerRoman (1–3999),
  bullet (the text), none; negative values and other formats unsupported. **Not evaluated** (never predicted through):
  unreadable, an earlier unreadable paragraph of the list (`earlier`), numStyleLink/styleLink lists, a full override
  level (`w:lvlOverride/w:lvl`, `override`), an abstractNum two nums share with counted paragraphs (`shared`: Word
  continues one count, LibreOffice keeps them apart), a `%j` naming a level with no current value (`skipped`) — and a
  level a deeper paragraph reached while it had no value stays uncertain until a restart resets it: the next paragraph
  AT it, or naming it, is `skipped` too (`skipped_below` ↔ `_v_skipped_below`; LibreOffice gives it its start then and
  continues) —, a startOverride level starting a second time (`start-override`), an unsupported format or value. `count(root, sheet,
  defs, override)` simulates a planned output; `count_reads` is what a counting op declares (rule 9). **Scheme**: 1–9
  paragraph styles numbered by ONE num at levels 0…n−1 (`resolve_scheme`), each level's **pattern** its lvlText with
  `%j` → the format's pattern. **Typed number**: on batch 1's run text, `^[ \t]*` + exactly one level's pattern +
  `[ \t\u00a0]+` or the end (`typed_number`); **visible line** = rendered number + suffix + accepted run text, whitespace
  runs collapsed, stripped (`visible_line`). **Destination check** (`list_is_clean`): no `w:lvlOverride`, not shared, not
  linked, every level supported. `tests/test_lists.py` compares the two on every paragraph of ten fixture variants and
  ~10,000 random paragraphs and plants shared defects in each rule.
- **The conversion destination** (§0.3, §1, §2; `ops/numbering_convert.py` ↔ `verify/numberingconvert.py`): the scheme
  resolved in the template (`bad-params`, fatal) or the document (`scheme`); each level's style: `keep` → the document's
  own when the reference resolves there, else the template's (imported by batch 4's import, new list ids); `replace` →
  the template's; the planned output = source styles + the import's copies, source lists + the copied list
  (`ListDefs.with_extra`); the scheme re-resolved there; the destination num must pass `list_is_clean` against the SOURCE
  count (`numbering-unsupported`); a level whose `w:lvl/w:rPr` turns on vanish/caps/smallCaps is refused
  (`level_display_problem` ↔ `_v_levels_shown`).
- **`convert_numbering`** (§1): source paragraphs = those of the main abstractNum (`main_abstract`: the most counted
  non-bullet paragraphs; tie → the first) at their ilvl, or of named styles per level (`from.styles`, numbered ones only);
  `level_map`; a paragraph at a level beyond the scheme → `scheme-depth`; an unevaluated source number converts only
  when its LEVEL is known (`shared`, `start-override`, `skipped`, `override`) and its whole num is converted (flagged:
  "could not predict before"; one still skipped after, at an unchanged level, flagged as the outline's own skip —
  `skipped_both`), else `numbering-unsupported`; a held level style numbering differently from the template's level
  (`level_pattern_sig` ↔ `_v_level_sig`) with `existing: "keep"` → `scheme` (use replace); a paragraph NOT converted that
  would show another number → `numbering-mismatch` (convert every level) — the destination list's own paragraphs are
  flagged instead. Change: `w:pStyle` = the level's style first in pPr, own numPr removed (an ilvl-only one too), cleared
  children (apply_style's clear) gone; tracked → one pPrChange, refused (`tracked-format`) when a converted paragraph's
  chain holds a style the import replaces (`replaced_in_chain`: reject-all would show two numbers). Predicted: every
  counted paragraph's counters and number over the planned output; V10 predicts with its own model, compares with the
  plan's and with its own count of the output. Flagged: linked scheme levels, a new style turning caps on.
- **`convert_typed_numbers`** (§2): candidates = counted unnumbered paragraphs of the main part (not in tables unless
  `include_tables`), selected; prefix = position 0 to the end of the whitespace after the typed number; the plan refuses
  (`numbering-mismatch`) unless the visible line of EVERY counted paragraph is unchanged over the planned output and no
  converted level's `suff` is `nothing`; refused (`typed-format`) a prefix run or mark with effective vanish/specVanish
  on or ambiguous, caps/smallCaps where the typed number holds a lower-case letter, and — the number takes the planned
  MARK's formatting (with the level's `w:lvl/w:rPr`) — a number whose planned look differs from the typed run's planned
  look (both read in the planned output, over the new style) in strike, dstrike, vertAlign, position, colour (auto ≡
  000000), size or a symbol font; bold, italic, underline or another font differing is flagged (`numbering_convert.
  look_of`/`number_look`/`number_symbolic`, `display_problem` ↔ `_v_display`). Change: the prefix deleted by the edit
  core (`tabs=True`), pStyle + own numPr off as convert_numbering. V10 checks each converted paragraph by the item model
  (`verified_blocks`) and the visible-line post-condition over every counted paragraph on the OUTPUT.
- **The edit core deletes a tab** (§2.3; `ops/textedit.py` ↔ `verify/edits.py`, keyword `tabs=True`, default off): a pure
  deletion (`text == ""`) may cover a run's `w:tab` children (never `w:ptab`, breaks or other elements); untracked the
  tab goes (V3: the plan declares `struct_after`), tracked it goes into the `w:del`. `tests/test_text_edits.py` plants a
  tab left behind, a tab outside the range taken, a ptab accepted.
- **List levels** (§3; `ops/set_list_level.py` ↔ `verify/listlevel.py`): the level = the style's own numbering
  (`style_numbering`), `level` replacing the ilvl; changes (`changes_for` ↔ `_v_changes`) against the level's own fields —
  restart **in effect** (`restart_in_effect` ↔ `_v_in_effect`: none, an r Word ignores, or `{after_level: level−1}`, is
  "after any higher level" — only the normal form is written);
  the written level (`expected_level` ↔ `_v_expected_lvl`): the source `w:lvl` with each changed element a fresh one
  holding only `w:val`, in CT_Lvl order (the verifier reads the order from the bundled schema), `w:lvlRestart` removed for
  "after any higher level". Refusals: `numbering-unsupported` (unreadable/linked list, missing or unsupported level,
  start/restart on a shared abstractNum), `numbering-override` (a full override level), `list-level` (start 0 on a letter
  or roman level, after_level not above, a change leaving an evaluated paragraph unevaluated).
- **The numbering audit** (§4; `ops/audit_numbering.py`, report only; the fix ↔ `verify/numberingaudit.py`): blank
  content (`blank_content` ↔ `_v_blank_content`: only pPr, proofErr, bookmarks, runs of rPr/lastRenderedPageBreak/`w:t` of
  spaces and NBSPs), an empty numbered paragraph = counted, evaluated, a non-empty non-bullet number, blank content
  (`empty_numbered` ↔ `_v_empties`); `unnumber_empty` writes exactly `<w:numPr><w:numId w:val="0"/></w:numPr>` and its kind
  checks only that (numPr, its place, the pPrChange rules) — the mark's rPr, a sectPr and every other child are the
  residual's, compared there with the other ops' strips. The visible sequence walk (typed numbers judged, the main
  list's automatic numbers as context; table cells skipped; keyword shapes without their dash) and the typed grammar are
  advisory (transform side only). Renumbering flags (stale REF/PAGEREF/NOTEREF fields, written references to an old
  number) are shared by the numbering ops: `ops/numbering_flags.py`.
- **The table selector** (§5.1; `ops/table_select.py` ↔ `verify/tableselect.py`): ordinal = index among `root.iter(w:tbl)`
  of the part (nested included); a table's style = tblStyle's value (empty names none), else (no tblStyle) the default
  table style; `index_range` inclusive, `style_in`, `first_row_regex` (the first own row's cells' paragraphs' block texts:
  space within a cell, tab between cells), `text_regex` (any paragraph whose nearest table is it), `nested`; rows
  `tr:<k>:<r>` and cells `tc:<k>:<r>:<c>` by own children (a row or cell in a content control is not own:
  `table-structure` when rows or cells are changed).
- **Table properties** (§5.2; `ops/set_table_props.py` ↔ `verify/tableprops.py`): style chain = the table's style and its
  basedOn ancestors; expected `w:tblBorders` = asked sides exactly (`space` default 0), a side not asked that ANY chain
  level shows visibly → `nil`, else absent; `w:tblCellMar` merged (asked sides `{w:w, w:type=dxa}`); `w:tblW` (pct ×50,
  dxa, auto 0); header rows: the first n rows `<w:tblHeader/>`, later rows `w:val="0"` where the chain's trPr turns it
  on, else none; `cant_split` likewise; "already" = own element equal (both absent counts); written in schema order
  (`ooxml.TBLPR_ORDER`, `TRPR_ORDER`, `TCPR_ORDER` ↔ the bundled schema); never tracked. `clear_cell_overrides` removes
  only the **competing** cell sides (`competing` ↔ `_v_competing_sides`: borders — every side but the diagonals
  `tl2br`/`tr2bl`; margins — the asked sides, either spelling), an emptied tcBorders/tcMar/tcPr going. **Per table**
  (`disposition` ↔ `_v_skip_or_refuse`): a table in a text box under an every-table selection, or with fewer own rows
  than the header rows asked, is left alone and flagged (named explicitly: refused); a row or cell in a content control
  when rows or cells change → `table-structure`; `start`/`end` sides and a right-to-left (`bidiVisual`) table asked for
  borders or margins → `conflict-property`.
- **Applying a table style** (§5.3; `ops/apply_table_style.py` ↔ `verify/applytablestyle.py`): apply_style's resolution
  for a TABLE style (the import only when a selected table is restyled); restyled = its style is not the asked one;
  `w:tblStyle` first in tblPr; `clear` removes the table's own tblBorders/shd/tblCellMar, its rows' `w:tblPrEx` ones and
  its cells' tcBorders/shd/tcMar, soft-reads all of them on every selected table (an already-styled one included) and
  its V10 kind checks on the OUTPUT that none is left and every selected table is in the style.
- **Sections by content** (§7; `ops/selectors.resolve_sections(…, content=True)` ↔ `verify/props._v_resolve_sections`):
  `{containing: <paragraph predicate>, with_tables, index_range}`, every given key holding; a paragraph's section by
  `paragraph_sections` ↔ `tabs._v_sections`, a table's by its first paragraph; `set_page_setup`'s assertion carries the
  user's `select` and V10 re-derives the list (an assertion without it fails). `different_first_page`: on = `w:titlePg`
  without `w:val`, off = absent; `section_props` element specs `{"present": true, "remove": [...]}` / `{"absent": true}`.
- **What the cover shows** (verifier only, `props._cover_unnumbered`): fix_page_numbering's cover-unnumbered asserts the
  footer it edited is the one the cover shows in the output (its first-page footer while titlePg is on — `w:val` not
  0/false/off — and it has one, else its default) and that it holds no PAGE field.

## Roadmap

`docs/NL_SURGICAL_EDITOR_PLAN.md` is the design and phased plan for the natural-language editor built on
this engine (compile-once EditSpec, op library, verifier extensions incl. V10, compiler, preview, Tier B).
Read it before starting any work beyond the existing ops. Phase 1 (op library foundation) is done; its
"Phase 1 status" section lists every departure from the plan.

Decisions on the plan's §14 open questions: the answers were not filled in when Phase 1 started; Phase 1
implements the plan's own proposal for #1 (plain for whole-document formatting, tracked for targeted
formatting — "targeted" = the selector has a `paragraphs` predicate; explicit `tracked` wins). **#2 answered by
Abraham before Phase 2: "both"** — yellow highlight and `{{NAME}}` placeholders are both slot markers.
#3–#5 are still open — ask before Phase 4 (#4, #5) and Phase 5 (#3).

Phase 2 (templating op + V7 template reference) is done; the plan's "Phase 2 status" section lists the
departures. Phases 3–6 (profiler/planner/catalog, compiler + preview + `do`, Tier B codegen, advisor) are done
(v0.2.0), and the window exists (`gui.py` + `ui/`, Phase 7: a Tk first cut, replaced in v0.4.0 by a Qt window with
remembered settings and in-app updates from GitHub Releases). Abraham skipped the real-file sweep, live API run and
Word check to start using the window; expect fixes from his first real runs. Abraham asked
(2026-10-13) to finish the prototype without stopping for questions: §14 #3–#5 are decided by the plan's own
proposals and recorded in the plan's status sections.

## Where things are

- `package.py` — zip/rels/sections/repack. `sections()` returns `SectionInfo` with resolved header/footer part
  names (never guessed) and `sect_pr_ordinal` to find the sectPr again. `UnsupportedFormat` (code `unsupported-format`)
  is raised by `main_part()`/`sect_prs()` for a Strict Open XML file; `batch._process_one` and `planner.plan_file`
  record an exception's own `code` when it has one.
- `ooxml.py` — transform-side helpers: `live` vs `full` text views, `split_run_at_text` (carries rPr and
  non-text children), `RevisionStamp` (Word's local-time-with-Z `w:date` + `w16du:dateUtc` when the file
  uses it), `merge_adjacent_runs` (paragraph-local), `PPR_ORDER` (complete schema order — abbreviated
  tables have bitten before).
- `package.py` (cont.) — `DocxPackage` is a source view plus a working overlay: `copy()`, `write/write_xml`,
  `remove_member` (+ its CT override), `add_member(name, data, content_type)`, `edit_rels(part, add, remove)`,
  `relationships(source=…)`, `unreferenced_media()`, `output_bytes()`. `data`/`members` always describe the
  source. Ops read parts from the working copy they are given (an earlier op may have changed them).
- `ooxml.py` (cont.) — `RPR_ORDER`, `SECTPR_ORDER`, `STYLE_ORDER`, `insert_in_order`, `set_rfonts`,
  `property_change` (rPrChange/pPrChange snapshot), `count_revisions`.
- `ops/base.py` — `Op`, `Plan` (v0.1.0 fields + `members_removed/added`, `property_assertions`,
  `geometry_reference/reason/template`, `ops`), `PlanFragment` (+ `touches`, `rev_ids_needed`), `JobException`,
  `merge_fragments` (conflicts, mode, revision-id ranges, geometry), `check_conflicts` + `verify_family` (the read
  grammar of `touches`: `verify`, `verify:<family>`, the soft `verify-props:<family>`, whole-member family reads, and —
  batch 4 — `soft_read`, `incremental` (`uses+`/`uses-`), `read_covers`, `changes_collide`; rule 9). `ops/__init__.py` —
  registry (`@register`).
- `ops/selectors.py` — transform-side selector resolution, `DEFAULT_PRESERVE_FONTS`, font param parsing; (batch 4) `chain_reads`/`table_ordinals` (what an effective value reads through the style levels) and `selection_families`/`selection_reads` (what a selection reads through every paragraph's chain; `editspec.plan_spec` adds them); an empty `scope` list is refused (`bad-params`).
- `ops/*.py` — `fix_page_numbering`, `replace_text`, `remove_header_footer`, `set_font`, `set_style_font`, `set_properties`,
  `set_doc_defaults_font`, `set_paragraph_props`, `set_page_setup`, `apply_header_footer_template`; built-in batch 1:
  `replace_table.py` (`replace_table`, `set_issue_stamp`, `_Replacing`, `find_all`, `resolve_scope`, `tracked_of`),
  `fill_variables.py`, `resolve_options.py`, `filter_tagged.py`, `end_of_section.py`, `agree.py`
  (`make_values_agree`), `naming.py` (`name_output`, `check_name`, `transform`, `OUTPUT_NAME_TOUCH`), `swap_image.py`
  (`match_rule`, `picture_of`, `picture_problem`, `shared_use`, `unused_after`, `free_media_name`); built-in batch 2:
  `comments.py` (`remove_comments`, `CommentBook`), `highlight.py` (`remove_highlight`), `revisions.py`
  (`resolve_tracked_changes`, `analyze`, `predict`, `resolve`), `hidden.py` (`remove_hidden_text`, `remove_notes`,
  `CLEANUP_PARAMS`, `keep_rules`, `FieldSpans`, `describe`), `empty_paragraphs.py` (`remove_empty_paragraphs`),
  `watermark.py` (`add_watermark`, `remove_watermark`, `WIDTHS`), `links.py` (`remove_hyperlinks`, `unlink_fields`),
  `docprops.py` (`set_document_properties`, `set_document_state`, `PROPERTIES`, `Changes`, `CustomProps`); built-in batch 3:
  `set_tab_stops.py` (`set_tab_stops`, `clean_params`, `decide`, `paragraph_sections`, `Margins`, `write_tabs`) and
  `selectors.effective_color` (the `color_in` run selector); built-in batch 4: `set_borders_shading.py`
  (`set_borders_shading`, `expected_borders`, `expected_shading`, `decide`), `clear_formatting.py`
  (`clear_direct_formatting`, `ClearSettings`, `paragraph_clearing`, `clear_paragraph` — the reuse API), `set_language.py`
  (`set_language`), `apply_style.py` (`apply_style`), `map_styles.py` (`map_styles`), `style_import.py` (the shared import:
  `resolve_style`, `TemplateStyles`, `plan_import`, `StyleImport`, `apply_import`), `remove_unused_styles.py`
  (`remove_unused_styles`, `analyze`), and `set_paragraph_props.merged_attrs`/`write_values`/`inherited_refusal` (its styles
  target); and the shared
  pieces `fields.py` (`fields_of`, `Field`, `unlink`, `NEVER_UNLINK`, `REFUSAL_TEXT`), `markup.py` (`Look`, `color_in`),
  `removal.py` (`deletable`, `plan_paragraph_deletion`, `delete_paragraph`, `plan_run_removal`, `remove_runs`).
- Built-in batch 5's ops: `ops/lists.py` (the shared list model: `ListDefs`, `paragraph_numbering`, `style_numbering`,
  `count`, `format_value`, `resolve_scheme`, `typed_number`, `visible_line`, `list_is_clean`, `has_restart_rule`,
  `count_reads`), `numbering_convert.py` (the conversion destination), `convert_numbering.py`, `convert_typed_numbers.py`,
  `set_list_level.py`, `audit_numbering.py`, `table_select.py` (the table selector), `set_table_props.py`,
  `apply_table_style.py`; `set_page_setup.py` grew `different_first_page` and `selectors.resolve_sections(…, content=True)`
  / `section_reads`. Their V10 kinds: `verify/lists.py` (the model's twin), `verify/numberingconvert.py`
  (`converted_numbering`), `verify/typednumbers.py` (`typed_numbers`), `verify/listlevel.py` (`list_level`),
  `verify/numberingaudit.py` (`unnumbered_empty`), `verify/tableselect.py`, `verify/tableprops.py` (`table_props`),
  `verify/applytablestyle.py` (`applied_table_style`), and in `verify/props.py` the content form of `section_props`
  (`_v_resolve_sections`, `_v_page_setup`) and `cover_unnumbered`. Fixtures: `build_numbering`, `build_typed`,
  `build_scheme_example` (= `examples/templates/NUMBERING SCHEME.docx`, `--numbering-scheme`), `build_tables`,
  `build_schedule`. The profile's `tables` and `lists` summaries (`profile.table_summary`/`list_summary`) show Claude a
  pattern's tables, lists, numbered styles (a scheme's levels are named from them) and typed numbers.
- `ops/textedit.py` — the in-paragraph edit core (batch 1): `RunText`, `Edit`, `check_edits`, `predict`,
  `apply_untracked`/`apply_tracked`, `plan_paragraph` (BlockDiff or accepted view, rev delta, touch),
  `apply_part_edits`, `edits_to_data`/`edits_from_data`. `ops/tables.py` — table files (`xlsx_rows`, `csv_rows`,
  `xlsx_sheet_names`, `load`, `check_unchanged`, `variables`, `pairs`, `Bad`). `ops/images.py` — picture files
  (`sniff`, `load_image`, `fitted`, `check_unchanged`, `CONTENT_TYPES`).
- `ops/hf_common.py` — header/footer refs, `effective_refs` (what each section shows), `plan_removal`/
  `apply_removal` (shared by `remove_header_footer` and templating: rels, members, overrides, orphaned media,
  revisions flagged). `ops/slots.py` — slots, display text, `apply_slot`, `expected_block_text`, `extract_fill`;
  reference slots (`located_text`, `bounded`, `reference_values`, `reference_slots`), `carve`, `mark_slots` (⟦ ⟧ for notes).
  `ops/effective.py` — `Resolver` (effective pinnable properties), `theme_fonts` (+ `theme_part`, the part it reads —
  what the templating op declares as a read), `text_width`, `rescale_tabs`;
  `PropResolver` (set_properties' effective value with provenance, with an `override` that simulates a style change).
- `apply_header_footer_template` plan: copies keyed by (template part, template width, target width); pins are
  the diff of template-effective vs target-effective (with imported styles); `transform_data` carries pins,
  slots, rels/media maps, docPr start ids, new refs, removal plan. Apply works on a fresh parse of the template.
- `chain.py` (2026-10-16) — chains: several specs run one after another, each stage a full `run_batch` in
  `<out>/stage-NN/` on the files the stage before it verified (`run_chain`, `ChainResult`/`ChainFile`/`StageRun`,
  `validate_chain`, `chain_from_specs`, `chain_templates`, `chain_key` — the approval key —, `chain_intent`,
  `load_chain_file`); the reports in `report.write_chain_report`/`write_chain_diagnostics`/
  `build_chain_set_aside_manifest`. See "2026-10-16 Chains" below.
- `editspec.py` — EditSpec validate → `plan_spec` → `apply_spec` (minimal Phase 1 executor; the Phase 3
  planner grows out of it). `examples/specs/` — the worked example specs (Phase 1's two, the templating ones,
  `match-template-spec-header-footer` and `match-template-spec-font`); `examples/templates/` — the template fixture
  and `08 71 00 - DOOR HARDWARE (ACME).docx` (an architect's spec; `make_fixtures.py OUT --reference`).
- `jobs/` — `OpJob` aliases (`page-numbering` → `fix_page_numbering`, `tracked-replace` → `replace_text`).
- `verify/suite.py` — V1…V10 as a `VerificationRecord`; V6/V7/V9 are filled by `batch.py`
  (`geometry_check()` implements V7's `source | none | template` modes). V5a takes `template_baselines` for
  added parts. Every sort of values that can be None (V2's `(part, before, after)` — an inserted block has no
  `before`, a removed one no `after` —, V4's duplicate ids, V5b's new ids — a revision without `w:id`) uses
  `_total_key` (None, then text, then tuples): it decides the order only, the lists compared are the same as ever.
- `verify/reader.py` — `REL_CONTENT_TYPES`: V1's content-type-vs-relationship-type table.
- `verify/template.py` — V10 kinds `hf_template_refs`, `template_parts`, `styles_added` (registered into
  `props.KINDS`); template packages cached by content md5 (re-read every time, so an edited template shows).
- `verify/geometry.py` — `hf_only_copy()` writes a headers/footers-only copy of the template AND of each
  output (body reduced to its section breaks, three pages each, sections nextPage, numbering restarted at 1);
  `compare_template_bands()` pairs their pages by section (map from the spec's `template_sections`) and
  variant — never a "best page" search (a blank variant would stand in for anything) — and checks each
  templated region: blank ↔ blank, else outer edge + shared words, 2 pt window, footer from the page bottom.
- `verify/props.py` — V10: assertion kinds (the `properties` kind of `set_properties` included, with its own effective-value
  resolver `_v_run_prop`/`_v_para_prop`), independent selectors/styles/theme, residual identity. `first_difference`
  names the paragraph (its text) or the style (its id) a difference lies in.
- `verify/edits.py` (the item model), `verify/tables.py` (table readers), `verify/textops.py` (`TEXT_OP_KINDS`:
  `replaced_text`, `filled_variables`, `resolved_options`, `tagged_paragraphs`, `end_of_section`, `output_name`,
  `agreed_values`), `verify/images.py` (`IMAGE_KINDS`: `image_swaps`) — built-in batch 1's V10 kinds, registered into
  `props.KINDS` at the end of `props.py`. `props.Ctx` carries `source_name`/`output_name` (the names the suite was given
  — `output_name` checks the written name), `verified_blocks` and `masked_extents`. Built-in batch 2's kinds:
  `verify/comments.py` (`COMMENT_KINDS`: `removed_comments`), `verify/highlight.py` (`removed_highlight`),
  `verify/revisions.py` (`REVISION_KINDS`: `resolved_changes`), `verify/removal.py` (`REMOVAL_KINDS`:
  `removed_hidden_text`, `removed_notes`, `removed_empty_paragraphs`), `verify/watermark.py` (`added_watermark`,
  `removed_watermark`), `verify/links.py` (`removed_hyperlinks`, `unlinked_fields`), `verify/docprops.py`
  (`document_properties`, `document_state`), over the shared `verify/fields.py` and `verify/markup.py`; batch 3's
  `verify/tabs.py` (`TAB_KINDS`: `tab_stops`); batch 4's `verify/borders.py` (`borders_shading`), `verify/clearing.py`
  (`cleared_formatting` + the reuse API), `verify/language.py` (`language`), `verify/styleimport.py` (`styles_imported`),
  `verify/restyle.py` (`mapped_styles`, `v_resolve_style`), `verify/applystyle.py` (`applied_style`), `verify/unused.py`
  (`removed_styles`), and `paragraph_props`' styles branch in `props.py`; `Ctx` gained
  `verified_parts`, `expected_removed`, `verified_members`, `member_strip` (see "V10's batch-2 hooks") and, batch 4,
  `replaced_styles`, `removed_styles`, `strip_tbl_style`.
- `verify/explain.py` — why a check failed, in specifics, from the check's own data and the record's predictions
  (`failure_details`, `text_change` word diffs with ⇥ ⍽ ⏎ made visible); `VerificationRecord.failures()` and the
  record's "WHY IT FAILED" section use it. It is verifier code: the independence rule applies to it too.
- `batch.py` — the pipeline; `_process_one` is the per-file worker (must stay top-level and picklable); it
  runs a named job or an EditSpec (`spec=`). Batch 1: `planned_names(files, spec)` runs the naming op's plan per file
  before the workers start (collisions → `name-collision` refusals handed to `_process_one(…, refuse=)`),
  `output_names(files, fixed)` keeps those names, `run_batch` refuses `in_place` for a spec that renames
  (`editspec.spec_renames_outputs`) before anything is written, and the worker checks `plan.output_name` is the name
  it writes under; a rename-only file is delivered (not a no-op). `outputs/` holds only files that passed every check, V9 included: an output
  that fails verification is moved to `failed/` (`quarantine_failed_output`) before delivery, and one whose V9 guard
  fails afterwards (drift during the run, a refused in-place write-back) is moved the same way; V9 records where it went
  and `delivered_to` stays empty. The worker verifies in memory and writes the output only after the suite ran (a
  verifier that raises leaves nothing in `outputs/`). `output_names` gives every input an output name unique across the
  batch, case-insensitively, never a name another input really has (records/plans/exceptions share the stem:
  `FileResult.record_stem`); `check_inputs_outside` refuses an input under the results folder before anything is
  created; sources that share a name are rendered from copies under their unique output name (`render/source-in/`).
- `render/` — `word_com.py` (DispatchEx only; copies only; Quit the instance you made; `available()` needs Word
  registered in `HKEY_CLASSES_ROOT\Word.Application`, not just pywin32; `com_error_text` for COM errors; logs Word's
  version), `libreoffice.py` (`failure_text`: exit status + the end of stderr/stdout; `version()` read from the files
  beside soffice, never by running it). `render_pdfs(..., log, detail)`: `detail` is the run log only. `render.render_pdfs` never
  raises: a renderer that cannot run or a file it cannot convert is a `log` line and a missing PDF (V6 "render failed"
  for that file; a warning in the preview), never a crashed batch; LibreOffice retries a failed chunk one file at a
  time.
- `diagnostics.py` — `classify`/`exception_record` (kind, code, stage, op, `where` = the line of code via `raise_site`,
  traceback for internal/environment/unreadable), `sniff` (what a file that will not open is), `malformed_parts`,
  `os_error_hint` (Windows codes first), `Tracer` (per-file trace, flushed per line; `watch(crash=True)` only in a
  pool worker), `RunLog` (run.log; a call is a progress line too, `detail`/`block` file only), `environment`,
  `setup_app_logging`, `CrashWatch` + `com_quiet` (the window), `pid_alive` (never `os.kill` on Windows — it would
  terminate the process; `winapp.process_alive` asks the kernel), `make_bundle` (`also=`: more results folders under
  `LATER_PREFIX` `results-later/<folder name>/`, `-2`, `-3` for repeated names; `_within` skips a folder already
  bundled or inside one; `environment.json` and the result carry `also`). `report.write_diagnostics` /
  `file_diagnosis` / `exception_diagnosis` build DIAGNOSTICS.md and the window's per-file diagnosis.
- `guard.py` — identity, drift (size, mtime, then md5), lock file, read-only refusal (`is_read_only`: the file's mode, never `os.access`, so
  root/administrator see the same answer), atomic write; every reason a write-back cannot happen is a `GuardError`
  (a refused `os.replace` included), which `batch.py` records as that file's V9 failure — never a crashed batch.
- `tests/qt_support.py` — `qt_app_or_skip()` (offscreen Qt, or skip unless `SPEC_SURGEON_REQUIRE_QT`) and
  `close_windows(app, windows)` — the ONLY way a test lets go of a window (see the Qt rules under `ui/`, and
  `docs/audits/2026-10-16_qt_teardown_segfault.md`); `flush_deferred_deletes(app)` delivers pending `deleteLater()`s,
  which `processEvents()` alone never does outside an event loop.
- `tests/make_fixtures.py` — synthetic specs with the hard cases; `build_rich()` is the op fixture (first/even
  headers, shared footer part, inheriting section, header-only and shared media, theme/explicit/symbol fonts,
  runs inside revisions, an earlier rPrChange). `build_template()` is the template fixture (both slot
  conventions, fragmented placeholders, a style targets lack, a logo, a footer table, 1" margins) and
  `TEMPLATE_FILLS` its fill rules; `examples/templates/00_00_00_TEMPLATE.docx` is its output (regenerate with
  `--template` when it changes). `build_reference_spec()` is a template spec (an architect's, Arial 10; options for a
  title in another case, by STYLEREF field, an extra header line) and `REFERENCE_FILLS` fit it and `build()`;
  `build_sized()` is the font-size fixture (9 pt text, 8 pt headers, a 12 pt note, sized symbol runs);
  `build(body_footer_linked=True)` is a body section that inherits the cover's footer, `build(appendix_restart=True)` a
  third section that restarts page numbering. Add a case here when an op grows.
- `editspec.py` — `load_spec` resolves a relative `template` against the spec's folder; `is_template_file`.
  `validate_spec` runs `_check_shapes` right after the unknown-key check: every spec-level field has the published
  schema's shape (`request`/`summary` text, `unsupported`/`proposal` text or null, `assumptions`/`questions` lists of
  strings, `review`/`applies_to` objects, `assertions` a list), then `mode` a string in `MODES`, an op's `op` a string,
  `review.sample_files` a positive int (not a bool), `applies_to.pattern_ids` a list of strings, `applies_to.or` only
  `"all"` — all `SpecError` code `spec`, never a `TypeError` or a misreading. `effective_font` sub-fields are not
  shape-checked (a wrong type there is V10's "could not evaluate").
- `compiler.py` — the tools, the SYSTEM prompt (`system_prompt(chat)`; `CHAT_NOTE` adds the chat rules), `TOOLS`
  (one-shot) and `CHAT_TOOLS` (+ `view_pages`), `_Batch` (the read-only tools, plus `show_xml`/`find_paragraphs` for
  the transform writer and `view_pages` for the chat), the submission checks: `validate_spec`, `untraceable()` over
  `placed_words()` (add any new op that places text there), dry plan per pattern (spec-level codes only). Files are
  shown and addressed by **label** (`file_labels(files)`, keyed by `str(path)`: the name when no other input has it,
  else the fewest trailing folders that tell same-named files apart, joined with "/" — `a/Spec.docx`; the same path
  twice is one file). `_Batch.labels` (path → label), `_Batch.label(path)`, `_Batch.files` (label → path);
  `_relabel` renames files in the profiles (a pattern's `files` are labels, its `paths` are paths; the
  representative's `file`, `unprofiled`, an error profile's `files`, `unreadable`), and `_dry_errors` names files by
  label. Never key anything by `Path.name` again — two folders' `Spec.docx` would shadow each other.
  `compile_request` is a wrapper over `Conversation(mode="compile")` (Phase 8). `CompileResult` carries `transcript`
  and `seconds` (diagnostics) beside `usage`/`served_models`/`custom_transform`; `is_api_error`, `api_error_text` and
  `block_dict` are importable here (re-exported from `llm`). `preview.py` — `make_preview`, `approval_token`,
  `approval_inputs` + `explain_approval_change` (what changed since the preview), `render_samples`,
  `preview_generated` (a custom transform's preview), `write_preview_files`; both previews write `logs/preview.log`.
  `generated_key(g)` (`{generated_source, select, params}` — the same key as `gui.Session._generated_key`) and
  `changed_since_preview(key, files, token, before)` (None when unchanged, else `explain_approval_change` lines; a
  missing file is a change, never an exception) are what `cli.cmd_do`/`_do_codegen` re-check after y/N or `--yes`.
  `make_preview` names sample outputs with `batch.output_names(picks)` (`Spec.docx`, `Spec (1).docx`).
  `sample_labels(samples)` (`compiler.file_labels` over the samples) names them in `Preview.to_text`/
  `GeneratedPreview.to_text` (the note Claude gets) and in `render_samples`' warnings — the labels the visual check
  uses. `explain_approval_change(before, now, since=)` names the moment `before` was taken ("the preview" by default;
  "the custom transform was started" for the files' stats taken before a transform is written).
  Default model `claude-opus-5-5` (`SPEC_SURGEON_MODEL`); the live API path is untested in the container.
- `llm.py` — the one Claude call site (rule 18): `call`, `history_content`, `tool_uses`, `texts`, `served`,
  `stop_problem`, `Usage`, `PRICES`, `client()` (`ClaudeUnavailable` without the SDK); keyless mode's one decision
  `credentials_found()` (+ `_sdk_credentials`) and `CLAUDE_FEATURES` (the list every "needs a key" text shows);
  diagnostics: `is_api_error`, `api_error_text`, `failure_text`, and the per-call records `response_entry` (stop reason,
  serving model, usage, seconds, request id, content via `block_dict`), `results_entry`, `error_entry`,
  `transcript_content` (total, no image data).
- `conversation.py` — `Conversation` (start/reply/choice/note/brief/save/compile_result), `Turn`, `Choice`,
  `check_pairing`, `clean_wording` (rules 13–14). Since 2026-10-16: `LIBRARY_CHOICE_KINDS`, `LibraryChanged`,
  `Choice.words/name/item_hash/snapshot`, `_library_choice`, `take_library_choice`, `_dropped` (see "2026-10-16 Claude
  and your library" below). At most 12 calls per turn; two rejections in one user turn end
  it as `failed`; asks take precedence over a submission in the same assistant turn. `conversation.json` is a record,
  never replayed: `entries` is what the user saw, `exchange` the model exchange of every call (never the API
  `messages`), which `compile_result` hands to `CompileResult.transcript`. `ACCEPTED_RESULT` is the accepted spec's
  tool result. `to_dict` also writes `approved_words` (`sources`, in order: request, `--answer` lines,
  `memory_source` — PROJECT.json without `_path` as JSON text, only when it holds something —, then each picked
  wording and typed reply; never an empty source) and `answers`. `invisible`/`INVISIBLE_RANGES`/`INVISIBLE_CATEGORIES` back `clean_wording`; `_texts` reads a list of
  texts from Claude (one string = one item; else dropped and said). Only a terminal conversation (`cli._chat`) and
  the window (`Session._save_chat`) write `conversation.json`; `compile_request` (a scripted `do`) saves none — the
  CLI writes `compile.json` after it returns.
- `vision.py` — `page_pngs`/`rasterize_pages` (≤ 2000 px long edge), `est_tokens`, `image_block`, `render_pdf` (md5
  cache), `review_samples` + `VisualReview` (+ `transcript`, without the images) + `save_review` (rule 15; its guards:
  `_read_answer`, `_finding` — a page is an int, not a bool —, `_skip_for`, `_quiet`, `_describe`, `_traceback`;
  page labels and the findings' `file` are `compiler.file_labels` labels over all the samples);
  `VIEW_BUDGET_TOKENS` for `view_pages` (1,000,000 by default, configurable per conversation: `page_tokens()` is
  2,714 per letter page at 150 dpi, 368 one at a time or 366 in calls of three; 301 pages of 11×17 at 117 dpi,
  3,312 each; `page_dpi` is the one dpi rule
  `rasterize_pages` uses; a test derives the comment's figures).
- `library.py` — recipes (`recipes/<slug>.json` + `.meta.json`), chains (`chains/<slug>.json` + `.meta.json`, the
  stages' specs embedded as saved; `save_chain`/`load_chain`/`chain_file`, 2026-10-16), operations
  (`operations/<slug>/op.py` + `manifest.json`) and snippets (`snippets/<slug>.json` + `.meta.json`; batch 1:
  `save_snippet`, `snippet_from_document`, `read_snippet`, `load_snippet`, `snippet_spec`, `snippet_text_problem`) under
  `config_dir()/library` (rule 17). `FILE_KINDS` are the file-backed kinds (recipe, chain, snippet); `LABELS` the
  labels; `_portable_spec` makes a spec portable (every input-file path absolute — `editspec.input_slots` —, pattern
  pins dropped) for a recipe and every chain stage. `other_kind_holding(kind, slug)`; `_check_free` refuses the
  other kind's name even with `replace` ("… cannot take that name, not even with replace …"). `item_hash(item)`: the
  content hash a conversation's library choice pins; `recipe_file(item)` / `read_manifest(item)`: the `_inside`-checked
  reads the compiler's index and `read_library_item` use (2026-10-16).
- `codegen.py` + `sandbox_runner.py` — custom transforms (Tier B; offered in the conversation, or `do
  --allow-codegen` without a terminal): AST whitelist (`ALLOWED_FROM`, `ALLOWED_IMPORT`, `FORBIDDEN_*`,
  `EXCLUDED_FROM_EXAMPLES`), isolated subprocess on copies, `hard_limits` (rule 16), the full suite as the test, 3
  attempts / 24 calls, the brief (the whole conversation) and read tools (`CODEGEN_TOOLS`), `pick_samples`, outputs
  only (`run_generated` keeps `check_inputs_outside`, `output_names`, the md5 re-check). `codegen/history.json` after
  every attempt (`_save_history`, after the result is set — the exchange included); `SandboxResult.traceback`/
  `stderr`/`returncode`, `sandbox-stderr.txt` in the trial's workdir; an API error ends the generation as a recorded
  failure, a defect propagates. Never let generated code import `verify/`, write files, or run in place; never relax a
  hard limit to make a generated op pass.
- `advisor.py` — exception record → recommendation or re-compiled spec (pinned to the file's pattern and to the
  original spec's `request` — `_respec_errors(…, request, original_ops)`; may not add an op of
  `catalog.REQUEST_ONLY_OPS` the original spec lacks — checked first —; must pass `compiler.check_submission` and plan
  on that file). Writes `advice/`; never applies anything. `ADVISOR_TOOLS` is an
  explicit list (a test pins it). Its exchange with Claude is kept (`Advice.transcript` → `advice/<name>.transcript.json`,
  built with `llm.response_entry`/`results_entry`/`error_entry`); an API error is a recorded `failed` advice, a defect
  propagates (`advise_batch` still isolates every record: one file's failure never stops the others).
- `about.py` — who made it, the links (GitHub, LinkedIn, email), the license text and its plain-words summary, the
  third-party licenses: the single source for the About box (`ui/about_dialog.py`), the installer (`installer.iss`:
  `AppCopyright`, `AppContact`, `LicenseFile`, the welcome-page links), `pyproject.toml` and `updater.REPO`.
  `tests/test_about.py` pins them to each other and to the `LICENSE` file (MIT, © 2026 Abraham Borg). No Qt.
- `gui.py` — the window's logic and entry point (`spec-surgeon-gui` / `spec-surgeon gui`). `Session` holds all logic
  (files, the conversation — `compile`/`reply`/`end_conversation`/`conversation_matches` —, preview with the visual
  check, run with the approval-token and review gates, custom transforms (`_codegen`), the library
  (`load_recipe`/`use_operation`/`save_recipe`/`save_operation`), set-aside, advise; `last_batch`/`last_trials`
  keep the results for the tables) and is what most tests drive. `_set_outcome` is the ONE place the outcome (a spec
  XOR a custom transform) changes; it voids every preview, token and acknowledgement of the old outcome (and
  `generated_inputs`). The session folder: `compile()` opens it (`out_dir = chat_dir = _compiled_dir`); `_save_chat`
  writes `conversation.json` + `compile.json` (`last_compile`, via `diagnostics.write_json`) after every turn;
  `_session_dir()` gives the first preview or custom transform that folder and later ones a new folder the records
  follow; `reply()` opens the next folder first once a preview used the current one (`_next_session_dir`), so a
  used folder's records are frozen. Custom transforms (Phase 8 review fixes): `TransformFailed` (`PREFIX` "No custom
  transform passed verification: ", `.details`) is what `_codegen` raises when no attempt passed or a call failed, and
  the ONLY exception `reply` notes to Claude as an outcome — any other is a defect: the queued "the outcome follows"
  note (`take_app_choice`) is withdrawn and replaced by "… The app could not finish the custom transform: it stopped
  with an internal error (<Type>) before any result — nothing was applied." (the type, never the text; second pass),
  `_save_chat()`, then propagate. `_codegen` takes `approval_inputs({}, files)` BEFORE `generate_op`; a file whose
  stats moved by the time the token is taken leaves the token empty and the inputs at the old stats (Run names what
  moved; the window's reply previews, so `_prove_again` tries it at once). `compile` and `use_operation` take their
  batch with `files(outcome=False)` (never through the outgoing spec's templates), before anything changes.
  `_prove(g, files, samples, folder, refusal)` takes the token and inputs BEFORE the trials and raises naming every
  failing sample; `_show_generated` sets the outcome, preview and visual check, and the token LAST (a preview that
  stops leaves nothing runnable); `_prove_again` (from `make_preview` when the token is stale) re-proves on the
  current samples in `_next_session_dir()/retrial/NN`, then `_use_folder` + `_show_generated`, or raises "the custom
  transform 'NAME' does not pass on the files as they are now — …" and changes nothing; `generated_intent` keeps the
  visual-check intent for it. `_preview_again` (from `make_preview` when the token is current but the preview was made with
  *Render pages* set the other way — `GeneratedPreview.rendered`) makes the preview again from the same trials, in a new
  folder, with pages and the visual check when rendering (Codex on #12). `use_operation` proves first in `_next_session_dir()/library-trial/NN` and only then
  `end_conversation`/`_use_folder`/`_show_generated`. `_use_folder(out)` (split from `_session_dir`) moves `out_dir`
  and the conversation's records. `conversation_matches` runs the conversation's files through the same template
  filter as `files()` (the attached template + `spec_templates(self.spec)`, `is_template_file`) and is False on
  `ValueError`/`OSError`. No Qt here: `main()`/`build_window()` import
  `ui/` lazily and explain `pip install "spec-surgeon[gui]"` when PySide6 is missing (exit 2).
- `ui/` — the Qt window (PySide6-Essentials ≥ 6.8, v0.4.0; reworked 2026-10-15). `app.py`: `MainWindow` (all wiring),
  `build_window` (test hook `window._spec_surgeon`: buttons incl. `next`/`about` and `send`/`start_over`/`review_ack`/
  `library`/`save_to_library`, `Var` values, `request`/`reply`/`chat`/`preview_chat`/`choices`, `claude`, `wait()`;
  never
  `answers` or `vars.codegen` — those controls are gone), `Prompts`/`RecordingPrompts` (every dialog goes through
  `prompts` — a modal dialog hangs an offscreen test), `qt_app`, `main`. `pages.py` the four steps (layout only: header
  with "STEP n OF 4", cards, an action bar with the primary button and a hint label — `next_hint`, `preview_hint`,
  `continue_hint`, `run_hint`; `empty` states), `widgets.py` (`Card`, `PathPicker`, `DropZone`, `EmptyState`,
  `StatTile`, `InfoBar`, `BadgeDelegate`, `StepDelegate` — paints the rail from `STEP_STATE_ROLE`/`STEP_STATUS_ROLE`,
  `pill`, `link_label`, `TextDialog`), `models.py`, `rows.py` (tables + `ProgressTracker`; **no Qt**, it parses
  `run_batch`'s `"N file(s); transform+verify"` / `"  done NAME"` lines — `test_ui_rows.py` pins them), `theme.py`
  (Fusion + own palette/QSS; follows Windows light/dark, `SPEC_SURGEON_THEME` forces one, `SPEC_SURGEON_QT_STYLE` forces
  a style for diagnosis), `worker.py`, `update_ui.py`, `settings_dialog.py` (results folder + API key, nothing else),
  `about_dialog.py` (rich-text body from `about.py`), `chat.py` (`ChatView`: the conversation card — two instances, on
  Describe right under the request and under the preview; choice buttons are rebuilt per turn and never in
  `job_buttons`; Claude's text shown as plain text only; bubbles are added AND shown at once (`_add_now`) so
  `MainWindow._show_chat_reply` can scroll the Describe step to the choices and Send; a choice's `detail` tooltip is
  `html.escape`d — Claude's words are never rendered as rich text), `resources/icon.svg` (source of
  `packaging/windows/SpecSurgeon.ico` via `make_icon.py`). In `app.py` (Phase 8 review fixes): `_widget_key(keep_spec)`
  ends with the *Render pages* state (`keep_spec=False` in `do_library`'s operation branch: the key as it reads once
  `use_operation` replaced the loaded spec); `_refresh_spec_bar` hides the "Saved spec" bar whenever the outcome is not
  that loaded spec; `do_preview` with an empty request box and a custom transform as the outcome previews it again
  ("Using the custom transform NAME.") instead of compiling; `_reply` ends the chat with the info bar "The conversation
  ended" when `sync()` ended the conversation (folder changed) and starts no job; `_clear_set_aside` refreshes the
  chat; `_refresh_run_state` enables Save to library… and I've looked — allow Run only while the preview is current
  and nothing is busy or updating; `_bundle_folders` → `save_bundle` (see Diagnostics rule 8).
  **Qt rules:** PySide6 is imported at module level only inside `ui/` (never `ui/__init__.py` or `ui/rows.py`) —
  the CLI, `Session` and pool workers must load without Qt (`test_gui.py` checks both ways). Background work runs
  on a daemon thread (`worker.start_job`); its signals connect only to `@Slot` methods of GUI-thread QObjects, never
  to lambdas; read every widget value before the job starts; never touch a widget from the job. **Every QObject the
  window makes has a Qt parent in the window** (or goes to Qt through a layout or `deleteLater()`), so Qt deletes it with
  the window, on the GUI thread; never leave a Qt object to the garbage collector — it runs on whatever thread happens
  to allocate, and PySide6 6.12 deletes a Python subclass of a Qt class right there and defers a plain widget's deletion
  to a pointer its parent already freed (the 2026-10-16 segmentation fault, below). **Tests let go of a window only
  through `tests/qt_support.close_windows`** (close, `deleteLater`, the deferred deletes flushed, `gc.collect()` — on
  the main thread, while the test still holds every wrapper); `close()` alone, or `deleteLater()` + `processEvents()`
  (which deletes nothing outside an event loop), is the crash coming back. `tests/test_qt_lifetime.py` checks every
  test module that builds a window.
- `updater.py` — GitHub release check / verified download / detached Setup launch (no Qt; every network and
  process call injectable). `settings.py` — `settings.json` (+ `config_dir`/`data_dir`/`updates_dir`/`logs_dir`;
  `SPEC_SURGEON_CONFIG_DIR` moves them). `winapp.py` — app mutex, AppUserModelID, message box (ctypes; no-ops off
  Windows). `credentials.py` — the API key in Windows Credential Manager (`win32cred`).
- `inputs.py` — the tables and pictures beside the documents (batch 1): `near` (the folders of the batch and the
  template, non-recursive, ≤ 30), `overview`/`listing` (the first message's INPUT FILES), `read` (`read_input_file`:
  only a listed file).
- `profile.py` — bounded per-pattern profiles (descriptive only; no op reads them), incl. `fonts.body_text` and (built-in
  batch 2) `markup` (`profile.markup`: comments by author and resolved, tracked changes by author/kind and their dates,
  highlight colours, hidden runs, run colours, hyperlinks, field types, watermark, document properties, marked final,
  tracking locked — thinned by the bound like the rest); `template_spec_summary()` is `read_template`'s `template_spec`. `planner.py` — `plan_batch` dry run +
  `summarize_plan`. `catalog.py` — `op_catalog()` (+ `COMMON_ASSUMPTIONS` per op — add one for every new op;
  `REFERENCE_FILLS`, the CSI number/title rules for template specs) and `editspec_json_schema()`.

## Adding or changing an op (or a job)

Follow README → "Adding an op" (new work goes into `ops/`; jobs are aliases). Every new grammar needs a
planted-defect test proving the verifier catches a wrong implementation of it — ideally a wrong output AND a
wrong implementation shared by plan and apply. Run `py -3 -m pytest -q -s` before and after (72 tests at the
end of Phase 1, 116 at the end of Phase 2, 125 after Phase 3, 135 after Phase 4, 153 after Phase 5, 158 after Phase 6, 166 with the window, 178 with v0.3.0, 189 with the v0.3.1 audit fixes, 252 with v0.4.0, 316 with template specs and font sizes, 324 with the 2026-10-15 sweep fixes, 344 with the fixes from the external audit of v0.4.0, 356 with the 2026-10-15 GUI rework, license and About, 395 with diagnostics, 410 with
Phase 8 (the conversation, visual check, library: 66 tests on top of 344, on its own branch), 481 with the two
merged and the merge's own tests, 685 with the Phase 8 review fixes (`tests/test_phase8_fixes*.py`), 709 with Claude and your library
(`tests/test_library_choices.py`, 2026-10-16), 734 with `set_properties` (`tests/test_set_properties.py`), 740 with the Qt
teardown fix (`tests/test_qt_lifetime.py`, 2026-10-16), 756 with running without an API key
(`tests/test_keyless.py`, 2026-10-16), 768 with the plan-time conflicts for what an op reads
(`tests/test_read_conflicts.py` and two tests in `tests/test_ops_base.py`, 2026-10-16), 784 with chains
(`tests/test_chain.py`, 2026-10-16), 963 with built-in operations batch 1 (2026-10-16), 1556 with master's bounded
recovery and built-in operations batch 2 (2026-10-16), 1600 with built-in operations batch 3
(`tests/test_tab_stops.py`, `tests/test_color_selector.py`, 2026-10-16), 1938 with built-in operations batch 4 and its review round (2026-10-17), 2429 with built-in operations batch 5 and its
review rounds, 2838 with batch 5 merged with master's adaptive workspaces and correlated diagnostics (2026-10-17) —
all pass but four that skip in the Linux container: the
Windows-only Credential Manager round trip and three `tests/test_llm.py` tests that need the `anthropic` package; in the
container `python3 -m pytest -q -s`).

## Updater rules (v0.4.0)

1. **Never start anything that has not passed every check:** the asset name equals
   `SpecSurgeon-<release version>-Setup-x64.exe` exactly; `SHA256SUMS` is fetched first and lists that exact name
   (no installer byte without a known hash); GitHub's asset `digest`, when present, agrees; the size matches; and
   (once releases are signed) `verify_signature` passes. Re-hash immediately before `Popen`. Never relax a check to
   make an update go through — a failing check is reported, the download deleted.
2. **Only an installed build installs** (`is_installed_build`: frozen, Windows, `unins000.exe` beside the exe); pip
   copies get instructions. Never while a job runs or another instance holds the app mutex.
3. **Names shared with the installer** (`installer.iss`) are pinned by `tests/test_packaging.py`: `INSTALLER_NAME` ↔
   `OutputBaseFilename`, `winapp.APP_MUTEX` ↔ `AppMutexName`, `/RELAUNCH=1` ↔ `ShouldRelaunch`, `winapp.AUMID` ↔
   the shortcuts' `AppUserModelID`, `contents_directory="_internal"` ↔ `[InstallDelete]`. Change both or neither.
4. **Setup's environment** (`installer_env`) never carries the API key or the frozen runtime's `_PYI_*`/`_MEI*`
   variables — the reopened app must start as a fresh top-level process.
5. **Tests never touch the network or start a process**: scripted openers, recording popen, fake
   checker/downloader/launcher. The real chain is proven on Windows by `validate_installer.ps1` (update mode).
6. The API key never goes into `settings.json`; the write-back switch, the conversation and a visual-check
   acknowledgement are never persisted (the AI-written-transform switch is gone since Phase 8).
7. **The daily check has no setting.** `updater.should_auto_check(settings)` is once a day for every copy (installed or
   pip); `SPEC_SURGEON_NO_UPDATE_CHECK` is the only off switch (tests and screenshots set it). Do not add a switch
   back — Abraham asked (2026-10-15) that the user never has to think about it.

## Documentation duty

Any implementation change: update README.md (don't shorten it — add). Dependency change: update
`requirements.txt` and `pyproject.toml` together. Change to the rules above or to the block-text definition:
update this file. Name, links, email, license: change `about.py` only (and `LICENSE` with it) — `tests/test_about.py`
tells you every other place that must follow.

## Environment notes

- Developed in a Linux container with LibreOffice, pandoc, poppler; Word path untested (marked [unproven]).
  The container has no `py` launcher: use `python3`. `pip install -e ".[dev,gui]"` first (pytest is not
  preinstalled). Qt needs `apt-get install -y libegl1 libopengl0` in the container; run the suite with
  `QT_QPA_PLATFORM=offscreen` (no Xvfb needed). Qt tests skip when Qt can't load unless `SPEC_SURGEON_REQUIRE_QT=1`
  (CI sets it — a broken Qt install must fail, not skip). Every Qt fixture tears its windows down with
  `tests/qt_support.close_windows` (close, the deferred deletes delivered, `gc.collect()` — all on the GUI thread): a
  window left for a later garbage collection was destroyed on a batch's pool thread in an unrelated test
  (`QObject::killTimer: Timers cannot be stopped from another thread`) and segfaulted the whole run, at a spot that
  moved with allocation timing (2026-10-16: a new test module made it land in `test_editspec`'s perf test every time).
  Never go back to a bare `w.close()` in a fixture. `python3 packaging/gui_screenshot.py` regenerates
  `docs/gui-files.png` / `docs/gui.png` / `docs/gui-results.png` (`--chat`: `docs/gui-chat.png`, scripted client;
  `SPEC_SURGEON_THEME=dark` + `SPEC_SURGEON_SCREENSHOT_SUFFIX=-dark` for the dark look) — run it with `PYTHONPATH=src`
  in a worktree (the editable install points at the main checkout); look at screenshots before changing layout. A grab
  taken right after a job can show widgets the job added still unlaid-out (`settle()` first).
- Target platform is Windows 10/11; paths with en-dashes and doubled spaces are normal; keep `pathlib`.
- `ProcessPoolExecutor` on Windows requires the `__main__` guard in any new entry point.


## v0.3.0 additions (mixed batches, garbage tolerance, iterative set-aside sieve, paragraph ops)

- **Selector grammar additions** (must stay identical in `ops/selectors.py` and `verify/props.py`): `has_bold`, `is_all_caps`, `after_heading_regex`, `between_paragraphs` (`start_regex`, `end_regex`, `include_bounds`), plus fallback regex matching against tab/NBSP-normalized block text (`_block_text_with_tabs` / `_v_block_text_with_tabs`).
- **Fill rule additions** (must stay identical in `ops/slots.py` and `verify/template.py`): `first_match` (`rules` list tried in order), `flexible_whitespace` (default `True`), `occurrence` (`"all"` | `"first"`), and multi-anchor consensus in `paragraph_after`.
- **New operations**: `delete_paragraphs` (`ops/delete_paragraphs.py`) and `insert_paragraph` (`ops/insert_paragraph.py`), backed by V10 assertion kinds `deleted_paragraphs` and `inserted_paragraphs` in `verify/props.py` and inserted-block accept-view handling in `verify/suite.py` (V8). `insert_paragraph.text` is checked by `compiler.placed_words()`.

## v0.3.1 (fixes from the 2026-10-14 independent audit)

`docs/audits/2026-10-14_v0.3.0_independent_audit.md` is the audit; `tests/test_audit_fixes.py` pins every fix.

- **V4** fails on duplicate revision ids of every kind (see "Revision kinds" above).
- **Compiler gate** (`compiler._dry_errors`): every non-fatal refusal is pattern-sensitive; a spec that plans on no
  pattern, or an empty batch, is rejected. `PATTERN_SENSITIVE_CODES` is documentation only.
- **`expect` validation**: ranges are non-negative with `min ≤ max`; `rev_delta` kinds must be in
  `editspec.REV_DELTA_KINDS` (derived from `ooxml`'s revision tag tables).
- **Noise set** `ooxml.NOISE_RPR_LOCALS = {lang, noProof, webHidden}`; `w:cs`, `w:eastAsianLayout`, `w:snapToGrid`
  are visual. `merge_adjacent_runs(ignore_noise=True)` removes inert markers only between (or inside) two runs it merges.
- **`delete_paragraphs` tracked mode** keeps `bookmarkStart`/`bookmarkEnd`/`proofErr` children in place
  (`INERT_P_CHILDREN`); other non-run children still refuse (`structure`).
- **`insert_paragraph` `formatting: inherit`** — a both-sides definition, listed above.
- **Fills keep the document's characters**: `ops/slots.extract_fill` and `verify/template.fill_value` normalize
  whitespace only to match and to judge consensus; the value is the raw text, stripped.
- **`failed/`**: `batch.quarantine_failed_output` moves a failed output out of `outputs/`; `report.FAILURE_EXPLANATIONS`
  covers `verify-fail`, `section-break`, `existing-revisions`, `structure`, `textbox`, `table-cell`, `toggle-ambiguous`,
  `bad-params`, `conflict`.
- **Catalog**: `catalog.FILL_RULES` / `EXPECT_SYNTAX` (keys `fill_rules`, `expect_syntax`), a real `slot_fills`
  description, and `examples/specs/apply-header-footer-template-fallback.json`. Keep them in step with
  `ops/slots.py` and `editspec.py`.
- **Set-aside workflow**: `report.write_batch_report` writes `set_aside.json` and `SET_ASIDE_REPORT.md`; `batch.load_set_aside` loads them for `cli.cmd_retry` (`spec-surgeon retry`) and `gui.Session.load_set_aside`.


## v0.4.0 (Qt window, installable app, in-app updates)

- The Tk window is gone; `ui/` is the Qt window (see "Where things are" and the Qt rules there). `gui.Session` is
  unchanged apart from `last_batch`/`last_trials`; its strings that name controls must keep matching the window's
  labels — since Phase 8: "reply in the chat", "Start over", "I've looked — allow Run", "Load set-aside files",
  "Advise on exceptions" (the "Answers" box and the "Allow AI-written transforms" checkbox are gone).
- `FileResult.delivered_to` is the output path for a copy run and the source path for an in-place run
  (`ui/rows.written_back`); the window's run text no longer says "written back" for copies.
- Updates: `updater.py` + `ui/update_ui.py` + `installer.iss` (`[Code]` waits for the app mutex in silent mode,
  `ShouldRelaunch` reopens the app, `[InstallDelete] {app}\_internal` replaces the runtime). See "Updater rules".
- Packaging: `app.spec` filters Qt to what a widgets app loads (no software OpenGL, Quick/QML, PDF, TLS,
  translations); the installed smoke test (`installed_smoke.py`, `gui` and `update` modes) is the gate that nothing
  needed was dropped. Release notes in `docs/releases/v<version>.md` are what the update banner's What's new shows.

## 2026-10-15 GUI rework, license and About (after v0.4.1)

Abraham's ask: the window looked bland and was not intuitive; Settings carried panels nobody needs; pick a license.
What changed (`tests/test_gui.py` "the rework after v0.4.1" block, `tests/test_about.py`, `tests/test_packaging.py`):

- **Look and flow.** A dark navy rail with the four steps (`StepDelegate`: numbered circles on a line, green check when
  done, accent when current, a status line under each — files ready, the request, "N plan cleanly · M set aside",
  "N passed"); "STEP n OF 4" headers; white cards; an action bar per step whose primary button is disabled with a hint
  beside it until the step can go on (Files: a folder with .docx files; Preview: a current preview); a dashed drop
  zone on Files while no folder is chosen; empty states on Preview and Run; stat tiles with a toned top edge; the
  API-key pill. `theme.Tokens` gained the `sidebar*`/`rail`/`selection` colours; the look follows Windows light/dark
  only (`SPEC_SURGEON_THEME` for screenshots: `packaging/gui_screenshot.py` writes `docs/gui-files.png`, `gui.png`,
  `gui-results.png`, and with `--chat` `gui-chat.png`; look at them before changing layout).
- **Removed:** Settings → Appearance and Settings → Updates (and the `theme`, `native_style`, `auto_check_updates`
  settings — an old `settings.json` still loads), Help → Report a problem. Help: Check for updates…, What's new, User
  guide (online), Open logs folder, Save diagnostics bundle… (diagnostics), About Spec Surgeon. File (Phase 8): Your
  library… (Ctrl+L), Open library folder. The rail's foot: Settings, Check for updates, About Spec Surgeon.
- **About** (`ui/about_dialog.py`): author, © line, email, GitHub, LinkedIn, the MIT summary + full text, the
  third-party components, this copy's folders, Copy details. Values from `about.py` only.
- **License:** MIT (`LICENSE`, `pyproject.toml` `license = "MIT"` with `license-files`, `setuptools>=77` for PEP 639).
  The installer shows it (`LicenseFile`), names the author (`AppCopyright`, `AppContact`, `BeveledLabel`), shows the
  welcome page again (`DisableWelcomePage=no`) with the MIT summary and clickable GitHub/LinkedIn links
  (`InitializeWizard`), and ships `LICENSE.txt` beside the app; `app.spec` carries `LegalCopyright`. Silent installs
  (the in-app update, CI) are unchanged: Inno skips the license and welcome pages in silent mode.

## 2026-10-15 hands-on sweep fixes

`docs/audits/2026-10-15_v0.4.0_hands_on_sweep.md` is the sweep (Abraham's expectation list run through the engine,
plus a read of every module); `tests/test_sweep_fixes.py` pins every fix. The approval token covers the templates
(rule 12); font/paragraph ops carry `verify` touches (rule 9); `apply_header_footer_template` judges `unknown-slot`
against every template section the spec maps (a single-section spec in a cover+body batch plans); the guard refuses
read-only files and turns a refused replace into a V9 failure; `planner.summarize_plan` names pure tracked deletions.
Design notes recorded there and left as they are: a `replace_text` hit inside a field refuses the file (not just the
occurrence); a targeted paragraph selector makes `set_paragraph_props` tracked by default (§14.1).

## 2026-10-15 fixes from the external audit of v0.4.0

`docs/audits/2026-10-14_v0.4.0_checklist_audit.md` is the audit (another model's, at `3977ad9`);
`docs/audits/2026-10-15_v0.4.0_external_audit_response.md` the response; `tests/test_audit2_fixes.py` pins every fix.
Rules that came out of it, beside the batch/guard/render entries above and the slot-in-field sentence in "Slots":

- `fix_page_numbering`: the cover footer must be the cover's alone — `hf_common.effective_refs` decides what every later
  section SHOWS (default always; first with titlePg; even with evenAndOddHeaders), and a part shown by any of them is
  `shared-part` (rule 10 forbids adding an empty part to break the link). `continuous` removes the restart of EVERY
  section after the cover (one `section_props` assertion over all of them; more than one restart is flagged).
- `replace_text`: complex-field depth is counted over the whole part (`field_start_depths`), as `slots.field_runs` does;
  a paragraph inside a field that began earlier refuses `field-result`.
- `set_paragraph_props`: a point value for a side whose direct pPr or style chain sets `beforeLines`/`afterLines` is
  `conflict-property` unless the spec sets the lines value too.
- `compiler._dry_errors` tries a pattern's other files after a file-specific refusal (the gate must not depend on file
  order); `profile_batch` takes the next file of a pattern when the first will not profile (`unprofiled`); `advisor`
  isolates every record (an unreadable file gets a readable-file recommendation, no model call); `codegen` resolves the
  sandbox paths and turns a verifier exception into a failed trial.
- The window's preview key includes the results folder (and, since the Phase 8 review fixes, the Render pages option).
- Deferred, recorded in the response: F11 (`w:vanish` on a target's Header style — rendering on catches it), F8b (a
  results folder reused by hand), and the Tier B "bold headings" expectation (V10 has no bold assertion kind: a design
  limit, not a bug — stays unsupported until a verified operation exists).

## Diagnostics (2026-10-15)

Abraham: "Things are opaque right now. When things fail we need to be able to trace that and determine the why and
how." README → "Diagnostics — when something fails" is the user's view; `tests/test_diagnostics.py` pins it. Rules:

1. **Never call an engine failure a file problem.** `diagnostics.classify` decides: a `JobException` keeps its code
   (`refusal`; `internal` is an internal error); `unreadable` only for a file that will not open (zip/XML errors and a
   missing member before the transform, an OSError at acquire/open, a malformed SOURCE part found by
   `malformed_parts` at any stage); `io-error` for any other OSError; everything else `internal-error` with the
   traceback. Nothing records `crash` any more (it stays in `FAILURE_EXPLANATIONS` for old manifests).
2. **Every stage is traced as it happens.** `_process_one` notes start and end of acquire, open, fingerprint, each op
   (plan/apply via the `trace` hook of `plan_spec`/`apply_spec`), verify (with each failed check's specifics), write;
   the parent appends render, deliver and the result line. A new stage gets its notes; a note never stops a file.
3. **A diagnostic never stops the work it describes**: trace, log, compile record and explanation failures are
   swallowed (`write_json`, `Tracer._raw`, `RunLog._write`, `failure_details` falls back to the summary).
4. **The progress lines `run_batch` gives `progress` are pinned** (`ui/rows.ProgressTracker`, `test_ui_rows.py`):
   `"N file(s); transform+verify …"` and exactly `"  done NAME"` per file. Extra lines (a problem as it happens, the
   worker-died notice) must not match `^\s+done `.
5. **A dying worker costs only its own file**: `BrokenProcessPool` futures are run again, each in a fresh one-process
   pool (`_run_alone`, `attempt=2` appends to the trace); a second death is `worker-died` and its partial output is
   removed. Never retry in the main process (a native crash there kills the run or the window).
6. **A comparison that cannot run is a FAIL, never a skip** — a V6/V7 comparison that raises is that file's V6 FAIL
   with the traceback (as V10's "could not evaluate"); the batch's own failure is logged (`THE BATCH STOPPED during
   <stage>`) and DIAGNOSTICS.md written before the exception propagates.
7. **The model's exchange is kept**: `CompileResult.transcript`/`seconds` (built by `Conversation` from every `llm.call`
   of the conversation — its `exchange` — and written to `compile.json`), `Advice.transcript`
   (`advice/<name>.transcript.json`), `codegen/history.json` (every attempt, saved after the result is set),
   `VisualReview.transcript` (`visual_review.json`). Only API errors become a `failed` result — in a conversation a
   failed turn that appends nothing (rule 14) — with `llm.failure_text`/`api_error_text` (kind, HTTP status, request
   id): `llm.is_api_error` (also importable from `compiler`) covers the SDK's errors, httpx/httpx2 and
   httpcore/httpcore2, network `OSError`/`TimeoutError`/`ConnectionError`, the SDK's "Could not resolve authentication
   method" `TypeError` and `llm.ClaudeUnavailable` (no `anthropic` package: "Claude is not available: …"). A defect (a
   wrong argument, a test script that ran out, the tests' plain `RuntimeError` from `llm.client`) propagates — after
   `Session`/`cli._chat` saved the conversation's records. Two exceptions, by rule: `vision.review_samples` never
   raises (rule 15: a non-API error is `skipped` with its traceback in `VisualReview.transcript`), and a tool's own
   exception is answered as an `is_error` tool result (rule 14). The record builders (`llm.response_entry`,
   `results_entry`, `error_entry`, `transcript_content`, `block_dict`) are total — a block they cannot read is a
   `record_error` entry (rule 3) — and never keep image data (page renders are document content).
8. **Bundles never carry documents** (`DOCUMENT_SUFFIXES`, `DOCUMENT_DIRS`); keep it so, and keep the README's warning
   that records quote document text. A session's `run/` is bundled with its session (`bundle_root`, `SESSION_MARKERS`:
   compile.json, conversation.json, preview/, codegen/ beside it), mirrored under `results/`. Newer folders go under
   `results-later/<folder name>/` (`make_bundle(…, also=)`), never in place of the run: the window's `save_bundle`
   (`_bundle_folders`) bundles the last run with its session (else the last preview's folder — `out_dir` —, else the
   conversation's), plus `out_dir` and, while a conversation is open, `chat_dir` beside it; no folder twice.
9. **The approval refusal says what changed** (`preview.approval_inputs` kept in `preview.json` and on the `Preview`;
   `explain_approval_change`). `approval_token` itself is unchanged — old tokens stay valid.
10. **New work gets correlated start/outcome evidence.** `diagnostics.scope/event/span` (implemented in
    `telemetry.py`) write local JSONL with UTC time, process/thread identity, context IDs, parent spans and durations.
    Use explicit context propagation for process workers and a fresh `contextvars.copy_context()` per thread task.
    Workers write separate file journals; app journals rotate per PID. A span's `succeeded` means normal return,
    not document approval: include the actual status/check outcome. Keep the existing readable traces and pinned
    progress lines. See `docs/DEVELOPER_DIAGNOSTICS.md` and `tests/test_*observability.py`.
11. **Diagnostic metadata is bounded and credential-redacted.** Do not log raw model messages, generated source,
    image data, full argument/environment dictionaries or SDK objects. Keep detailed content in its existing
    transcript/verification records. Diagnostic writes must not replace the original exception or change processing
    decisions; failed destinations report once through stderr and any working application journal.
12. **No diagnostic I/O between the final overwrite check and `os.replace`.** The last
    `guard.check_before_write(..., _record=False)` deliberately omits success logging. Logging before it is safe:
    an external save during logging must be detected by that final check. Logging after it enlarges the race and
    can overwrite that save (`tests/test_pipeline_observability.py`).

## Phase 8: the conversation, Claude's visual check, custom transforms as an offer, your library

Decided with Abraham on 2026-10-15 (the plan's "Phase 8 status" records every decision and what was cut):
don't give up on the user (a conversation with choices instead of a dead end; replies from the Describe and Preview
steps and the terminal; suggested wording counts only when the user picks the choice showing it); the "Allow
AI-written transforms" checkbox is replaced by an offer at the point of need; Claude looks at rendered sample pages
only (cost), and sample pages may go to Anthropic; custom operations write edited copies only; a local library of
named recipes and operations (sharing later); a 64,000-token output cap, hence streaming. Decided by me (Abraham
delegated): `ask`/`agent` and `agent.py` retired (`SKILL_DIR` moved to `spec_surgeon/__init__.py`); the method rules
unchanged (no accept/reject of tracked changes, no docProps edits, no trackRevisions toggle — all three relaxed in
built-in batch 2 at Abraham's request, to "only when the user asks, through a verified op"); `set_properties` and a
generic V10 kind wait for the built-in-operations round; generated ops plain and limited to text parts + styles;
model/effort/fallback/cache unchanged; no version bump in the PR. Rules 13–19 above are this phase's.

- `verify/` imports none of `llm`, `conversation`, `vision`, `library`, `compiler`, `codegen`, `advisor`, `preview`,
  `gui` (`test_independence.py`).
- The window's run key includes the conversation (`_inputs_key`: id + number of user turns, read after the job), so
  a reply after a preview disables Run. Preview continues a matching conversation instead of starting a new one;
  Send and choices are disabled while the request text differs from `conversation.request`.
- The CLI's terminal loop is `cli._chat` (`#n` picks a choice; wording needs a yes; an empty line stops, exit 2);
  `cli._interactive()` decides terminal vs one-shot. Every prompt goes through `cli._ask` (None on `EOFError`/
  `KeyboardInterrupt`) or `cli._confirm` (True / False / None): None stops at the reply prompt and the wording approval
  (exit 2) and is a no at a y/N question; a typed `n` at the wording approval only declines the words. No bare
  `input(` in `cli.py`. `cmd_do` passes `conv.brief()` to `_visual_check` (`THE CONVERSATION:`), and re-checks the
  approval token (`preview.changed_since_preview`) after y/N or `--yes`, before `cmd_run` / `run_generated` (exit 2,
  "Not run: a file or the template changed since the preview — …" / "Not run: a file changed since the custom
  transform was tested and previewed — …"); `_do_codegen` takes the files' stats before `generate_op` and, once the
  preview is made, refuses a file that changed since (`_changed_while_writing`: exit 2 before the visual check, "Not
  run: a file changed while the custom transform was being written and tested — …", one line per file). `--save-as` goes through `_save_as_refusal(name, kind, replace)` — before
  the compile (kind "recipe" without a terminal and without `--allow-codegen`, else unknown: a name both kinds hold is
  refused), again once the kind is known (before the preview; at the start of `_do_codegen`); a `LibraryError` from
  the save is exit 2 ("--save-as: … — nothing was saved or run").

## Phase 8 merged with the GUI rework and diagnostics (2026-10-15)

Both landed after v0.4.1 on separate branches; the merge keeps both sides' features, under these decisions:

- **Master's model-side diagnostics are ported onto Phase 8's calls**, never back to `messages.create`: every call is
  still `llm.call`; `is_api_error`/`api_error_text`/`block_dict` live in `llm.py` (rule 18) and `compiler` re-exports
  them; one error rule everywhere Claude is called (Diagnostics rule 7), with `llm.ClaudeUnavailable` for a missing
  `anthropic` package. `Conversation.exchange` is the model exchange (conversation.json key `exchange`; `entries` stays
  what the user saw); `compile_result` copies it into `CompileResult.transcript`. The accepted-spec tool result is
  `conversation.ACCEPTED_RESULT` (tests assert equality with it).
- **The window's session folder** (`gui.Session`): `compile()` opens it (`out_dir = chat_dir = _compiled_dir`;
  `Conversation.work_dir` = its `chat/`); `_save_chat` writes `conversation.json` and `compile.json`
  (`{request, answers, **compile_result(turn).to_dict()}` via `diagnostics.write_json`; `last_compile`) after every
  turn; `_session_dir()` hands the first preview or custom transform that folder (master's `_compiled_dir`). Once a
  preview or transform has used a folder its records are frozen: `reply()` first opens the next session folder
  (`_next_session_dir`, never an existing one), and the records and the next preview go there. A non-API exception
  saves `conversation.json` before it propagates. A terminal conversation (`cli._chat`) keeps `OUT/conversation.json`,
  `OUT/compile.json` (via `write_json`) and `OUT/chat/`; a scripted `do` (`compile_request`) writes `OUT/compile.json`
  only, after the compile returns (no conversation record; nothing when a defect propagates).
  `diagnostics.SESSION_MARKERS` includes `conversation.json` (and `BUNDLE_README` names it); `save_bundle`
  (`_bundle_folders`) bundles the last run with its session (else the last preview's folder, else the conversation's),
  plus the last preview's folder and, while a conversation is open, `chat_dir`, under `results-later/<folder name>/`
  via `make_bundle(…, also=)` (Phase 8 review fixes; it used to bundle the newer folder INSTEAD of the run).
- **Run refusals say what changed** for a custom transform too: `Session.generated_inputs` (reset in `_set_outcome`,
  set beside `generated_token` — since the Phase 8 review fixes in `_show_generated`, last, for `_codegen`,
  `use_operation` and `_prove_again` alike); `explain_approval_change` bullets follow the
  unchanged prefix of the refusal.
- **preview.py** keeps Phase 8's `render_samples`, unique image stems, `GeneratedPreview`, `SampleView.pdf/look_pages`
  and the review fields, plus master's `logs/preview.log` (also for `preview_generated`) and a failing sample that no
  longer loses the preview; a sample's look pages are `[1]` when the page comparison cannot run.
- **The window** is master's reworked layout with Phase 8's pieces placed in it: the chat directly under "What should
  change?"; *Your library…* beside *Load saved spec…* in the "Or use a saved spec" card; *Save to library…* beside the
  request title at the top of the Preview body (the action bar keeps one primary button); the visual-check card in the
  Preview body. Rail status lines "Waiting for your reply", "Custom transform: <name>", "Look at Claude's findings
  before Run"; `preview_hint` "Claude is waiting for your reply"; `continue_hint` "Look at Claude's findings above
  first"; `run_hint` a short "Claude's visual check flagged problems — see the Preview step" with the full
  `Session.run_blocked_reason()` as its tooltip and in `do_run`'s message. Master's Preview `EmptyState` shows whenever
  `session.preview` and `session.generated` are both None (`_refresh_preview_empty`). `Page.scroll`,
  `_show_chat_reply` and `ChatView._add_now` stay. No Settings theme/native_style/auto_check_updates; the About box
  stays. The Answers box, the codegen checkbox and the hook keys `answers`/`vars.codegen` stay gone.
- **Tests:** master's tests that drove Claude through `messages.create` use `tests/fake_claude.py`
  (`raise_on_enter`/`raise_on_final`/`api_error`) and `Session.compile(request, log)`; never loosen an assertion to
  fit (an exact string stays exact — e.g. `ACCEPTED_RESULT`, not a `startswith`).

## Phase 8 review fixes (2026-10-15)

The merged branch was reviewed (an internal adversarially-verified review, Codex, the merge verifiers); every finding was
reproduced first and `tests/test_phase8_fixes.py` pins each fix with a test that fails without it (685 tests in
total). README → "The Phase 8 review fixes" is the finding-by-finding table. Rules that came out of it (the details are
in rules 13–17 and 19, Diagnostics rule 8 and "Where things are"):

- **A malformed submission is a rejection, never an exception** (rule 14): `validate_spec._check_shapes` first, and
  any exception of `check_submission` answered as `REJECTED`; Claude's lists of the wrong shape dropped and said.
- **The user's request, the user's words** (rule 13): the accepted spec's `request` is `Conversation.request`;
  `conversation.json` has `approved_words` and `answers`; `conversation.invisible` is the definition of a wording
  character the user cannot see.
- **Files by label** (`compiler.file_labels`): never key a batch file by its name.
- **The visual check is guarded at every stage** (rule 15): pages, client, call, answer; a skipped review has no
  findings.
- **A custom transform's approval follows its files** (rule 16): the token is checked again before every run (window,
  CLI); a stale one is re-proven by Preview (`_prove_again`); only `gui.TransformFailed` is noted to Claude.
- **A saved operation changes nothing until it passes** (rule 17): trials first, in a new session folder.
- **The CLI asks safely**: `_ask`/`_confirm` for every prompt; `--allow-codegen` needs `"possible"`; `--save-as`
  never takes the other kind's name.
- **The window**: *Render pages* is in the preview key; Save to library… and I've looked — allow Run only while the
  preview is current; the "Saved spec" bar only while that spec is the outcome; the chat closes when the conversation
  ends; Claude's choice details are escaped.
- **Bundles add newer folders, never swap the run out** (Diagnostics rule 8).
- **Tests are sealed from Credential Manager and the developer's model** (rule 19).

Second pass (the verifiers' residuals and gaps, each reproduced first and pinned the same way):

- **A custom transform is approved for the files as they were before it was written** (rule 16): window
  (`Session._codegen`: token left empty when a file moved; the reply's preview tries it again) and CLI
  (`_do_codegen`: exit 2 once the preview is made).
- **An advisor respec carries the original request** (rule 13); **no empty approved word** — no `"{}"` memory source.
- **Two more blank characters** (U+1D159, U+16FE4) refused in a wording; the Zs spaces stay allowed (rule 13).
- **Samples by label** in the visual check and the preview text (`compiler.file_labels`; rule 15).
- **The verifier never raises on None beside text in a sort** (`verify/suite._total_key`): the comparison is the
  same, so a mismatch is still a FAIL (rule 3: never weaker).
- **The window** (`gui.py`, `ui/app.py`): `Session.compile` takes `files(outcome=False)` before voiding the outcome (a
  template of the batch folder named by the outgoing spec is in the new conversation's files; `use_operation` the same);
  a defect while making a custom transform withdraws the queued "the outcome follows" note and tells Claude "… stopped
  with an internal error (<Type>) before any result — nothing was applied." (type only; never a verification result);
  an operation from Your library clears the request box once it passes; *Save to library…* warns on an `OSError` and on
  a name the other kind holds (Replace is offered only for a same-kind name).

The second pass's tests are `tests/test_phase8_fixes_window.py` (the window and its session) and
`tests/test_phase8_fixes_cli.py` (the command line, the advisor, the visual check, wording, the verifier's sorts).

- **The custom-transform sandbox** (review-7, rule 16): `codegen.check_source` is an ALLOWLIST for module attributes —
  a plainly imported module (`re`, `json`, `copy`, `math`, `itertools`, `functools`) or lxml's `etree` is usable only
  as `module.name` with `name` in `MODULE_ATTRS`, and any other use of the module object (passed, assigned, rebound,
  indexed) is refused; dunder names, attributes with `FORBIDDEN_ATTR_PREFIXES` (frames, code objects, generator
  internals: `f_`, `gi_`, `cr_`, `ag_`, `tb_`, `co_`, `im_`, `func_`) and lxml's own file readers/writers
  (`write_c14n`, `xinclude`, `xmlfile`, `XSLT`, `parser`, … in `FORBIDDEN_ATTRS`) are refused. The writer's prompt
  lists `MODULE_ATTRS`. `sandbox_runner.install_guard(workdir)` adds an audit hook before a line of generated code
  runs: writes, removals and renames only inside the workdir; no processes, sockets or native code; no `.pyc` writes.
  lxml's C-level file access is not audited — the whitelist is what keeps generated code away from it. Still a
  prototype sandbox, not an OS-level jail. Never relax `MODULE_ATTRS` or a forbidden list to let a generated op pass.
  `tests/test_phase8_fixes.py` feeds the bypass shapes to `check_source` as text (never executes them) and runs the
  guard on throw-away files.
- `codegen.generate_op` lists the samples to the writer by `compiler._Batch.label` (the labels its tools take).

## 2026-10-16 Qt teardown: the test suite's segmentation fault

`docs/audits/2026-10-16_qt_teardown_segfault.md` is the record; `tests/test_qt_lifetime.py` pins the fix. The pair
`python3 -m pytest -q tests/test_diagnostics.py tests/test_editspec.py` (and, sometimes, the full suite) died with
SIGSEGV in `Shiboken::BindingManager::runDeletionInMainThread` during the 24-file batch of `test_editspec.py`, leaving
four pool workers orphaned. Cause: `test_diagnostics.py`'s `qt` fixture only closed its windows, so the cyclic garbage
collector freed them later, on whatever thread was allocating — the pool's manager thread. PySide6 6.12 deletes a
Python subclass of a Qt class (`MainWindow`) on that thread at once (the `delete_in_main_thread` deferral applies to
plain Qt types only: `QWidget`, `QMainWindow`, `QDialog` are deferred to the main thread; `QObject`, `QTimer` and every
Python subclass are not) and defers the 44 plain widgets the window still "owns" by Shiboken's books (the parentless
`QLabel`/`QPushButton`/`QCheckBox` placed through layouts) to the main thread, where that second `delete` ran on memory
Qt had freed with the window. Rules that came out of it (also under "Qt rules"):

- A test lets go of a window only through `tests/qt_support.close_windows` (master's commit `4058f10` landed the
  remedy under that name the same day, from the symptom; this fix adds the mechanism, the wait for the window's threads
  and the regression tests): the Qt objects die now, on the main thread, in Qt's order, while the test holds every
  wrapper; `gc.collect()` there frees the rest of the test's Qt garbage on the main thread too. A window whose job,
  update check or download thread still runs at the deadline is kept alive in `KEPT_ALIVE` and named in a
  `TimeoutError`, never deleted under its thread (Codex's finding on the PR). Never `close()` alone; `deleteLater()` + `processEvents()` deletes nothing outside a running
  event loop (Qt holds a DeferredDelete back until control returns to the loop that posted it) — `test_gui.py`'s old
  fixture leaked its windows, which is why it never crashed anything. `flush_deferred_deletes` is
  `QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)`.
- Every QObject the window makes has a Qt parent in the window: `RowsModel` and `ProblemsFilter` were the only ones
  without (`ui/pages.py` now parents them to their page).
- The regression tests run the crash's own scenario (teardown, a collection on a worker thread, Python on the main
  thread) in a subprocess — with `close()` alone it segfaults three times in three; never assert on PySide crashing,
  assert on the invariant: after the teardown no wrapper of the window or of anything it owns is valid.

## v0.4.0 additions: template specs and font sizes

- `apply_header_footer_template` with `slot_markers: ["reference"]` (a both-sides definition, listed above). New refusal
  codes: `template-slot` (fatal in `compiler.FATAL_SPEC_CODES`: the template is the same for every file),
  `template-value-left`, `slot-in-field`; all in `report.FAILURE_EXPLANATIONS` with the older slot codes. The plan's
  notes show every occurrence (`mark_slots`) and flag a value shown in another case/spacing. The `template_parts`
  assertion carries `template_values`.
- `carve` replaced the old split-at-offset isolation for every slot kind; V10's run model now compares each run's
  content in order (it used to compare non-text children apart from the text, so a tab moved inside a run passed).
- Font sizes (both-sides definition above); `effective_font` assertions may omit `value` and carry `size`/`size_from`
  (`editspec.validate_spec` checks them).
- Compiler: `read_template` returns `template_spec` (prepared slots, or own values by `REFERENCE_FILLS` + ⟦ ⟧
  occurrences + problems, body font/size); the SYSTEM prompt and the catalog's rules say how to compose "match the
  header/footer / font of the template spec". Window: the Files card is "Template spec (optional)"; two new chips.

## 2026-10-16 Claude and your library

Abraham: "when I type something a saved item already does ('strip the headers and footers like last time'), Claude can
find my recipe or operation and offer it as a choice, so I reuse it instead of getting a fresh spec." The design note
`docs/design/2026-10-16_claude_and_your_library.md` was approved as recommended (after Codex's two findings on it: the
offered item pinned by hash, a failing terminal operation back to the conversation); `tests/test_library_choices.py`
pins every rule (24 tests, 709 in total); README → "Your library → Claude and your library" is the user's view. Rules:

1. **Chat only.** `compiler.library_index()` goes into the first message (`build_messages(library=)`, `YOUR LIBRARY`,
   at most `INDEX_LIMIT` 40 items within `INDEX_BUDGET`; `library_entry` counts a recipe's words, never shows them) and
   `CHAT_TOOLS` gets `LIBRARY_TOOLS` — `list_library` (`_Batch.list_library`) and `read_library_item`
   (`_Batch.read_library_item` → `library_item_detail`: the spec as saved, `placed_words` under `PLACED_WORDS_RULE`, the
   templates' existence, `dry_plan_summary`; an operation's manifest without `op.py`) — and `ASK_USER_CHAT` (the one-shot
   `ask_user` plus the kinds `recipe`/`operation` and `name`). `TOOLS`, `mode="compile"`, `ADVISOR_TOOLS` and
   `CODEGEN_TOOLS` are unchanged; a library kind in compile mode is dropped ("offered in a conversation only").
   Every read goes through the library's containment check (`library.recipe_file` / `read_manifest`, `_inside`; rule
   17): a listed item that is a symlink leading outside the library is an `error` entry in the index, the tool's
   error, and no choice. Results stay valid JSON: an index entry lists at most `ENTRY_OPS` op names and one that does
   not fit `INDEX_BUDGET` is skipped (the items after it still show); `_fit_detail` keeps a `read_library_item` result
   within `DETAIL_BUDGET` — the spec as saved gives way to an outline of its ops first, then over-long placed words are
   cut and said to be (the choice shows them whole) — never cut mid-JSON by `_dispatch`'s cap (Codex on #17).
2. **Named by Claude, built by code** (rule 13, "Library choices"): `conversation._library_choice`; a drop is said in the
   turn's `errors`, to the user as an app `Entry` (`_dropped`, appended by `_finish` after Claude's entry) and to Claude
   as a note with the next message. The label comes from the item (name, description cut at `DESCRIPTION_CHARS`, saved
   date, the request cut at 120); Claude's `detail` (or `label`) is the tooltip.
3. **The pick approves, and only the pick** (rule 13): `take_library_choice`; `take_app_choice` refuses library kinds.
   `Choice.words` is the truth of what a pick approves for every kind (`[wording]` for Claude's choices;
   `__post_init__`); `describe()`/`exact_words()` show them; `to_dict` records `words`, `name`, `item_hash`, never the
   snapshot.
4. **A saved operation is proven first, the conversation kept** (rule 17): `Session._take_library_choice`,
   `_use_saved_operation`, `TrialsFailed` / `OperationFailed`. A recipe: `_set_outcome(spec, source="compiled",
   recipe=name)` → `Session.recipe_name` (the Preview step's heading, `_intent`, `_note_preview`'s heading "THE PREVIEW OF
   YOUR RECIPE «NAME» (used as saved)"); `spec_source` stays `"compiled"` (the conversation's outcome: Preview with the
   request typed continues the conversation and previews the recipe, never compiles).
5. **Terminal**: `cli._talk(conv, save, out_dir, files)` — a choice's `words` approved in one prompt ("Approve these
   exact words for your documents: «A»; «B»? [y/N]"); a recipe returns `("recipe", spec, conv)` and takes `cmd_do`'s
   spec path; an operation is tried inside the loop (`_try_saved_operation`: `OUT/library-trial/try-N`, the token before
   the trials; a failure prints "Not used: …", notes it and `continue`s) and a passing one returns
   `("operation", (g, key, token, inputs), conv)` → `_run_saved_operation` → `_approve_and_run_generated`, the tail
   `_do_codegen` now shares (visual check, `--save-as`, y/N, the token re-check, `run_generated`). `_print_turn` prints
   one `exact words:` line per word.
6. **Window**: `ui/chat.py` builds one *Exact words* line per word from `words` (never Claude's text) and the buttons
   *Use it* / *Try it* (beside *Choose* / *Do this*); `ui/app.py` heads the Preview step *Your recipe “NAME” (as saved)
   for “request”*. `Your library…` (`use_operation`, `load_recipe`) is unchanged.

## Built-in operations round, part 1 (2026-10-16): `set_properties`

Abraham approved the design note's recommendations (`docs/SET_PROPERTIES_DESIGN.md`; PR #16) and the op was built
verifier-first. Rules that came out of it (the definition is in the both-sides list above; README → "Formatting
properties" is the user's view):

- **One op, two targets, never mixed in one call**: direct formatting (`scope`/`paragraphs`/`runs`; the compiler's
  default, tracked when targeted) or style definitions (`styles: {ids}`; never tracked; `paragraph` flags on paragraph
  styles only).
- **Zero characters**: `caps` is a display property. Highlight (the slot marker), hidden text, borders, shading,
  numbering, theme colours and the complex-script variants other than the `bCs`/`iCs` twins stay out until a verified
  kind exists.
- **Already as asked = untouched and uncounted**; the plan notes the count. Tracked mode records a change only where
  Word itself would have.
- **A style change is certain or refused** (`toggle-ambiguous`, `conflict-property`); V10 re-derives the partition on
  the output. Word's §17.7.3 XOR is still not modelled anywhere in the engine.
- **Rule 9 granularity unchanged**: a font change and `set_properties` on the same paragraphs are two specs; one
  `set_properties` call carries all of its properties. Finer aspects (per property) are a recorded follow-up.
- **Layout reference** `none` for `b`/`i`/`caps`/`smallCaps` and the paragraph flags, `source` for `color`/`u`/`strike`.
- `report.FAILURE_EXPLANATIONS` gained `conflict-property`; `CUSTOM_TRANSFORM_LIMITS` maps the new family to
  `properties` (the "cannot" list lost bold/italic/underline/caps and text colour); the profile's `styles_in_use` show
  `italic`, `color` and `color_theme` (Word renders the theme colour, not the cached `w:val`); `examples/specs/bold-part-headings.json` is the worked example. The verifier's change-marker
  tails of `run_fonts` and `paragraph_props` became `_check_rpr_change`/`_check_ppr_change` (shared with `properties`;
  the same checks, byte for byte).

## 2026-10-16 Chains: several specs, one after another

Abraham: "the user can elect to first change the font, then remove existing header/footer, and finally, to match the
header/footer of the template spec" — asked how feasible; the probe showed the engine refuses that as one spec
(rule 9: the font op's `verify` touch over the header parts the templating op removes; `remove_header_footer` +
`apply_header_footer_template` both remove the same parts) while a chain of RUNS passes, and he said "make it happen".
README → "Chains: several recipes, one after another" is the user's view; `tests/test_chain.py` pins every rule (16
tests, 784 in total). Rules:

1. **A chain never changes the engine's rules.** Every stage is an ordinary `run_batch` with its own plan, apply,
   V1–V10, render and guarded delivery; `merge_fragments` is untouched, and so are rules 2 and 9 — a chain is the home
   for "split them into separate specs". `chain.py` imports nothing from `verify/` beyond what `batch` does.
2. **The pin between stages.** Stage N+1 reads a file only when its md5 equals stage N's `FileResult.output_md5` (the
   bytes the suite verified, the hash `batch.verified_bytes` re-checks at delivery): copies mode reads stage N's
   `outputs/` copy, in place the source as stage N wrote it back; a mismatch is that file's `chain-drift` at stage N+1
   (`report.FAILURE_EXPLANATIONS`), never a read. The chain's own `outputs/` is written from `verified_bytes` of the last
   stage (`chain-deliver` when it is not).
3. **A file goes as far as it passes**: `ChainFile.stopped_at`, `stage_status` per stage (`PASS | FAIL | EXCEPTION |
   DRIFTED | NOT_RUN`), the stopping stage's `FileResult` in `results`; the later stages never see it. In place, the
   stages before the stop stay written back and the reason says so. `set_aside.json` lists the ORIGINAL sources with
   the stage (`stage: "chain stage N"`, `details.chain_stage/stage_name/stage_folder`) — `batch.load_set_aside`,
   `Session.load_set_aside` and `cmd_retry` read it as a batch's.
4. **Templates of every stage are inputs** (rule 12): `chain_templates` filters the inputs (`is_template_file`), and the
   approval token is taken over `chain_key(chain)` — `preview.key_templates` reads every stage's templates from it, so
   `approval_token`/`approval_inputs`/`changed_since_preview` take a chain key in place of a spec. `check_inputs_outside`
   runs on the chain's folder before anything is created.
5. **Order is the user's, flagged, never refused** (the one departure from rule 9, by design): a stage whose plan's
   `intended_parts`/`members_removed` meet a part an earlier stage's plan `members_added` gets a flag per file
   (`ChainFile.flags`, the report's "Flagged for review"). A plan-time conflict check ACROSS stages is a follow-up.
6. **The preview runs every stage on the samples in memory** (`preview.make_chain_preview` → `ChainPreview(Preview)`;
   `SampleView.stages`, `stages_text()`): stage 1's `plan_batch` on every file is the per-file table; a sample stops at
   the stage that refuses or fails (`exception["chain_stage"]`, the reason prefixed "stage N (NAME): "); the output
   written is the file as the last stage that applied left it. `Session.preview` holds it (a `Preview`), so the visual
   check, `run_blocked_reason`, `acknowledge_review` and the window's cards work unchanged; the intent is
   `chain_intent`.
7. **The library's chain kind** follows rules 13 and 17: `chains/<slug>.json` embeds the stages' specs as saved (made
   portable like a recipe — `_portable_spec`), a name belongs to one item across the three kinds (`_check_free`,
   `other_kind_holding`), reads go through `_inside`. Claude never offers a chain: `compiler.OFFERABLE_KINDS` keeps
   chains out of `library_index`/`list_library`; `library_item_detail` of a chain names its stages and says it cannot
   be offered; `LIBRARY_CHOICE_KINDS` is unchanged.
8. **One outcome at a time** (`Session._set_outcome(spec | generated | chain)`): `Session.chain`/`chain_source`
   (`"recipes"` from `build_chain`, `"library:NAME"` from `load_chain`, a path from `load_chain_file`),
   `outcome_key()` for the token, `chain_label()`, `_run_chain` → `last_chain` (`last_batch`/`last_trials` None),
   `save_chain` only while the preview is current, `advise` per stage folder. The window: `_widget_key` carries the
   chain, `_preview_current`/`_refresh_nav`/`_refresh_run_state`/`_refresh_spec_bar`/`do_preview` count a chain as a
   loaded outcome, `_fill_preview` heads the step with the stages, `_after_run` uses `rows.chain_rows`, `do_report`
   opens `CHAIN_REPORT.md`, `do_save_to_library` saves a chain; `Prompts.pick_sequence` (`SequenceDialog`, every
   widget parented to the dialog) and `RecordingPrompts(sequences=)`; hook `buttons["chain"]`.
9. **The progress lines gained one**: `"stage i of n — NAME: k file(s)"` before each stage (`rows.ProgressTracker`
   names the stage in the phase and restarts the bar per stage; `test_ui_rows.py`'s pinned lines are unchanged).

## 2026-10-16 Running without an API key (keyless mode)

Abraham: "most users either not have or will not want an api key … give the user [the option] to run this software API
keyless. And inform them that some functions are unavailable." README → "Running without an API key" is the user's view;
`tests/test_keyless.py` pins every rule (16 tests, each failing without the code it pins; 756 in total with
`set_properties` and the Qt teardown fix merged). Rules:

1. **One decision.** `llm.credentials_found()`: `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` (`CREDENTIAL_ENV`, non-empty)
   or a profile the SDK finds (`_sdk_credentials`: `anthropic.lib.credentials.default_credentials`, files only, never a
   call; any failure or no SDK is False). `Session.claude_ready()` = a client was given OR `credentials_found()`. The
   window caches it as `MainWindow.claude_ok`, set by `refresh_key_label` (at start, and whenever Settings saves or
   forgets a key) — never per keystroke. The command line asks `llm.credentials_found()` directly.
2. **Claude's features refuse before anything changes, with the reason; nothing else changes.** `Session.compile`
   (`gui.NEEDS_KEY`, before the outcome is voided), `Session.reply` (`NEEDS_KEY_REPLY`, nothing said or voided — the
   conversation waits), `Session.advise` (`NEEDS_KEY_ADVICE`); `cli.cmd_do`/`retry` (`NEEDS_KEY_DO`, exit 2 before the
   out folder is made) and `cmd_advise` (`NEEDS_KEY_ADVISE`). Never let a keyless path reach `llm.call` to fail there.
3. **The visual check without a key is a skipped review, built without preparing or sending anything**
   (`Session._visual_check`, `cli._review`: `vision.VisualReview(skipped=NO_KEY_REVIEW)`), saved like any review. Rule
   15 holds: it never blocks Run and never passes anything. The Preview card's helper says Claude did not look (`REVIEW_HELP`).
4. **The window in keyless mode** (`_apply_claude_mode`, `RequestPage.set_claude`): step 2 is *Choose the change* (page
   title, rail item, `KEYLESS_HINT`), the saved-spec card first, the request box/chips/codegen note hidden and the note
   listing `llm.CLAUDE_FEATURES` with *Set API key…* shown, the rail's `keyless_note`, *Advise* off with its reason
   (`_gate_claude_buttons`, re-applied after every `set_busy`/`set_updating`/`_after_run`; a key coming back turns
   on what the gate turned off, as the last run left it — `_advise_gated`, Codex on #20), the chat blocked
   (`NEEDS_KEY_REPLY`), the set-aside strip and run summary pointing at saved specs. `typed_request()` is "" keyless:
   every read of the request box that decides anything goes through it (`_widget_key`, `_refresh_nav`,
   `_refresh_run_state`, `do_preview`), so a hidden draft never compiles and never hides a loaded spec; `do_preview`
   with nothing loaded shows *Choose a saved spec* instead of compiling. **Nothing is cleared**: the draft stays in the box
   (and `settings.json`) and a key set later brings the full step back at once.
5. **`spec-surgeon library use NAME FILES…`** (`cli._use_library_item`): a recipe takes `_preview_and_run` — the tail
   `cmd_do` now shares after the compile (`--save-as`, preview, visual check, y/N/`--yes`, the token re-check,
   `cmd_run`); an operation `_try_saved_operation(None, …)` (rule 17: trials first, the token before them; `conv` None
   = nothing to note) then `_run_saved_operation`. `library show` names `library use`.
6. `packaging/gui_screenshot.py --keyless` writes `docs/gui-keyless.png` (refuses on a machine with an `ant auth login`
   sign-in). The other screenshots set a dummy `ANTHROPIC_API_KEY` in the script's own process (nothing calls Claude:
   a saved spec, *Render pages* off), so they keep the look with a key; `--chat` has its scripted client.

## 2026-10-16 Plan-time conflicts for what an op reads

The gap: rule 9 promised that overlapping ops are refused at plan time, but `apply_header_footer_template` computes
its pins from the SOURCE's `styles.xml` while `set_style_font` / `set_doc_defaults_font` / `set_properties` on styles
in the same spec change it underneath — its `touches` named only the imported styles, theirs the styles they change:
different targets, no conflict, a clean plan, a V10 `template_parts` failure after apply (when the target's style
already gave the template's font, so no pin was made) and a dry plan the compiler accepted. The same for the text
width and distances it reads from each section's `pgSz`/`pgMar` under a `set_page_setup`; and worse, for the
`titlePg` that `fix_page_numbering`'s cover-unnumbered reads: a headers-only templating op following its template's
titlePg turned the cover's off, the PAGE paragraph was cleared from the first-page footer the cover no longer showed,
and the file PASSED with the page number still on the cover (V7 skips page 1 there). Rules (rule 9 has the detail;
`tests/test_read_conflicts.py` plants each old behaviour and pins each refusal; `tests/test_ops_base.py` the grammar):

1. **An op declares what its prediction reads** as `verify` touches (`base.verify_family`): a whole member
   `(member, "*", "verify")`, a family of a target `(member, target, "verify:<family>")`, every family of a target
   `(member, target, "verify")`; `(member, "scope", "verify")` keeps its older, weaker meaning (the member must survive
   as a whole). `check_conflicts` rule 1 pairs a whole-member read with any change inside the member by another op,
   rule 2 a family read with a change of that family or of the target's `text`. Two reads never conflict; an op's own
   change of what it reads is no conflict. The refusal names the op to split out and the order: "split out X into a
   spec of its own and run it first, then Y on its outputs" — the second spec plans on the first's outputs.
2. **The templating op reads** the styles part and the theme part whole, and `pgSz`, `pgMar`, `titlePg` of every
   section (unconditionally: V10's `template_parts` reads the output's widths whether or not tabs are rescaled, and
   `hf_template_refs` predicts every titlePg). A `set_page_setup` of any section beside it is refused, top/bottom
   margins included (the aspect is the element, not the attribute).
3. **Cover-unnumbered reads `titlePg` of every section**; not `hfref`: a footer-reference change by another op always
   comes with the removal of the footer part it edits (rule 1 already) — `remove_header_footer` refuses to leave a
   section inheriting (`inheritance`), and the templating op removes every old part of the kinds it replaces.
4. **`continuous` beside the templating op is right as is** (neither reads nor changes the other's sectPr children;
   both strip their own before V10's residual) and stays pinned as planning and verifying in both orders.
5. **Not changed, recorded:** `set_font`'s symbol-run rule and `set_properties`' effective values with provenance also
   read the style chains from the source; a `set_style_font` beside them plans today and the compiler's "change all
   fonts" spec relies on it (`test_sweep_fixes.py`). A style change to a symbol font beside a `set_font` is still
   undeclared. Built-in batch 4 found a second way, now closed: `set_properties` on a style beside a direct
   `set_properties` judging the same paragraphs by their effective value through that style planned and failed V10 — the
   style target declares its own family (`props`) and the direct op reads it through its chains (`verify:props`), while
   fonts (`set_style_font`) stay a family of their own (`tests/test_batch4_conflicts.py`).

## 2026-10-16 Bounded recovery, final assertions and local repair memory

User-facing workflow and limits: `docs/RECOVERY.md`. Real-project validation protocol:
`docs/RECOVERY_EVALUATION.md`. These are source changes, not evidence of a published release or successful live
Claude/Word validation. The existing chains remain the execution mechanism; recovery connects investigation,
trial, final checks and reuse around them.

1. **Investigation is separate from export.** `recovery.recover` and `recover_from_run` operate on copies and return
   `RecoveryResult` (`READY`, `UNRESOLVED`, `NEEDS_INPUT`). Only `deliver_recovery` exports ready bytes. It rechecks
   frozen source/template identity, the recovery requirements, candidate identities and verified output hashes
   before writing. Never add recovery in-place delivery or a force flag. A partially written in-place chain is
   refused as a recovery input because replaying successful earlier stages may duplicate their effects.
2. **One budget covers every nested model call.** `llm.CallBudget` / `budget_scope` enforce calls, estimated USD,
   total tokens and per-call output limits at `llm.call`, including generated transforms and visual review. The
   configured model/effort applies within that scope; normal callers outside it keep their existing defaults.
   Preflight reserves the next request conservatively; failed calls with unreported usage retain reservations.
   Unknown pricing stops calls under a USD budget. Do not describe listed-price estimates as hard billing caps.
   Do not invoke a model through another SDK path to escape the budget. A new OS thread needs explicit context
   propagation or its own scope; ContextVars do not propagate to threads automatically.
3. **Keep the approved task fixed.** A candidate for an existing spec/chain must preserve its operation effects,
   selectors and desired values. Extraction rules, expected counts and stage order may be repaired; dropping an
   operation, narrowing a selection or changing the goal is refused. `--request` and desktop recovery answers
   append clarification to the original request, including requests without a compiled spec. They never silently
   replace the original task. Current user wording is the only approval source; retrieved cases and model notes
   cannot authorize project wording or templates.
   An explicitly approved adaptive contract instead fixes final effects and source-bound targets, allowing
   different operations/selectors or generated methods only under independent original-to-final verification.
   The contract, its template hashes, wording, target cardinality and user choices are immutable authority.
4. **Stage verification and final requirements are distinct.** A chain's declared `assertions` are frozen and
   independently evaluated by `verify.outcomes` after the final stage, before delivery. Preserve them through
   `chain_key`, saved library chains, preview, and CLI `chain:NAME` expansion. A later stage violating a declared
   final requirement fails the chain even when every stage passed. Chains with final assertions require copies
   mode: stage-by-stage in-place writes cannot promise a final result before altering originals. The outcome
   verifier obeys rule 1's forbidden imports and does not ask the transform what happened.
5. **Unsupported final requirements are refused, not discarded.** Source-relative final font requirements such
   as preservation exceptions or sizes derived from the original source cannot currently be represented by the
   supported independent final assertions. Recovery refuses them; a note or visual check is not a substitute.
   Normalize EditSpec defaults before inferring final checks: omitted `preserve_fonts` means the engine's
   symbol-font exceptions, while an explicitly empty list has none. Candidate validation uses the same rule
   against the original goal; a candidate cannot opt out of the original preservation contract. EditSpec size
   assertions exempt runs holding `w:sym` even with an empty preservation list and cannot become universal checks.
   Declared final assertions check only what they express. Never equate stage PASS, final assertion PASS, READY,
   delivery or a model's "no findings" with satisfaction of arbitrary natural-language intent.
6. **Custom recovery requires independent coverage.** Legacy tasks without an adaptive contract still require
   explicit opt-in and only support goals consisting entirely of page-setup operations. Supported approved
   adaptive contracts allow sandboxed custom methods automatically unless disabled. Independently verify the
   original-to-final effect and complete preservation boundary, as well as the generated method's own plan.
   A writer or critic cannot authorize unrelated edits by predicting or endorsing them. Library reuse must retain
   the contract and binding authority or explicitly refuse governed methods; it must never silently downgrade them.
7. **Visual review remains advisory and can block export.** Recovery feeds findings back into investigation while
   attempts remain, preserves unresolved or skipped-review evidence, and shows current findings in Preview.
   `review_ack` is ephemeral and bound to this recovery token; it is never restored from saved JSON. CLI `--yes`
   does not imply `--ack-review`. No generated-plan success or reused case can automatically acknowledge findings.
8. **Memory is evidence, not authority.** `repair_memory.CaseStore` uses bounded local SQLite JSON records, never
   executable serialized code. Store structural verification, visual evidence and user feedback separately.
   Cases and model tool results retain only their own trial's visual review, never the aggregate batch review
   or stale findings from another attempt. A trial without verified output explicitly records no review.
   Similarity retrieves suggestions; automatic eligibility also checks the current request, goal, structural
   pattern, template hashes and application version. Every reuse still requires current wording validation and
   fresh planning/verification on EVERY file. Negative feedback blocks the repair identity across duplicate cases;
   a new successful record or later acceptance cannot erase it. Failed attempts remain searchable. Malformed or
   unavailable stores are reported and left intact, never silently reset or treated as verified evidence.
9. **Local memory has an explicit data boundary.** Requests, notes and feedback remain freeform local text; path
   redaction does not make records anonymous. Relevant previous cases may be sent to the configured model when
   recovery uses memory. No weight training or shared cloud repair pool exists. `--no-memory` / the desktop
   checkbox disable both retrieval and recording for that investigation; existing cases are managed through
   `memory list|show|accept|reject|forget` or File → Repair memory. Do not imply disable means deletion.
10. **Continuation must retain scope and ready work.** Recovery writes `set_aside.json` with unresolved files and
    the original goal/approved wording. `Session.resolve_remaining` can enter from failed compilation, preview
    or a run. A newer conversation/preview supersedes an unrelated older run. Ready copies must be exported before
    another investigation replaces their preview; continuation then uses the current recovery's unresolved
    manifest, never all original files again. Do not resume from partially edited stage outputs as though they
    were the original source.
11. **Validation claims need provenance.** Scripted-client tests never call Claude or use real credentials.
    `recovery_evaluation` compares saved reports and explicit human judgments without opening documents or making
    model calls. Keep scripted and live cohorts/costs separate. Real Claude repair quality, actual API economics,
    project-document success rates and Word COM fidelity remain unvalidated until the documented real-batch
    protocol is performed; do not infer them from unit-test counts.

## Built-in operations, batch 1 (2026-10-16)

Abraham: "let's implement additional prebuilt operations … in manageable chunks. here is the first batch" — ten items
(fill project variables, a replacement table, MasterSpec options, tagged paragraphs, the issue stamp, END OF SECTION,
agreeing header/title/file name, a logo swap, a snippet library, output names). `docs/BUILTIN_OPS_BATCH1_DESIGN.md` is
the design with every decision (taken on his behalf, each changeable); README → "Built-in operations, batch 1" is the
user's view; the both-sides definitions are listed above. Rules that came out of it:

1. **One edit core for every in-paragraph word change, one item model to check it.** A new op that changes words
   inside paragraphs plans through `textedit.plan_paragraph` and applies through `textedit.apply_part_edits`; its V10
   kind derives the edits again from the SOURCE with its own code and hands each paragraph to
   `edits.check_edited_paragraph` (tracked deletions of whole paragraphs: `check_deleted_paragraph`), then records the
   blocks in `ctx.verified_blocks`. `replace_text` is left as it was.
2. **Text-changing ops are tracked by default**; `tracked: false` → mode `untracked` (clean copies). Formatting
   (`swap_image`, `name_output`) stays `formatting`.
3. **Tables and pictures are inputs like a template** (rule 12): pinned by md5, re-checked at apply, re-read by V10,
   hashed by the approval token, absolute in a recipe. Readers on both sides, no new dependency; a number Excel formats
   its own way, a boolean, an error or an invisible character is refused when used (`table-value`) — never converted.
4. **A file name is not document text**: `name_output` may transform a value (named, visible transforms only), the
   batch takes every name from the plans before the workers start, two files with one name are both set aside, a spec
   that renames writes copies only (in place refused before anything is written), and V10 compares the derived name
   with the name the copy was written under.
5. **A logo swap adds exactly one member, a copy of the user's file**, keeps every relationship id, removes old media
   only when nothing references it, and is refused for any picture whose swap Word would not show as fitted (linked,
   SVG, cropped, turned, VML, `mc:AlternateContent`, a shared relationship). The residual masks only the two sizes of the
   drawings `image_swaps` checked.
6. **Snippets are a library kind, inserted by `insert_paragraph`** (`snippet_spec`: `allow_missing` and `expect`
   `text_diffs` ≤ 1 — every file that has the anchor, never twice); one plain paragraph (`snippet_text_problem`); in a
   conversation the pick is a reply (rule 13).
7. **Claude sees the user's tables and pictures only as listed** (`inputs.py`): the first message's INPUT FILES and
   `read_input_file` over exactly those paths; never a path Claude makes up.
8. **Every refusal code has a plain-words explanation** (`report.FAILURE_EXPLANATIONS`); `tests/test_batch1_integration.py`
   scans the batch-1 op modules for codes and fails on one without an explanation.
9. **Not in this batch** (recorded): tags as styles/highlights or whole articles, text boxes, replacing across tabs or
   breaks, a tab in a replacement or snippet, rich snippets, renaming in place, two logo swaps in one header in one spec.
10. **Merged with master's reads and chains** (2026-10-16): the ops that extract a value declare what they read
    (rule 9: `slots.fill_reads` → `(part, block, "verify:text")`; `ensure_end_of_section` also `(main, "end", "verify")`
    and its copy-from paragraph; `make_values_agree` every matched paragraph and, with a file-name place, the output
    name), so rewriting the title line a name or a number comes from — or adding a paragraph after an END OF SECTION
    line — in the same spec is a plan-time conflict (`tests/test_naming.py`). A chain follows a renaming stage's name to
    its `outputs/` (`ChainFile.out_name` takes each passing stage's `out_name`), and refuses `in_place` with a renaming
    stage before anything is written (`chain.run_chain`). The approval token of a chain covers every stage's tables and
    pictures (`preview.key_templates` → `spec_input_files`); snippets are offerable (`compiler.OFFERABLE_KINDS`), chains
    never are.

## Built-in operations, batch 2 (2026-10-16)

Abraham: "Let's add some more prebuilt operations" — items 11–21 (remove comments ★, remove highlighting ★, accept or
reject tracked changes, remove hidden text, remove specifier notes, add/remove a watermark, remove blank paragraphs,
remove hyperlinks, convert fields to text, set document properties, track changes off / mark as final).
`docs/BUILTIN_OPS_BATCH2_DESIGN.md` is the design with every decision (taken on his behalf, each changeable) and, at its
end, what the build changed; README → "Built-in operations, batch 2" is the user's view; the both-sides definitions are
listed above. Built by six parallel builders on the shared machinery (§1), then integrated. Rules that came out of it:

1. **Three method rules changed — only this far** (design §0; `skill/SKILL.md`, `references/element-types.md`):
   accepting/rejecting tracked changes, editing the document properties and toggling Track Changes happen **never on the
   engine's or Claude's own initiative, only when the user asks for it by name**, through `resolve_tracked_changes`,
   `set_document_properties`, `set_document_state` — each previewed with exactly what it does and verified like every
   other op. `codegen.hard_limits` is unchanged: a custom transform still can never do any of the three. Claude's
   prompt says so; never offer them as a side effect of another request. `catalog.REQUEST_ONLY_OPS` names the three;
   the advisor's respec may not add one the original spec lacks (`advisor._respec_errors` checks it before anything
   else, and `ADVISOR_SYSTEM` says to recommend it instead).
2. **Clean-up runs over whole batches**: every op of the batch that removes something defaults `allow_missing` to true —
   a file with nothing to remove plans as a no-op with a note and passes; `false` keeps `not-found`.
3. **An assertion carries the user's own words**: scope, options and rules exactly as the spec gave them (absent = the
   default); the V10 kind applies its OWN defaults and normalisation and compares its decisions with the plan's. Never
   hand a kind the transform's resolved copy of a parameter (a wrong default would reach plan, apply and check alike).
   Tests plant exactly that (`test_a_default_scope_that_forgets_the_headers_is_caught` and siblings).
4. **A kind that re-derives over every part in scope declares `(part, "scope", "verify")`** (rule 9) — whether or not
   it changes the part.
5. **File-specific refusals are never `bad-params`** (`compiler.FATAL_SPEC_CODES` rejects a whole spec on it): a value
   that does not fit one file (`watermark-size`), a property of another type in one file (`property-type`) get codes of
   their own. Every code has a plain-words explanation; `tests/test_batch2_integration.py` scans the batch's op
   modules with `ast` (the code argument of every `JobException`; the one computed code, `links._field_refusal`, is a
   key of `fields.REFUSAL_TEXT`).
6. **Structure-only declarations never excuse a paragraph** (Codex on #27): the paragraph stays in V10's residual unless
   the kind that made the change checked it item by item and registered it in `verified_blocks`.
7. **Whole parts, whole members.** `resolve_tracked_changes` is checked by a REBUILD of each part it resolves (the
   verifier never edits a copy in place as the transform does) compared element for element; the comment side parts,
   the rels part of a removed address and a created `custom.xml` are compared whole (`verified_members`); `settings.xml`
   and the properties parts by the member residual. Never register a part or member as verified without comparing all
   of it — and never claim one the op does not change: another op in the same spec may change it legitimately, checked
   by its own kind, and the residual is what lets that through and nothing else.
8. **Words placed** (rule 13): `add_watermark.text`, `set_document_properties.set` and `custom` values
   (`compiler.placed_words`); the other ops place none (accepting keeps the document's own inserted words, rejecting
   brings back its own deleted words). Custom property NAMES are labels, not document text.
9. **V5a and `settings.xml`.** libxml2 stops checking a sequence at the first child it does not expect, so a source
   whose `w:trackRevisions` stands first hides a later error (python-docx's `<w:zoom w:val="bestFit"/>` without
   `w:percent`); "track changes off" removed the element and V5a called the reached error new on a correct output.
   `xsd.compare` therefore also validates, for a root `xsd.REORDER_BASELINE` names (`w:settings`), a copy of the source
   with its children stable-sorted into the schema's sequence (`xsd.sequence_order`, read from the bundled schema;
   `in_schema_order`) and adds those errors to the baseline: it only reorders, so each is the source's own — a really new
   error is still new (`tests/test_batch2_integration.py`). **Recorded, not changed**: V5a still drops messages
   containing `hyphenationZone`, which hides an out-of-order settings element before it (`document_state` checks the
   place of `w:trackRevisions` itself — `verify/docprops._in_order`). The clean-up ops' reads of the style chains are
   not declared as reads (as `set_font`'s). Not in this batch: adding a
   picture watermark, comments whose marks sit in text boxes, joining paragraphs on accept/reject, table row/cell
   revisions, rejecting section/table property changes, highlight in styles, `w:removePersonalInformation`.
10. **Profiles show the markup** (`profile.markup`) so Claude can see what a clean-up op would find; it is descriptive
    only (no op reads a profile).

## Built-in operations, batch 3 (2026-10-16)

Abraham: "Let's add more prebuilt operations" — items 22–25 (bold/italic/underline/caps/small caps on chosen paragraphs ★,
page-flow settings ★, text colour, tab stops). `docs/BUILTIN_OPS_BATCH3_DESIGN.md` is the design with every decision
(taken on his behalf, each changeable); README → "Built-in operations, batch 3" is the user's view; the both-sides
definitions are listed above. Rules that came out of it:

1. **Items 22–23 are `set_properties`** (built in the round's part 1). What changed is the guidance: "each PART on a new
   page" is pageBreakBefore on every PART heading AFTER the first (the PART pattern + `after_heading_regex` of the same
   pattern) so PART 1 stays under the section title; "no stranded article titles" is keepNext on the article titles;
   widowControl is on by default (ECMA-376 §17.3.1.44). Worked examples: `part-on-new-page.json`.
2. **Recolouring chooses by the effective colour** (`runs.color_in`): a style's blue counts; "black" is `auto` unless the
   user asks for `000000`; Claude takes the colours from the profiles and names them in assumptions. `runs` narrowing alone
   is not "targeted" (§14.1: plain); a `paragraphs` predicate is. Worked example: `editors-blue-to-black.json`.
3. **`set_tab_stops` is direct formatting with an "already" rule**, like `set_properties` (not `set_paragraph_props`' blind
   merge): a paragraph whose stops are already as asked is untouched and uncounted; tracked records a change only where
   one happens. Inherited stops are cleared with clear entries, own ones removed.
4. **Margin positions are measured, never guessed**: per paragraph from its section's text width; a header/footer part shown
   at two widths, a section without page size/margins, footnotes/endnotes/comments, or a margin position landing on another
   asked position refuse the file (`tab-position`, file-specific — never `bad-params`, batch 2 rule 5). The reads are
   declared (rule 9).
5. **Numbered paragraphs: only own entries are certain.** The op does not read list definitions (their `num` tab); an
   explicit clear writes a clear entry on a numbered paragraph, `clear_others` leaves the list's tab and is flagged. Do not
   "improve" this into reading numbering.xml without a both-sides definition of list levels (pStyle links, lvlOverride,
   numStyleLink) and its tests.
6. **The assertion carries the user's own words** (batch 2 rule 3): `select` and the raw `set`/`clear`/`clear_others`/
   `tracked`; the kind applies its own defaults (align left, leader none, scope document, tracked by §14.1). Tests plant a
   wrong default alignment and a wrong default scope shared by plan and apply.
7. **pPr granularity unchanged** (rule 9): tabs, spacing/indents and paragraph flags on the same paragraph are separate specs.
8. **Custom transforms** may emit a `tab_stops` assertion (`CUSTOM_TRANSFORM_LIMITS`: "tab stops of paragraphs").
9. **Not in this batch** (recorded): tab stops in style definitions, bold/colour on part of a paragraph (run-in headings), a
   list-level selector, theme colours as a value to set.

## Built-in operations, batch 4 (2026-10-17)

Abraham: "let's add more prebuilt operations. build these" — items 26–32 (paragraph borders and shading; spacing and indents
on the styles themselves; clear direct formatting; the proofing language; ★ apply a style to paragraphs picked by
appearance or pattern; ★ style mapping importing your styles from a template; remove unused styles).
`docs/BUILTIN_OPS_BATCH4_DESIGN.md` is the design with every decision (taken on his behalf, each changeable) and, at its
end, what the build changed; README → "Built-in operations, batch 4" is the user's view; the both-sides definitions are
listed above. Built by four parallel builders on the groundwork commit (the verifier hooks, the `pStyle` reads, the
fixtures `build_styles`/`build_style_template`), `apply_style` last on their reuse APIs, then integrated and reviewed.
Rules that came out of it:

1. **A paragraph's style is a property family others read** (`pStyle`, §0.1): restyling changes how every inherited
   property resolves, so an op whose plan or post-condition reads the style chain declares a soft `verify-props:pStyle`
   per paragraph plus `selectors.chain_reads` (the table's style, `verify:replace` of every chain style, `verify:add` of a
   chain style the file lacks), and the restyling ops touch `pStyle`/`rStyle`/`tblPr`. A new op that reads a paragraph's
   effective formatting in the output must declare it too. A selection that reads every paragraph's chain (`style_in`,
   `has_bold`, `is_all_caps`, runs `color_in`, effective hidden) gets whole-member soft reads from
   `selectors.selection_families` — add a key there when a selector starts reading the chains.
2. **A post-condition on an already-as-asked target needs a read** (Codex on #28, again here): an op whose V10 check reads
   a target in the OUTPUT whether or not it changed it declares a family read of that target (`verify:tabs`,
   `verify-props:pBdr`, `verify-props:rPr` …), and an op that removes property elements declares one touch per element
   name it removes (`clear_direct_formatting`), so the two meet at plan time. Make such a read soft (`verify-props:`) when
   the kind re-resolves its targets over the output: a hard one refused every text edit of the paragraph (the review's
   regression: `set_properties` beside `replace_text`).
3. **Styles named by an op are read; removals are every aspect; uses are counted.** An op that writes a reference to an
   existing style (applies it, maps to it, inserts with it, imports a style based on it) reads `(styles, "style:<id>",
   "verify")`; `remove_unused_styles` declares its removals as `remove`/`rPr`/`pPr`/`tblPr` of that style. Which elements
   name a style is the incremental family `uses+`/`uses-`: mapping A→B beside B→C, applying a style beside mapping it
   away, applying a style beside a selection by it, and mapping beside removing unused styles are plan-time conflicts
   (each order gave another file); two ops adding uses of one style still plan. "Used" is judged on the SOURCE, so mapping
   then removing is a chain, the removal last — in one spec it is refused.
4. **The style import is shared and checked on its own** (`ops/style_import.py` ↔ `verify/styleimport.py`): an op that
   imports adds the import's touches and its `styles_imported` assertion, and its own kind checks that assertion carries
   exactly the template styles it found needed (`check_requested`) — an op can neither skip the import's check nor smuggle
   an import past it. Numbering is copied with new ids above the document's own, never renumbering what is there;
   picture bullets and numbering-style lists refuse (`style-numbering`).
5. **Borders replace the whole box, with `nil` for every side a lower level shows** — Word's per-side vs per-element
   inheritance of paragraph borders is not modelled; the written box is right under both readings, and V10's output check
   tests both.
6. **Clearing never reveals or renumbers**: hidden text stays hidden, list numbers stay, symbol fonts keep their glyphs,
   superscripts and highlight stay unless asked. `keep`/`also` take element names (bold is `b` + `bCs`).
7. **Never tracked, by design**: style definitions (spacing, borders on styles, imports, removals) and the proofing
   language. Mapping is plain by default (a whole-document change), tracked on request (tables refuse tracked).
8. **Not in this batch** (recorded): run, page and table-cell borders; theme colours as a border/shading colour to set;
   tab stops in styles; mapping a linked character style automatically; editing field codes that name a mapped style
   (flagged); picture-bullet lists in imports.

## Built-in operations, batch 5 (2026-10-17)

Abraham: "let's add some more prebuilt functions" — items 33–40 (★ convert to your PART / 1.1 / A. / 1. / a. scheme from an
example file; ★ typed "A." and "1." become real list numbers, the typed characters removed exactly as predicted; restart
per PART, a level's format or zero padding; a numbering audit; table borders, cell margins, width, header rows, rows that
do not split; a table style on every table; different first page and header/footer distances; landscape for chosen
sections). `docs/BUILTIN_OPS_BATCH5_DESIGN.md` is the design with every decision (taken on his behalf, each changeable)
and, at its end, what the build and the review changed; README → "Built-in operations, batch 5" is the user's view; the
both-sides definitions are listed above. Built by a list-model builder first, then four builders in parallel (numbering
conversions, list levels and the audit, tables, sections), each put through an adversarial review whose findings were
reproduced before they were fixed. Rules that came out of it:

1. **Numbering is predicted, never assumed.** Every numbering op counts with the shared list model and its V10 kind
   recounts with the verifier's twin: its own prediction from the source must equal the plan's, and its own count of the
   output must equal both. Where Word's behaviour is not certain — or Word and LibreOffice differ — the model says "not
   evaluated" with the reason (shared list definitions, override levels, skipped levels, a startOverride level starting
   again, a level the paragraph's own numPr leaves ambiguous, linked lists, unsupported formats); an op never predicts
   through one: a conversion refuses, the audit reports. Do not "improve" a not-evaluated case into a guess without a
   both-sides definition, its tests, and Word evidence.
2. **The scheme is the user's own.** The engine never writes a list definition: the levels come from the user's example
   file (imported by batch 4's style import, new list ids) or the document. A conversion's destination list must be
   clean (`list_is_clean`).
3. **Typed numbers become list numbers only when the document reads the same.** The plan computes the visible line of
   every counted paragraph over the planned output and refuses the file on any difference (a typo, a skipped letter, a
   list that would renumber other paragraphs) — never a silent renumbering; hidden or capitalised typed numbers, and a
   number its mark would show hidden or in capitals, refuse (`typed-format`). The edit core deletes a tab only for this op
   (`tabs=True`).
4. **Plans see what V10 checks** (Codex on #32): a post-condition over the whole document is computed by the plan first
   and refused or flagged there — V10 never discovers a number change the plan did not predict.
5. **LibreOffice is not Word for numbering**: it ignores `w:lvlRestart`, does not count hidden numbered paragraphs, ignores
   linked levels, numbers text boxes. The model follows Word; the ops flag where the proof PDFs will differ.
6. **numbering.xml has owners.** `list_level` compares it whole; the import's no-copy check stands down only for an
   owner kind (`NUMBERING_OWNER_KINDS`). Two ops changing it are a plan-time conflict.
7. **Tables are whole border sets, merged margins, never tracked**, chosen by the table selector (ordinals over
   `root.iter(w:tbl)`); cell overrides are flagged and cleared only on request, and then only the sides asked.
8. **Sections by content are re-derived by V10** from the user's own `select`; a content selection that chooses every
   section is flagged; the engine never adds a section break.
9. **A titlePg written off is off** everywhere (`package.sections` reads `w:val`), and fix_page_numbering's
   cover-unnumbered is checked against what the cover shows in the output (`cover_unnumbered`).
10. **Not in this batch** (recorded): inserting section breaks around a schedule; restarting a list with new `w:num`
    instances; tracked table or numbering changes; numbering in headers/footers/text boxes; numStyleLink/styleLink lists;
    custom (`001`) formats; rotating margins for landscape; Word itself (nothing here was checked in Word — the LibreOffice
    proofs were).
11. **The op catalog has a size budget** (found merging batch 5 with master's context epochs). `compiler.system_prompt`
    carries the whole `op_catalog()` — ops, the EditSpec schema, every worked example — in every conversation, and
    `contexts.estimate_context_tokens` (3 bytes a token, 4,784 an image) starts a new epoch past
    `resources.DEFAULT_CONTEXT_TOKENS` (120,000). Batch 5 took the first request from about 86,500 to about 105,000.
    `tests/test_contexts.py::test_the_default_allowance_leaves_a_conversation_room_for_two_page_images` runs a real
    two-page `view_pages` exchange and fails when it starts a new epoch (about 5,300 estimated tokens of room are left
    after batch 5): then compact the catalog or raise the allowance — Abraham's call,
    a documented default (`docs/ADAPTIVE_WORKSPACES.md`) — never loosen the guard. A test whose subject is not rollover
    sets its own `max_context_tokens` (as `tests/test_vision.py`'s image-allowance test and master's rollover test do).
