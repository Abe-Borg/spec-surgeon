"""Exercise the real installed app without an API key or a network.

``gui`` mode (default): the Qt window (native ``windows`` platform) previews and runs a spec through its buttons,
the process pool and the isolated sandbox work, bundled resources are present, Credential Manager works, and the
updater downloads and verifies an installer from a canned release (``file://`` assets) and refuses a tampered one.

``update`` mode: the real in-app update hand-off — the window starts the CI-built installer detached with the real
switches (/SILENT … /RELAUNCH=1) and quits (Windows releases the app mutex as the process ends). validate_installer.ps1
then checks that Setup
waited, installed over this copy, and reopened the app.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback


def _gui(request, result):
    import anthropic
    import pymupdf
    import pythoncom  # noqa: F401
    import win32com.client  # noqa: F401
    from PySide6.QtWidgets import QStyleFactory
    from spec_surgeon import __version__, catalog, credentials, updater, winapp
    from spec_surgeon import SKILL_DIR
    from spec_surgeon.codegen import _examples, trial
    from spec_surgeon.gui import Session, build_window
    from spec_surgeon.ui.app import RecordingPrompts, qt_app
    from spec_surgeon.verify.xsd import SCHEMA_DIR

    assert getattr(sys, "frozen", False), "Test must run from the installed executable"
    assert len(list(SCHEMA_DIR.rglob("*.xsd"))) == 39
    assert (SKILL_DIR / "SKILL.md").is_file()
    assert "set_page_setup.py" in _examples()
    examples = catalog.worked_examples()
    assert any(e["request"] == "remove all headers and footers" for e in examples)
    assert updater.is_installed_build(), "unins000.exe must sit beside the installed app"

    app = qt_app()
    assert app.platformName() == "windows", app.platformName()
    assert "windows11" in [k.lower() for k in QStyleFactory.keys()], QStyleFactory.keys()
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
    session = Session(out_root=Path(request["output_dir"]))
    session.load_spec(catalog.EXAMPLES_DIR / "remove-all-headers-footers.json")
    w = build_window(session, prompts=RecordingPrompts())
    ui = w._spec_surgeon
    w.show()
    ui["vars"]["folder"].set(str(inputs))
    ui["vars"]["render"].set(False)
    ui["buttons"]["preview"].click()
    log = ui["wait"](120)
    assert ui["status"].get() == "Ready.", log
    assert "2 plan cleanly" in log, log
    ui["buttons"]["run"].click()
    log = ui["wait"](180)
    assert ui["status"].get() == "Ready.", log
    assert "PASS 2" in log, log
    assert ui["results_model"].badges() == ["pass", "pass"]
    w.grab().save(str(Path(request["report"]).with_name("gui-windows.png")))
    assert len(list((session.last_run_dir / "outputs").glob("*.docx"))) == 2
    record_files = list((session.last_run_dir / "records").glob("*.json"))
    assert len(record_files) == 2
    for path in record_files:
        record = json.loads(path.read_text(encoding="utf-8"))
        assert not any(c["status"] == "FAIL" for c in record["checks"]), record
        assert any(c["id"] == "V5a" and c["status"] == "PASS" for c in record["checks"]), record
    w.force_close = True
    w.close()

    generated = trial(request["generated_source"], sources[0], {}, {"twips": 900},
                      Path(request["output_dir"]) / "Isolated generated operation")
    assert generated.passed, generated.problems
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}

    _updater_offline(updater)
    result.update({"ok": True, "version": __version__, "gui_preview_run": "PASS 2",
                   "process_pool": "PASS", "isolated_generated_op": "PASS",
                   "schemas": 39, "sources_unchanged": True, "qt_style": app.style().name(),
                   "updater_offline": "PASS", "credential_manager": "PASS",
                   "anthropic": anthropic.__version__, "pymupdf": pymupdf.VersionBind, "word_modules": True})


def _updater_offline(updater):
    """Download + verify from a canned release served from local files; a tampered byte is refused."""
    import urllib.request
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
    """Hand over to the real installer the way Install now does, then quit."""
    from spec_surgeon import updater
    from spec_surgeon.gui import Session, build_window
    from spec_surgeon.ui.app import RecordingPrompts
    installer = Path(request["installer"])
    dl = updater.DownloadedInstaller(installer, request["sha256"], request["version"])
    w = build_window(Session(out_root=Path(request["output_dir"])), prompts=RecordingPrompts())
    started = []

    def launcher(dl, install_dir, log):
        proc = updater.launch_installer(dl, install_dir=install_dir, log=Path(request["install_log"]))
        started.append(proc.pid)
        return proc
    w.updates.launcher = launcher
    quit_called = []
    w.quit_for_update = lambda: quit_called.append(True)
    assert w.updates.install_downloaded(dl), "the installer was not started"
    assert quit_called and started
    result.update({"ok": True, "installer_pid": started[0],
                   "pending_update": w.settings.pending_update})


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
