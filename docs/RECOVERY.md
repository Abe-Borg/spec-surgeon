# Recovery and local repair memory

Spec Surgeon v0.7.1 adds a bounded investigation workflow for requests that fail during
compilation, preview, or a batch/chain run. Recovery can inspect document structure and XML,
template data, diagnostics and rendered pages; try a repair on copies; read the verification
results; and revise its approach.
It stops when supported repairs succeed, a limit is reached, a user decision is needed, or the
required capability is unavailable.

## In the desktop application

1. After a failed compile or preview, follow **Investigate with recovery…** or **Resolve
   remaining files…**. After a run, use **Run & results → Resolve remaining files**.
2. Set the estimated spending budget, candidate-attempt limit and model-call limit. An optional
   clarification box lets you answer a question or clarify the request. Custom formatting
   transforms require a separate checkbox; local repair memory can be disabled for this run.
3. Start the investigation. Its results appear on **Preview**, with ready copies, unresolved
   files, questions, notes and available before/after pages. Inspect whether the result satisfies
   the complete request. A structural verification result is not a judgment of user intent.
4. A blocking visual finding requires the existing **I've looked — allow Run** acknowledgement
   for this preview. **Export recovery copies** then rechecks the inputs and verified output
   bytes before exporting. Recovery never writes over the originals.

Export any ready copies before starting another investigation. The next investigation reads
only the current recovery's unresolved-file manifest. A new failed conversation or preview uses
its current files, rather than silently retrying an unrelated older run. A chain that already
wrote stages in place cannot be replayed automatically: start from original copies to avoid
performing successful earlier edits twice.

## At the terminal

From a prior batch, chain or recovery folder containing `set_aside.json`:

```text
spec-surgeon resolve "previous-run" --out "recovery-01" --budget 5 --max-attempts 3 --max-calls 12
```

In a terminal, the command shows the investigation and visual findings, then asks before
exporting. Without an interactive terminal or `--yes`, it writes the investigation for review
and does not export. `--yes` approves export; it **does not** acknowledge blocking visual
findings. `--ack-review` is the separate explicit acknowledgement for findings you have inspected.
There is no recovery `--in-place` option.

Useful options:

| Option | Meaning |
| --- | --- |
| `--request "..."` | Append a clarification or answer to the original request; it does not replace the pinned goal. |
| `--model MODEL --effort medium` | Choose recovery's model and effort (`low`, `medium`, `high`, `max`). |
| `--budget 5` | Estimated model-spending limit in USD, default 5. |
| `--max-attempts 3` | Candidate-attempt limit per problem group, default 3. |
| `--max-calls 12` | Shared model-call limit for the investigation, default 12. |
| `--allow-codegen` | Permit offered custom formatting transforms within the restricted recovery contract. |
| `--no-memory` | Neither retrieve nor record local repair cases for this investigation. |
| `--no-render` | Skip rendering and visual review; this leaves visual acceptance to you. |
| `--renderer word` | Choose `auto`, `word`, `libreoffice`, or `none`. |

The same budget covers investigation, generated-transform calls and visual-review calls. A
conservative reservation is checked before each call; calls with missing usage retain their
reservation. Unknown pricing prevents further calls under a USD budget. These are listed-price
estimates and application limits, not a provider billing guarantee. The report retains usage,
reservations and stop reasons. The Python `RecoveryOptions` also exposes output and total-token
limits; its defaults are 8,000 output tokens per call and 500,000 total tokens.

`RECOVERY_REPORT.md` and `recovery.json` retain the investigation, attempts and evidence.
`set_aside.json` retains unresolved files and their goal for another pass. Exported copies go
under `outputs/`; intermediate trials and their verification records stay in the investigation
folder. Reusing a nonempty delivery folder is refused.

Recovery preserves filenames assigned by naming operations. If separate problem groups would
produce the same explicit filename, those files remain unresolved instead of overwriting each
other. Tables and images listed beside the documents, or named by the approved goal, are
inspectable inputs whose contents are pinned before investigation and rechecked at export.

## What a repair may change

For an existing EditSpec or chain, recovery preserves the approved operation effects, target
selectors and desired values. It can revise extraction rules and expected counts, or split and
reorder stages. It cannot obtain success by dropping an operation, narrowing its scope,
inventing wording or weakening verification. Each stage plans from its actual input and uses
the existing independent verification suite.

