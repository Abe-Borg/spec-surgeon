"""The shared style import (built-in batch 4, docs/BUILTIN_OPS_BATCH4_DESIGN.md §5): ops/style_import.py ↔
verify/styleimport.py (V10 kind ``styles_imported``). Driven through map_styles (the first op over it) and directly
through the API apply_style reuses: the closure (basedOn / link / next / numbering-level pStyles), imported vs replaced
styles, numbering definitions copied with new ids, every refusal; a wrong OUTPUT per rule; wrong IMPLEMENTATIONS shared
by plan and apply; the template as a pinned input (rule 12); the two sides' resolvers and derivations compared."""
from __future__ import annotations

import copy
import io
import zipfile
from pathlib import Path
from typing import List, Tuple

import pytest
from lxml import etree

from make_fixtures import build_style_template, build_styles
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import style_import as imp_module
from spec_surgeon.ops.style_import import TemplateStyles, apply_import, plan_import, resolve_style
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.verify import run_suite
from spec_surgeon.verify.props import Styles, check_properties
from spec_surgeon.verify.reader import VPackage
from spec_surgeon.verify.restyle import v_resolve_style
from spec_surgeon.verify.styleimport import v_derive_import, v_used_ids

STYLES = "word/styles.xml"
NUMBERING = "word/numbering.xml"
W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
PRT_FAMILY = ["PRT", "ART", "PR1", "PR2", "PRTChar"]


@pytest.fixture(scope="module")
def src() -> bytes:
    return build_styles()


@pytest.fixture(scope="module")
def tpl_bytes() -> bytes:
    return build_style_template()


@pytest.fixture()
def tpl(tmp_path, tpl_bytes) -> str:
    p = tmp_path / "OFFICE STYLES.docx"
    p.write_bytes(tpl_bytes)
    return str(p)


