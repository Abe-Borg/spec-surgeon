# The window's API — contract between the server and the web window

Spec Surgeon's desktop window is a web page (React, built once into `src/spec_surgeon/web/static/`) hosted in a
native WebView2 window by `pywebview`, talking to a local FastAPI server (`spec_surgeon.web.server`) that wraps one
`DocumentTask`. The server binds `127.0.0.1` on a free port; every request carries the launch token
(`X-Spec-Surgeon-Token`, also accepted as `?token=` on image URLs and the SSE endpoints), which the shell hands the
page in its address, `?token=…&api=http://127.0.0.1:<port>&theme=<scheme>` (an injected
`window.SPEC_SURGEON = {token, version, api}` would win over the address). `theme` is the colour scheme kept in
`settings.json`; the page's inline script applies it before the first paint. Without the right token
every `/api` route answers 401. One job runs at a time: a second `send`, `choose`, `primary` or `attach` while
`busy` is true answers 409 `{"error": "busy"}`. So do `recipe`, `mode` and `handoff` (Safe mode, below).

The page never applies edits or derives state itself: every `done`/`state` event and `GET /api/state` carry the
FULL view, and the page renders it. Streamed frames (text deltas, status, activity) are presentation only; the view
that ends a job is authoritative.

## The view

```json
{
  "version": "1.6.0",
  "busy": false,
  "stage": "empty | question | preview | done | budget | resume",
  "connection": {"ready": true, "origin": "saved | environment | session | none"},
  "cost": {"usd": 0.12, "text": "about $0.12", "calls": 3, "fast": false, "request": 0.04, "limit": 20.0},
  "attachments": [
    {"path": "C:/Specs/21 13 13.docx", "name": "21 13 13.docx", "kind": "document | template | reference",
     "state": "ok | note | refused", "notes": ["Numbers are typed by hand, not automatic list numbering."],
     "reason": "", "current": "C:/.../runs/20261020-1/run/outputs/21 13 13.docx"}
  ],
  "messages": [
    {"id": "m1", "role": "user | assistant | app", "text": "Change the font to Arial 10 pt.", "at": 1760000000.0}
  ],
  "plan": {
    "groups": [{"id": "", "label": "", "count": 2, "files": [],
                "changes": [{"title": "Change fonts", "summary": "Set the font of every run …", "details": ["…"]}],
                "assumptions": ["…"]}],
    "targets": [{"title": "21 13 13.docx", "source": "…", "details": ["…"]}],
    "set_aside": [{"name": "…", "reason": "…"}],
    "stages": 2
  },
  "questions": ["Only the body text, or the headers, footers and titles too?"],
  "choices": [{"id": "c1", "label": "Only the body text", "words": [], "detail": "", "editable": false}],
  "chips": ["Everything, including the headers", "Just the body"],
  "samples": [{"source": "…", "name": "21 13 13.docx", "images": ["/api/image?path=…"],
               "pages_before": 12, "pages_after": 12, "passed": true, "failures": [],
               "geometry": {"status": "PASS", "note": "Page layout checked on the rendered pages: …"}}],
  "occurrences": [{"source": "…", "name": "21 13 13.docx", "status": "planned | refused", "reason": "",
                   "items": [{"key": "…", "part": "word/document.xml", "paragraph": 11, "start": 30, "end": 38, "where": "the body",
                              "heading": "2.01", "before": "H. See the", "match": "2022 CBC", "after": "portal.",
                              "find": "2022 CBC", "replace": "2025 CBC", "skipped": false, "op_index": 0}]}],
  "warnings": ["…"], "blocking": false,
  "results": [{"source": "…", "output": "…", "name": "21 13 13.docx", "unverified": [], "saved_to": "",
               "changed_since_saved": false, "saved_file_changed": false}],
  "pdfs": [{"source": "C:/Specs/21 13 13.docx", "output": "C:/.../scratch/20261027-090000-7/20261027-1/run/pdf/21 13 13.pdf",
            "name": "21 13 13.pdf", "unverified": [], "saved_to": "", "changed_since_saved": false,
            "saved_file_changed": false}],
  "pdf": {"available": true, "renderer": "Microsoft Word | LibreOffice", "reason": ""},
  "export": {"folder": "", "unsaved": 2, "copies": 2},
  "action": {"label": "Create edited copies", "token": "…", "kind": "approve | continue | save | none",
             "can_hold": true, "export": " | pdf"},
  "todo": [{"path": "C:/Specs/21 05 00.docx", "name": "21 05 00.docx", "why": "held | asked | refused | failed",
            "reason": "You marked its preview as needing more work."}],
  "focus": [],
  "working": {"generation": 0, "from_originals": true, "can_start_over": false, "undo": "", "undo_blocked": ""},
  "budget": {"paused": false, "reason": "limit | unpriced | interrupted | "},
  "mode": "safe | default | obliterate",
  "modes": ["safe", "default"],
  "template": {"kind": "none | prepared | reference | unusable", "typed_slots": ["ISSUED FOR PERMIT"], "reason": "",
               "body": {"roles": {"part": "PRT", "article": "ART"}, "styles": [{"id": "PRT", "name": "PRT"}],
                        "labels": {"part": "PART"}, "order": ["section_id", "part"], "page": "8.5 × 11 in portrait, margins 1 in",
                        "font": "Arial 10 pt", "problems": []}},
  "left_untouched": [{"path": "C:/Specs/21 05 00.docx", "name": "21 05 00.docx", "reason": "…"}],
  "tutorial": {"active": false, "id": "", "scenario": "", "finished_version": 0},
  "update": {"state": "none | available | downloading | ready | installing | updated | failed | error",
             "version": "", "notes": "", "progress": 0, "error": "", "text": "", "manual_result": "",
             "install_log": ""}
}
```

