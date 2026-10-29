"""LibreOffice keeps its user profiles (render/libreoffice.py): one per parallel worker under the config folder's
``render-profiles/<n>``, reused across calls instead of created and deleted every time; leased for the life of a
soffice run so a second process takes the next free index; a profile soffice cannot start from is wiped and
recreated once, after which the ordinary one-file-at-a-time retry stands; a timeout or a crash that came after a
PDF never wipes one. The last test runs the real soffice when it is installed."""
from __future__ import annotations

import json
import shutil
import subprocess
import time
import sys
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
    files = [_docx(tmp_path / f"{n}.docx", "21 13 13", f"T{n}") for n in range(4)]
    assert profiles_root() == config_dir() / "render-profiles"
    for n in range(2):
        got = libreoffice.render_pdfs(files, tmp_path / f"out{n}", workers=2)
        assert set(got) == set(files)
    # chunk i on profile i in both calls (two threads: the order they finish in is theirs)
    assert sorted(r[0] for r in runs[:2]) == ["0", "1"] and sorted(r[0] for r in runs[2:]) == ["0", "1"]
    assert sorted(runs[0][1] + runs[1][1]) == ["0.docx", "1.docx", "2.docx", "3.docx"]
    assert (profiles_root() / "0").is_dir() and (profiles_root() / "1").is_dir()   # kept, not deleted
    with ProfileLease(profiles_root(), 0) as lease:                           # and the leases were released
        assert lease.index == 0 and lease.path == profiles_root() / "0"


@pytest.mark.parametrize("sizes, workers, expected", [
    ([1], 4, [1]), ([2], 4, [1]), ([3], 4, [1]), ([4], 4, [2]), ([24], 8, [8]),   # at least two files a process
    ([1, 1], 2, [1, 1]), ([8, 8], 8, [4, 4]), ([3, 3], 3, [1, 1]),               # a run's sources and copies
    ([0, 2], 4, [0, 1]), ([1, 1, 1], 1, [1, 1, 1]),                              # more groups than workers: one each
])
def test_each_group_gets_processes_of_at_least_two_files_within_the_workers(sizes, workers, expected):
    assert libreoffice.plan_processes(sizes, workers) == expected


def test_groups_render_side_by_side_each_into_its_own_folder(tmp_path, monkeypatch):
    runs = []
    _fake_soffice(monkeypatch, runs)
    sources = [_docx(tmp_path / "in" / f"{n}.docx", "21 13 13", f"T{n}") for n in range(2)]
    copies = [_docx(tmp_path / "copies" / f"{n}.docx", "21 13 13", f"C{n}") for n in range(2)]
    src, out = libreoffice.render_groups([(sources, tmp_path / "source"), (copies, tmp_path / "output")], workers=4)
    assert src == {f: tmp_path / "source" / f"{f.stem}.pdf" for f in sources}
    assert out == {f: tmp_path / "output" / f"{f.stem}.pdf" for f in copies}
    # one process a group (two files each, not one a file), on profiles 0 and 1, side by side
    assert sorted(runs) == [("0", ["0.docx", "1.docx"]), ("1", ["0.docx", "1.docx"])]


def test_a_failed_chunk_retries_its_files_into_their_own_groups_folder(tmp_path, monkeypatch):
    runs = []

    def copies_crash(profile_dir, files):
        if len(files) > 1 and Path(files[0]).parent.name == "copies":
            raise subprocess.CalledProcessError(139, ["soffice"], output=b"", stderr=b"Segmentation fault")
    _fake_soffice(monkeypatch, runs, copies_crash)
    sources = [_docx(tmp_path / "in" / f"{n}.docx", "21 13 13", f"T{n}") for n in range(2)]
    copies = [_docx(tmp_path / "copies" / f"{n}.docx", "21 13 13", f"C{n}") for n in range(2)]
    said = []
    src, out = libreoffice.render_groups([(sources, tmp_path / "source"), (copies, tmp_path / "output")], workers=4,
                                         log=said.append)
    assert set(src) == set(sources) and out == {f: tmp_path / "output" / f"{f.stem}.pdf" for f in copies}
    assert any("retrying them one by one" in m for m in said)
    assert sorted(r for r in runs if len(r[1]) == 1) == [("1", ["0.docx"]), ("1", ["1.docx"])]


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
        # a timed-out profile is retired for the process: each one-by-one retry leases the next index (here every
        # run times out, so each retry retires its own profile too)
        assert got == {} and runs == [("0", ["A.docx", "B.docx"]), ("1", ["A.docx"]), ("2", ["B.docx"])]
        assert any(e["event"] == "render.profile_retired" for e in _events(journal))
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


@pytest.mark.render
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


@pytest.mark.skipif(sys.platform == "win32", reason="a POSIX process group; Windows uses taskkill /T")
def test_a_timed_out_soffice_run_takes_its_whole_process_tree_with_it(tmp_path):
    """``soffice`` is a launcher whose child does the work; killing the launcher alone on a timeout left the child
    alive on the profile, where every later request on that profile was handed to it and lost."""
    import stat
    import textwrap
    pidfile = tmp_path / "child.pid"
    launcher = tmp_path / "soffice"
    launcher.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        sleep 300 &
        echo $! > "{pidfile}"
        wait
        """))
    launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
    with pytest.raises(subprocess.TimeoutExpired):
        libreoffice._convert_batch(str(launcher), [tmp_path / "A.docx"], tmp_path / "out", tmp_path / "profile", 1)
    child = int(pidfile.read_text().strip())
    for _ in range(50):                                    # the kill is delivered; the child is reaped by init
        if _killed(child):
            break
        time.sleep(0.1)
    else:
        raise AssertionError(f"the launcher's child {child} survived the timeout")


def _killed(pid: int) -> bool:
    """Gone, or a zombie: ``kill(pid, 0)`` still succeeds on a killed process whose PID 1 does not reap orphans
    (a plain container), so on Linux the state in ``/proc/<pid>/stat`` decides; where ``/proc`` is absent (macOS)
    the ``kill`` answer stands."""
    import os
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    try:
        stat_line = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat_line.rpartition(")")[2].split()[0] == "Z"     # the field after the parenthesised command name


def test_a_retired_profile_is_never_leased_again_in_this_process(tmp_path, monkeypatch):
    root = profiles_root()
    libreoffice.retire_profile(root / "0")
    with libreoffice.ProfileLease(root, 0) as lease:
        assert lease.index == 1
    with libreoffice.ProfileLease(root, 1) as lease:
        assert lease.index == 1


def test_the_renderer_identity_carries_the_version_and_the_executable(monkeypatch):
    """A distribution's build id is the same across its security updates; the executable's size and date move."""
    from spec_surgeon import render
    monkeypatch.setattr(libreoffice, "version", lambda soffice=None: "build 420(Build:2)")
    monkeypatch.setattr(libreoffice, "executable_identity", lambda soffice=None: "exe-1-2")
    assert render.renderer_identity("libreoffice") == "libreoffice build 420(Build:2) exe-1-2"
    monkeypatch.setattr(libreoffice, "version", lambda soffice=None: None)
    assert render.renderer_identity("libreoffice") == "libreoffice exe-1-2"
    monkeypatch.setattr(libreoffice, "executable_identity", lambda soffice=None: None)
    assert render.renderer_identity("libreoffice") == "libreoffice unknown"
