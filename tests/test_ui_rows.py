"""The window's tables and progress (``ui.rows``, no Qt) computed from a real preview and a real run — and the
``run_batch`` log lines the progress bar reads, pinned so a change to them cannot silently freeze the bar."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from make_fixtures import build
from spec_surgeon.gui import Session
from spec_surgeon.ui.rows import (PREVIEW_COLUMNS, RESULT_COLUMNS, ProgressTracker, preview_counts, preview_rows,
                                  result_counts, result_rows, written_back)

SPECS = Path(__file__).resolve().parent.parent / "examples" / "specs"


@pytest.fixture
def folder(tmp_path) -> Path:
    d = tmp_path / "Specs"
    d.mkdir()
    for i in range(3):
        (d / f"21 0{i} 00 – SPEC {i}.docx").write_bytes(build(f"21 0{i} 00", f"SPEC {i}"))
    return d


def test_rows_from_a_real_preview_and_run(folder, tmp_path):
    s = Session(folder=folder, out_root=tmp_path / "out")
    s.load_spec(SPECS / "remove-all-headers-footers.json")
    s.make_preview(render=False, log=lambda m: None)
    rows = preview_rows(s.preview)
    assert [r.badge for r in rows] == ["planned"] * 3 and all(len(r.cells) == len(PREVIEW_COLUMNS) for r in rows)
    assert rows[0].cells[0] == "21 00 00 – SPEC 0.docx" and "members removed" in rows[0].cells[3]
    assert preview_counts(s.preview) == {"files": 3, "planned": 3, "exceptions": 0, "patterns": 1}
    lines = []
    text = s.run(in_place=False, render=False, log=lines.append)
    assert "PASS 3" in text and s.last_batch is not None and s.last_batch.counts()["PASS"] == 3
    res = result_rows(s.last_batch)
    assert [r.badge for r in res] == ["pass"] * 3 and all(len(r.cells) == len(RESULT_COLUMNS) for r in res)
    assert all(r.open_path and r.open_path.exists() and r.open_path.parent.name == "outputs" for r in res)
    assert not any(written_back(f) for f in s.last_batch.files)       # copies: delivered to outputs/, not back
    assert result_counts(res) == {"PASS": 3, "FAIL": 0, "EXCEPTION": 0}
    # the progress bar follows run_batch's own lines
    tracker = ProgressTracker()
    moves = [m for m in (tracker.feed(line) for line in lines) if m]
    assert moves[0] == (0, 3, "Editing and verifying") and (3, 3, "Editing and verifying") in moves
    assert sorted(tracker.seen) == sorted(f.name for f in folder.glob("*.docx"))


def test_in_place_rows_say_written_back(folder, tmp_path):
    s = Session(folder=folder, out_root=tmp_path / "out")
    s.load_spec(SPECS / "remove-all-headers-footers.json")
    s.make_preview(render=False, log=lambda m: None)
    s.run(in_place=True, render=False, log=lambda m: None)
    assert all(written_back(f) for f in s.last_batch.files)
    assert all("written back" in r.cells[2] for r in result_rows(s.last_batch))


def test_exception_rows_explain_in_plain_english(folder, tmp_path):
    spec = json.loads((SPECS / "remove-all-headers-footers.json").read_text(encoding="utf-8"))
    (folder / "21 99 00 – BROKEN.docx").write_bytes(b"not a zip")
    s = Session(folder=folder, out_root=tmp_path / "out")
    s.spec, s.spec_source = spec, "x.json"
    s.make_preview(render=False, log=lambda m: None)
    rows = preview_rows(s.preview)
    broken = [r for r in rows if r.cells[0].startswith("21 99 00")][0]
    assert broken.badge == "exception" and broken.is_problem and broken.cells[2] == "Exception"
    s.run(in_place=False, render=False, log=lambda m: None)
    res = [r for r in result_rows(s.last_batch) if r.cells[0].startswith("21 99 00")][0]
    assert res.badge == "exception" and "untouched" in res.cells[3] and res.tooltip


def test_progress_tracker_lines():
    t = ProgressTracker()
    assert t.feed("compiler: submit_editspec") is None
    assert t.feed("12 file(s); transform+verify with 4 worker(s)") == (0, 12, "Editing and verifying")
    assert t.feed("  done a.docx") == (1, 12, "Editing and verifying")
    assert t.feed("rendering 24 PDF(s) with libreoffice") == (0, 0, "Rendering pages")
    fresh = ProgressTracker()
    assert fresh.feed("  done a.docx") is None          # a done line before the total is not progress
