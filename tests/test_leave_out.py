"""Leaving documents out of a request (the 1.9.0 live run of 2026-10-22): the user wrote "leave out file 20. leave out
file 23 for now. leave out 22." and was told to "set the project to the other 10 files". The plan now covers the
others: a left-out document is not planned, previewed, run or changed, the window lists it under Left untouched with
the reason, and the next request covers it again unless the user leaves it out again."""
from __future__ import annotations

from pathlib import Path

import pytest

from fake_claude import FakeClient, resp, tool
from spec_surgeon import compiler
from spec_surgeon.conversation import Conversation
from spec_surgeon.document_task import LEFT_OUT_REASON, DocumentTask
from spec_surgeon.gui import Session
from test_document_task import REQUEST, SPEC, document


def task_for(tmp_path, fake):
    return DocumentTask(Session(client=fake, out_root=tmp_path / "out", visual_check=False), render=False)


def three(tmp_path):
    return [document(tmp_path / name) for name in ("20 Port.docx", "21 Keep.docx", "22 Michigan.docx")]


def submit(spec=SPEC, **extra):
    return resp(tool("submit_editspec", {"spec": spec, **extra}))


def test_documents_left_out_are_not_planned_previewed_run_or_changed(tmp_path):
    docs = three(tmp_path)
    fake = FakeClient(submit(leave_out=["20 Port.docx", "22 Michigan.docx"],
                             message="I'll make the title bold in 21; 20 and 22 stay as they are."))
    task = task_for(tmp_path, fake)
    task.attach(docs)
    before = [p.read_bytes() for p in docs]
    view = task.perform("send", text="Make the title bold. Leave out 20 and 22.")
    assert view["stage"] == "preview"
    assert task.session.planned_files() == [docs[1]] and task.session.files() == docs
    assert [Path(sv.source) for sv in view["samples"]] == [docs[1]]
    assert task.session.preview.batch_plan.files[0].source == docs[1] and len(task.session.preview.batch_plan.files) == 1
    assert view["set_aside"] == [{"name": "20 Port.docx", "reason": LEFT_OUT_REASON},
                                 {"name": "22 Michigan.docx", "reason": LEFT_OUT_REASON}]
    note = task.session.conversation.notes[-1]
    assert "LEFT OUT AT THE USER'S REQUEST (not planned, previewed or changed): 20 Port.docx, 22 Michigan.docx" in note
    task.perform("primary", token=view["token"])
    assert task.stage == "done" and list(task.results) == [str(docs[1])]
    assert [f.source for f in task.session.last_batch.files] == [docs[1]]
    assert [p.read_bytes() for p in docs] == before
    assert not task.session.recovery                # nothing left out is "still needing work"
    assert list(task.applied) == [str(docs[1])]      # only 21 carries the change the model will hear of


def test_a_left_out_document_cannot_be_set_aside_or_run_from_a_stale_page(tmp_path):
    docs = three(tmp_path)
    task = task_for(tmp_path, FakeClient(submit(leave_out=["20 Port.docx"])))
    task.attach(docs)
    view = task.perform("send", text=REQUEST)
    with pytest.raises(ValueError, match="documents or plan changed"):
        task.perform("primary", token=view["token"], hold=[str(docs[0])])
    assert task.results == {} and task.todo == {} and task.session.preview is not None
    with pytest.raises(ValueError, match="documents or plan changed"):   # the session refuses it whatever the caller says
        task.session.run(False, False, lambda _: None, only=[docs[0]])
    assert task.session.last_batch is None


def test_the_next_request_covers_every_document_again(tmp_path):
    docs = three(tmp_path)
    fake = FakeClient(submit(leave_out=["22 Michigan.docx"]), submit())
    task = task_for(tmp_path, fake)
    task.attach(docs)
    view = task.perform("send", text=REQUEST + " Not 22.")
    task.perform("primary", token=view["token"])
    assert len(task.results) == 2
    view = task.perform("send", text="Now the same for 22.")
    assert view["stage"] == "preview" and task.session.left_out == []
    assert len(task.session.planned_files()) == 3 and not view["set_aside"]


