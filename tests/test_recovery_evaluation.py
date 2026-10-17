"""Evaluation measurements and scripted end-to-end recovery evidence, never live benchmarks."""
from __future__ import annotations

import copy
import hashlib
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
from spec_surgeon.recovery_evaluation import EvaluationError, evaluate, main, markdown
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


def _paired_manifest(tmp_path, *, scope="recovery", pairs=1, execution="live"):
    entries = []
    for pair in range(pairs):
        for variant in ("baseline", "current"):
            report = _report(cost=2 if variant == "baseline" else 1)
            report["recovery_id"] = f"{pair}-{variant}"
            report["request"] = "Use the architect's approved styles."
            report["key"].update(request=report["request"], recovery_templates=["/not-opened/template.docx"],
                                recovery_inputs=[], goal={"ops": [{"op": "map_styles",
                                "params": {"template": "/not-opened/template.docx"}}]})
            report["input_pins"] = {"/not-opened/source.docx": "a" * 64,
                                    "/not-opened/template.docx": "b" * 64}
            name = f"{pair}-{variant}.json"
            path = tmp_path / name
            path.write_text(json.dumps(report), encoding="utf-8")
            entries.append({"id": f"{pair}-{variant}", "pair_id": f"batch-{pair}",
                            "variant": variant, "cohort": "holdout-cold", "execution": execution,
                            "revision": ("1" if variant == "baseline" else "2") * 40,
                            "report": name, "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                            "human_interventions": 2 if variant == "baseline" else 1,
                            "human_review": _accepted(), **({"workflow": {
                                "elapsed_seconds": 120 if variant == "baseline" else 90,
                                "human_interventions": 3 if variant == "baseline" else 1,
                                "additional_model_cost_usd": 4}} if scope == "workflow" else {})})
    manifest = tmp_path / "paired.json"
    manifest.write_text(json.dumps({"version": 2, "measurement_scope": scope, "cases": entries}), encoding="utf-8")
    return manifest


def _change_case(manifest, index, change):
    data = json.loads(manifest.read_text())
    change(data["cases"][index])
    manifest.write_text(json.dumps(data), encoding="utf-8")


def _change_report(manifest, index, change, *, repin=True):
    data = json.loads(manifest.read_text())
    path = manifest.parent / data["cases"][index]["report"]
    report = json.loads(path.read_text())
    change(report)
    path.write_text(json.dumps(report), encoding="utf-8")
    if repin:
        _change_case(manifest, index, lambda c: c.update(report_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))


def test_paired_workflow_reports_include_failed_batches_and_pre_recovery_spending(tmp_path):
    manifest = _paired_manifest(tmp_path, scope="workflow", pairs=2)
    _change_report(manifest, 0, lambda r: r["files"][0].update(status="UNRESOLVED"))
    _change_case(manifest, 0, lambda c: c["human_review"].update(request_satisfied=False))
    result = evaluate(manifest)
    group = result["comparisons"][0]
    assert group["baseline"]["human_accepted_batches"] == 1
    assert group["current"]["human_accepted_batches"] == 2
    assert group["baseline"]["estimated_cost_usd"] == 12
    assert group["baseline"]["cost_per_human_accepted_batch_usd"] == 12
    assert group["current"]["cost_per_human_accepted_batch_usd"] == 5
    assert group["current_minus_baseline"]["human_acceptance_rate"] == 0.5
    assert group["current_minus_baseline"]["elapsed_seconds"] == -60
    assert group["current_minus_baseline"]["human_interventions"] == -4
    assert result["cases"][0]["seconds"] == 2.5  # Recovery time remains separately observable.
    assert result["pairs"][0]["human_accepted_delta"] == 1
    assert "Measurement scope: **workflow**" in markdown(result)
    assert "live_estimated_cost_usd" not in result["summary"]  # Never pool the two variants' recovery costs.
    assert "groups" not in result


def test_partial_measurements_never_make_missing_review_time_interventions_or_cost_zero(tmp_path):
    manifest = _paired_manifest(tmp_path, scope="workflow")
    _change_case(manifest, 1, lambda c: c.update(workflow={"elapsed_seconds": None,
                        "human_interventions": None, "additional_model_cost_usd": None}, human_review={}))
    result = evaluate(manifest)
    group = result["comparisons"][0]
    for field in ("human_acceptance_rate", "elapsed_seconds", "human_interventions", "estimated_cost_usd"):
        assert group["current"][field] is None
        assert group["current_minus_baseline"][field] is None
    assert group["current"]["human_review_coverage"] == 0
    assert result["pairs"][0]["human_accepted_delta"] is None
    assert result["pairs"][0]["estimated_cost_usd_delta"] is None