def mutate(docx: bytes, part: str, fn) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(docx))
    root = etree.fromstring(zin.read(part))
    fn(root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            z.writestr(info, serialize_xml(root) if info.filename == part else zin.read(info.filename))
    return buf.getvalue()


def style_of(root, sid):
    return next(st for st in root.findall(W + "style") if st.get(W + "styleId") == sid)


def ids_of(root) -> List[str]:
    return [st.get(W + "styleId") for st in root.findall(W + "style")]


def shape(el):
    """Tag, attributes, text and children — namespace declarations ignored."""
    return el.tag, sorted(el.attrib.items()), (el.text or "").strip(), [shape(c) for c in el]


def run_ops(data: bytes, specs: List[Tuple[str, dict, dict]]):
    pkg = DocxPackage(data)
    frags = [get_op(o).plan(pkg, sel, par) for o, sel, par in specs]
    plan = merge_fragments(frags)
    work = pkg.copy()
    for f in frags:
        get_op(f.op).apply(work, f)
    out = work.output_bytes()
    return plan, out, run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")


def mapping(data: bytes, pairs, **params):
    return run_ops(data, [("map_styles", {}, {"map": [{"from": f, "to": t} for f, t in pairs], **params})])


def failed(rec):
    return [c.id for c in rec.checks if c.status == "FAIL"]


def v10(rec) -> str:
    c = next(c for c in rec.checks if c.id == "V10")
    return c.summary + " | " + " | ".join(c.data.get("errors", []))


def imported_assertion(plan):
    return next(a for a in plan.property_assertions if a["kind"] == "styles_imported")


def with_extra_list(data: bytes) -> bytes:
    """The architect's spec with one more list (num 12 → abstractNum 0): new numIds now start at 13, so a numId left as
    the template's 10 shows."""
    def add(root):
        root.append(etree.fromstring(f'<w:num xmlns:w="{W[1:-1]}" w:numId="12"><w:abstractNumId w:val="0"/></w:num>'))
    return mutate(data, NUMBERING, add)


def tpl_variant(tmp_path, tpl_bytes, part, fn) -> str:
    p = tmp_path / "VARIANT STYLES.docx"
    p.write_bytes(mutate(tpl_bytes, part, fn))
    return str(p)


# ---- end to end ------------------------------------------------------------------------------------------------------

def test_a_numbered_style_brings_its_family_and_its_list(src, tpl, tpl_bytes):
    """Heading 1 → PRT: PRT links PRTChar and hands over to ART; its list's levels name ART, PR1 and PR2 (PR2 based on
    PR1) — all imported, in the template's order, appended; num 10 → 10 (the file's lists end at 9), abstractNum 10 → 9;
    Normal and DefaultParagraphFont are the file's own (flagged: Normal differs)."""
    plan, out, rec = mapping(src, [("Heading 1", "PRT")], template=tpl)
    assert rec.passed, rec.to_markdown()
    a = imported_assertion(plan)
    assert a["requested"] == ["PRT"] and a["imported"] == PRT_FAMILY and a["replaced"] == []
    assert a["num_map"] == {"10": "10"} and a["abs_map"] == {"10": "9"} and a["template_md5"]
    o, s = DocxPackage(out).xml(STYLES), DocxPackage(src).xml(STYLES)
    assert ids_of(o) == ids_of(s) + PRT_FAMILY
    t = DocxPackage(tpl_bytes).xml(STYLES)
    for sid in PRT_FAMILY:                                     # the template's definitions (num 10 stays 10 here)
        assert shape(style_of(o, sid)) == shape(style_of(t, sid)), sid
    num = DocxPackage(out).xml(NUMBERING)
    assert [c.get(W + "abstractNumId") for c in num.findall(W + "abstractNum")] == [str(i) for i in range(10)]
    assert [c.get(W + "numId") for c in num.findall(W + "num")] == [str(i) for i in range(1, 11)]
    new_num = num.findall(W + "num")[-1]
    assert new_num.find(W + "abstractNumId").get(W + "val") == "9"
    assert set(plan.intended_parts) == {"word/document.xml", STYLES, NUMBERING}
    assert any("PRT renders on this file's own Normal" in f for f in plan.flags)
    assert any("stylesWithEffects" in f for f in plan.flags)


def test_new_list_ids_go_above_the_files_largest(src, tpl):
    data = with_extra_list(src)
    plan, out, rec = mapping(data, [("Heading 1", "PRT")], template=tpl)
    assert rec.passed, rec.to_markdown()
    a = imported_assertion(plan)
    assert a["num_map"] == {"10": "13"} and a["abs_map"] == {"10": "9"}
    o = DocxPackage(out).xml(STYLES)
    for sid in ("PRT", "ART", "PR1", "PR2"):
        assert style_of(o, sid).find(f"{W}pPr/{W}numPr/{W}numId").get(W + "val") == "13"
    num = DocxPackage(out).xml(NUMBERING)
    assert [c.get(W + "numId") for c in num.findall(W + "num")][-2:] == ["12", "13"]


def test_a_plain_style_brings_only_what_it_is_based_on(src, tpl):
    plan, out, rec = mapping(src, [("Normal", "CMT")], template=tpl)
    assert rec.passed, rec.to_markdown()
    a = imported_assertion(plan)
    assert a["imported"] == ["CMTBase", "CMT"] and a["num_map"] == {} and NUMBERING not in plan.intended_parts
    assert DocxPackage(out).read(NUMBERING) == DocxPackage(src).read(NUMBERING)


def test_keep_uses_the_files_own_definition_and_replace_takes_its_place(src, tpl, tpl_bytes):
    plan, out, rec = mapping(src, [("ArchArticle", "Heading 1")], template=tpl)
    assert rec.passed, rec.to_markdown()
    assert not any(a["kind"] == "styles_imported" for a in plan.property_assertions)
    assert DocxPackage(out).read(STYLES) == DocxPackage(src).read(STYLES)
    plan, out, rec = mapping(src, [("ArchArticle", "Heading 1")], template=tpl, existing="replace")
    assert rec.passed, rec.to_markdown()
    a = imported_assertion(plan)
    assert a["replaced"] == ["Heading1"] and a["imported"] == []
    o, s, t = DocxPackage(out).xml(STYLES), DocxPackage(src).xml(STYLES), DocxPackage(tpl_bytes).xml(STYLES)
    assert ids_of(o) == ids_of(s)                                                   # in its old place
    assert style_of(o, "Heading1").find(f"{W}rPr/{W}rFonts").get(W + "ascii") == "Arial"
    assert style_of(t, "Heading1").find(f"{W}rPr/{W}rFonts").get(W + "ascii") == "Arial"
    touches = set(map(tuple, get_op("map_styles").plan(DocxPackage(src), {}, {
        "map": [{"from": "ArchArticle", "to": "Heading 1"}], "template": tpl, "existing": "replace"}).touches))
    assert {(STYLES, "style:Heading1", a_) for a_ in ("replace", "rPr", "pPr", "tblPr")} <= touches


def test_the_import_touches(src, tpl):
    f = get_op("map_styles").plan(DocxPackage(src), {}, {"map": [{"from": "Heading 1", "to": "PRT"}], "template": tpl})
    t = set(map(tuple, f.touches))
    assert {(STYLES, f"style:{sid}", "add") for sid in PRT_FAMILY} <= t
    assert (NUMBERING, "*", "add") in t
    assert {(STYLES, "style:Normal", "verify"), (STYLES, "style:DefaultParagraphFont", "verify")} <= t


def _removing(*ids):
    """A fragment removing styles as remove_unused_styles declares it: (styles, "style:<id>", a) for a in remove, rPr,
    pPr, tblPr."""
    from spec_surgeon.ops.base import PlanFragment
    f = PlanFragment(job="remove_unused_styles", mode="formatting", params={}, intended_parts=[STYLES],
                     op="remove_unused_styles")
    f.touches = [(STYLES, f"style:{sid}", a) for sid in ids for a in ("remove", "rPr", "pPr", "tblPr")]
    return f


def test_removing_a_style_an_imported_copy_relies_on_conflicts(src, tpl):
    """PRT is imported based on the file's own Normal and PRTChar on its DefaultParagraphFont (and a list level could name
    a style the file has): another op removing — or changing — one of those in the same spec is refused at plan time,
    never a dangling basedOn / a V10 failure after apply. A removal of a style nothing imported relies on still plans."""
    from spec_surgeon.ops import check_conflicts
    pkg = DocxPackage(src)
    for held in ("Normal", "DefaultParagraphFont"):
        m = get_op("map_styles").plan(pkg, {}, {"map": [{"from": "Heading 1", "to": "PRT"}], "template": tpl})
        other = _removing(held)
        for i, f in enumerate((m, other)):
            f.op_index = i
        with pytest.raises(JobException) as e:
            check_conflicts([m, other])
        assert e.value.code == "conflict" and f"style:{held}" in str(e.value), str(e.value)
    # a style held only through an imported LIST LEVEL: the file has its own PR1 — PRT's list level 2 names it
    def own_pr1(r):
        r.append(etree.fromstring(f'<w:style xmlns:w="{W[1:-1]}" w:type="paragraph" w:customStyle="1" '
                                  f'w:styleId="PR1"><w:name w:val="PR1"/><w:basedOn w:val="Normal"/></w:style>'))
    data = mutate(src, STYLES, own_pr1)
    m = get_op("map_styles").plan(DocxPackage(data), {}, {"map": [{"from": "Heading 1", "to": "PRT"}], "template": tpl})
    assert "PR1" not in imported_assertion(m)["imported"] and (STYLES, "style:PR1", "verify") in set(map(tuple, m.touches))
    other = _removing("PR1")
    m.op_index, other.op_index = 0, 1
    with pytest.raises(JobException) as e:
        check_conflicts([m, other])
    assert e.value.code == "conflict"
    # removing a style no copy names (ArchUnused) beside the import: no conflict
    m = get_op("map_styles").plan(pkg, {}, {"map": [{"from": "Heading 1", "to": "PRT"}], "template": tpl})
    other = _removing("ArchUnused")
    m.op_index, other.op_index = 0, 1
    check_conflicts([m, other])
    # and a style-definition change of the file's Normal beside the import is refused too (every family is read)
    from spec_surgeon.editspec import plan_spec
    with pytest.raises(JobException) as e:
        plan_spec(pkg, {"request": "r", "summary": "s", "mode": "formatting", "ops": [
            {"op": "map_styles", "params": {"map": [{"from": "Heading 1", "to": "PRT"}], "template": tpl}},
            {"op": "set_properties", "select": {"styles": {"ids": ["Normal"]}}, "params": {"paragraph": {"keepLines": True}}}]})
    assert e.value.code == "conflict"


# ---- the API apply_style reuses ---------------------------------------------------------------------------------------

def test_the_import_api_and_its_kind_need_nothing_from_the_op(src, tpl):
    pkg = DocxPackage(src)
    t = TemplateStyles.load(tpl)
    assert resolve_style(t.sheet.styles, "prt char", "the template") == "PRTChar"
    imp = plan_import(pkg, t, ["ART"], "keep")
    assert imp.changes and imp.imported == PRT_FAMILY and sorted(imp.held) == ["DefaultParagraphFont", "Normal"]
    assert imp.intended_parts() == [STYLES, NUMBERING]
    assert imp.assertion()["kind"] == "styles_imported" and imp.transform_data()["num_map"] == {"10": "10"}
    work = pkg.copy()
    apply_import(work, imp.transform_data())
    out = work.output_bytes()
    ok, summary, data = check_properties(VPackage(src), VPackage(out), {"property_assertions": [imp.assertion()]})
    assert ok, data
    assert "imported ['PRT', 'ART', 'PR1', 'PR2', 'PRTChar']" in summary
    # nothing requested: no change, nothing to assert
    none = plan_import(pkg, t, [], "keep")
    assert not none.changes and none.touches() == [] and none.intended_parts() == []


def test_the_kind_alone_fails_a_wrong_import(src, tpl):
    pkg = DocxPackage(src)
    imp = plan_import(pkg, TemplateStyles.load(tpl), ["CMT"], "keep")
    work = pkg.copy()
    apply_import(work, imp.transform_data())
    out = mutate(work.output_bytes(), STYLES, lambda r: style_of(r, "CMT").find(W + "rPr").append(etree.Element(W + "b")))
    ok, summary, data = check_properties(VPackage(src), VPackage(out), {"property_assertions": [imp.assertion()]})
    assert not ok and any("CMT is not the template's definition" in e for e in data["errors"])


# ---- refusals ----------------------------------------------------------------------------------------------------

def _refused(src, tpl, pairs, code, **params):
    with pytest.raises(JobException) as e:
        get_op("map_styles").plan(DocxPackage(src), {}, {"map": [{"from": f, "to": t} for f, t in pairs],
                                                         "template": tpl, **params})
    assert e.value.code == code, (e.value.code, str(e.value))
    return str(e.value)


def test_style_import_refusals(src, tpl, tmp_path, tpl_bytes):
    # a name another style holds: MyBody is "Body Text", the file's BodyText too
    assert "Body Text" in _refused(src, tpl, [("ArchPara", "MyBody")], "style-import")
    # replacing the file's default paragraph style
    assert "default" in _refused(src, tpl, [("ArchArticle", "Normal")], "style-import", existing="replace")
    # an imported style that is a default in the template
    dflt = tpl_variant(tmp_path, tpl_bytes, STYLES, lambda r: style_of(r, "SpecNote").set(W + "default", "1"))
    assert "default" in _refused(src, dflt, [("ArchNote", "SpecNote")], "style-import")
    # an extension element carrying text
    def ext_text(r):
        e = etree.SubElement(style_of(r, "SpecNote"), f"{{{W14}}}note")
        etree.SubElement(etree.SubElement(e, W + "r"), W + "t").text = "hidden words"
    ext = tpl_variant(tmp_path, tpl_bytes, STYLES, ext_text)
    assert "carries text" in _refused(src, ext, [("ArchNote", "SpecNote")], "style-import")
    # an extension element without text is stripped from the copy, and the import passes
    def ext_plain(r):
        etree.SubElement(style_of(r, "SpecNote"), f"{{{W14}}}note").set(f"{{{W14}}}x", "1")
        style_of(r, "SpecNote").set(f"{{{W14}}}tag", "y")
    plain = tpl_variant(tmp_path, tpl_bytes, STYLES, ext_plain)
    plan, out, rec = mapping(src, [("ArchNote", "SpecNote")], template=plain)
    assert rec.passed, rec.to_markdown()
    got = style_of(DocxPackage(out).xml(STYLES), "SpecNote")
    assert got.find(f"{{{W14}}}note") is None and f"{{{W14}}}tag" not in got.attrib
    # replacing a style with a definition of another type
    def char_mine_paragraph(r):
        r.append(etree.fromstring(f'<w:style xmlns:w="{W[1:-1]}" w:type="paragraph" w:styleId="CharMine">'
                                  f'<w:name w:val="Char Mine Para"/></w:style>'))
    other = mutate(src, STYLES, char_mine_paragraph)
    assert "wrong kind" in _refused(other, tpl, [("ArchStrong", "CharMine")], "style-import", existing="replace")


def test_style_numbering_refusals(src, tpl, tmp_path, tpl_bytes):
    assert "picture bullet" in _refused(src, tpl, [("ArchNote", "PicList")], "style-numbering")
    assert "numbering style" in _refused(src, tpl, [("ArchNote", "LinkList")], "style-numbering")

    def num_gone(r):
        r.remove(next(n for n in r.findall(W + "num") if n.get(W + "numId") == "10"))
    _refused(src, tpl_variant(tmp_path, tpl_bytes, NUMBERING, num_gone), [("Heading 1", "PRT")], "style-numbering")

    def abs_gone(r):
        r.remove(next(n for n in r.findall(W + "abstractNum") if n.get(W + "abstractNumId") == "10"))
    _refused(src, tpl_variant(tmp_path, tpl_bytes, NUMBERING, abs_gone), [("Heading 1", "PRT")], "style-numbering")

    def override_picture(r):
        num = next(n for n in r.findall(W + "num") if n.get(W + "numId") == "10")
        ov = etree.SubElement(num, W + "lvlOverride", {W + "ilvl": "0"})
        lvl = etree.SubElement(ov, W + "lvl", {W + "ilvl": "0"})
        etree.SubElement(lvl, W + "lvlPicBulletId", {W + "val": "0"})
    assert "picture bullet" in _refused(src, tpl_variant(tmp_path, tpl_bytes, NUMBERING, override_picture),
                                        [("Heading 1", "PRT")], "style-numbering")

    def bad_num_id(r):
        style_of(r, "PR1").find(f"{W}pPr/{W}numPr/{W}numId").set(W + "val", "x")
    assert "whole number" in _refused(src, tpl_variant(tmp_path, tpl_bytes, STYLES, bad_num_id), [("Heading 1", "PRT")],
                                      "style-numbering")

    def no_numbering_rel(r):
        for rel in list(r):
            if rel.get("Type", "").endswith("/numbering"):
                r.remove(rel)
    bare = mutate(src, "word/_rels/document.xml.rels", no_numbering_rel)
    assert "no numbering part" in _refused(bare, tpl, [("Heading 1", "PRT")], "style-numbering")
    plan, out, rec = mapping(bare, [("Normal", "CMT")], template=tpl)           # an unnumbered style still imports
    assert rec.passed, rec.to_markdown()


def test_the_template_must_be_usable(src, tmp_path):
    with pytest.raises(JobException) as e:
        get_op("map_styles").plan(DocxPackage(src), {}, {"map": [{"from": "Heading 1", "to": "PRT"}],
                                                         "template": str(tmp_path / "nope.docx")})
    assert e.value.code == "bad-params"
    junk = tmp_path / "junk.docx"
    junk.write_bytes(b"not a zip")
    with pytest.raises(JobException) as e:
        get_op("map_styles").plan(DocxPackage(src), {}, {"map": [{"from": "Heading 1", "to": "PRT"}],
                                                         "template": str(junk)})
    assert e.value.code == "bad-params"
    with pytest.raises(JobException) as e:
        plan_import(DocxPackage(src), TemplateStyles.from_bytes(build_style_template(), "t.docx"), ["Nope"], "keep")
    assert e.value.code == "bad-params"
    with pytest.raises(JobException) as e:
        plan_import(DocxPackage(src), TemplateStyles.from_bytes(build_style_template(), "t.docx"), ["PRT"], "merge")
    assert e.value.code == "bad-params"


# ---- a wrong output per rule ----------------------------------------------------------------------------------------

def _caught(src, plan, out, part, fn, needle=None):
    bad = mutate(out, part, fn)
    rec = run_suite(src, bad, plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec), rec.to_markdown()
    if needle:
        assert needle in v10(rec), v10(rec)


def test_wrong_outputs_are_caught(src, tpl, tpl_bytes):
    data = with_extra_list(src)
    plan, out, rec = mapping(data, [("Heading 1", "PRT")], template=tpl)
    assert rec.passed
    # an imported style altered
    _caught(data, plan, out, STYLES, lambda r: style_of(r, "PRT").find(W + "rPr").remove(
        style_of(r, "PRT").find(W + "rPr")[0]), "PRT is not the template's definition")
    # a style imported that should not be
    spec_note = style_of(DocxPackage(tpl_bytes).xml(STYLES), "SpecNote")
    _caught(data, plan, out, STYLES, lambda r: r.append(copy.deepcopy(spec_note)), "unpredicted change")
    # an imported style twice
    _caught(data, plan, out, STYLES, lambda r: r.append(copy.deepcopy(style_of(r, "PR1"))), "occurs 2 time(s)")
    # an imported style not appended
    def before(r):
        r.insert(list(r).index(style_of(r, "Heading1")), style_of(r, "PRTChar"))
    _caught(data, plan, out, STYLES, before, "not appended")
    # imported styles out of the template's order
    def swap(r):
        r.append(style_of(r, "PR1"))
    _caught(data, plan, out, STYLES, swap, "template's order")
    # a numId not remapped (13 in the plan, the template's 10 left in a copy)
    _caught(data, plan, out, STYLES, lambda r: style_of(r, "ART").find(f"{W}pPr/{W}numPr/{W}numId").set(W + "val", "10"),
            "ART is not the template's definition")
    # an abstractNum copied with the wrong id
    _caught(data, plan, out, NUMBERING, lambda r: r.findall(W + "abstractNum")[-1].set(W + "abstractNumId", "10"),
            "numbering.xml is not the source's")
    # a num inserted in the wrong place
    def misplaced(r):
        last = r.findall(W + "num")[-1]
        r.insert(list(r).index(r.findall(W + "num")[0]), last)
    _caught(data, plan, out, NUMBERING, misplaced, "numbering.xml is not the source's")
    # an extra numbering element
    _caught(data, plan, out, NUMBERING, lambda r: r.append(copy.deepcopy(r.findall(W + "num")[-1])),
            "numbering.xml is not the source's")
    # a style the copies rely on (PRTChar is based on DefaultParagraphFont) gone from the output
    _caught(data, plan, out, STYLES, lambda r: r.remove(style_of(r, "DefaultParagraphFont")), "the output lacks")
    # the new num pointing at the template's abstractNum id
    _caught(data, plan, out, NUMBERING, lambda r: r.findall(W + "num")[-1].find(W + "abstractNumId").set(W + "val", "10"),
            "numbering.xml is not the source's")


def test_a_replaced_style_moved_to_the_end_is_caught(src, tpl):
    plan, out, rec = mapping(src, [("ArchArticle", "Heading 1")], template=tpl, existing="replace")
    assert rec.passed
    _caught(src, plan, out, STYLES, lambda r: r.append(style_of(r, "Heading1")), "their own order")
    _caught(src, plan, out, STYLES, lambda r: style_of(r, "Heading1").find(W + "pPr").append(etree.Element(W + "keepLines")),
            "replaced style Heading1 is not the template's definition")


# ---- wrong implementations shared by plan and apply ------------------------------------------------------------------

def test_a_shared_closure_that_forgets_link_and_next_is_caught(src, tpl, monkeypatch):
    monkeypatch.setattr(imp_module, "style_links",
                        lambda st: [e.get(W + "val") for e in [st.find(W + "basedOn")] if e is not None])
    plan, out, rec = mapping(src, [("Heading 1", "PRT")], template=tpl)
    assert "PRTChar" not in imported_assertion(plan)["imported"]
    assert "V10" in failed(rec) and "imported" in v10(rec)


def test_a_shared_closure_that_forgets_the_list_levels_is_caught(src, tpl, monkeypatch):
    monkeypatch.setattr(imp_module, "numbering_styles", lambda num, abstract: [])
    plan, out, rec = mapping(src, [("Heading 1", "PRT")], template=tpl)
    assert imported_assertion(plan)["imported"] == ["PRT", "ART", "PR1", "PRTChar"]   # PR1 still ART's next; PR2 lost
    assert "V10" in failed(rec) and "imported" in v10(rec)


def test_a_shared_allocator_that_reuses_a_list_id_is_caught(src, tpl, monkeypatch):
    real = imp_module.next_ids
    monkeypatch.setattr(imp_module, "next_ids", lambda pkg, root: tuple(n - 1 for n in real(pkg, root)))
    plan, out, rec = mapping(src, [("Heading 1", "PRT")], template=tpl)
    assert imported_assertion(plan)["num_map"] == {"10": "9"}                          # num 9 exists already
    assert "V10" in failed(rec) and "list ids" in v10(rec)


def test_a_shared_closure_that_walks_through_held_styles_is_caught(src, tpl, monkeypatch):
    """A style the file keeps (Normal) is not copied, so its template links do not matter; walking them anyway would
    import copies nobody asked for — here the template's Normal hands over to SpecNote."""
    Path(tpl).write_bytes(mutate(Path(tpl).read_bytes(), STYLES, lambda r: etree.SubElement(
        style_of(r, "Normal"), W + "next", {W + "val": "SpecNote"})))
    pkg = DocxPackage(src)
    honest = plan_import(pkg, TemplateStyles.load(tpl), ["CMT"], "keep")
    assert honest.imported == ["CMTBase", "CMT"] and honest.held == ["Normal"]
    real = imp_module.closure_of
    monkeypatch.setattr(imp_module, "closure_of", lambda tsheet, requested, copied, tnum:
                        real(tsheet, requested, lambda sid: True, tnum))
    imp = plan_import(pkg, TemplateStyles.load(tpl), ["CMT"], "keep")
    assert "SpecNote" in imp.imported
    work = pkg.copy()
    apply_import(work, imp.transform_data())
    ok, _, data = check_properties(VPackage(src), VPackage(work.output_bytes()), {"property_assertions": [imp.assertion()]})
    assert not ok and any("imported" in e for e in data["errors"])


# ---- the template is an input (rule 12) -----------------------------------------------------------------------------

def test_a_template_changed_between_plan_and_apply_is_refused(src, tpl):
    pkg = DocxPackage(src)
    f = get_op("map_styles").plan(pkg, {}, {"map": [{"from": "Heading 1", "to": "PRT"}], "template": tpl})
    Path(tpl).write_bytes(mutate(Path(tpl).read_bytes(), STYLES,
                                 lambda r: style_of(r, "PRT").find(W + "rPr").append(etree.Element(W + "b"))))
    with pytest.raises(JobException) as e:
        get_op("map_styles").apply(pkg.copy(), f)
    assert e.value.code == "internal" and "changed between plan and apply" in str(e.value)


def test_v10_fails_on_a_changed_template(src, tpl):
    plan, out, rec = mapping(src, [("Heading 1", "PRT")], template=tpl)
    assert rec.passed
    Path(tpl).write_bytes(mutate(Path(tpl).read_bytes(), STYLES,
                                 lambda r: style_of(r, "PRT").find(W + "rPr").append(etree.Element(W + "b"))))
    rec = run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec) and "template file changed since planning" in v10(rec)


