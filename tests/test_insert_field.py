"""insert_field and V10's ``inserted_fields`` kind (built-in batch 6, item F8 — docs/BUILTIN_OPS_BATCH6_DESIGN.md §9):
every field type at the user's placeholder and at the start or end of paragraphs, tracked and untracked, with an empty
result and the dirty mark where Word needs it; the edit core's placements (over runs, in a link, in someone's pending
insertion); a wrong OUTPUT per rule; wrong IMPLEMENTATIONS shared by plan and apply; refusals with their codes;
parameters; plan-time conflicts; the catalog; the batch pipeline and a LibreOffice render of the page numbers."""
from __future__ import annotations

import ast
import io
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import List, Tuple

import pytest
from lxml import etree

from fx_fields import CUSTOM, DOC, FOOTER, FOOTNOTES, HEADER, build_fields
from spec_surgeon.catalog import COMMON_ASSUMPTIONS, op_catalog
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import insert_field as ifm
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.report import FAILURE_EXPLANATIONS
from spec_surgeon.verify import run_suite

SPACE = "{http://www.w3.org/XML/1998/namespace}space"
BODY = {"scope": ["document"]}


@pytest.fixture(scope="module")
def src() -> bytes:
    return build_fields()


def run_ops(data: bytes, specs: List[Tuple[str, dict, dict]]):
    pkg = DocxPackage(data)
    frags = [get_op(o).plan(pkg, sel, par) for o, sel, par in specs]
    plan = merge_fragments(frags)
    work = pkg.copy()
    for f in frags:
        get_op(f.op).apply(work, f)
    out = work.output_bytes()
    return plan, out, run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")


def field(data: bytes, select: dict, params: dict):
    return run_ops(data, [("insert_field", select, {"author": "Abe", **params})])


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


def xml(data: bytes, part: str):
    return etree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read(part))


def body(root) -> list:
    return [p for p in root.find(W + "body") if p.tag == W + "p"]


def shown(p) -> str:
    """What a reader sees of the paragraph's words once changes are accepted: its live w:t text."""
    out = []
    for t in p.iter(W + "t"):
        if not any(a.tag == W + "del" for a in t.iterancestors()):
            out.append(t.text or "")
    return "".join(out)


def fields_in(p) -> List[dict]:
    """Each complex field of the paragraph: its begin's attributes, its instruction, what it shows."""
    out, cur, state = [], None, None
    for el in p.iter(W + "fldChar", W + "instrText", W + "t"):
        if el.tag == W + "fldChar":
            kind = el.get(W + "fldCharType")
            if kind == "begin":
                cur = {"begin": {etree.QName(k).localname: v for k, v in el.attrib.items()}, "code": "", "shows": ""}
                state = "code"
            elif kind == "separate":
                state = "result"
            elif kind == "end" and cur is not None:
                out.append(cur)
                cur = state = None
        elif cur is not None and el.tag == W + "instrText" and state == "code":
            cur["code"] += el.text or ""
        elif cur is not None and el.tag == W + "t" and state == "result":
            cur["shows"] += el.text or ""
    return out


def _caught(src, plan, out, part, fn, needle=None, check="V10"):
    bad = mutate(out, part, fn)
    rec = run_suite(src, bad, plan.to_dict(), "src.docx", "out.docx")
    assert check in failed(rec), rec.to_markdown()
    if needle:
        assert needle in v10(rec), v10(rec)


