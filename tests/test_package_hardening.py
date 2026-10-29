"""Package-level hardening carried over from the Spec Template Normalizer's lessons:

* C8 — the settings part is found as Word finds it, through the main part's relationship, never by its usual name
  (``DocxPackage.settings_part``): a renamed settings part is read, a stray ``word/settings.xml`` the document does not
  relate is not, and the transform now agrees with the verifier, which already resolved it so.
* C9 — new revision ids go above every annotation id in every WordprocessingML part (``ooxml.max_revision_id``: the
  styles part's property revisions, permission ranges, comments and bookmark ends included; a footnote's or a
  web-settings div's id excluded), and a range past 2**31 - 1 is refused (``revision-ids``). V4 checks both on its
  own: a new revision id may reuse no annotation id of the source and may not pass the largest id Word reads.
* C10 — a part with an XML document type declaration is refused (``xml-dtd``) wherever a part is read: at attach time
  (triage), by ``parse_xml`` and by the verifier's own reader; an SVG picture's DOCTYPE is not the document's.

Every rule is planted against: a wrong output the verifier must catch, and a wrong implementation shared by plan and
apply (the old ``max_revision_id``, the transform blind to a DOCTYPE).
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import pytest
from lxml import etree

from fx_table_structure import xlsx
from make_fixtures import build, build_rich, patch_docx
from spec_surgeon import diagnostics, ooxml
from spec_surgeon import package as package_module
from spec_surgeon.batch import run_batch
from spec_surgeon.editspec import apply_spec, plan_spec
from spec_surgeon.ops import JobException
from spec_surgeon.ops import base as base_module
from spec_surgeon.ops import replace_text as replace_text_module
from spec_surgeon.ops import tables as tables_module
from spec_surgeon.package import DocxPackage, W, XmlDoctype
from spec_surgeon.planner import plan_file
from spec_surgeon.report import FAILURE_EXPLANATIONS, explain_failure
from spec_surgeon.triage import triage_file
from spec_surgeon.verify import run_suite
from spec_surgeon.verify import tables as vtables
from spec_surgeon.verify.reader import SETTINGS_REL as V_SETTINGS_REL
from spec_surgeon.verify.reader import VDoctype, VPackage

AUTHOR = "Abraham Borg"
MAIN = "word/document.xml"
RICH = build_rich()
PLAIN = build("21 05 00", "COMMON WORK RESULTS FOR FIRE SUPPRESSION")
DOCTYPE = '<!DOCTYPE w:document [<!ENTITY owner "Global Risk Consultants">]>'


# ---- fixture surgery (raw bytes: patch_docx re-serializes, which would drop a DOCTYPE) ----------------------------------

def _rewrite(data: bytes, members: dict, rename: dict = None) -> bytes:
    """``data`` with each member in ``members`` given new bytes (a new name is added at the end) and each member in
    ``rename`` moved to its new name in place."""
    rename = rename or {}
    zin = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            payload = members.get(info.filename, zin.read(info.filename))
            zout.writestr(zipfile.ZipInfo(rename.get(info.filename, info.filename), date_time=info.date_time), payload)
        for name, payload in members.items():
            if name not in zin.namelist():
                zout.writestr(name, payload)
    return out.getvalue()


def _read(data: bytes, name: str) -> bytes:
    return zipfile.ZipFile(io.BytesIO(data)).read(name)


def _rename_settings(data: bytes, new: str = "word/docSettings.xml") -> bytes:
    """The settings part moved to ``new``: its relationship and its content-type Override follow."""
    rels = _read(data, "word/_rels/document.xml.rels").replace(b'Target="settings.xml"',
                                                                f'Target="{new.split("/", 1)[1]}"'.encode())
    types = _read(data, "[Content_Types].xml").replace(b'PartName="/word/settings.xml"', f'PartName="/{new}"'.encode())
    assert rels != _read(data, "word/_rels/document.xml.rels") and types != _read(data, "[Content_Types].xml")
    return _rewrite(data, {"word/_rels/document.xml.rels": rels, "[Content_Types].xml": types},
                    rename={"word/settings.xml": new})


def _with_doctype(xml: bytes, encoding: str = "utf-8") -> bytes:
    """The part with a document type declaration after its XML declaration, in ``encoding``."""
    text = xml.decode("utf-8")
    decl = re.match(r"<\?xml[^>]*\?>", text)
    body = text[decl.end():] if decl else text
    head = f'<?xml version="1.0" encoding="{encoding.upper()}" standalone="yes"?>'
    return (head + DOCTYPE + body).encode("utf-16" if encoding == "utf-16" else encoding)


def _tracked_replace(find: str, replace: str, tracked: bool = True) -> dict:
    return {"request": "r", "summary": "s", "assumptions": [], "questions": [], "applies_to": {"or": "all"},
            "mode": "content" if tracked else "untracked",
            "ops": [{"op": "replace_text", "params": {"find": find, "replace": replace, "author": AUTHOR,
                                                      "tracked": tracked}}]}


def _run(data: bytes, spec: dict):
    pkg = DocxPackage(data)
    plan, frags = plan_spec(pkg, spec)
    out = apply_spec(pkg, frags).output_bytes()
    return plan, out, run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")


def _failed(rec):
    return [c.id for c in rec.checks if c.status == "FAIL"]


def _v4(rec):
    return next(c for c in rec.checks if c.id == "V4")


def _new_ins_ids(src: bytes, out: bytes):
    before = {el.get(W + "id") for el in DocxPackage(src).xml(MAIN).iter(W + "ins")}
    return [el.get(W + "id") for el in DocxPackage(out).xml(MAIN).iter(W + "ins") if el.get(W + "id") not in before]


# ---- C8: the settings part through the relationship ---------------------------------------------------------------------

def test_a_renamed_settings_part_is_read_through_the_relationship():
    data = _rename_settings(RICH)
    pkg = DocxPackage(data)
    assert not pkg.has("word/settings.xml") and pkg.settings_part() == "word/docSettings.xml"
    assert pkg.settings_flag("evenAndOddHeaders") and pkg.settings_flag("trackRevisions")    # both on in build_rich
    assert DocxPackage(RICH).settings_part() == "word/settings.xml"                            # the usual name: as before
    assert VPackage(data).related(V_SETTINGS_REL) == "word/docSettings.xml"                   # the verifier's own reading


def test_the_verifiers_reader_decompresses_a_member_once_and_hands_out_exactly_its_bytes(monkeypatch):
    pkg = VPackage(RICH)
    reads = []
    real = pkg.zip.read
    monkeypatch.setattr(pkg.zip, "read", lambda name, *a: reads.append(name) or real(name, *a))
    archive = zipfile.ZipFile(io.BytesIO(RICH))
    for _ in range(3):
        assert {n: pkg.read(n) for n in pkg.names} == {n: archive.read(n) for n in pkg.names}
        assert pkg.xml("word/document.xml") is not pkg.xml("word/document.xml")    # a tree is parsed for each caller
    assert sorted(reads) == sorted(pkg.names)                                        # each member came out once
    office = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
    assert pkg.package_related(office) == "word/document.xml" == pkg.package_related(office)
    assert VPackage(RICH).package_related("http://example.test/no-such-relationship") is None


def test_a_stray_settings_part_the_document_does_not_relate_is_not_read():
    rels = etree.fromstring(_read(RICH, "word/_rels/document.xml.rels"))
    for rel in list(rels):
        if rel.get("Type", "").endswith("/settings"):
            rels.remove(rel)
    data = _rewrite(RICH, {"word/_rels/document.xml.rels": etree.tostring(rels, xml_declaration=True,
                                                                          encoding="UTF-8", standalone=True)})
    pkg = DocxPackage(data)
    assert pkg.has("word/settings.xml")                                    # still in the package, evenAndOddHeaders on
    assert pkg.settings_part() is None
    assert not pkg.settings_flag("evenAndOddHeaders") and not pkg.settings_flag("trackRevisions")
    assert VPackage(data).related(V_SETTINGS_REL) is None


def test_an_op_reads_the_even_page_switch_from_a_renamed_settings_part():
    """set_page_setup says which even-page parts a section shows (the switch is on in build_rich): the same request on
    the same document says the same thing, and verifies, whatever the settings part is called."""
    spec = {"request": "r", "summary": "s", "assumptions": [], "questions": [], "applies_to": {"or": "all"},
            "mode": "formatting", "ops": [{"op": "set_page_setup", "select": {"sections": [0]},
                                            "params": {"different_first_page": False}}]}
    notes = []
    for data in (RICH, _rename_settings(RICH)):
        plan, out, rec = _run(data, spec)
        assert rec.passed, rec.to_markdown()
        note = [n for n in plan.notes if n.startswith("section 0: different first page off")][0]
        assert "even-page ones" in note
        notes.append(note)
    assert notes[0] == notes[1]


# ---- C9: revision ids above every annotation id, and no larger than Word reads ------------------------------------------

def _annotated(data: bytes, ids: dict) -> bytes:
    """``data`` with annotations Word writes outside the text parts or outside the old id list: a reviewer's
    ``w:rPrChange`` in the Normal style (``style``), a numbered permission range around the body's last paragraph
    (``perm``), a footnote-sized id on nothing that counts (a web-settings div, ``div``)."""
    def styles(root):
        normal = next(s for s in root.iter(W + "style") if s.get(W + "styleId") == "Normal")
        rpr = normal.find(W + "rPr")
        if rpr is None:
            rpr = etree.SubElement(normal, W + "rPr")
        chg = etree.SubElement(rpr, W + "rPrChange")
        chg.set(W + "id", str(ids["style"]))
        chg.set(W + "author", "Reviewer")
        chg.set(W + "date", "2026-01-02T00:00:00Z")
        etree.SubElement(chg, W + "rPr")

    def document(root):
        last = list(root.iter(W + "p"))[-1]
        start = etree.Element(W + "permStart")
        start.set(W + "id", str(ids["perm"]))
        start.set(W + "edGrp", "everyone")
        end = etree.Element(W + "permEnd")
        end.set(W + "id", str(ids["perm"]))
        last.insert(1 if last.find(W + "pPr") is not None else 0, start)
        last.append(end)

    def web(root):
        divs = root.find(W + "divs")
        if divs is None:
            divs = etree.SubElement(root, W + "divs")
        div = etree.SubElement(divs, W + "div")
        div.set(W + "id", str(ids["div"]))
        etree.SubElement(div, W + "bodyDiv").set(W + "val", "1")

    edits = {}
    if "style" in ids:
        edits["word/styles.xml"] = styles
    if "perm" in ids:
        edits[MAIN] = document
    if "div" in ids:
        edits["word/webSettings.xml"] = web
    return patch_docx(data, edits)


def _old_max_revision_id(pkg: DocxPackage) -> int:
    """The id scan as it was before C9: the text parts only, and a shorter list of elements."""
    mx = 0
    tags = {W + t for t in ("ins", "del", "pPrChange", "rPrChange", "sectPrChange", "tblPrChange", "trPrChange",
                            "tcPrChange", "cellIns", "cellDel", "cellMerge", "moveFrom", "moveTo", "moveFromRangeStart",
                            "moveToRangeStart", "bookmarkStart", "commentRangeStart", "numberingChange")}
    for part in pkg.text_parts():
        for el in pkg.xml(part).iter():
            if el.tag in tags and el.get(W + "id") is not None:
                mx = max(mx, int(el.get(W + "id")))
    return mx


def test_the_id_scan_reads_every_document_part_and_only_annotation_ids():
    text_max = _old_max_revision_id(DocxPackage(PLAIN))
    data = _annotated(PLAIN, {"style": text_max + 40, "perm": text_max + 20, "div": 2_100_000_000})
    pkg = DocxPackage(data)
    assert "word/styles.xml" in pkg.wordprocessing_xml_parts() and "word/webSettings.xml" in pkg.wordprocessing_xml_parts()
    assert ooxml.max_revision_id(pkg) == text_max + 40                    # the style's revision; the div does not count
    assert _old_max_revision_id(pkg) == text_max                          # what the old scan saw
    assert ooxml.max_revision_id(DocxPackage(_annotated(PLAIN, {"perm": text_max + 20}))) == text_max + 20


def test_ids_that_are_not_whole_numbers_are_passed_over():
    text_max = ooxml.max_revision_id(DocxPackage(PLAIN))

    def named(root):
        last = list(root.iter(W + "p"))[-1]
        for value in ("everyone-edit", "1_000_000", "٣٣٣٣٣٣"):      # a named range; Python-only spellings of numbers
            el = etree.SubElement(last, W + "permStart")
            el.set(W + "id", value)
            el.set(W + "edGrp", "everyone")
        el = etree.SubElement(last, W + "permEnd")
        el.set(W + "id", f" +{text_max + 3} ")                               # sign and spaces: xsd:integer allows them
    assert ooxml.max_revision_id(DocxPackage(patch_docx(PLAIN, {MAIN: named}))) == text_max + 3


def test_new_revision_ids_go_above_annotations_in_other_parts_and_verify():
    text_max = _old_max_revision_id(DocxPackage(PLAIN))
    data = _annotated(PLAIN, {"style": text_max + 1, "perm": text_max + 2, "div": 2_100_000_000})
    plan, out, rec = _run(data, _tracked_replace("FM Global", "the Owner's risk consultant"))
    assert rec.passed, rec.to_markdown()
    new = [int(i) for i in _new_ins_ids(data, out)]
    assert new and min(new) > text_max + 2 and max(new) < 2_100_000_000
    data4 = _v4(rec).data
    assert data4["new_ids_reusing_source_annotation_ids"] == [] and data4["new_ids_out_of_range"] == []


def test_planted_the_old_scan_shared_by_plan_and_apply_is_caught_by_v4(monkeypatch):
    """A wrong implementation shared by plan and apply: the id scan as it was, blind to the styles part and to
    permission ranges. Its ids collide with the style's revision and the permission range; V4 says so."""
    text_max = _old_max_revision_id(DocxPackage(PLAIN))
    data = _annotated(PLAIN, {"style": text_max + 1, "perm": text_max + 2})
    monkeypatch.setattr(replace_text_module, "max_revision_id", _old_max_revision_id)
    plan, out, rec = _run(data, _tracked_replace("FM Global", "the Owner's risk consultant"))
    assert _failed(rec) == ["V4"], rec.to_markdown()
    reused = _v4(rec).data["new_ids_reusing_source_annotation_ids"]
    assert sorted(int(i) for i in reused) == [text_max + 1, text_max + 2]
    assert "reuse an annotation id of the source" in _v4(rec).summary
    assert any("already uses (in any part)" in line for f in rec.failures() for line in f[3])


