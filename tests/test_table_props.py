"""set_table_props and V10's ``table_props`` kind (built-in batch 5, item 37 — docs/BUILTIN_OPS_BATCH5_DESIGN.md §5.1–§5.2):
the table selector on both sides; end to end on the tables fixture (borders against the table styles, merged cell margins,
width, header rows and row splitting, cell overrides, headers' tables, already as asked); a wrong OUTPUT per rule; wrong
IMPLEMENTATIONS shared by plan and apply; the two sides' decisions on every table; the schema order pinned to the bundled
schema; plan-time conflicts (rule 9); parameters and refusals; the batch pipeline and a LibreOffice render."""
from __future__ import annotations

import ast
import io
import shutil
import zipfile
from pathlib import Path
from typing import List, Tuple

import pytest
from lxml import etree

from make_fixtures import build_styles, build_tables
from spec_surgeon import ooxml
from spec_surgeon.catalog import COMMON_ASSUMPTIONS, op_catalog
from spec_surgeon.editspec import apply_spec, plan_spec
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import set_table_props as stp_module
from spec_surgeon.ops import table_select as tsel
from spec_surgeon.ops.base import verify_family
from spec_surgeon.ops.selectors import StyleSheet
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.report import FAILURE_EXPLANATIONS
from spec_surgeon.verify import run_suite
from spec_surgeon.verify import tableprops as vtp
from spec_surgeon.verify import tableselect as vts
from spec_surgeon.verify.props import Styles, sig
from spec_surgeon.verify.reader import VPackage

MAIN = "word/document.xml"
HEADER = "word/header1.xml"
STYLES = "word/styles.xml"
SINGLE = {"val": "single", "sz": "4", "space": "0", "color": "auto"}
NIL = {"val": "nil"}
EXAMPLE = {"borders": {"top": {}, "bottom": {}, "insideH": {}}, "cell_margins": {"left": 115, "right": 115},
           "width": {"pct": 100}, "repeat_header_rows": 1, "cant_split": True}


@pytest.fixture(scope="module")
def src() -> bytes:
    return build_tables()


def run_ops(data: bytes, specs: List[Tuple[str, dict, dict]]):
    pkg = DocxPackage(data)
    frags = [get_op(o).plan(pkg, sel, par) for o, sel, par in specs]
    plan = merge_fragments(frags)
    work = pkg.copy()
    for f in frags:
        get_op(f.op).apply(work, f)
    out = work.output_bytes()
    return plan, out, run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")


def stp(data: bytes, select: dict, params: dict):
    return run_ops(data, [("set_table_props", select, params)])


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


def el(tag, **attrs):
    e = etree.Element(W + tag)
    for k, v in attrs.items():
        e.set(W + k, v)
    return e


def tables(data: bytes, part: str = MAIN):
    return list(DocxPackage(data).xml(part).iter(W + "tbl"))


def tbls(root):
    return list(root.iter(W + "tbl"))


def rows(tbl):
    return [c for c in tbl if c.tag == W + "tr"]


def cells(tr):
    return [c for c in tr if c.tag == W + "tc"]


def local(e) -> str:
    return etree.QName(e).localname


def box(tbl) -> List[Tuple[str, dict]]:
    b = tbl.find(f"{W}tblPr/{W}tblBorders")
    return [(local(s), {local(k): v for k, v in s.attrib.items()}) for s in (b if b is not None else [])]


def kids(el_) -> List[str]:
    return [local(c) for c in el_] if el_ is not None else []


def attrs(e):
    return None if e is None else {local(k): v for k, v in e.attrib.items()}


def _touches(data, select, params, op="set_table_props"):
    return set(map(tuple, get_op(op).plan(DocxPackage(data), select, params).touches))


def _caught(src, plan, out, part, fn, needle=None):
    bad = mutate(out, part, fn)
    rec = run_suite(src, bad, plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec), rec.to_markdown()
    if needle:
        assert needle in v10(rec), v10(rec)


# ---- end to end ------------------------------------------------------------------------------------------------------

def test_the_worked_example_on_every_table(src):
    """Top/bottom/between-rows rules, margins, full width, one header row, rows kept whole: each table gets exactly the
    elements that differ, a side its style shows is cancelled with nil, a table already as asked keeps its own."""
    plan, out, rec = stp(src, {}, EXAMPLE)
    assert rec.passed, rec.to_markdown()
    a = plan.property_assertions[0]
    assert a["kind"] == "table_props" and a["parts"] == [MAIN] and a["expected_tables"] == 8
    assert plan.mode == "formatting" and plan.geometry_reference == "none" and not any(plan.rev_delta.values())
    t = tbls(DocxPackage(out).xml(MAIN))
    # Table Grid shows all six sides: the three not asked are cancelled
    assert box(t[0]) == [("top", SINGLE), ("left", NIL), ("bottom", SINGLE), ("right", NIL), ("insideH", SINGLE),
                         ("insideV", NIL)]
    # the default table style shows none: only the asked sides
    assert box(t[1]) == [("top", SINGLE), ("bottom", SINGLE), ("insideH", SINGLE)]
    # ArchGrid shows exactly the asked three: nothing to cancel
    assert box(t[2]) == [("top", SINGLE), ("bottom", SINGLE), ("insideH", SINGLE)]
    # schema order kept: tblStyle, tblW, tblBorders, shd, tblCellMar, tblLook
    assert kids(t[0].find(W + "tblPr")) == ["tblStyle", "tblW", "tblBorders", "shd", "tblCellMar", "tblLook"]
    assert attrs(t[0].find(f"{W}tblPr/{W}tblW")) == {"w": "5000", "type": "pct"}
    # table 1 was already 100 % wide with these margins: only its borders changed
    assert a["changed"][MAIN]["tables"][1] == [1, ["tblBorders"]]
    # rows: the first repeats, every row is kept whole; table 1's own header row and split row are left alone
    for tbl in t:
        if tbl.getparent().tag == W + "tc" and tbl is not t[5]:
            continue
        rs = rows(tbl)
        assert rs[0].find(f"{W}trPr/{W}tblHeader") is not None
        assert all(r.find(f"{W}trPr/{W}cantSplit") is not None for r in rs)
    # ArchHeader turns the header row on for every row: the second row gets an explicit off
    assert attrs(rows(t[6])[1].find(f"{W}trPr/{W}tblHeader")) == {"val": "0"}
    assert [1, 0, ["cantSplit"]] in a["changed"][MAIN]["rows"] and [1, 1] not in [x[:2] for x in a["changed"][MAIN]["rows"]]
    # the header's table is out of scope
    assert DocxPackage(out).read(HEADER) == DocxPackage(src).read(HEADER)


