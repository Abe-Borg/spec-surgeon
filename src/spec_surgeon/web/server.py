"""The window's server: one ``DocumentTask`` behind a local HTTP API (docs/WEB_API.md), the page that drives it,
and the job stream — ``send``, ``choose``, ``primary`` and ``recipe`` run on a worker thread and stream what happens as
server-sent events until ``done`` or ``error``: the model's words, its activity, the plan, the previews and the
copies arrive the moment they exist. The job thread carries the request's diagnostics context (session, action,
span) with it, so a failure on the worker is journalled under the action that started it.

Nothing here decides anything about documents: every rule lives in ``DocumentTask``, ``Session`` and the engine.
The server binds 127.0.0.1 and refuses every ``/api`` request without the launch token.
"""
from __future__ import annotations

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

from .. import __version__, about, credentials, llm, recipes
from .. import diagnostics as diag
from ..document_task import MODES, STEPS_PER_PAUSE, DocumentTask
from ..gui import Session
from ..settings import THEMES, Settings, config_dir, data_dir, default_run_root, logs_dir, save_settings
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
        what = f"choice {diag.snip(values.get('choice_id') or '?', 40)}"
    elif action == "primary":
        hold = values.get("hold") or []
        what = "the primary action" + (f", {len(hold)} document(s) held back" if hold else "")
    elif action == "recipe":
        what = f"the Safe-mode button {diag.snip(values.get('recipe') or '?', 40)}"
    else:
        what = ""
    return f"Job {action} started" + (f" ({what})" if what else "")