# ---- end to end ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("tracked", [True, False])
def test_a_date_at_every_placeholder_of_the_body(src, tracked):
    """One run, two runs with a proofing mark between, someone's pending insertion: the placeholder goes, an empty DATE
    field with its picture and the dirty mark takes its place, the words around it stay."""
    plan, out, rec = field(src, BODY, {"field": "DATE", "at_text": "{{DATE}}", "format": "MMMM d, yyyy",
                                       "tracked": tracked})
    assert rec.passed, rec.to_markdown()
    pars = body(xml(out, DOC))
    for i, words in ((1, "Issued  for review."), (2, "Printed  by the office."), (4, "Rev  draft")):
        assert shown(pars[i]) == words
        assert fields_in(pars[i]) == [{"begin": {"fldCharType": "begin", "dirty": "true"},
                                       "code": ' DATE \\@ "MMMM d, yyyy" ', "shows": ""}]
    assert plan.property_assertions[0]["expected"] == {DOC: {"1": 1, "2": 1, "4": 1}}
    if tracked:
        assert plan.rev_delta == {"ins": 4, "del": 4}            # 1 + 2 + 1 runs deleted; three fields, one split
        assert [d.accepted_view for d in plan.diffs][0] == 'Issued  DATE \\@ "MMMM d, yyyy"  for review.'
    else:
        assert plan.rev_delta == {"ins": 0, "del": 0} and plan.mode == "untracked"


@pytest.mark.parametrize("name, params, part, block, code", [
    ("PAGE", {}, FOOTER, 0, " PAGE "),
    ("NUMPAGES", {"format": "roman"}, FOOTER, 0, " NUMPAGES \\* roman "),
    ("SECTIONPAGES", {"format": "ALPHABETIC"}, FOOTER, 0, " SECTIONPAGES \\* ALPHABETIC "),
    ("SECTION", {"format": "arabic"}, DOC, 6, " SECTION \\* Arabic "),
    ("SAVEDATE", {"format": "M/d/yyyy h:mm am/pm"}, DOC, 6, ' SAVEDATE \\@ "M/d/yyyy h:mm am/pm" '),
    ("FILENAME", {"path": True}, DOC, 6, " FILENAME \\p "),
    ("STYLEREF", {"style": "Heading1", "last_on_page": True, "paragraph_number": True}, HEADER, 0,
     ' STYLEREF "heading 1" \\l \\n '),
    ("STYLEREF", {"style": "HEADING 1"}, HEADER, 0, ' STYLEREF "heading 1" '),
    ("DOCPROPERTY", {"property": "title"}, DOC, 6, ' DOCPROPERTY "Title" '),
    ("DOCPROPERTY", {"property": "project NUMBER"}, DOC, 6, ' DOCPROPERTY "Project Number" '),
])
@pytest.mark.parametrize("tracked", [True, False])
def test_every_field_at_the_end_of_a_paragraph(src, name, params, part, block, code, tracked):
    """The instruction as Word writes it (the style's own name, Word's spelling of a built-in property, the custom
    property's stored name); dirty unless Word computes the field at layout; an empty result."""
    scope = {DOC: "document", FOOTER: "footers", HEADER: "headers"}[part]
    select = {"scope": [scope], "paragraphs": {"index_range": [block, block + 1]}}
    plan, out, rec = field(src, select, {"field": name, "position": "end", "tracked": tracked, **params})
    assert rec.passed, rec.to_markdown()
    p = list(xml(out, part).iter(W + "p"))[block]
    assert shown(p) == shown(list(xml(src, part).iter(W + "p"))[block])        # zero characters added
    dirty = {} if name in ("PAGE", "NUMPAGES", "SECTIONPAGES") else {"dirty": "true"}
    assert fields_in(p)[-1] == {"begin": {"fldCharType": "begin", **dirty}, "code": code, "shows": ""}
    runs = [c for c in p if c.tag == W + "r"] if not tracked else list(p.findall(W + "ins")[-1])
    assert [etree.QName(c[-1]).localname for c in runs[-4:]] == ["fldChar", "instrText", "fldChar", "fldChar"]
    assert runs[-3][-1].get(SPACE) == "preserve"


