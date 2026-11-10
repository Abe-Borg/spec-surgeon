"""Obliterate's rescue at attach (docs/BACKLOG.md O6; CLAUDE.md, Modes): a document the attach-time check refuses
offers **Try to rescue** in Obliterate mode, and what comes of it is attached as a new document marked SALVAGED.

The app's own rescues need no Word: a document type declaration written out of a part, an empty paragraph added to a
body that has none, the size limits raised for this window. Word's are a private copy converted (a ``.doc``, a
``.docm``, a Strict Open XML document), opened with a password and saved without one, or opened with Open and Repair.
No test machine has Word: ``fake_word.FakeWord`` stands behind the one seam.

Pinned here: what each refusal can and cannot be tried for; every rescue and every failure; the user's file read and
never written; a password handed to Word and written nowhere; the SALVAGED mark on the document and on every copy
made from it, in every mode and across a restart; and Safe and Default offering none of it.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import List

import pytest
from fastapi.testclient import TestClient

from fake_claude import FakeClient, resp, tool
from fake_word import FakeWord, WrongPassword
from make_fixtures import build, patch_docx
from spec_surgeon import rescue, word_actions
from spec_surgeon.document_task import RESCUE_OBLITERATE_ONLY, DocumentTask
from spec_surgeon.gui import Session
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.package_limits import ArchiveLimits
from spec_surgeon.settings import Settings
from spec_surgeon.triage import triage_file
from spec_surgeon.web.server import TOKEN_HEADER, WindowState, create_app
from test_document_task import SPEC, document
from test_triage import _strict
from test_web_server import TOKEN, FakeUpdates, frames

SECRET = "correct horse battery staple 7#"


@pytest.fixture(scope="module")
def plain() -> bytes:
    return build("21 00 00", "SPEC 0")


@pytest.fixture
def word(monkeypatch) -> FakeWord:
    fake = FakeWord()
    monkeypatch.setattr(word_actions, "run_word_actions", fake)
    monkeypatch.setattr(word_actions, "available", lambda fresh=False: True)
    return fake


def member(data: bytes, name: str) -> bytes:
    return zipfile.ZipFile(io.BytesIO(data)).read(name)


def with_bytes(data: bytes, name: str, change) -> bytes:
    made = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as z, zipfile.ZipFile(made, "w", zipfile.ZIP_DEFLATED) as target:
        for n in z.namelist():
            target.writestr(n, change(z.read(n)) if n == name else z.read(n))
    return made.getvalue()


def with_doctype(data: bytes, declaration: bytes = b'<!DOCTYPE document SYSTEM "x.dtd">') -> bytes:
    return with_bytes(data, "word/document.xml", lambda b: b[:b.index(b"?>") + 2] + declaration + b[b.index(b"?>") + 2:])


def without_paragraphs(data: bytes) -> bytes:
    def drop(root):
        body = root.find(f"{W}body")
        for child in list(body):
            if child.tag != f"{W}sectPr":
                body.remove(child)
    return patch_docx(data, {"word/document.xml": drop})


def encrypted_bytes() -> bytes:
    """What an Office document encrypted with a password looks like from outside: an OLE compound file with an
    ``EncryptedPackage`` stream. No zip, and nothing the app can read."""
    return b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 504 + "EncryptedPackage".encode("utf-16-le") + b"\0" * 2000


def write(folder: Path, name: str, data: bytes) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(data)
    return path


# ---- what can be tried ----------------------------------------------------------------------------------------------

def test_each_refusal_names_what_could_be_tried_or_nothing(tmp_path, plain):
    def kind(name: str, data: bytes) -> str:
        path = write(tmp_path / "k", name, data)
        found = triage_file(path)
        assert found.refused, name
        return rescue.kind_of(path, found.code)
    assert kind("old.doc", b"an old binary document") == "format"
    assert kind("macros.DOCM", plain) == "format"
    assert kind("strict.docx", _strict(plain)) == "strict"
    assert kind("typed.docx", with_doctype(plain)) == "doctype"
    assert kind("bare.docx", without_paragraphs(plain)) == "no-paragraph"
    assert kind("broken.docx", b"this is not a zip archive") == "unopenable"
    assert kind("secret.docx", encrypted_bytes()) == "password"
    # nothing to rescue: no document is there to read, or it is not a kind of file Word's converter is asked for
    assert kind("notes.pdf", b"%PDF-1.7") == "" and kind("page.rtf", b"{\\rtf1}") == ""
    assert kind("~$lock.docx", b"lock") == ""
    assert rescue.kind_of(tmp_path / "gone.docx", triage_file(tmp_path / "gone.docx").code) == ""
    assert rescue.kind_of(tmp_path, triage_file(tmp_path).code) == ""
    assert set(rescue.SAYS) == set(rescue.KINDS) and set(rescue.APP_KINDS) | set(rescue.WORD_KINDS) == set(rescue.KINDS)
    assert all(rescue.needs_word(k) for k in rescue.WORD_KINDS) and not any(rescue.needs_word(k) for k in rescue.APP_KINDS)


# ---- the app's own rescues ------------------------------------------------------------------------------------------

def test_a_document_type_declaration_is_written_out_and_every_other_part_is_the_files(tmp_path, plain):
    source = with_doctype(plain)
    path = write(tmp_path, "typed.docx", source)
    done = rescue.rescue_file(path, "doctype", tmp_path / "work")
    assert done.path == tmp_path / "work" / "typed.docx" and path.read_bytes() == source       # read, never written
    rescued = done.path.read_bytes()
    assert not triage_file(done.path).refused and b"<!DOCTYPE" not in member(rescued, "word/document.xml")
    names = zipfile.ZipFile(io.BytesIO(source)).namelist()
    assert zipfile.ZipFile(io.BytesIO(rescued)).namelist() == names
    assert all(member(rescued, n) == member(source, n) for n in names if n != "word/document.xml")
    # the same words are in the body: only the declaration went
    text = lambda data: "".join(DocxPackage(data).xml("word/document.xml").itertext())  # noqa: E731
    assert text(rescued) == text(plain)
    assert done.steps == ["The app wrote word/document.xml again without the document type declaration; every other "
                          "part is your file's, byte for byte."]
    assert done.holds.startswith("It holds ") and "paragraphs" in done.holds and done.notes == []


def test_a_part_that_uses_an_entity_its_declaration_defines_is_not_rescued(tmp_path, plain):
    """The entity's text exists nowhere but in the declaration: written without it, the part would lose words."""
    source = with_doctype(plain, b'<!DOCTYPE document [<!ENTITY owner "Acme Insurance">]>')
    source = with_bytes(source, "word/document.xml", lambda b: b.replace(b"FM Global", b"&owner;", 1))
    path = write(tmp_path, "entity.docx", source)
    with pytest.raises(rescue.RescueFailed, match="uses 1 entity that its document type declaration defines"):
        rescue.rescue_file(path, "doctype", tmp_path / "work")
    assert path.read_bytes() == source and not (tmp_path / "work" / "entity.docx").exists()


