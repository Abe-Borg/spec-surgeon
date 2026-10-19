"""Ordinary delivery depends on the approved recipe and the independent engine suite."""
from __future__ import annotations

import pytest
from docx import Document

from fake_claude import FakeClient, resp, tool
from spec_surgeon import compiler
from spec_surgeon.cli import main
from spec_surgeon.gui import Session
from spec_surgeon.batch import run_batch
from spec_surgeon.chain import chain_from_specs, run_chain

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


def test_preview_is_the_only_ordinary_approval_and_calls_no_second_planner(tmp_path):
    folder = tmp_path / "sources"
    folder.mkdir()
    source = document(folder / "spec.docx")
    before = source.read_bytes()
    # The preview establishes the desired scope, including when it differs from an old request contract.
    spec = recipe({"text_regex": "^Owner"})
    fake = FakeClient(resp(tool("submit_editspec", {"spec": spec})))      # one scripted turn: a second call fails
    session = Session(documents=[source], out_root=tmp_path / "results", client=fake, visual_check=False)
    session.compile(REQUEST, lambda _: None)
    session.make_preview(False, lambda _: None)
    assert session.run_blocked_reason() == ""
    assert len(fake.requests) == 1                                        # the plan; no second outcome planner
    session.run(False, False, lambda _: None)
    assert session.last_batch.counts() == {"PASS": 1, "FAIL": 0, "EXCEPTION": 0, "PENDING": 0}
    delivered = next((session.last_run_dir / "outputs").glob("*.docx"))
    assert Document(delivered).paragraphs[2].runs[0].bold
    assert session.last_batch.files[0].record.get("GOAL") is None
    assert source.read_bytes() == before


@pytest.mark.parametrize("chain,in_place", [(False, False), (False, True), (True, False), (True, True)])
def test_legacy_outcome_metadata_cannot_withhold_verified_delivery(tmp_path, chain, in_place):
    source = document(tmp_path / "spec.docx")
    spec = recipe({"text_regex": "^Owner"})
    out = tmp_path / "run"
    kwargs = dict(workers=1, do_render=False, in_place=in_place,
                  goal_contract=CONTRACT, goal_bindings={})  # even missing old bindings cannot create a gate
    result = (run_chain([str(source)], chain_from_specs([("bold", spec)]), out, **kwargs) if chain else
              run_batch([str(source)], "edit-spec", {}, out, spec=spec, **kwargs))
    assert result.counts() == {"PASS": 1, "FAIL": 0, "EXCEPTION": 0, "PENDING": 0}
    assert result.goal_contract == CONTRACT
    assert result.files[0].delivered_to
    record = result.files[0].results[-1].record if chain else result.files[0].record
    assert record.get("GOAL") is None


def test_cli_yes_runs_without_a_planner_and_the_legacy_outcome_flags_are_gone(tmp_path, monkeypatch):
    source = document(tmp_path / "spec.docx")
    spec = recipe({"text_regex": "^Owner"})
    fake = FakeClient(resp(tool("submit_editspec", {"spec": spec})))
    monkeypatch.setattr(compiler, "_client", lambda: fake)
    monkeypatch.setattr("spec_surgeon.llm.credentials_found", lambda: True)
    monkeypatch.setattr("spec_surgeon.cli._interactive", lambda: False)
    result_dir = tmp_path / "ordinary"
    assert main(["do", REQUEST, str(source), "--out", str(result_dir), "--yes", "--no-render",
                 "--workers", "1"]) == 0
    assert len(fake.requests) == 1
    assert (result_dir / "run" / "outputs" / source.name).is_file()
    with pytest.raises(SystemExit):                     # the separate outcome approval no longer exists at all
        main(["run-spec", str(result_dir / "preview" / "spec.json"), str(source), "--out", str(tmp_path / "legacy"),
              "--outcome-review", str(tmp_path / "review.json"), "--outcome-approval", "obsolete-digest",
              "--no-render", "--workers", "1"])
    assert not (tmp_path / "legacy").exists()


def test_ordinary_preview_still_refuses_changed_source(tmp_path):
    folder = tmp_path / "sources"
    folder.mkdir()
    source = document(folder / "spec.docx")
    session = Session(documents=[source], out_root=tmp_path / "results", visual_check=False)
    session._set_outcome(spec=recipe({"text_regex": "^Owner"}), source="compiled")
    session.make_preview(False, lambda _: None)
    doc = Document(source)
    doc.add_paragraph("A new user edit")
    doc.save(source)
    with pytest.raises(ValueError, match="changed since the preview"):
        session.run(False, False, lambda _: None)
    assert session.last_batch is None


def test_resolve_with_no_exceptions_returns_without_model_calls(tmp_path, monkeypatch):
    folder = tmp_path / "sources"
    folder.mkdir()
    source = document(folder / "spec.docx")
    session = Session(documents=[source], out_root=tmp_path / "results", visual_check=False)
    session._set_outcome(spec=recipe({"text_regex": "^Owner"}), source="compiled")
    session.make_preview(False, lambda _: None)
    def unexpected(*args, **kwargs):
        pytest.fail("nothing to resolve must not invoke recovery")
    monkeypatch.setattr("spec_surgeon.recovery.recover", unexpected)
    monkeypatch.setattr("spec_surgeon.recovery.recover_from_run", unexpected)
    assert "Nothing to resolve: 0" in session.resolve_remaining(lambda _: None)
    session.run(False, False, lambda _: None)
    assert "Nothing to resolve: 0" in session.resolve_remaining(lambda _: None)
