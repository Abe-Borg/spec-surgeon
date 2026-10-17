"""audit_numbering and V10's ``unnumbered_empty`` kind (built-in batch 5, item 36 — docs/BUILTIN_OPS_BATCH5_DESIGN.md
§4): the report on the architect's outline (clean, and with a shared definition), on typed numbering (with a duplicate
"B."), on the example scheme (nothing to report) and on crafted sequences (gap, duplicate, goes back, starts at); the typed
grammar; the fix (``unnumber_empty``) end to end through the full suite, plain and tracked, a blank line of no-break
spaces and a bookmark; wrong OUTPUTS and wrong IMPLEMENTATIONS shared by plan and apply (a blank rule that forgets the
no-break space, a fix that removes the whole numPr, a count that skips hidden paragraphs); refusals; plan-time conflicts
(rule 9); the batch pipeline; the catalog and the worked example."""
from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from typing import List

import pytest
from docx import Document
from lxml import etree

from make_fixtures import build_numbering, build_scheme_example, build_typed, patch_docx
from spec_surgeon.catalog import COMMON_ASSUMPTIONS, op_catalog
from spec_surgeon.editspec import apply_spec, plan_spec, validate_spec
from spec_surgeon.ops import JobException, get_op
from spec_surgeon.ops import audit_numbering as aud
from spec_surgeon.ops import lists as tl
from spec_surgeon.ops.selectors import StyleSheet
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.verify import run_suite
from spec_surgeon.verify import numberingaudit as vna

MAIN = "word/document.xml"
NUMBERING = "word/numbering.xml"
SPECS = Path(__file__).resolve().parents[1] / "examples" / "specs"
WD = f'xmlns:w="{W[1:-1]}"'
EMPTY_C = 10                     # build_numbering: the empty ArchPara paragraph that shows "C."


@pytest.fixture(scope="module")
def clean() -> bytes:
    return build_numbering(shared_restart=False)


@pytest.fixture(scope="module")
def shared() -> bytes:
    return build_numbering(shared_restart=True)


def _spec(*ops):
    return {"request": "r", "summary": "s", "mode": "formatting", "ops": list(ops)}


def run(data: bytes, *ops):
    pkg = DocxPackage(data)
    spec = _spec(*ops)
    if any(o["op"] == "delete_paragraphs" for o in ops):
        spec["mode"] = "content"
    plan, frags = plan_spec(pkg, spec)
    out = apply_spec(pkg, frags).output_bytes()
    return plan, out, run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")


def auditing(**params) -> dict:
    return {"op": "audit_numbering", "params": params}


def findings(data: bytes):
    pkg = DocxPackage(data)
    return aud.audit(pkg, StyleSheet(pkg), tl.ListDefs.load(pkg), pkg.xml(MAIN)).findings


def kinds(data: bytes) -> List[tuple]:
    return [(f.kind, f.index) for f in findings(data)]


def v10(rec) -> str:
    c = next(c for c in rec.checks if c.id == "V10")
    return c.summary + " | " + " | ".join(c.data.get("errors", []))


def failed(rec) -> List[str]:
    return [c.id for c in rec.checks if c.status == "FAIL"]


def text_of(p) -> str:
    return "".join(t.text or "" for t in p.iter(W + "t"))


def numbers(data: bytes) -> dict:
    pkg = DocxPackage(data)
    root = pkg.xml(MAIN)
    pars = list(root.iter(W + "p"))
    return {np_.index: np_.text for np_ in tl.count(root, StyleSheet(pkg), tl.ListDefs.load(pkg))
            if text_of(pars[np_.index]) or np_.index == EMPTY_C}


def par(data: bytes, i: int):
    return list(DocxPackage(data).xml(MAIN).iter(W + "p"))[i]


def edit_par(data: bytes, i: int, fn) -> bytes:
    return patch_docx(data, {MAIN: lambda r: fn(list(r.iter(W + "p"))[i])})


