"""Style identities alone cannot authorize losing inherited template formatting."""
import pytest
from lxml import etree

from test_template import mutate, run
from test_template_styles import check_goal, effect, setup_case
from spec_surgeon.ops import JobException
from spec_surgeon.ooxml import STYLE_ORDER, insert_in_order
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.verify.goal_templates import verify_template_effect
from spec_surgeon.verify.reader import VPackage


def style_properties(data, sid, xml):
    def edit(root):
        style = next(s for s in root if s.get(W + "styleId") == sid)
        prop = etree.fromstring(f'<wrapper xmlns:w="{W[1:-1]}">{xml}</wrapper>')[0]
        previous = style.find(prop.tag)
        if previous is not None:
            style.replace(previous, prop)
        else:
            insert_in_order(style, prop, STYLE_ORDER)
    return mutate(data, "word/styles.xml", edit)


@pytest.mark.parametrize("sid,xml", [
    ("Header", '<w:pPr><w:shd w:val="clear" w:fill="CCFFCC"/></w:pPr>'),
    ("Header", '<w:pPr><w:pBdr><w:bottom w:val="single" w:sz="18" w:color="FF0000"/></w:pBdr></w:pPr>'),
    ("Normal", '<w:pPr><w:shd w:val="clear" w:fill="CCFFCC"/></w:pPr>'),
    ("HeaderChar", '<w:rPr><w:u w:val="double"/></w:rPr>'),
    ("HeaderChar", '<w:rPr><w:shd w:val="clear" w:fill="CCFFCC"/></w:rPr>'),
    ("DefaultParagraphFont", '<w:rPr><w:u w:val="double"/></w:rPr>'),
    ("TableNormal", '<w:tcPr><w:shd w:val="clear" w:fill="CCFFCC"/></w:tcPr>'),
])
@pytest.mark.parametrize("styled_side", ["template", "destination"])
def test_unpinned_style_differences_are_isolated(tmp_path, sid, xml, styled_side):
    template, source, values = setup_case(tmp_path, {})
    if styled_side == "template":
        template.write_bytes(style_properties(template.read_bytes(), sid, xml))
    else:
        source = style_properties(source, sid, xml)
    plan, output, record = run(source, values)
    assert record.passed, record.to_markdown()
    check_goal(source, output, template, values)
    assertion = next(a for a in plan.property_assertions if a["kind"] == "styles_added")
    mapped = assertion["style_map"][sid]
    assert mapped != sid
    before = DocxPackage(source).xml("word/styles.xml")
    after = DocxPackage(output).xml("word/styles.xml")
    old = next(s for s in before if s.get(W + "styleId") == sid)
    unchanged = next(s for s in after if s.get(W + "styleId") == sid)
    imported = next(s for s in after if s.get(W + "styleId") == mapped)
    wanted = next(s for s in DocxPackage(template.read_bytes()).xml("word/styles.xml")
                  if s.get(W + "styleId") == sid)
    assert etree.tostring(old, method="c14n") == etree.tostring(unchanged, method="c14n")
    for tag in ("pPr", "rPr", "tcPr"):
        actual_prop, expected_prop = imported.find(W + tag), wanted.find(W + tag)
        assert (etree.tostring(actual_prop, method="c14n", exclusive=True) if actual_prop is not None else None) == (
            etree.tostring(expected_prop, method="c14n", exclusive=True) if expected_prop is not None else None)
    assert imported.get(W + "default") is None
    copied = [a["part"] for a in plan.property_assertions if a["kind"] == "template_parts"]
    if sid == "DefaultParagraphFont":
        assert all(r.find(f"{W}rPr/{W}rStyle") is not None
                   for part in copied for r in DocxPackage(output).xml(part).iter(W + "r"))
    if sid == "Normal":
        assert all(p.find(f"{W}pPr/{W}pStyle") is not None
                   for part in copied for p in DocxPackage(output).xml(part).iter(W + "p"))
    if sid == "TableNormal":
        assert all(t.find(f"{W}tblPr/{W}tblStyle").get(W + "val") == mapped
                   for part in copied for t in DocxPackage(output).xml(part).iter(W + "tbl"))


