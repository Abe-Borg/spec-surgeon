"""User feedback comes from real verdicts/refusals; the original diagnostic evidence is preserved."""
import copy
from types import SimpleNamespace

from make_fixtures import build_dirty
from spec_surgeon import verify
from spec_surgeon.settings import Settings
from spec_surgeon.ui_feedback import failed_preview_checks
from spec_surgeon.web.server import WindowState
from test_ui_preview_regressions import fail_checks, task_for


def test_naming_refusal_is_readable_before_and_after_create_without_changing_evidence(tmp_path):
    task, _ = task_for(tmp_path)
    empty = tmp_path / "empty.docx"
    empty.write_bytes(build_dirty("empty_body"))
    task.attach([empty])
    view = task.perform("recipe", recipe="name_files", inputs={})
    exception = task.session.preview.batch_plan.exceptions[0].exception
    saved = copy.deepcopy(exception)
    assert "pattern" in exception["reason"] and exception["code"] == "not-found"
    reason = view["set_aside"][0]["reason"]
    assert "section number or title" in reason and "SECTION heading" in reason
    assert "op #" not in reason and "pattern" not in reason
    view = task.perform("primary", token=view["token"])
    assert view["left_untouched"][0]["reason"] == reason
    assert exception == saved


def test_watermark_refusal_explains_the_missing_header_in_the_preview(tmp_path):
    task, _ = task_for(tmp_path)
    view = task.perform("recipe", recipe="remove_headers_footers", inputs={})
    task.perform("primary", token=view["token"])
    task.session.out_root = tmp_path / "next-run"
    view = task.perform("recipe", recipe="watermark", inputs={"text": "DRAFT"})
    reason = view["set_aside"][0]["reason"]
    assert "no header to hold a watermark" in reason and "Word" in reason
    assert "op #" not in reason and "add_watermark" not in reason
    assert task.session.preview.batch_plan.exceptions[0].exception["code"] == "no-header"


def test_failed_checks_have_the_same_plain_explanation_in_streams_and_refreshes(tmp_path, monkeypatch):
    task, _ = task_for(tmp_path)
    fail_checks(monkeypatch, verify)
    task.perform("recipe", recipe="remove_highlight", inputs={})
    state = WindowState(task, Settings(), token="feedback-test")
    sample = state.view()["samples"][0]
    assert sample["passed"] is False
    assert sample["failures"] == [{"check": "V2", "reason": "The copy's wording does not match the change predicted by the plan."}]
    assert task._samples_view()["samples"][0]["failures"] == sample["failures"]


def test_a_later_passing_stage_does_not_hide_an_earlier_failed_check():
    sample = SimpleNamespace(passed=False, checks={"V2": "PASS"},
                             stages=[{"checks": {"V2": "FAIL"}}, {"checks": {"V2": "PASS"}}])
    assert [failure["check"] for failure in failed_preview_checks(sample)] == ["V2"]


def test_a_missing_renderer_is_not_a_failed_verification():
    assert failed_preview_checks(SimpleNamespace(passed=True, checks={"V6": "NA"}, stages=[])) == []
