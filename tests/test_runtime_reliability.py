"""Archive fidelity, bounded intake, and explicit desktop-safe process creation."""
from __future__ import annotations

import contextlib
import faulthandler
import io
import importlib.util
import json
import struct
import sys
import zipfile
from copy import copy
from pathlib import Path
from types import SimpleNamespace
import types

import pytest

from make_fixtures import build
from spec_surgeon import batch, diagnostics as diag, render as render_mod
from spec_surgeon.batch import run_batch
from spec_surgeon.editspec import plan_spec
from spec_surgeon.package import DocxPackage
from spec_surgeon.package_limits import ArchiveLimitError, ArchiveLimits, check_archive
from spec_surgeon.render import libreoffice, word_com, word_worker
from spec_surgeon.verify.reader import VPackage
from spec_surgeon.verify import run_suite

REAL_WORD_AVAILABLE = word_com.available          # conftest hides every renderer from a test not marked render

CONTINUOUS = {"request": "Number the pages continuously", "summary": "Continuous page numbers", "mode": "formatting",
              "ops": [{"op": "fix_page_numbering", "params": {"option": "continuous"}}]}


def _archive(entries, comment=b""):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.comment = comment
        for name, data in entries:
            archive.writestr(name, data)
    return output.getvalue()


def _with_metadata(data):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as original, zipfile.ZipFile(output, "w") as result:
        result.comment = b"office archive comment"
        for number, info in enumerate(original.infolist()):
            info = copy(info)
            info.comment = f"member {number}".encode()
            info.extra = b"\xfe\xca\x04\x00data"
            info.internal_attr = 1
            result.writestr(info, original.read(info.filename))
    return output.getvalue()


@pytest.mark.parametrize("limits,entries", [
    (ArchiveLimits(max_members=1), [("first", b"1"), ("second", b"2")]),
    (ArchiveLimits(max_member_bytes=8), [("large", b"a" * 9)]),
    (ArchiveLimits(max_total_bytes=10), [("first", b"a" * 6), ("second", b"b" * 6)]),
    (ArchiveLimits(max_archive_bytes=10), [("first", b"1")]),
])
def test_archive_declared_limits_refuse_before_document_use(limits, entries):
    with pytest.raises(ArchiveLimitError):
        DocxPackage(_archive(entries), limits=limits)


def test_archive_stream_checks_real_counts_not_just_central_directory():
    requests = []

    class Stream(io.BytesIO):
        def read(self, count=-1):
            requests.append(count)
            return super().read(count)

    info = SimpleNamespace(filename="lied-about-size", file_size=4)
    archive = SimpleNamespace(infolist=lambda: [info], open=lambda entry: Stream(b"a" * 20))
    with pytest.raises(ArchiveLimitError, match="expansion exceeds"):
        check_archive(archive, ArchiveLimits(max_member_bytes=8))
    assert requests == [9]


def test_archive_member_count_is_bounded_before_zipinfo_allocation(monkeypatch):
    import spec_surgeon.package as package_module
    data = bytearray(_archive([("a", b"1"), ("b", b"2")]))
    end = data.rfind(b"PK\x05\x06")
    # A dishonest end record cannot hide the actual central-directory record count.
    struct.pack_into("<HH", data, end + 8, 1, 1)
    monkeypatch.setattr(package_module.zipfile, "ZipFile", lambda *args: pytest.fail("metadata should be bounded first"))
    with pytest.raises(ArchiveLimitError, match="central directory"):
        DocxPackage(bytes(data), limits=ArchiveLimits(max_members=1))


def test_small_zip64_archive_and_prepended_stub_keep_compatible_intake():
    data = _archive([("body", b"text"), ("metadata", b"kept")], comment=b"office")
    end = data.rfind(b"PK\x05\x06")
    original = struct.unpack_from("<4s4H2LH", data, end)
    extended = struct.pack("<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0,
                           original[3], original[4], original[5], original[6])
    locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, end, 1)
    classic = bytearray(data[end:])
    struct.pack_into("<HHLL", classic, 8, 65535, 65535, 0xFFFFFFFF, 0xFFFFFFFF)
    zip64 = data[:end] + extended + locator + bytes(classic)
    for source in (zip64, b"inert archive prefix" + zip64):
        package = DocxPackage(source, limits=ArchiveLimits(max_members=2))
        assert package.read("body") == b"text" and package.repack() == source


