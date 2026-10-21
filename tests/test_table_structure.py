"""The table structure ops of built-in batch 6 (F5 — docs/BUILTIN_OPS_BATCH6_DESIGN.md §6): delete_tables,
delete_table_rows, insert_table_rows, insert_table and their V10 kinds (verify/tablestructure.py).

End to end on the structure fixture (tests/fx_table_structure.py), tracked and untracked; the row selector and the grid
on both sides agreeing on every row; refusals with their codes; a wrong OUTPUT per rule and wrong IMPLEMENTATIONS shared
by plan and apply caught; parameters; plan-time conflicts with the table formatting ops and their composition into
steps; the catalog, placed words, the batch pipeline and a LibreOffice render. Accepting and rejecting what these ops
write is tests/test_table_rows_resolve.py."""
from __future__ import annotations

import ast
import io
import itertools
import json
import shutil
import zipfile
from pathlib import Path
from typing import List, Tuple

import pytest
from lxml import etree

from fx_table_structure import TITLE, build_structure, xlsx
from spec_surgeon.catalog import COMMON_ASSUMPTIONS, op_catalog, op_sheet
from spec_surgeon.compiler import placed_words
from spec_surgeon.editspec import apply_spec, plan_chain, plan_spec, split_into_stages, validate_spec
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import insert_table as it_module
from spec_surgeon.ops import table_edit, table_rows
from spec_surgeon.ops.selectors import StyleSheet
from spec_surgeon.ops.table_select import own_rows, style_chain
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.report import FAILURE_EXPLANATIONS
from spec_surgeon.verify import run_suite
from spec_surgeon.verify import tablerows as vtr
from spec_surgeon.verify.props import Styles
from spec_surgeon.verify.reader import VPackage
from spec_surgeon.verify.tableselect import VTables, v_style_chain

MAIN = "word/document.xml"
HEADER = "word/header1.xml"
AUTHOR = "F5 Tester"
SCHEDULE = {"first_row_regex": "^Tag"}           # table 0
ZONES = {"first_row_regex": "^Zone"}             # table 1 (a vertical merge, a spanning cell)
VALVES = {"first_row_regex": "^Valve"}           # table 2 (holds table 3)
NESTED = {"first_row_regex": "^Size"}            # table 3
REFS = {"first_row_regex": "^Ref"}               # table 4 (Arch Header; a bookmark crossing its edge)
ITEMS = {"first_row_regex": "^Item"}             # table 5 (section 2, landscape)


@pytest.fixture(scope="module")
def src() -> bytes:
    return build_structure()


def run_ops(data: bytes, specs: List[Tuple[str, dict, dict]]):
    pkg = DocxPackage(data)
    frags = [get_op(o).plan(pkg, sel, par) for o, sel, par in specs]
    plan = merge_fragments(frags)
    work = pkg.copy()
    for f in frags:
        get_op(f.op).apply(work, f)
    out = work.output_bytes()
    return plan, out, run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")


def run(data: bytes, op: str, select: dict, params: dict):
    return run_ops(data, [(op, select, {"author": AUTHOR, **params})])


def failed(rec):
    return [c.id for c in rec.checks if c.status == "FAIL"]


def v10(rec) -> str:
    c = next(c for c in rec.checks if c.id == "V10")
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


def frag_xml(xml: str):
    return etree.fromstring(f'<x xmlns:w="{W[1:-1]}">{xml}</x>')[0]


def tbls(root) -> list:
    return list(root.iter(W + "tbl"))


def tables(data: bytes, part: str = MAIN) -> list:
    return tbls(DocxPackage(data).xml(part))


def rows(tbl) -> list:
    return [c for c in tbl if c.tag == W + "tr"]


def cells(tr) -> list:
    return [c for c in tr if c.tag == W + "tc"]


def local(e) -> str:
    return etree.QName(e).localname


def kids(el) -> List[str]:
    return [local(c) for c in el] if el is not None else []


def text_of(el) -> str:
    return "".join(t.text or "" for t in el.iter(W + "t", W + "delText"))


def row_texts(tbl) -> List[str]:
    return ["|".join(text_of(tc) for tc in cells(tr)) for tr in rows(tbl)]


def _caught(src, plan, out, part, fn, needle=None):
    bad = mutate(out, part, fn)
    rec = run_suite(src, bad, plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec), rec.to_markdown()
    if needle:
        assert needle in v10(rec), v10(rec)
    return rec


def refused(data, op, select, params, code):
    with pytest.raises(JobException) as e:
        get_op(op).plan(DocxPackage(data), select, {"author": AUTHOR, **params})
    assert e.value.code == code, e.value.reason
    return e.value


# ---- the row selector and the grid, both sides ------------------------------------------------------------------------

ROW_PREDICATES = ["all", {}, {"index_range": [0, 0]}, {"index_range": [1, 2]}, {"index_range": [3, 9]},
                  {"text_regex": "^FP"}, {"text_regex": "pump\\t"}, {"text_regex": "\\t$"}, {"header": True},
                  {"header": False}, {"header": True, "index_range": [1, 5]}, {"text_regex": "R-1", "header": True},
                  {"text_regex": "^Totals\\t3$"}]


def test_the_two_sides_of_the_row_selector_agree_on_every_row(src):
    pkg, vpkg = DocxPackage(src), VPackage(src)
    sheet, styles = StyleSheet(pkg), Styles(vpkg.xml("word/styles.xml"))
    for part in (MAIN, HEADER):
        root = pkg.xml(part)
        vt = VTables(vpkg.xml(part))
        assert len(tbls(root)) == len(vt.tables)
        for tbl, vtbl in zip(tbls(root), vt.tables):
            chain, _ = style_chain(tbl, sheet)
            vchain = v_style_chain(vtbl, styles)
            for r, (tr, vtr_) in enumerate(zip(own_rows(tbl), rows(vtbl))):
                assert table_rows.row_text(tr) == vtr.v_row_text(vtr_, vt.text)
                assert table_rows.is_header_row(tr, chain) == vtr.v_header_row(vtr_, vchain)
                for tc, vtc in zip(own_rows(tbl)[r].findall(W + "tc"), cells(vtr_)):
                    assert table_rows.is_merged(tc) == vtr.v_merged(vtc)
            for pred in ROW_PREDICATES:
                assert table_rows.selected_rows(tbl, pred, chain) == vtr.v_rows_chosen(vtbl, pred, vchain, vt.text), pred
            assert table_rows.merge_groups(tbl) == vtr.v_merges(vtbl)
            n = len(own_rows(tbl))
            for k in range(n + 1):
                for gone in itertools.combinations(range(n), k):
                    assert table_rows.merge_crossings(tbl, gone) == vtr.v_cuts(vtbl, set(gone)), gone


