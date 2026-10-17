"""Original/template/final graph verification, without relying on a transform plan."""
from __future__ import annotations

import copy
import hashlib
import io
import zipfile

import pytest
from lxml import etree

from make_fixtures import build, build_template
from test_template import params, run
from spec_surgeon.verify.goal_templates import verify_template_effect
from spec_surgeon.verify.reader import CT, REL, VPackage, W


@pytest.fixture
def template_result(tmp_path):
    template = tmp_path / "template.docx"
    template.write_bytes(build_template())
    source = build("21 05 00", "COMMON WORK RESULTS FOR FIRE SUPPRESSION")
    values = params(template)
    _, output, record = run(source, values)
    assert record.passed, record.to_markdown()
    effect = {"id": "branding", "type": "header_footer_template", "template": str(template),
              "template_sha256": hashlib.sha256(template.read_bytes()).hexdigest(),
              "params": {k: v for k, v in values.items() if k != "template"}}
    return source, output, effect


def _rewrite(data, member, change):
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as old, zipfile.ZipFile(buffer, "w") as new:
        for entry in old.infolist():
            content = old.read(entry)
            if entry.filename == member:
                content = change(content)
            new.writestr(entry, content)
    return buffer.getvalue()


def _xml_change(change):
    def apply(data):
        root = etree.fromstring(data)
        change(root)
        return etree.tostring(root, encoding="UTF-8", xml_declaration=True)
    return apply


def _verify(case, output=None):
    source, original_output, effect = case
    src, out = VPackage(source), VPackage(output or original_output)
    try:
        return verify_template_effect(src, out, effect)
    finally:
        src.zip.close()
        out.zip.close()


def test_template_contract_derives_exact_graph_permissions(template_result):
    result = _verify(template_result)
    assert result["errors"] == []
    source, output, _ = template_result
    with zipfile.ZipFile(io.BytesIO(source)) as before, zipfile.ZipFile(io.BytesIO(output)) as after:
        assert set(result["allowed_added"]) == set(after.namelist()) - set(before.namelist())
        assert set(result["allowed_removed"]) == set(before.namelist()) - set(after.namelist())
    assert result["style_ids"] == ["HFStamp"]


@pytest.mark.parametrize("member,change,reason", [
    ("word/media/image1.png", lambda data: data + b"tampered", "image bytes"),
    ("word/_rels/header2.xml.rels", _xml_change(
        lambda root: root[0].set("Type", "https://example.test/unapproved")), "relationship changed"),
    ("word/_rels/document.xml.rels", _xml_change(
        lambda root: next(el for el in root if el.get("Type", "").endswith("/styles")).set(
            "Target", "theme/theme1.xml")), "styles relationship"),
    ("[Content_Types].xml", _xml_change(
        lambda root: next(el for el in root if el.tag == CT + "Default").set(
            "ContentType", "application/x-unapproved")), "content-type changes"),
    ("word/styles.xml", _xml_change(
        lambda root: root.find(W + "style").set(W + "customStyle", "1")), "preexisting styles"),
    ("word/header2.xml", _xml_change(
        lambda root: setattr(next(root.iter(W + "t")), "text", "INVENTED PROJECT")), "text"),
])
def test_template_graph_tampering_is_rejected(template_result, member, change, reason):
    output = _rewrite(template_result[1], member, change)
    errors = _verify(template_result, output)["errors"]
    assert errors, reason
    assert any(reason in error for error in errors), errors


def test_template_pin_is_checked_again_at_verification(template_result):
    from pathlib import Path
    Path(template_result[2]["template"]).write_bytes(build_template() + b"changed")
    assert "approved template changed" in " ".join(_verify(template_result)["errors"])


def test_unused_approved_fill_cannot_be_silently_ignored(template_result):
    source, output, effect = template_result
    effect = copy.deepcopy(effect)
    effect["params"]["slot_fills"]["UNUSED"] = {"rule": "literal", "value": "Required wording"}
    errors = _verify((source, output, effect))["errors"]
    assert any("unused" in error and "UNUSED" in error for error in errors), errors


def test_fill_without_any_selected_template_parts_is_refused(tmp_path):
    from docx import Document
    template = tmp_path / "empty-template.docx"
    Document().save(template)
    source = build("21 05 00", "COMMON WORK RESULTS FOR FIRE SUPPRESSION")
    values = {"template": str(template), "template_sections": {"all": 0}, "slot_fills": {}}
    _, output, record = run(source, values)
    assert record.passed, record.to_markdown()
    effect = {"id": "branding", "type": "header_footer_template", "template": str(template),
              "template_sha256": hashlib.sha256(template.read_bytes()).hexdigest(),
              "params": {"template_sections": {"all": 0}, "slot_fills": {
                  "UNUSED": {"rule": "literal", "value": "Required wording"}}}}
    errors = _verify((source, output, effect))["errors"]
    assert any("unused" in error and "UNUSED" in error for error in errors), errors


def test_duplicate_imported_style_id_cannot_hide_a_second_definition(template_result):
    def duplicate(root):
        definition = next(style for style in root.findall(W + "style") if style.get(W + "styleId") == "HFStamp")
        conflicting = copy.deepcopy(definition)
        conflicting.set(W + "customStyle", "1")
        root.insert(root.index(definition), conflicting)
    output = _rewrite(template_result[1], "word/styles.xml", _xml_change(duplicate))
    errors = _verify(template_result, output)["errors"]
    assert any("exactly once" in error and "HFStamp" in error for error in errors), errors


@pytest.mark.parametrize("reference", ["rStyle", "numStyleLink", "styleLink"])
def test_style_import_cannot_rebind_original_numbering_references(template_result, reference):
    def dangling(root):
        container = root.find(W + "abstractNum")
        etree.SubElement(container, W + reference).set(W + "val", "HFStamp")
    source, output, effect = template_result
    source = _rewrite(source, "word/numbering.xml", _xml_change(dangling))
    output = _rewrite(output, "word/numbering.xml", _xml_change(dangling))
    errors = _verify((source, output, effect))["errors"]
    assert any("original style reference" in error and "numbering" in error for error in errors), errors


def test_missing_source_margins_do_not_make_template_distance_optional(template_result):
    def remove_margins(root):
        for section in root.iter(W + "sectPr"):
            margin = section.find(W + "pgMar")
            if margin is not None:
                section.remove(margin)
    source, output, effect = template_result
    source = _rewrite(source, "word/document.xml", _xml_change(remove_margins))
    output = _rewrite(output, "word/document.xml", _xml_change(remove_margins))
    errors = _verify((source, output, effect))["errors"]
    assert any("distance differs" in error for error in errors), errors
