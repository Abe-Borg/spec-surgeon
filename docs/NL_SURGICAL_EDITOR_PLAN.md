# Spec Surgeon NL — natural-language surgical editing of .docx specs at scale

Design and implementation plan. Written 2026-10-13 for a follow-on build session. Assumes the Spec Surgeon v0.1.0 codebase (this repo) and the bundled `docx-surgical-edit` skill (`src/spec_surgeon/skill/`).

---

## 0. The decision in one paragraph

Natural-language surgical editing works at scale if the model is used as a **compiler, not an executor**. The user's sentence ("remove all headers/footers", "change all font to Arial", "make the header/footer look like this template") is compiled once per batch into an **EditSpec** — a JSON program over a library of deterministic, individually verified **operations**. The engine then plans, applies, and verifies that spec on every file in milliseconds, with the same nine-check record v0.1.0 already produces. The model never touches XML per file. Requests the op library cannot express fall through to a sandboxed path where the model writes a transform against the engine's API and the verifier is its test; requests that need judgment per occurrence (rewording) become propose-and-approve lists. Dozens of files costs one to three model calls and a few minutes of rendering. Roughly 85% of v0.1.0 carries over unchanged; the new work is the operation library, the EditSpec planner/verifier extensions, the compiler, and the preview flow.

---

## 1. Goals and non-goals

**Goals**

- Input: a plain-English request, a set of .docx files (1–100+), optionally a template or example file.
- Output: edited files that are provably changed only as stated, a per-file verification record, a batch report, a preview the user approves before anything is written, and guarded in-place write-back.
- Flexibility: formatting edits (fonts, styles, spacing, headers/footers, page setup, numbering, sections), structural edits (remove/insert paragraphs, parts, sections), and text edits (exact replacements; proposed rewordings approved by the user) — composable.
- Scale: the per-file cost is engine time, not model time. Files sharing a structure pattern share a compiled spec.
- Safety: predict-first, independent verifier, no generated characters without approval, overwrite guard. Nothing in this plan weakens any check in v0.1.0.

**Non-goals**

- Aesthetic judgment ("make it look nicer") — not surgical, not verifiable.
- Free rewriting of content at scale without approval — violates the contract.
- Perfect fidelity without Word — LibreOffice is the fallback renderer; Word via COM is the truth on the user's machine.
- Legacy/odd constructs (VML drawings, embedded OLE, macros) — detect and route to exceptions, don't edit.

---

## 2. Architecture — compile once, run many

```
                 ┌──────────────┐
 user request ──►│  PROFILER    │  fingerprint + document profile per pattern (ms/file, no model)
 + files        │              │
 + template ──► └──────┬───────┘
                       ▼
                ┌──────────────┐   tools: inspect_profile, list_styles, sample_paragraphs,
                │  COMPILER    │   show_part, read_template, ask_user
                │  (Claude)    │──► EditSpec (JSON) + assumptions + questions + plain-English summary
                └──────┬───────┘   one call per batch (one per structure pattern if they differ)
                       ▼
                ┌──────────────┐
                │  PLANNER     │  EditSpec × file → Plan (predicted diffs, parts, members, rev delta)
                │  (engine)    │  deterministic; raises JobException instead of guessing
                └──────┬───────┘
                       ▼
                ┌──────────────┐
                │  PREVIEW     │  per-file plan summary + before/after render of 1–2 sample files
                │  (GUI/CLI)   │  user approves, edits assumptions, or asks again
                └──────┬───────┘
                       ▼
       ┌──────────────────────────────────┐
       │ EXECUTOR → VERIFIER → RENDER →    │  v0.1.0 batch.py, per file, parallel
       │ GEOMETRY → GUARDED DELIVERY       │
       └──────────────┬───────────────────┘
                      ▼
             records/ + BATCH_REPORT.md ; exceptions/ → advisor (Claude) or human ; Tier B codegen
```

**Components**

| Component | New / reused | Role |
|---|---|---|
| Profiler | extend `fingerprint.py` | per-file fingerprint (exists) + per-pattern *profile*: section layout, header/footer text and fields, styles in use with their fonts/sizes, docDefaults/theme fonts, numbering definitions, sample paragraphs per style, counts. Small enough to put in a prompt (target < 4 KB per pattern). |
| Compiler | new (`compiler.py`) | Claude with tool use. Input: request, profiles, op catalog (auto-generated from the op classes), project memory. Output: EditSpec. Must state assumptions and ask when ambiguous. |
| Op library | new (`ops/`), absorbs `jobs/` | each op = `plan(pkg, selector, params) → PlanFragment` + `apply(pkg, fragment) → parts`. Composable; fragments merge into one Plan. |
| Planner | new (`planner.py`) | resolves selectors, runs each op's plan on the file, merges fragments (conflict detection: two ops touching the same block/part must be ordered or rejected), produces the Plan v0.1.0's verifier already understands plus new fields (§5). |
| Executor | reuse `batch.py` | job = "execute EditSpec" instead of a named job. |
| Verifier | extend `verify/` | §5. |
| Preview | new | sample-apply + render + side-by-side images; plan summaries. |
| Advisor | rework `agent.py` | exceptions → recommendation or a re-compiled spec for that pattern. |
| Tier B codegen | new (`codegen.py`) | §8. |
| Guard, render, report, CLI | reuse | unchanged or lightly extended. |

---

## 3. The EditSpec

A JSON document the compiler produces and the planner executes. Everything a user could veto is explicit in it.

```json
{
  "request": "remove all headers and footers",
  "summary": "Remove every header and footer part from every section of each document; leave page setup, body text and all tracked changes untouched.",
  "assumptions": ["'all' means every section, including first-page and even-page variants",
                  "section page margins are left as they are (the header/footer space collapses)"],
  "questions": [],
  "applies_to": {"pattern_ids": ["b20e6c1da8"], "or": "all"},
  "ops": [
    {"op": "remove_header_footer", "select": {"sections": "all", "kinds": ["header", "footer"], "types": ["default", "first", "even"]},
     "params": {}, "expect": {"members_removed": "header/footer parts and their rels/media not referenced elsewhere", "text_diffs": "deleted blocks in removed parts only"}}
  ],
  "mode": "formatting",
  "review": {"sample_files": 2, "geometry_reference": "source"}
}
```

Three more examples, abbreviated:

*"change all font to Arial"*

```json
{"ops": [
  {"op": "set_font", "select": {"scope": ["document", "headers", "footers", "footnotes"], "runs": "all"},
   "params": {"ascii": "Arial", "hAnsi": "Arial", "cs": "Arial", "eastAsia": null, "clear_theme_fonts": true}},
  {"op": "set_style_font", "select": {"styles": "all-paragraph-and-character"}, "params": {"font": "Arial"}},
  {"op": "set_doc_defaults_font", "params": {"font": "Arial"}}
 ],
 "assumptions": ["Symbol/Wingdings runs (bullets, special characters) are left alone — changing them would change the glyphs",
                 "eastAsia font slot left unchanged", "font size unchanged"],
 "mode": "formatting"}
```

*"make the header/footer look like this template"* (template attached)

```json
{"ops": [
  {"op": "apply_header_footer_template", "select": {"sections": "all"},
   "params": {"template": "00_00_00_TEMPLATE.docx", "template_sections": {"cover": 0, "body": 1},
              "slot_fills": {"SECTION_NUMBER": {"rule": "regex", "source": "footer.default", "pattern": "^(\\d{2} \\d{2} \\d{2})"},
                             "SECTION_TITLE": {"rule": "paragraph_after", "source": "document", "anchor_regex": "^SECTION \\d{2} \\d{2} \\d{2}$"},
                             "ISSUE_STAMP": {"rule": "literal", "value": "ISSUED FOR CONSTRUCTION  2026-02-15"}},
              "rescale_tabs_to_body_width": true, "pin_spacing": true, "import_styles": ["HeaderText", "FooterText"]}}
 ],
 "questions": ["The template's stamp reads 'ISSUED FOR PERMIT'. Which stamp should these files get?"],
 "review": {"sample_files": 2, "geometry_reference": "template"}}
```

