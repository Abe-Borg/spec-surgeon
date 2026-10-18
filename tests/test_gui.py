"""The window: its logic (``gui.Session``) end to end with a scripted client, and the real Qt window (offscreen)
driven through its buttons — preview and run, stale previews, the chat (Claude's questions and choices, replies, a
custom transform, Start over), the visual check's findings gate, the rail and the step hints, errors, updates (check,
skip, install hand-off, post-update banners), Settings, Help and About, what it remembers, and that Qt never leaks into
the engine or the command line."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from make_fixtures import build
from spec_surgeon.gui import Session
from test_codegen import GOOD_OP
from test_compiler import (FakeClient, resp, tool, remove_outcome_proposal, header_outcome_proposal,
                           generate_reviewed_custom)

SPECS = Path(__file__).resolve().parent.parent / "examples" / "specs"
REMOVE = json.loads((SPECS / "remove-all-headers-footers.json").read_text(encoding="utf-8"))


@pytest.fixture
def folder(tmp_path) -> Path:
    d = tmp_path / "Specs"
    d.mkdir()
    for i in range(2):
        (d / f"21 0{i} 00 – SPEC {i}.docx").write_bytes(build(f"21 0{i} 00", f"SPEC {i}"))
    return d


def log_to(lines):
    return lambda m: lines.append(m)


def test_request_preview_run(folder, tmp_path):
    s = Session(folder=folder, out_root=tmp_path / "out", client=FakeClient(
        resp(tool("submit_editspec", {"spec": REMOVE})), remove_outcome_proposal()))
    lines = []
    with pytest.raises(ValueError, match="Preview first"):
        s.run(False, False, log_to(lines))
    assert s.compile("remove all headers and footers", log_to(lines)) == "Compiled."
    text = s.make_preview(render=False, log=log_to(lines))
    assert "2 plan cleanly" in text and s.preview.token in text
    assert s.run_blocked_reason()
    s.approve_outcomes(s.outcome_review["digest"])
    before = {f: f.read_bytes() for f in folder.glob("*.docx")}
    out = s.run(in_place=False, render=False, log=log_to(lines))
    assert "PASS 2" in out and "your files were not changed" in out
    assert {f: f.read_bytes() for f in folder.glob("*.docx")} == before
    assert (s.last_run_dir / "outputs").is_dir()


def test_run_refuses_after_a_file_changed(folder, tmp_path):
    s = Session(folder=folder, out_root=tmp_path / "out")
    s.load_spec(SPECS / "remove-all-headers-footers.json")
    s.make_preview(render=False, log=lambda m: None)
    f = sorted(folder.glob("*.docx"))[0]
    os.utime(f, ns=(f.stat().st_atime_ns, f.stat().st_mtime_ns + 10_000_000))
    with pytest.raises(ValueError, match="changed since the preview"):
        s.run(False, False, lambda m: None)


def test_in_place_run_is_guarded(folder, tmp_path):
    s = Session(folder=folder, out_root=tmp_path / "out")
    s.load_spec(SPECS / "remove-all-headers-footers.json")
    s.make_preview(render=False, log=lambda m: None)
    before = {f: f.read_bytes() for f in folder.glob("*.docx")}
    out = s.run(in_place=True, render=False, log=lambda m: None)
    assert "written back" in out
    assert all(f.read_bytes() != before[f] for f in folder.glob("*.docx"))


def test_questions_and_unsupported(folder, tmp_path):
    s = Session(folder=folder, out_root=tmp_path / "out",
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
    s.client = FakeClient(header_outcome_proposal(),
                          resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}})))
    assert "passed verification" in generate_reviewed_custom(s)
    assert "passed its sample trials" in s.conversation.notes[-1]         # Claude hears what happened
    s.make_preview(render=False, log=lambda m: None)
    s.approve_outcomes(s.outcome_review["digest"])
    out = s.run(False, False, lambda m: None)
    assert "2 of 2 file(s) PASS" in out


def test_a_reply_replaces_the_custom_transform_and_voids_old_previews(folder, tmp_path):
    """Plan review blocker: after a custom transform, a reply that yields a spec must replace it (Run runs the spec,
    after its preview), and a reply that only asks a question leaves nothing runnable."""
    unsupported = {"request": "r", "summary": "s", "unsupported": "no op", "proposal": "set_header_distance"}
    s = Session(folder=folder, out_root=tmp_path / "out",
                client=FakeClient(resp(tool("submit_editspec", {"spec": unsupported, "custom_transform": "possible"})),
                                  header_outcome_proposal(),
                                  resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}})),
                                  resp(tool("submit_editspec", {"spec": REMOVE}, "s2")),
                                  remove_outcome_proposal(),
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


def test_a_reply_refuses_when_the_files_changed(folder, tmp_path):
    s = Session(folder=folder, out_root=tmp_path / "out",
                client=FakeClient(resp(tool("ask_user", {"questions": ["Which sections?"]}))))
    s.compile("remove some headers", lambda m: None)
    (folder / "21 99 00 – NEW.docx").write_bytes(build("21 99 00", "NEW"))
    with pytest.raises(ValueError, match="Start over"):
        s.reply("all of them")


def test_errors_are_plain(tmp_path):
    with pytest.raises(ValueError, match="choose the folder"):
        Session(out_root=tmp_path).files()
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="no .docx files"):
        Session(folder=empty, out_root=tmp_path).files()
    with pytest.raises(ValueError, match="type what you want"):
        Session(folder=empty, out_root=tmp_path).compile("  ", lambda m: None)


# ---- the real window (Qt, offscreen) ------------------------------------------------------------------

@pytest.fixture
def qt(tmp_path, monkeypatch):
    """A QApplication, a private settings folder, no update check and no API key from the developer's machine."""
    from qt_support import close_windows, qt_app_or_skip
    app = qt_app_or_skip()
    monkeypatch.setenv("SPEC_SURGEON_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("SPEC_SURGEON_NO_UPDATE_CHECK", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
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


def test_the_window_previews_and_runs_a_spec_file(qt, folder, tmp_path):
    s = Session(out_root=tmp_path / "out")
    s.load_spec(SPECS / "remove-all-headers-footers.json")
    w, ui = window(qt, s)
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["render"].set(False)
    assert not ui["buttons"]["run"].isEnabled()                 # nothing runs before a preview
    ui["buttons"]["preview"].click()
    text = ui["wait"]()
    assert "2 plan cleanly" in text, text
    assert ui["status"].get() == "Ready."
    assert ui["preview_model"].badges() == ["planned", "planned"]
    assert w.stack.currentWidget() is w.preview_page
    before = {f: f.read_bytes() for f in folder.glob("*.docx")}
    ui["buttons"]["run"].click()
    text = ui["wait"](120)
    assert "PASS 2" in text, text
    assert ui["results_model"].badges() == ["pass", "pass"]
    assert all("Verified" in r.cells[2] and "written back" not in r.cells[2] for r in ui["results_model"].rows)
    assert {f: f.read_bytes() for f in folder.glob("*.docx")} == before    # copies only
    assert w.stack.currentWidget() is w.run_page and w.run_page.results.isVisible() is not None


def test_run_is_enabled_only_while_the_preview_is_current(qt, folder, tmp_path):
    s = Session(out_root=tmp_path / "out")
    s.load_spec(SPECS / "remove-all-headers-footers.json")
    w, ui = window(qt, s)
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["render"].set(False)
    ui["buttons"]["preview"].click()
    ui["wait"]()
    assert ui["buttons"]["run"].isEnabled()
    ui["request"].set("change all fonts to Arial")              # a typed request replaces the loaded spec
    assert not ui["buttons"]["run"].isEnabled()
    assert "press Preview again" in w.run_page.run_hint.text()
    ui["request"].set("")
    assert ui["buttons"]["run"].isEnabled()
    other = tmp_path / "Other"
    other.mkdir()
    ui["vars"]["folder"].set(str(other))
    assert not ui["buttons"]["run"].isEnabled()
    assert w.do_run() is False


def test_the_window_shows_claude_s_questions(qt, folder, tmp_path):
    s = Session(out_root=tmp_path / "out", client=FakeClient(resp(tool("ask_user", {"questions": ["Which sections?"]}))))
    w, ui = window(qt, s)
    ui["vars"]["folder"].set(str(folder))
    ui["request"].set("remove some headers")
    ui["buttons"]["preview"].click()
    ui["wait"]()
    assert ui["status"].get() == "Ready."
    assert not ui["chat"].isHidden() and "Claude needs your reply" in w.request_page.info.title.text()
    texts = [lab.text() for lab in ui["chat"].findChildren(type(w.request_page.info.text))]
    assert any("Which sections?" in t for t in texts) and any(t == "remove some headers" for t in texts)
    assert not ui["buttons"]["run"].isEnabled()
    from spec_surgeon.ui.widgets import STEP_STATUS_ROLE
    assert w.nav.item(1).data(STEP_STATUS_ROLE) == "Waiting for your reply"
    w._refresh_files()                                        # the folder check runs on a 250 ms timer
    assert "waiting for your reply" in w.request_page.preview_hint.text()
    from PySide6.QtWidgets import QCheckBox                   # the chat replaced the Answers box and the codegen switch
    assert "answers" not in ui and "codegen" not in ui["vars"] and "codegen" not in ui["buttons"]
    assert not any(hasattr(w.request_page, n) for n in ("answers_card", "answers_label", "questions", "codegen_cb"))
    assert not [c.text() for c in w.findChildren(QCheckBox) if "AI-written" in c.text()]
    # Preview again while Claude waits: nothing is recompiled (no second scripted turn is needed)
    assert w.do_preview() is False and "waiting for your reply" in w.request_page.info.title.text()


def test_the_window_chat_choice_reply_preview_and_start_over(qt, folder, tmp_path):
    """The whole loop in the window: Claude asks with choices; picking one sends it; the spec that comes back is
    previewed; a reply typed under the preview revises it; Start over forgets the conversation."""
    fake = FakeClient(resp(tool("ask_user", {"understanding": "Remove the headers and footers.",
                                             "choices": [{"label": "Every section", "kind": "clarify"},
                                                         {"label": "Stamp instead", "kind": "alternative",
                                                          "wording": "ISSUED FOR PERMIT"}]})),
                      resp(tool("submit_editspec", {"spec": REMOVE}, "s2")),
                      remove_outcome_proposal(),
                      resp(tool("submit_editspec", {"spec": REMOVE}, "s3")),
                      remove_outcome_proposal())
    s = Session(out_root=tmp_path / "out", client=fake)
    w, ui = window(qt, s)
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["render"].set(False)
    ui["request"].set("remove all headers and footers")
    w.resize(1100, 640)                                       # short enough that the chat starts below the fold
    w.show()
    ui["buttons"]["preview"].click()
    ui["wait"]()
    qt[0].processEvents()
    choices = ui["choices"]()
    assert len(choices) == 2
    chat = w.request_page.chat                                # scrolled to Claude's choices, the reply box and Send
    assert not chat.reply.visibleRegion().isEmpty() and not chat.send_btn.visibleRegion().isEmpty()
    labels = [lab.text() for lab in ui["chat"].findChildren(type(w.request_page.info.text))]
    assert any("Exact words: «ISSUED FOR PERMIT»" in t and "approves these exact words" in t for t in labels)
    choices[0][1].click()                                     # "Every section"
    ui["wait"]()
    assert w.stack.currentWidget() is w.preview_page and not ui["buttons"]["run"].isEnabled()
    w.preview_page.outcome_approve_btn.click()
    assert ui["buttons"]["run"].isEnabled()
    assert fake.requests[1]["messages"][-1]["content"][0]["content"].startswith("The user chose: «Every section»")
    assert ui["choices"]() == [] and not ui["preview_chat"].isHidden()
    key_before = w.preview_key
    ui["preview_chat"].reply.setPlainText("remove every section's header and footer")
    ui["preview_chat"].send_btn.click()
    ui["wait"]()
    last = fake.requests[3]["messages"][-1]["content"]
    assert last[0]["tool_use_id"] == "s2" and "THE PREVIEW OF YOUR SPEC" in last[1]["text"]
    assert last[-1]["text"] == "The user replied: remove every section's header and footer"
    assert w.preview_key != key_before and not ui["buttons"]["run"].isEnabled()
    w.preview_page.outcome_approve_btn.click()
    assert ui["buttons"]["run"].isEnabled()
    assert ui["preview_chat"].reply.toPlainText() == ""
    assert w.preview_page.save_btn.parentWidget() is not w.preview_page.continue_btn.parentWidget()
    ui["buttons"]["start_over"].click()
    assert s.conversation is None and s.spec is None and ui["chat"].isHidden()
    assert not ui["buttons"]["run"].isEnabled()
    assert w.preview_page.body.isHidden() and not w.preview_page.empty.isHidden()


def test_the_window_blocks_replies_to_a_different_request(qt, folder, tmp_path):
    s = Session(out_root=tmp_path / "out", client=FakeClient(resp(tool("ask_user", {"questions": ["Which?"]}))))
    w, ui = window(qt, s)
    ui["vars"]["folder"].set(str(folder))
    ui["request"].set("remove some headers")
    ui["buttons"]["preview"].click()
    ui["wait"]()
    assert ui["buttons"]["send"].isEnabled()
    ui["request"].set("something else entirely")
    assert not ui["buttons"]["send"].isEnabled() and "not the one this conversation" in ui["chat"].hint.text()
    assert w.do_send("all") is False


def test_an_error_is_shown_not_swallowed(qt, tmp_path):
    w, ui = window(qt, Session(out_root=tmp_path / "out"))
    ui["request"].set("remove all headers and footers")
    ui["buttons"]["preview"].click()
    text = ui["wait"]()
    assert "ERROR: ValueError: choose the folder" in text
    assert ui["status"].get() == "Error — see the log."
    assert not w.request_page.info.isHidden() and "choose the folder" in w.request_page.info.text.text()


def test_template_in_the_folder_is_left_out_of_the_window_count(qt, folder, tmp_path):
    from make_fixtures import build_template
    tpl = folder / "00 00 00 TEMPLATE.docx"
    tpl.write_bytes(build_template())
    w, ui = window(qt, Session(out_root=tmp_path / "out"))
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["template"].set(str(tpl))
    w._refresh_files()
    assert w.files_page.file_list.count() == 2
    assert w.files_page.folder_hint.text().startswith("2 Word file(s) ready")


def test_template_in_the_folder_is_neither_previewed_nor_run(folder, tmp_path):
    """Codex review on PR #4: preview and Run must use the same filtered file set — a template file selected in the
    window and sitting in the specs folder is excluded from both (else Run always refused, or edited the template)."""
    from make_fixtures import build_template
    tpl = folder / "00 00 00 TEMPLATE.docx"
    tpl.write_bytes(build_template())
    s = Session(folder=folder, template=tpl, out_root=tmp_path / "out")
    s.load_spec(SPECS / "remove-all-headers-footers.json")
    s.make_preview(render=False, log=lambda m: None)
    assert len(s.preview.batch_plan.files) == 2
    before = tpl.read_bytes()
    out = s.run(in_place=True, render=False, log=lambda m: None)
    assert "2 file(s): PASS 2" in out and tpl.read_bytes() == before


def test_generated_run_refuses_unpreviewed_files(folder, tmp_path):
    """Codex review on PR #4: an AI-written transform runs only on the files it was tested on."""
    unsupported = {"request": "r", "summary": "s", "unsupported": "no op", "proposal": "set_header_distance"}
    s = Session(folder=folder, out_root=tmp_path / "out",
                client=FakeClient(resp(tool("submit_editspec", {"spec": unsupported, "custom_transform": "possible"})),
                                  header_outcome_proposal(),
                                  resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}}))))
    s.compile("Set every section's header distance to 900 twips.", lambda m: None)
    assert "passed verification" in generate_reviewed_custom(s)
    s.make_preview(render=False, log=lambda m: None)
    s.approve_outcomes(s.outcome_review["digest"])
    (folder / "21 99 00 – NEW.docx").write_bytes(build("21 99 00", "NEW"))
    with pytest.raises(ValueError, match="since the AI-written transform was tested"):
        s.run(False, False, lambda m: None)


