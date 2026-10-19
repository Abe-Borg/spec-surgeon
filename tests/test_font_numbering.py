"""Live-run regressions: label fonts, symbol glyphs, slot consistency, and an independent whole-member proof."""
from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from make_fixtures import build_numbering
from spec_surgeon.editspec import SpecError, apply_spec, load_spec, plan_spec, validate_spec
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import font_numbering, selectors
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.verify import run_suite
from spec_surgeon.verify.props import sig

NUMBERING = "word/numbering.xml"
FONT = "Times New Roman"


def mutate(data, part, fn):
    pkg = DocxPackage(data).copy()
    root = pkg.xml(part)
    fn(root)
    pkg.write_xml(part, root)
    return pkg.output_bytes()


def level(root, aid=10, k=0):
    return next(lvl for a in root.findall(W + "abstractNum") if a.get(W + "abstractNumId") == str(aid)
                for lvl in a.findall(W + "lvl") if lvl.get(W + "ilvl") == str(k))


def font(lvl):
    return lvl.find(W + "rPr/" + W + "rFonts")


@pytest.fixture(scope="module")
def numbered():
    def label_fonts(root):
        for lvl in root.iter(W + "lvl"):
            rpr = lvl.find(W + "rPr")
            if rpr is None:
                rpr = etree.SubElement(lvl, W + "rPr")
            rf = etree.Element(W + "rFonts")
            for slot in ("ascii", "hAnsi", "cs"):
                rf.set(W + slot, "Arial")
            rf.set(W + "eastAsia", "MS Mincho")
            rf.set(W + "asciiTheme", "majorHAnsi")
            rpr.insert(0, rf)
            etree.SubElement(rpr, W + "sz").set(W + "val", "18")
        # A real symbol-font bullet must retain its glyph, even in a change-all-font recipe.
        rf = font(level(root, 21))
        rf.attrib.pop(W + "asciiTheme", None)
        for slot in ("ascii", "hAnsi", "cs"):
            rf.set(W + slot, "Wingdings")
    return mutate(build_numbering(), NUMBERING, label_fonts)


def spec(ops, assertions=()):
    return {"request": "change all fonts", "summary": "change all fonts", "mode": "formatting",
            "ops": ops, "assertions": list(assertions), "assumptions": [], "questions": []}


def run(data, ops, assertions=()):
    pkg = DocxPackage(data)
    plan, frags = plan_spec(pkg, spec(ops, assertions))
    output = apply_spec(pkg, frags).output_bytes()
    return plan, output, run_suite(data, output, plan.to_dict(), "before.docx", "after.docx")


def op(name="set_font", select=None, **params):
    return {"op": name, "select": select or {}, "params": {"font": FONT, **params}}


@pytest.mark.parametrize("name", ["set_font", "set_style_font"])
def test_whole_document_fonts_include_used_levels_and_full_overrides(numbered, name):
    plan, output, record = run(numbered, [op(name)])
    assert record.passed, record.to_markdown()
    before = DocxPackage(numbered).xml(NUMBERING)
    after = DocxPackage(output).xml(NUMBERING)
    assertion = next(a for a in plan.property_assertions if a["kind"] == "numbering_fonts")
    # Five style-linked outline levels, four appendix levels, one base override-list level and its full override.
    assert len(assertion["expected_levels"]) == 11
    assert ["num", 23, 1] in assertion["expected_levels"]
    assert ["abstractNum", 23, 1] not in assertion["expected_levels"]
    for target in assertion["expected_levels"]:
        old = font_numbering.level_element(before, target)
        new = font_numbering.level_element(after, target)
        assert font(new).get(W + "ascii") == FONT
        assert font(new).get(W + "hAnsi") == FONT
        assert font(new).get(W + "cs") == FONT
        assert font(new).get(W + "eastAsia") == "MS Mincho"
        assert font(new).get(W + "asciiTheme") is None
        assert sig(new.find(W + "rPr/" + W + "sz")) == sig(old.find(W + "rPr/" + W + "sz"))
    assert sig(level(before, 21)) == sig(level(after, 21))  # symbol bullet
    assert sig(level(before, 0)) == sig(level(after, 0))  # unused built-in list
    assert sig(level(before, 23, 1)) == sig(level(after, 23, 1))  # unused base behind a full override


@pytest.mark.parametrize("missing", ["rPr", "rFonts"])
def test_inherited_label_fonts_are_pinned_without_changing_other_properties(numbered, missing):
    def remove(root):
        lvl = level(root)
        parent = lvl if missing == "rPr" else lvl.find(W + "rPr")
        parent.remove(parent.find(W + missing))
    data = mutate(numbered, NUMBERING, remove)
    _, output, record = run(data, [op()])
    assert record.passed, record.to_markdown()
    assert font(level(DocxPackage(output).xml(NUMBERING))).get(W + "ascii") == FONT


