# The trust claims ledger

Every factual claim made by the window's two trust surfaces, with the code that makes it true and how it is checked:
**Why trust it?** (Help → Why trust it, `frontend/src/components/WhyTrustIt.tsx`) and the **"I'm not convinced"
dossier** (`frontend/src/components/TrustDossier.tsx`, its runtime cards in `frontend/src/lib/trustRuntime.ts`).
Every number those surfaces quote comes from `frontend/src/lib/trustFacts.ts`, and `tests/test_trust_facts.py` pins
each of those values to the constant in the Python code.

**The rule.** The trust surfaces are a contract. A change to a behaviour they describe updates them, and this ledger,
in the same change. That covers a new user action, a new network call, a new automatic behaviour, a change to the
model or its settings, and a new or changed limit.

Written 2026-10-22 against `master` at 3d1799b (version 1.8.0), from the code rather than the docs. Fast responses
(A22, B17, T48, T49) and the worker processes and Word instances side by side (B16, T50, T51) added 2026-10-22 with
the change that made them. Re-audited the
same day against `master` at 099ee91 (C2–C6, A1 and the licence change): every model call site, network import,
thread, timer, `/api` route and outside reference in the built page checked again; what changed is marked
"(re-audit)". Updated 2026-10-22 after the 1.9.0 live run over test-set-2 (outcome planning's trigger in B8, the
documents a request leaves out in B5, per-file wording in provenance); those lines say "(test-set-2)". Updated
2026-10-23 for Safe mode (`docs/PLAN-2026-10-23-modes.md`, M1): A23–A25, the A8/B6/B9 exceptions for a Safe-mode
button, `mode` in settings.json, T40, T52 and T53; and for its "match body formatting to template" button (M3), A24's
path and T52's test. Updated 2026-10-23 after the 1.10.0 live run over test-set-3 (a sample a later step refuses no
longer starts B8); that line also says "(test-set-3)". Updated 2026-10-23 with the to-do list as the place
documents wait (the chat's `set_aside` tool in §1d, A15, A26, A27, B18, T54, T55); those lines say "(to-do list)".
Paths are relative to `src/spec_surgeon/` unless they start with `frontend/`, `tests/` or `docs/`.

**Verification column:**
- `facts` means `tests/test_trust_facts.py` pins the value to its source constant.
- `trust.test` means `frontend/tests/trust.test.ts`.
- `flows` means `frontend/tools/flows.mjs`, run in a real browser.
- `existing: <test>` names a test that already pins the behaviour.
- `manual` means read from the code for this ledger, with no test of its own.

## 1. Inventory

### 1a. Actions the user triggers (each has a runtime card)

| # | Action (the window's words) | Route / handler | AI involved |
| --- | --- | --- | --- |
| A1 | Settings → paste the key, **Save** (optional **Remember securely on this computer**) | `POST /api/settings` → `credentials.save` | none |
| A2 | Settings → **Appearance** (colour scheme) | `POST /api/settings` `{theme}` | none |
| A3 | **Add Word documents**, **Add a template (optional)**, **Add a picture or table (optional)**, or a drop anywhere in the window | `POST /api/pick` → `/api/attach`; a drop → `POST /api/upload` → `DocumentTask.attach` → `triage` | none |
| A4 | Remove an attachment (✕) | `POST /api/remove` | none |
| A5 | Type a request or answer, then **Send** or Enter | `POST /api/send` → `DocumentTask._send` | conversation |
| A6 | Pick a choice (after **Edit wording**, with your own version of its drafted wording), type its number, or tap a suggested reply | `POST /api/choose` (`wording`) or `/api/send` (`chip`) | conversation |
| A7 | Mark a preview ✓ / ✗ | page state only; ✗ is sent as `hold` with A8 | none |
| A8 | **Create edited copies** (or **I reviewed the flagged pages — create copies**) | `POST /api/primary` → `DocumentTask._primary` → `Session.run` | none to make and check the copies; recovery afterwards if a copy fails (never after a Safe-mode button: `Session.model_free`) |
| A9 | **Save N ready copies** | `POST /api/primary` during recovery | none |
| A10 | **Continue** (after a pause, a stop, a failure or a restored conversation) | `POST /api/primary` → `_resume` | conversation or recovery |
| A11 | **Stop now**, and closing the window while work runs | `POST /api/stop`; `web/shell.on_closing` | cuts the running call off |
| A12 | Open a copy, or the copies' folder | `POST /api/open` → `os.startfile` | none |
| A13 | **Start over from the originals** | `POST /api/start-over` (or the typed reply) | none |
| A14 | **New conversation** | `POST /api/new-conversation` | none |
| A15 | **Work on the documents I set aside** (to-do list: whatever put them there, each with why) | `/api/send` with `WORK_TODO` → `start_todo` | none until the next request |
| A16 | Click a page to enlarge it | page only; the image comes from `GET /api/image` | none |
| A17 | About → **Check for updates** | `POST /api/update/check` | none |
| A18 | **Download** / **Download and install** / **Restart to update** / **Not now** | `/api/update/download`, `/install`; **Not now** hides the banner in the page only (`App.tsx` `dismissedUpdate`) | none |
| A19 | About → **Open diagnostic logs** / **Save a diagnostic bundle** | `/api/logs`, `/api/diagnostics/bundle` | none |
| A20 | Click a link: in the chat, in release notes, About's links, the releases page | the system browser (`target=_blank`) | none |
| A21 | **Reload the window** (shown only when the page fails to draw) | `frontend/src/components/ErrorBoundary.tsx` | none |
| A22 | Settings → **Fast responses**: on with the second click on the button that names the price, off with one click | `POST /api/settings` `{fast_mode}` → `llm.set_fast_mode`, kept in `settings.json` | none (changes the speed of later calls) |
| A23 | Choose **Safe** or **Default** in the top bar | `POST /api/mode` → `DocumentTask.set_mode`; kept in settings.json (`Settings.mode`) | none |
| A24 | In Safe mode: a button, its short form, **Preview** | `POST /api/recipe` → `DocumentTask._recipe` → `recipes.build` → `compiler.compose_submission` (for *Match body formatting to template*, once per layout in `body_match.build_layouts`) → `Session.make_preview` (`model_free`: no visual check, no outcome planning) | none |
| A25 | **Try these in Default mode** (beside what a button left untouched), **Go to Default mode** | `POST /api/handoff` → `DocumentTask.hand_off`; `POST /api/mode` | none |
| A26 | (to-do list) **Put back** beside a document on the to-do list | `POST /api/put-back` → `DocumentTask.put_back` | none |
| A27 | (to-do list) **Set the rest aside for later**, a reply offered while files are checked again once the ready copies are saved | `/api/send` with `SET_ASIDE_REST` → `DocumentTask.set_rest_aside` | none |

Count: 27 actions.

(re-audit) The server also has `/api/update/cancel`, `/api/update/skip`, `/api/update/dismiss`, `/api/update/releases`
and `/api/quit`, behind the same launch token. The page calls none of them (`frontend/src/lib/api.ts`), so no click
reaches them; they make no network call of their own except `/api/update/releases`, which opens the releases page in
your browser (N4).

### 1b. What runs without a click (each has a runtime card or a row in one)

| # | Behaviour | Where | AI | Network |
| --- | --- | --- | --- | --- |
| B1 | At start: archive the last conversation, report a session that never closed, tidy old installer downloads | `gui.main`, `DocumentTask.archive`, `diag.CrashWatch`, `UpdateFlow.startup` | none | none |
| B2 | Automatic update check, at most once every 24 h | `UpdateFlow.auto_check`, `updater.should_auto_check` | none | GitHub |
| B3 | Every attachment checked on arrival (triage); two attached documents of one name are named in the chat with both folders (re-audit) | `triage.triage`, `triage_reference`, `DocumentTask._say_same_names` | none | none |
| B4 | After a reply, up to `AUTO_STEPS` internal steps: make the preview, prepare a custom method, carry out a recovery plan | `DocumentTask._settle`, `Session.outcome` (`auto`) | yes | Anthropic |
| B5 | (test-set-2) The preview: plan every file the request covers (a document the user left out, `leave_out`, is not planned and is named under Left untouched), apply and verify samples in memory, render them | `Session.make_preview` (`planned_files`), `preview.make_preview`, `DocumentTask._left_untouched` | none | none |
| B6 | Claude's visual check of the preview pages (not of a Safe-mode button's preview) | `Session._visual_check` → `vision.review_samples` | yes | Anthropic |
| B7 | A custom method when no built-in operation fits | `Session._generate_custom` → `codegen.generate_op` | yes | Anthropic |
| B8 | (test-set-2, test-set-3) An outcome plan and its trials when the built-in plan cannot be made: Claude calls the request unsupported, the preview's plan covers no file, a sample fails its check, or every sample is refused. A file the plan refuses while others plan does not start it, nor does a sample a later step of a chain refuses (`preview.refusals`); it waits under Left untouched beside the preview | `continuous_recovery.propose` / `execute`, `campaign_planner`, the condition in `Session.make_preview` | yes | Anthropic |
| B9 | Recovery of copies that failed their check after Create edited copies (not after a Safe-mode button) | `DocumentTask._primary` → `Session.resolve_remaining` | yes | Anthropic |
| B10 | A call that failed for a passing reason is re-sent | `llm.call`, `RETRIES` | yes | Anthropic |
| B11 | The page reports itself (load, errors, a 10 s heartbeat) to the local server; the watchdog | `frontend/src/lib/diagnostics.ts`, `web/watch.py` | none | loopback only |
| B12 | The page polls `/api/state` and `/api/update` every 2 s while a job or download runs | `frontend/src/App.tsx` | none | loopback only |
| B13 | Repair memory: past repair cases searched, reused and recorded during recovery | `recovery._investigate`, `repair_memory.CaseStore` | yes (sent with the request) | Anthropic |
| B14 | The render cache serves pages it has rendered before | `render/cache.py` | none | none |
| B15 | (re-audit) A follow-up reply Claude declines on the first call after it (`stop_reason` refusal, after the provider's own fallback) is taken back out of the history, its words no longer approved; any decline is said in plain words. A first request, or a reply declined after a tool call, stays (`_take_back` needs the reply's mark as the last message) | `Conversation._take_back`, `conversation.declined_text`, `contexts.checkpoint_history(withdrawn=)` | follows a call | none of its own |

