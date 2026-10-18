"""Localized Word style identities and hostile collisions, through both verifiers.

Synthetic fixtures only: no client document is stored in the repository.
"""
import copy
import hashlib
import io
import zipfile

import pytest
from lxml import etree

from make_fixtures import build, build_template
from test_template import run, params, mutate
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.verify import run_suite
from spec_surgeon.verify.goal_templates import verify_template_effect
from spec_surgeon.verify.reader import VPackage

SPANISH = {"Header": "Encabezado", "Footer": "Piedepgina", "HeaderChar": "EncabezadoCar",
           "FooterChar": "PiedepginaCar", "DefaultParagraphFont": "Fuentedeprrafopredeter"}
REFERENCES = {W + n for n in ("pStyle", "rStyle", "tblStyle", "basedOn", "next", "link")}


def renamed(data, mapping):
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as old, zipfile.ZipFile(buf, "w") as new:
        for info in old.infolist():
            content = old.read(info)
            if info.filename.startswith("word/") and info.filename.endswith(".xml"):
                root = etree.fromstring(content)
                for el in root.iter():
                    if el.tag == W + "style" and el.get(W + "styleId") in mapping:
                        original = el.get(W + "styleId")
                        el.set(W + "styleId", mapping[original])
                        if original.endswith("Char"):
                            el.find(W + "name").set(W + "val", mapping[original] + " localized name")
                    elif el.tag in REFERENCES and el.get(W + "val") in mapping:
                        el.set(W + "val", mapping[el.get(W + "val")])
                content = serialize_xml(root)
            new.writestr(info, content)
    return buf.getvalue()


def effect(template, values):
    return {"id": "branding", "type": "header_footer_template", "template": str(template),
            "template_sha256": hashlib.sha256(template.read_bytes()).hexdigest(),
            "params": {k: v for k, v in values.items() if k != "template"}}


def check_goal(source, output, template, values):
    src, out = VPackage(source), VPackage(output)
    try:
        result = verify_template_effect(src, out, effect(template, values))
        assert not result["errors"], result["errors"]
    finally:
        src.zip.close()
        out.zip.close()


def setup_case(tmp_path, mapping=SPANISH):
    template = tmp_path / "localized-template.docx"
    data = build_template()
    # An explicitly used linked character style, plus a custom style that reaches
    # the default character style, exercises more than just Header -> Header.
    def character(root):
        run = next(root.iter(W + "r"))
        rpr = etree.Element(W + "rPr")
        etree.SubElement(rpr, W + "rStyle").set(W + "val", "HeaderChar")
        run.insert(0, rpr)
    data = mutate(data, "word/header1.xml", character)
    def dependency(root):
        stamp = next(s for s in root if s.get(W + "styleId") == "HFStamp")
        base = etree.Element(W + "basedOn", {W + "val": "DefaultParagraphFont"})
        stamp.insert(1, base)
    data = mutate(data, "word/styles.xml", dependency)
    template.write_bytes(renamed(data, mapping))
    source = build("21 05 00", "COMMON WORK RESULTS FOR FIRE SUPPRESSION")
    return template, source, params(template)


@pytest.mark.parametrize("mapping", [SPANISH, {key: "Locale" + str(i) for i, key in enumerate(SPANISH)}])
def test_localized_ids_and_linked_names_work_without_rebuilding_template(tmp_path, mapping):
    template, source, values = setup_case(tmp_path, mapping)
    before = template.read_bytes()
    plan, output, record = run(source, values)
    assert record.passed, record.to_markdown()
    check_goal(source, output, template, values)
    assert template.read_bytes() == before
    pkg = DocxPackage(output)
    styles = pkg.xml("word/styles.xml")
    defaults = [s.get(W + "styleId") for s in styles if s.get(W + "type") == "character" and s.get(W + "default") == "1"]
    assert defaults == ["DefaultParagraphFont"]
    stamp = next(s for s in styles if s.get(W + "styleId") == "HFStamp")
    assert stamp.find(W + "basedOn").get(W + "val") == "DefaultParagraphFont"
    assert any("reconciled" in note for note in plan.notes)
    # Every preexisting style is identical; font differences belong on copied runs.
    original = DocxPackage(source).xml("word/styles.xml")
    for old in original.findall(W + "style"):
        new = next(s for s in styles if s.get(W + "styleId") == old.get(W + "styleId"))
        assert etree.tostring(old, method="c14n") == etree.tostring(new, method="c14n")


