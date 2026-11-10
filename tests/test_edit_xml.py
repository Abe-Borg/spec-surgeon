"""edit_xml — the raw-XML operation of Obliterate mode (docs/BACKLOG.md O3; CLAUDE.md, Modes).

One stated change to the XML of one part: an XPath, an exact count, one action. It is a built-in operation, so it
predicts first: what it predicts is the edit itself, and the verifier's own twin (V10 ``xml_edit``, verify/xmledit.py)
selects with its own evaluation, makes the same edit with its own code and compares the part whole. What the edit
does to the words is listed from the copy (D1), never predicted.

Pinned here: every action, proven; every refusal; every planted defect — a wrong output, a wrong implementation the
plan and the transform share, a plan that chose other elements, lied about the count or should have been refused —
caught by the verifier; the text checks leave a part to the whole-part comparison only where that comparison exists;
the operation runs as a step of its own, so "clear the obstacle, then the same plan" is two verified steps; and it
exists in Obliterate mode alone: Safe and Default are told nothing of it and refuse it at the gate.
"""
from __future__ import annotations

import copy
import io
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Tuple

import pytest
from lxml import etree

from make_fixtures import build
from spec_surgeon import catalog, codegen, compiler, recipes
from spec_surgeon.chain import validate_chain
from spec_surgeon.compiler import _Batch, compose_submission
from spec_surgeon.editspec import SpecError, apply_spec, plan_chain, plan_spec, split_into_stages, validate_spec
from spec_surgeon.ops import OPS, JobException, get_op, merge_fragments
from spec_surgeon.ops import edit_xml as ex
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.preview import raw_listing
from spec_surgeon.report import FAILURE_EXPLANATIONS
from spec_surgeon.ui_feedback import refusal_message
from spec_surgeon.verify import difference_lines, run_suite
from spec_surgeon.verify import xmledit as vx

DOC = "word/document.xml"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
BOX = ('<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
       'xmlns:v="urn:schemas-microsoft-com:vml"><w:pict><v:shape><v:textbox><w:txbxContent><w:p><w:r>'
       '<w:t>SUPERSEDED STAMP</w:t></w:r></w:p></w:txbxContent></v:textbox></v:shape></w:pict></w:r>')
CLEAR_BOX = {"part": DOC, "xpath": "//w:r[w:pict//w:txbxContent]", "count": 1, "action": "remove"}
DELETE_HEADING = {"request": "delete the revision history heading", "summary": "Delete the heading.",
                  "mode": "content",
                  "ops": [{"op": "delete_paragraphs",
                           "select": {"paragraphs": {"text_regex": "^PROJECT REVISION HISTORY"}}, "params": {}}]}


@pytest.fixture(scope="module")
def source() -> bytes:
    return build("21 00 00", "SPEC 0")


def with_text_box(data: bytes) -> bytes:
    """The fixture with a text box in its PROJECT REVISION HISTORY paragraph: what a built-in deletion refuses."""
    pkg = DocxPackage(data)
    root = pkg.xml(DOC)
    for p in root.iter(W + "p"):
        if "".join(t.text or "" for t in p.iter(W + "t")) == "PROJECT REVISION HISTORY":
            p.append(etree.fromstring(BOX))
            break
    else:
        raise AssertionError("the fixture has no such paragraph")
    work = pkg.copy()
    work.write_xml(DOC, root)
    return work.output_bytes()


def planned(src: bytes, **params) -> Tuple[Any, Dict[str, Any]]:
    frag = get_op("edit_xml").plan(DocxPackage(src), {}, params)
    return frag, merge_fragments([frag]).to_dict()


def edit(src: bytes, **params):
    """Plan, apply, verify: ``(plan, output bytes, record)``."""
    frag, plan = planned(src, **params)
    work = DocxPackage(src).copy()
    get_op("edit_xml").apply(work, frag)
    out = work.output_bytes()
    return plan, out, run_suite(src, out, plan, "source.docx", "copy.docx")


def part_of(data: bytes, name: str = DOC):
    return etree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read(name))


def with_part(data: bytes, name: str, root) -> bytes:
    work = DocxPackage(data).copy()
    work.write_xml(name, root)
    return work.output_bytes()


def failed(rec) -> Dict[str, str]:
    return {c.id: c.summary for c in rec.checks if c.status == "FAIL"}


def refusal(src: bytes, **params) -> JobException:
    with pytest.raises(JobException) as caught:
        get_op("edit_xml").plan(DocxPackage(src), {}, params)
    return caught.value


# ---- the edit, proven ----------------------------------------------------------------------------------------------

def test_removing_elements_takes_exactly_those_and_the_copy_is_verified(source):
    plan, out, rec = edit(source, part=DOC, xpath="//w:tbl", count=1, action="remove")
    assert rec.passed, failed(rec)
    assert len(list(part_of(source).iter(W + "tbl"))) == 1 and not list(part_of(out).iter(W + "tbl"))
    # predicted first: the edit itself, as an assertion; no text diff, no layout reference, never tracked
    [assertion] = plan["property_assertions"]
    assert assertion["kind"] == "xml_edit" and assertion["part"] == DOC and assertion["count"] == 1
    assert assertion["action"] == "remove" and len(assertion["places_sha256"]) == 64
    assert plan["diffs"] == [] and plan["intended_parts"] == [DOC] and plan["mode"] == "untracked"
    assert plan["geometry_reference"] == "none" and plan["rev_delta"] == {"ins": 0, "del": 0}
    assert "xml_edit remove ×1" in rec.get("V10").summary
    # no other member moved
    assert rec.get("V1").data["changed"] == [DOC]


def test_the_words_a_raw_edit_changes_are_listed_from_the_copy_never_predicted(source):
    plan, out, rec = edit(source, part=DOC, xpath="//w:tbl", count=1, action="remove")
    v2 = rec.get("V2")
    assert v2.status == "PASS" and v2.data["raw_parts"] == [DOC] and v2.data["missing"] == [] == v2.data["unpredicted"]
    assert len(v2.data["actual"]) == 6 and "listed" in v2.summary and "not predicted" in v2.summary
    words, other = raw_listing(rec)
    assert [w["removed"] for w in words][:3] == [["Rev"], ["Date"], ["Description"]] and all(not w["added"] for w in words)
    assert other == difference_lines(rec) and "Tables: 1 before, 0 after." in other
    assert any(line.startswith("Paragraphs:") for line in other)
    assert rec.get("D1").status == "NA"                         # a report, never a verdict
    # a record of a plan without a raw edit has no such listing, and no D1
    spec = {"request": "r", "summary": "s", "mode": "formatting",
            "ops": [{"op": "set_page_setup", "select": {"sections": "all"}, "params": {"header_distance": 576}}]}
    try:
        ordinary, frags = plan_spec(DocxPackage(source), spec)
    except JobException:
        return                                                  # (the fixture's op vocabulary: nothing more to say)
    made = apply_spec(DocxPackage(source), frags).output_bytes()
    plain = run_suite(source, made, ordinary.to_dict(), "a", "b")
    assert raw_listing(plain) is None and plain.get("D1") is None and "raw_parts" not in plain.get("V2").data


