"""The table grid and cell ops of built-in batch 6 (F6 — docs/BUILTIN_OPS_BATCH6_DESIGN.md §7): delete_table_columns,
set_table_cells, merge_table_cells and their V10 kinds (verify/tablegrid.py).

End to end on the grid fixture (tests/fx_table_grid.py): a fixed and a percent table width, cells spanning columns and
merged down rows, rows starting late and ending early, a nested table, a table style that shades its cells; the column
selector and the grid on both sides agreeing on every column; refusals with their codes; a wrong OUTPUT per rule and
wrong IMPLEMENTATIONS shared by plan and apply caught; parameters; plan-time conflicts with the table formatting ops
and F5's row ops and their composition into steps; the catalog, the explanations, the batch pipeline and a LibreOffice
render."""
from __future__ import annotations

import ast
import io
import shutil
import zipfile
from pathlib import Path
from typing import List, Tuple

import pytest
from lxml import etree

from fx_table_grid import TITLE, build_grid
from spec_surgeon.catalog import COMMON_ASSUMPTIONS, op_catalog, op_sheet
from spec_surgeon.compiler import placed_words
from spec_surgeon.editspec import apply_spec, plan_chain, split_into_stages, validate_spec
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import delete_table_columns as dtc
from spec_surgeon.ops import merge_table_cells as mtc
from spec_surgeon.ops import set_table_cells as stc
from spec_surgeon.ops import table_columns, table_rows
from spec_surgeon.ops.table_select import own_rows
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.report import FAILURE_EXPLANATIONS
from spec_surgeon.verify import run_suite
from spec_surgeon.verify import tablecolumns as vtc
from spec_surgeon.verify.reader import VPackage
from spec_surgeon.verify.tableselect import VTables

MAIN = "word/document.xml"
HEADER = "word/header1.xml"
PUMPS = {"first_row_regex": "^Tag"}              # table 0: fixed width, a vertical merge, a spanning cell
ZONES = {"first_row_regex": "^Zone"}             # table 1: percent width, gridBefore / gridAfter, percent cell widths
VALVES = {"first_row_regex": "^Valve"}           # table 2: holds table 3
NESTED = {"first_row_regex": "^Size"}            # table 3
ITEMS = {"first_row_regex": "^Item"}             # table 4: "Arch Shaded" (style shading, no-wrap, header rows)


@pytest.fixture(scope="module")
def src() -> bytes:
    return build_grid()


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
    return run_ops(data, [(op, select, params)])


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


def kids(el) -> List[str]:
    return [etree.QName(c).localname for c in el] if el is not None else []


def text_of(el) -> str:
    return "".join(t.text or "" for t in el.iter(W + "t"))


def row_texts(tbl) -> List[str]:
    return ["|".join(text_of(tc) for tc in cells(tr)) for tr in rows(tbl)]


def grid_of(tbl) -> List[str]:
    return [g.get(W + "w") for g in tbl.find(W + "tblGrid")]


def tcw(tc) -> Tuple[str, str]:
    w = tc.find(f"{W}tcPr/{W}tcW")
    return w.get(W + "type"), w.get(W + "w")


def _caught(src, plan, out, part, fn, needle=None):
    bad = mutate(out, part, fn)
    rec = run_suite(src, bad, plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec), rec.to_markdown()
    if needle:
        assert needle in v10(rec), v10(rec)
    return rec


def refused(data, op, select, params, code):
    with pytest.raises(JobException) as e:
        get_op(op).plan(DocxPackage(data), select, params)
    assert e.value.code == code, e.value.reason
    return e.value


# ---- the column selector and the grid, both sides --------------------------------------------------------------------

COLUMN_PREDICATES = ["all", {}, {"index": [0]}, {"index": [1, 3]}, {"index": [2, 9]}, {"header_regex": "^Notes$"},
                     {"header_regex": "e"}, {"header_regex": "^$"}, {"header_regex": "SECTION|Rating", "index": [0, 1]},
                     {"header_regex": "^Nothing"}]


def test_the_two_sides_of_the_column_selector_agree_on_every_column(src):
    pkg, vpkg = DocxPackage(src), VPackage(src)
    for part in (MAIN, HEADER):
        root = pkg.xml(part)
        vt = VTables(vpkg.xml(part))
        assert len(tbls(root)) == len(vt.tables)
        for tbl, vtbl in zip(tbls(root), vt.tables):
            assert table_columns.grid_problem(tbl) is None and vtc.v_unnumbered(vtbl) is None
            grid = table_columns.grid_columns_of(tbl)
            assert [table_columns.column_width(g) for g in grid] == [vtc.v_width(g) for g in vtc.v_grid(vtbl)]
            assert table_columns.header_texts(tbl, len(grid)) == vtc.v_headers(vtbl, len(grid), vt.text)
            for tr, vtr in zip(own_rows(tbl), rows(vtbl)):
                lay, vlay = table_columns.row_layout(tr), vtc.v_layout(vtr)
                assert (lay.before, [(s, n) for s, n, _ in lay.cells], lay.after, lay.end) == \
                       (vlay[0], [(s, n) for s, n, _ in vlay[1]], vlay[2], vtc.v_end(vlay))
                assert [table_columns.cell_is_empty(tc) for _, _, tc in lay.cells] == \
                       [vtc.v_empty(tc) for _, _, tc in vlay[1]]
            for pred in COLUMN_PREDICATES:
                assert table_columns.selected_columns(tbl, pred) == vtc.v_columns_chosen(vtbl, pred, vt.text), pred
            merges = table_columns.vertical_merges(tbl)
            assert merges == vtc.v_vmerges(vtbl)
            assert [rs for _c, rs in merges] == table_rows.merge_groups(tbl)        # F5's reading, with the column


def test_the_fixture_reads_as_documented(src):
    t = tables(src)
    assert [row_texts(x)[0] for x in t] == ["Tag|Description|Size|Notes", "Zone|Area|Floor", "Valve|Detail",
                                            "Size|Rating", "Item|Count|Remarks"]
    assert table_columns.vertical_merges(t[0]) == [(3, [2, 3])]
    assert [(lay.before, lay.after) for lay in map(table_columns.row_layout, rows(t[1]))] == [(0, 0), (1, 0), (0, 1),
                                                                                             (0, 0)]
    assert table_columns.selected_columns(t[0], {"header_regex": "^(Tag|Description)$"}) == [0, 1]
    assert table_columns.cell_is_empty(cells(rows(t[0])[3])[3])
    assert not table_columns.cell_is_empty(cells(rows(t[2])[1])[1])                # it holds a table


@pytest.mark.parametrize("pred", ["some", {"columns": [1]}, {"index": []}, {"index": [1, 1]}, {"index": [-1]},
                                  {"index": 2}, {"index": [True]}, {"header_regex": "("}, {"header_regex": 3}])
