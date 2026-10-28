"""The call budget (``llm.CallBudget``) covers every model caller without changing ordinary llm.call behavior."""
from __future__ import annotations

import json
from contextvars import Context
from types import SimpleNamespace

import pytest

from fake_claude import FakeClient, api_error, iteration, raise_on_enter, raise_on_final, resp, text
from spec_surgeon import llm


def ask(client, **overrides):
    return llm.call(client, **({"model": "claude-opus-5-5", "system": "S", "tools": [],
                               "messages": [{"role": "user", "content": "hello"}]} | overrides))


def test_budget_routes_all_nested_calls_and_preserves_stream_and_fallback_settings():
    budget = llm.CallBudget(model="claude-sonnet-5-5", effort="low", max_calls=2, max_output_tokens=1234)
    fake = FakeClient(resp(text("first"), model=budget.model), resp(text("nested"), model=budget.model))

    def another_existing_caller():
        return ask(fake, model="claude-opus-4-8")

    with llm.budget_scope(budget):
        ask(fake)
        another_existing_caller()
        with pytest.raises(llm.BudgetExceeded, match="call limit"):
            ask(fake)
    assert budget.calls == budget.usage.calls == len(fake.requests) == 2
    assert budget.usage.tokens() == 240
    assert budget.exhausted_reason
    for request in fake.requests:
        assert request["model"] == "claude-sonnet-5-5"
        assert request["max_tokens"] == 1234
        assert request["output_config"] == {"effort": "low"}
        assert request["fallbacks"] == "default" and request["betas"] == [llm.FALLBACK_BETA]
        assert request["cache_control"] == {"type": "ephemeral"}


def test_scope_cleanup_on_failure_and_unrelated_contexts():
    fake = FakeClient(resp(), resp(), resp())
    budget = llm.CallBudget(model="claude-sonnet-5-5", max_output_tokens=42)
    with pytest.raises(RuntimeError, match="caller failed"):
        with llm.budget_scope(budget):
            ask(fake)
            Context().run(ask, fake)  # a different task must not inherit limits accidentally
            raise RuntimeError("caller failed")
    ask(fake)
    assert budget.calls == 1
    assert [r["max_tokens"] for r in fake.requests] == [42, llm.MAX_TOKENS, llm.MAX_TOKENS]
    assert fake.requests[-1]["output_config"] == {"effort": llm.EFFORT}


def test_nested_scopes_keep_outer_limits_and_same_budget_is_counted_once():
    fake = FakeClient(resp(), resp())
    outer = llm.CallBudget(model="claude-opus-5-5", max_calls=2, max_output_tokens=100)
    inner = llm.CallBudget(model="claude-sonnet-5-5", effort="low", max_calls=10, max_output_tokens=200)
    with llm.budget_scope(outer):
        with llm.budget_scope(outer):
            ask(fake)
        with llm.budget_scope(inner):
            ask(fake)
            with pytest.raises(llm.BudgetExceeded, match="call limit"):
                ask(fake)
    assert (outer.calls, inner.calls) == (2, 1)
    assert outer.usage.tokens() == 240 and inner.usage.tokens() == 120
    assert fake.requests[1]["max_tokens"] == 100
    assert fake.requests[1]["model"] == "claude-sonnet-5-5"


@pytest.mark.parametrize("field,value", [
    ("max_calls", True), ("max_calls", 1.0), ("max_calls", 0), ("max_calls", -2),
    ("max_calls", float("inf")), ("max_calls", None),
    ("max_output_tokens", False), ("max_output_tokens", "10"), ("max_output_tokens", 0),
    ("max_output_tokens", 128001), ("max_output_tokens", float("nan")),
    ("max_total_tokens", True), ("max_total_tokens", -1), ("max_total_tokens", 3.5),
    ("max_total_tokens", float("inf")),
    ("max_cost_usd", True), ("max_cost_usd", "5"), ("max_cost_usd", 0),
    ("max_cost_usd", -1.0), ("max_cost_usd", float("nan")), ("max_cost_usd", float("inf")),
    ("max_cost_usd", float("-inf")), ("max_cost_usd", 10 ** 1000),
    ("model", False), ("model", ""), ("model", " "), ("model", " claude-opus-5-5"),
    ("effort", True), ("effort", "unlimited"), ("effort", None), ("effort", "max"),
])
def test_configuration_rejects_non_finite_or_ambiguous_values(field, value):
    with pytest.raises(ValueError, match=field):
        llm.CallBudget(**{field: value})


