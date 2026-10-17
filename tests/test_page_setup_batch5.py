"""Built-in operations batch 5, items 39–40 (docs/BUILTIN_OPS_BATCH5_DESIGN.md §7): ``set_page_setup`` grows a
``different_first_page`` switch (and the header/footer distances are its ``margins.header`` / ``margins.footer``), and
its ``select.sections`` a content form ``{containing, with_tables, index_range}``.

Every rule is planted against: wrong outputs (a ``w:val="0"`` left on the title page, a title page added to a section
nobody chose, one left on), wrong implementations shared by plan and apply (the "already" rule, the writer, the section
selector with a key dropped, the paragraph-section reader), V10's re-derivation catching each; the two resolvers of the
content form are compared on every section of several fixtures for many predicates; the reads are plan-time conflicts
with ``fix_page_numbering`` and the templating op (rule 9); old-shape ``section_props`` plans verify as before.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from lxml import etree

from make_fixtures import build, build_batch1, build_rich, build_schedule, build_styles, build_tabs, patch_docx
from spec_surgeon import recovery
from spec_surgeon.catalog import COMMON_ASSUMPTIONS, SELECTOR_GRAMMAR, op_catalog
from spec_surgeon.editspec import apply_spec, load_spec, plan_spec, validate_spec
from spec_surgeon.ooxml import SECTPR_ORDER, insert_in_order
from spec_surgeon.ops import OPS, JobException, get_op, merge_fragments
from spec_surgeon.ops import selectors as selectors_module
from spec_surgeon.ops import set_page_setup as page_module
from spec_surgeon.ops import set_tab_stops as tabs_module
from spec_surgeon.ops.base import verify_family
from spec_surgeon.ops.selectors import resolve_sections
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.verify import props as vprops
from spec_surgeon.verify import run_suite
from spec_surgeon.verify.reader import STYLES_REL, VPackage

MAIN = "word/document.xml"
ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples" / "specs"
HF_SPEC = EXAMPLES / "match-template-spec-header-footer.json"

SCHEDULE = build_schedule()
SCHEDULE_COVER = build_schedule(cover_first_page=True)
RICH = build_rich()


def _spec(*ops, request="change the page setup"):
    return {"request": request, "summary": request, "assumptions": [], "questions": [], "applies_to": {"or": "all"},
            "mode": "formatting", "ops": list(ops)}


def _setup(sections=None, **params):
    return {"op": "set_page_setup", "select": {} if sections is None else {"sections": sections}, "params": params}


def _run(data: bytes, spec):
    pkg = DocxPackage(data)
    plan, frags = plan_spec(pkg, spec)
    out = apply_spec(pkg, frags).output_bytes()
    return plan, frags, out, run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")


def _failed(rec):
    return [c.id for c in rec.checks if c.status == "FAIL"]


def _v10(rec):
    return [c for c in rec.checks if c.id == "V10"][0]


def _v10_errors(rec):
    return " | ".join(_v10(rec).data.get("errors", []))


def _secs(data: bytes):
    return DocxPackage.sect_prs(DocxPackage(data).xml(MAIN))


def _title(sp):
    tp = sp.find(W + "titlePg")
    return None if tp is None else {etree.QName(k).localname: v for k, v in tp.attrib.items()}


def _with_title_pg(data: bytes, section: int, val=None) -> bytes:
    """``data`` with section ``section``'s sectPr given a ``<w:titlePg w:val=…/>`` (fixture surgery)."""
    def edit(root):
        sp = DocxPackage.sect_prs(root)[section]
        for old in sp.findall(W + "titlePg"):
            sp.remove(old)
        tp = etree.Element(W + "titlePg")
        if val is not None:
            tp.set(W + "val", val)
        insert_in_order(sp, tp, SECTPR_ORDER)
    return patch_docx(data, {MAIN: edit})


def _mutate_sections(data: bytes, fn) -> bytes:
    return patch_docx(data, {MAIN: lambda root: fn(DocxPackage.sect_prs(root))})


# ---- 1. different first page ---------------------------------------------------------------------------------------

