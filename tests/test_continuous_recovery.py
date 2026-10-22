"""Ordinary chat reaches outcome recovery without a separate project workflow."""
import pytest

from fake_claude import FakeClient, resp, tool
from spec_surgeon.continuous_recovery import approve, execute
from spec_surgeon.conversation import Conversation
from spec_surgeon.recovery import deliver_recovery
from test_goals import write_document
from test_recovery import opts
from test_recovery_adaptive import contract


def refusal():
    return resp(tool("submit_editspec", {"spec": {"request": "model summary", "summary": "Title",
        "unsupported": "The old operation cannot express this layout.", "proposal": "Investigate"},
        "custom_transform": "outside_limits", "custom_transform_reason": "Use broader outcome checks."}))


def proposal(goal=None):
    return resp(tool("propose_plan", {"contract": goal or contract(),
        "rationale": "Make the exact title bold and preserve the rest."}))


def conversation(tmp_path, *responses):
    source = write_document(tmp_path / "source.docx")
    c = Conversation("Make the title bold", [source], work_dir=tmp_path / "chat",
        client=FakeClient(refusal(), *responses), continuous_recovery=True)
    return c, source


def test_ordinary_refusal_becomes_reviewed_outcome_and_verified_copies(tmp_path):
    c, source = conversation(tmp_path, proposal())
    before = source.read_bytes()
    turn = c.start()
    assert turn.status == "questions" and turn.choices[0].kind == "recovery_plan"
    assert '"exact_text": "FIRE SUPPRESSION"' in turn.message
    assert '"b": true' in turn.message
    snapshot = approve(c, turn.choices[0].id)
    assert snapshot["contract"]["request"] == c.request
    # The deterministic route handles this case; recovery need not make a model call.
    result = execute(c, snapshot, tmp_path / "recovery", options=opts(critic_review=False))
    assert result.ready and result.budget["calls"] == 0
    destination = deliver_recovery(result)
    assert (destination / source.name).is_file() and source.read_bytes() == before
    assert c.recovery_planning["calls"] == 1
    assert c.calls == 2 and c.usage.calls == 2
    with pytest.raises(ValueError, match="not offered"):
        approve(c, turn.choices[0].id)


def test_followup_question_stays_in_same_conversation_and_authority(tmp_path):
    c, _ = conversation(tmp_path, resp(tool("need_user", {"question": "Which title?"})), proposal())
    assert c.start().questions == ["Which title?"]
    turn = c.reply("Use FIRE SUPPRESSION")
    assert turn.choices[0].kind == "recovery_plan"
    assert "Use FIRE SUPPRESSION" in c.sources
    assert "Use FIRE SUPPRESSION" in str(c._client.requests[-1]["messages"])


@pytest.mark.parametrize("change", ["source", "proposal", "wording", "execution", "scope"])
def test_changed_source_or_outcome_never_reuses_approval(tmp_path, change):
    c, source = conversation(tmp_path, proposal())
    choice = c.start().choices[0]
    if change == "source":
        source.write_bytes(source.read_bytes() + b"drift")
    elif change == "proposal":
        choice.snapshot["contract"]["effects"][0]["run"]["i"] = True
    elif change == "wording":
        choice.words.append("unshown words")
    else:
        snapshot = approve(c, choice.id)
        if change == "scope":
            c.files = [write_document(tmp_path / "unapproved.docx")]
        else:
            snapshot["contract"]["effects"][0]["run"]["i"] = True
        with pytest.raises(ValueError, match="exact outcomes|source scope"):
            execute(c, snapshot, tmp_path / "recovery", options=opts())
        return
    with pytest.raises(ValueError, match="changed"):
        approve(c, choice.id)
    assert not getattr(c, "approved_recovery_hash", None)


def test_gui_keeps_conversation_and_exports_the_recovered_preview(tmp_path, monkeypatch):
    from spec_surgeon.gui import Session
    from spec_surgeon import continuous_recovery
    source = write_document(tmp_path / "source.docx")
    s = Session(documents=[source], out_root=tmp_path / "out", client=FakeClient(refusal(), proposal()))
    monkeypatch.setattr(continuous_recovery, "options_for", lambda c, **kw: opts(critic_review=False))
    s.compile("Make the title bold", lambda _: None)
    c = s.conversation
    s.reply(choice_id=s.turn.choices[0].id)
    assert s.recovery.ready and s.conversation is c and s.turn is None
    assert s.spec is None and s.generated is None
    s.run(False, False, lambda _: None)
    assert (s.last_run_dir / "outputs" / source.name).is_file()


def test_failed_preview_automatically_investigates_without_switching_modes(tmp_path):
    from docx import Document
    from docx.enum.style import WD_STYLE_TYPE
    from spec_surgeon.gui import Session
    source = write_document(tmp_path / "source.docx")
    easy = Document(source)
    easy.styles.add_style("MissingStyle", WD_STYLE_TYPE.PARAGRAPH)
    easy.paragraphs[0].style = "MissingStyle"
    easy.save(tmp_path / "easy.docx")
    broken = {"request": "Make the title bold", "summary": "Use a style", "mode": "formatting",
        "ops": [{"op": "apply_style", "params": {"style": "MissingStyle"}}]}
    client = FakeClient(resp(tool("submit_editspec", {"spec": broken})), proposal())
    s = Session(documents=sorted(tmp_path.glob("*.docx")), out_root=tmp_path / "out", client=client)
    assert s.compile("Make the title bold", lambda _: None) == "Compiled."
    s.make_preview(False, lambda _: None)
    assert s.spec is None and s.preview is None
    assert s.turn.choices[0].kind == "recovery_plan"
    assert "MissingStyle" in str(client.requests[-1]["messages"])
    assert source.is_file()


