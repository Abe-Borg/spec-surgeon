"""llm.py — the one place Spec Surgeon calls Claude: the streamed request (max_tokens 64000, effort, refusal fallback),
the fallback echo rule, which model served a response, token/cost accounting; and the real SDK, offline, against a
canned event stream (proves the request body the SDK actually sends and that its parsed message works with llm.py).
Also the one classification of a failed call (``is_api_error``: an API error is a recorded failure, anything else a
defect) and the transcript entries every caller records, which are total and never hold image data."""
from __future__ import annotations

import json

import pytest

from fake_claude import FakeClient, api_error, fallback, iteration, resp, text, thinking, tool
from spec_surgeon import compiler, llm


def test_call_streams_with_the_agreed_settings():
    fake = FakeClient(resp(text("hi"), stop="end_turn"))
    r = llm.call(fake, model="claude-opus-5-5", system="S", tools=[{"name": "t"}], messages=[{"role": "user", "content": "x"}])
    assert r.content[0].text == "hi"
    req = fake.requests[0]
    assert req["max_tokens"] == 64000 == llm.MAX_TOKENS
    assert req["output_config"] == {"effort": "max"}
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
    assert body["output_config"] == {"effort": "max"} and body["cache_control"] == {"type": "ephemeral"}
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


# ---- the classification of a failed call, and the transcript entries ------------------------------------------------

def test_an_api_error_is_the_sdks_its_http_stacks_the_networks_or_claude_missing():
    for module in ("anthropic._exceptions", "httpx", "httpx2", "httpcore", "httpcore2._exceptions"):
        assert llm.is_api_error(api_error(module=module)), module
    assert llm.is_api_error(ConnectionResetError("reset")) and llm.is_api_error(TimeoutError())
    assert llm.is_api_error(OSError(101, "Network is unreachable"))
    assert llm.is_api_error(llm.ClaudeUnavailable("install the Claude layer"))
    assert llm.is_api_error(TypeError('Could not resolve authentication method. Expected either api_key or '
                                      'auth_token to be set.'))                        # the SDK without a key
    for defect in (TypeError("stream() got an unexpected keyword argument 'x'"), AssertionError("scripted turns ran out"),
                   KeyError("x"), ValueError("x"), AttributeError("x"),
                   RuntimeError("no live Claude calls in tests"), api_error(module="spec_surgeon.llm")):
        assert not llm.is_api_error(defect), defect
    assert compiler.is_api_error is llm.is_api_error and compiler.api_error_text is llm.api_error_text  # master's names
    assert compiler.block_dict is llm.block_dict


def test_the_real_sdks_errors_are_api_errors():
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    assert llm.is_api_error(httpx2.RemoteProtocolError("peer closed connection"))      # a stream that drops mid-way
    assert llm.is_api_error(anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x")))


def test_the_sdk_without_a_key_raises_what_is_api_error_accepts():
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    client = anthropic.Anthropic(api_key=None, auth_token=None, max_retries=0,
                                 http_client=anthropic.DefaultHttpxClient(
                                     transport=httpx2.MockTransport(lambda r: httpx2.Response(500))))
    with pytest.raises(Exception) as caught:
        llm.call(client, model="claude-opus-5-5", system="S", tools=[], messages=[{"role": "user", "content": "x"}])
    assert llm.is_api_error(caught.value), repr(caught.value)


def test_api_error_text_names_the_kind_status_and_request_id():
    from types import SimpleNamespace
    assert llm.api_error_text(api_error("Overloaded", 529, "req_9")) == "APIStatusError: Overloaded · HTTP 529 · request id req_9"
    e = api_error("Bad gateway", 502)
    e.response = SimpleNamespace(headers={"request-id": "req_h"})
    assert llm.api_error_text(e).endswith("HTTP 502 · request id req_h")
    assert llm.failure_text(llm.ClaudeUnavailable("pip install it")) == "Claude is not available: pip install it"
    assert llm.failure_text(api_error("x"), "the call to the model failed") == "the call to the model failed: APIConnectionError: x"
    assert llm.error_entry(3, api_error("x", 500), 1.234) == {"turn": 3, "error": "APIStatusError: x · HTTP 500", "seconds": 1.23}


def test_a_response_entry_is_plain_data_with_the_serving_model_and_every_block():
    spec = {"ops": [{"op": "x"}]}
    r = resp(thinking("A", "t" * 9000), text("hi"), tool("submit_editspec", {"spec": spec}, "s1"), fallback(),
             model="claude-opus-4-8", iterations=[iteration("fallback_message", "claude-opus-4-8")])
    r._request_id = "req_7"
    e = llm.response_entry(2, r, 0.5)
    assert (e["turn"], e["role"], e["stop_reason"], e["request_id"], e["model"], e["fell_back"]) == (
        2, "assistant", "tool_use", "req_7", "claude-opus-4-8", True)
    assert e["usage"] == {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 0,
                          "cache_creation_input_tokens": 0}
    assert [b["type"] for b in e["content"]] == ["thinking", "text", "tool_use", "fallback"]
    assert len(e["content"][0]["thinking"]) == 8000 and e["content"][3] == {"type": "fallback", "from": "claude-opus-5-5",
                                                                           "to": "claude-opus-4-8"}
    spec["ops"][0]["op"] = "changed later"                     # a caller may change the input later: the record keeps it
    assert e["content"][2]["input"] == {"spec": {"ops": [{"op": "x"}]}}
    json.dumps(e)


def test_the_entries_are_total_and_never_hold_image_data():
    class NoCopy:
        def __deepcopy__(self, memo):
            raise RuntimeError("cannot copy")
    from types import SimpleNamespace
    bad = SimpleNamespace(type="tool_use", id="t1", name="n", input={"x": NoCopy()})
    assert llm.block_dict(bad) == {"type": "tool_use", "record_error": "RuntimeError: cannot copy"}
    assert "record_error" in llm.block_dict(SimpleNamespace(type="text", text="x", input=5))
    assert llm.response_entry(1, resp(bad, text("ok")), 0.1)["content"][1] == {"type": "text", "text": "ok"}

    class Boom:
        @property
        def content(self):
            raise RuntimeError("unreadable response")
    assert llm.response_entry(4, Boom(), 0.1)["record_error"].startswith("RuntimeError")
    assert "record_error" in llm.results_entry(1, [None])
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}}
    sent = llm.transcript_content([{"type": "tool_result", "tool_use_id": "v1",
                                    "content": [{"type": "text", "text": "a.docx — page 1"}, image]},
                                   {"type": "text", "text": "[note from the app] PREVIEW"}, image])
    assert sent == [{"type": "tool_result", "tool_use_id": "v1", "is_error": False, "content": "a.docx — page 1 | [image]"},
                    {"type": "text", "text": "[note from the app] PREVIEW"}, {"type": "image"}]
    assert llm.results_entry(1, [{"tool_use_id": "v1", "content": [image]}])["tool_results"][0]["content"] == "[image]"
    assert "iVBOR" not in json.dumps([sent, llm.transcript_content("x" * 9000)]) and len(llm.transcript_content("x" * 9000)) == 6000
