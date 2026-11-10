"""Export anyway (docs/BACKLOG.md O7; CLAUDE.md, Modes; the owner's decision of 2026-11-10): in Obliterate mode, when
a clean export (Word, Tracked Changes OFF, or PDF, Clean) leaves a MARKED copy without a file — UNVERIFIED, UNPROVEN or
SALVAGED: a copy no check proves as it is — the Export menu offers that kind MADE BY WORD instead.

Word runs a fixed list of its own named commands on a private copy of the edited copy (accept every tracked change;
for the PDF, delete every comment first) and saves it. Nothing is planned, predicted or passed through the gate, so
the file is UNCHECKED and the summary names it so, with Word's own count and what the independent reader found in the
file Word saved. The choice is fixed in code and named by the menu, never a model's.

No test machine has Word: ``fake_word.FakeWord`` stands behind the one seam (``word_actions.run_word_actions``). What
Word itself makes of a real document is the owner's real-file run.

Pinned here: the offer is Obliterate's alone, for marked copies alone, and only after the checked way left them
without a file; Word gets a private copy and exactly the fixed commands; the file is written as Word made it and named
UNCHECKED, with Word's count and the app's reading; a file whose words differ, or that keeps a tracked change, is
saved and said; a document Word cannot finish gets no file and stays offered; the edited copies, the history and Undo
are untouched; no AI call; Stop ends the job; Safe and Default are told nothing of it.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from lxml import etree

from fake_claude import FakeClient
from fake_word import FakeWord, W, save_again
from spec_surgeon import catalog, codegen, compiler, exports, pdf_export, word_actions
from spec_surgeon.document_task import (ANYWAY_KINDS, ANYWAY_NOTHING, ANYWAY_OBLITERATE_ONLY, EARLIER_COPY, STOPPED,
                                        DocumentTask)
from spec_surgeon.gui import Session
from spec_surgeon.ops import OPS
from spec_surgeon.render import word_actions as commands
from spec_surgeon.settings import Settings
from spec_surgeon.web import server
from spec_surgeon.web.server import TOKEN_HEADER, WindowState, create_app
from test_export_clean import (COMMENTS, MARKUP, markup, members, no_ai, redlined_set, renderer, state_of,  # noqa: F401
                               styled_redline)
from test_web_server import TOKEN, FakeUpdates, frames
from test_word_actions import Collection, FakeDocument, session_over

EQUATION_WHY = "a tracked change inside an equation (Jeremy) — accepting it is done in Word"
CLEAN_READING = ("The app read the file Word saved: its words are your edited copy's with every tracked change "
                 "accepted, and no tracked change is left.")
CLEAN_PDF_READING = CLEAN_READING.replace("no tracked change is left", "no tracked change or comment is left")


@pytest.fixture
def word(monkeypatch) -> FakeWord:
    fake = FakeWord()
    monkeypatch.setattr(word_actions, "run_word_actions", fake)
    monkeypatch.setattr(word_actions, "available", lambda fresh=False: True)
    return fake


def edited(tmp_path, names, mode="obliterate", journal=None) -> DocumentTask:
    """A window whose documents went through one Safe-mode button (Remove highlighting): one verified working copy
    each, their tracked changes and comments as the originals had them. Then the mode asked for."""
    task = DocumentTask(Session(client=FakeClient(), out_root=tmp_path / "out", visual_check=False), render=False,
                        mode="safe", obliterate_allowed=True, journal=journal)
    docs = tmp_path / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    others = [n for n in names if n != "styled.docx"]
    files = redlined_set(docs, others) if others else []
    if "styled.docx" in names:
        (docs / "styled.docx").write_bytes(styled_redline())
        files.append(docs / "styled.docx")
    task.attach(files)
    view = task.perform("recipe", recipe="remove_highlight", inputs={})
    view = task.perform("primary", token=view["token"])
    assert view["stage"] == "done" and len(task.working) == len(names)
    if mode != "safe":
        task.set_mode(mode, acknowledged=True)
    return task


def origin_of(task, name: str) -> str:
    return next(k for k in task.working if Path(k).name == name)


def mark(task, *names) -> None:
    """As an Obliterate delivery leaves a copy: UNVERIFIED, or built on one."""
    for name in names:
        task.unverified[origin_of(task, name)] = [EARLIER_COPY + "V2 (Text preservation)"]


def folder(tmp_path, name) -> Path:
    dest = tmp_path / name
    dest.mkdir(exist_ok=True)
    return dest


def said(task) -> str:
    return task.messages[-1]["text"]


# ---- the fixed commands ----------------------------------------------------------------------------------------------

def test_the_commands_are_fixed_in_code_and_no_plan_can_name_the_one_that_is_exports_alone():
    assert exports.ANYWAY_ACTIONS == {"accepted": ("accept_changes",), "pdf": ("delete_comments", "accept_changes")}
    assert set(exports.ANYWAY_LABELS) == set(exports.ANYWAY_ACTIONS) == set(exports.ANYWAY_DOES)
    assert all(label.endswith(", made by Word") for label in exports.ANYWAY_LABELS.values())
    # every command is one of the app's own Word commands; deleting comments is Export anyway's alone — off the
    # menu the user picks from and out of the operation a plan names an action by
    for actions in exports.ANYWAY_ACTIONS.values():
        assert word_actions.check(list(actions), offered_only=False) == list(actions)
        assert all(a in commands.COMMANDS for a in actions)
    assert "delete_comments" not in word_actions.OFFERED
    assert "delete_comments" not in OPS["word_action"].params_schema["properties"]["action"]["enum"]
    assert "delete_comments" not in [a["id"] for a in word_actions.menu()]
    with pytest.raises(ValueError, match="not a Word action on the menu"):
        word_actions.check(["delete_comments"])
    assert word_actions.account([{"action": "delete_comments", "count": 2, "note": ""}]) == ["2 comments deleted"]
    with pytest.raises(ValueError, match="not a clean export"):
        exports.make_anyway({"a": Path("a.docx")}, "word", Path("nowhere"))


def test_word_deletes_the_comments_with_its_own_command(tmp_path):
    source = tmp_path / "in" / "a.docx"
    source.parent.mkdir()
    source.write_bytes(redlined_set(tmp_path / "docs", ["commented.docx"])[0].read_bytes())
    doc, opened = FakeDocument(), []
    doc.Comments = Collection(range(2))
    doc.DeleteAllComments = lambda: setattr(doc, "Comments", Collection([]))
    session, _ = session_over(doc, tmp_path, opened)
    report = session.run_actions(source, tmp_path / "clean.docx", list(exports.ANYWAY_ACTIONS["pdf"]))
    assert [(e["action"], e["count"]) for e in report] == [("delete_comments", 2), ("accept_changes", 3)]
    assert doc.Comments.Count == 0 and doc.Revisions.Count == 0 and doc.saved == [("clean.docx", 12, True)]
    assert opened[0]["path"] != source                          # a private copy, never the file given
    # a document without comments is not asked to delete any
    plain, opened = FakeDocument(), []
    plain.Comments = Collection([])
    plain.DeleteAllComments = lambda: (_ for _ in ()).throw(AssertionError("nothing to delete"))
    session, _ = session_over(plain, tmp_path, opened)
    assert session.run_actions(source, tmp_path / "again.docx", ["delete_comments"])[0]["count"] == 0


# ---- when it is offered ------------------------------------------------------------------------------------------------

def test_it_is_offered_in_obliterate_for_a_marked_copy_the_checked_way_left_without_a_file(tmp_path, word, no_ai):
    task = edited(tmp_path, ["redline.docx", "equation.docx"], mode="safe")
    dest = folder(tmp_path, "Clean")
    # Safe mode: the checked way leaves the equation without a file, and nothing new is said or offered
    made = task.export(dest, "accepted")
    assert made == {"folder": str(dest.resolve()), "kind": "accepted", "saved": 1, "already": 0,
                    "missing": {"equation.docx": EQUATION_WHY}}
    assert said(task).endswith(f"Not saved: equation.docx — {EQUATION_WHY}.") and "anyway" not in said(task)
    assert "export_anyway" not in task.view() and task.export_anyway_view() == []
    mark(task, "equation.docx")
    assert "export_anyway" not in task.view()                    # a marked copy, and still Safe mode: nothing
    with pytest.raises(ValueError) as refused:
        task.export(dest, "accepted", anyway=True)
    assert str(refused.value) == ANYWAY_OBLITERATE_ONLY
    # Obliterate mode: offered, by name, for the kind that left it
    task.set_mode("obliterate", acknowledged=True)
    assert task.view()["export_anyway"] == [{
        "kind": "accepted", "label": "Word (Tracked Changes OFF), made by Word",
        "does": "Word accepts every tracked change on a private copy and saves it; comments stay.",
        "copies": ["equation.docx"], "blocked": ""}]
    # never for a kind that was not tried, for Word (Tracked Changes ON), or for a copy with no mark
    for kind, why in (("pdf", ANYWAY_NOTHING), ("word", ANYWAY_KINDS)):
        with pytest.raises(ValueError) as refused:
            task.export_ready(kind, anyway=True)
        assert str(refused.value) == why
    del task.unverified[origin_of(task, "equation.docx")]
    assert "export_anyway" not in task.view()
    made = task.export(dest, "accepted")                         # the same export, in Obliterate, with no mark
    assert list(made["missing"]) == ["equation.docx"] and "anyway" not in said(task)
    # the mark of a document the app rescued counts: its copies are proven against the rescued file alone
    next(a for a in task.attachments if a.path.name == "equation.docx").salvaged = {
        "from": "equation.doc", "kind": "format", "steps": [], "notes": []}
    assert [o["copies"] for o in task.view()["export_anyway"]] == [["equation.docx"]]
    # back in Default the offer is gone, and so is the way in
    task.set_mode("default")
    assert "export_anyway" not in task.view()
    with pytest.raises(ValueError, match="Obliterate mode's"):
        task.export_ready("accepted", anyway=True)
    assert word.calls == []


def test_the_offer_is_for_the_copy_as_it_was_when_the_export_left_it(tmp_path, word, no_ai):
    journal = tmp_path / "journal.json"
    task = edited(tmp_path, ["redline.docx", "equation.docx"], journal=journal)
    mark(task, "equation.docx")
    task.export(folder(tmp_path, "Clean"), "accepted")
    assert said(task).endswith(
        "equation.docx is a marked copy, so in Obliterate mode Export can have Word make it anyway, unchecked: "
        "Export → “Word (Tracked Changes OFF), made by Word”.")
    assert task.export_anyway_set("accepted") == {origin_of(task, "equation.docx"): task.working[
        origin_of(task, "equation.docx")]}
    # the offer is kept nowhere: a window started again from the journal runs the export again to be offered it
    assert "export_missed" not in journal.read_text(encoding="utf-8")
    again = DocumentTask(Session(client=FakeClient(), out_root=tmp_path / "out", visual_check=False), render=False,
                         obliterate_allowed=True, journal=journal)
    assert again.restore() and again.unverified == task.unverified and again.working == task.working
    again.set_mode("obliterate", acknowledged=True)
    assert again.export_missed == {} and "export_anyway" not in again.view()
    # the copy edited since (opened from the window and saved in Word): the export that left it was of other bytes
    copy = Path(task.working[origin_of(task, "equation.docx")])
    copy.write_bytes(save_again(copy.read_bytes(), ["repair"])[0])
    assert task.export_anyway_set("accepted") == {} and "export_anyway" not in task.view()
    with pytest.raises(ValueError) as refused:
        task.export_ready("accepted", anyway=True)
    assert str(refused.value) == ANYWAY_NOTHING


def test_without_word_the_offer_says_so_and_cannot_run(tmp_path, monkeypatch, no_ai):
    monkeypatch.setattr(word_actions, "available", lambda fresh=False: False)
    task = edited(tmp_path, ["equation.docx"])
    mark(task, "equation.docx")
    dest = folder(tmp_path, "Clean")
    task.export(dest, "accepted")
    assert said(task).endswith("made by Word”. That needs Microsoft Word, which the app cannot reach on this computer.")
    [offer] = task.view()["export_anyway"]
    assert offer["blocked"] == word_actions.NO_WORD
    with pytest.raises(ValueError) as refused:
        task.export(dest, "accepted", anyway=True)
    assert str(refused.value) == word_actions.NO_WORD and list(dest.iterdir()) == []


# ---- Word (Tracked Changes OFF), made by Word --------------------------------------------------------------------------

def test_word_makes_the_clean_copy_of_a_copy_word_made_and_it_is_named_unchecked(tmp_path, word, no_ai):
    task = edited(tmp_path, ["redline.docx", "equation.docx"])
    eq = origin_of(task, "equation.docx")
    # the document Obliterate was reached for: Word's own action made its copy, which is UNPROVEN
    view = task.perform("word_actions", path=eq, actions=["repair"])
    task.perform("primary", token=view["token"])
    assert task.unverified == {eq: [word_actions.UNPROVEN_MARK]}
    dest = folder(tmp_path, "Clean")
    made = task.export(dest, "accepted")
    assert made["saved"] == 1 and made["missing"] == {"equation.docx": EQUATION_WHY}
    assert [p.name for p in dest.iterdir()] == ["redline.docx"]
    before, checked = state_of(task), (dest / "redline.docx").read_bytes()
    word.calls.clear()

    made = task.export(dest, "accepted", anyway=True)
    assert made == {"folder": str(dest.resolve()), "kind": "accepted", "saved": 1, "already": 0, "missing": {},
                    "anyway": True}
    # Word was handed that one copy and exactly the fixed command; the verified copy's file is not made again
    assert word.calls == [("equation.docx", ["accept_changes"])]
    assert sorted(p.name for p in dest.iterdir()) == ["equation.docx", "redline.docx"]
    assert (dest / "redline.docx").read_bytes() == checked
    run = next(p for p in (Path(task.session.out_root) / exports.EXPORT_FOLDER).iterdir()
               if p.name.endswith("-accepted-anyway"))
    # a private copy of the edited copy's bytes went to Word, and what Word saved is what was written
    copy = Path(task.working[eq])
    assert (run / "word" / "001" / "in" / copy.name).read_bytes() == copy.read_bytes()
    assert (dest / "equation.docx").read_bytes() == (run / "word" / "001" / copy.name).read_bytes()
    assert markup(dest / "equation.docx") == []
    # the independent reader's record of the file Word saved is kept with the export's run
    record = json.loads((run / "records" / f"{copy.stem}.record.json").read_text(encoding="utf-8"))
    assert record["job"] == "Word actions (Obliterate)" and record["mode"] == "shown"
    assert said(task) == (
        f"Saved 1 Word copy with every tracked change accepted to {dest.resolve()}, made by Word and UNCHECKED: the "
        "checked way could not make it, so Word accepted every tracked change on a private copy of it and saved that. "
        "No check proves the file is your edited copy with nothing else changed. equation.docx — Word's own count: "
        f"1 tracked change accepted. {CLEAN_READING} Your edited copies are as they were.")
    # the edited copies, the history, Undo and what counts as saved are as they were; nothing joined the conversation
    assert state_of(task) == before and task.session.spec is None and task.session.generated is None
    # made: no longer offered
    assert "export_anyway" not in task.view()
    with pytest.raises(ValueError) as refused:
        task.export(dest, "accepted", anyway=True)
    assert str(refused.value) == ANYWAY_NOTHING


def test_what_the_reading_finds_is_said_and_the_file_is_saved_all_the_same(tmp_path, monkeypatch, no_ai):
    """Report, do not refuse: a file Word made whose words are not the copy's, or that keeps a tracked change, is
    written and the summary says exactly that."""
    class LosesAParagraph(FakeWord):
        def __call__(self, source, actions, output, timeout=None, password=None):
            report = super().__call__(source, actions, output, timeout, password)
            if Path(source).name == "equation.docx":
                with zipfile.ZipFile(output) as z:
                    parts = {n: z.read(n) for n in z.namelist()}
                root = etree.fromstring(parts["word/document.xml"])
                first = next(p for p in root.iter(W + "p") if "".join(p.itertext()).strip())
                first.getparent().remove(first)
                parts["word/document.xml"] = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
                with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
                    for name, data in parts.items():
                        z.writestr(name, data)
            return report
    fake = LosesAParagraph()
    monkeypatch.setattr(word_actions, "run_word_actions", fake)
    monkeypatch.setattr(word_actions, "available", lambda fresh=False: True)
    task = edited(tmp_path, ["equation.docx", "styled.docx"])
    mark(task, "equation.docx", "styled.docx")
    dest = folder(tmp_path, "Clean")
    assert list(task.export(dest, "accepted")["missing"]) == ["equation.docx", "styled.docx"]
    assert "equation.docx and styled.docx are marked copies, so in Obliterate mode Export can have Word make them " \
           "anyway, unchecked" in said(task)
    made = task.export(dest, "accepted", anyway=True)
    assert made["saved"] == 2 and made["missing"] == {}
    assert sorted(p.name for p in dest.iterdir()) == ["equation.docx", "styled.docx"]
    text = said(task)
    assert text.startswith(f"Saved 2 Word copies with every tracked change accepted to {dest.resolve()}, made by Word "
                           "and UNCHECKED: the checked way could not make them, so Word accepted every tracked change "
                           "on a private copy of each and saved that. No check proves the files are your edited copies "
                           "with nothing else changed.")
    assert ("equation.docx — Word's own count: 1 tracked change accepted. The app read the file Word saved and found: "
            "1 block of text differs from your edited copy read with every tracked change accepted.") in text
    # the stand-in's Accept All leaves the change in the style definitions: said, with where
    assert "styled.docx — Word's own count: " in text and "tracked changes are still pending in " in text
    assert "the style definitions (1)" in text and CLEAN_READING not in text


def test_a_document_word_cannot_finish_gets_no_file_and_stays_offered(tmp_path, monkeypatch, no_ai):
    stopped = RuntimeError("Word stopped")
    stopped.code, stopped.reason, stopped.details = "word-com", "The file is corrupt.", {
        "stage": "word_action_accept_changes"}
    fake = FakeWord(fail={"equation.docx": stopped})
    monkeypatch.setattr(word_actions, "run_word_actions", fake)
    monkeypatch.setattr(word_actions, "available", lambda fresh=False: True)
    task = edited(tmp_path, ["equation.docx", "comment_in_deletion.docx"])
    mark(task, "equation.docx", "comment_in_deletion.docx")
    dest = folder(tmp_path, "Clean")
    task.export(dest, "accepted")
    made = task.export(dest, "accepted", anyway=True)
    assert made["saved"] == 1 and made["missing"] == {
        "equation.docx": "Word could not finish in “Accept all tracked changes” [word-com]: The file is corrupt."}
    assert [p.name for p in dest.iterdir()] == ["comment_in_deletion.docx"]
    assert said(task).endswith("Not saved: equation.docx — Word could not finish in “Accept all tracked changes” "
                               "[word-com]: The file is corrupt.")
    assert [o["copies"] for o in task.view()["export_anyway"]] == [["equation.docx"]]
    # a file Word saved that is no package at all is no file to hand over
    fake.fail.clear()
    monkeypatch.setattr(word_actions, "run_word_actions",
                        lambda source, actions, output, timeout=None, password=None:
                        Path(output).write_bytes(b"not a package") and [])
    made = task.export(dest, "accepted", anyway=True)
    assert made["saved"] == 0 and made["missing"]["equation.docx"].startswith(
        "the file Word saved could not be read at all (")
    assert said(task).startswith("Word made no file, so nothing was saved. Not saved: equation.docx — the file Word "
                                 "saved could not be read at all")


# ---- PDF (Clean), made by Word -----------------------------------------------------------------------------------------

def test_word_makes_the_clean_pdf_from_a_copy_it_deleted_the_comments_of(tmp_path, word, renderer, no_ai):
    task = edited(tmp_path, ["redline.docx", "equation.docx", "styled.docx"])
    mark(task, "equation.docx", "styled.docx")
    dest = folder(tmp_path, "PDF")
    made = task.export(dest, "pdf")
    assert made["saved"] == 1 and list(made["missing"]) == ["equation.docx", "styled.docx"]
    assert [o["kind"] for o in task.view()["export_anyway"]] == ["pdf"]
    before = state_of(task)
    renderer["calls"].clear()

    made = task.export(dest, "pdf", anyway=True)
    assert made == {"folder": str(dest.resolve()), "kind": "pdf", "saved": 2, "already": 0, "missing": {},
                    "anyway": True}
    assert word.calls == [("equation.docx", ["delete_comments", "accept_changes"]),
                          ("styled.docx", ["delete_comments", "accept_changes"])]
    assert sorted(p.name for p in dest.iterdir()) == ["equation.pdf", "redline.pdf", "styled.pdf"]
    assert all(pdf_export.check_pdf(pdf) == "" for pdf in dest.iterdir())
    # the PDFs were rendered from the files Word saved, in the export's own folder — never from a working copy
    run = next(p for p in (Path(task.session.out_root) / exports.EXPORT_FOLDER).iterdir()
               if p.name.endswith("-pdf-anyway"))
    [rendered] = renderer["calls"]
    assert [f.parent.parent for f in rendered] == [run / "word", run / "word"]
    styled = next(f for f in rendered if f.name == "styled.docx")
    assert markup(styled, COMMENTS) == [] and "word/comments.xml" not in members(styled)   # Word deleted them
    assert markup(Path(task.working[origin_of(task, "styled.docx")]), COMMENTS)            # the edited copy keeps its
    # the report beside the PDFs does not say a check passed what they were made from
    report = (run / pdf_export.REPORT_NAME).read_text(encoding="utf-8")
    assert exports.ANYWAY_PDF_BASIS in report and pdf_export.CHECKED_BASIS not in report
    text = said(task)
    assert text.startswith(f"Saved 2 clean PDFs to {dest.resolve()}, made by Word and UNCHECKED: the checked way could "
                           "not make them, so Word deleted every comment and accepted every tracked change on a "
                           "private copy of each, and the PDFs were made from that by LibreOffice. No check proves the "
                           "files are your edited copies with nothing else changed.")
    assert (f"equation.docx — Word's own count: Delete all comments: nothing to do; 1 tracked change accepted. "
            f"{CLEAN_PDF_READING}") in text
    # the comments Word deleted are not words the copy lost: only what else differs is a finding
    assert "styled.docx — Word's own count: 1 comment deleted; " in text and "block of text differ" not in text
    assert state_of(task) == before and "export_anyway" not in task.view()


def test_a_comment_word_left_in_the_file_the_pdf_is_made_from_is_said(tmp_path, monkeypatch, renderer, no_ai):
    """A wrong output, planted: a Word that reports the comments deleted and leaves them in the file it saves. The
    reading counts them itself, never taking Word's count for it."""
    class LeavesTheComments(FakeWord):
        def __call__(self, source, actions, output, timeout=None, password=None):
            kept = [a for a in actions if a != "delete_comments"]
            report = super().__call__(source, kept, output, timeout, password)
            self.calls[-1] = (Path(source).name, list(actions))
            return [{"action": "delete_comments", "count": 1, "note": ""}] + report
    fake = LeavesTheComments()
    monkeypatch.setattr(word_actions, "run_word_actions", fake)
    monkeypatch.setattr(word_actions, "available", lambda fresh=False: True)
    task = edited(tmp_path, ["styled.docx"])
    mark(task, "styled.docx")
    dest = folder(tmp_path, "PDF")
    task.export(dest, "pdf")
    made = task.export(dest, "pdf", anyway=True)
    assert made["saved"] == 1 and [p.name for p in dest.iterdir()] == ["styled.pdf"]
    text = said(task)
    assert "Word's own count: 1 comment deleted; " in text and "; 1 comment is still in it." in text
    assert CLEAN_PDF_READING not in text
    # and the comments Word did NOT delete are words the copy still has: no block is counted as differing for them
    assert "of text differ" not in text