@pytest.mark.parametrize("reviewer", [None, "", " \t\n"])
@pytest.mark.parametrize("satisfied", [True, False])
def test_paired_review_without_a_named_reviewer_never_counts_as_complete(tmp_path, reviewer, satisfied):
    manifest = _paired_manifest(tmp_path)

    def change(case):
        case["human_review"]["request_satisfied"] = satisfied
        if reviewer is None:
            case["human_review"].pop("reviewer")
        else:
            case["human_review"]["reviewer"] = reviewer

    _change_case(manifest, 1, change)
    result = evaluate(manifest)
    row = result["cases"][1]
    assert row["human_review"]["request_satisfied"] is satisfied  # Preserve the supplied judgment.
    assert row["human_review_complete"] is False and row["human_accepted"] is False
    current = result["comparisons"][0]["current"]
    assert current["human_reviewed_batches"] == current["human_accepted_batches"] == 0
    assert current["human_review_coverage"] == 0
    assert current["human_acceptance_rate"] is None
    assert current["cost_per_human_accepted_batch_usd"] is None
    assert current["estimated_cost_usd"] == 1  # Unreviewed work still costs money.
    assert result["pairs"][0]["human_accepted_delta"] is None
    assert result["comparisons"][0]["current_minus_baseline"]["human_acceptance_rate"] is None


def test_named_reviewer_can_complete_a_negative_review(tmp_path):
    manifest = _paired_manifest(tmp_path)
    _change_case(manifest, 1, lambda c: c["human_review"].update(request_satisfied=False))
    result = evaluate(manifest)
    assert result["cases"][1]["human_review_complete"] is True
    assert result["cases"][1]["human_accepted"] is False
    current = result["comparisons"][0]["current"]
    assert current["human_reviewed_batches"] == 1 and current["human_acceptance_rate"] == 0
    assert result["pairs"][0]["human_accepted_delta"] == -1


def test_scripted_and_unknown_pairs_never_enter_live_economics(tmp_path):
    manifest = _paired_manifest(tmp_path, pairs=3)
    for index in (2, 3):
        _change_case(manifest, index, lambda c: c.update(execution="scripted"))
    for index in (4, 5):
        _change_case(manifest, index, lambda c: c.update(execution="unknown"))
    result = evaluate(manifest)
    groups = {g["execution"]: g for g in result["comparisons"]}
    assert len(groups) == 3
    assert groups["live"]["baseline"]["estimated_cost_usd"] == 2
    for execution in ("scripted", "unknown"):
        assert groups[execution]["baseline"]["estimated_cost_usd"] is None
        assert groups[execution]["current_minus_baseline"]["estimated_cost_usd"] is None


def test_pair_identity_follows_bytes_across_relocated_paths_and_discloses_condition_changes(tmp_path):
    manifest = _paired_manifest(tmp_path)
    def relocate(report):
        report["files"][0]["source"] = "/new/source.docx"
        report["input_pins"] = {"/new/source.docx": "a" * 64, "/new/template.docx": "b" * 64}
        report["key"]["goal"]["ops"][0]["params"]["template"] = "/new/template.docx"
        report["key"]["recovery_templates"] = ["/new/template.docx"]
        report["renderer"] = "word"
    _change_report(manifest, 1, relocate)
    result = evaluate(manifest)
    assert result["pairs"][0]["changed_conditions"] == ["renderer"]
    assert result["cases"][0]["identity_sha256"] == result["cases"][1]["identity_sha256"]


@pytest.mark.parametrize("literal_location", ["approved_words", "replacement"])
def test_input_path_relocation_never_rewrites_approved_literal_words(tmp_path, literal_location):
    manifest = _paired_manifest(tmp_path)
    def change(report, path):
        report["files"][0]["source"] = path
        report["input_pins"] = {path: "a" * 64, "/not-opened/template.docx": "b" * 64}
        if literal_location == "approved_words":
            report["key"]["approved_words"] = [path]
        else:
            report["key"]["goal"] = {"ops": [{"op": "replace_text", "params": {"replace": path}}]}
    _change_report(manifest, 0, lambda r: change(r, "/not-opened/source.docx"))
    _change_report(manifest, 1, lambda r: change(r, "/new/source.docx"))
    with pytest.raises(EvaluationError, match="identity"):
        evaluate(manifest)