def test_column_selectors_are_checked_on_both_sides(src, pred):
    with pytest.raises(JobException) as e:
        table_columns.check_column_selector(pred)
    assert e.value.code == "bad-params"
    with pytest.raises(ValueError):
        vtc.v_check_columns(pred)
    refused(src, "delete_table_columns", {"columns": pred}, {}, "bad-params")


def _no_grid(root):
    tbl = tbls(root)[0]
    tbl.remove(tbl.find(W + "tblGrid"))


def _past_grid(root):
    tr = rows(tbls(root)[0])[1]
    tr.insert(0, frag_xml('<w:trPr><w:gridAfter w:val="1"/></w:trPr>'))


def _odd_before(root):
    tr = rows(tbls(root)[0])[1]
    tr.insert(0, frag_xml('<w:trPr><w:gridBefore w:val="x"/></w:trPr>'))


@pytest.mark.parametrize("plant", [_no_grid, _past_grid, _odd_before])
def test_a_grid_that_cannot_be_numbered_is_refused_by_every_column_op(src, plant):
    data = mutate(src, MAIN, plant)
    t0 = tbls(DocxPackage(data).xml(MAIN))[0]
    assert table_columns.grid_problem(t0) and vtc.v_unnumbered(tbls(VPackage(data).xml(MAIN))[0])
    refused(data, "delete_table_columns", {"tables": PUMPS, "columns": {"index": [1]}}, {}, "table-grid")
    refused(data, "merge_table_cells", {"tables": PUMPS, "rows": {"index_range": [1, 1]}, "columns": {"index": [0, 1]}},
            {}, "table-grid")
    refused(data, "set_table_cells", {"tables": PUMPS, "columns": {"index": [1]}}, {"shading": "FFFF00"}, "table-grid")
    plan, out, rec = run(data, "set_table_cells", {"tables": PUMPS}, {"shading": "FFFF00"})     # no grid needed
    assert rec.passed, rec.to_markdown()


# ---- delete_table_columns ----------------------------------------------------------------------------------------------

def test_a_column_goes_a_spanning_cell_narrows_and_a_fixed_width_shrinks(src):
    plan, out, rec = run(src, "delete_table_columns", {"tables": PUMPS, "columns": {"index": [1]}}, {})
    assert rec.passed, rec.to_markdown()
    assert plan.mode == "untracked" and plan.geometry_reference == "none"
    t0 = tables(out)[0]
    assert row_texts(t0) == ["Tag|Size|Notes", "P-1|2 in|Duty", "P-2|2 in|Standby", "P-3|1 in|", "Totals|5 in|"]
    assert grid_of(t0) == ["1440", "2160", "2160"]
    assert t0.find(f"{W}tblPr/{W}tblW").get(W + "w") == "5760"
    totals = cells(rows(t0)[4])[0]
    assert totals.find(f"{W}tcPr/{W}gridSpan") is None and tcw(totals) == ("dxa", "1440")
    assert [d.before for d in plan.diffs] == ["Description", "Base-mounted pump", "Base-mounted pump", "Inline pump"]
    assert plan.property_assertions[0]["changed"] == {MAIN: [[0, [1]]]}
    assert any("UNTRACKED deletion of 1 table column" in f for f in plan.flags)


def test_a_column_by_its_header_takes_its_vertical_merge_whole(src):
    plan, out, rec = run(src, "delete_table_columns", {"tables": PUMPS, "columns": {"header_regex": "^Notes$"}}, {})
    assert rec.passed, rec.to_markdown()
    t0 = tables(out)[0]
    assert not list(t0.iter(W + "vMerge")) and grid_of(t0) == ["1440", "2880", "2160"]
    assert t0.find(f"{W}tblPr/{W}tblW").get(W + "w") == "6480"
    plan, out, rec = run(src, "delete_table_columns", {"tables": PUMPS, "columns": {"index": [0, 1]}}, {})
    assert rec.passed, rec.to_markdown()
    assert row_texts(tables(out)[0])[-1] == "5 in|"                          # the spanning cell went whole


def test_columns_counted_before_and_after_a_row_s_cells(src):
    plan, out, rec = run(src, "delete_table_columns", {"tables": ZONES, "columns": {"index": [0, 2]}}, {})
    assert rec.passed, rec.to_markdown()
    t1 = tables(out)[1]
    assert row_texts(t1) == ["Area", "North", "Annex", "Ground"] and grid_of(t1) == ["3120"]
    assert rows(t1)[1].find(W + "trPr") is None and rows(t1)[2].find(W + "trPr") is None
    assert t1.find(f"{W}tblPr/{W}tblW").attrib == {W + "type": "pct", W + "w": "5000"}
    assert any("width in percent" in f for f in plan.flags)
    # one of the two empty columns before row 1's cells goes, the other stays: the count and its width follow
    data = mutate(src, MAIN, lambda r: _widen_before(r))
    plan, out, rec = run(data, "delete_table_columns", {"tables": ZONES, "columns": {"index": [0]}}, {})
    assert rec.passed, rec.to_markdown()
    trpr = rows(tables(out)[1])[1].find(W + "trPr")
    assert trpr.find(W + "gridBefore").get(W + "val") == "1" and trpr.find(W + "wBefore").get(W + "w") == "3120"


def _widen_before(root):
    """Table 1, row 1: two empty columns before its one cell (gridBefore 2, wBefore 6240)."""
    tr = rows(tbls(root)[1])[1]
    tr.find(f"{W}trPr/{W}gridBefore").set(W + "val", "2")
    tr.find(f"{W}trPr/{W}wBefore").set(W + "w", "6240")
    tr.remove(cells(tr)[0])


def test_a_percent_cell_width_spanning_a_deleted_column_is_left_and_flagged(src):
    def span(root):
        tr = rows(tbls(root)[1])[3]
        first, second = cells(tr)[:2]
        first.find(W + "tcPr").append(frag_xml('<w:gridSpan w:val="2"/>'))
        tr.remove(second)
    plan, out, rec = run(mutate(src, MAIN, span), "delete_table_columns", {"tables": ZONES, "columns": {"index": [1]}},
                         {})
    assert rec.passed, rec.to_markdown()
    assert tcw(cells(rows(tables(out)[1])[3])[0]) == ("pct", "1667")
    assert any("given in percent" in f for f in plan.flags)


def test_a_nested_table_goes_with_its_cell_or_loses_its_own_column(src):
    plan, out, rec = run(src, "delete_table_columns", {"columns": {"header_regex": "^(Detail|Rating)$"}}, {})
    assert rec.passed, rec.to_markdown()
    assert plan.property_assertions[0]["changed"] == {MAIN: [[2, [1]]]} and len(tables(out)) == 4
    plan, out, rec = run(src, "delete_table_columns", {"tables": NESTED, "columns": {"index": [0]}}, {})
    assert rec.passed, rec.to_markdown()
    assert row_texts(tables(out)[3]) == ["Rating", "175 psi"]


