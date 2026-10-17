"""clear_direct_formatting and V10's ``cleared_formatting`` kind (built-in batch 4, item 28 —
docs/BUILTIN_OPS_BATCH4_DESIGN.md §3): end to end on the architect's spec (plain and tracked, keep/also, the run
selector, run/paragraph off, the symbol rule, hidden text, superscripts, list numbers, emptied rPr/pPr, already clean);
a wrong OUTPUT per rule; wrong IMPLEMENTATIONS shared by plan and apply; the two decisions compared; conflicts;
parameters; the reuse API apply_style builds on."""
from __future__ import annotations

import ast
import io
import zipfile
from pathlib import Path
from typing import List, Tuple

import pytest
from lxml import etree

from make_fixtures import build_formatted, build_rich, build_styles
from spec_surgeon.editspec import plan_spec, validate_spec
from spec_surgeon.ooxml import RevisionStamp, paragraphs
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import clear_formatting as clear_module
from spec_surgeon.ops.selectors import StyleSheet, runs_by_paragraph
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.report import FAILURE_EXPLANATIONS
from spec_surgeon.verify import clearing as vclear
from spec_surgeon.verify import props as vprops
from spec_surgeon.verify import run_suite
from spec_surgeon.verify.reader import STYLES_REL, VPackage

AUTHOR = "Abraham Borg"
MAIN = "word/document.xml"
OP = "clear_direct_formatting"


@pytest.fixture(scope="module")
def src() -> bytes:
    return build_styles()


def run_ops(data: bytes, specs: List[Tuple[str, dict, dict]]):
    pkg = DocxPackage(data)
    frags = [get_op(o).plan(pkg, sel, par) for o, sel, par in specs]
    plan = merge_fragments(frags)
    work = pkg.copy()
    for f in frags:
        get_op(f.op).apply(work, f)
    out = work.output_bytes()
    return plan, out, run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")


def clear(data: bytes, select: dict, params: dict):
    return run_ops(data, [(OP, select, params)])


def failed(rec):
    return [c.id for c in rec.checks if c.status == "FAIL"]


def v10(rec) -> str:
    c = next(c for c in rec.checks if c.id == "V10")
    return c.summary + " | " + " | ".join(c.data.get("errors", []))


def text_of(p) -> str:
    return "".join(t.text or "" for t in p.iter(W + "t"))


def para(root, needle):
    return [p for p in root.iter(W + "p") if text_of(p).startswith(needle)][0]


def run_of(root, needle):
    for r in root.iter(W + "r"):
        if "".join(t.text or "" for t in r.findall(W + "t")) == needle:
            return r
    raise AssertionError(needle)


def sym_run(root):
    return next(r for r in root.iter(W + "r") if r.find(W + "sym") is not None)


def locals_of(el) -> List[str]:
    return [etree.QName(c).localname for c in el] if el is not None else []


def root_of(data: bytes, part: str = MAIN):
    return DocxPackage(data).xml(part)


