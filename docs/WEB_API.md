# The window's API — contract between the server and the web window

Spec Surgeon's desktop window is a web page (React, built once into `src/spec_surgeon/web/static/`) hosted in a
native WebView2 window by `pywebview`, talking to a local FastAPI server (`spec_surgeon.web.server`) that wraps one
`DocumentTask`. The server binds `127.0.0.1` on a free port; every request carries the launch token
(`X-Spec-Surgeon-Token`, also accepted as `?token=` on image URLs and the SSE endpoints), which the shell hands the
page in its address, `?token=…&api=http://127.0.0.1:<port>&theme=<scheme>` (an injected
`window.SPEC_SURGEON = {token, version, api}` would win over the address). `theme` is the colour scheme kept in
`settings.json`; the page's inline script applies it before the first paint. Without the right token
every `/api` route answers 401. One job runs at a time: a second `send`, `choose`, `primary` or `attach` while
`busy` is true answers 409 `{"error": "busy"}`.

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
  "cost": {"usd": 0.12, "text": "about $0.12", "calls": 3},
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
  "todo": [{"path": "C:/Specs/21 05 00.docx", "name": "21 05 00.docx"}],
  "focus": [],
  "working": {"generation": 0, "from_originals": true, "can_start_over": false},
  "budget": {"paused": false, "steps_per_pause": 48},
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
- `plan` is present whenever a plan exists (stages `preview` and `question` with a partial result); `stages` is the
  number of ordered steps the engine composed internally — shown as a quiet "in N ordered steps" note, never as a
  procedure the user performs.
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
- `todo` lists the documents set aside this way (attached originals), waiting; while it is non-empty and no pass is
  under way, the page shows a to-do card whose button sends the reply "Work on the documents I set aside" (as a
  chip). `focus` lists the documents of that pass while it runs: they are the only documents in scope, the plan is
  made for them alone, and the pass ends when their copies are created. Documents on the to-do list are out of
  scope until the pass begins; the server also offers the same reply in `chips` outside the question stage.
- `results` lists the edited copies of the latest run; `working.generation` > 0 means the next request builds on
  those copies, and `can_start_over` shows the "Start over from the originals" chip.
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
| POST | `/api/stop` | | `{"ok": true}` — the running model call is cut off; the stream then ends with `done` |
| POST | `/api/start-over` | | the view (the working set returns to the originals; the plan is void) |
| POST | `/api/new-conversation` | | the view (archives the journal, starts empty) |
| POST | `/api/open` | `{"path": "..."}` | opens a result in Word / a folder with the OS (`{"ok": true}`); 404 if gone |
| GET | `/api/image` | `?path=…` | the PNG (only files under the app's run folders are served; else 404) |
| GET | `/api/settings` | | `{"key_present": true, "origin": "saved", "can_remember": true, "out_root": "…", "logs_dir": "…", "steps_per_pause": 48, "theme": "clay"}` |
| POST | `/api/settings` | `{"api_key": "sk-…", "remember": true}` and/or `{"theme": "linen"}` | the settings; 400 with `{"error"}` (an unknown scheme included). `theme` is one of `clay`, `blueprint`, `graphite`, `carbon`, `linen` and is saved to `settings.json` at once |
| GET | `/api/about` | | `{"version", "name", "tagline", "license", "license_name", "license_summary": [...], "release_notes", "credits": [...], "copyright", "author", "email", "github", "linkedin", "model", "components": [{"name", "license", "url"}]}` — every value from `about.py` (and `model` from `llm.DEFAULT_MODEL`); `credits` is the same as plain lines |
| GET | `/api/update` | | the `update` block |
| POST | `/api/update/check` | | the `update` block (starts a background check) |
| POST | `/api/update/download` | | the `update` block (starts the download; `progress` 0–100) |
| POST | `/api/update/install` | | the `update` block: `installing` and the app quits to install; a download that finished while work ran waits as `ready` for this (409 with the reason while work still runs) |
| POST | `/api/logs` | | opens the diagnostic logs folder |
| POST | `/api/quit` | | the shell closes the window (409 while busy) |

Every error is JSON `{"error": "one sentence for the user"}` with 400/404/409/500.

## The job stream (`send`, `choose`, `primary`)

`Content-Type: text/event-stream`; each frame is `data: <json>\n\n`; the last frame is `done` or `error`.

| type | payload | meaning |
| --- | --- | --- |
| `status` | `kind`, `text` | what the app is doing now: `working`, `thinking`, `reading` (documents/template), `planning`, `checking` (verification), `previewing`, `rendering`, `looking` (visual check), `creating` (copies), `recovering`, `resuming`. Replaces the status strip; cleared by the next `text_delta` or `message`. `retrying` means the running attempt was abandoned and sent again from the start: the page drops the words it had from it |
| `activity` | `text` | one plain sentence of model activity, e.g. "Reading the template's header and footer" (from a tool call), shown as a quiet line inside the streaming reply |
| `thinking_delta` | `text` | a chunk of the model's thinking summary (collapsible aside) |
| `text_delta` | `text` | a chunk of the assistant's reply as it is written |
| `message` | `message` | a complete message object (assistant or app) to append; the streaming bubble is finalized with it |
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
