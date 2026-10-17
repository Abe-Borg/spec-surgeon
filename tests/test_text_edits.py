"""The in-paragraph edit core (ops/textedit.py ↔ verify/edits.py) through replace_table and set_issue_stamp
(docs/BUILTIN_OPS_BATCH1_DESIGN.md §0.1, §2, §5): end to end tracked and untracked, Word's grammar inside a pending
insertion, the refusals, the table files, and planted defects — a wrong OUTPUT and wrong IMPLEMENTATIONS shared by plan
and apply — each caught by V10 alone."""
from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path
from typing import Any, Dict, List

import pytest
from lxml import etree

from make_fixtures import build, build_batch1
from spec_surgeon.editspec import SpecError, plan_spec, validate_spec
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import replace_table as replace_module
from spec_surgeon.ops import textedit
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.verify import run_suite
from spec_surgeon.verify.edits import v_paragraph_text

AUTHOR = "Abraham Borg"


@pytest.fixture(scope="module")
def doc() -> bytes:
    return build_batch1()


def run_op(src: bytes, op: str, params: Dict[str, Any], select: Dict[str, Any] = None):
    pkg = DocxPackage(src)
    frag = get_op(op).plan(pkg, select or {}, {"author": AUTHOR, **params})
    plan = merge_fragments([frag])
    work = pkg.copy()
    get_op(op).apply(work, frag)
    out = work.output_bytes()
    return plan, out, run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")


def failed(rec) -> List[str]:
    return [c.id for c in rec.checks if c.status == "FAIL"]


def v10_errors(rec) -> List[str]:
    return rec.get("V10").data.get("errors", [])


def para(data: bytes, starts: str, part: str = "word/document.xml"):
    root = DocxPackage(data).xml(part)
    for p in root.iter(W + "p"):
        if "".join(t.text or "" for t in p.iter(W + "t", W + "delText")).startswith(starts):
            return p
    raise AssertionError(f"no paragraph starting {starts!r}")


def accepted_text(p) -> str:
    """What the reader sees with every change accepted: w:t text and tabs outside w:del / w:moveFrom."""
    out = []
    for t in p.iter(W + "t", W + "tab"):
        if t.tag == W + "tab" and t.getparent().tag != W + "r":
            continue
        q, gone = t.getparent(), False
        while q is not None and q is not p:
            if q.tag in (W + "del", W + "moveFrom"):
                gone = True
            q = q.getparent()
        if not gone:
            out.append("\t" if t.tag == W + "tab" else (t.text or ""))
    return "".join(out)


