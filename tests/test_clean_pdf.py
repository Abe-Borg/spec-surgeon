"""Safe mode's *Save clean PDFs* (``recipes._clean_pdf``, ``pdf_export``, ``Session._export_pdfs``,
``DocumentTask._end_export``): two planned steps — every comment removed, then every tracked change accepted — through
the gate, the preview and the checked run like any button, then a PDF of each copy that passed, checked itself. The PDFs
are the result: the working set, the history and the undo points stay as they were. No AI call anywhere; a document
that cannot be cleaned exactly, or whose PDF cannot be made, has no PDF and says why."""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from fx_comments import build_comment_in_deletion, build_commented
from fx_revisions import awkward, redline
from make_fixtures import build
from spec_surgeon import compiler, llm, pdf_export, recipes, render, vision
from spec_surgeon.document_task import DocumentTask
from spec_surgeon.gui import Session
from spec_surgeon.verify.geometry import fitz

MARKUP = ("<w:ins ", "<w:del ", "<w:moveFrom ", "<w:moveTo ", "<w:rPrChange", "<w:pPrChange", "commentReference",
          "commentRangeStart", "commentRangeEnd")


def write_pdf(path: Path, text: str, note: bool = False) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    if note:
        page.add_text_annot((72, 120), "a comment balloon")
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def no_ai(monkeypatch):
    def never(*a, **k):
        raise AssertionError("Save clean PDFs makes no AI call")
    monkeypatch.setattr(vision, "review_samples", never)
    monkeypatch.setattr(llm, "client", never)


@pytest.fixture
def renderer(monkeypatch):
    """A stand-in for Word or LibreOffice: a one-page PDF named after each copy, holding the copy's name; ``fail``
    names copies it cannot render (said the way ``render.render_pdfs`` says it), ``noted`` copies whose PDF carries a
    note. ``calls`` records what it was asked to render."""
    state = {"fail": set(), "noted": set(), "calls": []}

    def fake_render(files, out_dir, backend, workers=4, log=None, detail=None, cache=None):
        state["calls"].append([Path(f) for f in files])
        out = {}
        for f in map(Path, files):
            if f.name in state["fail"]:
                (log or print)(f"{f.name}: LibreOffice could not render it: exit status 1; stderr: boom")
                continue
            out[f] = write_pdf(Path(out_dir) / (f.stem + ".pdf"), f"rendered {f.name}", note=f.name in state["noted"])
        return out
    monkeypatch.setattr(pdf_export, "renderer", lambda prefer="auto": ("libreoffice", ""))
    monkeypatch.setattr(render, "render_pdfs", fake_render)
    return state


