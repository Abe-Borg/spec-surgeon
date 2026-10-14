# CLAUDE.md — Spec Surgeon

Working notes for any Claude session touching this repository. Read before editing.

## What this is

A deterministic engine for surgical .docx edits (Tier 1 of the three-tier design), plus a thin optional
model layer. Since NL-editor Phase 1 the engine is an **operation library** (`ops/`) executed through
**EditSpecs** (`editspec.py`, `spec-surgeon run-spec`); the v0.1.0 jobs are thin aliases over two of the ops. Phase 2
added the header/footer templating op (`apply_header_footer_template`), the first op that adds members. The method it implements is the `docx-surgical-edit` skill bundled at
`src/spec_surgeon/skill/` — read `SKILL.md` and `references/verification-checklist.md` there before
changing any job or check. The user (Abraham) is a fire sprinkler designer; these files go to clients;
"I will lose my job" if content is silently lost or invented is the spec.

## Hard rules

1. **Verifier independence.** Nothing under `src/spec_surgeon/verify/` may import `spec_surgeon.jobs`,
   `spec_surgeon.ops`, `spec_surgeon.editspec`, `spec_surgeon.ooxml`, or `spec_surgeon.package`
   (`tests/test_independence.py`). It has its own zip reader (`verify/reader.py`), its own text extraction
   (`verify/textcheck.py`), and its own selector resolution, style-chain walk and theme lookup
   (`verify/props.py`), and its own slot detection, fill extraction and effective-property resolver
   (`verify/template.py`). Do not "deduplicate" these into shared helpers — the duplication is the point: a
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
   No `force` flag exists and none should be added.
7. **Scripts live in the repo, not in project folders.** Scratch work goes to a temp dir. Never name a
   module after a stdlib module (`inspect.py`, `types.py`, …) — a script in the working dir shadows
   stdlib imports inside third-party libraries.
8. **Everything an op changes is predicted or asserted.** V10 runs on every plan: after stripping what the
   `property_assertions` cover and the blocks with predicted text changes, the changed text parts and
   `styles.xml` must be identical. A new property family needs a new V10 assertion kind written in
   `verify/props.py` first (selection re-resolved independently, target count checked, "exactly this change and
   nothing else" on each target). Never add a kind that strips without checking. Spec-level `assertions` in an
   EditSpec are post-conditions only (`effective_font`) — a spec must never be able to exempt anything from V10.
9. **Overlapping ops are refused, never ordered by guess.** `merge_fragments` raises `conflict` when two ops
   touch the same block's text, the same property family of the same target, or anything in a member another
   op removes/restructures. Ops declare what they touch in `touches`; keep that list honest.
10. **Never create a part to make an edit work.** E.g. `remove_header_footer` refuses (`inheritance`) rather
    than add an empty header part. Adding members is allowed only when the op's whole purpose is the added
    member and it is predicted in `members_added` — today only `apply_header_footer_template`, whose new parts
    are copies of the template's parts (never synthesized), each with a V5a template baseline and a
    `template_parts` assertion. A new part's content type must match its relationship type (V1 checks it).
11. **Fills are extracted, never typed.** A template slot gets its value from the target by a declared rule
    (`regex`, `paragraph_after`) or is the user's approved wording (`literal` → the op is `untracked` and the
    plan flags the words). V10 re-extracts every fill from the SOURCE by the same rule, independently.
12. **The template file is an input like the source.** Pinned by md5 in the plan; apply refuses a changed
    template, V10 fails on one. A batch skips input files that are a spec's template (same path or bytes).

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
  (`has_direct_font`, `font_in`, `not_in_revisions`), `sections`, `styles`. A symbol run: any explicit font
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
  slot's runs into the first (it keeps its rPr minus the highlight for highlight slots), text = the fill.
  Transform: `ops/slots.py`; verifier: `verify/template.py` (`slots_of`, `display_of`, `_run_model`).
