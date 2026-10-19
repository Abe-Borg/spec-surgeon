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
   `jobs/`, `editspec.py`, `ooxml.py`, `package.py`, `goals.py`, `goal_contracts.py` or the Claude layer
   (`tests/test_independence.py`); it has its own readers, text extraction, selectors, style chains and definitions.
   Do not deduplicate the twin implementations: the duplication is the point.
3. **Repack by member substitution.** `DocxPackage.repack` keeps every untouched member byte-identical, in source
   order; never pretty-print, never regex-edit XML, never `zip -r`; serialize with `serialize_xml()` only.
4. **Never write back blind.** In-place delivery goes through `guard.write_back` and nothing else; it refuses a
   source whose size, mtime or md5 moved, a lock file, or a read-only file. No `force` flag exists and none may be added.
5. **Refusals over guesses.** A case the op cannot predict raises `JobException` with a code and sets the file aside;
   overlapping ops are refused at plan time (`merge_fragments`), never ordered by guess.
6. **No generated characters.** Formatting ops add zero text; content ops place the user's approved wording
   verbatim (`compiler.placed_words` must trace every word to the user's own sources). Claude's words are never the user's.
7. **One specification per edit.** The approved EditSpec (or chain of them) is the only description of a change the
   engine executes; nothing runs that was not planned, previewed and approved as that spec.

## Repository layout

The default desktop is `ui/conversation_window.py`, coordinated by `document_task.py`:
explicit Word attachments, an optional template, and chat. `review_display.py` describes
the actual previewed EditSpec; ordinary requests never invoke a second outcome planner.
Keep operations, recipes, model knobs and recovery workspaces out of this interface.
`ui.app.build_legacy_window` retains the previous UI for integration regression tests.
See `docs/CONVERSATIONAL_DESKTOP.md` for persistence, cost limits and review behavior.

- `src/spec_surgeon/` — the package.
  - `package.py` — `DocxPackage`: zip, rels, sections, working overlay, repack by substitution.
  - `ooxml.py` — transform-side XML helpers (text views, run surgery, revision stamps, schema orders).
  - `ops/` — the operation library; `base.py` (`Op`, `Plan`, `PlanFragment`, `merge_fragments`, `JobException`),
    `selectors.py`, one module per op, shared cores (`textedit.py`, `slots.py`, `effective.py`, `lists.py`, `style_import.py`).
  - `verify/` — the INDEPENDENT verifier: `suite.py` (V1–V10 as a `VerificationRecord`), `props.py` (V10 assertion
    kinds and the residual), `textcheck.py`, `reader.py`, `xsd.py`, `geometry.py`, `explain.py`, one module per family.
  - `jobs/` — the two v0.1.0 CLI jobs, aliases over ops.
  - `editspec.py` — EditSpec load, validate, plan every op, merge, apply.
  - `batch.py` — the per-file pipeline (pool, verify, render, guarded delivery, records); `report.py`, `diagnostics.py`.
  - `guard.py` — acquisition identity, drift, lock file, atomic guarded write-back.
  - `chain.py` — several specs one after another, each stage a verified run on the one before.
  - `profile.py`, `planner.py`, `catalog.py` — per-pattern profiles, dry runs, the op catalog and EditSpec schema.
  - `compiler.py`, `conversation.py`, `llm.py`, `vision.py` — request → EditSpec with Claude; `llm.call` is the one call site.
  - `preview.py` — samples applied and verified in memory, before/after pages, the approval token.
  - `codegen.py`, `sandbox_runner.py` — custom transforms: AST whitelist, isolated process, hard limits.
  - `advisor.py` — a recommendation or re-compiled spec per exception; writes, never applies.
  - `library.py` — recipes, chains, operations and snippets under the config folder.
  - `workflows.py`, `goals.py`, `goal_contracts.py`, `goal_operations.py`, `ordinary_outcomes.py` — named workflows
    and reviewed outcome contracts; `recovery*.py`, `campaign*.py`, `inventory.py` — bounded investigation and workspaces.
  - `office_profiles.py`, `structure.py`, `spec_qa.py`, `project_evidence.py` — office standards, structure analysis, QA audits.
  - `cli.py`, `gui.py` (`Session`, no Qt), `ui/` (the Qt window), `updater.py`, `settings.py`, `credentials.py`, `winapp.py`.
  - `render/` — Word COM and LibreOffice renderers. `schemas/` — vendored ECMA-376 XSDs. `skill/` — the method
    (`SKILL.md`, `references/verification-checklist.md`). `examples/` — the installed copy of the worked examples.
- `tests/` — pytest suite; `make_fixtures.py` builds the synthetic hard-case documents; `fake_claude.py` scripts
  Claude; `conftest.py` seals keys, config dir and the model; `qt_support.py` is the only way a test lets go of a window.
- `examples/specs/`, `examples/templates/`, `examples/jobs/` — worked EditSpecs, template fixtures, job params.
- `docs/` — design notes, `releases/`, `audits/`, `history/` (the previous CLAUDE.md and README.md).
- `packaging/` — PyInstaller spec, Inno Setup script, installed-app smoke tests, screenshot script.

## How to run

```sh
pip install -e ".[dev,gui,agent]"            # Windows: py -3 -m pip install -e ".[dev,gui,agent,word]"
QT_QPA_PLATFORM=offscreen python3 -m pytest -q    # Windows: py -3 -m pytest -q -s
python3 -m pytest -q tests/test_ops.py        # one module
python3 tests/make_fixtures.py OUT            # fixtures; --rich, --template, --reference, --styles-template, --numbering-scheme
spec-surgeon ops --catalog
spec-surgeon plan-spec examples/specs/change-all-font-arial.json FILES...
spec-surgeon run-spec examples/specs/change-all-font-arial.json FILES... --out OUT
spec-surgeon verify SOURCE.docx OUTPUT.docx --plan OUT/plans/NAME.plan.json
spec-surgeon diagnose OUT
spec-surgeon do "change all fonts to Arial" FILES... --out OUT
spec-surgeon-gui                              # or: spec-surgeon gui
```

## How to add an op

1. Copy `src/spec_surgeon/ops/set_font.py`; keep `@register`, `name`, `mode`, `params_schema`, `select_schema`,
   `verification_profile`, `plan()`, `apply()`; import it at the bottom of `ops/__init__.py`.
2. `plan()` fills the fragment analytically from the source: `intended_parts`, `diffs`, members, `rev_delta`,
   `property_assertions`, and honest `touches` (what it changes and what it reads).
3. Add the V10 assertion kind in `verify/` first, with its own selector resolution and "exactly this change" check.
4. `apply()` mutates the working copy through the overlay (`write_xml`, `remove_member`, `add_member`, `edit_rels`).
5. Fixture in `tests/make_fixtures.py`, an end-to-end test (plan → merge → apply → `run_suite`, `record.passed`),
   and a planted-defect test for every new grammar: a wrong output and a wrong implementation shared by plan and apply.
6. Add the op to `compiler.placed_words` if it places text, and a `COMMON_ASSUMPTIONS` entry in `catalog.py`.

## Session protocol

Current round: docs/PLAN-2026-10-18-live-runs.md. Read it before any work. One workstream
item per session, chosen from the plan's status table. Follow its goal, why and expected
outcome. Add no operations, review layers, planners, or documentation beyond what the plan
names. Nothing is 'done' on fixtures alone; the plan's acceptance names a real-file run,
which the user performs. Before ending, update the item's row in the plan's status table
with a dated one-line note: what changed, what's tested, what still needs the user's machine.
