"""Running without an API key (keyless mode): what needs Claude says so instead of failing, and everything else — saved
specs, your library's recipes and operations, Preview, Run and its verification, set-aside passes — works without a
key, in the window, the session and on the command line (``library use``).

``tests/conftest.py`` makes ``llm.credentials_found`` True for every test (the tests script their Claude clients); the
tests here patch it to False — or, for the window's switch, to "is a key in the environment" — and test the real
function on its own (``REAL_CREDENTIALS_FOUND``, taken when this module is collected, before any fixture patches it)."""
from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

import pytest

from make_fixtures import build
from spec_surgeon import compiler as compiler_module
from spec_surgeon import library, llm, vision
from spec_surgeon.cli import main as cli_main
from spec_surgeon.gui import NEEDS_KEY, NEEDS_KEY_ADVICE, NEEDS_KEY_REPLY, NO_KEY_REVIEW, Session
from test_compiler import FakeClient, resp, tool
from test_library import passing_op

SPECS = Path(__file__).resolve().parent.parent / "examples" / "specs"
REMOVE = json.loads((SPECS / "remove-all-headers-footers.json").read_text(encoding="utf-8"))
REAL_CREDENTIALS_FOUND = llm.credentials_found
REAL_SDK_CREDENTIALS = llm._sdk_credentials


@pytest.fixture
def keyless(monkeypatch):
    monkeypatch.setattr(llm, "credentials_found", lambda: False)


@pytest.fixture
def folder(tmp_path) -> Path:
    d = tmp_path / "Specs"
    d.mkdir()
    for i in range(2):
        (d / f"21 0{i} 00 – SPEC {i}.docx").write_bytes(build(f"21 0{i} 00", f"SPEC {i}"))
    return d


def quiet(_m):
    pass


# ---- the one decision: llm.credentials_found ------------------------------------------------------------------------

def test_credentials_are_a_key_in_the_environment_or_a_profile_the_sdk_finds(monkeypatch):
    assert REAL_CREDENTIALS_FOUND() is False                       # sealed: no key, no profile (conftest)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    assert REAL_CREDENTIALS_FOUND() is True
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")
    assert REAL_CREDENTIALS_FOUND() is True
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")                     # an empty variable is no key
    assert REAL_CREDENTIALS_FOUND() is False
    monkeypatch.setattr(llm, "_sdk_credentials", lambda: True)      # an `ant auth login` profile
    assert REAL_CREDENTIALS_FOUND() is True


def test_the_sdk_profile_lookup_never_raises_and_never_calls(monkeypatch):
    fake = types.ModuleType("anthropic.lib.credentials")
    monkeypatch.setitem(sys.modules, "anthropic.lib.credentials", fake)
    fake.default_credentials = lambda: object()
    assert REAL_SDK_CREDENTIALS() is True
    fake.default_credentials = lambda: None
    assert REAL_SDK_CREDENTIALS() is False

    def broken():
        raise ValueError("profile 'dev' cannot be read")
    fake.default_credentials = broken
    assert REAL_SDK_CREDENTIALS() is False                          # cannot authenticate either: keyless
    monkeypatch.setitem(sys.modules, "anthropic.lib.credentials", None)   # no SDK (or one without profiles)
    assert REAL_SDK_CREDENTIALS() is False


# ---- the session ------------------------------------------------------------------------------------------------

def test_a_keyless_session_previews_and_runs_a_saved_spec_and_refuses_what_needs_claude(keyless, folder, tmp_path,
                                                                                          monkeypatch):
    s = Session(folder=folder, out_root=tmp_path / "out")
    assert s.claude_ready() is False
    s.load_spec(SPECS / "remove-all-headers-footers.json")
    with pytest.raises(ValueError) as e:
        s.compile("change all fonts to Arial", quiet)
    assert str(e.value) == NEEDS_KEY
    assert s.spec == REMOVE and s.conversation is None              # refused before anything changed
    assert "2 plan cleanly" in s.make_preview(render=False, log=quiet)
    out = s.run(False, False, quiet)
    assert "PASS 2" in out and "Advise" not in out
    with pytest.raises(ValueError) as e:
        s.advise(quiet)
    assert str(e.value) == NEEDS_KEY_ADVICE


