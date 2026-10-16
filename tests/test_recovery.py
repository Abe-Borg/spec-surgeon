"""Exercise real repair trials; only the model is scripted."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from fake_claude import FakeClient, resp, tool
from make_fixtures import build
from spec_surgeon import recovery, vision
from spec_surgeon.recovery import RecoveryOptions, deliver_recovery, recover
from spec_surgeon.verify.outcomes import assert_outcomes


def spec(ops, assertions=None):
    out = {"request": "Set the left margin to one inch and right margin to two inches", "summary": "Margins",
           "mode": "formatting", "ops": ops}
    if assertions is not None:
        out["assertions"] = assertions
    return out


def margin(key="left", value=1440):
    return {"op": "set_page_setup", "params": {"margins": {key: value}}}


def candidate(goal):
    return {"kind": "spec", "spec": copy.deepcopy(goal)}


def submit(value, tid="repair"):
    return resp(tool("submit_repair", {"candidate": value}, tid))


def opts(**kw):
    return RecoveryOptions(**{"render": False, "use_memory": False, **kw})


@pytest.fixture
def source(tmp_path):
    p = tmp_path / "21 13 13.docx"
    p.write_bytes(build("21 13 13", "SPRINKLER SYSTEMS"))
    return p


def test_recovers_conflicting_spec_as_verified_sequence_then_delivers_exact_bytes(source, tmp_path):
    goal = spec([margin(), margin("right", 2880)])
    chain = {"stages": [{"spec": spec([op])} for op in goal["ops"]]}
    original = source.read_bytes()
    client = FakeClient(submit(candidate(goal)), submit({"kind": "chain", "chain": chain}, "second"))
    result = recover([source], goal["request"], tmp_path / "recovery", spec=goal, options=opts(), client=client)
    assert len(result.attempts) == 2
    assert result.ready, result.to_text()
    assert "split into separate specs" in result.attempts[0]["results"][0]["reason"]
    assert len(result.ready[0].outcomes) == 2
    assert all(a["status"] == "PASS" for a in result.ready[0].outcomes)
    assert source.read_bytes() == original
    assert not (result.out_dir / "outputs").exists()
    target = deliver_recovery(result)
    assert (target / source.name).read_bytes() == result.ready[0].output.read_bytes()
    assert source.read_bytes() == original
    assert "not reviewed" in result.review.skipped
    assert json.loads((result.out_dir / "recovery.json").read_text())["budget"]["calls"] == 2


def test_repair_cannot_drop_an_operation_to_get_a_pass(source, tmp_path):
    goal = spec([margin(), margin("right", 2880)])
    result = recover([source], goal["request"], tmp_path / "recovery", spec=goal, options=opts(max_attempts=1),
                     client=FakeClient(submit(candidate(spec([margin()])))))
    assert not result.ready
    assert "effects or selectors" in result.files[0].reason
    assert not list(result.out_dir.glob("attempts/*/outputs/*.docx"))


def test_repair_cannot_narrow_selection(source, tmp_path):
    goal = spec([margin()])
    narrow = copy.deepcopy(goal)
    narrow["ops"][0]["select"] = {"sections": [0]}
    result = recover([source], goal["request"], tmp_path / "recovery", spec=goal, options=opts(max_attempts=1),
                     client=FakeClient(submit(candidate(narrow))))
    assert not result.ready


def test_read_tools_inspect_actual_xml_and_answer_every_call(source, tmp_path):
    goal = spec([margin()])
    client = FakeClient(resp(tool("inspect_xml", {"file": source.name, "part": "word/document.xml"}, "a"),
                             tool("inspect_xml", {"file": "unrelated.docx", "part": "word/document.xml"}, "b")),
                        submit(candidate(goal)))
    result = recover([source], goal["request"], tmp_path / "recovery", spec=goal, options=opts(), client=client)
    assert result.ready
    answers = client.requests[1]["messages"][-1]["content"]
    assert [a["tool_use_id"] for a in answers] == ["a", "b"]
    assert "sectPr" in answers[0]["content"]
    assert answers[1]["is_error"]


@pytest.mark.parametrize("what", ["output", "source", "same_stat_source", "requirements"])
def test_delivery_refuses_changed_inputs_outputs_or_requirements(source, tmp_path, what):
    goal = spec([margin()])
    result = recover([source], goal["request"], tmp_path / "recovery", spec=goal, options=opts(),
                     client=FakeClient(submit(candidate(goal))))
    assert result.ready, result.to_text()
    if what == "requirements":
        result.key["request"] = "Different goal"
    elif what == "same_stat_source":
        stat = source.stat()
        data = bytearray(source.read_bytes())
        data[-1] ^= 1
        source.write_bytes(data)
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    else:
        path = source if what == "source" else result.ready[0].output
        path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="changed"):
        deliver_recovery(result)
    assert not (result.out_dir / "outputs").exists()


def test_genuine_question_stops_and_is_saved_for_remaining_files(source, tmp_path):
    goal = spec([margin()])
    result = recover([source], goal["request"], tmp_path / "recovery", spec=goal, options=opts(), client=FakeClient(
        resp(tool("need_user", {"question": "Does the landscape schedule use the same margins?"}))))
    assert result.files[0].status == "NEEDS_INPUT"
    assert result.questions
    manifest = json.loads((result.out_dir / "set_aside.json").read_text())
    assert manifest["spec"] == goal
    assert manifest["files"][0]["source"] == str(source)


def test_budget_exhaustion_saves_diagnostics_without_unverified_delivery(source, tmp_path):
    goal = spec([margin()])
    client = FakeClient(resp(tool("list_styles", {"file": source.name})))
    result = recover([source], goal["request"], tmp_path / "recovery", spec=goal, options=opts(max_calls=1), client=client)
    assert not result.ready
    assert result.budget["calls"] == 1
    assert result.budget["exhausted_reason"]
    assert any("budget" in note for note in result.notes)
    assert (result.out_dir / "RECOVERY_REPORT.md").exists()


def test_same_pattern_repair_uses_one_model_trial_but_checks_each_file(source, tmp_path):
    second = tmp_path / "21 05 00.docx"
    second.write_bytes(build("21 05 00", "COMMON WORK"))
    goal = spec([margin()])
    client = FakeClient(submit(candidate(goal)))
    result = recover([source, second], goal["request"], tmp_path / "recovery", spec=goal, options=opts(), client=client)
    assert len(client.requests) == 1
    assert len(result.ready) == 2
    for f in result.ready:
        assert all(c["status"] == "PASS" for c in assert_outcomes(f.output.read_bytes(), result.key["assertions"]))


def test_visual_problem_returns_to_investigator_and_stale_findings_are_replaced(source, tmp_path, monkeypatch):
    goal = spec([margin()])
    revised = copy.deepcopy(goal)
    revised["ops"][0]["expect"] = {"text_diffs": 0}
    reviews = [vision.VisualReview(findings=[{"severity": "block", "file": source.name, "page": 1, "text": "Layout needs inspection"}]),
               vision.VisualReview(summary="No issue on the sampled pages")]
    monkeypatch.setattr(recovery, "render_samples", lambda *a, **kw: "word")
    monkeypatch.setattr(vision, "review_samples", lambda *a, **kw: reviews.pop(0))
    client = FakeClient(submit(candidate(goal)), submit(candidate(revised), "second"))
    result = recover([source], goal["request"], tmp_path / "recovery", spec=goal,
                     options=opts(render=True), client=client)
    assert len(result.attempts) == 2
    assert result.ready, result.to_text()
    assert not result.review.blocking()
    assert "blocking concern" in client.requests[1]["messages"][-1]["content"][0]["content"]


def test_last_visual_block_requires_separate_acknowledgement(source, tmp_path, monkeypatch):
    goal = spec([margin()])
    monkeypatch.setattr(recovery, "render_samples", lambda *a, **kw: "word")
    monkeypatch.setattr(vision, "review_samples", lambda *a, **kw: vision.VisualReview(findings=[
        {"severity": "block", "file": source.name, "page": 1, "text": "Review this"}]))
    result = recover([source], goal["request"], tmp_path / "recovery", spec=goal,
                     options=opts(render=True, max_attempts=1), client=FakeClient(submit(candidate(goal))))
    assert result.ready
    with pytest.raises(ValueError, match="acknowledge"):
        deliver_recovery(result)
    assert deliver_recovery(result, acknowledge_review=True).is_dir()


@pytest.mark.parametrize("field,value", [("max_attempts", True), ("max_calls", 0), ("max_cost_usd", float('nan')),
                                        ("max_cost_usd", float('inf')), ("max_output_tokens", -1)])
def test_invalid_budget_refused_before_reading_documents(tmp_path, field, value):
    with pytest.raises(ValueError):
        recover([], "fix margins", tmp_path / "out", options=opts(**{field: value}))
    assert not (tmp_path / "out").exists()