def test_recovery_options_may_drop_the_spending_and_token_ceilings_but_never_the_call_limit():
    from spec_surgeon.recovery import RecoveryOptions
    RecoveryOptions(max_cost_usd=None, max_total_tokens=None).validate()
    for field, value in (("max_calls", None), ("max_cost_usd", 0), ("max_total_tokens", 0)):
        with pytest.raises(ValueError, match=field):
            RecoveryOptions(**{field: value}).validate()


def test_input_and_full_output_are_reserved_before_opening_the_stream():
    fake = FakeClient()
    budget = llm.CallBudget(max_output_tokens=1000, max_total_tokens=1100)
    with llm.budget_scope(budget), pytest.raises(llm.BudgetExceeded, match="token budget"):
        ask(fake)
    assert budget.calls == 0 and not fake.requests

    # Output alone costs $0.02; the input plus cache-write price must also fit before it is sent.
    money = llm.CallBudget(max_output_tokens=1000, max_cost_usd=0.0201)
    with llm.budget_scope(money), pytest.raises(llm.BudgetExceeded, match="spending ceiling"):
        ask(fake)
    assert money.calls == 0 and not fake.requests


def test_actual_usage_replaces_reservation_without_double_counting_caller_usage():
    fake = FakeClient(resp(inp=100, out=20), resp(inp=100, out=20))
    budget = llm.CallBudget(max_output_tokens=1000, max_total_tokens=2500, max_cost_usd=0.03)
    caller_usage = llm.Usage()
    with llm.budget_scope(budget):
        caller_usage.add(ask(fake), "claude-opus-5-5")
        caller_usage.add(ask(fake), "claude-opus-5-5")
    assert budget.calls == caller_usage.calls == budget.usage.calls == 2
    assert budget.accounted_tokens == caller_usage.tokens() == 240
    assert budget.estimated_cost_usd == pytest.approx(0.0016)
    assert budget.to_dict()["unreported_reserved_tokens"] == 0
    snapshot = budget.to_dict()
    budget.usage.by_model["claude-opus-5-5"]["input_tokens"] += 10
    assert snapshot["usage"]["input_tokens"] == 200
    assert snapshot["usage"]["by_model"]["claude-opus-5-5"]["input_tokens"] == 200
    assert "not a provider bill" in snapshot["cost_basis"]
    json.dumps(snapshot, allow_nan=False)


@pytest.mark.parametrize("failure", [raise_on_enter, raise_on_final])
def test_failed_api_calls_count_and_keep_unreported_reservations(failure):
    fake = FakeClient(failure(api_error("offline")), resp())
    budget = llm.CallBudget(max_calls=1)
    with llm.budget_scope(budget):
        with pytest.raises(Exception) as caught:
            ask(fake)
        assert llm.is_api_error(caught.value)
        with pytest.raises(llm.BudgetExceeded, match="call limit"):
            ask(fake)
    assert budget.calls == budget.failed_calls == len(fake.requests) == 1
    assert budget.usage.calls == 0
    assert budget.accounted_tokens > budget.max_output_tokens
    assert budget.estimated_cost_usd > 0



def sdk_error(cause, kind="APIConnectionError"):
    """The SDK's own wrapper as it raises one (``anthropic._base_client``: ``raise APIConnectionError(...) from err``)
    around the transport's error."""
    import anthropic
    import httpx2
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    err = getattr(anthropic, kind)(request=request)
    err.__cause__ = cause
    return err


