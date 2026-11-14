# CLAUDE.md — Spec Surgeon

## What this is

Spec Surgeon edits Word (.docx) specification files in batches, from one word or one property up to whole tables,
sections, pictures and the page structure, with an independent verification record for every file. A deterministic
engine plans each change from the source before touching it, applies it, and checks the result with a verifier that
shares no code with the transform. An optional Claude layer turns plain-English requests into EditSpecs and never
edits a file itself. The user is in control: whatever they ask for, preview and approve, the engine does exactly. The
files go to clients, so the one failure that matters is content lost or invented without the user asking for it.

## Invariants

1. **Predict first.** `Op.plan()` computes every prediction (diffs, parts, members, revision deltas, assertions and
   their target counts) from the SOURCE, analytically; never by running the transform on a copy and reading the result.
   Two exceptions, the owner's (2026-11-10), both Obliterate's alone (Modes, below): a custom method written there
   predicts nothing and is its `apply()` alone; and a named Word action (`word_action`) predicts nothing, because Word
   saves the whole file again, and is never planned or applied by the engine. Every other built-in op predicts first
   in every mode.
2. **The verifier shares no code with the transforms.** `src/spec_surgeon/verify/` imports nothing from `ops/`,
   `editspec.py`, `ooxml.py`, `package.py`, `goals.py`, `goal_contracts.py` or the Claude layer
   (`tests/test_independence.py`). It has its own readers, text extraction, selectors, style chains and definitions.
   Do not deduplicate the twin implementations: the duplication is the point.
3. **Repack by member substitution.** `DocxPackage.repack` keeps every untouched member byte-identical, in source
   order. Never pretty-print, regex-edit XML or `zip -r`; serialize with `serialize_xml()` only. Read parts with
   `DocxPackage.xml()` (`parse_xml` refuses a `<!DOCTYPE`, `xml-dtd`) and find them through relationships
   (`main_part`, `text_parts`, `settings_part`), never by their usual names; new revision ids come from
   `assign_revision_ids`, above every annotation id in every document part (`ooxml.max_revision_id`).
4. **Never write back blind.** In-place delivery goes through `guard.write_back` and nothing else. It refuses a source
   whose size, mtime or md5 moved, a lock file, or a read-only file. No `force` flag exists and none may be added.
5. **Refuse what cannot be predicted, not what is large.** A case an op cannot predict exactly raises `JobException`
   with a code and sets the file aside; the engine never guesses. Size and destructiveness are not reasons to refuse:
   deleting a table, a section or the whole body is a change like any other once it is planned, previewed and approved.
   Overlapping ops are composed into ordered steps in the user's own order (`editspec.stage_cuts`), never reordered:
   a source-bound template op not listed first is refused, not moved.
6. **No generated characters.** Formatting and structural ops add zero text. Content ops place the user's approved
   wording verbatim, or the words of a file the user attached (a table, a template), and `compiler.placed_words` must
   trace every typed word to the user's own sources. Claude's words are never the user's: a tapped suggested reply is a
   decision, never approved wording. A choice's drafted wording becomes the user's only by the pick — as offered, or as
   the user edited it first (`conversation.edited_wording`: the same `clean_wording` rule); a draft the user replaced
   is never approved.
7. **One specification per edit.** The approved EditSpec (or chain of them) is the only description of a change the
   engine executes; nothing runs that was not planned, previewed and approved as that spec. One exception, the
   owner's (2026-10-29): Export's clean choices (`exports.clean_spec`: every tracked change accepted; or every comment
   removed, then every tracked change accepted) are fixed specs built in code that the menu choice names and approves,
   without a preview. They run over the edited copies through the gate and the checked run, into the scratch folder,
   and change no copy, history line or undo point; never widen them, and never let a model choose one. Amended for
   Obliterate alone (the owner's, 2026-11-10; Modes, "Export anyway"): where a clean choice left a marked copy
   without a file, the menu offers that kind made by Word — commands fixed in code (`exports.ANYWAY_ACTIONS`), no
   spec, no gate, no checked run, the file named UNCHECKED. Still the menu's choice and never a model's.

Out of reach by decision (refused with a code, "change this one in Word"): shapes, SmartArt, charts, equations,
embedded objects, data-bound content controls and the theme, and text boxes as objects (adding, moving, sizing or
formatting one). The words inside a text box are `replace_text`'s to change (a nested paragraph is a paragraph of its
own) and set a file aside under `replace_table` (Word keeps two copies of a text box; the code `textbox`).

## Modes

Every invariant binds **Safe** and **Default**. **Obliterate** (`Session.unguarded`)
is the owner's decision to lift, for that mode only, the guardrails that protect a document's content: the placed-words
trace (invariant 6: `compose_submission(unguarded=, shown=)` traces placed words to the user's words or to the reply
the user reads, `Conversation.shown_text`, never to nothing; Claude may write wording once its reply shows it;
`compiler.UNGUARDED_RULES` rewrites the prompt's rules it lifts), a custom method's formatting-only limits
and its prediction (the method just does the work: one class with `apply(self, pkg)`, no `plan()`, selector or
parameters, written under `codegen.FREE_SYSTEM`; the writer gets the `GOAL_FROM` helpers, `pkg.set_bytes` and the
user's attachments as `pkg.inputs`, pinned in `GeneratedOp.attachments`. Nothing is predicted, so the suite cannot hold
the copy to a plan: the verifier's own report reads it instead — `verify.run_difference`, the Obliterate record: the
words added and removed in V2's shape, D1 "What changed" (counts, formatting, tables, page setup, styles, numbering,
settings, relationships, parts, and every changed part it compared by bytes only), and the checks that need no
prediction (V1 package integrity, V4 revision ids, V5a schema, V5c revision grammar; V6 when the pages are rendered).
The writer reads that report after a submission runs and takes the method with `confirm_op`, at most `SHOWN_ROUNDS`
reports; a copy whose file fails a check goes back `UNGUARDED_CHECK_ROUNDS` times, after which the latest method is
used. The method's copy of every document is made before approval (`Session._every_copy`), the approval names each
copy's bytes (`preview.generated_key`'s `results`), and Create delivers those copies without running the method again
(`codegen._kept_trial`; a shown copy that changed or is gone refuses Create and has Preview make every copy again,
`codegen.kept_copies_changed`). No such copy is called verified or passed: each is marked UNPROVEN
(`ui_feedback.UNPROVEN_MARK`), and UNVERIFIED when a check of its file failed — `codegen.samples_verdict` words the
note, the message and the plan's details. In every mode the words a method's copies add and remove are listed before
approval from the verifier's V2 record, never from the method or the model — `preview.text_changes_of` →
`SampleView.text_changes`, said by `DocumentTask._say_text_changes` and shown under each sample; an Obliterate method's
other changes follow them, `SampleView.changes`, `preview.shown_changes_lines`), and
the holding back of a copy that fails a check (`deliver_unverified`: delivered to `outputs/` marked UNVERIFIED, its
failed checks in `FileResult.unverified`/`ChainFile.unverified`, no recovery; the mark stays on every later copy built
on it, `DocumentTask._mark_unverified`, in every mode). The verifier still runs on every copy
and reports; invariant 1 binds every built-in op but `word_action` (below), and invariants 2, 3, 4, 5 (built-in ops
still refuse what they cannot predict; Obliterate's way past a refusal is a raw XML edit, a Word action or a custom
method) and 7 bind every mode, and so do copies only, the source guard, the sandbox and its
whitelist, and the governed outcome path (never unguarded). Obliterate is hidden unless `Settings.show_obliterate`,
needs the warning acknowledged each time (`DocumentTask.set_mode(acknowledged=True)`), voids the plan when entered or
left, and is never what a launch opens in (`settings.MODES`, `web.server.launch_mode`). Pinned by
`tests/test_obliterate.py` and, for the record itself, `tests/test_obliterate_record.py`.

