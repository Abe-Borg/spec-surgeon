"""The run selector ``color_in`` (built-in batch 3, item 24 — docs/BUILTIN_OPS_BATCH3_DESIGN.md §1): "the editor's blue
text to black" end to end with set_properties, the same selector under set_font, the two readings compared, a wrong
OUTPUT and wrong IMPLEMENTATIONS shared by plan and apply — each caught by V10 alone."""
from __future__ import annotations

import io
import zipfile
from typing import List, Tuple

import pytest
from lxml import etree

from make_fixtures import build_formatted, build_tabs
from spec_surgeon.ooxml import paragraphs
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import markup as markup_module
from spec_surgeon.ops import selectors as selectors_module
from spec_surgeon.ops.selectors import StyleSheet, effective_color, rpr_selected, runs_by_paragraph
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.verify import props as vprops
from spec_surgeon.verify import run_suite
from spec_surgeon.verify.reader import STYLES_REL, VPackage

BLUE = {"runs": {"color_in": ["0000FF"]}}


@pytest.fixture(scope="module")
def tabs() -> bytes:
    return build_tabs()


def run_ops(src: bytes, specs: List[Tuple[str, dict, dict]]):
    pkg = DocxPackage(src)
    frags = [get_op(o).plan(pkg, sel, par) for o, sel, par in specs]
    plan = merge_fragments(frags)
    work = pkg.copy()
    for f in frags:
        get_op(f.op).apply(work, f)
    out = work.output_bytes()
    return plan, out, run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")


def failed(rec):
    return [c.id for c in rec.checks if c.status == "FAIL"]


def v10(rec) -> str:
    """V10's summary and every error it recorded (the summary shows only the first three)."""
    c = next(c for c in rec.checks if c.id == "V10")
    return c.summary + " | " + " | ".join(c.data.get("errors", []))


def colour_of(run):
    c = run.find(W + "rPr/" + W + "color")
    return None if c is None else {etree.QName(k).localname: v for k, v in c.attrib.items()}


def run_with_text(root, needle):
    return [r for r in root.iter(W + "r") if any(needle in (t.text or "") for t in r.iter(W + "t"))][0]


def para_with_text(root, needle):
    return [p for p in root.iter(W + "p") if needle in "".join(t.text or "" for t in p.iter(W + "t"))][0]


