"""Evaluation measurements and scripted end-to-end recovery evidence, never live benchmarks."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from fake_claude import FakeClient, resp, tool
from make_fixtures import build_reference_spec, build_sized
from spec_surgeon import recovery
from spec_surgeon.editspec import load_spec, plan_spec
from spec_surgeon.fingerprint import fingerprint
from spec_surgeon.ops import JobException
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.recovery_evaluation import EvaluationError, evaluate, main
from spec_surgeon.repair_memory import CaseStore


def _report(*, cost=0.25, ready=True, review=None):
    return {"files": [{"source": "/not-opened/source.docx", "output": "/not-opened/output.docx",
                       "status": "READY" if ready else "UNRESOLVED", "outcomes": [], "delivered_to": None}],
            "attempts": [{"number": 1}], "seconds": 2.5, "key": {"assertions": []},
            "budget": {"calls": 1, "estimated_cost_usd": cost, "usage": {"cost_usd": cost},
                       "exhausted_reason": None}, "visual_review": review}


def _manifest(tmp_path, cases):
    entries = []
    for i, (report, extra) in enumerate(cases):
        name = f"report-{i}.json"
        (tmp_path / name).write_text(json.dumps(report), encoding="utf-8")
        entries.append({"id": f"case-{i}", "report": name, **extra})
    manifest = tmp_path / "evaluation.json"
    manifest.write_text(json.dumps({"version": 1, "cases": entries}), encoding="utf-8")
    return manifest


def _accepted():
    return {"request_satisfied": True, "content_preserved": True, "visual_checked": True,
            "reviewer": "Test reviewer", "notes": "Scripted human annotation, not a real review."}


def test_readiness_delivery_and_advisory_review_never_supply_human_acceptance(tmp_path):
    report = _report(review={"findings": [], "summary": "No visible issues.", "skipped": ""})
    report["files"][0]["delivered_to"] = "/not-opened/delivered.docx"
    manifest = _manifest(tmp_path, [(report, {"execution": "scripted", "expect": {"ready": 1, "delivered": 1}})])
    result = evaluate(manifest)
    row = result["cases"][0]
    assert row["ready"] == row["delivered"] == 1
    assert row["model_visual_review"] == "advisory_no_findings"
    assert not row["human_accepted"] and row["human_review"]["request_satisfied"] is None
    assert row["final_assertion_count"] == row["final_assertions_passed_files"] == 0
    assert row["expectation_status"] == "PASS"
    assert result["summary"]["live_cost_per_human_accepted_batch_usd"] is None


def test_live_cost_per_accepted_batch_includes_failures_and_excludes_scripted_usage(tmp_path):
    manifest = _manifest(tmp_path, [
        (_report(cost=1), {"execution": "live", "cohort": "cold", "human_review": _accepted()}),
        (_report(cost=2, ready=False), {"execution": "live", "cohort": "warm", "expect": {"unresolved": 1}}),
        (_report(cost=100), {"execution": "scripted", "human_review": _accepted()}),
    ])
    result = evaluate(manifest)
    summary = result["summary"]
    assert summary["live_batches"] == 2 and summary["live_human_accepted_batches"] == 1
    assert summary["live_estimated_cost_usd"] == summary["live_cost_per_human_accepted_batch_usd"] == 3
    by_cohort = {g["cohort"]: g for g in result["groups"]}
    assert by_cohort["cold"]["live_cost_per_human_accepted_batch_usd"] == 1
    assert by_cohort["warm"]["live_cost_per_human_accepted_batch_usd"] is None


def test_unknown_cost_does_not_become_zero_or_disappear_from_live_economics(tmp_path):
    report = _report(cost=None)
    # Known reported usage alone is incomplete: a failed stream can retain a reservation.
    report["budget"]["usage"]["cost_usd"] = 0.1
    manifest = _manifest(tmp_path, [(_report(cost=1), {"execution": "live", "human_review": _accepted()}),
                                    (report, {"execution": "live"})])
    result = evaluate(manifest)
    assert result["cases"][1]["estimated_cost_usd"] is None
    assert result["summary"]["live_estimated_cost_usd"] is None
    assert result["summary"]["live_cost_per_human_accepted_batch_usd"] is None


def test_continuation_costs_and_calls_include_prior_resource_segments(tmp_path):
    report = _report(cost=2)
    report["resource_history"] = [{"calls": 7, "estimated_cost_usd": 5,
                                   "usage": {"cost_usd": 4, "served_models": ["prior-model"]}}]
    row = evaluate(_manifest(tmp_path, [(report, {"execution": "live", "human_review": _accepted()})]))["cases"][0]
    assert row["calls"] == 8 and row["estimated_cost_usd"] == 7
    assert row["reported_usage_cost_usd"] == 6 and row["resource_segment_count"] == 2
    assert row["served_models"] == ["prior-model"]


def test_unknown_prior_spend_does_not_disappear_after_resuming(tmp_path):
    report = _report(cost=2)
    report["resource_history"] = [{"calls": 1, "estimated_cost_usd": None, "usage": {"cost_usd": None}}]
    result = evaluate(_manifest(tmp_path, [(report, {"execution": "live", "human_review": _accepted()})]))
    assert result["cases"][0]["estimated_cost_usd"] is None
    assert result["summary"]["live_cost_per_human_accepted_batch_usd"] is None


def test_budget_stopped_case_can_meet_expectations_without_becoming_accepted(tmp_path):
    report = _report(ready=False)
    report["budget"]["exhausted_reason"] = "recovery call limit reached"
    report["visual_review"] = {"skipped": "Rendering off", "findings": []}
    manifest = _manifest(tmp_path, [(report, {"execution": "scripted", "expect": {
        "budget_stopped": True, "calls_max": 1, "unresolved": 1}, "human_review": _accepted()})])
    row = evaluate(manifest)["cases"][0]
    assert row["expectation_status"] == "PASS" and not row["human_accepted"]
    assert row["model_visual_review"] == "not_reviewed"


def test_partial_final_assertion_evidence_does_not_count_as_full_coverage(tmp_path):
    report = _report()
    report["key"]["assertions"] = [{"kind": "effective_font", "value": "Arial"},
                                    {"kind": "page_setup", "margins": {"left": 1440}}]
    report["files"][0]["outcomes"] = [{"kind": "effective_font", "status": "PASS"}]
    row = evaluate(_manifest(tmp_path, [(report, {"human_review": _accepted()})]))["cases"][0]
    assert row["ready"] == 1 and row["final_assertion_count"] == 2
    assert row["final_assertions_passed_files"] == 0 and not row["human_accepted"]


def test_local_evaluation_cli_reads_reports_only_and_reports_expectation_failures(tmp_path, monkeypatch):
    # Attempting to build a client is an immediate failure, in addition to conftest's seal.
    monkeypatch.setattr("spec_surgeon.llm.client", lambda: pytest.fail("evaluation must never access a model"))
    manifest = _manifest(tmp_path, [(_report(), {"expect": {"ready": 0}})])
    before = manifest.read_bytes()
    output = tmp_path / "results"
    assert main([str(manifest), "--out", str(output)]) == 1
    data = json.loads((output / "evaluation.json").read_text())
    assert data["summary"]["expectation_failures"] == 1
    assert len(data["cases"][0]["report_sha256"]) == 64
    assert "ready: expected 0, observed 1" in (output / "EVALUATION.md").read_text()
    assert manifest.read_bytes() == before
    with pytest.raises(SystemExit) as error:
        main([str(manifest), "--out", str(output)])
    assert error.value.code == 2


@pytest.mark.parametrize("extra", [
    {"execution": "inferred-live"}, {"human_review": {"request_satisfied": "true"}},
    {"human_interventions": -1}, {"expect": {"calls_max": True}}, {"expect": {"typo": 1}},
])
def test_invalid_measurements_are_refused_instead_of_misreported(tmp_path, extra):
    with pytest.raises(EvaluationError):
        evaluate(_manifest(tmp_path, [(_report(), extra)]))


def _template_goal(template):
    path = Path(__file__).resolve().parent.parent / "examples/specs/match-template-spec-header-footer.json"
    goal = load_spec(path)
    goal["request"] = "Match the architect's header and footer using each spec's own section number and title."
    params = goal["ops"][0]["params"]
    params["template"] = str(template)
    params["slot_fills"]["SECTION_TITLE"] = {
        "rule": "regex", "source": "document", "pattern": "^NOT A SECTION TITLE: (.+)$",
        "in_template": "DOOR HARDWARE"}
    return goal


def _header_footer_text(path):
    package = DocxPackage.from_path(path)
    return "\n".join("".join(t.text or "" for t in package.xml(part).iter(W + "t"))
                     for part in package.text_parts() if part.startswith(("word/header", "word/footer")))


def test_repaired_extraction_reuses_current_project_words_and_rebound_template_with_no_model_call(tmp_path):
    """Real engine/verifier, two projects and one scripted investigation; no live reliability claim."""
    architect = tmp_path / "architect.docx"
    architect.write_bytes(build_reference_spec())
    first = tmp_path / "project-a.docx"
    first.write_bytes(build_sized("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    goal = _template_goal(architect)
    with pytest.raises(JobException):
        plan_spec(DocxPackage.from_path(first), goal)
    repaired = copy.deepcopy(goal)
    repaired["ops"][0]["params"]["slot_fills"]["SECTION_TITLE"] = {
        "rule": "paragraph_after", "source": "document", "anchor_regex": r"^SECTION\s+\d{2}\s\d{2}\s\d{2}$",
        "in_template": "DOOR HARDWARE"}
    client = FakeClient(resp(tool("submit_repair", {"candidate": {"kind": "spec", "spec": repaired}})))
    options = recovery.RecoveryOptions(render=False, renderer="none", max_cost_usd=20)
    before = first.read_bytes()
    result = recovery.recover([first], goal["request"], tmp_path / "first-run", spec=goal,
                              options=options, client=client)
    assert len(result.ready) == 1 and len(client.requests) == 1
    assert first.read_bytes() == before
    assert "WET-PIPE SPRINKLER SYSTEMS" in _header_footer_text(result.ready[0].output)
    assert result.review.skipped and not result.ready[0].delivered_to
    case = CaseStore().get(result.ready[0].case_id)
    assert case["structural_verified"] and case["visual_review"]["skipped"]

    # Another project has the same structure; its template has identical bytes at a new path.
    project = tmp_path / "next-project"
    project.mkdir()
    second = project / "section.docx"
    second.write_bytes(build_sized("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS"))
    moved_template = project / "architect-reference.docx"
    moved_template.write_bytes(architect.read_bytes())
    new_goal = _template_goal(moved_template)
    assert fingerprint(DocxPackage.from_path(first))["pattern_id"] == fingerprint(DocxPackage.from_path(second))["pattern_id"]
    no_calls = FakeClient()
    next_before = second.read_bytes()
    reused = recovery.recover([second], new_goal["request"], tmp_path / "second-run", spec=new_goal,
                              options=options, client=no_calls)
    assert len(reused.ready) == 1 and not no_calls.requests
    assert reused.budget["calls"] == 0
    text = _header_footer_text(reused.ready[0].output)
    assert "21 13 16" in text and "DRY-PIPE SPRINKLER SYSTEMS" in text
    assert "21 13 13" not in text and "WET-PIPE SPRINKLER SYSTEMS" not in text
    assert second.read_bytes() == next_before and reused.review.skipped

    # Saved actual recovery reports feed the local evaluator; READY still isn't human acceptance.
    manifest = tmp_path / "real-engine-manifest.json"
    manifest.write_text(json.dumps({"version": 1, "cases": [{
        "id": "warm-new-project", "execution": "scripted", "report": "second-run/recovery.json",
        "expect": {"ready": 1, "calls_max": 0}}]}), encoding="utf-8")
    row = evaluate(manifest)["cases"][0]
    assert row["expectation_status"] == "PASS" and not row["human_accepted"]
    assert row["model_visual_review"] == "not_reviewed" and row["final_assertion_count"] == 0
