"""Obliterate's hands-on tools (docs/BACKLOG.md O4; CLAUDE.md, Modes): a read-only browser of a document's parts,
saving one out as it is, and replacing one with a file of the user's.

Nothing in the window edits XML: the user changes a saved part in an editor of their own and puts it back with
``replace_part``, a built-in operation no model is told of and no model's plan may name. It predicts first — the
member holds the file's bytes, and no other member changes — and the verifier's own ``part_bytes``
(verify/partswap.py) hashes the output member itself. What the new part does to the words is listed from the copy,
never predicted.

Pinned here: the swap proven byte for byte, for any member; every refusal; every planted defect; a part that is no
longer XML reported as one failed check, never an exception; the tools off unless the user turns them on, in
Obliterate mode alone; reading that writes nothing; a save that never lands on a working file or in the scratch tree;
and the whole flow with no model call.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient
from lxml import etree

from fake_claude import FakeClient, resp, tool
from standing_helpers import owe
from make_fixtures import build
from spec_surgeon import catalog, codegen, compiler, hands_on, saved_recipes
from spec_surgeon.compiler import _Batch, compose_submission
from spec_surgeon.document_task import (HANDS_ON_NEEDED, HANDS_ON_OFF, HANDS_ON_ON, OBLITERATE_ON, PART_ONLY_THERE,
                                         DocumentTask)
from spec_surgeon.gui import Session
from spec_surgeon.ops import OPS, JobException, get_op, merge_fragments
from spec_surgeon.ops import replace_part as rp
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.preview import (PART_NO_WORDS_CHANGE, PART_OTHER_CHANGES_HEAD, PART_WORDS_CHANGE_HEAD, raw_kind,
                                  raw_listing)
from spec_surgeon.report import FAILURE_EXPLANATIONS
from spec_surgeon.settings import Settings
from spec_surgeon.ui_feedback import UNPROVEN_MARK, refusal_message
from spec_surgeon.verify import run_difference, run_suite
from spec_surgeon.verify.reader import VPackage
from spec_surgeon.verify.props import KIND_CONTRACTS, KINDS, OP_ONLY_KINDS
from spec_surgeon.web.server import TOKEN_HEADER, WindowState, create_app
from test_document_task import SPEC, document
from test_web_server import TOKEN, FakeUpdates, frames

DOC = "word/document.xml"
KEEP = b"Keep this technical wording intact."


@pytest.fixture(scope="module")
def source() -> bytes:
    return build("21 00 00", "SPEC 0")


def member(data: bytes, name: str) -> bytes:
    return zipfile.ZipFile(io.BytesIO(data)).read(name)


def swap(src: bytes, tmp_path: Path, part: str, new: bytes, name: str = "mine.xml"):
    """Plan, apply, verify: ``(plan, output bytes, record)``."""
    file = tmp_path / name
    file.write_bytes(new)
    op = get_op("replace_part")
    frag = op.plan(DocxPackage(src), {}, {"part": part, "file": str(file), "sha256": hashlib.sha256(new).hexdigest()})
    plan = merge_fragments([frag]).to_dict()
    work = DocxPackage(src).copy()
    op.apply(work, frag)
    out = work.output_bytes()
    return plan, out, run_suite(src, out, plan, "source.docx", "copy.docx")


def failed(rec) -> Dict[str, str]:
    return {c.id: c.summary for c in rec.checks if c.status == "FAIL"}


# ---- the swap, proven ----------------------------------------------------------------------------------------------

def test_a_replaced_part_holds_the_files_bytes_and_its_words_are_listed_from_the_copy(source, tmp_path):
    new = member(source, DOC).replace(b"FM Approved", b"UL Listed")
    plan, out, rec = swap(source, tmp_path, DOC, new)
    assert rec.passed, failed(rec)
    assert member(out, DOC) == new and rec.get("V1").data["changed"] == [DOC]
    [assertion] = plan["property_assertions"]
    assert assertion == {"kind": "part_bytes", "part": DOC, "sha256": hashlib.sha256(new).hexdigest(),
                         "size": len(new), "op": "replace_part", "op_index": 0}
    assert plan["mode"] == "untracked" and plan["diffs"] == [] and plan["geometry_reference"] == "none"
    assert "part_bytes word/document.xml" in rec.get("V10").summary
    # the words are the verifier's own reading of the copy, never a prediction; the listing is a replaced part's
    assert rec.get("V2").data["raw_parts"] == [DOC] and raw_kind(rec) == "part"
    words, _other = raw_listing(rec)
    assert {(tuple(w["removed"]), tuple(w["added"])) for w in words} == {
        (("FM Approved.",), ("UL Listed.",)), (("FM Approved",), ("UL Listed",))}
    # every other member is the source's, byte for byte, in its order
    names = zipfile.ZipFile(io.BytesIO(source)).namelist()
    assert zipfile.ZipFile(io.BytesIO(out)).namelist() == names
    assert all(member(out, n) == member(source, n) for n in names if n != DOC)


def test_any_member_can_be_replaced_and_what_word_would_not_open_fails_a_check(source, tmp_path):
    """The bytes are placed as they are, so nothing is out of reach — a picture, the styles, the package's own
    relationships — and what the result does to the file is the checks' to say."""
    plan, out, rec = swap(source, tmp_path, "docProps/thumbnail.jpeg", b"\xff\xd8\xff\xe0 another picture", "t.jpeg")
    assert rec.passed and raw_listing(rec)[0] == []
    assert any("not read by this report" in line for line in raw_listing(rec)[1])
    plan, out, rec = swap(source, tmp_path, "word/styles.xml", member(source, "word/styles.xml") + b"\n")
    assert rec.passed, failed(rec)
    # a relationship id the body still names: the reference dangles, and the package check says so
    body = member(source, DOC)
    rid = body[body.index(b'r:id="') + 6:].split(b'"')[0]
    rels = member(source, "word/_rels/document.xml.rels")
    plan, out, rec = swap(source, tmp_path, "word/_rels/document.xml.rels",
                          rels.replace(b'Id="' + rid + b'"', b'Id="rId991"'), "document.xml.rels")
    assert set(failed(rec)) == {"V1"} and rid.decode() in failed(rec)["V1"]
    assert rec.get("V10").status == "PASS"                          # the swap itself is what was asked
    # a document property: text the paragraph reader does not read, listed as such
    core = member(source, "docProps/core.xml").replace(b"python-docx</dc:creator>", b"Design Team</dc:creator>")
    plan, out, rec = swap(source, tmp_path, "docProps/core.xml", core)
    assert rec.passed and raw_listing(rec)[0] == [
        {"part": "docProps/core.xml", "where": "docProps/core.xml, other text (not a paragraph's words)",
         "paragraph": None, "removed": ["python-docx"], "added": ["Design Team"], "other": True}]


