"""ops/base.py — fragment merge and conflict detection (no documents needed)."""
from __future__ import annotations

from lxml import etree
import pytest

from make_fixtures import build, build_rich, build_styles, patch_docx
from spec_surgeon.editspec import apply_spec, plan_spec
from spec_surgeon.ooxml import block_full_text, paragraphs
from spec_surgeon.ops import (BlockDiff, JobException, PlanFragment, get_op, merge_fragments,
                              selectors as selectors_module)
from spec_surgeon.ops.selectors import StyleSheet, paragraph_selected
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.verify import run_suite
from spec_surgeon.verify.props import PartView, Styles, para_matches
from spec_surgeon.verify.reader import STYLES_REL, VPackage
from test_adaptive_messy import mutate_xml


def _frag(op, touches=(), mode="formatting", **kw):
    f = PlanFragment(job=op, mode=mode, params={}, intended_parts=list(kw.pop("parts", [])), op=op)
    f.touches = list(touches)
    for k, v in kw.items():
        setattr(f, k, v)
    return f


def test_disjoint_targets_merge_in_order():
    a = _frag("set_style_font", [("word/styles.xml", "style:Heading1", "rPr")], parts=["word/styles.xml"],
              property_assertions=[{"kind": "style_fonts"}])
    b = _frag("set_doc_defaults_font", [("word/styles.xml", "docDefaults", "rPr")], parts=["word/styles.xml"],
              property_assertions=[{"kind": "doc_defaults_fonts"}])
    c = _frag("set_paragraph_props", [("word/document.xml", "3", "pPr")], parts=["word/document.xml"])
    d = _frag("set_font", [("word/document.xml", "3", "rPr"), ("word/document.xml", "3", "markRPr")],
              parts=["word/document.xml"])
    plan = merge_fragments([a, b, c, d])
    assert plan.intended_parts == ["word/styles.xml", "word/document.xml"]
    assert [x["op_index"] for x in plan.property_assertions] == [0, 1]
    assert [o["op"] for o in plan.ops] == ["set_style_font", "set_doc_defaults_font", "set_paragraph_props", "set_font"]
    assert plan.mode == "formatting"


def test_same_aspect_on_same_target_conflicts():
    a = _frag("set_font", [("word/document.xml", "3", "rPr")])
    b = _frag("set_font", [("word/document.xml", "3", "rPr")])
    with pytest.raises(JobException) as e:
        merge_fragments([a, b])
    assert e.value.code == "conflict"


def test_text_edit_conflicts_with_any_aspect_on_that_block():
    a = _frag("replace_text", [("word/document.xml", "7", "text")], mode="content",
              diffs=[BlockDiff("word/document.xml", 7, "a", "ab")])
    b = _frag("set_font", [("word/document.xml", "7", "rPr")])
    with pytest.raises(JobException) as e:
        merge_fragments([a, b])
    assert e.value.code == "conflict"
    # a different block is fine
    b2 = _frag("set_font", [("word/document.xml", "8", "rPr")])
    assert merge_fragments([a, b2]).mode == "content"


def test_whole_member_touch_conflicts_with_anything_in_that_member():
    a = _frag("remove_header_footer", [("word/header1.xml", "*", "remove")], members_removed=["word/header1.xml"])
    b = _frag("set_font", [("word/header1.xml", "0", "rPr")])
    with pytest.raises(JobException) as e:
        merge_fragments([a, b])
    assert e.value.code == "conflict"


def test_mode_rules():
    tracked = _frag("replace_text", [("word/document.xml", "1", "text")], mode="content",
                    diffs=[BlockDiff("word/document.xml", 1, "x", "xy")])
    untracked = _frag("replace_text", [("word/document.xml", "2", "text")], mode="untracked",
                      diffs=[BlockDiff("word/document.xml", 2, "x", "y")], untracked_diffs_expected=True)
    with pytest.raises(JobException):
        merge_fragments([tracked, untracked])
    # untracked text diffs in a SURVIVING part cannot ride along with tracked content ops (V5b would fail) …
    cover = _frag("fix_page_numbering", [("word/footer1.xml", "*", "structure")],
                  diffs=[BlockDiff("word/footer1.xml", 0, "x", None)], untracked_diffs_expected=True)
    with pytest.raises(JobException):
        merge_fragments([tracked, cover])
    # … but deletions inside REMOVED parts can (they are accounted for by V1/V2, not by the reject view)
    removal = _frag("remove_header_footer", [("word/header1.xml", "*", "remove")],
                    diffs=[BlockDiff("word/header1.xml", 0, "x", None)], members_removed=["word/header1.xml"],
                    untracked_diffs_expected=True)
    assert merge_fragments([tracked, removal]).mode == "content"


