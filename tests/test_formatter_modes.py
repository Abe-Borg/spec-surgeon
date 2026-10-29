"""The Specification Formatter's four modes as Spec Surgeon requests (docs/SPEC_FORMATTER_MODES_DESIGN.md N7), end to
end on synthetic documents: each plan is planned from the source, applied, and passes the independent checker; the
numbers each copy shows are the source's counters in the new scheme; no character is added.

* CSI to Canadian, automatic numbers, onto the bundled CSC PageFormat scheme;
* CSI to Canadian, typed numbers (typed_as "csi");
* Canadian back to CSI, onto the bundled CSI scheme: automatic numbers stay automatic, no literal marker is written;
* formatting to a template where the structure reader leaves a heading: read_outline's proposal, through the window
  (the gate, the preview, Create), restyles exactly the paragraph it chose.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from fake_claude import FakeClient, resp, tool
from make_fixtures import build, build_canadian_outline, build_outline_spec, build_styled_outline, build_typed
from spec_surgeon.document_task import DocumentTask
from spec_surgeon.editspec import apply_spec, plan_spec
from spec_surgeon.gui import Session
from spec_surgeon.ooxml import block_full_text, paragraphs
from spec_surgeon.ops import lists
from spec_surgeon.ops.selectors import StyleSheet
from spec_surgeon.package import DocxPackage
from spec_surgeon.schemes import levels, scheme_file
from spec_surgeon.verify import run_suite


def shown(data: bytes):
    """Each numbered paragraph's rendered number and its text, in order (the list model's own count)."""
    pkg = DocxPackage(data)
    root = pkg.xml(pkg.main_part())
    pars = paragraphs(root)
    return [(row.text, block_full_text(pars[row.index]).strip())
            for row in lists.count(root, StyleSheet(pkg), lists.ListDefs.load(pkg))]


def run(data: bytes, ops, mode="formatting"):
    spec = {"request": "r", "summary": "s", "mode": mode, "ops": ops}
    pkg = DocxPackage(data)
    plan, frags = plan_spec(pkg, spec)
    out = apply_spec(pkg, frags).output_bytes()
    rec = run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")
    assert rec.passed, rec.to_markdown()
    assert not plan.diffs or mode != "formatting"
    return out


def test_csi_automatic_numbers_onto_the_bundled_pageformat_scheme():
    out = run(build_styled_outline(), [{"op": "convert_numbering", "params": {
        "template": str(scheme_file("csc")), "levels": levels("csc")[:4], "counters": "keep"}}])
    assert shown(out) == [("PART 1", "GENERAL"), ("1.1", "SUMMARY"), (".1", shown(out)[2][1]), (".2", shown(out)[3][1]),
                          (".1", shown(out)[4][1]), ("1.2", "SUBMITTALS"), (".1", shown(out)[6][1]),
                          ("PART 2", "PRODUCTS"), ("2.1", "MATERIALS"), (".1", shown(out)[9][1])]


def test_typed_csi_numbers_onto_the_bundled_pageformat_scheme():
    src = build_typed(masterspec=True)
    out = run(src, [{"op": "convert_typed_numbers", "params": {
        "template": str(scheme_file("csc")), "levels": levels("csc")[:5], "typed_as": "csi", "match": "counters",
        "tracked": False}}], mode="untracked")
    got = dict((text, number) for number, text in shown(out))
    assert got["GENERAL"] == "PART 1" and got["SUMMARY"] == "1.1" and got["REFERENCES"] == "1.2"
    assert got["Section includes wet-pipe sprinkler systems."] == ".1"
    assert got["Hangers."] == ".1" and got["Supports."] == ".2"
    texts = [block_full_text(p) for p in paragraphs(DocxPackage(out).xml("word/document.xml"))]
    assert "GENERAL" in texts and not any(t.startswith(("PART 1", "A.", "1.1")) for t in texts)


def test_canadian_automatic_numbers_back_onto_the_bundled_csi_scheme():
    out = run(build_canadian_outline(), [{"op": "convert_numbering", "params": {
        "template": str(scheme_file("csi")), "levels": levels("csi")[:5], "counters": "keep"}}])
    assert [number for number, _ in shown(out)] == ["PART 1", "1.1", "A.", "1.", "2.", "a.", "B.", "1.2", "A.",
                                                    "PART 2", "2.1", "A."]
    before = [block_full_text(p) for p in paragraphs(DocxPackage(build_canadian_outline()).xml("word/document.xml"))]
    after = [block_full_text(p) for p in paragraphs(DocxPackage(out).xml("word/document.xml"))]
    assert after == before                                    # no literal marker: the words are untouched


def test_a_heading_the_reader_leaves_is_restyled_by_claudes_reading_through_the_window(tmp_path):
    """The reader leaves "GENERAL" (PART 1's title without "PART") and a revision log heading, and its typed readings of
    two requirement sentences go to Claude too; Claude's reading places "GENERAL" and leaves the rest out; the plan
    restyles paragraph 3 alone, through the gate, the preview and Create."""
    data = build_outline_spec()
    source = tmp_path / "docs" / "21 13 13 - WET-PIPE.docx"
    source.parent.mkdir()
    source.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    spec = {"request": "Make the PART headings Heading 1", "summary": "Restyle the PART headings", "mode": "formatting",
            "ops": [{"op": "apply_style", "params": {"style": "Heading1"},
                     "select": {"scope": ["document"], "paragraphs": {"indices_by_document": {digest: [3]}}}}]}
    fake = FakeClient(
        resp(tool("read_outline", {"file": source.name}, "o1")),
        resp(tool("submit_roles", {"paragraphs": [
            {"index": 2, "role": "none", "reason": "the revision log's heading"},
            {"index": 3, "role": "part", "reason": "PART 1's title"},
            {"index": 9, "role": "none", "reason": "requirement wording"},
            {"index": 10, "role": "none", "reason": "a cross-reference"}]}, "r1")),
        resp(tool("submit_editspec", {"spec": spec}, "s1")))
    task = DocumentTask(Session(client=fake, out_root=tmp_path / "out", visual_check=False), render=False)
    task.attach([source])
    view = task.perform("send", text="Make the PART headings Heading 1.")
    assert view["stage"] == "preview", view
    task.perform("primary", token=view["token"])
    assert task.stage == "done"
    out = Path(task.results[str(source)]).read_bytes()
    pkg = DocxPackage(out)
    styled = [i for i, p in enumerate(paragraphs(pkg.xml(pkg.main_part())))
              if StyleSheet(pkg).paragraph_style_id(p) == "Heading1"]
    assert styled == [3]
    assert source.read_bytes() == data


# ---- keeping each paragraph's look (styles "keep"), as the Formatter's template-free modes did ----------------------

def styles_of(data: bytes):
    pkg = DocxPackage(data)
    sheet = StyleSheet(pkg)
    return [sheet.paragraph_style_id(p) for p in paragraphs(pkg.xml(pkg.main_part()))]


def test_csi_to_pageformat_keeping_every_paragraphs_style():
    src = build_styled_outline()
    out = run(src, [{"op": "convert_numbering", "params": {
        "template": str(scheme_file("csc")), "levels": levels("csc")[:4], "styles": "keep", "counters": "keep"}}])
    assert styles_of(out) == styles_of(src)
    assert [n for n, _ in shown(out)] == ["PART 1", "1.1", ".1", ".2", ".1", "1.2", ".1", "PART 2", "2.1", ".1"]


def test_pageformat_to_csi_keeping_every_paragraphs_style_and_every_word():
    src = build_canadian_outline()
    out = run(src, [{"op": "convert_numbering", "params": {
        "template": str(scheme_file("csi")), "levels": levels("csi")[:5], "styles": "keep", "counters": "keep"}}])
    assert styles_of(out) == styles_of(src)
    assert [n for n, _ in shown(out)][:7] == ["PART 1", "1.1", "A.", "1.", "2.", "a.", "B."]
    assert [block_full_text(p) for p in paragraphs(DocxPackage(out).xml("word/document.xml"))] == \
        [block_full_text(p) for p in paragraphs(DocxPackage(src).xml("word/document.xml"))]


# ---- the worked examples ----------------------------------------------------------------------------------------

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "specs"


@pytest.mark.parametrize("name, make", [
    ("convert-numbering-to-pageformat.json", build_styled_outline),
    ("convert-typed-csi-to-pageformat.json", lambda: build_typed(masterspec=True)),
    ("convert-pageformat-to-csi.json", build_canadian_outline),
])
def test_the_worked_examples_plan_and_verify(name, make):
    from spec_surgeon.editspec import load_spec, validate_spec
    spec = load_spec(EXAMPLES / name)
    validate_spec(spec)
    src = make()
    plan, frags = plan_spec(DocxPackage(src), spec)
    out = apply_spec(DocxPackage(src), frags).output_bytes()
    assert run_suite(src, out, plan.to_dict(), "src.docx", "out.docx").passed


def test_the_outline_example_names_its_documents_by_digest():
    from spec_surgeon.editspec import load_spec, validate_spec
    from spec_surgeon.ops import JobException
    spec = load_spec(EXAMPLES / "restyle-outline-from-read-outline.json")
    validate_spec(spec)
    with pytest.raises(JobException) as e:                       # a document its table does not name is refused
        plan_spec(DocxPackage(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS")), spec)
    assert e.value.code == "not-outlined"