def test_planted_a_wrong_output_reusing_a_bookmark_id_of_a_header_is_caught_by_v4():
    """A wrong output: one new insertion renumbered to the id of a bookmark in a header (a part the edit never touched,
    and no revision): only V4's id-space rule sees it."""
    def header(root):
        p = next(root.iter(W + "p"))
        start = etree.SubElement(p, W + "bookmarkStart")
        start.set(W + "id", "5000")
        start.set(W + "name", "HeaderMark")
        etree.SubElement(p, W + "bookmarkEnd").set(W + "id", "5000")
    data = patch_docx(PLAIN, {"word/header1.xml": header})
    plan, out, rec = _run(data, _tracked_replace("FM Global", "the Owner's risk consultant"))
    assert rec.passed, rec.to_markdown()
    victim = _new_ins_ids(data, out)[0]

    def renumber(root):
        next(el for el in root.iter(W + "ins") if el.get(W + "id") == victim).set(W + "id", "5000")
    bad = patch_docx(out, {MAIN: renumber})
    rec2 = run_suite(data, bad, plan.to_dict(), "src.docx", "bad.docx")
    assert _failed(rec2) == ["V4"], rec2.to_markdown()
    assert _v4(rec2).data["new_ids_reusing_source_annotation_ids"] == ["5000"]