def typed_doc(*lines: str) -> bytes:
    doc = Document()
    for line in lines:
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def conflict(data: bytes, *ops) -> str:
    for order in (ops, tuple(reversed(ops))):
        spec = _spec(*order)
        if any(o["op"] == "delete_paragraphs" for o in ops):
            spec["mode"] = "content"
        with pytest.raises(JobException) as e:
            plan_spec(DocxPackage(data), spec)
        assert e.value.code == "conflict", (order, e.value.code, e.value.reason)
    return str(e.value)


# ---- the report ------------------------------------------------------------------------------------------------------

def test_the_report_on_the_architects_outline(clean):
    """Report only: nothing changes; the empty "C.", the hidden "B.", the typed "B." on a numbered paragraph, the two
    lists the model does not predict and the appendix list's restart rules are flagged — and the outline itself (every
    article, paragraph and subparagraph of the main list) has no gap: the hidden B. moves the sequence on silently."""
    plan, out, rec = run(clean, auditing())
    assert rec.passed and out == clean and not plan.intended_parts and not plan.property_assertions
    assert plan.geometry_reference == "source"
    flags = "\n".join(plan.flags)
    assert "‘C.’ is shown on an empty line (after ‘Division 28 Section" in flags
    assert "‘Hidden requirement.’ (B.) is hidden but uses up a number" in flags
    assert "‘B. Check valves.’ is numbered automatically (B.) and starts with a typed ‘B.’ — double numbering" in flags
    assert "list 23 — 1 paragraph(s), first ‘Override sub’" in flags and "full override level" in flags
    assert "list 22 — 2 paragraph(s), first ‘Legal three’: skipped level" in flags
    assert "list 10 — 3 paragraph(s), first ‘Shop drawings.’: skipped level: level 2 of list 10 was skipped" in flags
    assert "list 22 (first ‘Appendix one’) sets a restart rule (w:lvlRestart) on level(s) [2, 3]: LibreOffice" in flags
    k = kinds(clean)
    assert ("empty", EMPTY_C) in k and ("hidden", 14) in k and ("double", 37) in k
    outline = [f for f in findings(clean) if f.kind in ("gap", "duplicate", "back", "start") and f.index < 45]
    assert outline == []                                       # the main list reads as it should
    assert any(n.startswith("numbering audit: 4 list(s) number 51 counted paragraph(s) (45 evaluated)") for n in plan.notes)
    frag = get_op("audit_numbering").plan(DocxPackage(clean), {}, {})
    assert (NUMBERING, "*", "verify") in frag.touches and (MAIN, "37", "verify:text") in frag.touches
    assert (MAIN, str(EMPTY_C), "verify:numPr") in frag.touches and not any(a in ("pPr", "numPr") for _, _, a in
                                                                            frag.touches)


def test_a_shared_definition_is_named_once(shared):
    plan, out, rec = run(shared, auditing())
    assert rec.passed and out == shared
    shared_flags = [f for f in plan.flags if "share one definition" in f]
    assert len(shared_flags) == 1 and "lists [10, 11] share one definition (abstractNum 10) — 35 paragraph(s)" in \
        shared_flags[0] and "LibreOffice keeps them apart" in shared_flags[0]
    assert not any(f.kind == "empty" for f in findings(shared))  # not evaluated: its number is not known


def test_typed_numbering_and_its_duplicate(clean):
    assert findings(build_typed()) == [] and findings(build_typed(leading_tab=True)) == []
    assert findings(build_typed(spaces=True)) == []
    plan, out, rec = run(build_typed(typo=True), auditing())
    assert rec.passed
    dup = [f for f in findings(build_typed(typo=True)) if f.kind == "duplicate"]
    assert len(dup) == 1 and dup[0].text == "numbering: ‘B. Reliable.’ shows B. again, as ‘B. Viking.’ before it"
    assert len(findings(build_typed(typo=True))) == 1
    assert any("22 typed number(s) checked; 1 finding(s): 1 duplicate" in n for n in plan.notes)   # not the 2 cells