def test_placeholders_in_the_footer_and_a_footnote(src):
    plan, out, rec = field(src, {"scope": ["footers"]}, {"field": "PAGE", "at_text": "{{PAGE}}"})
    assert rec.passed, rec.to_markdown()
    p = xml(out, FOOTER).find(W + "p")
    assert shown(p) == "Page  of {{NUMPAGES}}" and fields_in(p)[0]["code"] == " PAGE "
    for r in p.iter(W + "r"):                                   # the placeholder's small capitals on every run
        assert r.find(f"{W}rPr/{W}smallCaps") is not None
    with_note = build_fields(footnote=True)
    plan, out, rec = field(with_note, {"scope": ["footnotes"]}, {"field": "DATE", "at_text": "{{DATE}}",
                                                                 "tracked": False})
    assert rec.passed, rec.to_markdown()
    assert plan.intended_parts == [FOOTNOTES]
    assert fields_in(list(xml(out, FOOTNOTES).iter(W + "p"))[-1])[0]["code"] == " DATE "


def test_two_placeholders_in_one_paragraph_and_the_default_scope(src):
    def twice(root):
        p = body(root)[1]
        p.find(W + "r").find(W + "t").text = "Issued {{DATE}}, revised {{DATE}}."
    data = mutate(src, DOC, twice)
    for tracked in (True, False):
        plan, out, rec = field(data, {}, {"field": "DATE", "at_text": "{{DATE}}", "tracked": tracked})
        assert rec.passed, rec.to_markdown()
        assert shown(body(xml(out, DOC))[1]) == "Issued , revised ."
        assert len(fields_in(body(xml(out, DOC))[1])) == 2
        assert plan.property_assertions[0]["expected"][DOC]["1"] == 2
        assert set(plan.intended_parts) == {DOC}               # default scope: body, headers, footers (none there)


def test_the_field_takes_its_neighbours_formatting(src):
    """At the placeholder: the placeholder run's properties (bold over two runs); at the end: the last text run's
    (italic); at the start of an empty paragraph: the paragraph mark's (bold)."""
    plan, out, rec = field(src, BODY, {"field": "DATE", "at_text": "{{DATE}}", "tracked": False})
    assert rec.passed
    runs = [r for r in body(xml(out, DOC))[2].iter(W + "r") if r.find(W + "fldChar") is not None
            or r.find(W + "instrText") is not None]
    assert len(runs) == 4 and all(r.find(f"{W}rPr/{W}b") is not None for r in runs)
    plan, out, rec = field(src, {"scope": ["document"], "paragraphs": {"text_regex": "^End"}},
                           {"field": "PAGE", "position": "end", "tracked": False})
    assert rec.passed
    assert all(r.find(f"{W}rPr/{W}i") is not None for r in body(xml(out, DOC))[6].findall(W + "r"))
    plan, out, rec = field(src, {"scope": ["headers"], "paragraphs": {"is_empty": True}},
                           {"field": "PAGE", "position": "start"})
    assert rec.passed, rec.to_markdown()
    ins = xml(out, HEADER).findall(W + "p")[1].find(W + "ins")
    assert len(ins) == 4 and all(r.find(f"{W}rPr/{W}b") is not None for r in ins)


def test_a_file_without_the_placeholder(src):
    with pytest.raises(JobException) as e:
        get_op("insert_field").plan(DocxPackage(src), {}, {"field": "PAGE", "at_text": "{{NOWHERE}}"})
    assert e.value.code == "not-found"
    plan, out, rec = field(src, {}, {"field": "PAGE", "at_text": "{{NOWHERE}}", "allow_missing": True})
    assert rec.passed and out == src and plan.property_assertions[0]["expected"] == {}


# ---- a wrong output is caught ----------------------------------------------------------------------------------------

def _date(src, tracked=True):
    return field(src, BODY, {"field": "DATE", "at_text": "{{DATE}}", "tracked": tracked})


def _field_runs(root, k=1):
    return [r for r in body(root)[k].iter(W + "r") if r.find(W + "fldChar") is not None
            or r.find(W + "instrText") is not None]