def test_a_pdf_that_cannot_be_made_from_words_file_is_named_and_stays_offered(tmp_path, word, renderer, no_ai):
    task = edited(tmp_path, ["equation.docx"])
    mark(task, "equation.docx")
    dest = folder(tmp_path, "PDF")
    task.export(dest, "pdf")
    renderer["fail"].add("equation.docx")
    made = task.export(dest, "pdf", anyway=True)
    assert made["saved"] == 0 and made["missing"] == {
        "equation.docx": "its PDF could not be made: LibreOffice could not render it: exit status 1; stderr: boom"}
    assert list(dest.iterdir()) == [] and said(task).startswith("Word made no PDF, so nothing was saved. Not saved: ")
    assert [o["kind"] for o in task.view()["export_anyway"]] == ["pdf"]


# ---- the route, Stop, and the modes that are told nothing --------------------------------------------------------------

def window(tmp_path, names):
    task = edited(tmp_path, names, mode="safe")
    state = WindowState(task, Settings(show_obliterate=True), token=TOKEN, updates=FakeUpdates(), version="1.6.0")
    return state, TestClient(create_app(state), headers={TOKEN_HEADER: TOKEN})


def test_the_route_exports_anyway_in_obliterate_alone(tmp_path, word, no_ai):
    state, client = window(tmp_path, ["redline.docx", "equation.docx"])
    task = state.task
    mark(task, "equation.docx")
    dest = folder(tmp_path, "Clean")
    events = frames(client.post("/api/export", json={"kind": "accepted", "folder": str(dest)}))
    assert events[-1]["type"] == "done" and "anyway" not in events[-1]["state"]["export"]
    refused = client.post("/api/export", json={"kind": "accepted", "folder": str(dest), "anyway": True})
    assert refused.status_code == 400 and refused.json()["error"] == ANYWAY_OBLITERATE_ONLY
    assert client.post("/api/mode", json={"mode": "obliterate", "acknowledged": True}).status_code == 200
    [offer] = client.get("/api/state").json()["export"]["anyway"]
    assert offer == {"kind": "accepted", "label": exports.ANYWAY_LABELS["accepted"],
                     "does": exports.ANYWAY_DOES["accepted"], "copies": ["equation.docx"], "blocked": ""}
    for body, why in (({"kind": "word", "anyway": True}, ANYWAY_KINDS), ({"kind": "pdf", "anyway": True}, ANYWAY_NOTHING)):
        refused = client.post("/api/export", json={**body, "folder": str(dest)})
        assert refused.status_code == 400 and refused.json()["error"] == why
    assert word.calls == []
    events = frames(client.post("/api/export", json={"kind": "accepted", "folder": str(dest), "anyway": True}))
    done = events[-1]
    assert done["type"] == "done" and "anyway" not in done["state"]["export"]
    assert any(e.get("type") == "status" and e.get("text") == exports.WORD_MAKING for e in events)
    assert word.calls == [("equation.docx", ["accept_changes"])]
    assert sorted(p.name for p in dest.iterdir()) == ["equation.docx", "redline.docx"]
    assert "made by Word and UNCHECKED" in done["state"]["messages"][-1]["text"]
    # the job calls no AI in a conversation mode, so the server arms Stop for it
    assert "export_anyway" in server.MODEL_FREE_JOBS
    assert server.job_started_line("export_anyway", {"kind": "pdf"}) == \
        "Job export_anyway started (Export as pdf, made by Word (Export anyway))"


