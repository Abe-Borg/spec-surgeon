"""The chat's telling tools stream their input as it is written (``eager_input_streaming``, compiler.STREAMED_TOOLS):
the window shows Claude's words from ask_user and submit_editspec as they come instead of in one burst after the
server has buffered them. The server then no longer checks that input is valid JSON, so llm checks it: a broken one is
a call sent again, a refusal or a cut-off answer is reported by ``stop_problem`` before any tool is acted on. Nothing
else streams unbuffered: the background writers (custom methods, recovery, the visual check) have no one watching."""
from __future__ import annotations

import json

import pytest

from spec_surgeon import codegen, compiler, document_task, llm


def _sse(events):
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode("utf-8")


def _tool_answer(partial, stop="tool_use"):
    """One streamed response: an ask_user call whose input arrives as ``partial`` (possibly broken JSON)."""
    return _sse([
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 100, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {
            "type": "tool_use", "id": "toolu_1", "name": "ask_user", "input": {}}}),
        *[("content_block_delta", {"type": "content_block_delta", "index": 0,
                                   "delta": {"type": "input_json_delta", "partial_json": piece}}) for piece in partial],
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                           "usage": {"output_tokens": 20}}),
        ("message_stop", {"type": "message_stop"}),
    ])


def test_only_the_chats_telling_tools_stream_their_input_unbuffered():
    eager = {t["name"] for t in compiler.CHAT_TOOLS if t.get("eager_input_streaming")}
    assert eager == set(compiler.STREAMED_TOOLS) == set(document_task.TELLING_TOOLS)
    assert not any(t.get("eager_input_streaming") for t in compiler.TOOLS)            # compile mode, recovery
    assert not any(t.get("eager_input_streaming")
                   for t in codegen.CODEGEN_TOOLS + codegen.CODEGEN_TOOLS_UNGUARDED)
    # the flag is the only difference: the definitions Claude reads are the same
    plain = {t["name"]: t for t in compiler.TOOLS}
    for t in compiler.CHAT_TOOLS:
        if t["name"] == "submit_editspec":
            assert {k: v for k, v in t.items() if k != "eager_input_streaming"} == plain["submit_editspec"]


@pytest.fixture
def sdk(monkeypatch):
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    monkeypatch.setattr(llm, "RETRIES", 1)
    monkeypatch.setattr(llm, "RETRY_DELAYS", (0.0,))
    sent, answers = [], []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=answers.pop(0))

    client = anthropic.Anthropic(api_key="test-key", max_retries=0,
                                 http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    return client, sent, answers


def _call(client):
    return llm.call(client, model="claude-opus-5-5", system="S", tools=compiler.CHAT_TOOLS,
                    messages=[{"role": "user", "content": "x"}])


def test_the_real_sdk_sends_the_flag_and_a_broken_streamed_input_is_sent_again(sdk):
    client, sent, answers = sdk
    answers += [_tool_answer(['{"message": "Which sec', 'tions?", "choices": [}']),      # broken: the model's own
                _tool_answer(['{"message": "Which sec', 'tions?"}'])]
    r = _call(client)
    assert len(sent) == 2                                                    # the broken answer was never acted on
    flags = {t["name"]: t.get("eager_input_streaming") for t in sent[0]["tools"]}
    assert flags["ask_user"] is True and flags["submit_editspec"] is True and flags["read_paragraphs"] is None
    assert [b.input for b in llm.tool_uses(llm.history_content(r))] == [{"message": "Which sections?"}]


def test_a_broken_input_cut_off_by_max_tokens_is_reported_not_sent_again(sdk):
    client, sent, answers = sdk
    answers.append(_tool_answer(['{"message": "Which sec'], stop="max_tokens"))
    r = _call(client)
    assert len(sent) == 1 and llm.stop_problem(r) == "the model's answer was cut off (max_tokens)"


def test_a_broken_input_of_a_buffered_tool_is_still_a_failure_not_a_resend(sdk):
    client, sent, answers = sdk
    answers += [_tool_answer(['{"message": "Which sec', 'tions?", "choices": [}'])] * 2
    with pytest.raises(llm.IncompleteResponse) as raised:
        llm.call(client, model="claude-opus-5-5", system="S", tools=compiler.TOOLS,
                 messages=[{"role": "user", "content": "x"}])
    assert not raised.value.transient and len(sent) == 1
