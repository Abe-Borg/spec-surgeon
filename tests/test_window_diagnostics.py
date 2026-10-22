"""The window's own diagnostics (docs/DEVELOPER_DIAGNOSTICS.md, "The window"): the page's reports reach the journal
and the app log, the watchdog names a frozen page, a quiet job and an unobeyed Stop with every thread's stack, every
page action leaves a request event, the window's close and shutdown are logged, the next start says what a session
that never closed was doing, and the diagnostic bundle holds the logs and records but no document."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from spec_surgeon import diagnostics as diag
from spec_surgeon import telemetry
from spec_surgeon.web import server as server_mod
from spec_surgeon.web import watch as watch_mod
from spec_surgeon.web.server import TOKEN_HEADER, create_app
from spec_surgeon.web.shell import on_closing, shutdown
from spec_surgeon.web.watch import WindowWatch
from test_document_task import REQUEST, document
from test_web_server import TOKEN, frames, window


@pytest.fixture
def logs(tmp_path):
    """The window's app log and event journal as ``gui.main`` sets them up; ``logs.lines()`` the readable log,
    ``logs.events(prefix)`` the journal's events whose names start with ``prefix``."""
    folder = tmp_path / "logs"
    path = diag.setup_app_logging(folder)
    journal = telemetry.app_events_path(folder)

    class Logs:
        dir = folder

        @staticmethod
        def lines():
            for h in logging.getLogger("spec_surgeon").handlers:
                h.flush()
            return path.read_text(encoding="utf-8")

        @staticmethod
        def events(prefix=""):
            rows = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()] \
                if journal.exists() else []
            return [r for r in rows if r["event"].startswith(prefix)]
    try:
        yield Logs
    finally:
        for lg in (logging.getLogger("spec_surgeon"), telemetry._LOGGER):
            for h in [h for h in lg.handlers if isinstance(h, logging.FileHandler)]:
                lg.removeHandler(h)
                h.close()


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


# ---- the page's reports -------------------------------------------------------------------------------------------

def test_the_pages_reports_reach_the_journal_and_the_app_log(tmp_path, logs):
    state, client = window(tmp_path)
    reports = [
        {"kind": "loaded", "visible": True, "user_agent": "Mozilla/5.0 Edg/141.0.0.0", "viewport": "1440x900",
         "reduced_motion": False, "version": "1.8.1"},
        {"kind": "error", "message": "TypeError: x is undefined", "source": "assets/index.js", "line": 3,
         "column": 77, "stack": "TypeError: x is undefined\n    at reveal (assets/index.js:3:77)", "count": 4},
        {"kind": "rejection", "message": "fetch failed", "stack": ""},
        {"kind": "render", "message": "Cannot read properties of null", "stack": "at MessageBubble",
         "component_stack": "\n    at MessageBubble\n    at Chat\n    at App"},
        {"kind": "stream", "action": "send", "reason": "cut", "message": ""},
        {"kind": "beat", "visible": True, "long_tasks": {"count": 3, "total_ms": 3100, "max_ms": 2400}},
        {"kind": "beat", "visible": True, "long_tasks": {"count": 1, "total_ms": 80, "max_ms": 80}},
        {"kind": "nonsense"},
        "not a report",
    ]
    answer = client.post("/api/diagnostics", json={"reports": reports})
    assert answer.status_code == 200 and answer.json() == {"ok": True, "taken": 7}
    names = [e["event"] for e in logs.events("page.")]
    assert names == ["page.loaded", "page.error", "page.unhandled_rejection", "page.render_failed",
                     "page.stream_ended_early", "page.slow"]          # a short long task is no event
    error = logs.events("page.error")[0]
    assert error["level"] == "error" and error["where"] == "assets/index.js:3:77" and error["count"] == 4
    assert "at reveal" in error["stack"]
    assert "MessageBubble" in logs.events("page.render_failed")[0]["component_stack"]
    text = logs.lines()
    assert "Window page loaded (load 1 this session; 1440x900; full motion): Mozilla/5.0 Edg/141.0.0.0" in text
    assert "Window page error: TypeError: x is undefined at assets/index.js:3:77 (4× since the last report)" in text
    assert "    at reveal (assets/index.js:3:77)" in text
    assert "Window page error (a promise nobody caught): fetch failed" in text
    assert "The window stopped drawing (its error screen is showing): Cannot read properties of null" in text
    assert "in the window's parts:" in text and "at Chat" in text
    assert "The window lost the stream of job send before it ended (cut)" in text
    assert "The window's page was blocked for 2.4 s at once (3 long tasks, 3.1 s in all" in text