def test_the_template_is_an_input_file_of_the_spec(src, tpl, tmp_path):
    from spec_surgeon.editspec import load_spec, spec_input_files
    import json
    spec = {"request": "map my styles", "summary": "s", "mode": "formatting",
            "ops": [{"op": "map_styles", "params": {"map": [{"from": "Heading 1", "to": "PRT"}],
                                                    "template": "OFFICE STYLES.docx"}}]}
    (tmp_path / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    loaded = load_spec(tmp_path / "spec.json")
    assert Path(loaded["ops"][0]["params"]["template"]) == Path(tpl).resolve()
    assert Path(tpl).resolve() in spec_input_files(loaded)


# ---- fix round (adversarial review): numbering untouched, one id two styles, names, list ids, levels, mc, template ----

def zip_with(docx: bytes, part: str, data: bytes) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(docx))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            z.writestr(info, data if info.filename == part else zin.read(info.filename))
    return buf.getvalue()


def plan_or_code(data: bytes, ops):
    from spec_surgeon.editspec import plan_spec
    try:
        return plan_spec(DocxPackage(data), {"request": "r", "summary": "s", "mode": "formatting",
                                             "ops": [{"op": o, "select": s, "params": p} for o, s, p in ops]})
    except JobException as e:
        return e.code, str(e)


