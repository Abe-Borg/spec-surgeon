import copy
import hashlib
import json

import pytest

from spec_surgeon.workflow_evaluation import EvaluationError, adapt_report, evaluate, main
from workflow_corpus import CASES, build_corpus, margin_spec


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2))
    return path


def pin(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def case(path, variant, **extra):
    return {"id": variant, "pair_id": "same-workload", "variant": variant, "cohort": "development-cold",
            "execution": "scripted", "revision": "1" * 40 if variant == "baseline" else "2" * 40,
            "report": str(path), "report_sha256": pin(path),
            "workflow": {"elapsed_seconds": None, "human_interventions": None, "additional_model_cost_usd": None},
            "human_review": {}, **extra}


def human():
    return {"reviewer": "Test reviewer (synthetic judgment)", "request_satisfied": True,
            "content_preserved": True, "visual_checked": True}


@pytest.fixture(scope="module")
def actual_batches(tmp_path_factory):
    from spec_surgeon.batch import run_batch
    root = tmp_path_factory.mktemp("workflow-corpus")
    paths = build_corpus(root / "sources")
    snapshots = []
    for variant in ("baseline", "current"):
        output = root / variant
        batch = run_batch([str(p) for p in paths], None, {}, output, spec=margin_spec(), workers=1,
                          do_render=False, renderer="none", progress=lambda message: None)
        assert batch.counts()["PASS"] == len(CASES)
        snapshots.append(write(root / f"{variant}-record.json", adapt_report(output / "batch.json")))
    return root, paths, snapshots


def test_actual_corpus_has_original_pins_and_explicit_automated_check_coverage(actual_batches):
    _, paths, snapshots = actual_batches
    record = adapt_report(snapshots[0])
    assert record["version"] == 3 and record["kind"] == "batch"
    assert len(record["files"]) == len(CASES) == 12
    assert {r["sha256"] for r in record["source_pins"]} == {pin(p) for p in paths}
    assert all(file["ready"] for file in record["files"])
    assert {c["id"] for c in record["check_coverage"]} >= {"V1", "V2", "V6", "V7", "V10"}
    assert {c["status"] for c in record["check_coverage"] if c["id"] in {"V6", "V7"}} == {"NA"}
    assert record["estimated_model_cost_usd"] == 0


def test_actual_paired_runs_do_not_infer_human_acceptance_or_live_improvement(actual_batches, tmp_path):
    _, _, snapshots = actual_batches
    manifest = write(tmp_path / "manifest.json", {"version": 3,
                     "cases": [case(path, variant) for path, variant in zip(snapshots, ("baseline", "current"))]})
    result = evaluate(manifest)
    assert len(result["pairs"]) == 1
    assert all(row["human_accepted"] is None for row in result["cases"])
    assert all(row["acceptance_rate"] is None and row["live_cost_per_accepted_batch"] is None for row in result["groups"])
    assert result["pairs"][0]["delta"] == {"elapsed_seconds": None, "human_interventions": None,
        "estimated_model_cost_usd": None, "human_accepted": None}


def test_actual_chain_ingests_stage_checks_without_counting_intermediate_sources(actual_batches, tmp_path):
    from spec_surgeon.chain import chain_from_specs, run_chain
    _, paths, _ = actual_batches
    chain = chain_from_specs([("Left margin", margin_spec()), ("Idempotent repeat", margin_spec())])
    run = run_chain([str(paths[0])], chain, tmp_path / "chain", workers=1, renderer="none", do_render=False,
                    progress=lambda message: None)
    assert run.counts()["PASS"] == 1
    record = adapt_report(tmp_path / "chain" / "chain.json")
    assert len(record["files"]) == len(record["source_pins"]) == 1
    assert record["source_pins"][0]["sha256"] == pin(paths[0])
    assert {c["stage"] for c in record["check_coverage"]} >= {1, 2}
    assert len(record["children"]) == 2
    assert record["estimated_model_cost_usd"] == 0


def test_real_failed_file_stays_in_complete_batch_and_cannot_be_human_accepted(actual_batches, tmp_path):
    from spec_surgeon.batch import run_batch
    _, paths, _ = actual_batches
    invalid = tmp_path / "damaged.docx"
    invalid.write_bytes(b"This is deliberately not a Word package.")
    snapshots = []
    for variant in ("baseline", "current"):
        folder = tmp_path / variant
        run = run_batch([str(paths[0]), str(invalid)], None, {}, folder, spec=margin_spec(), workers=1,
                        renderer="none", do_render=False, progress=lambda message: None)
        assert run.counts()["PASS"] == 1 and run.counts()["EXCEPTION"] == 1
        record = adapt_report(folder / "batch.json")
        assert len(record["source_pins"]) == 2
        assert all(row["sha256"] is not None for row in record["source_pins"])
        snapshots.append(write(tmp_path / f"{variant}.json", record))
    cases = [case(p, v, human_review=human()) for p, v in zip(snapshots, ("baseline", "current"))]
    result = evaluate(write(tmp_path / "manifest.json", {"version": 3, "cases": cases}))
    assert all(row["files"] == 2 and row["ready"] == 1 and row["human_accepted"] is False for row in result["cases"])


def test_in_place_report_pins_original_bytes_not_the_delivered_document(actual_batches, tmp_path):
    from spec_surgeon.batch import run_batch
    source = tmp_path / "original.docx"
    source.write_bytes(actual_batches[1][0].read_bytes())
    before = pin(source)
    output = tmp_path / "run"
    run = run_batch([str(source)], None, {}, output, spec=margin_spec(), workers=1,
                    renderer="none", do_render=False, in_place=True, progress=lambda message: None)
    assert run.counts()["PASS"] == 1
    assert pin(source) != before
    assert adapt_report(output / "batch.json")["source_pins"][0]["sha256"] == before


def recovery(path, *, session="one", cost=3, source="source.docx", sha="a" * 64):
    return write(path, {"recovery_id": session, "request": "Set margin", "input_pins": {source: sha},
        "key": {"goal": margin_spec(), "recovery_templates": [], "recovery_inputs": []},
        "files": [{"source": source, "status": "READY", "outcomes": [{"kind": "page_setup", "status": "PASS"}]}],
        "resource_history": [{"estimated_cost_usd": 2, "calls": 1}],
        "budget": {"estimated_cost_usd": cost, "calls": 2}, "attempts": [], "seconds": 4})


def test_recovery_and_campaign_count_parent_or_child_cost_once_and_include_failed_work(tmp_path):
    child = recovery(tmp_path / "child" / "recovery.json")
    assert adapt_report(child)["estimated_model_cost_usd"] == 5
    campaign = {"id": "campaign-one", "request": "Set margin", "documents": {
        "doc": {"source": "source.docx", "source_sha256": "a" * 64, "status": "READY"}},
        "projects": {"north": {"goal": {"spec": margin_spec()}, "runs": [{"path": str(child.parent)}],
            "resource_history": [{"estimated_cost_usd": 10}],
            "planning_resource_history": [{"estimated_cost_usd": 4}]}}}
    record = adapt_report(write(tmp_path / "campaign.json", campaign))
    assert record["estimated_model_cost_usd"] == 14  # The child's $5 is already within the parent's $10.
    assert len(record["children"]) == 1
    del campaign["projects"]["north"]["resource_history"]
    assert adapt_report(write(tmp_path / "campaign.json", campaign))["estimated_model_cost_usd"] == 9
    campaign["projects"]["north"]["planning_resource_history"][0]["estimated_cost_usd"] = None
    assert adapt_report(write(tmp_path / "campaign.json", campaign))["estimated_model_cost_usd"] is None


def test_archived_failed_runs_retain_cost_and_do_not_poison_current_checks(tmp_path):
    previous = recovery(tmp_path / "previous" / "recovery.json", session="previous", cost=6)
    failed = json.loads(previous.read_text())
    failed["files"][0].update(status="UNRESOLVED", outcomes=[{"kind": "page_setup", "status": "FAIL"}])
    write(previous, failed)
    current = recovery(tmp_path / "current" / "recovery.json", session="current", cost=3)
    campaign = {"id": "campaign-archive", "request": "Set margin", "documents": {
        "doc": {"source": "source.docx", "source_sha256": "a" * 64, "status": "READY"}},
        "projects": {"north": {"goal": {"spec": margin_spec()},
            "archived_runs": [{"path": str(previous.parent)}], "runs": [{"path": str(current.parent)}]}}}
    record = adapt_report(write(tmp_path / "campaign.json", campaign))
    assert record["estimated_model_cost_usd"] == 13  # Both complete recoveries, each including its $2 continuation.
    assert [(c["status"], c["lineage_role"]) for c in record["check_coverage"]] == [("FAIL", "historical"), ("PASS", "current")]


def test_missing_campaign_child_keeps_cost_unknown_and_final_evidence_incomplete(tmp_path):
    from spec_surgeon.workflow_evaluation import _supported_final_outcomes
    child = recovery(tmp_path / "available" / "recovery.json")
    campaign = {"id": "campaign-incomplete", "request": "Set margin", "documents": {
        "one": {"source": "source.docx", "source_sha256": "a" * 64, "status": "READY"},
        "two": {"source": "missing.docx", "source_sha256": "b" * 64, "status": "READY"}},
        "projects": {"north": {"goal": {"spec": margin_spec()}, "runs": [
            {"path": str(child.parent), "document_id": "one"},
            {"path": str(tmp_path / "unavailable"), "document_id": "two"}]}}}
    record = adapt_report(write(tmp_path / "campaign.json", campaign))
    assert record["estimated_model_cost_usd"] is None
    assert not _supported_final_outcomes(record)
    # A complete parent execution ledger still establishes recorded cost even
    # when a child's detailed outcome evidence is unavailable.
    campaign["projects"]["north"]["resource_history"] = [{"estimated_cost_usd": 9}]
    record = adapt_report(write(tmp_path / "campaign.json", campaign))
    assert record["estimated_model_cost_usd"] == 9
    assert not _supported_final_outcomes(record)


def test_actual_campaign_and_recovery_can_be_snapshotted_without_a_model_call(actual_batches, tmp_path):
    from spec_surgeon.campaign import create_campaign
    from spec_surgeon.recovery import RecoveryOptions
    _, paths, _ = actual_batches
    campaign = create_campaign([str(paths[0])], tmp_path / "workspace")
    project_id = next(iter(campaign.projects))
    campaign.set_goal(project_id, margin_spec()["request"], spec=margin_spec())
    campaign.run(options=RecoveryOptions(render=False, use_memory=False), log=lambda message: None)
    record = adapt_report(campaign.out_dir / "campaign.json")
    assert record["kind"] == "campaign" and record["files"][0]["ready"]
    assert record["source_pins"][0]["sha256"] == pin(paths[0])
    assert record["children"][0]["kind"] == "recovery"


def test_pairing_refuses_changed_source_and_repeated_lineage(actual_batches, tmp_path):
    _, _, snapshots = actual_batches
    records = [adapt_report(p) for p in snapshots]
    records[1]["source_pins"][0]["sha256"] = "f" * 64
    second = write(tmp_path / "changed.json", records[1])
    cases = [case(snapshots[0], "baseline"), case(second, "current")]
    with pytest.raises(EvaluationError, match="identity differs"):
        evaluate(write(tmp_path / "manifest.json", {"version": 3, "cases": cases}))
    cases[1] = case(snapshots[0], "current")
    with pytest.raises(EvaluationError, match="Overlapping"):
        evaluate(write(tmp_path / "manifest.json", {"version": 3, "cases": cases}))


def test_report_tampering_and_unpinned_sidecars_are_rejected(actual_batches, tmp_path):
    root, _, snapshots = actual_batches
    cases = [case(p, v) for p, v in zip(snapshots, ("baseline", "current"))]
    cases[0]["report_sha256"] = "0" * 64
    with pytest.raises(EvaluationError, match="Report changed"):
        evaluate(write(tmp_path / "manifest.json", {"version": 3, "cases": cases}))
    cases[0] = case(root / "baseline" / "batch.json", "baseline")
    with pytest.raises(EvaluationError, match="sidecar"):
        evaluate(write(tmp_path / "manifest.json", {"version": 3, "cases": cases}))


def test_unnamed_human_judgment_remains_unreviewed_and_unknown_cost_stays_unknown(actual_batches, tmp_path):
    _, _, snapshots = actual_batches
    cases = [case(p, v, human_review={**human(), "reviewer": " "}) for p, v in zip(snapshots, ("baseline", "current"))]
    result = evaluate(write(tmp_path / "manifest.json", {"version": 3, "cases": cases}))
    assert all(r["human_accepted"] is None and r["estimated_model_cost_usd"] is None for r in result["cases"])


def test_partial_review_failed_work_and_unknown_measurements_remain_in_denominator(actual_batches, tmp_path):
    _, _, snapshots = actual_batches
    cases = [case(p, v, human_review=human(), workflow={"elapsed_seconds": 10, "human_interventions": 0,
                  "additional_model_cost_usd": 0}) for p, v in zip(snapshots, ("baseline", "current"))]
    result = evaluate(write(tmp_path / "manifest.json", {"version": 3, "cases": cases}))
    assert all(r["human_accepted"] is True for r in result["cases"])
    assert all(g["acceptance_rate"] == 1 and g["live_cost_per_accepted_batch"] is None for g in result["groups"])
    # Supplied fixture judgments above validate aggregation only; the corpus has
    # never been visually reviewed in Word or promoted to live evidence.


def test_cli_adapt_never_overwrites_existing_results(actual_batches, tmp_path):
    root, _, _ = actual_batches
    path = tmp_path / "record.json"
    assert main(["adapt", str(root / "baseline" / "batch.json"), "--out", str(path)]) == 0
    with pytest.raises(SystemExit) as exc:
        main(["adapt", str(root / "baseline" / "batch.json"), "--out", str(path)])
    assert exc.value.code == 2


def test_supplied_pins_cannot_rewrite_history(tmp_path):
    path = recovery(tmp_path / "recovery.json")
    with pytest.raises(EvaluationError, match="contradicts"):
        adapt_report(path, evidence={"source_pins": {"source.docx": "b" * 64}})
    with pytest.raises(EvaluationError, match="population"):
        adapt_report(path, evidence={"source_pins": {"missing.docx": "b" * 64}})


@pytest.mark.parametrize("mutation", [
    lambda r: r["files"][0].update(ready="false"),
    lambda r: r["files"][0].update(status="READY", ready=False),
    lambda r: r["check_coverage"][0].update(status="SUCCESS"),
    lambda r: r["auxiliary_pins"].append(dict(r["source_pins"][0])),
    lambda r: r.update(estimated_model_cost_usd=True),
])
def test_adapted_snapshots_are_strictly_validated(actual_batches, tmp_path, mutation):
    record = adapt_report(actual_batches[2][0])
    mutation(record)
    with pytest.raises(EvaluationError):
        adapt_report(write(tmp_path / "invalid.json", record))


def test_different_user_approved_targets_require_a_reviewed_common_outcome(actual_batches, tmp_path):
    snapshots = []
    for index, original in enumerate(actual_batches[2]):
        record = adapt_report(original)
        record["approved_outcome"] = {"kind": "document_effects", "effects": [{"id": "heading", "type": "format"}]}
        record["target_bindings"] = {record["files"][0]["source"]: {"effects": {"heading": {"targets": [index]}}}}
        snapshots.append(write(tmp_path / f"targets-{index}.json", record))
    cases = [case(p, v) for p, v in zip(snapshots, ("baseline", "current"))]
    with pytest.raises(EvaluationError, match="identity differs"):
        evaluate(write(tmp_path / "manifest.json", {"version": 3, "cases": cases}))


def test_equivalent_template_relocation_does_not_change_approved_target_identity(actual_batches, tmp_path):
    snapshots = []
    for index, original in enumerate(actual_batches[2]):
        record = adapt_report(original)
        template = f"/checkout-{index}/office.docx"
        record["approved_outcome"] = {"kind": "document_effects", "effects": [
            {"id": "heading", "type": "apply_style", "template": template}]}
        record["auxiliary_pins"] = [{"path": template, "sha256": "c" * 64, "provenance": "report"}]
        record["target_bindings"] = {record["files"][0]["source"]: {
            "source_sha256": record["source_pins"][0]["sha256"], "contract_sha256": str(index + 1) * 64,
            "effects": {"heading": {"targets": [{"paragraph_index": 1}]}}}}
        snapshots.append(write(tmp_path / f"relocated-{index}.json", record))
    cases = [case(p, v) for p, v in zip(snapshots, ("baseline", "current"))]
    result = evaluate(write(tmp_path / "manifest.json", {"version": 3, "cases": cases}))
    assert len(result["pairs"]) == 1
    assert "target_bindings" in result["pairs"][0]["condition_changes"]
