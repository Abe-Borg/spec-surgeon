"""Export → Save the copies…: the one place a copy is written where the user chose. The working copies go into the
chosen folder under their planned names, byte for byte the copies the checker passed, never over an input; a copy
already there unchanged is that copy, saved; a different file under its name is left alone and the copy numbered.
The window says which copies are unsaved, asks once before a close would delete them, keeps the export folder for
the next dialog, and the journal keeps what was saved where."""
from __future__ import annotations

import hashlib
import json
import os
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


def _md5(path) -> str:
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


def _edit(copy, title="ANOTHER TITLE") -> None:
    """Word standing in: the working copy rewritten in place through the window's own link."""
    before = Path(copy).read_bytes()
    Path(copy).write_bytes(build(Path(copy).stem, title))
    assert Path(copy).read_bytes() != before


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
        assert saved.read_bytes() == Path(current).read_bytes()
        assert task.exported[current] == {"to": str(saved), "md5": _md5(saved)}   # the bytes written, fingerprinted
    assert task.unsaved_copies() == [] and task.export_folder == str(dest.resolve())
    assert task.export_states() == {current: "saved" for current in task.working.values()}
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
    assert (dest / name).read_bytes() == b"someone else's" and task.exported[first]["to"] == str(numbered)
    assert _names(dest) == sorted(names + [numbered.name])


def test_a_copy_changed_since_it_was_saved_is_unsaved_again_until_the_next_export(tmp_path, files):
    """A copy opened through the window's link is the working copy in the scratch folder, not the exported file:
    edited there (in Word) and saved, it holds content the exported file never had. It counts as unsaved again (the
    close question asks, the view says so), and the next Export writes it beside the file saved before."""
    task = _run(tmp_path, files)
    dest = tmp_path / "Saved"
    dest.mkdir()
    task.export(dest)
    first, second = task.working.values()
    assert task.unsaved_copies() == [] and task.view()["unsaved"] == []
    _edit(first)
    assert task.unsaved_copies() == [first] and task.export_states() == {first: "changed", second: "saved"}
    view = task.view()
    assert view["unsaved"] == [first] and view["export_state"] == {first: "changed", second: "saved"}
    assert view["exported"][first]["to"] == str(dest / Path(first).name)     # where it was saved is still said
    # the second export writes it again: the file saved before is the user's, so the copy lands numbered beside it
    made = task.export(dest)
    numbered = dest / f"{Path(first).stem} (2).docx"
    assert made == {"folder": str(dest.resolve()), "saved": 1, "already": 1}
    assert numbered.read_bytes() == Path(first).read_bytes() and task.exported[first]["to"] == str(numbered)
    assert task.unsaved_copies() == [] and task.export_states() == {first: "saved", second: "saved"}
    assert _names(dest) == sorted([Path(first).name, numbered.name, Path(second).name])


def test_a_saved_file_that_is_gone_makes_its_copy_unsaved_again(tmp_path, files):
    task = _run(tmp_path, files)
    dest = tmp_path / "Saved"
    dest.mkdir()
    task.export(dest)
    first, second = task.working.values()
    (dest / Path(first).name).unlink()
    assert task.unsaved_copies() == [first] and task.export_states() == {first: "gone", second: "saved"}
    assert task.view()["unsaved"] == [first]
    assert task.export(dest) == {"folder": str(dest.resolve()), "saved": 1, "already": 1}
    assert task.unsaved_copies() == [] and (dest / Path(first).name).read_bytes() == Path(first).read_bytes()