@pytest.mark.parametrize("xml", [
    '<w:tblPr><w:tblBorders><w:bottom w:val="double" w:sz="18" w:color="FF0000"/></w:tblBorders></w:tblPr>',
    '<w:tblStylePr w:type="firstRow"><w:rPr><w:b/></w:rPr><w:tcPr><w:shd w:val="clear" w:fill="FFCC00"/></w:tcPr></w:tblStylePr>',
    '<w:rPr><w:sz w:val="36"/></w:rPr>',
])
def test_table_style_formatting_requires_complete_compatibility(tmp_path, xml):
    template, source, values = setup_case(tmp_path, {})
    def use_grid(root):
        table_properties = next(root.iter(W + "tblPr"))
        table_properties.insert(0, etree.Element(W + "tblStyle", {W + "val": "TableGrid"}))
    data = mutate(template.read_bytes(), "word/footer2.xml", use_grid)
    template.write_bytes(style_properties(data, "TableGrid", xml))
    plan, output, record = run(source, values)
    assert record.passed, record.to_markdown()
    check_goal(source, output, template, values)
    mapping = next(a for a in plan.property_assertions if a["kind"] == "template_parts")["style_map"]
    assert mapping["TableGrid"] != "TableGrid"
    table = next(s for s in DocxPackage(output).xml("word/styles.xml")
                 if s.get(W + "styleId") == mapping["TableGrid"])
    expected = etree.fromstring(f'<wrapper xmlns:w="{W[1:-1]}">{xml}</wrapper>')[0]
    assert etree.tostring(table.find(expected.tag), method="c14n", exclusive=True) == etree.tostring(expected, method="c14n", exclusive=True)


def test_nested_paragraph_style_cannot_rely_on_top_level_pins(tmp_path):
    template, source, values = setup_case(tmp_path, {})
    def textbox(root):
        outer = etree.SubElement(root, W + "p")
        content = etree.SubElement(etree.SubElement(etree.SubElement(outer, W + "r"), W + "pict"), W + "txbxContent")
        p = etree.SubElement(content, W + "p")
        etree.SubElement(etree.SubElement(p, W + "pPr"), W + "pStyle").set(W + "val", "Header")
        etree.SubElement(etree.SubElement(p, W + "r"), W + "t").text = "Nested template text"
    template.write_bytes(mutate(template.read_bytes(), "word/header1.xml", textbox))
    plan, output, record = run(source, values)
    assert record.passed, record.to_markdown()
    check_goal(source, output, template, values)
    mapping = next(a for a in plan.property_assertions if a["kind"] == "template_parts")["style_map"]
    assert mapping["Header"] != "Header"  # Header's source font size differs; nested content has no pins.


def test_disabled_import_does_not_allow_incompatible_name_reuse(tmp_path):
    template, source, values = setup_case(tmp_path, {})
    source = style_properties(source, "Header", '<w:pPr><w:shd w:val="clear" w:fill="FFCC00"/></w:pPr>')
    values["import_missing_styles"] = False
    with pytest.raises(JobException, match="no equivalent destination style"):
        run(source, values)


def test_verifiers_reject_a_transform_that_skips_formatting_compatibility(tmp_path, monkeypatch):
    import spec_surgeon.ops.template_styles as transform
    template, source, values = setup_case(tmp_path, {})
    source = style_properties(source, "Header", '<w:pPr><w:shd w:val="clear" w:fill="FFCC00"/></w:pPr>')
    monkeypatch.setattr(transform, "_formatting_signature", lambda *args: ())
    _, output, record = run(source, values)
    assert not record.passed and "input-derived mapping" in record.get("V10").summary
    before, after = VPackage(source), VPackage(output)
    try:
        assert verify_template_effect(before, after, effect(template, values))["errors"]
    finally:
        before.zip.close()
        after.zip.close()


def test_private_defaults_cannot_be_imported_without_binding_the_copied_content(tmp_path, monkeypatch):
    import spec_surgeon.ops.apply_header_footer_template as transform
    template, source, values = setup_case(tmp_path, {})
    template.write_bytes(style_properties(template.read_bytes(), "DefaultParagraphFont", '<w:rPr><w:u w:val="double"/></w:rPr>'))
    real = transform.remap_references
    monkeypatch.setattr(transform, "remap_references", lambda root, mapping, *args: real(root, mapping))
    _, output, record = run(source, values)
    assert not record.passed and record.get("V10").status == "FAIL"
    before, after = VPackage(source), VPackage(output)
    try:
        assert verify_template_effect(before, after, effect(template, values))["errors"]
    finally:
        before.zip.close()
        after.zip.close()
