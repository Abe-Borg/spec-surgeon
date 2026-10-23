"""The to-do list as the place documents wait (the owner's request of 2026-10-23: "I want to be able to tell the llm to
set certain files aside for later processing", and "does the app put files that can't be processed or those I set
aside in some kind of bin, so I can come back to them?"). Before this, only the red X on a preview put a document on
the to-do list; "leave out" lasted one request; a file the plan refused or whose copy could not be made was named
beside that preview and then rejoined the next request unnoticed.

Now the assistant sets documents aside at the user's word (``set_aside``), a file the plan refused goes on the list
when the others' copies are created, a run failure nothing will check again goes there too, recovery's leftovers go
there at the user's word ("Set the rest aside for later"), every entry says why, and Put back takes one off the list
without a pass. Nothing on the list is changed, and the model never plans it until the user works on it."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fake_claude import FakeClient, resp, text, tool
from spec_surgeon import compiler
from spec_surgeon.document_task import (ASKED_REASON, HELD_REASON, SET_ASIDE_REST, START_OVER, WORK_TODO,
                                        DocumentTask)
from spec_surgeon.gui import Session
from test_document_task import REQUEST, SPEC, document


def task_for(tmp_path, fake, journal=None):
    return DocumentTask(Session(client=fake, out_root=tmp_path / "out", visual_check=False), render=False,
                        journal=journal)


def three(tmp_path):
    return [document(tmp_path / name) for name in ("20 Port.docx", "21 Keep.docx", "22 Michigan.docx")]


def park(*labels, reason="", tid="p1"):
    return resp(tool("set_aside", {"documents": list(labels), "reason": reason}, tid=tid))


def submit(spec=SPEC, tid="s1", **extra):
    return resp(tool("submit_editspec", {"spec": spec, **extra}, tid=tid))


def app_says(task, words):
    return any(words in m["text"] for m in task.messages if m["role"] == "app")


def test_the_assistant_sets_a_document_aside_and_it_waits_on_the_to_do_list_across_requests(tmp_path):
    docs = three(tmp_path)
    fake = FakeClient(park("20 Port.docx", reason="waiting on the owner's comments"), submit(),
                      submit(tid="s2"), submit(tid="s3"))
    task = task_for(tmp_path, fake)
    task.attach(docs)
    before = docs[0].read_bytes()
    view = task.perform("send", text="Set 20 aside for later, I'm waiting on the owner. Make the title bold.")
    # the call was answered, and the plan that followed covers the others only
    answered = fake.requests[1]["messages"][-1]["content"][0]
    assert answered["type"] == "tool_result" and "Set aside: 20 Port.docx" in answered["content"]
    assert "21 Keep.docx, 22 Michigan.docx" in answered["content"]
    assert view["stage"] == "preview"
    assert task.session.planned_files() == docs[1:] and task.session.conversation.files == docs[1:]
    assert sorted(Path(sv.source).name for sv in task.session.preview.samples) == ["21 Keep.docx", "22 Michigan.docx"]
    assert view["todo"] == [{"path": str(docs[0]), "name": "20 Port.docx", "why": "asked",
                             "reason": "You asked to set it aside for later: waiting on the owner's comments."}]
    assert view["set_aside"] == []                     # not "left untouched" beside this preview: it is on the list
    assert app_says(task, "Set aside for later: 20 Port.docx")
    task.perform("primary", token=view["token"])
    assert task.stage == "done" and sorted(Path(k).name for k in task.results) == ["21 Keep.docx", "22 Michigan.docx"]
    assert docs[0].read_bytes() == before and list(task.todo) == [str(docs[0])]
    assert WORK_TODO in task.view()["chips"]
    # the next request leaves it on the list, and the model is told what waits there and why
    view = task.perform("send", text="Now make the title italic.")
    sent = fake.requests[2]["messages"][0]["content"]
    assert "ON THE USER'S TO-DO LIST" in sent
    assert "- 20 Port.docx: You asked to set it aside for later: waiting on the owner's comments." in sent
    assert "20 Port.docx" not in sent.split("THE BATCH (the overview")[1]   # not among the documents it may plan
    assert [Path(p).name for p in task.session.planned_files()] == ["21 Keep.docx", "22 Michigan.docx"]
    task.perform("primary", token=view["token"])
    # working on it: the request under way when it was set aside, and why it waited
    view = task.perform("send", text=WORK_TODO)
    assert task.focus["documents"] == [str(docs[0])] and task.documents() == [docs[0]]
    assert task.decisions == ["Set 20 aside for later, I'm waiting on the owner. Make the title bold."]
    assert app_says(task, "Working on 20 Port.docx, from your to-do list.")
    task.perform("send", text="The comments are in; go ahead.")
    sent = fake.requests[3]["messages"][0]["content"]
    assert "FROM THE USER'S TO-DO LIST" in sent and "do not set them aside again" in sent
    assert "- 20 Port.docx: You asked to set it aside for later: waiting on the owner's comments." in sent
    assert "ON THE USER'S TO-DO LIST" not in sent                     # nothing else waits


def test_a_later_reply_goes_on_in_the_same_conversation_without_the_set_aside_document(tmp_path):
    docs = three(tmp_path)
    fake = FakeClient(park("22 Michigan.docx"), submit(), submit(tid="s2"))
    task = task_for(tmp_path, fake)
    task.attach(docs)
    task.perform("send", text="Park the Michigan one. Make the title bold.")
    conversation = task.session.conversation
    view = task.perform("send", text="Bold it is, thanks.")
    assert task.session.conversation is conversation                 # a reply, not a new conversation
    assert len(fake.requests[2]["messages"]) > 1
    assert view["stage"] == "preview" and task.session.planned_files() == docs[:2]
    assert task.todo[str(docs[2])]["reason"] == ASKED_REASON
    assert sum("Set aside for later" in m["text"] for m in task.messages) == 1   # said once, not on every turn


def test_leaving_out_the_rest_of_a_set_that_is_partly_set_aside_is_refused_as_nothing_to_plan(tmp_path):
    docs = three(tmp_path)
    fake = FakeClient(park("20 Port.docx"), submit(leave_out=["21 Keep.docx", "22 Michigan.docx"]),
                      submit(tid="s2", leave_out=["20 Port.docx"]))
    task = task_for(tmp_path, fake)
    task.attach(docs)
    view = task.perform("send", text="Set 20 aside. Make the title bold.")
    rejected = fake.requests[2]["messages"][-1]["content"][0]
    assert rejected.get("is_error") and "leave_out names every document" in rejected["content"]
    assert "(the others are set aside)" in rejected["content"]
    # naming the set-aside document in leave_out as well is no error, and it is not "left untouched" too
    assert view["stage"] == "preview" and task.session.planned_files() == docs[1:]
    assert task.session.left_out == [] and view["set_aside"] == []


def test_a_label_the_batch_does_not_list_goes_back_to_the_model_and_nothing_is_set_aside(tmp_path):
    docs = three(tmp_path)
    fake = FakeClient(park("23 Nowhere.docx"), submit())
    task = task_for(tmp_path, fake)
    task.attach(docs)
    view = task.perform("send", text="Set 23 aside. Make the title bold.")
    answered = fake.requests[1]["messages"][-1]["content"][0]
    assert answered.get("is_error") and "'23 Nowhere.docx' is not a label THE BATCH lists" in answered["content"]
    assert task.todo == {} and view["stage"] == "preview" and task.session.planned_files() == docs


def test_setting_every_document_aside_ends_the_request_and_nothing_is_asked_of_the_model(tmp_path):
    docs = three(tmp_path)[:2]
    fake = FakeClient(park("20 Port.docx", "21 Keep.docx"), resp(text("Both are on your to-do list."), stop="end_turn"))
    task = task_for(tmp_path, fake)
    task.attach(docs)
    view = task.perform("send", text="Set both aside for later.")
    assert "Every document is now set aside" in fake.requests[1]["messages"][-1]["content"][0]["content"]
    assert view["stage"] == "empty" and task.decisions == [] and task.documents() == []
    assert task.session.conversation is None and list(task.todo) == [str(d) for d in docs]
    assert view["chips"][0] == WORK_TODO
    task.perform("send", text="Make the title bold.")                # no model call: there is nothing to plan
    assert len(fake.requests) == 2 and app_says(task, "Every document is on your to-do list")
    task.perform("send", text=WORK_TODO)
    assert task.focus["documents"] == [str(d) for d in docs]


def test_setting_aside_again_during_a_pass_ends_the_pass(tmp_path):
    docs = three(tmp_path)[:2]
    fake = FakeClient(submit(), park("21 Keep.docx", reason="not today"),
                      resp(text("It is back on your list."), stop="end_turn"))
    task = task_for(tmp_path, fake)
    task.attach(docs)
    view = task.perform("send", text=REQUEST)
    task.perform("primary", token=view["token"], hold=[str(docs[1])])
    first_copy = Path(task.results[str(docs[0])])
    task.perform("send", text=WORK_TODO)
    view = task.perform("send", text="Actually, put it back on the list.")
    assert task.focus is None and task.decisions == [] and view["stage"] == "empty"
    assert task.todo[str(docs[1])]["why"] == "asked" and task.todo[str(docs[1])]["reason"] == (
        "You asked to set it aside for later: not today.")
    assert task.documents() == [first_copy]
    assert app_says(task, "The pass over your to-do list is over")


def test_put_back_takes_a_document_off_the_list_without_a_pass(tmp_path):
    docs = three(tmp_path)[:2]
    fake = FakeClient(submit(), submit(tid="s2"))
    task = task_for(tmp_path, fake)
    task.attach(docs)
    view = task.perform("send", text=REQUEST)
    task.perform("primary", token=view["token"], hold=[str(docs[1])])
    first_copy = Path(task.results[str(docs[0])])
    with pytest.raises(ValueError, match="not on your to-do list"):
        task.put_back(docs[0])
    task.put_back(docs[1])
    assert task.todo == {} and task.documents() == [first_copy, docs[1]]
    assert app_says(task, "is off your to-do list and back among the documents")
    assert WORK_TODO not in task.view()["chips"]
    view = task.perform("send", text="Now the same for the other one.")
    assert task.session.planned_files() == [first_copy.resolve(), docs[1]]


def test_put_back_during_a_pass_waits_for_the_pass_to_end(tmp_path):
    docs = [document(tmp_path / name) for name in ("A.docx", "B.docx", "C.docx")]
    fake = FakeClient(submit(), submit(tid="s2"))
    task = task_for(tmp_path, fake)
    task.attach(docs)
    view = task.perform("send", text=REQUEST)
    task.perform("primary", token=view["token"], hold=[str(docs[1])])
    view = task.perform("send", text="Also make it bold again.")      # a second request, on A and C
    task.perform("primary", token=view["token"], hold=[str(task.current_of(docs[2]))])
    task.perform("send", text=WORK_TODO)                            # B's pass (the oldest request)
    assert task.focus["documents"] == [str(docs[1])] and list(task.todo) == [str(docs[2])]
    task.put_back(docs[2])
    assert task.todo == {} and task.documents() == [docs[1]]        # in scope once B's pass ends
    assert app_says(task, "C.docx is off your to-do list; it joins the other documents when this pass ends.")


def _refusing(tmp_path, *more):
    """easy.docx plans; source.docx is refused (it has no MissingStyle)."""
    from test_continuous_recovery import STYLE_SPEC, _two_documents
    source, easy = _two_documents(tmp_path)
    task = task_for(tmp_path, FakeClient(submit(STYLE_SPEC), *more))
    task.attach([easy, source])
    return task, source, easy


def test_a_file_the_plan_refuses_goes_on_the_to_do_list_when_the_others_are_created(tmp_path):
    task, source, easy = _refusing(tmp_path, submit(tid="s2"))
    before = source.read_bytes()
    view = task.perform("send", text="Make the title bold")
    assert [item["name"] for item in view["set_aside"]] == ["source.docx"]   # beside the preview, as before
    assert task.todo == {}                                          # nothing moves before the click
    task.perform("primary", token=view["token"])
    assert task.stage == "done" and list(task.results) == [str(easy)]
    entry = task.todo[str(source)]
    assert entry["why"] == "refused" and entry["reason"].startswith("The plan could not be applied to it: ")
    assert "MissingStyle" in entry["reason"] and entry["decisions"] == ["Make the title bold"]
    assert source.read_bytes() == before
    assert app_says(task, "source.docx could not take this change, so it waits on your to-do list with the reason.")
    assert task.documents() == [Path(task.results[str(easy)])]
    view = task.view()
    assert view["chips"][0] == WORK_TODO and view["todo"][0]["why"] == "refused"
    # working on it later: the model hears the plan's reason
    task.perform("send", text=WORK_TODO)
    task.perform("send", text="Use Heading 1 instead.")
    sent = task.session.client.requests[1]["messages"][0]["content"]
    assert "FROM THE USER'S TO-DO LIST" in sent and "source.docx: The plan could not be applied to it" in sent


def test_a_create_that_is_refused_moves_nothing_to_the_list(tmp_path):
    task, source, easy = _refusing(tmp_path)
    view = task.perform("send", text="Make the title bold")
    document(easy)                                                   # the file changed since the preview
    with pytest.raises(ValueError):
        task.perform("primary", token=view["token"])
    assert task.todo == {} and task.results == {}


def test_recoverys_leftovers_go_on_the_to_do_list_at_the_users_word(tmp_path):
    from spec_surgeon import recovery
    from test_recovery import candidate, margin, opts, spec, submit as submit_repair
    from test_recovery_resume import distinct_failures
    paths = [document(tmp_path / name) for name in ("First.docx", "Second.docx")]
    original = [p.read_bytes() for p in paths]
    goal = spec([margin()])
    fake = FakeClient(submit_repair(candidate(goal)),
                      resp(tool("need_user", {"question": "Use these margins on the second document?"})))
    task = task_for(tmp_path, fake, journal=tmp_path / "journal.json")
    task.attach(paths)
    task.decisions = task.word_sources = [goal["request"]]
    result = recovery.recover(paths, goal["request"], tmp_path / "recovery", spec=goal, options=opts(),
                              prior_failures=distinct_failures(paths), client=fake)
    task.session._adopt_recovery(result)
    task._settle(lambda _: None)
    assert SET_ASIDE_REST not in task.view()["chips"]               # the ready copy is not saved yet
    task.perform("send", text=SET_ASIDE_REST)
    assert app_says(task, "Save the ready copies first") and task.todo == {}
    view = task.perform("primary", token=task.action_token())
    assert view["stage"] == "question" and SET_ASIDE_REST in view["chips"]
    first_copy = Path(task.results[str(paths[0])])
    calls = len(fake.requests)
    view = task.perform("send", text=SET_ASIDE_REST, chip=True)
    assert len(fake.requests) == calls                               # the app's own step: no model call
    assert view["stage"] == "done" and task.session.recovery is None and task.decisions == []
    assert list(task.todo) == [str(paths[1])] and task.todo[str(paths[1])]["why"] == "failed"
    assert task.todo[str(paths[1])]["reason"].startswith("It still needed work when you set it aside: ")
    assert task.documents() == [first_copy] and task.generation == 1
    assert [p.read_bytes() for p in paths] == original
    assert view["chips"][0] == WORK_TODO
    again = task_for(tmp_path, FakeClient(), journal=task.journal)
    assert again.restore() and again.todo[str(paths[1])]["why"] == "failed"


def test_the_journal_keeps_why_and_an_older_one_reads_as_the_red_x(tmp_path):
    docs = three(tmp_path)
    journal = tmp_path / "conversation.json"
    task = task_for(tmp_path, FakeClient(park("20 Port.docx", reason="later"), submit()), journal=journal)
    task.attach(docs)
    task.perform("send", text="Set 20 aside for later. Make the title bold.")
    again = task_for(tmp_path, FakeClient(), journal=journal)
    assert again.restore()
    assert again.todo[str(docs[0])]["why"] == "asked" and "later" in again.todo[str(docs[0])]["reason"]
    data = json.loads(journal.read_text(encoding="utf-8"))
    for entry in data["todo"].values():
        del entry["why"], entry["reason"]
    journal.write_text(json.dumps(data), encoding="utf-8")
    older = task_for(tmp_path, FakeClient(), journal=journal)
    assert older.restore() and older.todo[str(docs[0])]["why"] == "held"
    assert older.todo[str(docs[0])]["reason"] == HELD_REASON
    data["todo"][str(docs[0])]["why"] = 7
    journal.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        task_for(tmp_path, FakeClient(), journal=journal).restore()


def test_start_over_clears_the_list_whatever_put_documents_there(tmp_path):
    docs = three(tmp_path)
    task = task_for(tmp_path, FakeClient(park("20 Port.docx"), submit()))
    task.attach(docs)
    task.perform("send", text="Set 20 aside. Make the title bold.")
    task.perform("send", text=START_OVER)
    assert task.todo == {} and task.documents() == docs


def test_only_the_conversation_can_set_aside_and_the_prompt_says_when():
    assert "set_aside" in {t["name"] for t in compiler.CHAT_TOOLS}
    assert "set_aside" not in {t["name"] for t in compiler.TOOLS}   # a one-shot compile has no to-do list
    prompt = compiler.system_prompt(chat=True)
    assert "call set_aside" in prompt and "Leave out is for this request only; set aside is for later." in prompt
    assert "puts it on the to-do list with its reason" in prompt
    assert compiler.resolve_set_aside([], None)[1]                  # an empty call is refused
