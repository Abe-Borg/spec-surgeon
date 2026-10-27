"""Ordinary chat reaches outcome recovery without a separate project workflow."""
import json

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


def test_an_outcome_plans_tracked_changes_carry_the_users_author_never_a_literal_name(tmp_path, monkeypatch):
    """The planner's contract names no author (the fixtures' never do): the app fills in the engine's own default,
    as every built-in op uses (SPEC_SURGEON_AUTHOR, else the account name) — the code holds no name of its own."""
    import inspect
    from spec_surgeon import campaign_planner, goal_contracts, goal_operations, goals, review_display
    from spec_surgeon.verify import effects, goal_numbering, reader
    from spec_surgeon.verify import goals as verify_goals
    monkeypatch.setenv("SPEC_SURGEON_AUTHOR", "Outcome Tester")
    c, _ = conversation(tmp_path, proposal())
    turn = c.start()
    assert turn.choices[0].snapshot["contract"]["author"] == "Outcome Tester"
    assert campaign_planner.contract_author({}) == "Outcome Tester"
    assert campaign_planner.contract_author({"author": "Reviewer"}) == "Reviewer"
    assert goals.contract_author(None) == "Outcome Tester"
    assert reader.default_author() == "Outcome Tester"          # the verifier's own reading of the same rule
    for module in (campaign_planner, goal_contracts, goal_operations, goals, review_display,
                   effects, goal_numbering, reader, verify_goals):
        assert "Abraham" not in inspect.getsource(module), module.__name__


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


STYLE_SPEC = {"request": "Make the title bold", "summary": "Use a style", "mode": "formatting",
              "ops": [{"op": "apply_style", "params": {"style": "MissingStyle"}}]}


def _two_documents(tmp_path):
    """source.docx has no MissingStyle (the spec is refused on it); easy.docx has it (the spec plans)."""
    from docx import Document
    from docx.enum.style import WD_STYLE_TYPE
    source = write_document(tmp_path / "source.docx")
    easy = Document(source)
    easy.styles.add_style("MissingStyle", WD_STYLE_TYPE.PARAGRAPH)
    easy.paragraphs[0].style = "MissingStyle"
    easy.save(tmp_path / "easy.docx")
    return source, tmp_path / "easy.docx"


def _one_file_refuses(tmp_path, *responses):
    """Two documents; the spec plans on easy.docx and is refused on source.docx (it has no MissingStyle)."""
    from spec_surgeon.gui import Session
    source, _ = _two_documents(tmp_path)
    client = FakeClient(resp(tool("submit_editspec", {"spec": STYLE_SPEC})), *responses)
    s = Session(documents=sorted(tmp_path.glob("*.docx")), out_root=tmp_path / "out", client=client)
    assert s.compile("Make the title bold", lambda _: None) == "Compiled."
    return s, client, source


BOLD = {"request": "Make the title bold", "summary": "Bold", "mode": "formatting",
        "ops": [{"op": "set_properties", "params": {"run": {"b": True}}}]}