def transport(name):
    import httpx2
    return getattr(httpx2, name)("[Errno -3] Temporary failure in name resolution")


@pytest.mark.parametrize("name,kind", [("ConnectError", "APIConnectionError"), ("ProxyError", "APIConnectionError"),
                                       ("ConnectTimeout", "APITimeoutError"), ("PoolTimeout", "APITimeoutError")])
def test_a_call_that_never_connected_keeps_no_reservation(name, kind):
    """The stress test's network-blocked attempts: the app reserved about $12.57 though no request reached Anthropic.
    A call whose connection — or proxy tunnel, or pooled connection — was never made sent nothing, so its reservation
    goes; it is still a failed call and an attempt against the call limit."""
    fake = FakeClient(raise_on_enter(sdk_error(transport(name), kind)), resp())
    budget = llm.CallBudget(max_calls=2)
    with llm.budget_scope(budget):
        with pytest.raises(Exception) as caught:
            ask(fake)
        assert llm.is_api_error(caught.value) and llm.never_sent(caught.value)
        assert budget.spent_usd == budget.estimated_cost_usd == 0 and budget.accounted_tokens == 0
        assert budget.calls == budget.failed_calls == 1 and budget.usage.calls == 0
        ask(fake)                                         # the next call is counted as usual
    assert budget.calls == 2 and budget.spent_usd > 0 and budget.accounted_tokens == budget.usage.tokens()



def test_the_sdk_s_own_refused_connection_keeps_no_reservation():
    """End to end with the real SDK and its HTTP stack: a connection refused on this computer (nothing listens on port
    1) comes back as the SDK's APIConnectionError over httpx2's and httpcore2's ConnectError, and costs nothing."""
    anthropic = pytest.importorskip("anthropic")
    client = anthropic.Anthropic(api_key="sk-ant-test-not-a-key", base_url="http://127.0.0.1:1", max_retries=0)
    budget = llm.CallBudget(max_calls=1)
    with llm.budget_scope(budget):
        with pytest.raises(Exception) as caught:
            ask(client)
    assert llm.is_api_error(caught.value) and llm.never_sent(caught.value)
    assert budget.failed_calls == 1 and budget.spent_usd == 0 and budget.accounted_tokens == 0

@pytest.mark.parametrize("failure", [
    raise_on_enter(sdk_error(transport("ReadTimeout"), "APITimeoutError")),   # the request may have been sent
    raise_on_enter(sdk_error(transport("RemoteProtocolError"))),
    raise_on_enter(sdk_error(transport("ReadError"))),
    raise_on_enter(api_error("Overloaded", status=529)),                       # an answer came back
    raise_on_enter(api_error("offline")),                                      # nothing says it was never sent
    raise_on_final(sdk_error(transport("ConnectError"))),                      # mid-stream: it was sent
])
def test_a_call_that_may_have_reached_the_api_keeps_its_reservation(failure):
    fake = FakeClient(failure)
    budget = llm.CallBudget(max_calls=1)
    with llm.budget_scope(budget):
        with pytest.raises(Exception):
            ask(fake)
    assert budget.failed_calls == 1 and budget.estimated_cost_usd > 0 and budget.spent_usd > 0


def test_never_sent_reads_the_transport_not_the_sdk_s_own_names():
    """Only the HTTP stack's names count, through the cause chain; an SDK class or an unrelated one of the same name
    does not, and a cycle in the chain ends."""
    assert llm.never_sent(transport("ConnectError")) and llm.never_sent(sdk_error(transport("ProxyError")))
    assert not llm.never_sent(api_error("ConnectError", module="anthropic._exceptions"))
    lookalike = type("ConnectError", (Exception,), {"__module__": "mypackage.net"})("x")
    assert not llm.never_sent(lookalike) and not llm.never_sent(sdk_error(lookalike))
    looped = RuntimeError("a")
    looped.__cause__ = RuntimeError("b")
    looped.__cause__.__cause__ = looped
    assert not llm.never_sent(looped)
    # an error merely raised while a connect error was being handled is not one
    during = sdk_error(transport("ReadError"))
    during.__context__ = transport("ConnectError")
    assert not llm.never_sent(during)