*"change FM Global to the GRC wording everywhere"* (content)

```json
{"ops": [{"op": "replace_text", "select": {"scope": ["document", "headers", "footers"]},
          "params": {"find": "FM Global", "replace": "the Owner's risk consultant – GRC (Global Risk Consultants)", "tracked": true}}],
 "mode": "content", "questions": []}
```

**Selectors** (resolved by the planner, never by the model):

- `scope`: part kinds — `document | headers | footers | footnotes | endnotes | comments`.
- `sections`: `all | [indices] | {"cover": true} | {"has_pgnum_restart": true}`.
- `paragraphs`: predicates — `style_in`, `text_regex`, `numbering_level`, `in_table`, `after_anchor_regex`, `index_range`, `is_empty`.
- `runs`: `all | {"font_in": [...]} | {"bold": true} | {"not_in_revisions": true}`.
- `styles`: `all | by_id | by_type`.
- `parts`: explicit part names only when they came from the profile (the compiler sees resolved names; it never invents them).

**Rules the schema enforces**

- Every op has an `expect` block the planner must reproduce from the file (member removals, text-diff shape, revision delta shape). A mismatch at plan time is an exception, not a surprise at verify time.
- `mode` is formatting unless any op generates characters; then every such character must be in `params` verbatim (came from the user or a template), and the op is tracked unless the user said otherwise.
- `questions` non-empty ⇒ nothing runs until answered.

---

## 4. Operation library v1

Each op is a module under `src/spec_surgeon/ops/` with: `name`, `mode`, `params_schema`, `plan(pkg, selector, params) → PlanFragment`, `apply(pkg, fragment) → {part: bytes}`, and a `verification_profile` describing which checks it affects and how. v0.1.0's two jobs become ops (`page_numbering` → `fix_page_numbering`; `tracked_replace` → `replace_text`). `ooxml.py` grows the shared primitives.

| Op | Mode | What it does | Predicted effects | Lessons that bite |
|---|---|---|---|---|
| `remove_header_footer` | formatting | delete header/footer references from selected sections; drop parts + rels + content-type overrides + media no longer referenced | members removed; text diffs = deleted blocks in removed parts; rev delta −(revisions inside them), flagged | parts resolved via rels; titlePg/evenAndOddHeaders; pending revisions baked/dropped flagged by name |
| `apply_header_footer_template` | formatting | copy template header/footer parts into target sections, fill slots by rule, rescale tab stops, pin spacing, import listed styles, keep revision-history tables | members added; text diffs = template blocks with fills; geometry vs template render | the whole templating playbook; fills extracted never typed; never import Normal; collisions; placeholder fragmentation |
| `set_font` | formatting | set `w:rFonts` on selected runs (ascii/hAnsi/cs; eastAsia optional); optionally clear theme-font attributes | zero text diffs; parts touched; rPr changes counted | Symbol/Wingdings runs; runs inside revisions untouched or tracked via rPrChange (decide per spec); `w:sym` runs |
| `set_style_font` / `set_style_props` | formatting | change rFonts/size/spacing/indent on styles | zero text diffs; `styles.xml` only | effective chain docDefaults→Normal→style; LibreOffice spacing pitch |
| `set_doc_defaults_font` | formatting | `docDefaults/rPrDefault` | `styles.xml` only | theme fonts vs explicit |
| `replace_text` | content/untracked | v0.1.0 tracked-replace | as today | as today |
| `delete_paragraphs` | content/untracked | tracked (del on runs + paragraph mark) or untracked deletion of selected paragraphs | deleted/merged blocks; rev delta | paragraph-mark revision grammar; sectPr inside pPr must be preserved; numbering consumed by empty paragraphs |
| `insert_paragraph` | content | insert user-supplied text (verbatim) after an anchor, tracked, with a given style | inserted block; +1 ins (+mark) | xml:space; pPr order |
| `fix_page_numbering` | formatting | v0.1.0 job | as today | as today |
| `set_page_setup` | formatting | margins, orientation, size per section | zero text diffs | sectPr child order |
| `set_paragraph_props` | formatting | spacing/indent/alignment on selected paragraphs, optionally tracked via pPrChange | zero text diffs | complete pPr order table; merge into existing spacing |
| `renumber_scheme` | formatting | numbering-conversion playbook (styles bound to a cloned abstractNum) | zero text diffs; `numbering.xml`, `styles.xml`, pStyle changes | stale cross-references flagged; empty numbered paragraphs |
| `set_issue_stamp` | untracked/formatting | sugar over `replace_text` in headers/footers with `allow_missing` + flag | as replace_text | per-project stamp confirmation |
| `remove_section_breaks` / `merge_sections` | formatting | remove selected sectPr (paragraphs join the following section) | zero text diffs; header inheritance change flagged | inheritance rules |
| `strip_comments` | content | remove comment anchors + all comments parts | members removed; text diffs in comments part | four comments parts must go together |
| `accept_or_reject_revisions` | content | **off by default; requires explicit user instruction** | large rev delta | the durable default says the user does this in Word |

Ops the compiler may *compose*: `set_font` + `set_style_font` + `set_doc_defaults_font` is what "all font to Arial" means in practice; the compiler knows that from the op docs, and the assumptions make the composition visible.

---

## 5. Verifier extensions

v0.1.0's suite stays; the Plan gains fields and the checks learn to read them. Nothing becomes weaker.

| Check | Extension |
|---|---|
| V1 | `members_removed`, `members_added` in the plan; assert the actual set equals them exactly (today: nothing added/removed). `[Content_Types].xml` and `.rels` changes must be predicted too. |
| V2 | text scope = union of source and output text parts; a removed part's blocks are predicted deletions; an added part's blocks are predicted insertions with their exact text (template blocks with fills). |
| V3 | unchanged; template parts declare their structural counts. |
| V4 | unchanged; ops that drop revisions predict negative deltas per region (already supported by the plan field). |
| V5a | validate added parts against the baseline of the *template* when they came from it. |
| V5b | unchanged for content ops; for formatting ops: no new revision ids unless the op declared tracked property changes (`pPrChange`/`rPrChange` ids are counted separately). |
| **V10 — Property assertion (new)** | for formatting ops: assert the stated property on every selected target and *no* property change elsewhere: e.g. every selected run's effective font == Arial, and the multiset of rPr/pPr signatures of unselected runs/paragraphs is unchanged. Computed independently in `verify/props.py`. This is the check that makes "change all font" provable. |
| V7 | geometry reference becomes a plan field: `source` (default), `template` (anchor words in header/footer regions), or `none` with reason (a font change reflows everything). |
| V8 | unchanged. |
| V9 | unchanged. |

---

## 6. The compiler

**Inputs**: the request; the per-pattern profiles (§2); the op catalog (generated from the op classes: name, description, params schema, selector grammar, the `expect` contract, the assumptions each op commonly needs); project memory (per-project setup answers from the skill — template file, stamp, author, numbering exemplar); the template/example file profile if one was attached.

**Tools** (tool-use loop, max ~8 calls): `inspect_profile(pattern_id)`, `list_styles(file)`, `sample_paragraphs(file, style|regex, n)`, `show_part_text(file, part)`, `read_template(template)`, `ask_user(question)` (ends the turn; the GUI relays the answer).

**Output contract**: an EditSpec validated against a JSON schema. Rejected outputs (schema failure, unknown op, invented part name, generated text not traceable to the request/template) are returned to the model once with the error; a second failure is an exception to the user.

**Ambiguity policy**: the model must either put an assumption in `assumptions` (the user sees and can veto it in the preview) or a question in `questions` (nothing runs). Over-asking is the lesser failure; the preview makes assumptions cheap to correct.

**Per-pattern specs**: the profiler groups files by pattern; if the model's spec declares `applies_to` for one pattern, the planner compiles the remaining patterns separately (another call with the first spec as context). In practice one spec covers a project's specs because they share a template.

