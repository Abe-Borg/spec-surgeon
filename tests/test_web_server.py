"""The window's server (docs/WEB_API.md): the launch token guards every route, the view has the contract's shape,
attachments arrive by upload, a job streams its events and ends in ``done`` (or ``error`` with a retry-safe state),
images are served only from the app's own folders, and settings, about and updates answer."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fake_claude import FakeClient, resp, text, tool
from spec_surgeon.document_task import START_OVER, DocumentTask
from spec_surgeon.gui import Session
from spec_surgeon.settings import Settings, load_settings
from spec_surgeon.web.server import TOKEN_HEADER, WindowState, create_app
from test_document_task import REQUEST, SPEC, document

TOKEN = "t-0123456789"


class FakeUpdates:
    def __init__(self):
        self.calls = []
        self._state = {"state": "available", "version": "9.9.9", "notes": "Faster.", "progress": 0, "error": "",
                       "text": "You have 1.5.0.", "manual_result": "", "install_log": ""}

    def state(self):
        return dict(self._state)

    def check(self, manual=True):
        self.calls.append(("check", manual))

    def install(self):
        self.calls.append(("install",))
        return ""

    def cancel_download(self):
        self.calls.append(("cancel",))

    def skip(self):
        self._state["state"] = "none"

    def dismiss(self):
        self._state["state"] = "none"

    def releases_url(self):
        return "https://example.invalid/releases"


def window(tmp_path, fake=None, journal=None):
    fake = fake or FakeClient(resp(tool("submit_editspec", {"spec": SPEC})))
    session = Session(client=fake, out_root=tmp_path / "out", visual_check=False)
    task = DocumentTask(session, render=False, journal=journal)
    settings = Settings()
    state = WindowState(task, settings, token=TOKEN, updates=FakeUpdates(), version="1.6.0")
    client = TestClient(create_app(state), headers={TOKEN_HEADER: TOKEN})
    return state, client


def frames(response):
    out = []
    for line in response.iter_lines():
        if line.startswith("data: "):
            out.append(json.loads(line[6:]))
    return out


@pytest.fixture
def job_log(tmp_path):
    """The window's app log (``spec-surgeon.log``) as ``gui.main`` sets it up, redacting formatter included; the
    fixture returns the lines that say how a job ended."""
    import logging
    from spec_surgeon import diagnostics as diag
    path = diag.setup_app_logging(tmp_path / "logs")
    logger = logging.getLogger("spec_surgeon")

    def ended():
        for h in logger.handlers:
            h.flush()
        return [line for line in path.read_text(encoding="utf-8").splitlines() if " Job " in line and " ended: " in line]
    try:
        yield ended
    finally:
        for h in [h for h in logger.handlers if isinstance(h, logging.FileHandler)]:
            logger.removeHandler(h)
            h.close()


def test_every_api_route_needs_the_launch_token(tmp_path):
    state, client = window(tmp_path)
    bare = TestClient(create_app(state))
    assert bare.get("/api/state").status_code == 401
    assert bare.get("/api/state?token=" + TOKEN).status_code == 200
    assert client.get("/api/health").json()["ok"] is True


def test_the_view_has_the_contracts_shape_and_uploads_attach_documents(tmp_path, monkeypatch):
    monkeypatch.setenv("SPEC_SURGEON_CONFIG_DIR", str(tmp_path / "config"))
    state, client = window(tmp_path)
    view = client.get("/api/state").json()
    for key in ("version", "busy", "stage", "connection", "cost", "attachments", "messages", "plan", "questions",
                "choices", "chips", "samples", "warnings", "worth_knowing", "blocking", "results", "action", "working",
                "budget", "update"):
        assert key in view, key
    assert view["stage"] == "empty" and view["plan"] is None and view["action"]["kind"] == "none"
    assert view["worth_knowing"] == []
    assert view["update"]["state"] == "available" and view["version"] == "1.6.0"
    source = document(tmp_path / "Spec.docx")
    with open(source, "rb") as fh:
        view = client.post("/api/upload", files=[("files", ("Spec.docx", fh.read(), "application/octet-stream"))],
                           data={"template": "false"}).json()
    assert [a["name"] for a in view["attachments"]] == ["Spec.docx"]
    assert view["attachments"][0]["kind"] == "document" and view["attachments"][0]["state"] in ("ok", "note")
    assert Path(view["attachments"][0]["path"]).is_file() and Path(view["attachments"][0]["path"]) != source
    bad = client.post("/api/attach", json={"paths": [str(tmp_path / "notes.txt")]})
    assert bad.status_code == 400 and "Word" in bad.json()["error"]
    view = client.post("/api/remove", json={"path": view["attachments"][0]["path"]}).json()
    assert view["attachments"] == []


def test_a_job_streams_its_events_and_ends_with_the_full_view(tmp_path):
    fake = FakeClient(resp(text("I'll make the title bold."),
                           tool("submit_editspec", {"spec": SPEC, "suggested_replies": ["Also italic"]})), live=True)
    state, client = window(tmp_path, fake)
    client.post("/api/attach", json={"paths": [str(document(tmp_path / "Spec.docx"))]})
    with client.stream("POST", "/api/send", json={"text": REQUEST}) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        events = frames(response)
    kinds = [e["type"] for e in events]
    assert kinds[-1] == "done" and "text_delta" in kinds and "plan" in kinds and "samples" in kinds
    # A mid-job samples event carries the same URLs the state payload does, never a file path (the page would
    # request the path and get a 404 until the final view arrived).
    said = [e["message"] for e in events if e["type"] == "message"]
    assert said and all(isinstance(m.get("id"), str) for m in said)   # the page finalizes the bubble under this id
    final = {m["id"]: m["text"] for m in events[-1]["state"]["messages"]}
    assert all(final[m["id"]] == m["text"] for m in said)
    for event in (e for e in events if e["type"] == "samples"):
        assert event["samples"], "the previews are in"
        for sample in event["samples"]:
            assert all(url.startswith("/api/image?path=") and "token=" in url for url in sample["images"])
    assert "".join(e["text"] for e in events if e["type"] == "text_delta") == "I'll make the title bold."
    done = events[-1]["state"]
    assert done["stage"] == "preview" and done["action"]["kind"] == "approve" and done["busy"] is False
    assert done["action"]["label"] == "Create edited copies" and done["chips"] == ["Also italic"]
    assert done["plan"]["stages"] == 1 and done["plan"]["groups"][0]["changes"][0]["summary"]
    assert done["messages"][-1]["role"] == "assistant" and done["messages"][-1]["id"]
    assert client.post("/api/stop").json() == {"ok": False}                 # nothing is running
    with client.stream("POST", "/api/primary", json={"token": done["action"]["token"]}) as response:
        events = frames(response)
    done = events[-1]
    assert done["type"] == "done" and done["state"]["stage"] == "done"
    assert done["state"]["results"][0]["name"] == "Spec.docx" and done["state"]["working"]["generation"] == 1
    assert START_OVER in done["state"]["chips"]
    assert client.post("/api/open", json={"path": done["state"]["results"][0]["output"] + ".missing"}).status_code == 404
    view = client.post("/api/start-over").json()
    assert view["working"]["generation"] == 0 and view["stage"] == "empty"


def test_a_failed_job_streams_an_error_with_a_retry_safe_view(tmp_path):
    state, client = window(tmp_path, FakeClient())           # a defect in the engine: nothing scripted at all
    client.post("/api/attach", json={"paths": [str(document(tmp_path / "Spec.docx"))]})
    with client.stream("POST", "/api/send", json={"text": REQUEST}) as response:
        events = frames(response)
    assert events[-1]["type"] == "error" and events[-1]["state"]["stage"] in ("empty", "resume")
    assert "could not finish" in events[-1]["message"] and events[-1]["state"]["busy"] is False
    assert events[-1]["resend"] == "" and events[-1]["state"]["messages"][0]["text"] == REQUEST
    assert client.post("/api/send", json={"text": ""}).status_code == 400


def test_a_choice_s_edited_wording_must_be_text_and_its_log_line_never_carries_it(tmp_path):
    from spec_surgeon.web.server import job_started_line
    state, client = window(tmp_path)
    bad = client.post("/api/choose", json={"choice_id": "c1", "wording": ["ISSUED"]})
    assert bad.status_code == 400 and "wording must be text" in bad.json()["error"]
    line = job_started_line("choose", {"choice_id": "c1", "wording": "ISSUED FOR BID"})
    assert line == "Job choose started (choice c1, its wording edited)" and "ISSUED" not in line


def test_images_are_served_only_from_the_apps_own_folders(tmp_path):
    state, client = window(tmp_path)
    inside = tmp_path / "out" / "x" / "page.png"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)
    outside = tmp_path / "elsewhere.png"
    outside.write_bytes(inside.read_bytes())
    assert client.get("/api/image", params={"path": str(inside)}).status_code == 200
    assert client.get("/api/image", params={"path": str(outside)}).status_code == 404
    assert state.image_url(inside).startswith("/api/image?path=")


def test_settings_about_updates_and_new_conversation(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    state, client = window(tmp_path)
    s = client.get("/api/settings").json()
    assert s["key_present"] is True and "steps_per_pause" not in s     # a scripted client counts as connected
    # the spending check: $20 a request until the user changes it, 0 or null for off, nothing else accepted
    assert s["spending_limit_usd"] == s["spending_default_usd"] == 20.0 == state.task.spending_limit
    assert client.post("/api/settings", json={"spending_limit_usd": 7.5}).status_code == 200
    assert state.task.spending_limit == 7.5 == load_settings().spending_limit_usd
    for bad in (-1, "ten", True, float("inf")):
        r = client.post("/api/settings", content=json.dumps({"spending_limit_usd": bad}).replace("Infinity", "1e999"),
                        headers={"content-type": "application/json"})
        assert r.status_code == 400 and state.task.spending_limit == 7.5
    assert client.get("/api/settings").json()["spending_limit_usd"] == 7.5
    client.post("/api/settings", json={"spending_limit_usd": None})
    assert state.task.spending_limit is None and load_settings().spending_limit_usd == 0.0
    assert client.get("/api/settings").json()["spending_limit_usd"] is None
    state.new_conversation()
    assert state.task.spending_limit is None                            # a new conversation keeps the user's check
    s = client.post("/api/settings", json={"api_key": "sk-ant-test", "remember": False}).json()
    assert s["origin"] == "session" and s["theme"] == "clay"
    assert client.post("/api/settings", json={"theme": "linen"}).json()["theme"] == "linen"
    assert state.settings.theme == "linen" and load_settings().theme == "linen"      # kept for the next start
    refused = client.post("/api/settings", json={"theme": "dark"})
    assert refused.status_code == 400 and "colour scheme" in refused.json()["error"]
    assert client.get("/api/settings").json()["theme"] == "linen"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # fast mode: off until turned on, offered with the price table's own numbers, kept for the next start
    from spec_surgeon import llm
    s = client.get("/api/settings").json()
    assert s["fast_mode"] is False and llm.FAST_MODE is False and state.view()["cost"]["fast"] is False
    assert s["fast"] == {"model": "claude-opus-5-5", "available": True, "speedup": llm.FAST_SPEEDUP,
                         "price_factor": 2, "standard_usd_per_mtok": [4.0, 20.0], "fast_usd_per_mtok": [8.0, 40.0],
                         "refused": None}
    assert client.post("/api/settings", json={"fast_mode": True}).json()["fast_mode"] is True
    assert llm.FAST_MODE is True and load_settings().fast_mode is True and state.view()["cost"]["fast"] is True
    refused = client.post("/api/settings", json={"fast_mode": "yes"})
    assert refused.status_code == 400 and llm.FAST_MODE is True
    # refused for this key: the setting stays on, but nothing says Fast, and Settings says why
    monkeypatch.setattr(llm, "_fast_unavailable", "PermissionDeniedError: speed not allowed · HTTP 403")
    s = client.get("/api/settings").json()
    assert s["fast_mode"] is True and "speed not allowed" in s["fast"]["refused"]
    assert state.view()["cost"]["fast"] is False
    monkeypatch.setattr(llm, "_fast_unavailable", None)
    assert state.view()["cost"]["fast"] is True
    assert client.post("/api/settings", json={"fast_mode": False}).json()["fast_mode"] is False
    assert llm.FAST_MODE is False and load_settings().fast_mode is False
    about = client.get("/api/about").json()
    assert about["version"] == "1.6.0" and "MIT" in about["license"] and about["credits"]
    assert client.post("/api/update/check").json()["state"] == "available"
    assert client.post("/api/update/download").json()["state"] == "available"
    assert ("check", True) in state.updates.calls and ("install",) in state.updates.calls
    assert client.post("/api/update/skip").json()["state"] == "none"
    client.post("/api/attach", json={"paths": [str(document(tmp_path / "Spec.docx"))]})
    old = state.task
    view = client.post("/api/new-conversation").json()
    assert state.task is not old and view["attachments"] == [] and view["messages"] == []


def test_the_native_window_opens_the_page_in_the_kept_scheme_on_its_background(tmp_path, monkeypatch):
    """The shell hands the page its colour scheme in the query string (applied before the first paint) and gives
    the window that scheme's background, so a light scheme never opens on a dark frame."""
    import types
    from spec_surgeon.web import shell
    created = {}

    class Events:
        def __init__(self):
            self.closing = self

        def __iadd__(self, handler):
            return self

    def create_window(title, url, **kw):
        created.update(kw, url=url)
        return types.SimpleNamespace(events=Events())
    fake = types.ModuleType("webview")
    fake.settings, fake.create_window, fake.start = {}, create_window, lambda **kw: None
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(shell, "native_backend_problem", lambda: None)
    monkeypatch.setattr(shell, "serve", lambda state, port, dev=False: None)
    monkeypatch.setattr(shell, "shutdown", lambda state, server: None)
    state, _ = window(tmp_path)
    for theme, background in (("clay", "#262624"), ("linen", "#f5f1ea")):
        state.settings.theme = theme
        assert shell.run_window(state) == 0
        assert created["url"].endswith(f"&theme={theme}") and created["background_color"] == background


