"""The desktop requires fresh choices and two approvals before producing copies."""
from __future__ import annotations

import time

import pytest
from docx import Document

from test_office_profiles import setup_profile


@pytest.fixture
def ui(tmp_path, monkeypatch):
    from qt_support import close_windows, qt_app_or_skip
    from spec_surgeon.gui import Session
    from spec_surgeon.ui.app import RecordingPrompts, build_legacy_window as build_window
    app = qt_app_or_skip()
    monkeypatch.setenv("SPEC_SURGEON_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("SPEC_SURGEON_CONFIG_DIR", str(tmp_path / "config"))
    source, template, profile = setup_profile(tmp_path, text=["PART 1 - GENERAL", "Unlabelled scope."])
    session = Session(selected_files=[source], out_root=tmp_path / "out")
    window = build_window(session, prompts=RecordingPrompts())
    window.do_office_profiles()
    dialog = window.office_profiles_dialog
    dialog.render.setChecked(False)
    dialog._use_profile(profile)
    yield app, window, dialog, source, template, session
    close_windows(app, [window])


def wait(app, dialog):
    deadline = time.monotonic() + 30
    while dialog.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert not dialog.busy, dialog.status.text()
    app.processEvents()


def test_desktop_review_choices_preview_and_run_create_only_verified_copies(ui):
    app, window, dialog, source, _, session = ui
    original = source.read_bytes()
    assert not dialog.run_button.isEnabled()
    dialog.analyze_button.click()
    wait(app, dialog)
    assert dialog.review["status"] == "NEEDS_INPUT"
    dialog.review_approval.setChecked(True)
    assert not dialog.preview_button.isEnabled()
    choice = dialog.table.cellWidget(1, 3)
    choice.setCurrentIndex(choice.findData("paragraph"))
    assert dialog.review is None and not dialog.review_approval.isChecked()
    dialog.analyze_button.click()
    wait(app, dialog)
    assert dialog.review["status"] == "PROPOSED"
    assert not dialog.preview_button.isEnabled()
    dialog.review_approval.setChecked(True)
    dialog.preview_button.click()
    wait(app, dialog)
    assert dialog.previews, dialog.status.text()
    assert not dialog.run_button.isEnabled()
    dialog.preview_approval.setChecked(True)
    assert dialog.run_button.isEnabled()
    dialog.run_button.click()
    wait(app, dialog)
    assert '"PASS": 1' in dialog.details.toPlainText(), dialog.details.toPlainText()
    assert not dialog.previews and not dialog.run_button.isEnabled()
    assert not window.busy and not session.preview
    assert source.read_bytes() == original


def test_changed_file_scope_and_render_settings_invalidate_approval(ui, tmp_path):
    app, _, dialog, _, _, session = ui
    dialog._choices = {str(session.selected_files[0]): {"1": "exclude"}}
    dialog.analyze()
    wait(app, dialog)
    dialog.review_approval.setChecked(True)
    dialog.preview()
    wait(app, dialog)
    dialog.preview_approval.setChecked(True)
    dialog.render.setChecked(True)
    assert not dialog.previews and not dialog.preview_approval.isChecked()
    dialog.render.setChecked(False)
    dialog.preview()
    wait(app, dialog)
    dialog.preview_approval.setChecked(True)
    other = tmp_path / "other.docx"
    Document().save(other)
    session.selected_files.append(other)
    assert dialog.run() is False
    assert dialog.review is None
    assert "scope changed" in dialog.status.text()


def test_profile_form_collects_explicit_style_policy_and_margin_choice(ui):
    from qt_support import close_windows
    from spec_surgeon.ui.office_profiles_dialog import ProfileCreateDialog
    from spec_surgeon.ui.app import RecordingPrompts
    app, _, _, _, template, _ = ui
    dialog = ProfileCreateDialog(RecordingPrompts())
    try:
        dialog.name.setText("Office standard")
        dialog.template.setText(str(template))
        assert dialog.values()["policies"] == []
        dialog.match_margins.setChecked(True)
        dialog.margins["left"].setValue(1.25)
        dialog.direct.setCurrentIndex(dialog.direct.findData("replace_style_owned"))
        values = dialog.values()
        assert values["policies"][0]["margins"]["left"] == 1800
        assert values["direct_formatting"] == "replace_style_owned"
        dialog._accept()
        assert dialog.result() == dialog.DialogCode.Accepted
    finally:
        close_windows(app, [dialog])
