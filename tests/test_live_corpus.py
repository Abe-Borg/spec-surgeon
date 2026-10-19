"""Synthetic metadata tests for the private-corpus loader and delivery oracle.

These validate the harness itself and do not count as real-document acceptance.
"""
import json
from types import SimpleNamespace

import pytest

from real.corpus import LiveRun, assert_delivered, load_corpus, load_recipe


def manifest_fixture(tmp_path):
    sources = []
    for index in range(12):
        path = tmp_path / f"metadata-only-source-{index}.docx"
        path.touch()
        sources.append(path.name)
    (tmp_path / "template.docx").touch()
    for name in ("1418.json", "1521.json"):
        (tmp_path / name).write_text("{}")
    return {"sources": sources, "template": "template.docx", "runs": [
        {"id": "1418", "recipe": "1418.json", "expected_files": 12},
        {"id": "1521", "recipe": "1521.json", "expected_files": 12}]}


@pytest.mark.parametrize("change,reason", [
    (lambda value: value["sources"].pop(), "12 distinct"),
    (lambda value: value["sources"].__setitem__(0, value["sources"][1]), "12 distinct"),
    (lambda value: value["runs"][0].__setitem__("expected_files", 11), "expecting 12"),
    (lambda value: value["runs"][1].__setitem__("id", "1418"), "both live session"),
    (lambda value: value.__setitem__("template", value["sources"][0]), "separate"),
    (lambda value: value["sources"].__setitem__(0, "../outside.docx"), "inside its folder"),
])
def test_private_corpus_misconfiguration_cannot_silently_reduce_acceptance(tmp_path, change, reason):
    value = manifest_fixture(tmp_path)
    change(value)
    (tmp_path / "manifest.json").write_text(json.dumps(value))
    with pytest.raises(ValueError, match=reason):
        load_corpus(tmp_path)


def test_manifest_loads_both_exact_saved_sessions(tmp_path):
    value = manifest_fixture(tmp_path)
    (tmp_path / "manifest.json").write_text(json.dumps(value))
    corpus = load_corpus(tmp_path)
    assert len(corpus.sources) == 12 and {run.id for run in corpus.runs} == {"1418", "1521"}


def test_template_relocation_changes_only_input_paths_not_literal_request_words(tmp_path):
    template = tmp_path / "owner.docx"
    recipe = tmp_path / "recipe.json"
    recipe.write_text(json.dumps({"request": "Use $TEMPLATE literally in this test request", "summary": "Use template",
        "mode": "untracked", "ops": [{"op": "apply_header_footer_template", "select": {"sections": "all"},
                                        "params": {"template": "$TEMPLATE", "template_sections": {"cover": 0, "body": 0},
                                                   "slot_fills": {}}}]}))
    value = load_recipe(LiveRun("1418", recipe, 12), template)
    assert value["ops"][0]["params"]["template"] == str(template)
    assert "$TEMPLATE" in value["request"]


def test_delivery_oracle_refuses_withheld_or_quarantined_files(tmp_path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    for index in range(12):
        (outputs / f"{index}.docx").touch()
    record = SimpleNamespace(passed=True, checks=[])
    result = SimpleNamespace(out_dir=tmp_path, files=[SimpleNamespace(status="PASS", record=record) for _ in range(12)])
    assert_delivered(result, 12)
    (outputs / "0.docx").unlink()
    with pytest.raises(AssertionError, match="withheld"):
        assert_delivered(result, 12)
    (outputs / "0.docx").touch()
    failed = tmp_path / "stage-01" / "failed"
    failed.mkdir(parents=True)
    (failed / "wrong.docx").touch()
    with pytest.raises(AssertionError, match="quarantined"):
        assert_delivered(result, 12)