def test_the_visual_check_is_skipped_without_a_key_and_nothing_is_sent(keyless, folder, tmp_path, monkeypatch):
    def never(*a, **k):
        raise AssertionError("keyless: nothing is prepared or sent")
    monkeypatch.setattr(vision, "review_samples", never)
    s = Session(folder=folder, out_root=tmp_path / "out")
    s.load_spec(SPECS / "remove-all-headers-footers.json")
    s.make_preview(render=False, log=quiet)
    lines = []
    s._visual_check(s.preview, s._intent(), s.out_dir / "preview", lines.append)
    rv = s.preview.review
    assert rv.skipped == NO_KEY_REVIEW and rv.findings == [] and rv.blocking() == []
    assert s.run_blocked_reason() == ""                             # it can stop a run, never block one by its absence
    assert any("visual check skipped" in m for m in lines)
    saved = json.loads((s.out_dir / "preview" / "visual_review.json").read_text(encoding="utf-8"))
    assert saved["skipped"] == NO_KEY_REVIEW
    seen = []
    monkeypatch.setattr(vision, "review_samples", lambda *a, **k: seen.append(1) or vision.VisualReview())
    s.client = FakeClient()                                         # a client given: Claude can be called
    s._visual_check(s.preview, s._intent(), s.out_dir / "preview", quiet)
    assert seen == [1]


def test_a_conversation_waits_for_a_key_once_it_is_gone(folder, tmp_path, monkeypatch):
    s = Session(folder=folder, out_root=tmp_path / "out",
                client=FakeClient(resp(tool("ask_user", {"questions": ["Which sections?"]}))))
    s.compile("remove the headers", quiet)
    assert s.conversation is not None and s.turn.status == "questions"
    s.client = None
    monkeypatch.setattr(llm, "credentials_found", lambda: False)    # Settings → Forget saved key
    turns = s.conversation.user_turns()
    with pytest.raises(ValueError) as e:
        s.reply("all of them", log=quiet)
    assert str(e.value) == NEEDS_KEY_REPLY and s.conversation.user_turns() == turns


def test_one_of_your_operations_runs_without_a_key(keyless, folder, tmp_path):
    library.save_operation("Header distance", passing_op(folder, tmp_path))   # made earlier, by someone with a key
    s = Session(folder=folder, out_root=tmp_path / "out")
    assert "passed verification" in s.use_operation("Header distance", quiet)
    assert "2 of 2 file(s) PASS" in s.run(False, False, quiet)


def test_a_keyless_set_aside_pass_points_at_saved_specs(keyless, folder, tmp_path):
    (folder / "21 09 00 – BROKEN.docx").write_bytes(b"not a zip")
    s = Session(folder=folder, out_root=tmp_path / "out")
    s.load_spec(SPECS / "remove-all-headers-footers.json")
    s.make_preview(render=False, log=quiet)
    s.run(False, False, quiet)
    msg = s.load_set_aside()
    assert "Load a saved spec or use Your library" in msg and "compile" not in msg


# ---- the window -----------------------------------------------------------------------------------------------------

@pytest.fixture
def qt(tmp_path, monkeypatch):
    from qt_support import close_windows, qt_app_or_skip
    app = qt_app_or_skip()
    monkeypatch.setenv("SPEC_SURGEON_NO_UPDATE_CHECK", "1")
    windows = []
    yield app, windows
    close_windows(app, windows)


def window(qt, session=None, **kw):
    from spec_surgeon.gui import build_window
    from spec_surgeon.ui.app import RecordingPrompts
    kw.setdefault("prompts", RecordingPrompts())
    w = build_window(session, **kw)
    qt[1].append(w)
    return w, w._spec_surgeon


