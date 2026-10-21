"""Built-in batch 6, F3 — text and character formatting (docs/BUILTIN_OPS_BATCH6_DESIGN.md §3, §4): set_properties'
new run properties (superscript/subscript, hidden, highlight, character spacing, double strikethrough), insert_paragraph's
tabs, line breaks, segments and several paragraphs, and replace_text's tabs and line breaks through both sides of the
edit core. Each end to end (plan → merge → apply → run_suite) tracked and untracked; the "already as asked" no-ops; a
wrong OUTPUT per rule and wrong IMPLEMENTATIONS shared by plan and apply, each caught by the verifier; the two resolvers
compared; the schema order pinned to the bundled schema; parameters; the catalog, the placed words, the batch pipeline
and a LibreOffice render."""
from __future__ import annotations

import copy
import io
import shutil
import zipfile
from typing import List, Tuple

import pytest
from lxml import etree

from fx_text import LITERAL_TAB_TEXT, build_notes, build_text
from make_fixtures import build_formatted, build_rich
from spec_surgeon import compiler, ooxml
from spec_surgeon.catalog import COMMON_ASSUMPTIONS, op_catalog
from spec_surgeon.editspec import SpecError, plan_spec, validate_spec
from spec_surgeon.ooxml import RevisionStamp, paragraphs
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import effective as effective_module
from spec_surgeon.ops import insert_paragraph as insert_module
from spec_surgeon.ops import set_properties as set_properties_module
from spec_surgeon.ops import textedit
from spec_surgeon.ops.base import PlanFragment
from spec_surgeon.ops.effective import RUN_TOGGLE_ELEMENTS, PropResolver
from spec_surgeon.ops.selectors import StyleSheet
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.verify import props as vprops
from spec_surgeon.verify import run_suite
from spec_surgeon.verify import tableprops as vtp
from spec_surgeon.verify.edits import check_edited_paragraph, v_check_edits, v_items
from spec_surgeon.verify.reader import STYLES_REL, VPackage

AUTHOR = "Abraham Borg"
MAIN = "word/document.xml"
STYLES = "word/styles.xml"


@pytest.fixture(scope="module")
def src() -> bytes:
    return build_text()


@pytest.fixture(scope="module")
def notes() -> bytes:
    return build_notes()


RED_NOTES = {"runs": {"color_in": ["FF0000"]}}


def run_ops(data: bytes, specs: List[Tuple[str, dict, dict]]):
    pkg = DocxPackage(data)
    frags = [get_op(o).plan(pkg, sel, par) for o, sel, par in specs]
    plan = merge_fragments(frags)
    work = pkg.copy()
    for f in frags:
        get_op(f.op).apply(work, f)
    out = work.output_bytes()
    return plan, out, run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")


def props(data: bytes, select: dict, run: dict, **more):
    return run_ops(data, [("set_properties", select, {"run": run, "author": AUTHOR, **more})])


def failed(rec) -> List[str]:
    return [c.id for c in rec.checks if c.status == "FAIL"]


def v10(rec) -> str:
    c = rec.get("V10")
    return c.summary + " | " + " | ".join(c.data.get("errors", []))