def mutate(docx: bytes, part: str, fn) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(docx))
    root = etree.fromstring(zin.read(part))
    fn(root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            z.writestr(info, serialize_xml(root) if info.filename == part else zin.read(info.filename))
    return buf.getvalue()


# ---- end to end ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("tracked", [True, False])
def test_replace_table_end_to_end_on_the_original_fixture(tracked):
    """FM Global in one run, split over two runs, inside Jeremy's pending insertion, beside a field — every check
    passes, tracked and untracked."""
    src = build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS")
    plan, out, rec = run_op(src, "replace_table", {"pairs": [{"find": "FM Global", "replace": "GRC"},
                                                             {"find": "Owner", "replace": "Client"}],
                                                   "tracked": tracked})
    assert rec.passed, rec.to_markdown()
    assert plan.mode == ("content" if tracked else "untracked")
    p = para(out, "C.")
    assert accepted_text(p) == "C.\tSprinklers: UL listed and FM Approved per the Client's insurer GRC requirements " \
                               "as indicated."
    if tracked:
        # Word's grammar inside Jeremy's insertion: the deletion stays in it, the new text follows it, the rest of his
        # insertion is a second w:ins with his attributes and a fresh id
        kids = [c for c in p if c.tag in (W + "ins", W + "r")]
        authors = [c.get(W + "author") for c in kids if c.tag == W + "ins"]
        assert authors == ["Jeremy", AUTHOR, "Jeremy", AUTHOR, "Jeremy"]
        mine = [c for c in kids if c.tag == W + "ins" and c.get(W + "author") == "Jeremy"]
        assert [len(c.findall(W + "del")) for c in mine] == [1, 1, 0]     # each deletion nested in his insertion
        assert len({c.get(W + "id") for c in mine}) == 3                    # the split-off rests got fresh ids
        assert rec.get("V8").status == "PASS" and rec.get("V5b").status == "PASS"


def test_replace_table_untracked_edits_in_place_without_splitting_runs():
    src = build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS")
    plan, out, rec = run_op(src, "replace_table", {"pairs": [{"find": "FM Global", "replace": "GRC"}],
                                                   "tracked": False})
    assert rec.passed
    s, o = para(src, "A."), para(out, "A.")
    assert len(o.findall(W + "r")) == len(s.findall(W + "r"))
    assert accepted_text(o).endswith("is GRC and all equipment shall be FM Approved.")
    b = para(out, "B.")                       # "FM " + "Global and …": the second run lost its first word
    assert [t.text for t in b.iter(W + "t")] == ["B.", "The auditor is GRC", " and reviews the hydraulic calculations."]


def test_pairs_are_simultaneous_so_two_words_can_swap(doc):
    plan, out, rec = run_op(doc, "replace_table", {"pairs": [{"find": "wet-pipe", "replace": "dry-pipe"},
                                                             {"find": "dry-pipe", "replace": "wet-pipe"}],
                                                   "tracked": False}, {"scope": ["document"]})
    assert rec.passed
    assert accepted_text(para(out, "C.")) == ("C.\tProvide [dry-pipe] [wet-pipe] [preaction] sprinkler systems for the "
                                              "data halls.")


def test_whole_word_and_match_case_defaults(doc):
    plan, out, rec = run_op(doc, "replace_table", {"pairs": [{"find": "Ownership", "replace": "Possession"},
                                                             {"find": "owner", "replace": "nobody"}],
                                                   "tracked": False, "allow_missing": True},
                            {"scope": ["document"], "paragraphs": {"text_regex": "^G\\."}})
    assert rec.passed
    assert accepted_text(para(out, "G.")) == "G.\tThe Owner\tshall review the Possession documents."
    plan, out, rec = run_op(doc, "replace_table", {"pairs": [{"find": "owner", "replace": "Client", "match_case": False,
                                                              "whole_word": False}], "tracked": False},
                            {"scope": ["document"], "paragraphs": {"text_regex": "^G\\."}})
    assert accepted_text(para(out, "G.")) == "G.\tThe Client\tshall review the Clientship documents."


def test_a_page_break_mark_inside_the_range(doc):
    """Word writes w:lastRenderedPageBreak on every page: a phrase across a page boundary is still replaced — the mark
    stays in place untracked, and goes into the w:del with the deleted words tracked."""
    sel = {"scope": ["document"], "paragraphs": {"text_regex": "^I\\."}}
    plan, out, rec = run_op(doc, "replace_table", {"pairs": [{"find": "FM Global", "replace": "GRC"}],
                                                   "tracked": False}, sel)
    assert rec.passed
    assert accepted_text(para(out, "I.")) == "I.\tThe insurer of the Owner is GRC."
    assert para(out, "I.").find(".//" + W + "lastRenderedPageBreak") is not None
    plan, out, rec = run_op(doc, "replace_table", {"pairs": [{"find": "FM Global", "replace": "GRC"}]}, sel)
    assert rec.passed, rec.to_markdown()
    mark = para(out, "I.").find(".//" + W + "lastRenderedPageBreak")
    assert mark.getparent().getparent().tag == W + "del"


def test_hyperlink_untracked_passes_tracked_refuses(doc):
    sel = {"scope": ["document"], "paragraphs": {"text_regex": "^H\\."}}
    plan, out, rec = run_op(doc, "replace_table", {"pairs": [{"find": "Owner portal", "replace": "Client portal"}],
                                                   "tracked": False}, sel)
    assert rec.passed
    assert para(out, "H.").find(W + "hyperlink").findtext(".//" + W + "t") == "Client portal"
    with pytest.raises(JobException) as e:
        run_op(doc, "replace_table", {"pairs": [{"find": "Owner portal", "replace": "Client portal"}]}, sel)
    assert e.value.code == "nested-container"


@pytest.mark.parametrize("find,code,where", [
    ("Owner shall", "spans-structure", "^G\\."),            # a tab between the words: a near miss is never skipped
    ("21 10 00", "field-result", "^L\\."),                  # a REF field's result
    ("Owner shall", "structure", "^K\\."),                  # Jack's pending deletion sits between the words
])
def test_refusals(doc, find, code, where):
    with pytest.raises(JobException) as e:
        run_op(doc, "replace_table", {"pairs": [{"find": find, "replace": "X"}], "tracked": False},
               {"scope": ["document"], "paragraphs": {"text_regex": where}})
    assert e.value.code == code


def test_textbox_text_is_refused_never_half_edited():
    src = build_batch1(textbox=True)
    with pytest.raises(JobException) as e:
        run_op(src, "replace_table", {"pairs": [{"find": "Owner", "replace": "Client"}], "tracked": False},
               {"scope": ["document"], "paragraphs": {"text_regex": "box text"}})
    assert e.value.code == "textbox"


def test_overlapping_pairs_are_ambiguous(doc):
    with pytest.raises(JobException) as e:
        run_op(doc, "replace_table", {"pairs": [{"find": "sprinkler systems", "replace": "a"},
                                                {"find": "systems for", "replace": "b"}]}, {"scope": ["document"]})
    assert e.value.code == "ambiguous"


def test_nothing_found_is_not_found_unless_allow_missing(doc):
    with pytest.raises(JobException) as e:
        run_op(doc, "replace_table", {"pairs": [{"find": "Zebra", "replace": "Horse"}]})
    assert e.value.code == "not-found"
    plan, out, rec = run_op(doc, "replace_table", {"pairs": [{"find": "Zebra", "replace": "Horse"}],
                                                   "allow_missing": True})
    assert rec.passed and out == doc


def test_pairs_validation():
    for bad in ({"find": "", "replace": "x"}, {"find": "a", "replace": 3}, {"find": "a", "replace": "b", "whole": True},
                {"find": "a\tb", "replace": "c"}, {"find": "(", "replace": "c", "regex": True}):
        with pytest.raises(JobException) as e:
            replace_module.normalize_pair(bad, "pair 1")
        assert e.value.code == "bad-params"
    spec = {"request": "r", "summary": "s", "mode": "content",
            "ops": [{"op": "replace_table", "params": {"pairs": [{"find": "a", "replace": "b", "wholeword": True}]}}]}
    with pytest.raises(SpecError):
        validate_spec(spec)


# ---- table files ----------------------------------------------------------------------------------------------------

def _xlsx(rows: List[List[Any]], number_format: int = 0) -> bytes:
    """A minimal workbook: shared strings for text, numbers as numbers (style 1 → numFmtId ``number_format``)."""
    strings: List[str] = []
    cells = []
    for r, row in enumerate(rows, 1):
        xs = []
        for c, v in enumerate(row):
            ref = f"{chr(65 + c)}{r}"
            if isinstance(v, str):
                strings.append(v)
                xs.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
            elif isinstance(v, bool):
                xs.append(f'<c r="{ref}" t="b"><v>{int(v)}</v></c>')
            else:
                xs.append(f'<c r="{ref}" s="1"><v>{v}</v></c>')
        cells.append(f'<row r="{r}">{"".join(xs)}</row>')
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    files = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                       'relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets><sheet name="Sites" sheetId="1" '
                           f'r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                                      'relationships"><Relationship Id="rId1" Type="x/worksheet" '
                                      'Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="x/sharedStrings" '
                                      'Target="sharedStrings.xml"/><Relationship Id="rId3" Type="x/styles" '
                                      'Target="styles.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": f'<worksheet xmlns="{main}"><sheetData>{"".join(cells)}</sheetData></worksheet>',
        "xl/sharedStrings.xml": f'<sst xmlns="{main}">' + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>",
        "xl/styles.xml": f'<styleSheet xmlns="{main}"><cellXfs><xf numFmtId="0"/><xf numFmtId="{number_format}"/>'
                         f'</cellXfs></styleSheet>',
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    return buf.getvalue()


@pytest.mark.parametrize("kind", ["csv", "json", "xlsx"])
def test_pairs_from_a_table_file(doc, tmp_path, kind):
    rows = [["Find", "Replace"], ["FM Global", "GRC"], ["Owner", "Client"]]
    path = tmp_path / f"terms.{kind}"
    if kind == "csv":
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f).writerows(rows)
    elif kind == "json":
        path.write_text(json.dumps({"terms": {"FM Global": "GRC", "Owner": "Client"}}), encoding="utf-8")
    else:
        path.write_bytes(_xlsx(rows))
    table = {"file": str(path), **({"key": "terms"} if kind == "json" else {})}
    plan, out, rec = run_op(doc, "replace_table", {"pairs_table": table, "tracked": False},
                            {"scope": ["document"], "paragraphs": {"text_regex": "^(I|J|K)\\."}})
    assert rec.passed, rec.to_markdown()
    assert accepted_text(para(out, "I.")) == "I.\tThe insurer of the Client is GRC."
    a = [x for x in plan.property_assertions if x["kind"] == "replaced_text"][0]
    assert a["pairs"] == [] and a["table"]["md5"]            # the verifier reads the rows itself