def test_a_bookmark_crossing_a_deleted_cell_keeps_its_place_in_the_row(src):
    def mark(root):
        p = cells(rows(tbls(root)[0])[1])[1].find(W + "p")
        p.insert(0, frag_xml('<w:bookmarkStart w:id="40" w:name="_PumpNote"/>'))
        next(x for x in root.iter(W + "p") if text_of(x) == "1.2 ZONE SCHEDULE").append(
            frag_xml('<w:bookmarkEnd w:id="40"/>'))
    plan, out, rec = run(mutate(src, MAIN, mark), "delete_table_columns", {"tables": PUMPS, "columns": {"index": [1]}},
                         {})
    assert rec.passed, rec.to_markdown()
    tr = rows(tables(out)[0])[1]
    assert kids(tr) == ["tc", "bookmarkStart", "tc", "tc"]              # in the deleted cell's place


def test_tracked_changes_inside_a_deleted_cell_go_with_it(src):
    def pending(root):
        run = cells(rows(tbls(root)[0])[2])[1].find(f"{W}p/{W}r")
        ins = frag_xml('<w:ins w:id="900" w:author="Jack" w:date="2026-09-23T08:00:00Z"/>')
        run.addprevious(ins)
        ins.append(run)
    plan, out, rec = run(mutate(src, MAIN, pending), "delete_table_columns", {"tables": PUMPS,
                                                                              "columns": {"index": [1]}}, {})
    assert rec.passed, rec.to_markdown()
    assert plan.rev_delta["ins"] == -1


def test_the_header_part_and_nothing_to_delete(src):
    plan, out, rec = run(src, "delete_table_columns", {"scope": ["headers"], "columns": {"index": [1]}}, {})
    assert rec.passed and row_texts(tables(out, HEADER)[0]) == ["SECTION"]
    refused(src, "delete_table_columns", {"columns": {"header_regex": "^Nothing"}}, {}, "not-found")
    plan, out, rec = run(src, "delete_table_columns", {"columns": {"header_regex": "^Nothing"}}, {"allow_missing": True})
    assert rec.passed and out == src


def _hmerge(root):
    cells(rows(tbls(root)[0])[1])[0].find(W + "tcPr").append(frag_xml('<w:hMerge w:val="restart"/>'))


def _cell_change(root):
    cells(rows(tbls(root)[0])[1])[0].find(W + "tcPr").append(frag_xml(
        '<w:tcPrChange w:id="801" w:author="Jack" w:date="2026-09-01T08:00:00Z"><w:tcPr/></w:tcPrChange>'))


def _comment_in_column(root):
    p = cells(rows(tbls(root)[0])[1])[1].find(W + "p")
    p.insert(0, frag_xml('<w:commentRangeStart w:id="5"/>'))
    p.append(frag_xml('<w:commentRangeEnd w:id="5"/>'))


def _note_in_column(root):
    cells(rows(tbls(root)[0])[1])[1].find(W + "p").append(frag_xml('<w:r><w:footnoteReference w:id="2"/></w:r>'))


def _split_merge(root):
    """Table 0: row 2's last two cells become one spanning both, merged down; row 3's third cell continues it alone."""
    r2, r3 = rows(tbls(root)[0])[2:4]
    third, fourth = cells(r2)[2:4]
    third.find(f"{W}tcPr/{W}tcW").set(W + "w", "4320")
    third.find(W + "tcPr").append(frag_xml('<w:gridSpan w:val="2"/>'))
    third.find(W + "tcPr").append(frag_xml('<w:vMerge w:val="restart"/>'))
    r2.remove(fourth)
    cells(r3)[2].find(W + "tcPr").append(frag_xml('<w:vMerge/>'))
    cells(r3)[3].find(W + "tcPr").remove(cells(r3)[3].find(f"{W}tcPr/{W}vMerge"))


def _thin_totals(root):
    cells(rows(tbls(root)[0])[4])[0].find(f"{W}tcPr/{W}tcW").set(W + "w", "100")


def _no_width(root):
    tbls(root)[0].find(W + "tblGrid")[1].attrib.pop(W + "w")


@pytest.mark.parametrize("plant,select,code", [
    (None, {"tables": PUMPS, "columns": "all"}, "every-column"),
    (None, {"tables": ZONES, "columns": {"index": [1, 2]}}, "table-grid"),           # row 1 would keep no cell
    (_hmerge, {"tables": PUMPS, "columns": {"index": [2]}}, "table-grid"),
    (_cell_change, {"tables": PUMPS, "columns": {"index": [2]}}, "existing-revisions"),
    (_comment_in_column, {"tables": PUMPS, "columns": {"index": [1]}}, "table-anchor"),
    (_note_in_column, {"tables": PUMPS, "columns": {"index": [1]}}, "table-anchor"),
    (_split_merge, {"tables": PUMPS, "columns": {"index": [2]}}, "vertical-merge"),
    (_thin_totals, {"tables": PUMPS, "columns": {"index": [1]}}, "table-grid"),      # the width would fall to nothing
    (_no_width, {"tables": PUMPS, "columns": {"index": [1]}}, "table-grid"),         # the fixed width needs it
])
def test_delete_table_columns_refusals(src, plant, select, code):
    refused(mutate(src, MAIN, plant) if plant else src, "delete_table_columns", select, {}, code)


@pytest.mark.parametrize("op", ["delete_table_columns", "merge_table_cells"])
@pytest.mark.parametrize("params", [{"tracked": True}, {"tracked": "no"}, {"allow_missing": 1}, {"author": "J"}])
def test_the_untracked_ops_take_only_untracked(src, op, params):
    select = {"tables": PUMPS, "columns": {"index": [1]}, **({"rows": {"index_range": [1, 1]}} if "merge" in op else {})}
    refused(src, op, select, params, "bad-params")


def test_the_selectors_are_checked(src):
    for select in ({"tables": PUMPS}, {"tables": PUMPS, "columns": {"index": [1]}, "rows": "all"},
                   {"tables": "some", "columns": {"index": [1]}}, {"paragraphs": "all", "columns": {"index": [1]}}):
        refused(src, "delete_table_columns", select, {}, "bad-params")
    for select in ({"tables": PUMPS, "rows": {"index_range": [1, 1]}}, {"tables": PUMPS, "columns": {"index": [1]}},
                   {"rows": {"index_range": [1, 1]}, "columns": {"index": [1]}, "cells": "all"}):
        refused(src, "merge_table_cells", select, {}, "bad-params")