def test_a_second_job_while_one_runs_is_refused(tmp_path):
    import threading
    gate = threading.Event()

    class Slow(FakeClient):
        def _stream(self, **kw):
            gate.wait(5)
            return super()._stream(**kw)
    state, client = window(tmp_path, Slow(resp(tool("submit_editspec", {"spec": SPEC}))))
    client.post("/api/attach", json={"paths": [str(document(tmp_path / "Spec.docx"))]})
    events = state.start_job("send", text=REQUEST)
    assert state.busy and client.get("/api/state").json()["busy"] is True
    assert client.post("/api/send", json={"text": "again"}).status_code == 409
    assert client.post("/api/attach", json={"paths": []}).status_code == 409
    assert client.post("/api/discard").status_code == 409 and client.post("/api/undo").status_code == 409
    gate.set()
    while True:
        event = events.get(timeout=30)
        if event["type"] in ("done", "error"):
            break
    state.thread.join(10)
    assert event["type"] == "done" and event["state"]["busy"] is False and not state.busy


def test_a_chip_sent_by_the_page_is_never_approved_wording(tmp_path):
    chip = "Everything, and replace Contractor with Design-Builder"
    fake = FakeClient(resp(tool("ask_user", {"message": "Only the body text, or everything?",
                                             "questions": ["Only the body text, or everything?"],
                                             "suggested_replies": [chip]})),
                      resp(tool("submit_editspec", {"spec": SPEC})))
    state, client = window(tmp_path, fake)
    client.post("/api/attach", json={"paths": [str(document(tmp_path / "Spec.docx"))]})
    with client.stream("POST", "/api/send", json={"text": REQUEST}) as response:
        done = frames(response)[-1]
    assert done["type"] == "done" and chip in done["state"]["chips"]
    with client.stream("POST", "/api/send", json={"text": chip, "chip": True}) as response:
        done = frames(response)[-1]
    assert done["type"] == "done" and done["state"]["stage"] == "preview"
    assert chip in state.task.decisions and chip not in state.task.word_sources


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM on Windows ends a process without running its handlers")
@pytest.mark.parametrize("why", ["absent", "no-backend"])
def test_the_browser_fallback_serves_the_page_and_stops_on_a_termination_request(tmp_path, why):
    """Without pywebview — or with it installed but no backend on this computer (Linux without GTK bindings: every
    box with the ``gui`` extra and no desktop toolkit) — the window serves the page for the default browser
    and waits for Ctrl+C. A job a script starts in the background inherits SIGINT ignored (Python then installs no
    KeyboardInterrupt handler), so a termination request must stop it the same way: the server goes down and the
    exit code is 0."""
    import os
    import re
    import signal
    import subprocess
    import textwrap
    import urllib.request
    code = textwrap.dedent("""
        import sys
        import types
        if sys.argv[2] == "absent":
            sys.modules["webview"] = None                  # pywebview is not installed
        else:                                              # installed, but no backend here (what pywebview raises)
            fake, guilib = types.ModuleType("webview"), types.ModuleType("webview.guilib")
            def initialize(forced_gui=None):
                raise RuntimeError("You must have either QT or GTK with Python extensions installed in order to use pywebview.")
            guilib.initialize, fake.guilib = initialize, guilib
            sys.modules["webview"], sys.modules["webview.guilib"] = fake, guilib
        from spec_surgeon.document_task import DocumentTask
        from spec_surgeon.gui import Session
        from spec_surgeon.settings import Settings
        from spec_surgeon.web.server import WindowState, new_token
        from spec_surgeon.web.shell import run_window
        task = DocumentTask(Session(out_root=sys.argv[1]), render=False)
        sys.exit(run_window(WindowState(task, Settings(), token=new_token())))
    """)
    env = {**os.environ, "BROWSER": "true", "SPEC_SURGEON_CONFIG_DIR": str(tmp_path / "config")}
    proc = subprocess.Popen([sys.executable, "-c", code, str(tmp_path / "out"), why], env=env, text=True,
                            stderr=subprocess.PIPE,
                            preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN))  # a shell's background job
    try:
        port = None
        for _ in range(10):
            line = proc.stderr.readline()
            found = re.search(r"open in your browser at http://127\.0\.0\.1:(\d+)/", line)
            if found:
                port = found.group(1)
                break
        assert port, "the fallback never said where the page is"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:   # noqa: S310 — loopback
            assert resp.status == 200
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=20) == 0
    finally:
        if proc.poll() is None:
            proc.kill()


