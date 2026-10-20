"""The window's live channel and continue-on-result (2026-10-20): a job streams what the model writes and does,
the plan shows before any page renders, and after a run the next request builds on the delivered copies — the
originals stay as they are, and "Start over from the originals" drops the copies."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from docx import Document

from fake_claude import FakeClient, resp, text, tool
from spec_surgeon.document_task import START_OVER, DocumentTask
from spec_surgeon.gui import Session
from test_document_task import REQUEST, SPEC, document


def task_for(tmp_path, fake, journal=None):
    return DocumentTask(Session(client=fake, out_root=tmp_path / "out", visual_check=False), render=False,
                        journal=journal)


def test_a_job_streams_the_models_words_its_activity_the_plan_and_the_result(tmp_path):
    fake = FakeClient(resp(tool("inspect_profile", {"pattern_id": "x"}, "t0")),
                      resp(text("I'll make the title bold and leave everything else as it is."),
                           tool("submit_editspec", {"spec": SPEC, "suggested_replies": ["Also make it italic"]})),
                      live=True)
    task = task_for(tmp_path, fake)
    source = document(tmp_path / "Spec.docx")
    task.attach([source])
    events = []
    view = task.perform("send", text=REQUEST, events=events.append)
    kinds = [e["type"] for e in events]
    assert "text_delta" in kinds and "activity" in kinds and "plan" in kinds and "samples" in kinds and "cost" in kinds
    assert "".join(e["text"] for e in events if e["type"] == "text_delta") == \
        "I'll make the title bold and leave everything else as it is."
    assert [e["text"] for e in events if e["type"] == "activity"] == ["Looking at how the documents are built"]
    assert any(e["type"] == "status" and e["kind"] == "planning" for e in events)
    assert any(e["type"] == "status" and e["kind"] == "thinking" for e in events)
    assistant = [e["message"] for e in events if e["type"] == "message" and e["message"]["role"] == "assistant"]
    assert assistant and assistant[-1]["text"].startswith("I'll make the title bold")
    assert kinds.index("plan") < kinds.index("samples")                 # the plan shows before the preview is made
    plan = next(e["plan"] for e in events if e["type"] == "plan")
    assert plan["stages"] == 1 and plan["groups"][0]["changes"][0]["title"].startswith("Change text")
    assert view["stage"] == "preview" and view["chips"] == ["Also make it italic"]
    assert fake.requests[0].get("thinking") == {"type": "adaptive", "display": "summarized"}
    events.clear()
    task.perform("primary", token=view["token"], events=events.append)
    assert [e["type"] for e in events][:1] == ["status"]
    assert any(e["type"] == "results" and e["results"][0]["name"] == "Spec.docx" for e in events)
    assert task.stage == "done"


def test_the_next_request_builds_on_the_copies_and_start_over_returns_to_the_originals(tmp_path):
    second = copy.deepcopy(SPEC)
    second["ops"][0]["params"]["run"] = {"i": True}
    second["summary"] = "Make the title italic"
    fake = FakeClient(resp(tool("submit_editspec", {"spec": SPEC})),
                      resp(tool("submit_editspec", {"spec": second})),
                      resp(tool("submit_editspec", {"spec": SPEC})))
    journal = tmp_path / "conversation.json"
    task = task_for(tmp_path, fake, journal=journal)
    source = document(tmp_path / "Spec.docx")
    before = source.read_bytes()
    task.attach([source])
    view = task.perform("send", text=REQUEST)
    task.perform("primary", token=view["token"])
    first_copy = Path(task.results[str(source)])
    assert task.stage == "done" and first_copy.is_file() and first_copy != source
    assert task.documents() == [first_copy]                       # the next request works on the copy
    assert view["working"]["generation"] == 0
    view = task.view()
    assert view["working"] == {"generation": 1, "from_originals": False, "can_start_over": True}
    assert START_OVER in view["chips"]
    assert view["attachments"][0]["current"] == str(first_copy)
    view = task.perform("send", text="Also make that title italic.")
    sent = fake.requests[1]["messages"][0]["content"]
    assert "CHANGES ALREADY MADE TO THESE COPIES" in sent and "bold" in sent.lower()
    assert "Also make that title italic." in sent and REQUEST not in sent.split("CHANGES ALREADY MADE")[0]
    task.perform("primary", token=view["token"])
    second_copy = Path(task.results[str(source)])                # results stay keyed by the attached original
    assert second_copy != first_copy and second_copy.is_file()
    run = Document(second_copy).paragraphs[1].runs[0]
    assert run.bold and run.italic                                # the second change landed on top of the first
    assert source.read_bytes() == before
    assert task.generation == 2 and len(task.history) == 2
    # the journal keeps the working set
    restored = task_for(tmp_path, FakeClient(), journal=journal)
    restored.restore()
    assert restored.documents() == [second_copy] and restored.generation == 2 and len(restored.history) == 2
    # start over: the originals again, the plan void, the copies untouched
    view = task.perform("send", text=START_OVER)
    assert task.documents() == [source] and task.generation == 0 and task.history == []
    assert view["working"]["from_originals"] and view["stage"] == "empty"
    assert second_copy.is_file()
    view = task.perform("send", text=REQUEST)
    assert "CHANGES ALREADY MADE" not in fake.requests[2]["messages"][0]["content"]
    assert view["stage"] == "preview"
