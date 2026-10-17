"""Planted failures leave correlated evidence without changing pipeline decisions."""
from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from make_fixtures import build
from spec_surgeon import batch, diagnostics as diag, guard, preview
from spec_surgeon.chain import chain_from_specs, run_chain
from spec_surgeon.render import libreoffice
from spec_surgeon.render.word_com import WordSession

_REAL_PROCESS = batch._process_one


def _crash_on_bad(path, *args, **kwargs):
    if Path(path).name == "Bad.docx":
        os._exit(7)
    return _REAL_PROCESS(path, *args, **kwargs)


def _spec(find="FM Global", replace="GRC"):
    return {"request": f"replace {find} with {replace}", "summary": "replace approved wording", "mode": "content",
            "ops": [{"op": "replace_text", "select": {"scope": ["document"]},
                     "params": {"find": find, "replace": replace, "author": "Abraham Borg"}}]}


def _events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _source(tmp_path, name="Spec.docx"):
    path = tmp_path / name
    path.write_bytes(build("21 13 13", "WET-PIPE"))
    return path


def test_worker_processes_keep_batch_correlation_and_delivery_evidence(tmp_path):
    sources = [_source(tmp_path, name) for name in ("A.docx", "B.docx")]
    out = tmp_path / "run"
    result = batch.run_batch([str(f) for f in sources], "edit-spec", {}, out, workers=2,
                             do_render=False, spec=_spec())
    assert result.counts()["PASS"] == 2
    parent = _events(out / "logs" / "events.jsonl")
    run_id = parent[0]["run_id"]
    queued = [e for e in parent if e["event"] == "worker.queued"]
    assert {e["source"] for e in queued} == set(map(str, sources))
    assert len({e["batch_id"] for e in queued}) == 1
    for source in sources:
        worker = _events(out / "logs" / f"{source.stem}.trace.events.jsonl")
        assert all(e["run_id"] == run_id and e["source"] == str(source) for e in worker)
        assert all(e["pid"] != os.getpid() for e in worker)
        assert worker[0]["event"] == "file.process.started"
        assert worker[-1]["event"] == "file.process.succeeded" and worker[-1]["status"] == "pass"
        assert worker[-1]["duration_ms"] >= 0
    delivery = [e for e in parent if e["event"] == "delivery.identity"]
    assert len(delivery) == 2 and all(e["matches"] and e["expected_md5"] == e["actual_md5"] for e in delivery)
    assert len([e for e in parent if e["event"] == "delivery.finished" and e["status"] == "PASS"]) == 2


def test_validation_failure_is_logged_without_creating_unsafe_results(tmp_path):
    source = _source(tmp_path)
    journal = tmp_path.parent / (tmp_path.name + ".events.jsonl")
    with diag.scope(journal):
        with pytest.raises(ValueError):
            batch.run_batch([str(source)], "edit-spec", {}, tmp_path, do_render=False, spec=_spec())
    events = _events(journal)
    assert events[-1]["event"] == "batch.failed"
    assert events[-1]["error_type"] == "ValueError"
    assert "check_inputs_outside" in events[-1]["traceback"]
    assert not (tmp_path / "logs").exists()
    assert not (tmp_path / "outputs").exists()


def test_native_worker_death_leaves_retry_and_terminal_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "_process_one", _crash_on_bad)
    sources = [_source(tmp_path, name) for name in ("Good.docx", "Bad.docx")]
    out = tmp_path / "run"
    result = batch.run_batch([str(f) for f in sources], "edit-spec", {}, out, workers=2,
                             do_render=False, spec=_spec())
    by_name = {f.source.name: f for f in result.files}
    assert by_name["Good.docx"].status == "PASS"
    assert by_name["Bad.docx"].exception["code"] == "worker-died"
    events = _events(out / "logs" / "events.jsonl")
    failed = [e for e in events if e.get("source") == str(sources[1])]
    assert any(e["event"] == "worker.pool_broken" and e["retry_scheduled"] for e in failed)
    assert any(e["event"] == "worker.retry" and e["attempt"] == 2 for e in failed)
    assert any(e["event"] == "worker.died" and e["attempt"] == 2 and e["stage"] for e in failed)
    assert not (out / "outputs" / "Bad.docx").exists()


