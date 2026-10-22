"""What a custom method may format (docs/PLAN-2026-10-22-custom-envelope.md, Option A).

A1 — the limits say what the checker proves. ``CUSTOM_TRANSFORM_LIMITS`` (2026-10-17) listed highlight under "cannot",
though V10's ``properties`` kind has verified highlight, superscript and subscript, hidden text, character spacing and
double strikethrough since set_properties grew them (2026-10-21). A custom method picks its targets by its own logic
and names them (an ``index_range`` per paragraph); the kind checks those got exactly the asked property and nothing
else changed. Each scripted method here runs through the real trial: the whitelist, the sandbox, ``hard_limits`` and
the suite."""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from docx import Document

from make_fixtures import build
from spec_surgeon import codegen
from spec_surgeon.catalog import CUSTOM_TRANSFORM_LIMITS, op_catalog
from spec_surgeon.codegen import check_source, trial
from spec_surgeon.ops import get_op
from spec_surgeon.verify import props as vprops

HIGHLIGHT_OP = '''
from __future__ import annotations
from lxml import etree
from spec_surgeon.ops.base import Op, JobException
from spec_surgeon.package import W
from spec_surgeon.ooxml import RPR_ORDER, insert_in_order, paragraphs, block_full_text, run_rpr, mark_rpr
from spec_surgeon.ops.selectors import DEFAULT_PRESERVE_FONTS


class HighlightInsurerParagraphs(Op):
    name = "highlight_insurer_paragraphs"
    mode = "formatting"
    description = "Highlight the paragraphs that name the insurer, but not one holding a field or a tracked change."
    params_schema = {"type": "object", "properties": {"color": {"type": "string"}, "words": {"type": "string"}}}
    select_schema = {}

    def plan(self, pkg, select, params):
        color, words = params.get("color", "yellow"), params.get("words", "FM Global")
        main = pkg.main_part()
        frag = self.fragment(select, params)
        chosen = []
        for bi, p in enumerate(paragraphs(pkg.xml(main))):
            if words not in block_full_text(p):
                continue
            if p.find(".//" + W + "fldChar") is not None or p.find(".//" + W + "ins") is not None \\
                    or p.find(".//" + W + "del") is not None:
                continue
            runs = [r for r in p.iter(W + "r") if any(c.tag != W + "rPr" for c in r)]
            frag.property_assertions.append({
                "kind": "properties", "target": "direct", "scope": ["document"], "parts": [main],
                "select": {"runs": "all", "paragraphs": {"index_range": [bi, bi + 1]}},
                "set": {"run": {"highlight": color}, "paragraph": {}}, "preserve_fonts": list(DEFAULT_PRESERVE_FONTS),
                "skip_sym_runs": True, "in_revisions": "include", "include_paragraph_marks": True, "tracked": False,
                "author": None, "expected_runs": len(runs), "expected_marks": 1, "expected_paragraphs": 0,
                "expected_shown_marks": 0})
            frag.touches.append((main, str(bi), "rPr"))
            chosen.append(bi)
        if not chosen:
            raise JobException("no paragraph names the insurer", "not-found")
        frag.intended_parts = [main]
        frag.transform_data = {"main": main, "paragraphs": chosen, "color": color}
        frag.geometry_reference = "source"
        return frag

    def apply(self, pkg, frag):
        td = frag.transform_data
        root = pkg.xml(td["main"])
        pars = paragraphs(root)
        for bi in td["paragraphs"]:
            p = pars[bi]
            targets = [run_rpr(r) for r in p.iter(W + "r") if any(c.tag != W + "rPr" for c in r)]
            targets.append(mark_rpr(p, create=True))
            for rpr in targets:
                h = rpr.find(W + "highlight")
                if h is None:
                    h = insert_in_order(rpr, etree.Element(W + "highlight"), RPR_ORDER)
                h.set(W + "val", td["color"])
        pkg.write_xml(td["main"], root)
'''