def mutate(docx: bytes, part: str, fn) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(docx))
    root = etree.fromstring(zin.read(part))
    fn(root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            z.writestr(info, serialize_xml(root) if info.filename == part else zin.read(info.filename))
    return buf.getvalue()


def el(xml: str):
    return etree.fromstring(f'<w:x xmlns:w="{W[1:-1]}">{xml}</w:x>')[0]


def _caught(src, plan, out, part, fn, needle=None):
    bad = mutate(out, part, fn)
    rec = run_suite(src, bad, plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec), rec.to_markdown()
    if needle:
        assert needle in v10(rec), v10(rec)


# ---- end to end ----------------------------------------------------------------------------------------------------

def test_whole_document_plain_clears_to_the_styles(src):
    plan, out, rec = clear(src, {}, {"author": AUTHOR})
    assert rec.passed, rec.to_markdown()
    a = plan.property_assertions[0]
    assert (a["expected_runs"], a["expected_marks"], a["expected_paragraphs"]) == (8, 1, 6)
    assert "rPrChange" not in plan.rev_delta and plan.geometry_reference == "none"
    root = root_of(out)
    title = para(root, "SECTION")
    assert title.find(W + "pPr") is None                                   # jc gone, the emptied pPr removed
    assert locals_of(title.find(W + "r/" + W + "rPr")) == ["lang"]          # font, bold, caps, size gone; lang stays
    assert para(root, "WET-PIPE").find(W + "pPr") is None
    assert run_of(root, "WET-PIPE SPRINKLER SYSTEMS").find(W + "rPr") is None   # the emptied rPr removed
    a_par = para(root, "A.")
    assert locals_of(a_par.find(W + "pPr")) == ["pStyle", "rPr"]            # spacing/ind gone: ArchPara's own apply
    assert locals_of(a_par.find(W + "pPr/" + W + "rPr")) == ["lang"]        # the mark: bold and size gone
    assert run_of(root, "wet-pipe").find(W + "rPr") is None                 # italic + colour
    assert locals_of(run_of(root, "2").find(W + "rPr")) == ["vertAlign"]    # the superscript stays
    assert locals_of(run_of(root, "[verify]").find(W + "rPr")) == ["highlight"]
    assert locals_of(run_of(root, " hidden note").find(W + "rPr")) == ["vanish"]   # hidden text stays hidden
    assert locals_of(run_of(root, " voir annexe").find(W + "rPr")) == ["lang"]
    assert locals_of(run_of(root, " colour").find(W + "rPr")) == ["lang"]
    assert locals_of(run_of(root, "l").find(W + "rPr")) == ["rFonts", "caps"]      # Wingdings keeps its font and caps
    assert sym_run(root).find(W + "rPr") is None                                   # the w:sym run's colour goes
    assert locals_of(para(root, "Manual list item").find(W + "pPr")) == ["numPr"]  # the list number stays
    assert locals_of(para(root, "Coordinate").find(W + "pPr")) == []  or para(root, "Coordinate").find(W + "pPr") is None
    earlier = para(root, "Earlier")
    assert locals_of(earlier.find(W + "pPr")) == ["pPrChange"]
    assert locals_of(earlier.find(W + "r/" + W + "rPr")) == ["rPrChange"]
    assert any("earlier reviewer" in f for f in plan.flags)
    assert any("kept unless asked" in n and "highlight" in n for n in plan.notes)
    # styles, character styles and the footer (out of scope) are untouched
    assert locals_of(run_of(root, " (general)").find(W + "rPr")) == ["rStyle"]
    assert locals_of(para(root_of(out, "word/footer1.xml"), "Page").find(W + "pPr")) == ["pStyle", "tabs"]


def test_targeted_clearing_is_tracked_with_snapshots(src):
    sel = {"paragraphs": {"text_regex": "^(SECTION|A\\.|Manual)"}}
    plan, out, rec = clear(src, sel, {"author": AUTHOR})
    assert rec.passed, rec.to_markdown()
    a = plan.property_assertions[0]
    assert (a["expected_runs"], a["expected_marks"], a["expected_paragraphs"]) == (5, 1, 3)
    assert plan.rev_delta["rPrChange"] == 6 and plan.rev_delta["pPrChange"] == 3
    root, s_root = root_of(out), root_of(src)
    r, s_r = run_of(root, "SECTION 21 13 13"), run_of(s_root, "SECTION 21 13 13")
    chg = r.find(W + "rPr/" + W + "rPrChange")
    assert chg.get(W + "author") == AUTHOR and locals_of(r.find(W + "rPr")) == ["lang", "rPrChange"]
    assert [serialize_xml(c) for c in chg.find(W + "rPr")] == [serialize_xml(c) for c in s_r.find(W + "rPr")]
    title = para(root, "SECTION")
    assert locals_of(title.find(W + "pPr")) == ["pPrChange"]
    assert locals_of(title.find(W + "pPr/" + W + "pPrChange/" + W + "pPr")) == ["jc"]
    mark = para(root, "A.").find(W + "pPr/" + W + "rPr")
    assert locals_of(mark) == ["lang", "rPrChange"]
    assert locals_of(para(root, "A.").find(W + "pPr")) == ["pStyle", "rPr", "pPrChange"]
    # tracked: nothing emptied is removed — the change marker stays
    assert locals_of(run_of(root, "wet-pipe").find(W + "rPr")) == ["rPrChange"]


def test_existing_change_markers_refuse_tracked_and_are_flagged_plain(src):
    sel = {"paragraphs": {"text_regex": "^Earlier"}}
    with pytest.raises(JobException) as e:
        get_op(OP).plan(DocxPackage(src), sel, {})
    assert e.value.code == "tracked-format"
    plan, out, rec = clear(src, sel, {"tracked": False, "author": AUTHOR})
    assert rec.passed, rec.to_markdown()
    assert any("earlier reviewer" in f for f in plan.flags)


def test_a_tracked_change_on_a_pending_mark_revision_refuses(src):
    def pending_mark(root):
        mrpr = para(root, "A.").find(W + "pPr/" + W + "rPr")
        mrpr.insert(0, el(f'<w:ins w:id="900" w:author="Jeremy" w:date="2026-09-22T11:00:00Z"/>'))
    data = mutate(src, MAIN, pending_mark)
    with pytest.raises(JobException) as e:
        get_op(OP).plan(DocxPackage(data), {"paragraphs": {"text_regex": "^A\\."}}, {})
    assert e.value.code == "tracked-format" and "pending revision" in e.value.reason
    plan, out, rec = clear(data, {"paragraphs": {"text_regex": "^A\\."}}, {"tracked": False})
    assert rec.passed, rec.to_markdown()
    assert locals_of(para(root_of(out), "A.").find(W + "pPr/" + W + "rPr")) == ["ins", "lang"]   # the marker stays


def test_keep_and_also(src):
    plan, out, rec = clear(src, {}, {"keep": ["b", "bCs"], "also": ["highlight", "vertAlign"]})
    assert rec.passed, rec.to_markdown()
    root = root_of(out)
    assert locals_of(run_of(root, "WET-PIPE SPRINKLER SYSTEMS").find(W + "rPr")) == ["b"]
    assert run_of(root, "2").find(W + "rPr") is None and run_of(root, "[verify]").find(W + "rPr") is None
    assert locals_of(para(root, "A.").find(W + "pPr/" + W + "rPr")) == ["b", "lang"]
    # an outline level stays unless asked
    def outline(root):
        para(root, "Coordinate").find(W + "pPr").append(el('<w:outlineLvl w:val="1"/>'))
    data = mutate(src, MAIN, outline)
    plan, out, rec = clear(data, {}, {})
    assert rec.passed and locals_of(para(root_of(out), "Coordinate").find(W + "pPr")) == ["outlineLvl"]
    plan, out, rec = clear(data, {}, {"also": ["outlineLvl"]})
    assert rec.passed and para(root_of(out), "Coordinate").find(W + "pPr") is None


def test_the_run_selector_narrows_runs_and_marks_only(src):
    plan, out, rec = clear(src, {"runs": {"color_in": ["0000FF"]}}, {"paragraph": False})
    assert rec.passed, rec.to_markdown()
    a = plan.property_assertions[0]
    assert (a["expected_runs"], a["expected_marks"], a["expected_paragraphs"]) == (1, 0, 0)
    root = root_of(out)
    assert run_of(root, "wet-pipe").find(W + "rPr") is None
    assert locals_of(run_of(root, "WET-PIPE SPRINKLER SYSTEMS").find(W + "rPr")) == ["b"]
    # with paragraph formatting on, the selected paragraphs' pPr are cleared whatever the runs
    plan, out, rec = clear(src, {"runs": {"has_direct_font": True}}, {})
    assert rec.passed, rec.to_markdown()
    assert plan.property_assertions[0]["expected_paragraphs"] == 6
    assert locals_of(run_of(root_of(out), "l").find(W + "rPr")) == ["rFonts", "caps"]


def test_paragraph_false_and_run_false(src):
    plan, out, rec = clear(src, {}, {"paragraph": False})
    assert rec.passed, rec.to_markdown()
    root = root_of(out)
    assert locals_of(para(root, "SECTION").find(W + "pPr")) == ["jc"]
    assert locals_of(para(root, "A.").find(W + "pPr")) == ["pStyle", "spacing", "ind", "rPr"]
    assert locals_of(para(root, "A.").find(W + "pPr/" + W + "rPr")) == ["lang"]
    plan, out, rec = clear(src, {}, {"run": False})
    assert rec.passed, rec.to_markdown()
    root = root_of(out)
    assert locals_of(para(root, "A.").find(W + "pPr")) == ["pStyle", "rPr"]
    assert locals_of(para(root, "A.").find(W + "pPr/" + W + "rPr")) == ["b", "sz", "lang"]
    assert locals_of(run_of(root, "wet-pipe").find(W + "rPr")) == ["i", "color"]
    # a mark emptied with nothing else in its pPr: the pPr goes too
    def bare_mark(root):
        p = para(root, "PART 2")
        p.insert(0, el('<w:pPr><w:rPr><w:b/></w:rPr></w:pPr>'))
    data = mutate(src, MAIN, bare_mark)
    plan, out, rec = clear(data, {}, {"paragraph": False})
    assert rec.passed, rec.to_markdown()
    assert para(root_of(out), "PART 2").find(W + "pPr") is None


def _symbol_note(src):
    """The ArchNote style takes a Symbol font, and its paragraph's run direct bold and caps: a symbol run by the
    paragraph style chain alone."""
    def style_font(root):
        st = next(s for s in root.iter(W + "style") if s.get(W + "styleId") == "ArchNote")
        st.find(W + "rPr").insert(0, el('<w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/>'))
    data = mutate(src, "word/styles.xml", style_font)

    def direct(root):
        r = para(root, "NOTE TO SPECIFIER").find(W + "r")
        r.insert(0, el('<w:rPr><w:b/><w:caps/><w:sz w:val="18"/></w:rPr>'))
        sym = sym_run(root).find(W + "rPr")
        sym.insert(0, el("<w:caps/>"))
    return mutate(data, MAIN, direct)


def test_the_symbol_rule(src):
    data = _symbol_note(src)
    plan, out, rec = clear(data, {}, {})
    assert rec.passed, rec.to_markdown()
    root = root_of(out)
    assert locals_of(para(root, "NOTE TO SPECIFIER").find(W + "r/" + W + "rPr")) == ["caps"]   # by its style chain
    assert locals_of(sym_run(root).find(W + "rPr")) == ["caps"]                               # the w:sym run
    assert locals_of(run_of(root, "l").find(W + "rPr")) == ["rFonts", "caps"]                 # Wingdings
    # with skip_sym_runs false and no preserved fonts, nothing is a symbol run
    plan, out, rec = clear(data, {}, {"skip_sym_runs": False, "preserve_fonts": []})
    assert rec.passed, rec.to_markdown()
    root = root_of(out)
    assert sym_run(root).find(W + "rPr") is None and run_of(root, "l").find(W + "rPr") is None


def test_already_clean_is_left_alone(src):
    plan, out, rec = clear(src, {}, {"tracked": False})
    plan2, out2, rec2 = clear(out, {}, {"tracked": False})
    assert rec2.passed, rec2.to_markdown()
    a = plan2.property_assertions[0]
    assert (a["expected_runs"], a["expected_marks"], a["expected_paragraphs"]) == (0, 0, 0)
    assert plan2.intended_parts == [] and plan2.geometry_reference == "source"
    assert any("nothing to clear, left alone" in n for n in plan2.notes)
    assert all(t[2] == "verify" for t in get_op(OP).plan(DocxPackage(out), {}, {}).touches)


def test_runs_inside_revisions_can_be_left_alone():
    rich = build_rich()
    s_root = root_of(rich)
    revised = [(bi, ri) for bi, rs in enumerate(runs_by_paragraph(s_root, paragraphs(s_root)))
               for ri, r in enumerate(rs) if r.getparent().tag in (W + "ins", W + "del") and r.find(W + "rPr") is not None]
    assert revised
    p_all, out, rec = clear(rich, {}, {"tracked": False})
    assert rec.passed, rec.to_markdown()
    p_ex, out, rec = clear(rich, {}, {"tracked": False, "in_revisions": "exclude"})
    assert rec.passed, rec.to_markdown()
    assert p_ex.property_assertions[0]["expected_runs"] < p_all.property_assertions[0]["expected_runs"]
    o_root = root_of(out)
    s_runs, o_runs = runs_by_paragraph(s_root, paragraphs(s_root)), runs_by_paragraph(o_root, paragraphs(o_root))
    for bi, ri in revised:                                    # exactly as they were
        assert serialize_xml(o_runs[bi][ri].find(W + "rPr")) == serialize_xml(s_runs[bi][ri].find(W + "rPr"))


def test_header_and_footer_scope(src):
    plan, out, rec = clear(src, {"scope": ["footers"]}, {})
    assert rec.passed, rec.to_markdown()
    assert locals_of(para(root_of(out, "word/footer1.xml"), "Page").find(W + "pPr")) == ["pStyle"]
    assert plan.intended_parts == ["word/footer1.xml"]


def test_parameters_are_checked(src):
    op = get_op(OP)
    pkg = DocxPackage(src)
    for params in ({"keep": ["bold"]}, {"also": ["vanish"]}, {"keep": ["b"], "also": ["b"]}, {"keep": "b"},
                   {"run": False, "paragraph": False}, {"in_revisions": "maybe"}, {"run": "yes"},
                   {"preserve_fonts": "Symbol"}):
        with pytest.raises(JobException) as e:
            op.plan(pkg, {}, params)
        assert e.value.code == "bad-params", params
    for select in ({"styles": {"ids": ["Normal"]}}, {"runs": {"bold": True}}, {"paragraphs": {"text": "x"}}):
        with pytest.raises(JobException) as e:
            op.plan(pkg, select, {})
        assert e.value.code == "bad-params", select
    spec = {"request": "r", "summary": "s", "mode": "formatting",
            "ops": [{"op": OP, "select": {}, "params": {"keep": ["b"], "colour": True}}]}
    with pytest.raises(Exception) as e:
        validate_spec(spec)
    assert "colour" in str(e.value)
    # a name both lists hold (shd, spacing) applies to runs and to paragraphs alike
    run, par = clear_module.cleared_names(keep=["shd"])
    assert "shd" not in run and "shd" not in par


# ---- wrong outputs ---------------------------------------------------------------------------------------------------

def test_wrong_outputs_are_caught(src):
    plan, out, rec = clear(src, {}, {"author": AUTHOR})
    assert rec.passed
    # a kept child removed from a cleared run (its language)
    _caught(src, plan, out, MAIN, lambda r: run_of(r, " colour").find(W + "rPr").remove(
        run_of(r, " colour").find(W + "rPr/" + W + "lang")), "kept but missing")
    # a cleared child left in place
    _caught(src, plan, out, MAIN, lambda r: run_of(r, "l").find(W + "rPr").append(el('<w:sz w:val="16"/>')),
            "cleared but still there")
    # a symbol run's font removed
    _caught(src, plan, out, MAIN, lambda r: run_of(r, "l").find(W + "rPr").remove(
        run_of(r, "l").find(W + "rPr/" + W + "rFonts")), "rFonts")
    # an emptied rPr left in place
    _caught(src, plan, out, MAIN, lambda r: run_of(r, "wet-pipe").insert(0, etree.Element(W + "rPr")),
            "emptied w:rPr")
    # an emptied pPr left in place
    _caught(src, plan, out, MAIN, lambda r: para(r, "WET-PIPE").insert(0, etree.Element(W + "pPr")),
            "emptied w:pPr")
    # a cleared paragraph property left in place
    _caught(src, plan, out, MAIN, lambda r: para(r, "A.").find(W + "pPr").insert(1, el('<w:spacing w:after="120"/>')),
            "paragraph properties are")
    # the list number dropped
    _caught(src, plan, out, MAIN, lambda r: para(r, "Manual list item").remove(
        para(r, "Manual list item").find(W + "pPr")), "numPr")
    # the mark's language dropped with the rest
    _caught(src, plan, out, MAIN, lambda r: para(r, "A.").find(W + "pPr").remove(
        para(r, "A.").find(W + "pPr/" + W + "rPr")))
    # someone's earlier change marker removed (plain must leave it)
    _caught(src, plan, out, MAIN, lambda r: para(r, "Earlier").find(W + "r/" + W + "rPr").remove(
        para(r, "Earlier").find(W + "r/" + W + "rPr/" + W + "rPrChange")), "rPrChange")
    # hidden text that loses its vanish (not a target: the residual)
    _caught(src, plan, out, MAIN, lambda r: run_of(r, " hidden note").remove(run_of(r, " hidden note").find(W + "rPr")))

    plan, out, rec = clear(src, {"paragraphs": {"text_regex": "^(SECTION|A\\.)"}}, {"author": AUTHOR})
    assert rec.passed

    def bad_snapshot(r):
        snap = run_of(r, "SECTION 21 13 13").find(W + "rPr/" + W + "rPrChange/" + W + "rPr")
        snap.remove(snap.find(W + "caps"))
    _caught(src, plan, out, MAIN, bad_snapshot, "snapshot")

    def mark_moved(r):                       # the mark's rPr after the pPrChange: out of place
        ppr = para(r, "A.").find(W + "pPr")
        ppr.append(ppr.find(W + "rPr"))
    _caught(src, plan, out, MAIN, mark_moved)


# ---- wrong implementations shared by plan and apply -------------------------------------------------------------------

def test_a_shared_cleared_set_that_includes_vanish_is_caught(src, monkeypatch):
    monkeypatch.setattr(clear_module, "RUN_CLEARED", clear_module.RUN_CLEARED + ("vanish",))
    plan, out, rec = clear(src, {}, {})
    assert run_of(root_of(out), " hidden note").find(W + "rPr") is None        # the hidden text became visible
    assert "V10" in failed(rec) and "plan predicted" in v10(rec)


def test_a_shared_cleared_set_that_includes_vertalign_is_caught(src, monkeypatch):
    monkeypatch.setattr(clear_module, "RUN_CLEARED", clear_module.RUN_CLEARED + ("vertAlign",))
    plan, out, rec = clear(src, {}, {})
    assert run_of(root_of(out), "2").find(W + "rPr") is None                   # m² became m2
    assert "V10" in failed(rec)


def test_a_shared_paragraph_set_that_includes_numpr_is_caught(src, monkeypatch):
    monkeypatch.setattr(clear_module, "PARA_CLEARED", clear_module.PARA_CLEARED + ("numPr",))
    plan, out, rec = clear(src, {}, {})
    assert para(root_of(out), "Manual list item").find(W + "pPr") is None
    assert "V10" in failed(rec)


def test_a_shared_symbol_rule_that_forgets_the_paragraph_style_chain_is_caught(src, monkeypatch):
    real = clear_module.is_symbol_rpr
    monkeypatch.setattr(clear_module, "is_symbol_rpr", lambda rpr, p, sheet, preserve: real(rpr, None, sheet, preserve))
    plan, out, rec = clear(_symbol_note(src), {}, {})
    assert para(root_of(out), "NOTE TO SPECIFIER").find(W + "r/" + W + "rPr") is None   # caps gone from a symbol run
    assert "V10" in failed(rec)


def test_a_shared_symbol_rule_that_forgets_wsym_is_caught(src, monkeypatch):
    real = clear_module.clean_params
    monkeypatch.setattr(clear_module, "clean_params", lambda params: clear_module.replace(real(params), skip_sym=False))
    plan, out, rec = clear(_symbol_note(src), {}, {})
    assert sym_run(root_of(out)).find(W + "rPr") is None
    assert "V10" in failed(rec)


def test_a_shared_default_symbol_font_list_is_caught(src, monkeypatch):
    """The assertion carries the user's own words (no preserve_fonts); the verifier applies its own default list."""
    monkeypatch.setattr(clear_module, "DEFAULT_PRESERVE_FONTS", [])
    plan, out, rec = clear(src, {}, {})
    assert run_of(root_of(out), "l").find(W + "rPr") is None
    assert "V10" in failed(rec)


def test_a_shared_default_that_skips_the_marks_is_caught(src, monkeypatch):
    real = clear_module.clean_params
    monkeypatch.setattr(clear_module, "clean_params", lambda params: clear_module.replace(real(params), marks=False))
    plan, out, rec = clear(src, {}, {})
    assert "V10" in failed(rec) and "paragraph mark(s) cleared here" in v10(rec)


def test_an_apply_that_leaves_emptied_elements_is_caught(src, monkeypatch):
    monkeypatch.setattr(clear_module, "_empty", lambda el: False)
    plan, out, rec = clear(src, {}, {})
    assert run_of(root_of(out), "wet-pipe").find(W + "rPr") is not None
    assert "V10" in failed(rec) and "emptied" in v10(rec)


# ---- the two decisions agree ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("params", [{}, {"keep": ["b", "bCs", "jc"], "also": ["highlight", "outlineLvl"]},
                                    {"skip_sym_runs": False, "preserve_fonts": ["Wingdings"]},
                                    {"in_revisions": "exclude", "include_paragraph_marks": False}])
