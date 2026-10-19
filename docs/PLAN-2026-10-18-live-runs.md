# Spec Surgeon — Implementation plan after the first live runs

Oct 18, 2026 · @Abraham

## Evidence from the two live runs

Both sessions on 2026-10-18 ran the same 12 real Division 21/28 specs against the owner's header/footer template. Every engine check passed every time. Zero files were delivered. In both, the layer that blocked delivery was the v1.1 outcome review (`ordinary_outcomes`, outcome planning, the GOAL check).

| Session | Request | Wall clock | Model cost | Engine result | What blocked delivery |
| --- | --- | --- | --- | --- | --- |
| 14:18 | header/footer from template | 32 min | $7.15 | 12/12 planned, 9 samples PASS | Outcome review never approvable; raw `ValueError` on click |
| 15:21 | header/footer + font (staged by Claude) | 29 min | $7.03 | 12/12 PASS V1–V10 and V6 render | GOAL check failed 12/12; outputs moved to `failed/` |

What else the bundles showed:

- The outcome planner alone cost 18 min / $3.90 and 12 min / $3.33 — about 55% of each session's spend. Twelve and five of its attempts were rejected with `unsupported template fill source`: its schema accepts only `source: "document"`, while the compiler correctly used `footer.default`.
- The GOAL contract disagreed with the recipe three ways: it demanded the whole request when the recipe was stage 1 of 2; it treated the font change in header parts and `styles.xml` as unapproved residual; it required Times New Roman in all four font slots when `set_font` leaves eastAsia alone by design (1,506 failures in one file).
- Recovery made 0 model calls: nine `FileNotFoundError`s on a 278-character `.tmp` path, over Windows' 260 limit.
- The crash log holds repeated access-violation dumps at the moment the batch spawned 8 worker processes. The run survived.
- The output root is inside OneDrive; one run wrote ~70 MB of JSON there. The outcome review was 7.1 MB; one record was 600 KB.
- Real engine gaps, found only because real files were used: filled placeholders keep the template's red colour; list labels keep numbering-definition fonts (Arial, Calibri) after a font change; the preview reports page reflow only as prose.
- What worked: Claude's clarifying question (ALL CAPS vs title case, the tracked title in 21 05 53), its two-stage plan, the transforms, Word rendering, and the visual check's findings were all correct on the real documents.

## Principles for this round

Every item below follows from one of these. If a proposed change doesn't, leave it out.

1. **One specification per edit.** The EditSpec the user approved is the only description of the change. No second model re-derives intent through a second schema. Two specifications written by two models will disagree, and when they do the user pays for both and gets nothing.
2. **Two authorities, not three.** The engine's verifier (V1–V10, V6/V7) answers "did the recipe do exactly what it predicted." The user's approval of the preview answers "is this what I wanted." Nothing else may withhold a verified output.
3. **Evidence over process.** Every change in this round is motivated by something a real run showed. Nothing new ships until it has run on the 12 real specs. Audits of audits, reliability docs and contract layers are not progress; delivered files are.
4. **Refuse early and cheaply.** Refusing is still better than guessing on a spec that gets stamped. But a refusal belongs at plan time, in seconds, not after 30 minutes and $7.
5. **The model judges per document; code applies.** Reading a spec, binding its section number and title, spotting a red footer or a Calibri label — that is model work and it was done well. Applying a header part, rescaling tabs, rewriting `rFonts` — that is deterministic code and it must stay that way.
6. **Everything the user sees fits in one sitting.** Records, reviews, reports and the window's messages are for a person. A 7 MB review is not a review.
7. **The engine is the asset.** Predict-first planning, the independent verifier, byte-identical repack and guarded delivery are what make this more than a wrapper. Protect them; stop building things in front of them.

## Workstream A (P0) — The delivery gate

**Goal.** No output that passes the engine's verification is withheld by the outcome review. Remove the GOAL check from the delivery path, or reduce it to an advisory line in the record. Remove the outcome-planning step from the request path.

**Why.** Both live sessions were lost here and nowhere else. The outcome review is a second compile of the same request by a second model under a narrower schema (fill sources, font slots, staging), and its disagreements with the approved recipe are structural, not bugs to patch one at a time. It also consumed about 55% of each session's cost and 40% of its wall clock, and asked the user to approve ~3,500 per-paragraph "targets" he could not read. The verifier already proves the recipe did what it predicted; the preview and the user's approval already establish what was wanted.