# ---- updates in the window ----------------------------------------------------------------------------------

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
    from spec_surgeon.settings import Settings, load_settings
    monkeypatch.delenv("SPEC_SURGEON_NO_UPDATE_CHECK")
    checker = _checker(["update"])
    w, ui = window(qt, Session(out_root=tmp_path / "out"), settings=Settings(), checker=checker)
    assert ui["banner"].isHidden()
    assert w.updates.auto_check() is True
    ui["wait"]()
    assert not ui["banner"].isHidden() and ui["banner"].mode == "available"
    assert "9.9.9 is available" in ui["banner"].title.text()
    assert ui["buttons"]["install_update"].isEnabled()
    assert w.settings.last_update_check > 0
    assert w.updates.auto_check() is False                      # once a day
    ui["banner"].btn_skip.click()
    assert ui["banner"].isHidden() and load_settings().skipped_version == "9.9.9"
    w.settings.last_update_check = 0
    w.updates.auto_check()
    ui["wait"]()
    assert ui["banner"].isHidden()                              # a skipped version stays quiet on automatic checks
    ui["buttons"]["check_updates"].click()
    ui["wait"]()
    assert not ui["banner"].isHidden() and "skip" in ui["banner"].text.text()
    assert checker.calls[-1] == (True, "9.9.9")


