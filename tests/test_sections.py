"""Built-in batch 6, F7 — sections (docs/BUILTIN_OPS_BATCH6_DESIGN.md §8): insert_section_break, remove_section_break
and set_page_setup's columns, line numbering, vertical alignment and section start, with V10's
``inserted_section_breaks``, ``removed_section_breaks`` and ``section_props`` kinds.

End to end on multi-section fixtures (tests/fx_sections.py: a cover with a different first page and a logo, a body that
restarts its page numbers, sections with their own headers, two sections sharing one header part, a landscape appendix);
a wrong OUTPUT per rule; wrong IMPLEMENTATIONS shared by plan and apply; the refusals and their codes; parameters;
plan-time conflicts and the engine's composition into steps; the catalog; the batch pipeline; LibreOffice renders (the
pages a break adds or removes, two columns)."""
from __future__ import annotations

import ast
import copy
import shutil
from pathlib import Path

import pytest
from lxml import etree

from fx_sections import (MAIN, W, body_paragraph, build_columns_text, build_page_break, build_sections, build_single,
                         in_content_control, sect_prs, text_of, with_section_change, with_section_xml, with_textbox,
                         with_tracked_mark)
from make_fixtures import add_parts, build_schedule, patch_docx
from spec_surgeon import editspec
from spec_surgeon.catalog import COMMON_ASSUMPTIONS, SELECTOR_GRAMMAR, op_catalog
from spec_surgeon.editspec import apply_spec, plan_chain, plan_spec, split_into_stages, validate_spec
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import insert_section_break as ins_module
from spec_surgeon.ops import remove_section_break as rem_module
from spec_surgeon.ops import set_page_setup as page_module
from spec_surgeon.package import DocxPackage
from spec_surgeon.report import FAILURE_EXPLANATIONS
from spec_surgeon.verify import run_suite
from spec_surgeon.verify.props import sig

SECTIONS = build_sections()
SINGLE = build_single()
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
R = "{%s}" % R_NS


def _spec(*ops, request="change the sections"):
    return {"request": request, "summary": request, "assumptions": [], "questions": [], "applies_to": {"or": "all"},
            "mode": "formatting", "ops": list(ops)}


def ins(rx=None, select=None, **params):
    return {"op": "insert_section_break", "select": select or {"paragraphs": {"text_regex": rx}}, "params": params}


def rem(sections="all", **params):
    return {"op": "remove_section_break", "select": {"sections": sections}, "params": params}


def setup(sections, **params):
    return {"op": "set_page_setup", "select": {"sections": sections}, "params": params}


def _run(data: bytes, *ops):
    pkg = DocxPackage(data)
    plan, frags = plan_spec(pkg, _spec(*ops))
    out = apply_spec(pkg, frags).output_bytes()
    return plan, frags, out, run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")


def _refused(data: bytes, *ops) -> JobException:
    with pytest.raises(JobException) as e:
        plan_spec(DocxPackage(data), _spec(*ops))
    return e.value


def _failed(rec):
    return [c.id for c in rec.checks if c.status == "FAIL"]


def _v10(rec) -> str:
    c = next(c for c in rec.checks if c.id == "V10")
    return c.summary + " | " + " | ".join(c.data.get("errors", []))


def _root(data: bytes):
    return DocxPackage(data).xml(MAIN)


def _secs(data: bytes):
    return sect_prs(_root(data))


def _attrs(el):
    return None if el is None else {etree.QName(k).localname: v for k, v in el.attrib.items()}


def _kids(sp):
    return [etree.QName(c).localname for c in sp]


def _refs(sp):
    return [(etree.QName(c).localname, c.get(W + "type"), c.get(R + "id")) for c in sp
            if etree.QName(c).localname in ("headerReference", "footerReference")]


def _holder_text(sp) -> str:
    return text_of(sp.getparent().getparent())


def _caught(data, plan, out, fn, needle=None, checks=("V10",)):
    bad = patch_docx(out, {MAIN: fn})
    rec = run_suite(data, bad, plan.to_dict(), "src.docx", "out.docx")
    assert set(checks) <= set(_failed(rec)), rec.to_markdown()
    if needle:
        assert needle in _v10(rec), _v10(rec)
    return rec


def _el(xml: str):
    return etree.fromstring(f'<w:x xmlns:w="{W[1:-1]}" xmlns:r="{R_NS}">{xml}</w:x>')[0]


def test_the_fixture_reads_as_intended():
    pkg = DocxPackage(SECTIONS)
    info = pkg.sections()
    assert len(info) == 4
    assert info[0].title_pg and info[0].header_refs.keys() == {"default", "first"} and info[0].footer_refs
    assert info[1].pg_num_start == 1 and info[1].header_refs["default"] == info[3].header_refs["default"]
    assert not info[2].header_refs and not info[2].footer_refs and not info[2].title_pg
    assert info[3].footer_refs["default"] != info[1].footer_refs["default"]
    secs = _secs(SECTIONS)
    assert int(secs[2].find(W + "pgSz").get(W + "w")) > int(secs[2].find(W + "pgSz").get(W + "h"))
    assert [_holder_text(sp) for sp in secs[:3]] == ["", "", ""]          # python-docx's empty break paragraphs


# ---- insert_section_break: end to end -----------------------------------------------------------------------------

def test_a_break_before_part_2_splits_the_body_and_its_restart_stays_with_the_first_part():
    plan, frags, out, rec = _run(SECTIONS, ins("^PART 2"))
    assert rec.passed, rec.to_markdown()
    src, got = _secs(SECTIONS), _secs(out)
    assert len(got) == 5
    # the paragraph before the match carries a copy of the body's sectPr as it was: same references, restart at 1
    assert _holder_text(got[1]) == "A.\tThis section includes wet-pipe sprinkler systems."
    assert sig(got[1]) == sig(src[1])
    # the body's own sectPr (now ending the second part) starts on a new page with no restart: the numbers run on
    assert got[2].find(W + "pgNumType") is None and got[2].find(W + "type") is None
    assert _refs(got[2]) == _refs(src[1])
    # every other section exactly as it was
    assert [sig(x) for x in (got[0], got[3], got[4])] == [sig(x) for x in (src[0], src[2], src[3])]
    a = plan.property_assertions[0]
    assert a["kind"] == "inserted_section_breaks" and a["expected_breaks"] == 1 and a["sections"] == 5
    assert a["select"] == {"paragraphs": {"text_regex": "^PART 2"}} and a["params"] == {}
    assert [b["section"] for b in a["breaks"]] == [1]
    assert (MAIN, "*", "sections") in frags[0].touches
    assert plan.geometry_reference == "none" and not plan.diffs and plan.intended_parts == [MAIN]
    assert any("page-number restart (at 1) stays with its first part" in n for n in plan.notes)
    assert "the main part compared whole" in _v10(rec)