def test_the_fixture_reads_as_documented(src):
    root = DocxPackage(src).xml(MAIN)
    sheet = StyleSheet(DocxPackage(src))
    t = tbls(root)
    assert [row_texts(x)[0] for x in t] == ["Tag|Description|Capacity", "Zone|Area|Notes", "Valve|Detail",
                                            "Size|4 in", "Ref|Value", "Item|Count"]
    assert table_rows.merge_groups(t[1]) == [[1, 2, 3]]
    assert table_rows.merge_crossings(t[1], [1]) == [1] and table_rows.merge_crossings(t[1], [2, 3]) == []
    assert table_rows.merge_crossings(t[1], [1, 2, 3]) == [] and table_rows.grid_columns(rows(t[1])[4]) == [0, 2]
    assert table_rows.selected_rows(t[0], {"header": True}, style_chain(t[0], sheet)[0]) == [0]
    assert table_rows.selected_rows(t[4], {"header": True}, style_chain(t[4], sheet)[0]) == [0, 1]   # by its style


@pytest.mark.parametrize("pred", ["some", {"index": 1}, {"index_range": [2, 1]}, {"index_range": [0]},
                                  {"index_range": [-1, 2]}, {"text_regex": "("}, {"text_regex": 3}, {"header": "yes"}])
def test_row_selectors_are_checked_on_both_sides(src, pred):
    with pytest.raises(JobException) as e:
        table_rows.check_row_selector(pred)
    assert e.value.code == "bad-params"
    with pytest.raises(ValueError):
        vtr.v_check_rows(pred)
    refused(src, "delete_table_rows", {"rows": pred}, {}, "bad-params")


# ---- delete_tables ----------------------------------------------------------------------------------------------------

def test_a_tracked_table_deletion_is_recorded_as_word_records_it(src):
    plan, out, rec = run(src, "delete_tables", {"tables": SCHEDULE}, {})
    assert rec.passed, rec.to_markdown()
    assert plan.mode == "content" and plan.geometry_reference == "none"
    t0 = tables(out)[0]
    assert all(r.find(f"{W}trPr/{W}del").get(W + "author") == AUTHOR for r in rows(t0))
    assert kids(rows(t0)[0].find(W + "trPr")) == ["cantSplit", "tblHeader", "del"]          # schema order kept
    pars = list(t0.iter(W + "p"))
    assert len(pars) == 12 and all(p.find(f"{W}pPr/{W}rPr/{W}del") is not None for p in pars)
    assert all(r.getparent().tag == W + "del" for r in t0.iter(W + "r")) and not list(t0.iter(W + "t"))
    assert text_of(t0) == "TagDescriptionCapacityFP-1Fire pump500 gpmFP-2Jockey pump25 gpmAC-1Air compressor10 cfm"
    # 4 rows + 12 run groups deleted, 12 marks
    assert plan.rev_delta["del"] == 16 and plan.rev_delta["del_mark"] == 12 and not plan.diffs
    assert len(plan.accepted_views) == 12 and plan.property_assertions[0]["changed"] == {MAIN: [0]}


def test_an_untracked_table_deletion_removes_the_table(src):
    plan, out, rec = run(src, "delete_tables", {"tables": SCHEDULE}, {"tracked": False})
    assert rec.passed, rec.to_markdown()
    assert plan.mode == "untracked" and len(tables(out)) == 5
    assert [d.before for d in plan.diffs][:3] == ["Tag", "Description", "Capacity"] and len(plan.diffs) == 12
    body = DocxPackage(out).xml(MAIN).find(W + "body")
    assert [text_of(c) for c in body][:3] == [TITLE, "1.1 SCHEDULES", "Zone schedule"]
    assert any("UNTRACKED deletion of 1 table(s)" in f for f in plan.flags)


def test_a_nested_table_goes_with_its_table_and_alone_leaves_its_cell_a_paragraph(src):
    plan, out, rec = run(src, "delete_tables", {"tables": {"text_regex": "^(Valve|Size)$"}}, {"tracked": False})
    assert rec.passed, rec.to_markdown()
    assert plan.property_assertions[0]["changed"] == {MAIN: [2]} and len(tables(out)) == 4
    for tracked in (True, False):
        plan, out, rec = run(src, "delete_tables", {"tables": NESTED}, {"tracked": tracked})
        assert rec.passed, rec.to_markdown()
        cell = cells(rows(tables(out)[2])[1])[1]
        assert kids(cell)[-1] == "p" and text_of(cell).endswith("See detail.")


def test_a_bookmark_crossing_the_edge_keeps_its_place(src):
    plan, out, rec = run(src, "delete_tables", {"tables": REFS}, {"tracked": False})
    assert rec.passed, rec.to_markdown()
    body = DocxPackage(out).xml(MAIN).find(W + "body")
    start = body.find(W + "bookmarkStart")                   # the table's place, between the two paragraphs
    assert start is not None and start.get(W + "name") == "_RefTagList"
    assert text_of(start.getprevious()) == "Reference table" and text_of(start.getnext()) == "The tags above."
    plan, out, rec = run(src, "delete_tables", {"tables": REFS}, {})
    assert rec.passed and tables(out)[4].find(f".//{W}bookmarkStart") is not None    # tracked: it stays put


def test_a_bookmark_wholly_inside_goes_and_a_reference_to_it_is_flagged(src):
    def inside(root):                                         # the bookmark's end moved into the table too
        end = next(root.iter(W + "bookmarkEnd"))
        end.getparent().remove(end)
        tbls(root)[4].find(f".//{W}tc/{W}p").append(end)
    data = mutate(src, MAIN, inside)
    plan, out, rec = run(data, "delete_tables", {"tables": REFS}, {"tracked": False})
    assert rec.passed, rec.to_markdown()
    assert not list(DocxPackage(out).xml(MAIN).iter(W + "bookmarkStart"))
    assert any("_RefTagList" in f and "cross-reference" in f for f in plan.flags)


def test_every_table_and_the_header_table(src):
    for tracked in (True, False):
        plan, out, rec = run(src, "delete_tables", {"tables": "all"}, {"tracked": tracked})
        assert rec.passed, rec.to_markdown()
        assert plan.property_assertions[0]["expected_tables"] == 5          # table 3 goes inside table 2
        plan, out, rec = run(src, "delete_tables", {"scope": ["headers"]}, {"tracked": tracked})
        assert rec.passed, rec.to_markdown()
        assert DocxPackage(out).read(MAIN) == DocxPackage(src).read(MAIN)


def _comment_in_cell(root):
    p = tbls(root)[0].find(f".//{W}tc/{W}p")
    p.insert(0, frag_xml('<w:commentRangeStart w:id="5"/>'))
    p.append(frag_xml('<w:commentRangeEnd w:id="5"/>'))


def _note_in_cell(root):
    tbls(root)[0].find(f".//{W}tc/{W}p").append(frag_xml('<w:r><w:footnoteReference w:id="2"/></w:r>'))


