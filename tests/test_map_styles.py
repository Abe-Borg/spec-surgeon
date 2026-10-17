"""map_styles and V10's ``mapped_styles`` kind (built-in batch 4, item 31 ★ — docs/BUILTIN_OPS_BATCH4_DESIGN.md §7): end to
end on the architect's spec (paragraph, character and table styles, the default paragraph style, keep and replace,
tracked), what is skipped and flagged, the parameters and refusals; a wrong OUTPUT per rule; wrong IMPLEMENTATIONS shared
by plan and apply; plan-time conflicts (rule 9); a spec through the batch. The import itself: tests/test_style_import.py."""
from __future__ import annotations

import ast
import copy
import io
import json
import zipfile
from pathlib import Path
from typing import List, Tuple

import pytest
from lxml import etree

from make_fixtures import build, build_style_template, build_styles
from spec_surgeon.editspec import apply_spec, load_spec, plan_spec
from spec_surgeon.ooxml import paragraphs
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import map_styles as map_module
from spec_surgeon.ops import style_import as imp_module
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.report import FAILURE_EXPLANATIONS
from spec_surgeon.verify import run_suite

AUTHOR = "Abraham Borg"
MAIN = "word/document.xml"
STYLES = "word/styles.xml"
HF_SPEC = Path(__file__).resolve().parent.parent / "examples" / "specs" / "match-template-spec-header-footer.json"


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


def run_ops(data: bytes, specs: List[Tuple[str, dict, dict]]):
    pkg = DocxPackage(data)
    frags = [get_op(o).plan(pkg, sel, par) for o, sel, par in specs]
    plan = merge_fragments(frags)
    work = pkg.copy()
    for f in frags:
        get_op(f.op).apply(work, f)
    out = work.output_bytes()
    return plan, out, run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")


def mapping(data: bytes, pairs, select=None, **params):
    return run_ops(data, [("map_styles", select or {}, {"map": [{"from": f, "to": t} for f, t in pairs], **params})])


def plan_only(data: bytes, pairs, select=None, **params):
    return get_op("map_styles").plan(DocxPackage(data), select or {},
                                     {"map": [{"from": f, "to": t} for f, t in pairs], **params})


def failed(rec):
    return [c.id for c in rec.checks if c.status == "FAIL"]


def v10(rec) -> str:
    c = next(c for c in rec.checks if c.id == "V10")
    return c.summary + " | " + " | ".join(c.data.get("errors", []))


def text_of(p) -> str:
    return "".join(t.text or "" for t in p.iter(W + "t"))


def para(root, needle):
    return [p for p in root.iter(W + "p") if text_of(p).startswith(needle)][0]


def index_of(data: bytes, needle: str, part: str = MAIN) -> int:
    pars = paragraphs(DocxPackage(data).xml(part))
    return next(i for i, p in enumerate(pars) if text_of(p).startswith(needle))


def pstyle(p):
    e = p.find(f"{W}pPr/{W}pStyle")
    return e.get(W + "val") if e is not None else None


def rstyle(r):
    e = r.find(f"{W}rPr/{W}rStyle")
    return e.get(W + "val") if e is not None else None


def root_of(data: bytes, part: str = MAIN):
    return DocxPackage(data).xml(part)


def mapped_assertion(plan):
    return next(a for a in plan.property_assertions if a["kind"] == "mapped_styles")


def with_mark_style(data: bytes) -> bytes:
    """The "1.01 SUMMARY" paragraph's mark in Arch Strong too (a character style on a paragraph mark)."""
    def add(root):
        ppr = para(root, "1.01").find(W + "pPr")
        etree.SubElement(etree.SubElement(ppr, W + "rPr"), W + "rStyle", {W + "val": "ArchStrong"})
    return mutate(data, MAIN, add)


# ---- end to end ----------------------------------------------------------------------------------------------------

