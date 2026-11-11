"""Where each document stands (the owner's request of 2026-11-10: "the app needs a way to track what worked on what
spec(s) and what didn't"): one record of every operation asked for and each document's outcome under it — done (in
which mode, with which marks), failed (the reason kept, not forgotten at the next button) or not run (left out,
waiting, attached later) — kept in the journal, taken back by Undo, and shown as the window's grid
(`spec_surgeon/standing.py`, `DocumentTask.standing`, the view's `standing`). It describes and never decides: what
the buttons, the conversation and the to-do list do is unchanged."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fake_claude import FakeClient, resp, tool
from make_fixtures import build_dirty
from spec_surgeon.document_task import (FAILED_COPY_TAKEN, LEFT_OUT_REASON, SET_ASIDE_REST, WAITING_REASON, WORK_TODO,
                                        DocumentTask)
from spec_surgeon.gui import Session
from spec_surgeon.standing import ATTACHED_LATER, DONE, FAILED, NOT_PART, NOT_RUN, Standing
from test_document_task import REQUEST, SPEC, document
from test_going_back import TITLE, files, press, safe_task  # noqa: F401
from test_set_aside_bin import _refusing, park, submit, task_for, three
from standing_helpers import case_of, owe, owed, untouched
from test_word_actions import word  # noqa: F401


# ---- the record itself ------------------------------------------------------------------------------------------------

def test_a_cell_is_its_last_attempt_and_keeps_every_one():
    record = Standing()
    op = record.open("Remove all headers and footers", mode="safe", recipe="remove_headers", documents=["a", "b"])
    record.record(op, "a", DONE, mode="safe")
    record.record(op, "b", FAILED, mode="safe", why="refused", reason="It has no header.")
    assert record.state(op, "a") == DONE and record.state(op, "b") == FAILED and record.state(op, "c") == ""
    assert record.owed() == [(op, "b")] and record.owed(["a"]) == []
    approved = record.seq                              # a later approval: the pass that works on b
    record.record(op, "b", DONE, mode="default", since=approved)
    assert record.owed() == []
    cell = record.find(op)["cells"]["b"]
    assert [(a["state"], a["mode"]) for a in cell] == [(FAILED, "safe"), (DONE, "default")]
    assert cell[0]["reason"] == "It has no header."


def test_within_one_approval_a_later_word_replaces_the_earlier_and_remembers_why_it_failed_first():
    record = Standing()
    op = record.open("Make the title bold", mode="default", documents=["a"])
    approved = record.seq
    record.record(op, "a", FAILED, mode="default", why="failed", reason="Its copy could not be made: V2.",
                  since=approved)
    record.record(op, "a", FAILED, mode="default", why="failed", reason="It still needed work.", since=approved)
    assert [a["reason"] for a in record.find(op)["cells"]["a"]] == ["It still needed work."]
    record.record(op, "a", DONE, mode="default", since=approved)          # the second check fixed it
    [attempt] = record.find(op)["cells"]["a"]
    assert attempt["state"] == DONE and attempt["first"] == "It still needed work."


def test_rewind_takes_back_what_was_recorded_since_and_drops_an_operation_left_empty():
    record = Standing()
    first = record.open("One", mode="safe", documents=["a", "b"])
    record.record(first, "a", DONE, mode="safe")
    record.record(first, "b", FAILED, mode="safe", reason="No.")
    point = record.seq
    second = record.open("Two", mode="safe", documents=["a", "b"])
    record.record(second, "a", DONE, mode="safe")
    record.record(first, "b", DONE, mode="default", since=point)          # b's fix, after the point
    record.rewind(point)
    assert [op["id"] for op in record.operations] == [first] and not record.has(second)
    assert record.state(first, "b") == FAILED and record.seq > point      # numbers are never given again


def test_forgetting_a_document_takes_its_cells_and_an_operation_that_was_its_alone():
    record = Standing()
    both = record.open("Both", mode="safe", documents=["a", "b"])
    record.record(both, "a", DONE, mode="safe")
    record.record(both, "b", DONE, mode="safe")
    alone = record.open("Only b", mode="obliterate", documents=["a", "b"])
    record.record(alone, "b", DONE, mode="obliterate")
    record.forget("b")
    assert [op["id"] for op in record.operations] == [both]
    assert record.find(both)["documents"] == ["a"] and "b" not in record.find(both)["cells"]


def test_the_view_is_a_grid_over_the_documents_attached_now():
    record = Standing()
    first = record.open("Remove all comments", mode="safe", recipe="remove_comments", documents=["a", "b"])
    record.record(first, "a", DONE, mode="safe")
    record.record(first, "b", FAILED, mode="safe", why="refused", reason="No.")
    second = record.open("Run in Word on a.docx: repair.", mode="obliterate", documents=["a", "b"])
    record.record(second, "a", DONE, mode="obliterate", marks=["UNPROVEN (Word made it)"])
    view = record.view([("a", "a.docx"), ("b", "b.docx"), ("c", "c.docx")])
    assert [(o["number"], o["request"], o["mode"], o["recipe"], o["done"], o["failed"]) for o in view["operations"]] \
        == [(1, "Remove all comments", "safe", "remove_comments", 1, 1),
            (2, "Run in Word on a.docx: repair.", "obliterate", "", 1, 0)]
    a, b, c = view["documents"]
    assert [cell["state"] for cell in a["cells"]] == [DONE, DONE] and a["owed"] == 0
    assert a["cells"][1]["marks"] == ["UNPROVEN (Word made it)"]
    assert [(cell["state"], cell["why"]) for cell in b["cells"]] == [(FAILED, "refused"), (NOT_RUN, NOT_PART)]
    assert b["owed"] == 1 and b["cells"][1]["reason"] == "This was not run on it." and b["cells"][1]["attempts"] == []
    assert [(cell["state"], cell["why"]) for cell in c["cells"]] == [(NOT_RUN, ATTACHED_LATER)] * 2
    assert view["owed"] == {"cells": 1, "documents": 1}
    # an operation none of the documents listed holds an attempt under is not a column
    assert record.view([("c", "c.docx")]) == {"operations": [], "documents": [
        {"path": "c", "name": "c.docx", "cells": [], "owed": 0}], "owed": {"cells": 0, "documents": 0}}


def test_the_journal_keeps_the_record_and_one_that_does_not_read_is_none():
    record = Standing()
    op = record.open("One", mode="default", documents=["a"])
    record.record(op, "a", FAILED, mode="default", why="failed", reason="No.")
    record.record(op, "a", DONE, mode="obliterate", marks=["V2 (Text preservation)"], note="taken", since=record.seq)
    kept = json.loads(json.dumps(record.to_json()))
    again = Standing.from_json(kept)
    assert again.to_json() == record.to_json() and again.seq == record.seq
    for broken in (None, [], {"seq": 1}, {**kept, "seq": "3"},
                   {**kept, "operations": [{**kept["operations"][0], "mode": "turbo"}]},
                   {**kept, "operations": [{**kept["operations"][0], "cells": {"a": []}}]},
                   {**kept, "operations": kept["operations"] * 2}):
        assert Standing.from_json(broken).operations == []


def test_a_state_or_mode_the_record_does_not_know_is_refused():
    record = Standing()
    with pytest.raises(ValueError):
        record.open("One", mode="turbo")
    op = record.open("One", mode="safe")
    with pytest.raises(ValueError):
        record.record(op, "a", "maybe", mode="safe")
    with pytest.raises(KeyError):
        record.record("gone", "a", DONE, mode="safe")


# ---- Safe mode ----------------------------------------------------------------------------------------------------------

def grid(task) -> dict:
    return task.view()["standing"]


def row(task, path) -> dict:
    return next(r for r in grid(task)["documents"] if r["path"] == str(path))


def states(task, path) -> list:
    return [cell["state"] for cell in row(task, path)["cells"]]


@pytest.fixture
def empty(tmp_path) -> Path:
    path = tmp_path / "empty.docx"
    path.write_bytes(build_dirty("empty_body"))
    return path


def test_a_button_that_fails_on_one_document_is_still_recorded_after_the_next_button(tmp_path, files, empty):
    """The owner's case: the first button fails on one of the documents, the next works on all of them. The Left
    untouched card forgets the first at the second press; the record does not."""
    task = safe_task(tmp_path)
    task.attach([*files, empty])
    press(task, "title_property", TITLE)
    reason = untouched(task)[0]["reason"]
    press(task, "remove_comments")
    assert untouched(task) == []                                    # the newest button changed every document
    view = grid(task)
    assert [(o["number"], o["recipe"], o["mode"], o["done"], o["failed"]) for o in view["operations"]] == [
        (1, "title_property", "safe", 2, 1), (2, "remove_comments", "safe", 3, 0)]
    assert view["operations"][1]["request"] == "Remove all comments"
    assert states(task, files[0]) == [DONE, DONE] and states(task, files[1]) == [DONE, DONE]
    failed, done = row(task, empty)["cells"]
    assert (failed["state"], failed["mode"], failed["why"], failed["reason"]) == (FAILED, "safe", "refused", reason)
    assert (done["state"], done["mode"]) == (DONE, "safe")
    assert view["owed"] == {"cells": 1, "documents": 1} and row(task, empty)["owed"] == 1
    assert task.todo == {}                                          # the record describes; nothing else changed


def test_a_button_no_document_can_take_is_recorded_though_nothing_was_approved(tmp_path, empty):
    task = safe_task(tmp_path)
    task.attach([empty])
    view = task.perform("recipe", recipe="title_property", inputs=TITLE)
    assert view["action"] == "" and task.results == {}              # nothing to create
    [operation] = grid(task)["operations"]
    assert (operation["recipe"], operation["done"], operation["failed"]) == ("title_property", 0, 1)
    assert states(task, empty) == [FAILED] and row(task, empty)["cells"][0]["reason"]
    if view["stage"] == "preview":
        task.discard_preview()                                      # it was tried: withdrawing its preview keeps that
        assert states(task, empty) == [FAILED]
    assert task.view()["working"]["done"] == []                     # no run delivered copies


def test_the_red_x_is_recorded_as_set_aside_and_later_buttons_as_not_run_on_it(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_highlight", hold=[files[1]])
    held = row(task, files[1])
    assert (held["cells"][0]["state"], held["cells"][0]["why"]) == (FAILED, "held")
    assert held["badges"] == ["set aside"] and row(task, files[0])["badges"] == []
    assert task.todo[str(files[1])]["operation"] == grid(task)["operations"][0]["id"]
    press(task, "remove_comments")
    waiting = row(task, files[1])["cells"][1]
    assert (waiting["state"], waiting["why"], waiting["reason"]) == (NOT_RUN, "set aside", WAITING_REASON)
    assert states(task, files[0]) == [DONE, DONE] and grid(task)["owed"] == {"cells": 1, "documents": 1}


def test_setting_every_document_aside_records_each_and_no_copy(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_highlight", hold=files)
    assert task.results == {} and [states(task, f) for f in files] == [[FAILED], [FAILED]]
    assert all(row(task, f)["cells"][0]["why"] == "held" for f in files)


def test_undo_takes_the_newest_operation_off_the_record(tmp_path, files, empty):
    task = safe_task(tmp_path)
    task.attach([*files, empty])
    press(task, "title_property", TITLE)
    press(task, "remove_comments")
    task.undo()
    assert [o["recipe"] for o in grid(task)["operations"]] == ["title_property"]
    assert states(task, empty) == [FAILED] and states(task, files[0]) == [DONE]
    task.undo()
    assert grid(task)["operations"] == [] and task.standing.operations == []


def test_the_record_survives_a_restart(tmp_path, files, empty):
    journal = tmp_path / "journal.json"
    task = safe_task(tmp_path, journal=journal)
    task.attach([*files, empty])
    press(task, "title_property", TITLE)
    press(task, "remove_comments")
    shown = grid(task)
    again = safe_task(tmp_path, journal=journal)
    assert again.restore() and again.view()["standing"] == shown
    again.undo()                                                    # and Undo still takes the newest off
    assert [o["recipe"] for o in again.view()["standing"]["operations"]] == ["title_property"]


def test_a_journal_whose_record_does_not_read_restores_without_one(tmp_path, files):
    journal = tmp_path / "journal.json"
    task = safe_task(tmp_path, journal=journal)
    task.attach(files)
    press(task, "remove_comments")
    data = json.loads(journal.read_text(encoding="utf-8"))
    assert data["standing"]["operations"] and all("standing" in point for point in data["undo"])
    data["standing"] = {"operations": "all of them"}
    journal.write_text(json.dumps(data), encoding="utf-8")
    again = safe_task(tmp_path, journal=journal)
    assert again.restore() and again.view()["standing"]["operations"] == []
    assert again.working and again.view()["working"]["done"]        # the conversation itself is as it was


def test_undoing_a_run_from_before_the_record_shows_nothing_as_done(tmp_path, files):
    journal = tmp_path / "journal.json"
    task = safe_task(tmp_path, journal=journal)
    task.attach(files)
    press(task, "remove_comments")
    press(task, "remove_highlight")
    data = json.loads(journal.read_text(encoding="utf-8"))
    for point in data["undo"]:
        del point["standing"]                                       # a journal an earlier version wrote
    journal.write_text(json.dumps(data), encoding="utf-8")
    again = safe_task(tmp_path, journal=journal)
    assert again.restore()
    again.undo()
    assert again.view()["standing"]["operations"] == []


def test_a_removed_document_attached_again_reads_as_attached_later(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_comments")
    task.remove(files[1])
    assert [r["name"] for r in grid(task)["documents"]] == [files[0].name]
    task.attach([files[1]])
    again = row(task, files[1])["cells"][0]
    assert (again["state"], again["why"]) == (NOT_RUN, ATTACHED_LATER)
    press(task, "remove_highlight")
    assert states(task, files[1]) == [NOT_RUN, DONE] and states(task, files[0]) == [DONE, DONE]


def test_start_over_and_removing_every_document_clear_the_record(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_comments")
    task.start_over()
    assert grid(task)["operations"] == [] and [r["cells"] for r in grid(task)["documents"]] == [[], []]
    press(task, "remove_comments")
    task.remove_documents()
    assert grid(task) == {"operations": [], "documents": [], "owed": {"cells": 0, "documents": 0}}


def test_the_same_button_twice_is_two_operations(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_comments")
    press(task, "remove_comments")
    assert [o["number"] for o in grid(task)["operations"]] == [1, 2] and states(task, files[0]) == [DONE, DONE]


# ---- Default mode -------------------------------------------------------------------------------------------------------

def test_a_document_the_plan_refuses_fails_and_the_pass_that_fixes_it_settles_the_same_cell(tmp_path):
    task, source, easy = _refusing(tmp_path, submit(tid="s2"))
    view = task.perform("send", text="Make the title bold")
    assert grid(task)["operations"] == []                           # nothing is recorded before the approval
    task.perform("primary", token=view["token"])
    [operation] = grid(task)["operations"]
    assert (operation["request"], operation["mode"], operation["done"], operation["failed"]) == (
        "Make the title bold", "default", 1, 1)
    refused = row(task, source)["cells"][0]
    assert (refused["state"], refused["why"]) == (FAILED, "refused") and "MissingStyle" in refused["reason"]
    # the engine could not change it, so it stays in the batch: only the user's own set-aside takes a document out
    assert row(task, source)["badges"] == [] and task.todo == {} and states(task, easy) == [DONE]
    assert source in task.documents() and owed(task)[0]["operation"] == operation["id"]
    # the pass over the to-do list: its copy settles the cell of the operation it was set aside from
    task.perform("send", text=WORK_TODO)
    assert task.focus["operations"] == {str(source): operation["id"]}
    view = task.perform("send", text="Use Heading 1 instead.")
    task.perform("primary", token=view["token"])
    assert [o["id"] for o in grid(task)["operations"]] == [operation["id"]]         # no new column for the fix
    fixed = row(task, source)["cells"][0]
    assert (fixed["state"], fixed["mode"]) == (DONE, "default")
    assert [(a["state"], a["why"]) for a in fixed["attempts"]] == [(FAILED, "refused"), (DONE, "")]
    assert grid(task)["owed"] == {"cells": 0, "documents": 0} and row(task, source)["badges"] == []
    assert states(task, easy) == [DONE]                             # the pass said nothing of the others
    # Undo takes the fix back, and the cell is owed again
    task.undo()
    assert row(task, source)["cells"][0]["state"] == FAILED


def test_dropping_a_pass_leaves_the_cell_as_it_was(tmp_path):
    task, source, _easy = _refusing(tmp_path)
    view = task.perform("send", text="Make the title bold")
    task.perform("primary", token=view["token"])
    before = grid(task)
    task.perform("send", text=WORK_TODO)
    task.discard_preview()
    assert task.todo == {} and task.focus is None and owed(task)[0]["operation"] == before["operations"][0]["id"]
    assert grid(task) == before


def test_a_document_set_aside_at_the_users_word_fails_that_request_and_is_not_run_under_the_next(tmp_path):
    docs = three(tmp_path)
    task = task_for(tmp_path, FakeClient(park("20 Port.docx", reason="waiting on the owner"), submit(),
                                         submit(tid="s2")))
    task.attach(docs)
    view = task.perform("send", text="Set 20 aside for later. Make the title bold.")
    asked = row(task, docs[0])["cells"][0]
    assert (asked["state"], asked["why"]) == (FAILED, "asked") and "waiting on the owner" in asked["reason"]
    task.perform("primary", token=view["token"])
    assert [states(task, d) for d in docs] == [[FAILED], [DONE], [DONE]]
    assert len(row(task, docs[0])["cells"][0]["attempts"]) == 1
    view = task.perform("send", text="Now make the title italic.")
    task.perform("primary", token=view["token"])
    assert [o["request"] for o in grid(task)["operations"]] == [
        "Set 20 aside for later. Make the title bold.", "Now make the title italic."]
    waiting = row(task, docs[0])["cells"][1]
    assert (waiting["state"], waiting["why"]) == (NOT_RUN, "set aside")
    # Undo takes back the second request alone: the set-aside came before the first approval and stays recorded
    task.undo()
    task.undo()
    assert [states(task, d) for d in docs] == [[FAILED], [NOT_RUN], [NOT_RUN]]
    assert row(task, docs[1])["cells"][0]["why"] == NOT_PART


def test_a_document_the_user_left_out_of_a_request_is_not_run_and_not_owed(tmp_path):
    docs = three(tmp_path)
    task = task_for(tmp_path, FakeClient(submit(leave_out=["22 Michigan.docx"])))
    task.attach(docs)
    view = task.perform("send", text="Make the title bold, but not in 22.")
    task.perform("primary", token=view["token"])
    left = row(task, docs[2])["cells"][0]
    assert (left["state"], left["why"], left["reason"]) == (NOT_RUN, "left out", LEFT_OUT_REASON)
    assert grid(task)["owed"] == {"cells": 0, "documents": 0} and row(task, docs[2])["badges"] == []


def test_recoverys_leftover_is_recorded_with_the_reason_it_was_set_aside_for(tmp_path):
    from spec_surgeon import recovery
    from test_recovery import candidate, margin, opts, spec, submit as submit_repair
    from test_recovery_resume import distinct_failures
    paths = [document(tmp_path / name) for name in ("First.docx", "Second.docx")]
    goal = spec([margin()])
    fake = FakeClient(submit_repair(candidate(goal)),
                      resp(tool("need_user", {"question": "Use these margins on the second document?"})))
    task = task_for(tmp_path, fake)
    task.attach(paths)
    task.decisions = task.word_sources = [goal["request"]]
    result = recovery.recover(paths, goal["request"], tmp_path / "recovery", spec=goal, options=opts(),
                              prior_failures=distinct_failures(paths), client=fake)
    task.session._adopt_recovery(result)
    task._settle(lambda _: None)
    task.perform("primary", token=task.action_token())              # Save the ready copy
    task.perform("send", text=SET_ASIDE_REST, chip=True)
    [operation] = grid(task)["operations"]
    assert (operation["request"], operation["done"], operation["failed"]) == (goal["request"], 1, 1)
    assert states(task, paths[0]) == [DONE]
    leftover = row(task, paths[1])["cells"][0]
    assert leftover["state"] == FAILED and leftover["reason"].startswith("It still needed work when you left it for later")
    assert len(leftover["attempts"]) == 1


# ---- Obliterate -----------------------------------------------------------------------------------------------------------

def test_a_copy_delivered_unverified_is_done_with_its_mark_not_owed(tmp_path, monkeypatch):
    from spec_surgeon import batch as batch_module
    from spec_surgeon.verify.suite import CheckResult
    real = batch_module.run_suite

    def failing(src, out, plan, s, o):
        rec = real(src, out, plan, s, o)
        rec.set(CheckResult("V2", "Text preservation", "FAIL", "planted"))
        return rec
    monkeypatch.setattr(batch_module, "run_suite", failing)
    path = document(tmp_path / "Spec.docx")
    task = DocumentTask(Session(client=FakeClient(resp(tool("submit_editspec", {"spec": SPEC}))),
                                out_root=tmp_path / "out", visual_check=False), render=False, obliterate_allowed=True)
    task.set_mode("obliterate", acknowledged=True)
    task.attach([path])
    view = task.perform("send", text=REQUEST)
    task.perform("primary", token=view["token"])
    cell = row(task, path)["cells"][0]
    assert (cell["state"], cell["mode"], cell["marks"]) == (DONE, "obliterate", ["V2 (Text preservation)"])
    assert row(task, path)["badges"] == ["UNVERIFIED"] and grid(task)["owed"] == {"cells": 0, "documents": 0}


def test_taking_the_copy_that_failed_settles_the_cell_and_says_how(tmp_path, monkeypatch):
    from test_obliterate_handoff import straggler
    task, _fake, good, source = straggler(tmp_path, monkeypatch, obliterate_allowed=True)
    operation = owed(task)[0]["operation"]
    assert states(task, good) == [DONE]
    assert task.standing.state(operation, str(source)) == FAILED
    task.obliterate_todo(str(source), acknowledged=True)
    task.use_failed_copy()
    cell = row(task, source)["cells"][0]
    assert (cell["state"], cell["mode"], cell["note"]) == (DONE, "obliterate", FAILED_COPY_TAKEN)
    assert cell["marks"] and [a["state"] for a in cell["attempts"]] == [FAILED, DONE]
    assert [o["id"] for o in grid(task)["operations"]] == [operation]
    task.undo()
    assert row(task, source)["cells"][0]["state"] == FAILED


def test_a_word_action_inside_a_pass_is_its_own_operation_and_the_plan_then_settles_the_owed_cell(tmp_path, word):
    """Obliterate's "clear the obstacle, then the same plan" in two requests: Word's action is something done to the
    document, a column of its own; the document still owes the request it was set aside from until the plan runs."""
    fake = FakeClient(resp(tool("submit_editspec", {"spec": SPEC})))
    a, b = document(tmp_path / "a.docx"), document(tmp_path / "b.docx")
    task = DocumentTask(Session(client=fake, out_root=tmp_path / "out", visual_check=False), render=False,
                        obliterate_allowed=True)
    task.attach([a, b])
    owed = task.standing.open(REQUEST, mode="default", documents=[str(a), str(b)])
    task.standing.record(owed, str(a), DONE, mode="default")
    task.standing.record(owed, str(b), FAILED, mode="default", why="refused", reason="A field stopped the plan.")
    task.todo[str(b)] = {"name": "b.docx", "why": "refused", "reason": "A field stopped the plan.",
                         "decisions": [REQUEST], "seen": ["Make the title bold."], "operation": owed,
                         "case": {"code": "field", "reason": "a field the op would not guess about",
                                  "plan": json.dumps(SPEC)}}
    task._sync()
    task.obliterate_todo(str(b), acknowledged=True)
    view = task.perform("word_actions", path=str(b), actions=["repair"])
    view = task.perform("primary", token=view["token"])
    first, second = grid(task)["operations"]
    assert first["id"] == owed and second["request"].startswith("Run in Word on b.docx") and second["mode"] == "obliterate"
    assert states(task, b) == [FAILED, DONE] and states(task, a) == [DONE, NOT_RUN]
    assert row(task, a)["cells"][1]["why"] == NOT_PART and row(task, b)["badges"] == ["UNPROVEN"]
    view = task.perform("primary", token=view["token"])              # Continue: the request, on the copy Word made
    task.perform("primary", token=view["token"])
    assert len(grid(task)["operations"]) == 2
    settled = row(task, b)["cells"][0]
    assert (settled["state"], settled["mode"]) == (DONE, "obliterate")
    assert [x["state"] for x in settled["attempts"]] == [FAILED, DONE]
    # a cell carries the marks of its own delivery: the plan's copy was checked, so its cell is a clean check; the
    # mark Word's copy left is the document's from then on, on its row and on the cell that made it
    assert settled["marks"] == [] and row(task, b)["badges"] == ["UNPROVEN"]
    assert row(task, b)["cells"][1]["marks"] and "UNPROVEN" in row(task, b)["cells"][1]["marks"][0]


def test_a_safe_buttons_failure_handed_to_default_mode_is_settled_in_its_own_cell(tmp_path, files):
    """The ladder's first step up: what a button could not change is worked in Default mode, and the copy that pass
    delivers turns the button's own cell to done — in Default mode, with both attempts — instead of adding a column."""
    from docx import Document
    odd = tmp_path / "odd.docx"                          # no SECTION line: the title button cannot read its number
    doc = Document()
    doc.add_paragraph("FIRE SUPPRESSION")
    doc.add_paragraph("Keep this technical wording intact.")
    doc.save(odd)
    task = DocumentTask(Session(client=FakeClient(submit()), out_root=tmp_path / "out", visual_check=False),
                        render=False, mode="safe")
    task.attach([files[0], odd])
    press(task, "title_property", TITLE)
    [operation] = grid(task)["operations"]
    assert states(task, files[0]) == [DONE] and states(task, odd) == [FAILED]
    task.work_todo(operation=operation["id"], path=str(odd))        # Work on it in Default mode: the pass begins
    assert task.mode == "default" and task.focus["operations"] == {str(odd): operation["id"]}
    assert row(task, odd)["badges"] == [] and task.todo == {}
    view = task.perform("send", text="Make its FIRE SUPPRESSION title bold instead.")
    task.perform("primary", token=view["token"])
    [still] = grid(task)["operations"]
    assert (still["id"], still["mode"], still["recipe"]) == (operation["id"], "safe", "title_property")
    cell = row(task, odd)["cells"][0]
    assert (cell["state"], cell["mode"]) == (DONE, "default")
    assert [(a["state"], a["mode"]) for a in cell["attempts"]] == [(FAILED, "safe"), (DONE, "default")]
    assert grid(task)["owed"] == {"cells": 0, "documents": 0}