def test_a_dangling_list_is_reported(clean):
    data = edit_par(clean, 0, lambda p: p.insert(0, etree.fromstring(
        f'<w:pPr {WD}><w:numPr><w:ilvl w:val="0"/><w:numId w:val="99"/></w:numPr></w:pPr>')))
    got = [f for f in findings(data) if f.kind == "dangling"]
    assert len(got) == 1 and got[0].index == 0 and "names list 99, which numbering.xml does not define" in got[0].text


def test_the_example_scheme_has_nothing_to_report():
    plan, out, rec = run(build_scheme_example(), auditing())
    assert rec.passed and plan.flags == [] and findings(build_scheme_example()) == []


def test_gaps_going_back_and_new_starts():
    data = typed_doc("PART 1 - GENERAL", "1.1\tSUMMARY", "A.\tOne.", "C.\tThree.", "B.\tTwo.", "1.2\tREFERENCES",
                     "B.\tStarts late.", "1.2\tAgain.", "PART 2 - PRODUCTS", "2.1\tMANUFACTURERS", "A.\tFine.",
                     "Body text 1.5 inch.", "1.5 inch pipe.")
    got = [(f.kind, f.index) for f in findings(data)]
    assert ("gap", 3) in got and ("back", 4) in got and ("start", 6) in got and ("duplicate", 7) in got
    assert ("gap", 12) in got and ("part", 12) in got   # "1.5 inch pipe." reads as a typed n.n (advisory)
    assert len(got) == 6
    texts = {f.index: f.text for f in findings(data)}
    assert texts[3] == "numbering: ‘C. Three.’ shows C. after A. (‘A. One.’): a number is skipped"
    assert texts[6] == "numbering: ‘B. Starts late.’ starts its sequence at B."


def test_the_typed_grammar():
    cases = {"PART 1 - GENERAL": ("PART <1>", 1), "PART 12 GENERAL": ("PART <1>", 12),
             "ARTICLE 3": ("ARTICLE <1>", 3), "1.1\tSUMMARY": ("<1>.<1>", 1), "2.01 MATERIALS": ("<1>.<1>", 1),
             "1.2.3. Item": ("<1>.<1>.<1>.", 3), "\t3.\tThree": ("<1>.", 3), "4) Four": ("<1>)", 4),
             "(5) Five": ("(<1>)", 5), "B.\tTwo": ("<A>.", 2), "c) three": ("<a>)", 3), "(d) four": ("(<a>)", 4),
             "I. One": ("<A>.", 9), "II. Two": ("<I>.", 2), "iv) four": ("<i>)", 4), "(xii) twelve": ("(<i>)", 12),
             "A. Nbsp": ("<A>.", 1), "Z.": ("<A>.", 26)}
    for text, (shape, value) in cases.items():
        got = aud.typed_shape(text)
        assert got is not None and (got.shape, got.value) == (shape, value), text
    for text in ("SECTION 21 13 13", "A.Section", "1.5in", "AB. two letters", "IIII. not roman", "Part 1 - lower",
                 "10 psi", "", "Body text."):
        assert aud.typed_shape(text) is None, text


# ---- the fix ---------------------------------------------------------------------------------------------------------

