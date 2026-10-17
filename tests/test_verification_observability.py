"""The verifier leaves durable check-level evidence without altering its decisions."""
from __future__ import annotations

import json
import zipfile

import pytest

from make_fixtures import build
from spec_surgeon import telemetry as diag
from spec_surgeon.verify import suite


CHECKS = {"V1", "V2", "V3", "V4", "V5a", "V5b", "V5c", "V8", "V10"}


def records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def plan(**fields):
    return {"job": "test", "mode": "formatting", "intended_parts": [], "diffs": [],
            "rev_delta": {"ins": 0, "del": 0}, **fields}


@pytest.mark.parametrize("mode", ["formatting", "content"])
def test_each_check_records_started_duration_outcome_and_correlation(tmp_path, mode):
    source = build("21 05 00", "A")
    path = tmp_path / "verification.jsonl"
    expected = suite.run_suite(source, source, plan(mode=mode), "source.docx", "output.docx")
    with diag.scope(path, run_id="test-run", file_id="test-file"):
        actual = suite.run_suite(source, source, plan(mode=mode), "source.docx", "output.docx")

    assert actual.to_dict() == expected.to_dict()
    assert actual.passed
    rows = records(path)
    started = {r["check_id"]: r for r in rows if r["event"] == "verification.check.started"}
    completed = {r["check_id"]: r for r in rows if r["event"] == "verification.check.succeeded"}
    assert set(started) == set(completed) == CHECKS
    for check_id in CHECKS:
        start, finish = started[check_id], completed[check_id]
        assert start["span_id"] == finish["span_id"]
        assert start["monotonic_ns"] <= finish["monotonic_ns"]
        assert finish["duration_ms"] >= 0
        assert finish["outcome"] == actual.get(check_id).status
    assert completed["V2"]["blocks_compared"] > 0
    assert completed["V2"]["shared_computation"] == completed["V3"]["shared_computation"] == "V2/V3"
    assert completed["V8"]["outcome"] == ("PASS" if mode == "content" else "NA")
    assert {r["check_id"] for r in rows if r["event"] == "verification.check.deferred"} == {"V6", "V7", "V9"}
    assert all(r["run_id"] == "test-run" and r["file_id"] == "test-file" for r in rows)
    assert rows[-1]["event"] == "verification.suite.succeeded" and rows[-1]["outcome"] == "PASS"
    assert "check_id" not in rows[-1]


def test_validation_failures_are_reported_without_copying_document_text(tmp_path):
    secret_before, secret_after = "PRIVATE SOURCE WORDS", "PRIVATE OUTPUT WORDS"
    source = build("21 05 00", secret_before)
    output = build("21 05 00", secret_after)
    path = tmp_path / "verification.jsonl"
    with diag.scope(path):
        rec = suite.run_suite(source, output, plan(), "source.docx", "output.docx")

    rows = records(path)
    checked = {r["check_id"]: r for r in rows if r["event"] == "verification.check.succeeded"}
    assert rec.get("V2").status == checked["V2"]["outcome"] == "FAIL"
    assert checked["V2"]["unpredicted_differences"] > 0
    assert rows[-1]["outcome"] == "FAIL" and "V2" in rows[-1]["failed_checks"]
    assert secret_before not in path.read_text() and secret_after not in path.read_text()
    assert not any("summary" in r or "data" in r for r in rows)


@pytest.mark.parametrize("target,check_ids", [("hashes", {"V1"}), ("blocks", {"V2", "V3"}),
                                              ("compare", {"V5a"})])
def test_started_check_is_durable_before_work_and_crash_keeps_original_exception(tmp_path, monkeypatch, target, check_ids):
    source = build("21 05 00", "A")
    path = tmp_path / "verification.jsonl"
    error = RuntimeError("planted verifier failure")

    def crash(*args, **kwargs):
        rows = records(path)
        starts = {r["check_id"] for r in rows if r["event"] == "verification.check.started"}
        finishes = {r["check_id"] for r in rows if r["event"] == "verification.check.succeeded"}
        assert check_ids <= starts and not check_ids & finishes
        raise error

    owner = suite.VPackage if target == "hashes" else suite._xsd if target == "compare" else suite
    monkeypatch.setattr(owner, target, crash)
    with pytest.raises(RuntimeError) as caught, diag.scope(path):
        suite.run_suite(source, source, plan(), "source.docx", "output.docx")

    assert caught.value is error
    failures = [r for r in records(path) if r["event"] == "verification.check.failed"]
    assert {r["check_id"] for r in failures} == check_ids
    assert all(r["outcome"] == "ERROR" and r["duration_ms"] >= 0 and "crash" in r["traceback"] for r in failures)
    assert records(path)[-1]["event"] == "verification.suite.failed"


def test_property_exception_remains_a_failed_check_and_retains_traceback(tmp_path, monkeypatch):
    source = build("21 05 00", "A")
    path = tmp_path / "verification.jsonl"

    def cannot_evaluate(*args, **kwargs):
        try:
            raise KeyError("missing-property")
        except KeyError as cause:
            raise RuntimeError("property resolution failed") from cause

    monkeypatch.setattr(suite, "check_properties", cannot_evaluate)
    with diag.scope(path):
        rec = suite.run_suite(source, source, plan(), "source.docx", "output.docx")

    assert rec.get("V10").status == "FAIL"
    assert rec.get("V10").summary == "could not evaluate: RuntimeError: property resolution failed"
    rows = records(path)
    error = next(r for r in rows if r["event"] == "verification.properties.failed")
    assert error["check_id"] == "V10"
    assert [item["type"] for item in error["exception_chain"]] == ["RuntimeError", "KeyError"]
    assert "cannot_evaluate" in error["traceback"]
    assert rows[-1]["failed_checks"] == ["V10"]


def test_unreadable_package_records_failure_before_any_check(tmp_path):
    path = tmp_path / "verification.jsonl"
    with pytest.raises(zipfile.BadZipFile), diag.scope(path):
        suite.run_suite(b"not a zip", b"not a zip", plan(), "source.docx", "output.docx")
    rows = records(path)
    assert [r["event"] for r in rows] == ["verification.suite.started", "verification.open_packages.started",
                                         "verification.open_packages.failed", "verification.suite.failed"]
    assert rows[-1]["error_type"] == "BadZipFile"


def test_unwritable_diagnostics_cannot_change_verification(tmp_path, capsys):
    source = build("21 05 00", "A")
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("blocked")
    expected = suite.run_suite(source, source, plan(), "source.docx", "output.docx")
    with diag.scope(blocked / "verification.jsonl"):
        actual = suite.run_suite(source, source, plan(), "source.docx", "output.docx")
    assert actual.to_dict() == expected.to_dict()
    assert capsys.readouterr().err.count("Diagnostics could not be written") == 1
