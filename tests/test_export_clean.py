"""Export's three kinds (``exports``, ``DocumentTask.export``, ``POST /api/export``): Word (Tracked Changes ON) writes
each edited copy as it is; Word (Tracked Changes OFF) and PDF (Clean) first make each copy's clean version — a fixed
spec through the gate and a checked run, comments kept or removed, every tracked change accepted — then, for PDF, a
checked PDF of each clean copy that passed. The edited copies, the history, the undo points and the request open stay
as they were; a document whose clean version or PDF cannot be made gets no file and is named with why. No AI call
anywhere. Export covers every copy the window lists, the ones a run still checking other documents delivered among
them, and Safe mode's Save clean PDFs button is gone (the owner's decision, 2026-10-29)."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from fx_comments import build_comment_in_deletion, build_commented
from fx_revisions import awkward, redline
from make_fixtures import build
from spec_surgeon import campaign_planner, compiler, exports, llm, pdf_export, recipes, recovery, render, vision
from spec_surgeon.document_task import EARLIER_COPY, DocumentTask
from spec_surgeon.gui import Session
from spec_surgeon.verify.geometry import fitz

MARKUP = ("<w:ins ", "<w:del ", "<w:moveFrom ", "<w:moveTo ", "<w:rPrChange", "<w:pPrChange")
COMMENTS = ("commentReference", "commentRangeStart", "commentRangeEnd")


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
        raise AssertionError("Export makes no AI call")
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


def redlined_set(folder: Path, names=None):
    folder.mkdir(parents=True, exist_ok=True)
    files = {"redline.docx": redline, "commented.docx": build_commented,
             "comment_in_deletion.docx": build_comment_in_deletion,
             "21 13 13.docx": lambda: build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"),
             "equation.docx": lambda: awkward("math")}
    out = []
    for name in names or ["redline.docx", "commented.docx", "comment_in_deletion.docx", "21 13 13.docx"]:
        (folder / name).write_bytes(files[name]())
        out.append(folder / name)
    return out


def safe_task(tmp_path, **kwargs) -> DocumentTask:
    return DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=False, mode="safe", **kwargs)


def edited(tmp_path, names=None, **kwargs) -> DocumentTask:
    """A Safe-mode task whose documents went through one button (Remove highlighting): one working copy each, their
    tracked changes and comments as the originals had them."""
    task = safe_task(tmp_path, **kwargs)
    task.attach(redlined_set(tmp_path / "docs", names))
    view = task.perform("recipe", recipe="remove_highlight", inputs={})
    view = task.perform("primary", token=view["token"])
    assert view["stage"] == "done" and len(task.working) == len(task.attachments)
    return task


def members(docx: Path):
    with zipfile.ZipFile(docx) as z:
        return {n: z.read(n).decode("utf-8", "replace") for n in z.namelist() if n.endswith(".xml")}


def markup(docx: Path, marks=MARKUP):
    return [m for text in members(docx).values() for m in marks if m in text]


def state_of(task):
    """What an export must leave as it was: the copies' bytes, the working set, the history, the undo points, the
    stage, what was saved and the open request's approval."""
    return {"bytes": {k: Path(v).read_bytes() for k, v in task.working.items()}, "working": dict(task.working),
            "generation": task.generation, "history": list(task.history), "undo": json.dumps(task.undo_points),
            "stage": task.stage, "exported": dict(task.exported), "decisions": list(task.decisions),
            "token": task.action_token()}


# ---- the fixed plans -------------------------------------------------------------------------------------------------

