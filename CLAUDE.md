# CLAUDE.md — Spec Surgeon

Working notes for any Claude session touching this repository. Read before editing.

## What this is

A deterministic engine for surgical .docx edits (Tier 1 of the three-tier design), plus a thin optional
model layer. Since NL-editor Phase 1 the engine is an **operation library** (`ops/`) executed through
**EditSpecs** (`editspec.py`, `spec-surgeon run-spec`); the v0.1.0 jobs are thin aliases over two of the ops. Phase 2
added the header/footer templating op (`apply_header_footer_template`), the first op that adds members; v0.4.0 let
it take a **template spec** (an ordinary spec, e.g. the architect's: `slot_markers: ["reference"]`) and gave the three
font ops **sizes**. The method it implements is the `docx-surgical-edit` skill bundled at
`src/spec_surgeon/skill/` — read `SKILL.md` and `references/verification-checklist.md` there before
changing any job or check. Phase 8 turned the compile into a conversation (`conversation.py`), added Claude's visual
check of the preview (`vision.py`, advisory), custom transforms as an offer, and a personal library (`library.py`);
every Claude call goes through `llm.py`. The user (Abraham) is a fire sprinkler designer; these files go to clients;
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
9. **Overlapping ops are refused, never ordered by guess.** `merge_fragments` raises `conflict` when two ops
   touch the same block's text, the same property family of the same target, or anything in a member another
   op removes/restructures. Ops declare what they touch in `touches`; keep that list honest — including a
   `(part, "scope", "verify")` touch for every part an op's V10 assertion re-resolves its selection over
   (`set_font`, `set_paragraph_props`: every part in scope, changed or not), so an op that removes that part is a
   plan-time conflict rather than a V10 failure after apply. Two `verify` touches of one member never conflict.
10. **Never create a part to make an edit work.** E.g. `remove_header_footer` refuses (`inheritance`) rather
    than add an empty header part. Adding members is allowed only when the op's whole purpose is the added
    member and it is predicted in `members_added` — today only `apply_header_footer_template`, whose new parts
    are copies of the template's parts (never synthesized), each with a V5a template baseline and a
    `template_parts` assertion. A new part's content type must match its relationship type (V1 checks it).
11. **Fills are extracted, never typed.** A template slot gets its value from the target by a declared rule
    (`regex`, `paragraph_after`) or is the user's approved wording (`literal` → the op is `untracked` and the
    plan flags the words). V10 re-extracts every fill from the SOURCE by the same rule, independently. A
    reference slot (template spec) is the template's OWN value: the same rule run on the TEMPLATE, or the rule's
    `in_template` text — V10 re-derives it the same way. Never convert a value (case, spacing, "087100" → "08 71
    00"): a value the template shows in another form is refused (`template-value-left`), not normalised.
12. **The template file is an input like the source.** Pinned by md5 in the plan; apply refuses a changed
    template, V10 fails on one; the preview's approval token (`preview.approval_token`) hashes every template
    the spec reads (path, size, mtime, content) beside the batch files, so a template edited after the preview
    refuses to run until it is previewed again. A batch skips input files that are a spec's template (same path
    or bytes).
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
    and advised on again.
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
    anything is written).
18. **One call site** (Phase 8). Every Claude call goes through `llm.call` (streamed, `MAX_TOKENS` 64000, effort
    `high`, `fallbacks="default"`, 5-minute cache). Callers resolve the client when the call is made, through
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
    in every loaded `spec_surgeon` module (`pin_model`).
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
  left alone; `direct` disagreeing → `conflict-property`; ambiguous → `toggle-ambiguous`; `caps`/`smallCaps` reaching a
  symbol run → `conflict-property`. Transform: `ops/effective.PropResolver` (+ `run_elements`/`para_elements`; an
  `override` simulates the style change), `ops/set_properties.py`; verifier: `verify/props._v_run_prop`/`_v_para_prop`/
  `_v_gets_run`/`_v_gets_para`/`_v_style_gets`/`_v_expected_attrs`/`_properties`. `tests/test_set_properties.py::
  test_transform_and_verifier_property_resolvers_agree` compares them on every run, mark and paragraph of four fixtures;
  the planted-defect tests there share a wrong pair table, "already" rule, symbol rule, certainty check and on/off writer
  between plan and apply.

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
  `merge_fragments` (conflicts, mode, revision-id ranges, geometry). `ops/__init__.py` — registry (`@register`).