def test_archive_checks_actual_aggregate_and_mismatched_declarations():
    infos = [SimpleNamespace(filename="a", file_size=4), SimpleNamespace(filename="b", file_size=4)]
    archive = SimpleNamespace(infolist=lambda: infos, open=lambda entry: io.BytesIO(b"a" * 4))
    check_archive(archive, ArchiveLimits(max_total_bytes=8))  # exact bound is accepted
    archive.open = lambda entry: io.BytesIO(b"a" * 3)
    with pytest.raises(ArchiveLimitError, match="not its declared"):
        check_archive(archive, ArchiveLimits())
    archive.open = lambda entry: io.BytesIO(b"a" * (4 if entry.filename == "a" else 6))
    with pytest.raises(ArchiveLimitError, match="expansion exceeds"):
        check_archive(archive, ArchiveLimits(max_total_bytes=8))


def test_archive_limits_environment_and_copy(monkeypatch):
    monkeypatch.setenv("SPEC_SURGEON_ARCHIVE_MAX_MEMBER_BYTES", "3")
    with pytest.raises(ArchiveLimitError):
        DocxPackage(_archive([("odd/../name", b"aaaa")]))
    limits = ArchiveLimits(max_member_bytes=4)
    package = DocxPackage(_archive([("odd/../name", b"aaaa")]), limits=limits)
    assert package.member_names == ["odd/../name"]  # never extract or normalize names
    assert package.copy().limits == limits
    monkeypatch.setenv("SPEC_SURGEON_ARCHIVE_MAX_MEMBER_BYTES", "invalid")
    with pytest.raises(ValueError, match="positive integer"):
        DocxPackage(_archive([]))


def test_archive_limit_is_a_per_file_refusal_without_source_mutation(tmp_path, monkeypatch):
    source = tmp_path / "oversized.docx"
    data = build("21 13 13", "WET-PIPE")
    source.write_bytes(data)
    monkeypatch.setenv("SPEC_SURGEON_ARCHIVE_MAX_MEMBERS", "1")
    result = batch.run_batch([str(source)], "edit-spec", {}, tmp_path / "results", workers=1, do_render=False,
                             spec=CONTINUOUS)
    assert result.files[0].exception["code"] == "archive-limit"
    assert source.read_bytes() == data and result.files[0].delivered_to is None


def test_noop_repack_and_equal_substitutions_keep_entire_archive_bytes():
    data = _with_metadata(build("21 13 13", "WET-PIPE"))
    package = DocxPackage(data)
    assert package.repack() is data
    assert package.repack({"word/document.xml": package.read("word/document.xml")}) is data


def test_repack_keeps_archive_metadata_and_distinct_duplicate_inert_members():
    with pytest.warns(UserWarning, match="Duplicate name"):
        data = _archive([("main", b"original"), ("[trash]/same.dat", b"first"),
                         ("[trash]/same.dat", b"second")], comment=b"comment")
    package = DocxPackage(data)
    with pytest.warns(UserWarning, match="Duplicate name"):
        output = package.repack({"main": b"changed"})
    with zipfile.ZipFile(io.BytesIO(output)) as archive:
        assert archive.comment == b"comment"
        assert [archive.read(info) for info in archive.infolist()] == [b"changed", b"first", b"second"]


def test_metadata_survives_changes_and_independent_reader_catches_dropped_comments():
    data = _with_metadata(build("21 13 13", "WET-PIPE"))
    package = DocxPackage(data)
    output = package.repack({"word/document.xml": package.read("word/document.xml") + b"\n"})
    assert VPackage(data).metadata_changes(VPackage(output)) == []
    damaged = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(output)) as source, zipfile.ZipFile(damaged, "w") as destination:
        for info in source.infolist():
            info = copy(info)
            info.comment = b""
            destination.writestr(info, source.read(info.filename))
    changes = VPackage(data).metadata_changes(VPackage(damaged.getvalue()))
    assert "archive comment" in changes
    assert any("word/document.xml: comment" == change for change in changes)


def test_v1_fails_metadata_damage_even_when_member_bytes_are_unchanged():
    source = _with_metadata(build("21 13 13", "WET-PIPE"))
    package = DocxPackage(source)
    spec = {"request": "Use Arial", "summary": "Apply Arial", "mode": "formatting",
            "ops": [{"op": "set_font", "select": {"scope": ["document"]},
                                             "params": {"font": "Arial"}}]}
    damaged = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source)) as original, zipfile.ZipFile(damaged, "w") as changed:
        for info in original.infolist():
            changed.writestr(info, original.read(info))
    # Use the existing plan, but no mutation: absent requested font would independently fail V10 too.
    plan, _ = plan_spec(package, spec)
    record = run_suite(source, damaged.getvalue(), plan.to_dict(), "source.docx", "damaged.docx")
    v1 = next(check for check in record.checks if check.id == "V1")
    assert v1.status == "FAIL" and "archive comment" in v1.data["metadata_changes"]


