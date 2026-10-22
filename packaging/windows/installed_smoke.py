"""Exercise the real installed app without an API key, a network or a browser.

``gui`` mode (default): the window's server (``spec_surgeon.web``) is served on a free port from the installed
executable and driven over HTTP the way the page drives it — attach, send, read the job stream to ``done``, create
the copies, About, updates — with a scripted offline model; pywebview's Windows backend (pythonnet, WinForms, the
WebView2 interop) loads and finds the WebView2 runtime; the built page is in the bundle; the process pool and the
isolated sandbox work; bundled resources are present; Credential Manager works; and the updater downloads and
verifies an installer from a canned release (``file://`` assets) and refuses a tampered one. No window is opened.

``update`` mode: the real in-app update hand-off — the update flow starts the CI-built installer detached with the
real switches (/SILENT … /RELAUNCH=1) and quits (Windows releases the app mutex as the process ends).
validate_installer.ps1 then checks that Setup waited, installed over this copy, and reopened the app.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
import urllib.request


def _check_schemas(folder, expected):
    actual = {p.relative_to(folder).as_posix() for p in folder.rglob("*.xsd")}
    expected = set(expected)
    assert actual == expected, (f"Schema inventory mismatch: missing={sorted(expected - actual)}, "
                                f"unexpected={sorted(actual - expected)}")
    return len(actual)


def _check_worked_examples(expected):
    from spec_surgeon import catalog

    actual = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in catalog.EXAMPLES_DIR.glob("*.json")}
    assert expected and actual == expected, "Packaged worked examples differ from the source inventory"
    assert catalog.worked_examples() == [json.loads(p.read_text(encoding="utf-8"))
                                         for p in sorted(catalog.EXAMPLES_DIR.glob("*.json"))]
    return len(actual)


def _offline_client(spec):
    """One scripted chat turn, using no credentials or network even in an installed build."""
    from contextlib import nullcontext
    from types import SimpleNamespace as NS
    from spec_surgeon.llm import DEFAULT_MODEL

    response = NS(content=[NS(type="tool_use", id="smoke", name="submit_editspec", input={"spec": spec})],
                  stop_reason="tool_use", stop_details=None, model=DEFAULT_MODEL,
                  usage=NS(input_tokens=0, output_tokens=0, cache_read_input_tokens=0,
                           cache_creation_input_tokens=0, iterations=None))
    calls = []
    def stream(**kwargs):
        assert not calls, "The smoke test unexpectedly requested another model turn"
        calls.append(True)
        return nullcontext(NS(get_final_message=lambda: response))
    return NS(beta=NS(messages=NS(stream=stream)))


class _Api:
    """The page's side of docs/WEB_API.md over urllib: JSON routes and the job stream."""

    def __init__(self, port, token):
        self.base = f"http://127.0.0.1:{port}"
        self.token = token

    def _request(self, route, body=None, timeout=30):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.base + route, data=data, method="POST" if body is not None else "GET",
                                     headers={"X-Spec-Surgeon-Token": self.token, "Content-Type": "application/json"})
        return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 — our own loopback server

    def get(self, route):
        with self._request(route) as resp:
            assert resp.status == 200, (route, resp.status)
            return json.loads(resp.read().decode("utf-8"))

    def post(self, route, body):
        with self._request(route, body) as resp:
            assert resp.status == 200, (route, resp.status)
            return json.loads(resp.read().decode("utf-8"))

    def stream(self, route, body, timeout):
        """Every ``data:`` frame of a job stream, up to and including ``done`` or ``error``."""
        events = []
        with self._request(route, body, timeout=timeout) as resp:
            assert resp.status == 200 and resp.headers.get("Content-Type", "").startswith("text/event-stream"), route
            for raw in resp:
                line = raw.decode("utf-8").rstrip("\r\n")
                if line.startswith("data: "):
                    event = json.loads(line[6:])
                    events.append(event)
                    if event["type"] in ("done", "error"):
                        break
        assert events and events[-1]["type"] in ("done", "error"), events[-1:]
        return events


def _conversation_run(api, sources, version):
    """Exercise the installed window as the page does: attachments, chat, the job stream, approval, verified copies."""
    view = api.get("/api/state")
    assert view["version"] == version and view["stage"] == "empty" and view["busy"] is False, view
    view = api.post("/api/attach", {"paths": [str(p) for p in sources]})
    assert len(view["attachments"]) == len(sources), view["attachments"]
    assert all(a["state"] in ("ok", "note") and a["kind"] == "document" for a in view["attachments"]), view["attachments"]
    events = api.stream("/api/send", {"text": "remove all headers and footers"}, timeout=120)
    assert events[-1]["type"] == "done", events[-1]
    view = events[-1]["state"]
    assert view["stage"] == "preview" and view["action"]["kind"] == "approve" and not view["results"], view
    assert view["plan"] and view["plan"]["groups"], view["plan"]
    events = api.stream("/api/primary", {"token": view["action"]["token"]}, timeout=180)
    assert events[-1]["type"] == "done", events[-1]
    view = events[-1]["state"]
    assert view["stage"] == "done" and len(view["results"]) == len(sources), view
    assert all(Path(r["output"]).is_file() for r in view["results"]), view["results"]
    return view


