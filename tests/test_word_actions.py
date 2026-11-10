"""Obliterate's named Word actions (docs/BACKLOG.md O5; CLAUDE.md, Modes): a fixed menu of actions that Word itself
runs on a copy, picked by the user in the window or named by Claude in a plan.

Word saves the whole file again, so nothing is predicted: every document's copy is made before approval, read by the
verifier's own report of a file Word saved (``verify.run_difference(rewritten=True)``: parts paired by their place in
the document, Word's own save marks set aside), shown, and delivered as shown — marked UNPROVEN, never verified.

No test machine has Word. ``fake_word.FakeWord`` stands behind the one seam (``word_actions.run_word_actions``) and
saves the whole file again as Word does; ``FakeDocument`` below stands in for Word's object model so the code that
calls Word's commands (``render/word_actions.py``, ``WordSession.run_actions``) runs at all. What Word does with a
real document is the owner's real-file run.

Pinned here: the menu is fixed and every action has its code; the report of a Word-saved copy says nothing changed
when nothing did and lists what did, by content; the gate takes a plan of Word actions alone, in Obliterate alone;
the copies are kept from before the approval and Word is never run again at Create; a document Word cannot finish
gets no copy and the reason; the window's menu makes no AI call; a pass over the to-do list goes on after Word; and
Safe and Default are told nothing of any of it.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from fake_claude import FakeClient, resp, tool
from fake_word import FakeWord, save_again
from make_fixtures import build, build_rich
from spec_surgeon import catalog, codegen, compiler, saved_recipes, word_actions
from spec_surgeon.compiler import _Batch, compose_submission
from spec_surgeon.document_task import (WORD_ONLY_THERE, WORD_THEN_SAME, DocumentTask)
from spec_surgeon.gui import Session
from spec_surgeon.ops import OPS, JobException, get_op
from spec_surgeon.package import DocxPackage
from spec_surgeon.preview import (WORD_ACCOUNT_HEAD, WORD_OTHER_CHANGES_HEAD, WORD_WORDS_CHANGE_HEAD, text_change_phrase,
                                  text_changes_of)
from spec_surgeon.render import word_actions as commands
from spec_surgeon.render.word_com import WordSession
from spec_surgeon.report import FAILURE_EXPLANATIONS
from spec_surgeon.settings import Settings
from spec_surgeon.ui_feedback import UNPROVEN_MARK, unproven_only
from spec_surgeon.verify import difference_lines, run_difference
from spec_surgeon.verify.difference import SAVED_AGAIN
from spec_surgeon.web.server import TOKEN_HEADER, WindowState, create_app
from test_document_task import SPEC, document
from test_web_server import TOKEN, FakeUpdates, frames

REQUEST = "Make the FIRE SUPPRESSION title bold."


@pytest.fixture(scope="module")
def plain() -> bytes:
    return build("21 00 00", "SPEC 0")          # a field in the body, PAGE fields in its footers, one tracked change


@pytest.fixture(scope="module")
def rich() -> bytes:
    return build_rich()                         # four headers (first, default, even), pictures in two, tracked changes


@pytest.fixture
def word(monkeypatch) -> FakeWord:
    fake = FakeWord()
    monkeypatch.setattr(word_actions, "run_word_actions", fake)
    monkeypatch.setattr(word_actions, "available", lambda fresh=False: True)
    return fake


def member(data: bytes, name: str) -> bytes:
    return zipfile.ZipFile(io.BytesIO(data)).read(name)


def statuses(rec) -> Dict[str, str]:
    return {c.id: c.status for c in rec.checks}


# ---- the menu --------------------------------------------------------------------------------------------------------

def test_the_menu_is_fixed_and_every_action_has_its_code():
    assert set(word_actions.ACTIONS) == set(commands.COMMANDS)
    assert word_actions.OFFERED == ("repair", "remove_protection", "accept_changes", "reject_changes",
                                    "update_fields", "unlink_fields", "numbers_to_text", "upgrade_compatibility",
                                    "textboxes_to_text", "delete_shapes")
    assert "convert" in word_actions.ACTIONS and "convert" not in word_actions.OFFERED   # the worker's alone, for now
    assert [a["id"] for a in word_actions.menu()] == list(word_actions.OFFERED)
    assert all(a["label"] and a["does"].startswith("Word ") for a in word_actions.menu())
    # the operation a plan names an action by takes exactly the menu's ids
    assert OPS["word_action"].params_schema["properties"]["action"]["enum"] == list(word_actions.OFFERED)
    assert OPS["word_action"].obliterate_only and not OPS["word_action"].hands_on


def test_a_choice_of_actions_is_checked_in_plain_words():
    assert word_actions.check(["repair", "unlink_fields"]) == ["repair", "unlink_fields"]
    for bad, said in (([], "at least one"), (["macro"], "not a Word action on the menu"), (["convert"], "not a Word"),
                      (["unlink_fields", "unlink_fields"], "listed twice"),
                      (["unlink_fields", "repair"], "comes first"),
                      (["accept_changes", "reject_changes"], "cannot both run"), ("unlink_fields", "at least one")):
        with pytest.raises(ValueError, match=said):
            word_actions.check(bad)
    assert word_actions.check(["convert"], offered_only=False) == ["convert"]


def test_words_own_account_is_said_in_the_actions_words():
    said = word_actions.account([{"action": "unlink_fields", "count": 1, "note": ""},
                                 {"action": "accept_changes", "count": 1234, "note": ""},
                                 {"action": "delete_shapes", "count": 0, "note": ""},
                                 {"action": "textboxes_to_text", "count": 2, "note": "1 left as it was: locked"},
                                 {"action": "repair", "count": 0, "note": ""}])
    assert said == ["1 field replaced with its text", "1,234 tracked changes accepted",
                    "Delete drawn shapes: nothing to do", "2 text boxes turned into plain text (1 left as it was: "
                    "locked)", "opened with Open and Repair and saved again"]
    assert word_actions.did_nothing(["unlink_fields"], [{"action": "unlink_fields", "count": 0}])
    assert not word_actions.did_nothing(["unlink_fields", "repair"], [{"action": "unlink_fields", "count": 0}])


# ---- the report of a copy Word saved ---------------------------------------------------------------------------------

def test_a_save_that_changes_nothing_reads_as_nothing_changed(rich):
    """Word wrote every part again, renamed the headers and gave every relationship id again: by content, nothing
    differs. Read as any other copy's (by part name and by bytes) the same file looks changed everywhere."""
    out, _ = save_again(rich, ["repair"])
    names = zipfile.ZipFile(io.BytesIO(rich)).namelist()
    assert all(member(out, n) != member(rich, n) for n in ("word/document.xml", "word/styles.xml",
                                                          "word/settings.xml", "word/_rels/document.xml.rels"))
    assert zipfile.ZipFile(io.BytesIO(out)).namelist() != names
    rec = run_difference(rich, out, "a.docx", "copy.docx", rewritten=True)
    assert rec.passed and rec.job == "Word actions (Obliterate)"
    assert statuses(rec)["V1"] == "PASS" and all(statuses(rec)[k] == "NA" for k in ("V2", "V4", "V5a", "V5c", "V10"))
    assert rec.get("V2").data["actual"] == [] and text_changes_of(rec.to_dict()) == []
    lines = difference_lines(rec)
    assert lines[0] == SAVED_AGAIN and lines[1].startswith("Nothing this report counts differs")
    assert not any(line.startswith(("Relationships", "Parts added", "Parts removed", "Styles", "Document settings",
                                    "Bookmarks", "In ")) for line in lines)
    d1 = rec.get("D1").data
    assert d1["counts"] == {} and d1["formatting_only"] == {} and d1["page_setup"] == 0 and d1["styles"] == {
        "added": [], "removed": [], "changed": []} and d1["relationships"] == {"added": [], "removed": []}
    # the same copy, read as a custom method's: its words 'move' between the renamed headers, and checks fail
    other = run_difference(rich, out, "a.docx", "copy.docx")
    assert text_changes_of(other.to_dict()) and not other.passed