def test_none_cancels_what_the_styles_show_and_removes_direct_borders(src):
    plan, out, rec = stp(src, {}, {"borders": "none"})
    assert rec.passed, rec.to_markdown()
    t = tbls(DocxPackage(out).xml(MAIN))
    assert box(t[0]) == [(s, NIL) for s in ("top", "left", "bottom", "right", "insideH", "insideV")]
    assert box(t[1]) == [] and t[1].find(f"{W}tblPr/{W}tblBorders") is None
    assert box(t[2]) == [(s, NIL) for s in ("top", "bottom", "insideH")]
    assert box(t[3]) == [(s, NIL) for s in ("top", "left", "bottom", "right", "insideH", "insideV")]  # via TableGrid
    a = plan.property_assertions[0]
    assert [k for k, _ in a["changed"][MAIN]["tables"]] == [0, 2, 3, 5, 7]
    assert any("already as asked, left alone: 3 table(s)" in n for n in plan.notes)
    assert any("conditional formatting" in f for f in plan.flags)               # ArchBanded's firstRow borders
    assert any("w:tblPrEx" in f for f in plan.flags)                             # table 3's row 1


def test_rows_may_break_and_no_header_rows_against_styles_that_turn_them_on(src):
    plan, out, rec = stp(src, {}, {"cant_split": False, "repeat_header_rows": 0})
    assert rec.passed, rec.to_markdown()
    t = tbls(DocxPackage(out).xml(MAIN))
    # ArchGrid keeps rows whole: an explicit off on its rows; table 1's own elements simply go (trPr emptied → gone)
    assert all(attrs(r.find(f"{W}trPr/{W}cantSplit")) == {"val": "0"} for r in rows(t[2]))
    assert all(r.find(W + "trPr") is None for r in rows(t[1]))
    # ArchHeader repeats every row: explicit offs; the pending trPrChange of row 0 stays (flagged)
    assert [attrs(r.find(f"{W}trPr/{W}tblHeader")) for r in rows(t[6])] == [{"val": "0"}, {"val": "0"}]
    assert kids(rows(t[6])[0].find(W + "trPr")) == ["tblHeader", "trPrChange"]
    assert any("pending tracked property change" in f for f in plan.flags)
    a = plan.property_assertions[0]
    assert a["expected_rows"] == 6 and a["expected_tables"] == 3


def test_cell_margins_merge_into_the_tables_own(src):
    """Table 1 has left/right 115: asking top 50 keeps them and puts top first; table 0 (none of its own) gets one."""
    plan, out, rec = stp(src, {"tables": {"index_range": [0, 1]}}, {"cell_margins": {"top": 50, "left": 115}})
    assert rec.passed, rec.to_markdown()
    t = tbls(DocxPackage(out).xml(MAIN))
    mar = t[1].find(f"{W}tblPr/{W}tblCellMar")
    assert [(local(c), attrs(c)) for c in mar] == [("top", {"w": "50", "type": "dxa"}), ("left", {"w": "115", "type": "dxa"}),
                                                    ("right", {"w": "115", "type": "dxa"})]
    assert [local(c) for c in t[0].find(f"{W}tblPr/{W}tblCellMar")] == ["top", "left"]
    assert any("keep their own margins" in f for f in plan.flags)                 # cell (2, 1) of table 0


def test_clear_cell_overrides_takes_the_cells_own_borders_and_margins(src):
    plan, out, rec = stp(src, {"tables": {"index_range": [0, 0]}},
                         {"borders": {"top": {}}, "cell_margins": {"left": 100}, "clear_cell_overrides": True})
    assert rec.passed, rec.to_markdown()
    t0 = tbls(DocxPackage(out).xml(MAIN))[0]
    assert kids(cells(rows(t0)[1])[0].find(W + "tcPr")) == ["tcW"]
    assert kids(cells(rows(t0)[2])[1].find(W + "tcPr")) == ["tcW"]
    assert kids(cells(rows(t0)[0])[0].find(W + "tcPr")) == ["tcW", "shd"]        # shading is not a border or margin
    a = plan.property_assertions[0]
    assert a["changed"][MAIN]["cells"] == [[0, 1, 0, ["tcBorders"]], [0, 2, 1, ["tcMar"]]] and a["expected_cells"] == 2
    assert not any("keep their own" in f for f in plan.flags)


def test_a_new_row_property_goes_after_the_rows_table_exceptions(src):
    plan, out, rec = stp(src, {"tables": {"style_in": ["ArchBanded"]}}, {"repeat_header_rows": 2})
    assert rec.passed, rec.to_markdown()
    r1 = rows(tbls(DocxPackage(out).xml(MAIN))[3])[1]
    assert [local(c) for c in r1][:2] == ["tblPrEx", "trPr"]


def test_the_headers_tables_and_an_already_table(src):
    plan, out, rec = stp(src, {"scope": ["headers"]}, {"borders": {"bottom": {"size": 8}}, "width": {"pct": 100}})
    assert rec.passed, rec.to_markdown()
    ht = tbls(DocxPackage(out).xml(HEADER))[0]
    assert box(ht) == [("bottom", {**SINGLE, "sz": "8"})]
    assert DocxPackage(out).read(MAIN) == DocxPackage(src).read(MAIN)
    # table 1 already is 100 % wide with left/right 115: untouched, uncounted, and the file unchanged
    plan, out, rec = stp(src, {"tables": {"first_row_regex": "^Valve"}}, {"width": {"pct": 100},
                                                                           "cell_margins": {"left": 115, "right": 115}})
    assert rec.passed and out == src and plan.property_assertions[0]["expected_tables"] == 0
    assert any("already as asked, left alone: 1 table(s)" in n for n in plan.notes)


def test_no_matching_table_is_a_noted_no_op(src):
    plan, out, rec = stp(src, {"tables": {"text_regex": "no such text"}}, {"borders": "none"})
    assert rec.passed and out == src
    assert any("no table in this file matches" in n for n in plan.notes)


