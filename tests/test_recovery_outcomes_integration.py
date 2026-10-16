"""Final requirements survive recovery translation and the saved-chain CLI surface."""
from __future__ import annotations

import json
from pathlib import Path

from fake_claude import FakeClient, resp, tool
from spec_surgeon import library, recovery
from spec_surgeon.chain import chain_from_specs
from spec_surgeon.cli import main as cli_main
from test_chain_outcomes import _document, _font, _require_font
from test_codegen import GOOD_OP


def _recover(tmp_path, goal):
    source = tmp_path / "spec.docx"
    before = _document()
    source.write_bytes(before)
    client = FakeClient(resp(tool("submit_repair", {"candidate": {"kind": "spec", "spec": goal}})))
    result = recovery.recover([source], goal["request"], tmp_path / "recovery", spec=goal, client=client,
                              options=recovery.RecoveryOptions(render=False, max_attempts=1,
                                                               use_memory=False, max_cost_usd=20))
    assert source.read_bytes() == before
    return result


def test_stage_font_assertion_optional_scopes_remain_a_real_final_check(tmp_path):
    goal = _font("Courier New")
    # This is a universal final requirement, explicitly without symbol-font exceptions.
    goal["assertions"] = [_require_font("Courier New", preserve_fonts=[],
                                        scope=["document", "headers", "footers", "footnotes", "endnotes"])]
    result = _recover(tmp_path, goal)
    assert result.ready, result.to_text()
    assert result.key["assertions"][0]["allow_absent_scopes"] is True
    assert result.ready[0].outcomes[0]["status"] == "PASS" and result.ready[0].outcomes[0]["checked"] > 0


def test_recovery_infers_oriented_page_dimensions_from_the_approved_operation(tmp_path):
    goal = {"request": "Make the Letter page landscape", "summary": "Letter landscape", "mode": "formatting",
            "ops": [{"op": "set_page_setup", "params": {"size": {"w": 12240, "h": 15840},
                                                          "orientation": "landscape"}}]}
    result = _recover(tmp_path, goal)
    assert result.ready, result.to_text()
    assert result.key["assertions"][0]["size"] == {"w": 15840, "h": 12240}


def test_allowed_custom_page_repair_passes_an_original_plan_frozen_before_generation(tmp_path):
    source = tmp_path / "spec.docx"
    before = _document()
    source.write_bytes(before)
    goal = {"request": "Set the header distance to 900 twips", "summary": "Header distance", "mode": "formatting",
            "ops": [{"op": "set_page_setup", "params": {"margins": {"header": 900}}}]}
    client = FakeClient(resp(tool("custom_transform", {"proposal": "Set the header distance"})),
                        resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}})))
    result = recovery.recover([source], goal["request"], tmp_path / "recovery", spec=goal, client=client,
                              options=recovery.RecoveryOptions(render=False, allow_codegen=True, max_attempts=1,
                                                               use_memory=False, max_cost_usd=20))
    assert result.ready, result.to_text()
    record_path = Path(result.attempts[0]["approved_checks"][0]["record"])
    record = json.loads(record_path.read_text())
    assert record["passed"] is True and any(c["id"] == "V10" and c["status"] == "PASS" for c in record["checks"])
    plan = json.loads((record_path.parent.parent / "approved-plans" / "spec.json").read_text())
    assert plan["property_assertions"][0]["op"] == "set_page_setup"
    assert source.read_bytes() == before


def test_cli_flattening_a_saved_chain_keeps_its_final_constraints(tmp_path):
    source = tmp_path / "spec.docx"
    before = _document()
    source.write_bytes(before)
    saved = chain_from_specs([("Arial", _font("Arial"))], assertions=[_require_font()])
    library.save_chain("Keep Arial", saved)
    out = tmp_path / "cli"
    code = cli_main(["chain", "--stage", "chain:Keep Arial", "--stage", json.dumps(_font("Courier New")),
                     str(source), "--out", str(out), "--no-render", "--yes", "--workers", "1"])
    assert code == 1
    preview = json.loads((out / "preview" / "chain.json").read_text())
    assert preview["assertions"] == saved["assertions"]
    report = json.loads((out / "run" / "chain.json").read_text())
    assert report["files"][0]["stages"] == ["PASS", "PASS"]
    assert report["files"][0]["code"] == "chain-outcome"
    assert not (out / "run" / "outputs").exists() and source.read_bytes() == before
