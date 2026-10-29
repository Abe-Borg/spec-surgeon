"""convert_numbering ``styles: "keep"`` (docs/SPEC_FORMATTER_MODES_DESIGN.md N5): the Specification Formatter's
template-free conversion changed only numbering, so a converted paragraph keeps its own style and its own numPr points
at the destination list's level. A CSI document (zero-padded articles, its own styles) onto a Canadian CSC PageFormat
example file, end to end plain and tracked; a second run changes nothing; the default stays the scheme's styles; and a
wrong output and a wrong implementation shared by plan and apply, each caught by V10 alone."""
from __future__ import annotations

import copy
import dataclasses
import io
import zipfile
from pathlib import Path
from typing import List

import pytest
from lxml import etree

from make_fixtures import build_canadian_outline, build_styled_outline
from spec_surgeon.ooxml import paragraphs
from spec_surgeon.ops import JobException, get_op, lists, merge_fragments
from spec_surgeon.ops import convert_numbering as convert_module
from spec_surgeon.ops.selectors import StyleSheet
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.verify import run_suite

LEVELS = ["CaPart", "CaArt", "CaPara", "CaSub"]
SOURCE_STYLES = ["Normal", "Normal", "SpPart", "SpArt", "SpPara", "SpPara", "SpSub", "SpArt", "SpPara", "SpPart",
                 "SpArt", "SpPara", "Normal"]
NUMBERS = [None, None, "PART 1", "1.1", ".1", ".2", ".1", "1.2", ".1", "PART 2", "2.1", ".1", None]
SOURCE_LEVELS = [None, None, 0, 1, 2, 2, 3, 1, 2, 0, 1, 2, None]


@pytest.fixture(scope="module")
def src() -> bytes:
    return build_styled_outline()


@pytest.fixture
def template(tmp_path) -> Path:
    path = tmp_path / "PageFormat scheme.docx"
    path.write_bytes(build_canadian_outline())
    return path


def params(template: Path, **extra) -> dict:
    return {"levels": LEVELS, "template": str(template), "counters": "keep", "styles": "keep", **extra}


def run(src: bytes, prm: dict):
    pkg = DocxPackage(src)
    frag = get_op("convert_numbering").plan(pkg, {}, prm)
    plan = merge_fragments([frag])
    work = pkg.copy()
    get_op("convert_numbering").apply(work, frag)
    out = work.output_bytes()
    return frag, plan, out, run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")


def failed(rec) -> List[str]:
    return [c.id for c in rec.checks if c.status == "FAIL"]


def v10(rec) -> str:
    c = next(c for c in rec.checks if c.id == "V10")
    return c.summary + " | " + " | ".join(c.data.get("errors", []))


def reading(data: bytes):
    """(style, rendered number, num id, own numPr (ilvl, numId) or None) of every body paragraph."""
    pkg = DocxPackage(data)
    sheet = StyleSheet(pkg)
    root = pkg.xml(pkg.main_part())
    numbered = {x.index: x for x in lists.count(root, sheet, lists.ListDefs.load(pkg))}
    out = []
    for i, p in enumerate(paragraphs(root)):
        n = numbered.get(i)
        numpr = p.find(f"{W}pPr/{W}numPr")
        own = None
        if numpr is not None:
            own = (numpr.find(W + "ilvl").get(W + "val"), numpr.find(W + "numId").get(W + "val"))
        out.append((sheet.paragraph_style_id(p), n.text if n else None, n.num_id if n else None, own))
    return out


def mutate(data: bytes, part: str, fn) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            payload = zin.read(info.filename)
            if info.filename == part:
                root = etree.fromstring(payload)
                fn(root)
                payload = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            zout.writestr(info, payload)
    return buf.getvalue()


