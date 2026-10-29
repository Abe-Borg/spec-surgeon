"""The structure reader's Canadian CSC PageFormat reading (docs/SPEC_FORMATTER_MODES_DESIGN.md N4): an automatic ".1"
takes its depth from the nearest placed paragraph of its list one level up; a typed ".1" carries no depth and stays
unresolved; the reading is off unless asked for, so Safe mode, the profile and template_roles read every file exactly
as before; a CSI document reads the same either way."""
from __future__ import annotations

import pytest

from make_fixtures import (build_canadian_outline, build_numbering, build_scheme_example, build_styled_outline,
                           build_typed, patch_docx)
from spec_surgeon.package import DocxPackage
from spec_surgeon.structure import (CANADIAN_NO_PARENT, CANADIAN_TYPED, analyze_structure, role_summary)


def roles(data: bytes, canadian: bool):
    return [(r["role"], r["disposition"], r["reason"]) for r in
            analyze_structure(DocxPackage(data), canadian=canadian)["paragraphs"]]


def test_automatic_pageformat_levels_take_their_depth_from_the_list():
    rows = analyze_structure(DocxPackage(build_canadian_outline()), canadian=True)["paragraphs"]
    assert [r["role"] for r in rows] == [
        "section_id", "section_title", "part", "article", "paragraph", "subparagraph", "subparagraph", "subparagraph_2",
        "paragraph", "article", "paragraph", "part", "article", "paragraph", "end_of_section"]
    assert [r["reference_id"] for r in rows[3:9]] == ["1.1", "1.1.1", "1.1.1.1", "1.1.1.2", "1.1.1.2.1", "1.1.2"]
    assert rows[5]["parent"] == 4 and rows[7]["parent"] == 6 and rows[8]["parent"] == 3
    assert all(r["disposition"] == "classified" for r in rows)
    assert all(r["hierarchy_reliable"] for r in rows if r["role"] not in (None, "end_of_section", "section_title"))


def test_the_default_reading_is_unchanged():
    data = build_canadian_outline()
    plain = analyze_structure(DocxPackage(data))["paragraphs"]
    assert [r["paragraph"] for r in plain if r["disposition"] == "unresolved"] == [4, 5, 6, 7, 8, 10, 13]
    assert role_summary(DocxPackage(data)) != role_summary(DocxPackage(data), canadian=True)
    assert role_summary(DocxPackage(data)) == role_summary(DocxPackage(data), canadian=False)


def test_role_summary_gives_a_selector_for_every_pageformat_level():
    summary = role_summary(DocxPackage(build_canadian_outline()), canadian=True)
    assert summary["selectors"]["paragraph"] == {"style_in": ["CaPara"]}
    assert summary["selectors"]["subparagraph"] == {"style_in": ["CaSub"]}
    assert summary["selectors"]["subparagraph_2"] == {"style_in": ["CaSub2"]}
    assert summary["unresolved"]["count"] == 0
    assert summary["roles"]["paragraph"]["markers"] == [".1", ".2"]


def test_a_typed_canadian_number_stays_unresolved_with_its_own_reason():
    rows = analyze_structure(DocxPackage(build_canadian_outline(typed=True)), canadian=True)["paragraphs"]
    dotted = [r for r in rows if r["text"].startswith(".")]
    assert len(dotted) == 7
    assert all(r["disposition"] == "unresolved" and r["reason"] == CANADIAN_TYPED for r in dotted)
    plain = analyze_structure(DocxPackage(build_canadian_outline(typed=True)))["paragraphs"]
    assert all(r["reason"] != CANADIAN_TYPED for r in plain)


def test_a_dot_number_with_no_placed_parent_one_level_up_stays_unresolved():
    """The article is taken out of the list (its own numId 0): the ".1" below it has no parent of its list."""
    from lxml import etree
    from spec_surgeon.package import W

    def unnumber_article(root):
        p = list(root.iter(W + "p"))[3]
        ppr = p.find(W + "pPr")
        numpr = etree.Element(W + "numPr")
        etree.SubElement(numpr, W + "ilvl").set(W + "val", "0")
        etree.SubElement(numpr, W + "numId").set(W + "val", "0")
        ppr.insert(1, numpr)
    bad = patch_docx(build_canadian_outline(), {"word/document.xml": unnumber_article})
    rows = analyze_structure(DocxPackage(bad), canadian=True)["paragraphs"]
    assert rows[4]["disposition"] == "unresolved" and rows[4]["reason"] == CANADIAN_NO_PARENT
    assert all(r["reason"] == CANADIAN_NO_PARENT for r in rows[5:9])
    # the list skips a level there, so its next article is not evaluated; PART 2's outline reads again
    assert [r["role"] for r in rows[11:14]] == ["part", "article", "paragraph"]


@pytest.mark.parametrize("make", [build_styled_outline, build_scheme_example, build_typed,
                                  lambda: build_numbering(True), lambda: build_numbering(False)])
def test_a_csi_document_reads_the_same_either_way(make):
    data = make()
    assert roles(data, False) == roles(data, True)
