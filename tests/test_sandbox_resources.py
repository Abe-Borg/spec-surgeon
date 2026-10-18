"""Resource failures are contained in the generated subprocess, with bounded diagnostics."""
import json
import subprocess
import sys

import pytest

from make_fixtures import build
from spec_surgeon import codegen
from test_codegen import GOOD_OP


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source.docx"
    path.write_bytes(build("21 00 00", "FIRE SUPPRESSION"))
    return path


@pytest.mark.skipif(not sys.platform.startswith("linux") and sys.platform != "win32", reason="OS memory isolation required")
def test_generated_allocation_fails_inside_the_process_memory_ceiling(source, tmp_path):
    source_code = GOOD_OP.replace('twips = params.get("twips")',
                                 'allocation = "x" * (256 * 1024 * 1024)\n        twips = params.get("twips")')
    result = codegen.run_in_sandbox(source_code, source, {}, {"twips": 900}, tmp_path / "sandbox",
                                    memory_mb=128, timeout=10)
    assert not result.ok and result.code == "resource-limit"
    assert "MemoryError" in result.error


def test_large_print_is_refused_without_buffering_output_in_the_parent(source, tmp_path):
    source_code = GOOD_OP.replace('twips = params.get("twips")',
                                 'print("x" * 1048576)\n        twips = params.get("twips")')
    folder = tmp_path / "sandbox"
    result = codegen.run_in_sandbox(source_code, source, {}, {"twips": 900}, folder, log_bytes=65536)
    assert not result.ok and result.code == "resource-limit"
    assert "diagnostics" in result.error
    assert (folder / "sandbox-stdout.txt").stat().st_size <= 65536
    assert not (folder / "out.docx").exists()


def test_document_output_limit_returns_a_structured_failure(source, tmp_path):
    result = codegen.run_in_sandbox(GOOD_OP, source, {}, {"twips": 900}, tmp_path / "sandbox", output_bytes=1024)
    assert not result.ok and result.code == "resource-limit"
    assert "document" in result.error


def test_success_records_actual_os_enforcement(source, tmp_path):
    folder = tmp_path / "sandbox"
    result = codegen.run_in_sandbox(GOOD_OP, source, {}, {"twips": 900}, folder)
    assert result.ok
    isolation = json.loads((folder / "result.json").read_text())["isolation"]
    assert isolation["memory_bytes"] == 2048 * 1024 * 1024
    assert isolation["wall_timeout"] == "parent"
    if sys.platform.startswith("linux"):
        assert isolation["file_bytes"] == codegen.DEFAULT_OUTPUT_BYTES
    else:
        assert "no OS file-size ceiling" in isolation["file_limit"]


def test_subprocess_diagnostics_use_files_instead_of_captured_pipes(source, tmp_path, monkeypatch):
    original = codegen.subprocess.run
    def run(command, **kwargs):
        assert "capture_output" not in kwargs
        assert kwargs["stdout"].fileno() >= 0 and kwargs["stderr"].fileno() >= 0
        return original(command, **kwargs)
    monkeypatch.setattr(codegen.subprocess, "run", run)
    assert codegen.run_in_sandbox(GOOD_OP, source, {}, {"twips": 900}, tmp_path / "sandbox").ok


def test_abrupt_failure_in_reused_folder_cannot_reuse_an_old_success(source, tmp_path, monkeypatch):
    folder = tmp_path / "sandbox"
    assert codegen.run_in_sandbox(GOOD_OP, source, {}, {"twips": 900}, folder).ok
    monkeypatch.setattr(codegen.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 9))
    failed = codegen.run_in_sandbox(GOOD_OP, source, {}, {"twips": 800}, folder)
    assert not failed.ok and failed.code == "crash"
    assert failed.output is None and not (folder / "result.json").exists()


def test_large_exception_is_bounded_before_writing_the_result_artifact(source, tmp_path):
    code = GOOD_OP.replace('twips = params.get("twips")',
                           'raise ValueError("x" * 70000)\n        twips = params.get("twips")')
    folder = tmp_path / "sandbox"
    result = codegen.run_in_sandbox(code, source, {}, {"twips": 900}, folder, output_bytes=1024)
    assert not result.ok and result.error.startswith("ValueError:")
    assert len(result.error) < 2100
    assert (folder / "result.json").stat().st_size <= codegen.MAX_RESULT_BYTES
    assert json.loads((folder / "result.json").read_text())["ok"] is False


@pytest.mark.parametrize("result_bytes,plan_bytes,has_output", [
    (b'{"ok":', b'{}', True),
    (b'[]', b'{}', True),
    (b'\xff', b'{}', True),
    (b'{"ok":true}', b'{}', False),
    (b'{"ok":true}', None, True),
    (b'{"ok":true}', b'{', True),
    (b'{"ok":true}', b'[' * 1100 + b'0' + b']' * 1100, True),
])
def test_missing_or_malformed_sandbox_artifacts_are_structured_failures(
        source, tmp_path, monkeypatch, result_bytes, plan_bytes, has_output):
    folder = tmp_path / "sandbox"
    def broken_process(command, **kwargs):
        (folder / "result.json").write_bytes(result_bytes)
        if plan_bytes is not None:
            (folder / "plan.json").write_bytes(plan_bytes)
        if has_output:
            (folder / "out.docx").write_bytes(b"candidate")
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(codegen.subprocess, "run", broken_process)
    result = codegen.run_in_sandbox(GOOD_OP, source, {}, {"twips": 900}, folder)
    assert not result.ok and result.code == "crash"
    assert "missing or malformed" in result.error
