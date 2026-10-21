"""The window's logic (``gui.Session``) end to end with a scripted client — preview and run, stale previews, the
conversation (Claude's questions and choices, replies, a custom transform), copies only, plain errors — plus what the
window keeps beside the chat without a toolkit (the update flow, About over the server; the server itself is driven
in ``test_web_server.py``), and that the web stack never leaks into the engine."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

import pytest

from fake_claude import api_error, raise_on_enter
from make_fixtures import build, build_reference_spec
from spec_surgeon import vision
from spec_surgeon.editspec import load_spec
from spec_surgeon.gui import Session, TransformFailed
from test_codegen import GOOD_OP
from test_compiler import FakeClient, resp, tool, generate_reviewed_custom
from test_execution_delivery_safety import document, recipe

SPECS = Path(__file__).resolve().parent.parent / "examples" / "specs"
WEB_STACK = ("fastapi", "starlette", "uvicorn", "webview", "multipart", "python_multipart")   # the window's own
REMOVE = json.loads((SPECS / "remove-all-headers-footers.json").read_text(encoding="utf-8"))


@pytest.fixture
def folder(tmp_path) -> Path:
    d = tmp_path / "Specs"
    d.mkdir()
    for i in range(2):
        (d / f"21 0{i} 00 – SPEC {i}.docx").write_bytes(build(f"21 0{i} 00", f"SPEC {i}"))
    return d


def documents(folder: Path) -> list:
    """The folder's Word files in the order the old folder picker gave them (``expand_inputs`` sorted a folder)."""
    return sorted(folder.glob("*.docx"))


def load(session: Session, name: str) -> None:
    """A saved EditSpec becomes the session's outcome, as the old Load-a-spec button did (nothing is compiled)."""
    path = SPECS / name
    session._set_outcome(spec=load_spec(path), source=str(path))


def log_to(lines):
    return lambda m: lines.append(m)


def test_request_preview_run(folder, tmp_path):
    s = Session(documents=documents(folder), out_root=tmp_path / "out", client=FakeClient(
        resp(tool("submit_editspec", {"spec": REMOVE}))))
    lines = []
    with pytest.raises(ValueError, match="Preview first"):
        s.run(False, False, log_to(lines))
    assert s.compile("remove all headers and footers", log_to(lines)) == "Compiled."
    text = s.make_preview(render=False, log=log_to(lines))
    assert "2 plan cleanly" in text and s.preview.token in text
    assert s.run_blocked_reason() == ""
    before = {f: f.read_bytes() for f in folder.glob("*.docx")}
    out = s.run(in_place=False, render=False, log=log_to(lines))
    assert "PASS 2" in out and "your files were not changed" in out
    assert {f: f.read_bytes() for f in folder.glob("*.docx")} == before
    assert (s.last_run_dir / "outputs").is_dir()


def test_run_refuses_after_a_file_changed(folder, tmp_path):
    s = Session(documents=documents(folder), out_root=tmp_path / "out")
    load(s, "remove-all-headers-footers.json")
    s.make_preview(render=False, log=lambda m: None)
    f = documents(folder)[0]
    os.utime(f, ns=(f.stat().st_atime_ns, f.stat().st_mtime_ns + 10_000_000))
    with pytest.raises(ValueError, match="changed since the preview"):
        s.run(False, False, lambda m: None)


def test_the_session_writes_edited_copies_only(folder, tmp_path):
    """The one desktop never overwrites an original: in-place delivery is refused after the preview, and nothing
    runs (the engine's guarded write-back is covered in test_engine / test_editspec / test_chain)."""
    s = Session(documents=documents(folder), out_root=tmp_path / "out")
    load(s, "remove-all-headers-footers.json")
    s.make_preview(render=False, log=lambda m: None)
    before = {f: f.read_bytes() for f in folder.glob("*.docx")}
    with pytest.raises(ValueError, match="Edited copies only"):
        s.run(in_place=True, render=False, log=lambda m: None)
    assert {f: f.read_bytes() for f in folder.glob("*.docx")} == before and s.last_run_dir is None
    out = s.run(in_place=False, render=False, log=lambda m: None)
    assert "PASS 2" in out and {f: f.read_bytes() for f in folder.glob("*.docx")} == before


def test_questions_and_unsupported(folder, tmp_path):
    s = Session(documents=documents(folder), out_root=tmp_path / "out",
                client=FakeClient(resp(tool("ask_user", {"questions": ["Which sections?"]}))))
    msg = s.compile("remove some headers", lambda m: None)
    assert "reply in the chat" in msg and s.questions == ["Which sections?"] and s.spec is None
    assert (s.chat_dir / "conversation.json").is_file() and (s.chat_dir / "compile.json").is_file()
    unsupported = {"request": "r", "summary": "s", "unsupported": "no op", "proposal": "set_header_distance"}
    s.client = FakeClient(resp(tool("submit_editspec", {"spec": unsupported})),
                          resp(tool("need_user", {"question": "What outcome should the header have?"})))
    msg = s.compile("x", lambda m: None)
    assert "reply in the chat" in msg and s.questions == ["What outcome should the header have?"]
    assert s.conversation.recovery_active  # broader planning happens in this same conversation
    s.client = FakeClient(resp(tool("submit_editspec", {"spec": unsupported, "custom_transform": "possible"})))
    msg = s.compile("Set every section's header distance to 900 twips.", lambda m: None)
    assert "custom transform" in msg and [c.kind for c in s.turn.choices] == ["custom_transform"]
    s.client = FakeClient(
                          resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}})))
    assert "passed verification" in generate_reviewed_custom(s)
    assert "passed verification on the sample" in s.conversation.notes[-1]         # Claude hears what happened
    s.make_preview(render=False, log=lambda m: None)
    out = s.run(False, False, lambda m: None)
    assert "2 of 2 file(s) PASS" in out