def test_a_table_in_a_text_box(src):
    def boxed(root):
        t = tbls(root)[4]
        p = next(p for p in root.iter(W + "p") if text_of(p) == "1.4 EQUIPMENT")
        box = frag_xml('<w:r><w:pict><w:txbxContent/></w:pict></w:r>')
        p.append(box)
        t.getparent().remove(t)
        box.find(f".//{W}txbxContent").append(t)
        etree.SubElement(box.find(f".//{W}txbxContent"), W + "p")
    data = mutate(src, MAIN, boxed)
    refused(data, "delete_table_columns", {"tables": ITEMS, "columns": {"index": [2]}}, {}, "textbox")
    refused(data, "set_table_cells", {"tables": ITEMS}, {"shading": "FFFF00"}, "textbox")
    refused(data, "merge_table_cells", {"tables": ITEMS, "rows": {"index_range": [1, 2]}, "columns": {"index": [2]}}, {},
            "textbox")
    plan, out, rec = run(data, "set_table_cells", {}, {"vertical_alignment": "center"})
    assert rec.passed and any("text box left alone" in f for f in plan.flags)


# ---- set_table_cells ----------------------------------------------------------------------------------------------------

def test_header_rows_shaded_and_aligned(src):
    plan, out, rec = run(src, "set_table_cells", {"rows": {"header": True}},
                         {"shading": "d9d9d9", "vertical_alignment": "center"})
    assert rec.passed, rec.to_markdown()
    assert plan.mode == "formatting" and not plan.diffs
    t0, t4 = tables(out)[0], tables(out)[4]
    first = cells(rows(t0)[0])[0].find(W + "tcPr")
    assert kids(first) == ["tcW", "shd", "vAlign"] and first.find(W + "vAlign").get(W + "val") == "center"
    # table 4's style makes every row a header row: all its cells get the fill, written as Word writes it
    shd = cells(rows(t4)[2])[1].find(f"{W}tcPr/{W}shd")
    assert shd.attrib == {W + "val": "clear", W + "color": "auto", W + "fill": "D9D9D9"}
    assert rows(tables(out)[0])[1].find(f".//{W}vAlign") is None              # not a header row
    assert plan.property_assertions[0]["expected_cells"] == 4 + 9             # table 0's row 0 and all of table 4


def test_no_shading_and_wrapping_against_a_style_that_shades(src):
    plan, out, rec = run(src, "set_table_cells", {"tables": ITEMS}, {"shading": "none", "no_wrap": False})
    assert rec.passed, rec.to_markdown()
    pr = cells(rows(tables(out)[4])[1])[0].find(W + "tcPr")
    assert pr.find(W + "shd").attrib == {W + "val": "clear", W + "color": "auto", W + "fill": "auto"}
    assert pr.find(W + "noWrap").get(W + "val") == "0"
    plan, out, rec = run(src, "set_table_cells", {"tables": PUMPS, "rows": {"index_range": [0, 0]}}, {"shading": "none"})
    assert rec.passed, rec.to_markdown()
    assert not list(rows(tables(out)[0])[0].iter(W + "shd"))                 # no style shading: no element at all
    plan, out, rec = run(src, "set_table_cells", {"tables": PUMPS}, {"no_wrap": True})
    assert rec.passed and cells(rows(tables(out)[0])[2])[1].find(f"{W}tcPr/{W}noWrap").attrib == {}


def test_a_column_width_follows_the_grid_the_cells_and_a_fixed_width(src):
    plan, out, rec = run(src, "set_table_cells", {"tables": PUMPS, "columns": {"index": [1]}}, {"width": 2.5})
    assert rec.passed, rec.to_markdown()
    t0 = tables(out)[0]
    assert grid_of(t0) == ["1440", "3600", "2160", "2160"] and t0.find(f"{W}tblPr/{W}tblW").get(W + "w") == "9360"
    assert tcw(cells(rows(t0)[1])[1]) == ("dxa", "3600") and tcw(cells(rows(t0)[4])[0]) == ("dxa", "5040")
    assert plan.property_assertions[0]["expected_columns"] == 1
    assert plan.property_assertions[0]["expected_cells"] == 5


def test_a_column_width_beside_rows_that_start_late(src):
    plan, out, rec = run(src, "set_table_cells", {"tables": ZONES, "columns": {"index": [0]}}, {"width": 1.5})
    assert rec.passed, rec.to_markdown()
    t1 = tables(out)[1]
    assert grid_of(t1)[0] == "2160" and rows(t1)[1].find(f"{W}trPr/{W}wBefore").get(W + "w") == "2160"
    assert tcw(cells(rows(t1)[3])[0]) == ("dxa", "2160")                    # a percent width made the column's
    assert any("width in percent" in f for f in plan.flags)


def test_already_as_asked_is_left_alone(src):
    plan, out, rec = run(src, "set_table_cells", {"tables": PUMPS, "columns": {"index": [0]}}, {"width": 1})
    assert rec.passed and out == src and any("already as asked" in n for n in plan.notes)
    plan, out, rec = run(src, "set_table_cells", {"tables": PUMPS, "rows": {"header": True}}, {"shading": "D9D9D9"})
    assert rec.passed and out == src
    plan, out, rec = run(src, "set_table_cells", {"tables": {"first_row_regex": "^Nothing"}}, {"shading": "D9D9D9"})
    assert rec.passed and out == src and any("nothing to change" in n for n in plan.notes)


def test_a_cell_without_properties_gets_them_first(src):
    def bare(root):
        tc = cells(rows(tbls(root)[2])[2])[0]
        tc.remove(tc.find(W + "tcPr"))
    plan, out, rec = run(mutate(src, MAIN, bare), "set_table_cells", {"tables": VALVES, "rows": {"index_range": [2, 2]}},
                         {"vertical_alignment": "bottom"})
    assert rec.passed, rec.to_markdown()
    assert kids(cells(rows(tables(out)[2])[2])[0])[0] == "tcPr"


def test_what_set_table_cells_flags(src):
    def changed(root):
        _cell_change(root)
    plan, out, rec = run(mutate(src, MAIN, changed), "set_table_cells", {"tables": PUMPS}, {"vertical_alignment": "top"})
    assert rec.passed and any("pending tracked property change" in f for f in plan.flags)

    def banded(root):
        tbls(root)[0].find(W + "tblPr").append(frag_xml('<w:shd w:val="clear" w:color="auto" w:fill="EEEEEE"/>'))
    plan, out, rec = run(mutate(src, MAIN, banded), "set_table_cells", {"tables": PUMPS}, {"shading": "none"})
    assert rec.passed and any("shaded as a whole" in f for f in plan.flags)


@pytest.mark.parametrize("select,params", [
    ({}, {}), ({}, {"colour": "red"}), ({}, {"shading": "grey"}), ({}, {"shading": "#D9D9D9"}),
    ({}, {"vertical_alignment": "middle"}), ({}, {"no_wrap": "yes"}), ({}, {"width": 0}), ({}, {"width": 23}),
    ({}, {"width": True}), ({}, {"shading": "none", "tracked": True}),
    ({"rows": {"header": True}}, {"width": 1}),                                # a column's width is the whole column's
    ({"cells": "all"}, {"shading": "none"}), ({"rows": {"index": 1}}, {"shading": "none"}),
])
def test_set_table_cells_parameters(src, select, params):
    refused(src, "set_table_cells", select, params, "bad-params")


