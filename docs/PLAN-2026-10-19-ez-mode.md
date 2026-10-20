# Spec Surgeon — Implementation plan: ez mode

Oct 19, 2026 · @Abraham (direction) · agent sessions execute

## Where this round starts

PR #43 made the default desktop one conversation: attach Word documents, optionally a template, type the request,
review a plan and before/after pages, create verified copies. It did so by adding a coordinator and a window on top of
the old product rather than replacing it. The review of that PR found:

| What | Figure |
| --- | --- |
| Lines added vs. removed | 2,421 vs. 213 |
| Legacy window and dialogs still shipped | about 6,250 lines across 14 UI modules, renamed `build_legacy_window` |
| Operations with a plain-English description in the plan view | 13 of 51 |
| Conversation modes in the engine | two; the old one (library, memory, neighbor files) is still the default |

The owner's definition of done: a person completes a real document task without learning a single Spec Surgeon concept.
The user's inputs are documents, one optional template and chat. Nothing else is visible.

## Principles for this round

1. **One intent, one plan per layout, one verification per file.** A batch may mix projects with different structures.
   The user's sentence is one intent. The planner resolves it into a specification per structural layout, the batch
   routes each file to exactly one specification, and the independent verifier checks every delivered file as before.
   This keeps "one specification per edit" (CLAUDE.md invariant 7): every file still has exactly one authority.
2. **Delete, don't rename.** A surface the user must not see is removed from the package, the installer and the tests.
   "Legacy", "advanced" and "hidden" modes are not allowed.
3. **Refuse early, with a remedy.** A file the engine cannot handle for the requested edit is set aside at attach time
   or plan time, in one sentence that names the file, the reason and what the user can do. Never after a paid investigation.
4. **Never fix what we were not asked to fix.** Typed numbers, direct formatting, whitespace and pending revisions are
   normalized only when the user accepts an explicitly offered first stage. (Owner decision 2026-10-19: always explicit.)
5. **Readable by construction.** Every operation describes its own parameters in a sentence; units are inches and points;
   selectors are "paragraphs in the Normal style", never field dumps. The user-facing message is separate from the record.
6. **The verifier stays independent.** Nothing in this round adds code to `verify/` that imports from the transform side.
7. **Evidence over process.** Each item ends with a test on synthetic fixtures that includes dirty files; the owner's
   12-spec corpus, a live model run and Word rendering remain the acceptance gate and are run on the owner's machine.

Decisions recorded 2026-10-19: office profiles and spec QA audits remain command-line features; their Qt dialogs are
removed; the paragraph role classifier in `structure.py` is promoted into ordinary planning.

Decisions recorded 2026-10-20 (owner, PR #45): the window has no spending ceiling (every call gets the model's full
output at high effort; work pauses every 48 calls; **Stop now** cuts off the running call; transient failures are
re-sent); pictures and tables may be attached explicitly for the edit to use, never discovered.

## Workstream J (P0) — One product

**Goal.** The package contains one desktop window, one conversation mode and one coordinator that owns the state machine.

**Expected outcome.**

- `ui/app.py` MainWindow, `pages.py`, `chat.py`, `campaign_dialog.py`, `office_profiles_dialog.py`, `workflow_dialog.py`,
  `operation_browser.py`, `qa_dialog.py`, `project_evidence_dialog.py`, `resource_controls.py`, `settings_dialog.py`,
  `plan_text.py`, `rows.py`, `models.py` and their tests are gone. A slim `ui/app.py` keeps `qt_app`, `app_icon`,
  `Prompts`, `RecordingPrompts`, release notes lookup, the exception hook, the crash notice and `main`.
- The conversation has one mode: no library tools, no project memory, no neighboring input discovery, choice kinds
  limited to clarify, alternative, rephrase and capitalization. The `simple_conversation` and `simple_experience` flags
  disappear. Engine modules the dialogs alone consumed are deleted; CLI-only modules stay.
- `Session` is a toolbox of named steps whose results are explicit outcomes. `DocumentTask` owns the stages and never
  derives them by probing Session fields after the fact.
- The ordinary outcome review (`ordinary_outcomes.py` and the Session fields around it) is removed. Governed recovery
  keeps its adaptive contracts.

