import pytest

from spec_surgeon.campaign import create_campaign, load_campaign


@pytest.fixture
def dialog(tmp_path):
    from make_fixtures import build
    from qt_support import close_windows, qt_app_or_skip
    from spec_surgeon.ui.project_evidence_dialog import ProjectEvidenceDialog
    app = qt_app_or_skip()
    source = tmp_path / "source.docx"
    source.write_bytes(build("21 13 13", "SPRINKLER SYSTEMS"))
    campaign = create_campaign([str(source)], tmp_path / "workspace")
    project_id = next(iter(campaign.projects))
    view = ProjectEvidenceDialog(campaign, project_id)
    windows = [view]
    yield view
    close_windows(app, windows)


def test_dialog_preserves_assumptions_and_requires_reviewer_before_confirmation(dialog):
    fields = dialog.fact_form
    fields["id"].setText("initial")
    fields["statement"].setPlainText("Ordinary hazard")
    fields["source"].setText("Designer assumption pending owner review")
    dialog.add_fact()
    assert dialog.facts.rowCount() == 1 and dialog.changed
    dialog.facts.selectRow(0)
    fields["id"].setText("reviewed")
    fields["statement"].setPlainText("Light hazard")
    fields["status"].setCurrentText("confirmed")
    fields["source"].setText("Owner review")
    dialog.supersede_fact()
    assert dialog.facts.rowCount() == 1 and "confirmed_by" in dialog.status.text()
    fields["reviewer"].setText("A. Reviewer")
    dialog.supersede_fact()
    assert dialog.facts.rowCount() == 2
    context = load_campaign(dialog.campaign.out_dir).projects[dialog.project_id]["context"]
    assert [r["status"] for r in context["project_evidence"]["facts"]] == ["superseded", "confirmed"]


def test_dialog_standards_require_basis_and_persist_exact_user_edition(dialog):
    fields = dialog.standard_form
    fields["identifier"].setText("NFPA 13")
    fields["edition"].setText("2019")
    fields["source"].setText("Permit register")
    fields["reviewer"].setText("A. Reviewer")
    dialog.add_standard()
    assert dialog.standards.rowCount() == 0 and "basis" in dialog.status.text()
    fields["basis"].setText("Approved permit basis, item 4")
    dialog.add_standard()
    assert dialog.standards.rowCount() == 1
    dialog.standards.selectRow(0)
    dialog.remove_standard()
    assert dialog.standards.rowCount() == 0