def test_after_a_restart_and_an_undo_a_failure_is_still_worked_on_in_its_own_cell(tmp_path, files, empty):
    """What an earlier button could not do is in the journal's record, so it is still on the to-do list after a
    restart and an Undo of a later button, and working on it then settles the cell it failed in."""
    journal = tmp_path / "journal.json"
    task = safe_task(tmp_path, journal=journal)
    task.attach([files[0], empty])
    press(task, "title_property", TITLE)
    operation = grid(task)["operations"][0]["id"]
    press(task, "remove_comments")
    again = safe_task(tmp_path, journal=journal)
    assert again.restore()
    again.undo()                                                    # back to before the second button
    assert [e["name"] for e in untouched(again)] == ["empty.docx"]
    again.work_todo(path=str(empty))
    assert again.mode == "default" and again.focus["operations"] == {str(empty): operation}


# ---- the batch rule and the hand-up (the owner's decisions of 2026-11-11) ----------------------------------------------

def test_rule_1_a_document_the_engine_could_not_change_gets_the_next_request_with_the_others(tmp_path):
    """Only the user's own set-aside takes a document out of the batch. One the plan refused stays in it: the next
    request is planned on it too, and the model is told what it still owes, as context."""
    task, source, easy = _refusing(tmp_path, submit(tid="s2"))
    view = task.perform("send", text="Make the title bold")
    task.perform("primary", token=view["token"])
    assert states(task, source) == [FAILED] and task.todo == {}
    view = task.perform("send", text="Now make the FIRE SUPPRESSION title bold.")
    assert sorted(Path(p).name for p in task.session.planned_files()) == ["easy.docx", "source.docx"]
    told = task.session.client.requests[1]["messages"][0]["content"]
    assert "STILL ON THE USER'S TO-DO LIST, FOR CONTEXT" in told and "<app_note>" in told
    assert "- source.docx: “Make the title bold” — The plan could not be applied to it" in told
    assert "SET ASIDE BY THE USER" not in told                      # nobody set anything aside
    task.perform("primary", token=view["token"])
    # it got the second request; the first is still owed, in its own cell
    assert states(task, source) == [FAILED, DONE] and states(task, easy) == [DONE, DONE]
    assert [(e["name"], e["request"]) for e in owed(task)] == [("source.docx", "Make the title bold")]