def test_touches_declare_changes_and_reads(src):
    t = _touches(src, {"tables": {"index_range": [0, 0]}}, {"borders": {"top": {}}, "repeat_header_rows": 1})
    assert {(MAIN, "tbl:0", "tblProps"), (MAIN, "tbl:0", "tblBorders"), (MAIN, "tbl:0", "verify-props:tblPr"),
            (MAIN, "tbl:0", "verify-props:tblBorders"), (MAIN, "tr:0:0", "trPr"), (MAIN, "tr:0:0", "tblHeader"),
            (MAIN, "tr:0:1", "verify-props:tblHeader"), (MAIN, "scope", "verify"),
            (STYLES, "style:TableGrid", "verify:tblPr"), (STYLES, "style:TableNormal", "verify:replace")} <= t
    assert (MAIN, "tr:0:1", "trPr") not in t and (MAIN, "tbl:1", "verify-props:tblPr") not in t
    # an already-as-asked table is read, not changed
    t = _touches(src, {"tables": {"index_range": [1, 1]}}, {"width": {"pct": 100}})
    assert (MAIN, "tbl:1", "verify-props:tblW") in t and (MAIN, "tbl:1", "tblW") not in t
    # the selection's own reads: every table's style for style_in, the searched paragraphs' text for the regexes
    t = _touches(src, {"tables": {"style_in": ["TableGrid"]}}, {"width": "auto"})
    assert {(MAIN, f"tbl:{k}", "verify-props:tblPr") for k in range(8)} <= t
    t = _touches(src, {"tables": {"first_row_regex": "^Item"}}, {"width": "auto"})
    assert any(a == "verify:text" for _, _, a in t)


# ---- a wrong output per rule ---------------------------------------------------------------------------------------

def test_wrong_outputs_are_caught(src):
    plan, out, rec = stp(src, {}, EXAMPLE)
    assert rec.passed
    # a wrong side attribute
    _caught(src, plan, out, MAIN, lambda r: tbls(r)[0].find(f"{W}tblPr/{W}tblBorders/{W}top").set(W + "sz", "6"),
            "w:tblPr is")
    # a nil side left out: the style's left border shows again
    def no_nil(r):
        b = tbls(r)[0].find(f"{W}tblPr/{W}tblBorders")
        b.remove(b.find(W + "left"))
    _caught(src, plan, out, MAIN, no_nil, "left border shows")
    # another table property changed on a changed table
    _caught(src, plan, out, MAIN, lambda r: tbls(r)[0].find(W + "tblPr").insert(2, el("jc", val="center")), "w:tblPr is")
    # a margin replaced instead of merged
    _caught(src, plan, out, MAIN, lambda r: tbls(r)[0].find(f"{W}tblPr/{W}tblCellMar/{W}left").set(W + "w", "100"),
            "cell margin")
    # a later row left repeating
    _caught(src, plan, out, MAIN, lambda r: rows(tbls(r)[0])[1].find(W + "trPr").append(el("tblHeader")), "tblHeader")
    # a row property of an untouched row changed
    _caught(src, plan, out, MAIN, lambda r: rows(tbls(r)[1])[1].find(W + "trPr").append(el("trHeight", val="400")),
            "unpredicted change")
    # an element twice
    _caught(src, plan, out, MAIN, lambda r: rows(tbls(r)[0])[2].find(W + "trPr").append(el("cantSplit")), "2 times")
    # a table outside the selection changed
    _caught(src, plan, out, HEADER, lambda r: tbls(r)[0].find(W + "tblPr").insert(0, el("jc", val="center")),
            "unpredicted change")


def test_a_wrong_trpr_place_and_a_cell_left_uncleared_are_caught(src):
    plan, out, rec = stp(src, {"tables": {"style_in": ["ArchBanded"]}}, {"repeat_header_rows": 2})
    assert rec.passed

    def trpr_first(r):
        tr = rows(tbls(r)[3])[1]
        pr = tr.find(W + "trPr")
        tr.remove(pr)
        tr.insert(0, pr)
    _caught(src, plan, out, MAIN, trpr_first, "first property element")
    plan, out, rec = stp(src, {"tables": {"index_range": [0, 0]}}, {"borders": "none", "clear_cell_overrides": True})
    assert rec.passed
    _caught(src, plan, out, MAIN, lambda r: cells(rows(tbls(r)[0])[1])[0].find(W + "tcPr").append(etree.fromstring(
        f'<w:tcBorders xmlns:w="{W[1:-1]}"><w:top w:val="single"/></w:tcBorders>')), "keeps its own")


def test_a_no_op_change_on_an_already_table_is_caught(src):
    plan, out, rec = stp(src, {"tables": {"index_range": [1, 1]}}, {"width": {"pct": 100}})
    assert rec.passed and out == src
    _caught(src, plan, out, MAIN, lambda r: tbls(r)[1].find(f"{W}tblPr/{W}tblW").set(W + "w", "4000"), "width")


# ---- wrong implementations shared by plan and apply ----------------------------------------------------------------

def test_a_shared_border_set_without_nil_sides_is_caught(src, monkeypatch):
    real = stp_module.expected_borders

    def no_nil(asked, chain):
        e = real(asked, chain)
        for c in list(e if e is not None else []):
            if c.get(W + "val") == "nil":
                e.remove(c)
        return e if e is not None and len(e) else None
    monkeypatch.setattr(stp_module, "expected_borders", no_nil)
    plan, out, rec = stp(src, {"tables": {"index_range": [0, 0]}}, {"borders": {"top": {}}})
    assert box(tables(out)[0]) == [("top", SINGLE)]
    assert "V10" in failed(rec) and "w:tblPr is" in v10(rec) and "left border shows" in v10(rec)


def test_shared_margins_replaced_instead_of_merged_are_caught(src, monkeypatch):
    real = stp_module.expected_margins
    monkeypatch.setattr(stp_module, "expected_margins", lambda asked, own: real(asked, None))
    plan, out, rec = stp(src, {"tables": {"index_range": [1, 1]}}, {"cell_margins": {"top": 50}})
    assert [local(c) for c in tables(out)[1].find(f"{W}tblPr/{W}tblCellMar")] == ["top"]       # left/right lost
    assert "V10" in failed(rec) and "w:tblPr is" in v10(rec)


def test_shared_header_rows_on_every_row_are_caught(src, monkeypatch):
    real = stp_module.expected_row_element
    monkeypatch.setattr(stp_module, "expected_row_element",
                        lambda name, on, chain: real(name, True if name == "tblHeader" else on, chain))
    plan, out, rec = stp(src, {"tables": {"index_range": [0, 0]}}, {"repeat_header_rows": 1})
    assert all(r.find(f"{W}trPr/{W}tblHeader") is not None for r in rows(tables(out)[0]))
    assert "V10" in failed(rec) and "tblHeader is on in the output, asked off" in v10(rec)


def test_a_shared_explicit_off_rule_that_forgets_the_style_is_caught(src, monkeypatch):
    monkeypatch.setattr(stp_module, "chain_turns_on", lambda chain, name: False)
    plan, out, rec = stp(src, {"tables": {"style_in": ["ArchGrid"]}}, {"cant_split": False})
    assert plan.property_assertions[0]["expected_tables"] == 0                    # wrongly "already"
    assert "V10" in failed(rec) and "cantSplit is on in the output, asked off" in v10(rec)