def test_the_outcome_plan_is_a_choice_the_user_picks_and_leaves_out_what_they_left_out(tmp_path, monkeypatch):
    """F1 and P2 (the 1.10.0 run over test-set-3: eight documents left out, the planner asked again which to do,
    then planned all 27): the outcome plan is offered as a choice, its words beneath it, and carried out only once
    the user picks it; the documents the user left out of the request stay out of its documents, its trials and its
    questions, and the planner is told they are out of scope."""
    from pathlib import Path
    from spec_surgeon import continuous_recovery, preview as preview_module
    from spec_surgeon.gui import Session
    from spec_surgeon.recovery import RecoveryOptions
    real = preview_module.make_preview

    def failing(*args, **kwargs):
        pv = real(*args, **kwargs)
        pv.samples[0].passed = False
        return pv
    monkeypatch.setattr(preview_module, "make_preview", failing)
    monkeypatch.setattr(continuous_recovery, "options_for", lambda *a, **kw:
                        RecoveryOptions(render=False, critic_review=False, visual_review=False))
    source, easy = _two_documents(tmp_path)
    client = FakeClient(resp(tool("submit_editspec", {"spec": BOLD, "leave_out": ["easy.docx"]})), proposal())
    s = Session(documents=[easy, source], out_root=tmp_path / "out", client=client, visual_check=False)
    assert s.compile("Make the title bold", lambda _: None) == "Compiled."
    assert s.left_out == [easy.resolve()] and s.conversation.left_out == [str(easy.resolve())]
    s.make_preview(False, lambda _: None)
    # the failed sample sent the request to the outcome planner, which was given source.docx alone
    assert s.spec is None and s.preview is None and len(client.requests) == 2
    asked = client.requests[-1]["messages"][0]["content"]
    # the paths as the message's JSON writes them (a Windows path's backslashes escaped)
    source_json, easy_json = (json.dumps(str(p.resolve()), ensure_ascii=False) for p in (source, easy))
    assert '"documents": [{"id": "0", "source": ' + source_json in asked and easy_json[1:-1] not in asked
    assert "easy.docx: the user left it out of this request" in asked
    outcome = s.outcome()
    assert outcome.kind == "question" and [c.kind for c in outcome.choices] == ["recovery_plan"]
    choice = outcome.choices[0]
    assert choice.label == "Use these outcomes and continue" and choice.words == []
    assert s.left_out == [easy.resolve()]                     # Left untouched keeps it while the question is open
    # the pick carries the plan out, on source.docx alone; easy.docx is still left out
    s.reply(choice_id=choice.id, log=lambda _: None)
    assert s.recovery is not None and [Path(f.source).name for f in s.recovery.files] == ["source.docx"]
    assert s.left_out == [easy.resolve()] and s.outcome().kind in ("preview", "question")
    assert len(client.requests) == 2                          # the deterministic route: no further model call


def test_a_file_the_plan_refuses_leaves_the_preview_of_the_others_standing(tmp_path):
    # The 1.9.0 live run: one file of thirteen refused at plan time (its even-page footers) voided the preview of the
    # other twelve and handed the conversation to the outcome planner. The refused file is Left untouched instead;
    # the preview stands, no model call is made, and the conversation hears which file and why, first.
    s, client, source = _one_file_refuses(tmp_path)
    s.make_preview(False, lambda _: None)
    assert s.spec is not None and s.preview is not None and s.outcome().kind == "preview"
    assert [f.source.name for f in s.preview.batch_plan.exceptions] == ["source.docx"]
    assert s.turn is None or not s.turn.choices
    assert not s.conversation.recovery_active and len(client.requests) == 1
    note = s.conversation.notes[-1]
    assert note.index("LEFT UNTOUCHED") < note.index("PREVIEW —")
    assert "source.docx: [" in note and "1 of 2 file(s) plan cleanly." in note


def test_create_runs_only_the_files_the_plan_covers_and_starts_no_recovery(tmp_path):
    # From review (Codex on #91): the refused file stayed in the run, failed there again and sent the request to
    # recovery after the copies — the outcome planner taking over one step later, for a file the window had listed as
    # Left untouched, not part of the plan. It is never run; the others get their copies; no model call follows.
    from spec_surgeon.document_task import DocumentTask
    from spec_surgeon.gui import Session
    source, easy = _two_documents(tmp_path)
    before = source.read_bytes()
    client = FakeClient(resp(tool("submit_editspec", {"spec": STYLE_SPEC})))
    task = DocumentTask(Session(client=client, out_root=tmp_path / "out", visual_check=False), render=False)
    task.attach([easy, source])
    view = task.perform("send", text="Make the title bold")
    assert view["stage"] == "preview" and [item["name"] for item in view["set_aside"]] == ["source.docx"]
    task.perform("primary", token=view["token"])
    assert task.stage == "done" and list(task.results) == [str(easy)]
    assert [f.source.name for f in task.session.last_batch.files] == ["easy.docx"]
    assert task.session.recovery is None and len(client.requests) == 1
    assert source.read_bytes() == before