def test_both_batch_pools_use_spawn_even_when_frozen(monkeypatch):
    calls = []
    context = object()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(batch.multiprocessing, "get_context", lambda method: calls.append(method) or context)
    monkeypatch.setattr(batch, "ProcessPoolExecutor", lambda **kwargs: kwargs)
    assert batch._process_pool(3) == {"max_workers": 3, "mp_context": context}
    assert batch._process_pool(1) == {"max_workers": 1, "mp_context": context}
    assert calls == ["spawn", "spawn"]


@pytest.mark.parametrize("worker_mode,frozen", [("thread", False), ("serial", False), ("auto", True)])
def test_thread_serial_and_a_small_auto_batch_start_no_process(tmp_path, monkeypatch, worker_mode, frozen):
    def unexpected_pool(*args, **kwargs):
        pytest.fail("these worker modes must not start a process pool")

    monkeypatch.setattr(batch, "_process_pool", unexpected_pool)
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    if worker_mode in ("serial", "auto"):                   # two files: auto runs them inline
        monkeypatch.setattr(batch, "ThreadPoolExecutor", unexpected_pool)
    sources = []
    for index in range(2):
        source = tmp_path / f"spec-{index}.docx"
        source.write_bytes(build("21 13 13", f"WET-PIPE {index}"))
        sources.append(str(source))
    options = {} if worker_mode == "thread" else {"worker_mode": worker_mode}
    result = batch.run_batch(sources, "edit-spec", {}, tmp_path / "run", workers=0 if worker_mode == "auto" else 2,
                             do_render=False, spec=CONTINUOUS, **options)
    assert len(result.files) == 2 and all(file.output and file.output.exists() for file in result.files)


def test_the_worker_plan_shares_three_or_more_files_over_processes_in_the_packaged_app_too(monkeypatch):
    monkeypatch.setattr(batch.os, "cpu_count", lambda: 4)
    for frozen in (False, True):                            # the packaged app no longer swaps processes for threads
        monkeypatch.setattr(sys, "frozen", frozen, raising=False)
        assert batch.worker_plan("auto", 0, 1) == ("serial", 1)
        assert batch.worker_plan("auto", 0, 2) == ("serial", 1)
        assert batch.worker_plan("auto", 0, 3) == ("process", 3)
        assert batch.worker_plan("auto", 0, 40) == ("process", 4)            # one per core
        assert batch.worker_plan("auto", 1, 40) == ("serial", 1)             # one asked for: inline
        assert batch.worker_plan("auto", 2, 40) == ("process", 2)
        assert batch.worker_plan("process", 0, 2) == ("process", 2)
        assert batch.worker_plan("thread", 0, 40) == ("thread", 1)
        assert batch.worker_plan("serial", 6, 40) == ("serial", 1)
    monkeypatch.setattr(batch.os, "cpu_count", lambda: 64)
    assert batch.worker_plan("auto", 0, 40) == ("process", batch.MAX_PROCESS_WORKERS)
    monkeypatch.setattr(batch, "DEFAULT_WORKER_MODE", "auto")
    assert batch.worker_plan(None, 0, 5)[0] == "process"                     # None is the module's default
    with pytest.raises(ValueError, match="auto, thread, serial or process"):
        batch.worker_plan("fibers", 0, 5)


def test_an_auto_batch_of_three_runs_in_worker_processes_with_the_same_results_as_inline(tmp_path, monkeypatch):
    """The window's default for three or more files: real spawned workers, and every output byte and every check's
    status the same as the same batch run inline."""
    sources = []
    for index in range(3):
        source = tmp_path / "in" / f"spec-{index}.docx"
        source.parent.mkdir(exist_ok=True)
        source.write_bytes(build("21 13 13", f"WET-PIPE {index}"))
        sources.append(str(source))
    monkeypatch.setattr(batch, "DEFAULT_WORKER_MODE", "auto")
    parallel = batch.run_batch(sources, "edit-spec", {}, tmp_path / "auto", do_render=False, spec=CONTINUOUS)
    log = (tmp_path / "auto" / "logs" / "run.log").read_text(encoding="utf-8")
    assert "process worker(s)" in log
    inline = batch.run_batch(sources, "edit-spec", {}, tmp_path / "serial", do_render=False, spec=CONTINUOUS,
                             worker_mode="serial")
    assert [f.source for f in parallel.files] == [f.source for f in inline.files]      # the sources' order
    for a, b in zip(parallel.files, inline.files):
        assert a.record.passed and b.record.passed
        assert a.output.read_bytes() == b.output.read_bytes()
        assert [(c.id, c.status) for c in a.record.checks] == [(c.id, c.status) for c in b.record.checks]