def test_a_missing_dirty_mark_is_caught(src):
    plan, out, rec = _date(src)
    assert rec.passed
    _caught(src, plan, out, DOC, lambda r: _field_runs(r)[0].find(W + "fldChar").attrib.pop(W + "dirty"),
            "not exactly the field")


def test_a_changed_instruction_is_caught(src):
    plan, out, rec = _date(src)
    _caught(src, plan, out, DOC, lambda r: setattr(_field_runs(r)[1].find(W + "instrText"), "text", " TIME "),
            check="V2")
    _caught(src, plan, out, DOC, lambda r: setattr(_field_runs(r)[1].find(W + "instrText"), "text", " TIME "),
            "not exactly the field")


def test_a_written_result_is_caught(src):
    """The engine never writes what a field shows: one character between separate and end is a generated character."""
    plan, out, rec = _date(src, tracked=False)

    def result(root):
        sep = _field_runs(root)[2]
        r = etree.Element(W + "r")
        etree.SubElement(r, W + "t").text = "1"
        sep.addnext(r)
    _caught(src, plan, out, DOC, result, check="V2")
    _caught(src, plan, out, DOC, result, "not exactly the field")


def test_lost_formatting_and_merged_runs_are_caught(src):
    plan, out, rec = _date(src, tracked=False)
    assert rec.passed

    def bare(root):
        r = _field_runs(root, 2)[1]
        r.remove(r.find(W + "rPr"))
    _caught(src, plan, out, DOC, bare, "formatting or place")

    def merged(root):
        runs = _field_runs(root)
        for r in runs[1:]:
            runs[0].append(r[-1])
            r.getparent().remove(r)
    _caught(src, plan, out, DOC, merged, "four runs of their own")


def test_an_untracked_field_in_a_tracked_plan_is_caught(src):
    plan, out, rec = _date(src)

    def unwrap(root):
        ins = _field_runs(root)[0].getparent()
        for r in list(ins):
            ins.addprevious(r)
        ins.getparent().remove(ins)
    bad = mutate(out, DOC, unwrap)
    rec = run_suite(src, bad, plan.to_dict(), "src.docx", "out.docx")
    assert {"V4", "V10"} <= set(failed(rec))


def test_a_placeholder_left_behind_is_caught(src):
    plan, out, rec = _date(src, tracked=False)

    def back(root):
        t = body(root)[1].find(W + "r").find(W + "t")
        t.text = "Issued {{DATE}}"
    _caught(src, plan, out, DOC, back, check="V2")
    _caught(src, plan, out, DOC, back, "not exactly the field")


# ---- a wrong implementation shared by plan and apply is caught -------------------------------------------------------

def test_a_shared_wrong_instruction_is_caught(src, monkeypatch):
    real = ifm.instruction
    monkeypatch.setattr(ifm, "instruction", lambda pkg, sheet, params: (" DATE ", real(pkg, sheet, params)[1]))
    plan, out, rec = field(src, BODY, {"field": "DATE", "at_text": "{{DATE}}", "format": "d MMMM yyyy"})
    assert "V2" not in failed(rec)                                  # plan and apply agree with each other
    assert "V10" in failed(rec) and "not exactly the field" in v10(rec)


def test_a_shared_wrong_style_name_is_caught(src, monkeypatch):
    monkeypatch.setattr(ifm, "style_name", lambda sheet, wanted: ("Heading1", "Heading1"))
    plan, out, rec = field(src, {"scope": ["headers"]}, {"field": "STYLEREF", "style": "Heading1",
                                                         "at_text": "{{TITLE}}"})
    assert "V10" in failed(rec) and "not exactly the field" in v10(rec)


def test_a_shared_dirty_rule_that_leaves_a_date_clean_is_caught(src, monkeypatch):
    monkeypatch.setattr(ifm, "COMPUTED_AT_LAYOUT", ("PAGE", "NUMPAGES", "SECTIONPAGES", "DATE"))
    plan, out, rec = _date(src)
    assert "V10" in failed(rec) and "not exactly the field" in v10(rec)