def test_the_architects_heading_becomes_your_part_style(src, tpl):
    plan, out, rec = mapping(src, [("Heading 1", "PRT")], template=tpl)
    assert rec.passed, rec.to_markdown()
    root = root_of(out)
    assert pstyle(para(root, "PART 1")) == "PRT" and pstyle(para(root, "PART 3")) == "PRT"
    general = [r for r in para(root, "PART 1").iter(W + "r") if text_of(r) == " (general)"][0]
    assert rstyle(general) == "Heading1Char"                                   # the linked character style stays
    assert any("use Heading1Char, the character style linked to Heading1" in f for f in plan.flags)
    a = mapped_assertion(plan)
    assert a["expected"] == {"paragraphs": 2, "runs": 0, "marks": 0, "tables": 0}
    assert a["pairs"][0]["status"] == "mapped" and a["pairs"][0]["from_id"] == "Heading1"
    assert plan.mode == "formatting" and plan.geometry_reference == "none" and not plan.diffs
    assert not {k: v for k, v in plan.rev_delta.items() if v}
    bi = index_of(src, "PART 1")
    touches = set(map(tuple, get_op("map_styles").plan(DocxPackage(src), {}, {
        "map": [{"from": "Heading 1", "to": "PRT"}], "template": tpl}).touches))
    assert {(MAIN, str(bi), "pPr"), (MAIN, str(bi), "pStyle"), (MAIN, "scope", "verify"),
            ("word/header1.xml", "scope", "verify"), (STYLES, "style:Heading1", "verify")} <= touches
    assert (STYLES, "style:PRT", "verify") not in touches                     # imported: an add, not a read


def test_paragraph_character_and_table_styles_in_one_call(src, tpl):
    plan, out, rec = mapping(src, [("ArchArticle", "ART"), ("Arch Strong", "Char Mine"), ("ArchTable", "TblMine")],
                             template=tpl)
    assert rec.passed, rec.to_markdown()
    root = root_of(out)
    assert pstyle(para(root, "1.01")) == "ART" and pstyle(para(root, "2.01")) == "ART"
    summary = [r for r in para(root, "1.01").iter(W + "r") if text_of(r) == "SUMMARY"][0]
    assert rstyle(summary) == "CharMine"
    assert root.find(f".//{W}tbl/{W}tblPr/{W}tblStyle").get(W + "val") == "TblMine"
    a = mapped_assertion(plan)
    assert a["expected"] == {"paragraphs": 2, "runs": 1, "marks": 0, "tables": 1}
    assert a["changed"][MAIN]["tables"] == [0]
    assert any("names style 'Arch Article'" in f and "TOC" in f for f in plan.flags)
    touches = set(map(tuple, get_op("map_styles").plan(DocxPackage(src), {}, {
        "map": [{"from": "ArchTable", "to": "TblMine"}], "template": tpl}).touches))
    assert (MAIN, "tbl:0", "tblPr") in touches


def test_the_default_paragraph_style_maps_paragraphs_without_a_style(src, tpl):
    plan, out, rec = mapping(src, [("Normal", "CMT")], template=tpl)
    assert rec.passed, rec.to_markdown()
    root = root_of(out)
    for needle in ("SECTION 21", "WET-PIPE", "Coordinate", "Earlier change", "Manual list", "PART 2", "175 psi"):
        assert pstyle(para(root, needle)) == "CMT", needle
    p2 = para(root, "PART 2")                                     # it had no pPr: one is created first
    assert p2[0].tag == W + "pPr" and [c.tag for c in p2[0]] == [W + "pStyle"]
    sec = para(root, "SECTION 21")                                # pStyle goes first, before the direct jc
    assert [etree.QName(c).localname for c in sec.find(W + "pPr")] == ["pStyle", "jc"]
    assert pstyle(para(root_of(out, "word/header1.xml"), "SECTION")) == "Header"
    assert pstyle(para(root, "1.01")) == "ArchArticle"
    assert any("already carried a pending tracked formatting change" in f for f in plan.flags)
    assert mapped_assertion(plan)["expected"]["paragraphs"] == 8


def test_a_style_the_file_has_is_used_as_it_is(src, tpl):
    plan, out, rec = mapping(src, [("ArchNote", "Heading 2")])                 # no template at all
    assert rec.passed, rec.to_markdown()
    assert pstyle(para(root_of(out), "NOTE TO")) == "Heading2"
    assert DocxPackage(out).read(STYLES) == DocxPackage(src).read(STYLES)
    plan, out, rec = mapping(src, [("Heading 1", "Heading 1")], template=tpl, existing="replace")
    assert rec.passed and out == src
    assert mapped_assertion(plan)["pairs"][0]["status"] == "same"
    assert any("nothing to map, skipped" in n for n in plan.notes) and any("no change" in n for n in plan.notes)


