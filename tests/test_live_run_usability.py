"""Regressions motivated by the live-run plan; no live-model acceptance is claimed here."""
from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from fake_claude import FakeClient, resp, tool
from make_fixtures import build
from spec_surgeon import compiler, llm, render
from spec_surgeon.catalog import describe_op, op_catalog
from spec_surgeon.ops import get_op, merge_fragments
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.preview import SampleView, render_samples
from spec_surgeon.verify import run_suite
from test_vision import make_pdf


def test_compiler_carries_every_operations_sheet_and_reads_the_rest_on_demand(tmp_path):
    """Every operation's selectors and parameters ride the cached system prompt (the op sheet), so a plan needs no
    round trip per operation; describe_op still returns an operation's verification rules and worked examples."""
    from spec_surgeon.catalog import op_sheet
    source = tmp_path / "spec.docx"
    source.write_bytes(build("21 13 13", "SPRINKLERS"))
    spec = {"request": "remove headers", "summary": "remove headers", "mode": "formatting",
            "ops": [{"op": "remove_header_footer", "select": {"sections": "all", "kinds": ["header"]}, "params": {}}]}
    fake = FakeClient(resp(tool("describe_op", {"name": "remove_header_footer"})),
                      resp(tool("submit_editspec", {"spec": spec}, "submit")))
    result = compiler.compile_request("remove headers", [source], client=fake)
    assert result.status == "ok", result.errors
    prompt = fake.requests[0]["system"]
    sheet = op_sheet()
    assert json.dumps(sheet["ops"]["remove_header_footer"]["select"]) in prompt or "remove_header_footer" in prompt
    assert len(prompt) < len(json.dumps(op_catalog()))          # the sheet is the catalog without its examples
    docs = json.loads(fake.requests[1]["messages"][-1]["content"][0]["content"])
    assert docs["params"] == get_op("remove_header_footer").params_schema
    assert docs["verification"] and docs["worked_examples"]
    assert all(any(op["op"] == "remove_header_footer" for op in example["ops"])
               for example in docs["worked_examples"])
    with pytest.raises(ValueError, match="Unknown operation"):
        describe_op("not_an_op")


def test_cancellation_prevents_next_provider_call_and_scope_resets():
    event = threading.Event()
    fake = FakeClient(resp(tool("submit_editspec", {"spec": {}})))
    event.set()
    arguments = dict(model=llm.DEFAULT_MODEL, system="s", tools=[], messages=[])
    with llm.cancellation_scope(event), pytest.raises(llm.UserCancelled):
        llm.call(fake, **arguments)
    assert fake.requests == []
    assert llm.call(fake, **arguments).stop_reason == "tool_use"
    assert len(fake.requests) == 1


@pytest.mark.parametrize("case_insensitive,flexible,expected", [(True, True, 1), (False, True, 0), (True, False, 0)])
def test_selectors_match_variation_and_verifier_rechecks_targets(case_insensitive, flexible, expected):
    package = DocxPackage(build("21 13 13", "SPRINKLERS"))
    part = "word/document.xml"
    root = package.xml(part)
    root.find(".//" + W + "t").text = "Section\u00a021\u00a0  13\t13.13"
    package.write(part, serialize_xml(root))
    original = package.output_bytes()
    package = DocxPackage(original)
    select = {"paragraphs": {"text_regex": r"^SECTION 21 13 13\.13$",
                              "case_insensitive": case_insensitive, "flexible_whitespace": flexible}}
    op = get_op("set_properties")
    fragment = op.plan(package, select, {"paragraph": {"keepNext": True}, "tracked": False})
    plan = merge_fragments([fragment])
    output = package.copy()
    op.apply(output, fragment)
    record = run_suite(original, output.output_bytes(), plan.to_dict(), "before.docx", "after.docx")
    assert record.passed, record.to_markdown()
    changed = package.xml(part).xpath("count(.//w:pPr/w:keepNext)", namespaces={"w": W[1:-1]})
    actual = output.xml(part).xpath("count(.//w:pPr/w:keepNext)", namespaces={"w": W[1:-1]})
    assert actual - changed == expected
    if expected:
        wrong = output.copy()
        badroot = wrong.xml(part)
        child = badroot.find(".//" + W + "keepNext")
        child.set(W + "val", "0")
        wrong.write(part, serialize_xml(badroot))
        assert not run_suite(original, wrong.output_bytes(), plan.to_dict(), "before.docx", "wrong.docx").passed


def test_page_growth_is_visible_and_advisory(tmp_path, monkeypatch):
    source, output = tmp_path / "source.docx", tmp_path / "output.docx"
    source.write_bytes(build("21 13 13", "SPRINKLERS"))
    output.write_bytes(source.read_bytes())
    before, after = make_pdf(tmp_path / "before.pdf", pages=2), make_pdf(tmp_path / "after.pdf", pages=3)
    monkeypatch.setattr(render, "pick", lambda _: "word")
    monkeypatch.setattr(render, "render_pdfs", lambda paths, folder, *a, **kw:
                        {p: before if folder.name == "source" else after for p in paths})
    sample = SampleView(source=source, output=output, passed=True)
    warnings = []
    render_samples([sample], tmp_path / "preview", "auto", True, warnings)
    assert sample.pages_before == 2 and sample.pages_after == 3 and sample.passed
    assert sample.to_dict()["pages_after"] == 3
    assert any("pages 2 → 3" in warning for warning in warnings)
    from spec_surgeon.preview import ChainPreview, GeneratedPreview
    assert "pages: 2 → 3" in GeneratedPreview(tmp_path, "custom", samples=[sample]).to_text()
    batch_plan = SimpleNamespace(to_text=lambda **kwargs: "Planned", spec={})
    assert "pages: 2 → 3" in ChainPreview(tmp_path, batch_plan, samples=[sample]).to_text()