def test_c1_numbering_changed_without_a_list_copied_fails(src, tpl, monkeypatch):
    """A shared defect: the import names numbering.xml as intended and rewrites list 1 although it copies no list
    (CMT is unnumbered). numbering.xml is under no residual: styles_imported compares it whole anyway."""
    orig_ip, orig_apply = imp_module.StyleImport.intended_parts, imp_module.apply_import

    def ip(self):
        out = orig_ip(self)
        return out + ([self.numbering_part] if self.numbering_part and self.numbering_part not in out else [])

    def ap(pkg, data):
        orig_apply(pkg, data)
        if data and data.get("numbering_part"):
            r = pkg.xml(data["numbering_part"])
            r.findall(W + "num")[0].find(W + "abstractNumId").set(W + "val", "5")
            pkg.write_xml(data["numbering_part"], r)
    monkeypatch.setattr(imp_module.StyleImport, "intended_parts", ip)
    import spec_surgeon.ops.map_styles as map_module
    monkeypatch.setattr(map_module, "apply_import", ap)
    plan, out, rec = mapping(src, [("Normal", "CMT")], template=tpl)
    assert NUMBERING in plan.intended_parts and imported_assertion(plan)["num_map"] == {}
    assert "V10" in failed(rec) and "changed although no list is copied" in v10(rec)


