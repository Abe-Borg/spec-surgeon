"""Obliterate from the to-do list (the owner's decision of 2026-11-10; CLAUDE.md, Modes).

Obliterate is the top rung of a ladder: Safe, then Default, then — for the one or two documents neither could take —
Obliterate. By then the request is known, the reason is on record and the other documents show what done looks like.
So a document that goes on the to-do list because the engine stopped on it, or because its copy failed its checks,
keeps a **case file** with its entry: the engine's record, the failed checks as the verifier named them, the copy
that failed when it is the whole of the plan, and a document of the same run that took the change.

What is pinned here:

* **Obliterate this one** (``DocumentTask.obliterate_todo``, ``POST /api/todo/obliterate``) turns the mode on for one
  document — only while Settings shows it, only with the warning acknowledged — and begins the pass over it alone;
* that conversation hears the case file and may read the worked example, and no spec can name the example;
* **Use the copy that failed, marked UNVERIFIED** (``use_failed_copy``) takes the kept copy as it is, with no AI call,
  after the source guard's identity is checked again; it is one change, undone by Undo;
* Safe and Default are told and shown exactly what they were before.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fake_claude import FakeClient, resp, text, tool
from make_fixtures import build
from spec_surgeon import batch as batch_module, verify
from spec_surgeon.compiler import _Batch, compose_submission
from spec_surgeon.document_task import (EXAMPLE_AFTER, EXAMPLE_BEFORE, FAILED_COPY_TAKEN, OBLITERATE_ON,
                                         USE_FAILED_COPY, WHY_FAILED_COPY, WORK_TODO, DocumentTask,
                                         _case_of_exception, _restored_case)
from spec_surgeon.gui import Session
from spec_surgeon.settings import Settings
from spec_surgeon.verify import CheckResult
from spec_surgeon.web.server import TOKEN_HEADER, WindowState, create_app
from test_document_task import REQUEST, SPEC, document
from test_web_server import TOKEN, FakeUpdates

V2 = "V2 (Text preservation)"


def documents(tmp_path, *names):
    """One document per name; the request (``SPEC``: the FIRE SUPPRESSION title in bold) changes each of them."""
    return [document(tmp_path / name) for name in names]


def fail_v2(monkeypatch, *names):
    """V2 fails on the named documents' copies, in the preview and in the run; the others pass as they are."""
    for module in (verify, batch_module):
        real = module.run_suite

        def failing(*args, _real=real, **kwargs):
            record = _real(*args, **kwargs)
            if Path(args[3]).name in names:
                record.set(CheckResult("V2", "Text preservation", "FAIL", "planted verification failure"))
            return record

        monkeypatch.setattr(module, "run_suite", failing)


def submit(spec=SPEC, tid="s1"):
    return resp(tool("submit_editspec", {"spec": spec}, tid=tid))


def task_for(tmp_path, fake, **kwargs):
    kwargs.setdefault("obliterate_allowed", True)
    return DocumentTask(Session(client=fake, out_root=tmp_path / "out", visual_check=False), render=False, **kwargs)


def said(task):
    return [m["text"] for m in task.messages if m["role"] == "app"]


def straggler(tmp_path, monkeypatch, *more, **kwargs):
    """Default mode over good.docx and bad.docx: bad's preview fails V2, good's copy is created, and bad waits on the
    to-do list with its case file."""
    good, bad = documents(tmp_path, "good.docx", "bad.docx")
    fail_v2(monkeypatch, "bad.docx")
    fake = FakeClient(submit(), *more)
    task = task_for(tmp_path, fake, **kwargs)
    task.attach([good, bad])
    view = task.perform("send", text=REQUEST)
    assert view["stage"] == "preview" and [item["name"] for item in view["set_aside"]] == ["bad.docx"]
    task.perform("primary", token=view["token"])
    assert task.stage == "done" and list(task.results) == [str(good)] and list(task.todo) == [str(bad)]
    return task, fake, good, bad


# ---- the case file --------------------------------------------------------------------------------------------------