def test_a_body_with_no_paragraph_gets_one_empty_paragraph(tmp_path, plain):
    source = without_paragraphs(plain)
    path = write(tmp_path, "bare.docx", source)
    done = rescue.rescue_file(path, "no-paragraph", tmp_path / "work")
    body = DocxPackage(done.path.read_bytes()).xml("word/document.xml").find(f"{W}body")
    assert [c.tag for c in body] == [f"{W}p", f"{W}sectPr"] and len(body[0]) == 0
    assert not triage_file(done.path).refused and path.read_bytes() == source
    assert all(member(done.path.read_bytes(), n) == member(source, n)
               for n in zipfile.ZipFile(io.BytesIO(source)).namelist() if n != "word/document.xml")


def test_the_size_limits_are_raised_for_this_window_and_never_past_the_ceiling(tmp_path, plain, monkeypatch):
    for field in ArchiveLimits.__dataclass_fields__:
        monkeypatch.delenv("SPEC_SURGEON_ARCHIVE_" + field.upper(), raising=False)
    monkeypatch.setenv("SPEC_SURGEON_ARCHIVE_MAX_ARCHIVE_BYTES", "1000")
    monkeypatch.setenv("SPEC_SURGEON_ARCHIVE_MAX_MEMBERS", "5")
    path = write(tmp_path, "big.docx", plain)
    found = triage_file(path)
    assert found.refused and rescue.kind_of(path, found.code) == "too-large"
    done = rescue.rescue_file(path, "too-large", tmp_path / "work")
    # nothing is written: the user's own file is the document, and only the limits it was past moved, with headroom
    assert done.path is None and not (tmp_path / "work").exists() and path.read_bytes() == plain
    members = len(zipfile.ZipFile(io.BytesIO(plain)).namelist())
    assert done.limits == {"max_archive_bytes": len(plain) * rescue.HEADROOM, "max_members": members * rescue.HEADROOM}
    assert ArchiveLimits.from_environment().max_archive_bytes == len(plain) * rescue.HEADROOM
    assert not triage_file(path).refused and "raised its size limits for this window" in done.steps[0]
    # applying never lowers a limit, and a file past the ceiling is still refused
    rescue.apply_limits({"max_archive_bytes": 10, "max_members": 1, "nonsense": 5})
    assert ArchiveLimits.from_environment().max_members == members * rescue.HEADROOM
    monkeypatch.setenv("SPEC_SURGEON_ARCHIVE_MAX_ARCHIVE_BYTES", "1000")
    monkeypatch.setattr(rescue, "CEILING", ArchiveLimits(max_archive_bytes=2000))
    with pytest.raises(rescue.RescueFailed, match="is past what a rescue will open"):
        rescue.rescue_file(path, "too-large", tmp_path / "work2")
    assert ArchiveLimits.from_environment().max_archive_bytes == 1000


