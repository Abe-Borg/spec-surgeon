"""set_tab_stops and V10's ``tab_stops`` kind (built-in batch 3, item 25 — docs/BUILTIN_OPS_BATCH3_DESIGN.md §2): end to
end on the fixture (margin positions per section, inherited stops, numbered paragraphs, tracked and plain, already as
asked); a wrong OUTPUT per rule; wrong IMPLEMENTATIONS shared by plan and apply; the two section readers and the two
rules compared; conflicts; parameters; the catalog, prompt, profile, explanations and worked examples."""
from __future__ import annotations

import ast
import copy
import io
import random
import zipfile
from pathlib import Path
from typing import List, Tuple

import pytest
from lxml import etree

from make_fixtures import build, build_batch1, build_rich, build_tabs
from spec_surgeon import compiler as compiler_module
from spec_surgeon.catalog import COMMON_ASSUMPTIONS, CUSTOM_TRANSFORM_LIMITS, op_catalog
from spec_surgeon.editspec import plan_spec
from spec_surgeon.ooxml import paragraphs
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import set_tab_stops as tabs_module
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.profile import profile
from spec_surgeon.report import FAILURE_EXPLANATIONS
from spec_surgeon.verify import props as vprops
from spec_surgeon.verify import run_suite
from spec_surgeon.verify import tabs as vtabs

AUTHOR = "Abraham Borg"
ARTICLES = {"paragraphs": {"style_in": ["ArticleTabs", "ArticleChild"]}}
MAIN = "word/document.xml"


@pytest.fixture(scope="module")
def src() -> bytes:
    return build_tabs()


def run_ops(data: bytes, specs: List[Tuple[str, dict, dict]]):
    pkg = DocxPackage(data)
    frags = [get_op(o).plan(pkg, sel, par) for o, sel, par in specs]
    plan = merge_fragments(frags)
    work = pkg.copy()
    for f in frags:
        get_op(f.op).apply(work, f)
    out = work.output_bytes()
    return plan, out, run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")


def tab_op(data: bytes, select: dict, params: dict):
    return run_ops(data, [("set_tab_stops", select, params)])


def failed(rec):
    return [c.id for c in rec.checks if c.status == "FAIL"]


def v10(rec) -> str:
    c = next(c for c in rec.checks if c.id == "V10")
    return c.summary + " | " + " | ".join(c.data.get("errors", []))


def para(root, needle):
    return [p for p in root.iter(W + "p") if "".join(t.text or "" for t in p.iter(W + "t")).startswith(needle)][0]


def stops(p) -> List[Tuple[str, int, str]]:
    t = p.find(W + "pPr/" + W + "tabs")
    return [(e.get(W + "val"), int(e.get(W + "pos")), e.get(W + "leader") or "none") for e in (t if t is not None else [])]


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


def tab(val, pos, leader=None):
    t = etree.Element(W + "tab")
    t.set(W + "val", val)
    if leader:
        t.set(W + "leader", leader)
    t.set(W + "pos", str(pos))
    return t


# ---- end to end ----------------------------------------------------------------------------------------------------

def test_footer_tabs_follow_each_sections_own_margins(src):
    """'Centre tab in the middle, page number at the right margin' over both footers: section 1 (text width 9360) already
    has center 4680 / right 9360 from the Footer style; section 2's footer (8640) gets center 4320 and right 8640, and the
    style's stops are cleared with clear entries — a whole-part change, plain."""
    plan, out, rec = tab_op(src, {"scope": ["footers"]},
                            {"set": [{"pos": "center", "align": "center"}, {"pos": "right_margin", "align": "right"}],
                             "clear_others": True})
    assert rec.passed, rec.to_markdown()
    a = plan.property_assertions[0]
    assert a["changed"] == {"word/footer2.xml": [0]} and a["expected_paragraphs"] == 1
    assert plan.ops[0]["params"]["tracked"] is False and plan.geometry_reference == "none"
    assert stops(paragraphs(root_of(out, "word/footer2.xml"))[0]) == [
        ("center", 4320, "none"), ("clear", 4680, "none"), ("right", 8640, "none"), ("clear", 9360, "none")]
    assert stops(paragraphs(root_of(out, "word/footer1.xml"))[0]) == []          # already right: untouched
    assert any("right_margin = 8640" in n for n in plan.notes) and any("already as asked" in n for n in plan.notes)