def test_different_first_page_on_shows_the_inherited_first_page_parts_and_flags_them():
    src_secs = _secs(RICH)
    assert [_title(sp) is not None for sp in src_secs] == [True, False, False]
    plan, frags, out, rec = _run(RICH, _spec(_setup([1, 2], different_first_page=True)))
    assert rec.passed, rec.to_markdown()
    secs = _secs(out)
    assert _title(secs[1]) == {} and _title(secs[2]) == {}                 # present, no w:val
    assert _title(secs[0]) == _title(src_secs[0])                          # the cover, not chosen, as it was
    a = plan.property_assertions[0]
    assert a["kind"] == "section_props" and a["sections"] == [1, 2] and a["expected_sections"] == 2
    assert a["select"] == {"sections": [1, 2]} and a["params"] == {"different_first_page": True}
    assert a["selected"] == [1, 2]
    assert a["per_section"] == {"1": {"titlePg": {"present": True, "remove": ["val"]}},
                                "2": {"titlePg": {"present": True, "remove": ["val"]}}}
    touches = frags[0].touches
    assert (MAIN, "sectPr:1", "titlePg") in touches and (MAIN, "sectPr:2", "titlePg") in touches
    assert (MAIN, "sectPr:1", "verify:titlePg") in touches
    # what the first page shows, through inheritance: section 0's first-page header and footer (the cover's)
    assert ("section 1: different first page on — its first page shows header word/header1.xml (section 0's, "
            "inherited), footer word/footer1.xml (section 0's, inherited)") in plan.notes
    assert any("section 2: different first page is turned on and it has no first-page footer of its own: its first "
               "page will show section 0's first-page footer word/footer1.xml" in f for f in plan.flags)
    assert "selection re-derived: sections [1, 2]" in _v10(rec).summary


def test_different_first_page_off_removes_the_title_page_and_says_what_the_first_page_shows():
    plan, frags, out, rec = _run(RICH, _spec(_setup([0], different_first_page=False)))
    assert rec.passed, rec.to_markdown()
    assert _title(_secs(out)[0]) is None
    assert plan.property_assertions[0]["per_section"] == {"0": {"titlePg": {"absent": True}}}
    note = [n for n in plan.notes if n.startswith("section 0: different first page off")][0]
    assert "header word/header2.xml, footer word/footer2.xml" in note
    assert "even-page ones" in note                                        # evenAndOddHeaders is on in build_rich
    assert not plan.flags


def test_a_title_page_switched_off_by_val_zero_is_turned_on_by_removing_the_val():
    data = _with_title_pg(SCHEDULE, 1, "0")
    assert _title(_secs(data)[1]) == {"val": "0"}
    plan, frags, out, rec = _run(data, _spec(_setup([1], different_first_page=True)))
    assert rec.passed, rec.to_markdown()
    assert _title(_secs(out)[1]) == {}
    # and asked off, the same section is already off: left alone, uncounted, nothing written
    plan, frags, out, rec = _run(data, _spec(_setup([1], different_first_page=False)))
    assert rec.passed, rec.to_markdown()
    a = plan.property_assertions[0]
    assert a["sections"] == [] and a["expected_sections"] == 0 and a["selected"] == [1]
    assert "different first page already off in section(s) [1]: left alone" in plan.notes
    assert DocxPackage(out).read(MAIN) == DocxPackage(data).read(MAIN)
    assert (MAIN, "sectPr:1", "verify:titlePg") in frags[0].touches       # read all the same (its post-condition)
    assert not any(t[2] == "titlePg" for t in frags[0].touches)


def test_a_section_already_as_asked_is_left_alone_and_uncounted():
    plan, frags, out, rec = _run(RICH, _spec(_setup([0, 1], different_first_page=True)))
    assert rec.passed, rec.to_markdown()
    a = plan.property_assertions[0]
    assert a["sections"] == [1] and a["selected"] == [0, 1]
    assert "different first page already on in section(s) [0]: left alone" in plan.notes
    assert DocxPackage(out).sections()[0] == DocxPackage(RICH).sections()[0]


def test_a_first_page_without_any_first_page_part_is_flagged_blank():
    plan, _, out, rec = _run(SCHEDULE, _spec(_setup({"containing": {"text_regex": "SCHEDULE"}},
                                                    different_first_page=True)))
    assert rec.passed, rec.to_markdown()
    assert plan.property_assertions[0]["sections"] == [2]
    assert "section 2: different first page on — its first page shows no header, no footer" in plan.notes
    assert sum("no section up to it has a first-page" in f and "will show no" in f for f in plan.flags) == 2
    # with a first-page footer on the cover, the schedule's first page shows that one ("COVER") — flagged, inherited
    plan, _, out, rec = _run(SCHEDULE_COVER, _spec(_setup({"containing": {"text_regex": "SCHEDULE"}},
                                                          different_first_page=True)))
    assert rec.passed, rec.to_markdown()
    assert any("no section up to it has a first-page header" in f and "will show no header" in f for f in plan.flags)
    assert any("will show section 0's first-page footer word/footer2.xml" in f for f in plan.flags)