- **Fill rules.** `literal` (value verbatim), `regex` (`pattern` searched in the display text of every paragraph
  of `source` = `document` | `header.<type>` | `footer.<type>` as shown by each section or by `section`; group 1
  if any groups; ≥1 match, all equal), `paragraph_after` (exactly one paragraph matches `anchor_regex`; value =
  display text of the next non-blank paragraph). `strip` default true; empty → refused. Transform:
  `ops/slots.extract_fill`; verifier: `verify/template.fill_value`.
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
(v0.2.0), and a first-cut window (`gui.py`, Phase 7) exists. Abraham skipped the real-file sweep, live API run and
Word check to start using the window; expect fixes from his first real runs. Abraham asked
(2026-10-13) to finish the prototype without stopping for questions: §14 #3–#5 are decided by the plan's own
proposals and recorded in the plan's status sections.

## Where things are

- `package.py` — zip/rels/sections/repack. `sections()` returns `SectionInfo` with resolved header/footer part
  names (never guessed) and `sect_pr_ordinal` to find the sectPr again.
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
  `merge_fragments` (conflicts, mode, revision-id ranges, geometry). `ops/__init__.py` — registry (`@register`).
- `ops/selectors.py` — transform-side selector resolution, `DEFAULT_PRESERVE_FONTS`, font param parsing.
- `ops/*.py` — `fix_page_numbering`, `replace_text`, `remove_header_footer`, `set_font`, `set_style_font`,
  `set_doc_defaults_font`, `set_paragraph_props`, `set_page_setup`, `apply_header_footer_template`.
- `ops/hf_common.py` — header/footer refs, `effective_refs` (what each section shows), `plan_removal`/
  `apply_removal` (shared by `remove_header_footer` and templating: rels, members, overrides, orphaned media,
  revisions flagged). `ops/slots.py` — slots, display text, `apply_slot`, `expected_block_text`, `extract_fill`.
  `ops/effective.py` — `Resolver` (effective pinnable properties), `theme_fonts`, `text_width`, `rescale_tabs`.
- `apply_header_footer_template` plan: copies keyed by (template part, template width, target width); pins are
  the diff of template-effective vs target-effective (with imported styles); `transform_data` carries pins,
  slots, rels/media maps, docPr start ids, new refs, removal plan. Apply works on a fresh parse of the template.
- `editspec.py` — EditSpec validate → `plan_spec` → `apply_spec` (minimal Phase 1 executor; the Phase 3
  planner grows out of it). `examples/specs/` — the two worked example specs.
