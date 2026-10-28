"""The preview's sample verdict covers the page layout too (backlog P7): V6 and V7 run on the samples the preview
renders, with the run's own geometry code, so a sample's PASS is what the run will find; where the preview cannot
check the layout (no render; a chain's step whose reference is its own input; a custom method's trial kept without
its plan; a layout reference it could not render or compare) the sample says so, in the text Claude reads and in what
the window shows, and the layout check waits for the run. A custom method's samples and copies get V6 and V7 by the
plan the method predicted, in the preview and in the run (the deep-dive stress test's D3). The note says what did happen: a sample
whose pages were rendered (its before/after pictures and page counts are real) never says no pages were rendered.

V6's warning (the stress-test finding F4): the words the original's rendered pages show that the copy's do not, counted
over the whole document (``verify.geometry.shown_words``) less what the plan removes — a copy that hides text without
moving a word the page-for-page comparison sees. A warning beside the sample, in the chat and in the visual check's
brief, never a failed sample: a layout change moves words between pages, and header and footer words repeat per page."""
from __future__ import annotations

import copy
import json
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


def fake_renderer(monkeypatch, *, shift_outputs: float = 0.0, hide=(), split_outputs: bool = False):
    """A renderer that draws each body paragraph of a .docx on one page, a line per paragraph — the same words at the
    same places for a source and the copy made from it, unless ``shift_outputs`` moves the copy's lines (a planted
    layout defect the preview must catch, as the run would), ``hide`` names words the copy's render leaves out (text
    hidden without moving a line: what V6's whole-document word count must name), or ``split_outputs`` draws the copy
    over two pages (words that moved to another page: not missing)."""
    import pymupdf
    rendered = []

    def render_pdfs(files, out_dir, backend, workers=4, log=None, detail=None, cache=None):
        out = {}
        for f in files:
            f = Path(f)
            texts = [block_full_text(p) for p in paragraphs(DocxPackage(f.read_bytes()).xml("word/document.xml"))]
            is_output = any(part in f.parts for part in ("samples", "outputs", "output", "output-in"))
            dy = shift_outputs if is_output else 0.0
            doc = pymupdf.open()
            page = doc.new_page(width=612, height=792)
            for i, t in enumerate(texts[:40]):
                if t.strip():
                    line = t[:60]
                    if is_output:
                        for word in hide:              # blanked in place: the other words keep their positions
                            line = line.replace(word, " " * len(word))
                    row = i
                    if is_output and split_outputs and i >= 20:
                        if page.number == 0:
                            page = doc.new_page(width=612, height=792)
                        row = i - 20
                    page.insert_text((72, 72 + 14 * row + dy), line, fontsize=9, fontname="cour")
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


# ---- V6's warning: the words the original's pages show that the copy's do not (the stress-test finding F4) ----------

MISSING_SPRINKLER = "1 word(s) shown in the original's pages are not shown in the copy's: SPRINKLER"
# a plan that reflows the pages by design (no layout reference: V7 is N/A): where a copy that hides text was a PASS
# before this signal — a plan whose reference is the source already fails V7 on a word missing from an asserted page
FONT = SPECS / "change-all-font-arial.json"


def test_a_copy_whose_render_lacks_words_the_plan_did_not_remove_passes_with_the_words_named(folder, tmp_path, monkeypatch):
    # the title's SPRINKLER is drawn once in the source render and never in the copy's: V6 stays PASS, names it
    fake_renderer(monkeypatch, hide=("SPRINKLER",))
    pv = make_preview(load_spec(FONT), [str(folder)], tmp_path / "pv", renderer="fake", do_render=True)
    [sv] = pv.samples
    assert sv.passed is True and sv.checks["V6"] == "PASS" and sv.checks["V7"] == "NA"
    assert sv.render_warnings == [MISSING_SPRINKLER]
    assert [w for w in pv.warnings if "not shown in the copy's" in w] == [f"21 13 13 – WET-PIPE.docx: {MISSING_SPRINKLER}"]
    assert f"WARNING: 21 13 13 – WET-PIPE.docx: {MISSING_SPRINKLER}" in pv.to_text()
    assert failed_preview_checks(sv) == []                       # a warning, never a failed check
    assert sv.to_dict()["render_warnings"] == [MISSING_SPRINKLER]  # kept in preview.json for the chat and the visual check