def test_two_breaks_in_one_section_make_three_parts_and_a_continuous_start():
    plan, _, out, rec = _run(SECTIONS, ins("^1\\.01|^PART 2", type="continuous"))
    assert rec.passed, rec.to_markdown()
    src, got = _secs(SECTIONS), _secs(out)
    assert len(got) == 6
    first, middle, last = got[1], got[2], got[3]
    assert _holder_text(first) == "PART 1 - GENERAL" and sig(first) == sig(src[1])      # the original start, restart
    assert _holder_text(middle) == "A.\tThis section includes wet-pipe sprinkler systems."
    for sp in (middle, last):
        assert _attrs(sp.find(W + "type")) == {"val": "continuous"} and sp.find(W + "pgNumType") is None
        assert _refs(sp) == _refs(src[1])
    assert _kids(middle).index("type") < _kids(middle).index("pgSz")                    # schema order
    assert "splits into 3 parts" in " ".join(plan.notes)


def test_a_break_in_a_section_with_a_different_first_page_is_flagged():
    plan, _, out, rec = _run(SECTIONS, ins("^PROJECT REVISION"))
    assert rec.passed, rec.to_markdown()
    got = _secs(out)
    assert len(got) == 5 and _holder_text(got[0]) == "WET-PIPE SPRINKLER SYSTEMS"
    assert got[0].find(W + "titlePg") is not None and got[1].find(W + "titlePg") is not None
    flag = [f for f in plan.flags if "different first page" in f]
    assert flag and "“PROJECT REVISION HISTORY”" in flag[0] and "header word/header2.xml, no footer" in flag[0]


def test_a_break_in_the_last_section_copies_the_bodys_last_sectpr():
    plan, _, out, rec = _run(SECTIONS, ins("^3\\.01"))
    assert rec.passed, rec.to_markdown()
    src, got = _secs(SECTIONS), _secs(out)
    assert _holder_text(got[3]) == "PART 3 - EXECUTION" and sig(got[3]) == sig(src[3])
    assert sig(got[4]) == sig(src[3])                     # a next-page start, no restart: as it was
    assert _refs(got[3])[0][2] == _refs(got[1])[0][2]     # the shared header part, now named by three sections


def test_line_numbers_notes_columns_and_vertical_alignment_are_flagged():
    data = with_section_xml(SECTIONS, 1, '<w:footnotePr><w:numRestart w:val="eachSect"/></w:footnotePr>'
                                         '<w:lnNumType w:countBy="1" w:restart="newSection"/>'
                                         '<w:cols w:num="2" w:space="720"/><w:vAlign w:val="center"/>')
    plan, _, out, rec = _run(data, ins("^PART 2"))
    assert rec.passed, rec.to_markdown()
    words = " ".join(plan.flags)
    for needle in ("line numbers restart at each section", "footnote numbers restart at each section",
                   "set in 2 columns: the columns of each part end at its break", "aligned vertically (center)"):
        assert needle in words, needle
    plan, _, _, rec = _run(data, ins("^PART 2", type="continuous"))
    assert rec.passed and "and are balanced there" in " ".join(plan.flags)


def test_odd_page_breaks_in_a_single_section_document():
    plan, _, out, rec = _run(SINGLE, ins("^APPENDIX", type="odd_page"))
    assert rec.passed, rec.to_markdown()
    got = _secs(out)
    assert _holder_text(got[0]).startswith("1.03\tArticle 3.") and _holder_text(got[1]).startswith("Appendix A text.")
    assert got[0].find(W + "type") is None                                        # the original start
    assert [_attrs(sp.find(W + "type")) for sp in got[1:]] == [{"val": "oddPage"}] * 2
    assert any("Word adds a blank page" in n for n in plan.notes)
    assert sum("different first page" in f for f in plan.flags) == 1             # one flag naming both breaks


def test_a_new_page_break_after_a_manual_page_break_is_flagged():
    """The usual manual page break before an appendix: a next-page (even, odd) section break carried by the paragraph
    that holds it puts that paragraph's mark alone on a page — a blank page in Word. Flagged, nothing else changes; a
    continuous break, or words after the page break, leave no blank page."""
    needle = ("ends with a manual page break: a page break and a {} section break together leave a blank page in Word; "
              "remove the page break, or use a continuous break")
    data = build_page_break()
    for kind in ("next_page", "odd_page", "even_page"):
        plan, _, out, rec = _run(data, ins("^APPENDIX A", type=kind))
        assert rec.passed, rec.to_markdown()
        assert sum(needle.format(kind.replace("_", "-")) in f for f in plan.flags) == 1, plan.flags
        assert len(list(_root(out).iter(W + "br"))) == 1                          # the page break stays
    plan, _, _, rec = _run(data, ins("^APPENDIX A", type="continuous"))
    assert rec.passed and not any("manual page break" in f for f in plan.flags)
    plan, _, _, rec = _run(build_page_break(text_after=True), ins("^APPENDIX A"))
    assert rec.passed and not any("manual page break" in f for f in plan.flags)
    plan, _, _, rec = _run(SINGLE, ins("^APPENDIX A"))
    assert rec.passed and not any("manual page break" in f for f in plan.flags)


def test_allow_missing_makes_no_match_a_no_op():
    e = _refused(SECTIONS, ins("^NO SUCH HEADING"))
    assert e.code == "not-found"
    plan, _, out, rec = _run(SECTIONS, ins("^NO SUCH HEADING", allow_missing=True))
    assert rec.passed and plan.intended_parts == [] and DocxPackage(out).read(MAIN) == DocxPackage(SECTIONS).read(MAIN)
    d = plan.to_dict()                                    # planted: the user did not allow it — V10 knows
    d["property_assertions"][0]["params"] = {}
    rec = run_suite(SECTIONS, out, d, "s", "o")
    assert _failed(rec) == ["V10"] and "the op must refuse (not-found)" in _v10(rec)


# ---- insert_section_break: refusals and parameters --------------------------------------------------------------------

