"""Sustained recovery uses real trials and scripted model responses, never live APIs."""
from __future__ import annotations

import copy
import json
import os

import pytest

from fake_claude import FakeClient, api_error, raise_on_enter, resp, thinking, tool
from make_fixtures import build
from spec_surgeon import recovery, vision
from test_recovery import candidate, margin, opts, spec, submit


def documents(tmp_path, count=2):
    paths = []
    for i in range(count):
        path = tmp_path / f"source-{i}.docx"
        path.write_bytes(build(f"21 13 {i:02d}", "SPRINKLER SYSTEMS"))
        paths.append(path)
    return paths


def distinct_failures(paths):
    return [{"source": str(p), "code": f"case-{i}"} for i, p in enumerate(paths)]


def test_resume_keeps_ready_candidates_and_answers_only_the_pending_group(tmp_path):
    paths = documents(tmp_path)
    goal = spec([margin()])
    first = recovery.recover(paths, goal["request"], tmp_path / "session", spec=goal, options=opts(),
        prior_failures=distinct_failures(paths), client=FakeClient(submit(candidate(goal)),
            resp(tool("need_user", {"question": "Use these margins on the schedule?"}))))
    ready_output = first.ready[0].output
    ready_bytes, ready_stat = ready_output.read_bytes(), ready_output.stat().st_mtime_ns
    client = FakeClient(submit(candidate(goal)))
    second = recovery.resume_recovery(first.out_dir, request={"question_id": "q0001", "answer": "Yes, use them."},
                                      options=opts(), client=client)
    assert len(second.ready) == 2 and second.out_dir == first.out_dir
    assert second.files[0].attempts == 1 and ready_output.read_bytes() == ready_bytes
    assert ready_output.stat().st_mtime_ns == ready_stat
    assert second.question_records[0]["status"] == "answered" and not second.questions
    assert second.decisions[0]["files"] == [str(paths[1])]
    assert second.resource_history[0]["calls"] == 2 and second.budget["calls"] == 1
    assert len(client.requests) == 1
    assert second.request == goal["request"], "Scoped decisions are not global approved wording"


def test_scoped_answer_splits_a_group_without_resuming_unanswered_documents(tmp_path):
    paths = documents(tmp_path)
    goal = spec([margin()])
    first = recovery.recover(paths, goal["request"], tmp_path / "session", spec=goal, options=opts(),
        client=FakeClient(resp(tool("need_user", {"question": "Which files need the margins?"}))))
    client = FakeClient(submit(candidate(goal)))
    second = recovery.resume_recovery(first.out_dir,
        request={"answer": "This document uses the requested margins.", "files": [str(paths[0])]},
        options=opts(), client=client)
    assert [f.status for f in second.files] == ["READY", "NEEDS_INPUT"]
    assert len(client.requests) == 1 and second.questions
    assert second.decisions[0]["files"] == [str(paths[0])]


@pytest.mark.parametrize("sliced", [False, True])
def test_scoped_unsupported_keeps_other_files_running_and_can_resume_later(tmp_path, sliced):
    paths = documents(tmp_path)
    goal = spec([margin()])
    originals = [p.read_bytes() for p in paths]
    reason = "This document needs an unsupported layout capability."
    unavailable = resp(thinking("Keep this signed investigation"),
                       tool("unsupported", {"files": [str(paths[0])], "reason": reason}, "unsupported"),
                       tool("list_styles", {"file": str(paths[0])}, "after-stop"))
    policy = opts(group_call_slice=1)
    first = recovery.recover(paths, goal["request"], tmp_path / "session", spec=goal, options=policy,
        _single_round=sliced, client=FakeClient(unavailable) if sliced else FakeClient(unavailable, submit(candidate(goal))))
    if sliced:
        reopened = recovery.load_recovery(first.out_dir)
        assert len(reopened.session_state["groups"]) == 2
        client = FakeClient(submit(candidate(goal)))
        first = recovery.resume_recovery(first.out_dir, options=policy, _single_round=True,
                                         _continue_from=first, client=client)
        assert len(client.requests) == 1
    assert [f.status for f in first.files] == ["UNRESOLVED", "READY"], first.to_text()
    assert first.files[0].reason == reason and first.files[0].attempts == 0
    assert first.attempts[0]["results"][0]["source"] == str(paths[1])
    states = list(recovery.load_recovery(first.out_dir).session_state["groups"].values())
    assert {s["status"] for s in states} == {"unsupported", "complete"}
    assert len({s["context_folder"] for s in states}) == 2
    assert all("Keep this signed investigation" in json.dumps(s["messages"]) for s in states)
    from spec_surgeon.conversation import check_pairing
    for state in states:
        check_pairing(state["messages"])
    ready_bytes = first.files[1].output.read_bytes()
    retry = FakeClient(submit(candidate(goal)))
    final = recovery.resume_recovery(first.out_dir, options=policy, client=retry)
    assert len(retry.requests) == 1 and len(final.ready) == 2, final.to_text()
    assert final.files[1].output.read_bytes() == ready_bytes
    assert [f.attempts for f in final.files] == [1, 1]
    assert [p.read_bytes() for p in paths] == originals