def test_a_copy_whose_render_lacks_exactly_the_words_a_text_change_removed_gets_no_warning(folder, tmp_path, monkeypatch):
    fake_renderer(monkeypatch)
    spec = {"request": "drop SPRINKLER from the title", "summary": "WET-PIPE SPRINKLER SYSTEMS becomes WET-PIPE SYSTEMS.",
            "mode": "untracked", "ops": [{"op": "replace_text", "params": {"find": "WET-PIPE SPRINKLER SYSTEMS",
                                                                           "replace": "WET-PIPE SYSTEMS", "tracked": False}}]}
    pv = make_preview(spec, [str(folder)], tmp_path / "pv", renderer="fake", do_render=True)
    [sv] = pv.samples
    assert sv.passed is True and sv.checks["V6"] == "PASS"
    assert sv.plan["diffs"] and sv.render_warnings == []        # the diff's before words beyond its after words: SPRINKLER
    assert not [w for w in pv.warnings if "not shown in the copy's" in w]


def test_words_that_moved_to_another_page_are_not_missing(folder, tmp_path, monkeypatch):
    fake_renderer(monkeypatch, split_outputs=True)
    pv = make_preview(load_spec(FONT), [str(folder)], tmp_path / "pv", renderer="fake", do_render=True)
    [sv] = pv.samples
    assert (sv.pages_before, sv.pages_after) == (1, 2) and sv.checks["V6"] == "PASS"
    assert sv.render_warnings == [] and not [w for w in pv.warnings if "not shown in the copy's" in w]
    assert any("pages 1 → 2" in w for w in pv.warnings)         # the page count's own warning stands


def test_the_window_says_the_warning_in_the_apps_words_and_the_visual_check_is_told_the_words(folder, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from spec_surgeon import vision
    from spec_surgeon.document_task import DocumentTask
    from spec_surgeon.gui import Session
    fake_renderer(monkeypatch, hide=("SPRINKLER",))
    # Safe mode: no conversation, so the app says it in the chat itself, before the button that creates the copies
    task = DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=True, mode="safe")
    task.attach(sorted(folder.glob("*.docx")))
    task.perform("recipe", recipe="accept_changes", inputs={})
    said = [m["text"] for m in task.messages if m["role"] == "app"]
    assert f"21 13 13 – WET-PIPE.docx: {MISSING_SPRINKLER}." in said
    view = task._samples_view()
    assert f"21 13 13 – WET-PIPE.docx: {MISSING_SPRINKLER}" in view["warnings"]
    assert [s["passed"] for s in view["samples"]] == [True]            # a warning beside a sample that passed
    # Default mode: the visual check's brief names the words to look for, and the preview's note says them in the chat
    intents = []
    monkeypatch.setattr(vision, "review_samples",
                        lambda intent, *a, **k: intents.append(intent) or vision.VisualReview(summary="looked"))
    s = Session(documents=sorted(folder.glob("*.docx")), out_root=tmp_path / "out2", visual_check=True)
    s._set_outcome(spec=load_spec(FONT), source="test")
    s.make_preview(render=True, log=lambda m: None)
    [intent] = intents
    assert "WORDS THE ORIGINAL'S PAGES SHOW THAT THE COPY'S PAGES DO NOT" in intent
    assert f"21 13 13 – WET-PIPE.docx: {MISSING_SPRINKLER}" in intent
    notes = []
    s.conversation = SimpleNamespace(note=lambda text, display=None: notes.append((text, display)))
    s._note_preview()
    [(text, display)] = notes
    assert f"WORDS NOT SHOWN IN THE COPY'S PAGES (the render comparison's warning; say it to the user) — " \
           f"21 13 13 – WET-PIPE.docx: {MISSING_SPRINKLER}" in text
    assert display == f"Preview made: 1 of 1 file(s) plan cleanly. 21 13 13 – WET-PIPE.docx: {MISSING_SPRINKLER}."