def test_a_shared_already_rule_that_ignores_attributes_is_caught(src, monkeypatch):
    def tags_only(parent, name, expected):
        own = parent.findall(W + name) if parent is not None else []
        return len(own) == (expected is not None)
    monkeypatch.setattr(stp_module, "already", tags_only)
    plan, out, rec = stp(src, {"tables": {"index_range": [1, 1]}}, {"width": {"pct": 50}})
    assert plan.property_assertions[0]["expected_tables"] == 0                    # 100 % ≠ 50 %: wrongly "already"
    assert "V10" in failed(rec) and "plan predicted 0" in v10(rec) and "the width is" in v10(rec)


def test_a_shared_style_chain_without_based_on_is_caught(src, monkeypatch):
    real = stp_module.style_chain
    monkeypatch.setattr(stp_module, "style_chain", lambda tbl, sheet: (real(tbl, sheet)[0][:1], None))
    plan, out, rec = stp(src, {"tables": {"style_in": ["ArchBanded"]}}, {"borders": {"top": {}}})
    assert box(tables(out)[3]) == [("top", SINGLE)]                               # TableGrid's sides not cancelled
    assert "V10" in failed(rec) and "left border shows" in v10(rec)


def test_a_shared_element_written_last_is_caught(src, monkeypatch):
    def append(parent, name, e, order):
        for old in parent.findall(W + name):
            parent.remove(old)
        if e is not None:
            parent.append(e)
    monkeypatch.setattr(stp_module, "write_element", append)
    plan, out, rec = stp(src, {"tables": {"index_range": [2, 2]}}, {"width": {"pct": 100}})
    assert kids(tables(out)[2].find(W + "tblPr"))[-1] == "tblW"
    assert "V10" in failed(rec) and "w:tblPr is" in v10(rec)


def test_a_shared_writer_that_leaves_an_emptied_row_property_is_caught(src, monkeypatch):
    monkeypatch.setattr(stp_module, "_drop_if_empty", lambda e: None)
    plan, out, rec = stp(src, {"tables": {"index_range": [1, 1]}}, {"cant_split": False, "repeat_header_rows": 0})
    assert rows(tables(out)[1])[0].find(W + "trPr") is not None and not len(rows(tables(out)[1])[0].find(W + "trPr"))
    assert "V10" in failed(rec) and "w:trPr is" in v10(rec)


def test_a_shared_default_border_space_is_caught(src, monkeypatch):
    """The assertion carries the user's own words (a side without 'space'); the verifier applies its own default (0)."""
    real = stp_module.clean_params

    def one_by_default(params):
        a = real(params)
        if a.borders:
            a.borders = {s: [(k, "1" if k == "space" and "space" not in params["borders"][s] else v) for k, v in x]
                         for s, x in a.borders.items()}
        return a
    monkeypatch.setattr(stp_module, "clean_params", one_by_default)
    plan, out, rec = stp(src, {"tables": {"index_range": [1, 1]}}, {"borders": {"top": {}}})
    assert box(tables(out)[1]) == [("top", {**SINGLE, "space": "1"})]
    assert "V10" in failed(rec) and "top border shows" in v10(rec)


def test_a_shared_default_scope_is_caught(src, monkeypatch):
    real = stp_module.scope_parts
    monkeypatch.setattr(stp_module, "scope_parts",
                        lambda pkg, scope: real(pkg, ["document", "headers"] if scope == ["document"] else scope))
    plan, out, rec = stp(src, {}, {"borders": {"top": {}}})
    assert "V10" in failed(rec) and "scope resolves to" in v10(rec)


def test_a_shared_selector_that_counts_only_top_level_tables_is_caught(src, monkeypatch):
    real = stp_module.table_selected
    monkeypatch.setattr(stp_module, "table_selected",
                        lambda pred, tbl, k, sheet: real(pred, tbl, k, sheet) and not tsel.is_nested(tbl))
    plan, out, rec = stp(src, {}, {"width": {"pct": 100}})
    assert "V10" in failed(rec) and "not the plan's" in v10(rec)


def test_without_the_reads_the_post_condition_still_catches_a_stale_table(src, monkeypatch):
    """Plant the old behaviour (no reads): a clearing table style op beside borders on a table already as asked plans,
    and the borders the table shows are no longer the asked ones — V10's post-condition fails it."""
    cls = type(get_op("set_table_props"))
    original = cls.plan

    def plan(self, pkg, select, params):
        frag = original(self, pkg, select, params)
        frag.touches = [t for t in frag.touches if not verify_family(t[2])[0]]
        return frag
    monkeypatch.setattr(cls, "plan", plan)
    sel = {"tables": {"index_range": [0, 0]}}
    data = mutate(src, MAIN, lambda r: tbls(r)[0].find(f"{W}tblPr/{W}tblBorders").getparent().replace(
        tbls(r)[0].find(f"{W}tblPr/{W}tblBorders"), etree.fromstring(
            f'<w:tblBorders xmlns:w="{W[1:-1]}"><w:top w:val="nil"/><w:left w:val="nil"/><w:bottom w:val="nil"/>'
            f'<w:right w:val="nil"/><w:insideH w:val="nil"/><w:insideV w:val="nil"/></w:tblBorders>')))
    pl, out, rec = run_ops(data, [("set_table_props", sel, {"borders": "none"}),
                                  ("apply_table_style", sel, {"style": "TableGrid", "direct_formatting": "clear"})])
    assert pl.property_assertions[0]["expected_tables"] == 0
    assert "V10" in failed(rec) and "top border shows" in v10(rec)


# ---- the two readings agree ------------------------------------------------------------------------------------------

SHAPES = ["all", {}, {"index_range": [0, 2]}, {"index_range": [3, 3]}, {"index_range": [5, 40]},
          {"style_in": ["TableGrid"]}, {"style_in": ["TableNormal"]}, {"style_in": ["ArchGrid", "ArchBanded"]},
          {"style_in": ["ArchTable"]}, {"first_row_regex": "^Valve"}, {"first_row_regex": "^Item\tValue$"},
          {"first_row_regex": "^Outer \tcell$"}, {"first_row_regex": "Nested"}, {"text_regex": "Nested"},
          {"text_regex": "in$"}, {"text_regex": "^$"}, {"nested": True}, {"nested": False},
          {"nested": False, "text_regex": "table", "index_range": [0, 6]}, {"text_regex": "175 psi"},
          {"first_row_regex": "SECTION"}]


def _fixtures():
    """The tables fixture, its start/end variant, the batch-4 fixture, and (review round) variants with a right-to-left
    table, cell diagonals and an extra cell margin side, and a table in a text box."""
    base = build_tables()
    return [base, build_tables(start_end=True), build_styles(), mutate(base, MAIN, _rtl),
            mutate(base, MAIN, _diagonal_and_top_margin), mutate(base, MAIN, _boxed)]