def test_the_fix_unnumbers_the_empty_paragraph_and_later_letters_move_up(clean):
    plan, out, rec = run(clean, auditing(unnumber_empty=True))
    assert rec.passed, rec.to_markdown()
    a = plan.property_assertions[0]
    assert a["kind"] == "unnumbered_empty" and a["changed"] == [EMPTY_C] and a["params"] == {"unnumber_empty": True}
    numpr = par(out, EMPTY_C).find(f"{W}pPr/{W}numPr")
    assert [(c.tag, dict(c.attrib)) for c in numpr] == [(W + "numId", {W + "val": "0"})] and not numpr.attrib
    assert [etree.QName(c).localname for c in par(out, EMPTY_C).find(W + "pPr")] == ["pStyle", "numPr"]
    before, after = numbers(clean), numbers(out)
    assert before[11] == "D." and after[11] == "C." and EMPTY_C not in after
    assert {i: t for i, t in after.items() if i != 11} == {i: t for i, t in before.items() if i not in (11, EMPTY_C)}
    assert "numbering after the fix: ‘Deferred submittal.’ D. → C." in plan.flags
    assert plan.intended_parts == [MAIN] and plan.geometry_reference == "none" and not any(plan.rev_delta.values())
    frag = get_op("audit_numbering").plan(DocxPackage(clean), {}, {"unnumber_empty": True})
    for t in ((MAIN, str(EMPTY_C), "pPr"), (MAIN, str(EMPTY_C), "numPr"), (MAIN, "scope", "verify")):
        assert t in frag.touches, t


def test_the_fix_tracked(clean):
    plan, out, rec = run(clean, auditing(unnumber_empty=True, tracked=True, author="Abraham Borg"))
    assert rec.passed, rec.to_markdown()
    ppr = par(out, EMPTY_C).find(W + "pPr")
    chg = ppr.find(W + "pPrChange")
    assert list(ppr)[-1] is chg and chg.get(W + "author") == "Abraham Borg"
    assert [etree.QName(c).localname for c in chg.find(W + "pPr")] == ["pStyle"]
    assert plan.rev_delta.get("pPrChange") == 1


def test_no_break_spaces_and_a_bookmark_are_still_blank(clean):
    def fill(p):
        p.append(etree.fromstring(f'<w:bookmarkStart {WD} w:id="77" w:name="_Toc77"/>'))
        p.append(etree.fromstring(f"<w:r {WD}><w:rPr><w:b/></w:rPr><w:t xml:space=\"preserve\">   </w:t>"
                                  f"<w:lastRenderedPageBreak/></w:r>"))
        p.append(etree.fromstring(f'<w:bookmarkEnd {WD} w:id="77"/>'))
    data = edit_par(clean, EMPTY_C, fill)
    plan, out, rec = run(data, auditing(unnumber_empty=True))
    assert rec.passed and plan.property_assertions[0]["changed"] == [EMPTY_C]
    # a tab is content: no longer empty, nothing to change
    data2 = edit_par(clean, EMPTY_C, lambda p: p.append(etree.fromstring(f"<w:r {WD}><w:tab/></w:r>")))
    plan, out, rec = run(data2, auditing(unnumber_empty=True))
    assert rec.passed and out == data2 and plan.property_assertions[0]["changed"] == []
    assert any("no empty numbered paragraph" in n for n in plan.notes)


def test_a_file_without_empty_numbered_paragraphs_is_a_noted_no_op():
    data = build_scheme_example()
    plan, out, rec = run(data, auditing(unnumber_empty=True))
    assert rec.passed and out == data and not plan.intended_parts


def test_a_wrong_output_is_caught(clean):
    plan, out, rec = run(clean, auditing(unnumber_empty=True))
    for fn, msg in ((lambda p: p.find(f"{W}pPr/{W}numPr").insert(0, etree.fromstring(f'<w:ilvl {WD} w:val="2"/>')),
                     "not exactly"),
                    (lambda p: p.find(W + "pPr").append(etree.fromstring(f"<w:jc {WD} w:val=\"center\"/>")),
                     "unpredicted change"),         # the residual: the fix's check leaves the rest to it
                    (lambda p: p.find(W + "pPr").insert(0, p.find(f"{W}pPr/{W}numPr")), "w:numPr is child 0")):
        bad = edit_par(out, EMPTY_C, fn)
        rec = run_suite(clean, bad, plan.to_dict(), "src.docx", "out.docx")
        assert "V10" in failed(rec) and msg in v10(rec), msg


