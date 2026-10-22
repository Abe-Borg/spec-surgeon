# Investigating Spec Surgeon failures

Diagnostics are enabled automatically in the desktop app. They are local files; this system does not upload telemetry. The readable reports explain document outcomes, while JSONL event journals reconstruct the work that led to those outcomes. Collect evidence from the same attempt before rerunning it into the same output folder.

## Collect the evidence

The quickest way: **About → Save a diagnostic bundle** in the window (also on the window's error screen). It writes `spec-surgeon-diagnostics-<time>.zip` under `%LOCALAPPDATA%\Spec Surgeon\diagnostics` and shows it in Explorer: the application logs folder (app log, every event journal, crash logs, session markers), `settings.json`, the current and last three conversations, and from the five newest folders of the run root their `logs/`, `records/`, reports, `compile.json` and `conversation.json` (`.log`, `.jsonl`, `.json`, `.md`, `.txt` only; `outputs/`, `failed/`, `samples/`, `pages/`, `images/`, `attachments/` and the other folders that hold documents are never read). `MANIFEST.txt` lists every file in it and every one left out, with the reason (`diagnostics.diagnostic_bundle`).

By hand: take the results folder's `DIAGNOSTICS.md`, `logs/` and `records/`, and the application logs folder (**About → Open diagnostic logs** in the window). Leave out `outputs/`, `failed/`, `samples/` and anything else holding documents or page images. Reports, transcripts, conversations, exception messages and trace summaries can still quote client text, so treat the evidence as sensitive project material.

| Evidence | Location | What it answers |
| --- | --- | --- |
| Outcome explanations | `DIAGNOSTICS.md`, `BATCH_REPORT.md`, per-file records | What failed, why the file was refused or withheld, and which checks failed |
| Run timeline | `logs/events.jsonl` | Scheduling, worker results/retries, render, delivery and reporting phases |
| Worker timeline | `logs/<output stem>.trace.events.jsonl` | The file's process, attempt, individual operations and verification checks |
| Readable traces | `logs/run.log`, `logs/<output stem>.trace.log` | Stage messages, plan summaries, error details and native fault/hang stacks |
| Conversation timeline | `<conversation work folder>/logs/events.jsonl` | Model calls, tool execution, validation and accepted/rejected submissions |
| Preview/chain/recovery timeline | Their own `logs/events.jsonl`, including nested run folders | Sample processing, chain stages, repair trials, budgets, reviews and delivery |
| Application timeline | Application logs folder, `app-events-<pid>.jsonl` and `.1`–`.3` (the `started` line of `spec-surgeon.log` names the session's file) | The window's requests (`web.request`), jobs (`web.job.*`), the page's own reports (`page.*`), the window's life (`window.*`), session state and failures before a run folder exists |
| Readable application log/crashes | `spec-surgeon.log`, `crash-*.log` | Activity, caught failures and native crashes; every window job as it starts (`Job <action> started (a typed message)`) and how it ended (`Job <action> ended: stage …; stopped yes/no; [failed (<error type>);] N model calls so far; estimated cost …; N s`); Stop; the window's open and close; page errors, a page blocked or frozen, a quiet job or an unobeyed Stop with every thread's stack; and `Spec Surgeon closed` on a clean exit. A `Job … started` with no `Job … ended` after it was cut off; a session that did not exit cleanly is described at the next start (`The previous session (pid …) ended without closing. Its last recorded events: …`) |
| Diagnostic bundle | `%LOCALAPPDATA%\Spec Surgeon\diagnostics\spec-surgeon-diagnostics-<time>.zip` | All of the above in one file, with a manifest of what is in it and what was left out |

On Windows, the application logs folder is `%LOCALAPPDATA%\Spec Surgeon\logs`. Other platforms use `spec_surgeon.settings.logs_dir()`. `SPEC_SURGEON_CONFIG_DIR` overrides application storage for isolated development/tests.

Application JSONL files rotate at 5 MiB with three backups per process. Startup keeps the five most recent inactive process journal groups plus all live ones. Separate process files avoid rotation races between concurrent processes. Run and worker journals are retained with their results without rotation; keep or remove those result folders according to the project's retention needs.

## Follow one action

Each JSON line is a complete event with `schema_version`, `event_id`, a full UTC `timestamp`, `monotonic_ns`, `pid`, `thread_id`, `thread`, and `session_id`. Scope fields link the work:

- `action_id`: the initiating window action.
- `run_id`, `batch_id`, `chain_id`, `preview_id`: processing and nested runs.
- `source`, `output_name`, `attempt`, `worker_id`: a document and its worker attempt.
- `conversation_id`, `user_turn`, `model_turn`, `model_call_id`: conversations and individual requests.
- `recovery_id`, `pattern_id`, `recovery_attempt`: repair groups and trials. Saved recovery sessions retain their
  `recovery_id` across reload, continuation and delivery; `recovery.resume` and `recovery.schedule` spans identify
  continuations and scheduling slices, including paused reasons and the current budget.
- `span_id`, `parent_span_id`: one operation and the operation that caused it.

Not every field applies to every event. A separate worker process receives the parent's correlation fields explicitly; background/render threads inherit them through a copied context. Source paths identify files across parent-side delivery events and worker journals; `attempt` distinguishes retries. Application and scoped journals can contain the same event: deduplicate by `event_id` when combining them.

Timed spans write `*.started` before running, then `*.succeeded` or `*.failed`. Terminal events include `duration_ms`, measured using a monotonic clock. **`succeeded` means the function returned normally.** Inspect `status`, `passed`, `counts`, `outcome`, and decision events to determine whether a document passed, a model submission was accepted, or anything was delivered. A normal return can describe a refused document or a failed verification check.

To answer **when**, locate the start and terminal timestamps and elapsed duration. To answer **what happened**, follow child spans and the recorded decisions. To answer **why/how**, inspect `error_type`, `error`, `exception_chain`, `traceback`, stage and check metadata, then the corresponding verification record or model transcript. Tracebacks contain code locations without frame locals.

A started span without a terminal event means the operation may still be running or was interrupted. It does not establish the cause by itself. Check the process, the last checkpoint, native crash logs, the per-file 300-second hang dumps, and any `worker.pool_broken`/`worker.retry`/`worker.died` events. A sudden power loss can still lose buffered OS writes; logs flush each event but are not a transactional audit database.

## Useful investigation paths

**A file failed:** Find `file.result` or `worker.result`, then use its `source` and `batch_id` to find the worker journal. Inspect `file.checkpoint`, operation numbers, check spans and `file.exception`. Read the matching `.record.json`/`.exception.json` for detailed independent verification evidence. Follow `delivery.identity`, `delivery.finished`, `delivery.skipped` and `guard.*` events to distinguish verification failure, source drift, altered output and filesystem failure.

**A model call stalled or failed:** Start at `model.call.started`, then `model.request.prepared` and `model.stream.opened`. The terminal event's `phase` distinguishes preflight, opening the stream, receiving its final response and usage accounting. Successful calls record serving model, stop reason, request ID and usage without copying message bodies. `model.budget.*` events explain reservations and exhausted limits. SDK-internal HTTP retries outside budgeted recovery remain inside the SDK; these events describe the application's call and stream boundaries, not every network packet.

**A repair did not help:** Follow `recovery.group`, `recovery.tool`, trial spans, `recovery.file.outcome`, review events and memory reuse/rejection events. Compare the attempt's output/check records with `recovery.json`. An accepted repair candidate is not proof of delivery; inspect the later delivery events.

**A custom transform stopped:** Follow `codegen.sandbox.prepared`, the process span, timeout/exit/missing-result events and the trial verification outcome. Generation and generated-run folders retain their own `logs/events.jsonl`, including evidence from temporary sandbox work that was later cleaned up. The generated source and detailed result remain in the existing codegen history, not the event metadata.

**A button appears to do nothing:** Find the click's `web.request` (route, status, `duration_ms`); a job route is followed by `web.job.started` and, on the job's thread, the session spans (`session.*`), approval decisions (`session.approval.*`) and model calls, then `web.job.succeeded`/`failed`. `queue_ms` on the job span is the wait before it ran. A refused gate records the reason. A route that raised has `web.request.failed` with its traceback (also in `spec-surgeon.log`). Unhandled Python exceptions in the main thread or background threads are recorded while preserving the host's exception hooks; the page's own uncaught errors are `page.error` and `page.unhandled_rejection`.

**The window froze, went blank or closed by itself:** see "The window" below.

**The diagnostics themselves fail:** Look for `diagnostics.write_failed` in the application journal and the once-per-destination stderr warning. Diagnostic I/O failure does not change document processing. A broken progress callback produces `diagnostics.progress_callback_failed` after the progress line is persisted. If every destination is unavailable and the app has no console, there may be no durable evidence; correct the storage failure first.

## The window

The page runs in WebView2 with no console anyone sees, so it reports on itself (`frontend/src/lib/diagnostics.ts`, `POST /api/diagnostics`), and `web/watch.py` journals each report and watches the page and the running job on its own thread (`spec-surgeon-watch`, a tick every 5 s).

| Event | When | In `spec-surgeon.log` |
| --- | --- | --- |
| `page.loaded` / `page.unload` | the page loads or reloads (`load`, `user_agent` with the WebView2 version, `viewport`, `reduced_motion`) / unloads | yes |
| `page.visibility` | the window is shown or hidden (minimised) | no |
| `page.error` / `page.unhandled_rejection` | an error or a rejected promise nothing caught (`message`, `where`, `stack`, `count`: the same error is sent once, then counted for 30 s) | yes, up to 100 a session |
| `page.render_failed` | a drawing failed; the error screen replaced the page (`message`, `stack`, `component_stack`) | yes |
| `page.stream_ended_early` | a job's stream closed before `done`/`error` (`reason`: `cut`) or failed (`failed`) | yes |
| `page.slow` | the heartbeat (every 10 s) carried a long task of 2 s or more (`max_ms`, `total_ms`, `count`) | yes |
| `page.unresponsive` / `page.responsive` | a showing page sent nothing for 45 s / answered again (`silent_s`) | yes, with every thread's stack |
| `web.request` / `web.request.failed` | every `/api` request but the polled ones (`/api/state`, `/api/update`, `/api/image`, `/api/health`, `/api/diagnostics`), which appear only when they fail (status ≥ 400) or take 2 s or more | slow (not the file dialog) and failed ones |
| `web.job.started` … `succeeded`/`failed` | a job's span (`action`, `queue_ms`) | started and ended lines |
| `web.job.stop_requested` / `web.job.stop_overdue` | Stop pressed / the job still runs 30 s later | yes; overdue with every thread's stack |
| `web.job.quiet` | a job with no progress (no event, log line or cost) for 5 min, then every 15 min | yes, with every thread's stack |
| `window.server.started`, `window.opened`, `window.closing`, `window.closed`, `window.shutdown.*` | the window's life; `window.shutdown` says whether a running job stopped within its 60 s wait | open, closing, and a job that would not stop (with stacks) |
| `window.watch.resumed` | a tick came 30 s late (sleep, suspend): the page's and the job's clocks restart instead of reporting a stall | no |
| `app.previous_session.unclean` | at start, for a session that never closed: its pid, version, crash log and `last_events` | `The previous session (pid …) ended without closing …` |
| `diagnostics.bundle.saved` / `.failed` | About → Save a diagnostic bundle | saved |

To read an incident: find the last `page.*` and `web.*` events before it. A page that froze shows `page.unresponsive` with the Python threads at that moment (a server thread stuck in a call means the app stopped answering the page; idle threads mean the page itself froze, and the `page.slow` that follows says for how long). A window that went blank shows `page.render_failed` with the component stack. A session that ended without `Spec Surgeon closed` is described by the next start; its own journal (`app-events-<pid>.jsonl`, kept for the five newest ended sessions) has every event to its last.

## Read a combined timeline without extra packages

Run this with one or more result/application-log folders. It ignores incomplete JSON lines and deduplicates events mirrored into multiple journals.

```python
# Save as a temporary developer script, then: python timeline.py RESULTS APP_LOGS
import json
import sys
from pathlib import Path

seen = set()
events = []
for folder in sys.argv[1:]:
    for path in Path(folder).rglob("*.jsonl*"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or "event_id" not in row:
                continue
            if row["event_id"] not in seen:
                seen.add(row["event_id"])
                events.append(row)
for row in sorted(events, key=lambda r: (r["timestamp"], r.get("monotonic_ns", 0))):
    print(row["timestamp"], row["event"], row.get("duration_ms", ""),
          row.get("source", ""), row.get("error", ""),
          "span=" + str(row.get("span_id", "")))
```

Filter `events` by a known `run_id`, `session_id`, `source`, or `model_call_id` before sorting to investigate one incident. Event names and `schema_version` are intended for developer queries; tolerate additive fields.

## Adding diagnostic coverage

Use the shared API rather than an independent logger or ad hoc serialization:

```python
from spec_surgeon import diagnostics as diag

with diag.scope(run_id=diag.new_id()):
    with diag.span("component.operation", source=str(path)) as outcome:
        diag.event("component.decision", reason="explicit reason", selected="choice")
        result = perform_work()
        outcome.update(status=result.status)
```

`diag.scope(path_to_jsonl, **context)` selects a scoped journal; without a path it inherits the current destination. A library host should call `diag.setup_app_logging(log_directory)` or establish its own journal scope to capture failures before a result folder can safely be created; the window does this automatically. `diag.context()` returns a copy suitable for passing into spawned processes. New threads require `contextvars.copy_context().run`; do not enter one copied context concurrently in multiple threads. Verifier code imports `telemetry` directly to preserve its independence from transform/application modules.

Record starts before calls that can block, explicit reasons for skips/refusals/retries, terminal outcomes and durations. Preserve the original exception and processing policy. Catching an error for diagnostics is not a reason to swallow it or weaken verification. Record IDs, counts, hashes, paths and selected options; do not pass raw prompts, documents, images, arbitrary SDK objects, full arguments or environment dictionaries.

The shared writer bounds field sizes, handles cyclic metadata and omits known payload fields. It redacts credential-shaped fields, active provider credentials, common token patterns, authorization headers, credential assignments and URL passwords. Redaction is defense in depth, not permission to log secrets. Existing model transcripts and verification records remain separate evidence with their existing content semantics.