def test_transform_and_verifier_select_alike_on_every_table():
    for data in _fixtures():
        pkg, vpkg = DocxPackage(data), VPackage(data)
        sheet, vst = StyleSheet(pkg), Styles(vpkg.xml(STYLES))
        for part in pkg.text_parts():
            root = pkg.xml(part)
            vt = vts.VTables(vpkg.xml(part))
            for pred in SHAPES:
                t = [k for k, _ in tsel.selected_tables(root, pred, sheet)]
                v = [k for k, _ in vt.selected(pred, vst)]
                assert t == v, (part, pred, t, v)
            for tbl, vtbl in zip(root.iter(W + "tbl"), vt.tables):
                assert tsel.table_style_id(tbl, sheet) == vts.v_table_style(vtbl, vst)
                assert [s.get(W + "styleId") for s in tsel.style_chain(tbl, sheet)[0]] == \
                    [s.get(W + "styleId") for s in vts.v_style_chain(vtbl, vst)]


ASKS = [{"borders": "none"}, {"borders": {"top": {}, "bottom": {}, "insideH": {}}},
        {"borders": {"left": {"style": "double", "size": 6, "color": "ff0000", "space": 2}}},
        {"cell_margins": {"left": 115, "right": 115}}, {"cell_margins": {"top": 0}}, {"width": {"pct": 100}},
        {"width": "auto"}, {"width": {"twips": 9360}}, {"repeat_header_rows": 0},
        {"repeat_header_rows": 1, "cant_split": True}, {"cant_split": False},
        {"borders": "none", "cell_margins": {"left": 0}, "clear_cell_overrides": True}]


def test_transform_and_verifier_decide_alike_on_every_table():
    """The expected elements, rows and cells of both sides, for a spread of asks, on every table of every text part of
    the fixtures (refusals compared too: the same tables refuse on both sides)."""
    for data in _fixtures():
        pkg, vpkg = DocxPackage(data), VPackage(data)
        sheet, vst = StyleSheet(pkg), Styles(vpkg.xml(STYLES))
        for params in ASKS:
            asked, vasked = stp_module.clean_params(params), vtp._v_asked(params)
            for part in pkg.text_parts():
                for k, (tbl, vtbl) in enumerate(zip(pkg.xml(part).iter(W + "tbl"), vpkg.xml(part).iter(W + "tbl"))):
                    chain, vchain = tsel.style_chain(tbl, sheet)[0], vts.v_style_chain(vtbl, vst)
                    for every in (True, False):
                        try:
                            t_way = stp_module.disposition(tbl, chain, asked, every, "t") or "plan"
                        except JobException:
                            t_way = "refuse"
                        try:
                            v_way = "skip" if vtp._v_skip_or_refuse(vtbl, vchain, vasked, every, "t") else "plan"
                        except ValueError:
                            v_way = "refuse"
                        assert (t_way if t_way in ("plan", "refuse") else "skip") == v_way, (part, k, params, every)
                    if t_way != "plan":
                        continue
                    t = stp_module.decide_table(tbl.find(W + "tblPr"), chain, asked)
                    v = vtp._v_table_gets(vtbl.find(W + "tblPr"), vchain, vasked)
                    assert {n: sig(e) for n, e in t.items()} == {n: sig(e) for n, e in v.items()}, (part, k, params)
                    t = stp_module.decide_rows(tbl, chain, asked)
                    v = vtp._v_row_gets(vtbl, vchain, vasked)
                    assert [(r, {n: sig(e) for n, e in g.items()}) for r, g in t] == \
                        [(r, {n: sig(e) for n, e in g.items()}) for r, g in v], (part, k, params)
                    assert stp_module.decide_cells(tbl, asked) == vtp._v_cell_gets(vtbl, vasked), (part, k, params)


def test_the_order_tables_are_the_bundled_schemas():
    """The transform's hand-written order tables (ooxml.py) and the verifier's reading of wml.xsd agree."""
    assert tuple(ooxml.TBLPR_ORDER) == vtp.v_order("CT_TblPr")
    assert tuple(ooxml.TRPR_ORDER) == vtp.v_order("CT_TrPr")
    assert tuple(ooxml.TCPR_ORDER) == vtp.v_order("CT_TcPr")
    assert tuple(ooxml.TBL_BORDERS_ORDER) == vtp.v_order("CT_TblBorders")
    assert tuple(ooxml.TBL_CELLMAR_ORDER) == vtp.v_order("CT_TblCellMar")


# ---- conflicts (rule 9) --------------------------------------------------------------------------------------------

def _spec(*ops):
    return {"request": "r", "summary": "s", "mode": "formatting", "ops": list(ops)}


def _conflict(data, *ops):
    for order in (ops, tuple(reversed(ops))):
        with pytest.raises(JobException) as e:
            plan_spec(DocxPackage(data), _spec(*order))
        assert e.value.code == "conflict", order
    return str(e.value)


def _passes(data, *ops):
    for order in (ops, tuple(reversed(ops))):
        plan, frags = plan_spec(DocxPackage(data), _spec(*order))
        out = apply_spec(DocxPackage(data), frags).output_bytes()
        rec = run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")
        assert rec.passed, rec.to_markdown()


T0 = {"tables": {"index_range": [0, 0]}}
BORDERS_T0 = {"op": "set_table_props", "select": T0, "params": {"borders": "none"}}
IN_T0 = {"paragraphs": {"text_regex": "^Riser"}}


def test_a_table_style_change_of_the_same_table_conflicts(src):
    assert "apply_table_style" in _conflict(src, BORDERS_T0, {"op": "apply_table_style", "select": T0,
                                                               "params": {"style": "Arch Grid"}})
    assert "map_styles" in _conflict(src, BORDERS_T0, {"op": "map_styles",
                                                       "params": {"map": [{"from": "TableGrid", "to": "ArchGrid"}]}})


def test_two_table_ops_on_one_table_conflict_on_other_tables_they_compose(src):
    _conflict(src, BORDERS_T0, {"op": "set_table_props", "select": T0, "params": {"width": {"pct": 100}}})
    _conflict(src, {"op": "set_table_props", "select": T0, "params": {"cant_split": True}},
              {"op": "set_table_props", "select": T0, "params": {"repeat_header_rows": 1}})
    _passes(src, BORDERS_T0, {"op": "set_table_props", "select": {"tables": {"index_range": [1, 1]}},
                              "params": {"width": {"pct": 100}}})


