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
| S4 — what a run left open | Over test-set-2 (13 specifications, eight owners): the reference rules find no section number in seven of them (VA, Wisconsin, Port of Portland, Michigan layouts); a chain preview repeats the spec's summary and assumptions for each step; after an outcome-planner question the next reply still goes to that planner, not the assistant (seen again over test-set-3) | Reference rules for those layouts; a chain preview that says its summary once; the reply after an outcome-planner question goes back to the assistant |
| Font button: documents built alike at two body sizes | **Match font to template** plans per body size (one layout per size, `recipes._font_layouts`), and a layout routes by structure pattern (`layouts.route`): two documents of one pattern whose body sizes differ cannot take two sizes in one press, so the size most of them have wins and the rest wait under Left untouched with the reason, to be pressed alone | A layout that names its documents beside its patterns (`validate_layout_plan`, `route`, the `applies_to` pin and the approval token), so one press covers them — only if the owner's runs meet the case |

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

### Hold the work in memory; write only on export (H2–H4)

Owner question (2026-10-27): the window leaves many folders and files behind, several in one session — why, and can
it work as Build-a-Spec does, which holds the document in memory while the user works and writes a file only when
they save or export (several export choices)? Yes. The engine's own pipeline is already in memory: a file is read
once, planned, applied and verified as bytes (`DocxPackage(bytes)` → `apply_spec` → `work.output_bytes()` →
`run_suite(source bytes, output bytes, plan)`), and `batch._transform_one` writes the copy only after the suite
passed. Every folder comes from the layers around that pipeline, each written for a reason that held when the engine
was a batch tool with a results folder and the window was added over it:

