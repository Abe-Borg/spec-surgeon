# Reliability and approved outcomes

The operation library supplies deterministic editing methods. A method's verification proves that its declared
edit preserved the required source properties. An independently reviewed outcome adds a separate question:
does the final document satisfy the intended effects on the approved original targets?

## Desktop review

Open **File → Built-in operations…** to search all 50 operations and inspect their inputs, selection rules,
behavior, limits and checks. Related operations link to the four existing guided workflows. Browsing is offline
and does not change the current recipe. Guided workflows and saved EditSpecs remain usable without an API key.

For natural-language edits, Preview prepares two kinds of evidence: the editing recipe and a separately proposed
outcome contract based on the request and original document inventories. The outcome card shows the full contract,
literal wording, counts and per-document targets. Ambiguous matches require deliberate choices; none is preselected.
Approve the current outcome review, inspect the sample preview and any visual findings, then Run. A changed request,
recipe, source, template or scope invalidates the relevant approval.

A requested one-off custom method prepares its outcome review before code generation. Approving that review
allows generation and trial on copies; the resulting preview still needs inspection before Run.

If independent outcome coverage is unavailable, the natural-language flow pauses. Clarify the request or deliberately
load an explicit saved recipe and review its narrower verification scope. The supported outcome vocabulary is in
[Adaptive workspaces](ADAPTIVE_WORKSPACES.md) and [Built-in workflows](BUILTIN_WORKFLOWS.md). It does not cover every
possible request or every operation combination. A contract proposal is model output until the user approves it.

Workspace **Only selected documents** scope persists for direct title, EditSpec and chain goals. Valid saved recipes
are tried deterministically before a model is needed; a keyless workspace pauses clearly if investigation is required.
The application refuses to close while background work is active.

## Command line approval

An interactive `do` prints the separate outcome review and asks for approval. A noninteractive run saves
`outcome-review.json` and stops until that exact review is approved. `--yes` only accepts execution prompts;
it cannot approve outcome evidence that has not been inspected.

```text
spec-surgeon do "Project specs" "Make the section titles bold" --out review-run --no-render
# Inspect review-run/outcome-review.json and its printed digest and targets.
spec-surgeon do "Project specs" "Make the section titles bold" --out approved-run --no-render --yes --outcome-review review-run/outcome-review.json --outcome-approval DIGEST
```

Use the digest actually emitted for your review. The review must match the current request and source files.
The printed continuation command can also run the saved implementation with `run-spec` while retaining the
`--outcome-review` and `--outcome-approval` flags. A manual `run-spec` without them remains recipe-only.

`retry` and **Load set-aside files** retain a contract-bearing run's approved contract and exact source bindings.
They resume recovery on copies; a revised method cannot drop an approved effect or pick a different ambiguous
target. Missing or inconsistent saved authority is refused. A clarification does not authorize expanded effects.
Older in-place chains that already wrote partial stages require original copies before replay.

## Source and delivery checks

Preview captures source and auxiliary-input identities before planning/rendering, including content digests.
Planning and sample reads are checked against those identities; observing a change invalidates approval even if
the original bytes and timestamps are subsequently restored. Ordinary batch workers compare acquired identity
with the actual bytes they read. Preview identities carry into execution, and every source is pinned before work
starts so a queued file cannot adopt a later save as its baseline. Chain and recovery delivery recheck originals
after final outcome verification.

Source documents, templates, input tables and pictures must be outside results trees. Existing symlinks, Windows
junctions and hardlinks that could alias an input are refused before writes or cleanup. A new run clears previous
delivery artifacts so an earlier PASS cannot survive a later failure under the same output name.

Templates, tables and pictures are copied into pinned auxiliary snapshots for preview and execution. Reports retain
the mapping to original inputs; changing an original before delivery prevents export. Snapshot copies are excluded
from diagnostics bundles regardless of their file extension. Diagnostic reports can still quote document text.

For approved contracts, both batch and chain final results must pass the independent original-to-final verifier.
Contract-bearing in-place chains stage on copies and write each original only after its complete result passes;
this is per-file delivery, not a transaction across the entire batch. Manual chains retain their documented
stage-by-stage in-place behavior. Existing guarded atomic replacement remains the only in-place write path.

Recovery checkpoints use versioned approval tokens. Valid older recovery checkpoints can be revalidated against
their original full content pins and upgraded on the next save; an old metadata-only preview token is never
accepted as a recovery capability. Acknowledging visual concerns is not persisted as delivery authority.

## What PASS means

The per-file JSON and Markdown records list checks performed and checks marked N/A with reasons. Rendering may be
unavailable or intentionally disabled; some layout-changing operations have no source-geometry comparison.
PASS means no performed required check failed. It does not mean that unavailable checks ran, that Microsoft Word
was used, or that someone visually accepted the result. Batch and chain reports explicitly distinguish recipe-only
verification from approved outcome verification.

V6 is **Automated render comparison**. V7 requires matching word evidence on the pages that must remain stable;
a blank PDF or missing unchanged words cannot pass by matching zero anchors. PDF evidence is specific to the
renderer used. Human review in Word remains necessary for production acceptance and renderer-specific behavior.
For header/footer removal, an independently rendered source copy identifies intentionally removed margin words;
all surviving words must retain their original positions, and body words remain required. A header intruding into
the body region is handled conservatively rather than exempting that region.

Text-edit verification independently reconstructs allowed character edits and compares paragraph items, fonts,
hyperlinks, tabs and revision context. Changed text alone does not exempt the entire paragraph from preservation.
Tracked deletion preserves interleaved bookmark/proofing boundaries; repeated paragraphs align by source position
after declared structural changes. New custom editing primitives need analytical source predictions and independent
item-level assertions; a matching text diff alone is insufficient.

## Generated transforms

Generated code runs in a separate, restricted process. Defaults allow 60 seconds, 2 GiB process memory,
256 MiB document output, 16 MiB serialized plan and 1 MiB normal diagnostics per stream. Host callers can configure
the process limits separately from model allowances. Linux uses address-space, CPU and file-size resource limits;
Windows uses a Job Object for process memory and checks document/plan sizes before writing. Windows does not have
an OS file-size ceiling here. Parent log capture is file-backed and only a bounded tail is read into memory.
Unavailable memory isolation fails closed. These controls contain accidental resource exhaustion; they do not
turn the Python restriction layer into an OS security jail.

The model's ample existing allowances remain unchanged: 900,000 estimated working-context tokens and 64,000 output
tokens by default, with the configured investigation call, attempt and spending budgets. Model output/effort
overrides also reach visual review. A streamed response needs completed blocks, terminal message evidence and
valid final tool JSON before it is accepted. Truncated streams and context-window cutoffs fail; incomplete calls
retain their reservations instead of being reported as cheaply completed work.

## Packaging and evidence limits

All 49 worked EditSpecs ship as package resources. The source checkout, wheel and frozen application use the same
examples and compiler prompt; a missing bundle fails visibly. Installer publication checks the exact source SHA
against the release tag and never replaces an existing version's installer with a different master build.
Checksum publication verifies the actual release assets and retains the prior manifest as a backup while promoting a staged replacement, so interrupted updates can be retried without replacing the installer.

Regression tests exercise synthetic Word packages, real local LibreOffice PDFs, scripted model exchanges and real
SDK streaming through an offline HTTP transport. They do not establish success rates on customer projects,
live-model spending or human-accepted Microsoft Word appearance. Use the existing
[paired evaluation workflow](WORKFLOW_EVALUATION.md) on representative batches to measure those outcomes.