Rules the page relies on:

- `messages` is the whole conversation, oldest first. `app` messages are the app's own notes (quiet, centred).
- `questions`/`choices`/`chips`: `choices` carry approval semantics (picking one may approve exact `words`);
  `chips` are plain suggested replies the model offered and are sent as typed text. Both appear only in stage
  `question`, under the latest assistant message. Choices with `words` show the words beneath the button.
  Each of the model's words shows once: the assistant message carries its reply without the questions, and
  `questions` never repeats that message (a reply with nothing to pick, plain words or a failure, is in `messages`
  only; `questions` carries it only when the chat did not say it, as a recovery turn's). An outcome plan (the
  conversation's fallback when no ordinary plan fits) is one choice, **Use these outcomes and continue**, with the
  words it would place beneath it and the outcomes in full in `questions`; the app carries it out only once it is
  picked (`DocumentTask._take` approves the words as any pick does).
- `mode` is the window's mode (docs/CONVERSATIONAL_DESKTOP.md, "Safe mode"). In `safe`, `action.kind` is only ever
  `approve` or `none` (no conversation to continue), `send` and `choose` end in `error`, and the page shows the
  buttons (`GET /api/recipes`) in place of the composer. `template` says what kind of header/footer template the
  attached template is (`recipes.template_kind`): a prepared template's `typed_slots` are the values its form asks
  for; `body` is what *Match body formatting to template* reads from it (`template_roles.detect`: the detected style
  per role, the template's paragraph styles for the form's `roles` field, the page and font in words, and `problems`
  — non-empty, the button cannot be pressed, the first one says why). `left_untouched` lists what the last Safe-mode button's run gave no copy, with the engine's reason, until the
  next button or a hand-off; it is empty while a preview waits.
- `pdfs` lists the clean PDFs the last run saved (Safe mode's **Save clean PDFs**, `pdf_export`), keyed by the attached
  document; `results` is empty for that run, since the PDFs change no working copy (`working` is as it was). A later
  button's press, a run that saved none, Start over and Undo replace it (Discard shows again what the press replaced).
  `pdfs[].unverified` carries the marks of a working copy delivered UNVERIFIED earlier (Obliterate mode), as an
  earlier copy's: the PDF's clean copy passed its checks, against that copy (`[]` otherwise); the page marks it.
  `pdf` says whether this computer can make PDFs and with which program; `available: false` holds back every button
  whose `needs` names `pdf`, `reason` saying why (an older server sends no `pdf`: the page holds nothing back). While
  such a button's preview waits, `action.export` is `"pdf"` and `action.label` is "Save clean PDFs"; it is `""`
  otherwise.
- `modes` lists `obliterate` only while Settings → Advanced shows it (`show_obliterate`). In `obliterate` the page is
  the conversation, as in `default`, with Obliterate's strip above the composer; the engine runs with
  `Session.unguarded` (CLAUDE.md, "Modes"). `results[].unverified` names the checks a copy failed when it was
  delivered anyway in that mode (`[]` for a verified copy); the page marks it UNVERIFIED.
- `tutorial` is the guided tour (docs/CONVERSATIONAL_DESKTOP.md, "The guided tour"): `active` while the practice
  workspace stands in for the conversation, `id` the tour's lease, `scenario` the chapter state last built (one of
  `tutorial.SCENARIOS`: `start`, `button`, `copies`, `conversation`, `question`, `plan`, `todo`, or `""` before the
  first), `finished_version` the tour version this computer finished (`settings.json`'s `tutorial_version`; 0 =
  never). While `active`, every other field describes the practice workspace, `send` and `choose` end in `error`
  before any model call, and `/api/new-conversation` answers 409.
- `plan` is present whenever a plan exists (stages `preview` and `question` with a partial result); `stages` is the
  number of ordered steps the engine composed internally, never a procedure the user performs. The page shows only
  `set_aside` (the **Left untouched** card); `groups`, `targets` and `stages` tell it a plan exists but are not
  displayed (the chat says what will change, the previews show it).
- `samples[].images` are URLs served by `GET /api/image?path=…&token=…` (PNG side-by-side before/after pages).
- `samples[].failures` lists actual failed checks as `{check, reason}` in plain language, including earlier chain
  steps that failed before a later step passed. A preview that could not complete says so; a missing renderer is
  not a failed verification. `samples[].geometry` is the page-layout verdict (check V7) in plain words — `status`
  `PASS`, `FAIL` (then V7 is among the failures and the sample did not pass) or `NA` with a `note` saying why: no
  layout reference by design, a wording change that reflows the text, or that the run checks it — no pages rendered
  here, or pages rendered here (the before/after pictures and page counts stand) that the preview could not check,
  because it holds no plan for the sample (a custom method's trial) or because the layout reference could not be
  rendered or compared, the note saying which; a plan done in steps renders its last step's pages only, so an
  earlier step's layout is checked at the run. A note never says no pages were rendered when they were.
  `"at": "run"` marks the verdicts the run will give. `null` for a sample the plan refused. Common refusal reasons under Left untouched are presented without operation numbers
  or extraction patterns; the engine's original exceptions and check records remain in diagnostics.
- `occurrences` is the find-and-replace list of the plan waiting for the user (stages `preview` and `question`; empty
  otherwise, and empty when the plan has no `replace_table` op): one entry per document the plan covers — every
  document, not the sampled ones only — with every occurrence the plan found, kept or unticked (`skipped`). `where`
  says which part in words, `paragraph` is the paragraph's ordinal in that part from 0 (the page shows it from 1),
  `heading` the article the body paragraph sits under when the structure analysis reads one (`""` otherwise), and
  `before`/`match`/`after` the words around it, whitespace collapsed; none of these is verified, the occurrence's
  place is. `key` is what the page sends back as `skip` (below); the server resolves it from its own record, never
  from the page's numbers. A document with `status` `refused` is one the plan set aside (its `reason` as under Left
  untouched); its items are every occurrence the op listed (a refusal waits until the whole document is listed), the
  refused one included, so the user can
  untick the one the reason names and the document then plans. A plan done in steps lists its first step's only. An
  older server sends no `occurrences`.
- `samples` are the pages of the plan waiting for the user (stages `preview` and `question`), less any document
  whose copy a partial save already delivered; every other stage sends none, so a finished run takes its
  pages along. The visual check's findings in `warnings`, and `blocking`, follow the same rule.
- `action.kind`: `approve` → the one button "Create edited copies" (or "I reviewed the flagged pages — create
  copies" when `blocking`), `continue` → "Continue", `save` → "Save N ready copies", `none` → no button. The button
  lives in the conversation pane, above the composer; while `busy` the composer shows "Stop now" instead.
  `action.disabled_reason` is empty when the action is available; otherwise the button is disabled and this reason
  appears beside it and in its tooltip. Safe and Default refuse Create after a failed preview check; the server
  enforces the same gate even if a caller submits a current token directly. Recovery can still save READY copies.
  In Safe mode, `results` contains only the current button's copies; earlier copies remain in the working set and
  on disk, so a later failed or refused button cannot present them as its results.
  `action.can_hold` is true when the previews can be marked: each preview card then carries a green checkmark
  (looks good) and a red X (needs more work). The page sends the X'd previews' `source` paths as `hold` with the
  primary action: those documents go on the to-do list and the others get their copies. It is false during a
  recovery's "Save N ready copies".
- `todo` lists the documents waiting on the to-do list (attached originals), each with `why` and a `reason`
  sentence the page shows as written: `held` (the red X above), `asked` (the user asked the assistant to set it aside,
  its `set_aside` tool), `refused` (the plan could not be applied to it; it goes on the list once the others run) or
  `failed` (its copy could not be made and nothing will check it again, or recovery had not fixed it when the user
  set the rest aside). An older server sends neither field; the page reads that as the red X. While the list is
  non-empty and no pass is under way, the page shows a to-do card whose button sends the reply "Work on the
  documents I set aside" (as a chip), and the workbench lists them with a **Put back** beside each
  (`POST /api/put-back`). `focus` lists the documents of that pass while it runs: they are the only documents in
  scope, the plan is made for them alone, and the pass ends when their copies are created. Documents on the to-do
  list are out of scope until the pass begins or they are put back; the server also offers the same reply in
  `chips` outside the question stage.
- During recovery (stage `question`), once every ready copy is saved, `chips` carries "Set the rest aside for later":
  sent as a reply, it puts what recovery has not fixed on the to-do list and ends the request (no model call).
- `results` lists the edited copies of the latest run, each with `saved_to` (where Export saved it, while that file
  is still there; `""` while the copy lives only in the window's scratch folder, or once the saved file is gone) and
  `changed_since_saved` (the copy's bytes are no longer the ones written to `saved_to`: a copy opened from the window
  — the working copy in the scratch folder — and edited in Word; it counts as unsaved again, the page says "changed
  since it was saved" beside the link, and the next Export writes it beside the file saved before) and
  `saved_file_changed` (the copy is as it was, but the file at `saved_to` no longer holds the bytes Export wrote:
  written over, truncated or edited since; the page says NOT SAVED beside the link and opens the working copy, and
  the next Export writes the copy beside that file). A copy is saved when a file with its current bytes sits where
  Export put it: the server keeps the md5 of the bytes it wrote and compares both the copy and the saved file with
  it (`DocumentTask.export_states`). `pdfs[]` carries the same three fields. `working.generation` > 0 means the
  next request builds on those copies, and `can_start_over` shows the "Start over from the originals" chip.
- `export` is `{"folder", "unsaved", "copies"}`: where Export last saved (`""` before the first), how many working
  copies and clean PDFs are not saved as they are now (never saved, changed since they were, their saved file
  changed since, or gone; only a saved copy survives closing the window), and how many working copies there are.
  The Export button is disabled at `copies` 0 and says the `unsaved` count.
- `working.undo` is the request of the last run that delivered copies (the button's sentence in Safe mode), which
  `POST /api/undo` goes back before; "" when nothing can be undone. `working.undo_blocked` says why it cannot be
  undone now ("Discard the preview first." while a preview waits, a request still open); "" when it can. In Safe mode
  the page shows **Undo the last button** beside Start over while `undo` is set, disabled with `undo_blocked` as its
  reason. While a Safe-mode preview waits (`mode` `safe`, stage `preview`), the page shows **Discard preview** beside
  the one button (`POST /api/discard`), alone when `action.kind` is `none`; **All buttons** discards it too. The page
  keeps each button's form when the user goes back to all the buttons (page memory only; **Clear form** empties it).
- The page keeps the unsent message and staged Safe form across mode switches. Only a conversation mode's draft
  holds its Create action. A successful New conversation or Start over clears both inputs; a failed reset keeps
  them. Start over retains the attached originals and clears the last button's untouched status; New conversation
  starts with no attachments.
- `cost.fast` is true while fast mode is in effect (`llm.fast_for(llm.DEFAULT_MODEL)`: Settings → Fast responses
  is on, the model is served fast, and Anthropic has not refused fast mode for this key): the spending pill then says
  "Fast". An older server leaves it out.
- `update.manual_result` is the answer to the last check the user asked for (About → Check for updates): "You're up
  to date — …", "No release has been published yet." or "Couldn't check for updates: …". `POST /api/update/check`
  clears it when the check starts, so About shows "Checking for updates…" until it is written again or `state`
  reports an update. An automatic check never writes it.

## Routes

`connection.ready` and Settings' `key_present` report credential availability, not tested authentication. Saving a
key makes no provider call. `can_remove_key` permits removal only for the app's session/saved key. Removal restores
any launch-time environment key and leaves auth tokens/SDK profiles intact; other credentials may therefore remain
available afterward. A blank key in a settings update still means "leave unchanged".
Key replacement/removal also discards app-created conversation clients that cached the previous credential;
explicitly injected clients are retained. Both key replacement and removal refuse while work runs (409).

| Method | Route | Body | Answer |
| --- | --- | --- | --- |
| POST | `/api/settings/remove-key` | | the settings; 409 while work runs; 400 for external-only credentials or secure-store deletion failure |
| GET | `/api/state` | | the view |
| POST | `/api/attach` | `{"paths": [..], "template": false}` | the view; a document triage refuses is in it, `state` `refused` with its `reason` (400 with `{"error"}` for a file that is not a Word document, picture or table, a refused template, or a `paths` that is not a list of paths); a request that fails attaches none of its files: every file is triaged before any is attached |
| POST | `/api/attach-folder` | `{"folder": "…"}`, optional: without it the native folder dialog asks | **Add a folder of documents**: the folder's own `.docx` files attached as if picked one by one (sorted by name, Word's `~$` lock files skipped, subfolders not entered — the `app` message says how many Word documents the subfolders hold, to add those folders too); each file is triaged as any attachment; the view. `{"cancelled": true}` when the dialog was dismissed; 400 with `{"error"}` for a folder with no Word documents of its own, or one of the app's own folders (the scratch tree, the run root, the config folder); 409 while work runs; 501 without a dialog and a folder |
| POST | `/api/upload` | multipart `files[]`, `template` field | copies the bytes under the scratch folder's `attachments/<stamp>/` (deleted with it when the window closes) and attaches them as `/api/attach` does; the view. 400 with `{"error"}` naming the file when the folder cannot take its name (too long for the path: Windows' 260 characters; the sentence says to shorten the name, the app never shortens it, since the attached name is the copy's planned name), nothing attached then |
| POST | `/api/pick` | `{"kind": "documents | template | references"}` | opens the NATIVE file dialog through the shell (pywebview); `{"paths": [...]}` — 501 when no shell |
| POST | `/api/remove` | `{"path": "...", "template": false}` | the view |
| POST | `/api/send` | `{"text": "...", "chip"?: true}` — `chip` marks one of the model's suggested replies the user tapped: their decision, never approved wording | SSE stream (below) |
| POST | `/api/choose` | `{"choice_id": "c1", "wording": "…"}` — `wording` only for an `editable` choice (one drafted wording): the user's edit, approved instead of the draft (400 when it is not text; an edit that breaks the wording rule ends the job in `error`) | SSE stream |
| POST | `/api/primary` | `{"token": "...", "hold"?: ["<sample source path>", …]}` — `hold` names the previewed documents marked with the red X: set aside on the to-do list, the others run; a path that is not one of the plan's documents is refused (400 for a malformed list, the job's `error` for an unknown path) | SSE stream |
| GET | `/api/recipes` | | `{"recipes": [{"id", "label", "group", "summary", "needs": ["template" \| "picture" \| "pdf"], "fields": [{"name", "kind": "text \| pairs \| choice \| check \| picture \| table \| slots", "label", "required", "default"?, "options"?, "help"?, "placeholder"?, "optional_choice"?}]}], "groups": [...]}` — Safe mode's buttons (`recipes.catalog()`), the same for the whole session |
| POST | `/api/recipe` | `{"recipe": "<id>", "inputs": {<field name>: value}}` — a Safe-mode button and its form; a form that cannot be used, or a plan the gate refuses, is said in one `app` message and nothing is previewed; 400 for inputs that are not an object | SSE stream |
| POST | `/api/occurrences` | `{"skip": ["<key>", …]}` — every find-and-replace occurrence the user unticked in the preview's list, by its `key` (the whole unticked set: a key left out is an occurrence put back); the previewed plan is made again with those occurrences left as they are (backlog F1: each `replace_table` op's `skip` list is exactly the unticked occurrences of its own, the spec passes the gate again with those keys as the ones recorded from the user's unticks, and the preview is remade on the same documents; the approval token changes, so the page's marks start over); 400 for a body that is not a list of keys; the job's `error` when no preview waits or a key is not in the list; a plan the gate refuses keeps the preview as it was and says so in one `app` message | SSE stream |
| POST | `/api/mode` | `{"mode": "safe \| default \| obliterate", "acknowledged"?: true}` — `obliterate` needs `show_obliterate` on and `acknowledged: true` (the user ticked its warning this time); entering or leaving it voids the plan | the view; Safe or Default is kept in settings.json, Obliterate never is (the next start opens in Default); 400 for another mode, a hidden Obliterate or a missing acknowledgement |
| POST | `/api/handoff` | `{"paths": ["<left_untouched path>", …]}` — the documents go on the to-do list with the button's sentence as their request, and the mode becomes `default`; 400 for a path the last button did not leave untouched, or while a preview waits | the view |
| POST | `/api/stop` | | `{"ok": true}` — the running model call is cut off; the stream then ends with `done` |
| POST | `/api/put-back` | `{"path": "<a todo entry's path>"}` | the view (the document is off the to-do list and back among the documents the next request works on; during a pass it joins them when the pass ends; a plan on screen is void when the documents in scope change); 400 with `{"error"}` for a path not on the list, 409 while work runs |
| POST | `/api/start-over` | | the view (the working set returns to the originals; the plan is void; the to-do list is cleared; nothing can be undone) |
| POST | `/api/discard` | | Safe mode's Discard: the view (the waiting preview withdrawn, nothing created; `results` and `left_untouched` as they were before the button was pressed); 400 with `{"error"}` outside Safe mode or with no preview waiting, 409 while work runs |
| POST | `/api/undo` | | the view (the working set back to the copies from before the last run that delivered copies; `results`, `left_untouched` and the to-do entries that run's ✗ added as they were; no file written or deleted); 400 with `{"error"}` when there is nothing to undo, a preview waits, a request is open or a copy it would go back to is gone, 409 while work runs |
| POST | `/api/new-conversation` | | the view (starts empty over the same scratch folder; the journal is written over; the page asks first when a copy was never saved with Export); 409 with `{"error"}` while a tour runs (the page ends the tour first) |
| GET | `/api/tutorial/status` | | the view's `tutorial` block |
| POST | `/api/tutorial/start` | `{"request_id": "<the page's id for this start>"}` | begins the guided tour: the conversation's task is set aside untouched, a `TutorialTask` over the bundled showcase (`examples/tutorial/`, copied to a fresh practice folder under `data_dir()/tutorial/`) takes its place in the `start` state; the view, its `tutorial` block naming the tour. The same `request_id` sent again joins the tour that start began (a retried request starts no second one); 409 while work runs or another tour is running; 500 when the practice folder cannot be made |
| POST | `/api/tutorial/scenario` | `{"tutorial_id": "<id>", "kind": "start \| button \| copies \| conversation \| question \| plan \| todo"}` | builds that chapter's state from the showcase through the production paths (no model call) and shows it; the view. The kind already shown is answered without rebuilding; 400 for an unknown kind; 409 for a stale id or while work runs; 500 when the build fails (the view is unchanged) |
| POST | `/api/tutorial/restore` | `{"tutorial_id": "<id>"}` (`null` ends whatever tour runs) | ends the tour: the conversation's task back exactly as it was, the practice folder deleted; the view (`tutorial.active` false). Idempotent for a tour already ended; 409 for another tour's id or while work runs |
| POST | `/api/export` | `{"folder": "…"}`, optional: without it the native folder dialog asks, opened at `export_folder` | Export → Save the copies…: every working copy, and every clean PDF of the last Save clean PDFs run, written into the folder under its planned name (a file there with the same bytes is that copy, saved; a different one is left alone and the copy numbered beside it; a destination that is an attached file or a working copy, or a folder inside the app's scratch tree, refuses the whole export, nothing written); the view, with `results[].saved_to`, `pdfs[].saved_to` and `export` filled (each copy's destination recorded with the md5 of the bytes written, so a copy changed since reads as unsaved again) and one `app` message; `{"cancelled": true}` when the dialog was dismissed; 400 with `{"error"}` for nothing to save or a folder that refuses, 409 while work runs or a tour runs, 501 without a dialog and a folder |
| POST | `/api/open` | `{"path": "..."}` | opens a result in Word, a clean PDF in the PDF viewer, or a folder, with the OS (`{"ok": true}`); 404 if gone. The page sends `results[].saved_to` when it is set and neither `changed_since_saved` nor `saved_file_changed` (the saved file holds the copy's current bytes), else `results[].output`, the working copy in the scratch folder (the copy the list names, with the newer content when it was edited there; `frontend/src/lib/workbench.ts`, `openTarget`); the hover text says which opens |
| GET | `/api/image` | `?path=…` | the PNG (only files under the window's scratch folder or its config folder are served; else 404) |
| GET | `/api/settings` | | `{"key_present": true, "origin": "saved", "can_remember": true, "export_folder": "…", "logs_dir": "…", "theme": "clay", "spending_limit_usd": 20.0, "spending_default_usd": 20.0, "fast_mode": false, "show_obliterate": false, "tutorial_version": 0, "fast": {"model": "claude-opus-5-5", "available": true, "speedup": 2.5, "price_factor": 2, "standard_usd_per_mtok": [4.0, 20.0], "fast_usd_per_mtok": [8.0, 40.0], "refused": null}}` — `fast` is what Settings says about fast mode, from `llm.PRICES` / `llm.FAST_PRICES` for the model the calls use; `refused` is the API's reason when it refused fast mode for this key; `export_folder` is where Export last saved (its dialog opens there; `""` before the first export) |
| POST | `/api/settings` | `{"api_key": "sk-…", "remember": true}` and/or `{"theme": "linen"}` and/or `{"fast_mode": true}` and/or `{"show_obliterate": true}` and/or `{"spending_limit_usd": 12.5}` and/or `{"tutorial_version": 1}` | the settings (`GET` also carries `tutorial_version`, the tour version this computer finished, 0 = never; the page writes it when the tour is finished, and it is kept in `settings.json`); 400 with `{"error"}` (an unknown scheme, a `fast_mode` or `show_obliterate` that is not a boolean, a `spending_limit_usd` that is neither a positive amount nor `null`/`0`, a `tutorial_version` that is not a whole number of 0 or more). `spending_limit_usd` is the spending check per request (`null` or `0` turns it off; `GET` says `null` then), kept in `settings.json` and used from the next request (`DocumentTask.set_spending_limit`). `show_obliterate` (Settings → Advanced) adds `obliterate` to the view's `modes` and is kept; turning it off while the window is in Obliterate leaves the mode for Default (409 while work runs). `theme` is one of `clay`, `blueprint`, `graphite`, `carbon`, `linen` and is saved to `settings.json` at once; `fast_mode` is saved too and applies from the next AI step (`llm.set_fast_mode`; a call already running keeps its speed) |
| GET | `/api/about` | | `{"version", "name", "tagline", "license", "license_name", "license_summary": [...], "release_notes", "credits": [...], "copyright", "author", "email", "github", "linkedin", "model", "components": [{"name", "license", "url"}]}` — every value from `about.py` (and `model` from `llm.DEFAULT_MODEL`); `credits` is the same as plain lines |
| GET | `/api/update` | | the `update` block |
| POST | `/api/update/check` | | the `update` block (starts a background check) |
| POST | `/api/update/download` | | the `update` block (starts the download; `progress` 0–100) |
| POST | `/api/update/install` | | the `update` block: `installing` and the app quits to install; a download that finished while work ran waits as `ready` for this (409 with the reason while work still runs) |
| POST | `/api/logs` | | opens the diagnostic logs folder |
| POST | `/api/diagnostics` | `{"reports": [{"kind": "…", …}, …]}` (at most 25; below) | `{"ok": true, "taken": n}` — the page's own reports, journalled as `page.*` events (`web/watch.py`); 400 for a body that is not JSON |
| POST | `/api/diagnostics/bundle` | | writes `spec-surgeon-diagnostics-<time>.zip` under the app's data folder and shows it in the file manager; `{"path", "name", "files", "skipped"}` (500 with `{"error"}` when it cannot be written) |
| POST | `/api/quit` | | the shell closes the window (409 while busy) |

Every error is JSON `{"error": "one sentence for the user"}` with 400/404/409/500. A route that reads a JSON body
answers 400 `The request body must be a JSON object.` when the body is not one (`null`, a list, a string, a number, a
boolean, JSON cut short, no body) — `server.json_object`, used by `/api/attach`, `/api/pick`, `/api/remove`,
`/api/send`, `/api/choose`, `/api/primary`, `/api/recipe`, `/api/occurrences`, `/api/mode`, `/api/handoff`,
`/api/put-back`, `/api/open` and `/api/settings`. The routes whose body is optional read a bad one as `{}`:
`/api/attach-folder`, `/api/export`, the tour's routes, and `/api/diagnostics` (400 for a body that is not JSON, a
non-object taken as no reports). No route answers a text/plain 500.

## The page's reports (`/api/diagnostics`)

The page (`frontend/src/lib/diagnostics.ts`) reports on itself, since nobody sees its console in the native window.
Unknown kinds and fields are passed over; text is cut to 2,000 characters and stacks to 8,000.

| kind | fields | sent |
| --- | --- | --- |
| `loaded` | `visible`, `user_agent`, `viewport` (`"1440x900"`), `reduced_motion`, `version` | once per page load |
| `unload` | | on `pagehide` (with `keepalive`) |
| `visibility` | `visible` | on every change |
| `beat` | `visible`, `long_tasks`: `{"count", "total_ms", "max_ms"}` since the last beat | every 10 s |
| `error` | `message`, `source`, `line`, `column`, `stack`, `count` | an error nothing caught; the same one at most every 30 s, with the count of repeats |
| `rejection` | `message`, `stack`, `count` | a rejected promise nothing caught; as `error` |
| `render` | `message`, `stack`, `component_stack` | a drawing failed and the error screen took over |
| `stream` | `action`, `reason` (`cut`, `failed`), `message` | a job stream closed before `done`/`error`, or failed on the way |

Any report is a sign of life: a showing page (`visible`) that sends nothing for 45 s is journalled as unresponsive.

## The job stream (`send`, `choose`, `primary`, `recipe`, `occurrences`)

`Content-Type: text/event-stream`; each frame is `data: <json>\n\n`; the last frame is `done` or `error`.

| type | payload | meaning |
| --- | --- | --- |
| `status` | `kind`, `text` | what the app is doing now: `working`, `thinking`, `reading` (documents/template), `planning`, `checking` (verification), `previewing`, `rendering`, `looking` (visual check), `creating` (copies), `recovering`, `resuming`. Named on the reply's working line; cleared by the next `text_delta` or `message`. `thinking` is sent as each model call starts: the words written before it belong to an earlier call of the same turn, which are not the reply the turn ends with (that is its last call's words), and the page keeps them among the reply's steps. `retrying` means the running attempt was abandoned and sent again from the start: the page drops the words it had from it |
| `activity` | `text` | one plain sentence of model activity, e.g. "Reading the template's header and footer" (from a tool call), named on the working line until the next frame and kept as a quiet line in the reply's log |
| `thinking_delta` | `text` | a chunk of the model's thinking summary (in the reply's log) |
| `text_delta` | `text` | a chunk of the assistant's reply as it is written: the model's plain text, then the `message` of its question or submission (read from the tool call's input as it streams, nothing else of it), a paragraph apart, as the reply joins them |
| `message` | `message` | a complete message object (assistant or app) to append; the streaming bubble is finalized with it, and the page carries the reply on from the words already shown |
| `plan` | `plan` | the plan became available (before previews render); the page drops the pages of the plan before it |
| `samples` | `samples`, `occurrences`, `warnings`, `blocking` | previews are in (`occurrences` as the view carries them; absent from an older server) |
| `results` | `results` | copies were written |
| `question` | `questions`, `choices`, `chips` | the model needs the user |
| `cost` | `cost` | the running spend |
| `done` | `state` | the job ended; `state` is the full view |
| `error` | `message`, `state`, `resend`? | the job failed; `state` is the full view after the failure (safe to retry); `resend` is the request's text when it was refused before it was recorded (no API key yet) — the composer takes it back |

Order within a job: any number of `status`/`activity`/`thinking_delta`/`text_delta`, then `message` when a reply is
complete, `plan`/`samples`/`results`/`question`/`cost` as they happen, then exactly one `done` or `error`.

A `send` or `choose` voids the plan and pages on screen (the server sets the old outcome aside as it reads the
reply), so the page takes them off the workbench when the job starts; `primary` keeps them until its view.

## Static files and dev mode

`GET /` serves the built page from `src/spec_surgeon/web/static/` (`index.html` + assets). In development
(`SPEC_SURGEON_DEV=1`) the server allows the Vite dev server's origin (CORS) and the page is opened from Vite with
`?token=…&api=http://127.0.0.1:<port>`.
