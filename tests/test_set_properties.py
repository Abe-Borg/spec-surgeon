"""set_properties and V10's ``properties`` kind (docs/SET_PROPERTIES_DESIGN.md): every property family and both target
families end to end on the fixtures; a wrong OUTPUT per family; wrong IMPLEMENTATIONS shared by plan and apply; the two
effective resolvers compared; conflicts; the spec schema; the catalog, prompt, profile and failure explanations."""
from __future__ import annotations

import io
import json
import zipfile
from typing import List, Tuple

import pytest
from lxml import etree

from make_fixtures import build, build_formatted, build_rich, build_sized
from spec_surgeon import compiler as compiler_module
from spec_surgeon.catalog import CUSTOM_TRANSFORM_LIMITS, op_catalog
from spec_surgeon.editspec import SpecError, plan_spec, validate_spec
from spec_surgeon.ooxml import paragraphs
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import effective as effective_module
from spec_surgeon.ops import set_properties as set_properties_module
from spec_surgeon.ops.effective import PARA_FLAGS, RUN_TOGGLE_ELEMENTS, PropResolver
from spec_surgeon.ops.selectors import StyleSheet
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.profile import profile
from spec_surgeon.report import FAILURE_EXPLANATIONS
from spec_surgeon.verify import props as vprops
from spec_surgeon.verify import run_suite
from spec_surgeon.verify.reader import STYLES_REL, VPackage

AUTHOR = "Abraham Borg"
PART = {"paragraphs": {"style_in": ["SpecPart"]}}
TITLES = {"paragraphs": {"style_in": ["ArticleTitle"]}}


@pytest.fixture(scope="module")
def fmt() -> bytes:
    return build_formatted()


@pytest.fixture(scope="module")
def rich() -> bytes:
    return build_rich()


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