**Expected outcome.**

- A run whose files pass V1–V10 (and V6/V7 when rendering is on) delivers. `goal_contract` / `goal_bindings` are no longer required by `batch.py`, `chain.py` or the window's Run.
- `--outcome-review` / `--outcome-approval` either disappear or become opt-in flags whose result is recorded, never enforced.
- If any part of the planner is kept (for example, to list risks), it runs after approval, in the background, with the compiler's own schema (`compiler.py` fill rules, `set_font` slot semantics), and its output is a report section. It never blocks and never asks for approval.
- Request cost and wall clock roughly halve with no other change. Re-running the 15:21 session delivers 12 files from the same recipe.

**Acceptance.** Re-run both 2026-10-18 sessions from their saved recipes. All 12 files land in `outputs/`. The record for each shows V1–V10 and V6 as before and no GOAL failure. A test pins that a passing verification suite is sufficient for delivery.

## Workstream B (P0) — Environment bugs on the real machine

Three problems that only appeared in the installed Windows app. None is deep; all three make the app unreliable on Abraham's actual computer.

**B1 — Paths over 260 characters.**

- *Goal:* nothing the app writes can exceed Windows' `MAX_PATH`, whatever folder the user chose.
- *Why:* recovery wrote `…/conversations/<64 hex>/context/<64 hex>.tmp` — 278 characters. The directory (209) was created, the file was not, and recovery died with 0 model calls and nine `FileNotFoundError`s. The user saw "0 of 12 ready for review" with no explanation.
- *Expected outcome:* artifact and conversation ids on disk shortened to 12–16 characters (keep the full digest inside the file); the frozen build declares `longPathAware` in its manifest; a startup check warns when the output root is deep enough to put generated paths near the limit. A test builds the longest path the app can generate from a 120-character root and asserts it stays under 240.

**B2 — Output inside a synced folder.**

- *Goal:* the default output root is local, and a synced root is a visible warning, not a silent risk.
- *Why:* the current root is under `OneDrive - Ehvert`. One session wrote ~70 MB of JSON there, sync clients lock and rename files mid-write, and long paths get longer under OneDrive's folder names.
- *Expected outcome:* default output under `%LOCALAPPDATA%\Spec Surgeon\runs` (or beside the specs when the user chooses, with a one-line warning if that folder is synced); bundle and context sizes capped (see Workstream D) so a run is tens of kilobytes, not tens of megabytes.

**B3 — Access violations at worker spawn.**

- *Goal:* a batch run never produces a crash dump.
- *Why:* `crash-20261018-151939-23376.log` holds repeated `Windows fatal exception: access violation` traces at `ProcessPoolExecutor.submit` inside `batch._transform_and_verify`, when 8 workers were spawned from the frozen exe. The process survived, which suggests handled first-chance exceptions, but it is unexplained — and the 12 files transformed and verified in 2 seconds, so the pool bought nothing.
- *Expected outcome:* small batches (under ~25 files) run on threads or serially by default; the process pool is opt-in above that; the cause of the dumps is identified or the pool is removed from the frozen build. No crash log after a run on the 12 real files.

## Workstream C (P1) — Engine gaps the real files exposed

These are the first findings from the real-file corpus. They are the kind of thing the synthetic fixtures could never show, and each one would have produced a wrong deliverable.

**C1 — Placeholder styling survives the fill.**

- *Goal:* a filled slot looks like the text around it, not like a placeholder.
- *Why:* the template marks its two slots red on yellow. `apply_header_footer_template` removes the highlight but pins the run's colour (`RUN_KEYS` includes `color`), so all 12 footers came out with red titles and numbers. The visual check caught it; a reviewer would have rejected the submittal.
- *Expected outcome:* on fill, direct run formatting that distinguishes the slot from its paragraph's other runs (colour, highlight, and by default shading) is dropped and the slot inherits the look of the non-slot runs; a `slot_formatting: keep | clear` parameter (default `clear`) for templates whose placeholder styling is intentional. The verifier's `template_parts` assertion re-derives the same rule.

**C2 — List labels keep their own fonts.**

- *Goal:* "match the template's font" changes what the reader sees, including A./B./1. labels.
- *Why:* `set_font` skips `numbering.xml` level fonts by design. After the font run, labels stayed Arial in 21 05 00 and 21 31 13 and Calibri in 28 46 21. The plan declared it; the visual check flagged it; the user still would have shipped mixed fonts.
- *Expected outcome:* `set_font` and `set_style_font` gain `numbering_levels: true` (default when the selection is the whole document), rewriting `w:lvl/w:rPr/w:rFonts` for lists the document uses, with the symbol-font exclusion kept so bullets don't change glyphs. The effective-font assertion covers label runs.

