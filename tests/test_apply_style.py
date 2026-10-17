"""apply_style and V10's ``applied_style`` kind (built-in batch 4, item 30 ★ — docs/BUILTIN_OPS_BATCH4_DESIGN.md §6): end to
end on the architect's spec (paragraphs picked by appearance and by pattern; the style from the document or imported
from a template, keep and replace; direct formatting kept or cleared; tracked and plain); a wrong OUTPUT per rule; wrong
IMPLEMENTATIONS shared by plan and apply; plan-time conflicts (rule 9); the template as an input (rule 12); parameters
and refusals; the catalog."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import List, Tuple

import pytest
from lxml import etree

from make_fixtures import build_style_template, build_styles
from spec_surgeon.catalog import COMMON_ASSUMPTIONS, op_catalog
from spec_surgeon.editspec import plan_spec
from spec_surgeon.ooxml import paragraphs
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import apply_style as apply_module
from spec_surgeon.ops import clear_formatting as clear_module
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.report import FAILURE_EXPLANATIONS
from spec_surgeon.verify import run_suite

MAIN = "word/document.xml"
STYLES = "word/styles.xml"
AUTHOR = "Abraham Borg"
BOLD_CAPS = {"paragraphs": {"has_bold": True, "is_all_caps": True}}
PARTS = {"paragraphs": {"text_regex": "^PART"}}


@pytest.fixture(scope="module")
def src() -> bytes:
    return build_styles()


@pytest.fixture()
def tpl(tmp_path) -> str:
    p = tmp_path / "OFFICE STYLES.docx"
    p.write_bytes(build_style_template())
    return str(p)


def run_ops(data: bytes, specs: List[Tuple[str, dict, dict]]):
    pkg = DocxPackage(data)
    frags = [get_op(o).plan(pkg, sel, par) for o, sel, par in specs]
    plan = merge_fragments(frags)
    work = pkg.copy()
    for f in frags:
        get_op(f.op).apply(work, f)
    out = work.output_bytes()
    return plan, out, run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")


def applying(data: bytes, select: dict, **params):
    return run_ops(data, [("apply_style", select, params)])


def v10(rec) -> str:
    c = next(c for c in rec.checks if c.id == "V10")
    return c.summary + " | " + " | ".join(c.data.get("errors", []))


def failed(rec):
    return [c.id for c in rec.checks if c.status == "FAIL"]


def text_of(p) -> str:
    return "".join(t.text or "" for t in p.iter(W + "t"))


def par(data: bytes, needle: str):
    return next(p for p in paragraphs(DocxPackage(data).xml(MAIN)) if text_of(p).startswith(needle))


def pstyle(p):
    ps = p.find(f"{W}pPr/{W}pStyle")
    return ps.get(W + "val") if ps is not None else None


def mutate(docx: bytes, part: str, fn) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(docx))
    root = etree.fromstring(zin.read(part))
    fn(root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            z.writestr(info, serialize_xml(root) if info.filename == part else zin.read(info.filename))
    return buf.getvalue()


def find_p(root, needle):
    return next(p for p in root.iter(W + "p") if text_of(p).startswith(needle))


# ---- end to end ------------------------------------------------------------------------------------------------------

def test_bold_all_caps_paragraphs_become_headings_by_appearance(src):
    """The starred case: paragraphs picked by how they look (bold and all caps) get a style the document has; their own
    direct formatting stays by default; targeted → tracked (§14.1)."""
    plan, out, rec = applying(src, BOLD_CAPS, style="heading 1", author=AUTHOR)
    assert rec.passed, rec.to_markdown()
    a = plan.property_assertions[0]
    assert a["style_id"] == "Heading1" and a["expected_restyled"] == 5 and a["expected_runs"] == 0
    for needle in ("SECTION 21 13 13", "WET-PIPE", "1.01", "PART 2", "2.01"):
        assert pstyle(par(out, needle)) == "Heading1", needle
    sec = par(out, "SECTION 21 13 13")
    assert sec.find(f"{W}pPr/{W}jc") is not None                         # direct formatting kept
    assert sec.find(f"{W}pPr/{W}pPrChange") is not None and plan.rev_delta.get("pPrChange") == 5
    assert any("already as asked, left alone: 1" in n for n in plan.notes)   # PART 1 is Heading 1 already
    assert (MAIN, "0", "pStyle") in get_op("apply_style").plan(DocxPackage(src), BOLD_CAPS, {"style": "Heading1"}).touches


def test_clear_makes_the_paragraphs_follow_the_new_style(src):
    plan, out, rec = applying(src, BOLD_CAPS, style="Heading1", direct_formatting="clear", tracked=False)
    assert rec.passed, rec.to_markdown()
    sec = par(out, "SECTION 21 13 13")
    ppr = sec.find(W + "pPr")
    assert [c.tag for c in ppr] == [W + "pStyle"]                          # jc cleared, pStyle first
    rpr = sec.find(f"{W}r/{W}rPr")
    assert [etree.QName(c).localname for c in rpr] == ["lang"]             # fonts, b, caps, sz gone; language stays
    title = par(out, "WET-PIPE")
    assert title.find(f"{W}pPr/{W}pBdr") is None and title.find(f"{W}pPr/{W}spacing") is None
    assert plan.property_assertions[0]["expected_runs"] == 3 and not plan.rev_delta.get("pPrChange")


def test_a_style_from_the_template_is_imported_with_its_list(src, tpl):
    """'Make every PART heading our PRT style': PRT is not in the architect's file, so it comes from the template with
    the styles its list names (ART, PR1, PR2), its linked PRTChar and the list itself."""
    plan, out, rec = applying(src, PARTS, style="PRT", template=tpl, direct_formatting="clear")
    assert rec.passed, rec.to_markdown()
    kinds = [a["kind"] for a in plan.property_assertions]
    assert kinds == ["applied_style", "styles_imported"]
    imp = plan.property_assertions[1]
    assert imp["requested"] == ["PRT"] and set(imp["imported"]) == {"PRT", "ART", "PR1", "PR2", "PRTChar"}
    for needle in ("PART 1", "PART 2", "PART 3"):
        assert pstyle(par(out, needle)) == "PRT", needle
    numbering = DocxPackage(out).xml("word/numbering.xml")
    assert any(n.get(W + "numId") == imp["num_map"]["10"] for n in numbering.findall(W + "num"))


def test_existing_replace_takes_the_templates_definition(src, tpl):
    plan, out, rec = applying(src, {"paragraphs": {"text_regex": "^PART 2"}}, style="Heading1", template=tpl,
                              existing="replace", tracked=False)
    assert rec.passed, rec.to_markdown()
    imp = plan.property_assertions[1]
    assert imp["replaced"] == ["Heading1"] and imp["imported"] == []
    h1 = next(s for s in DocxPackage(out).xml(STYLES).findall(W + "style") if s.get(W + "styleId") == "Heading1")
    assert h1.find(f"{W}rPr/{W}rFonts").get(W + "ascii") == "Arial"
    # keep (the default): the file's own Heading 1, no import at all
    plan, out, rec = applying(src, {"paragraphs": {"text_regex": "^PART 2"}}, style="Heading1", template=tpl)
    assert rec.passed and [a["kind"] for a in plan.property_assertions] == ["applied_style"]


def test_already_styled_paragraphs_are_left_alone_and_own_numbering_is_flagged(src):
    plan, out, rec = applying(src, {"paragraphs": {"text_regex": "^(PART 1|Manual)"}}, style="Heading1")
    assert rec.passed, rec.to_markdown()
    assert plan.property_assertions[0]["changed"] == {MAIN: [[9, True]]}
    assert any("own numbering" in f for f in plan.flags)


def test_whole_document_restyle_is_plain_and_a_pending_change_is_flagged(src):
    plan, out, rec = applying(src, {}, style="Normal", direct_formatting="clear")
    assert rec.passed, rec.to_markdown()
    assert plan.ops[0]["params"]["tracked"] is False
    assert any("pending tracked formatting change" in f for f in plan.flags)


def test_nothing_to_do_is_a_clean_no_op(src):
    plan, out, rec = applying(src, {"paragraphs": {"style_in": ["Heading1"]}}, style="Heading1")
    assert rec.passed and plan.property_assertions[0]["expected_paragraphs"] == 0
    assert any("nothing to change" in n for n in plan.notes)


# ---- a wrong OUTPUT is caught ----------------------------------------------------------------------------------------

def _broken(src, fn, select=BOLD_CAPS, **params):
    plan, out, rec = applying(src, select, **params)
    assert rec.passed, rec.to_markdown()
    bad = mutate(out, MAIN, fn)
    rec2 = run_suite(src, bad, plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec2)
    return v10(rec2)


def test_a_paragraph_left_in_its_old_style_fails(src):
    def fn(root):
        p = find_p(root, "2.01")
        p.find(f"{W}pPr/{W}pStyle").set(W + "val", "ArchArticle")
    assert "applied_style" in _broken(src, fn, style="Heading1", tracked=False)


def test_another_paragraph_property_changed_fails(src):
    def fn(root):
        p = find_p(root, "SECTION 21 13 13")
        p.find(f"{W}pPr/{W}jc").set(W + "val", "right")
    assert "paragraph properties" in _broken(src, fn, style="Heading1", tracked=False)


def test_a_cleared_property_left_in_place_fails(src):
    def fn(root):
        r = find_p(root, "SECTION 21 13 13").find(W + "r")
        etree.SubElement(r.find(W + "rPr"), W + "b")
    msg = _broken(src, fn, style="Heading1", direct_formatting="clear", tracked=False)
    assert "cleared but still there" in msg or "run properties" in msg


def test_a_tracked_change_with_a_wrong_snapshot_fails(src):
    def fn(root):
        chg = find_p(root, "WET-PIPE").find(f"{W}pPr/{W}pPrChange/{W}pPr")
        etree.SubElement(chg, W + "keepNext")
    assert "snapshot" in _broken(src, fn, style="Heading1", author=AUTHOR)


# ---- wrong IMPLEMENTATIONS shared by plan and apply are caught -------------------------------------------------------

def test_a_style_rule_that_forgets_the_default_paragraph_style_is_caught(src, monkeypatch):
    """A transform that takes a paragraph without pStyle as having no style restyles Normal paragraphs to Normal — a
    change the verifier (pStyle, else the default) does not predict."""
    real = apply_module.StyleSheet.paragraph_style_id
    monkeypatch.setattr(apply_module.StyleSheet, "paragraph_style_id",
                        lambda self, p: (p.find(f"{W}pPr/{W}pStyle").get(W + "val")
                                         if p.find(f"{W}pPr/{W}pStyle") is not None else None))
    plan, out, rec = applying(src, {"paragraphs": {"text_regex": "^(Coordinate|Manual)"}}, style="Normal")
    monkeypatch.setattr(apply_module.StyleSheet, "paragraph_style_id", real)
    assert not rec.passed and "applied_style" in v10(rec)


def test_a_clear_set_that_reveals_hidden_text_is_caught(src, monkeypatch):
    """A shared default clear set that includes w:vanish (hidden text would show) — the verifier's own default set keeps
    it."""
    bad = clear_module.ClearSettings(run_names=frozenset(clear_module.RUN_CLEARED) | {"vanish"})
    monkeypatch.setattr(apply_module, "ClearSettings", lambda: bad)
    plan, out, rec = applying(src, {"paragraphs": {"text_regex": "^A\\."}}, style="ArchPara",
                              direct_formatting="clear", tracked=False)
    assert not rec.passed and "applied_style" in v10(rec)


def test_a_pstyle_written_last_is_caught(src, monkeypatch):
    def edit_factory(style_id):
        def edit(ppr):
            ps = etree.SubElement(ppr, W + "pStyle")
            ps.set(W + "val", style_id)
        return edit
    monkeypatch.setattr(apply_module, "set_pstyle", edit_factory)
    plan, out, rec = applying(src, {"paragraphs": {"text_regex": "^Coordinate"}}, style="Heading1", tracked=False)
    assert not rec.passed and "paragraph properties" in v10(rec)


def test_an_import_left_out_of_the_assertions_is_caught(src, tpl):
    """styleimport.check_requested: the applied style comes from the template, so this op's styles_imported assertion
    must check its import — a plan that drops it fails even though the output is right."""
    plan, out, rec = applying(src, PARTS, style="PRT", template=tpl)
    assert rec.passed
    d = plan.to_dict()
    d["property_assertions"] = [a for a in d["property_assertions"] if a["kind"] != "styles_imported"]
    rec2 = run_suite(src, out, d, "src.docx", "out.docx")
    assert not rec2.passed and "styles_imported" in v10(rec2)


# ---- plan-time conflicts (rule 9) ------------------------------------------------------------------------------------

def _spec(*ops):
    return {"request": "r", "summary": "s", "mode": "formatting", "ops": list(ops)}


def test_restyling_beside_formatting_that_reads_the_style_conflicts(src):
    """set_properties reads each selected paragraph's style chain (verify:pStyle) — a restyle of the same paragraph is a
    plan-time conflict, also where set_properties finds the run already as asked."""
    sel = {"paragraphs": {"text_regex": "^PART 2"}}
    apply_op = {"op": "apply_style", "select": sel, "params": {"style": "Heading1"}}
    for other in ({"op": "set_properties", "select": sel, "params": {"run": {"b": True}}},
                  {"op": "set_tab_stops", "select": sel, "params": {"set": [{"pos": 720}]}},
                  {"op": "set_borders_shading", "select": sel, "params": {"shading": {"fill": "D9D9D9"}}}):
        with pytest.raises(JobException) as e:
            plan_spec(DocxPackage(src), _spec(apply_op, other))
        assert e.value.code == "conflict", other
    plan, frags = plan_spec(DocxPackage(src), _spec(apply_op, {"op": "set_properties",
                                                               "select": {"paragraphs": {"text_regex": "^Coordinate"}},
                                                               "params": {"run": {"i": True}}}))
    assert len(frags) == 2


def test_removing_the_applied_style_beside_conflicts(src):
    pkg = DocxPackage(src)
    with pytest.raises(JobException) as e:
        plan_spec(pkg, _spec({"op": "apply_style", "select": PARTS, "params": {"style": "ArchUnused"}},
                             {"op": "remove_unused_styles", "params": {"which": "custom"}}))
    assert e.value.code == "conflict" and "ArchUnused" in e.value.reason


# ---- the template is an input (rule 12) ------------------------------------------------------------------------------

def test_a_template_changed_after_planning_is_refused_and_fails_v10(src, tpl):
    pkg = DocxPackage(src)
    frag = get_op("apply_style").plan(pkg, PARTS, {"style": "PRT", "template": tpl})
    plan = merge_fragments([frag])
    work = pkg.copy()
    get_op("apply_style").apply(work, frag)                       # the template as planned (the builder is deterministic)
    out = work.output_bytes()

    def touch(root):                                               # an unrelated style added: the file is another file
        extra = etree.SubElement(root, W + "style")
        extra.set(W + "type", "paragraph")
        extra.set(W + "styleId", "AddedLater")
    Path(tpl).write_bytes(mutate(build_style_template(), STYLES, touch))
    with pytest.raises(JobException):
        get_op("apply_style").apply(pkg.copy(), frag)
    rec = run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")
    assert not rec.passed and "template" in v10(rec)


# ---- parameters and refusals -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("params, code", [
    ({}, "bad-params"),
    ({"style": ""}, "bad-params"),
    ({"style": "Heading1", "direct_formatting": "wipe"}, "bad-params"),
    ({"style": "Heading1", "existing": "replace"}, "bad-params"),        # existing without a template
    ({"style": "Heading1", "colour": "red"}, "bad-params"),
    ({"style": "Heading1", "tracked": "yes"}, "bad-params"),
    ({"style": "NoSuchStyle"}, "missing-style"),
    ({"style": "ArchStrong"}, "style-mismatch"),                         # a character style
])
def test_parameters_and_refusals(src, params, code):
    with pytest.raises(JobException) as e:
        get_op("apply_style").plan(DocxPackage(src), PARTS, params)
    assert e.value.code == code


def test_template_refusals(src, tpl):
    for params in ({"style": "NotInTemplate", "template": tpl}, {"style": "CharMine", "template": tpl}):
        with pytest.raises(JobException) as e:
            get_op("apply_style").plan(DocxPackage(src), PARTS, params)
        assert e.value.code == "bad-params", params
    with pytest.raises(JobException) as e:                               # a name another style here already has
        get_op("apply_style").plan(DocxPackage(src), PARTS, {"style": "MyBody", "template": tpl})
    assert e.value.code == "style-import"
    with pytest.raises(JobException) as e:                               # a picture-bullet list
        get_op("apply_style").plan(DocxPackage(src), PARTS, {"style": "PicList", "template": tpl})
    assert e.value.code == "style-numbering"


def test_catalog_and_explanations():
    cat = op_catalog()
    assert "apply_style" in cat["ops"] and COMMON_ASSUMPTIONS["apply_style"]
    for code in ("style-mismatch", "style-import", "style-numbering", "missing-style"):
        assert code in FAILURE_EXPLANATIONS
