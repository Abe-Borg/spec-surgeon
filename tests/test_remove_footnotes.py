"""remove_footnotes and V10's ``removed_footnotes`` kind (built-in batch 6, F2 — docs/BUILTIN_OPS_BATCH6_DESIGN.md §2):
footnotes, endnotes or both, by text; end to end untracked and tracked; a wrong OUTPUT per rule; wrong IMPLEMENTATIONS
shared by plan and apply; refusals with their codes; parameters; touches; the batch pipeline and a LibreOffice render."""
from __future__ import annotations

import io
import shutil
import zipfile

import pytest
from lxml import etree

import fx_deletion as fx
from fx_deletion import by_text
from make_fixtures import _textbox_run, docx_bytes
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import deletion
from spec_surgeon.ops import remove_footnotes as rf
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.verify import run_suite

MAIN = "word/document.xml"
AUTHOR = "Spec Team"
OP = "remove_footnotes"


@pytest.fixture(scope="module")
def notes() -> bytes:
    return fx.build_notes()


def run_ops(data, specs):
    pkg = DocxPackage(data)
    frags = [get_op(o).plan(pkg, sel, par) for o, sel, par in specs]
    plan = merge_fragments(frags)
    work = pkg.copy()
    for f in frags:
        get_op(f.op).apply(work, f)
    out = work.output_bytes()
    return plan, out, run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")


def remove(data, tracked, select=None, **params):
    plan, out, rec = run_ops(data, [(OP, select or {}, {"tracked": tracked, "author": AUTHOR, **params})])
    assert rec.passed, rec.to_markdown()
    return plan, out


def refused(data, tracked=False, **params) -> JobException:
    with pytest.raises(JobException) as e:
        get_op(OP).plan(DocxPackage(data), {}, {"tracked": tracked, **params})
    return e.value


def failed(rec):
    return [c.id for c in rec.checks if c.status == "FAIL"]


def v10(rec) -> str:
    c = next(c for c in rec.checks if c.id == "V10")
    return c.summary + " | " + " | ".join(c.data.get("errors", []))