# ---- Word's rescues -------------------------------------------------------------------------------------------------

def test_word_converts_a_doc_a_docm_and_a_strict_document_from_a_private_copy(tmp_path, plain, monkeypatch):
    fake = FakeWord(saves={"old.doc": plain})
    monkeypatch.setattr(word_actions, "run_word_actions", fake)
    monkeypatch.setattr(word_actions, "available", lambda fresh=False: True)
    old = write(tmp_path, "old.doc", b"an old binary document")
    done = rescue.rescue_file(old, "format", tmp_path / "w1")
    assert done.path.name == "old.docx" and not triage_file(done.path).refused
    assert old.read_bytes() == b"an old binary document" and fake.calls == [("old.doc", ["convert"])]
    assert done.steps[0].startswith("Word opened a private copy of the .doc file and saved it as a .docx (Word's own "
                                    "account: saved as a Word document).")
    assert done.notes == [] and fake.given == [rescue.NO_PASSWORD]          # Word is never left to ask for one
    macros = write(tmp_path, "macros.docm", plain)
    done = rescue.rescue_file(macros, "format", tmp_path / "w2")
    assert done.path.name == "macros.docx" and done.notes == [rescue.NO_MACROS]
    strict = write(tmp_path, "strict.docx", _strict(plain))
    fake.saves["strict.docx"] = plain
    done = rescue.rescue_file(strict, "strict", tmp_path / "w3")
    assert not triage_file(done.path).refused and "Strict Open XML document" in done.steps[0]
    assert strict.read_bytes() == _strict(plain)


def test_word_repairs_what_is_not_a_package_and_its_failure_is_the_reason(tmp_path, plain, monkeypatch):
    fake = FakeWord(saves={"broken.docx": plain})
    monkeypatch.setattr(word_actions, "run_word_actions", fake)
    monkeypatch.setattr(word_actions, "available", lambda fresh=False: True)
    broken = write(tmp_path, "broken.docx", b"this is not a zip archive")
    done = rescue.rescue_file(broken, "unopenable", tmp_path / "w1")
    assert fake.calls == [("broken.docx", ["repair"])] and not triage_file(done.path).refused
    assert "with Open and Repair and saved what it could read" in done.steps[0]
    error = RuntimeError("The file is corrupt and cannot be opened.")
    error.code, error.details = "word-com", {"stage": "word_open"}
    monkeypatch.setattr(word_actions, "run_word_actions", FakeWord(fail={"broken.docx": error}))
    with pytest.raises(rescue.RescueFailed, match=r"Word could not finish \[word-com\]: The file is corrupt"):
        rescue.rescue_file(broken, "unopenable", tmp_path / "w2")
    monkeypatch.setattr(word_actions, "available", lambda fresh=False: False)
    with pytest.raises(rescue.RescueFailed, match="Microsoft Word is not installed"):
        rescue.rescue_file(broken, "unopenable", tmp_path / "w3")
    assert broken.read_bytes() == b"this is not a zip archive"