def test_invisible_proposed_words_are_not_offered_as_an_approval(tmp_path):
    goal = {"kind": "document_effects", "effects": [{"id": "wording", "type": "replace_text",
        "target": {"all": True}, "find": "FIRE", "replace": "NEW\u200bPROJECT",
        "expected_matches": 1, "tracked": False}]}
    c, _ = conversation(tmp_path, proposal(goal))
    turn = c.start()
    assert turn.status == "questions" and not turn.choices
    assert "invisible" in turn.message and "NEW\u200bPROJECT" not in c.sources


def test_ambiguous_targets_are_picked_in_the_same_chat_and_checked_on_resume(tmp_path):
    from spec_surgeon.continuous_recovery import result_turn, resume_choice
    from spec_surgeon.goals import choose_goal, compile_goal_candidate
    from test_recovery import submit
    from test_recovery_adaptive import critic
    source = write_document(tmp_path / "source.docx", duplicate=True)
    c = Conversation("Make the intended title bold", [source], client=FakeClient(refusal(), proposal()),
        work_dir=tmp_path / "chat", continuous_recovery=True)
    snapshot = approve(c, c.start().choices[0].id)
    result = execute(c, snapshot, tmp_path / "recovery", options=opts())
    turn = result_turn(c, result)
    assert len(turn.choices) == 2 and all(ch.kind == "recovery_target" for ch in turn.choices)
    choice = turn.choices[1]
    answer = choice.snapshot["answer"]
    bound = choose_goal(source, snapshot["contract"], answer["candidate_ids"], effect_id="titles")
    c._client = FakeClient(submit(compile_goal_candidate(source, snapshot["contract"], bound)), critic())
    resumed = resume_choice(c, choice.id, options=opts())
    assert resumed.ready and resumed.out_dir == result.out_dir
    assert resumed.decisions[-1]["candidate_ids"] == answer["candidate_ids"]
    assert result_turn(c, resumed) is None


def test_completed_recovery_can_be_revised_in_chat_into_a_new_review(tmp_path, monkeypatch):
    from spec_surgeon.gui import Session
    from spec_surgeon import continuous_recovery
    source = write_document(tmp_path / "source.docx")
    changed = contract()
    changed["effects"][0]["run"] = {"i": False}
    s = Session(documents=[source], out_root=tmp_path / "out",
        client=FakeClient(refusal(), proposal(), proposal(changed)))
    monkeypatch.setattr(continuous_recovery, "options_for", lambda c, **kw: opts(critic_review=False))
    s.compile("Make the title bold", lambda _: None)
    s.reply(choice_id=s.turn.choices[0].id)
    previous = s.recovery.out_dir
    s.reply("Actually remove italics from that title instead.")
    assert s.recovery is None and s.turn.choices[0].kind == "recovery_plan"
    s.reply(choice_id=s.turn.choices[0].id)
    assert s.recovery.ready and s.recovery.out_dir != previous


def test_the_window_runs_an_approved_outcome_plan_at_its_own_attempt_limit_and_high_effort(tmp_path, monkeypatch):
    """The 1.8.0 live run's recovery after a reviewed outcome plan ran with the conversation's allowances (50
    attempts per problem group, effort max) rather than the window's (docs/RECOVERY.md: eight, high)."""
    from dataclasses import replace
    from spec_surgeon import continuous_recovery
    from spec_surgeon.document_task import RECOVERY_ATTEMPTS, DocumentTask
    from spec_surgeon.gui import Session
    source = write_document(tmp_path / "source.docx")
    s = Session(documents=[source], out_root=tmp_path / "out", client=FakeClient(refusal(), proposal()))
    DocumentTask(s, render=False)
    seen = []
    real = continuous_recovery.execute

    def recorded(c, snapshot, out_dir, *, render=False, options=None):
        seen.append(options)
        return real(c, snapshot, out_dir, render=render, options=replace(options, critic_review=False))

    monkeypatch.setattr(continuous_recovery, "execute", recorded)
    s.compile("Make the title bold", lambda _: None)
    s.reply(choice_id=s.turn.choices[0].id)
    assert s.recovery.ready
    assert (seen[0].max_attempts, seen[0].effort) == (RECOVERY_ATTEMPTS, "high") and RECOVERY_ATTEMPTS == 8


def test_a_recovery_saved_at_max_effort_reopens_at_high():
    from spec_surgeon.recovery import RecoveryOptions
    options = RecoveryOptions.from_snapshot({"effort": "max", "max_attempts": 8})
    options.validate()
    assert (options.effort, options.max_attempts) == ("high", 8)
    assert RecoveryOptions.from_snapshot({"effort": "medium"}).effort == "medium"