def mutate(docx, part, fn):
    zin = zipfile.ZipFile(io.BytesIO(docx))
    root = etree.fromstring(zin.read(part))
    fn(root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            z.writestr(info, serialize_xml(root) if info.filename == part else zin.read(info.filename))
    return buf.getvalue()


def caught(src, plan, out, part, fn, needle=None):
    rec = run_suite(src, mutate(out, part, fn), plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec), rec.to_markdown()
    if needle:
        assert needle in v10(rec), v10(rec)


def ids(data, part):
    return [e.get(W + "id") for e in DocxPackage(data).xml(part)]


def names(data):
    return zipfile.ZipFile(io.BytesIO(data)).namelist()


TRACKED = pytest.mark.parametrize("tracked", [False, True], ids=["untracked", "tracked"])


# ---- end to end ---------------------------------------------------------------------------------------------------

def test_untracked_removes_every_note_its_reference_and_what_only_it_used(notes):
    plan, out = remove(notes, False)
    assert ids(out, fx.FOOTNOTES) == ["-1", "0"] and ids(out, fx.ENDNOTES) == ["-1", "0"]
    root = DocxPackage(out).xml(MAIN)
    assert not list(root.iter(W + "footnoteReference")) and not list(root.iter(W + "endnoteReference"))
    assert by_text(root, "A. First").findall(W + "r")[-1].find(W + "t") is not None    # the reference's run went
    assert plan.members_removed == [fx.FOOTNOTE_IMAGE] and fx.FOOTNOTE_IMAGE not in names(out)
    assert list(DocxPackage(out).xml("word/_rels/footnotes.xml.rels")) == []
    assert {d.struct_after.get("footnoteReference", 0) for d in plan.diffs if d.part == MAIN} == {0}


def test_tracked_deletes_the_reference_and_the_notes_content(notes):
    plan, out = remove(notes, True)
    root = DocxPackage(out).xml(MAIN)
    assert all(ref.getparent().getparent().tag == W + "del" for ref in root.iter(W + "footnoteReference"))
    fn = DocxPackage(out).xml(fx.FOOTNOTES)
    note1 = next(e for e in fn if e.get(W + "id") == "1")
    assert note1.find(f"{W}p/{W}pPr/{W}rPr/{W}del") is not None and note1.find(f"{W}p/{W}del/{W}r/{W}delText") is not None
    assert ids(out, fx.FOOTNOTES) == ids(notes, fx.FOOTNOTES) and plan.members_removed == []
    # 4 references (one w:del each) + note runs: fn1 2 groups (a bookmark between), fn2 2 (one inside the link),
    # each endnote 1 group of its two runs; one mark per note paragraph
    assert plan.rev_delta == {"ins": 0, "del": 4 + 2 + 3 + 1 + 1, "del_mark": 4}


@TRACKED
def test_a_pattern_chooses_by_the_notes_own_text(notes, tracked):
    plan, out = remove(notes, tracked, text_regex="NFPA 13")
    expected = ["-1", "0", "2"] if not tracked else ["-1", "0", "1", "2"]
    assert ids(out, fx.FOOTNOTES) == expected and ids(out, fx.ENDNOTES) == ids(notes, fx.ENDNOTES)
    assert plan.property_assertions[0]["expected"] == {"footnotes": ["1"], "endnotes": []}


@TRACKED
def test_endnotes_alone(notes, tracked):
    plan, out = remove(notes, tracked, which="endnotes")
    assert ids(out, fx.FOOTNOTES) == ids(notes, fx.FOOTNOTES)
    assert ids(out, fx.ENDNOTES) == (["-1", "0"] if not tracked else ids(notes, fx.ENDNOTES))


def test_a_bookmark_inside_a_note_goes_with_it(notes):
    _, out = remove(notes, False, which="footnotes", text_regex="NFPA")
    assert "InNote" not in [b.get(W + "name") for b in DocxPackage(out).xml(fx.FOOTNOTES).iter(W + "bookmarkStart")]


def test_a_file_without_notes_is_left_alone_unless_asked(tmp_path):
    from docx import Document
    doc = Document()
    doc.add_paragraph("No notes here.")
    data = docx_bytes(doc)
    plan, out = remove(data, True)
    assert plan.intended_parts == []
    assert refused(data, allow_missing=False).code == "not-found"


# ---- wrong outputs ------------------------------------------------------------------------------------------------

def test_wrong_untracked_outputs_are_caught(notes):
    plan, out = remove(notes, False)
    caught(notes, plan, out, fx.FOOTNOTES, lambda root: root.append(etree.fromstring(
        f'<w:footnote xmlns:w="{fx.W_NS}" w:id="9"><w:p/></w:footnote>')), "is not the source without exactly")

    def keep_reference(root):
        p = by_text(root, "A. First")
        r = etree.SubElement(p, W + "r")
        etree.SubElement(r, W + "footnoteReference").set(W + "id", "1")
    caught(notes, plan, out, MAIN, keep_reference)


def test_wrong_tracked_outputs_are_caught(notes):
    plan, out = remove(notes, True)

    def undelete_reference(root):
        d = next(root.iter(W + "footnoteReference")).getparent().getparent()
        d.addprevious(d[0])
        d.getparent().remove(d)
    caught(notes, plan, out, MAIN, undelete_reference, "not exactly the tracked deletion")

    def keep_note_mark(root):
        note1 = next(e for e in root if e.get(W + "id") == "1")
        rpr = note1.find(f"{W}p/{W}pPr/{W}rPr")
        rpr.remove(rpr.find(W + "del"))
    caught(notes, plan, out, fx.FOOTNOTES, keep_note_mark, "carries 0 new w:del")


# ---- wrong implementations shared by plan and apply ----------------------------------------------------------------

def _shared(src, monkeypatch, tracked, patches, needle=None, **params):
    for target, name, value in patches:
        monkeypatch.setattr(target, name, value)
    plan, out, rec = run_ops(src, [(OP, {}, {"tracked": tracked, "author": AUTHOR, **params})])
    assert "V10" in failed(rec), rec.to_markdown()
    if needle:
        assert needle in v10(rec), v10(rec)


def test_a_shared_note_text_that_misses_the_link_text_is_caught(notes, monkeypatch):
    plain = lambda entry: "\n".join("".join(t.text or "" for p_ in [p] for t in p_.findall(f"{W}r/{W}t"))
                                    for p in entry.iter(W + "p"))
    _shared(notes, monkeypatch, False, [(rf, "note_text", plain)], "notes", which="footnotes", text_regex="nfpa\\.org")


def test_a_shared_walk_that_skips_a_notes_link_runs_is_caught(notes, monkeypatch):
    real = deletion.tracked_runs
    skip = lambda p, where: [r for r in real(p, where) if r.getparent().tag != W + "hyperlink"]
    _shared(notes, monkeypatch, True, [(rf, "tracked_runs", skip)], "not exactly the tracked deletion")


def test_a_shared_removal_that_keeps_the_notes_media_is_caught(notes, monkeypatch):
    real = deletion.Removal._relationships

    def keep_media(self):
        real(self)
        self.media, self.members, self.rels = [], [], {}
    _shared(notes, monkeypatch, False, [(deletion.Removal, "_relationships", keep_media)], "the removal takes")


# ---- refusals -----------------------------------------------------------------------------------------------------

def test_a_custom_mark_is_refused_and_a_pattern_can_leave_it():
    data = fx.build_notes(custom=True)
    for tracked in (False, True):
        assert refused(data, tracked).code == "custom-mark"
    remove(data, False, which="footnotes", text_regex="NFPA")


def test_a_note_referenced_twice_is_refused():
    assert refused(fx.build_notes(twice=True), which="footnotes").code == "structure"


def test_a_reference_in_a_text_box_is_refused(notes):
    from docx import Document

    def into_box(root):                            # footnote 1's reference moved into the text box's first copy
        ref_run = next(root.iter(W + "footnoteReference")).getparent()
        next(p for p in root.iter(W + "p") if p.getparent().tag == W + "txbxContent").append(ref_run)
    doc = Document(io.BytesIO(notes))
    _textbox_run(doc.paragraphs[-1], "Boxed")
    data = mutate(docx_bytes(doc), MAIN, into_box)
    assert refused(data, which="footnotes").code == "textbox"


def test_a_tracked_removal_refuses_a_shared_run_and_pending_revisions(notes):
    assert refused(fx.build_notes(shared_run=True), True, which="footnotes", text_regex="NFPA").code == "structure"
    remove(fx.build_notes(shared_run=True), False, which="footnotes", text_regex="NFPA")    # untracked: the mark alone

    def pending(root):
        note1 = next(e for e in root if e.get(W + "id") == "1")
        r = note1.find(f"{W}p/{W}r")
        ins = etree.Element(W + "ins", {W + "id": "500", W + "author": "Jack", W + "date": "2026-09-01T00:00:00Z"})
        r.addprevious(ins)
        ins.append(r)
    data = mutate(notes, fx.FOOTNOTES, pending)
    assert refused(data, True, which="footnotes", text_regex="NFPA").code == "existing-revisions"


@pytest.mark.parametrize("select,params", [
    ({"paragraphs": "all"}, {}), ({"scope": ["footnotes"]}, {}), ({}, {"which": "notes"}), ({}, {"text_regex": "("}),
    ({}, {"tracked": "no"}), ({}, {"allow_missing": "yes"}), ({}, {"date_convention": "local"}),
])
def test_parameters_are_checked(notes, select, params):
    with pytest.raises(JobException) as e:
        get_op(OP).plan(DocxPackage(notes), select, params)
    assert e.value.code == "bad-params"


# ---- touches, catalog, pipeline, render ------------------------------------------------------------------------------

def test_touches(notes):
    pkg = DocxPackage(notes)
    untracked = get_op(OP).plan(pkg, {}, {"tracked": False})
    assert (MAIN, "*", "text") in untracked.touches and (fx.FOOTNOTES, "*", "text") in untracked.touches
    tracked = get_op(OP).plan(pkg, {}, {"tracked": True})
    assert (MAIN, "*", "text") not in tracked.touches and (fx.FOOTNOTES, "scope", "verify") in tracked.touches
    font = get_op("set_font").plan(pkg, {"paragraphs": {"text_regex": "^E\\. Plain"}}, {"font": "Arial"})
    merge_fragments([tracked, font])


def test_a_spec_runs_through_the_batch_pipeline(notes, tmp_path):
    from spec_surgeon.batch import run_batch
    path = tmp_path / "21 13 13 – NOTES.docx"
    path.write_bytes(notes)
    for k, tracked in enumerate((False, True)):
        spec = {"request": "r", "summary": "s", "mode": "content" if tracked else "untracked",
                "ops": [{"op": OP, "params": {"tracked": tracked}}]}
        batch = run_batch([str(path)], "edit-spec", {}, tmp_path / f"out{k}", workers=1, renderer="none",
                          do_render=False, spec=spec)
        f = batch.files[0]
        assert f.status == "PASS", f.record.to_markdown() if f.record else f.exception


@pytest.mark.render
@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
def test_libreoffice_opens_and_renders_the_output(notes, tmp_path):
    from spec_surgeon.render import libreoffice
    outs = []
    for k, tracked in enumerate((False, True)):
        _, out = remove(notes, tracked)
        path = tmp_path / f"out{k}.docx"
        path.write_bytes(out)
        outs.append(path)
    pdfs = libreoffice.render_pdfs(outs, tmp_path / "pdf", workers=1)
    if len(pdfs) != len(outs):
        pytest.skip("LibreOffice did not render here")
    assert all(pdfs[p].exists() and pdfs[p].read_bytes().startswith(b"%PDF") for p in outs)
