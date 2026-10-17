"""Built-in batch 4's profile additions (docs/BUILTIN_OPS_BATCH4_DESIGN.md §9): what Claude sees about borders and
shading, the character and table styles in use, the proofing languages and a template's styles — descriptive only."""
from __future__ import annotations

from lxml import etree

from make_fixtures import W, build_style_template, build_styles, patch_docx
from spec_surgeon.package import DocxPackage
from spec_surgeon.profile import bound, profile, template_spec_summary


def _prof(data: bytes):
    return profile(DocxPackage(data, name="spec.docx"))


def _style(root, sid):
    return next(s for s in root.findall(W + "style") if s.get(W + "styleId") == sid)


def test_styles_show_their_border_box_and_shading():
    styles = {s["id"]: s for s in _prof(build_styles())["styles_in_use"]}
    assert styles["ArchNote"]["borders"] == ["top", "left", "bottom", "right"]
    assert styles["ArchNote"]["shading"] == "D9D9D9"
    assert styles["ArchNoteChild"]["borders"] == ["top", "left", "right"]      # its own bottom nil wins


def test_a_theme_fill_is_reported_as_the_theme_colour():
    """Codex on #31: a w:shd with w:themeFill and no cached w:fill is shading Word renders."""
    def edit(root):
        shd = _style(root, "ArchNote").find(f"{W}pPr/{W}shd")
        del shd.attrib[W + "fill"]
        shd.set(W + "themeFill", "accent1")
        shd.set(W + "themeFillTint", "33")
    styles = {s["id"]: s for s in _prof(patch_docx(build_styles(), {"word/styles.xml": edit}))["styles_in_use"]}
    assert styles["ArchNote"]["shading"] == "accent1 tint 33"


def test_character_and_table_styles_in_use_cover_every_part_and_marks_but_no_snapshot():
    """Codex on #31: map_styles works over every text part and on paragraph marks; a tracked change's snapshot is what
    reject-all would restore, not what the file shows."""
    def edit_header(root):
        p = root.find(f".//{W}p")
        r = etree.SubElement(p, W + "r")
        rpr = etree.SubElement(r, W + "rPr")
        etree.SubElement(rpr, W + "rStyle").set(W + "val", "ArchUnusedChar")
        etree.SubElement(r, W + "t").text = "header words"

    def edit_doc(root):
        p = root.findall(f".//{W}body/{W}p")[3]
        ppr = p.find(W + "pPr")
        mark = etree.SubElement(ppr, W + "rPr")
        etree.SubElement(mark, W + "rStyle").set(W + "val", "ArchLinkedChar")
        r = p.find(W + "r")
        rpr = r.find(W + "rPr") if r.find(W + "rPr") is not None else etree.SubElement(r, W + "rPr")
        chg = etree.SubElement(rpr, W + "rPrChange")
        chg.set(W + "id", "900"); chg.set(W + "author", "Jeremy")
        snap = etree.SubElement(chg, W + "rPr")
        etree.SubElement(snap, W + "rStyle").set(W + "val", "SubtleEmphasis")
    data = patch_docx(build_styles(), {"word/header1.xml": edit_header, "word/document.xml": edit_doc})
    prof = _prof(data)
    chars = {e["id"] for e in prof["character_styles_in_use"]}
    assert {"ArchUnusedChar", "ArchLinkedChar", "ArchStrong", "Heading1Char"} <= chars
    assert "SubtleEmphasis" not in chars
    assert [e["id"] for e in prof["table_styles_in_use"]] == ["ArchTable"]


def test_languages_leave_out_tracked_snapshots():
    """Codex on #31: a w:pPrChange snapshot's paragraph-mark language is not the file's."""
    def edit(root):
        chg = root.find(f".//{W}pPrChange/{W}pPr")
        rpr = etree.SubElement(chg, W + "rPr")
        etree.SubElement(rpr, W + "lang").set(W + "val", "de-DE")
    langs = _prof(patch_docx(build_styles(), {"word/document.xml": edit}))["markup"]["languages"]
    assert langs["default"] == "en-GB" and langs["styles"] == {"en-GB": 1}
    assert langs["direct"] == {"en-GB": 3, "fr-FR": 1}


def test_the_bound_thins_the_new_style_lists_before_dropping_everything():
    """Codex on #31: long character/table style lists must be trimmed by the staged truncation, not push the profile
    into the last-resort branch."""
    prof = _prof(build_styles())
    prof["character_styles_in_use"] = [{"id": f"CharStyle{i:03d}", "count": i, "name": "A long character style name " * 3}
                                       for i in range(80)]
    prof["table_styles_in_use"] = [{"id": f"TableStyle{i:03d}", "count": i, "name": "A long table style name " * 3}
                                   for i in range(80)]
    out = bound(prof, limit=6000)
    assert "character_styles_in_use" in out and len(out["character_styles_in_use"]) == 4
    assert "name" not in out["character_styles_in_use"][0]
    assert any("character style(s) in use" in t for t in out["truncated"])


def test_a_template_lists_the_styles_it_offers():
    summary = template_spec_summary(DocxPackage(build_style_template(), name="OFFICE STYLES.docx"))
    rows = {s["id"]: s for s in summary["styles"]}
    assert rows["PRT"]["numbered"] is True and rows["PR2"]["basedOn"] == "PR1"
    assert rows["MyBody"]["name"] == "Body Text" and rows["TblMine"]["type"] == "table"
