"""Verified candidates survive a bounded investigation stop without hiding unresolved visual concerns."""
from __future__ import annotations

import copy
import hashlib

import pytest

from fake_claude import FakeClient, resp, tool
from make_fixtures import build
from spec_surgeon import llm, recovery, vision


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    source = tmp_path / "spec.docx"
    source.write_bytes(build("21 13 13", "SPRINKLER SYSTEMS"))
    goal = {"request": "Set the left margin to one inch", "summary": "Set the left margin", "mode": "formatting",
            "ops": [{"op": "set_page_setup", "params": {"margins": {"left": 1440}}}]}
    real_run = recovery.run_chain
    monkeypatch.setattr(recovery, "run_chain", lambda *a, **kw: real_run(*a, **{**kw, "do_render": False}))
    monkeypatch.setattr(recovery, "render_samples", lambda *a, **kw: "word")
    monkeypatch.setattr(vision, "review_samples", lambda *a, **kw: blocking(source.name))
    return source, goal


def blocking(name):
    return vision.VisualReview(findings=[{"severity": "block", "file": name, "page": 1,
                                         "text": "The footer overlaps the body text"}])


def submit(goal, tid="repair"):
    return resp(tool("submit_repair", {"candidate": {"kind": "spec", "spec": copy.deepcopy(goal)}}, tid))


def run(prepared, tmp_path, fake, **options):
    source, goal = prepared
    return recovery.recover([source], goal["request"], tmp_path / "recovery", spec=goal,
                            options=recovery.RecoveryOptions(render=True, use_memory=False, **options), client=fake)


def test_budget_stop_keeps_flagged_verified_copy_available_only_with_acknowledgement(prepared, tmp_path, monkeypatch):
    source, goal = prepared
    fake = FakeClient(submit(goal), resp())

    def charged_review(*args, **kwargs):
        llm.call(fake, model=llm.DEFAULT_MODEL, system="Review", tools=[], messages=[{"role": "user", "content": "Pages"}])
        return blocking(source.name)

    monkeypatch.setattr(vision, "review_samples", charged_review)
    result = run(prepared, tmp_path, fake, max_calls=2)
    assert result.budget["calls"] == 2 and result.budget["exhausted_reason"]
    assert len(result.ready) == 1 and result.review.blocking()
    assert "unresolved" in result.ready[0].reason
    with pytest.raises(ValueError, match="acknowledge"):
        recovery.deliver_recovery(result)
    dest = recovery.deliver_recovery(result, acknowledge_review=True)
    assert (dest / source.name).read_bytes() == result.ready[0].output.read_bytes()


def test_unsupported_stop_retains_candidate_and_explains_remaining_concern(prepared, tmp_path):
    _, goal = prepared
    result = run(prepared, tmp_path, FakeClient(submit(goal), resp(tool("unsupported", {
        "reason": "This requires a footer capability the engine does not support"}))))
    assert result.ready and result.review.blocking()
    assert "footer capability" in result.ready[0].reason
    with pytest.raises(ValueError, match="acknowledge"):
        recovery.deliver_recovery(result)


def test_a_missing_user_decision_does_not_restore_a_candidate(prepared, tmp_path):
    _, goal = prepared
    result = run(prepared, tmp_path, FakeClient(submit(goal), resp(tool("need_user", {
        "question": "Should this landscape page have the same margins?"}))))
    assert not result.ready and result.files[0].status == "NEEDS_INPUT"


def test_later_failed_trial_does_not_resurrect_previous_candidate(prepared, tmp_path):
    _, goal = prepared
    wrong = copy.deepcopy(goal)
    wrong["ops"][0]["params"]["margins"]["left"] = 2880
    result = run(prepared, tmp_path, FakeClient(submit(goal), submit(wrong, "second")), max_attempts=2)
    assert not result.ready and result.files[0].attempts == 2
    assert result.files[0].output is None


def test_changed_parked_output_is_not_restored(tmp_path):
    output = tmp_path / "output.docx"
    output.write_bytes(b"verified")
    original_md5 = hashlib.md5(output.read_bytes()).hexdigest()
    f = recovery.RecoveryFile(tmp_path / "source.docx", output=output, output_md5=original_md5, attempts=1)
    result = recovery.RecoveryResult(tmp_path, "Margin change", files=[f])
    result._reviews[str(f.source)] = blocking(f.source.name)
    ctx = {"result": result, "visually_blocked_candidates": {str(f.source): (output, original_md5, 1)}}
    output.write_bytes(b"changed")
    recovery._restore_reviewable_candidates(ctx)
    assert not result.ready


def test_one_skipped_group_does_not_hide_another_groups_blocking_findings(tmp_path, monkeypatch):
    first = recovery.RecoveryFile(tmp_path / "a.docx", status="READY", output=tmp_path / "a-out.docx", attempts=1)
    second = recovery.RecoveryFile(tmp_path / "b.docx", status="READY", output=tmp_path / "b-out.docx", attempts=1)
    result = recovery.RecoveryResult(tmp_path, "Margin change", files=[first, second])
    ctx = {"result": result, "options": recovery.RecoveryOptions(max_attempts=1), "client": object(), "log": lambda _: None}
    reviews = [blocking(first.source.name), vision.VisualReview(skipped="Model budget exhausted")]
    monkeypatch.setattr(recovery, "render_samples", lambda *a, **kw: "word")
    monkeypatch.setattr(vision, "review_samples", lambda *a, **kw: reviews.pop(0))
    recovery._review(ctx, [first], tmp_path / "first")
    recovery._review(ctx, [second], tmp_path / "second")
    assert not result.review.skipped and result.review.blocking()
    text = result.review.to_text()
    assert "[block]" in text and "footer overlaps" in text
    assert "Model budget exhausted" in text
