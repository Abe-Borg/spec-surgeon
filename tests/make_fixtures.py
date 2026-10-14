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
are its fill rules. ``build()``'s keyword options make templating targets: ``footer_revision`` (Jeremy's pending
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
          footer_distance_twips: int | None = None, body_picture: bool = False) -> bytes:
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


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--count", type=int, default=len(SPECS))
    ap.add_argument("--rich", action="store_true", help="build the NL-editor op fixture (build_rich) instead")
    ap.add_argument("--template", action="store_true", help="write the header/footer template fixture only")
    args = ap.parse_args(argv)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
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
