# Spec Surgeon — backlog

The open work, in one place. Take an item from here. The pull request that finishes it removes it (or moves it to
"Waiting on the owner's machine" when only a real-file run is left): the pull request is the record of what changed
and how it was tested, so nothing here keeps a log. A new operation gets its design here, or in a design note this
file links, before it is built.

The dated plans these items came from (2026-10-18 to 2026-10-23) were removed on 2026-10-26 once everything else in
them was built; `git log --all -- 'docs/PLAN-*'` finds them, with their findings and session notes.

## Not built

### Batch consistency: what the owner's live runs of 2026-10-22/23 left open

| Item | What the run showed | What to build |
| --- | --- | --- |
| C7 — numbering evidence beside redlines | Numbering could not be checked in files with pending tracked changes: `structure.py`'s role reading marks a whole list unresolved when any of its paragraphs carries any revision, a wording redline inside a run included, which cannot change a number | A list is disturbed only by revisions that can change a number: an inserted, deleted or moved paragraph mark, a tracked paragraph-property change, a numbering change. A wording redline inside a run no longer makes the list unresolved, and a paragraph with one still takes part in roles when both views fit the same role. Roles are planning evidence only, never the authority of an edit, and the verifier is unchanged. Test against W4's Word-made redlines once they exist |
| S4 — what a run left open | Over test-set-2 (13 specifications, eight owners): the reference rules find no section number in seven of them (VA, Wisconsin, Port of Portland, Michigan layouts); a chain preview repeats the spec's summary and assumptions for each step; after an outcome-planner question the next reply still goes to that planner, not the assistant (seen again over test-set-3) | Reference rules for those layouts; a chain preview that says its summary once; the reply after an outcome-planner question goes back to the assistant |
| P2 — the outcome planner keeps the documents left out | Over test-set-3 the owner left out eight files; after a failed preview the outcome planner asked again which to do, then planned all 27 and guessed fills for the eight. `continuous_recovery.propose` builds its documents from `Conversation.files`, and `execute` runs every file | The documents the user left out stay out of `propose`'s documents, its contract and `execute`; the planner hears that the user left them out and why |
| P3 — list-label fonts in an outcome (**the owner's call**) | 13 copies that passed every edit check were refused by the outcome check ("unapproved package-member change: word/numbering.xml"): an outcome's whole-document `format` effect becomes `set_font` on every run, whose list-label fonts change `numbering.xml`, and `verify/effects.py` approves no `numbering.xml` change for a `format` effect. Every file with automatic numbering fails | Either the `format` effect's check approves the list-label font change (every used level's label font and size, symbol fonts kept, nothing else in `numbering.xml`), or the method leaves labels alone (`numbering_levels: false`, which also lifts the numbering-style refusals but leaves list numbers in their old font) |
| P5 — recovery's pace | Recovery of 27 files took 30 minutes: one trial per file, serially (about 25 s each, 177 s for the largest), three checkpoints per model call (5–18 s each), and Stop waited 37 s for two of them | A checkpoint per model call instead of three; an `all` target's bindings kept once, not per paragraph in every checkpoint and in `set_aside.json`; trials of files with the same method in one run |
| P7 — what a sample's PASS covers | Six files failed V7 in recovery (footer lines 11.6 pt above the template's); five of them had shown "verification PASS" as samples. A preview's sample verdict is V1–V10 only; V7 belongs to a run | The preview says its sample verdict is V1–V10 only, or runs V7 on the samples it renders. Whether the shift is real needs the documents (a footer line wrapping in a narrower text width is one candidate: the template's tabs were scaled 9360 → 8640) |

### Word as the judge of tracked changes (W1–W4)

Real Word checks the revisions the engine writes and the redlines it reads, as Build-a-Spec does (its own
`docs/DOCX_FIDELITY.md`, "Real Word as the judge"). Test evidence only: no op, prediction or verifier rule changes.
Today V8/V5b simulate Accept All and Reject All in the engine's own XML, `tests/test_batch6_round_trip.py` settles the
engine's output with the engine's own `resolve_tracked_changes`, and the "someone else's pending changes" the resolver
is tested on are built in Python. `render/word_com.py` has Word run Accept All / Reject All (`accept_reject_text`) but
returns the main story's plain text only, and nothing calls it.

Rules for this work:

1. **The judge is not the engine.** The comparison lives in `tests/word_judge.py` and imports nothing from
   `spec_surgeon`; copied ideas from Build-a-Spec are adapted, never imported.
2. **Symmetric.** Word re-saves the reference too (the untracked output, the source), so whatever a Word save rewrites
   on its own cancels without a tolerance.
3. **Four tolerances, in tests only:** rsid attributes, `w:proofErr`, `w:lastRenderedPageBreak`, the `_GoBack`
   bookmark. Never in the app or the verifier. A fifth needs a real run that shows it, a reason here and a test that
   it removes exactly that.
4. **Placeholder content only.** No client file, real name or local path is checked in; a Word-made file is scanned
   for the local identity before its checksum is pinned.
5. **Word stays optional.** The judge collects as skips unless `SPEC_SURGEON_WORD_JUDGE=1` on Windows with Word. The
   app never depends on it. Never change an op, a prediction or a verifier rule to make a case pass without first
   naming the defect.