def test_a_part_that_is_no_longer_xml_is_one_failed_check_never_an_exception(source, tmp_path):
    for part in (DOC, "word/styles.xml", "word/footer1.xml"):
        plan, out, rec = swap(source, tmp_path, part, b"<w:document unfinished", "broken.xml")
        assert not rec.passed and set(failed(rec)) == {"V1"}
        assert failed(rec)["V1"].startswith(f"NOT WELL-FORMED XML in the copy: {part} (")
        assert "Word does not open such a file" in failed(rec)["V1"] and rec.get("V1").data["unreadable"] == [part]
        for check in ("V2", "V3", "V4", "V5a", "V5b", "V5c", "V8", "V10"):
            assert rec.get(check).status == "NA" and "not evaluated" in rec.get(check).summary
        assert raw_listing(rec) is None
    # a member that was never XML is no such case
    plan, out, rec = swap(source, tmp_path, "docProps/thumbnail.jpeg", b"<not xml either", "t.jpeg")
    assert rec.passed


WNS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
RELS = "word/_rels/document.xml.rels"
RELNS = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'


def test_a_part_that_is_not_the_kind_its_place_calls_for_fails_the_package_check(source, tmp_path):
    """The schema knows w:styles and w:document alike, so a body part that is a w:styles has no schema error; Word
    reads a part by its place in the package. The root element of a part of Office's own vocabularies is checked."""
    plan, out, rec = swap(source, tmp_path, DOC, f"<w:styles {WNS}/>".encode())
    assert set(failed(rec)) == {"V1"}, failed(rec)
    assert "word/document.xml: its root element is <styles> where the source's is <document>" in failed(rec)["V1"]
    assert rec.get("V10").status == "PASS"                         # the swap itself is what was asked
    plan, out, rec = swap(source, tmp_path, "word/styles.xml", f"<w:document {WNS}><w:body/></w:document>".encode())
    assert "its root element is <document> where the source's is <styles>" in failed(rec).get("V1", "")
    plan, out, rec = swap(source, tmp_path, RELS, f"<w:styles {WNS}/>".encode(), "x.rels")
    assert "<styles> where the source's is <Relationships>" in " ".join(rec.get("V1").data["integrity"])
    # the Obliterate record, which a custom method's copy is read by, says the same of such a copy
    shown = run_difference(source, swap(source, tmp_path, DOC, f"<w:styles {WNS}/>".encode())[1], "a.docx", "b.docx")
    assert shown.get("V1").status == "FAIL" and "its root element is <styles>" in " ".join(shown.get("V1").data["integrity"])
    # the user's own data may have any root: stored XML in a namespace of theirs
    work = DocxPackage(source).copy()
    work.write("customXml/item1.xml", b'<data xmlns="urn:mine"><v>1</v></data>')
    mine = work.output_bytes()
    plan, out, rec = swap(mine, tmp_path, "customXml/item1.xml", b'<other xmlns="urn:mine"/>')
    assert "V1" not in failed(rec), failed(rec)
    # and the same root passes, whatever else the part now holds
    plan, out, rec = swap(source, tmp_path, DOC, member(source, DOC).replace(b"FM Approved", b"UL Listed"))
    assert rec.passed


def test_a_relationships_part_that_lacks_what_a_relationship_needs_is_a_failed_check_never_a_crash(source, tmp_path,
                                                                                                    monkeypatch):
    for lacking, relationship in (("Type", '<Relationship Id="rId1" Target="nowhere.xml"/>'),
                                  ("Target", '<Relationship Id="rId1" Type="x"/>'),
                                  ("Id", '<Relationship Type="x" Target="styles.xml"/>'),
                                  ("Id, Type, Target", "<Relationship/>")):
        new = f"<Relationships {RELNS}>{relationship}</Relationships>".encode()
        plan, out, rec = swap(source, tmp_path, RELS, new, "document.xml.rels")
        assert "V1" in failed(rec) and rec.get("V10").status == "PASS", lacking
        assert f"relationship in {RELS} without its {lacking}" in rec.get("V1").data["integrity"]
        shown = run_difference(source, out, "a.docx", "b.docx")     # the Obliterate record reads it too
        assert shown.get("V1").status == "FAIL"
    # the net under what the readers do not foresee: one that raises on the copy's bookkeeping alone
    new = member(source, RELS) + b"<!-- mine -->"
    real = VPackage.referenced_members

    def touchy(self):
        if self.read(RELS) == new:
            raise KeyError("a shape no reader foresaw")
        return real(self)
    monkeypatch.setattr(VPackage, "referenced_members", touchy)
    plan, out, rec = swap(source, tmp_path, RELS, new, "document.xml.rels")
    assert set(failed(rec)) == {"V1"}
    assert failed(rec)["V1"].startswith(f"RELATIONSHIPS OR CONTENT TYPES THAT DO NOT READ in the copy: {RELS} (KeyError")
    assert rec.get("V2").status == "NA" and "does not read as the package's relationships" in rec.get("V2").summary
    # when no bookkeeping part changed, a reader's exception is the verifier's own defect, and goes on as one

    def defect(self):
        raise KeyError("a verifier defect")
    monkeypatch.setattr(VPackage, "referenced_members", defect)
    with pytest.raises(KeyError, match="a verifier defect"):
        swap(source, tmp_path, DOC, member(source, DOC).replace(b"FM Approved", b"UL Listed"))


