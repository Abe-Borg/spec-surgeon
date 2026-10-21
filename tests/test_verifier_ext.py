"""Verifier extensions for the NL editor: V1 member prediction + integrity, V2 text-scope union, V4 revision kinds,
V10 property assertions / residual identity, V7 reference modes. Each test plants a defect the v0.1.0 suite could
not see and proves the extended suite catches it — and that the correct version passes."""
from __future__ import annotations

import io
import zipfile

from lxml import etree
import pytest

from make_fixtures import build, build_styles
from spec_surgeon.batch import geometry_check
from spec_surgeon.ooxml import block_full_text, paragraphs
from spec_surgeon.ops import get_op, merge_fragments
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.verify import run_suite
from spec_surgeon.verify.geometry import GeometryResult, PageGeom
from spec_surgeon.verify.reader import VPackage
from test_adaptive_messy import mutate_xml


def _failed(rec):
    return [c.id for c in rec.checks if c.status == "FAIL"]


def _plan(**kw):
    p = {"job": "test", "mode": "formatting", "params": {}, "intended_parts": [], "diffs": [],
         "rev_delta": {"ins": 0, "del": 0}}
    p.update(kw)
    return p


def _remove_default_header(pkg: DocxPackage, keep_part=False, keep_rel=False):
    """A hand-written header removal on section 0 of the v0.1.0 fixture: returns (work, plan pieces)."""
    work = pkg.copy()
    main = pkg.main_part()
    hdr = pkg.sections()[0].header_refs["default"]
    rid = [r.rid for r in pkg.rels().values() if r.resolved == hdr][0]
    root = work.xml(main)
    for ref in list(root.iter(W + "headerReference")):
        if ref.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id") == rid:
            ref.getparent().remove(ref)
    work.write_xml(main, root)
    if not keep_rel:
        work.edit_rels(main, remove=[rid])
    if not keep_part:
        work.remove_member(hdr)
    hroot = pkg.xml(hdr)
    diffs = [{"part": hdr, "block_index": i, "before": block_full_text(p), "after": None}
             for i, p in enumerate(paragraphs(hroot))]
    return work, hdr, diffs


def test_v1_v2_predicted_part_removal_passes_and_unpredicted_fails():
    pkg = DocxPackage(build("21 05 00", "A"))
    work, hdr, diffs = _remove_default_header(pkg)
    out = work.output_bytes()
    intended = ["word/document.xml", "word/_rels/document.xml.rels", "[Content_Types].xml"]
    assertion = {"kind": "section_hf_refs", "sections": [0], "kinds": ["header"], "types": ["default"], "expected_removed": 1}
    good = _plan(intended_parts=intended, members_removed=[hdr], diffs=diffs, untracked_diffs_expected=True,
                 property_assertions=[assertion])
    rec = run_suite(pkg.data, out, good, "s", "o")
    assert rec.passed, rec.to_markdown()
    assert "no longer text parts" in rec.get("V2").summary
    # removal not predicted → V1; deleted blocks not predicted → V2 (text-scope union sees the part's text go)
    rec = run_suite(pkg.data, out, {**good, "members_removed": []}, "s", "o")
    assert "V1" in _failed(rec)
    rec = run_suite(pkg.data, out, {**good, "diffs": []}, "s", "o")
    assert "V2" in _failed(rec)
    # the sectPr reference removed without an assertion → V10 residual
    rec = run_suite(pkg.data, out, {**good, "property_assertions": []}, "s", "o")
    assert "V10" in _failed(rec)


def test_v1_integrity_catches_dangling_and_orphaned_members():
    pkg = DocxPackage(build("21 05 00", "A"))
    intended = ["word/document.xml", "word/_rels/document.xml.rels", "[Content_Types].xml"]
    assertion = {"kind": "section_hf_refs", "sections": [0], "kinds": ["header"], "types": ["default"], "expected_removed": 1}
    # part removed but its relationship left behind → dangling relationship
    work, hdr, diffs = _remove_default_header(pkg, keep_rel=True)
    rec = run_suite(pkg.data, work.output_bytes(), _plan(intended_parts=intended, members_removed=[hdr], diffs=diffs,
                    property_assertions=[assertion]), "s", "o")
    assert "V1" in _failed(rec) and "dangling" in rec.get("V1").summary
    # relationship and reference removed but the part kept → orphaned member (Word keeps junk; we predicted nothing)
    work, hdr, diffs = _remove_default_header(pkg, keep_part=True)
    rec = run_suite(pkg.data, work.output_bytes(), _plan(intended_parts=intended, diffs=diffs,
                    property_assertions=[assertion]), "s", "o")
    assert "V1" in _failed(rec) and "ORPHANED" in rec.get("V1").summary
    # relationship removed while the sectPr still points at it → dangling r:id (repair prompt)
    work = pkg.copy()
    hdr = pkg.sections()[0].header_refs["default"]
    rid = [r.rid for r in pkg.rels().values() if r.resolved == hdr][0]
    work.edit_rels("word/document.xml", remove=[rid])
    work.remove_member(hdr)
    _, _, diffs = _remove_default_header(pkg)
    rec = run_suite(pkg.data, work.output_bytes(), _plan(intended_parts=intended, members_removed=[hdr], diffs=diffs),
                    "s", "o")
    assert "V1" in _failed(rec) and "names no relationship" in rec.get("V1").summary


