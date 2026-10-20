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
from spec_surgeon.settings import Settings
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
    settings = Settings(out_root=str(tmp_path / "out"))
    state = WindowState(task, settings, token=TOKEN, updates=FakeUpdates(), version="1.6.0")
    client = TestClient(create_app(state), headers={TOKEN_HEADER: TOKEN})
    return state, client


def frames(response):
    out = []
    for line in response.iter_lines():
        if line.startswith("data: "):
            out.append(json.loads(line[6:]))
    return out


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
                "choices", "chips", "samples", "warnings", "blocking", "results", "action", "working", "budget",
                "update"):
        assert key in view, key
    assert view["stage"] == "empty" and view["plan"] is None and view["action"]["kind"] == "none"
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
    assert s["key_present"] is True and "steps_per_pause" in s          # a scripted client counts as connected
    s = client.post("/api/settings", json={"api_key": "sk-ant-test", "remember": False}).json()
    assert s["origin"] == "session"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
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
        sys.exit(run_window(WindowState(task, Settings(out_root=sys.argv[1]), token=new_token())))
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