# ---- merge_table_cells --------------------------------------------------------------------------------------------------

def test_a_title_row_merged_across(src):
    plan, out, rec = run(src, "merge_table_cells", {"tables": PUMPS, "rows": {"index_range": [0, 0]},
                                                    "columns": {"index": [0, 1]}}, {})
    assert rec.passed, rec.to_markdown()
    first = cells(rows(tables(out)[0])[0])[0]
    assert kids(first.find(W + "tcPr")) == ["tcW", "gridSpan", "shd"] and tcw(first) == ("dxa", "4320")
    assert [text_of(p) for p in first.iter(W + "p")] == ["Tag", "Description"]
    assert len(cells(rows(tables(out)[0])[0])) == 3 and not plan.diffs and plan.mode == "untracked"


def test_a_block_of_rows_merged_down_past_the_cells_beside_it(src):
    plan, out, rec = run(src, "merge_table_cells", {"tables": PUMPS, "rows": {"index_range": [1, 2]},
                                                    "columns": {"index": [1, 2]}}, {})
    assert rec.passed, rec.to_markdown()
    t0 = tables(out)[0]
    anchor, cont = cells(rows(t0)[1])[1], cells(rows(t0)[2])[1]
    assert [text_of(p) for p in anchor.iter(W + "p")] == ["Base-mounted pump", "2 in", "Base-mounted pump", "2 in"]
    assert kids(anchor.find(W + "tcPr")) == ["tcW", "gridSpan", "vMerge"] and tcw(anchor) == ("dxa", "5040")
    assert anchor.find(f"{W}tcPr/{W}vMerge").get(W + "val") == "restart"
    assert cont.find(f"{W}tcPr/{W}vMerge").attrib == {} and kids(cont) == ["tcPr", "p"] and not text_of(cont)
    assert row_texts(t0)[1:3] == ["P-1|Base-mounted pump2 inBase-mounted pump2 in|Duty", "P-2||Standby"]
    # the second row's paragraphs pass "Duty": a deletion and an insertion each; the continuing cell's new paragraph
    assert sorted((d.block_index, d.before, d.after) for d in plan.diffs) == [
        (9, None, "Base-mounted pump"), (10, None, "2 in"), (11, "Base-mounted pump", None), (12, "2 in", None),
        (13, None, "")]
    assert plan.mode == "untracked"


def test_empty_cells_add_nothing(src):
    plan, out, rec = run(src, "merge_table_cells", {"tables": ITEMS, "rows": {"index_range": [1, 2]},
                                                    "columns": {"index": [1, 2]}}, {})
    assert rec.passed, rec.to_markdown()
    anchor = cells(rows(tables(out)[4])[1])[1]
    assert [text_of(p) for p in anchor.iter(W + "p")] == ["3", "12"]
    # the two empty cells' paragraphs go; "12" passes "Valves" (a cell beside the block) on its way up
    assert sorted((d.before or "", d.after or "") for d in plan.diffs) == [("", ""), ("", ""), ("", ""), ("", "12"),
                                                                           ("12", "")]
    plan, out, rec = run(src, "merge_table_cells", {"tables": ITEMS, "rows": {"index_range": [1, 2]},
                                                    "columns": {"index": [2]}}, {})
    assert rec.passed, rec.to_markdown()                                    # every cell empty: the anchor keeps its own
    assert len(list(cells(rows(tables(out)[4])[1])[2].iter(W + "p"))) == 1


def test_a_block_that_is_one_cell_already(src):
    for rs, cs in (([2, 3], [3]), ([4, 4], [0, 1]), ([1, 1], [2])):
        plan, out, rec = run(src, "merge_table_cells", {"tables": PUMPS, "rows": {"index_range": rs},
                                                        "columns": {"index": cs}}, {})
        assert rec.passed and out == src and any("one cell already" in n for n in plan.notes), (rs, cs)


def test_a_nested_table_merged_inside_a_merge(src):
    plan, out, rec = run(src, "merge_table_cells", {"tables": {"text_regex": "^(Swing|Rating)$"},
                                                    "rows": {"index_range": [0, 1]}, "columns": {"index": [1]}}, {})
    assert rec.passed, rec.to_markdown()
    assert plan.property_assertions[0]["changed"] == {MAIN: [[2, [0, 1], [1, 1]], [3, [0, 1], [1, 1]]]}
    outer = cells(rows(tables(out)[2])[0])[1]
    assert outer.find(W + "tbl") is not None and text_of(outer).startswith("Detail")
    assert row_texts(tables(out)[3]) == ["Size|Rating175 psi", "4 in|"]


def test_merges_in_the_header_part_and_by_the_rows_text(src):
    plan, out, rec = run(src, "merge_table_cells", {"scope": ["headers"], "rows": "all", "columns": "all"}, {})
    assert rec.passed and row_texts(tables(out, HEADER)[0]) == ["SECTION23 21 13"]
    plan, out, rec = run(src, "merge_table_cells", {"tables": PUMPS, "rows": {"text_regex": "^P-[12]"},
                                                    "columns": {"header_regex": "^Size$"}}, {})
    assert rec.passed, rec.to_markdown()
    assert row_texts(tables(out)[0])[1:3] == ["P-1|Base-mounted pump|2 in2 in|Duty", "P-2|Base-mounted pump||Standby"]


def _tracked_row(root):
    rows(tbls(root)[0])[2].insert(0, frag_xml('<w:trPr><w:ins w:id="77" w:author="Jack" '
                                              'w:date="2026-09-01T08:00:00Z"/></w:trPr>'))


def _continues_below(root):
    """Table 4: row 2's first cell continues a merge opening there (nothing above it merges)."""
    cells(rows(tbls(root)[4])[2])[0].find(W + "tcPr").append(frag_xml("<w:vMerge/>"))


def _crossing_bookmark(root):
    t0 = tbls(root)[0]
    cells(rows(t0)[1])[3].find(W + "p").insert(0, frag_xml('<w:bookmarkStart w:id="41" w:name="_Duty"/>'))
    cells(rows(t0)[2])[1].find(W + "p").append(frag_xml('<w:bookmarkEnd w:id="41"/>'))


BLOCK = {"tables": PUMPS, "rows": {"index_range": [1, 2]}, "columns": {"index": [1, 2]}}