**Acceptance.** The test suite is green with the legacy modules absent; the installed smoke test delivers verified copies
through the conversation; `grep -r legacy src/` returns nothing.

## Workstream K (P0) — Triage at attach time, and dirty fixtures

**Goal.** The moment a file is attached, the app knows whether it can work with it and says so in one quiet line.

**Expected outcome.**

- A deterministic `triage.py` runs the fingerprint and hygiene checks when a file is attached (milliseconds, no model
  call): opens as a package, macro-enabled, protection enforced, compatibility mode, styled vs Normal-only body, real
  list numbering vs typed numbers, run fragmentation, text-box headers, pending revision count, comment count.
- Each attachment shows a state: fine, a one-line note, or refused with the reason. Refused files never enter a plan.
- The compiler receives the same facts per layout so it plans with them instead of discovering them.
- `tests/make_fixtures.py --dirty` builds a family of bad files: Normal-only styling, typed numbers, fragmented runs,
  text-box headers, non-breaking spaces, compatibility mode, many tracked changes, a LibreOffice-style package. Tests pin
  each file's triage result, that mechanical edits (remove headers, change fonts) still pass verification on them, and
  that incompatible edits are refused at plan time with a sentence and a remedy.

**Acceptance.** Every dirty fixture attaches with the expected note; no dirty fixture reaches a model call before its
note is shown; the owner drops one of his worst real files and sees the right note.

## Workstream L (P1) — Plan per layout

**Goal.** A mixed batch gets one plan the user approves once, built from one specification per structural layout.

**Expected outcome.**

- The compile conversation submits a plan: a list of layouts, each with the pattern ids it covers and its own EditSpec
  (or chain). Each layout's spec is validated and dry-planned against that layout's files. A batch with one layout is
  the existing single-spec case.
- The batch runner routes each file to its layout's spec; preview covers one sample per layout; delivery and records are
  per file as before. A file whose pattern no layout covers is set aside at plan time with a sentence.
- The compiler sees, per layout, the role evidence from `structure.py` (what an article title, a PART heading, the
  section title look like in that layout) and writes concrete selectors with required counts. The verifier checks
  concrete selectors; the role classifier never becomes edit authority.
- Questions are asked per layout, never per file. The review groups changes and samples by layout: "Layout A · 8 files".

**Acceptance.** A synthetic batch of three layouts (styled, Normal-only with typed numbers, Heading-styled) with one
request "make the article titles bold" produces three layout specs, one preview per layout, one approval, and every
output passes verification with exactly the article titles bold.

## Workstream M (P1) — Readable by construction, one button

**Goal.** The plan reads like a colleague wrote it, and every path ends in the same single action.

**Expected outcome.**

- Every registered operation implements `describe(params, select) -> str` beside its schema; a test asserts each one
  returns a sentence with no JSON, no underscores, no twips.
- Assumptions appear inline. Units are inches and points. Selectors read as prose.
- The model's user-facing message is required on submission and is what the chat shows; the spec summary stays in the
  record. Choices are buttons. A free-text question renders once.
- Recovery, custom methods and ordinary edits all end in "Create edited copies"; a second look is asked for only when
  a material finding changed, and the card says what changed.

**Acceptance.** The headless coordinator run for a tab-stop spec and for a recovery plan each show prose only; the
window shows one primary action in every stage except questions.

## Workstream N (P2) — Docs and release

README, CLAUDE.md, `docs/CONVERSATIONAL_DESKTOP.md`, release notes and screenshots describe the one product. Design
notes for removed surfaces move to `docs/history/`.

## Do not, in this round

- Add operations, planners, critics or review layers. The layout plan is the same compile conversation returning
  several specs, not a second planner.
- Keep a removed surface behind a flag, an environment variable or an "advanced" menu.
- Normalize a file without an explicitly accepted first stage.
- Mark an item done on fixtures alone when its acceptance names the owner's machine.

## Status — agents update this at the end of every session

