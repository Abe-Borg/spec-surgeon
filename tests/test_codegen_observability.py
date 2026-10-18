"""Custom transformation diagnostics survive sandbox failure and temporary workspace cleanup."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from fake_claude import FakeClient, resp, tool
from make_fixtures import build
from spec_surgeon import codegen, diagnostics as diag
from test_codegen import GOOD_OP, UNASSERTED_OP


def events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def named(records, name):
    return [record for record in records if record["event"] == name]


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "input" / "21 13 13.docx"
    path.parent.mkdir()
    path.write_bytes(build("21 13 13", "SPRINKLER SYSTEMS"))
    return path


def test_sandbox_start_is_durable_before_timeout_without_code_or_environment(source, tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("PRIVATE_SANDBOX_ENV", "private-environment-value")
    child_stderr = b"private-child-stderr"

    def timeout(command, **kwargs):
        recorded = events(path)
        assert named(recorded, "codegen.sandbox.prepared")
        assert named(recorded, "codegen.sandbox.process.started")
        assert not named(recorded, "codegen.sandbox.exited")
        assert "PRIVATE_SANDBOX_ENV" not in kwargs["env"]
        raise subprocess.TimeoutExpired(command, kwargs["timeout"], stderr=child_stderr)

    monkeypatch.setattr(codegen.subprocess, "run", timeout)
    with diag.scope(path, action_id="action-timeout"):
        result = codegen.run_in_sandbox("private-generated-source", source, {},
                                       {"custom": "private-params"}, tmp_path / "sandbox", timeout=3)
    assert not result.ok and result.code == "timeout" and result.stderr == "private-child-stderr"
    recorded = events(path)
    failure = named(recorded, "codegen.sandbox.process.failed")[0]
    finish = named(recorded, "codegen.sandbox.succeeded")[0]
    assert failure["error_type"] == "TimeoutExpired" and failure["duration_ms"] >= 0
    assert failure["sandbox_id"] == finish["sandbox_id"] and failure["action_id"] == "action-timeout"
    assert finish["ok"] is False and finish["code"] == "timeout"
    assert named(recorded, "codegen.sandbox.timeout")[0]["timeout_seconds"] == 3
    assert "private-" not in path.read_text()


def test_abrupt_sandbox_exit_keeps_returncode_and_missing_result(source, tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    def abrupt_exit(cmd, **kwargs):
        kwargs["stdout"].write(b"private-stdout")
        kwargs["stderr"].write(b"private-stderr")
        return subprocess.CompletedProcess(cmd, 97)

    monkeypatch.setattr(codegen.subprocess, "run", abrupt_exit)
    with diag.scope(path):
        result = codegen.run_in_sandbox(GOOD_OP, source, {}, {"twips": 900}, tmp_path / "sandbox")
    assert not result.ok and result.code == "crash" and result.returncode == 97
    recorded = events(path)
    exited = named(recorded, "codegen.sandbox.exited")[0]
    assert exited["returncode"] == 97 and not exited["result_present"]
    assert exited["stderr_characters"] == len("private-stderr")
    assert named(recorded, "codegen.sandbox.result_missing")[0]["level"] == "error"
    assert named(recorded, "codegen.sandbox.process.succeeded")[0]["duration_ms"] >= 0
    assert "private-" not in path.read_text()


def test_sandbox_launch_error_preserves_original_exception(source, tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    failure = PermissionError("cannot launch the isolated Python process")

    def denied(*args, **kwargs):
        raise failure

    monkeypatch.setattr(codegen.subprocess, "run", denied)
    with diag.scope(path), pytest.raises(PermissionError) as caught:
        codegen.run_in_sandbox(GOOD_OP, source, {}, {"twips": 900}, tmp_path / "sandbox")
    assert caught.value is failure
    recorded = events(path)
    assert named(recorded, "codegen.sandbox.process.failed")[0]["error_type"] == "PermissionError"
    assert named(recorded, "codegen.sandbox.failed")


def test_generated_verification_failure_records_check_without_changing_source(source, tmp_path):
    path = tmp_path / "events.jsonl"
    before = source.read_bytes()
    with diag.scope(path):
        result = codegen.trial(UNASSERTED_OP, source, {}, {"twips": 900}, tmp_path / "sandbox")
    assert not result.passed and source.read_bytes() == before
    recorded = events(path)
    sandbox = named(recorded, "codegen.sandbox.succeeded")[0]
    verify = named(recorded, "codegen.trial.verify.succeeded")[0]
    trial = named(recorded, "codegen.trial.succeeded")[0]
    assert sandbox["ok"] and not verify["passed"] and verify["checks"]["V10"] == "FAIL"
    assert trial["failed_checks"] == ["V10"] and trial["problem_count"] >= 1
    assert sandbox["trial_id"] == trial["trial_id"] == verify["trial_id"]


def test_generation_attempts_and_generated_run_keep_persistent_journals(source, tmp_path):
    client = FakeClient(resp(tool("submit_op", {"source": "import os\n"}, "attempt-1")),
                        resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}}, "attempt-2")))
    folder = tmp_path / "codegen"
    before = source.read_bytes()
    generated = codegen.generate_op("private-request", "private-proposal", [source], folder, client=client)
    assert generated.passed
    recorded = events(folder / "logs/events.jsonl")
    attempts = named(recorded, "codegen.attempt.succeeded")
    assert [r["codegen_attempt"] for r in attempts] == [1, 2]
    assert [r["passed"] for r in attempts] == [False, True]
    assert named(recorded, "codegen.sandbox.process.started")[0]["codegen_attempt"] == 2
    assert "private-" not in (folder / "logs/events.jsonl").read_text()

    result = codegen.run_generated(generated, [str(source)], tmp_path / "run")
    assert result[0].passed and result[0].output.is_file() and source.read_bytes() == before
    recorded = events(tmp_path / "run/logs/events.jsonl")
    sandbox = named(recorded, "codegen.sandbox.started")[0]
    assert not Path(sandbox["workdir"]).exists()
    assert named(recorded, "codegen.run.succeeded")[0]["passed"] == 1
    assert named(recorded, "codegen.sandbox.process.succeeded")[0]["returncode"] == 0


def test_generated_run_refuses_source_inside_output_before_creating_logs(source):
    with pytest.raises(ValueError):
        codegen.run_generated(codegen.GeneratedOp(source=GOOD_OP, params={"twips": 900}),
                              [str(source)], source.parent)
    assert not (source.parent / "logs").exists()