def test_a_shared_rule_that_skips_the_core_checks_is_caught(src, monkeypatch):
    """A tracked change in a hyperlink is the core's refusal; a plan and apply that skip it are caught by the verifier's
    own reading of the rules."""
    monkeypatch.setattr(ifm, "check_edits",
                        lambda rt, edits, tracked, in_field, where: sorted(edits, key=lambda e: e.start))
    plan, out, rec = field(src, BODY, {"field": "FILENAME", "at_text": "{{FILENAME}}"})
    assert "V10" in failed(rec) and "the op must refuse" in v10(rec)


def test_a_shared_field_depth_that_misses_a_table_of_contents_is_caught(monkeypatch):
    data = build_fields(toc=True)
    monkeypatch.setattr(ifm, "field_depths", lambda pars: [(0, 0)] * len(pars))
    plan, out, rec = field(data, {"scope": ["document"], "paragraphs": {"text_regex": "^ TOC"}},
                           {"field": "PAGE", "position": "end"})
    assert "V10" in failed(rec) and "inside a field" in v10(rec)


def test_a_shared_wrong_neighbour_is_caught(src, monkeypatch):
    monkeypatch.setattr(ifm, "edge_rpr", lambda p, position: None)
    plan, out, rec = field(src, {"scope": ["document"], "paragraphs": {"text_regex": "^End"}},
                           {"field": "PAGE", "position": "end"})
    assert "V10" in failed(rec) and "formatting or place" in v10(rec)


# ---- refusals --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("data_kw, params, code", [
    ({}, {"field": "FILENAME", "at_text": "{{FILENAME}}"}, "nested-container"),        # a tracked change in a link
    ({"in_field": True}, {"field": "PAGE", "at_text": "{{PAGE}}"}, "field-result"),
    ({"in_field": True}, {"field": "PAGE", "at_text": "{{PAGE}}", "tracked": False}, "field-result"),
    ({"deleted_inside": True}, {"field": "PAGE", "at_text": "{{PAGE}}", "tracked": False}, "structure"),
    ({"tracked_format": True}, {"field": "DATE", "at_text": "{{DATE}}"}, "tracked-format"),
    ({"tracked_format": True}, {"field": "DATE", "at_text": "{{DATE}}", "tracked": False}, "tracked-format"),
    ({"textbox": True}, {"field": "DATE", "at_text": "Box dated {{DATE}}", "tracked": False}, "textbox"),
    ({}, {"field": "STYLEREF", "style": "Spec Heading", "at_text": "{{TITLE}}"}, "field-argument"),
    ({}, {"field": "STYLEREF", "style": "TableGrid", "at_text": "{{TITLE}}"}, "field-argument"),
    ({}, {"field": "DOCPROPERTY", "property": "Client", "at_text": "{{PROJECT}}"}, "field-argument"),
    ({"custom": False}, {"field": "DOCPROPERTY", "property": "Project Number", "at_text": "{{PROJECT}}"},
     "field-argument"),
])
def test_refusals(data_kw, params, code):
    data = build_fields(**data_kw)
    with pytest.raises(JobException) as e:
        get_op("insert_field").plan(DocxPackage(data), {}, params)
    assert e.value.code == code, e.value.reason


def test_a_table_of_contents_refuses_a_field_inside_it():
    data = build_fields(toc=True)
    first = {"scope": ["document"], "paragraphs": {"text_regex": "^ TOC"}}
    last = {"scope": ["document"], "paragraphs": {"text_regex": "^Part 2"}}
    for select, position in ((first, "end"), (last, "start")):
        with pytest.raises(JobException) as e:
            get_op("insert_field").plan(DocxPackage(data), select, {"field": "PAGE", "position": position})
        assert e.value.code == "field-result", (select, position)
    plan, out, rec = field(data, first, {"field": "PAGE", "position": "start"})       # before the field begins
    assert rec.passed, rec.to_markdown()
    plan, out, rec = field(data, last, {"field": "PAGE", "position": "end"})           # after it ends
    assert rec.passed, rec.to_markdown()


