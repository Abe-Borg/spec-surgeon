# Evaluate complete ordinary and adaptive workflows

Version 3 adds adapters for actual `batch.json`, `chain.json`, `campaign.json` and
`recovery.json` reports. It preserves original input hashes, recipe/approved outcome,
source-bound target choices, recorded checks including N/A, renderer, file statuses,
and nested artifact lineage. It does not rerun document verification. Recovery
evaluation versions 1 and 2 and their existing commands remain unchanged.

## Save immutable evidence snapshots

```text
spec-surgeon evaluate adapt /runs/baseline/batch.json --out /evaluation/baseline.json
spec-surgeon evaluate adapt /runs/current/chain.json --evidence current-evidence.json --out /evaluation/current.json
spec-surgeon evaluate compare /evaluation/pairs.json --out /evaluation/result.json
```

The standalone equivalent is `python -m spec_surgeon.workflow_evaluation` with the
same `adapt` or `compare` arguments. Output paths must be new; existing files are
never overwritten. The commands read local JSON only and call no providers.

Batch adapters read saved verification records. Chains retain each stage and its
checks while counting original documents once. Campaigns retain recovery lineage
and project resource accounting. Recovery adapters preserve recorded final checks;
they explicitly disclose that detailed attempt-side verification records were not
ingested. Missing evidence remains unknown, rather than becoming a zero or a PASS.

The adapted snapshot contains hashes of every report and verification sidecar read.
Pin the snapshot's own SHA-256 in the manifest. Its nested artifacts need not remain
online to compare the retained evidence. If comparing raw reports directly, provide
`artifact_sha256` mapping every ingested sidecar/nested-report absolute path to its
hash; the evaluator refuses unpinned supplemental files.

New ordinary reports record source SHA-256 from the exact bytes acquired and checked
by the worker, including in-place runs. Template snapshots record their acquired
SHA-256. Old ordinary reports may lack these fields. Never substitute hashes of the
current source file for missing historical acquisition evidence.

An optional `--evidence` JSON can supply *previously retained* missing input pins,
the original whole-task request when it was not recorded (ordinary chains record
stage requests), and a reviewed common outcome:

```json
{
  "request": "Apply the reviewed office styles while preserving the specification's wording.",
  "source_pins": {"/original/21 13 13.docx": "64-character lowercase SHA-256"},
  "auxiliary_pins": {"/original/office.docx": "64-character lowercase SHA-256"},
  "reviewed_outcome": {
    "statement": "Every original part heading uses PRT and every article heading uses ART from the pinned office template; other wording and formatting are preserved.",
    "reviewer": "Person who inspected both intended outcomes"
  }
}
```

Hashes above are explanatory placeholders and must be replaced with actual retained
digests. Supplemental evidence is labelled evaluator-supplied. It cannot contradict
recorded hashes or add sources to the population. A common-outcome declaration
permits a changed implementation or reviewed target choices; it is comparison
metadata, not edit authorization or proof that either result satisfied the request.

## Version 3 manifest

```json
{
  "version": 3,
  "cases": [
    {
      "id": "office-a-baseline",
      "pair_id": "office-a",
      "variant": "baseline",
      "cohort": "development-cold",
      "execution": "unknown",
      "revision": "40-character lowercase Git commit SHA",
      "report": "baseline.json",
      "report_sha256": "64-character lowercase SHA-256",
      "workflow": {
        "elapsed_seconds": null,
        "human_interventions": null,
        "additional_model_cost_usd": null
      },
      "human_review": {
        "request_satisfied": null,
        "content_preserved": null,
        "visual_checked": null,
        "reviewer": "",
        "notes": ""
      }
    }
  ]
}
```

Add exactly one `current` case for every baseline, using a separate complete run.
This abbreviated example deliberately has one case and placeholder hashes; it is a
schema guide, not runnable evidence. Pairing requires identical source and auxiliary
hash multisets, original request, approved words/assertions, outcome identity and
approved source target choices. A reviewed common outcome can explicitly explain
changed methods/targets. Sources may be relocated. Literal text is not path-normalized.
Execution provenance and cohort must match. Reusing a recovery session, nested report,
or earlier snapshot as a second case is refused to avoid double counting.

## Measurements and cost accounting

`workflow.elapsed_seconds` includes the whole task through review, not a sum of
parallel workers. `human_interventions` follows a declared experiment policy.
`additional_model_cost_usd` includes spending outside the selected root report and
must not include nested spend already represented there. Unknown is `null`; explicit
zero means the experiment established none occurred.

Deterministic batch/chain engine cost is zero because those APIs do not call models;
planning/conversation costs are additional. Recovery costs include all retained
resource segments and reservations. For adaptive campaigns, project execution
ledgers already include shared child recovery spending, so the evaluator uses the
parent execution ledger **or** unique child totals, then adds planning ledgers.
It never sums both representations. Missing costs propagate as unknown. These are
estimated costs, not provider invoices.

Readiness and human acceptance are separate. Acceptance requires all files ready,
no recorded failed checks, explicit positive request/content/visual judgments and
a nonblank reviewer. Missing review is unknown. A human can record rejection even
when automated checks passed. Acceptance rates stay unknown until every run in a
group has complete review. Failed and unreviewed work stays in cost/time totals.
Only `live` execution can produce a live cost-per-accepted-batch number.

## Reproducible synthetic acceptance corpus

`tests/workflow_corpus.py` creates 12 actual Word documents covering ordinary and
styled headings, typed headings, direct formatting, automatic lists, tables,
headers/footers, Unicode, repeated numeric provisions, tracked insertion, multiple
sections and blank paragraphs. `tests/test_workflow_evaluation.py` runs the real
batch, chain, campaign and recovery paths, then adapts their actual persisted
reports. It verifies source preservation, explicit check coverage, unknown visual
acceptance, paired accounting and refusal of mismatched or reused evidence.

Both test variants run the current implementation; they validate the measurement
pipeline, not an improvement over an earlier revision. No corpus case was reviewed
in Microsoft Word or sent to a live model. Human-review objects used in aggregation
tests are explicitly synthetic judgments, not recorded acceptance of those files.

Before publishing a performance claim, use the representative development/holdout
protocol in [WORKFLOW_EVALUATION.md](WORKFLOW_EVALUATION.md), pin clean actual revisions,
retain unsuccessful cases, and collect human Word review and live-provider evidence
where relevant. The local adapters make that collection possible; they cannot supply
missing external qualification evidence.