def test_setting_aside_every_previewed_document_beside_a_refused_one_begins_the_pass(tmp_path):
    # From review (Codex on #93): the refused file counted as left to run, so the all-set-aside path was skipped and
    # the run, which drops it, found nothing and failed. Setting aside every previewed document begins the pass.
    from spec_surgeon.document_task import DocumentTask
    from spec_surgeon.gui import Session
    source, easy = _two_documents(tmp_path)
    before = [p.read_bytes() for p in (source, easy)]
    client = FakeClient(resp(tool("submit_editspec", {"spec": STYLE_SPEC})))
    task = DocumentTask(Session(client=client, out_root=tmp_path / "out", visual_check=False), render=False)
    task.attach([easy, source])
    view = task.perform("send", text="Make the title bold")
    assert task.session.runnable_files() == [easy.resolve()]
    task.perform("primary", token=view["token"], hold=[str(easy)])
    assert task.results == {} and task.session.last_batch is None
    # the refused file joins the pass from the to-do list, with the plan's reason (it never ran either)
    assert task.focus is not None and task.focus["documents"] == [str(easy), str(source)]
    assert task.focus["why"][str(source)]["why"] == "refused"
    assert "MissingStyle" in task.focus["why"][str(source)]["reason"]
    assert any("set every document aside" in m["text"] for m in task.messages if m["role"] == "app")
    assert [p.read_bytes() for p in (source, easy)] == before


BOLD_THEN_STYLE = {"request": "Make the title bold", "summary": "Bold, then the style",
                   "stages": [{"name": "bold", "spec": {"request": "Make the title bold", "summary": "Bold",
                                                        "mode": "formatting",
                                                        "ops": [{"op": "set_properties", "params": {"run": {"b": True}}}]}},
                              {"name": "style", "spec": STYLE_SPEC}]}


def _a_later_step_refuses(tmp_path, *responses):
    """Two documents, a chain of two steps: the first plans on both, the second (MissingStyle) refuses source.docx,
    and only on its sample, since a chain plans its later steps on the samples alone."""
    from spec_surgeon.gui import Session
    source, easy = _two_documents(tmp_path)
    client = FakeClient(resp(tool("submit_editspec", {"spec": BOLD_THEN_STYLE})), *responses)
    s = Session(documents=[easy, source], out_root=tmp_path / "out", client=client, visual_check=False)
    assert s.compile("Make the title bold", lambda _: None) == "Compiled."
    return s, client, source, easy


def test_a_sample_a_later_step_refuses_leaves_the_preview_of_the_others_standing(tmp_path):
    # The 1.10.0 live run (2026-10-23): a chain's font step refused two samples' list numbering (numbering-unsupported)
    # and the nineteen-file preview, sixteen of its samples passed, went to the outcome planner — twice in one morning.
    # A later step's refusal is a refused file like a first step's: Left untouched, the preview stands. Every step is
    # planned on every file now (backlog Q2), so the refusal is the batch plan's, naming its step.
    from spec_surgeon.preview import refusals
    s, client, source, easy = _a_later_step_refuses(tmp_path)
    s.make_preview(False, lambda _: None)
    assert s.spec is not None and s.preview is not None and s.outcome().kind == "preview"
    [refused] = s.preview.batch_plan.exceptions                 # the first step planned on both, the second not
    assert refused.source.name == "source.docx" and refused.exception["chain_stage"] == 2
    assert [p.name for p, _ in refusals(s.preview)] == ["source.docx"]
    assert not s.conversation.recovery_active and len(client.requests) == 1
    note = s.conversation.notes[-1]
    assert note.index("LEFT UNTOUCHED") < note.index("PREVIEW —")
    assert "source.docx: [" in note and "MissingStyle" in note and "1 of 2 file(s) plan cleanly." in note