def test_a_reply_replaces_the_custom_transform_and_voids_old_previews(folder, tmp_path):
    """Plan review blocker: after a custom transform, a reply that yields a spec must replace it (Run runs the spec,
    after its preview), and a reply that only asks a question leaves nothing runnable."""
    unsupported = {"request": "r", "summary": "s", "unsupported": "no op", "proposal": "set_header_distance"}
    s = Session(documents=documents(folder), out_root=tmp_path / "out",
                client=FakeClient(resp(tool("submit_editspec", {"spec": unsupported, "custom_transform": "possible"})),
                                  resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}})),
                                  resp(tool("submit_editspec", {"spec": REMOVE}, "s2")),
                                  resp(tool("ask_user", {"questions": ["Really?"]}, "a3"))))
    s.compile("Set every section's header distance to 900 twips.", lambda m: None)
    generate_reviewed_custom(s)
    assert s.generated is not None
    assert s.reply("actually, just remove all headers and footers") == "Compiled."
    assert s.generated is None and s.spec == dict(REMOVE, request="Set every section's header distance to 900 twips.")
    assert "2 plan cleanly" in s.make_preview(render=False, log=lambda m: None)
    assert "THE PREVIEW OF YOUR SPEC" in s.conversation.notes[-1]
    s.reply("hmm")
    assert s.spec is None and s.preview is None and s.questions == ["Really?"]
    with pytest.raises(ValueError, match="Preview first"):
        s.run(False, False, lambda m: None)


def test_a_reply_refuses_when_the_documents_changed(folder, tmp_path):
    s = Session(documents=documents(folder), out_root=tmp_path / "out",
                client=FakeClient(resp(tool("ask_user", {"questions": ["Which sections?"]}))))
    s.compile("remove some headers", lambda m: None)
    new = folder / "21 99 00 – NEW.docx"
    new.write_bytes(build("21 99 00", "NEW"))
    s.documents.append(new)                                   # another document attached mid-conversation
    with pytest.raises(ValueError, match="describe the change again"):
        s.reply("all of them")


def test_errors_are_plain(tmp_path):
    with pytest.raises(ValueError, match="Add the Word documents you want to edit"):
        Session(out_root=tmp_path).files()
    notes = tmp_path / "notes.txt"
    notes.write_text("not a document", encoding="utf-8")
    with pytest.raises(ValueError, match=r"Attach a Word \.docx document: notes\.txt"):
        Session(documents=[notes], out_root=tmp_path).files()
    with pytest.raises(ValueError, match="An attached document is missing"):
        Session(documents=[tmp_path / "gone.docx"], out_root=tmp_path).files()
    with pytest.raises(ValueError, match="Type what you want done"):
        Session(out_root=tmp_path).compile("  ", lambda m: None)


def test_the_template_is_neither_previewed_nor_run(folder, tmp_path):
    """Codex review on PR #4: preview and Run must use the same filtered file set — the template is excluded from
    both (else Run always refused, or edited the template). Attached among the documents, it is refused by name."""
    from make_fixtures import build_template
    tpl = folder / "00 00 00 TEMPLATE.docx"
    tpl.write_bytes(build_template())
    with pytest.raises(ValueError, match="TEMPLATE.docx is also a copy of the reference template"):
        Session(documents=documents(folder), template=tpl, out_root=tmp_path / "out").files()
    s = Session(documents=[f for f in documents(folder) if f != tpl], template=tpl, out_root=tmp_path / "out")
    load(s, "remove-all-headers-footers.json")
    s.make_preview(render=False, log=lambda m: None)
    assert len(s.preview.batch_plan.files) == 2
    before = tpl.read_bytes()
    out = s.run(in_place=False, render=False, log=lambda m: None)
    assert "2 file(s): PASS 2" in out and tpl.read_bytes() == before


def test_generated_run_refuses_unpreviewed_files(folder, tmp_path):
    """Codex review on PR #4: an AI-written transform runs only on the files it was tested on."""
    unsupported = {"request": "r", "summary": "s", "unsupported": "no op", "proposal": "set_header_distance"}
    s = Session(documents=documents(folder), out_root=tmp_path / "out",
                client=FakeClient(resp(tool("submit_editspec", {"spec": unsupported, "custom_transform": "possible"})),
                                  resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}}))))
    s.compile("Set every section's header distance to 900 twips.", lambda m: None)
    assert "passed verification" in generate_reviewed_custom(s)
    s.make_preview(render=False, log=lambda m: None)
    new = folder / "21 99 00 – NEW.docx"
    new.write_bytes(build("21 99 00", "NEW"))
    s.documents.append(new)
    with pytest.raises(ValueError, match="since the AI-written transform was tested"):
        s.run(False, False, lambda m: None)


def test_generated_approval_identity_includes_approved_goal_and_bound_targets(tmp_path):
    from types import SimpleNamespace
    from spec_surgeon.preview import approval_token, generated_key
    source = tmp_path / "source.docx"
    source.write_bytes(b"pinned source")
    generated = SimpleNamespace(source="approved code", select={}, params={},
        goal_contract={"kind": "document_effects", "effects": [{"id": "title", "type": "format"}]},
        goal_bindings={str(source): {"candidate_id": "first"}})
    original = approval_token(generated_key(generated), [source])
    assert Session._generated_key(generated) == generated_key(generated)
    generated.goal_bindings[str(source)]["candidate_id"] = "other"
    assert approval_token(generated_key(generated), [source]) != original
    generated.goal_bindings[str(source)]["candidate_id"] = "first"
    generated.goal_contract["effects"][0]["type"] = "delete_paragraphs"
    assert approval_token(generated_key(generated), [source]) != original


# ---- beside the chat, without a toolkit: the update flow (web.updates) and About (/api/about) -----------------

def _release(version="9.9.9"):
    from spec_surgeon.updater import Asset, UpdateInfo
    name = f"SpecSurgeon-{version}-Setup-x64.exe"
    return UpdateInfo(version=version, tag=f"v{version}", notes="## New\n- a thing", html_url="https://example.invalid",
                      installer=Asset(name, "https://example.invalid/" + name, 10),
                      sums=Asset("SHA256SUMS", "https://example.invalid/SHA256SUMS"))