def _field_from_cell(root):
    t0 = tbls(root)[0]
    t0.find(f".//{W}tc/{W}p").append(frag_xml('<w:r><w:fldChar w:fldCharType="begin"/></w:r>'))
    after = t0.getnext()
    after.append(frag_xml('<w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'))
    after.append(frag_xml('<w:r><w:fldChar w:fldCharType="end"/></w:r>'))


def _pending_in_cell(root):
    run = tbls(root)[0].find(f".//{W}r")
    ins = frag_xml('<w:ins w:id="900" w:author="Jack" w:date="2026-09-23T08:00:00Z"/>')
    run.addprevious(ins)
    ins.append(run)


def _link_in_cell(root):
    run = tbls(root)[0].find(f".//{W}r")
    link = frag_xml('<w:hyperlink w:anchor="x"/>')
    run.addprevious(link)
    link.append(run)


def _box_in_cell(root):
    tbls(root)[0].find(f".//{W}r").append(frag_xml('<w:pict><w:txbxContent><w:p/></w:txbxContent></w:pict>'))


def _section_in_cell(root):
    p = tbls(root)[0].find(f".//{W}tc/{W}p")
    p.insert(0, frag_xml("<w:pPr><w:sectPr/></w:pPr>"))


@pytest.mark.parametrize("plant,params,code", [
    (_comment_in_cell, {}, "table-anchor"), (_comment_in_cell, {"tracked": False}, "table-anchor"),
    (_note_in_cell, {"tracked": False}, "table-anchor"), (_field_from_cell, {}, "table-anchor"),
    (_field_from_cell, {"tracked": False}, "table-anchor"), (_pending_in_cell, {}, "existing-revisions"),
    (_link_in_cell, {}, "structure"), (_box_in_cell, {}, "textbox"), (_section_in_cell, {"tracked": False},
                                                                       "section-break"),
])
def test_what_a_deleted_table_may_hold(src, plant, params, code):
    refused(mutate(src, MAIN, plant), "delete_tables", {"tables": SCHEDULE}, params, code)


@pytest.mark.parametrize("plant,params", [(_note_in_cell, {}), (_pending_in_cell, {"tracked": False}),
                                          (_link_in_cell, {"tracked": False}), (_box_in_cell, {"tracked": False})])
def test_what_the_other_mode_takes_in_its_stride(src, plant, params):
    plan, out, rec = run(mutate(src, MAIN, plant), "delete_tables", {"tables": SCHEDULE}, params)
    assert rec.passed, rec.to_markdown()


def test_a_table_cannot_leave_its_container_broken(src):
    def side_by_side(root):                                   # tables 0, 1 and 2 with no paragraph between them
        for t in ("Zone schedule", "Valve schedule"):
            p = next(p for p in root.iter(W + "p") if text_of(p) == t)
            p.getparent().remove(p)
    data = mutate(src, MAIN, side_by_side)
    e = refused(data, "delete_tables", {"tables": ZONES}, {"tracked": False}, "table-structure")
    assert "side by side" in e.reason
    plan, out, rec = run(data, "delete_tables", {"tables": ZONES}, {})            # tracked: nothing moves yet
    assert rec.passed

    def bare_cell(root):                                      # the nested table alone in its cell
        p = next(p for p in root.iter(W + "p") if text_of(p) == "See detail.")
        p.getparent().remove(p)
    refused(mutate(src, MAIN, bare_cell), "delete_tables", {"tables": NESTED}, {"tracked": False}, "table-structure")


def test_a_table_in_a_text_box(src):
    def boxed(root):
        t = tbls(root)[5]
        p = next(p for p in root.iter(W + "p") if text_of(p) == "EQUIPMENT")
        box = frag_xml('<w:r><w:pict><w:txbxContent/></w:pict></w:r>')
        p.append(box)
        t.getparent().remove(t)
        box.find(f".//{W}txbxContent").append(t)
        etree.SubElement(box.find(f".//{W}txbxContent"), W + "p")
    data = mutate(src, MAIN, boxed)
    refused(data, "delete_tables", {"tables": {"first_row_regex": "^Item"}}, {}, "textbox")
    plan, out, rec = run(data, "delete_tables", {}, {"tracked": False})
    assert rec.passed and any("text box left alone" in f for f in plan.flags)


def test_nothing_to_delete(src):
    refused(src, "delete_tables", {"tables": {"first_row_regex": "^Nothing"}}, {}, "not-found")
    plan, out, rec = run(src, "delete_tables", {"tables": {"first_row_regex": "^Nothing"}}, {"allow_missing": True})
    assert rec.passed and out == src


@pytest.mark.parametrize("op", ["delete_tables", "delete_table_rows", "insert_table_rows", "insert_table"])
@pytest.mark.parametrize("params", [{"tracked": "yes"}, {"author": 5}, {"date_convention": "local"},
                                    {"allow_missing": 1}, {"colour": "red"}])
def test_the_tracking_parameters_are_checked(src, op, params):
    select = {"tables": SCHEDULE, "rows": {"index_range": [1, 1]}} if "rows" in op else \
        {"paragraphs": {"text_regex": "^1\\.2"}} if op == "insert_table" else {"tables": SCHEDULE}
    extra = {"rows": [["a", "b", "c"]]} if op.startswith("insert") else {}
    refused(src, op, select, {**extra, **params}, "bad-params")


def test_the_selectors_are_checked(src):
    for select in ({"paragraphs": "all"}, {"tables": "some"}, {"tables": {"index": 1}}, {"scope": []}):
        refused(src, "delete_tables", select, {}, "bad-params")


# ---- delete_table_rows ------------------------------------------------------------------------------------------------

def test_rows_by_their_text_tracked_and_untracked(src):
    plan, out, rec = run(src, "delete_table_rows", {"tables": SCHEDULE, "rows": {"text_regex": "^FP-2"}}, {})
    assert rec.passed, rec.to_markdown()
    t0 = tables(out)[0]
    assert [r.find(f"{W}trPr/{W}del") is not None for r in rows(t0)] == [False, False, True, False]
    assert plan.rev_delta == {"ins": 0, "del": 4, "del_mark": 3}
    plan, out, rec = run(src, "delete_table_rows", {"tables": SCHEDULE, "rows": {"index_range": [1, 2]}},
                         {"tracked": False})
    assert rec.passed, rec.to_markdown()
    assert row_texts(tables(out)[0]) == ["Tag|Description|Capacity", "AC-1|Air compressor|10 cfm"]
    assert plan.property_assertions[0]["changed"] == {MAIN: [[0, [1, 2], False]]}


def test_deleting_every_row_deletes_the_table(src):
    for tracked in (True, False):
        plan, out, rec = run(src, "delete_table_rows", {"tables": ITEMS, "rows": "all"}, {"tracked": tracked})
        assert rec.passed, rec.to_markdown()
        assert any("lose every row and are deleted whole" in f for f in plan.flags)
        assert len(tables(out)) == (6 if tracked else 5)
    # the header rows of a table whose style makes every row one
    plan, out, rec = run(src, "delete_table_rows", {"tables": REFS, "rows": {"header": True}}, {"tracked": False})
    assert rec.passed and len(tables(out)) == 5