def test_manual_check_reports_up_to_date_and_errors(qt, tmp_path):
    from spec_surgeon.updater import UpdateError
    prompts_ = __import__("spec_surgeon.ui.app", fromlist=["RecordingPrompts"]).RecordingPrompts(answers=[False])
    checker = _checker(["current", UpdateError("Couldn't reach GitHub — check your internet connection or proxy.",
                                               "offline")])
    w, ui = window(qt, Session(out_root=tmp_path / "out"), checker=checker, prompts=prompts_)
    ui["buttons"]["check_updates"].click()
    ui["wait"]()
    assert prompts_.shown[-1][0] == "notify" and "up to date" in prompts_.shown[-1][2]
    ui["buttons"]["check_updates"].click()
    ui["wait"]()
    assert prompts_.shown[-1][0] == "ask" and "Couldn't check for updates" in prompts_.shown[-1][2]
    assert ui["banner"].isHidden()
    assert "Couldn't check for updates" in ui["log"].get()


def test_install_waits_for_a_running_job(qt, tmp_path):
    import threading
    gate = threading.Event()
    w, ui = window(qt, Session(out_root=tmp_path / "out"), checker=_checker(["update"]), installed=lambda: True)
    ui["buttons"]["check_updates"].click()
    ui["wait"]()
    assert ui["buttons"]["install_update"].isEnabled()
    assert w._work("Something long", lambda log: gate.wait(30) and "finished")
    qt[0].processEvents()
    assert w.busy and not ui["buttons"]["install_update"].isEnabled()
    assert not ui["banner"].busy_note.isHidden()
    assert w.updates.install_now() is False
    gate.set()
    ui["wait"]()
    assert ui["buttons"]["install_update"].isEnabled() and ui["banner"].busy_note.isHidden()