def test_tracked_mapping_records_each_change(src, tpl):
    plan, out, rec = mapping(with_mark_style(src), [("ArchArticle", "ART"), ("ArchStrong", "CharMine")], template=tpl,
                             tracked=True, author=AUTHOR)
    assert rec.passed, rec.to_markdown()
    assert plan.rev_delta["pPrChange"] == 2 and plan.rev_delta["rPrChange"] == 2
    root = root_of(out)
    p = para(root, "1.01")
    chg = p.find(f"{W}pPr/{W}pPrChange")
    assert chg.get(W + "author") == AUTHOR and chg.find(f"{W}pPr/{W}pStyle").get(W + "val") == "ArchArticle"
    summary = [r for r in p.iter(W + "r") if text_of(r) == "SUMMARY"][0]
    assert summary.find(f"{W}rPr/{W}rPrChange/{W}rPr/{W}rStyle").get(W + "val") == "ArchStrong"
    mark = p.find(f"{W}pPr/{W}rPr")
    assert mark.find(W + "rStyle").get(W + "val") == "CharMine" and mark[-1].tag == W + "rPrChange"
    assert mapped_assertion(plan)["expected"] == {"paragraphs": 2, "runs": 1, "marks": 1, "tables": 0}


def test_scope_and_what_is_skipped(src, tpl):
    plan, out, rec = mapping(src, [("Footer", "CMT")], select={"scope": ["document"]}, template=tpl)
    assert rec.passed and mapped_assertion(plan)["pairs"][0]["status"] == "unused"
    assert not any(a["kind"] == "styles_imported" for a in plan.property_assertions)     # nothing used: nothing imported
    assert any("nothing in scope uses Footer" in n for n in plan.notes)
    plan, out, rec = mapping(src, [("Footer", "CMT")], template=tpl)                     # every text part by default
    assert rec.passed, rec.to_markdown()
    assert pstyle(paragraphs(root_of(out, "word/footer1.xml"))[0]) == "CMT"
    plan, out, rec = mapping(src, [("Nope", "PRT"), ("ArchArticle", "ART")], template=tpl)
    assert rec.passed and [p["status"] for p in mapped_assertion(plan)["pairs"]] == ["missing", "mapped"]
    assert any("this file has no style 'Nope'" in n for n in plan.notes)
    plan, out, rec = mapping(src, [("Nope", "PRT")], template=tpl)
    assert rec.passed and not plan.intended_parts and out == src
    for pairs in ([("Nope", "PRT")], [("ArchUnused", "ART")]):
        with pytest.raises(JobException) as e:
            plan_only(src, pairs, template=tpl, allow_missing=False)
        assert e.value.code == "not-found"


# ---- parameters and refusals ---------------------------------------------------------------------------------------

def test_parameters_are_checked(src, tpl):
    pkg = DocxPackage(src)
    for params in ({}, {"map": []}, {"map": "Heading 1"}, {"map": [{"from": "A"}]}, {"map": [{"from": "A", "to": ""}]},
                   {"map": [{"from": "A", "to": "B", "x": 1}]}, {"map": [{"from": "A", "to": "B"}, {"from": "A", "to": "C"}]},
                   {"map": [{"from": "A", "to": "B"}, {"from": "B", "to": "C"}]},
                   {"map": [{"from": "A", "to": "B"}], "existing": "replace"},
                   {"map": [{"from": "A", "to": "B"}], "existing": "merge", "template": tpl},
                   {"map": [{"from": "A", "to": "B"}], "template": ""},
                   {"map": [{"from": "A", "to": "B"}], "template": tpl + ".missing"},
                   {"map": [{"from": "A", "to": "B"}], "tracked": "yes"},
                   {"map": [{"from": "A", "to": "B"}], "allow_missing": 1},
                   {"map": [{"from": "Heading 1", "to": "Nope"}], "template": tpl}):
        with pytest.raises(JobException) as e:
            get_op("map_styles").plan(pkg, {}, params)
        assert e.value.code == "bad-params", params
    for select in ({"paragraphs": "all"}, {"scope": ["everything"]}):
        with pytest.raises(JobException) as e:
            get_op("map_styles").plan(pkg, select, {"map": [{"from": "A", "to": "B"}]})
        assert e.value.code == "bad-params", select


