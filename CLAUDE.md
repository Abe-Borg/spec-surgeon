# CLAUDE.md — Spec Surgeon

## What this is

Spec Surgeon edits Word (.docx) specification files in batches, from one word or one property up to whole tables,
sections, pictures and the page structure, with an independent verification record for every file. A deterministic
engine plans each change from the source before touching it, applies it, and checks the result with a verifier that
shares no code with the transform. An optional Claude layer turns plain-English requests into EditSpecs and never
edits a file itself. The user is in control: whatever they ask for, preview and approve, the engine does exactly. The
files go to clients, so the one failure that matters is content lost or invented without the user asking for it.

## Invariants

1. **Predict first.** `Op.plan()` computes every prediction (diffs, parts, members, revision deltas, assertions and
   their target counts) from the SOURCE, analytically; never by running the transform on a copy and reading the result.
2. **The verifier shares no code with the transforms.** `src/spec_surgeon/verify/` imports nothing from `ops/`,
   `editspec.py`, `ooxml.py`, `package.py`, `goals.py`, `goal_contracts.py` or the Claude layer
   (`tests/test_independence.py`). It has its own readers, text extraction, selectors, style chains and definitions.
   Do not deduplicate the twin implementations: the duplication is the point.
3. **Repack by member substitution.** `DocxPackage.repack` keeps every untouched member byte-identical, in source
   order. Never pretty-print, regex-edit XML or `zip -r`; serialize with `serialize_xml()` only. Read parts with
   `DocxPackage.xml()` (`parse_xml` refuses a `<!DOCTYPE`, `xml-dtd`) and find them through relationships
   (`main_part`, `text_parts`, `settings_part`), never by their usual names; new revision ids come from
   `assign_revision_ids`, above every annotation id in every document part (`ooxml.max_revision_id`).
4. **Never write back blind.** In-place delivery goes through `guard.write_back` and nothing else. It refuses a source
   whose size, mtime or md5 moved, a lock file, or a read-only file. No `force` flag exists and none may be added.
5. **Refuse what cannot be predicted, not what is large.** A case an op cannot predict exactly raises `JobException`
   with a code and sets the file aside; the engine never guesses. Size and destructiveness are not reasons to refuse:
   deleting a table, a section or the whole body is a change like any other once it is planned, previewed and approved.
   Overlapping ops are composed into ordered steps in the user's own order (`editspec.stage_cuts`), never reordered:
   a source-bound template op not listed first is refused, not moved.
6. **No generated characters.** Formatting and structural ops add zero text. Content ops place the user's approved
   wording verbatim, or the words of a file the user attached (a table, a template), and `compiler.placed_words` must
   trace every typed word to the user's own sources. Claude's words are never the user's: a tapped suggested reply is a
   decision, never approved wording. A choice's drafted wording becomes the user's only by the pick — as offered, or as
   the user edited it first (`conversation.edited_wording`: the same `clean_wording` rule); a draft the user replaced
   is never approved.
7. **One specification per edit.** The approved EditSpec (or chain of them) is the only description of a change the
   engine executes; nothing runs that was not planned, previewed and approved as that spec. One exception, the
   owner's (2026-10-29): Export's clean choices (`exports.clean_spec`: every tracked change accepted; or every comment
   removed, then every tracked change accepted) are fixed specs built in code that the menu choice names and approves,
   without a preview. They run over the edited copies through the gate and the checked run, into the scratch folder,
   and change no copy, history line or undo point; never widen them, and never let a model choose one.

Out of reach by decision (refused with a code, "change this one in Word"): shapes, SmartArt, charts, equations,
embedded objects, data-bound content controls and the theme, and text boxes as objects (adding, moving, sizing or
formatting one). The words inside a text box are `replace_text`'s to change (a nested paragraph is a paragraph of its
own) and set a file aside under `replace_table` (Word keeps two copies of a text box; the code `textbox`).

## Modes

