"""The preview's sample verdict covers the page layout too (backlog P7): V6 and V7 run on the samples the preview
renders, with the run's own geometry code, so a sample's PASS is what the run will find; where the preview cannot
check the layout (no render; a chain's step whose reference is its own input) the sample says so, in the text Claude
reads and in what the window shows, and the layout check waits for the run."""
from __future__ import annotations

import copy
import io
import shutil
from pathlib import Path

import pytest

from make_fixtures import build
from spec_surgeon import render as render_mod
from spec_surgeon.batch import run_batch
from spec_surgeon.editspec import load_spec
from spec_surgeon.ooxml import block_full_text, paragraphs
from spec_surgeon.package import DocxPackage
from spec_surgeon.preview import make_chain_preview, make_preview
from spec_surgeon.ui_feedback import failed_preview_checks

SPECS = Path(__file__).resolve().parent.parent / "examples" / "specs"
# a change that leaves the pages as they are, so the source render is its layout reference (V7 asserted)
TITLE = {"request": "set the title property", "summary": "Set the Title property.", "mode": "untracked",
         "ops": [{"op": "set_document_properties", "params": {"set": {"title": "Wet-pipe sprinkler systems"}}}]}
SUBJECT = {**TITLE, "request": "set the subject property", "summary": "Set the Subject property.",
           "ops": [{"op": "set_document_properties", "params": {"set": {"subject": "Division 21"}}}]}