def test_matching_works_in_reverse_with_localized_destination(tmp_path):
    template, source, values = setup_case(tmp_path, {})
    source = renamed(source, SPANISH)
    _, output, record = run(source, values)
    assert record.passed, record.to_markdown()
    check_goal(source, output, template, values)


def test_equivalent_styles_need_no_import_even_when_option_is_off(tmp_path):
    template, source, values = setup_case(tmp_path)
    # Use the custom stamp already installed, so every used style has an equivalent.
    def add_stamp(root):
        tpl = DocxPackage(template.read_bytes()).xml("word/styles.xml")
        st = copy.deepcopy(next(s for s in tpl if s.get(W + "styleId") == "HFStamp"))
        st.find(W + "basedOn").set(W + "val", "DefaultParagraphFont")
        root.append(st)
    source = mutate(source, "word/styles.xml", add_stamp)
    values["import_missing_styles"] = False
    plan, output, record = run(source, values)
    assert record.passed, record.to_markdown()
    check_goal(source, output, template, values)
    assert "word/styles.xml" not in plan.intended_parts


@pytest.mark.parametrize("collision", ["different_type", "different_name", "dangling_body", "duplicate_names"])
def test_collisions_are_isolated_without_changing_original_references(tmp_path, collision):
    template, source, values = setup_case(tmp_path)
    def clash(root):
        if collision in ("different_type", "different_name"):
            st = etree.SubElement(root, W + "style", {W + "styleId": "HFStamp", W + "type":
                                      "paragraph" if collision == "different_type" else "character"})
            etree.SubElement(st, W + "name").set(W + "val", "Existing unrelated style")
        elif collision == "duplicate_names":
            for sid in ("A", "B"):
                st = etree.SubElement(root, W + "style", {W + "styleId": sid, W + "type": "character"})
                etree.SubElement(st, W + "name").set(W + "val", "HFStamp")
    source = mutate(source, "word/styles.xml", clash)
    if collision == "dangling_body":
        def dangling(root):
            run = next(root.iter(W + "r"))
            rpr = etree.Element(W + "rPr")
            etree.SubElement(rpr, W + "rStyle").set(W + "val", "HFStamp")
            run.insert(0, rpr)
        source = mutate(source, "word/document.xml", dangling)
    _, output, record = run(source, values)
    assert record.passed, record.to_markdown()
    check_goal(source, output, template, values)
    out = DocxPackage(output)
    expected_id = "HFStamp" if collision == "duplicate_names" else "TemplateHFStamp"
    assert any(s.get(W + "styleId") == expected_id for s in out.xml("word/styles.xml"))


def test_missing_destination_default_is_imported_as_private_nondefault(tmp_path):
    template, source, values = setup_case(tmp_path)
    def no_default(root):
        for st in list(root):
            if st.get(W + "styleId") == "DefaultParagraphFont":
                root.remove(st)
    source = mutate(source, "word/styles.xml", no_default)
    _, output, record = run(source, values)
    assert record.passed, record.to_markdown()
    check_goal(source, output, template, values)
    assert not any(s.get(W + "type") == "character" and s.get(W + "default") == "1"
                   for s in DocxPackage(output).xml("word/styles.xml"))