APPLY_LOOP = 'for bi in td["paragraphs"]:'
SET_VALUE = 'h.set(W + "val", td["color"])'
PLANTED = {
    # the colour written is not the one asserted
    "a wrong colour": HIGHLIGHT_OP.replace(SET_VALUE, 'h.set(W + "val", "green")'),
    # a paragraph the plan never named highlighted too
    "an unlisted paragraph": HIGHLIGHT_OP.replace(APPLY_LOOP, 'for bi in td["paragraphs"] + [td["paragraphs"][-1] + 1]:'),
    # a word changed beside the formatting
    "a changed word": HIGHLIGHT_OP.replace(
        SET_VALUE, SET_VALUE + '\n            for t in p.iter(W + "t"):\n'
                               '                t.text = (t.text or "").replace("FM Global", "FM Approvals")'),
}


@pytest.fixture
def spec(tmp_path) -> Path:
    f = tmp_path / "21 05 00 - COMMON WORK RESULTS.docx"
    f.write_bytes(build("21 05 00", "COMMON WORK RESULTS"))
    return f


def highlighted(docx: bytes):
    out = []
    for p in Document(io.BytesIO(docx)).paragraphs:
        colours = {r.font.highlight_color for r in p.runs if r.text}
        if colours and None not in colours:
            out.append(p.text[:2])
    return out


def test_a_custom_method_highlights_the_paragraphs_it_picks(spec, tmp_path):
    assert check_source(HIGHLIGHT_OP) == []
    before = spec.read_bytes()
    t = trial(HIGHLIGHT_OP, spec, {}, {"color": "yellow"}, tmp_path / "sb")
    assert t.passed, t.problems
    assert "properties" in t.record.get("V10").summary
    # A. and B. name the insurer; C. holds Jeremy's insertion and E. a field: the method's own logic left them
    assert highlighted(Path(t.output).read_bytes()) == ["A.", "B."]
    assert spec.read_bytes() == before


@pytest.mark.parametrize("case", sorted(PLANTED))
def test_a_wrong_custom_method_fails_the_trial(case, spec, tmp_path):
    t = trial(PLANTED[case], spec, {}, {"color": "yellow"}, tmp_path / "sb")
    assert not t.passed, case
    assert any(p.startswith(("V10 FAIL", "V2 FAIL")) for p in t.problems), t.problems


# what the `properties` family's words name, per property the kind takes (set_properties' own parameters)
WORDS = {"b": "bold", "i": "italic", "caps": "caps", "smallCaps": "small caps", "strike": "strikethrough",
         "dstrike": "double strikethrough", "u": "underline", "color": "text colour", "vertAlign": "superscript and "
         "subscript", "vanish": "hidden text", "spacing": "character spacing", "highlight": "highlight",
         "keepNext": "keep-with-next", "keepLines": "keep lines together", "widowControl": "widow control",
         "pageBreakBefore": "page break before"}
SAMPLES = {"u": "single", "color": "FF0000", "vertAlign": "superscript", "highlight": "yellow", "spacing": 1}


def test_the_properties_family_names_every_property_the_kind_verifies():
    family = next(words for words, kind in CUSTOM_TRANSFORM_LIMITS["families"].items() if kind == "properties")
    schema = get_op("set_properties").params_schema["properties"]
    run_keys, para_keys = list(schema["run"]["properties"]), list(schema["paragraph"]["properties"])
    assert set(WORDS) == set(run_keys) | set(para_keys), "a property was added: name it in the family and here"
    for k in run_keys:
        assert vprops._v_run_elements({k: SAMPLES.get(k, True)}), k          # the kind verifies it
        assert WORDS[k] in family, k
    for k in para_keys:
        assert vprops._v_para_elements({k: True}), k
        assert WORDS[k] in family, k
    assert set(CUSTOM_TRANSFORM_LIMITS["families"].values()) <= set(vprops.KINDS)


def test_highlight_is_no_longer_out_of_reach_and_the_writer_is_told():
    assert not any("highlight" in c for c in CUSTOM_TRANSFORM_LIMITS["cannot"])
    assert "highlight of selected text" in codegen.system_prompt()
    assert "highlight of selected text" in str(op_catalog(examples=False)["custom_transform_limits"])