# ---- the review's probes (PR #46): closing, the loopback probe, a download during work, a bad token --------------

def test_closing_the_window_stops_the_job_and_nothing_outlives_it(tmp_path):
    """The window's X while work runs cuts the running step off (its progress saved, as Stop now does) and the
    shutdown waits for the job, so its last writes land now and never over a later launch's conversation."""
    import threading as th
    from spec_surgeon.web.shell import on_closing, shutdown
    started, release = th.Event(), th.Event()

    class Slow(FakeClient):
        def _stream(self, **kw):
            started.set()
            release.wait(10)
            return super()._stream(**kw)
    state, client = window(tmp_path, Slow(resp(tool("submit_editspec", {"spec": SPEC}))))
    client.post("/api/attach", json={"paths": [str(document(tmp_path / "Spec.docx"))]})
    state.start_job("send", text=REQUEST)
    assert started.wait(10) and state.busy
    assert on_closing(state) is True and state.cancel is not None and state.cancel.is_set()
    release.set()

    class Server:
        should_exit = False
    server = Server()
    shutdown(state, server, wait=30)
    assert not state.busy and server.should_exit


# ---- the app log says how every job ended (spec-surgeon.log) ----------------------------------------------------

def test_the_app_log_says_a_job_ended_at_the_preview_and_then_done(tmp_path, job_log):
    state, client = window(tmp_path)
    client.post("/api/attach", json={"paths": [str(document(tmp_path / "Spec.docx"))]})
    with client.stream("POST", "/api/send", json={"text": REQUEST}) as response:
        done = frames(response)[-1]
    assert done["state"]["stage"] == "preview"
    with client.stream("POST", "/api/primary", json={"token": done["state"]["action"]["token"]}) as response:
        assert frames(response)[-1]["state"]["stage"] == "done"
    lines = job_log()
    assert len(lines) == 2 and all(" INFO " in line for line in lines)
    assert "Job send ended: stage preview; stopped no; 1 model call so far; estimated cost " in lines[0]
    assert "Job primary ended: stage done; stopped no; 1 model call so far; estimated cost " in lines[1]
    assert not any("failed" in line or REQUEST in line or "FIRE SUPPRESSION" in line for line in lines)