def test_what_word_saves_is_checked_again_and_mended_once_by_the_app(tmp_path, plain, monkeypatch):
    """Word's result is a file like any other: still refused for something the app can mend, it is mended; still
    refused for anything else, there is no document."""
    monkeypatch.setattr(word_actions, "available", lambda fresh=False: True)
    monkeypatch.setattr(word_actions, "run_word_actions",
                        lambda source, actions, output, timeout=None, password=None:
                        Path(output).write_bytes(without_paragraphs(plain)) and [])
    old = write(tmp_path, "old.doc", b"an old binary document")
    done = rescue.rescue_file(old, "format", tmp_path / "w1")
    assert len(done.steps) == 2 and "added one empty paragraph" in done.steps[1] and not triage_file(done.path).refused
    monkeypatch.setattr(word_actions, "run_word_actions",
                        lambda source, actions, output, timeout=None, password=None:
                        Path(output).write_bytes(b"still not a package") and [])
    with pytest.raises(rescue.RescueFailed, match="After that the file is still one the app cannot take"):
        rescue.rescue_file(old, "format", tmp_path / "w2")
    with pytest.raises(rescue.RescueFailed, match="nothing to try"):
        rescue.rescue_file(old, "lock", tmp_path / "w3")


def test_a_password_is_asked_for_given_to_word_and_the_result_has_none(tmp_path, plain, monkeypatch):
    fake = FakeWord(saves={"secret.docx": plain}, passwords={"secret.docx": SECRET})
    monkeypatch.setattr(word_actions, "run_word_actions", fake)
    monkeypatch.setattr(word_actions, "available", lambda fresh=False: True)
    path = write(tmp_path, "secret.docx", encrypted_bytes())
    assert rescue.encrypted(path) and not rescue.encrypted(write(tmp_path, "plain.docx", plain))
    with pytest.raises(rescue.NeedsPassword) as asked:
        rescue.rescue_file(path, "password", tmp_path / "w1")
    assert not asked.value.wrong and "needs the password" in str(asked.value)
    with pytest.raises(rescue.NeedsPassword) as asked:
        rescue.rescue_file(path, "password", tmp_path / "w2", password="a guess")
    assert asked.value.wrong and "not the password" in str(asked.value) and "a guess" not in str(asked.value)
    done = rescue.rescue_file(path, "password", tmp_path / "w3", password=SECRET)
    assert not triage_file(done.path).refused and done.notes == [rescue.NO_PASSWORD_NOW]
    assert fake.given == [rescue.NO_PASSWORD, "a guess", SECRET]
    assert SECRET not in " ".join(done.steps + done.notes + [done.holds])
    # an old .doc that turns out to want one: Word's own answer says so, and no other failure reads as that
    assert rescue.wrong_password(WrongPassword())
    assert not rescue.wrong_password(RuntimeError("The file is corrupt and cannot be opened."))
    fake.passwords["old.doc"] = SECRET
    with pytest.raises(rescue.NeedsPassword):
        rescue.rescue_file(write(tmp_path, "old.doc", b"binary"), "format", tmp_path / "w4")


# ---- the window -----------------------------------------------------------------------------------------------------

def task_for(tmp_path, fake=None, **kwargs):
    kwargs.setdefault("obliterate_allowed", True)
    return DocumentTask(Session(client=fake or FakeClient(), out_root=tmp_path / "out", visual_check=False),
                        render=False, **kwargs)


def said(task) -> List[str]:
    return [m["text"] for m in task.messages if m["role"] == "app"]


def attached(task) -> List[dict]:
    return task.view()["attachments"]


def test_safe_and_default_offer_no_rescue_and_take_no_doc(tmp_path, plain):
    task = task_for(tmp_path)
    typed = write(tmp_path, "typed.docx", with_doctype(plain))
    task.attach([typed])
    for mode in ("default", "safe"):
        task.set_mode(mode)
        [entry] = attached(task)
        assert entry["state"] == "refused" and "rescue" not in entry and "salvaged" not in entry
        with pytest.raises(ValueError, match="Choose Word .docx documents"):
            task.attach([write(tmp_path, "old.doc", b"binary")])
        with pytest.raises(ValueError, match="Obliterate mode's"):
            task.perform("rescue", path=str(typed))
    assert "salvaged" not in task.view() and len(task.attachments) == 1


