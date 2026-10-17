"""Phase 3: document profiles, the batch planner (dry run), the op catalog / EditSpec schema, and their CLI."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from make_fixtures import build, build_rich, build_template
from spec_surgeon.catalog import editspec_json_schema, op_catalog
from spec_surgeon.cli import main as cli_main
from spec_surgeon.editspec import load_spec
from spec_surgeon.ops import OPS
from spec_surgeon.package import DocxPackage
from spec_surgeon.planner import plan_batch
from spec_surgeon.profile import MAX_PROFILE_BYTES, bound, profile, profile_batch

SPECS = Path(__file__).resolve().parent.parent / "examples" / "specs"


@pytest.fixture(scope="module")
def folder(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("batch")
    for i in range(3):
        (d / f"21 0{i} 00 – SPEC {i}.docx").write_bytes(build(f"21 0{i} 00", f"SPEC {i}"))
    (d / "21 13 13 – RICH.docx").write_bytes(build_rich())
    return d


def test_profile_describes_sections_parts_styles_and_fits_the_budget():
    prof = profile(DocxPackage(build_rich(), name="rich.docx"))
    assert len(json.dumps(prof, ensure_ascii=False)) <= MAX_PROFILE_BYTES and not prof.get("truncated")
    s0, s1 = prof["sections"][0], prof["sections"][1]
    assert s0["titlePg"] and s0["headers"]["first"]["part"] == "word/header1.xml"
    s2 = prof["sections"][2]
    assert s1["headers"]["even"]["own"] and s1["footers"]["default"]["part"] == s0["footers"]["default"]["part"]
    assert not any(v["own"] for k in ("headers", "footers") for v in s2[k].values())     # section 2 inherits all
    assert prof["settings"]["evenAndOddHeaders"]
    assert "EVEN PAGE HEADER" in prof["header_footer_parts"]["word/header4.xml"]["text"][0]
    styles = {s["id"]: s for s in prof["styles_in_use"]}
    assert styles["SpecNote"]["font"] == "Courier New"
    assert "Wingdings" in prof["fonts"]["direct_in_runs"]


def test_profile_of_a_template_lists_its_slots():
    prof = profile(DocxPackage(build_template(), name="T.docx"))
    parts = prof["header_footer_parts"]
    assert parts["word/header2.xml"]["slots"] == ["SECTION_NUMBER", "SECTION_TITLE", "ISSUED FOR PERMIT"]
    assert parts["word/footer1.xml"]["slots"] == ["XX XX XX"]
    assert "⇥" in parts["word/header1.xml"]["text"][0]                                 # tabs stay visible


def test_bound_always_fits_and_cuts_samples_before_structure():
    """Codex review on PR #3: the bound must hold even when structure alone exceeds it; every structural cut is
    reported in ``truncated``."""
    prof = profile(DocxPackage(build_rich(), name="rich.docx"))
    roomy = bound(json.loads(json.dumps(prof)), limit=3500)
    assert roomy["sections"] == prof["sections"] and not roomy["first_paragraphs"]          # samples went first
    # batch 4's character/table styles in use go before any section or part (they matter less than the page setup), and
    # so does batch 5's table summary (its count stays under counts)
    assert roomy["truncated"] == ["header/footer text", "character styles in use", "tables"]
    for limit in (3000, 1500, 800, 300):
        small = bound(json.loads(json.dumps(prof)), limit=limit)
        assert len(json.dumps(small, ensure_ascii=False)) <= limit, limit
    tiny = bound(json.loads(json.dumps(prof)), limit=300)
    assert set(tiny) <= {"file", "pattern_id", "settings", "counts", "truncated"} and tiny["truncated"]


def test_an_unreadable_file_is_a_record_not_an_abort(folder, tmp_path):
    """Codex review on PR #3: one truncated .docx must not stop the dry run or the profiler."""
    d = tmp_path / "with-broken"
    shutil.copytree(folder, d)
    (d / "BROKEN.docx").write_bytes(b"PK\x03\x04 not really a zip")
    bp = plan_batch([str(d)], load_spec(SPECS / "remove-all-headers-footers.json"))
    broken = [f for f in bp.files if f.source.name == "BROKEN.docx"][0]
    assert broken.status == "exception" and broken.exception["code"] == "unreadable"
    assert broken.exception["kind"] == "unreadable" and broken.exception["stage"] == "open"
    assert "damaged or cut short" in broken.exception["reason"]           # it starts like a zip, but is not one
    assert sum(f.status == "planned" for f in bp.files) == 4
    pb = profile_batch(sorted(d.glob("*.docx")))
    assert [u["file"] for u in pb["unreadable"]] == ["BROKEN.docx"] and sum(p["count"] for p in pb["patterns"]) == 4


