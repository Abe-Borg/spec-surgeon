"""Export → Save the copies…: the one place a copy is written where the user chose. The working copies go into the
chosen folder under their planned names, byte for byte the copies the checker passed, never over an input; a copy
already there unchanged is that copy, saved; a different file under its name is left alone and the copy numbered.
The window says which copies are unsaved, asks once before a close would delete them, keeps the export folder for
the next dialog, and the journal keeps what was saved where."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from make_fixtures import build
from spec_surgeon.document_task import DocumentTask
from spec_surgeon.gui import Session
from spec_surgeon.web.server import Shell
from spec_surgeon.web.shell import on_closing, unsaved_copies_question
from test_going_back import TITLE, press, safe_task
from test_web_server import document, frames, window


@pytest.fixture
def files(tmp_path) -> list:
    out = []
    for num, title in (("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"), ("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS")):
        path = tmp_path / "Specs" / f"{num}.docx"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(build(num, title))
        out.append(path)
    return out


def _run(tmp_path, files, journal=None) -> DocumentTask:
    task = safe_task(tmp_path, journal=journal)
    task.attach(files)
    press(task, "remove_highlight")
    assert len(task.working) == 2
    return task


def _names(folder: Path) -> list:
    return sorted(p.name for p in folder.iterdir())


# ---- the task ----------------------------------------------------------------------------------------------------

def test_export_writes_the_working_copies_verbatim_into_the_chosen_folder(tmp_path, files):
    task = _run(tmp_path, files)
    assert task.unsaved_copies() == list(task.working.values()) and task.export_folder == ""
    view = task.view()
    assert view["unsaved"] == list(task.working.values()) and view["copies"] == 2 and view["exported"] == {}
    dest = tmp_path / "Saved"
    dest.mkdir()
    made = task.export(dest)
    assert made == {"folder": str(dest.resolve()), "saved": 2, "already": 0}
    for current in task.working.values():
        saved = dest / Path(current).name
        assert saved.read_bytes() == Path(current).read_bytes() and task.exported[current] == str(saved)
    assert task.unsaved_copies() == [] and task.export_folder == str(dest.resolve())
    assert task.messages[-1]["text"] == f"Saved 2 copies to {dest.resolve()}."
    view = task.view()
    assert view["unsaved"] == [] and view["copies"] == 2 and view["export_folder"] == str(dest.resolve())
    assert view["exported"] == task.exported
    names = _names(dest)
    # saving again writes nothing new: the same bytes are already there
    assert task.export(dest) == {"folder": str(dest.resolve()), "saved": 0, "already": 2} and _names(dest) == names
    assert task.messages[-1]["text"] == f"Saved 2 copies to {dest.resolve()} (2 already there, unchanged)."
    # someone else's file under a copy's name is left alone: the copy is numbered beside it
    first = next(iter(task.working.values()))
    name = Path(first).name
    (dest / name).write_bytes(b"someone else's")
    made = task.export(dest)
    numbered = dest / f"{Path(name).stem} (2){Path(name).suffix}"
    assert made["saved"] == 1 and made["already"] == 1 and numbered.read_bytes() == Path(first).read_bytes()
    assert (dest / name).read_bytes() == b"someone else's" and task.exported[first] == str(numbered)
    assert _names(dest) == sorted(names + [numbered.name])


def test_export_refuses_a_folder_where_a_copy_would_land_on_an_input(tmp_path, files):
    task = _run(tmp_path, files)
    originals = {p: p.read_bytes() for p in files}
    # the originals' folder: these copies carry their originals' names
    with pytest.raises(ValueError, match="one of the files this conversation works on"):
        task.export(files[0].parent)
    # the run's own outputs folder: the copies themselves
    with pytest.raises(ValueError, match="one of the files this conversation works on"):
        task.export(Path(next(iter(task.working.values()))).parent)
    assert {p: p.read_bytes() for p in files} == originals and task.exported == {} and task.export_folder == ""
    assert len(task.unsaved_copies()) == 2 and _names(files[0].parent) == ["21 13 13.docx", "21 13 16.docx"]
    with pytest.raises(ValueError, match="is not a folder"):
        task.export(tmp_path / "missing")
    with pytest.raises(ValueError, match="no edited copies"):
        safe_task(tmp_path / "other").export(tmp_path)


def test_a_copy_that_is_gone_refuses_the_whole_export(tmp_path, files):
    task = _run(tmp_path, files)
    dest = tmp_path / "Saved"
    dest.mkdir()
    Path(list(task.working.values())[1]).unlink()
    with pytest.raises(ValueError, match="no longer where it was made"):
        task.export(dest)
    assert _names(dest) == [] and task.exported == {}


def test_the_journal_keeps_what_was_saved_where(tmp_path, files):
    journal = tmp_path / "conversation.json"
    task = _run(tmp_path, files, journal=journal)
    dest = tmp_path / "Saved"
    dest.mkdir()
    task.export(dest)
    data = json.loads(journal.read_text(encoding="utf-8"))
    assert data["exported"] == task.exported and data["export_folder"] == str(dest.resolve())
    restored = safe_task(tmp_path, journal=journal)
    assert restored.restore()
    assert restored.exported == task.exported and restored.export_folder == task.export_folder
    assert restored.unsaved_copies() == []
    # a journal from before Export has nothing saved
    data.pop("exported")
    data.pop("export_folder")
    journal.write_text(json.dumps(data), encoding="utf-8")
    again = safe_task(tmp_path, journal=journal)
    assert again.restore() and again.exported == {} and again.export_folder == ""
    assert again.unsaved_copies() == list(again.working.values())


def test_a_new_runs_copies_are_unsaved_again_and_undo_remembers_the_saved_ones(tmp_path, files):
    task = _run(tmp_path, files)
    dest = tmp_path / "Saved"
    dest.mkdir()
    task.export(dest)
    press(task, "title_property", TITLE)
    assert task.generation == 2 and task.unsaved_copies() == list(task.working.values())
    assert task.view()["unsaved"] == list(task.working.values())
    task.undo()
    assert task.generation == 1 and task.unsaved_copies() == []        # the copies from before were saved
    task.start_over()
    assert task.unsaved_copies() == [] and task.view()["copies"] == 0  # nothing to save on the originals


# ---- the window ----------------------------------------------------------------------------------------------------

class FolderShell(Shell):
    """A native shell for the tests: the folder dialog answers what it is given; the question, as told."""

    def __init__(self, folder=None, answer=True):
        self.folder, self.answer = folder, answer
        self.starts, self.questions = [], []

    def pick_folder(self, start=""):
        self.starts.append(start)
        return self.folder

    def confirm(self, title, message):
        self.questions.append((title, message))
        return self.answer


def _safe_run(client, tmp_path):
    """A Safe-mode button through the routes: two copies delivered."""
    for num, title in (("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"), ("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS")):
        (tmp_path / "Specs").mkdir(exist_ok=True)
        (tmp_path / "Specs" / f"{num}.docx").write_bytes(build(num, title))
    client.post("/api/mode", json={"mode": "safe"})
    client.post("/api/attach", json={"paths": [str(p) for p in sorted((tmp_path / "Specs").glob("*.docx"))]})
    with client.stream("POST", "/api/recipe", json={"recipe": "remove_highlight", "inputs": {}}) as r:
        done = frames(r)[-1]
    assert done["type"] == "done" and done["state"]["stage"] == "preview", done
    with client.stream("POST", "/api/primary", json={"token": done["state"]["action"]["token"]}) as r:
        view = frames(r)[-1]["state"]
    assert view["stage"] == "done" and len(view["results"]) == 2
    return view


def test_the_export_route_saves_through_the_folder_dialog_and_remembers_the_folder(tmp_path, monkeypatch):
    from spec_surgeon.settings import load_settings
    monkeypatch.setenv("SPEC_SURGEON_CONFIG_DIR", str(tmp_path / "config"))
    state, client = window(tmp_path)
    assert client.post("/api/export").status_code == 400                   # nothing to save yet
    view = _safe_run(client, tmp_path)
    assert view["export"] == {"folder": "", "unsaved": 2, "copies": 2}
    assert all(r["saved_to"] == "" for r in view["results"])
    dest = tmp_path / "Saved"
    dest.mkdir()
    state.shell = FolderShell(str(dest))
    view = client.post("/api/export").json()
    assert state.shell.starts == [str(Path.home())]                        # the first dialog opens at home
    assert view["export"] == {"folder": str(dest.resolve()), "unsaved": 0, "copies": 2}
    assert {r["saved_to"] for r in view["results"]} == {str(dest / r["name"]) for r in view["results"]}
    assert _names(dest) == sorted(r["name"] for r in view["results"])
    assert view["messages"][-1]["text"].startswith("Saved 2 copies to ")
    assert load_settings().export_folder == str(dest.resolve())            # kept for the next dialog
    assert client.get("/api/settings").json()["export_folder"] == str(dest.resolve())
    state.shell = FolderShell("")                                          # the dialog dismissed: nothing happens
    assert client.post("/api/export").json() == {"cancelled": True} and state.shell.starts == [str(dest.resolve())]
    state.shell = Shell()                                                  # the browser: no dialog, a folder in the body
    assert client.post("/api/export").status_code == 501
    other = tmp_path / "Other"
    other.mkdir()
    view = client.post("/api/export", json={"folder": str(other)}).json()
    assert view["export"]["folder"] == str(other.resolve()) and _names(other) == _names(dest)
    refused = client.post("/api/export", json={"folder": str(tmp_path / "Specs")})
    assert refused.status_code == 400 and "one of the files this conversation works on" in refused.json()["error"]


def test_the_export_route_waits_for_a_running_job(tmp_path):
    import threading as th
    from fake_claude import FakeClient, resp, tool
    from test_document_task import REQUEST, SPEC
    started, release = th.Event(), th.Event()

    class Slow(FakeClient):
        def _stream(self, **kw):
            started.set()
            release.wait(10)
            return super()._stream(**kw)
    state, client = window(tmp_path, Slow(resp(tool("submit_editspec", {"spec": SPEC}))))
    client.post("/api/attach", json={"paths": [str(document(tmp_path / "Spec.docx"))]})
    state.start_job("send", text=REQUEST)
    assert started.wait(10) and state.busy
    assert client.post("/api/export", json={"folder": str(tmp_path)}).status_code == 409
    release.set()
    state.thread.join(30)


def test_closing_asks_once_before_unsaved_copies_are_deleted(tmp_path):
    state, client = window(tmp_path)
    state.shell = FolderShell(answer=False)
    assert on_closing(state) is True and state.close_confirmed and state.shell.questions == []   # nothing to lose
    state.close_confirmed = False
    _safe_run(client, tmp_path)
    assert len(state.task.unsaved_copies()) == 2
    assert on_closing(state) is False and not state.close_confirmed
    title, question = state.shell.questions[-1]
    assert title == "Copies not saved" and question == unsaved_copies_question(2)
    assert question.startswith("2 edited copies have not been saved. Closing deletes them")
    assert "Export → Save the copies…" in question and "Cancel goes back" in question
    assert unsaved_copies_question(1).startswith("1 edited copy has not been saved. Closing deletes it")
    state.shell.answer = True
    assert on_closing(state) is True and state.close_confirmed and len(state.shell.questions) == 2
    # a shell that cannot ask answers no: the close is refused rather than the copies deleted
    state.close_confirmed, state.shell = False, Shell()
    assert on_closing(state) is False and not state.close_confirmed
    # once saved there is nothing to ask about
    dest = tmp_path / "Saved"
    dest.mkdir()
    state.task.export(dest)
    assert on_closing(state) is True and state.close_confirmed


def test_a_dropped_file_lands_in_the_scratch_folder(tmp_path):
    state, client = window(tmp_path)
    source = document(tmp_path / "Spec.docx")
    with open(source, "rb") as fh:
        view = client.post("/api/upload", files=[("files", ("Spec.docx", fh.read(), "application/octet-stream"))],
                           data={"template": "false"}).json()
    dropped = Path(view["attachments"][0]["path"])
    assert dropped.is_file() and dropped.parent.parent == Path(state.task.session.out_root) / "attachments"
    assert dropped.read_bytes() == source.read_bytes()


def test_new_conversation_keeps_the_scratch_folder_and_writes_the_journal_over(tmp_path):
    journal = tmp_path / "out" / "conversation.json"
    state, client = window(tmp_path, journal=journal)
    _safe_run(client, tmp_path)
    copies = list(state.task.working.values())
    assert json.loads(journal.read_text(encoding="utf-8"))["working"]
    view = client.post("/api/new-conversation").json()
    assert view["results"] == [] and view["export"] == {"folder": "", "unsaved": 0, "copies": 0}
    assert json.loads(journal.read_text(encoding="utf-8"))["working"] == {}
    assert all(Path(c).is_file() for c in copies)                          # until the window closes
    assert not (journal.parent / "conversations").exists()
