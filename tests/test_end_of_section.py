"""ensure_end_of_section (docs/BUILTIN_OPS_BATCH1_DESIGN.md §6): every file ends with its own END OF SECTION line —
nothing to do, a wrong line corrected, a missing one inserted (after a final table; with a style; with the title's
formatting), the refusals, and planted defects caught by the verifier."""
from __future__ import annotations

from typing import Any, Dict

import pytest
from lxml import etree

from make_fixtures import build_batch1
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import end_of_section as eos_module
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.verify import run_suite
from test_text_edits import accepted_text, failed, mutate, v10_errors

AUTHOR = "Abraham Borg"
NUMBER = {"rule": "regex", "source": "document", "pattern": r"^SECTION\s+(\d{2} \d{2} \d{2})"}


def run_op(src: bytes, params: Dict[str, Any]):
    pkg = DocxPackage(src)
    frag = get_op("ensure_end_of_section").plan(pkg, {}, {"author": AUTHOR, "number": NUMBER, **params})
    plan = merge_fragments([frag])
    work = pkg.copy()
    get_op("ensure_end_of_section").apply(work, frag)
    out = work.output_bytes()
    return plan, out, run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")


def body_lines(data: bytes):
    body = DocxPackage(data).xml("word/document.xml").find(W + "body")
    return [(c.tag.split("}")[1], accepted_text(c) if c.tag == W + "p" else "") for c in body if c.tag != W + "sectPr"]


def test_a_file_that_already_ends_right_is_left_alone():
    src = build_batch1(eos="right")
    plan, out, rec = run_op(src, {})
    assert rec.passed and out == src and "already ends with" in plan.notes[-1]


@pytest.mark.parametrize("tracked", [True, False])
def test_a_wrong_line_is_corrected(tracked):
    src = build_batch1(eos="wrong")
    plan, out, rec = run_op(src, {"tracked": tracked})
    assert rec.passed, rec.to_markdown()
    assert ("p", "END OF SECTION 21 13 19") in body_lines(out)
    assert ("p", "End of Section 21 13 13") not in body_lines(out)


@pytest.mark.parametrize("tracked", [True, False])
def test_a_missing_line_goes_after_the_final_table(tracked):
    src = build_batch1(eos="missing", ends_with_table=True)
    plan, out, rec = run_op(src, {"tracked": tracked})
    assert rec.passed, rec.to_markdown()
    lines = body_lines(out)
    k = [n for n, (tag, _) in enumerate(lines) if tag == "tbl"][-1]
    assert lines[k + 1] == ("p", "END OF SECTION 21 13 19")


def test_inserted_with_the_files_own_style_or_the_titles_formatting():
    src = build_batch1(eos="styled")
    plan, out, rec = run_op(src, {"style": "EOS", "tracked": False})
    assert rec.passed
    new = [p for p in DocxPackage(out).xml("word/document.xml").iter(W + "p")
           if accepted_text(p) == "END OF SECTION 21 13 19"][0]
    assert new.find(f"{W}pPr/{W}pStyle").get(W + "val") == "EOS"
    with pytest.raises(JobException) as e:
        run_op(build_batch1(eos="missing"), {"style": "EOS"})               # this file has no such style
    assert e.value.code == "missing-style"

    def bold_title(root):
        for p in root.iter(W + "p"):
            if accepted_text(p).startswith("SECTION "):
                ppr = p.find(W + "pPr")
                if ppr is None:
                    ppr = etree.Element(W + "pPr")
                    p.insert(0, ppr)
                etree.SubElement(ppr, W + "jc").set(W + "val", "center")
                r = p.find(W + "r")
                rpr = etree.Element(W + "rPr")
                etree.SubElement(rpr, W + "b")
                r.insert(0, rpr)
    src = mutate(build_batch1(eos="missing"), "word/document.xml", bold_title)
    plan, out, rec = run_op(src, {"formatting": "copy", "copy_from_regex": "^SECTION ", "tracked": True})
    assert rec.passed, rec.to_markdown()
    new = [p for p in DocxPackage(out).xml("word/document.xml").iter(W + "p")
           if accepted_text(p) == "END OF SECTION 21 13 19"][0]
    assert new.find(f"{W}pPr/{W}jc").get(W + "val") == "center" and new.find(f".//{W}r/{W}rPr/{W}b") is not None


