"""The identity approved in a frontend survives its handoff to queued engine work."""
from docx import Document

from fake_claude import FakeClient, resp, tool
from spec_surgeon.gui import Session
from spec_surgeon.preview import approval_inputs, approval_token, generated_key
from test_codegen import GOOD_OP
from test_execution_delivery_safety import document


def changed_source(tmp_path):
    path = document(tmp_path / "changed.docx")
    doc = Document(path)
    doc.paragraphs[1].text = "WATER SUPPRESSION"
    doc.save(path)
    return path.read_bytes()


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
    session = Session(documents=[source], out_root=tmp_path / "results", out_dir=tmp_path / "results",
                      visual_check=False)
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
