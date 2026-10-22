"""Phase 1 ops on the hard-case fixture (make_fixtures.build_rich): every op passes the full suite end to end,
and for every op at least one planted defect — a wrong output or a wrong implementation — is caught."""
from __future__ import annotations

import io
import zipfile
from typing import List, Tuple

import pytest
from lxml import etree

from fx_revisions import tracked_column_resize
from make_fixtures import build_rich
from spec_surgeon import ooxml
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import set_font as set_font_module
from spec_surgeon.ops.remove_header_footer import RemoveHeaderFooter
from spec_surgeon.ops.selectors import DEFAULT_PRESERVE_FONTS
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.verify import run_suite

AUTHOR = "Abraham Borg"
EVERYWHERE = ["document", "headers", "footers"]


@pytest.fixture(scope="module")
def rich() -> bytes:
    return build_rich()


def run_ops(src: bytes, specs: List[Tuple[str, dict, dict]], extra_assertions=(), apply_with=None):
    """plan every op on the source, merge, apply in order on a working copy, verify. ``apply_with`` swaps in a
    (deliberately wrong) implementation for apply()."""
    pkg = DocxPackage(src)
    frags = [get_op(o).plan(pkg, sel, par) for o, sel, par in specs]
    plan = merge_fragments(frags)
    work = pkg.copy()
    for f in frags:
        (apply_with or get_op(f.op)).apply(work, f)
    out = work.output_bytes()
    d = plan.to_dict()
    d["property_assertions"] += list(extra_assertions)
    return plan, out, run_suite(src, out, d, "src.docx", "out.docx")


def failed(rec):
    return [c.id for c in rec.checks if c.status == "FAIL"]


