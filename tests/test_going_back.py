"""Changing your mind in Safe mode: Discard withdraws a button's preview and shows again what the window showed before
it, and Undo takes the working set back one run at a time — the copies each document builds on, the history, what
was left untouched and the documents the ✗ set aside. Neither writes or deletes a file: the copies a run made stay
where they were saved, and the originals are never touched."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from make_fixtures import build, build_dirty
from spec_surgeon.document_task import DocumentTask
from spec_surgeon.gui import Session

TITLE = {"pattern": "SECTION {number} - {title}"}


@pytest.fixture
def files(tmp_path) -> list:
    out = []
    for num, title in (("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"), ("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS")):
        path = tmp_path / f"{num}.docx"
        path.write_bytes(build(num, title))
        out.append(path)
    return out


def safe_task(tmp_path, journal=None) -> DocumentTask:
    return DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=False, mode="safe",
                        journal=journal)


def press(task, recipe, inputs=None, hold=()):
    """A button through its preview and Create; the view after the run."""
    view = task.perform("recipe", recipe=recipe, inputs=inputs or {})
    assert view["stage"] == "preview", [m["text"] for m in task.messages]
    return task.perform("primary", token=view["token"], hold=[str(p) for p in hold])


def on_disk(tmp_path) -> dict:
    return {p: p.read_bytes() for p in sorted(tmp_path.rglob("*")) if p.is_file() and p.suffix == ".docx"}


# ---- Discard --------------------------------------------------------------------------------------------------------

def test_discard_withdraws_the_preview_and_shows_the_last_copies_again(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_highlight")
    copies, working = dict(task.results), dict(task.working)
    view = task.perform("recipe", recipe="title_property", inputs=TITLE)
    assert view["stage"] == "preview" and view["results"] == {}       # a button's preview shows no earlier copies
    before = on_disk(tmp_path)
    task.discard_preview()
    view = task.view()
    assert view["stage"] == "done" and view["results"] == copies and task.working == working
    assert task.session.spec is None and view["action"] == "" and view["samples"] == []
    assert task.decisions == [] and task.generation == 1
    assert on_disk(tmp_path) == before                                 # nothing created, nothing deleted
    assert task.messages[-1]["text"].startswith("Preview discarded; nothing was created.")
    with pytest.raises(ValueError, match="no preview to discard"):
        task.discard_preview()


def test_a_first_buttons_discard_returns_to_the_empty_window(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    task.perform("recipe", recipe="remove_highlight", inputs={})
    task.discard_preview()
    view = task.view()
    assert view["stage"] == "empty" and view["results"] == {} and task.working == {}
    assert view["working"]["undo"] == ""                               # nothing ran, so nothing to undo


def test_a_button_pressed_over_a_preview_discards_back_to_what_came_before_both(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_highlight")
    copies = dict(task.results)
    task.perform("recipe", recipe="remove_comments", inputs={})
    task.perform("recipe", recipe="title_property", inputs=TITLE)    # a second button over the first's preview
    task.discard_preview()
    assert task.results == copies


def test_discard_restores_what_the_last_button_left_untouched(tmp_path, files):
    empty = tmp_path / "empty.docx"
    empty.write_bytes(build_dirty("empty_body"))
    task = safe_task(tmp_path)
    task.attach([files[0], empty])
    press(task, "title_property", TITLE)
    left = task.view()["left_untouched"]
    assert [e["name"] for e in left] == ["empty.docx"]
    task.perform("recipe", recipe="remove_highlight", inputs={})
    assert task.view()["left_untouched"] == []                         # hidden while the preview waits
    task.discard_preview()
    assert task.view()["left_untouched"] == left


def test_discard_is_safe_modes(tmp_path, files):
    task = DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=False)
    task.attach(files)
    with pytest.raises(ValueError, match="Safe mode"):
        task.discard_preview()


# ---- Undo -----------------------------------------------------------------------------------------------------------

def test_undo_goes_back_one_button_at_a_time_and_deletes_nothing(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    originals = {p: p.read_bytes() for p in files}
    press(task, "remove_highlight")
    first = {"working": dict(task.working), "history": list(task.history), "results": dict(task.results),
             "applied": {k: list(v) for k, v in task.applied.items()}}
    press(task, "title_property", TITLE)
    assert task.generation == 2 and task.working != first["working"]
    view = task.view()
    assert view["working"]["undo"].startswith("Set the document title property")
    assert view["working"]["undo_blocked"] == ""
    before = on_disk(tmp_path)

    task.undo()
    assert task.working == first["working"] and task.generation == 1 and task.history == first["history"]
    assert task.applied == first["applied"] and task.results == first["results"] and task.stage == "done"
    assert task.documents() == [Path(v) for v in first["working"].values()]   # the next button builds on them
    assert task.messages[-1]["text"].startswith("Undid “Set the document title property")
    assert "nothing was deleted" in task.messages[-1]["text"]
    assert task.view()["working"]["undo"].startswith("Remove")

    task.undo()
    assert task.working == {} and task.generation == 0 and task.history == [] and task.applied == {}
    assert task.results == {} and task.stage == "empty" and task.documents() == files
    assert "works on your originals again" in task.messages[-1]["text"]
    with pytest.raises(ValueError, match="nothing to undo"):
        task.undo()
    assert on_disk(tmp_path) == before                                 # every copy is where it was saved
    assert {p: p.read_bytes() for p in files} == originals


def test_the_next_button_after_an_undo_builds_on_the_copies_from_before(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_highlight")
    first = dict(task.working)
    press(task, "remove_comments")
    task.undo()
    view = task.perform("recipe", recipe="title_property", inputs=TITLE)
    assert view["stage"] == "preview"
    assert sorted(str(p) for p in task.session.documents) == sorted(first.values())
    task.perform("primary", token=view["token"])
    assert task.generation == 2 and len(task.undo_points) == 2
    assert task.undo_points[-1]["working"] == first


def test_undo_takes_the_documents_its_x_set_aside_off_the_to_do_list(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_highlight", hold=[files[1]])
    assert list(task.todo) == [str(files[1])] and list(task.working) == [str(files[0])]
    task.undo()
    assert task.todo == {} and task.working == {}


def test_undo_keeps_the_to_do_entries_added_after_the_run(tmp_path, files):
    empty = tmp_path / "empty.docx"
    empty.write_bytes(build_dirty("empty_body"))
    task = safe_task(tmp_path)
    task.attach([*files, empty])
    press(task, "remove_highlight")
    press(task, "title_property", TITLE)
    task.hand_off([task.view()["left_untouched"][0]["path"]])          # put on the list after that run, by the user
    task.mode = "safe"
    task.undo()
    assert [e["name"] for e in task.todo.values()] == ["empty.docx"]
    assert task.view()["left_untouched"] == []                         # the run before left nothing untouched


def test_undo_restores_what_the_run_before_left_untouched(tmp_path, files):
    empty = tmp_path / "empty.docx"
    empty.write_bytes(build_dirty("empty_body"))
    task = safe_task(tmp_path)
    task.attach([files[0], empty])
    press(task, "title_property", TITLE)
    left = task.view()["left_untouched"]
    press(task, "remove_highlight")
    assert task.view()["left_untouched"] != left
    task.undo()
    assert task.view()["left_untouched"] == left


def test_undo_waits_for_a_preview_to_be_discarded(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_highlight")
    task.perform("recipe", recipe="remove_comments", inputs={})
    assert task.view()["working"]["undo_blocked"] == "Discard the preview first."
    with pytest.raises(ValueError, match="Discard the preview first"):
        task.undo()
    task.discard_preview()
    task.undo()
    assert task.working == {}


def test_undo_refuses_when_a_copy_it_would_go_back_to_is_gone(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_highlight")
    gone = Path(next(iter(task.working.values())))
    press(task, "remove_comments")
    working, points = dict(task.working), len(task.undo_points)
    gone.unlink()
    with pytest.raises(ValueError, match="no longer where they were saved"):
        task.undo()
    assert task.working == working and len(task.undo_points) == points  # nothing changed


def test_a_removed_document_comes_back_as_its_original_not_an_old_copy(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_highlight")
    press(task, "remove_comments")
    task.remove(files[1])
    task.attach([files[1]])
    task.undo()
    assert list(task.working) == [str(files[0])] and task.current_of(files[1]) == files[1]


def test_start_over_clears_what_can_be_undone(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_highlight")
    task.start_over()
    assert task.undo_points == [] and task.view()["working"]["undo"] == ""


def test_undo_points_survive_a_restart_and_a_malformed_one_is_dropped(tmp_path, files):
    journal = tmp_path / "conversation.json"
    task = safe_task(tmp_path, journal=journal)
    task.attach(files)
    press(task, "remove_highlight")
    first = dict(task.working)
    press(task, "remove_comments")
    restored = safe_task(tmp_path, journal=journal)
    assert restored.restore() and len(restored.undo_points) == 2
    restored.undo()
    assert restored.working == first and restored.generation == 1

    data = json.loads(journal.read_text(encoding="utf-8"))
    data["undo"][0]["working"] = ["not", "a", "map"]
    journal.write_text(json.dumps(data), encoding="utf-8")
    again = safe_task(tmp_path, journal=journal)
    assert again.restore() and again.undo_points == []                 # nothing restored wrongly
    assert again.view()["working"]["undo"] == ""


def test_two_runs_in_one_second_never_share_a_folder(tmp_path, files, monkeypatch):
    # A run reads the copies the last one saved, so a run folder shared within one second would hold the run's own
    # inputs, and the run would be refused; Discard and pressing again makes that quick.
    from datetime import datetime

    from spec_surgeon import gui

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 26, 12, 0, 0)
    monkeypatch.setattr(gui, "datetime", Frozen)
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_highlight")
    press(task, "remove_comments")
    assert task.generation == 2
    folders = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert folders[:2] == ["20261026-120000", "20261026-120000-2"]
