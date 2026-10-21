"""Fixtures for the table structure ops of built-in batch 6 (F5 — delete_tables, delete_table_rows, insert_table_rows,
insert_table; resolve_tracked_changes on rows): tests/test_table_structure.py.

``build_structure()`` — a two-section specification whose tables cover what the ops decide on. Every table is found by
its ordinal (``root.iter(w:tbl)`` of its part) and its first row's text:

* body, section 1: "SECTION 23 05 93 - TESTING, ADJUSTING, AND BALANCING"; "1.1 SCHEDULES";
  table 0 — "Table Grid", 4 rows "Tag | Description | Capacity" (a repeating header row: own ``w:tblHeader``, bold,
  shaded cells D9D9D9, row ``w:cantSplit``) / "FP-1 | Fire pump | 500 gpm" / "FP-2 | Jockey pump | 25 gpm" /
  "AC-1 | Air compressor | 10 cfm"; the body rows' text Arial 9 pt, their first cell centred (``w:jc``) with a
  ``w:cnfStyle`` (Word's cache, never copied), row 1 ``w:trHeight`` 360;
  "Zone schedule"; table 1 — "Table Grid", 5 rows: "Zone | Area | Notes" / "Z1 | North | Shared" (column 2 merges down:
  ``w:vMerge restart``) / "Z2 | South | " (continue) / "Z3 | East | " (continue) / "Totals | 3" (its first cell spans
  two columns);
  "Valve schedule"; table 2 — no style, 2 rows "Valve | Detail" / "Gate | …" whose second cell holds table 3 — nested,
  "Table Grid", 2 rows "Size | 4 in" / "Rating | 175 psi" — and a paragraph "See detail." after it;
  "Reference table"; table 4 — "Arch Header" (its style's ``w:trPr`` repeats every row as a header row), 2 rows
  "Ref | Value" / "R-1 | First"; a bookmark "_RefTagList" starts in row 1's first cell and ends in the paragraph after
  the table ("The tags above."), which holds a REF field to it in a later paragraph ("See the tags.");
  "1.2 NOTES"; a section break (new page, landscape);
* section 2: "EQUIPMENT"; table 5 — "Table Grid", 2 rows "Item | Count" / "Pumps | 2"; "END OF SECTION".
* header: table 0 — no style, 1 row "SECTION | 23 05 93"; a paragraph after it.
"""
from __future__ import annotations

import io
import zipfile
from typing import Any, List

from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from lxml import etree

from make_fixtures import _add_style, _fill, _row_first, _tcpr_add, docx_bytes

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = "{%s}" % W_NS
_DECL = f'xmlns:w="{W_NS}"'
TITLE = "SECTION 23 05 93 - TESTING, ADJUSTING, AND BALANCING"


def _xml(text: str) -> List[Any]:
    return list(etree.fromstring(f"<w:x {_DECL}>{text}</w:x>"))


def _run_props(cell, xml: str) -> None:
    for p in cell.paragraphs:
        for r in p.runs:
            rpr = r._r.get_or_add_rPr()
            for el in _xml(xml):
                rpr.append(el)


def _fld_char(paragraph, kind: str) -> None:
    fc = OxmlElement("w:fldChar")
    fc.set(qn("w:fldCharType"), kind)
    paragraph.add_run()._r.append(fc)


def _field(paragraph, instr: str, result: str) -> None:
    """A complex field: begin, its instruction, separate, its cached result, end — each in a run of its own."""
    _fld_char(paragraph, "begin")
    it = OxmlElement("w:instrText")
    it.set(qn("xml:space"), "preserve")
    it.text = instr
    paragraph.add_run()._r.append(it)
    _fld_char(paragraph, "separate")
    paragraph.add_run(result)
    _fld_char(paragraph, "end")