def test_c1_numbering_changed_by_an_op_that_imports_nothing_fails(src, tpl):
    """map_styles without any import (the file's own Heading 2): its kind compares numbering.xml whole too."""
    plan, out, rec = mapping(src, [("ArchNote", "Heading 2")])
    assert rec.passed
    bad = mutate(out, NUMBERING, lambda r: r.findall(W + "num")[0].find(W + "abstractNumId").set(W + "val", "5"))
    rec = run_suite(src, bad, plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec) and "changed although no list is copied" in v10(rec)


def test_c1_apply_style_without_an_import_is_covered_too(src):
    """apply_style with the file's own style: its kind calls check_requested with nothing requested, which compares
    numbering.xml whole as well."""
    plan, out, rec = run_ops(src, [("apply_style", {"paragraphs": {"text_regex": "^NOTE TO"}}, {"style": "Heading 2"})])
    assert rec.passed, rec.to_markdown()
    bad = mutate(out, NUMBERING, lambda r: r.findall(W + "num")[0].find(W + "abstractNumId").set(W + "val", "5"))
    rec = run_suite(src, bad, plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec) and "changed although no list is copied" in v10(rec)


def test_c1_an_import_without_lists_beside_one_with_lists_passes(src, tpl):
    """One op copies a list, the other none: the list-copying op's check owns numbering.xml (expected = source + its
    lists); the other does not demand an unchanged part."""
    from spec_surgeon.editspec import apply_spec, plan_spec
    spec = {"request": "r", "summary": "s", "mode": "formatting", "ops": [
        {"op": "map_styles", "params": {"map": [{"from": "ArchNote", "to": "SpecNote"}], "template": tpl}},
        {"op": "apply_style", "select": {"paragraphs": {"text_regex": "^Coordinate"}},
         "params": {"style": "PRT", "template": tpl}}]}
    plan, frags = plan_spec(DocxPackage(src), spec)
    out = apply_spec(DocxPackage(src), frags).output_bytes()
    rec = run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")
    assert rec.passed, rec.to_markdown()
    assert [a["num_map"] for a in plan.property_assertions if a["kind"] == "styles_imported"] == [{}, {"10": "10"}]