def test_install_hands_over_to_setup_and_closes(qt, tmp_path):
    import hashlib
    from spec_surgeon.settings import load_settings
    from spec_surgeon.updater import DownloadedInstaller
    exe = tmp_path / "SpecSurgeon-9.9.9-Setup-x64.exe"
    exe.write_bytes(b"MZ")
    launched, closed = [], []

    def downloader(info, dest, progress, cancel):
        progress(1, 2)
        return DownloadedInstaller(exe, hashlib.sha256(b"MZ").hexdigest(), info.version)

    w, ui = window(qt, Session(out_root=tmp_path / "out"), checker=_checker(["update"]), installed=lambda: True,
                   downloader=downloader, launcher=lambda dl, install_dir, log: launched.append((dl, install_dir, log)))
    w.quit_for_update = lambda: closed.append(True)
    ui["buttons"]["check_updates"].click()
    ui["wait"]()
    ui["buttons"]["install_update"].click()
    ui["wait"]()
    assert launched and launched[0][0].path == exe and launched[0][2].name == "install-9.9.9.log"
    assert closed == [True]
    assert load_settings().pending_update["version"] == "9.9.9"
    assert w.updating and not ui["buttons"]["preview"].isEnabled()


def test_a_failed_launch_is_reported_and_unblocks(qt, tmp_path):
    from spec_surgeon.settings import load_settings
    from spec_surgeon.updater import DownloadedInstaller, UpdateError

    def launcher(dl, install_dir, log):
        raise UpdateError("The downloaded installer changed after it was checked; it was not started.", "integrity")
    w, ui = window(qt, Session(out_root=tmp_path / "out"), checker=_checker(["update"]), installed=lambda: True,
                   downloader=lambda info, dest, progress, cancel: DownloadedInstaller(tmp_path / "x.exe", "0", "9.9.9"),
                   launcher=launcher)
    ui["buttons"]["check_updates"].click()
    ui["wait"]()
    ui["buttons"]["install_update"].click()
    ui["wait"]()
    assert ui["banner"].mode == "error" and "changed after it was checked" in ui["banner"].text.text()
    assert not w.updating and ui["buttons"]["preview"].isEnabled()
    assert load_settings().pending_update == {}