**C3 — One definition of "the font changed."**

- *Goal:* the op, the EditSpec assertion and every report agree on which font slots a font change covers.
- *Why:* `set_font` sets ascii/hAnsi/cs and leaves eastAsia; the GOAL contract demanded all four and failed 1,506 runs on a slot that renders nothing in these documents.
- *Expected outcome:* one documented rule (ascii, hAnsi, cs; eastAsia only with `east_asia: true`), used by the op, the V10 assertion and any report. A slot mismatch is one line with a count, never one line per run.

**C4 — Page-count deltas in the preview.**

- *Goal:* the user sees which files gain or lose pages before approving.
- *Why:* the owner's footer is five lines in a one-inch margin. Word pushes body text up; four files grew. Today that lives only in the visual check's prose.
- *Expected outcome:* when rendering is on, the preview table has a pages-before / pages-after column per file, computed from the PDFs the engine already makes; a grown file is flagged, not failed.

**C5 — Matching tolerates ordinary variation.**

- *Goal:* the common variations in real spec files don't turn into exceptions.
- *Why:* the shipped match recipe is case-sensitive on `SECTION` and assumes six-digit numbers; `Section 21 13 13.13 - Title` on one line was set aside in my sandbox. Real folders mix these.
- *Expected outcome:* fill rules and selectors accept `case_insensitive` and flexible whitespace, and the section-number pattern allows the fourth level and `08-71-00` / `087100` forms; refusals name the first line of the file they couldn't read.

## Workstream D (P1) — Records, reviews and bundles a person can read

**Goal.** Every file the app writes for a human — record, preview, set-aside report, review, diagnostics bundle — can be read in one sitting, and its size is bounded regardless of how many paragraphs a document has.

**Why.** The second session produced a 7.1 MB `outcome-review.json` with ~293 "targets" per document, a 600 KB record repeating one eastAsia complaint 1,506 times, a 929 KB batch report, a 616 KB set-aside report, 5 MB context files per recovery conversation, and a 14 MB bundle. The diagnostics bundle's own README warns that it quotes document text, so size is also a confidentiality problem. None of this helped anyone decide anything; the one useful sentence in each was buried.

**Expected outcome.**

- Findings are grouped by identity: one line, a count, and up to three examples ("1,506 runs: eastAsia slot is Calibri; e.g. paragraph 0 run 0 …"). The full list is available on request, never inline.
- Reviews and previews summarize per structure pattern and per file as counts, with a sample; nothing enumerates paragraphs.
- Context archives store the catalog by reference (a hash), not a copy per conversation. A recovery session's folder is kilobytes, not 50 MB.
- Hard caps: a record under 100 KB, a report under 500 KB, a bundle under 5 MB; a truncation note says what was cut and where the rest is.
- The set-aside report's first line answers the question a person asks: which check failed, in one sentence, and whether the output in `failed/` is usable.

**Acceptance.** Re-run the 15:21 session with Workstream A done and the eastAsia mismatch reintroduced on purpose: the 28 46 21 record is one short paragraph about it, and the bundle is under 5 MB.

## Workstream E (P1) — The model binds values per file; the engine applies literals

**Goal.** Template fills (and, later, other per-document values) are bound by reading each document, not by a regex that must generalize across the batch. The engine applies the bound literal and verifies that it occurs in the source where the binding says it does.

**Why.** The compile-once-per-batch design forces the model to express "this spec's title" as a rule — `^SECTION\s+(\d{2}…)` — and the rule is where brittleness lives: case, a fourth number level, a title on the same line, a title that exists only in the old footer. The first live run needed a question and a footer-reading rule to get title case right; the planner could not express that at all and chose ALL CAPS. Meanwhile the model had already read every document and knew every title. A per-file binding is cheaper, more accurate, and exactly what a reviewer would do.

**Expected outcome.**