def test_table_numbers_dates_and_booleans(tmp_path):
    from spec_surgeon.ops import tables
    from spec_surgeon.verify import tables as vtables
    path = tmp_path / "v.xlsx"
    path.write_bytes(_xlsx([["Name", "VA3"], ["PROJECT_NO", 2026117], ["RATIO", 2.5], ["FLAG", True]]))
    got, _, _ = tables.variables({"file": str(path), "value_column": "VA3"})
    assert got["PROJECT_NO"] == "2026117"
    assert isinstance(got["RATIO"], tables.Bad) and isinstance(got["FLAG"], tables.Bad)
    v = vtables.table_variables({"file": str(path), "value_column": "VA3"})
    assert v["PROJECT_NO"] == "2026117" and isinstance(v["RATIO"], vtables.Unusable)
    path.write_bytes(_xlsx([["Name", "VA3"], ["DATE", 46068]], number_format=14))       # a date
    got, _, _ = tables.variables({"file": str(path), "value_column": "VA3"})
    assert isinstance(got["DATE"], tables.Bad)
    assert isinstance(vtables.table_variables({"file": str(path), "value_column": "VA3"})["DATE"], vtables.Unusable)


def test_a_table_changed_after_planning(doc, tmp_path):
    path = tmp_path / "terms.json"
    path.write_text(json.dumps({"FM Global": "GRC"}), encoding="utf-8")
    pkg = DocxPackage(doc)
    frag = get_op("replace_table").plan(pkg, {"scope": ["document"]}, {"pairs_table": {"file": str(path)},
                                                                         "tracked": False, "author": AUTHOR})
    plan = merge_fragments([frag])
    path.write_text(json.dumps({"FM Global": "Someone else"}), encoding="utf-8")
    with pytest.raises(JobException) as e:                      # apply refuses
        get_op("replace_table").apply(pkg.copy(), frag)
    assert e.value.code == "table-changed"
    path.write_text(json.dumps({"FM Global": "GRC"}), encoding="utf-8")
    work = pkg.copy()
    get_op("replace_table").apply(work, frag)
    path.write_text(json.dumps({"FM Global": "Changed again"}), encoding="utf-8")   # V10 fails on a changed table
    rec = run_suite(doc, work.output_bytes(), plan.to_dict(), "s", "o")
    assert failed(rec) == ["V10"] and any("changed since planning" in e for e in v10_errors(rec))