def test_the_run_records_the_words_in_v6_and_the_reports_carry_the_line(folder, tmp_path, monkeypatch):
    fake_renderer(monkeypatch, hide=("SPRINKLER",))
    batch = run_batch([str(folder)], "edit-spec", {}, tmp_path / "run", workers=1, renderer="fake", spec=load_spec(FONT))
    [fr] = batch.files
    v6 = fr.record.get("V6")
    assert fr.record.passed and v6.status == "PASS"
    assert v6.data["words_missing_from_render"] == {"count": 1, "words": ["SPRINKLER"]}
    assert v6.summary.endswith("(XML checks are character truth); " + MISSING_SPRINKLER)
    assert "header_footer_words_not_compared" not in v6.data      # equal page counts: every word compared
    [record] = (tmp_path / "run" / "records").glob("*.record.md")
    assert MISSING_SPRINKLER in record.read_text(encoding="utf-8")
    report = (tmp_path / "run" / "BATCH_REPORT.md").read_text(encoding="utf-8")
    assert "## Rendered pages worth a look" in report and f"- **21 13 13 – WET-PIPE.docx**: {MISSING_SPRINKLER}." in report


def _page(doc, header=None, body=(), footer=None):
    page = doc.new_page(width=612, height=792)
    if header:
        page.insert_text((72, 30), header)             # wholly above the fixture's 72 pt top margin
    for i, line in enumerate(body):
        page.insert_text((72, 200 + 14 * i), line)
    if footer:
        page.insert_text((72, 770), footer)            # wholly below the 72 pt bottom margin
    return page


def _pdf(path: Path, pages):
    import pymupdf
    doc = pymupdf.open()
    for header, body, footer in pages:
        _page(doc, header, body, footer)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def source_docx(folder) -> Path:
    [f] = sorted(folder.glob("*.docx"))               # 1 in margins: the bands the rule reads
    return f


def test_header_and_footer_words_are_attributed_to_the_band_only_when_the_page_counts_differ(tmp_path, source_docx):
    from spec_surgeon.verify.geometry import compare_pdfs
    three = _pdf(tmp_path / "three.pdf", [("Spec header", ["alpha beta"], "Page footer"),
                                          ("Spec header", ["gamma delta"], "Page footer"),
                                          ("Spec header", ["epsilon"], "Page footer")])
    two = _pdf(tmp_path / "two.pdf", [("Spec header", ["alpha beta gamma"], "Page footer"),
                                      ("Spec header", ["delta epsilon"], "Page footer")])
    # a shorter document: every body word moved, its count stays; the per-page header and footer words are one short
    # each — attributed to the band, recorded, never a warning
    g = compare_pdfs(three, two, plan={"diffs": []}, source_docx=source_docx)
    assert g.words_missing.count == 0 and g.words_new.count == 0
    assert g.hf_words_not_compared.to_dict() == {"count": 4, "words": ["Spec", "header", "Page", "footer"]}
    data = g.to_dict()
    assert data["words_missing_from_render"] == {"count": 0, "words": []}
    assert data["header_footer_words_not_compared"]["count"] == 4
    # the same, and a body word gone: that one is named
    two_short = _pdf(tmp_path / "two-short.pdf", [("Spec header", ["alpha beta"], "Page footer"),
                                                  ("Spec header", ["delta epsilon"], "Page footer")])
    g = compare_pdfs(three, two_short, plan={"diffs": []}, source_docx=source_docx)
    assert g.words_missing.to_dict() == {"count": 1, "words": ["gamma"]}
    # equal page counts: a header word that vanished from one page is reported like any other
    lost_header = _pdf(tmp_path / "lost.pdf", [("Spec header", ["alpha beta"], "Page footer"),
                                               (None, ["gamma delta"], "Page footer"),
                                               ("Spec header", ["epsilon"], "Page footer")])
    g = compare_pdfs(three, lost_header, plan={"diffs": []}, source_docx=source_docx)
    assert g.words_missing.to_dict() == {"count": 2, "words": ["Spec", "header"]} and g.hf_words_not_compared.count == 0
    # without the source file there is no band: the rule grants nothing, the words are reported
    g = compare_pdfs(three, two, plan={"diffs": []})
    assert g.words_missing.count == 4 and g.hf_words_not_compared.count == 0
    # no plan: no whole-document count at all (the preview's look-pages comparison)
    g = compare_pdfs(three, two)
    assert g.words_missing is None and "words_missing_from_render" not in g.to_dict()