def test_scoped_unsupported_preserves_another_files_pending_question(tmp_path):
    paths = documents(tmp_path, 3)
    goal = spec([margin()])
    result = recovery.recover(paths, goal["request"], tmp_path / "session", spec=goal,
        options=opts(group_call_slice=1), client=FakeClient(
            resp(tool("need_user", {"files": [str(paths[0])], "question": "Which margin for the cover?"})),
            resp(tool("unsupported", {"files": [str(paths[1])], "reason": "Needs a layout capability."})),
            submit(candidate(goal))))
    assert [f.status for f in result.files] == ["NEEDS_INPUT", "UNRESOLVED", "READY"], result.to_text()
    assert result.question_records[0]["files"] == [str(paths[0])]
    assert result.question_records[0]["status"] == "open"
    assert {s["status"] for s in result.session_state["groups"].values()} == {"needs_input", "unsupported"}
    assert [r["source"] for r in result.attempts[0]["results"]] == [str(paths[2])]


@pytest.mark.parametrize("changed", ["source", "template", "input", "output"])
def test_reload_refuses_changed_pinned_inputs_and_verified_outputs(tmp_path, changed):
    source = documents(tmp_path, 1)[0]
    template = tmp_path / "template.docx"
    template.write_bytes(build("99 99 99", "OTHER TEMPLATE"))
    table = tmp_path / "values.csv"
    table.write_text("name,value\nsection,21 13 00\n")
    goal = spec([margin()])
    result = recovery.prepare_recovery([source], goal["request"], tmp_path / "session", spec=goal,
                                      options=opts(), template=template, input_files=[table])
    assert result.ready
    path = {"source": source, "template": template, "input": table, "output": result.ready[0].output}[changed]
    stat = path.stat()
    data = bytearray(path.read_bytes())
    data[-1] ^= 1
    path.write_bytes(data)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(ValueError, match="changed"):
        recovery.load_recovery(result.out_dir)


def test_resume_keeps_full_provider_history_and_failed_call_accounting(tmp_path, monkeypatch):
    source = documents(tmp_path, 1)[0]
    goal = spec([margin()])
    monkeypatch.setattr(recovery.compiler._Batch, "list_styles", lambda self, **kw: {"evidence": "z" * 14000})
    first = recovery.recover([source], goal["request"], tmp_path / "session", spec=goal, options=opts(),
        client=FakeClient(resp(thinking("signed-thought", "reasoning"),
                               tool("list_styles", {"file": source.name})),
                          raise_on_enter(api_error("temporarily unavailable"))))
    assert "connection" in first.paused_reason.lower()
    assert first.budget["failed_calls"] == 1
    loaded = recovery.load_recovery(first.out_dir)
    history = next(iter(loaded.session_state["groups"].values()))["messages"]
    assert history[1]["content"][0]["signature"] == "signed-thought"
    assert "z" * 14000 in history[2]["content"][0]["content"]
    client = FakeClient(submit(candidate(goal)))
    second = recovery.resume_recovery(first.out_dir, options=opts(), client=client)
    assert second.ready and second.resource_history[0]["failed_calls"] == 1
    assert client.requests[0]["messages"][:len(history)] == history


