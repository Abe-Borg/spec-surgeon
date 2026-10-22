"""Fast mode (Settings → "Fast responses", ``llm.set_fast_mode``): the same model asked for ``speed="fast"`` only when the
user turned it on and only for a model Anthropic serves fast; priced at the fast table (twice every standard price); a
fast call the fast capacity cannot take is re-sent at once at standard speed, never waited out; a key refused fast mode
is told once and runs at standard speed from then on; the real SDK sends the setting as the request body's ``speed``."""
from __future__ import annotations

import json

import pytest

from fake_claude import FakeClient, api_error, raise_on_enter, resp, text
from spec_surgeon import llm

ARGS = dict(model="claude-opus-5-5", system="S", tools=[], messages=[{"role": "user", "content": "x"}])


def fast_resp(*blocks, model="claude-opus-5-5", inp=1000, out=500, speed="fast"):
    r = resp(*blocks, stop="end_turn", model=model, inp=inp, out=out)
    r.usage.speed = speed
    return r


@pytest.fixture
def fast():
    llm.set_fast_mode(True)
    yield
    llm.set_fast_mode(False)


def test_off_by_default_no_call_asks_for_speed():
    assert llm.FAST_MODE is False
    fake = FakeClient(resp(text("hi"), stop="end_turn"))
    llm.call(fake, **ARGS)
    assert "speed" not in fake.requests[0] and fake.requests[0]["betas"] == [llm.FALLBACK_BETA]


def test_on_a_call_to_opus_5_5_asks_for_fast_without_the_preview_header(fast):
    fake = FakeClient(fast_resp(text("hi")))
    llm.call(fake, **ARGS)
    req = fake.requests[0]
    assert req["speed"] == "fast"
    assert req["betas"] == [llm.FALLBACK_BETA]                 # Opus 5.5 serves fast without the research-preview header
    assert req["output_config"] == {"effort": "high"} and req["fallbacks"] == "default"   # nothing else changes


def test_the_research_preview_models_carry_the_fast_header_and_a_model_without_fast_never_asks(fast):
    fake = FakeClient(fast_resp(text("hi"), model="claude-opus-4-8"), resp(text("hi"), stop="end_turn",
                                                                            model="claude-sonnet-5-5"))
    llm.call(fake, **{**ARGS, "model": "claude-opus-4-8"})
    llm.call(fake, **{**ARGS, "model": "claude-sonnet-5-5"})
    assert fake.requests[0]["speed"] == "fast" and fake.requests[0]["betas"] == [llm.FALLBACK_BETA, llm.FAST_BETA]
    assert "speed" not in fake.requests[1] and fake.requests[1]["betas"] == [llm.FALLBACK_BETA]


def test_a_budget_that_changes_the_model_decides_the_speed_by_the_model_it_sends(fast):
    fake = FakeClient(resp(text("hi"), stop="end_turn", model="claude-sonnet-5-5"))
    with llm.budget_scope(llm.CallBudget(model="claude-sonnet-5-5", max_cost_usd=None, max_total_tokens=None)):
        llm.call(fake, **ARGS)
    assert fake.requests[0]["model"] == "claude-sonnet-5-5" and "speed" not in fake.requests[0]


@pytest.mark.parametrize("failure", [api_error("rate limited", status=429), api_error("Overloaded", status=529)])
def test_a_fast_call_the_fast_capacity_cannot_take_is_sent_again_at_once_at_standard_speed(fast, failure):
    # tests/conftest.py turns the ordinary re-sends off (RETRIES 0): this one is not a re-send of the same request
    fake = FakeClient(raise_on_enter(failure), resp(text("standard"), stop="end_turn"))
    assert llm.call(fake, **ARGS).content[0].text == "standard"
    assert fake.requests[0]["speed"] == "fast" and "speed" not in fake.requests[1]
    assert llm.fast_unavailable() is None                      # capacity passes: the next call asks for fast again
    fake = FakeClient(fast_resp(text("fast again")))
    llm.call(fake, **ARGS)
    assert fake.requests[0]["speed"] == "fast"