def test_the_clean_plans_are_fixed_place_no_word_and_pass_the_gate(tmp_path):
    files = redlined_set(tmp_path / "docs")
    accepted, request = exports.clean_spec("accepted")
    assert [o["op"] for o in accepted["ops"]] == ["resolve_tracked_changes"]
    assert accepted["ops"][0]["params"] == {"action": "accept"} and accepted["mode"] == "untracked"
    outcome, errors = compiler.compose_submission(accepted, compiler._Batch(files, None), [request])
    assert errors == [] and outcome["ops"] == accepted["ops"]
    clean, request = exports.clean_spec("pdf")
    assert [[o["op"] for o in st["spec"]["ops"]] for st in clean["stages"]] == [
        ["remove_comments"], ["resolve_tracked_changes"]]
    outcome, errors = compiler.compose_submission(clean, compiler._Batch(files, None), [request])
    assert errors == [] and [st["name"] for st in outcome["stages"]] == ["Remove every comment",
                                                                         "Accept every tracked change"]
    # the order is the point: accepting first would take the comment's anchor away with Jack's deletion
    backwards = {**clean, "stages": list(reversed(clean["stages"]))}
    _, errors = compiler.compose_submission(backwards, compiler._Batch(files[2:3], None), [request])
    assert errors and "marks-in-change" in " ".join(errors)
    with pytest.raises(ValueError):
        exports.clean_spec("word")
    assert exports.LABELS == {"word": "Word (Tracked Changes ON)", "accepted": "Word (Tracked Changes OFF)",
                              "pdf": "PDF (Clean)"}


def test_save_clean_pdfs_is_no_longer_a_safe_mode_button():
    assert "clean_pdf" not in recipes.BY_ID
    assert all("pdf" not in r.needs for r in recipes.RECIPES)
    assert not hasattr(Session(), "last_pdfs") and not hasattr(Session(), "export")


# ---- Word (Tracked Changes OFF) --------------------------------------------------------------------------------------

def test_tracked_changes_off_accepts_every_change_keeps_the_comments_and_leaves_the_copies_as_they_were(tmp_path, no_ai):
    task = edited(tmp_path)
    before = state_of(task)
    assert all(markup(Path(c)) for k, c in task.working.items() if "redline" in k)    # a redline to accept
    dest = tmp_path / "Clean Word"
    dest.mkdir()
    made = task.export(dest, "accepted")
    assert made["kind"] == "accepted" and made["saved"] == 3 and made["already"] == 0
    # the one a deletion would take a comment's anchor away from is named with the engine's reason, in plain words
    assert list(made["missing"]) == ["comment_in_deletion.docx"]
    assert made["missing"]["comment_in_deletion.docx"].startswith("the tracked change accepted away holds a comment")
    assert "op #" not in made["missing"]["comment_in_deletion.docx"]
    assert sorted(p.name for p in dest.iterdir()) == ["21 13 13.docx", "commented.docx", "redline.docx"]
    for saved in dest.iterdir():
        assert markup(saved) == [], saved.name                                   # every tracked change accepted
    assert markup(dest / "commented.docx", COMMENTS)                             # and the comments kept
    # the edited copies, the history, Undo and what counts as saved are as they were
    assert state_of(task) == before and task.unsaved_copies() == list(task.working.values())
    said = task.messages[-1]["text"]
    assert said.startswith(f"Saved 3 Word copies with every tracked change accepted to {dest.resolve()}. Comments "
                           "stay; your edited copies are as they were. Not saved: comment_in_deletion.docx — the "
                           "tracked change accepted away holds a comment mark")
    assert task.export_folder == str(dest.resolve())
    # its run is the window's own, in the scratch folder, with its records
    runs = list((Path(task.session.out_root) / exports.EXPORT_FOLDER).iterdir())
    assert len(runs) == 1 and runs[0].name.endswith("-accepted") and (runs[0] / "BATCH_REPORT.md").is_file()
    # the same export again writes nothing new
    again = task.export(dest, "accepted")
    assert again["saved"] == 0 and again["already"] == 3 and len(list(dest.iterdir())) == 3


def test_a_document_the_engine_cannot_clean_exactly_gets_no_file_and_says_why(tmp_path, renderer, no_ai):
    task = edited(tmp_path, ["redline.docx", "equation.docx"])     # a tracked change inside an equation
    dest = tmp_path / "Saved"
    dest.mkdir()
    made = task.export(dest, "pdf")
    assert made["saved"] == 1 and list(made["missing"]) == ["equation.docx"]
    assert made["missing"]["equation.docx"] == "a tracked change inside an equation (Jeremy) — accepting it is done in Word"
    assert [p.name for p in dest.iterdir()] == ["redline.pdf"]
    assert "Not saved: equation.docx — a tracked change inside an equation" in task.messages[-1]["text"]