def test_a_shared_blank_rule_that_forgets_the_no_break_space_is_caught(clean, monkeypatch):
    data = edit_par(clean, EMPTY_C, lambda p: p.append(etree.fromstring(
        f"<w:r {WD}><w:t xml:space=\"preserve\"> </w:t></w:r>")))
    monkeypatch.setattr(aud, "BLANK", " ")
    plan, out, rec = run(data, auditing(unnumber_empty=True))
    assert plan.property_assertions[0]["changed"] == [] and out == data
    assert "V10" in failed(rec) and "the empty numbered paragraphs here are [10], the plan's are []" in v10(rec)


def test_a_shared_fix_that_removes_the_whole_numpr_is_caught(clean, monkeypatch):
    def remove(p, stamp, rev_id):
        ppr = p.find(W + "pPr")
        if ppr is not None and ppr.find(W + "numPr") is not None:
            ppr.remove(ppr.find(W + "numPr"))
    monkeypatch.setattr(aud, "unnumber", remove)
    plan, out, rec = run(clean, auditing(unnumber_empty=True))
    assert numbers(out)[EMPTY_C] == "C."                       # the style's list still numbers it
    assert "V10" in failed(rec) and "no w:numPr in the output" in v10(rec)
    assert "the output's numbers are not the predicted ones" in v10(rec)


def test_a_shared_count_that_skips_hidden_paragraphs_is_caught(clean, monkeypatch):
    real = tl.counted

    def visible_only(p):
        ppr = p.find(W + "pPr")
        return real(p) and not (ppr is not None and ppr.find(f"{W}rPr/{W}vanish") is not None)
    monkeypatch.setattr(tl, "counted", visible_only)
    plan, out, rec = run(clean, auditing(unnumber_empty=True))
    assert "V10" in failed(rec) and "the plan's predicted numbers are not the verifier's" in v10(rec)


def test_both_blank_rules_agree_on_every_paragraph_of_the_fixtures(clean, shared):
    for data in (clean, shared, build_typed(), build_scheme_example()):
        for p in DocxPackage(data).xml(MAIN).iter(W + "p"):
            assert aud.blank_content(p) == vna._v_blank_content(p), text_of(p)


# ---- refusals and parameters ------------------------------------------------------------------------------------------

def _plan(data: bytes, **params):
    return get_op("audit_numbering").plan(DocxPackage(data), {}, params)


def test_refusals_and_bad_parameters(clean):
    for params in ({"tracked": True}, {"unnumber_empty": "yes"}, {"fix": True}):
        with pytest.raises(JobException) as e:
            _plan(clean, **params)
        assert e.value.code == "bad-params", params
    with pytest.raises(JobException) as e:
        get_op("audit_numbering").plan(DocxPackage(clean), {"scope": ["document"]}, {})
    assert e.value.code == "bad-params"
    pending = edit_par(clean, EMPTY_C, lambda p: p.find(W + "pPr").append(etree.fromstring(
        f'<w:numPr {WD}><w:ilvl w:val="2"/><w:numId w:val="10"/><w:ins w:id="901" w:author="J" '
        f'w:date="2026-09-22T10:15:00Z"/></w:numPr>')))
    with pytest.raises(JobException) as e:
        _plan(pending, unnumber_empty=True)
    assert e.value.code == "tracked-format"
    _plan(pending)                                             # the report itself is fine
    older = edit_par(clean, EMPTY_C, lambda p: p.find(W + "pPr").append(etree.fromstring(
        f'<w:pPrChange {WD} w:id="902" w:author="J" w:date="2026-09-22T10:15:00Z"><w:pPr/></w:pPrChange>')))
    with pytest.raises(JobException) as e:
        _plan(older, unnumber_empty=True, tracked=True)
    assert e.value.code == "tracked-format"
    plan, out, rec = run(older, auditing(unnumber_empty=True))
    assert rec.passed, rec.to_markdown()
    assert any("already carried a pending tracked paragraph change" in f for f in plan.flags)


# ---- plan-time conflicts (rule 9) -------------------------------------------------------------------------------------