- The EditSpec gains a `bindings` section: per source file, slot name → literal value plus where it was read (part, paragraph). Rules remain as a fallback and as the way to bind files added later.
- Bindings are produced by the compile conversation (it already inspects the files) or by a cheap, fast model pass that reads page one of each document and returns the section number and title; the engine refuses a binding whose literal does not occur in the stated location.
- The preview shows the binding table: file, number, title, source. The user's approval of that table is the approval of the fills. Fixing one wrong title is editing one cell, not rewriting a regex.
- Tool paper cuts fixed on the way: `read_xml_page`, `run_analysis`, `show_part_text` and `list_parts` accept `template` as a file alias; the template is a readable file in every tool that reads files. (Four model turns were lost to this in each session.)

**Acceptance.** The 14:18 request runs without the clarifying question's regex: the binding table lists 12 title-case titles read from the old footers, 21 05 53 shows its footer title with the tracked body title as a note, and the engine refuses a deliberately wrong binding.

## Workstream F (P2) — Cost and latency

**Goal.** A 12-file header/footer request reaches an approvable preview in under 5 minutes and under $2. Today it is about 15 minutes and $3.50 before the planner, and 30 minutes and $7 with it.

**Why.** The machine-readable op catalog is 483 KB — about 120,000 tokens — and it rides in every system prompt. The first session read 2.7 million cached tokens across 16 calls, about 168,000 per call; single calls took 5 to 63 seconds; a conversation that views more than two pages overflows into a new "context epoch." Caching keeps the dollar cost tolerable, but the latency and the context pressure are paid on every request and grow with every op added. Workstream A removes the planner's share outright; this workstream addresses the rest.

**Expected outcome.**

- The system prompt carries an op index: name and one line each, roughly 2,000 tokens. A `describe_op` tool returns one op's full schema, examples and verification notes on demand. The compiler loads only the ops it is considering.
- Routine inspections that don't need Opus-class judgment — profile summaries, page-one extraction for bindings, listing styles — use a smaller, faster model, with the choice recorded per call.
- Visual check sends page one and the first changed page per sample, as now, but at the resolution the check needs; its cost and time are reported alongside the compile's in the preview.
- The preview shows elapsed time, calls and cost as they accrue, and offers cancel.

**Acceptance.** The 14:18 request, re-run after Workstreams A and F: preview ready in under 5 minutes, model cost under $2, no context epoch created, same EditSpec produced.

## Workstream G (P2) — The window tells the truth about its state

**Goal.** The user is never shown a raw exception, never sees a button that cannot work, and always knows what the app is doing and why the next step is unavailable.

**Why.** In the first session the user sat through a 23-minute "Reply" with no indication that 18 of those minutes were a planner, reached step 4, found Run unavailable with no reason given, clicked *Resolve remaining files* with zero exceptions to resolve, and got `ValueError: Approve the exact outcomes and per-file targets shown in the current review.` as the app's answer. In the second, 12 verified files went to `failed/` with a set-aside report whose guidance paragraph says the failure "is not a problem with your file" and nothing about what to do.

**Expected outcome.**

- Every disabled control carries its reason as a tooltip or inline note ("Run needs an approved preview"; "Nothing to resolve: 0 exceptions"). A control with nothing to do is hidden or disabled, never clickable into an error.
- Long steps show what is running, elapsed time, model calls and cost so far, and a cancel that stops cleanly at the next call boundary.
- An unexpected exception becomes one plain sentence plus a "Send diagnostics" button; the traceback goes to the log only.
- A failed verification tells the user which check failed in a sentence, whether `failed/` holds a usable file, and the one action that most likely fixes it.
- Approval is one decision: the preview. Acknowledging the visual check and approving an outcome review are no longer separate gates.

**Acceptance.** Walk the first session's path with the planner still present behind a flag: no raw error can be reached from any button; every disabled button explains itself; the progress panel shows time and cost during the compile.

## Workstream H (P2) — A real-file corpus and a repository people can read

**H1 — The eval corpus.**

- *Goal:* every workstream above is accepted against real specifications, and every future change runs against them before release.
- *Why:* the synthetic fixtures were careful and still missed Arial list definitions, red-on-yellow placeholders, Spanish style ids in the template, hidden first-page headers with stale text, and a tracked title. Each of those was found by a $7 session instead of a 15-second test. "No matter what the specs look like" is a claim earned by a test set.
- *Expected outcome:* the 12 Division 21/28 specs and the owner's template (or anonymized derivatives with the same structures) become a fixture family under `tests/real/`, with the two saved recipes and their expected outcomes. Each new real-file surprise is added as it is found. The suite runs in CI with LibreOffice and, on the Windows runner, with Word.

**H2 — The docs diet.**