@pytest.mark.parametrize("data, rx, code, needle", [
    (SECTIONS, "^SECTION 21", "break-position", "first paragraph of the document"),
    (SECTIONS, "^2\\.02", "break-position", "follows a table"),
    (SECTIONS, "^APPENDIX A", "break-position", "already starts a section"),
    (SECTIONS, "^PART 1", "break-position", "already starts a section"),
    (SECTIONS, "^Schedule 40", "break-position", "inside a table"),
    (in_content_control(SECTIONS, "PART 2"), "^PART 2", "break-position", "content control"),
    (in_content_control(SECTIONS, "A.\tThis section"), "^PART 2", "break-position", "follows a content control"),
    (with_textbox(SECTIONS, "2.01", "BOXED NOTE"), "^BOXED", "textbox", "text box"),
    (with_tracked_mark(SECTIONS, "A.\tThis section"), "^PART 2", "tracked-section", "paragraph mark"),
    (with_tracked_mark(SECTIONS, "A.\tThis section", "del"), "^PART 2", "tracked-section", "paragraph mark"),
    (with_section_change(SECTIONS, 1), "^PART 2", "tracked-section", "w:sectPrChange"),
], ids=["first", "after-table", "starts-section", "starts-body", "in-table", "in-sdt", "after-sdt", "textbox",
        "tracked-ins-mark", "tracked-del-mark", "section-change"])
def test_insert_refusals(data, rx, code, needle):
    e = _refused(data, ins(rx))
    assert e.code == code and needle in e.reason, (e.code, e.reason)
    assert code in FAILURE_EXPLANATIONS


def test_a_bookmark_between_the_paragraphs_is_no_obstacle():
    def mark(root):
        p = body_paragraph(root, "PART 2")
        p.addprevious(_el('<w:bookmarkStart w:id="77" w:name="part2"/>'))
        p.addnext(_el('<w:bookmarkEnd w:id="77"/>'))
    data = patch_docx(SECTIONS, {MAIN: mark})
    plan, _, out, rec = _run(data, ins("^PART 2"))
    assert rec.passed, rec.to_markdown()
    assert _holder_text(_secs(out)[1]) == "A.\tThis section includes wet-pipe sprinkler systems."


@pytest.mark.parametrize("select, params", [
    ({"paragraphs": "all"}, {}), ({}, {}), ({"paragraphs": {"case_insensitive": True}}, {}),
    ({"paragraphs": {"text_regex": "^PART 2"}, "scope": ["headers"]}, {}),
    ({"paragraphs": {"text_regex": "^PART 2"}}, {"type": "column"}),
    ({"paragraphs": {"text_regex": "^PART 2"}}, {"tracked": True}),
    ({"paragraphs": {"text_regex": "^PART 2"}}, {"allow_missing": "yes"}),
    ({"paragraphs": {"text_regex": "^PART 2"}}, {"text": "PART 2"}),
    ({"paragraphs": {"text_regex": "("}}, {}),
])
def test_insert_parameters_are_checked(select, params):
    with pytest.raises(JobException) as e:
        get_op("insert_section_break").plan(DocxPackage(SECTIONS), select, params)
    assert e.value.code == "bad-params"
    assert get_op("insert_section_break").plan(DocxPackage(SECTIONS), {"paragraphs": {"text_regex": "^PART 2"}},
                                               {"tracked": False}).property_assertions


# ---- insert_section_break: wrong outputs, wrong implementations ------------------------------------------------------

def test_wrong_insert_outputs_are_caught():
    plan, _, out, rec = _run(SECTIONS, ins("^PART 2"))
    assert rec.passed

    def drop_reference(root):
        sp = sect_prs(root)[1]
        sp.remove(sp.find(W + "headerReference"))
    _caught(SECTIONS, plan, out, drop_reference, "not the source with exactly the predicted section change")

    def restart_kept(root):
        sp = sect_prs(root)[2]
        sp.insert(len(sp) - 1, _el('<w:pgNumType w:start="1"/>'))
    _caught(SECTIONS, plan, out, restart_kept, "not the source with exactly")

    def break_on_the_match(root):                 # the break carried by the matched paragraph, not the one before it
        sp = sect_prs(root)[1]
        p = body_paragraph(root, "PART 2")
        sp.getparent().remove(sp)
        p.find(W + "pPr").append(sp)
    _caught(SECTIONS, plan, out, break_on_the_match, "not the source with exactly")

    def another_margin(root):
        sect_prs(root)[3].find(W + "pgMar").set(W + "left", "720")
    _caught(SECTIONS, plan, out, another_margin, "not the source with exactly")


def test_a_carrier_reader_shared_by_plan_and_apply_is_caught(monkeypatch):
    """Planted, shared: the paragraph before the match read as the one before THAT. Plan and apply agree (the carrier
    index travels in the transform data); V10 re-derives the carrier and the expected part."""
    real = ins_module.previous_block
    monkeypatch.setattr(ins_module, "previous_block", lambda p: real(real(p)))
    plan, _, out, rec = _run(SECTIONS, ins("^PART 2"))
    assert _holder_text(_secs(out)[1]) == "1.01\tSUMMARY"
    assert _failed(rec) == ["V10"]
    assert "the selection asks for breaks" in _v10(rec) and "not the source with exactly" in _v10(rec)


def test_a_section_reader_shared_by_plan_and_apply_is_caught(monkeypatch):
    """Planted, shared: a paragraph's section read as the one before it (the last break before the paragraph rather than
    the first sectPr after it), so the copy is the cover's sectPr and the cover's start is the one rewritten."""
    real = ins_module.paragraph_sections
    monkeypatch.setattr(ins_module, "paragraph_sections", lambda root: [max(k - 1, 0) for k in real(root)])
    plan, _, out, rec = _run(SECTIONS, ins("^PART 2"))
    assert sig(_secs(out)[1]) == sig(_secs(SECTIONS)[0])
    assert _failed(rec) == ["V10"] and "the selection asks for breaks" in _v10(rec)


def test_a_start_writer_that_keeps_the_restart_is_caught(monkeypatch):
    """Planted in apply: the governing section keeps its page-number restart (the numbers would start over at the new
    break). The plan says nothing of the start; V10 rebuilds the expected part itself."""
    real = ins_module.set_start
    monkeypatch.setattr(ins_module, "set_start", lambda sp, t, r: real(sp, t, sp.find(W + "pgNumType").get(W + "start")
                                                                     if sp.find(W + "pgNumType") is not None else r))
    plan, _, out, rec = _run(SECTIONS, ins("^PART 2"))
    assert _attrs(_secs(out)[2].find(W + "pgNumType")) == {"start": "1"}
    assert _failed(rec) == ["V10"] and "not the source with exactly" in _v10(rec)


def test_the_verifier_refuses_what_the_op_must_refuse():
    """A plan that inserted a break the op must refuse (after a table): V10 says the op must refuse."""
    plan, _, out, rec = _run(SECTIONS, ins("^PART 2"))
    d = plan.to_dict()
    d["property_assertions"][0]["select"] = {"paragraphs": {"text_regex": "^PART 2|^2\\.02"}}
    rec = run_suite(SECTIONS, out, d, "s", "o")
    assert _failed(rec) == ["V10"] and "follows a tbl, not a paragraph — the op must refuse" in _v10(rec)


