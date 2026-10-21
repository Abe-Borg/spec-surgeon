"""Reconstruct window actions, approval refusals and cross-thread failures without recording request payloads."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from make_fixtures import build
from spec_surgeon import diagnostics as diag
from spec_surgeon.editspec import load_spec
from spec_surgeon.gui import Session

SPEC = Path(__file__).resolve().parent.parent / "examples" / "specs" / "remove-all-headers-footers.json"


def events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def load(session):
    """A saved EditSpec becomes the session's outcome (what the window does with a spec file)."""
    session._set_outcome(spec=load_spec(SPEC), source=str(SPEC))


@pytest.fixture
def session(tmp_path):
    folder = tmp_path / "specs"
    folder.mkdir()
    (folder / "sample.docx").write_bytes(build("21 01 00", "SAMPLE"))
    return Session(documents=sorted(folder.glob("*.docx")), out_root=tmp_path / "out", visual_check=False)


def test_session_actions_correlate_preview_and_delivery(session, tmp_path):
    journal = tmp_path / "events.jsonl"
    with diag.scope(journal):
        load(session)
        session.make_preview(False, lambda _: None)
        result = session.run(False, False, lambda _: None)
    assert "PASS 1" in result
    records = events(journal)
    actions = [r for r in records if r["event"].startswith("session.")]
    assert {r["session_id"] for r in actions} == {session.diagnostic_id}
    outcome = next(r for r in actions if r["event"] == "session.set_outcome.succeeded")
    assert outcome["before"]["outcome"] == "none" and outcome["after"]["outcome"] == "spec"
    preview = next(r for r in actions if r["event"] == "session.make_preview.succeeded")
    assert preview["after"]["approval_digest"] == session.preview.token
    assert preview["options"]["render"] is False
    accepted = next(r for r in actions if r["event"] == "session.approval.accepted")
    assert accepted["file_count"] == 1 and accepted["in_place"] is False
    assert accepted["approval_digest"] == session.preview.token
    started = next(r for r in actions if r["event"] == "session.run.started")
    assert accepted["span_id"] == started["span_id"]            # the approval belongs to this delivery
    run = next(r for r in actions if r["event"] == "session.run.succeeded")
    assert run["after"]["last_run_dir"] == str(session.last_run_dir)
    assert run["duration_ms"] >= 0


def test_stale_preview_records_exact_guard_and_keeps_original(session, tmp_path):
    journal = tmp_path / "events.jsonl"
    load(session)
    session.make_preview(False, lambda _: None)
    source = session.documents[0]
    before = source.read_bytes()
    os.utime(source, ns=(source.stat().st_atime_ns, source.stat().st_mtime_ns + 10_000_000))
    with diag.scope(journal), pytest.raises(ValueError, match="changed since the preview"):
        session.run(False, False, lambda _: None)
    assert source.read_bytes() == before
    records = events(journal)
    refusal = next(r for r in records if r["event"] == "session.approval.refused")
    assert refusal["gate"] == "approval_digest"
    assert refusal["reason"] == "preview_inputs_changed"
    assert any("sample.docx" in item for item in refusal["changes"])
    failed = next(r for r in records if r["event"] == "session.run.failed")
    assert failed["error_type"] == "ValueError"
    assert failed["options"]["in_place"] is False
    assert failed["after"]["last_run_dir"] is None


def test_early_failure_records_metadata_without_request_or_reply(session, tmp_path, monkeypatch):
    journal = tmp_path / "events.jsonl"
    request = "CONFIDENTIAL_REQUEST_CONTENT_DO_NOT_LOG"
    monkeypatch.setattr(session, "claude_ready", lambda: False)
    with diag.scope(journal), pytest.raises(ValueError, match="API key"):
        session.compile(request, lambda _: None)
    records = events(journal)
    failed = next(r for r in records if r["event"] == "session.compile.failed")
    assert failed["options"]["request_characters"] == len(request)
    assert failed["after"]["out_dir"] is None
    assert request not in journal.read_text(encoding="utf-8")


def test_internal_session_failure_keeps_cause_and_does_not_change_behavior(session, tmp_path, monkeypatch):
    from spec_surgeon import preview
    cause = OSError("fixture storage disappeared")
    error = RuntimeError("fixture planner defect")

    def broken(*args, **kwargs):
        raise error from cause

    load(session)
    monkeypatch.setattr(preview, "make_preview", broken)
    journal = tmp_path / "events.jsonl"
    with diag.scope(journal), pytest.raises(RuntimeError) as raised:
        session.make_preview(False, lambda _: None)
    assert raised.value is error
    failed = next(r for r in events(journal) if r["event"] == "session.make_preview.failed")
    assert [item["type"] for item in failed["exception_chain"]] == ["RuntimeError", "OSError"]
    assert "broken" in failed["traceback"]
    assert session.preview is None and session.spec is not None     # nothing previewed, the outcome kept


def test_the_window_job_preserves_action_context_and_failure_before_the_page_hears(tmp_path):
    """The server's job thread carries the request's session and span (``copy_context``) and journals a failure
    under them, with the queue wait, before the page is told."""
    from spec_surgeon.document_task import DocumentTask
    from spec_surgeon.settings import Settings
    from spec_surgeon.web.server import WindowState
    journal = tmp_path / "events.jsonl"
    task = DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=False)

    def perform(action, log, events, **values):
        diag.event("fixture.worker.checkpoint")
        raise RuntimeError("fixture background defect")
    task.perform = perform
    state = WindowState(task, Settings(out_root=str(tmp_path / "out")), token="t")
    with diag.scope(journal, session_id="fixture-session", action_id="fixture-action"):
        with diag.span("fixture.dispatch"):
            queue = state.start_job("send", text="anything")
        delivered = queue.get(timeout=30)
        state.thread.join(timeout=10)
    assert not state.thread.is_alive()
    assert delivered["type"] == "error" and "could not finish" in delivered["message"]
    records = events(journal)
    queued = next(r for r in records if r["event"] == "fixture.dispatch.started")
    checkpoint = next(r for r in records if r["event"] == "fixture.worker.checkpoint")
    failed = next(r for r in records if r["event"] == "web.job.failed")
    assert failed["parent_span_id"] == queued["span_id"] and failed["action"] == "send"
    assert checkpoint["session_id"] == "fixture-session" and checkpoint["action_id"] == "fixture-action"
    assert checkpoint["thread_id"] != threading.get_ident()
    assert failed["queue_ms"] >= 0 and failed["duration_ms"] >= 0
    assert "fixture background defect" in failed["error"]