def test_planted_a_wrong_output_past_the_largest_id_word_reads_is_caught_by_v4():
    plan, out, rec = _run(PLAIN, _tracked_replace("FM Global", "the Owner's risk consultant"))
    assert rec.passed, rec.to_markdown()
    victim = _new_ins_ids(PLAIN, out)[0]

    def renumber(root):
        next(el for el in root.iter(W + "ins") if el.get(W + "id") == victim).set(W + "id", "2147483648")
    rec2 = run_suite(PLAIN, patch_docx(out, {MAIN: renumber}), plan.to_dict(), "src.docx", "bad.docx")
    assert _failed(rec2) == ["V4"], rec2.to_markdown()
    assert _v4(rec2).data["new_ids_out_of_range"] == ["2147483648"]
    assert "not a whole number up to 2147483647" in _v4(rec2).summary


def test_a_tracked_change_past_the_largest_id_word_reads_is_refused_and_one_just_below_it_is_not():
    near = _annotated(PLAIN, {"perm": ooxml.MAX_REVISION_ID - 100})
    plan, out, rec = _run(near, _tracked_replace("FM Global", "the Owner's risk consultant"))
    assert rec.passed, rec.to_markdown()
    assert all(int(i) <= ooxml.MAX_REVISION_ID for i in _new_ins_ids(near, out))

    full = _annotated(PLAIN, {"perm": ooxml.MAX_REVISION_ID - 1})        # one id left; the replacement needs more
    with pytest.raises(JobException) as e:
        plan_spec(DocxPackage(full), _tracked_replace("FM Global", "the Owner's risk consultant"))
    assert e.value.code == "revision-ids" and str(ooxml.MAX_REVISION_ID) in e.value.reason
    assert e.value.details["last_id"] > ooxml.MAX_REVISION_ID
    assert explain_failure("revision-ids", "x")[0] == "Revision Numbers Used Up"
    plan, out, rec = _run(full, _tracked_replace("FM Global", "the Owner's risk consultant", tracked=False))
    assert rec.passed, rec.to_markdown()                                   # an untracked change needs no id