def test_a_pip_copy_gets_instructions_instead_of_an_install(qt, tmp_path):
    from spec_surgeon.ui.app import RecordingPrompts
    prompts_ = RecordingPrompts(answers=[False])
    w, ui = window(qt, Session(out_root=tmp_path / "out"), checker=_checker(["update"]), installed=lambda: False,
                   prompts=prompts_, downloader=lambda *a: pytest.fail("must not download"))
    ui["buttons"]["check_updates"].click()
    ui["wait"]()
    assert w.updates.install_now() is False
    assert "pip" in prompts_.shown[-1][2] or "release page" in prompts_.shown[-1][2]


def test_after_an_update_the_window_says_so(qt, tmp_path):
    from spec_surgeon import __version__
    from spec_surgeon.settings import Settings
    w, ui = window(qt, Session(out_root=tmp_path / "out"),
                   settings=Settings(pending_update={"version": __version__, "log": ""}))
    w.updates.startup()
    assert ui["banner"].mode == "updated" and __version__ in ui["banner"].title.text()
    assert w.settings.pending_update == {} and w.settings.last_run_version == __version__
    w2, ui2 = window(qt, Session(out_root=tmp_path / "out"),
                     settings=Settings(pending_update={"version": "99.0.0", "log": str(tmp_path / "x.log")}))
    w2.updates.startup()
    assert ui2["banner"].mode == "failed" and "didn't finish" in ui2["banner"].title.text()


