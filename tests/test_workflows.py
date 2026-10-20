"""Packaged workflows use the same verified, keyless preview/run/approval path as saved chains."""
from __future__ import annotations

import json

import pytest

from make_fixtures import SCHEME_LEVELS, build_numbering, build_scheme_example, build_style_template, build_styles
from spec_surgeon import llm
from spec_surgeon.chain import run_chain
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.workflows import WorkflowError, build_workflow, get_workflow, load_workflow_inputs, workflow_catalog


@pytest.fixture
def documents(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "credentials_found", lambda: False)
    folder = tmp_path / "specs"
    folder.mkdir()
    source = folder / "sprinklers.docx"
    source.write_bytes(build_styles())
    template = tmp_path / "office.docx"
    template.write_bytes(build_style_template())
    return source, template


def _all_text(path):
    pkg = DocxPackage.from_path(path)
    return {part: [e.text for e in pkg.xml(part).iter(W + "t", W + "delText")]
            for part in pkg.text_parts()}


@pytest.mark.parametrize("name", ["architect-styles", "match-numbering", "format-tables", "proofing-language"])
def test_workflows_execute_verified_stages_without_a_key(name, documents, tmp_path):
    source, template = documents
    if name == "architect-styles":
        values = {"template": str(template), "style_map": [{"from": "ArchNote", "to": "SpecNote"}],
                  "unused_styles": "custom", "keep_styles": ["ArchUnusedChar"]}
    elif name == "match-numbering":
        source.write_bytes(build_numbering(shared_restart=False))
        template.write_bytes(build_scheme_example())
        values = {"template": str(template), "levels": list(SCHEME_LEVELS), "source": "main"}
    elif name == "format-tables":
        values = {"template": str(template), "style": "TblMine", "tables": "all",
                  "properties": {"width": {"pct": 100}, "repeat_header_rows": 1, "cant_split": True}}
    else:
        values = {"language": "en-US", "replace_only": ["en-GB"]}
    before = source.read_bytes()
    expected_text = _all_text(source)
    chain = build_workflow(name, values)
    result = run_chain([str(source)], chain, tmp_path / "run", workers=1, renderer="none", do_render=False)
    assert result.counts()["PASS"] == 1, [(f.code, f.reason) for f in result.files]
    assert source.read_bytes() == before
    final = result.files[0].delivered_to
    assert _all_text(final) == expected_text
    assert all(s.batch.counts()["PASS"] == 1 for s in result.stages)
    assert (tmp_path / "run" / "CHAIN_REPORT.md").is_file()
    out = DocxPackage.from_path(final)
    if name == "architect-styles":
        styles = {s.get(W + "styleId") for s in out.xml("word/styles.xml").iter(W + "style")}
        assert "SpecNote" in styles and "ArchUnused" not in styles and "ArchUnusedChar" in styles
        assert "SpecNote" in [e.get(W + "val") for e in out.xml("word/document.xml").iter(W + "pStyle")]
        assert len(result.stages) == 2
    elif name == "format-tables":
        table = next(out.xml("word/document.xml").iter(W + "tbl"))
        assert table.find(f"{W}tblPr/{W}tblStyle").get(W + "val") == "TblMine"
        assert table.find(f"{W}tblPr/{W}tblW").get(W + "w") == "5000"
        assert table.find(f"{W}tr/{W}trPr/{W}tblHeader") is not None
    elif name == "proofing-language":
        languages = [e.get(W + "val") for e in out.xml("word/document.xml").iter(W + "lang")]
        assert "en-US" in languages and "fr-FR" in languages and "en-GB" not in languages
    else:
        assert "PRT" in [e.get(W + "val") for e in out.xml("word/document.xml").iter(W + "pStyle")]


def test_cleanup_requires_an_explicit_choice_and_inputs_are_snapshot_copied(documents):
    _, template = documents
    values = {"template": str(template), "style_map": [{"from": "ArchNote", "to": "SpecNote"}]}
    chain = build_workflow("architect-styles", values)
    values["style_map"][0]["to"] = "Normal"
    assert len(chain["stages"]) == 1
    assert chain["stages"][0]["spec"]["ops"][0]["params"]["map"][0]["to"] == "SpecNote"
    # Consumers cannot change the packaged metadata for the next invocation.
    catalog = workflow_catalog()
    catalog[0]["inputs"][0]["name"] = "different"
    assert get_workflow("architect-styles")["inputs"][0]["name"] == "template"


def test_inputs_resolve_relative_to_their_file(documents, tmp_path):
    _, template = documents
    path = tmp_path / "inputs.json"
    path.write_text(json.dumps({"template": template.name,
                                "style_map": [{"from": "ArchNote", "to": "SpecNote"}]}), encoding="utf-8")
    values, base = load_workflow_inputs("@" + str(path))
    chain = build_workflow("architect-styles", values, base)
    assert chain["stages"][0]["spec"]["ops"][0]["params"]["template"] == str(template)


@pytest.mark.parametrize("name,values,match", [
    ("proofing-language", {}, "language is required"),
    ("proofing-language", {"language": "en-US", "langauge": "en-CA"}, "unknown workflow inputs"),
    ("proofing-language", {"language": ""}, "must not be blank"),
    ("format-tables", {"style": "Table Grid", "tables": "all", "properties": {}}, "properties must request"),
    ("format-tables", {"style": "Table Grid", "tables": {"style_in": ["Old"]}, "properties": {"cant_split": True}}, "stable table selector"),
    ("format-tables", {"style": "Table Grid", "tables": "all", "properties": {"cant_split": True}, "existing": "replace"}, "requires a template"),
])
def test_missing_ambiguous_or_mistyped_inputs_are_refused(name, values, match):
    with pytest.raises(WorkflowError, match=match):
        build_workflow(name, values)


def test_missing_numbering_scope_and_incomplete_mapping_never_choose_for_the_user(documents):
    _, template = documents
    with pytest.raises(WorkflowError, match="source is required"):
        build_workflow("match-numbering", {"template": str(template), "levels": ["PRT"]})
    with pytest.raises(WorkflowError, match="style_map must"):
        build_workflow("architect-styles", {"template": str(template), "style_map": [{"from": "ArchNote"}]})