# ---- remove_section_break: end to end ---------------------------------------------------------------------------------

def test_the_cover_joins_the_body():
    plan, frags, out, rec = _run(SECTIONS, rem([0]))
    assert rec.passed, rec.to_markdown()
    src, got = _secs(SECTIONS), _secs(out)
    assert len(got) == 3
    assert body_paragraph(_root(out), "PROJECT REVISION").getparent().tag == W + "body"
    # the body's sectPr: its own references, the cover's first-page header moved in after them (the body had none of
    # that type), the cover's start (none: next page) and no restart — the restart was at the removed break
    joined = got[0]
    cover_first = next(r for r in _refs(src[0]) if r[1] == "first")
    assert _refs(joined) == _refs(src[1]) + [cover_first]
    assert joined.find(W + "pgNumType") is None and joined.find(W + "titlePg") is None
    # the cover's own default header (with its logo) and footer leave the package
    assert plan.members_removed == ["word/header1.xml", "word/_rels/header1.xml.rels", "word/media/image1.png",
                                    "word/footer1.xml"]
    names = DocxPackage(out).member_names
    assert "word/header1.xml" not in names and "word/header2.xml" in names
    a = plan.property_assertions[0]
    assert a["kind"] == "removed_section_breaks" and a["chosen"] == [0] and a["sections"] == 3
    assert a["runs"] == [{"first": 0, "last": 0, "target": 1, "moved": [[0, 2]]}]   # the cover's third reference
    words = " ".join(plan.flags)
    for needle in ("restarted its page numbers at 1; that restart was at the removed break and goes",
                   "take its first-page setting", "will show the header word/header3.xml (section 1's) instead of "
                   "word/header1.xml", "first page of section 0 (now the joined section's first page) will show the "
                   "header word/header3.xml instead of word/header2.xml"):
        assert needle in words, needle
    assert (MAIN, "*", "sections") in frags[0].touches and ("word/header1.xml", "*", "remove") in frags[0].touches
    assert any(d.part == "word/header1.xml" for d in plan.diffs)     # the removed part's blocks: predicted deletions


def test_the_body_joins_the_appendix_and_its_pages_keep_their_headers():
    plan, _, out, rec = _run(SECTIONS, rem([1]))
    assert rec.passed, rec.to_markdown()
    src, got = _secs(SECTIONS), _secs(out)
    joined = got[1]
    assert _refs(joined) == _refs(src[1])                         # the body's references move into the appendix's
    assert _attrs(joined.find(W + "pgNumType")) == {"start": "1"}  # the body's restart: where the joined part begins
    assert sig(joined.find(W + "pgSz")) == sig(src[2].find(W + "pgSz"))      # landscape, the appendix's setup
    assert plan.members_removed == [] and plan.intended_parts == [MAIN]
    assert plan.flags == ["the pages of section 1 join section 2 and take its page size and orientation"]
    assert "the joined section restarts its page numbers at 1, as section 1 did" in plan.notes


def test_every_break_removed_leaves_one_section_and_keeps_the_shared_header():
    plan, _, out, rec = _run(SECTIONS, rem("all"))
    assert rec.passed, rec.to_markdown()
    got = _secs(out)
    assert len(got) == 1
    src = _secs(SECTIONS)
    cover_first = next(r for r in _refs(src[0]) if r[1] == "first")
    assert _refs(got[0]) == _refs(src[3]) + [cover_first]
    assert plan.members_removed == ["word/header1.xml", "word/_rels/header1.xml.rels", "word/media/image1.png",
                                    "word/footer1.xml", "word/footer2.xml"]
    assert "word/header3.xml" in DocxPackage(out).member_names       # the body's header, shared with the last section
    assert all(p.find(f"{W}pPr/{W}sectPr") is None for p in _root(out).iter(W + "p"))
    assert len(list(_root(out).iter(W + "p"))) == len(list(_root(SECTIONS).iter(W + "p")))   # every paragraph stays


def test_a_section_chosen_by_content_and_not_found():
    plan, _, out, rec = _run(SECTIONS, rem({"containing": {"text_regex": "^APPENDIX"}}))
    assert rec.passed, rec.to_markdown()
    assert plan.property_assertions[0]["chosen"] == [2] and len(_secs(out)) == 3
    assert "footer word/footer3.xml (section 3's) instead of word/footer2.xml" in " ".join(plan.flags)
    e = _refused(SECTIONS, rem({"containing": {"text_regex": "^NO SUCH"}}))
    assert e.code == "not-found"
    plan, _, out, rec = _run(SECTIONS, rem({"containing": {"text_regex": "^NO SUCH"}}, allow_missing=True))
    assert rec.passed and DocxPackage(out).read(MAIN) == DocxPackage(SECTIONS).read(MAIN)
    plan, _, out, rec = _run(SINGLE, rem("all"))                 # one section: no break anywhere, nothing to do
    assert rec.passed and "no section break to remove here" in " ".join(plan.notes)


def test_later_sections_inheriting_through_the_joined_one_keep_what_they_show():
    from spec_surgeon.ops.hf_common import effective_refs

    def unlinked(root):                              # the last section names nothing: it shows the body's parts too
        sp = sect_prs(root)[3]
        for ref in [c for c in sp if etree.QName(c).localname in ("headerReference", "footerReference")]:
            sp.remove(ref)
    data = patch_docx(SECTIONS, {MAIN: unlinked})
    before = effective_refs(DocxPackage(data))
    plan, _, out, rec = _run(data, rem([1]))
    assert rec.passed, rec.to_markdown()
    after = effective_refs(DocxPackage(out))
    assert after[1] == before[2] and after[2] == before[3] == before[1]     # the appendix and the end, as they were
    assert plan.members_removed == []


def test_inserting_a_break_and_removing_it_gives_the_source_back():
    _, _, once, rec = _run(SECTIONS, ins("^PART 2"))
    assert rec.passed
    _, _, back, rec = _run(once, rem([1]))
    assert rec.passed, rec.to_markdown()
    assert sig(_root(back)) == sig(_root(SECTIONS))