def test_a_template_plan_replaces_the_bands_and_the_plan_s_own_removals_are_subtracted(tmp_path, source_docx):
    from spec_surgeon.verify.geometry import compare_pdfs, plan_removed_words
    source = _pdf(tmp_path / "s.pdf", [("Old header", ["alpha beta"], None), ("Old header", ["gamma"], None)])
    templated = _pdf(tmp_path / "t.pdf", [("New header", ["alpha beta"], None), ("New header", ["gamma"], None)])
    # equal page counts, but the plan's layout reference is the template: the old header's words are the band's
    g = compare_pdfs(source, templated, plan={"diffs": [], "geometry_reference": "template"}, source_docx=source_docx)
    assert g.words_missing.count == 0 and g.hf_words_not_compared.to_dict() == {"count": 2, "words": ["Old", "Old"]}
    assert g.words_new.to_dict() == {"count": 2, "words": ["New", "New"]}
    # a wording change: the diff's before words beyond its after words are removed on purpose, by count
    replaced = _pdf(tmp_path / "r.pdf", [("Old header", ["beta"], None), ("Old header", ["gamma"], None)])
    plan = {"diffs": [{"part": "word/document.xml", "block_index": 0, "before": "alpha beta", "after": "beta"}]}
    g = compare_pdfs(source, replaced, plan=plan, source_docx=source_docx)
    assert g.words_missing.count == 0 and g.words_new.count == 0
    assert dict(plan_removed_words(plan)[0]) == {"alpha": 1}
    # the same output without the diff: alpha is missing
    assert compare_pdfs(source, replaced, plan={"diffs": []}, source_docx=source_docx).words_missing.words == ["alpha"]
    # a removed watermark was drawn on every page: its words go in any count
    stamped = _pdf(tmp_path / "w.pdf", [(None, ["DRAFT", "alpha beta"], None), (None, ["DRAFT", "gamma"], None)])
    clean = _pdf(tmp_path / "c.pdf", [(None, ["alpha beta"], None), (None, ["gamma"], None)])
    watermark = {"diffs": [], "property_assertions": [{"kind": "removed_watermark", "text": "DRAFT", "expected": {}}]}
    assert compare_pdfs(stamped, clean, plan=watermark).words_missing.count == 0
    assert compare_pdfs(stamped, clean, plan={"diffs": []}).words_missing.to_dict() == {"count": 2, "words": ["DRAFT", "DRAFT"]}
    # the record lists the first 50 of a long count, in the render's order; the count is complete
    from spec_surgeon.verify.geometry import WORDS_LISTED
    many = _pdf(tmp_path / "many.pdf", [(None, [" ".join(f"w{i}" for i in range(k * 10, k * 10 + 10)) for k in range(6)], None)])
    blank = _pdf(tmp_path / "blank.pdf", [(None, [], None)])
    g = compare_pdfs(many, blank, plan={"diffs": []})
    assert g.words_missing.count == 60 and g.words_missing.words == [f"w{i}" for i in range(WORDS_LISTED)]


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
def test_with_a_real_renderer_smaller_margins_reflow_the_words_without_a_warning(folder, tmp_path):
    spec = {"request": "half-inch margins", "summary": "Every section's margins become half an inch.", "mode": "formatting",
            "ops": [{"op": "set_page_setup", "select": {"sections": "all"},
                     "params": {"margins": {"top": 720, "bottom": 720, "left": 720, "right": 720}}}]}
    pv = make_preview(spec, [str(folder)], tmp_path / "pv", renderer="libreoffice", do_render=True)
    [sv] = pv.samples
    assert sv.passed is True and sv.checks["V6"] == "PASS", pv.to_text()
    assert sv.render_warnings == [] and not [w for w in pv.warnings if "not shown in the copy's" in w], pv.warnings


# ---- a custom method's samples and copies: V6 and V7 by the plan it predicted (the deep-dive stress test's D3) ------