| What is written today | Where | Why it was written |
| --- | --- | --- |
| A session folder per conversation and per preview after a reply (`Session._next_session_dir`: never one that exists), holding `conversation.json`, `compile.json`, `chat\` (the provider history, its sealed epochs in `chat\context*`), `preview\` (samples, their PDFs, page images, `preview.json`, `spec.json`), `codegen\` (every custom-method attempt), `run\` (`outputs\`, `failed\`, `records\`, `plans\`, `exceptions\`, `render\`, `logs\`, `BATCH_REPORT.md`, `DIAGNOSTICS.md`, `batch.json`), `recovery\` (checkpoints, trials, exports) | `data_dir()\runs\` or Settings' Output folder | the record beside its preview and run is the audit trail, frozen once used; `settings.default_run_root` says "run results must never use a disposable cache" |
| Full copies of every file dropped on the window | `config_dir()\attachments\<time>\` | a drop hands the page bytes, not a path; the server needs a file to triage and to guard (`/api/upload`) |
| The journal after every step (`DocumentTask.save`), archived at each start under `conversations\` | `config_dir()` | crash resume: the conversation, the working copies (paths into run folders), the to-do list, undo points, spending |
| PDFs and page PNGs of every source and copy | `render\` folders, `runs\.render-cache\` | Word and LibreOffice render files, not bytes; the page shows PNGs by path (`/api/image`, `image_allowed`) |
| The custom-method sandbox's copies, requests and results | `codegen\` | the sandbox runs in another process and may write only in its own folder |
| The app log, event journals, crash logs, session markers | `data_dir()\logs\` | the window's trail; Build-a-Spec writes the same kind of thing to its state dir |

What never got deleted was the owner's own earlier decision (T31, F5): run folders, conversations, attachments copies
and bundles — reversed by H1, built 2026-10-27 (below). Build-a-Spec's shape, for reference only: the document and its versions live in `SessionState`
(undo and redo in memory), one explicit **Save** writes the `.baspec`, **Export** offers the Word exports and the
review report, and nothing of the document is on disk otherwise; its logs and traces still go to disk.

Rules for this work:

1. **The engine, the verifier and their invariants do not change.** `batch._transform_one` keeps its order (plan,
   apply, verify in memory, then the copy); the sandbox keeps its own folder and its limits; the guard, the approval
   token and the source pins stay as they are, over bytes where they were over files.
2. **One scratch folder per window, gone when the window closes.** What must be on disk (renders and page images,
   the sandbox, uploads, worker spill, recovery's checkpoints with their lock) lives under
   `data_dir()\scratch\<start stamp>\`, deleted at a clean exit and swept at the next start. The app deletes its own
   scratch only: never a file the user attached by path, exported or chose a folder for. The render cache stays
   (a cache with its own limit, keyed by content).
3. **A file is written where the user chose, on an export they clicked, through the guard.** Never over an input,
   never over an original (T1 holds; write-back stays unoffered unless the owner decides otherwise, below).
4. **The trust surfaces change in the same pull request:** T1, T31, F5, A12, the data-at-rest table (§1e), the
   dossier cards, Help's copy ("each run's copies go into their own folder"), README, this file's owner's-machine
   list, `docs/CONVERSATIONAL_DESKTOP.md`, `docs/DEVELOPER_DIAGNOSTICS.md` (the bundle reads the scratch folder's
   logs and records, not "the five newest run folders") and `docs/WEB_API.md`.

H1 — the scratch folder and Export — is built (2026-10-27): `scratch.py`, `DocumentTask.export`, `POST /api/export`,
the close question, the restore of a window that never closed; its real-file run waits under "Waiting on the owner's
machine". What is left:

| Item | What to build |
| --- | --- |
| H2 — the working set as bytes | `_transform_one` returns the copy's bytes with its record and plan (workers send them back over the pool; `FileResult.output` becomes a name plus bytes); the delivered set, `results`, `working` and `undo_points` are versions in memory (bytes, name, record, plan, the `applied` sentences), the approval token hashes them, a chain hands bytes between steps (`chain._deliver` already holds them) and a preview writes a sample to scratch for the renderer only. `/api/open` takes a result id and writes the copy to scratch on demand; `results[].output` becomes an id in `docs/WEB_API.md`. Recovery's checkpoints stay files in scratch (their lock and resume design holds). Records are written only by an export |
| H3 — a session file | **Save this session…** / **Open a session…**: one file (a zip: the conversation, decisions and approved wording, the working set's versions with their records, the to-do list, undo points, spending; never the API key) in place of `config_dir()\conversation.json` and the `conversations\` archive. The crash journal of H1 is this file, autosaved under scratch after every step, removed at a clean exit |
| H4 — more export choices | **Save the copies with their records…** (`records\`, `BATCH_REPORT.md`, `DIAGNOSTICS.md` beside the copies: the audit trail that today sits in the run folder, the find-and-replace occurrence log of F2 among it); **Save beside the originals** (each copy in its original's folder under its planned name, refused where that name is an input's); the diagnostic bundle built from the scratch folder and memory. Write-back over the originals (`guard.write_back`, invariant 4, never bypassed) is possible as a further choice and is the owner's call: it reverses T1 |

Decisions taken (the owner, 2026-10-27): crash resume is kept through the scratch journal — the next start restores
a window that never closed, once (H1); on export the copies alone, the records as a second choice (H4); no
write-back over the originals unless asked for (T1 is the claim most of the trust copy rests on).

## Waiting on the owner's machine

Everything else is built and tested on fixtures; nothing is done on fixtures alone. These runs need real
specifications, Windows and Word, and only the owner can do them. Record what a run finds as a new item above.

- **Find and replace, occurrence by occurrence (F1–F3, built 2026-10-27):** a real code-cycle rule set (the small
  tool's `find,replace` CSV attached as the table, or typed pairs) over a real specification folder attached with
  **Add a folder of documents**; the occurrence list under the previews reads right (the article labels, the words
  around each occurrence, a header's occurrence named as a header's); untick a historical reference and a line in a
  table, **Update the preview**, the previews return with those left as they are and Create waits until they do; the
  copies in Word carry every other replacement and not those two; each copy's record (`records\*.record.md`, the
  diagnostic bundle until H4) lists every occurrence with its status and `BATCH_REPORT.md` counts them; a tracked run
  whose document the plan sets aside for a hyperlink's occurrence lists it, and unticking that one lets the document
  through; the folder button over a folder with subfolders says how many documents they hold and attaches none of
  them; the guided tour's new chapter reads well at 1280 px wide.
- **The scratch folder and Export (H1, built 2026-10-27):** a real batch attached, previewed, run, undone and
  saved with Export from the installed app (the dialog opening where the last export went; a second export of the
  same copies writing nothing; a different file under a copy's name left alone and the copy numbered; the originals'
  folder refused); the saved copies open in Word (a copy's link opens the saved file; after a change to the working
  copy in Word, through the folder button, the list says CHANGED beside it, the link opens the working copy, the
  close asks again, and the next Export writes it beside the file saved before); closing with an unsaved copy asks
  once in a native dialog, Cancel keeps the window open and OK closes; after the window closes nothing of the session is under
  `%LOCALAPPDATA%\Spec Surgeon\scratch` but `.render-cache`, and `%APPDATA%\Spec Surgeon` holds only
  `settings.json`; the process killed mid-session and the next start restoring the conversation and its copies from
  the scratch folder, then sweeping it at that window's clean close; a copy left open in Word at the close and the
  remainder swept at the next start.
- **Safe mode:** a real mixed batch (several projects, good and badly built files) through each button with Word
  rendering. Match body formatting to template over office masters, MasterSpec ("1.01", "PART 1 –") and
  Heading-styled files. Record the Left untouched reasons. The copies open in Word without a repair prompt. Then
  change your mind on that batch in the installed app: Discard a preview, go back to a button and find its form as
  you left it, run two buttons and Undo twice (the next button builds on the right copies each time, and every copy
  is still in its session folder under the scratch folder), and Undo after a crash's restore.
- **Match font to template, per body size (the stress-test finding; built 2026-10-28):** the finding's two documents
  attached together with the 11 pt template: the preview shows two groups, one per body size, each sentence naming
  its size; in the copies the 11 pt-body document's 9 pt table text is still 9 pt (82 runs went to 11 pt before)
  and the 9 pt-body document's body is 11 pt with its headings and notes as they were; the same document attached
  alone gives the same copy. Then two specifications built alike (one structure pattern) with different body sizes:
  the odd one waits under Left untouched with the reason, and pressed alone it is matched at its own size.
- **Words hidden on the rendered pages (the stress-test finding F4, second half; built 2026-10-28):** the finding's
  copies again, with Word: a copy whose pages came out blank, or without the inserted text, while its XML held it —
  the preview's sample says "N word(s) shown in the original's pages are not shown in the copy's: …" beside it, the
  chat says it before Create, Claude's visual check names the pages, and `BATCH_REPORT.md` and the copy's record carry
  the line; then a font change and a margin change over a real specification folder (the pages reflow, the page count
  changes) with no such warning, the header and footer words recorded under the record's
  `header_footer_words_not_compared`; a header/footer template over the same folder with none either.
- **Default mode:** a choice's drafted wording edited before it is picked, and the copy carrying the edit.
- **A question answered without a plan (the stress test's F7, built 2026-10-28):** in Default mode over a real
  batch, park one document on the to-do list, edit the other two, ask a status question ending "Do not prepare or
  make an edit" (the assistant answers in words), put the parked document back, then ask for a Subject property
  change in all three. No Continue follows the answer; the edit is previewed for all three without the question being
  answered again; that request's `compile.json` (the session folder, the diagnostic bundle) shows the edit as the
  REQUEST and the question with the answer's first sentence under EARLIER IN THIS CONVERSATION. Then a follow-up
  typed straight after an answer ("and the headers?") planned as a new request that reads the exchange.
- **A request kept to one document** (built 2026-10-27): over a real batch, "only 23" (and "just the Michigan one",
  "in 21 and 22, …") plans, previews and runs those documents alone, every other one under Left untouched and
  unchanged; a follow-up "now make its title bold too" keeps to them; a later request about the whole set covers
  every document, and the assistant says which documents already carry the earlier change instead of calling the
  others newly attached.
- **The outcome planner with documents left out** (P2, built 2026-10-27): over test-set-3 again, leave out several
  documents and let a failed preview send the request to the outcome planner. The left-out documents stay under
  Left untouched, the plan is offered as **Use these outcomes and continue** with its words beneath it and runs
  only once picked, its trials cover the others alone, and the planner's message names the left-out documents as out
  of scope instead of asking which to do. An outcome that deletes several long paragraphs of a real specification
  (a whole article's submittal items) runs as the built-in deletion in one step, not a custom method.
- **Obliterate:** a request Default refuses (a text box, Claude's own wording), with Word; a copy marked UNVERIFIED
  opened in Word. The stress-test finding's request again (widen the footer text boxes and add a sentence, with the
  wording to be shown before approval; built 2026-10-28): before the method is written the chat says Claude's reason
  and what the method is to do in Claude's words; before Create the chat lists the exact sentence as words
  added to the footer and each preview card lists the same, with rendering on and off; a method kept with failed
  checks has them said in the chat before Create, ahead of the words; a request that says to keep the
  words as they are, whose method changes a title anyway, lists the words removed and added before Create; a
  formatting-only method reads "No words change". A request that needs a custom method using an attached file (a logo from an attached picture, a
  template's text box): its failed checks going back to the writer, any it keeps shown with its reason beside the
  copy, and the copy opened in Word.
- **Save clean PDFs** (built 2026-10-27): over a real redlined and commented set, with Word. Each PDF shows no
  comment balloon and no redline and matches Word's own *No Markup* print of the accepted document. The PDFs open in
  Acrobat. A file with an inserted table cell is listed without a PDF. The Word documents still carry their comments
  and tracked changes afterwards, and Undo goes back before the last button that changed them. Then once with
  LibreOffice only.
- **The chat streaming its words:** a question's or a plan's message appearing in the window as Claude writes it.
- **The spending check and the kill switch:** a recovery or custom method that reaches a low check (say $5) pauses
  with the card's amounts, Continue goes on, and the request's spending in Anthropic's console agrees with the
  estimate; Stop in the top bar cuts off a running step at once. A custom method paused while it investigates
  (live QA finding F09) continues in its one `codegen` folder after Continue, its first call after the pause carrying
  the inspections it had made, none repeated, and the method submits and passes.
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
- **The guided tour in the installed app, with Word** (built 2026-10-27): take it end to end in WebView2 — the
  Safe-mode chapters' preview and copies render through Word, the practice folder under `%LOCALAPPDATA%\Spec
  Surgeon\tutorial\` appears at the start and is gone at the end, the conversation that was open comes back exactly
  as it was (its journal untouched, its mode and folders unchanged), a reload mid-tour resumes at the same step, New
  conversation during a tour ends it first, and after Finish the chip reads "Take the guided tour again" across a
  restart. Record a chapter whose card sits badly at 1280 px wide as a new item above.
- **The installed app:** a walkthrough in WebView2 (window states and errors, a frozen page's diagnostics, nothing
  outliving the window, fast responses, files side by side, the colour schemes), and the private real-file corpus
  (`tests/real`, `SPEC_SURGEON_REAL_CORPUS`, `SPEC_SURGEON_REAL_RENDERER=word`).