def test_revision_ids_are_disjoint_and_geometry_none_dominates():
    a = _frag("replace_text", [("word/document.xml", "1", "text")], mode="content", rev_ids_needed=3, rev_id_base=200)
    b = _frag("set_paragraph_props", [("word/document.xml", "2", "pPr")], rev_ids_needed=2, rev_id_base=200,
              geometry_reference="none", geometry_reason="spacing changes reflow the page")
    plan = merge_fragments([a, b])
    assert a.transform_data["first_rev_id"] == 200 and b.transform_data["first_rev_id"] == 203
    assert plan.geometry_reference == "none" and "reflow" in plan.geometry_reason


# ---- an op's own parameter checks ----------------------------------------------------------------------------------

def test_replace_text_refuses_tracked_false_beside_mode_tracked():
    from make_fixtures import build
    from spec_surgeon.ops import get_op
    from spec_surgeon.package import DocxPackage
    pkg = DocxPackage(build("21 05 00", "COMMON WORK RESULTS FOR FIRE SUPPRESSION"))
    with pytest.raises(JobException):
        get_op("replace_text").plan(pkg, {}, {"find": "FM Global", "replace": "GRC", "tracked": False, "mode": "tracked"})


# ---- reads: an op's prediction depends on what another op would change (2026-10-16) ------------------------------

def test_a_whole_member_read_conflicts_with_any_change_inside_that_member_and_never_with_a_read():
    templating = _frag("apply_header_footer_template", [("word/styles.xml", "*", "verify")])
    for change in ([("word/styles.xml", "style:Normal", "rPr")], [("word/styles.xml", "docDefaults", "rPr")],
                   [("word/styles.xml", "style:Normal", "pPr")], [("word/styles.xml", "*", "remove")]):
        with pytest.raises(JobException) as e:
            merge_fragments([templating, _frag("set_style_font", change)])
        assert e.value.code == "conflict" and e.value.details["member"] == "word/styles.xml"
        assert "split out ['#2 set_style_font']" in e.value.reason or "replaces, removes or restructures" in e.value.reason
    # the other way round: the change first in the spec is the same conflict
    with pytest.raises(JobException) as e:
        merge_fragments([_frag("set_style_font", [("word/styles.xml", "style:Normal", "rPr")]), templating])
    assert e.value.code == "conflict" and e.value.details["changes"] == ["#1 set_style_font"]
    # two reads of one member never conflict: another whole-member read, a scope read, a family read
    for other in ([("word/styles.xml", "*", "verify")], [("word/styles.xml", "scope", "verify")],
                  [("word/styles.xml", "docDefaults", "verify:rPr")]):
        plan = merge_fragments([templating, _frag("set_font", other)])
        assert [o["op"] for o in plan.ops] == ["apply_header_footer_template", "set_font"]
    # the reader's own change inside the member it reads (an imported style) is no conflict with its own read
    own = _frag("apply_header_footer_template", [("word/styles.xml", "*", "verify"), ("word/styles.xml", "style:HFStamp", "add")])
    assert merge_fragments([own, _frag("set_font", [("word/document.xml", "3", "rPr")])]).mode == "formatting"
    # a change of another member is no conflict either
    assert merge_fragments([templating, _frag("set_font", [("word/document.xml", "3", "rPr")])]).mode == "formatting"
    # two readers of one member, one of which also changes something inside it: the other's read meets that change
    with pytest.raises(JobException) as e:
        merge_fragments([templating, own])
    assert e.value.code == "conflict" and e.value.details["reads"] == ["#1 apply_header_footer_template"]
    assert e.value.details["changes"] == ["#2 apply_header_footer_template"]
    with pytest.raises(JobException) as e:
        merge_fragments([own, templating])
    assert e.value.details["reads"] == ["#2 apply_header_footer_template"] and e.value.details["changes"] == ["#1 apply_header_footer_template"]