@pytest.mark.parametrize("tracked", [False, True])
def test_each_paragraph_keeps_its_style_and_takes_the_pageformat_numbers(src, template, tracked):
    frag, plan, out, rec = run(src, params(template, tracked=tracked))
    assert rec.passed, (failed(rec), v10(rec))
    got = reading(out)
    assert [g[0] for g in got] == SOURCE_STYLES                       # every style as it was
    assert [g[1] for g in got] == NUMBERS                             # the PageFormat numbers
    dest = frag.transform_data["num_id"]
    assert all(g[2] == dest for g, n in zip(got, NUMBERS) if n)
    assert [g[3] for g in got] == [None if lv is None else (str(lv), str(dest)) for lv in SOURCE_LEVELS]
    assert plan.property_assertions[0]["expected_restyled"] == 0
    assert any("keep their own style" in f for f in frag.flags)
    assert any("stay unused" in n for n in frag.notes)
    assert ("word/document.xml", "2", "pStyle") not in frag.touches
    assert ("word/document.xml", "2", "numPr") in frag.touches
    if tracked:
        assert plan.rev_delta.get("pPrChange") == 10


def test_a_second_run_changes_nothing(src, template):
    _, _, out, _ = run(src, params(template))
    frag, plan, again, rec = run(out, params(template))
    assert rec.passed, (failed(rec), v10(rec))
    assert frag.transform_data["changed"] == []
    assert any("already as asked, left alone: 10" in n for n in frag.notes)


def test_the_default_is_the_schemes_styles_unchanged(src, template):
    prm = params(template)
    del prm["styles"]
    _, _, default_out, rec = run(src, prm)
    assert rec.passed
    _, _, scheme_out, _ = run(src, {**prm, "styles": "scheme"})
    assert default_out == scheme_out
    assert [g[0] for g in reading(default_out)][2:12] == ["CaPart", "CaArt", "CaPara", "CaPara", "CaSub", "CaArt",
                                                           "CaPara", "CaPart", "CaArt", "CaPara"]


def test_an_unknown_styles_value_is_refused(src, template):
    with pytest.raises(JobException) as e:
        get_op("convert_numbering").plan(DocxPackage(src), {}, params(template, styles="mine"))
    assert e.value.code == "bad-params"


def test_the_sentence_says_each_paragraph_keeps_its_style(template):
    words = get_op("convert_numbering").describe({}, params(template))
    assert "keeping each paragraph's own style" in words
    words = get_op("convert_numbering").describe({}, params(template, **{"from": {"styles": [["SpPart"], ["SpArt"]]}}))
    assert "are numbered at the level of the “CaPart” style" in words and "keeping each paragraph's own style" in words


# ---- a wrong output, a wrong implementation shared by plan and apply -----------------------------------------------

def test_a_paragraph_restyled_anyway_is_caught(src, template):
    _, plan, out, rec = run(src, params(template))
    assert rec.passed

    def restyle(root):
        p = list(root.iter(W + "p"))[4]
        p.find(f"{W}pPr/{W}pStyle").set(W + "val", "CaPara")
    bad = mutate(out, "word/document.xml", restyle)
    rec2 = run_suite(src, bad, plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec2) and "block 4" in v10(rec2)


def test_a_wrong_level_written_is_caught(src, template):
    _, plan, out, rec = run(src, params(template))
    assert rec.passed

    def wrong_level(root):
        p = list(root.iter(W + "p"))[4]
        p.find(f"{W}pPr/{W}numPr/{W}ilvl").set(W + "val", "3")
    bad = mutate(out, "word/document.xml", wrong_level)
    rec2 = run_suite(src, bad, plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec2)
    assert "block 4" in v10(rec2) or "the output's numbers are not the predicted ones" in v10(rec2)


def test_a_wrong_list_shared_by_plan_and_apply_is_caught(src, template, monkeypatch):
    """Plan and apply both point the paragraphs at the document's OWN old list (num 20) instead of the imported one:
    their numbers agree with each other; the verifier resolves the destination itself."""
    real = convert_module.destination

    def wrong(*a, **k):
        dest = real(*a, **k)
        return dataclasses.replace(dest, scheme=dataclasses.replace(dest.scheme, num_id=20))
    monkeypatch.setattr(convert_module, "destination", wrong)
    try:
        frag, plan, out, rec = run(src, params(template, counters="flag"))
    except JobException:
        pytest.skip("the wrong list is refused at planning already")
    assert frag.transform_data["num_id"] == 20
    assert "V10" in failed(rec)
    assert "the destination is list" in v10(rec)
