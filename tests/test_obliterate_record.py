"""The Obliterate record (``verify/difference.py``): what differs between an original and a copy, with nothing
predicted.

A custom method written in Obliterate mode predicts nothing, so its copy is read by ``verify.run_difference``: the
independent verifier's own reading of what is different, and the checks that need no prediction. What is pinned here:
every kind of difference is said in a sentence and kept as data, none of them ever a failure; what Word repairs or
refuses (a part that is not XML, a relationship to nothing, a member with no content type, an ``r:id`` that names no
relationship, a revision id Word cannot tell apart or read, a new schema error, tracked-change markup outside Word's
grammar) is a FAIL, planted here one at a time; what the original already got wrong is not held against the copy; and
a copy that is no document at all is not described."""
from __future__ import annotations

import io
import zipfile
from typing import Callable, Dict, Optional

import pytest
from lxml import etree

from make_fixtures import build
from spec_surgeon.preview import text_changes_of
from spec_surgeon.verify import CheckResult, difference_lines, run_difference
from spec_surgeon.verify import difference

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"
DOCUMENT = "word/document.xml"
DOCUMENT_RELS = "word/_rels/document.xml.rels"
IMAGE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 40


def rezip(data: bytes, changes: Dict[str, Optional[bytes]]) -> bytes:
    """``data`` with the named members replaced (bytes), added (a new name) or removed (None), every other member
    as it was and in its place."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            if info.filename in changes:
                if changes[info.filename] is not None:
                    zout.writestr(info.filename, changes[info.filename])
            else:
                zout.writestr(info, zin.read(info.filename))
        for name, value in changes.items():
            if value is not None and name not in zin.namelist():
                zout.writestr(name, value)
    return out.getvalue()


def member(data: bytes, name: str) -> bytes:
    return zipfile.ZipFile(io.BytesIO(data)).read(name)


def edited(data: bytes, name: str, change: Callable) -> bytes:
    """``data`` with the XML member ``name`` changed by ``change(root)``."""
    root = etree.fromstring(member(data, name))
    change(root)
    return rezip(data, {name: etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)})


def replaced(data: bytes, name: str, old: bytes, new: bytes, count: int = 1) -> bytes:
    raw = member(data, name)
    assert raw.count(old) >= 1, (name, old)
    return rezip(data, {name: raw.replace(old, new, count)})


@pytest.fixture(scope="module")
def source() -> bytes:
    return build("21 00 00", "SPEC 0")


def record(source: bytes, copy: bytes):
    return run_difference(source, copy, "source.docx", "copy.docx")


def said(rec) -> str:
    return "\n".join(difference_lines(rec))


def statuses(rec) -> Dict[str, str]:
    return {c.id: c.status for c in rec.checks}


# ---- the shape of the record ---------------------------------------------------------------------------------------

def test_an_identical_copy_differs_in_nothing_and_fails_nothing(source):
    rec = record(source, source)
    assert rec.passed and rec.job == difference.JOB and rec.mode == difference.MODE
    assert difference.NOTE in rec.notes
    assert difference_lines(rec) == ["Nothing differs: every part of the copy is the original's, byte for byte."]
    assert text_changes_of(rec.to_dict()) == []
    assert statuses(rec) == {"V1": "PASS", "V2": "NA", "V4": "PASS", "V5a": "PASS", "V5b": "NA", "V5c": "PASS",
                             "V8": "NA", "V10": "NA", "D1": "NA", "V6": "NA", "V7": "NA", "V9": "NA"}
    # what compares a copy with a prediction says it has none, in words a reader of the record understands
    assert "nothing was predicted" in rec.get("V5b").summary and "asserts nothing" in rec.get("V10").summary
    # every reader of a suite record reads this one: the dict, the markdown, the sentences from either
    as_dict = rec.to_dict()
    assert difference_lines(as_dict) == difference_lines(rec) and "What changed" in rec.to_markdown("r.json")
    assert difference_lines({"checks": [{"id": "V1"}]}) == [] and difference_lines({}) == []


def test_the_words_a_copy_adds_and_removes_are_listed_and_never_a_failure(source):
    copy = replaced(source, DOCUMENT, b"FM Global and all equipment", b"GRC and all equipment")
    rec = record(source, copy)
    assert rec.passed and rec.get("V2").status == "NA"
    [entry] = text_changes_of(rec.to_dict())
    assert entry["part"] == DOCUMENT and entry["removed"] == ["FM Global"] and entry["added"] == ["GRC"]
    assert rec.get("V2").data["actual"][0]["after"].startswith("A.The Owner's insurer is GRC")
    # the words are V2's to list: D1 does not call the paragraph a formatting change, or say nothing differs
    assert "formatting or markup" not in said(rec) and "Nothing differs" not in said(rec)


def test_a_change_inside_tracked_changes_lists_what_accepting_and_rejecting_them_would_show(source):
    """A part that carries tracked changes is read three ways, as V2 reads it: as it stands, with every change
    accepted and with every change rejected. Words changed inside a tracked insertion show in the first two and not
    in the third, where the insertion is gone on both sides."""
    assert b"<w:ins " in member(source, DOCUMENT)

    def reword(root):
        next(next(root.iter(W + "ins")).iter(W + "t")).text = "CHANGED"
    rec = record(source, edited(source, DOCUMENT, reword))
    views = rec.get("V2").data["views"]
    assert views["parts"] == [DOCUMENT] and rec.passed
    assert len(views["accepted"]) == 1 and "CHANGED" in views["accepted"][0]["after"] and views["rejected"] == []
    assert any("CHANGED" in entry["added"] for entry in text_changes_of(rec.to_dict()))


# ---- what differs, said and kept -----------------------------------------------------------------------------------

def test_what_a_reader_would_count_on_the_page_is_counted_before_and_after(source):
    def drop(root):
        body = root.find(W + "body")
        body.remove(body.find(W + "tbl"))
        body.remove(body.find(W + "p"))
    rec = record(source, edited(source, DOCUMENT, drop))
    before, after = rec.get("D1").data["counts_before"], rec.get("D1").data["counts_after"]
    assert before["tables"] == 1 and after["tables"] == 0 and after["paragraphs"] == before["paragraphs"] - 7
    lines = difference_lines(rec)
    assert f"Paragraphs: {before['paragraphs']} before, {after['paragraphs']} after." in lines
    assert "Tables: 1 before, 0 after." in lines
    assert rec.get("D1").data["counts"] == {"paragraphs": [before["paragraphs"], after["paragraphs"]],
                                            "tables": [1, 0]}
    assert rec.passed                                   # a table removed is a change the user reads, not a failure


def test_pictures_text_boxes_fields_and_page_breaks_are_counted(source):
    def add(root):
        body = root.find(W + "body")
        p = etree.SubElement(body[0], W + "r")
        etree.SubElement(p, W + "br").set(W + "type", "page")
        etree.SubElement(body[1], W + "fldSimple").set(W + "instr", " PAGE ")
        etree.SubElement(etree.SubElement(body[2], W + "r"), W + "drawing")
        box = etree.SubElement(etree.SubElement(body[2], W + "r"), W + "pict")
        shape = etree.SubElement(box, "{urn:schemas-microsoft-com:vml}textbox")
        content = etree.SubElement(shape, W + "txbxContent")
        etree.SubElement(content, W + "p")
    rec = record(source, edited(source, DOCUMENT, add))
    moved = rec.get("D1").data["counts"]
    assert {key: after - before for key, (before, after) in moved.items()} == {
        "paragraphs": 1, "pictures": 1, "text_boxes": 1, "fields": 1, "page_breaks": 1}
    assert "Text boxes: 0 before, 1 after." in difference_lines(rec)


def test_formatting_that_changed_without_the_words_is_said_per_part(source):
    def bold(root):
        runs = [r for r in root.iter(W + "r") if r.find(W + "t") is not None]
        for r in runs[:2]:
            etree.SubElement(r, W + "rPr").append(etree.Element(W + "b"))
            r.insert(0, r[-1])
    rec = record(source, edited(source, DOCUMENT, bold))
    assert rec.get("D1").data["formatting_only"] == {DOCUMENT: 2}
    assert ("In the body, the formatting or markup of 2 paragraphs changed without changing their words."
            in difference_lines(rec))
    assert text_changes_of(rec.to_dict()) == []
    header = next(n for n in zipfile.ZipFile(io.BytesIO(source)).namelist() if n.startswith("word/header"))
    rec = record(source, edited(source, header, lambda root: next(root.iter(W + "p")).set(W + "rsidR", "00AB12CD")))
    assert (f"In a header ({header}), the formatting or markup of 1 paragraph changed without changing its words."
            in difference_lines(rec))


def test_a_paragraph_that_came_or_went_does_not_shift_the_formatting_count(source):
    """Paragraphs are paired by their words, so one removed in the middle does not make every later paragraph look
    changed."""
    def drop_and_bold(root):
        body = root.find(W + "body")
        plain = [p for p in body.findall(W + "p") if p.find(W + "pPr") is None]
        body.remove(plain[1])
        etree.SubElement(plain[-1], W + "pPr").append(etree.Element(W + "keepNext"))
        plain[-1].insert(0, plain[-1][-1])
    rec = record(source, edited(source, DOCUMENT, drop_and_bold))
    assert rec.get("D1").data["formatting_only"] == {DOCUMENT: 1}


def test_a_tables_own_layout_and_a_sections_page_setup_are_said(source):
    def widen(root):
        next(root.iter(W + "gridCol")).set(W + "w", "4000")
    rec = record(source, edited(source, DOCUMENT, widen))
    assert rec.get("D1").data["tables"] == {DOCUMENT: 1} and rec.get("D1").data["formatting_only"] == {}
    assert ("In the body, the layout or formatting of 1 table changed (widths, borders, shading, or row and cell "
            "settings)." in difference_lines(rec))

    def margins(root):
        list(root.iter(W + "pgMar"))[-1].set(W + "left", "1440")
    rec = record(source, edited(source, DOCUMENT, margins))
    assert rec.get("D1").data["page_setup"] == 1
    assert any(line.startswith("Page setup: the settings of 1 section changed") for line in difference_lines(rec))


def test_styles_numbering_and_settings_are_read_by_what_they_hold(source):
    def styles(root):
        found = root.findall(W + "style")
        root.remove(found[5])
        found[6].set(W + "customStyle", "1")
        new = etree.SubElement(root, W + "style")
        new.set(W + "type", "paragraph")
        new.set(W + "styleId", "ObliterateNew")
    removed_id = etree.fromstring(member(source, "word/styles.xml")).findall(W + "style")[5].get(W + "styleId")
    changed_id = etree.fromstring(member(source, "word/styles.xml")).findall(W + "style")[6].get(W + "styleId")
    rec = record(source, edited(source, "word/styles.xml", styles))
    assert rec.get("D1").data["styles"] == {"added": ["ObliterateNew"], "removed": [removed_id],
                                            "changed": [changed_id]}
    assert (f"Styles: 1 added (ObliterateNew); 1 removed ({removed_id}); 1 changed ({changed_id})."
            in difference_lines(rec))

    def numbering(root):
        next(root.iter(W + "lvlText")).set(W + "val", "%1)")
    rec = record(source, edited(source, "word/numbering.xml", numbering))
    assert rec.get("D1").data["numbering"]["changed"] and "List numbering: 1 changed." in difference_lines(rec)

    def settings(root):
        root.insert(0, etree.Element(W + "evenAndOddHeaders"))
    rec = record(source, edited(source, "word/settings.xml", settings))
    assert rec.get("D1").data["settings"]["added"] == ["evenAndOddHeaders"]
    assert "Document settings: added evenAndOddHeaders." in difference_lines(rec)


def with_picture(data: bytes, *, content_type: bool = True, relationship: bool = True) -> bytes:
    """``data`` with a picture member added: its content type and its relationship from the body, unless left out."""
    changes: Dict[str, Optional[bytes]] = {"word/media/logo.png": PNG}
    if content_type:
        types = etree.fromstring(member(data, "[Content_Types].xml"))
        if not any(d.get("Extension") == "png" for d in types.findall(CT + "Default")):
            default = etree.Element(CT + "Default")
            default.set("Extension", "png")
            default.set("ContentType", "image/png")
            types.insert(0, default)
        changes["[Content_Types].xml"] = etree.tostring(types, xml_declaration=True, encoding="UTF-8",
                                                         standalone=True)
    if relationship:
        rels = etree.fromstring(member(data, DOCUMENT_RELS))
        rel = etree.SubElement(rels, REL + "Relationship")
        rel.set("Id", "rIdLogo")
        rel.set("Type", IMAGE_REL)
        rel.set("Target", "media/logo.png")
        changes[DOCUMENT_RELS] = etree.tostring(rels, xml_declaration=True, encoding="UTF-8", standalone=True)
    return rezip(data, changes)


def test_parts_and_relationships_that_came_and_went_are_named(source):
    rec = record(source, with_picture(source))
    lines = difference_lines(rec)
    assert rec.passed, rec.get("V1").summary
    assert "Relationships: 1 added (image → logo.png)." in lines
    assert f"Parts added: word/media/logo.png ({len(PNG)} bytes)." in lines
    assert rec.get("V1").data["added"] == ["word/media/logo.png"]
    # a part whose relationship is removed while the part stays is named as left behind
    header = next(n for n in zipfile.ZipFile(io.BytesIO(source)).namelist() if n.startswith("word/header"))

    def unlink(root):
        for rel in root.findall(REL + "Relationship"):
            if rel.get("Target") == header[len("word/"):]:
                root.remove(rel)
    rec = record(source, edited(source, DOCUMENT_RELS, unlink))
    assert rec.get("V1").data["orphaned"] == [header]
    assert f"Left in the package but no longer referenced by anything: {header}." in difference_lines(rec)
    assert "Relationships: 1 removed (header → " + header[len("word/"):] + ")." in difference_lines(rec)
    # the body still names that relationship, which Word repairs: the same copy fails V1 for it
    assert rec.get("V1").status == "FAIL" and "names no relationship" in " ".join(rec.get("V1").data["integrity"])
    rec = record(source, rezip(source, {"docProps/thumbnail.jpeg": None}))
    assert "Parts removed: docProps/thumbnail.jpeg." in difference_lines(rec)


def test_a_changed_part_the_report_does_not_read_is_named_as_unread(source):
    """The report says what it read. A theme, a picture's bytes, a data store: it compares their bytes and says so,
    so nothing that changed goes unsaid."""
    copy = replaced(source, "word/theme/theme1.xml", b"<a:srgbClr val=", b"<a:srgbClr  val=")
    rec = record(source, copy)
    assert rec.get("D1").data["unread"] == ["word/theme/theme1.xml"] and rec.passed
    assert ("Changed, and not read by this report (it compares their bytes, not what they hold): "
            "word/theme/theme1.xml — the theme." in difference_lines(rec))
    copy = replaced(source, "customXml/item1.xml", b"APA", b"WRONG OWNER")
    assert record(source, copy).get("D1").data["unread"] == ["customXml/item1.xml"]


def test_a_change_no_sentence_counts_still_names_the_part(source):
    """Markup outside every paragraph, table and section (here a body-level bookmark end) is in no count: the report
    then says which part changed and that it counted nothing, never that nothing differs."""
    def mark(root):
        root.find(W + "body").insert(0, etree.Element(W + "proofErr"))
    rec = record(source, edited(source, DOCUMENT, mark))
    [line] = difference_lines(rec)
    assert line.startswith(f"1 part changed ({DOCUMENT}), with no difference this report counts")


# ---- what Word repairs or refuses: planted one at a time ------------------------------------------------------------

def failed(rec):
    return sorted(c.id for c in rec.checks if c.status == "FAIL")


def test_a_part_that_is_no_longer_xml_fails_the_package_check(source):
    broken = rezip(source, {"word/footer1.xml": member(source, "word/footer1.xml")[:-9]})
    rec = record(source, broken)
    assert failed(rec) == ["V1"] and not rec.passed
    assert any(p.startswith("word/footer1.xml: not well-formed XML") for p in rec.get("V1").data["integrity"])
    # a part that is not a text part is opened too
    rec = record(source, rezip(source, {"word/settings.xml": b"<w:settings"}))
    assert failed(rec) == ["V1"]
    assert any(p.startswith("word/settings.xml: not well-formed XML") for p in rec.get("V1").data["integrity"])


def test_a_relationship_to_nothing_and_a_member_with_no_content_type_fail_it(source):
    def dangle(root):
        rel = etree.SubElement(root, REL + "Relationship")
        rel.set("Id", "rIdGone")
        rel.set("Type", IMAGE_REL)
        rel.set("Target", "media/gone.png")
    rec = record(source, edited(source, DOCUMENT_RELS, dangle))
    assert failed(rec) == ["V1"] and "dangling relationship" in " ".join(rec.get("V1").data["integrity"])
    rec = record(source, with_picture(source, content_type=False))
    assert failed(rec) == ["V1"] and "word/media/logo.png" in " ".join(rec.get("V1").data["integrity"])


def test_a_reference_that_names_no_relationship_fails_it(source):
    def repoint(root):
        next(root.iter(W + "headerReference")).set(R + "id", "rId999")
    rec = record(source, edited(source, DOCUMENT, repoint))
    assert failed(rec) == ["V1"] and "rId999" in " ".join(rec.get("V1").data["integrity"])


def test_what_the_original_already_got_wrong_is_not_held_against_the_copy(source):
    def dangle(root):
        rel = etree.SubElement(root, REL + "Relationship")
        rel.set("Id", "rIdGone")
        rel.set("Type", IMAGE_REL)
        rel.set("Target", "media/gone.png")
    flawed = edited(source, DOCUMENT_RELS, dangle)
    copy = replaced(flawed, DOCUMENT, b"FM Global and all equipment", b"GRC and all equipment")
    rec = record(flawed, copy)
    assert rec.passed and rec.get("V1").data["integrity"] == []


def tracked(root, *, rev_id: str = "9001", author: Optional[str] = "Tester", tag: str = "ins"):
    """A tracked insertion (or deletion) of one run, added to the body's first paragraph."""
    p = root.find(W + "body").find(W + "p")
    mark = etree.SubElement(p, W + tag)
    mark.set(W + "id", rev_id)
    if author is not None:
        mark.set(W + "author", author)
    mark.set(W + "date", "2026-11-10T00:00:00Z")
    run = etree.SubElement(mark, W + "r")
    etree.SubElement(run, W + ("delText" if tag == "del" else "t")).text = "NEW"
    return mark