def test_the_app_log_says_a_job_ended_on_a_question(tmp_path, job_log):
    question = "Only the body text, or everything?"
    state, client = window(tmp_path, FakeClient(resp(tool("ask_user", {"message": question,
                                                                       "questions": [question]}))))
    client.post("/api/attach", json={"paths": [str(document(tmp_path / "Spec.docx"))]})
    with client.stream("POST", "/api/send", json={"text": REQUEST}) as response:
        assert frames(response)[-1]["state"]["stage"] == "question"
    [line] = job_log()
    assert "Job send ended: stage question; stopped no; 1 model call so far; estimated cost " in line
    assert question not in line and REQUEST not in line


def test_the_app_log_says_a_job_paused_at_the_spending_check(tmp_path, job_log):
    fake = FakeClient(resp(tool("ask_user", {"questions": ["Which title?"]})),
                      resp(tool("submit_editspec", {"spec": SPEC})))
    state, client = window(tmp_path, fake)
    client.post("/api/attach", json={"paths": [str(document(tmp_path / "Spec.docx"))]})
    with client.stream("POST", "/api/send", json={"text": REQUEST}) as response:
        assert frames(response)[-1]["state"]["stage"] == "question"
    state.task.set_spending_limit(0.01)             # less than one call's largest answer: the next request pauses
    with client.stream("POST", "/api/send", json={"text": "FIRE SUPPRESSION"}) as response:
        done = frames(response)[-1]
    assert done["type"] == "done" and done["state"]["stage"] == "budget" and done["state"]["budget"]["paused"]
    assert done["state"]["budget"]["reason"] == "limit" and done["state"]["cost"]["limit"] == 0.01
    assert len(fake.requests) == 1
    first, paused = job_log()
    assert "Job send ended: stage question; stopped no" in first
    assert "Job send ended: stage budget; stopped no; 1 model call so far; estimated cost " in paused