| Item | Status | Owner / branch | Last note |
| --- | --- | --- | --- |
| J — one product | implemented; installed-app and live walkthrough pending | claude/admiring-edison-f6guko | 2026-10-19: Legacy window, 13 dialog/page modules, library offers in chat, PROJECT.json memory, neighbor-file discovery and the ordinary outcome review deleted; `ui/app.py` slimmed to application, prompts and `main`; `Session` reduced to a toolbox with one decision table (`Session.outcome`) and `DocumentTask` owning the stages; CLI subcommands unchanged (library, recipes, office profiles, QA, workspaces stay command-line). Legacy test modules deleted or ported. Still needs the owner's machine: the installed Windows app and a live request. |
| K — triage and dirty fixtures | implemented; owner's real-file drop pending | claude/admiring-edison-f6guko | 2026-10-19: `triage.py` (deterministic, model-free, 3–12 ms per file) refuses non-files, .docm/.doc, `~$` lock files, packages that do not open and empty bodies with one sentence each, and notes protection, compatibility mode, Normal-only styling, typed numbers, fragmented runs, text-box headers, pending revisions, comments and irregular spacing, with the raw facts for the planner; `make_fixtures.py --dirty` family pinned by tests/test_dirty_fixtures.py and tests/test_triage.py. Pending: the owner drops one of his worst real files and sees the right note. |
| L — plan per layout | implemented; live mixed-batch run pending | claude/admiring-edison-f6guko | 2026-10-19: `layouts.py` routes by fingerprint, previews one sample per layout, runs each layout as a verified batch and binds the token to every layout, its files and the routing; the compiler accepts `layouts` in a submission and checks each spec on its own patterns, refusing uncovered patterns; the window shows one card per layout; `plan-spec`/`run-spec` accept a plan file. Tested on three synthetic layouts (typed, styled, heading-styled). Still needs the owner's machine: a live request over specs from two projects. |
| M — readable, one button | implemented; live walkthrough pending | claude/admiring-edison-f6guko | 2026-10-19: Every operation has `describe()` over `ops/prose.py` (270 cases, no JSON, inches and points); the plan shows one sentence per change with assumptions inline; the model's message is required and shown instead of the summary; choices are buttons; recovery and custom methods end in the same action; a question renders once. Still needs the owner's machine: a live run to judge the model's own sentences. |
| N — docs and release | implemented; release acceptance pending | claude/admiring-edison-f6guko | 2026-10-19: README, CLAUDE.md, `docs/CONVERSATIONAL_DESKTOP.md`, the eleven guides and `docs/releases/unreleased.md` describe the one window; the two superseded plans moved to `docs/history/`; screenshots regenerated from the window; module docstrings cite the moved plan. The legacy test modules were ported or deleted and the whole suite runs green on Linux with Qt offscreen. Still needs the owner's machine: the four release-acceptance boxes in the pull request template (private-corpus replay, installed Windows candidate with Word, one live `do` request, the diagnostics bundle). 2026-10-20: Prepared v1.4.0 from latest master including PR #44; Linux/Windows and installed-app PR CI passed, with tag CI gating publication. Private-corpus replay, owner-machine Word and a live request with diagnostics remain pending and are disclosed in the release notes. 2026-10-20: Prepared v1.5.0 from merged PR #45; its Linux/Windows and installed-app CI passed, and tag CI gates publication. Private-corpus replay, owner-machine Word and a live request with diagnostics remain pending and disclosed. |
| R — render pipeline (`docs/RENDER_PIPELINE.md`) | implemented; owner's Word run pending | wip/render-pipeline | 2026-10-20: `render/cache.py` (PDFs keyed by the .docx sha256 + renderer identity, beside the session folders, 2 GB bound, `SPEC_SURGEON_RENDER_CACHE=0` off) consulted by every `render_pdfs` call of the preview and the run; `word_worker.run_word_batch` renders a batch in one private Word with a per-file deadline held by the parent (a stuck file ends that worker, the rest continue in a fresh one); LibreOffice profiles kept under `config_dir()/render-profiles/<n>`, leased per run, wiped once when soffice cannot start from one. Measured on 6 fixtures with LibreOffice: preview 5.9 s cold → 1.9 s with every source and output served. Tested with a fake Word worker behind a real spawned process, fake and real soffice. Still needs the owner's machine: a Word batch (one instance, a stuck file, the registry version in the cache key) and profile reuse timings on Windows. |

Still needed for real-file acceptance: the owner's 12 specifications and template, a live model run with time and cost,
and Word rendering on the owner's machine.