@pytest.mark.parametrize("params", [
    {}, {"field": "page", "at_text": "x"}, {"field": "TIME", "at_text": "x"}, {"field": "PAGE"},
    {"field": "PAGE", "at_text": "x", "position": "end"}, {"field": "PAGE", "at_text": ""},
    {"field": "PAGE", "at_text": "a\tb"}, {"field": "PAGE", "position": "middle"},
    {"field": "PAGE", "at_text": "x", "format": "Roman"}, {"field": "PAGE", "at_text": "x", "format": "MMMM"},
    {"field": "DATE", "at_text": "x", "format": "roman"}, {"field": "DATE", "at_text": "x", "format": "dd 'of' MMMM"},
    {"field": "DATE", "at_text": "x", "format": 'd"M'}, {"field": "DATE", "at_text": "x", "format": " - "},
    {"field": "FILENAME", "at_text": "x", "format": "arabic"}, {"field": "FILENAME", "at_text": "x", "path": "yes"},
    {"field": "PAGE", "at_text": "x", "style": "Heading1"}, {"field": "STYLEREF", "at_text": "x"},
    {"field": "DOCPROPERTY", "at_text": "x"}, {"field": "DOCPROPERTY", "at_text": "x", "property": " "},
    {"field": "PAGE", "at_text": "x", "tracked": "no"}, {"field": "PAGE", "at_text": "x", "instruction": " PAGE "},
    {"field": "PAGE", "at_text": "x", "date_convention": "local"}, {"field": "PAGE", "at_text": "x", "author": ""},
])
def test_parameters_are_checked(src, params):
    with pytest.raises(JobException) as e:
        get_op("insert_field").plan(DocxPackage(src), {}, params)
    assert e.value.code == "bad-params"


@pytest.mark.parametrize("select", [
    {"scope": ["comments"]}, {"scope": []}, {"runs": {}}, {"paragraphs": "some"},
])
def test_selectors_are_checked(src, select):
    with pytest.raises(JobException) as e:
        get_op("insert_field").plan(DocxPackage(src), select, {"field": "PAGE", "at_text": "{{PAGE}}"})
    assert e.value.code == "bad-params"
    with pytest.raises(JobException) as e:                       # a field at an edge names its paragraphs
        get_op("insert_field").plan(DocxPackage(src), {}, {"field": "PAGE", "position": "end"})
    assert e.value.code == "bad-params"


def test_every_refusal_code_is_explained():
    codes = set()
    for n in ast.walk(ast.parse(Path(ifm.__file__).read_text(encoding="utf-8"))):
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "JobException" and len(n.args) > 1:
            assert isinstance(n.args[1], ast.Constant), n.lineno
            codes.add(n.args[1].value)
    codes.discard("internal")
    assert codes == {"bad-params", "field-argument", "field-result", "not-found", "textbox", "tracked-format"}
    # the edit core's own refusals reach this op too
    assert sorted(c for c in codes | {"structure", "nested-container", "ambiguous"} if c not in FAILURE_EXPLANATIONS) == []


# ---- touches and conflicts (rule 9) ----------------------------------------------------------------------------------

def _plan(data, *specs):
    pkg = DocxPackage(data)
    return merge_fragments([get_op(o).plan(pkg, s, p) for o, s, p in specs])


def test_a_text_edit_of_the_same_paragraph_conflicts(src):
    with pytest.raises(JobException) as e:
        _plan(src, ("insert_field", BODY, {"field": "DATE", "at_text": "{{DATE}}"}),
              ("replace_text", BODY, {"find": "for review", "replace": "for bid"}))
    assert e.value.code == "conflict"
    # another paragraph: both run, each verified
    plan, out, rec = run_ops(src, [("insert_field", BODY, {"field": "DATE", "at_text": "{{DATE}}", "author": "A"}),
                                   ("replace_text", BODY, {"find": "End of section", "replace": "END OF SECTION",
                                                           "author": "A"})])
    assert rec.passed, rec.to_markdown()