def mutate(docx: bytes, part: str, fn) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(docx))
    root = etree.fromstring(zin.read(part))
    fn(root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            z.writestr(info, serialize_xml(root) if info.filename == part else zin.read(info.filename))
    return buf.getvalue()


def caught(src_: bytes, out: bytes, plan, part: str, defect) -> List[str]:
    """The checks that fail when ``defect`` is planted in the output's ``part``."""
    return failed(run_suite(src_, mutate(out, part, defect), plan.to_dict(), "s", "o"))


def root_of(data: bytes, part: str = MAIN):
    return DocxPackage(data).xml(part)


def shown(el) -> str:
    """A run's or paragraph's text as the tests name it: w:t / w:delText characters, a w:tab as \\t, a w:br as \\n."""
    out = []
    for c in el.iter(W + "t", W + "delText", W + "tab", W + "br"):
        if c.tag == W + "tab" and c.getparent().tag != W + "r":
            continue                                       # a tab-stop definition
        out.append({W + "tab": "\t", W + "br": "\n"}.get(c.tag, c.text or ""))
    return "".join(out)


def run_with(root, text: str):
    """The run whose text is ``text`` — or, ending in "…", starts with it."""
    if text.endswith("…"):
        return next(r for r in root.iter(W + "r") if shown(r).startswith(text[:-1]))
    return next(r for r in root.iter(W + "r") if shown(r) == text)


def para_with(root, starts: str):
    return next(p for p in root.iter(W + "p") if shown(p).startswith(starts))


def run_in(root, starts: str, text: str):
    """The run whose text is ``text`` in the paragraph starting with ``starts``."""
    return run_with(para_with(root, starts), text)


def local(el) -> str:
    return etree.QName(el).localname


def kids(el) -> List[str]:
    return [local(c) for c in el] if el is not None else []


def val(run, element: str):
    e = run.find(f"{W}rPr/{W}{element}")
    return None if e is None else dict((local(k), v) for k, v in e.attrib.items())


def para_sel(regex: str) -> dict:
    return {"paragraphs": {"text_regex": regex}}


def counts(plan) -> Tuple[int, int]:
    a = plan.property_assertions[0]
    return a["expected_runs"], a["expected_marks"]


def mark_hidden(p) -> bool:
    v = p.find(f"{W}pPr/{W}rPr/{W}vanish")
    return v is not None and v.get(W + "val") not in ("0", "false", "off")


def empty_paragraph(root):
    return next(p for p in root.iter(W + "p") if not shown(p))


# ==== §3 set_properties: the new run properties ===========================================================================

def test_superscript_where_it_is_not_already_and_back_to_the_baseline(src):
    """Superscript on the "A." paragraph (targeted, so tracked): every run but the "2" (direct) and the "3" (its
    SpecSuper character style) gets w:vertAlign superscript with a snapshot; the whole document back on the baseline
    (plain) touches only those two — the style's 3 by a direct value that wins over it."""
    plan, out, rec = props(src, para_sel(r"^A\."), {"vertAlign": "superscript"})
    assert rec.passed, rec.to_markdown()
    root = root_of(out)
    assert val(run_with(root, "struck"), "vertAlign") == {"val": "superscript"}
    assert kids(run_with(root, "struck").find(W + "rPr"))[-1] == "rPrChange"
    assert val(run_with(root, "2"), "vertAlign") == {"val": "superscript"} and kids(run_with(root, "2").find(W + "rPr")) \
        == ["vertAlign"]                                                       # already as asked: no snapshot
    assert val(run_with(root, "3"), "vertAlign") is None                    # superscript by its style already
    assert counts(plan) == (6, 1) and plan.rev_delta["rPrChange"] == 7 and plan.geometry_reference == "none"
    assert any("already as asked" in n for n in plan.notes)
    plan, out, rec = props(src, {"runs": "all"}, {"vertAlign": "baseline"})
    assert rec.passed, rec.to_markdown()
    root = root_of(out)
    assert val(run_with(root, "2"), "vertAlign") == {"val": "baseline"} and val(run_with(root, "3"), "vertAlign") == \
        {"val": "baseline"}
    assert counts(plan) == (2, 0) and plan.ops[0]["params"]["tracked"] is False
    plan, out, rec = props(src, para_sel(r"^A\."), {"vertAlign": "subscript"}, tracked=False)
    assert rec.passed and val(run_with(root_of(out), "3"), "vertAlign") == {"val": "subscript"}


def test_hidden_text_on_and_off_and_the_toggle_rule(src):
    """Hidden is a display property — zero text diffs; it is a toggle: "twice hidden" (SpecHidden paragraph +
    SpecHiddenChar run) is ambiguous, so it is set directly; the style target refuses it (Word XORs the two)."""
    plan, out, rec = props(src, para_sel(r"^C\."), {"vanish": True})
    assert rec.passed, rec.to_markdown()
    assert counts(plan) == (2, 1) and not plan.diffs                         # "secret" already hidden
    assert val(run_with(root_of(out), " visible."), "vanish") == {}
    plan, out, rec = props(src, {"runs": "all"}, {"vanish": False})
    assert rec.passed, rec.to_markdown()
    root = root_of(out)
    for text in ("secret", "E.…", "twice hidden"):
        assert val(run_with(root, text), "vanish") == {"val": "0"}, text
    assert para_with(root, "E.").find(f"{W}pPr/{W}rPr/{W}vanish").get(W + "val") == "0"   # the hidden mark too
    assert counts(plan) == (3, 1)
    plan, out, rec = props(src, para_sel(r"^E\."), {"vanish": True})
    assert rec.passed and val(run_with(root_of(out), "twice hidden"), "vanish") == {}   # ambiguous: set directly
    assert counts(plan) == (1, 0)
    with pytest.raises(JobException) as e:
        get_op("set_properties").plan(DocxPackage(src), {"styles": {"ids": ["SpecHidden"]}}, {"run": {"vanish": False}})
    assert e.value.code == "toggle-ambiguous"


@pytest.mark.parametrize("tracked", [True, False])
def test_a_paragraph_mark_is_hidden_only_with_all_of_its_text(notes, tracked):
    """Hiding the red notes: Word joins a paragraph whose mark is hidden to the next one, so a red mark is hidden only
    where every content run of its paragraph is hidden after the change — the note paragraph, the one whose other
    sentence was hidden already, the empty one; "A." and "B." keep visible text and keep their mark (flagged); "D."'s
    mark was hidden already and is left as it is."""
    plan, out, rec = props(notes, RED_NOTES, {"vanish": True}, tracked=tracked)
    assert rec.passed, rec.to_markdown()
    root = root_of(out)
    for text in ("[Note: verify…", "[Note: delete…", "[Note: retain…", "[Note: two sets.]"):
        assert val(run_with(root, text), "vanish") == {}, text
    assert val(run_with(root, "A.\tProvide shop drawings. "), "vanish") is None
    assert not mark_hidden(para_with(root, "A.")) and not mark_hidden(para_with(root, "B."))
    assert mark_hidden(para_with(root, "[Note: delete")) and mark_hidden(para_with(root, "Superseded"))
    assert mark_hidden(empty_paragraph(root)) and mark_hidden(para_with(root, "D."))
    assert kids(para_with(root, "A.").find(f"{W}pPr/{W}rPr")) == ["color"]          # left exactly as it was
    assert counts(plan) == (4, 3) and plan.property_assertions[0]["expected_shown_marks"] == 2
    assert plan.rev_delta.get("rPrChange", 0) == (7 if tracked else 0)
    flag = next(f for f in plan.flags if "paragraph mark(s) left visible" in f)
    assert flag.startswith("2 ") and "‘A. Provide shop drawings." in flag and "‘B. Provide product data.’" in flag
    assert any("already as asked, left alone: 1 marks" in n for n in plan.notes)
    assert "mark(s) left visible beside visible text" in rec.get("V10").summary
    # the whole of "A." selected: its mark goes hidden with its text, nothing is flagged
    plan, out, rec = props(notes, para_sel(r"^A\."), {"vanish": True}, tracked=tracked)
    assert rec.passed and counts(plan) == (2, 1) and mark_hidden(para_with(root_of(out), "A."))
    assert not any("left visible" in f for f in plan.flags)
    # showing the notes again touches the marks as before: off where hidden
    plan, out, rec = props(notes, RED_NOTES, {"vanish": False}, tracked=tracked)
    assert rec.passed and counts(plan) == (0, 1) and not mark_hidden(para_with(root_of(out), "D."))


def test_highlight_a_colour_and_none(src):
    plan, out, rec = props(src, para_sel(r"^B\."), {"highlight": "yellow"})
    assert rec.passed, rec.to_markdown()
    root = root_of(out)
    assert val(run_with(root, "green note"), "highlight") == {"val": "yellow"}
    assert kids(run_with(root, "yellow note").find(W + "rPr")) == ["highlight"]   # already yellow
    assert counts(plan) == (3, 1) and plan.geometry_reference == "source"
    plan, out, rec = props(src, {"runs": "all"}, {"highlight": "none"})
    assert rec.passed, rec.to_markdown()
    root = root_of(out)
    assert val(run_with(root, "yellow note"), "highlight") == val(run_with(root, "green note"), "highlight") == \
        {"val": "none"}
    assert val(run_with(root, "PART 3 - EXECUTION"), "highlight") == {"val": "none"}   # over SpecPartLow's grey
    assert counts(plan) == (3, 1)


def test_highlight_is_refused_in_a_style_and_set_on_its_text(src):
    """Word does not show highlighting set in a style (its style dialogs offer none; LibreOffice writes a style's
    background as shading): asked of a paragraph or character style it is a bad parameter, with the way to do it —
    highlighting the style's text directly works and is verified."""
    for ids in (["SpecPart"], ["SpecSuper"], ["SpecPart", "SpecSpaced"]):
        for run in ({"highlight": "yellow"}, {"highlight": "none"}, {"highlight": "cyan", "b": True}):
            with pytest.raises(JobException) as e:
                get_op("set_properties").plan(DocxPackage(src), {"styles": {"ids": ids}}, {"run": run})
            assert e.value.code == "bad-params" and "highlight the text directly" in e.value.reason, (ids, run)
    plan, out, rec = props(src, {"paragraphs": {"style_in": ["SpecPart"]}}, {"highlight": "yellow"})
    assert rec.passed, rec.to_markdown()
    assert val(run_with(root_of(out), "PART 2 - PRODUCTS"), "highlight") == {"val": "yellow"}
    assert plan.intended_parts == [MAIN] and counts(plan) == (2, 2)


def test_character_spacing_in_points(src):
    """2 pt is 40 twentieths: "wide" is already 40; "measured" says "2pt" (a measure with a unit, never "already") and
    is written 40; 0 on the SpecSpaced paragraph writes 0 over the style's 20; −0.5 pt condenses by 10."""
    plan, out, rec = props(src, para_sel(r"^D\."), {"spacing": 2})
    assert rec.passed, rec.to_markdown()
    root = root_of(out)
    assert val(run_with(root, "measured"), "spacing") == {"val": "40"}
    assert kids(run_with(root, "wide").find(W + "rPr")) == ["spacing"]
    assert counts(plan) == (3, 1) and plan.geometry_reference == "none"
    plan, out, rec = props(src, para_sel(r"^G\."), {"spacing": 0})
    assert rec.passed and val(run_with(root_of(out), "G.\tSpaced by its style"), "spacing") == {"val": "0"}
    plan, out, rec = props(src, para_sel(r"^C\."), {"spacing": -0.5}, tracked=False)
    assert rec.passed and val(run_with(root_of(out), "secret"), "spacing") == {"val": "-10"}


def test_double_strikethrough_turns_single_strikethrough_off_and_the_reverse(src):
    """Word draws one strikethrough or the other: double on writes single off where single shows ("struck"), and the
    reverse; "double struck" and the SpecDouble run are already double; both on is a bad parameter."""
    plan, out, rec = props(src, para_sel(r"^A\."), {"dstrike": True})
    assert rec.passed, rec.to_markdown()
    root = root_of(out)
    assert val(run_with(root, "struck"), "strike") == {"val": "0"} and val(run_with(root, "struck"), "dstrike") == {}
    assert val(run_with(root, " and "), "strike") is None                    # single never showed there
    assert kids(run_with(root, "double struck").find(W + "rPr")) == ["dstrike"]   # already as asked
    assert plan.geometry_reference == "source"
    plan, out, rec = props(src, para_sel(r"^[AF]\."), {"strike": True})
    assert rec.passed, rec.to_markdown()
    root = root_of(out)
    assert val(run_with(root, "double struck"), "dstrike") == {"val": "0"}
    assert val(run_with(root, "double style"), "dstrike") == {"val": "0"}   # over its character style
    assert val(run_with(root, "double style"), "strike") == {}
    plan, out, rec = props(src, para_sel(r"^F\."), {"dstrike": True})
    assert rec.passed and counts(plan) == (1, 1)                             # the SpecDouble run is double already
    plan, out, rec = props(src, para_sel(r"^A\."), {"dstrike": True, "strike": False})
    assert rec.passed, rec.to_markdown()
    with pytest.raises(JobException) as e:
        get_op("set_properties").plan(DocxPackage(src), para_sel("^A"), {"run": {"dstrike": True, "strike": True}})
    assert e.value.code == "bad-params"


def test_symbol_runs_take_the_new_properties(src):
    """Caps never touch a symbol run; hidden, highlight, superscript, spacing and strikethrough do (the glyph stays)."""
    plan, out, rec = props(src, para_sel(r"^H\."), {"vanish": True, "highlight": "cyan", "vertAlign": "subscript",
                                                   "spacing": 1, "dstrike": True})
    assert rec.passed, rec.to_markdown()
    root = root_of(out)
    bullet = run_with(root, "l")
    sym = next(r for r in root.iter(W + "r") if r.find(W + "sym") is not None)
    for r in (bullet, sym):
        assert kids(r.find(W + "rPr"))[-6:] == ["dstrike", "vanish", "spacing", "highlight", "vertAlign", "rPrChange"]


def test_style_definitions_take_the_new_properties(src):
    """On style definitions (styles.xml only, never tracked): SpecPart gets them all but highlight (refused in a style:
    see below) — double strikethrough with single off, as Word writes it in a style; SpecPartLow (based on it) keeps its
    own baseline (noted); SpecSpaced's own 20 becomes 0. A style whose text carries a disagreeing direct value
    refuses."""
    plan, out, rec = props(src, {"styles": {"ids": ["SpecPart"]}}, {"vertAlign": "subscript", "spacing": 0.5,
                                                                   "dstrike": True, "vanish": False})
    assert rec.passed, rec.to_markdown()
    assert plan.intended_parts == [STYLES] and not {k: v for k, v in plan.rev_delta.items() if v}
    st = next(s for s in root_of(out, STYLES).iter(W + "style") if s.get(W + "styleId") == "SpecPart")
    assert kids(st.find(W + "rPr")) == ["strike", "dstrike", "vanish", "spacing", "vertAlign"]
    assert st.find(f"{W}rPr/{W}spacing").get(W + "val") == "10" and st.find(f"{W}rPr/{W}strike").get(W + "val") == "0"
    assert any("SpecPartLow keeps its own vertAlign over SpecPart" in n for n in plan.notes)
    plan, out, rec = props(src, {"styles": {"ids": ["SpecSpaced"]}}, {"spacing": 0})
    assert rec.passed and plan.property_assertions[0]["expected_ids"] == ["SpecSpaced"]
    with pytest.raises(JobException) as e:           # the "2" says superscript directly; Word keeps it
        get_op("set_properties").plan(DocxPackage(src), {"styles": {"ids": ["Normal"]}}, {"run": {"vertAlign": "subscript"}})
    assert e.value.code == "conflict-property" and "vertAlign" in e.value.reason


def test_already_as_asked_is_a_no_op(src):
    for select, run in ((para_sel(r"^A\."), {"vanish": False}), (para_sel(r"^B\."), {"spacing": 0}),
                        (para_sel(r"^C\."), {"vertAlign": "baseline", "highlight": "none", "dstrike": False})):
        plan, out, rec = props(src, select, run)
        assert rec.passed, (run, rec.to_markdown())
        assert counts(plan) == (0, 0) and plan.intended_parts == [] and not plan.rev_delta.get("rPrChange"), run
        assert any("already as asked" in n for n in plan.notes)


def test_a_tracked_change_over_an_older_snapshot_refuses_and_plain_flags(src):
    with pytest.raises(JobException) as e:
        get_op("set_properties").plan(DocxPackage(src), para_sel(r"^I\."), {"run": {"highlight": "yellow"}})
    assert e.value.code == "tracked-format"
    plan, out, rec = props(src, para_sel(r"^I\."), {"highlight": "yellow"}, tracked=False)
    assert rec.passed and any("already carried a pending tracked formatting change" in f for f in plan.flags)


def test_the_new_elements_are_written_in_schema_order_and_the_order_table_is_the_bundled_schema(src):
    """The transform's hand-written rPr order (ooxml.RPR_ORDER: CT_ParaRPr = the mark's revision marks, then
    EG_RPrBase) is the verifier's reading of the bundled wml.xsd; the complex-script run gets every new element where
    that order puts it, around its own w:cs."""
    assert tuple(ooxml.RPR_ORDER) == vtp.v_order("CT_ParaRPr")
    assert tuple(ooxml.RPR_ORDER[4:]) == vtp.v_order("CT_RPr")
    plan, out, rec = props(src, para_sel(r"^J\."), {"vertAlign": "superscript", "highlight": "yellow", "spacing": 1,
                                                   "dstrike": True, "vanish": True, "b": True}, tracked=False)
    assert rec.passed, rec.to_markdown()
    names = kids(run_with(root_of(out), "Complex script").find(W + "rPr"))
    assert names == ["b", "bCs", "dstrike", "vanish", "spacing", "highlight", "vertAlign", "cs"]
    assert names == sorted(names, key=list(ooxml.RPR_ORDER).index)


def test_the_two_resolvers_agree_on_the_new_properties():
    checked = 0
    for data in (build_text(), build_formatted(), build_rich()):
        pkg, vpkg = DocxPackage(data), VPackage(data)
        res = PropResolver(StyleSheet(pkg))
        vstyles = vprops.Styles(vpkg.xml(vpkg.related(STYLES_REL)))
        for part in pkg.text_parts():
            for p, vp in zip(paragraphs(pkg.xml(part)), vpkg.xml(part).iter(W + "p")):
                pairs = [(p.find(f"{W}pPr/{W}rPr"), vp.find(f"{W}pPr/{W}rPr"))]
                pairs += list(zip([r.find(W + "rPr") for r in p.iter(W + "r")],
                                  [r.find(W + "rPr") for r in vp.iter(W + "r")]))
                for rpr, vrpr in pairs:
                    for e in RUN_TOGGLE_ELEMENTS + ("u", "color", "vertAlign", "highlight", "spacing"):
                        assert res.run_prop(rpr, p, e) == vprops._v_run_prop(vrpr, vp, vstyles, e), (part, e)
                    checked += 1
    assert checked > 200
    pkg = DocxPackage(build_text())
    root = pkg.xml(MAIN)
    res = PropResolver(StyleSheet(pkg))
    assert res.run_prop(run_with(root, "twice hidden").find(W + "rPr"), para_with(root, "E."), "vanish") == \
        (None, "ambiguous")
    assert res.run_prop(run_with(root, "measured").find(W + "rPr"), para_with(root, "D."), "spacing") == ("2pt", "direct")
    assert res.run_prop(run_with(root, "3").find(W + "rPr"), para_with(root, "A."), "vertAlign") == \
        ("superscript", "style:SpecSuper")
    assert res.run_prop(None, para_with(root, "G."), "spacing") == (20, "style:SpecSpaced")
    assert res.run_prop(None, para_with(root, "SECTION"), "highlight") == ("none", "default")


# ---- planted OUTPUT defects: V10 alone catches each -----------------------------------------------------------------

def test_wrong_outputs_of_the_new_properties_are_caught(src):
    plan, out, rec = props(src, para_sel(r"^A\."), {"vertAlign": "superscript", "dstrike": True})
    assert rec.passed

    def word_super(root):                       # not one of ST_VerticalAlignRun's values
        run_with(root, "struck").find(f"{W}rPr/{W}vertAlign").set(W + "val", "super")

    def single_left_on(root):                   # both strikethroughs in effect
        run_with(root, "struck").find(f"{W}rPr/{W}strike").attrib.clear()

    def noop_snapshot(root):                    # "Formatted: Superscript" on text that already was
        r = run_with(root, "2")
        chg = etree.SubElement(r.find(W + "rPr"), W + "rPrChange")
        chg.set(W + "id", "990"); chg.set(W + "author", AUTHOR); chg.set(W + "date", "2026-10-21T00:00:00Z")
        etree.SubElement(chg, W + "rPr")

    def neighbour_hidden(root):                 # a run outside the selection
        rpr = etree.Element(W + "rPr"); etree.SubElement(rpr, W + "vanish")
        run_with(root, "B.\tHighlighted ").insert(0, rpr)          # (paragraph B: outside the selection)

    for defect in (word_super, single_left_on, noop_snapshot, neighbour_hidden):
        assert "V10" in caught(src, out, plan, MAIN, defect), defect.__name__

    plan, out, rec = props(src, para_sel(r"^D\."), {"spacing": 2, "highlight": "yellow"})
    assert rec.passed

    def spacing_in_points(root):                # w:spacing is twentieths of a point
        run_with(root, "measured").find(f"{W}rPr/{W}spacing").set(W + "val", "2")

    def highlight_colour_changed(root):
        run_with(root, "wide").find(f"{W}rPr/{W}highlight").set(W + "val", "green")

    def extra_attribute(root):                  # an attribute the element does not take
        run_in(root, "D.", " and ").find(f"{W}rPr/{W}highlight").set(W + "themeColor", "accent1")

    for defect in (spacing_in_points, highlight_colour_changed, extra_attribute):
        assert "V10" in caught(src, out, plan, MAIN, defect), defect.__name__

    plan, out, rec = props(src, {"styles": {"ids": ["SpecPart"]}}, {"vanish": True})
    assert rec.passed

    def vanish_off_in_style(root):
        st = next(s for s in root.iter(W + "style") if s.get(W + "styleId") == "SpecPart")
        st.find(f"{W}rPr/{W}vanish").set(W + "val", "0")
    assert "V10" in caught(src, out, plan, STYLES, vanish_off_in_style)


@pytest.mark.parametrize("tracked", [True, False])
def test_a_mark_hidden_beside_visible_text_and_a_mark_left_shown_are_caught(notes, tracked):
    plan, out, rec = props(notes, RED_NOTES, {"vanish": True}, tracked=tracked)
    assert rec.passed

    def hidden_anyway(root):                    # Word would join "A." to the note paragraph after it
        para_with(root, "A.").find(f"{W}pPr/{W}rPr").insert(0, etree.Element(W + "vanish"))

    def b_hidden_too(root):                     # a mark selected by its colour alone
        para_with(root, "B.").find(f"{W}pPr/{W}rPr").insert(0, etree.Element(W + "vanish"))

    for defect in (hidden_anyway, b_hidden_too):
        bad = run_suite(notes, mutate(out, MAIN, defect), plan.to_dict(), "s", "o")
        assert "V10" in failed(bad) and "join the paragraph to the next one" in v10(bad), defect.__name__

    def left_shown(root):                       # the note paragraph's text is all hidden: its mark goes too
        mrpr = para_with(root, "[Note: delete").find(f"{W}pPr/{W}rPr")
        mrpr.remove(mrpr.find(W + "vanish"))
    assert "V10" in caught(notes, out, plan, MAIN, left_shown)


def test_the_kind_refuses_bad_values_of_the_new_properties(src):
    base = {"job": "x", "mode": "formatting", "params": {}, "intended_parts": [], "diffs": [], "rev_delta": {},
            "members_removed": [], "members_added": []}
    for run in ({"vertAlign": "super"}, {"highlight": "orange"}, {"spacing": 0.01}, {"spacing": "1"},
                {"strike": True, "dstrike": True}):
        a = {"kind": "properties", "target": "direct", "scope": ["document"], "parts": [MAIN],
             "set": {"run": run, "paragraph": {}}, "expected_runs": 0, "expected_marks": 0, "expected_paragraphs": 0}
        rec = run_suite(src, src, {**base, "property_assertions": [a]}, "s", "o")
        assert failed(rec) == ["V10"], run
    # highlighting in a style definition, which Word does not show: the op refuses it, the kind too
    a = {"kind": "properties", "target": "styles", "styles_part": STYLES, "select": {"ids": ["SpecPart"]},
         "set": {"run": {"highlight": "yellow"}, "paragraph": {}}, "expected_ids": [], "tracked": False}
    rec = run_suite(src, src, {**base, "property_assertions": [a]}, "s", "o")
    assert failed(rec) == ["V10"] and "highlighting set in a style" in v10(rec)


# ---- planted IMPLEMENTATION defects shared by plan and apply --------------------------------------------------------

def test_a_shared_partner_rule_that_forgets_single_strikethrough_is_caught(src, monkeypatch):
    """Plan and apply both leave "struck"'s single strikethrough on under the double one; V10 expands the pair itself."""
    monkeypatch.setattr(effective_module, "STRIKE_PARTNER", {})
    plan, out, rec = props(src, para_sel(r"^A\."), {"dstrike": True})
    assert "V10" in failed(rec) and "strike" in v10(rec)


def test_a_shared_unit_that_writes_points_as_tenths_is_caught(src, monkeypatch):
    monkeypatch.setattr(effective_module, "twentieths", lambda pt: int(round(float(pt) * 10)))
    plan, out, rec = props(src, para_sel(r"^D\."), {"spacing": 2})
    assert "V10" in failed(rec)


def test_a_shared_resolver_that_reads_hidden_as_nearest_wins_is_caught(src, monkeypatch):
    """Without the toggle rule "twice hidden" looks hidden already (its character style is nearest) and is left alone
    by plan and apply; V10's own toggle rule finds it ambiguous — it had to be set."""
    monkeypatch.setattr(effective_module, "RUN_TOGGLE_ELEMENTS",
                        tuple(e for e in effective_module.RUN_TOGGLE_ELEMENTS if e != "vanish"))
    plan, out, rec = props(src, para_sel(r"^E\."), {"vanish": True})
    assert counts(plan) == (0, 0)
    assert "V10" in failed(rec) and "predicted" in v10(rec)


@pytest.mark.parametrize("helper, wrong", [
    ("stays_visible", lambda resolver, p, left_out: False),     # every selected mark hidden, whatever its text shows
    ("shows_content", lambda run: False),                       # no run counted as showing anything
])
def test_a_shared_rule_that_hides_a_mark_beside_visible_text_is_caught(notes, monkeypatch, helper, wrong):
    """Plan and apply both hide "A."'s and "B."'s marks while their text stays visible; V10 reads each paragraph's runs
    itself and finds the paragraph joined to the next one."""
    monkeypatch.setattr(set_properties_module, helper, wrong)
    plan, out, rec = props(notes, RED_NOTES, {"vanish": True})
    assert counts(plan) == (4, 5) and not any("left visible" in f for f in plan.flags)
    assert "V10" in failed(rec) and "join the paragraph to the next one" in v10(rec)


def test_a_shared_rule_that_never_hides_a_mark_is_caught(notes, monkeypatch):
    """Plan and apply both keep every mark visible, the note paragraph's too, whose text is all hidden; V10 expects
    its mark hidden."""
    monkeypatch.setattr(set_properties_module, "stays_visible", lambda resolver, p, left_out: True)
    plan, out, rec = props(notes, RED_NOTES, {"vanish": True})
    assert counts(plan) == (4, 0)
    assert "V10" in failed(rec) and "predicted" in v10(rec)


def test_a_shared_default_that_takes_everything_for_superscript_is_caught(src, monkeypatch):
    monkeypatch.setattr(effective_module, "VALUE_DEFAULTS", {**effective_module.VALUE_DEFAULTS,
                                                              "vertAlign": "superscript"})
    plan, out, rec = props(src, para_sel(r"^B\."), {"vertAlign": "superscript"})
    assert counts(plan) == (0, 0) and "V10" in failed(rec)


def test_a_shared_reading_that_takes_a_measure_for_its_twentieths_is_caught(src, monkeypatch):
    real = effective_module.prop_read

    def lenient(element, el):
        v = real(element, el)
        return 40 if element == "spacing" and v == "2pt" else v
    monkeypatch.setattr(effective_module, "prop_read", lenient)
    plan, out, rec = props(src, para_sel(r"^D\."), {"spacing": 2})
    assert "V10" in failed(rec)


def test_a_writer_that_spells_the_colour_as_the_menu_does_is_caught(src, monkeypatch):
    """ST_HighlightColor is case-sensitive: "Yellow" is no highlight Word knows."""
    good = set_properties_module.write_element

    def menu_spelling(container, element, value, order):
        good(container, element, value.capitalize() if element == "highlight" else value, order)
    monkeypatch.setattr(set_properties_module, "write_element", menu_spelling)
    plan, out, rec = props(src, para_sel(r"^B\."), {"highlight": "yellow"})
    assert "V10" in failed(rec) and "highlight" in v10(rec)


# ---- parameters and the spec schema ----------------------------------------------------------------------------------

@pytest.mark.parametrize("run", [
    {"vertAlign": "super"}, {"vertAlign": True}, {"vanish": "yes"}, {"dstrike": 1}, {"highlight": "orange"},
    {"highlight": "Yellow"}, {"spacing": "1"}, {"spacing": True}, {"spacing": 0.01}, {"spacing": 2000},
    {"strike": True, "dstrike": True}, {"position": 2},
])
def test_bad_run_properties_are_refused(src, run):
    with pytest.raises(JobException) as e:
        get_op("set_properties").plan(DocxPackage(src), para_sel("^A"), {"run": run})
    assert e.value.code == "bad-params"


def test_the_spec_schema_knows_the_new_properties(src):
    def spec(run):
        return {"request": "r", "summary": "s", "mode": "formatting",
                "ops": [{"op": "set_properties", "select": para_sel("^A"), "params": {"run": run}}]}
    validate_spec(spec({"vertAlign": "superscript", "vanish": True, "highlight": "darkBlue", "spacing": -0.75,
                        "dstrike": False}))
    for bad in ({"vertAlign": "super"}, {"highlight": "orange"}, {"spacing": "1pt"}, {"hidden": True}):
        with pytest.raises(SpecError):
            validate_spec(spec(bad))
    plan, _ = plan_spec(DocxPackage(src), spec({"spacing": 1.5}))
    assert plan.property_assertions[0]["set"]["run"] == {"spacing": 1.5}


# ==== §4.1–4.2 insert_paragraph: tabs, line breaks, segments, several paragraphs ===========================================

SUMMARY = para_sel(r"^1\.01\tSUMMARY")
ANCHOR_K = para_sel(r"^K\.")


def insert(data: bytes, select: dict, **params):
    return run_ops(data, [("insert_paragraph", select, {"author": AUTHOR, **params})])


def content(p) -> List[Tuple[str, str]]:
    """The paragraph's run children in order (inside any w:ins): ("t", text), ("tab", ""), ("br", "")."""
    return [(local(c), c.text or "") for r in p.iter(W + "r") for c in r if local(c) != "rPr"]


def after_anchor(root, anchor_starts: str, k: int = 1):
    """The paragraph ``k`` places after (negative: before) the one starting with ``anchor_starts``."""
    pars = list(root.iter(W + "p"))
    i = next(j for j, p in enumerate(pars) if shown(p).startswith(anchor_starts))
    return pars[i + k]


@pytest.mark.parametrize("tracked", [True, False])
def test_a_tab_and_a_line_break_are_written_as_words_own(src, tracked):
    plan, out, rec = insert(src, SUMMARY, text="A.\tProvide calculations.\nSee drawings.", tracked=tracked)
    assert rec.passed, rec.to_markdown()
    new = after_anchor(root_of(out), "1.01\tSUMMARY")
    assert content(new) == [("t", "A."), ("tab", ""), ("t", "Provide calculations."), ("br", ""),
                            ("t", "See drawings.")]
    assert new.find(f".//{W}br").attrib == {}                                 # a line break, not a page break
    assert plan.diffs[0].after == "A.Provide calculations.See drawings."     # tabs and breaks are no block text
    assert (new.find(W + "ins") is not None) is tracked
    assert plan.rev_delta.get("ins", 0) == (1 if tracked else 0)


@pytest.mark.parametrize("tracked", [True, False])
def test_segments_are_direct_formatting_over_the_inherited_run(src, tracked):
    """The anchor K's first run says Courier New, bold, 9 pt, a red single underline: every segment starts from it;
    "Note:" is bold already (its own b written anyway, as asked), "not bold" turns bold off as the pair Word writes,
    the double underline keeps the red colour, the superscript lands where the schema puts it."""
    plan, out, rec = insert(src, ANCHOR_K, tracked=tracked, runs=[
        {"text": "Note:", "b": True}, {"text": " not bold", "b": False}, {"text": " doubled", "u": "double"},
        {"text": "2", "vertAlign": "superscript"}, {"text": "\tplain"}])
    assert rec.passed, rec.to_markdown()
    new = after_anchor(root_of(out), "K.")
    runs = list(new.iter(W + "r"))
    assert [shown(r) for r in runs] == ["Note:", " not bold", " doubled", "2", "\tplain"]
    assert kids(runs[0].find(W + "rPr")) == ["rFonts", "b", "bCs", "sz", "u"]
    assert val(runs[1], "b") == val(runs[1], "bCs") == {"val": "0"}
    assert val(runs[2], "u") == {"val": "double", "color": "FF0000"}
    assert kids(runs[3].find(W + "rPr")) == ["rFonts", "b", "sz", "u", "vertAlign"]
    assert kids(runs[4].find(W + "rPr")) == ["rFonts", "b", "sz", "u"]       # the inherited run, as it was
    assert len(new.findall(W + "ins")) == (1 if tracked else 0) and plan.rev_delta.get("ins", 0) == (1 if tracked else 0)
    plan, out, rec = insert(src, ANCHOR_K, formatting="none", runs=[{"text": "Bold", "b": True}, {"text": " plain"}])
    assert rec.passed, rec.to_markdown()
    runs = list(after_anchor(root_of(out), "K.").iter(W + "r"))
    assert kids(runs[0].find(W + "rPr")) == ["b", "bCs"] and runs[1].find(W + "rPr") is None


@pytest.mark.parametrize("tracked", [True, False])
def test_several_paragraphs_in_order_at_every_anchor(src, tracked):
    """Three paragraphs before each of two anchors (the PART 1 and PART 2 headings): in the order given, the second
    in its own style; the block indices shift by three per earlier anchor."""
    plan, out, rec = insert(src, para_sel(r"^PART [12] "), position="before", tracked=tracked, paragraphs=[
        {"text": "A.\tFirst."}, {"runs": [{"text": "B.", "b": True}, {"text": "\tSecond."}], "style": "SpecHidden"},
        {"text": "C.\tThird.\nWith a break."}])
    assert rec.passed, rec.to_markdown()
    root = root_of(out)
    for heading in ("PART 1 - GENERAL", "PART 2 - PRODUCTS"):
        trio = [after_anchor(root, heading, k) for k in (-3, -2, -1)]
        assert [shown(p) for p in trio] == ["A.\tFirst.", "B.\tSecond.", "C.\tThird.\nWith a break."], heading
        styles = [p.find(f"{W}pPr/{W}pStyle") for p in trio]
        assert [s.get(W + "val") if s is not None else None for s in styles] == ["SpecPart", "SpecHidden", "SpecPart"]
    assert [d.block_index for d in plan.diffs] == [2, 3, 4, 22, 23, 24]
    assert plan.rev_delta.get("ins", 0) == plan.rev_delta.get("ins_mark", 0) == (6 if tracked else 0)
    plan, out, rec = insert(src, para_sel(r"^PART [12] "), tracked=tracked,
                            paragraphs=[{"text": "X1"}, {"text": "X2", "style": None}])
    assert rec.passed, rec.to_markdown()
    assert [shown(after_anchor(root_of(out), "PART 2", k)) for k in (1, 2)] == ["X1", "X2"]


def test_a_new_paragraph_in_the_header_and_in_a_table_cell(src):
    plan, out, rec = insert(src, {"scope": ["headers"], "paragraphs": {"text_regex": "PROJECT NAME"}},
                            text="Issued for Bid\t2026-10-21")
    assert rec.passed, rec.to_markdown()
    plan, out, rec = insert(src, para_sel("^Tag$"), runs=[{"text": "Tag", "i": True}, {"text": "\nnumber"}],
                            tracked=False)
    assert rec.passed, rec.to_markdown()


def test_a_section_break_anchor_takes_paragraphs_before_it_only(src):
    with pytest.raises(JobException) as e:
        get_op("insert_paragraph").plan(DocxPackage(src), para_sel("^Last paragraph"), {"text": "X"})
    assert e.value.code == "section-break"
    plan, out, rec = insert(src, para_sel("^Last paragraph"), position="before", paragraphs=[{"text": "Y"}, {"text": "Z"}])
    assert rec.passed, rec.to_markdown()


def test_an_anchor_holding_a_text_box_takes_paragraphs_before_it_only(src):
    """The text box's own paragraphs come right after its anchor in the part's paragraph order: an insertion after it
    is refused rather than placed by a guessed index; before it, the anchor's own runs are inherited, not the box's."""
    def boxed(root):
        p = para_with(root, "K.")
        box = etree.fromstring(f'<w:r xmlns:w="{W[1:-1]}"><w:pict><w:txbxContent><w:p><w:r><w:t>Boxed</w:t></w:r>'
                               f'</w:p></w:txbxContent></w:pict></w:r>')
        p.append(box)
    data = mutate(src, MAIN, boxed)
    with pytest.raises(JobException) as e:
        get_op("insert_paragraph").plan(DocxPackage(data), ANCHOR_K, {"text": "X"})
    assert e.value.code == "textbox"
    plan, out, rec = insert(data, ANCHOR_K, position="before", runs=[{"text": "Y", "i": True}])
    assert rec.passed, rec.to_markdown()
    assert kids(after_anchor(root_of(out), "K.", -1).find(f"{W}ins/{W}r/{W}rPr")) == ["rFonts", "b", "i", "iCs", "sz",
                                                                                       "u"]


@pytest.mark.parametrize("params", [
    {}, {"text": ""}, {"text": "   "}, {"text": "\t\n"}, {"text": "a\x01b"}, {"text": "a\r\nb"}, {"text": 3},
    {"text": "a", "runs": [{"text": "b"}]}, {"runs": []}, {"runs": "Bold"}, {"runs": [{"text": ""}]},
    {"runs": [{"text": "x", "strike": True}]}, {"runs": [{"text": "x", "b": "yes"}]},
    {"runs": [{"text": "x", "u": "squiggly"}]}, {"runs": [{"text": "x", "vertAlign": "super"}]}, {"runs": [{"b": True}]},
    {"paragraphs": []}, {"paragraphs": "two"}, {"paragraphs": [{"text": "a", "runs": [{"text": "b"}]}]},
    {"paragraphs": [{"style": "SpecPart"}]}, {"paragraphs": [{"text": "a", "colour": "red"}]},
    {"paragraphs": [{"text": "a", "style": "Nope"}]}, {"paragraphs": [{"text": "a", "style": 1}]},
    {"paragraphs": [{"text": "a"}], "text": "b"},
])
def test_bad_wording_is_refused(src, params):
    with pytest.raises(JobException) as e:
        get_op("insert_paragraph").plan(DocxPackage(src), SUMMARY, params)
    assert e.value.code == "bad-params"


def test_the_spec_schema_knows_runs_and_paragraphs(src):
    def spec(params):
        return {"request": "r", "summary": "s", "mode": "content",
                "ops": [{"op": "insert_paragraph", "select": SUMMARY, "params": params}]}
    validate_spec(spec({"runs": [{"text": "Note:", "b": True, "vertAlign": "baseline"}, {"text": " x"}]}))
    validate_spec(spec({"paragraphs": [{"text": "a"}, {"runs": [{"text": "b", "u": "single"}], "style": "SpecPart"}]}))
    for bad in ({"runs": [{"text": "x", "strike": True}]}, {"paragraphs": [{"text": "a", "colour": "red"}]},
                {"runs": [{"text": "x", "u": "squiggly"}]}):
        with pytest.raises(SpecError):
            validate_spec(spec(bad))
    plan, _ = plan_spec(DocxPackage(src), spec({"paragraphs": [{"text": "a\tb"}, {"text": "c"}]}))
    a = plan.property_assertions[0]
    assert a["paragraphs"] == [{"text": "a\tb"}, {"text": "c"}] and "text" not in a


# ---- planted OUTPUT defects --------------------------------------------------------------------------------------------

def test_wrong_inserted_paragraphs_are_caught(src):
    plan, out, rec = insert(src, SUMMARY, tracked=False, paragraphs=[
        {"text": "A.\tFirst.\nSecond line."}, {"runs": [{"text": "B.", "b": True}, {"text": " Plain."}]}])
    assert rec.passed

    def literal_tab(root):                     # the tab typed into the text instead of Word's own
        tab = after_anchor(root, "1.01\tSUMMARY").find(f".//{W}tab")
        tab.getprevious().text += "\t"
        tab.getparent().remove(tab)

    def page_break(root):                      # a page break where a line break was asked
        after_anchor(root, "1.01\tSUMMARY").find(f".//{W}br").set(W + "type", "page")

    def bold_on_both(root):                    # the plain segment bold too
        r = list(after_anchor(root, "1.01\tSUMMARY", 2).iter(W + "r"))[1]
        rpr = r.find(W + "rPr")
        if rpr is None:
            rpr = etree.Element(W + "rPr")
            r.insert(0, rpr)
        etree.SubElement(rpr, W + "b")

    def segments_merged(root):                 # one run: the bold segment's formatting spread over both
        p = after_anchor(root, "1.01\tSUMMARY", 2)
        first, second = list(p.iter(W + "r"))
        first.find(W + "t").text += second.find(W + "t").text
        p.remove(second)

    def order_swapped(root):                   # the same two paragraphs the other way round: V2 counts alike
        a, b = after_anchor(root, "1.01\tSUMMARY"), after_anchor(root, "1.01\tSUMMARY", 2)
        a.addprevious(b)

    for defect in (literal_tab, page_break, bold_on_both, segments_merged):
        assert "V10" in caught(src, out, plan, MAIN, defect), defect.__name__
    assert caught(src, out, plan, MAIN, order_swapped) == ["V10"]

    plan, out, rec = insert(src, SUMMARY, text="A.\tTracked.")
    assert rec.passed

    def run_outside_the_insertion(root):       # Word would not show it as inserted
        ins = after_anchor(root, "1.01\tSUMMARY").find(W + "ins")
        for r in list(ins):
            ins.addprevious(r)
    assert "V10" in caught(src, out, plan, MAIN, run_outside_the_insertion)


# ---- planted IMPLEMENTATION defects --------------------------------------------------------------------------------------

def test_a_shared_reading_that_drops_the_last_segment_is_caught(src, monkeypatch):
    """Plan (its V2 text) and apply agree on a paragraph without its last segment; V10 reads the user's words itself."""
    real = insert_module.new_paragraphs

    def short(params):
        out = real(params)
        for para in out:
            para["segments"] = para["segments"][:-1] or para["segments"]
        return out
    monkeypatch.setattr(insert_module, "new_paragraphs", short)
    plan, out, rec = insert(src, SUMMARY, runs=[{"text": "Note:", "b": True}, {"text": " read the drawings."}])
    assert failed(rec) == ["V10"]


def test_a_shared_break_table_that_writes_a_carriage_return_is_caught(src, monkeypatch):
    """A line break written as w:cr wherever the core writes one: V2 (no text) cannot tell; V10's own reading can."""
    monkeypatch.setattr(textedit, "BREAKS", {"\t": "tab", "\n": "cr"})
    plan, out, rec = insert(src, SUMMARY, text="Line one\nline two", tracked=False)
    assert failed(rec) == ["V10"]


def test_a_segment_writer_that_forgets_the_inherited_run_is_caught(src, monkeypatch):
    real = insert_module.segment_rpr
    monkeypatch.setattr(insert_module, "segment_rpr", lambda base, props: real(None, props))
    plan, out, rec = insert(src, ANCHOR_K, runs=[{"text": "Note:", "b": True}, {"text": " x"}])
    assert failed(rec) == ["V10"]


# ==== §4.3 replace_text: tabs and line breaks in the new text, through both sides of the edit core =========================

def replace(data: bytes, find: str, new: str, select: dict = None, **params):
    return run_ops(data, [("replace_text", select or {}, {"find": find, "replace": new, "author": AUTHOR, **params})])


@pytest.mark.parametrize("tracked", [True, False])
def test_a_line_break_in_the_replacement(src, tracked):
    plan, out, rec = replace(src, "SUMMARY", "SUMMARY\nAND SCOPE", tracked=tracked)
    assert rec.passed, rec.to_markdown()
    p = para_with(root_of(out), "1.01")
    if tracked:
        assert content(p) == [("t", "1.01"), ("tab", ""), ("delText", "SUMMARY"), ("t", "SUMMARY"), ("br", ""),
                              ("t", "AND SCOPE")]
        assert plan.rev_delta["ins"] == plan.rev_delta["del"] == 1
    else:                                       # in the edited run itself, after its w:t
        assert [(local(c), c.text or "") for c in p.find(W + "r") if local(c) != "rPr"] == \
            [("t", "1.01"), ("tab", ""), ("t", "SUMMARY"), ("br", ""), ("t", "AND SCOPE")]
    d = plan.diffs[0]
    assert d.struct_after == {"tab": 1, "br": 1} and "\n" not in d.after and d.accepted_view == "1.01SUMMARYAND SCOPE"


@pytest.mark.parametrize("tracked", [True, False])
def test_tabs_in_several_occurrences_of_one_text_node(src, tracked):
    plan, out, rec = replace(src, " ", "\t", para_sel("^SECTION"), tracked=tracked)
    assert rec.passed, rec.to_markdown()
    p = para_with(root_of(out), "SECTION")
    # (tracked, each deleted space stays before its tab as deleted text)
    assert shown(p) == ("SECTION \t21 \t13 \t13" if tracked else "SECTION\t21\t13\t13")
    assert plan.diffs[0].struct_after == {"tab": 3}
    assert plan.rev_delta.get("ins", 0) == plan.rev_delta.get("del", 0) == (3 if tracked else 0)


@pytest.mark.parametrize("tracked", [True, False])
def test_inside_someones_pending_insertion(src, tracked):
    """Word's grammar for an edit inside Jeremy's insertion holds with the new run's tab: the deletion inside it, the
    new text after it, the rest of it in a second insertion."""
    plan, out, rec = replace(src, "hot-dip galvanized", "hot-dip\tgalvanized", tracked=tracked)
    assert rec.passed, rec.to_markdown()
    if tracked:
        assert plan.rev_delta["ins"] == 1 and plan.rev_delta["del"] == 1
    p = para_with(root_of(out), "M.")
    assert len(p.findall(f".//{W}r/{W}tab")) == 2


@pytest.mark.parametrize("tracked", [True, False])
def test_the_documents_own_literal_tab_stays_literal(src, tracked):
    """The w:t of "L." itself holds literal tab characters: only the replacement's line break becomes a w:br — the
    document's own characters before and after the edit are never re-read as structure."""
    plan, out, rec = replace(src, "REPLACE ME", "A\nB", tracked=tracked)
    assert rec.passed, rec.to_markdown()
    p = para_with(root_of(out), "L.")
    assert len(p.findall(f".//{W}br")) == 1 and not p.findall(f".//{W}tab")
    assert "".join(t.text for t in p.iter(W + "t")).count("\t") == LITERAL_TAB_TEXT.count("\t") + 1


def test_a_replacement_of_only_a_tab_declares_its_structure(src):
    """Tracked, the old words stay as deleted text: the block text is unchanged, so the plan declares the new tab as a
    structure-only change (V2 leaves it out, V3 checks it); untracked the text changes too."""
    plan, out, rec = replace(src, " and ", "\t", para_sel(r"^B\."))
    assert rec.passed, rec.to_markdown()
    d = plan.diffs[0]
    assert d.before == d.after and d.struct_after == {"tab": 2}
    plan, out, rec = replace(src, " and ", "\t", para_sel(r"^B\."), tracked=False)
    assert rec.passed and plan.diffs[0].after == "B.Highlighted yellow notegreen note"


def test_in_the_header_and_in_a_hyperlink(src):
    plan, out, rec = replace(src, "PROJECT NAME", "PROJECT\nNAME", scope=["headers"])
    assert rec.passed, rec.to_markdown()
    plan, out, rec = replace(src, "Owner standards", "Owner\tstandards", tracked=False)
    assert rec.passed, rec.to_markdown()
    assert para_with(root_of(out), "N.").find(f"{W}hyperlink/{W}r/{W}tab") is not None


@pytest.mark.parametrize("new", ["a\x0bb", "a\r\nb", "a\x00b", "\x7f"])
def test_other_control_characters_are_refused(src, new):
    with pytest.raises(JobException) as e:
        get_op("replace_text").plan(DocxPackage(src), {}, {"find": "SUMMARY", "replace": new})
    assert e.value.code == "bad-params"


def test_wrong_replacements_are_caught(src):
    plan, out, rec = replace(src, "SUMMARY", "SUMMARY\nAND SCOPE", tracked=False)
    assert rec.passed

    def literal_newline(root):                 # a newline typed into the text: Word shows a space
        br = para_with(root, "1.01").find(f".//{W}br")
        br.getprevious().text += "\n"
        br.getparent().remove(br)

    def break_before_the_word(root):           # the break placed before "SUMMARY": same text, same counts
        r = para_with(root, "1.01").find(W + "r")
        br = r.find(W + "br")
        r.remove(br)
        r.find(W + "tab").addnext(br)

    def a_tab_too_many(root):
        para_with(root, "1.01").find(W + "r").append(etree.Element(W + "tab"))

    for defect in (literal_newline, a_tab_too_many):
        assert "V10" in caught(src, out, plan, MAIN, defect), defect.__name__
    assert caught(src, out, plan, MAIN, break_before_the_word) == ["V10"]

    plan, out, rec = replace(src, "SUMMARY", "SUMMARY\nAND SCOPE")
    assert rec.passed

    def break_outside_the_insertion(root):     # the break shown as never inserted
        p = para_with(root, "1.01")
        br = p.find(f"{W}ins/{W}r/{W}br")
        r = etree.SubElement(p, W + "r")
        br.getparent().remove(br)
        r.append(br)
    assert "V10" in caught(src, out, plan, MAIN, break_outside_the_insertion)


def test_a_shared_break_table_is_caught_in_a_replacement(src, monkeypatch):
    """w:cr for a line break, the same in the plan's declared structure and the apply: V3 agrees with the plan, V10's
    item model expects a w:br."""
    monkeypatch.setattr(textedit, "BREAKS", {"\t": "tab", "\n": "cr"})
    plan, out, rec = replace(src, "SUMMARY", "SUMMARY\nAND SCOPE", tracked=False)
    assert plan.diffs[0].struct_after == {"tab": 1, "cr": 1}
    assert failed(rec) == ["V10"]


# ---- the edit core itself: breaks=True on both sides; the strict core unchanged ------------------------------------------

def _plain_ranges(rt) -> List[Tuple[int, int]]:
    """A few ranges of plain characters of a paragraph's run text: the first word, the last character, a middle one."""
    plain = [i for i, ps in enumerate(rt.positions) if ps.offset >= 0]
    if len(plain) < 4:
        return []
    out = [(plain[0], plain[0] + 2), (plain[-1], plain[-1] + 1), (plain[len(plain) // 2], plain[len(plain) // 2] + 1)]
    return [(a, b) for a, b in out if all(rt.positions[i].offset >= 0 for i in range(a, b))]


@pytest.mark.parametrize("tracked", [False, True])
def test_placed_breaks_agree_on_both_sides(tracked):
    """The transform plans and applies new texts holding tabs and line breaks (at the start, inside, at the end, alone,
    doubled) over ranges of every paragraph of two fixtures; the verifier's own item model with breaks=True expects
    exactly the paragraph the transform wrote, and the declared structure is the paragraph's own."""
    from make_fixtures import build_batch1
    n = 0
    for data in (build_text(), build_batch1()):
        for src_p in DocxPackage(data).xml(MAIN).iter(W + "p"):
            rt = textedit.RunText(src_p)
            for a, b in _plain_ranges(rt):
                for new in ("\tX", "X\nY", "Z\t", "\n", "\t\tW\n\n"):
                    p = copy.deepcopy(src_p)
                    frag = PlanFragment(job="t", mode="content", params={"author": AUTHOR}, intended_parts=[])
                    try:
                        done = textedit.plan_paragraph(frag, MAIN, 0, p, [textedit.Edit(a, b, new)], tracked, set(),
                                                       "here", breaks=True)
                    except JobException:
                        continue                          # a range the core refuses for its own reasons (a field …)
                    if tracked:
                        textedit.apply_tracked(p, done, RevisionStamp.now(AUTHOR, False), [900], breaks=True)
                    else:
                        textedit.apply_untracked(p, done, breaks=True)
                    errs: List[str] = []
                    check_edited_paragraph(src_p, p, [(a, b, new, False)], tracked, AUTHOR, set(), set(), "here", errs,
                                           breaks=True)
                    assert errs == [], (rt.text, a, b, new, errs)
                    d = frag.diffs[0]
                    assert d.struct_after == ooxml.block_struct_counts(p), (rt.text, a, b, new)
                    if not tracked:
                        assert textedit.RunText(p).text == rt.text[:a] + new + rt.text[b:]
                    n += 1
    assert n >= 100


def test_the_strict_core_still_refuses_a_placed_tab_on_both_sides():
    p = para_with(DocxPackage(build_text()).xml(MAIN), "B.")
    rt = textedit.RunText(p)
    a = rt.text.index("yellow")
    for new in ("x\ty", "x\ny"):
        with pytest.raises(JobException) as e:
            textedit.check_edits(rt, [textedit.Edit(a, a + 6, new)], False, set(), "here")
        assert e.value.code == "bad-params"
        with pytest.raises(ValueError):
            v_check_edits(p, v_items(p), [(a, a + 6, new, False)], False, set())
        with pytest.raises(JobException) as e:             # apply's own guard: not planned with breaks
            textedit.apply_untracked(copy.deepcopy(p), [textedit.Edit(a, a + 6, new)])
        assert e.value.code == "internal"
    with pytest.raises(JobException) as e:                  # breaks never admit another control character
        textedit.check_edits(rt, [textedit.Edit(a, a + 6, "x\ry")], False, set(), "here", breaks=True)
    assert e.value.code == "bad-params"
    with pytest.raises(ValueError):
        v_check_edits(p, v_items(p), [(a, a + 6, "x\ry", False)], False, set(), breaks=True)


@pytest.mark.parametrize("tracked", [False, True])
def test_a_paragraph_edits_assertion_with_breaks_runs_through_the_suite(src, tracked):
    """The core's own assertion (``paragraph_edits`` with ``breaks``) verified end to end — and refused by the
    verifier's strict reading when the plan does not say breaks."""
    pkg = DocxPackage(src)
    pars = list(pkg.xml(MAIN).iter(W + "p"))
    p = next(q for q in pars if shown(q).startswith("B."))
    bi = pars.index(p)
    rt = textedit.RunText(p)
    a = rt.text.index("yellow note")
    frag = PlanFragment(job="t", mode="content" if tracked else "untracked", params={"author": AUTHOR},
                        intended_parts=[], untracked_diffs_expected=not tracked, rev_delta={"ins": 0, "del": 0})
    edits = textedit.plan_paragraph(frag, MAIN, bi, p, [textedit.Edit(a, a + 11, "yellow\tnote\nhere")], tracked,
                                    set(), "B.", breaks=True, author=AUTHOR)
    work = pkg.copy()
    root = work.xml(MAIN)
    textedit.apply_part_edits(root, {str(bi): textedit.edits_to_data(edits)}, tracked,
                              RevisionStamp.now(AUTHOR, False), [ooxml.max_revision_id(pkg) + 1], breaks=True)
    work.write_xml(MAIN, root)
    out = work.output_bytes()
    plan = frag.to_dict()
    assert plan["property_assertions"][0]["breaks"] is True
    rec = run_suite(src, out, plan, "s", "o")
    assert rec.passed, rec.to_markdown()
    del plan["property_assertions"][0]["breaks"]
    rec = run_suite(src, out, plan, "s", "o")
    assert failed(rec) == ["V10"] and "control character" in v10(rec)


# ==== what Claude sees: placed words, the catalog, the batch pipeline, a render ==========================================

def test_every_placed_text_is_traced_to_the_user():
    spec = {"request": "r", "summary": "s", "mode": "content", "ops": [
        {"op": "insert_paragraph", "select": SUMMARY, "params": {"text": "A.\tProvide calculations."}},
        {"op": "insert_paragraph", "select": SUMMARY, "params": {"runs": [{"text": "Note:", "b": True},
                                                                          {"text": " see drawings"}]}},
        {"op": "insert_paragraph", "select": SUMMARY, "params": {"paragraphs": [
            {"text": "First"}, {"runs": [{"text": "Second", "i": True}, {"text": "\nThird"}]}]}},
        {"op": "replace_text", "params": {"find": "PART 1 GENERAL", "replace": "PART 1\nGENERAL"}}]}
    # a paragraph of segments is placed as it reads: its segments joined, traced as one phrase
    assert compiler.placed_words(spec) == ["A.\tProvide calculations.", "Note: see drawings", "First",
                                           "Second\nThird", "PART 1\nGENERAL"]
    request = ("Add 'A. Provide calculations.' with a tab after A., a note 'Note: see drawings' with Note: in bold, "
               "then the paragraphs First and 'Second Third' with Second in italic and Third on its own line; put "
               "GENERAL on its own line in 'PART 1 GENERAL'")
    assert compiler.untraceable(spec, [request]) == []
    # a tab or a line break is whitespace like any other: the words around it trace only together, in order
    assert compiler.untraceable(spec, ["Add A. and Note: see drawings First Second Third PART 1 GENERAL"]) == \
        ["A.\tProvide calculations."]
    assert compiler.untraceable(spec, ["Add 'Note:' in bold then 'see drawings'"]) == \
        [w for w in compiler.placed_words(spec)]
    assert compiler.untraceable(spec, ["Add a note"]) == compiler.placed_words(spec)


@pytest.mark.parametrize("placed", ["Provide shop drawings\nnot\nfor all piping.",   # a word spliced in from elsewhere
                                    "for all piping.\tProvide shop drawings",        # the stretches reordered
                                    "1.\tProvide shop drawings for all piping.",      # "1." from inside "1.2"
                                    "2\tProvide shop drawings for all piping."])
def test_words_around_a_tab_or_break_cannot_be_stitched_together(placed):
    """Review on PR #53: tracing each stretch between tabs and breaks on its own let words be spliced from elsewhere in
    the request, reordered, or lifted from inside a number."""
    request = "In 1.2, add the paragraph 'Provide shop drawings for all piping.' Do not change anything else."
    for spec in ({"ops": [{"op": "replace_text", "params": {"find": "x", "replace": placed}}]},
                 {"ops": [{"op": "insert_paragraph", "params": {"text": placed}}]},
                 {"ops": [{"op": "insert_paragraph", "params": {"runs": [{"text": part} for part in
                                                                         placed.replace("\t", "\n").split("\n")]}}]}):
        assert compiler.untraceable(spec, [request]), (placed, spec)
    exact = {"ops": [{"op": "insert_paragraph", "params": {"text": "Provide shop drawings\tfor all piping."}}]}
    assert compiler.untraceable(exact, [request]) == []          # the user's words in order, a tab between them


def test_the_catalog_says_what_the_ops_now_take(src):
    ops = op_catalog()["ops"]
    run = ops["set_properties"]["params"]["properties"]["run"]["properties"]
    assert {"vertAlign", "vanish", "highlight", "spacing", "dstrike"} <= set(run)
    assert {"runs", "paragraphs"} <= set(ops["insert_paragraph"]["params"]["properties"])
    assert "required" not in ops["insert_paragraph"]["params"]
    assert any("line break" in a for a in COMMON_ASSUMPTIONS["insert_paragraph"])
    assert any("line break" in a for a in COMMON_ASSUMPTIONS["replace_text"])
    assert any("double strikethrough" in a for a in COMMON_ASSUMPTIONS["set_properties"])


def _spec(mode: str, *ops):
    return {"request": "r", "summary": "s", "mode": mode, "ops": list(ops)}


def test_specs_run_through_the_batch_pipeline(src, tmp_path):
    """Plan, apply, verify, write and deliver in a worker — the plan and its transform data through JSON."""
    from spec_surgeon.batch import run_batch
    path = tmp_path / "21 13 13 - SPRINKLERS.docx"
    path.write_bytes(src)
    specs = [
        _spec("formatting", {"op": "set_properties", "select": para_sel(r"^[A-D]\."),
                             "params": {"run": {"vertAlign": "superscript", "highlight": "yellow", "spacing": 0.5,
                                                "dstrike": True}}}),
        _spec("content", {"op": "insert_paragraph", "select": SUMMARY, "params": {"paragraphs": [
            {"text": "A.\tFirst.\nSecond line."}, {"runs": [{"text": "B.", "b": True}, {"text": "\tSecond."}]}]}},
              {"op": "replace_text", "select": {"scope": ["headers"]},
               "params": {"find": "PROJECT NAME", "replace": "PROJECT\tNAME"}}),
    ]
    for k, spec in enumerate(specs):
        batch = run_batch([str(path)], "edit-spec", {}, tmp_path / f"out{k}", workers=1, renderer="none",
                          do_render=False, spec=spec)
        f = batch.files[0]
        assert f.status == "PASS", f.record.to_markdown() if f.record else f.exception


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
def test_libreoffice_opens_and_renders_the_output(src, tmp_path):
    from spec_surgeon.render import libreoffice
    out = src
    for stage in (                              # one stage each: an insertion restructures the part a formatting op reads
            ("set_properties", para_sel(r"^[A-D]\."), {"run": {"vertAlign": "subscript", "highlight": "cyan",
                                                              "spacing": -0.5, "dstrike": True}, "author": AUTHOR}),
            ("insert_paragraph", para_sel("^END OF SECTION"), {"author": AUTHOR, "paragraphs": [
                {"text": "A.\tFirst.\nSecond line."},
                {"runs": [{"text": "m", "i": True}, {"text": "2", "vertAlign": "superscript"}]}]}),
            ("replace_text", {"scope": ["headers"]}, {"find": "PROJECT NAME", "replace": "PROJECT\nNAME",
                                                      "author": AUTHOR})):
        _, out_next, rec = run_ops(out, [stage])
        assert rec.passed, rec.to_markdown()
        out = out_next
    (tmp_path / "out.docx").write_bytes(out)
    pdfs = libreoffice.render_pdfs([tmp_path / "out.docx"], tmp_path / "pdf", workers=1)
    if len(pdfs) != 1:
        pytest.skip("LibreOffice did not render here")
    pdf = pdfs[tmp_path / "out.docx"]
    assert pdf.exists() and pdf.stat().st_size > 1000 and pdf.read_bytes().startswith(b"%PDF")