def styled_redline() -> bytes:
    """The redline with one tracked change of its own in a style definition (Jack made a style bold), which the accept
    step flags and leaves for Word (``resolve_tracked_changes``, Codex review on PR #150)."""
    import io
    src = zipfile.ZipFile(io.BytesIO(redline()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "word/styles.xml":
                text = data.decode("utf-8")
                at = text.index("</w:rPr>", text.index("<w:style "))
                change = ('<w:rPrChange w:id="9901" w:author="Jack" w:date="2026-01-01T00:00:00Z"><w:rPr/>'
                          '</w:rPrChange>')
                data = (text[:at] + change + text[at:]).encode("utf-8")
            z.writestr(info, data)
    return out.getvalue()


def test_a_tracked_change_the_accept_step_leaves_in_a_style_gets_no_clean_file(tmp_path, renderer, no_ai):
    """Both clean kinds promise every tracked change accepted, with no preview to show the accept step's flag: a copy
    that keeps one in its style definitions is named with why and gets no file, never one with pending changes."""
    task = safe_task(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "styled.docx").write_bytes(styled_redline())
    (docs / "redline.docx").write_bytes(redline())
    task.attach([docs / "styled.docx", docs / "redline.docx"])
    view = task.perform("recipe", recipe="remove_highlight", inputs={})
    task.perform("primary", token=view["token"])
    # the working copy is the redline: its text, a header and a comment carry tracked changes besides the style
    assert exports.pending_changes(Path(task.working[str(docs / "styled.docx")])) == (
        "tracked changes stay pending in the text (13) and the style definitions (1) and a header (1) and the comments "
        "(1), which the accept step leaves as they are — accept them in Word, then export again")
    for kind, made_name in (("accepted", "redline.docx"), ("pdf", "redline.pdf")):
        dest = tmp_path / kind
        dest.mkdir()
        made = task.export(dest, kind)
        assert [p.name for p in dest.iterdir()] == [made_name] and list(made["missing"]) == ["styled.docx"]
        assert made["missing"]["styled.docx"] == ("tracked changes stay pending in the style definitions (1), which "
                                                  "the accept step leaves as they are — accept them in Word, then "
                                                  "export again")
    assert exports.pending_changes(tmp_path / "accepted" / "redline.docx") == ""


# ---- PDF (Clean) ------------------------------------------------------------------------------------------------------

def test_clean_pdfs_from_checked_clean_copies_and_the_edited_copies_stay_as_they_were(tmp_path, renderer, no_ai):
    task = edited(tmp_path)
    before = state_of(task)
    dest = tmp_path / "PDF"
    dest.mkdir()
    made = task.export(dest, "pdf")
    assert made == {"folder": str(dest.resolve()), "kind": "pdf", "saved": 4, "already": 0, "missing": {}}
    names = sorted(p.name for p in dest.iterdir())
    assert names == ["21 13 13.pdf", "comment_in_deletion.pdf", "commented.pdf", "redline.pdf"]
    for pdf in dest.iterdir():
        assert pdf_export.check_pdf(pdf) == ""
    # one render, of the clean copies in the export's own run folder in the scratch folder — never a working copy
    run = next((Path(task.session.out_root) / exports.EXPORT_FOLDER).iterdir())
    assert run.name.endswith("-pdf") and [len(c) for c in renderer["calls"]] == [4]
    for clean in renderer["calls"][0]:
        assert clean.parent == run / "outputs"
        assert markup(clean, MARKUP + COMMENTS) == [], clean.name
        assert "<w:comment " not in members(clean).get("word/comments.xml", "")
    report = (run / pdf_export.REPORT_NAME).read_text(encoding="utf-8")
    assert all(f"| {Path(k).name} | pdf/{Path(k).stem}.pdf | 1 |" in report for k in task.working)
    assert state_of(task) == before                     # the edited copies keep their comments and tracked changes
    said = task.messages[-1]["text"]
    assert said == (f"Saved 4 clean PDFs to {dest.resolve()}, made by LibreOffice: every comment removed and every "
                    "tracked change accepted, each from a copy the checks passed. Your edited copies are as they were.")


def test_a_pdf_that_cannot_be_made_or_fails_its_check_is_named_with_the_reason(tmp_path, renderer, no_ai):
    task = edited(tmp_path, ["redline.docx", "commented.docx", "comment_in_deletion.docx"])
    renderer["fail"].add("commented.docx")
    renderer["noted"].add("comment_in_deletion.docx")
    dest = tmp_path / "PDF"
    dest.mkdir()
    made = task.export(dest, "pdf")
    assert [p.name for p in dest.iterdir()] == ["redline.pdf"]
    assert made["missing"]["commented.docx"] == ("its PDF could not be made: LibreOffice could not render it: exit "
                                                 "status 1; stderr: boom")
    assert made["missing"]["comment_in_deletion.docx"].startswith("its PDF could not be made: page 1 carries 1 note(s)")
    run = next((Path(task.session.out_root) / exports.EXPORT_FOLDER).iterdir())
    assert (run / pdf_export.FAILED_FOLDER / "comment_in_deletion.pdf").is_file()   # kept apart, for diagnosis


def test_without_word_or_libreoffice_pdf_says_so_before_any_folder_is_asked(tmp_path, monkeypatch, no_ai):
    monkeypatch.setattr(pdf_export, "renderer", lambda prefer="auto": (None, "Word: Windows only; LibreOffice: none"))
    task = edited(tmp_path, ["redline.docx"])
    assert task.pdf_info() == {"available": False, "renderer": "",
                               "reason": "Saving PDFs needs Microsoft Word or LibreOffice on this computer, and "
                                         "neither was found."}
    with pytest.raises(ValueError, match="needs Microsoft Word or LibreOffice"):
        task.export_ready("pdf")
    assert task.export_ready("accepted") == task.working          # the Word kinds need no renderer
    # a renderer installed since is seen at the next export
    monkeypatch.setattr(pdf_export, "renderer", lambda prefer="auto": ("libreoffice", ""))
    assert task.export_ready("pdf") == task.working and task.pdf_info()["available"]


def test_a_pdf_built_on_a_copy_delivered_unverified_says_so(tmp_path, renderer, no_ai):
    task = edited(tmp_path, ["redline.docx", "commented.docx"])
    first = next(iter(task.working))
    task.unverified[first] = [EARLIER_COPY + "V2 (Text preservation)"]       # as an Obliterate delivery leaves it
    dest = tmp_path / "PDF"
    dest.mkdir()
    task.export(dest, "pdf")
    assert "1 was made from an edited copy delivered UNVERIFIED; its failed checks are beside it in the window." \
        in task.messages[-1]["text"]


# ---- every kind: the folder, the inputs, the request open --------------------------------------------------------------

def test_every_kind_refuses_the_scratch_folder_and_a_folder_where_a_file_would_land_on_an_input(tmp_path, renderer):
    task = edited(tmp_path, ["redline.docx", "commented.docx"])
    originals = tmp_path / "docs"
    for kind in exports.KINDS:
        with pytest.raises(ValueError, match="inside the app's scratch folder"):
            task.check_export(task.session.out_root, kind)
        with pytest.raises(ValueError, match="is not a folder"):
            task.check_export(tmp_path / "missing", kind)
    # the Word kinds would land on the originals under their own names; a PDF never does
    for kind in ("word", "accepted"):
        with pytest.raises(ValueError, match="one of the files this conversation works on"):
            task.export(originals, kind)
    assert sorted(p.name for p in originals.iterdir()) == ["commented.docx", "redline.docx"]
    assert not (Path(task.session.out_root) / exports.EXPORT_FOLDER).exists()      # refused before any run
    task.export(originals, "pdf")
    assert sorted(p.name for p in originals.iterdir()) == ["commented.docx", "commented.pdf", "redline.docx",
                                                           "redline.pdf"]
    with pytest.raises(ValueError, match="Export saves Word with tracked changes on or off, or a clean PDF"):
        task.export_ready("docx")
    with pytest.raises(ValueError, match="no edited copies to save yet"):
        safe_task(tmp_path / "other").export_ready("pdf")


def test_someone_elses_file_under_the_name_is_left_alone_and_the_new_one_numbered(tmp_path, renderer):
    task = edited(tmp_path, ["redline.docx"])
    dest = tmp_path / "PDF"
    dest.mkdir()
    (dest / "redline.pdf").write_bytes(b"someone else's")
    made = task.export(dest, "pdf")
    assert made["saved"] == 1 and (dest / "redline.pdf").read_bytes() == b"someone else's"
    assert pdf_export.check_pdf(dest / "redline (2).pdf") == ""


def test_an_export_leaves_the_open_preview_and_its_approval_as_they_were(tmp_path, renderer, no_ai):
    task = edited(tmp_path, ["redline.docx", "commented.docx"])
    view = task.perform("recipe", recipe="remove_comments", inputs={})           # the next button's preview waits
    assert view["stage"] == "preview"
    before = state_of(task)
    dest = tmp_path / "PDF"
    dest.mkdir()
    view = task.perform("export", kind="pdf", folder=str(dest))                  # as the window's job runs it
    assert view["stage"] == "preview" and state_of(task) == before and view["token"] == before["token"]
    assert len(list(dest.iterdir())) == 2
    view = task.perform("primary", token=view["token"])                          # and Create still creates
    assert view["stage"] == "done" and task.generation == 2


def test_export_covers_the_copies_a_run_still_checking_other_documents_delivered(tmp_path):
    """The owner's run of 2026-10-28: nine copies listed under Your edited copies while recovery still checked three
    documents, and Export greyed out — it counted the working set only, which those copies join at the end."""
    task = edited(tmp_path, ["redline.docx", "commented.docx"])
    first, second = task.working
    delivered = Path(task.working[first]).parent.parent / "recovered" / "redline.docx"
    delivered.parent.mkdir()
    delivered.write_bytes(Path(task.working[first]).read_bytes())
    task.results = {first: str(delivered)}                    # recovery delivered it; the working set is the old one
    assert task.export_set() == {first: str(delivered), second: task.working[second]}
    view = task.view()
    assert view["copies"] == 2 and str(delivered) in view["unsaved"]
    dest = tmp_path / "Saved"
    dest.mkdir()
    assert task.export(dest)["saved"] == 2
    assert task.exported[str(delivered)]["to"] == str(dest / "redline.docx")
    # a document no longer attached is not exported for it
    task.results[str(tmp_path / "gone.docx")] = str(delivered)
    assert str(tmp_path / "gone.docx") not in task.export_set()
    # nothing at all listed: nothing to export
    fresh = safe_task(tmp_path / "fresh")
    fresh.attach(redlined_set(tmp_path / "fresh-docs", ["redline.docx"]))
    assert fresh.export_set() == {} and fresh.view()["copies"] == 0


def test_a_journal_from_before_export_kinds_still_restores(tmp_path):
    journal = tmp_path / "conversation.json"
    task = edited(tmp_path, ["redline.docx"], journal=journal)
    data = json.loads(journal.read_text(encoding="utf-8"))
    assert "pdfs" not in data
    pdf = write_pdf(tmp_path / "old" / "redline.pdf", "an old clean PDF")
    data["pdfs"] = {next(iter(task.working)): str(pdf)}
    for point in data["undo"]:
        point["pdfs"] = {}
    journal.write_text(json.dumps(data), encoding="utf-8")
    again = safe_task(tmp_path, journal=journal)
    assert again.restore() and again.working == task.working and len(again.undo_points) == 1
    assert "pdfs" not in again.undo_points[0] and not hasattr(again, "pdfs")


# ---- the window ----------------------------------------------------------------------------------------------------

def test_the_export_route_streams_a_clean_export_and_refuses_before_the_dialog(tmp_path, renderer, no_ai, monkeypatch):
    from fastapi.testclient import TestClient
    from spec_surgeon.settings import Settings
    from spec_surgeon.web.server import TOKEN_HEADER, WindowState, create_app
    from test_export import FolderShell
    monkeypatch.setenv("SPEC_SURGEON_CONFIG_DIR", str(tmp_path / "config"))
    task = edited(tmp_path, ["redline.docx", "commented.docx"])
    state = WindowState(task, Settings(), token="t", version="1.6.0")
    client = TestClient(create_app(state), headers={TOKEN_HEADER: "t"})
    view = client.get("/api/state").json()
    assert view["export"]["copies"] == 2 and view["pdf"] == {"available": True, "renderer": "LibreOffice", "reason": ""}
    assert "pdfs" not in view and "export" not in view["action"]
    dest = tmp_path / "PDF"
    dest.mkdir()
    state.shell = FolderShell(str(dest))
    with client.stream("POST", "/api/export", json={"kind": "pdf"}) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    assert events[-1]["type"] == "done" and not events[-1]["state"]["busy"]
    assert {"type": "status", "kind": "creating", "text": "Creating and checking the clean copies…"} in events
    assert {"type": "status", "kind": "rendering", "text": "Saving the clean PDFs…"} in events
    assert sorted(p.name for p in dest.iterdir()) == ["commented.pdf", "redline.pdf"]
    assert events[-1]["state"]["messages"][-1]["text"].startswith("Saved 2 clean PDFs to ")
    assert state.shell.starts == [str(Path.home())] and state.settings.export_folder == str(dest.resolve())
    # Word (Tracked Changes ON) answers with the view, as before; the dialog opens where the last export went
    words = tmp_path / "Word"
    words.mkdir()
    state.shell = FolderShell(str(words))
    view = client.post("/api/export", json={"kind": "word"}).json()
    assert view["export"]["unsaved"] == 0 and state.shell.starts == [str(dest.resolve())]
    # refused before the dialog: an unknown kind, or no program that makes PDFs; a dismissed dialog changes nothing
    state.shell = FolderShell(str(words))
    assert client.post("/api/export", json={"kind": "rtf"}).status_code == 400 and state.shell.starts == []
    monkeypatch.setattr(pdf_export, "renderer", lambda prefer="auto": (None, "none"))
    refused = client.post("/api/export", json={"kind": "pdf"})
    assert refused.status_code == 400 and "Microsoft Word or LibreOffice" in refused.json()["error"]
    assert state.shell.starts == []
    state.shell = FolderShell("")
    assert client.post("/api/export", json={"kind": "accepted"}).json() == {"cancelled": True}
    # a folder that refuses is said before the job starts
    refused = client.post("/api/export", json={"kind": "accepted", "folder": str(tmp_path / "docs")})
    assert refused.status_code == 400 and "one of the files this conversation works on" in refused.json()["error"]
    assert not state.busy


# ---- what the assistants are told ----------------------------------------------------------------------------------

def test_every_assistant_knows_export_makes_the_pdfs():
    """The owner's screenshot of 2026-10-28: asked for clean PDFs, the assistant said it could not make them."""
    for prompt in (compiler.system_prompt(chat=True), compiler.system_prompt()):
        assert "Word (Tracked Changes ON)" in prompt and "Word (Tracked Changes OFF)" in prompt
        assert "PDF (Clean)" in prompt and "never say PDFs cannot be made" in prompt
    assert recovery.SYSTEM.endswith(exports.PROMPT_NOTE) and recovery.ADAPTIVE_SYSTEM.endswith(exports.PROMPT_NOTE)
    assert campaign_planner.SYSTEM.endswith(exports.PROMPT_NOTE)
    assert "never say PDFs cannot be made" in exports.PROMPT_NOTE


# ---- with the real LibreOffice, when this computer has it --------------------------------------------------------------

@pytest.mark.skipif(render.pick("libreoffice") is None, reason="LibreOffice is not installed here")
def test_libreoffice_makes_a_clean_pdf_where_the_redlined_copy_shows_its_markup(tmp_path, no_ai):
    task = edited(tmp_path, ["redline.docx"])                  # Jack deleted "black steel "
    dest = tmp_path / "PDF"
    dest.mkdir()
    assert task.export(dest, "pdf")["saved"] == 1
    with fitz.open(str(dest / "redline.pdf")) as doc:
        clean = " ".join(page.get_text() for page in doc)
    assert "hot-dip galvanized" in clean and "black steel" not in clean and "Check the finish" not in clean
    assert clean.count("Moved sentence.") == 1                  # the move accepted: once, where it went
    copy = Path(next(iter(task.working.values())))
    source = render.render_pdfs([copy], tmp_path / "source", "libreoffice")[copy]
    with fitz.open(str(source)) as doc:
        redlined = " ".join(page.get_text() for page in doc)
    assert "black steel" in redlined                            # what the PDF would carry without the clean step