def mutate(docx: bytes, part: str, fn) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(docx))
    root = etree.fromstring(zin.read(part))
    fn(root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            z.writestr(info, serialize_xml(root) if info.filename == part else zin.read(info.filename))
    return buf.getvalue()


def run_with_text(root, needle):
    return [r for r in root.iter(W + "r") if any(needle in (t.text or "") for t in r.iter(W + "t", W + "delText"))][0]


def rfonts(run):
    rf = run.find(W + "rPr/" + W + "rFonts")
    return {etree.QName(k).localname: v for k, v in rf.attrib.items()} if rf is not None else {}


# ---- remove_header_footer -----------------------------------------------------------------------------

def test_remove_all_headers_and_footers(rich):
    plan, out, rec = run_ops(rich, [("remove_header_footer", {}, {})])
    assert rec.passed, rec.to_markdown()
    pkg = DocxPackage(out)
    assert all(not s.header_refs and not s.footer_refs for s in pkg.sections())
    assert set(plan.members_removed) == {"word/header1.xml", "word/_rels/header1.xml.rels", "word/media/image1.png",
                                         "word/header2.xml", "word/_rels/header2.xml.rels", "word/header3.xml",
                                         "word/header4.xml", "word/footer1.xml", "word/footer2.xml"}
    assert pkg.has("word/media/image2.png")                 # still used by the body picture
    assert plan.rev_delta["ins"] == -1 and any("Jeremy" in f for f in plan.flags)   # pending insertion in a header
    assert not [p for p in pkg.content_types()[1] if "header" in p or "footer" in p]
    assert plan.geometry_reference == "source"
    assert "no longer text parts" in rec.get("V2").summary and "section_hf_refs" in rec.get("V10").summary


def test_remove_keeps_a_footer_part_another_section_still_uses(rich):
    plan, out, rec = run_ops(rich, [("remove_header_footer", {"sections": [0], "kinds": ["footer"], "types": ["default"]}, {})])
    assert rec.passed, rec.to_markdown()
    assert plan.members_removed == [] and plan.intended_parts == ["word/document.xml"]   # only the reference goes
    secs = DocxPackage(out).sections()
    assert "default" not in secs[0].footer_refs and secs[1].footer_refs["default"] == "word/footer2.xml"


def test_remove_cover_footers_flags_invisible_inheritance(rich):
    plan, out, rec = run_ops(rich, [("remove_header_footer", {"sections": [0], "kinds": ["footer"]}, {})])
    assert rec.passed, rec.to_markdown()
    assert plan.members_removed == ["word/footer1.xml"]
    assert any("no titlePg" in f for f in plan.flags)


def test_remove_refuses_when_inheritance_would_keep_or_change_a_visible_header(rich):
    with pytest.raises(JobException) as e:     # section 1 would fall back to section 0's default header
        get_op("remove_header_footer").plan(DocxPackage(rich), {"sections": [1], "kinds": ["header"]}, {})
    assert e.value.code == "inheritance"
    with pytest.raises(JobException) as e:     # section 2 (unselected) inherits section 1's default header
        get_op("remove_header_footer").plan(DocxPackage(rich), {"sections": [0, 1], "kinds": ["header"]}, {})
    assert e.value.code == "inheritance"


class _DropsSharedMedia(RemoveHeaderFooter):
    """Wrong: also deletes image2.png, which the body still shows."""
    def apply(self, pkg, frag):
        super().apply(pkg, frag)
        pkg.remove_member("word/media/image2.png")


class _ForgetsMedia(RemoveHeaderFooter):
    """Wrong in plan AND apply alike (no cross-check): never considers media — the logo is left orphaned."""
    def plan(self, pkg, select, params):
        frag = super().plan(pkg, select, params)
        frag.members_removed = [m for m in frag.members_removed if not m.startswith("word/media/")]
        frag.transform_data["members"] = list(frag.members_removed)
        frag.transform_data["media"] = []
        return frag

    def apply(self, pkg, frag):
        td = frag.transform_data
        root = pkg.xml(td["main"])
        for sp in DocxPackage.sect_prs(root):
            for el in list(sp):
                if el.tag in (W + "headerReference", W + "footerReference"):
                    sp.remove(el)
        pkg.write_xml(td["main"], root)
        pkg.edit_rels(td["main"], remove=td["rids"])
        for m in td["members"]:
            pkg.remove_member(m)


class _AlsoUnselected(RemoveHeaderFooter):
    """Wrong: removes the even-page header reference of section 1 too, though only default was selected."""
    def apply(self, pkg, frag):
        super().apply(pkg, frag)
        root = pkg.xml("word/document.xml")
        sp = DocxPackage.sect_prs(root)[1]
        for el in list(sp):
            if el.tag == W + "headerReference" and el.get(W + "type") == "even":
                sp.remove(el)
        pkg.write_xml("word/document.xml", root)


def test_remove_planted_defects(rich):
    _, _, rec = run_ops(rich, [("remove_header_footer", {}, {})], apply_with=_DropsSharedMedia())
    assert "V1" in failed(rec) and "dangling" in rec.get("V1").summary
    pkg = DocxPackage(rich)
    bad = _ForgetsMedia()
    frag = bad.plan(pkg, {}, {})
    plan = merge_fragments([frag])
    work = pkg.copy()
    bad.apply(work, frag)
    rec = run_suite(rich, work.output_bytes(), plan.to_dict(), "s", "o")
    assert "V1" in failed(rec) and "ORPHANED" in rec.get("V1").summary and "image1.png" in rec.get("V1").summary
    sel = {"sections": "all", "kinds": ["header", "footer"], "types": ["default", "first"]}
    _, _, rec = run_ops(rich, [("remove_header_footer", sel, {})])
    assert rec.passed, rec.to_markdown()
    _, _, rec = run_ops(rich, [("remove_header_footer", sel, {})], apply_with=_AlsoUnselected())
    assert "V10" in failed(rec)


# ---- set_font -----------------------------------------------------------------------------------------

def test_set_font_whole_document_plain(rich):
    plan, out, rec = run_ops(rich, [("set_font", {"scope": EVERYWHERE, "runs": "all"}, {"font": "Arial"})])
    assert rec.passed, rec.to_markdown()
    assert plan.params == {} and plan.ops[0]["params"]["tracked"] is False      # whole document → plain (§14.1)
    assert plan.geometry_reference == "none" and "reflow" in plan.geometry_reason
    root = DocxPackage(out).xml("word/document.xml")
    assert rfonts(run_with_text(root, "§")) == {"ascii": "Wingdings", "hAnsi": "Wingdings"}          # dingbat kept
    bullet = [r for r in root.iter(W + "r") if r.find(W + "rPr/" + W + "rStyle") is not None
              and r.find(W + "rPr/" + W + "rStyle").get(W + "val") == "BulletSymbol"][0]
    assert rfonts(bullet) == {}                                      # BulletSymbol style run: no direct font added
    sym = [r for r in root.iter(W + "r") if r.find(W + "sym") is not None][0]
    assert sym.find(W + "rPr") is None                               # w:sym run untouched
    assert rfonts(run_with_text(root, "FM Approved")) == {"ascii": "Arial", "hAnsi": "Arial", "cs": "Arial"}  # theme cleared
    assert rfonts(run_with_text(root, "black steel"))["ascii"] == "Arial"          # inside Jack's deletion
    assert any("w:rPrChange" in f for f in plan.flags)                             # Jeremy's older format change


def test_set_font_can_leave_revisions_alone(rich):
    plan, out, rec = run_ops(rich, [("set_font", {"runs": {"has_direct_font": True}},
                                     {"font": "Arial", "in_revisions": "exclude"})])
    assert rec.passed, rec.to_markdown()
    root = DocxPackage(out).xml("word/document.xml")
    assert rfonts(run_with_text(root, "black steel"))["ascii"] == "Arial Narrow"
    assert rfonts(run_with_text(root, "hot-dip"))["ascii"] == "Times New Roman"
    assert rfonts(run_with_text(root, "UL listed"))["ascii"] == "Arial"


def test_set_font_targeted_is_tracked_by_default(rich):
    plan, out, rec = run_ops(rich, [("set_font", {"paragraphs": {"style_in": ["Heading1"]}},
                                     {"font": "Arial", "author": AUTHOR})])
    assert rec.passed, rec.to_markdown()
    assert plan.rev_delta["rPrChange"] == 4 and plan.ops[0]["params"]["tracked"] is True
    root = DocxPackage(out).xml("word/document.xml")
    chg = run_with_text(root, "PART 2").find(W + "rPr/" + W + "rPrChange")
    assert chg is not None and chg.get(W + "author") == AUTHOR and len(chg.find(W + "rPr")) == 0
    with pytest.raises(JobException) as e:     # Jeremy's run already has a w:rPrChange
        get_op("set_font").plan(DocxPackage(rich), {"runs": "all"}, {"font": "Arial", "tracked": True})
    assert e.value.code == "tracked-format"


def test_set_font_planted_defects(rich):
    spec = [("set_font", {"scope": ["headers"], "runs": "all"}, {"font": "Arial"})]
    plan, out, rec = run_ops(rich, spec)
    assert rec.passed, rec.to_markdown()

    def body_run_changed(root):               # a run outside the selector (body, scope was headers)
        r = run_with_text(root, "UL listed")
        r.find(W + "rPr/" + W + "rFonts").set(W + "ascii", "Arial")
    rec2 = run_suite(rich, mutate(out, "word/document.xml", body_run_changed),
                     {**plan.to_dict(), "intended_parts": plan.intended_parts + ["word/document.xml"]}, "s", "o")
    assert failed(rec2) == ["V10"]

    plan, out, rec = run_ops(rich, [("set_font", {"runs": "all"}, {"font": "Arial"})])

    def symbol_run_changed(root):             # the dingbat turned into a letter
        run_with_text(root, "§").find(W + "rPr/" + W + "rFonts").set(W + "ascii", "Arial")
    rec3 = run_suite(rich, mutate(out, "word/document.xml", symbol_run_changed), plan.to_dict(), "s", "o")
    assert failed(rec3) == ["V10"]

    def theme_left(root):                     # explicit font set but the theme attribute still wins in Word
        run_with_text(root, "FM Approved").find(W + "rPr/" + W + "rFonts").set(W + "asciiTheme", "minorHAnsi")
    rec4 = run_suite(rich, mutate(out, "word/document.xml", theme_left), plan.to_dict(), "s", "o")
    assert failed(rec4) == ["V10"]

    plan, out, rec = run_ops(rich, [("set_font", {"paragraphs": {"style_in": ["Heading1"]}}, {"font": "Arial", "author": AUTHOR})])

    def bad_snapshot(root):                   # reject-all would not restore the original formatting
        chg = run_with_text(root, "PART 2").find(W + "rPr/" + W + "rPrChange/" + W + "rPr")
        etree.SubElement(chg, W + "b")
    rec5 = run_suite(rich, mutate(out, "word/document.xml", bad_snapshot), plan.to_dict(), "s", "o")
    assert "V10" in failed(rec5)


def test_set_font_selector_bug_shared_by_plan_and_apply_is_caught(rich, monkeypatch):
    """The transform's symbol detection is broken in plan AND apply alike (so the plan 'predicts' the damage).
    V10 resolves the selection independently: the counts disagree and the dingbat run changed unasserted."""
    monkeypatch.setattr(set_font_module, "is_symbol_rpr", lambda *a, **k: False)
    plan, out, rec = run_ops(rich, [("set_font", {"runs": "all"}, {"font": "Arial", "skip_sym_runs": False})])
    assert "V10" in failed(rec)
    assert "predicted" in rec.get("V10").summary


def test_effective_font_postcondition(rich):
    """'change all font to Arial' = set_font + set_style_font + set_doc_defaults_font; the effective-font assertion
    proves every non-symbol run and paragraph mark now resolves to Arial, and catches an incomplete composition."""
    eff = {"kind": "effective_font", "scope": EVERYWHERE, "slots": ["ascii", "hAnsi", "cs"], "value": "Arial",
           "preserve_fonts": DEFAULT_PRESERVE_FONTS, "include_paragraph_marks": True}
    full = [("set_font", {"scope": EVERYWHERE, "runs": {"has_direct_font": True}}, {"font": "Arial"}),
            ("set_style_font", {"styles": "all"}, {"font": "Arial"}),
            ("set_doc_defaults_font", {}, {"font": "Arial"})]
    plan, out, rec = run_ops(rich, full, extra_assertions=[eff])
    assert rec.passed, rec.to_markdown()
    assert "effective_font" in rec.get("V10").summary
    _, _, rec = run_ops(rich, full[1:], extra_assertions=[eff])          # forgot the direct run fonts
    assert failed(rec) == ["V10"] and "Arial Narrow" in str(rec.get("V10").data["errors"])


def test_effective_font_checks_text_edited_blocks(rich):
    """A replace_text edit makes its block's runs unpairable; the post-condition must still check every output run
    of that block (Codex review on PR #1: it used to skip text-edited blocks entirely)."""
    eff = {"kind": "effective_font", "scope": ["document"], "slots": ["ascii", "hAnsi", "cs"], "value": "Arial",
           "preserve_fonts": DEFAULT_PRESERVE_FONTS, "include_paragraph_marks": True}
    def fonts(first_block):          # set_font on direct-font runs from block `first_block` on, styles, docDefaults
        return [("set_font", {"runs": {"has_direct_font": True}, "paragraphs": {"index_range": [first_block, 99]}},
                 {"font": "Arial", "tracked": False}),
                ("set_style_font", {"styles": "all"}, {"font": "Arial"}),
                ("set_doc_defaults_font", {}, {"font": "Arial"})]
    # edit block 9 (no direct fonts); set_font covers every block with direct fonts: all text resolves to Arial
    ok = [("replace_text", {}, {"find": "Spacing set", "replace": "Spacing set exactly", "author": AUTHOR})] + fonts(0)
    _, _, rec = run_ops(rich, ok, extra_assertions=[eff])
    assert rec.passed, rec.to_markdown()
    # edit block 4, which keeps a direct "Arial Narrow" run (UL listed); set_font cannot cover it (it would
    # conflict with the text edit), so the post-condition must fail — and only because of that edited block
    bad = [("replace_text", {}, {"find": "per NFPA 13", "replace": "per NFPA 13 as adopted", "author": AUTHOR})] + fonts(5)
    _, _, rec = run_ops(rich, bad, extra_assertions=[eff])
    assert failed(rec) == ["V10"], rec.to_markdown()
    assert "block 4 (text-edited)" in str(rec.get("V10").data["errors"])


# ---- set_style_font / set_doc_defaults_font ------------------------------------------------------------

def test_set_style_font(rich):
    plan, out, rec = run_ops(rich, [("set_style_font", {"styles": "all"}, {"font": "Arial"})])
    assert rec.passed, rec.to_markdown()
    assert plan.intended_parts == ["word/styles.xml"]
    ids = plan.property_assertions[0]["expected_ids"]
    assert "Normal" in ids and "Heading1" in ids and "SpecNote" in ids and "BulletSymbol" not in ids
    assert any("stylesWithEffects" in f for f in plan.flags)
    root = DocxPackage(out).xml("word/styles.xml")
    st = {s.get(W + "styleId"): s for s in root.findall(W + "style")}
    h1 = st["Heading1"].find(W + "rPr/" + W + "rFonts")
    assert h1.get(W + "ascii") == "Arial" and h1.get(W + "asciiTheme") is None
    assert h1.get(W + "eastAsiaTheme") == "majorEastAsia"                    # eastAsia slot untouched
    assert st["BulletSymbol"].find(W + "rPr/" + W + "rFonts").get(W + "ascii") == "Wingdings"
    with pytest.raises(JobException):
        get_op("set_style_font").plan(DocxPackage(rich), {"styles": {"ids": ["NoSuchStyle"]}}, {"font": "Arial"})

    def symbol_style_changed(root):
        for s in root.findall(W + "style"):
            if s.get(W + "styleId") == "BulletSymbol":
                s.find(W + "rPr/" + W + "rFonts").set(W + "ascii", "Arial")
    rec2 = run_suite(rich, mutate(out, "word/styles.xml", symbol_style_changed), plan.to_dict(), "s", "o")
    assert failed(rec2) == ["V10"]


def test_set_doc_defaults_font(rich):
    plan, out, rec = run_ops(rich, [("set_doc_defaults_font", {}, {"font": "Arial"})])
    assert rec.passed, rec.to_markdown()
    rf = DocxPackage(out).xml("word/styles.xml").find(f"{W}docDefaults/{W}rPrDefault/{W}rPr/{W}rFonts")
    assert rf.get(W + "ascii") == "Arial" and rf.get(W + "hAnsiTheme") is None and rf.get(W + "eastAsiaTheme") == "minorEastAsia"

    def theme_left(root):
        root.find(f"{W}docDefaults/{W}rPrDefault/{W}rPr/{W}rFonts").set(W + "asciiTheme", "minorHAnsi")
    assert failed(run_suite(rich, mutate(out, "word/styles.xml", theme_left), plan.to_dict(), "s", "o")) == ["V10"]

    def ppr_default_touched(root):            # the paragraph defaults are not part of a font change
        root.find(f"{W}docDefaults/{W}pPrDefault/{W}pPr/{W}spacing").set(W + "after", "0")
    assert failed(run_suite(rich, mutate(out, "word/styles.xml", ppr_default_touched), plan.to_dict(), "s", "o")) == ["V10"]


# ---- set_paragraph_props ------------------------------------------------------------------------------

def test_set_paragraph_props_whole_document_merges(rich):
    plan, out, rec = run_ops(rich, [("set_paragraph_props", {}, {"spacing": {"after": 0}, "ind": {"firstLine": 0}, "jc": "both"})])
    assert rec.passed, rec.to_markdown()                     # V5a: pPr order incl. sectPr in section-break paragraphs
    assert plan.ops[0]["params"]["tracked"] is False
    root = DocxPackage(out).xml("word/document.xml")
    p_e = [p for p in root.iter(W + "p") if "Spacing set before" in "".join(t.text or "" for t in p.iter(W + "t"))][0]
    sp = p_e.find(W + "pPr/" + W + "spacing")
    assert sp.get(W + "before") == "240" and sp.get(W + "after") == "0"            # merged, not replaced
    p_f = [p for p in root.iter(W + "p") if "Hanging indent" in "".join(t.text or "" for t in p.iter(W + "t"))][0]
    ind = p_f.find(W + "pPr/" + W + "ind")
    assert ind.get(W + "hanging") is None and ind.get(W + "firstLine") == "0" and ind.get(W + "left") == "720"
    breaks = [p for p in root.iter(W + "p") if p.find(W + "pPr/" + W + "sectPr") is not None]
    assert breaks and all(list(p.find(W + "pPr"))[-1].tag == W + "sectPr" for p in breaks)

    def spacing_replaced(root):               # the lesson: replacing w:spacing loses the style's before=240
        p = [p for p in root.iter(W + "p") if "Spacing set before" in "".join(t.text or "" for t in p.iter(W + "t"))][0]
        del p.find(W + "pPr/" + W + "spacing").attrib[W + "before"]
    assert failed(run_suite(rich, mutate(out, "word/document.xml", spacing_replaced), plan.to_dict(), "s", "o")) == ["V10"]

    def sectpr_dropped(root):                 # rewriting a section-break paragraph's pPr must keep its sectPr
        p = [p for p in root.iter(W + "p") if p.find(W + "pPr/" + W + "sectPr") is not None][0]
        p.find(W + "pPr").remove(p.find(W + "pPr/" + W + "sectPr"))
    assert "V10" in failed(run_suite(rich, mutate(out, "word/document.xml", sectpr_dropped), plan.to_dict(), "s", "o"))


def test_set_paragraph_props_targeted_is_tracked(rich):
    sel = {"paragraphs": {"text_regex": r"^[A-F]\."}}
    plan, out, rec = run_ops(rich, [("set_paragraph_props", sel, {"spacing": {"after": 120}, "author": AUTHOR})])
    assert rec.passed, rec.to_markdown()
    assert plan.rev_delta["pPrChange"] == 5 and "pPrChange" in rec.get("V4").summary

    def bad_snapshot(root):
        chg = next(root.iter(W + "pPrChange"))
        chg.find(W + "pPr").append(etree.Element(W + "keepNext"))
    assert "V10" in failed(run_suite(rich, mutate(out, "word/document.xml", bad_snapshot), plan.to_dict(), "s", "o"))
    with pytest.raises(JobException):
        get_op("set_paragraph_props").plan(DocxPackage(rich), {}, {"ind": {"firstLine": 1, "hanging": 1}})


# ---- set_page_setup -----------------------------------------------------------------------------------

def test_set_page_setup(rich):
    plan, out, rec = run_ops(rich, [("set_page_setup", {"sections": [1, 2]},
                                     {"margins": {"left": 1440, "right": 1440}, "orientation": "landscape"})])
    assert rec.passed, rec.to_markdown()
    secs = DocxPackage.sect_prs(DocxPackage(out).xml("word/document.xml"))
    pg = secs[1].find(W + "pgSz")
    assert pg.get(W + "orient") == "landscape" and int(pg.get(W + "w")) > int(pg.get(W + "h"))
    assert secs[1].find(W + "pgMar").get(W + "left") == "1440"
    assert secs[0].find(W + "pgSz").get(W + "orient") is None                          # section 0 not selected
    assert secs[0].find(W + "pgMar").get(W + "left") == "1800"

    def unselected_changed(root):
        DocxPackage.sect_prs(root)[0].find(W + "pgMar").set(W + "left", "1000")
    assert failed(run_suite(rich, mutate(out, "word/document.xml", unselected_changed), plan.to_dict(), "s", "o")) == ["V10"]

    def gutter_dropped(root):
        del DocxPackage.sect_prs(root)[1].find(W + "pgMar").attrib[W + "gutter"]
    assert "V10" in failed(run_suite(rich, mutate(out, "word/document.xml", gutter_dropped), plan.to_dict(), "s", "o"))


# ---- composition ---------------------------------------------------------------------------------------

def test_conflicting_ops_are_refused(rich):
    pkg = DocxPackage(rich)
    frags = [get_op("remove_header_footer").plan(pkg, {}, {}),
             get_op("set_font").plan(pkg, {"scope": ["headers"]}, {"font": "Arial"})]
    with pytest.raises(JobException) as e:
        merge_fragments(frags)
    assert e.value.code == "conflict"


# ---- replace_text on the rich fixture -------------------------------------------------------------------

def test_replace_text_refuses_to_split_a_run_with_a_tracked_format_change(rich):
    """Splitting a run copies its rPr — a w:rPrChange would be duplicated under one w:id. v0.1.0 would have done
    it silently; the extended V4 (every revision kind) would now see it. The op refuses up front instead."""
    with pytest.raises(JobException) as e:
        get_op("replace_text").plan(DocxPackage(rich), {}, {"find": "Hangers", "replace": "Supports", "author": AUTHOR})
    assert e.value.code == "tracked-format"
    plan, out, rec = run_ops(rich, [("replace_text", {}, {"find": "Hangers", "replace": "Supports", "tracked": False})])
    assert rec.passed, rec.to_markdown()                  # untracked: the w:t is edited in place, no split
    plan, out, rec = run_ops(rich, [("replace_text", {"scope": ["document", "headers"]},
                                     {"find": "Schedule 40", "replace": "Schedule 10", "author": AUTHOR})])
    assert rec.passed, rec.to_markdown()


def test_new_revision_ids_start_above_a_tracked_column_resize():
    """New revision ids start above EVERY w:id of the file. The seed once counted a list of tags without
    ``w:tblGridChange``: the two tracked replacements here took ids 1–4, one of them the resize's own, and V4 set
    the file aside for a duplicate the engine itself wrote."""
    src = tracked_column_resize(1)
    assert ooxml.max_revision_id(DocxPackage(src)) == 1
    plan, out, rec = run_ops(src, [("replace_text", {}, {"find": "Owner", "replace": "Client", "author": AUTHOR})])
    assert rec.passed, rec.to_markdown()
    ids = [el.get(W + "id") for el in etree.fromstring(zipfile.ZipFile(io.BytesIO(out)).read("word/document.xml")).iter()
           if el.get(W + "id") is not None]
    assert sorted(ids, key=int) == ["1", "2", "3", "4", "5"]
