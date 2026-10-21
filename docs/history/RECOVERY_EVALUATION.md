# Evaluating recovery on real specification batches

The automated tests exercise the real document engine and independent verifier with synthetic
DOCX fixtures and scripted model replies. They prove specific control-flow, preservation and
failure-detection behavior. They do **not** measure Claude's ability to discover a repair, real
project success rates, Microsoft Word fidelity, or actual API spending. Those remain separate
validation work until the following protocol is run with authorized real files and credentials.

## A small representative corpus

Start with 10–20 locally retained batches, including the ones that required manual intervention.
Keep their original inputs and the architect's template immutable. Use separate results folders
for every experiment. Record the exact request, approved wording, template hash, source hashes,
application revision, model/effort, renderer, limits and operating system. Recovery reports
already retain input pins and budget details; preserve the reports with the experiment.

Include at least these cases:

| Case | What a useful result establishes |
| --- | --- |
| Font, page setup and architect header/footer conflict in one spec | A verified sequence solves the conflict while preserving the requested effects. |
| A later stage undoes an earlier font or margin requirement | Individual stage PASS results cannot substitute for the final assertion. |
| Section title uses an inline dash or unusual whitespace | Revised extraction finds this file's actual wording without inventing or normalizing it. |
| Several files share the same failure but have different section numbers/titles | One investigation can be reused, and each file is freshly verified with its own extracted values. |
| A new project has the same structure as a past repair | Project-specific literals cannot travel with the repair; the current approved goal and template still bind it. |
| Template, source or verified output changes before delivery | Stale results are refused instead of exported. |
| A requested operation disappears from a proposed repair | Original operation effects and selectors remain required. |
| An ambiguous title, unsupported operation, exhausted budget or failed model call | The unresolved files and the reason remain explicit; no success is fabricated. |
| Rendering is unavailable, or visual review is skipped | Structural results remain distinct from visual and human acceptance. |
| Symbol-font preservation or source-relative final size requirements | Unsupported final assertions stop recovery explicitly; they are never silently dropped. |
| A complex template layout | Structural success remains separate from complete visual acceptance. |

Separate development batches from a held-out group. Fixes, prompt tuning and memory captured on
development batches must not quietly contaminate a cold evaluation of the holdout group.
Keep de-identified regression fixtures in the repository where authorized; retain client
documents outside the repository.

## Run the experiment deliberately

1. Run the ordinary workflow and retain its failure report. Record its model cost and the amount
   of human intervention separately from recovery. The evaluator below measures recovery only.
2. Run recovery explicitly using the application or `spec-surgeon resolve --help`. Use a fresh
   output folder and declared model, effort, attempt/call/spending limits (the Python
   `RecoveryOptions` also configures token limits). This is the step
   that may send document excerpts or rendered pages to the configured model provider and incur
   charges. The evaluation command below does neither.
3. Review the resulting copies against the **complete original request**. Check all important
   pages, first/even-page variants, section boundaries, project/section identifiers, page fields,
   fonts and dimensions. A sampled advisory visual review does not establish the rest of a batch.
   On Windows, inspect the files in Microsoft Word and record whether Word repaired a document,
   changed pagination, or displayed an incorrect field/header/footer.
4. Record request satisfaction, content preservation, whether a human checked visual output,
   and the number of interventions. Do not mark an uninspected result accepted. An expected
   unsupported or budget-stopped case can pass its behavior expectations without being an
   accepted completed batch.
5. Repeat the same corpus with an isolated memory store for a cold comparison, then with that
   store retained for a warm comparison. `SPEC_SURGEON_CONFIG_DIR` selects a local store; use a
   dedicated evaluation directory so this experiment does not alter the user's normal settings
   or repair memory. Also test a different project with the same structure and an altered template.
6. Compare with the manually successful workflow on a separate copy. Record the same quality,
   model spending, time and intervention measurements. Include unsuccessful attempts when
   calculating cost per completed batch.

The current recovery contract preserves operation effects and selectors while permitting
changed extraction rules, expectations and stage ordering. Final checks cover supported explicit
font assertions and inferred page setup (and supported chain final assertions). These checks do
not independently translate arbitrary natural language into a complete acceptance test. In
particular, unsupported source-relative final font requirements are currently refused by recovery;
they are not downgraded to review obligations. Template appearance still needs review. Generated
formatting remains opt-in and is currently limited to page-setup-only goals, with independent
verification against the original analytically predicted plan as well as the generated plan.

## Compare saved reports without running a model

For a pinned baseline/current comparison, explicit end-to-end time and intervention measurements,
and costs outside recovery, use the version 2 paired manifest described in
[WORKFLOW_EVALUATION.md](WORKFLOW_EVALUATION.md). The version 1 report summary below remains
supported and measures recovery only.

Create a local manifest next to the experiment folders:

```json
{
  "version": 1,
  "cases": [
    {
      "id": "architect-a-cold",
      "cohort": "holdout-cold",
      "execution": "live",
      "report": "architect-a-cold/recovery.json",
      "expect": {"ready": 12, "unresolved": 0, "needs_input": 0, "calls_max": 12},
      "human_interventions": 1,
      "human_review": {
        "request_satisfied": null,
        "content_preserved": null,
        "visual_checked": false,
        "reviewer": "",
        "notes": "Awaiting review in Word; READY alone does not count as accepted."
      }
    }
  ]
}
```

Run:

```text
python -m spec_surgeon.recovery_evaluation evaluation-manifest.json --out evaluation-results
```

This command reads only the manifest and its named JSON reports. It does not open DOCX files,
rerun verification, access credentials, make network requests, alter repair memory, or deliver
documents. It writes `evaluation.json` and `EVALUATION.md` to a new empty folder. Paths in the
manifest resolve relative to the manifest. Existing output folders containing files are refused.

Use `execution: "scripted"` for fixture runs, `"live"` for real provider calls, and `"unknown"`
when provenance was not recorded. This label is the evaluator's declaration, not a conclusion
deduced from token counters. Scripted token usage is never included in live model economics.
Optional expectations are `ready`, `unresolved`, `needs_input`, `delivered`, `calls_max`, and
`budget_stopped`. Exit status is 0 when all supplied expectations match, 1 for a mismatch, and
2 for invalid input. With no expectations a case is `NOT_SCORED`.

Human acceptance requires every file to be READY plus all three human-review booleans set to
true. Delivery, stage verification and a model's "no findings" do not supply those judgments.
The command preserves report SHA-256 hashes for traceability, but does not authenticate reports
or independently confirm human judgments. Keep the original reports and reviewed copies.

The reported live cost per human-accepted batch includes the estimated costs of failed live
investigations. If any live cost is unknown, or no live batch has been accepted, the metric stays
unknown. Reservations retained for failed/unreported requests are included when available;
listed-price estimates are not provider bills or hard billing caps. The original baseline run,
manual comparison, human labor and rendering time outside recovery are not silently included.
The JSON also groups these metrics by cohort, requested model and effort so cold/warm and model
comparisons can be kept separate; each case retains the reported serving-model list.

Do not infer a real-world success rate from a scripted test pass or a corpus selected only for
successful repairs. Publish the corpus size, cold/warm split, review coverage, unresolved cases,
model versions and renderer together with any measured improvement.