def test_margin_reads_are_declared(src):
    f = get_op("set_tab_stops").plan(DocxPackage(src), {"scope": ["footers"]},
                                     {"set": [{"pos": "right_margin", "align": "right"}]})
    assert {(MAIN, "sectPr:0", "verify:pgMar"), (MAIN, "sectPr:1", "verify:pgSz"), (MAIN, "sectPr:1", "verify:hfref"),
            ("word/footer2.xml", "0", "pPr"), ("word/footer1.xml", "scope", "verify")} <= set(map(tuple, f.touches))
    plain = get_op("set_tab_stops").plan(DocxPackage(src), ARTICLES, {"set": [{"pos": 720}]})
    assert not any(t[2].startswith("verify:") for t in plain.touches)          # an explicit position reads no section


def test_a_header_shown_at_two_widths_is_refused(src):
    with pytest.raises(JobException) as e:
        tab_op(src, {"scope": ["headers"]}, {"set": [{"pos": "right_margin", "align": "right"}]})
    assert e.value.code == "tab-position" and "different" in str(e.value)
    plan, out, rec = tab_op(src, {"scope": ["headers"]}, {"set": [{"pos": 9360, "align": "right", "leader": "dot"}]})
    assert rec.passed, rec.to_markdown()                                      # an explicit position is fine


def test_body_paragraphs_measure_their_own_section(src):
    plan, out, rec = tab_op(src, {"paragraphs": {"text_regex": "^(1\\.01|Schedule)"}},
                            {"set": [{"pos": "right_margin", "align": "right", "leader": "dot"}], "author": AUTHOR})
    assert rec.passed, rec.to_markdown()
    root = root_of(out)
    assert stops(para(root, "1.01")) == [("right", 9360, "dot")]
    assert stops(para(root, "Schedule")) == [("right", 8640, "dot")]
    with pytest.raises(JobException) as e:                                     # 8640 is section 2's right margin
        tab_op(src, {"paragraphs": {"text_regex": "^Schedule"}},
               {"set": [{"pos": 8640}, {"pos": "right_margin", "align": "right"}]})
    assert e.value.code == "tab-position"
    plan, out, rec = tab_op(src, {"paragraphs": {"text_regex": "^1\\.01"}},
                            {"set": [{"pos": 8640}, {"pos": "right_margin", "align": "right"}]})
    assert rec.passed, rec.to_markdown()


def test_article_titles_end_with_exactly_one_stop_tracked(src):
    """Inherited stops are cleared with clear entries, the paragraph's own stops removed; a pPrChange per changed
    paragraph whose snapshot is the old pPr."""
    plan, out, rec = tab_op(src, ARTICLES, {"set": [{"pos": 720}], "clear_others": True, "author": AUTHOR})
    assert rec.passed, rec.to_markdown()
    assert plan.ops[0]["params"]["tracked"] is True and plan.rev_delta["pPrChange"] == 3
    root = root_of(out)
    assert stops(para(root, "1.01")) == [("clear", 1440, "none")]
    assert stops(para(root, "1.02")) == [("left", 720, "none"), ("clear", 1440, "none")]
    assert stops(para(root, "1.03")) == [("clear", 4320, "none")]
    chg = para(root, "1.02").find(W + "pPr/" + W + "pPrChange")
    assert chg.get(W + "author") == AUTHOR
    assert stops(chg) == [("clear", 720, "none"), ("right", 9360, "dot")]