def test_stop_ends_it_between_documents_and_nothing_is_saved(tmp_path, monkeypatch, no_ai):
    import threading

    from spec_surgeon import stopping
    event = threading.Event()

    class StopsAfterOne(FakeWord):
        def __call__(self, source, actions, output, timeout=None, password=None):
            report = super().__call__(source, actions, output, timeout, password)
            event.set()                                         # the user presses Stop while Word has the first one
            return report
    fake = StopsAfterOne()
    monkeypatch.setattr(word_actions, "run_word_actions", fake)
    monkeypatch.setattr(word_actions, "available", lambda fresh=False: True)
    task = edited(tmp_path, ["equation.docx", "comment_in_deletion.docx"])
    mark(task, "equation.docx", "comment_in_deletion.docx")
    dest = folder(tmp_path, "Clean")
    task.export(dest, "accepted")
    before = state_of(task)
    with stopping.scope(event), pytest.raises(stopping.Stopped):
        task.perform("export_anyway", kind="accepted", folder=str(dest))
    assert [c[0] for c in fake.calls] == ["equation.docx"]       # no further document reached Word
    assert list(dest.iterdir()) == [] and said(task) == STOPPED["export"]
    assert state_of(task) == before
    assert [o["copies"] for o in task.view()["export_anyway"]] == [["equation.docx", "comment_in_deletion.docx"]]


def test_no_model_is_told_of_it_in_any_mode():
    """The choice is the menu's, never a model's: no prompt, tool, catalog or note names it."""
    told = json.dumps([compiler.system_prompt(chat, unguarded) for chat in (False, True) for unguarded in (False, True)]
                      + [codegen.system_prompt(), codegen.system_prompt(unguarded=True), exports.PROMPT_NOTE,
                         compiler.CHAT_TOOLS, catalog.op_catalog(unguarded=True), catalog.editspec_json_schema(True)],
                      default=str)
    for words in ("Export anyway", "delete_comments", "UNCHECKED", *exports.ANYWAY_LABELS.values()):
        assert words not in told, words
