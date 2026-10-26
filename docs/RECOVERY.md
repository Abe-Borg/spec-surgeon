# Recovery and local repair memory

Contract-bearing batch, chain and generated runs retain their approved outcomes and exact source bindings in
set-aside artifacts. The conversation window's saved checkpoint resumes recovery under that same authority,
refusing missing or inconsistent approvals. Ordinary delivery is governed by the preview the user
approved and the independent verifier; see [Reliability and approved outcomes](RELIABILITY.md).

Spec Surgeon v0.7.1 adds a bounded investigation workflow for requests that fail during
compilation, preview, or a batch/chain run. Recovery can inspect document structure and XML,
template data, diagnostics and rendered pages; try a repair on copies; read the verification
results; and revise its approach.
It stops when supported repairs succeed, a limit is reached, a user decision is needed, or the
required capability is unavailable.

## In the conversation window

Recovery runs inside the conversation and needs no setup. When **Create edited copies** leaves a
file that did not pass, or a sample fails during the preview, the app investigates those files on
copies in the same conversation. It shares the conversation's ledger (no spending or token ceiling,
the pause every 48 AI steps, 128,000 output tokens per call), up to eight candidate attempts per
problem group and high reasoning effort (`xhigh` while it writes a custom method:
`resources.CODEGEN_EFFORT`); **Stop now** cuts it off like any other step. The
window has no budget, attempt or model controls; Settings holds only the API key.

A question from the investigation appears in the chat once, as buttons or a text box. The result
appears as the ordinary plan view: the ready copies, the documents that still need attention with
their reason, notes and available before/after pages. A blocking visual finding is listed under
**Please check**. The one action, **Create edited copies**, exports the ready copies and records
that you looked at the findings shown with them. Ready copies can be exported while other documents
are still being investigated; each export goes to a new folder. Recovery never writes over the
originals. A structural verification result is not a judgment of user intent: inspect whether the
result satisfies the complete request.

Custom methods are prepared and tested on sample copies internally, only under an independently
checked outcome contract, and end in the same action. Local repair memory is read and recorded in
the window; there is no switch to turn it off.

The saved conversation keeps the location of the current investigation. Reopening restores its
attempts, evidence and pending decisions; a changed source invalidates the dependent candidates.
A new failed conversation or preview uses its current files, rather than silently retrying an
unrelated older run. A chain that already wrote stages in place cannot be replayed automatically:
start from original copies to avoid performing successful earlier edits twice.

## Layout runs, allowances and records

A layout run (the window's plan per layout) writes one batch folder per layout, `layout-<id>`, each with its own
specification and `set_aside.json`, and no manifest at its root. Recovery takes the layouts whose documents still
need work one at a time, each against its own specification. A document that no layout covered has no specification
to recover against and is not among them.

The same allowance covers investigation, generated-transform calls and visual-review calls. A conservative
reservation is checked before each call; calls with missing usage retain their reservation. These are listed-price
estimates and application limits, not a provider billing guarantee. The report retains usage, reservations and stop
reasons. Independent problem groups take turns investigating, so a long first group does not monopolize the initial
model calls. Reopening restores the saved resource settings; each continuation receives a fresh allowance at those
settings, with prior usage retained in the resource history.

`RECOVERY_REPORT.md` and `recovery.json` retain the investigation, attempts and evidence.
`set_aside.json` retains unresolved files and their goal for compatibility with older workflows.
The durable recovery state retains the full investigation for resume, including ready candidates.
Exported copies go
under `outputs/`; intermediate trials and their verification records stay in the investigation
folder. Reusing a nonempty delivery folder is refused.

Recovery preserves filenames assigned by naming operations. If separate problem groups would
produce the same explicit filename, those files remain unresolved instead of overwriting each
other. Tables and images named by the specification or the approved goal are inspectable inputs
whose contents are pinned before investigation and rechecked at export; nothing beside the
documents is discovered.

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

For EditSpecs without an adaptive outcome contract, custom recovery remains opt-in and
restricted to page-setup goals. Approved adaptive contracts provide a broader independent boundary:
agents can choose another built-in sequence or a sandboxed custom method, including supported
exact-wording edits. Automatic custom methods are enabled for these contracts unless explicitly
disabled. Every result must pass both the ordinary suite and independent original-to-final checks.
A separately prompted critic can reject a candidate or request more investigation; it cannot
override a failed check.

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
There is no model-weight training or shared cloud pool of repairs. The conversation window always
uses it, and since the command line was removed (2026-10-21) nothing in the app inspects, accepts,
rejects or forgets a case; entries stay until the size bound prunes them or the database is deleted.

Similarity only helps find evidence. Automatic reuse requires the current goal, request,
document pattern, template identities and application version to match the applicability checks.
Each reuse must still satisfy current wording rules and fresh verification on every file.
Past project wording never becomes current approved wording merely because it was remembered.
Failed cases remain useful evidence of approaches to avoid.

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
been validated by those tests. Real batches are needed before making reliability or savings claims (the earlier
evaluation protocol is kept in [docs/history/RECOVERY_EVALUATION.md](history/RECOVERY_EVALUATION.md)).