# ---- A2 — table appearance: table_props and table_cells, the targets named by position -----------------------------

SHADE_OP = '''
from __future__ import annotations
from lxml import etree
from spec_surgeon.ops.base import Op, JobException
from spec_surgeon.package import W
from spec_surgeon.ooxml import TCPR_ORDER, insert_in_order, block_full_text


def own(el, tag):
    return [c for c in el if c.tag == W + tag]


def first_cell(tbl):
    rows = own(tbl, "tr")
    cells = own(rows[0], "tc") if rows else []
    return " ".join(block_full_text(p) for p in own(cells[0], "p")).strip() if cells else None


class ShadeEveryOtherRow(Op):
    name = "shade_every_other_row"
    mode = "formatting"
    description = "Shade every other row of the revision tables (the tables whose first cell reads Rev)."
    params_schema = {"type": "object", "properties": {"fill": {"type": "string"}, "first_cell": {"type": "string"}}}
    select_schema = {}

    def plan(self, pkg, select, params):
        fill, first = params.get("fill", "D9D9D9").upper(), params.get("first_cell", "Rev")
        main = pkg.main_part()
        frag = self.fragment(select, params)
        targets = []
        want = {W + "val": "clear", W + "color": "auto", W + "fill": fill}
        for k, tbl in enumerate(pkg.xml(main).iter(W + "tbl")):
            if first_cell(tbl) != first:
                continue
            rows = own(tbl, "tr")
            for r in range(1, len(rows), 2):
                cells = []
                for c, tc in enumerate(own(rows[r], "tc")):
                    pr = tc.find(W + "tcPr")
                    shd = pr.find(W + "shd") if pr is not None else None
                    if shd is None or dict(shd.attrib) != want:
                        cells.append(c)
                frag.property_assertions.append({
                    "kind": "table_cells", "parts": [main],
                    "select": {"scope": ["document"], "tables": {"index_range": [k, k]},
                               "rows": {"index_range": [r, r]}},
                    "params": {"shading": fill}, "expected_tables": 1 if cells else 0,
                    "expected_cells": len(cells), "expected_columns": 0,
                    "changed": {main: {"cells": [[k, r, c, ["shd"]] for c in cells]}} if cells else {}})
                targets += [[k, r, c] for c in cells]
                frag.touches.append((main, "tr:" + str(k) + ":" + str(r), "tcPr"))
        if not frag.property_assertions:
            raise JobException("no revision table in this file", "not-found")
        frag.intended_parts = [main] if targets else []
        frag.transform_data = {"main": main, "targets": targets, "fill": fill}
        frag.geometry_reference = "source"
        return frag

    def apply(self, pkg, frag):
        td = frag.transform_data
        if not td["targets"]:
            return
        root = pkg.xml(td["main"])
        tables = list(root.iter(W + "tbl"))
        for k, r, c in td["targets"]:
            tc = own(own(tables[k], "tr")[r], "tc")[c]
            pr = tc.find(W + "tcPr")
            if pr is None:
                pr = etree.Element(W + "tcPr")
                tc.insert(0, pr)
            old = pr.find(W + "shd")
            if old is not None:
                pr.remove(old)
            insert_in_order(pr, etree.Element(W + "shd", {W + "val": "clear", W + "color": "auto",
                                                          W + "fill": td["fill"]}), TCPR_ORDER)
        pkg.write_xml(td["main"], root)
'''