**Model and cost**: Sonnet-class is enough for most compilation; Opus/Fable for templating and Tier B. One to three calls per batch, 5–30 s each. The per-file cost stays zero.

**Prompt material**: the skill's `SKILL.md` contract and the relevant lessons; the op docs; two or three worked examples of request → EditSpec (the ones in §3).

---

## 7. Preview and approval

Before anything is applied to the batch:

1. **Plan summary per file** (from the planner, no model): "header1/2/3.xml removed; 0 text changes outside removed parts; 2 pending revisions in footer2.xml will be dropped — flagged". For dozens of files: totals + the exceptions on top.
2. **Sample render**: apply to 1–2 representative files (one per pattern), render source and output, show first page and a changed page side by side. Word on the user's machine when available.
3. **Assumptions and questions** from the spec, editable; editing an assumption re-plans (no model call) or re-compiles (if it changes the request's meaning).
4. **Approve** → the batch runs; **Reject** → back to the request with the user's note appended.

Pre-flight fingerprinting shows "58 of 60 match pattern A; 2 are different" before the model is even called.

---

## 8. Tier B — generated transforms for requests the library cannot express

When the compiler cannot express a request with existing ops (it says so explicitly: `{"unsupported": "...", "proposal": "..."}`), the user can allow Tier B:

1. The model writes a transform module against a **restricted API**: `DocxPackage`, `ooxml` helpers, the `Op` base class, lxml. No file system, no network, no subprocess — the module is executed in a subprocess with a timeout, on copies, under a minimal import whitelist (AST-checked before execution).
2. The module must implement `plan()` and `apply()` like any op — predict-first is not optional for generated code.
3. The engine runs it on the sample files with the **full suite as the test**; failures (with the record) go back to the model, up to N iterations (3).
4. On pass: preview as in §7; on approval: the batch. The generated op is saved next to the batch's records (`generated_ops/<hash>.py`) with its verification history — a cache, not a promoted module. Promotion to `ops/` is a human decision after review, with tests.
5. Hard limits: no generated characters except from params; no `accept_all`/`reject_all`; no edits to `settings.xml` `trackRevisions`; no deletion of members not predicted.

This is the user's standing preference ("write the scripts fresh, don't persist them") applied where it belongs: Tier B scripts are fresh per batch, verified per file, and only become permanent code after review.

---

## 9. Scale and performance budget

| Stage | Cost | Scales with |
|---|---|---|
| fingerprint + profile | ~5–20 ms/file | files |
| compile | 1–3 model calls, 5–30 s each | patterns, not files |
| plan + apply + verify (V1–V5, V8, V10) | 30–150 ms/file | files × document size |
| render (source + output) | 0.2–2 s/file LibreOffice batched, 1–3 s/file Word | files × pages |
| preview | 2 sample renders + human look | constant |
| deliver | ms/file | files |

Sixty files: under a minute of machine time plus the time the user spends on the preview. Rendering is optional on repeat batches of a proven spec (`--no-render` → V6/V7 N/A, V10 still proves the property).

---

## 10. Flexibility and limits — what the user can realistically ask for

| Request class | Feasible? | Control given | Caveat |
|---|---|---|---|
| Remove/replace headers, footers, cover elements | yes, deterministic | per section/type, keep or drop revisions inside | shared parts across sections are detected; template fills need rules |
| Fonts, sizes, spacing, indents, alignment, styles | yes, deterministic, provable by V10 | scope, selectors, theme vs explicit, tracked or not | symbol fonts excluded by default; LibreOffice preview pitch differs from Word |
| Page setup, section breaks, page numbering | yes | per section | header inheritance side effects flagged |
| Numbering scheme conversion | yes (playbook exists) | exemplar-driven | stale literal cross-references flagged, not edited |
| Exact text replacement, tracked | yes (exists) | scope, occurrences, case, tracked/untracked | text inside fields never edited |
| Delete/insert paragraphs with given text | yes | tracked, anchored | paragraph-mark grammar |
| "Reword X to sound better", "fix grammar" | only as propose-and-approve: the model lists before→after per occurrence; the user approves; then it runs as `replace_text` | per occurrence | not a one-click batch; by design |
| "Make it look like this" beyond headers/footers (whole-document restyle) | partially: styles + docDefaults + page setup from the template | | layout differences beyond properties are not expressible |
| Images, tables structure, content controls | later ops; traps documented in the skill's `element-types.md` | | |
| Anything judged visually ("nicer", "cleaner") | no | | not surgical |

---

## 11. What carries over from v0.1.0

| v0.1.0 file | Use in NL editor |
|---|---|
| `package.py` | as is (add helpers: remove member, add member + content-type override, rels editing) |
| `ooxml.py` | as is; grows with each op (rPr/rFonts helpers, pPr/rPr change tracking, sectPr editing, style cloning) |
| `fingerprint.py` | as is + profile generation |
| `guard.py` | as is |
| `verify/*` | as is + plan fields for members, text-scope union, `props.py` (V10), geometry reference modes |
| `render/*`, `verify/geometry.py` | as is |
| `batch.py`, `report.py` | as is; "job" becomes "EditSpec"; preview hook before the full run |
| `jobs/page_numbering.py`, `jobs/tracked_replace.py` | become ops; `tracked_replace`'s run surgery is the core of every text op |
| `jobs/base.py` | becomes `ops/base.py` (`PlanFragment` + merge) |
| `agent.py` | replaced by `compiler.py` (NL → EditSpec with tools) and `advisor.py` (exceptions) |
| `cli.py` | extended: `spec-surgeon do "request" FILES… [--template T] [--approve]` |
| `skill/` | prompt material for the compiler and Tier B; unchanged |
| `tests/make_fixtures.py` | extended with per-op fixtures (symbol-font runs, theme fonts, first/even headers, shared footer parts, template file) |

Estimate: ~85% of the code and all of the method carry over. The verifier independence rule and the planted-defect test pattern apply to every new op.

---

## 12. Implementation plan

Work in the order below; each phase ends with the full test suite green and the README updated. Estimates are rough and assume a Claude Code session with the repo and this plan.

### Phase 1 — Op library foundation (2–3 sessions)

1. `ops/base.py`: `Op`, `PlanFragment` (same fields as `Plan` plus `members_removed/added`, `property_assertions`), `merge_fragments()` with conflict detection (same part+block touched by two ops → ordered application or exception).
2. Move `page_numbering` and `tracked_replace` into `ops/` as `fix_page_numbering` and `replace_text`; keep `jobs/` as thin aliases so the v0.1.0 CLI keeps working.
3. `package.py`: `remove_member`, `add_member(name, data, content_type)`, `edit_rels(part, add=…, remove=…)`, `unreferenced_media()`.
4. Ops: `remove_header_footer`, `set_font`, `set_style_font`, `set_doc_defaults_font`, `set_paragraph_props`, `set_page_setup`.
5. Verifier: plan fields for members; text-scope union; `verify/props.py` (V10); V7 reference modes.
6. Fixtures: first/even headers, shared footer part, theme fonts + explicit fonts + a Wingdings bullet run, runs inside revisions.
7. Tests per op (pass) + planted defects (a run outside the selector changed; a symbol run changed; a media file dropped that was still referenced).

**Deliverable**: `spec-surgeon run-spec spec.json FILES…` executes a hand-written EditSpec end to end.

**Status: done (2026-10-13).** See "Phase 1 status" after §14 for what was built and every departure from this plan.

### Phase 2 — Templating op (2–3 sessions)

1. `apply_header_footer_template` per the playbook: part copy with rels/media/content types, slot detection by marker formatting, fill rules (`regex`, `paragraph_after`, `literal`, `from_footer`), tab-stop rescale, spacing pin, style import with collision check and mc-ignorable stripping, revision-history table preserved.
2. V7 with `geometry_reference: template` (anchor words from header/footer regions).
3. Fixtures: a template file with highlighted slots; targets with differing margins; a target whose footer carries a pending revision.
4. Tests + planted defects (a fill typed instead of extracted → V2 catches; a missing content-type override → V5a/Word-repair simulation).

**Status: done (2026-10-13).** See "Phase 2 status" after "Phase 1 status" for what was built and every departure from this plan.

### Phase 3 — Profiler + planner + CLI (1–2 sessions)

1. `profile.py`: per-pattern profile (bounded size), JSON.
2. `planner.py`: EditSpec → Plan per file; selector resolution; `applies_to` by pattern; exceptions with the same diagnosis shape as today.
3. EditSpec JSON schema + validator; op catalog generator (`spec-surgeon ops --catalog`).
4. CLI: `plan-spec`, `run-spec`, `profile`.

**Status: done (2026-10-13).** See "Phase 3 status" below.

### Phase 4 — Compiler + preview (2 sessions)

1. `compiler.py`: tool-use loop with the tools in §6; output validation and one retry; assumption/question policy.
2. `preview.py`: sample selection (one file per pattern), apply+render, side-by-side PNGs, plan summaries, approval token.
3. CLI: `spec-surgeon do "request" FILES… [--template T] [--approve]` — compiles, previews, waits (or auto-approves with `--approve` when there are no questions), runs.
4. NL evaluation set (§13) and a test that compiles each request and compares the resulting EditSpec's ops/selectors (not exact JSON) to expectations.

**Status: done (2026-10-13)** except the live NL evaluation set — see "Phase 4 status" below.

### Phase 5 — Tier B codegen (2–3 sessions)

1. Restricted API surface + AST whitelist + subprocess runner with timeout.
2. Loop: generate → sample-run with full suite → feedback → max 3 iterations.
3. Cache and promotion path; report section listing generated ops used.
4. Safety tests: generated code that imports `os` is rejected; a transform that drops a tab fails V3 and is returned.

**Status: done (2026-10-13)** — see "Phase 5 status" below.

### Phase 6 — Advisor, exceptions, hardening (ongoing)

1. `advisor.py`: exception → recommendation or re-compiled spec limited to that file's pattern.
2. Real-file corpus from past projects (CLT11, AVC11, LVL16 specs) run read-only (`plan-spec`, `--no-render`) to find structures the fixtures lack; add each to fixtures.
3. Word COM path exercised on the user's machine; promote the skill's [unproven] marks that hold.

**Status:** item 1 done (2026-10-13); items 2–3 need the user's machine — see "Phase 6 status" below.

### Phase 7 — GUI (the user's)

Needs from the engine: `profile` for the pre-flight message, `compile` returning the spec + questions, `preview` returning images and summaries, `run_batch` with progress, `records` to display. All are plain Python calls; the `__main__` guard for the process pool on Windows.

**Status: first cut done (2026-10-13)** — `gui.py`, a single Tk window (`spec-surgeon-gui`): specs folder, optional template, results folder, request or spec file, answers, API key (session only), options (render, guarded in-place, AI-written transforms), Preview / Load spec file / Run / Open results folder / Advise on exceptions, and a log. `gui.Session` holds the logic and is tested without widgets; the real window is driven under Xvfb in the container. Abraham chose to skip the real-file sweep, the live API run and the Word check for now and start using the window; those happen through it. The `spec_surgeon/__main__.py` guard (missing until now) was added for `python -m spec_surgeon` with the process pool on Windows.

**Status: second cut done (v0.4.0, 2026-10-14)** — Abraham: "the gui looks like shit … this is going to be a windows app that my users can install … auto update, and also a button … to check for updates."
- **The window** was rebuilt in Qt 6 (PySide6-Essentials, the `gui` extra; `ui/`). It has four steps (Files → Describe the change → Preview → Run & results), Preview and Results tables with plain-English reasons, the sample before/after pages in the app, a progress bar, errors shown on the step that caused them, a collapsible activity log, light/dark themes and remembered settings (`settings.json`). The API key can be kept in Windows Credential Manager.
- **Unchanged:** `gui.Session`, apart from keeping the last results for the tables.
- **Updates** (`updater.py`, `ui/update_ui.py`, `installer.iss`) follow Abraham's choices: notify with one-click install, unsigned for now with SHA-256 checks against the release's `SHA256SUMS`. The app checks GitHub Releases once a day, and Help → Check for updates checks at any time. *Install now* downloads and verifies the installer, starts Setup detached and quits; Setup waits for the app mutex, installs without admin rights and reopens the app. CI performs a real in-app update of the installed app.
- **One departure from what Abraham was shown when choosing Qt:** the default look is the app's own Windows 11-like theme on Qt's Fusion style, which looks the same on Windows 10 and 11 and in the screenshots. Qt's native `windows11` style is a Settings option. The reason is that the native style's look with this layout could not be checked in the container.

**Status: template specs and font sizes done (v0.4.0, 2026-10-15)** — Abraham: "Let the user upload a spec template (for example, from the architect) … add / match the header/footer of the template to my discipline specs … match the font of the template spec."
- **Reference slots** (`apply_header_footer_template`, `slot_markers: ["reference"]`): the template is an ordinary spec. Each fill rule is run on the template spec itself (or names `in_template` text; a literal must) to get *its* value; every bounded occurrence of that value in the copied header/footer parts is a slot, filled from each target by the same rule. Refused rather than copied: the value left elsewhere in another form (`template-value-left`), shown by a field (`slot-in-field`), too short or not found in the template (`template-slot`). Runs holding a value and a tab/field character are cut around the value (`carve`); the verifier models the cut and now compares run content in order. Both sides re-implement located text, word bounds, tokens and field detection (CLAUDE.md).
- **Font sizes** in `set_font` / `set_style_font` / `set_doc_defaults_font` (`size`, `size_from`; `only_where_defined` on `set_font`), always "where a level sets one"; V10 checks w:sz per target and the `effective_font` post-condition gained `size`/`size_from` (verifier-only effective size).
- **Compiler:** `read_template` adds a `template_spec` summary (own values by the catalog's `reference_fills`, where they show, what would be refused, body font/size); every profile has `fonts.body_text`; `template-slot` is a fatal compile code. **Window:** the picker is "Template spec", two new example chips. Two worked examples and an example architect's spec.
- **Departure from the design shown to Abraham:** none in scope. The case of a value is matched loosely (a header that shows "Door Hardware" for the template's "DOOR HARDWARE" is a slot) and each spec's value is placed as that spec writes it; the plan flags it. Values are not reformatted (no case or spacing conversion: that would be typing).

---

## 13. Test strategy

- **Per-op fixtures and tests**: every op has a fixture that exercises its hard cases and at least one planted defect the verifier must catch.
- **Planted-defect discipline** (existing): any new grammar → a deliberately wrong implementation → the suite must fail.
- **NL evaluation set**: ~30 requests with expected op sets, selectors, and expected questions/assumptions, including near-misses ("change the font in the headers only", "remove the first-page header but keep the footer", "replace the stamp but only where it says PERMIT"). Run against the compiler; score op/selector match and question-asking.
- **Real-file read-only sweeps**: `plan-spec` across past project folders; zero outputs written; every exception becomes a fixture or an op improvement.
- **Oracle agreement**: pandoc (tests) and Word COM (user's machine) accept/reject views vs the XML simulation on content ops.

---

## 14. Open questions for Abraham

1. Should formatting ops be **tracked by default** (`rPrChange`/`pPrChange`, visible in All Markup) or plain? Tracked is reviewable but noisy on a whole-document font change. Proposed: plain for whole-document formatting, tracked for targeted paragraph edits, always stated in the preview.
2. Template slot conventions: is yellow highlight the standing marker for slots in your templates, or should the profiler detect `{{NAME}}`-style placeholders too?
3. Tier B: allowed by default, or only when the user ticks "let it write a custom transform"? Proposed: off by default; the preview explains why the request needs it.
4. Per-project memory: keep the skill's notes-file format (`per-project-setup.md`) as a `PROJECT.json` beside the specs that the compiler reads? Proposed: yes.
5. Fidelity: on your machine, should Word be mandatory for the preview (fail if absent) or fall back to LibreOffice silently with a warning?

**Status after Phase 1:** the answers arrived as unfilled option lists, so nothing was decided by Abraham yet. Phase 1 needed only #1 and implements the proposal above as a default — whole-document formatting plain; formatting whose selector targets particular paragraphs (a `paragraphs` predicate) tracked via `w:rPrChange`/`w:pPrChange`; an explicit `tracked` always wins; the record states which rule applied. Styles, docDefaults and page setup are never tracked. #2 (slot markers) must be answered before Phase 2; #3 before Phase 5; #4 and #5 before Phase 4.

**#2 answered (before Phase 2): "both."** Yellow highlight and `{{NAME}}` placeholders are both slot markers; the op detects both by default (`slot_markers`, `highlight_colors` can narrow it per spec). #3, #4, #5 remain open.

---

## Phase 1 status (2026-10-13)

**Built** (all in the repo, 62 tests green, `README.md` → "Operations and EditSpecs"):

* `ops/base.py` — `Op`, `Plan` (v0.1.0 fields + `members_removed`, `members_added`, `property_assertions`, `geometry_reference/reason/template`, per-op summary), `PlanFragment` (+ `touches`, revision-id needs), `merge_fragments()` with conflict detection, merged mode rules, disjoint revision-id ranges, geometry merge.
* `fix_page_numbering` and `replace_text` moved into `ops/`; `jobs/` are `OpJob` aliases; the v0.1.0 CLI, `run_batch(job_name, params)` and tests unchanged.
* `package.py` — `DocxPackage` gained a working overlay: `copy`, `write/write_xml`, `remove_member`, `add_member(name, data, content_type)`, `edit_rels(part, add, remove)`, `relationships`, `content_types`, `unreferenced_media`, `output_bytes`; `repack` handles removals and additions.
* Verifier — V1 member prediction + package integrity, V2 text-scope union, V4 every revision kind, V5b/V5c for tracked property changes, `verify/props.py` (V10: assertions + residual identity + effective-font resolver), V7 reference modes (`batch.geometry_check`).
* Ops — `remove_header_footer`, `set_font`, `set_style_font`, `set_doc_defaults_font`, `set_paragraph_props`, `set_page_setup`; fixture `build_rich()`; end-to-end and planted-defect tests for each.
* `editspec.py` + `spec-surgeon run-spec` (+ `spec-surgeon ops`); `examples/specs/change-all-font-arial.json`, `examples/specs/remove-all-headers-footers.json`.

**Departures from this plan, and why:**

1. **`apply()` mutates a working package instead of returning `{part: bytes}`.** Removing/adding members and composing several ops on one part (`set_style_font` + `set_doc_defaults_font` both edit `styles.xml`) cannot be expressed as independent byte substitutions from the source. Each op reads the working copy an earlier op left; the v0.1.0 `Job.apply` still returns substitutions.
2. **V10 runs on every plan and has two halves.** Besides the per-assertion check of §5 it does a *residual identity* comparison: strip what the assertions cover and the predicted text-changed blocks; everything else in every changed text part and in `styles.xml` must be identical. That is what makes "no property change elsewhere" total rather than per-domain, and it also closes a v0.1.0 hole (a stray formatting change inside an intended part was invisible to V1–V9). Consequence: `fix_page_numbering` (`continuous`) now declares its sectPr change as a `section_props` assertion.
3. **V1 gained package-integrity checks** (vs the source's own baseline): dangling relationships, `r:id`s naming no relationship, overrides for missing members, untyped members, members orphaned by the edit. Needed so "a media file dropped that was still referenced" is caught even when plan and apply share the bug.
4. **V4 counts every revision kind**, not only w:ins/w:del (paragraph-mark revisions, rPrChange/pPrChange, table revisions). It exposed a latent v0.1.0 bug: a tracked replacement in a run carrying a `w:rPrChange` copied that revision (same `w:id`) onto every split fragment. `replace_text` now refuses that case (`tracked-format`).
5. **EditSpec gained spec-level `assertions`** (post-conditions only; Phase 1 knows `effective_font`). "Change all fonts" is three ops; only a post-condition over the *effective* font proves their composition. Kinds that strip residual content are not allowed at spec level, so a spec cannot exempt anything from V10.
6. **`expect` is checked for a small numeric vocabulary** (`text_diffs`, `text_diffs_outside_removed_parts`, `members_added`, `members_removed`, `rev_delta`); string values are prose and go into the record. The §3 examples' prose expects remain valid.
7. **`review.geometry_reference` must agree with the ops** (where the file changes): a spec can neither drop a reference an op keeps (weakening) nor claim one an op gives up (a guaranteed V7 FAIL).
8. **V7 `template` is plumbing only**: the plan field, the merge rule and a FAIL with an explicit "Phase 2" message. The anchor-word comparator is Phase 2 item 2 and was not started.
9. **Conflicts are refused, not ordered.** §4/§12 allow "ordered application or exception"; Phase 1 only refuses (code `conflict`). Ordering text edits before formatting on the same block needs run re-resolution and V10 alignment across restructured runs — a Phase 3 planner topic.
10. **Tracked formatting scope.** Runs (`w:rPrChange`) and paragraphs (`w:pPrChange`) only; styles/docDefaults/page setup refuse `tracked: true` (`w:sectPrChange` not implemented; Word does not track style definitions). A target already carrying someone's property change is refused when tracked (one snapshot only) and flagged when plain.
11. **`remove_header_footer` inheritance guard**, refined by visibility: it refuses when a selected section would still *show* an inherited part, or an unselected section would show something different; first-page parts count only with `titlePg`, even-page parts only with `evenAndOddHeaders` — invisible changes are flagged. It never creates an empty replacement part. Header parts with relationships other than images/hyperlinks are refused (`structure`).
12. **`set_style_font` changes only styles that already define a font** by default (`only_where_defined`): the rest inherit through `basedOn` → docDefaults, so adding fonts to them would add formatting nobody had. `stylesWithEffects.xml` is flagged, not edited.
13. **Minimal executor in `editspec.py`** rather than `planner.py`: §12 puts the planner in Phase 3. `editspec.py` resolves no selectors itself (each op does, via `ops/selectors.py`); Phase 3 centralises that and adds per-pattern compilation, the JSON-schema validator and the op catalog.
14. **`spec-surgeon ops`** (a human-readable listing, needed to hand-write specs) was added; `ops --catalog` for the compiler stays in Phase 3.

**Measured** (development container, 4 cores, 24 synthetic specs, `run-spec … --no-render --workers 4`): remove-all-headers-footers 0.47 s wall (≈ 50 ms/file single-threaded); change-all-font-arial 1.32 s wall (≈ 175 ms/file single-threaded — V5a on python-docx's ~160-style `styles.xml` and V10's effective-font/residual walk dominate). Rendered smoke runs: header/footer removal moved body text 0.00 pt (V7 vs source).

**Next step — Phase 2** (done — see "Phase 2 status" below): `apply_header_footer_template` as an op that *adds* members (`members_added`, content-type overrides, `.rels`, media via `add_member`/`edit_rels`), with slot detection by the confirmed marker convention, fill rules that extract (never type) values, tab-stop rescale, spacing pin, style import with collision checks; V2's text-scope union already accepts predicted insertions for added parts, so the remaining verifier work is V5a against the template's baseline for added parts, the V10 assertion kinds the op needs, and V7 `template` (anchor words in header/footer bands of the output vs the template render) replacing today's FAIL.

---

## Phase 2 status (2026-10-13)

**Built** (116 tests green, `README.md` → "Header/footer templating (Phase 2)"):

* `ops/apply_header_footer_template.py` — the playbook as an op: per target section the template section's *displayed* parts (default; first with titlePg; even with evenAndOddHeaders) copied in as new parts with rels, media, content-type overrides and document relationships; slots filled by rule; tabs rescaled; effective properties pinned; missing styles imported; old parts removed (shared with `remove_header_footer` via `ops/hf_common.py`); header/footer distance and titlePg aligned to the template (both optional).
* `ops/slots.py` (slot detection — both conventions — display text, slot filling, fill rules), `ops/effective.py` (transform-side effective-property resolver, tab rescale).
* Verifier — `verify/template.py` (V10 kinds `hf_template_refs`, `template_parts`, `styles_added`, with their own slot detection, fill extraction and resolver), V5a template baselines for added parts, V1 content-type-vs-relationship integrity, V7 `template` (`verify/geometry.py`: headers/footers-only reference render + region comparator).
* `tests/make_fixtures.build_template()` (+ `examples/templates/00_00_00_TEMPLATE.docx`), target variants in `build()` (footer revision, side margins, header/footer distances, a body picture), `tests/test_template.py` (43 tests: end to end, 18 refusals, 12 planted defects plus rendered V7 misalignments, V7 page pairing incl. the blank-variant case, resolver agreement, the V7 reference documents, batch with the template skipped), `examples/specs/apply-header-footer-template.json`.

**Departures from this plan, and why:**

1. **No `from_footer` fill rule.** `regex` with `source: "footer.default"` (or any `header.<type>`/`footer.<type>`, optionally one section's) covers it and more; the third rule is `paragraph_after` (the section title is "the paragraph after SECTION nn nn nn"). All rules read the **display text** (live `w:t` only), not the block text — a field instruction or a deleted word must not become a fill.
2. **"Spacing pin" generalised to effective-property pinning.** Pinning only spacing left the fixture's 7 pt template footer rendering at the target's 10 pt. The op resolves the effective value of a fixed property list (spacing, indentation, jc, tab stops; ascii/hAnsi fonts, size, color, b/i/caps/smallCaps) in both packages and pins every difference as direct formatting. This needed a **transform-side resolver** (`ops/effective.py`), implemented independently of the verifier's (`verify/template.Eff`), with a test proving they agree. Toggles set at more than one style level are refused (`toggle-ambiguous`) rather than XOR-modelled.
3. **Styles are imported, never overwritten** (the plan said "style import with collision check"). A target style with the same id is left alone — the pins make the copy render as in the template under the target's definition. Missing default styles and styles with numbering are refused.
4. **The V7 template reference is the template's headers/footers rendered alone**, not "anchor words in header/footer bands". A first version used the template's margins as band edges; the rendered test showed it never looked at the fixture's body footer (a paragraph + table taller than the bottom margin) and would have counted body words of a target with smaller margins as header words. The verifier now writes a copy of the template whose body is only its section breaks (three pages per section), so every word on that render is header/footer text, and checks each output region's outer edge and shared words against it, with the region found or not found within a 2 pt window. Footer positions are measured from the page bottom (page heights may differ). The Codex review on PR #2 then found that choosing the "best matching" template page for each output page let a blank variant (a cover's first page with titlePg and no first-page header) stand in for any page, so a displaced header could pass. The output is now rendered headers/footers-only too (sections forced to nextPage with numbering restarted at 1 in both copies), and pages are **paired** — output section k's v-th page with template section map[k]'s v-th page, map from the spec's `template_sections` — so a blank template region requires a blank output region. Cost: one extra render per file.
5. **The missing content-type override is a V1 finding, not V5a.** V5a validates XML against schemas; a header typed `application/xml` by the `xml` Default is a package-integrity problem. V1 now checks every part's content type against the type its relationship requires (header, footer, styles, settings, numbering, notes, comments, fontTable, webSettings, theme, images).
6. **V5a for added parts uses the template part as its baseline** — schema errors the template already carries are not the op's.
7. **Template file handling**: relative template paths resolve against the spec's folder; the template's md5 is pinned in the plan (apply refuses a changed template, V10 fails on one); batch inputs that are a spec's template (same path or same bytes) are skipped and listed in the report.
8. **`expect` gained `text_diffs_in_kept_parts`** — `text_diffs_outside_removed_parts` is not 0 for templating (the new parts' blocks are predicted insertions), so the example spec needed a key that says "nothing changes in parts that survive".
9. **Refusals added** beyond the plan: `even-odd` (evenAndOddHeaders differs — a document-wide setting), `title-page` (titlePg differs with alignment off), `template-revisions`, `slot-overlap`, `slot-structure`, `slot-in-textbox`, `unfilled-slot`, `unknown-slot`, `missing-style`, and `structure` for bookmarks, comments, notes references, altChunk, subDoc, OLE, numbered paragraphs and data-bound content controls in template parts.
10. **Mode and tracking.** Formatting mode unless a `literal` fill places user words (then `untracked`, flagged); `tracked: false` only — Word cannot track a header part being replaced. Pending revisions in the removed parts are discarded and flagged by author.
11. **Not rescaled**: table widths (flagged when wider than the target's text width) and tab stops inside tables. "Revision-history table preserved" is a consequence of the op never touching the body (V2 + V10 residual identity prove it).
12. **Drawing ids** (`wp:docPr/@id`) of copied parts are renumbered above the target's maximum (Word repairs duplicates); V10 checks uniqueness across the output.

**Measured** (development container, 4 cores): 24 synthetic specs `run-spec apply-header-footer-template.json --no-render --workers 4` 2.07 s wall; single-threaded per file ≈ 250 ms (plan 33, apply 23, verify ≈ 190 — V5a on the changed `styles.xml` and four new parts, V10's effective-property resolution on both packages). Rendered (LibreOffice, 12 files, 4 workers): 8.9 s (source, output and output headers/footers-only renders), all PASS, worst header/footer |Δy| vs the template render 0.00 pt.

**Next step — Phase 3**: `profile.py` + `planner.py` (selector resolution centralised, `applies_to` per pattern, ordered application of overlapping ops as a planner topic), the EditSpec JSON-schema validator and `spec-surgeon ops --catalog`, and the `plan-spec`/`profile` CLI. Real template files from past projects (the user's `00 00 00 TEMPLATE.docx` files) should be run read-only through `run-spec … --no-render` early in Phase 3 or 6 to find template constructs the fixture lacks.

---

## Phase 3 status (2026-10-13)

**Built** (125 tests green): `profile.py` (per-file profile bounded to 6 KB, `profile_batch` grouping by pattern), `planner.py` (`plan_batch` dry run over a batch, `summarize_plan`, `BatchPlan.to_text/to_dict`), `catalog.py` (`op_catalog` with per-op common assumptions, selector grammar, rules and worked examples; `editspec_json_schema`), CLI `profile`, `plan-spec`, `ops --catalog`; `tests/test_planner.py`.

**Departures:** (1) selector resolution was already shared by every op through `ops/selectors.py`, so the planner does not resolve selectors itself; `applies_to` by pattern stays in `editspec.plan_spec`. (2) Overlapping ops stay refused (`conflict`); ordered application is not implemented. (3) The JSON schema is published for the compiler and the GUI, but `validate_spec` remains the authority (it checks mode, expect and geometry against the plan). (4) Per-pattern *compilation* (one compiler call per pattern) moves to Phase 4 with the compiler.

---

## Phase 4 status (2026-10-13)

**Built** (135 tests green): `compiler.py` (tool-use loop with `inspect_profile`, `list_styles`, `sample_paragraphs`, `show_part_text`, `read_template`, `ask_user`, `submit_editspec`; submission checks: validation, traceable words, dry plan per pattern; one retry), `preview.py` (batch dry plan, in-memory sample apply + verify, before/after page images, `PREVIEW.md`, approval token), CLI `do` and `run-spec --approval`; `tests/test_compiler.py` with a scripted client.

**§14 decisions taken without a round-trip** (Abraham asked to finish the prototype without stopping for questions): **#4** project memory — yes, as `PROJECT.json` beside the specs, given to the compiler and counted as the user's words; **#5** Word for the preview — not mandatory: Word when present, otherwise LibreOffice with a visible warning in the preview (never silent).

**Departures:** (1) the model is `claude-opus-5-5` by default (the plan suggested Sonnet-class for compilation; one model keeps behaviour predictable; `SPEC_SURGEON_MODEL` overrides it), with the server-side refusal fallback enabled. (2) "Generated text not traceable to the request/template" is enforced as a hard check on the words ops would place (`placed_words`), not left to the prompt. (3) The NL evaluation set (§13) needs the live API and is not built; the compiler is tested against scripted model turns. (4) Approval is a token over spec + file identities rather than a stored approval record.

---

## Phase 5 status (2026-10-13)

**Built** (153 tests green): `codegen.py` (`check_source` whitelist, `run_in_sandbox`, `hard_limits`, `trial`, `generate_op` loop with at most 3 attempts, `save_generated`, `run_generated`), `sandbox_runner.py` (isolated process: re-check, reduced builtins, import hook, plan + apply on a copy), `do --allow-codegen`; `tests/test_codegen.py`.

**§14 #3 decided without a round-trip**: Tier B is off by default; `--allow-codegen` turns it on per run (the plan's proposal).

**Departures:** (1) generated ops are formatting-only (no text changes, no added members, no tracked insertions/deletions) — the plan allowed "characters from params"; that needs a traceability rule for generated code and is left for later. (2) The "drops a tab → V3" safety test became "an unasserted property change → V10" (generated formatting ops cannot predict text diffs at all under the hard limits, so V10's residual identity is the check that bites). (3) Generated ops run standalone, not inside EditSpecs, and never in place. (4) The sandbox is a process + whitelist + timeout, documented as not a security boundary.

---

## Phase 6 status (2026-10-13)

**Built** (158 tests green): `advisor.py` (`advise(record)` → a recommendation or a re-compiled spec pinned to the file's structure pattern, held to the compiler's checks and to planning without an exception on that file; one retry; `advise_batch(out_dir)` writing `advice/`), CLI `advise`; `tests/test_advisor.py`. The version is now 0.2.0.

**Not done (needs the user's machine and files):** the read-only real-file sweep (item 2) and the Word COM path (item 3); live runs of the compiler, Tier B and the advisor (no API credentials in the development container — the loops are tested with scripted model turns). **Departure:** the old `agent.py` (`ask`, `agent`) stays for the v0.1.0 CLI; `advise` is the EditSpec-aware successor. *(Phase 8: `agent.py`, `ask` and `agent` are retired — see "Phase 8 status".)*

**State of the prototype:** every phase of §12 up to the GUI is implemented. The recommended next steps, in order: (1) run `profile` and `plan-spec` read-only over two or three past projects and turn every exception that is not a true refusal into a fixture; (2) run `do` on a copy of a real project folder with an API key and tune the compiler prompt on what it gets wrong; (3) render with Word and compare with the LibreOffice numbers; (4) build the GUI on the library calls.

---

## Phase 8 status (2026-10-15) — the conversation, Claude's visual check, custom transforms as an offer, your library

Not a phase of §12: it came out of Abraham's questions about how the model fits into the flow, after the Qt window. **Built** (410 tests: 409 pass, the Windows-only Credential Manager test skips elsewhere), one commit per step:

1. **Hermetic tests and one call site.** `tests/conftest.py` seals every test (a temp config folder, no key, no live client, no terminal); `tests/fake_claude.py` is a streaming-only scripted client. `llm.py` is the only module that calls Claude: streamed (`client.beta.messages.stream` + `get_final_message`), `max_tokens` 64,000 (Abraham: "if we are going to switch to streaming, just do 64,000 cap"), effort `high`, `fallbacks="default"` with the server-side fallback beta, 5-minute cache, no `thinking` / `tool_choice` / prefill; `history_content` keeps only what to echo and act on after a fallback; `Usage` per serving model with a cost estimate. `ask` / `agent` / `agent.py` retired (`SKILL_DIR` moved to `spec_surgeon/__init__.py`). Two traceability leaks closed: the advisor's sources were the whole original spec JSON (Claude's own summary and assumptions included) — now the words it placed + the request + the answers; project memory's `_path` no longer counts.
2. **The conversation** (`conversation.py`; §6's one-shot compile becomes a back-and-forth). Decision (Abraham): *don't give up on the user* — when no op fits, Claude restates what it understood, explains what is missing and offers choices (`clarify`, `alternative` with exactly how it differs, `rephrase`, and — added by code only — `custom_transform`); replies from the Describe and Preview steps and the terminal; suggested wording counts only when the user picks the choice showing the exact words; the custom transform gets the whole conversation; the running cost is shown; the CLI gets the same loop. `compile_request` is a wrapper over `Conversation(mode="compile")`. Append-only history, `check_pairing` before every request, a failed call appends nothing, frozen system/tools, ≤ 12 calls per turn, rejections counted per user turn. After a preview the app sends Claude a note of what it found, so a reply is a correction.
3. **Claude looks at rendered pages** (`vision.py`). Decision (Abraham): samples only, to keep costs down; sample pages may go to Anthropic. `view_pages` in the chat (≤ 3 pages a call, ~60k image tokens per conversation); Claude's visual check of the preview's before/after sample pages — advisory: a `block` finding stops Run until the user acknowledges it for that preview token; it never passes anything and is never a `CheckResult`.
4. **Custom transforms** (§8). Decision (Abraham): replace the "Allow AI-written transforms" checkbox with an offer at the point of need, reusing the compile result; edited copies only. Claude judges `custom_transform: possible | outside_limits` against `catalog.CUSTOM_TRANSFORM_LIMITS` (six property families, each mapped to the V10 kind that checks it). The writer gets the brief (the whole conversation), read tools over the samples' XML, three reference ops; attempts (3) counted apart from calls (24); one submission per turn. `hard_limits` now refuses any revision of any kind and any part outside the text parts and the styles part. The transform gets a real preview (samples, before/after pages, the visual check). `run_generated` keeps the batch rules (inputs outside the results folder, unique output names, the output hash re-checked before delivery).
5. **Your library** (`library.py`). Decision (Abraham): users need to save and name their own solutions; local now, shareable later. Recipes (a previewed spec) and operations (a passing custom transform, sha256-pinned and re-proven on every batch before use). Window: Save to library…, Your library…, Open library folder; CLI: `do --save-as`, `library list|show|delete`.

**Decisions I took (Abraham delegated them: "decide what you will code up"):**
- Retire `ask` / `agent`: `do` and `advise` supersede them, and their 800 / 1,500-token caps break under always-on thinking.
- The method rules stay as they are: no accepting or rejecting tracked changes, no `docProps` edits, no toggling `trackRevisions`. They matter only for operations that do not exist yet.
- Generated custom transforms stay plain (untracked) formatting on text parts and the styles part; no tracked-change helper is whitelisted.
- Model `claude-opus-5-5`, effort `high`, fallback `"default"`, 5-minute cache: unchanged; effort is tuned after the first live runs.
- No version bump in this work: release preparation (version + release notes) is its own step.

**Cut from this round, recorded as next steps:**
- More built-in operations (Abraham: "Let's do the additional built in operations later") — about 60 ideas were listed in the conversation, for example bold/italic/caps, colours, borders and shading, numbering and list levels, table formatting, keep-with-next / widow control, tracked-change acceptance with its own verification, document properties.
- A generic `set_properties` op with a generic V10 kind (ideas #22–23): it belongs with the built-ins round, where its open question (two ops on one paragraph — touches are per block) is decided.
- Claude browsing the library during the conversation (`read_recipe`, recipe/operation choices): it complicates the approved-words rule.
- CLI `run-op` / `run-recipe` (`library show` prints a recipe's `run-spec` command; operations run from the window).
- Sharing the library between users.

**Not done (needs Abraham's machine or a key):** a live API run of any of it — the conversation, the visual check, the transform writer and the advisor are tested with scripted turns, and the real SDK's request body is checked offline over a mock transport; the window's chat on a Windows screen.

**Merged with master after Phase 8 (2026-10-15):** master had gained, after v0.4.1, the GUI rework (a step rail, an action bar per step with a hint, empty states, Settings down to the results folder and the API key, the look following Windows, an About box, the MIT license) and diagnostics (failure kinds, a trace per file, the run log, `DIAGNOSTICS.md`, *WHY IT FAILED* in each record, bundles, `spec-surgeon diagnose`). The model-side diagnostics were ported onto this phase's architecture, not back to a one-shot `messages.create`: every call still goes through `llm.call`; the exchange is kept per caller (the conversation's `exchange` → `compile.json`'s `transcript` beside `conversation.json`, the advisor's in `advice/<name>.transcript.json`, a custom transform's in `codegen/history.json` after every attempt, the visual check's in `visual_review.json`); an API error (`llm.is_api_error`, `llm.ClaudeUnavailable` when the SDK is missing) is a recorded failure with its HTTP status and request id (a turn fails and appends nothing), a defect propagates (after the conversation's record is saved), the visual check is skipped instead and keeps the traceback, and a tool's exception stays an `is_error` result. The window's session folder holds `conversation.json` and `compile.json` beside `preview/`, `codegen/` and `run/`; once a preview has used a folder its records are frozen and the next reply opens a new one. The chat (right under the request), *Your library…* (in the saved-spec card), *Save to library…* (at the top of the Preview body) and the visual-check card sit in the reworked steps, and the rail and the action-bar hints say when Claude waits for a reply or its findings block Run; the Answers box and the AI-written-transforms checkbox stay gone. A Run refused for a custom transform also says what changed since its preview. 481 tests (all pass but the Windows-only Credential Manager test, which skips elsewhere).

**Review fixes after the merge (2026-10-15):** the merged branch went through an internal adversarially-verified review, a Codex review and the merge's own verifiers; every finding was reproduced on the merged code before it was fixed, and `tests/test_phase8_fixes.py` pins each fix with a test that fails without it (685 tests in total, all pass but the Windows-only Credential Manager round trip). README → "The Phase 8 review fixes" has the finding-by-finding table. In short:
- *The conversation* (step 2): a submission with a wrongly shaped field is refused by `validate_spec` as `spec` (every spec-level field now has the published schema's shape) and any check that still raises is answered as `REJECTED`, so every tool call stays answered; Claude's questions and choices of the wrong shape are dropped and said, never raised or split into characters; an answered turn's choices cannot be picked after a failed call; a wording character the user cannot see is defined by `conversation.invisible` (Cc, Cf, Cs, Co, Cn, Zl, Zp, every Default_Ignorable_Code_Point, U+2800); the spec handed to the app carries the user's request, never Claude's text; `conversation.json` records `approved_words` and `answers`; two same-named files in different folders are told apart by label (`compiler.file_labels`: `a/Spec.docx`) in everything Claude sees and every tool.
- *Claude's visual check* (step 3): guarded at every stage (pages, client, call, reading the answer) — an answer that is not a review's shape is "Claude's answer could not be read (…)", a client error is classified like a call error, and a skipped review has no findings; the `view_pages` budget is about 20 letter pages, derived from `est_tokens`; the command line's check gets the terminal conversation too.
- *Custom transforms and your operations* (steps 4 and 5): only `gui.TransformFailed` ("No custom transform passed verification: …") is told to Claude, a defect never is; a transform whose files changed is tried again by Preview in a new session folder (`retrial/`) instead of being shown as it was with Run refusing for ever; a saved operation is tried first, in a new session folder (`library-trial/`), and changes nothing unless it passes; without a terminal `--allow-codegen` accepts only Claude's `"possible"`; `--save-as … --replace` and the library never let a recipe and an operation share a name.
- *Approval* (§7): `do` checks the approval token again after its y/N question or `--yes`, for a spec and for a custom transform; preview samples get unique output names.
- *The window and the command line*: *Render pages* is part of the preview key; Save to library… and I've looked — allow Run are live only while the preview is current; the "Saved spec" bar goes when its spec is replaced, and Preview with an empty request box previews the current custom transform again; the chat closes when the conversation ends (a folder change, *Use the whole folder again*); a choice's detail is shown as plain text; the end of input or Ctrl-C at a terminal prompt stops (or means no) instead of a traceback; the diagnostics bundle keeps the last run with its session and adds the newer session folders under `results-later/`; a scripted `do` (no terminal) writes `compile.json` only — `conversation.json` comes from a terminal conversation or the window.
- *Hermetic tests* (step 1): the suite no longer reaches the Windows Credential Manager (it used to read and overwrite a saved key), restores the machine's key variables after each test, and pins the model whatever `SPEC_SURGEON_MODEL` says.

The custom-transform whitelist (review-7) is fixed too: `codegen.check_source` is now an allowlist for module attributes (`MODULE_ATTRS`) and refuses any other use of a module object, frame/generator internals, dunder names and lxml's file readers/writers; the sandbox runner installs an audit hook (`install_guard`) that keeps every file-system change inside its working folder and refuses processes, sockets and native code. The prototype's statement still stands: the sandbox is a whitelist plus an audit hook in a separate process, not an OS-level jail. The second pass's tests are `tests/test_phase8_fixes_window.py` and `tests/test_phase8_fixes_cli.py`.

---

## Built-in operations status, part 1 (2026-10-16) — `set_properties` and the `properties` V10 kind

The first of the deferred built-ins (Phase 8 status: ideas #22–23). Design note `docs/SET_PROPERTIES_DESIGN.md` — two
Codex findings on the first draft folded in (the complex-script pairs, the provenance rule for style targets) — with
nine decisions, all approved by Abraham as recommended; built verifier-first on PR #16. `set_properties` sets
b/i/caps/smallCaps/strike/u/color on runs and paragraph marks or in style definitions, and
keepNext/keepLines/widowControl/pageBreakBefore on paragraphs or paragraph styles; it adds no characters (caps is a
display property). V10's `properties` kind re-derives the targets and the elements each gets with the verifier's own
effective-value resolver, checks every target whole, resolves the effective result in the output (and everything that
uses a changed style) and strips only what it compared. The open question recorded in Phase 8 — two ops on one
paragraph — is answered for this round by keeping rule 9's granularity: one `set_properties` carries all of its
properties; a font change and `set_properties` on the same paragraphs are two specs; finer aspects are a follow-up.
README → "Formatting properties" is the user's view; `tests/test_set_properties.py` (25 tests) pins it.

---

## Appendix A — EditSpec JSON schema (draft)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["request", "summary", "ops", "mode"],
  "properties": {
    "request": {"type": "string"},
    "summary": {"type": "string"},
    "assumptions": {"type": "array", "items": {"type": "string"}},
    "questions": {"type": "array", "items": {"type": "string"}},
    "applies_to": {"type": "object", "properties": {"pattern_ids": {"type": "array", "items": {"type": "string"}}, "or": {"const": "all"}}},
    "mode": {"enum": ["formatting", "content", "untracked"]},
    "review": {"type": "object", "properties": {"sample_files": {"type": "integer", "minimum": 1, "maximum": 3},
                                                 "geometry_reference": {"enum": ["source", "template", "none"]}}},
    "ops": {"type": "array", "minItems": 1, "items": {
      "type": "object", "required": ["op", "params"],
      "properties": {
        "op": {"type": "string"},
        "select": {"type": "object"},
        "params": {"type": "object"},
        "expect": {"type": "object"},
        "tracked": {"type": "boolean"}
      }}},
    "unsupported": {"type": "string"},
    "proposal": {"type": "string"}
  }
}
```

## Appendix B — compiler prompt outline

1. Role: you compile a user's request about editing Word specification files into an EditSpec for a deterministic engine; you never edit files and never invent document text.
2. The contract (from `SKILL.md`): formatting = zero generated characters; content = only approved characters, tracked.
3. The op catalog (generated), with each op's common assumptions.
4. The profiles of the files in this batch (one per pattern) and the template profile if any.
5. Project memory (stamp, author, template, numbering exemplar).
6. Rules: resolved part names only; assumptions visible; questions when the request is ambiguous; `unsupported` + `proposal` when no op fits; output JSON only, validated by the schema in Appendix A.
7. Three worked examples (§3).