def test_a_well_formed_tracked_change_passes_and_is_counted(source):
    rec = record(source, edited(source, DOCUMENT, tracked))
    assert rec.passed, [(c.id, c.summary) for c in rec.checks if c.status == "FAIL"]
    before = sum(rec.get("V4").data["counts_before"].values())
    assert sum(rec.get("V4").data["counts_after"].values()) == before + 1
    assert f"Tracked changes: {before} before, {before + 1} after (+1 tracked insertions)." in difference_lines(rec)


def test_revision_ids_word_cannot_tell_apart_or_read_fail_the_id_check(source):
    def twice(root):
        tracked(root)
        tracked(root)
    rec = record(source, edited(source, DOCUMENT, twice))
    assert failed(rec) == ["V4"] and rec.get("V4").data["duplicate_ids_all_kinds"] == ["9001"]
    rec = record(source, edited(source, DOCUMENT, lambda root: tracked(root, rev_id="abc")))
    assert "V4" in failed(rec) and rec.get("V4").data["new_ids_out_of_range"] == ["abc"]

    def with_bookmark(root):
        mark = etree.Element(W + "bookmarkStart")
        mark.set(W + "id", "9001")
        mark.set(W + "name", "kept")
        root.find(W + "body").find(W + "p").insert(0, mark)
    marked = edited(source, DOCUMENT, with_bookmark)
    rec = record(marked, edited(marked, DOCUMENT, tracked))
    assert failed(rec) == ["V4"] and rec.get("V4").data["new_ids_reusing_source_annotation_ids"] == ["9001"]
    # an id the original itself duplicated is the original's: a copy that leaves it alone does not fail for it
    doubled = edited(source, DOCUMENT, twice)
    rec = record(doubled, replaced(doubled, DOCUMENT, b"FM Global and all equipment", b"GRC and all equipment"))
    assert rec.passed


