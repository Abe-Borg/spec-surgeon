"""The window's server: one ``DocumentTask`` behind a local HTTP API (docs/WEB_API.md), the page that drives it,
and the job stream — ``send``, ``choose``, ``primary`` and ``recipe`` run on a worker thread and stream what happens as
server-sent events until ``done`` or ``error``: the model's words, its activity, the plan, the previews and the
copies arrive the moment they exist. The job thread carries the request's diagnostics context (session, action,
span) with it, so a failure on the worker is journalled under the action that started it.

Nothing here decides anything about documents: every rule lives in ``DocumentTask``, ``Session`` and the engine.
The server binds 127.0.0.1 and refuses every ``/api`` request without the launch token.
"""
from __future__ import annotations

import errno
import json
import os
import queue
import secrets
import subprocess
import sys
import threading
import time
import traceback
from contextvars import copy_context
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__, about, credentials, llm, recipes, stopping
from ..ui_feedback import failed_preview_checks
from .. import diagnostics as diag
from ..document_task import LAUNCH_MODES, DocumentTask
from ..gui import Session
from ..settings import (DEFAULT_SPENDING_LIMIT_USD, THEMES, Settings, config_dir, data_dir, logs_dir,
                        save_settings, spending_check)
from ..tutorial import SCENARIOS, Tutorial
from .watch import WindowWatch

STATIC = Path(__file__).resolve().parent / "static"
TOKEN_HEADER = "X-Spec-Surgeon-Token"
# The routes the page polls or fetches by the dozen: journalled only when they fail or are slow (every other /api
# request leaves a ``web.request`` event: the page's actions, in order).
QUIET_ROUTES = frozenset({"/api/state", "/api/update", "/api/image", "/api/health", "/api/diagnostics"})
SLOW_REQUEST_MS = 2000
WAITS_ON_USER = frozenset({"/api/pick"})        # a native dialog: slow because the user takes their time
KNOWN_FAILURES = {"ValueError", "TrialsFailed", "TransformFailed", "SpecError", "FileNotFoundError", "PermissionError"}
NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>Spec Surgeon</title>
<body style="font-family:system-ui;margin:3rem;max-width:40rem"><h1>Spec Surgeon</h1>
<p>The window's page has not been built. From the project folder:</p>
<pre>cd frontend
npm install
npm run build</pre><p>Then start Spec Surgeon again.</p></body>"""


def release_notes_text(version: str = __version__) -> str:
    """The release notes shipped with this version (docs/releases/v<version>.md beside the executable or in the
    source tree), or ""."""
    name = f"v{version}.md"
    for base in (Path(sys.executable).resolve().parent / "docs" / "releases",
                 Path(__file__).resolve().parents[3] / "docs" / "releases"):
        if (base / name).is_file():
            try:
                return (base / name).read_text(encoding="utf-8")
            except OSError:
                return ""
    return ""


def open_path(path: Path) -> None:
    """Open a file with its application or a folder in the file manager."""
    path = Path(path)
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # noqa: S606 — the user asked to open their own file
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def reveal_path(path: Path) -> None:
    """Show a file selected in the file manager (its folder elsewhere)."""
    path = Path(path)
    if sys.platform.startswith("win"):
        subprocess.Popen(f'explorer /select,"{path}"')  # noqa: S602,S607 — Explorer's own syntax; our own file
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


def spent_text(cost) -> str:
    if cost is None:
        return "an amount the price table cannot estimate"
    return "less than $0.01" if cost < 0.005 else f"about ${cost:.2f}"


def job_ended_line(action: str, stage: str, *, stopped: bool, failure: str, cost: Dict[str, Any],
                   seconds: float) -> str:
    """The app log's line at the end of every job: how it ended, never what was said — the action, the stage it left
    the task in (``done``, ``preview``, ``question``, ``budget``, ``resume``…), whether the user stopped it, the
    exception's type when it failed, the model calls so far and the estimated spending (``DocumentTask._cost``)."""
    calls = cost.get("calls", 0)
    return (f"Job {action} ended: stage {stage}; stopped {'yes' if stopped else 'no'}"
            + (f"; failed ({failure})" if failure else "")
            + f"; {calls} model call{'' if calls == 1 else 's'} so far; estimated cost {cost.get('text')}"
            + f"; {seconds:.1f} s")


def job_started_line(action: str, values: Dict[str, Any]) -> str:
    """The app log's line at the start of every job, so a job that never ends still shows: the action and what kind of
    input started it, never the input itself."""
    if action == "send":
        what = "a suggested reply the user tapped" if values.get("chip") else "a typed message"
    elif action == "choose":
        what = f"choice {diag.snip(values.get('choice_id') or '?', 40)}" + \
            (", its wording edited" if values.get("wording") is not None else "")
    elif action == "primary":
        hold = values.get("hold") or []
        what = "the primary action" + (f", {len(hold)} document(s) held back" if hold else "")
    elif action == "recipe":
        what = f"the Safe-mode button {diag.snip(values.get('recipe') or '?', 40)}"
    elif action == "occurrences":
        what = f"{len(values.get('skip') or [])} occurrence(s) unticked in the preview"
    elif action == "saved_recipe":
        picks = values.get("steps") if isinstance(values.get("steps"), list) else []
        skipped = sum(1 for p in picks if isinstance(p, dict) and p.get("skip") is True)
        what = f"a saved recipe, {len(picks)} step(s)" + (f", {skipped} skipped" if skipped else "")
    elif action == "export":
        what = f"Export as {diag.snip(values.get('kind') or '?', 20)}"
    else:
        what = ""
    return f"Job {action} started" + (f" ({what})" if what else "")


def launch_mode(mode: str) -> str:
    """The mode settings.json keeps: Safe or Default; a window in Obliterate mode is kept as Default (never restored)."""
    return mode if mode in LAUNCH_MODES else "default"


class Shell:
    """What the native window offers the server: file dialogs, a yes/no question and closing. The default has none of
    them (a browser): no dialog, and a question that cannot be asked is answered no."""

    def pick_files(self, kind: str) -> Optional[List[str]]:
        return None

    def pick_folder(self, start: str = "") -> Optional[str]:
        """The folder the user chose, "" when they cancelled, None when this shell has no dialog."""
        return None

    def confirm(self, title: str, message: str) -> bool:
        return False

    def close(self) -> None:
        pass