# ---- what the window remembers ------------------------------------------------------------------------------

def test_the_window_remembers_folders_but_not_the_risky_switches_or_the_key(qt, folder, tmp_path):
    import os
    from spec_surgeon.settings import settings_path
    from spec_surgeon.ui.settings_dialog import SettingsDialog
    w, ui = window(qt)                                   # a fresh start: values come from settings.json
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["out"].set(str(tmp_path / "Results"))
    ui["vars"]["render"].set(False)
    ui["vars"]["in_place"].set(True)
    ui["request"].set("change all fonts to Arial")
    dlg = SettingsDialog(w)
    dlg.key.setText("sk-ant-test-not-a-real-key")
    dlg.apply()
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-test-not-a-real-key" and w.key_origin == "session"
    w.close()
    assert "sk-ant-test" not in settings_path().read_text(encoding="utf-8")
    w2, ui2 = window(qt)
    assert ui2["vars"]["folder"].get() == str(folder) and ui2["vars"]["out"].get() == str(tmp_path / "Results")
    assert ui2["vars"]["render"].get() is False and ui2["request"].get() == "change all fonts to Arial"
    assert ui2["vars"]["in_place"].get() is False


def test_closing_during_a_job_keeps_the_window_open(qt, tmp_path):
    import threading
    from spec_surgeon.ui.app import RecordingPrompts
    gate = threading.Event()
    prompts_ = RecordingPrompts(answers=[False])
    w, ui = window(qt, Session(out_root=tmp_path / "out"), prompts=prompts_)
    w.show()
    w._work("Something long", lambda log: gate.wait(30) and "finished")
    w.close()
    assert w.isVisible() and not prompts_.shown
    gate.set()
    ui["wait"]()


# ---- the rework after v0.4.1: the rail, the guided steps, Settings, Help, About ------------------------------

def test_the_rail_shows_where_each_step_stands(qt, folder, tmp_path):
    from spec_surgeon.ui.widgets import STEP_STATE_ROLE, STEP_STATUS_ROLE
    s = Session(out_root=tmp_path / "out")
    s.load_spec(SPECS / "remove-all-headers-footers.json")
    w, ui = window(qt, s)
    rail = lambda: [(w.nav.item(i).data(STEP_STATE_ROLE), w.nav.item(i).data(STEP_STATUS_ROLE)) for i in range(4)]
    assert not ui["buttons"]["next"].isEnabled() and "docx" in w.files_page.next_hint.text()
    assert not w.files_page.drop.isHidden() and w.files_page.file_list.isHidden()
    assert rail()[0] == ("current", "Pick the specs folder")
    assert rail()[2] == ("todo", "Check before anything runs")
    ui["vars"]["folder"].set(str(folder))
    w._refresh_files()
    assert ui["buttons"]["next"].isEnabled() and w.files_page.next_hint.text() == ""
    assert w.files_page.drop.isHidden() and w.files_page.file_list.count() == 2
    assert rail()[0] == ("current", "2 Word files ready")
    assert rail()[1][0] == "done" and "remove-all-headers-footers.json" in rail()[1][1]
    assert "Ctrl+Enter" in w.request_page.preview_hint.text()
    ui["vars"]["render"].set(False)
    ui["buttons"]["preview"].click()
    ui["wait"]()
    assert rail()[0][0] == "done" and rail()[2] == ("current", "2 plan cleanly · 0 set aside")
    assert w.preview_page.continue_btn.isEnabled() and w.preview_page.continue_hint.text() == ""
    ui["request"].set("change all fonts to Arial")
    assert rail()[2] == ("current", "Changed since the preview — preview again")
    assert "Preview again" in w.preview_page.continue_hint.text()
    ui["request"].set("")
    ui["buttons"]["run"].click()
    ui["wait"](120)
    assert rail()[3] == ("current", "2 passed · no problems") and w.run_page.empty.isHidden()