HEADER_OP = '''
from __future__ import annotations
from lxml import etree
from spec_surgeon.ops.base import Op, JobException
from spec_surgeon.package import W
from spec_surgeon.ooxml import TRPR_ORDER, insert_in_order, block_full_text


def own(el, tag):
    return [c for c in el if c.tag == W + tag]


def plain_on(pr, name):
    els = pr.findall(W + name) if pr is not None else []
    return len(els) == 1 and not els[0].attrib


class RepeatRevisionHeader(Op):
    name = "repeat_revision_header"
    mode = "formatting"
    description = "Repeat the first row of the revision tables on every page and keep their rows whole."
    params_schema = {"type": "object", "properties": {}}
    select_schema = {}

    def plan(self, pkg, select, params):
        main = pkg.main_part()
        frag = self.fragment(select, params)
        rows_changed = []
        for k, tbl in enumerate(pkg.xml(main).iter(W + "tbl")):
            rows = own(tbl, "tr")
            cells = own(rows[0], "tc") if rows else []
            if not cells or " ".join(block_full_text(p) for p in own(cells[0], "p")).strip() != "Rev":
                continue
            changed = []
            for r, tr in enumerate(rows):
                pr = tr.find(W + "trPr")
                names = [n for n in (["tblHeader"] if r == 0 else []) + ["cantSplit"] if not plain_on(pr, n)]
                if r > 0 and pr is not None and pr.find(W + "tblHeader") is not None:
                    raise JobException("a body row is marked as a header row", "structure")
                if names:
                    changed.append([k, r, sorted(names)])
            frag.property_assertions.append({
                "kind": "table_props", "parts": [main],
                "select": {"scope": ["document"], "tables": {"index_range": [k, k]}},
                "params": {"repeat_header_rows": 1, "cant_split": True},
                "expected_tables": 1 if changed else 0, "expected_rows": len(changed), "expected_cells": 0,
                "changed": {main: {"rows": changed}} if changed else {}})
            rows_changed += changed
            frag.touches.append((main, "tbl:" + str(k), "trPr"))
        if not frag.property_assertions:
            raise JobException("no revision table in this file", "not-found")
        frag.intended_parts = [main] if rows_changed else []
        frag.transform_data = {"main": main, "rows": rows_changed}
        frag.geometry_reference = "none"
        frag.geometry_reason = "rows kept whole may move to the next page"
        return frag

    def apply(self, pkg, frag):
        td = frag.transform_data
        if not td["rows"]:
            return
        root = pkg.xml(td["main"])
        tables = list(root.iter(W + "tbl"))
        for k, r, names in td["rows"]:
            tr = own(tables[k], "tr")[r]
            pr = tr.find(W + "trPr")
            if pr is None:
                pr = etree.Element(W + "trPr")
                ex = tr.find(W + "tblPrEx")
                tr.insert(tr.index(ex) + 1 if ex is not None else 0, pr)
            for n in names:
                for old in pr.findall(W + n):
                    pr.remove(old)
                insert_in_order(pr, etree.Element(W + n), TRPR_ORDER)
        pkg.write_xml(td["main"], root)
'''

SHADE_LOOP = 'for k, r, c in td["targets"]:'
SHADE_PLANTED = {
    # a row the plan never named shaded too
    "an unlisted row": SHADE_OP.replace(SHADE_LOOP, 'for k, r, c in td["targets"] + [[td["targets"][0][0], 2, 0]]:'),
    # a cell's words changed beside its shading
    "a changed cell text": SHADE_OP.replace(
        "        pkg.write_xml(td[\"main\"], root)",
        "        for t in tables[td[\"targets\"][0][0]].iter(W + \"t\"):\n"
        "            t.text = (t.text or \"\").replace(\"Issued\", \"Withdrawn\")\n"
        "        pkg.write_xml(td[\"main\"], root)"),
    # the merged cell of row 3 split back into two cells
    "a merged cell split": SHADE_OP.replace(
        "        pkg.write_xml(td[\"main\"], root)",
        "        for tc in own(own(tables[td[\"targets\"][0][0]], \"tr\")[3], \"tc\"):\n"
        "            span = tc.find(W + \"tcPr/\" + W + \"gridSpan\")\n"
        "            if span is not None:\n"
        "                span.getparent().remove(span)\n"
        "                tc.addnext(etree.fromstring(etree.tostring(tc)))\n"
        "                break\n"
        "        pkg.write_xml(td[\"main\"], root)"),
    # a grid column added to the table
    "a grid column added": SHADE_OP.replace(
        "        pkg.write_xml(td[\"main\"], root)",
        "        grid = tables[td[\"targets\"][0][0]].find(W + \"tblGrid\")\n"
        "        grid.append(etree.Element(W + \"gridCol\", {W + \"w\": \"720\"}))\n"
        "        pkg.write_xml(td[\"main\"], root)"),
}