def test_a_refused_document_is_rescued_and_attached_in_its_place_marked_salvaged(tmp_path, plain):
    fake = FakeClient()
    task = task_for(tmp_path, fake)
    good, typed = write(tmp_path, "good.docx", plain), write(tmp_path, "typed.docx", with_doctype(plain))
    original = typed.read_bytes()
    task.attach([typed, good])
    task.set_mode("obliterate", acknowledged=True)
    first, second = attached(task)
    assert first["rescue"] == "doctype" and first["state"] == "refused" and "rescue" not in second
    view = task.perform("rescue", path=str(typed))
    assert fake.requests == [] and typed.read_bytes() == original
    first, second = view["attachments"]                       # in the refused file's own place
    rescued = Path(first["path"])
    assert rescued.name == "typed.docx" and rescued != typed and tmp_path / "out" in rescued.parents
    assert first["state"] != "refused" and "rescue" not in first and Path(second["path"]) == good
    assert first["salvaged"]["from"] == "typed.docx" and first["salvaged"]["kind"] == "doctype"
    assert first["salvaged"]["steps"][0].startswith("The app wrote word/document.xml again")
    [told] = [t for t in said(task) if t.startswith("Rescued")]
    assert told.startswith("Rescued typed.docx. The app wrote word/document.xml again")
    assert "marked SALVAGED; your file was read and not changed." in told and rescue.UNPROVEN in told
    assert "It holds " in told
    assert task.session.documents == [rescued, good]
    # nothing more to rescue in it, and nothing in an ordinary document
    for path in (rescued, good):
        with pytest.raises(ValueError, match="nothing to rescue"):
            task.perform("rescue", path=str(path))
    with pytest.raises(ValueError, match="not attached"):
        task.perform("rescue", path=str(typed))


def test_the_mark_stays_on_the_document_and_on_every_copy_in_every_mode_and_across_a_restart(tmp_path, plain):
    journal = tmp_path / "conversation.json"
    spec = {**SPEC, "ops": [{"op": "set_properties", "select": {"paragraphs": {"text_regex": "^SPEC 0$"}},
                             "params": {"run": {"b": True}, "tracked": False}}]}
    fake = FakeClient(resp(tool("submit_editspec", {"spec": spec})))
    task = task_for(tmp_path, fake, journal=journal)
    typed = write(tmp_path, "typed.docx", with_doctype(plain))
    task.attach([typed])
    task.set_mode("obliterate", acknowledged=True)
    task.perform("rescue", path=str(typed))
    rescued = Path(attached(task)[0]["path"])
    task.set_mode("default")                                   # the rescued document runs in any mode
    [entry] = attached(task)
    assert entry["salvaged"]["from"] == "typed.docx" and "rescue" not in entry
    view = task.perform("send", text="Make the title bold.")
    assert view["stage"] == "preview"
    # the model hears that the document was rescued, as it hears any mark
    assert "typed.docx is SALVAGED: the app rescued it from typed.docx" in str(fake.requests[0]["messages"])
    view = task.perform("primary", token=view["token"])
    assert list(task.results) == [str(rescued)] and task.unverified == {}       # checked against the salvaged file
    line = view["salvaged"][str(rescued)]
    assert line.startswith("SALVAGED: the app rescued it from typed.docx, a file it could not read as it was")
    assert task._results_view()[0]["salvaged"] == line
    # Export says so too
    saved = tmp_path / "saved"
    saved.mkdir()
    task.export(str(saved), "word")
    assert "1 was made from a document the app rescued (SALVAGED)" in said(task)[-1]
    # a restart keeps the mark: it is the document's, for as long as it is attached
    again = task_for(tmp_path, journal=journal)
    again.restore()
    [entry] = again.view()["attachments"]
    assert entry["salvaged"]["from"] == "typed.docx" and entry["salvaged"]["steps"]
    assert again.view()["salvaged"][str(rescued)] == line
    again.remove(rescued)
    assert again.view()["attachments"] == [] and "salvaged" not in again.view()