def test_transform_and_verifier_decisions_agree(params):
    checked = 0
    for data in (build_styles(), _symbol_note(build_styles()), build_rich(), build_formatted()):
        pkg, vpkg = DocxPackage(data), VPackage(data)
        sheet = StyleSheet(pkg)
        vstyles = vprops.Styles(vpkg.xml(vpkg.related(STYLES_REL)))
        settings = clear_module.clean_params(params)
        st = vclear._v_settings(params)
        for part in pkg.text_parts():
            root, vroot = pkg.xml(part), vpkg.xml(part)
            pars = paragraphs(root)
            groups = runs_by_paragraph(root, pars)
            view = vprops.PartView(vroot)
            for bi, p in enumerate(pars):
                d = clear_module.paragraph_clearing(p, groups[bi], sheet, settings)
                v = vclear.v_decide(view.pars[bi], view.runs[bi], vstyles, st["run_names"], st["para_names"],
                                    exclude_rev=st["exclude_rev"], marks=st["marks"], preserve=st["preserve"],
                                    skip_sym=st["skip_sym"])
                assert {ri: set(n) for ri, n in d.runs} == v.runs, (part, bi)
                assert (None if d.mark is None else set(d.mark)) == v.mark and set(d.ppr) == v.ppr, (part, bi)
                checked += 1
    assert checked > 50