@pytest.mark.parametrize("name,select", [("set_font", {"paragraphs": {"index_range": [0, 1]}}),
                                         ("set_style_font", {"styles": {"ids": ["Normal"]}})])
def test_narrow_selections_opt_in_to_shared_label_fonts(numbered, name, select):
    _, output, record = run(numbered, [op(name, select, tracked=False)])
    assert record.passed, record.to_markdown()
    assert DocxPackage(output).read(NUMBERING) == DocxPackage(numbered).read(NUMBERING)
    _, output, record = run(numbered, [op(name, select, tracked=False, numbering_levels=True)])
    assert record.passed, record.to_markdown()
    assert font(level(DocxPackage(output).xml(NUMBERING))).get(W + "ascii") == FONT


def test_matching_modifiers_alone_do_not_narrow_whole_document_font_changes(numbered):
    plan, _, record = run(numbered, [op(select={"paragraphs": {"case_insensitive": True,
                                                             "flexible_whitespace": True}})])
    assert record.passed, record.to_markdown()
    assert plan.rev_delta.get("rPrChange", 0) == 0
    assert any(a["kind"] == "numbering_fonts" for a in plan.property_assertions)


@pytest.mark.parametrize("reverse", [False, True])
def test_composed_font_recipe_has_one_shared_numbering_owner(numbered, reverse):
    ops = [op(), op("set_style_font")]
    if reverse:
        ops.reverse()
    ops.append(op("set_doc_defaults_font"))
    plan, _, record = run(numbered, ops, [{"kind": "effective_font", "value": FONT}])
    assert record.passed, record.to_markdown()
    assert sum(a["kind"] == "numbering_fonts" for a in plan.property_assertions) == 1
    assert "label(s)" in record.get("V10").summary


@pytest.mark.parametrize("directory", ["examples/specs", "src/spec_surgeon/examples/specs"])
def test_shipped_arial_recipe_preserves_its_promised_numbering_fonts(numbered, directory):
    recipe = load_spec(Path(__file__).parents[1] / directory / "change-all-font-arial.json")
    def courier_labels(root):
        for lvl in root.iter(W + "lvl"):
            fonts = lvl.find(W + "rPr/" + W + "rFonts")
            if fonts is not None and fonts.get(W + "ascii") != "Wingdings":
                fonts.attrib.pop(W + "asciiTheme", None)
                for slot in ("ascii", "hAnsi", "cs"):
                    fonts.set(W + slot, "Courier New")
    original = mutate(numbered, NUMBERING, courier_labels)
    source = DocxPackage(original)
    plan, fragments = plan_spec(source, recipe)
    output = apply_spec(source, fragments).output_bytes()
    assert DocxPackage(output).read(NUMBERING) == source.read(NUMBERING)
    assert NUMBERING not in plan.intended_parts
    record = run_suite(original, output, plan.to_dict(), "source.docx", "arial.docx")
    assert record.passed, record.to_markdown()


def test_different_label_fonts_and_target_sets_are_plan_time_conflicts(numbered):
    pkg = DocxPackage(numbered)
    with pytest.raises(JobException, match="numbering.xml") as exc:
        plan_spec(pkg, spec([op(), op("set_style_font", font="Courier New")]))
    assert exc.value.code == "conflict"
    frags = [get_op(n).plan(pkg, {}, {"font": FONT}) for n in ("set_font", "set_style_font")]
    frags[1].transform_data["numbering_fonts"]["targets"] = frags[1].transform_data["numbering_fonts"]["targets"][:-1]
    with pytest.raises(JobException, match="numbering.xml"):
        merge_fragments(frags)


@pytest.mark.parametrize("defect", ["selected_font", "unused_font", "symbol_font", "counter", "label_text", "extra_property"])
def test_every_other_numbering_change_is_rejected(numbered, defect):
    plan, output, record = run(numbered, [op()])
    assert record.passed
    def plant(root):
        if defect in ("selected_font", "unused_font", "symbol_font"):
            aid = {"selected_font": 10, "unused_font": 0, "symbol_font": 21}[defect]
            font(level(root, aid)).set(W + "ascii", "Calibri")
        elif defect == "counter":
            level(root).find(W + "start").set(W + "val", "9")
        elif defect == "label_text":
            level(root).find(W + "lvlText").set(W + "val", "%1)")
        else:
            etree.SubElement(level(root).find(W + "rPr"), W + "b")
    bad = run_suite(numbered, mutate(output, NUMBERING, plant), plan.to_dict(), "before", "after")
    assert bad.get("V10").status == "FAIL" and "numbering_fonts" in bad.get("V10").summary