def _gui(request, result):
    import anthropic
    import fastapi  # noqa: F401
    import pymupdf
    import pythoncom  # noqa: F401
    import uvicorn  # noqa: F401
    import webview
    import win32com.client  # noqa: F401
    from webview.guilib import initialize as initialize_gui
    from spec_surgeon import __version__, catalog, credentials, updater, winapp
    from spec_surgeon import SKILL_DIR
    from spec_surgeon.codegen import _examples, trial
    from spec_surgeon.document_task import DocumentTask
    from spec_surgeon.gui import Session
    from spec_surgeon.settings import Settings
    from spec_surgeon.verify.xsd import SCHEMA_DIR
    from spec_surgeon.web.server import STATIC, WindowState, new_token
    from spec_surgeon.web.shell import free_port, serve

    assert getattr(sys, "frozen", False), "Test must run from the installed executable"
    schema_count = _check_schemas(SCHEMA_DIR, request["schemas"])
    example_count = _check_worked_examples(request["worked_examples"])
    assert (SKILL_DIR / "SKILL.md").is_file()
    assert "set_page_setup.py" in _examples()
    examples = catalog.worked_examples()
    assert any(e["request"] == "remove all headers and footers" for e in examples)
    assert updater.is_installed_build(), "unins000.exe must sit beside the installed app"
    assert (STATIC / "index.html").is_file(), f"the built page must ship in the bundle ({STATIC})"
    # PyMuPDF 2.0 loads a layout-analysis model on import; pyproject pins below it so the window never carries one
    assert "onnxruntime" not in sys.modules, f"PyMuPDF {pymupdf.VersionBind} loaded its layout model"

    # pywebview's Windows backend without opening a window: importing it loads pythonnet, WinForms and the WebView2
    # interop assemblies and reads the WebView2 runtime's version from the registry (``renderer``); ``initialize``
    # then only sets the WinForms application defaults.
    gui = initialize_gui()
    assert gui.renderer == "edgechromium", f"pywebview would not use WebView2 here: {gui.renderer}"
    assert winapp.mutex_exists(), "the app mutex must be held"
    assert winapp.get_app_user_model_id() == winapp.AUMID

    target = "Spec Surgeon/installer-smoke-test"
    credentials.save("not-a-real-key", target)
    try:
        assert credentials.load(target) == "not-a-real-key"
    finally:
        assert credentials.delete(target)

    inputs = Path(request["input_dir"])
    sources = sorted(inputs.glob("*.docx"))
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    spec = json.loads((catalog.EXAMPLES_DIR / "remove-all-headers-footers.json").read_text(encoding="utf-8"))
    session = Session(out_root=Path(request["output_dir"]), client=_offline_client(spec), visual_check=False)
    task = DocumentTask(session, render=False)
    state = WindowState(task, Settings(out_root=str(session.out_root)), token=new_token(), version=__version__)
    port = free_port()
    server = serve(state, port)
    try:
        api = _Api(port, state.token)
        assert api.get("/api/health") == {"ok": True, "version": __version__}
        _conversation_run(api, sources, __version__)
        about = api.get("/api/about")
        assert about["version"] == __version__ and "MIT License" in about["license"] and about["credits"], about
        assert any("pywebview" in line for line in about["credits"]), about["credits"]
        update = api.get("/api/update")
        assert update["state"] == "none", update                  # no update flow is attached to the smoke's window
        settings = api.get("/api/settings")
        assert settings["can_remember"] is True and settings["out_root"] == str(session.out_root), settings
        # the page's own reports reach the frozen app's watch (web/watch.py), as the WebView2 page sends them
        taken = api.post("/api/diagnostics", {"reports": [{"kind": "loaded", "visible": True, "user_agent": "smoke"},
                                                          {"kind": "beat", "visible": True}]})
        assert taken == {"ok": True, "taken": 2}, taken
    finally:
        server.should_exit = True
        if state.thread is not None:
            state.thread.join(timeout=30)
    assert len(list((session.last_run_dir / "outputs").glob("*.docx"))) == 2
    record_files = list((session.last_run_dir / "records").glob("*.json"))
    assert len(record_files) == 2
    for path in record_files:
        record = json.loads(path.read_text(encoding="utf-8"))
        assert not any(c["status"] == "FAIL" for c in record["checks"]), record
        assert any(c["id"] == "V5a" and c["status"] == "PASS" for c in record["checks"]), record

    generated = trial(request["generated_source"], sources[0], {}, {"twips": 900},
                      Path(request["output_dir"]) / "Isolated generated operation")
    assert generated.passed, generated.problems
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}

    _updater_offline(updater)
    result.update({"ok": True, "version": __version__, "gui_preview_run": "PASS 2",
                   "process_pool": "PASS", "isolated_generated_op": "PASS",
                   "schemas": schema_count, "worked_examples": example_count,
                   "sources_unchanged": True, "page_bundled": True,
                   "window_backend": gui.renderer, "pywebview": getattr(webview, "__version__", "?"),
                   "updater_offline": "PASS", "credential_manager": "PASS", "page_diagnostics": "PASS",
                   "anthropic": anthropic.__version__, "pymupdf": pymupdf.VersionBind, "word_modules": True})