- `ops/selectors.py` — transform-side selector resolution, `DEFAULT_PRESERVE_FONTS`, font param parsing.
- `ops/*.py` — `fix_page_numbering`, `replace_text`, `remove_header_footer`, `set_font`, `set_style_font`, `set_properties`,
  `set_doc_defaults_font`, `set_paragraph_props`, `set_page_setup`, `apply_header_footer_template`.
- `ops/hf_common.py` — header/footer refs, `effective_refs` (what each section shows), `plan_removal`/
  `apply_removal` (shared by `remove_header_footer` and templating: rels, members, overrides, orphaned media,
  revisions flagged). `ops/slots.py` — slots, display text, `apply_slot`, `expected_block_text`, `extract_fill`;
  reference slots (`located_text`, `bounded`, `reference_values`, `reference_slots`), `carve`, `mark_slots` (⟦ ⟧ for notes).
  `ops/effective.py` — `Resolver` (effective pinnable properties), `theme_fonts`, `text_width`, `rescale_tabs`;
  `PropResolver` (set_properties' effective value with provenance, with an `override` that simulates a style change).
- `apply_header_footer_template` plan: copies keyed by (template part, template width, target width); pins are
  the diff of template-effective vs target-effective (with imported styles); `transform_data` carries pins,
  slots, rels/media maps, docPr start ids, new refs, removal plan. Apply works on a fresh parse of the template.
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
- `verify/explain.py` — why a check failed, in specifics, from the check's own data and the record's predictions
  (`failure_details`, `text_change` word diffs with ⇥ ⍽ ⏎ made visible); `VerificationRecord.failures()` and the
  record's "WHY IT FAILED" section use it. It is verifier code: the independence rule applies to it too.
- `batch.py` — the pipeline; `_process_one` is the per-file worker (must stay top-level and picklable); it
  runs a named job or an EditSpec (`spec=`). `outputs/` holds only files that passed every check, V9 included: an output
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
  `review.sample_files` an int 1–3 (not a bool), `applies_to.pattern_ids` a list of strings, `applies_to.or` only
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
  `stop_problem`, `Usage`, `PRICES`, `client()` (`ClaudeUnavailable` without the SDK); diagnostics: `is_api_error`,
  `api_error_text`, `failure_text`, and the per-call records `response_entry` (stop reason, serving model, usage,
  seconds, request id, content via `block_dict`), `results_entry`, `error_entry`, `transcript_content` (total, no
  image data).
- `conversation.py` — `Conversation` (start/reply/choice/note/brief/save/compile_result), `Turn`, `Choice`,
  `check_pairing`, `clean_wording` (rules 13–14). At most 12 calls per turn; two rejections in one user turn end
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
  `VIEW_BUDGET_TOKENS` for `view_pages` (60,000: about 20 letter pages — `page_tokens()` 2,714 a letter page at 150 dpi,
  22 one at a time, 21 in calls of three; 18 pages of 11×17 at 117 dpi, 3,312 each; `page_dpi` is the one dpi rule
  `rasterize_pages` uses; a test derives the comment's figures).
- `library.py` — recipes (`recipes/<slug>.json` + `.meta.json`) and operations (`operations/<slug>/op.py` +
  `manifest.json`) under `config_dir()/library` (rule 17). `other_kind_holding(kind, slug)`; `_check_free` refuses the
  other kind's name even with `replace` ("… cannot take that name, not even with replace …").
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
  original spec's `request` — `_respec_errors(…, request)`; must pass `compiler.check_submission` and plan on that
  file). Writes `advice/`; never applies anything. `ADVISOR_TOOLS` is an
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
  `library`/`save_to_library`, `Var` values, `request`/`reply`/`chat`/`preview_chat`/`choices`, `wait()`; never
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
  to lambdas; read every widget value before the job starts; never touch a widget from the job.
- `updater.py` — GitHub release check / verified download / detached Setup launch (no Qt; every network and
  process call injectable). `settings.py` — `settings.json` (+ `config_dir`/`data_dir`/`updates_dir`/`logs_dir`;
  `SPEC_SURGEON_CONFIG_DIR` moves them). `winapp.py` — app mutex, AppUserModelID, message box (ctypes; no-ops off
  Windows). `credentials.py` — the API key in Windows Credential Manager (`win32cred`).
- `profile.py` — bounded per-pattern profiles (descriptive only; no op reads them), incl. `fonts.body_text`;
  `template_spec_summary()` is `read_template`'s `template_spec`. `planner.py` — `plan_batch` dry run +
  `summarize_plan`. `catalog.py` — `op_catalog()` (+ `COMMON_ASSUMPTIONS` per op — add one for every new op;
  `REFERENCE_FILLS`, the CSI number/title rules for template specs) and `editspec_json_schema()`.

## Adding or changing an op (or a job)

Follow README → "Adding an op" (new work goes into `ops/`; jobs are aliases). Every new grammar needs a
planted-defect test proving the verifier catches a wrong implementation of it — ideally a wrong output AND a
wrong implementation shared by plan and apply. Run `py -3 -m pytest -q -s` before and after (72 tests at the
end of Phase 1, 116 at the end of Phase 2, 125 after Phase 3, 135 after Phase 4, 153 after Phase 5, 158 after Phase 6, 166 with the window, 178 with v0.3.0, 189 with the v0.3.1 audit fixes, 252 with v0.4.0, 316 with template specs and font sizes, 324 with the 2026-10-15 sweep fixes, 344 with the fixes from the external audit of v0.4.0, 356 with the 2026-10-15 GUI rework, license and About, 395 with diagnostics, 410 with
Phase 8 (the conversation, visual check, library: 66 tests on top of 344, on its own branch), 481 with the two
merged and the merge's own tests, 685 with the Phase 8 review fixes (`tests/test_phase8_fixes*.py`), 709 with `set_properties`
(`tests/test_set_properties.py`) — all pass but
the Windows-only Credential Manager round trip, which skips elsewhere; in the Linux container `python3 -m pytest -q -s`).

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
  (CI sets it — a broken Qt install must fail, not skip). `python3 packaging/gui_screenshot.py` regenerates
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

## Phase 8: the conversation, Claude's visual check, custom transforms as an offer, your library

Decided with Abraham on 2026-10-15 (the plan's "Phase 8 status" records every decision and what was cut):
don't give up on the user (a conversation with choices instead of a dead end; replies from the Describe and Preview
steps and the terminal; suggested wording counts only when the user picks the choice showing it); the "Allow
AI-written transforms" checkbox is replaced by an offer at the point of need; Claude looks at rendered sample pages
only (cost), and sample pages may go to Anthropic; custom operations write edited copies only; a local library of
named recipes and operations (sharing later); a 64,000-token output cap, hence streaming. Decided by me (Abraham
delegated): `ask`/`agent` and `agent.py` retired (`SKILL_DIR` moved to `spec_surgeon/__init__.py`); the method rules
unchanged (no accept/reject of tracked changes, no docProps edits, no trackRevisions toggle); `set_properties` and a
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
  `italic` and `color`; `examples/specs/bold-part-headings.json` is the worked example. The verifier's change-marker
  tails of `run_fonts` and `paragraph_props` became `_check_rpr_change`/`_check_ppr_change` (shared with `properties`;
  the same checks, byte for byte).