def test_what_word_changed_is_listed_by_content(plain, rich):
    out, report = save_again(rich, ["accept_changes"])
    rec = run_difference(rich, out, "a.docx", "copy.docx", rewritten=True)
    assert rec.passed and report == [{"action": "accept_changes", "count": 4, "note": ""}]
    words = {(w["part"], tuple(w["removed"]), tuple(w["added"])) for w in text_changes_of(rec.to_dict())}
    # each entry carries the ORIGINAL's name for its part, whatever Word named the part in the copy
    assert words == {("word/document.xml", ("black steel",), ("hot-dip galvanized",)),
                     ("word/header2.xml", ("AVC11ISSUED",), ("AVC11REV A ISSUED",))}
    # the copy carries no tracked change any more: these were the original's, settled — never "in the copy's"
    assert all(w.get("settled") and not w.get("tracked") for w in text_changes_of(rec.to_dict()))
    assert text_change_phrase(text_changes_of(rec.to_dict())[0]).endswith(
        "(a tracked change of the original, settled in the copy)")
    assert rec.get("V2").data["views"]["after"] == []
    assert any(line.startswith("Tracked changes: 4 before, 0 after") for line in difference_lines(rec))
    assert rec.get("V4").data == {"counts_before": {"ins": 2, "del": 1, "rPrChange": 1}, "counts_after": {}}
    out, report = save_again(plain, ["unlink_fields"])
    rec = run_difference(plain, out, "a.docx", "copy.docx", rewritten=True)
    assert report[0]["count"] == 1 and "Fields: 3 before, 2 after." in difference_lines(rec)   # the footers' stay
    [gone] = text_changes_of(rec.to_dict())
    assert gone["removed"] == ["REF _Ref123 \\h"] and gone["added"] == [] and gone["where"].startswith("body")


def test_words_a_save_lost_are_listed_and_a_copy_that_does_not_open_fails(plain):
    """Nothing is predicted, so nothing can be held against a prediction: a word that went is LISTED for the user,
    and what makes the file one Word would repair or refuse is still a failed check."""
    out, _ = save_again(plain, ["repair"])
    lost = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(out)) as z, zipfile.ZipFile(lost, "w") as target:
        for name in z.namelist():
            data = z.read(name)
            if name == "word/document.xml":
                assert b"FM Approved" in data
                data = data.replace(b"FM Approved", b"")
            target.writestr(name, data)
    rec = run_difference(plain, lost.getvalue(), "a.docx", "copy.docx", rewritten=True)
    assert rec.passed and any("FM Approved" in " ".join(w["removed"]) for w in text_changes_of(rec.to_dict()))
    broken = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(out)) as z, zipfile.ZipFile(broken, "w") as target:
        for name in z.namelist():
            if name != "word/footer1.xml":                     # a footer the body still refers to
                target.writestr(name, z.read(name))
    rec = run_difference(plain, broken.getvalue(), "a.docx", "copy.docx", rewritten=True)
    assert not rec.passed and statuses(rec)["V1"] == "FAIL"


# ---- Word's own commands, on a stand-in for its object model ---------------------------------------------------------