def test_the_close_fingerprints_every_copy_again(tmp_path, files):
    """A view reads the last fingerprint while a copy's size and mtime stand; the close question (``fresh``) reads
    every copy's bytes again, so a copy rewritten to the same size and time is still caught where it matters."""
    task = _run(tmp_path, files)
    dest = tmp_path / "Saved"
    dest.mkdir()
    task.export(dest)
    first = next(iter(task.working.values()))
    st = os.stat(first)
    data = bytearray(Path(first).read_bytes())
    data[-1] ^= 0xFF                                   # the same size
    Path(first).write_bytes(bytes(data))
    os.utime(first, ns=(st.st_atime_ns, st.st_mtime_ns))   # and the same time
    assert task.unsaved_copies() == []                 # the view's reading: nothing moved that a stat can see
    assert task.unsaved_copies(fresh=True) == [first]  # the close's reading: the bytes
    assert task.unsaved_copies() == [first]            # and the fingerprint taken is kept


def test_export_refuses_a_folder_where_a_copy_would_land_on_an_input(tmp_path, files):
    task = _run(tmp_path, files)
    originals = {p: p.read_bytes() for p in files}
    # the originals' folder: these copies carry their originals' names
    with pytest.raises(ValueError, match="one of the files this conversation works on"):
        task.export(files[0].parent)
    # the run's own outputs folder: the copies themselves, and inside the scratch folder besides
    with pytest.raises(ValueError, match="inside the app's scratch folder"):
        task.export(Path(next(iter(task.working.values()))).parent)
    assert {p: p.read_bytes() for p in files} == originals and task.exported == {} and task.export_folder == ""
    assert len(task.unsaved_copies()) == 2 and _names(files[0].parent) == ["21 13 13.docx", "21 13 16.docx"]
    with pytest.raises(ValueError, match="is not a folder"):
        task.export(tmp_path / "missing")
    with pytest.raises(ValueError, match="no edited copies or clean PDFs"):
        safe_task(tmp_path / "other").export(tmp_path)