def test_comparison_discloses_prior_resource_policies_and_actual_serving_models(tmp_path):
    manifest = _paired_manifest(tmp_path)
    _change_report(manifest, 0, lambda r: r.update(resource_history=[{
        "calls": 2, "estimated_cost_usd": 3, "model": "earlier-model", "effort": "low", "max_calls": 4,
        "usage": {"cost_usd": 3, "served_models": ["fallback-model"]}}]))
    result = evaluate(manifest)
    changed = result["pairs"][0]["changed_conditions"]
    assert "resource_policies" in changed and "served_models" in changed and "resource_segment_count" in changed
    assert result["comparisons"][0]["baseline"]["estimated_cost_usd"] == 5
    policies = result["cases"][0]["conditions"]["resource_policies"]
    assert any(p["model"] == "earlier-model" and p["effort"] == "low" and p["max_calls"] == 4 for p in policies)


def test_same_independent_outcome_allows_different_execution_recipes(tmp_path):
    manifest = _paired_manifest(tmp_path)
    for index in (0, 1):
        _change_report(manifest, index, lambda r: r["key"].update(adaptive_contract={
            "kind": "document_effects", "effects": [{"type": "format", "font": "Arial"}]}))
    _change_report(manifest, 1, lambda r: r["key"].update(goal={"ops": [{"op": "different_method"}]}))
    result = evaluate(manifest)
    assert result["pairs"][0]["changed_implementation"]
    assert result["pairs"][0]["outcome_basis"] == "independent_contract"


def test_different_legacy_recipes_need_explicit_reviewed_common_outcome(tmp_path):
    manifest = _paired_manifest(tmp_path)
    _change_report(manifest, 1, lambda r: r["key"].update(goal={"ops": [{"op": "different_method"}]}))
    with pytest.raises(EvaluationError, match="identity"):
        evaluate(manifest)
    declaration = {"statement": "Same reviewed style mappings and all original wording retained.", "reviewer": "Test reviewer"}
    for index in (0, 1):
        _change_case(manifest, index, lambda c: c.update(reviewed_outcome=declaration))
    result = evaluate(manifest)
    assert result["pairs"][0]["changed_implementation"]
    assert result["pairs"][0]["outcome_basis"] == "reviewed_declaration"
    _change_case(manifest, 1, lambda c: c["reviewed_outcome"].update(statement="Different outcome"))
    with pytest.raises(EvaluationError, match="identity"):
        evaluate(manifest)


@pytest.mark.parametrize("change", [
    lambda r: r.update(request="Use a different request."),
    lambda r: r["input_pins"].update({"/not-opened/source.docx": "c" * 64}),
    lambda r: r["input_pins"].update({"/not-opened/template.docx": "c" * 64}),
    lambda r: r["key"].update(approved_words=["Different literal wording"]),
    lambda r: r["key"]["goal"]["ops"][0]["params"].update(mapping={"A": "B"}),
    lambda r: r["input_pins"].pop("/not-opened/source.docx"),
    lambda r: r["input_pins"].update({"/not-opened/source.docx": "not-a-sha256"}),
    lambda r: r["files"].append(dict(r["files"][0])),
])
def test_pair_label_never_overrides_mismatched_or_incomplete_report_identity(tmp_path, change):
    manifest = _paired_manifest(tmp_path)
    _change_report(manifest, 1, change)
    with pytest.raises(EvaluationError):
        evaluate(manifest)


@pytest.mark.parametrize("change", [
    lambda c: c.update(variant="baseline"),
    lambda c: c.update(pair_id="unmatched-batch"),
    lambda c: c.update(execution="scripted"),
    lambda c: c.update(cohort="warm"),
    lambda c: c.update(revision="1" * 40),
    lambda c: c.update(revision="main"),
    lambda c: c.update(report_sha256="b" * 64),
])
def test_invalid_comparisons_are_refused_instead_of_silently_omitting_runs(tmp_path, change):
    manifest = _paired_manifest(tmp_path)
    _change_case(manifest, 1, change)
    with pytest.raises(EvaluationError):
        evaluate(manifest)