def _checker(results):
    """Answers each check with the next item: a CheckResult, or an exception to raise."""
    from spec_surgeon.updater import CheckResult, current_version
    calls = []

    def check(manual, skipped):
        calls.append((manual, skipped))
        item = results.pop(0) if len(results) > 1 else results[0]
        if isinstance(item, Exception):
            raise item
        if item == "update":
            info = _release()
            if skipped == info.version and not manual:
                return CheckResult("current", current_version(), info, skipped=True)
            return CheckResult("update", current_version(), info, skipped=skipped == info.version)
        return CheckResult(item, current_version())
    check.calls = calls
    return check


def _settle(flow, done, timeout=10.0):
    """Wait for the flow's check to land (it runs on a daemon thread, as in the window) and return the state."""
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = flow.state()
        if done(state) and not flow.checking:
            return state
        time.sleep(0.01)
    raise AssertionError(f"the update check did not settle: {flow.state()}")


def test_update_flow_skip_and_manual_check(tmp_path, monkeypatch):
    from spec_surgeon.settings import Settings, load_settings
    from spec_surgeon.web.updates import UpdateFlow
    monkeypatch.setenv("SPEC_SURGEON_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("SPEC_SURGEON_NO_UPDATE_CHECK", raising=False)
    checker = _checker(["update"])
    flow = UpdateFlow(Settings(), checker=checker)
    assert flow.state()["state"] == "none"
    assert flow.auto_check() is True
    shown = _settle(flow, lambda s: s["state"] == "available")
    assert shown["version"] == "9.9.9" and shown["notes"].startswith("## New") and "You have" in shown["text"]
    assert flow.settings.last_update_check > 0
    assert flow.auto_check() is False                           # once a day
    flow.skip()
    assert flow.state()["state"] == "none" and load_settings().skipped_version == "9.9.9"
    flow.settings.last_update_check = 0
    assert flow.auto_check() is True
    _settle(flow, lambda s: True)
    assert flow.state()["state"] == "none"                      # a skipped version stays quiet on automatic checks
    assert flow.check(manual=True) is True
    shown = _settle(flow, lambda s: s["state"] == "available")
    assert "skip" in shown["text"] and checker.calls[-1] == (True, "9.9.9")


def test_a_manual_check_clears_the_last_answer_until_its_own_lands(tmp_path, monkeypatch):
    """The About page shows ``manual_result`` once a check it started answers: the previous answer must not stand in
    for it while the new check runs, or a second "up to date" would read as instant."""
    import threading
    from spec_surgeon.settings import Settings
    from spec_surgeon.updater import CheckResult, current_version
    from spec_surgeon.web.updates import UpdateFlow
    monkeypatch.setenv("SPEC_SURGEON_CONFIG_DIR", str(tmp_path / "config"))
    gate = threading.Event()

    def checker(manual, skipped):
        gate.wait(5)
        return CheckResult("current", current_version())
    flow = UpdateFlow(Settings(), checker=checker)
    gate.set()
    assert flow.check(manual=True) is True
    first = _settle(flow, lambda s: bool(s["manual_result"]))["manual_result"]
    assert "up to date" in first
    gate.clear()
    assert flow.check(manual=True) is True
    assert flow.state()["manual_result"] == ""                 # cleared the moment the check starts
    gate.set()
    assert _settle(flow, lambda s: bool(s["manual_result"]))["manual_result"] == first
    gate.clear()
    assert flow.check(manual=False) is True
    assert flow.state()["manual_result"] == first               # an automatic check never touches it
    gate.set()
    _settle(flow, lambda s: True)


def test_about_says_who_made_it_the_license_and_how_to_reach_him(tmp_path):
    from fastapi.testclient import TestClient
    from spec_surgeon import __version__, about
    from spec_surgeon.document_task import DocumentTask
    from spec_surgeon.settings import Settings
    from spec_surgeon.web.server import TOKEN_HEADER, WindowState, create_app
    state = WindowState(DocumentTask(Session(out_root=tmp_path / "out"), render=False), Settings(), token="t")
    info = TestClient(create_app(state), headers={TOKEN_HEADER: "t"}).get("/api/about").json()
    assert info["name"] == about.APP_NAME and info["version"] == __version__ and info["github"] == about.GITHUB_URL
    plain = "\n".join(info["credits"])
    assert plain.startswith(f"{about.APP_NAME} {__version__}")
    assert about.COPYRIGHT_LINE in plain and about.LICENSE_NAME in plain and about.AUTHOR in plain
    for line in about.LICENSE_SUMMARY:
        assert line in plain
    for value in (about.EMAIL, about.GITHUB_URL, about.LINKEDIN_URL):
        assert value in plain
    for name, lic, _ in about.THIRD_PARTY:
        assert f"{name}: {lic}" in plain
    assert info["license"].strip() == about.LICENSE_TEXT.strip()
    # The window's About page reads the same values field by field, never from a copy of its own.
    assert (info["author"], info["email"], info["linkedin"]) == (about.AUTHOR, about.EMAIL, about.LINKEDIN_URL)
    assert info["copyright"] == about.COPYRIGHT_LINE and info["license_name"] == about.LICENSE_NAME
    assert info["license_summary"] == about.LICENSE_SUMMARY
    assert info["components"] == [{"name": n, "license": lic, "url": u} for n, lic, u in about.THIRD_PARTY]
    from spec_surgeon import llm
    assert info["model"] == llm.DEFAULT_MODEL


# ---- the web stack stays inside the window -----------------------------------------------------------------------

def test_without_the_gui_extra_the_window_explains_how_to_install_it():
    import subprocess
    import sys
    code = "import sys; sys.modules['fastapi'] = None; from spec_surgeon.gui import main; sys.exit(main())"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and 'pip install "spec-surgeon[gui]"' in r.stderr


def test_without_the_gui_extra_a_message_box_only_when_there_is_no_console(monkeypatch, capsys):
    """The native message box is modal: with a stream to print to it must never appear (it hung the Windows CI
    run of the subprocess test above); only pythonw (no stderr) gets it."""
    import sys
    from spec_surgeon import gui, winapp
    shown = []
    monkeypatch.setattr(winapp, "message_box", lambda title, text: shown.append(text))
    monkeypatch.setitem(sys.modules, "fastapi", None)
    for name in ("spec_surgeon.web.server", "spec_surgeon.web.shell", "spec_surgeon.web.updates"):
        monkeypatch.delitem(sys.modules, name, raising=False)     # a copy imported earlier would mask the absence
    assert gui.main() == 2 and shown == []
    assert 'pip install "spec-surgeon[gui]"' in capsys.readouterr().err
    # Restore stderr while capsys is still open. The monkeypatch fixture tears down after capsys; restoring
    # its captured stream then would leave sys.stderr closed for subsequent tests when pytest runs with -s.
    with monkeypatch.context() as no_console:
        no_console.setattr(sys, "stderr", None)
        assert gui.main() == 2 and len(shown) == 1 and "spec-surgeon[gui]" in shown[0]


def test_the_engine_never_loads_the_window():
    import subprocess
    import sys
    code = ("import sys, spec_surgeon.gui, spec_surgeon.document_task, spec_surgeon.batch, "
            "spec_surgeon.updater, spec_surgeon.llm, spec_surgeon.conversation, "
            "spec_surgeon.settings, spec_surgeon.winapp, spec_surgeon.credentials; "
            f"stack = {WEB_STACK!r}; "
            "bad = [m for m in sys.modules if m.split('.')[0] in stack or m.startswith('spec_surgeon.web')]; "
            "assert not bad, bad")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr


def test_the_web_stack_is_imported_only_by_the_web_package():
    import ast
    src = Path(__file__).resolve().parent.parent / "src" / "spec_surgeon"
    offenders = []
    for py in src.rglob("*.py"):
        rel = py.relative_to(src).as_posix()
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in tree.body:                       # module level only; function-level imports are lazy
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(n.split(".")[0] in WEB_STACK for n in names) and \
                    not (rel.startswith("web/") and rel != "web/__init__.py"):
                offenders.append(rel)
    assert offenders == []


# ---- a plan per layout: recovery one layout at a time ----------------------------------------------------------

def quiet(_message: str) -> None:
    pass


def _layout_plan(tmp_path):
    from make_fixtures import build_rich, build_styles
    from spec_surgeon.fingerprint import fingerprint
    from spec_surgeon.package import DocxPackage
    typed, styled, other = tmp_path / "a" / "21 13 13.docx", tmp_path / "b" / "21 13 16.docx", tmp_path / "c" / "21 13 19.docx"
    for path, data in ((typed, build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS")),
                       (styled, build_styles("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS")),
                       (other, build_rich("21 13 19", "PREACTION SPRINKLER SYSTEMS"))):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    pid = lambda p: fingerprint(DocxPackage.from_path(p))["pattern_id"]  # noqa: E731
    bold = {"run": {"b": True}, "tracked": False}
    plan = {"kind": "layouts", "request": "Make the body paragraphs bold.", "summary": "two layouts", "layouts": [
        {"id": "A", "label": "typed numbers", "pattern_ids": [pid(typed)],
         "spec": {"summary": "by text", "mode": "formatting",
                  "ops": [{"op": "set_properties", "select": {"paragraphs": {"text_regex": r"^[A-E]\.\t"}}, "params": bold}]}},
        {"id": "B", "label": "styled", "pattern_ids": [pid(styled)],
         "spec": {"summary": "by style", "mode": "formatting",
                  "ops": [{"op": "set_properties", "select": {"paragraphs": {"style_in": ["ArchPara"]}}, "params": bold}]}}]}
    return plan, [typed, styled, other]


def _stub_recovery(monkeypatch, calls):
    """Recovery itself is the model's investigation (its own tests); here only the folder it is pointed at matters."""
    from spec_surgeon import recovery

    def fake(run_dir, out_dir, *, request=None, options=None, client=None, log=None):
        calls.append(Path(run_dir))
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        return recovery.RecoveryResult(out_dir=Path(out_dir), request=str(request or ""))
    monkeypatch.setattr(recovery, "recover_from_run", fake)


def test_a_layout_run_recovers_one_layout_at_a_time_in_plan_order(tmp_path, monkeypatch):
    plan, docs = _layout_plan(tmp_path)
    for layout in plan["layouts"]:
        layout["spec"]["ops"][0]["expect"] = {"text_diffs": 5}       # every layout's document is set aside
    s = Session(documents=docs, out_root=tmp_path / "out", visual_check=False)
    s._set_outcome(spec=plan, source="test")
    s.make_preview(False, quiet)
    s.run(False, False, quiet)
    run = s.last_run_dir
    assert s.last_batch.counts()["EXCEPTION"] == 3
    calls = []
    _stub_recovery(monkeypatch, calls)
    s.resolve_remaining(quiet)
    assert calls == [run / "layout-A"] and s.pending_layout_recovery == [run / "layout-B"]
    assert s.recovery is not None and s.outcome().kind == "preview"
    s.resolve_next_layout(quiet)
    assert calls == [run / "layout-A", run / "layout-B"] and s.pending_layout_recovery == []
    with pytest.raises(ValueError, match="No other layout"):
        s.resolve_next_layout(quiet)
    s._set_outcome(spec=plan, source="test")                         # a new outcome forgets the queue
    assert s.pending_layout_recovery == []


def test_a_layout_run_whose_only_set_aside_document_had_no_layout_has_nothing_to_resolve(tmp_path, monkeypatch):
    plan, docs = _layout_plan(tmp_path)
    s = Session(documents=docs, out_root=tmp_path / "out", visual_check=False)
    s._set_outcome(spec=plan, source="test")
    s.make_preview(False, quiet)
    s.run(False, False, quiet)
    assert s.last_batch.counts() == {"PASS": 2, "FAIL": 0, "EXCEPTION": 1, "PENDING": 0}
    calls = []
    _stub_recovery(monkeypatch, calls)
    assert s.resolve_remaining(quiet).startswith("Nothing to resolve")
    assert calls == [] and s.recovery is None and s.pending_layout_recovery == []


def test_resolve_with_no_exceptions_returns_without_model_calls(tmp_path, monkeypatch):
    folder = tmp_path / "sources"
    folder.mkdir()
    source = document(folder / "spec.docx")
    session = Session(documents=[source], out_root=tmp_path / "results", visual_check=False)
    session._set_outcome(spec=recipe({"text_regex": "^Owner"}), source="compiled")
    session.make_preview(False, lambda _: None)
    def unexpected(*args, **kwargs):
        pytest.fail("nothing to resolve must not invoke recovery")
    monkeypatch.setattr("spec_surgeon.recovery.recover", unexpected)
    monkeypatch.setattr("spec_surgeon.recovery.recover_from_run", unexpected)
    assert "Nothing to resolve: 0" in session.resolve_remaining(lambda _: None)
    session.run(False, False, lambda _: None)
    assert "Nothing to resolve: 0" in session.resolve_remaining(lambda _: None)


EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "specs"
HF_SPEC = EXAMPLES / "match-template-spec-header-footer.json"


@pytest.fixture
def project(tmp_path):
    """Two specs and the architect's spec as the template, with a spec file that names it."""
    folder = tmp_path / "specs"
    folder.mkdir()
    (folder / "21 13 13 – WET-PIPE SPRINKLER SYSTEMS.docx").write_bytes(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    (folder / "21 13 16 – DRY-PIPE SPRINKLER SYSTEMS.docx").write_bytes(build("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS"))
    tpl = tmp_path / "ACME.docx"
    tpl.write_bytes(build_reference_spec())
    spec = load_spec(HF_SPEC)
    spec["ops"][0]["params"]["template"] = str(tpl)
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    return folder, tpl, spec, spec_file


def test_run_refuses_after_the_template_spec_was_edited_since_the_preview(project, tmp_path):
    folder, tpl, spec, spec_file = project
    s = Session(documents=sorted(folder.glob("*.docx")), template=tpl, out_root=tmp_path / "out")
    s._set_outcome(spec=load_spec(spec_file), source=str(spec_file))
    s.make_preview(render=False, log=lambda m: None)
    # the architect sends a new version of their spec with another header line
    tpl.write_bytes(build_reference_spec(extra_header_text="ISSUED FOR BID — DO NOT USE FOR CONSTRUCTION"))
    with pytest.raises(ValueError, match="changed since the preview"):
        s.run(in_place=False, render=False, log=lambda m: None)
    assert s.last_batch is None
    # a new preview of the new template is approved as usual
    s.make_preview(render=False, log=lambda m: None)
    s.run(in_place=False, render=False, log=lambda m: None)
    assert s.last_batch is not None and all(f.status == "PASS" for f in s.last_batch.files)


# ---- custom transforms in the session: errors, edited samples, the visual check --------------------------------------

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = ROOT / "examples" / "templates" / "08 71 00 - DOOR HARDWARE (ACME).docx"
UNSUPPORTED = {"request": "r", "summary": "s", "unsupported": "no op sets the header distance alone",
               "proposal": "set_header_distance"}
OFFER_TURN = resp(tool("submit_editspec", {"spec": UNSUPPORTED, "custom_transform": "possible"}))
WRITER = resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}}, "w1"))