W14 = "{http://schemas.microsoft.com/office/word/2010/wordml}"
XS = "{http://www.w3.org/2001/XMLSchema}"
SCHEMAS = Path(ooxml.__file__).resolve().parent / "schemas"
# Elements the schemas give a w:id that is no annotation's, each for its reason: a web-settings div (a pasted web page's
# ids, random values near 2**31); a run's fitText and eastAsianLayout (they group runs, and Word writes large values);
# a note and its reference (the note's own number, another space); a table cell (a string id).
NOT_ANNOTATIONS = {"div", "fitText", "eastAsianLayout", "footnote", "endnote", "footnoteReference", "endnoteReference",
                   "tc"}


def _schema_id_elements() -> set:
    """Every element of WordprocessingML (wml.xsd) and Word 2010's additions (wml-2010.xsd) whose type, through its
    extensions, declares an ``id`` attribute: the list as the schemas state it."""
    wml = etree.parse(str(SCHEMAS / "ISO-IEC29500-4_2016" / "wml.xsd")).getroot()
    w14 = etree.parse(str(SCHEMAS / "microsoft" / "wml-2010.xsd")).getroot()
    types = {ct.get("name"): ct for ct in wml.iter(XS + "complexType") if ct.get("name")}

    def has_id(name, seen=()):
        ct = types.get(name)
        if ct is None or name in seen:
            return False
        if any(a.get("name") == "id" for a in ct.iter(XS + "attribute")):
            return True
        return any(has_id(e.get("base", "").split(":")[-1], seen + (name,)) for e in ct.iter(XS + "extension"))

    def named(root, ns):
        return {ns + el.get("name") for el in root.iter(XS + "element")
                if el.get("name") and el.get("type") and has_id(el.get("type").split(":")[-1])}
    return named(wml, W) | named(w14, W14)


