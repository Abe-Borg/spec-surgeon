"""LibreOffice keeps its user profiles (render/libreoffice.py): one per parallel worker under the config folder's
``render-profiles/<n>``, reused across calls instead of created and deleted every time; leased for the life of a
soffice run so a second process takes the next free index; a profile soffice cannot start from is wiped and
recreated once, after which the ordinary one-file-at-a-time retry stands; a timeout or a crash that came after a
PDF never wipes one. The last test runs the real soffice when it is installed."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from make_fixtures import build
from spec_surgeon import diagnostics as diag
from spec_surgeon.render import libreoffice
from spec_surgeon.render.libreoffice import ProfileLease, profiles_root
from spec_surgeon.settings import config_dir
from test_vision import make_pdf


def _docx(path: Path, number="21 13 13", title="WET-PIPE") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build(number, title))
    return path


def _events(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _fake_soffice(monkeypatch, runs, behaviour=None):
    """``_convert_batch`` as a fake that records (profile index, file names) per run and writes PDFs, unless
    ``behaviour(profile_dir, files)`` raises instead."""
    def convert(soffice, files, out_dir, profile_dir, timeout):
        runs.append((profile_dir.name, [Path(f).name for f in files]))
        if behaviour is not None:
            behaviour(profile_dir, files)
        for f in files:
            make_pdf(Path(out_dir) / (Path(f).stem + ".pdf"))
        return subprocess.CompletedProcess(["soffice"], 0, b"convert done", b"")
    monkeypatch.setattr(libreoffice, "find_soffice", lambda: "soffice")
    monkeypatch.setattr(libreoffice, "_convert_batch", convert)


def test_profiles_live_under_the_config_folder_and_are_reused_across_calls(tmp_path, monkeypatch):
    runs = []
    _fake_soffice(monkeypatch, runs)
    files = [_docx(tmp_path / f"{n}.docx", "21 13 13", f"T{n}") for n in range(3)]
    assert profiles_root() == config_dir() / "render-profiles"
    for n in range(2):
        got = libreoffice.render_pdfs(files, tmp_path / f"out{n}", workers=2)
        assert set(got) == set(files)
    # chunk i on profile i in both calls (two threads: the order they finish in is theirs)
    assert sorted(r[0] for r in runs[:2]) == ["0", "1"] and sorted(r[0] for r in runs[2:]) == ["0", "1"]
    assert sorted(runs[0][1] + runs[1][1]) == ["0.docx", "1.docx", "2.docx"]
    assert (profiles_root() / "0").is_dir() and (profiles_root() / "1").is_dir()   # kept, not deleted
    with ProfileLease(profiles_root(), 0) as lease:                           # and the leases were released
        assert lease.index == 0 and lease.path == profiles_root() / "0"


def test_a_profile_soffice_cannot_start_from_is_wiped_and_recreated_once(tmp_path, monkeypatch):
    runs = []
    root = profiles_root()
    (root / "0").mkdir(parents=True)
    (root / "0" / "user").mkdir()
    (root / "0" / "user" / "registrymodifications.xcu").write_text("<damaged", encoding="utf-8")

    def broken_until_wiped(profile_dir, files):
        if (profile_dir / "user" / "registrymodifications.xcu").exists():
            raise subprocess.CalledProcessError(81, ["soffice"], output=b"", stderr=b"Application Error")
    _fake_soffice(monkeypatch, runs, broken_until_wiped)
    files = [_docx(tmp_path / "A.docx"), _docx(tmp_path / "B.docx", "21 13 16", "DRY-PIPE")]
    journal = tmp_path / "events.jsonl"
    said, details = [], []
    with diag.scope(journal):
        got = libreoffice.render_pdfs(files, tmp_path / "out", workers=1, log=said.append, detail=details.append)
    assert set(got) == set(files) and said == []                             # both rendered, nothing to complain of
    assert runs == [("0", ["A.docx", "B.docx"]), ("0", ["A.docx", "B.docx"])]   # the chunk again on the fresh profile
    assert not (root / "0" / "user" / "registrymodifications.xcu").exists() and (root / "0").is_dir()
    resets = [e for e in _events(journal) if e["event"] == "render.profile_reset"]
    assert len(resets) == 1 and resets[0]["profile"] == str(root / "0") and "exit status 81" in resets[0]["reason"]
    assert any("profile 0 failed to start" in d and "recreating it" in d for d in details)


def test_after_one_wipe_a_still_failing_profile_takes_the_ordinary_one_by_one_retry(tmp_path, monkeypatch):
    runs = []

    def always_fails(profile_dir, files):
        raise subprocess.CalledProcessError(1, ["soffice"], output=b"", stderr=b"javaldx: Could not find a Java Runtime")
    _fake_soffice(monkeypatch, runs, always_fails)
    files = [_docx(tmp_path / "A.docx"), _docx(tmp_path / "B.docx", "21 13 16", "DRY-PIPE")]
    journal = tmp_path / "events.jsonl"
    said = []
    with diag.scope(journal):
        assert libreoffice.render_pdfs(files, tmp_path / "out", workers=1, log=said.append) == {}
    # the chunk, the chunk again after the wipe, then each file alone (no second wipe) — four soffice starts, not more
    assert runs == [("0", ["A.docx", "B.docx"]), ("0", ["A.docx", "B.docx"]), ("0", ["A.docx"]), ("0", ["B.docx"])]
    events = _events(journal)
    assert sum(e["event"] == "render.profile_reset" for e in events) == 1
    assert any("retrying them one by one" in m for m in said)
    assert sum("could not render it" in m and "Java Runtime" in m for m in said) == 2


@pytest.mark.parametrize("failure", ["timeout", "after_output"])
def test_a_timeout_or_a_crash_after_output_never_wipes_the_profile(tmp_path, monkeypatch, failure):
    runs = []
    root = profiles_root()
    (root / "0").mkdir(parents=True)
    marker = root / "0" / "user"
    marker.mkdir()

    def behaviour(profile_dir, files):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(["soffice"], 7, stderr=b"hung on file")
        if len(files) > 1:                                                     # the chunk: one PDF out, then a crash
            make_pdf(tmp_path / "out" / "A.pdf")
            raise subprocess.CalledProcessError(139, ["soffice"], output=b"", stderr=b"Segmentation fault")
        if Path(files[0]).name == "B.docx":
            raise subprocess.CalledProcessError(139, ["soffice"], output=b"", stderr=b"Segmentation fault")
    _fake_soffice(monkeypatch, runs, behaviour)
    files = [_docx(tmp_path / "A.docx"), _docx(tmp_path / "B.docx", "21 13 16", "DRY-PIPE")]
    journal = tmp_path / "events.jsonl"
    with diag.scope(journal):
        got = libreoffice.render_pdfs(files, tmp_path / "out", workers=1, timeout=7)
    assert marker.is_dir()                                                     # the profile was left alone
    assert not any(e["event"] == "render.profile_reset" for e in _events(journal))
    if failure == "timeout":
        assert got == {} and runs == [("0", ["A.docx", "B.docx"]), ("0", ["A.docx"]), ("0", ["B.docx"])]
    else:
        assert set(got) == {files[0]} and runs == [("0", ["A.docx", "B.docx"]), ("0", ["B.docx"])]


def test_a_profile_another_process_holds_is_skipped_for_the_next_free_one(tmp_path, monkeypatch):
    root = profiles_root()
    root.mkdir(parents=True)
    held = open(root / "0.lock", "a+b")                                       # what another soffice run looks like
    libreoffice._try_lock(held)
    try:
        with ProfileLease(root, 0) as lease:
            assert lease.index == 1 and lease.path == root / "1" and lease.path.is_dir()
            with ProfileLease(root, 0) as another:                             # 0 and 1 busy: 2
                assert another.index == 2
        with ProfileLease(root, 1) as lease:                                   # released on exit
            assert lease.index == 1
    finally:
        libreoffice._unlock(held)
    with ProfileLease(root, 0) as lease:
        assert lease.index == 0
    runs = []
    _fake_soffice(monkeypatch, runs)
    held = open(root / "0.lock", "a+b")
    libreoffice._try_lock(held)
    try:
        got = libreoffice.render_pdfs([_docx(tmp_path / "A.docx")], tmp_path / "out", workers=1)
    finally:
        libreoffice._unlock(held)
    assert len(got) == 1 and runs == [("1", ["A.docx"])]                       # the render went to profile 1


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
def test_the_real_soffice_renders_twice_on_one_kept_profile(tmp_path):
    source = _docx(tmp_path / "Spec.docx")
    details = []
    for n in range(2):
        got = libreoffice.render_pdfs([source], tmp_path / f"out{n}", workers=1, detail=details.append)
        assert got == {source: tmp_path / f"out{n}" / "Spec.pdf"} and got[source].stat().st_size > 0
    profile = profiles_root() / "0"
    assert profile.is_dir() and (profile / "user").is_dir()                    # LibreOffice's own files, kept
    assert all("(profile 0)" in d for d in details) and len(details) == 2
    with ProfileLease(profiles_root(), 0) as lease:                           # the lease was released after each run
        assert lease.index == 0