def test_settings_is_down_to_the_results_folder_and_the_key(qt, tmp_path):
    from spec_surgeon.ui.settings_dialog import SettingsDialog
    w, ui = window(qt, Session(out_root=tmp_path / "out"))
    dlg = SettingsDialog(w)
    assert hasattr(dlg, "out") and hasattr(dlg, "key") and hasattr(dlg, "remember")
    assert not any(hasattr(dlg, n) for n in ("theme", "native", "auto", "check_now", "clear_skip"))
    from spec_surgeon.ui.widgets import Card
    assert [c.title.text() for c in dlg.findChildren(Card) if hasattr(c, "title")] == \
        ["Results folder", "Anthropic API key"]
    dlg.out.setText(str(tmp_path / "Elsewhere"))
    dlg.apply()
    assert ui["vars"]["out"].get() == str(tmp_path / "Elsewhere")


def test_help_has_no_report_a_problem_and_the_sidebar_has_the_three_buttons(qt, tmp_path):
    from PySide6.QtWidgets import QMenu
    w, ui = window(qt, Session(out_root=tmp_path / "out"))
    help_menu = [m for m in w.menuBar().findChildren(QMenu) if m.title() == "&Help"][0]
    texts = [a.text() for a in help_menu.actions() if not a.isSeparator()]
    assert texts == ["Check for updates…", "What's new in this version", "User guide (online)", "Open logs folder",
                     "Save diagnostics bundle…", "About Spec Surgeon"]               # + the two diagnostics entries
    assert not hasattr(w, "act_issue")
    assert [b.text() for b in (w.settings_btn, w.check_btn, w.about_btn)] == \
        ["Settings", "Check for updates", "About Spec Surgeon"]


def test_about_says_who_made_it_the_license_and_how_to_reach_him(qt, tmp_path):
    from spec_surgeon import __version__, about
    from spec_surgeon.ui.about_dialog import AboutDialog
    w, ui = window(qt, Session(out_root=tmp_path / "out"))
    ui["buttons"]["about"].click()
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
    text = w.about_text()
    for value in (about.EMAIL, about.GITHUB_URL, about.LINKEDIN_URL, about.LICENSE_NAME, "Settings:"):
        assert value in text
    dlg.close()


