"""One spawned worker, one private Word, every file of a batch (render/word_worker.run_word_batch) — the parent
side: per-file results, the per-file deadline the parent enforces (a stuck file ends the worker and its private
Word, the rest go on in a fresh one), a worker that dies, an instance that is lost, a Word that never starts, and
the private-instance cleanup accounting. The COM calls cannot run here, so a fake worker stands in for
``_batch_worker`` behind a REAL spawned process and pipe: it speaks the worker's protocol and misbehaves on cue
by file name. Also the renderer identity Word reports to the cache without starting, and the HRESULTs that mean
the instance is gone."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from spec_surgeon.render import word_com, word_worker
from spec_surgeon.render.word_worker import WordWorkerError, run_word_batch

PDF = b"%PDF-1.4 fake render\n"


def fake_worker(connection, request):
    """The worker's protocol, scripted by file name: ``ok*`` renders, ``refused`` is a per-file COM error, ``stuck``
    never answers, ``crash`` kills the process mid-file, ``lost`` is a COM error that says Word is gone, ``nopdf``
    says ok and writes nothing, and a batch whose first file is ``nostart``/``slowstart`` never gets a Word."""
    scratch = Path(request["scratch"])
    names = [Path(f).name for f in request["files"]]
    try:
        if names and names[0] == "nostart.docx":
            connection.send({"kind": "worker_failed", "code": "word-com", "reason": "COM error 0x80080005 (Server execution failed)",
                             "stage": "word_start"})
            return
        if names and names[0] == "slowstart.docx":
            time.sleep(30)
        connection.send({"kind": "started", "identity": {"pid": 4242, "created": "2026-10-20T12:00:00"}})
        connection.send({"kind": "ready", "version": "16.0 (build 17328)"})
        for index, name in enumerate(names):
            connection.send({"kind": "file_started", "index": index})
            if name == "stuck.docx":
                time.sleep(30)
            if name == "crash.docx":
                os._exit(3)
            if name == "refused.docx":
                connection.send({"kind": "file_done", "index": index, "ok": False, "code": "word-com",
                                 "reason": "COM error 0x80020009 (Exception occurred.): Microsoft Word: cannot open",
                                 "stage": "word_open"})
                continue
            if name == "lost.docx":
                connection.send({"kind": "file_done", "index": index, "ok": False, "code": "word-com",
                                 "reason": "COM error 0x800706BA (The RPC server is unavailable.)", "stage": "word_export"})
                connection.send({"kind": "worker_failed", "code": "word-com",
                                 "reason": "COM error 0x800706BA (The RPC server is unavailable.)", "stage": "word_file"})
                return
            if name != "nopdf.docx":
                (scratch / f"{index}.pdf").write_bytes(PDF + name.encode())
            time.sleep(0.05)
            connection.send({"kind": "file_done", "index": index, "ok": True})
        connection.send({"kind": "finished"})
    finally:
        connection.close()


@pytest.fixture
def word(monkeypatch, tmp_path):
    """The fake worker behind a real spawned process; the private-instance handle calls recorded, never made."""
    cleanup = []
    monkeypatch.setattr(word_worker, "_batch_worker", fake_worker)
    monkeypatch.setattr(word_worker, "_open_private_handle", lambda identity: ("private", identity["pid"]))
    monkeypatch.setattr(word_worker, "_close_private_handle",
                        lambda handle, **kwargs: cleanup.append((handle, kwargs["terminate"])))

    def files(*names):
        out = []
        for name in names:
            f = tmp_path / "in" / name
            f.parent.mkdir(exist_ok=True)
            f.write_bytes(b"docx " + name.encode())
            out.append(f)
        return out
    return SimpleNamespace(files=files, out=tmp_path / "pdf", cleanup=cleanup)


def _pdf(path: Path, name: str) -> None:
    assert isinstance(path, Path) and path.read_bytes() == PDF + name.encode()


def test_one_worker_renders_every_file_and_publishes_each_pdf(word):
    files = word.files("ok1.docx", "ok2.docx", "ok3.docx")
    batch = run_word_batch(files, word.out, timeout_per_file=20)
    assert batch.workers == 1 and batch.version == "16.0 (build 17328)" and batch.start_error is None
    assert list(batch.results) == files
    for f in files:
        _pdf(batch.results[f], f.name)
        assert batch.results[f] == word.out / (f.stem + ".pdf") and batch.seconds[f] >= 0.05
    assert not list(word.out.glob(".specsurgeon-render-*"))                 # staged copies are gone
    assert word.cleanup == [(("private", 4242), False)]                      # Word quit by itself: nothing to terminate


def test_a_refused_file_is_reported_and_the_others_render_in_the_same_instance(word):
    ok1, refused, ok2 = word.files("ok1.docx", "refused.docx", "ok2.docx")
    batch = run_word_batch([ok1, refused, ok2], word.out, timeout_per_file=20)
    assert batch.workers == 1
    _pdf(batch.results[ok1], "ok1.docx")
    _pdf(batch.results[ok2], "ok2.docx")
    error = batch.results[refused]
    assert isinstance(error, WordWorkerError) and error.code == "word-com" and error.details["stage"] == "word_open"
    assert "cannot open" in str(error) and not (word.out / "refused.pdf").exists()


def test_a_stuck_file_ends_its_worker_and_private_word_and_the_rest_continue_in_a_fresh_one(word):
    ok1, stuck, ok2 = word.files("ok1.docx", "stuck.docx", "ok2.docx")
    t = time.perf_counter()
    batch = run_word_batch([ok1, stuck, ok2], word.out, timeout_per_file=1)
    assert time.perf_counter() - t < 15                                      # the deadline, not the fake's 30 s sleep
    assert batch.workers == 2 and batch.start_error is None
    _pdf(batch.results[ok1], "ok1.docx")
    _pdf(batch.results[ok2], "ok2.docx")
    error = batch.results[stuck]
    assert isinstance(error, WordWorkerError) and error.code == "word-timeout"
    assert "stuck.docx" in str(error) and error.details["timeout_seconds"] == 1
    assert error.details["private_cleanup_available"] is True
    assert word.cleanup == [(("private", 4242), True), (("private", 4242), False)]   # the stuck one terminated


def test_a_worker_that_dies_mid_file_fails_that_file_only(word):
    ok1, crash, ok2 = word.files("ok1.docx", "crash.docx", "ok2.docx")
    batch = run_word_batch([ok1, crash, ok2], word.out, timeout_per_file=20)
    assert batch.workers == 2
    _pdf(batch.results[ok1], "ok1.docx")
    _pdf(batch.results[ok2], "ok2.docx")
    error = batch.results[crash]
    assert isinstance(error, WordWorkerError) and error.code == "word-worker-died"
    assert word.cleanup[0] == (("private", 4242), True)                      # the orphaned instance is terminated


def test_a_lost_instance_ends_the_worker_and_the_rest_get_a_fresh_one(word):
    lost, ok = word.files("lost.docx", "ok.docx")
    batch = run_word_batch([lost, ok], word.out, timeout_per_file=20)
    assert batch.workers == 2
    error = batch.results[lost]
    assert isinstance(error, WordWorkerError) and "RPC server is unavailable" in str(error)
    assert error.details["stage"] == "word_export"
    _pdf(batch.results[ok], "ok.docx")


def test_a_pdf_the_worker_did_not_write_is_word_no_pdf(word):
    nopdf, ok = word.files("nopdf.docx", "ok.docx")
    batch = run_word_batch([nopdf, ok], word.out, timeout_per_file=20)
    assert batch.workers == 1 and batch.results[nopdf].code == "word-no-pdf"
    _pdf(batch.results[ok], "ok.docx")


def test_a_word_that_never_starts_is_a_start_error_for_every_file(word):
    files = word.files("nostart.docx", "ok.docx")
    batch = run_word_batch(files, word.out, timeout_per_file=20)
    assert batch.workers == 1 and batch.start_error is not None
    assert batch.start_error.details["stage"] == "word_start" and "Server execution failed" in str(batch.start_error)
    assert all(batch.results[f] is batch.start_error for f in files)
    assert word.cleanup == [(None, True)]                                    # no handle was ever owned: nothing to end


def test_a_slow_start_is_a_start_timeout(word):
    files = word.files("slowstart.docx", "ok.docx")
    t = time.perf_counter()
    batch = run_word_batch(files, word.out, timeout_per_file=1)
    assert time.perf_counter() - t < 15
    assert batch.start_error is not None and batch.start_error.code == "word-timeout"
    assert "did not start" in str(batch.start_error) and batch.start_error.details["stage"] == "word_start"
    assert all(batch.results[f] is batch.start_error for f in files)


def test_a_fresh_worker_that_cannot_start_fails_the_rest_without_losing_what_rendered(word):
    ok1, stuck, nostart, ok2 = word.files("ok1.docx", "stuck.docx", "nostart.docx", "ok2.docx")
    batch = run_word_batch([ok1, stuck, nostart, ok2], word.out, timeout_per_file=1)
    assert batch.workers == 2 and batch.start_error is None                   # something rendered: no backend failure
    _pdf(batch.results[ok1], "ok1.docx")
    assert batch.results[stuck].code == "word-timeout"
    assert batch.results[nostart].details["stage"] == "word_start"
    assert batch.results[ok2] is batch.results[nostart]


def test_word_com_render_pdfs_runs_the_batch_and_says_what_each_file_did(word):
    ok1, refused, ok2 = word.files("ok1.docx", "refused.docx", "ok2.docx")
    (word.out).mkdir()
    (word.out / "ok1.pdf").write_bytes(b"stale")
    said, details = [], []
    result = word_com.render_pdfs([ok1, refused, ok2], word.out, log=said.append, detail=details.append, timeout=20)
    assert result == {ok1: word.out / "ok1.pdf", ok2: word.out / "ok2.pdf"}
    _pdf(result[ok1], "ok1.docx")
    assert said == ["refused.docx: Word could not render it: COM error 0x80020009 (Exception occurred.): "
                    "Microsoft Word: cannot open"]
    assert [d.split(" in ")[0] for d in details] == ["Word 16.0 (build 17328): ok1.docx → ok1.pdf",
                                                      "Word 16.0 (build 17328): ok2.docx → ok2.pdf"]


def test_an_empty_batch_spawns_nothing(word):
    batch = run_word_batch([], word.out, timeout_per_file=20)
    assert batch.workers == 0 and batch.results == {} and batch.start_error is None


# ---- what the parent decides from a COM error -------------------------------------------------------------------------

def test_instance_lost_reads_the_hresults_that_mean_word_is_gone():
    class com_error(Exception):  # noqa: N801 — pywintypes' name, which is what is checked
        def __init__(self, hresult):
            super().__init__(hresult, "text", None, None)
            self.hresult = hresult
    assert word_worker.instance_lost(com_error(-2147023174))                  # 0x800706BA as pywin32 reports it
    assert word_worker.instance_lost(com_error(0x80010108))
    assert not word_worker.instance_lost(com_error(-2147352567))              # 0x80020009: this file was refused
    assert not word_worker.instance_lost(RuntimeError("not COM"))


# ---- the version the cache keys on, read without starting Word -------------------------------------------------------

def _winreg(values):
    """A winreg whose keys are the (root, path) pairs in ``values``: {(root, path): {name: value}}."""
    class Key:
        def __init__(self, root, path):
            self.values = values[(root, path)]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def OpenKey(root, path):
        if (root, path) not in values:
            raise OSError("key not found")
        return Key(root, path)
    return SimpleNamespace(HKEY_LOCAL_MACHINE="HKLM", HKEY_CLASSES_ROOT="HKCR", OpenKey=OpenKey,
                           QueryValueEx=lambda key, name: (key.values[name], 1),
                           QueryValue=lambda key, name: key.values[name])


def test_installed_word_version_comes_from_the_registry_click_to_run_first(monkeypatch):
    c2r = {("HKLM", word_com.C2R_CONFIGURATION_KEYS[0]): {"VersionToReport": "16.0.17328.20068"},
           ("HKCR", word_com.WORD_CURVER_KEY): {None: "Word.Application.16"}}
    monkeypatch.setitem(sys.modules, "winreg", _winreg(c2r))
    assert word_com.installed_version() == "16.0.17328.20068"
    monkeypatch.setitem(sys.modules, "winreg", _winreg({("HKCR", word_com.WORD_CURVER_KEY): {None: "Word.Application.16"}}))
    assert word_com.installed_version() == "Word.Application.16"            # an MSI install without an App Path
    monkeypatch.setitem(sys.modules, "winreg", _winreg({}))
    assert word_com.installed_version() is None                               # no Word registered at all