@pytest.mark.parametrize("params,check", [
    ({"xpath": "//w:pgMar", "count": 2, "action": "set_attribute", "name": "w:header", "value": "576"},
     lambda root: [m.get(W + "header") for m in root.iter(W + "pgMar")] == ["576", "576"]),
    ({"xpath": "//w:p[.//w:t[contains(., 'FM Global')]]", "count": 3, "action": "remove"},
     lambda root: len(list(root.iter(W + "p"))) == 43),
    ({"xpath": "//w:body/w:p[2]", "count": 1, "action": "insert", "position": "after",
      "xml": "<w:p>\n  <w:r>\n    <w:t>Added line</w:t>\n  </w:r>\n</w:p>"},
     lambda root: "".join(root.find(W + "body")[2].itertext()) == "Added line"),
    ({"xpath": "//w:body/w:p[2]", "count": 1, "action": "insert", "position": "before", "xml": "<w:p/>"},
     lambda root: root.find(W + "body")[1].tag == W + "p" and len(root.find(W + "body")[1]) == 0),
    ({"xpath": "//w:body/w:p[1]", "count": 1, "action": "insert", "position": "last_child",
      "xml": "<w:r><w:t>, revised</w:t></w:r>"},
     lambda root: "".join(root.find(W + "body")[0].itertext()) == "SECTION 21 00 00, revised"),
    ({"xpath": "//w:body/w:p[1]", "count": 1, "action": "insert", "position": "first_child",
      "xml": "<w:pPr><w:keepNext/></w:pPr>"},
     lambda root: root.find(W + "body")[0][0].tag == W + "pPr"),
    ({"xpath": "//w:body/w:p[2]", "count": 1, "action": "replace",
      "xml": "<w:p><w:r><w:t>Replaced</w:t></w:r></w:p><w:p/>"},
     lambda root: "".join(root.find(W + "body")[1].itertext()) == "Replaced" and len(root.find(W + "body")[2]) == 0),
    ({"xpath": "//w:body/w:p[1]/w:r/w:t", "count": 1, "action": "set_text", "text": "SECTION 21 13 13"},
     lambda root: next(root.iter(W + "t")).text == "SECTION 21 13 13"),
    ({"xpath": "//w:ins", "count": 1, "action": "unwrap"},
     lambda root: not list(root.iter(W + "ins")) and "UL listed" in "".join(root.itertext())),
    ({"xpath": "//w:del", "count": 1, "action": "remove"},
     lambda root: not list(root.iter(W + "del")) and "black steel" not in "".join(
         t.text or "" for t in root.iter(W + "delText"))),
], ids=["set-attribute", "remove-several", "insert-after", "insert-before", "insert-last-child",
        "insert-first-child", "replace", "set-text", "unwrap", "remove-tracked-deletion"])
def test_every_action_changes_exactly_the_selected_elements_and_is_proven(source, params, check):
    plan, out, rec = edit(source, part=DOC, **params)
    assert check(part_of(out))
    # the package's own checks may still speak (a schema the edit breaks): everything about the edit itself passes
    assert {"V1", "V2", "V3", "V4", "V5b", "V10"}.isdisjoint(failed(rec)), failed(rec)
    assert rec.get("V10").data["errors"] == [] and rec.get("V10").data["unread_members"] == []


def test_unwrapping_keeps_the_children_of_the_one_child_named(source):
    """A content control goes and what it held stays: the children of its ``w:sdtContent`` take its place."""
    root = part_of(source)
    body = root.find(W + "body")
    target = body[1]
    sdt = etree.Element(W + "sdt")
    props = etree.SubElement(sdt, W + "sdtPr")
    etree.SubElement(props, W + "dataBinding").set(W + "xpath", "/root/title")
    content = etree.SubElement(sdt, W + "sdtContent")
    body.replace(target, sdt)
    content.append(target)
    bound = with_part(source, DOC, root)
    plan, out, rec = edit(bound, part=DOC, xpath="//w:sdt[w:sdtPr/w:dataBinding]", count=1, action="unwrap",
                          keep="w:sdtContent")
    assert rec.passed, failed(rec)
    after = part_of(out)
    assert not list(after.iter(W + "sdt")) and "".join(after.find(W + "body")[1].itertext()) == "SPEC 0"
    assert plan["property_assertions"][0]["keep"] == W + "sdtContent"
    assert raw_listing(rec)[0] == []                            # the words stay: nothing is listed as changed
    assert "Content controls: 1 before, 0 after." in difference_lines(rec)
    # the control's own properties cannot be kept in its place: one child of the kind, or the file is refused
    assert refusal(bound, part=DOC, xpath="//w:sdt", count=1, action="unwrap", keep="w:nothing").code == "xml-action"


def test_text_set_with_spaces_at_its_ends_is_told_to_keep_them(source):
    plan, out, rec = edit(source, part=DOC, xpath="//w:body/w:p[1]/w:r/w:t", count=1, action="set_text",
                          text=" SECTION 21 13 13 ")
    first = next(part_of(out).iter(W + "t"))
    assert first.text == " SECTION 21 13 13 " and first.get(XML_SPACE) == "preserve" and rec.passed, failed(rec)


def test_parts_beside_the_text_parts_are_compared_whole_too(source):
    """The styles part, the settings and the numbering are read by this kind when a raw edit changes them: nothing
    the edit touches is left to a check that does not read it."""
    for part, params in (
            ("word/numbering.xml", {"xpath": "(//w:lvlText)[1]", "count": 1, "action": "set_attribute",
                                    "name": "w:val", "value": "%1)"}),
            ("word/styles.xml", {"xpath": "//w:style[@w:styleId='Footer']", "count": 1, "action": "set_attribute",
                                 "name": "w:customStyle", "value": "1"}),
            ("word/settings.xml", {"xpath": "/w:settings/*[1]", "count": 1, "action": "remove"}),
            ("word/theme/theme1.xml", {"xpath": "(//a:srgbClr)[1]", "count": 1, "action": "set_attribute",
                                       "name": "val", "value": "207060"})):
        plan, out, rec = edit(source, part=part, **params)
        assert {"V1", "V2", "V10"}.isdisjoint(failed(rec)), (part, failed(rec))
        assert rec.get("V1").data["changed"] == [part] and rec.get("V10").data["unread_members"] == []
        assert rec.get("V2").data.get("raw_parts") == [part]


def test_a_prefix_the_table_does_not_know_is_given_with_the_request(source):
    root = part_of(source)
    extra = etree.SubElement(root.find(W + "body")[0], "{urn:example:marks}mark")
    extra.set("kind", "draft")
    marked = with_part(source, DOC, root)
    assert refusal(marked, part=DOC, xpath="//ex:mark", count=1, action="remove").code == "bad-params"
    plan, out, rec = edit(marked, part=DOC, xpath="//ex:mark[@kind='draft']", count=1, action="remove",
                          namespaces={"ex": "urn:example:marks"})
    assert plan["property_assertions"][0]["namespaces"] == {"ex": "urn:example:marks"}
    assert {"V1", "V2", "V10"}.isdisjoint(failed(rec)), failed(rec)
    for bad in ({"xml": "urn:x"}, {"1x": "urn:x"}, {"ex": ""}, ["ex"]):
        assert refusal(marked, part=DOC, xpath="//w:p", count=1, action="remove", namespaces=bad).code == "bad-params"


# ---- refusals ------------------------------------------------------------------------------------------------------