def test_a_failed_preview_sample_keeps_its_checks_its_copy_and_a_worked_example(tmp_path, monkeypatch):
    task, _fake, good, bad = straggler(tmp_path, monkeypatch)
    case = task.todo[str(bad)]["case"]
    assert case["checks"] == [{"id": "V2", "name": "Text preservation", "summary": "planted verification failure"}]
    copy = case["copy"]
    assert copy["marks"] == [V2] and Path(copy["path"]).is_file() and copy["source"] == str(bad)
    import hashlib
    assert hashlib.md5(Path(copy["path"]).read_bytes()).hexdigest() == copy["md5"]
    stat = bad.stat()
    assert copy["identity"] == [stat.st_size, stat.st_mtime_ns, hashlib.md5(bad.read_bytes()).hexdigest()]
    # the worked example: the document of the same run that took the change, as it was and as its copy
    assert case["sibling"] == {"name": "good.docx", "before": str(good), "copy": task.results[str(good)]}
    # the journal keeps it, and a case that does not read is dropped without losing the entry
    journal = tmp_path / "journal.json"
    task.journal = journal
    task.save()
    again = task_for(tmp_path, FakeClient(), journal=journal)
    assert again.restore() and again.todo[str(bad)]["case"] == case
    data = json.loads(journal.read_text(encoding="utf-8"))
    data["todo"][str(bad)]["case"]["checks"] = "not a list"
    journal.write_text(json.dumps(data), encoding="utf-8")
    broken = task_for(tmp_path, FakeClient(), journal=journal)
    assert broken.restore() and "case" not in broken.todo[str(bad)] and broken.todo[str(bad)]["why"] == "failed"


def test_a_refused_document_keeps_the_engines_record_and_no_copy(tmp_path):
    from test_continuous_recovery import STYLE_SPEC, _two_documents
    source, easy = _two_documents(tmp_path)
    task = task_for(tmp_path, FakeClient(submit(STYLE_SPEC)))
    task.attach([easy, source])
    view = task.perform("send", text="Make the title bold")
    task.perform("primary", token=view["token"])
    case = task.todo[str(source)]["case"]
    assert case["code"] and "MissingStyle" in case["reason"] and case["checks"] == [] and case["copy"] is None
    assert case["sibling"]["name"] == "easy.docx"
    view = task.view()
    assert view["todo"][0]["obliterate"] == "" and "failed_copy" not in view["todo"][0]


def test_a_run_result_that_failed_keeps_the_copy_the_run_held_back(tmp_path, monkeypatch):
    # Safe mode: no preview failure (only the run's suite is planted), so the copy fails at the run and is held back
    good, bad = documents(tmp_path, "good.docx", "bad.docx")
    real = batch_module.run_suite

    def failing(*args, **kwargs):
        record = real(*args, **kwargs)
        if Path(args[3]).name == "bad.docx":
            record.set(CheckResult("V2", "Text preservation", "FAIL", "planted at the run"))
        return record

    monkeypatch.setattr(batch_module, "run_suite", failing)
    task = DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=False, mode="safe",
                        obliterate_allowed=True)
    task.attach([good, bad])
    task.perform("recipe", recipe="remove_highlight", inputs={})
    task.perform("primary", token=task.action_token())
    [left] = task.left_untouched
    copy = left["case"]["copy"]
    assert left["name"] == "bad.docx" and copy["marks"] == [V2]
    assert Path(copy["path"]).parent.name == "failed" and Path(copy["path"]).is_file()
    assert left["case"]["sibling"]["name"] == "good.docx"
    # handed to Default mode, the entry carries it; straight to Obliterate from there, the copy can be taken
    task.hand_off([left["path"]])
    assert task.mode == "default" and task.todo[str(bad)]["case"]["copy"] == copy
    task.obliterate_todo(str(bad), acknowledged=True)
    assert task.mode == "obliterate" and USE_FAILED_COPY in task.view()["chips"]
    view = task.perform("send", text=USE_FAILED_COPY, chip=True)
    assert view["unverified"][str(bad)] == [V2] and Path(task.results[str(bad)]).read_bytes() == \
        Path(copy["path"]).read_bytes()


