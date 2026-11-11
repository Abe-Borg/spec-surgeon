"""The raw XML edit in the window (docs/BACKLOG.md O3; CLAUDE.md, Modes): Obliterate's conversation plans it, the chat
says the words it changes before the approval button, and "clear the obstacle, then the same plan" finishes a document
from the to-do list in two verified steps. Default and Safe refuse it and are told nothing new.

The engine and the verifier's twin are pinned in tests/test_edit_xml.py; here, what the user sees and what the model
hears.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from lxml import etree

from fake_claude import FakeClient, resp, text, tool
from standing_helpers import case_of, owing
from spec_surgeon.document_task import CASE_PLAN, CLEAR_THEN_SAME, DocumentTask, _restored_case
from spec_surgeon.gui import Session
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.preview import RAW_NO_WORDS_CHANGE, RAW_OTHER_CHANGES_HEAD, RAW_WORDS_CHANGE_HEAD
from spec_surgeon.ui_feedback import UNPROVEN_MARK
from test_document_task import document

DOC = "word/document.xml"
KEEP = "Keep this technical wording intact."
BOX = ('<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
       'xmlns:v="urn:schemas-microsoft-com:vml"><w:pict><v:shape><v:textbox><w:txbxContent><w:p><w:r>'
       '<w:t>SUPERSEDED STAMP</w:t></w:r></w:p></w:txbxContent></v:textbox></v:shape></w:pict></w:r>')
REQUEST = "Delete the line that starts Keep this."
DELETE = {"request": REQUEST, "summary": "Delete the line.", "mode": "content",
          "ops": [{"op": "delete_paragraphs", "select": {"paragraphs": {"text_regex": "^Keep this technical"}},
                   "params": {}}]}
CLEAR = {"request": "clear the text box", "summary": "Remove the text box that stops the deletion.",
         "mode": "untracked",
         "ops": [{"op": "edit_xml", "params": {"part": DOC, "xpath": "//w:r[w:pict//w:txbxContent]", "count": 1,
                                               "action": "remove"}}]}
STAGES = {"summary": "Clear the text box, then delete the line.",
          "stages": [{"name": "Clear the text box", "spec": CLEAR}, {"name": "Delete the line", "spec": DELETE}]}
RAW_REQUEST = "Take the last line out of each file with a raw XML edit."
RAW = {"request": RAW_REQUEST, "summary": "Remove the last line.", "mode": "untracked",
       "ops": [{"op": "edit_xml", "params": {"part": DOC, "xpath": f"//w:p[w:r/w:t='{KEEP}']", "count": 1,
                                             "action": "remove"}}]}
BOLD = {"request": "bold", "summary": "Mark the first run bold, as raw XML.", "mode": "untracked",
        "ops": [{"op": "edit_xml", "params": {"part": DOC, "xpath": "(//w:body/w:p[1]/w:r)[1]", "count": 1,
                                              "action": "insert", "position": "first_child",
                                              "xml": "<w:rPr><w:b/></w:rPr>"}}]}


def boxed(path: Path) -> Path:
    """``document`` with a text box in its Keep this line: what a built-in deletion refuses to guess about."""
    document(path)
    pkg = DocxPackage(path.read_bytes())
    root = pkg.xml(DOC)
    [line] = [p for p in root.iter(W + "p") if "".join(t.text or "" for t in p.iter(W + "t")) == KEEP]
    line.append(etree.fromstring(BOX))
    work = pkg.copy()
    work.write_xml(DOC, root)
    path.write_bytes(work.output_bytes())
    return path


def submit(spec, tid="s1"):
    return resp(tool("submit_editspec", {"spec": spec}, tid=tid))


def task_for(tmp_path, fake, **kwargs):
    kwargs.setdefault("obliterate_allowed", True)
    return DocumentTask(Session(client=fake, out_root=tmp_path / "out", visual_check=False), render=False, **kwargs)


def said(task):
    return [m["text"] for m in task.messages if m["role"] == "app"]


def body_of(path) -> etree._Element:
    return etree.fromstring(zipfile.ZipFile(io.BytesIO(Path(path).read_bytes())).read(DOC))


def words(path) -> str:
    return "".join(body_of(path).itertext())


# ---- Obliterate's conversation plans it -----------------------------------------------------------------------------

def test_the_chat_says_the_words_a_raw_edit_changes_before_the_button_and_the_copies_are_verified(tmp_path):
    a, b = document(tmp_path / "a.docx"), document(tmp_path / "b.docx")
    fake = FakeClient(submit(RAW), resp(text("Anything else?"), stop="end_turn"))
    task = task_for(tmp_path, fake)
    task.attach([a, b])
    task.set_mode("obliterate", acknowledged=True)
    view = task.perform("send", text=RAW_REQUEST)
    assert view["stage"] == "preview", said(task)[-1]
    # the model was offered the operation, and told it is this mode's alone
    assert '"edit_xml"' in str(fake.requests[0]["system"])
    samples = task._samples_view()["samples"]
    assert [s["name"] for s in samples] == ["a.docx", "b.docx"]
    for sample in samples:
        assert sample["raw_xml"] and sample["passed"] and not sample["unproven"] and sample["failures"] == []
        assert [c["removed"] for c in sample["text_changes"]] == [[KEEP]]
        assert "Paragraphs: 3 before, 2 after." in sample["changes"]
    # said once, before the button: the words from the verifier's reading of the copies, then what else changed
    told = said(task)
    [heard] = [t for t in told if t.startswith(RAW_WORDS_CHANGE_HEAD)]
    assert f"words removed “{KEEP}”" in heard and "a.docx" in heard and "b.docx" in heard
    [other] = [t for t in told if t.startswith(RAW_OTHER_CHANGES_HEAD)]
    assert "Paragraphs: 3 before, 2 after." in other
    assert told.index(heard) < told.index(other) == len(told) - 1
    task.view()
    assert len([t for t in said(task) if t.startswith(RAW_WORDS_CHANGE_HEAD)]) == 1
    # the plan's own sentence states the edit; the preview's text carries the listing, under its own headings
    [change] = task.view()["groups"][0]["changes"]
    assert change["title"] == "Raw XML edit" and change["summary"].startswith(
        "Raw XML edit in the part “word/document.xml”: remove the 1 element that the XPath ")
    assert "Settings · XPath: " + RAW["ops"][0]["params"]["xpath"] in change["details"]
    shown = task.session.outcome().preview.to_text()
    assert "RAW XML EDIT — WORDS" in shown and "RAW XML EDIT — WHAT ELSE CHANGED" in shown
    assert RAW_WORDS_CHANGE_HEAD not in shown and RAW_OTHER_CHANGES_HEAD not in shown
    before = a.read_bytes()
    task.perform("primary", token=view["token"])
    assert task.stage == "done" and sorted(Path(k).name for k in task.results) == ["a.docx", "b.docx"]
    assert task.unverified == {} and UNPROVEN_MARK not in json.dumps(task.view(), default=str)
    assert a.read_bytes() == before and KEEP in words(a)                # copies only
    for copy in task.results.values():
        assert KEEP not in words(copy) and "FIRE SUPPRESSION" in words(copy)
    # each copy's history is the plan's own sentence
    assert all(line.startswith("Raw XML edit in the part “word/document.xml”") for lines in task.applied.values()
               for line in lines)


def test_an_edit_that_changes_no_words_says_so(tmp_path):
    a = document(tmp_path / "a.docx")
    fake = FakeClient(submit(BOLD), resp(text("Only the first run."), stop="end_turn"))
    task = task_for(tmp_path, fake)
    task.attach([a])
    task.set_mode("obliterate", acknowledged=True)
    view = task.perform("send", text="Make the first run bold with a raw XML edit.")
    assert view["stage"] == "preview", said(task)[-1]
    [sample] = task._samples_view()["samples"]
    assert sample["raw_xml"] and sample["text_changes"] == [] and sample["passed"]
    assert RAW_NO_WORDS_CHANGE in said(task)
    assert any(t.startswith(RAW_OTHER_CHANGES_HEAD) and "formatting or markup of 1 paragraph" in t for t in said(task))
    # the model hears the same lines when the user goes on about the plan
    task.perform("send", text="Is that everything it changes?")
    heard = json.dumps(fake.requests[-1]["messages"], default=str, ensure_ascii=False)
    assert RAW_NO_WORDS_CHANGE in heard and "formatting or markup of 1 paragraph" in heard


def test_text_outside_the_paragraphs_is_said_as_changed_never_as_no_words(tmp_path):
    """A raw edit of text the paragraph reader does not read (here a document property; a chart's title is the same
    case): the chat and the card list it, and neither says that no words change."""
    a = document(tmp_path / "a.docx")
    author = {"request": "r", "summary": "Set the author property.", "mode": "untracked",
              "ops": [{"op": "edit_xml", "params": {"part": "docProps/core.xml", "xpath": "//dc:creator", "count": 1,
                                                    "action": "set_text", "text": "Design Team"}}]}
    task = task_for(tmp_path, FakeClient(submit(author)))
    task.attach([a])
    task.set_mode("obliterate", acknowledged=True)
    view = task.perform("send", text="Set the document's author property to Design Team with a raw XML edit.")
    assert view["stage"] == "preview", said(task)[-1]
    [sample] = task._samples_view()["samples"]
    assert sample["raw_xml"] and sample["passed"]
    assert sample["text_changes"] == [{"part": "docProps/core.xml", "paragraph": None, "other": True,
                                       "where": "docProps/core.xml, other text (not a paragraph's words)",
                                       "removed": ["python-docx"], "added": ["Design Team"]}]
    [heard] = [t for t in said(task) if t.startswith(RAW_WORDS_CHANGE_HEAD)]
    assert "text removed “python-docx”; text added “Design Team”" in heard
    assert RAW_NO_WORDS_CHANGE not in said(task)


def test_default_mode_refuses_the_plan_at_the_gate_and_the_model_is_told_why(tmp_path):
    a = document(tmp_path / "a.docx")
    fake = FakeClient(submit(RAW), resp(text("I cannot do that here."), stop="end_turn"))
    task = task_for(tmp_path, fake)
    task.attach([a])
    view = task.perform("send", text=RAW_REQUEST)
    assert view["stage"] != "preview" and task.session.spec is None
    assert "edit_xml" not in str(fake.requests[0]["system"])
    back = json.dumps(fake.requests[1]["messages"][-1], default=str)
    assert "[obliterate-only]" in back and "Obliterate mode" in back
    # and a plan that reached a session some other way plans nothing in Default: the gate is the one way in
    safe = DocumentTask(Session(out_root=tmp_path / "safe", visual_check=False), render=False, mode="safe")
    assert "edit_xml" not in json.dumps(safe.view(), default=str)


def test_wording_a_raw_edit_places_must_be_the_users_or_shown_in_the_reply(tmp_path):
    a = document(tmp_path / "a.docx")
    retitle = {"request": "r", "summary": "Retitle.", "mode": "untracked",
               "ops": [{"op": "edit_xml", "params": {"part": DOC, "xpath": "(//w:body/w:p[2]//w:t)[1]", "count": 1,
                                                     "action": "set_text", "text": "WET-PIPE SPRINKLER SYSTEMS"}}]}
    fake = FakeClient(submit(retitle), resp(text("I will set the title to “WET-PIPE SPRINKLER SYSTEMS”."),
                                            tool("submit_editspec", {"spec": retitle}, tid="s2")))
    task = task_for(tmp_path, fake)
    task.attach([a])
    task.set_mode("obliterate", acknowledged=True)
    view = task.perform("send", text="Give the title better wording with a raw XML edit.")
    assert "the user has not read them" in json.dumps(fake.requests[1]["messages"][-1], default=str)
    assert view["stage"] == "preview", said(task)[-1]
    [sample] = task._samples_view()["samples"]
    assert sample["text_changes"][0]["added"] == ["WET-PIPE SPRINKLER SYSTEMS"]
    assert sample["text_changes"][0]["removed"] == ["FIRE SUPPRESSION"]


# ---- clear the obstacle, then the same plan --------------------------------------------------------------------------

def straggler(tmp_path, *more):
    """Default mode over good.docx and bad.docx: the deletion is refused on bad (a text box in the line), good's copy
    is created, and bad waits on the to-do list with its case file."""
    good, bad = document(tmp_path / "good.docx"), boxed(tmp_path / "bad.docx")
    fake = FakeClient(submit(DELETE), *more)
    task = task_for(tmp_path, fake)
    task.attach([good, bad])
    view = task.perform("send", text=REQUEST)
    assert view["stage"] == "preview" and [item["name"] for item in view["set_aside"]] == ["bad.docx"]
    task.perform("primary", token=view["token"])
    assert task.stage == "done" and list(task.results) == [str(good)]
    assert owing(task) == ["bad.docx"] and task.todo == {}           # owed, and still in the batch
    return task, fake, good, bad


def test_the_case_file_keeps_the_plan_the_other_documents_got(tmp_path):
    task, _fake, good, bad = straggler(tmp_path)
    case = case_of(task, bad)
    assert case["code"] == "textbox" and case["copy"] is None and case["sibling"]["name"] == "good.docx"
    assert json.loads(case["plan"]) == DELETE
    # journalled with the entry; a plan too long to keep, or not text, is dropped and the rest of the case stays
    assert _restored_case(case) == case
    assert "plan" not in _restored_case({**case, "plan": "x" * (CASE_PLAN + 1)})
    assert "plan" not in _restored_case({**case, "plan": {"ops": []}})
    journal = tmp_path / "journal.json"
    task.journal = journal
    task.save()
    again = task_for(tmp_path, FakeClient(), journal=journal)
    assert again.restore() and case_of(again, bad)["plan"] == case["plan"]
    # Default's own pass over the list hears what it always did: the entry's sentence, no case file, no plan
    task.start_todo()
    assert "CASE FILE" not in task._focus_context() and "edit_xml" not in task._focus_context()
    assert task._case_note() == ""


def test_a_plan_in_steps_or_per_layout_is_kept_as_the_document_would_get_it(tmp_path):
    a = document(tmp_path / "a.docx")
    task = task_for(tmp_path, FakeClient())
    task.attach([a])
    s = task.session
    s.spec = {**DELETE, "applies_to": {"patterns": ["p"]}, "questions": []}
    assert json.loads(task._case_plan(a)) == DELETE                     # the spec, without the app's own pins
    s.spec = {"summary": "two steps", "stages": [{"name": "one", "spec": DELETE}, {"name": "two", "spec": CLEAR}]}
    assert json.loads(task._case_plan(a)) == {"stages": [{"name": "one", "spec": DELETE}, {"name": "two", "spec": CLEAR}]}
    s.spec = {**DELETE, "summary": "x" * CASE_PLAN}
    assert task._case_plan(a) == ""                                     # longer than a case file keeps
    s.spec = None
    assert task._case_plan(a) == ""


def test_a_document_one_thing_stopped_is_finished_by_clearing_it_then_the_same_plan(tmp_path):
    task, fake, good, bad = straggler(tmp_path, submit(STAGES, tid="s2"))
    original = bad.read_bytes()
    task.obliterate_todo(str(bad), acknowledged=True)
    view = task.perform("send", text="Do whatever it takes.")
    # the conversation heard the case file: the engine's reason, the plan as JSON, and how to finish the document
    note = fake.requests[1]["messages"][0]["content"].split("<app_note>")[1].split("</app_note>")[0]
    assert "The engine stopped on it in delete_paragraphs [textbox]" in note
    assert "The plan it was set aside from, as the app ran it on the other documents (JSON): " in note
    assert json.dumps(DELETE, ensure_ascii=False, separators=(",", ":")) in note and CLEAR_THEN_SAME in note
    assert view["stage"] == "preview", said(task)[-1]
    [sample] = task._samples_view()["samples"]
    assert sample["name"] == "bad.docx" and sample["raw_xml"] and sample["passed"] and not sample["unproven"]
    # the words the clearing step takes out are the text box's own, read from the copy; the deletion's are its plan's
    assert [c["removed"] for c in sample["text_changes"]] == [["SUPERSEDED STAMP"]]
    assert "Text boxes: 1 before, 0 after." in sample["changes"]
    [heard] = [t for t in said(task) if t.startswith(RAW_WORDS_CHANGE_HEAD)]
    assert "words removed “SUPERSEDED STAMP”" in heard and "bad.docx" in heard
    first, second = task.view()["groups"][0]["changes"]
    assert first["summary"].startswith("Raw XML edit in the part “word/document.xml”: remove the 1 element")
    assert second["title"] == "Remove paragraphs"
    assert "stage 1 PASS · stage 2 PASS" in task.session.outcome().preview.to_text()
    task.perform("primary", token=view["token"])
    assert task.stage == "done" and task.todo == {} and task.unverified == {}
    copy = Path(task.results[str(bad)])
    assert bad.read_bytes() == original                                 # copies only
    tree = body_of(copy)
    assert "SUPERSEDED STAMP" not in "".join(tree.itertext()) and not list(tree.iter(W + "pict"))
    # the second step is the plan as good.docx got it: the same tracked deletion of the same line
    assert "".join(t.text or "" for t in tree.iter(W + "delText")) == KEEP
    assert "".join(t.text or "" for t in body_of(task.results[str(good)]).iter(W + "delText")) == KEEP
    # both steps were checked, each on the copy the one before it made
    [result] = task.session.last_batch.files
    assert result.status == "PASS" and result.stage_status == ["PASS", "PASS"]
    assert UNPROVEN_MARK not in json.dumps(task.view(), default=str)


def test_one_flat_list_is_refused_with_the_reason_the_steps_exist_for(tmp_path):
    """The model that puts the raw edit and the plan in one list hears the plan's own refusal again (every op of a
    spec is planned on the document as it is), and the steps it then submits pass."""
    flat = {**DELETE, "ops": [*CLEAR["ops"], *DELETE["ops"]]}
    task, fake, _good, bad = straggler(tmp_path, submit(flat, tid="s2"), submit(STAGES, tid="s3"))
    task.obliterate_todo(str(bad), acknowledged=True)
    view = task.perform("send", text="Do whatever it takes.")
    back = json.dumps(fake.requests[2]["messages"][-1], default=str)
    assert "textbox" in back or "text box" in back
    assert view["stage"] == "preview" and task._samples_view()["samples"][0]["passed"]