- `jobs/` — `OpJob` aliases (`page-numbering` → `fix_page_numbering`, `tracked-replace` → `replace_text`).
- `verify/suite.py` — V1…V10 as a `VerificationRecord`; V6/V7/V9 are filled by `batch.py`
  (`geometry_check()` implements V7's `source | none | template` modes). V5a takes `template_baselines` for
  added parts.
- `verify/reader.py` — `REL_CONTENT_TYPES`: V1's content-type-vs-relationship-type table.
- `verify/template.py` — V10 kinds `hf_template_refs`, `template_parts`, `styles_added` (registered into
  `props.KINDS`); template packages cached by content md5 (re-read every time, so an edited template shows).
- `verify/geometry.py` — `hf_only_copy()` writes a headers/footers-only copy of the template AND of each
  output (body reduced to its section breaks, three pages each, sections nextPage, numbering restarted at 1);
  `compare_template_bands()` pairs their pages by section (map from the spec's `template_sections`) and
  variant — never a "best page" search (a blank variant would stand in for anything) — and checks each
  templated region: blank ↔ blank, else outer edge + shared words, 2 pt window, footer from the page bottom.
- `verify/props.py` — V10: assertion kinds, independent selectors/styles/theme, residual identity.
- `batch.py` — the pipeline; `_process_one` is the per-file worker (must stay top-level and picklable); it
  runs a named job or an EditSpec (`spec=`). `outputs/` holds only files that passed every check, V9 included: an output
  that fails verification is moved to `failed/` (`quarantine_failed_output`) before delivery, and one whose V9 guard
  fails afterwards (drift during the run, a refused in-place write-back) is moved the same way; V9 records where it went
  and `delivered_to` stays empty.
- `render/` — `word_com.py` (DispatchEx only; copies only; Quit the instance you made), `libreoffice.py`.
- `guard.py` — identity, drift, lock file, atomic write.
- `tests/make_fixtures.py` — synthetic specs with the hard cases; `build_rich()` is the op fixture (first/even
  headers, shared footer part, inheriting section, header-only and shared media, theme/explicit/symbol fonts,
  runs inside revisions, an earlier rPrChange). `build_template()` is the template fixture (both slot
  conventions, fragmented placeholders, a style targets lack, a logo, a footer table, 1" margins) and
  `TEMPLATE_FILLS` its fill rules; `examples/templates/00_00_00_TEMPLATE.docx` is its output (regenerate with
  `--template` when it changes). Add a case here when an op grows.
- `editspec.py` — `load_spec` resolves a relative `template` against the spec's folder; `is_template_file`.
- `compiler.py` — the Claude tool-use loop (injectable `client`; tests use a scripted fake). Submission checks:
  `validate_spec`, `untraceable()` over `placed_words()` (add any new op that places text there), dry plan per
  pattern (spec-level codes only). `preview.py` — `make_preview`, `approval_token`. Default model
  `claude-opus-5-5` (`SPEC_SURGEON_MODEL`); the live API path is untested in the container.
- `codegen.py` + `sandbox_runner.py` — Tier B (off by default, `do --allow-codegen`): AST whitelist
  (`ALLOWED_FROM`, `ALLOWED_IMPORT`, `FORBIDDEN_*`), isolated subprocess on copies, `hard_limits` (formatting
  only), the full suite as the test, 3 attempts, outputs only. Never let generated code import `verify/`, write
  files, or run in place; never relax a hard limit to make a generated op pass.
- `advisor.py` — exception record → recommendation or re-compiled spec (pinned to the file's pattern; must pass
  `compiler.check_submission` and plan on that file). Writes `advice/`; never applies anything.
- `gui.py` — the window (`spec-surgeon-gui` / `spec-surgeon gui`). `Session` holds all logic (files, compile,
  preview, run with the approval-token check, Tier B, advise) and is what the tests drive; `build_app` is Tk
  layout only. Tk widgets/variables may only be touched on the main thread: read every value before starting the
  background job (a background read raises "main thread is not in main loop"). Real-window test: Python with Tk +
  a display (in the container: a 3.12 venv under `xvfb-run`; the system 3.13 has no Tk, so it skips there).
- `profile.py` — bounded per-pattern profiles (descriptive only; no op reads them). `planner.py` — `plan_batch`
  dry run + `summarize_plan`. `catalog.py` — `op_catalog()` (+ `COMMON_ASSUMPTIONS` per op — add one for every
  new op) and `editspec_json_schema()`.

## Adding or changing an op (or a job)

Follow README → "Adding an op" (new work goes into `ops/`; jobs are aliases). Every new grammar needs a
planted-defect test proving the verifier catches a wrong implementation of it — ideally a wrong output AND a
wrong implementation shared by plan and apply. Run `py -3 -m pytest -q -s` before and after (72 tests at the
end of Phase 1, 116 at the end of Phase 2, 125 after Phase 3, 135 after Phase 4, 153 after Phase 5, 158 after Phase 6, 166 with the window, 178 with v0.3.0, 189 with the v0.3.1 audit fixes; in the Linux container `python3 -m pytest -q -s`).

## Documentation duty

Any implementation change: update README.md (don't shorten it — add). Dependency change: update
`requirements.txt` and `pyproject.toml` together. Change to the rules above or to the block-text definition:
update this file.

## Environment notes

- Developed in a Linux container with LibreOffice, pandoc, poppler; Word path untested (marked [unproven]).
  The container has no `py` launcher: use `python3`. `pip install -e ".[dev]"` first (pytest is not preinstalled).
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