def build_structure() -> bytes:
    doc = Document()
    _add_style(doc, '<w:style w:type="table" w:customStyle="1" w:styleId="ArchHeader"><w:name w:val="Arch Header"/>'
                    '<w:basedOn w:val="TableNormal"/><w:trPr><w:tblHeader/></w:trPr></w:style>')
    sec = doc.sections[0]
    ht = sec.header.add_table(1, 2, sec.page_width - sec.left_margin - sec.right_margin)
    _fill(ht, [["SECTION", "23 05 93"]])
    sec.header.add_paragraph("")

    doc.add_paragraph(TITLE)
    doc.add_paragraph("1.1 SCHEDULES")
    t0 = doc.add_table(rows=4, cols=3)
    t0.style = doc.styles["Table Grid"]
    _fill(t0, [["Tag", "Description", "Capacity"], ["FP-1", "Fire pump", "500 gpm"], ["FP-2", "Jockey pump", "25 gpm"],
               ["AC-1", "Air compressor", "10 cfm"]])
    _row_first(t0.rows[0], "<w:trPr><w:cantSplit/><w:tblHeader/></w:trPr>")
    for c in range(3):
        _tcpr_add(t0.cell(0, c), '<w:shd w:val="clear" w:color="auto" w:fill="D9D9D9"/>')
        _run_props(t0.cell(0, c), "<w:b/>")
    for r in range(1, 4):
        for c in range(3):
            _run_props(t0.cell(r, c), '<w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:sz w:val="18"/>')
        ppr = t0.cell(r, 0).paragraphs[0]._p.get_or_add_pPr()
        for el in _xml('<w:jc w:val="center"/><w:cnfStyle w:val="000000100000"/>'):     # schema order
            ppr.append(el)
    _row_first(t0.rows[1], '<w:trPr><w:trHeight w:val="360"/></w:trPr>')

    doc.add_paragraph("Zone schedule")
    t1 = doc.add_table(rows=5, cols=3)
    t1.style = doc.styles["Table Grid"]
    _fill(t1, [["Zone", "Area", "Notes"], ["Z1", "North", "Shared"], ["Z2", "South", ""], ["Z3", "East", ""],
               ["Totals", "", "3"]])
    _tcpr_add(t1.cell(1, 2), '<w:vMerge w:val="restart"/>')
    _tcpr_add(t1.cell(2, 2), '<w:vMerge/>')
    _tcpr_add(t1.cell(3, 2), '<w:vMerge/>')
    last = t1.rows[4]._tr
    tcs = last.findall(qn("w:tc"))
    tcs[0].get_or_add_tcPr().append(_xml('<w:gridSpan w:val="2"/>')[0])
    last.remove(tcs[1])

    doc.add_paragraph("Valve schedule")
    t2 = doc.add_table(rows=2, cols=2)
    _fill(t2, [["Valve", "Detail"], ["Gate", ""]])
    cell = t2.cell(1, 1)
    t3 = cell.add_table(2, 2)                           # python-docx: [the cell's empty p, the table, a new empty p]
    t3.style = doc.styles["Table Grid"]
    _fill(t3, [["Size", "4 in"], ["Rating", "175 psi"]])
    cell.paragraphs[-1].text = "See detail."
    first = cell.paragraphs[0]._p
    first.getparent().remove(first)

    doc.add_paragraph("Reference table")
    t4 = doc.add_table(rows=2, cols=2)
    t4.style = doc.styles["Arch Header"]
    _fill(t4, [["Ref", "Value"], ["R-1", "First"]])
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), "77")
    start.set(qn("w:name"), "_RefTagList")
    holder = t4.cell(1, 0).paragraphs[0]._p
    holder.insert(1 if holder.pPr is not None else 0, start)
    after = doc.add_paragraph("The tags above.")
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), "77")
    after._p.append(end)
    see = doc.add_paragraph("See the tags: ")
    _field(see, " REF _RefTagList \\h ", "R-1")
    doc.add_paragraph("1.2 NOTES")

    first_section = doc.sections[0]
    second = doc.add_section(WD_SECTION.NEW_PAGE)
    second.orientation = WD_ORIENT.LANDSCAPE
    second.page_width, second.page_height = first_section.page_height, first_section.page_width
    doc.add_paragraph("EQUIPMENT")
    t5 = doc.add_table(rows=2, cols=2)
    t5.style = doc.styles["Table Grid"]
    _fill(t5, [["Item", "Count"], ["Pumps", "2"]])
    doc.add_paragraph("END OF SECTION")
    return docx_bytes(doc)


def xlsx(rows: List[List[Any]], sheet: str = "Pumps") -> bytes:
    """A minimal workbook of one sheet: text as shared strings, numbers as numbers in the General format, a ``None`` a
    cell that carries formatting only (an empty cell Excel keeps)."""
    strings: List[str] = []
    out = []
    for r, row in enumerate(rows, 1):
        cells = []
        for c, v in enumerate(row):
            ref = f"{chr(65 + c)}{r}"
            if v is None:
                cells.append(f'<c r="{ref}" s="0"/>')
            elif isinstance(v, str):
                strings.append(v)
                cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
            else:
                cells.append(f'<c r="{ref}"><v>{v}</v></c>')
        out.append(f'<row r="{r}">{"".join(cells)}</row>')
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    files = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                       'relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets><sheet name="Notes" sheetId="1" '
                           f'r:id="rId9"/><sheet name="{sheet}" sheetId="2" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                                      'relationships"><Relationship Id="rId1" Type="x/worksheet" '
                                      'Target="worksheets/sheet1.xml"/><Relationship Id="rId9" Type="x/worksheet" '
                                      'Target="worksheets/sheet0.xml"/><Relationship Id="rId2" Type="x/sharedStrings" '
                                      'Target="sharedStrings.xml"/><Relationship Id="rId3" Type="x/styles" '
                                      'Target="styles.xml"/></Relationships>',
        "xl/worksheets/sheet0.xml": f'<worksheet xmlns="{main}"><sheetData/></worksheet>',
        "xl/worksheets/sheet1.xml": f'<worksheet xmlns="{main}"><sheetData>{"".join(out)}</sheetData></worksheet>',
        "xl/sharedStrings.xml": f'<sst xmlns="{main}">' + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>",
        "xl/styles.xml": f'<styleSheet xmlns="{main}"><cellXfs><xf numFmtId="0"/></cellXfs></styleSheet>',
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    return buf.getvalue()