def test_a_chains_failed_file_keeps_no_copy(tmp_path):
    from types import SimpleNamespace
    from spec_surgeon.document_task import _case_of_result
    out = tmp_path / "failed.docx"
    out.write_bytes(b"x")
    check = CheckResult("V2", "Text preservation", "FAIL", "planted")
    identity = SimpleNamespace(path=str(tmp_path / "a.docx"), size=1, mtime_ns=2, md5="m")
    result = SimpleNamespace(exception=None, record=SimpleNamespace(checks=[check]), output=out, output_md5="abc",
                             identity=identity)
    assert _case_of_result(result)["copy"]["marks"] == [V2]
    result.stage_status = ["PASS", "FAIL"]            # a chain's file: what it left is not the plan's copy
    assert _case_of_result(result)["copy"] is None and _case_of_result(result)["checks"][0]["id"] == "V2"
    # a copy the source guard refused (the source moved while the run worked) is never kept: Obliterate's own run
    # does not deliver that one either
    del result.stage_status
    result.record.checks.append(CheckResult("V9", "Overwrite guard", "FAIL", "SOURCE DRIFTED during the run"))
    moved = _case_of_result(result)
    assert moved["copy"] is None and [c["id"] for c in moved["checks"]] == ["V2", "V9"]


def test_a_case_file_bounds_what_it_keeps_and_survives_anything_in_the_record():
    huge = {"code": "textbox", "reason": "r" * 10000, "op": "set_font", "details": {"found": ["x" * 10000]}}
    case = _case_of_exception(huge)
    assert len(case["reason"]) == 2000 and len(case["details"]) == 4000 and case["op"] == "set_font"
    assert _restored_case(case) == case
    assert _case_of_exception(None)["code"] == "" and _case_of_exception({"details": {1, 2}})["details"]
    for wrong in (None, [], {"code": 1}, {"checks": [1]}, {"checks": [{"id": 1}]}):
        assert _restored_case(wrong) is None
    # a copy or a worked example that does not read is dropped; the rest of the case stays
    kept = _restored_case({**case, "copy": {"path": "p"}, "sibling": {"name": 1}})
    assert kept["copy"] is None and kept["sibling"] is None and kept["code"] == "textbox"


# ---- Obliterate this one --------------------------------------------------------------------------------------------

def test_obliterate_this_one_needs_the_setting_and_the_warning_and_scopes_the_pass_to_that_document(tmp_path,
                                                                                                    monkeypatch):
    good, bad, worse = documents(tmp_path, "good.docx", "bad.docx", "worse.docx")
    fail_v2(monkeypatch, "bad.docx", "worse.docx")
    fake = FakeClient(submit())
    task = task_for(tmp_path, fake, obliterate_allowed=False)
    task.attach([good, bad, worse])
    view = task.perform("send", text=REQUEST)
    task.perform("primary", token=view["token"])
    assert sorted(Path(k).name for k in task.todo) == ["bad.docx", "worse.docx"]
    # hidden: the entries read as they always did, and the action is refused
    assert all(set(item) == {"path", "name", "why", "reason"} for item in task.view()["todo"])
    with pytest.raises(ValueError, match="hidden"):
        task.obliterate_todo(str(bad), acknowledged=True)
    task.obliterate_allowed = True
    assert all(item["obliterate"] == "" and item["failed_copy"] == [V2] for item in task.view()["todo"])
    with pytest.raises(ValueError, match="not on your to-do list"):
        task.obliterate_todo(str(good), acknowledged=True)
    for ack in (False, None, "yes", 1):
        with pytest.raises(ValueError, match="warning"):
            task.obliterate_todo(str(bad), acknowledged=ack)
        assert task.mode == "default" and task.focus is None and len(task.todo) == 2     # nothing moved
    calls = len(fake.requests)
    task.obliterate_todo(str(bad), acknowledged=True)
    assert len(fake.requests) == calls                               # the app's own step: no model call
    assert task.mode == "obliterate" and task.session.unguarded
    assert task.focus["documents"] == [str(bad)] and list(task.todo) == [str(worse)]   # the other one still waits
    assert task.documents() == [bad] and task.decisions == [REQUEST] and task.stage == "resume"
    texts = said(task)
    assert OBLITERATE_ON in texts and any(t.startswith("Working on bad.docx, from your to-do list.") for t in texts)
    assert texts[-1].startswith(f"A copy of bad.docx was made and failed {V2}, and it is kept: ")
    assert f"“{USE_FAILED_COPY}” takes it as it is, with no AI call." in texts[-1]
    view = task.view()
    assert view["chips"][0] == USE_FAILED_COPY
    assert view["failed_copies"] == [{"path": str(bad), "name": "bad.docx", "failed": [V2]}]
    assert view["todo"][0]["name"] == "worse.docx" and "Finish the pass" in view["todo"][0]["obliterate"]
    with pytest.raises(ValueError, match="Finish the pass"):
        task.obliterate_todo(str(worse))
    # already in Obliterate, no second warning is asked: drop the pass, then the other document
    task.discard_preview()
    assert task.focus is None and task.todo[str(bad)]["case"]["copy"]["marks"] == [V2]   # back as it was, case kept
    task.obliterate_todo(str(worse))
    assert task.focus["documents"] == [str(worse)] and task.mode == "obliterate"