# ---- refusals ------------------------------------------------------------------------------------------------------

def refusal(src: bytes, **params) -> JobException:
    with pytest.raises(JobException) as caught:
        get_op("replace_part").plan(DocxPackage(src), {}, params)
    return caught.value


def test_a_choice_that_cannot_be_used_is_refused_with_the_reason(source, tmp_path, monkeypatch):
    file = tmp_path / "mine.xml"
    file.write_bytes(b"<a/>")
    missing = refusal(source, part="word/header9.xml", file=str(file))
    assert missing.code == "part-missing" and DOC in missing.details["parts"]
    assert "a part is replaced, never added" in refusal_message(
        {"code": missing.code, "reason": missing.reason, "details": missing.details})
    same = tmp_path / "same.xml"
    same.write_bytes(member(source, DOC))
    stop = refusal(source, part=DOC, file=str(same))
    assert stop.code == "part-same" and "nothing would change" in refusal_message(
        {"code": stop.code, "reason": stop.reason, "details": stop.details})
    gone = refusal(source, part=DOC, file=str(tmp_path / "nowhere.xml"))
    assert gone.code == "part-file" and gone.details["why"] == "unreadable"
    moved = refusal(source, part=DOC, file=str(file), sha256=hashlib.sha256(b"<b/>").hexdigest())
    assert moved.code == "part-file" and moved.details["why"] == "changed"
    assert "is not the file you chose any more" in refusal_message(
        {"code": moved.code, "reason": moved.reason, "details": moved.details})
    monkeypatch.setattr(rp, "MAX_PART_BYTES", 3)
    big = refusal(source, part=DOC, file=str(file))
    assert big.code == "part-file" and big.details["why"] == "too-big" and big.details["limit"] == 3
    monkeypatch.undo()
    for bad in ({"part": "", "file": str(file)}, {"part": DOC, "file": ""}, {"part": DOC, "file": 3},
                {"part": DOC, "file": str(file), "sha256": "abc"}, {"part": DOC, "file": str(file), "tracked": True}):
        assert refusal(source, **bad).code == "bad-params", bad
    with pytest.raises(JobException, match="takes no select"):
        get_op("replace_part").plan(DocxPackage(source), {"scope": ["document"]}, {"part": DOC, "file": str(file)})
    for code in ("part-missing", "part-file", "part-same", "hands-on-only"):
        assert code in FAILURE_EXPLANATIONS


def test_a_file_that_changed_between_the_plan_and_the_run_is_never_placed(source, tmp_path):
    file = tmp_path / "mine.xml"
    file.write_bytes(member(source, DOC).replace(b"FM Approved", b"UL Listed"))
    op = get_op("replace_part")
    frag = op.plan(DocxPackage(source), {}, {"part": DOC, "file": str(file)})
    file.write_bytes(b"<w:document>something else</w:document>")
    work = DocxPackage(source).copy()
    with pytest.raises(JobException) as stop:
        op.apply(work, frag)
    assert stop.value.code == "part-file" and stop.value.details["why"] == "changed" and not work.is_modified()


# ---- planted defects ------------------------------------------------------------------------------------------------

def test_a_copy_that_holds_other_bytes_or_changes_another_part_fails(source, tmp_path):
    new = member(source, DOC).replace(b"FM Approved", b"UL Listed")
    plan, out, rec = swap(source, tmp_path, DOC, new)

    def with_member(data: bytes, name: str, content: bytes) -> bytes:
        work = DocxPackage(data).copy()
        work.write(name, content)
        return work.output_bytes()
    # one byte off: the same words, another file
    off = run_suite(source, with_member(out, DOC, new + b" "), plan, "a", "b")
    assert "V10" in failed(off) and "is not the bytes the plan names" in failed(off)["V10"]
    # the swap not made at all
    assert "V10" in failed(run_suite(source, source, plan, "a", "b"))
    # another part changed beside it
    other = run_suite(source, with_member(out, "word/footer1.xml", member(source, "word/footer1.xml").replace(
        b"21 00 00", b"99 99 99")), plan, "a", "b")
    assert {"V1", "V2"} <= set(failed(other))
    # a plan that names bytes the copy does not hold, and one the operation had to refuse
    lying = copy.deepcopy(plan)
    lying["property_assertions"][0]["sha256"] = hashlib.sha256(b"other").hexdigest()
    assert "V10" in failed(run_suite(source, out, lying, "a", "b"))
    noop = copy.deepcopy(plan)
    noop["property_assertions"][0].update(sha256=hashlib.sha256(member(source, DOC)).hexdigest(),
                                          size=len(member(source, DOC)))
    assert "already those bytes: the op had to refuse" in failed(run_suite(source, source, noop, "a", "b"))["V10"]
    absent = copy.deepcopy(plan)
    absent["property_assertions"][0]["part"] = "word/nothing.xml"
    assert "a part is replaced, never added" in failed(run_suite(source, out, absent, "a", "b"))["V10"]
    # the text checks leave the part to this comparison only while the assertion is there
    bare = {**plan, "property_assertions": []}
    assert {"V2", "V10"} <= set(failed(run_suite(source, out, bare, "a", "b")))


