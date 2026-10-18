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
    s = Session(folder=tmp_path, out_root=tmp_path / "out", client=FakeClient(refusal(), proposal()))
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
    s = Session(folder=tmp_path, out_root=tmp_path / "out", client=client)
    assert s.compile("Make the title bold", lambda _: None) == "Compiled."
    s.make_preview(False, lambda _: None)
    assert s.spec is None and s.preview is None
    assert s.turn.choices[0].kind == "recovery_plan"
    assert "MissingStyle" in str(client.requests[-1]["messages"])
    assert source.is_file()


def test_noninteractive_cli_requires_exact_new_wording_even_with_yes(tmp_path, monkeypatch, capsys):
    from spec_surgeon import cli, compiler
    source = write_document(tmp_path / "source.docx")
    goal = {"kind": "document_effects", "effects": [{"id": "wording", "type": "replace_text",
        "target": {"all": True}, "find": "FIRE", "replace": "INVENTED PROJECT",
        "expected_matches": 1, "tracked": False}]}
    client = FakeClient(refusal(), proposal(goal))
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    monkeypatch.setattr(compiler, "_client", lambda: client)
    monkeypatch.setattr("spec_surgeon.llm.client", lambda: client)
    code = cli.main(["do", "Change the project name", str(source), "--yes", "--no-render", "--out", str(tmp_path / "out")])
    assert code == 2
    assert "exact wording" in capsys.readouterr().out
    assert not (tmp_path / "out" / "recovery").exists()


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
    write_document(tmp_path / "source.docx")
    changed = contract()
    changed["effects"][0]["run"] = {"i": False}
    s = Session(folder=tmp_path, out_root=tmp_path / "out",
        client=FakeClient(refusal(), proposal(), proposal(changed)))
    monkeypatch.setattr(continuous_recovery, "options_for", lambda c, **kw: opts(critic_review=False))
    s.compile("Make the title bold", lambda _: None)
    s.reply(choice_id=s.turn.choices[0].id)
    previous = s.recovery.out_dir
    s.reply("Actually remove italics from that title instead.")
    assert s.recovery is None and s.turn.choices[0].kind == "recovery_plan"
    s.reply(choice_id=s.turn.choices[0].id)
    assert s.recovery.ready and s.recovery.out_dir != previous


def test_noninteractive_cli_continues_with_yes_and_preserves_model_settings(tmp_path, monkeypatch):
    from spec_surgeon import cli, compiler, continuous_recovery
    source = write_document(tmp_path / "source.docx")
    client = FakeClient(refusal(), proposal())
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    monkeypatch.setattr(compiler, "_client", lambda: client)
    monkeypatch.setattr("spec_surgeon.llm.client", lambda: client)
    out = tmp_path / "out"
    assert cli.main(["do", "Make the title bold", str(source), "--yes", "--no-render",
        "--max-context-tokens", "800000", "--out", str(out)]) == 0
    assert (out / "recovery" / "outputs" / source.name).is_file()
    import json
    journal = json.loads((out / "conversation.json").read_text())
    assert journal["calls"] == 2 and journal["resource_policy"]["max_context_tokens"] == 800000


@pytest.mark.parametrize("question_kind", ["investigation", "critic"])
def test_cli_free_text_resumes_current_recovery_without_replanning(tmp_path, monkeypatch, question_kind):
    from types import SimpleNamespace
    from spec_surgeon import cli, continuous_recovery, recovery
    from spec_surgeon.goals import compile_goal_candidate
    from test_recovery import submit
    from test_recovery_adaptive import critic

    c, source = conversation(tmp_path, proposal())
    original = source.read_bytes()
    snapshot = approve(c, c.start().choices[0].id)
    goal = snapshot["contract"]
    candidate = compile_goal_candidate(source, goal)
    question = "Should the selected title stay bold?"
    pause = ([submit(candidate), critic("need_user", question=question)] if question_kind == "critic"
             else [resp(tool("need_user", {"question": question}))])
    out = tmp_path / "out"
    initial = recovery.recover([source], c.request, out / "recovery", adaptive_contract=goal, options=opts(),
        client=FakeClient(resp(tool("list_styles", {"file": str(source)})), *pause))
    assert initial.questions == [question] and not initial.ready
    retained_output = initial.files[0].output
    retained_bytes = retained_output.read_bytes() if retained_output else None
    c._client = FakeClient(resp(tool("review_candidate", {})) if question_kind == "critic" else submit(candidate),
                           critic())
    monkeypatch.setattr(continuous_recovery, "execute", lambda *a, **kw: initial)
    monkeypatch.setattr(continuous_recovery, "options_for", lambda *a, **kw: opts())
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    answer = "Yes, keep the selected title bold."
    replies = iter([answer])
    monkeypatch.setattr(cli, "_ask", lambda _: next(replies))
    args = SimpleNamespace(no_render=True, renderer="none", no_review=True, ack_review=False, yes=True)

    assert cli._do_continuous_recovery(args, c, snapshot, out) == 0
    resumed = recovery.load_recovery(initial.out_dir)
    assert resumed.ready and len(resumed.attempts) == 1
    assert resumed.question_records[0]["status"] == "answered"
    assert resumed.decisions[-1]["answer"] == answer
    assert "list_styles" in str(c._client.requests[0]["messages"])
    assert answer in str(c._client.requests[0]["messages"])
    assert len(c._client.requests) == 2 and answer in c.sources
    assert c.recovery_planning["calls"] == 1
    assert list(out.glob("recovery*")) == [initial.out_dir]
    assert (initial.out_dir / "outputs" / source.name).is_file()
    assert source.read_bytes() == original
    if retained_output:
        assert resumed.ready[0].output == retained_output and retained_output.read_bytes() == retained_bytes


def test_compile_handoff_retains_time_and_does_not_guess_fallback_pricing(tmp_path):
    from spec_surgeon.compiler import CompileResult
    from spec_surgeon.continuous_recovery import after_compile
    source = write_document(tmp_path / "source.docx")
    prior = CompileResult(status="unsupported", seconds=10.0,
        usage={"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 0,
               "cache_creation_input_tokens": 0}, served_models=["claude-sonnet-5-5"],
        transcript=[{"turn": 1, "role": "assistant", "fell_back": True}])
    c = after_compile("Make the title bold", [source], None, prior, client=FakeClient(proposal()),
                      work_dir=tmp_path / "chat")
    assert c.seconds > 10.0 and c.calls == 2 and c.usage.calls == 2
    assert c.usage.as_dict()["input_tokens"] == 1100
    assert c.usage.cost_usd() is None  # aggregate usage cannot price each fallback attempt