def docx(d: Path):
    return sorted(d.glob("*.docx"))


def revise(f: Path) -> None:
    """The user edits a spec and saves it: new content (still a good .docx), new modification time."""
    st = f.stat()
    f.write_bytes(build("21 00 00", "SPEC 0 REVISED"))
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def offered(folder, tmp_path, *more) -> Session:
    s = Session(documents=docx(folder), out_root=tmp_path / "out", client=FakeClient(OFFER_TURN, *more))
    s.compile("put the header 0.625 in from the top", quiet)
    assert [c.kind for c in s.turn.choices] == ["custom_transform"]
    return s


def create_approved_custom(s, *, choice_id=None, render=False):
    message = s.reply(choice_id=choice_id or s.turn.choices[0].id, log=quiet, render=render)
    assert s.pending_custom_transform is None and s.generated is not None
    return message


def saved_entries(s) -> list:
    return json.loads((s.chat_dir / "conversation.json").read_text(encoding="utf-8"))["entries"]


def sent_texts(fake) -> list:
    return [b.get("text") for b in fake.requests[-1]["messages"][-1]["content"] if b.get("type") == "text"]


def _template_spec(folder: Path):
    tpl = folder / REFERENCE.name
    shutil.copy(REFERENCE, tpl)
    spec = json.loads((SPECS / "match-template-spec-header-footer.json").read_text(encoding="utf-8"))
    spec["ops"][0]["params"]["template"] = str(tpl)
    return tpl, spec