def test_printer_settings_only_the_joined_section_used_leave_the_package():
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/printerSettings"
    data = add_parts(SECTIONS, {"word/printerSettings/printerSettings1.bin": b"\x00printer\x00"},
                     rels=[("word/_rels/document.xml.rels", "rId900", rel, "printerSettings/printerSettings1.bin")],
                     overrides={"word/printerSettings/printerSettings1.bin":
                                "application/vnd.openxmlformats-officedocument.wordprocessingml.printerSettings"})
    data = with_section_xml(data, 0, '<w:printerSettings r:id="rId900"/>')
    plan, _, out, rec = _run(data, rem([0]))
    assert rec.passed, rec.to_markdown()
    assert "word/printerSettings/printerSettings1.bin" in plan.members_removed
    assert "rId900" in plan.property_assertions[0]["gone_rids"]
    # the same printer settings named by the section that stays too: they stay
    both = with_section_xml(data, 1, '<w:printerSettings r:id="rId900"/>')
    plan, _, out, rec = _run(both, rem([0]))
    assert rec.passed and "word/printerSettings/printerSettings1.bin" not in plan.members_removed


# ---- remove_section_break: refusals, parameters -----------------------------------------------------------------------

def test_remove_refusals():
    e = _refused(SECTIONS, rem([3]))
    assert e.code == "last-section" and "choose that one" in e.reason
    e = _refused(SECTIONS, rem({"containing": {"text_regex": "^PART"}}))          # PART 1/2 and PART 3: the last too
    assert e.code == "last-section"
    e = _refused(with_section_change(SECTIONS, 0), rem([0]))
    assert e.code == "tracked-section" and "w:sectPrChange" in e.reason

    def mark(root):                                  # the cover's break paragraph: its mark a tracked deletion
        p = sect_prs(root)[0].getparent().getparent()
        rpr = etree.SubElement(p.find(W + "pPr"), W + "rPr")
        rpr.append(_el('<w:del w:id="903" w:author="Jack" w:date="2026-09-23T08:00:00Z"/>'))
        p.find(W + "pPr").insert(0, rpr)
    e = _refused(patch_docx(SECTIONS, {MAIN: mark}), rem([0]))
    assert e.code == "tracked-section"

    def in_cell(root):                               # a sectPr in a table cell's paragraph (malformed, but read)
        cell_p = body_paragraph(root, "Schedule 40")
        ppr = cell_p.find(W + "pPr")
        if ppr is None:
            ppr = etree.SubElement(cell_p, W + "pPr")
            cell_p.insert(0, ppr)
        ppr.append(copy.deepcopy(sect_prs(root)[1]))
    e = _refused(patch_docx(SECTIONS, {MAIN: in_cell}), rem([1]))
    assert e.code == "structure"
    for code in ("last-section", "tracked-section", "structure", "not-found"):
        assert code in FAILURE_EXPLANATIONS


def test_the_verifier_refuses_the_last_section_and_flags_the_joined_into_sections_pending_change():
    plan, _, out, rec = _run(SECTIONS, rem([2]))
    d = plan.to_dict()
    d["property_assertions"][0]["select"] = {"sections": [2, 3]}
    rec = run_suite(SECTIONS, out, d, "s", "o")
    assert _failed(rec) == ["V10"] and "ends the document at no break — the op must refuse" in _v10(rec)
    plan, _, out, rec = _run(with_section_change(SECTIONS, 1), rem([0]))
    assert rec.passed, rec.to_markdown()
    assert any("section 1 carries a pending tracked section change" in f for f in plan.flags)


@pytest.mark.parametrize("select, params", [
    ({"sections": "all"}, {"tracked": True}), ({"sections": [9]}, {}), ({"sections": "some"}, {}),
    ({"sections": {"pages": 2}}, {}), ({"sections": "all", "kinds": ["header"]}, {}),
    ({"sections": "all"}, {"allow_missing": 1}), ({"sections": "all"}, {"type": "continuous"}),
])
def test_remove_parameters_are_checked(select, params):
    with pytest.raises(JobException) as e:
        get_op("remove_section_break").plan(DocxPackage(SECTIONS), select, params)
    assert e.value.code == "bad-params"


# ---- remove_section_break: wrong outputs, wrong implementations -------------------------------------------------------

def test_wrong_remove_outputs_are_caught():
    plan, _, out, rec = _run(SECTIONS, rem([0]))
    assert rec.passed

    def no_moved_reference(root):
        sp = sect_prs(root)[0]
        sp.remove([c for c in sp if c.get(W + "type") == "first"][0])
    _caught(SECTIONS, plan, out, no_moved_reference, "not the source with exactly the predicted section change")

    def restart_kept(root):
        sect_prs(root)[0].insert(5, _el('<w:pgNumType w:start="1"/>'))
    _caught(SECTIONS, plan, out, restart_kept, "not the source with exactly")

    def paragraph_gone(root):
        p = body_paragraph(root, "PROJECT REVISION")
        tbl = p.getnext()
        empty = tbl.getnext()
        empty.getparent().remove(empty)                  # the paragraph that held the break deleted as well
    _caught(SECTIONS, plan, out, paragraph_gone, "not the source with exactly")


def test_a_removed_part_left_in_the_package_is_caught():
    plan, _, out, rec = _run(SECTIONS, rem([0]))
    d = plan.to_dict()
    for holder in (d, d["property_assertions"][0]):      # a plan that keeps the cover's footer part
        holder["members_removed"] = [m for m in holder["members_removed"] if m != "word/footer1.xml"]
    rec = run_suite(SECTIONS, out, d, "s", "o")
    assert "V1" in _failed(rec)                          # the output lacks a member the plan keeps
    assert "the members that leave here are" in _v10(rec)


def test_a_join_that_keeps_the_following_sections_start_is_caught(monkeypatch):
    """Planted in apply: the joined section keeps section t's own start — its restart at 1, which was at the removed
    break — as Word's sectPr would if nothing moved. The plan says nothing of starts; V10 rebuilds the expected part."""
    monkeypatch.setattr(rem_module, "set_start", lambda sp, type_el, restart: None)
    plan, _, out, rec = _run(SECTIONS, rem([0]))
    assert _attrs(_secs(out)[0].find(W + "pgNumType")) == {"start": "1"}
    assert _failed(rec) == ["V10"] and "not the source with exactly" in _v10(rec)


def test_runs_that_do_not_join_shared_by_plan_and_apply_are_caught(monkeypatch):
    """Planted, shared: neighbouring chosen sections treated as separate joins (section 0 joins section 1, which is
    itself joined to section 2)."""
    monkeypatch.setattr(rem_module, "runs_of", lambda chosen: [(i, i) for i in sorted(chosen)])
    plan, _, out, rec = _run(SECTIONS, rem([0, 1]))
    assert "V10" in _failed(rec) and "the sections join as" in _v10(rec)


