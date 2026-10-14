# Playbook — untracked edits [unproven]

Every proven job tracked its edits under the user's name, and tracked is the default: a tracked edit is reversible by the reviewer, visible in All Markup, and checked by V5's untracked-edit detector. An untracked (plain) edit is acceptable only when the user explicitly asks for it, and the usual reasons are:

* the document is not under review (no `trackRevisions`, no pending changes from anyone) and the user wants a clean file;
* the change is mechanical and invisible to a reader (a style attribute, a numbering definition, a field flag), where a `pPrChange`/`rPrChange` would only add noise;
* the user is the sole author and says so.

If the document carries anyone else's pending tracked changes, recommend tracking anyway — an untracked edit beside someone else's revisions is indistinguishable from an edit they made.

## What changes in the method

The contract does not change: in formatting mode no characters are generated; in content mode only approved words are. What changes is the verification arithmetic, because the edit is no longer wrapped in a revision:

* **V2** — the enumerated diff list carries the edit itself: for each edited block, the exact before and after strings. The verifier asserts those blocks differ exactly as predicted and every other block is byte-identical. This replaces the "text differs only inside `w:ins`/`w:del`" guarantee with "text differs only in the enumerated blocks".
* **V4** — predicted delta is 0 / 0, and the assertion that the document's existing revision count is unchanged becomes the proof that nobody else's tracked changes were disturbed.
* **V5** — run `validate.py` WITHOUT `--author`: with it, the redlining check correctly reports the untracked edit as a failure. Say in the report that the redlining check was deliberately not run and why, and that V2's enumerated list stands in for it.
* **V8** — N/A (no revisions were added), recorded as such.
* **Everything else** — unchanged: hash whitelist, structural children, XSD against the source baseline, render and look, geometry where a reference exists, overwrite guard.

## Report

The report says in its first line that the edit is untracked at the user's request, so a reviewer opening the file in All Markup is not surprised to see nothing.