@pytest.mark.parametrize("plant,select,code", [
    (None, {"tables": PUMPS, "rows": {"text_regex": "^P-[13]"}, "columns": {"index": [1]}}, "table-shape"),
    (None, {"tables": PUMPS, "rows": {"index_range": [1, 1]}, "columns": {"index": [0, 2]}}, "table-shape"),
    (None, {"tables": ZONES, "rows": {"index_range": [0, 1]}, "columns": {"index": [0]}}, "table-shape"),
    (None, {"tables": PUMPS, "rows": {"index_range": [3, 4]}, "columns": {"index": [1]}}, "merge-overlap"),
    (None, {"tables": PUMPS, "rows": {"index_range": [1, 2]}, "columns": {"index": [3]}}, "merge-overlap"),
    (_continues_below, {"tables": ITEMS, "rows": {"index_range": [0, 1]}, "columns": {"index": [0]}}, "merge-overlap"),
    (_hmerge, BLOCK, "table-grid"),
    (_cell_change, BLOCK, "existing-revisions"),
    (_tracked_row, BLOCK, "existing-revisions"),
    (_crossing_bookmark, BLOCK, "table-anchor"),
    (None, {"tables": PUMPS, "rows": {"text_regex": "^Nothing"}, "columns": {"index": [1]}}, "not-found"),
])
def test_merge_table_cells_refusals(src, plant, select, code):
    refused(mutate(src, MAIN, plant) if plant else src, "merge_table_cells", select, {}, code)


def test_a_bookmark_inside_the_block_moves_with_its_text(src):
    def inside(root):
        t0 = tbls(root)[0]
        cells(rows(t0)[1])[1].find(W + "p").insert(0, frag_xml('<w:bookmarkStart w:id="42" w:name="_Pump"/>'))
        cells(rows(t0)[2])[2].find(W + "p").append(frag_xml('<w:bookmarkEnd w:id="42"/>'))
    plan, out, rec = run(mutate(src, MAIN, inside), "merge_table_cells", BLOCK, {})
    assert rec.passed, rec.to_markdown()
    plan, out, rec = run(mutate(src, MAIN, _crossing_bookmark), "merge_table_cells",
                         {**BLOCK, "rows": {"index_range": [1, 1]}}, {})       # one row: nothing passes the bookmark
    assert rec.passed, rec.to_markdown()


# ---- a wrong output is caught ---------------------------------------------------------------------------------------

def test_wrong_outputs_of_a_column_deletion_are_caught(src):
    plan, out, rec = run(src, "delete_table_columns", {"tables": PUMPS, "columns": {"index": [1]}}, {})
    assert rec.passed
    _caught(src, plan, out, MAIN, lambda r: tbls(r)[0].find(f"{W}tblPr/{W}tblW").set(W + "w", "8640"), "is not the source")
    _caught(src, plan, out, MAIN, lambda r: cells(rows(tbls(r)[0])[4])[0].find(f"{W}tcPr/{W}tcW").set(W + "w", "4320"))
    _caught(src, plan, out, MAIN, lambda r: tbls(r)[0].find(W + "tblGrid").append(frag_xml('<w:gridCol w:w="2880"/>')))
    plan, out, rec = run(src, "delete_table_columns", {"tables": ZONES, "columns": {"index": [0]}}, {})
    assert rec.passed
    _caught(src, plan, out, MAIN, lambda r: rows(tbls(r)[1])[1].insert(0, frag_xml(
        '<w:trPr><w:gridBefore w:val="1"/></w:trPr>')))


def test_wrong_outputs_of_cell_formatting_are_caught(src):
    plan, out, rec = run(src, "set_table_cells", {"tables": PUMPS, "columns": {"index": [1]}},
                         {"width": 2.5, "shading": "FFFF00"})
    assert rec.passed, rec.to_markdown()
    _caught(src, plan, out, MAIN, lambda r: cells(rows(tbls(r)[0])[2])[1].find(f"{W}tcPr/{W}shd").set(
        W + "fill", "FF0000"), "w:tcPr is")
    _caught(src, plan, out, MAIN, lambda r: tbls(r)[0].find(W + "tblGrid")[1].set(W + "w", "3601"), "w:tblGrid is")
    _caught(src, plan, out, MAIN, lambda r: tbls(r)[0].find(f"{W}tblPr/{W}tblW").set(W + "w", "8640"), "w:tblPr is")

    def misplaced(r):                                          # shading before the width: out of schema order
        pr = cells(rows(tbls(r)[0])[1])[1].find(W + "tcPr")
        pr.insert(0, pr.find(W + "shd"))
    _caught(src, plan, out, MAIN, misplaced, "w:tcPr is")
    _caught(src, plan, out, MAIN, lambda r: cells(rows(tbls(r)[0])[1])[0].find(W + "tcPr").append(
        frag_xml('<w:shd w:val="clear" w:color="auto" w:fill="FFFF00"/>')))   # a cell not chosen: the residual
    plan, out, rec = run(src, "set_table_cells", {"tables": ZONES, "columns": {"index": [0]}}, {"width": 1.5})
    assert rec.passed
    _caught(src, plan, out, MAIN, lambda r: rows(tbls(r)[1])[1].find(f"{W}trPr/{W}wBefore").set(W + "w", "3120"))


def test_wrong_outputs_of_a_merge_are_caught(src):
    plan, out, rec = run(src, "merge_table_cells", BLOCK, {})
    assert rec.passed
    _caught(src, plan, out, MAIN, lambda r: cells(rows(tbls(r)[0])[2])[1].find(f"{W}tcPr/{W}vMerge").set(
        W + "val", "restart"), "is not the source with")
    _caught(src, plan, out, MAIN, lambda r: cells(rows(tbls(r)[0])[1])[1].find(f"{W}tcPr/{W}tcW").set(W + "w", "2880"))

    def swapped(r):                                            # the merged text out of reading order
        anchor = cells(rows(tbls(r)[0])[1])[1]
        ps = anchor.findall(W + "p")
        anchor.remove(ps[1])
        anchor.append(ps[1])
    bad = mutate(out, MAIN, swapped)
    rec = run_suite(src, bad, plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec)


# ---- a wrong implementation shared by plan and apply is caught ------------------------------------------------------

def test_a_shared_wrong_column_selection_is_caught(src, monkeypatch):
    real = table_columns.selected_columns
    shifted = lambda tbl, pred: [g + 1 for g in real(tbl, pred) if g + 1 < len(table_columns.grid_columns_of(tbl))]
    for module in (dtc, stc, mtc):
        monkeypatch.setattr(module, "selected_columns", shifted)
    plan, out, rec = run(src, "delete_table_columns", {"tables": PUMPS, "columns": {"index": [1]}}, {})
    assert "V10" in failed(rec) and "not the plan's" in v10(rec)
    plan, out, rec = run(src, "set_table_cells", {"tables": PUMPS, "columns": {"index": [1]}}, {"shading": "FFFF00"})
    assert "V10" in failed(rec)
    plan, out, rec = run(src, "merge_table_cells", {"tables": PUMPS, "rows": {"index_range": [1, 1]},
                                                    "columns": {"index": [0, 1]}}, {})
    assert "V10" in failed(rec)


