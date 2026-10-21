# CLAUDE.md — Spec Surgeon

## What this is

Spec Surgeon makes surgical edits to Word (.docx) specification files in batches, with an independent
verification record for every file. A deterministic engine plans each change from the source before touching it,
applies it, and checks the result with a verifier that shares no code with the transform; an optional Claude layer
turns plain-English requests into EditSpecs and never edits a file itself. The files go to clients: content silently
lost or invented is the one failure that matters.

## Invariants

1. **Predict first.** `Op.plan()` computes every prediction (diffs, parts, members, revision deltas, assertions and
   their target counts) from the SOURCE, analytically; never by running the transform on a copy and reading off what changed.
2. **The verifier shares no code with the transforms.** `src/spec_surgeon/verify/` imports nothing from `ops/`,
   `editspec.py`, `ooxml.py`, `package.py`, `goals.py`, `goal_contracts.py` or the Claude layer
   (`tests/test_independence.py`); it has its own readers, text extraction, selectors, style chains and definitions.
   Do not deduplicate the twin implementations: the duplication is the point.
3. **Repack by member substitution.** `DocxPackage.repack` keeps every untouched member byte-identical, in source
   order; never pretty-print, never regex-edit XML, never `zip -r`; serialize with `serialize_xml()` only.
4. **Never write back blind.** In-place delivery goes through `guard.write_back` and nothing else; it refuses a
   source whose size, mtime or md5 moved, a lock file, or a read-only file. No `force` flag exists and none may be added.
5. **Refusals over guesses.** A case the op cannot predict raises `JobException` with a code and sets the file aside;
   overlapping ops are refused at plan time (`merge_fragments`), never ordered by guess. A submission whose ops
   overlap is composed by the engine into ordered steps in the user's own order (`editspec.stage_cuts`, a chain whose
   every step is planned on the step before it), never reordered: a source-bound template op not listed first is
   refused, not moved.
6. **No generated characters.** Formatting ops add zero text; content ops place the user's approved wording
   verbatim (`compiler.placed_words` must trace every word to the user's own sources). Claude's words are never the
   user's: a tapped suggested reply is the user's decision, never approved wording.
7. **One specification per edit.** The approved EditSpec (or chain of them) is the only description of a change the
   engine executes; nothing runs that was not planned, previewed and approved as that spec.

## Repository layout

The desktop is one window — the page in `web/static/`, served by `web/server.py` and shown by
`web/shell.py` — coordinated by `document_task.py`:
explicit Word attachments, an optional template, explicitly attached pictures and tables for the
edit to use (INPUT FILES; nothing beside them is discovered), and chat. `triage.py` checks every
document the moment it arrives (deterministic, no model call) and refuses or notes it in one
sentence. The window has no spending ceiling: every call gets the model's full output (the
conversation at medium effort, `CONVERSATION_EFFORT`; recovery at high), work pauses every 48
calls (`STEPS_PER_PAUSE`), and **Stop now** closes the running stream (`llm.cancellation_scope`);
`llm.call` re-sends transient failures and relays each call's stream to the window
(`llm.stream_observer_scope`). After a run the delivered copies are the working set the next
request builds on (`DocumentTask.working`, `history`, `START_OVER`); the model's
`suggested_replies` are the chat's tap-to-reply chips.
`layouts.py` gives a mixed batch one EditSpec per structural layout, each planned, previewed
and verified on its own documents, with one approval. `review_display.py` describes the actual
previewed EditSpec in the sentences each operation writes for itself (`Op.describe`,
`ops/prose.py`); ordinary requests never invoke a second outcome planner. Keep operations,
model knobs and recovery internals out of this interface. There is no legacy window, no other
mode and no command line (removed 2026-10-21 with the library, workspaces, workflows, office
profiles, spec QA audits, project evidence, evaluations and the named v0.1.0 jobs). See
`docs/CONVERSATIONAL_DESKTOP.md` for persistence, cost limits and review behavior.