def test_create_leaves_a_sample_a_later_step_refused_untouched(tmp_path):
    from spec_surgeon.document_task import DocumentTask
    from spec_surgeon.gui import Session
    source, easy = _two_documents(tmp_path)
    before = source.read_bytes()
    client = FakeClient(resp(tool("submit_editspec", {"spec": BOLD_THEN_STYLE})))
    task = DocumentTask(Session(client=client, out_root=tmp_path / "out", visual_check=False), render=False)
    task.attach([easy, source])
    view = task.perform("send", text="Make the title bold")
    assert view["stage"] == "preview"
    assert [item["name"] for item in view["set_aside"]] == ["source.docx"]
    assert "MissingStyle" in view["set_aside"][0]["reason"]
    assert [sample.source.name for sample in view["samples"]] == ["easy.docx"]   # no page of a copy nobody makes
    assert task.session.runnable_files() == [easy.resolve()]
    task.perform("primary", token=view["token"])
    assert task.stage == "done" and list(task.results) == [str(easy)]
    assert task.session.recovery is None and len(client.requests) == 1
    assert source.read_bytes() == before


def test_a_preview_whose_every_sample_a_later_step_refuses_investigates_the_outcome(tmp_path, monkeypatch):
    # No sample got through: nothing shows the plan working, so the preview cannot stand on its own.
    from spec_surgeon import preview as preview_module
    real = preview_module.make_chain_preview

    def every_sample_refused(*args, **kwargs):
        pv = real(*args, **kwargs)
        for sv in pv.samples:
            sv.passed, sv.exception = None, {"code": "not-found", "reason": "planted", "kind": "refusal"}
        return pv
    monkeypatch.setattr(preview_module, "make_chain_preview", every_sample_refused)
    s, client, source, easy = _a_later_step_refuses(tmp_path, proposal())
    s.make_preview(False, lambda _: None)
    assert s.spec is None and s.preview is None
    assert s.turn.choices[0].kind == "recovery_plan"


def test_a_sample_the_engine_failed_on_still_investigates_the_outcome(tmp_path, monkeypatch):
    # Only a refusal (a case the op cannot predict, with its code) sets a file aside; the engine's own failure on a
    # sample is a failed preview, as before.
    from spec_surgeon import preview as preview_module
    real = preview_module.make_chain_preview

    def crashed(*args, **kwargs):
        pv = real(*args, **kwargs)
        broken = next(sv for sv in pv.samples if sv.passed)
        broken.passed = None
        broken.exception = {"code": "internal-error", "reason": "planted", "kind": "internal-error"}
        return pv
    monkeypatch.setattr(preview_module, "make_chain_preview", crashed)
    s, client, source, easy = _a_later_step_refuses(tmp_path, proposal())
    s.make_preview(False, lambda _: None)
    assert s.spec is None and s.preview is None
    assert s.turn.choices[0].kind == "recovery_plan"


def test_a_sample_that_fails_its_check_still_investigates_the_outcome(tmp_path, monkeypatch):
    from spec_surgeon import preview as preview_module
    real = preview_module.make_preview

    def failing(*args, **kwargs):
        pv = real(*args, **kwargs)
        pv.samples[0].passed = False
        return pv
    monkeypatch.setattr(preview_module, "make_preview", failing)
    s, client, source = _one_file_refuses(tmp_path, proposal())
    s.make_preview(False, lambda _: None)
    assert s.spec is None and s.preview is None
    assert s.turn.choices[0].kind == "recovery_plan"
    assert "MissingStyle" in str(client.requests[-1]["messages"])
    assert source.is_file()


def test_a_preview_that_plans_on_no_file_investigates_the_outcome(tmp_path, monkeypatch):
    from spec_surgeon import preview as preview_module
    real = preview_module.make_preview

    def nothing_plans(*args, **kwargs):
        pv = real(*args, **kwargs)
        for f in pv.batch_plan.files:             # the file that planned is refused too: the plan stands on nothing
            if f.status != "exception":
                f.status, f.exception = "exception", {"code": "not-found", "reason": "planted"}
        pv.samples = []
        return pv
    monkeypatch.setattr(preview_module, "make_preview", nothing_plans)
    s, client, source = _one_file_refuses(tmp_path, proposal())
    s.make_preview(False, lambda _: None)
    assert s.spec is None and s.preview is None
    assert s.turn.choices[0].kind == "recovery_plan"


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