def test_a_new_request_after_a_spec_that_names_a_folder_template_keeps_its_conversation(folder, tmp_path):
    tpl, spec = _template_spec(folder)
    fake = FakeClient(resp(tool("submit_editspec", {"spec": spec}, "s1")),
                      resp(tool("ask_user", {"questions": ["Which sections?"]}, "a2")),
                      resp(tool("ask_user", {"questions": ["And the cover?"]}, "a3")))
    s = Session(documents=docx(folder), out_root=tmp_path / "out", client=fake)   # the template is attached too
    assert s.compile("match the template spec", quiet) == "Compiled."
    assert tpl not in s.files() and tpl in s.conversation.files     # the accepted spec reads it: filtered, not refused
    assert "needs your answer" in s.compile("now remove the footers", quiet)   # a new request, while that spec is out
    assert s.spec is None
    assert s.conversation.files == s.files() and tpl in s.conversation.files  # the batch a new conversation sees
    assert s.conversation_matches("now remove the footers")
    assert "needs your answer" in s.reply("every section", log=quiet)          # never "describe the change again"
    assert len(fake.requests) == 3


def test_a_new_request_over_a_folder_that_cannot_be_read_changes_nothing(folder, tmp_path):
    s = Session(documents=docx(folder), out_root=tmp_path / "out",
                client=FakeClient(resp(tool("submit_editspec", {"spec": REMOVE}))))
    assert s.compile("remove all headers and footers", quiet) == "Compiled."
    s.make_preview(render=False, log=quiet)
    conversation, preview = s.conversation, s.preview
    shutil.move(str(folder), str(tmp_path / "moved"))
    with pytest.raises((ValueError, OSError)):
        s.compile("now remove the footers", quiet)
    assert s.conversation is conversation and s.spec == REMOVE and s.preview is preview   # refused before anything moved