def test_profile_batch_groups_by_pattern(folder):
    pb = profile_batch(sorted(folder.glob("*.docx")))
    assert pb["files"] == 4 and [p["count"] for p in pb["patterns"]] == [3, 1]
    assert pb["patterns"][0]["files"][0].startswith("21 00 00")


def test_plan_batch_dry_run(folder):
    before = {f: f.read_bytes() for f in folder.glob("*.docx")}
    bp = plan_batch([str(folder)], load_spec(SPECS / "apply-header-footer-template.json"))
    assert len(bp.files) == 4 and len(bp.patterns) == 2
    exc = bp.exceptions
    assert [f.source.name for f in exc] == ["21 13 13 – RICH.docx"] and exc[0].exception["code"] == "even-odd"
    ok = [f for f in bp.files if f.status == "planned"][0]
    assert any(line.startswith("members added: header2.xml") for line in ok.summary)
    assert any("literal fill" in line for line in ok.summary)
    assert {f: f.read_bytes() for f in folder.glob("*.docx")} == before                  # nothing written
    text = bp.to_text(per_file=False)
    assert "4 file(s), 3 plan cleanly, 1 exception(s)" in text and "… and 2 more file(s)" in text
    json.dumps(bp.to_dict())


def test_plan_batch_skips_the_spec_template(folder, tmp_path):
    spec = load_spec(SPECS / "apply-header-footer-template.json")
    d = tmp_path / "with-template"
    shutil.copytree(folder, d)
    shutil.copy(spec["ops"][0]["params"]["template"], d / "00 00 00 TEMPLATE.docx")
    bp = plan_batch([str(d)], spec)
    assert [Path(s).name for s in bp.skipped] == ["00 00 00 TEMPLATE.docx"] and len(bp.files) == 4


def test_catalog_and_schema_cover_every_op():
    cat = op_catalog()
    assert set(cat["ops"]) == set(OPS)
    for name, entry in cat["ops"].items():
        assert entry["description"] and entry["common_assumptions"], name
    consts = [item["properties"]["op"]["const"] for item in cat["editspec_schema"]["properties"]["ops"]["items"]["oneOf"]]
    assert sorted(consts) == sorted(OPS)
    assert {e["request"] for e in cat["worked_examples"]} >= {"change all font to Arial", "remove all headers and footers"}
    json.dumps(cat)


def test_example_specs_conform_to_the_published_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = editspec_json_schema()
    for f in SPECS.glob("*.json"):
        jsonschema.validate(json.loads(f.read_text(encoding="utf-8")), schema)
    bad = json.loads((SPECS / "remove-all-headers-footers.json").read_text(encoding="utf-8"))
    bad["ops"][0]["params"]["colour"] = "red"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


def test_cli_profile_plan_spec_and_catalog(folder, capsys):
    assert cli_main(["profile", str(folder)]) == 0
    assert "2 structure pattern(s)" in capsys.readouterr().out
    assert cli_main(["plan-spec", str(SPECS / "remove-all-headers-footers.json"), str(folder)]) == 0
    assert "4 plan cleanly" in capsys.readouterr().out
    assert cli_main(["plan-spec", str(SPECS / "apply-header-footer-template.json"), str(folder), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["exceptions"] == 1
    assert cli_main(["ops", "--catalog"]) == 0
    assert "editspec_schema" in json.loads(capsys.readouterr().out)