# ---- conflicts -------------------------------------------------------------------------------------------------------

def _spec(*ops):
    return {"request": "r", "summary": "s", "mode": "formatting", "ops": list(ops)}


def test_clearing_beside_other_formatting_of_the_same_paragraphs_conflicts(src):
    sel = {"paragraphs": {"text_regex": "^A\\."}}
    clear_op = {"op": OP, "select": sel, "params": {}}
    for other in ({"op": "set_properties", "select": sel, "params": {"run": {"b": True}}},
                  {"op": "set_font", "select": sel, "params": {"font": "Arial"}},
                  {"op": "set_paragraph_props", "select": sel, "params": {"spacing": {"after": 120}}},
                  {"op": "set_properties", "select": sel, "params": {"paragraph": {"keepNext": True}}}):
        with pytest.raises(JobException) as e:
            plan_spec(DocxPackage(src), _spec(clear_op, other))
        assert e.value.code == "conflict", other
    # removing a paragraph's tab stops beside a tab op that finds them already as asked: the tab op changes nothing
    # there but reads the stops in the output (verify:tabs); the clear declares one touch per pPr element it clears, so
    # the pair is a plan-time conflict, never a failure after apply
    tabs_op = {"op": "set_tab_stops", "select": {"scope": ["footers"]},
               "params": {"set": [{"pos": 9000, "align": "right"}], "tracked": False}}
    alone = get_op("set_tab_stops").plan(DocxPackage(src), tabs_op["select"], tabs_op["params"])
    assert alone.property_assertions[0]["expected_paragraphs"] == 0                      # already as asked
    frag = get_op(OP).plan(DocxPackage(src), {"scope": ["footers"]}, {"run": False})
    assert ("word/footer1.xml", "0", "tabs") in frag.touches and ("word/footer1.xml", "0", "pPr") in frag.touches
    with pytest.raises(JobException) as e:
        plan_spec(DocxPackage(src), _spec({"op": OP, "select": {"scope": ["footers"]}, "params": {"run": False}}, tabs_op))
    assert e.value.code == "conflict" and "tabs" in e.value.reason
    # one touch per cleared paragraph-property element, named by its local name
    frag = get_op(OP).plan(DocxPackage(src), {}, {"run": False})
    bi = next(i for i, p in enumerate(paragraphs(DocxPackage(src).xml(MAIN))) if text_of(p).startswith("Coordinate"))
    assert {a for m, t, a in frag.touches if m == MAIN and t == str(bi)} >= {"pPr", "pBdr", "shd"}
    # another paragraph's formatting in the same spec: verified together
    plan, frags = plan_spec(DocxPackage(src), _spec(clear_op, {"op": "set_properties",
                                                              "select": {"paragraphs": {"text_regex": "^PART 2"}},
                                                              "params": {"run": {"i": True}}}))
    work = DocxPackage(src).copy()
    for f in frags:
        get_op(f.op).apply(work, f)
    rec = run_suite(src, work.output_bytes(), plan.to_dict(), "src.docx", "out.docx")
    assert rec.passed, rec.to_markdown()