def test_a_family_read_of_a_target_conflicts_with_a_change_of_that_family_or_of_its_text_only():
    sect = "word/document.xml", "sectPr:0"
    templating = _frag("apply_header_footer_template", [(*sect, "verify:pgMar"), (*sect, "verify:pgSz"), (*sect, "hfref")])
    numbering = _frag("fix_page_numbering", [(*sect, "pgNumType")])
    assert [o["op"] for o in merge_fragments([templating, numbering]).ops] == ["apply_header_footer_template", "fix_page_numbering"]
    for aspect in ("pgMar", "pgSz", "text"):
        with pytest.raises(JobException) as e:
            merge_fragments([templating, _frag("set_page_setup", [(*sect, aspect)])])
        assert e.value.code == "conflict"
        assert e.value.details == {"member": sect[0], "target": sect[1], "ops": ["#1 apply_header_footer_template", "#2 set_page_setup"],
                                   "aspects": [f"verify:{aspect}" if aspect != "text" else "verify:pgMar", aspect],
                                   "reads": ["#1 apply_header_footer_template"], "changes": ["#2 set_page_setup"]}
        assert "split out #2 set_page_setup into a spec of its own and run it first, then #1 apply_header_footer_template on its outputs" in e.value.reason
    # a read of every family of the target conflicts with any change of it
    with pytest.raises(JobException) as e:
        merge_fragments([_frag("x", [(*sect, "verify")]), numbering])
    assert e.value.code == "conflict" and "plans from every property of word/document.xml [sectPr:0]" in e.value.reason
    # a bare "verify:" reads every family too (never less than it says)
    with pytest.raises(JobException):
        merge_fragments([_frag("x", [(*sect, "verify:")]), numbering])
    # the same target read by two ops, and an op's own change of what it reads: no conflict
    both = _frag("fix_page_numbering", [(*sect, "verify:titlePg"), ("word/footer1.xml", "0", "text")])
    own = _frag("apply_header_footer_template", [(*sect, "verify:titlePg"), (*sect, "verify:pgMar"), (*sect, "titlePg"), (*sect, "pgMar")])
    assert merge_fragments([templating, both]).mode == "formatting"
    with pytest.raises(JobException) as e:
        merge_fragments([both, own])                 # the other op's change of titlePg meets the read
    assert e.value.code == "conflict" and e.value.details["aspects"] == ["verify:titlePg", "titlePg"]
    assert merge_fragments([own, _frag("set_page_setup", [("word/document.xml", "sectPr:1", "pgMar")])]).mode == "formatting"
    # a whole-member change of the member still conflicts with a family read inside it (rule 1, as before)
    with pytest.raises(JobException) as e:
        merge_fragments([templating, _frag("delete_paragraphs", [("word/document.xml", "*", "text")])])
    assert e.value.code == "conflict" and "would verify word/document.xml without changing it" not in e.value.reason


def test_a_soft_read_is_reached_by_a_change_of_its_family_and_never_by_a_text_change():
    """Built-in batch 4: set_properties' reads of the runs it finds already as asked are soft (``verify-props``) — a
    replace_text edit in the same paragraph plans, as before batch 4 (V2 judges the words, the kind re-resolves its runs
    over the output), while clear_direct_formatting's change of those run properties is a conflict."""
    sp = _frag("set_properties", [("word/document.xml", "3", "verify-props:rPr"),
                                  ("word/document.xml", "3", "verify-props:pStyle")])
    assert merge_fragments([sp, _frag("replace_text", [("word/document.xml", "3", "text")])]).mode == "formatting"
    for aspect in ("rPr", "pStyle"):
        with pytest.raises(JobException) as e:
            merge_fragments([_frag("clear_direct_formatting", [("word/document.xml", "3", aspect)]), sp])
        assert e.value.code == "conflict" and e.value.details["reads"] == ["#2 set_properties"]
    # a hard family read is still reached by a text change, and a bare soft read by every family but text
    with pytest.raises(JobException):
        merge_fragments([_frag("x", [("word/document.xml", "3", "verify:rPr")]),
                         _frag("replace_text", [("word/document.xml", "3", "text")])])
    bare = _frag("x", [("word/document.xml", "3", "verify-props:")])
    assert merge_fragments([bare, _frag("replace_text", [("word/document.xml", "3", "text")])]).mode == "formatting"
    with pytest.raises(JobException):
        merge_fragments([bare, _frag("set_font", [("word/document.xml", "3", "rPr")])])