def mutate(docx: bytes, part: str, fn) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(docx))
    root = etree.fromstring(zin.read(part))
    fn(root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            z.writestr(info, serialize_xml(root) if info.filename == part else zin.read(info.filename))
    return buf.getvalue()


def doc_root(data: bytes, part: str = "word/document.xml"):
    return DocxPackage(data).xml(part)


def run_with_text(root, needle):
    return [r for r in root.iter(W + "r") if any(needle in (t.text or "") for t in r.iter(W + "t", W + "delText"))][0]


def para_with_text(root, needle):
    return [p for p in root.iter(W + "p") if needle in "".join(t.text or "" for t in p.iter(W + "t"))][0]


def rpr_locals(run) -> List[str]:
    rpr = run.find(W + "rPr")
    return [etree.QName(c).localname for c in rpr] if rpr is not None else []


def style_el(root, sid):
    return [s for s in root.findall(W + "style") if s.get(W + "styleId") == sid][0]


# ---- the three requests, end to end --------------------------------------------------------------------------------

def test_part_headings_bold_and_kept_with_next(fmt):
    """'Make every PART heading bold and keep it with the next paragraph' — targeted, hence tracked: every run of
    the SpecPart paragraphs and their marks get w:b + w:bCs, each paragraph w:keepNext; one snapshot per target."""
    plan, out, rec = run_ops(fmt, [("set_properties", PART, {"run": {"b": True}, "paragraph": {"keepNext": True},
                                                            "author": AUTHOR})])
    assert rec.passed, rec.to_markdown()
    a = plan.property_assertions[0]
    assert (a["kind"], a["target"]) == ("properties", "direct")
    assert (a["expected_runs"], a["expected_marks"], a["expected_paragraphs"]) == (6, 4, 4)
    assert plan.rev_delta["rPrChange"] == 10 and plan.rev_delta["pPrChange"] == 4
    assert plan.ops[0]["params"]["tracked"] is True and plan.geometry_reference == "none"
    root = doc_root(out)
    r1 = run_with_text(root, "PART 1")
    assert rpr_locals(r1) == ["b", "bCs", "rPrChange"]                       # the pair Word writes, then the marker
    chg = r1.find(W + "rPr/" + W + "rPrChange")
    assert chg.get(W + "author") == AUTHOR and len(chg.find(W + "rPr")) == 0
    ex = run_with_text(root, "EXECUTION")                                   # a direct w:b w:val="0" is rewritten
    assert dict(ex.find(W + "rPr/" + W + "b").attrib) == {} and ex.find(W + "rPr/" + W + "bCs") is not None
    assert ex.find(W + "rPr/" + W + "rPrChange/" + W + "rPr/" + W + "b").get(W + "val") == "0"   # the snapshot
    cs = run_with_text(root, "CLOSEOUT")                                    # the complex-script run gets both too
    assert cs.find(W + "rPr/" + W + "bCs") is not None and cs.find(W + "rPr/" + W + "cs") is not None
    p1 = para_with_text(root, "PART 1")
    ppr = p1.find(W + "pPr")
    assert ppr.find(W + "keepNext") is not None and etree.QName(ppr[-1]).localname == "pPrChange"
    assert ppr.find(W + "rPr/" + W + "b") is not None                        # the paragraph mark follows
    assert any("6 run(s), 4 paragraph mark(s) and 4 paragraph(s)" in n for n in plan.notes)


def test_already_formatted_headings_are_left_alone(rich):
    """build_rich's Heading 1 is already bold with keep-with-next (Word's default style): nothing changes, nothing is
    counted, no tracked-change marker is written — and V10 agrees on zero targets."""
    plan, out, rec = run_ops(rich, [("set_properties", {"paragraphs": {"style_in": ["Heading1"]}},
                                     {"run": {"b": True}, "paragraph": {"keepNext": True}, "author": AUTHOR})])
    assert rec.passed, rec.to_markdown()
    assert plan.intended_parts == [] and not {k: v for k, v in plan.rev_delta.items() if v}
    a = plan.property_assertions[0]
    assert (a["expected_runs"], a["expected_marks"], a["expected_paragraphs"]) == (0, 0, 0)
    assert any("already as asked" in n for n in plan.notes) and plan.geometry_reference == "source"


def test_article_titles_all_caps_and_dark_blue(fmt):
    """'Make the article titles all caps and dark blue': w:caps and w:color on every title run and mark — no letters
    retyped (a title already typed in capitals gets w:caps too); the Wingdings bullet and the w:sym run get the colour
    but never caps; the theme colour attribute goes."""
    plan, out, rec = run_ops(fmt, [("set_properties", TITLES, {"run": {"caps": True, "color": "002060"},
                                                              "author": AUTHOR})])
    assert rec.passed, rec.to_markdown()
    a = plan.property_assertions[0]
    assert (a["expected_runs"], a["expected_marks"]) == (7, 3) and a["set"]["run"]["color"] == "002060"
    root = doc_root(out)
    summary = run_with_text(root, "SUMMARY")
    assert summary.find(W + "rPr/" + W + "caps") is not None
    assert "".join(t.text for t in summary.iter(W + "t")) == "1.01SUMMARY"            # letters untouched
    see = run_with_text(root, "see list")
    assert dict(see.find(W + "rPr/" + W + "color").attrib) == {W + "val": "002060"}
    bullet = [r for r in root.iter(W + "r") if r.find(W + "rPr/" + W + "rFonts") is not None
              and r.find(W + "rPr/" + W + "rFonts").get(W + "ascii") == "Wingdings"][0]
    assert bullet.find(W + "rPr/" + W + "color") is not None and bullet.find(W + "rPr/" + W + "caps") is None
    sym = [r for r in root.iter(W + "r") if r.find(W + "sym") is not None][0]
    assert sym.find(W + "rPr/" + W + "color") is not None and sym.find(W + "rPr/" + W + "caps") is None
    assert plan.geometry_reference == "none"                                  # caps reflows


def test_italic_off_in_the_body_text_is_plain_and_minimal(fmt):
    """'Turn off italic in the body text' over the whole document is plain (§14.1) and touches only what shows italic:
    the direct-italic run and the italic-style paragraph's run and mark — explicitly off. Only the member of the pair
    that shows italic is written: w:iCs was already off everywhere, so it is left alone."""
    plan, out, rec = run_ops(fmt, [("set_properties", {"scope": ["document"], "runs": "all"}, {"run": {"i": False}})])
    assert rec.passed, rec.to_markdown()
    a = plan.property_assertions[0]
    assert (a["expected_runs"], a["expected_marks"], a["expected_paragraphs"]) == (2, 1, 0)
    assert plan.ops[0]["params"]["tracked"] is False and not {k: v for k, v in plan.rev_delta.items() if v}
    root = doc_root(out)
    direct = run_with_text(root, "per NFPA 13")
    assert direct.find(W + "rPr/" + W + "i").get(W + "val") == "0" and direct.find(W + "rPr/" + W + "iCs") is None
    styled = run_with_text(root, "italic style")
    assert rpr_locals(styled) == ["i"] and styled.find(W + "rPr/" + W + "i").get(W + "val") == "0"
    assert rpr_locals(run_with_text(root, "Provide hangers")) == []           # never italic: untouched
    assert any("already as asked, left alone" in n for n in plan.notes)


def test_underline_merges_and_paragraph_flags(fmt):
    plan, out, rec = run_ops(fmt, [("set_properties", {"paragraphs": {"text_regex": r"^C\."}},
                                    {"run": {"u": "double"}, "paragraph": {"widowControl": False, "pageBreakBefore": True},
                                     "author": AUTHOR})])
    assert rec.passed, rec.to_markdown()
    root = doc_root(out)
    red = run_with_text(root, "red underline").find(W + "rPr/" + W + "u")
    assert red.get(W + "val") == "double" and red.get(W + "color") == "FF0000"      # the underline colour kept
    assert dict(run_with_text(root, "Underlined").find(W + "rPr/" + W + "u").attrib) == {W + "val": "double"}
    ppr = para_with_text(root, "Underlined").find(W + "pPr")
    assert ppr.find(W + "widowControl").get(W + "val") == "0" and dict(ppr.find(W + "pageBreakBefore").attrib) == {}
    assert [etree.QName(c).localname for c in ppr][-1] == "pPrChange" and plan.rev_delta["pPrChange"] == 1
    # a flag already at its value — the explicit keepNext, and widowControl's default on — is not a target
    plan2, _, rec2 = run_ops(fmt, [("set_properties", {"paragraphs": {"text_regex": r"^D\."}},
                                    {"paragraph": {"keepNext": True, "widowControl": True}})])
    assert rec2.passed and plan2.property_assertions[0]["expected_paragraphs"] == 0


def test_colour_underline_and_strike_alone_keep_the_layout_reference(fmt):
    for run in ({"color": "FF0000"}, {"u": "single"}, {"strike": True}):
        plan, _, rec = run_ops(fmt, [("set_properties", TITLES, {"run": run, "author": AUTHOR})])
        assert rec.passed and plan.geometry_reference == "source", run
    plan, _, rec = run_ops(fmt, [("set_properties", TITLES, {"run": {"smallCaps": True}, "author": AUTHOR})])
    assert rec.passed and plan.geometry_reference == "none"


def test_scope_includes_the_header_paragraph_in_the_heading_style(fmt):
    plan, out, rec = run_ops(fmt, [("set_properties", {**PART, "scope": ["document", "headers"]},
                                    {"run": {"b": True}, "paragraph": {"keepNext": True}, "author": AUTHOR})])
    assert rec.passed, rec.to_markdown()
    a = plan.property_assertions[0]
    assert (a["expected_runs"], a["expected_marks"], a["expected_paragraphs"]) == (7, 5, 5)
    assert any(part.startswith("word/header") for part in plan.intended_parts)


def test_runs_inside_revisions_can_be_left_alone(rich):
    plan, out, rec = run_ops(rich, [("set_properties", {"runs": "all"}, {"run": {"i": True}, "in_revisions": "exclude"})])
    assert rec.passed, rec.to_markdown()
    root = doc_root(out)
    assert "i" not in rpr_locals(run_with_text(root, "hot-dip"))              # inside Jeremy's insertion
    assert "i" not in rpr_locals(run_with_text(root, "black steel"))          # inside Jack's deletion
    assert "i" in rpr_locals(run_with_text(root, "Schedule 40"))


def test_tracked_change_on_a_run_with_an_older_snapshot_refuses_and_plain_flags(fmt):
    sel = {"paragraphs": {"text_regex": r"^E\."}}
    with pytest.raises(JobException) as e:
        get_op("set_properties").plan(DocxPackage(fmt), sel, {"run": {"i": True}, "author": AUTHOR})
    assert e.value.code == "tracked-format"
    plan, out, rec = run_ops(fmt, [("set_properties", sel, {"run": {"i": True}, "tracked": False})])
    assert rec.passed and any("already carried a pending tracked formatting change" in f for f in plan.flags)


# ---- style definitions as the target ------------------------------------------------------------------------------

def test_style_target_colour_and_keep_next(fmt):
    """'Make the PART headings dark blue and keep them with the next paragraph' as a change of the SpecPart style:
    styles.xml only, untracked; a style based on it inherits the change, one with its own colour keeps it (noted)."""
    plan, out, rec = run_ops(fmt, [("set_properties", {"styles": {"ids": ["SpecPart"]}},
                                    {"run": {"color": "002060"}, "paragraph": {"keepNext": True}})])
    assert rec.passed, rec.to_markdown()
    assert plan.intended_parts == ["word/styles.xml"] and not {k: v for k, v in plan.rev_delta.items() if v}
    a = plan.property_assertions[0]
    assert (a["target"], a["expected_ids"]) == ("styles", ["SpecPart"])
    st = style_el(doc_root(out, "word/styles.xml"), "SpecPart")
    assert dict(st.find(W + "rPr/" + W + "color").attrib) == {W + "val": "002060"}
    assert st.find(W + "pPr/" + W + "keepNext") is not None
    assert any("SpecSubPlain (based on SpecPart) inherits the change to color" in n for n in plan.notes)
    assert any("SpecSub keeps its own color over SpecPart" in n for n in plan.notes)
    assert style_el(doc_root(out, "word/styles.xml"), "SpecSub").find(W + "rPr/" + W + "color").get(W + "val") == "FF0000"
    assert plan.geometry_reference == "none"                                   # keepNext moves paragraphs
    plan2, _, rec2 = run_ops(fmt, [("set_properties", {"styles": {"ids": ["SpecPart"]}}, {"run": {"color": "002060"}})])
    assert rec2.passed and plan2.geometry_reference == "source"


def test_style_target_toggle_and_character_style(fmt):
    plan, out, rec = run_ops(fmt, [("set_properties", {"styles": {"ids": ["SpecNoteItalic"]}}, {"run": {"i": False}})])
    assert rec.passed, rec.to_markdown()
    st = style_el(doc_root(out, "word/styles.xml"), "SpecNoteItalic")
    assert st.find(W + "rPr/" + W + "i").get(W + "val") == "0" and st.find(W + "rPr/" + W + "iCs").get(W + "val") == "0"
    plan, out, rec = run_ops(fmt, [("set_properties", {"styles": {"ids": ["SpecStrong"]}}, {"run": {"u": "single"}})])
    assert rec.passed, rec.to_markdown()
    assert style_el(doc_root(out, "word/styles.xml"), "SpecStrong").find(W + "rPr/" + W + "u").get(W + "val") == "single"


def test_style_target_refuses_what_it_cannot_make_certain(fmt):
    pkg = DocxPackage(fmt)
    op = get_op("set_properties")
    with pytest.raises(JobException) as e:        # a SpecStrong run would have bold at two style levels
        op.plan(pkg, {"styles": {"ids": ["SpecPart"]}}, {"run": {"b": True}})
    assert e.value.code == "toggle-ambiguous" and "(strong)" not in e.value.reason and "+1 more" in e.value.reason
    assert any("direct formatting" in p and "EXECUTION" not in p for p in e.value.details["problems"])
    with pytest.raises(JobException) as e:        # the Wingdings bullet and the w:sym run would be case-mapped
        op.plan(pkg, {"styles": {"ids": ["ArticleTitle"]}}, {"run": {"caps": True}})
    assert e.value.code == "conflict-property" and "symbol run" in e.value.reason
    for sel, params, code in (
        ({"styles": {"ids": ["SpecPart"]}}, {"run": {"b": True}, "tracked": True}, "bad-params"),
        ({"styles": {"ids": ["SpecStrong"]}}, {"paragraph": {"keepNext": True}}, "bad-params"),
        ({"styles": {"ids": ["TableGrid"]}}, {"run": {"b": True}}, "bad-params"),
        ({"styles": {"ids": ["Nope"]}}, {"run": {"b": True}}, "not-found"),
        ({"styles": "all"}, {"run": {"b": True}}, "bad-params"),
        ({"styles": {"ids": ["SpecPart"]}, "paragraphs": "all"}, {"run": {"b": True}}, "bad-params"),
        ({"styles": {"ids": ["SpecPart"]}}, {"run": {"b": True}, "include_paragraph_marks": False}, "bad-params"),
    ):
        with pytest.raises(JobException) as e:
            op.plan(pkg, sel, params)
        assert e.value.code == code, (sel, params)


# ---- planted OUTPUT defects: V10 alone catches each -------------------------------------------------------------------

def test_wrong_outputs_are_caught(fmt, rich):
    plan, out, rec = run_ops(fmt, [("set_properties", PART, {"run": {"b": True}, "paragraph": {"keepNext": True},
                                                            "author": AUTHOR})])
    assert rec.passed

    def bcs_left_off(root):                    # the complex-script run would stay regular in Word
        rpr = run_with_text(root, "CLOSEOUT").find(W + "rPr"); rpr.remove(rpr.find(W + "bCs"))

    def val_one(root):                         # not the canonical on form
        run_with_text(root, "PART 1").find(W + "rPr/" + W + "b").set(W + "val", "1")

    def neighbour_bolded(root):                # a run outside the selection
        rpr = etree.Element(W + "rPr"); etree.SubElement(rpr, W + "b"); run_with_text(root, "SUMMARY").insert(0, rpr)

    def keep_next_missing(root):
        ppr = para_with_text(root, "PART 2").find(W + "pPr"); ppr.remove(ppr.find(W + "keepNext"))

    def wrong_snapshot(root):                  # reject-all would make the heading italic
        etree.SubElement(run_with_text(root, "PART 1").find(W + "rPr/" + W + "rPrChange/" + W + "rPr"), W + "i")

    def extra_property(root):                  # bold asked, italic slipped in
        rpr = run_with_text(root, "PART 4").find(W + "rPr"); rpr.insert(2, etree.Element(W + "i"))

    for defect in (bcs_left_off, val_one, neighbour_bolded, keep_next_missing, wrong_snapshot, extra_property):
        rec2 = run_suite(fmt, mutate(out, "word/document.xml", defect), plan.to_dict(), "s", "o")
        assert "V10" in failed(rec2), defect.__name__

    plan, out, rec = run_ops(fmt, [("set_properties", TITLES, {"run": {"caps": True, "color": "002060"}, "author": AUTHOR})])

    def theme_left(root):                      # Word would still show the theme colour
        run_with_text(root, "see list").find(W + "rPr/" + W + "color").set(W + "themeColor", "text2")

    def bullet_capitalised(root):              # the Wingdings glyph would change
        rpr = [r for r in root.iter(W + "r") if r.find(W + "rPr/" + W + "rFonts") is not None
               and r.find(W + "rPr/" + W + "rFonts").get(W + "ascii") == "Wingdings"][0].find(W + "rPr")
        rpr.insert(1, etree.Element(W + "caps"))

    for defect in (theme_left, bullet_capitalised):
        rec2 = run_suite(fmt, mutate(out, "word/document.xml", defect), plan.to_dict(), "s", "o")
        assert "V10" in failed(rec2), defect.__name__

    plan, out, rec = run_ops(fmt, [("set_properties", {"paragraphs": {"text_regex": r"^C\."}}, {"run": {"u": "double"}, "author": AUTHOR})])

    def underline_colour_dropped(root):
        del run_with_text(root, "red underline").find(W + "rPr/" + W + "u").attrib[W + "color"]
    rec2 = run_suite(fmt, mutate(out, "word/document.xml", underline_colour_dropped), plan.to_dict(), "s", "o")
    assert "V10" in failed(rec2)

    plan, out, rec = run_ops(fmt, [("set_properties", {"styles": {"ids": ["SpecPart"]}},
                                    {"run": {"color": "002060"}, "paragraph": {"keepNext": True}})])

    def derived_style_overwritten(root):       # SpecSub's own red must survive
        style_el(root, "SpecSub").find(W + "rPr/" + W + "color").set(W + "val", "002060")

    def keep_next_lost_in_style(root):
        ppr = style_el(root, "SpecPart").find(W + "pPr"); ppr.remove(ppr.find(W + "keepNext"))

    for defect in (derived_style_overwritten, keep_next_lost_in_style):
        rec2 = run_suite(fmt, mutate(out, "word/styles.xml", defect), plan.to_dict(), "s", "o")
        assert "V10" in failed(rec2), defect.__name__

    plan, out, rec = run_ops(rich, [("set_properties", {"paragraphs": {"style_in": ["Heading1"]}},
                                     {"run": {"b": True}, "author": AUTHOR})])
    assert rec.passed and plan.property_assertions[0]["expected_runs"] == 0

    def noop_tracked_change(root):             # "Formatted: Bold" on text that was bold already
        run = run_with_text(root, "PART 2")
        rpr = run.find(W + "rPr")
        if rpr is None:
            rpr = etree.Element(W + "rPr"); run.insert(0, rpr)
        chg = etree.SubElement(rpr, W + "rPrChange"); chg.set(W + "id", "900"); chg.set(W + "author", AUTHOR)
        chg.set(W + "date", "2026-10-16T00:00:00Z"); etree.SubElement(chg, W + "rPr")
    rec2 = run_suite(rich, mutate(out, "word/document.xml", noop_tracked_change),
                     {**plan.to_dict(), "intended_parts": ["word/document.xml"]}, "s", "o")
    assert "V10" in failed(rec2)


def test_the_kind_refuses_an_unknown_target_or_property(fmt):
    base = {"job": "x", "mode": "formatting", "params": {}, "intended_parts": [], "diffs": [], "rev_delta": {},
            "members_removed": [], "members_added": []}
    for a in ({"kind": "properties", "target": "nope", "set": {"run": {"b": True}}},
              {"kind": "properties", "target": "direct", "scope": ["document"], "parts": ["word/document.xml"],
               "set": {"run": {"bold": True}}},
              {"kind": "properties", "target": "direct", "scope": ["document"], "parts": ["word/document.xml"],
               "set": {"paragraph": {"keepWithNext": True}}}):
        rec = run_suite(fmt, fmt, {**base, "property_assertions": [a]}, "s", "o")
        assert failed(rec) == ["V10"], a


# ---- planted IMPLEMENTATION defects shared by plan and apply -----------------------------------------------------

def test_pair_expansion_bug_shared_by_plan_and_apply_is_caught(fmt, monkeypatch):
    """The transform forgets the complex-script twin everywhere (plan and apply agree); V10 expands the pair itself."""
    monkeypatch.setattr(effective_module, "PROP_PAIRS", {**effective_module.PROP_PAIRS, "b": ("b",)})
    plan, out, rec = run_ops(fmt, [("set_properties", PART, {"run": {"b": True}, "author": AUTHOR})])
    assert "V10" in failed(rec)
    assert any("bCs" in e for e in rec.get("V10").data["errors"])


def test_already_rule_bug_shared_by_plan_and_apply_is_caught(rich, monkeypatch):
    """The transform believes nothing is already bold and formats every heading run (with a no-op tracked change
    each); V10's own resolver finds nothing to change."""
    monkeypatch.setattr(effective_module.PropResolver, "gets_run", lambda self, rpr, p, asked, symbol: dict(asked))
    plan, out, rec = run_ops(rich, [("set_properties", {"paragraphs": {"style_in": ["Heading1"]}},
                                     {"run": {"b": True}, "author": AUTHOR})])
    assert plan.property_assertions[0]["expected_runs"] > 0
    assert "V10" in failed(rec) and "predicted" in rec.get("V10").summary


def test_symbol_rule_bug_shared_by_plan_and_apply_is_caught(fmt, monkeypatch):
    """The transform's symbol detection is broken, so the Wingdings bullet and the w:sym run get w:caps (and the plan
    predicts it); V10 excludes symbol runs from caps on its own."""
    monkeypatch.setattr(set_properties_module, "is_symbol_rpr", lambda *a, **k: False)
    plan, out, rec = run_ops(fmt, [("set_properties", TITLES, {"run": {"caps": True}, "skip_sym_runs": False,
                                                              "author": AUTHOR})])
    assert "V10" in failed(rec)


def test_certainty_check_bug_is_caught_by_the_post_condition(fmt, monkeypatch):
    """The transform's resolver never reports an ambiguous toggle or a direct override, so the style change proceeds;
    V10 resolves the output itself: the SpecStrong run is ambiguous and 'EXECUTION' keeps its own value."""
    orig = effective_module.PropResolver._resolve

    def lenient(self, levels, direct, element):
        val, prov = orig(self, levels, direct, element)
        if element in ("b", "bCs") and prov in ("ambiguous", "direct"):
            return True, "style:SpecPart"
        return val, prov
    monkeypatch.setattr(effective_module.PropResolver, "_resolve", lenient)
    plan, out, rec = run_ops(fmt, [("set_properties", {"styles": {"ids": ["SpecPart"]}}, {"run": {"b": True}})])
    errors = rec.get("V10").data["errors"]
    assert "V10" in failed(rec)
    assert any("several style levels" in e for e in errors) and any("overrides style SpecPart" in e for e in errors)


def test_on_off_writer_bug_is_caught(fmt, monkeypatch):
    good = set_properties_module.write_element

    def writes_val_one(container, element, value, order):
        good(container, element, value, order)
        if element in RUN_TOGGLE_ELEMENTS and value:
            container.find(W + element).set(W + "val", "1")
    monkeypatch.setattr(set_properties_module, "write_element", writes_val_one)
    plan, out, rec = run_ops(fmt, [("set_properties", PART, {"run": {"b": True}, "author": AUTHOR})])
    assert "V10" in failed(rec)


# ---- the two resolvers implement one definition --------------------------------------------------------------------

def test_transform_and_verifier_property_resolvers_agree():
    checked = 0
    for data in (build_formatted(), build_rich(), build_sized(), build("21 05 00", "A")):
        pkg, vpkg = DocxPackage(data), VPackage(data)
        res = PropResolver(StyleSheet(pkg))
        vstyles = vprops.Styles(vpkg.xml(vpkg.related(STYLES_REL)))
        for part in pkg.text_parts():
            for p, vp in zip(paragraphs(pkg.xml(part)), vpkg.xml(part).iter(W + "p")):
                for e in PARA_FLAGS:
                    assert res.para_prop(p, e) == vprops._v_para_prop(vp, vstyles, e)
                pairs = [(p.find(f"{W}pPr/{W}rPr"), vp.find(f"{W}pPr/{W}rPr"))]
                pairs += list(zip([r.find(W + "rPr") for r in p.iter(W + "r")],
                                  [r.find(W + "rPr") for r in vp.iter(W + "r")]))
                for rpr, vrpr in pairs:
                    for e in RUN_TOGGLE_ELEMENTS + ("u", "color"):
                        assert res.run_prop(rpr, p, e) == vprops._v_run_prop(vrpr, vp, vstyles, e), (part, e)
                    checked += 1
    assert checked > 200
    # the ambiguity both sides report: bold specified by SpecPart (simulated) and by SpecStrong
    pkg = DocxPackage(build_formatted())
    sim = PropResolver(StyleSheet(pkg), {("SpecPart", "b"): True})
    root = pkg.xml("word/document.xml")
    strong = run_with_text(root, "(strong)")
    assert sim.run_prop(strong.find(W + "rPr"), para_with_text(root, "PART 2"), "b") == (None, "ambiguous")
    assert sim.run_prop(run_with_text(root, "EXECUTION").find(W + "rPr"), para_with_text(root, "PART 3"), "b") == (False, "direct")
    assert sim.run_prop(run_with_text(root, "PART 1").find(W + "rPr"), para_with_text(root, "PART 1"), "b") == (True, "style:SpecPart")


# ---- rule 9, composition, the spec schema ------------------------------------------------------------------------

def test_font_and_properties_on_the_same_paragraphs_conflict_but_paragraph_props_compose(fmt):
    pkg = DocxPackage(fmt)
    frags = [get_op("set_font").plan(pkg, PART, {"font": "Arial", "author": AUTHOR}),
             get_op("set_properties").plan(pkg, PART, {"run": {"b": True}, "author": AUTHOR})]
    with pytest.raises(JobException) as e:
        merge_fragments(frags)
    assert e.value.code == "conflict"
    plan, out, rec = run_ops(fmt, [("set_properties", PART, {"run": {"b": True}, "author": AUTHOR}),
                                   ("set_paragraph_props", PART, {"spacing": {"after": 120}, "author": AUTHOR})])
    assert rec.passed, rec.to_markdown()
    assert plan.rev_delta["rPrChange"] == 10 and plan.rev_delta["pPrChange"] == 4
    frags = [get_op("set_style_font").plan(pkg, {"styles": {"ids": ["SpecPart"]}}, {"font": "Arial", "only_where_defined": False}),
             get_op("set_properties").plan(pkg, {"styles": {"ids": ["SpecPart"]}}, {"run": {"color": "002060"}})]
    with pytest.raises(JobException) as e:
        merge_fragments(frags)
    assert e.value.code == "conflict"


def test_spec_schema_refuses_misspelled_properties_and_bad_values(fmt):
    def spec(params, select=None):
        return {"request": "r", "summary": "s", "mode": "formatting",
                "ops": [{"op": "set_properties", "select": select or PART, "params": params}]}
    with pytest.raises(SpecError) as e:
        validate_spec(spec({"run": {"bold": True}}))
    assert "params.run.bold is not a known key" in str(e.value)
    with pytest.raises(SpecError):
        validate_spec(spec({"run": {"u": "squiggly"}}))
    with pytest.raises(SpecError):
        validate_spec(spec({"paragraph": {"keepNext": "yes"}}))
    validate_spec(spec({"run": {"color": "blue"}}))                               # the shape is right …
    with pytest.raises(JobException) as e:                                        # … the value is the op's to refuse
        plan_spec(DocxPackage(fmt), spec({"run": {"color": "blue"}}))
    assert e.value.code == "bad-params"
    for params in ({}, {"run": {}}, {"run": {"b": 1}}):
        with pytest.raises((SpecError, JobException)):
            plan_spec(DocxPackage(fmt), spec(params))
    plan, _ = plan_spec(DocxPackage(fmt), spec({"run": {"b": True, "color": "002060"}, "paragraph": {"keepNext": True}}))
    assert plan.property_assertions[0]["set"] == {"run": {"b": True, "color": "002060"}, "paragraph": {"keepNext": True}}
    plan, _ = plan_spec(DocxPackage(fmt), {**spec({"run": {"color": "1f3864"}}), "review": {"geometry_reference": "source"}})
    assert plan.property_assertions[0]["set"]["run"]["color"] == "1F3864"


def test_worked_example_plans_on_the_fixture(fmt):
    import pathlib
    example = json.loads((pathlib.Path(__file__).resolve().parent.parent / "examples" / "specs" / "bold-part-headings.json").read_text(encoding="utf-8"))
    validate_spec(example)
    plan, frags = plan_spec(DocxPackage(fmt), example)
    a = plan.property_assertions[0]
    assert (a["expected_runs"], a["expected_paragraphs"]) == (4, 3)                # PART 1, 3 (two runs), 4; PART 2's text has a suffix
    assert plan.mode == "formatting" and plan.geometry_reference == "none"


# ---- what Claude sees ---------------------------------------------------------------------------------------------

def test_catalog_prompt_profile_and_explanations(fmt):
    cat = op_catalog()
    assert cat["ops"]["set_properties"]["common_assumptions"]
    assert [f for f, k in CUSTOM_TRANSFORM_LIMITS["families"].items() if k == "properties"]
    assert not any("bold" in c for c in CUSTOM_TRANSFORM_LIMITS["cannot"])
    assert "set_properties" in compiler_module.system_prompt(chat=True)
    assert {e["request"] for e in cat["worked_examples"]} >= {"Make every PART 1 - GENERAL / PART 2 - PRODUCTS / PART 3 - EXECUTION heading bold and keep it with the next paragraph"}
    assert "conflict-property" in FAILURE_EXPLANATIONS and "toggle-ambiguous" in FAILURE_EXPLANATIONS
    styles = {e["id"]: e for e in profile(DocxPackage(fmt))["styles_in_use"]}
    assert styles["SpecNoteItalic"].get("italic") is True and styles["SpecSub"].get("color") == "FF0000"
    assert "italic" not in styles["Normal"] and "color" not in styles["SpecPart"]