def test_style_mismatch_and_missing_style(src, tpl):
    for pairs, needle, with_tpl in (([("ArchStrong", "PRT")], "of its own type", True),
                                    ([("Default Paragraph Font", "CharMine")], "default character style", True),
                                    ([("TableNormal", "TblMine")], "default table style", True),
                                    ([("ArchArticle", "ART"), ("Arch Article", "PR1")], "resolve to the same style", True),
                                    ([("Arch Para", "ArchNote"), ("Arch Note", "Heading 2")], "mapped to by one pair",
                                     False),
                                    ([("ArchPara", "Body Text")], "two styles are named 'Body Text'", True),
                                    ([("NoList", "PRT")], "numbering style", True)):
        with pytest.raises(JobException) as e:
            plan_only(src, pairs, **({"template": tpl} if with_tpl else {}))
        assert e.value.code == "style-mismatch" and needle in str(e.value), (pairs, str(e.value))
    with pytest.raises(JobException) as e:
        plan_only(src, [("ArchNote", "SpecNote")])
    assert e.value.code == "missing-style"


def test_tracked_refusals(src, tpl):
    def refused(data, pairs):
        with pytest.raises(JobException) as e:
            plan_only(data, pairs, template=tpl, tracked=True)
        assert e.value.code == "tracked-format", str(e.value)
    refused(src, [("ArchTable", "TblMine")])                                 # a table style is not tracked here
    refused(src, [("Normal", "CMT")])                                        # Jeremy's pending pPrChange

    def run_change(root):
        r = [r for r in para(root, "1.01").iter(W + "r") if text_of(r) == "SUMMARY"][0]
        etree.SubElement(r.find(W + "rPr"), W + "rPrChange", {W + "id": "800", W + "author": "J"}).append(
            etree.Element(W + "rPr"))
    refused(mutate(src, MAIN, run_change), [("ArchStrong", "CharMine")])

    def mark_revision(root):
        m = with_mark_style_el(root)
        m.insert(0, etree.Element(W + "ins", {W + "id": "801", W + "author": "J"}))
    refused(mutate(src, MAIN, mark_revision), [("ArchStrong", "CharMine")])
    plan, out, rec = mapping(mutate(src, MAIN, run_change), [("ArchStrong", "CharMine")], template=tpl)
    assert rec.passed and any("already carried" in f for f in plan.flags)     # plain: changed and flagged


def with_mark_style_el(root):
    ppr = para(root, "1.01").find(W + "pPr")
    rpr = etree.SubElement(ppr, W + "rPr")
    etree.SubElement(rpr, W + "rStyle", {W + "val": "ArchStrong"})
    return rpr


def test_every_refusal_code_is_known():
    codes = set()
    for module in (map_module, imp_module):
        for n in ast.walk(ast.parse(Path(module.__file__).read_text(encoding="utf-8"))):
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "JobException" and len(n.args) > 1:
                assert isinstance(n.args[1], ast.Constant) or ast.unparse(n.args[1]) == "code", ast.unparse(n)
                if isinstance(n.args[1], ast.Constant):
                    codes.add(n.args[1].value)
    assert codes == {"bad-params", "style-mismatch", "style-import", "style-numbering", "missing-style", "not-found",
                     "tracked-format", "internal", "structure"}
    # the three new codes get their plain-words explanations at integration (docs/batch4_notes/)
    assert set(c for c in codes if c not in FAILURE_EXPLANATIONS) <= {"style-mismatch", "style-import",
                                                                      "style-numbering", "internal"}


# ---- a wrong output per rule ---------------------------------------------------------------------------------------

def _caught(src, plan, out, part, fn, needle=None):
    bad = mutate(out, part, fn)
    rec = run_suite(src, bad, plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec), rec.to_markdown()
    if needle:
        assert needle in v10(rec), v10(rec)