def test_the_keyless_window_offers_saved_specs_first_and_says_what_a_key_adds(qt, keyless, folder, tmp_path):
    w, ui = window(qt, Session(out_root=tmp_path / "out"))
    r = w.request_page
    assert ui["claude"]() is False
    assert r.title.text() == "Choose the change" and w.nav.item(1).text() == "Choose the change"
    assert r.content.indexOf(r.saved_card) == 0                     # the saved specs come first
    assert r.request.isHidden() and r.chips.isHidden() and not r.keyless.isHidden()
    for feature in llm.CLAUDE_FEATURES:
        assert feature[1:] in r.keyless_lbl.text()
    assert not w.keyless_note.isHidden() and "No API key" in w.keyless_note.text()
    assert r.key_pill.text() == "No API key"


def test_a_draft_request_never_compiles_without_a_key_and_a_saved_spec_runs(qt, keyless, folder, tmp_path,
                                                                             monkeypatch):
    def never(*a, **k):
        raise AssertionError("keyless: no compile")
    monkeypatch.setattr(Session, "compile", never)
    (folder / "21 09 00 – BROKEN.docx").write_bytes(b"not a zip")
    s = Session(out_root=tmp_path / "out")
    w, ui = window(qt, s)
    r = w.request_page
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["render"].set(False)
    ui["request"].set("change all fonts to Arial")                  # a draft from a session with a key
    w._refresh_files()
    assert r.preview_hint.text() == "Load a saved spec or use Your library"
    assert ui["buttons"]["preview"].isEnabled()
    assert w.do_preview() is False
    assert not r.info.isHidden() and r.info.title.text() == "Choose a saved spec" and r.info.text.text() == NEEDS_KEY
    s.load_spec(SPECS / "remove-all-headers-footers.json")          # the draft does not hide the loaded spec
    w._inputs_changed()
    ui["buttons"]["preview"].click()
    ui["wait"]()
    assert w.stack.currentIndex() == 2 and w.preview_page.continue_btn.isEnabled()
    ui["buttons"]["run"].click()
    ui["wait"]()
    run = w.run_page
    assert run.t_pass.value.text() == "2" and run.set_aside_btn.isEnabled()
    assert not run.advise_btn.isEnabled() and run.advise_btn.toolTip() == NEEDS_KEY_ADVICE
    assert "ask for advice" not in run.summary.text.text()
    assert w.do_advise() is False and run.info.title.text() == "Advice needs an API key"
    assert ui["request"].get() == "change all fonts to Arial"       # the draft is kept for when a key is set


def test_setting_a_key_in_settings_brings_claude_back(qt, folder, tmp_path, monkeypatch):
    from spec_surgeon.ui.settings_dialog import SettingsDialog
    monkeypatch.setattr(llm, "credentials_found", lambda: bool(os.environ.get("ANTHROPIC_API_KEY")))
    w, ui = window(qt, Session(out_root=tmp_path / "out"))
    r = w.request_page
    ui["request"].set("change all fonts to Arial")
    assert w.claude_ok is False and w.typed_request() == ""
    dlg = SettingsDialog(w)
    dlg.key.setText("sk-ant-test-not-a-real-key")
    dlg.apply()
    assert w.claude_ok is True and w.typed_request() == "change all fonts to Arial"
    assert r.title.text() == "Describe the change" and w.nav.item(1).text() == "Describe the change"
    assert not r.request.isHidden() and r.keyless.isHidden() and w.keyless_note.isHidden()
    assert r.content.indexOf(r.saved_card) == r.content.indexOf(r.chat) + 1