def test_the_conversation_hears_the_case_file_and_reads_the_worked_example_only_in_obliterate(tmp_path, monkeypatch):
    task, fake, good, bad = straggler(tmp_path, monkeypatch, submit(tid="s2"))
    # Default's pass over the list is given what it always was: the entry's sentence, no case file, no example
    task.perform("send", text=WORK_TODO)
    assert "FROM THE USER'S TO-DO LIST" in task._focus_context() and "CASE FILE" not in task._focus_context()
    assert task._focus_examples() == {} and task._case_note() == ""
    task.discard_preview()
    # Obliterate: the same pass hears the case file, and its tools may read the example
    task.obliterate_todo(str(bad), acknowledged=True)
    task.perform("send", text="Do whatever it takes.")
    told = fake.requests[1]["messages"][0]["content"]
    note = told.split("<app_note>")[1].split("</app_note>")[0]
    assert "FROM THE USER'S TO-DO LIST" in note and "CASE FILE (what the app recorded" in note
    assert f"Its copy was made and failed: {V2}: planted verification failure" in note
    assert f"“{USE_FAILED_COPY}”" in note
    before, after = EXAMPLE_BEFORE.format(name="good.docx"), EXAMPLE_AFTER.format(name="good.docx")
    assert f"“{before}” (as it was) and “{after}” (its copy)" in note
    batch = task.session.conversation.batch
    assert batch.examples == {before: good, after: Path(task.results[str(good)])}
    # the tools read it; THE BATCH does not list it, and no spec can use it
    assert batch.read_paragraphs(after)["file"] == after and batch.list_parts(before)["file"] == before
    assert "good.docx" not in told.split("<batch>")[1].split("</batch>")[0]
    assert list(batch.files) == ["bad.docx"]
    # the custom method's writer starts from the same note
    assert "THE APP'S NOTE ON THESE DOCUMENTS" in task.session.conversation.brief()
    assert "CASE FILE" in task.session.conversation.brief()


def test_a_worked_example_is_read_only_no_spec_can_name_it(tmp_path):
    doc, example = documents(tmp_path, "doc.docx", "example.docx")
    batch = _Batch([doc], None)
    batch.add_examples({"EXAMPLE after — example.docx": example, "doc.docx": example, "template": example,
                        "gone": tmp_path / "nowhere.docx"})
    assert list(batch.examples) == ["EXAMPLE after — example.docx"]      # a batch label, "template", a missing file: no
    assert batch.pkg("EXAMPLE after — example.docx").name == str(example)
    assert [p["path"] for p in batch.source_pins] == [str(doc.resolve()), str(example.resolve())]
    # as a template of a plan it is refused: it is not a file of this request
    spec = {"request": "r", "summary": "s", "mode": "formatting",
            "ops": [{"op": "apply_style", "select": {"paragraphs": {"text_regex": "^SECTION"}},
                     "params": {"style": "Heading 1", "template": str(example)}}]}
    _planned, errors = compose_submission(spec, batch, ["r"], unguarded=True)
    assert errors and "example.docx" in " ".join(errors)
    # a pinned example that changed is refused like any source that moved
    example.write_bytes(build("21 13 99", "CHANGED"))
    with pytest.raises(ValueError, match="source changed"):
        batch.pkg("EXAMPLE after — example.docx")


