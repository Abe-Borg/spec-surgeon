"""audit_numbering and V10's ``unnumbered_empty`` kind (built-in batch 5, item 36 — docs/BUILTIN_OPS_BATCH5_DESIGN.md
§4): the report on the architect's outline (clean, and with a shared definition), on typed numbering (with a duplicate
"B."), on the example scheme (nothing to report) and on crafted sequences (gap, duplicate, goes back, starts at); the typed
grammar; the fix (``unnumber_empty``) end to end through the full suite, plain and tracked, a blank line of no-break
spaces and a bookmark; wrong OUTPUTS and wrong IMPLEMENTATIONS shared by plan and apply (a blank rule that forgets the
no-break space, a fix that removes the whole numPr, a count that skips hidden paragraphs); refusals; plan-time conflicts
(rule 9); the batch pipeline; the catalog and the worked example."""
from __future__ import annotations

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
    assert "list 22 — 1 paragraph(s), first ‘Legal three’: skipped level" in flags
    assert "list 22 (first ‘Appendix one’) sets a restart rule (w:lvlRestart) on level(s) [2, 3]: LibreOffice" in flags
    k = kinds(clean)
    assert ("empty", EMPTY_C) in k and ("hidden", 14) in k and ("double", 37) in k
    outline = [f for f in findings(clean) if f.kind in ("gap", "duplicate", "back", "start") and f.index < 45]
    assert outline == []                                       # the main list reads as it should
    assert any(n.startswith("numbering audit: 4 list(s) number 51 counted paragraph(s) (49 evaluated)") for n in plan.notes)
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
    assert any("24 typed number(s) checked; 1 finding(s): 1 duplicate" in n for n in plan.notes)


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
    assert ("gap", 12) in got                       # "1.5 inch pipe." reads as a typed n.n (advisory)
    assert len(got) == 5
    texts = {f.index: f.text for f in findings(data)}
    assert texts[3] == "numbering: ‘C. Three.’ shows C. after A. (‘A. One.’): a number is skipped"
    assert texts[6] == "numbering: ‘B. Starts late.’ starts its sequence at B."


def test_the_typed_grammar():
    cases = {"PART 1 - GENERAL": ("PART <1> -", 1), "PART 12 GENERAL": ("PART <1>", 12),
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
                     "other than numPr changed"),
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