# ---- set_issue_stamp -------------------------------------------------------------------------------------------------

def test_issue_stamp_wherever_it_sits(doc):
    plan, out, rec = run_op(doc, "set_issue_stamp", {"pattern": r"ISSUED FOR (PERMIT|BID)( \d{4}-\d{2}-\d{2})?",
                                                     "text": "ISSUED FOR CONSTRUCTION 2026-03-01"})
    assert rec.passed, rec.to_markdown()
    header = [p for p in DocxPackage(out).xml("word/header1.xml").iter(W + "p")][0]
    assert accepted_text(header).endswith("\tISSUED FOR CONSTRUCTION 2026-03-01 ")
    assert accepted_text(para(out, "ISSUED FOR BID")) == "ISSUED FOR CONSTRUCTION 2026-03-01"
    assert para(out, "ISSUED FOR BID").find(".//" + W + "ins/" + W + "r/" + W + "rPr/" + W + "b") is not None


def test_issue_stamp_any_of_and_missing(doc):
    plan, out, rec = run_op(doc, "set_issue_stamp", {"any_of": ["ISSUED FOR BID"], "text": "ISSUED FOR CONSTRUCTION",
                                                     "tracked": False})
    assert rec.passed and accepted_text(para(out, "ISSUED FOR CONSTRUCTION")) == "ISSUED FOR CONSTRUCTION"
    with pytest.raises(JobException) as e:
        run_op(doc, "set_issue_stamp", {"any_of": ["ISSUED FOR TENDER"], "text": "X"})
    assert e.value.code == "not-found"
    with pytest.raises(JobException) as e:
        run_op(doc, "set_issue_stamp", {"any_of": ["A"], "pattern": "B", "text": "X"})
    assert e.value.code == "bad-params"