def test_already_as_asked_is_left_alone(src):
    plan, out, rec = tab_op(src, {"paragraphs": {"style_in": ["ArticleTabs"]}}, {"set": [{"pos": 1440}]})
    assert rec.passed, rec.to_markdown()
    assert plan.property_assertions[0]["expected_paragraphs"] == 0 and plan.intended_parts == []
    assert not {k: v for k, v in plan.rev_delta.items() if v} and plan.geometry_reference == "source"
    assert any("already as asked, left alone: 2" in n for n in plan.notes)


def test_numbered_paragraphs_count_only_their_own_entries(src):
    """A numbered paragraph's list definition may set stops the op does not read: an explicit clear writes a clear entry
    even where no style sets a stop, and clear_others is flagged."""
    plan, out, rec = tab_op(src, {"paragraphs": {"text_regex": "^Numbered"}}, {"clear": [2160, 720], "author": AUTHOR})
    assert rec.passed, rec.to_markdown()
    root = root_of(out)
    for needle in ("Numbered", "Numbered direct"):          # the style sets no stop at either: the list might
        assert stops(para(root, needle)) == [("clear", 720, "none"), ("clear", 2160, "none")], needle
    plan, out, rec = tab_op(src, {"paragraphs": "all"}, {"clear_others": True})
    assert rec.passed, rec.to_markdown()
    assert any("2 numbered paragraph(s)" in f for f in plan.flags)
    # already: a numbered paragraph whose own entries already clear the position
    plan2, _, rec2 = tab_op(out, {"paragraphs": {"text_regex": "^Numbered direct"}}, {"clear": [2160]})
    assert rec2.passed and plan2.property_assertions[0]["expected_paragraphs"] == 0


def test_unsorted_own_stops_are_kept_and_sorted(src):
    plan, out, rec = tab_op(src, {"paragraphs": {"text_regex": "^(Unsorted|Earlier)"}},
                            {"set": [{"pos": 4320, "align": "decimal", "leader": "dot"}], "tracked": False})
    assert rec.passed, rec.to_markdown()
    root = root_of(out)
    assert stops(para(root, "Unsorted")) == [("left", 1440, "none"), ("left", 2880, "none"), ("decimal", 4320, "dot")]
    assert any("pending tracked paragraph change" in f for f in plan.flags)
    with pytest.raises(JobException) as e:                                       # tracked: Jeremy's pPrChange refuses
        tab_op(src, {"paragraphs": {"text_regex": "^Earlier"}}, {"set": [{"pos": 4320}]})
    assert e.value.code == "tracked-format"


def test_the_last_entry_going_removes_the_tab_list(src):
    plan, out, rec = tab_op(src, {"paragraphs": {"text_regex": "^Earlier"}}, {"clear_others": True, "tracked": False})
    assert rec.passed, rec.to_markdown()
    assert para(root_of(out), "Earlier").find(W + "pPr/" + W + "tabs") is None


def test_parameters_are_checked(src):
    pkg = DocxPackage(src)
    for params in ({}, {"set": []}, {"set": [{"pos": 720, "align": "middle"}]}, {"set": [{"pos": 720, "leader": "dash"}]},
                   {"set": [{"pos": "left_margin"}]}, {"set": [{"pos": 40000}]}, {"set": [{"pos": True}]},
                   {"set": [{"pos": 720}, {"pos": 720, "align": "right"}]}, {"set": [{"pos": 720}], "clear": [720]},
                   {"clear": [720, 720]}, {"clear": "720"}, {"set": [{"align": "left"}]}, {"set": [{"pos": 1, "x": 2}]},
                   {"clear_others": "yes"}):
        with pytest.raises(JobException) as e:
            get_op("set_tab_stops").plan(pkg, {}, params)
        assert e.value.code == "bad-params", params
    with pytest.raises(JobException) as e:
        get_op("set_tab_stops").plan(pkg, {"runs": "all"}, {"clear_others": True})
    assert e.value.code == "bad-params"


