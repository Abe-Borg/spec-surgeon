"""End-to-end tests for the engine. No renderer needed (V6/V7 are N/A here); the batch test runs
the renderer only if one is available."""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

import pytest
from lxml import etree

from spec_surgeon import guard
from spec_surgeon.batch import run_batch
from spec_surgeon.jobs import get_job, JobException
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.verify import run_suite

from make_fixtures import build

AUTHOR = "Abraham Borg"


@pytest.fixture(scope="module")
def fx(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("fx")
    (d / "21 05 00 – COMMON WORK RESULTS.docx").write_bytes(build("21 05 00", "COMMON WORK RESULTS FOR FIRE SUPPRESSION"))
    (d / "NO COVER PAGE FIELD.docx").write_bytes(build("21 99 00", "VARIANT", with_cover_page_field=False))
    (d / "SINGLE SECTION.docx").write_bytes(build("21 98 00", "VARIANT", single_section=True))
    return d


def _run(path: Path, job: str, params: dict):
    pkg = DocxPackage.from_path(path)
    j = get_job(job)
    plan = j.plan(pkg, params)
    subs = j.apply(pkg, plan)
    out = pkg.repack(subs) if subs else pkg.data
    rec = run_suite(pkg.data, out, plan.to_dict(), str(path), "out.docx")
    return plan, out, rec


def _failed(rec):
    return [c.id for c in rec.checks if c.status == "FAIL"]


# ---- page numbering -----------------------------------------------------------------------------

def test_page_numbering_cover_unnumbered(fx):
    plan, out, rec = _run(fx / "21 05 00 – COMMON WORK RESULTS.docx", "page-numbering", {"option": "cover-unnumbered"})
    assert rec.passed, rec.to_markdown()
    assert plan.intended_parts == ["word/footer1.xml"]
    assert len(plan.diffs) == 1 and plan.diffs[0].after == ""
    # the cover footer paragraph is empty now; the body footer is untouched
    pkg = DocxPackage(out)
    cover = pkg.sections()[0].footer_refs["default"]
    assert b"PAGE" not in pkg.read(cover)
    body = pkg.sections()[1].footer_refs["default"]
    assert b"PAGE" in pkg.read(body)


def test_page_numbering_continuous(fx):
    plan, out, rec = _run(fx / "21 05 00 – COMMON WORK RESULTS.docx", "page-numbering", {"option": "continuous"})
    assert rec.passed, rec.to_markdown()
    assert plan.diffs == []
    assert rec.get("V2").summary.startswith("blocks compared")
    assert DocxPackage(out).sections()[1].pg_num_start is None


def test_page_numbering_exceptions(fx):
    job = get_job("page-numbering")
    with pytest.raises(JobException) as e:
        job.plan(DocxPackage.from_path(fx / "NO COVER PAGE FIELD.docx"), {"option": "cover-unnumbered"})
    assert e.value.code == "not-found"
    with pytest.raises(JobException) as e:
        job.plan(DocxPackage.from_path(fx / "SINGLE SECTION.docx"), {"option": "continuous"})
    assert e.value.code == "structure"


# ---- tracked replace ----------------------------------------------------------------------------

def test_tracked_replace_all_cases(fx):
    params = {"find": "FM Global", "replace": "the Owner's risk consultant – GRC (Global Risk Consultants)",
              "scope": ["document", "headers", "footers"], "author": AUTHOR}
    plan, out, rec = _run(fx / "21 05 00 – COMMON WORK RESULTS.docx", "tracked-replace", params)
    assert rec.passed, rec.to_markdown()
    assert plan.rev_delta == {"ins": 5, "del": 4}          # plain, merged-run, nested(+suffix), after-field
    assert len(plan.diffs) == 4
    root = DocxPackage(out).xml("word/document.xml")
    # nested grammar: Jeremy's insertion now contains our deletion; our insertion follows; Jeremy's suffix re-wrapped
    jeremy = [i for i in root.iter(W + "ins") if i.get(W + "author") == "Jeremy"]
    assert len(jeremy) == 2
    nested_del = jeremy[0].find(W + "del")
    assert nested_del is not None and nested_del.get(W + "author") == AUTHOR
    assert nested_del.find(W + "r/" + W + "delText").text == "FM Global"
    ours = [i for i in root.iter(W + "ins") if i.get(W + "author") == AUTHOR]
    assert len(ours) == 4
    # date convention copied from the file (w16du:dateUtc present on Jeremy's revision)
    assert any(k.endswith("}dateUtc") for k in ours[0].attrib)
    # pandoc as independent oracle when available
    if shutil.which("pandoc"):
        p = Path(str(fx / "tr_out.docx")); p.write_bytes(out)
        acc = subprocess.run(["pandoc", str(p), "--track-changes=accept", "-t", "plain"], capture_output=True, text=True).stdout
        rej = subprocess.run(["pandoc", str(p), "--track-changes=reject", "-t", "plain"], capture_output=True, text=True).stdout
        assert "FM Global" not in acc.replace("\n", " ")
        assert "Global Risk Consultants" in acc.replace("\n", " ")
        assert "Global Risk Consultants" not in rej
        src_rej = subprocess.run(["pandoc", str(fx / "21 05 00 – COMMON WORK RESULTS.docx"), "--track-changes=reject", "-t", "plain"],
                                 capture_output=True, text=True).stdout
        assert rej == src_rej


def test_tracked_pure_deletion(fx):
    params = {"find": " and all equipment shall be FM Approved", "replace": "", "author": AUTHOR}
    plan, out, rec = _run(fx / "21 05 00 – COMMON WORK RESULTS.docx", "tracked-replace", params)
    assert rec.passed, rec.to_markdown()
    assert plan.rev_delta == {"ins": 0, "del": 1}


def test_untracked_replace_in_header(fx):
    params = {"find": "ISSUED FOR CONSTRUCTION", "replace": "ISSUED FOR PERMIT", "scope": ["headers"], "mode": "untracked"}
    plan, out, rec = _run(fx / "21 05 00 – COMMON WORK RESULTS.docx", "tracked-replace", params)
    assert rec.passed, rec.to_markdown()
    assert plan.mode == "untracked" and plan.rev_delta == {"ins": 0, "del": 0}
    assert rec.get("V5b").summary.startswith("no revision added")
    assert b"ISSUED FOR PERMIT" in DocxPackage(out).read(plan.intended_parts[0])


def test_tracked_replace_exceptions(fx):
    job = get_job("tracked-replace")
    pkg = DocxPackage.from_path(fx / "21 05 00 – COMMON WORK RESULTS.docx")
    with pytest.raises(JobException) as e:
        job.plan(pkg, {"find": "not in this document", "replace": "x"})
    assert e.value.code == "not-found"
    plan = job.plan(pkg, {"find": "not in this document", "replace": "x", "allow_missing": True})
    assert plan.diffs == [] and job.apply(pkg, plan) == {}
    with pytest.raises(JobException) as e:              # field instructions are not live text at all
        job.plan(pkg, {"find": "_Ref123", "replace": "_Ref999"})
    assert e.value.code == "not-found"
    with pytest.raises(JobException) as e:
        job.plan(pkg, {"find": "21 10 00", "replace": "21 10 01"})   # cached REF result
    assert e.value.code == "field-result"


# ---- the verifier must catch planted defects -------------------------------------------------------

def _mutate_document_xml(docx: bytes, fn) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(docx))
    root = etree.fromstring(zin.read("word/document.xml"))
    fn(root)
    new = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            z.writestr(info, new if info.filename == "word/document.xml" else zin.read(info.filename))
    return buf.getvalue()