def test_the_annotation_lists_are_the_schemas_own():
    """No hand-kept list stays complete (a short one handed out a w:tblGridChange's id again), so both sides' lists are
    held to the schemas: every element the vendored XSDs give an id, less the named non-annotations, and nothing else."""
    from spec_surgeon.verify import suite as vsuite
    stated = _schema_id_elements()
    assert {W + "div", W + "fitText", W + "tblGridChange", W + "tblPrExChange", W14 + "conflictIns"} <= stated
    expected = stated - {W + n for n in NOT_ANNOTATIONS}
    assert ooxml.ANNOTATION_ID_TAGS == expected
    verifier = {W + n for n in vsuite.ANNOTATION_ELEMENTS} | {W14 + n for n in vsuite.W14_ANNOTATION_ELEMENTS}
    assert verifier == expected


def test_run_grouping_and_note_ids_do_not_raise_the_seed():
    """A w:fitText id at a value Word writes (large) and a footnote's number leave the seed where the annotations put
    it, so a tracked edit beside them is not pushed toward the ceiling."""
    base = ooxml.max_revision_id(DocxPackage(PLAIN))

    def document(root):
        r = next(root.iter(W + "r"))
        rpr = r.find(W + "rPr")
        if rpr is None:
            rpr = etree.Element(W + "rPr")
            r.insert(0, rpr)
        fit = etree.SubElement(rpr, W + "fitText")
        fit.set(W + "val", "1680")
        fit.set(W + "id", "2147483000")
    data = patch_docx(PLAIN, {MAIN: document})
    assert ooxml.max_revision_id(DocxPackage(data)) == base
    plan, out, rec = _run(data, _tracked_replace("FM Global", "the Owner's risk consultant"))
    assert rec.passed, rec.to_markdown()