| B16 | A batch of 3 or more files is edited and checked in the app's own worker processes, one per core up to 8, each file whole in one worker; a preview plans 12 or more files, and checks 3 or more samples, the same way; Word renders over up to 3 private instances side by side | `batch.worker_plan` (`DEFAULT_WORKER_MODE` "auto"), `batch.map_files`, `planner.PLAN_PROCESS_MIN_FILES`, `render/word_worker.run_word_batches` | none | none |
| B17 | With Fast responses on: a fast call the fast capacity cannot take (429, 529) is re-sent at once at standard speed; a key refused fast mode (a 400 or 403 naming the speed) runs at standard speed until the setting is turned on again | `llm.call` (`_fast_capacity`, `_speed_refused`, `_fast_unavailable`) | follows a call | Anthropic |
| B18 | (to-do list) After Create edited copies runs: a file its preview refused, and a run failure no recovery takes, go on the to-do list with the reason (a Create that is refused moves nothing). After a conversation turn: the documents Claude set aside at the user's word (`set_aside`) go there too | `DocumentTask._refused_now`, `_bin_failed`, `_take_set_aside` | none of its own | none |

Count: 18 automatic behaviours. B4–B10, B13, B15 and B17 run only inside work a click started. B1–B3, B11, B12, B14,
B16 and B18 involve no model.

### 1c. Network destinations