class Collection:
    def __init__(self, items):
        self.items = list(items)

    @property
    def Count(self):
        return len(self.items)

    def Item(self, i):
        return self.items[i - 1]

    def __iter__(self):
        return iter(self.items)


class Fields(Collection):
    def Unlink(self):
        self.items = []


class Field:
    def __init__(self, code):
        self.Code = type("Code", (), {"Text": code})()
        self.updated = 0

    def Update(self):
        self.updated += 1


class Story:
    def __init__(self, kind, codes=(), following=None):
        self.StoryType, self.Fields, self.NextStoryRange = kind, Fields(Field(c) for c in codes), following


class Shape:
    def __init__(self, owner, kind, text=None, fails=False):
        self.owner, self.Type, self.text, self.fails = owner, kind, text, fails
        self.TextFrame = type("Frame", (), {"HasText": text is not None, "TextRange": type(
            "Range", (), {"FormattedText": text})()})()
        log = owner.log

        class At:
            Text = ""

            def Collapse(self, direction):
                log.append(("collapse", direction))

            def InsertParagraphAfter(self):
                log.append("paragraph after")

            def __setattr__(self, name, value):
                if name == "FormattedText":
                    log.append(("placed", value))
                object.__setattr__(self, name, value)
        self.Anchor = type("Anchor", (), {"Paragraphs": staticmethod(lambda i: type("P", (), {"Range": At()})())})()

    def Delete(self):
        if self.fails:
            raise RuntimeError("this shape is locked")
        self.owner.items.remove(self)


class Shapes(Collection):
    def __init__(self, log, specs):
        self.log = log
        super().__init__([])
        self.items = [Shape(self, *spec) for spec in specs]


class FakeDocument:
    """What ``render/word_actions.py`` asks of a Word document, and a log of what was done to it."""

    def __init__(self, protected=False, password=False):
        self.log: List[Any] = []
        self.ProtectionType = 2 if protected else -1
        self.password = password
        self.ReadOnlyRecommended, self.Final = True, False
        self.TrackRevisions = True
        self.Revisions = Collection(range(3))
        self.ListParagraphs = Collection(range(5))
        self.CompatibilityMode = 12
        header = Story(7, ["PAGE"])
        self.StoryRanges = [Story(1, ["REF a", "TOC \\o", "ASK who", "INCLUDETEXT x.docx"], Story(5, ["DATE"])),
                            Story(2, ["NOTEREF b"]), header]
        self.header = header
        self.Shapes = Shapes(self.log, [(17, "Box one"), (13, None), (1, None), (17, "Locked", True)])
        in_headers = Shapes(self.log, [(17, "Header box"), (3, None)])
        self.Sections = lambda i: type("S", (), {"Headers": staticmethod(lambda j: type("H", (), {
            "Shapes": in_headers})())})()
        self.in_headers = in_headers
        self.saved, self.closed = [], []

    def Unprotect(self):
        if self.password:
            raise RuntimeError("The password is incorrect.")
        self.ProtectionType = -1

    def AcceptAllRevisions(self):
        self.Revisions = Collection([])

    def RejectAllRevisions(self):
        self.Revisions = Collection([])

    def ConvertNumbersToText(self):
        self.ListParagraphs = Collection([])

    def Convert(self):
        self.CompatibilityMode = 15

    def SaveAs2(self, path, FileFormat=None, AddToRecentFiles=None):
        self.saved.append((Path(path).name, FileFormat, self.TrackRevisions))
        Path(path).write_bytes(b"saved")

    def Close(self, SaveChanges=None):
        self.closed.append(SaveChanges)


def session_over(doc, tmp_path, opened: List[Dict[str, Any]]):
    session = WordSession.__new__(WordSession)
    session.tmp, session.stage = tmp_path, "word_start"
    options = type("Options", (), {"UpdateLinksAtOpen": True})()

    def open_(path, **kwargs):
        opened.append({"path": Path(path), "links": options.UpdateLinksAtOpen, **kwargs})
        return doc
    session.app = type("App", (), {"Options": options, "Documents": type("Docs", (), {"Open": staticmethod(open_)})()})()
    return session, options


def test_every_command_calls_word_as_the_menu_says(tmp_path, plain):
    source = tmp_path / "in" / "a.docx"
    source.parent.mkdir()
    source.write_bytes(plain)
    doc, opened = FakeDocument(protected=True), []
    session, options = session_over(doc, tmp_path, opened)
    out = tmp_path / "edited.docx"
    report = session.run_actions(source, out, [a for a in word_actions.OFFERED if a != "reject_changes"])
    # opened once, a private copy (never the file given), with Open and Repair, links not updated — and the user's
    # own option put back
    [how] = opened
    assert how["path"] != source and how["path"].read_bytes() == plain
    assert how["OpenAndRepair"] is True and how["ReadOnly"] is False and how["links"] is False
    assert options.UpdateLinksAtOpen is True
    counts = {entry["action"]: entry["count"] for entry in report}
    assert counts == {"repair": 1, "remove_protection": 2, "accept_changes": 3, "update_fields": 5,
                      "unlink_fields": 6, "numbers_to_text": 5, "upgrade_compatibility": 1, "textboxes_to_text": 2,
                      "delete_shapes": 1}
    notes = {entry["action"]: entry["note"] for entry in report if entry["note"]}
    # a field that reads another file or asks a question is never updated; a locked shape is counted, with why
    assert notes["update_fields"].startswith("2 left as they were")
    assert "1 left as it was" in notes["textboxes_to_text"] and "locked" in notes["textboxes_to_text"]
    # the header's fields stay live; the picture and the chart stay; the boxes' text was placed before they went
    assert doc.header.Fields.Count == 1 and doc.ProtectionType == -1 and doc.ReadOnlyRecommended is False
    assert [s.Type for s in doc.Shapes.items] == [13, 17] and [s.Type for s in doc.in_headers.items] == [3]
    assert ("placed", "Box one") in doc.log and ("placed", "Header box") in doc.log
    # saved once, as a .docx, with the document's own tracking put back; closed without saving the opened copy
    assert doc.saved == [("edited.docx", 12, True)] and doc.closed == [0] and out.read_bytes() == b"saved"