def _method(layout_by_design: bool = False, unguarded: bool = False):
    """The scripted custom method of tests/test_codegen.py (it sets every section's header distance), declaring the
    source render as its layout reference — its own plan says no word moves — or, as written there, no reference by
    design."""
    from spec_surgeon import codegen
    from test_codegen import GOOD_OP
    source = GOOD_OP if layout_by_design else GOOD_OP.replace(
        '        frag.geometry_reference = "none"\n        frag.geometry_reason = "the header moves by design"\n', "")
    assert layout_by_design or 'geometry_reference = "none"' not in source
    return codegen.GeneratedOp(source=source, name="set_header_distance", passed=True, params={"twips": 900},
                               unguarded=unguarded)


def _proved(g, folder: Path, tmp_path: Path):
    from spec_surgeon import codegen
    [f] = sorted(folder.glob("*.docx"))
    g.trials = [codegen.trial(g.source, f, g.select, g.params, tmp_path / "trial", **codegen.trial_options(g))]
    assert g.trials[0].passed and g.trials[0].plan is not None
    return g


def test_a_custom_methods_sample_is_layout_checked_by_the_plan_it_predicted(folder, tmp_path, monkeypatch):
    from spec_surgeon import codegen
    from spec_surgeon.preview import layout_failures
    fake_renderer(monkeypatch)
    g = _proved(_method(), folder, tmp_path)
    gp = preview_generated(g, tmp_path / "pv", renderer="fake", do_render=True)
    [sv] = gp.samples
    assert sv.plan is g.trials[0].plan and sv.checks["V6"] == "PASS" and sv.checks["V7"] == "PASS"
    assert sv.geometry["status"] == "PASS" and "at" not in sv.geometry and sv.passed is True
    assert "page geometry (V7) PASS" in gp.to_text()
    assert layout_failures(gp.samples) == [] and codegen.samples_verdict(g, []) == [codegen.SAMPLES_PASSED]


def test_a_custom_sample_whose_words_moved_fails_and_is_never_called_passed(folder, tmp_path, monkeypatch):
    from spec_surgeon import codegen
    from spec_surgeon.preview import layout_failures
    fake_renderer(monkeypatch, shift_outputs=6.0)
    g = _proved(_method(), folder, tmp_path)
    gp = preview_generated(g, tmp_path / "pv", renderer="fake", do_render=True)
    [sv] = gp.samples
    assert sv.checks["V7"] == "FAIL" and sv.passed is False and "MOVED" in sv.geometry["note"]
    [line] = layout_failures(gp.samples)
    assert line.startswith("21 13 13 – WET-PIPE.docx: Page layout differs from what the plan predicts")
    verdict = codegen.samples_verdict(g, [line])
    assert verdict == [codegen.SAMPLES_LAYOUT, line] and codegen.SAMPLES_PASSED not in verdict
    assert codegen.samples_verdict(_method(unguarded=True), [line])[0] == codegen.SAMPLES_LAYOUT_UNVERIFIED


def test_a_custom_sample_with_no_layout_reference_by_design_says_why(folder, tmp_path, monkeypatch):
    fake_renderer(monkeypatch, shift_outputs=6.0)
    gp = preview_generated(_proved(_method(layout_by_design=True), folder, tmp_path), tmp_path / "pv",
                           renderer="fake", do_render=True)
    [sv] = gp.samples
    assert sv.checks["V7"] == "NA" and sv.passed is True and "the header moves by design" in sv.geometry["note"]


def test_a_custom_methods_run_renders_and_compares_every_copy_before_delivering_it(folder, tmp_path, monkeypatch):
    from spec_surgeon import codegen
    rendered = fake_renderer(monkeypatch)
    [t] = codegen.run_generated(_method(), [str(folder)], tmp_path / "run", renderer="fake", do_render=True)
    assert t.passed and t.output == tmp_path / "run" / "outputs" / "21 13 13 – WET-PIPE.docx" and t.output.is_file()
    assert t.record.get("V6").status == "PASS" and t.record.get("V7").status == "PASS"
    assert any("output-in" in Path(r).parts for r in rendered)       # the copy, under its own name, not out.docx
    record = (tmp_path / "run" / "records" / "21 13 13 – WET-PIPE.record.md").read_text(encoding="utf-8")
    assert "no renderer run yet" not in record


