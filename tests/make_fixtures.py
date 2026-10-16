"""Build synthetic spec-like .docx fixtures that exercise the engine's hard cases:

* cover section with its own footer carrying "<section> - {PAGE}" and a body section restarting at 1
* a header with an issue stamp
* tracked changes already present (an insertion by Jeremy with w16du:dateUtc, a deletion by Jack)
* a target phrase that sits inside Jeremy's pending insertion (nested-revision case)
* a target phrase split across two identically formatted runs (paragraph-local merge case)
* a tab inside a run, a REF-style field, a textbox-free but table-bearing cover
* trackRevisions ON

Usage:  python tests/make_fixtures.py OUT_DIR [--count N] [--rich | --template]

``build_rich()`` (``--rich``) is the NL-editor op fixture: first/even headers, a shared footer part, an inheriting
section, header-only and shared media, theme + explicit fonts, symbol runs/styles, runs inside revisions.

``build_template()`` (``--template``) is the Phase 2 header/footer template (both slot conventions); ``TEMPLATE_FILLS``
are its fill rules. ``build_reference_spec()`` (``--reference``) is an ordinary spec from another firm used as a
template spec (reference slots); ``REFERENCE_FILLS`` fit it and build() targets. ``build_sized()`` is the size fixture
of the font ops (9 pt text, 8 pt headers, a 12 pt note, symbol runs with sizes). ``build()``'s keyword options make templating targets: ``footer_revision`` (Jeremy's pending
insertion in the body footer), ``side_margin_twips``, ``header_distance_twips``, ``footer_distance_twips``,
``body_picture`` (a drawing whose id the template's logo must not reuse).
"""
from __future__ import annotations

import argparse
import copy
import io
import struct
import sys
import zipfile
import zlib
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt
from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = "{%s}" % W_NS
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
W16DU = "http://schemas.microsoft.com/office/word/2023/wordml/word16du"