def test_spawning_workers_keeps_handled_start_up_exceptions_out_of_the_crash_log_even_side_by_side(tmp_path):
    """Several threads start processes at once (Word instances, transform workers): the crash log is paused while any
    of them is starting one and watches again once the last has."""
    import threading
    was_enabled = faulthandler.is_enabled()
    watch = diag.CrashWatch(tmp_path / "logs")
    try:
        watch.start()
        first_in, second_in, first_may_leave = threading.Event(), threading.Event(), threading.Event()
        seen = []

        def first():
            with diag.com_quiet():
                first_in.set()
                second_in.wait(5)
                first_may_leave.wait(5)
            seen.append(("first left", faulthandler.is_enabled()))

        worker = threading.Thread(target=first)
        worker.start()
        assert first_in.wait(5)
        with diag.com_quiet():
            second_in.set()
            seen.append(("both in", faulthandler.is_enabled()))
            first_may_leave.set()
            worker.join(5)
            seen.append(("second still in", faulthandler.is_enabled()))
        assert seen == [("both in", False), ("first left", False), ("second still in", False)]
        assert faulthandler.is_enabled()                    # the last one out resumes the crash log
    finally:
        watch.stop()
        if was_enabled:
            faulthandler.enable()


def test_thread_mode_defaults_to_one_worker_and_honors_an_explicit_count(tmp_path, monkeypatch):
    # The work holds the GIL: a default thread pool only added memory and time, so none starts unless asked for.
    pools = []
    real_pool = batch.ThreadPoolExecutor
    monkeypatch.setattr(batch, "ThreadPoolExecutor", lambda **kwargs: pools.append(kwargs) or real_pool(**kwargs))
    sources = []
    for index in range(3):
        source = tmp_path / f"spec-{index}.docx"
        source.write_bytes(build("21 13 13", f"WET-PIPE {index}"))
        sources.append(str(source))
    default = batch.run_batch(sources, "edit-spec", {}, tmp_path / "default", do_render=False, spec=CONTINUOUS)
    assert pools == [] and all(file.output and file.output.exists() for file in default.files)
    assert "with 1 thread worker(s)" in (tmp_path / "default" / "logs" / "run.log").read_text(encoding="utf-8")
    explicit = batch.run_batch(sources, "edit-spec", {}, tmp_path / "explicit", workers=2, do_render=False,
                               spec=CONTINUOUS)
    assert pools == [{"max_workers": 2}] and all(file.output and file.output.exists() for file in explicit.files)


def test_frozen_launcher_handles_spawn_before_app_mutex_or_ui(monkeypatch):
    from spec_surgeon import winapp
    path = Path(__file__).resolve().parents[1] / "packaging/windows/launcher_bootstrap.py"
    module_spec = importlib.util.spec_from_file_location("_runtime_spawn_launcher", path)
    launcher = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(launcher)

    def worker_exit():
        raise SystemExit(0)  # freeze_support owns the child and never opens a window

    monkeypatch.setattr(launcher.multiprocessing, "freeze_support", worker_exit)
    monkeypatch.setattr(winapp, "hold_app_mutex", lambda: pytest.fail("worker must not acquire the app mutex"))
    with pytest.raises(SystemExit) as result:
        launcher.prepare()
    assert result.value.code == 0


def _fake_worker_context(monkeypatch, *, messages=None, result=None, alive=False, pdf=b"PDF", exitcode=0):
    controls = SimpleNamespace(terminated=False, killed=False, closed=False, opened=[], cleanup=[],
                               fault_handler_at_start=[])
    pending = list(messages if messages is not None else [{"kind": "finished"}])
    receive = SimpleNamespace(poll=lambda timeout: bool(pending), recv=lambda: pending.pop(0), close=lambda: None)
    send = SimpleNamespace(close=lambda: None)

    class Process:
        def __init__(self, target, args, name):
            self.request = args[1]
            self.exitcode = exitcode
            self.alive = alive

        def start(self):
            controls.fault_handler_at_start.append(faulthandler.is_enabled())
            folder = Path(self.request["scratch"])
            (folder / "result.json").write_text(json.dumps(result or {"ok": True, "version": "mock"}))
            if pdf is not None:
                (folder / "render.pdf").write_bytes(pdf)

        def is_alive(self):
            return self.alive

        def join(self, timeout):
            pass

        def terminate(self):
            controls.terminated = True
            self.alive = False

        def kill(self):
            controls.killed = True
            self.alive = False

        def close(self):
            controls.closed = True

    context = SimpleNamespace(Pipe=lambda duplex: (receive, send), Process=Process)
    monkeypatch.setattr(word_worker.multiprocessing, "get_context", lambda method: context if method == "spawn" else None)
    monkeypatch.setattr(word_worker, "_open_private_handle", lambda identity: controls.opened.append(identity) or "private")
    monkeypatch.setattr(word_worker, "_close_private_handle", lambda handle, **kwargs: controls.cleanup.append((handle, kwargs)))
    return controls