def test_a_custom_copy_whose_words_moved_is_held_like_any_failed_check(folder, tmp_path, monkeypatch):
    from spec_surgeon import codegen
    fake_renderer(monkeypatch, shift_outputs=6.0)
    [t] = codegen.run_generated(_method(), [str(folder)], tmp_path / "run", renderer="fake", do_render=True)
    assert not t.passed and t.output is None and t.record.get("V7").status == "FAIL"
    assert any(p.startswith("V7 FAIL: ") and "MOVED" in p for p in t.problems)
    assert not list((tmp_path / "run" / "outputs").glob("*.docx"))
    record = json.loads((tmp_path / "run" / "records" / "21 13 13 – WET-PIPE.record.json").read_text(encoding="utf-8"))
    assert {c["id"]: c["status"] for c in record["checks"]}["V7"] == "FAIL"


def test_in_obliterate_a_custom_copy_whose_words_moved_is_delivered_unverified(folder, tmp_path, monkeypatch):
    from spec_surgeon import codegen
    fake_renderer(monkeypatch, shift_outputs=6.0)
    [t] = codegen.run_generated(_method(unguarded=True), [str(folder)], tmp_path / "run", renderer="fake",
                                do_render=True)
    assert t.passed and t.output.is_file() and t.unverified == ["V7 (Geometry assertion)"]


def test_a_custom_run_without_rendering_says_why_its_pages_were_not_checked(folder, tmp_path):
    from spec_surgeon import codegen
    [t] = codegen.run_generated(_method(), [str(folder)], tmp_path / "run")
    assert t.passed and t.output.is_file()
    assert t.record.get("V6").status == t.record.get("V7").status == "NA"
    assert t.record.get("V7").summary == "rendering disabled"


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
def test_with_a_real_renderer_a_custom_samples_layout_verdict_is_its_runs(folder, tmp_path):
    from spec_surgeon import codegen
    g = _proved(_method(layout_by_design=True), folder, tmp_path)
    gp = preview_generated(g, tmp_path / "pv", renderer="libreoffice", do_render=True)
    [sv] = gp.samples
    [t] = codegen.run_generated(g, [str(folder)], tmp_path / "run", renderer="libreoffice", do_render=True)
    assert sv.checks["V6"] == t.record.get("V6").status == "PASS", t.record.get("V6").summary
    assert sv.checks["V7"] == t.record.get("V7").status == "NA" and "by design" in t.record.get("V7").summary


def test_in_default_a_custom_sample_whose_words_moved_makes_create_unavailable_and_the_chat_says_why(
        folder, tmp_path, monkeypatch):
    from fake_claude import FakeClient, resp, tool
    from spec_surgeon import codegen
    from spec_surgeon.document_task import DocumentTask
    from spec_surgeon.gui import Session
    fake_renderer(monkeypatch, shift_outputs=6.0)
    request = "Set the header distance to 900 twips."
    fake = FakeClient(resp(tool("submit_editspec", {"spec": {"request": request, "summary": "Move header",
        "unsupported": "Use a custom method", "proposal": "Set the header distance to 900 twips"},
        "custom_transform": "possible", "custom_transform_reason": "Formatting only"})),
        resp(tool("submit_op", {"source": _method().source, "params": {"twips": 900}})),
        resp(tool("ask_user", {"message": "Anything else?"}, "t3")))
    task = DocumentTask(Session(client=fake, out_root=tmp_path / "out", visual_check=False), render=True)
    task.attach(sorted(folder.glob("*.docx")))
    view = task.perform("send", text=request)
    assert view["stage"] == "preview" and task.session.generated.passed      # its trial passed: no pages there
    [sample] = task._samples_view()["samples"]
    assert sample["passed"] is False and sample["geometry"]["status"] == "FAIL"
    assert [f["check"] for f in sample["failures"]] == ["V7"]
    said = [m["text"] for m in task.messages if m["role"] == "app"]
    [verdict] = [t for t in said if t.startswith(codegen.SAMPLES_LAYOUT)]
    assert "• 21 13 13 – WET-PIPE.docx: Page layout differs from what the plan predicts" in verdict
    assert not any(codegen.SAMPLES_PASSED in t for t in said)
    assert codegen.SAMPLES_LAYOUT in task._groups()[0]["changes"][0]["details"]
    assert any(codegen.SAMPLES_LAYOUT in n for n in task.session.conversation.notes)   # the model hears it too
    assert "did not pass its preview checks (V7)" in task.session.run_blocked_reason()
    assert not task.results