def test_an_action_word_cannot_do_stops_the_run_with_nothing_saved(tmp_path, plain):
    source = tmp_path / "a.docx"
    source.write_bytes(plain)
    doc, opened = FakeDocument(protected=True, password=True), []
    session, _ = session_over(doc, tmp_path / "scratch", opened)
    (tmp_path / "scratch").mkdir()
    with pytest.raises(RuntimeError, match="password is incorrect"):
        session.run_actions(source, tmp_path / "edited.docx", ["remove_protection", "unlink_fields"])
    assert session.failure_stage == "word_action_remove_protection"
    assert doc.saved == [] and doc.closed == [0] and not (tmp_path / "edited.docx").exists()
    with pytest.raises(ValueError, match="unknown Word action"):
        session.run_actions(source, tmp_path / "edited.docx", ["macro"])
    # the window's words for it: Word's own message, the action, and what can remove a password's restriction
    error = RuntimeError("The password is incorrect.")
    error.code, error.reason, error.details = "word-com", "The password is incorrect.", {
        "stage": "word_action_remove_protection"}
    said = word_actions.failure(error, ["remove_protection"])
    assert said.startswith("Word could not finish in “Remove protection” [word-com]: The password is incorrect.")
    assert "raw XML edit" in said


# ---- one document through Word and the verifier ----------------------------------------------------------------------

def test_a_copy_is_made_on_a_private_copy_read_by_the_verifier_and_never_called_verified(tmp_path, plain, word,
                                                                                         monkeypatch):
    f = tmp_path / "Spec.docx"
    f.write_bytes(plain)
    seen = []
    real = word_actions.run_word_actions
    monkeypatch.setattr(word_actions, "run_word_actions",
                        lambda source, actions, output, timeout=None: (seen.append(Path(source)),
                                                                       real(source, actions, output))[1])
    t = word_actions.trial(["unlink_fields"], f, tmp_path / "work")
    assert t.passed and t.unproven and t.unverified == [] and t.problems == []
    assert seen and seen[0] != f.resolve() and tmp_path / "work" in seen[0].parents      # never the user's file
    assert f.read_bytes() == plain
    assert t.record.passed and t.record.mode == "shown" and t.notes == ["1 field replaced with its text"]
    assert any("Word's own account" in note and "not the verifier's" in note for note in t.record.notes)
    assert t.plan["geometry_reference"] == "none" and t.plan["property_assertions"] == []


def test_a_document_word_cannot_finish_or_has_nothing_to_do_in_gets_no_copy(tmp_path, plain, monkeypatch):
    f = tmp_path / "Spec.docx"
    f.write_bytes(plain)
    error = RuntimeError("Word exceeded 300 seconds for Spec.docx")
    error.code, error.details = "word-timeout", {}
    monkeypatch.setattr(word_actions, "run_word_actions", FakeWord(fail={"Spec.docx": error}))
    t = word_actions.trial(["unlink_fields"], f, tmp_path / "w1")
    assert not t.passed and t.output is None and t.record is None
    assert t.problems == ["Word could not finish [word-timeout]: Word exceeded 300 seconds for Spec.docx."]
    monkeypatch.setattr(word_actions, "run_word_actions", FakeWord(nothing={"Spec.docx"}))
    t = word_actions.trial(["delete_shapes"], f, tmp_path / "w2")
    assert not t.passed and t.problems == ["Word found nothing to do in it: Delete drawn shapes: nothing to do."]
    # Open and Repair saves the file again whatever Word counts: that is the change
    t = word_actions.trial(["repair"], f, tmp_path / "w3")
    assert t.passed and t.notes == ["opened with Open and Repair and saved again"]
    # a copy the verifier cannot open at all is no copy
    monkeypatch.setattr(word_actions, "run_word_actions",
                        lambda source, actions, output, timeout=None: Path(output).write_bytes(b"not a zip") and [])
    t = word_actions.trial(["repair"], f, tmp_path / "w4")
    assert not t.passed and "could not read the copy Word saved" in t.problems[0]


# ---- the gate --------------------------------------------------------------------------------------------------------

def word_spec(*actions: str, **more) -> Dict[str, Any]:
    return {"request": "x", "summary": "Word runs named actions.", "mode": "untracked",
            "ops": [{"op": "word_action", "params": {"action": a}} for a in actions], **more}


@pytest.fixture
def batch(tmp_path, plain):
    f = tmp_path / "Spec.docx"
    f.write_bytes(plain)
    return _Batch([f], None)


