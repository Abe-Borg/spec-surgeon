"""A template spec (e.g. the architect's) attached to a request: what read_template tells the compiler about it, the
two worked examples ("make the header and footer match the template spec", "match the font of the template spec")
through the batch, and the compiler loop with a scripted client (no API calls)."""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from make_fixtures import build, build_reference_spec, build_sized, build_template
from spec_surgeon.batch import run_batch
from spec_surgeon.catalog import REFERENCE_FILLS, op_catalog
from spec_surgeon.compiler import compile_request
from spec_surgeon.editspec import load_spec
from spec_surgeon.package import DocxPackage
from spec_surgeon.profile import profile, template_spec_summary

SPECS = Path(__file__).resolve().parent.parent / "examples" / "specs"
HF_SPEC = SPECS / "match-template-spec-header-footer.json"
FONT_SPEC = SPECS / "match-template-spec-font.json"


def tool(name, inp, tid="t1"):
    return SimpleNamespace(type="tool_use", id=tid, name=name, input=inp)


def resp(*blocks, stop="tool_use"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop, stop_details=None,
                           usage=SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=0))


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.requests.append(copy.deepcopy({k: v for k, v in kw.items() if k != "system"}) | {"system": kw.get("system")})
        return self.responses.pop(0)


@pytest.fixture
def folder(tmp_path) -> Path:
    d = tmp_path / "specs"
    d.mkdir()
    (d / "21 05 00 – COMMON WORK.docx").write_bytes(build("21 05 00", "COMMON WORK RESULTS FOR FIRE SUPPRESSION"))
    (d / "21 13 13 – WET-PIPE.docx").write_bytes(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS", footer_revision=True))
    (d / "21 13 16 – DRY-PIPE.docx").write_bytes(build("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS", side_margin_twips=2160))
    return d


# ---- what the compiler is told ----------------------------------------------------------------------

def test_summary_of_a_template_spec_names_its_own_values_and_where_they_show():
    s = template_spec_summary(DocxPackage(build_reference_spec()))
    assert "reference" in s["kind"]
    assert s["own_values"] == {"SECTION_NUMBER": "08 71 00", "SECTION_TITLE": "DOOR HARDWARE"}
    assert s["shown_in_headers_footers"] == ["word/header1.xml: «SECTION ⟦08 71 00⟧ – ⟦DOOR HARDWARE⟧»",
                                             "word/footer1.xml: «⟦DOOR HARDWARE⟧ ⟦08 71 00⟧ - 1»"]
    assert s["body_text"] == [{"font": "Arial", "size_pt": 10.0, "share": 1.0}] and "problems" not in s


def test_summary_reports_what_reference_slots_would_refuse():
    s = template_spec_summary(DocxPackage(build_reference_spec(title_by_field=True, extra_header_text="Ref 087100")))
    codes = sorted(p.split("]")[0].strip("[") for p in s["problems"])
    assert codes == ["slot-in-field", "template-value-left"]


def test_summary_of_a_prepared_template_lists_its_slots():
    s = template_spec_summary(DocxPackage(build_template()))
    assert "prepared" in s["kind"] and "SECTION_TITLE" in s["prepared_slots"] and "own_values" not in s


def test_every_profile_says_what_the_body_text_is_set_in():
    fonts = profile(DocxPackage(build_sized()))["fonts"]["body_text"]
    assert fonts[0] == {"font": "Century Gothic", "size_pt": 9.0, "share": 0.65}
    assert {f["size_pt"] for f in fonts} == {9.0, 8.0, 12.0}


def test_the_catalog_carries_the_reference_rules_and_the_examples():
    cat = op_catalog()
    assert cat["reference_fills"] == REFERENCE_FILLS and "in_template" in cat["fill_rules"]
    assert {"make the header and footer match the template spec", "match the font of the template spec"} <= \
        {e["request"] for e in cat["worked_examples"]}
    assert "size" in cat["ops"]["set_font"]["params"]["properties"]
    assert "reference" in cat["ops"]["apply_header_footer_template"]["params"]["properties"]["slot_markers"]["items"]["enum"]


# ---- the worked examples through the batch -----------------------------------------------------------

def test_header_footer_example_runs_in_a_batch_and_skips_the_template_spec(folder, tmp_path):
    spec = load_spec(HF_SPEC)
    tpl = Path(spec["ops"][0]["params"]["template"])
    assert tpl.is_file() and tpl.name == "08 71 00 - DOOR HARDWARE (ACME).docx"
    shutil.copy(tpl, folder / "08 71 00 – DOOR HARDWARE (architect).docx")      # in the project folder: skipped
    batch = run_batch([str(folder)], "edit-spec", {}, tmp_path / "out", workers=1, renderer="none", do_render=False,
                      spec=spec)
    assert len(batch.files) == 3 and len(batch.skipped) == 1
    for f in batch.files:
        assert f.status == "PASS", f.record.to_markdown() if f.record else f.exception
    out = DocxPackage.from_path(Path(batch.files[0].delivered_to))
    texts = [t for p in out.members if p.filename.startswith("word/header") and p.filename.endswith(".xml")
             for t in ["".join(x.text or "" for x in out.xml(p.filename).iter("{%s}t" % "http://schemas.openxmlformats.org/wordprocessingml/2006/main"))]]
    assert any("SECTION 21 05 00 – COMMON WORK RESULTS FOR FIRE SUPPRESSION" in t for t in texts)


def test_font_example_runs_in_a_batch(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    (src / "21 13 13 – WET-PIPE.docx").write_bytes(build_sized())
    (src / "21 13 16 – DRY-PIPE.docx").write_bytes(build_sized("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS"))
    batch = run_batch([str(src)], "edit-spec", {}, tmp_path / "out", workers=1, renderer="none", do_render=False,
                      spec=load_spec(FONT_SPEC))
    assert len(batch.files) == 2
    for f in batch.files:
        assert f.status == "PASS", f.record.to_markdown() if f.record else f.exception


# ---- the compiler ------------------------------------------------------------------------------------

def test_compiler_reads_the_template_spec_and_submits_a_reference_spec(folder, tmp_path):
    tpl = tmp_path / "08 71 00 – DOOR HARDWARE.docx"
    tpl.write_bytes(build_reference_spec())
    spec = load_spec(HF_SPEC)
    spec["ops"][0]["params"]["template"] = str(tpl)
    fake = FakeClient(resp(tool("read_template", {})), resp(tool("submit_editspec", {"spec": spec}, "t2")))
    res = compile_request("make the header and footer match the architect's spec", sorted(folder.glob("*.docx")),
                          template=tpl, client=fake)
    assert res.status == "ok", res.errors
    first = fake.requests[0]
    assert "template spec" in first["messages"][0]["content"] and "reference_fills" in first["system"]
    got = json.loads(fake.requests[1]["messages"][-1]["content"][0]["content"])
    assert got["template_spec"]["own_values"]["SECTION_NUMBER"] == "08 71 00"
    assert any("⟦DOOR HARDWARE⟧" in x for x in got["template_spec"]["shown_in_headers_footers"])


def test_a_rule_that_finds_nothing_in_the_template_spec_is_a_compile_error(folder, tmp_path):
    tpl = tmp_path / "arch.docx"
    tpl.write_bytes(build_reference_spec())
    spec = load_spec(HF_SPEC)
    spec["ops"][0]["params"]["template"] = str(tpl)
    bad = copy.deepcopy(spec)
    bad["ops"][0]["params"]["slot_fills"]["SECTION_NUMBER"]["pattern"] = r"^SECTION\s+(\d{6})\b"
    fake = FakeClient(resp(tool("submit_editspec", {"spec": bad})), resp(tool("submit_editspec", {"spec": spec}, "t2")))
    res = compile_request("make the header and footer match the template spec", sorted(folder.glob("*.docx")),
                          template=tpl, client=fake)
    assert res.status == "ok", res.errors
    rejected = fake.requests[1]["messages"][-1]["content"][0]
    assert rejected["is_error"] and "[template-slot]" in rejected["content"]