def test_export_refuses_a_folder_inside_the_scratch_tree(tmp_path, files):
    """A copy saved into the window's scratch folder (or any folder under the app's scratch root) would be deleted
    with it at the close, while reading as saved: refused before anything is written or marked (Codex review on
    PR #124)."""
    from spec_surgeon.settings import scratch_root
    task = _run(tmp_path, files)
    inside = Path(task.session.out_root) / "Saved"
    inside.mkdir()
    with pytest.raises(ValueError, match="inside the app's scratch folder"):
        task.export(inside)
    with pytest.raises(ValueError, match="inside the app's scratch folder"):
        task.export(task.session.out_root)
    other = scratch_root() / "20261001-120000-9" / "Saved"            # another window's, or a leftover's
    other.mkdir(parents=True)
    with pytest.raises(ValueError, match="inside the app's scratch folder"):
        task.export(other)
    assert _names(inside) == [] and _names(other) == [] and task.exported == {}
    assert len(task.unsaved_copies()) == 2
    beside = tmp_path / "Saved"                                         # a folder of the user's own, as before
    beside.mkdir()
    assert task.export(beside)["saved"] == 2


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
    # the hash check runs on the restored state too: a copy changed since it was saved, with the window closed
    first, second = restored.working.values()
    saved_bytes = Path(first).read_bytes()
    _edit(first)
    restored = safe_task(tmp_path, journal=journal)
    assert restored.restore() and restored.unsaved_copies() == [first]
    assert restored.export_states() == {first: "changed", second: "saved"}
    Path(first).write_bytes(saved_bytes)                 # the saved bytes again
    # a journal from before the fingerprint kept the destination alone: saved, its hash taken from the saved file
    old = {copy: entry["to"] for copy, entry in task.exported.items()}
    journal.write_text(json.dumps({**data, "exported": old}), encoding="utf-8")
    plain = safe_task(tmp_path, journal=journal)
    assert plain.restore() and plain.exported == task.exported and plain.unsaved_copies() == []
    # ... and with the saved file gone, the copy is unsaved again (the hash stays unknown)
    (dest / Path(first).name).unlink()
    gone = safe_task(tmp_path, journal=journal)
    assert gone.restore() and gone.exported[first] == {"to": old[first], "md5": ""}
    assert gone.exported[second] == task.exported[second] and gone.unsaved_copies() == [first]
    # a malformed entry is dropped, never an error
    journal.write_text(json.dumps({**data, "exported": {first: 7, second: {"md5": "x"}, "other": ["y"]}}), encoding="utf-8")
    odd = safe_task(tmp_path, journal=journal)
    assert odd.restore() and odd.exported == {} and odd.unsaved_copies() == [first, second]
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
    assert all(r["changed_since_saved"] is False for r in view["results"])
    assert _names(dest) == sorted(r["name"] for r in view["results"])
    # a copy changed since it was saved (opened from the window, edited in Word): the view says so beside its
    # saved_to, so the page opens the working copy and says "changed since it was saved"; a saved file that is gone
    # leaves its copy plainly unsaved
    first, second = view["results"]
    _edit(first["output"])
    view = client.get("/api/state").json()
    assert view["export"]["unsaved"] == 1
    assert [(r["saved_to"], r["changed_since_saved"]) for r in view["results"]] == [
        (str(dest / first["name"]), True), (str(dest / second["name"]), False)]
    (dest / second["name"]).unlink()
    view = client.get("/api/state").json()
    assert view["export"]["unsaved"] == 2
    assert [(r["saved_to"], r["changed_since_saved"]) for r in view["results"]] == [
        (str(dest / first["name"]), True), ("", False)]
    view = client.post("/api/export").json()
    assert view["export"]["unsaved"] == 0 and _names(dest) == sorted(
        [first["name"], f"{Path(first['name']).stem} (2).docx", second["name"]])
    assert [(r["saved_to"], r["changed_since_saved"]) for r in view["results"]] == [
        (str(dest / f"{Path(first['name']).stem} (2).docx"), False), (str(dest / second["name"]), False)]
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
    assert view["export"]["folder"] == str(other.resolve()) and _names(other) == sorted(r["name"] for r in view["results"])
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
    # nothing to lose: no question, and no consent recorded either — a run still finishing may deliver copies after
    # this, and the launcher then keeps the folder rather than delete copies nobody was asked about (Codex review on
    # PR #124)
    assert on_closing(state) is True and not state.close_confirmed and state.shell.questions == []
    _safe_run(client, tmp_path)
    assert len(state.task.unsaved_copies()) == 2
    assert on_closing(state) is False and not state.close_confirmed
    title, question = state.shell.questions[-1]
    assert title == "Copies not saved" and question == unsaved_copies_question(2)
    assert question.startswith("2 files made in this window (edited copies or clean PDFs) have not been saved as "
                               "they are now (never saved with Export, or changed since). Closing deletes them")
    assert "Export → Save the copies…" in question and "Cancel goes back" in question
    assert unsaved_copies_question(1).startswith("1 file made in this window (an edited copy or a clean PDF) has not "
                                                 "been saved as it is now (never saved with Export, or changed "
                                                 "since). Closing deletes it")
    state.shell.answer = True
    assert on_closing(state) is True and state.close_confirmed and len(state.shell.questions) == 2
    # a shell that cannot ask answers no: the close is refused rather than the copies deleted
    state.close_confirmed, state.shell = False, Shell()
    assert on_closing(state) is False and not state.close_confirmed
    # once saved there is nothing to ask about, and nothing to consent to
    dest = tmp_path / "Saved"
    dest.mkdir()
    state.task.export(dest)
    state.shell = FolderShell(answer=False)
    assert on_closing(state) is True and not state.close_confirmed and state.shell.questions == []
    # a copy changed since it was saved (opened through the window's link and edited in Word) is asked about again
    first = next(iter(state.task.working.values()))
    _edit(first)
    assert on_closing(state) is False and not state.close_confirmed
    assert state.shell.questions[-1] == ("Copies not saved", unsaved_copies_question(1))
    state.task.export(dest)
    assert on_closing(state) is True and len(state.shell.questions) == 1


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
