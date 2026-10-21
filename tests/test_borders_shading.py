"""set_borders_shading and V10's ``borders_shading`` kind (built-in batch 4, item 26 — docs/BUILTIN_OPS_BATCH4_DESIGN.md
§1): end to end on the architect's spec (direct and style targets, plain and tracked, "none" against a style's box and
shading, already as asked); a wrong OUTPUT per rule; wrong IMPLEMENTATIONS shared by plan and apply; conflicts;
parameters and refusals."""
from __future__ import annotations

import io
import zipfile
from typing import List, Tuple

import pytest
from lxml import etree

from make_fixtures import build_styles
from spec_surgeon.editspec import plan_spec
from spec_surgeon.ooxml import paragraphs
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import set_borders_shading as bs_module
from spec_surgeon.ops.base import verify_family
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.verify import run_suite
from spec_surgeon.verify import borders as vborders

AUTHOR = "Abraham Borg"
MAIN = "word/document.xml"
STYLES = "word/styles.xml"
NOTES = {"paragraphs": {"text_regex": "^NOTE"}}
BOX = {"top": {}, "left": {"space": 4}, "bottom": {}, "right": {"space": 4}}


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


def bs(data: bytes, select: dict, params: dict):
    return run_ops(data, [("set_borders_shading", select, params)])


def failed(rec):
    return [c.id for c in rec.checks if c.status == "FAIL"]


def v10(rec) -> str:
    c = next(c for c in rec.checks if c.id == "V10")
    return c.summary + " | " + " | ".join(c.data.get("errors", []))


def para(root, needle):
    return [p for p in root.iter(W + "p") if "".join(t.text or "" for t in p.iter(W + "t")).startswith(needle)][0]


def style(root, sid):
    return next(s for s in root.findall(W + "style") if s.get(W + "styleId") == sid)


def root_of(data: bytes, part: str = MAIN):
    return DocxPackage(data).xml(part)


def box(ppr) -> List[Tuple[str, dict]]:
    """The w:pBdr of a pPr as (side, {attr: value}) in order (local names); [] when none."""
    b = ppr.find(W + "pBdr") if ppr is not None else None
    return [(etree.QName(s).localname, {etree.QName(k).localname: v for k, v in s.attrib.items()})
            for s in (b if b is not None else [])]


def shd(ppr):
    e = ppr.find(W + "shd") if ppr is not None else None
    return None if e is None else {etree.QName(k).localname: v for k, v in e.attrib.items()}


