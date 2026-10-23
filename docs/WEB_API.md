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
  "cost": {"usd": 0.12, "text": "about $0.12", "calls": 3, "fast": false},
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
  "choices": [{"id": "c1", "label": "Only the body text", "words": [], "detail": ""}],
  "chips": ["Everything, including the headers", "Just the body"],
  "samples": [{"source": "…", "name": "21 13 13.docx", "images": ["/api/image?path=…"],
               "pages_before": 12, "pages_after": 12, "passed": true}],
  "warnings": ["…"], "blocking": false,
  "results": [{"source": "…", "output": "…", "name": "21 13 13.docx"}],
  "action": {"label": "Create edited copies", "token": "…", "kind": "approve | continue | save | none",
             "can_hold": true},
  "todo": [{"path": "C:/Specs/21 05 00.docx", "name": "21 05 00.docx", "why": "held | asked | refused | failed",
            "reason": "You marked its preview as needing more work."}],
  "focus": [],
  "working": {"generation": 0, "from_originals": true, "can_start_over": false},
  "budget": {"paused": false, "steps_per_pause": 48},
  "mode": "safe | default",
  "modes": ["safe", "default"],
  "template": {"kind": "none | prepared | reference | unusable", "typed_slots": ["ISSUED FOR PERMIT"], "reason": "",
               "body": {"roles": {"part": "PRT", "article": "ART"}, "styles": [{"id": "PRT", "name": "PRT"}],
                        "labels": {"part": "PART"}, "order": ["section_id", "part"], "page": "8.5 × 11 in portrait, margins 1 in",
                        "font": "Arial 10 pt", "problems": []}},
  "left_untouched": [{"path": "C:/Specs/21 05 00.docx", "name": "21 05 00.docx", "reason": "…"}],
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
  only; `questions` carries it only when the chat did not say it, as a recovery turn's).
- `mode` is the window's mode (docs/CONVERSATIONAL_DESKTOP.md, "Safe mode"). In `safe`, `action.kind` is only ever
  `approve` or `none` (no conversation to continue), `send` and `choose` end in `error`, and the page shows the
  buttons (`GET /api/recipes`) in place of the composer. `template` says what kind of header/footer template the
  attached template is (`recipes.template_kind`): a prepared template's `typed_slots` are the values its form asks
  for; `body` is what *Match body formatting to template* reads from it (`template_roles.detect`: the detected style
  per role, the template's paragraph styles for the form's `roles` field, the page and font in words, and `problems`
  — non-empty, the button cannot be pressed, the first one says why). `left_untouched` lists what the last Safe-mode button's run gave no copy, with the engine's reason, until the
  next button or a hand-off; it is empty while a preview waits.
- `plan` is present whenever a plan exists (stages `preview` and `question` with a partial result); `stages` is the
  number of ordered steps the engine composed internally, never a procedure the user performs. The page shows only
  `set_aside` (the **Left untouched** card); `groups`, `targets` and `stages` tell it a plan exists but are not
  displayed (the chat says what will change, the previews show it).
- `samples[].images` are URLs served by `GET /api/image?path=…&token=…` (PNG side-by-side before/after pages).
- `samples` are the pages of the plan waiting for the user (stages `preview` and `question`), less any document
  whose copy a partial save already delivered; every other stage sends none, so a finished run takes its
  pages along. The visual check's findings in `warnings`, and `blocking`, follow the same rule.
