"""Selective style application: preservation, independent proofs and composition."""
from __future__ import annotations

import hashlib

import pytest
from lxml import etree

from make_fixtures import build_styles
from spec_surgeon.goals import GoalError, resolve_goal, validate_contract, verify_goal
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import apply_style as transform
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.verify import run_suite
from spec_surgeon.verify.effects import _validate
from test_apply_style import MAIN, STYLES, applying, mutate, par, v10
from test_goal_contracts import contract, execute


def xml(text):
    return etree.fromstring(f'<wrapper xmlns:w="{W[1:-1]}">{text}</wrapper>')[0]


@pytest.fixture
def source():
    data = build_styles()
    def styles(root):
        root.append(xml('<w:style w:type="paragraph" w:styleId="SelectiveBase"><w:name w:val="Selective Base"/>'
                        '<w:pPr><w:spacing w:before="120"/></w:pPr>'
                        '<w:rPr><w:rFonts w:asciiTheme="minorHAnsi"/><w:b w:val="0"/></w:rPr></w:style>'))
        root.append(xml('<w:style w:type="paragraph" w:styleId="Selective"><w:name w:val="Selective"/>'
                        '<w:basedOn w:val="SelectiveBase"/><w:pPr><w:spacing w:after="60"/>'
                        '<w:ind w:left="720"/></w:pPr><w:rPr><w:sz w:val="22"/></w:rPr></w:style>'))
    data = mutate(data, STYLES, styles)
    def body(root):
        paragraph = next(root.iter(W + "p"))
        old = paragraph.find(W + "pPr")
        paragraph.remove(old)
        paragraph.insert(0, xml('<w:pPr><w:spacing w:before="900" w:after="800" w:line="320" w:lineRule="auto"/>'
                                '<w:ind w:left="300" w:right="400"/><w:jc w:val="center"/>'
                                '<w:rPr><w:rFonts w:ascii="Arial" w:eastAsia="MS Mincho"/><w:b/><w:i/></w:rPr></w:pPr>'))
        run = paragraph.find(W + "r")
        old = run.find(W + "rPr")
        run.remove(old)
        run.insert(0, xml('<w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="MS Mincho"/>'
                          '<w:b/><w:i/><w:vanish/><w:color w:val="FF0000"/><w:sz w:val="30"/>'
                          '<w:highlight w:val="yellow"/><w:vertAlign w:val="superscript"/></w:rPr>'))
    return mutate(data, MAIN, body)


SELECT = {"paragraphs": {"index_range": [0, 1]}}
PARAMS = {"style": "Selective", "direct_formatting": "replace_style_owned"}


@pytest.mark.parametrize("tracked", [False, True])
def test_selective_policy_replaces_owned_attributes_and_preserves_other_properties(source, tracked):
    plan, output, record = applying(source, SELECT, **PARAMS, tracked=tracked)
    assert record.passed, record.to_markdown()
    paragraph = par(output, "SECTION")
    ppr, rpr = paragraph.find(W + "pPr"), paragraph.find(W + "r/" + W + "rPr")
    assert ppr.find(W + "spacing").attrib == {W + "line": "320", W + "lineRule": "auto"}
    assert ppr.find(W + "ind").attrib == {W + "right": "400"}
    assert ppr.find(W + "jc").get(W + "val") == "center"
    assert rpr.find(W + "rFonts").attrib == {W + "hAnsi": "Arial", W + "eastAsia": "MS Mincho"}
    assert rpr.find(W + "b") is None and rpr.find(W + "sz") is None
    for protected in ("i", "vanish", "color", "highlight", "vertAlign"):
        assert rpr.find(W + protected) is not None, protected
    assert ppr.find(W + "rPr/" + W + "rFonts").attrib == {W + "eastAsia": "MS Mincho"}
    assert plan.property_assertions[0]["expected_runs"] == 1
    assert plan.property_assertions[0]["expected_marks"] == 1
    if tracked:
        assert {k: n for k, n in plan.rev_delta.items() if n} == {"pPrChange": 1, "rPrChange": 2}


