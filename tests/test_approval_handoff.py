"""The identity approved in a frontend survives its handoff to queued engine work."""
import json

import pytest
from docx import Document

from fake_claude import FakeClient, resp, tool
from spec_surgeon.cli import main
from spec_surgeon.gui import Session
from spec_surgeon.preview import approval_inputs, approval_token, generated_key
from test_codegen import GOOD_OP
from test_ordinary_outcomes import document, recipe


def changed_source(tmp_path):
    path = document(tmp_path / "changed.docx")
    doc = Document(path)
    doc.paragraphs[1].text = "WATER SUPPRESSION"
    doc.save(path)
    return path.read_bytes()


@pytest.mark.parametrize("chain", [False, True])
def test_gui_preserves_approved_identity_between_frontend_check_and_engine_entry(tmp_path, monkeypatch, chain):
    folder = tmp_path / "sources"
    folder.mkdir()
    source = document(folder / "spec.docx")
    session = Session(folder=folder, out_root=tmp_path / "results", visual_check=False)
    spec = recipe({"text_regex": "^Owner"})
    if chain:
        session.chain = {"name": "Manual recipe chain", "stages": [{"spec": spec}]}
    else:
        session.spec = spec
    session.make_preview(False, lambda _: None)
    approved = session.preview.token_inputs
    replacement = changed_source(tmp_path)
    module_name = "spec_surgeon.chain" if chain else "spec_surgeon.batch"
    import importlib
    module = importlib.import_module(module_name)
    name = "run_chain" if chain else "run_batch"
    original = getattr(module, name)
    handoffs = []

    def run(*args, **kwargs):
        handoffs.append(kwargs.get("approved_inputs"))
        source.write_bytes(replacement)  # after Session's token check, before engine acquisition
        return original(*args, **kwargs)

    monkeypatch.setattr(module, name, run)
    try:
        session.run(True, False, lambda _: None)
    except ValueError:
        pass
    assert handoffs == [approved]
    assert source.read_bytes() == replacement
    result = session.last_chain if chain else session.last_batch
    assert result is None or result.counts()["PASS"] == 0


def test_run_spec_hands_off_the_same_capture_used_to_validate_approval(tmp_path, monkeypatch):
    from spec_surgeon import cli
    source = document(tmp_path / "spec.docx")
    spec = recipe({"text_regex": "^Owner"})
    spec_path = tmp_path / "recipe.json"
    spec_path.write_text(json.dumps(spec))
    original_capture = approval_inputs(spec, [source])
    token = approval_token(spec, [source])
    replacement = changed_source(tmp_path)
    original = cli.run_batch
    handoffs = []

    def run(*args, **kwargs):
        handoffs.append(kwargs.get("approved_inputs"))
        source.write_bytes(replacement)
        return original(*args, **kwargs)

    monkeypatch.setattr(cli, "run_batch", run)
    assert main(["run-spec", str(spec_path), str(source), "--approval", token, "--in-place",
                 "--out", str(tmp_path / "result"), "--workers", "1", "--no-render"]) != 0
    assert handoffs == [original_capture]
    assert source.read_bytes() == replacement


def test_generated_gui_run_keeps_its_approved_source_identity(tmp_path, monkeypatch):
    from spec_surgeon import codegen
    folder = tmp_path / "sources"
    folder.mkdir()
    source = document(folder / "spec.docx")
    fake = FakeClient(resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}})))
    generated = codegen.generate_op("Set the header distance to 900", "Set header distance", [source],
                                    tmp_path / "writer", client=fake)
    assert generated.passed
    key = generated_key(generated)
    approved = approval_inputs(key, [source])
    session = Session(folder=folder, out_root=tmp_path / "results", out_dir=tmp_path / "results", visual_check=False)
    session._show_generated(generated, session.out_dir, approval_token(key, [source]), approved,
                            "YOUR SAVED OPERATION: header distance", False, lambda _: None)
    replacement = changed_source(tmp_path)
    original = codegen.run_generated
    handoffs = []

    def run(*args, **kwargs):
        handoffs.append(kwargs.get("approved_inputs"))
        source.write_bytes(replacement)
        return original(*args, **kwargs)

    monkeypatch.setattr(codegen, "run_generated", run)
    try:
        session.run(False, False, lambda _: None)
    except ValueError:
        pass
    assert handoffs == [approved]
    assert source.read_bytes() == replacement
    assert not any((session.out_dir / "run" / "outputs").glob("*.docx"))