def test_a_standard_call_that_is_rate_limited_is_not_re_sent_by_the_fast_rule():
    fake = FakeClient(raise_on_enter(api_error("rate limited", status=429)), resp(text("never")))
    with pytest.raises(Exception, match="rate limited"):
        llm.call(fake, **ARGS)
    assert len(fake.requests) == 1


def test_a_key_refused_fast_mode_runs_at_standard_speed_until_the_setting_is_turned_on_again(fast):
    refusal = api_error("speed: fast mode is not available for this organization", status=400)
    fake = FakeClient(raise_on_enter(refusal), resp(text("standard"), stop="end_turn"),
                      resp(text("still standard"), stop="end_turn"))
    assert llm.call(fake, **ARGS).content[0].text == "standard"
    llm.call(fake, **ARGS)
    assert fake.requests[0]["speed"] == "fast"
    assert "speed" not in fake.requests[1] and "speed" not in fake.requests[2]
    assert "fast mode is not available" in llm.fast_unavailable()
    llm.set_fast_mode(False)
    assert llm.fast_unavailable() is None                      # off: nothing to explain
    llm.set_fast_mode(True)                                    # on again: tried again
    fake = FakeClient(fast_resp(text("fast")))
    llm.call(fake, **ARGS)
    assert fake.requests[0]["speed"] == "fast"


def test_a_bad_request_that_does_not_name_the_speed_propagates_and_is_not_re_sent(fast):
    fake = FakeClient(raise_on_enter(api_error("messages: a thinking block was edited", status=400)), resp(text("x")))
    with pytest.raises(Exception, match="thinking block"):
        llm.call(fake, **ARGS)
    assert len(fake.requests) == 1 and llm.fast_unavailable() is None


def test_a_fast_call_fails_at_once_on_a_429_instead_of_the_sdk_waiting_it_out(fast):
    seen = []

    class Client(FakeClient):
        def with_options(self, **kw):
            seen.append(kw)
            return self

    llm.call(Client(fast_resp(text("hi"))), **ARGS)
    assert seen == [{"max_retries": 0}]


def test_fast_prices_are_twice_every_standard_price():
    assert llm.FAST_PRICE_FACTOR == 2
    for model, fast_prices in llm.FAST_PRICES.items():
        assert fast_prices == tuple(llm.FAST_PRICE_FACTOR * p for p in llm.PRICES[model]), model


def test_tokens_served_fast_are_their_own_row_and_cost_twice_as_much():
    u_fast, u_std = llm.Usage(), llm.Usage()
    u_fast.add(fast_resp(text("x")), "claude-opus-5-5")
    u_std.add(fast_resp(text("x"), speed="standard"), "claude-opus-5-5")
    assert set(u_fast.by_model) == {"claude-opus-5-5 (fast)"} and set(u_std.by_model) == {"claude-opus-5-5"}
    assert u_std.cost_usd() == pytest.approx((1000 * 4.00 + 500 * 20.00) / 1e6)
    assert u_fast.cost_usd() == pytest.approx(2 * u_std.cost_usd())
    assert "fast mode" in u_fast.line() and "fast mode" not in u_std.line()
    assert u_fast.to_dict()["served_models"] == ["claude-opus-5-5"]      # the model is the same; only the speed differs
    # a model served fast without a fast price is "cost unknown", never priced at the standard table
    u = llm.Usage()
    u.add(fast_resp(text("x"), model="claude-opus-9"), "claude-opus-9")
    assert u.cost_usd() is None


def test_a_fallback_attempt_under_a_fast_response_is_fast_only_when_its_model_is_served_fast():
    from fake_claude import iteration
    r = fast_resp(text("x"))
    r.usage.iterations = [iteration("message", "claude-opus-5-5", inp=10, out=5),
                          iteration("fallback_message", "claude-sonnet-5-5", inp=12, out=25)]
    u = llm.Usage()
    u.add(r, "claude-opus-5-5")
    assert set(u.by_model) == {"claude-opus-5-5 (fast)", "claude-sonnet-5-5"}


