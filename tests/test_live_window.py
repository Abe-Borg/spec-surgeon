"""The window's live channel and continue-on-result (2026-10-20): a job streams what the model writes and does,
the plan shows before any page renders, and after a run the next request builds on the delivered copies — the
originals stay as they are, and "Start over from the originals" drops the copies."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
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


def test_a_tapped_suggested_reply_is_the_users_decision_but_never_their_wording(tmp_path):
    """A chip is the model's text. Tapping it sends it as the user's reply (the model hears the decision) but it
    never joins the approved word sources: a later edit placing its words would still need the user's own words
    (CLAUDE.md invariant 6)."""
    chip = "Everything, and replace Contractor with Design-Builder"
    fake = FakeClient(resp(text("One thing first."),
                           tool("ask_user", {"message": "Only the body text, or everything?",
                                             "questions": ["Only the body text, or everything?"],
                                             "suggested_replies": [chip, "Just the body text"]})),
                      resp(tool("submit_editspec", {"spec": SPEC})))
    task = task_for(tmp_path, fake)
    task.attach([document(tmp_path / "Spec.docx")])
    view = task.perform("send", text=REQUEST)
    assert view["stage"] == "question" and chip in view["chips"]
    view = task.perform("send", text=chip, chip=True)
    assert view["stage"] == "preview"
    assert chip in task.decisions and chip not in task.word_sources
    assert chip not in task.session.conversation.sources
    assert chip in repr(fake.requests[1]["messages"])                  # the model heard the decision
    assert any(m["role"] == "user" and m["text"] == chip for m in task.messages)
    # typed as the user's own words, the same text is approved wording
    typed = task_for(tmp_path / "typed", FakeClient(
        resp(text("One thing first."), tool("ask_user", {"message": "?", "questions": ["?"], "suggested_replies": [chip]})),
        resp(tool("submit_editspec", {"spec": SPEC}))))
    typed.attach([document(tmp_path / "typed" / "Spec.docx")])
    typed.perform("send", text=REQUEST)
    typed.perform("send", text=chip)
    assert chip in typed.word_sources and chip in typed.session.conversation.sources


# ---- the review's probes (PR #46): the working set across runs, attachments beside copies, the live channel ----------

def test_the_working_set_advances_when_the_results_folder_is_reached_by_another_path(tmp_path):
    """The engine resolves the paths it reads; a results folder behind a link (a junction, a mapped drive) must still
    match the copy the run was given, or the third request would silently build on the first copy."""
    import os
    real = tmp_path / "real-runs"
    real.mkdir()
    link = tmp_path / "runs-link"
    try:
        os.symlink(real, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks here")
    second = copy.deepcopy(SPEC)
    second["ops"][0]["params"]["run"] = {"i": True}
    second["summary"] = "Make the title italic"
    fake = FakeClient(resp(tool("submit_editspec", {"spec": SPEC})), resp(tool("submit_editspec", {"spec": second})))
    task = DocumentTask(Session(client=fake, out_root=link, visual_check=False), render=False)
    source = document(tmp_path / "Spec.docx")
    task.attach([source])
    view = task.perform("send", text=REQUEST)
    task.perform("primary", token=view["token"])
    first_copy = task.current_of(source)
    assert task.generation == 1 and first_copy != source
    view = task.perform("send", text="Also make it italic.")
    task.perform("primary", token=view["token"])
    second_copy = task.current_of(source)
    assert task.generation == 2 and second_copy not in (source, first_copy), task.working
    assert list(task.results) == [str(source)]                     # keyed by the attached original, not by a copy
    assert task.documents() == [second_copy]


def test_removing_the_last_copy_or_attaching_an_original_beside_copies_is_said_and_told(tmp_path):
    fake = FakeClient(resp(tool("submit_editspec", {"spec": SPEC})), resp(tool("submit_editspec", {"spec": SPEC})),
                      resp(tool("submit_editspec", {"spec": SPEC})))
    task = task_for(tmp_path, fake)
    source = document(tmp_path / "Spec.docx")
    task.attach([source])
    view = task.perform("send", text=REQUEST)
    task.perform("primary", token=view["token"])
    assert task.generation == 1 and task.history
    # an original attached beside the copies: the user hears it, and the model is told which document is fresh
    other = document(tmp_path / "Other.docx")
    task.attach([other])
    assert any("Other.docx" in m["text"] and "attached as an original" in m["text"] for m in task.messages)
    assert task._fresh_documents() == ["Other.docx"]
    task.perform("send", text="Also make it italic.")
    first_message = repr(fake.requests[1]["messages"][0])
    assert "CHANGES ALREADY MADE" in first_message and "EXCEPT Other.docx" in first_message
    # the last copy leaves with its original: the history is reset and the user is told
    task.remove(other)
    assert task.working and task.history                            # the first document's copy still carries it
    task.remove(source)
    assert task.working == {} and task.history == [] and task.generation == 0 and task.results == {}
    assert any("starts from your originals" in m["text"] for m in task.messages)


def test_the_live_channel_numbers_messages_and_relays_only_the_conversations_words(tmp_path):
    fake = FakeClient(resp(text("I'll make the title bold."), tool("submit_editspec", {"spec": SPEC})), live=True)
    task = task_for(tmp_path, fake)
    task.attach([document(tmp_path / "Spec.docx")])
    events = []
    view = task.perform("send", text=REQUEST, events=events.append)
    said = [e["message"] for e in events if e["type"] == "message"]
    assert said and all(isinstance(m.get("id"), str) for m in said)
    by_id = {m["id"]: m for m in view["messages"]} if "id" in (view["messages"] or [{}])[0] else {
        f"m{i}": m for i, m in enumerate(view["messages"])}
    for m in said:
        assert by_id[m["id"]]["text"] == m["text"]                  # the id the view numbers it by
    assert any(e["type"] == "text_delta" for e in events)           # the conversation's own words were relayed
    # a model call outside the conversation (the visual reviewer, recovery, a custom method) is not the reply
    outside = []
    task._events = outside.append
    task._on_stream({"type": "text_delta", "text": "Looking at page 1…"})
    task._on_stream({"type": "tool", "name": "read_paragraphs"})
    assert [e["type"] for e in outside] == ["activity"]
    with task._conversation():
        task._on_stream({"type": "text_delta", "text": "I'll…"})
    assert outside[-1] == {"type": "text_delta", "text": "I'll…"}


def test_a_restored_journal_whose_copies_are_gone_says_so_and_lists_no_vanished_result(tmp_path):
    fake = FakeClient(resp(tool("submit_editspec", {"spec": SPEC})))
    journal = tmp_path / "conversation.json"
    task = task_for(tmp_path, fake, journal=journal)
    source = document(tmp_path / "Spec.docx")
    task.attach([source])
    view = task.perform("send", text=REQUEST)
    task.perform("primary", token=view["token"])
    copy_path = Path(task.results[str(source)])
    assert copy_path.is_file() and task.generation == 1
    copy_path.unlink()
    again = task_for(tmp_path, FakeClient(), journal=journal)
    assert again.restore()
    assert again.working == {} and again.generation == 0 and again.results == {}
    assert again.documents() == [source] and again.stage == "empty"
    assert any("could not be found" in m["text"] for m in again.messages)
    assert again.view()["results"] == {}                           # the window lists no vanished copy


def test_a_request_refused_for_its_thinking_display_is_sent_again_without_it_once(tmp_path, monkeypatch):
    """The SDK sends the request when the stream is entered; the 400 for the display must be caught there, the
    request sent once more without it, and the display kept off for the process."""
    from fake_claude import api_error, raise_on_enter
    from spec_surgeon import llm
    monkeypatch.setattr(llm, "_thinking_display_refused", False)
    fake = FakeClient(raise_on_enter(api_error("thinking.display: Extra inputs are not permitted", status=400)),
                      resp(text("I'll make the title bold."), tool("submit_editspec", {"spec": SPEC})), live=True)
    task = task_for(tmp_path, fake)
    task.attach([document(tmp_path / "Spec.docx")])
    events = []
    view = task.perform("send", text=REQUEST, events=events.append)
    assert view["stage"] == "preview"
    assert len(fake.requests) == 2 and "thinking" in fake.requests[0] and "thinking" not in fake.requests[1]
    assert llm._thinking_display_refused is True
    monkeypatch.setattr(llm, "_thinking_display_refused", False)
    # a 400 for anything else propagates untouched: one request, the latch untouched, the turn a recorded failure
    other = FakeClient(raise_on_enter(api_error("messages.3.content.0: thinking blocks must be unmodified", status=400)),
                       live=True)
    again = task_for(tmp_path / "again", other)
    again.attach([document(tmp_path / "again" / "Spec.docx")])
    view = again.perform("send", text=REQUEST)
    assert view["stage"] != "preview" and len(other.requests) == 1 and llm._thinking_display_refused is False


def test_a_resent_attempt_tells_the_page_to_drop_the_abandoned_words(tmp_path, monkeypatch):
    from fake_claude import api_error, raise_on_final
    from spec_surgeon import llm
    monkeypatch.setattr(llm, "RETRY_DELAYS", (0.0,))
    monkeypatch.setattr(llm, "RETRIES", 1)
    fake = FakeClient(raise_on_final(api_error("Overloaded", status=529)),
                      resp(text("I'll make the title bold."), tool("submit_editspec", {"spec": SPEC})), live=True)
    task = task_for(tmp_path, fake)
    task.attach([document(tmp_path / "Spec.docx")])
    events = []
    view = task.perform("send", text=REQUEST, events=events.append)
    assert view["stage"] == "preview" and len(fake.requests) == 2
    kinds = [(e["type"], e.get("kind")) for e in events]
    at = kinds.index(("status", "retrying"))
    words = "".join(e["text"] for e in events[at:] if e["type"] == "text_delta")
    assert words == "I'll make the title bold."


def test_a_stream_before_any_conversation_call_neither_raises_nor_relays(tmp_path):
    task = task_for(tmp_path, FakeClient())
    seen = []
    task._events = seen.append
    task._on_stream({"type": "text_delta", "text": "Looking at page 1…"})
    task._on_stream({"type": "thinking_delta", "text": "…"})
    assert seen == []


def test_pages_show_only_beside_a_plan_waiting_for_the_user(tmp_path):
    """The pages and their findings belong to the plan awaiting approval: the run that finishes it takes them along,
    and a stage with nothing to approve shows none of them, whatever preview the session still holds (a run that
    delivered no copy does not advance the working set, and its preview stays in the session)."""
    task = task_for(tmp_path, FakeClient(resp(tool("submit_editspec", {"spec": SPEC}))))
    task.attach([document(tmp_path / "Spec.docx")])
    view = task.perform("send", text=REQUEST)
    unrendered = "Page appearance was not checked"
    assert view["stage"] == "preview" and len(view["samples"]) == 1
    assert any(w.startswith(unrendered) for w in view["warnings"])
    held = task.session.outcome().preview
    task.stage = "done"
    assert task.session.outcome().preview is held
    view = task.view()
    assert view["samples"] == [] and not any(w.startswith(unrendered) for w in view["warnings"])
    task.stage = "preview"
    view = task.perform("primary", token=task.action_token())
    assert view["stage"] == "done" and view["results"] and view["samples"] == []


def test_the_words_of_a_question_or_a_submission_stream_as_the_model_writes_them(tmp_path):
    """The reply's words in an ask_user or submit_editspec ``message`` reach the page as they are written, a
    paragraph after the call's plain text: what streamed is the reply the turn ends with."""
    ask = {"understanding": "Arial in the headers too?", "message": "The headers use “Times New Roman” at 9 pt.",
           "questions": ["Should the headers change too?"], "suggested_replies": ["Yes, the headers too"]}
    fake = FakeClient(resp(text("I read the three documents."), tool("ask_user", ask)), live=True)
    task = task_for(tmp_path, fake)
    task.attach([document(tmp_path / "Spec.docx")])
    events = []
    view = task.perform("send", text=REQUEST, events=events.append)
    assert view["stage"] == "question"
    words = "".join(e["text"] for e in events if e["type"] == "text_delta")
    reply = [e["message"] for e in events if e["type"] == "message" and e["message"]["role"] == "assistant"][-1]
    assert words == reply["text"] == "I read the three documents.\n\nThe headers use “Times New Roman” at 9 pt."
    assert "Should the headers" not in words and "Arial in the headers" not in words   # the rest stays unshown


