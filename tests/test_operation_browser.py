"""The built-in contracts are visible offline without changing an editing session."""
import pytest

from spec_surgeon.catalog import op_catalog


@pytest.fixture
def browser():
    from qt_support import close_windows, qt_app_or_skip
    from spec_surgeon.ui.operation_browser import OperationBrowserDialog
    app = qt_app_or_skip()
    dialog = OperationBrowserDialog()
    yield dialog
    close_windows(app, [dialog])


def test_catalog_search_exposes_operations_and_their_limits_without_a_model(browser, monkeypatch):
    from spec_surgeon import llm
    def no_model(*args, **kwargs):
        raise AssertionError("Browsing operations is offline")
    monkeypatch.setattr(llm, "client", no_model)
    assert browser.operations.count() == len(op_catalog(False)["ops"]) == 51
    browser.search.setText("add_watermark")
    assert browser.operations.count() == 1
    detail = browser.details.toPlainText()
    assert "100 characters" in detail
    assert "required" in detail and "V10" in detail
    assert "human visual acceptance" in detail
    browser.search.setText("no operation matches this sentence")
    assert browser.operations.count() == 0
    assert "No operations" in browser.details.toPlainText()


def test_category_filter_and_related_workflow_are_explicit(browser):
    browser.category.setCurrentText("Tables and pictures")
    assert browser.operations.count() == 4
    browser.search.setText("apply_table_style")
    assert browser.operations.count() == 1
    requested = []
    browser.workflow_requested.connect(requested.append)
    browser.workflow.click()
    assert requested == ["format-tables"]
    browser.category.setCurrentText("All categories")
    browser.search.setText("set_document_state")
    assert "Requires an explicit user request" in browser.details.toPlainText()


def test_window_reuses_browser_without_replacing_loaded_recipe(tmp_path, monkeypatch):
    from qt_support import close_windows, qt_app_or_skip
    from spec_surgeon.gui import Session
    from spec_surgeon.ui.app import build_window
    app = qt_app_or_skip()
    monkeypatch.setenv("SPEC_SURGEON_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("SPEC_SURGEON_NO_UPDATE_CHECK", "1")
    session = Session(out_root=tmp_path / "output")
    session.spec = {"request": "Keep this loaded recipe", "ops": []}
    before = session.spec
    window = build_window(session)
    try:
        window.do_operations()
        dialog = window.operation_browser
        dialog.search.setText("set font")
        window.do_operations()
        assert window.operation_browser is dialog
        assert session.spec is before
        assert session.preview is None
    finally:
        close_windows(app, [window])