def test_wrong_outputs_are_caught(src, tpl):
    plan, out, rec = mapping(src, [("Normal", "CMT")], template=tpl)
    assert rec.passed
    # a paragraph not mapped
    _caught(src, plan, out, MAIN, lambda r: para(r, "Coordinate").find(f"{W}pPr/{W}pStyle").set(W + "val", "Normal"),
            "w:pStyle should be")
    # pStyle not first in the pPr
    def late(r):
        ppr = para(r, "SECTION 21").find(W + "pPr")
        ppr.append(ppr.find(W + "pStyle"))
    _caught(src, plan, out, MAIN, late, "first child of the pPr")
    # the created pPr not first in the paragraph
    def ppr_last(r):
        p = para(r, "PART 2")
        p.append(p.find(W + "pPr"))
    _caught(src, plan, out, MAIN, ppr_last, "no pPr as its first child")
    # an extra pPr child changed on a mapped paragraph
    _caught(src, plan, out, MAIN, lambda r: para(r, "Coordinate").find(W + "pPr").insert(1, etree.Element(W + "keepNext")),
            "other than w:pStyle changed")
    # a paragraph mapped that should not be
    _caught(src, plan, out, MAIN, lambda r: para(r, "NOTE TO").find(f"{W}pPr/{W}pStyle").set(W + "val", "CMT"),
            "unpredicted change")
    # a pending change marker touched by a plain op
    _caught(src, plan, out, MAIN, lambda r: para(r, "Earlier").find(f"{W}pPr/{W}pPrChange").set(W + "author", "X"),
            "w:pPrChange changed by an untracked")

    plan, out, rec = mapping(src, [("ArchStrong", "CharMine"), ("ArchTable", "TblMine")], template=tpl)
    assert rec.passed

    def summary(r):
        return [x for x in para(r, "1.01").iter(W + "r") if text_of(x) == "SUMMARY"][0]
    # a run not mapped
    _caught(src, plan, out, MAIN, lambda r: summary(r).find(f"{W}rPr/{W}rStyle").set(W + "val", "ArchStrong"),
            "w:rStyle should be")
    # another run property changed on a mapped run
    _caught(src, plan, out, MAIN, lambda r: summary(r).find(W + "rPr").append(etree.Element(W + "i")),
            "other than w:rStyle changed")
    # a table style not mapped
    _caught(src, plan, out, MAIN, lambda r: r.find(f".//{W}tbl/{W}tblPr/{W}tblStyle").set(W + "val", "ArchTable"),
            "w:tblStyle should be")
    # the table style moved inside tblPr
    def moved(r):
        pr = r.find(f".//{W}tbl/{W}tblPr")
        pr.append(pr.find(W + "tblStyle"))
    _caught(src, plan, out, MAIN, moved, "in the source's place")
    # another table property changed
    _caught(src, plan, out, MAIN, lambda r: r.find(f".//{W}tbl/{W}tblPr").append(etree.Element(W + "tblLayout")),
            "unpredicted change")


def test_wrong_tracked_outputs_are_caught(src, tpl):
    plan, out, rec = mapping(with_mark_style(src), [("ArchArticle", "ART"), ("ArchStrong", "CharMine")], template=tpl,
                             tracked=True, author=AUTHOR)
    assert rec.passed
    data = with_mark_style(src)
    _caught(data, plan, out, MAIN, lambda r: para(r, "1.01").find(f"{W}pPr/{W}pPrChange/{W}pPr/{W}pStyle").set(
        W + "val", "ART"), "snapshot")
    _caught(data, plan, out, MAIN, lambda r: para(r, "2.01").find(W + "pPr").remove(
        para(r, "2.01").find(f"{W}pPr/{W}pPrChange")), "tracked change missing")
    _caught(data, plan, out, MAIN, lambda r: para(r, "1.01").find(f"{W}pPr/{W}rPr").remove(
        para(r, "1.01").find(f"{W}pPr/{W}rPr/{W}rPrChange")), "paragraph mark")
    _caught(data, plan, out, MAIN, lambda r: para(r, "1.01").find(f"{W}pPr/{W}rPr/{W}rStyle").set(W + "val", "ArchStrong"),
            "paragraph mark: w:rStyle should be")
    # the restyled mark moved before the pStyle: both are stripped for the residual, so only the kind sees the order
    def mark_first(r):
        ppr = para(r, "1.01").find(W + "pPr")
        ppr.insert(0, ppr.find(W + "rPr"))
    _caught(data, plan, out, MAIN, mark_first, "rPr left its place")