def test_rule_1_a_document_the_user_set_aside_waits_out_of_the_batch_and_what_it_missed_is_not_owed(tmp_path):
    docs = three(tmp_path)[:2]
    task = task_for(tmp_path, FakeClient(submit(), submit(tid="s2")))
    task.attach(docs)
    view = task.perform("send", text=REQUEST)
    task.perform("primary", token=view["token"], hold=[str(docs[1])])       # the red X
    view = task.perform("send", text="And once more.")
    assert [Path(p).name for p in task.session.planned_files()] == [docs[0].name]
    told = task.session.client.requests[1]["messages"][0]["content"]
    assert "SET ASIDE BY THE USER" in told and "STILL ON THE USER'S TO-DO LIST" not in told
    task.perform("primary", token=view["token"])
    held = row(task, docs[1])
    assert [(c["state"], c["why"]) for c in held["cells"]] == [(FAILED, "held"), (NOT_RUN, "set aside")]
    assert held["owed"] == 1 and held["badges"] == ["set aside"]


def test_rule_3_a_pass_takes_one_operation_at_a_time_the_oldest_first_or_the_one_named(tmp_path):
    docs = three(tmp_path)
    task = task_for(tmp_path, FakeClient())
    task.attach(docs)
    first = owe(task, docs[0], "Remove the headers.", reason="It has none.")
    task.standing.record(first, str(docs[1]), FAILED, mode="default", why="refused", reason="It has none either.",
                         decisions=["Remove the headers."])
    second = owe(task, docs[2], "Match the template.", reason="No SECTION line.")
    assert task.view()["chips"][0] == WORK_TODO
    task.perform("send", text=WORK_TODO)
    assert task.focus["documents"] == [str(docs[0]), str(docs[1])] and task.decisions == ["Remove the headers."]
    assert task.focus["operations"] == {str(docs[0]): first, str(docs[1]): first}
    assert any("1 more on your to-do list waits for the next pass." in m["text"] for m in task.messages)
    with pytest.raises(ValueError, match="Finish the pass"):
        task.work_todo(operation=second)
    task.discard_preview()                                          # the pass dropped: nothing changed, all still owed
    assert len(owed(task)) == 3 and task.focus is None
    task.work_todo(operation=second)                                # the one named
    assert task.focus["documents"] == [str(docs[2])] and task.decisions == ["Match the template."]
    task.discard_preview()
    task.work_todo(operation=first, path=str(docs[1]))              # one cell alone
    assert task.focus["documents"] == [str(docs[1])]
    task.discard_preview()
    with pytest.raises(ValueError, match="not on your to-do list"):
        task.work_todo(operation=second, path=str(docs[0]))         # it does not owe that one