def test_a_whole_member_family_read_is_reached_by_that_family_anywhere_in_the_member_only():
    """A selection by style_in/has_bold/is_all_caps reads every paragraph's pStyle: a restyle of any paragraph of the
    part conflicts, a run-property or text change does not; a whole-member change still does (rule 1)."""
    sel = _frag("set_font", [("word/document.xml", "*", "verify-props:pStyle"), ("word/document.xml", "scope", "verify")])
    with pytest.raises(JobException) as e:
        merge_fragments([sel, _frag("apply_style", [("word/document.xml", "17", "pStyle")])])
    assert e.value.code == "conflict" and "the pStyle of every target in it" in e.value.reason
    assert e.value.details["reads"] == ["#1 set_font"] and e.value.details["changes"] == ["#2 apply_style"]
    for other in ([("word/document.xml", "17", "rPr")], [("word/document.xml", "17", "text")],
                  [("word/footer1.xml", "2", "pStyle")], [("word/document.xml", "17", "verify:pStyle")]):
        assert merge_fragments([sel, _frag("other", other)]).mode == "formatting", other
    with pytest.raises(JobException):
        merge_fragments([sel, _frag("delete_paragraphs", [("word/document.xml", "*", "text")])])
    # a hard whole-member family read is reached by text changes inside the member too
    with pytest.raises(JobException):
        merge_fragments([_frag("x", [("word/document.xml", "*", "verify:pStyle")]),
                         _frag("replace_text", [("word/document.xml", "4", "text")])])


def test_incremental_uses_commute_in_one_direction_and_collide_in_opposite_ones():
    """``uses+``/``uses-`` of a style: two ops adding uses of one style commute; mapping a style away (uses-) beside an
    op that applies it (uses+) is order-dependent; a selection by the style (verify:uses) meets both; remove_unused's
    judgement (verify:uses-) only a removal; a bare read of the style's definition neither."""
    st = "word/styles.xml", "style:Heading2"
    add1, add2 = _frag("apply_style", [(*st, "uses+")]), _frag("apply_style", [(*st, "uses+")])
    away = _frag("map_styles", [(*st, "uses-")])
    away2 = _frag("apply_style", [(*st, "uses-")])
    assert merge_fragments([add1, add2]).mode == "formatting"
    assert merge_fragments([away, away2]).mode == "formatting"
    for a, b in ((add1, away), (away, add1)):
        with pytest.raises(JobException) as e:
            merge_fragments([a, b])
        assert e.value.code == "conflict" and sorted(e.value.details["aspects"]) == ["uses+", "uses-"]
    selection = _frag("set_font", [(*st, "verify:uses")])
    for change in (add1, away):
        with pytest.raises(JobException):
            merge_fragments([selection, change])
    judgement = _frag("remove_unused_styles", [("word/styles.xml", "*", "verify:uses-")])
    assert merge_fragments([judgement, add1]).mode == "formatting"
    with pytest.raises(JobException):
        merge_fragments([judgement, away])
    definition = _frag("insert_paragraph", [(*st, "verify")])
    for change in (add1, away2):
        assert merge_fragments([definition, change]).mode == "formatting"
    with pytest.raises(JobException):
        merge_fragments([definition, _frag("set_style_font", [(*st, "rPr")])])
    # a plain change of the family collides with an incremental one, and an incremental aspect is never a read
    with pytest.raises(JobException):
        merge_fragments([_frag("x", [(*st, "uses")]), add1])
    from spec_surgeon.ops.base import incremental
    assert incremental("uses+") == ("uses", "+") and incremental("verify:uses-") is None and incremental("rPr") is None


@pytest.fixture(scope="module")
def src() -> bytes:
    return build_styles()


@pytest.mark.parametrize("op, params", [("set_properties", {"run": {"b": True}}), ("apply_style", {"style": "Heading2"}),
                                        ("map_styles", {"map": [{"from": "ArchLines", "to": "ArchPara"}]}),
                                        ("set_borders_shading", {"shading": {"fill": "D9D9D9"}})])
def test_an_empty_scope_is_refused(src, op, params):
    """Review A5/C7: the transform took [] for the document, the verifiers for no part — a clean plan, a V10 FAIL."""
    with pytest.raises(JobException) as e:
        get_op(op).plan(DocxPackage(src), {"scope": []}, params)
    assert e.value.code == "bad-params" and "empty" in e.value.reason


# ---- selectors on both sides; merging many touches -------------------------------------------------------------------

MAIN = "word/document.xml"


def _spec(*ops, mode="formatting"):
    return {"request": "r", "summary": "r", "assumptions": [], "questions": [], "applies_to": {"or": "all"},
            "mode": mode, "ops": list(ops)}