def test_a_shared_grid_that_ignores_the_columns_before_a_row_is_caught(src, monkeypatch):
    real = table_columns.row_layout

    def early(tr):
        lay = real(tr)
        return table_columns.Layout(before=0, cells=[(s - lay.before, n, tc) for s, n, tc in lay.cells], after=lay.after)
    for module in (dtc, stc):
        monkeypatch.setattr(module, "row_layout", early)
    plan, out, rec = run(src, "set_table_cells", {"tables": ZONES, "columns": {"index": [0]}},
                         {"vertical_alignment": "top"})
    assert "V10" in failed(rec)
    plan, out, rec = run(src, "delete_table_columns", {"tables": ZONES, "columns": {"index": [2]}}, {})
    assert "V10" in failed(rec) and "is not the source with" in v10(rec)


def test_a_shared_vertical_merge_rule_of_the_deletion_is_caught(src, monkeypatch):
    monkeypatch.setattr(dtc, "vertical_merges", lambda tbl: [])
    plan, out, rec = run(mutate(src, MAIN, _split_merge), "delete_table_columns",
                         {"tables": PUMPS, "columns": {"index": [2]}}, {})
    assert "V10" in failed(rec) and "vertical-merge" in v10(rec)


def test_a_shared_deletion_that_keeps_a_fixed_width_is_caught(src, monkeypatch):
    real = dtc.deletion

    def keeps(tbl, cols, where):
        d = real(tbl, cols, where)
        d.width = None
        return d
    monkeypatch.setattr(dtc, "deletion", keeps)
    plan, out, rec = run(src, "delete_table_columns", {"tables": PUMPS, "columns": {"index": [1]}}, {})
    assert "V10" in failed(rec) and "is not the source with" in v10(rec)


def test_a_shared_empty_cell_rule_that_keeps_every_paragraph_is_caught(src, monkeypatch):
    monkeypatch.setattr(mtc, "cell_is_empty", lambda tc: False)
    plan, out, rec = run(src, "merge_table_cells", {"tables": ITEMS, "rows": {"index_range": [1, 2]},
                                                    "columns": {"index": [1, 2]}}, {})
    assert "V10" in failed(rec) and "is not the source with" in v10(rec)


def test_a_shared_width_sum_and_merge_rule_are_caught(src, monkeypatch):
    monkeypatch.setattr(mtc, "summed_width", lambda cells: (None, False))
    plan, out, rec = run(src, "merge_table_cells", {"tables": PUMPS, "rows": {"index_range": [0, 0]},
                                                    "columns": {"index": [0, 1]}}, {})
    assert "V10" in failed(rec)
    monkeypatch.undo()
    monkeypatch.setattr(mtc, "vertical_merges", lambda tbl: [])
    plan, out, rec = run(src, "merge_table_cells", {"tables": PUMPS, "rows": {"index_range": [3, 4]},
                                                    "columns": {"index": [3]}}, {})
    assert "V10" in failed(rec) and "merge-overlap" in v10(rec)


def test_a_shared_order_rule_of_the_merge_is_caught(src, monkeypatch):
    monkeypatch.setattr(mtc, "_order_problem", lambda root, work, pos: None)
    plan, out, rec = run(mutate(src, MAIN, _crossing_bookmark), "merge_table_cells", BLOCK, {})
    assert "V10" in failed(rec) and "table-anchor" in v10(rec)


def test_a_shared_width_rule_that_leaves_the_fixed_width_is_caught(src, monkeypatch):
    real = stc.widths

    def leaves(tbl, cols, twips, where):
        out = real(tbl, cols, twips, where)
        out.table = None
        return out
    monkeypatch.setattr(stc, "widths", leaves)
    plan, out, rec = run(src, "set_table_cells", {"tables": PUMPS, "columns": {"index": [1]}}, {"width": 2.5})
    assert "V10" in failed(rec) and "not the plan's" in v10(rec)


def test_a_shared_shading_rule_that_ignores_the_style_is_caught(src, monkeypatch):
    monkeypatch.setattr(stc, "expected_shading", lambda asked, chain: None if asked == "none" else
                        stc.build("shd", [("val", "clear"), ("color", "auto"), ("fill", asked)]))
    plan, out, rec = run(src, "set_table_cells", {"tables": ITEMS}, {"shading": "none"})
    assert "V10" in failed(rec)


# ---- conflicts and composition ----------------------------------------------------------------------------------------

def _spec(*ops, mode="formatting"):
    return {"request": "r", "summary": "the tables", "mode": mode, "ops": list(ops)}


def _conflict(src, *specs):
    pkg = DocxPackage(src)
    frags = [get_op(o).plan(pkg, s, p) for o, s, p in specs]
    with pytest.raises(JobException) as e:
        merge_fragments(frags)
    assert e.value.code == "conflict"


def test_the_grid_ops_conflict_with_the_table_ops_on_the_same_table(src):
    deleting = ("delete_table_columns", {"tables": PUMPS, "columns": {"index": [1]}}, {})
    merging = ("merge_table_cells", BLOCK, {})
    for other in (("set_table_props", {"tables": PUMPS}, {"cant_split": True}),
                  ("apply_table_style", {"tables": PUMPS}, {"style": "Table Grid"}),
                  ("delete_table_rows", {"tables": PUMPS, "rows": {"index_range": [1, 1]}}, {"tracked": False}),
                  ("set_table_cells", {"tables": PUMPS}, {"shading": "FFFF00"})):
        _conflict(src, other, deleting)
        _conflict(src, merging, other)
    _conflict(src, ("set_table_cells", {"tables": ITEMS}, {"shading": "none"}),          # it reads the style
              ("apply_table_style", {"tables": ITEMS}, {"style": "Table Grid"}))
    _conflict(src, ("set_table_cells", {"tables": PUMPS, "columns": {"index": [1]}}, {"width": 2}),
              ("set_table_props", {"tables": PUMPS}, {"width": {"pct": 100}}))
    _conflict(src, ("set_table_cells", {"tables": PUMPS, "rows": {"index_range": [1, 1]}}, {"shading": "FFFF00"}),
              ("insert_table_rows", {"tables": PUMPS, "rows": {"index_range": [1, 1]}},
               {"rows": [["a", "b", "c", "d"]], "position": "before", "tracked": False}))


def test_independent_cell_and_table_formatting_share_one_step(src):
    plan, out, rec = run_ops(src, [
        ("set_table_cells", {"tables": PUMPS, "rows": {"index_range": [1, 4]}}, {"shading": "FFF2CC"}),
        ("set_table_props", {"tables": PUMPS}, {"borders": "none"}),
        ("set_table_cells", {"tables": ITEMS, "columns": {"index": [1]}}, {"vertical_alignment": "center"}),
        ("set_properties", {"paragraphs": {"text_regex": "^1\\.1"}}, {"run": {"b": True}, "tracked": False})])
    assert rec.passed, rec.to_markdown()