# ---- the free rung --------------------------------------------------------------------------------------------------

def test_taking_the_copy_that_failed_is_one_change_with_no_ai_call(tmp_path, monkeypatch):
    task, fake, good, bad = straggler(tmp_path, monkeypatch)
    before = bad.read_bytes()
    kept = task.todo[str(bad)]["case"]["copy"]
    failed_bytes = Path(kept["path"]).read_bytes()
    task.obliterate_todo(str(bad), acknowledged=True)
    calls, generation = len(fake.requests), task.generation
    view = task.perform("send", text=USE_FAILED_COPY, chip=True)
    assert len(fake.requests) == calls                                # no AI was used
    assert view["stage"] == "done" and task.focus is None and task.todo == {} and task.decisions == []
    taken = Path(task.results[str(bad)])
    assert taken.read_bytes() == failed_bytes and taken.parent.parent.name == "accepted"
    assert Path(kept["path"]).read_bytes() == failed_bytes            # the preview's own copy is not moved
    assert bad.read_bytes() == before                                 # copies only
    assert task.unverified[str(bad)] == [V2] and view["unverified"] == {str(bad): [V2]}
    assert task.working[str(bad)] == str(taken.resolve()) and task.generation == generation + 1
    assert USE_FAILED_COPY not in view["chips"]
    assert said(task)[-1].startswith(f"Taken as it is: bad.docx, which failed {V2}.")
    assert "No AI was used." in said(task)[-1]
    record = json.loads((taken.parent.parent / "accepted.json").read_text(encoding="utf-8"))
    assert record["request"] == REQUEST and record["taken"][0]["failed"] == [V2]
    assert record["taken"][0]["md5"] == kept["md5"] and "UNVERIFIED" in record["note"]
    # one change: listed under What you've done so far, never a recipe step, in the copy's history, and undone by Undo
    done = view["working"]["done"]
    named = f"{REQUEST.rstrip('.')} — {FAILED_COPY_TAKEN}"
    assert done[-1]["request"] == named and done[-1]["documents"] == 1 and done[-1]["why_not"] == WHY_FAILED_COPY
    assert view["working"]["undo"] == named and done[0]["request"] == REQUEST
    assert task.applied[str(bad)] == task.applied[str(good)]          # the plan's own sentences
    assert any("bad.docx was delivered UNVERIFIED" in line for line in task._history_in_scope())
    task.undo()
    assert said(task)[-1].startswith(f"Undid “{named}”.")
    assert str(bad) not in task.working and str(bad) not in task.unverified and task.results == {
        str(good): task.results[str(good)]}
    assert taken.is_file()                                            # nothing is deleted


def test_the_copy_is_refused_when_anything_it_stands_on_moved(tmp_path, monkeypatch):
    task, fake, good, bad = straggler(tmp_path, monkeypatch)
    kept = dict(task.todo[str(bad)]["case"]["copy"])
    # outside Obliterate, and outside a pass, it is not offered and not taken
    with pytest.raises(ValueError, match="Obliterate mode's"):
        task.use_failed_copy()
    task.set_mode("obliterate", acknowledged=True)
    with pytest.raises(ValueError, match="from your to-do list first"):
        task.use_failed_copy()
    task.obliterate_todo(str(bad))
    # the copy changed since it was checked
    original = Path(kept["path"]).read_bytes()
    Path(kept["path"]).write_bytes(original + b" ")
    task.perform("send", text=USE_FAILED_COPY)
    assert "has changed since it was checked" in said(task)[-1] and str(bad) not in task.results
    assert task.focus["documents"] == [str(bad)]                      # nothing moved: the pass is as it was
    Path(kept["path"]).write_bytes(original)
    # the source moved: the guard's identity, as for any delivery
    saved = bad.read_bytes()
    bad.write_bytes(build("21 13 14", "SOMETHING ELSE"))
    with pytest.raises(ValueError, match="changed since"):
        task.use_failed_copy()
    assert str(bad) not in task.results and USE_FAILED_COPY in task.view()["chips"]
    bad.write_bytes(saved)
    # the copy is gone: no longer offered
    Path(kept["path"]).unlink()
    assert USE_FAILED_COPY not in task.view()["chips"]
    with pytest.raises(ValueError, match="no longer where it was kept"):
        task.use_failed_copy()
    assert task.results == {str(good): task.results[str(good)]} and task.unverified == {}