def test_refusals():
    with pytest.raises(JobException) as e:
        run_op(build_batch1(eos="early"), {})
    assert e.value.code == "eos-not-last"
    with pytest.raises(JobException) as e:
        run_op(build_batch1(eos="missing"), {"number": {"rule": "regex", "source": "document", "pattern": "^NOPE (\\d+)"}})
    assert e.value.code == "not-found"
    for params in ({"text": "END OF SECTION"}, {"text": "{number} {number}"}, {"formatting": "copy"},
                   {"number": "21 13 19"}, {"detect_regex": "("}):
        with pytest.raises(JobException) as e:
            run_op(build_batch1(eos="missing"), params)
        assert e.value.code == "bad-params"


# ---- planted defects ---------------------------------------------------------------------------------------------

def test_defect_a_typo_in_the_inserted_number():
    src = build_batch1(eos="missing")
    plan, out, rec = run_op(src, {"tracked": False})

    def typo(root):
        for t in root.iter(W + "t"):
            if t.text == "END OF SECTION 21 13 19":
                t.text = "END OF SECTION 21 13 18"
    rec = run_suite(src, mutate(out, "word/document.xml", typo), plan.to_dict(), "s", "o")
    assert "V2" in failed(rec) and "V10" in failed(rec)


def test_defect_the_inserted_line_gets_formatting_of_its_own():
    src = build_batch1(eos="missing")
    plan, out, rec = run_op(src, {"tracked": False})

    def bold(root):
        for r in root.iter(W + "r"):
            if any(t.text == "END OF SECTION 21 13 19" for t in r.iter(W + "t")):
                rpr = etree.Element(W + "rPr")
                etree.SubElement(rpr, W + "b")
                r.insert(0, rpr)
    rec = run_suite(src, mutate(out, "word/document.xml", bold), plan.to_dict(), "s", "o")
    assert failed(rec) == ["V10"]


def test_shared_defect_the_number_from_somewhere_else(monkeypatch):
    monkeypatch.setattr(eos_module, "extract_fill", lambda pkg, name, rule: "21 13 13")
    plan, out, rec = run_op(build_batch1(eos="missing"), {"tracked": False})
    assert ("p", "END OF SECTION 21 13 13") in body_lines(out)                 # plan and apply agree …
    assert failed(rec) == ["V10"]                                              # … the verifier reads 21 13 19
    assert any("21 13 19" in e for e in v10_errors(rec))


def test_shared_defect_a_final_table_is_overlooked(monkeypatch):
    monkeypatch.setattr(eos_module, "last_block",                        # the last paragraph with text, tables ignored
                        lambda body: [c for c in body if c.tag == W + "p" and accepted_text(c).strip()][-1])
    plan, out, rec = run_op(build_batch1(eos="missing", ends_with_table=True), {"tracked": False})
    lines = body_lines(out)
    assert lines.index(("p", "END OF SECTION 21 13 19")) < [n for n, (t, _) in enumerate(lines) if t == "tbl"][-1]
    assert failed(rec) == ["V10"]


def test_shared_defect_copied_formatting_keeps_the_numbering(monkeypatch):
    def number_title(root):
        for p in root.iter(W + "p"):
            if accepted_text(p).startswith("SECTION "):
                ppr = p.find(W + "pPr")
                if ppr is None:
                    ppr = etree.Element(W + "pPr")
                    p.insert(0, ppr)
                num = etree.SubElement(ppr, W + "numPr")
                etree.SubElement(num, W + "ilvl").set(W + "val", "0")
                etree.SubElement(num, W + "numId").set(W + "val", "1")
    src = mutate(build_batch1(eos="missing"), "word/document.xml", number_title)
    plan, out, rec = run_op(src, {"formatting": "copy", "copy_from_regex": "^SECTION ", "tracked": False})
    assert rec.passed
    monkeypatch.setattr(eos_module, "copied_ppr", eos_module.inherited_ppr)
    plan, out, rec = run_op(src, {"formatting": "copy", "copy_from_regex": "^SECTION ", "tracked": False})
    assert failed(rec) == ["V10"]