def test_guard_refusal_records_identity_and_does_not_replace_source(tmp_path):
    source = _source(tmp_path)
    identity = guard.acquire(source)
    source.write_bytes(b"changed by another editor")
    journal = tmp_path / "guard.events.jsonl"
    with diag.scope(journal, run_id="guard-case"):
        with pytest.raises(guard.GuardError):
            guard.write_back(identity, b"edited copy")
    assert source.read_bytes() == b"changed by another editor"
    events = _events(journal)
    refusal = next(e for e in events if e["event"] == "guard.refused")
    assert refusal["check"] == "source_identity" and refusal["source"] == str(source)
    assert events[-1]["event"] == "guard.write_back.failed"
    assert not any(e["event"] == "guard.replace.started" for e in events)


def test_preview_sample_failure_names_source_phase_and_original_exception(tmp_path, monkeypatch):
    source = _source(tmp_path)
    original = source.read_bytes()

    def broken_apply(*args, **kwargs):
        raise RuntimeError("planted sample failure")

    monkeypatch.setattr(preview, "apply_spec", broken_apply)
    out = tmp_path / "preview"
    result = preview.make_preview(_spec(), [str(source)], out, do_render=False)
    assert result.samples[0].exception["stage"] == "apply"
    assert source.read_bytes() == original
    events = _events(out / "logs" / "events.jsonl")
    failure = next(e for e in events if e["event"] == "preview.sample.apply.failed")
    assert failure["source"] == str(source) and failure["error_type"] == "RuntimeError"
    assert "planted sample failure" in failure["traceback"]
    assert any(e["event"] == "preview.sample_failed" and e["exception"]["stage"] == "apply" for e in events)
    assert events[-1]["event"] == "preview.succeeded"  # the usable preview still returns its refusal