def test_two_ops_on_one_paragraph_conflict(doc):
    pkg = DocxPackage(doc)
    a = get_op("replace_table").plan(pkg, {}, {"pairs": [{"find": "ISSUED", "replace": "RELEASED"}], "author": AUTHOR})
    b = get_op("set_issue_stamp").plan(pkg, {}, {"any_of": ["ISSUED FOR BID"], "text": "X", "author": AUTHOR})
    with pytest.raises(JobException) as e:
        merge_fragments([a, b])
    assert e.value.code == "conflict"


def test_a_spec_runs_end_to_end(doc):
    spec = {"request": "replace FM Global with GRC", "summary": "s", "mode": "content",
            "ops": [{"op": "replace_table", "params": {"pairs": [{"find": "FM Global", "replace": "GRC"}],
                                                       "author": AUTHOR}}]}
    validate_spec(spec)
    pkg = DocxPackage(doc)
    plan, frags = plan_spec(pkg, spec)
    from spec_surgeon.editspec import apply_spec
    out = apply_spec(pkg, frags).output_bytes()
    assert accepted_text(para(out, "I.")) == "I.\tThe insurer of the Owner is GRC."
    assert run_suite(doc, out, plan.to_dict(), "s", "o").passed


# ---- planted defects: a wrong OUTPUT ---------------------------------------------------------------------------------

def _tracked_owner(doc):
    return run_op(doc, "replace_table", {"pairs": [{"find": "Owner", "replace": "Client"}]},
                  {"scope": ["document"], "paragraphs": {"text_regex": "^A\\."}})


def test_defect_new_text_with_other_formatting(doc):
    plan, out, rec = _tracked_owner(doc)
    assert rec.passed

    def bold_new(root):
        for ins in root.iter(W + "ins"):
            if ins.get(W + "author") == AUTHOR:
                r = ins.find(W + "r")
                rpr = r.find(W + "rPr")
                if rpr is None:
                    rpr = etree.SubElement(r, W + "rPr")
                    r.insert(0, rpr)
                etree.SubElement(rpr, W + "b")
    bad = mutate(out, "word/document.xml", bold_new)
    rec = run_suite(doc, bad, plan.to_dict(), "s", "o")
    assert "V10" in failed(rec)
    assert any("formatting or place" in e for e in v10_errors(rec))


def test_defect_new_text_before_the_deletion(doc):
    plan, out, rec = _tracked_owner(doc)

    def swap(root):
        for ins in list(root.iter(W + "ins")):
            if ins.get(W + "author") == AUTHOR:
                prev = ins.getprevious()
                prev.addprevious(ins)
    rec = run_suite(doc, mutate(out, "word/document.xml", swap), plan.to_dict(), "s", "o")
    assert "V10" in failed(rec)


def test_defect_a_character_outside_the_edit_is_lost(doc):
    plan, out, rec = run_op(doc, "replace_table", {"pairs": [{"find": "Owner", "replace": "Client"}],
                                                   "tracked": False},
                            {"scope": ["document"], "paragraphs": {"text_regex": "^A\\."}})

    def eat(root):
        for t in root.iter(W + "t"):
            if t.text and "Client is" in t.text:
                t.text = t.text.replace("Client is", "Client i")
    rec = run_suite(doc, mutate(out, "word/document.xml", eat), plan.to_dict(), "s", "o")
    assert "V2" in failed(rec) and "V10" in failed(rec)