def test_paragraph_formatting_inside_the_table_plans_beside_it(src):
    _passes(src, BORDERS_T0, {"op": "set_paragraph_props", "select": IN_T0, "params": {"spacing": {"after": 0}}})
    _passes(src, BORDERS_T0, {"op": "set_properties", "select": IN_T0, "params": {"run": {"b": True}}})


def test_a_selection_by_style_or_text_conflicts_with_what_changes_it(src):
    by_style = {"op": "set_table_props", "select": {"tables": {"style_in": ["ArchGrid"]}}, "params": {"width": "auto"}}
    _conflict(src, by_style, {"op": "apply_table_style", "select": {"tables": {"index_range": [1, 1]}},
                              "params": {"style": "Arch Grid"}})
    by_text = {"op": "set_table_props", "select": {"tables": {"text_regex": "Riser"}}, "params": {"width": "auto"}}
    _conflict(src, by_text, {"op": "replace_text", "params": {"find": "Riser", "replace": "Standpipe"}})


# ---- parameters and refusals ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("params", [
    {}, {"tracked": True}, {"borders": {}}, {"borders": {"start": {}}}, {"borders": {"top": {"weight": 4}}},
    {"borders": {"top": {"size": 1}}}, {"borders": {"top": {"color": "red"}}}, {"cell_margins": {"end": 100}},
    {"cell_margins": {"left": -1}}, {"width": {"pct": 0}}, {"width": {"pct": 50, "twips": 100}}, {"width": "full"},
    {"repeat_header_rows": True}, {"repeat_header_rows": -1}, {"cant_split": "yes"}, {"width": "auto", "colour": 1},
    {"cant_split": True, "clear_cell_overrides": True}, {"borders": "none", "clear_cell_overrides": "yes"},
])
def test_parameters_are_checked(src, params):
    with pytest.raises(JobException) as e:
        get_op("set_table_props").plan(DocxPackage(src), {}, params)
    assert e.value.code == "bad-params"


@pytest.mark.parametrize("select", [
    {"tables": "some"}, {"tables": {"index": 1}}, {"tables": {"index_range": [2, 1]}}, {"tables": {"index_range": [0]}},
    {"tables": {"style_in": []}}, {"tables": {"first_row_regex": "("}}, {"tables": {"nested": "no"}}, {"paragraphs": "all"},
    {"scope": []},
])
def test_selectors_are_checked(src, select):
    with pytest.raises(JobException) as e:
        get_op("set_table_props").plan(DocxPackage(src), select, {"width": "auto"})
    assert e.value.code == "bad-params"


def test_refusals_of_a_table(src):
    aliased = build_tables(start_end=True)
    for params in ({"borders": {"top": {}}}, {"cell_margins": {"top": 0}}):
        with pytest.raises(JobException) as e:
            get_op("set_table_props").plan(DocxPackage(aliased), {"tables": {"index_range": [8, 8]}}, params)
        assert e.value.code == "conflict-property", params
    # the style alone spelling start/end refuses borders too
    no_own = mutate(aliased, MAIN, lambda r: [tbls(r)[8].find(W + "tblPr").remove(x) for x in (
        tbls(r)[8].find(f"{W}tblPr/{W}tblBorders"), tbls(r)[8].find(f"{W}tblPr/{W}tblCellMar"))])
    with pytest.raises(JobException) as e:
        get_op("set_table_props").plan(DocxPackage(no_own), {"tables": {"index_range": [8, 8]}}, {"borders": "none"})
    assert e.value.code == "conflict-property" and "ArchStartEnd" in e.value.reason
    get_op("set_table_props").plan(DocxPackage(no_own), {"tables": {"index_range": [8, 8]}}, {"cell_margins": {"top": 0}})

    def wrap(r):
        t = tbls(r)[7]
        tr = rows(t)[1]
        sdt = etree.fromstring(f'<w:sdt xmlns:w="{W[1:-1]}"><w:sdtPr/><w:sdtContent/></w:sdt>')
        t.replace(tr, sdt)
        sdt.find(W + "sdtContent").append(tr)
    wrapped = mutate(src, MAIN, wrap)
    with pytest.raises(JobException) as e:
        get_op("set_table_props").plan(DocxPackage(wrapped), {"tables": {"index_range": [7, 7]}}, {"cant_split": True})
    assert e.value.code == "table-structure"
    get_op("set_table_props").plan(DocxPackage(wrapped), {"tables": {"index_range": [7, 7]}}, {"width": "auto"})


def test_a_table_in_a_text_box_refuses(src):
    def boxed(r):
        p = r.find(f"{W}body/{W}p")
        t = tbls(r)[7]
        run = etree.SubElement(p, W + "r")
        txbx = etree.fromstring(f'<w:pict xmlns:w="{W[1:-1]}"><w:txbxContent/></w:pict>')
        run.append(txbx)
        t.getparent().remove(t)
        txbx.find(W + "txbxContent").append(t)
        etree.SubElement(txbx.find(W + "txbxContent"), W + "p")
    data = mutate(src, MAIN, boxed)
    with pytest.raises(JobException) as e:
        get_op("set_table_props").plan(DocxPackage(data), {"tables": {"first_row_regex": "^Tag"}}, {"width": "auto"})
    assert e.value.code == "textbox"


def test_every_refusal_code_of_the_table_ops_is_explained():
    codes = set()
    for name in ("set_table_props", "table_select", "apply_table_style"):
        path = Path(stp_module.__file__).with_name(f"{name}.py")
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "JobException" and len(n.args) > 1:
                assert isinstance(n.args[1], ast.Constant), (name, n.lineno)
                codes.add(n.args[1].value)
    assert {"bad-params", "conflict-property", "table-structure", "textbox", "missing-style", "style-mismatch",
            "structure"} == codes
    assert sorted(c for c in codes if c not in FAILURE_EXPLANATIONS) == []


def test_catalog_and_schema(src):
    from spec_surgeon.editspec import SpecError, validate_spec
    cat = op_catalog()
    for op in ("set_table_props", "apply_table_style"):
        assert op in cat["ops"] and COMMON_ASSUMPTIONS[op]
    validate_spec(_spec({"op": "set_table_props", "select": {"tables": {"style_in": ["TableGrid"]}},
                         "params": {"borders": {"top": {"style": "double", "size": 6}}, "cant_split": True}}))
    with pytest.raises(SpecError):
        validate_spec(_spec({"op": "set_table_props", "params": {"borders": {"top": {"weight": 4}}}}))