def test_round_robin_gives_every_group_an_inspection_slice(tmp_path):
    paths = documents(tmp_path)
    goal = spec([margin()])
    client = FakeClient(resp(tool("list_styles", {"file": paths[0].name})),
                        resp(tool("list_styles", {"file": paths[1].name})),
                        submit(candidate(goal)), submit(candidate(goal)))
    result = recovery.recover(paths, goal["request"], tmp_path / "session", spec=goal,
                              prior_failures=distinct_failures(paths), options=opts(group_call_slice=1), client=client)
    assert len(result.ready) == 2 and result.budget["calls"] == 4
    contexts = [json.loads(r["messages"][0]["content"])["failures"][0]["source"] for r in client.requests]
    assert contexts == [str(paths[0]), str(paths[1]), str(paths[0]), str(paths[1])]


def test_budget_pause_resumes_in_place_without_repeating_a_failed_candidate(tmp_path):
    source = documents(tmp_path, 1)[0]
    goal = spec([margin(), margin("right", 2880)])
    first = recovery.recover([source], goal["request"], tmp_path / "session", spec=goal, options=opts(max_calls=1),
                             client=FakeClient(submit(candidate(goal))))
    assert not first.ready and len(first.attempts) == 1
    chain = {"kind": "chain", "chain": {"stages": [{"spec": spec([op])} for op in goal["ops"]]}}
    second = recovery.resume_recovery(first.out_dir, options=opts(max_calls=2), client=FakeClient(submit(chain)))
    assert second.ready and len(second.attempts) == 2 and second.files[0].attempts == 2
    assert len(next(iter(second.session_state["groups"].values()))["attempted"]) == 2


def test_reload_never_restores_visual_acknowledgement(tmp_path, monkeypatch):
    source = documents(tmp_path, 1)[0]
    goal = spec([margin()])
    monkeypatch.setattr(recovery, "render_samples", lambda *a, **kw: "word")
    monkeypatch.setattr(vision, "review_samples", lambda *a, **kw: vision.VisualReview(
        findings=[{"severity": "block", "file": source.name, "text": "Check this page"}]))
    result = recovery.recover([source], goal["request"], tmp_path / "session", spec=goal,
                              options=opts(render=True, max_attempts=1), client=FakeClient(submit(candidate(goal))))
    result.review_ack = result.token
    recovery._save(result)
    loaded = recovery.load_recovery(result.out_dir)
    assert loaded.review_ack == "" and loaded.review.blocking()
    with pytest.raises(ValueError, match="acknowledge"):
        recovery.deliver_recovery(loaded)


def test_full_trial_results_are_paginated_without_losing_tail_files(tmp_path):
    rows = [{"source": f"file-{i}", "status": "UNRESOLVED", "reason": "failure " + "x" * 2000}
            for i in range(105)]
    entry = {"number": 1, "results": rows}
    summary = recovery._trial_summary(entry)
    page = recovery._page(rows, {})
    assert summary["counts"] == {"UNRESOLVED": 105} and page["next_offset"] == 20
    final = recovery._page(rows, {"offset": 100})
    assert final["files"][-1]["source"] == "file-104" and final["next_offset"] is None
    assert json.loads(json.dumps(final)) == final


def test_explicit_project_inputs_do_not_discover_nearby_other_project_files(tmp_path):
    source = documents(tmp_path, 1)[0]
    goal = spec([margin()])
    nearby = tmp_path / "other-project-values.csv"           # another job's table beside the document
    nearby.write_text("name,value\nsection,99 99 99\n", encoding="utf-8")
    result = recovery.prepare_recovery([source], goal["request"], tmp_path / "session", spec=goal,
                                      options=opts(), project_context={"job": "A"}, input_files=[])
    assert result.ready and result.key["project_context"] == {"job": "A"}
    assert result.key["recovery_inputs"] == [] and result.budget["calls"] == 0
    assert str(nearby) not in result.input_pins and str(nearby) not in json.dumps(result.key)
    assert recovery.compiler._Batch([source], None).inputs == []   # nothing is discovered beside the documents


def test_session_lock_refuses_overlapping_continuations_and_releases_after_error(tmp_path):
    from spec_surgeon.recovery_state import session_lock
    source = documents(tmp_path, 1)[0]
    goal = spec([margin()])
    result = recovery.prepare_recovery([source], goal["request"], tmp_path / "session", spec=goal, options=opts())
    before = (result.out_dir / "recovery.json").read_bytes()
    with session_lock(result.out_dir):
        with pytest.raises(ValueError, match="another worker"):
            recovery.resume_recovery(result.out_dir, options=opts())
    assert (result.out_dir / "recovery.json").read_bytes() == before
    assert recovery.resume_recovery(result.out_dir, options=opts()).ready