# ---- planted defects: a wrong IMPLEMENTATION shared by plan and apply ------------------------------------------------

def test_shared_defect_matcher_skips_the_last_occurrence(doc, monkeypatch):
    real = replace_module.find_all

    def skip_last(text, pairs, compiled, where):
        found = real(text, pairs, compiled, where)
        return found[:-1] if len(found) > 1 else found
    monkeypatch.setattr(replace_module, "find_all", skip_last)
    plan, out, rec = run_op(doc, "replace_table", {"pairs": [{"find": "a", "replace": "A", "whole_word": False}],
                                                   "tracked": False},
                            {"scope": ["document"], "paragraphs": {"text_regex": "^N\\."}})
    assert accepted_text(para(out, "N.")) == "N.\tUntAgged closing pArAgraph."     # the last "a" was skipped
    assert failed(rec) == ["V10"]
    assert any("replaced_text" in e for e in v10_errors(rec))


def test_shared_defect_whole_word_rule_ignored(doc, monkeypatch):
    monkeypatch.setattr(replace_module, "whole_word_ok", lambda text, a, b: True)
    plan, out, rec = run_op(doc, "replace_table", {"pairs": [{"find": "Owner", "replace": "Client"}], "tracked": False},
                            {"scope": ["document"], "paragraphs": {"text_regex": "^G\\."}})
    assert accepted_text(para(out, "G.")).endswith("Clientship documents.")     # both sides of the op agree …
    assert failed(rec) == ["V10"]                                               # … the verifier does not


def _italic_second_run(src: bytes) -> bytes:
    def italic(root):                                 # "FM " + "Global and …": the second run becomes italic
        for r in root.iter(W + "r"):
            if any((t.text or "").startswith("Global and") for t in r.iter(W + "t")):
                rpr = r.find(W + "rPr")
                if rpr is None:
                    rpr = etree.Element(W + "rPr")
                    r.insert(0, rpr)
                etree.SubElement(rpr, W + "i")
    return mutate(src, "word/document.xml", italic)