def test_a_new_schema_error_fails_the_schema_check_and_an_old_one_does_not(source):
    def wrong_order(root):
        p = [p for p in root.find(W + "body").findall(W + "p") if p.find(W + "pPr") is None][0]
        p.append(etree.Element(W + "pPr"))          # pPr after the runs: not the schema's order
    bad = edited(source, DOCUMENT, wrong_order)
    rec = record(source, bad)
    assert failed(rec) == ["V5a"] and DOCUMENT in rec.get("V5a").data["new_errors"]
    rec = record(bad, replaced(bad, DOCUMENT, b"FM Global and all equipment", b"GRC and all equipment"))
    assert rec.passed and rec.get("V5a").data["baseline"][DOCUMENT] >= 1


def test_tracked_change_markup_outside_words_grammar_fails_the_grammar_check(source):
    def stray(root):
        run = etree.SubElement(root.find(W + "body").find(W + "p"), W + "r")
        etree.SubElement(run, W + "delText").text = "gone"       # deleted text outside a w:del
    rec = record(source, edited(source, DOCUMENT, stray))
    assert "V5c" in failed(rec) and rec.get("V5c").data["errors"]
    rec = record(source, edited(source, DOCUMENT, lambda root: tracked(root, author=None)))
    assert "V5c" in failed(rec)


def test_every_failure_is_counted_at_once(source):
    def both(root):
        tracked(root)
        tracked(root)
        next(root.iter(W + "headerReference")).set(R + "id", "rId999")
    rec = record(source, edited(source, DOCUMENT, both))
    assert failed(rec) == ["V1", "V4"] and not rec.passed
    # the report of what changed is still written beside the failures
    assert any(line.startswith("Tracked changes:") for line in difference_lines(rec))


# ---- a copy that is no document ------------------------------------------------------------------------------------

def test_a_copy_that_is_not_a_package_is_not_described(source):
    with pytest.raises(Exception):
        record(source, b"not a zip at all")
    with pytest.raises(ValueError, match="no main document part"):
        record(source, rezip(source, {DOCUMENT: None}))


def test_the_record_keeps_the_runners_checks_for_the_runner(source):
    """V6, V7 and V9 are the runner's, as in the suite: it sets them on this record the way it sets them on any."""
    rec = record(source, source)
    rec.set(CheckResult("V6", "Automated render comparison", "FAIL", "the renderer could not open the copy"))
    assert not rec.passed and failed(rec) == ["V6"]