def test_rule_4_obliterate_this_one_is_the_cells_with_that_cells_own_case_file(tmp_path):
    a, b = document(tmp_path / "a.docx"), document(tmp_path / "b.docx")
    task = DocumentTask(Session(client=FakeClient(), out_root=tmp_path / "out", visual_check=False), render=False,
                        obliterate_allowed=True)
    task.attach([a, b])
    first = owe(task, b, "Remove the headers.", reason="A field stopped it.", case={"code": "field", "reason": "one"})
    second = owe(task, b, "Match the template.", reason="A text box stopped it.",
                 case={"code": "textbox", "reason": "two"})
    assert [c["obliterate"] for c in row(task, b)["cells"]] == ["", ""]     # each failed cell offers it
    task.obliterate_todo(str(b), acknowledged=True, operation=second)
    assert task.mode == "obliterate" and task.focus["operations"] == {str(b): second}
    assert task.decisions == ["Match the template."] and task.focus["case"][str(b)]["code"] == "textbox"
    assert "textbox" in task._focus_context() and "A field stopped it." not in task._focus_context()
    task.discard_preview()
    task.obliterate_todo(str(b))                                    # none named: the oldest it owes
    assert task.focus["operations"] == {str(b): first} and task.focus["case"][str(b)]["code"] == "field"


