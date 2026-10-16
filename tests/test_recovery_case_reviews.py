"""Repair memory keeps visual evidence tied to the candidate that produced it.

The editing engine and independent checks run normally; only model replies and rendering are scripted.
"""
from __future__ import annotations

import copy
import json

import pytest

from fake_claude import FakeClient, resp, tool
from make_fixtures import build
from spec_surgeon import recovery, vision
from spec_surgeon.repair_memory import CaseStore
from test_codegen import GOOD_OP


def _goal():
    return {"request": "Set the header distance to 900 twips", "summary": "Header distance",
            "mode": "formatting", "ops": [
                {"op": "set_page_setup", "params": {"margins": {"header": 900}}}]}


def _submit(goal, tid="repair"):
    return resp(tool("submit_repair", {"candidate": {"kind": "spec", "spec": goal}}, tid))


def _source(tmp_path, name="spec.docx", **kwargs):
    source = tmp_path / name
    source.write_bytes(build("21 13 19", "SPRINKLERS", **kwargs))
    return source


def _reviews(monkeypatch, source):
    blocked = vision.VisualReview(summary="First candidate has a layout concern", findings=[
        {"severity": "block", "file": source.name, "page": 1, "text": "Header overlaps body text"}])
    clear = vision.VisualReview(summary="The current sampled pages have no visible issues")
    pending = [blocked, clear]
    monkeypatch.setattr(recovery, "render_samples", lambda *args, **kwargs: "word")
    monkeypatch.setattr(vision, "review_samples", lambda *args, **kwargs: pending.pop(0))
    return blocked, clear, pending


def _case(result, index):
    return CaseStore().get(result.attempts[index]["case_id"])


@pytest.mark.parametrize("second_kind", ["spec", "generated"])
def test_later_group_records_only_its_own_review_while_ui_retains_both_groups(tmp_path, monkeypatch, second_kind):
    first = _source(tmp_path, "first.docx")
    second = _source(tmp_path, "second.docx", single_section=True)
    goal = _goal()
    blocked, clear, pending = _reviews(monkeypatch, first)
    replies = [_submit(goal)]
    if second_kind == "generated":
        replies += [resp(tool("custom_transform", {"proposal": "Set the header distance"})),
                    resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}}))]
    else:
        replies.append(_submit(goal, "second"))

    result = recovery.recover([first, second], goal["request"], tmp_path / "recovery", spec=goal,
                              options=recovery.RecoveryOptions(render=True, max_attempts=1,
                                                               allow_codegen=True, max_cost_usd=20),
                              client=FakeClient(*replies))

    assert len(result.ready) == 2, result.to_text()
    assert result.files[0].pattern_id != result.files[1].pattern_id
    assert not pending
    assert _case(result, 0)["visual_review"] == blocked.to_dict()
    assert _case(result, 1)["visual_review"] == clear.to_dict()
    assert result.attempts[1]["visual_review"] == clear.to_dict()
    assert result.review.blocking() == blocked.findings
    assert blocked.summary in result.review.summary and clear.summary in result.review.summary


def test_cleared_retry_keeps_each_case_review_as_it_was_for_that_candidate(tmp_path, monkeypatch):
    source = _source(tmp_path)
    goal = _goal()
    revised = copy.deepcopy(goal)
    revised["ops"][0]["expect"] = {"text_diffs": 0}
    blocked, clear, pending = _reviews(monkeypatch, source)

    result = recovery.recover([source], goal["request"], tmp_path / "recovery", spec=goal,
                              options=recovery.RecoveryOptions(render=True, max_attempts=2, max_cost_usd=20),
                              client=FakeClient(_submit(goal), _submit(revised, "second")))

    assert result.ready and not pending, result.to_text()
    assert _case(result, 0)["visual_review"] == blocked.to_dict()
    assert _case(result, 1)["visual_review"] == clear.to_dict()
    assert result.attempts[0]["results"][0]["status"] == "UNRESOLVED"
    assert not result.review.blocking()
    assert result.review.summary == clear.summary


@pytest.mark.parametrize("second_kind", ["spec", "generated"])
def test_failed_retry_has_no_visual_evidence_from_the_earlier_verified_candidate(tmp_path, monkeypatch, second_kind):
    source = _source(tmp_path)
    goal = _goal()
    rejected = copy.deepcopy(goal)
    # The operation changes no text. This fails the real planning check before there is a new verified output.
    rejected["ops"][0]["expect"] = {"text_diffs": 1}
    blocked, clear, pending = _reviews(monkeypatch, source)
    replies = [_submit(goal)]
    if second_kind == "generated":
        # Its own plan passes, but the original approved distance remains 900; no output is ready for review.
        replies += [resp(tool("custom_transform", {"proposal": "Set the header distance"})),
                    resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 1000}}))]
    else:
        replies.append(_submit(rejected, "second"))
    replies.append(resp(tool("unsupported", {"reason": "Cannot resolve this layout within the current limits"})))
    client = FakeClient(*replies)

    result = recovery.recover([source], goal["request"], tmp_path / "recovery", spec=goal,
                              options=recovery.RecoveryOptions(render=True, max_attempts=3,
                                                               allow_codegen=True, max_cost_usd=20),
                              client=client)

    assert not result.ready
    assert pending == [clear], "The failed trial should have no verified output to review"
    assert _case(result, 0)["visual_review"] == blocked.to_dict()
    failed = _case(result, 1)
    assert failed["status"] == "failed" and not failed["structural_verified"]
    assert failed["visual_review"]["findings"] == []
    assert "No independently verified output" in failed["visual_review"]["skipped"]
    returned = json.loads(client.requests[-1]["messages"][-1]["content"][0]["content"])
    assert returned["visual_review"] == failed["visual_review"]
    assert result.attempts[1]["visual_review"] == failed["visual_review"]