def test_overlapping_table_ops_are_composed_into_steps(src):
    spec = _spec({"op": "merge_table_cells", "select": {"tables": PUMPS, "rows": {"index_range": [0, 0]},
                                                         "columns": {"index": [0, 1]}}},
                 {"op": "set_table_cells", "select": {"tables": PUMPS, "rows": {"index_range": [0, 0]}},
                  "params": {"shading": "BFBFBF"}},
                 {"op": "delete_table_columns", "select": {"tables": PUMPS, "columns": {"header_regex": "^Notes$"}}},
                 {"op": "delete_table_rows", "select": {"tables": PUMPS, "rows": {"text_regex": "^P-3"}},
                  "params": {"tracked": False}},
                 {"op": "set_table_props", "select": {"tables": PUMPS}, "params": {"cant_split": True}},
                 mode="untracked")
    validate_spec(spec)
    chain = split_into_stages(DocxPackage(src), spec)
    assert chain is not None and len(chain["stages"]) >= 4
    current = DocxPackage(src)
    for plan, frags in plan_chain(current, chain):
        nxt = apply_spec(current, frags)
        rec = run_suite(current.output_bytes(), nxt.output_bytes(), plan.to_dict(), "in.docx", "out.docx")
        assert rec.passed, rec.to_markdown()
        current = nxt
    t0 = tables(current.output_bytes())[0]
    assert row_texts(t0) == ["TagDescription|Size", "P-1|Base-mounted pump|2 in", "P-2|Base-mounted pump|2 in",
                             "Totals|5 in"]
    assert cells(rows(t0)[0])[0].find(f"{W}tcPr/{W}shd").get(W + "fill") == "BFBFBF"


def test_the_selection_reads_what_it_chose_by(src):
    pkg = DocxPackage(src)
    touches = set(get_op("delete_table_columns").plan(pkg, {"tables": PUMPS, "columns": {"header_regex": "^Notes"}},
                                                      {}).touches)
    assert {(MAIN, "*", "text"), (MAIN, "scope", "verify"), (MAIN, "5", "verify:text")} <= touches
    touches = set(get_op("set_table_cells").plan(pkg, {"tables": ITEMS, "rows": {"header": True}},
                                                 {"shading": "none"}).touches)
    assert (MAIN, "tbl:4", "verify-props:tblPr") in touches and (MAIN, "tc:4:1:0", "shd") in touches
    assert ("word/styles.xml", "style:ArchShaded", "verify:tblPr") in touches
    assert not any(t == "*" for _m, t, _a in touches)                      # a formatting op: cell by cell


# ---- the catalog, the explanations, the pipeline and a render -------------------------------------------------------

NEW_OPS = ("delete_table_columns", "set_table_cells", "merge_table_cells")


def test_catalog_sheet_and_assumptions():
    cat, sheet = op_catalog(), op_sheet()
    for op in NEW_OPS:
        assert op in cat["ops"] and op in sheet["ops"] and COMMON_ASSUMPTIONS[op]
    validate_spec(_spec({"op": "set_table_cells", "select": {"tables": PUMPS, "columns": {"index": [1]}},
                         "params": {"width": 2}},
                        {"op": "merge_table_cells", "select": BLOCK},
                        {"op": "delete_table_columns", "select": {"columns": {"header_regex": "^Notes$"}}}))


def test_the_grid_ops_place_no_words():
    spec = _spec({"op": "set_table_cells", "params": {"shading": "D9D9D9"}},
                 {"op": "merge_table_cells", "select": BLOCK}, {"op": "delete_table_columns", "select": {"columns": {}}})
    assert placed_words(spec) == []


def test_every_refusal_code_of_the_grid_ops_is_explained():
    codes = set()
    here = Path(table_columns.__file__).parent
    for name in ("table_columns", "delete_table_columns", "set_table_cells", "merge_table_cells"):
        for n in ast.walk(ast.parse((here / f"{name}.py").read_text(encoding="utf-8"))):
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "JobException" and len(n.args) > 1:
                assert isinstance(n.args[1], ast.Constant), (name, n.lineno)
                codes.add(n.args[1].value)
    assert {"every-column", "table-grid", "merge-overlap", "table-shape", "table-structure", "vertical-merge",
            "existing-revisions", "table-anchor", "textbox", "not-found", "bad-params"} <= codes
    codes |= {"section-break"}                                  # table_edit.removal's, over the deleted cells
    assert sorted(c for c in codes if c not in FAILURE_EXPLANATIONS) == []


def test_a_spec_runs_through_the_batch_pipeline(src, tmp_path):
    from spec_surgeon.batch import run_batch
    spec = _spec({"op": "set_table_cells", "select": {"rows": {"header": True}},
                  "params": {"shading": "D9D9D9", "vertical_alignment": "center"}},
                 {"op": "merge_table_cells", "select": {"scope": ["headers"], "rows": "all", "columns": "all"}},
                 mode="untracked")
    path = tmp_path / "23 21 13 – Hydronic Piping.docx"
    path.write_bytes(src)
    batch = run_batch([str(path)], "edit-spec", {}, tmp_path / "out", workers=1, renderer="none", do_render=False,
                      spec=spec)
    f = batch.files[0]
    assert f.status == "PASS", f.record.to_markdown() if f.record else f.exception


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
def test_libreoffice_opens_and_renders_the_output(src, tmp_path):
    from spec_surgeon.render import libreoffice
    plan, out, rec = run(src, "delete_table_columns", {"tables": {"first_row_regex": "^(Tag|Zone)"},
                                                       "columns": {"header_regex": "^(Description|Floor)$"}}, {})
    assert rec.passed, rec.to_markdown()
    plan, out2, rec = run(out, "merge_table_cells", {"tables": PUMPS, "rows": {"index_range": [1, 3]},
                                                     "columns": {"index": [0]}}, {})
    assert rec.passed, rec.to_markdown()
    plan, out3, rec = run(out2, "set_table_cells", {"rows": {"header": True}},
                          {"shading": "BFBFBF", "vertical_alignment": "center", "no_wrap": True})
    assert rec.passed, rec.to_markdown()
    assert TITLE in text_of(DocxPackage(out3).xml(MAIN))
    (tmp_path / "out.docx").write_bytes(out3)
    pdfs = libreoffice.render_pdfs([tmp_path / "out.docx"], tmp_path / "pdf", workers=1)
    if len(pdfs) != 1:
        pytest.skip("LibreOffice did not render here")
    pdf = pdfs[tmp_path / "out.docx"]
    assert pdf.exists() and pdf.stat().st_size > 1000 and pdf.read_bytes().startswith(b"%PDF")