def test_a_vertical_merge_is_shortened_or_refused(src):
    e = refused(src, "delete_table_rows", {"tables": ZONES, "rows": {"text_regex": "^Z1"}}, {}, "vertical-merge")
    assert "row 1" in e.reason
    for rs in ({"index_range": [2, 3]}, {"index_range": [1, 3]}, {"text_regex": "^Z3"}):
        for tracked in (True, False):
            plan, out, rec = run(src, "delete_table_rows", {"tables": ZONES, "rows": rs}, {"tracked": tracked})
            assert rec.passed, (rs, rec.to_markdown())
    plan, out, rec = run(src, "delete_table_rows", {"tables": ZONES, "rows": {"index_range": [2, 3]}}, {"tracked": False})
    assert row_texts(tables(out)[1]) == ["Zone|Area|Notes", "Z1|North|Shared", "Totals|3"]


def test_rows_of_a_nested_table_inside_a_row_that_goes(src):
    plan, out, rec = run(src, "delete_table_rows", {"rows": {"text_regex": "^(Gate|Rating)"}}, {"tracked": False})
    assert rec.passed, rec.to_markdown()
    assert plan.property_assertions[0]["changed"] == {MAIN: [[2, [1], False]]}
    plan, out, rec = run(src, "delete_table_rows", {"tables": NESTED, "rows": {"text_regex": "^Rating"}}, {})
    assert rec.passed, rec.to_markdown()


def test_rows_in_a_content_control_and_other_refusals(src):
    def wrap(root):
        tr = rows(tbls(root)[0])[2]
        sdt = frag_xml("<w:sdt><w:sdtPr/><w:sdtContent/></w:sdt>")
        tr.addprevious(sdt)
        sdt.find(W + "sdtContent").append(tr)
    refused(mutate(src, MAIN, wrap), "delete_table_rows", {"tables": SCHEDULE, "rows": {"index_range": [1, 1]}}, {},
            "table-structure")
    refused(src, "delete_table_rows", {"tables": SCHEDULE}, {}, "bad-params")             # rows must be named
    refused(src, "delete_table_rows", {"rows": {"text_regex": "^Nothing"}}, {}, "not-found")
    plan, out, rec = run(src, "delete_table_rows", {"rows": {"text_regex": "^Nothing"}}, {"allow_missing": True})
    assert rec.passed and out == src
    refused(mutate(src, MAIN, _comment_in_cell), "delete_table_rows", {"tables": SCHEDULE, "rows": {"index_range": [0, 0]}},
            {}, "table-anchor")


def test_rows_in_the_header_part(src):
    plan, out, rec = run(src, "delete_table_rows", {"scope": ["headers"], "rows": "all"}, {"tracked": False})
    assert rec.passed and not tables(out, HEADER)


# ---- insert_table_rows -----------------------------------------------------------------------------------------------

def test_a_new_row_copies_its_reference_row(src):
    plan, out, rec = run(src, "insert_table_rows", {"tables": SCHEDULE, "rows": {"text_regex": "^FP-1"}},
                         {"rows": [["FP-3", "Booster pump", "100 gpm"]]})
    assert rec.passed, rec.to_markdown()
    t0 = tables(out)[0]
    assert row_texts(t0)[2] == "FP-3|Booster pump|100 gpm"
    new, ref = rows(t0)[2], rows(t0)[1]
    assert kids(new.find(W + "trPr")) == ["trHeight", "ins"] and not new.attrib
    first = cells(new)[0].find(W + "p")
    assert kids(first.find(W + "pPr")) == ["jc", "rPr"]                         # cnfStyle is never copied
    assert first.find(f"{W}ins/{W}r/{W}rPr/{W}rFonts").get(W + "ascii") == "Arial"
    assert etree.tostring(cells(new)[1].find(W + "tcPr")) == etree.tostring(cells(ref)[1].find(W + "tcPr"))
    assert plan.rev_delta == {"ins": 4, "ins_mark": 3, "del": 0}
    assert [(d.block_index, d.after) for d in plan.diffs] == [(8, "FP-3"), (9, "Booster pump"), (10, "100 gpm")]


def test_header_rows_are_copied_without_their_marking_unless_asked(src):
    for repeat in (False, True):
        plan, out, rec = run(src, "insert_table_rows", {"tables": SCHEDULE, "rows": {"index_range": [0, 0]}},
                             {"rows": [["SCHEDULE", "", ""]], "position": "before", "repeat_header": repeat,
                              "tracked": False})
        assert rec.passed, rec.to_markdown()
        new = rows(tables(out)[0])[0]
        assert kids(new.find(W + "trPr")) == (["cantSplit", "tblHeader"] if repeat else ["cantSplit"])
        assert new.find(f".//{W}b") is not None and new.find(f".//{W}shd").get(W + "fill") == "D9D9D9"
        assert [len(list(tc.iter(W + "r"))) for tc in cells(new)] == [1, 0, 0]     # an empty text is an empty cell
    # a table whose style repeats every row: a new row says it is not a header row
    plan, out, rec = run(src, "insert_table_rows", {"tables": REFS}, {"rows": [["R-2", "Second"]], "position": "at_end"})
    assert rec.passed, rec.to_markdown()
    assert rows(tables(out)[4])[2].find(f"{W}trPr/{W}tblHeader").get(W + "val") == "0"


def test_rows_at_the_end_of_tables_in_both_parts(src):
    for tracked in (True, False):
        plan, out, rec = run(src, "insert_table_rows", {"scope": ["document", "headers"],
                                                         "tables": {"first_row_regex": "^(Item|SECTION)"}},
                             {"rows": [["Valves", "6"], ["Hangers", "40"]], "position": "at_end", "tracked": tracked})
        assert rec.passed, rec.to_markdown()
        assert row_texts(tables(out)[5])[-2:] == ["Valves|6", "Hangers|40"]
        assert row_texts(tables(out, HEADER)[0])[-1] == "Hangers|40"
        assert plan.property_assertions[0]["expected_tables"] == 2


def test_rows_into_a_nested_table_and_its_outer_one(src):
    plan, out, rec = run(src, "insert_table_rows", {"tables": {"text_regex": "^(Gate|Size)$"}},
                         {"rows": [["X", "Y"]], "position": "at_end"})
    assert rec.passed, rec.to_markdown()
    assert row_texts(tables(out)[2])[-1] == "X|Y" and row_texts(tables(out)[3])[-1] == "X|Y"
    blocks = [(d.block_index, d.after) for d in plan.diffs]
    assert blocks == [(37, "X"), (38, "Y"), (40, "X"), (41, "Y")]          # the nested table's row comes first