def test_a_part_removal_that_keeps_every_part_shared_by_plan_and_apply_is_caught(monkeypatch):
    """Planted, shared: nothing leaves the package. V1 is content (the relationships still name the parts); V10
    re-derives the relationships and members that go."""
    real = rem_module.plan_removal

    def keep_all(pkg, main, root, removed, kept):
        rp = real(pkg, main, root, removed, kept)
        rp.gone_rids, rp.parts_gone, rp.members, rp.media, rp.diffs = [], [], [], [], []
        return rp
    monkeypatch.setattr(rem_module, "plan_removal", keep_all)
    plan, _, out, rec = _run(SECTIONS, rem([0]))
    assert "word/header1.xml" in DocxPackage(out).member_names
    assert _failed(rec) == ["V10"]
    assert "relationships no longer used here ['rId10', 'rId9'], the plan removes []" in _v10(rec)


# ---- set_page_setup: columns, line numbering, vertical alignment, section start ------------------------------------------

SCHEDULE = build_schedule()


def test_columns_end_to_end():
    plan, frags, out, rec = _run(SCHEDULE, setup([1, 2], columns={"count": 2, "space": 360, "separator": True}))
    assert rec.passed, rec.to_markdown()
    for i in (1, 2):
        assert _attrs(_secs(out)[i].find(W + "cols")) == {"space": "360", "num": "2", "sep": "1"}
    assert _secs(out)[0].find(W + "cols").attrib == _secs(SCHEDULE)[0].find(W + "cols").attrib
    a = plan.property_assertions[0]
    assert a["per_section"]["1"] == {"cols": {"exact": {"space": "360", "num": "2", "sep": "1"}}}
    assert (MAIN, "sectPr:1", "cols") in frags[0].touches
    # unequal columns become equal: the widths and w:equalWidth go; back to one column the line goes too
    data = with_section_xml(SCHEDULE, 1, '<w:cols w:num="2" w:space="720" w:equalWidth="0" w:sep="1">'
                                         '<w:col w:w="3000" w:space="720"/><w:col w:w="5640"/></w:cols>')
    plan, _, out, rec = _run(data, setup([1], columns={"count": 3}))
    assert rec.passed, rec.to_markdown()
    cols = _secs(out)[1].find(W + "cols")
    assert _attrs(cols) == {"num": "3", "space": "720", "sep": "1"} and not len(cols)
    plan, _, out, rec = _run(data, setup([1], columns={"count": 1}))
    assert rec.passed, rec.to_markdown()
    cols = _secs(out)[1].find(W + "cols")
    assert _attrs(cols) == {"space": "720"} and not len(cols)


def test_columns_already_as_asked_and_narrow_columns():
    plan, _, out, rec = _run(SCHEDULE, setup("all", columns={"count": 1}))
    assert rec.passed and plan.property_assertions[0]["sections"] == []
    assert DocxPackage(out).read(MAIN) == DocxPackage(SCHEDULE).read(MAIN)
    assert any("already in effect in section(s) [0, 1, 2, 3]" in n for n in plan.notes)
    plan, _, out, rec = _run(SCHEDULE, setup([1], columns={"count": 12, "space": 720}))
    assert rec.passed and any("12 columns leave each column" in f and "less than half an inch" in f
                              for f in plan.flags)
    plan, _, _, rec = _run(SCHEDULE, setup([1], columns={"count": 2}))
    assert rec.passed and not plan.flags


def test_line_numbering_end_to_end():
    plan, _, out, rec = _run(SCHEDULE, setup([2], line_numbering={"count_by": 5, "start": 1, "restart": "new_section",
                                                                    "distance": 360}))
    assert rec.passed, rec.to_markdown()
    assert _attrs(_secs(out)[2].find(W + "lnNumType")) == {"countBy": "5", "restart": "newSection", "distance": "360"}
    kids = _kids(_secs(out)[2])
    assert kids.index("pgMar") < kids.index("lnNumType") < kids.index("cols")
    # Word shows w:start plus one: the first number 10 is written 9; restarting on each page is the default (absent)
    data = with_section_xml(SCHEDULE, 2, '<w:lnNumType w:countBy="2" w:restart="continuous"/>')
    plan, _, out, rec = _run(data, setup([2], line_numbering={"start": 10, "restart": "new_page"}))
    assert rec.passed, rec.to_markdown()
    assert _attrs(_secs(out)[2].find(W + "lnNumType")) == {"countBy": "2", "start": "9"}
    plan, _, out, rec = _run(data, setup([2], line_numbering="none"))
    assert rec.passed and _secs(out)[2].find(W + "lnNumType") is None
    plan, _, _, rec = _run(data, setup([2], line_numbering={"count_by": 2, "restart": "continuous"}))
    assert rec.passed and plan.property_assertions[0]["sections"] == []           # already as asked
    plan, _, out, rec = _run(SCHEDULE, setup([2], line_numbering={}))               # on, every line
    assert rec.passed and _attrs(_secs(out)[2].find(W + "lnNumType")) == {"countBy": "1"}


def test_vertical_alignment_and_section_start():
    plan, _, out, rec = _run(SCHEDULE, setup([2], vertical_alignment="center", section_start="continuous"))
    assert rec.passed, rec.to_markdown()
    sp = _secs(out)[2]
    assert _attrs(sp.find(W + "vAlign")) == {"val": "center"} and _attrs(sp.find(W + "type")) == {"val": "continuous"}
    assert _kids(sp).index("type") < _kids(sp).index("pgSz") and _kids(sp).index("cols") < _kids(sp).index("vAlign")
    # the defaults are absent elements; an explicit default is already as asked and left alone
    plan, _, back, rec = _run(out, setup([2], vertical_alignment="top", section_start="next_page"))
    assert rec.passed and sig(_secs(back)[2]) == sig(_secs(SCHEDULE)[2])
    assert plan.property_assertions[0]["per_section"]["2"] == {"vAlign": {"absent": True}, "type": {"absent": True}}
    explicit = with_section_xml(SCHEDULE, 2, '<w:type w:val="nextPage"/><w:vAlign w:val="top"/>')
    plan, _, _, rec = _run(explicit, setup([2], vertical_alignment="top", section_start="next_page"))
    assert rec.passed and plan.property_assertions[0]["sections"] == []
    plan, _, _, rec = _run(SCHEDULE, setup([0, 2], section_start="even_page"))
    assert rec.passed and any("Word adds a blank page" in n for n in plan.notes)
    assert any(n.startswith("section 0 is the first section") for n in plan.notes)