# ---- 2. wrong outputs and wrong implementations ---------------------------------------------------------------------

def test_a_title_page_left_with_val_zero_fails_v10():
    data = _with_title_pg(SCHEDULE, 1, "0")
    plan, _, out, rec = _run(data, _spec(_setup([1], different_first_page=True)))
    assert rec.passed

    def keep_val(secs):
        secs[1].find(W + "titlePg").set(W + "val", "0")
    bad = _mutate_sections(out, keep_val)
    rec = run_suite(data, bad, plan.to_dict(), "s", "o")
    assert _failed(rec) == ["V10"]
    assert "section 1 titlePg: expected {} got {'val': '0'}" in _v10_errors(rec)
    assert "different first page is off in the output, asked on" in _v10_errors(rec)


def test_a_title_page_added_to_a_section_nobody_chose_fails_v10():
    plan, _, out, rec = _run(SCHEDULE, _spec(_setup([2], different_first_page=True)))
    assert rec.passed

    def also_three(secs):
        insert_in_order(secs[3], etree.Element(W + "titlePg"), SECTPR_ORDER)
    rec = run_suite(SCHEDULE, _mutate_sections(out, also_three), plan.to_dict(), "s", "o")
    assert _failed(rec) == ["V10"]                                         # the residual: section 3 is not asserted


def test_a_title_page_left_on_when_asked_off_fails_v10():
    plan, _, out, rec = _run(RICH, _spec(_setup([0], different_first_page=False)))
    assert rec.passed

    def put_back(secs):
        insert_in_order(secs[0], etree.Element(W + "titlePg"), SECTPR_ORDER)
    rec = run_suite(RICH, _mutate_sections(out, put_back), plan.to_dict(), "s", "o")
    assert _failed(rec) == ["V10"]
    assert "section 0 titlePg: should be absent" in _v10_errors(rec)


def test_an_already_rule_that_reads_val_zero_as_on_is_caught(monkeypatch):
    """Planted, shared by plan and apply: the title page read as the package reader reads it (present = on, w:val
    ignored). The plan finds the val-0 section 'already on' and changes nothing; V10 re-derives the rule itself."""
    data = _with_title_pg(SCHEDULE, 1, "0")
    monkeypatch.setattr(page_module, "title_pg_on", lambda sp: sp.find(W + "titlePg") is not None)
    plan, _, out, rec = _run(data, _spec(_setup([1], different_first_page=True)))
    assert plan.property_assertions[0]["sections"] == []                  # the wrong rule: "already on"
    assert _failed(rec) == ["V10"]
    errs = _v10_errors(rec)
    assert "section 1 must get {'titlePg': {'present': True, 'remove': ['val']}}" in errs
    assert "different first page is off in the output, asked on" in errs


def test_a_writer_that_keeps_the_val_shared_by_plan_and_apply_is_caught(monkeypatch):
    """Planted, shared: the plan asserts the element present with its attributes kept, apply keeps the w:val="0". The
    element check passes (the output is what the plan says); the re-derived spec and the post-condition do not."""
    data = _with_title_pg(SCHEDULE, 1, "0")
    cls = OPS["set_page_setup"]
    plan_orig, apply_orig = cls.plan, cls.apply

    def plan(self, pkg, select, params):
        frag = plan_orig(self, pkg, select, params)
        for a in frag.property_assertions:
            for elements in a["per_section"].values():
                if "titlePg" in elements:
                    elements["titlePg"] = {"present": True}
        return frag

    def apply(self, pkg, frag):
        frag = copy.deepcopy(frag)
        frag.transform_data["title_pg"] = {}                              # leave the existing element (val 0) alone
        apply_orig(self, pkg, frag)
    monkeypatch.setattr(cls, "plan", plan)
    monkeypatch.setattr(cls, "apply", apply)
    plan_, _, out, rec = _run(data, _spec(_setup([1], different_first_page=True)))
    assert _title(_secs(out)[1]) == {"val": "0"}
    assert _failed(rec) == ["V10"]
    errs = _v10_errors(rec)
    assert "must get {'titlePg': {'present': True, 'remove': ['val']}}" in errs and "the plan gives" in errs
    assert "section 1 titlePg:" not in errs                               # the element check alone would pass it


