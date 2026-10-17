"""Built-in batch 4, the review round on rule 9 (docs/BUILTIN_OPS_BATCH4_DESIGN.md, "Built"): what an op's prediction reads
through a paragraph's style chain, its table's style and the set of styles is declared, so that a restyle, an import or a
style change beside it is a plan-time conflict — never a clean plan that fails V10 after apply, or one whose result
depends on an order nobody chose — while combinations whose order cannot matter still plan and verify (an in-paragraph
text edit beside set_properties, two ops adding uses of one style, a font change beside set_properties)."""
from __future__ import annotations

import io
import zipfile

import pytest
from lxml import etree

from make_fixtures import build_style_template, build_styles
from spec_surgeon.editspec import apply_spec, plan_spec
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.profile import profile
from spec_surgeon.verify import run_suite

STYLES = "word/styles.xml"


@pytest.fixture(scope="module")
def src() -> bytes:
    return build_styles()


@pytest.fixture(scope="module")
def tpl(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("tpl") / "OFFICE STYLES.docx"
    path.write_bytes(build_style_template())
    return str(path)


def _spec(*ops, mode="formatting"):
    return {"request": "r", "summary": "s", "mode": mode, "ops": list(ops)}


def _conflict(data, *ops, mode="formatting"):
    for order in (ops, tuple(reversed(ops))):
        with pytest.raises(JobException) as e:
            plan_spec(DocxPackage(data), _spec(*order, mode=mode))
        assert e.value.code == "conflict", (order, e.value.code, str(e.value))
    return str(e.value)


def _passes(data, *ops, mode="formatting"):
    for order in (ops, tuple(reversed(ops))):
        pkg = DocxPackage(data)
        plan, frags = plan_spec(pkg, _spec(*order, mode=mode))
        out = apply_spec(DocxPackage(data), frags).output_bytes()
        rec = run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")
        assert rec.passed, rec.to_markdown()


def _with_part(data: bytes, name: str, edit) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            raw = zin.read(item.filename)
            if item.filename == name:
                raw = edit(raw)
            zout.writestr(item, raw)
    return buf.getvalue()


# ---- soft reads: the batch-4 regression ------------------------------------------------------------------------------

def test_set_properties_beside_an_in_paragraph_text_edit_plans_and_verifies_again(src):
    """Review D4: set_properties' new reads of the runs it finds already as asked refused any text edit of the same
    paragraph (it planned and passed before batch 4). The reads are soft now: the text edit plans beside it."""
    widow = {"op": "set_properties", "params": {"paragraph": {"widowControl": True}}}
    no_italics = {"op": "set_properties", "params": {"run": {"i": False}}}
    edit = {"op": "replace_text", "params": {"find": "fire alarm", "replace": "fire-alarm"}}
    for sp in (widow, no_italics):
        _passes(src, sp, edit, mode="content")
    # a change of what it read (the runs' properties) is still refused
    _conflict(src, no_italics, {"op": "clear_direct_formatting", "params": {"paragraph": False}})


# ---- the style chain, the table's style, styles the file lacks ---------------------------------------------------------

def test_a_remapped_table_style_beside_ops_reading_the_cell_paragraphs_conflicts(src, tpl):
    """Review A1/F3: the effective values of a paragraph in a table come through the table's style too."""
    cells = {"paragraphs": {"in_table": True}}
    remap = {"op": "map_styles", "params": {"map": [{"from": "ArchTable", "to": "Tbl Mine"}], "template": tpl}}
    for other in ({"op": "set_properties", "select": cells, "params": {"run": {"b": False}}},
                  {"op": "set_properties", "select": cells, "params": {"paragraph": {"keepNext": False}}},
                  {"op": "set_tab_stops", "select": cells, "params": {"clear": [1440]}},
                  {"op": "set_borders_shading", "select": cells, "params": {"shading": "none"}}):
        assert "tbl:0" in _conflict(src, remap, other), other


def test_a_style_the_file_lacks_imported_beside_a_paragraph_naming_it_conflicts(src, tpl):
    """Review A1: a paragraph names a style the file does not define (it falls back to nothing); an import adding that
    style in the same spec changes the paragraph's chain underneath an op reading it."""
    def dangle(raw):
        root = etree.fromstring(raw)
        p = list(root.iter(W + "p"))[7]                      # "Coordinate with …", no pStyle
        ppr = p.find(W + "pPr")
        if ppr is None:
            ppr = etree.SubElement(p, W + "pPr")
            p.insert(0, ppr)
        ps = etree.Element(W + "pStyle")
        ps.set(W + "val", "SpecNote")
        ppr.insert(0, ps)
        return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    data = _with_part(src, "word/document.xml", dangle)
    coord = {"paragraphs": {"text_regex": "^Coordinate"}}
    importing = {"op": "apply_style", "select": {"paragraphs": {"text_regex": "^NOTE TO"}},
                 "params": {"style": "Spec Note", "template": tpl}}
    frag = get_op("set_borders_shading").plan(DocxPackage(data), coord, {"shading": {"fill": "D9D9D9"}})
    assert (STYLES, "style:SpecNote", "verify:add") in frag.touches
    for other in ({"op": "set_borders_shading", "select": coord, "params": {"shading": {"fill": "D9D9D9"}}},
                  {"op": "set_properties", "select": coord, "params": {"run": {"b": True}}},
                  {"op": "set_tab_stops", "select": coord, "params": {"set": [{"pos": 720}]}}):
        assert "SpecNote" in _conflict(data, importing, other), other


def test_a_replaced_style_beside_set_properties_on_its_paragraphs_conflicts(src, tpl):
    """Review C4: existing 'replace' swaps Heading1's definition (the template's is italic) while set_properties finds
    the PART paragraphs already as asked (no italics) — it used to plan cleanly and fail V10 after apply."""
    applying = {"op": "apply_style", "select": {"paragraphs": {"text_regex": "^PART 2"}},
                "params": {"style": "Heading 1", "template": tpl, "existing": "replace"}}
    parts1 = {"paragraphs": {"text_regex": "^PART 1"}}
    assert "Heading1" in _conflict(src, applying, {"op": "set_properties", "select": parts1,
                                                   "params": {"run": {"i": False}}})
    mapping = {"op": "map_styles", "params": {"map": [{"from": "ArchLines", "to": "Heading 1"}], "template": tpl,
                                              "existing": "replace"}}
    _conflict(src, mapping, {"op": "set_tab_stops", "select": parts1, "params": {"set": [{"pos": 720}]}})


def test_set_properties_on_a_style_beside_set_properties_on_its_paragraphs_conflicts(src):
    """Review D5 (was open before batch 4): direct 'b off' on Arch Para paragraphs judges them by the effective value
    through ArchPara; 'b on' in the ArchPara definition beside it planned in both orders and failed V10. set_properties
    on a style now declares its own family (props) and the direct op reads it through the chains; a font change of the
    same style (set_style_font) still plans beside it — fonts are not set_properties' families."""
    paras = {"paragraphs": {"style_in": ["ArchPara"]}}
    direct = {"op": "set_properties", "select": paras, "params": {"run": {"b": False}}}
    _conflict(src, direct, {"op": "set_properties", "select": {"styles": {"ids": ["ArchPara"]}},
                            "params": {"run": {"b": True}}})
    _passes(src, direct, {"op": "set_style_font", "select": {"styles": {"ids": ["ArchPara"]}},
                          "params": {"font": "Arial"}})
    _passes(src, direct, {"op": "set_style_font", "select": {"styles": "all"}, "params": {"font": "Arial"}})


# ---- which paragraphs name which style ----------------------------------------------------------------------------------

def test_mapping_and_applying_styles_whose_uses_meet_conflicts(src, tpl):
    """Review C3: the outcome depended on the order the ops happened to run in."""
    # a chain of mappings across two ops (in one op the pairs are refused as order-dependent already)
    _conflict(src, {"op": "map_styles", "params": {"map": [{"from": "ArchArticle", "to": "ArchPara"}]}},
              {"op": "map_styles", "params": {"map": [{"from": "ArchPara", "to": "ArchNote"}]}})
    # applying a style beside mapping it away
    _conflict(src, {"op": "apply_style", "select": {"paragraphs": {"text_regex": "^PART 2"}},
                    "params": {"style": "Heading1"}},
              {"op": "map_styles", "params": {"map": [{"from": "Heading 1", "to": "Heading 2"}]}})
    # applying a style beside an op selecting by it
    _conflict(src, {"op": "apply_style", "select": {"paragraphs": {"text_regex": "^PART 2"}},
                    "params": {"style": "Heading1"}},
              {"op": "apply_style", "select": {"paragraphs": {"style_in": ["Heading1"]}},
               "params": {"style": "Heading2"}})


def test_ops_whose_uses_commute_still_plan_and_verify(src):
    """Two mappings into one style, two applications of one style to different paragraphs: same file in either
    order."""
    _passes(src, {"op": "map_styles", "params": {"map": [{"from": "ArchLines", "to": "ArchPara"}]}},
            {"op": "map_styles", "params": {"map": [{"from": "ArchAuto", "to": "ArchPara"}]}})
    _passes(src, {"op": "apply_style", "select": {"paragraphs": {"text_regex": "^PART 2"}},
                  "params": {"style": "Heading1"}},
            {"op": "apply_style", "select": {"paragraphs": {"text_regex": "^Manual list"}},
             "params": {"style": "Heading1"}})


def test_a_selection_by_appearance_or_colour_beside_a_restyle_conflicts(src):
    """A selection by has_bold / is_all_caps / style_in / runs.color_in reads every paragraph's chain; a restyle anywhere
    in scope could change what it chooses. remove_hidden_text judges effective hidden through the chains too."""
    restyle = {"op": "apply_style", "select": {"paragraphs": {"text_regex": "^Manual list"}},
               "params": {"style": "ArchNote"}}
    plain = {"font": "Arial", "tracked": False}
    for other in ({"op": "set_font", "select": {"paragraphs": {"has_bold": True}}, "params": plain},
                  {"op": "set_font", "select": {"paragraphs": {"style_in": ["ArchPara"]}}, "params": plain},
                  {"op": "set_font", "select": {"runs": {"color_in": ["0000FF"]}}, "params": plain},
                  {"op": "remove_hidden_text", "params": {"tracked": False}}):
        mode = "untracked" if other["op"] == "remove_hidden_text" else "formatting"
        with pytest.raises(JobException) as e:
            plan_spec(DocxPackage(src), _spec(restyle, other, mode=mode))
        assert e.value.code == "conflict" and "of every target in it" in e.value.reason, other
    # a selection by text alone reads no chain: it plans beside the restyle
    _passes(src, restyle, {"op": "set_font", "select": {"paragraphs": {"text_regex": "^PART"}}, "params": plain})


def test_an_import_beside_set_style_font_on_every_style_conflicts(src, tpl):
    """Review C3: the imported CMT kept Times New Roman while every other style became Arial."""
    importing = {"op": "map_styles", "params": {"map": [{"from": "ArchLines", "to": "CMT"}], "template": tpl}}
    _conflict(src, importing, {"op": "set_style_font", "select": {"styles": "all"}, "params": {"font": "Arial"}})
    _conflict(src, importing, {"op": "set_style_font", "select": {"styles": {"types": ["paragraph"]}},
                               "params": {"font": "Arial"}})
    # by id, on styles the import neither adds nor replaces: one spec
    _passes(src, importing, {"op": "set_style_font", "select": {"styles": {"ids": ["Heading2"]}},
                             "params": {"font": "Arial"}})


# ---- parameters, the residual, the profile, the template pin ----------------------------------------------------------

@pytest.mark.parametrize("op, params", [("set_properties", {"run": {"b": True}}), ("apply_style", {"style": "Heading2"}),
                                        ("map_styles", {"map": [{"from": "ArchLines", "to": "ArchPara"}]}),
                                        ("set_borders_shading", {"shading": {"fill": "D9D9D9"}})])
def test_an_empty_scope_is_refused(src, op, params):
    """Review A5/C7: the transform took [] for the document, the verifiers for no part — a clean plan, a V10 FAIL."""
    with pytest.raises(JobException) as e:
        get_op(op).plan(DocxPackage(src), {"scope": []}, params)
    assert e.value.code == "bad-params" and "empty" in e.value.reason


def test_a_changed_first_duplicate_of_a_style_id_fails_the_residual(src):
    """Review F4 (rule 8): the residual stripped the rPr of EVERY style sharing a changed id, while the kinds check the
    last one (the one Word and both sheets use); a change to the first one passed."""
    def duplicate(raw):
        root = etree.fromstring(raw)
        st = next(s for s in root.findall(W + "style") if s.get(W + "styleId") == "ArchPara")
        twin = etree.fromstring(etree.tostring(st))
        root.insert(list(root).index(st), twin)               # the FIRST of the two
        return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    data = _with_part(src, STYLES, duplicate)
    pkg = DocxPackage(data)
    frag = get_op("set_style_font").plan(pkg, {"styles": {"ids": ["ArchPara"]}}, {"font": "Arial"})
    plan = merge_fragments([frag])
    work = pkg.copy()
    get_op("set_style_font").apply(work, frag)
    good = work.output_bytes()
    assert run_suite(data, good, plan.to_dict(), "src.docx", "out.docx").passed

    def plant(raw):
        root = etree.fromstring(raw)
        first = next(s for s in root.findall(W + "style") if s.get(W + "styleId") == "ArchPara")
        rpr = first.find(W + "rPr")
        if rpr is None:
            rpr = etree.SubElement(first, W + "rPr")
        etree.SubElement(rpr, W + "caps")
        return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    bad = _with_part(good, STYLES, plant)
    rec = run_suite(data, bad, plan.to_dict(), "src.docx", "out.docx")
    assert not rec.passed and "styles.xml" in rec.to_markdown()


def test_the_profile_survives_a_malformed_numbering_part(src):
    """Review D6: the unused-style counts parse numbering and settings; a part that will not parse leaves them out, the
    file still profiles."""
    data = _with_part(src, "word/numbering.xml", lambda raw: raw[: len(raw) // 2])
    prof = profile(DocxPackage(data, name="spec.docx"))
    assert prof.get("styles_unused") is None and prof["styles_in_use"]


def test_apply_style_refuses_a_template_changed_after_planning_even_when_nothing_is_imported(src, tmp_path):
    """Review C8 (rule 12): the plan read the template to resolve the style; the file holds that style, so nothing is
    copied — apply still refuses a template that changed since."""
    path = tmp_path / "OFFICE STYLES.docx"
    path.write_bytes(build_style_template())
    pkg = DocxPackage(src)
    frag = get_op("apply_style").plan(pkg, {"paragraphs": {"text_regex": "^PART 2"}},
                                      {"style": "Heading 1", "template": str(path)})
    assert frag.transform_data["import"] is None
    path.write_bytes(path.read_bytes() + b"\0")
    with pytest.raises(JobException) as e:
        get_op("apply_style").apply(pkg.copy(), frag)
    assert e.value.code == "internal" and "changed" in e.value.reason