def test_a_malformed_tab_list_refuses(src):
    def twice(root):
        para(root, "Earlier").find(W + "pPr/" + W + "tabs").append(tab("right", 2880))
    def no_pos(root):
        para(root, "Unsorted").find(W + "pPr/" + W + "tabs").append(etree.Element(W + "tab", {W + "val": "left"}))
    for fn, needle in ((twice, "^Earlier"), (no_pos, "^Unsorted")):
        bad = mutate(src, MAIN, fn)
        with pytest.raises(JobException) as e:
            tab_op(bad, {"paragraphs": {"text_regex": needle}}, {"set": [{"pos": 720}], "tracked": False})
        assert e.value.code == "structure"


# ---- a wrong output per rule ---------------------------------------------------------------------------------------

def _caught(src, plan, out, part, fn, needle=None):
    bad = mutate(out, part, fn)
    rec = run_suite(src, bad, plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec), rec.to_markdown()
    if needle:
        assert needle in v10(rec), v10(rec)


def test_wrong_outputs_are_caught(src):
    plan, out, rec = tab_op(src, ARTICLES, {"set": [{"pos": 720}], "clear_others": True, "author": AUTHOR})
    assert rec.passed
    # a new stop at the wrong place
    _caught(src, plan, out, MAIN, lambda r: para(r, "1.02").find(W + "pPr/" + W + "tabs")[0].set(W + "pos", "721"),
            "expected")
    # a clear entry missing: the style's stop is still in effect
    _caught(src, plan, out, MAIN, lambda r: para(r, "1.01").find(W + "pPr/" + W + "tabs").remove(
        para(r, "1.01").find(W + "pPr/" + W + "tabs")[0]), "still in effect")
    # a neighbouring paragraph property changed on a changed paragraph
    _caught(src, plan, out, MAIN, lambda r: para(r, "1.03").find(W + "pPr").insert(1, etree.Element(W + "keepNext")))
    # the entries out of order
    def swap(r):
        t = para(r, "1.02").find(W + "pPr/" + W + "tabs")
        t.insert(0, t[1])
    _caught(src, plan, out, MAIN, swap, "tab entry 0")

    plan, out, rec = tab_op(src, {"paragraphs": {"text_regex": "^(Unsorted|1\\.01)"}},
                            {"set": [{"pos": 4320, "align": "decimal", "leader": "dot"}], "tracked": False})
    assert rec.passed
    # the leader dropped
    def no_leader(r):
        for e in para(r, "1.01").find(W + "pPr/" + W + "tabs"):
            e.attrib.pop(W + "leader", None)
    _caught(src, plan, out, MAIN, no_leader, "4320")
    # an untouched own stop removed
    _caught(src, plan, out, MAIN, lambda r: para(r, "Unsorted").find(W + "pPr/" + W + "tabs").remove(
        para(r, "Unsorted").find(W + "pPr/" + W + "tabs")[1]), "entr")

    # a no-op pPrChange on a paragraph that was already as asked
    plan, out, rec = tab_op(src, {"paragraphs": {"style_in": ["ArticleTabs"]}}, {"set": [{"pos": 1440}]})
    assert rec.passed

    def noop_change(r):
        ppr = para(r, "1.01").find(W + "pPr")
        chg = etree.SubElement(ppr, W + "pPrChange", {W + "id": "900", W + "author": AUTHOR})
        snap = etree.SubElement(chg, W + "pPr")
        snap.append(copy.deepcopy(ppr.find(W + "pStyle")))
    _caught(src, plan, out, MAIN, noop_change, "unpredicted change")

    # an empty tab list left behind
    plan, out, rec = tab_op(src, {"paragraphs": {"text_regex": "^Earlier"}}, {"clear_others": True, "tracked": False})
    assert rec.passed
    _caught(src, plan, out, MAIN, lambda r: para(r, "Earlier").find(W + "pPr").insert(1, etree.Element(W + "tabs")),
            "no entry should remain")