- *Goal:* a new engineer or a fresh coding agent can orient in ten minutes.
- *Why:* `README.md` is 515 KB and `CLAUDE.md` is 255 KB. Both are changelogs that accreted over five days of agent sessions. Nobody can read them; every agent session loads a quarter-megabyte of history before touching code; and the parts that matter — the hard rules, the block-text definition, the verifier's independence — are buried in release notes.
- *Expected outcome:* `README.md` under 3,000 words: what it is, install, the four-step flow, the verification record, limits, where to go next. `CLAUDE.md` under 200 lines: the invariants only (predict first, independent verifier, repack by substitution, never write back blind, one specification per edit), the layout, how to add an op, how to run the real-file suite. Release history and design notes move to `docs/` and are linked, not inlined. Audit documents are archived.

**H3 — Nothing ships unexercised.**

- *Goal:* no feature is marked done until it has run on the real corpus with a live model.
- *Why:* 76,000 lines and eleven releases were built with every Claude path "[unproven]" and no real file. The first two live hours found more than the previous five days of audits.
- *Expected outcome:* a release checklist item, enforced in CI where possible: the real-file suite passes; one live `do` request has been run on the release candidate and its bundle attached to the release.

## Sequencing, acceptance and what not to do

The order is by what unblocks delivery, not by size. Phases 1 and 2 are small; they are first because nothing can be evaluated on real files until a run can deliver and recovery can start.

1. **Phase 1 — A and B (hours, not days).** Gate off, long paths fixed, output root local, pool behind a flag. *Gate:* both 2026-10-18 sessions re-run from their saved recipes and deliver 12 files each. Everything after this is measured on those re-runs.
2. **Phase 2 — H1 first cut.** The 12 specs and template become fixtures with the two recipes as expected outcomes. *Gate:* the suite runs green locally with LibreOffice.
3. **Phase 3 — C and D.** Placeholder styling, numbering-level fonts, one font-slot rule, page deltas, variation tolerance; grouped findings and size caps. *Gate:* the header/footer run shows black fills and no page-growth surprise; the font run shows Times New Roman labels; no artifact over its cap.
4. **Phase 4 — E.** Per-file bindings in the EditSpec and the preview; tool aliases. *Gate:* the title-case fill needs no regex and no clarifying question.
5. **Phase 5 — F and G.** Op index plus `describe_op`, cheaper model for inspections, progress and cancel, honest disabled states. *Gate:* under 5 minutes and under $2 to an approvable preview; no reachable raw error.
6. **Phase 6 — H2 and H3.** Docs cut down; release checklist with a live run.

**Definition of done for the round.** Abraham runs the two requests from 2026-10-18 on the same 12 files and the same template and gets 24 delivered, verified, black-footered, Times-New-Roman-labelled documents in under 10 minutes and under $4 total, with records he can read and nothing in `failed/`.

**Do not, in this round:**

- Add operations, workflows, review layers, planners, critics or recovery modes. The catalog has 50 ops and the real files have exercised two of them.
- Add documentation beyond what H2 keeps. A change is described in its test and its one-line release note.
- Mark anything done on fixtures alone. The acceptance for every item names a real-file run.
- Re-enable a delivery gate "just for safety." The verifier is the safety; the preview is the consent. A third gate has cost two sessions and delivered nothing.

## Status — agents update this at the end of every session

One row per item. Change Status only when the gate in that item's Acceptance has been met on the real files; "coded" is not "done". Add a dated one-line note per session: what changed, what is tested, what still needs Abraham's machine (Word, API key, the 12 real specs).