def test_leave_out_labels_are_checked_and_a_bad_one_goes_back_to_claude(tmp_path):
    docs = three(tmp_path)
    batch = compiler._Batch(docs, None)
    assert compiler.resolve_leave_out(["20 Port.docx"], batch) == ([docs[0]], [])
    assert compiler.resolve_leave_out(None, batch) == ([], [])
    _, errs = compiler.resolve_leave_out(["file 20"], batch)
    assert errs and "[leave-out]" in errs[0] and "'file 20'" in errs[0] and "21 Keep.docx" in errs[0]
    _, errs = compiler.resolve_leave_out([p.name for p in docs], batch)
    assert errs and "every document" in errs[0]
    _, errs = compiler.resolve_leave_out("20 Port.docx", batch)
    assert errs and "list of file labels" in errs[0]
    c = Conversation(REQUEST, docs, client=FakeClient(submit(leave_out=["file 20"]),
                                                      submit(leave_out=["20 Port.docx"])))
    turn = c.start()
    assert turn.status == "ok" and turn.leave_out == [str(docs[0].resolve())]
    assert "[leave-out] leave_out names 'file 20'" in str(c._client.requests[-1]["messages"])


def test_a_layout_plan_need_not_cover_the_patterns_the_user_left_out(tmp_path):
    from test_document_task import _layout_submission
    submission, (typed, styled, other) = _layout_submission(tmp_path)
    only_a = {"summary": "typed numbers only", "layouts": [submission["layouts"][0]]}
    batch = compiler._Batch([typed, styled, other], None)
    sources = ["Make the body paragraphs bold."]
    _, errs = compiler.compose_submission(only_a, batch, sources)
    assert errs and all("no layout covers pattern" in e for e in errs)
    outcome, errs = compiler.compose_submission(only_a, batch, sources, leave_out=[styled, other])
    assert errs == [] and [layout["id"] for layout in outcome["layouts"]] == ["A"]


def test_the_prompts_teach_leaving_out_and_keep_procedures_from_the_user():
    from spec_surgeon import campaign_planner
    prompt = compiler.system_prompt(chat=True)
    assert "submit the plan for the others with leave_out" in prompt
    assert "never ask the user to remove documents, start another conversation" in prompt
    assert "do not leave files out unasked" in prompt
    assert "(a pattern whose documents the user left out needs none)" in prompt
    tool = next(t for t in compiler.TOOLS if t["name"] == "submit_editspec")
    assert tool["input_schema"]["properties"]["leave_out"]["items"] == {"type": "string"}
    # the outcome planner's questions reach the user verbatim: no "project", no procedure (the live run's reply
    # asked the user to "set the project to the other 10 files" and offered "ten one-file projects")
    assert 'never "project", "contract", "effect", "scope" or\ndocument IDs' in campaign_planner.SYSTEM


def test_a_layout_of_left_out_documents_only_is_refused_with_a_reason(tmp_path):
    from test_document_task import _layout_submission
    submission, (typed, styled, _) = _layout_submission(tmp_path)      # layout A: typed; layout B: styled
    batch = compiler._Batch([typed, styled], None)
    assert compiler.compose_submission(submission, batch, ["Make the body paragraphs bold."])[1] == []
    _, errs = compiler.compose_submission(submission, batch, ["Make the body paragraphs bold."], leave_out=[typed])
    assert errs == ["layout 'A': [leave-out] every document this spec covers is left out of the request; drop it (a "
                    "layout of left-out documents only is not needed)"]


def test_a_custom_method_leaves_out_the_documents_the_user_left_out(tmp_path):
    # From review (Codex on #91): an unsupported request with "leave out 20" fell back to a custom method over every
    # document. The documents left out stay out of its trials, its preview and its copies.
    from test_codegen import GOOD_OP
    from docx import Document
    docs = three(tmp_path)
    request = "Set the header distance to 900 twips, not in 22."
    fake = FakeClient(resp(tool("submit_editspec", {
        "spec": {"request": request, "summary": "Move header", "unsupported": "Use a custom method",
                 "proposal": "Set the header distance to 900 twips"},
        "custom_transform": "possible", "custom_transform_reason": "Formatting only",
        "leave_out": ["22 Michigan.docx"]})), resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}})))
    task = task_for(tmp_path, fake)
    task.attach(docs)
    before = docs[2].read_bytes()
    view = task.perform("send", text=request)
    assert view["stage"] == "preview" and task.session.generated.passed
    assert task.session.left_out == [docs[2].resolve()] and task.session.planned_files() == docs[:2]
    assert docs[2] not in [Path(s.source) for s in task.session.generated_preview.samples]
    assert view["set_aside"] == [{"name": "22 Michigan.docx", "reason": LEFT_OUT_REASON}]
    task.perform("primary", token=view["token"])
    assert task.stage == "done" and sorted(task.results) == sorted(str(p) for p in docs[:2])
    assert all(Document(task.results[str(p)]).sections[0].header_distance.twips == 900 for p in docs[:2])
    assert docs[2].read_bytes() == before