def test_verifier_catches_planted_defects(fx):
    src_path = fx / "21 05 00 – COMMON WORK RESULTS.docx"
    params = {"find": "FM Global", "replace": "GRC", "author": AUTHOR}
    plan, good, rec = _run(src_path, "tracked-replace", params)
    assert rec.passed
    src = src_path.read_bytes()
    XS = "{http://www.w3.org/XML/1998/namespace}space"

    def drop_preserve(root):   # silent single-character loss on open
        for t in root.iter(W + "t"):
            if t.text and t.text.endswith(" ") and t.get(XS):
                del t.attrib[XS]; return
    rec1 = run_suite(src, _mutate_document_xml(good, drop_preserve), plan.to_dict(), "s", "o")
    assert "V5c" in _failed(rec1)

    def drop_tab(root):        # a tab is content; w:t-only checks cannot see it go
        for tab in root.iter(W + "tab"):
            if tab.getparent().tag == W + "r":
                tab.getparent().remove(tab); return
    rec2 = run_suite(src, _mutate_document_xml(good, drop_tab), plan.to_dict(), "s", "o")
    assert "V3" in _failed(rec2)

    def stray_edit(root):      # an untracked character change in an untouched paragraph
        for t in root.iter(W + "t"):
            if t.text and "Article 5 body" in t.text:
                t.text = t.text.replace("Article 5", "Article 6"); return
    rec3 = run_suite(src, _mutate_document_xml(good, stray_edit), plan.to_dict(), "s", "o")
    assert "V2" in _failed(rec3) and "V5b" in _failed(rec3)

    def wrong_words(root):     # tracked, but not the approved wording
        for t in root.iter(W + "t"):
            if t.text == "GRC":
                t.text = "GRC Inc."; return
    rec4 = run_suite(src, _mutate_document_xml(good, wrong_words), plan.to_dict(), "s", "o")
    assert "V2" in _failed(rec4) and "V8" in _failed(rec4)

    def flat_del_instead_of_nested(root):   # the lesson: a flat sibling del resurrects text on reject
        for ins in root.iter(W + "ins"):
            if ins.get(W + "author") == "Jeremy":
                d = ins.find(W + "del")
                if d is not None:
                    ins.addnext(d); return
    rec5 = run_suite(src, _mutate_document_xml(good, flat_del_instead_of_nested), plan.to_dict(), "s", "o")
    assert "V5b" in _failed(rec5) or "V8" in _failed(rec5)

    # package-level: an extra member
    buf = io.BytesIO(good)
    with zipfile.ZipFile(buf, "a") as z:
        z.writestr("word/extra.xml", "<x/>")
    rec6 = run_suite(src, buf.getvalue(), plan.to_dict(), "s", "o")
    assert "V1" in _failed(rec6)