def test_a_wrong_implementation_the_plan_and_the_transform_share_fails(source, tmp_path, monkeypatch):
    """The transform's own reader trims the file. The plan hashes what it read, and so does the copy: only a
    verifier that hashes the member itself against the digest pinned when the user chose the file can tell — here
    the pinned digest is the user's file's, and the op refuses before anything is made."""
    real = rp.read_replacement
    monkeypatch.setattr(rp, "read_replacement", lambda file: real(file).rstrip())
    new = member(source, DOC).replace(b"FM Approved", b"UL Listed") + b"\n"
    file = tmp_path / "mine.xml"
    file.write_bytes(new)
    stop = refusal(source, part=DOC, file=str(file), sha256=hashlib.sha256(new).hexdigest())
    assert stop.code == "part-file" and stop.details["why"] == "changed"


# ---- the user's own tool: no model is told of it, and no plan may name it -------------------------------------------

@pytest.fixture
def batch(tmp_path):
    folder = tmp_path / "specs"
    folder.mkdir()
    for i in range(2):
        (folder / f"21 0{i} 00 – SPEC {i}.docx").write_bytes(build(f"21 0{i} 00", f"SPEC {i}"))
    files = sorted(folder.glob("*.docx"))
    file = tmp_path / "mine.xml"
    file.write_bytes(member(files[0].read_bytes(), DOC).replace(b"FM Approved", b"UL Listed"))
    return _Batch(files, None, input_files=[file]), files, file


def test_the_gate_takes_it_only_from_the_window_while_the_tools_are_on(batch):
    b, files, file = batch
    spec = {"request": "r", "summary": "s", "mode": "untracked",
            "ops": [{"op": "replace_part", "params": {"part": DOC, "file": str(file)}}]}
    as_stages = {"summary": "s", "stages": [{"name": "one", "spec": spec}]}
    as_layouts = {"summary": "s", "layouts": [{"id": "a", "label": "A", "spec": spec,
                                               "pattern_ids": [p["pattern_id"] for p in b.prof["patterns"]]}]}
    for shape in (spec, as_stages, as_layouts):
        for unguarded in (False, True):                             # Default, and Obliterate without the tools
            _outcome, errors = compose_submission(copy.deepcopy(shape), b, ["r"], unguarded=unguarded,
                                                  leave_out=[files[1]])
            assert len(errors) == 1 and errors[0].startswith("[hands-on-only]") and "replace_part" in errors[0]
    _outcome, errors = compose_submission(copy.deepcopy(spec), b, ["r"], unguarded=True, hands_on=True,
                                          leave_out=[files[1]])
    assert errors == [], errors
    # the tools' flag is no way round the mode, and no way to a file nobody chose
    _outcome, errors = compose_submission(copy.deepcopy(spec), b, ["r"], unguarded=False, hands_on=True)
    assert errors and errors[0].startswith("[hands-on-only]")
    stray = copy.deepcopy(spec)
    stray["ops"][0]["params"]["file"] = str(files[1])
    _outcome, errors = compose_submission(stray, b, ["r"], unguarded=True, hands_on=True, leave_out=[files[1]])
    assert errors and errors[0].startswith("[input]")
    # a raw XML edit is still Obliterate's own, tools or no tools
    raw = {"request": "r", "summary": "s", "mode": "untracked",
           "ops": [{"op": "edit_xml", "params": {"part": DOC, "xpath": "//w:tbl", "count": 1, "action": "remove"}}]}
    assert compiler._mode_errors(raw, False, True)[0].startswith("[obliterate-only]")
    assert compiler._mode_errors(raw, True) == [] and compiler._mode_errors(spec, True, True) == []


def test_no_model_is_ever_told_of_it(batch):
    b, _files, _file = batch
    assert [name for name, cls in OPS.items() if cls.hands_on] == ["replace_part"]
    assert OPS["replace_part"].obliterate_only
    for unguarded in (False, True):
        for told in (catalog.op_catalog(examples=False, unguarded=unguarded), catalog.op_sheet(unguarded),
                     catalog.editspec_json_schema(unguarded), catalog.op_index(unguarded)):
            assert "replace_part" not in str(told)
        with pytest.raises(ValueError):
            catalog.describe_op("replace_part", unguarded=unguarded)
        for chat in (False, True):
            assert "replace_part" not in compiler.system_prompt(chat, unguarded)
            assert "part_bytes" not in compiler.system_prompt(chat, unguarded)
    for prompt in (codegen.system_prompt(), codegen.system_prompt(unguarded=True)):
        assert "replace_part" not in prompt and "part_bytes" not in prompt
    # the verifier's kind is a built-in operation's alone
    assert "part_bytes" in KINDS and "part_bytes" in OP_ONLY_KINDS and "part_bytes" not in codegen.writer_kinds()
    assert "Compared" in KIND_CONTRACTS["part_bytes"]
    # and a part swapped for one document is no step a recipe can repeat
    spec = {"request": "r", "summary": "s", "mode": "untracked",
            "ops": [{"op": "replace_part", "params": {"part": DOC, "file": "x"}}]}
    assert saved_recipes.portable_plan(spec) == (None, saved_recipes.WHY_PART)


# ---- reading: the part browser --------------------------------------------------------------------------------------