Obliterate also has one built-in operation the other modes do not: **`edit_xml`**, the raw XML edit (`ops/edit_xml.py`,
`Op.obliterate_only`; the owner's decision, 2026-11-10): one XML part (never `.rels` or `[Content_Types].xml`), an
XPath, the exact count of elements it selects, and one action (`ACTIONS`: set or remove an attribute; remove, replace,
insert; set text; unwrap). It is a built-in op, so it predicts first: the prediction is the edit itself, the V10
assertion `xml_edit` with a digest of the selected elements' places, and the verifier's twin (`verify/xmledit.py`)
judges and evaluates the XPath itself, makes the same edit in its own copy of the part and compares the part whole
and exactly (`_v_exact`: text and the text after each node too, no significance rules — the part may be any XML).
What the edit does to the words is NOT predicted: for the parts such an assertion names (`raw_parts(plan)`)
`verify/suite.py` leaves the part to that comparison in V2, V3, V4's counts and V5b, lists the text V2's paragraph
reader does not read (`unread_text_changes` → V2's `raw_text`: a chart's title, a property) and adds D1 "What
changed"; the words, that other text and D1's lines are listed before approval from the verifier's record
(`preview.raw_listing` →
`SampleView.raw_xml`/`text_changes`/`changes`, `DocumentTask._say_raw_changes`). Its copies are verified, never
UNPROVEN; the checks of the file still run, so a broken result fails them and is delivered UNVERIFIED. It touches its
part whole, so it is always a step of its own and never tracked; "clear the obstacle, then the same plan" is explicit
stages (a flat list of ops is planned whole on the source, so the plan's own refusal would come first): a case file
keeps the plan the document was set aside from (`DocumentTask._case_plan`, `CASE_PLAN`) and the pass's note says how
(`CLEAR_THEN_SAME`). Safe and Default never see it: `catalog.offered_ops` keeps it out of their catalog, schema,
prompts and `describe_op` (their prompts and tools stay byte-stable), the gate refuses a plan that names it
(`compiler._mode_errors`, `[obliterate-only]`), and a custom method may never assert its kind
(`verify.xmledit.OP_ONLY_KINDS`, `codegen.hard_limits`). Its XPath is bounded on both sides (`check_xpath` /
`_v_judge`: `MAX_XPATH_CHARS`, `XPATH_TIMEOUT_S`, no root path inside `[ ]`, no `following`/`preceding` axis). Every
stretch of text it places is traced like any Obliterate wording (`fragment_text`, `compiler.placed_words`); attribute
values are markup, neither traced nor listed as words (the plan's sentence quotes them). A part with a comment or
processing instruction outside its root is refused (`serialize_xml` would not keep it). Pinned by `tests/test_edit_xml.py` and
`tests/test_edit_xml_window.py`.

Obliterate also has **hands-on tools** (the owner's decision, 2026-11-10; the cheaper editor: nothing in the window
edits XML), off unless the user turns them on — a box in the warning, empty every time (`set_mode(hands_on=)`,
`obliterate_todo(hands_on=)`), or the strip's switch (`set_hands_on`, `/api/hands-on`) — never kept, and gone with
the mode (`DocumentTask.hands_on`, the view's `hands_on`). **Parts**, beside a document, is a read-only browser of
its file's parts (`hands_on.py`: `Reader` over the current file or the original — the list, one part indented for
display a page at a time, a search; `/api/parts`, `/api/part`, `/api/parts/search`; nothing written, no model).
**Save this part** writes the part's own bytes where the user chooses (`DocumentTask.save_part` →
`guard.write_new`, never over a working file, never into the scratch tree). **Replace this part** puts a file of the
user's in a part's place: the op `replace_part` (`ops/replace_part.py`, `Op.hands_on`), which no model is ever told
of (`catalog.offered_ops` lists it in no mode) and which the gate takes only from the plan the window builds while
the tools are on (`compose_submission(hands_on=True)`, else `[hands-on-only]`; `DocumentTask._replace_part`: one op,
one document, every other left out, `outcome_source` `"hands-on"`, `Session.model_free`). It predicts first and
exactly — the member holds the file's bytes, pinned by SHA-256 when chosen, and no other member changes — and the
verifier's `part_bytes` (`verify/partswap.py`) hashes the output member itself; the part is a whole-part kind's
(`verify.xmledit.raw_parts`), so its words are listed, not predicted (`SampleView.raw_what` `"part"`), exactly as
for `edit_xml`. Any member can be replaced, `.rels` and `[Content_Types].xml` included: the bytes are placed as given
and the checks say what Word would not open — a changed part that is no longer well-formed XML is one failed check
(V1; `verify/suite._unreadable_v1`), never an exception out of the suite; V1 also fails a changed part whose root
element is not its role's (`verify.reader.root_problems`) and names a relationship that lacks its id, type or target. A hands-on run is no recipe step
(`saved_recipes.WHY_PART`). Pinned by `tests/test_hands_on.py`.

Obliterate also has **named Word actions** (the owner's decision, 2026-11-10): Word itself may change a copy, through
a FIXED MENU (`word_actions.MENU`: repair, remove protection, accept or reject every tracked change, update or unlink
fields, numbers to typed text, leave compatibility mode, text boxes to plain text, delete drawn shapes). Each action
is code the app ships (`render/word_actions.py`: Word's own commands; `WordSession.run_actions`, the worker's
`actions` action); **no Word script is composed anywhere, by the user or by a model: a case the menu cannot express
is first a new menu item**. The user picks actions beside a document (`DocumentTask._word_actions`,
`/api/word-actions`; no AI: `outcome_source` `"word"`, `Session.model_free`; needs no hands-on toggle and no API key),
or Claude names them in a plan (the op `word_action`, `Op.obliterate_only`, in Obliterate's catalog and prompt alone).
Word saves the whole file again, so nothing is predicted (invariant 1's second exception) and nothing can be planned
after it: a plan of Word actions holds nothing else — a flat list of `word_action` ops, no select, stage or layout
(`word_actions.plan_problem`; the gate's `[word-alone]`, and `[word-missing]` where Word cannot be reached) — and is
never dry-planned; the op's own `plan()` and `apply()` refuse. The path is an Obliterate custom method's
(`GeneratedOp.word_actions`, `codegen.trial_of`): `Session._word_copies` has Word make every planned document's copy
before approval (`word_actions.trial`: a private copy of the document's bytes, never the user's file or a working
copy; macros disabled, links not updated, tracking off while the actions run, one save, `TIMEOUT_S` a document), the
verifier reads each (`verify.run_difference(rewritten=True)`: text parts paired by their place in the document,
`_paired`, never by the names Word gives again; Word's own save marks set aside, `_plain`; V4, V5a and V5c N/A because
every byte is Word's own writing; V1 still fails a copy that does not open as a package), the words added and removed,
what else differs and Word's own count (shown apart, as Word's: `SampleView.word_account`) are listed before the
button, and Create delivers those bytes without running Word again (`codegen._kept_trial`), each marked UNPROVEN
(`word_actions.UNPROVEN_MARK`) or UNVERIFIED. A document Word cannot finish, or finds nothing to do in, gets no copy
and the reason; no copy at all voids the plan (`gui.WordActionsFailed`). "Clear the obstacle, then the same plan" is
two requests here: in a pass over the to-do list the pass stays open after the Word run
(`_advance_generation(keep_pass=)`, `DocumentTask._pass_goes_on`, the note `WORD_THEN_SAME`), and Continue plans the
request the document was set aside from on the copy Word made. Not a recipe step (`saved_recipes.WHY_WORD`). No test
machine has Word: `tests/fake_word.py` stands behind the one seam (`word_actions.run_word_actions`) and saves the
whole file again as Word does; what Word itself does with a real document is the owner's real-file run. Pinned by
`tests/test_word_actions.py`.

Obliterate also has **a rescue at attach** (the owner's decision, 2026-11-10; `rescue.py`): a document the
attach-time check refuses (`triage.Triage.code` says why, in one word, for this alone) carries **Try to rescue**, and
a `.doc` or `.docm` can be attached there to be rescued (`rescue.FORMATS`; `DocumentTask.attach` takes them in
Obliterate alone, and the native dialog offers them, `/api/pick` `rescuable`). `rescue.kind_of` names the one thing
to try: the app's own, with no Word — `doctype` (each part with a document type declaration parsed with entities
unresolved, no DTD loaded and no network, and written again from its root; refused when the part uses an entity the
declaration defines: that text exists nowhere else), `no-paragraph` (one empty paragraph added to the body),
`too-large` (nothing written: the archive limits the file is past raised in this process's environment, to
`HEADROOM` times what it measures and never past `CEILING`; `DocumentTask.archive_limits`, journalled, raised again
at a restart) — or Word's, through the same seam as the Word actions (`word_actions.run_word_actions`, the worker's
`convert` and `repair`, a private copy, macros disabled): `format` (`.doc`, `.docm`), `strict`, `unopenable`, and
`password` (an encrypted document, `rescue.encrypted`, or any file Word answers with its password error). **The
password is handed to Word for that one attempt and written nowhere** — no message, view, journal, log, span or
model; Word is always given one (`NO_PASSWORD` when none was typed) so it never stops to ask behind a hidden window.
What Word saves is checked by `triage_file` and mended once more by the app if that is all it lacks (`MAX_STEPS`).
A document that comes of it is written under the scratch folder's `rescued\`, attached in the refused file's place
(`DocumentTask._rescue`, `/api/rescue`; a job with no AI: Stop ends it and touches nothing else) and carries
`Attachment.salvaged` for as long as it is attached: **SALVAGED**, a permanent mark on the document and on every copy
made from it, in every mode and across a restart (`salvaged_line`, the view's `salvaged`, Export's summary, the
history the model hears). It proves nothing about content — the file could not be read as it was, so there is
nothing to compare with — and from then on the document is one like any other, its copies verified against the
salvaged file. The user's file is read and never written (invariant 4 untouched); a document rescued with its
password has NONE afterwards, and the chat and the mark say so. A file past the size limits is attached as it is,
unmarked: nothing in it changed. A lock file, a folder, a missing file and any other kind of file are still
refused. Safe and Default refuse as before and are told nothing new. Pinned by `tests/test_rescue.py`.

Obliterate also has **Export anyway** (the owner's decision, 2026-11-10; `exports.make_anyway`), which amends
invariant 7's exception for this mode alone. When a clean export — Word (Tracked Changes OFF) or PDF (Clean) — leaves
a MARKED copy without a file (`DocumentTask._marked`: UNVERIFIED or UNPROVEN in `unverified`, or SALVAGED: a copy no
check proves as it is), the Export menu offers that kind **made by Word** under *Made by Word, unchecked*
(`DocumentTask.export_anyway_set` / `export_anyway_view`, the view's `export.anyway`; `POST /api/export` with
`anyway`, the job `export_anyway`, no AI: `web.server.MODEL_FREE_JOBS`). The offer follows a failure and nothing
else: `export()` records each copy a clean kind left out with the md5 it had (`export_missed`, never journalled), and
a copy changed since, a copy with no mark, any other mode and Word (Tracked Changes ON) are offered nothing. Word
runs **commands fixed in code** on a private copy of the edited copy, through the same seam as the Word actions
(`word_actions.run_word_actions`): `exports.ANYWAY_ACTIONS` — `accept_changes`; for the PDF `delete_comments` first, a
command that is on no menu (`Action.offered=False`, as `convert` is) and that no plan can name. Nothing is planned,
predicted or passed through the gate, so nothing proves the file: the chat names it **UNCHECKED**
(`DocumentTask._export_anyway_said`), with Word's own count in Word's words and what the independent reader found in
the file Word saved (`verify.run_difference(rewritten=True)`, its record kept in the run's `records\`, read by
`exports.reading`: whether its words, block for block, are the copy's read with every tracked change accepted — for
the PDF outside the comments Word deleted — whether a tracked change is still pending, for the PDF whether a
comment is still there (`_comments_left`, the app's own count), whether it opens as a package). **Report, do not refuse:** a file with such a finding is written and the finding said; a document Word
cannot finish, whose saved file cannot be read at all, or whose PDF cannot be made or fails `check_pdf` gets no file
and stays offered. The PDF is rendered from the file Word saved (`pdf_export.export_pdfs(basis=)`, so
`PDF_REPORT.md` never says a check passed it). The edited copies are read and never written, nothing joins the
history, no undo point is taken, and a copy made leaves the offer. **No model is told of it in any mode** — no
prompt, tool, catalog or schema names it — and Safe and Default write every Export file the checked way or not at
all. Pinned by `tests/test_export_anyway.py`.

Obliterate is the top rung of a ladder (the owner's decision, 2026-11-10): Safe, then Default for what Safe could not
take, then Obliterate for the one or two documents neither could, so it also starts from the to-do list: the failed
cells of where each document stands (Repository layout, below). A failed cell the engine made — it stopped on the
document, or the document's copy failed a check — keeps a **case file** (`DocumentTask._owe` →
`Standing.record(case=)`: the cell's, one per document per operation, no longer a to-do entry's, the owner's decision
of 2026-11-11; built by `document_task._case_of_exception`, `_case_of_result`, `_case_of_sample`; journalled with the
cell, `_restored_case`): the engine's record, the failed checks, the copy that failed when it is the whole of the
plan (a batch's result or a plan's own preview sample, never a chain's), a document of the same run that got a
verified copy, and the plan the document was set aside from (JSON, `_case_plan`). **Obliterate this one** is a
failed cell's (`DocumentTask.obliterate_todo(path, operation=)`, `/api/todo/obliterate`): it turns the mode on for
that one document, behind the same setting and warning, and begins the pass over it alone on that operation, or the
oldest it owes (`start_todo(operation=, only=)`); its conversation hears that cell's case file (`_case_note`) and
may read the worked example, which no spec can name (`_focus_examples`,
`compiler._Batch.add_examples`). **Use the copy that failed, marked UNVERIFIED** (`USE_FAILED_COPY`,
`use_failed_copy`) takes the held-back copy as it is with no AI call, only after `_failed_copy_problem` (its md5, the
document still the file it was made from, `guard.drifted` on that file): one change, with an undo point. Safe and
Default never read a case file: their pass tells the model the cell's one sentence, and with Obliterate hidden a cell
is shown as before (`_standing_view` adds `obliterate` and `failed_copy` only while Settings shows the mode). Pinned
by `tests/test_obliterate_handoff.py`. The rest of the ladder is in `docs/BACKLOG.md`
("Obliterate: the cost ladder"): decided, and not built until its item is.

## Repository layout

The desktop is one window with two modes (`docs/CONVERSATIONAL_DESKTOP.md`) and a hidden third (Modes, above): **Safe**
— buttons whose plans `recipes.py` builds in code from the user's form, no model call anywhere (`Session.model_free`:
no visual check, no recovery) — and **Default**, the conversation below. Both pass the same gate (`compiler.compose_submission`), preview,
approval and verification. The page in `web/static/` (built from `frontend/`), served by `web/server.py` over one
`document_task.DocumentTask` (contract `docs/WEB_API.md`) and shown by `web/shell.py`. The user attaches Word files, an
optional template and pictures or tables for the edit to use; `triage.py` checks each on arrival without a model call.
`layouts.py` gives a mixed batch one EditSpec per structural layout with one approval; `review_display.py` describes
the previewed spec in the sentences each op writes for itself (`Op.describe`); the window shows no plan card (the chat
says what will change, the previews show it), and those sentences record each copy's changes in `applied`. The one
button lives in the chat pane;
each preview carries a checkmark (filled from the start: it gets its copy) and an X, and Create counts the copies it
makes (`action.copies`, less the X'd; `action.unpreviewed` have no preview); X'd documents are `hold` on the primary action (`Session.run(only=)` keeps
every approved pin) and wait out of the batch on `DocumentTask.todo`, each owing that request (the to-do list and the
pass over it: below); `applied` keeps the changes each copy carries, so the history the model hears is per document;
`runs` keeps each run that delivered copies (its request, the Safe-mode button, the documents it delivered and why
the others got none), which the window lists under the copies as **What you've done so far** in every mode
(`DocumentTask._done_view`, the view's `working.done`; a Safe-mode button it names carries a checkmark; which
documents lack a run is the grid's to say), Undo taking the newest off.
**Where each document stands** (`standing.py`, `DocumentTask.standing`, the view's `standing`; the owner's decisions of
2026-11-11) is the record of what worked on what: an operation per button, request, saved recipe run or Obliterate
action — one no document could take included — and under it each document's attempts, oldest first: done (the mode,
the marks its copy carries), failed (the reason, kept however many buttons follow, with what a pass needs: the
request it failed, `decisions`, the plan the user saw, `seen`, and the engine's case file, `case`), not run, or
waived. `_stands` writes an outcome wherever one is decided; Undo rewinds the record to its approval's number, Start
over clears it, the journal keeps it (an earlier version's to-do list is read into it, `_adopt_todo`), and the
window draws it as a grid under the copies (`WindowState.view` passes `standing` to the page).
**Its failed cells are the to-do list** (`DocumentTask._owed` → `Standing.owed`), by these rules (numbered in
`docs/CONVERSATIONAL_DESKTOP.md`, a test per rule in `tests/test_standing.py`, `test_rule_N_…`). **A failed
document stays in the batch, in every mode**: one the
engine could not change (the plan, a button or a saved recipe refused it; its preview sample failed; its copy failed
a check or could not be made; recovery's leftovers) is recorded by `DocumentTask._owe` as a failed cell and nothing
else, and the next button or request works on it with the others. **Only the user's own set-aside takes a document
out** (`_set_aside`, `DocumentTask.todo`: below). **The pass** (`start_todo(operation=None, only=None)`; the reply
`WORK_TODO`, "Work on my to-do list") takes ONE operation — the oldest that is owed, or the one named — and the
documents that owe it and failed it from the same decisions, or one document; `focus` carries `operations` (document
→ operation id) and `waiting` (the documents of it the user had set aside, back out of the batch if the pass is
dropped, `_back_to_waiting`); copies it delivers turn those cells to done in that mode, both attempts kept, and a
dropped pass leaves them failed. **Work on it** (`work_todo(operation=None, path=None)`, `/api/todo/work`) begins
the pass on one cell, on every document that owes one operation, or on the list, turning from Safe to Default mode
first; it replaced `hand_off`, `/api/handoff` and Try these in Default mode, which took the last button's documents
alone. **Not needed on this one** (`waive(operation, path, on=True)`, `/api/todo/waive`) takes a failed cell off the
list at the user's word (state `waived`, its failed attempt kept under it) or puts it back: no document changes, no
AI call. **Obliterate this one** is the cell's (Modes, above). **Nothing is inferred**, with one exception: the same
Safe-mode button pressed again with the identical form settles its own earlier failed cells on the documents it now
delivers (`Standing.same_button`, the note `SAME_BUTTON_AGAIN`), the new press still an operation of its own; a pass
over one of two identical presses' failures is no press and settles its own cell alone. **The
model hears** the documents the user set aside in the first message (`<todo>`, SET ASIDE BY THE USER) and what the
documents in scope still owe in the app's note (`<app_note>`, `DocumentTask._owed_note`: context only, never during a
pass). The record itself plans, orders and changes nothing: a failed cell keeps no document out of a plan.
`DocumentTask.left_untouched`, the view's `left_untouched` and the page's cards Left untouched by the last button
and Set aside for more work are removed (Left untouched beside a preview, `plan.set_aside`, stays): after a
Safe-mode run the chat names what was not done and points to the grid, and the chat's to-do card lists each owed
line.
A request ends with copies, with a discarded preview or dropped request, or with a reply that is an answer and nothing pending (words
alone: no question, choice, suggested reply, plan, refused plan or custom method — `DocumentTask._answered`): the
decisions are cleared, the conversation ends, and the exchange (the user's words, the answer's first sentence) is kept
in `DocumentTask.exchanges` (journalled, the last `EXCHANGES_KEPT`) and told to the model with the next request
(`Session.compile(earlier=)` → `build_messages` `EARLIER IN THIS CONVERSATION`), so a put-back, an attach or a mode
change revives nothing stale and the next message is a fresh REQUEST; a follow-up while something is pending stays a
revision (`conversation_matches`). `start_todo` still reuses the decisions its failed cells keep.
Documents the user leaves out of a request (`submit_editspec.leave_out` → `Session.left_out`) are not planned,
previewed or run (`planned_files`) and wait under Left untouched; a file the plan refuses does too, at any step of a
chain (`planner.plan_batch_chain` plans every step on every file; `preview.refusals`), and so does a sample that fails
its check while another passes (`preview.failed_samples`, `Session.failed_samples`: never run, its card kept, the
others created; a custom method's preview has none), and the preview of the others stands (outcome planning starts,
and Safe mode's Create is unavailable, only when no file plans or no sample gets through).
`DocumentTask.todo` (each entry with `why` and `reason`) holds only the documents the USER set aside, which wait
out of the batch: the red X (`held`), or the chat's `set_aside` tool at the user's word (`asked`;
`Conversation.set_aside` → `DocumentTask._take_set_aside`); each also has its failed cell, and `put_back` returns one
to the batch without a pass, what it owes staying on the to-do list ("That document is not set aside." for any
other). A file the plan refused or whose sample failed once the others run, a run failure no recovery takes, and
recovery's leftovers at `SET_ASIDE_REST` ("Leave the rest for later") are never on it: each is a failed cell
(`_owe`), on the to-do list and still in the batch. A failed cell the engine made keeps its case file, and with
Obliterate shown offers **Obliterate this one** (Modes, above).
Delivered copies are the working set the next request builds on. The window
works in a scratch folder (`scratch.py`: one per window under `data_dir()\scratch`, locked while it runs, holding
every session folder, dropped file and the journal; deleted at a clean close after one question when a copy is not
saved as it is now — `DocumentTask.export_states`: a copy is saved while a file with its current bytes sits where
Export → Word (Tracked Changes ON) put it, its destination recorded with the md5 of the bytes written — swept at the
next start, a crashed window's taken over and restored), and writes a file where the user chose only through
**Export** (the top bar's menu, `ExportMenu.tsx`; `DocumentTask.export` → `guard.write_new`, never over an input,
never into the scratch tree). Export covers every copy the window lists (`export_set`: the working set and copies a
run still in recovery delivered) in the owner's three kinds (`exports.py`): **Word (Tracked Changes ON)**, the copies
as they are; **Word (Tracked Changes OFF)**, every tracked change accepted, comments kept; **PDF (Clean)**, comments
removed and tracked changes accepted, then `pdf_export`. The clean kinds run as a job (`perform("export")`), each a
fixed spec through the gate and a checked run in `exports\` (invariant 7's exception); a copy whose clean version or
PDF cannot be made gets no file, named with why. Safe mode's Save clean PDFs button was retired for it (2026-10-29).
The assistants point to Export for PDFs (`compiler.SIMPLE_EXPERIENCE`, `exports.PROMPT_NOTE`).
**Discard preview**
(`DocumentTask.discard_preview`; what it withdraws is `discard_kind`, the view's `discard`) withdraws a preview: in Safe
mode it shows again the copies shown before the button (`_before_button`, kept by `_recipe`); in Default and
Obliterate it — or **Drop this request**, at a question, a pause or a reply to continue — ends the request
(`_discard_request`: decisions cleared, the request kept in `exchanges` as withdrawn, a pass's cells still owed and
its set-aside documents waiting again). **Undo the last button** (Safe) or **Undo the last change**
(`DocumentTask.undo`) pops `undo_points` (one per run that delivered copies, taken at approval, kept in the journal)
to put the working set back one run, in every mode: the record rewound to that approval (`Standing.rewind`), the
documents its ✗ set aside in the batch again. Neither writes or deletes a file.
A run that saved some copies while recovery checks its other documents is one change: those copies join the working
set when the check ends (`_advance_generation`), so until Save the ready copies or Leave the rest for later ends it, every
action that would drop the check first waits with the reason (`DocumentTask._recovery_blocked`, the view's
`recovery_blocked`: attach, remove, a mode change across Obliterate or to Safe, Put back, the to-do pass, a Safe-mode
button, a recipe, Discard, Undo, and words not about the documents it checks, `Session.recovery_takes_words`); a run
that saved nothing ends with its check, its undo point and steps with it. A new action that voids the outcome joins
that list.
There is no command line (removed 2026-10-21 with the
library, workspaces, workflows, office profiles, spec QA audits, project evidence, evaluations and the named v0.1.0
jobs). Details: `docs/CONVERSATIONAL_DESKTOP.md`.

- `src/spec_surgeon/`
  - `package.py` (`DocxPackage`: zip, rels, sections, overlay, repack) · `ooxml.py` (transform-side XML helpers).
  - `ops/` — the operation library: `base.py` (`Op`, `PlanFragment`, `merge_fragments`, `JobException`),
    `selectors.py`, `table_select.py`, `prose.py` (the words of `describe()`), one module per op, shared cores
    (`textedit.py`, `slots.py`, `effective.py`, `lists.py`, `style_import.py`, `images.py`, `hf_common.py`),
    `elsewhere.py` (flags naming where a change kept to one place leaves the same words, and a fill in another case than the template; `plan_spec` runs it),
    `edit_xml.py` (the raw XML edit, Obliterate only — Modes, above; an op only Obliterate may plan sets
    `obliterate_only`), `replace_part.py` (a part replaced with the user's file, Obliterate's hands-on tools only:
    `hands_on`, never offered to a model), `word_action.py` (how a plan names one of Obliterate's Word actions; it
    predicts nothing and its `plan()` and `apply()` refuse — Modes, above),
    `patterns.py` (`compile_pattern`: the one way a user- or model-written pattern is compiled under `ops/` — and
    outside it, for the model's read tools' `regex` and an outcome contract's locators — the `regex` module in its
    `V0` (`re`) behaviour, `REGEX_TIMEOUT_S` = 2 s on each text searched, `MAX_PATTERN_CHARS` = 1000 except for an
    exact-text pattern (`exact_text_only`: `\A`, escaped literal text or one group of such alternatives, `\Z` — how the
    app selects bound paragraphs by their whole words); past the deadline `JobException("regex-timeout")`, the file
    set aside — stress-test finding F8; the app's own constant patterns and the ones it builds from escaped literals
    stay on `re`).
  - `verify/` — the INDEPENDENT verifier: `suite.py` (V1–V10 as a `VerificationRecord`), `props.py` (V10 dispatch, the
    residual identity check; each op family's kinds live in their own module, imported at its end; `KIND_CONTRACTS`
    and `KIND_EXAMPLES`, the plain-English contract and worked assertion of each kind, filled by the kind's module
    at import — the verifier's own words, which the custom-method writer reads through `describe_assertion_kind`;
    a kind whose module writes none gets that module's docstring, so every kind has one), `reader.py`,
    `textcheck.py`, `edits.py`, `xsd.py`, `geometry.py` (V6/V7's page evidence, and `shown_words`: the words the
    original's pages show that the copy's do not, less what the plan removes — V6's warning, never a FAIL;
    `report.words_missing_sentence` is its one wording), `xmledit.py` (V10 `xml_edit`, the raw XML edit's twin, and `raw_parts`: the parts whose text the suite leaves to
    its whole-part comparison — Modes, above), `partswap.py` (V10 `part_bytes`: a replaced part's own hash),
    `difference.py` (the Obliterate record, `run_difference`:
    what differs between an original and a copy with nothing predicted, in a `VerificationRecord` every reader of a
    record reads; `rewritten=True` for a copy Word saved: parts paired by place, Word's save marks set aside —
    Modes, above), `template_layout.py` (V7's template reference: the template
    as each document's plan makes it show, its fills in the slots and each section at the document's own text
    width, the template's vertical layout kept — live QA finding F08 — but the source's own header/footer distances
    where the plan keeps them, `keeps_distances` — the deep-dive stress test's D4), `explain.py`, `patterns.py` (the verifier's
    own bounded patterns, `v_compile`/`v_search`, the same deadline and length limit written independently: a
    pattern past its deadline is a `ValueError`, a failed check with that reason, never an exception out of the
    suite).
  - `editspec.py` — load, validate, plan, merge, apply; composition (`stage_cuts`, `compose_chain`, `plan_chain`).
  - `batch.py` (per-file pipeline, guarded delivery, records; `worker_plan`/`map_files`: three or more files go to
    spawned worker processes, each file whole in one, in the packaged app too — a test's patches never reach one,
    so `tests/conftest.py` runs threads), `guard.py`, `chain.py`, `report.py`
    (`FAILURE_EXPLANATIONS`: every refusal code in plain words), `diagnostics.py`, `ui_feedback.py` (the window's
    words for one refusal, `refusal_message`: the step that refused the file — `editspec.plan_chain` records its
    number, count and name — then what was looked for and what the document holds instead, from the record's
    details; the record itself never changes).
  - `catalog.py` (op catalog, `COMMON_ASSUMPTIONS`, `op_sheet`), `profile.py`, `planner.py`, `structure.py`, `triage.py`.
    `structure.analyze_structure(canadian=)` also reads Canadian CSC PageFormat numbering when asked (the outline tool
    asks; Safe mode, the profile and `template_roles` never do: their reading is unchanged).
  - `outline.py` — the chat's `read_outline` (docs/SPEC_FORMATTER_MODES_DESIGN.md N1): the structure reader's roles,
    final except a typed PART/article/section reading that is not shaped like a heading (`demote_unshaped`, the
    Specification Formatter's close negatives), and Claude's reading of the rest in one-shot calls at
    `resources.OUTLINE_EFFORT` (`Conversation._read_outline`: the conversation's model, usage, spending check and Stop),
    every paragraph asked about answered once or left unplaced with why, kept by the file's SHA-256 (`_Batch.outlines`).
    Its selectors name each document's paragraphs under that document's SHA-256 (`indices_by_document`):
    `editspec.resolve_document_select` gives each document its own `indices` and `document_sha256` at planning and
    refuses a document it does not name (`not-outlined`); the verifier hashes the source itself (`verify/props._v_pins`).
    A spec never carries a bare `indices` (`editspec._check_document_indices`).
  - `schemes.py` — the bundled numbering schemes (`examples/schemes/`: CSC PageFormat and CSI, built byte-stable by
    `tests/make_scheme_examples.py`), offered by the chat as an app choice of kind `scheme` whose label the app writes;
    the window attaches a copy as the template and plans again (`DocumentTask._take_scheme`).
  - `compiler.py`, `conversation.py`, `llm.py`, `vision.py` — request → EditSpec. The chat's first message
    (`compiler.build_messages`) carries the long data first and the user's words last, each part in its own XML tag
    under the heading the prompts name it by (`<batch>` THE BATCH … `<request>` REQUEST, then `<answers>`): Anthropic's
    long-context guidance; the message is fixed for the conversation, so the order changes no caching. `llm.call` is
    the one call site: the system prompt carries its own 1-hour cache breakpoint, the history the automatic 5-minute
    one, and a `one_shot`
    call caches nothing after the system prompt; keep system prompts and tools byte-stable. No call runs at effort
    `max` (the owner's decision, 2026-10-22): `resources.EFFORTS` refuses it. The window's chat and its visual check
    run at medium, outcome planning and recovery at high, the outline tool's reading at high
    (`resources.OUTLINE_EFFORT`, through `llm.effort_scope`), and the custom-method writer at `xhigh`
    (`resources.CODEGEN_EFFORT`, 2026-10-26: `generate_op` enters `llm.effort_scope`, after which calls are back at
    their own effort). The chat's `ask_user` and `submit_editspec` stream their input unbuffered
    (`compiler.STREAMED_TOOLS`, `eager_input_streaming`); `llm` checks it and re-sends a call whose input is broken.
    The window's only spending limit is the user's spending check per request (Settings → Spending check,
    `DocumentTask._new_request` inside the conversation's ledger; the owner's decision of 2026-10-27, which removed the
    pause after 48 calls); Stop is the kill switch (`llm.cancellation_scope`). In Safe mode, which calls no AI, Stop
    ends the job itself (`stopping.py`, armed for a Safe-mode job only by `web.server.WindowState.start_job`):
    `stopping.check` between files and steps, `stopping.wait`/`as_completed` over worker pools (`batch.stop_pool`
    terminates their processes), the Word workers' and LibreOffice's polls; `stopping.Stopped` is a `BaseException`
    so no `except Exception` takes it for a file's failure, and `DocumentTask._stopped` says what it left (a preview
    withdrawn, a Create with no copy, its preview waiting and the record of where each document stands as it was
    before the press, an export with nothing saved). A new wait on work
    running elsewhere looks at Stop; a new `except BaseException` re-raises.
    Fast mode (`speed="fast"`, Settings → Fast responses, `llm.set_fast_mode`) is the user's choice, off by default:
    it changes speed and price (`FAST_PRICES`), never the model, effort or prompt; a fast call the fast capacity
    cannot take is re-sent at standard speed. `compiler.compose_submission` is the one gate a submission passes.
  - `preview.py`, `codegen.py` + `sandbox_runner.py` (custom transforms; the writer's read tools are
    `CODEGEN_READ_TOOLS` — the sample files' XML, paragraphs, styles and part texts, and `describe_assertion_kind`,
    a V10 kind's contract from `verify.props.KIND_CONTRACTS`; the prompt and tools are cached, so an addition to
    them is a deliberate change; a writer the spending check pauses stops with `GeneratedOp.budget_stop` and its
    state on disk, and `generate_op(resume_from=)` continues it from its checkpoint in the same folder —
    `Session._generate_custom` keeps the folder in `pending["paused"]`; a trial is verified without pages, so its
    samples get V6/V7 in the preview by the plan the trial predicted, `FileTrial.plan`, and `run_generated` renders
    and compares every copy before delivering any, `_check_layout`; an Obliterate method is its `apply()` alone, read
    by `verify.run_difference`, its tools `CODEGEN_TOOLS_UNGUARDED` with `confirm_op` and no
    `describe_assertion_kind`, and its copies kept from before the approval — Modes, above), `goals.py`, `goal_contracts.py`,
    `goal_operations.py` (reviewed outcome contracts; a target with `all: true` is bound once, by count), `recovery*.py` (one checkpoint per model call, at the turn's end), `continuous_recovery.py`, `campaign_planner.py`
    (bounded investigation of set-aside files: the outcome plan is a choice the user picks, its words beneath it —
    `Session.outcome` auto-takes a custom transform only — and the documents the user left out of the request stay
    out of it, `Conversation.left_out`), `inventory.py` (the content digest that pins a file).
  - `recipes.py` — Safe mode's buttons: each a fixed EditSpec from its form (`build`), the header/footer template's kind
    (`template_kind`); above them the note that says what Safe mode is and points to Default mode with an API key
    (`frontend/src/lib/safeNote.ts`, `SafeNote.tsx`; folded: `Settings.safe_note_hidden`); a form's `lines` field is a
    list of one-line wordings and fields sharing a `row` sit side by side; the second round of buttons (M1, M3) is
    designed in the backlog; M2, **Remove specifier notes**, is built (`_remove_notes`: a two-step chain, no form).
    The font buttons (Match font to template, Change font) group the documents by their own body size, one spec per size as a layout plan when there are two (`_font_layouts`; documents built alike take the
    size most of them have, the rest are left out with why); the page's copy of the list (`frontend/mock/recipes.json`) is pinned to `catalog()`. Find and
    replace, occurrence by occurrence (backlog F1): `replace_table` lists every occurrence it finds in its
    `replaced_text` assertion (and in a refusal's details), `planner.FilePlan.occurrences` exposes them, the window
    shows them per document (`DocumentTask._occurrences_view`, the page's `OccurrencesCard`), and an untick goes back
    as `POST /api/occurrences` → `DocumentTask._occurrences`: the op's `skip` list, the gate again with the recorded
    keys (`compiler.compose_submission(skips=)`: a request never writes `skip` itself), a new preview. The verifier
    re-finds every occurrence, honours the plan's skips by its own reading and records the log under V10's data
    (`verify/suite.occurrence_lines`). **Add a folder of documents** (`DocumentTask.attach_folder`, `/api/attach-folder`)
    attaches a folder's own `.docx` files, subfolders not entered; **Remove all documents**
    (`DocumentTask.remove_documents`, `/api/remove-documents`) takes every Word document out, each as its ✕ would.
    `exports.py` (Export's three kinds: `clean_spec`, `make_clean`, `PROMPT_NOTE`; and Obliterate's Export anyway,
    `make_anyway`: a marked copy's clean kind made by Word, unchecked — Modes, above) and `pdf_export.py` (Export → PDF
    (Clean): a PDF of each clean copy that passed, rendered through `render` and its cache, then `check_pdf`).
    `template_roles.py` (a template's style per role, page and font, read without a model) and `body_match.py` (match
    body formatting: one plan per document from its own structure, one layout per agreeing pattern, each through the
    gate; what it cannot match is left out with the reason, `Session.left_out_reasons`).
  - `hands_on.py` — Obliterate's hands-on tools, the reading half: a document's parts listed, one shown as text, a
    search across them (`Reader`; read-only, no model — Modes, above).
  - `rescue.py` — Obliterate's rescue at attach: what can be tried for a refused file (`kind_of`), the app's own
    mending and Word's conversion or repair of a private copy (`rescue_file`), the size limits raised for the
    window, and the password that goes to Word and nowhere else (Modes, above).
  - `word_actions.py` — Obliterate's named Word actions: the fixed menu (`MENU`, `check`), what a plan of them must
    be (`plan_problem`), the one seam to Word (`run_word_actions`) and one document through Word and the verifier
    (`trial`). The commands themselves are `render/word_actions.py` (Modes, above).
  - `saved_recipes.py` — saved recipes: the runs of What you've done so far kept under a name (`Store`,
    `config_dir()\recipes.json`, each change read, made and written atomically under `recipes.json.lock`, which every
    window takes). A step is a Safe-mode button and
    its form, or the user's words and the plan they approved (`portable_plan`: a custom method, bindings, typed or bound
    slot fills, per-occurrence capitalisation and paragraphs chosen per document by read_outline
    (`indices_by_document`) are refused with the reason; unticked occurrences dropped), with the
    words it places (`compiler.placed_words` at approval). Taken at approval (`DocumentTask._steps_now` → `run_steps`,
    journalled beside `runs`). A run (`build_run`, `DocumentTask._saved_recipe`) builds every kept step from the
    documents attached now (a plan's template, picture or table remapped onto the attached one, `remap` — the owner's
    decision), passes each through the gate, composes them in the user's order into ONE plan (`combine`: a chain, or a
    plan per layout of chains) and through the gate again: one preview, one approval, one checked run, `outcome_source`
    `"saved"` (`Session.model_free`: no AI anywhere). A recipe runs in its own mode or one with fewer guardrails
    (`runs_in`): a Default recipe is never offered in Safe mode, an Obliterate one runs only in Obliterate. A document a
    step cannot take stays in the batch and owes the run (a failed cell of `standing`, `_owe`), in every mode (no AI
    re-plans it); a recipe's run is never handed to Claude as a request (`_invalidate`, the journal's `recipe_run`).
    Edited in Manage (`Store.edit`: name, note, steps kept in a new order, removed, or a button's form changed; a
    plan step is moved or removed whole, never rewritten; refused when the recipe's `revision` moved since the editor
    opened) and grown from the save dialog (`Store.append`, **Add to a saved recipe**); the mode is always its steps'.
    No export or import of recipe files (the owner's decision, 2026-10-29). Pinned by `tests/test_saved_recipes.py`.
  - `standing.py` — where each document stands: the record of every operation asked for and each document's attempts
    under it, done, failed, not run or `waived` (`Standing`: `open`, `record`, `waive`/`unwaive`, `rewind`, `forget`,
    `view`, the journal's `to_json`/`from_json`). A failed attempt carries what a pass over it needs (`decisions`,
    `seen`, `case`); `owed` lists the failed cells, the to-do list `DocumentTask._owed` reads; `same_button` names the
    earlier operations that are the same Safe-mode button with the same form. Plain data; it imports nothing of the
    engine and by itself plans, orders and changes nothing. Pinned by `tests/test_standing.py` (a test per rule,
    `test_rule_N_…`; helpers in `tests/standing_helpers.py`).
  - `tutorial.py` — the guided tour's practice workspace (`docs/CONVERSATIONAL_DESKTOP.md`, "The guided tour"):
    `TutorialTask` (a `DocumentTask` without a journal over the bundled showcase in `examples/tutorial/`, which
    `tests/make_tutorial_showcase.py` builds byte-stable; `send`/`choose` refused, `NO_CONVERSATION`) and `Tutorial`
    (the lease `web/server.py` swaps the window's task for: `begin`, `show(kind)`, `end`; the original task waits
    untouched in `WindowState.original_task`; the practice folder is `tutorial\` in the window's scratch folder,
    `practice_root`, never a folder windows share). Each chapter state (`SCENARIOS`) is built through the production
    paths — triage, `recipes.build` and the gate, `Session.make_preview`, `Session.run` — with no model call; the
    conversation chapters are recordings that say so (`RECORDED`, `TutorialTask.scripted`). The page's side:
    `frontend/src/lib/tour.ts` (the manifest: chapters, steps, `data-tour` anchors, the capabilities each teaches),
    `capabilities.ts` (every end-user control carries a `data-capability`), `useTour.ts`, `tourStorage.ts`,
    `components/TourOverlay.tsx`; a Safe-mode chapter's form is opened by its step (`prepare`: `TOUR_FORM`,
    `TOUR_FIND_FORM`, the values the server's chapter ran), and the user's own form is set aside when the tour starts
    and put back when it ends (`App.tsx`). `frontend/tests/tour.test.ts` fails on a `data-capability` the vocabulary
    lacks, a capability no control carries or no step teaches, an anchor the page lacks, a scenario the server does
    not build, or a form value the server's chapter did not run: **a new control needs its capability and its tour
    step in the same change** (the test cannot see a control that carries no capability; the reviewer can).
  - `document_task.py`, `review_display.py`, `gui.py` (`Session`, `main`; `python -m spec_surgeon` runs it), `web/`
    (`server.py`, `shell.py`, `watch.py`: the page's own reports and the watchdog over the page and the job),
    `scratch.py`, `updater.py`, `settings.py`, `credentials.py`, `winapp.py`.
  - `render/` (Word COM, LibreOffice), `schemas/` (ECMA-376 XSDs), `skill/`, `examples/` (the worked EditSpecs, and
    `examples/tutorial/`, the tour's showcase, shipped in the wheel and the app; `packaging/installed_wheel_smoke.py`
    checks the installed bytes against the checkout).
- `tests/` — pytest; `make_fixtures.py` and `fx_*.py` build synthetic documents; `fake_claude.py` scripts Claude;
  `fake_render.py` fakes the renderer seam (`render.render_pdf_groups`); `fake_word.py` stands in for Word behind
  `word_actions.run_word_actions` (it saves the whole file again, as Word does); `conftest.py` seals keys, config dir and
  model, and hides Word and LibreOffice from every test not marked `@pytest.mark.render` (a test that looks at
  rendered pages carries the mark; one that fakes a renderer patches it itself); `describe_cases/` feeds
  `test_describe.py`. Only a failing test keeps its temporary folder (`tmp_path_retention_policy`, pyproject).
- `docs/` — `BACKLOG.md` (the open work), design notes (`BUILTIN_OPS_BATCH*_DESIGN.md`), `releases/`;
  `TRUST_CLAIMS.md` (the ledger behind the trust surfaces).
- `packaging/` — PyInstaller spec, Inno Setup script, installed-app smoke tests, `license_notices.py` (the app's
  LICENSE.txt and its AGPL Corresponding Source). Spec Surgeon's own code and the app as a whole are
  AGPL-3.0-only (`about.py`); the Windows app bundles PyMuPDF (AGPL-3.0, no commercial license held).
  Preserve earlier MIT grants and their notice (`legal/MIT-legacy.txt`) for previously MIT-licensed portions.
  Third-party components retain their own licenses. Never describe current project code as MIT-licensed.

## How to run

```sh
pip install -e ".[dev,gui,agent]"            # Windows: py -3 -m pip install -e ".[dev,gui,agent,word]"
python3 -m pytest -q -n auto --dist worksteal # every core, as CI; Windows: py -3 -m pytest -q -n auto --dist worksteal
python3 -m pytest -q -n auto -m "not render"  # the quick loop: everything but the tests that render real pages
python3 -m pytest -q tests/test_ops.py        # one module (in a git worktree: PYTHONPATH=src python3 -m pytest …)
cd frontend && npm ci && npm run build        # rebuild the window's page (Node 22) into web/static; commit it
python3 tests/make_fixtures.py OUT            # fixtures; --rich, --template, --reference, --styles-template, --numbering-scheme, --dirty
spec-surgeon-gui                              # the window; or: python3 -m spec_surgeon
```

There is no command line. Check a change with the test suite; to run the engine by hand, call it from Python:
`batch.run_batch(files, "edit-spec", {}, out_dir, spec=editspec.load_spec(path))` for a batch, `verify.run_suite`
for one pair, `gui.Session` for the window's steps with a scripted client (`tests/fake_claude.py`). A run's
`DIAGNOSTICS.md`, `records/` and `logs/` say why a file did not pass. The window's own trail (requests, jobs, the
page's errors, a frozen page, an unclean exit) is in the logs folder, `spec-surgeon.log` and `app-events-<pid>.jsonl`
(`docs/DEVELOPER_DIAGNOSTICS.md`, "The window"); About → Save a diagnostic bundle zips it with the conversation's
journal and the newest session folders' records (all in the window's scratch folder, gone when it closes).

## How to add an op

1. Copy `src/spec_surgeon/ops/set_font.py`; keep `@register`, `name`, `mode`, `params_schema`, `select_schema`,
   `verification_profile`, `describe()`, `plan()`, `apply()`; import it at the bottom of `ops/__init__.py`.
   (An op only Obliterate may plan also sets `obliterate_only = True`: `catalog.offered_ops` and the gate do the rest.)
   `describe(select, params)` returns one or two sentences built with `ops/prose.py` (inches and points, quoted exact
   text, no identifiers, no JSON): the line that records the change in the history the model hears.
2. `plan()` fills the fragment analytically from the source: `intended_parts`, `diffs`, members, `rev_delta`,
   `property_assertions`, and honest `touches` (what it changes and what it reads). A pattern the user or the model
   writes is compiled through `ops/patterns.compile_pattern` (never `re`), and in the verifier through
   `verify/patterns.v_compile`.
3. Write the V10 assertion kind in `verify/` first, with its own selection and an "exactly this change" check.
4. `apply()` mutates the working copy through the overlay (`write_xml`, `remove_member`, `add_member`, `edit_rels`).
5. A fixture, an end-to-end test (plan → merge → apply → `run_suite`, `record.passed`), and a planted-defect test for
   every new rule: a wrong output, and a wrong implementation shared by plan and apply.
6. Every new refusal code in `report.FAILURE_EXPLANATIONS` (and its details enough for `ui_feedback.refusal_message`
   to name what was looked for: the words, the selection, the value); the op in `compiler.placed_words` if it places typed
   text; a `COMMON_ASSUMPTIONS` entry in `catalog.py`; at least one case in `tests/describe_cases/`.

## The trust surfaces

Help → **Why trust it?** (`frontend/src/components/WhyTrustIt.tsx`) and the "I'm not convinced" dossier it opens
(`TrustDossier.tsx`, its cards in `lib/trustRuntime.ts`, its diagram in `TrustDiagram.tsx`) describe what the app does
action by action. **The trust surfaces are a contract. Any change to a behaviour they describe updates them in the same
change:** a new user action or button (which also needs its capability and tour step, above), a new network call or destination, a new automatic behaviour (a model call
without its own click, a timer, a background job), a model, effort or caching change, a new or changed limit, or
anything new kept on disk. Update `docs/TRUST_CLAIMS.md` (the ledger: every claim, its source, its test) with it. Every
number the copy quotes lives in `frontend/src/lib/trustFacts.ts`, and `tests/test_trust_facts.py` pins each to its
constant in the code; the copy never types a number itself. A trust document that has drifted from the code is worse
than none.

## Session protocol

The open work is in `docs/BACKLOG.md`. Read it before any work, and take an item from it unless the user asks for
something else. The pull request that finishes an item removes it from the backlog, or moves it to "Waiting on the
owner's machine" when only a real-file run is left. The pull request says what changed and what is tested, so the
backlog keeps no log and no dated notes. Do not add dated plan files: a new operation gets its design in the backlog
(or in a design note it links) before it is built. Nothing is done on fixtures alone: an item's acceptance names a
real-file run, which the user performs.

Standing decisions from the earlier rounds (their plans are in the git history):

- Text boxes as objects are not pursued (the words inside one: "Out of reach by decision", above). No raw-XML
  operation, catch-all deletion operation or `force` flag is added. In Safe and Default this stands. For Obliterate
  alone the owner reversed parts of it on 2026-11-10 (next item); `force`, and any write to an original, stay out in
  every mode.
- Obliterate's cost ladder (the owner's decisions, 2026-11-10; `docs/BACKLOG.md`, "Obliterate: the cost ladder").
  Obliterate is the emergency lever: inside the copy nothing is refused for being unusual, the original and the
  machine stay protected (copies only, the source guard, the sandbox's containment), and everything done is reported
  (the verifier on every copy, the marks, the words added and removed). Decided, for Obliterate only: a custom method
  just does the work and the verifier reports the difference, the user approving the actual result (built: invariant 1
  is lifted for that one path; built-in ops predict first in every mode); a raw-XML operation Claude plans (built:
  `edit_xml`, Modes; a catch-all deletion is its `remove`),
  with a read-only part browser and swap-a-part behind a toggle (built: the hands-on tools, Modes; in-app XML
  editing is not built); Word may edit
  copies through a fixed menu of named actions the user or Claude chains (built: the Word actions, Modes — a plan of
  their own, with nothing planned after them in the same approval; Claude-composed Word scripts are not
  built: a miss is first a new menu item); a file refused at attach may be rescued (built: Try to rescue, Modes;
  the rescued document is marked SALVAGED for good); Export may make a marked copy's
  clean version anyway (built: Export anyway, Modes — made by Word, named UNCHECKED, offered only after the checked
  way left that copy without a file). Until an item is built, the invariants and the Modes section describe the code
  as it is.
- In Safe and Default, a custom method may do more kinds of formatting, never more kinds of change: a family is added
  only behind a V10 kind that proves the content it touches is the source's, and the sandbox's limits stay as they are.
- Capitals are a display property (`w:caps`) unless the user gives capitalised wording; nothing retypes a file's letters
  in another case.
- The batch overview and the plan's flags describe and never decide: they change, choose and order nothing.
- The standalone find-and-replace tool (`Abe-Borg/docx-find-and-replace`, python-docx and tkinter, editing in place)
  is not merged into Spec Surgeon (the owner's decision, 2026-10-27): Safe mode's **Find and replace** button is where
  that work lives (its rule-set CSV loads as the attached table), with every occurrence listed to untick (backlog F1,
  built), the occurrence log in the records (F2) and a folder of documents attached at once (F3). Text boxes, editing
  in place over the originals and re-scanning replaced text stay out, as decided above and by T1.
- The Specification Formatter (`Abe-Borg/spec-template-normalizer`) is not merged as code; what it does lives in
  Default mode, rebuilt under these invariants (the owner's decision, 2026-10-29;
  docs/SPEC_FORMATTER_MODES_DESIGN.md): the outline tool, per-document chosen paragraphs, the header template's
  even/odd switch (`even_odd: "template"`), the bundled schemes, `convert_numbering` `styles: "keep"` and
  `convert_typed_numbers` `typed_as: "csi"`. Safe mode is untouched by it. Numbers stay automatic: no literal CSI
  marker is written (a real job that needs typed numbers gets its own design first), and a typed Canadian ".1" is not
  converted. The template's theme and compatibility settings are never carried.
