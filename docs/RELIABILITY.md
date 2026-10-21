# Reliability and approved outcomes

The operation library supplies deterministic editing methods. A method's verification proves that its declared
edit preserved the required source properties. An independently reviewed outcome adds a separate question:
does the final document satisfy the intended effects on the approved original targets?

## Desktop review

The conversation window has one approval. Attach the Word documents and, optionally, one template, then type the
request. Each attachment is triaged the moment it is added, without a model call: a file the engine cannot work
with (not a `.docx`, a macro-enabled file, a Word lock file, a package that does not open, an empty body) is refused
in one sentence and never enters a plan; a note says what to expect (typed numbers, no styles, text-box headers,
compatibility mode, protection, pending tracked changes, comments).

The compiler turns the request into one EditSpec per structural layout of the batch. The plan shows one sentence per
change, written by the operation itself, with assumptions inline; a mixed batch shows one card per layout. The
preview applies and verifies the specification on one sample per layout and shows before/after pages. Files left
untouched are listed with their reason. Clarifications are buttons; typing also works.

**Create edited copies** approves exactly the displayed preview. Its approval token covers the specification, the
documents, the template and every other named input; if any of them changes, the action is invalid and the plan is
prepared again. Every delivered file must pass the independent verifier (V1–V10); a file that fails is set aside,
never delivered, and its recovery runs inside the same conversation and ends in the same action. A blocking visual
finding appears under **Please check**; the same action records that you looked at it.

There is no second, model-derived outcome contract for ordinary edits and no outcome review gate. Custom methods are
prepared and tested on sample copies internally before their preview. Governed recovery keeps its approved contract
and exact source bindings. The window refuses to close while work is active. There is no command line (removed
2026-10-21) and no keyless path: every request needs an API key.

## Set-aside files

A run whose set-aside manifest carries an approved outcome contract recovers on copies under the same authority: it
cannot drop an approved effect or pick a different ambiguous target, a clarification does not expand effects, missing
or inconsistent saved authority is refused, and writing back in place is refused. A run without a contract is
recovered as an ordinary pass.

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
the mapping to original inputs; changing an original before delivery prevents export. Diagnostic reports can quote
document text.

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

The model's ample existing allowances remain unchanged: 900,000 estimated working-context tokens and 128,000
output tokens (the model's maximum) by default, with the configured investigation call, attempt and spending budgets. Model output/effort
overrides also reach visual review. A streamed response needs completed blocks, terminal message evidence and
valid final tool JSON before it is accepted. Truncated streams and context-window cutoffs fail; incomplete calls
retain their reservations instead of being reported as cheaply completed work.

## Packaging and evidence limits

All 50 worked EditSpecs ship as package resources. The source checkout, wheel and frozen application use the same
examples and compiler prompt; a missing bundle fails visibly. Installer publication checks the exact source SHA
against the release tag and never replaces an existing version's installer with a different master build.
Checksum publication verifies the actual release assets and retains the prior manifest as a backup while promoting a staged replacement, so interrupted updates can be retried without replacing the installer.

Regression tests exercise synthetic Word packages, real local LibreOffice PDFs, scripted model exchanges and real
SDK streaming through an offline HTTP transport. They do not establish success rates on customer projects,
live-model spending or human-accepted Microsoft Word appearance; only real batches on the owner's machine measure
those outcomes.