def test_a_diagnostics_report_needs_the_token_and_a_json_object(tmp_path):
    state, client = window(tmp_path)
    bare = TestClient(create_app(state))
    assert bare.post("/api/diagnostics", json={"reports": []}).status_code == 401
    assert client.post("/api/diagnostics", content=b"{not json", headers={"Content-Type": "application/json"}
                       ).status_code == 400
    assert client.post("/api/diagnostics", json=["a list"]).json() == {"ok": True, "taken": 0}
    many = [{"kind": "visibility", "visible": True}] * (watch_mod.MAX_REPORTS + 10)
    assert client.post("/api/diagnostics", json={"reports": many}).json()["taken"] == watch_mod.MAX_REPORTS


def test_page_errors_stop_filling_the_app_log_but_not_the_journal(logs, monkeypatch):
    monkeypatch.setattr(watch_mod, "MAX_ERROR_LINES", 3)
    w = WindowWatch()
    w.page_reports([{"kind": "error", "message": f"boom {i}"} for i in range(5)])
    text = logs.lines()
    assert "boom 2" in text and "boom 3" not in text
    assert "(further page errors this session go to the event journal only)" in text
    assert len(logs.events("page.error")) == 5


# ---- the watchdog -------------------------------------------------------------------------------------------------

def test_a_showing_page_that_goes_silent_is_named_with_every_threads_stack(logs):
    clock = Clock()
    w = WindowWatch(clock=clock)
    w.tick()
    w.page_reports([{"kind": "loaded", "visible": True}])
    for _ in range(8):                                   # 40 s of silence: not yet
        clock.t += 5
        w.tick()
    assert logs.events("page.unresponsive") == []
    clock.t += 5
    w.tick()                                             # 45 s
    [silent] = logs.events("page.unresponsive")
    assert silent["level"] == "warning" and silent["silent_s"] == 45.0 and silent["job"] is None
    clock.t += 5
    w.tick()
    assert len(logs.events("page.unresponsive")) == 1    # said once per silence
    text = logs.lines()
    assert "The window's page, showing, has sent nothing for 45 s (no job running)" in text
    assert "Thread MainThread" in text and "(this one)" in text
    clock.t += 3
    w.page_reports([{"kind": "beat", "visible": True}])
    [back] = logs.events("page.responsive")
    assert back["silent_s"] == 53.0
    assert "The window's page is answering again, after 53 s silent" in logs.lines()


def test_a_hidden_or_closed_page_is_never_called_frozen(logs):
    clock = Clock()
    w = WindowWatch(clock=clock)
    w.tick()
    w.page_reports([{"kind": "loaded", "visible": True}, {"kind": "visibility", "visible": False}])
    for _ in range(30):
        clock.t += 5
        w.tick()
    w.page_reports([{"kind": "visibility", "visible": True}, {"kind": "unload"}])
    for _ in range(30):
        clock.t += 5
        w.tick()
    fresh = WindowWatch(clock=clock)                    # no page ever loaded
    for _ in range(30):
        clock.t += 5
        fresh.tick()
    assert logs.events("page.unresponsive") == []