def test_a_doc_is_attached_in_obliterate_alone_and_rescued_by_word(tmp_path, plain, monkeypatch):
    fake_word = FakeWord(saves={"old.doc": plain})
    monkeypatch.setattr(word_actions, "run_word_actions", fake_word)
    monkeypatch.setattr(word_actions, "available", lambda fresh=False: True)
    task = task_for(tmp_path)
    old = write(tmp_path, "old.doc", b"an old binary document")
    task.set_mode("obliterate", acknowledged=True)
    task.attach([old])
    [entry] = attached(task)
    assert entry["state"] == "refused" and entry["rescue"] == "format" and "not a .docx file" in entry["reason"]
    assert task.session.documents == []                        # a refused file is in no plan
    view = task.perform("rescue", path=str(old))
    [entry] = view["attachments"]
    assert Path(entry["path"]).name == "old.docx" and entry["salvaged"]["kind"] == "format"
    assert any(t.startswith("Rescued old.doc. Word opened a private copy of the .doc file") for t in said(task))
    assert old.read_bytes() == b"an old binary document"
    # Remove all documents takes a refused .doc too
    task.attach([write(tmp_path, "other.docm", plain)])
    assert task.remove_documents() == 2 and task.attachments == []


def test_a_rescue_that_makes_no_document_says_why_and_leaves_the_file_refused(tmp_path, plain, monkeypatch):
    monkeypatch.setattr(word_actions, "available", lambda fresh=False: False)
    task = task_for(tmp_path)
    broken = write(tmp_path, "broken.docx", b"this is not a zip archive")
    task.attach([broken])
    task.set_mode("obliterate", acknowledged=True)
    view = task.perform("rescue", path=str(broken))
    [entry] = view["attachments"]
    assert entry["state"] == "refused" and entry["rescue"] == "unopenable" and Path(entry["path"]) == broken
    assert said(task)[-1].startswith("Try to rescue: broken.docx was not rescued. Microsoft Word is not installed")
    assert said(task)[-1].endswith("The file is as it was.")
    left = tmp_path / "out" / "rescued"
    assert not left.exists() or not any(left.rglob("*"))       # a rescue that made nothing leaves nothing behind


def test_the_password_goes_to_word_and_is_written_nowhere(tmp_path, plain, monkeypatch):
    fake_word = FakeWord(saves={"secret.docx": plain}, passwords={"secret.docx": SECRET})
    monkeypatch.setattr(word_actions, "run_word_actions", fake_word)
    monkeypatch.setattr(word_actions, "available", lambda fresh=False: True)
    journal = tmp_path / "conversation.json"
    task = task_for(tmp_path, journal=journal)
    secret = write(tmp_path, "secret.docx", encrypted_bytes())
    task.attach([secret])
    task.set_mode("obliterate", acknowledged=True)
    assert attached(task)[0]["rescue"] == "password"
    # without one, Word is not even tried: the window asks first
    task.perform("rescue", path=str(secret))
    assert fake_word.calls == [] and "is protected with a password" in said(task)[-1]
    task.perform("rescue", path=str(secret), password="not it")
    assert "Word says that is not the password of secret.docx" in said(task)[-1]
    assert attached(task)[0]["state"] == "refused"
    view = task.perform("rescue", path=str(secret), password=SECRET)
    [entry] = view["attachments"]
    assert entry["salvaged"]["kind"] == "password" and entry["salvaged"]["notes"] == [rescue.NO_PASSWORD_NOW]
    assert any(rescue.NO_PASSWORD_NOW in t for t in said(task)) and fake_word.given == ["not it", SECRET]
    # no message, no view, no journal and no file the app wrote holds either password
    for word_ in (SECRET, "not it"):
        assert word_ not in json.dumps(task.view(), default=str) and word_ not in journal.read_text(encoding="utf-8")
        for file in (tmp_path / "out").rglob("*"):
            if file.is_file():
                data = file.read_bytes()
                assert word_.encode() not in data and word_.encode("utf-16-le") not in data, file


def test_an_old_doc_that_wants_a_password_is_asked_for_it_on_the_next_press(tmp_path, plain, monkeypatch):
    fake_word = FakeWord(saves={"old.doc": plain}, passwords={"old.doc": SECRET})
    monkeypatch.setattr(word_actions, "run_word_actions", fake_word)
    monkeypatch.setattr(word_actions, "available", lambda fresh=False: True)
    task = task_for(tmp_path)
    task.set_mode("obliterate", acknowledged=True)
    old = write(tmp_path, "old.doc", b"an old binary document")
    task.attach([old])
    assert attached(task)[0]["rescue"] == "format"
    task.perform("rescue", path=str(old))
    assert attached(task)[0]["rescue"] == "password" and "Word needs the password of old.doc" in said(task)[-1]
    view = task.perform("rescue", path=str(old), password=SECRET)
    assert view["attachments"][0]["salvaged"]["from"] == "old.doc"