def test_the_chat_waits_for_a_key_in_the_window(qt, folder, tmp_path, monkeypatch):
    s = Session(out_root=tmp_path / "out", client=FakeClient(resp(tool("ask_user", {"questions": ["Which?"]}))))
    w, ui = window(qt, s)
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["render"].set(False)
    ui["request"].set("remove the headers")
    ui["buttons"]["preview"].click()
    ui["wait"]()
    assert s.conversation is not None and ui["chat"].send_btn.isEnabled()
    s.client = None
    monkeypatch.setattr(llm, "credentials_found", lambda: False)
    w.refresh_key_label()
    assert ui["chat"].hint.text() == NEEDS_KEY_REPLY and not ui["chat"].reply.isEnabled()
    assert w.do_send("all of them") is False


# ---- the command line -------------------------------------------------------------------------------------------

def test_do_and_advise_say_what_works_without_a_key(keyless, folder, tmp_path, monkeypatch, capsys):
    def never(*a, **k):
        raise AssertionError("keyless: no compile")
    monkeypatch.setattr(compiler_module, "compile_request", never)
    out = tmp_path / "o"
    assert cli_main(["do", "change all fonts to Arial", str(folder), "--out", str(out), "--no-render"]) == 2
    err = capsys.readouterr().err
    assert "needs an Anthropic API key" in err and "library use" in err and "run-spec" in err
    assert not out.exists()                                         # nothing written
    assert cli_main(["advise", str(tmp_path)]) == 2
    assert "spec-surgeon diagnose" in capsys.readouterr().err


def test_library_use_runs_a_recipe_without_a_key(keyless, folder, tmp_path, capsys):
    library.save_recipe("Strip headers", REMOVE)
    out = tmp_path / "o"
    assert cli_main(["library", "use", "strip headers", str(folder), "--out", str(out), "--no-render", "--yes"]) == 0
    assert len(list((out / "run" / "outputs").glob("*.docx"))) == 2 and (out / "preview" / "spec.json").is_file()
    assert cli_main(["library", "use", "strip headers", str(folder), "--out", str(tmp_path / "o2"), "--no-render"]) == 0
    text = capsys.readouterr().out
    assert "Not run. To run exactly this:" in text and "--approval" in text   # no --yes, no terminal: nothing runs
    assert not (tmp_path / "o2" / "run").exists()
    assert cli_main(["library", "use", "strip headers"]) == 2                 # which files?
    assert cli_main(["library", "use", "no such thing", str(folder)]) == 2
    assert cli_main(["library", "show", "strip headers"]) == 0 and "library use" in capsys.readouterr().out


def test_library_use_tries_an_operation_first_and_runs_it_without_a_key(keyless, folder, tmp_path, monkeypatch,
                                                                       capsys):
    library.save_operation("Header distance", passing_op(folder, tmp_path))
    out = tmp_path / "o"
    assert cli_main(["library", "use", "Header distance", str(folder), "--out", str(out), "--no-render",
                     "--yes"]) == 0
    text = capsys.readouterr().out
    assert "passed on the samples" in text and "2 of 2 file(s) PASS" in text
    assert (out / "library-trial" / "try-1").is_dir()
    import spec_surgeon.codegen as cg
    real = cg.trial

    def failing(*a, **k):
        t = real(*a, **k)
        t.passed, t.problems = False, ["V10 FAIL: not here"]
        return t
    monkeypatch.setattr(cg, "trial", failing)
    out2 = tmp_path / "o2"
    assert cli_main(["library", "use", "Header distance", str(folder), "--out", str(out2), "--no-render",
                     "--yes"]) == 2
    assert "does not pass on these files" in capsys.readouterr().out and not (out2 / "run").exists()


def test_the_command_line_visual_check_is_skipped_without_a_key(keyless, tmp_path, monkeypatch, capsys):
    from spec_surgeon import cli

    def never(*a, **k):
        raise AssertionError("keyless: nothing is prepared or sent")
    monkeypatch.setattr(vision, "review_samples", never)
    rv = cli._review("REQUEST: x", [], tmp_path, types.SimpleNamespace())
    assert rv.skipped == cli.NO_KEY_REVIEW and rv.blocking() == []
    assert "visual check skipped" in capsys.readouterr().err