@pytest.mark.parametrize("select,params,code", [
    ({"tables": SCHEDULE, "rows": {"text_regex": "^FP"}}, {"rows": [["a", "b", "c"]]}, "ambiguous"),
    ({"tables": ZONES, "rows": {"text_regex": "^Z1"}}, {"rows": [["a", "b", "c"]]}, "table-shape"),
    ({"tables": ZONES, "rows": {"text_regex": "^Totals"}}, {"rows": [["a", "b"]]}, "table-shape"),
    ({"tables": SCHEDULE, "rows": {"text_regex": "^FP-1"}}, {"rows": [["a", "b"]]}, "table-shape"),
    ({"tables": SCHEDULE}, {"rows": [["a", "b", "c"]], "position": "at_end", "colour": 1}, "bad-params"),
    ({"tables": SCHEDULE, "rows": {"index_range": [1, 1]}}, {"rows": [["a", "b", "c"]], "position": "at_end"},
     "bad-params"),
    ({"tables": SCHEDULE}, {"rows": [["a", "b", "c"]]}, "bad-params"),
    ({"tables": SCHEDULE}, {"rows": [["a\tb", "b", "c"]], "position": "at_end"}, "bad-params"),
    ({"tables": SCHEDULE}, {"rows": [["a", "b", 3]], "position": "at_end"}, "bad-params"),
    ({"tables": SCHEDULE}, {"rows": [], "position": "at_end"}, "bad-params"),
    ({"tables": SCHEDULE}, {"rows": [["a", "b", "c"]], "position": "middle"}, "bad-params"),
    ({"tables": SCHEDULE}, {"rows": [["a", "b", "c"]], "position": "at_end", "repeat_header": "yes"}, "bad-params"),
    ({"tables": {"first_row_regex": "^Nothing"}}, {"rows": [["a"]], "position": "at_end"}, "not-found"),
])
def test_insert_table_rows_refusals(src, select, params, code):
    refused(src, "insert_table_rows", select, params, code)


def test_a_table_with_no_chosen_row_is_left_alone(src):
    plan, out, rec = run(src, "insert_table_rows", {"tables": {"text_regex": "pump|Pumps"}, "rows": {"text_regex": "^FP-2"}},
                         {"rows": [["FP-9", "Spare", "-"]]})
    assert rec.passed and any("no row chosen, left alone" in n for n in plan.notes)
    assert plan.property_assertions[0]["changed"] == {MAIN: [[0, 2]]}


# ---- insert_table -------------------------------------------------------------------------------------------------------

NOTES = {"text_regex": "^1\\.2 NOTES"}
EQUIPMENT = {"text_regex": "^EQUIPMENT"}


def _width(data: bytes, landscape: bool) -> int:
    root = DocxPackage(data).xml(MAIN)
    sects = [s for s in root.iter(W + "sectPr")]
    s = sects[-1] if landscape else sects[0]
    return int(s.find(W + "pgSz").get(W + "w")) - int(s.find(W + "pgMar").get(W + "left")) - \
        int(s.find(W + "pgMar").get(W + "right"))


def test_a_typed_table_full_width_with_borders(src):
    for tracked in (True, False):
        plan, out, rec = run(src, "insert_table", {"paragraphs": NOTES},
                             {"rows": [["Tag", "Description", "Size"], ["FP-1", "Fire pump", ""]], "header_rows": 1,
                              "tracked": tracked})
        assert rec.passed, rec.to_markdown()
        body = DocxPackage(out).xml(MAIN).find(W + "body")
        new = next(p for p in body if text_of(p) == "1.2 NOTES").getnext()
        assert new.tag == W + "tbl" and row_texts(new) == ["Tag|Description|Size", "FP-1|Fire pump|"]
        pr = new.find(W + "tblPr")
        assert kids(pr) == ["tblW", "tblBorders", "tblLook"]
        assert pr.find(W + "tblW").attrib == {W + "w": "5000", W + "type": "pct"}
        assert pr.find(W + "tblLook").get(W + "firstRow") == "1"
        widths = [int(g.get(W + "w")) for g in new.iter(W + "gridCol")]
        assert sum(widths) == _width(src, False) and max(widths) - min(widths) <= 1
        assert rows(new)[0].find(f"{W}trPr/{W}tblHeader") is not None
        assert rows(new)[1].find(f"{W}trPr/{W}tblHeader") is None
        if tracked:
            assert plan.rev_delta == {"ins": 2 + 5, "ins_mark": 6, "del": 0}
        assert [d.block_index for d in plan.diffs] == [46, 47, 48, 49, 50, 51]


def test_a_table_in_a_style_with_widths_before_the_anchor_in_the_landscape_section(src):
    plan, out, rec = run(src, "insert_table", {"paragraphs": EQUIPMENT},
                         {"rows": [["A", "B"]], "style": "Table Grid", "position": "before", "column_widths": [1, 2.5]})
    assert rec.passed, rec.to_markdown()
    new = next(p for p in DocxPackage(out).xml(MAIN).iter(W + "p") if text_of(p) == "EQUIPMENT").getprevious()
    assert new.find(f"{W}tblPr/{W}tblStyle").get(W + "val") == "TableGrid"
    assert new.find(f"{W}tblPr/{W}tblBorders") is None and new.find(f"{W}tblPr/{W}tblW").get(W + "w") == "5040"
    assert [g.get(W + "w") for g in new.iter(W + "gridCol")] == ["1440", "3600"]
    plan, out, rec = run(src, "insert_table", {"paragraphs": EQUIPMENT}, {"rows": [["A", "B"]], "position": "before"})
    assert rec.passed, rec.to_markdown()
    new = next(p for p in DocxPackage(out).xml(MAIN).iter(W + "p") if text_of(p) == "EQUIPMENT").getprevious()
    assert sum(int(g.get(W + "w")) for g in new.iter(W + "gridCol")) == _width(src, True)       # the landscape width


def _table_file(tmp_path: Path, kind: str) -> dict:
    grid = [["Tag", "Pump", "Flow"], ["FP-1", "Fire pump", "500"], ["FP-2", "Jockey", ""]]
    path = tmp_path / f"pumps.{kind}"
    if kind == "csv":
        path.write_text("Tag,Pump,Flow,\nFP-1,Fire pump,500\nFP-2,Jockey,,\n,,\n", encoding="utf-8")
        return {"file": str(path)}
    if kind == "json":
        path.write_text(json.dumps({"schedules": {"pumps": grid}}), encoding="utf-8")
        return {"file": str(path), "key": "schedules.pumps"}
    path.write_bytes(xlsx([["Tag", "Pump", "Flow", None], ["FP-1", "Fire pump", 500], ["FP-2", "Jockey"], [None]]))
    return {"file": str(path), "sheet": "Pumps"}