def test_an_internal_error_while_making_the_transform_tells_claude_so_never_the_outcome_follows(folder, tmp_path,
                                                                                               monkeypatch):
    import spec_surgeon.preview as pv
    s = offered(folder, tmp_path, WRITER, resp(tool("ask_user", {"questions": ["Shall I try another way?"]}, "a9")))
    chosen = s.turn.choices[0]
    real = pv.preview_generated

    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(pv, "preview_generated", broken)
    with pytest.raises(OSError, match="disk full") as e:
        create_approved_custom(s, choice_id=chosen.id)
    assert not isinstance(e.value, TransformFailed)                 # a defect propagates as itself
    notes = s.conversation.notes
    assert any("internal error (OSError) before any result — nothing was applied" in note for note in notes)
    assert not any("outcome follows" in n or "passed verification" in n or "disk full" in n for n in notes)
    entries = saved_entries(s)                                      # the transcript is what the user saw: the choice
    assert [entry for entry in entries if entry["role"] == "user"][-1]["text"].startswith("Chose: ")
    monkeypatch.setattr(pv, "preview_generated", real)
    s.reply("so did it work?")
    sent = sent_texts(s.client)                                     # the next message Claude gets
    assert all("[note from the app] " + note in sent for note in notes)
    assert not any("outcome follows" in (t or "") for t in sent)


def test_a_transform_that_comes_out_records_its_independently_verified_result(folder, tmp_path):
    s = offered(folder, tmp_path, WRITER)
    assert "passed verification" in create_approved_custom(s)
    assert any("passed its independent sample verification" in n for n in s.conversation.notes)


def test_a_sample_edited_while_the_transform_is_written_is_never_approved_with_its_new_stats(folder, tmp_path,
                                                                                              monkeypatch):
    import spec_surgeon.codegen as cg
    real = cg.generate_op
    edited = []

    def written_while_edited(request, proposal, samples, *a, **k):
        g = real(request, proposal, samples, *a, **k)               # its trials read the samples as they were
        revise(samples[0])                                          # saved in Word before the writer was done
        edited.append(samples[0])
        return g
    monkeypatch.setattr(cg, "generate_op", written_while_edited)
    s = offered(folder, tmp_path, WRITER)
    said = create_approved_custom(s)
    assert "passed verification" in said and "press Preview to try it again" in said
    assert edited and s.generated is not None
    assert s.generated_token == ""                                  # its token: none until it is tried again
    with pytest.raises(ValueError, match="press Preview again") as e:
        s.run(False, False, quiet)                                  # never approved with the edited file's stats
    assert f"{edited[0].name}: modified since the preview" in str(e.value)    # it names the file
    with pytest.raises(ValueError, match="not the current one"):
        s.acknowledge_review(s.generated_token)                     # nothing of it can be approved
    lines = []
    assert "transform verified" in s.make_preview(render=False, log=lines.append)
    assert s.pending_custom_transform is None
    assert all((s.out_dir / "retrial" / f"{i:02d}" / "result.json").is_file() for i in range(2))
    assert "2 of 2 file(s) PASS" in s.run(False, False, quiet)


def test_a_custom_transform_s_visual_findings_are_included_in_preview_consent(folder, tmp_path, monkeypatch):
    import spec_surgeon.preview as pv
    monkeypatch.setattr(pv, "render_samples", lambda *a, **k: None)  # no renderer in this test
    intents = []

    def review(intent, samples, work_dir, client_factory, *a, **k):
        intents.append(intent)
        return vision.VisualReview(summary="looked at the pages", findings=[
            {"severity": "block", "file": "21 00 00 – SPEC 0.docx", "page": 1, "text": "the header moved off the page"}])
    monkeypatch.setattr(vision, "review_samples", review)
    s = offered(folder, tmp_path, WRITER)
    assert "passed verification" in create_approved_custom(s, render=True)
    assert len(intents) == 1 and intents[0].startswith("A ONE-OFF CUSTOM TRANSFORM (set_header_distance)")
    assert s.generated_preview.review.blocking() and s.generated_token
    assert s.run_blocked_reason() == ""
    with pytest.raises(ValueError, match="not the current one"):
        s.acknowledge_review("an old preview's token")
    before = {f: f.read_bytes() for f in docx(folder)}
    out = s.run(in_place=False, render=False, log=quiet)            # edited copies: the documents are never touched
    assert "2 of 2 file(s) PASS" in out and "your files were not changed" in out
    assert {f: f.read_bytes() for f in docx(folder)} == before
    outputs = sorted((s.last_run_dir / "outputs").glob("*.docx"))
    assert len(outputs) == 2 and all(o.read_bytes() not in before.values() for o in outputs)
    assert [t.passed for t in s.last_trials] == [True, True]