| Item | Status | Owner / branch | Last note |
| --- | --- | --- | --- |
| A — delivery gate off | implemented; live replay pending | codex/live-runs-follow-through | 2026-10-19: Ordinary CLI/desktop/batch/chain use preview consent + engine checks; legacy goal metadata is advisory. Explicit profiles/governed recovery retain their own authority. |
| B1 — long paths | implemented; Windows replay pending | codex/live-runs-follow-through | 2026-10-19: 16-character disk IDs preserve full hashes, immutable archives migrate/read legacy paths, frozen manifest is longPathAware; deep roots warn. |
| B2 — output root local | implemented; Windows replay pending | codex/live-runs-follow-through | 2026-10-19: Default runs use persistent local application data; synced/deep roots show warnings. PR review regression tests keep Linux/macOS deliverables out of disposable caches. |
| B3 — worker spawn | implemented; Windows replay pending | codex/live-runs-follow-through | 2026-10-19: Threads default, serial/process modes explicit, frozen apps never spawn transform pools. Independent XSD caches isolated per worker. |
| C1 — placeholder styling | implemented; live replay pending | codex/live-runs-follow-through | 2026-10-19: clear default removes slot-only color/highlight/shading before effective style pinning; keep explicit. Independent verifier and decorated shipped-template tamper tests. |
| C2 — numbering-level fonts | implemented; Word replay pending | codex/live-runs-follow-through | 2026-10-19: Whole-document font changes include used body list definitions/overrides; symbols/pictures and sizes preserved; whole-member independent verification. PR review pins the shipped Arial recipe's explicit numbering opt-out in both copies and its postcondition. |
| C3 — one font-slot rule | implemented; live replay pending | codex/live-runs-follow-through | 2026-10-19: Font shorthand/postconditions use ascii/hAnsi/cs; eastAsia requires an explicit request. Font failures grouped with counts. |
| C4 — page-count deltas | implemented for rendered samples; live replay pending | codex/live-runs-follow-through | 2026-10-19: Before/after PDF counts shown in preview rows and text; reflow warns. Unsampled files explicitly show unavailable counts. |
| C5 — matching tolerance | implemented; live replay pending | codex/live-runs-follow-through | 2026-10-19: Explicit case-insensitive/flexible selector and fill matching; exact captured source characters preserved; CSI examples accept spaced/hyphenated/compact/four-level numbers. |
| D — readable records and caps | implemented; live replay pending | codex/live-runs-follow-through | 2026-10-19: Human records <100 KB, reports/previews <500 KB, bundles <5 MB, counts + three examples; complete machine authority retained separately and referenced. PR review fixes Windows newline expansion at byte-capped writers and tests copied/bundled catalogs with original results removed. |
| E — per-file bindings, tool aliases | implemented; live replay pending | codex/live-runs-follow-through | 2026-10-19: Source-path/MD5/part/paragraph bound literal fills verified independently, editable preview table requires re-preview, every inspection tool accepts template alias. Later-stage bindings refuse early. |
| F — op index, cheaper inspections | implemented in part; live timing pending | codex/live-runs-follow-through | 2026-10-19: Compact operation index + describe_op, elapsed/calls/cost and cooperative cancel. Catalogs now live within their owning results with relative references; recovery retains shared hash deduplication. Legacy references and unchanged signed archives tested. Routine inspections remain deterministic tools. |
| G — window states and errors | implemented; live walkthrough pending | codex/conversational-desktop | 2026-10-19: Default DOCX/template/chat UI keeps v1.2.1 single-preview approval, verified copies, cancellation, progress and page deltas; adds saved conversations and explicit attachment scope. PR review fixes paused-work resumption and repeated partial exports, including restart. Integration regression tests and LibreOffice previews checked; private corpus, live API and installed Word walkthrough remain pending. |
| H1 — real-file corpus | harness ready; private corpus unavailable | codex/live-runs-follow-through | 2026-10-19: External pinned 12-file/two-recipe replay in tests/real; set SPEC_SURGEON_REAL_CORPUS and optionally SPEC_SURGEON_REAL_RENDERER=word. Missing private corpus skips explicitly. |
| H2 — docs diet | user-managed | codex/live-runs-follow-through | 2026-10-19: README.md and CLAUDE.md left to the cleanup the user reported; this PR does not redo it. |
| H3 — release checklist | checklist added; live evidence pending | v1.3.0 | 2026-10-19: Prepared v1.3.0 from latest master including PR #43; its Linux/Windows and installed-app CI passed, and tagged release CI gates publication. Private-corpus, owner-machine Word and live-model acceptance remain pending and are disclosed in the release notes. |

2026-10-19: The ordinary outcome planner is removed from the request/delivery path, as authorized by the user's instruction to handle this plan. Independent engine verification and source-bound preview approval remain required. Explicit office-profile and governed custom recovery workflows retain their separately requested contracts. No new outcome planner was added.

Still needed for real-file acceptance: the private 12 specifications, owner's template and saved recipes; native Windows/Word rendering and crash-log checks; a live model run measuring time and cost. These inputs and credentials are absent from this checkout. No workstream is certified against those live gates by the regression tests alone. Full machine JSON and immutable provider histories remain complete; byte caps apply to the human views and diagnostics bundles, with references to omitted evidence. The corpus harness is ready for those files, and the release checklist requires the resulting evidence.