@pytest.mark.parametrize("dropped, sections, right, wrong", [
    ("containing", {"containing": {"text_regex": "SCHEDULE"}, "with_tables": True}, [2], [0, 2]),
    ("with_tables", {"containing": {"text_regex": "(?i)schedule"}, "with_tables": True}, [2], [1, 2]),
    ("index_range", {"with_tables": True, "index_range": [1, 3]}, [2], [0, 2]),
])
def test_a_section_selector_that_drops_a_key_shared_by_plan_and_apply_is_caught(monkeypatch, dropped, sections,
                                                                                right, wrong):
    assert resolve_sections(DocxPackage(SCHEDULE), sections, content=True) == right
    plan, _, out, rec = _run(SCHEDULE, _spec(_setup(sections, orientation="landscape")))
    assert rec.passed, rec.to_markdown()
    original = selectors_module._sections_by_content

    def forgetful(pkg, s, n):
        return original(pkg, {k: v for k, v in s.items() if k != dropped}, n)
    monkeypatch.setattr(selectors_module, "_sections_by_content", forgetful)
    plan, _, out, rec = _run(SCHEDULE, _spec(_setup(sections, orientation="landscape")))
    assert plan.property_assertions[0]["selected"] == wrong                # plan and apply agree with each other
    assert _failed(rec) == ["V10"]
    assert f"chooses sections {right} here, the plan chose {wrong}" in _v10_errors(rec)
    assert "changed but the selection does not choose them" in _v10_errors(rec)


def test_a_paragraph_section_reader_off_by_one_shared_by_plan_and_apply_is_caught(monkeypatch):
    """Planted: the paragraph that carries a section break counted in the NEXT section (its sectPr ends its own)."""
    def late(root):
        out, k = [], 0
        for p in root.iter(W + "p"):
            if p.find(f"{W}pPr/{W}sectPr") is not None:
                k += 1
            out.append(k)
        return out
    sections = {"containing": {"is_empty": True}}          # the empty paragraphs python-docx puts the breaks in
    assert resolve_sections(DocxPackage(SCHEDULE), sections, content=True) == [0, 1, 2]
    monkeypatch.setattr(tabs_module, "paragraph_sections", late)
    plan, _, out, rec = _run(SCHEDULE, _spec(_setup(sections, different_first_page=True)))
    assert plan.property_assertions[0]["selected"] == [1, 2, 3]
    assert _failed(rec) == ["V10"] and "chooses sections [0, 1, 2] here, the plan chose [1, 2, 3]" in _v10_errors(rec)


# ---- 3. the content form: both resolvers agree ----------------------------------------------------------------------

SELECTIONS = [
    "all", [0], {"with_tables": True}, {"with_tables": False}, {"index_range": [0, 0]}, {"index_range": [1, 9]},
    {"containing": {"text_regex": "SCHEDULE"}}, {"containing": {"text_regex": "(?i)schedule"}},
    {"containing": {"text_regex": "^PART \\d"}}, {"containing": {"text_regex": "\\t"}},
    {"containing": {"text_regex": "1\\.01 SUMMARY"}},                      # the tab-normalised fallback
    {"containing": {"style_in": ["Heading1"]}}, {"containing": {"style_in": ["Title", "Heading1"]}},
    {"containing": {"in_table": True}}, {"containing": {"in_table": False, "is_empty": True}},
    {"containing": {"has_bold": True}}, {"containing": {"has_bold": False}}, {"containing": {"is_all_caps": True}},
    {"containing": {"after_heading_regex": "^PART 2"}}, {"containing": {"index_range": [0, 4]}},
    {"containing": {"between_paragraphs": {"start_regex": "^PART 1", "end_regex": "^PART 3"}}},
    {"containing": {"between_paragraphs": {"start_regex": "^PART 1", "end_regex": "^PART 3", "include_bounds": True}}},
    {"containing": {"text_regex": "APPENDIX"}, "with_tables": False},
    {"containing": {"has_bold": True}, "with_tables": True, "index_range": [1, 2]},
]
FIXTURES = {"schedule": SCHEDULE, "schedule-cover": SCHEDULE_COVER, "rich": RICH,
            "build": build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS", appendix_restart=True),
            "tabs": build_tabs(), "batch1": build_batch1(), "styles": build_styles()}


def _v_styles(data: bytes):
    v = VPackage(data)
    part = v.related(STYLES_REL)
    return v, vprops.Styles(v.xml(part) if part else None)


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_transform_and_verifier_section_selections_agree(name):
    data = FIXTURES[name]
    pkg = DocxPackage(data)
    v, styles = _v_styles(data)
    n = len(pkg.sections())
    seen = set()
    for sel in SELECTIONS:
        if isinstance(sel, list) and max(sel) >= n:
            continue
        got = resolve_sections(pkg, sel, content=True)
        assert got == vprops._v_resolve_sections(v, sel, styles), (name, sel)
        seen.add(tuple(got))
    assert len(seen) >= 2, name                                             # the selections tell sections apart here