def test_retry_after_dropped_stream_must_fit_remaining_money():
    fake = FakeClient(raise_on_final(api_error("dropped")))
    budget = llm.CallBudget(max_calls=5, max_output_tokens=1000, max_cost_usd=0.04)
    with llm.budget_scope(budget):
        with pytest.raises(Exception):
            ask(fake)
        with pytest.raises(llm.BudgetExceeded, match="spending ceiling"):
            ask(fake)
    assert budget.calls == len(fake.requests) == 1


def test_sdk_retries_are_disabled_only_for_budgeted_calls():
    class ConfigurableClient(FakeClient):
        def with_options(self, **kwargs):
            self.options = kwargs
            return self

    fake = ConfigurableClient(resp(), resp())
    ask(fake)
    assert not hasattr(fake, "options")
    with llm.budget_scope(llm.CallBudget()):
        ask(fake)
    assert fake.options == {"max_retries": 0}


def test_unknown_requested_price_is_refused_but_explicit_token_only_budget_works():
    fake = FakeClient(resp(model="claude-mystery"))
    budget = llm.CallBudget(model="claude-mystery")
    with llm.budget_scope(budget), pytest.raises(llm.BudgetExceeded, match="price unknown"):
        ask(fake)
    assert not fake.requests and budget.calls == 0
    token_only = llm.CallBudget(model="claude-mystery", max_cost_usd=None)
    with llm.budget_scope(token_only):
        ask(fake)
    assert token_only.accounted_tokens == 120 and token_only.estimated_cost_usd is None


def test_fallback_iterations_are_charged_at_their_serving_prices():
    response = resp(model="claude-opus-4-8", iterations=[
        iteration("message", "claude-opus-5-5", 1000, 10, cache_read=200, cache_write=300),
        iteration("fallback_message", "claude-opus-4-8", 2000, 30),
    ])
    budget = llm.CallBudget()
    with llm.budget_scope(budget):
        assert ask(FakeClient(response)) is response
    expected = (1000 * 4 + 10 * 20 + 200 * 0.2 + 300 * 5 + 2000 * 5 + 30 * 25) / 1e6
    assert budget.estimated_cost_usd == pytest.approx(expected)
    assert budget.accounted_tokens == 3540 and budget.usage.fallbacks == 1


def test_unknown_fallback_price_keeps_response_and_stops_subsequent_calls():
    response = resp(model="claude-mystery", iterations=[
        iteration("message", "claude-opus-5-5"), iteration("fallback_message", "claude-mystery"),
    ])
    fake = FakeClient(response)
    budget = llm.CallBudget()
    with llm.budget_scope(budget):
        assert ask(fake) is response
        assert budget.exhausted_reason and budget.estimated_cost_usd is None
        with pytest.raises(llm.BudgetExceeded, match="unknown price"):
            ask(fake)
    assert budget.calls == len(fake.requests) == 1 and budget.usage.fallbacks == 1


@pytest.mark.parametrize("which", ["tokens", "money"])
def test_actual_usage_overrun_is_recorded_without_discarding_response(which):
    # The provider's fallback is outside our request count/output cap; its iterations can exceed a reservation.
    response = resp(model="claude-opus-4-8", iterations=[
        iteration("message", "claude-opus-5-5", 10000, 10000),
        iteration("fallback_message", "claude-opus-4-8", 10000, 10000),
    ])
    budget = llm.CallBudget(max_output_tokens=1000, max_total_tokens=3000 if which == "tokens" else None,
                            max_cost_usd=0.05 if which == "money" else None)
    fake = FakeClient(response)
    with llm.budget_scope(budget):
        assert ask(fake) is response
        assert budget.exhausted_reason
        with pytest.raises(llm.BudgetExceeded):
            ask(fake)
    assert budget.usage.tokens() == 40000 and len(fake.requests) == 1


