"""Discover a built-in workflow in the keyless desktop, then preview and run through the guarded session."""
from __future__ import annotations

import pytest

from make_fixtures import build_styles
from spec_surgeon import llm
from spec_surgeon.gui import Session
from spec_surgeon.workflows import get_workflow


@pytest.fixture
def qt(monkeypatch):
    from qt_support import close_windows, qt_app_or_skip
    app = qt_app_or_skip()
    monkeypatch.setenv("SPEC_SURGEON_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(llm, "credentials_found", lambda: False)
    windows = []
    yield app, windows
    close_windows(app, windows)


def _window(qt, session, prompts):
    from spec_surgeon.gui import build_window
    window = build_window(session, prompts=prompts)
    qt[1].append(window)
    return window, window._spec_surgeon


def test_keyless_workflow_is_discoverable_and_cannot_run_before_preview(qt, tmp_path):
    from spec_surgeon.ui.app import RecordingPrompts
    folder = tmp_path / "specs"
    folder.mkdir()
    source = folder / "sprinklers.docx"
    source.write_bytes(build_styles())
    before = source.read_bytes()
    definition = get_workflow("proofing-language")
    choice = definition["name"] + " — " + definition["description"]
    prompts = RecordingPrompts(items=[choice], workflows=[{"language": "en-US"}])
    session = Session(folder=folder, out_root=tmp_path / "out")
    window, ui = _window(qt, session, prompts)
    assert "workflow" in window.request_page.saved_card.title.text()
    assert ui["buttons"]["workflows"].isEnabled()
    ui["buttons"]["workflows"].click()
    assert session.chain_source == "workflow:proofing-language"
    assert session.preview is None and not ui["buttons"]["run"].isEnabled()
    assert source.read_bytes() == before
    ui["vars"]["render"].set(False)
    ui["vars"]["in_place"].set(False)
    ui["buttons"]["preview"].click()
    ui["wait"]()
    assert session.preview is not None and ui["buttons"]["run"].isEnabled()
    ui["buttons"]["run"].click()
    ui["wait"]()
    assert session.last_chain.counts()["PASS"] == 1
    assert source.read_bytes() == before


def test_cancel_or_invalid_workflow_keeps_current_selection(qt, tmp_path):
    from spec_surgeon.ui.app import RecordingPrompts
    definition = get_workflow("proofing-language")
    choice = definition["name"] + " — " + definition["description"]
    prompts = RecordingPrompts(items=[choice, choice], workflows=[None, {}])
    session = Session(out_root=tmp_path / "out")
    original = session.use_workflow("proofing-language", {"language": "en-US"})
    window, _ = _window(qt, session, prompts)
    assert window.do_workflow() is False and session.chain == original
    assert window.do_workflow() is False and session.chain == original
    assert prompts.shown[-1][0] == "warn" and "language is required" in prompts.shown[-1][2]


def test_workflow_form_uses_named_styles_lists_and_explicit_cleanup(qt, tmp_path):
    from PySide6.QtWidgets import QTableWidgetItem
    from spec_surgeon.ui.app import RecordingPrompts
    from spec_surgeon.ui.workflow_dialog import WorkflowDialog
    from make_fixtures import build_style_template
    template = tmp_path / "office.docx"
    template.write_bytes(build_style_template())
    dialog = WorkflowDialog("architect-styles", RecordingPrompts())
    qt[1].append(dialog)
    dialog.editors["template"].setText(str(template))
    table = dialog.editors["style_map"].table
    table.setItem(0, 0, QTableWidgetItem("ArchNote"))
    table.setItem(0, 1, QTableWidgetItem("SpecNote"))
    values = dialog.values()
    assert values["style_map"] == [{"from": "ArchNote", "to": "SpecNote"}]
    assert values["scope"] == ["document"] and values["unused_styles"] == "keep"
    assert values["keep_styles"] == []
    dialog._accept_inputs()
    assert dialog.result() == dialog.DialogCode.Accepted


def test_table_form_requires_selection_and_layout_without_inventing_defaults(qt):
    from spec_surgeon.ui.app import RecordingPrompts
    from spec_surgeon.ui.workflow_dialog import WorkflowDialog
    dialog = WorkflowDialog("format-tables", RecordingPrompts())
    qt[1].append(dialog)
    dialog.editors["style"].setText("Table Grid")
    dialog._accept_inputs()
    assert "Choose which tables" in dialog.error.text()
    selection = dialog.editors["tables"]
    selection.choice.setCurrentIndex(4)
    selection.first.setValue(2)
    selection.last.setValue(3)
    dialog._accept_inputs()
    assert "properties must request" in dialog.error.text()
    layout = dialog.editors["properties"]
    layout.enabled["width"].setChecked(True)
    layout.editors["width"].setValue(90)
    layout.rows.setCurrentIndex(1)
    values = dialog.values()
    assert values["tables"] == {"index_range": [1, 2]}
    assert values["properties"] == {"width": {"pct": 90}, "cant_split": True}
    dialog._accept_inputs()
    assert dialog.result() == dialog.DialogCode.Accepted


def test_numbering_form_requires_source_choice_and_preserves_level_order(qt):
    from spec_surgeon.ui.app import RecordingPrompts
    from spec_surgeon.ui.workflow_dialog import WorkflowDialog
    dialog = WorkflowDialog("match-numbering", RecordingPrompts())
    qt[1].append(dialog)
    dialog.editors["levels"].setPlainText("PRT\nART\nPR1")
    with pytest.raises(ValueError, match="Choose which automatic numbering"):
        dialog.values()
    source = dialog.editors["source"]
    source.choice.setCurrentIndex(2)
    source.styles.setPlainText("Part, Alternate Part\nArticle\nParagraph")
    values = dialog.values()
    assert values["levels"] == ["PRT", "ART", "PR1"]
    assert values["source"] == {"styles": [["Part", "Alternate Part"], ["Article"], ["Paragraph"]]}
