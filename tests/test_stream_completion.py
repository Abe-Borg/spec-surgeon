"""The real SDK must not promote a partial stream or partial tool JSON into a spec."""
import json

import pytest

from make_fixtures import build
from spec_surgeon import llm
from spec_surgeon.conversation import Conversation
from test_conversation import REMOVE


def events(*, stop="tool_use", block_stop=True, message_stop=True, partial=False):
    payload = json.dumps({"spec": REMOVE})
    result = [
        {"type": "message_start", "message": {
            "id": "msg_completion", "type": "message", "role": "assistant", "model": llm.DEFAULT_MODEL,
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "submit", "name": "submit_editspec", "input": {}}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": payload[:-1] if partial else payload}},
    ]
    if block_stop:
        result.append({"type": "content_block_stop", "index": 0})
    if stop is not None:
        result.append({"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                       "usage": {"output_tokens": 123}})
    if message_stop:
        result.append({"type": "message_stop"})
    return result


def client_for(event_list):
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    wire = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in event_list).encode()
    return anthropic.Anthropic(api_key="offline-test-key", max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(
            lambda request: httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=wire))))


@pytest.mark.parametrize("arguments", [
    {"stop": None, "block_stop": False, "message_stop": False, "partial": True},
    {"message_stop": False},
    {"block_stop": False},
    {"partial": True},
    {"stop": "model_context_window_exceeded"},
])
def test_incomplete_stream_never_accepts_tools_or_releases_usage_reservation(tmp_path, arguments):
    source = tmp_path / "source.docx"
    source.write_bytes(build("21 00 00", "FIRE SUPPRESSION"))
    budget = llm.CallBudget(max_calls=1)
    with client_for(events(**arguments)) as client:
        conversation = Conversation("remove all headers and footers", [source], client=client,
                                    work_dir=tmp_path / "chat")
        with llm.budget_scope(budget):
            result = conversation.start()
    assert result.status == "failed" and result.spec is None
    assert conversation.tool_calls == [] and conversation.usage.calls == 0
    assert budget.calls == budget.failed_calls == 1
    assert budget.usage.calls == 0
    assert budget.accounted_tokens > budget.max_output_tokens
    assert budget.to_dict()["unreported_reserved_tokens"] == budget.accounted_tokens


def test_complete_sdk_stream_keeps_tools_and_accounts_final_usage(tmp_path):
    source = tmp_path / "source.docx"
    source.write_bytes(build("21 00 00", "FIRE SUPPRESSION"))
    budget = llm.CallBudget(max_calls=1)
    with client_for(events()) as client, llm.budget_scope(budget):
        conversation = Conversation("remove all headers and footers", [source], client=client,
                                    work_dir=tmp_path / "chat")
        result = conversation.start()
    assert result.status == "ok" and result.spec == REMOVE
    assert budget.calls == budget.usage.calls == 1 and budget.failed_calls == 0
    assert budget.accounted_tokens == 133 and budget.to_dict()["unreported_reserved_tokens"] == 0


def test_invalid_json_rejected_by_sdk_is_a_recorded_provider_failure(tmp_path):
    source = tmp_path / "source.docx"
    source.write_bytes(build("21 00 00", "FIRE SUPPRESSION"))
    stream = events()
    stream[2]["delta"]["partial_json"] = '{"spec": !'
    with client_for(stream) as client:
        conversation = Conversation("remove headers", [source], client=client, work_dir=tmp_path / "chat")
        result = conversation.start()
    assert result.status == "failed" and "malformed" in result.message
    assert conversation.tool_calls == []