# ---- wrong implementations shared by plan and apply ----------------------------------------------------------------

def test_a_shared_rule_that_never_clears_an_inherited_stop_is_caught(src, monkeypatch):
    real = tabs_module.decide

    def no_clear_entries(stops_, clear, others, own, inherited, numbered):
        out = real(stops_, clear, others, own, inherited, numbered)
        return None if out is None else [e for e in out if not (e[0] != "keep" and e[1] == "clear")] or None
    monkeypatch.setattr(tabs_module, "decide", no_clear_entries)
    plan, out, rec = tab_op(src, ARTICLES, {"set": [{"pos": 720}], "clear_others": True, "author": AUTHOR})
    assert "V10" in failed(rec) and "still in effect" in v10(rec)


def test_a_shared_margin_that_measures_the_page_not_the_text_is_caught(src, monkeypatch):
    monkeypatch.setattr(tabs_module, "text_width", lambda sp: int(sp.find(W + "pgSz").get(W + "w")))
    plan, out, rec = tab_op(src, {"paragraphs": {"text_regex": "^1\\.01"}},
                            {"set": [{"pos": "right_margin", "align": "right"}], "author": AUTHOR})
    assert stops(para(root_of(out), "1.01"))[-1][1] == 12240
    assert "V10" in failed(rec)


def test_a_shared_section_reader_that_ignores_section_breaks_is_caught(src, monkeypatch):
    monkeypatch.setattr(tabs_module, "paragraph_sections", lambda root: [0] * len(paragraphs(root)))
    plan, out, rec = tab_op(src, {"paragraphs": {"text_regex": "^Schedule"}},
                            {"set": [{"pos": "right_margin", "align": "right"}], "author": AUTHOR})
    assert stops(para(root_of(out), "Schedule")) == [("right", 9360, "none")]
    assert "V10" in failed(rec) and "8640" in v10(rec)


def test_a_shared_rule_that_forgets_numbering_is_caught(src, monkeypatch):
    monkeypatch.setattr(tabs_module, "numbered", lambda p, sheet: False)
    plan, out, rec = tab_op(src, {"paragraphs": {"text_regex": "^Numbered"}}, {"clear": [720], "author": AUTHOR})
    assert plan.property_assertions[0]["expected_paragraphs"] == 0           # "nothing at 720" — the list may set one
    assert "V10" in failed(rec) and "plan predicted 0" in v10(rec)


def test_a_shared_rule_that_ignores_the_styles_is_caught(src, monkeypatch):
    monkeypatch.setattr(tabs_module, "inherited_stops", lambda res, p: {})
    plan, out, rec = tab_op(src, ARTICLES, {"set": [{"pos": 720}], "clear_others": True, "author": AUTHOR})
    assert "V10" in failed(rec)


def test_a_shared_already_rule_that_ignores_leaders_is_caught(src, monkeypatch):
    real = tabs_module.in_effect
    monkeypatch.setattr(tabs_module, "in_effect",
                        lambda inherited, own: {k: (v[0], "none") for k, v in real(inherited, own).items()})
    plan, out, rec = tab_op(src, {"paragraphs": {"text_regex": "^1\\.02"}},
                            {"set": [{"pos": 9360, "align": "right"}], "author": AUTHOR})
    assert plan.property_assertions[0]["expected_paragraphs"] == 0            # the dot leader stays: wrongly "already"
    assert "V10" in failed(rec) and "9360" in v10(rec)