def mutate(docx: bytes, part: str, fn) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(docx))
    root = etree.fromstring(zin.read(part))
    fn(root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            z.writestr(info, serialize_xml(root) if info.filename == part else zin.read(info.filename))
    return buf.getvalue()


def el(tag, **attrs):
    e = etree.Element(W + tag)
    for k, v in attrs.items():
        e.set(W + k, v)
    return e


SINGLE = {"val": "single", "sz": "4", "space": "1", "color": "auto"}
NIL = {"val": "nil"}


# ---- end to end ----------------------------------------------------------------------------------------------------

def test_notes_get_a_box_and_shading_tracked(src):
    """The two NOTE paragraphs already show ArchNote's box and D9D9D9: asking a top rule and F2F2F2 shading writes the
    top side, 'nil' for every side the style shows, and the new shading — one w:pPrChange each (targeted → tracked)."""
    plan, out, rec = bs(src, NOTES, {"borders": {"top": {}}, "shading": {"fill": "f2f2f2"}, "author": AUTHOR})
    assert rec.passed, rec.to_markdown()
    a = plan.property_assertions[0]
    assert a["kind"] == "borders_shading" and a["target"] == "direct" and a["expected_paragraphs"] == 2
    assert a["changed"] == {MAIN: [[5, ["pBdr", "shd"]], [6, ["pBdr", "shd"]]]}
    assert plan.ops[0]["params"]["tracked"] is True and plan.rev_delta["pPrChange"] == 2
    assert plan.geometry_reference == "none" and "border" in plan.geometry_reason
    root = root_of(out)
    for needle in ("NOTE TO", "NOTE child"):
        ppr = para(root, needle).find(W + "pPr")
        assert box(ppr) == [("top", SINGLE), ("left", NIL), ("bottom", NIL), ("right", NIL)], needle
        assert shd(ppr) == {"val": "clear", "color": "auto", "fill": "F2F2F2"}
        chg = ppr.find(W + "pPrChange")
        assert chg.get(W + "author") == AUTHOR and [etree.QName(c).localname for c in chg[0]] == ["pStyle"]


def test_none_against_a_style_box_writes_nil_and_clear_auto_plain(src):
    """'No borders, no shading' over the whole document: where a style shows them, nil sides and a clear/auto shading
    are written (the style keeps its definition); direct boxes and shading are removed; everything else is already as
    asked. A whole-document selection is plain."""
    plan, out, rec = bs(src, {}, {"borders": "none", "shading": "none"})
    assert rec.passed, rec.to_markdown()
    assert plan.ops[0]["params"]["tracked"] is False and not plan.rev_delta.get("pPrChange")
    a = plan.property_assertions[0]
    assert a["changed"] == {MAIN: [[1, ["pBdr"]], [5, ["pBdr", "shd"]], [6, ["pBdr", "shd"]], [7, ["pBdr", "shd"]]]}
    root = root_of(out)
    note, child = para(root, "NOTE TO").find(W + "pPr"), para(root, "NOTE child").find(W + "pPr")
    assert box(note) == box(child) == [("top", NIL), ("left", NIL), ("bottom", NIL), ("right", NIL)]
    assert shd(note) == shd(child) == {"val": "clear", "color": "auto", "fill": "auto"}
    assert para(root, "Coordinate").find(W + "pPr") is None                 # the emptied pPr went with them
    assert box(para(root, "WET-PIPE").find(W + "pPr")) == []
    assert any("already as asked, left alone: 15" in n for n in plan.notes)
    # run again on the output: nothing left to do
    plan2, out2, rec2 = bs(out, {}, {"borders": "none", "shading": "none"})
    assert rec2.passed and plan2.property_assertions[0]["expected_paragraphs"] == 0 and plan2.intended_parts == []
    assert plan2.geometry_reference == "source"


def test_shading_alone_keeps_the_source_layout_reference(src):
    plan, out, rec = bs(src, {"paragraphs": {"text_regex": "^Coordinate"}}, {"shading": {"fill": "D9D9D9", "pattern": "pct10"}})
    assert rec.passed, rec.to_markdown()
    assert plan.geometry_reference == "source"
    assert shd(para(root_of(out), "Coordinate").find(W + "pPr")) == {"val": "pct10", "color": "auto", "fill": "D9D9D9"}
    assert box(para(root_of(out), "Coordinate").find(W + "pPr"))[0][1]["val"] == "double"   # borders not asked: kept


def test_already_as_asked_is_left_alone_and_uncounted(src):
    plan, out, rec = bs(src, {"paragraphs": {"text_regex": "^Coordinate"}},
                        {"borders": {"top": {"style": "double"}, "bottom": {"style": "double"}},
                         "shading": {"fill": "FFFF00"}})
    assert rec.passed, rec.to_markdown()
    assert plan.property_assertions[0]["expected_paragraphs"] == 0 and plan.intended_parts == []
    assert not {k: v for k, v in plan.rev_delta.items() if v} and out == src
    assert any("already as asked, left alone: 1" in n for n in plan.notes)


def test_a_partly_right_paragraph_changes_only_the_element_that_differs(src):
    plan, out, rec = bs(src, {"paragraphs": {"text_regex": "^Coordinate"}},
                        {"borders": {"top": {"style": "double"}, "bottom": {"style": "double"}},
                         "shading": "none", "tracked": False})
    assert rec.passed, rec.to_markdown()
    assert plan.property_assertions[0]["changed"] == {MAIN: [[7, ["shd"]]]}
    assert plan.geometry_reference == "source"                              # the borders did not change
    t = set(map(tuple, _touches(src, {"paragraphs": {"text_regex": "^Coordinate"}},
                                {"borders": {"top": {"style": "double"}, "bottom": {"style": "double"}},
                                 "shading": "none"})))
    assert {(MAIN, "7", "pPr"), (MAIN, "7", "shd"), (MAIN, "7", "verify-props:pBdr")} <= t and (MAIN, "7", "pBdr") not in t


def test_a_table_style_and_the_document_defaults_count_as_lower_levels(src):
    """A side the table style shows, and shading the document defaults give, are cancelled too."""
    def lower(root):
        style(root, "ArchTable").find(W + "pPr").insert(0, etree.fromstring(
            f'<w:pBdr xmlns:w="{W[1:-1]}"><w:left w:val="single" w:sz="4" w:space="4" w:color="auto"/></w:pBdr>'))
        root.find(W + "docDefaults/" + W + "pPrDefault/" + W + "pPr").insert(0, el("shd", val="clear", color="auto",
                                                                                      fill="EEEEEE"))
    data = mutate(src, STYLES, lower)
    plan, out, rec = bs(data, {"paragraphs": {"text_regex": "^Cell item"}}, {"borders": {"top": {}}, "shading": "none"})
    assert rec.passed, rec.to_markdown()
    ppr = para(root_of(out), "Cell item").find(W + "pPr")
    assert box(ppr) == [("top", SINGLE), ("left", NIL)]
    assert shd(ppr) == {"val": "clear", "color": "auto", "fill": "auto"}


def test_styles_target_changes_the_definitions_and_flags_what_keeps_its_own(src):
    """Normal gets a bottom rule: every style based on it inherits it except those defining their own box (flagged),
    and two paragraphs with a direct box keep theirs (flagged). Never tracked; borders → no layout reference."""
    plan, out, rec = bs(src, {"styles": {"ids": ["Normal"]}}, {"borders": {"bottom": {"size": 8}}})
    assert rec.passed, rec.to_markdown()
    a = plan.property_assertions[0]
    assert a["target"] == "styles" and a["expected_ids"] == ["Normal"] and a["changed"] == {"Normal": ["pBdr"]}
    assert plan.geometry_reference == "none" and not {k: v for k, v in plan.rev_delta.items() if v}
    assert box(style(root_of(out, STYLES), "Normal").find(W + "pPr")) == [("bottom", {**SINGLE, "sz": "8"})]
    assert any("2 paragraph(s) using style Normal" in f and "borders" in f for f in plan.flags), plan.flags
    assert any("'ArchNote', 'ArchNoteChild'" in f and "based on Normal" in f for f in plan.flags), plan.flags
    assert {(STYLES, "style:Normal", "pPr"), (STYLES, "docDefaults", "verify:pPr")} <= set(map(tuple, _touches(src, {"styles": {"ids": ["Normal"]}}, {"borders": {"bottom": {"size": 8}}})))


def test_styles_target_a_child_style_cancels_what_its_parent_shows(src):
    """Two styles of one chain in one call: each is computed from the SOURCE levels below it (predict first) — the
    child writes nil for the sides its parent showed in the source; the box rendered is the asked one either way."""
    plan, out, rec = bs(src, {"styles": {"ids": ["ArchNote", "ArchNoteChild"]}},
                        {"borders": {"left": {"size": 12, "space": 4}}, "shading": {"fill": "auto"}})
    assert rec.passed, rec.to_markdown()
    styles = root_of(out, STYLES)
    assert box(style(styles, "ArchNote").find(W + "pPr")) == [("left", {**SINGLE, "sz": "12", "space": "4"})]
    assert box(style(styles, "ArchNoteChild").find(W + "pPr")) == [
        ("top", NIL), ("left", {**SINGLE, "sz": "12", "space": "4"}), ("bottom", NIL), ("right", NIL)]
    assert shd(style(styles, "ArchNoteChild").find(W + "pPr")) == {"val": "clear", "color": "auto", "fill": "auto"}


def test_styles_already_as_asked_are_noted(src):
    plan, out, rec = bs(src, {"styles": {"ids": ["ArchNote"]}},
                        {"borders": {"top": {}, "left": {"space": 4}, "bottom": {}, "right": {"space": 4}},
                         "shading": {"fill": "D9D9D9"}})
    assert rec.passed and plan.property_assertions[0]["expected_ids"] == [] and out == src
    assert any("already as asked: ['ArchNote']" in n for n in plan.notes)


def test_a_pending_paragraph_change_refuses_tracked_and_is_flagged_plain(src):
    sel = {"paragraphs": {"text_regex": "^Earlier"}}
    with pytest.raises(JobException) as e:
        bs(src, sel, {"shading": {"fill": "FFFF00"}})
    assert e.value.code == "tracked-format"
    plan, out, rec = bs(src, sel, {"shading": {"fill": "FFFF00"}, "tracked": False})
    assert rec.passed, rec.to_markdown()
    assert any("pending tracked paragraph change" in f for f in plan.flags)
    chg = para(root_of(out), "Earlier").find(W + "pPr/" + W + "pPrChange")
    assert chg.get(W + "author") == "Jeremy"                                  # someone else's change: kept as it was


def _touches(data, select, params):
    return get_op("set_borders_shading").plan(DocxPackage(data), select, params).touches


def test_touches_declare_changes_and_reads(src):
    t = set(map(tuple, _touches(src, NOTES, {"borders": {"top": {}}})))
    assert {(MAIN, "5", "pPr"), (MAIN, "5", "pBdr"), (MAIN, "5", "verify-props:pStyle"), (MAIN, "5", "verify-props:pBdr"),
            (MAIN, "scope", "verify"), (STYLES, "style:ArchNote", "verify:pPr"), (STYLES, "style:Normal", "verify:pPr"),
            (STYLES, "docDefaults", "verify:pPr")} <= t
    assert (MAIN, "5", "shd") not in t and (MAIN, "5", "verify-props:shd") not in t
    # an already-as-asked paragraph is read (its post-condition), not changed
    t = set(map(tuple, _touches(src, {"paragraphs": {"text_regex": "^Coordinate"}}, {"shading": {"fill": "FFFF00"}})))
    assert (MAIN, "7", "verify-props:shd") in t and (MAIN, "7", "pPr") not in t


# ---- a wrong output per rule ---------------------------------------------------------------------------------------

def _caught(src, plan, out, part, fn, needle=None):
    bad = mutate(out, part, fn)
    rec = run_suite(src, bad, plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec), rec.to_markdown()
    if needle:
        assert needle in v10(rec), v10(rec)


def test_wrong_outputs_are_caught(src):
    plan, out, rec = bs(src, NOTES, {"borders": {"top": {}}, "shading": {"fill": "F2F2F2"}, "author": AUTHOR})
    assert rec.passed
    # a wrong side attribute
    _caught(src, plan, out, MAIN, lambda r: para(r, "NOTE TO").find(W + "pPr/" + W + "pBdr/" + W + "top").set(
        W + "sz", "6"), "w:pBdr is")
    # a missing nil: the style's left border shows again
    def no_nil(r):
        b = para(r, "NOTE child").find(W + "pPr/" + W + "pBdr")
        b.remove(b.find(W + "left"))
    _caught(src, plan, out, MAIN, no_nil, "left border shows")
    # another pPr child changed on a changed paragraph
    _caught(src, plan, out, MAIN, lambda r: para(r, "NOTE TO").find(W + "pPr").insert(1, el("keepNext")),
            "other than")
    # a wrong shading attribute
    _caught(src, plan, out, MAIN, lambda r: para(r, "NOTE child").find(W + "pPr/" + W + "shd").set(W + "fill", "F2F2F3"),
            "w:shd is")
    # the tracked snapshot is not the source pPr
    def bad_snapshot(r):
        chg = para(r, "NOTE TO").find(W + "pPr/" + W + "pPrChange")
        chg[0].append(el("keepNext"))
    _caught(src, plan, out, MAIN, bad_snapshot, "snapshot")
    # a paragraph outside the selection gets a border
    _caught(src, plan, out, MAIN, lambda r: para(r, "1.01").find(W + "pPr").insert(1, etree.fromstring(
        f'<w:pBdr xmlns:w="{W[1:-1]}"><w:top w:val="single"/></w:pBdr>')), "unpredicted change")
    # the element twice
    _caught(src, plan, out, MAIN, lambda r: para(r, "NOTE TO").find(W + "pPr").insert(
        2, el("shd", val="clear", color="auto", fill="F2F2F2")), "more")


def test_a_no_op_change_on_an_already_paragraph_is_caught(src):
    plan, out, rec = bs(src, {"paragraphs": {"text_regex": "^Coordinate"}}, {"shading": {"fill": "FFFF00"}})
    assert rec.passed and out == src
    _caught(src, plan, out, MAIN, lambda r: para(r, "Coordinate").find(W + "pPr/" + W + "shd").set(W + "fill", "FFFF01"))


def test_wrong_style_outputs_are_caught(src):
    plan, out, rec = bs(src, {"styles": {"ids": ["ArchNote"]}}, {"borders": {"top": {}}, "shading": "none"})
    assert rec.passed, rec.to_markdown()
    assert shd(style(root_of(out, STYLES), "ArchNote").find(W + "pPr")) is None     # nothing below shows shading
    # another child of the style changed
    _caught(src, plan, out, STYLES, lambda r: style(r, "ArchNote").find(W + "pPr").insert(0, el("keepNext")),
            "other than")
    _caught(src, plan, out, STYLES, lambda r: style(r, "ArchNote").find(W + "rPr").append(el("b")),
            "unpredicted change")
    # the box not the asked one
    _caught(src, plan, out, STYLES, lambda r: style(r, "ArchNote").find(W + "pPr/" + W + "pBdr").append(
        el("bottom", val="single")), "bottom border shows")
    # the shading left in
    _caught(src, plan, out, STYLES, lambda r: style(r, "ArchNote").find(W + "pPr").append(
        el("shd", val="clear", color="auto", fill="D9D9D9")), "still shows")


# ---- wrong implementations shared by plan and apply ----------------------------------------------------------------

def test_a_shared_level_reader_that_forgets_the_document_defaults_is_caught(src, monkeypatch):
    data = mutate(src, STYLES, lambda r: r.find(W + "docDefaults/" + W + "pPrDefault/" + W + "pPr").insert(
        0, el("shd", val="clear", color="auto", fill="EEEEEE")))
    real = bs_module.paragraph_levels
    monkeypatch.setattr(bs_module, "paragraph_levels", lambda res, p: real(res, p)[1:])
    plan, out, rec = bs(data, {"paragraphs": {"text_regex": "^Coordinate"}}, {"shading": "none"})
    assert para(root_of(out), "Coordinate").find(W + "pPr/" + W + "shd") is None      # the defaults' shading shows
    assert "V10" in failed(rec) and "w:shd is None" in v10(rec) and "still shows" in v10(rec)


def test_a_shared_level_reader_that_forgets_the_table_style_is_caught(src, monkeypatch):
    data = mutate(src, STYLES, lambda r: style(r, "ArchTable").find(W + "pPr").insert(0, etree.fromstring(
        f'<w:pBdr xmlns:w="{W[1:-1]}"><w:left w:val="single" w:sz="4" w:space="4" w:color="auto"/></w:pBdr>')))

    def no_table(res, p):
        return [("docDefaults", res.dd_ppr)] + [(f"style:{st.get(W + 'styleId')}", st.find(W + "pPr"))
                                                for st in res.chain(res._pstyle(p))]
    monkeypatch.setattr(bs_module, "paragraph_levels", no_table)
    plan, out, rec = bs(data, {"paragraphs": {"text_regex": "^Cell item"}}, {"borders": {"top": {}}})
    assert box(para(root_of(out), "Cell item").find(W + "pPr")) == [("top", SINGLE)]
    assert "V10" in failed(rec) and "left border shows" in v10(rec)


def test_a_shared_box_builder_that_drops_nil_sides_is_caught(src, monkeypatch):
    real = bs_module.expected_borders
    monkeypatch.setattr(bs_module, "expected_borders",
                        lambda asked, lower: [(s, a) for s, a in real(asked, lower) if a != [("val", "nil")]])
    plan, out, rec = bs(src, NOTES, {"borders": {"top": {}}, "author": AUTHOR})
    assert box(para(root_of(out), "NOTE TO").find(W + "pPr")) == [("top", SINGLE)]
    assert "V10" in failed(rec) and "w:pBdr is" in v10(rec)


def test_a_shared_already_rule_that_ignores_attributes_is_caught(src, monkeypatch):
    def tags_only(a, b):
        return (a is None) == (b is None) and (a is None or [c.tag for c in a] == [c.tag for c in b])
    monkeypatch.setattr(bs_module, "same_element", tags_only)
    plan, out, rec = bs(src, {"paragraphs": {"text_regex": "^Coordinate"}},
                        {"borders": {"top": {}, "bottom": {}}, "author": AUTHOR})
    assert plan.property_assertions[0]["expected_paragraphs"] == 0          # double ≠ single: wrongly "already"
    assert "V10" in failed(rec) and "plan predicted 0" in v10(rec) and "top border shows" in v10(rec)


def test_a_shared_visibility_rule_that_counts_clear_auto_as_shading_is_caught(src, monkeypatch):
    data = mutate(src, STYLES, lambda r: style(r, "Normal").append(etree.fromstring(
        f'<w:pPr xmlns:w="{W[1:-1]}"><w:shd w:val="clear" w:color="auto" w:fill="auto"/></w:pPr>')))
    monkeypatch.setattr(bs_module, "shading_visible", lambda e: e is not None)
    plan, out, rec = bs(data, {"paragraphs": {"text_regex": "^Coordinate"}}, {"shading": "none", "author": AUTHOR})
    assert shd(para(root_of(out), "Coordinate").find(W + "pPr")) == {"val": "clear", "color": "auto", "fill": "auto"}
    assert "V10" in failed(rec) and "expected None" in v10(rec)


def test_a_shared_style_level_reader_that_includes_the_style_itself_is_caught(src, monkeypatch):
    real = bs_module.style_levels
    monkeypatch.setattr(bs_module, "style_levels",
                        lambda res, sid: real(res, sid) + [(f"style:{sid}", res.styles[sid].find(W + "pPr"))])
    plan, out, rec = bs(src, {"styles": {"ids": ["ArchNote"]}}, {"borders": {"top": {}}})
    assert box(style(root_of(out, STYLES), "ArchNote").find(W + "pPr"))[1] == ("left", NIL)
    assert "V10" in failed(rec) and "style ArchNote: w:pBdr is" in v10(rec)


def test_a_shared_default_border_size_is_caught(src, monkeypatch):
    """The assertion carries the user's own words (a side without 'size'); the verifier applies its own default."""
    real = bs_module.clean_params

    def eight_by_default(params):
        borders, shading = real(params)
        if borders:
            borders = {s: [(k, "8" if k == "sz" and "size" not in params["borders"][s] else v) for k, v in a]
                       for s, a in borders.items()}
        return borders, shading
    monkeypatch.setattr(bs_module, "clean_params", eight_by_default)
    plan, out, rec = bs(src, {"paragraphs": {"text_regex": "^Coordinate"}}, {"borders": {"top": {}}, "author": AUTHOR})
    assert box(para(root_of(out), "Coordinate").find(W + "pPr")) == [("top", {**SINGLE, "sz": "8"})]
    assert "V10" in failed(rec) and "top border shows" in v10(rec)


def test_a_shared_default_scope_is_caught(src, monkeypatch):
    real = bs_module.scope_parts
    monkeypatch.setattr(bs_module, "scope_parts",
                        lambda pkg, scope: real(pkg, ["document", "footers"] if scope == ["document"] else scope))
    plan, out, rec = bs(src, {}, {"shading": {"fill": "EEEEEE"}})
    assert "V10" in failed(rec) and "scope resolves to" in v10(rec)


# ---- the two readings agree ----------------------------------------------------------------------------------------

def _empty_pstyles(src):
    """Normal shows a box and yellow shading; "PART 2 - PRODUCTS" names an empty paragraph style (w:val="") and "Lines
    spacing" a w:pStyle without w:val — both name NO style (template.Eff / effective.Resolver), not the default."""
    def styles(root):
        style(root, "Normal").append(etree.fromstring(
            f'<w:pPr xmlns:w="{W[1:-1]}"><w:pBdr><w:top w:val="single"/></w:pBdr>'
            f'<w:shd w:val="clear" w:color="auto" w:fill="FFFF00"/></w:pPr>'))

    def doc(root):
        para(root, "PART 2").insert(0, etree.fromstring(f'<w:pPr xmlns:w="{W[1:-1]}"><w:pStyle w:val=""/></w:pPr>'))
        para(root, "Lines spacing").find(W + "pPr/" + W + "pStyle").attrib.clear()
    return mutate(mutate(src, STYLES, styles), MAIN, doc)


def test_an_empty_paragraph_style_names_no_style_on_both_sides(src):
    """A4: an empty (or valueless) w:pStyle names no style — the levels below the paragraph are docDefaults alone — on
    the transform side (effective.Resolver) and the verifier's (template.Eff's reading); the verifier used to take the
    default paragraph style there and failed a correct output."""
    data = _empty_pstyles(src)
    plan, out, rec = bs(data, {"paragraphs": {"text_regex": "^(PART 2|Lines spacing|1\\.01)"}},
                        {"borders": "none", "shading": "none", "tracked": False})
    assert rec.passed, rec.to_markdown()
    a = plan.property_assertions[0]
    changed = {bi for bi, _ in a["changed"][MAIN]}
    root = root_of(data)
    pars = paragraphs(root)
    assert pars.index(para(root, "1.01")) in changed                 # Normal's box and shading cancelled there …
    assert not changed & {pars.index(para(root, "PART 2")), pars.index(para(root, "Lines spacing"))}   # … not here


def test_transform_and_verifier_decide_alike_on_every_paragraph_and_style(src):
    """The expected elements of both sides, for a spread of asks, on every paragraph of every text part and every
    paragraph style of the fixture (and of a variant whose table style and document defaults show borders/shading)."""
    from spec_surgeon.ops.effective import Resolver
    from spec_surgeon.ops.selectors import StyleSheet
    from spec_surgeon.verify.props import Styles, sig
    from spec_surgeon.verify.reader import VPackage

    def lower(root):
        style(root, "ArchTable").find(W + "pPr").insert(0, etree.fromstring(
            f'<w:pBdr xmlns:w="{W[1:-1]}"><w:left w:val="single"/><w:between w:val="none"/></w:pBdr>'))
        root.find(W + "docDefaults/" + W + "pPrDefault/" + W + "pPr").insert(0, el("shd", val="pct5", fill="auto"))
    asks = [{"borders": "none"}, {"shading": "none"}, {"borders": BOX, "shading": {"fill": "D9D9D9"}},
            {"borders": {"bar": {"style": "wave", "color": "00ff00"}}, "shading": {"fill": "auto", "pattern": "solid"}}]
    for data in (src, mutate(src, STYLES, lower), _empty_pstyles(src)):
        pkg, vpkg = DocxPackage(data), VPackage(data)
        sheet = StyleSheet(pkg)
        res, vst = Resolver(sheet, {}), Styles(vpkg.xml(STYLES))
        for params in asks:
            borders, shading = bs_module.clean_params(params)
            vb, vs = vborders._v_asked(params)
            for part in pkg.text_parts():
                vpars = list(vpkg.xml(part).iter(W + "p"))
                for p, vp in zip(paragraphs(pkg.xml(part)), vpars):
                    t = bs_module.decide(p.find(W + "pPr"), [x for _, x in bs_module.paragraph_levels(res, p)],
                                         borders, shading)
                    v = vborders._v_gets(vp.find(W + "pPr"), vborders._v_below_paragraph(vp, vst), vb, vs)
                    assert sorted(t) == sorted(v)
                    for name in t:
                        built = bs_module.build_pbdr([(s, [tuple(x) for x in a]) for s, a in t[name] or []]) \
                            if name == "pBdr" else bs_module.build_shd([tuple(x) for x in t[name]] if t[name] else None)
                        assert sig(built) == sig(v[name]), (part, params, name)
            for sid, st in sheet.styles.items():
                if st.get(W + "type", "paragraph") != "paragraph":
                    continue
                t = bs_module.decide(st.find(W + "pPr"), [x for _, x in bs_module.style_levels(res, sid)], borders,
                                     shading)
                v = vborders._v_gets(vst.by_id[sid].find(W + "pPr"), vborders._v_below_style(vst.by_id[sid], vst), vb, vs)
                assert sorted(t) == sorted(v), (sid, params)


# ---- conflicts -----------------------------------------------------------------------------------------------------

def _spec(*ops):
    return {"request": "r", "summary": "s", "mode": "formatting", "ops": list(ops)}


def _plan_and_verify(src, spec):
    plan, frags = plan_spec(DocxPackage(src), spec)
    work = DocxPackage(src).copy()
    for f in frags:
        get_op(f.op).apply(work, f)
    rec = run_suite(src, work.output_bytes(), plan.to_dict(), "src.docx", "out.docx")
    assert rec.passed, rec.to_markdown()
    return plan


BOX_NOTES = {"op": "set_borders_shading", "select": NOTES, "params": {"borders": {"top": {}}}}


@pytest.mark.parametrize("other", [
    {"op": "set_paragraph_props", "select": NOTES, "params": {"spacing": {"after": 120}}},
    {"op": "set_properties", "select": NOTES, "params": {"paragraph": {"keepNext": True}}},
    {"op": "set_tab_stops", "select": NOTES, "params": {"set": [{"pos": 720}]}},
    {"op": "set_borders_shading", "select": {"styles": {"ids": ["ArchNote"]}}, "params": {"shading": "none"}},
    {"op": "set_properties", "select": {"styles": {"ids": ["ArchNote"]}}, "params": {"paragraph": {"keepLines": True}}},
    {"op": "set_paragraph_props", "select": {"styles": {"ids": ["Normal"]}}, "params": {"jc": "both"}},
])
def test_paragraph_property_ops_on_the_same_paragraphs_or_their_styles_conflict(src, other):
    with pytest.raises(JobException) as e:
        plan_spec(DocxPackage(src), _spec(BOX_NOTES, other))
    assert e.value.code == "conflict"


def test_other_paragraphs_and_other_families_compose(src):
    title = {"paragraphs": {"text_regex": "^WET-PIPE"}}
    _plan_and_verify(src, _spec(BOX_NOTES, {"op": "set_paragraph_props", "select": title,
                                            "params": {"spacing": {"after": 120}}}))
    _plan_and_verify(src, _spec(BOX_NOTES, {"op": "set_properties", "select": NOTES, "params": {"run": {"b": True}}}))
    _plan_and_verify(src, _spec({"op": "set_borders_shading", "select": {"styles": {"ids": ["ArchNote"]}},
                                 "params": {"shading": "none"}},
                                {"op": "set_properties", "select": {"styles": {"ids": ["ArchNote"]}},
                                 "params": {"run": {"b": True}}}))


def test_two_style_ops_on_one_style_conflict(src):
    with pytest.raises(JobException) as e:
        plan_spec(DocxPackage(src), _spec(
            {"op": "set_borders_shading", "select": {"styles": {"ids": ["ArchNote"]}}, "params": {"shading": "none"}},
            {"op": "set_properties", "select": {"styles": {"ids": ["ArchNote"]}}, "params": {"paragraph": {"keepNext": True}}}))
    assert e.value.code == "conflict"


def test_a_second_box_op_on_a_paragraph_the_first_leaves_alone_conflicts(src):
    first = {"op": "set_borders_shading", "select": {"paragraphs": {"text_regex": "^Coordinate"}},
             "params": {"shading": {"fill": "FFFF00"}}}
    with pytest.raises(JobException) as e:
        plan_spec(DocxPackage(src), _spec(first, {"op": "set_borders_shading",
                                                  "select": {"paragraphs": {"text_regex": "^Coord"}},
                                                  "params": {"shading": "none"}}))
    assert e.value.code == "conflict"


def test_without_the_style_reads_the_post_condition_still_catches_a_stale_box(src, monkeypatch):
    """Plant the old behaviour (no reads): a style change beside a direct box plans, and the box the paragraph shows is
    no longer the asked one — V10's post-condition on the output fails it instead of passing it."""
    cls = type(get_op("set_borders_shading"))
    original = cls.plan

    def plan(self, pkg, select, params):
        frag = original(self, pkg, select, params)
        frag.touches = [t for t in frag.touches if not verify_family(t[2])[0]]
        return frag
    monkeypatch.setattr(cls, "plan", plan)
    pl, out, rec = run_ops(src, [("set_borders_shading", NOTES, {"borders": {"top": {}}, "author": AUTHOR}),
                                 ("set_borders_shading", {"styles": {"ids": ["ArchNote"]}},
                                  {"borders": {"top": {}, "between": {}}})])
    assert "V10" in failed(rec) and "between border shows" in v10(rec)


# ---- parameters and refusals ---------------------------------------------------------------------------------------

def test_parameters_are_checked(src):
    pkg = DocxPackage(src)
    for params in ({}, {"borders": {}}, {"borders": "box"}, {"borders": {"middle": {}}}, {"borders": {"top": "single"}},
                   {"borders": {"top": {"style": "hairline"}}}, {"borders": {"top": {"size": 1}}},
                   {"borders": {"top": {"size": 97}}}, {"borders": {"top": {"size": True}}},
                   {"borders": {"top": {"space": 32}}}, {"borders": {"top": {"color": "blue"}}},
                   {"borders": {"top": {"width": 4}}}, {"shading": {}}, {"shading": {"pattern": "solid"}},
                   {"shading": {"fill": "grey"}}, {"shading": {"fill": "auto", "pattern": "pct11"}},
                   {"shading": {"fill": "auto", "color": "12345"}}, {"shading": "clear"},
                   {"shading": {"fill": "auto", "x": 1}}, {"borders": "none", "tracked": "yes"},
                   {"borders": "none", "colour": "auto"}):
        with pytest.raises(JobException) as e:
            get_op("set_borders_shading").plan(pkg, {}, params)
        assert e.value.code == "bad-params", params
    for select in ({"runs": "all"}, {"styles": {"ids": ["ArchNote"]}, "paragraphs": "all"}, {"styles": ["ArchNote"]},
                   {"styles": {"ids": []}}):
        with pytest.raises(JobException) as e:
            get_op("set_borders_shading").plan(pkg, select, {"borders": "none"})
        assert e.value.code == "bad-params", select
    with pytest.raises(JobException) as e:
        get_op("set_borders_shading").plan(pkg, {"styles": {"ids": ["ArchNote"]}}, {"borders": "none", "tracked": True})
    assert e.value.code == "bad-params"
    with pytest.raises(JobException) as e:                                      # a character style
        get_op("set_borders_shading").plan(pkg, {"styles": {"ids": ["ArchStrong"]}}, {"borders": "none"})
    assert e.value.code == "style-mismatch"                                   # this file's ArchStrong: file-specific
    with pytest.raises(JobException) as e:
        get_op("set_borders_shading").plan(pkg, {"styles": {"ids": ["NoSuchStyle"]}}, {"borders": "none"})
    assert e.value.code == "not-found"


def test_the_spec_schema_takes_the_op(src):
    from spec_surgeon.editspec import SpecError, validate_spec
    validate_spec(_spec({"op": "set_borders_shading", "select": NOTES,
                         "params": {"borders": {"top": {"style": "double", "size": 6}}, "shading": "none"}}))
    validate_spec(_spec({"op": "set_borders_shading", "select": {"styles": {"ids": ["ArchNote"]}},
                         "params": {"borders": "none"}}))
    with pytest.raises(SpecError):
        validate_spec(_spec({"op": "set_borders_shading", "select": NOTES, "params": {"borders": {"top": {"weight": 4}}}}))


def test_a_spec_runs_through_the_batch_pipeline(src, tmp_path):
    """Plan, apply, verify, write and deliver in a worker, from a spec (the plan and its transform data go through JSON)."""
    from spec_surgeon.batch import run_batch
    spec = _spec({"op": "set_borders_shading", "select": NOTES,
                  "params": {"borders": {"top": {"style": "double", "size": 6}}, "shading": "none"}},
                 {"op": "set_borders_shading", "select": {"styles": {"ids": ["Heading1"]}},
                  "params": {"borders": {"bottom": {}}}})
    path = tmp_path / "21 13 13 – WET-PIPE.docx"
    path.write_bytes(src)
    batch = run_batch([str(path)], "edit-spec", {}, tmp_path / "out", workers=1, renderer="none", do_render=False,
                      spec=spec)
    f = batch.files[0]
    assert f.status == "PASS", f.record.to_markdown() if f.record else f.exception