def test_the_reader_lists_shows_and_searches_and_writes_nothing(source):
    reader = hands_on.Reader(source)
    names = [p["name"] for p in reader.parts]
    assert names == zipfile.ZipFile(io.BytesIO(source)).namelist()
    body = next(p for p in reader.parts if p["name"] == DOC)
    assert body["size"] == len(member(source, DOC)) and body["xml"] and "wordprocessingml.document.main" in body["type"]
    picture = next(p for p in reader.parts if p["name"] == "docProps/thumbnail.jpeg")
    assert not picture["xml"]
    page = reader.page(DOC)
    assert page["page"] == 1 and page["first"] == 1 and page["total"] > 100 and len(page["lines"]) <= hands_on.PAGE_LINES
    assert page["lines"][0].startswith("<w:document ") and "indented for reading" in page["note"]
    assert reader.page(DOC, 99)["page"] == page["pages"] and reader.page(DOC, -4)["page"] == 1
    shown = reader.page("docProps/thumbnail.jpeg")
    assert shown["lines"] == [] and shown["note"].startswith("Not text: ")
    with pytest.raises(KeyError):
        reader.page("word/nothing.xml")
    found = reader.search("fm global")
    assert not found["more"] and len(found["matches"]) >= 3 and {m["part"] for m in found["matches"]} == {DOC}
    first = found["matches"][0]
    assert "FM Global" in first["text"] and "FM Global" in reader.page(DOC, first["page"])["lines"][
        first["line"] - reader.page(DOC, first["page"])["first"]]
    assert reader.search("   ") == {"query": "", "matches": [], "more": False}
    many = reader.search("w:")
    assert many["more"] and len(many["matches"]) == hands_on.SEARCH_HITS
    assert hands_on.part_bytes(source, DOC) == member(source, DOC)


def test_a_part_that_does_not_parse_is_shown_as_its_own_text_and_long_lines_are_cut(source):
    work = DocxPackage(source).copy()
    work.write("customXml/item1.xml", b"<a><b>unfinished")
    work.write("docProps/app.xml", b"<p>" + b"x" * (hands_on.LINE_CHARS + 50) + b"</p>")
    reader = hands_on.Reader(work.output_bytes())
    broken = reader.page("customXml/item1.xml")
    assert broken["lines"] == ["<a><b>unfinished"] and broken["note"].startswith("Not well-formed XML")
    [line] = reader.page("docProps/app.xml")["lines"]
    assert len(line) < hands_on.LINE_CHARS + 60 and line.endswith("more characters on this line)")
    assert hands_on.saved_name("21 13 13 - Wet.docx", "word/_rels/document.xml.rels") == \
        "21 13 13 - Wet - word__rels_document.xml.rels"
    assert hands_on.saved_name("a.DOCX", "[Content_Types].xml") == "a - [Content_Types].xml"


# ---- the window: off unless turned on, in Obliterate alone ----------------------------------------------------------

def task_for(tmp_path, fake=None, **kwargs):
    kwargs.setdefault("obliterate_allowed", True)
    return DocumentTask(Session(client=fake or FakeClient(), out_root=tmp_path / "out", visual_check=False),
                        render=False, **kwargs)


def said(task):
    return [m["text"] for m in task.messages if m["role"] == "app"]


def hands_on_task(tmp_path, *names, fake=None):
    docs = [document(tmp_path / name) for name in (names or ("a.docx", "b.docx"))]
    task = task_for(tmp_path, fake)
    task.attach(docs)
    task.set_mode("obliterate", acknowledged=True, hands_on=True)
    return task, docs


def test_the_tools_are_off_until_the_user_turns_them_on_and_go_with_the_mode(tmp_path):
    a = document(tmp_path / "a.docx")
    task = task_for(tmp_path)
    task.attach([a])
    assert task.view()["hands_on"] is False
    for call in (lambda: task.parts(str(a)), lambda: task.part(str(a), DOC), lambda: task.search_parts(str(a), "x"),
                 lambda: task.save_part(str(a), DOC, str(tmp_path))):
        with pytest.raises(ValueError, match="hands-on tools are off"):
            call()
    with pytest.raises(ValueError, match="Obliterate mode's"):
        task.set_hands_on(True)                                     # Default has no such switch
    # the warning's toggle is off unless ticked
    task.set_mode("obliterate", acknowledged=True)
    assert task.hands_on is False and task.view()["hands_on"] is False and HANDS_ON_ON not in said(task)
    with pytest.raises(ValueError, match="hands-on tools are off"):
        task.parts(str(a))
    task.set_hands_on(True)
    assert task.view()["hands_on"] is True and said(task)[-1] == HANDS_ON_ON and task.parts(str(a))["parts"]
    task.set_hands_on(True)                                          # said once
    assert said(task).count(HANDS_ON_ON) == 1
    task.set_hands_on(False)
    assert said(task)[-1] == HANDS_ON_OFF and task.view()["hands_on"] is False
    # ticked in the warning, they turn on with the mode — and leaving the mode turns them off
    task.set_mode("default")
    task.set_mode("obliterate", acknowledged=True, hands_on=True)
    assert task.hands_on and said(task)[-2:] == [OBLITERATE_ON, HANDS_ON_ON]
    task.set_mode("default")
    assert task.hands_on is False and task.view()["hands_on"] is False
    with pytest.raises(ValueError, match="hands-on tools are off"):
        task.parts(str(a))
    for truthy in ("yes", 1, None):                                  # only the tick itself
        task.set_mode("obliterate", acknowledged=True, hands_on=truthy)
        assert task.hands_on is False
        task.set_mode("default")
    # never kept: a window restored from its journal opens with them off, in the mode a launch may open in
    journal = tmp_path / "journal.json"
    task.journal = journal
    task.set_mode("obliterate", acknowledged=True, hands_on=True)
    task.save()
    again = task_for(tmp_path, journal=journal)
    assert again.restore() and again.mode != "obliterate" and again.hands_on is False
    assert "hands_on" not in json.loads(journal.read_text(encoding="utf-8"))


