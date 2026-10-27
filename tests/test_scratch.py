"""The window's scratch folder (``scratch.py``): one locked folder per window; a crashed window's folder, with a
journal worth restoring, is taken over by the next start and every other leftover is deleted; another window's
folder is never touched; the render cache beside them is never swept; a clean close removes the folder whole."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from spec_surgeon import scratch as sc


def _journal(folder: Path, **extra) -> None:
    data = {"version": 2, "messages": [], "decisions": [], "results": {}, "budget": {}, **extra}
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "conversation.json").write_text(json.dumps(data), encoding="utf-8")


def test_a_window_opens_a_new_locked_folder_and_discard_removes_it(tmp_path):
    root = tmp_path / "scratch"
    s = sc.open_window(root, now=datetime(2026, 10, 27, 9, 0, 0))
    assert s.path.parent == root and s.path.name == f"20261027-090000-{os.getpid()}" and not s.adopted
    assert s.locked and (s.path / sc.LOCK_FILE).is_file() and s.journal == s.path / "conversation.json"
    second = sc.open_window(root, now=datetime(2026, 10, 27, 9, 0, 0))   # the same second: another folder, never this
    assert second.path != s.path and second.path.parent == root and s.path.is_dir()
    assert second.discard() and not second.path.exists()
    (s.path / "run" / "outputs").mkdir(parents=True)
    (s.path / "run" / "outputs" / "a.docx").write_bytes(b"PK")
    s.journal.write_text("{}", encoding="utf-8")
    assert s.discard() and not s.path.exists() and not s.locked
    assert sorted(root.iterdir()) == []


def test_a_crashed_windows_folder_is_taken_over_and_every_other_leftover_is_swept(tmp_path):
    root = tmp_path / "scratch"
    older = root / "20261001-080000-1"
    _journal(older, messages=[{"role": "user", "text": "older"}])
    newer = root / "20261002-080000-2"
    _journal(newer, decisions=["Arial"])
    empty = root / "20261003-080000-3"
    _journal(empty)                                           # a journal with nothing in it worth restoring
    broken = root / "20261004-080000-4"
    broken.mkdir(parents=True)
    (broken / "conversation.json").write_text("not json", encoding="utf-8")
    remnant = root / "20261005-080000-5"                      # a close that could not delete everything
    (remnant / "preview").mkdir(parents=True)
    (remnant / "preview" / "preview.json").write_text("{}", encoding="utf-8")
    cache = root / ".render-cache"
    cache.mkdir()
    (cache / "x.pdf").write_bytes(b"%PDF")
    s = sc.open_window(root)
    assert s.adopted and s.path == newer and s.locked and s.journal.is_file()
    for gone in (older, empty, broken, remnant):
        assert not gone.exists(), gone
    assert (cache / "x.pdf").is_file()
    assert s.discard() and not newer.exists() and (cache / "x.pdf").is_file()


def test_another_windows_folder_is_never_touched_until_its_lock_is_gone(tmp_path):
    root = tmp_path / "scratch"
    first = sc.open_window(root)
    _journal(first.path, decisions=["mine"])                  # the first window is mid-conversation
    second = sc.open_window(root)
    assert second.path != first.path and not second.adopted and first.path.is_dir() and first.journal.is_file()
    first.release()                                           # the first window ends without a clean close
    third = sc.open_window(root)
    assert third.adopted and third.path == first.path
    assert second.discard() and third.discard() and sorted(root.iterdir()) == []


def test_only_a_journal_with_a_conversation_in_it_is_worth_restoring(tmp_path):
    j = tmp_path / "conversation.json"
    assert not sc._worth_restoring(j)                                       # none
    j.write_text("{}", encoding="utf-8")
    assert not sc._worth_restoring(j)
    j.write_text(json.dumps({"version": 2}), encoding="utf-8")
    assert not sc._worth_restoring(j)
    j.write_text(json.dumps({"version": 3, "messages": [{"role": "user", "text": "x"}]}), encoding="utf-8")
    assert not sc._worth_restoring(j)                                       # a version this app cannot read
    j.write_text("[]", encoding="utf-8")
    assert not sc._worth_restoring(j)
    for key in sc.RESTORABLE_KEYS:
        value = {"a": "b"} if key in ("results", "working", "todo") else [{"x": 1}]
        j.write_text(json.dumps({"version": 2, key: value}), encoding="utf-8")
        assert sc._worth_restoring(j), key


def test_discard_removes_the_journal_first_so_a_remainder_is_never_restored(tmp_path, monkeypatch):
    """A copy held open in Word can keep a folder from being deleted whole; the journal goes first, so what is left
    reads as a remnant (swept at the next start), never as a crashed session."""
    root = tmp_path / "scratch"
    s = sc.open_window(root)
    _journal(s.path, decisions=["Arial"])
    held = s.path / "run" / "outputs" / "Spec.docx"
    held.parent.mkdir(parents=True)
    held.write_bytes(b"PK")
    monkeypatch.setattr(sc, "_delete", lambda folder: None)               # the folder cannot be removed
    assert not s.discard() and s.path.is_dir() and not s.journal.exists() and held.is_file()
    monkeypatch.undo()
    assert not sc._worth_restoring(s.journal)
    again = sc.open_window(root)                                           # the next start sweeps the remnant
    assert not again.adopted and not held.exists() and sorted(p.name for p in root.iterdir()) == [again.path.name]
    assert again.discard() and sorted(root.iterdir()) == []