def test_shared_defect_new_text_takes_the_last_runs_formatting(monkeypatch):
    """An apply that writes the new text where the range ENDS (the italic run) instead of where it starts — the plan's
    text predictions are the same, so V2 passes; the item model sees every new character in the wrong formatting."""
    src = _italic_second_run(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    sel = {"scope": ["document"], "paragraphs": {"text_regex": "^B\\."}}
    plan, out, rec = run_op(src, "replace_table", {"pairs": [{"find": "FM Global", "replace": "GRC"}],
                                                   "tracked": False}, sel)
    assert rec.passed                                  # the honest apply: GRC keeps the first run's formatting

    def at_the_end(p, edits):
        rt = textedit.RunText(p)
        for e in sorted(edits, key=lambda e: -e.start):
            groups = []
            for ps in rt.positions[e.start:e.end]:
                if groups and groups[-1][0] is ps.child:
                    groups[-1][2] = ps.offset + 1
                else:
                    groups.append([ps.child, ps.offset, ps.offset + 1])
            for k in range(len(groups) - 1, -1, -1):
                t, lo, hi = groups[k]
                txt = t.text or ""
                textedit.set_text(t, txt[:lo] + (e.text if k == len(groups) - 1 else "") + txt[hi:])
    monkeypatch.setattr(textedit, "apply_untracked", at_the_end)
    plan, out, rec = run_op(src, "replace_table", {"pairs": [{"find": "FM Global", "replace": "GRC"}],
                                                   "tracked": False}, sel)
    assert rec.get("V2").status == "PASS"
    assert failed(rec) == ["V10"]


def test_verifier_run_text_matches_the_transform_on_every_paragraph():
    """The two run-text implementations agree on every paragraph of the fixtures (both sides' definitions)."""
    for data in (build_batch1(textbox=True), build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS")):
        pkg = DocxPackage(data)
        for part in pkg.text_parts():
            for p in pkg.xml(part).iter(W + "p"):
                assert textedit.RunText(p).text == v_paragraph_text(p)


# ---- built-in batch 5: the core deletes a tab (tabs=True — docs/BUILTIN_OPS_BATCH5_DESIGN.md §2.3) -------------------

import copy  # noqa: E402

from make_fixtures import SCHEME_LEVELS, build_scheme_example, build_typed, patch_docx  # noqa: E402
from spec_surgeon.ooxml import RevisionStamp, block_struct_counts  # noqa: E402
from spec_surgeon.ops import convert_typed_numbers as typed_module  # noqa: E402
from spec_surgeon.ops.base import PlanFragment  # noqa: E402
from spec_surgeon.verify.edits import check_edited_paragraph, v_check_edits, v_items  # noqa: E402


def _tab_paragraphs():
    """Every paragraph of the typed fixture's three variants whose run text holds a tab."""
    for variant in ({}, {"leading_tab": True}, {"spaces": True}):
        for p in DocxPackage(build_typed(**variant)).xml("word/document.xml").iter(W + "p"):
            if "\t" in textedit.RunText(p).text:
                yield p


def _tab_ranges(text: str):
    """Pure deletions around the paragraph's first tab: ending at it, starting at it, the tab alone, the tab inside."""
    i = text.index("\t")
    out = {(0, i + 1), (i, i + 1)}
    if i > 0 and i + 2 <= len(text):
        out.add((i - 1, i + 2))
    if i + 2 <= len(text):
        out.add((i, i + 2))
    return sorted(out)


@pytest.mark.parametrize("tracked", [False, True])
def test_tab_deletions_agree_on_both_sides(tracked):
    """The transform plans and applies a pure deletion covering a w:tab (at the end, at the start, alone, inside the
    range); the verifier's own item model with tabs=True expects exactly the paragraph the transform wrote; untracked
    the plan declares the tab gone (V3's struct_after), tracked the tab goes into the w:del (counts unchanged)."""
    n = 0
    for src_p in _tab_paragraphs():
        text = textedit.RunText(src_p).text
        for a, b in _tab_ranges(text):
            p = copy.deepcopy(src_p)
            frag = PlanFragment(job="t", mode="formatting", params={}, intended_parts=[])
            done = textedit.plan_paragraph(frag, "word/document.xml", 0, p, [textedit.Edit(a, b, "")], tracked, set(),
                                           "here", tabs=True)
            assert done, (text, a, b)
            tabs_in = text[a:b].count("\t")
            if tracked:
                textedit.apply_tracked(p, done, RevisionStamp.now(AUTHOR, False), [900], tabs=True)
                assert not [d for d in frag.diffs if d.struct_after]
            else:
                diff = frag.diffs[0]
                before = block_struct_counts(src_p).get("tab", 0)
                assert (diff.struct_after or {}).get("tab", 0) == before - tabs_in
                textedit.apply_untracked(p, done, tabs=True)
            errs: List[str] = []
            check_edited_paragraph(src_p, p, [(a, b, "", False)], tracked, AUTHOR, set(), set(), "here", errs, tabs=True)
            assert errs == [], (text, a, b, errs)
            assert textedit.RunText(p).text == text[:a] + text[b:]          # tracked: the deleted runs are hidden
            n += 1
    assert n >= 40


def test_the_strict_core_still_refuses_a_tab_and_tabs_never_cover_placed_text():
    p = next(_tab_paragraphs())
    text = textedit.RunText(p).text
    i = text.index("\t")
    for edit, tabs in (((0, i + 1, ""), False), ((0, i + 1, "X"), True)):
        with pytest.raises(JobException) as e:
            textedit.check_edits(textedit.RunText(p), [textedit.Edit(*edit)], False, set(), "here", tabs=tabs)
        assert e.value.code == "structure"
        with pytest.raises(ValueError):
            v_check_edits(p, v_items(p), [edit + (False,)], False, set(), tabs=tabs)
    with pytest.raises(JobException) as e:                         # apply's own guard: not planned with tabs
        textedit.apply_untracked(copy.deepcopy(p), [textedit.Edit(0, i + 1, "")])
    assert e.value.code == "internal"


def _typed_with(xml: str) -> bytes:
    """The typed fixture with one more typed paragraph (XML, the w: prefix undeclared) after "A.⇥Steel pipe."."""
    def add(root):
        anchor = next(q for q in root.iter(W + "p") if textedit.RunText(q).text == "A.\tSteel pipe.")
        anchor.addnext(etree.fromstring(xml.replace("<w:p>", f'<w:p xmlns:w="{W[1:-1]}">', 1)))
    return patch_docx(build_typed(), {"word/document.xml": add})


def _convert(src: bytes, tmp_path):
    t = tmp_path / "NUMBERING SCHEME.docx"
    t.write_bytes(build_scheme_example())
    return run_op(src, "convert_typed_numbers", {"levels": SCHEME_LEVELS, "template": str(t), "tracked": False})


SECOND_TAB = "<w:p><w:r><w:t>B.</w:t><w:tab/><w:t>Black steel.</w:t><w:tab/><w:t>Schedule 40.</w:t></w:r></w:p>"
PTAB = ('<w:p><w:r><w:t>B.</w:t><w:ptab w:relativeTo="margin" w:alignment="left" w:leader="none"/>'
        '<w:t>Positional tab item.</w:t></w:r></w:p>')


def test_shared_defect_a_tab_left_behind(tmp_path, monkeypatch):
    """A range one short (the tab or space after the typed number left), the same in plan and apply: V2 and V3 agree
    with the plan; only the verifier's own prefix sees the tab left behind."""
    real = typed_module.Edit
    monkeypatch.setattr(typed_module, "Edit", lambda a, b, text: real(a, b - 1, text))
    plan, out, rec = _convert(build_typed(), tmp_path)
    assert failed(rec) == ["V10"] and any("not exactly the predicted change" in e for e in v10_errors(rec))


def test_shared_defect_a_tab_outside_the_range_taken(tmp_path, monkeypatch):
    """An apply that also takes the next tab after the range, and a plan whose struct_after counts it gone: V2 (tabs are
    no block text) and V3 (declared) pass; the item model finds the paragraph's second tab missing."""
    src = _typed_with(SECOND_TAB)
    plan, out, rec = _convert(src, tmp_path)
    assert rec.passed, rec.to_markdown()                      # the honest core
    real_apply, real_counts = textedit.apply_untracked, textedit.block_struct_counts

    def next_tab(p, after: int):
        rt = textedit.RunText(p)
        return [ps.child for ps in rt.positions[after:] if textedit.is_tab_position(ps)][:1]

    def greedy_apply(p, edits, tabs=False):
        extra = [t for e in edits for t in next_tab(p, e.end)]
        real_apply(p, edits, tabs=tabs)
        for t in extra:
            t.getparent().remove(t)

    def greedy_counts(p, gone=()):
        rt = textedit.RunText(p)
        last = max([i for i, ps in enumerate(rt.positions) if any(ps.child is g for g in gone)], default=None)
        return real_counts(p, list(gone) + (next_tab(p, last + 1) if last is not None else []))
    monkeypatch.setattr(textedit, "apply_untracked", greedy_apply)
    monkeypatch.setattr(textedit, "block_struct_counts", greedy_counts)
    plan, out, rec = _convert(src, tmp_path)
    assert failed(rec) == ["V10"] and any("not exactly the predicted change" in e for e in v10_errors(rec))


def test_shared_defect_a_positional_tab_accepted(tmp_path, monkeypatch):
    """A w:ptab after a typed number: the core refuses it (structure). A core whose tab rule also takes w:ptab removes it
    (and declares it, so V3 passes) — the verifier's own rule says the op had to refuse."""
    src = _typed_with(PTAB)
    with pytest.raises(JobException) as e:
        _convert(src, tmp_path)
    assert e.value.code == "structure"
    monkeypatch.setattr(textedit, "is_tab_position",
                        lambda ps: ps.offset < 0 and ps.child.tag in (W + "tab", W + "ptab"))
    plan, out, rec = _convert(src, tmp_path)
    assert failed(rec) == ["V10"] and any("the op must refuse" in e for e in v10_errors(rec))