def test_forged_mapping_shared_by_plan_and_transform_is_independently_rejected(tmp_path, monkeypatch):
    template, source, values = setup_case(tmp_path)
    import spec_surgeon.ops.apply_header_footer_template as module
    real = module.reconcile_styles
    def wrong(*args):
        mapping, imports = real(*args)
        mapping["Encabezado"] = "Footer"
        return mapping, imports
    monkeypatch.setattr(module, "reconcile_styles", wrong)
    plan, output, record = run(source, values)
    assert not record.passed
    assert "input-derived mapping" in record.get("V10").summary
    src, out = VPackage(source), VPackage(output)
    try:
        assert verify_template_effect(src, out, effect(template, values))["errors"]
    finally:
        src.zip.close()
        out.zip.close()


def test_extra_default_style_is_not_accepted_as_a_workaround(tmp_path):
    template, source, values = setup_case(tmp_path)
    plan, output, record = run(source, values)
    assert record.passed
    def extra(root):
        st = etree.SubElement(root, W + "style", {W + "styleId": "Fuentedeprrafopredeter",
                              W + "type": "character", W + "default": "1"})
        etree.SubElement(st, W + "name").set(W + "val", "Default Paragraph Font")
    tampered = mutate(output, "word/styles.xml", extra)
    bad = run_suite(source, tampered, plan.to_dict(), "source", "output")
    assert not bad.passed and bad.get("V10").status == "FAIL"


def test_assertions_cannot_authorize_styles_from_uncopied_parts(tmp_path):
    template, source, values = setup_case(tmp_path)
    plan, output, record = run(source, values)
    assert record.passed
    forged = plan.to_dict()
    for assertion in forged["property_assertions"]:
        if assertion["kind"] in ("template_parts", "styles_added"):
            assertion["style_parts"] = assertion["style_parts"] + ["word/document.xml"]
    bad = run_suite(source, output, forged, "source", "output")
    assert not bad.passed and "style dependency roots" in bad.get("V10").summary


def test_agent_preflight_uses_current_template_and_source_pins(tmp_path):
    from spec_surgeon.compiler import _Batch
    template, source, values = setup_case(tmp_path)
    path = tmp_path / "target.docx"
    path.write_bytes(source)
    batch = _Batch([path], template)
    result = batch.inspect_template_styles(path.name)
    assert result["status"] == "compatible"
    assert result["style_map"]["Encabezado"] == "Header"
    assert result["style_map"]["Fuentedeprrafopredeter"] == "DefaultParagraphFont"
    assert result["imported_ids"] == ["HFStamp"]
    with pytest.raises((ValueError, KeyError)):
        batch.inspect_template_styles(path.name, template_file="/unlisted.docx")
    with pytest.raises(ValueError, match="parts"):
        batch.inspect_template_styles(path.name, parts=["word/document.xml"])
    template.write_bytes(template.read_bytes() + b"drift")
    with pytest.raises(ValueError, match="changed"):
        batch.inspect_template_styles(path.name)


def test_normal_chat_compiles_original_localized_template_without_user_rebuild(tmp_path):
    from fake_claude import FakeClient, resp, tool
    from spec_surgeon.conversation import Conversation
    template, source, values = setup_case(tmp_path)
    path = tmp_path / "target.docx"
    path.write_bytes(source)
    stamp = values["slot_fills"]["ISSUED FOR PERMIT"]["value"]
    request = "Apply the attached header/footer template with " + stamp
    spec = {"request": request, "summary": "Apply the original project template", "mode": "untracked",
            "ops": [{"op": "apply_header_footer_template", "select": {"sections": "all"}, "params": values}]}
    client = FakeClient(resp(tool("inspect_template_styles", {"file": path.name}, "inspect")),
                        resp(tool("submit_editspec", {"spec": spec}, "submit")))
    conversation = Conversation(request, [path], template=template, client=client)
    turn = conversation.start()
    assert turn.status == "ok", turn.errors
    assert not turn.choices and not turn.questions
    assert conversation.calls == 2