def test_unlinking_fields_beside_an_inserted_one_conflicts(src):
    """Unlinking every field of a part while a field goes into it: the order would decide whether the new one stays."""
    with pytest.raises(JobException) as e:
        _plan(src, ("insert_field", {"scope": ["footers"]}, {"field": "PAGE", "at_text": "{{PAGE}}"}),
              ("unlink_fields", {"scope": ["footers"]}, {"types": "all"}))
    assert e.value.code == "conflict"
    _plan(src, ("insert_field", {"scope": ["footers"]}, {"field": "PAGE", "at_text": "{{PAGE}}"}),
          ("unlink_fields", {"scope": ["headers"]}, {"types": "all"}))


def test_two_fields_in_one_paragraph_conflict_and_compose_in_order(src):
    footer = {"scope": ["footers"]}
    with pytest.raises(JobException) as e:
        _plan(src, ("insert_field", footer, {"field": "PAGE", "at_text": "{{PAGE}}"}),
              ("insert_field", footer, {"field": "NUMPAGES", "at_text": "{{NUMPAGES}}"}))
    assert e.value.code == "conflict"
    _, first, rec = field(src, footer, {"field": "PAGE", "at_text": "{{PAGE}}"})
    _, second, rec = field(first, footer, {"field": "NUMPAGES", "at_text": "{{NUMPAGES}}"})
    assert rec.passed, rec.to_markdown()
    assert [f["code"] for f in fields_in(xml(second, FOOTER).find(W + "p"))] == [" PAGE ", " NUMPAGES "]


def test_page_and_count_in_one_request_run_as_two_verified_steps(src, tmp_path):
    """"Page {{PAGE}} of {{NUMPAGES}}" asked in one spec: the two fields share a paragraph, so the engine cuts the spec
    into two steps in the user's order, the second planned on the first one's output, each verified."""
    from spec_surgeon.chain import run_chain
    from spec_surgeon.editspec import split_into_stages, stage_cuts
    spec = _spec({"op": "insert_field", "select": {"scope": ["footers"]},
                  "params": {"field": "PAGE", "at_text": "{{PAGE}}", "author": "Abe"}},
                 {"op": "insert_field", "select": {"scope": ["footers"]},
                  "params": {"field": "NUMPAGES", "at_text": "{{NUMPAGES}}", "author": "Abe"}})
    pkg = DocxPackage(src, name="x.docx")
    assert stage_cuts(pkg, spec) == [1, 1]
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "21 13 13 – WET-PIPE SPRINKLER SYSTEMS.docx").write_bytes(src)
    r = run_chain([str(folder)], split_into_stages(pkg, spec), tmp_path / "run", workers=1, renderer="none",
                  do_render=False)
    assert r.counts()["PASS"] == 1, [(f.status, f.reason) for f in r.files]
    out = next((tmp_path / "run" / "outputs").glob("*.docx")).read_bytes()
    assert [f["code"] for f in fields_in(xml(out, FOOTER).find(W + "p"))] == [" PAGE ", " NUMPAGES "]


def test_the_reads_of_a_style_and_a_custom_property(src):
    t = set(get_op("insert_field").plan(DocxPackage(src), {"scope": ["headers"]},
                                        {"field": "STYLEREF", "style": "heading 1", "at_text": "{{TITLE}}"}).touches)
    assert ("word/styles.xml", "style:Heading1", "verify") in t and (HEADER, "fields", "add") in t
    t = set(get_op("insert_field").plan(DocxPackage(src), BODY, {"field": "DOCPROPERTY", "property": "Project Number",
                                                                 "at_text": "{{PROJECT}}"}).touches)
    assert (CUSTOM, "*", "verify") in t and (DOC, "5", "text") in t and (DOC, "scope", "verify") in t
    with pytest.raises(JobException) as e:
        _plan(src, ("insert_field", BODY, {"field": "DOCPROPERTY", "property": "Project Number",
                                           "at_text": "{{PROJECT}}"}),
              ("set_document_properties", {}, {"custom": {"Project Number": "P-2000"}}))
    assert e.value.code == "conflict"