def _same_id_other_style(src, tpl_bytes, tmp_path):
    def tfix(root):
        style_of(root, "CMT").find(W + "name").set(W + "val", "Office Comment")
    t = tmp_path / "OFFICE_N.docx"
    t.write_bytes(mutate(tpl_bytes, STYLES, tfix))
    d = mutate(src, STYLES, lambda r: r.append(etree.fromstring(
        f'<w:style xmlns:w="{W[1:-1]}" w:type="paragraph" w:customStyle="1" w:styleId="CMT"><w:name '
        f'w:val="Architect Callout"/><w:basedOn w:val="Normal"/><w:rPr><w:color w:val="FF0000"/></w:rPr></w:style>')))
    return d, str(t)


def test_c2_a_template_id_the_file_uses_for_another_style_is_refused(src, tpl_bytes, tmp_path):
    d, t = _same_id_other_style(src, tpl_bytes, tmp_path)
    for ops in ([("map_styles", {}, {"map": [{"from": "ArchNote", "to": "Office Comment"}], "template": t})],
                [("apply_style", {"paragraphs": {"text_regex": "^NOTE TO"}}, {"style": "Office Comment", "template": t})]):
        code, reason = plan_or_code(d, ops)
        assert code == "style-import" and "'Office Comment'" in reason and "'Architect Callout'" in reason, reason
    # the same style under one id, its name written with other spacing and case: names resolve whitespace-collapsed
    # and casefolded, so "Office Comment" is the file's own CMT — mapped to, nothing imported
    d2 = mutate(d, STYLES, lambda r: style_of(r, "CMT").find(W + "name").set(W + "val", "office   COMMENT"))
    plan, out, rec = mapping(d2, [("ArchNote", "Office Comment")], template=t)
    assert rec.passed, rec.to_markdown()
    assert not any(a["kind"] == "styles_imported" for a in plan.property_assertions)
    assert any("ArchNote → CMT (paragraph; this file's own definition)" in n for n in plan.notes)


def test_c2_held_closure_style_under_one_id_is_refused_too(src, tpl_bytes, tmp_path):
    """CMT (imported) is based on CMTBase; the file has its own CMTBase that is another style: refused."""
    d = mutate(src, STYLES, lambda r: r.append(etree.fromstring(
        f'<w:style xmlns:w="{W[1:-1]}" w:type="paragraph" w:customStyle="1" w:styleId="CMTBase"><w:name '
        f'w:val="Callout Base"/></w:style>')))
    t = tmp_path / "T.docx"
    t.write_bytes(tpl_bytes)
    code, reason = plan_or_code(d, [("map_styles", {}, {"map": [{"from": "Normal", "to": "CMT"}], "template": str(t)})])
    assert code == "style-import" and "CMTBase" in reason


def test_c2_a_shared_check_that_misses_it_is_caught(src, tpl_bytes, tmp_path, monkeypatch):
    """The plan keeps the file's CMT ("Architect Callout") for the template's "Office Comment": nothing to import, so
    no styles_imported assertion — mapped_styles derives that the template's CMT is needed and fails the plan; the
    verifier's own derivation refuses the import outright (test_transform_and_verifier_derive_the_same_import)."""
    d, t = _same_id_other_style(src, tpl_bytes, tmp_path)
    monkeypatch.setattr(imp_module, "same_style", lambda mine, theirs: True)
    plan, out, rec = mapping(d, [("ArchNote", "Office Comment")], template=t)
    assert pstyle_of(out, "NOTE TO") == "CMT"                                # the architect's callout, wrongly
    assert "V10" in failed(rec) and "styles_imported assertion(s) of this op" in v10(rec)


def pstyle_of(data: bytes, needle: str):
    from spec_surgeon.ooxml import paragraphs
    p = next(p for p in paragraphs(DocxPackage(data).xml("word/document.xml"))
             if "".join(t.text or "" for t in p.iter(W + "t")).startswith(needle))
    e = p.find(f"{W}pPr/{W}pStyle")
    return e.get(W + "val") if e is not None else None


def test_c3_two_imports_of_one_name_from_two_templates_conflict(src, tpl_bytes, tmp_path):
    t1 = tmp_path / "T1.docx"
    t1.write_bytes(tpl_bytes)

    def t2fix(root):
        root.append(etree.fromstring(f'<w:style xmlns:w="{W[1:-1]}" w:type="paragraph" w:customStyle="1" '
                                     f'w:styleId="OfficeNote2"><w:name w:val="Spec  NOTE"/><w:basedOn w:val="Normal"/>'
                                     f'</w:style>'))
        root.remove(style_of(root, "SpecNote"))
    t2 = tmp_path / "T2.docx"
    t2.write_bytes(mutate(tpl_bytes, STYLES, t2fix))
    first = ("map_styles", {}, {"map": [{"from": "ArchNote", "to": "SpecNote"}], "template": str(t1)})
    for second in (("map_styles", {}, {"map": [{"from": "ArchPara", "to": "OfficeNote2"}], "template": str(t2)}),
                   ("apply_style", {"paragraphs": {"text_regex": "^Coordinate"}},
                    {"style": "OfficeNote2", "template": str(t2)})):
        code, reason = plan_or_code(src, [first, second])
        assert code == "conflict" and "name:spec note" in reason, reason
    f = get_op("map_styles").plan(DocxPackage(src), {}, first[2])
    assert (STYLES, "name:spec note", "add") in set(map(tuple, f.touches))


def test_c3_a_replaced_style_whose_name_changes_declares_the_new_name(src, tpl_bytes, tmp_path):
    t = tmp_path / "T.docx"
    t.write_bytes(mutate(tpl_bytes, STYLES, lambda r: style_of(r, "Heading1").find(W + "name").set(W + "val",
                                                                                                    "Office Heading")))
    f = get_op("map_styles").plan(DocxPackage(src), {}, {"map": [{"from": "ArchArticle", "to": "Heading1"}],
                                                         "template": str(t), "existing": "replace"})
    assert (STYLES, "name:office heading", "add") in set(map(tuple, f.touches))
    same = tmp_path / "SAME.docx"
    same.write_bytes(tpl_bytes)
    keep_name = get_op("map_styles").plan(DocxPackage(src), {}, {"map": [{"from": "ArchArticle", "to": "Heading 1"}],
                                                                 "template": str(same), "existing": "replace"})
    assert imported_assertion(keep_name)["replaced"] == ["Heading1"]
    assert not any(t_[1].startswith("name:") for t_ in keep_name.touches)    # same name: nothing new