@pytest.fixture
def folder(tmp_path) -> Path:
    d = tmp_path / "specs"
    d.mkdir()
    (d / "21 13 13 – WET-PIPE.docx").write_bytes(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    return d


def fake_renderer(monkeypatch, *, shift_outputs: float = 0.0):
    """A renderer that draws each body paragraph of a .docx on one page, a line per paragraph — the same words at the
    same places for a source and the copy made from it, unless ``shift_outputs`` moves the copy's lines (a planted
    layout defect the preview must catch, as the run would)."""
    import pymupdf
    rendered = []

    def render_pdfs(files, out_dir, backend, workers=4, log=None, detail=None, cache=None):
        out = {}
        for f in files:
            f = Path(f)
            texts = [block_full_text(p) for p in paragraphs(DocxPackage(f.read_bytes()).xml("word/document.xml"))]
            dy = shift_outputs if "samples" in f.parts or "outputs" in f.parts else 0.0
            doc = pymupdf.open()
            page = doc.new_page(width=612, height=792)
            for i, t in enumerate(texts[:40]):
                if t.strip():
                    page.insert_text((72, 72 + 14 * i + dy), t[:60], fontsize=9)
            pdf = Path(out_dir) / (f.stem + ".pdf")
            pdf.parent.mkdir(parents=True, exist_ok=True)
            doc.save(str(pdf))
            out[f] = pdf
            rendered.append(f)
        return out
    monkeypatch.setattr(render_mod, "pick", lambda prefer="auto": "fake")
    monkeypatch.setattr(render_mod, "render_pdfs", render_pdfs)
    return rendered


def test_the_preview_checks_the_page_layout_of_its_samples_with_the_runs_own_checks(folder, tmp_path, monkeypatch):
    fake_renderer(monkeypatch)
    pv = make_preview(copy.deepcopy(TITLE), [str(folder)], tmp_path / "pv", renderer="fake", do_render=True)
    [sv] = pv.samples
    assert sv.passed is True and sv.checks["V6"] == "PASS" and sv.checks["V7"] == "PASS"
    assert sv.geometry["status"] == "PASS" and "checked" in sv.geometry["note"] and "at" not in sv.geometry
    text = pv.to_text()
    assert "page geometry (V7) PASS" in text and "checked at the run" not in text
    assert failed_preview_checks(sv) == []


def test_a_copy_whose_rendered_lines_moved_fails_its_sample_in_the_preview(folder, tmp_path, monkeypatch):
    fake_renderer(monkeypatch, shift_outputs=6.0)
    pv = make_preview(copy.deepcopy(TITLE), [str(folder)], tmp_path / "pv", renderer="fake", do_render=True)
    [sv] = pv.samples
    assert sv.checks["V7"] == "FAIL" and sv.passed is False
    assert sv.geometry["status"] == "FAIL" and "MOVED" in sv.geometry["note"]
    assert [f["check"] for f in failed_preview_checks(sv)] == ["V7"]
    assert "page geometry (V7) FAIL" in pv.to_text()


def test_without_a_render_the_sample_says_the_layout_is_checked_at_the_run(folder, tmp_path):
    pv = make_preview(copy.deepcopy(TITLE), [str(folder)], tmp_path / "pv", renderer="none", do_render=False)
    [sv] = pv.samples
    assert sv.passed is True and sv.checks["V7"] == "NA"
    assert sv.geometry == {"status": "NA", "at": "run",
                           "note": "Page layout is checked when the copies are created; no pages were rendered here."}
    assert "page geometry (V7): checked at the run" in pv.to_text()
    assert failed_preview_checks(sv) == []


def test_a_plan_with_no_layout_reference_by_design_says_why(folder, tmp_path, monkeypatch):
    fake_renderer(monkeypatch)
    spec = load_spec(SPECS / "change-all-font-arial.json")
    pv = make_preview(spec, [str(folder)], tmp_path / "pv", renderer="fake", do_render=True)
    [sv] = pv.samples
    assert sv.passed is True and sv.checks["V7"] == "NA"
    assert sv.geometry["status"] == "NA" and "by design" in sv.geometry["note"]


def test_a_chain_checks_the_layout_of_its_last_step_only_when_that_step_needs_no_earlier_render(
        folder, tmp_path, monkeypatch):
    fake_renderer(monkeypatch)
    two = {"name": "two steps", "stages": [{"name": "step 1", "spec": copy.deepcopy(TITLE)},
                                           {"name": "step 2", "spec": copy.deepcopy(SUBJECT)}]}
    pv = make_chain_preview(two, [str(folder)], tmp_path / "pv", renderer="fake", do_render=True)
    [sv] = pv.samples
    assert sv.passed is True and sv.checks["V7"] == "NA"
    assert sv.geometry["status"] == "NA" and sv.geometry["at"] == "run" and "steps" in sv.geometry["note"]
    assert "page geometry (V7): checked at the run" in pv.to_text()
    one = {"name": "one step", "stages": [{"name": "step 1", "spec": copy.deepcopy(TITLE)}]}
    pv = make_chain_preview(one, [str(folder)], tmp_path / "pv1", renderer="fake", do_render=True)
    [sv] = pv.samples
    assert sv.checks["V7"] == "PASS" and sv.geometry["status"] == "PASS"
    last_none = {"name": "font last", "stages": [{"name": "step 1", "spec": copy.deepcopy(TITLE)},
                                                 {"name": "step 2", "spec": load_spec(SPECS / "change-all-font-arial.json")}]}
    pv = make_chain_preview(last_none, [str(folder)], tmp_path / "pv2", renderer="fake", do_render=True)
    [sv] = pv.samples
    assert sv.checks["V7"] == "NA" and "by design" in sv.geometry["note"]


def test_the_window_and_the_contract_carry_the_layout_note(folder, tmp_path, monkeypatch):
    from spec_surgeon.document_task import DocumentTask
    from spec_surgeon.gui import Session
    fake_renderer(monkeypatch, shift_outputs=6.0)
    session = Session(out_root=tmp_path / "out", visual_check=False)
    task = DocumentTask(session, render=True, mode="safe")
    task.attach(sorted(folder.glob("*.docx")))
    task.perform("recipe", recipe="remove_highlight", inputs={})
    [sample] = task._samples_view()["samples"]                 # what the server sends the page (docs/WEB_API.md)
    assert sample["geometry"]["status"] in ("PASS", "NA", "FAIL") and sample["geometry"]["note"]
    assert set(sample) >= {"source", "name", "images", "pages_before", "pages_after", "passed", "failures", "geometry"}


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
def test_with_a_real_renderer_the_previews_layout_verdict_is_the_runs(folder, tmp_path):
    spec = load_spec(SPECS / "remove-all-headers-footers.json")
    pv = make_preview(spec, [str(folder)], tmp_path / "pv", renderer="libreoffice", do_render=True)
    [sv] = pv.samples
    batch = run_batch([str(folder)], "edit-spec", {}, tmp_path / "run", workers=1, renderer="libreoffice", spec=spec)
    [fr] = batch.files
    assert sv.checks["V7"] == fr.record.get("V7").status == "PASS", (sv.geometry, fr.record.get("V7").summary)
    assert sv.checks["V6"] == fr.record.get("V6").status == "PASS"