def redlined_set(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    files = {"redline.docx": redline(), "commented.docx": build_commented(),
             "comment_in_deletion.docx": build_comment_in_deletion(),
             "21 13 13.docx": build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS")}
    out = []
    for name, data in files.items():
        (folder / name).write_bytes(data)
        out.append(folder / name)
    return out


def safe_task(tmp_path, **kwargs) -> DocumentTask:
    return DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=False, mode="safe", **kwargs)


def members(docx: Path):
    with zipfile.ZipFile(docx) as z:
        return {n: z.read(n).decode("utf-8", "replace") for n in z.namelist() if n.endswith(".xml")}


# ---- the button's plan -----------------------------------------------------------------------------------------------

def test_the_button_is_two_ordered_steps_comments_first_and_places_no_word(tmp_path, renderer):
    files = redlined_set(tmp_path / "docs")
    built = recipes.build("clean_pdf", {}, recipes.Context(files))
    assert built.export == "pdf" and built.sources == []
    assert [[o["op"] for o in st["spec"]["ops"]] for st in built.spec["stages"]] == [
        ["remove_comments"], ["resolve_tracked_changes"]]
    assert built.spec["stages"][1]["spec"]["ops"][0]["params"] == {"action": "accept"}
    outcome, errors = compiler.compose_submission(built.spec, compiler._Batch(files, None), [built.request])
    assert errors == [] and [st["name"] for st in outcome["stages"]] == ["Remove every comment",
                                                                         "Accept every tracked change"]
    # the order is the point: accepting first would take the comment's anchor away with Jack's deletion
    backwards = {**built.spec, "stages": list(reversed(built.spec["stages"]))}
    _, errors = compiler.compose_submission(backwards, compiler._Batch(files[2:3], None), [built.request])
    assert errors and "marks-in-change" in " ".join(errors)
    assert recipes.BY_ID["clean_pdf"].needs == ("pdf",) and recipes.BY_ID["clean_pdf"].group in recipes.GROUPS


def test_without_word_or_libreoffice_the_button_says_so_and_nothing_is_previewed(tmp_path, monkeypatch, no_ai):
    monkeypatch.setattr(pdf_export, "renderer", lambda prefer="auto": (None, "Word: Windows only; LibreOffice: none"))
    task = safe_task(tmp_path)
    task.attach(redlined_set(tmp_path / "docs")[:1])
    assert task.pdf_info() == {"available": False, "renderer": "",
                               "reason": "Saving PDFs needs Microsoft Word or LibreOffice on this computer, and "
                                         "neither was found."}
    view = task.perform("recipe", recipe="clean_pdf", inputs={})
    assert view["stage"] == "empty" and task.session.spec is None
    assert "needs Microsoft Word or LibreOffice" in task.messages[-1]["text"]


# ---- the run ---------------------------------------------------------------------------------------------------------

def test_clean_pdfs_from_checked_copies_and_the_word_documents_stay_as_they_were(tmp_path, renderer, no_ai):
    files = redlined_set(tmp_path / "docs")
    before = {f: f.read_bytes() for f in files}
    task = safe_task(tmp_path)
    task.attach(files)
    view = task.perform("recipe", recipe="clean_pdf", inputs={})
    assert view["stage"] == "preview" and task.session.model_free and task.session.export == "pdf"
    assert view["action"] == "Save clean PDFs" and view["export"] == "pdf" and view["set_aside"] == []
    assert task.messages[-1] == {**task.messages[-1], "role": "user",
                                 "text": "Save clean PDFs: every comment removed, every tracked change accepted"}
    view = task.perform("primary", token=view["token"])
    assert view["stage"] == "done" and view["left_untouched"] == []
    # every document has its PDF, from a copy that passed every check of both steps
    assert set(view["pdfs"]) == {str(f) for f in files}
    run = task.session.last_run_dir
    for f in files:
        pdf = Path(view["pdfs"][str(f)])
        assert pdf.parent == run / "pdf" and pdf.name == f.stem + ".pdf" and pdf_export.check_pdf(pdf) == ""
    assert all(item.status == "PASS" for item in task.session.last_batch.files)
    assert [len(c) for c in renderer["calls"]] == [len(files)]          # one render of the clean copies, nothing else
    for copy in renderer["calls"][0]:
        assert copy.parent == run / "outputs"                           # never an original
        xml = members(copy)
        assert not [m for part, text in xml.items() for m in MARKUP if m in text], copy.name
        assert "<w:comment " not in xml.get("word/comments.xml", "")
    report = (run / pdf_export.REPORT_NAME).read_text(encoding="utf-8")
    assert all(f"| {f.name} | pdf/{f.stem}.pdf | 1 |" in report for f in files)
    # the PDFs are the result; the working set, the history and Undo are as they were
    assert task.results == {} and view["results"] == {}
    assert task.working == {} and task.generation == 0 and task.history == [] and task.undo_points == []
    assert view["working"]["undo"] == ""
    assert [Path(p).resolve() for p in task.session.documents] == [f.resolve() for f in files]
    assert task.session.export == "" and task.session.spec is None
    assert {f: f.read_bytes() for f in files} == before
    said = task.messages[-1]["text"]
    assert said.startswith("4 clean PDFs are ready, made by LibreOffice: every comment removed and every tracked "
                           "change accepted") and "Your Word documents are as they were" in said


def test_pdfs_of_the_working_copies_and_undo_still_goes_back_before_the_last_edit(tmp_path, renderer, no_ai):
    files = redlined_set(tmp_path / "docs")[:2]
    task = safe_task(tmp_path)
    task.attach(files)
    view = task.perform("recipe", recipe="remove_highlight", inputs={})
    task.perform("primary", token=view["token"])
    copies = dict(task.working)
    assert task.generation == 1 and len(copies) == 2
    view = task.perform("recipe", recipe="clean_pdf", inputs={})
    view = task.perform("primary", token=view["token"])
    # made from the copies the last button delivered (their clean versions), not from the originals
    assert task.session.last_batch.files[0].source in {Path(c) for c in copies.values()}
    assert task.working == copies and task.generation == 1
    assert view["working"]["undo"] == "Remove highlighting" and len(view["pdfs"]) == 2
    # a later button's preview takes the PDFs off the window; Discard shows them again
    pdfs = dict(view["pdfs"])
    view = task.perform("recipe", recipe="remove_comments", inputs={})
    assert view["pdfs"] == {}
    task.discard_preview()
    assert task.view()["pdfs"] == pdfs
    task.undo()
    assert task.working == {} and task.generation == 0


def test_a_document_the_engine_cannot_clean_exactly_gets_no_pdf_and_says_why(tmp_path, renderer, no_ai):
    files = redlined_set(tmp_path / "docs")[:1]
    math = tmp_path / "docs" / "equation.docx"
    math.write_bytes(awkward("math"))                    # a tracked change inside an equation: Word settles it
    task = safe_task(tmp_path)
    task.attach(files + [math])
    view = task.perform("recipe", recipe="clean_pdf", inputs={})
    assert view["stage"] == "preview" and [e["name"] for e in view["set_aside"]] == ["equation.docx"]
    view = task.perform("primary", token=view["token"])
    assert list(view["pdfs"]) == [str(files[0])]
    assert [e["name"] for e in view["left_untouched"]] == ["equation.docx"] and view["left_untouched"][0]["reason"]
    assert "Without a PDF: equation.docx" in task.messages[-1]["text"]


def test_a_pdf_that_cannot_be_made_or_fails_its_check_is_left_untouched_with_the_reason(tmp_path, renderer, no_ai):
    files = redlined_set(tmp_path / "docs")[:3]
    renderer["fail"].add("commented.docx")
    renderer["noted"].add("comment_in_deletion.docx")
    task = safe_task(tmp_path)
    task.attach(files)
    view = task.perform("recipe", recipe="clean_pdf", inputs={})
    view = task.perform("primary", token=view["token"])
    assert list(view["pdfs"]) == [str(files[0])]
    reasons = {e["name"]: e["reason"] for e in view["left_untouched"]}
    assert reasons["commented.docx"] == ("Its PDF could not be made: LibreOffice could not render it: exit status 1; "
                                         "stderr: boom.")
    assert reasons["comment_in_deletion.docx"].startswith("Its PDF could not be made: page 1 carries 1 note(s)")
    run = task.session.last_run_dir
    assert not (run / "pdf" / "comment_in_deletion.pdf").exists()
    assert (run / pdf_export.FAILED_FOLDER / "comment_in_deletion.pdf").is_file()   # kept apart, for diagnosis
    report = (run / pdf_export.REPORT_NAME).read_text(encoding="utf-8")
    assert "| commented.docx | none: LibreOffice could not render it" in report


def test_the_pdf_check(tmp_path):
    assert pdf_export.check_pdf(write_pdf(tmp_path / "ok.pdf", "clean")) == ""
    assert "note" in pdf_export.check_pdf(write_pdf(tmp_path / "noted.pdf", "x", note=True))
    (tmp_path / "empty.pdf").write_bytes(b"")
    assert pdf_export.check_pdf(tmp_path / "empty.pdf").startswith("the PDF cannot be opened")
    (tmp_path / "text.pdf").write_bytes(b"not a pdf at all")
    assert pdf_export.check_pdf(tmp_path / "text.pdf")


def test_the_pdfs_survive_a_restart_while_they_are_where_they_were_saved(tmp_path, renderer, no_ai):
    files = redlined_set(tmp_path / "docs")[:1]
    journal = tmp_path / "conversation.json"
    task = safe_task(tmp_path, journal=journal)
    task.attach(files)
    view = task.perform("recipe", recipe="clean_pdf", inputs={})
    view = task.perform("primary", token=view["token"])
    again = safe_task(tmp_path, journal=journal)
    assert again.restore() and again.pdfs == task.pdfs
    Path(next(iter(task.pdfs.values()))).unlink()
    gone = safe_task(tmp_path, journal=journal)
    assert gone.restore() and gone.pdfs == {}


def test_the_window_shows_the_pdfs_and_whether_pdfs_can_be_made(tmp_path, renderer, no_ai, monkeypatch):
    from fastapi.testclient import TestClient
    from spec_surgeon.settings import Settings
    from spec_surgeon.web.server import TOKEN_HEADER, WindowState, create_app
    monkeypatch.setenv("SPEC_SURGEON_CONFIG_DIR", str(tmp_path / "config"))
    task = safe_task(tmp_path)
    state = WindowState(task, Settings(out_root=str(tmp_path / "out")), token="t", version="1.6.0")
    client = TestClient(create_app(state), headers={TOKEN_HEADER: "t"})
    client.post("/api/attach", json={"paths": [str(p) for p in redlined_set(tmp_path / "docs")[:1]]})

    def stream(route, body):
        import json
        with client.stream("POST", route, json=body) as r:
            return [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")][-1]["state"]
    view = stream("/api/recipe", {"recipe": "clean_pdf", "inputs": {}})
    assert view["pdf"] == {"available": True, "renderer": "LibreOffice", "reason": ""}
    assert view["action"]["label"] == "Save clean PDFs" and view["action"]["export"] == "pdf"
    view = stream("/api/primary", {"token": view["action"]["token"]})
    assert view["results"] == [] and view["action"]["export"] == ""
    assert [p["name"] for p in view["pdfs"]] == ["redline.pdf"] and Path(view["pdfs"][0]["output"]).is_file()
    assert client.get("/api/recipes").json()["recipes"][-1]["needs"] == ["pdf"]


# ---- with the real LibreOffice, when this computer has it --------------------------------------------------------------

@pytest.mark.skipif(render.pick("libreoffice") is None, reason="LibreOffice is not installed here")
def test_libreoffice_makes_a_clean_pdf_where_the_redlined_source_shows_its_markup(tmp_path, no_ai):
    files = redlined_set(tmp_path / "docs")[:1]                  # redline.docx: Jack deleted "black steel "
    task = safe_task(tmp_path)
    task.attach(files)
    view = task.perform("recipe", recipe="clean_pdf", inputs={})
    view = task.perform("primary", token=view["token"])
    pdf = Path(view["pdfs"][str(files[0])])
    with fitz.open(str(pdf)) as doc:
        clean = " ".join(page.get_text() for page in doc)
    assert "hot-dip galvanized" in clean and "black steel" not in clean and "Check the finish" not in clean
    assert clean.count("Moved sentence.") == 1                  # the move accepted: once, where it went
    source = render.render_pdfs(files, tmp_path / "source", "libreoffice")[files[0]]
    with fitz.open(str(source)) as doc:
        redlined = " ".join(page.get_text() for page in doc)
    assert "black steel" in redlined                            # what the PDF would carry without the clean step


def test_only_a_copy_that_passed_every_check_is_rendered(tmp_path, renderer):
    """The export renders what the run delivered and the checker passed, an identical copy included — never a copy
    that failed, one delivered UNVERIFIED, or a file whose run stopped."""
    from types import SimpleNamespace as Item
    out = tmp_path / "run" / "outputs"
    out.mkdir(parents=True)
    made = {name: (out / name) for name in ("passed.docx", "same.docx", "failed.docx", "unverified.docx")}
    for path in made.values():
        path.write_bytes(b"docx")
    files = [Item(source=tmp_path / "passed.docx", status="PASS", delivered_to=str(made["passed.docx"]), noop=False,
                  output=made["passed.docx"], unverified=[]),
             Item(source=tmp_path / "same.docx", status="PASS", delivered_to=None, noop=True,
                  output=made["same.docx"], unverified=[]),
             Item(source=tmp_path / "failed.docx", status="FAIL", delivered_to=None, noop=False,
                  output=made["failed.docx"], unverified=[]),
             Item(source=tmp_path / "unverified.docx", status="PASS", delivered_to=str(made["unverified.docx"]),
                  noop=False, output=made["unverified.docx"], unverified=["V2 (Text preservation)"]),
             Item(source=tmp_path / "stopped.docx", status="EXCEPTION", delivered_to=None, noop=False, output=None,
                  unverified=[])]
    s = Session(out_root=tmp_path / "out")
    s.last_batch, s.last_run_dir = Item(files=files), tmp_path / "run"
    s._export_pdfs(lambda m: None)
    assert renderer["calls"] == [[made["passed.docx"], made["same.docx"]]]
    assert set(s.last_pdfs.pdfs) == {tmp_path / "passed.docx", tmp_path / "same.docx"}


def test_only_a_spec_can_carry_an_export_and_a_new_outcome_drops_it():
    s = Session()
    with pytest.raises(ValueError):
        s._set_outcome(export="pdf")
    with pytest.raises(ValueError):
        s._set_outcome(spec={"ops": []}, export="docx")
    s._set_outcome(spec={"ops": []}, source="recipe", export="pdf")
    assert s.export == "pdf"
    s._set_outcome(spec={"ops": []}, source="recipe")
    assert s.export == ""


def test_a_document_without_a_pdf_handed_to_default_mode_says_what_happened(tmp_path, renderer, no_ai):
    files = redlined_set(tmp_path / "docs")[:2]
    renderer["fail"].add("commented.docx")
    task = safe_task(tmp_path)
    task.attach(files)
    view = task.perform("recipe", recipe="clean_pdf", inputs={})
    task.perform("primary", token=view["token"])
    task.hand_off([str(files[1])])
    entry = task.todo[str(files[1])]
    assert entry["reason"] == ("Its PDF could not be made: LibreOffice could not render it: exit status 1; stderr: "
                               "boom.")
    assert entry["decisions"] == ["Save clean PDFs: every comment removed, every tracked change accepted"]
    assert task.mode == "default"


def test_a_pdf_built_on_a_copy_delivered_unverified_keeps_its_mark(tmp_path, renderer, no_ai, monkeypatch):
    """A working copy delivered UNVERIFIED (Obliterate mode) keeps its mark on every later copy built on it
    (``_mark_unverified``), and on the PDF made from it: the PDF's clean copy passed, but against that copy."""
    from fastapi.testclient import TestClient
    from spec_surgeon.document_task import EARLIER_COPY
    from spec_surgeon.settings import Settings
    from spec_surgeon.web.server import TOKEN_HEADER, WindowState, create_app
    monkeypatch.setenv("SPEC_SURGEON_CONFIG_DIR", str(tmp_path / "config"))
    files = redlined_set(tmp_path / "docs")[:2]
    task = safe_task(tmp_path)
    task.attach(files)
    view = task.perform("recipe", recipe="remove_highlight", inputs={})
    task.perform("primary", token=view["token"])
    task.unverified[str(files[0])] = ["V2 (Text preservation)"]       # as an Obliterate-mode delivery leaves it
    view = task.perform("recipe", recipe="clean_pdf", inputs={})
    view = task.perform("primary", token=view["token"])
    assert set(view["pdfs"]) == {str(f) for f in files}
    assert view["pdf_unverified"] == {str(files[0]): [EARLIER_COPY + "V2 (Text preservation)"]}
    assert "1 is marked UNVERIFIED" in task.messages[-1]["text"]
    state = WindowState(task, Settings(out_root=str(tmp_path / "out")), token="t", version="1.6.0")
    payload = TestClient(create_app(state), headers={TOKEN_HEADER: "t"}).get("/api/state").json()
    marks = {p["source"]: p["unverified"] for p in payload["pdfs"]}
    assert marks == {str(files[0]): [EARLIER_COPY + "V2 (Text preservation)"], str(files[1]): []}