def test_interrupted_first_call_has_a_reloadable_checkpoint_and_releases_the_lock(tmp_path):
    source = documents(tmp_path, 1)[0]
    goal = spec([margin()])
    folder = tmp_path / "session"
    with pytest.raises(KeyboardInterrupt):
        recovery.recover([source], goal["request"], folder, spec=goal, options=opts(),
                         client=FakeClient(raise_on_enter(KeyboardInterrupt())))
    checkpoint = recovery.load_recovery(folder)
    assert not checkpoint.ready and checkpoint.budget["failed_calls"] == 1
    continued = recovery.resume_recovery(folder, options=opts(), client=FakeClient(submit(candidate(goal))))
    assert continued.ready and continued.resource_history[0]["failed_calls"] == 1
    assert "Session total across 2 continuation(s): 2 model calls" in continued.to_text()


def test_five_useful_strategy_revisions_are_not_cut_off_after_three_attempts(tmp_path):
    source = documents(tmp_path, 1)[0]
    goal = spec([margin("left"), margin("right", 2880), margin("top"), margin("bottom"), margin("gutter")])
    responses = []
    for split in range(5):
        stages = [{"spec": spec([op])} for op in goal["ops"][:split]]
        stages.append({"spec": spec(goal["ops"][split:])})
        responses.append(submit({"kind": "chain", "chain": {"stages": stages}}, f"attempt-{split}"))
    result = recovery.recover([source], goal["request"], tmp_path / "session", spec=goal,
                              options=opts(), client=FakeClient(*responses))
    assert result.ready and len(result.attempts) == 5 and result.files[0].attempts == 5
    assert all(check["status"] == "PASS" for check in result.ready[0].outcomes)


def test_repeated_rejected_strategy_pauses_without_spending_the_whole_allowance(tmp_path):
    source = documents(tmp_path, 1)[0]
    goal = spec([margin(), margin("right", 2880)])
    proposed = candidate(goal)
    responses = [submit(proposed)]
    for i in range(3):
        rewritten = copy.deepcopy(proposed)
        rewritten["spec"]["summary"] = f"Same edit with a new label {i}"
        responses.append(submit(rewritten, f"repeat-{i}"))
    result = recovery.recover([source], goal["request"], tmp_path / "session", spec=goal,
                              options=opts(), client=FakeClient(*responses))
    assert not result.ready and len(result.attempts) == 1 and result.budget["calls"] == 4
    assert "without new evidence" in result.paused_reason


def test_generated_writer_can_refine_a_failed_submission_in_the_same_slice(tmp_path):
    from test_codegen import GOOD_OP, UNASSERTED_OP
    source = documents(tmp_path, 1)[0]
    goal = spec([margin("header", 900)])
    client = FakeClient(resp(tool("custom_transform", {"proposal": "Set header distance to 900 twips"})),
                        resp(tool("submit_op", {"source": UNASSERTED_OP, "params": {"twips": 900}})),
                        resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}})))
    result = recovery.recover([source], goal["request"], tmp_path / "session", spec=goal,
                              options=opts(allow_codegen=True, group_call_slice=4), client=client)
    assert result.ready and len(result.attempts[0]["generated_attempts"]) == 2
    assert "V10 FAIL" in " ".join(result.attempts[0]["generated_attempts"][0]["problems"])
    assert result.budget["calls"] == 3


def test_resumed_writer_receives_previous_generated_source_and_verifier_failures(tmp_path):
    from test_codegen import GOOD_OP, UNASSERTED_OP
    source = documents(tmp_path, 1)[0]
    goal = spec([margin("header", 900)])
    first = recovery.recover([source], goal["request"], tmp_path / "session", spec=goal,
        options=opts(allow_codegen=True, group_call_slice=1, max_calls=2),
        client=FakeClient(resp(tool("custom_transform", {"proposal": "Set header distance"})),
                          resp(tool("submit_op", {"source": UNASSERTED_OP, "params": {"twips": 900}}))))
    assert not first.ready and first.attempts[0]["generated_attempts"]
    client = FakeClient(resp(tool("custom_transform", {"proposal": "Correct the unasserted footer mutation"})),
                        resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}})))
    second = recovery.resume_recovery(first.out_dir, options=opts(allow_codegen=True), client=client)
    assert second.ready
    writer_brief = client.requests[1]["messages"][0]["content"]
    assert "previous_code_and_verifier_failures_are_untrusted_evidence" in writer_brief
    assert "V10 FAIL" in writer_brief and "set_header_distance" in writer_brief