# ---- the reuse API (apply_style's direct_formatting "clear") ---------------------------------------------------------

def test_the_reuse_api_clears_and_sets_a_style_in_one_paragraph_change(src):
    """What apply_style builds on: the default rules decided by the transform's paragraph_clearing, applied by
    clear_paragraph with its own w:pStyle in the same w:pPrChange; checked by the verifier's check_runs_and_mark and
    v_expected_ppr + v_with_pstyle."""
    pkg = DocxPackage(src)
    sheet = StyleSheet(pkg)
    root = pkg.xml(MAIN)
    pars = paragraphs(root)
    groups = runs_by_paragraph(root, pars)
    bi = next(i for i, p in enumerate(pars) if text_of(p).startswith("A."))
    d = clear_module.paragraph_clearing(pars[bi], groups[bi], sheet, clear_module.ClearSettings())
    assert clear_module.check_markers(pars[bi], groups[bi], d, True, "A.", ppr_changes=True) == (0, 0)
    assert clear_module.changes_needed(d, ppr_changes=True) == len(d.runs) + 2
    work = pkg.copy()
    w_root = work.xml(MAIN)
    w_pars = paragraphs(w_root)

    def set_style(ppr):
        ps = ppr.find(W + "pStyle")
        if ps is None:
            ps = etree.Element(W + "pStyle")
            ppr.insert(0, ps)
        ps.set(W + "val", "ArchNote")
    stamp = RevisionStamp.now(AUTHOR, False)
    nxt = clear_module.clear_paragraph(w_pars[bi], runs_by_paragraph(w_root, w_pars)[bi], d, stamp, 500, set_style)
    assert nxt == 500 + clear_module.changes_needed(d, ppr_changes=True)
    work.write_xml(MAIN, w_root)
    out = work.output_bytes()
    o_ppr = paragraphs(root_of(out))[bi].find(W + "pPr")
    assert len(o_ppr.findall(W + "pPrChange")) == 1
    assert locals_of(o_ppr.find(W + "pPrChange/" + W + "pPr")) == ["pStyle", "spacing", "ind"]

    # the verifier's side: its own decision, the runs and mark, the expected pPr with the added style
    ctx = vprops.Ctx(VPackage(src), VPackage(out), {"diffs": []})
    sv, ov = ctx.view(MAIN)
    run_names, para_names = vclear.v_cleared_sets()
    vd = vclear.v_decide(sv.pars[bi], sv.runs[bi], ctx.src_styles, run_names, para_names)
    assert vd.ppr == {"spacing", "ind"} and vd.mark == {"b", "sz"}
    errs: List[str] = []
    nr, nm, stays = vclear.check_runs_and_mark(ctx, MAIN, bi, bi, vd, True, AUTHOR, errs)
    assert errs == [] and (nr, nm, stays) == (len(vd.runs), 1, True)
    assert ctx.strip_marks[MAIN] == {bi} and len(ctx.strip_runs[MAIN]) == nr
    S, O = sv.pars[bi].find(W + "pPr"), ov.pars[bi].find(W + "pPr")
    expected = vclear.v_with_pstyle(vclear.v_expected_ppr(S, vd.ppr, stays), "ArchNote")
    assert expected[0] == vprops.sig(el('<w:pStyle w:val="ArchNote"/>')) and vclear.MARK in expected
    vclear.check_cleared_ppr(S, O, expected, True, AUTHOR, "A.", errs)
    assert errs == [], errs
    # without the style in the expectation, the same output is a failure
    vclear.check_cleared_ppr(S, O, vclear.v_expected_ppr(S, vd.ppr, stays), True, AUTHOR, "A.", errs)
    assert errs and "paragraph properties are" in errs[0]
    # plain: the same helpers, no change markers, nothing emptied removed while the style stays
    work = pkg.copy()
    w_root = work.xml(MAIN)
    w_pars = paragraphs(w_root)
    clear_module.clear_paragraph(w_pars[bi], runs_by_paragraph(w_root, w_pars)[bi], d, None, 0, set_style)
    assert locals_of(w_pars[bi].find(W + "pPr")) == ["pStyle", "rPr"]


def _codes(module) -> set:
    codes = set()
    for n in ast.walk(ast.parse(Path(module.__file__).read_text(encoding="utf-8"))):
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "JobException" and len(n.args) > 1:
            assert isinstance(n.args[1], ast.Constant), ast.unparse(n)
            codes.add(n.args[1].value)
    return codes


def test_every_refusal_code_of_clear_direct_formatting_is_explained():
    codes = _codes(clear_module)
    assert codes == {"bad-params", "tracked-format"}
    assert sorted(c for c in codes if c not in FAILURE_EXPLANATIONS) == []