def test_second_plain_application_is_a_noop(source):
    _, output, record = applying(source, SELECT, **PARAMS, tracked=False)
    assert record.passed
    plan, again, record = applying(output, SELECT, **PARAMS, tracked=False)
    assert record.passed and again == output and plan.intended_parts == []


def test_symbol_fonts_and_own_numbering_stay(source):
    def patch(root):
        paragraph = next(root.iter(W + "p"))
        paragraph.find(W + "r/" + W + "rPr/" + W + "rFonts").set(W + "ascii", "Wingdings")
        paragraph.find(W + "pPr").insert(0, xml('<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'))
    source = mutate(source, MAIN, patch)
    plan, output, record = applying(source, SELECT, **PARAMS, tracked=False)
    assert record.passed, record.to_markdown()
    p = par(output, "SECTION")
    assert p.find(W + "r/" + W + "rPr/" + W + "rFonts").get(W + "ascii") == "Wingdings"
    assert p.find(W + "pPr/" + W + "numPr/" + W + "numId").get(W + "val") == "1"
    assert any("own numbering" in flag for flag in plan.flags)


def test_existing_format_revision_still_refuses_a_second_tracked_change(source):
    def patch(root):
        next(root.iter(W + "rPr")).append(xml('<w:rPrChange w:id="222" w:author="Previous">'
                                               '<w:rPr><w:b/></w:rPr></w:rPrChange>'))
    source = mutate(source, MAIN, patch)
    with pytest.raises(JobException, match="pending"):
        applying(source, SELECT, **PARAMS, tracked=True)


@pytest.mark.parametrize("damage", ["color", "rFonts", "spacing", "owned_not_removed"])
def test_independent_verifier_rejects_collateral_or_incomplete_formatting(source, damage):
    plan, output, record = applying(source, SELECT, **PARAMS, tracked=False)
    assert record.passed
    def corrupt(root):
        paragraph = next(root.iter(W + "p"))
        rpr = paragraph.find(W + "r/" + W + "rPr")
        if damage == "owned_not_removed":
            rpr.insert(1, xml('<w:b/>'))
        elif damage == "spacing":
            paragraph.find(W + "pPr/" + W + "spacing").set(W + "line", "1")
        elif damage == "rFonts":
            rpr.find(W + "rFonts").attrib.pop(W + "eastAsia")
        else:
            rpr.remove(rpr.find(W + damage))
    corrupted = mutate(output, MAIN, corrupt)
    checked = run_suite(source, corrupted, plan.to_dict(), "source.docx", "output.docx")
    assert not checked.passed and next(c for c in checked.checks if c.id == "V10").status == "FAIL", v10(checked)


def test_shared_wrong_ownership_in_plan_and_transform_is_caught(source, monkeypatch):
    original = transform.style_owned_properties
    def wrong(*args):
        ownership, ids = original(*args)
        ownership["rPr"]["color"] = [xml('<w:color w:val="000000"/>')]
        return ownership, ids
    monkeypatch.setattr(transform, "style_owned_properties", wrong)
    _, _, record = applying(source, SELECT, **PARAMS, tracked=False)
    assert not record.passed and next(c for c in record.checks if c.id == "V10").status == "FAIL"


def test_partial_borders_are_preserved_and_reported(source):
    def style(root):
        target = next(s for s in root if s.get(W + "styleId") == "Selective")
        target.find(W + "pPr").append(xml('<w:pBdr><w:bottom w:val="single"/></w:pBdr>'))
    source = mutate(source, STYLES, style)
    source = mutate(source, MAIN, lambda root: next(root.iter(W + "pPr")).append(
        xml('<w:pBdr><w:top w:val="single"/><w:bottom w:val="double"/></w:pBdr>')))
    plan, output, record = applying(source, SELECT, **PARAMS, tracked=False)
    assert record.passed, record.to_markdown()
    assert par(output, "SECTION").find(W + "pPr/" + W + "pBdr/" + W + "top") is not None
    assert any("pBdr" in flag and "retained" in flag for flag in plan.flags)


