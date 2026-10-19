"""Live-run regressions: placeholder decoration and source-bound, verbatim per-file fills."""
import copy
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from lxml import etree

from make_fixtures import TEMPLATE_FILLS, build, build_template
from spec_surgeon.compiler import _Batch, _dry_errors
from spec_surgeon.editspec import apply_spec, load_spec, plan_spec, validate_spec
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops.slots import extract_fill
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.verify import run_suite as _run_suite
from spec_surgeon.verify.reader import VPackage
from spec_surgeon.verify.template import fill_value


def run_suite(source, output, plan):
    return _run_suite(source, output, plan, "source.docx", "output.docx")


def replace_xml(data, part, edit):
    source = zipfile.ZipFile(io.BytesIO(data))
    root = etree.fromstring(source.read(part))
    edit(root)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for member in source.infolist():
            archive.writestr(member, serialize_xml(root) if member.filename == part else source.read(member.filename))
    return out.getvalue()


def template_params(path, **options):
    return {"template": str(path), "template_sections": {"cover": 0, "body": 1},
            "slot_fills": copy.deepcopy(TEMPLATE_FILLS), **options}


@pytest.mark.parametrize("policy", ["clear", "keep"])
def test_filled_slot_decoration_and_independent_tamper_check(tmp_path, policy):
    # The distributed specimen is exercised too: the defect is decoration on its existing highlighted slot.
    template = Path(__file__).parents[1] / "examples/templates/00_00_00_TEMPLATE.docx"
    def red_slot(root):
        run = next(r for r in root.iter(W + "r") if r.find(f"{W}rPr/{W}highlight") is not None)
        rpr = run.find(W + "rPr")
        etree.SubElement(rpr, W + "color").set(W + "val", "FF0000")
        etree.SubElement(rpr, W + "shd").set(W + "fill", "FFFF00")
    decorated = tmp_path / "template.docx"
    decorated.write_bytes(replace_xml(template.read_bytes(), "word/header1.xml", red_slot))
    src = build("21 05 00", "Fire Suppression")
    pkg = DocxPackage(src)
    fragment = get_op("apply_header_footer_template").plan(pkg, {}, template_params(decorated, slot_formatting=policy))
    plan = merge_fragments([fragment]).to_dict()
    work = pkg.copy()
    get_op("apply_header_footer_template").apply(work, fragment)
    output = work.output_bytes()
    record = run_suite(src, output, plan)
    assert record.passed, record.to_markdown()
    part = next(a["part"] for a in plan["property_assertions"]
                if a["kind"] == "template_parts" and a["template_part"] == "word/header1.xml")
    def filled(root):
        return next(r for r in root.iter(W + "r")
                    if "".join(t.text or "" for t in r.findall(W + "t")) == TEMPLATE_FILLS["ISSUED FOR PERMIT"]["value"])
    rpr = filled(DocxPackage(output).xml(part)).find(W + "rPr")
    if policy == "clear":
        assert rpr.find(W + "highlight") is None and rpr.find(W + "shd") is None
        assert rpr.find(W + "color").get(W + "val") != "FF0000"
    else:
        assert rpr.find(W + "highlight") is not None and rpr.find(W + "shd") is not None
        assert rpr.find(W + "color").get(W + "val") == "FF0000"
    def wrong_colour(root):
        rpr = filled(root).find(W + "rPr")
        colour = rpr.find(W + "color")
        if colour is None:
            colour = etree.SubElement(rpr, W + "color")
        colour.set(W + "val", "FF0000" if policy == "clear" else "000000")
    damaged = replace_xml(output, part, wrong_colour)
    assert run_suite(src, damaged, plan).get("V10").status == "FAIL"


def bound_spec(source, template):
    fills = copy.deepcopy(TEMPLATE_FILLS)
    del fills["SECTION_TITLE"]
    return {"request": "Use this template", "summary": "Bound title from old footer", "mode": "untracked",
            "bindings": [{"file": str(source.resolve()), "md5": hashlib.md5(source.read_bytes()).hexdigest(),
                          "slots": {"SECTION_TITLE": {"value": "Fire\u00a0 Suppression", "part": "word/footer1.xml", "paragraph": 0}}}],
            "ops": [{"op": "apply_header_footer_template", "params": {**template_params(template), "slot_fills": fills}}]}


def test_binding_overrides_rules_and_verifier_rechecks_provenance_and_identity(tmp_path):
    template = tmp_path / "template.docx"
    template.write_bytes(build_template())
    source = tmp_path / "source.docx"
    def add_title(root):
        paragraph = next(root.iter(W + "p"))
        run = etree.SubElement(paragraph, W + "r")
        etree.SubElement(run, W + "t").text = "Fire\u00a0 Suppression"
    source.write_bytes(replace_xml(build("21 05 00", "BODY TITLE"), "word/footer1.xml", add_title))
    spec = bound_spec(source, template)
    pkg = DocxPackage.from_path(source)
    plan, fragments = plan_spec(pkg, spec)
    work = apply_spec(pkg, fragments)
    output, predicted = work.output_bytes(), plan.to_dict()
    assert run_suite(pkg.data, output, predicted).passed
    for change in ({"value": "Fire Suppression"}, {"paragraph": 1}, {"part": "word/document.xml"}, {"source_md5": "0" * 32}):
        tampered = copy.deepcopy(predicted)
        for assertion in tampered["property_assertions"]:
            if assertion["kind"] == "template_parts":
                assertion["fills"]["SECTION_TITLE"].update(change)
        assert run_suite(pkg.data, output, tampered).get("V10").status == "FAIL", change
    for change in ({"value": "Fire Suppression"}, {"paragraph": 1}, {"part": "word/document.xml"}):
        wrong = copy.deepcopy(spec)
        wrong["bindings"][0]["slots"]["SECTION_TITLE"].update(change)
        with pytest.raises(JobException, match="does not occur verbatim"):
            plan_spec(pkg, wrong)
    stale = copy.deepcopy(spec)
    stale["bindings"][0]["md5"] = "0" * 32
    with pytest.raises(JobException, match="bound source changed"):
        plan_spec(pkg, stale)
    # A same-named file elsewhere is not this source, and has no title fallback.
    with pytest.raises(JobException, match="no fill rule"):
        plan_spec(DocxPackage(pkg.data, name=str(tmp_path / "elsewhere/source.docx")), spec)