def test_a_message_is_read_from_its_json_however_the_pieces_fall():
    from spec_surgeon.document_task import MessageWords
    payload = json.dumps({"spec": {"message": "not this", "ops": [{"message": "nor this"}]},
                          "message": "  Line one — “quoted” \\ / \"x\"\n\nLine two\t😀  ", "choices": ["message"]})
    for size in range(1, 9):
        words = MessageWords()
        got = "".join(words.feed(payload[i:i + size]) for i in range(0, len(payload), size))
        assert got == "Line one — “quoted” \\ / \"x\"\n\nLine two\t😀"
    assert MessageWords().feed(json.dumps({"message": "   ", "questions": ["q"]})) == ""
    assert MessageWords().feed(json.dumps({"message": None, "x": "message"})) == ""
    assert MessageWords().feed(json.dumps({"questions": ["q"]}, ensure_ascii=False)) == ""
    # outside the conversation, or for a tool that tells the user nothing, nothing is relayed
    task = DocumentTask.__new__(DocumentTask)
    task._events, task._talking, task._said, task._telling = [].append, False, False, {}
    seen = []
    task._events = seen.append
    task._on_stream({"type": "tool", "name": "ask_user", "index": 0})
    task._on_stream({"type": "tool_input", "index": 0, "json": '{"message": "hidden"}'})
    with task._conversation():
        task._on_stream({"type": "tool", "name": "read_paragraphs", "index": 1})
        task._on_stream({"type": "tool_input", "index": 1, "json": '{"message": "hidden"}'})
        task._on_stream({"type": "tool", "name": "ask_user", "index": 2})
        task._on_stream({"type": "tool_input", "index": 2, "json": '{"message": "Shown."}'})
    assert [e for e in seen if e["type"] == "text_delta"] == [{"type": "text_delta", "text": "Shown."}]