def test_the_crash_log_is_paused_while_the_word_worker_starts(tmp_path, monkeypatch):
    """Starting the worker raised a handled access violation inside CreateProcess on every spawn of the 1.8.0 live
    run, and the window's crash log took each for a crash (68 of them). The log is paused for the start alone."""
    was_enabled = faulthandler.is_enabled()
    watch = diag.CrashWatch(tmp_path / "logs")
    try:
        watch.start()
        controls = _fake_worker_context(monkeypatch)
        word_worker.run_word("render", tmp_path / "source.docx", pdf=tmp_path / "output.pdf", timeout=1)
        controls_batch = _fake_worker_context(monkeypatch, messages=[{"kind": "started", "identity": None},
                                                                     {"kind": "finished"}])
        word_worker.run_word_batch([tmp_path / "one.docx"], tmp_path / "pdf", timeout_per_file=1)
        assert controls.fault_handler_at_start == controls_batch.fault_handler_at_start == [False]
        assert faulthandler.is_enabled()                       # watching again once the worker is running
    finally:
        watch.stop()
        if was_enabled:
            faulthandler.enable()


def test_word_worker_success_publishes_only_completed_pdf(tmp_path, monkeypatch):
    controls = _fake_worker_context(monkeypatch)
    destination = tmp_path / "output.pdf"
    result = word_worker.run_word("render", tmp_path / "source.docx", pdf=destination, timeout=1)
    assert result["version"] == "mock" and destination.read_bytes() == b"PDF"
    assert controls.closed and not controls.terminated


def test_word_timeout_terminates_worker_and_only_pinned_private_word(tmp_path, monkeypatch):
    controls = _fake_worker_context(monkeypatch, messages=[{"kind": "started", "identity": {"pid": 42, "created": "time"}}],
                                    alive=True)
    ticks = iter([0, 0.1, 2])
    monkeypatch.setattr(word_worker.time, "monotonic", lambda: next(ticks))
    with pytest.raises(word_worker.WordWorkerError) as caught:
        word_worker.run_word("render", tmp_path / "source.docx", pdf=tmp_path / "output.pdf", timeout=1)
    assert caught.value.code == "word-timeout"
    assert controls.terminated and controls.cleanup == [("private", {"terminate": True})]
    assert not (tmp_path / "output.pdf").exists()


def test_word_timeout_without_ownership_never_selects_another_word_process(tmp_path, monkeypatch):
    controls = _fake_worker_context(monkeypatch, messages=[], alive=True)
    ticks = iter([0, 2])
    monkeypatch.setattr(word_worker.time, "monotonic", lambda: next(ticks))
    with pytest.raises(word_worker.WordWorkerError) as caught:
        word_worker.run_word("accept_reject", tmp_path / "source.docx", timeout=1)
    assert not caught.value.details["private_cleanup_available"]
    assert controls.opened == [] and controls.cleanup == [(None, {"terminate": True})]


@pytest.mark.parametrize("result,pdf,code", [
    ({"ok": False, "code": "word-com", "reason": "COM error 0x80020009", "stage": "word_export"}, b"partial", "word-com"),
    ({"ok": True}, None, "word-no-pdf"),
])
def test_word_failed_or_missing_result_cannot_publish_partial_pdf(tmp_path, monkeypatch, result, pdf, code):
    _fake_worker_context(monkeypatch, result=result, pdf=pdf)
    with pytest.raises(word_worker.WordWorkerError) as caught:
        word_worker.run_word("render", tmp_path / "source.docx", pdf=tmp_path / "output.pdf", timeout=1)
    assert caught.value.code == code
    assert not (tmp_path / "output.pdf").exists()