def test_conflicts(clean):
    # deleting a numbered paragraph beside the fix (tracked: a text change of a counted paragraph; untracked: the part)
    for tracked in (True, False):
        conflict(clean, auditing(unnumber_empty=True), {"op": "delete_paragraphs",
                                                        "select": {"paragraphs": {"text_regex": "^Deferred"}},
                                                        "params": {"tracked": tracked}})
    # the fix and spacing on the same empty paragraph: both change its pPr
    conflict(clean, auditing(unnumber_empty=True), {"op": "set_paragraph_props",
                                                    "select": {"paragraphs": {"index_range": [EMPTY_C, EMPTY_C + 1]}},
                                                    "params": {"spacing": {"after": 240}}})
    # restyling beside the report: the count reads every paragraph's style
    conflict(clean, auditing(), {"op": "apply_style", "select": {"paragraphs": {"text_regex": "^Tyco"}},
                                 "params": {"style": "ArchArt"}})
    # a tracked deletion of an UNNUMBERED paragraph changes no count: it plans beside the report
    plan, out, rec = run(clean, auditing(), {"op": "delete_paragraphs",
                                             "select": {"paragraphs": {"text_regex": "^SECTION 21"}}})
    assert rec.passed, rec.to_markdown()


# ---- the pipeline, the catalog, the worked example --------------------------------------------------------------------

def test_a_spec_runs_through_the_batch_pipeline(clean, tmp_path):
    from spec_surgeon.batch import run_batch
    path = tmp_path / "21 13 13 – NUMBERED.docx"
    path.write_bytes(clean)
    batch = run_batch([str(path)], "edit-spec", {}, tmp_path / "out", workers=1, renderer="none", do_render=False,
                      spec=_spec(auditing(unnumber_empty=True, tracked=True)))
    f = batch.files[0]
    assert f.status == "PASS", f.record.to_markdown() if f.record else f.exception
    # report only: nothing to write, the findings reach the record's "flagged for review"
    batch = run_batch([str(path)], "edit-spec", {}, tmp_path / "out2", workers=1, renderer="none", do_render=False,
                      spec=_spec(auditing()))
    f = batch.files[0]
    assert f.status == "PASS" and f.noop, f.record.to_markdown() if f.record else f.exception
    assert any("is shown on an empty line" in fl for fl in f.record.flags)


def test_catalog_and_the_worked_example(clean):
    assert "audit_numbering" in op_catalog()["ops"] and COMMON_ASSUMPTIONS["audit_numbering"]
    spec = json.loads((SPECS / "audit-numbering.json").read_text(encoding="utf-8"))
    validate_spec(spec)
    pkg = DocxPackage(clean)
    plan, frags = plan_spec(pkg, spec)
    out = apply_spec(pkg, frags).output_bytes()
    assert run_suite(clean, out, plan.to_dict(), "src.docx", "out.docx").passed and out == clean and plan.flags


# ---- the adversarial review's findings (each test fails without its fix) ---------------------------------------------

def _after(text: str, el):
    def fn(root):
        p = next(p for p in root.iter(W + "p") if text_of(p) == text)
        p.addnext(el)
    return fn


def _table(cells):
    tcs = "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in cells)
    return etree.fromstring(f"<w:tbl {WD}><w:tblGrid/><w:tr>{tcs}</w:tr></w:tbl>")