def test_the_browser_reads_the_file_the_next_change_is_made_on_or_the_original(tmp_path):
    task, (a, b) = hands_on_task(tmp_path)
    before = {p: p.read_bytes() for p in (a, b)}
    listing = task.parts(str(a))
    assert listing["document"] == "a.docx" and listing["which"] == "current" and listing["has_copy"] is False
    assert DOC in [p["name"] for p in listing["parts"]]
    page = task.part(str(a), DOC)
    assert any("Keep this technical wording intact." in line for line in page["lines"])
    [hit] = task.search_parts(str(a), "technical wording")["matches"]
    assert hit["part"] == DOC and hit["page"] == 1
    with pytest.raises(ValueError, match="has no part named"):
        task.part(str(a), "word/nothing.xml")
    with pytest.raises(ValueError, match="not one of the documents"):
        task.parts(str(tmp_path / "stranger.docx"))
    with pytest.raises(ValueError, match="edited copy or the original"):
        task.parts(str(a), "elsewhere")
    assert {p: p.read_bytes() for p in (a, b)} == before and not (tmp_path / "out").exists()   # nothing was written
    assert task.stage == "empty" and task.session.spec is None


def test_saving_a_part_writes_its_own_bytes_where_the_user_chose_and_nowhere_else(tmp_path):
    task, (a, _b) = hands_on_task(tmp_path)
    mine = tmp_path / "mine"
    mine.mkdir()
    made = task.save_part(str(a), DOC, str(mine))
    saved = Path(made["saved"])
    assert saved == mine.resolve() / "a - word_document.xml" and made["already"] is False
    assert saved.read_bytes() == member(a.read_bytes(), DOC)        # exactly, not the indented view
    assert said(task)[-1].startswith(f"Saved the part {DOC} of a.docx (the original), as it is, to {saved}.")
    assert task.stage == "empty" and task.results == {} and task.working == {}
    # again: the same file, nothing written; other bytes there: left alone, this one numbered beside it
    assert task.save_part(str(a), DOC, str(mine)) == {"saved": str(saved), "already": True}
    saved.write_bytes(b"my own edit")
    beside = Path(task.save_part(str(a), DOC, str(mine))["saved"])
    assert beside.name == "a - word_document (2).xml" and saved.read_bytes() == b"my own edit"
    # never into the scratch tree, never onto a file the conversation works on, never a folder that is not one
    scratch = Path(task.session.out_root)
    scratch.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError, match="scratch folder"):
        task.save_part(str(a), DOC, str(scratch))
    with pytest.raises(ValueError, match="is not a folder"):
        task.save_part(str(a), DOC, str(tmp_path / "nowhere"))
    with pytest.raises(ValueError, match="has no part named"):
        task.save_part(str(a), "word/nothing.xml", str(mine))
    clash = tmp_path / "clash"
    clash.mkdir()
    twin = document(clash / "a - word_document.xml.docx")
    twin.rename(clash / "a - word_document.xml")
    task.attach([document(clash / "c.docx")])
    task.attachments[-1].path = clash / "a - word_document.xml"      # a working file under the very name
    with pytest.raises(ValueError, match="one of the files this conversation works on"):
        task.save_part(str(a), DOC, str(clash))


def test_replacing_a_part_is_one_checked_change_with_no_model_call(tmp_path):
    fake = FakeClient()
    task, (a, b) = hands_on_task(tmp_path, fake=fake)
    original = a.read_bytes()
    mine = tmp_path / "mine"
    mine.mkdir()
    file = Path(task.save_part(str(a), DOC, str(mine))["saved"])
    new = file.read_bytes().replace(KEEP, KEEP + b" Reviewed.")
    file.write_bytes(new)
    view = task.perform("replace_part", path=str(a), part=DOC, file=str(file))
    assert view["stage"] == "preview" and fake.requests == []
    request = f"Replace the part {DOC} of a.docx with my file {file.name}."
    assert task.decisions == [request] and task.session.outcome_source == "hands-on" and task.session.model_free
    [change] = view["groups"][0]["changes"]
    assert change["title"] == "Replace a part" and change["summary"].startswith(
        f"Replace the part “{DOC}” with the file “{file.name}”, byte for byte.")
    # one document is in the plan; the other was not asked about
    [sample] = task._samples_view()["samples"]
    assert sample["name"] == "a.docx" and sample["passed"] and not sample["unproven"]
    assert sample["raw_xml"] and sample["raw_what"] == "part"
    assert [(c["removed"], c["added"]) for c in sample["text_changes"]] == [([], ["Reviewed."])]
    assert task.session.left_out_reasons == {str(b.resolve()): PART_ONLY_THERE.format(name="a.docx")}
    # said before the button: no AI, checked byte for byte, and the words from the copy
    told = said(task)
    assert any(t.startswith("Planned without AI: Replace the part “word/document.xml”") for t in told)
    [heard] = [t for t in told if t.startswith(PART_WORDS_CHANGE_HEAD)]
    assert "a.docx, body paragraph 3: words added “Reviewed.”" in heard
    view = task.perform("primary", token=view["token"])
    assert task.stage == "done" and list(task.results) == [str(a)] and task.unverified == {}
    assert UNPROVEN_MARK not in json.dumps(task.view(), default=str) and fake.requests == []
    copy_ = Path(task.results[str(a)])
    assert member(copy_.read_bytes(), DOC) == new and a.read_bytes() == original       # copies only
    # one change: listed, never a recipe's step, undone by Undo; the copy is what the browser now reads
    done = view["working"]["done"][-1]
    assert done["request"] == request and done["documents"] == 1 and done["why_not"] == saved_recipes.WHY_PART
    assert task.parts(str(a))["has_copy"] is True
    assert any("Reviewed." in line for line in task.part(str(a), DOC)["lines"])
    assert not any("Reviewed." in line for line in task.part(str(a), DOC, which="original")["lines"])
    task.undo()
    assert task.results == {} and str(a) not in task.working and copy_.is_file()


