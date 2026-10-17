"""Persistent model/recovery checkpoints, with scripted providers and real local repair trials."""
from __future__ import annotations

import copy
import json

import pytest

from fake_claude import FakeClient, api_error, iteration, raise_on_enter, raise_on_final, resp, text, tool
from make_fixtures import build
from spec_surgeon import diagnostics as diag, llm, recovery
from spec_surgeon.conversation import Conversation, check_pairing


def events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def named(records, name):
    return [record for record in records if record["event"] == name]


def request(client):
    return llm.call(client, model=llm.DEFAULT_MODEL, system="private-system-instructions", tools=[],
                    messages=[{"role": "user", "content": "private-question"}])


def budget(**kwargs):
    return llm.CallBudget(max_cost_usd=None, max_total_tokens=None, **kwargs)


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "21 13 13.docx"
    path.write_bytes(build("21 13 13", "SPRINKLER SYSTEMS"))
    return path


def goal():
    return {"request": "Set the left margin to one inch", "summary": "Left margin", "mode": "formatting",
            "ops": [{"op": "set_page_setup", "params": {"margins": {"left": 1440}}}]}


def repair(spec):
    return resp(tool("submit_repair", {"candidate": {"kind": "spec", "spec": spec}}))


def test_model_start_is_flushed_before_waiting_and_usage_includes_fallbacks(tmp_path):
    path = tmp_path / "events.jsonl"
    answer = resp(text("private-answer"), model="claude-sonnet-5-5", iterations=[
        iteration("message", "claude-opus-5-5", inp=12, out=3),
        iteration("fallback_message", "claude-sonnet-5-5", inp=7, out=2)])
    answer._request_id = "req_observed"

    class WatchingClient(FakeClient):
        def _stream(self, **kwargs):
            stream = super()._stream(**kwargs)
            original = stream.get_final_message

            def final():
                recorded = events(path)
                assert named(recorded, "model.call.started")
                assert named(recorded, "model.stream.opened")
                assert not named(recorded, "model.call.succeeded")
                return original()

            stream.get_final_message = final
            return stream

    with diag.scope(path, conversation_id="conversation-1"):
        assert request(WatchingClient(answer)) is answer
    recorded = events(path)
    start = named(recorded, "model.call.started")[0]
    done = named(recorded, "model.call.succeeded")[0]
    assert start["model_call_id"] == done["model_call_id"]
    assert start["span_id"] == done["span_id"]
    assert done["conversation_id"] == "conversation-1"
    assert done["request_id"] == "req_observed"
    assert done["model"] == "claude-opus-5-5" and done["served_model"] == "claude-sonnet-5-5"
    assert done["fell_back"] and done["usage"]["input_tokens"] == 19
    assert done["usage"]["output_tokens"] == 5 and done["duration_ms"] >= 0
    assert "private-" not in path.read_text()


@pytest.mark.parametrize("failure,phase", [(raise_on_enter, "stream.open"), (raise_on_final, "stream.receive")])
def test_stream_failures_retain_phase_cause_request_id_and_budget_reservation(tmp_path, failure, phase):
    path = tmp_path / "events.jsonl"
    exc = api_error("provider disconnected", 503, "req_failure")
    exc.__cause__ = ConnectionResetError("underlying socket closed")
    spending = budget()
    with diag.scope(path), llm.budget_scope(spending), pytest.raises(type(exc)) as caught:
        request(FakeClient(failure(exc)))
    assert caught.value is exc
    failed = named(events(path), "model.call.failed")[0]
    assert failed["phase"] == phase and failed["request_id"] == "req_failure"
    assert failed["status_code"] == 503
    assert "underlying socket closed" in failed["traceback"]
    assert spending.calls == spending.failed_calls == 1 and spending.accounted_tokens > 0
    assert failed["budgets"][0]["failed_calls"] == 1
    assert named(events(path), "model.budget.reserved")
    assert not named(events(path), "model.budget.accounted")


def test_budget_refusal_is_visible_without_opening_another_request(tmp_path):
    path = tmp_path / "events.jsonl"
    client = FakeClient(resp(text("ok")))
    spending = budget(max_calls=1)
    with diag.scope(path), llm.budget_scope(spending):
        request(client)
        with pytest.raises(llm.BudgetExceeded):
            request(client)
    assert len(client.requests) == 1
    stopped = named(events(path), "model.budget.exhausted")[0]
    assert "call limit" in stopped["reason"]
    assert stopped["budgets"][0]["calls"] == 1
    assert named(events(path), "model.call.failed")[0]["phase"] == "preflight"


def test_bad_diagnostic_response_metadata_cannot_discard_success(tmp_path):
    class UnreadableUsage:
        model = llm.DEFAULT_MODEL

        @property
        def usage(self):
            raise ValueError("unreadable diagnostic metadata")

    answer = UnreadableUsage()
    path = tmp_path / "events.jsonl"
    with diag.scope(path):
        assert request(FakeClient(answer)) is answer
    assert "ValueError" in named(events(path), "model.call.succeeded")[0]["record_error"]


def test_tool_failure_is_persisted_and_still_paired_with_model_history(source, tmp_path):
    spec = goal()
    fake = FakeClient(resp(tool("unknown_tool", {}, "bad")),
                      resp(tool("submit_editspec", {"spec": spec}, "submit")))
    conversation = Conversation(spec["request"], [source], client=fake, work_dir=tmp_path / "session")
    assert conversation.start().status == "ok"
    check_pairing(fake.requests[1]["messages"])
    result = fake.requests[1]["messages"][-1]["content"][0]
    assert result["tool_use_id"] == "bad" and result["is_error"]
    recorded = events(tmp_path / "session/logs/events.jsonl")
    failure = named(recorded, "conversation.tool.failed")[0]
    assert failure["tool"] == "unknown_tool" and failure["tool_use_id"] == "bad"
    assert failure["conversation_id"] == conversation.id and failure["model_turn"] == 1
    assert "_dispatch" in failure["traceback"]
    assert named(recorded, "conversation.submission.accepted")
    assert named(recorded, "conversation.turn.succeeded")[0]["status"] == "ok"