def test_a_file_past_the_size_limit_is_opened_as_it_is_and_carries_no_mark(tmp_path, plain, monkeypatch):
    for field in ArchiveLimits.__dataclass_fields__:
        monkeypatch.delenv("SPEC_SURGEON_ARCHIVE_" + field.upper(), raising=False)
    monkeypatch.setenv("SPEC_SURGEON_ARCHIVE_MAX_ARCHIVE_BYTES", "1000")
    journal = tmp_path / "conversation.json"
    task = task_for(tmp_path, journal=journal)
    big = write(tmp_path, "big.docx", plain)
    task.attach([big])
    task.set_mode("obliterate", acknowledged=True)
    assert attached(task)[0]["rescue"] == "too-large"
    view = task.perform("rescue", path=str(big))
    [entry] = view["attachments"]
    assert Path(entry["path"]) == big and entry["state"] != "refused" and "salvaged" not in entry
    assert said(task)[-1].startswith("Opened big.docx past the size limit.")
    assert "it carries no mark; the limits stay raised until this window closes." in said(task)[-1]
    assert task.archive_limits == {"max_archive_bytes": len(plain) * rescue.HEADROOM}
    # a restart raises them again before it reads the document
    monkeypatch.setenv("SPEC_SURGEON_ARCHIVE_MAX_ARCHIVE_BYTES", "1000")
    again = task_for(tmp_path, journal=journal)
    again.restore()
    assert again.view()["attachments"][0]["state"] != "refused"
    assert ArchiveLimits.from_environment().max_archive_bytes == len(plain) * rescue.HEADROOM


# ---- the server -----------------------------------------------------------------------------------------------------

def window(tmp_path):
    session = Session(client=FakeClient(), out_root=tmp_path / "out", visual_check=False)
    task = DocumentTask(session, render=False, obliterate_allowed=True)
    state = WindowState(task, Settings(show_obliterate=True), token=TOKEN, updates=FakeUpdates(), version="1.6.0")
    return state, TestClient(create_app(state), headers={TOKEN_HEADER: TOKEN})


def test_the_route_rescues_in_obliterate_alone(tmp_path, plain):
    state, client = window(tmp_path)
    typed, good = write(tmp_path, "typed.docx", with_doctype(plain)), write(tmp_path, "good.docx", plain)
    state.task.attach([typed, good])
    assert "rescue" not in client.get("/api/state").json()["attachments"][0]
    refused = client.post("/api/rescue", json={"path": str(typed)})
    assert refused.status_code == 400 and refused.json()["error"] == RESCUE_OBLITERATE_ONLY
    assert client.post("/api/mode", json={"mode": "obliterate", "acknowledged": True}).status_code == 200
    assert client.get("/api/state").json()["attachments"][0]["rescue"] == "doctype"
    for body, why in (({"path": str(good)}, "nothing to rescue"), ({"path": str(tmp_path / "x.docx")}, "not attached"),
                      ({"path": str(typed), "password": 7}, "must be text")):
        refused = client.post("/api/rescue", json=body)
        assert refused.status_code == 400 and why in refused.json()["error"], body
    events = frames(client.post("/api/rescue", json={"path": str(typed)}))
    first = events[-1]["state"]["attachments"][0]
    assert events[-1]["type"] == "done" and first["salvaged"]["from"] == "typed.docx" and first["name"] == "typed.docx"
    assert first["state"] != "refused" and "rescue" not in first


def test_the_file_dialog_offers_doc_and_docm_in_obliterate_alone(tmp_path):
    from spec_surgeon.web import shell
    state, client = window(tmp_path)
    asked: List[str] = []
    state.shell = type("Shell", (), {"pick_files": lambda self, kind: asked.append(kind) or [],
                                     "pick_folder": lambda self, start="": None})()
    client.post("/api/pick", json={"kind": "documents"})
    client.post("/api/mode", json={"mode": "obliterate", "acknowledged": True})
    client.post("/api/pick", json={"kind": "documents"})
    client.post("/api/pick", json={"kind": "template"})
    assert asked == ["documents", "rescuable", "template"]
    assert shell.FILE_TYPES["rescuable"] == ("Word documents (*.docx;*.doc;*.docm)",)
    assert shell.FILE_TYPES["documents"] == ("Word documents (*.docx)",)