# ---- catalog, pipeline, render ---------------------------------------------------------------------------------------

def _spec(*ops):
    return {"request": "r", "summary": "s", "mode": "content", "ops": list(ops)}


def test_catalog_and_schema():
    from spec_surgeon.editspec import SpecError, validate_spec
    cat = op_catalog()
    assert "insert_field" in cat["ops"] and COMMON_ASSUMPTIONS["insert_field"]
    validate_spec(_spec({"op": "insert_field", "select": {"scope": ["footers"]},
                         "params": {"field": "PAGE", "at_text": "{{PAGE}}", "format": "roman"}}))
    for bad in ({"field": "TIME", "at_text": "x"}, {"at_text": "x"}, {"field": "PAGE", "position": "middle"}):
        with pytest.raises(SpecError):
            validate_spec(_spec({"op": "insert_field", "params": bad}))


def test_a_spec_runs_through_the_batch_pipeline(src, tmp_path):
    from spec_surgeon.batch import run_batch
    spec = _spec({"op": "insert_field", "select": {"scope": ["footers"]},
                  "params": {"field": "PAGE", "at_text": "{{PAGE}}", "author": "Abe"}},
                 {"op": "insert_field", "select": {"scope": ["document"]},
                  "params": {"field": "DATE", "at_text": "{{DATE}}", "format": "MMMM d, yyyy", "author": "Abe"}})
    path = tmp_path / "21 13 13 – WET-PIPE SPRINKLER SYSTEMS.docx"
    path.write_bytes(src)
    batch = run_batch([str(path)], "edit-spec", {}, tmp_path / "out", workers=1, renderer="none", do_render=False,
                      spec=spec)
    f = batch.files[0]
    assert f.status == "PASS", f.record.to_markdown() if f.record else f.exception


@pytest.mark.skipif(shutil.which("soffice") is None or shutil.which("pdftotext") is None,
                    reason="LibreOffice or pdftotext not installed")
def test_libreoffice_renders_the_page_numbers(src, tmp_path):
    """The fields go in empty; LibreOffice computes them as it lays out the pages: every page's footer shows its own
    number and the count (untracked here: LibreOffice's PDF shows a tracked deletion's words struck through)."""
    from spec_surgeon.render import libreoffice

    def two_pages(root):
        p = body(root)[3]
        br = etree.SubElement(etree.SubElement(p, W + "r"), W + "br")
        br.set(W + "type", "page")
    data = mutate(src, DOC, two_pages)
    footer = {"scope": ["footers"]}
    _, first, rec = field(data, footer, {"field": "PAGE", "at_text": "{{PAGE}}", "tracked": False})
    assert rec.passed, rec.to_markdown()
    _, out, rec = field(first, footer, {"field": "NUMPAGES", "at_text": "{{NUMPAGES}}", "tracked": False})
    assert rec.passed, rec.to_markdown()
    (tmp_path / "out.docx").write_bytes(out)
    pdfs = libreoffice.render_pdfs([tmp_path / "out.docx"], tmp_path / "pdf", workers=1)
    if len(pdfs) != 1:
        pytest.skip("LibreOffice did not render here")
    text = subprocess.run(["pdftotext", "-layout", str(pdfs[tmp_path / "out.docx"]), "-"], capture_output=True,
                          text=True, check=True).stdout
    words = " ".join(text.split()).upper()                      # the footer's small capitals
    assert "PAGE 1 OF 2" in words and "PAGE 2 OF 2" in words, text
    assert "{{PAGE}}" not in text and "{{NUMPAGES}}" not in text