def test_another_number_of_elements_than_stated_refuses_the_file_and_says_what_was_found(source):
    stop = refusal(source, part=DOC, xpath="//w:p", count=3, action="remove")
    assert stop.code == "xml-count" and stop.details["expected"] == 3 and stop.details["found"] == 46
    assert len(stop.details["matches"]) == ex.SHOWN_MATCHES and stop.details["matches"][0].startswith("<w:p>")
    assert "“SECTION 21 00 00”" in stop.details["matches"][0]
    assert refusal(source, part=DOC, xpath="//w:nothing", count=1, action="remove").details["found"] == 0
    said = refusal_message({"code": "xml-count", "reason": stop.reason, "details": stop.details})
    assert "states 3 elements" in said and "selects 46 in this document" in said and "nothing was changed" in said
    for code in ("xml-count", "xml-part", "xml-action", "xpath-timeout", "obliterate-only"):
        assert code in FAILURE_EXPLANATIONS


def test_a_part_that_is_not_there_or_is_the_packages_own_bookkeeping_is_refused(source):
    missing = refusal(source, part="word/header9.xml", xpath="//w:p", count=1, action="remove")
    assert missing.code == "xml-part" and missing.details["why"] == "missing" and DOC in missing.details["parts"]
    for name in ("word/_rels/document.xml.rels", "[Content_Types].xml", "_rels/.rels"):
        stop = refusal(source, part=name, xpath="/*", count=1, action="set_attribute", name="x", value="y")
        assert stop.code == "xml-part" and stop.details["why"] == "bookkeeping"
    picture = refusal(source, part="docProps/thumbnail.jpeg", xpath="/*", count=1, action="remove")
    assert picture.code == "xml-part" and picture.details["why"] == "not-xml"
    assert "has no part named word/header9.xml" in refusal_message(
        {"code": "xml-part", "reason": missing.reason, "details": missing.details})


@pytest.mark.parametrize("params,why", [
    ({"xpath": "/w:document", "count": 1, "action": "remove"}, "root element"),
    ({"xpath": "/w:document", "count": 1, "action": "unwrap"}, "root element"),
    ({"xpath": "/w:document", "count": 1, "action": "insert", "position": "after", "xml": "<w:p/>"}, "root element"),
    ({"xpath": "//w:tbl | //w:tbl//w:p", "count": 7, "action": "remove"}, "inside another selected element"),
    ({"xpath": "//w:pgMar", "count": 2, "action": "remove_attribute", "name": "w:nothing"}, "no such attribute"),
    ({"xpath": "//w:body/w:p[1]", "count": 1, "action": "set_text", "text": "x"}, "holds other elements"),
    ({"xpath": "//w:body/w:p[1]", "count": 1, "action": "unwrap", "keep": "w:pPr"}, "exactly one child"),
    ({"xpath": "//w:body/w:p[1]/w:r/w:t", "count": 1, "action": "unwrap"}, "text of its own"),
    ({"xpath": "//w:pgMar", "count": 2, "action": "set_attribute", "name": "w:gutter", "value": "0"},
     "would change nothing"),
    ({"xpath": "//w:body/w:p[1]/w:r/w:t", "count": 1, "action": "set_text", "text": "SECTION 21 00 00"},
     "would change nothing"),
], ids=["remove-root", "unwrap-root", "sibling-of-root", "nested", "missing-attribute", "text-on-a-parent",
        "no-child-to-keep", "unwrap-over-text", "attribute-already-so", "text-already-so"])
def test_an_action_a_selected_element_cannot_take_refuses_the_file(source, params, why):
    stop = refusal(source, part=DOC, **params)
    assert stop.code == "xml-action" and why in stop.reason and stop.details["action"] == params["action"]
    assert stop.details["element"].startswith("w:")
    assert "cannot" in refusal_message({"code": stop.code, "reason": stop.reason, "details": stop.details})


@pytest.mark.parametrize("params", [
    {"xpath": "//w:p[//w:t]", "count": 1, "action": "remove"},
    {"xpath": "//w:p[count(/w:document//w:p) > 1]", "count": 1, "action": "remove"},
    {"xpath": "//w:p[following::w:tbl]", "count": 1, "action": "remove"},
    {"xpath": "//w:p[preceding::w:tbl]", "count": 1, "action": "remove"},
    {"xpath": "//w:p[", "count": 1, "action": "remove"},
    {"xpath": "//w:p]", "count": 1, "action": "remove"},
    {"xpath": "//w:p[@w:x='unclosed]", "count": 1, "action": "remove"},
    {"xpath": "//w:pgMar/@w:top", "count": 2, "action": "remove"},
    {"xpath": "count(//w:p)", "count": 1, "action": "remove"},
    {"xpath": "string(//w:t)", "count": 1, "action": "remove"},
    {"xpath": "//w:t/text()", "count": 1, "action": "remove"},
    {"xpath": "//w:p/following::w:tbl", "count": 1, "action": "remove"},
    {"xpath": "//w:p[w:r and //w:tbl]", "count": 1, "action": "remove"},
    {"xpath": "//w:p[1 * //w:t]", "count": 1, "action": "remove"},
    {"xpath": "//w:p{1}", "count": 1, "action": "remove"},
    {"xpath": "//unknown:p", "count": 1, "action": "remove"},
    {"xpath": "//w:p" + "[1]" * 400, "count": 1, "action": "remove"},
    {"xpath": "", "count": 1, "action": "remove"},
    {"xpath": "//w:p", "count": 0, "action": "remove"},
    {"xpath": "//w:p", "count": True, "action": "remove"},
    {"xpath": "//w:p", "count": "3", "action": "remove"},
    {"xpath": "//w:p", "count": 1, "action": "delete"},
    {"xpath": "//w:p", "count": 1, "action": "remove", "name": "w:val"},
    {"xpath": "//w:p", "count": 1, "action": "set_attribute", "name": "w:val"},
    {"xpath": "//w:p", "count": 1, "action": "set_attribute", "name": "bad name", "value": "x"},
    {"xpath": "//w:p", "count": 1, "action": "set_attribute", "name": "zz:val", "value": "x"},
    {"xpath": "//w:p", "count": 1, "action": "insert", "xml": "<w:p/>", "position": "inside"},
    {"xpath": "//w:p", "count": 1, "action": "insert", "xml": "<w:p>", "position": "after"},
    {"xpath": "//w:p", "count": 1, "action": "replace", "xml": "words only"},
    {"xpath": "//w:p", "count": 1, "action": "replace", "xml": "<w:p/> and words"},
    {"xpath": "//w:p", "count": 1, "action": "replace", "xml": "<!-- note --><w:p/>"},
    {"xpath": "//w:p", "count": 1, "action": "replace", "xml": "<!DOCTYPE x [<!ENTITY e 'boom'>]><w:p>&e;</w:p>"},
    {"xpath": "//w:p", "count": 1, "action": "replace", "xml": "<q:p/>"},
    {"xpath": "//w:p", "count": 1, "action": "remove", "tracked": True},
    {"xpath": "//w:t", "count": 1, "action": "set_text", "text": "a\x00b"},
    {"xpath": "//w:t", "count": 1, "action": "set_text", "text": "a\x0bb"},
    {"xpath": "//w:pgMar", "count": 2, "action": "set_attribute", "name": "w:header", "value": "5\x0176"},
])
def test_a_request_that_is_not_one_stated_edit_is_refused_before_the_file_is_read(source, params):
    assert refusal(source, part=DOC, **params).code == "bad-params"