class WindowState:
    """The task, the one job that may run, and the view the page reads."""

    def __init__(self, task: DocumentTask, settings: Settings, *, token: str, shell: Optional[Shell] = None,
                 updates: Any = None, version: str = __version__):
        self.task = task
        self.settings = settings
        self.token = token
        self.shell = shell or Shell()
        self.updates = updates
        self.version = version
        self.lock = threading.Lock()
        self.thread: Optional[threading.Thread] = None
        self.cancel: Optional[threading.Event] = None
        self.started_at = 0.0
        self.job: Optional[Dict[str, Any]] = None       # the running job's clock, for the watch (``job_status``)
        self.close_confirmed = False                     # the user answered OK to the close question over unsaved
                                                         # copies (``web.shell.on_closing``): the scratch folder may
                                                         # go with them; never set when nothing was asked
        self.watch = WindowWatch(job_status=self.job_status)
        self._last_view: Optional[Dict[str, Any]] = None
        # The guided tour (``tutorial``): while it runs, ``task`` is the practice workspace and the user's own task
        # waits in ``original_task``, untouched; a chapter's build holds ``_transition`` so no job starts over it
        self.tutorial = Tutorial()
        self.original_task: Optional[DocumentTask] = None
        self._transition = False
        llm.set_fast_mode(settings.fast_mode)          # the user's choice, for every call from the window's start
        task.set_spending_limit(settings.spending_limit_usd)   # the user's spending check, per request (0: off)
        self._environment_key = os.environ.get(credentials.ENV)
        self._credential_store_expected = credentials.available() or sys.platform == "win32"
        self.key_origin = "environment" if self._environment_key else "none"
        if self.key_origin == "none":
            saved = credentials.load()
            if saved:
                os.environ[credentials.ENV] = saved
                self.key_origin = "saved"

    def remove_app_key(self) -> None:
        """Remove only the key managed by this window and its app-specific secure-store entry. A launch-time
        environment key is restored; auth tokens and SDK profiles are owned outside this app and stay intact."""
        if self.key_origin not in {"saved", "session"}:
            raise ValueError("This window has no app-managed key to remove. Environment and SDK credentials are managed outside the app.")
        if self._credential_store_expected or credentials.available():
            try:
                credentials.remove_saved_key()
            except RuntimeError as exc:
                raise ValueError("The saved key could not be removed securely. The current key is still available.") from exc
        if self._environment_key:
            os.environ[credentials.ENV] = self._environment_key
            self.key_origin = "environment"
        else:
            os.environ.pop(credentials.ENV, None)
            self.key_origin = "none"
        self._forget_cached_credentials()

    def _tasks(self) -> List[DocumentTask]:
        """The task on screen and, during a tour, the user's own waiting behind it. A setting that reaches a task (a
        changed key, the spending check, Obliterate shown or hidden) reaches both, so the conversation comes back
        from the tour as current as Settings says it is (Codex review on #122)."""
        return [self.task] if self.original_task is None else [self.task, self.original_task]

    def _forget_cached_credentials(self) -> None:
        for task in self._tasks():
            conversation = task.session.conversation
            if conversation is not None:
                conversation.forget_cached_credentials()

    @property
    def busy(self) -> bool:
        return self._transition or (self.thread is not None and self.thread.is_alive())

    @property
    def settings_task(self) -> DocumentTask:
        """The user's own task: what settings.json remembers (its folder, its mode) and whose journal is saved. The
        tour's practice task, while one stands in, is never it."""
        return self.original_task if self.original_task is not None else self.task

    def tutorial_block(self) -> Dict[str, Any]:
        """The view's ``tutorial`` block (docs/WEB_API.md): whether the tour runs, its id and the chapter state on
        screen, and the tour version this computer finished (``Settings.tutorial_version``)."""
        return {**self.tutorial.status(), "finished_version": int(self.settings.tutorial_version)}

    # ---- the guided tour ----
    def begin_tutorial(self, request_id: Optional[str] = None) -> Dict[str, Any]:
        """Set the user's task aside and put the practice workspace in its place (``tutorial.Tutorial.begin``). A
        repeated start with the same ``request_id`` joins the running tour; another start while one runs is a
        conflict (``LookupError``); a running job or build is ``RuntimeError`` (busy)."""
        with self.lock:
            if self.busy:
                raise RuntimeError("busy")
            if self.tutorial.active and not (request_id and request_id == self.tutorial.request_id):
                raise LookupError("A tour is already running; end it before starting another.")
            self._transition = True
        try:
            keep = self.settings_task
            # the practice folder goes in this window's scratch folder (the user's own task's out_root)
            task = self.tutorial.begin(render=keep.render, scratch=Path(keep.session.out_root),
                                       spending_limit=keep.spending_limit, request_id=request_id)
            if self.original_task is None:
                self.original_task = self.task
            self.task = task
            self._last_view = None
        finally:
            self._transition = False
        return self.view()

    def tutorial_scenario(self, tutorial_id: str, kind: str) -> Dict[str, Any]:
        """The chapter state ``kind`` on screen, built afresh from the showcase."""
        with self.lock:
            if self.busy:
                raise RuntimeError("busy")
            if not self.tutorial.active or tutorial_id != self.tutorial.id:
                raise LookupError("This tour is no longer running.")
            if kind not in SCENARIOS:
                raise ValueError(f"There is no tutorial chapter state named {kind!r}.")
            self._transition = True
        try:
            keep = self.settings_task
            self.task = self.tutorial.show(tutorial_id, kind, render=keep.render, spending_limit=keep.spending_limit)
            self._last_view = None
        finally:
            self._transition = False
        return self.view()

    def restore_tutorial(self, tutorial_id: Optional[str] = None) -> Dict[str, Any]:
        """The one ending: the user's own task is on screen again, the very object that was set aside, and the
        practice folder is removed. With no tour running there is nothing to do (the call is idempotent)."""
        with self.lock:
            if self.busy:
                raise RuntimeError("busy")
            if not self.tutorial.active:
                return self.view()
            if tutorial_id is not None and tutorial_id != self.tutorial.id:
                raise LookupError("This tour is no longer running.")
            if self.original_task is not None:
                self.task, self.original_task = self.original_task, None
            self._last_view = None
            self.tutorial.end()
        return self.view()

    def job_status(self) -> Optional[Dict[str, Any]]:
        """The running job's clock for the watch (``web/watch.py``): None when no job runs."""
        job = self.job
        return dict(job) if job is not None and self.busy else None

    # ---- the view ----
    def view(self) -> Dict[str, Any]:
        if self.busy and self._last_view is not None:
            return {**self._last_view, "busy": True, "cost": self.task._cost(),
                    "update": self.updates.state() if self.updates else _no_update()}
        self._last_view = self._payload(self.task.view())
        return dict(self._last_view)

    def _payload(self, raw: Dict[str, Any], busy: Optional[bool] = None) -> Dict[str, Any]:
        task = self.task
        busy = self.busy if busy is None else busy
        stage = raw["stage"]
        label = raw["action"]
        kind = ("approve" if stage == "preview" else "continue" if stage in ("resume", "budget") else
                "save" if stage == "question" and label else "none") if label else "none"
        attachments = []
        for a in raw["attachments"]:
            attachments.append({"path": a["path"], "name": Path(a["path"]).name,
                                "kind": "template" if a["template"] else "reference" if a.get("reference") else "document",
                                "state": a["state"], "notes": list(a["notes"]), "reason": a["reason"],
                                "current": a.get("current", a["path"])})
        messages = [{"id": f"m{i}", "role": m["role"], "text": m["text"], "at": m.get("at")}
                    for i, m in enumerate(raw["messages"])]
        plan = {"groups": raw["groups"], "targets": raw["targets"], "set_aside": raw["set_aside"],
                "stages": raw.get("stages", 0)} if raw["groups"] or raw["set_aside"] else None
        aside = set(raw.get("samples_set_aside") or [])
        samples = [{"source": str(sv.source), "name": Path(sv.source).name,
                    "images": [self.image_url(p) for p in sv.images if Path(p).is_file()],
                    "pages_before": sv.pages_before, "pages_after": sv.pages_after, "passed": sv.passed,
                    "failures": failed_preview_checks(sv), "geometry": sv.geometry,
                    "text_changes": getattr(sv, "text_changes", None), "set_aside": str(sv.source) in aside}
                   for sv in raw["samples"]]
        unverified = raw.get("unverified") or {}
        exported = raw.get("exported") or {}
        states = raw.get("export_state") or {}

        def saved(output: str) -> Dict[str, Any]:
            # where Export saved this copy while that file is still there (``""`` once it is gone, or never saved);
            # whether the copy's bytes moved since (the page then opens the working copy, the newer one); and whether
            # the saved file no longer holds the bytes Export wrote (written over, truncated or edited since)
            state = states.get(output, "unsaved")
            entry = exported.get(output) or {}
            return {"saved_to": entry.get("to", "") if state in ("saved", "changed", "replaced") else "",
                    "changed_since_saved": state == "changed", "saved_file_changed": state == "replaced"}
        results = [{"source": source, "output": output, "name": Path(output).name,
                    "unverified": list(unverified.get(source, [])), **saved(output)}
                   for source, output in raw["results"].items()]
        return {"version": self.version, "busy": busy, "stage": stage, "mode": raw.get("mode", "default"),
                "modes": task.available_modes(), "template": task.template_info(),
                "left_untouched": [dict(e) for e in raw.get("left_untouched", [])],
                "connection": {"ready": task.session.claude_ready(), "origin": self.key_origin},
                "cost": task._cost(), "attachments": attachments, "messages": messages, "plan": plan,
                "questions": list(raw["questions"]), "choices": list(raw["choices"]), "chips": list(raw["chips"]),
                "samples": samples, "occurrences": [dict(d) for d in raw.get("occurrences") or []],
                "warnings": list(raw["warnings"]), "worth_knowing": [dict(n) for n in raw.get("worth_knowing") or []],
                "blocking": bool(raw["blocking"]),
                "results": results, "pdf": task.pdf_info(),
                "export": {"folder": raw.get("export_folder", ""), "unsaved": len(raw.get("unsaved") or []),
                           "copies": int(raw.get("copies") or 0)},
                "action": {"label": label, "token": raw["token"], "kind": kind,
                           "disabled_reason": raw.get("blocked_reason", ""),
                           "can_hold": kind == "approve" and bool(raw.get("can_hold")),
                           "copies": int(raw.get("copies_to_create") or 0) if kind == "approve" else 0,
                           "unpreviewed": int(raw.get("unpreviewed") or 0) if kind == "approve" else 0},
                "todo": [dict(item) for item in raw.get("todo", [])], "focus": list(raw.get("focus", [])),
                "working": raw["working"], "budget": {"paused": bool(raw["budget_paused"]),
                                                      "reason": raw.get("pause_reason", "")},
                "discard": "" if busy else raw.get("discard", ""),
                "saved_recipes": [dict(r) for r in raw.get("saved_recipes") or []],
                "recipe_blocked": raw.get("recipe_blocked", ""),
                "tutorial": self.tutorial_block(), "safe_note_hidden": bool(self.settings.safe_note_hidden),
                "update": self.updates.state() if self.updates else _no_update()}

    def image_url(self, path) -> str:
        return f"/api/image?path={_quote(str(path))}&token={self.token}"

    def image_allowed(self, path: Path) -> bool:
        roots = [Path(self.task.session.out_root).resolve(), config_dir().resolve()]
        try:
            resolved = path.resolve()
        except OSError:
            return False
        return resolved.suffix.lower() == ".png" and any(str(resolved).startswith(str(root) + os.sep) for root in roots)

    # ---- jobs ----
    def start_job(self, action: str, **values) -> queue.Queue:
        """Run ``task.perform`` on a worker thread; the queue carries the job's events, ending with ``done`` or
        ``error``. Raises RuntimeError when a job is already running."""
        with self.lock:
            if self.busy:
                raise RuntimeError("busy")
            events: queue.Queue = queue.Queue()
            self.cancel = threading.Event()
            self.started_at = time.monotonic()
            cancel = self.cancel
            task = self.task
            submitted = values.get("text", "")
            context = copy_context()            # the request's session/action/span follow the job onto its thread
            queued = time.perf_counter()
            job = {"action": action, "id": diag.new_id(), "started": time.monotonic(),
                   "last_progress": time.monotonic(), "stop_requested": None}
            self.job = job

            def moved():
                job["last_progress"] = time.monotonic()

            def log(text):
                moved()
                diag.app_log().info(str(text))

            def progress(phase, usage):
                moved()
                if phase != "started":
                    events.put({"type": "cost", "cost": task._cost()})

            def relay(event):
                moved()
                # The coordinator's events name files; the page is sent URLs (the contract's shape for
                # ``samples[].images``), exactly as the state payload sends them.
                if isinstance(event, dict) and event.get("type") == "samples":
                    event = dict(event)
                    event["samples"] = [dict(sv, images=[self.image_url(p) for p in sv.get("images", [])
                                                         if Path(p).is_file()])
                                        for sv in event.get("samples", [])]
                events.put(event)

            # Stop in Safe mode, which calls no AI, ends the job itself (``stopping``); in Default and Obliterate it cuts
            # the AI call off and a file operation already running finishes first (docs/CONVERSATIONAL_DESKTOP.md)
            whole_job = cancel if task.mode == "safe" else None

            def target():
                terminal: Dict[str, Any]
                failure = ""
                try:
                    with llm.cancellation_scope(cancel), stopping.scope(whole_job), \
                            llm.model_progress_scope(progress), \
                            diag.span("web.job", action=action, queue_ms=round((time.perf_counter() - queued) * 1000, 3)):
                        task.perform(action, log=log, events=relay, **values)
                except stopping.Stopped as e:
                    # the task has said what the stop left (``DocumentTask._stopped``); nothing it made is used
                    diag.app_log().info(f"Job {action} stopped by the user: {e}")
                    terminal = {"type": "done", "state": self._payload(task.view(), busy=False), "stopped": True}
                except Exception as e:  # noqa: BLE001 — every outcome reaches the page
                    diag.app_log().error(traceback.format_exc())
                    kind = type(e).__name__
                    if kind == "UserCancelled":
                        task.say("Stopped. No AI step runs until you continue; your progress is saved. Estimated "
                                 f"spending so far: {spent_text(task._budget.spent_usd)}.")
                        terminal = {"type": "done", "state": self._payload(task.view(), busy=False), "stopped": True}
                    else:
                        failure = kind
                        message = (str(e).replace("press Preview again", "continue to check the documents again")
                                   if kind in KNOWN_FAILURES else
                                   "The work could not finish. You can continue, or open the diagnostic logs from About.")
                        lost = bool(submitted) and not any(m["role"] == "user" and m["text"] == submitted
                                                           for m in task.messages)
                        terminal = {"type": "error", "message": message, "state": self._payload(task.view(), busy=False),
                                    "resend": submitted if lost else ""}
                else:
                    terminal = {"type": "done", "state": self._payload(task.view(), busy=False)}
                try:
                    self.remember_settings()
                    task.save()
                except Exception:  # noqa: BLE001
                    pass
                self._last_view = dict(terminal["state"])
                diag.app_log().info(job_ended_line(action, task.stage, stopped=bool(terminal.get("stopped")),
                                                   failure=failure, cost=task._cost(),
                                                   seconds=time.perf_counter() - queued))
                events.put(terminal)
            diag.app_log().info(job_started_line(action, values))
            self.thread = threading.Thread(target=context.run, args=(target,), daemon=False, name="spec-surgeon-job")
            self.thread.start()
            return events

    def stop(self) -> bool:
        if not self.busy or self.cancel is None:
            return False
        job = self.job
        if job is not None and job.get("stop_requested") is None:
            job["stop_requested"] = time.monotonic()
            running = time.monotonic() - job["started"]
            diag.event("web.job.stop_requested", action=job["action"], running_s=round(running, 1))
            diag.app_log().info(f"Stop requested for job {job['action']} (running {running:.0f} s)")
        self.cancel.set()
        return True

    def new_conversation(self) -> None:
        """An empty conversation over the same scratch folder: its journal is written over, and the copies the old
        one made stay in the scratch folder until the window closes (the page asks first when any was never saved)."""
        if self.tutorial.active:
            raise RuntimeError("End the tour first; New conversation then starts your own conversation afresh.")
        task = self.task
        session = Session(out_root=task.session.out_root, client=task.session.client,
                          visual_check=task.session.visual_check)
        self.task = DocumentTask(session, journal=task.journal, render=task.render, mode=task.mode,
                                 obliterate_allowed=task.obliterate_allowed, spending_limit=task.spending_limit,
                                 recipes=task.recipes)
        self.task.save()

    def remember_settings(self) -> None:
        """settings.json takes the user's own task's mode (``settings_task``), never the tour's practice task's while
        one stands in. The export folder is written by the export route itself."""
        keep = self.settings_task
        self.settings.mode = launch_mode(keep.mode)
        save_settings(self.settings)

    def save_settings(self) -> None:
        self.remember_settings()
        if not self.busy:
            self.settings_task.save()