def test_compiler_checks_each_bound_file_and_refuses_outside_sources(tmp_path):
    template = tmp_path / "template.docx"
    template.write_bytes(build_template())
    first, second = tmp_path / "first.docx", tmp_path / "second.docx"
    for source in (first, second):
        source.write_bytes(build("21 05 00", "Fire\u00a0 Suppression"))
    batch = _Batch([first, second], template)
    spec = bound_spec(first, template)
    spec["bindings"][0]["slots"]["SECTION_TITLE"]["part"] = "word/document.xml"
    spec["bindings"][0]["slots"]["SECTION_TITLE"]["paragraph"] = 1
    other = copy.deepcopy(spec["bindings"][0])
    other["file"] = str(second)
    other["slots"]["SECTION_TITLE"]["value"] = "WRONG TITLE"
    spec["bindings"].append(other)
    assert "second.docx" in " ".join(_dry_errors(spec, batch))
    other["file"] = str(tmp_path / "elsewhere/second.docx")
    assert "outside this batch" in " ".join(_dry_errors(spec, batch))


def test_source_binding_remains_formatting_mode_with_reference_template_rules(tmp_path):
    from make_fixtures import build_reference_spec
    source, template = tmp_path / "source.docx", tmp_path / "template.docx"
    source.write_bytes(build("21 05 00", "Fire\u00a0 Suppression"))
    template.write_bytes(build_reference_spec())
    spec = load_spec(Path(__file__).parents[1] / "examples/specs/match-template-spec-header-footer.json")
    spec["ops"][0]["params"]["template"] = str(template)
    spec["bindings"] = [{"file": str(source), "md5": hashlib.md5(source.read_bytes()).hexdigest(),
                         "slots": {"SECTION_TITLE": {"value": "Fire\u00a0 Suppression", "part": "word/document.xml", "paragraph": 1}}}]
    pkg = DocxPackage.from_path(source)
    plan, fragments = plan_spec(pkg, spec)
    assert plan.mode == "formatting"
    assert run_suite(pkg.data, apply_spec(pkg, fragments).output_bytes(), plan.to_dict()).passed


@pytest.mark.parametrize("how", ["regex", "paragraph_after"])
def test_case_and_flexible_matching_preserve_source_characters(how):
    src = build("21 05 00", "Fire\u00a0  Suppression")
    rule = {"rule": how, "case_insensitive": True, "flexible_whitespace": True}
    if how == "regex":
        rule["pattern"] = r"^(Fire Suppression)$"
    else:
        rule["anchor_regex"] = r"^section 21 05 00$"
    expected = "Fire\u00a0  Suppression"
    assert extract_fill(DocxPackage(src), "TITLE", rule) == expected
    assert fill_value(VPackage(src), "TITLE", rule) == expected
    for key in ("case_insensitive", "flexible_whitespace"):
        with pytest.raises(JobException, match="boolean"):
            extract_fill(DocxPackage(src), "TITLE", {**rule, key: "true"})


def test_template_alias_is_readable_by_all_package_inspection_tools(tmp_path):
    source, template = tmp_path / "source.docx", tmp_path / "template.docx"
    source.write_bytes(build("21 05 00", "FIRE SUPPRESSION"))
    template.write_bytes(build_template())
    batch = _Batch([source], template)
    assert batch.pkg("template").data == template.read_bytes()
    assert batch.list_parts("template")["total"] > 0
    assert "document" in batch.read_xml_page("template")["xml"]
    assert batch.show_part_text("template", "word/document.xml")
    batch.artifact_dir = tmp_path / "artifacts"
    analysis = batch.run_analysis("def analyze(documents, inputs):\n    return documents[0]['label']", files=["template"])
    assert json.loads(analysis["result_json"])["result"] == "template"
    assert batch.summary()["binding_sources"] == [{"file": "source.docx", "path": str(source),
                                                  "md5": hashlib.md5(source.read_bytes()).hexdigest()}]


@pytest.mark.parametrize("bindings", [None, {}, [{"file": "relative.docx", "md5": "0" * 32, "slots": {}}]])
def test_binding_schema_refuses_ill_typed_or_unidentified_sources(bindings):
    with pytest.raises(JobException):
        validate_spec({"request": "x", "summary": "x", "mode": "formatting", "bindings": bindings,
                       "ops": [{"op": "set_font", "params": {"font": "Arial"}}]})