def test_a_selector_is_not_part_of_this_operation_and_the_spec_validator_knows_its_parameters(source):
    with pytest.raises(JobException, match="takes no select"):
        get_op("edit_xml").plan(DocxPackage(source), {"scope": ["document"]}, dict(CLEAR_BOX))
    good = {"request": "r", "summary": "s", "mode": "untracked", "ops": [{"op": "edit_xml", "params": dict(CLEAR_BOX)}]}
    validate_spec(good)
    for broken in ({"count": "one"}, {"action": "erase"}, {"depth": 3}):
        with pytest.raises(SpecError):
            validate_spec({**good, "ops": [{"op": "edit_xml", "params": {**CLEAR_BOX, **broken}}]})
    with pytest.raises(SpecError, match="params.xpath is required"):
        validate_spec({**good, "ops": [{"op": "edit_xml", "params": {"part": DOC, "count": 1, "action": "remove"}}]})
    with pytest.raises(SpecError, match="select takes no keys"):
        validate_spec({**good, "ops": [{"op": "edit_xml", "select": {"scope": ["document"]},
                                        "params": dict(CLEAR_BOX)}]})


def test_inside_a_predicate_a_path_may_start_at_the_element_never_at_the_root():
    for fine in ("//w:p[.//w:t]", "//w:p[w:r/w:t='a/b']", "//w:p[../w:tbl]", "//w:p[w:r[1]/w:t]",
                 "//w:sdt[w:sdtPr/w:dataBinding]", "//w:p[following-sibling::w:tbl]", "//w:p[*/w:t]",
                 "/w:document/w:body//w:p", "//w:t[contains(., '//')]", "//w:p[@w:x=\"[\"]",
                 # a name where a name can stand is a name, whatever it spells; a slash after a value goes on
                 "//w:p[and/w:t]", "//w:p[w:r and or/w:t]", "//w:p[w:and/w:t]", "//w:p[count(w:r)*2 = 4]",
                 "//w:p[(w:r)/w:t]", "(//w:tc)[1]//w:t", "//w:p[w:r [1] / w:t]", "//w:p[w:a-b/w:t]",
                 "//w:p[preceding-sibling::w:p[1]/w:r]", "//w:p[w:r|w:hyperlink/w:r]", "//w:p[.5 < 1]",
                 "//w:p[ancestor::w:tbl]", "//mc:AlternateContent[mc:Choice//wps:txbx]"):
        assert ex.check_xpath(fine) == fine and vx._v_judge(fine) == fine
    for bad in ("//w:p[//w:t]", "//w:p[ /w:document]", "//w:p[count(//w:t) > 1]", "//w:p[1 = //w:t]",
                "//w:p[w:r and //w:tbl]", "//w:p[w:r and/w:tbl]", "//w:p[w:r or //w:tbl]", "//w:p[1 * //w:t]",
                "//w:p[2 div /w:document]", "//w:p[w:r | //w:tbl]", "//w:p[-//w:t]", "//w:p[w:r[/w:document]]",
                "//w:p[not(w:r) and (//w:t)]", "//w:p[contains(., 'x') or //w:tbl]",
                "//w:p[following::w:p]", "//w:p[w:r[preceding :: w:r]]", "//w:p/following::w:tbl",
                "//w:tbl/preceding::w:p", "//w:p[", "//w:p]", "//w:p['x]", "//w:p{1}"):
        with pytest.raises(JobException):
            ex.check_xpath(bad)
        with pytest.raises(ValueError):                         # the verifier's own judgement agrees
            vx._v_judge(bad)


def test_an_expression_that_runs_past_its_deadline_refuses_the_file(source, monkeypatch):
    class Slow:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, root):
            time.sleep(0.6)
            return []
    monkeypatch.setattr(ex.etree, "XPath", Slow)
    monkeypatch.setattr(ex, "XPATH_TIMEOUT_S", 0.05)
    stop = refusal(source, part=DOC, xpath="//w:p", count=1, action="remove")
    assert stop.code == "xpath-timeout" and stop.details["timeout_s"] == 0.05
    assert "took too long" in refusal_message({"code": stop.code, "reason": stop.reason, "details": stop.details})
    assert ex.XPATH_TIMEOUT_S == 0.05 and vx.V_XPATH_TIMEOUT_S == 2.0 and vx.V_MAX_XPATH_CHARS == ex.MAX_XPATH_CHARS


def test_a_very_large_selection_plans_applies_and_verifies_in_one_pass_each(source):
    root = part_of(source)
    body = root.find(W + "body")
    for _ in range(4000):
        body.insert(0, copy.deepcopy(body[0]))
    big = with_part(source, DOC, root)
    started = time.perf_counter()
    plan, out, rec = edit(big, part=DOC, xpath="/w:document/w:body/w:p[w:r/w:t='SECTION 21 00 00']", count=4001,
                          action="remove")
    assert {"V1", "V2", "V10"}.isdisjoint(failed(rec)), failed(rec)
    assert time.perf_counter() - started < 60


# ---- planted defects: what the verifier's own twin catches ----------------------------------------------------------

def judged(src: bytes, out: bytes, plan: Dict[str, Any]):
    return run_suite(src, out, plan, "source.docx", "copy.docx")


def test_a_copy_that_changes_anything_beside_the_stated_edit_fails(source):
    plan, out, rec = edit(source, part=DOC, xpath="//w:tbl", count=1, action="remove")
    assert rec.passed
    # one more change in the same part: a run made bold
    root = part_of(out)
    run = next(root.iter(W + "r"))
    etree.SubElement(run, W + "rPr").append(etree.Element(W + "b"))
    run.insert(0, run[-1])
    more = judged(source, with_part(out, DOC, root), plan)
    assert "V10" in failed(more) and "not the source with exactly the stated edit" in failed(more)["V10"]
    # one more word in the same part: the text checks leave the part to V10, and V10 says no
    root = part_of(out)
    next(root.iter(W + "t")).text = "SECTION 99 99 99"
    worded = judged(source, with_part(out, DOC, root), plan)
    assert set(failed(worded)) == {"V10"} and worded.get("V2").status == "PASS"
    # the edit not made at all
    assert "V10" in failed(judged(source, source, plan))
    # another part changed too: the package check, and that part's own text check
    footer = part_of(out, "word/footer1.xml")
    next(footer.iter(W + "t")).text = "Changed"
    other = judged(source, with_part(out, "word/footer1.xml", footer), plan)
    assert {"V1", "V2"} <= set(failed(other))


def test_an_edit_made_to_other_elements_than_the_expression_selects_fails(source):
    """A wrong implementation: the transform changes the second table cell where the plan chose the first."""
    _frag, plan = planned(source, part=DOC, xpath="(//w:tc)[1]//w:t", count=1, action="set_text", text="Revision")
    root = DocxPackage(source).xml(DOC)
    wrong = list(list(root.iter(W + "tc"))[1].iter(W + "t"))[0]
    wrong.text = "Revision"
    out = with_part(source, DOC, root)
    rec = judged(source, out, plan)
    assert "V10" in failed(rec) and "not the source with exactly the stated edit" in failed(rec)["V10"]