@pytest.fixture
def schedule(tmp_path) -> Path:
    """The revision table with five rows (Rev, then 0–3), row 3's last two cells merged."""
    doc = Document(io.BytesIO(build("21 05 00", "COMMON WORK RESULTS")))
    tbl = doc.tables[0]
    for n, (date, what) in enumerate((("2026-02-01", "Addendum 1"), ("2026-03-01", "Addendum 2"),
                                      ("2026-04-01", "Issued for Permit")), start=1):
        cells = tbl.add_row().cells
        cells[0].text, cells[1].text, cells[2].text = str(n), date, what
    merged = tbl.rows[3].cells[1].merge(tbl.rows[3].cells[2])
    merged.text = "2026-03-01 Addendum 2"
    f = tmp_path / "21 05 00 - COMMON WORK RESULTS.docx"
    doc.save(f)
    return f


def shaded_rows(docx: bytes):
    tbl = Document(io.BytesIO(docx)).tables[0]
    out = []
    for r, row in enumerate(tbl.rows):
        fills = {tc._tc.tcPr.find(W_ + "shd").get(W_ + "fill") if tc._tc.tcPr is not None
                 and tc._tc.tcPr.find(W_ + "shd") is not None else None for tc in row.cells}
        if fills == {"D9D9D9"}:
            out.append(r)
    return out


W_ = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def test_a_custom_method_shades_every_other_row_it_picks(schedule, tmp_path):
    assert check_source(SHADE_OP) == []
    t = trial(SHADE_OP, schedule, {}, {"fill": "d9d9d9"}, tmp_path / "sb")
    assert t.passed, t.problems
    assert "table_cells" in t.record.get("V10").summary
    assert shaded_rows(Path(t.output).read_bytes()) == [1, 3]      # the merged cell of row 3 shaded with its row


def test_a_custom_method_repeats_the_header_row_and_keeps_rows_whole(schedule, tmp_path):
    assert check_source(HEADER_OP) == []
    t = trial(HEADER_OP, schedule, {}, {}, tmp_path / "sb")
    assert t.passed, t.problems
    assert "table_props" in t.record.get("V10").summary
    rows = Document(io.BytesIO(Path(t.output).read_bytes())).tables[0].rows
    flags = [(tr._tr.trPr.find(W_ + "tblHeader") is not None, tr._tr.trPr.find(W_ + "cantSplit") is not None)
             for tr in rows]
    assert flags == [(True, True)] + [(False, True)] * 4


@pytest.mark.parametrize("case", sorted(SHADE_PLANTED))
def test_a_custom_method_that_touches_table_content_fails_the_trial(case, schedule, tmp_path):
    t = trial(SHADE_PLANTED[case], schedule, {}, {"fill": "D9D9D9"}, tmp_path / "sb")
    assert not t.passed, case
    assert any(p.startswith(("V10 FAIL", "V2 FAIL", "V3 FAIL")) for p in t.problems), t.problems


def test_table_appearance_is_a_custom_method_family_and_structure_is_not():
    families = CUSTOM_TRANSFORM_LIMITS["families"]
    assert {"table_props", "table_cells"} <= set(families.values())
    assert not any(c == "tables" for c in CUSTOM_TRANSFORM_LIMITS["cannot"])
    assert any("table structure" in c for c in CUSTOM_TRANSFORM_LIMITS["cannot"])
    assert {"TBLPR_ORDER", "TRPR_ORDER", "TCPR_ORDER"} <= codegen.ALLOWED_FROM["spec_surgeon.ooxml"]
