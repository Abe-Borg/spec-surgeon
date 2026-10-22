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
   decision, never approved wording.
7. **One specification per edit.** The approved EditSpec (or chain of them) is the only description of a change the
   engine executes; nothing runs that was not planned, previewed and approved as that spec.

Out of reach by decision (refused with a code, "change this one in Word"): shapes, SmartArt, charts, equations,
embedded objects, data-bound content controls and the theme.

## Repository layout

The desktop is one window: the page in `web/static/` (built from `frontend/`), served by `web/server.py` over one
`document_task.DocumentTask` (contract `docs/WEB_API.md`) and shown by `web/shell.py`. The user attaches Word files, an
optional template and pictures or tables for the edit to use; `triage.py` checks each on arrival without a model call.
`layouts.py` gives a mixed batch one EditSpec per structural layout with one approval; `review_display.py` describes
the previewed spec in the sentences each op writes for itself (`Op.describe`); the window shows no plan card (the chat
says what will change, the previews show it), and those sentences record each copy's changes in `applied`. The one
button lives in the chat pane;
each preview carries a checkmark and an X: X'd documents are `hold` on the primary action (`Session.run(only=)` keeps
every approved pin), wait on `DocumentTask.todo`, and the reply `WORK_TODO` begins a pass over them alone (`focus`, one
request at a time); `applied` keeps the changes each copy carries, so the history the model hears is per document.
Delivered copies are the working set the next request builds on. There is no command line (removed 2026-10-21 with the
library, workspaces, workflows, office profiles, spec QA audits, project evidence, evaluations and the named v0.1.0
jobs). Details: `docs/CONVERSATIONAL_DESKTOP.md`.

