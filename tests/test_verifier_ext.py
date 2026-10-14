"""Verifier extensions for the NL editor: V1 member prediction + integrity, V2 text-scope union, V4 revision kinds,
V10 property assertions / residual identity, V7 reference modes. Each test plants a defect the v0.1.0 suite could
not see and proves the extended suite catches it — and that the correct version passes."""
from __future__ import annotations

import io
import zipfile

from lxml import etree

from make_fixtures import build
from spec_surgeon.batch import geometry_check
from spec_surgeon.ooxml import block_full_text, paragraphs
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.verify import run_suite
from spec_surgeon.verify.geometry import GeometryResult, PageGeom


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