def test_c5a_new_list_ids_go_above_ids_paragraphs_point_at(src, tpl):
    def dangle(root):
        for n in root.iter(W + "numId"):
            if n.get(W + "val") == "1":
                n.set(W + "val", "10")                       # list 10 is not defined: a dangling reference
    d = mutate(src, "word/document.xml", dangle)
    plan, out, rec = mapping(d, [("Heading 1", "PRT")], template=tpl)
    assert rec.passed, rec.to_markdown()
    assert imported_assertion(plan)["num_map"] == {"10": "11"}


def test_c5a_a_shared_allocator_that_ignores_references_is_caught(src, tpl, monkeypatch):
    def dangle(root):
        for n in root.iter(W + "numId"):
            if n.get(W + "val") == "1":
                n.set(W + "val", "10")
    d = mutate(src, "word/document.xml", dangle)
    real = imp_module.used_ids

    def defined_only(pkg, root):
        return ([imp_module._int_attr(n, "numId") for n in root.iter(W + "num")],
                [imp_module._int_attr(a, "abstractNumId") for a in root.iter(W + "abstractNum")])
    monkeypatch.setattr(imp_module, "used_ids", defined_only)
    plan, out, rec = mapping(d, [("Heading 1", "PRT")], template=tpl)
    assert imported_assertion(plan)["num_map"] == {"10": "10"}               # captures the dangling paragraph
    assert "V10" in failed(rec) and "list ids" in v10(rec)


def _masterspec(src):
    def add(root):
        root.append(etree.fromstring(f'<w:style xmlns:w="{W[1:-1]}" w:type="paragraph" w:customStyle="1" '
                                     f'w:styleId="PRT"><w:name w:val="PRT"/><w:basedOn w:val="Normal"/><w:pPr><w:numPr>'
                                     f'<w:numId w:val="3"/></w:numPr></w:pPr></w:style>'))
        root.append(etree.fromstring(f'<w:style xmlns:w="{W[1:-1]}" w:type="paragraph" w:customStyle="1" '
                                     f'w:styleId="ART"><w:name w:val="ART"/><w:basedOn w:val="PRT"/></w:style>'))
    return mutate(src, STYLES, add)


def test_c5b_a_list_naming_the_files_own_numbered_styles_is_refused(src, tpl):
    """The file holds PRT (on its list 3) and ART (based on PRT, numbered through it); the template's list that PR1
    uses names both for its levels: importing it would split that numbering — refused."""
    code, reason = plan_or_code(_masterspec(src), [("map_styles", {}, {"map": [{"from": "ArchPara", "to": "PR1"}],
                                                                        "template": tpl})])
    assert code == "style-numbering" and "list 3" in reason, reason


def test_c5b_a_shared_rule_that_misses_it_is_caught(src, tpl, monkeypatch):
    monkeypatch.setattr(imp_module, "numbered_in", lambda sheet, sid: None)
    plan, out, rec = mapping(_masterspec(src), [("ArchPara", "PR1")], template=tpl)
    assert "V10" in failed(rec) and "numbers with its own list" in v10(rec)


def test_c5b_an_unnumbered_held_level_style_is_flagged(src, tpl):
    def own_pr1(r):
        r.append(etree.fromstring(f'<w:style xmlns:w="{W[1:-1]}" w:type="paragraph" w:customStyle="1" '
                                  f'w:styleId="PR1"><w:name w:val="PR1"/><w:basedOn w:val="Normal"/></w:style>'))
    plan, out, rec = mapping(mutate(src, STYLES, own_pr1), [("Heading 1", "PRT")], template=tpl)
    assert rec.passed, rec.to_markdown()
    assert any("levels name ['PR1']" in f for f in plan.flags)


def _alternate_in_level(tpl_bytes, tmp_path):
    mc = "http://schemas.openxmlformats.org/markup-compatibility/2006"

    def tnum(root):
        a = next(x for x in root.findall(W + "abstractNum") if x.get(W + "abstractNumId") == "10")
        lvl = a.findall(W + "lvl")[3]
        nf = lvl.find(W + "numFmt")
        at = list(lvl).index(nf)
        lvl.remove(nf)
        lvl.insert(at, etree.fromstring(f'<mc:AlternateContent xmlns:mc="{mc}" xmlns:w="{W[1:-1]}"><mc:Choice '
                                        f'Requires="w14"><w:numFmt w:val="custom" w:format="01, 02, 03, ..."/>'
                                        f'</mc:Choice><mc:Fallback><w:numFmt w:val="decimal"/></mc:Fallback>'
                                        f'</mc:AlternateContent>'))
    p = tmp_path / "OFFICE_A.docx"
    p.write_bytes(mutate(tpl_bytes, NUMBERING, tnum))
    return str(p)


def test_c5c_alternate_content_in_a_list_is_refused(src, tpl_bytes, tmp_path):
    t = _alternate_in_level(tpl_bytes, tmp_path)
    code, reason = plan_or_code(src, [("map_styles", {}, {"map": [{"from": "Heading 1", "to": "PRT"}], "template": t})])
    assert code == "style-numbering" and "mc:AlternateContent" in reason, reason
    plan, out, rec = mapping(src, [("Normal", "CMT")], template=t)          # a list it does not copy: fine
    assert rec.passed, rec.to_markdown()


def test_c5c_a_shared_strip_that_drops_it_is_caught(src, tpl_bytes, tmp_path, monkeypatch):
    t = _alternate_in_level(tpl_bytes, tmp_path)
    monkeypatch.setattr(imp_module, "MC_ALTERNATE", "{urn:nothing}nothing")
    plan, out, rec = mapping(src, [("Heading 1", "PRT")], template=t)
    assert "V10" in failed(rec) and "mc:AlternateContent" in v10(rec)


def test_c6_a_malformed_template_numbering_part(src, tpl_bytes, tmp_path):
    p = tmp_path / "BROKEN_NUM.docx"
    p.write_bytes(zip_with(tpl_bytes, NUMBERING, b"<w:numbering xmlns:w='x'><w:num"))
    code, reason = plan_or_code(src, [("map_styles", {}, {"map": [{"from": "Heading 1", "to": "PRT"}],
                                                          "template": str(p)})])
    assert code == "bad-params" and "numbering part" in reason, reason
    plan, out, rec = mapping(src, [("Normal", "CMT")], template=str(p))     # no list needed: never read
    assert rec.passed, rec.to_markdown()


