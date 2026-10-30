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

### What the Specification Formatter's lessons found (from N1–N7's audit)

Spec Surgeon was audited against the Formatter's list of common mistakes and its hardening program. Its CT_Settings and
CT_PPr order tables match the Formatter's schema-checked ones element for element, pictures (an SVG's DOCTYPE) are kept
out of the revision-id scan, template parts with tracked changes are refused, and revision ids start above every
annotation id. The typed headings the reader misreads were fixed for the outline tool (N1). Left open:

| Item | What the audit found | What to build |
| --- | --- | --- |
| X1 — Word 2010 text effects on import | `style_import` and the header template copy a template style without its extension markup (`apply_header_footer_template.strip_foreign`, mirrored by `verify/template.strip_extensions`): a `w14:textFill`, `w14:glow`, `w14:shadow` or `w14:ligatures` in the template's style is dropped, so a coloured text fill or glow does not arrive and nothing says so. The Formatter carries them, keyed by expanded name, with their namespaces declared | Carry them (both sides, the prefixes declared on the target's root, a prefix bound elsewhere refused) — or at least flag each dropped property by name |
| X2 — the structure reader's typed headings | Safe mode's reading (`structure.analyze_structure`) still takes "1.5 times the pipe diameter shall be maintained." for an article and "PART 1 of the Contract Documents shall govern." for a PART; the outline tool holds them to a heading's shape (`outline.demote_unshaped`), Safe mode's body-formatting button does not | The same rule in the reader itself, which changes what Safe mode matches: the owner's decision |

### Safe mode, the second round of buttons (M1, M3)

The owner's go-ahead of 2026-10-28 built the Safe mode proposal's first round (twelve buttons, Reject beside Accept,
and the note above the buttons). These were proposed with it and need their forms designed before they are
built (M2, Remove specifier notes, is built: under "Waiting on the owner's machine"); each is an operation the engine already has and verifies, so each is a recipe in `recipes.py`, its form, its
sentence and its tests, never a new operation. Every rule of the first round holds: built in code from the form, the
one gate, no AI call, every placed word typed or read from an attached file, a capability and a tour step for any new
control, the trust surfaces in the same change.

| Item | Operation | What to design |
| --- | --- | --- |
| M1 — Fill in project information | `fill_variables` | The form lists the placeholders found across the attached documents (`{{NAME}}` markers and yellow highlights), a box for each, as the template button lists a prepared template's `typed_slots`; that needs a reading step over the documents (not the template) carried by the view, like `template_kind`. Or an attached table (CSV or Excel: the names column and this project's column, chosen in the form). A placeholder with no value refuses the document (`unknown: refuse`), never left half-filled. Typed values are the sources (`compiler.placed_words` already traces `fill_variables` values) |
| M3 — Make section numbers agree | `make_values_agree` | The SECTION line is the truth (`REFERENCE_FILLS["SECTION_NUMBER"]`); the places are the headers, the footers, the END OF SECTION line and the file name, each with a fixed pattern the app writes. Waits on S4: the reference rules miss the section number in some layouts, and a button that cannot read the truth sets those documents aside |

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

- **A removed document attached again is its original (built 2026-10-30):** in Default mode with Word, two real
  specifications: one request over both and **Create edited copies**; the ✕ on the second — its copy leaves **Your
  edited copies** and Export's count drops by one; attach it again — still no copy listed for it, and Export → Word
  (Tracked Changes ON) saves the first one's only. Then a request kept to the first ("only <its name>"), or the second
  set aside with the ✗ on its preview, and Create: What you've done so far gives the second neither run (attached
  later, then left untouched or set aside), Export still saves the first one's copy only, and a next request over both
  previews the second from its original (without the first change). In Obliterate, a copy delivered UNVERIFIED and
  removed takes its mark with it: attached again, it shows none, and its next copy carries none ("in an earlier copy").
