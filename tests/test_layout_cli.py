"""The command line plans and runs a plan per layout from a file (Workstream L, CLI route)."""
from __future__ import annotations

import json
from pathlib import Path

from make_fixtures import build, build_styles
from spec_surgeon.cli import main
from spec_surgeon.fingerprint import fingerprint
from spec_surgeon.package import DocxPackage


def _plan(tmp_path):
    typed, styled = tmp_path / "a" / "21 13 13.docx", tmp_path / "b" / "21 13 16.docx"
    typed.parent.mkdir(); styled.parent.mkdir()
    typed.write_bytes(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    styled.write_bytes(build_styles("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS"))
    pid = lambda p: fingerprint(DocxPackage.from_path(p))["pattern_id"]  # noqa: E731
    bold = {"run": {"b": True}, "tracked": False}
    plan = {"kind": "layouts", "request": "Make the body paragraphs bold.", "summary": "two layouts", "layouts": [
        {"id": "A", "label": "typed numbers", "pattern_ids": [pid(typed)],
         "spec": {"summary": "by text", "mode": "formatting",
                  "ops": [{"op": "set_properties", "select": {"paragraphs": {"text_regex": r"^[A-E]\.\t"}}, "params": bold}]}},
        {"id": "B", "label": "styled", "pattern_ids": [pid(styled)],
         "spec": {"summary": "by style", "mode": "formatting",
                  "ops": [{"op": "set_properties", "select": {"paragraphs": {"style_in": ["ArchPara"]}}, "params": bold}]}}]}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    return path, typed, styled


def test_plan_spec_and_run_spec_accept_a_layout_plan(tmp_path, capsys):
    plan, typed, styled = _plan(tmp_path)
    assert main(["plan-spec", str(plan), str(typed), str(styled)]) == 0
    out = capsys.readouterr().out
    assert "LAYOUT PLAN" in out and "2 plan cleanly" in out
    out_dir = tmp_path / "out"
    assert main(["run-spec", str(plan), str(typed), str(styled), "--out", str(out_dir), "--no-render"]) == 0
    out = capsys.readouterr().out
    assert "PASS 2" in out and (out_dir / "LAYOUT_REPORT.md").is_file() and (out_dir / "plan.json").is_file()
    assert (out_dir / "layout-A" / "outputs" / "21 13 13.docx").is_file()
    assert (out_dir / "layout-B" / "outputs" / "21 13 16.docx").is_file()