def test_a_part_that_changes_no_words_says_so_and_a_broken_one_is_delivered_unverified(tmp_path):
    task, (a, _b) = hands_on_task(tmp_path)
    styles = tmp_path / "styles.xml"
    styles.write_bytes(member(a.read_bytes(), "word/styles.xml") + b"\n")
    view = task.perform("replace_part", path=str(a), part="word/styles.xml", file=str(styles))
    assert view["stage"] == "preview" and PART_NO_WORDS_CHANGE in said(task)
    assert not any(t.startswith(PART_OTHER_CHANGES_HEAD) and "words" in t.split(":")[0] for t in said(task)[:0])
    task.discard_preview()
    assert task.stage in ("empty", "done") and task.session.spec is None
    # a part that is no longer XML: the sample did not pass, the plan stands, and the copy is marked
    broken = tmp_path / "broken.xml"
    broken.write_bytes(b"<w:document unfinished")
    view = task.perform("replace_part", path=str(a), part=DOC, file=str(broken))
    assert view["stage"] == "preview"
    [sample] = task._samples_view()["samples"]
    assert sample["raw_xml"] and sample["raw_what"] == "part" and sample["text_changes"] is None   # nothing was read
    assert not any(t.startswith(PART_WORDS_CHANGE_HEAD) for t in said(task)[-2:])
    assert said(task)[-1] == ("The words this changes could not be listed for a.docx: the copy could not be read "
                              "part by part. Its preview says which check failed.")
    assert sample["passed"] is False and sample["failures"] == [
        {"check": "V1", "reason": "The copy's file has a fault Word repairs or refuses: a part that does not parse "
                                  "or is not the kind its place in the file calls for, a broken reference or a part "
                                  "without its content type."}]
    task.perform("primary", token=view["token"])
    assert task.stage == "done" and task.unverified[str(a)] == ["V1 (Package hash whitelist)"]
    assert member(Path(task.results[str(a)]).read_bytes(), DOC) == b"<w:document unfinished"


def test_a_choice_that_cannot_be_used_is_said_once_and_nothing_is_previewed(tmp_path):
    task, (a, b) = hands_on_task(tmp_path)
    same = tmp_path / "same.xml"
    same.write_bytes(member(a.read_bytes(), DOC))
    for part, file, why in ((DOC, same, "nothing would change"),
                            ("word/nothing.xml", same, "a part is replaced, never added"),
                            (DOC, tmp_path / "nowhere.xml", "cannot be read")):
        count = len(task.messages)
        view = task.perform("replace_part", path=str(a), part=part, file=str(file))
        assert view["stage"] == "empty" and task.session.spec is None and len(task.messages) == count + 1
        assert said(task)[-1].startswith("Replace this part: ") and why in said(task)[-1]
    # a preview that waits is finished or discarded first; a document on the to-do list is out of every plan
    new = tmp_path / "new.xml"
    new.write_bytes(same.read_bytes().replace(KEEP, KEEP + b" Reviewed."))
    view = task.perform("replace_part", path=str(a), part=DOC, file=str(new))
    spec = task.session.spec
    again = task.perform("replace_part", path=str(b), part=DOC, file=str(new))
    assert again["stage"] == "preview" and task.session.spec is spec       # the first one still waits, as it was
    assert said(task)[-1] == "Replace this part: Discard the preview first, or create its copies."
    task.discard_preview()
    task.todo[str(b)] = {"name": "b.docx", "why": "set aside", "reason": "You set it aside."}
    task._sync()
    task.perform("replace_part", path=str(b), part=DOC, file=str(new))
    assert "waits on your to-do list" in said(task)[-1] and task.session.spec is None
    # turning the tools off withdraws a replacement that waits: the gate takes it only while they are on
    view = task.perform("replace_part", path=str(a), part=DOC, file=str(new))
    assert view["stage"] == "preview"
    task.set_hands_on(False)
    assert task.session.spec is None and task.stage != "preview" and task.decisions == []
    with pytest.raises(ValueError, match="hands-on tools are off"):
        task.perform("replace_part", path=str(a), part=DOC, file=str(new))


def test_a_file_changed_after_the_preview_is_not_what_was_approved(tmp_path):
    task, (a, _b) = hands_on_task(tmp_path)
    new = tmp_path / "new.xml"
    new.write_bytes(member(a.read_bytes(), DOC).replace(KEEP, KEEP + b" Reviewed."))
    view = task.perform("replace_part", path=str(a), part=DOC, file=str(new))
    new.write_bytes(member(a.read_bytes(), DOC).replace(KEEP, b"Something nobody previewed."))
    with pytest.raises(ValueError, match="changed since the preview"):
        task.perform("primary", token=view["token"])
    assert str(a) not in task.results
    assert not any(Path(p).is_file() and b"nobody previewed" in member(Path(p).read_bytes(), DOC)
                   for p in task.working.values())


def test_a_document_the_engine_stopped_on_is_what_the_tool_is_for(tmp_path):
    """From **Obliterate this one**: the pass is over that one document, its request standing, and a part of it can
    be replaced by hand without dropping the pass first."""
    a, b = document(tmp_path / "a.docx"), document(tmp_path / "b.docx")
    fake = FakeClient(resp(tool("submit_editspec", {"spec": SPEC})))
    task = task_for(tmp_path, fake)
    task.attach([a, b])
    owe(task, b, "", reason="The plan could not be applied to it.")
    task.obliterate_todo(str(b), acknowledged=True, hands_on=True)
    assert task.mode == "obliterate" and task.hands_on and task.focus["documents"] == [str(b)]
    new = tmp_path / "new.xml"
    new.write_bytes(member(b.read_bytes(), DOC).replace(KEEP, KEEP + b" Reviewed."))
    calls = len(fake.requests)
    view = task.perform("replace_part", path=str(b), part=DOC, file=str(new))
    assert view["stage"] == "preview" and len(fake.requests) == calls
    task.perform("primary", token=view["token"])
    assert list(task.results) == [str(b)] and str(b) not in task.todo and len(fake.requests) == calls