def test_v1_added_member_must_be_predicted_and_appended():
    pkg = DocxPackage(build("21 05 00", "A"))
    work = pkg.copy()
    work.add_member("word/extra.xml", b"<?xml version='1.0'?><x/>", "application/vnd.example+xml")
    work.edit_rels("word/document.xml", add=[{"type": "urn:example", "target": "extra.xml"}])
    out = work.output_bytes()
    plan = _plan(intended_parts=["[Content_Types].xml", "word/_rels/document.xml.rels"], members_added=["word/extra.xml"])
    assert run_suite(pkg.data, out, plan, "s", "o").get("V1").status == "PASS"
    assert run_suite(pkg.data, out, {**plan, "members_added": []}, "s", "o").get("V1").status == "FAIL"


def _mutate(docx: bytes, part: str, fn) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(docx))
    root = etree.fromstring(zin.read(part))
    fn(root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            z.writestr(info, serialize_xml(root) if info.filename == part else zin.read(info.filename))
    return buf.getvalue()


def test_v4_v5b_count_property_change_revisions():
    src = build("21 05 00", "A")

    def add_rpr_change(root):
        r = [r for r in root.iter(W + "r") if any("Article 3 body" in (t.text or "") for t in r.findall(W + "t"))][0]
        rpr = r.find(W + "rPr")
        if rpr is None:
            rpr = etree.Element(W + "rPr"); r.insert(0, rpr)
        etree.SubElement(rpr, W + "b")
        chg = etree.SubElement(rpr, W + "rPrChange", {W + "id": "900", W + "author": "X", W + "date": "2026-01-01T00:00:00Z"})
        etree.SubElement(chg, W + "rPr")

    out = _mutate(src, "word/document.xml", add_rpr_change)
    rec = run_suite(src, out, _plan(intended_parts=["word/document.xml"]), "s", "o")
    assert {"V4", "V5b", "V10"} <= set(_failed(rec))
    assert "rPrChange" in rec.get("V4").summary


def test_v10_catches_formatting_only_changes_and_unknown_kinds():
    src = build("21 05 00", "A")

    def bold(root):
        r = [r for r in root.iter(W + "r") if any("Article 7 body" in (t.text or "") for t in r.findall(W + "t"))][0]
        rpr = r.find(W + "rPr")
        if rpr is None:
            rpr = etree.Element(W + "rPr"); r.insert(0, rpr)
        etree.SubElement(rpr, W + "b")

    out = _mutate(src, "word/document.xml", bold)
    rec = run_suite(src, out, _plan(intended_parts=["word/document.xml"]), "s", "o")
    assert _failed(rec) == ["V10"], rec.to_markdown()        # V1–V9 cannot see a run turning bold
    rec = run_suite(src, src, _plan(property_assertions=[{"kind": "made_up"}]), "s", "o")
    assert _failed(rec) == ["V10"]


def _geom(dy):
    g = GeometryResult(ref_pages=2, out_pages=2, pages=[PageGeom(1, 10, dy, 0.0, 0, 0, False)])
    g.worst_dy_asserted = dy
    return g


def test_v7_reference_modes():
    assert geometry_check({"mode": "formatting"}, _geom(0.1)).status == "PASS"
    assert geometry_check({"mode": "formatting"}, _geom(3.0)).status == "FAIL"
    none = {"mode": "formatting", "geometry_reference": "none", "geometry_reason": "a font change reflows every page"}
    r = geometry_check(none, _geom(9.0))
    assert r.status == "NA" and "reflows" in r.summary
    assert geometry_check({**none, "geometry_reason": ""}, _geom(0.0)).status == "FAIL"     # no reason → no waiver
    assert geometry_check({"geometry_reference": "template", "geometry_template": "T.docx"}, _geom(0.0)).status == "FAIL"
    assert geometry_check({"mode": "content", "diffs": [{}]}, _geom(9.0)).status == "NA"


STYLES = "word/styles.xml"


@pytest.fixture(scope="module")
def src() -> bytes:
    return build_styles()


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


# ---- duplicate revision ids and a misplaced rPrChange ----------------------------------------------------------------

AUTHOR = "Abraham Borg"


def run_op(src: bytes, op: str, select, params):
    pkg = DocxPackage(src)
    frag = get_op(op).plan(pkg, select, params)
    plan = merge_fragments([frag])
    work = pkg.copy()
    get_op(op).apply(work, frag)
    out = work.output_bytes()
    return plan, out, run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")


def fails(rec):
    return {c.id for c in rec.checks if c.status == "FAIL"}


def doc_root(data: bytes):
    return etree.fromstring(VPackage(data).read("word/document.xml"))


def first_text_el(root, prefix: str):
    for t in root.find(W + "body").iter(W + "t"):
        if t.text and t.text.startswith(prefix):
            return t
    raise AssertionError(prefix)


def _add_rprchange_to_owner_run(root):
    t = first_text_el(root, "The Owner's insurer is FM Global")
    r = t.getparent()
    rpr = r.find(W + "rPr")
    if rpr is None:
        rpr = etree.Element(W + "rPr")
        r.insert(0, rpr)
    etree.SubElement(rpr, W + "b")
    chg = etree.SubElement(rpr, W + "rPrChange")
    chg.set(W + "id", "900"); chg.set(W + "author", "Jeremy"); chg.set(W + "date", "2026-09-22T10:15:00Z")
    etree.SubElement(chg, W + "rPr")


def test_v4_catches_duplicate_revision_ids_on_split_rprchange_copies():
    src = mutate_xml(build("21 05 00", "X"), "word/document.xml", _add_rprchange_to_owner_run)
    sel = {"paragraphs": {"text_regex": "Owner's insurer"}, "scope": ["document"]}
    for find, d_rpr in (("The Owner's", 2), ("FM Global", 3), ("FM Approved.", 2)):   # start / middle / end of the run
        plan, out, rec = run_op(src, "replace_text", sel,
                                {"find": find, "replace": "XYZ", "allow_tracked_format_split": True, "author": AUTHOR})
        assert rec.passed, rec.to_markdown()
        assert plan.rev_delta["rPrChange"] == d_rpr

    plan, out, rec = run_op(src, "replace_text", sel,
                            {"find": "FM Global", "replace": "XYZ", "allow_tracked_format_split": True, "author": AUTHOR})

    def same_id_everywhere(root):            # the transform forgot to renumber the copies
        for chg in root.iter(W + "rPrChange"):
            chg.set(W + "id", "900")
    bad = mutate_xml(out, "word/document.xml", same_id_everywhere)
    assert sum(1 for c in doc_root(bad).iter(W + "rPrChange") if c.get(W + "id") == "900") == 4
    bad_rec = run_suite(src, bad, plan.to_dict(), "src.docx", "out.docx")
    assert bad_rec.get("V4").status == "FAIL"
    assert "DUPLICATE ids ['900']" in bad_rec.get("V4").summary
    # a duplicate the SOURCE already had is baseline, not a new defect
    src_dup = mutate_xml(src, "word/document.xml", lambda root: [el.set(W + "id", "900") for el in root.iter(W + "ins")])
    plan2, out2, rec2 = run_op(src_dup, "replace_text", {"scope": ["document"]},
                               {"find": "Schedule 40", "replace": "Schedule 10", "author": AUTHOR})
    assert rec2.get("V4").status == "PASS", rec2.get("V4").summary


def test_misplaced_rprchange_is_caught_by_v5a_and_v10():
    src = mutate_xml(build("21 05 00", "X"), "word/document.xml", _add_rprchange_to_owner_run)
    plan, out, rec = run_op(src, "replace_text", {"paragraphs": {"text_regex": "Owner's insurer"}, "scope": ["document"]},
                            {"find": "FM Global", "replace": "XYZ", "allow_tracked_format_split": True, "author": AUTHOR})
    assert rec.passed

    def rprchange_first(root):
        for chg in root.iter(W + "rPrChange"):
            rpr = chg.getparent(); rpr.remove(chg); rpr.insert(0, chg)
    bad = run_suite(src, mutate_xml(out, "word/document.xml", rprchange_first), plan.to_dict(), "s", "o")
    assert {"V5a", "V10"} <= fails(bad)


# ---- values that can be None sort beside text ------------------------------------------------------------------------

DOC = "word/document.xml"


def mutate(data: bytes, member: str, fn) -> bytes:
    src, out = zipfile.ZipFile(io.BytesIO(data)), io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for info in src.infolist():
            b = src.read(info.filename)
            if info.filename == member:
                root = etree.fromstring(b)
                fn(root)
                b = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            z.writestr(info, b)
    return out.getvalue()


def text_paragraphs(root):
    return [p for p in root.find(W + "body").findall(W + "p") if p.find(".//" + W + "t") is not None]


def set_text(p, text):
    ts = list(p.iter(W + "t", W + "delText", W + "instrText", W + "delInstrText"))
    ts[0].text = text
    for t in ts[1:]:
        t.text = ""


def new_paragraph(text):
    p = etree.Element(W + "p")
    etree.SubElement(etree.SubElement(p, W + "r"), W + "t").text = text
    return p


def status(rec, cid):
    return next(c for c in rec.checks if c.id == cid)


def test_v2_compares_a_part_mixing_an_inserted_and_a_changed_block_and_still_fails_a_mismatch():
    src = mutate(build("21 05 00", "COMMON WORK RESULTS"), DOC, lambda r: set_text(text_paragraphs(r)[2], "ALPHA TEXT"))

    def edit(root):
        ps = text_paragraphs(root)
        set_text(ps[2], "ALPHA TEXT CHANGED")
        ps[8].addnext(new_paragraph("BRAND NEW"))
    out = mutate(src, DOC, edit)
    changed, inserted = (DOC, "ALPHA TEXT", "ALPHA TEXT CHANGED"), (DOC, None, "BRAND NEW")

    def v2(*diffs):
        plan = {"job": "x", "mode": "formatting", "untracked_diffs_expected": True, "intended_parts": [DOC],
                "rev_delta": {}, "diffs": [{"part": p, "before": b, "after": a} for p, b, a in diffs]}
        return status(run_suite(src, out, plan, "s", "o"), "V2")      # raised TypeError before the fix

    nothing = v2()
    assert nothing.status == "FAIL" and nothing.data["missing"] == []
    assert sorted(nothing.data["unpredicted"], key=str) == sorted([changed, inserted], key=str)
    for diffs in ([changed, inserted], [inserted, changed]):           # predicted exactly, in either order
        ok = v2(*diffs)
        assert ok.status == "PASS" and ok.data["missing"] == [] and ok.data["unpredicted"] == []
    half = v2(changed)
    assert half.status == "FAIL" and half.data["missing"] == [] and half.data["unpredicted"] == [inserted]
    wrong = v2(changed, (DOC, None, "BRAND OLD"))
    assert wrong.status == "FAIL" and wrong.data["missing"] == [(DOC, None, "BRAND OLD")]
    assert wrong.data["unpredicted"] == [inserted]
    removed = v2(changed, inserted, (DOC, "GONE", None))
    assert removed.status == "FAIL" and removed.data["missing"] == [(DOC, "GONE", None)]


def test_v4_and_v5b_report_revisions_without_an_id_beside_ones_with_one():
    src = build("21 05 00", "COMMON WORK RESULTS")

    def wrap(root):                                    # four insertions: two without w:id, two sharing id 9
        for p, rid in zip(text_paragraphs(root)[2:6], (None, None, "9", "9")):
            r = p.find(W + "r")
            ins = etree.Element(W + "ins")
            if rid is not None:
                ins.set(W + "id", rid)
            ins.set(W + "author", "x")
            r.addprevious(ins)
            ins.append(r)
    out = mutate(src, DOC, wrap)
    rec = run_suite(src, out, {"job": "x", "mode": "formatting", "intended_parts": [DOC], "rev_delta": {}, "diffs": []},
                    "s", "o")                          # raised TypeError (None beside "9") before the fix
    v4, v5b = status(rec, "V4"), status(rec, "V5b")
    assert v4.status == "FAIL" and v4.data["duplicate_ids"] == [None, "9"]
    assert v5b.status == "FAIL" and v5b.data["new_revision_ids"] == [(DOC, None), (DOC, "9")]