- **Safe mode's kill switch (built 2026-10-29):** in the installed app with Word, over a batch of 20 or more real
  specifications: press **Match headers and footers to template** and, while the preview's pages render, **Stop now**
  in the bar above the buttons — the chat says the preview was withdrawn within a second or two, the window shows the
  copies it showed before, and Task Manager shows no `WINWORD.EXE` the app started and no worker process of the
  app's (a Word you had open yourself is still open, untouched). Preview again and **Create edited copies**,
  then the top bar's red **Stop** while the copies are checked: no copy is listed, the preview waits with its ✗ marks
  as they were, and Create then makes the copies. Export → PDF (Clean) and Stop: nothing lands in the folder. Then
  once on a machine with LibreOffice and no Word: no `soffice.bin` left running after the stop, and the next
  preview renders. Closing the window while a Safe-mode run is checking copies ends at once. A stop that does not land within 30 s is in the app log ("is still running … after Stop", with
  every thread's stack): record it here.
- **Remove specifier notes (M2, built 2026-10-29):** in Safe mode with Word rendering, the button over a real batch
  from each kind of master the owner starts from (MasterSpec sections and an owner's data-center masters): every note
  paragraph is gone (the CMT or note-style editor's notes, "NOTE TO SPECIFIER", "Retain or delete …", "Retain
  paragraph below …", "Select one of the following …") and the hidden text with it; no requirement, heading,
  placeholder, "to be determined", copyright or confidentiality line, red or blue requirement text, tracked change or
  comment went. Read the preview's list of removed notes against the documents for anything that is not a note, and
  the copies for a note the button missed (a note style by another name, an opening it does not know): either is a
  new item here, with the words or the style name. A document a note's field or section break set aside is under Left
  untouched with the reason, and **Try these in Default mode** removes that note when asked. The copies open in Word
  without a repair prompt. Then, in Default mode on those copies, one request cuts the placeholders and deletes the
  copyright line.
- **The Specification Formatter's modes in Default mode (N1–N7, built 2026-10-29;
  [docs/SPEC_FORMATTER_MODES_DESIGN.md](SPEC_FORMATTER_MODES_DESIGN.md)):** with Word, a consultant's CSI
  specification formatted to a real architect template: the outline from the reader and Claude's reading (its message
  names the parts that rest on Claude's reading; a misread one is seen in the preview), the template's headers with its
  even/odd switch ("with the template's odd and even page headers"). A CSI batch converted to CSC PageFormat with an
  architect's Canadian template, then with the built-in scheme picked in the chat, keeping each paragraph's look; a
  typed CSI set ("PART 1 - GENERAL", "1.01", "A.") converted the same way; a PageFormat document converted back to CSI.
  Every copy opens in Word without a repair prompt, its numbers read as the source's counters in the new scheme, and
  Word's indents look right where a paragraph kept its own style. Record a misread outline, a refusal that should not
  have happened or a spending surprise as a new item above.
- **Left untouched says what was looked for; Remove all documents on the title line (the owner's Safe-mode run of
  2026-10-29; built 2026-10-29):** the recipe "DIV 21 IFP" (Match headers and footers to template, then Find and
  replace) over the same 28 documents with the template attached, with Word: no line under Left untouched reads
  "This file does not contain the text or location needed for this change" or "More than one possible match was
  found"; each names its step ("Step 1 of 2 (Match headers and footers to template): …") and what it looked for and
  did not find (a SECTION line to read the section number or title from, with the document's first line quoted, if
  that is why), the Michigan document the different values it gives, and the ASHRAE, Reliable and AcousticalSolutions
  documents the Different Odd & Even Pages setting in plain words. A line that still reads vaguely is recorded here
  with its document's refusal record (`records\`, the diagnostic bundle). Create the copies: the same
  reasons under Left untouched by the last button, and on the to-do list after **Try these in Default mode**. With
  the preview open and the documents list folded, **Remove all documents** shows on its title line and, after the
  confirmation, takes all 28 out while the template stays attached.
- **Saved recipes (built 2026-10-29):** in Safe mode with Word rendering over a real Division 21 batch, Match headers
  and footers to template, Change the issue stamp and Accept all tracked changes, then **Save as a recipe…** with all
  three ticked; the recipe's tile under **My recipes** shows the three steps; on a second real batch from another
  project, with the office template attached, opening it shows each step's form, **Change** on the stamp step takes
  another wording for this run, **Preview 3 steps** shows one set of pages after all three, and Create makes copies
  that open in Word without a repair prompt and read as asked; What you've done so far lists the recipe as one entry
  with its steps, the three buttons carry their checkmark, and one Undo takes the whole recipe off; without the template
  attached the tile is greyed with the reason. In Default mode, two real requests (a code-cycle replacement and a
  formatting change) saved as a recipe and run on another project's specifications with the API key removed: the
  spending pill stays at $0.00, a document that does not fit waits on the to-do list with the reason, and the
  assistant plans it when asked there. A custom-method run is unticked in the save dialog with its reason. Two windows
  open at once each see the other's saved recipe after reopening its list; `%APPDATA%\Spec Surgeon\recipes.json` holds
  the steps and words and no document text beyond the user's own words. The tiles, the strip and the dialogs read well
  at 1280 px wide.
- **Editing saved recipes (built 2026-10-29):** in Manage, **Edit** on the three-step Safe recipe above: move Accept
  all tracked changes to the top, remove Remove all comments, change the stamp step's wording, **Save**; the tile shows
  the two steps in their new order, and running it on a real batch previews and creates copies after those two in that
  order, with the new wording. In Default mode, **Edit** on a recipe saved from two requests: a request step offers
  only moving and removing (never its words); removing it leaves the other running as approved. Run one more change,
  **Save as a recipe…** → **Add to a saved recipe** onto the Safe recipe: the chat says it has three steps now and, for
  a request added, that it is now a Default-mode recipe (its tile leaves My recipes in Safe mode). With two windows
  open, an Edit opened in one and saved after the other renamed the recipe is refused with the reason and nothing is
  written over; `recipes.json` shows the edited steps. The editor reads well at 1280 px wide.
- **A run recovery finishes, undone and saved whole (built 2026-10-29):** in Default mode with Word rendering over a
  real batch, a request whose run sends a document to recovery (a check fails and the chat says it is checking the
  files that still need work), then Create on recovery's preview: What you've done so far lists the request once, and
  **Save as a recipe…** keeps it (no "What it ran was not kept"), the step's plan the one approved at the first
  Create. **Undo the last change** then shows the copies from before the request, not its first copies, and the next
  request builds on those. Again with the window closed from Task Manager while recovery's preview waits: after the
  restart and Continue, the same holds, and the earlier changes in the list can still be undone one by one.
- **A run's saved copies wait on its recovery (built 2026-10-29):** in Default mode with Word rendering over a real
  batch, a request whose run saves some copies and sends one document to recovery. While recovery's preview or its
  question waits, the document tray's buttons and ✕, **Remove all documents**, Safe and Obliterate in the mode switch
  and **Put back** are greyed, each hover text saying that copies from this request are saved while the rest are
  checked again and how to finish (create the copies it fixed, or answer its question, save the ready copies or set
  the rest aside); a file dropped on the window is refused with the same words and nothing is added; a typed
  "make it italic instead" at recovery's preview shows in the chat with that reason and no AI step runs. Create on the
  preview (or Set the rest aside for later): What you've done so far lists the request once with every saved copy,
  the next request builds on all of them (open one in Word: it carries the first change), Undo the last change takes
  the whole run off, and the greyed controls work again. Again with the window closed from Task Manager while the
  preview waits: after the restart the same controls and **Drop this request** wait until Continue brings the
  preview back. With a request whose only document goes to recovery, attaching a second document still works as
  before (the request is checked again over both).
- **What you've done so far (built 2026-10-29):** over a real batch in Safe mode, Remove all comments, Accept all
  tracked changes and Match headers and footers to the template, each created in turn; the list under the copies
  names the three in order, the newest marked LATEST, with "All N documents" or which documents did not get one and
  why (a document the template button left untouched; one set aside with the ✗; one attached after the first button);
  the three buttons carry their checkmark and the hover text says how many times; Undo the last button takes the
  header/footer line and its checkmark off; a window ended without closing (Task Manager) shows the list again when
  the next start restores it; Start over clears it. In Default mode, two requests in a row list the words typed, and Undo the last change
  takes the second off. The list and the checkmarks read well at 1280 px wide.
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
- **One failed preview no longer holds back the batch (the owner's Safe-mode run of 2026-10-28; built
  2026-10-28):** the owner's Division 21 batch (twelve documents) with `00_00_00_SPECIFICATION HEADER AND FOOTER
  TEMPLATE.docx` through **Match headers and footers to template** with Word rendering, where *21 05 17 Sleeves and
  Sleeve Seals for Fire-Suppression Piping* failed its page-layout check (V7) and Create was unavailable for all
  twelve: Create is available; 21 05 17's preview keeps its pages and its V7 line, carries no checkmark or X, and is
  listed under Left untouched with the reason; the chat and the sentence beside the button say it gets no copy; the
  other eleven copies are created and open in Word, 21 05 17's file is unchanged and is under **Left untouched by the
  last button**, and **Try these in Default mode** puts it on the to-do list with the same reason. The same batch in
  Default mode: no outcome planning starts at the preview, Create makes the eleven, and 21 05 17 waits on the to-do
  list (why: failed); **Work on the documents I set aside** plans it alone. A batch whose every preview fails still
  says Create is unavailable.
- **Safe mode's third set and the note (built 2026-10-28):** a real specification folder with Word rendering through
  each new button: *Change font* (Arial 10 pt, then Keep each document's sizes: headings and notes keep theirs); *Page
  size and margins* over a folder with a landscape schedule (that document waits under Left untouched with the
  reason, the others take Letter and 1 in margins; Keep each document's own paper takes the margins on every one);
  *Change the issue stamp* (ISSUED FOR BID and ISSUED FOR PERMIT to ISSUED FOR CONSTRUCTION with a date, on covers,
  headers and footers); *Add END OF SECTION where missing* over MasterSpec and office masters (a missing line added
  like the SECTION line, an "END OF SECTION" without its number corrected, a document whose SECTION line is not
  found waiting with the reason); *Keep numbers with their units*, *Clean up spaces*, *Standardize quotes and dashes*,
  *Remove hyperlinks*, *Freeze fields as plain text* (a table of contents sets its document aside), *Remove unused
  styles*, *Accept or reject all tracked changes* with Reject, *Set author and company* (and Clear the author names),
  *Set proofing language*. The copies open in Word without a repair prompt and read as asked. The note: it reads
  well at 1280 px wide, **Hide** folds it and it stays folded after a restart, **What is Safe mode?** opens it, **Add
  a Claude API key** opens Settings, **Switch to Default mode** switches.
- **Safe mode:** a real mixed batch (several projects, good and badly built files) through each button with Word
  rendering. Match body formatting to template over office masters, MasterSpec ("1.01", "PART 1 –") and
  Heading-styled files. Record the Left untouched reasons. The copies open in Word without a repair prompt. Then
  change your mind on that batch in the installed app: Discard a preview, go back to a button and find its form as
  you left it, run two buttons and Undo twice (the next button builds on the right copies each time, and every copy
  is still in its session folder under the scratch folder), and Undo after a crash's restore.
- **A prepared template's "00 00 00" and "Specification Name" (the owner's Safe-mode run of 2026-10-28; built
  2026-10-28):** the owner's `00_00_00_SPECIFICATION HEADER AND FOOTER TEMPLATE.docx` over the same Division 21
  batch in Safe mode, with Word: **Match headers and footers to template** asks for nothing (the form says each
  document fills in its own section number at "00 00 00" and its own title at "Specification Name"), and every
  copy's footer shows the document's own title and "21 05 17 - 1" where the placeholders were, typed as the
  document types them (capitals stay capitals). A template naming its placeholders otherwise ("SECTION NAME",
  "[SECTION NUMBER]", "000000") the same; one with a project stamp asks for the stamp alone.
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
- **A prepared template whose header wraps (live QA finding F08, built 2026-10-28):** the finding's wrapped
  prepared template over its three-page targets, with Word: the headers/footers request previews and runs without a
  copy held on V7 because its own section number or title rewraps the header (the record's V7 line reads "as this
  document's plan fills them, at its own text widths"); then specifications with other side margins than the
  template's, and one with a landscape section, the same; then one with its header distance not aligned
  (`align_header_footer_distance` off, a header 1" from the edge): the copy keeps its header 1" from the edge and
  passes V7, its record's V7 line reading "and its own header/footer distances" (the deep-dive stress test's D4: the
  report's Houston and Michigan copies, kept at their own distances as asked, were held on V7 and recovery spent 35–40
  calls on them) — run the report's three-file template request again (VA, Houston, Michigan, distances kept) and
  see all three copies created without recovery.
- **Claude's visual check, read whole (the deep-dive stress test's D1, built 2026-10-28):** the report's
  Obliterate footer request (widen both footer title boxes to 4" and add a sentence) five times over its real
  specification with Word rendering and the visual check on: no preview shows a clean visual check while its saved
  `visual_review.json` transcript holds findings; a check whose answer could not be read says "skipped: Claude's
  answer could not be read (…)" beside the preview, and one nested under a key reads its findings (the transcript
  line `"unwrapped"`).
- **A custom method's pages checked by its own plan (the deep-dive stress test's D3, built 2026-10-28):** a Default
  custom method over a real specification folder with Word rendering: each sample card gives the page-layout verdict
  (V7) by the method's plan, not "checked when the copies are created"; the copies' records carry V6 and V7 from
  Word, never "no renderer run yet"; a method whose words move where its plan says none do shows "Did not pass" on
  the sample, the chat says the page-layout check failed before the button, and Create is unavailable. The same in
  Obliterate: the copy is delivered marked UNVERIFIED with V7 beside it.
- **Parts no check reads, page counts, refused calls (the deep-dive stress test's follow-ups, built 2026-10-28):** in
  Obliterate, a custom method over a real specification that changes the theme's accent colours (the report's
  four-file theme batch): every copy reads UNVERIFIED with UNREAD naming the theme part, or with Claude's reason when
  it kept the check, and opens in Word with the colours asked for; a Default request for the same is refused with
  the reason. A preview of a long specification (17 pages or more) with the visual check on: its findings never say a
  later page was not rendered. A request sent with a revoked or mistyped key: the chat says the request failed
  (HTTP 401), and the pill and the request's spending stay where they were.
- **Default mode:** a choice's drafted wording edited before it is picked, and the copy carrying the edit.
- **Changing your mind in Default and Obliterate** (built 2026-10-28): over a real batch in the installed app, a
  preview **Discard preview**d (nothing created, the copies list as it was, the next message planned as a new
  request that "the same, but italic" still reads); a question the assistant asks answered with **Drop this
  request**; a request paused at a low spending check dropped from beside the pause card; two runs undone with
  **Undo the last change**, the next request building on the right copies each time and every copy still in the
  scratch folder; a pass over the to-do list discarded and its documents back on the list; the same in Obliterate
  with a copy marked UNVERIFIED undone and its mark gone with it.
- **Spending with no connection** (built 2026-10-28): the network unplugged (or a proxy that refuses the API) and a
  request sent: the pill and the request's spending stay at what they were, the request does not pause at the
  spending check, and the chat says the connection failed.
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
- **Export's three kinds (built 2026-10-29; Safe mode's Save clean PDFs retired for it):** over the owner's
  Division 21 batch in Default mode (headers and footers to the template, Arial, red), with Word: Export opens while
  recovery still checks some documents and the copies it already delivered are listed; asking the assistant for clean
  PDFs gets an answer pointing to Export, never "I can't save PDFs". **PDF (Clean)**: each PDF shows no comment balloon
  and no redline and matches Word's own *No Markup* print of the accepted document, opens in Acrobat, and a file with
  an inserted table cell is named in the chat without a PDF. **Word (Tracked Changes OFF)**: the files open in Word
  with no tracked change left and their comments still there; a comment inside a tracked deletion names its document
  without a file. **Word (Tracked Changes ON)**: the copies as the window lists them. Afterwards the edited copies in
  the window still carry their comments and tracked changes, Undo goes back before the last run, a preview that was
  waiting is still waiting, and the close asks about the copies until they are saved as Word (Tracked Changes ON).
  Then PDF (Clean) once with LibreOffice only, and the menu read at 1280 px wide.
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
- **The guided tour in the installed app, with Word** (built 2026-10-27, brought up to date 2026-10-29): take it end
  to end in WebView2 with a Safe-mode form open and a document attached beforehand — the tour opens on the buttons,
  the Safe-mode chapters' preview and copies render through Word, the find-and-replace chapter shows the Find and
  replace form with "FM Global" → "FM Approvals", the practice folder appears as `tutorial\` inside the window's
  scratch folder (`%LOCALAPPDATA%\Spec Surgeon\scratch\<start>-<pid>\`) and is gone at the end, the conversation that was
  open comes back exactly as it was (its journal untouched, its mode and folders unchanged, the same Safe-mode form
  open with what was typed in it), a reload mid-tour resumes at the same step, New conversation during a tour ends it
  first, and after Finish the chip reads "Take the guided tour again" across a restart (someone who finished the
  earlier tour is invited again once). Two windows each running a tour keep their own practice copies. Record a
  chapter whose card sits badly at 1280 px wide as a new item above.
- **The top bar at 1280 px wide (built 2026-10-29):** in the installed app (WebView2, Segoe UI) at 1280 px, the title,
  the help topics, the mode switch and the right-hand buttons never overlap and the gear stays inside the window: with
  documents attached, after a run with copies unsaved, in Safe mode before and after a button, while a Default request
  runs (Stop and "this request ≈ … of $20.00" in the pill), with Obliterate shown, and with an update set aside with
  **Not now**; a wider window brings the context line and the full words back, and hovering a one-word help topic
  names it in full.
- **The installed app:** a walkthrough in WebView2 (window states and errors, a frozen page's diagnostics, nothing
  outliving the window, fast responses, files side by side, the colour schemes), and the private real-file corpus
  (`tests/real`, `SPEC_SURGEON_REAL_CORPUS`, `SPEC_SURGEON_REAL_RENDERER=word`).