def test_rule_5_not_needed_on_this_one_takes_a_cell_off_the_to_do_list_and_puts_it_back(tmp_path, files, empty):
    journal = tmp_path / "journal.json"
    task = safe_task(tmp_path, journal=journal)
    task.attach([files[0], empty])
    press(task, "title_property", TITLE)
    [item] = owed(task)
    with pytest.raises(ValueError, match="not on your to-do list"):
        task.waive(item["operation"], files[0])                     # that one is done: nothing to waive
    task.waive(item["operation"], empty)
    cell = row(task, empty)["cells"][0]
    assert cell["state"] == "waived" and [a["state"] for a in cell["attempts"]] == [FAILED, "waived"]
    assert owed(task) == [] and grid(task)["owed"] == {"cells": 0, "documents": 0}
    assert WORK_TODO not in task.view()["chips"] and "Nothing in the document changed" in task.messages[-1]["text"]
    with pytest.raises(ValueError, match="not on your to-do list"):
        task.work_todo(path=str(empty))
    again = safe_task(tmp_path, journal=journal)                    # the journal keeps the decision
    assert again.restore() and again.view()["standing"]["documents"][1]["cells"][0]["state"] == "waived"
    with pytest.raises(ValueError, match="not marked as not needed"):
        task.waive(item["operation"], files[0], on=False)
    task.waive(item["operation"], empty, on=False)
    assert owed(task) == [item] and "back on your to-do list" in task.messages[-1]["text"]
    task.work_todo(path=str(empty))
    with pytest.raises(ValueError, match="Finish the pass"):
        task.waive(item["operation"], empty)                        # not while the pass works on it


