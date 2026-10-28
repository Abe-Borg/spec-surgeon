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
``body_picture`` (a drawing whose id the template's logo must not reuse). Built-in batch 5's numbering fixtures:
``build_numbering()`` (an architect's spec numbered by its own outline list), ``build_typed()`` (numbered by typed text)
and ``build_scheme_example()`` (``--numbering-scheme``: ``examples/templates/NUMBERING SCHEME.docx``). The dirty
fixture family: ``build_dirty(kind)`` for every kind in ``DIRTY_KINDS`` (``--dirty``) — one recognizable specification
per deliberate defect family (fragmented runs, text-box headers, non-breaking spaces, compatibility mode, hundreds of
tracked changes, protection, LibreOffice output, an empty body) beside a well-styled one.
"""
from __future__ import annotations

import argparse
import copy
import io
import posixpath
import re
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
from docx.shared import Inches, Pt
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
          body_footer_linked: bool = False, appendix_restart: bool = False, body_size: float = 10,
          table_size: float | None = None) -> bytes:
    """``body_footer_linked``: the body section has no footer of its own ("Link to Previous"), so Word shows the
    cover's footer — and its PAGE field — on every body page. ``appendix_restart``: a third section (an appendix)
    that restarts page numbering at 1 again, like the body section does. ``body_size``: Normal's size in points
    (the body text's); ``table_size``: a direct size on every run of the revision table's cells (a specification
    whose tables are set smaller than its body)."""
    doc = Document()
    st = doc.styles["Normal"]; st.font.name = "Arial"; st.font.size = Pt(body_size)

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
    if table_size is not None:
        for cell in t._cells:
            for cp in cell.paragraphs:
                for run in cp.runs:
                    _sz(run, int(round(table_size * 2)))

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


def build_schedule(section_number: str = "21 13 13", title: str = "WET-PIPE SPRINKLER SYSTEMS",
                   cover_first_page: bool = False) -> bytes:
    """Built-in batch 5's section fixture (set_page_setup: landscape schedules, different first page):

    * section 0 (cover): the section number, title and a revision-history table; its own default header (a tab, then
      the issue stamp) and default footer ("<number> - {PAGE}", right-aligned); with ``cover_first_page`` also titlePg
      and its own first-page footer ("COVER");
    * section 1 (body, PART 1/2 in Heading 1): no references (shows the cover's header and footer), restarts at 1;
      "Schedule 40" in lower case;
    * section 2 (a schedule): "SPRINKLER HEAD SCHEDULE" and a table with a bold header row; no references;
    * section 3 (body continued, PART 3 in Heading 1, END OF SECTION): no references.
    """
    doc = Document()
    st = doc.styles["Normal"]; st.font.name = "Arial"; st.font.size = Pt(10)
    cover = doc.sections[0]
    _header(cover, "ISSUED FOR CONSTRUCTION  2026-01-01")
    _footer(cover, section_number)
    if cover_first_page:
        cover.different_first_page_header_footer = True
        ff = cover.first_page_footer; ff.is_linked_to_previous = False
        ff.paragraphs[0].add_run("COVER")
    doc.add_paragraph(f"SECTION {section_number}")
    doc.add_paragraph(title)
    doc.add_paragraph("PROJECT REVISION HISTORY")
    t = doc.add_table(rows=2, cols=3)
    t.cell(0, 0).text = "Rev"; t.cell(0, 1).text = "Date"; t.cell(0, 2).text = "Description"
    t.cell(1, 0).text = "0"; t.cell(1, 1).text = "2026-01-01"; t.cell(1, 2).text = "Issued for Construction"

    def section(restart: bool):
        sec = doc.add_section(WD_SECTION.NEW_PAGE)
        sec.header.is_linked_to_previous = True
        sec.footer.is_linked_to_previous = True
        if cover_first_page:
            sec.different_first_page_header_footer = False
            sec.first_page_footer.is_linked_to_previous = True
        if restart:
            _set_pgnum_start(sec, 1)
        else:
            for old in sec._sectPr.findall(qn("w:pgNumType")):   # add_section copies the previous restart
                sec._sectPr.remove(old)
        return sec

    section(restart=True)
    doc.add_paragraph("PART 1 - GENERAL", style="Heading 1")
    doc.add_paragraph("1.01\tSUMMARY")
    doc.add_paragraph("A.\tPipe shall be Schedule 40 per NFPA 13 (2025).")
    doc.add_paragraph("PART 2 - PRODUCTS", style="Heading 1")
    doc.add_paragraph("2.01\tSPRINKLERS")
    section(restart=False)
    doc.add_paragraph("SPRINKLER HEAD SCHEDULE")
    t = doc.add_table(rows=3, cols=4)
    for c, text in enumerate(("Mark", "Type", "K-factor", "Temperature")):
        t.cell(0, c).paragraphs[0].add_run(text).bold = True
    for c, text in enumerate(("S1", "Pendent", "5.6", "155 F")):
        t.cell(1, c).text = text
    for c, text in enumerate(("S2", "Upright", "8.0", "200 F")):
        t.cell(2, c).text = text
    section(restart=False)
    doc.add_paragraph("PART 3 - EXECUTION", style="Heading 1")
    doc.add_paragraph("3.01\tINSTALLATION")
    doc.add_paragraph("END OF SECTION")
    buf = io.BytesIO(); doc.save(buf)
    return buf.getvalue()


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


WRAPPING_HEADER = ("SECTION {{SECTION_NUMBER}} – {{SECTION_TITLE}} – AVC11 DATA CENTER PHASE 2 – 100% CONSTRUCTION "
                   "DOCUMENTS – PROJECT 2026-0451 – ISSUED FOR PERMIT – REVISION 3")
WRAPPING_FILLS = {
    "SECTION_NUMBER": {"rule": "regex", "source": "footer.default", "pattern": r"^(\d{2} \d{2} \d{2})"},
    "SECTION_TITLE": {"rule": "paragraph_after", "source": "document", "anchor_regex": r"^SECTION \d{2} \d{2} \d{2}$"},
}


def build_wrapping_template() -> bytes:
    """A prepared template whose header is one paragraph that wraps (live QA finding F08): WRAPPING_HEADER in Arial
    10 pt over three lines of a 6" text width (1.25" side margins, as build() targets have), the two placeholders on
    its first line with literal words after them, so a fill shorter or longer than its placeholder moves words
    between the lines; footer "{{SECTION_NUMBER}} - <PAGE>". One section (``template_sections: {"all": 0}``)."""
    doc = Document()
    doc.styles["Normal"].font.name = "Arial"; doc.styles["Normal"].font.size = Pt(10)
    doc.styles["Header"].font.size = Pt(10)
    s = doc.sections[0]
    s.left_margin = s.right_margin = Inches(1.25)
    h = s.header; h.is_linked_to_previous = False
    h.paragraphs[0].add_run(WRAPPING_HEADER)
    f = s.footer; f.is_linked_to_previous = False
    p = f.paragraphs[0]
    p.add_run("{{SECTION_NUMBER}} - ")
    _page_field(p)
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


# ---- built-in batch 3 (docs/BUILTIN_OPS_BATCH3_DESIGN.md) ------------------------------------------------------------

def _tabs(ppr_owner, stops):
    """Put own tab entries (val, pos[, leader]) on a paragraph or a style element's pPr, in the order given."""
    ppr = ppr_owner.get_or_add_pPr()
    tabs = OxmlElement("w:tabs")
    for st in stops:
        t = OxmlElement("w:tab"); t.set(qn("w:val"), st[0]); t.set(qn("w:pos"), str(st[1]))
        if len(st) > 2:
            t.set(qn("w:leader"), st[2])
        tabs.append(t)
    ppr.insert_element_before(tabs, "w:suppressAutoHyphens", "w:kinsoku", "w:wordWrap", "w:overflowPunct",
                              "w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN", "w:bidi", "w:adjustRightInd",
                              "w:snapToGrid", "w:spacing", "w:ind", "w:contextualSpacing", "w:mirrorIndents",
                              "w:suppressOverlap", "w:jc", "w:textDirection", "w:textAlignment",
                              "w:textboxTightWrap", "w:outlineLvl", "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr",
                              "w:pPrChange")


def build_tabs(section_number: str = "21 13 13", narrow_second: bool = True, colours: bool = True) -> bytes:
    """The set_tab_stops and color_in fixture (docs/BUILTIN_OPS_BATCH3_DESIGN.md). Every paragraph is found by its
    text in the tests.

    Styles: ``Header``/``Footer`` (python-docx's: center 4680, right 9360), ``ArticleTabs`` (left 720 and 1440),
    ``ArticleChild`` (based on ArticleTabs: clears 1440, adds right 4320), ``ListNumber`` (numbered by its style),
    ``EditorNote`` (a blue character colour, 0000FF, from the style), ``BlueNote`` (a paragraph style in that blue: its
    runs and its paragraph mark are blue). Section 1: Letter, 1" margins (text width 9360);
    section 2 (``narrow_second``): 1.25" margins (text width 8640) with a footer of its own and the header inherited from
    section 1 (a header shown at two widths).

    Body: the title lines; "1.01<tab>SUMMARY" (ArticleTabs, inherits two stops); "1.02<tab>REFERENCES" (ArticleTabs, own
    right 9360 dot leader and a clear at 720); "1.03<tab>SUBMITTALS" (ArticleChild); "Numbered<tab>item" (ListNumber);
    "Numbered direct<tab>item" (direct numPr, own left 2160); "Earlier<tab>change" (own left 2880 and a pending
    w:pPrChange by Jeremy); "Unsorted<tab>stops<tab>here" (own 2880 then 1440); a table cell paragraph with a tab; the
    section break paragraph "End of section one"; section 2: "Schedule<tab>value". Colours (``colours``): a run in direct
    blue 0000FF, one in theme accent1 (4472C4), one in the EditorNote style, a red run, a hyperlink-blue (0563C1) run
    — inside "Colours: …".
    """
    from docx.enum.style import WD_STYLE_TYPE
    from docx.shared import RGBColor, Twips
    doc = Document()
    styles = doc.styles
    art = styles.add_style("ArticleTabs", WD_STYLE_TYPE.PARAGRAPH)
    art.base_style = styles["Normal"]
    _tabs(art.element, [("left", 720), ("left", 1440)])
    child = styles.add_style("ArticleChild", WD_STYLE_TYPE.PARAGRAPH)
    child.base_style = art
    _tabs(child.element, [("clear", 1440), ("right", 4320)])
    note = styles.add_style("EditorNote", WD_STYLE_TYPE.CHARACTER)
    note.font.color.rgb = RGBColor(0x00, 0x00, 0xFF)
    blue_par = styles.add_style("BlueNote", WD_STYLE_TYPE.PARAGRAPH)
    blue_par.base_style = styles["Normal"]
    blue_par.font.color.rgb = RGBColor(0x00, 0x00, 0xFF)

    first = doc.sections[0]
    first.left_margin = first.right_margin = Twips(1440)
    hp = first.header.paragraphs[0]
    hp.style = styles["Header"]
    hp.add_run("ACME DATA CENTERS\tPROJECT 2026-07")
    fp = first.footer.paragraphs[0]
    fp.style = styles["Footer"]
    fp.add_run(f"SECTION {section_number}\t\tPAGE")
    doc.add_paragraph(f"SECTION {section_number}")
    doc.add_paragraph("WET-PIPE SPRINKLER SYSTEMS")
    doc.add_paragraph("1.01\tSUMMARY", style="ArticleTabs")
    p = doc.add_paragraph("1.02\tREFERENCES", style="ArticleTabs")
    _tabs(p._p, [("clear", 720), ("right", 9360, "dot")])
    doc.add_paragraph("1.03\tSUBMITTALS", style="ArticleChild")
    doc.add_paragraph("Numbered\titem", style="List Number")
    p = doc.add_paragraph("Numbered direct\titem")
    numpr = OxmlElement("w:numPr")
    ilvl = OxmlElement("w:ilvl"); ilvl.set(qn("w:val"), "0"); numpr.append(ilvl)
    nid = OxmlElement("w:numId"); nid.set(qn("w:val"), "5"); numpr.append(nid)
    p._p.get_or_add_pPr().insert(0, numpr)
    _tabs(p._p, [("left", 2160)])
    p = doc.add_paragraph("Earlier\tchange")
    _tabs(p._p, [("left", 2880)])
    chg = OxmlElement("w:pPrChange")
    chg.set(qn("w:id"), "404"); chg.set(qn("w:author"), "Jeremy"); chg.set(qn("w:date"), "2026-09-22T11:00:00Z")
    chg.append(OxmlElement("w:pPr"))
    p._p.get_or_add_pPr().append(chg)
    p = doc.add_paragraph("Unsorted\tstops\there")
    _tabs(p._p, [("left", 2880), ("left", 1440)])
    if colours:
        p = doc.add_paragraph("Colours: ")
        r = p.add_run("direct blue"); r.font.color.rgb = RGBColor(0x00, 0x00, 0xFF)
        p.add_run(", ")
        r = p.add_run("theme blue"); r.font.color.rgb = RGBColor(0x44, 0x72, 0xC4)
        r._r.rPr.find(qn("w:color")).set(qn("w:themeColor"), "accent1")
        p.add_run(", ")
        r = p.add_run("note style blue"); r.style = styles["EditorNote"]
        p.add_run(", ")
        r = p.add_run("red words"); r.font.color.rgb = RGBColor(0xFF, 0x00, 0x00)
        p.add_run(", ")
        r = p.add_run("link blue"); r.font.color.rgb = RGBColor(0x05, 0x63, 0xC1)
        p.add_run(" end.")
        p = doc.add_paragraph("Blue note paragraph ", style="BlueNote")
        r = p.add_run("kept red"); r.font.color.rgb = RGBColor(0xFF, 0x00, 0x00)
    t = doc.add_table(rows=1, cols=1)
    t.cell(0, 0).paragraphs[0].add_run("Cell\ttext")
    doc.add_paragraph("End of section one")
    if narrow_second:
        sec = doc.add_section(WD_SECTION.NEW_PAGE)
        sec.left_margin = sec.right_margin = Twips(1800)
        sec.header.is_linked_to_previous = True
        sec.footer.is_linked_to_previous = False
        f2 = sec.footer.paragraphs[0]
        f2.style = styles["Footer"]
        f2.add_run(f"SECTION {section_number} (2)\t\tPAGE")
    doc.add_paragraph("Schedule\tvalue")
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


# ---- built-in batch 4 (docs/BUILTIN_OPS_BATCH4_DESIGN.md) ------------------------------------------------------------

_W_DECL = f'xmlns:w="{W_NS}"'


def _add_style(doc, xml: str):
    """Append a style written as XML (``<w:style …>…</w:style>``, the w: prefix undeclared) to the styles part."""
    el = etree.fromstring(xml.replace("<w:style ", f"<w:style {_W_DECL} ", 1))
    doc.styles.element.append(el)
    return el


def _style_el(doc, sid: str):
    return next(s for s in doc.styles.element.findall(qn("w:style")) if s.get(qn("w:styleId")) == sid)


def _rpr_add(run, xml: str):
    """Add rPr children written as XML (``<w:b/><w:sz w:val="28"/>``) to a python-docx run, in that order."""
    rpr = run._r.get_or_add_rPr()
    for el in etree.fromstring(f"<w:x {_W_DECL}>{xml}</w:x>"):
        rpr.append(el)
    return rpr


def _ppr_add(paragraph, xml: str):
    """Add pPr children written as XML to a python-docx paragraph (appended — give them in schema order, after pStyle)."""
    ppr = paragraph._p.get_or_add_pPr()
    for el in etree.fromstring(f"<w:x {_W_DECL}>{xml}</w:x>"):
        ppr.append(el)
    return ppr


def _mark_add(paragraph, xml: str):
    ppr = paragraph._p.get_or_add_pPr()
    rpr = ppr.find(qn("w:rPr"))
    if rpr is None:
        rpr = OxmlElement("w:rPr")
        ppr.append(rpr)
    for el in etree.fromstring(f"<w:x {_W_DECL}>{xml}</w:x>"):
        rpr.append(el)
    return rpr


def _field_run(paragraph, instr: str, result: str):
    """A complex field begin/instr/separate/result/end in five runs."""
    def r(xml):
        el = etree.fromstring(f"<w:r {_W_DECL}>{xml}</w:r>")
        paragraph._p.append(el)
    r('<w:fldChar w:fldCharType="begin"/>')
    r(f'<w:instrText xml:space="preserve"> {instr} </w:instrText>')
    r('<w:fldChar w:fldCharType="separate"/>')
    r(f"<w:t>{result}</w:t>")
    r('<w:fldChar w:fldCharType="end"/>')


def build_styles(section_number: str = "21 13 13", title: str = "WET-PIPE SPRINKLER SYSTEMS") -> bytes:
    """The batch-4 fixture: an architect's spec (docs/BUILTIN_OPS_BATCH4_DESIGN.md). Every paragraph is found by its text
    in the tests. python-docx's template underneath: 164 styles, numbering.xml (9 lists), stylesWithEffects.xml.

    Styles (the architect's): ``ArchArticle`` ("Arch Article", spacing before 240 after 60, keepNext, next
    ``ArchNextOnly``), ``ArchPara`` ("Arch Para", left 720 hanging 360), ``ArchNote`` ("Arch Note", a box border and
    D9D9D9 shading), ``ArchNoteChild`` (based on ArchNote, its own bottom border nil — inherits the rest), ``ArchStrong``
    (bold character style), ``ArchTable`` (a table style), ``ArchLines`` (spacing after in lines: afterLines 100),
    ``ArchAuto`` (beforeAutospacing on), ``ArchStart`` (indentation written w:start/w:end); unused on purpose:
    ``ArchUnused`` (custom, based on ArchPara), ``ArchUnusedChar`` (custom character), ``ArchLinkedPara`` + its linked
    ``ArchLinkedChar`` (both unused), ``ArchNextOnly`` (used only as ArchArticle's next), ``ArchFieldOnly`` ("Arch Field
    Only", named only by a STYLEREF field in the header), ``ArchSnapshotOnly`` (named only by a pending w:pPrChange's
    snapshot). Heading1 (python-docx's, with its linked Heading1Char) carries w:lang en-GB in its rPr; docDefaults'
    language is en-GB.

    Body: "SECTION 21 13 13" (Normal; direct b, caps, sz 28, rFonts Arial, lang en-GB; direct jc center — bold and all
    caps); the title (Normal; direct b; spacing after 240; a direct bottom border); "PART 1 - GENERAL" (Heading1, a run in
    Heading1Char); "1.01 SUMMARY" (ArchArticle, "SUMMARY" in ArchStrong); "A. Section includes …" (ArchPara: direct ind
    left 1080, spacing after 120; runs: direct italic + colour 0000FF, a superscript "2" (vertAlign), a yellow
    highlight, a hidden run (vanish), lang fr-FR, lang en-GB, a Wingdings bullet run with direct caps, a w:sym run;
    its mark: direct b and sz 20, lang en-GB); "NOTE TO SPECIFIER …" (ArchNote); "NOTE child …" (ArchNoteChild);
    "Coordinate with …" (Normal; direct box border and direct shading FFFF00); "Earlier change" (Normal, direct keepNext
    and a pending w:pPrChange by Jeremy whose snapshot names ArchSnapshotOnly; its run with direct b under a pending
    w:rPrChange); "Manual list item" (direct numPr numId 1 + direct ind); "PART 2 - PRODUCTS" (Normal, typed in capitals,
    direct b — the appearance of a PART heading); "2.01 MATERIALS" (ArchArticle); "Lines spacing" (ArchLines); "Auto
    spacing" (ArchAuto); "Start indent" (ArchStart); a TOC field naming "Arch Article" in its \t switch; a table in
    ArchTable whose cell paragraph is ArchPara; "PART 3 - EXECUTION" (Heading1). Header: "SECTION 21 13 13" (Header
    style) with a STYLEREF "Arch Field Only" field. Footer: Footer style with a direct tab.
    """
    from docx.shared import Pt
    doc = Document()
    styles_el = doc.styles.element
    dd = styles_el.find(qn("w:docDefaults") + "/" + qn("w:rPrDefault") + "/" + qn("w:rPr") + "/" + qn("w:lang"))
    dd.set(qn("w:val"), "en-GB")
    h1 = _style_el(doc, "Heading1")
    lang = OxmlElement("w:lang"); lang.set(qn("w:val"), "en-GB")
    h1.find(qn("w:rPr")).append(lang)
    _add_style(doc, '<w:style w:type="paragraph" w:customStyle="1" w:styleId="ArchArticle"><w:name w:val="Arch Article"/>'
                    '<w:basedOn w:val="Normal"/><w:next w:val="ArchNextOnly"/><w:qFormat/><w:pPr><w:keepNext/>'
                    '<w:spacing w:before="240" w:after="60"/></w:pPr><w:rPr><w:b/></w:rPr></w:style>')
    _add_style(doc, '<w:style w:type="paragraph" w:customStyle="1" w:styleId="ArchNextOnly"><w:name w:val="Arch Next Only"/>'
                    '<w:basedOn w:val="Normal"/></w:style>')
    _add_style(doc, '<w:style w:type="paragraph" w:customStyle="1" w:styleId="ArchPara"><w:name w:val="Arch Para"/>'
                    '<w:basedOn w:val="Normal"/><w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:style>')
    _add_style(doc, '<w:style w:type="paragraph" w:customStyle="1" w:styleId="ArchNote"><w:name w:val="Arch Note"/>'
                    '<w:basedOn w:val="Normal"/><w:pPr><w:pBdr>'
                    '<w:top w:val="single" w:sz="4" w:space="1" w:color="auto"/>'
                    '<w:left w:val="single" w:sz="4" w:space="4" w:color="auto"/>'
                    '<w:bottom w:val="single" w:sz="4" w:space="1" w:color="auto"/>'
                    '<w:right w:val="single" w:sz="4" w:space="4" w:color="auto"/></w:pBdr>'
                    '<w:shd w:val="clear" w:color="auto" w:fill="D9D9D9"/></w:pPr>'
                    '<w:rPr><w:i/></w:rPr></w:style>')
    _add_style(doc, '<w:style w:type="paragraph" w:customStyle="1" w:styleId="ArchNoteChild"><w:name w:val="Arch Note Child"/>'
                    '<w:basedOn w:val="ArchNote"/><w:pPr><w:pBdr><w:bottom w:val="nil"/></w:pBdr></w:pPr></w:style>')
    _add_style(doc, '<w:style w:type="character" w:customStyle="1" w:styleId="ArchStrong"><w:name w:val="Arch Strong"/>'
                    '<w:basedOn w:val="DefaultParagraphFont"/><w:rPr><w:b/><w:bCs/></w:rPr></w:style>')
    _add_style(doc, '<w:style w:type="table" w:customStyle="1" w:styleId="ArchTable"><w:name w:val="Arch Table"/>'
                    '<w:basedOn w:val="TableNormal"/><w:pPr><w:spacing w:after="0"/></w:pPr><w:tblPr><w:tblBorders>'
                    '<w:top w:val="single" w:sz="4" w:space="0" w:color="auto"/></w:tblBorders></w:tblPr></w:style>')
    _add_style(doc, '<w:style w:type="paragraph" w:customStyle="1" w:styleId="ArchLines"><w:name w:val="Arch Lines"/>'
                    '<w:basedOn w:val="Normal"/><w:pPr><w:spacing w:afterLines="100"/></w:pPr></w:style>')
    _add_style(doc, '<w:style w:type="paragraph" w:customStyle="1" w:styleId="ArchAuto"><w:name w:val="Arch Auto"/>'
                    '<w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="100" w:beforeAutospacing="1"/></w:pPr></w:style>')
    _add_style(doc, '<w:style w:type="paragraph" w:customStyle="1" w:styleId="ArchStart"><w:name w:val="Arch Start"/>'
                    '<w:basedOn w:val="Normal"/><w:pPr><w:ind w:start="360" w:end="0"/></w:pPr></w:style>')
    _add_style(doc, '<w:style w:type="paragraph" w:customStyle="1" w:styleId="ArchUnused"><w:name w:val="Arch Unused"/>'
                    '<w:basedOn w:val="ArchPara"/></w:style>')
    _add_style(doc, '<w:style w:type="character" w:customStyle="1" w:styleId="ArchUnusedChar"><w:name w:val="Arch Unused Char"/>'
                    '<w:basedOn w:val="DefaultParagraphFont"/><w:rPr><w:i/></w:rPr></w:style>')
    _add_style(doc, '<w:style w:type="paragraph" w:customStyle="1" w:styleId="ArchLinkedPara"><w:name w:val="Arch Linked Para"/>'
                    '<w:basedOn w:val="Normal"/><w:link w:val="ArchLinkedChar"/></w:style>')
    _add_style(doc, '<w:style w:type="character" w:customStyle="1" w:styleId="ArchLinkedChar"><w:name w:val="Arch Linked Char"/>'
                    '<w:basedOn w:val="DefaultParagraphFont"/><w:link w:val="ArchLinkedPara"/></w:style>')
    _add_style(doc, '<w:style w:type="paragraph" w:customStyle="1" w:styleId="ArchFieldOnly"><w:name w:val="Arch Field Only"/>'
                    '<w:basedOn w:val="Normal"/></w:style>')
    _add_style(doc, '<w:style w:type="paragraph" w:customStyle="1" w:styleId="ArchSnapshotOnly"><w:name w:val="Arch Snapshot Only"/>'
                    '<w:basedOn w:val="Normal"/></w:style>')

    hp = doc.sections[0].header.paragraphs[0]
    hp.style = doc.styles["Header"]
    hp.add_run(f"SECTION {section_number}\t")
    _field_run(hp, 'STYLEREF "Arch Field Only" \\* MERGEFORMAT', "WET-PIPE")
    fp = doc.sections[0].footer.paragraphs[0]
    fp.style = doc.styles["Footer"]
    fp.add_run("Page\tend")
    _ppr_add(fp, '<w:tabs><w:tab w:val="right" w:pos="9000"/></w:tabs>')

    p = doc.add_paragraph()
    _ppr_add(p, '<w:jc w:val="center"/>')
    r = p.add_run(f"SECTION {section_number}")
    _rpr_add(r, '<w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:b/><w:caps/><w:sz w:val="28"/><w:lang w:val="en-GB"/>')
    p = doc.add_paragraph()
    _ppr_add(p, '<w:pBdr><w:bottom w:val="single" w:sz="8" w:space="1" w:color="auto"/></w:pBdr>'
                '<w:spacing w:after="240"/>')
    r = p.add_run(title)
    _rpr_add(r, "<w:b/>")
    p = doc.add_paragraph("PART 1 - GENERAL", style="Heading 1")
    r = p.add_run(" (general)")
    r.style = doc.styles["Heading 1 Char"]
    p = doc.add_paragraph("1.01\t", style="Arch Article")
    p.add_run("SUMMARY").style = doc.styles["Arch Strong"]
    p = doc.add_paragraph(style="Arch Para")
    _ppr_add(p, '<w:spacing w:after="120"/><w:ind w:left="1080"/>')
    p.add_run("A.\tSection includes ")
    r = p.add_run("wet-pipe")
    _rpr_add(r, '<w:i/><w:color w:val="0000FF"/>')
    p.add_run(" systems for 1000 ft")
    r = p.add_run("2")
    _rpr_add(r, '<w:vertAlign w:val="superscript"/>')
    p.add_run(" areas; ")
    r = p.add_run("[verify]")
    _rpr_add(r, '<w:highlight w:val="yellow"/>')
    r = p.add_run(" hidden note")
    _rpr_add(r, "<w:vanish/>")
    r = p.add_run(" voir annexe")
    _rpr_add(r, '<w:lang w:val="fr-FR"/>')
    r = p.add_run(" colour")
    _rpr_add(r, '<w:sz w:val="22"/><w:lang w:val="en-GB"/>')
    r = p.add_run("l")
    _rpr_add(r, '<w:rFonts w:ascii="Wingdings" w:hAnsi="Wingdings"/><w:caps/><w:sz w:val="16"/>')
    r = p.add_run()
    sym = OxmlElement("w:sym"); sym.set(qn("w:font"), "Symbol"); sym.set(qn("w:char"), "F0B7"); r._r.append(sym)
    _rpr_add(r, '<w:color w:val="FF0000"/>')
    _mark_add(p, '<w:b/><w:sz w:val="20"/><w:lang w:val="en-GB"/>')
    doc.add_paragraph("NOTE TO SPECIFIER: delete the article that does not apply.", style="Arch Note")
    doc.add_paragraph("NOTE child: inherits the box without its bottom.", style="Arch Note Child")
    p = doc.add_paragraph("Coordinate with the fire alarm contractor.")
    _ppr_add(p, '<w:pBdr><w:top w:val="double" w:sz="4" w:space="1" w:color="auto"/>'
                '<w:bottom w:val="double" w:sz="4" w:space="1" w:color="auto"/></w:pBdr>'
                '<w:shd w:val="clear" w:color="auto" w:fill="FFFF00"/>')
    p = doc.add_paragraph()
    _ppr_add(p, '<w:keepNext/>')
    r = p.add_run("Earlier change by Jeremy.")
    _rpr_add(r, "<w:b/>")
    rchg = etree.fromstring(f'<w:rPrChange {_W_DECL} w:id="301" w:author="Jeremy" w:date="2026-09-22T11:00:00Z"><w:rPr/></w:rPrChange>')
    r._r.rPr.append(rchg)
    pchg = etree.fromstring(f'<w:pPrChange {_W_DECL} w:id="302" w:author="Jeremy" w:date="2026-09-22T11:00:00Z">'
                            '<w:pPr><w:pStyle w:val="ArchSnapshotOnly"/></w:pPr></w:pPrChange>')
    p._p.pPr.append(pchg)
    p = doc.add_paragraph("Manual list item")
    _ppr_add(p, '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr><w:ind w:left="720" w:hanging="360"/>')
    p = doc.add_paragraph()
    r = p.add_run("PART 2 - PRODUCTS")
    _rpr_add(r, "<w:b/>")
    doc.add_paragraph("2.01\tMATERIALS", style="Arch Article")
    doc.add_paragraph("Lines spacing", style="Arch Lines")
    doc.add_paragraph("Auto spacing", style="Arch Auto")
    doc.add_paragraph("Start indent", style="Arch Start")
    p = doc.add_paragraph()
    _field_run(p, 'TOC \\o "1-3" \\t "Arch Article,2"', "Table of contents")
    t = doc.add_table(rows=1, cols=2)
    tbl_pr = t._tbl.tblPr
    ts = tbl_pr.find(qn("w:tblStyle"))
    if ts is None:
        ts = OxmlElement("w:tblStyle"); tbl_pr.insert(0, ts)
    ts.set(qn("w:val"), "ArchTable")
    t.cell(0, 0).paragraphs[0].style = doc.styles["Arch Para"]
    t.cell(0, 0).paragraphs[0].add_run("Cell item")
    t.cell(0, 1).text = "175 psi"
    doc.add_paragraph("PART 3 - EXECUTION", style="Heading 1")
    buf = io.BytesIO(); doc.save(buf)
    return buf.getvalue()


def build_style_template() -> bytes:
    """The office styles template (``examples/templates/OFFICE STYLES.docx``): "your" styles for map_styles / apply_style.

    ``PRT``, ``ART``, ``PR1``, ``PR2`` — paragraph styles numbered by one list (num 10 → abstractNum 10, levels 0–3, each
    level's w:pStyle naming its style); ``PRT`` links the character style ``PRTChar``; ``ART``'s next is ``PR1``; ``PR2``
    is based on ``PR1``. ``SpecNote`` — a box border and F2F2F2 shading. ``CMT`` — an unnumbered comment style based on
    ``CMTBase``. ``MyBody`` is named "Body Text" (python-docx's BodyText holds that name: a name clash). ``Heading1`` is
    redefined (Arial 14 bold, no theme fonts). ``Normal`` differs from python-docx's (Times New Roman 11). ``PicList`` is
    numbered by a picture-bullet list (num 11 → abstractNum 11, w:lvlPicBulletId); ``LinkList`` by a list whose
    abstractNum names a numbering style (num 12 → abstractNum 12, w:numStyleLink). ``TblMine`` is a table style;
    ``CharMine`` a character style.
    """
    doc = Document()
    normal = _style_el(doc, "Normal")
    rpr = normal.find(qn("w:rPr"))
    if rpr is None:
        rpr = OxmlElement("w:rPr"); normal.append(rpr)
    for el in etree.fromstring(f'<w:x {_W_DECL}><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>'
                               f'<w:sz w:val="22"/></w:x>'):
        rpr.append(el)
    h1 = _style_el(doc, "Heading1")
    h1rpr = h1.find(qn("w:rPr"))
    for c in list(h1rpr):
        h1rpr.remove(c)
    for el in etree.fromstring(f'<w:x {_W_DECL}><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:b/>'
                               f'<w:sz w:val="28"/></w:x>'):
        h1rpr.append(el)

    def numbered(sid, name, ilvl, extra="", based="Normal", rpr_xml=""):
        _add_style(doc, f'<w:style w:type="paragraph" w:customStyle="1" w:styleId="{sid}"><w:name w:val="{name}"/>'
                        f'<w:basedOn w:val="{based}"/>{extra}<w:qFormat/><w:pPr><w:numPr><w:ilvl w:val="{ilvl}"/>'
                        f'<w:numId w:val="10"/></w:numPr><w:spacing w:before="120" w:after="0"/></w:pPr>{rpr_xml}</w:style>')
    numbered("PRT", "PRT", 0, '<w:next w:val="ART"/><w:link w:val="PRTChar"/>', rpr_xml="<w:rPr><w:caps/></w:rPr>")
    numbered("ART", "ART", 1, '<w:next w:val="PR1"/>', rpr_xml="<w:rPr><w:b/></w:rPr>")
    numbered("PR1", "PR1", 2)
    numbered("PR2", "PR2", 3, based="PR1")
    _add_style(doc, '<w:style w:type="character" w:customStyle="1" w:styleId="PRTChar"><w:name w:val="PRT Char"/>'
                    '<w:basedOn w:val="DefaultParagraphFont"/><w:link w:val="PRT"/><w:rPr><w:caps/></w:rPr></w:style>')
    _add_style(doc, '<w:style w:type="paragraph" w:customStyle="1" w:styleId="SpecNote"><w:name w:val="Spec Note"/>'
                    '<w:basedOn w:val="Normal"/><w:pPr><w:pBdr>'
                    '<w:top w:val="single" w:sz="4" w:space="1" w:color="auto"/>'
                    '<w:left w:val="single" w:sz="4" w:space="4" w:color="auto"/>'
                    '<w:bottom w:val="single" w:sz="4" w:space="1" w:color="auto"/>'
                    '<w:right w:val="single" w:sz="4" w:space="4" w:color="auto"/></w:pBdr>'
                    '<w:shd w:val="clear" w:color="auto" w:fill="F2F2F2"/></w:pPr></w:style>')
    _add_style(doc, '<w:style w:type="paragraph" w:customStyle="1" w:styleId="CMTBase"><w:name w:val="CMT Base"/>'
                    '<w:basedOn w:val="Normal"/><w:rPr><w:color w:val="0000FF"/></w:rPr></w:style>')
    _add_style(doc, '<w:style w:type="paragraph" w:customStyle="1" w:styleId="CMT"><w:name w:val="CMT"/>'
                    '<w:basedOn w:val="CMTBase"/><w:rPr><w:i/></w:rPr></w:style>')
    _add_style(doc, '<w:style w:type="paragraph" w:customStyle="1" w:styleId="MyBody"><w:name w:val="Body Text"/>'
                    '<w:basedOn w:val="Normal"/></w:style>')
    _add_style(doc, '<w:style w:type="paragraph" w:customStyle="1" w:styleId="PicList"><w:name w:val="Pic List"/>'
                    '<w:basedOn w:val="Normal"/><w:pPr><w:numPr><w:numId w:val="11"/></w:numPr></w:pPr></w:style>')
    _add_style(doc, '<w:style w:type="paragraph" w:customStyle="1" w:styleId="LinkList"><w:name w:val="Link List"/>'
                    '<w:basedOn w:val="Normal"/><w:pPr><w:numPr><w:numId w:val="12"/></w:numPr></w:pPr></w:style>')
    _add_style(doc, '<w:style w:type="table" w:customStyle="1" w:styleId="TblMine"><w:name w:val="Tbl Mine"/>'
                    '<w:basedOn w:val="TableNormal"/><w:tblPr><w:tblBorders>'
                    '<w:bottom w:val="single" w:sz="8" w:space="0" w:color="auto"/></w:tblBorders></w:tblPr></w:style>')
    _add_style(doc, '<w:style w:type="character" w:customStyle="1" w:styleId="CharMine"><w:name w:val="Char Mine"/>'
                    '<w:basedOn w:val="DefaultParagraphFont"/><w:rPr><w:u w:val="single"/></w:rPr></w:style>')

    numbering = doc.part.numbering_part.element
    lvls = "".join(f'<w:lvl w:ilvl="{i}"><w:start w:val="1"/><w:numFmt w:val="{fmt}"/><w:pStyle w:val="{sid}"/>'
                   f'<w:lvlText w:val="{text}"/><w:lvlJc w:val="left"/><w:pPr><w:ind w:left="{720 * i}" '
                   f'w:hanging="720"/></w:pPr></w:lvl>'
                   for i, (sid, fmt, text) in enumerate([("PRT", "decimal", "PART %1 -"), ("ART", "decimal", "%1.%2"),
                                                         ("PR1", "upperLetter", "%3."), ("PR2", "decimal", "%4.")]))
    new_abs = [
        f'<w:abstractNum {_W_DECL} w:abstractNumId="10"><w:nsid w:val="1A2B3C4D"/><w:multiLevelType '
        f'w:val="multilevel"/>{lvls}</w:abstractNum>',
        f'<w:abstractNum {_W_DECL} w:abstractNumId="11"><w:multiLevelType w:val="singleLevel"/><w:lvl w:ilvl="0">'
        f'<w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val=""/><w:lvlPicBulletId w:val="0"/>'
        f'<w:lvlJc w:val="left"/></w:lvl></w:abstractNum>',
        f'<w:abstractNum {_W_DECL} w:abstractNumId="12"><w:multiLevelType w:val="multilevel"/>'
        f'<w:numStyleLink w:val="NoList"/></w:abstractNum>',
    ]
    last_abs = numbering.findall(qn("w:abstractNum"))[-1]
    at = list(numbering).index(last_abs) + 1
    for k, x in enumerate(new_abs):
        numbering.insert(at + k, etree.fromstring(x))
    for nid in (10, 11, 12):
        numbering.append(etree.fromstring(f'<w:num {_W_DECL} w:numId="{nid}"><w:abstractNumId w:val="{nid}"/></w:num>'))

    doc.add_paragraph("OFFICE STYLES", style="Title")
    for sid, text in (("PRT", "GENERAL"), ("ART", "SUMMARY"), ("PR1", "Item"), ("PR2", "Sub item"),
                      ("SpecNote", "A note."), ("CMT", "A comment.")):
        doc.add_paragraph(text, style=doc.styles[next(s.name for s in doc.styles if s.style_id == sid)])
    buf = io.BytesIO(); doc.save(buf)
    return buf.getvalue()


# ---- built-in batch 5: numbering (docs/BUILTIN_OPS_BATCH5_DESIGN.md) -------------------------------------------------

def _lvl_xml(ilvl: int, fmt: str, text: str, start: int = 1, pstyle: str = None, restart: int = None,
             legal: bool = False, suff: str = None, left: int = None, hanging: int = 360) -> str:
    """A ``w:lvl`` in CT_Lvl order (start, numFmt, lvlRestart, pStyle, isLgl, suff, lvlText, lvlJc, pPr)."""
    left = 720 * (ilvl + 1) if left is None else left
    return (f'<w:lvl w:ilvl="{ilvl}"><w:start w:val="{start}"/><w:numFmt w:val="{fmt}"/>'
            + (f'<w:lvlRestart w:val="{restart}"/>' if restart is not None else "")
            + (f'<w:pStyle w:val="{pstyle}"/>' if pstyle else "")
            + ("<w:isLgl/>" if legal else "")
            + (f'<w:suff w:val="{suff}"/>' if suff else "")
            + f'<w:lvlText w:val="{text}"/><w:lvlJc w:val="left"/>'
            + (f'<w:pPr><w:ind w:left="{left}" w:hanging="{hanging}"/></w:pPr>' if hanging else
               f'<w:pPr><w:ind w:left="{left}" w:firstLine="0"/></w:pPr>')
            + "</w:lvl>")


def _add_lists(doc, abstracts, nums):
    """Add ``w:abstractNum`` XML strings after the template's last abstractNum and ``w:num`` XML strings after its last
    num (python-docx's template: abstractNums 0–8, nums 1–9 — new ids from 10 never clash)."""
    numbering = doc.part.numbering_part.element
    at = list(numbering).index(numbering.findall(qn("w:abstractNum"))[-1]) + 1
    for k, x in enumerate(abstracts):
        numbering.insert(at + k, etree.fromstring(x.replace("<w:abstractNum ", f"<w:abstractNum {_W_DECL} ", 1)))
    at = list(numbering).index(numbering.findall(qn("w:num"))[-1]) + 1
    for k, x in enumerate(nums):
        numbering.insert(at + k, etree.fromstring(x.replace("<w:num ", f"<w:num {_W_DECL} ", 1)))


def _abstract_xml(aid: int, levels, nsid: str, kind: str = "multilevel") -> str:
    return (f'<w:abstractNum w:abstractNumId="{aid}"><w:nsid w:val="{nsid}"/><w:multiLevelType w:val="{kind}"/>'
            + "".join(levels) + "</w:abstractNum>")


def _style_name(doc, sid: str) -> str:
    return next(s.name for s in doc.styles if s.style_id == sid)


def _replace_last_paragraph(doc, xml: str):
    """Add a paragraph written as XML (``<w:p>…</w:p>``, the w: prefix undeclared) at the end of the body."""
    p = doc.add_paragraph()
    p._p.getparent().replace(p._p, etree.fromstring(xml.replace("<w:p>", f"<w:p {_W_DECL}>", 1)))


def _textbox_paragraph(paragraph, pstyle: str, text: str):
    """A run holding a text box whose one paragraph is numbered by ``pstyle`` (DrawingML + VML fallback: twice)."""
    inner = (f'<w:p><w:pPr><w:pStyle w:val="{pstyle}"/></w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>')
    xml = (
        '<w:r xmlns:w="%s" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
        'xmlns:v="urn:schemas-microsoft-com:vml">'
        '<mc:AlternateContent><mc:Choice Requires="wps"><w:drawing><wp:inline><wp:extent cx="914400" cy="457200"/>'
        '<wp:docPr id="951" name="Text Box 51"/><a:graphic><a:graphicData '
        'uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"><wps:wsp><wps:txbx><w:txbxContent>'
        '%s</w:txbxContent></wps:txbx><wps:bodyPr/></wps:wsp></a:graphicData>'
        '</a:graphic></wp:inline></w:drawing></mc:Choice><mc:Fallback><w:pict><v:shape><v:textbox><w:txbxContent>'
        '%s</w:txbxContent></v:textbox></v:shape></w:pict></mc:Fallback>'
        '</mc:AlternateContent></w:r>') % (W_NS, inner, inner)
    paragraph._p.append(etree.fromstring(xml))


def build_numbering(shared_restart: bool = True) -> bytes:
    """The numbering fixture (built-in batch 5, §0): an architect's spec numbered automatically by its OWN outline list.
    Every paragraph is found by its text in the tests (tests/test_lists.py ``NUMBERING_EXPECTED`` lists each one's
    number). python-docx's template underneath (abstractNums 0–8, nums 1–9; its List Number/List Bullet styles unused).

    The outline list: num 10 → abstractNum 10, levels named by their styles (``w:lvl/w:pStyle``):
    0 ``ArchPart`` ("Arch Part") ``PART %1 -`` decimal, suffix space — the style's numPr names ilvl 0;
    1 ``ArchArt`` ("Arch Article") ``%1.%2``, level 1 decimalZero (``1.01``) — ilvl 1;
    2 ``ArchPara`` ("Arch Paragraph") ``%3.`` upperLetter — numPr WITHOUT ilvl: the LINKED level;
    3 ``ArchSub`` ("Arch Subparagraph") ``%4.`` decimal — ilvl 3;
    4 ``ArchSub2`` ("Arch Subparagraph 2", based on ArchSub) ``%5.`` lowerLetter — its own numPr without ilvl (linked);
    ``ArchParaAlt`` is based on ArchPara with no numPr of its own (inherits level 2 through the chain).

    Body (outline numbers in brackets): SECTION 21 13 13; the title; GENERAL [PART 1 -]; SUMMARY [1.01]; "Section
    includes wet-pipe sprinkler systems." [A.]; "Related requirements:" [B.]; "Division 21 Section …" [1.]; "Hangers."
    [a.]; "Supports." [b.]; "Division 28 Section …" [2.]; an EMPTY ArchPara [C. — it uses up a number]; "Deferred
    submittal." [D.]; REFERENCES [1.02] (bookmark ``_Ref1001`` around its text); "NFPA 13." [A.]; "Hidden requirement."
    (hidden: run and mark w:vanish) [B.]; "UL listings." (ArchParaAlt) [C.]; SUBMITTALS [1.03]; "Product data." (ArchPara
    whose own numPr has ONLY ``ilvl 3``) [1.]; "Shop drawings." [A.]; "Deleted requirement." (its mark and run a PENDING
    deletion by Jeremy — not counted); "Calculations, as specified in Article 1.02." [B.] (a written cross-reference);
    "Comply with Article 1.02." [C.] (a ``REF _Ref1001 \\r \\h`` field, cached "1.02"); "Bullet one", "Bullet two"
    (Normal, direct numPr num 21 → abstractNum 21, a bullet "•"); a paragraph holding a text box whose paragraph "Text box
    item" is ArchPara (not counted; it exists twice: DrawingML and VML); PRODUCTS [PART 2 -]; MANUFACTURERS [2.01];
    "Tyco." [A.]; "Viking." [B.]; MATERIALS [2.02]; "Steel pipe." [A.]; PIPING; "Black steel."; VALVES; "Gate valves.";
    "B.\\tCheck valves." (ArchPara whose text starts with a TYPED "B." — double numbering); EXECUTION [PART 3 -];
    INSTALLATION [3.01]; "Install per NFPA 13." [A.]; "Unnumbered note under a numbered style." (ArchPara, own numPr
    numId 0: numbering off); a table: "Cell requirement." (ArchPara) / "Remark" (Normal); "After the table." (ArchPara).

    ``shared_restart`` (the default): PIPING carries a direct numPr ``num 11, ilvl 1``; num 11 → the SAME abstractNum 10
    with ``startOverride`` 2 on levels 0 and 1 (Word's "Set numbering value"/restart glitch: a duplicate 2.02). Two nums of
    one abstractNum number paragraphs, so EVERY paragraph of num 10 and num 11 is "not evaluated" (``shared``: Word and
    LibreOffice number them differently) — the audit's case. ``shared_restart=False``: PIPING is a plain ArchArt and the
    outline is clean — PIPING [2.03], "Black steel." [A.], VALVES [2.04], "Gate valves." [A.], "B.\\tCheck…" [B.]; (with
    True and a per-numId count it would be PIPING 2.02, "Black steel." B., VALVES 2.03.)

    Then three small lists of their own (Normal paragraphs, direct numPr):
    * the appendix list, num 22 → abstractNum 22 (NOT shared), ``startOverride`` 3 on level 0: 0 upperRoman
      ``APPENDIX %1`` (abstract start 1), 1 upperLetter ``%2.`` start 26, 2 decimal ``%1.%2.%3`` isLgl lvlRestart 1,
      3 lowerRoman ``(%4)`` lvlRestart 0 — "Appendix one" [APPENDIX III], "Zed" [Z.], "Legal one" [3.26.1], "Double A"
      [AA.], "Legal two" [3.27.2], "Double B" [BB.], "Roman one" [(i)], "Appendix two" [APPENDIX IV], "Legal three"
      [4.26.1], "Roman two" [(ii)], "Zed again" [Z.];
    * the override list, num 23 → abstractNum 23 (0 decimal ``%1.``, 1 lowerLetter ``%2)``) whose num carries a FULL
      override level for level 1 (``w:lvlOverride/w:lvl``): "Override first" [1.], "Override sub" (level 1 — not
      evaluated: ``override``), "Override second" [2.].
    """
    doc = Document()
    for sid, name, numpr, extra in (
            ("ArchPart", "Arch Part", '<w:ilvl w:val="0"/><w:numId w:val="10"/>', "<w:keepNext/>"),
            ("ArchArt", "Arch Article", '<w:ilvl w:val="1"/><w:numId w:val="10"/>', "<w:keepNext/>"),
            ("ArchPara", "Arch Paragraph", '<w:numId w:val="10"/>', ""),
            ("ArchSub", "Arch Subparagraph", '<w:ilvl w:val="3"/><w:numId w:val="10"/>', "")):
        rpr = "<w:rPr><w:b/><w:caps/></w:rPr>" if sid == "ArchPart" else ("<w:rPr><w:b/></w:rPr>" if sid == "ArchArt"
                                                                           else "")
        _add_style(doc, f'<w:style w:type="paragraph" w:customStyle="1" w:styleId="{sid}"><w:name w:val="{name}"/>'
                        f'<w:basedOn w:val="Normal"/><w:qFormat/><w:pPr>{extra}<w:numPr>{numpr}</w:numPr>'
                        f'<w:spacing w:before="120" w:after="0"/></w:pPr>{rpr}</w:style>')
    _add_style(doc, '<w:style w:type="paragraph" w:customStyle="1" w:styleId="ArchSub2"><w:name w:val="Arch Subparagraph 2"/>'
                    '<w:basedOn w:val="ArchSub"/><w:qFormat/><w:pPr><w:numPr><w:numId w:val="10"/></w:numPr></w:pPr>'
                    '</w:style>')
    _add_style(doc, '<w:style w:type="paragraph" w:customStyle="1" w:styleId="ArchParaAlt"><w:name w:val="Arch Paragraph Alt"/>'
                    '<w:basedOn w:val="ArchPara"/><w:rPr><w:i/></w:rPr></w:style>')
    outline = [_lvl_xml(0, "decimal", "PART %1 -", pstyle="ArchPart", suff="space", left=0, hanging=0),
               _lvl_xml(1, "decimalZero", "%1.%2", pstyle="ArchArt", left=864, hanging=864),
               _lvl_xml(2, "upperLetter", "%3.", pstyle="ArchPara", left=1440, hanging=576),
               _lvl_xml(3, "decimal", "%4.", pstyle="ArchSub", left=2016, hanging=576),
               _lvl_xml(4, "lowerLetter", "%5.", pstyle="ArchSub2", left=2592, hanging=576)]
    appendix = [_lvl_xml(0, "upperRoman", "APPENDIX %1", suff="space", left=0, hanging=0),
                _lvl_xml(1, "upperLetter", "%2.", start=26),
                _lvl_xml(2, "decimal", "%1.%2.%3", restart=1, legal=True),
                _lvl_xml(3, "lowerRoman", "(%4)", restart=0)]
    override = [_lvl_xml(0, "decimal", "%1."), _lvl_xml(1, "lowerLetter", "%2)")]
    nums = ['<w:num w:numId="10"><w:abstractNumId w:val="10"/></w:num>']
    if shared_restart:
        nums.append('<w:num w:numId="11"><w:abstractNumId w:val="10"/>'
                    '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="2"/></w:lvlOverride>'
                    '<w:lvlOverride w:ilvl="1"><w:startOverride w:val="2"/></w:lvlOverride></w:num>')
    nums += ['<w:num w:numId="21"><w:abstractNumId w:val="21"/></w:num>',
             '<w:num w:numId="22"><w:abstractNumId w:val="22"/>'
             '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="3"/></w:lvlOverride></w:num>',
             '<w:num w:numId="23"><w:abstractNumId w:val="23"/><w:lvlOverride w:ilvl="1">'
             + _lvl_xml(1, "lowerRoman", "%2)") + '</w:lvlOverride></w:num>']
    _add_lists(doc, [_abstract_xml(10, outline, "5A5A0010"),
                     _abstract_xml(21, [_lvl_xml(0, "bullet", "•")], "5A5A0021", "singleLevel"),
                     _abstract_xml(22, appendix, "5A5A0022"),
                     _abstract_xml(23, override, "5A5A0023")], nums)

    S = {sid: _style_name(doc, sid) for sid in ("ArchPart", "ArchArt", "ArchPara", "ArchParaAlt", "ArchSub", "ArchSub2")}
    doc.add_paragraph("SECTION 21 13 13")
    doc.add_paragraph("WET-PIPE SPRINKLER SYSTEMS")
    doc.add_paragraph("GENERAL", style=S["ArchPart"])
    doc.add_paragraph("SUMMARY", style=S["ArchArt"])
    doc.add_paragraph("Section includes wet-pipe sprinkler systems.", style=S["ArchPara"])
    doc.add_paragraph("Related requirements:", style=S["ArchPara"])
    doc.add_paragraph('Division 21 Section "Common Work Results for Fire Suppression".', style=S["ArchSub"])
    doc.add_paragraph("Hangers.", style=S["ArchSub2"])
    doc.add_paragraph("Supports.", style=S["ArchSub2"])
    doc.add_paragraph('Division 28 Section "Fire Detection and Alarm".', style=S["ArchSub"])
    doc.add_paragraph(style=S["ArchPara"])                                   # empty: uses up "C."
    doc.add_paragraph("Deferred submittal.", style=S["ArchPara"])
    p = doc.add_paragraph(style=S["ArchArt"])
    p._p.append(etree.fromstring(f'<w:bookmarkStart {_W_DECL} w:id="1" w:name="_Ref1001"/>'))
    p.add_run("REFERENCES")
    p._p.append(etree.fromstring(f'<w:bookmarkEnd {_W_DECL} w:id="1"/>'))
    doc.add_paragraph("NFPA 13.", style=S["ArchPara"])
    p = doc.add_paragraph(style=S["ArchPara"])
    _rpr_add(p.add_run("Hidden requirement."), "<w:vanish/>")
    _mark_add(p, "<w:vanish/>")
    doc.add_paragraph("UL listings.", style=S["ArchParaAlt"])
    doc.add_paragraph("SUBMITTALS", style=S["ArchArt"])
    p = doc.add_paragraph("Product data.", style=S["ArchPara"])
    _ppr_add(p, '<w:numPr><w:ilvl w:val="3"/></w:numPr>')
    doc.add_paragraph("Shop drawings.", style=S["ArchPara"])
    _replace_last_paragraph(
        doc, '<w:p><w:pPr><w:pStyle w:val="ArchPara"/><w:rPr><w:del w:id="501" w:author="Jeremy" '
             'w:date="2026-09-22T10:15:00Z"/></w:rPr></w:pPr><w:del w:id="502" w:author="Jeremy" '
             'w:date="2026-09-22T10:15:00Z"><w:r><w:delText>Deleted requirement.</w:delText></w:r></w:del></w:p>')
    doc.add_paragraph("Calculations, as specified in Article 1.02.", style=S["ArchPara"])
    p = doc.add_paragraph("Comply with Article ", style=S["ArchPara"])
    _field_run(p, "REF _Ref1001 \\r \\h", "1.02")
    p.add_run(".")
    for text in ("Bullet one", "Bullet two"):
        p = doc.add_paragraph(text)
        _ppr_add(p, '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="21"/></w:numPr>')
    p = doc.add_paragraph("Text box holder: ")
    _textbox_paragraph(p, "ArchPara", "Text box item")
    doc.add_paragraph("PRODUCTS", style=S["ArchPart"])
    doc.add_paragraph("MANUFACTURERS", style=S["ArchArt"])
    doc.add_paragraph("Tyco.", style=S["ArchPara"])
    doc.add_paragraph("Viking.", style=S["ArchPara"])
    doc.add_paragraph("MATERIALS", style=S["ArchArt"])
    doc.add_paragraph("Steel pipe.", style=S["ArchPara"])
    p = doc.add_paragraph("PIPING", style=S["ArchArt"])
    if shared_restart:
        _ppr_add(p, '<w:numPr><w:ilvl w:val="1"/><w:numId w:val="11"/></w:numPr>')
    doc.add_paragraph("Black steel.", style=S["ArchPara"])
    doc.add_paragraph("VALVES", style=S["ArchArt"])
    doc.add_paragraph("Gate valves.", style=S["ArchPara"])
    doc.add_paragraph("B.\tCheck valves.", style=S["ArchPara"])
    doc.add_paragraph("EXECUTION", style=S["ArchPart"])
    doc.add_paragraph("INSTALLATION", style=S["ArchArt"])
    doc.add_paragraph("Install per NFPA 13.", style=S["ArchPara"])
    p = doc.add_paragraph("Unnumbered note under a numbered style.", style=S["ArchPara"])
    _ppr_add(p, '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="0"/></w:numPr>')
    t = doc.add_table(rows=1, cols=2)
    t.cell(0, 0).paragraphs[0].style = doc.styles[S["ArchPara"]]
    t.cell(0, 0).paragraphs[0].add_run("Cell requirement.")
    t.cell(0, 1).text = "Remark"
    doc.add_paragraph("After the table.", style=S["ArchPara"])
    for text, num, ilvl in (("Appendix one", 22, 0), ("Zed", 22, 1), ("Legal one", 22, 2), ("Double A", 22, 1),
                            ("Legal two", 22, 2), ("Double B", 22, 1), ("Roman one", 22, 3), ("Appendix two", 22, 0),
                            ("Legal three", 22, 2), ("Roman two", 22, 3), ("Zed again", 22, 1),
                            ("Override first", 23, 0), ("Override sub", 23, 1), ("Override second", 23, 0)):
        p = doc.add_paragraph(text)
        _ppr_add(p, f'<w:numPr><w:ilvl w:val="{ilvl}"/><w:numId w:val="{num}"/></w:numPr>')
    buf = io.BytesIO(); doc.save(buf)
    return buf.getvalue()


def _typed_paragraph(doc, runs, style=None):
    """A paragraph whose runs are ``runs`` (strings; a ``\\t`` inside one becomes a w:tab in that run — python-docx)."""
    p = doc.add_paragraph(style=style)
    for r in runs:
        p.add_run(r)
    return p


def build_typed(typo: bool = False, leading_tab: bool = False, spaces: bool = False, masterspec: bool = False) -> bytes:
    """A spec numbered by TYPED text (built-in batch 5, §2): every paragraph Normal, nothing numbered automatically; the
    numbers fit the example file's scheme (``build_scheme_example``: ``PART %1 -``, ``%1.%2``, ``%3.``, ``%4.``,
    ``%5.``). Found by text in the tests.

    Body: "SECTION 21 13 13"; "WET-PIPE SPRINKLER SYSTEMS"; "PART 1 - GENERAL" (one run, a space after the number);
    "1.1" + a tab-only run + "SUMMARY" (three runs); "A" + ".⇥Section includes wet-pipe sprinkler systems." (the number
    SPLIT across two runs, the tab inside the second); "B.⇥Related requirements:" (one run, the tab inside); "1.⇥Division
    21 Section …"; "a.⇥Hangers."; "b.⇥Supports."; "2.⇥Division 28 Section …"; "C.  Submittals." (two spaces, no tab);
    "1.2⇥REFERENCES"; "A.⇥NFPA 13."; "PART 2 - PRODUCTS"; "2." + "1⇥" + "MANUFACTURERS" (split); "A.⇥Tyco.";
    "B.⇥Viking."; "C.⇥Reliable." — ``typo``: "B.⇥Reliable." (a duplicate B: a conversion must refuse); "2.2⇥MATERIALS";
    "A.⇥Steel pipe."; a table whose cells hold "1.⇥Cell item." and "2.⇥Cell item two." (left alone by default);
    "PART 3 - EXECUTION"; "3.1⇥INSTALLATION"; "A.⇥Install per NFPA 13."; "1.⇥Hangers per NFPA 13."; "Unnumbered closing
    note." (no typed number); "END OF SECTION 21 13 13".

    ``leading_tab``: the letter and number items start with whitespace — a tab before every "A."-level item, two spaces
    before every "1."-level item. ``spaces``: every tab separator is a single space instead. ``masterspec``: the
    articles typed with a zero, "1.01", "1.02", "2.01" (its "01" in the second run), "2.02", "3.01", and every PART with
    an en dash, "PART 1 – GENERAL" — the same values in another look than the example file's scheme.
    """
    sep = " " if spaces else "\t"
    z = "0" if masterspec else ""
    dash = "\u2013" if masterspec else "-"
    lead1 = "\t" if leading_tab else ""
    lead2 = "  " if leading_tab else ""
    doc = Document()
    doc.add_paragraph("SECTION 21 13 13")
    doc.add_paragraph("WET-PIPE SPRINKLER SYSTEMS")
    _typed_paragraph(doc, [f"PART 1 {dash} GENERAL"])
    _typed_paragraph(doc, [f"1.{z}1", sep, "SUMMARY"])
    _typed_paragraph(doc, [lead1 + "A", f".{sep}Section includes wet-pipe sprinkler systems."])
    _typed_paragraph(doc, [f"{lead1}B.{sep}Related requirements:"])
    _typed_paragraph(doc, [f'{lead2}1.{sep}Division 21 Section "Common Work Results for Fire Suppression".'])
    _typed_paragraph(doc, [f"a.{sep}Hangers."])
    _typed_paragraph(doc, [f"b.{sep}Supports."])
    _typed_paragraph(doc, [f'{lead2}2.{sep}Division 28 Section "Fire Detection and Alarm".'])
    _typed_paragraph(doc, [f"{lead1}C.  Submittals."])
    _typed_paragraph(doc, [f"1.{z}2{sep}REFERENCES"])
    _typed_paragraph(doc, [f"{lead1}A.{sep}NFPA 13."])
    _typed_paragraph(doc, [f"PART 2 {dash} PRODUCTS"])
    _typed_paragraph(doc, ["2.", f"{z}1{sep}", "MANUFACTURERS"])
    _typed_paragraph(doc, [f"{lead1}A.{sep}Tyco."])
    _typed_paragraph(doc, [f"{lead1}B.{sep}Viking."])
    _typed_paragraph(doc, [f"{lead1}{'B' if typo else 'C'}.{sep}Reliable."])
    _typed_paragraph(doc, [f"2.{z}2{sep}MATERIALS"])
    _typed_paragraph(doc, [f"{lead1}A.{sep}Steel pipe."])
    t = doc.add_table(rows=1, cols=2)
    t.cell(0, 0).paragraphs[0].add_run(f"1.{sep}Cell item.")
    t.cell(0, 1).paragraphs[0].add_run(f"2.{sep}Cell item two.")
    _typed_paragraph(doc, [f"PART 3 {dash} EXECUTION"])
    _typed_paragraph(doc, [f"3.{z}1{sep}INSTALLATION"])
    _typed_paragraph(doc, [f"{lead1}A.{sep}Install per NFPA 13."])
    _typed_paragraph(doc, [f"{lead2}1.{sep}Hangers per NFPA 13."])
    doc.add_paragraph("Unnumbered closing note.")
    doc.add_paragraph("END OF SECTION 21 13 13")
    buf = io.BytesIO(); doc.save(buf)
    return buf.getvalue()


SCHEME_LEVELS = ["PRT", "ART", "PR1", "PR2", "PR3"]


def build_scheme_example() -> bytes:
    """The user's example file (``examples/templates/NUMBERING SCHEME.docx``; built-in batch 5 §0.3): paragraph styles
    ``PRT``, ``ART``, ``PR1``, ``PR2``, ``PR3`` (names equal to the ids; based on Normal) numbered by ONE list — num 10 →
    abstractNum 10, levels 0–4: ``PART %1 -`` (decimal, ``w:suff`` space), ``%1.%2`` (decimal, decimal → "1.1"),
    ``%3.`` (upperLetter), ``%4.`` (decimal), ``%5.`` (lowerLetter). Each level's ``w:pStyle`` names its style and each
    style's numPr names numId 10 AND its ilvl explicitly (LibreOffice ignores the linked level — ooxml-lessons §6).

    Body (numbers in brackets): "NUMBERING SCHEME" (Title); GENERAL [PART 1 -]; SUMMARY [1.1]; "Paragraph." [A.];
    "Subparagraph." [1.]; "Sub-subparagraph." [a.]; "Second sub-subparagraph." [b.]; "Second subparagraph." [2.]; "Second
    paragraph." [B.]; "SECOND ARTICLE" [1.2]; PRODUCTS [PART 2 -]; MATERIALS [2.1]; "Paragraph under part 2." [A.].
    """
    doc = Document()
    ids = SCHEME_LEVELS
    for k, sid in enumerate(ids):
        rpr = {"PRT": "<w:rPr><w:b/><w:caps/></w:rPr>", "ART": "<w:rPr><w:b/></w:rPr>"}.get(sid, "")
        keep = "<w:keepNext/>" if k < 2 else ""
        nxt = f'<w:next w:val="{ids[k + 1]}"/>' if k + 1 < len(ids) else ""
        _add_style(doc, f'<w:style w:type="paragraph" w:customStyle="1" w:styleId="{sid}"><w:name w:val="{sid}"/>'
                        f'<w:basedOn w:val="Normal"/>{nxt}<w:qFormat/><w:pPr>{keep}<w:numPr><w:ilvl w:val="{k}"/>'
                        f'<w:numId w:val="10"/></w:numPr><w:spacing w:before="120" w:after="0"/></w:pPr>{rpr}</w:style>')
    levels = [_lvl_xml(0, "decimal", "PART %1 -", pstyle="PRT", suff="space", left=0, hanging=0),
              _lvl_xml(1, "decimal", "%1.%2", pstyle="ART", left=720, hanging=720),
              _lvl_xml(2, "upperLetter", "%3.", pstyle="PR1", left=1440, hanging=720),
              _lvl_xml(3, "decimal", "%4.", pstyle="PR2", left=2160, hanging=720),
              _lvl_xml(4, "lowerLetter", "%5.", pstyle="PR3", left=2880, hanging=720)]
    _add_lists(doc, [_abstract_xml(10, levels, "6B6B0010")],
               ['<w:num w:numId="10"><w:abstractNumId w:val="10"/></w:num>'])
    doc.add_paragraph("NUMBERING SCHEME", style="Title")
    for sid, text in (("PRT", "GENERAL"), ("ART", "SUMMARY"), ("PR1", "Paragraph."), ("PR2", "Subparagraph."),
                      ("PR3", "Sub-subparagraph."), ("PR3", "Second sub-subparagraph."), ("PR2", "Second subparagraph."),
                      ("PR1", "Second paragraph."), ("ART", "SECOND ARTICLE"), ("PRT", "PRODUCTS"),
                      ("ART", "MATERIALS"), ("PR1", "Paragraph under part 2.")):
        doc.add_paragraph(text, style=doc.styles[_style_name(doc, sid)])
    buf = io.BytesIO(); doc.save(buf)
    return buf.getvalue()


# ---- Safe mode's "match body formatting to template" -------------------------------

def _page(doc, margin: int, size=(12240, 15840), header: int = 720):
    """Every section's page: ``size`` (w, h twips), all four margins ``margin``, header/footer distance ``header``."""
    for sec in doc.sections:
        sp = sec._sectPr
        for el in sp.findall(qn("w:pgSz")) + sp.findall(qn("w:pgMar")):
            sp.remove(el)
        sz = etree.fromstring(f'<w:pgSz {_W_DECL} w:w="{size[0]}" w:h="{size[1]}"/>')
        mar = etree.fromstring(f'<w:pgMar {_W_DECL} w:top="{margin}" w:right="{margin}" w:bottom="{margin}" '
                               f'w:left="{margin}" w:header="{header}" w:footer="{header}" w:gutter="0"/>')
        sp.insert(0, mar)
        sp.insert(0, sz)


def _body_font(doc, font: str, half_points: int):
    """The document defaults' font and size (every style based on them inherits it)."""
    styles = doc.styles.element
    rpr = styles.find(qn("w:docDefaults") + "/" + qn("w:rPrDefault") + "/" + qn("w:rPr"))
    for el in rpr.findall(qn("w:rFonts")) + rpr.findall(qn("w:sz")) + rpr.findall(qn("w:szCs")):
        rpr.remove(el)
    rpr.insert(0, etree.fromstring(f'<w:rFonts {_W_DECL} w:ascii="{font}" w:hAnsi="{font}" w:cs="{font}"/>'))
    rpr.append(etree.fromstring(f'<w:sz {_W_DECL} w:val="{half_points}"/>'))
    rpr.append(etree.fromstring(f'<w:szCs {_W_DECL} w:val="{half_points}"/>'))
    normal = _style_el(doc, "Normal")
    for el in normal.findall(qn("w:rPr")):
        normal.remove(el)


BODY_TEXT = ("Comply with the requirements of the Contract Documents and with the referenced standards, and coordinate "
             "the work of this Section with the work of the other trades.")


def build_body_template() -> bytes:
    """The user's template for "match body formatting to template": the scheme of ``build_scheme_example`` (``PRT`` …
    ``PR3``, one list, ``PART %1 -`` / ``%1.%2`` / ``%3.`` / ``%4.`` / ``%5.``), and three styles of its own: ``SCT``
    (the section number line, centred bold), ``TTL`` (the title, centred bold capitals) and ``EOS`` (END OF SECTION,
    centred). Arial 10 pt (the document defaults), Letter, 1 in margins, header and footer 0.5 in from the edge.

    Body: "SECTION 00 00 00" (SCT); "SECTION TITLE" (TTL); GENERAL [PART 1 -]; SUMMARY [1.1]; three PR1 paragraphs of
    body text [A.–C.], a PR2 [1.] under the second; SUBMITTALS [1.2] with two PR1 [A., B.]; PRODUCTS [PART 2 -];
    MATERIALS [2.1] with a PR1 [A.] and a PR3 under a PR2; "END OF SECTION 00 00 00" (EOS)."""
    doc = Document()
    _body_font(doc, "Arial", 20)
    _page(doc, 1440)
    ids = SCHEME_LEVELS
    for k, sid in enumerate(ids):
        rpr = {"PRT": "<w:rPr><w:b/><w:caps/></w:rPr>", "ART": "<w:rPr><w:b/></w:rPr>"}.get(sid, "")
        keep = "<w:keepNext/>" if k < 2 else ""
        nxt = f'<w:next w:val="{ids[k + 1]}"/>' if k + 1 < len(ids) else ""
        _add_style(doc, f'<w:style w:type="paragraph" w:customStyle="1" w:styleId="{sid}"><w:name w:val="{sid}"/>'
                        f'<w:basedOn w:val="Normal"/>{nxt}<w:qFormat/><w:pPr>{keep}<w:numPr><w:ilvl w:val="{k}"/>'
                        f'<w:numId w:val="10"/></w:numPr><w:spacing w:before="120" w:after="0"/></w:pPr>{rpr}</w:style>')
    for sid, name, rpr in (("SCT", "Section Number", "<w:b/>"), ("TTL", "Section Title", "<w:b/><w:caps/>"),
                           ("EOS", "End of Section", "")):
        _add_style(doc, f'<w:style w:type="paragraph" w:customStyle="1" w:styleId="{sid}"><w:name w:val="{name}"/>'
                        f'<w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:before="240" w:after="240"/>'
                        f'<w:jc w:val="center"/></w:pPr><w:rPr>{rpr}</w:rPr></w:style>')
    levels = [_lvl_xml(0, "decimal", "PART %1 -", pstyle="PRT", suff="space", left=0, hanging=0),
              _lvl_xml(1, "decimal", "%1.%2", pstyle="ART", left=720, hanging=720),
              _lvl_xml(2, "upperLetter", "%3.", pstyle="PR1", left=1440, hanging=720),
              _lvl_xml(3, "decimal", "%4.", pstyle="PR2", left=2160, hanging=720),
              _lvl_xml(4, "lowerLetter", "%5.", pstyle="PR3", left=2880, hanging=720)]
    _add_lists(doc, [_abstract_xml(10, levels, "6B6B0010")],
               ['<w:num w:numId="10"><w:abstractNumId w:val="10"/></w:num>'])
    style = {sid: doc.styles[_style_name(doc, sid)] for sid in ids + ["SCT", "TTL", "EOS"]}
    doc.add_paragraph("SECTION 00 00 00", style=style["SCT"])
    doc.add_paragraph("SECTION TITLE", style=style["TTL"])
    for sid, text in (("PRT", "GENERAL"), ("ART", "SUMMARY"), ("PR1", BODY_TEXT), ("PR1", BODY_TEXT),
                      ("PR2", BODY_TEXT), ("PR1", BODY_TEXT), ("ART", "SUBMITTALS"), ("PR1", BODY_TEXT),
                      ("PR1", BODY_TEXT), ("PRT", "PRODUCTS"), ("ART", "MATERIALS"), ("PR1", BODY_TEXT),
                      ("PR2", BODY_TEXT), ("PR3", BODY_TEXT)):
        doc.add_paragraph(text, style=style[sid])
    doc.add_paragraph("END OF SECTION 00 00 00", style=style["EOS"])
    return docx_bytes(doc)


def build_styled_outline(number: str = "21 13 13", title: str = "WET-PIPE SPRINKLER SYSTEMS",
                         table: bool = False) -> bytes:
    """A specification outlined by its OWN styles and list, unlike ``build_body_template`` in every way the button
    matches: styles ``SpPart`` / ``SpArt`` / ``SpPara`` / ``SpSub`` (ids the template does not use) numbered by one
    list — num 20: ``PART %1 -``, ``%1.%2`` with a ZERO-PADDED article ("1.01"), ``%3.``, ``%4.`` —; the section number
    and title in Normal with direct bold and centring; Times New Roman 11 pt; 0.75 in margins. Body paragraphs carry
    direct spacing, indents and alignment, and one a bold word (it stays bold); ``table``: a two-cell table after
    SUBMITTALS holding a Normal paragraph with a direct indent (left as it is).

    Body: "SECTION nn nn nn" (Normal, bold, centred); the title (Normal, bold, centred); GENERAL [PART 1 -]; SUMMARY
    [1.01]; two SpPara [A., B.] (the first with direct spacing and a 0.25 in indent, the second justified with a bold
    "shall"), an SpSub [1.] under B.; SUBMITTALS [1.02] with an SpPara [A.]; PRODUCTS [PART 2 -]; MATERIALS [2.01] with
    an SpPara [A.]; "END OF SECTION nn nn nn" (Normal, centred)."""
    doc = Document()
    _body_font(doc, "Times New Roman", 22)
    _page(doc, 1080)
    ids = ["SpPart", "SpArt", "SpPara", "SpSub"]
    for k, sid in enumerate(ids):
        _add_style(doc, f'<w:style w:type="paragraph" w:customStyle="1" w:styleId="{sid}"><w:name w:val="{sid}"/>'
                        f'<w:basedOn w:val="Normal"/><w:pPr><w:numPr><w:ilvl w:val="{k}"/><w:numId w:val="20"/>'
                        f'</w:numPr></w:pPr></w:style>')
    levels = [_lvl_xml(0, "decimal", "PART %1 -", pstyle="SpPart", suff="space", left=0, hanging=0),
              _lvl_xml(1, "decimalZero", "%1.%2", pstyle="SpArt", left=864, hanging=864),
              _lvl_xml(2, "upperLetter", "%3.", pstyle="SpPara", left=1440, hanging=576),
              _lvl_xml(3, "decimal", "%4.", pstyle="SpSub", left=2016, hanging=576)]
    _add_lists(doc, [_abstract_xml(20, levels, "6B6B0020")],
               ['<w:num w:numId="20"><w:abstractNumId w:val="20"/></w:num>'])
    style = {sid: doc.styles[_style_name(doc, sid)] for sid in ids}
    for text in (f"SECTION {number}", title):
        p = doc.add_paragraph()
        p.alignment = 1
        p.add_run(text).bold = True
    doc.add_paragraph("GENERAL", style=style["SpPart"])
    doc.add_paragraph("SUMMARY", style=style["SpArt"])
    p = doc.add_paragraph(BODY_TEXT, style=style["SpPara"])
    p.paragraph_format.space_after = Pt(12)
    p.paragraph_format.left_indent = Inches(0.25)
    p = doc.add_paragraph("The installer ", style=style["SpPara"])
    p.alignment = 3
    p.add_run("shall").bold = True
    p.add_run(" " + BODY_TEXT)
    doc.add_paragraph(BODY_TEXT, style=style["SpSub"])
    doc.add_paragraph("SUBMITTALS", style=style["SpArt"])
    if table:
        t = doc.add_table(rows=1, cols=2)
        cell = t.cell(0, 0).paragraphs[0]
        cell.add_run("Product data")
        cell.paragraph_format.left_indent = Inches(0.1)
        t.cell(0, 1).paragraphs[0].add_run("Required")
    doc.add_paragraph(BODY_TEXT, style=style["SpPara"])
    doc.add_paragraph("PRODUCTS", style=style["SpPart"])
    doc.add_paragraph("MATERIALS", style=style["SpArt"])
    doc.add_paragraph(BODY_TEXT, style=style["SpPara"])
    p = doc.add_paragraph(f"END OF SECTION {number}")
    p.alignment = 1
    return docx_bytes(doc)


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


# ---- built-in batch 5, tables (docs/BUILTIN_OPS_BATCH5_DESIGN.md §5) ------------------------------------------------

_TBLPR_ORDER = ["tblStyle", "tblpPr", "tblOverlap", "bidiVisual", "tblStyleRowBandSize", "tblStyleColBandSize", "tblW",
                "jc", "tblCellSpacing", "tblInd", "tblBorders", "shd", "tblLayout", "tblCellMar", "tblLook",
                "tblCaption", "tblDescription", "tblPrChange"]


def _tblpr_add(table, xml: str):
    """Add w:tblPr children written as XML to a python-docx table, each in schema order (fixture surgery)."""
    pr = table._tbl.tblPr
    for el in etree.fromstring(f"<w:x {_W_DECL}>{xml}</w:x>"):
        rank = _TBLPR_ORDER.index(etree.QName(el).localname)
        at = next((i for i, c in enumerate(pr) if _TBLPR_ORDER.index(etree.QName(c).localname) > rank), len(pr))
        pr.insert(at, el)
    return pr


def _tcpr_add(cell, xml: str):
    """Append w:tcPr children written as XML to a python-docx cell (give them in schema order, after w:tcW)."""
    tcpr = cell._tc.get_or_add_tcPr()
    for el in etree.fromstring(f"<w:x {_W_DECL}>{xml}</w:x>"):
        tcpr.append(el)
    return tcpr


def _row_first(row, xml: str):
    """Insert row-level elements written as XML (w:tblPrEx, w:trPr) first in a python-docx row, in the given order."""
    for i, el in enumerate(etree.fromstring(f"<w:x {_W_DECL}>{xml}</w:x>")):
        row._tr.insert(i, el)


def _fill(table, rows):
    for r, values in enumerate(rows):
        for c, v in enumerate(values):
            table.cell(r, c).text = v


def build_tables(section_number: str = "21 13 13", title: str = "WET-PIPE SPRINKLER SYSTEMS", *,
                 start_end: bool = False) -> bytes:
    """The batch-5 table fixture (set_table_props / apply_table_style). Two sections (the second landscape, for schedules);
    every table is found by its ordinal (``root.iter(w:tbl)`` per part) and its first row's text. python-docx's template
    underneath: ``TableNormal`` (the default table style: cell margins left/right 108, no borders) and ``TableGrid``
    ("Table Grid": all six border sides single 4, cell margins 108).

    Styles added: ``ArchGrid`` ("Arch Grid", based on TableNormal: tblBorders top, bottom and insideH single 4 — a
    side not asked must be written nil — and ``w:trPr/w:cantSplit`` on: rows that may break need an explicit off);
    ``ArchBanded`` ("Arch Banded", based on TableGrid, a ``w:tblStylePr`` firstRow with ``w:tcPr/w:tcBorders`` —
    conditional borders, flagged); ``ArchHeader`` ("Arch Header", based on TableNormal, ``w:trPr/w:tblHeader`` on);
    ``ArchStartEnd`` (only with ``start_end``: tblBorders spelled start/end).

    Body, section 1: "SECTION …"; table 0 — Table Grid with direct tblBorders (top and bottom double), direct shading
    EEEEEE, 3 rows "Item | Value" / "Riser | 4 in" / "Main | 6 in"; cell (1, 0) has its own tcBorders (bottom single
    12), cell (2, 1) its own tcMar (left 200), cell (0, 0) its own shading D9D9D9. "Valve schedule"; table 1 — no style
    (the default), 3 rows "Valve | Size" / "Gate | 4 in" / "Check | 6 in"; row 0 already carries tblHeader, row 1 cantSplit;
    tblW pct 5000 (100 %); own tblCellMar left/right 115 dxa. "Pipe schedule"; table 2 — ArchGrid, 2 rows "Size |
    Material" / "2 in | Steel". Table 3 — ArchBanded, 3 rows "Banded | Table" / …; row 1 carries a w:tblPrEx with its own
    tblBorders (flagged). Table 4 — no style, 1 row "Outer | cell"; its cell (0, 0) holds table 5 — Table Grid, nested,
    1 row "Nested | table". Table 6 — ArchHeader, 2 rows "Tracked | table"; a pending w:tblPrChange (Jeremy, id 401)
    and, in row 0, a w:trPrChange (id 402). Section break (new page). Section 2 (landscape): "EQUIPMENT SCHEDULE";
    table 7 — Table Grid, 2 rows "Tag | Description" / "FP-1 | Fire pump". With ``start_end``: table 8 — ArchStartEnd,
    own tblBorders start/end and tblCellMar start/end, 1 row "Aliased | sides".

    Header (section 1): table 0 — no style, 1 row "SECTION | {section_number}".
    """
    from docx.enum.section import WD_ORIENT
    doc = Document()
    _add_style(doc, '<w:style w:type="table" w:customStyle="1" w:styleId="ArchGrid"><w:name w:val="Arch Grid"/>'
                    '<w:basedOn w:val="TableNormal"/><w:tblPr><w:tblBorders>'
                    '<w:top w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
                    '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
                    '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="auto"/></w:tblBorders></w:tblPr>'
                    '<w:trPr><w:cantSplit/></w:trPr></w:style>')
    _add_style(doc, '<w:style w:type="table" w:customStyle="1" w:styleId="ArchBanded"><w:name w:val="Arch Banded"/>'
                    '<w:basedOn w:val="TableGrid"/><w:tblPr><w:tblStyleRowBandSize w:val="1"/></w:tblPr>'
                    '<w:tblStylePr w:type="firstRow"><w:rPr><w:b/></w:rPr><w:tcPr><w:tcBorders>'
                    '<w:bottom w:val="single" w:sz="12" w:space="0" w:color="auto"/></w:tcBorders></w:tcPr>'
                    '</w:tblStylePr></w:style>')
    _add_style(doc, '<w:style w:type="table" w:customStyle="1" w:styleId="ArchHeader"><w:name w:val="Arch Header"/>'
                    '<w:basedOn w:val="TableNormal"/><w:trPr><w:tblHeader/></w:trPr></w:style>')
    if start_end:
        _add_style(doc, '<w:style w:type="table" w:customStyle="1" w:styleId="ArchStartEnd"><w:name w:val="Arch Start End"/>'
                        '<w:basedOn w:val="TableNormal"/><w:tblPr><w:tblBorders>'
                        '<w:start w:val="single" w:sz="4" w:space="0" w:color="auto"/></w:tblBorders></w:tblPr></w:style>')

    ht = doc.sections[0].header.add_table(1, 2, doc.sections[0].page_width - doc.sections[0].left_margin
                                          - doc.sections[0].right_margin)
    _fill(ht, [["SECTION", section_number]])
    doc.sections[0].header.add_paragraph("")                      # a header ends with a paragraph

    doc.add_paragraph(f"SECTION {section_number} - {title}")
    t0 = doc.add_table(rows=3, cols=2)
    t0.style = doc.styles["Table Grid"]
    _fill(t0, [["Item", "Value"], ["Riser", "4 in"], ["Main", "6 in"]])
    _tblpr_add(t0, '<w:tblBorders><w:top w:val="double" w:sz="4" w:space="0" w:color="auto"/>'
                   '<w:bottom w:val="double" w:sz="4" w:space="0" w:color="auto"/></w:tblBorders>'
                   '<w:shd w:val="clear" w:color="auto" w:fill="EEEEEE"/>')
    _tcpr_add(t0.cell(1, 0), '<w:tcBorders><w:bottom w:val="single" w:sz="12" w:space="0" w:color="auto"/></w:tcBorders>')
    _tcpr_add(t0.cell(2, 1), '<w:tcMar><w:left w:w="200" w:type="dxa"/></w:tcMar>')
    _tcpr_add(t0.cell(0, 0), '<w:shd w:val="clear" w:color="auto" w:fill="D9D9D9"/>')

    doc.add_paragraph("Valve schedule")
    t1 = doc.add_table(rows=3, cols=2)
    _fill(t1, [["Valve", "Size"], ["Gate", "4 in"], ["Check", "6 in"]])
    w = t1._tbl.tblPr.find(qn("w:tblW"))
    w.set(qn("w:w"), "5000"); w.set(qn("w:type"), "pct")
    _tblpr_add(t1, '<w:tblCellMar><w:left w:w="115" w:type="dxa"/><w:right w:w="115" w:type="dxa"/></w:tblCellMar>')
    _row_first(t1.rows[0], "<w:trPr><w:tblHeader/></w:trPr>")
    _row_first(t1.rows[1], "<w:trPr><w:cantSplit/></w:trPr>")

    doc.add_paragraph("Pipe schedule")
    t2 = doc.add_table(rows=2, cols=2)
    t2.style = doc.styles["Arch Grid"]
    _fill(t2, [["Size", "Material"], ["2 in", "Steel"]])

    t3 = doc.add_table(rows=3, cols=2)
    t3.style = doc.styles["Arch Banded"]
    _fill(t3, [["Banded", "Table"], ["Row", "one"], ["Row", "two"]])
    _row_first(t3.rows[1], '<w:tblPrEx><w:tblBorders><w:top w:val="single" w:sz="8" w:space="0" w:color="auto"/>'
                           '</w:tblBorders></w:tblPrEx>')

    t4 = doc.add_table(rows=1, cols=2)
    _fill(t4, [["Outer", "cell"]])
    t5 = t4.cell(0, 0).add_table(1, 2)
    t5.style = doc.styles["Table Grid"]
    _fill(t5, [["Nested", "table"]])

    t6 = doc.add_table(rows=2, cols=2)
    t6.style = doc.styles["Arch Header"]
    _fill(t6, [["Tracked", "table"], ["Older", "change"]])
    _tblpr_add(t6, '<w:tblPrChange w:id="401" w:author="Jeremy" w:date="2026-09-22T11:00:00Z"><w:tblPr>'
                   '<w:tblW w:w="0" w:type="auto"/></w:tblPr></w:tblPrChange>')
    _row_first(t6.rows[0], '<w:trPr><w:trPrChange w:id="402" w:author="Jeremy" w:date="2026-09-22T11:00:00Z">'
                           '<w:trPr/></w:trPrChange></w:trPr>')

    first = doc.sections[0]
    second = doc.add_section(WD_SECTION.NEW_PAGE)
    second.orientation = WD_ORIENT.LANDSCAPE
    second.page_width, second.page_height = first.page_height, first.page_width
    doc.add_paragraph("EQUIPMENT SCHEDULE")
    t7 = doc.add_table(rows=2, cols=2)
    t7.style = doc.styles["Table Grid"]
    _fill(t7, [["Tag", "Description"], ["FP-1", "Fire pump"]])
    if start_end:
        t8 = doc.add_table(rows=1, cols=2)
        t8.style = doc.styles["Arch Start End"]
        _fill(t8, [["Aliased", "sides"]])
        _tblpr_add(t8, '<w:tblBorders><w:start w:val="single" w:sz="4" w:space="0" w:color="auto"/></w:tblBorders>'
                       '<w:tblCellMar><w:start w:w="100" w:type="dxa"/><w:end w:w="100" w:type="dxa"/></w:tblCellMar>')
    doc.add_paragraph("END OF SECTION")
    return docx_bytes(doc)


# ---- the dirty fixture family (live runs on messy project files) -----------------

DIRTY_KINDS = ("fragmented_runs", "textbox_headers", "nbsp_spaces", "compat_mode", "many_revisions", "protected",
               "libreoffice_like", "empty_body", "styled")

DIRTY_STAMP = "ISSUED FOR PERMIT 2026-01-15"

# CT_Settings child order (ECMA-376 §17.15.1.78) as far as these fixtures write into it; what python-docx's template
# carries after this prefix (characterSpacingControl, compat, rsids, …) stays where it is.
_SETTINGS_ORDER = ["writeProtection", "view", "zoom", "removePersonalInformation", "removeDateAndTime",
                   "doNotDisplayPageBoundaries", "displayBackgroundShape", "printPostScriptOverText",
                   "printFractionalCharacterWidth", "printFormsData", "embedTrueTypeFonts", "embedSystemFonts",
                   "saveSubsetFonts", "saveFormsData", "mirrorMargins", "alignBordersAndEdges",
                   "bordersDoNotSurroundHeader", "bordersDoNotSurroundFooter", "gutterAtTop", "hideSpellingErrors",
                   "hideGrammaticalErrors", "activeWritingStyle", "proofState", "formsDesign", "attachedTemplate",
                   "linkStyles", "stylePaneFormatFilter", "stylePaneSortMethod", "documentType", "mailMerge",
                   "revisionView", "trackRevisions", "doNotTrackMoves", "doNotTrackFormatting", "documentProtection",
                   "autoFormatOverride", "styleLockTheme", "styleLockQFSet", "defaultTabStop", "autoHyphenation"]

_LO_MEMBERS_GONE = ("word/stylesWithEffects.xml", "word/webSettings.xml", "customXml/item1.xml",
                    "customXml/_rels/item1.xml.rels", "customXml/itemProps1.xml", "docProps/thumbnail.jpeg")
_LO_SETTINGS = (f'<w:settings xmlns:w="{W_NS}"><w:zoom w:percent="100"/><w:defaultTabStop w:val="709"/>'
                '<w:autoHyphenation w:val="true"/><w:compat><w:compatSetting w:name="compatibilityMode" '
                'w:uri="http://schemas.microsoft.com/office/word" w:val="15"/></w:compat>'
                '<w:themeFontLang w:val="en-US"/></w:settings>')
_FRAG_TOKEN = re.compile(r"\s+|\w+|[^\w\s]")


def _settings_insert(root, el):
    """Insert a settings child at its CT_Settings position: after the last sibling the schema puts before it."""
    rank = _SETTINGS_ORDER.index(etree.QName(el).localname)
    at = 0
    for i, c in enumerate(root):
        local = etree.QName(c).localname if isinstance(c.tag, str) else ""
        if local in _SETTINGS_ORDER and _SETTINGS_ORDER.index(local) < rank:
            at = i + 1
    root.insert(at, el)


def _set_compat_mode(root, val: str):
    """Word's compatibilityMode in a settings root (python-docx's template says 14 — Word 2010 mode — so every
    fixture built on it is in compatibility mode unless this says otherwise)."""
    compat = root.find(W + "compat")
    if compat is None:
        compat = etree.SubElement(root, W + "compat")
    for cs in compat.findall(W + "compatSetting"):
        if cs.get(W + "name") == "compatibilityMode":
            cs.set(W + "val", val)
            return
    cs = etree.SubElement(compat, W + "compatSetting")
    cs.set(W + "name", "compatibilityMode"); cs.set(W + "uri", "http://schemas.microsoft.com/office/word")
    cs.set(W + "val", val)


def _set_app(root, application: str, version: str):
    """docProps/app.xml's Application and AppVersion (CT_Properties is xsd:all — order-free)."""
    ns = etree.QName(root).namespace
    for tag, val in (("Application", application), ("AppVersion", version)):
        el = root.find("{%s}%s" % (ns, tag))
        if el is None:
            el = etree.SubElement(root, "{%s}%s" % (ns, tag))
        el.text = val


def _strip_rsids(root):
    """Every rsid* attribute and every w:rsids / w:rsid / w:rsidRoot element, gone (as LibreOffice writes nothing
    of the kind)."""
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for k in list(el.attrib):
            if etree.QName(k).localname.startswith("rsid"):
                del el.attrib[k]
    for el in list(root.iter(W + "rsids", W + "rsid", W + "rsidRoot")):
        el.getparent().remove(el)


def _drop_members(data: bytes, names) -> bytes:
    """``data`` without the members ``names``, the relationships that target them (in every .rels member) and their
    content-type overrides (fixture surgery: a package another application never wrote those parts into)."""
    gone = set(names)
    zin = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            if info.filename in gone:
                continue
            payload = zin.read(info.filename)
            if info.filename.endswith(".rels"):
                base = info.filename.split("_rels/")[0]              # "_rels/.rels" → "", "word/_rels/x.rels" → "word/"
                root = etree.fromstring(payload)
                for rel in list(root):
                    target = rel.get("Target") or ""
                    if rel.get("TargetMode") != "External" and posixpath.normpath(posixpath.join(base, target)) in gone:
                        root.remove(rel)
                payload = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            elif info.filename == "[Content_Types].xml":
                root = etree.fromstring(payload)
                for ov in list(root.findall("{%s}Override" % CT_NS)):
                    if (ov.get("PartName") or "").lstrip("/") in gone:
                        root.remove(ov)
                payload = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(zi, payload)
    return out.getvalue()


def _rezip_stored(data: bytes) -> bytes:
    """``data`` with every member stored (no compression), so its headers can be patched byte by byte."""
    zin = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            zi.compress_type = zipfile.ZIP_STORED
            zout.writestr(zi, zin.read(info.filename))
    return out.getvalue()


def _patch_zip_headers(data: bytes, local: int, central: int, fn) -> bytes:
    """Every local file header (``PK\x03\x04``, the 2-byte field at ``local``) and central directory entry
    (``PK\x01\x02``, the field at ``central``) with its field replaced by ``fn(value)`` (fixture surgery on a stored
    zip, whose payloads are text and never carry the signatures)."""
    out = bytearray(data)
    for sig, at in ((b"PK\x03\x04", local), (b"PK\x01\x02", central)):
        i = out.find(sig)
        while i >= 0:
            value, = struct.unpack_from("<H", out, i + at)
            struct.pack_into("<H", out, i + at, fn(value))
            i = out.find(sig, i + 4)
    return bytes(out)


def flag_encrypted(data: bytes) -> bytes:
    """``data`` with the encryption flag bit set on every member, as a password-protected zip carries it: Python's
    ``zipfile`` raises ``RuntimeError`` when a member is read without a password."""
    return _patch_zip_headers(_rezip_stored(data), 6, 8, lambda flags: flags | 0x0001)


def recompress(data: bytes, method: int = 96) -> bytes:
    """``data`` with every member's compression method set to ``method`` (96 is JPEG, which ``zipfile`` does not
    implement: ``NotImplementedError`` when a member is read); the payloads stay stored, so only the method is odd."""
    return _patch_zip_headers(_rezip_stored(data), 8, 10, lambda _method: method)


def _xml_text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _anchored_textbox(inner: str, docpr_id: int, name: str):
    """A run holding a floating text box anchored to its paragraph — the way Word writes a header or footer whose
    content was drawn as a "text box": DrawingML (wps) with the VML fallback, so the paragraphs in ``inner`` exist
    twice. ``inner`` is w:p XML with the w: prefix undeclared."""
    xml = (
        '<w:r xmlns:w="%s" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
        'xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">'
        '<mc:AlternateContent><mc:Choice Requires="wps"><w:drawing>'
        '<wp:anchor distT="0" distB="0" distL="114300" distR="114300" simplePos="0" relativeHeight="251659264" '
        'behindDoc="0" locked="0" layoutInCell="1" allowOverlap="1"><wp:simplePos x="0" y="0"/>'
        '<wp:positionH relativeFrom="margin"><wp:posOffset>0</wp:posOffset></wp:positionH>'
        '<wp:positionV relativeFrom="paragraph"><wp:posOffset>0</wp:posOffset></wp:positionV>'
        '<wp:extent cx="5943600" cy="457200"/><wp:effectExtent l="0" t="0" r="0" b="0"/><wp:wrapNone/>'
        '<wp:docPr id="%d" name="%s"/><wp:cNvGraphicFramePr/>'
        '<a:graphic><a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
        '<wps:wsp><wps:cNvSpPr txBox="1"/><wps:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="5943600" cy="457200"/>'
        '</a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></wps:spPr>'
        '<wps:txbx><w:txbxContent>%s</w:txbxContent></wps:txbx>'
        '<wps:bodyPr rot="0" vert="horz" wrap="square" lIns="91440" tIns="45720" rIns="91440" bIns="45720" '
        'anchor="t" anchorCtr="0"><a:noAutofit/></wps:bodyPr></wps:wsp></a:graphicData></a:graphic></wp:anchor>'
        '</w:drawing></mc:Choice><mc:Fallback><w:pict><v:shape id="%s" o:spid="_x0000_s%d" type="#_x0000_t202" '
        'style="position:absolute;margin-left:0;margin-top:0;width:468pt;height:36pt;z-index:251659264;'
        'mso-position-horizontal-relative:margin" filled="f" stroked="f"><v:textbox><w:txbxContent>%s</w:txbxContent>'
        '</v:textbox></v:shape></w:pict></mc:Fallback></mc:AlternateContent></w:r>'
    ) % (W_NS, docpr_id, name, inner, name, 1024 + docpr_id, inner)
    return etree.fromstring(xml)


def _dirty_lines(section_number: str, title: str, nbsp: bool = False):
    """The one specification every dirty kind shows: the SECTION line, the title, three PARTs with typed article
    numbers and lettered paragraphs, END OF SECTION. ``nbsp``: a non-breaking space between every number and its
    unit (and inside "NFPA 13"), and runs of two spaces where a typist doubled them."""
    u = " " if nbsp else " "
    d = "  " if nbsp else " "
    return [
        f"SECTION {section_number}",
        title,
        "PART 1 - GENERAL",
        "1.01\tSUMMARY",
        f"A.\tSection includes wet-pipe sprinkler systems, piping, valves and hangers for the data halls.",
        f"B.\tThe Owner's insurer is FM Global;{d}all equipment shall be FM Approved.",
        "1.02\tREFERENCES",
        f"A.\tNFPA{u}13 (2025), Standard for the Installation of Sprinkler Systems.",
        "B.\tFM Global Property Loss Prevention Data Sheet 2-0.",
        "PART 2 - PRODUCTS",
        "2.01\tPIPE AND FITTINGS",
        f"A.\tPipe shall be black steel, Schedule 40,{d}per ASTM A53.",
        f"B.\tWorking pressure 175{u}psi;{d}test pressure 200{u}psi for 2{u}hours.",
        "2.02\tSPRINKLERS",
        f"A.\tQuick-response pendent sprinklers, K-factor 5.6, 155{u}F.",
        "PART 3 - EXECUTION",
        "3.01\tINSTALLATION",
        f"A.\tInstall per NFPA{u}13 and the hydraulic calculations.",
        f"B.\tHangers at 12{u}ft maximum spacing for 4{u}in mains.{d}Trapeze hangers for mains over 4{u}in.",
        "END OF SECTION",
    ]


def _plain_paragraph(doc, text: str):
    return doc.add_paragraph(text)


def _fragmented_writer():
    """A paragraph writer that puts every word, every space and every punctuation mark in its own run — some words
    letter by letter — each run stamped with different rsid-like revision-save ids, as a paragraph looks after years
    of edits in Word."""
    k = [0]

    def write(doc, text: str):
        p = doc.add_paragraph()
        p._p.set(qn("w:rsidR"), f"{(0x0041A000 + 0x9B1 * k[0]) & 0xFFFFFFFF:08X}")
        p._p.set(qn("w:rsidRDefault"), "00B47730")
        for tok in _FRAG_TOKEN.findall(text):
            pieces = list(tok) if (tok.isalpha() and len(tok) > 3 and k[0] % 7 == 3) else [tok]
            for piece in pieces:
                k[0] += 1
                r = p.add_run(piece)
                r._r.set(qn("w:rsidR"), f"{(0x00A1C000 + 0x1F3 * k[0]) & 0xFFFFFFFF:08X}")
                if k[0] % 3 == 0:
                    r._r.set(qn("w:rsidRPr"), f"{(0x0062B000 + 0x2D1 * k[0]) & 0xFFFFFFFF:08X}")
        return p
    return write


def _tracked_paragraph(doc, k: int):
    """A lettered paragraph of four runs carrying one pending insertion and one pending deletion (INS:/DEL: markers
    that ``_post_process`` turns into Jeremy's w:ins and Jack's w:del) — few enough runs that hundreds of them leave
    the body's run fragmentation ratio under 4 (the ``fragmented_runs`` kind's own signal)."""
    p = doc.add_paragraph()
    p.add_run(f"{'ABCDEFGH'[k % 8]}.\tThe Owner's insurer ")
    p.add_run("INS:requires ")
    p.add_run("DEL:recommends ")
    p.add_run("FM Approved sprinklers per NFPA 13 (2025).")
    return p


def _dirty_doc(section_number: str, title: str, lines, *, textbox_hf: bool = False, writer=None,
               tracked_per_line: int = 0, styled_lo: bool = False):
    """One single-section spec in Normal, Arial 10: the header "SECTION <n> - <title><tab><stamp>", the footer
    "<n> - <PAGE>", the body ``lines`` written by ``writer`` (``_plain_paragraph`` unless told otherwise).
    ``textbox_hf``: the header and footer paragraphs hold nothing but a text box with that content inside it.
    ``tracked_per_line``: that many tracked paragraphs (``_tracked_paragraph``) after every body line.
    ``styled_lo``: PART lines in Heading 1, everything else in a "Text Body" style with LibreOffice's id."""
    doc = Document()
    st = doc.styles["Normal"]; st.font.name = "Arial"; st.font.size = Pt(10)
    if styled_lo:
        _add_style(doc, '<w:style w:type="paragraph" w:styleId="TextBody"><w:name w:val="Text Body"/>'
                        '<w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="140" w:line="276" w:lineRule="auto"/>'
                        '</w:pPr></w:style>')
    sec = doc.sections[0]
    sec.header.is_linked_to_previous = False
    sec.footer.is_linked_to_previous = False
    hp, fp = sec.header.paragraphs[0], sec.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    if textbox_hf:
        hp._p.append(_anchored_textbox(
            f'<w:p><w:r><w:t xml:space="preserve">SECTION {_xml_text(section_number)} - {_xml_text(title)}</w:t></w:r>'
            f'<w:r><w:tab/></w:r><w:r><w:t>{DIRTY_STAMP}</w:t></w:r></w:p>', 901, "Text Box 1"))
        fp._p.append(_anchored_textbox(
            f'<w:p><w:pPr><w:jc w:val="right"/></w:pPr><w:r><w:t xml:space="preserve">{_xml_text(section_number)} - '
            f'</w:t></w:r><w:fldSimple w:instr=" PAGE "><w:r><w:t>1</w:t></w:r></w:fldSimple></w:p>', 902, "Text Box 2"))
    else:
        hp.add_run(f"SECTION {section_number} - {title}"); hp.add_run("\t"); hp.add_run(DIRTY_STAMP)
        fp.add_run(f"{section_number} - ")
        _page_field(fp)
    write = writer or _plain_paragraph
    k = 0
    for line in lines:
        if styled_lo:
            doc.add_paragraph(line, style="Heading 1" if line.startswith("PART ") else "Text Body")
        else:
            write(doc, line)
        for _ in range(tracked_per_line):
            _tracked_paragraph(doc, k); k += 1
    return doc


def build_dirty(kind: str, section_number: str = "21 13 13", title: str = "WET-PIPE SPRINKLER SYSTEMS") -> bytes:
    """The dirty fixture family: one valid .docx per ``kind`` in ``DIRTY_KINDS``, each the same recognizable
    specification (``_dirty_lines``: SECTION line, title, PART/article/paragraph text, END OF SECTION; a header with
    the section line and the issue stamp ``DIRTY_STAMP``, a footer with the page number) carrying exactly one
    deliberate defect family — the shapes real project files bring. python-docx's template underneath says
    compatibilityMode 14 by itself; every kind but ``compat_mode`` is set to 15 (current Word) so that one kind alone
    is in compatibility mode.

    * ``fragmented_runs`` — every word, space and punctuation mark in its own w:r (some words letter by letter), each
      run with different rsid-like ids: ``body_hygiene.run_fragmentation_ratio`` far above 4, the visible text normal;
    * ``textbox_headers`` — the header's and the footer's content lives inside a floating text box
      (mc:AlternateContent with w:txbxContent, DrawingML and VML copies): the fingerprint reports text boxes in the
      header and footer parts; the issue stamp sits in the header's box;
    * ``nbsp_spaces`` — a non-breaking space between numbers and units (175 psi, 4 in, NFPA 13) and doubled spaces
      after punctuation: ``has_nbsp`` and ``has_multi_space``;
    * ``compat_mode`` — settings.xml's compatibilityMode 14 (Word 2010 mode; app.xml says AppVersion 14.0000);
    * ``many_revisions`` — eight tracked paragraphs after every body line, one of Jeremy's pending insertions and
      one of Jack's pending deletions each: 320 revisions spread through the body (the run fragmentation ratio stays
      under 4: that is ``fragmented_runs``'s signal, not this kind's); trackRevisions on;
    * ``protected`` — ``w:documentProtection w:edit="readOnly" w:enforcement="1"`` in settings.xml;
    * ``libreoffice_like`` — as LibreOffice 7.6 writes a file: no rsid attribute or element anywhere, body paragraphs
      in "Text Body" (style id ``TextBody``) and PART lines in Heading 1 (id ``Heading1``), docProps/app.xml
      Application "LibreOffice/7.6…", a settings.xml of five elements, and none of Word's extra parts
      (stylesWithEffects, webSettings, customXml, the thumbnail); the theme part stays (the styles' fonts name it);
    * ``empty_body`` — the body holds one empty paragraph and the sectPr, nothing else (the header still names the
      section);
    * ``styled`` — the well-formed styled specification (``build_styles``): the clean end of the range.
    """
    if kind not in DIRTY_KINDS:
        raise ValueError(f"unknown dirty kind {kind!r}; one of {DIRTY_KINDS}")
    lines = _dirty_lines(section_number, title, nbsp=(kind == "nbsp_spaces"))
    if kind == "styled":
        data = build_styles(section_number, title)
    elif kind == "empty_body":
        doc = _dirty_doc(section_number, title, [])
        doc.add_paragraph()
        data = docx_bytes(doc)
    elif kind == "fragmented_runs":
        data = docx_bytes(_dirty_doc(section_number, title, lines, writer=_fragmented_writer()))
    elif kind == "textbox_headers":
        data = docx_bytes(_dirty_doc(section_number, title, lines, textbox_hf=True))
    elif kind == "many_revisions":
        data = _post_process(docx_bytes(_dirty_doc(section_number, title, lines, tracked_per_line=8)))
    elif kind == "libreoffice_like":
        data = docx_bytes(_dirty_doc(section_number, title, lines, styled_lo=True))
    else:                                       # nbsp_spaces, compat_mode, protected: the plain spec, settings apart
        data = docx_bytes(_dirty_doc(section_number, title, lines))

    if kind == "libreoffice_like":
        zin = zipfile.ZipFile(io.BytesIO(data))
        edits = {n: _strip_rsids for n in zin.namelist() if n.endswith(".xml") and n not in _LO_MEMBERS_GONE}

        def settings(root):
            _strip_rsids(root)
            for c in list(root):
                root.remove(c)
            for c in etree.fromstring(_LO_SETTINGS):
                root.append(c)

        def app(root):
            _set_app(root, "LibreOffice/7.6.4.1$Linux_X86_64 LibreOffice_project/60", "15.0000")
        edits["word/settings.xml"] = settings
        edits["docProps/app.xml"] = app
        return _drop_members(patch_docx(data, edits), _LO_MEMBERS_GONE)

    def settings(root):
        _set_compat_mode(root, "14" if kind == "compat_mode" else "15")
        if kind == "protected":
            dp = etree.Element(W + "documentProtection")
            dp.set(W + "edit", "readOnly"); dp.set(W + "enforcement", "1")
            _settings_insert(root, dp)
        if kind == "many_revisions" and root.find(W + "trackRevisions") is None:
            _settings_insert(root, etree.Element(W + "trackRevisions"))

    def app(root):
        _set_app(root, "Microsoft Office Word", "14.0000" if kind == "compat_mode" else "16.0000")
    return patch_docx(data, {"word/settings.xml": settings, "docProps/app.xml": app})



def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--count", type=int, default=len(SPECS))
    ap.add_argument("--rich", action="store_true", help="build the NL-editor op fixture (build_rich) instead")
    ap.add_argument("--template", action="store_true", help="write the header/footer template fixture only")
    ap.add_argument("--reference", action="store_true", help="write the template-spec fixture (an architect's spec) only")
    ap.add_argument("--styles-template", action="store_true",
                    help="write the office styles template (map_styles / apply_style) only")
    ap.add_argument("--numbering-scheme", action="store_true",
                    help="write the numbering scheme example file (convert_numbering / convert_typed_numbers) only")
    ap.add_argument("--dirty", action="store_true",
                    help="write the dirty fixture family (dirty-<kind>.docx for every kind in DIRTY_KINDS) only")
    args = ap.parse_args(argv)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    if args.dirty:
        for kind in DIRTY_KINDS:
            (out / f"dirty-{kind}.docx").write_bytes(build_dirty(kind))
        print(f"wrote {len(DIRTY_KINDS)} dirty fixtures to {out}")
        return
    if args.numbering_scheme:
        (out / "NUMBERING SCHEME.docx").write_bytes(build_scheme_example())
        print(f"wrote {out / 'NUMBERING SCHEME.docx'}")
        return
    if args.styles_template:
        (out / "OFFICE STYLES.docx").write_bytes(build_style_template())
        print(f"wrote {out / 'OFFICE STYLES.docx'}")
        return
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