def test_the_update_check_needs_no_setting(qt, tmp_path, monkeypatch):
    """Once a day, on its own: nothing to turn on, and the only off switch is the environment variable."""
    from spec_surgeon.settings import Settings
    monkeypatch.delenv("SPEC_SURGEON_NO_UPDATE_CHECK")
    checker = _checker(["current"])
    w, ui = window(qt, Session(out_root=tmp_path / "out"), settings=Settings(), checker=checker)
    assert w.updates.auto_check() is True
    ui["wait"]()
    assert checker.calls == [(False, "")] and w.settings.last_update_check > 0
    assert w.updates.auto_check() is False                       # not again today
    monkeypatch.setenv("SPEC_SURGEON_NO_UPDATE_CHECK", "1")
    w.settings.last_update_check = 0
    assert w.updates.auto_check() is False


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
    code = ("import sys, spec_surgeon.gui, spec_surgeon.cli, spec_surgeon.batch, spec_surgeon.updater, "
            "spec_surgeon.llm, spec_surgeon.conversation, "
            "spec_surgeon.settings, spec_surgeon.winapp, spec_surgeon.credentials, spec_surgeon.ui, "
            "spec_surgeon.ui.rows; "
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
                    not (rel.startswith("ui/") and rel not in ("ui/__init__.py", "ui/rows.py")):
                offenders.append(rel)
    assert offenders == []


def test_the_window_blocks_run_on_claude_s_findings_until_acknowledged(qt, folder, tmp_path):
    from spec_surgeon.vision import VisualReview
    s = Session(out_root=tmp_path / "out")
    w, ui = window(qt, s)
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["render"].set(False)
    s.load_spec(SPECS / "remove-all-headers-footers.json")
    ui["buttons"]["preview"].click()
    ui["wait"]()
    assert ui["buttons"]["run"].isEnabled()
    s.preview.review = VisualReview(summary="Looked at page 1.",
                                    findings=[{"severity": "block", "file": "x.docx", "page": 1, "text": "Logo squashed."}])
    w._fill_preview()
    w._refresh_run_state()
    p = w.preview_page
    assert not p.review_card.isHidden() and "Logo squashed." in p.review_lbl.text()
    assert "advisory" in p.review_card.helper.text().lower() and "PASS" not in p.review_lbl.text()
    assert not ui["buttons"]["run"].isEnabled() and "visual check flagged" in w.run_page.run_hint.text()
    assert "visual check flagged" in w.run_page.run_hint.toolTip()          # the full reason
    assert "Look at Claude's findings" in w.preview_page.continue_hint.text()
    from spec_surgeon.ui.widgets import STEP_STATUS_ROLE
    w._refresh_nav()
    assert w.nav.item(2).data(STEP_STATUS_ROLE) == "Look at Claude's findings before Run"
    assert w.do_run() is False
    assert "visual check flagged" in w.run_page.info.text.text()
    assert not ui["buttons"]["review_ack"].isHidden()
    ui["buttons"]["review_ack"].click()
    assert ui["buttons"]["run"].isEnabled() and ui["buttons"]["review_ack"].isHidden()
    assert "Acknowledged" in p.review_bar.title.text()
    assert w.run_page.run_hint.text() == "" and w.preview_page.continue_hint.text() == ""


def test_the_window_runs_the_custom_transform_offered_in_the_chat(qt, folder, tmp_path):
    unsupported = {"request": "r", "summary": "Set the header distance.", "unsupported": "no op sets it alone",
                   "proposal": "set_header_distance"}
    fake = FakeClient(resp(tool("submit_editspec", {"spec": unsupported, "custom_transform": "possible"})),
                      header_outcome_proposal(),
                      resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}})))
    s = Session(out_root=tmp_path / "out", client=fake)
    w, ui = window(qt, s)
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["render"].set(False)
    ui["request"].set("put the header 0.625 in from the top")
    ui["buttons"]["preview"].click()
    ui["wait"]()
    assert "No built-in operation" in w.request_page.info.title.text()
    (cid, button), = ui["choices"]()
    assert button.text() == "Do this"
    button.click()
    ui["wait"]()
    p = w.preview_page
    assert w.stack.currentWidget() is p and s.generated is None and not ui["buttons"]["run"].isEnabled()
    p.outcome_approve_btn.click()
    ui["wait"]()
    assert not p.generated_card.isHidden() and not p.samples_card.isHidden()
    assert p.samples.count() == 2                                              # both files fit the sample allowance
    assert ui["buttons"]["run"].isEnabled()
    assert not w.run_page.inplace_rb.isEnabled()                            # custom transforms write copies only
    before = {f: f.read_bytes() for f in folder.glob("*.docx")}
    ui["buttons"]["run"].click()
    ui["wait"]()
    assert ui["results_model"].badges() == ["pass", "pass"]
    assert {f: f.read_bytes() for f in folder.glob("*.docx")} == before


def test_the_window_continues_into_recovery_and_exports_its_verified_preview(qt, folder, tmp_path):
    goal = {"kind": "document_effects", "effects": [
        {"id": "distance", "type": "page_setup", "sections": "all", "margins": {"header": 900}}]}
    refusal = {"request": "r", "summary": "Set header distance", "unsupported": "No usable method",
               "proposal": "Check the requested page setup"}
    fake = FakeClient(resp(tool("submit_editspec", {"spec": refusal, "custom_transform": "outside_limits"})),
        resp(tool("propose_plan", {"contract": goal, "rationale": "Set header distance to 0.625 in."})))
    s = Session(out_root=tmp_path / "out", client=fake)
    w, ui = window(qt, s)
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["render"].set(False)
    ui["request"].set("put the header 0.625 in from the top")
    before = {f: f.read_bytes() for f in folder.glob("*.docx")}
    ui["buttons"]["preview"].click()
    ui["wait"]()
    assert s.turn.choices[0].kind == "recovery_plan"
    ((_, button),) = ui["choices"]()
    button.click()
    ui["wait"]()
    assert s.recovery.ready and s.conversation is not None
    assert w.stack.currentWidget() is w.preview_page
    assert ui["buttons"]["run"].isEnabled()
    ui["buttons"]["run"].click()
    ui["wait"]()
    assert ui["results_model"].badges() == ["pass", "pass"]
    assert {f: f.read_bytes() for f in folder.glob("*.docx")} == before


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