def test_the_app_log_says_a_job_failed_or_was_stopped(tmp_path, job_log):
    import threading as th
    state, client = window(tmp_path, FakeClient())           # nothing scripted: the job fails
    client.post("/api/attach", json={"paths": [str(document(tmp_path / "Spec.docx"))]})
    with client.stream("POST", "/api/send", json={"text": REQUEST}) as response:
        assert frames(response)[-1]["type"] == "error"
    [failed] = job_log()
    assert "Job send ended: stage " in failed and "; stopped no; failed (" in failed and " INFO " in failed
    started, release = th.Event(), th.Event()

    class Slow(FakeClient):
        def _stream(self, **kw):
            started.set()
            release.wait(10)
            return super()._stream(**kw)
    state, client = window(tmp_path, Slow(resp(tool("submit_editspec", {"spec": SPEC})), live=True))
    client.post("/api/attach", json={"paths": [str(document(tmp_path / "Spec2.docx"))]})
    events = state.start_job("send", text=REQUEST)
    assert started.wait(10) and client.post("/api/stop").json() == {"ok": True}
    release.set()
    while events.get(timeout=30)["type"] not in ("done", "error"):
        pass
    state.thread.join(10)
    stopped = job_log()[-1]
    assert "Job send ended: stage " in stopped and "; stopped yes; " in stopped and "failed" not in stopped


def test_the_loopback_probe_ignores_a_system_proxy(tmp_path, monkeypatch):
    """A proxied machine (the Windows proxy, ``http_proxy``) cannot reach this computer's own server through the
    proxy, and the launch token must never be sent to it: the probe goes straight to the loopback."""
    from spec_surgeon.web.shell import free_port, serve
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9/")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9/")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    state, _client = window(tmp_path, FakeClient())
    server = serve(state, free_port())
    try:
        assert server is not None
    finally:
        server.should_exit = True


def test_an_update_downloaded_during_work_waits_for_restart(tmp_path):
    from types import SimpleNamespace
    from spec_surgeon.settings import Settings
    from spec_surgeon.web.updates import UpdateFlow
    busy = [True]
    launched, quit_calls = [], []
    flow = UpdateFlow(Settings(), launcher=lambda dl, d, log: launched.append(dl),
                      installed=lambda: True, quit_app=lambda: quit_calls.append(1), is_busy=lambda: busy[0])
    dl = SimpleNamespace(version="9.9.9", path=tmp_path / "setup.exe")
    assert flow.install_downloaded(dl) is False                 # work runs: nothing is launched, nothing quits
    assert flow.state()["state"] == "ready" and flow.ready is dl and launched == [] and quit_calls == []
    assert "Install the update once it has finished" in flow.install_ready()
    busy[0] = False
    assert flow.install_ready() == ""
    assert launched == [dl] and quit_calls == [1] and flow.state()["state"] == "installing" and flow.ready is None
    # the route: Restart to update installs the waiting download
    state, client = window(tmp_path, FakeClient())
    state.updates = UpdateFlow(Settings(), launcher=lambda dl, d, log: launched.append(dl),
                               installed=lambda: True, quit_app=lambda: quit_calls.append(2), is_busy=lambda: False)
    state.updates.ready = dl
    assert client.post("/api/update/install").json()["state"] == "installing" and quit_calls[-1] == 2


def test_a_non_ascii_token_is_refused_not_a_crash(tmp_path):
    state, _client = window(tmp_path, FakeClient())
    bare = TestClient(create_app(state))                        # no header: the query token is what is checked
    assert bare.get("/api/state", params={"token": "é"}).status_code == 401
    assert bare.get("/api/state", params={"token": "ééé" + TOKEN}).status_code == 401
    assert bare.get("/api/state", params={"token": TOKEN}).status_code == 200


def test_the_primary_action_takes_the_documents_the_user_set_aside(tmp_path):
    """The page sends ``hold``: the previewed documents marked with the red X. They go on the to-do list, the others
    get copies, the view says which and offers the reply that begins the pass over them."""
    from spec_surgeon.document_task import WORK_TODO
    state, client = window(tmp_path, FakeClient(resp(tool("submit_editspec", {"spec": SPEC})),
                                                resp(tool("submit_editspec", {"spec": SPEC}))))
    first, second = document(tmp_path / "A.docx"), document(tmp_path / "B.docx")
    client.post("/api/attach", json={"paths": [str(first), str(second)]})
    with client.stream("POST", "/api/send", json={"text": REQUEST}) as response:
        done = frames(response)[-1]["state"]
    assert done["action"]["kind"] == "approve" and done["action"]["can_hold"] is True
    assert done["todo"] == [] and done["focus"] == []
    bad = client.post("/api/primary", json={"token": done["action"]["token"], "hold": "B.docx"})
    assert bad.status_code == 400
    held = next(s["source"] for s in done["samples"] if s["name"] == "B.docx")
    with client.stream("POST", "/api/primary", json={"token": done["action"]["token"], "hold": [held]}) as response:
        done = frames(response)[-1]["state"]
    assert done["stage"] == "done" and [r["name"] for r in done["results"]] == ["A.docx"]
    assert done["todo"] == [{"path": str(second), "name": "B.docx", "why": "held",
                             "reason": "You marked its preview as needing more work."}]
    assert done["action"]["can_hold"] is False
    assert done["chips"][0] == WORK_TODO
    with client.stream("POST", "/api/send", json={"text": WORK_TODO, "chip": True}) as response:
        done = frames(response)[-1]["state"]
    assert done["focus"] == [str(second)] and done["todo"] == [] and done["action"]["kind"] == "continue"
    assert "Working on B.docx" in done["messages"][-1]["text"]


def test_the_window_applies_the_kept_fast_mode_from_its_start(tmp_path):
    """A settings.json that says fast mode is on turns it on for every call from the window's start; one that does
    not (every older file) leaves it off."""
    from spec_surgeon import llm
    session = Session(client=FakeClient(), out_root=tmp_path / "out", visual_check=False)
    WindowState(DocumentTask(session, render=False), Settings(fast_mode=True), token=TOKEN, updates=FakeUpdates())
    assert llm.FAST_MODE is True
    WindowState(DocumentTask(session, render=False), Settings(), token=TOKEN, updates=FakeUpdates())
    assert llm.FAST_MODE is False