def test_a_spec_runs_through_the_batch_pipeline(src, tmp_path):
    """Plan, apply, verify, write and deliver in a worker, from a spec (the plan and its transform data go through JSON)."""
    from spec_surgeon.batch import run_batch
    spec = _spec({"op": "set_table_props", "params": EXAMPLE},
                 {"op": "set_table_props", "select": {"scope": ["headers"]}, "params": {"borders": {"bottom": {}}}})
    path = tmp_path / "21 13 13 – SCHEDULES.docx"
    path.write_bytes(src)
    batch = run_batch([str(path)], "edit-spec", {}, tmp_path / "out", workers=1, renderer="none", do_render=False,
                      spec=spec)
    f = batch.files[0]
    assert f.status == "PASS", f.record.to_markdown() if f.record else f.exception


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
def test_libreoffice_opens_and_renders_the_output(src, tmp_path):
    from spec_surgeon.render import libreoffice
    _, out, rec = stp(src, {"scope": ["document", "headers"]}, {**EXAMPLE, "clear_cell_overrides": True})
    assert rec.passed, rec.to_markdown()
    (tmp_path / "out.docx").write_bytes(out)
    pdfs = libreoffice.render_pdfs([tmp_path / "out.docx"], tmp_path / "pdf", workers=1)
    if len(pdfs) != 1:
        pytest.skip("LibreOffice did not render here")
    pdf = pdfs[tmp_path / "out.docx"]
    assert pdf.exists() and pdf.stat().st_size > 1000 and pdf.read_bytes().startswith(b"%PDF")


# ---- the review round (batch 5 review of the table ops): each fix pinned by a test that fails without it ---------------

def frag_xml(xml: str):
    return etree.fromstring(f'<x xmlns:w="{W[1:-1]}">{xml}</x>')[0]


def _wrap_in_sdt(el_):
    sdt = etree.Element(W + "sdt")
    etree.SubElement(sdt, W + "sdtPr")
    content = etree.SubElement(sdt, W + "sdtContent")
    el_.addprevious(sdt)
    content.append(el_)


def _boxed(root):
    """Table 7 moved into a text box (a w:pict holding a w:txbxContent) in the body's first paragraph."""
    p = root.find(f"{W}body/{W}p")
    t = tbls(root)[7]
    run = etree.SubElement(p, W + "r")
    txbx = etree.fromstring(f'<w:pict xmlns:w="{W[1:-1]}"><w:txbxContent/></w:pict>')
    run.append(txbx)
    t.getparent().remove(t)
    txbx.find(W + "txbxContent").append(t)
    etree.SubElement(txbx.find(W + "txbxContent"), W + "p")


def test_B_overrides_in_content_controls_are_flagged(src):
    """The flags count every row and cell of the table — a row or cell wrapped in a content control too."""
    def wrap_row(root):
        tr = rows(tbls(root)[0])[1]                          # its cell (1, 0) has its own tcBorders
        tr.insert(0, frag_xml('<w:tblPrEx><w:tblBorders><w:top w:val="single" w:sz="24" w:space="0" '
                              'w:color="FF0000"/></w:tblBorders></w:tblPrEx>'))
        _wrap_in_sdt(tr)
    plan, out, rec = stp(mutate(src, MAIN, wrap_row), T0, {"borders": {"top": {}, "bottom": {}}})
    assert rec.passed, rec.to_markdown()
    assert any("1 cell(s) keep their own borders" in f for f in plan.flags)
    assert any("1 row(s) carry their own table-property exceptions" in f for f in plan.flags)

    def wrap_cell(root):
        _wrap_in_sdt(cells(rows(tbls(root)[0])[1])[0])
    plan, out, rec = stp(mutate(src, MAIN, wrap_cell), T0, {"borders": {"top": {}, "bottom": {}}})
    assert rec.passed and any("1 cell(s) keep their own borders" in f for f in plan.flags)


def _diagonal_and_top_margin(root):
    t0 = tbls(root)[0]
    cells(rows(t0)[0])[1].find(W + "tcPr").insert(1, frag_xml(
        '<w:tcBorders><w:tl2br w:val="single" w:sz="4" w:space="0" w:color="auto"/></w:tcBorders>'))
    cells(rows(t0)[1])[0].find(f"{W}tcPr/{W}tcBorders").append(frag_xml(
        '<w:tr2bl w:val="single" w:sz="4" w:space="0" w:color="auto"/>'))
    cells(rows(t0)[2])[1].find(f"{W}tcPr/{W}tcMar").insert(0, frag_xml('<w:top w:w="300" w:type="dxa"/>'))


CLEAR_T0 = {"borders": {"top": {}, "bottom": {}}, "cell_margins": {"left": 115}, "clear_cell_overrides": True}


def test_E_clearing_cell_overrides_takes_only_the_competing_sides(src):
    """A diagonal has no table-level counterpart and an unasked margin side does not compete: both stay; the sides that
    compete go, an emptied element with them."""
    data = mutate(src, MAIN, _diagonal_and_top_margin)
    plan, out, rec = stp(data, T0, CLEAR_T0)
    assert rec.passed, rec.to_markdown()
    t0 = tables(out)[0]
    assert [local(c) for c in cells(rows(t0)[0])[1].find(f"{W}tcPr/{W}tcBorders")] == ["tl2br"]   # untouched cell
    assert [local(c) for c in cells(rows(t0)[1])[0].find(f"{W}tcPr/{W}tcBorders")] == ["tr2bl"]   # bottom gone
    assert [local(c) for c in cells(rows(t0)[2])[1].find(f"{W}tcPr/{W}tcMar")] == ["top"]         # left gone
    a = plan.property_assertions[0]
    assert a["changed"][MAIN]["cells"] == [[0, 1, 0, ["tcBorders"]], [0, 2, 1, ["tcMar"]]]
    # the source fixture's lone cell overrides still go whole (their elements empty)
    plan, out, rec = stp(src, T0, CLEAR_T0)
    assert rec.passed and sum(1 for _ in tables(out)[0].iter(W + "tcBorders")) == 0


def test_E_a_shared_clear_that_takes_whole_elements_is_caught(src, monkeypatch):
    real = stp_module.cell_sides
    monkeypatch.setattr(stp_module, "cell_sides", lambda asked: {
        n: list(s) + (["tl2br", "tr2bl"] if n == "tcBorders" else ["top", "bottom"]) for n, s in real(asked).items()})
    plan, out, rec = stp(mutate(src, MAIN, _diagonal_and_top_margin), T0, CLEAR_T0)
    assert sum(1 for _ in tables(out)[0].iter(W + "tl2br")) == 0
    assert "V10" in failed(rec) and "not the plan's" in v10(rec)