# ---- wrong implementations shared by plan and apply ----------------------------------------------------------------

def test_a_shared_resolution_that_ignores_names_is_caught(src, tpl, monkeypatch):
    real = map_module.resolve_style
    monkeypatch.setattr(map_module, "resolve_style", lambda styles, ref, where="": ref if ref in styles else
                        (real(styles, ref, where) if where == "the template" else None))
    plan, out, rec = mapping(src, [("Arch Article", "ART")], template=tpl)
    assert mapped_assertion(plan)["pairs"][0]["status"] == "missing" and out == src
    assert "V10" in failed(rec) and "the pairs resolve to" in v10(rec)


def test_a_shared_mapper_that_forgets_unstyled_paragraphs_is_caught(src, tpl, monkeypatch):
    def explicit_only(p, sheet):
        ps = p.find(f"{W}pPr/{W}pStyle")
        return ps.get(W + "val") if ps is not None else None
    monkeypatch.setattr(map_module, "paragraph_style_of", explicit_only)
    plan, out, rec = mapping(src, [("Normal", "CMT")], template=tpl)
    assert mapped_assertion(plan)["expected"]["paragraphs"] == 0
    assert "V10" in failed(rec) and "plan predicted 0" in v10(rec)


def test_a_shared_mapper_that_forgets_paragraph_marks_is_caught(src, tpl, monkeypatch):
    real = map_module.mapped_holders
    monkeypatch.setattr(map_module, "mapped_holders", lambda p, runs: [h for h in real(p, runs) if h[0] != "mark"])
    plan, out, rec = mapping(with_mark_style(src), [("ArchStrong", "CharMine")], template=tpl)
    assert mapped_assertion(plan)["expected"]["marks"] == 0
    assert "V10" in failed(rec) and "marks restyled here" in v10(rec)


def test_a_shared_mapper_that_forgets_tables_is_caught(src, tpl, monkeypatch):
    monkeypatch.setattr(map_module, "table_style_of", lambda tbl: None)
    plan, out, rec = mapping(src, [("ArchTable", "TblMine"), ("ArchArticle", "ART")], template=tpl)
    assert mapped_assertion(plan)["pairs"][0]["status"] == "unused"
    assert "V10" in failed(rec)


def test_a_shared_default_that_tracks_is_caught(src, tpl, monkeypatch):
    """The assertion carries the user's own words (no 'tracked'); the verifier applies its own default (plain)."""
    real = map_module.clean_params
    monkeypatch.setattr(map_module, "clean_params", lambda params: real(params)[:4] + (True,))
    plan, out, rec = mapping(src, [("ArchArticle", "ART")], template=tpl)
    assert plan.rev_delta.get("pPrChange") == 2
    assert "V10" in failed(rec) and "untracked" in v10(rec)


def test_a_shared_default_scope_that_forgets_the_headers_is_caught(src, tpl, monkeypatch):
    real = map_module.scope_parts
    monkeypatch.setattr(map_module, "scope_parts",
                        lambda pkg, scope: real(pkg, ["document"]) if len(scope) == 6 else real(pkg, scope))
    plan, out, rec = mapping(src, [("Footer", "CMT")], template=tpl)
    assert "V10" in failed(rec) and "scope resolves to" in v10(rec)


# ---- conflicts (rule 9) --------------------------------------------------------------------------------------------

def _spec(*ops):
    return {"request": "r", "summary": "s", "mode": "formatting", "ops": list(ops)}


def _conflict(data, *ops):
    with pytest.raises(JobException) as e:
        plan_spec(DocxPackage(data), _spec(*ops))
    assert e.value.code == "conflict", str(e.value)
    return str(e.value)