Every invariant binds **Safe** and **Default**. **Obliterate** (`Session.unguarded`)
is the owner's decision to lift, for that mode only, the guardrails that protect a document's content: the placed-words
trace (invariant 6: `compose_submission(unguarded=, shown=)` traces placed words to the user's words or to the reply
the user reads, `Conversation.shown_text`, never to nothing; Claude may write wording once its reply shows it;
`compiler.UNGUARDED_RULES` rewrites the prompt's rules it lifts), a custom method's formatting-only limits
(`codegen` skips `hard_limits`, and a copy that changes a part no check reads fails `UNREAD`, from V10's
`unread_members`, `codegen._check_unread`; `UNGUARDED_LIMITS`; the writer gets the `GOAL_FROM` helpers and the user's attachments
as `pkg.inputs`, pinned in `GeneratedOp.attachments`, and its failed checks go back to it `UNGUARDED_CHECK_ROUNDS`
times, to fix or to keep with a reason in `GeneratedOp.accepted_failures`; `NEVER_KEPT_CHECKS` can never be kept; after the
rounds the latest method is used and never called passed: `codegen.samples_verdict` words the note, the message and the
plan's details; in every mode the words a method's sample copies add and remove are listed before approval from the
verifier's V2 record, never from the method or the model — `preview.text_changes_of` → `SampleView.text_changes`, said
by `DocumentTask._say_text_changes` and shown under each sample), and
the holding back of a copy that fails a check (`deliver_unverified`: delivered to `outputs/` marked UNVERIFIED, its
failed checks in `FileResult.unverified`/`ChainFile.unverified`, no recovery; the mark stays on every later copy built
on it, `DocumentTask._mark_unverified`, in every mode). The verifier still runs on every copy
and reports; invariants 1, 2, 3, 4, 5 (built-in ops still refuse what they cannot predict; Obliterate's way past a
refusal is a custom method) and 7 bind every mode, and so do copies only, the source guard, the sandbox and its
whitelist, and the governed outcome path (never unguarded). Obliterate is hidden unless `Settings.show_obliterate`,
needs the warning acknowledged each time (`DocumentTask.set_mode(acknowledged=True)`), voids the plan when entered or
left, and is never what a launch opens in (`settings.MODES`, `web.server.launch_mode`). Pinned by `tests/test_obliterate.py`.

## Repository layout

The desktop is one window with two modes (`docs/CONVERSATIONAL_DESKTOP.md`) and a hidden third (Modes, above): **Safe**
— buttons whose plans `recipes.py` builds in code from the user's form, no model call anywhere (`Session.model_free`:
no visual check, no recovery) — and **Default**, the conversation below. Both pass the same gate (`compiler.compose_submission`), preview,
approval and verification. The page in `web/static/` (built from `frontend/`), served by `web/server.py` over one
`document_task.DocumentTask` (contract `docs/WEB_API.md`) and shown by `web/shell.py`. The user attaches Word files, an
optional template and pictures or tables for the edit to use; `triage.py` checks each on arrival without a model call.
`layouts.py` gives a mixed batch one EditSpec per structural layout with one approval; `review_display.py` describes
the previewed spec in the sentences each op writes for itself (`Op.describe`); the window shows no plan card (the chat
says what will change, the previews show it), and those sentences record each copy's changes in `applied`. The one
button lives in the chat pane;
each preview carries a checkmark (filled from the start: it gets its copy) and an X, and Create counts the copies it
makes (`action.copies`, less the X'd; `action.unpreviewed` have no preview); X'd documents are `hold` on the primary action (`Session.run(only=)` keeps
every approved pin), wait on `DocumentTask.todo`, and the reply `WORK_TODO` begins a pass over them alone (`focus`, one
request at a time); `applied` keeps the changes each copy carries, so the history the model hears is per document; `runs` keeps each run
that delivered copies (its request, the Safe-mode button, the documents it delivered and why the others got none), which
the window lists under the copies as **What you've done so far** in every mode (`DocumentTask._done_view`, the view's
`working.done`; a Safe-mode button it names carries a checkmark), Undo taking the newest off.
A request ends with copies, with a discarded preview or dropped request, or with a reply that is an answer and nothing pending (words
alone: no question, choice, suggested reply, plan, refused plan or custom method — `DocumentTask._answered`): the
decisions are cleared, the conversation ends, and the exchange (the user's words, the answer's first sentence) is kept
in `DocumentTask.exchanges` (journalled, the last `EXCHANGES_KEPT`) and told to the model with the next request
(`Session.compile(earlier=)` → `build_messages` `EARLIER IN THIS CONVERSATION`), so a put-back, an attach or a mode
change revives nothing stale and the next message is a fresh REQUEST; a follow-up while something is pending stays a
revision (`conversation_matches`). `start_todo` still reuses the to-do entries' decisions.
Documents the user leaves out of a request (`submit_editspec.leave_out` → `Session.left_out`) are not planned,
previewed or run (`planned_files`) and wait under Left untouched; a file the plan refuses does too, at any step of a
chain (`planner.plan_batch_chain` plans every step on every file; `preview.refusals`), and so does a sample that fails
its check while another passes (`preview.failed_samples`, `Session.failed_samples`: never run, its card kept, the
others created; a custom method's preview has none), and the preview of the others stands (outcome planning starts,
and Safe mode's Create is unavailable, only when no file plans or no sample gets through). The to-do list
(`DocumentTask.todo`, each entry with `why` and `reason`) is where documents wait for later: the red X, the chat's
`set_aside` tool at the user's word (`Conversation.set_aside` → `DocumentTask._take_set_aside`), a file the plan
refused or whose sample failed once the others run, a run failure no recovery takes, and recovery's leftovers at `SET_ASIDE_REST`;
`put_back` takes one off without a pass.
Delivered copies are the working set the next request builds on. The window
works in a scratch folder (`scratch.py`: one per window under `data_dir()\scratch`, locked while it runs, holding
every session folder, dropped file and the journal; deleted at a clean close after one question when a copy is not
saved as it is now — `DocumentTask.export_states`: a copy is saved while a file with its current bytes sits where
Export → Word (Tracked Changes ON) put it, its destination recorded with the md5 of the bytes written — swept at the
next start, a crashed window's taken over and restored), and writes a file where the user chose only through
**Export** (the top bar's menu, `ExportMenu.tsx`; `DocumentTask.export` → `guard.write_new`, never over an input,
never into the scratch tree). Export covers every copy the window lists (`export_set`: the working set and copies a
run still in recovery delivered) in the owner's three kinds (`exports.py`): **Word (Tracked Changes ON)**, the copies
as they are; **Word (Tracked Changes OFF)**, every tracked change accepted, comments kept; **PDF (Clean)**, comments
removed and tracked changes accepted, then `pdf_export`. The clean kinds run as a job (`perform("export")`), each a
fixed spec through the gate and a checked run in `exports\` (invariant 7's exception); a copy whose clean version or
PDF cannot be made gets no file, named with why. Safe mode's Save clean PDFs button was retired for it (2026-10-29).
The assistants point to Export for PDFs (`compiler.SIMPLE_EXPERIENCE`, `exports.PROMPT_NOTE`).
**Discard preview**
(`DocumentTask.discard_preview`; what it withdraws is `discard_kind`, the view's `discard`) withdraws a preview: in Safe
mode it shows again what `_recipe` set aside (`_before_button`); in Default and Obliterate it — or **Drop this
request**, at a question, a pause or a reply to continue — ends the request (`_discard_request`: decisions cleared, the
request kept in `exchanges` as withdrawn, a pass's documents back on the to-do list). **Undo the last button** (Safe)
or **Undo the last change** (`DocumentTask.undo`) pops `undo_points` (one per run that delivered copies, taken at
approval, kept in the journal) to put the working set back one run, in every mode. Neither writes or deletes a file.
There is no command line (removed 2026-10-21 with the
library, workspaces, workflows, office profiles, spec QA audits, project evidence, evaluations and the named v0.1.0
jobs). Details: `docs/CONVERSATIONAL_DESKTOP.md`.

- `src/spec_surgeon/`
  - `package.py` (`DocxPackage`: zip, rels, sections, overlay, repack) · `ooxml.py` (transform-side XML helpers).
  - `ops/` — the operation library: `base.py` (`Op`, `PlanFragment`, `merge_fragments`, `JobException`),
    `selectors.py`, `table_select.py`, `prose.py` (the words of `describe()`), one module per op, shared cores
    (`textedit.py`, `slots.py`, `effective.py`, `lists.py`, `style_import.py`, `images.py`, `hf_common.py`),
    `elsewhere.py` (flags naming where a change kept to one place leaves the same words, and a fill in another case than the template; `plan_spec` runs it),
    `patterns.py` (`compile_pattern`: the one way a user- or model-written pattern is compiled under `ops/` — and
    outside it, for the model's read tools' `regex` and an outcome contract's locators — the `regex` module in its
    `V0` (`re`) behaviour, `REGEX_TIMEOUT_S` = 2 s on each text searched, `MAX_PATTERN_CHARS` = 1000 except for an
    exact-text pattern (`exact_text_only`: `\A`, escaped literal text or one group of such alternatives, `\Z` — how the
    app selects bound paragraphs by their whole words); past the deadline `JobException("regex-timeout")`, the file
    set aside — stress-test finding F8; the app's own constant patterns and the ones it builds from escaped literals
    stay on `re`).
  - `verify/` — the INDEPENDENT verifier: `suite.py` (V1–V10 as a `VerificationRecord`), `props.py` (V10 dispatch, the
    residual identity check; each op family's kinds live in their own module, imported at its end; `KIND_CONTRACTS`
    and `KIND_EXAMPLES`, the plain-English contract and worked assertion of each kind, filled by the kind's module
    at import — the verifier's own words, which the custom-method writer reads through `describe_assertion_kind`;
    a kind whose module writes none gets that module's docstring, so every kind has one), `reader.py`,
    `textcheck.py`, `edits.py`, `xsd.py`, `geometry.py` (V6/V7's page evidence, and `shown_words`: the words the
    original's pages show that the copy's do not, less what the plan removes — V6's warning, never a FAIL;
    `report.words_missing_sentence` is its one wording), `template_layout.py` (V7's template reference: the template
    as each document's plan makes it show, its fills in the slots and each section at the document's own text
    width, the template's vertical layout kept — live QA finding F08 — but the source's own header/footer distances
    where the plan keeps them, `keeps_distances` — the deep-dive stress test's D4), `explain.py`, `patterns.py` (the verifier's
    own bounded patterns, `v_compile`/`v_search`, the same deadline and length limit written independently: a
    pattern past its deadline is a `ValueError`, a failed check with that reason, never an exception out of the
    suite).
  - `editspec.py` — load, validate, plan, merge, apply; composition (`stage_cuts`, `compose_chain`, `plan_chain`).
  - `batch.py` (per-file pipeline, guarded delivery, records; `worker_plan`/`map_files`: three or more files go to
    spawned worker processes, each file whole in one, in the packaged app too — a test's patches never reach one,
    so `tests/conftest.py` runs threads), `guard.py`, `chain.py`, `report.py`
    (`FAILURE_EXPLANATIONS`: every refusal code in plain words), `diagnostics.py`, `ui_feedback.py` (the window's
    words for one refusal, `refusal_message`: the step that refused the file — `editspec.plan_chain` records its
    number, count and name — then what was looked for and what the document holds instead, from the record's
    details; the record itself never changes).
  - `catalog.py` (op catalog, `COMMON_ASSUMPTIONS`, `op_sheet`), `profile.py`, `planner.py`, `structure.py`, `triage.py`.
  - `compiler.py`, `conversation.py`, `llm.py`, `vision.py` — request → EditSpec. `llm.call` is the one call site: the
    system prompt carries its own 1-hour cache breakpoint, the history the automatic 5-minute one, and a `one_shot`
    call caches nothing after the system prompt; keep system prompts and tools byte-stable. No call runs at effort
    `max` (the owner's decision, 2026-10-22): `resources.EFFORTS` refuses it. The window's chat and its visual check
    run at medium, outcome planning and recovery at high, and the custom-method writer at `xhigh`
    (`resources.CODEGEN_EFFORT`, 2026-10-26: `generate_op` enters `llm.effort_scope`, after which calls are back at
    their own effort). The chat's `ask_user` and `submit_editspec` stream their input unbuffered
    (`compiler.STREAMED_TOOLS`, `eager_input_streaming`); `llm` checks it and re-sends a call whose input is broken.
    The window's only spending limit is the user's spending check per request (Settings → Spending check,
    `DocumentTask._new_request` inside the conversation's ledger; the owner's decision of 2026-10-27, which removed the
    pause after 48 calls); Stop is the kill switch (`llm.cancellation_scope`).
    Fast mode (`speed="fast"`, Settings → Fast responses, `llm.set_fast_mode`) is the user's choice, off by default:
    it changes speed and price (`FAST_PRICES`), never the model, effort or prompt; a fast call the fast capacity
    cannot take is re-sent at standard speed. `compiler.compose_submission` is the one gate a submission passes.
  - `preview.py`, `codegen.py` + `sandbox_runner.py` (custom transforms; the writer's read tools are
    `CODEGEN_READ_TOOLS` — the sample files' XML, paragraphs, styles and part texts, and `describe_assertion_kind`,
    a V10 kind's contract from `verify.props.KIND_CONTRACTS`; the prompt and tools are cached, so an addition to
    them is a deliberate change; a writer the spending check pauses stops with `GeneratedOp.budget_stop` and its
    state on disk, and `generate_op(resume_from=)` continues it from its checkpoint in the same folder —
    `Session._generate_custom` keeps the folder in `pending["paused"]`; a trial is verified without pages, so its
    samples get V6/V7 in the preview by the plan the trial predicted, `FileTrial.plan`, and `run_generated` renders
    and compares every copy before delivering any, `_check_layout`), `goals.py`, `goal_contracts.py`,
    `goal_operations.py` (reviewed outcome contracts; a target with `all: true` is bound once, by count), `recovery*.py` (one checkpoint per model call, at the turn's end), `continuous_recovery.py`, `campaign_planner.py`
    (bounded investigation of set-aside files: the outcome plan is a choice the user picks, its words beneath it —
    `Session.outcome` auto-takes a custom transform only — and the documents the user left out of the request stay
    out of it, `Conversation.left_out`), `inventory.py` (the content digest that pins a file).
  - `recipes.py` — Safe mode's buttons: each a fixed EditSpec from its form (`build`), the header/footer template's kind
    (`template_kind`); above them the note that says what Safe mode is and points to Default mode with an API key
    (`frontend/src/lib/safeNote.ts`, `SafeNote.tsx`; folded: `Settings.safe_note_hidden`); a form's `lines` field is a
    list of one-line wordings and fields sharing a `row` sit side by side; the second round of buttons (M1–M3) is
    designed in the backlog. The font buttons (Match font to template, Change font) group the documents by their own
    body size, one spec per size as a layout plan when there are two (`_font_layouts`; documents built alike take the
    size most of them have, the rest are left out with why); the page's copy of the list (`frontend/mock/recipes.json`) is pinned to `catalog()`. Find and
    replace, occurrence by occurrence (backlog F1): `replace_table` lists every occurrence it finds in its
    `replaced_text` assertion (and in a refusal's details), `planner.FilePlan.occurrences` exposes them, the window
    shows them per document (`DocumentTask._occurrences_view`, the page's `OccurrencesCard`), and an untick goes back
    as `POST /api/occurrences` → `DocumentTask._occurrences`: the op's `skip` list, the gate again with the recorded
    keys (`compiler.compose_submission(skips=)`: a request never writes `skip` itself), a new preview. The verifier
    re-finds every occurrence, honours the plan's skips by its own reading and records the log under V10's data
    (`verify/suite.occurrence_lines`). **Add a folder of documents** (`DocumentTask.attach_folder`, `/api/attach-folder`)
    attaches a folder's own `.docx` files, subfolders not entered; **Remove all documents**
    (`DocumentTask.remove_documents`, `/api/remove-documents`) takes every Word document out, each as its ✕ would.
    `exports.py` (Export's three kinds: `clean_spec`, `make_clean`, `PROMPT_NOTE`) and `pdf_export.py` (Export → PDF
    (Clean): a PDF of each clean copy that passed, rendered through `render` and its cache, then `check_pdf`).
    `template_roles.py` (a template's style per role, page and font, read without a model) and `body_match.py` (match
    body formatting: one plan per document from its own structure, one layout per agreeing pattern, each through the
    gate; what it cannot match is left out with the reason, `Session.left_out_reasons`).
  - `saved_recipes.py` — saved recipes: the runs of What you've done so far kept under a name (`Store`,
    `config_dir()\recipes.json`, each change read, made and written atomically under `recipes.json.lock`, which every
    window takes). A step is a Safe-mode button and
    its form, or the user's words and the plan they approved (`portable_plan`: a custom method, bindings, typed or bound
    slot fills and per-occurrence capitalisation are refused with the reason; unticked occurrences dropped), with the
    words it places (`compiler.placed_words` at approval). Taken at approval (`DocumentTask._steps_now` → `run_steps`,
    journalled beside `runs`). A run (`build_run`, `DocumentTask._saved_recipe`) builds every kept step from the
    documents attached now (a plan's template, picture or table remapped onto the attached one, `remap` — the owner's
    decision), passes each through the gate, composes them in the user's order into ONE plan (`combine`: a chain, or a
    plan per layout of chains) and through the gate again: one preview, one approval, one checked run, `outcome_source`
    `"saved"` (`Session.model_free`: no AI anywhere). A recipe runs in its own mode or one with fewer guardrails
    (`runs_in`): a Default recipe is never offered in Safe mode, an Obliterate one runs only in Obliterate. A document a
    step cannot take is left untouched in Safe mode and goes on the to-do list in Default and Obliterate (no AI
    re-plans it); a recipe's run is never handed to Claude as a request (`_invalidate`, the journal's `recipe_run`).
    No export or import of recipe files (the owner's decision, 2026-10-29). Pinned by `tests/test_saved_recipes.py`.
  - `tutorial.py` — the guided tour's practice workspace (`docs/CONVERSATIONAL_DESKTOP.md`, "The guided tour"):
    `TutorialTask` (a `DocumentTask` without a journal over the bundled showcase in `examples/tutorial/`, which
    `tests/make_tutorial_showcase.py` builds byte-stable; `send`/`choose` refused, `NO_CONVERSATION`) and `Tutorial`
    (the lease `web/server.py` swaps the window's task for: `begin`, `show(kind)`, `end`; the original task waits
    untouched in `WindowState.original_task`; the practice folder is `tutorial\` in the window's scratch folder,
    `practice_root`, never a folder windows share). Each chapter state (`SCENARIOS`) is built through the production
    paths — triage, `recipes.build` and the gate, `Session.make_preview`, `Session.run` — with no model call; the
    conversation chapters are recordings that say so (`RECORDED`, `TutorialTask.scripted`). The page's side:
    `frontend/src/lib/tour.ts` (the manifest: chapters, steps, `data-tour` anchors, the capabilities each teaches),
    `capabilities.ts` (every end-user control carries a `data-capability`), `useTour.ts`, `tourStorage.ts`,
    `components/TourOverlay.tsx`; a Safe-mode chapter's form is opened by its step (`prepare`: `TOUR_FORM`,
    `TOUR_FIND_FORM`, the values the server's chapter ran), and the user's own form is set aside when the tour starts
    and put back when it ends (`App.tsx`). `frontend/tests/tour.test.ts` fails on a `data-capability` the vocabulary
    lacks, a capability no control carries or no step teaches, an anchor the page lacks, a scenario the server does
    not build, or a form value the server's chapter did not run: **a new control needs its capability and its tour
    step in the same change** (the test cannot see a control that carries no capability; the reviewer can).
  - `document_task.py`, `review_display.py`, `gui.py` (`Session`, `main`; `python -m spec_surgeon` runs it), `web/`
    (`server.py`, `shell.py`, `watch.py`: the page's own reports and the watchdog over the page and the job),
    `scratch.py`, `updater.py`, `settings.py`, `credentials.py`, `winapp.py`.
  - `render/` (Word COM, LibreOffice), `schemas/` (ECMA-376 XSDs), `skill/`, `examples/` (the worked EditSpecs, and
    `examples/tutorial/`, the tour's showcase, shipped in the wheel and the app; `packaging/installed_wheel_smoke.py`
    checks the installed bytes against the checkout).
- `tests/` — pytest; `make_fixtures.py` and `fx_*.py` build synthetic documents; `fake_claude.py` scripts Claude;
  `fake_render.py` fakes the renderer seam (`render.render_pdf_groups`); `conftest.py` seals keys, config dir and
  model, and hides Word and LibreOffice from every test not marked `@pytest.mark.render` (a test that looks at
  rendered pages carries the mark; one that fakes a renderer patches it itself); `describe_cases/` feeds
  `test_describe.py`. Only a failing test keeps its temporary folder (`tmp_path_retention_policy`, pyproject).
- `docs/` — `BACKLOG.md` (the open work), design notes (`BUILTIN_OPS_BATCH*_DESIGN.md`), `releases/`;
  `TRUST_CLAIMS.md` (the ledger behind the trust surfaces).
- `packaging/` — PyInstaller spec, Inno Setup script, installed-app smoke tests, `license_notices.py` (the app's
  LICENSE.txt and its AGPL Corresponding Source). Two licenses, never one: Spec Surgeon's own code is MIT; the
  Windows app bundles PyMuPDF (AGPL-3.0, no commercial license held), so the app as a whole is AGPL v3
  (`about.py`). Never describe the distributed app as MIT-only.

## How to run

```sh
pip install -e ".[dev,gui,agent]"            # Windows: py -3 -m pip install -e ".[dev,gui,agent,word]"
python3 -m pytest -q -n auto --dist worksteal # every core, as CI; Windows: py -3 -m pytest -q -n auto --dist worksteal
python3 -m pytest -q -n auto -m "not render"  # the quick loop: everything but the tests that render real pages
python3 -m pytest -q tests/test_ops.py        # one module (in a git worktree: PYTHONPATH=src python3 -m pytest …)
cd frontend && npm ci && npm run build        # rebuild the window's page (Node 22) into web/static; commit it
python3 tests/make_fixtures.py OUT            # fixtures; --rich, --template, --reference, --styles-template, --numbering-scheme, --dirty
spec-surgeon-gui                              # the window; or: python3 -m spec_surgeon
```

There is no command line. Check a change with the test suite; to run the engine by hand, call it from Python:
`batch.run_batch(files, "edit-spec", {}, out_dir, spec=editspec.load_spec(path))` for a batch, `verify.run_suite`
for one pair, `gui.Session` for the window's steps with a scripted client (`tests/fake_claude.py`). A run's
`DIAGNOSTICS.md`, `records/` and `logs/` say why a file did not pass. The window's own trail (requests, jobs, the
page's errors, a frozen page, an unclean exit) is in the logs folder, `spec-surgeon.log` and `app-events-<pid>.jsonl`
(`docs/DEVELOPER_DIAGNOSTICS.md`, "The window"); About → Save a diagnostic bundle zips it with the conversation's
journal and the newest session folders' records (all in the window's scratch folder, gone when it closes).

## How to add an op

1. Copy `src/spec_surgeon/ops/set_font.py`; keep `@register`, `name`, `mode`, `params_schema`, `select_schema`,
   `verification_profile`, `describe()`, `plan()`, `apply()`; import it at the bottom of `ops/__init__.py`.
   `describe(select, params)` returns one or two sentences built with `ops/prose.py` (inches and points, quoted exact
   text, no identifiers, no JSON): the line that records the change in the history the model hears.
2. `plan()` fills the fragment analytically from the source: `intended_parts`, `diffs`, members, `rev_delta`,
   `property_assertions`, and honest `touches` (what it changes and what it reads). A pattern the user or the model
   writes is compiled through `ops/patterns.compile_pattern` (never `re`), and in the verifier through
   `verify/patterns.v_compile`.
3. Write the V10 assertion kind in `verify/` first, with its own selection and an "exactly this change" check.
4. `apply()` mutates the working copy through the overlay (`write_xml`, `remove_member`, `add_member`, `edit_rels`).
5. A fixture, an end-to-end test (plan → merge → apply → `run_suite`, `record.passed`), and a planted-defect test for
   every new rule: a wrong output, and a wrong implementation shared by plan and apply.
6. Every new refusal code in `report.FAILURE_EXPLANATIONS` (and its details enough for `ui_feedback.refusal_message`
   to name what was looked for: the words, the selection, the value); the op in `compiler.placed_words` if it places typed
   text; a `COMMON_ASSUMPTIONS` entry in `catalog.py`; at least one case in `tests/describe_cases/`.

## The trust surfaces

Help → **Why trust it?** (`frontend/src/components/WhyTrustIt.tsx`) and the "I'm not convinced" dossier it opens
(`TrustDossier.tsx`, its cards in `lib/trustRuntime.ts`, its diagram in `TrustDiagram.tsx`) describe what the app does
action by action. **The trust surfaces are a contract. Any change to a behaviour they describe updates them in the same
change:** a new user action or button (which also needs its capability and tour step, above), a new network call or destination, a new automatic behaviour (a model call
without its own click, a timer, a background job), a model, effort or caching change, a new or changed limit, or
anything new kept on disk. Update `docs/TRUST_CLAIMS.md` (the ledger: every claim, its source, its test) with it. Every
number the copy quotes lives in `frontend/src/lib/trustFacts.ts`, and `tests/test_trust_facts.py` pins each to its
constant in the code; the copy never types a number itself. A trust document that has drifted from the code is worse
than none.

## Session protocol

The open work is in `docs/BACKLOG.md`. Read it before any work, and take an item from it unless the user asks for
something else. The pull request that finishes an item removes it from the backlog, or moves it to "Waiting on the
owner's machine" when only a real-file run is left. The pull request says what changed and what is tested, so the
backlog keeps no log and no dated notes. Do not add dated plan files: a new operation gets its design in the backlog
(or in a design note it links) before it is built. Nothing is done on fixtures alone: an item's acceptance names a
real-file run, which the user performs.

Standing decisions from the earlier rounds (their plans are in the git history):

- Text boxes as objects are not pursued (the words inside one: "Out of reach by decision", above). No raw-XML
  operation, catch-all deletion operation or `force` flag is added.
- In Safe and Default, a custom method may do more kinds of formatting, never more kinds of change: a family is added
  only behind a V10 kind that proves the content it touches is the source's, and the sandbox's limits stay as they are.
- Capitals are a display property (`w:caps`) unless the user gives capitalised wording; nothing retypes a file's letters
  in another case.
- The batch overview and the plan's flags describe and never decide: they change, choose and order nothing.
- The standalone find-and-replace tool (`Abe-Borg/docx-find-and-replace`, python-docx and tkinter, editing in place)
  is not merged into Spec Surgeon (the owner's decision, 2026-10-27): Safe mode's **Find and replace** button is where
  that work lives (its rule-set CSV loads as the attached table), with every occurrence listed to untick (backlog F1,
  built), the occurrence log in the records (F2) and a folder of documents attached at once (F3). Text boxes, editing
  in place over the originals and re-scanning replaced text stay out, as decided above and by T1.