def test_a_wrong_implementation_the_plan_and_the_transform_share_fails(source, monkeypatch):
    """The transform's own ``apply_action`` takes the next sibling out as well. The plan cannot tell — it only states
    the edit — but the verifier makes the edit with its own code and the copy is not that."""
    real = ex.apply_action

    def greedy(targets, action, namespaces):
        for el in targets:
            after = el.getnext()
            if after is not None:
                after.getparent().remove(after)
        real(targets, action, namespaces)
    monkeypatch.setattr(ex, "apply_action", greedy)
    plan, out, rec = edit(source, part=DOC, xpath="//w:tbl", count=1, action="remove")
    assert "V10" in failed(rec) and "not the source with exactly the stated edit" in failed(rec)["V10"]


def test_a_plan_that_chose_other_elements_or_another_number_than_the_expression_selects_fails(source, monkeypatch):
    # a selection the transform side gets wrong: the op picks the first two paragraphs for an expression that selects
    # the first three
    real = ex.select_elements
    monkeypatch.setattr(ex, "select_elements", lambda root, expr, ns, part: real(root, expr, ns, part)[:2])
    plan, out, rec = edit(source, part=DOC, xpath="//w:body/w:p[position() <= 3]", count=2, action="remove")
    assert "V10" in failed(rec) and "the plan states 2: the op had to refuse" in failed(rec)["V10"]
    monkeypatch.setattr(ex, "select_elements", lambda root, expr, ns, part: real(root, expr, ns, part)[1:])
    plan, out, rec = edit(source, part=DOC, xpath="//w:body/w:p[position() <= 3]", count=2, action="remove")
    assert "V10" in failed(rec)
    monkeypatch.setattr(ex, "select_elements", real)
    # the right number at other places: the places' digest is the plan's word for which elements
    plan, out, rec = edit(source, part=DOC, xpath="//w:body/w:p[position() <= 2]", count=2, action="remove")
    assert rec.passed
    lying = copy.deepcopy(plan)
    lying["property_assertions"][0]["places_sha256"] = ex.places_digest([[0, 3], [0, 4]])
    assert "are not the ones the plan chose" in failed(judged(source, out, lying))["V10"]


def test_an_edit_the_operation_had_to_refuse_fails_even_when_the_copy_matches_it(source):
    """A plan that states an edit the op must refuse is a defect of the op: the verifier says so, whatever the copy."""
    plan, out, rec = edit(source, part=DOC, xpath="//w:tbl", count=1, action="remove")
    for change, why in (
            ({"xpath": "//w:tbl | //w:tbl//w:p", "count": 7,
              "places_sha256": None}, "the plan chose"),
            ({"part": "word/_rels/document.xml.rels"}, "bookkeeping"),
            ({"part": "word/nothing.xml"}, "no part"),
            ({"xpath": "//w:p[//w:t]"}, "starts a path at the root"),
            ({"count": 0}, "states no count"),
            ({"action": "erase"}, "unknown action"),
            ({"action": "set_text"}, "gives no text"),
            ({"action": "insert", "xml": "<w:p/>", "position": "beside"}, "unknown position"),
            ({"namespaces": {"xml": "urn:x"}}, "cannot be one")):
        bad = copy.deepcopy(plan)
        bad["property_assertions"][0].update(change)
        if change.get("part"):
            bad["intended_parts"] = [change["part"]]
        said = failed(judged(source, out, bad)).get("V10", "")
        assert why in said, (change, said)
    nested = copy.deepcopy(plan)
    nested["property_assertions"][0].update({"xpath": "//w:tbl | //w:tbl//w:tr", "count": 3})
    tree = part_of(source)
    nested["property_assertions"][0]["places_sha256"] = vx._v_digest(vx._v_places(
        tree, vx._v_evaluate(tree, "//w:tbl | //w:tbl//w:tr", dict(vx._V_PREFIXES))))
    assert "an element inside another selected element" in failed(judged(source, out, nested))["V10"]


def test_the_text_checks_leave_a_part_only_to_a_whole_part_comparison(source):
    """The same copy under a plan that says nothing of a raw edit: its unpredicted words fail V2 as always. The
    exemption is the assertion's, and the assertion is checked."""
    plan, out, rec = edit(source, part=DOC, xpath="//w:tbl", count=1, action="remove")
    bare = {**plan, "property_assertions": []}
    plain = judged(source, out, bare)
    assert {"V2", "V10"} <= set(failed(plain)) and "raw_parts" not in plain.get("V2").data
    # a raw edit of one part exempts that part alone
    head = [n for n in zip_names(source) if n.startswith("word/header")][0]
    plan2, out2, rec2 = edit(source, part=head, xpath="(//w:t)[1]", count=1, action="set_text", text="Changed")
    assert {"V1", "V2", "V10"}.isdisjoint(failed(rec2)) and rec2.get("V2").data["raw_parts"] == [head]
    root = part_of(out2)
    next(root.iter(W + "t")).text = "SECTION 99"
    assert {"V1", "V2"} <= set(failed(judged(source, with_part(out2, DOC, root), plan2)))


def zip_names(data: bytes):
    return zipfile.ZipFile(io.BytesIO(data)).namelist()


def test_what_word_repairs_or_refuses_is_still_a_failed_check_of_the_copy(source):
    """Report, never refuse: an edit that breaks the schema, repeats a revision id or leaves a reference pointing at
    nothing is made as stated, proven as stated (V10 passes) and fails the check of the file it broke."""
    plan, out, rec = edit(source, part=DOC, xpath="//w:pgMar", count=2, action="remove_attribute", name="w:gutter")
    assert set(failed(rec)) == {"V5a"} and rec.get("V10").status == "PASS"
    existing = next(part_of(source).iter(W + "ins")).get(W + "id")
    twin = (f'<w:ins w:id="{existing}" w:author="A" w:date="2026-11-10T00:00:00Z"><w:r><w:t>again</w:t></w:r></w:ins>')
    plan, out, rec = edit(source, part=DOC, xpath="//w:body/w:p[1]", count=1, action="insert", position="last_child",
                          xml=twin)
    assert "V4" in failed(rec) and rec.get("V10").status == "PASS" and "DUPLICATE ids" in failed(rec)["V4"]
    plan, out, rec = edit(source, part=DOC, xpath="//w:headerReference", count=1, action="set_attribute",
                          name="r:id", value="rId999")
    assert set(failed(rec)) == {"V1"} and "rId999" in failed(rec)["V1"]
    # a tracked insertion Word accepts: placed as written, counted in the report, nothing fails
    fine = '<w:ins w:id="9001" w:author="A" w:date="2026-11-10T00:00:00Z"><w:r><w:t>added</w:t></w:r></w:ins>'
    plan, out, rec = edit(source, part=DOC, xpath="//w:body/w:p[1]", count=1, action="insert", position="last_child",
                          xml=fine)
    assert rec.passed, failed(rec)
    assert any(line.startswith("Tracked changes: 2 before, 3 after") for line in difference_lines(rec))
    assert "not counted here" in rec.get("V4").summary and plan["rev_delta"] == {"ins": 0, "del": 0}


# ---- any XML, compared exactly; and the text the paragraph reader does not read --------------------------------------