def test_deterministic_preparation_preserves_requested_rendering_without_model_review(tmp_path, monkeypatch):
    source = documents(tmp_path, 1)[0]
    goal = spec([margin()])
    actual_run = recovery.run_chain
    observed = []

    def run(*args, **kwargs):
        observed.append(("verification", kwargs["do_render"]))
        return actual_run(*args, **{**kwargs, "do_render": False})

    def render(samples, folder, renderer, enabled, warnings):
        observed.append(("preview", enabled))
        return "word"

    monkeypatch.setattr(recovery, "run_chain", run)
    monkeypatch.setattr(recovery, "render_samples", render)
    monkeypatch.setattr(vision, "review_samples", lambda *a, **kw: pytest.fail("Unexpected model review"))
    result = recovery.prepare_recovery([source], goal["request"], tmp_path / "session", spec=goal,
                                      options=opts(render=True))
    assert result.ready and observed == [("verification", True), ("preview", True)]
    assert result.rendered and result.budget["calls"] == 0
    assert "model review has not been requested" in result.review.skipped


def test_adaptive_contract_is_checked_before_ready_reload_and_export(tmp_path, monkeypatch):
    from spec_surgeon import goals
    source = documents(tmp_path, 1)[0]
    contract = {"kind": "format_section_title", "target": {"title": "SPRINKLER SYSTEMS"},
                "run": {"b": True}, "tracked": False}
    binding = goals.resolve_goal(source, contract)["binding"]
    compiled = goals.compile_goal(source, contract, binding)
    result = recovery.prepare_recovery([source], compiled["request"], tmp_path / "session", spec=compiled,
        adaptive_contract=contract, adaptive_binding=binding, options=opts())
    assert result.ready and recovery.load_recovery(result.out_dir).ready
    assert result.key["adaptive_contract"] == contract and result.key["adaptive_binding"] == binding
    monkeypatch.setattr(goals, "verify_goal", lambda *a: {"passed": False, "checks": [], "errors": ["goal mismatch"]})
    with pytest.raises(ValueError, match="goal check failed"):
        recovery.load_recovery(result.out_dir)
    with pytest.raises(ValueError, match="goal check failed"):
        recovery.deliver_recovery(result)
    assert not (result.out_dir / "outputs").exists()


def test_internal_scheduler_slices_share_allowance_and_do_not_restart_paused_groups(tmp_path):
    source = documents(tmp_path, 1)[0]
    goal = spec([margin(), margin("right", 2880)])
    policy = opts(group_call_slice=1, max_attempts=1, max_calls=3)
    initial = recovery.recover([source], goal["request"], tmp_path / "session", spec=goal, options=policy,
        _single_round=True, client=FakeClient(resp(tool("list_styles", {"file": source.name}))))
    assert initial.budget["calls"] == 1
    assert next(iter(initial.session_state["groups"].values()))["status"] == "active"
    second = recovery.resume_recovery(initial.out_dir, options=policy, _single_round=True, _continue_from=initial,
                                       client=FakeClient(submit(candidate(goal))))
    state = next(iter(second.session_state["groups"].values()))
    assert state["used"] == state["attempt_limit"] == 1 and state["status"] == "paused"
    assert second.budget["calls"] == 2 and not second.resource_history
    assert second._scheduler_context["budget"] is initial._scheduler_context["budget"]
    unused = FakeClient()
    paused = recovery.resume_recovery(second.out_dir, options=policy, _single_round=True, _continue_from=second, client=unused)
    assert not unused.requests and paused.budget["calls"] == 2
    assert not paused.resource_history and not paused.ready
    chain = {"kind": "chain", "chain": {"stages": [{"spec": spec([op])} for op in goal["ops"]]}}
    explicit = recovery.resume_recovery(paused.out_dir, options=policy, _single_round=True, client=FakeClient(submit(chain)))
    assert explicit.ready and explicit.budget["calls"] == 1
    assert explicit.resource_history[0]["calls"] == 2
    assert next(iter(explicit.session_state["groups"].values()))["attempt_limit"] == 2