def test_verifier_reselects_used_levels_if_plan_and_apply_share_a_bug(numbered, monkeypatch):
    original = font_numbering.used_levels
    def drop_level(*args):
        part, targets = original(*args)
        return part, targets[:-1]
    monkeypatch.setattr(font_numbering, "used_levels", drop_level)
    _, _, record = run(numbered, [op()])
    assert record.get("V10").status == "FAIL"
    assert "different identities" in record.get("V10").summary


def test_verifier_rederives_label_opt_out_from_raw_request(numbered, monkeypatch):
    import spec_surgeon.ops.set_font as set_font
    monkeypatch.setattr(set_font, "numbering_enabled", lambda *args: True)
    _, _, record = run(numbered, [op(numbering_levels=False)])
    assert record.get("V10").status == "FAIL"
    assert "did not request a label-font change" in record.get("V10").summary


def test_verifier_rederives_default_symbol_exclusions(numbered, monkeypatch):
    import spec_surgeon.ops.set_font as set_font
    monkeypatch.setattr(set_font, "DEFAULT_PRESERVE_FONTS", [])
    _, _, record = run(numbered, [op()])
    assert record.get("V10").status == "FAIL"
    assert "symbol-font exclusions disagree" in record.get("V10").summary


def test_postcondition_checks_label_fonts_even_without_a_numbering_change(numbered):
    _, _, record = run(numbered, [op(numbering_levels=False)], [{"kind": "effective_font", "value": FONT}])
    assert record.get("V10").status == "FAIL"
    assert "label ascii" in record.get("V10").summary


def test_ordinary_paragraph_residual_still_catches_changes_beside_label_fonts(numbered):
    plan, output, _ = run(numbered, [op()])
    def plant(root):
        etree.SubElement(next(root.iter(W + "p")), W + "bookmarkStart", {W + "id": "987", W + "name": "intruder"})
    record = run_suite(numbered, mutate(output, "word/document.xml", plant), plan.to_dict(), "before", "after")
    assert record.get("V10").status == "FAIL" and "unpredicted" in record.get("V10").summary


def test_numbering_reads_refuse_restyle_or_counter_changes_in_the_same_spec(numbered):
    for other in ({"op": "set_list_level", "params": {"style": "ArchPara", "format": "lowerLetter"}},
                  {"op": "apply_style", "select": {"paragraphs": {"style_in": ["ArchPara"]}},
                   "params": {"style": "Normal"}}):
        with pytest.raises(JobException) as exc:
            plan_spec(DocxPackage(numbered), spec([op(), other]))
        assert exc.value.code == "conflict"


def test_theme_override_and_tracked_label_changes_refuse_at_plan_time(numbered):
    for params, code in [({"clear_theme_fonts": False}, "theme-font"), ({"tracked": True}, "bad-params")]:
        with pytest.raises(JobException) as exc:
            chosen = op("set_font", {"paragraphs": {"index_range": [0, 1]}},
                        numbering_levels=True, **params) if params.get("tracked") else op(**params)
            plan_spec(DocxPackage(numbered), spec([chosen]))
        assert exc.value.code == code


def test_east_asian_slot_is_explicit_in_every_font_operation_and_postcondition(numbered):
    ops = [op(name, east_asia=True) for name in ("set_font", "set_style_font", "set_doc_defaults_font")]
    plan, output, record = run(numbered, ops, [{"kind": "effective_font", "value": FONT, "east_asia": True}])
    assert record.passed, record.to_markdown()
    assert font(level(DocxPackage(output).xml(NUMBERING))).get(W + "eastAsia") == FONT
    post = next(a for a in plan.property_assertions if a["kind"] == "effective_font")
    assert post["slots"] == ["ascii", "hAnsi", "cs", "eastAsia"]


def test_independent_slot_rule_catches_an_accidental_extra_slot(numbered, monkeypatch):
    import spec_surgeon.ops.set_font as set_font
    original = selectors.font_values
    def extra_slot(params):
        return {**original(params), "eastAsia": params["font"]}
    monkeypatch.setattr(set_font, "font_values", extra_slot)
    _, _, record = run(numbered, [op()])
    assert record.get("V10").status == "FAIL"
    assert "disagree with requested slots" in record.get("V10").summary


@pytest.mark.parametrize("assertion", [{"east_asia": "yes"}, {"numbering_levels": 1}, {"slots": ["east_asia"]}])
def test_assertion_flags_are_validated(assertion):
    with pytest.raises(SpecError):
        validate_spec(spec([op()], [{"kind": "effective_font", "value": FONT, **assertion}]))