def test_a_budgeted_fast_call_reserves_and_accounts_at_the_fast_prices(fast):
    budget = llm.CallBudget(max_calls=4, max_total_tokens=None, max_cost_usd=1000.0)
    fake = FakeClient(fast_resp(text("hi")))
    seen = []
    original = budget._preflight

    def preflight(*a, **kw):
        seen.append(kw.get("fast"))
        return original(*a, **kw)

    budget._preflight = preflight
    with llm.budget_scope(budget):
        llm.call(fake, **ARGS)
    assert seen == [True]
    assert budget.spent_usd == pytest.approx(2 * (1000 * 4.00 + 500 * 20.00) / 1e6)
    standard = llm.CallBudget(max_calls=4, max_total_tokens=None, max_cost_usd=1000.0)
    assert (budget._preflight(ARGS["model"], 1000, 100, fast=True)[1]
            == pytest.approx(2 * standard._preflight(ARGS["model"], 1000, 100)[1]))


def test_a_stopped_fast_call_is_settled_at_the_fast_rate_and_prices():
    budget = llm.CallBudget(max_calls=48, max_cost_usd=None, max_total_tokens=None)
    budget._reserve((10_000 + llm.MAX_TOKENS, 1.0))
    budget._settle_stopped("claude-opus-5-5", (10_000 + llm.MAX_TOKENS, 1.0), None, llm.MAX_TOKENS, 4.0, fast=True)
    row = budget.usage.by_model["claude-opus-5-5 (fast)"]
    assert row["output_tokens"] == 4 * llm.STOPPED_OUTPUT_RATE * llm.FAST_SPEEDUP
    assert budget.spent_usd == pytest.approx((10_000 * 8.0 + row["output_tokens"] * 40.0) / 1e6)


def test_the_records_name_the_speed_that_served():
    r = fast_resp(text("x"))
    assert llm.response_entry(1, r, 0.1)["speed"] == "fast"
    assert llm._response_metadata(r, "claude-opus-5-5")["served_speed"] == "fast"


def _sse(events):
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode("utf-8")


def test_the_real_sdk_offline_sends_speed_in_the_body_and_reports_it_back(fast):
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    seen = []

    def handler(request):
        seen.append({"body": json.loads(request.content), "beta": request.headers.get("anthropic-beta")})
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=_sse([
            ("message_start", {"type": "message_start", "message": {
                "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [],
                "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": 1000, "output_tokens": 1, "speed": "fast"}}}),
            ("content_block_start", {"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "text_delta", "text": "hi"}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                               "usage": {"output_tokens": 500}}),
            ("message_stop", {"type": "message_stop"}),
        ]))

    client = anthropic.Anthropic(api_key="test-key",
                                 http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    r = llm.call(client, **ARGS)
    assert seen[0]["body"]["speed"] == "fast" and llm.FAST_BETA not in (seen[0]["beta"] or "")
    assert r.usage.speed == "fast"
    u = llm.Usage()
    u.add(r, "claude-opus-5-5")
    assert u.by_model["claude-opus-5-5 (fast)"]["output_tokens"] == 500


def test_the_real_sdk_offline_rate_limit_on_fast_is_sent_again_at_standard_without_waiting(fast):
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if body.get("speed") == "fast":
            return httpx2.Response(429, headers={"retry-after": "30", "content-type": "application/json"},
                                   content=json.dumps({"type": "error", "error": {
                                       "type": "rate_limit_error", "message": "fast mode rate limit"}}).encode())
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=_sse([
            ("message_start", {"type": "message_start", "message": {
                "id": "msg_2", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [],
                "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 1, "speed": "standard"}}}),
            ("content_block_start", {"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "text_delta", "text": "standard"}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                               "usage": {"output_tokens": 3}}),
            ("message_stop", {"type": "message_stop"}),
        ]))

    client = anthropic.Anthropic(api_key="test-key",
                                 http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    r = llm.call(client, **ARGS)
    assert [b.get("speed") for b in seen] == ["fast", None]   # one fast attempt (no SDK wait), then standard
    assert r.content[0].text == "standard" and r.usage.speed == "standard"


def test_set_fast_mode_is_what_the_window_turns_on():
    llm.set_fast_mode(True)
    try:
        assert llm.FAST_MODE and llm.fast_for("claude-opus-5-5") and not llm.fast_for("claude-sonnet-5-5")
    finally:
        llm.set_fast_mode(False)
    assert not llm.fast_for("claude-opus-5-5")