def test_wrong_layout_outputs_are_caught():
    plan, _, out, rec = _run(SCHEDULE, setup([2], columns={"count": 2, "separator": True},
                                             line_numbering={"start": 5}, vertical_alignment="bottom"))
    assert rec.passed, rec.to_markdown()

    def with_child(root):
        sect_prs(root)[2].find(W + "cols").append(_el('<w:col w:w="4000"/>'))
    _caught(SCHEDULE, plan, out, with_child, "section 2 cols: should have no children")

    def start_as_typed(root):
        sect_prs(root)[2].find(W + "lnNumType").set(W + "start", "5")
    _caught(SCHEDULE, plan, out, start_as_typed, "section 2 lnNumType: expected")

    def no_alignment(root):
        sp = sect_prs(root)[2]
        sp.remove(sp.find(W + "vAlign"))
    _caught(SCHEDULE, plan, out, no_alignment, "section 2 vAlign: should be present")

    def other_section(root):
        sect_prs(root)[1].find(W + "cols").set(W + "num", "2")
    _caught(SCHEDULE, plan, out, other_section)


def test_a_line_start_rule_shared_by_plan_and_apply_is_caught(monkeypatch):
    """Planted, shared: the first number written as the user typed it (w:start 5 for 5) — Word would show 6."""
    real = page_module.layout_specs

    def as_typed(sp, params):
        out = real(sp, params)
        if "lnNumType" in out and "start" in (params.get("line_numbering") or {}):
            out["lnNumType"]["exact"]["start"] = str(params["line_numbering"]["start"])
        return out
    monkeypatch.setattr(page_module, "layout_specs", as_typed)
    plan, _, out, rec = _run(SCHEDULE, setup([2], line_numbering={"start": 5}))
    assert _attrs(_secs(out)[2].find(W + "lnNumType"))["start"] == "5"
    assert _failed(rec) == ["V10"] and "section 2 must get" in _v10(rec) and "the plan gives" in _v10(rec)


def test_a_default_written_explicitly_shared_by_plan_and_apply_is_caught(monkeypatch):
    """Planted, shared: the top alignment written as an element instead of removing it."""
    real = page_module.layout_specs

    def explicit(sp, params):
        out = real(sp, params)
        if out.get("vAlign") == {"absent": True}:
            out["vAlign"] = {"exact": {"val": "top"}}
        return out
    monkeypatch.setattr(page_module, "layout_specs", explicit)
    data = with_section_xml(SCHEDULE, 2, '<w:vAlign w:val="center"/>')
    plan, _, out, rec = _run(data, setup([2], vertical_alignment="top"))
    assert _attrs(_secs(out)[2].find(W + "vAlign")) == {"val": "top"}
    assert _failed(rec) == ["V10"] and "must get {'vAlign': {'absent': True}}" in _v10(rec)


@pytest.mark.parametrize("params", [
    {"columns": 2}, {"columns": {"space": 720}}, {"columns": {"count": 0}}, {"columns": {"count": 46}},
    {"columns": {"count": 2, "space": -1}}, {"columns": {"count": 2, "separator": "yes"}},
    {"columns": {"count": 1, "space": 720}}, {"columns": {"count": 1, "separator": True}},
    {"columns": {"count": 2, "gutter": 1}}, {"columns": {"count": True}},
    {"line_numbering": "off"}, {"line_numbering": {"count_by": 0}}, {"line_numbering": {"start": 0}},
    {"line_numbering": {"restart": "each_page"}}, {"line_numbering": {"distance": "auto"}},
    {"line_numbering": {"every": 5}}, {"vertical_alignment": "middle"}, {"section_start": "new_column"},
    {"section_start": "nextPage"},
])
def test_layout_parameters_are_checked(params):
    with pytest.raises(JobException) as e:
        get_op("set_page_setup").plan(DocxPackage(SCHEDULE), {"sections": "all"}, params)
    assert e.value.code == "bad-params"


def test_the_verifier_refuses_layout_parameters_the_op_must_refuse():
    plan, _, out, rec = _run(SCHEDULE, setup([2], columns={"count": 2}))
    d = plan.to_dict()
    d["property_assertions"][0]["params"]["columns"] = {"count": 1, "separator": True}
    rec = run_suite(SCHEDULE, out, d, "s", "o")
    assert _failed(rec) == ["V10"] and "the op must refuse" in _v10(rec)


# ---- conflicts and composition (rule 9) ---------------------------------------------------------------------------------

def test_a_break_beside_a_page_setup_of_the_same_section_is_a_conflict_and_composes_into_steps():
    pkg = DocxPackage(SECTIONS)
    a = get_op("insert_section_break").plan(pkg, {"paragraphs": {"text_regex": "^PART 2"}}, {})
    b = get_op("set_page_setup").plan(pkg, {"sections": [1]}, {"columns": {"count": 2}})
    with pytest.raises(JobException) as e:
        merge_fragments([a, b])
    assert e.value.code == "conflict" and "as a whole" in e.value.reason
    # the engine composes the user's order into steps: the break first, then the new section by its content
    spec = _spec(ins("^PART 2"), setup({"containing": {"text_regex": "^PART 2"}}, columns={"count": 2}))
    chain = split_into_stages(pkg, spec)
    assert chain is not None and len(chain["stages"]) == 2
    current = pkg
    for (plan, frags), stage in zip(plan_chain(pkg, chain), chain["stages"]):
        out = apply_spec(current, frags)
        rec = run_suite(current.data, out.output_bytes(), plan.to_dict(), "s", "o")
        assert rec.passed, rec.to_markdown()
        current = DocxPackage(out.output_bytes())
    got = _secs(current.data)
    assert _attrs(got[2].find(W + "cols"))["num"] == "2" and _attrs(got[1].find(W + "cols")).get("num") is None


def test_a_break_beside_a_text_edit_runs_as_its_own_step():
    pkg = DocxPackage(SECTIONS)
    spec = _spec(ins("^PART 2"), {"op": "replace_text", "params": {"find": "quick response", "replace": "standard "
                                                                                                    "response"}})
    with pytest.raises(JobException) as e:
        plan_spec(pkg, spec)
    assert e.value.code == "conflict"
    chain = split_into_stages(pkg, spec)
    assert chain is not None and len(chain["stages"]) == 2


def test_a_removed_break_beside_a_header_removal_is_a_conflict():
    pkg = DocxPackage(SECTIONS)
    a = get_op("remove_section_break").plan(pkg, {"sections": [0]}, {})
    b = get_op("remove_header_footer").plan(pkg, {"sections": [0], "kinds": ["header"], "types": ["first"]}, {})
    with pytest.raises(JobException) as e:
        merge_fragments([a, b])
    assert e.value.code == "conflict"
    with pytest.raises(JobException) as e:
        merge_fragments([b, a])
    assert e.value.code == "conflict"