def _empty_pstyle_in_section(data, section, val=""):
    from spec_surgeon.ops.set_tab_stops import paragraph_sections

    def edit(root):
        for p, s in zip(list(root.iter(W + "p")), paragraph_sections(root)):
            if s != section:
                continue
            ppr = p.find(W + "pPr")
            if ppr is None:
                ppr = etree.Element(W + "pPr")
                p.insert(0, ppr)
            if ppr.find(W + "pStyle") is None:
                ps = etree.Element(W + "pStyle")
                if val is not None:
                    ps.set(W + "val", val)
                ppr.insert(0, ps)
    return patch_docx(data, {MAIN: edit})


@pytest.mark.parametrize("val", ["", None])
def test_an_empty_pstyle_names_no_style_on_both_sides(val):
    """It names no style (batch 4's definition): the transform read it so, the verifier as the default style — a section
    chosen by style_in differed and a valid plan failed V10."""
    from make_fixtures import build_schedule
    from spec_surgeon.ops.selectors import resolve_sections
    from spec_surgeon.verify import props as vprops
    from spec_surgeon.verify.reader import STYLES_REL, VPackage
    data = _empty_pstyle_in_section(build_schedule(), 2, val)
    for sel in ({"containing": {"style_in": ["Normal"]}}, {"containing": {"has_bold": True}},
                {"containing": {"is_all_caps": True}}):
        v = VPackage(data)
        styles = vprops.Styles(v.xml(v.related(STYLES_REL)))
        assert resolve_sections(DocxPackage(data), sel, content=True) == vprops._v_resolve_sections(v, sel, styles), sel
    op = {"op": "set_page_setup", "select": {"sections": {"containing": {"style_in": ["Normal"]}}},
          "params": {"orientation": "landscape"}}
    plan, frags = plan_spec(DocxPackage(data), _spec(op))
    out = apply_spec(DocxPackage(data), frags).output_bytes()
    assert run_suite(data, out, plan.to_dict(), "a.docx", "b.docx").passed


@pytest.mark.parametrize("pred", [{"style_in": "Normal"}, {"style_in": [1]}, {"index_range": ["a", 3]},
                                  {"index_range": [0, True]}])
def test_a_malformed_paragraph_selector_is_refused(pred):
    """A bare string was read as a set of characters on one side and a substring test on the other; non-numbers
    crashed."""
    op = {"op": "set_page_setup", "select": {"sections": {"containing": pred}}, "params": {"orientation": "landscape"}}
    with pytest.raises(JobException) as e:
        plan_spec(DocxPackage(build_rich()), _spec(op))
    assert e.value.code == "bad-params"


def test_repeated_touches_merge_in_linear_time():
    """One conversion over 2,000 paragraphs declared 16,000 touches, 6,000 distinct; the merge took 16 s."""
    import time
    from spec_surgeon.ops.base import PlanFragment, check_conflicts
    a = PlanFragment(job="a", mode="formatting", params={}, intended_parts=[], op="a", op_index=0)
    b = PlanFragment(job="b", mode="formatting", params={}, intended_parts=[], op="b", op_index=1)
    a.touches = [("word/styles.xml", "style:Normal", "verify:pPr")] * 3000 + [("word/document.xml", "1", "rPr")]
    b.touches = [("word/styles.xml", "style:Normal", "verify:pPr")] * 3000 + [("word/document.xml", "2", "rPr")]
    t = time.perf_counter()
    check_conflicts([a, b])
    assert time.perf_counter() - t < 2.0
    b.touches.append(("word/styles.xml", "style:Normal", "pPr"))         # still a conflict when there is one
    with pytest.raises(JobException) as e:
        check_conflicts([a, b])
    assert e.value.code == "conflict"


AUTHOR = "Abraham Borg"


def run_op(src: bytes, op: str, select, params):
    pkg = DocxPackage(src)
    frag = get_op(op).plan(pkg, select, params)
    plan = merge_fragments([frag])
    work = pkg.copy()
    get_op(op).apply(work, frag)
    out = work.output_bytes()
    return plan, out, run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")