| Item | What to build |
| --- | --- |
| W1 — the comparison (no Word needed; first) | `tests/word_judge.py`: for each text part (document, headers, footers, footnotes, endnotes, comments) its own canonical form — adjacent runs with identical properties merged, empty runs/text/property containers dropped, `w14:paraId`/`w14:textId` ignored, bookmarks compared by name, XML comments and processing instructions dropped — then the four tolerances on both sides; the first difference reported with both sides and its path. A relationship reference (`r:embed`, `r:link`, `r:id`) compares by what it reaches, never by its id or part name: an internal target by the SHA-256 of its bytes, an external one by its URL. `tests/test_word_judge.py` proves it without Word: each tolerance removes exactly what it names; a planted difference in text, formatting, a bookmark, a table row, a section break and a picture's media is reported; it imports nothing from `spec_surgeon` (pinned like `tests/test_independence.py`) |
| W2 — Word resolves and saves | A `resolve` action beside `render` and `accept_reject` in `render/word_worker.py` / `WordSession`: open a copy read-only, `accept`, `reject` or `resave` (optionally one author's revisions only), save as a new .docx (never overwriting), report Word's version and build, the revisions read and their authors, and how many were left. The same private-instance proof and per-file deadline as rendering; one Word per group of files. Nothing in the app calls it |
| W3 — the writer judged | `tests/test_word_judge_real.py` (opt-in): the tracked and untracked outputs on fixtures for every registered op whose `params_schema` accepts `tracked: true` (read from `ops.OPS`, never typed out), starting from `tests/test_batch6_round_trip.py`'s cases and `replace_text` inside someone's insertion; a coverage test (no Word needed) fails when such an op has no case. Word settles only the plan author's revisions (W2's author filter): every file opens and saves, every new revision reads as the plan's author, none of the plan author's is left after, other authors' survive both ways; Word's Accept All of the tracked output equals Word's re-save of the untracked output, and Word's Reject All equals Word's re-save of the source (W1). A `report.json` rewritten after each group (no paths) and every file handed to Word and saved, under `artifacts/word-judge/` (`SPEC_SURGEON_WORD_JUDGE_DIR` moves it) |
| W4 — the reader judged | A PowerShell recipe (`tests/word_redlines/make_word_redlines.ps1`) has Word make redlines on placeholder fixtures with Track Changes and Track Moves on: wording changed inside and across runs, a deletion inside someone's insertion, a paragraph deleted, paragraphs moved, a row inserted and deleted, a formatting change, a list paragraph inserted (C7's case). It sets a placeholder Office identity before Word opens anything and restores the owner's in a `finally`, scans every saved part for the local identity or a user path (any finding deletes the file), and saves Word's own Accept All and Reject All of each redline beside it, all checked in with a manifest (Word version, recipe, SHA-256). Then, in CI without Word: `resolve_tracked_changes` (accept and reject, all authors and one) and `verify/textcheck.py`'s accept and reject views compared with Word's pinned results (W1) |

On the owner's machine (Windows, Word): the W3 judge over every case, each failure either an engine fix or a Word
behaviour recorded here with its evidence; the W4 recipe run once, its files and Word's resolutions checked in. The
judge cannot see a repair prompt or how the Reviewing Pane draws the changes; those stay a manual look.

## Waiting on the owner's machine

Everything else is built and tested on fixtures; nothing is done on fixtures alone. These runs need real
specifications, Windows and Word, and only the owner can do them. Record what a run finds as a new item above.

- **Safe mode:** a real mixed batch (several projects, good and badly built files) through each button with Word
  rendering. Match body formatting to template over office masters, MasterSpec ("1.01", "PART 1 –") and
  Heading-styled files. Record the Left untouched reasons. The copies open in Word without a repair prompt.
- **Default mode:** a choice's drafted wording edited before it is picked, and the copy carrying the edit.
- **Obliterate:** a request Default refuses (a text box, Claude's own wording), with Word; a copy marked UNVERIFIED
  opened in Word. A request that needs a custom method using an attached file (a logo from an attached picture, a
  template's text box): its failed checks going back to the writer, any it keeps shown with its reason beside the
  copy, and the copy opened in Word.
- **The chat streaming its words:** a question's or a plan's message appearing in the window as Claude writes it.
- **The Division 21 batch again**, with its header/footer template: before anything runs the assistant names 21 05
  53's footer title and the stale Properties titles; a footer-only rename says the first page still reads the old
  title; "capitals like the others" is one plan with one sentence; "set every file's title to its own section" fixes
  21 12 00 and 21 31 13 in one request; a tracked deletion in a redlined file, then Accept All and Reject All in Word.
- **Redlined files:** a tracked request over real redlined specifications (bookmarks, comments, pending tracked
  changes) opens in Word without a repair prompt and accepts and rejects as listed; triage over the whole folder of
  specifications and templates refuses none for `xml-dtd`.
- **Full control and custom methods:** a live request per operation family (deletion, character formatting, pictures,
  table rows, table grid and cells, sections, fields and comments, moving) over real specifications, opened in Word;
  "shade every other row of the equipment schedules light grey" and "make the list numbers Arial 10 where the list
  text is Arial", offered as custom methods, pass and look as asked in Word.
- **The installed app:** a walkthrough in WebView2 (window states and errors, a frozen page's diagnostics, nothing
  outliving the window, fast responses, files side by side, the colour schemes), and the private real-file corpus
  (`tests/real`, `SPEC_SURGEON_REAL_CORPUS`, `SPEC_SURGEON_REAL_RENDERER=word`).