def test_a_computer_that_slept_restarts_the_clocks_instead_of_reporting_a_stall(logs):
    clock = Clock()
    job = {"action": "send", "id": "j1", "started": clock.t, "last_progress": clock.t, "stop_requested": None}
    w = WindowWatch(job_status=lambda: dict(job), clock=clock)
    w.tick()
    w.page_reports([{"kind": "loaded", "visible": True}])
    clock.t += 3600                                      # an hour asleep
    w.tick()
    assert [e["event"] for e in logs.events("window.watch")] == ["window.watch.resumed"]
    assert logs.events("page.unresponsive") == [] and logs.events("web.job.quiet") == []
    for _ in range(8):
        clock.t += 5
        w.tick()
    assert logs.events("page.unresponsive") == []        # 40 s since waking, not an hour
    clock.t += 5
    w.tick()
    assert len(logs.events("page.unresponsive")) == 1
    assert logs.events("web.job.quiet") == []            # the job's clock restarted at waking too


def test_a_quiet_job_and_an_unobeyed_stop_leave_every_threads_stack(logs):
    clock = Clock()
    job = {"action": "send", "id": "j1", "started": clock.t, "last_progress": clock.t, "stop_requested": None}
    w = WindowWatch(job_status=lambda: dict(job), clock=clock)

    def run(seconds):
        for _ in range(int(seconds // 5)):
            clock.t += 5
            w.tick()
    run(295)
    assert logs.events("web.job.quiet") == []
    run(5)
    [quiet] = logs.events("web.job.quiet")
    assert quiet["action"] == "send" and quiet["quiet_s"] == 300.0
    assert "Job send has shown no progress for 5 min 0 s (running 5 min 0 s). Where every thread" in logs.lines()
    run(895)
    assert len(logs.events("web.job.quiet")) == 1
    run(5)
    assert len(logs.events("web.job.quiet")) == 2        # again 15 minutes on
    job["last_progress"] = clock.t                       # it moved
    job["stop_requested"] = clock.t
    run(25)
    assert logs.events("web.job.stop_overdue") == []
    run(5)
    run(30)
    [overdue] = logs.events("web.job.stop_overdue")
    assert overdue["since_stop_s"] == 30.0
    assert "Job send is still running 30 s after Stop." in logs.lines()
    job.update(id="j2", started=clock.t, last_progress=clock.t, stop_requested=None)   # the next job starts afresh
    run(295)
    assert len(logs.events("web.job.quiet")) == 2


def test_the_watch_thread_ticks_and_stops(logs):
    ticks = threading.Event()
    w = WindowWatch(job_status=lambda: ticks.set() or None)
    w.start(interval=0.01)
    assert ticks.wait(5)
    w.stop()
    assert w._thread is None


# ---- jobs, requests, the window's life ------------------------------------------------------------------------------

def test_a_job_says_when_it_starts_and_when_stop_was_asked(tmp_path, logs):
    import threading as th
    from fake_claude import FakeClient
    started, release = th.Event(), th.Event()

    class Slow(FakeClient):
        def _stream(self, **kw):
            started.set()
            release.wait(10)
            return super()._stream(**kw)
    state, client = window(tmp_path, Slow())
    client.post("/api/attach", json={"paths": [str(document(tmp_path / "Spec.docx"))]})
    result = {}

    def go():
        with client.stream("POST", "/api/send", json={"text": REQUEST}) as response:
            result["frames"] = frames(response)
    t = th.Thread(target=go)
    t.start()
    assert started.wait(10)
    job = state.job_status()
    assert job["action"] == "send" and job["stop_requested"] is None
    assert client.post("/api/stop").json() == {"ok": True}
    assert state.job_status()["stop_requested"] is not None
    release.set()
    t.join(20)
    text = logs.lines()
    assert "Job send started (a typed message)" in text
    assert "Stop requested for job send (running " in text
    assert [e["action"] for e in logs.events("web.job.stop_requested")] == ["send"]
    assert state.job_status() is None


def test_job_started_lines_never_say_what_was_typed():
    assert server_mod.job_started_line("send", {"text": "secret words", "chip": False}) == \
        "Job send started (a typed message)"
    assert server_mod.job_started_line("send", {"text": "Yes", "chip": True}) == \
        "Job send started (a suggested reply the user tapped)"
    assert server_mod.job_started_line("choose", {"choice_id": "c2"}) == "Job choose started (choice c2)"
    assert server_mod.job_started_line("primary", {"token": "t", "hold": ["a", "b"]}) == \
        "Job primary started (the primary action, 2 document(s) held back)"


def test_every_page_action_leaves_a_request_event_and_a_failing_route_its_traceback(tmp_path, logs, monkeypatch):
    state, client = window(tmp_path)
    client.get("/api/state")
    client.post("/api/attach", json={"paths": [str(document(tmp_path / "Spec.docx"))]})
    client.post("/api/remove", json={"path": "nowhere.docx"})
    TestClient(create_app(state)).get("/api/state")                  # 401: logged although polled
    rows = logs.events("web.request")
    assert [(r["method"], r["path"], r["status"]) for r in rows] == [
        ("POST", "/api/attach", 200), ("POST", "/api/remove", 200), ("GET", "/api/state", 401)]
    assert all(isinstance(r["duration_ms"], (int, float)) for r in rows)

    def broken(*a, **k):
        raise RuntimeError("attach broke")
    monkeypatch.setattr(state.task, "attach", broken)
    loose = TestClient(create_app(state), headers={TOKEN_HEADER: TOKEN}, raise_server_exceptions=False)
    assert loose.post("/api/attach", json={"paths": ["x.docx"]}).status_code == 500
    [failed] = logs.events("web.request.failed")
    assert failed["path"] == "/api/attach" and failed["error_type"] == "RuntimeError"
    text = logs.lines()
    assert "POST /api/attach failed after " in text and "RuntimeError: attach broke" in text


def test_a_slow_route_is_named_but_the_file_dialog_is_not(tmp_path, logs, monkeypatch):
    state, client = window(tmp_path)
    monkeypatch.setattr(server_mod, "SLOW_REQUEST_MS", 0)
    client.get("/api/health")
    client.post("/api/pick", json={"kind": "documents"})
    text = logs.lines()
    assert "GET /api/health took " in text and "/api/pick took" not in text


def test_the_window_logs_its_close_and_a_job_that_would_not_stop(tmp_path, logs):
    state, client = window(tmp_path)
    assert on_closing(state) is True
    assert "Window closing (no job running)" in logs.lines()
    release = threading.Event()
    stuck = threading.Thread(target=release.wait, args=(20,), name="spec-surgeon-job")
    stuck.start()
    state.thread, state.cancel = stuck, threading.Event()
    state.job = {"action": "primary", "id": "j", "started": 0.0, "last_progress": 0.0, "stop_requested": None}
    try:
        on_closing(state)

        class Server:
            should_exit = False
        server = Server()
        shutdown(state, server, wait=0.05)
        assert server.should_exit is True
    finally:
        release.set()
        stuck.join(5)
    text = logs.lines()
    assert "Window closing while job primary runs: stopping it" in text
    assert "Job primary did not stop within 0 s of the window closing; the process stays until it does." in text
    assert "Thread spec-surgeon-job" in text
    [done] = logs.events("window.shutdown.succeeded")
    assert done["job_running"] is True and done["job_stopped"] is False
    assert [e["job"] for e in logs.events("window.closing")] == [None, "primary"]


# ---- the next start ---------------------------------------------------------------------------------------------------

def _dead_pid():
    run = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
    return int(run.stdout.strip())


def test_the_next_start_says_what_a_session_that_never_closed_was_doing(tmp_path):
    pid = _dead_pid()
    folder = tmp_path / "logs"
    folder.mkdir()
    rows = [{"event": f"older.{i}", "timestamp": "2026-10-22T10:40:00.000+00:00"} for i in range(5)]
    (folder / f"app-events-{pid}.jsonl.1").write_text("".join(json.dumps(r) + "\n" for r in rows), "utf-8")
    newest = [{"event": "web.job.succeeded", "action": "send", "timestamp": "2026-10-22T10:49:31.000+00:00"},
              {"event": "web.request", "method": "POST", "path": "/api/send", "status": 200,
               "timestamp": "2026-10-22T10:50:02.000+00:00"},
              {"event": "web.job.started", "action": "send", "timestamp": "2026-10-22T10:50:02.100+00:00"}]
    (folder / f"app-events-{pid}.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in newest) + '{"event": "cut sh', "utf-8")   # the process died mid-line
    (folder / "crash-gone.log").write_text("", "utf-8")
    (folder / f"session-{pid}.json").write_text(json.dumps(
        {"pid": pid, "version": "1.8.0", "started": "2026-10-22T03:39:50-07:00", "crash_log": "crash-gone.log"}),
        "utf-8")
    watch = diag.CrashWatch(folder)
    [gone] = watch.previous()
    assert gone["pid"] == pid and gone["crash_log"] is None and gone["crash_log_found"] is True
    events = [e["event"] for e in gone["last_events"]]
    assert events == ["older.0", "older.1", "older.2", "older.3", "older.4", "web.job.succeeded", "web.request",
                      "web.job.started"]                 # the rotation fills what the newest file lacks
    assert gone["last_events"][-1]["action"] == "send" and gone["last_events"][-2]["status"] == "200"
    line = diag.previous_session_line(gone)
    assert line.startswith(f"The previous session (pid {pid}, Spec Surgeon 1.8.0, started 2026-10-22T03:39:50-07:00)"
                           " ended without closing. Its last recorded events: ")
    assert " web.request method=POST path=/api/send status=200; " in line
    assert line.rstrip(".").split("; ")[-1].endswith("web.job.started action=send. Its crash log is empty: no native "
                                                     "crash was caught, so the process was ended from outside (Task "
                                                     "Manager, signing out, a power cut) or stopped answering and was "
                                                     "closed")


def test_a_previous_session_with_a_fault_or_no_journal_says_so(tmp_path):
    info = {"pid": 7, "version": "1.8.0", "started": "s", "crash_log": "C:/logs/crash-1.log", "crash_log_found": True,
            "excerpt": ["noise", "Windows fatal exception: access violation", "", "Thread 0x1 (most recent call first):"],
            "last_events": []}
    line = diag.previous_session_line(info)
    assert "Its event journal has nothing to say" in line
    assert "Python's fault handler wrote what it saw to C:/logs/crash-1.log: Windows fatal exception: access " \
           "violation" in line
    assert diag.last_events(tmp_path, 12345) == []
    assert "It left no crash log." in diag.previous_session_line({"pid": 1, "crash_log_found": False})


def test_thread_stacks_show_where_each_thread_waits():
    gate = threading.Event()
    t = threading.Thread(target=gate.wait, args=(10,), name="waiting-helper", daemon=True)
    t.start()
    try:
        text = diag.thread_stacks()
    finally:
        gate.set()
        t.join(5)
    assert "Thread waiting-helper (daemon):" in text and "in wait" in text
    assert "(this one)" in text and "in thread_stacks" in text


# ---- the diagnostic bundle --------------------------------------------------------------------------------------------

def _tree(root, files):
    for name, data in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def test_the_bundle_holds_logs_conversations_and_run_records_but_no_document(tmp_path):
    logs_dir, config, runs = tmp_path / "logs", tmp_path / "config", tmp_path / "runs"
    _tree(logs_dir, {"spec-surgeon.log": "started", "spec-surgeon.log.1": "older", "app-events-1.jsonl": "{}",
                     "crash-20261022-1.log": "", "session-1.json": "{}", "unrelated.txt": "no"})
    _tree(config, {"settings.json": "{}", "conversation.json": "{}",
                   **{f"conversations/{i}.json": "{}" for i in range(5)}})
    for i in range(5):
        os.utime(config / "conversations" / f"{i}.json", (1000 + i, 1000 + i))
    for i in range(7):
        _tree(runs, {f"2026102{i}-0000/compile.json": "{}"})
        os.utime(runs / f"2026102{i}-0000", (2000 + i, 2000 + i))
    _tree(runs / "20261026-0000", {
        "conversation.json": "{}", "DIAGNOSTICS.md": "#", "logs/events.jsonl": "{}", "logs/run.log": "x",
        "records/Spec.record.json": "{}", "preview/logs/preview.log": "p", "plans/spec.json": "{}",
        "outputs/Spec.docx": b"PK", "outputs/notes.json": "{}", "samples/Spec/page-1.png": b"\x89PNG",
        "samples/Spec/meta.json": "{}", "render/Spec.pdf": b"%PDF", "generated/op.py": "print()"})
    target = tmp_path / "out" / "bundle.zip"
    made = diag.diagnostic_bundle(target, logs=logs_dir, config=config, run_roots=[runs, runs],
                                  header=["Spec Surgeon 9.9.9"])
    assert made["path"] == target and target.is_file() and not (tmp_path / "out" / "bundle.zip.part").exists()
    with zipfile.ZipFile(target) as z:
        names = set(z.namelist())
        manifest = z.read("MANIFEST.txt").decode("utf-8")
    assert {"logs/spec-surgeon.log", "logs/spec-surgeon.log.1", "logs/app-events-1.jsonl", "logs/crash-20261022-1.log",
            "logs/session-1.json", "config/settings.json", "config/conversation.json", "config/conversations/4.json",
            "config/conversations/3.json", "config/conversations/2.json", "runs/20261026-0000/compile.json",
            "runs/20261026-0000/conversation.json", "runs/20261026-0000/DIAGNOSTICS.md",
            "runs/20261026-0000/logs/events.jsonl", "runs/20261026-0000/logs/run.log",
            "runs/20261026-0000/records/Spec.record.json", "runs/20261026-0000/preview/logs/preview.log",
            "runs/20261026-0000/plans/spec.json", "MANIFEST.txt"} <= names
    assert not any(n.endswith((".docx", ".png", ".pdf", ".py")) for n in names)
    assert not any("/outputs/" in n or "/samples/" in n for n in names)          # not even their JSON
    assert "logs/unrelated.txt" not in names and "config/conversations/1.json" not in names
    assert {n.split("/")[1] for n in names if n.startswith("runs/")} == {f"2026102{i}-0000" for i in range(2, 7)}
    assert manifest.startswith("Spec Surgeon 9.9.9\n") and "runs/20261026-0000/outputs/ (documents or pages)" in manifest
    assert "No document, picture or page image is in it" in manifest


def test_the_bundle_leaves_out_what_is_too_large(tmp_path, monkeypatch):
    monkeypatch.setattr(diag, "BUNDLE_FILE_CAP", 10)
    _tree(tmp_path / "logs", {"spec-surgeon.log": "x" * 50, "app-events-1.jsonl": "{}"})
    made = diag.diagnostic_bundle(tmp_path / "b.zip", logs=tmp_path / "logs", config=tmp_path / "none", run_roots=[])
    assert made["files"] == ["logs/app-events-1.jsonl"]
    assert made["skipped"] == ["logs/spec-surgeon.log (0 MB: larger than 0 MB)"]


def test_a_run_folder_that_cannot_be_listed_is_named_not_fatal(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    real = Path.iterdir

    def refuse(self):
        if self == runs:
            raise PermissionError("access is denied")
        return real(self)
    monkeypatch.setattr(Path, "iterdir", refuse)
    made = diag.diagnostic_bundle(tmp_path / "b.zip", logs=tmp_path / "none", config=tmp_path / "none",
                                  run_roots=[runs])
    assert made["files"] == [] and "the run folder could not be listed: access is denied" in made["skipped"][0]


def test_the_bundle_route_saves_and_shows_the_zip(tmp_path, logs, monkeypatch):
    shown = []
    monkeypatch.setattr(server_mod, "reveal_path", shown.append)
    monkeypatch.setattr(server_mod, "logs_dir", lambda: logs.dir)
    state, client = window(tmp_path)
    diag.app_log().info("something to bundle")
    answer = client.post("/api/diagnostics/bundle").json()
    path = Path(answer["path"])
    assert path.is_file() and path.name.startswith("spec-surgeon-diagnostics-") and answer["name"] == path.name
    assert shown == [path] and answer["files"] >= 2
    with zipfile.ZipFile(path) as z:
        assert "logs/spec-surgeon.log" in z.namelist() and "Job running: none" in z.read("MANIFEST.txt").decode()
    assert [e["files"] for e in logs.events("diagnostics.bundle.saved")] == [answer["files"]]