NOT_AN_OBJECT = "The request body must be a JSON object."


async def json_object(request: Request) -> Dict[str, Any]:
    """The request's body as the JSON object every route reads its fields from. ``ValueError`` (with the sentence the
    page shows) for a body that is not JSON, or whose root is not an object: ``null``, a list, a string, a number, a
    boolean or JSON cut short all used to escape as a 500 the moment a route called ``.get`` on them."""
    try:
        body = await request.json()
    except ValueError:                      # json.JSONDecodeError: no body, or a body cut short
        raise ValueError(NOT_AN_OBJECT) from None
    if not isinstance(body, dict):
        raise ValueError(NOT_AN_OBJECT)
    return body


def upload_failure(name: str, exc: OSError) -> str:
    """One sentence for a dropped file whose bytes could not be saved under the scratch folder: the file, what the OS
    said in plain words (``diagnostics.os_error_hint``: Windows' 206 and ENAMETOOLONG for a name too long, a full
    disk, a denied folder) and what to do. The name is never shortened for the user: the attached file's name is the
    copy's planned name."""
    hint = diag.os_error_hint(exc)
    too_long = exc.errno == errno.ENAMETOOLONG or getattr(exc, "winerror", None) in (206, 3)
    if too_long:
        return (f"{name} could not be saved for attaching: {hint or 'the path is too long'}. Give the file a shorter "
                f"name and attach it again.")
    said = hint or (exc.strerror or str(exc)).rstrip(".") or type(exc).__name__
    return f"{name} could not be saved for attaching: {said}. Check the file and attach it again."