def _updater_offline(updater):
    """Download + verify from a canned release served from local files; a tampered byte is refused."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        payload = os.urandom(1024 * 1024)
        name = "SpecSurgeon-9.9.9-Setup-x64.exe"
        assets = tmp / "assets"
        assets.mkdir()
        (assets / name).write_bytes(payload)
        sha = hashlib.sha256(payload).hexdigest()
        (assets / "SHA256SUMS").write_text(f"{sha}  {name}\n", encoding="utf-8")
        release = {"tag_name": "v9.9.9", "draft": False, "prerelease": False, "html_url": "https://example.invalid",
                   "body": "notes", "assets": [
                       {"name": name, "size": len(payload), "digest": f"sha256:{sha}",
                        "browser_download_url": (assets / name).as_uri()},
                       {"name": "SHA256SUMS", "browser_download_url": (assets / "SHA256SUMS").as_uri()}]}
        local = urllib.request.build_opener()

        def opener(req, timeout, redirect=True):
            return local.open(req, timeout=timeout)
        info = updater.parse_release(release)
        dl = updater.download_installer(info, tmp / "updates", opener=opener)
        assert dl.sha256 == sha and dl.path.read_bytes() == payload and not list((tmp / "updates").glob("*.part"))
        (assets / name).write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))
        try:
            updater.download_installer(info, tmp / "tampered", opener=opener)
        except updater.UpdateError as e:
            assert e.kind == "integrity", e.kind
        else:
            raise AssertionError("a tampered installer was accepted")
        assert not list((tmp / "tampered").glob("*"))
        exe_dir = Path(sys.executable).resolve().parent
        cmd = updater.installer_cmdline(dl.path, install_dir=exe_dir, log=tmp / "x.log")
        assert "/RELAUNCH=1" in cmd and f'/DIR="{exe_dir}"' in cmd


def _update(request, result):
    """Hand over to the real installer the way Install does, then quit."""
    from spec_surgeon import updater
    from spec_surgeon.settings import Settings
    from spec_surgeon.web.updates import UpdateFlow
    installer = Path(request["installer"])
    dl = updater.DownloadedInstaller(installer, request["sha256"], request["version"])
    started = []

    def launcher(dl, install_dir, log):
        proc = updater.launch_installer(dl, install_dir=install_dir, log=Path(request["install_log"]))
        started.append(proc.pid)
        return proc
    quit_called = []
    flow = UpdateFlow(Settings(out_root=request["output_dir"]), launcher=launcher,
                      quit_app=lambda: quit_called.append(True))
    assert flow.install_downloaded(dl), "the installer was not started"
    assert quit_called and started and flow.state()["state"] == "installing"
    result.update({"ok": True, "installer_pid": started[0],
                   "pending_update": flow.settings.pending_update})


def main(request_path):
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    report = Path(request["report"])
    result = {"ok": False, "mode": request.get("mode", "gui")}
    os.environ.setdefault("SPEC_SURGEON_NO_UPDATE_CHECK", "1")
    if request.get("config_dir"):
        os.environ["SPEC_SURGEON_CONFIG_DIR"] = request["config_dir"]
    try:
        from spec_surgeon.winapp import hold_app_mutex, set_app_user_model_id
        set_app_user_model_id()
        hold_app_mutex()                              # as the real launcher does after the smoke-test dispatch
        if result["mode"] == "update":
            _update(request, result)
        else:
            _gui(request, result)
    except Exception:
        result["error"] = traceback.format_exc()
    finally:
        report.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return 0 if result["ok"] else 1
