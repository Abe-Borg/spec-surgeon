"""Exercise real repair trials; only the model is scripted."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from fake_claude import FakeClient, resp, tool
from make_fixtures import build, build_batch1, build_template
from spec_surgeon import recovery, vision
from spec_surgeon.recovery import RecoveryOptions, RecoveryResult, deliver_recovery, recover, recover_from_run
from spec_surgeon.verify.outcomes import assert_outcomes
from test_naming import CONVENTION


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


# ---- inspection stays within the request's inputs --------------------------------------------------------------------

def test_loaded_spec_templates_are_inspectable_without_an_attachment(tmp_path):
    source = tmp_path / "source.docx"
    source.write_bytes(build("21 13 13", "SPRINKLER SYSTEMS"))
    template = tmp_path / "architect.docx"
    template.write_bytes(build_template())
    goal = json.loads((Path(__file__).resolve().parents[1] / "examples/specs/apply-header-footer-template.json").read_text())
    for op in goal["ops"]:
        if "template" in op.get("params", {}):
            op["params"]["template"] = str(template)
    client = FakeClient(resp(
        tool("read_template", {}, "profile"),
        tool("read_template", {"file": str(template)}, "named"),
        tool("inspect_xml", {"file": "template", "part": "word/document.xml"}, "primary"),
        tool("inspect_xml", {"file": str(template), "part": "word/document.xml"}, "xml"),
        tool("inspect_xml", {"file": str(tmp_path / "not-authorized.docx"), "part": "word/document.xml"}, "outside"),
        tool("view_pages", {"file": "template"}, "disabled")),
        resp(tool("submit_repair", {"candidate": {"kind": "spec", "spec": goal}})))
    result = recover([source], goal["request"], tmp_path / "recovery", spec=goal,
                     options=RecoveryOptions(render=False, use_memory=False), client=client)
    assert result.ready, result.to_text()
    answers = {a["tool_use_id"]: a for a in client.requests[1]["messages"][-1]["content"]}
    assert json.loads(answers["profile"]["content"])["path"] == str(template)
    assert json.loads(answers["named"]["content"])["path"] == str(template)
    assert "sectPr" in answers["primary"]["content"] and "sectPr" in answers["xml"]["content"]
    assert answers["outside"]["is_error"]
    assert answers["disabled"]["is_error"] and "disabled" in answers["disabled"]["content"]


def test_report_includes_reserved_cost_and_does_not_label_unknown_spending_free(tmp_path):
    result = RecoveryResult(tmp_path, "Set margins", budget={
        "calls": 1, "unreported_calls": 1, "usage": {"cost_usd": 0}, "estimated_cost_usd": 0.45})
    assert "$0.45" in result.to_text() and "reserved spending" in result.to_text()
    result.budget["estimated_cost_usd"] = None
    assert "unknown" in result.to_text() and "$0.00" not in result.to_text()


def test_answer_preserves_original_request_when_compile_has_no_spec(tmp_path):
    source = tmp_path / "source.docx"
    source.write_bytes(build("21 13 13", "SPRINKLER SYSTEMS"))
    options = RecoveryOptions(render=False, use_memory=False)
    first = recover([source], "Change the font to Verdana", tmp_path / "first", options=options,
                    client=FakeClient(resp(tool("need_user", {"question": "Which sections should change?"}))))
    client = FakeClient(resp(tool("unsupported", {"reason": "No supported proposal yet"})))
    second = recover_from_run(first.out_dir, tmp_path / "second", request="All sections", options=options, client=client)
    assert second.request == "Change the font to Verdana\n\nUser clarification: All sections"
    context = json.loads(client.requests[0]["messages"][0]["content"])
    assert context["request"] == "Change the font to Verdana", "Saved model history remains append-only"
    continuation = json.loads(client.requests[0]["messages"][-1]["content"])
    assert continuation["user_decisions"][0]["answer"] == "All sections"
    assert second.out_dir == first.out_dir
    assert second.question_records[0]["question"] == "Which sections should change?"
    assert second.question_records[0]["status"] == "answered"


# ---- verified names beside final requirements ------------------------------------------------------------------------

def goal():
    return {"stages": [
        {"spec": {"request": "Name files from their section number and title", "summary": "Name outputs",
                  "mode": "formatting", "ops": [{"op": "name_output", "params": copy.deepcopy(CONVENTION)}]}},
        {"spec": {"request": "Set the left margin to one inch", "summary": "Margins", "mode": "formatting",
                  "ops": [{"op": "set_page_setup", "params": {"margins": {"left": 1440}}}]}},
    ]}


def submit_chain(chain):
    return resp(tool("submit_repair", {"candidate": {"kind": "chain", "chain": chain}}))


def test_recovery_exports_the_verified_name_and_checks_final_requirements(tmp_path):
    source = tmp_path / "old-name.docx"
    source.write_bytes(build_batch1())
    original = source.read_bytes()
    chain = goal()
    result = recover([source], "Name the outputs and set the left margin to one inch", tmp_path / "recovery",
                     chain=chain, options=RecoveryOptions(render=False, use_memory=False),
                     client=FakeClient(submit_chain(chain)))
    assert len(result.ready) == 1, result.to_text()
    assert all(x["status"] == "PASS" for x in result.ready[0].outcomes)
    assert result.ready[0].out_name == "211319 - Preaction Sprinkler Systems.docx"
    dest = deliver_recovery(result)
    assert [p.name for p in dest.iterdir()] == [result.ready[0].out_name]
    assert source.read_bytes() == original
    result.ready[0].out_name = "altered-name.docx"
    with pytest.raises(ValueError, match="changed"):
        deliver_recovery(result, tmp_path / "altered-export")


def test_recovery_refuses_same_explicit_name_across_separate_problem_groups(tmp_path):
    sources = [tmp_path / "first.docx", tmp_path / "second.docx"]
    for source in sources:
        source.write_bytes(build_batch1())
    chain = goal()
    result = recover(sources, "Name the outputs and set the left margin to one inch", tmp_path / "recovery",
                     chain=chain, prior_failures=[{"source": str(p), "code": str(i)} for i, p in enumerate(sources)],
                     options=RecoveryOptions(render=False, use_memory=False),
                     client=FakeClient(submit_chain(chain), submit_chain(chain)))
    assert len(result.attempts) == 2
    assert not result.ready
    assert all("naming collision" in f.reason for f in result.files)
    with pytest.raises(ValueError, match="No verified repair"):
        deliver_recovery(result)
    assert not (result.out_dir / "outputs").exists()
