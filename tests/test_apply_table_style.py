"""apply_table_style and V10's ``applied_table_style`` kind (built-in batch 5, item 38 — docs/BUILTIN_OPS_BATCH5_DESIGN.md
§5.3): end to end on the tables fixture (a style of the document by name, one imported from the office template, keep
and replace, own formatting kept or cleared, headers' tables, nested tables left out); a wrong OUTPUT per rule; wrong
IMPLEMENTATIONS shared by plan and apply; the two sides' decisions on every table; plan-time conflicts (rule 9); the
template as an input (rule 12); parameters and refusals; the worked example."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import List, Tuple

import pytest
from lxml import etree

from make_fixtures import build_style_template, build_styles, build_tables
from spec_surgeon.editspec import apply_spec, load_spec, plan_spec, spec_input_files, validate_spec
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import apply_table_style as ats_module
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.verify import run_suite

ROOT = Path(__file__).resolve().parent.parent
MAIN = "word/document.xml"
HEADER = "word/header1.xml"
STYLES = "word/styles.xml"


@pytest.fixture(scope="module")
def src() -> bytes:
    return build_tables()


@pytest.fixture()
def tpl(tmp_path) -> str:
    p = tmp_path / "OFFICE STYLES.docx"
    p.write_bytes(build_style_template())
    return str(p)


def run_ops(data: bytes, specs: List[Tuple[str, dict, dict]]):
    pkg = DocxPackage(data)
    frags = [get_op(o).plan(pkg, sel, par) for o, sel, par in specs]
    plan = merge_fragments(frags)
    work = pkg.copy()
    for f in frags:
        get_op(f.op).apply(work, f)
    out = work.output_bytes()
    return plan, out, run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")


def ats(data: bytes, select: dict, **params):
    return run_ops(data, [("apply_table_style", select, params)])


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


def tbls(root):
    return list(root.iter(W + "tbl"))


def tables(data: bytes, part: str = MAIN):
    return tbls(DocxPackage(data).xml(part))


def rows(tbl):
    return [c for c in tbl if c.tag == W + "tr"]


def cells(tr):
    return [c for c in tr if c.tag == W + "tc"]


def local(e) -> str:
    return etree.QName(e).localname


def kids(e) -> List[str]:
    return [local(c) for c in e] if e is not None else []


def style_of(tbl):
    ts = tbl.find(f"{W}tblPr/{W}tblStyle")
    return ts.get(W + "val") if ts is not None else None


def el(tag, **attrs):
    e = etree.Element(W + tag)
    for k, v in attrs.items():
        e.set(W + k, v)
    return e


def _caught(src, plan, out, part, fn, needle=None):
    bad = mutate(out, part, fn)
    rec = run_suite(src, bad, plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec), rec.to_markdown()
    if needle:
        assert needle in v10(rec), v10(rec)


# ---- end to end ------------------------------------------------------------------------------------------------------

def test_a_document_style_by_name_on_every_table(src):
    """'Table Grid' is resolved by its name; tables already in it are left alone; their own borders, shading and cell
    margins stay (flagged); the new w:tblStyle goes first in the w:tblPr; the nested table is a table too."""
    plan, out, rec = ats(src, {}, style="Table Grid")
    assert rec.passed, rec.to_markdown()
    a = plan.property_assertions[0]
    assert a["kind"] == "applied_table_style" and a["style_id"] == "TableGrid" and a["requested"] == []
    assert [k for k, *_ in a["changed"][MAIN]] == [1, 2, 3, 4, 6] and a["expected_restyled"] == 5
    assert [a["kind"] for a in plan.property_assertions] == ["applied_table_style"]
    t = tables(out)
    assert all(style_of(x) == "TableGrid" for x in t)
    assert kids(t[1].find(W + "tblPr"))[:2] == ["tblStyle", "tblW"]                   # inserted first
    assert kids(t[1].find(W + "tblPr")).count("tblCellMar") == 1                       # own margins kept (keep)
    assert any("keep their own borders, shading or cell margins" in f for f in plan.flags)
    assert any("already as asked, left alone: 3 table(s)" in n for n in plan.notes)
    assert plan.geometry_reference == "none" and plan.mode == "formatting"
    assert DocxPackage(out).read(HEADER) == DocxPackage(src).read(HEADER)


def test_a_template_style_is_imported_and_clear_lets_it_show(src, tpl):
    plan, out, rec = ats(src, {}, style="TblMine", template=tpl, direct_formatting="clear")
    assert rec.passed, rec.to_markdown()
    assert [a["kind"] for a in plan.property_assertions] == ["applied_table_style", "styles_imported"]
    imp = plan.property_assertions[1]
    assert imp["requested"] == ["TblMine"] and imp["imported"] == ["TblMine"]
    t = tables(out)
    assert all(style_of(x) == "TblMine" for x in t)
    t0 = t[0]
    assert kids(t0.find(W + "tblPr")) == ["tblStyle", "tblW", "tblLook"]               # borders, shading gone; look kept
    assert kids(cells(rows(t0)[0])[0].find(W + "tcPr")) == ["tcW"]                    # cell shading gone
    assert kids(cells(rows(t0)[1])[0].find(W + "tcPr")) == ["tcW"]                    # cell borders gone
    assert kids(cells(rows(t0)[2])[1].find(W + "tcPr")) == ["tcW"]                    # cell margins gone
    assert kids(t[1].find(W + "tblPr")) == ["tblStyle", "tblW", "tblLook"]             # own margins gone
    a = plan.property_assertions[0]
    assert a["changed"][MAIN][0] == [0, True, ["shd", "tblBorders"], [[0, 0, ["shd"]], [1, 0, ["tcBorders"]],
                                                                       [2, 1, ["tcMar"]]], []]
    assert a["expected_cells"] == 3
    assert any(n.get(W + "styleId") == "TblMine" for n in DocxPackage(out).xml(STYLES).findall(W + "style"))


def test_existing_replace_takes_the_templates_definition_and_keep_the_documents(src, tpl):
    plan, out, rec = ats(src, {"tables": {"index_range": [1, 1]}}, style="TableGrid", template=tpl,
                         existing="replace")
    assert rec.passed, rec.to_markdown()
    assert plan.property_assertions[1]["replaced"] == ["TableGrid"]        # table 1 is restyled: the definition too
    assert plan.property_assertions[0]["expected_restyled"] == 1
    # keep (the default): the file's own Table Grid, nothing imported — and the template still read (rule 12)
    plan, out, rec = ats(src, {"tables": {"index_range": [1, 1]}}, style="Table Grid", template=tpl)
    assert rec.passed and [a["kind"] for a in plan.property_assertions] == ["applied_table_style"]
    assert plan.property_assertions[0]["template_md5"]


def test_clear_alone_on_a_table_already_in_the_style(src):
    plan, out, rec = ats(src, {"tables": {"index_range": [0, 0]}}, style="TableGrid", direct_formatting="clear")
    assert rec.passed, rec.to_markdown()
    a = plan.property_assertions[0]
    assert a["changed"][MAIN] == [[0, False, ["shd", "tblBorders"], [[0, 0, ["shd"]], [1, 0, ["tcBorders"]],
                                                                    [2, 1, ["tcMar"]]], []]]
    assert a["expected_restyled"] == 0 and style_of(tables(out)[0]) == "TableGrid"


def test_the_default_table_style_and_the_headers_table(src):
    plan, out, rec = ats(src, {"scope": ["headers"]}, style="Normal Table")
    assert rec.passed and out == src                               # the header's table has no style: the default
    plan, out, rec = ats(src, {"scope": ["document", "headers"], "tables": {"nested": False}}, style="Arch Grid")
    assert rec.passed, rec.to_markdown()
    assert style_of(tables(out, HEADER)[0]) == "ArchGrid" and style_of(tables(out)[5]) == "TableGrid"


def test_no_matching_table_is_a_noted_no_op(src):
    plan, out, rec = ats(src, {"tables": {"first_row_regex": "^Nothing"}}, style="TableGrid")
    assert rec.passed and out == src and any("no table in this file matches" in n for n in plan.notes)


def test_touches_declare_changes_and_reads(src, tpl):
    t = set(map(tuple, get_op("apply_table_style").plan(DocxPackage(src), {}, {"style": "ArchGrid",
                                                                                 "direct_formatting": "clear"}).touches))
    assert {(MAIN, "tbl:0", "tblPr"), (MAIN, "tbl:0", "tblProps"), (MAIN, "tbl:0", "tblBorders"), (MAIN, "tbl:0", "shd"),
            (MAIN, "tc:0:1:0", "tcPr"), (MAIN, "tc:0:1:0", "tcBorders"), (MAIN, "tbl:2", "verify-props:tblPr"),
            (STYLES, "style:ArchGrid", "verify"), (STYLES, "style:ArchGrid", "uses+"), (STYLES, "style:TableGrid", "uses-"),
            (STYLES, "style:TableNormal", "uses-"), (MAIN, "scope", "verify")} <= t
    assert (MAIN, "tbl:2", "tblPr") not in t                      # table 2 already is ArchGrid
    t = set(map(tuple, get_op("apply_table_style").plan(DocxPackage(src), {}, {"style": "TblMine",
                                                                                 "template": tpl}).touches))
    assert (STYLES, "style:TblMine", "add") in t and (STYLES, "style:TblMine", "verify") not in t


# ---- a wrong output per rule ---------------------------------------------------------------------------------------

def test_wrong_outputs_are_caught(src):
    plan, out, rec = ats(src, {}, style="ArchGrid", direct_formatting="clear")
    assert rec.passed
    _caught(src, plan, out, MAIN, lambda r: tbls(r)[1].find(f"{W}tblPr/{W}tblStyle").set(W + "val", "TableGrid"),
            "w:tblPr is not")

    def style_last(r):
        pr = tbls(r)[1].find(W + "tblPr")
        ts = pr.find(W + "tblStyle")
        pr.remove(ts)
        pr.append(ts)
    _caught(src, plan, out, MAIN, style_last, "w:tblPr is not")
    _caught(src, plan, out, MAIN, lambda r: tbls(r)[0].find(W + "tblPr").insert(
        3, el("shd", val="clear", color="auto", fill="EEEEEE")), "w:tblPr is not")
    _caught(src, plan, out, MAIN, lambda r: cells(rows(tbls(r)[0])[0])[0].find(W + "tcPr").append(
        el("shd", val="clear", color="auto", fill="D9D9D9")), "w:tcPr is not")
    _caught(src, plan, out, MAIN, lambda r: cells(rows(tbls(r)[3])[0])[0].find(W + "tcPr").append(el("noWrap")),
            "unpredicted change")
    _caught(src, plan, out, MAIN, lambda r: tbls(r)[2].find(W + "tblPr").insert(1, el("jc", val="center")),
            "unpredicted change")


def test_an_undefined_style_in_the_output_is_caught(src, tpl):
    plan, out, rec = ats(src, {}, style="TblMine", template=tpl)
    assert rec.passed

    def drop(r):
        r.remove(next(s for s in r.findall(W + "style") if s.get(W + "styleId") == "TblMine"))
    bad = mutate(out, STYLES, drop)
    rec2 = run_suite(src, bad, plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec2) and "not defined in the output" in v10(rec2)


# ---- wrong implementations shared by plan and apply ----------------------------------------------------------------

def test_a_shared_tblstyle_written_last_is_caught(src, monkeypatch):
    def last(tblpr, sid):
        ts = tblpr.find(W + "tblStyle")
        if ts is None:
            ts = etree.SubElement(tblpr, W + "tblStyle")
        ts.set(W + "val", sid)
    monkeypatch.setattr(ats_module, "set_table_style", last)
    plan, out, rec = ats(src, {"tables": {"index_range": [1, 1]}}, style="TableGrid")
    assert kids(tables(out)[1].find(W + "tblPr"))[-1] == "tblStyle"
    assert "V10" in failed(rec) and "w:tblPr is not" in v10(rec)


def test_a_shared_clear_that_forgets_the_cells_shading_is_caught(src, monkeypatch):
    monkeypatch.setattr(ats_module, "CLEARED_CELL", ("tcBorders", "tcMar"))
    plan, out, rec = ats(src, {"tables": {"index_range": [0, 0]}}, style="ArchGrid", direct_formatting="clear")
    assert "shd" in kids(cells(rows(tables(out)[0])[0])[0].find(W + "tcPr"))
    assert "V10" in failed(rec) and "not the plan's" in v10(rec) and "plan predicted 2" in v10(rec)


def test_a_shared_style_rule_that_forgets_the_default_table_style_is_caught(src, monkeypatch):
    def explicit_only(tbl, sheet):
        ts = tbl.find(f"{W}tblPr/{W}tblStyle")
        return ts.get(W + "val") if ts is not None else None
    monkeypatch.setattr(ats_module, "table_style_id", explicit_only)
    plan, out, rec = ats(src, {}, style="TableNormal")
    assert style_of(tables(out)[1]) == "TableNormal"                    # an unstyled table "restyled" to its own style
    assert "V10" in failed(rec) and "not the plan's" in v10(rec)


def test_an_import_left_out_of_the_assertions_is_caught(src, tpl):
    plan, out, rec = ats(src, {}, style="TblMine", template=tpl)
    assert rec.passed
    d = plan.to_dict()
    d["property_assertions"] = [a for a in d["property_assertions"] if a["kind"] != "styles_imported"]
    rec2 = run_suite(src, out, d, "src.docx", "out.docx")
    assert not rec2.passed and "styles_imported" in v10(rec2)


def test_a_shared_default_scope_is_caught(src, monkeypatch):
    real = ats_module.scope_parts
    monkeypatch.setattr(ats_module, "scope_parts",
                        lambda pkg, scope: real(pkg, ["document", "headers"] if scope == ["document"] else scope))
    plan, out, rec = ats(src, {}, style="TableGrid")
    assert "V10" in failed(rec) and "scope resolves to" in v10(rec)


# ---- the two readings agree ------------------------------------------------------------------------------------------

def test_transform_and_verifier_decide_alike_on_every_table(tpl):
    """Restyled or not, the cleared table elements and cells, for every table of every text part of the fixtures."""
    from spec_surgeon.ops.selectors import StyleSheet
    from spec_surgeon.ops.table_select import table_style_id
    from spec_surgeon.verify.props import Styles
    from spec_surgeon.verify.reader import VPackage
    from spec_surgeon.verify.tableselect import v_cells, v_rows, v_table_style
    for data in (build_tables(), build_tables(start_end=True), build_styles()):
        pkg, vpkg = DocxPackage(data), VPackage(data)
        sheet, vst = StyleSheet(pkg), Styles(vpkg.xml(STYLES))
        for part in pkg.text_parts():
            for tbl, vtbl in zip(pkg.xml(part).iter(W + "tbl"), vpkg.xml(part).iter(W + "tbl")):
                for sid in ("TableGrid", "TableNormal", "ArchGrid", "ArchTable"):
                    assert (table_style_id(tbl, sheet) != sid) == (v_table_style(vtbl, vst) != sid)
                names, cs, rs = ats_module.table_clearing(tbl)
                vpr = vtbl.find(W + "tblPr")
                assert names == [n for n in ("tblBorders", "shd", "tblCellMar") if vpr.find(W + n) is not None]
                vcs = [(r, c, [n for n in ("tcBorders", "shd", "tcMar") if tc.find(f"{W}tcPr/{W}{n}") is not None])
                       for r, tr in enumerate(v_rows(vtbl)) for c, tc in enumerate(v_cells(tr))]
                assert cs == [x for x in vcs if x[2]]
                vrs = [(r, [n for n in ("tblBorders", "shd", "tblCellMar") if tr.find(f"{W}tblPrEx/{W}{n}") is not None])
                       for r, tr in enumerate(v_rows(vtbl))]
                assert rs == [x for x in vrs if x[1]]


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


T1 = {"tables": {"index_range": [1, 1]}}
GRID_T1 = {"op": "apply_table_style", "select": T1, "params": {"style": "TableGrid"}}
IN_T1 = {"paragraphs": {"text_regex": "^Gate"}}


@pytest.mark.parametrize("other", [
    {"op": "set_properties", "select": IN_T1, "params": {"run": {"b": True}}},
    {"op": "set_paragraph_props", "select": IN_T1, "params": {"spacing": {"after": 0}}},
    {"op": "set_borders_shading", "select": IN_T1, "params": {"shading": {"fill": "D9D9D9"}}},
    {"op": "set_tab_stops", "select": IN_T1, "params": {"set": [{"pos": 720}]}},
    {"op": "map_styles", "params": {"map": [{"from": "TableGrid", "to": "ArchGrid"}]}},
    {"op": "apply_table_style", "select": T1, "params": {"style": "ArchGrid"}},
    {"op": "set_table_props", "select": T1, "params": {"width": "auto"}},
])
def test_formatting_that_reads_the_tables_style_conflicts(src, other):
    _conflict(src, GRID_T1, other)


def test_other_tables_and_other_aspects_compose(src):
    _passes(src, GRID_T1, {"op": "set_properties", "select": {"paragraphs": {"text_regex": "^Riser"}},
                           "params": {"run": {"b": True}}})
    _passes(src, GRID_T1, {"op": "set_table_props", "select": {"tables": {"index_range": [2, 2]}},
                           "params": {"borders": "none"}})
    # two ops giving tables one style commute
    _passes(src, GRID_T1, {"op": "apply_table_style", "select": {"tables": {"index_range": [2, 2]}},
                           "params": {"style": "TableGrid"}})


def test_removing_unused_styles_beside_a_restyle_conflicts(src):
    _conflict(src, GRID_T1, {"op": "remove_unused_styles", "params": {}})


# ---- the template is an input (rule 12) ------------------------------------------------------------------------------

def test_a_template_changed_after_planning_is_refused_and_fails_v10(src, tpl):
    pkg = DocxPackage(src)
    frag = get_op("apply_table_style").plan(pkg, {}, {"style": "TblMine", "template": tpl})
    plan = merge_fragments([frag])
    work = pkg.copy()
    get_op("apply_table_style").apply(work, frag)
    out = work.output_bytes()

    def touch(root):
        extra = etree.SubElement(root, W + "style")
        extra.set(W + "type", "paragraph")
        extra.set(W + "styleId", "AddedLater")
    Path(tpl).write_bytes(mutate(build_style_template(), STYLES, touch))
    with pytest.raises(JobException):
        get_op("apply_table_style").apply(pkg.copy(), frag)
    rec = run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")
    assert not rec.passed and "template" in v10(rec)


def test_a_template_read_without_an_import_is_still_pinned(src, tpl):
    original = Path(tpl).read_bytes()
    frag = get_op("apply_table_style").plan(DocxPackage(src), T1, {"style": "TableGrid", "template": tpl})
    Path(tpl).write_bytes(original)   # Rebuilding the ZIP can change its timestamps.
    get_op("apply_table_style").apply(DocxPackage(src).copy(), frag)
    Path(tpl).write_bytes(mutate(original, STYLES, lambda r: r.append(el("latentStyles"))))
    with pytest.raises(JobException) as e:
        get_op("apply_table_style").apply(DocxPackage(src).copy(), frag)
    assert e.value.code == "internal"


# ---- parameters and refusals -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("params, code", [
    ({}, "bad-params"),
    ({"style": ""}, "bad-params"),
    ({"style": "TableGrid", "direct_formatting": "wipe"}, "bad-params"),
    ({"style": "TableGrid", "existing": "replace"}, "bad-params"),
    ({"style": "TableGrid", "colour": "red"}, "bad-params"),
    ({"style": "TableGrid", "tracked": True}, "bad-params"),
    ({"style": "NoSuchStyle"}, "missing-style"),
    ({"style": "Normal"}, "style-mismatch"),
    ({"style": "Default Paragraph Font"}, "style-mismatch"),
])
def test_parameters_and_refusals(src, params, code):
    with pytest.raises(JobException) as e:
        get_op("apply_table_style").plan(DocxPackage(src), {}, params)
    assert e.value.code == code


def test_template_refusals(src, tpl):
    for params in ({"style": "NotInTemplate", "template": tpl}, {"style": "SpecNote", "template": tpl},
                   {"style": "CharMine", "template": tpl}):
        with pytest.raises(JobException) as e:
            get_op("apply_table_style").plan(DocxPackage(src), {}, params)
        assert e.value.code == "bad-params", params


def test_cells_in_a_content_control_refuse_clear(src):
    def wrap(r):
        tr = rows(tbls(r)[0])[1]
        tc = cells(tr)[0]
        sdt = etree.fromstring(f'<w:sdt xmlns:w="{W[1:-1]}"><w:sdtPr/><w:sdtContent/></w:sdt>')
        tr.replace(tc, sdt)
        sdt.find(W + "sdtContent").append(tc)
    data = mutate(src, MAIN, wrap)
    with pytest.raises(JobException) as e:
        get_op("apply_table_style").plan(DocxPackage(data), {}, {"style": "ArchGrid", "direct_formatting": "clear"})
    assert e.value.code == "table-structure"
    get_op("apply_table_style").plan(DocxPackage(data), {}, {"style": "ArchGrid"})        # keep: nothing counted


# ---- the worked examples ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["apply-table-style", "table-borders-header-rows"])
def test_the_worked_examples_plan_and_verify_on_the_tables_fixture(src, name):
    spec = load_spec(ROOT / "examples" / "specs" / f"{name}.json")
    validate_spec(spec)
    plan, frags = plan_spec(DocxPackage(src), spec)
    out = apply_spec(DocxPackage(src), frags).output_bytes()
    rec = run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")
    assert rec.passed, rec.to_markdown()
    assert plan.property_assertions


def test_the_table_style_example_reads_the_office_template_as_an_input():
    spec = load_spec(ROOT / "examples" / "specs" / "apply-table-style.json")
    tpl = (ROOT / "examples" / "templates" / "OFFICE STYLES.docx").resolve()
    assert Path(spec["ops"][0]["params"]["template"]) == tpl and tpl in spec_input_files(spec)


# ---- the review round (batch 5 review of the table ops): each fix pinned by a test that fails without it ---------------

T3 = {"tables": {"index_range": [3, 3]}}
T7 = {"tables": {"index_range": [7, 7]}}


def test_A_clear_takes_the_rows_own_exceptions_too(src):
    """Table 3's row 1 carries a w:tblPrEx with its own borders, which win over the style for that row: clear removes
    them (the emptied w:tblPrEx goes) and says so; keep flags them."""
    plan, out, rec = ats(src, T3, style="Table Grid", direct_formatting="clear")
    assert rec.passed, rec.to_markdown()
    assert rows(tables(out)[3])[1].find(W + "tblPrEx") is None
    a = plan.property_assertions[0]
    assert a["changed"][MAIN] == [[3, True, [], [], [[1, ["tblBorders"]]]]] and a["expected_rows"] == 1
    assert any("1 row(s)" in n for n in plan.notes)
    t = set(map(tuple, get_op("apply_table_style").plan(DocxPackage(src), T3, {
        "style": "Table Grid", "direct_formatting": "clear"}).touches))
    assert (MAIN, "tr:3:1", "tblPrEx") in t
    plan, out, rec = ats(src, T3, style="Table Grid")
    assert rec.passed and any("keep their own borders, shading or cell margins (or their rows" in f for f in plan.flags)


def test_A_a_row_exception_left_in_place_is_caught(src):
    plan, out, rec = ats(src, T3, style="Table Grid", direct_formatting="clear")
    assert rec.passed
    _caught(src, plan, out, MAIN, lambda r: rows(tbls(r)[3])[1].insert(0, etree.fromstring(
        f'<w:tblPrEx xmlns:w="{W[1:-1]}"><w:shd w:val="clear" w:color="auto" w:fill="FF0000"/></w:tblPrEx>')),
        "w:tblPrEx")


def test_A_a_shared_clear_that_forgets_the_rows_is_caught(src, monkeypatch):
    monkeypatch.setattr(ats_module, "CLEARED_ROW", ())
    plan, out, rec = ats(src, T3, style="Table Grid", direct_formatting="clear")
    assert rows(tables(out)[3])[1].find(W + "tblPrEx") is not None
    assert "V10" in failed(rec) and "keeps" in v10(rec) and "not the plan's" in v10(rec)


def test_C_nothing_restyled_imports_nothing(src, tpl):
    """A selection that restyles no table changes no style definition: no import, the note true, styles.xml as it was —
    also with existing 'replace', which used to replace Table Grid for three tables nobody selected."""
    from docx import Document
    from make_fixtures import docx_bytes
    doc = Document()
    doc.add_paragraph("No tables here.")
    bare = docx_bytes(doc)
    plan, out, rec = ats(bare, {}, style="TblMine", template=tpl)
    assert rec.passed and out == bare
    assert [a["kind"] for a in plan.property_assertions] == ["applied_table_style"]
    assert any("nothing to change" in n for n in plan.notes) and any("is not imported" in n for n in plan.notes)
    plan, out, rec = ats(src, {"tables": {"text_regex": "NO SUCH TEXT"}}, style="TableGrid", template=tpl,
                         existing="replace")
    assert rec.passed and out == src and any("taken over" in n for n in plan.notes)
    plan, out, rec = ats(src, T7, style="TableGrid", template=tpl, existing="replace")       # already in the style
    assert rec.passed and out == src and plan.property_assertions[0]["requested"] == []


def test_C_an_import_nothing_needs_is_caught(src, tpl):
    """The old behaviour planted: the plan imports the template's style although no selected table is restyled —
    the kind derives that nothing needs an import and fails it (check_requested)."""
    from spec_surgeon.ops.style_import import TemplateStyles, plan_import
    pkg = DocxPackage(src)
    frag = get_op("apply_table_style").plan(pkg, T7, {"style": "TableGrid", "template": tpl, "existing": "replace"})
    imp = plan_import(pkg, TemplateStyles.load(tpl), ["TableGrid"], "replace")
    frag.transform_data["import"] = imp.transform_data()
    frag.property_assertions.append(imp.assertion())
    frag.intended_parts += imp.intended_parts()
    plan = merge_fragments([frag])
    work = pkg.copy()
    get_op("apply_table_style").apply(work, frag)
    rec = run_suite(src, work.output_bytes(), plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec) and "nothing needs one here" in v10(rec)


CLEAR_T7 = {"op": "apply_table_style", "select": T7, "params": {"style": "TableGrid", "direct_formatting": "clear"}}
OWN_T7 = {"op": "set_table_props", "select": T7,
          "params": {"borders": {"top": {"size": 24, "color": "FF0000"}}, "cell_margins": {"left": 400}}}


def test_D_clear_beside_new_own_formatting_on_a_table_already_in_the_style_conflicts(src):
    """Table 7 is in Table Grid with nothing of its own: clear finds it already as asked. Another op giving it its own
    borders and margins in the same spec would leave the style hidden — a plan-time conflict in both orders."""
    _conflict(src, CLEAR_T7, OWN_T7)
    t = set(map(tuple, get_op("apply_table_style").plan(DocxPackage(src), T7, CLEAR_T7["params"]).touches))
    assert {(MAIN, "tbl:7", "verify-props:tblBorders"), (MAIN, "tbl:7", "verify-props:shd"),
            (MAIN, "tbl:7", "verify-props:tblCellMar"), (MAIN, "tr:7:0", "verify-props:tblPrEx"),
            (MAIN, "tc:7:1:1", "verify-props:tcMar")} <= t
    # keep reads none of them: the same pair plans
    keep = {"op": "apply_table_style", "select": T7, "params": {"style": "TableGrid"}}
    _passes(src, keep, {"op": "set_table_props", "select": T7, "params": {"cell_margins": {"left": 400}}})


def test_D_without_the_reads_the_post_condition_still_catches_it(src, monkeypatch):
    from spec_surgeon.ops.base import verify_family
    cls = type(get_op("apply_table_style"))
    original = cls.plan

    def plan(self, pkg, select, params):
        frag = original(self, pkg, select, params)
        frag.touches = [t for t in frag.touches if not verify_family(t[2])[0]]
        return frag
    monkeypatch.setattr(cls, "plan", plan)
    for ops in ((CLEAR_T7, OWN_T7), (OWN_T7, CLEAR_T7)):
        pl, frags = plan_spec(DocxPackage(src), _spec(*ops))
        out = apply_spec(DocxPackage(src), frags).output_bytes()
        rec = run_suite(src, out, pl.to_dict(), "src.docx", "out.docx")
        assert "V10" in failed(rec) and "keeps its own" in v10(rec), ops


def test_D_a_wrong_output_on_an_already_table_is_caught(src):
    plan, out, rec = ats(src, T7, style="TableGrid", direct_formatting="clear")
    assert rec.passed and out == src
    _caught(src, plan, out, MAIN, lambda r: cells(rows(tbls(r)[7])[0])[0].find(W + "tcPr").append(
        el("shd", val="clear", color="auto", fill="FFFF00")), "keeps its own")
    _caught(src, plan, out, MAIN, lambda r: tbls(r)[7].find(f"{W}tblPr/{W}tblStyle").set(W + "val", "ArchGrid"),
            "style is 'ArchGrid'")


def _boxed(root):
    p = root.find(f"{W}body/{W}p")
    t = tbls(root)[7]
    run = etree.SubElement(p, W + "r")
    txbx = etree.fromstring(f'<w:pict xmlns:w="{W[1:-1]}"><w:txbxContent/></w:pict>')
    run.append(txbx)
    t.getparent().remove(t)
    txbx.find(W + "txbxContent").append(t)
    etree.SubElement(txbx.find(W + "txbxContent"), W + "p")


def test_4_a_table_in_a_text_box_is_left_alone_under_every_table(src):
    data = mutate(src, MAIN, _boxed)                              # the boxed table becomes ordinal 0
    plan, out, rec = ats(data, {}, style="ArchGrid")
    assert rec.passed, rec.to_markdown()
    assert any("in a text box left alone" in f and "table 0" in f for f in plan.flags)
    assert style_of(tables(out)[0]) == "TableGrid"
    with pytest.raises(JobException) as e:
        get_op("apply_table_style").plan(DocxPackage(data), {"tables": {"first_row_regex": "^Tag"}}, {"style": "ArchGrid"})
    assert e.value.code == "textbox"