def test_held_source_ancestor_controls_imported_style_ownership(source, tmp_path):
    def template_styles(root):
        base = next(s for s in root if s.get(W + "styleId") == "SelectiveBase")
        base.remove(base.find(W + "rPr"))
        base.append(xml('<w:rPr><w:color w:val="000000"/></w:rPr>'))
        target = next(s for s in root if s.get(W + "styleId") == "Selective")
        target.set(W + "styleId", "ImportedSelective")
        target.find(W + "name").set(W + "val", "Imported Selective")
    template = tmp_path / "office.docx"
    template.write_bytes(mutate(source, STYLES, template_styles))
    _, output, record = applying(source, SELECT, style="ImportedSelective", template=str(template),
                                direct_formatting="replace_style_owned", tracked=False)
    assert record.passed, record.to_markdown()
    rpr = par(output, "SECTION").find(W + "r/" + W + "rPr")
    assert rpr.find(W + "b") is None and rpr.find(W + "color") is not None


@pytest.mark.parametrize("existing", ["keep", "replace"])
def test_template_ownership_uses_requested_keep_or_replace_rule(source, tmp_path, existing):
    def changed_template(root):
        target = next(s for s in root if s.get(W + "styleId") == "Selective")
        target.remove(target.find(W + "rPr"))
        target.append(xml('<w:rPr><w:color w:val="000000"/></w:rPr>'))
    template = tmp_path / "office.docx"
    template.write_bytes(mutate(source, STYLES, changed_template))
    _, output, record = applying(source, SELECT, **PARAMS, template=str(template), existing=existing, tracked=False)
    assert record.passed, record.to_markdown()
    rpr = par(output, "SECTION").find(W + "r/" + W + "rPr")
    assert (rpr.find(W + "color") is None) == (existing == "replace")
    assert (rpr.find(W + "sz") is None) == (existing == "keep")


def test_inactive_and_linked_styles_do_not_supply_ownership(source):
    def changed_styles(root):
        base = next(s for s in root if s.get(W + "styleId") == "SelectiveBase")
        base.append(xml('<w:basedOn w:val="DefaultParagraphFont"/>'))
        base.append(xml('<w:link w:val="ArchStrong"/>'))
        inactive = next(s for s in root if s.get(W + "styleId") == "DefaultParagraphFont")
        inactive.append(xml('<w:rPr><w:color w:val="000000"/></w:rPr>'))
        linked = next(s for s in root if s.get(W + "styleId") == "ArchStrong")
        linked.find(W + "rPr").append(xml('<w:i/>'))
    source = mutate(source, STYLES, changed_styles)
    _, output, record = applying(source, SELECT, **PARAMS, tracked=False)
    assert record.passed, record.to_markdown()
    rpr = par(output, "SECTION").find(W + "r/" + W + "rPr")
    assert rpr.find(W + "color") is not None and rpr.find(W + "i") is not None


def test_ancestor_definition_changes_conflict_at_plan_time(source):
    package = DocxPackage(source)
    style = get_op("apply_style").plan(package, SELECT, PARAMS)
    font = get_op("set_style_font").plan(package, {}, {"styles": ["SelectiveBase"], "font": "Arial"})
    with pytest.raises(JobException) as error:
        merge_fragments([style, font])
    assert error.value.code == "conflict"


def effect(identifier, interval, style="Selective"):
    return {"id": identifier, "type": "paragraph_style", "select": {"paragraphs": {"index_range": interval}},
            "params": {"style": style, "direct_formatting": "replace_style_owned", "tracked": False}, "expected_count": "all"}