def test_rule_6_the_same_button_with_the_same_form_settles_its_own_earlier_failure_and_nothing_else_does(
        tmp_path, files, monkeypatch):
    from spec_surgeon.document_task import SAME_BUTTON_AGAIN
    from test_obliterate_handoff import fail_v2
    task = safe_task(tmp_path)
    task.attach(files)
    fail_v2(monkeypatch, files[1].name)
    press(task, "title_property", TITLE)                             # could not be done on the second document
    first = grid(task)["operations"][0]["id"]
    assert states(task, files[1]) == [FAILED] and files[1] in task.documents()
    monkeypatch.undo()
    press(task, "title_property", {"pattern": "{number} {title}"})   # the same button, another form: another thing
    assert states(task, files[1]) == [FAILED, DONE]
    press(task, "remove_comments")                                   # another button
    assert states(task, files[1]) == [FAILED, DONE, DONE] and len(owed(task)) == 1
    press(task, "title_property", TITLE)                             # the same button, the same form
    assert states(task, files[1]) == [DONE, DONE, DONE, DONE] and owed(task) == []
    settled = row(task, files[1])["cells"][0]
    assert settled["note"] == SAME_BUTTON_AGAIN and [a["state"] for a in settled["attempts"]] == [FAILED, DONE]
    assert [o["id"] for o in grid(task)["operations"]][0] == first and len(grid(task)["operations"]) == 4
    task.undo()                                                      # Undo takes the settling back with its run
    assert states(task, files[1]) == [FAILED, DONE, DONE]