def test_a_plan_that_found_nothing_still_declares_what_it_read():
    pkg = DocxPackage(SINGLE)
    none_found = get_op("insert_section_break").plan(pkg, {"paragraphs": {"text_regex": "^APPENDIX C"}},
                                                     {"allow_missing": True})
    assert none_found.touches == [(MAIN, "*", "verify:text")]
    edit = get_op("replace_text").plan(pkg, {}, {"find": "Appendix B text", "replace": "Appendix C text"})
    with pytest.raises(JobException) as e:
        merge_fragments([none_found, edit])
    assert e.value.code == "conflict"
    no_break = get_op("remove_section_break").plan(pkg, {}, {})
    assert no_break.touches == [(MAIN, "*", "verify-props:sections")]
    a_break = get_op("insert_section_break").plan(pkg, {"paragraphs": {"text_regex": "^APPENDIX B"}}, {})
    with pytest.raises(JobException) as e:
        merge_fragments([no_break, a_break])
    assert e.value.code == "conflict"
    assert merge_fragments([no_break, edit]).ops                 # a text edit reaches no section


def test_two_section_ops_never_share_a_plan():
    pkg = DocxPackage(SECTIONS)
    a = get_op("insert_section_break").plan(pkg, {"paragraphs": {"text_regex": "^PART 2"}}, {})
    b = get_op("remove_section_break").plan(pkg, {"sections": [2]}, {})
    with pytest.raises(JobException) as e:
        merge_fragments([a, b])
    assert e.value.code == "conflict"


# ---- around the ops --------------------------------------------------------------------------------------------------------

def test_every_refusal_code_of_the_section_ops_is_explained():
    codes = set()
    for module in (ins_module, rem_module, page_module):
        for n in ast.walk(ast.parse(Path(module.__file__).read_text(encoding="utf-8"))):
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "JobException" and len(n.args) > 1:
                assert isinstance(n.args[1], ast.Constant), (module.__name__, n.lineno)
                codes.add(n.args[1].value)
    assert {"break-position", "tracked-section", "last-section", "textbox", "not-found", "structure",
            "bad-params"} == codes
    assert sorted(c for c in codes if c not in FAILURE_EXPLANATIONS) == []


def test_catalog_schema_and_assumptions():
    cat = op_catalog(examples=False)
    for op in ("insert_section_break", "remove_section_break"):
        assert op in cat["ops"] and COMMON_ASSUMPTIONS[op]
    words = " ".join(COMMON_ASSUMPTIONS["set_page_setup"])
    for needle in ("columns {count, space", "line_numbering {count_by", "'none'", "vertical_alignment",
                   "section_start next_page | continuous | even_page | odd_page"):
        assert needle in words, needle
    assert "set_page_setup as a later step" in " ".join(COMMON_ASSUMPTIONS["insert_section_break"])
    assert "remove_section_break" in SELECTOR_GRAMMAR["sections"]
    props = cat["ops"]["set_page_setup"]["params"]["properties"]
    assert {"columns", "line_numbering", "vertical_alignment", "section_start"} <= set(props)
    validate_spec(_spec(ins("^APPENDIX", type="continuous"), rem([0]),
                        setup([1], columns={"count": 2, "separator": True}, line_numbering="none",
                              vertical_alignment="center", section_start="odd_page")))


def test_the_ops_place_no_words():
    from spec_surgeon import compiler
    assert compiler.placed_words(_spec(ins("^APPENDIX"), rem("all"), setup("all", columns={"count": 2}))) == []


def test_a_spec_runs_through_the_batch_pipeline(tmp_path):
    """Plan, apply, verify, write and deliver in a worker, from a spec (the plan and its transform data go through JSON)."""
    from spec_surgeon.batch import run_batch
    for n, spec in enumerate((_spec(ins("^PART 2|^3\\.01", type="continuous")), _spec(rem([0, 2])),
                              _spec(setup([2], columns={"count": 2}, line_numbering={"count_by": 5})))):
        path = tmp_path / f"in{n}" / "21 13 13 – SPRINKLERS.docx"
        path.parent.mkdir()
        path.write_bytes(SECTIONS)
        batch = run_batch([str(path)], "edit-spec", {}, tmp_path / f"out{n}", workers=1, renderer="none",
                          do_render=False, spec=spec)
        f = batch.files[0]
        assert f.status == "PASS", f.record.to_markdown() if f.record else f.exception


# ---- LibreOffice ---------------------------------------------------------------------------------------------------------

def _render(tmp_path, name: str, data: bytes):
    from spec_surgeon.render import libreoffice
    fitz = pytest.importorskip("fitz")
    path = tmp_path / f"{name}.docx"
    path.write_bytes(data)
    pdfs = libreoffice.render_pdfs([path], tmp_path / f"pdf-{name}", workers=1)
    if len(pdfs) != 1:
        pytest.skip("LibreOffice did not render here")
    return fitz.open(str(pdfs[path]))


def _first_line(page) -> str:
    blocks = sorted((b for b in page.get_text("blocks") if b[4].strip()), key=lambda b: (b[1], b[0]))
    body = [b for b in blocks if b[1] > 60]                   # below the header (half an inch from the edge)
    return body[0][4].strip() if body else ""


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
def test_libreoffice_puts_the_new_section_on_a_new_page_and_joins_pages_again(tmp_path):
    src = _render(tmp_path, "src", SINGLE)
    _, _, out, rec = _run(SINGLE, ins("^APPENDIX B"))
    assert rec.passed
    got = _render(tmp_path, "out", out)
    assert len(got) == len(src) + 1
    assert any(_first_line(page).startswith("APPENDIX B") for page in got)
    assert not any(_first_line(page).startswith("APPENDIX B") for page in src)
    # a continuous break keeps the pages
    _, _, same, rec = _run(SINGLE, ins("^APPENDIX B", type="continuous"))
    assert rec.passed and len(_render(tmp_path, "continuous", same)) == len(src)
    # every break of the four-section file removed: the cover, body and appendix no longer start pages of their own
    four = _render(tmp_path, "four", SECTIONS)
    _, _, one, rec = _run(SECTIONS, rem("all"))
    assert rec.passed
    assert len(_render(tmp_path, "one", one)) < len(four)


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
def test_libreoffice_sets_two_columns(tmp_path):
    data = build_columns_text()
    _, _, out, rec = _run(data, setup("all", columns={"count": 2, "space": 720}))
    assert rec.passed
    page = _render(tmp_path, "cols", out)[0]
    lefts = sorted({round(b[0]) for b in page.get_text("blocks") if b[4].strip()})
    width = page.rect.width
    assert lefts[0] < width / 4 and any(x > width / 2 for x in lefts), lefts
    one = _render(tmp_path, "one-col", data)[0]
    assert not any(round(b[0]) > one.rect.width / 2 for b in one.get_text("blocks") if b[4].strip())
