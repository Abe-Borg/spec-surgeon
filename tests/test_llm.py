"""llm.py — the one place Spec Surgeon calls Claude: the streamed request (max_tokens 64000, effort, refusal fallback),
the fallback echo rule, which model served a response, token/cost accounting; and the real SDK, offline, against a
canned event stream (proves the request body the SDK actually sends and that its parsed message works with llm.py)."""
from __future__ import annotations

import json

import pytest

from fake_claude import FakeClient, fallback, iteration, resp, text, thinking, tool
from spec_surgeon import llm


def test_call_streams_with_the_agreed_settings():
    fake = FakeClient(resp(text("hi"), stop="end_turn"))
    r = llm.call(fake, model="claude-opus-5-5", system="S", tools=[{"name": "t"}], messages=[{"role": "user", "content": "x"}])
    assert r.content[0].text == "hi"
    req = fake.requests[0]
    assert req["max_tokens"] == 64000 == llm.MAX_TOKENS
    assert req["output_config"] == {"effort": "high"}
    assert req["fallbacks"] == "default" and req["betas"] == [llm.FALLBACK_BETA]
    assert req["cache_control"] == {"type": "ephemeral"}
    assert "thinking" not in req and "tool_choice" not in req       # both are refused by Opus 5.5 when forced
    with pytest.raises(AssertionError, match="non-streaming"):
        fake.beta.messages.create(model="m")


def test_history_content_drops_what_a_declined_attempt_produced():
    pre_tool, post_tool = tool("ask_user", {}, "pre"), tool("submit_editspec", {}, "post")
    r = resp(thinking("A"), text("before"), pre_tool, fallback(), thinking("B"), post_tool)
    content = llm.history_content(r)
    assert [getattr(b, "type") for b in content] == ["text", "thinking", "tool_use"]
    assert [b.id for b in llm.tool_uses(content)] == ["post"]
    assert content[1].signature == "B"                               # kept unchanged
    plain = resp(thinking("A"), pre_tool)
    assert llm.history_content(plain) == plain.content               # no fallback: content unchanged, thinking kept


def test_served_model_fallback_and_usage_by_iteration():
    r = resp(text("x"), model="claude-opus-4-8",
             iterations=[iteration("message", "claude-opus-5-5", 1000, 0), iteration("fallback_message", "claude-opus-4-8", 1000, 500)])
    assert llm.served(r) == ("claude-opus-4-8", True)
    u = llm.Usage()
    u.add(r, "claude-opus-5-5")
    assert u.as_dict()["input_tokens"] == 2000 and u.as_dict()["output_tokens"] == 500   # iterations, not + top-level
    assert u.cost_usd() == pytest.approx(1000 * 4 / 1e6 + (1000 * 5 + 500 * 25) / 1e6)
    assert u.fallbacks == 1 and "fallback" in u.line()
    plain = llm.Usage()
    plain.add(resp(text("x")), "claude-opus-5-5")
    assert plain.as_dict() == {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 0,
                               "cache_creation_input_tokens": 0}
    unknown = llm.Usage()
    unknown.add(resp(text("x"), model="claude-mystery-9"), "claude-mystery-9")
    assert unknown.cost_usd() is None and "cost unknown" in unknown.line()


def test_stop_problems():
    from types import SimpleNamespace
    r = resp(stop="refusal", details=SimpleNamespace(category="cyber", explanation="no"))
    assert "declined" in llm.stop_problem(r) and "cyber" in llm.stop_problem(r)
    assert "max_tokens" in llm.stop_problem(resp(stop="max_tokens"))
    assert "pause_turn" in llm.stop_problem(resp(stop="pause_turn"))
    assert llm.stop_problem(resp(stop="tool_use")) is None and llm.stop_problem(resp(stop="end_turn")) is None


def _sse(events):
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode("utf-8")


def test_the_real_sdk_offline_sends_what_we_mean_and_its_message_works_with_llm():
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    seen = []

    def handler(request):
        seen.append({"body": json.loads(request.content), "beta": request.headers.get("anthropic-beta")})
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=_sse([
            ("message_start", {"type": "message_start", "message": {
                "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [],
                "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 1}}}),
            ("content_block_start", {"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "thinking", "thinking": "", "signature": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "signature_delta", "signature": "SIG"}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {
                "type": "fallback", "from": {"model": "claude-opus-5-5"}, "to": {"model": "claude-opus-4-8"}, "trigger": None}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 1}),
            ("content_block_start", {"type": "content_block_start", "index": 2, "content_block": {
                "type": "tool_use", "id": "tu_1", "name": "ask_user", "input": {}}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 2, "delta": {
                "type": "input_json_delta", "partial_json": "{\"questions\": [\"Which sections?\"]}"}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 2}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                               "usage": {"output_tokens": 30, "iterations": [
                                   {"type": "message", "model": "claude-opus-5-5", "input_tokens": 10, "output_tokens": 5},
                                   {"type": "fallback_message", "model": "claude-opus-4-8", "input_tokens": 12,
                                    "output_tokens": 25}]}}),
            ("message_stop", {"type": "message_stop"}),
        ]))

    client = anthropic.Anthropic(api_key="test-key", max_retries=0,
                                 http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    tools = [{"name": "ask_user", "description": "ask", "input_schema": {"type": "object", "properties": {}}}]
    msgs = [{"role": "user", "content": "hi"}]
    r = llm.call(client, model="claude-opus-5-5", system="S", tools=tools, messages=msgs)
    body = seen[0]["body"]
    assert body["max_tokens"] == 64000 and body["stream"] is True and body["fallbacks"] == "default"
    assert body["output_config"] == {"effort": "high"} and body["cache_control"] == {"type": "ephemeral"}
    assert "thinking" not in body and "tool_choice" not in body
    assert llm.FALLBACK_BETA in (seen[0]["beta"] or "")
    assert llm.served(r) == ("claude-opus-4-8", True)
    content = llm.history_content(r)
    assert [llm.block_type(b) for b in content] == ["tool_use"]      # the declined attempt's thinking is not echoed
    use = llm.tool_uses(content)[0]
    assert use.input == {"questions": ["Which sections?"]}
    u = llm.Usage()
    u.add(r, "claude-opus-5-5")
    assert u.by_model["claude-opus-4-8"]["output_tokens"] == 25 and u.fallbacks == 1
    # the SDK's own block objects go back into the history and serialize (the next request of a conversation)
    msgs += [{"role": "assistant", "content": content},
             {"role": "user", "content": [{"type": "tool_result", "tool_use_id": use.id, "content": "The user replied: all"}]}]
    llm.call(client, model="claude-opus-5-5", system="S", tools=tools, messages=msgs)
    second = seen[1]["body"]["messages"]
    assert second[1]["content"][0]["type"] == "tool_use" and second[1]["content"][0]["id"] == "tu_1"
    assert second[2]["content"][0]["tool_use_id"] == "tu_1"
