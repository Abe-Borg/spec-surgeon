"""Built-in workflow discovery and the real keyless preview/approval/delivery route."""
import hashlib
import json

import pytest

from make_fixtures import build
from spec_surgeon import llm
from spec_surgeon.cli import main


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "credentials_found", lambda: False)
    path = tmp_path / "source" / "spec.docx"
    path.parent.mkdir()
    path.write_bytes(build("21 13 13", "SPRINKLER SYSTEMS"))
    return path


def test_discovery_lists_inputs_without_credentials(capsys):
    assert main(["workflows", "list", "--json"]) == 0
    entries = json.loads(capsys.readouterr().out)
    assert {entry["id"] for entry in entries} >= {"architect-styles", "match-numbering", "format-tables", "proofing-language"}
    assert main(["workflows", "show", "architect-styles", "--json"]) == 0
    entry = json.loads(capsys.readouterr().out)
    assert "template" in {field["name"] for field in entry["inputs"]}


@pytest.mark.parametrize("arguments", [
    ["show"], ["show", "nonexistent"], ["use", "proofing-language"],
])
def test_invalid_workflow_request_is_explained(arguments, capsys):
    assert main(["workflows", *arguments]) == 2
    assert "Workflow refused:" in capsys.readouterr().err


def test_preview_does_not_run_then_explicit_approval_delivers_copies(source, tmp_path, capsys):
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps({"language": "en-GB"}), encoding="utf-8")
    args = ["workflows", "use", "proofing-language", str(source), "--inputs", "@" + str(inputs),
            "--no-render", "--workers", "1"]
    preview = tmp_path / "preview-only"
    assert main([*args, "--out", str(preview)]) == 0
    assert (preview / "preview" / "chain.json").is_file()
    assert not (preview / "run").exists()
    assert "Not run" in capsys.readouterr().out
    approved = tmp_path / "approved"
    assert main([*args, "--out", str(approved), "--yes", "--save-as", "British proofing"]) == 0
    assert (approved / "run" / "CHAIN_REPORT.md").is_file()
    assert "PASS 1" in capsys.readouterr().out
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    from spec_surgeon.library import load_chain
    saved = load_chain("British proofing")
    assert saved["stages"][0]["spec"]["ops"][0]["params"]["language"] == "en-GB"


@pytest.mark.parametrize("inputs", ["[]", '{"language":"en-GB","unexpected":true}', '{"language":false}'])
def test_invalid_inputs_cannot_create_results(source, tmp_path, inputs, capsys):
    out = tmp_path / "refused"
    assert main(["workflows", "use", "proofing-language", str(source), "--inputs", inputs,
                 "--out", str(out), "--no-render", "--yes"]) == 2
    assert not out.exists()
    assert "Workflow refused:" in capsys.readouterr().err


@pytest.mark.parametrize("inputs", [
    {"style": "Table Grid", "tables": "all", "properties": {"width": {"pct": 100}, "unknown": True}},
    {"style": "Table Grid", "tables": "all", "properties": {"cant_split": True}, "scope": ["unknown"]},
])
def test_invalid_operation_settings_are_refused(source, tmp_path, inputs, capsys):
    out = tmp_path / "refused"
    assert main(["workflows", "use", "format-tables", str(source), "--inputs", json.dumps(inputs),
                 "--out", str(out), "--no-render", "--yes"]) == 2
    assert not out.exists()
    assert "Workflow refused:" in capsys.readouterr().err