def test_a_shared_default_alignment_is_caught(src, monkeypatch):
    """The assertion carries the user's own words (a stop without 'align'); the verifier applies its own default."""
    real = tabs_module.clean_params

    def right_by_default(params):
        stops_, clear, others = real(params)
        given = [s for s in params.get("set") or []]
        return [(pos, "right" if "align" not in g else al, le) for (pos, al, le), g in zip(stops_, given)], clear, others
    monkeypatch.setattr(tabs_module, "clean_params", right_by_default)
    plan, out, rec = tab_op(src, {"paragraphs": {"text_regex": "^Schedule"}}, {"set": [{"pos": 2160}], "author": AUTHOR})
    assert stops(para(root_of(out), "Schedule")) == [("right", 2160, "none")]
    assert "V10" in failed(rec) and "2160" in v10(rec)


def test_a_shared_default_scope_is_caught(src, monkeypatch):
    real = tabs_module.scope_parts
    monkeypatch.setattr(tabs_module, "scope_parts",
                        lambda pkg, scope: real(pkg, ["document", "footers"] if scope == ["document"] else scope))
    plan, out, rec = tab_op(src, {}, {"set": [{"pos": "right_margin", "align": "right"}], "tracked": False})
    assert "V10" in failed(rec) and "scope resolves to" in v10(rec)


# ---- the two readings agree ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("make", [build_tabs, build_rich, lambda: build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"),
                                  lambda: build("21 13 13", "T", appendix_restart=True),
                                  lambda: build_batch1(textbox=True)])
def test_transform_and_verifier_section_readers_agree(make):
    data = make()
    root = DocxPackage(data).xml(DocxPackage(data).main_part())
    t, v = tabs_module.paragraph_sections(root), vtabs._v_sections(root)
    assert t == v and len(t) == len(paragraphs(root))
    assert max(t) == len(DocxPackage.sect_prs(root)) - 1 or len(DocxPackage.sect_prs(root)) == 1


def test_transform_and_verifier_rules_agree_on_random_cases():
    rng = random.Random(25)
    pool = [-720, 0, 720, 1440, 2160, 4320, 4680, 9360]
    vals = ["left", "right", "center", "decimal", "bar", "clear", "num"]
    leaders = ["none", "dot", "hyphen"]
    for _ in range(3000):
        own = [(pos, rng.choice(vals), rng.choice(leaders), None) for pos in rng.sample(pool, rng.randint(0, 4))]
        inherited = {pos: (rng.choice(vals[:5]), rng.choice(leaders)) for pos in rng.sample(pool, rng.randint(0, 4))}
        positions = rng.sample(pool, rng.randint(0, 5))
        cut = rng.randint(0, len(positions))
        want = {pos: (rng.choice(vals[:5]), rng.choice(leaders)) for pos in positions[:cut]}
        gone, others, numbered = positions[cut:], rng.random() < 0.3, rng.random() < 0.3
        t = tabs_module.decide(want, gone, others, own, inherited, numbered)
        v = vtabs._v_decide(want, gone, others, own, inherited, numbered)
        assert (None if t is None else [tuple(e) for e in t]) == (None if v is None else [tuple(e) for e in v]), \
            (own, inherited, want, gone, others, numbered)


# ---- conflicts ---------------------------------------------------------------------------------------------------

def _spec(*ops):
    return {"request": "r", "summary": "s", "mode": "formatting", "ops": list(ops)}


def test_paragraph_property_ops_on_the_same_paragraphs_conflict(src):
    tabs_op = {"op": "set_tab_stops", "select": ARTICLES, "params": {"set": [{"pos": 720}], "clear_others": True}}
    for other in ({"op": "set_paragraph_props", "select": ARTICLES, "params": {"spacing": {"after": 120}}},
                  {"op": "set_properties", "select": ARTICLES, "params": {"paragraph": {"keepNext": True}}}):
        with pytest.raises(JobException) as e:
            plan_spec(DocxPackage(src), _spec(tabs_op, other))
        assert e.value.code == "conflict"
    # run properties on the same paragraphs are another family: one spec, verified
    plan, frags = plan_spec(DocxPackage(src), _spec(tabs_op, {"op": "set_properties", "select": ARTICLES,
                                                              "params": {"run": {"b": True}}}))
    work = DocxPackage(src).copy()
    for f in frags:
        get_op(f.op).apply(work, f)
    rec = run_suite(src, work.output_bytes(), plan.to_dict(), "src.docx", "out.docx")
    assert rec.passed, rec.to_markdown()