# ---- Safe mode ------------------------------------------------------------------

def test_safe_mode_routes_the_buttons_the_mode_and_the_hand_off(tmp_path, monkeypatch):
    from make_fixtures import build, build_dirty
    monkeypatch.setenv("SPEC_SURGEON_CONFIG_DIR", str(tmp_path / "config"))
    state, client = window(tmp_path)
    catalog = client.get("/api/recipes").json()
    assert [r["id"] for r in catalog["recipes"]][:1] == ["headers_footers_template"] and catalog["groups"]
    view = client.get("/api/state").json()
    assert view["mode"] == "default" and view["modes"] == ["safe", "default"]
    assert view["template"]["kind"] == "none" and view["left_untouched"] == []
    assert client.post("/api/mode", json={"mode": "obliterate"}).status_code == 400
    view = client.post("/api/mode", json={"mode": "safe"}).json()
    assert view["mode"] == "safe" and load_settings().mode == "safe"          # kept for the next start
    good, empty = tmp_path / "21 13 13.docx", tmp_path / "empty.docx"
    good.write_bytes(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    empty.write_bytes(build_dirty("empty_body"))
    client.post("/api/attach", json={"paths": [str(good), str(empty)]})
    refused = client.post("/api/send", json={"text": "Make it Arial"})
    events = frames(refused)
    assert events[-1]["type"] == "error" and "Safe mode has no conversation" in events[-1]["message"]
    with client.stream("POST", "/api/recipe", json={"recipe": "title_property",
                                                    "inputs": {"pattern": "SECTION {number} - {title}"}}) as r:
        events = frames(r)
    done = events[-1]
    assert done["type"] == "done" and done["state"]["stage"] == "preview"
    assert done["state"]["action"]["kind"] == "approve"
    assert [i["name"] for i in done["state"]["plan"]["set_aside"]] == ["empty.docx"]
    with client.stream("POST", "/api/primary", json={"token": done["state"]["action"]["token"]}) as r:
        view = frames(r)[-1]["state"]
    assert view["stage"] == "done" and [e["name"] for e in view["left_untouched"]] == ["empty.docx"]
    assert client.post("/api/handoff", json={"paths": []}).status_code == 400
    view = client.post("/api/handoff", json={"paths": [view["left_untouched"][0]["path"]]}).json()
    assert view["mode"] == "default" and [t["name"] for t in view["todo"]] == ["empty.docx"]
    assert load_settings().mode == "default"
    bad = client.post("/api/recipe", json={"recipe": "remove_highlight", "inputs": ["not", "a", "form"]})
    assert bad.status_code == 400


def test_the_occurrences_route_takes_the_unticks_and_the_view_lists_them(tmp_path, monkeypatch):
    """Backlog F1 over the server: the view and the samples event carry the occurrence list; the route refuses a body
    that is not a list of keys; the unticks stream a job that ends with the list marked and a new token."""
    from make_fixtures import build
    monkeypatch.setenv("SPEC_SURGEON_CONFIG_DIR", str(tmp_path / "config"))
    state, client = window(tmp_path)
    client.post("/api/mode", json={"mode": "safe"})
    spec = tmp_path / "21 13 13.docx"
    spec.write_bytes(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    client.post("/api/attach", json={"paths": [str(spec)]})
    assert client.post("/api/occurrences", json={"skip": "x"}).status_code == 400
    assert client.post("/api/occurrences", json={"skip": [1]}).status_code == 400
    with client.stream("POST", "/api/recipe", json={"recipe": "find_replace",
                                                     "inputs": {"pairs": [{"find": "FM Global", "replace": "GRC"}]}}) as r:
        got = frames(r)
    samples = next(f for f in got if f["type"] == "samples")
    assert samples["occurrences"][0]["name"] == "21 13 13.docx" and len(samples["occurrences"][0]["items"]) == 4
    view = got[-1]["state"]
    items = view["occurrences"][0]["items"]
    assert view["stage"] == "preview" and all(not i["skipped"] for i in items)
    assert {"key", "part", "paragraph", "start", "end", "where", "heading", "before", "match", "after", "find",
            "replace", "skipped", "op_index"} <= set(items[0])
    token = view["action"]["token"]
    with client.stream("POST", "/api/occurrences", json={"skip": [items[1]["key"]]}) as r:
        got = frames(r)
    assert got[-1]["type"] == "done"
    view = got[-1]["state"]
    assert view["stage"] == "preview" and view["action"]["token"] != token
    assert [i["skipped"] for i in view["occurrences"][0]["items"]] == [False, True, False, False]
    assert client.get("/api/state").json()["occurrences"] == view["occurrences"]
    with client.stream("POST", "/api/occurrences", json={"skip": ["no such key"]}) as r:
        got = frames(r)
    assert got[-1]["type"] == "done" and got[-1]["state"]["messages"][-1]["text"].startswith("An occurrence to leave")


def test_the_folder_route_attaches_a_folders_documents_or_says_there_is_no_dialog(tmp_path, monkeypatch):
    from make_fixtures import build
    monkeypatch.setenv("SPEC_SURGEON_CONFIG_DIR", str(tmp_path / "config"))
    state, client = window(tmp_path)
    assert client.post("/api/attach-folder", json={}).status_code == 501        # no native dialog in a test window
    folder = tmp_path / "Specs"
    folder.mkdir()
    (folder / "21 13 13.docx").write_bytes(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    view = client.post("/api/attach-folder", json={"folder": str(folder)}).json()
    assert [a["name"] for a in view["attachments"]] == ["21 13 13.docx"]
    assert view["messages"][-1]["role"] == "app" and "1 document added from Specs." == view["messages"][-1]["text"]
    assert client.post("/api/attach-folder", json={"folder": str(tmp_path / "config")}).status_code == 400
    assert client.post("/api/attach-folder", json={"folder": str(tmp_path / "nowhere")}).status_code == 400

    class Picks:
        def __init__(self, answer):
            self.answer = answer
        def pick_folder(self, start=""):
            return self.answer
        def pick_files(self, kind):
            return None
        def confirm(self, title, message):
            return False
        def close(self):
            pass
    state.shell = Picks("")
    assert client.post("/api/attach-folder").json() == {"cancelled": True}
    state.shell = Picks(str(folder))
    assert [a["name"] for a in client.post("/api/attach-folder").json()["attachments"]] == ["21 13 13.docx"]


def test_safe_mode_routes_discard_and_undo(tmp_path, monkeypatch):
    from make_fixtures import build
    monkeypatch.setenv("SPEC_SURGEON_CONFIG_DIR", str(tmp_path / "config"))
    state, client = window(tmp_path)
    client.post("/api/mode", json={"mode": "safe"})
    spec = tmp_path / "21 13 13.docx"
    spec.write_bytes(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    client.post("/api/attach", json={"paths": [str(spec)]})
    assert client.post("/api/discard").json() == {"error": "There is no preview to discard."}
    assert client.post("/api/undo").json() == {"error": "There is nothing to undo."}

    def press():
        with client.stream("POST", "/api/recipe", json={"recipe": "remove_highlight", "inputs": {}}) as r:
            return frames(r)[-1]["state"]
    assert press()["stage"] == "preview"
    view = client.post("/api/discard").json()
    assert view["stage"] == "empty" and view["action"]["kind"] == "none" and view["samples"] == []
    view = press()
    with client.stream("POST", "/api/primary", json={"token": view["action"]["token"]}) as r:
        view = frames(r)[-1]["state"]
    assert view["working"]["generation"] == 1 and view["working"]["undo"] == "Remove highlighting"
    assert view["working"]["undo_blocked"] == ""
    view = client.post("/api/undo").json()
    assert view["working"] == {"generation": 0, "from_originals": True, "can_start_over": False,
                               "undo": "", "undo_blocked": ""}
    assert view["results"] == [] and view["attachments"][0]["current"] == str(spec)



def test_default_mode_routes_discard_and_undo(tmp_path, monkeypatch):
    """Default mode's ways back go through the same routes: the view says what Discard withdraws (``discard``:
    "preview", "request" or ""), and Undo goes back before a conversation's run."""
    monkeypatch.setenv("SPEC_SURGEON_CONFIG_DIR", str(tmp_path / "config"))
    fake = FakeClient(resp(tool("submit_editspec", {"spec": SPEC})),
                      resp(tool("ask_user", {"questions": ["Which title?"]}, "q2")),
                      resp(tool("submit_editspec", {"spec": SPEC}, "s3")))
    state, client = window(tmp_path, fake)
    spec = document(tmp_path / "Spec.docx")
    view = client.post("/api/attach", json={"paths": [str(spec)]}).json()
    assert view["discard"] == ""
    assert client.post("/api/discard").json() == {"error": "There is nothing to discard: no preview or request is "
                                                           "waiting."}

    def send(words):
        with client.stream("POST", "/api/send", json={"text": words}) as r:
            return frames(r)[-1]["state"]
    view = send(REQUEST)
    assert view["stage"] == "preview" and view["discard"] == "preview"
    view = client.post("/api/discard").json()
    assert view["stage"] == "empty" and view["discard"] == "" and view["action"]["kind"] == "none"
    assert view["messages"][-1]["text"].startswith("Preview discarded; nothing was created.")
    view = send("Which title is it?")
    assert view["stage"] == "question" and view["discard"] == "request"
    assert view["working"]["undo"] == ""
    view = client.post("/api/discard").json()
    assert view["stage"] == "empty" and view["questions"] == []
    view = send(REQUEST)
    with client.stream("POST", "/api/primary", json={"token": view["action"]["token"]}) as r:
        view = frames(r)[-1]["state"]
    assert view["working"]["generation"] == 1 and view["working"]["undo"] == REQUEST
    assert view["working"]["undo_blocked"] == ""
    view = client.post("/api/undo").json()
    assert view["working"]["generation"] == 0 and view["working"]["undo"] == "" and view["results"] == []
    assert view["attachments"][0]["current"] == str(spec)


# ---- every error is JSON (docs/WEB_API.md): the four 500s the QA pass of 2026-10-28 found -------------------------

def _health(client):
    assert client.get("/api/health").json()["ok"] is True
    assert client.get("/api/state").json()["attachments"] == []


def test_an_overlong_upload_name_is_a_400_naming_the_file_not_a_crash(tmp_path):
    """A dropped file whose name the scratch folder cannot take (260 characters: Windows' MAX_PATH, ENAMETOOLONG
    elsewhere) used to raise out of the route before its error boundary and land as a text/plain 500. Now one readable
    sentence names the file and says to shorten the name; the name itself is never shortened for the user (it would be
    the copy's planned name), nothing is attached, and the window goes on."""
    state, client = window(tmp_path)
    name = "x" * 260 + ".docx"
    r = client.post("/api/upload", files=[("files", (name, document(tmp_path / "Spec.docx").read_bytes(),
                                                     "application/octet-stream"))], data={"template": "false"})
    assert r.status_code == 400 and r.headers["content-type"].startswith("application/json")
    message = r.json()["error"]
    assert name in message and message.endswith("attach it again.") and "\n" not in message
    if sys.platform != "win32":
        assert "the path is too long" in message and "shorter name" in message
    _health(client)


def _probe_uploads(client, name: str, data: bytes):
    """The probe dropped as a document, then as the template: a refused document is attached greyed out with its
    reason (the view, 200), a refused template is a 400 with the same sentence; never a 500. Both sentences, and the
    attachments list emptied again."""
    r = client.post("/api/upload", files=[("files", (name, data, "application/octet-stream"))],
                    data={"template": "false"})
    assert r.status_code == 200, r.text
    attached = r.json()["attachments"]
    assert [(a["name"], a["state"], a["kind"]) for a in attached] == [(name, "refused", "document")]
    as_document = attached[0]["reason"]
    assert client.post("/api/remove", json={"path": attached[0]["path"]}).json()["attachments"] == []
    r = client.post("/api/upload", files=[("files", (name, data, "application/octet-stream"))],
                    data={"template": "true"})
    assert r.status_code == 400 and r.headers["content-type"].startswith("application/json"), r.text
    as_template = r.json()["error"]
    assert as_template.split(".docx", 1)[1] == as_document.split(".docx", 1)[1]   # the same sentence, the copy's name
    return [as_document, as_template]


def test_an_encrypted_or_oddly_compressed_docx_is_refused_with_a_sentence(tmp_path):
    """``zipfile`` raises RuntimeError for an encrypted member and NotImplementedError for a compression method it
    does not implement; neither was among triage's open errors, so the exception rose through ``attach`` to the route
    and out as a 500. Both are refusals now (a password-protected document says what to do about the password), as a
    document, as the template, and through ``/api/attach``; the window still answers after each."""
    from make_fixtures import build, flag_encrypted, recompress
    state, client = window(tmp_path)
    clean = build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS")
    for message in _probe_uploads(client, "locked.docx", flag_encrypted(clean)):
        assert message.startswith("locked") and message.endswith(
            ".docx is password-protected; remove the password in Word and save it again as .docx.")
    for message in _probe_uploads(client, "odd.docx", recompress(clean, 96)):
        assert message.startswith("odd") and message.endswith(
            ".docx cannot be opened as a Word package; open it in Word and save it again as .docx.")
    _health(client)
    on_disk = tmp_path / "locked.docx"
    on_disk.write_bytes(flag_encrypted(clean))
    r = client.post("/api/attach", json={"paths": [str(on_disk)], "template": True})
    assert r.status_code == 400 and r.json()["error"].startswith("locked.docx is password-protected")
    view = client.post("/api/attach", json={"paths": [str(on_disk)]}).json()
    assert [a["state"] for a in view["attachments"]] == ["refused"]
    assert view["attachments"][0]["reason"].startswith("locked.docx is password-protected")
    client.post("/api/remove", json={"path": view["attachments"][0]["path"]})
    _health(client)


def test_a_docx_without_its_content_types_manifest_is_refused_at_intake(tmp_path):
    """A package with ``[Content_Types].xml`` deleted used to attach with ordinary notes and preview as PASS (the
    verifier is right: a defect in both source and output is not new) and was only withheld at a rendered run, when
    LibreOffice could not load it. Triage refuses it on arrival, with the sentence every unopenable package gets."""
    from make_fixtures import _drop_members, build
    state, client = window(tmp_path)
    hollow = _drop_members(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"), ["[Content_Types].xml"])
    for message in _probe_uploads(client, "no-manifest.docx", hollow):
        assert message.startswith("no-manifest") and message.endswith(
            ".docx cannot be opened as a Word package; open it in Word and save it again as .docx.")
    _health(client)


JSON_ROUTES = ["/api/attach", "/api/pick", "/api/remove", "/api/send", "/api/choose", "/api/primary", "/api/recipe",
               "/api/occurrences", "/api/mode", "/api/handoff", "/api/put-back", "/api/open", "/api/settings"]
BAD_BODIES = ["null", "[]", "[1]", '"hello"', "42", "true", '{"x":', ""]


@pytest.mark.parametrize("route", JSON_ROUTES)
@pytest.mark.parametrize("body", BAD_BODIES)
def test_a_body_that_is_not_a_json_object_is_a_400_on_every_json_route(tmp_path, route, body):
    """Every JSON route read its fields with ``body.get`` straight off ``request.json()``: ``null``, a list, a string,
    a number, a boolean, JSON cut short or no body at all raised (AttributeError, JSONDecodeError) and escaped as a
    text/plain 500. Now ``json_object`` answers each with the same 400 sentence."""
    state, client = window(tmp_path)
    r = client.post(route, content=body, headers={"content-type": "application/json"})
    assert r.status_code == 400, (route, body, r.status_code, r.text)
    assert r.json()["error"] == "The request body must be a JSON object."
    _health(client)


def test_attach_refuses_paths_that_are_not_a_list_of_strings(tmp_path):
    """``{"paths": 42}`` iterated an int (TypeError, a 500); a list with a number in it would have been str()'d into a
    file name. Both are a 400 now, and the tolerant routes keep reading a bad body as an empty one."""
    state, client = window(tmp_path)
    for paths in (42, "C:/spec.docx", {"a": 1}, [1], ["ok.docx", None]):
        r = client.post("/api/attach", json={"paths": paths})
        assert r.status_code == 400 and r.json()["error"] == "paths must list the files to attach, by path.", paths
    assert client.post("/api/attach", json={}).status_code == 200                # nothing to attach: the view
    assert client.post("/api/attach-folder", content="[1]",
                       headers={"content-type": "application/json"}).status_code == 501   # a bad body is {}: no dialog
    r = client.post("/api/diagnostics", content="[1]", headers={"content-type": "application/json"})
    assert r.status_code == 200 and r.json() == {"ok": True, "taken": 0}
    assert client.post("/api/diagnostics", content='{"x":', headers={"content-type": "application/json"}).status_code == 400
    _health(client)