def test_turning_render_pages_on_makes_the_custom_transforms_preview_again_with_the_visual_check(folder, tmp_path,
                                                                                                 monkeypatch):
    """Codex on #12: a custom transform previewed with Render pages off, then previewed with it on, reused the old
    preview — no page images, no visual check — while the window took the render-on key as current."""
    import spec_surgeon.preview as pv
    monkeypatch.setattr(pv, "render_samples", lambda *a, **k: None)   # no renderer in this test
    reviews = []
    monkeypatch.setattr(vision, "review_samples",
                        lambda intent, samples, *a, **k: reviews.append(intent) or vision.VisualReview(summary="ok"))
    s = offered(folder, tmp_path, WRITER)
    assert "passed verification" in create_approved_custom(s, render=False)
    first, token = s.generated_preview, s.generated_token
    assert first.rendered is False and first.review is None and reviews == [] and token
    s.make_preview(render=True, log=quiet)
    again = s.generated_preview
    assert again is not first and again.rendered is True and again.out_dir != first.out_dir
    assert len(reviews) == 1 and again.review is not None            # the visual check ran this time
    assert s.generated_token == token                               # same files, same transform: same approval
    s.make_preview(render=True, log=quiet)                          # nothing changed: the preview is reused
    assert s.generated_preview is again and len(reviews) == 1
    s.make_preview(render=False, log=quiet)                         # and back: made again without pages
    assert s.generated_preview.rendered is False and len(reviews) == 1
    assert "2 of 2 file(s) PASS" in s.run(False, False, quiet)


# ---- templates in the folder, vanished folders, a transform that does not come out, stale transforms -----------------

@pytest.fixture
def two_specs(tmp_path) -> Path:
    d = tmp_path / "specs"
    d.mkdir()
    for i in range(2):
        (d / f"21 0{i} 00 – SPEC {i}.docx").write_bytes(build(f"21 0{i} 00", f"SPEC {i}"))
    return d


def touch(f: Path) -> None:
    """The user opens a spec in Word and saves it: the file's mtime moves."""
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))


def _offered(two_specs, tmp_path, *writer):
    s = Session(documents=docx(two_specs), out_root=tmp_path / "out", client=FakeClient(OFFER_TURN, *writer))
    s.compile("put the header 0.625 in from the top", quiet)
    assert [c.kind for c in s.turn.choices] == ["custom_transform"]
    return s


def _saved(s):
    return json.loads((s.chat_dir / "conversation.json").read_text(encoding="utf-8"))


def test_a_template_spec_from_the_batch_folder_keeps_the_conversation(two_specs, tmp_path):
    """The template spec is attached with the other documents; Claude's spec names it as its template. ``files()``
    leaves it out ("the attached documents without the templates"; ``compile`` and ``conversation_matches`` compare
    like with like through the same filter) — it is the spec's template, never a copy of the user's own, so it is
    filtered, not refused, and the conversation keeps matching: nothing changed."""
    tpl = two_specs / "08 71 00 - DOOR HARDWARE (ACME).docx"
    shutil.copy(ROOT / "examples" / "templates" / "08 71 00 - DOOR HARDWARE (ACME).docx", tpl)
    spec = json.loads((SPECS / "match-template-spec-header-footer.json").read_text(encoding="utf-8"))
    spec["ops"][0]["params"]["template"] = str(tpl)
    request = "make the header and footer match the template spec"
    fake = FakeClient(resp(tool("submit_editspec", {"spec": spec}, "s1")),
                      resp(tool("ask_user", {"questions": ["Keep the logo?"]}, "a2")))
    s = Session(documents=docx(two_specs), out_root=tmp_path / "out", client=fake)
    assert s.compile(request, quiet) == "Compiled."
    assert tpl in s.conversation.files and tpl not in s.files()      # the batch it started on held the template
    assert s.conversation_matches(request)                           # nothing changed: the same conversation
    assert "needs your answer" in s.reply("also keep the logo", log=quiet)
    assert s.spec is None and s.conversation_matches(request)        # without a spec the template is a file again
    new = two_specs / "21 99 00 – NEW.docx"
    new.write_bytes(build("21 99 00", "NEW"))
    s.documents.append(new)                                          # another document attached: a real
    assert not s.conversation_matches(request)                       # change still ends the match
    with pytest.raises(ValueError, match="describe the change again"):
        s.reply("and the footer")


def test_a_removed_file_still_ends_the_match_when_a_spec_names_a_template(two_specs, tmp_path):
    tpl = two_specs / "08 71 00 - DOOR HARDWARE (ACME).docx"
    shutil.copy(ROOT / "examples" / "templates" / "08 71 00 - DOOR HARDWARE (ACME).docx", tpl)
    spec = json.loads((SPECS / "match-template-spec-header-footer.json").read_text(encoding="utf-8"))
    spec["ops"][0]["params"]["template"] = str(tpl)
    s = Session(documents=docx(two_specs), out_root=tmp_path / "out",
                client=FakeClient(resp(tool("submit_editspec", {"spec": spec}))))
    s.compile("match the template spec", quiet)
    assert s.conversation_matches("match the template spec")        # the match holds before a file goes
    sorted(two_specs.glob("21 00*.docx"))[0].unlink()
    assert not s.conversation_matches("match the template spec")


def test_a_vanished_folder_is_no_match_never_an_exception(two_specs, tmp_path):
    s = Session(documents=docx(two_specs), out_root=tmp_path / "out",
                client=FakeClient(resp(tool("ask_user", {"questions": ["Which sections?"]}))))
    s.compile("remove some headers", quiet)
    assert s.conversation_matches("remove some headers")
    shutil.move(str(two_specs), str(tmp_path / "moved"))                # renamed, or the network share dropped
    assert s.conversation_matches("remove some headers") is False
    with pytest.raises(ValueError, match="describe the change again"):
        s.reply("all of them")
    assert s.conversation is not None                                # nothing was thrown away


def test_no_passing_attempt_is_told_to_claude_once_and_saved(two_specs, tmp_path):
    bad = [resp(tool("submit_op", {"source": "x = 1"}, f"o{i}")) for i in range(3)]
    s = _offered(two_specs, tmp_path, *bad,
        resp(tool("need_user", {"question": "Which outcome should the replacement method preserve?"})))
    s.reply(choice_id=s.turn.choices[0].id, log=quiet)
    expected = ("No custom transform passed verification: no passing op after 3 attempt(s): "
                "investigation stalled; equivalent rejected transforms repeated without new evidence")
    assert s.conversation.notes[-1] == expected
    assert sum("passed verification" in n for n in s.conversation.notes) == 1
    assert any(e["role"] == "app" and e["text"] == expected for e in _saved(s)["entries"])
    assert s.generated is None
    assert s.conversation.recovery_active and s.turn.status == "questions"
    assert expected in str(s.client.requests[-1]["messages"])
    s.client.responses.append(resp(tool("need_user", {"question": "Which outcome?"}, "a9")))
    s.reply("so did it work?")
    assert "so did it work?" in str(s.client.requests[-1]["messages"])


