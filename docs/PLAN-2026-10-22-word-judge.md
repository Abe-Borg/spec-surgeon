# Spec Surgeon — Implementation plan: Word as the judge of tracked changes

2026-10-22. Owner's go-ahead after a comparison with Build-a-Spec, which has real Word check its redlines
(Build-a-Spec `docs/DOCX_FIDELITY.md` → "Real Word as the judge"; `docs/DOCX_FIDELITY_CORPUS.md` → "Word's own tracked
moves"). Test evidence only: no op, prediction or verifier rule changes in this round.

## Where this round starts

| What | Finding |
| --- | --- |
| Writer | V8/V5b simulate Accept All and Reject All in this engine's own XML and compare block text. `tests/test_batch6_round_trip.py` settles the engine's output with the engine's own `resolve_tracked_changes`, comparing text and row counts. Nothing outside the engine judges the revisions it writes |
| Reader | `resolve_tracked_changes` ships to users and settles real client redlines. The "someone else's pending changes" it is tested on (`tests/fx_revisions.py`, `make_fixtures.py`) are built in Python, in the shapes their author expected Word to write. C6 and C7 of the current round are both about other people's redlines |
| Word | `render/word_com.py` already has Word run Accept All / Reject All (`accept_reject_text`), but returns the main story's plain text only, and nothing calls it. The worker (`render/word_worker.py`) has the private-instance ownership proof and deadlines this round reuses |
| Files | CI sees synthetic fixtures only; the owner's private corpus (`tests/real/`) runs on the owner's machine. No checked-in file was saved by Word |

## Principles for this round

1. **The judge is not the engine.** The comparison lives in `tests/word_judge.py` and imports nothing from
   `spec_surgeon` (no `verify/`, `ops/`, `ooxml.py`, `package.py`): a judge that shares a reader with the engine can
   agree with it for the wrong reason. Copied ideas from Build-a-Spec are adapted, never imported.
2. **Symmetric.** Word re-saves the reference too (the untracked output, the source), so whatever a Word save rewrites
   on its own cancels without a tolerance.
3. **Four tolerances, in tests only.** What a Word save writes by itself, removed from both sides: rsid attributes,
   `w:proofErr`, `w:lastRenderedPageBreak`, the `_GoBack` bookmark. Never in the app or the verifier. A fifth needs a
   real run that shows it, a reason in the plan and a test that it removes exactly that.
4. **Placeholder content only.** No client file, no real name or path is checked in; a Word-made file is scanned for the
   local identity before its checksum is pinned.
5. **Word stays optional.** The judge collects as skips unless `SPEC_SURGEON_WORD_JUDGE=1` on Windows with Word. The app
   never depends on it.

## Workstreams

| Item | Goal | Expected outcome |
| --- | --- | --- |
| W1 — the comparison | Two Word bodies compared element for element, without Word | `tests/word_judge.py`: for each text part (document, headers, footers, footnotes, endnotes, comments) an own canonical form — adjacent runs with identical properties merged, empty runs/text/property containers dropped, `w14:paraId`/`w14:textId` ignored, bookmarks compared by name, XML comments and processing instructions dropped — then the four tolerances on both sides; the first difference reported with both sides and its path. A relationship reference (`r:embed`, `r:link`, `r:id`) compares by what it reaches, never by its id or part name (a Word save may renumber both): an internal target by the SHA-256 of its bytes (a picture's media), an external one by its URL, so two bodies with identical drawing XML and different pictures differ. `tests/test_word_judge.py` proves it without Word: each tolerance removes exactly what it names; a planted difference in text, formatting, a bookmark, a table row, a section break and a picture's media is reported; it imports nothing from `spec_surgeon` (pinned like `tests/test_independence.py`) |
| W2 — Word resolves and saves | Word's own Accept All / Reject All as a file | A `resolve` action beside `render` and `accept_reject` in `render/word_worker.py` / `WordSession`: open a copy read-only, `accept`, `reject` or `resave` (optionally only one author's revisions, for a source that already carries someone else's), save as a new .docx (never overwriting), report Word's version and build, the revisions read and their authors, and how many were left. Same private-instance proof and per-file deadline as rendering; one Word per group of files. Nothing in the app calls it |
| W3 — the writer judged | Every revision the engine writes, settled by Word | `tests/test_word_judge_real.py` (opt-in): the tracked output and the untracked output on fixtures, for every registered op whose `params_schema` accepts `tracked: true` (41 today, read from `ops.OPS`, never typed out), starting from `tests/test_batch6_round_trip.py`'s cases and `replace_text` inside someone's insertion; a coverage test (no Word needed) fails when such an op has no case. The sources already carry other authors' pending revisions, so Word settles only the plan author's (W2's author filter): Word must open every file and save every result; every new revision reads as the plan's author, none of the plan author's is left after, and the other authors' survive both ways; Word's Accept All of the tracked output equals Word's re-save of the untracked output, and Word's Reject All equals Word's re-save of the source (W1). A `report.json` rewritten after each group (case, op, action, Word version, counts, status, first difference; no paths) and every file handed to Word and saved, under `artifacts/word-judge/` (`SPEC_SURGEON_WORD_JUDGE_DIR` moves it) |
| W4 — the reader judged | The engine settles other people's redlines as Word does | A PowerShell recipe (`tests/word_redlines/make_word_redlines.ps1`) has Word make redlines on placeholder fixtures with Track Changes and Track Moves on: wording changed inside and across runs, a deletion inside someone's insertion, a paragraph deleted, paragraphs moved, a row inserted and deleted, a formatting change, a list paragraph inserted (C7's case). It sets a placeholder Office identity before Word opens anything and restores the owner's in a `finally`, checks what Word recorded before saving, scans every saved part for the local identity or a user path (any finding deletes the file), and saves Word's own Accept All and Reject All of each redline beside it. All of it is checked in with a manifest (Word version, recipe, SHA-256). Then, in CI without Word: `resolve_tracked_changes` (accept and reject, all authors and one) and `verify/textcheck.py`'s accept and reject views compared with Word's pinned results (W1). C6 and C7 test against these files once they exist |

Order: W1 (no Word needed), W2, W3, W4. C6 and C7 do not wait on this round.

## Acceptance

On fixtures: W1's planted differences caught and each tolerance exact; W2's action tested with a scripted session; W4's
manifest checks (checksums, the identity scan, every file a valid package) in CI. On the owner's machine (Windows,
Word): the W3 judge over every case, each failure either an engine fix or a Word behaviour recorded here with its
evidence; the W4 recipe run once, its files and Word's resolutions checked in; the engine's resolver matching Word on
every one of them in CI.

The judge cannot see a repair prompt (alerts are off in an owned Word, so a file Word refuses fails its job instead) or
how the Reviewing Pane draws the changes; those stay a manual look on the owner's machine.

## Do not, in this round

- Put a tolerance in the app or the verifier, or let the judge import the engine's readers.
- Add a fifth tolerance without a real run, a reason and a test.
- Check in a client file, a real name or a local path.
- Change an op, a prediction or a verifier rule to make a case pass without first naming the defect here.
- Make Word a runtime dependency of the app.

## Status — agents update this at the end of every session

| Item | Status | Owner / branch | Last note |
| --- | --- | --- | --- |
| W1 — the comparison | planned | — | — |
| W2 — Word resolves and saves | planned | — | — |
| W3 — the writer judged | planned | — | — |
| W4 — the reader judged | planned | — | — |
