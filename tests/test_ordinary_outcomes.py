"""Natural-language intent is reviewed separately from the recipe that implements it."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest
from docx import Document

from fake_claude import FakeClient, resp, tool
from spec_surgeon import compiler, ordinary_outcomes
from spec_surgeon.cli import main
from spec_surgeon.fingerprint import fingerprint
from spec_surgeon.gui import Session
from spec_surgeon.package import DocxPackage


REQUEST = "Make the FIRE SUPPRESSION section title bold in every document."
CONTRACT = {"kind": "document_effects", "effects": [{"id": "title", "type": "format",
    "target": {"locators": [{"exact_text": "FIRE SUPPRESSION"}], "cardinality": 1}, "run": {"b": True}}]}


def document(path, *, title_style="Normal", body_style="Normal", duplicate=False):
    doc = Document()
    doc.add_paragraph("SECTION 21 13 13")
    title = doc.add_paragraph("FIRE SUPPRESSION", style=title_style)
    title.runs[0].bold = False
    if duplicate:
        doc.add_paragraph("FIRE SUPPRESSION")
    body = doc.add_paragraph("Owner shall retain all content.", style=body_style)
    body.runs[0].bold = False
    doc.save(path)
    return path


def recipe(predicate):
    return {"request": REQUEST, "summary": "Bold the requested section titles", "mode": "formatting",
        "ops": [{"op": "set_properties", "select": {"paragraphs": predicate},
                 "params": {"run": {"b": True}, "tracked": False}}]}


def client_for(spec, contract=CONTRACT):
    return FakeClient(resp(tool("submit_editspec", {"spec": spec})),
        resp(tool("propose_plan", {"contract": contract,
             "rationale": "Bold exactly one FIRE SUPPRESSION title in each original document."})))


@pytest.mark.parametrize("in_place", [False, True])
def test_wrong_target_recipe_is_withheld_after_separate_outcome_approval(tmp_path, in_place):
    folder = tmp_path / "sources"
    folder.mkdir()
    source = document(folder / "spec.docx")
    before = source.read_bytes()
    fake = client_for(recipe({"text_regex": "^Owner"}))
    session = Session(folder=folder, out_root=tmp_path / "results", client=fake, visual_check=False)
    session.compile(REQUEST, lambda _: None)
    preview = session.make_preview(False, lambda _: None)
    assert "FIRE SUPPRESSION" in session.outcome_review_text()
    assert "paragraph 2" in session.outcome_review_text()
    assert session.outcome_review["status"] == "PROPOSED"
    assert "approve" in session.run_blocked_reason()
    with pytest.raises(ValueError, match="approve"):
        session.run(in_place, False, lambda _: None)
    # The second provider exchange is a different planner, without the submitted recipe.
    assert "SUPPORTED GOAL CONTRACTS" in fake.requests[1]["system"]
    assert "^Owner" not in json.dumps(fake.requests[1]["messages"])
    session.approve_outcomes(session.outcome_review["digest"])
    session.run(in_place, False, lambda _: None)
    assert session.last_batch.counts()["FAIL"] == 1
    assert not any((session.last_run_dir / "outputs").glob("*.docx"))
    assert any((session.last_run_dir / "failed").glob("*.docx"))
    assert source.read_bytes() == before


@pytest.mark.parametrize("missing_match", [False, True])
def test_per_file_outcomes_reject_style_generalization_and_zero_matches(tmp_path, missing_match):
    folder = tmp_path / "sources"
    folder.mkdir()
    first = document(folder / "a.docx", title_style="Heading 1")
    second = document(folder / "b.docx", body_style="Normal" if missing_match else "Heading 1")
    if not missing_match:
        assert fingerprint(DocxPackage.from_path(first))["pattern_id"] == fingerprint(DocxPackage.from_path(second))["pattern_id"]
    session = Session(folder=folder, out_root=tmp_path / "results",
        client=client_for(recipe({"style_in": ["Heading1"]})), visual_check=False)
    session.compile(REQUEST, lambda _: None)
    session.make_preview(False, lambda _: None)
    review_text = session.outcome_review_text()
    assert str(first) in review_text and str(second) in review_text
    assert review_text.count("paragraph 2: 'FIRE SUPPRESSION'") == 2
    session.approve_outcomes(session.outcome_review["digest"])
    session.run(False, False, lambda _: None)
    assert session.last_batch.counts()["PASS"] == 1
    assert session.last_batch.counts()["FAIL"] == 1
    rejected = next(item for item in session.last_batch.files if item.source == second)
    assert rejected.delivered_to is None


def test_review_approval_refuses_stale_scope_source_contract_and_preview(tmp_path):
    source = document(tmp_path / "spec.docx")
    review = ordinary_outcomes.build_review(REQUEST, [source], CONTRACT)
    ordinary_outcomes.validate(review, [source], approval=review["digest"])
    changed = copy.deepcopy(review)
    changed["contract"]["effects"][0]["run"]["b"] = False
    with pytest.raises(ValueError, match="review changed"):
        ordinary_outcomes.validate(changed, [source], approval=review["digest"])
    with pytest.raises(ValueError, match="scope changed"):
        ordinary_outcomes.validate(review, [], approval=review["digest"])
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="document changed"):
        ordinary_outcomes.validate(review, [source], approval=review["digest"])


def test_ambiguous_targets_need_deliberate_choice_and_new_review_digest(tmp_path):
    source = document(tmp_path / "spec.docx", duplicate=True)
    review = ordinary_outcomes.build_review(REQUEST, [source], CONTRACT)
    assert review["status"] == "NEEDS_INPUT"
    candidates = review["documents"][0]["resolution"]["effects"]["title"]["candidates"]
    with pytest.raises(ValueError, match="not independently covered"):
        ordinary_outcomes.validate(review, [source], approval=review["digest"])
    selected = ordinary_outcomes.choose_targets(review, source, [candidates[1]["candidate_id"]], effect_id="title")
    assert selected["status"] == "PROPOSED" and selected["digest"] != review["digest"]
    ordinary_outcomes.validate(selected, [source], approval=selected["digest"])


def test_cli_yes_does_not_approve_unseen_outcomes_and_explicit_review_enforces_them(tmp_path, monkeypatch, capsys):
    source = document(tmp_path / "spec.docx")
    fake = client_for(recipe({"text_regex": "^Owner"}))
    monkeypatch.setattr(compiler, "_client", lambda: fake)
    monkeypatch.setattr("spec_surgeon.llm.credentials_found", lambda: True)
    monkeypatch.setattr("spec_surgeon.cli._interactive", lambda: False)
    result_dir = tmp_path / "first"
    args = ["do", REQUEST, str(source), "--out", str(result_dir), "--yes", "--no-render", "--workers", "1"]
    assert main(args) == 2
    assert "--outcome-approval" in capsys.readouterr().out
    review_path = result_dir / "outcome-review.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert not (result_dir / "run" / "outputs").exists()
    spec_path = result_dir / "preview" / "spec.json"
    assert main(["run-spec", str(spec_path), str(source), "--out", str(tmp_path / "unapproved"),
                 "--outcome-review", str(review_path), "--no-render", "--workers", "1"]) == 2
    before = source.read_bytes()
    assert main(["run-spec", str(spec_path), str(source), "--out", str(tmp_path / "approved"),
                 "--outcome-review", str(review_path), "--outcome-approval", review["digest"],
                 "--no-render", "--workers", "1", "--in-place"]) == 1
    assert source.read_bytes() == before


def test_manual_recipe_stays_keyless_and_reports_its_limit(tmp_path, capsys):
    source = document(tmp_path / "spec.docx")
    spec_path = tmp_path / "manual.json"
    spec_path.write_text(json.dumps(recipe({"text_regex": "^Owner"})))
    assert main(["run-spec", str(spec_path), str(source), "--out", str(tmp_path / "manual-out"),
                 "--no-render", "--workers", "1"]) == 0
    assert "request satisfaction is not independently checked" in capsys.readouterr().out


def test_session_approval_cannot_survive_changed_preview_or_lost_conversation(tmp_path):
    folder = tmp_path / "sources"
    folder.mkdir()
    source = document(folder / "spec.docx")
    session = Session(folder=folder, out_root=tmp_path / "results",
        client=client_for(recipe({"text_regex": "^FIRE SUPPRESSION$"})), visual_check=False)
    session.compile(REQUEST, lambda _: None)
    session.make_preview(False, lambda _: None)
    with pytest.raises(ValueError, match="exact outcomes"):
        session.approve_outcomes("not-the-reviewed-digest")
    session.end_conversation()
    assert "approve" in session.run_blocked_reason()
    session.approve_outcomes(session.outcome_review["digest"])
    session.preview.token = "another-preview"
    with pytest.raises(ValueError, match="preview changed"):
        session.run(False, False, lambda _: None)


def test_unsupported_independent_outcome_never_falls_back_to_recipe_pass(tmp_path):
    folder = tmp_path / "sources"
    folder.mkdir()
    document(folder / "spec.docx")
    fake = FakeClient(resp(tool("submit_editspec", {"spec": recipe({"text_regex": "^Owner"})})),
                      resp(tool("unsupported", {"reason": "The requested spacing has no independent outcome grammar."})))
    session = Session(folder=folder, out_root=tmp_path / "results", client=fake, visual_check=False)
    session.compile(REQUEST, lambda _: None)
    session.make_preview(False, lambda _: None)
    assert session.outcome_review["status"] == "UNSUPPORTED"
    assert "no independent outcome grammar" in session.outcome_review_text()
    with pytest.raises(ValueError, match="independent coverage"):
        session.run(False, False, lambda _: None)
    assert session.last_batch is None


def test_retained_contract_retry_never_reproposes_its_outcomes(tmp_path, monkeypatch):
    folder = tmp_path / "sources"
    folder.mkdir()
    document(folder / "spec.docx")
    fake = client_for(recipe({"text_regex": "^Owner"}))
    session = Session(folder=folder, out_root=tmp_path / "results", client=fake, visual_check=False)
    session.compile(REQUEST, lambda _: None)
    session.make_preview(False, lambda _: None)
    session.approve_outcomes(session.outcome_review["digest"])
    session.run(False, False, lambda _: None)
    original_run = session.last_run_dir
    manifest = json.loads((original_run / "set_aside.json").read_text(encoding="utf-8"))
    assert manifest["goal_contract"] == CONTRACT
    session.load_set_aside(original_run)
    assert session.pending_recovery_run == original_run
    captured = []

    def resume(run_dir, out, **kwargs):
        captured.append((run_dir, kwargs))
        return SimpleNamespace(out_dir=out, questions=[], token="", review=None, files=[],
                               to_text=lambda: "Retained recovery")

    monkeypatch.setattr("spec_surgeon.recovery.recover_from_run", resume)
    assert session.compile("Clarify implementation without changing the approved title", lambda _: None) == "Retained recovery"
    assert captured[0][0] == original_run
    assert len(fake.requests) == 2  # no new compiler or outcome proposal
    assert session.pending_recovery_run is None and session.recovery is not None
    cli_routes = []
    monkeypatch.setattr("spec_surgeon.cli.cmd_resolve", lambda args: cli_routes.append(args.run_dir) or 0)
    assert main(["retry", str(original_run), "--yes", "--no-render"]) == 0
    assert cli_routes == [str(original_run)]


def test_declined_cli_run_preserves_outcome_authority_in_continuation(tmp_path, monkeypatch, capsys):
    source = document(tmp_path / "spec.docx")
    review = ordinary_outcomes.build_review(REQUEST, [source], CONTRACT)
    review_path = tmp_path / "review.json"
    ordinary_outcomes.save(review, review_path)
    fake = FakeClient(resp(tool("submit_editspec", {"spec": recipe({"text_regex": "^FIRE SUPPRESSION$"})})))
    monkeypatch.setattr(compiler, "_client", lambda: fake)
    monkeypatch.setattr("spec_surgeon.llm.credentials_found", lambda: True)
    monkeypatch.setattr("spec_surgeon.cli._interactive", lambda: True)
    answers = iter([True, False])  # approve the outcomes, then decline execution
    monkeypatch.setattr("spec_surgeon.cli._confirm", lambda _: next(answers))
    assert main(["do", REQUEST, str(source), "--out", str(tmp_path / "result"),
                 "--outcome-review", str(review_path), "--no-render", "--workers", "1"]) == 0
    continuation = capsys.readouterr().out.split("To run exactly this:")[-1]
    assert f'--outcome-review "{review_path}"' in continuation
    assert "--outcome-approval " + review["digest"] in continuation


def test_content_custom_generation_waits_for_independent_outcome_approval(tmp_path):
    from test_governed_codegen import CONTENT_OP, contract
    folder = tmp_path / "sources"
    folder.mkdir()
    source = document(folder / "spec.docx")
    goal = contract()
    fake = FakeClient(
        resp(tool("submit_editspec", {"spec": {"request": "r", "summary": "Custom title extraction",
            "unsupported": "A custom extraction method is needed", "proposal": "Replace FIRE with WATER"},
            "custom_transform": "possible"})),
        resp(tool("propose_plan", {"contract": goal, "rationale": "Replace FIRE with WATER in the exact title only."})),
        resp(tool("submit_op", {"source": CONTENT_OP, "params": {"replacement": "WATER"}})))
    session = Session(folder=folder, out_root=tmp_path / "results", client=fake, visual_check=False)
    session.compile("Change FIRE to WATER in the section title, untracked.", lambda _: None)
    session.reply(choice_id=session.turn.choices[0].id, log=lambda _: None)
    assert session.generated is None and session.pending_custom_transform is not None
    assert len(fake.requests) == 2  # neither code nor content capability was authorized by the model proposal
    with pytest.raises(ValueError, match="exact outcomes"):
        session.continue_custom_transform(lambda _: None)
    session.approve_outcomes(session.outcome_review["digest"])
    session.continue_custom_transform(lambda _: None)
    assert session.generated.passed and session.pending_custom_transform is None
    assert session.generated.goal_contract == goal
    session.run(False, False, lambda _: None)
    assert all(trial.passed and trial.record.get("GOAL").status == "PASS" for trial in session.last_trials)
    assert Document(source).paragraphs[1].text == "FIRE SUPPRESSION"
    assert Document(session.last_trials[0].output).paragraphs[1].text == "WATER SUPPRESSION"


def test_compilation_and_independent_planner_share_selected_model_policy(tmp_path, monkeypatch):
    folder = tmp_path / "sources"
    folder.mkdir()
    document(folder / "spec.docx")
    fake = client_for(recipe({"text_regex": "^FIRE SUPPRESSION$"}))
    policies = []
    original_propose = ordinary_outcomes.propose

    def propose(*args, **kwargs):
        policies.append(kwargs["options"])
        return original_propose(*args, **kwargs)

    monkeypatch.setattr(ordinary_outcomes, "propose", propose)
    session = Session(folder=folder, out_root=tmp_path / "results", client=fake, visual_check=False,
                      model="claude-sonnet-5-5", max_context_tokens=300000, max_output_tokens=8192, effort="low")
    session.compile(REQUEST, lambda _: None)
    session.make_preview(False, lambda _: None)
    assert len(fake.requests) == 2
    assert all(request["model"] == "claude-sonnet-5-5" and request["max_tokens"] == 8192 for request in fake.requests)
    assert policies[0].max_context_tokens == 300000 and policies[0].effort == "low"


def test_noninteractive_custom_outcome_planner_receives_user_answers(tmp_path, monkeypatch):
    from test_governed_codegen import contract
    source = document(tmp_path / "spec.docx")
    unsupported = {"request": "r", "summary": "Custom extraction", "unsupported": "Custom method needed",
                   "proposal": "Replace FIRE with the user's approved word."}
    fake = FakeClient(resp(tool("submit_editspec", {"spec": unsupported, "custom_transform": "possible"})),
        resp(tool("propose_plan", {"contract": contract(), "rationale": "Replace FIRE with the user's literal WATER."})))
    monkeypatch.setattr(compiler, "_client", lambda: fake)
    monkeypatch.setattr("spec_surgeon.llm.credentials_found", lambda: True)
    monkeypatch.setattr("spec_surgeon.cli._interactive", lambda: False)
    assert main(["do", "Replace FIRE using my answer", str(source), "--answer", "WATER", "--allow-codegen",
                 "--yes", "--no-render", "--out", str(tmp_path / "result")]) == 2
    reviewed_request = json.loads((tmp_path / "result" / "outcome-review.json").read_text(encoding="utf-8"))["request"]
    assert reviewed_request == "Replace FIRE using my answer\n\nUSER DECISIONS:\nWATER"
    assert "USER DECISIONS" in json.dumps(fake.requests[1]["messages"])