MIXED = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
         b'<svg xmlns="http://www.w3.org/2000/svg"><text x="1">Hello<tspan>world</tspan>!</text>\n  <g/>\n</svg>')
SVG = "http://www.w3.org/2000/svg"


def with_member(data: bytes, name: str, content: bytes) -> bytes:
    """``data`` with the member ``name`` holding ``content`` (the fixture's stored-data part stands in for any XML
    the document carries: a picture's SVG, a chart, stored data)."""
    work = DocxPackage(data).copy()
    work.set_bytes(name, content)
    return work.output_bytes()


def test_a_part_of_any_xml_is_compared_exactly_text_between_elements_included(source):
    """The whole-part comparison has no rule about which text matters: in a part of mixed content the text after an
    element is content, and a copy that changes it beside the stated edit fails — white space too."""
    mixed = with_member(source, "customXml/item1.xml", MIXED)
    plan, out, rec = edit(mixed, part="customXml/item1.xml", xpath="//s:tspan", count=1, action="set_attribute",
                          name="fill", value="red", namespaces={"s": SVG})
    assert {"V1", "V2", "V10"}.isdisjoint(failed(rec)), failed(rec)
    assert zipfile.ZipFile(io.BytesIO(out)).read("customXml/item1.xml").count(b"world</tspan>!</text>") == 1
    for wrong, said in ((b"world</tspan>?</text>", "the text after it '!'→'?'"),            # a tail
                        (b"world</tspan>!</text>\n    <g/>", "the text after it"),            # white space alone
                        (b"Hullo<tspan", "text 'Hello'→'Hullo'"),
                        (b"</svg><!-- left behind -->", "outside its root element")):
        good = zipfile.ZipFile(io.BytesIO(out)).read("customXml/item1.xml")
        old = {b"world</tspan>?</text>": b"world</tspan>!</text>", b"world</tspan>!</text>\n    <g/>":
               b"world</tspan>!</text>\n  <g/>", b"Hullo<tspan": b"Hello<tspan", b"</svg><!-- left behind -->": b"</svg>"}[wrong]
        assert good.count(old) == 1
        bad = judged(mixed, with_member(out, "customXml/item1.xml", good.replace(old, wrong)), plan)
        assert said in failed(bad).get("V10", ""), (wrong, failed(bad))
    # the same holds in a Word part: a stray word between two runs is not the stated edit
    plan, out, rec = edit(source, part=DOC, xpath="//w:tbl", count=1, action="remove")
    root = part_of(out)
    next(root.iter(W + "r")).tail = "stray"
    assert "the text after it ''→'stray'" in failed(judged(source, with_part(out, DOC, root), plan))["V10"]
    # removing an element keeps the text that followed it, on both sides
    plan, out, rec = edit(mixed, part="customXml/item1.xml", xpath="//s:tspan", count=1, action="remove",
                          namespaces={"s": SVG})
    assert rec.get("V10").status == "PASS" and b"<text x=\"1\">Hello!</text>" in zipfile.ZipFile(
        io.BytesIO(out)).read("customXml/item1.xml")


def test_a_part_with_a_comment_or_instruction_outside_its_root_is_refused_not_rewritten_without_it(source):
    """The app writes a part from its root element, which keeps nothing beside it: such a part is refused, and a plan
    that edits one anyway is the operation's defect, which the verifier names."""
    for around in (b"<!-- Generator: a drawing program -->", b"<?mso-contentType?>"):
        marked = with_member(source, "customXml/item1.xml", MIXED.replace(b"<svg ", around + b"<svg ", 1))
        stop = refusal(marked, part="customXml/item1.xml", xpath="//s:tspan", count=1, action="remove",
                       namespaces={"s": SVG})
        assert stop.code == "xml-part" and stop.details["why"] == "outside-root"
        assert "outside the root element of customXml/item1.xml" in refusal_message(
            {"code": stop.code, "reason": stop.reason, "details": stop.details})
    after = with_member(source, "customXml/item1.xml", MIXED + b"<!-- after -->")
    assert refusal(after, part="customXml/item1.xml", xpath="//s:g", count=1, action="remove",
                   namespaces={"s": SVG}).details["why"] == "outside-root"
    # the operation that does not refuse loses the comment; the verifier says it had to refuse
    marked = with_member(source, "customXml/item1.xml", MIXED.replace(b"<svg ", b"<!-- Generator --><svg ", 1))
    real = ex.outside_root
    ex.outside_root = lambda root: False
    try:
        plan, out, rec = edit(marked, part="customXml/item1.xml", xpath="//s:g", count=1, action="remove",
                              namespaces={"s": SVG})
    finally:
        ex.outside_root = real
    assert b"Generator" not in zipfile.ZipFile(io.BytesIO(out)).read("customXml/item1.xml")
    assert "outside its root element, which the writer does not keep): the op had to refuse" in failed(rec)["V10"]


def test_text_that_is_not_a_paragraphs_words_is_listed_never_said_to_be_unchanged(source):
    """V2 reads the words of paragraphs. A raw edit may change other text — a chart's title, a drawing's text, a
    document property, stored data — in a text part or any other: the record lists it, and the window's "no words
    change" is never said of text nobody read."""
    # a part that is no text part: a document property
    plan, out, rec = edit(source, part="docProps/core.xml", xpath="//dc:creator", count=1, action="set_text",
                          text="Design team")
    assert {"V1", "V2", "V10"}.isdisjoint(failed(rec)), failed(rec)
    assert rec.get("V2").data["raw_text"] == [{"part": "docProps/core.xml", "removed": ["python-docx"],
                                               "added": ["Design team"], "more": 0}]
    words, _other = raw_listing(rec)
    assert words == [{"part": "docProps/core.xml", "where": "docProps/core.xml, other text (not a paragraph's words)",
                      "paragraph": None, "removed": ["python-docx"], "added": ["Design team"], "other": True}]
    from spec_surgeon.preview import RAW_NO_WORDS_CHANGE, RAW_WORDS_CHANGE_HEAD, SampleView, raw_words_lines
    sample = SampleView(source=Path("a.docx"))
    sample.raw_xml, sample.text_changes = True, words
    assert raw_words_lines([sample]) == [
        RAW_WORDS_CHANGE_HEAD, "• a.docx, docProps/core.xml, other text (not a paragraph's words): text removed "
                               "“python-docx”; text added “Design team”"]
    # a text part: DrawingML text inside a paragraph is not one of the reader's text elements
    root = part_of(source)
    holder = etree.SubElement(next(root.iter(W + "r")), "{%s}t" % ex.NAMESPACES["a"])
    holder.text = "Flow test results"
    drawn = with_part(source, DOC, root)
    plan, out, rec = edit(drawn, part=DOC, xpath="//a:t", count=1, action="remove")
    assert rec.passed, failed(rec)
    assert rec.get("V2").data["actual"] == []                                   # no paragraph's words changed
    words, _other = raw_listing(rec)
    assert [(w["where"], w["removed"], w["added"]) for w in words] == [
        ("body, other text (not a paragraph's words)", ["Flow test results"], [])]
    # a raw edit that changes no text at all: only then is "no words" said, of every text node that was compared
    plan, out, rec = edit(source, part=DOC, xpath="//w:pgMar", count=2, action="set_attribute", name="w:header",
                          value="576")
    assert raw_listing(rec)[0] == [] and rec.get("V2").data["raw_text"] == []
    sample.text_changes = []
    assert raw_words_lines([sample]) == [RAW_NO_WORDS_CHANGE]
    assert "any other text between tags" in RAW_NO_WORDS_CHANGE and "attributes" in RAW_NO_WORDS_CHANGE
    # a paragraph's own words stay V2's entries, placed by paragraph; the same text is not listed twice
    plan, out, rec = edit(source, part=DOC, xpath="//w:body/w:p[1]/w:r/w:t", count=1, action="set_text",
                          text="SECTION 21 13 13")
    assert rec.get("V2").data["raw_text"] == [] and [w["where"] for w in raw_listing(rec)[0]] == ["body paragraph 1"]
    # many stretches: the lists are bounded and say how many they leave out
    root = part_of(source, "docProps/core.xml")
    for n in range(vx.V_SHOWN_TEXTS + 5):
        etree.SubElement(root, "note").text = f"note {n}"
    noted = with_part(source, "docProps/core.xml", root)
    plan, out, rec = edit(noted, part="docProps/core.xml", xpath="//note", count=vx.V_SHOWN_TEXTS + 5, action="remove")
    [entry] = raw_listing(rec)[0]
    assert len(entry["removed"]) == vx.V_SHOWN_TEXTS and entry["more"] == 5
    from spec_surgeon.preview import text_change_phrase
    assert text_change_phrase(entry).endswith("(and 5 more stretches not listed)")


