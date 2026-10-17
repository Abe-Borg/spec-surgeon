# Compare the same workload before and after a change

The version 2 recovery evaluation manifest pairs saved baseline/current reports and measures
human-accepted completion, elapsed time, manual interventions and estimated model cost. The
command is entirely local: it reads JSON reports, makes no provider calls, opens no documents,
and changes neither repair memory nor source files. It is a measurement tool, not a fresh
document verifier. Version 1 manifests and their CLI remain supported.

```text
python -m spec_surgeon.recovery_evaluation examples/evaluation/paired-scripted.json --out /tmp/style-evaluation
```

This runnable example contains **synthetic report shapes**, real hashes of repository example
inputs, placeholder Git revisions, no human acceptance and unknown time/cost measurements.
It demonstrates the manifest and output only. It does not demonstrate any improvement or
represent a run of either application revision. The tests separately exercise the real engine
and independent verifier with scripted clients; those are also not live performance evidence.

The command writes `evaluation.json` and `EVALUATION.md` into a new empty directory. Return codes
are 0 for matching expectations, 1 for failed expectations, and 2 for malformed or unpairable
input. Neither a successful exit nor an expectation PASS means that a human accepted the batch.

## Collect 10–20 representative pairs

Use the selection and review protocol in [RECOVERY_EVALUATION.md](RECOVERY_EVALUATION.md).
Include difficult and unsuccessful batches, ordinary fonts/project values, style mapping,
numbering, tables, template conflicts and ambiguous inputs. Keep client documents outside this
repository. Declare development/holdout and cold/warm cohorts before evaluating results.

1. Freeze the source documents, templates, auxiliary inputs, exact request and approved words.
   Record the exact baseline and current Git commits with `git rev-parse HEAD` in each checkout.
   Use clean checkouts; a commit SHA does not identify uncommitted changes.
2. Run each revision independently against copies of the same inputs, with the same model,
   effort, renderer, resource allowances and machine where possible. Preserve the ample agent
   allowances. Use separate `SPEC_SURGEON_CONFIG_DIR` directories for cold runs; deliberately
   record what memory was seeded or retained for warm runs. Never copy a holdout's successful
   repair into its cold baseline/current memory. Record different settings as experimental
   conditions, not as improvements attributable solely to the new code.
3. Preserve the final `recovery.json` for the whole recovery session, including failed attempts
   and its continuation resource history. Do not include an earlier snapshot of that same
   session as another case: its costs and attempts are already in the final report. A pair must
   cover the complete same source batch; a successful subset cannot stand in for a failed batch.
4. Time the whole task from its initial request through final human review, including failed
   attempts, clarification and manual intervention. Define a manual intervention consistently
   before the experiment: one human clarification, mapping choice, manual document correction
   or restart prompted by a failure is one intervention. Record the policy in your experiment
   notes. Record pauses/overnight waits separately so the elapsed-time interpretation is clear.
5. Record model spending outside the selected recovery report: initial conversation, workspace
   planning, independent failed attempts and other model work. This is **additional**, not a
   cumulative workspace total that already contains recovery spending. Count each call once.
   Use zero only when you have established there was no additional spend; use null when any
   part is unknown. The recovery ledger already includes its retained reservations and every
   recorded continuation. Estimates are not provider bills.
6. Review every resulting file against the full original request, original wording, and visual
   output in Word. Fill in the three human-review judgments and reviewer name. Do not mark an
   uninspected batch accepted. Retain unsuccessful and unreviewed runs in the manifest.

This version compares one complete recovery report per revision per pair. It does not ingest
`campaign.json` or ordinary batch reports directly. A workspace with several independent
recovery sessions needs separate complete pairs with separately attributable measurements;
do not copy one cumulative campaign cost into each. If an earlier standalone failed run was
part of a paired task, include its costs/time in the explicit workflow measurements and retain
that report beside the experiment. Missing recovery reports or revisions that cannot express
the same approved task must be recorded as a corpus coverage limitation, not replaced with a
fabricated report. No assertion about the entire workflow is justified by recovery scope alone.

## Record the manifest

Copy `examples/evaluation/paired-scripted.json` as a structure, replacing both cases completely.
Set `execution` to `live` only for runs whose real provider provenance is known, `scripted` for
fixtures, or `unknown` when it was not recorded. Each case needs:

| Field | Meaning |
| --- | --- |
| `id` | Unique run label, such as `architect-a-baseline`. |
| `pair_id` | Same batch/trial label on exactly one baseline and one current case. |
| `variant` | `baseline` or `current`. |
| `cohort` | Same explicit cohort on both halves, e.g. `holdout-cold`. |
| `revision` | Full lowercase 40-character Git commit SHA; one revision per variant. |
| `report` | Recovery JSON path, relative to the manifest or absolute. |
| `report_sha256` | SHA-256 of the exact retained JSON bytes. |
| `human_review` | `request_satisfied`, `content_preserved`, `visual_checked`, reviewer and notes. |
| `workflow` | End-to-end measurements described below; every field is explicit, possibly null. |
| `expect` | Optional behavioral expectations, using the existing version 1 vocabulary. |