def test_coauthoring_conflict_markup_is_in_the_id_space(monkeypatch):
    """Word 2010's co-authoring conflict markup (w14:conflictIns, its own namespace) carries a w:id in the same space.
    Counted on both sides; the scan blind to it, shared by plan and apply, is caught by V4."""
    base = ooxml.max_revision_id(DocxPackage(PLAIN))

    def document(root):
        last = list(root.iter(W + "p"))[-1]
        conflict = etree.SubElement(last, W14 + "conflictIns")
        conflict.set(W + "id", str(base + 1))
        conflict.set(W + "author", "Reviewer")
        conflict.set(W + "date", "2026-01-02T00:00:00Z")
    data = patch_docx(PLAIN, {MAIN: document})
    assert ooxml.max_revision_id(DocxPackage(data)) == base + 1
    plan, out, rec = _run(data, _tracked_replace("FM Global", "the Owner's risk consultant"))
    assert rec.passed, rec.to_markdown()
    assert min(int(i) for i in _new_ins_ids(data, out)) > base + 1
    monkeypatch.setattr(ooxml, "ANNOTATION_ID_TAGS", frozenset(t for t in ooxml.ANNOTATION_ID_TAGS if t.startswith(W)))
    plan, out, rec = _run(data, _tracked_replace("FM Global", "the Owner's risk consultant"))
    assert _failed(rec) == ["V4"], rec.to_markdown()
    assert _v4(rec).data["new_ids_reusing_source_annotation_ids"] == [str(base + 1)]


def _relocate_styles(data: bytes, new: str = "parts/styles.xml") -> bytes:
    """The styles part moved out of the main part's folder: its relationship (``../parts/styles.xml``) and its
    content-type Override follow, as OPC allows."""
    rels = _read(data, "word/_rels/document.xml.rels").replace(b'Target="styles.xml"', f'Target="../{new}"'.encode())
    types = _read(data, "[Content_Types].xml").replace(b'PartName="/word/styles.xml"', f'PartName="/{new}"'.encode())
    assert rels != _read(data, "word/_rels/document.xml.rels") and types != _read(data, "[Content_Types].xml")
    return _rewrite(data, {"word/_rels/document.xml.rels": rels, "[Content_Types].xml": types},
                    rename={"word/styles.xml": new})