def test_word_render_discards_stale_pdf_and_continues_after_timeout(tmp_path, monkeypatch):
    first, second = tmp_path / "one.docx", tmp_path / "two.docx"
    out = tmp_path / "pdf"
    out.mkdir()
    (out / "one.pdf").write_bytes(b"stale")
    asked = []

    def run_batch(files, out_dir, timeout_per_file=None):
        asked.append((list(files), out_dir, timeout_per_file))
        assert not (out / "one.pdf").exists()           # a PDF from an earlier run is gone before Word starts
        batch = word_worker.WordBatchResult(version="mock", workers=2)
        batch.results[first] = word_worker.WordWorkerError("word-timeout", "timed out", {"timeout_seconds": timeout_per_file})
        (out_dir / "two.pdf").write_bytes(b"fresh")
        batch.results[second] = out_dir / "two.pdf"
        batch.seconds[second] = 0.5
        return batch

    monkeypatch.setattr(word_worker, "run_word_batch", run_batch)
    errors, details = [], []
    assert word_com.render_pdfs([first, second], out, log=errors.append, detail=details.append,
                                timeout=2) == {second: out / "two.pdf"}
    assert asked == [([first, second], out, 2)]         # one batch call for every file, with the per-file deadline
    assert not (out / "one.pdf").exists() and "timed out" in errors[0] and errors[0].startswith("one.docx:")
    assert details == ["Word mock: two.docx → two.pdf in 0.5s"]


def test_word_render_raises_only_when_word_never_started_and_nothing_rendered(tmp_path, monkeypatch):
    first, second = tmp_path / "one.docx", tmp_path / "two.docx"
    out = tmp_path / "pdf"
    start = word_worker.WordWorkerError("word-com", "DispatchEx failed", {"stage": "word_start"})

    def never_starts(files, out_dir, timeout_per_file=None):
        return word_worker.WordBatchResult(results={f: start for f in files}, workers=1, start_error=start)

    monkeypatch.setattr(word_worker, "run_word_batch", never_starts)
    with pytest.raises(word_worker.WordWorkerError) as caught:
        word_com.render_pdfs([first, second], out)
    assert caught.value.details["stage"] == "word_start"

    def starts_then_dies(files, out_dir, timeout_per_file=None):
        # the first worker rendered one.docx; the fresh worker for the rest could not start: a per-file failure,
        # never a raise that would lose the PDF already made
        (out_dir / "one.pdf").write_bytes(b"%PDF")
        return word_worker.WordBatchResult(results={first: out_dir / "one.pdf", second: start}, workers=2)

    monkeypatch.setattr(word_worker, "run_word_batch", starts_then_dies)
    errors = []
    assert word_com.render_pdfs([first, second], out, log=errors.append) == {first: out / "one.pdf"}
    assert errors == ["two.docx: Word could not render it: DispatchEx failed"]


class _WordApp:
    """Word's Application as pywin32 shows it: a Caption, and no Hwnd (asking raises what the 1.8.0 live run logged)."""

    def __init__(self):
        self.Caption = "Word"
        self.captions = []

    def __setattr__(self, name, value):
        if name == "Caption" and hasattr(self, "captions"):
            self.captions.append(value)
        object.__setattr__(self, name, value)

    def __getattr__(self, name):
        raise AttributeError(f"Word.Application.{name}")


def _win32(monkeypatch, *, windows=None, processes=(), images=None, opened=None):
    """Fake pywin32: ``windows`` maps (class, caption) to a window's PID, ``processes`` is what EnumProcesses lists,
    ``images`` maps a PID to its executable."""
    windows = {} if windows is None else windows
    images = images or {}
    opened = [] if opened is None else opened
    handles = {}

    def find_window(cls, caption):
        pid = windows.get((cls, caption))
        if pid is not None:
            handles[1000 + pid] = pid
            return 1000 + pid
        return 0

    def open_process(access, inherit, pid):
        opened.append(pid)
        return SimpleNamespace(pid=pid, Close=lambda: None)

    monkeypatch.setitem(sys.modules, "win32gui", SimpleNamespace(FindWindow=find_window))
    monkeypatch.setitem(sys.modules, "win32api", SimpleNamespace(OpenProcess=open_process))
    monkeypatch.setitem(sys.modules, "win32process", SimpleNamespace(
        GetWindowThreadProcessId=lambda hwnd: (1, handles[hwnd]),
        EnumProcesses=lambda: list(processes),
        GetModuleFileNameEx=lambda handle, module: images.get(handle.pid, r"C:\Windows\System32\svchost.exe"),
        GetProcessTimes=lambda handle: {"CreationTime": SimpleNamespace(isoformat=lambda: f"born {handle.pid}")}))
    return opened


