"""The window's logic (``gui.Session``) end to end with a scripted client — preview and run, stale previews, the
conversation (Claude's questions and choices, replies, a custom transform), copies only, plain errors — plus what the
conversation window keeps beside the chat (the update banner, Help → About; the window itself is driven in
``test_conversation_window.py``), and that Qt never leaks into the engine or the command line."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from make_fixtures import build
from spec_surgeon.editspec import load_spec
from spec_surgeon.gui import Session
from test_codegen import GOOD_OP
from test_compiler import FakeClient, resp, tool, generate_reviewed_custom

SPECS = Path(__file__).resolve().parent.parent / "examples" / "specs"
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


# ---- the conversation window (Qt, offscreen): what it keeps beside the chat --------------------------------

@pytest.fixture
def qt(monkeypatch):
    """A QApplication and no update check; the settings folder and the key are sealed by conftest."""
    from qt_support import close_windows, qt_app_or_skip
    app = qt_app_or_skip()
    monkeypatch.setenv("SPEC_SURGEON_NO_UPDATE_CHECK", "1")
    windows = []
    yield app, windows
    close_windows(app, windows)


def window(qt, tmp_path, **kw):
    from spec_surgeon.gui import build_window
    from spec_surgeon.settings import Settings
    from spec_surgeon.ui.app import RecordingPrompts
    kw.setdefault("prompts", RecordingPrompts())
    kw.setdefault("settings", Settings())
    w = build_window(Session(out_root=tmp_path / "out"), **kw)
    qt[1].append(w)
    return w


def help_action(w, title: str):
    from PySide6.QtGui import QAction
    return next(a for a in w.findChildren(QAction) if a.text() == title)


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


def test_update_banner_skip_and_manual_check(qt, tmp_path, monkeypatch):
    from spec_surgeon.settings import load_settings
    monkeypatch.delenv("SPEC_SURGEON_NO_UPDATE_CHECK")
    checker = _checker(["update"])
    w = window(qt, tmp_path, checker=checker)
    banner, wait = w.updates.banner, w._spec_surgeon["wait"]
    assert banner.isHidden()
    assert w.updates.auto_check() is True
    wait()
    assert not banner.isHidden() and banner.mode == "available"
    assert "9.9.9 is available" in banner.title.text()
    assert banner.btn_install.isEnabled()
    assert w.settings.last_update_check > 0
    assert w.updates.auto_check() is False                      # once a day
    banner.btn_skip.click()
    assert banner.isHidden() and load_settings().skipped_version == "9.9.9"
    w.settings.last_update_check = 0
    w.updates.auto_check()
    wait()
    assert banner.isHidden()                                    # a skipped version stays quiet on automatic checks
    help_action(w, "Check for updates").trigger()
    wait()
    assert not banner.isHidden() and "skip" in banner.text.text()
    assert checker.calls[-1] == (True, "9.9.9")


def test_about_says_who_made_it_the_license_and_how_to_reach_him(qt, tmp_path):
    from PySide6.QtWidgets import QMenu
    from spec_surgeon import __version__, about
    from spec_surgeon.ui.about_dialog import AboutDialog
    w = window(qt, tmp_path)
    help_menu = next(m for m in w.menuBar().findChildren(QMenu) if m.title() == "Help")
    assert [a.text() for a in help_menu.actions()] == ["Check for updates", "Open diagnostic logs", "About Spec Surgeon"]
    help_action(w, "About Spec Surgeon").trigger()
    dlg = w._viewers[-1]
    assert isinstance(dlg, AboutDialog)
    assert dlg.name.text() == "Spec Surgeon" and __version__ in dlg.version.text()
    plain = dlg.body.toPlainText()
    assert about.COPYRIGHT_LINE in plain and about.LICENSE_NAME in plain and about.AUTHOR in plain
    for line in about.LICENSE_SUMMARY:
        assert line in plain
    assert f'href="mailto:{about.EMAIL}"' in dlg.html and about.EMAIL in plain
    assert f'href="{about.GITHUB_URL}"' in dlg.html and f'href="{about.LINKEDIN_URL}"' in dlg.html
    for name, lic, url in about.THIRD_PARTY:
        assert f'href="{url}"' in dlg.html and lic in plain
    assert "Settings:" in plain
    dlg.show_license()
    assert dlg._viewers[-1].browser.toPlainText().strip() == about.LICENSE_TEXT.strip()
    text = dlg.text()
    for value in (about.EMAIL, about.GITHUB_URL, about.LINKEDIN_URL, about.LICENSE_NAME, "Settings:"):
        assert value in text
    dlg.close()


# ---- Qt stays inside the window --------------------------------------------------------------------------------

def test_without_qt_the_window_explains_how_to_install_it():
    import subprocess
    import sys
    code = "import sys; sys.modules['PySide6'] = None; from spec_surgeon.gui import main; sys.exit(main())"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and 'pip install "spec-surgeon[gui]"' in r.stderr


def test_without_qt_a_message_box_only_when_there_is_no_console(monkeypatch, capsys):
    """The native message box is modal: with a stream to print to it must never appear (it hung the Windows CI
    run of the subprocess test above); only pythonw (no stderr) gets it."""
    import sys
    from spec_surgeon import gui, winapp
    shown = []
    monkeypatch.setattr(winapp, "message_box", lambda title, text: shown.append(text))
    monkeypatch.setitem(sys.modules, "PySide6", None)
    assert gui.main() == 2 and shown == []
    assert 'pip install "spec-surgeon[gui]"' in capsys.readouterr().err
    # Restore stderr while capsys is still open. The monkeypatch fixture tears down after capsys; restoring
    # its captured stream then would leave sys.stderr closed for subsequent tests when pytest runs with -s.
    with monkeypatch.context() as no_console:
        no_console.setattr(sys, "stderr", None)
        assert gui.main() == 2 and len(shown) == 1 and "spec-surgeon[gui]" in shown[0]


def test_the_engine_and_command_line_never_load_qt():
    import subprocess
    import sys
    code = ("import sys, spec_surgeon.gui, spec_surgeon.document_task, spec_surgeon.cli, spec_surgeon.batch, "
            "spec_surgeon.updater, spec_surgeon.llm, spec_surgeon.conversation, "
            "spec_surgeon.settings, spec_surgeon.winapp, spec_surgeon.credentials, spec_surgeon.ui; "
            "bad = [m for m in sys.modules if m.split('.')[0] in ('PySide6', 'shiboken6')]; "
            "assert not bad, bad")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr


def test_pyside6_is_imported_only_by_the_ui_package():
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
            if any(n.split(".")[0] in ("PySide6", "shiboken6") for n in names) and \
                    not (rel.startswith("ui/") and rel != "ui/__init__.py"):
                offenders.append(rel)
    assert offenders == []