def test_the_fix_beside_ops_that_change_the_mark_or_the_section_break(clean):
    """Item 3: the kind checks exactly what the fix changes (numPr, its place, pPrChange) and leaves the mark's rPr and a
    sectPr to the residual — set_font over the marks, bold on the paragraphs, page setup on a section break plan and
    pass beside the fix (they used to plan and fail V10)."""
    for other in ({"op": "set_font", "select": {"scope": ["document"], "runs": "all"}, "params": {"font": "Arial"}},
                  {"op": "set_properties", "select": {"scope": ["document"], "paragraphs": {"style_in": ["ArchPara"]}},
                   "params": {"run": {"b": True}, "tracked": False}}):
        plan, out, rec = run(clean, auditing(unnumber_empty=True), other)
        assert rec.passed, (other["op"], v10(rec))

    def section_break(r):
        body = r.find(W + "body")
        list(r.iter(W + "p"))[EMPTY_C].find(W + "pPr").append(copy.deepcopy(body.find(W + "sectPr")))
    data = patch_docx(clean, {MAIN: section_break})
    for tracked in (False, True):
        plan, out, rec = run(data, auditing(unnumber_empty=True, tracked=tracked),
                             {"op": "set_page_setup", "select": {"sections": "all"}, "params": {"margins": {"left": 2000}}})
        assert rec.passed, v10(rec)
    # still caught: a change of the mark nobody made
    plan, out, rec = run(clean, auditing(unnumber_empty=True))
    bad = edit_par(out, EMPTY_C, lambda p: p.find(W + "pPr").append(etree.fromstring(
        f"<w:rPr {WD}><w:b/></w:rPr>")))
    rec = run_suite(clean, bad, plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec) and "unpredicted change" in v10(rec)


def test_the_fix_flags_stale_cross_references(clean):
    """Item 4: the fix moves D. up to C.: a REF field pointing at it and a written "D." go stale — flagged."""
    def refs(r):
        deferred = next(p for p in r.iter(W + "p") if text_of(p) == "Deferred submittal.")
        deferred.insert(1, etree.fromstring(f'<w:bookmarkStart {WD} w:id="88" w:name="_Ref8800"/>'))
        deferred.append(etree.fromstring(f'<w:bookmarkEnd {WD} w:id="88"/>'))
        holder = next(p for p in r.iter(W + "p") if text_of(p) == "NFPA 13.")
        holder.append(etree.fromstring(
            f'<w:r {WD}><w:t xml:space="preserve"> See paragraph D. and </w:t></w:r>'))
        for x in (f'<w:r {WD}><w:fldChar w:fldCharType="begin"/></w:r>',
                  f'<w:r {WD}><w:instrText xml:space="preserve"> REF _Ref8800 \\r \\h </w:instrText></w:r>',
                  f'<w:r {WD}><w:fldChar w:fldCharType="separate"/></w:r>', f'<w:r {WD}><w:t>D.</w:t></w:r>',
                  f'<w:r {WD}><w:fldChar w:fldCharType="end"/></w:r>'):
            holder.append(etree.fromstring(x))
    data = patch_docx(clean, {MAIN: refs})
    plan, out, rec = run(data, auditing(unnumber_empty=True))
    assert rec.passed, v10(rec)
    flags = "\n".join(plan.flags)
    assert "numbering after the fix: 1 cross-reference field(s) point at paragraphs whose number the fix changes" in flags
    assert "REF _Ref8800 → ‘Deferred submittal.’" in flags
    assert "numbering after the fix: written references to old numbers that change" in flags and "'D.' (now 'C.')" in flags


def test_no_false_findings_on_ordinary_spec_text(clean):
    """Item 7: a pipe-size table's "1.5" is a value, not a number (table cells are skipped); a manufacturer "A. O.
    Smith Corporation." on a numbered paragraph is not double numbering; the outline's own counts — after a verified
    restart rule — are never judged (automatic numbers are context)."""
    data = patch_docx(clean, {MAIN: _after("Steel pipe.", _table(["1.5", "2.5"]))})
    assert not [f for f in findings(data) if f.kind in ("gap", "duplicate", "back", "start", "part")]

    def rename(r):
        next(p for p in r.iter(W + "p") if text_of(p) == "Tyco.").find(W + "r/" + W + "t").text = \
            "A. O. Smith Corporation."
    assert not [f for f in findings(patch_docx(clean, {MAIN: rename})) if f.kind == "double" and "Smith" in f.text]
    assert [f.kind for f in findings(clean) if f.kind == "double"] == ["double"]          # "B.⇥Check valves." still
    plan, out, rec = run(clean, {"op": "set_list_level", "params": {"style": "Arch Article", "restart": "never"}})
    assert rec.passed
    assert not [f for f in findings(out) if f.kind in ("gap", "duplicate", "back", "start", "part")]