Declared final assertions check supported properties again after the last stage. A later stage
that violates an earlier declared final requirement prevents final delivery even when every
individual stage passed. Assertions currently cover effective fonts and sizes, page setup, and
supported run/paragraph boolean properties (such as bold, italic, keep-with-next and page-break-before).
Paragraph spacing and header/footer absence are not final-assertion kinds. These are explicit checks, not an independent
translation of arbitrary prose into a complete acceptance test. Template appearance and other
semantic or visual requirements still need review.

Source-relative final font requirements, including preservation exceptions or sizes derived
from the original source, are currently refused by recovery when they cannot be represented
by its independent final checks. They are not silently dropped or replaced by a visual check.
An EditSpec font assertion that omits `preserve_fonts` still has the engine's default symbol-font
exceptions (including Symbol and Wingdings), so the same limitation applies. An explicitly empty
`preserve_fonts` list has no font-name exceptions and can become a universal final-font check.
EditSpec size assertions still exempt runs containing symbols, so recovery cannot translate
those into universal final-size checks even with an empty preservation list. Recovery cannot
remove preservation rules from the original request to make a candidate pass.

Custom recovery is opt-in and currently restricted to a goal made entirely of page-setup
operations. Generated code remains sandboxed and subject to the existing formatting limits.
Its output must also pass verification against the original goal's analytically predicted
plan, independently of the plan supplied by the generated transform. Broader custom recovery
requires additional independent verification support.

## What the application remembers

Repair memory is a local SQLite database, `repair-memory.sqlite3`, under the application's
configuration directory. `SPEC_SURGEON_CONFIG_DIR` can select a separate directory for evaluation.
It records requests, failure categories, candidate repairs, applicability conditions, verification
evidence, visual-review evidence, costs and user corrections. It is bounded in size and stores
descriptors rather than document packages or executable saved code.
Each case retains only the visual review of that candidate's verified outputs. Findings from
other document groups or earlier attempts remain in the investigation where relevant, but are
not attributed to a new repair case. An attempt without verified output is recorded as not reviewed.

Freeform requests, notes and feedback are retained locally; the store is not an anonymizer.
When memory is enabled, relevant previous cases can be included in a recovery model request.
There is no model-weight training or shared cloud pool of repairs. Disable memory for a run
with the checkbox or `--no-memory`. Existing entries remain until forgotten.

Similarity only helps find evidence. Automatic reuse requires the current goal, request,
document pattern, template identities and application version to match the applicability checks.
Each reuse must still satisfy current wording rules and fresh verification on every file.
Past project wording never becomes current approved wording merely because it was remembered.
Failed cases remain useful evidence of approaches to avoid.

Use **File → Repair memory…** to inspect a case, accept or reject its result, or forget it. The
equivalent commands are:

```text
spec-surgeon memory list
spec-surgeon memory show CASE_ID
spec-surgeon memory accept CASE_ID --note "Checked the result in Word"
spec-surgeon memory reject CASE_ID --note "The footer font was wrong"
spec-surgeon memory forget CASE_ID
```

Accepting records your judgment; it never bypasses future checks. Rejection blocks automatic
reuse of that repair across matching duplicate cases. A later acceptance does not erase the
negative evidence. Forgetting removes the named record, not other records of the same repair.

## Developer entry points and validation

`recovery.recover(...)` investigates current files; `recover_from_run(...)` reads a set-aside
manifest. Both return a `RecoveryResult` with `READY`, `UNRESOLVED` or `NEEDS_INPUT` files.
`deliver_recovery(...)` is the separate export boundary. It checks frozen source/template
identities, recovery requirements, candidate identities and verified output bytes. Review
acknowledgement is bound to the current token and is never restored from saved JSON.

`llm.CallBudget` and `budget_scope` apply limits at the one model-call site, including nested
calls. `verify.outcomes` independently checks final assertions; it must retain verifier import
independence. `repair_memory.CaseStore` stores evidence and feedback; applicability never grants
delivery authority. Changes to these contracts need adversarial tests, including per-file
verification, stale inputs, dropped goals and later stages undoing earlier requirements.

Automated tests use synthetic documents and scripted model replies. Live Claude repair quality,
real-project success rates, actual API economics and Microsoft Word COM fidelity have **not**
been validated by those tests. Use the [real-batch evaluation protocol](RECOVERY_EVALUATION.md)
before making reliability or savings claims.