def test_the_gate_takes_a_plan_of_word_actions_alone_in_obliterate_alone(batch, plain, word, monkeypatch):
    spec = word_spec("unlink_fields", "accept_changes")
    assert compose_submission(spec, batch, ["x"], unguarded=True) == (spec, [])
    # Safe and Default: refused before anything is planned, whoever built the plan
    _, errors = compose_submission(spec, batch, ["x"])
    assert len(errors) == 1 and errors[0].startswith("[obliterate-only]") and "word_action" in errors[0]
    font = {"op": "set_font", "select": {"scope": "all"}, "params": {"font": "Arial"}}
    for mixed in ({**spec, "ops": spec["ops"] + [font]},
                  {"summary": "s", "stages": [{"name": "word", "spec": spec},
                                              {"name": "font", "spec": {**spec, "ops": [font]}}]},
                  {"summary": "s", "stages": [{"name": "word", "spec": spec}]},
                  {"summary": "s", "layouts": [{"name": "all", "patterns": ["x"], "spec": spec}]}):
        _, errors = compose_submission(mixed, batch, ["x"], unguarded=True)
        assert len(errors) == 1 and errors[0].startswith("[word-alone]") and "plan of their own" in errors[0]
    for bad, said in (({**spec, "ops": [{**spec["ops"][0], "select": {"scope": "all"}}]}, "no select"),
                      (word_spec("unlink_fields", "repair"), "comes first"),
                      (word_spec("accept_changes", "reject_changes"), "cannot both run"),
                      (word_spec("unlink_fields", "unlink_fields"), "listed twice"),
                      (word_spec("unlink_fields", bindings=[{"file": "a", "md5": "b"}]), "no bindings")):
        _, errors = compose_submission(bad, batch, ["x"], unguarded=True)
        assert len(errors) == 1 and errors[0].startswith("[word-alone]") and said in errors[0], errors
    _, errors = compose_submission(word_spec("convert"), batch, ["x"], unguarded=True)
    assert errors and "convert" in errors[0]                   # the worker's alone: not on the menu, not in a plan
    # no Word here: said to the model, with the ways around it
    monkeypatch.setattr(word_actions, "available", lambda fresh=False: False)
    _, errors = compose_submission(spec, batch, ["x"], unguarded=True)
    assert errors[0].startswith("[word-missing]") and "not installed" in errors[0] and "raw XML edit" in errors[0]
    # the engine itself never plans or applies one
    for call in (lambda: get_op("word_action").plan(DocxPackage(plain), {}, {"action": "repair"}),
                 lambda: get_op("word_action").apply(None, None)):
        with pytest.raises(JobException) as refused:
            call()
        assert refused.value.code == "word-alone"
    assert {"word-alone", "word-missing"} <= set(FAILURE_EXPLANATIONS)


def test_safe_and_default_are_told_nothing_of_it():
    assert "word_action" not in catalog.offered_ops(False) and "word_action" in catalog.offered_ops(True)
    for unguarded, there in ((False, False), (True, True)):
        prompt = compiler.system_prompt(True, unguarded)
        assert ("word_action" in prompt) is there and ("WORD ACTIONS" in prompt) is there
        assert ("word_action" in json.dumps(catalog.op_catalog(unguarded=unguarded))) is there
        assert ("word_action" in json.dumps(catalog.editspec_json_schema(unguarded))) is there
    assert "word_action" not in codegen.system_prompt() and "word_action" not in codegen.system_prompt(unguarded=True)
    assert "word_action" not in json.dumps(compiler.CHAT_TOOLS)
    assert saved_recipes.portable_plan(word_spec("repair")) == (None, saved_recipes.WHY_WORD)


# ---- the window ------------------------------------------------------------------------------------------------------

def task_for(tmp_path, fake=None, **kwargs):
    kwargs.setdefault("obliterate_allowed", True)
    return DocumentTask(Session(client=fake or FakeClient(), out_root=tmp_path / "out", visual_check=False),
                        render=False, **kwargs)


def said(task) -> List[str]:
    return [m["text"] for m in task.messages if m["role"] == "app"]


def obliterate_task(tmp_path, data: bytes, *names, fake=None):
    docs = []
    for name in names or ("a.docx", "b.docx"):
        path = tmp_path / name
        path.write_bytes(data)
        docs.append(path)
    task = task_for(tmp_path, fake)
    task.attach(docs)
    task.set_mode("obliterate", acknowledged=True)
    return task, docs


def test_the_menu_runs_word_on_one_document_with_no_ai_and_create_delivers_the_copy_shown(tmp_path, plain, word):
    fake = FakeClient()
    task, (a, b) = obliterate_task(tmp_path, plain, fake=fake)
    assert task.view()["word"]["available"] is True and len(task.view()["word"]["actions"]) == 10
    view = task.perform("word_actions", path=str(a), actions=["unlink_fields"])
    assert view["stage"] == "preview" and fake.requests == []
    assert task.session.outcome_source == "word" and task.session.model_free
    assert task.decisions == ["Run in Word on a.docx: Unlink fields."]
    assert word.calls == [("a.docx", ["unlink_fields"])]
    [change] = view["groups"][0]["changes"]
    assert change["title"] == "Unlink fields" and change["summary"].startswith("Word action, Unlink fields: Word replaces")
    [sample] = task._samples_view()["samples"]
    assert sample["name"] == "a.docx" and sample["unproven"] and sample["made_by"] == "word"
    assert sample["word_account"] == ["1 field replaced with its text"]
    assert [(c["removed"], c["added"]) for c in sample["text_changes"]] == [(["REF _Ref123 \\h"], [])]
    assert sample["changes"][0] == SAVED_AGAIN and "Fields: 3 before, 2 after." in sample["changes"]
    assert task.session.left_out_reasons == {str(b.resolve()): WORD_ONLY_THERE.format(name="a.docx")}
    assert view["copies_to_create"] == 1
    told = said(task)
    assert any(t.startswith("Planned without AI. Word action, Unlink fields:") for t in told)
    assert codegen.WORD_UNPROVEN in told
    assert any(t.startswith(WORD_WORDS_CHANGE_HEAD) and "a.docx, body paragraph 17: words removed" in t for t in told)
    assert any(t.startswith(WORD_OTHER_CHANGES_HEAD) for t in told)
    assert any(t.startswith(WORD_ACCOUNT_HEAD) and "a.docx: 1 field replaced with its text." in t for t in told)
    shown = Path(task.session.generated.trials[0].output).read_bytes()
    view = task.perform("primary", token=view["token"])
    # Create ran no Word and no AI: the copy delivered is the copy shown, byte for byte; the original is untouched
    assert word.calls == [("a.docx", ["unlink_fields"])] and fake.requests == []
    assert task.stage == "done" and list(task.results) == [str(a)] and a.read_bytes() == plain
    assert Path(task.results[str(a)]).read_bytes() == shown
    assert task.unverified == {str(a): [word_actions.UNPROVEN_MARK]} and unproven_only(task.unverified[str(a)])
    assert UNPROVEN_MARK not in json.dumps(task.view(), default=str)        # the mark says who made it: Word
    assert any("1 is UNPROVEN: Word made it" in t for t in said(task))
    done = view["working"]["done"][-1]
    assert done["documents"] == 1 and done["why_not"] == saved_recipes.WHY_WORD
    assert task.applied[str(a)] == [change["summary"]]
    assert any("UNPROVEN (Obliterate mode): Word's named actions made its copy" in line
               for line in task._history_in_scope())
    record = json.loads(next((Path(task.session.last_run_dir) / "records").glob("*.record.json")).read_text())
    assert record["job"] == "Word actions (Obliterate)" and record["mode"] == "shown"
    task.undo()
    assert task.results == {} and str(a) not in task.working