def test_disjoint_style_effects_have_original_to_final_proof(source, tmp_path):
    path = tmp_path / "source.docx"
    path.write_bytes(source)
    goal = contract(effect("first", [0, 1]), effect("second", [1, 2]))
    output = execute(path, goal)
    binding = resolve_goal(path, goal)["binding"]
    corrupted = mutate(output, MAIN, lambda root: next(root.iter(W + "rFonts")).set(W + "eastAsia", "Wrong"))
    assert not verify_goal(path, corrupted, goal, binding)["passed"]


def test_multiple_imported_styles_share_a_held_ancestor_with_final_proof(source, tmp_path):
    def imported_styles(root):
        root.append(xml('<w:style w:type="paragraph" w:styleId="OfficeA"><w:name w:val="Office A"/>'
                        '<w:basedOn w:val="SelectiveBase"/><w:rPr><w:sz w:val="24"/></w:rPr></w:style>'))
        root.append(xml('<w:style w:type="paragraph" w:styleId="OfficeB"><w:name w:val="Office B"/>'
                        '<w:basedOn w:val="SelectiveBase"/><w:rPr><w:color w:val="000000"/></w:rPr></w:style>'))
    template, path = tmp_path / "office.docx", tmp_path / "source.docx"
    template.write_bytes(mutate(source, STYLES, imported_styles))
    path.write_bytes(source)
    effects = [effect("first", [0, 1], "OfficeA"), effect("second", [1, 2], "OfficeB")]
    for proposed in effects:
        proposed.update(template=str(template), template_sha256=hashlib.sha256(template.read_bytes()).hexdigest())
    execute(path, contract(*effects))


def test_alternate_units_and_font_theme_overrides_are_removed_together(source):
    def alternatives(root):
        paragraph = next(root.iter(W + "p"))
        spacing = paragraph.find(W + "pPr/" + W + "spacing")
        spacing.set(W + "beforeLines", "500")
        spacing.set(W + "afterAutospacing", "1")
        paragraph.find(W + "pPr/" + W + "ind").set(W + "startChars", "100")
        paragraph.find(W + "r/" + W + "rPr/" + W + "rFonts").set(W + "asciiTheme", "majorHAnsi")
    source = mutate(source, MAIN, alternatives)
    _, output, record = applying(source, SELECT, **PARAMS, tracked=False)
    assert record.passed, record.to_markdown()
    paragraph = par(output, "SECTION")
    assert set(paragraph.find(W + "pPr/" + W + "spacing").attrib) == {W + "line", W + "lineRule"}
    assert set(paragraph.find(W + "pPr/" + W + "ind").attrib) == {W + "right"}
    assert set(paragraph.find(W + "r/" + W + "rPr/" + W + "rFonts").attrib) == {W + "hAnsi", W + "eastAsia"}


def test_extension_attributes_never_become_destination_owned_formatting(source):
    extension = "{urn:office-extension}metadata"
    source = mutate(source, MAIN, lambda root: next(root.iter(W + "rFonts")).set(extension, "source annotation"))
    def style_extension(root):
        base = next(s for s in root if s.get(W + "styleId") == "SelectiveBase")
        base.find(W + "rPr/" + W + "rFonts").set(extension, "style annotation")
    source = mutate(source, STYLES, style_extension)
    _, output, record = applying(source, SELECT, **PARAMS, tracked=False)
    assert record.passed, record.to_markdown()
    assert next(DocxPackage(output).xml(MAIN).iter(W + "rFonts")).get(extension) == "source annotation"


@pytest.mark.parametrize("selector", [{"index_range": [0, 1]}, {"text_regex": "^OTHER"},
                                      {"index_range": [1, 2], "in_table": False}])
def test_nonprovable_or_overlapping_style_compositions_are_rejected(selector):
    first, second = effect("first", [0, 1]), effect("second", [1, 2])
    second["select"]["paragraphs"] = selector
    goal = contract(first, second)
    with pytest.raises(GoalError, match="clearing direct formatting"):
        validate_contract(goal)
    with pytest.raises(ValueError, match="clearing direct formatting"):
        _validate(goal)