@pytest.mark.parametrize("kind", ["csv", "json", "xlsx"])
def test_a_table_from_an_attached_file(src, tmp_path, kind):
    spec = _table_file(tmp_path, kind)
    plan, out, rec = run(src, "insert_table", {"paragraphs": NOTES}, {"table": spec, "header_rows": 1})
    assert rec.passed, rec.to_markdown()
    new = next(p for p in DocxPackage(out).xml(MAIN).iter(W + "p") if text_of(p) == "1.2 NOTES").getnext()
    assert row_texts(new) == ["Tag|Pump|Flow", "FP-1|Fire pump|500", "FP-2|Jockey|"]     # trailing empties left out
    assert plan.property_assertions[0]["params"]["table"]["md5"]
    assert placed_words({"ops": [{"op": "insert_table", "params": {"table": spec}}]}) == []


def test_a_changed_or_unusable_table_file(src, tmp_path):
    spec = _table_file(tmp_path, "csv")
    pkg = DocxPackage(src)
    frag = get_op("insert_table").plan(pkg, {"paragraphs": NOTES}, {"table": spec})
    Path(spec["file"]).write_text("Tag\nFP-9\n", encoding="utf-8")
    with pytest.raises(JobException) as e:
        get_op("insert_table").apply(pkg.copy(), frag)
    assert e.value.code == "table-changed"
    bad = tmp_path / "bad.xlsx"
    bad.write_bytes(xlsx([["Tag", "Flow"], ["FP-1", 2.5]]))
    refused(src, "insert_table", {"paragraphs": NOTES}, {"table": {"file": str(bad), "sheet": "Pumps"}}, "table-value")
    js = tmp_path / "bad.json"
    js.write_text(json.dumps([["Tag", 3]]), encoding="utf-8")
    refused(src, "insert_table", {"paragraphs": NOTES}, {"table": {"file": str(js)}}, "table-value")
    js.write_text(json.dumps([["Tag", "a\u200bb"]]), encoding="utf-8")
    refused(src, "insert_table", {"paragraphs": NOTES}, {"table": {"file": str(js)}}, "table-value")
    js.write_text(json.dumps([[""], []]), encoding="utf-8")
    refused(src, "insert_table", {"paragraphs": NOTES}, {"table": {"file": str(js)}}, "table-value")


@pytest.mark.parametrize("select,params,code", [
    ({"paragraphs": {"text_regex": "^Zone schedule"}}, {}, "table-placement"),                  # a table follows
    ({"paragraphs": {"text_regex": "^Zone schedule"}}, {"position": "before"}, "table-placement"),   # one precedes
    ({"paragraphs": {"text_regex": "^END OF SECTION"}}, {}, "table-placement"),                 # the last paragraph
    ({"paragraphs": {"text_regex": "^Fire pump$"}}, {}, "table-placement"),                     # inside a table
    ({"paragraphs": {"index_range": [46, 47]}}, {}, "section-break"),                          # after a section break
    ({"paragraphs": NOTES}, {"style": "No Such Style"}, "missing-style"),
    ({"paragraphs": NOTES}, {"style": "Normal"}, "style-mismatch"),
    ({"paragraphs": NOTES}, {"rows": [["a", "b"], ["c"]]}, "bad-params"),
    ({"paragraphs": NOTES}, {"header_rows": 3}, "bad-params"),
    ({"paragraphs": NOTES}, {"column_widths": [1]}, "bad-params"),
    ({"paragraphs": NOTES}, {"column_widths": [1, 0]}, "bad-params"),
    ({"paragraphs": NOTES}, {"table": {"file": "x.csv"}}, "bad-params"),
    ({"paragraphs": NOTES}, {"rows": None}, "bad-params"),
    ({"paragraphs": NOTES, "scope": ["headers"]}, {}, "bad-params"),
    ({"paragraphs": "all"}, {}, "bad-params"),
    ({"paragraphs": {"text_regex": "^Nothing"}}, {}, "not-found"),
])
def test_insert_table_refusals(src, select, params, code):
    refused(src, "insert_table", select, {"rows": [["a", "b"], ["c", "d"]], **params}, code)


def test_a_section_without_its_size_needs_widths(src):
    def no_size(root):
        sect = root.find(f"{W}body/{W}sectPr")
        sect.remove(sect.find(W + "pgSz"))
    data = mutate(src, MAIN, no_size)
    refused(data, "insert_table", {"paragraphs": EQUIPMENT}, {"rows": [["a"]], "position": "before"}, "table-placement")
    plan, out, rec = run(data, "insert_table", {"paragraphs": EQUIPMENT}, {"rows": [["a"]], "column_widths": [2],
                                                                          "position": "before"})
    assert rec.passed, rec.to_markdown()
    plan, out, rec = run(src, "insert_table", {"paragraphs": NOTES}, {"rows": [["a"]], "column_widths": [9]})
    assert rec.passed and any("wider than the text" in f for f in plan.flags)


# ---- a wrong output is caught ---------------------------------------------------------------------------------------

def test_wrong_outputs_of_a_deletion_are_caught(src):
    plan, out, rec = run(src, "delete_tables", {"tables": SCHEDULE}, {})
    assert rec.passed
    _caught(src, plan, out, MAIN, lambda r: rows(tbls(r)[0])[1].find(W + "trPr").remove(
        rows(tbls(r)[0])[1].find(f"{W}trPr/{W}del")), "is not the source with")
    _caught(src, plan, out, MAIN, lambda r: rows(tbls(r)[0])[2].find(f"{W}trPr/{W}del").set(W + "author", "Someone"),
            "differs in its author")
    _caught(src, plan, out, MAIN, lambda r: rows(tbls(r)[0])[2].find(f"{W}trPr/{W}del").set(
        W + "id", rows(tbls(r)[0])[1].find(f"{W}trPr/{W}del").get(W + "id")), "its id")
    plan, out, rec = run(src, "delete_tables", {"tables": REFS}, {"tracked": False})
    assert rec.passed
    _caught(src, plan, out, MAIN, lambda r: r.find(f"{W}body/{W}bookmarkStart").getparent().remove(
        r.find(f"{W}body/{W}bookmarkStart")))
    plan, out, rec = run(src, "delete_table_rows", {"tables": SCHEDULE, "rows": {"index_range": [1, 1]}},
                         {"tracked": False})
    assert rec.passed
    _caught(src, plan, out, MAIN, lambda r: tbls(r)[0].remove(rows(tbls(r)[0])[1]))