def test_a_margin_position_beside_a_page_setup_change_conflicts(src):
    setup = {"op": "set_page_setup", "select": {"sections": "all"}, "params": {"margins": {"left": 1800, "right": 1800}}}
    with pytest.raises(JobException) as e:
        plan_spec(DocxPackage(src), _spec({"op": "set_tab_stops", "select": {"scope": ["footers"]},
                                           "params": {"set": [{"pos": "right_margin", "align": "right"}]}}, setup))
    assert e.value.code == "conflict"
    plan, frags = plan_spec(DocxPackage(src), _spec({"op": "set_tab_stops", "select": ARTICLES,
                                                     "params": {"set": [{"pos": 720}]}}, setup))
    assert plan.ops                                                           # an explicit position reads no margin


# ---- around the op -------------------------------------------------------------------------------------------------

def test_catalog_prompt_profile_and_explanations(src):
    cat = op_catalog(examples=True)
    assert "set_tab_stops" in cat["ops"] and COMMON_ASSUMPTIONS["set_tab_stops"]
    assert "color_in" in cat["selector_grammar"]["runs"]
    assert CUSTOM_TRANSFORM_LIMITS["families"]["tab stops of paragraphs (set, clear, clear the others)"] == "tab_stops"
    assert set(CUSTOM_TRANSFORM_LIMITS["families"].values()) <= set(vprops.KINDS)
    for needle in ("set_tab_stops", "right_margin", "color_in", "after_heading_regex", "keepNext"):
        assert needle in compiler_module.SYSTEM, needle
    requests = {e["request"] for e in cat["worked_examples"]}
    assert {"Change the editor's blue text to black",
            "Start each PART on a new page and don't leave article titles stranded at the bottom of a page",
            "In the footers, put the page number tab at the right margin and the centre tab in the middle"} <= requests
    styles = {e["id"]: e for e in profile(DocxPackage(src))["styles_in_use"]}
    assert styles["ArticleTabs"]["tab_stops"] == ["left 720", "left 1440"]
    assert styles["ArticleChild"]["tab_stops"] == ["left 720", "right 4320"]
    assert "tab-position" in FAILURE_EXPLANATIONS


def test_every_refusal_code_of_set_tab_stops_is_explained():
    path = Path(tabs_module.__file__)
    codes = set()
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "JobException" and len(n.args) > 1:
            if isinstance(n.args[1], ast.Constant):
                codes.add(n.args[1].value)
            else:                                     # the one re-raise keeps the code of own_entries' refusal
                assert ast.unparse(n.args[1]) == "e.code"
    assert codes == {"bad-params", "structure", "tab-position", "tracked-format"}
    assert sorted(c for c in codes if c not in FAILURE_EXPLANATIONS) == []


@pytest.mark.parametrize("name,make", [("part-on-new-page", None), ("editors-blue-to-black", build_tabs),
                                       ("footer-right-tab", build_tabs)])
def test_the_worked_examples_plan_and_pass(name, make, tmp_path):
    from make_fixtures import build_formatted
    from spec_surgeon.batch import run_batch
    from spec_surgeon.editspec import load_spec
    spec = load_spec(Path(__file__).resolve().parent.parent / "examples" / "specs" / f"{name}.json")
    (tmp_path / "21 13 13 – SPEC.docx").write_bytes((make or build_formatted)())
    batch = run_batch([str(tmp_path / "21 13 13 – SPEC.docx")], "edit-spec", {}, tmp_path / "out", workers=1,
                      renderer="none", do_render=False, spec=spec)
    f = batch.files[0]
    assert f.status == "PASS", f.record.to_markdown() if f.record else f.exception