def test_every_document_can_get_the_actions_and_one_word_cannot_finish_gets_no_copy(tmp_path, plain, monkeypatch):
    error = RuntimeError("Word stopped")
    error.code, error.details = "word-com", {"stage": "word_action_accept_changes"}
    fake_word = FakeWord(fail={"b.docx": error})
    monkeypatch.setattr(word_actions, "run_word_actions", fake_word)
    monkeypatch.setattr(word_actions, "available", lambda fresh=False: True)
    task, (a, b) = obliterate_task(tmp_path, plain)
    view = task.perform("word_actions", path=str(a), actions=["accept_changes", "unlink_fields"], every=True)
    assert view["stage"] == "preview" and view["copies_to_create"] == 1
    assert [c[0] for c in fake_word.calls] == ["a.docx", "b.docx"] and task.session.left_out == []
    assert task.decisions == ["Run in Word on every document (2): Accept all tracked changes; then Unlink fields."]
    assert [c["title"] for c in view["groups"][0]["changes"]] == ["Accept all tracked changes", "Unlink fields"]
    by_name = {s["name"]: s for s in task._samples_view()["samples"]}
    assert by_name["a.docx"]["unproven"] and by_name["b.docx"]["no_copy"]
    assert any("b.docx: Word could not finish in “Accept all tracked changes” [word-com]: Word stopped." in t
               for t in said(task))
    task.perform("primary", token=view["token"])
    assert list(task.results) == [str(a)] and str(b) in task.todo
    assert "Word could not finish" in task.todo[str(b)]["reason"]
    assert len(fake_word.calls) == 2                                           # nothing ran again at Create


def test_when_word_makes_no_copy_nothing_is_previewed_and_the_reason_is_said(tmp_path, plain, monkeypatch):
    monkeypatch.setattr(word_actions, "available", lambda fresh=False: True)
    monkeypatch.setattr(word_actions, "run_word_actions", FakeWord(nothing={"a.docx"}))
    task, (a, b) = obliterate_task(tmp_path, plain)
    view = task.perform("word_actions", path=str(a), actions=["delete_shapes"])
    assert view["stage"] == "empty" and task.session.generated is None and task.session.spec is None
    assert task.decisions == [] and view["samples"] == []
    assert any(t.startswith("Word made no copy, so there is nothing to preview. a.docx: Word found nothing to do in it")
               for t in said(task))
    # a choice that cannot be used is said once and nothing runs
    before = len(task.messages)
    for actions, why in ((["unlink_fields", "repair"], "comes first"), ([], "at least one")):
        task.perform("word_actions", path=str(a), actions=actions)
        assert why in said(task)[-1] and said(task)[-1].startswith("Word actions: ")
    assert len(task.messages) == before + 2
    # no Word on this computer: said, nothing planned
    monkeypatch.setattr(word_actions, "available", lambda fresh=False: False)
    task.perform("word_actions", path=str(a), actions=["repair"])
    assert said(task)[-1] == "Word actions: " + word_actions.NO_WORD
    assert task.view()["word"] == {"available": False, "why": word_actions.NO_WORD,
                                   "actions": word_actions.menu(), "minutes": 5}


def test_the_menu_is_obliterates_alone_and_goes_with_the_mode(tmp_path, plain, word):
    task, (a, b) = obliterate_task(tmp_path, plain)
    view = task.perform("word_actions", path=str(a), actions=["repair"])
    assert view["stage"] == "preview"
    task.set_mode("default")                                    # leaving the mode voids what it planned
    assert task.session.generated is None and task.stage != "preview" and task.view()["word"] is None
    with pytest.raises(ValueError, match="Obliterate mode's"):
        task.perform("word_actions", path=str(a), actions=["repair"])
    task.set_mode("safe")
    assert task.view()["word"] is None
    with pytest.raises(ValueError, match="Obliterate mode's"):
        task.perform("word_actions", path=str(a), actions=["repair"])