def test_a_part_the_main_part_relates_outside_its_folder_is_read(monkeypatch):
    """A styles part the main part relates from outside its folder is a document part all the same: its revision ids
    count, the verifier's own listing finds it, a DOCTYPE in it is refused at attach time, and a folder-only listing
    shared by plan and apply is caught by V4."""
    base = ooxml.max_revision_id(DocxPackage(PLAIN))
    data = _relocate_styles(_annotated(PLAIN, {"style": base + 1}))
    pkg = DocxPackage(data)
    assert "parts/styles.xml" in pkg.wordprocessing_xml_parts()
    assert "parts/styles.xml" in VPackage(data).document_xml_parts()
    assert ooxml.max_revision_id(pkg) == base + 1
    plan, out, rec = _run(data, _tracked_replace("FM Global", "the Owner's risk consultant"))
    assert rec.passed, rec.to_markdown()
    assert min(int(i) for i in _new_ins_ids(data, out)) > base + 1
    with pytest.raises(XmlDoctype) as e:
        DocxPackage(_rewrite(data, {"parts/styles.xml": _with_doctype(_read(data, "parts/styles.xml"))})
                    ).check_no_doctype()
    assert e.value.part == "parts/styles.xml"
    listed = DocxPackage.wordprocessing_xml_parts
    monkeypatch.setattr(DocxPackage, "wordprocessing_xml_parts",
                        lambda self: [p for p in listed(self) if p.startswith("word/")])
    plan, out, rec = _run(data, _tracked_replace("FM Global", "the Owner's risk consultant"))
    assert _failed(rec) == ["V4"], rec.to_markdown()
    assert _v4(rec).data["new_ids_reusing_source_annotation_ids"] == [str(base + 1)]


def test_planted_a_transform_without_the_ceiling_is_caught_by_v4(monkeypatch):
    """A wrong implementation shared by plan and apply: the ceiling gone, the ids run past 2147483647; V4 says so."""
    monkeypatch.setattr(base_module, "MAX_REVISION_ID", 10 ** 12)
    full = _annotated(PLAIN, {"perm": ooxml.MAX_REVISION_ID - 1})
    plan, out, rec = _run(full, _tracked_replace("FM Global", "the Owner's risk consultant"))
    assert _failed(rec) == ["V4"], rec.to_markdown()
    past = {int(i) for i in _v4(rec).data["new_ids_out_of_range"]}
    assert min(past) == ooxml.MAX_REVISION_ID + 1
    assert {int(i) for i in _new_ins_ids(full, out)} <= past                 # every new insertion (and deletion)


# ---- C10: a document type declaration ---------------------------------------------------------------------------------

@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_a_part_with_a_doctype_is_refused_however_it_is_encoded(tmp_path, encoding):
    doc = _read(PLAIN, MAIN).replace(b"FM Global", b"FM Global and &owner;")
    data = _rewrite(PLAIN, {MAIN: _with_doctype(doc, encoding)})
    raw = etree.fromstring(_read(data, MAIN), etree.XMLParser(resolve_entities=False))
    text = "".join(t.text or "" for t in raw.iter(W + "t"))
    assert "Global Risk Consultants" not in text and "FM Global and " in text   # what a reading would silently lose
    assert (b"<!DOCTYPE" in _read(data, MAIN)) == (encoding == "utf-8")   # a byte search misses UTF-16
    with pytest.raises(XmlDoctype) as e:
        DocxPackage(data).xml(MAIN)
    assert e.value.code == "xml-dtd" and e.value.part == MAIN
    with pytest.raises(VDoctype) as v:                                     # the verifier's own reader, on its own
        VPackage(data).xml(MAIN)
    assert v.value.code == "xml-dtd"
    path = tmp_path / "DTD.docx"
    path.write_bytes(data)
    found = triage_file(path)
    assert found.state == "refused" and found.facts["part"] == MAIN
    assert found.reason.startswith("DTD.docx: word/document.xml carries an XML document type declaration")
    assert found.reason.count(". ") == 0 and found.reason.endswith(".")        # one sentence, the file and what to do


