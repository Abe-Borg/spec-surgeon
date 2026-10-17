"""Recovery surfaces preserve the investigation/export boundary and explicit visual acknowledgement."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from spec_surgeon.cli import main
from spec_surgeon.gui import Session
from spec_surgeon.ui.rows import recovery_rows, result_counts
from spec_surgeon.vision import VisualReview


def result(tmp_path, *, blocking=False, ready=True):
    review = VisualReview(findings=[{"severity": "block", "text": "Check the header", "file": "one.docx"}]
                          if blocking else [])
    source = tmp_path / "one.docx"
    source.write_bytes(b"unchanged original")
    candidate = tmp_path / "candidate.docx"
    candidate.write_bytes(b"verified candidate")
    item = SimpleNamespace(source=source, status="READY" if ready else "NEEDS_INPUT", reason="Review the result",
                           output=candidate if ready else None, output_md5="hash", case_id="case-1")
    return SimpleNamespace(out_dir=tmp_path / "recovery", request="Change fonts", files=[item],
                           samples=[], review=review, review_ack="", token="approved-files", token_inputs={},
                           questions=[] if ready else ["Which font?"], notes=[], ready=[item] if ready else [],
                           budget={}, to_text=lambda: "Recovery investigation: one candidate; inspect intended outcome.")


@pytest.fixture
def recovery_stub(monkeypatch, tmp_path):
    from spec_surgeon.recovery import RecoveryOptions
    state = SimpleNamespace(result=result(tmp_path), calls=[], exports=[])

    def recover_from_run(run, out, **kw):
        state.calls.append((run, out, kw))
        state.result.out_dir = out
        return state.result

    def deliver_recovery(value, **kw):
        state.exports.append((value, kw))
        return value.out_dir / "outputs"

    def resume_recovery(run, **kw):
        state.calls.append((run, run, kw))
        state.result.review_ack = ""
        return state.result

    module = SimpleNamespace(RecoveryOptions=RecoveryOptions, recover_from_run=recover_from_run,
                             recover=lambda *a, **kw: state.result, deliver_recovery=deliver_recovery,
                             resume_recovery=resume_recovery, load_recovery=lambda run: state.result)
    monkeypatch.setitem(sys.modules, "spec_surgeon.recovery", module)
    state.module = module
    return state


def test_cli_investigates_without_export_and_forwards_limits(recovery_stub, tmp_path, capsys):
    assert main(["resolve", str(tmp_path / "previous"), "--budget", "2.50", "--max-attempts", "2",
                 "--max-calls", "7", "--effort", "medium", "--allow-codegen", "--no-render", "--no-memory"]) == 0
    state = recovery_stub
    assert len(state.calls) == 1 and state.exports == []
    options = state.calls[0][2]["options"]
    assert (options.max_cost_usd, options.max_attempts, options.max_calls, options.effort) == (2.5, 2, 7, "medium")
    assert options.allow_codegen and not options.render and not options.use_memory
    assert "Not exported" in capsys.readouterr().out


def test_cli_yes_never_acknowledges_a_blocking_review(recovery_stub, tmp_path, capsys):
    recovery_stub.result = result(tmp_path, blocking=True)
    assert recovery_stub.result.review.blocking()
    assert main(["resolve", str(tmp_path), "--yes"]) == 2
    assert recovery_stub.exports == []
    assert "--yes does not acknowledge" in capsys.readouterr().err
    assert main(["resolve", str(tmp_path), "--yes", "--ack-review"]) == 0
    assert recovery_stub.exports[-1][1] == {"acknowledge_review": True}


def test_cli_no_ready_files_never_exports(recovery_stub, tmp_path):
    recovery_stub.result = result(tmp_path, ready=False)
    assert main(["resolve", str(tmp_path), "--yes", "--ack-review"]) == 2
    assert not recovery_stub.exports


def test_cli_recovery_has_no_inplace_escape_hatch(recovery_stub, tmp_path):
    with pytest.raises(SystemExit):
        main(["resolve", str(tmp_path), "--in-place", "--yes"])
    assert not recovery_stub.calls


def test_session_review_then_export_preserves_approval_and_originals(recovery_stub, tmp_path):
    recovery_stub.result = result(tmp_path, blocking=True)
    session = Session(out_root=tmp_path / "out", last_run_dir=tmp_path / "previous",
                      spec={"request": "Old request"})
    source = recovery_stub.result.files[0].source
    before = source.read_bytes()
    session.resolve_remaining(lambda _: None)
    assert session.spec is None and session.recovery is recovery_stub.result and not recovery_stub.exports
    with pytest.raises(ValueError, match="visual check"):
        session.run(False, False, lambda _: None)
    with pytest.raises(ValueError, match="not the current"):
        session.acknowledge_review("old-token")
    session.acknowledge_review(session.recovery.token)
    with pytest.raises(ValueError, match="copies only"):
        session.run(True, False, lambda _: None)
    session.run(False, False, lambda _: None)
    assert recovery_stub.exports[-1][1] == {"acknowledge_review": True}
    assert session.last_recovery is recovery_stub.result and source.read_bytes() == before
    session._set_outcome(spec={"request": "A new request"})
    assert session.recovery is None


def test_recovery_can_investigate_a_failed_compile_without_an_editspec(recovery_stub, tmp_path):
    seen = []
    recovery_stub.module.recover = lambda *a, **kw: seen.append((a, kw)) or recovery_stub.result
    session = Session(folder=tmp_path, out_root=tmp_path / "out",
                      selected_files=[recovery_stub.result.files[0].source],
                      out_dir=tmp_path / "current-conversation", last_run_dir=tmp_path / "older-run")
    session.conversation = SimpleNamespace(request="Fix the header", sources=["Fix the header", "Project 42"])
    session.resolve_remaining(lambda _: None)
    assert seen[0][0][0] == session.selected_files
    assert seen[0][0][1] == "Fix the header"
    assert seen[0][1]["spec"] is None and seen[0][1]["approved_words"] == ["Fix the header", "Project 42"]
    assert not recovery_stub.calls  # the unrelated older run was not retried


def test_resolving_again_retains_ready_copies_without_requiring_export(recovery_stub, tmp_path):
    session = Session(out_root=tmp_path / "out", last_run_dir=tmp_path / "original-run")
    session.resolve_remaining(lambda _: None)
    first_folder = session.recovery.out_dir
    candidates = list(session.recovery.ready)
    session.resolve_remaining(lambda _: None, request={"answer": "Arial", "question_id": "q1"})
    assert len(recovery_stub.calls) == 2 and not recovery_stub.exports
    assert session.recovery.out_dir == first_folder
    assert session.recovery.ready == candidates
    assert recovery_stub.calls[-1][2]["request"] == {"answer": "Arial", "question_id": "q1"}
    session.resolve_remaining(lambda _: None)
    assert recovery_stub.calls[-1][0] == first_folder  # current unresolved manifest, not the original batch again


def test_session_loads_saved_recovery_without_model_or_export(recovery_stub, tmp_path):
    session = Session(out_root=tmp_path / "out", client=None)
    session.load_recovery(tmp_path / "saved")
    assert session.recovery is recovery_stub.result
    assert session.selected_files == [recovery_stub.result.files[0].source]
    assert session.folder == tmp_path
    assert not recovery_stub.calls and not recovery_stub.exports


def test_recovery_rows_distinguish_ready_candidates_from_unresolved_files(tmp_path):
    value = result(tmp_path)
    assert recovery_rows(value, preview=True)[0].cells[2] == "Ready"
    assert recovery_rows(value, preview=True)[0].badge == "planned"
    value.files.append(SimpleNamespace(source=tmp_path / "two.docx", status="NEEDS_INPUT", reason="Which font?",
                                       output=None, case_id=""))
    rows = recovery_rows(value)
    assert result_counts(rows) == {"PASS": 1, "FAIL": 0, "EXCEPTION": 1}
    assert rows[1].cells[1] == "Needs input" and rows[1].open_path is None


def test_cli_memory_missing_case_is_an_error(tmp_path, capsys):
    assert main(["memory", "reject", "missing", "--note", "wrong font"]) == 2
    assert "no repair case" in capsys.readouterr().err


def test_cli_memory_feedback_and_forgetting_are_persistent(capsys):
    from spec_surgeon.repair_memory import CaseStore
    store = CaseStore()
    case_id = store.record({"request": "Change fonts", "status": "success", "structural_verified": True})
    assert main(["memory", "list"]) == 0
    assert case_id in capsys.readouterr().out
    assert main(["memory", "reject", case_id, "--note", "The footer font was wrong"]) == 0
    case = store.get(case_id)
    assert case["reuse_blocked"] and case["feedback"][-1]["note"] == "The footer font was wrong"
    assert main(["memory", "show", case_id]) == 0
    assert "The footer font was wrong" in capsys.readouterr().out
    assert main(["memory", "accept", case_id]) == 0
    assert store.get(case_id)["reuse_blocked"]  # accepting later never erases the earlier negative evidence
    assert main(["memory", "forget", case_id]) == 0
    assert store.get(case_id) is None


@pytest.fixture
def qt_env(monkeypatch):
    from qt_support import close_windows, qt_app_or_skip
    app = qt_app_or_skip()
    monkeypatch.setenv("SPEC_SURGEON_NO_UPDATE_CHECK", "1")
    windows = []
    yield app, windows
    close_windows(app, windows)


def test_window_resolves_reviews_and_exports_copies(qt_env, recovery_stub, tmp_path):
    from spec_surgeon.ui.app import RecordingPrompts, build_window
    recovery_stub.result = result(tmp_path, blocking=True)
    session = Session(folder=tmp_path, out_root=tmp_path / "out", last_run_dir=tmp_path / "previous")
    window = build_window(session, prompts=RecordingPrompts())
    qt_env[1].append(window)
    ui = window._spec_surgeon
    ui["vars"]["render"].set(False)
    window.run_page.recovery_budget.setValue(1.5)
    window.run_page.recovery_codegen.setChecked(True)
    ui["buttons"]["resolve"].click()
    log = ui["wait"]()
    assert "ERROR" not in log, log
    assert window.stack.currentWidget() is window.preview_page
    assert "Recovery:" in window.preview_page.request_lbl.text()
    assert not window.run_page.inplace_rb.isEnabled()
    assert not ui["buttons"]["run"].isEnabled()
    assert recovery_stub.calls[0][2]["options"].max_cost_usd == 1.5
    assert recovery_stub.calls[0][2]["options"].allow_codegen
    ui["buttons"]["review_ack"].click()
    assert ui["buttons"]["run"].isEnabled()
    ui["buttons"]["run"].click()
    log = ui["wait"]()
    assert "ERROR" not in log, log
    assert recovery_stub.exports[-1][1] == {"acknowledge_review": True}
    assert ui["results_model"].badges() == ["pass"]


def test_window_reopens_saved_recovery_keylessly(qt_env, recovery_stub, tmp_path, monkeypatch):
    from spec_surgeon import llm
    from spec_surgeon.ui.app import RecordingPrompts, build_window
    monkeypatch.setattr(llm, "credentials_found", lambda: False)
    window = build_window(Session(out_root=tmp_path / "out"),
                          prompts=RecordingPrompts(files=[str(tmp_path / "saved")]))
    qt_env[1].append(window)
    window.act_recovery.trigger()
    log = window._spec_surgeon["wait"]()
    assert "ERROR" not in log, log
    assert window.session.recovery is recovery_stub.result
    assert window.stack.currentWidget() is window.preview_page
    assert window.run_page.run_btn.isEnabled()
    assert not recovery_stub.calls and not recovery_stub.exports


def test_window_consumes_scoped_answer_once_without_global_replay(qt_env, recovery_stub, tmp_path):
    from spec_surgeon.ui.app import RecordingPrompts, build_window
    value = recovery_stub.result
    value.question_records = [{"id": "q0001", "question": "Which project wording?", "status": "open",
                               "files": [str(value.files[0].source)]}]
    window = build_window(Session(folder=tmp_path, out_root=tmp_path / "out", recovery=value),
                          prompts=RecordingPrompts())
    qt_env[1].append(window)
    window._fill_preview()
    window.run_page.recovery_question.setCurrentIndex(1)
    window.run_page.recovery_request.setPlainText("ALPHA PROJECT")
    assert window.do_resolve()
    log = window._spec_surgeon["wait"]()
    assert "ERROR" not in log, log
    assert recovery_stub.calls[-1][2]["request"] == {"answer": "ALPHA PROJECT", "question_id": "q0001"}
    assert window.run_page.recovery_request.toPlainText() == ""
    assert window.do_resolve()
    window._spec_surgeon["wait"]()
    assert recovery_stub.calls[-1][2]["request"] is None


def test_new_investigation_never_inherits_old_question_scope(qt_env, recovery_stub, tmp_path):
    from spec_surgeon.ui.app import RecordingPrompts, build_window
    window = build_window(Session(folder=tmp_path, out_root=tmp_path / "out", last_run_dir=tmp_path / "batch"),
                          prompts=RecordingPrompts())
    qt_env[1].append(window)
    page = window.run_page
    page.recovery_question.addItem("Question from an old investigation", "q0001")
    page.recovery_question.setCurrentIndex(1)
    page.recovery_request.setPlainText("Clarification for this batch")
    assert window.do_resolve()
    log = window._spec_surgeon["wait"]()
    assert "ERROR" not in log, log
    assert recovery_stub.calls[-1][2]["request"] == "Clarification for this batch"


def test_initial_session_refuses_scoped_answer_instead_of_crashing(tmp_path):
    session = Session(folder=tmp_path, out_root=tmp_path / "out")
    with pytest.raises(ValueError, match="existing recovery"):
        session.resolve_remaining(lambda _: None, request={"answer": "Arial", "question_id": "q0001"})


def test_reopened_recovery_preserves_saved_resource_and_privacy_settings(qt_env, recovery_stub, tmp_path):
    from spec_surgeon.ui.app import RecordingPrompts, build_window
    snapshot = dict(max_cost_usd=1.25, max_attempts=2, max_calls=4, max_output_tokens=1000,
                    max_total_tokens=5000, effort="medium", allow_codegen=True, use_memory=False,
                    render=False, model="saved-model", renderer="none", group_call_slice=8,
                    critic_review=False, max_context_tokens=64000)
    snapshot = vars(recovery_stub.module.RecoveryOptions(**snapshot))
    recovery_stub.result.options_snapshot = snapshot
    window = build_window(Session(out_root=tmp_path / "out"),
                          prompts=RecordingPrompts(files=[str(tmp_path / "saved")]))
    qt_env[1].append(window)
    window.do_load_recovery()
    window._spec_surgeon["wait"]()
    assert window.run_page.recovery_budget.value() == 1.25
    assert not window.run_page.recovery_memory.isChecked()
    assert not window.request_page.render_cb.isChecked()
    assert window.do_resolve()
    log = window._spec_surgeon["wait"]()
    assert "ERROR" not in log, log
    options = recovery_stub.calls[-1][2]["options"]
    assert vars(options) == snapshot