def mutate(docx: bytes, part: str, fn) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(docx))
    root = etree.fromstring(zin.read(part))
    fn(root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            z.writestr(info, serialize_xml(root) if info.filename == part else zin.read(info.filename))
    return buf.getvalue()


# ---- the request, end to end ---------------------------------------------------------------------------------------

def test_editors_blue_text_to_black_changes_only_the_blue(tabs):
    """Blue set directly, blue from a character style and blue from a paragraph style (its mark too) become
    Automatic; theme blue (another colour), red and the hyperlink blue stay; a whole-document recolour is plain."""
    plan, out, rec = run_ops(tabs, [("set_properties", BLUE, {"run": {"color": "auto"}})])
    assert rec.passed, rec.to_markdown()
    a = plan.property_assertions[0]
    assert (a["expected_runs"], a["expected_marks"]) == (3, 1)
    assert plan.ops[0]["params"]["tracked"] is False and not {k: v for k, v in plan.rev_delta.items() if v}
    assert plan.geometry_reference == "source"                    # colour alone: V7 still proves nothing moved
    root = DocxPackage(out).xml("word/document.xml")
    for needle in ("direct blue", "note style blue", "Blue note paragraph"):
        assert colour_of(run_with_text(root, needle)) == {"val": "auto"}, needle
    assert colour_of(run_with_text(root, "theme blue")) == {"val": "4472C4", "themeColor": "accent1"}
    assert colour_of(run_with_text(root, "red words")) == {"val": "FF0000"}
    assert colour_of(run_with_text(root, "kept red")) == {"val": "FF0000"}
    assert colour_of(run_with_text(root, "link blue")) == {"val": "0563C1"}
    mark = para_with_text(root, "Blue note paragraph").find(W + "pPr/" + W + "rPr/" + W + "color")
    assert mark is not None and mark.get(W + "val") == "auto"


def test_theme_names_and_values_match_case_insensitively(tabs):
    for cols in (["accent1"], ["ACCENT1"], ["4472c4"], ["#4472C4"]):
        plan, out, rec = run_ops(tabs, [("set_properties", {"runs": {"color_in": cols}}, {"run": {"color": "000000"}})])
        assert rec.passed, (cols, rec.to_markdown())
        assert plan.property_assertions[0]["expected_runs"] == 1, cols
        root = DocxPackage(out).xml("word/document.xml")
        assert colour_of(run_with_text(root, "theme blue")) == {"val": "000000"}      # the theme attribute goes too


def test_targeted_recolour_is_tracked(tabs):
    sel = {"paragraphs": {"text_regex": "^Colours"}, "runs": {"color_in": ["0000FF", "FF0000"]}}
    plan, out, rec = run_ops(tabs, [("set_properties", sel, {"run": {"color": "auto"}, "author": "Abraham Borg"})])
    assert rec.passed, rec.to_markdown()
    assert plan.property_assertions[0]["expected_runs"] == 3 and plan.rev_delta["rPrChange"] == 3


def test_set_font_takes_color_in_too(tabs):
    plan, out, rec = run_ops(tabs, [("set_font", {"runs": {"color_in": ["FF0000"]}}, {"font": "Courier New"})])
    assert rec.passed, rec.to_markdown()
    assert plan.property_assertions[0]["expected_runs"] == 2
    root = DocxPackage(out).xml("word/document.xml")
    assert run_with_text(root, "red words").find(W + "rPr/" + W + "rFonts").get(W + "ascii") == "Courier New"
    assert run_with_text(root, "direct blue").find(W + "rPr/" + W + "rFonts") is None


def test_a_colour_list_that_is_not_one_is_refused(tabs):
    for cols in (["blue"], [], "0000FF", ["0000F"], [7]):
        with pytest.raises(JobException) as e:
            get_op("set_properties").plan(DocxPackage(tabs), {"runs": {"color_in": cols}}, {"run": {"color": "auto"}})
        assert e.value.code == "bad-params", cols
    assert rpr_selected({"has_direct_font": True}, None, False) is False         # the old call shape still works
    with pytest.raises(JobException) as e:
        rpr_selected({"color_in": ["0000FF"]}, None, False)                        # color_in needs its paragraph
    assert e.value.code == "internal"


# ---- the two readings agree ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("build", [build_tabs, build_formatted])
def test_transform_and_verifier_colour_selectors_agree(build):
    src = build()
    pkg, vpkg = DocxPackage(src), VPackage(src)
    sheet = StyleSheet(pkg)
    styles = vprops.Styles(vpkg.xml(vpkg.related(STYLES_REL)))
    lists = [["0000FF"], ["accent1"], ["text2"], ["auto"], ["FF0000", "1f497d"], ["#0563C1"]]
    n = 0
    for part in pkg.text_parts():
        root, vroot = pkg.xml(part), vpkg.xml(part)
        pars, vpars = paragraphs(root), list(vroot.iter(W + "p"))
        groups, vgroups = runs_by_paragraph(root, pars), runs_by_paragraph(vroot, vpars)
        for p, vp, rs, vrs in zip(pars, vpars, groups, vgroups):
            holders = [(r.find(W + "rPr"), vr.find(W + "rPr")) for r, vr in zip(rs, vrs)]
            holders.append((p.find(W + "pPr/" + W + "rPr"), vp.find(W + "pPr/" + W + "rPr")))
            for rpr, vrpr in holders:
                for cols in lists:
                    pred = {"color_in": cols}
                    t = rpr_selected(pred, rpr, False, p, sheet)
                    v = vprops.target_matches(pred, vrpr, False, set(),
                                              lambda: vprops._v_run_prop(vrpr, vp, styles, "color")[0])
                    assert t == v, (part, cols, effective_color(rpr, p, sheet))
                    n += t
    assert n > 3


# ---- a wrong output, wrong implementations -------------------------------------------------------------------------

def test_a_blue_run_left_blue_is_caught(tabs):
    plan, out, _ = run_ops(tabs, [("set_properties", BLUE, {"run": {"color": "auto"}})])

    def leave_blue(root):
        c = run_with_text(root, "note style blue").find(W + "rPr/" + W + "color")
        c.getparent().remove(c)
    bad = mutate(out, "word/document.xml", leave_blue)
    rec = run_suite(tabs, bad, plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec) and "0000FF" in v10(rec)


def test_a_shared_matcher_that_forgets_the_styles_is_caught(tabs, monkeypatch):
    """Plan and apply both think a run's colour is its DIRECT colour only: the style-blue run and the BlueNote paragraph
    are never chosen. The verifier resolves the styles itself and counts them."""
    def direct_only(rpr, p, sheet):
        c = rpr.find(W + "color") if rpr is not None else None
        return ("auto", None, None, None) if c is None else (c.get(W + "val"), c.get(W + "themeColor"), None, None)
    monkeypatch.setattr(selectors_module, "effective_color", direct_only)
    plan, out, rec = run_ops(tabs, [("set_properties", BLUE, {"run": {"color": "auto"}})])
    assert plan.property_assertions[0]["expected_runs"] == 1
    assert "V10" in failed(rec) and "plan predicted 1" in v10(rec)


def test_a_shared_matcher_that_forgets_theme_names_is_caught(tabs, monkeypatch):
    monkeypatch.setattr(markup_module, "color_in",
                        lambda color, wanted: bool(color[0]) and color[0].casefold() in wanted)
    plan, out, rec = run_ops(tabs, [("set_properties", {"runs": {"color_in": ["accent1"]}}, {"run": {"color": "auto"}})])
    assert plan.property_assertions[0]["expected_runs"] == 0
    assert "V10" in failed(rec) and "plan predicted 0" in v10(rec)
