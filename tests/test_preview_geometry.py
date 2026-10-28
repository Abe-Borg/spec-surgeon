"""The preview's sample verdict covers the page layout too (backlog P7): V6 and V7 run on the samples the preview
renders, with the run's own geometry code, so a sample's PASS is what the run will find; where the preview cannot
check the layout (no render; a chain's step whose reference is its own input; a custom method's trial, whose plan the
preview does not hold; a layout reference it could not render or compare) the sample says so, in the text Claude
reads and in what the window shows, and the layout check waits for the run. The note says what did happen: a sample
whose pages were rendered (its before/after pictures and page counts are real) never says no pages were rendered."""
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
from spec_surgeon.preview import (GEOMETRY_AT_RUN, GEOMETRY_RENDERED_AT_RUN, GEOMETRY_RENDERED_NOT_COMPARED,
                                  make_chain_preview, make_preview, preview_generated)
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
    assert sv.geometry == GEOMETRY_AT_RUN and not sv.source_pdf and not sv.images and sv.pages_before is None
    assert "page geometry (V7): checked at the run" in pv.to_text()
    assert failed_preview_checks(sv) == []


def _trial(folder: Path, tmp_path: Path):
    """A custom method's trial as ``preview_generated`` reads one: a source, its output and a verdict, no plan."""
    from types import SimpleNamespace
    [f] = sorted(folder.glob("*.docx"))
    out = tmp_path / "trial" / f.name
    out.parent.mkdir(parents=True)
    out.write_bytes(f.read_bytes())
    return SimpleNamespace(name="op", trials=[SimpleNamespace(source=str(f), output=str(out), passed=True,
                                                               record=None, unverified=None, problems=[])])


def test_a_rendered_sample_whose_plan_the_preview_does_not_hold_says_its_pages_were_rendered(folder, tmp_path, monkeypatch):
    # live QA's F07: a custom method's trial is rendered (pictures, page counts, the visual check's pages) but has no
    # plan for the preview to check the layout against; its note said no pages were rendered
    rendered = fake_renderer(monkeypatch)
    gp = preview_generated(_trial(folder, tmp_path), tmp_path / "pv", renderer="fake", do_render=True)
    [sv] = gp.samples
    assert sv.plan is None and len(rendered) == 2 and sv.source_pdf and sv.output_pdf
    assert sv.pages_before == sv.pages_after == 1 and sv.images and sv.look_pages == [1]
    assert sv.geometry == GEOMETRY_RENDERED_AT_RUN
    assert sv.geometry["status"] == "NA" and sv.geometry["at"] == "run"          # what the page keys off, unchanged
    assert "Pages were rendered here" in sv.geometry["note"] and "no pages" not in sv.geometry["note"]
    assert "does not hold this sample's plan" in sv.geometry["note"]
    assert "page geometry (V7): checked at the run — Pages were rendered here" in gp.to_text()
    assert sv.passed is True and "V7" not in sv.checks and failed_preview_checks(sv) == []
    assert not [w for w in gp.warnings if "checked when the copies" in w]        # nothing failed: no warning either


def test_without_a_render_a_custom_methods_sample_says_no_pages_were_rendered(folder, tmp_path):
    gp = preview_generated(_trial(folder, tmp_path), tmp_path / "pv", renderer="none", do_render=False)
    [sv] = gp.samples
    assert sv.plan is None and not sv.source_pdf and sv.geometry == GEOMETRY_AT_RUN


@pytest.mark.parametrize("broken", ["geometry_references", "compare_pair"])
def test_a_comparison_the_preview_could_not_make_says_the_pages_were_rendered(folder, tmp_path, monkeypatch, broken):
    # the references could not be rendered, or the rendered pages could not be compared: a warning beside the plan
    # (the run makes the comparison again), and the sample's note says the pages were rendered, not that none were
    from spec_surgeon import batch as batch_mod

    def boom(*a, **k):
        raise RuntimeError("planted")
    monkeypatch.setattr(batch_mod, broken, boom)
    fake_renderer(monkeypatch)
    pv = make_preview(copy.deepcopy(TITLE), [str(folder)], tmp_path / "pv", renderer="fake", do_render=True)
    [sv] = pv.samples
    assert sv.plan is not None and sv.source_pdf and sv.output_pdf and sv.pages_before == sv.pages_after == 1
    assert sv.geometry == GEOMETRY_RENDERED_NOT_COMPARED
    assert sv.geometry["status"] == "NA" and sv.geometry["at"] == "run"
    assert "Pages were rendered here" in sv.geometry["note"] and "no pages" not in sv.geometry["note"]
    assert "could not be rendered or compared" in sv.geometry["note"]
    assert sv.passed is True and sv.checks["V7"] == "NA" and failed_preview_checks(sv) == []
    [warning] = [w for w in pv.warnings if "checked when the copies are created" in w]
    assert "planted" in warning and ("references could not be rendered" in warning
                                     or "pages could not be compared" in warning)
    assert "page geometry (V7): checked at the run — Pages were rendered here" in pv.to_text()


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
