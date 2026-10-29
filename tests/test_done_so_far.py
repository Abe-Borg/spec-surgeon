"""What you've done so far: the window lists each run that delivered copies under them, oldest first — the button's
sentence or the user's request, the button pressed (Safe mode's checkmark), and how many of the documents attached now
carry it, naming each other one with why (left untouched, set aside, attached later). Undo takes the newest off,
Start over and removing the last working copy clear it, and the journal keeps it (`DocumentTask.runs`, the view's
`working.done`)."""
from __future__ import annotations

import json

import pytest

from make_fixtures import build, build_dirty
from test_going_back import AGAIN, ITALIC, TITLE, chat_task, files, plan, press, safe_task  # noqa: F401
from test_document_task import REQUEST, document


def done(task) -> list:
    return task.view()["working"]["done"]


def test_each_button_joins_the_list_in_order_and_undo_takes_the_newest_off(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    assert done(task) == []
    press(task, "remove_comments")
    press(task, "accept_changes")
    press(task, "title_property", TITLE)
    steps = done(task)
    assert [s["recipe"] for s in steps] == ["remove_comments", "accept_changes", "title_property"]
    assert [s["request"] for s in steps[:2]] == ["Remove all comments", "Accept all tracked changes"]
    assert steps[2]["request"].startswith("Set the document title property")
    assert all(s["documents"] == s["of"] == 2 and s["not"] == [] for s in steps)

    task.undo()
    assert [s["recipe"] for s in done(task)] == ["remove_comments", "accept_changes"]
    task.undo()
    task.undo()
    assert done(task) == [] and task.runs == []


def test_a_discarded_preview_adds_nothing(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_comments")
    task.perform("recipe", recipe="remove_highlight", inputs={})
    task.discard_preview()
    assert [s["recipe"] for s in done(task)] == ["remove_comments"]


def test_a_document_the_x_set_aside_is_named_as_set_aside(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_highlight", hold=[files[1]])
    [step] = done(task)
    assert step["documents"] == 1 and step["of"] == 2
    assert step["not"] == [{"name": files[1].name, "why": "set aside"}]


def test_a_document_the_button_left_untouched_is_named_so(tmp_path, files):
    empty = tmp_path / "empty.docx"
    empty.write_bytes(build_dirty("empty_body"))
    task = safe_task(tmp_path)
    task.attach([files[0], empty])
    press(task, "title_property", TITLE)
    assert task.view()["left_untouched"]                               # the button could not change it
    [step] = done(task)
    assert (step["documents"], step["of"]) == (1, 2)
    assert step["not"] == [{"name": "empty.docx", "why": "left untouched"}]


def test_a_document_attached_after_a_run_is_named_as_attached_later(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach([files[0]])
    press(task, "remove_comments")
    task.attach([files[1]])
    press(task, "remove_highlight")
    first, second = done(task)
    assert (first["documents"], first["of"]) == (1, 2)
    assert first["not"] == [{"name": files[1].name, "why": "attached later"}]
    assert (second["documents"], second["of"]) == (2, 2) and second["not"] == []


def test_a_removed_document_attached_again_carries_none_of_the_runs(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_comments")
    press(task, "remove_highlight")
    task.remove(files[1])
    assert [(s["documents"], s["of"]) for s in done(task)] == [(1, 1), (1, 1)]
    task.attach([files[1]])
    for step in done(task):
        assert (step["documents"], step["of"]) == (1, 2)
        assert step["not"] == [{"name": files[1].name, "why": "attached later"}]
    task.undo()                                                         # the run before it, without the document too
    [step] = done(task)
    assert step["not"] == [{"name": files[1].name, "why": "attached later"}]


def test_start_over_and_removing_the_last_copy_clear_the_list(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_comments")
    task.start_over()
    assert done(task) == []
    press(task, "remove_comments")
    task.remove(files[0])
    task.remove(files[1])
    assert task.runs == [] and done(task) == []


def test_the_list_survives_a_restart_and_undo_still_takes_the_newest_off(tmp_path, files):
    journal = tmp_path / "conversation.json"
    task = safe_task(tmp_path, journal=journal)
    task.attach(files)
    press(task, "remove_comments")
    press(task, "remove_highlight", hold=[task.current_of(files[1])])   # the ✗ names the copy previewed
    assert done(task)[1]["not"] == [{"name": files[1].name, "why": "set aside"}]
    restored = safe_task(tmp_path, journal=journal)
    assert restored.restore()
    assert done(restored) == done(task)
    restored.undo()
    assert [s["recipe"] for s in done(restored)] == ["remove_comments"]


def test_a_list_the_journal_cannot_read_is_shown_as_none(tmp_path, files):
    journal = tmp_path / "conversation.json"
    task = safe_task(tmp_path, journal=journal)
    task.attach(files)
    press(task, "remove_comments")
    data = json.loads(journal.read_text(encoding="utf-8"))
    data["runs"][0]["not"] = {"x": "a reason the app never writes"}
    journal.write_text(json.dumps(data), encoding="utf-8")
    again = safe_task(tmp_path, journal=journal)
    assert again.restore() and again.working                         # the conversation itself is restored
    assert done(again) == []


def test_a_journal_from_before_the_list_restores_and_undoes(tmp_path, files):
    journal = tmp_path / "conversation.json"
    task = safe_task(tmp_path, journal=journal)
    task.attach(files)
    press(task, "remove_comments")
    press(task, "remove_highlight")
    data = json.loads(journal.read_text(encoding="utf-8"))
    del data["runs"]
    for point in data["undo"]:
        del point["runs"]
    journal.write_text(json.dumps(data), encoding="utf-8")
    old = safe_task(tmp_path, journal=journal)
    assert old.restore() and done(old) == [] and len(old.undo_points) == 2
    old.undo()
    assert done(old) == [] and old.generation == 1


@pytest.mark.parametrize("mode", ["default", "obliterate"])
def test_a_conversation_s_requests_join_the_list_without_a_button(tmp_path, mode):
    task = chat_task(tmp_path, plan(), plan(ITALIC, "t2"), mode=mode)
    task.attach([document(tmp_path / "Spec.docx")])
    view = task.perform("send", text=REQUEST)
    task.perform("primary", token=view["token"])
    view = task.perform("send", text=AGAIN)
    task.perform("primary", token=view["token"])
    assert [(s["request"], s["recipe"], s["documents"], s["of"]) for s in done(task)] == \
        [(REQUEST, "", 1, 1), (AGAIN, "", 1, 1)]
    task.undo()
    assert [s["request"] for s in done(task)] == [REQUEST]


def test_a_button_discarded_before_a_request_is_not_named_on_the_request(tmp_path):
    task = chat_task(tmp_path, plan())
    task.attach([document(tmp_path / "Spec.docx")])
    task.set_mode("safe")
    task.perform("recipe", recipe="remove_comments", inputs={})
    task.discard_preview()
    task.set_mode("default")
    view = task.perform("send", text=REQUEST)
    task.perform("primary", token=view["token"])
    assert [(s["request"], s["recipe"]) for s in done(task)] == [(REQUEST, "")]


def test_a_safe_button_and_a_request_share_one_list(tmp_path):
    task = chat_task(tmp_path, plan())
    task.attach([document(tmp_path / "Spec.docx")])
    view = task.perform("send", text=REQUEST)
    task.perform("primary", token=view["token"])
    task.set_mode("safe")
    press(task, "remove_comments")
    assert [(s["request"], s["recipe"]) for s in done(task)] == [(REQUEST, ""), ("Remove all comments",
                                                                                 "remove_comments")]


def test_the_list_is_read_from_the_documents_attached_now(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_comments")
    template = tmp_path / "template.docx"
    template.write_bytes(build("00 00 00", "TEMPLATE"))
    task.attach([template], template=True)
    [step] = done(task)
    assert (step["documents"], step["of"]) == (2, 2)                   # a template is not a document of the batch