def test_renderer_timeout_retry_preserves_thread_context_and_process_evidence(tmp_path, monkeypatch):
    source = _source(tmp_path)
    monkeypatch.setattr(libreoffice, "find_soffice", lambda: "/fake/soffice")

    def timed_out(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"], output=b"starting conversion", stderr=b"hung on file")

    monkeypatch.setattr(libreoffice.subprocess, "run", timed_out)
    journal = tmp_path / "render.events.jsonl"
    with diag.scope(journal, run_id="render-case"):
        result = libreoffice.render_pdfs([source], tmp_path / "pdf", workers=1, timeout=7)
    assert result == {}
    events = _events(journal)
    failures = [e for e in events if e["event"] == "render.process.failed"]
    assert len(failures) == 2
    assert failures[0]["thread_id"] != threading.get_ident()
    assert failures[1]["thread_id"] == threading.get_ident()
    assert [e["render_attempt"] for e in failures] == [1, 2]
    assert all(e["run_id"] == "render-case" and e["timeout_seconds"] == 7 for e in failures)
    assert all(e["stderr"] == "hung on file" and e["error_type"] == "TimeoutExpired" for e in failures)
    assert any(e["event"] == "render.retry" and e["attempt"] == 2 for e in events)
    assert any(e["event"] == "render.file_failed" and e["source"] == str(source) for e in events)


def test_word_export_failure_identifies_com_step_and_records_close(tmp_path):
    source = _source(tmp_path)
    closed = []

    def export(*args):
        raise OSError("planted COM export failure")

    document = SimpleNamespace(ExportAsFixedFormat=export, Close=lambda **kwargs: closed.append(kwargs))
    session = WordSession()
    session.tmp = tmp_path / "word-scratch"
    session.tmp.mkdir()
    session.app = SimpleNamespace(Documents=SimpleNamespace(Open=lambda *args, **kwargs: document))
    journal = tmp_path / "word.events.jsonl"
    with diag.scope(journal):
        with pytest.raises(OSError, match="planted COM export failure"):
            session.render_pdf(source, tmp_path / "render.pdf")
    assert closed == [{"SaveChanges": 0}]
    events = _events(journal)
    failed = next(e for e in events if e["event"] == "render.word_export.failed")
    assert failed["source"] == str(source) and failed["error_type"] == "OSError"
    assert events[-1]["event"] == "render.word_close.succeeded"


def test_chain_stage_journals_share_run_and_preserve_distinct_batch_ids(tmp_path):
    source = _source(tmp_path)
    chain = chain_from_specs([("first", _spec()), ("second", _spec("GRC", "Global Risk Consultants"))])
    out = tmp_path / "chain"
    result = run_chain([str(source)], chain, out, workers=1, do_render=False)
    assert result.counts()["PASS"] == 1
    parent = _events(out / "logs" / "events.jsonl")
    stages = [e for e in parent if e["event"] == "chain.stage.succeeded"]
    assert [e["chain_stage"] for e in stages] == [1, 2]
    ids = []
    for i, stage in enumerate(result.stages, 1):
        events = _events(stage.out_dir / "logs" / "events.jsonl")
        assert all(e["run_id"] == parent[0]["run_id"] and e["chain_stage"] == i for e in events)
        ids.append(events[0]["batch_id"])
    assert len(set(ids)) == 2
    assert any(e["event"] == "chain.delivered" and e["source"] == str(source) for e in parent)


def test_failed_event_destination_does_not_change_batch_result(tmp_path):
    source = _source(tmp_path)
    out = tmp_path / "run"
    (out / "logs" / "events.jsonl").mkdir(parents=True)
    result = batch.run_batch([str(source)], "edit-spec", {}, out, workers=1, do_render=False, spec=_spec())
    assert result.files[0].status == "PASS"
    assert Path(result.files[0].delivered_to).is_file()


def test_slow_diagnostic_before_replace_cannot_bypass_final_source_guard(tmp_path, monkeypatch):
    source = tmp_path / "source.docx"
    source.write_bytes(b"original")
    identity = guard.acquire(source)
    original_event = guard.diag.event
    seen = []

    def concurrent_save(name, **fields):
        seen.append(name)
        if name == "guard.replace.started":
            source.write_bytes(b"an external save during diagnostic I/O")
        original_event(name, **fields)

    monkeypatch.setattr(guard.diag, "event", concurrent_save)
    with pytest.raises(guard.GuardError, match="changed since acquisition"):
        guard.write_back(identity, b"transformed output")
    assert source.read_bytes() == b"an external save during diagnostic I/O"
    assert "guard.replace.succeeded" not in seen
    assert not list(tmp_path.glob(".specsurgeon-*.tmp"))


def test_final_guard_has_no_diagnostic_io_before_atomic_replace(tmp_path, monkeypatch):
    source = tmp_path / "source.docx"
    source.write_bytes(b"original")
    identity = guard.acquire(source)
    transitions = []
    original_check = guard.check_before_write
    original_replace = guard.os.replace
    original_event = guard.diag.event

    def check(*args, **kwargs):
        result = original_check(*args, **kwargs)
        transitions.append("checked")
        return result

    def record(name, **fields):
        transitions.append(name)
        original_event(name, **fields)

    def replace(*args):
        assert transitions[-1] == "checked"
        transitions.append("replaced")
        return original_replace(*args)

    monkeypatch.setattr(guard, "check_before_write", check)
    monkeypatch.setattr(guard.diag, "event", record)
    monkeypatch.setattr(guard.os, "replace", replace)
    guard.write_back(identity, b"transformed output")
    assert source.read_bytes() == b"transformed output"