def test_a_copy_that_changed_after_it_was_shown_is_not_what_was_approved(tmp_path, plain, word):
    task, (a, b) = obliterate_task(tmp_path, plain)
    view = task.perform("word_actions", path=str(a), actions=["unlink_fields"])
    kept = Path(task.session.generated.trials[0].output)
    kept.write_bytes(kept.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="a copy you were shown changed"):
        task.perform("primary", token=view["token"])
    assert task.results == {} and len(word.calls) == 1


def test_claude_plans_word_actions_as_a_plan_of_their_own(tmp_path, plain, word):
    spec = word_spec("accept_changes")
    spec["request"] = REQUEST
    fake = FakeClient(resp(tool("submit_editspec", {"spec": spec, "message": "Word will accept every tracked change."})))
    task, (a, b) = obliterate_task(tmp_path, plain, fake=fake)
    view = task.perform("send", text="Accept every tracked change with Word itself.")
    assert view["stage"] == "preview" and len(fake.requests) == 1
    assert task.session.outcome_source == "compiled" and not task.session.model_free
    assert [c[0] for c in word.calls] == ["a.docx", "b.docx"]
    assert all(s["made_by"] == "word" and s["unproven"] for s in task._samples_view()["samples"])
    notes = "\n".join(task.session.conversation.notes)
    assert codegen.WORD_UNPROVEN in notes and WORD_WORDS_CHANGE_HEAD in notes
    task.perform("primary", token=view["token"])
    assert sorted(task.results) == [str(a), str(b)] and len(word.calls) == 2
    assert all(marks == [word_actions.UNPROVEN_MARK] for marks in task.unverified.values())


def test_claude_is_refused_a_word_action_beside_anything_else_and_in_default_mode(tmp_path, plain, word):
    font = {"op": "set_font", "select": {"scope": "all"}, "params": {"font": "Arial"}}
    mixed = {**word_spec("accept_changes"), "ops": word_spec("accept_changes")["ops"] + [font]}
    fake = FakeClient(resp(tool("submit_editspec", {"spec": mixed})),
                      resp(tool("submit_editspec", {"spec": word_spec("accept_changes")})))
    task, _docs = obliterate_task(tmp_path, plain, fake=fake)
    view = task.perform("send", text="Accept the changes and set Arial.")
    assert view["stage"] == "preview" and len(fake.requests) == 2
    assert "[word-alone]" in str(fake.requests[1]["messages"])
    # Default mode: the model is told the operation is not its to use, and nothing runs in Word
    fake = FakeClient(resp(tool("submit_editspec", {"spec": word_spec("accept_changes")})),
                      resp(tool("submit_editspec", {"spec": SPEC})))
    task = task_for(tmp_path / "d", fake)
    doc = tmp_path / "c.docx"
    doc.write_bytes(plain)
    task.attach([doc])
    calls = len(word.calls)
    task.perform("send", text=REQUEST)
    assert "[obliterate-only]" in str(fake.requests[1]["messages"]) and len(word.calls) == calls


def test_a_pass_over_the_to_do_list_goes_on_after_word_and_then_takes_the_plan(tmp_path, plain, word):
    """Clear the obstacle, then the same plan — in two requests, because nothing can be planned after a file Word
    saved again: the Word action's copy is created, the pass stays open on its document, and Continue plans the
    request it was set aside from on that copy."""
    fake = FakeClient(resp(tool("submit_editspec", {"spec": SPEC})))
    a, b = document(tmp_path / "a.docx"), document(tmp_path / "b.docx")
    task = task_for(tmp_path, fake)
    task.attach([a, b])
    task.todo[str(b)] = {"name": "b.docx", "why": "refused", "reason": "A field stopped the plan.",
                         "decisions": [REQUEST], "seen": ["Make the title bold."],
                         "case": {"code": "field", "reason": "a field the op would not guess about",
                                  "plan": json.dumps(SPEC)}}
    task._sync()
    task.obliterate_todo(str(b), acknowledged=True)
    assert task.focus["documents"] == [str(b)] and "word_action" in task._focus_context()
    view = task.perform("word_actions", path=str(b), actions=["repair"])
    assert view["stage"] == "preview" and fake.requests == []
    view = task.perform("primary", token=view["token"])
    # the copy is in the working set, marked; the pass is still open on the document, its request standing
    assert list(task.results) == [str(b)] and task.unverified[str(b)] == [word_actions.UNPROVEN_MARK]
    assert task.focus and task.focus["documents"] == [str(b)] and task.decisions == [REQUEST]
    assert view["stage"] == "resume" and view["action"] == "Continue" and view["focus"] == [str(b)]
    assert any("b.docx is still in your to-do pass: Continue plans the request it was set aside from on this copy"
               in t for t in said(task))
    assert WORD_THEN_SAME in task._focus_context() and str(b) not in task.todo
    view = task.perform("primary", token=view["token"])
    assert view["stage"] == "preview" and len(fake.requests) == 1
    sent = json.dumps(fake.requests[0]["messages"])
    assert "A WORD ACTION WAS RUN ON THIS COPY" in sent and "Word action, Open and repair, then save" in sent
    task.perform("primary", token=view["token"])
    assert task.focus is None and task.stage == "done" and str(b) not in task.todo
    assert "in an earlier copy: " + word_actions.UNPROVEN_MARK in task.unverified[str(b)]