# ---- guard -----------------------------------------------------------------------------------------

def test_guard_lock_and_drift(tmp_path):
    f = tmp_path / "Section 21 05 00.docx"
    f.write_bytes(b"abc")
    ident = guard.acquire(f)
    assert guard.drifted(ident) is None
    lock = tmp_path / "~$ction 21 05 00.docx"      # Word's naming: first two chars replaced
    lock.write_bytes(b"")
    assert guard.word_lock_files(f) == [lock.name]
    with pytest.raises(guard.GuardError):
        guard.write_back(ident, b"xyz")
    lock.unlink()
    time.sleep(0.01)
    f.write_bytes(b"abcd")                             # user edited mid-session
    assert guard.drifted(ident)
    with pytest.raises(guard.GuardError):
        guard.write_back(ident, b"xyz")
    ident = guard.acquire(f)
    new = guard.write_back(ident, b"xyz")
    assert f.read_bytes() == b"xyz" and new.size == 3
    assert not list(tmp_path.glob(".specsurgeon-*"))


# ---- batch -----------------------------------------------------------------------------------------

def test_batch_many_files(tmp_path):
    n = 24
    for i in range(n):
        (tmp_path / f"21 {i:02d} 00 – SPEC {i}.docx").write_bytes(build(f"21 {i:02d} 00", f"SPEC {i}"))
    out = tmp_path / "out"
    t0 = time.perf_counter()
    batch = run_batch([str(tmp_path)], "page-numbering", {"option": "cover-unnumbered"}, out,
                      workers=4, renderer="none", do_render=False, in_place=False)
    dt = time.perf_counter() - t0
    c = batch.counts()
    assert c["PASS"] == n and c["FAIL"] == 0 and c["EXCEPTION"] == 0, (out / "BATCH_REPORT.md").read_text()
    assert (out / "BATCH_REPORT.md").exists() and len(list((out / "records").glob("*.record.md"))) == n
    print(f"\n{n} files transform+verify (no render): {dt:.2f}s = {dt / n * 1000:.0f} ms/file")


def test_batch_in_place_and_exceptions(tmp_path):
    good = tmp_path / "21 05 00 – A.docx"; good.write_bytes(build("21 05 00", "A"))
    bad = tmp_path / "SINGLE.docx"; bad.write_bytes(build("21 98 00", "B", single_section=True))
    before = good.read_bytes()
    batch = run_batch([str(good), str(bad)], "page-numbering", {"option": "continuous"}, tmp_path / "out",
                      workers=1, renderer="none", do_render=False, in_place=True)
    st = {f.source.name: f.status for f in batch.files}
    assert st == {"21 05 00 – A.docx": "PASS", "SINGLE.docx": "EXCEPTION"}
    assert good.read_bytes() != before                      # written back in place
    assert DocxPackage.from_path(good).sections()[1].pg_num_start is None
    assert (tmp_path / "out" / "exceptions" / "SINGLE.exception.json").exists()
    rec = [f for f in batch.files if f.source == good][0].record
    assert rec.get("V9").status == "PASS"