def test_restyling_beside_ops_that_read_the_paragraphs_style_conflicts(src, tpl):
    arts = {"paragraphs": {"style_in": ["ArchArticle"]}}
    m = {"op": "map_styles", "params": {"map": [{"from": "ArchArticle", "to": "ART"}], "template": tpl}}
    _conflict(src, m, {"op": "set_properties", "select": arts, "params": {"run": {"b": True}}})
    _conflict(src, m, {"op": "set_properties", "select": arts, "params": {"paragraph": {"keepNext": True}}})
    _conflict(src, m, {"op": "set_tab_stops", "select": arts, "params": {"set": [{"pos": 720}]}})
    # set_properties on other paragraphs: one spec, verified
    other = {"op": "set_properties", "select": {"paragraphs": {"text_regex": "^NOTE TO"}}, "params": {"run": {"b": True}}}
    plan, frags = plan_spec(DocxPackage(src), _spec(m, other))
    out = apply_spec(DocxPackage(src), frags).output_bytes()
    rec = run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")
    assert rec.passed, rec.to_markdown()


def test_restyling_beside_a_change_of_the_styles_it_names_conflicts(src, tpl):
    replace = {"op": "map_styles", "params": {"map": [{"from": "ArchArticle", "to": "Heading 1"}], "template": tpl,
                                              "existing": "replace"}}
    keep = {"op": "map_styles", "params": {"map": [{"from": "ArchArticle", "to": "Heading 1"}]}}
    for m in (replace, keep):
        _conflict(src, m, {"op": "set_properties", "select": {"styles": {"ids": ["Heading1"]}},
                           "params": {"run": {"i": True}}})
        _conflict(src, m, {"op": "set_properties", "select": {"styles": {"ids": ["ArchArticle"]}},
                           "params": {"run": {"i": True}}})
    _conflict(src, replace, {"op": "set_style_font", "select": {"styles": {"ids": ["Heading1"]}},
                             "params": {"font": "Arial"}})


def test_restyling_beside_the_header_footer_templating_op_conflicts(tpl):
    data = build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS")
    hf = copy.deepcopy(load_spec(HF_SPEC)["ops"][0])
    importing = {"op": "map_styles", "params": {"map": [{"from": "Normal", "to": "CMT"}], "template": tpl},
                 "select": {"scope": ["document"]}}
    assert "styles.xml" in _conflict(data, hf, importing)                    # it plans from styles.xml as a whole
    everywhere = {"op": "map_styles", "params": {"map": [{"from": "Normal", "to": "Heading 2"}]}}
    _conflict(data, everywhere, hf)                                         # it removes the header parts mapped over


# ---- fix round (adversarial review): the template pinned without an import, uses it cannot restyle, the mark ---------

def test_c8_apply_refuses_a_changed_template_even_with_nothing_imported(src, tpl):
    """The pairs were resolved through the template (rule 12): apply refuses a changed one although the mapping uses the
    file's own Heading1 and imports nothing."""
    pkg = DocxPackage(src)
    f = get_op("map_styles").plan(pkg, {}, {"map": [{"from": "ArchArticle", "to": "Heading 1"}], "template": tpl})
    assert f.transform_data["import"] is None and f.transform_data["template_md5"]
    Path(tpl).write_bytes(mutate(Path(tpl).read_bytes(), STYLES,
                                 lambda r: r.append(etree.Element(W + "style", {W + "styleId": "Extra"}))))
    with pytest.raises(JobException) as e:
        get_op("map_styles").apply(pkg.copy(), f)
    assert e.value.code == "internal" and "changed between plan and apply" in str(e.value)


def _strong_used_by(src, where):
    """The architect's Arch Strong also used by a content control's run properties, an equation run or a list level."""
    m = "http://schemas.openxmlformats.org/officeDocument/2006/math"

    def body(root):
        p = para(root, "Coordinate")
        if where == "sdt":
            sdt = etree.SubElement(p, W + "sdt")
            pr = etree.SubElement(sdt, W + "sdtPr")
            etree.SubElement(etree.SubElement(pr, W + "rPr"), W + "rStyle", {W + "val": "ArchStrong"})
            r = etree.SubElement(etree.SubElement(sdt, W + "sdtContent"), W + "r")
            etree.SubElement(r, W + "t").text = " (control)"
        else:
            om = etree.SubElement(p, f"{{{m}}}oMath")
            mr = etree.SubElement(om, f"{{{m}}}r")
            etree.SubElement(etree.SubElement(mr, W + "rPr"), W + "rStyle", {W + "val": "ArchStrong"})
            etree.SubElement(mr, f"{{{m}}}t").text = "x"

    def level(root):
        lvl = root.find(f"{W}abstractNum/{W}lvl")
        rpr = lvl.find(W + "rPr")
        if rpr is None:
            rpr = etree.SubElement(lvl, W + "rPr")
        rpr.insert(0, etree.Element(W + "rStyle", {W + "val": "ArchStrong"}))
    return mutate(src, "word/numbering.xml", level) if where == "lvl" else mutate(src, MAIN, body)