def test_dropping_the_pass_after_word_puts_the_document_back_and_undo_takes_the_note_off(tmp_path, plain, word):
    a, b = document(tmp_path / "a.docx"), document(tmp_path / "b.docx")
    task = task_for(tmp_path)
    task.attach([a, b])
    task.todo[str(b)] = {"name": "b.docx", "why": "refused", "reason": "A field stopped the plan.",
                         "decisions": [REQUEST], "seen": []}
    task._sync()
    task.obliterate_todo(str(b), acknowledged=True)
    view = task.perform("word_actions", path=str(b), actions=["repair"])
    task.perform("primary", token=view["token"])
    assert task.focus["after_word"] == task.generation
    task.undo()                                 # the copy Word just made in the pass can be undone; the pass goes on
    assert "after_word" not in task.focus and WORD_THEN_SAME not in task._focus_context()
    assert task.focus["documents"] == [str(b)] and task.stage == "resume" and str(b) not in task.working
    view = task.perform("word_actions", path=str(b), actions=["repair"])
    task.perform("primary", token=view["token"])
    assert task.discard_kind() == "request"
    task.discard_preview()
    assert task.focus is None and str(b) in task.todo and str(b) in task.working      # its copy stays its copy


# ---- the server ------------------------------------------------------------------------------------------------------

def window(tmp_path):
    session = Session(client=FakeClient(), out_root=tmp_path / "out", visual_check=False)
    task = DocumentTask(session, render=False, obliterate_allowed=True)
    state = WindowState(task, Settings(show_obliterate=True), token=TOKEN, updates=FakeUpdates(), version="1.6.0")
    return state, TestClient(create_app(state), headers={TOKEN_HEADER: TOKEN})


def test_the_route_runs_the_menu_in_obliterate_alone(tmp_path, plain, word):
    state, client = window(tmp_path)
    a = tmp_path / "a.docx"
    a.write_bytes(plain)
    state.task.attach([a])
    assert client.get("/api/state").json()["word"] is None
    refused = client.post("/api/word-actions", json={"path": str(a), "actions": ["repair"]})
    assert refused.status_code == 400 and "Obliterate mode's" in refused.json()["error"]
    assert client.post("/api/mode", json={"mode": "obliterate", "acknowledged": True}).status_code == 200
    menu = client.get("/api/state").json()["word"]
    assert menu["available"] is True and [x["id"] for x in menu["actions"]] == list(word_actions.OFFERED)
    for body, why in (({"path": str(a), "actions": []}, "at least one"),
                      ({"path": str(a), "actions": ["macro"]}, "not a Word action"),
                      ({"path": str(tmp_path / "other.docx"), "actions": ["repair"]}, "not attached"),
                      ({"path": str(a), "actions": "repair"}, "at least one")):
        refused = client.post("/api/word-actions", json=body)
        assert refused.status_code == 400 and why in refused.json()["error"], body
    assert word.calls == []
    events = frames(client.post("/api/word-actions", json={"path": str(a), "actions": ["repair", "unlink_fields"]}))
    done = events[-1]
    assert done["type"] == "done" and done["state"]["stage"] == "preview"
    [sample] = done["state"]["samples"]
    assert sample["made_by"] == "word" and sample["unproven"] and sample["word_account"][0].startswith("opened with")
    assert word.calls == [("a.docx", ["repair", "unlink_fields"])]


# ---- Stop, and a plan of Claude's that Word could not carry out ------------------------------------------------------

def test_stop_ends_the_run_between_documents_and_nothing_is_previewed(tmp_path, plain, monkeypatch):
    """The menu's job calls no AI, so Stop ends the job itself (the server arms it for this job in any mode): no
    further document reaches Word, the preview is withdrawn and no copy exists."""
    import threading

    from spec_surgeon import stopping
    event = threading.Event()

    class StopsAfterOne(FakeWord):
        def __call__(self, source, actions, output, timeout=None):
            report = super().__call__(source, actions, output, timeout)
            event.set()                                         # the user presses Stop while Word has the first one
            return report
    fake_word = StopsAfterOne()
    monkeypatch.setattr(word_actions, "run_word_actions", fake_word)
    monkeypatch.setattr(word_actions, "available", lambda fresh=False: True)
    task, (a, b) = obliterate_task(tmp_path, plain)
    with stopping.scope(event), pytest.raises(stopping.Stopped):
        task.perform("word_actions", path=str(a), actions=["repair"], every=True)
    assert [c[0] for c in fake_word.calls] == ["a.docx"]
    assert task.session.generated is None and task.session.spec is None and task.results == {}
    assert task.stage == "empty" and task.decisions == [] and task.view()["samples"] == []
    from spec_surgeon.web import server
    assert "word_actions" in server.MODEL_FREE_JOBS


def test_when_word_cannot_carry_out_claudes_plan_the_conversation_hears_why_and_waits(tmp_path, plain, monkeypatch):
    error = RuntimeError("Word stopped")
    error.code, error.details = "word-com", {"stage": "word_action_accept_changes"}
    monkeypatch.setattr(word_actions, "run_word_actions", FakeWord(fail={"a.docx": error, "b.docx": error}))
    monkeypatch.setattr(word_actions, "available", lambda fresh=False: True)
    spec = word_spec("accept_changes")
    fake = FakeClient(resp(tool("submit_editspec", {"spec": spec})))
    task, _docs = obliterate_task(tmp_path, plain, fake=fake)
    view = task.perform("send", text="Accept every tracked change with Word itself.")
    assert view["stage"] == "question" and task.session.generated is None and task.session.spec is None
    assert any(t.startswith("Word made no copy, so there is nothing to preview.") and "Accept all tracked changes" in t
               for t in said(task))
    assert any(n.startswith("Word made no copy") for n in task.session.conversation.notes)
    assert task.results == {} and task.session.conversation is not None        # the user's next words go on from here
