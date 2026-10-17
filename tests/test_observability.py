"""Regression tests for durable diagnostic evidence, isolation and credential handling."""
from __future__ import annotations

import contextvars
import json
import logging
import sys
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from spec_surgeon import diagnostics as diag
from spec_surgeon import telemetry


def records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_nested_spans_preserve_cause_timing_and_restore_context(tmp_path):
    path = tmp_path / "events.jsonl"
    before = diag.context()
    with diag.scope(path, run_id="run", file_id="file"):
        with diag.span("parent"):
            with pytest.raises(RuntimeError):
                with diag.span("child", stage="apply"):
                    try:
                        raise KeyError("missing-member")
                    except KeyError as cause:
                        raise RuntimeError("cannot apply") from cause
        diag.event("after")
    assert diag.context() == before
    rows = records(path)
    assert [r["event"] for r in rows] == ["parent.started", "child.started", "child.failed", "parent.succeeded", "after"]
    child = rows[2]
    assert child["parent_span_id"] == rows[0]["span_id"]
    assert rows[0]["span_id"] == rows[3]["span_id"]
    assert child["duration_ms"] >= 0
    assert [x["type"] for x in child["exception_chain"]] == ["RuntimeError", "KeyError"]
    assert "direct cause" in child["traceback"]
    assert "span_id" not in rows[-1]
    for row in rows:
        assert row["run_id"] == "run" and row["file_id"] == "file"
        assert row["timestamp"].endswith("+00:00") and "T" in row["timestamp"]
        assert row["pid"] > 0 and row["thread_id"] and row["schema_version"] == 1
    assert len({r["event_id"] for r in rows}) == len(rows)


def test_started_record_is_readable_while_operation_is_still_running(tmp_path):
    path = tmp_path / "events.jsonl"
    with diag.scope(path), diag.span("pending") as outcome:
        assert records(path)[0]["event"] == "pending.started"
        outcome["count"] = 3
    assert records(path)[1]["count"] == 3


def test_base_exceptions_are_recorded_and_reraised(tmp_path):
    path = tmp_path / "events.jsonl"
    with pytest.raises(KeyboardInterrupt), diag.scope(path), diag.span("interrupted"):
        raise KeyboardInterrupt()
    assert records(path)[-1]["error_type"] == "KeyboardInterrupt"


def test_nested_sinks_do_not_leak_context_or_events(tmp_path):
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    with diag.scope(a, run_id="a"):
        diag.event("a.before")
        with diag.scope(b, file_id="b"):
            diag.event("b")
        diag.event("a.after")
    assert [r["event"] for r in records(a)] == ["a.before", "a.after"]
    assert "file_id" not in records(a)[-1]
    assert records(b)[0]["run_id"] == "a" and records(b)[0]["file_id"] == "b"


def test_thread_handoffs_preserve_correlation_and_complete_json_records(tmp_path):
    path = tmp_path / "events.jsonl"
    def write(worker):
        with diag.scope(worker_id=worker):
            for i in range(30):
                diag.event("checkpoint", number=i)
    with diag.scope(path, run_id="parallel"), ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(contextvars.copy_context().run, write, i) for i in range(8)]
        for future in futures:
            future.result()
    rows = records(path)
    assert len(rows) == 240
    assert all(r["run_id"] == "parallel" for r in rows)
    assert {r["worker_id"] for r in rows} == set(range(8))


def test_credentials_and_payloads_are_not_written(tmp_path, monkeypatch):
    secret = "test-active-provider-credential"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    path = tmp_path / "events.jsonl"
    with diag.scope(path):
        diag.event("redaction", api_key="short-key", nested={"Authorization": "not-bearer-secret"},
            message=f"{secret} Bearer xyz.secret https://user:pass@example.test/?access_token=query-secret",
            prompt="PRIVATE DOCUMENT PROMPT", messages=[{"content": "PRIVATE CHAT"}],
            body="password='plain password'", opaque=object(), image_data=b"picture", input_tokens=42,
            approval_token="approval-hash", request_id="request-123")
    text = path.read_text()
    for hidden in (secret, "short-key", "not-bearer-secret", "xyz.secret", "user:pass", "query-secret",
                   "PRIVATE DOCUMENT", "PRIVATE CHAT", "plain password"):
        assert hidden not in text
    row = records(path)[0]
    assert row["input_tokens"] == 42 and row["approval_token"] == "approval-hash"
    assert row["request_id"] == "request-123"