def test_word_process_identity_comes_from_the_instances_own_window(monkeypatch):
    """Word's Application has no Hwnd: the old lookup (``app.Hwnd``) raised on every worker of the live run, so a hung
    Word could never be ended. The instance's main window is found by a caption only it carries, which is then put
    back."""
    app = _WordApp()
    with pytest.raises(AttributeError, match="Word.Application.Hwnd"):
        app.Hwnd                                           # what the old lookup read
    marker = {}

    class Windows(dict):
        def get(self, key, default=None):
            cls, caption = key
            marker["caption"] = caption
            return 77 if cls == word_worker.WORD_WINDOW_CLASS and caption == app.Caption else default

    _win32(monkeypatch, windows=Windows())
    assert word_worker._private_identity(app, {1, 2}) == {"pid": 77, "created": "born 77", "found_by": "window"}
    assert app.captions == [marker["caption"], "Word"] and marker["caption"].startswith("Spec Surgeon ")
    assert app.Caption == "Word"


def test_word_process_identity_never_adopts_an_existing_pid(monkeypatch):
    app = _WordApp()

    class Windows(dict):
        def get(self, key, default=None):
            return 42 if key[1] == app.Caption else default

    opened = _win32(monkeypatch, windows=Windows(), processes=[42, 99], images={99: r"C:\Office\WINWORD.EXE"})
    assert word_worker._private_identity(app, {42}) is None     # the window is an instance that was already running
    assert opened == []


def test_word_process_identity_falls_back_to_the_one_new_word_process(monkeypatch):
    word = r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE"
    _win32(monkeypatch, processes=[1, 2, 77, 88], images={77: word, 88: r"C:\Windows\notepad.exe", 2: word})
    assert word_worker._private_identity(_WordApp(), {1, 2}) == {"pid": 77, "created": "born 77",
                                                                  "found_by": "new process"}
    # two Word processes started since (the user opened Word at the same moment): neither is adopted
    _win32(monkeypatch, processes=[1, 2, 77, 78], images={77: word, 78: word})
    assert word_worker._private_identity(_WordApp(), {1, 2}) is None
    # no new Word at all (DispatchEx reached an instance that was already running)
    _win32(monkeypatch, processes=[1, 2, 88], images={2: word})
    assert word_worker._private_identity(_WordApp(), {1, 2}) is None


def test_word_process_handle_creation_time_prevents_pid_reuse(monkeypatch):
    closed = []
    handle = SimpleNamespace(Close=lambda: closed.append(True))
    monkeypatch.setitem(sys.modules, "win32api", SimpleNamespace(OpenProcess=lambda *args: handle))
    monkeypatch.setitem(sys.modules, "win32process", SimpleNamespace(
        GetProcessTimes=lambda handle: {"CreationTime": SimpleNamespace(isoformat=lambda: "different process")}))
    assert word_worker._open_private_handle({"pid": 42, "created": "original process"}) is None
    assert closed == [True]


# ---- Word detection and renderer failures ----------------------------------------------------------------------------

def _spec(mode, ops):
    return {"request": "x", "summary": "x", "assumptions": [], "questions": [], "applies_to": {"or": "all"},
            "mode": mode, "ops": ops}


REPLACE = _spec("content", [{"op": "replace_text", "params": {"find": "FM Global", "replace": "FM Approved"}}])


def test_word_is_picked_only_when_it_is_installed(monkeypatch):
    monkeypatch.setattr(word_com, "available", REAL_WORD_AVAILABLE)      # the check itself is what is tested here
    monkeypatch.setattr(sys, "platform", "win32")
    for name in ("win32com", "win32com.client", "pythoncom"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))

    class NoWord:                                   # pywin32 present (the installer bundles it), Word absent
        HKEY_CLASSES_ROOT = 0

        @staticmethod
        def OpenKey(root, key):
            raise OSError("key not found")
    monkeypatch.setitem(sys.modules, "winreg", NoWord)
    monkeypatch.setattr(libreoffice, "find_soffice", lambda: None)
    assert word_com.available() is False
    assert render_mod.pick("auto") is None and render_mod.pick("word") is None

    class Word(NoWord):
        @staticmethod
        def OpenKey(root, key):
            assert key == word_com.WORD_PROGID_KEY
            return contextlib.nullcontext()
    monkeypatch.setitem(sys.modules, "winreg", Word)
    assert word_com.available() is True and render_mod.pick("auto") == "word"