class Shell:
    """What the native window offers the server: file dialogs and closing. The default has neither (a browser)."""

    def pick_files(self, kind: str) -> Optional[List[str]]:
        return None

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
        self.watch = WindowWatch(job_status=self.job_status)
        self._last_view: Optional[Dict[str, Any]] = None
        llm.set_fast_mode(settings.fast_mode)          # the user's choice, for every call from the window's start
        self.key_origin = "environment" if os.environ.get(credentials.ENV) else "none"
        if self.key_origin == "none":
            saved = credentials.load()
            if saved:
                os.environ[credentials.ENV] = saved
                self.key_origin = "saved"

    @property
    def busy(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

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
        samples = [{"source": str(sv.source), "name": Path(sv.source).name,
                    "images": [self.image_url(p) for p in sv.images if Path(p).is_file()],
                    "pages_before": sv.pages_before, "pages_after": sv.pages_after, "passed": sv.passed}
                   for sv in raw["samples"]]
        results = [{"source": source, "output": output, "name": Path(output).name}
                   for source, output in raw["results"].items()]
        return {"version": self.version, "busy": busy, "stage": stage, "mode": raw.get("mode", "default"),
                "modes": list(MODES), "template": task.template_info(),
                "left_untouched": [dict(e) for e in raw.get("left_untouched", [])],
                "connection": {"ready": task.session.claude_ready(), "origin": self.key_origin},
                "cost": task._cost(), "attachments": attachments, "messages": messages, "plan": plan,
                "questions": list(raw["questions"]), "choices": list(raw["choices"]), "chips": list(raw["chips"]),
                "samples": samples, "warnings": list(raw["warnings"]), "blocking": bool(raw["blocking"]),
                "results": results,
                "action": {"label": label, "token": raw["token"], "kind": kind,
                           "can_hold": kind == "approve" and bool(raw.get("can_hold"))},
                "todo": [dict(item) for item in raw.get("todo", [])], "focus": list(raw.get("focus", [])),
                "working": raw["working"], "budget": {"paused": bool(raw["budget_paused"]),
                                                      "steps_per_pause": STEPS_PER_PAUSE},
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

            def target():
                terminal: Dict[str, Any]
                failure = ""
                try:
                    with llm.cancellation_scope(cancel), llm.model_progress_scope(progress), \
                            diag.span("web.job", action=action, queue_ms=round((time.perf_counter() - queued) * 1000, 3)):
                        task.perform(action, log=log, events=relay, **values)
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
                    self.settings.out_root = str(task.session.out_root)
                    self.settings.mode = task.mode
                    save_settings(self.settings)
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
        task = self.task
        task.archive()
        session = Session(out_root=task.session.out_root, client=task.session.client,
                          visual_check=task.session.visual_check)
        self.task = DocumentTask(session, journal=task.journal, render=task.render, mode=task.mode)
        self.task.save()

    def save_settings(self) -> None:
        self.settings.out_root = str(self.task.session.out_root)
        self.settings.mode = self.task.mode
        save_settings(self.settings)
        if not self.busy:
            self.task.save()


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
        body = await request.json()
        paths = [str(p) for p in (body.get("paths") or [])]
        try:
            state.task.attach(paths, template=bool(body.get("template")))
        except (OSError, ValueError) as e:
            return error(str(e))
        return state.view()

    @app.post("/api/upload")
    async def upload(files: List[UploadFile] = File(...), template: str = Form("false")):
        if state.busy:
            return busy_error()
        folder = config_dir() / "attachments" / time.strftime("%Y%m%d-%H%M%S")
        folder.mkdir(parents=True, exist_ok=True)
        saved: List[str] = []
        for f in files:
            name = Path(f.filename or "document.docx").name
            target = folder / name
            n = 1
            while target.exists():
                target = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
                n += 1
            target.write_bytes(await f.read())
            saved.append(str(target))
        try:
            state.task.attach(saved, template=str(template).lower() in ("1", "true", "yes"))
        except (OSError, ValueError) as e:
            return error(str(e))
        return state.view()

    @app.post("/api/pick")
    async def pick(request: Request):
        body = await request.json()
        kind = str(body.get("kind") or "documents")
        paths = state.shell.pick_files(kind)
        if paths is None:
            return error("This window has no native file dialog; drop the files on the page instead.", 501)
        return {"paths": list(paths)}

    @app.post("/api/remove")
    async def remove(request: Request):
        if state.busy:
            return busy_error()
        body = await request.json()
        state.task.remove(str(body.get("path") or ""), template=bool(body.get("template")))
        return state.view()

    @app.post("/api/send")
    async def send(request: Request):
        body = await request.json()
        text = str(body.get("text") or "").strip()
        if not text:
            return error("Type what you'd like to do with these documents.")
        return stream("send", text=text, chip=bool(body.get("chip")))

    @app.post("/api/choose")
    async def choose(request: Request):
        body = await request.json()
        return stream("choose", choice_id=str(body.get("choice_id") or ""))

    @app.post("/api/primary")
    async def primary(request: Request):
        body = await request.json()
        hold = body.get("hold") or []
        if not isinstance(hold, list) or not all(isinstance(p, str) for p in hold):
            return error("hold must list the documents to set aside, by path.")
        return stream("primary", token=str(body.get("token") or ""), hold=hold)

    @app.get("/api/recipes")
    def recipe_catalog():
        return {"recipes": recipes.catalog(), "groups": list(recipes.GROUPS)}

    @app.post("/api/recipe")
    async def recipe(request: Request):
        body = await request.json()
        inputs = body.get("inputs") if isinstance(body, dict) else None
        if inputs is not None and not isinstance(inputs, dict):
            return error("The button's form could not be read.")
        return stream("recipe", recipe=str(body.get("recipe") or ""), inputs=inputs or {})

    @app.post("/api/mode")
    async def set_mode(request: Request):
        if state.busy:
            return busy_error()
        body = await request.json()
        try:
            state.task.set_mode(str(body.get("mode") or ""))
        except ValueError as e:
            return error(str(e))
        state.save_settings()
        return state.view()

    @app.post("/api/handoff")
    async def hand_off(request: Request):
        if state.busy:
            return busy_error()
        body = await request.json()
        paths = body.get("paths") if isinstance(body, dict) else None
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

    @app.post("/api/new-conversation")
    def new_conversation():
        if state.busy:
            return busy_error()
        state.new_conversation()
        return state.view()

    @app.post("/api/open")
    async def open_result(request: Request):
        body = await request.json()
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
                "can_remember": credentials.available(), "out_root": str(state.task.session.out_root),
                "logs_dir": str(logs_dir()), "steps_per_pause": STEPS_PER_PAUSE, "theme": state.settings.theme,
                "fast_mode": state.settings.fast_mode, "fast": fast_offer()}

    @app.post("/api/settings")
    async def set_settings(request: Request):
        body = await request.json()
        if "theme" in body:
            if body["theme"] not in THEMES:
                return error("There is no such colour scheme.")
            state.settings.theme = body["theme"]
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
            os.environ[credentials.ENV] = key
            state.key_origin = "session"
            if remember and credentials.available():
                try:
                    credentials.save(key)
                    state.key_origin = "saved"
                except Exception:  # noqa: BLE001
                    return error("The key is set for this session but could not be saved securely.")
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
        roots = [Path(state.task.session.out_root), default_run_root()]
        try:
            made = diag.diagnostic_bundle(target, logs=logs_dir(), config=config_dir(), run_roots=roots,
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