def test_E_a_wrong_cell_output_is_caught(src):
    data = mutate(src, MAIN, _diagonal_and_top_margin)
    plan, out, rec = stp(data, T0, CLEAR_T0)
    assert rec.passed

    def drop_top(r):
        mar = cells(rows(tbls(r)[0])[2])[1].find(f"{W}tcPr/{W}tcMar")
        mar.remove(mar.find(W + "top"))
    _caught(data, plan, out, MAIN, drop_top, "w:tcPr is")
    _caught(data, plan, out, MAIN, lambda r: cells(rows(tbls(r)[0])[1])[0].find(f"{W}tcPr/{W}tcBorders").append(
        el("left", val="single")), "keeps its own")


def test_1_header_rows_of_a_floating_or_nested_table_are_flagged(src):
    plan, out, rec = stp(src, {"tables": {"nested": True}}, {"repeat_header_rows": 1})
    assert rec.passed and any("Word repeats header rows only for tables in the text flow" in f for f in plan.flags)
    data = mutate(src, MAIN, lambda r: tbls(r)[7].find(W + "tblPr").insert(1, el("tblpPr", vertAnchor="page")))
    plan, out, rec = stp(data, {"tables": {"index_range": [7, 7]}}, {"repeat_header_rows": 1})
    assert rec.passed and any("floating" in f and "table 7" in f for f in plan.flags)
    plan, out, rec = stp(src, {"tables": {"index_range": [7, 7]}}, {"repeat_header_rows": 1})
    assert not any("text flow" in f for f in plan.flags)


def _rtl(root, k=7):
    tbls(root)[k].find(W + "tblPr").insert(1, el("bidiVisual"))


def test_2_a_right_to_left_table_refuses_borders_and_margins(src):
    data = mutate(src, MAIN, _rtl)
    for params in ({"borders": {"top": {}}}, {"cell_margins": {"left": 100}}):
        with pytest.raises(JobException) as e:
            get_op("set_table_props").plan(DocxPackage(data), {"tables": {"index_range": [7, 7]}}, params)
        assert e.value.code == "conflict-property" and "right to left" in e.value.reason, params
    plan, out, rec = stp(data, {"tables": {"index_range": [7, 7]}}, {"width": {"pct": 100}, "cant_split": True})
    assert rec.passed

    def style_rtl(root):
        st = next(s for s in root.findall(W + "style") if s.get(W + "styleId") == "TableGrid")
        st.find(W + "tblPr").insert(0, el("bidiVisual"))
    with pytest.raises(JobException) as e:                       # a style turning it on counts too
        get_op("set_table_props").plan(DocxPackage(mutate(src, STYLES, style_rtl)), {"tables": {"index_range": [7, 7]}},
                                       {"borders": "none"})
    assert e.value.code == "conflict-property"
    off = mutate(src, MAIN, lambda r: tbls(r)[7].find(W + "tblPr").insert(1, el("bidiVisual", val="0")))
    assert stp(off, {"tables": {"index_range": [7, 7]}}, {"borders": "none"})[2].passed


def test_2_the_verifier_refuses_a_right_to_left_table_too(src, monkeypatch):
    data = mutate(src, MAIN, _rtl)
    monkeypatch.setattr(stp_module, "right_to_left", lambda tbl, chain: False)
    plan, out, rec = stp(data, {"tables": {"index_range": [7, 7]}}, {"borders": {"top": {}}})
    assert "V10" in failed(rec) and "right to left" in v10(rec)


def test_3_conditional_header_rows_of_a_table_style_are_flagged(src):
    def cond(root):
        st = next(s for s in root.findall(W + "style") if s.get(W + "styleId") == "ArchBanded")
        st.find(W + "tblStylePr").append(frag_xml("<w:trPr><w:tblHeader/></w:trPr>"))
    plan, out, rec = stp(mutate(src, STYLES, cond), {"tables": {"style_in": ["ArchBanded"]}}, {"repeat_header_rows": 1})
    assert rec.passed and any("sets header rows or row splitting of its own" in f for f in plan.flags)
    plan, out, rec = stp(src, {"tables": {"style_in": ["ArchBanded"]}}, {"repeat_header_rows": 1})
    assert not any("row splitting of its own" in f for f in plan.flags)


def test_4_a_table_in_a_text_box_is_left_alone_under_every_table(src):
    data = mutate(src, MAIN, _boxed)
    plan, out, rec = stp(data, {}, {"width": {"pct": 100}})
    assert rec.passed, rec.to_markdown()
    # the boxed table now stands first in document order (ordinal 0); the others moved up by one
    assert any("in a text box left alone" in f and "table 0" in f for f in plan.flags)
    assert [k for k, _ in plan.property_assertions[0]["changed"][MAIN]["tables"]] == [1, 3, 4, 5, 6, 7]
    with pytest.raises(JobException) as e:                       # named by a selection: refused
        get_op("set_table_props").plan(DocxPackage(data), {"tables": {"first_row_regex": "^Tag"}}, {"width": "auto"})
    assert e.value.code == "textbox"


def test_4_a_shared_rule_that_changes_the_text_box_table_is_caught(src, monkeypatch):
    data = mutate(src, MAIN, _boxed)
    real = stp_module.disposition
    monkeypatch.setattr(stp_module, "disposition",
                        lambda tbl, chain, asked, every, where: None if tsel.in_text_box(tbl) else
                        real(tbl, chain, asked, every, where))
    plan, out, rec = stp(data, {}, {"width": {"pct": 100}})
    assert "V10" in failed(rec) and "not the plan's" in v10(rec)


def test_5_a_table_too_short_for_the_header_rows_is_left_alone(src):
    plan, out, rec = stp(src, {}, {"repeat_header_rows": 3, "width": {"pct": 100}})
    assert rec.passed, rec.to_markdown()
    a = plan.property_assertions[0]
    changed = [k for k, _ in a["changed"][MAIN]["tables"]]
    assert 0 in changed and 2 not in changed and 7 not in changed       # tables 2, 4, 5, 6, 7 have fewer rows
    assert any("fewer rows than the 3 header row(s) asked" in f and "table 7" in f for f in plan.flags)
    assert tables(out)[7].find(f"{W}tblPr/{W}tblW").get(W + "type") == "auto"     # left alone whole
    plan, out, rec = stp(src, {"tables": {"index_range": [7, 7]}}, {"repeat_header_rows": 3})
    assert rec.passed and out == src


def test_5_a_shared_rule_that_writes_a_short_tables_header_rows_is_caught(src, monkeypatch):
    real = stp_module.disposition

    def no_skip(tbl, chain, asked, every, where):
        why = real(tbl, chain, asked, every, where)
        return None if why == "too few rows" else why
    monkeypatch.setattr(stp_module, "disposition", no_skip)
    plan, out, rec = stp(src, {"tables": {"index_range": [2, 2]}}, {"repeat_header_rows": 3, "width": {"pct": 100}})
    assert "V10" in failed(rec) and "not the plan's" in v10(rec)