def test_real_errors_in_typed_numbering_are_found():
    """Item 7: a component that belongs to another PART ("2.3" under PART 1, "1.1" under PART 2) is a finding; a roman
    list I./II./III. reads as roman; PART with and without the dash is one sequence."""
    got = [(f.kind, f.index) for f in findings(typed_doc(
        "PART 1 - GENERAL", "1.1\tSUMMARY", "1.2\tREFERENCES", "2.3\tSUBMITTALS", "PART 2 - PRODUCTS",
        "1.1\tMANUFACTURERS", "2.2\tMATERIALS"))]
    assert ("part", 3) in got and ("part", 5) in got and ("gap", 3) not in got
    assert findings(typed_doc("I.\tSCOPE", "II.\tPRODUCTS", "III.\tEXECUTION", "IV.\tCLOSE")) == []
    assert findings(typed_doc("PART 1 - GENERAL", "1.1\tSUMMARY", "1.2\tREFERENCES", "PART 2 PRODUCTS",
                              "2.1\tMANUFACTURERS")) == []
    assert findings(typed_doc("PART 1 GENERAL", "PART 2 - PRODUCTS")) == []
    assert findings(typed_doc(*[f"{c}.\tItem" for c in "ABCDEFGHIJ"])) == []    # I. continues the letters
    assert [(f.kind, f.index) for f in findings(typed_doc("I.\tOne", "III.\tThree"))] == [("gap", 1)]   # I = roman 1


def test_list_level_findings_come_first_and_survive_the_cap(clean):
    """Item 8: forty duplicate "A." lines cannot push the caveats (not evaluated, restart rule, linked level) past the
    cap; the empty paragraph after a text box names the body paragraph before it, not the text box's."""
    def flood(r):
        steel = next(p for p in r.iter(W + "p") if text_of(p) == "Steel pipe.")
        for k in range(40):
            steel.addnext(etree.fromstring(f'<w:p {WD}><w:r><w:t xml:space="preserve">A.\tLine {k}.</w:t></w:r></w:p>'))
    plan, out, rec = run(patch_docx(clean, {MAIN: flood}), auditing())
    assert rec.passed and len(plan.flags) == aud.MAX_FLAGS + 1 and "more finding(s)" in plan.flags[-1]
    head = plan.flags[:5]
    assert any("not evaluated" in f for f in head) and any("restart rule" in f for f in head)
    assert any("LINKED level" in f for f in head)

    def after_box(r):                          # a copy of the text box holder after "Tyco.", then an empty "B."
        holder = copy.deepcopy(next(p for p in r.iter(W + "p") if text_of(p).startswith("Text box holder")))
        tyco = next(p for p in r.iter(W + "p") if text_of(p) == "Tyco.")
        tyco.addnext(holder)
        holder.addnext(etree.fromstring(f'<w:p {WD}><w:pPr><w:pStyle w:val="ArchPara"/></w:pPr></w:p>'))
    empties = [f.text for f in findings(patch_docx(clean, {MAIN: after_box})) if f.kind == "empty"]
    assert any("(after ‘Text box holder:" in t for t in empties) and not any("Text box item" in t for t in empties)


def test_the_linked_level_finding(clean):
    """Item 9: list 10's paragraphs that take their level from a link are a list-level finding."""
    got = [f for f in findings(clean) if f.kind == "linked"]
    assert len(got) == 1 and "of list 10 take their level from a LINKED level" in got[0].text
    assert "LibreOffice shows them at level 0" in got[0].text
    assert not [f for f in findings(build_scheme_example()) if f.kind == "linked"]       # explicit levels there