def test_the_schedule_fixture_reads_as_intended():
    pkg = DocxPackage(SCHEDULE)
    assert resolve_sections(pkg, {"with_tables": True}, content=True) == [0, 2]
    assert resolve_sections(pkg, {"containing": {"text_regex": "SCHEDULE"}}, content=True) == [2]
    assert resolve_sections(pkg, {"containing": {"text_regex": "(?i)schedule"}}, content=True) == [1, 2]
    assert resolve_sections(pkg, {"containing": {"style_in": ["Heading1"]}}, content=True) == [1, 3]
    # the schedule's bold header row, and Heading 1 — bold by its style
    assert resolve_sections(pkg, {"containing": {"has_bold": True}}, content=True) == [1, 2, 3]
    assert resolve_sections(pkg, {"index_range": [1, 2]}, content=True) == [1, 2]


# ---- 4. landscape ---------------------------------------------------------------------------------------------------

def test_landscape_schedule_flags_the_header_and_footer_shared_with_portrait_pages():
    plan, _, out, rec = _run(SCHEDULE, _spec(_setup({"containing": {"text_regex": "SCHEDULE"}, "with_tables": True},
                                                    orientation="landscape")))
    assert rec.passed, rec.to_markdown()
    pg = _secs(out)[2].find(W + "pgSz")
    assert pg.get(W + "orient") == "landscape" and int(pg.get(W + "w")) > int(pg.get(W + "h"))
    assert all(sp.find(W + "pgSz").get(W + "orient") is None for i, sp in enumerate(_secs(out)) if i != 2)
    for kind, part in (("header", "word/header1.xml"), ("footer", "word/footer1.xml")):
        assert (f"the {kind} {part} is shown on portrait and landscape pages (portrait: sections [0, 1, 3]; "
                f"landscape: sections [2]): its right-aligned items stay where the portrait page puts them") in plan.flags
    assert len(plan.flags) == 2
    assert "orientation swaps page width/height where they disagree with it; margins are not rotated" in plan.notes
    # every section landscape: nothing shared with a portrait page, nothing flagged
    plan, _, _, rec = _run(SCHEDULE, _spec(_setup("all", orientation="landscape")))
    assert rec.passed and not plan.flags


def test_a_first_page_part_shown_only_by_the_portrait_cover_is_not_flagged_unless_shown_on_landscape():
    # the cover's first-page footer (titlePg on the cover only) is not shown by the landscape schedule …
    plan, _, _, rec = _run(SCHEDULE_COVER, _spec(_setup([2], orientation="landscape")))
    assert rec.passed and not any("word/footer2.xml" in f for f in plan.flags)
    # … until the schedule shows its first page differently too: then it is (through inheritance)
    plan, _, _, rec = _run(SCHEDULE_COVER, _spec(_setup([2], orientation="landscape", different_first_page=True)))
    assert rec.passed, rec.to_markdown()
    assert any(f.startswith("the footer word/footer2.xml is shown on portrait and landscape pages (portrait: "
                            "sections [0]; landscape: sections [2])") for f in plan.flags)


# ---- 5. header/footer distances, not-found, bad params ---------------------------------------------------------------

def test_header_and_footer_distance_are_margins_header_and_footer():
    plan, _, out, rec = _run(SCHEDULE, _spec(_setup("all", margins={"header": 720, "footer": 600})))
    assert rec.passed, rec.to_markdown()
    for sp, src in zip(_secs(out), _secs(SCHEDULE)):
        pm, sm = sp.find(W + "pgMar"), src.find(W + "pgMar")
        assert pm.get(W + "header") == "720" and pm.get(W + "footer") == "600"
        assert {k: v for k, v in pm.attrib.items() if etree.QName(k).localname not in ("header", "footer")} == \
            {k: v for k, v in sm.attrib.items() if etree.QName(k).localname not in ("header", "footer")}

    def other_left(secs):
        secs[1].find(W + "pgMar").set(W + "left", "1000")
    assert _failed(run_suite(SCHEDULE, _mutate_sections(out, other_left), plan.to_dict(), "s", "o")) == ["V10"]