# ---- a step of its own: clear the obstacle, then the same plan -------------------------------------------------------

def run_chain(src: bytes, chain: Dict[str, Any]):
    """Each stage planned on the copy the one before it made, applied and verified: ``[(name, record)]``."""
    out, current = [], src
    for (plan, frags), stage in zip(plan_chain(DocxPackage(src), chain), chain["stages"]):
        made = apply_spec(DocxPackage(current), frags).output_bytes()
        out.append((stage["name"], run_suite(current, made, plan.to_dict(), "before.docx", "after.docx")))
        current = made
    return out, current


def test_clear_the_obstacle_then_the_same_plan_is_two_verified_steps(source):
    boxed = with_text_box(source)
    with pytest.raises(JobException) as stopped:
        plan_spec(DocxPackage(boxed), DELETE_HEADING)
    assert stopped.value.code == "textbox"
    clear = {"request": "clear the text box", "summary": "Remove the text box that stops the deletion.",
             "mode": "untracked", "ops": [{"op": "edit_xml", "params": dict(CLEAR_BOX)}]}
    chain = {"name": "finish it", "stages": [{"name": "clear", "spec": clear}, {"name": "delete", "spec": DELETE_HEADING}]}
    validate_chain(chain)
    (first, second), final = run_chain(boxed, chain)
    assert first[1].passed and second[1].passed, (failed(first[1]), failed(second[1]))
    words, other = raw_listing(first[1])
    assert [w["removed"] for w in words] == [["SUPERSEDED STAMP"]] and "Text boxes: 1 before, 0 after." in other
    # the second step is the plan as the other documents got it, verified as always: predicted diffs, no listing
    assert raw_listing(second[1]) is None and second[1].get("V2").data.get("raw_parts") is None
    assert second[1].mode == "content" and second[1].get("V10").status == "PASS"
    assert "SUPERSEDED STAMP" not in "".join(part_of(final).itertext())
    # one flat list of ops cannot do it: every op of a spec is planned on the document as it is
    flat = {**DELETE_HEADING, "ops": [{"op": "edit_xml", "params": dict(CLEAR_BOX)}, *DELETE_HEADING["ops"]]}
    with pytest.raises(JobException) as still:
        plan_spec(DocxPackage(boxed), flat)
    assert still.value.code == "textbox"


def test_a_raw_edit_never_shares_a_step_with_another_change_of_its_part(source):
    """It changes its part as a whole, so the app puts it in a step of its own, in the user's order; an operation on
    another part may share its step, and a tracked one never does."""
    font = {"op": "set_font", "select": {"scope": ["document"], "runs": "all"}, "params": {"font": "Arial"}}
    raw = {"op": "edit_xml", "params": {"part": DOC, "xpath": "//w:tbl", "count": 1, "action": "remove"}}
    spec = {"request": "r", "summary": "s", "mode": "untracked", "ops": [raw, font]}
    with pytest.raises(JobException) as clash:
        plan_spec(DocxPackage(source), spec)
    assert clash.value.code == "conflict"
    chain = split_into_stages(DocxPackage(source), spec)
    assert [[o["op"] for o in st["spec"]["ops"]] for st in chain["stages"]] == [["edit_xml"], ["set_font"]]
    assert [st["spec"]["mode"] for st in chain["stages"]] == ["untracked", "formatting"]
    records, _final = run_chain(source, chain)
    assert all(rec.passed for _name, rec in records), [failed(rec) for _n, rec in records]
    # the other order is the user's too: the font first, then the edit on what it made
    swapped = split_into_stages(DocxPackage(source), {**spec, "ops": [font, raw]})
    assert [[o["op"] for o in st["spec"]["ops"]] for st in swapped["stages"]] == [["set_font"], ["edit_xml"]]
    # another part's op shares the step; the plan's mode is the raw edit's
    header = [n for n in zip_names(source) if n.startswith("word/header")][0]
    aside = {"op": "edit_xml", "params": {"part": header, "xpath": "(//w:t)[1]", "count": 1, "action": "set_text",
                                          "text": "Changed"}}
    together = {"request": "r", "summary": "s", "mode": "untracked",
                "ops": [aside, {"op": "set_style_font", "select": {"styles": "all"}, "params": {"font": "Arial"}}]}
    plan, frags = plan_spec(DocxPackage(source), together)
    assert plan.mode == "untracked" and len(frags) == 2
    made = apply_spec(DocxPackage(source), frags).output_bytes()
    assert {"V1", "V2", "V10"}.isdisjoint(failed(run_suite(source, made, plan.to_dict(), "a", "b")))
    # a tracked op beside it is another step: tracked and untracked changes never share one
    tracked = {"op": "replace_text", "select": {"scope": ["document"]},
               "params": {"find": "Schedule 40", "replace": "Schedule 10", "author": "A"}}
    mixed = split_into_stages(DocxPackage(source), {"request": "r", "summary": "s", "mode": "content",
                                                    "ops": [aside, tracked]})
    assert [st["spec"]["mode"] for st in mixed["stages"]] == ["untracked", "content"]


# ---- Obliterate mode alone ------------------------------------------------------------------------------------------

@pytest.fixture
def batch(tmp_path, source) -> _Batch:
    folder = tmp_path / "specs"
    folder.mkdir()
    for i in range(2):
        (folder / f"21 0{i} 00 – SPEC {i}.docx").write_bytes(build(f"21 0{i} 00", f"SPEC {i}"))
    return _Batch(sorted(folder.glob("*.docx")), None)


RAW_SPEC = {"request": "take the revision table out", "summary": "Remove the table.", "mode": "untracked",
            "ops": [{"op": "edit_xml", "params": {"part": DOC, "xpath": "//w:tbl", "count": 1, "action": "remove"}}]}