def test_client_factory_defect_is_recorded_and_propagates_without_appending_history(source, tmp_path, monkeypatch):
    from spec_surgeon import compiler
    exc = RuntimeError("broken client configuration")

    def broken():
        raise exc

    monkeypatch.setattr(compiler, "_client", broken)
    conversation = Conversation("fix margins", [source], work_dir=tmp_path / "session")
    with pytest.raises(RuntimeError) as caught:
        conversation.start()
    assert caught.value is exc
    assert len(conversation.messages) == 1 and conversation.messages[0]["role"] == "user"
    recorded = events(tmp_path / "session/logs/events.jsonl")
    assert named(recorded, "conversation.request.failed")[0]["error"] == str(exc)
    assert named(recorded, "conversation.turn.failed")


def test_recovery_retains_rejection_evidence_and_correlates_trial_and_delivery(source, tmp_path):
    original = source.read_bytes()
    spec = goal()
    wrong = copy.deepcopy(spec)
    wrong["ops"][0]["params"]["margins"]["left"] = 2880
    result = recovery.recover([source], spec["request"], tmp_path / "recovery", spec=spec,
        options=recovery.RecoveryOptions(render=False, use_memory=False),
        client=FakeClient(repair(wrong), repair(spec)))
    assert result.ready and len(result.attempts) == 2
    path = result.out_dir / "logs/events.jsonl"
    recorded = events(path)
    failed = named(recorded, "recovery.trial.failed")[0]
    assert failed["recovery_attempt"] == 1 and "_candidate" in failed["traceback"]
    assert failed["recovery_id"] == result.recovery_id
    assert named(recorded, "recovery.trial.succeeded")[0]["recovery_attempt"] == 2
    assert named(recorded, "recovery.file.outcome")[-1]["status"] == "READY"
    assert json.loads((result.out_dir / "recovery.json").read_text())["recovery_id"] == result.recovery_id
    destination = recovery.deliver_recovery(result)
    assert (destination / source.name).read_bytes() == result.ready[0].output.read_bytes()
    assert source.read_bytes() == original
    delivered = named(events(path), "recovery.delivery.succeeded")[0]
    assert delivered["recovery_id"] == result.recovery_id
    assert named(events(path), "recovery.delivery.write.succeeded")[0]["output_md5"] == result.ready[0].output_md5
    with pytest.raises(ValueError, match="empty delivery folder"):
        recovery.deliver_recovery(result)
    assert "earlier delivery" in named(events(path), "recovery.delivery.failed")[0]["error"]


def test_unwritable_model_journal_does_not_change_call_result(tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("occupied")
    answer = resp(text("ok"))
    with diag.scope(blocker / "events.jsonl"):
        assert request(FakeClient(answer)) is answer


def test_model_diagnostics_match_the_configured_request_limits(tmp_path):
    path = tmp_path / "events.jsonl"
    client = FakeClient(resp(text("ok")))
    with diag.scope(path):
        llm.call(client, model=llm.DEFAULT_MODEL, system="", tools=[], messages=[],
                 max_output_tokens=4096, effort="medium")
    sent = client.requests[0]
    done = named(events(path), "model.call.succeeded")[0]
    assert sent["max_tokens"] == done["max_output_tokens"] == 4096
    assert sent["output_config"]["effort"] == done["effort"] == "medium"


def test_resumed_recovery_keeps_its_journal_identity_and_budget_history(source, tmp_path):
    spec = goal()
    options = recovery.RecoveryOptions(render=False, use_memory=False, max_calls=1)
    first = recovery.recover([source], spec["request"], tmp_path / "recovery", spec=spec, options=options,
        client=FakeClient(resp(tool("list_styles", {"file": source.name}))))
    assert not first.ready and first.budget["calls"] == 1
    assert recovery.load_recovery(first.out_dir).recovery_id == first.recovery_id
    second = recovery.resume_recovery(first.out_dir, client=FakeClient(repair(spec)))
    assert second.ready and second.recovery_id == first.recovery_id
    assert second.resource_history[0]["calls"] == second.budget["calls"] == 1
    recovery.deliver_recovery(second)
    recorded = events(first.out_dir / "logs/events.jsonl")
    for name in ("recovery.budget.stopped", "recovery.resume.succeeded", "recovery.delivery.succeeded"):
        assert named(recorded, name)[0]["recovery_id"] == first.recovery_id
    slices = named(recorded, "recovery.schedule.succeeded")
    assert len(slices) == 2 and all(row["recovery_id"] == first.recovery_id for row in slices)
    assert slices[0]["paused_reason"] and slices[1]["statuses"] == {"READY": 1}


def test_memory_initialization_failure_remains_explainable_while_repair_continues(source, tmp_path, monkeypatch):
    from spec_surgeon import repair_memory

    def unavailable():
        raise OSError("repair store cannot be opened")

    monkeypatch.setattr(repair_memory, "CaseStore", unavailable)
    spec = goal()
    result = recovery.recover([source], spec["request"], tmp_path / "recovery", spec=spec,
        options=recovery.RecoveryOptions(render=False, use_memory=True), client=FakeClient(repair(spec)))
    assert result.ready and any("Repair memory unavailable" in note for note in result.notes)
    failed = named(events(result.out_dir / "logs/events.jsonl"), "recovery.memory.open.failed")[0]
    assert "repair store cannot be opened" in failed["error"]
    assert "unavailable" in failed["traceback"]