def test_no_section_matching_is_not_found_unless_allow_missing():
    nowhere = {"containing": {"text_regex": "NO SUCH SCHEDULE"}}
    with pytest.raises(JobException) as e:
        plan_spec(DocxPackage(SCHEDULE), _spec(_setup(nowhere, orientation="landscape")))
    assert e.value.code == "not-found"
    plan, _, out, rec = _run(SCHEDULE, _spec(_setup(nowhere, orientation="landscape", allow_missing=True)))
    assert rec.passed, rec.to_markdown()
    assert any("no section matches" in n and "allow_missing" in n for n in plan.notes)
    assert plan.intended_parts == [] and DocxPackage(out).read(MAIN) == DocxPackage(SCHEDULE).read(MAIN)
    # planted: an op that did not refuse — V10 knows the user did not allow it
    d = plan.to_dict()
    d["property_assertions"][0]["params"].pop("allow_missing")
    rec = run_suite(SCHEDULE, out, d, "s", "o")
    assert _failed(rec) == ["V10"] and "the op must refuse (not-found)" in _v10_errors(rec)


@pytest.mark.parametrize("sections, params", [
    ({}, {"orientation": "landscape"}),
    ({"containing": {"text_regex": "x"}, "pages": 2}, {"orientation": "landscape"}),
    ({"containing": "all"}, {"orientation": "landscape"}),
    ({"containing": {}}, {"orientation": "landscape"}),
    ({"containing": {"text_regex": "("}}, {"orientation": "landscape"}),
    ({"containing": {"colour": "red"}}, {"orientation": "landscape"}),
    ({"with_tables": "yes"}, {"orientation": "landscape"}),
    ({"index_range": [2, 1]}, {"orientation": "landscape"}),
    ({"index_range": [-1, 1]}, {"orientation": "landscape"}),
    ({"index_range": [0, True]}, {"orientation": "landscape"}),
    ("all", {"different_first_page": "yes"}),
    ("all", {"allow_missing": 1, "orientation": "landscape"}),
    ("all", {}),
])
def test_malformed_selections_and_params_are_bad_params(sections, params):
    with pytest.raises(JobException) as e:
        get_op("set_page_setup").plan(DocxPackage(SCHEDULE), {"sections": sections}, params)
    assert e.value.code == "bad-params"


def test_the_content_form_is_set_page_setup_s_only():
    with pytest.raises(JobException) as e:
        get_op("remove_header_footer").plan(DocxPackage(SCHEDULE), {"sections": {"with_tables": True}}, {})
    assert e.value.code == "bad-params"


# ---- 6. reads and conflicts (rule 9) ----------------------------------------------------------------------------------

def _reads(frag):
    return {t for t in frag.touches if verify_family(t[2])[0]}


def test_the_content_form_declares_what_it_reads():
    pkg = DocxPackage(SCHEDULE)
    plan = lambda s: get_op("set_page_setup").plan(pkg, {"sections": s}, {"orientation": "landscape"})
    assert _reads(plan({"containing": {"text_regex": "SCHEDULE"}})) == {(MAIN, "*", "verify:text")}
    assert _reads(plan({"containing": {"style_in": ["Heading1"]}})) == {(MAIN, "*", "verify-props:pStyle")}
    assert _reads(plan({"containing": {"has_bold": True}})) == {
        (MAIN, "*", "verify:text"), (MAIN, "*", "verify-props:pStyle"), (MAIN, "*", "verify-props:rStyle"),
        (MAIN, "*", "verify-props:tblPr")}
    # no text edit moves a table: with_tables reads only where tables and section breaks are, which only a whole-member
    # change of the main part moves (batch 5's review: it used to refuse every replace_text beside it)
    assert _reads(plan({"with_tables": True})) == set()
    assert _reads(plan({"index_range": [1, 2]})) == set()
    assert _reads(plan([1, 2])) == set() and _reads(plan("all")) == set()


def test_a_text_edit_beside_a_selection_by_text_is_a_conflict_and_by_index_is_not():
    pkg = DocxPackage(SCHEDULE)
    edit = get_op("replace_text").plan(pkg, {}, {"find": "Schedule 40", "replace": "Schedule 10"})
    by_text = get_op("set_page_setup").plan(pkg, {"sections": {"containing": {"text_regex": "(?i)schedule"}}},
                                            {"orientation": "landscape"})
    with pytest.raises(JobException) as e:
        merge_fragments([by_text, edit])
    assert e.value.code == "conflict" and "the text of every target in it" in e.value.reason
    by_index = get_op("set_page_setup").plan(pkg, {"sections": {"index_range": [1, 2]}}, {"orientation": "landscape"})
    assert merge_fragments([by_index, edit]).ops
    by_tables = get_op("set_page_setup").plan(pkg, {"sections": {"with_tables": True}}, {"orientation": "landscape"})
    assert merge_fragments([by_tables, edit]).ops