- `action.kind`: `approve` → the one button "Create edited copies" (or "I reviewed the flagged pages — create
  copies" when `blocking`), `continue` → "Continue", `save` → "Save N ready copies", `none` → no button. The button
  lives in the conversation pane, above the composer; while `busy` the composer shows "Stop now" instead.
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
- `results` lists the edited copies of the latest run; `working.generation` > 0 means the next request builds on
  those copies, and `can_start_over` shows the "Start over from the originals" chip.
- `cost.fast` is true while fast mode is in effect (`llm.fast_for(llm.DEFAULT_MODEL)`: Settings → Fast responses
  is on, the model is served fast, and Anthropic has not refused fast mode for this key): the spending pill then says
  "Fast". An older server leaves it out.
- `update.manual_result` is the answer to the last check the user asked for (About → Check for updates): "You're up
  to date — …", "No release has been published yet." or "Couldn't check for updates: …". `POST /api/update/check`
  clears it when the check starts, so About shows "Checking for updates…" until it is written again or `state`
  reports an update. An automatic check never writes it.

## Routes

| Method | Route | Body | Answer |
| --- | --- | --- | --- |
| GET | `/api/state` | | the view |
| POST | `/api/attach` | `{"paths": [..], "template": false}` | the view (400 with `{"error"}` for a refused file) |
| POST | `/api/upload` | multipart `files[]`, `template` field | copies the bytes under the config folder's `attachments/<stamp>/` and attaches them; the view |
| POST | `/api/pick` | `{"kind": "documents | template | references"}` | opens the NATIVE file dialog through the shell (pywebview); `{"paths": [...]}` — 501 when no shell |
| POST | `/api/remove` | `{"path": "...", "template": false}` | the view |
| POST | `/api/send` | `{"text": "...", "chip"?: true}` — `chip` marks one of the model's suggested replies the user tapped: their decision, never approved wording | SSE stream (below) |
| POST | `/api/choose` | `{"choice_id": "c1"}` | SSE stream |
| POST | `/api/primary` | `{"token": "...", "hold"?: ["<sample source path>", …]}` — `hold` names the previewed documents marked with the red X: set aside on the to-do list, the others run; a path that is not one of the plan's documents is refused (400 for a malformed list, the job's `error` for an unknown path) | SSE stream |
| GET | `/api/recipes` | | `{"recipes": [{"id", "label", "group", "summary", "needs": ["template" \| "picture"], "fields": [{"name", "kind": "text \| pairs \| choice \| check \| picture \| table \| slots", "label", "required", "default"?, "options"?, "help"?, "placeholder"?, "optional_choice"?}]}], "groups": [...]}` — Safe mode's buttons (`recipes.catalog()`), the same for the whole session |
| POST | `/api/recipe` | `{"recipe": "<id>", "inputs": {<field name>: value}}` — a Safe-mode button and its form; a form that cannot be used, or a plan the gate refuses, is said in one `app` message and nothing is previewed; 400 for inputs that are not an object | SSE stream |
| POST | `/api/mode` | `{"mode": "safe \| default"}` | the view; kept in settings.json; 400 for another mode |
| POST | `/api/handoff` | `{"paths": ["<left_untouched path>", …]}` — the documents go on the to-do list with the button's sentence as their request, and the mode becomes `default`; 400 for a path the last button did not leave untouched, or while a preview waits | the view |
| POST | `/api/stop` | | `{"ok": true}` — the running model call is cut off; the stream then ends with `done` |
| POST | `/api/put-back` | `{"path": "<a todo entry's path>"}` | the view (the document is off the to-do list and back among the documents the next request works on; during a pass it joins them when the pass ends; a plan on screen is void when the documents in scope change); 400 with `{"error"}` for a path not on the list, 409 while work runs |
| POST | `/api/start-over` | | the view (the working set returns to the originals; the plan is void; the to-do list is cleared) |
| POST | `/api/new-conversation` | | the view (archives the journal, starts empty) |
| POST | `/api/open` | `{"path": "..."}` | opens a result in Word / a folder with the OS (`{"ok": true}`); 404 if gone |
| GET | `/api/image` | `?path=…` | the PNG (only files under the app's run folders are served; else 404) |
| GET | `/api/settings` | | `{"key_present": true, "origin": "saved", "can_remember": true, "out_root": "…", "logs_dir": "…", "steps_per_pause": 48, "theme": "clay", "fast_mode": false, "fast": {"model": "claude-opus-5-5", "available": true, "speedup": 2.5, "price_factor": 2, "standard_usd_per_mtok": [4.0, 20.0], "fast_usd_per_mtok": [8.0, 40.0], "refused": null}}` — `fast` is what Settings says about fast mode, from `llm.PRICES` / `llm.FAST_PRICES` for the model the calls use; `refused` is the API's reason when it refused fast mode for this key |
| POST | `/api/settings` | `{"api_key": "sk-…", "remember": true}` and/or `{"theme": "linen"}` and/or `{"fast_mode": true}` | the settings; 400 with `{"error"}` (an unknown scheme, a `fast_mode` that is not a boolean). `theme` is one of `clay`, `blueprint`, `graphite`, `carbon`, `linen` and is saved to `settings.json` at once; `fast_mode` is saved too and applies from the next AI step (`llm.set_fast_mode`; a call already running keeps its speed) |
| GET | `/api/about` | | `{"version", "name", "tagline", "license", "license_name", "license_summary": [...], "release_notes", "credits": [...], "copyright", "author", "email", "github", "linkedin", "model", "components": [{"name", "license", "url"}]}` — every value from `about.py` (and `model` from `llm.DEFAULT_MODEL`); `credits` is the same as plain lines |
| GET | `/api/update` | | the `update` block |
| POST | `/api/update/check` | | the `update` block (starts a background check) |
| POST | `/api/update/download` | | the `update` block (starts the download; `progress` 0–100) |
| POST | `/api/update/install` | | the `update` block: `installing` and the app quits to install; a download that finished while work ran waits as `ready` for this (409 with the reason while work still runs) |
| POST | `/api/logs` | | opens the diagnostic logs folder |
| POST | `/api/diagnostics` | `{"reports": [{"kind": "…", …}, …]}` (at most 25; below) | `{"ok": true, "taken": n}` — the page's own reports, journalled as `page.*` events (`web/watch.py`); 400 for a body that is not JSON |
| POST | `/api/diagnostics/bundle` | | writes `spec-surgeon-diagnostics-<time>.zip` under the app's data folder and shows it in the file manager; `{"path", "name", "files", "skipped"}` (500 with `{"error"}` when it cannot be written) |
| POST | `/api/quit` | | the shell closes the window (409 while busy) |

Every error is JSON `{"error": "one sentence for the user"}` with 400/404/409/500.

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

## The job stream (`send`, `choose`, `primary`, `recipe`)

`Content-Type: text/event-stream`; each frame is `data: <json>\n\n`; the last frame is `done` or `error`.

| type | payload | meaning |
| --- | --- | --- |
| `status` | `kind`, `text` | what the app is doing now: `working`, `thinking`, `reading` (documents/template), `planning`, `checking` (verification), `previewing`, `rendering`, `looking` (visual check), `creating` (copies), `recovering`, `resuming`. Named on the reply's working line; cleared by the next `text_delta` or `message`. `thinking` is sent as each model call starts: the words written before it belong to an earlier call of the same turn, which are not the reply the turn ends with (that is its last call's words), and the page keeps them among the reply's steps. `retrying` means the running attempt was abandoned and sent again from the start: the page drops the words it had from it |
| `activity` | `text` | one plain sentence of model activity, e.g. "Reading the template's header and footer" (from a tool call), named on the working line until the next frame and kept as a quiet line in the reply's log |
| `thinking_delta` | `text` | a chunk of the model's thinking summary (in the reply's log) |
| `text_delta` | `text` | a chunk of the assistant's reply as it is written: the model's plain text, then the `message` of its question or submission (read from the tool call's input as it streams, nothing else of it), a paragraph apart, as the reply joins them |
| `message` | `message` | a complete message object (assistant or app) to append; the streaming bubble is finalized with it, and the page carries the reply on from the words already shown |
| `plan` | `plan` | the plan became available (before previews render); the page drops the pages of the plan before it |
| `samples` | `samples`, `warnings`, `blocking` | previews are in |
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