def png(rgb=(200, 30, 30), size=4) -> bytes:
    """A tiny solid-colour PNG (different colours → different bytes → different media parts)."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _page_field(paragraph):
    r1 = paragraph.add_run()
    fc = OxmlElement("w:fldChar"); fc.set(qn("w:fldCharType"), "begin"); r1._r.append(fc)
    r2 = paragraph.add_run()
    it = OxmlElement("w:instrText"); it.set(qn("xml:space"), "preserve"); it.text = " PAGE "; r2._r.append(it)
    r3 = paragraph.add_run()
    fc = OxmlElement("w:fldChar"); fc.set(qn("w:fldCharType"), "separate"); r3._r.append(fc)
    r4 = paragraph.add_run("1")
    r5 = paragraph.add_run()
    fc = OxmlElement("w:fldChar"); fc.set(qn("w:fldCharType"), "end"); r5._r.append(fc)


def _footer(section, section_number: str):
    section.footer.is_linked_to_previous = False
    p = section.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p.add_run(f"{section_number} - ")
    _page_field(p)


def _header(section, stamp: str):
    section.header.is_linked_to_previous = False
    p = section.header.paragraphs[0]
    p.add_run("Microsoft — AVC11\t")
    p.add_run(stamp)


def _set_pgnum_start(section, start: int):
    sp = section._sectPr
    for old in sp.findall(qn("w:pgNumType")):     # add_section copies the previous sectPr, restart included
        sp.remove(old)
    pg = OxmlElement("w:pgNumType")
    pg.set(qn("w:start"), str(start))
    cols = sp.find(qn("w:cols"))
    if cols is not None:
        cols.addprevious(pg)
    else:
        sp.append(pg)


def build(section_number: str, title: str, stamp: str = "ISSUED FOR CONSTRUCTION  2026-01-01",
          with_cover_page_field: bool = True, single_section: bool = False, footer_revision: bool = False,
          side_margin_twips: int | None = None, header_distance_twips: int | None = None,
          footer_distance_twips: int | None = None, body_picture: bool = False,
          body_footer_linked: bool = False, appendix_restart: bool = False) -> bytes:
    """``body_footer_linked``: the body section has no footer of its own ("Link to Previous"), so Word shows the
    cover's footer — and its PAGE field — on every body page. ``appendix_restart``: a third section (an appendix)
    that restarts page numbering at 1 again, like the body section does."""
    doc = Document()
    st = doc.styles["Normal"]; st.font.name = "Arial"; st.font.size = Pt(10)

    cover = doc.sections[0]
    _header(cover, stamp)
    if with_cover_page_field:
        _footer(cover, section_number)
    else:
        cover.footer.is_linked_to_previous = False
        cover.footer.paragraphs[0].add_run("Cover")

    doc.add_paragraph(f"SECTION {section_number}")
    doc.add_paragraph(title)
    doc.add_paragraph("PROJECT REVISION HISTORY")
    t = doc.add_table(rows=2, cols=3)
    t.cell(0, 0).text = "Rev"; t.cell(0, 1).text = "Date"; t.cell(0, 2).text = "Description"
    t.cell(1, 0).text = "0"; t.cell(1, 1).text = "2026-01-01"; t.cell(1, 2).text = "Issued for Construction"

    if not single_section:
        body = doc.add_section(WD_SECTION.NEW_PAGE)
        body.header.is_linked_to_previous = True
        if body_footer_linked:
            body.footer.is_linked_to_previous = True      # no footerReference: the cover's footer part is shown
        else:
            _footer(body, section_number)
        if footer_revision:                     # Jeremy's pending insertion in the body footer
            body.footer.paragraphs[0].add_run("INS: (rev)")
        _set_pgnum_start(body, 1)
    if side_margin_twips is not None:
        from docx.shared import Twips
        for sec in doc.sections:
            sec.left_margin = sec.right_margin = Twips(side_margin_twips)
    if header_distance_twips is not None or footer_distance_twips is not None:
        from docx.shared import Twips
        for sec in doc.sections:
            if header_distance_twips is not None:
                sec.header_distance = Twips(header_distance_twips)
            if footer_distance_twips is not None:
                sec.footer_distance = Twips(footer_distance_twips)
    if body_picture:                            # a body drawing whose wp:docPr id the template's logo must not reuse
        doc.add_paragraph().add_run().add_picture(io.BytesIO(png((30, 30, 200))), width=Pt(20))

    doc.add_paragraph("GENERAL")
    doc.add_paragraph("1.01\tSUMMARY")
    p = doc.add_paragraph()
    p.add_run("A.\t")
    p.add_run("The Owner's insurer is FM Global and all equipment shall be FM Approved.")
    p = doc.add_paragraph()
    p.add_run("B.\tThe auditor is FM ")
    p.add_run("Global and reviews the hydraulic calculations.")        # target spans two runs
    p = doc.add_paragraph()
    p.add_run("C.\tSprinklers: ")
    p.add_run("INS:UL listed and FM Approved per the Owner's insurer FM Global requirements")  # pending insertion (Jeremy)
    p.add_run(" as indicated.")
    p = doc.add_paragraph()
    p.add_run("D.\tPipe shall be ")
    p.add_run("DEL:black steel ")                                      # pending deletion (Jack)
    p.add_run("Schedule 40 per NFPA 13 (2025).")
    p = doc.add_paragraph()
    p.add_run("E.\tSee Section ")
    _ref_field(p)
    p.add_run(" for FM Global coordination.")
    for i in range(1, 30):
        doc.add_paragraph(f"{i + 1}.01\tArticle {i} body text for {section_number}. " * 3)
    if appendix_restart and not single_section:
        appendix = doc.add_section(WD_SECTION.NEW_PAGE)   # headers/footers linked to previous; its own restart
        _set_pgnum_start(appendix, 1)
        doc.add_paragraph("APPENDIX A - HYDRAULIC CALCULATIONS")
        doc.add_paragraph("Attached.")

    settings = doc.settings.element
    tr = OxmlElement("w:trackRevisions")
    settings.insert(0, tr)

    buf = io.BytesIO(); doc.save(buf)
    return _post_process(buf.getvalue())


def _ref_field(paragraph):
    r1 = paragraph.add_run()
    fc = OxmlElement("w:fldChar"); fc.set(qn("w:fldCharType"), "begin"); r1._r.append(fc)
    r2 = paragraph.add_run()
    it = OxmlElement("w:instrText"); it.set(qn("xml:space"), "preserve"); it.text = ' REF _Ref123 \\h '; r2._r.append(it)
    r3 = paragraph.add_run()
    fc = OxmlElement("w:fldChar"); fc.set(qn("w:fldCharType"), "separate"); r3._r.append(fc)
    paragraph.add_run("21 10 00")
    r5 = paragraph.add_run()
    fc = OxmlElement("w:fldChar"); fc.set(qn("w:fldCharType"), "end"); r5._r.append(fc)


def _post_process(data: bytes) -> bytes:
    """Turn INS:/DEL: marker runs into real tracked changes (in the body and in header/footer parts)."""
    zin = zipfile.ZipFile(io.BytesIO(data))
    rid = 100
    changed = {}
    for name in zin.namelist():
        if not (name == "word/document.xml" or (name.startswith("word/") and ("header" in name or "footer" in name)
                                                and name.endswith(".xml") and "/_rels/" not in name)):
            continue
        root = etree.fromstring(zin.read(name))
        rid = _mark_revisions(root, rid)
        changed[name] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            payload = changed.get(info.filename, None) or zin.read(info.filename)
            zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(zi, payload)
    return out.getvalue()


def _mark_revisions(root, rid: int) -> int:
    for r in list(root.iter(W + "r")):
        t = r.find(W + "t")
        if t is None or not t.text:
            continue
        if t.text.startswith("INS:"):
            t.text = t.text[4:]
            ins = etree.Element(W + "ins")
            ins.set(W + "id", str(rid)); rid += 1
            ins.set(W + "author", "Jeremy"); ins.set(W + "date", "2026-09-22T10:15:00Z")
            ins.set("{%s}dateUtc" % W16DU, "2026-09-22T17:15:00Z")
            r.addprevious(ins); ins.append(r)
        elif t.text.startswith("DEL:"):
            t.text = t.text[4:]
            t.tag = W + "delText"; t.set(XML_SPACE, "preserve")
            d = etree.Element(W + "del")
            d.set(W + "id", str(rid)); rid += 1
            d.set(W + "author", "Jack"); d.set(W + "date", "2026-09-23T08:00:00Z")
            r.addprevious(d); d.append(r)
    return rid


# ---- the NL-editor op fixture ---------------------------------------------------------------------------

R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _rev(tag: str, run, rid: int, author: str, date: str = "2026-09-22T10:15:00Z"):
    """Wrap a python-docx run in a w:ins / w:del (deleted text becomes w:delText)."""
    el = OxmlElement(f"w:{tag}")
    el.set(qn("w:id"), str(rid)); el.set(qn("w:author"), author); el.set(qn("w:date"), date)
    r = run._r
    if tag == "del":
        for t in r.findall(qn("w:t")):
            t.tag = qn("w:delText"); t.set(qn("xml:space"), "preserve")
    r.addprevious(el); el.append(r)
    return el


def _rfonts(run, **attrs):
    rpr = run._r.get_or_add_rPr()
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        rf = OxmlElement("w:rFonts")
        rstyle = rpr.find(qn("w:rStyle"))
        if rstyle is not None:
            rstyle.addnext(rf)
        else:
            rpr.insert(0, rf)
    for k, v in attrs.items():
        rf.set(qn(f"w:{k}"), v)


def build_rich(section_number: str = "21 13 13", title: str = "WET-PIPE SPRINKLER SYSTEMS") -> bytes:
    """Hard cases for the formatting/structure ops (remove_header_footer, set_font, set_style_font,
    set_doc_defaults_font, set_paragraph_props, set_page_setup):

    * section 0 (cover): titlePg; its own first-page header (with a header-only red logo) and footer, its own
      default header (blue image also used in the body; a pending insertion by Jeremy) and default footer;
    * section 1 (body): its own default header and even-page header (evenAndOddHeaders on), and a default footer
      reference to the SAME part as section 0's default footer (shared footer part);
    * section 2: no references at all — inherits everything from section 1;
    * fonts: docDefaults use theme fonts (minorHAnsi), Normal is explicit Times New Roman, Heading 1 uses theme major
      fonts, a SpecNote paragraph style (Courier New), a BulletSymbol character style (Wingdings), runs with direct
      explicit fonts, a direct theme-font run, a direct Wingdings run, a w:sym run, runs inside Jeremy's insertion
      and Jack's deletion with direct fonts, a run already carrying a w:rPrChange, a paragraph mark with a direct font;
    * paragraphs with existing spacing (before=240) and a hanging indent, a table, an empty paragraph;
    * trackRevisions on.
    """
    from docx.enum.style import WD_STYLE_TYPE
    doc = Document()
    styles = doc.styles
    styles["Normal"].font.name = "Times New Roman"
    bullet = styles.add_style("BulletSymbol", WD_STYLE_TYPE.CHARACTER)
    bullet.font.name = "Wingdings"
    note = styles.add_style("SpecNote", WD_STYLE_TYPE.PARAGRAPH)
    note.base_style = styles["Normal"]
    note.font.name = "Courier New"

    s0 = doc.sections[0]
    s0.different_first_page_header_footer = True
    fh = s0.first_page_header; fh.is_linked_to_previous = False
    fh.paragraphs[0].add_run(f"COVER {section_number} ")
    fh.paragraphs[0].add_run().add_picture(io.BytesIO(png((200, 30, 30))), width=Pt(12))      # header-only logo
    ff = s0.first_page_footer; ff.is_linked_to_previous = False
    ff.paragraphs[0].add_run(f"{section_number} COVER - ")
    _page_field(ff.paragraphs[0])
    dh = s0.header; dh.is_linked_to_previous = False
    dh.paragraphs[0].add_run("Microsoft — AVC11\t")
    rev_run = dh.paragraphs[0].add_run("REV A ")
    dh.paragraphs[0].add_run("ISSUED FOR CONSTRUCTION ")
    dh.paragraphs[0].add_run().add_picture(io.BytesIO(png((30, 30, 200))), width=Pt(12))      # shared with body
    _rev("ins", rev_run, 300, "Jeremy")
    df = s0.footer; df.is_linked_to_previous = False
    df.paragraphs[0].add_run(f"{section_number} - ")
    _page_field(df.paragraphs[0])

    doc.add_paragraph(f"SECTION {section_number}", style="Title")
    doc.add_paragraph(title)

    s1 = doc.add_section(WD_SECTION.NEW_PAGE)
    s1.different_first_page_header_footer = False
    bh = s1.header; bh.is_linked_to_previous = False
    bh.paragraphs[0].add_run(f"{section_number}  {title}")
    eh = s1.even_page_header; eh.is_linked_to_previous = False
    eh.paragraphs[0].add_run("EVEN PAGE HEADER")
    doc.settings.odd_and_even_pages_header_footer = True
    # section 1's default footer: a reference to section 0's default footer part (shared part)
    s0 = doc.sections[0]       # add_section moved the cover's sectPr into a paragraph clone: re-fetch
    fref0 = [e for e in s0._sectPr.findall(qn("w:footerReference")) if e.get(qn("w:type")) == "default"][0]
    shared = OxmlElement("w:footerReference")
    shared.set(qn("w:type"), "default"); shared.set(qn("r:id"), fref0.get(qn("r:id")))
    refs = s1._sectPr.findall(qn("w:headerReference")) + s1._sectPr.findall(qn("w:footerReference"))
    refs[-1].addnext(shared)

    doc.add_paragraph("PART 1 - GENERAL", style="Heading 1")
    p = doc.add_paragraph()
    p.add_run("A.\tSprinklers shall be ")
    _rfonts(p.add_run("UL listed"), ascii="Arial Narrow", hAnsi="Arial Narrow")
    _rfonts(p.add_run(" and FM Approved"), asciiTheme="minorHAnsi", hAnsiTheme="minorHAnsi", cstheme="minorBidi")
    p.add_run(" per NFPA 13 (2025).")
    p = doc.add_paragraph()
    b = p.add_run("l"); b.style = "BulletSymbol"                                   # bullet via a symbol style
    _rfonts(p.add_run("§"), ascii="Wingdings", hAnsi="Wingdings")                  # direct dingbat
    sym_run = p.add_run()
    sym = OxmlElement("w:sym"); sym.set(qn("w:font"), "Symbol"); sym.set(qn("w:char"), "F0B7"); sym_run._r.append(sym)
    p.add_run("\tB.\tPipe shall be ")
    ins_run = p.add_run("hot-dip galvanized ")
    _rfonts(ins_run, ascii="Times New Roman", hAnsi="Times New Roman")
    _rev("ins", ins_run, 301, "Jeremy")
    del_run = p.add_run("black steel ")
    _rfonts(del_run, ascii="Arial Narrow", hAnsi="Arial Narrow")
    _rev("del", del_run, 302, "Jack", "2026-09-23T08:00:00Z")
    p.add_run("Schedule 40.")
    p = doc.add_paragraph()
    fmt = p.add_run("C.\tHangers formatted earlier by Jeremy.")
    fmt.bold = True
    chg = OxmlElement("w:rPrChange")
    chg.set(qn("w:id"), "303"); chg.set(qn("w:author"), "Jeremy"); chg.set(qn("w:date"), "2026-09-22T11:00:00Z")
    chg.append(OxmlElement("w:rPr"))
    fmt._r.rPr.append(chg)
    doc.add_paragraph("NOTE: coordinate with the Owner's insurer.", style="SpecNote")
    p = doc.add_paragraph("D.\tParagraph mark carries a direct font.")
    mark = OxmlElement("w:rPr"); mrf = OxmlElement("w:rFonts")
    mrf.set(qn("w:ascii"), "Arial Narrow"); mrf.set(qn("w:hAnsi"), "Arial Narrow"); mark.append(mrf)
    p._p.get_or_add_pPr().append(mark)
    p = doc.add_paragraph("E.\tSpacing set before.")
    p.paragraph_format.space_before = Pt(12)
    p = doc.add_paragraph("F.\tHanging indent paragraph.")
    p.paragraph_format.left_indent = Pt(36); p.paragraph_format.first_line_indent = Pt(-18)
    t = doc.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "Item"; t.cell(0, 1).text = "Value"
    t.cell(1, 0).text = "Pressure"; t.cell(1, 1).text = "175 psi"
    doc.add_paragraph("")
    doc.add_picture(io.BytesIO(png((30, 30, 200))), width=Pt(12))                  # same bytes as the header image

    doc.add_section(WD_SECTION.NEW_PAGE)                                          # section 2: inherits everything
    doc.add_paragraph("PART 2 - PRODUCTS", style="Heading 1")
    for i in range(1, 6):
        doc.add_paragraph(f"2.0{i}\tProduct article {i} for {section_number}. " * 2)

    settings = doc.settings.element
    settings.insert(0, OxmlElement("w:trackRevisions"))
    buf = io.BytesIO(); doc.save(buf)
    return buf.getvalue()


# ---- the Phase 2 template fixture -------------------------------------------------------------------

TEMPLATE_FILLS = {
    "XX XX XX": {"rule": "regex", "source": "footer.default", "pattern": r"^(\d{2} \d{2} \d{2})"},
    "SECTION_NUMBER": {"rule": "regex", "source": "footer.default", "pattern": r"^(\d{2} \d{2} \d{2})"},
    "SECTION_TITLE": {"rule": "paragraph_after", "source": "document", "anchor_regex": r"^SECTION \d{2} \d{2} \d{2}$"},
    "ISSUED FOR PERMIT": {"rule": "literal", "value": "ISSUED FOR CONSTRUCTION  2026-02-15"},
}


def _highlight(run, color="yellow"):
    rpr = run._r.get_or_add_rPr()
    hl = OxmlElement("w:highlight"); hl.set(qn("w:val"), color)
    rpr.append(hl)


def _ptab(paragraph, kind: str, pos: int):
    ppr = paragraph._p.get_or_add_pPr()
    tabs = ppr.find(qn("w:tabs"))
    if tabs is None:
        tabs = OxmlElement("w:tabs")
        ppr.insert(0 if ppr.find(qn("w:pStyle")) is None else 1, tabs)
    t = OxmlElement("w:tab"); t.set(qn("w:val"), kind); t.set(qn("w:pos"), str(pos)); tabs.append(t)


def build_template() -> bytes:
    """A project header/footer template (the 00_00_00 TEMPLATE file of the playbook), both slot conventions:

    * section 0 (cover): header "MICROSOFT — AVC11 DATA CENTER<tab>[ISSUED FOR PERMIT]" (highlighted slot, character
      style HFStamp that targets lack; direct tabs clear the style's center stop and set a right stop at the
      template's text width) + a header-only logo;
      footer "SECTION [XX][ XX][ XX]" — one highlighted slot fragmented over three runs;
    * section 1 (body): header "{{SECTION_" + "NUMBER}} – {{SECTION_TITLE}}<tab>[ISSUED FOR PERMIT]" (a braces slot
      fragmented over two runs; tab stops from the Header style); footer "{{SECTION_NUMBER}} - <PAGE>" (a braces slot
      inside a run with other text) and a 1×2 table "© Owner | {{SECTION_NUMBER}}";
    * 1-inch side margins (text width 9360 twips; targets have 8640), Header style 8 pt, Footer style 7 pt with
      12 pt before — so the transplant needs tab rescaling and pinned properties.
    """
    from docx.enum.style import WD_STYLE_TYPE
    from docx.shared import Inches
    doc = Document()
    styles = doc.styles
    styles["Normal"].font.name = "Arial"; styles["Normal"].font.size = Pt(10)
    styles["Header"].font.size = Pt(8)
    styles["Footer"].font.size = Pt(7)
    styles["Footer"].paragraph_format.space_before = Pt(6)
    stamp = styles.add_style("HFStamp", WD_STYLE_TYPE.CHARACTER)
    stamp.font.bold = True; stamp.font.name = "Arial Narrow"

    s0 = doc.sections[0]
    s0.left_margin = s0.right_margin = Inches(1)
    h = s0.header; h.is_linked_to_previous = False
    p = h.paragraphs[0]
    p.add_run("MICROSOFT — AVC11 DATA CENTER")
    p.add_run("\t")
    r = p.add_run("ISSUED FOR PERMIT"); r.style = "HFStamp"; _highlight(r)
    _ptab(p, "clear", 4680)                              # the stamp goes to the right stop, not the style's center
    _ptab(p, "right", 9360)
    h.add_paragraph().add_run().add_picture(io.BytesIO(png((200, 30, 30))), width=Pt(14))   # template-only logo
    f = s0.footer; f.is_linked_to_previous = False
    p = f.paragraphs[0]
    p.add_run("SECTION ")
    for frag in ("XX", " XX", " XX"):                   # rsid churn: one placeholder, three runs
        _highlight(p.add_run(frag))
    doc.add_paragraph("SECTION XX XX XX")
    doc.add_paragraph("SECTION TITLE")

    s1 = doc.add_section(WD_SECTION.NEW_PAGE)
    s1.left_margin = s1.right_margin = Inches(1)
    h = s1.header; h.is_linked_to_previous = False
    p = h.paragraphs[0]
    p.add_run("{{SECTION_"); p.add_run("NUMBER}}")      # one placeholder, two runs
    p.add_run(" – {{SECTION_TITLE}}")
    p.add_run("\t")
    r = p.add_run("ISSUED FOR PERMIT"); r.style = "HFStamp"; _highlight(r)
    f = s1.footer; f.is_linked_to_previous = False
    p = f.paragraphs[0]
    p.add_run("{{SECTION_NUMBER}} - ")
    _page_field(p)
    t = f.add_table(rows=1, cols=2, width=Inches(6.5))
    t.cell(0, 0).text = "© Owner"
    t.cell(0, 1).text = "{{SECTION_NUMBER}}"
    f.add_paragraph("")
    doc.add_paragraph("PART 1 - GENERAL")
    buf = io.BytesIO(); doc.save(buf)
    return buf.getvalue()


def _styleref_field(paragraph, result: str):
    """A STYLEREF field whose cached result shows ``result`` (an architect's header showing the title by field)."""
    r1 = paragraph.add_run()
    fc = OxmlElement("w:fldChar"); fc.set(qn("w:fldCharType"), "begin"); r1._r.append(fc)
    r2 = paragraph.add_run()
    it = OxmlElement("w:instrText"); it.set(qn("xml:space"), "preserve"); it.text = ' STYLEREF "Title" '; r2._r.append(it)
    r3 = paragraph.add_run()
    fc = OxmlElement("w:fldChar"); fc.set(qn("w:fldCharType"), "separate"); r3._r.append(fc)
    paragraph.add_run(result)
    r5 = paragraph.add_run()
    fc = OxmlElement("w:fldChar"); fc.set(qn("w:fldCharType"), "end"); r5._r.append(fc)


def build_reference_spec(section_number: str = "08 71 00", title: str = "DOOR HARDWARE", *,
                         header_title: str | None = None, title_by_field: bool = False,
                         extra_header_text: str = "", number_body: str | None = None,
                         body_font: str = "Arial", body_size: float = 10) -> bytes:
    """An ordinary spec from another firm, used as the TEMPLATE SPEC (reference slots): nothing is highlighted and
    there are no {{placeholders}} — its own section number and title are simply typed into its header and footer.

    * single section, 1-inch side margins (text width 9360; build() targets have 8640), Normal Arial 10 pt
      (``body_font``/``body_size``), Header 8 pt;
    * header: "ACME ARCHITECTS<tab>AVC11 DATA CENTER – 100% CONSTRUCTION DOCUMENTS" and
      "SECTION 08 71 00 – DOOR HARDWARE" (``header_title`` changes how the title is written there, ``title_by_field``
      shows it through a STYLEREF field, ``extra_header_text`` adds a third line);
    * footer: "DOOR HARDWARE<tab>08 71 00 - <PAGE>";
    * body: "SECTION 08 71 00" (or ``number_body``), the title, then PART 1 - GENERAL.
    """
    from docx.shared import Inches
    doc = Document()
    styles = doc.styles
    styles["Normal"].font.name = body_font; styles["Normal"].font.size = Pt(body_size)
    styles["Header"].font.size = Pt(8)
    s0 = doc.sections[0]
    s0.left_margin = s0.right_margin = Inches(1)
    h = s0.header; h.is_linked_to_previous = False
    p = h.paragraphs[0]
    p.add_run("ACME ARCHITECTS\tAVC11 DATA CENTER – 100% CONSTRUCTION DOCUMENTS")
    p = h.add_paragraph()
    p.add_run(f"SECTION {section_number} – ")
    if title_by_field:
        _styleref_field(p, header_title or title)
    else:
        p.add_run(header_title or title)
    if extra_header_text:
        h.add_paragraph(extra_header_text)
    f = s0.footer; f.is_linked_to_previous = False
    p = f.paragraphs[0]
    p.add_run(f"{title}\t{section_number} - ")
    _page_field(p)
    doc.add_paragraph(f"SECTION {number_body or section_number}")
    doc.add_paragraph(title)
    doc.add_paragraph("PART 1 - GENERAL")
    doc.add_paragraph("1.1\tSUMMARY")
    doc.add_paragraph("A.\tSection includes door hardware for swinging doors.")
    buf = io.BytesIO(); doc.save(buf)
    return buf.getvalue()


# Fill rules that fit build() targets AND build_reference_spec() (the template spec's own values come from the same
# rules run on the template itself).
REFERENCE_FILLS = {
    "SECTION_NUMBER": {"rule": "regex", "source": "document", "pattern": r"^SECTION\s+(\d{2}\s?\d{2}\s?\d{2})\b"},
    "SECTION_TITLE": {"rule": "paragraph_after", "source": "document", "anchor_regex": r"^SECTION\s+\d{2}\s?\d{2}\s?\d{2}$"},
}


def _sz(run_or_rpr, half_points: int, cs: bool = False):
    """Direct w:sz (and w:szCs) on a python-docx run, or on an rPr element."""
    rpr = run_or_rpr._r.get_or_add_rPr() if hasattr(run_or_rpr, "_r") else run_or_rpr
    for tag in ("w:sz",) + (("w:szCs",) if cs else ()):
        el = OxmlElement(tag); el.set(qn("w:val"), str(half_points)); rpr.append(el)


def build_sized(section_number: str = "21 13 13", title: str = "WET-PIPE SPRINKLER SYSTEMS") -> bytes:
    """A discipline spec set in 9 pt with 8 pt headers — the size cases of the font ops ("match the template's font:
    Arial 10"):

    * docDefaults 9 pt; Normal Century Gothic 9 pt; Heading 1 9 pt bold; Header/Footer 8 pt; a SpecNote style 8 pt;
      a BulletSymbol character style (Wingdings, 9 pt);
    * body runs with their own size: "UL listed" (Arial Narrow, 9 pt with a w:szCs 9 pt), "pasted at 9 pt" (no direct
      font), "deliberately 12 pt", a direct Wingdings "§" at 9 pt, a w:sym run at 9 pt; a paragraph mark at 9 pt; a
      plain run inheriting 9 pt; a table;
    * header: the section line with a direct 8 pt run; footer: page number.
    """
    from docx.enum.style import WD_STYLE_TYPE
    doc = Document()
    styles = doc.styles
    sroot = styles.element
    dd = sroot.find(qn("w:docDefaults")).find(qn("w:rPrDefault")).find(qn("w:rPr"))
    for old in dd.findall(qn("w:sz")) + dd.findall(qn("w:szCs")):
        dd.remove(old)
    _sz(dd, 18)
    styles["Normal"].font.name = "Century Gothic"; styles["Normal"].font.size = Pt(9)
    styles["Heading 1"].font.size = Pt(9); styles["Heading 1"].font.bold = True
    styles["Header"].font.size = Pt(8); styles["Footer"].font.size = Pt(8)
    note = styles.add_style("SpecNote", WD_STYLE_TYPE.PARAGRAPH)
    note.base_style = styles["Normal"]; note.font.size = Pt(8)
    bullet = styles.add_style("BulletSymbol", WD_STYLE_TYPE.CHARACTER)
    bullet.font.name = "Wingdings"; bullet.font.size = Pt(9)
    s0 = doc.sections[0]
    h = s0.header; h.is_linked_to_previous = False
    hp = h.paragraphs[0]; hp.style = styles["Header"]
    _sz(hp.add_run(f"SECTION {section_number}"), 16)
    f = s0.footer; f.is_linked_to_previous = False
    fp = f.paragraphs[0]; fp.style = styles["Footer"]
    fp.add_run(f"{section_number} - ")
    _page_field(fp)
    doc.add_paragraph(f"SECTION {section_number}")
    doc.add_paragraph(title)
    doc.add_paragraph("PART 1 - GENERAL", style="Heading 1")
    p = doc.add_paragraph()
    p.add_run("A.\tSprinklers shall be ")
    ul = p.add_run("UL listed"); _rfonts(ul, ascii="Arial Narrow", hAnsi="Arial Narrow"); _sz(ul, 18, cs=True)
    p.add_run(" per NFPA 13 (2025).")
    p = doc.add_paragraph()
    _sz(p.add_run("B.\tText pasted at 9 pt."), 18)
    _sz(p.add_run(" A note deliberately 12 pt."), 24)
    p = doc.add_paragraph()
    b = p.add_run("l"); b.style = "BulletSymbol"
    dingbat = p.add_run("§"); _rfonts(dingbat, ascii="Wingdings", hAnsi="Wingdings"); _sz(dingbat, 18)
    sym_run = p.add_run()
    sym = OxmlElement("w:sym"); sym.set(qn("w:font"), "Symbol"); sym.set(qn("w:char"), "F0B7"); sym_run._r.append(sym)
    _sz(sym_run, 18)
    p.add_run("\tC.\tPlain text inheriting 9 pt.")
    p = doc.add_paragraph("D.\tParagraph mark at 9 pt.")
    mark = OxmlElement("w:rPr"); _sz(mark, 18); p._p.get_or_add_pPr().append(mark)
    doc.add_paragraph("NOTE: coordinate with the Owner's insurer.", style="SpecNote")
    t = doc.add_table(rows=1, cols=2)
    t.cell(0, 0).text = "Pressure"; t.cell(0, 1).text = "175 psi"
    buf = io.BytesIO(); doc.save(buf)
    return buf.getvalue()


SPECS = [
    ("21 05 00", "COMMON WORK RESULTS FOR FIRE SUPPRESSION"),
    ("21 10 00", "WATER-BASED FIRE-SUPPRESSION SYSTEMS"),
    ("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"),
    ("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS"),
    ("21 13 19", "PREACTION SPRINKLER SYSTEMS"),
    ("21 22 00", "CLEAN-AGENT FIRE-EXTINGUISHING SYSTEMS"),
    ("28 31 00", "FIRE DETECTION AND ALARM"),
    ("21 05 13", "COMMON MOTOR REQUIREMENTS FOR FIRE-SUPPRESSION EQUIPMENT"),
]


def build_formatted(section_number: str = "21 13 13", title: str = "WET-PIPE SPRINKLER SYSTEMS") -> bytes:
    """The set_properties fixture (docs/SET_PROPERTIES_DESIGN.md §7).

    Styles: ``SpecPart`` (the PART headings — plain: no bold, no keep-with-next, 11 pt), ``ArticleTitle`` (the article
    titles, plain), ``SpecSub`` (based on SpecPart, its own red colour), ``SpecSubPlain`` (based on SpecPart, inherits),
    ``SpecNoteItalic`` (italic through the style), ``SpecStrong`` (a bold character style).
    Body: PART 1 plain; PART 2 with a SpecStrong run (bold at a second style level once SpecPart turns bold); PART 3
    with "EXECUTION" explicitly not bold (a direct override); PART 4 as a complex-script run (w:cs); article titles in
    mixed case — one already uppercase, one with a theme-coloured run, one with a Wingdings bullet and a w:sym; body
    text with a direct italic run and an italic-style note; an underlined run with an underline colour; a paragraph
    already keep-with-next; a run bolded earlier under tracked changes (w:rPrChange); a SpecSub and a SpecSubPlain
    heading; a table; the header paragraph in SpecPart; trackRevisions on.
    """
    from docx.enum.style import WD_STYLE_TYPE
    from docx.shared import RGBColor
    doc = Document()
    styles = doc.styles
    styles["Normal"].font.name = "Arial"
    styles["Normal"].font.size = Pt(10)
    part = styles.add_style("SpecPart", WD_STYLE_TYPE.PARAGRAPH)
    part.base_style = styles["Normal"]
    part.font.size = Pt(11)
    art = styles.add_style("ArticleTitle", WD_STYLE_TYPE.PARAGRAPH)
    art.base_style = styles["Normal"]
    sub = styles.add_style("SpecSub", WD_STYLE_TYPE.PARAGRAPH)
    sub.base_style = part
    sub.font.color.rgb = RGBColor(0xFF, 0x00, 0x00)
    sub_plain = styles.add_style("SpecSubPlain", WD_STYLE_TYPE.PARAGRAPH)
    sub_plain.base_style = part
    note = styles.add_style("SpecNoteItalic", WD_STYLE_TYPE.PARAGRAPH)
    note.base_style = styles["Normal"]
    note.font.italic = True
    strong = styles.add_style("SpecStrong", WD_STYLE_TYPE.CHARACTER)
    strong.font.bold = True

    hp = doc.sections[0].header.paragraphs[0]
    hp.style = part
    hp.add_run(f"SECTION {section_number}")                                        # the heading style in a header
    doc.add_paragraph(f"SECTION {section_number}")
    doc.add_paragraph(title)
    doc.add_paragraph("PART 1 - GENERAL", style="SpecPart")
    doc.add_paragraph("1.01\tSUMMARY", style="ArticleTitle")                        # already typed in capitals
    p = doc.add_paragraph(style="ArticleTitle")
    p.add_run("1.02\tReferences ")
    r = p.add_run("(see list)")
    r.font.color.rgb = RGBColor(0x1F, 0x49, 0x7D)
    r._r.rPr.find(qn("w:color")).set(qn("w:themeColor"), "text2")                  # a theme colour over the value
    p = doc.add_paragraph(style="ArticleTitle")
    p.add_run("1.03\t")
    _rfonts(p.add_run("l"), ascii="Wingdings", hAnsi="Wingdings")                   # a bullet glyph: never capitalised
    sym_run = p.add_run()
    sym = OxmlElement("w:sym"); sym.set(qn("w:font"), "Symbol"); sym.set(qn("w:char"), "F0B7"); sym_run._r.append(sym)
    p.add_run(" Submittals")
    p = doc.add_paragraph()
    p.add_run("A.\tProvide hangers ")
    r = p.add_run("per NFPA 13")
    r.italic = True                                                                  # direct italic (w:i only)
    p.add_run(" (2025).")
    doc.add_paragraph("B.\tNote in the italic style.", style="SpecNoteItalic")
    p = doc.add_paragraph()
    p.add_run("C.\tUnderlined ")
    r = p.add_run("red underline")
    r.underline = True
    r._r.rPr.find(qn("w:u")).set(qn("w:color"), "FF0000")                           # an underline colour to keep
    p = doc.add_paragraph("D.\tKeep with next already.")
    p.paragraph_format.keep_with_next = True
    p = doc.add_paragraph()
    fmt = p.add_run("E.\tHangers formatted earlier by Jeremy.")
    fmt.bold = True
    chg = OxmlElement("w:rPrChange")
    chg.set(qn("w:id"), "303"); chg.set(qn("w:author"), "Jeremy"); chg.set(qn("w:date"), "2026-09-22T11:00:00Z")
    chg.append(OxmlElement("w:rPr"))
    fmt._r.rPr.append(chg)
    p = doc.add_paragraph("PART 2 - PRODUCTS", style="SpecPart")
    r = p.add_run(" (strong)")
    r.style = styles["SpecStrong"]                                                   # bold at a second style level
    p = doc.add_paragraph(style="SpecPart")
    p.add_run("PART 3 - ")
    r = p.add_run("EXECUTION")
    r.bold = False                                                                   # a direct override
    p = doc.add_paragraph(style="SpecPart")
    r = p.add_run("PART 4 - CLOSEOUT")
    r.font.complex_script = True                                                     # w:cs: weight comes from w:bCs
    doc.add_paragraph("Sub heading in red", style="SpecSub")
    doc.add_paragraph("Sub heading that inherits", style="SpecSubPlain")
    t = doc.add_table(rows=1, cols=2)
    t.cell(0, 0).text = "Item"; t.cell(0, 1).text = "Value"

    settings = doc.settings.element
    settings.insert(0, OxmlElement("w:trackRevisions"))
    buf = io.BytesIO(); doc.save(buf)
    return buf.getvalue()


# ---- built-in batch 1 (docs/BUILTIN_OPS_BATCH1_DESIGN.md) ------------------------------------------------------------

BATCH1_TAGS = ("WET", "DRY", "PREACTION", "WET/DRY", "CLEAN AGENT")


def _hyperlink(paragraph, text: str, url: str = "https://example.com/owner"):
    part = paragraph.part
    rid = part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                         is_external=True)
    h = OxmlElement("w:hyperlink"); h.set(qn("r:id"), rid)
    r = OxmlElement("w:r"); t = OxmlElement("w:t"); t.text = text; r.append(t); h.append(r)
    paragraph._p.append(h)


def _textbox_run(paragraph, text: str):
    """A run holding a text box (DrawingML + VML fallback, as Word writes it): the text exists twice."""
    xml = (
        '<w:r xmlns:w="%s" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
        'xmlns:v="urn:schemas-microsoft-com:vml">'
        '<mc:AlternateContent><mc:Choice Requires="wps"><w:drawing><wp:inline><wp:extent cx="914400" cy="457200"/>'
        '<wp:docPr id="901" name="Text Box 1"/><a:graphic><a:graphicData '
        'uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"><wps:wsp><wps:txbx><w:txbxContent>'
        '<w:p><w:r><w:t>%s</w:t></w:r></w:p></w:txbxContent></wps:txbx><wps:bodyPr/></wps:wsp></a:graphicData>'
        '</a:graphic></wp:inline></w:drawing></mc:Choice><mc:Fallback><w:pict><v:shape><v:textbox><w:txbxContent>'
        '<w:p><w:r><w:t>%s</w:t></w:r></w:p></w:txbxContent></v:textbox></v:shape></w:pict></mc:Fallback>'
        '</mc:AlternateContent></w:r>') % (W_NS, text, text)
    paragraph._p.append(etree.fromstring(xml))


def _run_child(paragraph, run_text_before: str, element: str, run_text_after: str):
    """One run: w:t, then an empty element (w:lastRenderedPageBreak, w:softHyphen …), then w:t."""
    r = paragraph.add_run(run_text_before)._r
    r.append(OxmlElement(f"w:{element}"))
    t = OxmlElement("w:t"); t.text = run_text_after; t.set(qn("xml:space"), "preserve"); r.append(t)


def build_batch1(section_number: str = "21 13 19", title: str = "PREACTION SPRINKLER SYSTEMS", *,
                 stamp: str = "ISSUED FOR PERMIT 2026-01-15", cover_stamp: str = "ISSUED FOR BID",
                 eos: str = "wrong", ends_with_table: bool = False, header_logo: bool = True,
                 tracked_on: bool = True, textbox: bool = False) -> bytes:
    """Hard cases for built-in batch 1 — every paragraph found by its text in the tests, never by index:

    * header: ``ACME DATA CENTERS — {{SITE_NAME}}``, a tab, the issue ``stamp``; a logo (``header_logo``); footer
      ``<section> - {PAGE}``;
    * body: the title line ``SECTION <n> - <title>``; the cover ``cover_stamp`` (bold); placeholders — one split over
      three runs, a highlighted ``PROJECT NO`` slot, one inside a field result; MasterSpec option groups, a nested one;
      "Owner" before a tab ("The Owner<tab>shall"), in "Ownership", in a hyperlink, in a text box, inside Jeremy's
      pending insertion, beside Jack's pending deletion, split by a page-break mark, in a text box (``textbox``);
      tagged paragraphs (BATCH1_TAGS);
    * the end: ``eos`` "right" (END OF SECTION <n>), "wrong" (another number), "missing", "early" (an END OF SECTION
      line followed by a paragraph), "styled" (missing, and an ``EOS`` style exists); ``ends_with_table``: a table after
      the last paragraph.
    """
    from docx.enum.style import WD_STYLE_TYPE
    from docx.enum.text import WD_COLOR_INDEX
    doc = Document()
    styles = doc.styles
    styles["Normal"].font.name = "Arial"
    styles["Normal"].font.size = Pt(10)
    if eos == "styled":
        eos_style = styles.add_style("EOS", WD_STYLE_TYPE.PARAGRAPH)
        eos_style.base_style = styles["Normal"]
        eos_style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sec = doc.sections[0]
    hp = sec.header.paragraphs[0]
    hp.add_run("ACME DATA CENTERS — ")
    hp.add_run("{{SITE_NAME}}")
    hp.add_run("\t")
    hp.add_run(stamp)
    if header_logo:
        hp.add_run(" ").add_picture(io.BytesIO(png((10, 80, 160), size=8)), width=Pt(36))
    sec.footer.paragraphs[0].add_run(f"{section_number} - ")
    _page_field(sec.footer.paragraphs[0])

    doc.add_paragraph(f"SECTION {section_number} - {title}")
    if cover_stamp:
        doc.add_paragraph().add_run(cover_stamp).bold = True
    doc.add_paragraph("PART 1 - GENERAL")
    doc.add_paragraph("1.01\tSUMMARY")
    p = doc.add_paragraph()
    p.add_run("A.\tThe Owner is {{OWNER_NAME}} for the ")
    p.add_run("{{SI")
    p.add_run("TE_")
    p.add_run("NAME}} campus.")                                                     # a placeholder over three runs
    p = doc.add_paragraph()
    p.add_run("B.\tThe project number is ")
    slot = p.add_run("PROJECT NO")
    slot.font.highlight_color = WD_COLOR_INDEX.YELLOW                               # a highlighted slot
    p.add_run(".")
    p = doc.add_paragraph()
    p.add_run("C.\tProvide [wet-pipe] [dry-pipe] [preaction] sprinkler systems for the data halls.")
    p = doc.add_paragraph()
    p.add_run("D.\t[Coordinate with the clean agent system.] Install hangers per NFPA 13.")
    p = doc.add_paragraph()
    p.add_run("E.\tPipe shall be [Schedule 40] [")
    p.add_run("Schedule 10] black steel.")                                          # a group over two runs
    doc.add_paragraph("F.\t[Provide [6] [8] inch mains.]")                         # nested
    p = doc.add_paragraph()
    p.add_run("G.\tThe Owner\tshall review the Ownership documents.")               # a tab inside "Owner shall"
    p = doc.add_paragraph()
    p.add_run("H.\tSee the ")
    _hyperlink(p, "Owner portal")
    p.add_run(".")
    p = doc.add_paragraph()
    p.add_run("I.\tThe insurer of the Owner is FM")
    _run_child(p, "", "lastRenderedPageBreak", " Global.")                          # a page-break mark in the phrase
    p = doc.add_paragraph()
    p.add_run("J.\tSprinklers: ")
    p.add_run("INS:listed per the Owner's insurer")                                 # Jeremy's pending insertion
    p.add_run(" as indicated.")
    p = doc.add_paragraph()
    p.add_run("K.\tThe Owner ")
    p.add_run("DEL:reserves ")                                                      # Jack's pending deletion
    p.add_run("shall approve.")
    p = doc.add_paragraph()
    p.add_run("L.\tSee ")
    _ref_field(p)                                                                    # REF field result "21 10 00"
    p.add_run(" for the Owner.")
    if textbox:
        p = doc.add_paragraph()
        p.add_run("M.\tOwner in a text box: ")
        _textbox_run(p, "Owner box text")
    for tag in BATCH1_TAGS:
        doc.add_paragraph(f"[{tag}] {tag.title()} system paragraph.")
    doc.add_paragraph("N.\tUntagged closing paragraph.")
    if eos == "early":
        doc.add_paragraph(f"END OF SECTION {section_number}")
        doc.add_paragraph("O.\tA paragraph after the end line.")
    elif eos == "right":
        doc.add_paragraph(f"END OF SECTION {section_number}")
    elif eos == "wrong":
        doc.add_paragraph("End of Section 21 13 13")
    doc.add_paragraph("")                                                           # an empty paragraph at the end
    if ends_with_table:
        t = doc.add_table(rows=1, cols=2)
        t.cell(0, 0).text = "Item"; t.cell(0, 1).text = "Value"
    if tracked_on:
        doc.settings.element.insert(0, OxmlElement("w:trackRevisions"))
    buf = io.BytesIO(); doc.save(buf)
    return _post_process(buf.getvalue())


# ---- built-in batch 2: generic package surgery for fixtures (docs/BUILTIN_OPS_BATCH2_DESIGN.md) -----------------------

def docx_bytes(doc) -> bytes:
    buf = io.BytesIO(); doc.save(buf)
    return buf.getvalue()


def patch_docx(data: bytes, edits: dict) -> bytes:
    """``data`` with each part in ``edits`` ({part name: fn(root) → None}) parsed, changed by its function and written
    back (fixture surgery only — the engine never edits XML this way)."""
    zin = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            payload = zin.read(info.filename)
            if info.filename in edits:
                root = etree.fromstring(payload)
                edits[info.filename](root)
                payload = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(zi, payload)
    return out.getvalue()


CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def add_parts(data: bytes, parts: dict, rels=(), overrides: dict = None) -> bytes:
    """``data`` with new members ``parts`` ({name: bytes}), relationships ``rels`` ((rels member, Id, Type, Target)
    — the rels member is created when missing) and content-type ``overrides`` ({member: content type})."""
    zin = zipfile.ZipFile(io.BytesIO(data))
    members = {i.filename: zin.read(i.filename) for i in zin.infolist()}
    order = [i.filename for i in zin.infolist()]
    for rels_member, rid, typ, target in rels:
        if rels_member in members:
            root = etree.fromstring(members[rels_member])
        else:
            root = etree.Element("{%s}Relationships" % REL_NS, nsmap={None: REL_NS})
            order.append(rels_member)
        el = etree.SubElement(root, "{%s}Relationship" % REL_NS)
        el.set("Id", rid); el.set("Type", typ); el.set("Target", target)
        members[rels_member] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    if overrides:
        root = etree.fromstring(members["[Content_Types].xml"])
        for name, ct in overrides.items():
            el = etree.SubElement(root, "{%s}Override" % CT_NS)
            el.set("PartName", "/" + name); el.set("ContentType", ct)
        members["[Content_Types].xml"] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    for name, payload in parts.items():
        members[name] = payload
        if name not in order:
            order.append(name)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zout:
        for name in order:
            zi = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(zi, members[name])
    return out.getvalue()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--count", type=int, default=len(SPECS))
    ap.add_argument("--rich", action="store_true", help="build the NL-editor op fixture (build_rich) instead")
    ap.add_argument("--template", action="store_true", help="write the header/footer template fixture only")
    ap.add_argument("--reference", action="store_true", help="write the template-spec fixture (an architect's spec) only")
    args = ap.parse_args(argv)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    if args.reference:
        (out / "08 71 00 - DOOR HARDWARE (ACME).docx").write_bytes(build_reference_spec())
        print(f"wrote {out / '08 71 00 - DOOR HARDWARE (ACME).docx'}")
        return
    if args.template:
        (out / "00_00_00_TEMPLATE.docx").write_bytes(build_template())
        print(f"wrote {out / '00_00_00_TEMPLATE.docx'}")
        return
    if args.rich:
        for i in range(args.count):
            num, title = SPECS[i % len(SPECS)]
            if i >= len(SPECS):
                num = f"{num} ({i // len(SPECS)})"
            (out / f"{num} – {title}.docx").write_bytes(build_rich(num, title))
        print(f"wrote {args.count} rich fixtures to {out}")
        return
    for i in range(args.count):
        num, title = SPECS[i % len(SPECS)]
        if i >= len(SPECS):
            num = f"{num} ({i // len(SPECS)})"
        (out / f"{num} – {title}.docx").write_bytes(build(num, title))   # en dash on purpose
    (out / "NO COVER PAGE FIELD.docx").write_bytes(build("21 99 00", "VARIANT", with_cover_page_field=False))
    (out / "SINGLE SECTION.docx").write_bytes(build("21 98 00", "VARIANT", single_section=True))
    print(f"wrote {args.count + 2} fixtures to {out}")


if __name__ == "__main__":
    main()