def test_wrong_outputs_of_an_insertion_are_caught(src):
    plan, out, rec = run(src, "insert_table_rows", {"tables": SCHEDULE, "rows": {"text_regex": "^FP-1"}},
                         {"rows": [["FP-3", "Booster pump", "100 gpm"]]})
    assert rec.passed
    _caught(src, plan, out, MAIN, lambda r: next(t for t in rows(tbls(r)[0])[2].iter(W + "t")).set(
        "{http://www.w3.org/XML/1998/namespace}space", "preserve"))
    _caught(src, plan, out, MAIN, lambda r: rows(tbls(r)[0])[2].find(f".//{W}rFonts").getparent().remove(
        rows(tbls(r)[0])[2].find(f".//{W}rFonts")))
    bad = mutate(out, MAIN, lambda r: setattr(next(rows(tbls(r)[0])[2].iter(W + "t")), "text", "FP-4"))
    rec = run_suite(src, bad, plan.to_dict(), "src.docx", "out.docx")
    assert {"V2", "V10"} <= set(failed(rec))
    plan, out, rec = run(src, "insert_table", {"paragraphs": NOTES}, {"rows": [["a", "b"]], "tracked": False})
    assert rec.passed
    assert row_texts(tables(out)[5]) == ["a|b"]                # the new table, after "1.2 NOTES"
    _caught(src, plan, out, MAIN, lambda r: next(tbls(r)[5].iter(W + "gridCol")).set(W + "w", "100"))
    _caught(src, plan, out, MAIN, lambda r: tbls(r)[5].find(W + "tblPr").remove(tbls(r)[5].find(f"{W}tblPr/{W}tblLook")))


# ---- a wrong implementation shared by plan and apply is caught ------------------------------------------------------

def test_a_shared_wrong_row_selection_is_caught(src, monkeypatch):
    real = table_rows.selected_rows
    monkeypatch.setattr(table_rows, "selected_rows", lambda tbl, pred, chain: [r + 1 for r in real(tbl, pred, chain)
                                                                                if r + 1 < len(own_rows(tbl))])
    import spec_surgeon.ops.delete_table_rows as dtr
    monkeypatch.setattr(dtr, "selected_rows", table_rows.selected_rows)
    plan, out, rec = run(src, "delete_table_rows", {"tables": SCHEDULE, "rows": {"index_range": [1, 1]}}, {})
    assert "V10" in failed(rec) and "not the plan's" in v10(rec)


def test_a_shared_wrong_merge_rule_is_caught(src, monkeypatch):
    import spec_surgeon.ops.delete_table_rows as dtr
    monkeypatch.setattr(dtr, "merge_crossings", lambda tbl, gone: [])
    plan, out, rec = run(src, "delete_table_rows", {"tables": ZONES, "rows": {"text_regex": "^Z1"}}, {})
    assert "V10" in failed(rec) and "vertical-merge" in v10(rec)


def test_a_shared_wrong_container_rule_is_caught(src, monkeypatch):
    import spec_surgeon.ops.delete_tables as dt
    monkeypatch.setattr(dt, "table_removal_problem", lambda container, going: None)

    def bare_cell(root):
        p = next(p for p in root.iter(W + "p") if text_of(p) == "See detail.")
        p.getparent().remove(p)
    data = mutate(src, MAIN, bare_cell)
    plan, out, rec = run(data, "delete_tables", {"tables": NESTED}, {"tracked": False})
    assert "V10" in failed(rec) and "table-structure" in v10(rec)


def test_a_shared_removal_that_drops_crossing_bookmarks_is_caught(src, monkeypatch):
    real = table_edit.removal

    def careless(root, gone, tracked, where):
        rem = real(root, gone, tracked, where)
        rem.kept_marks = [[] for _ in rem.gone]
        return rem
    import spec_surgeon.ops.delete_tables as dt
    monkeypatch.setattr(dt, "removal", careless)
    plan, out, rec = run(src, "delete_tables", {"tables": REFS}, {"tracked": False})
    assert "V10" in failed(rec) and "is not the source with" in v10(rec)


def test_a_shared_deletion_that_misses_the_paragraph_marks_is_caught(src, monkeypatch):
    real = table_edit.mark_deleted

    def no_marks(gone, stamp, next_id):
        real(gone, stamp, next_id)
        for g in gone:
            for m in list(g.iter(W + "del")):
                if m.getparent().tag == W + "rPr":
                    m.getparent().remove(m)
    import spec_surgeon.ops.delete_tables as dt
    monkeypatch.setattr(dt, "mark_deleted", no_marks)
    monkeypatch.setattr(dt, "deletion_counts", lambda gone: (lambda r, p, g: (r, 0, g))(*table_edit.deletion_counts(gone)))
    plan, out, rec = run(src, "delete_tables", {"tables": SCHEDULE}, {})
    assert "V10" in failed(rec) and "is not the source with" in v10(rec)


def test_a_shared_row_copy_that_keeps_word_s_cache_is_caught(src, monkeypatch):
    monkeypatch.setattr(table_edit, "cell_ppr", lambda p: table_edit.without(p.find(W + "pPr"), ("sectPr",)))
    plan, out, rec = run(src, "insert_table_rows", {"tables": SCHEDULE, "rows": {"text_regex": "^FP-1"}},
                         {"rows": [["FP-3", "Booster pump", "100 gpm"]]})
    assert rows(tables(out)[0])[2].find(f".//{W}cnfStyle") is not None
    assert "V10" in failed(rec) and "is not the source with" in v10(rec)


def test_a_shared_header_rule_that_lets_the_style_repeat_a_new_row_is_caught(src, monkeypatch):
    monkeypatch.setattr(table_edit, "expected_row_element", lambda name, on, chain: None)
    plan, out, rec = run(src, "insert_table_rows", {"tables": REFS}, {"rows": [["R-2", "Second"]], "position": "at_end"})
    assert "V10" in failed(rec)