def test_rule_7_the_model_is_told_what_is_owed_only_as_context_and_never_during_a_pass(tmp_path):
    docs = three(tmp_path)
    task = task_for(tmp_path, FakeClient(submit(), submit(tid="s2")))
    task.attach(docs)
    assert task._owed_note() == ""
    op = owe(task, docs[0], "Remove the headers.", reason="It has none.")
    note = task._owed_note()
    assert note.startswith("STILL ON THE USER'S TO-DO LIST, FOR CONTEXT:")
    assert "do not plan the earlier request for them unless the user asks for it" in note
    assert note.endswith("- 20 Port.docx: “Remove the headers.” — It has none.")
    task.waive(op, docs[0])
    assert task._owed_note() == ""                                   # not needed: nothing to tell
    task.waive(op, docs[0], on=False)
    task.perform("send", text=WORK_TODO)
    assert task._owed_note() == "" and "FROM THE USER'S TO-DO LIST" in task._focus_context()


def test_a_to_do_list_an_earlier_version_kept_is_read_into_the_record(tmp_path):
    """Then every entry was a document out of the batch. Now one the engine put there is back in the batch, owing
    what it owed, and one the user set aside still waits; each has its failed cell, with the request to work from."""
    docs = three(tmp_path)
    journal = tmp_path / "journal.json"
    task = task_for(tmp_path, FakeClient(), journal=journal)
    task.attach(docs)
    task.save()
    data = json.loads(journal.read_text(encoding="utf-8"))
    del data["standing"], data["op"], data["since"]
    data["todo"] = {
        str(docs[0]): {"name": docs[0].name, "decisions": ["Remove the headers."], "seen": ["Removes them."],
                       "why": "refused", "reason": "The plan could not be applied to it: no header.",
                       "case": {"code": "none", "reason": "no header", "op": "", "details": "", "checks": []}},
        str(docs[1]): {"name": docs[1].name, "decisions": ["Remove the headers."], "seen": [], "why": "held",
                       "reason": "You marked its preview as needing more work."}}
    journal.write_text(json.dumps(data), encoding="utf-8")
    again = task_for(tmp_path, FakeClient(), journal=journal)
    assert again.restore()
    assert list(again.todo) == [str(docs[1])] and docs[0] in again.documents() and docs[1] not in again.documents()
    listed = owed(again)
    assert [(e["name"], e["request"], e["why"]) for e in listed] == [
        (docs[0].name, "Remove the headers.", "refused"), (docs[1].name, "Remove the headers.", "held")]
    assert case_of(again, docs[0])["code"] == "none" and "case" not in again.todo[str(docs[1])]
    again.perform("send", text=WORK_TODO)                            # and the pass works from it
    assert again.decisions == ["Remove the headers."] and again.focus["documents"] == [str(docs[0])]