def _edge_case_fixture() -> bytes:
    def styles_mut(root):
        for sid, kind, el in (("BoldHead", "paragraph", "b"), ("CapsHead", "paragraph", "caps"), ("BoldChar", "character", "b")):
            st = etree.SubElement(root, W + "style"); st.set(W + "type", kind); st.set(W + "styleId", sid)
            etree.SubElement(st, W + "name").set(W + "val", sid)
            etree.SubElement(etree.SubElement(st, W + "rPr"), W + el)

    def body_mut(root):
        anchor = root.find(W + "body").findall(W + "p")[5]

        def para(text, pstyle=None, rstyle=None, direct=(), deleted_bold_run=False):
            p = etree.Element(W + "p")
            if pstyle:
                etree.SubElement(etree.SubElement(p, W + "pPr"), W + "pStyle").set(W + "val", pstyle)
            r = etree.SubElement(p, W + "r")
            if rstyle or direct:
                rpr = etree.SubElement(r, W + "rPr")
                if rstyle:
                    etree.SubElement(rpr, W + "rStyle").set(W + "val", rstyle)
                for tag, val in direct:
                    e = etree.SubElement(rpr, W + tag)
                    if val is not None:
                        e.set(W + "val", val)
            etree.SubElement(r, W + "t").text = text
            if deleted_bold_run:
                d = etree.SubElement(p, W + "del"); d.set(W + "id", "777"); d.set(W + "author", "Jack"); d.set(W + "date", "2026-01-01T00:00:00Z")
                rd = etree.SubElement(d, W + "r"); etree.SubElement(etree.SubElement(rd, W + "rPr"), W + "b")
                etree.SubElement(rd, W + "delText").text = "DELETED BOLD"
            return p
        new = [para("style bold heading", pstyle="BoldHead"), para("style caps heading", pstyle="CapsHead"),
               para("char style bold", rstyle="BoldChar"), para("style bold but direct off", pstyle="BoldHead", direct=(("b", "0"),)),
               para("plain with deleted bold run", deleted_bold_run=True), para("MIXED Case TITLE 123"), para("1.01"),
               para("ALL CAPS NO STYLE"), para("bold val 1 explicit", direct=(("b", "1"),))]
        for p in reversed(new):
            anchor.addnext(p)
    return mutate_xml(mutate_xml(build("21 05 00", "X"), "word/styles.xml", styles_mut), "word/document.xml", body_mut)


def test_new_selectors_agree_between_transform_and_verifier_and_a_shared_bug_is_caught():
    src = _edge_case_fixture()
    pkg = DocxPackage(src); root = pkg.xml("word/document.xml"); pars = paragraphs(root); sheet = StyleSheet(pkg)
    vp = VPackage(src); view = PartView(vp.xml("word/document.xml")); vstyles = Styles(vp.xml(vp.related(STYLES_REL)))
    preds = [{"has_bold": True}, {"is_all_caps": True}, {"after_heading_regex": r"^GENERAL$"},
             {"between_paragraphs": {"start_regex": r"^GENERAL$", "end_regex": r"^2\.01"}},
             {"between_paragraphs": {"start_regex": r"^GENERAL$", "end_regex": r"^2\.01", "include_bounds": True}},
             {"between_paragraphs": {"start_regex": r"^GENERAL$"}}, {"text_regex": r"^A\.\tThe Owner"},
             {"has_bold": True, "is_all_caps": True}]
    texts = [block_full_text(p) for p in pars]
    for pred in preds:
        t_sel = [i for i, p in enumerate(pars) if paragraph_selected(pred, p, i, texts[i], sheet, all_pars=pars)]
        v_sel = [i for i, p in enumerate(view.pars) if para_matches(pred, p, i, view, vstyles)]
        assert t_sel == v_sel, (pred, t_sel, v_sel)
    bold = [texts[i] for i, p in enumerate(pars) if paragraph_selected({"has_bold": True}, p, i, texts[i], sheet, all_pars=pars)]
    assert bold == ["style bold heading", "char style bold", "bold val 1 explicit"]
    caps = [texts[i] for i, p in enumerate(pars) if paragraph_selected({"is_all_caps": True}, p, i, texts[i], sheet, all_pars=pars)]
    assert "style caps heading" in caps and "MIXED Case TITLE 123" not in caps and "1.01" not in caps

    good = selectors_module._paragraph_has_bold
    selectors_module._paragraph_has_bold = lambda p, sheet: any(r.find(W + "rPr/" + W + "b") is not None
                                                                for r in selectors_module._live_runs(p))
    try:
        _, _, bad_rec = run_op(src, "set_font", {"paragraphs": {"has_bold": True}}, {"font": "Courier New", "author": AUTHOR})
    finally:
        selectors_module._paragraph_has_bold = good
    assert bad_rec.get("V10").status == "FAIL"
    _, _, rec = run_op(src, "set_font", {"paragraphs": {"has_bold": True}}, {"font": "Courier New", "author": AUTHOR})
    assert rec.passed, rec.to_markdown()