# ---- the two sides agree ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kids", [
    ["abstractNum:0", "abstractNum:1", "num:1", "num:2"],
    ["numPicBullet:0", "abstractNum:0", "num:1", "numIdMacAtCleanup:"],
    ["numPicBullet:0", "num:1"],                                  # a num without abstractNums: new ones before it
    ["numPicBullet:0", "numIdMacAtCleanup:"],                     # none of either: before numIdMacAtCleanup
    [],                                                           # empty: at the end
    ["abstractNum:0", "numIdMacAtCleanup:"],
])
def test_transform_and_verifier_place_new_lists_alike(kids):
    from spec_surgeon.verify.styleimport import v_expected_numbering

    def root_with(items):
        r = etree.Element(W + "numbering")
        for k in items:
            tag, _, v = k.partition(":")
            e = etree.SubElement(r, W + tag)
            if tag == "abstractNum":
                e.set(W + "abstractNumId", v)
            elif tag == "num":
                e.set(W + "numId", v)
        return r
    new = [etree.Element(W + "abstractNum", {W + "abstractNumId": "7"}), etree.Element(W + "abstractNum",
                                                                                       {W + "abstractNumId": "8"})]
    nums = [etree.Element(W + "num", {W + "numId": "9"})]
    t = root_with(kids)
    imp_module.insert_numbering(t, [copy.deepcopy(e) for e in new], [copy.deepcopy(e) for e in nums])
    v = v_expected_numbering(root_with(kids), new, nums)
    order = lambda r: [(etree.QName(c).localname, c.get(W + "abstractNumId") or c.get(W + "numId")) for c in r]
    assert order(t) == order(v), (order(t), order(v))
    names = [n for n, _ in order(t)]
    assert names.index("abstractNum") < names.index("num") if "num" in names else True
    assert names[-1] == "numIdMacAtCleanup" or "numIdMacAtCleanup" not in names

def test_transform_and_verifier_resolvers_agree(src, tpl_bytes):
    for data in (src, tpl_bytes):
        pkg = DocxPackage(data)
        from spec_surgeon.ops.selectors import StyleSheet
        sheet = StyleSheet(pkg)
        vstyles = Styles(VPackage(data).xml(STYLES))
        refs = ["", "nope", "BODY TEXT"]
        for sid, st in sheet.styles.items():
            name = st.find(W + "name").get(W + "val") if st.find(W + "name") is not None else ""
            refs += [sid, sid.lower(), name, name.upper(), name.casefold() + " "]
        for ref in refs:
            try:
                t = ("ok", resolve_style(sheet.styles, ref, "x"))
            except JobException as e:
                assert e.code == "style-mismatch"
                t = ("refused", None)
            try:
                v = ("ok", v_resolve_style(vstyles, ref))
            except ValueError:
                v = ("refused", None)
            assert t == v, ref


def test_transform_and_verifier_agree_on_the_review_cases(src, tpl_bytes, tmp_path):
    """One id two styles (C2), a list naming the file's own numbered styles (C5b), a dangling list id (C5a), an
    alternate-content level (C5c): the two derivations reach the same decision."""
    d_c2, t_c2 = _same_id_other_style(src, tpl_bytes, tmp_path)

    def dangle(root):
        for n in root.iter(W + "numId"):
            if n.get(W + "val") == "1":
                n.set(W + "val", "10")
    t_plain = tmp_path / "PLAIN.docx"
    t_plain.write_bytes(tpl_bytes)
    t_alt = _alternate_in_level(tpl_bytes, tmp_path)
    cases = [(d_c2, t_c2, ["CMT"], "keep", "refused"), (d_c2, t_c2, ["CMT"], "replace", None),
             (_masterspec(src), str(t_plain), ["PR1"], "keep", "refused"),
             (mutate(src, "word/document.xml", dangle), str(t_plain), ["PRT"], "keep", None),
             (src, t_alt, ["PRT"], "keep", "refused"), (src, t_alt, ["CMT"], "keep", None)]
    for data, t, requested, existing, expect in cases:
        try:
            imp = plan_import(DocxPackage(data), TemplateStyles.load(t), requested, existing)
            mine = (imp.imported, imp.replaced, sorted(imp.held), imp.num_map, imp.abs_map)
        except JobException as e:
            assert e.code in ("style-import", "style-numbering"), e.code
            mine = "refused"
        vp, tp = VPackage(data), VPackage(Path(t).read_bytes())
        try:
            dv = v_derive_import(Styles(vp.xml(STYLES)), vp.xml(NUMBERING), Styles(tp.xml(STYLES)), tp.xml(NUMBERING),
                                 requested, existing, used=v_used_ids(vp, vp.xml(NUMBERING)))
            theirs = (dv["imported"], dv["replaced"], dv["held"], dv["num_map"], dv["abs_map"])
        except ValueError:
            theirs = "refused"
        assert mine == theirs, (requested, existing, mine, theirs)
        assert (mine == "refused") == (expect == "refused"), (requested, existing, mine)


@pytest.mark.parametrize("requested,existing", [
    (["PRT"], "keep"), (["ART"], "keep"), (["PR2"], "keep"), (["CMT"], "keep"), (["SpecNote", "CharMine"], "keep"),
    (["TblMine"], "keep"), (["Heading1"], "replace"), (["Heading1", "PRT"], "replace"), (["Heading1"], "keep"),
    (["PicList"], "keep"), (["LinkList"], "keep"), (["MyBody"], "keep"), (["Normal"], "replace"), ([], "keep")])
def test_transform_and_verifier_derive_the_same_import(src, tpl, requested, existing):
    for data in (src, with_extra_list(src)):
        pkg = DocxPackage(data)
        t = TemplateStyles.load(tpl)
        try:
            imp = plan_import(pkg, t, requested, existing)
            mine = (imp.imported, imp.replaced, sorted(imp.held), imp.num_map, imp.abs_map)
        except JobException as e:
            assert e.code in ("style-import", "style-numbering"), e.code
            mine = "refused"
        vp, tp = VPackage(data), VPackage(Path(tpl).read_bytes())
        try:
            d = v_derive_import(Styles(vp.xml(STYLES)), vp.xml(NUMBERING), Styles(tp.xml(STYLES)), tp.xml(NUMBERING),
                                requested, existing, used=v_used_ids(vp, vp.xml(NUMBERING)))
            theirs = (d["imported"], d["replaced"], d["held"], d["num_map"], d["abs_map"])
        except ValueError:
            theirs = "refused"
        assert mine == theirs, (requested, existing, mine, theirs)