def test_oversized_cyclic_and_unserializable_fields_do_not_break_events(tmp_path):
    path = tmp_path / "events.jsonl"
    cycle = {}
    cycle["self"] = cycle
    class UnsafeRepr:
        def __repr__(self):
            raise AssertionError("must never evaluate arbitrary repr")
    with diag.scope(path):
        diag.event("bounded", text="x" * 100000, cycle=cycle, items=list(range(200)), opaque=UnsafeRepr(), value=float("nan"))
    row = records(path)[0]
    assert len(row["text"]) < 17000
    assert row["cycle"]["self"] == "[cycle]"
    assert row["items"][-1] == {"omitted_items": 100}
    assert row["opaque"] == "<UnsafeRepr>"


def test_unwritable_diagnostics_warn_once_without_changing_result(tmp_path, capsys):
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    with diag.scope(blocked / "events.jsonl"), diag.span("work"):
        diag.event("still.working")
    warning = capsys.readouterr().err
    assert warning.count("Diagnostics could not be written") == 1
    assert "NotADirectoryError" in warning or "FileExistsError" in warning


def test_failed_progress_callback_does_not_drop_the_persisted_step(tmp_path):
    def broken(message):
        raise ValueError("UI disappeared")
    path = tmp_path / "run.log"
    events = tmp_path / "events.jsonl"
    with diag.scope(events):
        log = diag.RunLog(path, broken)
        log("completed verification")
        log.close()
    assert "completed verification" in path.read_text()
    failure = records(events)[0]
    assert failure["event"] == "diagnostics.progress_callback_failed"
    assert failure["error_type"] == "ValueError" and "UI disappeared" in failure["error"]


def test_readable_logs_also_scrub_credentials(tmp_path):
    path = tmp_path / "trace.log"
    trace = diag.Tracer(path, "header")
    trace.note("plan", "Authorization: Bearer private-token")
    trace.close()
    assert "private-token" not in path.read_text()
    log = diag.RunLog(tmp_path / "run.log")
    log("api_key=private-key")
    log.close()
    assert "private-key" not in (tmp_path / "run.log").read_text()


def test_application_journal_rotates_and_bundle_contains_all_retained_events(tmp_path):
    logs = tmp_path / "logs"
    diag.setup_app_logging(logs)
    handler = next(h for h in telemetry._LOGGER.handlers if isinstance(h, telemetry.SafeRotatingHandler))
    handler.maxBytes = 1200
    for i in range(10):
        diag.event("application.checkpoint", number=i)
    assert Path(str(telemetry.app_events_path(logs)) + ".1").exists()
    with zipfile.ZipFile(diag.make_bundle(tmp_path / "bundle.zip", app_logs=logs)["path"]) as bundle:
        assert "app-logs/" + telemetry.app_events_path(logs).name in bundle.namelist()
        assert "app-logs/" + telemetry.app_events_path(logs).name + ".1" in bundle.namelist()
        for name in bundle.namelist():
            if "jsonl" in name:
                assert all(json.loads(line)["schema_version"] == 1 for line in bundle.read(name).splitlines())