For example, replace the example's `workflow` object with recorded observations:

```json
{
  "elapsed_seconds": null,
  "human_interventions": null,
  "additional_model_cost_usd": null
}
```

Do not replace null with zero to make a report complete. With top-level
`"measurement_scope": "workflow"`, the evaluator adds the reported recovery cost to
`additional_model_cost_usd`, uses the explicit end-to-end elapsed time and intervention count,
and retains the original recovery-only cost/time separately in JSON. All failed work belongs
in the recorded totals. With `"measurement_scope": "recovery"`, omit `workflow`, use the
case's optional `human_interventions`, and measure only `recovery.json` time and cost.

Compute a report hash using Python (the command does not inspect any document):

```text
python -c "import hashlib,pathlib; print(hashlib.sha256(pathlib.Path('baseline/recovery.json').read_bytes()).hexdigest())"
```

Reports are pinned before evaluation. If a legitimate resume changes one, preserve the earlier
evidence, review the complete final report, and explicitly update its manifest hash. An altered
report is otherwise refused. The evaluator does not establish that a supplied revision really
produced a report or that supplied human judgments are correct.

## How pairing protects the comparison

Matching pair labels are insufficient. The evaluator also requires the same exact request,
complete source SHA-256 multiset, auxiliary/template SHA-256 multiset and approved outcome.
Hashes are read from the report's recorded input pins; the evaluator does not re-open source
documents. Paths may differ across checkouts, but changed source/template bytes cannot pair.
All cases must belong to complete pairs; duplicate reports, multiple snapshots of the same
`recovery_id` and duplicate source paths are refused instead of silently dropping difficult runs.
Reports must retain a session `recovery_id` and the `key.recovery_templates`/`key.recovery_inputs`
lists (empty lists are valid); every listed auxiliary file needs its input pin. Older reports
without this evidence remain usable for version 1 summaries, not paired claims.
Scripted/live/unknown and different cohorts
cannot pair. Groups remain separate by execution provenance and cohort.

For adaptive workspaces, the independent approved outcome contract is compared; the execution
recipe in `key.goal` may change. With legacy reports, an identical recipe is conservative
evidence of the same task. When legacy recipes intentionally differ (or a legacy run is paired
with a new contract), a human must review their common intended outcome and add the following
explicit declaration to **both** cases, with the same precise statement:

```json
{
  "reviewed_outcome": {
    "statement": "Every original Heading 1 maps to office PRT and Heading 2 to ART using the pinned office template; all other wording and formatting are preserved.",
    "reviewer": "Name of the person who checked the two intended outcomes"
  }
}
```

This establishes the evaluator's comparison scope only. It neither authorizes a document edit
nor proves either run met that scope. Source/template hashes, exact request and approved words
still must match. Actual recipe/contract hashes remain in the result. Conflicting user-approved
target selections also require a reviewed common outcome; an unresolved baseline with no
selection remains a legitimate failure. Differing methods and source-bound target evidence are
disclosed; differing model, effort, renderer or options are also disclosed, including distinct
recorded continuation resource policies, continuation counts and actual serving-model lists.
Path relocation applies only to attached-file schema fields; approved literal wording stays
verbatim even when it happens to be a pathname. Use declarations
only after inspecting both intended outcomes, not to force a mismatched corpus into a pair.

## Interpret the results

Each pair and cohort reports **current minus baseline**. More human-accepted batches is better;
less elapsed time, fewer interventions and lower cost are better only at acceptable quality.
The tables show total batches/files, ready files, review coverage, confirmed accepted counts,
acceptance rates, elapsed time, interventions and live estimated cost per accepted batch.

Human acceptance requires all files READY, recorded supported final assertions passing, and
all three human-review judgments true. An acceptance rate remains unknown until every batch
in that cohort has complete human review; the confirmed accepted count is still shown. An
unknown time, intervention count or cost makes that aggregate and its delta unknown. Live cost
per confirmed accepted batch includes failed and unreviewed live runs; its denominator may
change as reviews finish. Scripted and unknown execution never produce live cost comparisons.

Model review, delivery and structural readiness remain separate from human acceptance. Publish
the corpus size, exclusions, review coverage, changed conditions and unresolved cases with any
improvement claim. These are descriptive paired measurements, not statistical evidence that
one code change caused the difference. Repeat real provider runs when variability matters.