def test_missing_usage_keeps_reservation_and_discloses_estimate():
    response = SimpleNamespace(model="claude-opus-5-5", usage=None, content=[], stop_reason="end_turn")
    budget = llm.CallBudget()
    with llm.budget_scope(budget):
        ask(FakeClient(response))
    assert budget.calls == budget.usage.calls == budget.unreported_calls == 1
    assert budget.usage.tokens() == 0 and budget.accounted_tokens > 8000
    assert budget.estimated_cost_usd > 0


def test_unreported_unknown_model_never_becomes_free_in_diagnostics():
    unknown = llm.CallBudget(model="claude-mystery", max_cost_usd=None)
    with llm.budget_scope(unknown), pytest.raises(Exception):
        ask(FakeClient(raise_on_final(api_error("dropped"))))
    assert unknown.estimated_cost_usd is None
    assert unknown.to_dict()["unreported_reserved_cost_usd"] is None

    fallback = llm.CallBudget()
    response = SimpleNamespace(model="claude-mystery", usage=None, stop_reason="end_turn")
    with llm.budget_scope(fallback):
        assert ask(FakeClient(response)) is response
        with pytest.raises(llm.BudgetExceeded, match="unknown price"):
            ask(FakeClient())
    assert fallback.estimated_cost_usd is None


def test_estimator_counts_full_prompt_tools_and_images_without_base64_size():
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "x" * 100000}}
    messages = [{"role": "user", "content": [image]}]
    base = llm.estimate_input_tokens(system="S", tools=[], messages=[])
    visual = llm.estimate_input_tokens(system="S", tools=[], messages=messages)
    assert base + 4784 < visual < base + 6000
    more = llm.estimate_input_tokens(system="S" * 1001, tools=[{"description": "x" * 1000}], messages=messages)
    assert more >= visual + 2000
    sdk = SimpleNamespace(type="text", text="Some response text")
    assert llm.estimate_input_tokens(system="S", tools=[], messages=[{"role": "assistant", "content": [sdk]}]) > base


def test_unknown_multimodal_estimate_fails_before_network_and_records_reason():
    budget, fake = llm.CallBudget(), FakeClient()
    messages = [{"role": "user", "content": [{"type": "document", "source": {"url": "https://example.test/a.pdf"}}]}]
    with llm.budget_scope(budget), pytest.raises(llm.BudgetExceeded, match="cannot estimate"):
        ask(fake, messages=messages)
    assert budget.exhausted_reason and not fake.requests
    assert not llm.is_api_error(llm.BudgetExceeded("limit"))


def test_advisory_visual_review_cannot_hide_an_exhausted_shared_budget(tmp_path, monkeypatch):
    from spec_surgeon import vision
    monkeypatch.setattr(vision, "rasterize_pages", lambda *args: [tmp_path / "page.png"])
    monkeypatch.setattr(vision, "image_block", lambda path: {
        "type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "image"}})
    sample = SimpleNamespace(source=tmp_path / "a.docx", source_pdf=tmp_path / "before.pdf",
                             output_pdf=tmp_path / "after.pdf", look_pages=[1])
    fake, budget = FakeClient(resp()), llm.CallBudget(max_calls=1)
    with llm.budget_scope(budget):
        ask(fake)
        review = vision.review_samples("Change the font", [sample], tmp_path / "review", lambda: fake)
    assert "call limit" in review.skipped
    assert budget.exhausted_reason and "call limit" in budget.exhausted_reason
    assert not review.findings and budget.calls == len(fake.requests) == 1