@pytest.mark.parametrize("where,needle", [("sdt", "content control"), ("math", "equation"), ("lvl", "list level")])
def test_c9_uses_the_op_cannot_restyle_are_refused(src, tpl, where, needle):
    with pytest.raises(JobException) as e:
        plan_only(_strong_used_by(src, where), [("ArchStrong", "CharMine")], template=tpl)
    assert e.value.code == "structure" and needle in str(e.value) and "ArchStrong" in str(e.value)


def test_c9_a_shared_scan_that_misses_them_is_caught(src, tpl, monkeypatch):
    monkeypatch.setattr(map_module, "unmapped_uses", lambda pkg, parts, styles: [])
    for where in ("sdt", "math", "lvl"):
        data = _strong_used_by(src, where)
        plan, out, rec = mapping(data, [("ArchStrong", "CharMine")], template=tpl)
        assert "V10" in failed(rec) and "does not restyle" in v10(rec), where


def test_d2_a_run_property_op_on_the_restyled_paragraphs_mark_plans_and_passes(src, tpl):
    """set_font over everything changes the paragraph marks of the paragraphs map_styles restyles: its own kind checks
    the marks; mapped_styles leaves the mark's rPr to the residual (it did not restyle the mark)."""
    spec = {"request": "r", "summary": "s", "mode": "formatting",
            "ops": [{"op": "set_font", "params": {"font": "Arial"}},
                    {"op": "map_styles", "params": {"map": [{"from": "Arch Para", "to": "Arch Unused"}]}}]}
    plan, frags = plan_spec(DocxPackage(src), spec)
    out = apply_spec(DocxPackage(src), frags).output_bytes()
    rec = run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")
    assert rec.passed, rec.to_markdown()


def test_d2_a_mark_changed_by_nobody_s_kind_still_fails(src, tpl):
    """map_styles alone: a w:caps added to a restyled paragraph's mark is no one's change — the residual catches it."""
    plan, out, rec = mapping(src, [("Arch Para", "Arch Unused")])
    assert rec.passed

    def caps(root):
        rpr = para(root, "A.").find(f"{W}pPr/{W}rPr")
        rpr.insert(0, etree.Element(W + "caps"))
    _caught(src, plan, out, MAIN, caps, "unpredicted change")


# ---- through a spec and a batch ------------------------------------------------------------------------------------

def test_a_spec_with_a_relative_template_runs_through_the_batch(src, tpl_bytes, tmp_path):
    from spec_surgeon.batch import run_batch
    folder = tmp_path / "job"
    folder.mkdir()
    (folder / "OFFICE STYLES.docx").write_bytes(tpl_bytes)
    (folder / "21 13 13 – SPEC.docx").write_bytes(src)
    spec = {"request": "Use my styles: Heading 1 becomes PRT, Arch Article becomes ART", "summary": "map styles",
            "mode": "formatting",
            "ops": [{"op": "map_styles", "params": {"map": [{"from": "Heading 1", "to": "PRT"},
                                                            {"from": "Arch Article", "to": "ART"}],
                                                    "template": "OFFICE STYLES.docx"}}]}
    (folder / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    loaded = load_spec(folder / "spec.json")
    batch = run_batch([str(folder / "21 13 13 – SPEC.docx")], "edit-spec", {}, tmp_path / "out", workers=1,
                      renderer="none", do_render=False, spec=loaded)
    f = batch.files[0]
    assert f.status == "PASS", f.record.to_markdown() if f.record else f.exception
