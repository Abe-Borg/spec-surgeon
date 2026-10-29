"""The top bar's context gauge (adapted from Build-a-Spec's context pill): how much of the model's context window the
conversation fills, as Anthropic counted it at the conversation's last step, beside the window and the point past which
the history is sealed and goes on afresh. A measurement carried in the ``cost`` payload, never spending, never text,
never journalled; only the conversation's own calls move it."""
from __future__ import annotations

from types import SimpleNamespace

from fake_claude import FakeClient, resp, text, tool
from spec_surgeon import llm
from spec_surgeon.conversation import Conversation
from spec_surgeon.resources import DEFAULT_CONTEXT_TOKENS
from test_document_task import REQUEST, SPEC, document, task_for

STATUS = "Which of these carry a Subject property? Do not prepare or make an edit."


def counted(response, *, inp=100, out=20, write=0, read=0):
    """A scripted response with Anthropic's four counts set."""
    response.usage.input_tokens, response.usage.output_tokens = inp, out
    response.usage.cache_creation_input_tokens, response.usage.cache_read_input_tokens = write, read
    return response


def submit(**counts):
    return counted(resp(tool("submit_editspec", {"spec": SPEC})), **counts)


def answered(**counts):
    return counted(resp(text("Only Spec.docx carries one."), stop="end_turn"), **counts)


def test_the_context_is_the_whole_prompt_plus_the_answer_the_conversation_keeps():
    r = counted(resp(text("x")), inp=7, out=30, write=2_000, read=40_000)
    assert llm.context_tokens(r) == 7 + 30 + 2_000 + 40_000
    assert llm.context_tokens(SimpleNamespace(usage=None)) is None
    assert llm.context_tokens(SimpleNamespace(usage=SimpleNamespace(input_tokens=True))) is None   # never a bool
    assert llm.context_tokens(SimpleNamespace(usage=SimpleNamespace(input_tokens=5))) == 5
    # the window of every model the price table knows; a model it lacks has none, never a guessed one
    assert set(llm.CONTEXT_WINDOWS) == set(llm.PRICES)
    assert llm.CONTEXT_WINDOWS["claude-opus-5-5"] == 1_000_000


def test_no_reading_before_the_first_step(tmp_path):
    task = task_for(tmp_path)
    assert task._cost()["context"] is None and task.view() is not None


def test_a_step_of_the_conversation_moves_the_gauge_at_once_on_the_live_channel(tmp_path):
    task = task_for(tmp_path, FakeClient(submit(inp=12, out=80, write=30_000, read=0)))
    task.attach([document(tmp_path / "Spec.docx")])
    events = []
    view = task.perform("send", text=REQUEST, events=events.append)
    assert view["stage"] == "preview"
    reading = {"tokens": 30_092, "window": 1_000_000, "rollover": DEFAULT_CONTEXT_TOKENS, "live": True}
    assert task._cost()["context"] == reading                           # the request is open: its conversation lives
    # sent as the step ended, before the preview is even planned, not with the job's last event
    first = next(i for i, e in enumerate(events) if e["type"] == "cost" and e["cost"]["context"])
    assert events[first]["cost"]["context"] == reading
    assert first < next(i for i, e in enumerate(events) if e["type"] == "plan")


def test_the_gauge_follows_the_last_step_of_a_turn(tmp_path):
    first = counted(resp(tool("list_styles", {"file": "Spec.docx"})), inp=10, out=50, write=25_000)
    task = task_for(tmp_path, FakeClient(first, submit(inp=300, out=90, read=25_060)))
    task.attach([document(tmp_path / "Spec.docx")])
    task.perform("send", text=REQUEST)
    assert task._cost()["context"]["tokens"] == 300 + 90 + 25_060


def test_the_reading_stands_for_the_conversation_before_once_the_request_ends(tmp_path):
    task = task_for(tmp_path, FakeClient(answered(inp=5, out=15, write=41_000)))
    task.attach([document(tmp_path / "Spec.docx")])
    view = task.perform("send", text=STATUS)
    assert task.session.conversation is None                             # an answer with nothing pending ends it
    assert view["stage"] == "empty"
    assert task._cost()["context"] == {"tokens": 41_020, "window": 1_000_000, "rollover": DEFAULT_CONTEXT_TOKENS,
                                       "live": False}


def test_a_declined_answer_joins_nothing_and_keeps_the_last_reading(tmp_path):
    refused = counted(resp(stop="refusal", details=SimpleNamespace(category="cyber", explanation="no")),
                      inp=9, out=1, read=60_000)
    task = task_for(tmp_path, FakeClient(resp(tool("ask_user", {"questions": ["Which title?"]})), refused))
    task.attach([document(tmp_path / "Spec.docx")])
    task.perform("send", text=REQUEST)
    before = task._cost()["context"]
    assert before["tokens"] == 120 and before["live"]                   # the scripted defaults: 100 in, 20 out
    task.perform("send", text="FIRE SUPPRESSION")
    assert task._cost()["context"]["tokens"] == 120


def test_a_new_window_conversation_starts_without_a_reading_and_the_journal_keeps_none(tmp_path):
    from spec_surgeon.document_task import DocumentTask
    from spec_surgeon.gui import Session
    journal = tmp_path / "journal.json"
    task = DocumentTask(Session(client=FakeClient(submit(write=5_000)), out_root=tmp_path / "out", visual_check=False),
                        journal=journal, render=False)
    task.attach([document(tmp_path / "Spec.docx")])
    task.perform("send", text=REQUEST)
    assert task._cost()["context"]["tokens"] == 100 + 20 + 5_000
    assert journal.is_file() and '"rollover"' not in journal.read_text(encoding="utf-8")       # never journalled
    fresh = DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=False)
    assert fresh._cost()["context"] is None


def test_an_unknown_model_has_its_tokens_and_no_window(tmp_path):
    seen = []
    conversation = Conversation(REQUEST, [document(tmp_path / "Spec.docx")], client=FakeClient(submit(write=7)),
                                model="claude-unlisted", work_dir=tmp_path / "chat", on_context=seen.append)
    conversation.start()
    assert conversation.context_reading == {"tokens": 127, "window": None, "rollover": DEFAULT_CONTEXT_TOKENS}
    assert seen == [conversation]


def test_an_observer_that_fails_never_affects_the_conversation(tmp_path):
    def broken(_):
        raise RuntimeError("the window's problem")
    conversation = Conversation(REQUEST, [document(tmp_path / "Spec.docx")], client=FakeClient(submit()),
                                work_dir=tmp_path / "chat", on_context=broken)
    turn = conversation.start()
    assert turn.status == "ok" and conversation.context_reading["tokens"] == 120


def test_the_reading_never_takes_the_place_of_the_conversations_own_context(tmp_path):
    """``Conversation.context`` is the app's account of how the documents come to be planned (set aside after a
    preview, say), which the first message carries; the gauge's reading is ``context_reading`` and leaves it be."""
    note = "SET ASIDE AFTER A PREVIEW: the plan they saw."
    fake = FakeClient(submit(write=9))
    conversation = Conversation(REQUEST, [document(tmp_path / "Spec.docx")], client=fake, context=note,
                                work_dir=tmp_path / "chat")
    assert conversation.context == note
    conversation.start()
    assert conversation.context == note and conversation.context_reading["tokens"] == 129
    assert note in str(fake.requests[0]["messages"][0]["content"])