def test_a_document_that_moved_on_since_no_longer_fits_its_failed_copy(tmp_path, monkeypatch):
    task, fake, good, bad = straggler(tmp_path, monkeypatch)
    # the document gets another copy first (put back, then a run that passes): the kept copy was made from the original
    monkeypatch.undo()
    task.session.client = FakeClient(submit(tid="s2"))
    task.put_back(bad)
    view = task.perform("send", text="Remove the highlighting again")
    task.perform("primary", token=view["token"])
    assert str(bad) in task.working
    kept = {"path": str(tmp_path / "x.docx"), "md5": "0", "source": str(bad), "identity": [1, 2, "m"], "marks": [V2]}
    Path(kept["path"]).write_bytes(b"x")
    assert not task._failed_copy_offered(str(bad), kept)
    assert "has moved on since that copy was made" in task._failed_copy_problem(str(bad), kept)


def test_a_pass_of_several_takes_the_copies_it_has_and_puts_the_rest_back(tmp_path, monkeypatch):
    good, bad, worse = documents(tmp_path, "good.docx", "bad.docx", "worse.docx")
    fail_v2(monkeypatch, "bad.docx", "worse.docx")
    task = task_for(tmp_path, FakeClient(submit()))
    task.attach([good, bad, worse])
    view = task.perform("send", text=REQUEST)
    task.perform("primary", token=view["token"])
    task.todo[str(worse)]["case"]["copy"] = None                      # worse.docx: no copy was kept
    task.set_mode("obliterate", acknowledged=True)
    task.perform("send", text=WORK_TODO)
    assert task.focus["documents"] == [str(bad), str(worse)] and USE_FAILED_COPY in task.view()["chips"]
    task.perform("send", text=USE_FAILED_COPY, chip=True)
    assert str(bad) in task.working and task.unverified[str(bad)] == [V2]
    assert list(task.todo) == [str(worse)] and task.todo[str(worse)]["why"] == "failed"   # back as it was
    assert "Not taken: No copy of worse.docx was kept" in said(task)[-2]
    assert task.runs[-1]["not"][str(worse)] == "set aside"
    task.undo()
    assert str(bad) not in task.working and list(task.todo) == [str(worse)]   # Undo leaves the other one waiting


def test_default_mode_never_takes_a_failed_copy_and_shows_no_chip(tmp_path, monkeypatch):
    task, fake, good, bad = straggler(tmp_path, monkeypatch, resp(text("Nothing to do."), stop="end_turn"))
    task.perform("send", text=WORK_TODO)
    assert task.mode == "default" and USE_FAILED_COPY not in task.view()["chips"]
    assert task.view()["failed_copies"] == []
    calls = len(fake.requests)
    task.perform("send", text=USE_FAILED_COPY)                        # typed anyway: refused in the chat, no model call
    assert len(fake.requests) == calls and "Obliterate mode's" in said(task)[-1]
    assert str(bad) not in task.results and task.focus["documents"] == [str(bad)]
    # and what Default's pass tells the model is what it always told it: the entry's sentence, nothing of the case
    task.perform("send", text="Why did it fail?")
    told = fake.requests[calls]["messages"][0]["content"]
    assert "FROM THE USER'S TO-DO LIST" in told and "bad.docx: Its preview did not pass its checks" in told
    assert "CASE FILE" not in told and "EXAMPLE" not in told and USE_FAILED_COPY not in told
    assert task.session.conversation is None or task.session.conversation.batch.examples == {}


