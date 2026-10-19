"""Human evidence stays bounded without weakening machine verification or retry inputs."""
from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

from spec_surgeon import diagnostics
from spec_surgeon.batch import BatchResult, FileResult
from spec_surgeon.readable import RECORD_BYTES, REPORT_BYTES, bounded, grouped_findings
from spec_surgeon.report import write_batch_report, write_diagnostics
from spec_surgeon.verify.suite import CheckResult, VerificationRecord
from spec_surgeon.verify.explain import failure_details


def record() -> VerificationRecord:
    rec = VerificationRecord("source.docx", "rejected.docx", "edit-spec", "format", [], [], {})
    errors = [f"word/document.xml paragraph {i} run 0: eastAsia slot is Calibri" for i in range(1506)]
    rec.set(CheckResult("V10", "Property assertions", "FAIL", "1,506 font slot mismatches", {"errors": errors}))
    return rec


def test_repeated_failures_are_counted_with_three_locations_and_keep_full_evidence():
    rec = record()
    before = rec.to_dict()
    text = rec.to_markdown()
    assert "1,506 occurrences" in text
    assert "paragraph 0 run 0" in text and "paragraph 2 run 0" in text
    assert "paragraph 3 run 0" not in text
    assert "eastAsia slot is Calibri" in text and "RESULT: FAIL" in text
    assert len(text.encode("utf-8")) < RECORD_BYTES
    assert rec.to_dict() == before and not rec.passed
    assert len(rec.to_dict()["checks"][0]["data"]["errors"]) == 1506


def test_grouping_preserves_distinct_font_slots_and_values():
    lines = ["paragraph 0 run 0: eastAsia slot is Calibri", "paragraph 1 run 0: eastAsia slot is Calibri",
             "paragraph 1 run 0: ascii slot is Calibri", "paragraph 1 run 0: eastAsia slot is Arial"]
    grouped = grouped_findings(lines)
    assert len(grouped) == 3 and grouped[0].startswith("2 occurrences")
    assert "ascii slot is Calibri" in grouped[1] and "eastAsia slot is Arial" in grouped[2]


def test_utf8_cap_has_a_notice_and_reference():
    text = bounded("漢" * 200_000, REPORT_BYTES, "batch.json")
    assert len(text.encode("utf-8")) < REPORT_BYTES
    assert "Truncated" in text and "batch.json" in text


def test_sampled_verifier_details_do_not_misstate_total_occurrences():
    data = {"errors": [f"paragraph {i} run 0: wrong font" for i in range(50)], "error_count": 1506}
    lines = failure_details("V10", data)
    assert lines[0].startswith("1,506 problems in total") and "sample" in lines[0]
    assert lines[1].startswith("50 occurrences")


def test_record_predictions_are_patterns_and_samples():
    rec = record()
    rec.predicted_diffs = [{"part": "word/document.xml", "block_index": i,
                            "before": "old " * 1000, "after": "new " * 1000} for i in range(2000)]
    text = rec.to_markdown()
    assert "2,000 block(s) changed" in text and "block 2:" in text and "block 3:" not in text
    assert len(text.encode("utf-8")) < RECORD_BYTES
    assert len(rec.to_dict()["predicted_diffs"]) == 2000


def test_failed_report_opens_with_failed_check_and_copy_usability(tmp_path):
    out = tmp_path / "results"
    out.mkdir()
    fr = FileResult(Path("source.docx"), fingerprint={"pattern_id": "body"}, record=record(),
                    output=out / "failed" / "rejected.docx")
    batch = BatchResult(out_dir=out, job="edit-spec", params={}, files=[fr])
    write_batch_report(batch)
    write_diagnostics(batch)
    first = (out / "SET_ASIDE_REPORT.md").read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("V10 failed") and "must not be used" in first
    for name in ("SET_ASIDE_REPORT.md", "BATCH_REPORT.md", "DIAGNOSTICS.md"):
        assert (out / name).stat().st_size < REPORT_BYTES
    saved = json.loads((out / "set_aside.json").read_text())
    assert saved["files"][0]["details"]["failed_checks"] == ["V10"]


def test_oversized_report_keeps_machine_evidence_and_names_its_reference(tmp_path, monkeypatch):
    # Exercise the Windows conversion path on every platform. Byte-bounded human
    # output must not grow when a text stream would turn LF into CRLF.
    original_open = Path.open
    def windows_text(path, mode="r", *args, **kwargs):
        if mode == "w" and kwargs.get("newline") is None:
            kwargs["newline"] = "\r\n"
        return original_open(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "open", windows_text)
    batch = BatchResult(out_dir=tmp_path, job="legacy", params={"request": "漢" * 200_000})
    write_batch_report(batch)
    text = (tmp_path / "BATCH_REPORT.md").read_text(encoding="utf-8")
    assert (tmp_path / "BATCH_REPORT.md").stat().st_size < REPORT_BYTES
    assert "Truncated" in text and "batch.json" in text
    assert json.loads((tmp_path / "batch.json").read_text(encoding="utf-8"))["params"] == batch.params


def test_preview_cap_keeps_exact_approval_token_and_machine_data(tmp_path):
    from types import SimpleNamespace
    from spec_surgeon.preview import Preview, write_preview_files
    plan = SimpleNamespace(spec={"request": "bounded preview", "summary": "漢" * 200_000},
                           to_text=lambda **kwargs: "one structure pattern", to_dict=lambda: {"full": "plan"})
    preview = Preview(tmp_path, plan, token="a" * 64, token_inputs={"exact": "approval evidence"})
    write_preview_files(preview)
    text = (tmp_path / "PREVIEW.md").read_text()
    assert (tmp_path / "PREVIEW.md").stat().st_size < REPORT_BYTES
    assert "Truncated" in text and "preview.json" in text and "approval token: " + "a" * 64 in text
    saved = json.loads((tmp_path / "preview.json").read_text())
    assert saved["token"] == preview.token and saved["approval_inputs"] == preview.token_inputs


def test_bundle_is_bounded_for_large_incompressible_evidence_and_lists_omissions(tmp_path):
    root = tmp_path / "results"
    root.mkdir()
    (root / "DIAGNOSTICS.md").write_text("V10 failed; rejected files are diagnosis only.")
    # Hex data defeats tiny compressed fixtures; collectively these exceed the bundle budget.
    for i in range(16):
        (root / f"evidence-{i}.json").write_text(json.dumps({"evidence": os.urandom(220_000).hex()}))
    (root / "large-record.json").write_text(json.dumps({"errors": ["private paragraph"] * 100_000}))
    result = diagnostics.make_bundle(tmp_path / "bundle.zip", root)
    assert result["path"].stat().st_size < diagnostics.MAX_BUNDLE_BYTES
    assert result["omitted_count"] > 0
    with zipfile.ZipFile(result["path"]) as bundle:
        assert "results/DIAGNOSTICS.md" in bundle.namelist()
        manifest = json.loads(bundle.read("environment.json"))
        assert manifest["omitted_count"] > 0 and manifest["full_evidence"]
        assert any(x["path"].endswith("large-record.json") for x in manifest["omitted"])
        for name in bundle.namelist():
            if name.endswith(".json"):
                json.loads(bundle.read(name))
    # Packaging neither removes nor truncates original machine evidence.
    assert len(json.loads((root / "large-record.json").read_text())["errors"]) == 100_000