- `src/spec_surgeon/`
  - `package.py` (`DocxPackage`: zip, rels, sections, overlay, repack) · `ooxml.py` (transform-side XML helpers).
  - `ops/` — the operation library: `base.py` (`Op`, `PlanFragment`, `merge_fragments`, `JobException`),
    `selectors.py`, `table_select.py`, `prose.py` (the words of `describe()`), one module per op, shared cores
    (`textedit.py`, `slots.py`, `effective.py`, `lists.py`, `style_import.py`, `images.py`, `hf_common.py`),
    `elsewhere.py` (flags naming where a change kept to one place leaves the same words, and a fill in another case than the template; `plan_spec` runs it).
  - `verify/` — the INDEPENDENT verifier: `suite.py` (V1–V10 as a `VerificationRecord`), `props.py` (V10 dispatch, the
    residual identity check; each op family's kinds live in their own module, imported at its end), `reader.py`,
    `textcheck.py`, `edits.py`, `xsd.py`, `geometry.py`, `explain.py`.
  - `editspec.py` — load, validate, plan, merge, apply; composition (`stage_cuts`, `compose_chain`, `plan_chain`).
  - `batch.py` (per-file pipeline, guarded delivery, records), `guard.py`, `chain.py`, `report.py`
    (`FAILURE_EXPLANATIONS`: every refusal code in plain words), `diagnostics.py`.
  - `catalog.py` (op catalog, `COMMON_ASSUMPTIONS`, `op_sheet`), `profile.py`, `planner.py`, `structure.py`, `triage.py`.
  - `compiler.py`, `conversation.py`, `llm.py`, `vision.py` — request → EditSpec. `llm.call` is the one call site: the
    system prompt carries its own 1-hour cache breakpoint, the history the automatic 5-minute one, and a `one_shot`
    call caches nothing after the system prompt; keep system prompts and tools byte-stable. No call runs at effort
    `max` (the owner's decision, 2026-10-22): `resources.EFFORTS` refuses it, the window's chat runs at medium and
    every other call at high. `compiler.compose_submission` is the one gate a submission passes.
  - `preview.py`, `codegen.py` + `sandbox_runner.py` (custom transforms), `goals.py`, `goal_contracts.py`,
    `goal_operations.py` (reviewed outcome contracts), `recovery*.py`, `continuous_recovery.py`, `campaign_planner.py`
    (bounded investigation of set-aside files), `inventory.py` (the content digest that pins a file).
  - `document_task.py`, `review_display.py`, `gui.py` (`Session`, `main`; `python -m spec_surgeon` runs it), `web/`
    (`server.py`, `shell.py`, `watch.py`: the page's own reports and the watchdog over the page and the job),
    `updater.py`, `settings.py`, `credentials.py`, `winapp.py`.
  - `render/` (Word COM, LibreOffice), `schemas/` (ECMA-376 XSDs), `skill/`, `examples/`.
- `tests/` — pytest; `make_fixtures.py` and `fx_*.py` build synthetic documents; `fake_claude.py` scripts Claude;
  `conftest.py` seals keys, config dir and model; `describe_cases/` feeds `test_describe.py`.
- `docs/` — design notes (`BUILTIN_OPS_BATCH*_DESIGN.md`), plans, `releases/`, `audits/`, `history/`; `TRUST_CLAIMS.md`
  (the ledger behind the trust surfaces).
- `packaging/` — PyInstaller spec, Inno Setup script, installed-app smoke tests, `license_notices.py` (the app's
  LICENSE.txt and its AGPL Corresponding Source). Two licenses, never one: Spec Surgeon's own code is MIT; the
  Windows app bundles PyMuPDF (AGPL-3.0, no commercial license held), so the app as a whole is AGPL v3
  (`about.py`). Never describe the distributed app as MIT-only.

## How to run

```sh
pip install -e ".[dev,gui,agent]"            # Windows: py -3 -m pip install -e ".[dev,gui,agent,word]"
python3 -m pytest -q -n auto                  # every core, as CI; Windows: py -3 -m pytest -q -n auto
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
(`docs/DEVELOPER_DIAGNOSTICS.md`, "The window"); About → Save a diagnostic bundle zips it with the conversations and
the newest runs' records.

## How to add an op

1. Copy `src/spec_surgeon/ops/set_font.py`; keep `@register`, `name`, `mode`, `params_schema`, `select_schema`,
   `verification_profile`, `describe()`, `plan()`, `apply()`; import it at the bottom of `ops/__init__.py`.
   `describe(select, params)` returns one or two sentences built with `ops/prose.py` (inches and points, quoted exact
   text, no identifiers, no JSON): the line that records the change in the history the model hears.
2. `plan()` fills the fragment analytically from the source: `intended_parts`, `diffs`, members, `rev_delta`,
   `property_assertions`, and honest `touches` (what it changes and what it reads).
3. Write the V10 assertion kind in `verify/` first, with its own selection and an "exactly this change" check.
4. `apply()` mutates the working copy through the overlay (`write_xml`, `remove_member`, `add_member`, `edit_rels`).
5. A fixture, an end-to-end test (plan → merge → apply → `run_suite`, `record.passed`), and a planted-defect test for
   every new rule: a wrong output, and a wrong implementation shared by plan and apply.
6. Every new refusal code in `report.FAILURE_EXPLANATIONS`; the op in `compiler.placed_words` if it places typed
   text; a `COMMON_ASSUMPTIONS` entry in `catalog.py`; at least one case in `tests/describe_cases/`.

## The trust surfaces

Help → **Why trust it?** (`frontend/src/components/WhyTrustIt.tsx`) and the "I'm not convinced" dossier it opens
(`TrustDossier.tsx`, its cards in `lib/trustRuntime.ts`, its diagram in `TrustDiagram.tsx`) describe what the app does
action by action. **The trust surfaces are a contract. Any change to a behaviour they describe updates them in the same
change:** a new user action or button, a new network call or destination, a new automatic behaviour (a model call
without its own click, a timer, a background job), a model, effort or caching change, a new or changed limit, or
anything new kept on disk. Update `docs/TRUST_CLAIMS.md` (the ledger: every claim, its source, its test) with it. Every
number the copy quotes lives in `frontend/src/lib/trustFacts.ts`, and `tests/test_trust_facts.py` pins each to its
constant in the code; the copy never types a number itself. A trust document that has drifted from the code is worse
than none.

## Session protocol

Current round: `docs/PLAN-2026-10-22-batch-consistency.md` (what the first live run found); the full-control round,
`docs/PLAN-2026-10-21-full-control.md` (design: `docs/BUILTIN_OPS_BATCH6_DESIGN.md`), waits on its live runs. Read both
before any work and take items from the current round's status table. Beside them, `docs/PLAN-2026-10-22-word-judge.md`
(real Word judges the tracked changes the engine writes and reads; test evidence only) has its own status table. New operations go into a plan before they are
built. Nothing is done on fixtures alone: the plan's acceptance names a real-file run, which the user performs. Before
ending, update the rows you worked in the status table with a dated one-line note: what changed, what is tested, what
still needs the user's machine.