def test_an_api_error_while_writing_is_a_recorded_failure_told_to_claude(two_specs, tmp_path):
    s = _offered(two_specs, tmp_path, raise_on_enter(api_error("Overloaded", 529, "req_7")))
    with pytest.raises(TransformFailed, match="the call to the model failed"):
        generate_reviewed_custom(s, log=quiet)
    note = s.conversation.notes[-1]
    assert note.startswith(TransformFailed.PREFIX) and "No AI-written" not in note and "529" in note
    assert _saved(s)["entries"][-1]["text"] == note


def test_a_defect_while_writing_is_never_told_to_claude_as_a_verification_result(two_specs, tmp_path):
    s = _offered(two_specs, tmp_path, raise_on_enter(RuntimeError("a defect in the writer")))
    with pytest.raises(RuntimeError, match="a defect in the writer") as e:
        generate_reviewed_custom(s, log=quiet)
    assert not isinstance(e.value, TransformFailed)
    assert not any("passed verification" in n or "defect" in n for n in s.conversation.notes)
    entries = _saved(s)["entries"]                                   # what was said is kept for the diagnosis
    assert [e for e in entries if e["role"] == "user"][-1]["text"].startswith("Chose: ")
    assert entries[-1]["role"] == "user"  # the failed method's choice is preserved; no second review ran
    assert "internal error (RuntimeError) before any result" in s.conversation.notes[-1]


def test_preview_tries_a_stale_custom_transform_again_and_run_then_runs(two_specs, tmp_path):
    s = _offered(two_specs, tmp_path, WRITER)
    assert "passed verification" in generate_reviewed_custom(s, log=quiet)
    s.make_preview(render=False, log=quiet)
    first = s.out_dir
    touch(sorted(two_specs.glob("*.docx"))[0])
    with pytest.raises(ValueError, match="press Preview again"):
        s.run(False, False, quiet)
    lines = []
    text = s.make_preview(render=False, log=lines.append)
    assert "transform verified" in text and s.pending_custom_transform is None
    retrials = [s.out_dir / "retrial"]
    assert s.out_dir != first and retrials[0].is_dir() and (s.out_dir / "preview").is_dir()
    assert all(t.output.is_relative_to(retrials[0]) for t in s.generated.trials)
    assert s.chat_dir == s.out_dir and (s.out_dir / "conversation.json").is_file()   # the records follow the preview
    assert (first / "preview").is_dir() and (first / "codegen" / "history.json").is_file()   # the old one is kept
    assert "2 of 2 file(s) PASS" in s.run(False, False, quiet)


def test_a_current_custom_transform_is_not_tried_again(two_specs, tmp_path, monkeypatch):
    s = _offered(two_specs, tmp_path, WRITER)
    generate_reviewed_custom(s, log=quiet)
    token, gp, out = s.generated_token, s.generated_preview, s.out_dir
    import spec_surgeon.codegen as cg

    def no_trial(*a, **k):
        raise AssertionError("a current transform must not be tried again")
    monkeypatch.setattr(cg, "trial", no_trial)
    assert "AI-written transform verified" in s.make_preview(render=False, log=quiet)
    assert (s.generated_token, s.generated_preview, s.out_dir) == (token, gp, out)


def test_trying_again_with_rendering_runs_the_visual_check_again(two_specs, tmp_path, monkeypatch):
    import spec_surgeon.preview as pv
    monkeypatch.setattr(pv, "render_samples", lambda *a, **k: None)  # no renderer in this test
    s = _offered(two_specs, tmp_path, WRITER)
    generate_reviewed_custom(s, log=quiet)
    checks = []
    monkeypatch.setattr(Session, "_visual_check", lambda self, p, intent, where, log: checks.append((p, intent)))
    touch(sorted(two_specs.glob("*.docx"))[0])
    s.make_preview(render=True, log=quiet)
    assert s.pending_custom_transform is None
    assert len(checks) == 1 and checks[0][0] is s.generated_preview
    assert checks[0][1].startswith("A ONE-OFF CUSTOM TRANSFORM (set_header_distance) for: put the header 0.625 in from the top")


def test_a_reply_after_a_run_opens_a_newer_folder_and_the_run_keeps_its_records(two_specs, tmp_path):
    """The run's session folder keeps its records as they were; a reply after the run goes to a new session folder."""
    fake = FakeClient(resp(tool("submit_editspec", {"spec": REMOVE})),
                      resp(tool("ask_user", {"questions": ["What about the cover?"]}, "a2")))
    s = Session(documents=docx(two_specs), out_root=tmp_path / "out", client=fake)
    s.compile("remove all headers and footers", quiet)
    s.make_preview(render=False, log=quiet)
    s.run(False, False, quiet)
    first = s.out_dir
    s.reply("hmm, what about the cover?")
    later = s.chat_dir
    assert later != first and s.last_run_dir == first / "run"
    for record in ("run/DIAGNOSTICS.md", "run/logs/run.log", "conversation.json", "compile.json",
                   "preview/preview.json"):
        assert (first / record).is_file(), record                                 # the last run, with its session
    assert (later / "conversation.json").is_file() and (later / "compile.json").is_file()
    newer = json.loads((later / "conversation.json").read_text(encoding="utf-8"))
    older = json.loads((first / "conversation.json").read_text(encoding="utf-8"))
    assert any("what about the cover" in (e.get("text") or "") for e in newer["entries"])
    assert not any("what about the cover" in (e.get("text") or "") for e in older["entries"])