def test_a_restyle_beside_a_selection_by_style_is_a_conflict():
    pkg = DocxPackage(SCHEDULE)
    restyle = get_op("apply_style").plan(pkg, {"paragraphs": {"text_regex": "^PART 3"}}, {"style": "Heading2"})
    by_style = get_op("set_page_setup").plan(pkg, {"sections": {"containing": {"style_in": ["Heading1"]}}},
                                             {"orientation": "landscape"})
    with pytest.raises(JobException) as e:
        merge_fragments([by_style, restyle])
    assert e.value.code == "conflict" and "pStyle" in e.value.reason


def test_different_first_page_beside_cover_unnumbered_is_a_conflict():
    pkg = DocxPackage(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    unnumber = {"op": "fix_page_numbering", "params": {"option": "cover-unnumbered"}}
    for ops, reader, changer in (([unnumber, _setup([0], different_first_page=True)], "#1 fix_page_numbering",
                                  "#2 set_page_setup"),
                                 ([_setup([0], different_first_page=True), unnumber], "#2 fix_page_numbering",
                                  "#1 set_page_setup")):
        with pytest.raises(JobException) as e:
            plan_spec(pkg, _spec(*ops))
        assert e.value.code == "conflict"
        assert f"op {reader} plans from titlePg of word/document.xml [sectPr:0] as it is in the source" in e.value.reason
        assert f"{changer} would change titlePg there underneath" in e.value.reason
    # a section the cover-unnumbered fix reads too: every section's titlePg
    with pytest.raises(JobException) as e:
        plan_spec(pkg, _spec(unnumber, _setup([1], different_first_page=True)))
    assert e.value.code == "conflict" and "[sectPr:1]" in e.value.reason


def test_continuous_numbering_beside_different_first_page_plans_and_verifies():
    data = build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS", appendix_restart=True)
    continuous = {"op": "fix_page_numbering", "params": {"option": "continuous"}}
    for ops in ([continuous, _setup([1, 2], different_first_page=True)],
                [_setup([1, 2], different_first_page=True), continuous]):
        plan, _, out, rec = _run(data, _spec(*ops))
        assert rec.passed, rec.to_markdown()
        assert [s.pg_num_start for s in DocxPackage(out).sections()] == [None, None, None]
        assert [_title(sp) for sp in _secs(out)][1:] == [{}, {}]


def _hf(**params):
    op = copy.deepcopy(load_spec(HF_SPEC)["ops"][0])
    op["params"].update(params)
    return op


def test_different_first_page_beside_the_templating_op_is_a_conflict():
    pkg = DocxPackage(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    with pytest.raises(JobException) as e:
        plan_spec(pkg, _spec(_setup([1], different_first_page=True), _hf()))
    assert e.value.code == "conflict"
    assert "op #2 apply_header_footer_template plans from titlePg of word/document.xml [sectPr:1]" in e.value.reason
    assert "#1 set_page_setup would change titlePg there underneath" in e.value.reason


def _title_page_target() -> bytes:
    """build_rich() without evenAndOddHeaders (the template spec has it off): a cover with titlePg the template
    turns off (as in test_read_conflicts)."""
    work = DocxPackage(RICH).copy()
    root = work.xml("word/settings.xml")
    for el in root.findall(W + "evenAndOddHeaders"):
        root.remove(el)
    work.write_xml("word/settings.xml", root)
    return work.output_bytes()


def test_a_section_already_as_asked_is_still_read_beside_a_template_that_flips_it(monkeypatch):
    pkg = DocxPackage(_title_page_target())
    keep_cover = _setup([0], different_first_page=True)                     # already on: nothing to change
    headers = _hf(kinds=["header"])                                         # the template has no title page
    with pytest.raises(JobException) as e:
        plan_spec(pkg, _spec(keep_cover, headers))
    assert e.value.code == "conflict"
    assert "op #1 set_page_setup plans from titlePg of word/document.xml [sectPr:0]" in e.value.reason
    assert "#2 apply_header_footer_template would change titlePg there underneath" in e.value.reason
    # planted: without set_page_setup's reads, the spec plans and the template turns the cover's first page off
    # underneath the "already on" — V10's post-condition fails after apply (a late refusal, never a wrong PASS)
    cls = OPS["set_page_setup"]
    original = cls.plan

    def plan(self, pkg_, select, params):
        frag = original(self, pkg_, select, params)
        frag.touches = [t for t in frag.touches if not verify_family(t[2])[0]]
        return frag
    monkeypatch.setattr(cls, "plan", plan)
    _, _, out, rec = _run(pkg.data, _spec(keep_cover, headers))
    assert "V10" in _failed(rec)
    assert "section 0 different first page is off in the output, asked on" in _v10_errors(rec)


# ---- 7. old plans, the generic element specs ---------------------------------------------------------------------------

def test_old_shape_section_props_plans_verify_as_before():
    plan, _, out, rec = _run(SCHEDULE, _spec(_setup([1, 2], margins={"left": 1440, "right": 1440})))
    assert rec.passed
    d = plan.to_dict()
    d["property_assertions"] = [{"kind": "section_props", "part": MAIN, "sections": [1, 2],
                                 "elements": {"pgMar": {"set": {"left": "1440", "right": "1440"}}},
                                 "expected_sections": 2}]
    assert run_suite(SCHEDULE, out, d, "s", "o").passed

    def gutter_dropped(secs):
        del secs[1].find(W + "pgMar").attrib[W + "gutter"]
    assert "V10" in _failed(run_suite(SCHEDULE, _mutate_sections(out, gutter_dropped), d, "s", "o"))


def test_present_and_absent_element_specs():
    _, _, out, _ = _run(SCHEDULE, _spec(_setup([2], different_first_page=True)))
    base = {"kind": "section_props", "part": MAIN, "sections": [2], "expected_sections": 1}

    def check(data, elements):
        d = {"property_assertions": [dict(base, elements=elements)], "diffs": [], "mode": "formatting",
             "intended_parts": [MAIN]}
        return vprops.check_properties(VPackage(SCHEDULE), VPackage(data), d)
    assert check(out, {"titlePg": {"present": True}})[0]
    assert check(out, {"titlePg": {"present": True, "remove": ["val"]}})[0]
    ok, _, info = check(out, {"titlePg": {"absent": True}})
    assert not ok and "section 2 titlePg: should be absent" in info["errors"][0]
    ok, _, info = check(SCHEDULE, {"titlePg": {"present": True}})            # nothing changed: it is not there
    assert not ok and "section 2 titlePg: should be present" in info["errors"][0]
    with_child = _mutate_sections(out, lambda secs: secs[2].find(W + "titlePg").append(etree.Element(W + "x")))
    ok, _, info = check(with_child, {"titlePg": {"present": True}})
    assert not ok and "children changed" in info["errors"][0]
    ok, _, info = check(out, {"titlePg": {"absent": True, "set": {"val": "1"}}})
    assert not ok and "an absent element spec takes no other keys" in info["errors"][0]


# ---- 8. around the op ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name, data, sections", [
    ("landscape-schedule-sections", SCHEDULE, {"2": {"pgSz"}}),
    ("different-first-page", SCHEDULE, None),
], ids=["landscape-schedule-sections", "different-first-page"])
def test_worked_examples_plan_and_verify(name, data, sections):
    spec = load_spec(EXAMPLES / f"{name}.json")
    validate_spec(spec)
    assert json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))["request"]
    plan, _, out, rec = _run(data, spec)
    assert rec.passed, rec.to_markdown()
    if sections is not None:
        a = plan.property_assertions[0]
        assert {k: set(v) for k, v in a["per_section"].items()} == sections
    # an example whose sections match nothing in a file is a no-op there, never a refusal
    if name == "landscape-schedule-sections":            # build(): "Schedule 40" in the body, its only table on the cover
        plan, _, out, rec = _run(FIXTURES["build"], spec)
        assert rec.passed and any("no section matches" in n for n in plan.notes)


def test_catalog_says_how_the_op_reads():
    words = " ".join(COMMON_ASSUMPTIONS["set_page_setup"])
    for needle in ("twips", "margins.header and margins.footer", "different_first_page", "landscape",
                   "margins as they are", "containing", "with_tables", "index_range", "allow_missing"):
        assert needle in words, needle
    assert "containing" in SELECTOR_GRAMMAR["sections"]
    cat = op_catalog(examples=False)
    assert "different_first_page" in cat["ops"]["set_page_setup"]["params"]["properties"]


def test_recovery_infers_no_final_page_check_for_a_content_selection():
    spec = load_spec(EXAMPLES / "landscape-schedule-sections.json")
    assertions, notes = recovery._goals(spec)
    assert not [a for a in assertions if a["kind"] == "page_setup"]
    assert any("chooses sections by their content" in n for n in notes)
    by_index = _spec(_setup([2], orientation="landscape"))
    assertions, _ = recovery._goals(by_index)
    assert assertions == [{"kind": "page_setup", "sections": [2], "orientation": "landscape"}]