def test_stop_ends_a_rescue_and_touches_nothing_else(tmp_path, plain, monkeypatch):
    """A rescue calls no AI, so Stop ends the job itself; the file stays refused, and a preview that was waiting for
    the other documents still waits."""
    import threading

    from spec_surgeon import stopping
    from spec_surgeon.web import server
    event = threading.Event()

    def stopped_by_the_user(source, actions, output, timeout=None, password=None):
        event.set()
        stopping.check("Word")
    monkeypatch.setattr(word_actions, "run_word_actions", stopped_by_the_user)
    monkeypatch.setattr(word_actions, "available", lambda fresh=False: True)
    fake = FakeClient(resp(tool("submit_editspec", {"spec": SPEC})))
    task = task_for(tmp_path, fake)
    good, old = document(tmp_path / "good.docx"), write(tmp_path, "old.doc", b"an old binary document")
    task.set_mode("obliterate", acknowledged=True)
    task.attach([good, old])
    view = task.perform("send", text="Make the FIRE SUPPRESSION title bold.")
    assert view["stage"] == "preview"
    with stopping.scope(event), pytest.raises(stopping.Stopped):
        task.perform("rescue", path=str(old))
    assert said(task)[-1].startswith("Stopped. Nothing was rescued: the file is as it was")
    assert task.stage == "preview" and task.session.spec is not None            # the waiting preview still waits
    assert [a["state"] for a in attached(task)] == [attached(task)[0]["state"], "refused"]
    assert "rescue" in server.MODEL_FREE_JOBS


def test_stop_during_the_apps_own_rescue_attaches_nothing_and_raises_no_limit(tmp_path, plain, monkeypatch):
    """Stop is looked at between a rescue's steps and before anything is attached, whoever does the mending: a
    stopped rescue leaves the refused file refused, a waiting preview waiting, and the size limits where they were."""
    import threading

    from spec_surgeon import stopping
    for field in ArchiveLimits.__dataclass_fields__:
        monkeypatch.delenv("SPEC_SURGEON_ARCHIVE_" + field.upper(), raising=False)
    event = threading.Event()
    mended = rescue._without_doctype

    def stopped_while_mending(data):
        made = mended(data)
        event.set()                              # the user presses Stop while the app writes the part again
        return made
    monkeypatch.setattr(rescue, "_without_doctype", stopped_while_mending)
    fake = FakeClient(resp(tool("submit_editspec", {"spec": SPEC})))
    task = task_for(tmp_path, fake)
    good, typed = document(tmp_path / "good.docx"), write(tmp_path, "typed.docx", with_doctype(plain))
    task.set_mode("obliterate", acknowledged=True)
    task.attach([good, typed])
    view = task.perform("send", text="Make the FIRE SUPPRESSION title bold.")
    assert view["stage"] == "preview"
    with stopping.scope(event), pytest.raises(stopping.Stopped):
        task.perform("rescue", path=str(typed))
    assert [a["state"] for a in attached(task)][1] == "refused" and Path(attached(task)[1]["path"]) == typed
    assert task.stage == "preview" and task.session.spec is not None            # the waiting preview still waits
    assert said(task)[-1].startswith("Stopped. Nothing was rescued")
    left = tmp_path / "out" / "rescued"
    assert not left.exists() or not any(left.rglob("*"))
    # Stop pressed before it began: nothing is even read
    monkeypatch.setattr(rescue, "_without_doctype", lambda data: pytest.fail("a stopped rescue mended a part"))
    with stopping.scope(event), pytest.raises(stopping.Stopped):
        task.perform("rescue", path=str(typed))
    # and a size-limit rescue that is stopped, or fails, leaves the limits as they were
    monkeypatch.setenv("SPEC_SURGEON_ARCHIVE_MAX_ARCHIVE_BYTES", "1000")
    big = write(tmp_path, "big.docx", plain)
    from spec_surgeon import triage as triage_module
    real = triage_module.triage_file

    def stopped_after_raising(path):
        if Path(path) == big and ArchiveLimits.from_environment().max_archive_bytes != 1000:
            event.set()
            stopping.check("the rescue")
        return real(path)
    monkeypatch.setattr(triage_module, "triage_file", stopped_after_raising)
    event.clear()
    with stopping.scope(event), pytest.raises(stopping.Stopped):
        rescue.rescue_file(big, "too-large", tmp_path / "w")
    assert ArchiveLimits.from_environment().max_archive_bytes == 1000