def test_reusing_same_report_cannot_count_as_an_independent_second_run(tmp_path):
    manifest = _paired_manifest(tmp_path)
    first = json.loads(manifest.read_text())["cases"][0]
    _change_case(manifest, 1, lambda c: c.update(report=first["report"], report_sha256=first["report_sha256"]))
    with pytest.raises(EvaluationError, match="reused"):
        evaluate(manifest)


def test_modified_report_requires_explicit_repinning(tmp_path):
    manifest = _paired_manifest(tmp_path)
    _change_report(manifest, 1, lambda r: r.update(seconds=0), repin=False)
    with pytest.raises(EvaluationError, match="changed since"):
        evaluate(manifest)


def test_different_snapshots_of_same_recovery_session_cannot_double_count_costs(tmp_path):
    manifest = _paired_manifest(tmp_path, pairs=2)
    _change_report(manifest, 2, lambda r: r.update(recovery_id="0-baseline", seconds=100))
    with pytest.raises(EvaluationError, match="snapshots"):
        evaluate(manifest)


def test_user_selected_targets_cannot_silently_change_between_paired_runs(tmp_path):
    manifest = _paired_manifest(tmp_path)
    for index, paragraph in ((0, 2), (1, 9)):
        _change_report(manifest, index, lambda r, p=paragraph: r["key"].update(adaptive_bindings={
            "/not-opened/source.docx": {"source_sha256": "a" * 64, "effects": {"heading": {
                "targets": [{"part": "word/document.xml", "paragraph_index": p}], "choice": "user"}}}}))
    with pytest.raises(EvaluationError, match="target selections"):
        evaluate(manifest)
    # An unresolved baseline is a legitimate failure, not a conflicting human selection.
    _change_report(manifest, 0, lambda r: r["key"].update(adaptive_bindings={}))
    result = evaluate(manifest)
    assert result["pairs"][0]["changed_target_bindings"]


def test_declared_template_without_its_pin_is_not_comparison_evidence(tmp_path):
    manifest = _paired_manifest(tmp_path)
    for index in (0, 1):
        _change_report(manifest, index, lambda r: r["input_pins"].pop("/not-opened/template.docx"))
    with pytest.raises(EvaluationError, match="auxiliary inputs"):
        evaluate(manifest)


@pytest.mark.parametrize("measurement", [
    {"elapsed_seconds": 1, "human_interventions": 0},
    {"elapsed_seconds": True, "human_interventions": 0, "additional_model_cost_usd": 0},
    {"elapsed_seconds": 1, "human_interventions": -1, "additional_model_cost_usd": 0},
    {"elapsed_seconds": 1, "human_interventions": 0, "additional_model_cost_usd": -1},
])
def test_workflow_measurements_require_explicit_unknowns_and_valid_numbers(tmp_path, measurement):
    manifest = _paired_manifest(tmp_path, scope="workflow")
    _change_case(manifest, 1, lambda c: c.update(workflow=measurement))
    with pytest.raises(EvaluationError):
        evaluate(manifest)


def test_paired_cli_is_read_only_offline_and_writes_comparison_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr("spec_surgeon.llm.client", lambda: pytest.fail("evaluation must never access a model"))
    manifest = _paired_manifest(tmp_path, scope="workflow", execution="scripted")
    before = {p: p.read_bytes() for p in tmp_path.glob("*.json")}
    output = tmp_path / "comparison"
    assert main([str(manifest), "--out", str(output)]) == 0
    result = json.loads((output / "evaluation.json").read_text())
    assert result["version"] == 2 and len(result["pairs"]) == 1
    assert result["comparisons"][0]["current"]["estimated_cost_usd"] is None
    assert "current_minus_baseline" in (output / "EVALUATION.md").read_text()
    assert all(path.read_bytes() == content for path, content in before.items())


def test_shipped_example_uses_real_input_hashes_without_claiming_measured_improvement():
    root = Path(__file__).resolve().parent.parent
    manifest = root / "examples/evaluation/paired-scripted.json"
    result = evaluate(manifest)
    assert all(row["execution"] == "scripted" and not row["human_accepted"] for row in result["cases"])
    assert result["comparisons"][0]["current"]["elapsed_seconds"] is None
    assert result["comparisons"][0]["current"]["estimated_cost_usd"] is None
    for case in json.loads(manifest.read_text())["cases"]:
        report = json.loads((manifest.parent / case["report"]).read_text())
        for path, digest in report["input_pins"].items():
            assert hashlib.sha256((root / path).read_bytes()).hexdigest() == digest


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