def test_switching_app_logs_does_not_keep_writing_to_old_sessions(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    diag.setup_app_logging(a)
    diag.event("first")
    diag.setup_app_logging(b)
    diag.event("second")
    assert [r["event"] for r in records(telemetry.app_events_path(a))] == ["first"]
    assert [r["event"] for r in records(telemetry.app_events_path(b))] == ["second"]


def test_unhandled_hooks_record_exception_and_preserve_original_hooks(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: called.append("process"))
    monkeypatch.setattr(threading, "excepthook", lambda args: called.append("thread"))
    diag.install_exception_hooks()
    hook = sys.excepthook
    diag.install_exception_hooks()
    assert sys.excepthook is hook
    path = tmp_path / "events.jsonl"
    with diag.scope(path):
        try:
            raise ValueError("unhandled failure")
        except ValueError as exc:
            sys.excepthook(type(exc), exc, exc.__traceback__)
            threading.excepthook(SimpleNamespace(exc_value=exc, thread=threading.current_thread()))
    assert called == ["process", "thread"]
    assert [r["event"] for r in records(path)] == ["process.unhandled_exception", "thread.unhandled_exception"]
    assert "test_unhandled_hooks" in records(path)[0]["traceback"]


def test_unreadable_exception_cannot_replace_original_failure(tmp_path):
    class BrokenException(Exception):
        def __str__(self):
            raise ValueError("broken str")
    original = BrokenException()
    path = tmp_path / "events.jsonl"
    with pytest.raises(BrokenException) as caught, diag.scope(path), diag.span("operation"):
        raise original
    assert caught.value is original
    assert records(path)[-1]["error_type"] == "BrokenException"


def test_parent_process_trace_continuation_scrubs_credentials(tmp_path):
    path = tmp_path / "trace.log"
    lines = diag.trace_event("render", "password=private-password", "Authorization: Bearer private-token")
    diag.append_trace(path, lines)
    assert "private-password" not in "\n".join(lines)
    assert "private-token" not in path.read_text()
    diag.append_trace(path, ["api_key=direct-private-key"])
    assert "direct-private-key" not in path.read_text()


def test_independent_processes_do_not_lose_events_during_rotation(tmp_path):
    import subprocess
    script = '''
import sys
from pathlib import Path
from spec_surgeon import telemetry as t
t.setup_events(Path(sys.argv[1]))
h = next(h for h in t._LOGGER.handlers if isinstance(h, t.SafeRotatingHandler))
h.maxBytes = 2000
h.backupCount = 100
for i in range(40):
    t.event("process.checkpoint", number=i)
'''
    processes = [subprocess.Popen([sys.executable, "-c", script, str(tmp_path)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
    for process in processes:
        out, err = process.communicate(timeout=30)
        assert process.returncode == 0, (out, err)
        assert not err
    rows = [row for path in tmp_path.glob("app-events-*.jsonl*") for row in records(path)]
    assert len(rows) == 80 and len({r["event_id"] for r in rows}) == 80
    assert len({r["pid"] for r in rows}) == 2


def test_retention_keeps_live_process_journals_and_recent_inactive_groups(tmp_path, monkeypatch):
    import os
    for i in range(10):
        for suffix in ("", ".1"):
            path = tmp_path / f"app-events-{100 + i}.jsonl{suffix}"
            path.write_text("old journal")
            os.utime(path, (1000 + i, 1000 + i))
    monkeypatch.setattr(telemetry, "_process_alive", lambda pid: pid == 100)
    telemetry._prune_events(tmp_path)
    assert (tmp_path / "app-events-100.jsonl").exists()
    assert (tmp_path / "app-events-100.jsonl.1").exists()
    assert len(list(tmp_path.iterdir())) == 12  # five inactive groups plus the live one
    assert not (tmp_path / "app-events-101.jsonl").exists()
    assert (tmp_path / "app-events-109.jsonl.1").exists()


def test_reconfiguring_inherited_app_handler_reopens_for_current_process(tmp_path, monkeypatch):
    import os
    path = diag.setup_app_logging(tmp_path)
    logger = logging.getLogger("spec_surgeon")
    inherited = next(h for h in logger.handlers if isinstance(h, telemetry.SafeRotatingHandler))
    monkeypatch.setattr(inherited, "owner_pid", -1)
    diag.setup_app_logging(tmp_path)
    current = next(h for h in logger.handlers if isinstance(h, telemetry.SafeRotatingHandler))
    assert current is not inherited and current.owner_pid == os.getpid()
    diag.app_log().info("current process writes readable logs")
    assert "current process writes readable logs" in path.read_text()