def test_the_gate_refuses_it_in_every_mode_but_obliterate_whatever_shape_the_plan_has(batch):
    sources = ["take the revision table out"]
    as_stages = {"summary": "s", "stages": [{"name": "one", "spec": RAW_SPEC}]}
    as_layouts = {"summary": "s", "layouts": [{"id": "a", "label": "A", "spec": RAW_SPEC,
                                               "pattern_ids": [p["pattern_id"] for p in batch.prof["patterns"]]}]}
    for shape in (RAW_SPEC, as_stages, as_layouts):
        outcome, errors = compose_submission(copy.deepcopy(shape), batch, sources)
        assert len(errors) == 1 and errors[0].startswith("[obliterate-only]") and "edit_xml" in errors[0], errors
        assert "Obliterate mode" in errors[0]
        outcome, errors = compose_submission(copy.deepcopy(shape), batch, sources, unguarded=True)
        assert errors == [], errors
    # no other submission is touched by the check
    plain = {"request": "r", "summary": "s", "mode": "formatting",
             "ops": [{"op": "set_font", "select": {"scope": ["document"], "runs": "all"}, "params": {"font": "Arial"}}]}
    assert compiler._mode_errors(plain, False) == [] and compiler._mode_errors(RAW_SPEC, True) == []
    assert compiler._mode_errors({"questions": ["?"]}, False) == [] and compiler._mode_errors("x", False) == []
    # the engine below the gate knows the op in every mode (a spec is a spec): the gate is what a mode is
    assert [name for name, cls in OPS.items() if cls.obliterate_only] == ["edit_xml"]


def test_text_a_raw_edit_places_passes_the_gate_only_when_the_user_has_read_it(batch):
    worded = {"request": "r", "summary": "s", "mode": "untracked", "ops": [
        {"op": "edit_xml", "params": {"part": DOC, "xpath": "//w:body/w:p[1]/w:r/w:t", "count": 1,
                                      "action": "set_text", "text": "SECTION 21 13 13"}},
    ]}
    placed = {"request": "r", "summary": "s", "mode": "untracked", "ops": [
        {"op": "edit_xml", "params": {"part": "word/footer1.xml", "xpath": "(//w:p)[1]", "count": 1,
                                      "action": "insert", "position": "after",
                                      "xml": "<w:p><w:r><w:t>Coordination draft</w:t></w:r><w:r><w:tab/></w:r></w:p>"}},
    ]}
    assert compiler.placed_words(worded) == ["SECTION 21 13 13"]
    assert compiler.placed_words(placed) == ["Coordination draft"]
    # nothing inside a fragment hides the words beside it: a comment, a child element, the text after either
    hidden = copy.deepcopy(placed)
    hidden["ops"][0]["params"]["xml"] = "<w:p><w:r><w:t>Unshown wording<!--note--></w:t></w:r></w:p>"
    assert compiler.placed_words(hidden) == ["Unshown wording"]
    _outcome, errors = compose_submission(copy.deepcopy(hidden), batch, ["change it"], unguarded=True)
    assert len(errors) == 1 and "Unshown wording" in errors[0] and "the user has not read them" in errors[0]
    assert ex.fragment_text("<x>before<b>inside</b>between<!--c-->after<?p i?>last</x>") == [
        "before", "inside", "between", "after", "last"]
    assert ex.fragment_text("<w:p>\n  <w:r>\n    <w:t>only this</w:t>\n  </w:r>\n</w:p>") == ["only this"]
    assert compiler.placed_words(RAW_SPEC) == []                # an edit that places no text has no words to trace
    attribute = {"request": "r", "summary": "s", "mode": "untracked", "ops": [
        {"op": "edit_xml", "params": {"part": DOC, "xpath": "//w:pgMar", "count": 2, "action": "set_attribute",
                                      "name": "w:header", "value": "576"}}]}
    assert compiler.placed_words(attribute) == []
    for spec, words in ((worded, "SECTION 21 13 13"), (placed, "Coordination draft")):
        _outcome, errors = compose_submission(copy.deepcopy(spec), batch, ["change it"], unguarded=True)
        assert len(errors) == 1 and "the user has not read them" in errors[0] and words in errors[0]
        _outcome, errors = compose_submission(copy.deepcopy(spec), batch, ["change it"], unguarded=True,
                                              shown=[f"I will place “{words}” there."])
        assert errors == [], errors
        _outcome, errors = compose_submission(copy.deepcopy(spec), batch, [f"make it say {words}"], unguarded=True)
        assert errors == [], errors


def test_safe_and_default_are_told_nothing_of_it(batch):
    """Hidden from the catalog and every prompt of the other modes, from the unknown-operation hint, from a custom
    method's writer and from Safe mode's buttons; Obliterate's conversation is told all of it."""
    for told in (catalog.op_catalog(examples=False), catalog.op_sheet(), catalog.editspec_json_schema()):
        assert "edit_xml" not in str(told)
    assert "edit_xml" not in catalog.op_index() and "edit_xml" in catalog.op_index(unguarded=True)
    assert "edit_xml" in catalog.op_catalog(examples=False, unguarded=True)["ops"]
    assert "edit_xml" in catalog.op_sheet(True)["ops"] and "edit_xml" in str(catalog.editspec_json_schema(True))
    with pytest.raises(ValueError, match="Unknown operation"):
        catalog.describe_op("edit_xml")
    assert catalog.describe_op("edit_xml", unguarded=True)["common_assumptions"][0].startswith("Obliterate mode only")
    with pytest.raises(ValueError):
        batch.describe_op("edit_xml")
    batch.unguarded = True
    assert batch.describe_op("edit_xml")["name"] == "edit_xml"
    for chat in (False, True):
        guarded, loose = compiler.system_prompt(chat, False), compiler.system_prompt(chat, True)
        assert "edit_xml" not in guarded and "RAW XML" not in guarded and "xpath" not in guarded.lower()
        assert '"edit_xml"' in loose and "RAW XML (edit_xml, this mode only)" in loose
        assert "submit stages" in loose and loose.endswith(compiler.UNGUARDED_PROMPT)
    for prompt in (codegen.system_prompt(), codegen.system_prompt(unguarded=True)):
        assert "edit_xml" not in prompt and "xml_edit" not in prompt
    with pytest.raises(SpecError) as unknown:
        validate_spec({"request": "r", "summary": "s", "mode": "formatting", "ops": [{"op": "edit_xnl"}]})
    assert "edit_xml" not in str(unknown.value) and "set_font" in str(unknown.value)
    assert "edit_xml" not in str(recipes.catalog())


def test_both_sides_know_the_same_prefixes_and_limits_each_from_its_own_table():
    assert dict(vx._V_PREFIXES) == ex.NAMESPACES and vx._V_ACTIONS == set(ex.ACTIONS) and vx._V_WHERE == set(ex.POSITIONS)
    assert vx.V_XPATH_TIMEOUT_S == ex.XPATH_TIMEOUT_S and vx.V_MAX_XPATH_CHARS == ex.MAX_XPATH_CHARS
    assert vx._V_KEEPS_SPACE == set(ex.SPACED_TEXT)
    source_text = Path(vx.__file__).read_text(encoding="utf-8")
    for line in source_text.splitlines():
        if line.startswith(("from ", "import ")):
            assert "ops" not in line.split() and ".ops" not in line and "package" not in line and "ooxml" not in line