def test_a_restart_keeps_a_waived_cell_waived_for_a_document_still_set_aside(tmp_path):
    """The journal's to-do entries are read against the record: a cell the user waived while its document stayed set
    aside is not owed again after a restart."""
    docs = three(tmp_path)[:2]
    journal = tmp_path / "journal.json"
    task = task_for(tmp_path, FakeClient(submit()), journal=journal)
    task.attach(docs)
    view = task.perform("send", text=REQUEST)
    task.perform("primary", token=view["token"], hold=[str(docs[1])])       # the red X
    [item] = owed(task)
    task.waive(item["operation"], docs[1])
    assert owed(task) == [] and list(task.todo) == [str(docs[1])]            # not needed, and still set aside
    again = task_for(tmp_path, FakeClient(), journal=journal)
    assert again.restore()
    assert owed(again) == [] and list(again.todo) == [str(docs[1])]
    assert [o["id"] for o in again.view()["standing"]["operations"]] == [item["operation"]]


def test_setting_every_previewed_document_aside_begins_the_pass_over_them_not_an_older_operation(tmp_path):
    docs = three(tmp_path)[:2]
    task = task_for(tmp_path, FakeClient(submit(), submit(tid="s2")))
    task.attach(docs)
    older = owe(task, docs[0], "Remove the headers.", reason="It has none.")
    view = task.perform("send", text=REQUEST)
    task.perform("primary", token=view["token"], hold=[str(d) for d in docs])
    assert task.focus["documents"] == [str(d) for d in docs] and task.decisions == [REQUEST]
    assert older not in task.focus["operations"].values()