def _no_update() -> Dict[str, Any]:
    return {"state": "none", "version": "", "notes": "", "progress": 0, "error": "", "text": "", "manual_result": "",
            "install_log": ""}


def _quote(value: str) -> str:
    from urllib.parse import quote
    return quote(value, safe="")


def _sse(event: Dict[str, Any]) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False, default=str) + "\n\n"


def create_app(state: WindowState, *, dev: bool = False) -> FastAPI:
    """The FastAPI application over one ``WindowState``."""
    app = FastAPI(title=about.APP_NAME, version=state.version, docs_url=None, redoc_url=None, openapi_url=None)
    if dev:
        from fastapi.middleware.cors import CORSMiddleware
        app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            given = request.headers.get(TOKEN_HEADER) or request.query_params.get("token") or ""
            # compared as bytes: compare_digest refuses non-ASCII text, and a malformed token is a 401, not a crash
            if not secrets.compare_digest(given.encode("utf-8", "surrogateescape"), state.token.encode("utf-8")):
                return JSONResponse({"error": "This window's session token is missing or wrong."}, status_code=401)
        return await call_next(request)

    @app.middleware("http")
    async def breadcrumbs(request: Request, call_next):
        """Every page action leaves a ``web.request`` event — the route, its status and time, never a body — so the
        journal shows what the user did, in order; the polled routes only when they fail or are slow. A route that
        raises leaves its traceback in the app log (uvicorn would print it to a console the window does not have)."""
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            took = round((time.perf_counter() - t0) * 1000, 1)
            diag.event("web.request.failed", level="error", method=request.method, path=path, duration_ms=took,
                       **diag.error_fields(exc))
            diag.app_log().error(f"{request.method} {path} failed after {took:.0f} ms\n{traceback.format_exc()}")
            raise
        took = round((time.perf_counter() - t0) * 1000, 1)
        status = response.status_code
        slow = took >= SLOW_REQUEST_MS and path not in WAITS_ON_USER
        if path not in QUIET_ROUTES or status >= 400 or slow:
            diag.event("web.request", level="warning" if status >= 500 or slow else "info", method=request.method,
                       path=path, status=status, duration_ms=took)
        if slow:
            diag.app_log().warning(f"{request.method} {path} took {took / 1000:.1f} s to answer ({status})")
        return response

    def error(message: str, status: int = 400):
        return JSONResponse({"error": message}, status_code=status)

    def busy_error():
        return error("Work is in progress. Wait for it to finish, or stop it.", 409)

    def stream(action: str, **values):
        try:
            events = state.start_job(action, **values)
        except RuntimeError:
            return busy_error()

        def frames():
            while True:
                event = events.get()
                yield _sse(event)
                if event["type"] in ("done", "error"):
                    return
        return StreamingResponse(frames(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": state.version}

    @app.get("/api/state")
    def get_state():
        return state.view()

    @app.post("/api/attach")
    async def attach(request: Request):
        if state.busy:
            return busy_error()
        try:
            body = await json_object(request)
        except ValueError as e:
            return error(str(e))
        paths = body.get("paths") or []
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            return error("paths must list the files to attach, by path.")
        try:
            state.task.attach(paths, template=bool(body.get("template")))
        except (OSError, ValueError) as e:
            return error(str(e))
        return state.view()

    @app.post("/api/attach-folder")
    async def attach_folder(request: Request):
        """**Add a folder of documents**: the folder in the body, else the one the native folder dialog returns; its
        own .docx files are attached (``DocumentTask.attach_folder``). 501 without a dialog and a folder, 409 while
        work runs, 400 for a folder that refuses; ``{"cancelled": true}`` when the dialog was dismissed; else the
        view."""
        if state.busy:
            return busy_error()
        try:
            body = await request.json()
        except ValueError:
            body = {}
        folder = str((body or {}).get("folder") or "") if isinstance(body, dict) else ""
        if not folder:
            chosen = state.shell.pick_folder(str(Path.home()))
            if chosen is None:
                return error("This window has no native folder dialog; drop the files on the page instead.", 501)
            if not chosen:
                return {"cancelled": True}
            folder = chosen
        try:
            state.task.attach_folder(folder)
        except (OSError, ValueError) as e:
            return error(str(e))
        return state.view()

    @app.post("/api/upload")
    async def upload(files: List[UploadFile] = File(...), template: str = Form("false")):
        """A dropped file's bytes go under the scratch folder's ``attachments/<stamp>/`` and are attached from there:
        kept only while the window is open, as everything in the scratch folder is. A file the folder cannot take
        under its own name (a name too long for the path, Windows' 260 characters first of all) is a 400 naming the
        file and the remedy (``upload_failure``), never a 500 and never a shortened name; nothing is attached then."""
        if state.busy:
            return busy_error()
        folder = Path(state.task.session.out_root) / "attachments" / time.strftime("%Y%m%d-%H%M%S")
        saved: List[str] = []
        name = ""
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for f in files:
                name = Path(f.filename or "document.docx").name
                target = folder / name
                n = 1
                while target.exists():
                    target = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
                    n += 1
                target.write_bytes(await f.read())
                saved.append(str(target))
        except OSError as e:
            return error(upload_failure(name or "The file", e))
        try:
            state.task.attach(saved, template=str(template).lower() in ("1", "true", "yes"))
        except (OSError, ValueError) as e:
            return error(str(e))
        return state.view()

    @app.post("/api/pick")
    async def pick(request: Request):
        try:
            body = await json_object(request)
        except ValueError as e:
            return error(str(e))
        kind = str(body.get("kind") or "documents")
        paths = state.shell.pick_files(kind)
        if paths is None:
            return error("This window has no native file dialog; drop the files on the page instead.", 501)
        return {"paths": list(paths)}

    @app.post("/api/remove")
    async def remove(request: Request):
        if state.busy:
            return busy_error()
        try:
            body = await json_object(request)
        except ValueError as e:
            return error(str(e))
        state.task.remove(str(body.get("path") or ""), template=bool(body.get("template")))
        return state.view()

    @app.post("/api/remove-documents")
    def remove_documents():
        """**Remove all documents**: every Word document out of this conversation at once
        (``DocumentTask.remove_documents``); the template, the pictures and tables and the files themselves stay.
        409 while work runs; else the view."""
        if state.busy:
            return busy_error()
        state.task.remove_documents()
        return state.view()

    @app.post("/api/send")
    async def send(request: Request):
        try:
            body = await json_object(request)
        except ValueError as e:
            return error(str(e))
        text = str(body.get("text") or "").strip()
        if not text:
            return error("Type what you'd like to do with these documents.")
        return stream("send", text=text, chip=bool(body.get("chip")))

    @app.post("/api/choose")
    async def choose(request: Request):
        try:
            body = await json_object(request)
        except ValueError as e:
            return error(str(e))
        wording = body.get("wording")
        if wording is not None and not isinstance(wording, str):
            return error("wording must be text.")
        return stream("choose", choice_id=str(body.get("choice_id") or ""), wording=wording)

    @app.post("/api/primary")
    async def primary(request: Request):
        try:
            body = await json_object(request)
        except ValueError as e:
            return error(str(e))
        hold = body.get("hold") or []
        if not isinstance(hold, list) or not all(isinstance(p, str) for p in hold):
            return error("hold must list the documents to set aside, by path.")
        return stream("primary", token=str(body.get("token") or ""), hold=hold)

    @app.get("/api/recipes")
    def recipe_catalog():
        return {"recipes": recipes.catalog(), "groups": list(recipes.GROUPS)}

    @app.post("/api/recipe")
    async def recipe(request: Request):
        try:
            body = await json_object(request)
        except ValueError as e:
            return error(str(e))
        inputs = body.get("inputs")
        if inputs is not None and not isinstance(inputs, dict):
            return error("The button's form could not be read.")
        return stream("recipe", recipe=str(body.get("recipe") or ""), inputs=inputs or {})

    # ---- saved recipes (``saved_recipes``) ----
    @app.post("/api/saved-recipes/run")
    async def run_saved_recipe(request: Request):
        """A saved recipe as one plan, previewed (a job, as a button's ``/api/recipe``): ``id``, and ``steps`` — one
        entry per saved step, ``{"skip": bool, "inputs": {…}}``, the form a button step runs with this time; ``rev``,
        the recipe as the run form opened it (refused when its steps were edited since)."""
        try:
            body = await json_object(request)
        except ValueError as e:
            return error(str(e))
        steps = body.get("steps")
        if steps is not None and (not isinstance(steps, list) or not all(isinstance(p, dict) for p in steps)):
            return error("The recipe's steps could not be read.")
        rev = body.get("rev")
        return stream("saved_recipe", recipe=str(body.get("id") or ""), steps=steps,
                      rev=rev if isinstance(rev, str) else None)

    @app.post("/api/saved-recipes/save")
    async def save_recipe(request: Request):
        """Save as a recipe: ``name``, ``note`` and ``runs`` (the ids of What you've done so far's runs to keep), or
        ``into`` (a saved recipe's id) to add them to the end of that recipe."""
        if state.busy:
            return busy_error()
        try:
            body = await json_object(request)
            state.task.save_recipe(body.get("name"), body.get("note"), body.get("runs"), into=body.get("into") or None)
        except ValueError as e:
            return error(str(e))
        return state.view()

    @app.post("/api/saved-recipes/change")
    async def change_recipe(request: Request):
        """Manage recipes: ``action`` ``rename`` (``name``, ``note``), ``edit`` (``name``, ``note``, ``steps`` — the
        steps kept in their new order, ``{"step", "inputs"?}`` — and ``rev``, the recipe as the editor opened it),
        ``duplicate``, ``delete``, or ``inputs`` (a Safe recipe's forms, one entry per step, null to leave one as it
        is), on the recipe ``id``."""
        if state.busy:
            return busy_error()
        try:
            body = await json_object(request)
            state.task.change_recipe(str(body.get("action") or ""), str(body.get("id") or ""),
                                     name=body.get("name"), note=body.get("note"), inputs=body.get("inputs"),
                                     steps=body.get("steps"), rev=body.get("rev"))
        except ValueError as e:
            return error(str(e))
        return state.view()

    @app.post("/api/occurrences")
    async def occurrences(request: Request):
        try:
            body = await json_object(request)
        except ValueError as e:
            return error(str(e))
        keys = body.get("skip")
        if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
            return error("The unticked occurrences could not be read.")
        return stream("occurrences", skip=list(keys))

    @app.post("/api/mode")
    async def set_mode(request: Request):
        if state.busy:
            return busy_error()
        try:
            body = await json_object(request)
        except ValueError as e:
            return error(str(e))
        try:
            state.task.set_mode(str(body.get("mode") or ""), acknowledged=body.get("acknowledged") is True)
        except ValueError as e:
            return error(str(e))
        state.save_settings()
        return state.view()

    @app.post("/api/handoff")
    async def hand_off(request: Request):
        if state.busy:
            return busy_error()
        try:
            body = await json_object(request)
        except ValueError as e:
            return error(str(e))
        paths = body.get("paths")
        if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths):
            return error("Name the documents to hand over, by path.")
        try:
            state.task.hand_off(paths)
        except ValueError as e:
            return error(str(e))
        state.save_settings()
        return state.view()

    @app.post("/api/stop")
    def stop():
        return {"ok": state.stop()}

    @app.post("/api/start-over")
    def start_over():
        if state.busy:
            return busy_error()
        state.task.start_over()
        return state.view()

    @app.post("/api/discard")
    def discard():
        if state.busy:
            return busy_error()
        try:
            state.task.discard_preview()
        except ValueError as e:
            return error(str(e))
        return state.view()

    @app.post("/api/undo")
    def undo():
        if state.busy:
            return busy_error()
        try:
            state.task.undo()
        except ValueError as e:
            return error(str(e))
        return state.view()

    @app.post("/api/put-back")
    async def put_back(request: Request):
        if state.busy:
            return busy_error()
        try:
            body = await json_object(request)
        except ValueError as e:
            return error(str(e))
        try:
            state.task.put_back(str(body.get("path") or ""))
        except ValueError as exc:
            return error(str(exc))
        return state.view()

    @app.post("/api/new-conversation")
    def new_conversation():
        if state.busy:
            return busy_error()
        try:
            state.new_conversation()
        except RuntimeError as exc:
            return error(str(exc), 409)
        return state.view()

    @app.post("/api/export")
    async def export_copies(request: Request):
        """Export, the top bar's menu (``exports``): every edited copy the window lists, written into a folder — the
        one in the body, else the one the native folder dialog returns (opened where the last export went) — in the
        body's ``kind``: ``word`` (Word, Tracked Changes ON: the copies as they are; the default), ``accepted`` (Word,
        Tracked Changes OFF) or ``pdf`` (PDF, Clean). 501 without a dialog and a folder, 409 while work runs, 400 when
        there is nothing to save, the kind is unknown, no program can make a PDF or the folder refuses — each asked
        before the dialog opens where it can be; ``{"cancelled": true}`` when the dialog was dismissed. ``word`` answers
        with the view; ``accepted`` and ``pdf`` make and check the clean copies first, so they answer with the job
        stream (``text/event-stream``, as ``/api/send``) ending in ``done`` with the view."""
        if state.busy:
            return busy_error()
        if state.tutorial.active:
            return error("End the tour first; Export saves your own copies, not the practice documents.", 409)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        kind = str(body.get("kind") or "word")
        folder = str(body.get("folder") or "")
        try:
            state.task.export_ready(kind)
        except ValueError as e:
            return error(str(e))
        if not folder:
            chosen = state.shell.pick_folder(state.settings.export_folder or str(Path.home()))
            if chosen is None:
                return error("This window has no native folder dialog; name the folder to save the copies into.", 501)
            if not chosen:
                return {"cancelled": True}
            folder = chosen
        try:
            checked = str(state.task.check_export(folder, kind))
        except (OSError, ValueError) as e:
            return error(str(e))
        state.settings.export_folder = checked
        state.save_settings()
        if kind != "word":
            diag.event("export.started", kind=kind, folder=checked)
            return stream("export", kind=kind, folder=checked)
        try:
            made = state.task.export(checked, kind)
        except (OSError, ValueError) as e:
            return error(str(e))
        diag.event("export.saved", kind=kind, folder=made["folder"], saved=made["saved"], already=made["already"])
        diag.app_log().info(f"Export: {made['saved']} copies saved to {made['folder']} ({made['already']} already there)")
        return state.view()

    # ---- the guided tour (docs/WEB_API.md, "The guided tour"; ``tutorial.py``) ----
    async def tour_body(request: Request) -> Dict[str, Any]:
        try:
            body = await request.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    @app.get("/api/tutorial/status")
    def tutorial_status():
        return state.tutorial_block()

    @app.post("/api/tutorial/start")
    async def tutorial_start(request: Request):
        body = await tour_body(request)
        request_id = str(body.get("request_id") or "") or None
        try:
            return state.begin_tutorial(request_id)
        except RuntimeError:
            return busy_error()
        except LookupError as exc:
            return error(str(exc), 409)
        except (OSError, ValueError) as exc:
            diag.app_log().error(f"The tour could not start\n{traceback.format_exc()}")
            return error(f"The tour could not start: {exc}", 500)

    @app.post("/api/tutorial/scenario")
    async def tutorial_scenario(request: Request):
        body = await tour_body(request)
        tutorial_id = str(body.get("tutorial_id") or "")
        kind = str(body.get("kind") or "")
        try:
            return state.tutorial_scenario(tutorial_id, kind)
        except RuntimeError:
            return busy_error()
        except LookupError as exc:
            return error(str(exc), 409)
        except ValueError as exc:
            return error(str(exc))
        except Exception as exc:  # noqa: BLE001 — the chapter's build failed: the tour stays, the page may retry
            diag.app_log().error(f"The tour's chapter {kind!r} could not be prepared\n{traceback.format_exc()}")
            return error(f"The chapter could not be prepared: {exc}", 500)

    @app.post("/api/tutorial/restore")
    async def tutorial_restore(request: Request):
        body = await tour_body(request)
        tutorial_id = str(body.get("tutorial_id") or "") or None
        try:
            return state.restore_tutorial(tutorial_id)
        except RuntimeError:
            return busy_error()
        except LookupError as exc:
            return error(str(exc), 409)

    @app.post("/api/open")
    async def open_result(request: Request):
        # The page sends the file Export saved when it still holds the copy's current bytes (``saved_to`` without
        # ``changed_since_saved`` or ``saved_file_changed``), else the working copy in the scratch folder: the copy
        # the list names, with the newer content when it was edited there.
        try:
            body = await json_object(request)
        except ValueError as e:
            return error(str(e))
        path = Path(str(body.get("path") or ""))
        if not path.exists():
            return error("That copy was moved or deleted. Continue the conversation to create a new result.", 404)
        open_path(path)
        return {"ok": True}

    @app.get("/api/image")
    def image(path: str):
        target = Path(path)
        if not state.image_allowed(target) or not target.is_file():
            return error("No such picture.", 404)
        return FileResponse(str(target), media_type="image/png")

    @app.get("/api/settings")
    def get_settings():
        return {"key_present": state.task.session.claude_ready(), "origin": state.key_origin,
                "can_remove_key": state.key_origin in {"saved", "session"},
                "can_remember": credentials.available(), "export_folder": state.settings.export_folder,
                "logs_dir": str(logs_dir()), "theme": state.settings.theme,
                "spending_limit_usd": state.task.spending_limit, "spending_default_usd": DEFAULT_SPENDING_LIMIT_USD,
                "fast_mode": state.settings.fast_mode, "fast": fast_offer(),
                "show_obliterate": state.settings.show_obliterate,
                "tutorial_version": state.settings.tutorial_version,
                "safe_note_hidden": state.settings.safe_note_hidden}

    @app.post("/api/settings")
    async def set_settings(request: Request):
        try:
            body = await json_object(request)
        except ValueError as e:
            return error(str(e))
        if "theme" in body:
            if body["theme"] not in THEMES:
                return error("There is no such colour scheme.")
            state.settings.theme = body["theme"]
            state.save_settings()
        if "show_obliterate" in body:
            if not isinstance(body["show_obliterate"], bool):
                return error("Show Obliterate mode is on or off.")
            if state.busy and not body["show_obliterate"] and any(t.mode == "obliterate" for t in state._tasks()):
                return busy_error()
            state.settings.show_obliterate = body["show_obliterate"]
            for task in state._tasks():
                task.obliterate_allowed = body["show_obliterate"]
                if not body["show_obliterate"] and task.mode == "obliterate":
                    task.set_mode("default")        # hidden again: the window leaves it
            state.save_settings()
        if "spending_limit_usd" in body:
            # The next request uses it; one paused at the check goes on at the new limit when continued.
            try:
                limit = spending_check(body["spending_limit_usd"])
            except ValueError as exc:
                return error(str(exc))
            state.settings.spending_limit_usd = 0.0 if limit is None else limit
            for task in state._tasks():
                task.set_spending_limit(limit)
            state.save_settings()
        if "tutorial_version" in body:
            # the guided tour's version this computer finished: the page's number, kept for the next start
            version = body["tutorial_version"]
            if isinstance(version, bool) or not isinstance(version, int) or version < 0:
                return error("The finished tour's version is a whole number.")
            state.settings.tutorial_version = version
            state.save_settings()
        if "safe_note_hidden" in body:
            # the note about Safe mode above its buttons: folded to one line, or open
            if not isinstance(body["safe_note_hidden"], bool):
                return error("The Safe mode note is hidden or shown.")
            state.settings.safe_note_hidden = body["safe_note_hidden"]
            state.save_settings()
        if "fast_mode" in body:
            if not isinstance(body["fast_mode"], bool):
                return error("Fast responses is on or off.")
            # The next AI step uses it; one already running finishes at the speed it started with.
            state.settings.fast_mode = body["fast_mode"]
            llm.set_fast_mode(body["fast_mode"])
            state.save_settings()
        key = str(body.get("api_key") or "").strip()
        remember = bool(body.get("remember"))
        if key:
            if state.busy:
                return busy_error()
            os.environ[credentials.ENV] = key
            state.key_origin = "session"
            state._forget_cached_credentials()
            if remember and credentials.available():
                try:
                    credentials.save(key)
                    state.key_origin = "saved"
                    state._credential_store_expected = True
                except Exception:  # noqa: BLE001
                    return error("The key is set for this session but could not be saved securely.")
        return get_settings()

    @app.post("/api/settings/remove-key")
    def remove_key():
        if state.busy:
            return busy_error()
        try:
            state.remove_app_key()
        except ValueError as exc:
            return error(str(exc))
        return get_settings()

    def fast_offer() -> Dict[str, Any]:
        """What Settings says about fast mode, from the code's own tables: the model the calls use, whether Anthropic
        serves it fast, the standard and fast prices per million tokens (input, output), the speed-up, and why the API
        refused fast mode for this key, if it did."""
        model = llm.DEFAULT_MODEL
        standard, fast = llm.PRICES.get(model), llm.FAST_PRICES.get(model)
        return {"model": model, "available": fast is not None, "speedup": llm.FAST_SPEEDUP,
                "price_factor": llm.FAST_PRICE_FACTOR,
                "standard_usd_per_mtok": list(standard[:2]) if standard else None,
                "fast_usd_per_mtok": list(fast[:2]) if fast else None,
                "refused": llm.fast_unavailable()}

    @app.get("/api/about")
    def about_info():
        return {"version": state.version, "name": about.APP_NAME, "tagline": about.TAGLINE,
                "license": about.license_text(*about.pdf_versions()), "release_notes": release_notes_text(state.version),
                "credits": about.about_lines(state.version), "github": about.GITHUB_URL,
                "author": about.AUTHOR, "email": about.EMAIL, "linkedin": about.LINKEDIN_URL,
                "copyright": about.COPYRIGHT_LINE, "license_name": about.APP_LICENSE,
                "license_summary": list(about.LICENSE_SUMMARY), "model": llm.DEFAULT_MODEL,
                "components": [{"name": name, "license": lic, "url": url} for name, lic, url in about.THIRD_PARTY]}

    @app.get("/api/update")
    def get_update():
        return state.updates.state() if state.updates else _no_update()

    @app.post("/api/update/check")
    def update_check():
        if state.updates:
            state.updates.check(manual=True)
        return get_update()

    @app.post("/api/update/download")
    def update_download():
        if not state.updates:
            return error("Updates are not available in this copy.", 501)
        why = state.updates.install()
        if why:
            return error(why, 409)
        return get_update()

    @app.post("/api/update/cancel")
    def update_cancel():
        if state.updates:
            state.updates.cancel_download()
        return get_update()

    @app.post("/api/update/skip")
    def update_skip():
        if state.updates:
            state.updates.skip()
        return get_update()

    @app.post("/api/update/dismiss")
    def update_dismiss():
        if state.updates:
            state.updates.dismiss()
        return get_update()

    @app.post("/api/update/install")
    def update_install():
        if state.updates and state.updates.ready is not None:      # downloaded while work ran: Restart to update
            why = state.updates.install_ready()
            return error(why, 409) if why else get_update()
        return update_download()

    @app.post("/api/update/releases")
    def update_releases():
        import webbrowser
        url = state.updates.releases_url() if state.updates else about.GITHUB_URL + "/releases"
        webbrowser.open(url)
        return {"ok": True}

    @app.post("/api/logs")
    def open_logs():
        logs_dir().mkdir(parents=True, exist_ok=True)
        open_path(logs_dir())
        return {"ok": True}

    @app.post("/api/diagnostics")
    async def page_diagnostics(request: Request):
        try:
            body = await request.json()
        except ValueError:
            return error("A diagnostics report is a JSON object with its reports.")
        taken = state.watch.page_reports(body.get("reports") if isinstance(body, dict) else None)
        return {"ok": True, "taken": taken}

    @app.post("/api/diagnostics/bundle")
    def diagnostics_bundle():
        target = data_dir() / "diagnostics" / f"spec-surgeon-diagnostics-{time.strftime('%Y%m%d-%H%M%S')}.zip"
        roots = [Path(state.task.session.out_root)]      # the scratch folder: this window's session folders
        try:
            made = diag.diagnostic_bundle(target, logs=logs_dir(), config=config_dir(), run_roots=roots,
                                          journal=state.task.journal,
                                          header=[f"Spec Surgeon {state.version}",
                                                  diag.environment_line(diag.environment()),
                                                  f"Job running: {state.job['action'] if state.busy and state.job else 'none'}"])
        except OSError as e:
            diag.event("diagnostics.bundle.failed", level="error", **diag.error_fields(e))
            return error(f"The diagnostic bundle could not be written: {e}", 500)
        diag.event("diagnostics.bundle.saved", path=str(made["path"]), files=len(made["files"]),
                   skipped=len(made["skipped"]), bytes=made["bytes"])
        diag.app_log().info(f"Diagnostic bundle saved: {made['path']} ({len(made['files'])} files)")
        try:
            reveal_path(made["path"])
        except OSError:
            pass
        return {"path": str(made["path"]), "name": made["path"].name, "files": len(made["files"]),
                "skipped": len(made["skipped"])}

    @app.post("/api/quit")
    def quit_window():
        if state.busy:
            return error("Please keep this window open while the current work finishes.", 409)
        state.save_settings()
        state.shell.close()
        return {"ok": True}

    if (STATIC / "index.html").is_file():
        app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")
    else:
        @app.get("/")
        def not_built():
            return HTMLResponse(NOT_BUILT)
    return app


def new_token() -> str:
    return secrets.token_urlsafe(24)