def test_a_renderer_failure_is_this_files_failed_render_never_a_crashed_batch(tmp_path, monkeypatch):
    msgs = []
    assert render_mod.render_pdfs([tmp_path / "x.docx"], tmp_path / "pdf", "word", log=msgs.append) == {}
    assert msgs and "rendering with word failed" in msgs[0]

    def convert(soffice, files, out_dir, profile_dir, timeout):      # soffice dies on one file of a chunk
        if any(f.name == "bad.docx" for f in files):
            raise RuntimeError("soffice crashed")
        for f in files:
            (out_dir / (f.stem + ".pdf")).write_bytes(b"%PDF")
    monkeypatch.setattr(libreoffice, "find_soffice", lambda: "soffice")
    monkeypatch.setattr(libreoffice, "_convert_batch", convert)
    good, bad = tmp_path / "good.docx", tmp_path / "bad.docx"
    good.write_bytes(b"x")
    bad.write_bytes(b"x")
    msgs = []
    out = render_mod.render_pdfs([good, bad], tmp_path / "pdf2", "libreoffice", workers=1, log=msgs.append)
    assert set(out) == {good} and any("bad.docx" in m for m in msgs)       # one bad file costs its own PDF only

    f = tmp_path / "A.docx"
    f.write_bytes(build("21 00 20", "OSCAR"))

    def always_fails(*a, **k):
        raise RuntimeError("soffice crashed")
    monkeypatch.setattr(libreoffice, "_convert_batch", always_fails)
    b = run_batch([str(f)], "edit-spec", {}, tmp_path / "out", workers=1, renderer="libreoffice", do_render=True,
                  spec=REPLACE)
    fr = b.files[0]
    assert b.renderer == "libreoffice" and fr.status == "FAIL"
    assert fr.record.get("V6").status == "FAIL" and "render failed" in fr.record.get("V6").summary
    assert (tmp_path / "out" / "failed" / "A.docx").is_file() and (tmp_path / "out" / "BATCH_REPORT.md").is_file()


def test_the_preview_plans_and_checks_its_samples_side_by_side_with_the_same_results(tmp_path, monkeypatch):
    """The window's default: a preview of many files plans them in worker processes (planner.PLAN_PROCESS_MIN_FILES)
    and runs three or more samples side by side — every plan, verdict, check and sample byte as when run here, each
    sample's events in its own journal."""
    from spec_surgeon import planner, preview
    sources = []
    for index in range(planner.PLAN_PROCESS_MIN_FILES):
        source = tmp_path / "in" / f"spec-{index:02d}.docx"
        source.parent.mkdir(exist_ok=True)
        source.write_bytes(build("21 13 13", f"WET-PIPE {index}"))
        sources.append(str(source))
    spec = {**CONTINUOUS, "review": {"sample_files": 3}}
    inline = preview.make_preview(spec, sources, tmp_path / "inline", do_render=False)
    monkeypatch.setattr(batch, "DEFAULT_WORKER_MODE", "auto")
    maps = []
    real_map = batch.map_files
    monkeypatch.setattr(batch, "map_files", lambda fn, calls, **kw: maps.append((fn.__name__, len(calls)))
                        or real_map(fn, calls, **kw))
    shared = preview.make_preview(spec, sources, tmp_path / "shared", do_render=False)
    assert maps == [("plan_file", len(sources)), ("_preview_sample", 3)]
    assert [f.to_dict() | {"ms": 0} for f in shared.batch_plan.files] == \
        [f.to_dict() | {"ms": 0} for f in inline.batch_plan.files]
    assert [s.source for s in shared.samples] == [s.source for s in inline.samples]
    for a, b in zip(shared.samples, inline.samples):
        assert a.passed and b.passed and a.checks == b.checks and a.exception is None
        assert a.output.read_bytes() == b.output.read_bytes()
    assert shared.token == inline.token
    journals = sorted((tmp_path / "shared" / "logs").glob("*.events.jsonl"))
    assert len(journals) == 3
    assert all('"preview.sample.verify.succeeded"' in j.read_text(encoding="utf-8") for j in journals)
    assert "verification PASS" in (tmp_path / "shared" / "logs" / "preview.log").read_text(encoding="utf-8")


def test_a_shared_map_whose_worker_dies_makes_that_call_here(monkeypatch):
    from concurrent.futures.process import BrokenProcessPool

    class Broken:
        def result(self):
            raise BrokenProcessPool("a worker stopped abruptly")

    class Pool:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def submit(self, fn, *args):
            return Broken()

    monkeypatch.setattr(batch, "DEFAULT_WORKER_MODE", "auto")
    monkeypatch.setattr(batch.os, "cpu_count", lambda: 4)
    monkeypatch.setattr(batch, "_process_pool", lambda workers: Pool())
    assert batch.map_files(divmod, [(7, 2), (9, 4), (5, 5)]) == [(3, 1), (2, 1), (1, 0)]
    monkeypatch.setattr(batch, "_process_pool", lambda workers: pytest.fail("two calls stay here"))
    assert batch.map_files(divmod, [(7, 2), (9, 4)]) == [(3, 1), (2, 1)]
    assert batch.map_files(divmod, [(7, 2), (9, 4), (5, 5)], min_files=12) == [(3, 1), (2, 1), (1, 0)]