def test_triage_finds_a_doctype_in_a_part_it_does_not_otherwise_read(tmp_path):
    web = _with_doctype(_read(PLAIN, "word/webSettings.xml"), "utf-16")
    path = tmp_path / "WEB.docx"
    path.write_bytes(_rewrite(PLAIN, {"word/webSettings.xml": web}))
    found = triage_file(path)
    assert found.state == "refused" and found.facts["part"] == "word/webSettings.xml"


def test_an_svg_picture_with_a_doctype_is_not_the_documents():
    svg = (b'<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
           b'"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd"><svg xmlns="http://www.w3.org/2000/svg"/>')
    types = _read(PLAIN, "[Content_Types].xml").replace(
        b"<Default ", b'<Default Extension="svg" ContentType="image/svg+xml"/><Default ', 1)
    data = _rewrite(PLAIN, {"[Content_Types].xml": types, "word/media/logo.svg": svg})
    pkg = DocxPackage(data)
    assert "word/media/logo.svg" not in pkg.wordprocessing_xml_parts()
    assert "word/media/logo.svg" not in VPackage(data).document_xml_parts()
    pkg.check_no_doctype()
    plan, out, rec = _run(data, _tracked_replace("FM Global", "the Owner's risk consultant"))
    assert rec.passed, rec.to_markdown()


def test_a_file_with_a_doctype_is_set_aside_with_its_code(tmp_path):
    path = tmp_path / "DTD.docx"
    path.write_bytes(_rewrite(PLAIN, {"word/styles.xml": _with_doctype(_read(PLAIN, "word/styles.xml"))}))
    spec = _tracked_replace("FM Global", "the Owner's risk consultant")
    fp = plan_file(path, spec)
    assert fp.status == "exception" and fp.exception["code"] == "xml-dtd"
    assert fp.exception["details"]["part"] == "word/styles.xml"
    b = run_batch([str(path)], "edit-spec", {}, tmp_path / "out", workers=1, renderer="none", do_render=False,
                  spec=spec)
    assert b.files[0].status == "EXCEPTION" and b.files[0].exception["code"] == "xml-dtd"
    assert b.files[0].exception["kind"] == "unreadable"
    assert not list((tmp_path / "out").rglob("outputs/*.docx"))
    assert explain_failure("xml-dtd", "x")[0] == "XML Document Type Declaration" and "xml-dtd" in FAILURE_EXPLANATIONS


def test_planted_a_transform_blind_to_doctypes_is_stopped_by_the_verifiers_own_reader(monkeypatch):
    """A wrong implementation on the transform side (its DOCTYPE check gone): the edit is planned and applied, and the
    verifier, reading the source with its own code, refuses with the same code instead of judging a misread part."""
    data = _rewrite(PLAIN, {MAIN: _with_doctype(_read(PLAIN, MAIN))})
    monkeypatch.setattr(package_module, "has_doctype", lambda root: False)
    pkg = DocxPackage(data)
    plan, frags = plan_spec(pkg, _tracked_replace("FM Global", "the Owner's risk consultant"))
    out = apply_spec(pkg, frags).output_bytes()
    with pytest.raises(VDoctype) as e:
        run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")
    record = diagnostics.classify(e.value, "verify")
    assert record["code"] == "xml-dtd" and record["kind"] == "unreadable"


def test_an_attached_workbook_with_a_doctype_is_refused_on_both_sides(tmp_path):
    data = xlsx([["Tag", "Description"], ["P-1", "Fire pump"]])
    strings = _read(data, "xl/sharedStrings.xml").decode("utf-8").replace("Fire pump", "Fire pump &owner;")
    bad = _rewrite(data, {"xl/sharedStrings.xml": _with_doctype(strings.encode("utf-8"))})
    with pytest.raises(JobException) as e:
        tables_module.xlsx_rows(bad, "Pumps", "the pump table")
    assert e.value.code == "table-value" and "document type declaration" in e.value.reason
    assert tables_module.xlsx_rows(data, "Pumps", "the pump table")[1] == ["P-1", "Fire pump"]
    path = tmp_path / "pumps.xlsx"
    path.write_bytes(bad)
    with pytest.raises(ValueError, match="document type declaration"):
        vtables.rows_of(str(path), "Pumps")