def test_shared_wrong_widths_and_placement_are_caught(src, monkeypatch):
    monkeypatch.setattr(it_module, "equal_columns", lambda width, n: [width // n] * n)
    plan, out, rec = run(src, "insert_table", {"paragraphs": NOTES}, {"rows": [list("abcdefg")]})   # 9360 / 7
    assert "V10" in failed(rec) and "is not the source with" in v10(rec)
    monkeypatch.undo()
    monkeypatch.setattr(it_module, "placement_problem", lambda p, position: None)
    plan, out, rec = run(src, "insert_table", {"paragraphs": {"text_regex": "^Zone schedule"}}, {"rows": [["a"]]})
    assert "V10" in failed(rec) and "table-placement" in v10(rec)


# ---- conflicts and composition ----------------------------------------------------------------------------------------

def _spec(*ops):
    return {"request": "r", "summary": "the tables", "mode": "content", "ops": list(ops)}


def test_structure_ops_claim_their_part_beside_the_table_formatting_ops(src):
    pkg = DocxPackage(src)
    for other in (("set_table_props", {"tables": SCHEDULE}, {"cant_split": True}),
                  ("apply_table_style", {"tables": SCHEDULE}, {"style": "Table Grid"})):
        for mine in (("delete_table_rows", {"tables": SCHEDULE, "rows": {"index_range": [1, 1]}}, {}),
                     ("insert_table_rows", {"tables": SCHEDULE}, {"rows": [["a", "b", "c"]], "position": "at_end"}),
                     ("delete_tables", {"tables": ITEMS}, {}), ("insert_table", {"paragraphs": NOTES}, {"rows": [["a"]]})):
            frags = [get_op(o).plan(pkg, s, p) for o, s, p in (other, mine)]
            with pytest.raises(JobException) as e:
                merge_fragments(frags)
            assert e.value.code == "conflict"
    # a different part: no conflict
    frags = [get_op("set_table_props").plan(pkg, {"scope": ["headers"]}, {"cant_split": True}),
             get_op("delete_tables").plan(pkg, {"tables": ITEMS}, {"tracked": False})]
    merge_fragments(frags)


def test_a_spec_whose_ops_overlap_is_composed_into_steps(src):
    spec = _spec({"op": "insert_table_rows", "select": {"tables": SCHEDULE},
                  "params": {"rows": [["FP-3", "Booster pump", "100 gpm"]], "position": "at_end", "tracked": False}},
                 {"op": "set_table_props", "select": {"tables": SCHEDULE}, "params": {"cant_split": True}},
                 {"op": "delete_table_rows", "select": {"tables": SCHEDULE, "rows": {"text_regex": "^FP-2"}},
                  "params": {"tracked": False}})
    spec["mode"] = "untracked"
    validate_spec(spec)
    chain = split_into_stages(DocxPackage(src), spec)
    assert chain is not None and len(chain["stages"]) == 3
    current = DocxPackage(src)
    for plan, frags in plan_chain(current, chain):
        nxt = apply_spec(current, frags)
        rec = run_suite(current.output_bytes(), nxt.output_bytes(), plan.to_dict(), "in.docx", "out.docx")
        assert rec.passed, rec.to_markdown()
        current = nxt
    assert row_texts(tables(current.output_bytes())[0]) == ["Tag|Description|Capacity", "FP-1|Fire pump|500 gpm",
                                                            "AC-1|Air compressor|10 cfm", "FP-3|Booster pump|100 gpm"]


def test_the_selection_reads_what_it_chose_by(src):
    pkg = DocxPackage(src)
    touches = set(get_op("delete_table_rows").plan(pkg, {"tables": SCHEDULE, "rows": {"text_regex": "^FP", "header": False}},
                                                   {}).touches)
    assert (MAIN, "*", "text") in touches and (MAIN, "scope", "verify") in touches
    assert (MAIN, "5", "verify:text") in touches and (MAIN, "tr:0:1", "verify-props:tblHeader") in touches
    assert ("word/styles.xml", "style:TableGrid", "verify:tblPr") in touches
    touches = set(get_op("insert_table").plan(pkg, {"paragraphs": NOTES}, {"rows": [["a"]], "style": "Table Grid"}).touches)
    assert ("word/styles.xml", "style:TableGrid", "verify") in touches


# ---- the catalog, the words, the explanations, the pipeline and a render -------------------------------------------

NEW_OPS = ("delete_tables", "delete_table_rows", "insert_table_rows", "insert_table")


def test_catalog_sheet_and_assumptions():
    cat = op_catalog()
    sheet = op_sheet()
    for op in NEW_OPS:
        assert op in cat["ops"] and op in sheet["ops"] and COMMON_ASSUMPTIONS[op]
    assert any("table rows" in a for a in COMMON_ASSUMPTIONS["resolve_tracked_changes"])
    validate_spec(_spec({"op": "delete_table_rows", "select": {"tables": SCHEDULE, "rows": {"index_range": [1, 2]}}},
                        {"op": "insert_table", "select": {"paragraphs": NOTES},
                         "params": {"table": {"file": "C:/p/pumps.xlsx", "sheet": "Pumps"}, "header_rows": 1}}))


def test_typed_cells_are_placed_words():
    spec = _spec({"op": "insert_table_rows", "params": {"rows": [["FP-3", "Booster pump", ""]]}},
                 {"op": "insert_table", "params": {"rows": [["Tag", "Size"], ["V-1", "4 in"]]}})
    assert placed_words(spec) == ["FP-3", "Booster pump", "Tag", "Size", "V-1", "4 in"]


def test_every_refusal_code_of_the_structure_ops_is_explained():
    codes = set()
    here = Path(table_edit.__file__).parent
    for name in ("table_rows", "table_edit", "delete_tables", "delete_table_rows", "insert_table_rows", "insert_table"):
        for n in ast.walk(ast.parse((here / f"{name}.py").read_text(encoding="utf-8"))):
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "JobException" and len(n.args) > 1:
                assert isinstance(n.args[1], ast.Constant) or name in ("table_edit", "insert_table_rows"), \
                    (name, n.lineno)
                if isinstance(n.args[1], ast.Constant):
                    codes.add(n.args[1].value)
    assert {"table-anchor", "vertical-merge", "table-shape", "table-placement", "table-structure", "ambiguous",
            "existing-revisions", "section-break", "textbox", "structure", "missing-style", "style-mismatch"} <= codes
    codes |= {"table-value"}                                   # cell_text's caller-given code
    assert sorted(c for c in codes if c not in FAILURE_EXPLANATIONS) == []


def test_a_spec_runs_through_the_batch_pipeline(src, tmp_path):
    from spec_surgeon.batch import run_batch
    spec = _spec({"op": "insert_table_rows", "select": {"tables": SCHEDULE},
                  "params": {"rows": [["FP-3", "Booster pump", "100 gpm"]], "position": "at_end"}},
                 {"op": "delete_tables", "select": {"scope": ["headers"]}})
    path = tmp_path / "23 05 93 – TAB.docx"
    path.write_bytes(src)
    batch = run_batch([str(path)], "edit-spec", {}, tmp_path / "out", workers=1, renderer="none", do_render=False,
                      spec=spec)
    f = batch.files[0]
    assert f.status == "PASS", f.record.to_markdown() if f.record else f.exception


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
def test_libreoffice_opens_and_renders_the_output(src, tmp_path):
    from spec_surgeon.render import libreoffice
    plan, out, rec = run_ops(src, [
        ("delete_table_rows", {"tables": ZONES, "rows": {"index_range": [2, 3]}}, {"tracked": False}),
        ("delete_tables", {"scope": ["headers"]}, {"tracked": False}),
    ])
    assert rec.passed, rec.to_markdown()
    plan, out2, rec = run(out, "insert_table", {"paragraphs": NOTES},
                          {"rows": [["Tag", "Description"], ["FP-1", "Fire pump"]], "header_rows": 1})
    assert rec.passed, rec.to_markdown()
    (tmp_path / "out.docx").write_bytes(out2)
    pdfs = libreoffice.render_pdfs([tmp_path / "out.docx"], tmp_path / "pdf", workers=1)
    if len(pdfs) != 1:
        pytest.skip("LibreOffice did not render here")
    pdf = pdfs[tmp_path / "out.docx"]
    assert pdf.exists() and pdf.stat().st_size > 1000 and pdf.read_bytes().startswith(b"%PDF")