# ---- the window -----------------------------------------------------------------------------------------------------

def test_the_window_routes_obliterate_this_one_and_never_keeps_the_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("SPEC_SURGEON_CONFIG_DIR", str(tmp_path / "config"))
    task, fake, good, bad = straggler(tmp_path, monkeypatch)
    state = WindowState(task, Settings(show_obliterate=True), token=TOKEN, updates=FakeUpdates(), version="1.18.0")
    client = TestClient(create_app(state), headers={TOKEN_HEADER: TOKEN})
    item = client.get("/api/state").json()["todo"][0]
    assert item["name"] == "bad.docx" and item["obliterate"] == "" and item["failed_copy"] == [V2]
    refused = client.post("/api/todo/obliterate", json={"path": str(bad)})
    assert refused.status_code == 400 and "warning" in refused.json()["error"]
    assert client.post("/api/todo/obliterate", json={"path": str(good), "acknowledged": True}).status_code == 400
    assert client.post("/api/todo/obliterate", json=["nope"]).status_code == 400
    view = client.post("/api/todo/obliterate", json={"path": str(bad), "acknowledged": True}).json()
    assert view["mode"] == "obliterate" and view["focus"] == [str(bad)] and view["todo"] == []
    assert view["chips"][0] == USE_FAILED_COPY and view["failed_copies"][0]["name"] == "bad.docx"
    from spec_surgeon.settings import load_settings
    assert load_settings().mode == "default"                          # never kept: the next launch opens in Default


# ---- the custom method's writer -------------------------------------------------------------------------------------

def test_the_methods_writer_reads_the_worked_example_and_the_method_never_receives_it(tmp_path):
    """The case file's worked example reaches the writer's read tools by label, in Obliterate only; it is no sample
    and no attachment, so the method itself is never given it."""
    from make_fixtures import build
    from spec_surgeon import codegen
    from test_codegen import GOOD_OP
    from test_obliterate import FREE_HEADER
    d = tmp_path / "specs"
    d.mkdir()
    sample, example = d / "21 00 00 – SPEC 0.docx", d / "21 01 00 – SPEC 1.docx"
    sample.write_bytes(build("21 00 00", "SPEC 0"))
    example.write_bytes(build("21 01 00", "SPEC 1"))
    label = EXAMPLE_AFTER.format(name=example.name)
    fake = FakeClient(resp(tool("show_part_text", {"file": label, "part": "word/document.xml"}, "r1")),
                      resp(tool("show_part_text", {"file": "EXAMPLE after: nowhere.docx", "part": "x"}, "r2")),
                      resp(tool("submit_op", {"source": FREE_HEADER}, "s1")), resp(tool("confirm_op", {}, "c1")))
    g = codegen.generate_op("widen it", "widen it", [sample], tmp_path / "cg", client=fake, unguarded=True,
                            examples={label: example})
    assert g.passed, g.attempts
    report = fake.requests[3]["messages"][-1]["content"][0]["content"]
    assert sample.name in report and example.name not in report                      # an example is no sample
    first = fake.requests[0]["messages"][0]["content"]
    assert "WORKED EXAMPLES (read-only" in first and label in first
    read = fake.requests[1]["messages"][-1]["content"][0]
    assert not read.get("is_error") and "SPEC 1" in read["content"]                  # the example was read
    missed = fake.requests[2]["messages"][-1]["content"][0]
    assert missed.get("is_error") and label in missed["content"]                     # a mistyped label names the examples
    assert g.attachments == []                                                       # never a pkg.inputs entry
    # outside Obliterate the writer gets none, whatever is passed
    plain = FakeClient(resp(tool("show_part_text", {"file": label, "part": "word/document.xml"}, "r1")),
                       resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}})))
    codegen.generate_op("widen it", "widen it", [sample], tmp_path / "cg2", client=plain, examples={label: example})
    assert "WORKED EXAMPLES" not in plain.requests[0]["messages"][0]["content"]
    assert plain.requests[1]["messages"][-1]["content"][0].get("is_error")
