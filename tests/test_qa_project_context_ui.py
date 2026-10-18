"""Desktop QA keeps one explicitly chosen project register across reruns."""
from __future__ import annotations

import json

import pytest

from spec_surgeon.spec_qa import AUDITS


def context():
    return {"project_evidence": {"version": 1, "facts": [], "standards": [{
        "id": "nfpa-13", "identifier": "NFPA 13", "edition": "2025", "basis": "Approved project register",
        "scope": {"kind": "project"}, "source": {"kind": "user", "reference": "Reviewed coordination memo"},
        "confirmed_by": "Project reviewer",
    }]}}


def window(tmp_path, monkeypatch, *, selections=()):
    from qt_support import qt_app_or_skip
    from docx import Document
    from spec_surgeon.gui import Session
    from spec_surgeon.ui.app import RecordingPrompts, build_window
    app = qt_app_or_skip()
    monkeypatch.setenv("SPEC_SURGEON_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("SPEC_SURGEON_NO_UPDATE_CHECK", "1")
    folder = tmp_path / "specs"
    folder.mkdir()
    path = folder / "source.docx"
    document = Document()
    for text in ("SECTION 21 13 13", "WET-PIPE SPRINKLERS", "PART 1 GENERAL", "NFPA 13, 2022 Edition."):
        document.add_paragraph(text)
    document.save(path)
    prompts = RecordingPrompts(files=list(selections))
    w = build_window(Session(folder=folder, out_root=tmp_path / "out"), prompts=prompts)
    return app, w, path


def finish(app, w):
    w._spec_surgeon["wait"]()
    app.processEvents()


@pytest.mark.parametrize("evidence_only", [False, True])
def test_import_register_identity_rerun_and_clear_keep_sources_untouched(tmp_path, monkeypatch, evidence_only):
    from qt_support import close_windows
    imported = tmp_path / "selected-context.json"
    data = context()
    imported.write_text(json.dumps(data["project_evidence"] if evidence_only else data), encoding="utf-8")
    app, w, source = window(tmp_path, monkeypatch, selections=[str(imported)])
    original = source.read_bytes()
    try:
        assert w.do_audit()
        finish(app, w)
        assert w.audit_dialog.report["audits"] == list(AUDITS)
        assert "No approved standards register" in w.audit_dialog.context_status.text()
        assert w.audit_dialog.import_context()
        finish(app, w)
        dialog = w.audit_dialog
        assert dialog.report["files"][0]["standards_register"][0]["status"] == "mismatch"
        assert str(imported) in dialog.context_status.text()
        assert "1 approved standard entries" in dialog.context_status.text()
        # A later disk change cannot silently switch the register in this review.
        imported.write_text(json.dumps({"project_evidence": {"version": 1, "facts": [], "standards": []}}))
        dialog.identity_fields["project_name"].setText("Harbor Tower")
        dialog.recheck_button.click()
        finish(app, w)
        assert w.audit_dialog.report["identity_variants"]["project_name"]["expected"] == "Harbor Tower"
        assert w.audit_dialog.report["files"][0]["standards_register"][0]["status"] == "mismatch"
        assert w.audit_dialog.clear_context()
        finish(app, w)
        assert "standards_register" not in w.audit_dialog.report["files"][0]
        assert "No approved standards register" in w.audit_dialog.context_status.text()
        assert source.read_bytes() == original
    finally:
        close_windows(app, [w])


def test_invalid_or_multi_project_context_import_does_not_replace_current_report(tmp_path, monkeypatch):
    from qt_support import close_windows
    selected = tmp_path / "workspace.json"
    selected.write_text(json.dumps({"projects": {"project-a": {"context": context()}, "project-b": {"context": context()}}}))
    app, w, _ = window(tmp_path, monkeypatch, selections=[str(selected)])
    try:
        w.do_audit()
        finish(app, w)
        dialog = w.audit_dialog
        assert not dialog.import_context()
        assert w.audit_dialog is dialog
        assert dialog.project_context is None
    finally:
        close_windows(app, [w])


def test_project_context_is_captured_before_worker_runs(tmp_path, monkeypatch):
    from qt_support import close_windows
    app, w, _ = window(tmp_path, monkeypatch)
    captured = {}
    def defer(message, action, **kwargs):
        captured["action"] = action
        return True
    monkeypatch.setattr(w, "_work", defer)
    data = context()
    try:
        assert w._start_audit(project_context=data, context_label="Selected project")
        data["project_evidence"]["standards"][0]["edition"] = "2022"
        report = captured["action"](lambda *args: None)
        assert report["files"][0]["standards_register"][0]["status"] == "mismatch"
    finally:
        close_windows(app, [w])