def test_internal_slices_apply_a_scoped_decision_once_without_fresh_allowance(tmp_path):
    source = documents(tmp_path, 1)[0]
    goal = spec([margin()])
    policy = opts(group_call_slice=1, max_calls=3)
    initial = recovery.recover([source], goal["request"], tmp_path / "session", spec=goal, options=policy,
        _single_round=True, client=FakeClient(resp(tool("need_user", {"question": "Use these margins?"}))))
    second = recovery.resume_recovery(initial.out_dir, options=policy, _single_round=True, _continue_from=initial,
        request={"question_id": "q0001", "answer": "Use the requested margins."},
        client=FakeClient(resp(tool("list_styles", {"file": source.name}))))
    third = recovery.resume_recovery(second.out_dir, options=policy, _single_round=True, _continue_from=second,
                                     client=FakeClient(submit(candidate(goal))))
    assert third.ready and len(third.decisions) == 1 and not third.resource_history
    assert third.budget["calls"] == 3
    assert third.question_records[0]["decision_ids"] == ["d0001"]


def test_internal_slice_cannot_rehydrate_a_budget_from_a_checkpoint(tmp_path):
    source = documents(tmp_path, 1)[0]
    goal = spec([margin()])
    first = recovery.recover([source], goal["request"], tmp_path / "session", spec=goal,
        options=opts(group_call_slice=1), _single_round=True,
        client=FakeClient(resp(tool("list_styles", {"file": source.name}))))
    checkpoint = recovery.load_recovery(first.out_dir)
    with pytest.raises(ValueError, match="in-memory"):
        recovery.resume_recovery(first.out_dir, _continue_from=checkpoint, _single_round=True, client=FakeClient())


def test_scheduler_yield_does_not_end_visual_investigation_early(tmp_path, monkeypatch):
    source = documents(tmp_path, 1)[0]
    goal = spec([margin()])
    monkeypatch.setattr(recovery, "render_samples", lambda *a, **kw: "word")
    monkeypatch.setattr(vision, "review_samples", lambda *a, **kw: vision.VisualReview(
        findings=[{"severity": "block", "file": source.name, "text": "The page still needs review."}]))
    policy = opts(render=True, group_call_slice=1)
    first = recovery.recover([source], goal["request"], tmp_path / "session", spec=goal, options=policy,
                              _single_round=True, client=FakeClient(submit(candidate(goal))))
    assert not first.ready and first.files[0].output
    assert next(iter(first.session_state["groups"].values()))["status"] == "active"
    final = recovery.resume_recovery(first.out_dir, options=policy, _single_round=True, _continue_from=first,
        client=FakeClient(resp(tool("unsupported", {"reason": "The remaining concern needs explicit visual review."}))))
    assert final.ready and final.review.blocking()
    with pytest.raises(ValueError, match="acknowledge"):
        recovery.deliver_recovery(final)


def test_external_decision_replay_after_crash_is_idempotent_and_cannot_change_scope(tmp_path):
    source = documents(tmp_path, 1)[0]
    goal = spec([margin()])
    initial = recovery.recover([source], goal["request"], tmp_path / "session", spec=goal,
        options=opts(), client=FakeClient(resp(tool("need_user", {"question": "Use these margins?"}))))
    answer = {"question_id": "q0001", "answer": "Use these margins.", "external_decision_id": "campaign-decision-1"}
    first = recovery.resume_recovery(initial.out_dir, request=answer, options=opts(max_calls=1),
        client=FakeClient(resp(tool("list_styles", {"file": source.name}))))
    assert len(first.decisions) == 1
    # Simulate recovery having saved the answer before its campaign recorded delivery.
    replayed = recovery.resume_recovery(first.out_dir, request=answer, options=opts(),
                                       client=FakeClient(submit(candidate(goal))))
    assert replayed.ready and len(replayed.decisions) == 1
    assert replayed.question_records[0]["decision_ids"] == ["d0001"]
    changed = {**answer, "answer": "A different request must not reuse this identity."}
    with pytest.raises(ValueError, match="different answer or scope"):
        recovery.resume_recovery(replayed.out_dir, request=changed, options=opts(), client=FakeClient())