- `src/spec_surgeon/` — the package.
  - `package.py` — `DocxPackage`: zip, rels, sections, working overlay, repack by substitution.
  - `ooxml.py` — transform-side XML helpers (text views, run surgery, revision stamps, schema orders).
  - `ops/` — the operation library; `base.py` (`Op`, `Plan`, `PlanFragment`, `merge_fragments`, `JobException`),
    `selectors.py`, `prose.py` (the words of `describe()`: inches, points, quoted text), one module per op, shared
    cores (`textedit.py`, `slots.py`, `effective.py`, `lists.py`, `style_import.py`).
  - `verify/` — the INDEPENDENT verifier: `suite.py` (V1–V10 as a `VerificationRecord`), `props.py` (V10 assertion
    kinds and the residual), `textcheck.py`, `reader.py`, `xsd.py`, `geometry.py`, `explain.py`, one module per family.
  - `editspec.py` — EditSpec load, validate, plan every op, merge, apply; composition (`stage_cuts`, `compose_chain`,
    `merge_cuts`, `plan_chain`): a spec whose ops overlap becomes a chain of ordered steps, each planned on the
    step before it.
  - `layouts.py` — a layout plan: one EditSpec per structure pattern, routing, per-layout preview, run and approval token.
  - `batch.py` — the per-file pipeline (pool, verify, render, guarded delivery, records); `report.py`, `diagnostics.py`.
  - `guard.py` — acquisition identity, drift, lock file, atomic guarded write-back.
  - `chain.py` — several specs one after another, each stage a verified run on the one before.
  - `profile.py`, `planner.py`, `catalog.py` — per-pattern profiles (with `structure.role_summary` as role evidence),
    dry runs, the op catalog and EditSpec schema.
  - `triage.py` — attach-time triage: refused, noted or ok, with the facts the planner reads.
  - `compiler.py`, `conversation.py`, `llm.py`, `vision.py` — request → EditSpec with Claude; `llm.call` is the one
    call site: the system prompt carries its own 1-hour cache breakpoint (shared by every conversation), the history
    the automatic 5-minute one, and a `one_shot` call (visual check, critic) caches nothing after the system prompt;
    keep system prompts and tools byte-stable. `compiler.compose_submission` is the one gate a submission passes (a spec, the model's own stages, a
    layout plan — composed into a chain when its ops overlap); the op sheet (`catalog.op_sheet`) rides the cached
    system prompt.
  - `preview.py` — samples applied and verified in memory, before/after pages, the approval token.
  - `codegen.py`, `sandbox_runner.py` — custom transforms: AST whitelist, isolated process, hard limits.
  - `goals.py`, `goal_contracts.py`, `goal_operations.py` — reviewed outcome contracts; `recovery*.py`,
    `continuous_recovery.py`, `campaign_planner.py` — bounded investigation of set-aside files and outcome planning;
    `inventory.py` — the content digest that pins a file.
  - `structure.py` — structure analysis (paragraph roles, the planner's role evidence).
  - `document_task.py` — the window's coordinator: attachments and triage, stages, the call ledger and its pause,
    Continue that resumes a stopped conversation turn, the journal;
    `review_display.py` — the plan as the window reads it (sentences, assumptions, layouts, targets).
  - `gui.py` (`Session`, `main`; `python -m spec_surgeon` runs it), `web/` (the window: `server.py` (the FastAPI API over one
    `DocumentTask`, contract `docs/WEB_API.md`), `shell.py` (pywebview/WebView2, the browser fallback),
    `updates.py` (the headless update flow), `static/` (the page, built from `frontend/` with Node 22 and
    committed), `icons/`), `updater.py`, `settings.py`, `credentials.py`, `winapp.py`.
  - `render/` — Word COM and LibreOffice renderers. `schemas/` — vendored ECMA-376 XSDs. `skill/` — the method
    (`SKILL.md`, `references/verification-checklist.md`). `examples/` — the installed copy of the worked examples.
- `tests/` — pytest suite; `make_fixtures.py` builds the synthetic hard-case documents; `fake_claude.py` scripts
  Claude; `conftest.py` seals keys, config dir and the model; `test_web_server.py` drives the window's server
  in-process; `describe_cases/` holds the (select, params) cases `test_describe.py` checks for every op.
- `examples/specs/`, `examples/templates/` — worked EditSpecs and template fixtures.
- `docs/` — design notes, `releases/`, `audits/`, `history/` (the previous CLAUDE.md and README.md).
- `packaging/` — PyInstaller spec, Inno Setup script, installed-app smoke tests, the icon generator.

## How to run

```sh
pip install -e ".[dev,gui,agent]"            # Windows: py -3 -m pip install -e ".[dev,gui,agent,word]"
python3 -m pytest -q                          # Windows: py -3 -m pytest -q -s (installed editable: no PYTHONPATH needed)
cd frontend && npm ci && npm run build        # rebuild the window's page (Node 22) into src/spec_surgeon/web/static; commit it
python3 -m pytest -q tests/test_ops.py        # one module
python3 tests/make_fixtures.py OUT            # fixtures; --rich, --template, --reference, --styles-template, --numbering-scheme, --dirty
spec-surgeon-gui                              # the window; or: python3 -m spec_surgeon
```

There is no command line. Check a change with the test suite; to run the engine by hand, call it from Python:
`batch.run_batch(files, "edit-spec", {}, out_dir, spec=editspec.load_spec(path))` for a batch, `verify.run_suite`
for one pair, `gui.Session` for the window's steps with a scripted client (`tests/fake_claude.py`). A run's
`DIAGNOSTICS.md`, `records/` and `logs/` say why a file did not pass.

## How to add an op

1. Copy `src/spec_surgeon/ops/set_font.py`; keep `@register`, `name`, `mode`, `params_schema`, `select_schema`,
   `verification_profile`, `describe()`, `plan()`, `apply()`; import it at the bottom of `ops/__init__.py`.
   `describe(select, params)` returns one or two sentences built with `ops/prose.py` (inches and points, quoted exact
   text, no identifiers, no JSON); it is the line the user reads in the plan.
2. `plan()` fills the fragment analytically from the source: `intended_parts`, `diffs`, members, `rev_delta`,
   `property_assertions`, and honest `touches` (what it changes and what it reads).
3. Add the V10 assertion kind in `verify/` first, with its own selector resolution and "exactly this change" check.
4. `apply()` mutates the working copy through the overlay (`write_xml`, `remove_member`, `add_member`, `edit_rels`).
5. Fixture in `tests/make_fixtures.py`, an end-to-end test (plan → merge → apply → `run_suite`, `record.passed`),
   and a planted-defect test for every new grammar: a wrong output and a wrong implementation shared by plan and apply.
6. Add the op to `compiler.placed_words` if it places text, a `COMMON_ASSUMPTIONS` entry in `catalog.py`, and at
   least one case in `tests/describe_cases/` (`test_describe.py` fails for an op without one).

## Session protocol

Current round: docs/PLAN-2026-10-20-one-conversation.md (docs/PLAN-2026-10-19-ez-mode.md before it). Read it before any work. One workstream
item per session, chosen from the plan's status table. Follow its goal, why and expected
outcome. Add no operations, review layers, planners, or documentation beyond what the plan
names. Nothing is 'done' on fixtures alone; the plan's acceptance names a real-file run,
which the user performs. Before ending, update the item's row in the plan's status table
with a dated one-line note: what changed, what's tested, what still needs the user's machine.
