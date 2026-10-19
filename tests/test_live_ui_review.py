"""Independent UI review: editable evidence and per-job model activity stay truthful."""
from __future__ import annotations

import threading
import time

import pytest

from fake_claude import FakeClient, resp, text
from make_fixtures import build, build_template
from spec_surgeon import llm
from spec_surgeon.gui import Session
from test_live_template_bindings import bound_spec


@pytest.fixture
def qt_env(monkeypatch):
    from qt_support import close_windows, qt_app_or_skip
    app = qt_app_or_skip()
    monkeypatch.setenv("SPEC_SURGEON_NO_UPDATE_CHECK", "1")
    windows = []
    try:
        yield app, windows
    finally:
        close_windows(app, windows)


def _until(app, predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert predicate()


def _window(session):
    from spec_surgeon.ui.app import RecordingPrompts, build_window
    return build_window(session, prompts=RecordingPrompts())


@pytest.mark.parametrize("paragraph", ["not a number", "0", "-1"])
def test_fill_paragraph_correction_is_plain_and_preserves_old_approval(qt_env, tmp_path, paragraph):
    folder = tmp_path / "specs"
    folder.mkdir()
    source, template = folder / "source.docx", tmp_path / "template.docx"
    source.write_bytes(build("21 05 00", "BODY TITLE"))
    template.write_bytes(build_template())
    session = Session(folder=folder, out_root=tmp_path / "out")
    session.spec, session.spec_source = bound_spec(source, template), "saved.json"
    session.spec["bindings"][0]["slots"]["SECTION_TITLE"].update(
        value="BODY TITLE", part="word/document.xml", paragraph=1)
    window = _window(session)
    qt_env[1].append(window)
    window.request_page.render_cb.setChecked(False)
    assert window.do_preview()
    window._spec_surgeon["wait"]()
    token = session.preview.token
    window.preview_page.bindings_table.item(0, 3).setText(paragraph)
    assert not window._apply_bindings()
    assert not window.run_page.run_btn.isEnabled()
    assert session.preview.token == token
    assert session.spec["bindings"][0]["slots"]["SECTION_TITLE"]["paragraph"] == 1
    message = window.preview_page.info.text.text()
    assert "enter a paragraph number of 1 or higher" in message
    assert "invalid literal" not in message
    from PySide6.QtCore import Qt
    assert window.preview_page.info.text.textFormat() == Qt.TextFormat.PlainText


def test_one_job_reports_nested_compile_recovery_and_visual_usage_without_document_text(qt_env, tmp_path):
    window = _window(Session(out_root=tmp_path / "out"))
    qt_env[1].append(window)
    fake = FakeClient(resp(text("compile secret"), stop="end_turn", inp=100, out=10),
                      resp(text("recovery secret"), stop="end_turn", inp=200, out=20),
                      resp(text("visual secret"), stop="end_turn", inp=300, out=30))
    reached, release = threading.Event(), threading.Event()
    responses = []

    def nested_model_work(stage):
        response = llm.call(fake, model=llm.DEFAULT_MODEL, system="private source text",
                            tools=[], messages=[{"role": "user", "content": stage + " private source text"}])
        responses.append(response)

    def work(log):
        for stage in ("compile", "recovery", "visual"):
            nested_model_work(stage)
        reached.set()
        assert release.wait(5)
        return "done"

    assert window._work("Preview", work)
    try:
        _until(qt_env[0], lambda: reached.is_set() and window._job_model_totals["calls"] == 3)
        expected = llm.Usage()
        for response in responses:
            expected.add(response, llm.DEFAULT_MODEL)
        _until(qt_env[0], lambda: window._job_model_totals["cost"] == expected.line())
        totals = window._job_model_totals
        assert totals == {"calls": 3, "cost": expected.line()}
        assert "3 model call(s)" in window.status_lbl.text() and expected.line() in window.status_lbl.text()
        assert "private" not in repr(totals) and "secret" not in repr(totals)
    finally:
        release.set()
        window._spec_surgeon["wait"]()


def test_cancelled_job_stops_next_call_and_next_job_starts_with_fresh_totals(qt_env, tmp_path):
    window = _window(Session(out_root=tmp_path / "out"))
    qt_env[1].append(window)
    fake = FakeClient(resp(text("first"), stop="end_turn"), resp(text("next job"), stop="end_turn"))
    reached, release = threading.Event(), threading.Event()

    def call():
        return llm.call(fake, model=llm.DEFAULT_MODEL, system="s", tools=[], messages=[])

    def work(log):
        call()
        reached.set()
        assert release.wait(5)
        call()

    assert window._work("Preview", work, page=window.request_page)
    try:
        _until(qt_env[0], lambda: reached.is_set() and window._job_model_totals["calls"] == 1)
        window._cancel_job()
    finally:
        release.set()
        window._spec_surgeon["wait"]()
    assert len(fake.requests) == 1
    assert "Cancelled before the next model call" in window.request_page.info.text.text()
    assert window.status_lbl.text() == "Cancelled"
    assert "ERROR:" not in window.log.toPlainText() and "Traceback" not in window.log.toPlainText()
    assert window._work("Preview", lambda log: call())
    window._spec_surgeon["wait"]()
    assert len(fake.requests) == 2 and window._job_model_totals["calls"] == 1


def test_progress_observer_failure_never_changes_accepted_model_response():
    fake = FakeClient(resp(text("accepted"), stop="end_turn"))

    def broken(*args):
        raise RuntimeError("observer failed")

    with llm.model_progress_scope(broken):
        response = llm.call(fake, model=llm.DEFAULT_MODEL, system="s", tools=[], messages=[])
    assert response.content[0].text == "accepted" and len(fake.requests) == 1