| # | Destination | Triggered by | Sends | Authenticates with | Class |
| --- | --- | --- | --- | --- | --- |
| N1 | The Anthropic API, `api.anthropic.com` by default (the `anthropic` SDK's address; `ANTHROPIC_BASE_URL` changes it) | A5, A6, A8 (recovery), A10, and B4–B10, B13 inside them | See §1d | Your API key (`x-api-key`) or an `ant auth login` profile | routine, only while work you started runs |
| N2 | `api.github.com/repos/Abe-Borg/spec-surgeon/releases/latest`, with a fallback to the `github.com/…/releases/latest` redirect | B2 (24 h), A17 | A GET with `User-Agent: SpecSurgeon/<version> (+https://github.com/Abe-Borg/spec-surgeon)`; nothing about documents | none | automatic; off with `SPEC_SURGEON_NO_UPDATE_CHECK` |
| N3 | `github.com/Abe-Borg/spec-surgeon/releases/download/<tag>/…` (the installer and `SHA256SUMS`, redirected to GitHub's file host) | A18 Download | A GET | none | only when you click |
| N4 | Any web address, opened in your own browser | A20 | Whatever your browser sends | — | only when you click |
| N5 | Any web address named by a Markdown picture (`![](https://…)`) in Claude's chat text or in GitHub's release notes | Rendering that text (react-markdown draws `<img>`) | A GET of the picture's address, from the window | — | **finding F2**: possible, not intended |
| N6 | `127.0.0.1:<random port>` (the window's own server) | every page action | the page's requests and reports | the launch token (`X-Spec-Surgeon-Token`) | loopback, never leaves the machine |

Count: 6 destinations (3 outbound by design, 1 through your browser, 1 unintended, 1 loopback).

No other network code exists. A grep of `src/spec_surgeon` for `urllib`, `requests`, `httpx`, `socket`, `http.client`,
`webbrowser` and `smtplib` finds the updater, the shell's loopback health check (proxies off), the two `webbrowser.open`
calls, and the sandboxes' block lists. `telemetry.py` says "Never sends telemetry over the network", and nothing
uploads logs or bundles.

### 1d. Model calls

**One model by default:** `claude-opus-5-5` (`llm.DEFAULT_MODEL`; the environment variable `SPEC_SURGEON_MODEL`
overrides it).
- Server-side refusal fallback: every request carries `fallbacks="default"` with beta `server-side-fallback-2026-07-01`
  (`llm.FALLBACK_BETA`). A request the model's safety filters decline is re-run on another model inside the same
  call. **The code does not name that model**; `llm.served` records which model answered.

**Settings (`llm._call`):**
- Output cap: 128,000 tokens (`resources.DEFAULT_MAX_OUTPUT_TOKENS`).
- Streaming; thinking left to the model, with its summary shown in the window (`_THINKING_DISPLAY`).
- No temperature is set.
- Speed: standard, unless the user turned Fast responses on (Settings; `settings.fast_mode`, applied by
  `web/server.WindowState` through `llm.set_fast_mode`). Then every call to a model in `llm.FAST_PRICES`
  (`claude-opus-5-5`, `claude-opus-5`, `claude-opus-4-8`) carries `speed="fast"` (the research-preview models add beta
  `fast-mode-2026-02-01`): the same model, effort and thinking, up to 2.5 times the output speed
  (`FAST_SPEEDUP`), at 2 times every price (`FAST_PRICE_FACTOR`). A fast call is sent with the SDK's own retries off,
  so a 429 or 529 comes back at once and `llm.call` re-sends it at standard speed (B17).
- Effort: medium for the conversation (`document_task.CONVERSATION_EFFORT`). Inside a budget scope the innermost
  budget's effort wins (`llm._call`), so what runs inside a turn without a budget of its own runs at medium too: the
  visual check and the custom-method writer. The outcome planner (`campaign_planner`, its own budget at the
  conversation object's effort, `resources.DEFAULT_EFFORT` = high, since `gui.Session.compile` builds the conversation
  without one) and recovery (`RECOVERY_EFFORT`, high) run at high. No call runs at max (`resources.EFFORTS`, from #77).

**Caching:**
- The system prompt is cached for an hour (`SYSTEM_CACHE`).
- The history is cached for the default five minutes (`MESSAGES_CACHE`).
- One-call checks cache nothing past the system prompt (`one_shot`).

**Conversations and their tools:**

| Conversation | Tools | Validated by |
| --- | --- | --- |
| Chat (`conversation.py`) | `CHAT_TOOLS`: describe_op, inspect_template_styles, read_context_archive, run_analysis, read_analysis_result, list_parts, read_paragraphs, read_xml_page, inspect_profile, compare_files, list_styles, sample_paragraphs, show_part_text, read_template, read_input_file, ask_user, submit_editspec, view_pages, propose_defined_terms, set_aside (to-do list: changes no file; it moves the named documents to the to-do list) | `compiler.compose_submission` (schema, attached-file checks, `untraceable` wording check, dry plan; the documents set aside are left out of it); `conversation._ask_turn` (choice kinds, invisible characters, ≤5 suggested replies); `compiler.resolve_set_aside` (labels THE BATCH lists) |
| Custom-method writer (`codegen.py`) | the inspection tools + show_xml, find_paragraphs, list_styles, show_part_text, submit_op | `codegen.check_source` (import whitelist, forbidden names and attributes), sandbox run, `hard_limits`, `verify.run_suite`. (re-audit) Under an approved outcome contract (recovery's `custom_transform`), `hard_limits` returns before its formatting-only rules, `allowed_imports` adds `GOAL_FROM`, and the copy must also pass `goals.verify_goal` → `verify/goals.py` or `verify/effects.py` (independent) |
| Visual check (`vision.py`) | submit_review | findings about pages it was not shown are dropped; advisory only |
| Outcome planner (`campaign_planner.py`) | read tools, view_pages, list_documents, inspect_goal_effect, propose_plan, need_user, unsupported | `goals.validate_contract`, template pin, invisible characters |
| Recovery (`recovery.py`) | read tools, read_template, inspect_xml, view_result, view_pages, recovery_status, trial_results, submit_repair, replan_goal, plan_repair, review_candidate, need_user, unsupported, custom_transform (with an approved outcome contract) | `recovery._candidate` (pinned assertions, `untraceable`, attached templates and inputs), trials on copies, `verify.run_suite` |
| Critic (`recovery_adaptive.py`) | review_repair | can withhold a candidate, never pass one |

**What a request carries:**
- The request, your answers and decisions.
- What the copies already carry.
- (to-do list) The names of the documents on your to-do list and why each waits (`compiler.build_messages` `todo`);
  their contents are not sent while they wait.
- File names, or trailing folders where two files share a name (`compiler.file_labels`). Recovery requests carry full
  paths.
- The full local path of an attached template and of attached pictures and tables.
- The batch's profiles: styles, section titles, header and footer text, sample paragraphs.
- The overview of every document side by side (`profile.batch_overview`, from #79): its section number and title, how
  its title shows, each displayed header and footer line, its File Properties title, and flags where a document differs
  from most; `compare_files` returns the same in full. Recovery and the outcome planner get it with the profiles.
- Whatever the read tools return: paragraph text, XML of a part, table rows.
- Page images: `view_pages`, the visual check, recovery's views.
- In recovery, up to 5 past repair cases from the local repair memory.

The model has no web search, web fetch, code execution or MCP tools: `llm.tool_uses` acts on `tool_use` blocks only,
and no server tool is configured.

**Count:** one configured model (plus the provider's unnamed refusal fallback), in six kinds of conversation.

### 1e. Data at rest

On Windows, `config_dir()` = `%APPDATA%\Spec Surgeon`, `data_dir()` = `%LOCALAPPDATA%\Spec Surgeon`.
`SPEC_SURGEON_CONFIG_DIR` moves both (`settings.py`).

| Location | Holds | Your words or document text? | Kept |
| --- | --- | --- | --- |
| Windows Credential Manager, `Spec Surgeon/ANTHROPIC_API_KEY` | the API key, if you ticked Remember | — | until you delete it (no button in the window) |
| `config_dir()\settings.json` | folders, colour scheme, whether Fast responses is on, the mode the window opens in (Safe or Default), update bookkeeping | paths only | overwritten |
| `config_dir()\conversation.json`, `conversations\*.json` | the chat, your decisions and approved wording, results, the to-do list with why each document waits, estimated spending | yes | archived at each start; **never pruned** (F5) |
| `config_dir()\attachments\<time>\` | full copies of files you dropped on the window | yes, whole files | **never deleted** (F5) |
| `config_dir()\repair-memory.sqlite3` | past repair cases: your request, candidate plans, wording and generated code | yes | 2,000 cases, oldest dropped; no clear button (F4) |
| `config_dir()\render-profiles\` | LibreOffice's own profiles | not inspected | kept |
| `data_dir()\logs\spec-surgeon.log` | the app log: job start/end, progress lines, errors | file names; error messages can quote document text | 2 MiB × 3 backups |
| `data_dir()\logs\app-events-<pid>.jsonl` | event journal: metadata of model calls (never prompts), refusal reasons | refusal reasons can quote text | 5 MiB × 3 per process, 5 past processes |
| `data_dir()\logs\crash-*.log`, `session-*.json` | crash stacks, session markers | no | newest 10 crash logs |
| `data_dir()\updates\` | downloaded installers, install logs | no | old installers deleted at start; 3 logs |
| `data_dir()\diagnostics\*.zip` | bundles you saved | yes (see F3) | **never deleted** |
| run folders under `data_dir()\runs\` (or the chosen folder) | delivered copies, failed copies, records, plans, previews, page images, conversation transcripts (`conversation.json`, `compile.json`, `chat\context\*.json` with page images), custom-method history and sandbox copies, recovery checkpoints | yes | **never deleted** (F5) |
| `runs\.render-cache\` | PDFs of rendered documents, by content hash | yes | 2 GiB, least recently used dropped |

### 1f. The deterministic parts (no model, no network)

- Triage.
- The operation library and its planning (`Op.plan`), in the app's process or its own worker processes (B16).
- Composition into ordered steps (`editspec.stage_cuts`).
- Layout routing.
- Apply and repack (`DocxPackage.repack`).
- The independent verifier V1–V10 (`verify/`).
- Rendering (Word, up to 3 private instances side by side, or LibreOffice on your machine).
- Rasterising page images.
- Approval tokens and the delivery guard (`guard.py`, `preview.approval_token`).
- The plan's sentences (`Op.describe` through `review_display.spec_changes`).
- Cost estimates (`llm.PRICES`, `llm.FAST_PRICES`).
- Logs, the diagnostic bundle.
- The update check and its integrity checks.
- The render cache.

### 1g. Provenance

Every source of content in a delivered copy:
- **Your words:** the request, typed answers, a picked choice's exact wording — as offered, or as you edited it
  before picking, when the draft you replaced is never approved (`conversation.edited_wording`; `conversation.sources`;
  `DocumentTask.word_sources`).
- **Your files:** an attached template, picture or table, and the document's own words (template fills).
- **The operations:** formatting and structure, which add zero characters.
- **Claude's plan:** which operations, where. It is never wording; `compiler.untraceable` refuses unapproved words.
- **Claude's custom method:** formatting only, with no text change allowed (`codegen.hard_limits`), outside an
  outcome plan. (re-audit) Under an outcome plan it may make that plan's changes, wording included
  (`recovery.ADAPTIVE_SYSTEM`: "The catalog's legacy formatting-only custom_transform_limits do not apply to this
  governed path"), checked by the independent outcome check.
- **A file's own section number and title** in a File Properties value built around your words (re-audit, C5:
  `set_document_properties` `from_document`; `verify/docprops.py`).
- **Your wording for one document's template slot** (test-set-2: a spec binding's `typed`, such as each title in the
  template's title case): your words like any other (`compiler.placed_words` traces each one to what you typed or
  picked), pinned to that document's bytes (`editspec._file_bindings`; `verify/template.fill_value` checks the pin
  against the bytes it read). Claude may draft them only as a choice whose wording you see and pick.
- **Claude's outcome plan in recovery:** its wording becomes approved when the app carries the plan out automatically
  (F1).
- **Remembered repair cases:** tried again, never trusted.

How each is labelled:
- The plan's sentences come from the operation code.
- Claude's assumptions are listed as assumptions.
- Claude's visual findings are listed as warnings with file and page.
- Notes that a change kept to one place leaves the same words elsewhere, or that a header/footer fill will show in
  another case than the template, are listed as warnings with the file; the plan writes them, not Claude
  (`ops/elsewhere.py`, `planner.left_elsewhere_warnings`, from #80 and C4). So is a deletion over someone's pending
  tracked changes, naming whose (re-audit, C6: `ops/deletion.pending_flag`).
- A custom method's summary is Claude's own proposal text.
- An outcome plan is shown as its effects (`review_display.effects`).

### 1h. Bounds and failure behaviour

- **No spending ceiling.** Work pauses after every 48 model calls; failed attempts count (`STEPS_PER_PAUSE`,
  `CallBudget.calls`).
- **Up to 6 internal steps per user action** (`AUTO_STEPS`).
- **Recovery:** 8 attempts per group of files (`document_task.RECOVERY_ATTEMPTS`, on every recovery path since #77,
  the outcome plan's included). A group pauses after 3 equivalent
  rejected tries (`DEFAULT_STALL_REPETITIONS`).
- **Custom method:** up to 50 submissions (`codegen.MAX_ITER`), each sandbox run 60 s (`SANDBOX_TIMEOUT`) and 2,048 MB
  (`process_limits.DEFAULT_MEMORY_MB`).
- **Analysis:** 120 s and 1,024 MB per run (`DEFAULT_ANALYSIS_SECONDS`, `DEFAULT_ANALYSIS_MEMORY_MB`).
- **Retries:** 2 re-sends after 5 s and 20 s; a provider's retry-after is honoured up to 60 s (`RETRIES`,
  `RETRY_DELAYS`, `RETRY_AFTER_CAP`).
- **Stop:**
  - A running call is cut within 0.25 s polling (`STOP_POLL_SECONDS`, `_sever`).
  - Its spending is settled from what the stream reported, plus at most 150 output tokens per second it ran
    (`STOPPED_OUTPUT_RATE`; 2.5 times that, `FAST_SPEEDUP`, for a fast call, priced fast).
  - Source files are never changed.
- **Failure:** a copy that fails its check is moved to `failed\` and never delivered (`batch.py`). A failed call
  appends nothing to the history (`conversation.py`).
- **Renderers:** Word 120 s per file (`word_worker.DEFAULT_TIMEOUT`), LibreOffice 600 s per batch
  (`libreoffice.render_pdfs`). Word: up to 3 private instances side by side (`WORD_INSTANCES`), at most one per two
  cores and one per 2 files (`word_instances`); `SPEC_SURGEON_WORD_INSTANCES` replaces the 3 (1: one instance).
- **Worker processes:** a batch of 3 or more files (`batch.PROCESS_MIN_FILES`), a preview's 12 or more plans
  (`planner.PLAN_PROCESS_MIN_FILES`) and 3 or more samples, one process per core up to 8
  (`batch.MAX_PROCESS_WORKERS`); they end with the work. A worker that stops abruptly costs its own file (recorded, as
  before) or, for a plan or a sample, the call is made again in the app's process (`batch.map_files`).
- **Closing:** closing the window stops a running job and waits up to 60 s for it (`web/shell.shutdown`); a job
  still checking or writing copies then finishes first (`gui.main`), and the process ends at once after it
  (`gui.run`, every entry point: `tests/test_gui.py`).

### 1i. Cost

- Anthropic bills the API key directly; Spec Surgeon has no account and no billing.
- The pill is an estimate from `llm.PRICES` (per model: input, output, cache read, 5-minute and 1-hour cache write). A
  model missing from the table (for example an unnamed fallback model) shows "an amount the price table cannot
  estimate".
- Fast responses: tokens a response reports as served fast (`usage.speed`) are their own row (`llm.FAST_ROW`), priced
  from `llm.FAST_PRICES`, 2 times every standard price. A fallback attempt inside a fast response is priced fast when
  its model has a fast price (an estimate that errs high). The pill says "Fast" while fast mode is in effect: the setting on,
  the model served fast, not refused for the key (`DocumentTask._cost`, `llm.fast_for`). Settings quotes the standard and fast prices from the same tables (`server.fast_offer`).
- A failed call keeps its reservation (`CallBudget._abandon`).
- A stopped call is settled from the stream plus the 150 tokens/s bound.
- Re-sent attempts are counted and billed.

### 1j. Security

- **API key:** Credential Manager (`CRED_PERSIST_LOCAL_MACHINE`) or the session only. It is put into the process
  environment and redacted from logs by value and by pattern (`telemetry.redact_text`).
- **Local server:**
  - It binds `127.0.0.1` on a free port (`web/shell.free_port`, `serve`).
  - Every `/api` request needs the launch token (`secrets.token_urlsafe(24)`, compared in constant time).
  - Pictures are served only as PNGs under the run folder or the config folder (`image_allowed`).
  - Cross-origin requests are allowed only in the developer mode (`SPEC_SURGEON_DEV`).
  - (re-audit) If no native window can open (`web/shell.native_backend_problem`: pywebview or its WebView2 backend
    missing), the page opens in the default browser at the same loopback address with `?token=` in the URL
    (`web/shell.main`), which the browser keeps in its history (F12).
- **Untrusted XML:** a part with `<!DOCTYPE` is refused; XML is read without entity resolution (`package.py`).
- **Model-written code:**
  - Both sandboxes run in a separate Python process (`-I`) with a stripped environment and an import whitelist.
  - An audit hook blocks processes, sockets and native code; writes are allowed only in the run's own folder.
  - On Linux, analysis also gets a syscall filter. On Windows there is a job object only (F6).
  - Not an operating-system security boundary (`sandbox_runner.py` says so).
- **Updates:** HTTPS only, redirects included. Only an asset named `SpecSurgeon-<version>-Setup-x64.exe`, at most
  500 MiB, whose SHA-256 is in the release's `SHA256SUMS` and in GitHub's digest. That proves integrity, not the
  publisher: the installer is unsigned (`updater.py`).
- **Links in model text** open in your browser, not in the window (`Markdown.tsx`). Pictures do not (F2).

### 1k. Known limits

All in the dossier's "What this does not do":
- PASS proves the predicted change, not that the request was right.
- Claude can misread a request.
- The visual check is advisory and can be wrong both ways.
- Only samples are rendered before approval.
- LibreOffice differs from Word.
- Untested on real files beyond the owner's runs (README "Limits").
- Out-of-reach content (shapes, charts and more).
- Weak sandbox on Windows.
- (re-audit) A custom method's prediction is written by the method itself (`codegen._trial` runs `run_suite` against
  `sb.plan`), bounded by `hard_limits` and V10's residual identity.
- Repair memory crosses projects.
- Data grows.

### 1l. Audit hooks

- **The run folder:** `records\*.record.md` and `.json` (V1–V10), `BATCH_REPORT.md`, `DIAGNOSTICS.md`,
  `exceptions\`, `plans\`.
- **The conversation's records:** `conversation.json` with `exchange` (every request as sent and every answer, with
  its served model, tokens and request id), `compile.json`, `visual_review.json`, `codegen\history.json`.
- **The logs:** `spec-surgeon.log` (one line per job with its model calls and estimated cost).
- **The bundle:** About → Save a diagnostic bundle.
- **Anthropic's console:** its usage page for the key.
- **The source:** on GitHub. Spec Surgeon's own code is MIT; the Windows app bundles PyMuPDF (AGPL-3.0, no
  commercial license held), so the app as a whole is distributed under the GNU AGPL v3, and each release page
  carries its Corresponding Source: the tag's source archives and the PyMuPDF and MuPDF source the installer
  was built with (`about.license_text`, `packaging/windows/license_notices.py`; `tests/test_about.py`,
  `tests/test_license_notices.py`).

## 2. Claims

Lv1 marks a claim in "Why trust it?"; D marks one in the dossier (section in brackets).

| # | Claim | Where | Source | Verified by |
| --- | --- | --- | --- | --- |
| T1 | The window never writes over an original: it delivers copies, each run in its own folder | Lv1, D(summary, terms, runtime A8) | `gui.Session.run` raises on `in_place`; `DocumentTask._primary` calls `s.run(False, …)`; `guard.write_back` reached only through `in_place` | existing: `tests/test_document_task.py` (`source.read_bytes() == before`), `tests/test_execution_delivery_safety.py` |
| T2 | Each change is predicted from the original before anything is changed | Lv1, D(terms) | CLAUDE.md invariant 1; `Op.plan` | existing: `tests/test_ops.py` and the op suites |
| T3 | The checker shares no code with the editing | Lv1, D(engine, terms) | `verify/` imports | existing: `tests/test_independence.py` |
| T4 | A copy that fails its check is never delivered; it goes to `failed\` | Lv1, D(runtime A8, limits) | `batch.py` | existing: `tests/test_batch*.py` |
| T5 | Claude never edits a file; it writes a plan (or, for a custom method, a program that runs in a sandbox and must pass the same checker) — formatting only, unless it carries out an outcome plan you are shown (re-audit: softened from "formatting-only") | Lv1, D(engine, blast radius) | `conversation.py`, `codegen._trial`, `codegen.run_generated`, `codegen.hard_limits` (`goal_contract`) | facts (`test_the_copy_names_the_exceptions_the_code_has`) + existing: `tests/test_codegen*.py` |
| T6 | Words placed in a document come from your request, answers, picked choices (as offered, or as you edited them) or attached files, with one exception (F1) | Lv1, D(provenance) | `compiler.untraceable`, `conversation.sources`, `conversation.edited_wording`, `continuous_recovery.approve` | existing: `tests/test_compiler*.py`, `tests/test_document_task.py::test_recovery_plan_and_preview_stay_in_same_task_without_json`, `tests/test_conversation.py::test_an_edited_wording_is_the_approved_one_and_the_draft_is_not`, `tests/test_document_task.py::test_an_edited_draft_is_the_users_wording_end_to_end` |
| T7 | A tapped suggested reply is a decision, never approved wording | Lv1, D(provenance, runtime A6) | `DocumentTask._send` (`chip`), `Conversation.reply(approve=False)` | existing: `tests/test_document_task.py` |
| T8 | Nothing is delivered without your click on Create edited copies or Save ready copies, and the click approves the exact plan and files on screen | Lv1, D(runtime A8, terms) | `DocumentTask._primary` token check; `preview.approval_token`; `Session.run` re-checks | existing: `tests/test_approval_handoff.py` |
| T9 | Every AI step runs inside work you started; one action can chain up to 6 internal steps | Lv1, D(summary, runtime) | `DocumentTask.perform`, `_settle`, `AUTO_STEPS` | facts (`autoSteps`) |
| T10 | The only request the app makes without a click is the update check, at most once every 24 h, to GitHub, carrying nothing about your documents; `SPEC_SURGEON_NO_UPDATE_CHECK` turns it off | Lv1, D(boundary, runtime B2) | `updater.should_auto_check`, `CHECK_INTERVAL`, `_request` headers; `gui.main` | facts (`updateCheckHours`, `noUpdateCheckEnv`, hosts) + existing: `tests/test_updater.py` |
| T11 | Checking a file when it arrives makes no AI call and no network call | Lv1, D(no AI, runtime A3) | `triage.py` docstring and imports | manual |
| T12 | The key lives in Windows Credential Manager under `Spec Surgeon/ANTHROPIC_API_KEY` (if you tick Remember), never in `settings.json`, and is sent only to the Anthropic API | Lv1, D(security) | `credentials.py`, `settings.Settings` fields, `llm.client` | facts (`credentialTarget`) + existing: `tests/test_settings.py` |
| T13 | The key is scrubbed from logs | D(security) | `telemetry.redact_text` | existing: `tests/test_diagnostics*.py` |
| T14 | No spending limit; a pause every 48 AI steps; Stop cuts off the running call | Lv1, D(money, runtime A11) | `STEPS_PER_PAUSE`, `llm._stop_on_cancel` | facts (`stepsPerPause`, `stopPollSeconds`) + existing: `tests/test_llm*.py` |
| T15 | The spending shown is an estimate from a built-in price table; Anthropic bills the key | Lv1, D(money) | `llm.PRICES`, `CallBudget.spent_usd` | manual |
| T16 | Model: Claude Opus 5.5 (`claude-opus-5-5`) by default (`SPEC_SURGEON_MODEL` replaces it; About and the records then name it), up to 128,000 output tokens per call, medium effort for the conversation, its visual check and its custom methods, high for outcome planning and recovery, never max | D(engine) | `llm.DEFAULT_MODEL`, `resources.DEFAULT_MAX_OUTPUT_TOKENS`, `document_task.CONVERSATION_EFFORT`/`RECOVERY_EFFORT`, `llm._call` budget override | facts |
| T17 | A declined request may be answered by another model the provider chooses; the record names it | D(engine, limits) | `llm.FALLBACK_BETA`, `fallbacks="default"`, `llm.served` | facts (presence) |
| T18 | Re-sends: 2, after 5 s and 20 s, honouring a retry-after up to 60 s | D(runtime B10) | `llm.RETRIES`, `RETRY_DELAYS`, `RETRY_AFTER_CAP` | facts |
| T19 | A stopped call is settled at most 150 output tokens per second it ran | D(money) | `llm.STOPPED_OUTPUT_RATE` | facts |
| T20 | The visual check sees up to 2 pages per sample, before and after; it is advisory; a blocking finding asks you to confirm | D(runtime B6, terms) | `vision.review_samples` (`[:2]`), `vision.py` docstring, `DocumentTask.view` | facts (`visualPagesPerSample` pinned by source text) |
| T21 | Samples: 8 representative files (or every file in a smaller batch), at least one per layout, so more when there are more layouts | D(runtime B5, limits) | `sampling.representative_samples`, `DEFAULT_SAMPLE_FILES` | facts |
| T22 | view_pages renders at most 3 pages per call, on your machine | D(blast radius) | `compiler._Batch.view_pages` (`[:3]`) | facts (source text) |
| T23 | Read tools take a file's label, never a path; only attached documents, the template and attached pictures/tables can be read | D(blast radius) | `compiler._Batch.pkg`, `inputs.read_input_file` | manual |
| T24 | Model-written analysis runs in a separate process for at most 120 s and 1,024 MB, with no file, network or process access | D(blast radius) | `investigation.run_analysis`, `analysis_runner.py`, `resources` | facts |
| T25 | A custom method is formatting only (except under an outcome plan, where it may make that plan's changes and must also pass the outcome check), runs in a separate process for at most 60 s and 2,048 MB, writes only in its own folder, and must pass the checker; up to 50 tries | D(origins, blast radius, runtime B7, B8) | `codegen.SANDBOX_TIMEOUT`, `MAX_ITER`, `process_limits.DEFAULT_MEMORY_MB`, `hard_limits`, `catalog.CUSTOM_TRANSFORM_LIMITS`, `goals.verify_goal` | facts |
| T26a | Documents and the key go only to `api.anthropic.com`, unless `ANTHROPIC_BASE_URL` (read by the SDK, never set by the window) names another address | Lv1, D(engine, boundary, runtime A5) | `llm.client` (`anthropic.Anthropic()` with no address) | facts (`apiAddressEnv`: the SDK honours it) |
| T26 | The model has no web search, no web fetch, no code execution on Anthropic's side, and no access to your file system or network | Lv1, D(blast radius) | tool lists; `llm.tool_uses` | manual |
| T27 | The window's server listens on 127.0.0.1 only and refuses a request without the launch token | D(security) | `web/shell.serve`, `server.require_token` | facts (`loopback`, `tokenHeader`) + existing: `tests/test_web_server.py` |
| T28 | Updates: HTTPS only; only an installer named for the version, at most 500 MiB, whose SHA-256 matches the release's `SHA256SUMS`; unsigned | D(security, runtime A18) | `updater.py` | facts + existing: `tests/test_updater.py` |
| T29 | Nothing is uploaded: no telemetry, no crash reports; the bundle is saved on your computer | Lv1, D(boundary) | `telemetry.py`, `server.diagnostics_bundle` | manual (grep in §1c) |
| T30 | The page loads no outside script, font or picture of its own | D(boundary) | `frontend/index.html`, `index.css`, built `web/static` | trust.test (trust components); manual (built page) |
| T31 | Run folders, conversations, attachments copies and bundles are never deleted by the app | D(security, limits) | §1e | manual |
| T32 | Log sizes: app log 2 MiB × 3, event journal 5 MiB × 3 for 5 past sessions, render cache 2 GiB, repair memory 2,000 cases, newest 10 crash logs | D(security) | `diagnostics.setup_app_logging`, `telemetry`, `render/cache.py`, `repair_memory.MAX_CASES`, `CrashWatch.KEEP` | facts |
| T33 | Recovery sends up to 5 past repair cases from earlier work with its request | D(provenance, boundary, limits) | `recovery._investigate` (`limit=5`) | facts (source text) |
| T34 | The bundle holds 3 recent conversations and 5 recent runs' text files | D(runtime A19) | `diagnostics.BUNDLE_RECENT_*` | facts |
| T35 | Recovery tries at most 8 attempts per group, pausing after 3 equivalent rejected tries | D(runtime B8, B9) | `document_task.RECOVERY_ATTEMPTS`, `DEFAULT_STALL_REPETITIONS` | facts |
| T36 | Pictures and tables over 20 MB are listed as too big, never read; a .docx is opened up to 256 MiB | D(runtime A3) | `inputs.MAX_BYTES`, `package_limits.ArchiveLimits.max_archive_bytes` | facts |
| T37 | Word renders on your machine in new invisible instances (up to 3 side by side, T51), never your open Word; else LibreOffice | D(no AI, engine) | `render/word_com.py` (DispatchEx, Visible False), `render/__init__.pick` | manual |
| T38 | The watchdog: heartbeat 10 s, a page silent 45 s, a job quiet 300 s, a Stop not obeyed in 30 s; closing waits 60 s for a job | D(runtime B11, A11) | `web/watch.py`, `web/shell.shutdown` | facts |
| T39 | Opening the app starts a clean conversation; the last one is archived on this computer and never reopened | D(runtime B1) | `gui.main`, `DocumentTask.archive` | existing: `tests/test_document_task.py` |
| T40 | Without a network, files still attach and are checked, Safe mode's buttons preview and create copies, copies open, logs and bundles work; Default mode cannot plan, because every request there goes through Claude | D(no AI) | `DocumentTask._ready_to_talk`, triage, `recipes.py` | existing: `tests/test_recipes.py` (every button with `llm.client` raising) |
| T41 | The sentences describing the plan are written by the operations' own code from the plan, not by Claude | D(provenance) | `review_display.spec_changes` → `change_sentence` (`Op.describe`) | existing: `tests/test_describe.py` |
| T42 | Help's "Why trust it?" opens the dossier, which stacks on it; one Escape closes one dialog; focus returns | — | `HelpModal.tsx`, `dialogFocus.ts`, `dialogStack.ts` | trust.test, flows |
| T43 | (re-audit) A decline is said in plain words and changes nothing; a follow-up reply declined straight away is taken back out of the conversation, while a first request or a reply declined after Claude read something stays in the history | D(runtime A5) | `Conversation._take_back` (`_reply_mark`, set only by `reply`), `declined_text` | facts (source text) + existing: `tests/test_conversation.py::test_a_reply_claude_declines_is_taken_back_and_said_in_plain_words`, `::test_a_refusal_after_tool_results_leaves_the_history_as_it_is` |
| T44 | (re-audit) Two attached documents with one name are named in the chat with both folders | D(runtime A3) | `DocumentTask._say_same_names` | facts (source text) + existing: `tests/test_document_task.py` ("are named") |
| T45 | (re-audit) A custom method's prediction is its own program's; the app bounds it and V10 holds everything else unchanged | D(limits) | `codegen._trial` (`run_suite(..., sb.plan, ...)`), `hard_limits` | facts (source text) |
| T46 | (re-audit) Without a native window the page opens in the default browser on the loopback address, the token in its URL | D(security) | `web/shell.native_backend_problem`, `main` | facts (source text) |
| T47 | (re-audit) A File Properties value can be built from each file's own section number and title around your words | D(provenance) | `ops/docprops.py` `from_document`, `verify/docprops.py` | existing: `tests/test_properties_from_document.py` |
| T48 | Fast responses is off until you turn it on in Settings, with a second click that names the price; on, the same model, effort and thinking write up to 2.5 times faster at 2 times the price on every token; the pill then says Fast | Lv1, D(engine, money, runtime A22) | `llm.set_fast_mode`, `fast_for`, `FAST_PRICES`, `FAST_SPEEDUP`, `FAST_PRICE_FACTOR`, `settings.Settings.fast_mode`, `SettingsModal.FastSection`, `DocumentTask._cost` | facts (`fastSpeedup`, `fastPriceFactor`, `test_fast_mode_is_off_until_turned_on_and_priced_from_the_table`), flows (9c) + existing: `tests/test_fast_mode.py`, `tests/test_web_server.py`, `frontend/tests/fastMode.test.ts` |
| T49 | When the fast capacity is busy a call runs at once at standard speed and price; a key refused fast mode runs at standard speed and Settings says why | D(engine, runtime B17) | `llm.call` (`_fast_capacity`, `_speed_refused`), `llm.fast_unavailable`, `server.fast_offer` | existing: `tests/test_fast_mode.py` |
| T50 | A batch of 3 or more files is edited and checked in the app's own worker processes, one per core up to 8, each file whole in one worker; a preview plans 12 or more files and checks 3 or more samples the same way; the results are those of running them one by one | D(engine, runtime B5, A8) | `batch.worker_plan`, `batch.map_files`, `planner.plan_batch`, `preview._preview_sample` | facts (`processMinFiles`, `maxProcessWorkers`, `planProcessMinFiles`) + existing: `tests/test_runtime_reliability.py` (`test_an_auto_batch_of_three_runs_in_worker_processes_with_the_same_results_as_inline`, `test_the_preview_plans_and_checks_its_samples_side_by_side_with_the_same_results`) |
| T51 | Word renders over up to 3 new invisible instances side by side, never your open Word | D(engine, runtime B5, A8) | `render/word_worker.run_word_batches`, `word_instances`, `WORD_INSTANCES` | facts (`wordInstances`, `wordInstancesEnv`) + existing: `tests/test_render_word_batch.py` |
| T52 | Safe mode makes no AI call: a button's plan is built in code from its form, its preview has no visual check and no outcome planning, and its run no recovery; a Default-mode preview approved after switching to Safe mode is created with no recovery either | Lv1, D(summary, no AI, runtime A23–A25) | `recipes.py`, `Session.model_free`, `Session.make_preview`, `DocumentTask._primary` | existing: `tests/test_recipes.py::test_each_button_previews_and_runs_to_verified_copies_without_any_ai_call` (`vision.review_samples` and `llm.client` raise), `tests/test_recipes.py::test_a_default_plan_approved_in_safe_mode_takes_no_ai_step`, `tests/test_body_match.py::test_the_button_matches_a_mixed_batch_and_leaves_out_what_it_cannot_match` |
| T53 | A Safe-mode button places only words typed in its form or read from a file you attached, and its plan passes the same gate as a conversation's | D(provenance, runtime A23–A25) | `recipes.Built.sources`, `compiler.compose_submission` | existing: `tests/test_recipes.py::test_typed_words_are_the_only_words_a_button_may_place` |
| T54 | (to-do list) Claude can set documents aside only at your word; it changes nothing in them, and no plan covers them until you work on them or put them back | D(runtime A5, blast radius) | `compiler.SET_ASIDE_TOOL`, `Conversation._take_set_aside`, `compiler.resolve_leave_out` (`set_aside`), `DocumentTask._take_set_aside` | existing: `tests/test_set_aside_bin.py` |
| T55 | (to-do list) A file the plan refused, or whose copy could not be made with nothing to check it again, goes on your to-do list with the reason once the others run; a refused Create moves nothing there; Put back and Set the rest aside change no file | D(runtime B5, B8, A8, A15) | `DocumentTask._refused_now`, `_bin_failed`, `set_rest_aside`, `put_back` | existing: `tests/test_set_aside_bin.py` |

## 3. Findings (reported, not fixed in this change)

- **F1. An outcome plan's wording becomes "approved" without a pick.**
  - When the built-in plan cannot be made, the outcome planner (Claude) proposes a contract, and the app offers it as
    the `recovery_plan` choice "Use these outcomes and continue".
  - `Session.outcome` returns `auto` for that choice and `DocumentTask._settle` takes it, which adds the contract's
    wording to the approved sources (`continuous_recovery.approve`: `c.sources.extend(choice.words)`). Trials then
    run on copies with that wording.
  - The user still sees the effects and the pages and must press Create edited copies. But CLAUDE.md invariant 6
    ("Claude's words are never the user's") and Help's "Words that go into a document are always ones you typed or
    picked" are not true on this path.
  - The copy names this exception.
- **F2. A Markdown picture in model text loads from the internet.**
  - The chat, the plan's cards and the About/update release notes render Markdown with react-markdown 9, which draws
    `![](https://…)` as an `<img>`. The page has no Content-Security-Policy.
  - A reply that contains a picture link, for example one prompted by text inside a document, would make the window
    fetch that address. The query string could carry text.
  - The copy names this.
- **F3. The diagnostic bundle can contain page images and document text.**
  - Its manifest and About's tooltip say "No document, picture or page image goes in it".
  - But `chat\context\active-*.json`, `chat\context-catalog\*.json` and recovery `conversations\` are `.json` files in
    folders the bundle does not skip (`BUNDLE_SKIP_DIRS`). They hold the lossless provider history, including the
    base64 page images from `view_pages` and tool results with document text (files up to 25 MiB).
  - The dossier says the bundle can contain them.
  - (re-audit) The About tooltip, the README, `docs/CONVERSATIONAL_DESKTOP.md` and (in this change) the release
    notes now agree. The bundle's own `MANIFEST.txt` (`diagnostics.diagnostic_bundle`: "No document, picture or page image is
    in it") still says otherwise.
- **F4. Repair memory crosses projects and has no clear button.**
  - `repair-memory.sqlite3` keeps up to 2,000 cases across sessions: the request verbatim, candidate wording,
    generated code.
  - Recovery sends up to 5 matching cases with its request to Claude, so wording from an earlier project can go out
    with a later one.
  - `CaseStore.forget` exists but nothing in the window calls it.
- **F5. Unbounded local data.**
  - Run folders (copies, page images, transcripts, sandbox copies), `conversations\` archives, `attachments\` (full
    copies of every file dropped on the window, in the Roaming profile) and saved bundles are never deleted.
- **F6. Windows sandboxes are weaker than Linux's.**
  - Model-written analysis on Linux gets a syscall filter and CPU and file-size limits. On Windows it gets a job object
    (memory, one process) plus the language and audit guards.
  - The custom-method sandbox has no syscall filter on any system; reads are kept out by the import whitelist, not
    by the audit hook (`sandbox_runner.py` says it "is still not a complete security boundary").
  - Generated transform code may call `etree.fromstring` with lxml's default parser; analysis code gets a hardened
    wrapper.
- **F7. The custom method's effort.**
  - README ("recovery and custom methods at high") and docs/CONVERSATIONAL_DESKTOP.md ("recovery and custom methods
    think at high") disagree with the code.
  - In the window a custom method prepared from the conversation runs at **medium**, because the conversation's
    budget effort overrides the call's (`llm._call`: `effort = budgets[-1].effort`). Custom methods inside recovery
    run at high.
  - Still true after #77 (which made "high" the default and refused "max"): master's README says "custom methods at
    high"; this branch's merge rewords it to the code's behaviour.
  - The new copy states the code's behaviour.
  - (re-audit) Resolved in the docs: README, Help and `docs/CONVERSATIONAL_DESKTOP.md` now say medium for a custom
    method inside a turn and high inside recovery. Only F11's docstring still disagrees.
- **F8. A custom method is written and tried without a separate click.**
  - When the conversation turns a request over to a custom method (`custom_transform` choice), the window takes it
    automatically (`AUTO_STEPS`). Claude writes Python that runs in the sandbox on copies of sample files before you
    have seen anything.
  - Delivery still needs your click. The copy says so.
- **F9. No in-process time limit on model-supplied regular expressions.**
  - read_paragraphs, sample_paragraphs and find_paragraphs run them in the app process.
- **F10. Stale or personal details in the code.**
  - `codegen.py`'s docstring names a `do --allow-codegen` flag that no longer exists.
  - `inputs.py`'s docstring describes folder discovery the code no longer does.
  - `campaign_planner.inspect_goal_effect` defaults `author="Abraham Borg"`.
- **F11. A doc comment disagrees with the code.**
  - `document_task.py`'s module docstring says "every model call writes up to the model's maximum output at high
    effort"; the conversation runs at medium. (re-audit: still there.)
- **F12. (re-audit) The browser fallback puts the launch token in the browser's history.**
  - When no native window can open, `web/shell.main` opens `http://127.0.0.1:<port>/?token=…` in the default browser.
    The token is good only for that launch and the address only on this computer, but the browser keeps it in its
    history (and a browser that syncs history sends it to that browser's account). The installed app ships WebView2's
    backend, so this needs WebView2 to be missing or broken. The dossier's security table says so.
- **F13. (re-audit) A custom method predicts itself.**
  - Not a contradiction of the code but of a reading of the copy: "every change is predicted, then proved" holds for a
    custom method only against the program's own prediction. `hard_limits` (no text change in surviving parts, no new
    member, no revision, text and styles parts only) and V10's residual identity bound it; the method's description
    in the plan is Claude's text. The dossier's limits now say so, and Level 1's custom-method sentence says the
    program predicts its own change.