# ---- the server ------------------------------------------------------------------------------------------------------

def window(tmp_path):
    session = Session(client=FakeClient(), out_root=tmp_path / "out", visual_check=False)
    task = DocumentTask(session, render=False, obliterate_allowed=True)
    state = WindowState(task, Settings(show_obliterate=True), token=TOKEN, updates=FakeUpdates(), version="1.6.0")
    return state, TestClient(create_app(state), headers={TOKEN_HEADER: TOKEN})


def test_the_routes_read_save_and_replace_only_while_the_tools_are_on(tmp_path):
    state, client = window(tmp_path)
    a = document(tmp_path / "a.docx")
    assert client.post("/api/attach", json={"paths": [str(a)], "template": False}).status_code == 200
    body = {"path": str(a)}
    for route, extra in (("/api/parts", {}), ("/api/part", {"part": DOC}), ("/api/parts/search", {"query": "x"}),
                         ("/api/part/save", {"part": DOC, "folder": str(tmp_path)})):
        answer = client.post(route, json={**body, **extra})
        assert answer.status_code == 400 and answer.json()["error"] == HANDS_ON_NEEDED, route
    refused = client.post("/api/part/replace", data={"path": str(a), "part": DOC}, files={"file": ("x.xml", b"<a/>")})
    assert refused.status_code == 400 and refused.json()["error"] == HANDS_ON_NEEDED
    assert client.post("/api/hands-on", json={"on": True}).status_code == 400      # Default has no such switch
    # the warning's toggle rides the mode's own request; the switch works while the mode is on
    view = client.post("/api/mode", json={"mode": "obliterate", "acknowledged": True}).json()
    assert view["mode"] == "obliterate" and view["hands_on"] is False
    assert client.post("/api/hands-on", json={"on": True}).json()["hands_on"] is True
    assert client.post("/api/mode", json={"mode": "default"}).json()["hands_on"] is False
    view = client.post("/api/mode", json={"mode": "obliterate", "acknowledged": True, "hands_on": True}).json()
    assert view["hands_on"] is True and view["messages"][-1]["text"] == HANDS_ON_ON
    listing = client.post("/api/parts", json=body).json()
    assert listing["document"] == "a.docx" and DOC in [p["name"] for p in listing["parts"]]
    page = client.post("/api/part", json={**body, "part": DOC, "page": 1}).json()
    assert page["part"] == DOC and page["lines"] and page["xml"] is True
    assert client.post("/api/part", json={**body, "part": "word/nothing.xml"}).status_code == 400
    found = client.post("/api/parts/search", json={**body, "query": "technical wording"}).json()
    assert [m["part"] for m in found["matches"]] == [DOC]
    # Save this part: a folder in the body, else the native dialog (this window has none)
    assert client.post("/api/part/save", json={**body, "part": DOC}).status_code == 501
    mine = tmp_path / "mine"
    mine.mkdir()
    saved = client.post("/api/part/save", json={**body, "part": DOC, "folder": str(mine)})
    assert saved.status_code == 200 and (mine / "a - word_document.xml").read_bytes() == member(a.read_bytes(), DOC)
    assert client.post("/api/part/save", json={**body, "part": DOC, "folder": str(tmp_path / "out")}).status_code == 400
    # Replace this part: the file's bytes arrive with the request, the job previews, nothing is created yet
    new = member(a.read_bytes(), DOC).replace(KEEP, KEEP + b" Reviewed.")
    with client.stream("POST", "/api/part/replace", data={"path": str(a), "part": DOC},
                       files={"file": ("a - word_document.xml", new)}) as response:
        assert response.status_code == 200
        events = frames(response)
    assert events[-1]["type"] == "done" and events[-1]["state"]["stage"] == "preview"
    view = events[-1]["state"]
    [sample] = view["samples"]
    assert sample["raw_xml"] and sample["raw_what"] == "part" and sample["passed"]
    kept = list((tmp_path / "out" / "parts").rglob("a - word_document.xml"))
    assert len(kept) == 1 and kept[0].read_bytes() == new           # in the scratch folder, gone with the window
    assert state.task.results == {}
    with client.stream("POST", "/api/primary", json={"token": view["action"]["token"]}) as response:
        done = frames(response)[-1]["state"]
    assert done["stage"] == "done" and [r["name"] for r in done["results"]] == ["a.docx"]
    assert member(Path(state.task.results[str(a)]).read_bytes(), DOC) == new
    # a file larger than a part may be is refused before a byte of it is kept
    import spec_surgeon.ops.replace_part as module
    limit, module.MAX_PART_BYTES = module.MAX_PART_BYTES, 8
    try:
        big = client.post("/api/part/replace", data={"path": str(a), "part": DOC},
                          files={"file": ("big.xml", b"<a>123456789</a>")})
    finally:
        module.MAX_PART_BYTES = limit
    assert big.status_code == 400 and "at most" in big.json()["error"]
    assert not list((tmp_path / "out" / "parts").rglob("big.xml"))


def test_obliterate_this_one_carries_the_toggle_too(tmp_path):
    state, client = window(tmp_path)
    a = document(tmp_path / "a.docx")
    client.post("/api/attach", json={"paths": [str(a)], "template": False})
    owe(state.task, a, "", reason="The plan could not be applied to it.")
    view = client.post("/api/todo/obliterate", json={"path": str(a), "acknowledged": True, "hands_on": True}).json()
    assert view["mode"] == "obliterate" and view["hands_on"] is True and view["focus"] == [str(a)]
    assert client.post("/api/parts", json={"path": str(a)}).status_code == 200
