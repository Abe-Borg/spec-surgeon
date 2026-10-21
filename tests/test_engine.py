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
import stat

import pytest
from lxml import etree

from spec_surgeon import guard, package as package_module
from spec_surgeon.batch import run_batch
from spec_surgeon.ops import JobException, get_op
from spec_surgeon.ops.base import assign_revision_ids
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.verify import run_suite

from make_fixtures import build, build_rich, patch_docx
from spec_surgeon.editspec import apply_spec, plan_spec
from spec_surgeon.ooxml import SECTPR_ORDER, insert_in_order
from spec_surgeon.ops.hf_common import effective_refs

AUTHOR = "Abraham Borg"


@pytest.fixture(scope="module")
def fx(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("fx")
    (d / "21 05 00 – COMMON WORK RESULTS.docx").write_bytes(build("21 05 00", "COMMON WORK RESULTS FOR FIRE SUPPRESSION"))
    (d / "NO COVER PAGE FIELD.docx").write_bytes(build("21 99 00", "VARIANT", with_cover_page_field=False))
    (d / "SINGLE SECTION.docx").write_bytes(build("21 98 00", "VARIANT", single_section=True))
    return d


def _plan(pkg: DocxPackage, op: str, params: dict):
    """One op's plan with its revision ids assigned, as a one-op spec merges it (``scope`` in params selects)."""
    frag = get_op(op).plan(pkg, {"scope": params["scope"]} if "scope" in params else {}, params)
    assign_revision_ids([frag])
    return frag


def _apply(pkg: DocxPackage, op: str, plan) -> dict:
    work = pkg.copy()
    get_op(op).apply(work, plan)
    return work.substitutions()


def _run(path: Path, op: str, params: dict):
    pkg = DocxPackage.from_path(path)
    plan = _plan(pkg, op, params)
    subs = _apply(pkg, op, plan)
    out = pkg.repack(subs) if subs else pkg.data
    rec = run_suite(pkg.data, out, plan.to_dict(), str(path), "out.docx")
    return plan, out, rec


def _spec(op: str, params: dict) -> dict:
    return {"request": "r", "summary": "s", "mode": get_op(op).mode, "ops": [{"op": op, "params": params}]}


def _failed(rec):
    return [c.id for c in rec.checks if c.status == "FAIL"]


# ---- page numbering -----------------------------------------------------------------------------

def test_page_numbering_cover_unnumbered(fx):
    plan, out, rec = _run(fx / "21 05 00 – COMMON WORK RESULTS.docx", "fix_page_numbering",
                          {"option": "cover-unnumbered"})
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
    plan, out, rec = _run(fx / "21 05 00 – COMMON WORK RESULTS.docx", "fix_page_numbering", {"option": "continuous"})
    assert rec.passed, rec.to_markdown()
    assert plan.diffs == []
    assert rec.get("V2").summary.startswith("blocks compared")
    assert DocxPackage(out).sections()[1].pg_num_start is None


def test_page_numbering_exceptions(fx):
    with pytest.raises(JobException) as e:
        _plan(DocxPackage.from_path(fx / "NO COVER PAGE FIELD.docx"), "fix_page_numbering",
              {"option": "cover-unnumbered"})
    assert e.value.code == "not-found"
    with pytest.raises(JobException) as e:
        _plan(DocxPackage.from_path(fx / "SINGLE SECTION.docx"), "fix_page_numbering", {"option": "continuous"})
    assert e.value.code == "structure"


# ---- tracked replace ----------------------------------------------------------------------------

def test_tracked_replace_all_cases(fx):
    params = {"find": "FM Global", "replace": "the Owner's risk consultant – GRC (Global Risk Consultants)",
              "scope": ["document", "headers", "footers"], "author": AUTHOR}
    plan, out, rec = _run(fx / "21 05 00 – COMMON WORK RESULTS.docx", "replace_text", params)
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
    plan, out, rec = _run(fx / "21 05 00 – COMMON WORK RESULTS.docx", "replace_text", params)
    assert rec.passed, rec.to_markdown()
    assert plan.rev_delta == {"ins": 0, "del": 1}


def test_untracked_replace_in_header(fx):
    params = {"find": "ISSUED FOR CONSTRUCTION", "replace": "ISSUED FOR PERMIT", "scope": ["headers"], "mode": "untracked"}
    plan, out, rec = _run(fx / "21 05 00 – COMMON WORK RESULTS.docx", "replace_text", params)
    assert rec.passed, rec.to_markdown()
    assert plan.mode == "untracked" and plan.rev_delta == {"ins": 0, "del": 0}
    assert rec.get("V5b").summary.startswith("no revision added")
    assert b"ISSUED FOR PERMIT" in DocxPackage(out).read(plan.intended_parts[0])


def test_tracked_replace_exceptions(fx):
    pkg = DocxPackage.from_path(fx / "21 05 00 – COMMON WORK RESULTS.docx")
    with pytest.raises(JobException) as e:
        _plan(pkg, "replace_text", {"find": "not in this document", "replace": "x"})
    assert e.value.code == "not-found"
    plan = _plan(pkg, "replace_text", {"find": "not in this document", "replace": "x", "allow_missing": True})
    assert plan.diffs == [] and _apply(pkg, "replace_text", plan) == {}
    with pytest.raises(JobException) as e:              # field instructions are not live text at all
        _plan(pkg, "replace_text", {"find": "_Ref123", "replace": "_Ref999"})
    assert e.value.code == "not-found"
    with pytest.raises(JobException) as e:
        _plan(pkg, "replace_text", {"find": "21 10 00", "replace": "21 10 01"})   # cached REF result
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
    plan, good, rec = _run(src_path, "replace_text", params)
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
    batch = run_batch([str(tmp_path)], "edit-spec", {}, out, workers=4, renderer="none", do_render=False,
                      in_place=False, spec=_spec("fix_page_numbering", {"option": "cover-unnumbered"}))
    dt = time.perf_counter() - t0
    c = batch.counts()
    assert c["PASS"] == n and c["FAIL"] == 0 and c["EXCEPTION"] == 0, (out / "BATCH_REPORT.md").read_text()
    assert (out / "BATCH_REPORT.md").exists() and len(list((out / "records").glob("*.record.md"))) == n
    print(f"\n{n} files transform+verify (no render): {dt:.2f}s = {dt / n * 1000:.0f} ms/file")


def test_batch_in_place_and_exceptions(tmp_path):
    good = tmp_path / "21 05 00 – A.docx"; good.write_bytes(build("21 05 00", "A"))
    bad = tmp_path / "SINGLE.docx"; bad.write_bytes(build("21 98 00", "B", single_section=True))
    before = good.read_bytes()
    batch = run_batch([str(good), str(bad)], "edit-spec", {}, tmp_path / "out", workers=1, renderer="none",
                      do_render=False, in_place=True, spec=_spec("fix_page_numbering", {"option": "continuous"}))
    st = {f.source.name: f.status for f in batch.files}
    assert st == {"21 05 00 – A.docx": "PASS", "SINGLE.docx": "EXCEPTION"}
    assert good.read_bytes() != before                      # written back in place
    assert DocxPackage.from_path(good).sections()[1].pg_num_start is None
    assert (tmp_path / "out" / "exceptions" / "SINGLE.exception.json").exists()
    rec = [f for f in batch.files if f.source == good][0].record
    assert rec.get("V9").status == "PASS"


# ---- cover-unnumbered when the title page is switched off ------------------------------------------------------------

MAIN = "word/document.xml"


def _spec_in_mode(*ops, mode="formatting"):
    return {"request": "r", "summary": "r", "assumptions": [], "questions": [], "applies_to": {"or": "all"},
            "mode": mode, "ops": list(ops)}


COVER_UNNUMBERED = {"op": "fix_page_numbering", "params": {"option": "cover-unnumbered"}}


def _cover_title_off(data: bytes) -> bytes:
    """The cover section's w:titlePg written as <w:titlePg w:val="0"/> — off (CT_OnOff), though present."""
    def edit(root):
        sp = DocxPackage.sect_prs(root)[0]
        for old in sp.findall(W + "titlePg"):
            sp.remove(old)
        tp = etree.Element(W + "titlePg")
        tp.set(W + "val", "0")
        insert_in_order(sp, tp, SECTPR_ORDER)
    return patch_docx(data, {MAIN: edit})


def test_cover_unnumbered_reads_a_title_page_switched_off_as_off():
    """build_rich's cover has a first-page footer (footer1) and a default one (footer2) its body shares. With
    <w:titlePg w:val="0"/> the cover shows footer2 — shared, so the op refuses — never footer1, which nobody sees."""
    pkg = DocxPackage(_cover_title_off(build_rich()))
    assert [s.title_pg for s in pkg.sections()][0] is False
    with pytest.raises(JobException) as e:
        plan_spec(pkg, _spec_in_mode(COVER_UNNUMBERED))
    assert e.value.code == "shared-part" and "word/footer2.xml" in e.value.reason


def test_cover_unnumbered_with_title_page_off_edits_the_footer_the_cover_shows_and_verifies():
    data = _cover_title_off(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    pkg = DocxPackage(data)
    plan, frags = plan_spec(pkg, _spec_in_mode(COVER_UNNUMBERED))
    assert {"kind": "cover_unnumbered", "footer": pkg.sections()[0].footer_refs["default"]} in [
        {k: a[k] for k in ("kind", "footer")} for a in plan.property_assertions if a["kind"] == "cover_unnumbered"]
    out = apply_spec(pkg, frags).output_bytes()
    assert run_suite(data, out, plan.to_dict(), "a.docx", "b.docx").passed


def test_v10_catches_a_plan_that_edits_a_footer_the_cover_does_not_show(monkeypatch):
    """Planted: the old reading (titlePg present = on) shared by plan and apply — the op edits the cover's first-page
    footer while the cover shows its default one. V10's own reading of what the cover shows fails it."""
    real = DocxPackage.sections

    def presence_only(self):
        out = real(self)
        for s, sp in zip(out, DocxPackage.sect_prs(self.xml(self.main_part()))):
            s.title_pg = sp.find(W + "titlePg") is not None
        return out

    data = _cover_title_off(build_rich())
    pkg = DocxPackage(data)
    monkeypatch.setattr(package_module.DocxPackage, "sections", presence_only)
    plan, frags = plan_spec(pkg, _spec_in_mode(COVER_UNNUMBERED))
    assert plan.intended_parts == ["word/footer1.xml"]                  # the footer nobody sees
    out = apply_spec(pkg, frags).output_bytes()
    monkeypatch.setattr(package_module.DocxPackage, "sections", real)
    rec = run_suite(data, out, plan.to_dict(), "a.docx", "b.docx")
    assert not rec.passed and "cover_unnumbered" in rec.get("V10").summary


# ---- the guard and read-only files -----------------------------------------------------------------------------------

def _spec_full(request, mode, ops, **extra):
    return {"request": request, "summary": request, "assumptions": [], "questions": [], "applies_to": {"or": "all"},
            "mode": mode, "ops": ops, **extra}


def test_guard_refuses_a_read_only_source_and_the_batch_carries_on(tmp_path):
    folder = tmp_path / "specs"
    folder.mkdir()
    a = folder / "A.docx"
    a.write_bytes(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    c = folder / "C.docx"
    c.write_bytes(build("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS"))
    c.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
    try:
        assert guard.is_read_only(c) and not guard.is_read_only(a)
        ident = guard.acquire(c)
        with pytest.raises(guard.GuardError, match="read-only"):
            guard.write_back(ident, b"anything")
        assert guard.acquire(c).md5 == ident.md5
        spec = _spec_full("replace", "untracked", [{"op": "replace_text", "tracked": False,
                                                "params": {"find": "FM Global", "replace": "FM"}}])
        batch = run_batch([str(folder)], "edit-spec", {}, tmp_path / "out", workers=1, renderer="none",
                          do_render=False, in_place=True, spec=spec)
        by_name = {f.source.name: f for f in batch.files}
        assert by_name["A.docx"].status == "PASS" and by_name["A.docx"].delivered_to == str(a)
        assert by_name["C.docx"].status == "FAIL"
        v9 = by_name["C.docx"].record.get("V9")
        assert v9.status == "FAIL" and "read-only" in v9.summary
        assert guard.acquire(c).md5 == ident.md5                      # untouched
        assert (tmp_path / "out" / "failed" / "C.docx").is_file() and not (tmp_path / "out" / "outputs" / "C.docx").exists()
        assert not list(folder.glob(".specsurgeon-*"))                 # no temp file left behind
    finally:
        c.chmod(stat.S_IREAD | stat.S_IWRITE)


def test_a_replace_the_file_system_refuses_is_a_guard_error_not_a_crash(tmp_path, monkeypatch):
    f = tmp_path / "A.docx"
    f.write_bytes(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    ident = guard.acquire(f)

    def refuse(src, dst):
        raise PermissionError(5, "Access is denied")
    monkeypatch.setattr(guard.os, "replace", refuse)
    with pytest.raises(guard.GuardError, match="could not be replaced"):
        guard.write_back(ident, b"new bytes")
    assert guard.acquire(f).md5 == ident.md5 and not list(tmp_path.glob(".specsurgeon-*"))


# ---- page numbering: inherited cover footers and every restart -------------------------------------------------------

def _spec_with_mode(mode, ops):
    return {"request": "x", "summary": "x", "assumptions": [], "questions": [], "applies_to": {"or": "all"},
            "mode": mode, "ops": ops}


NUMBER = lambda option: _spec_with_mode("formatting", [{"op": "fix_page_numbering", "params": {"option": option}}])


def _rewrite(data: bytes, part: str, fn) -> bytes:
    """``data`` with ``fn(root)`` applied to the XML of ``part``."""
    zin = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            b = zin.read(info.filename)
            if info.filename == part:
                root = etree.fromstring(b)
                fn(root)
                b = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            zout.writestr(info, b)
    return out.getvalue()


def test_cover_unnumbered_refuses_a_cover_footer_the_body_inherits():
    pkg = DocxPackage(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS", body_footer_linked=True))
    assert pkg.sections()[1].footer_refs == {}                                 # "Link to Previous": no reference
    assert effective_refs(pkg)[1][("footer", "default")] == "word/footer1.xml"   # … so Word shows the cover's
    with pytest.raises(JobException) as e:
        plan_spec(pkg, NUMBER("cover-unnumbered"))
    assert e.value.code == "shared-part" and "inherited" in e.value.reason and "Link to Previous" in e.value.reason
    # a body with a footer of its own still plans, applies and verifies
    pkg = DocxPackage(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    plan, frags = plan_spec(pkg, NUMBER("cover-unnumbered"))
    out = apply_spec(pkg, frags).output_bytes()
    assert run_suite(pkg.data, out, plan.to_dict(), "a.docx", "b.docx").passed


def test_continuous_removes_every_restart_after_the_cover():
    pkg = DocxPackage(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS", appendix_restart=True))
    assert [s.pg_num_start for s in pkg.sections()] == [None, 1, 1]
    plan, frags = plan_spec(pkg, NUMBER("continuous"))
    a = plan.property_assertions[0]
    assert a["kind"] == "section_props" and a["sections"] == [1, 2] and a["expected_sections"] == 2
    assert any("restarted" in fl for fl in plan.flags)
    out = apply_spec(pkg, frags).output_bytes()
    assert run_suite(pkg.data, out, plan.to_dict(), "a.docx", "b.docx").passed
    assert [s.pg_num_start for s in DocxPackage(out).sections()] == [None, None, None]

    def keep_appendix_restart(root):                       # the output the old op produced
        sp = root.find(W + "body").find(W + "sectPr")
        pg = etree.SubElement(sp, W + "pgNumType")
        pg.set(W + "start", "1")
    planted = _rewrite(out, "word/document.xml", keep_appendix_restart)
    rec = run_suite(pkg.data, planted, plan.to_dict(), "a.docx", "b.docx")
    assert not rec.passed and "section 2" in rec.get("V10").summary
    # two sections: as before
    pkg = DocxPackage(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    plan, frags = plan_spec(pkg, NUMBER("continuous"))
    assert plan.property_assertions[0]["sections"] == [1] and not plan.flags
    assert run_suite(pkg.data, apply_spec(pkg, frags).output_bytes(), plan.to_dict(), "a.docx", "b.docx").passed


def test_the_guard_sees_a_content_change_that_keeps_size_and_mtime(tmp_path):
    f = tmp_path / "A.docx"
    f.write_bytes(b"0123456789")
    ident = guard.acquire(f)
    st = f.stat()
    f.write_bytes(b"0123456780")
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert f.stat().st_size == ident.size and f.stat().st_mtime_ns == ident.mtime_ns
    assert "content changed" in (guard.drifted(ident) or "")
    with pytest.raises(guard.GuardError, match="content changed"):
        guard.write_back(ident, b"new")
    assert f.read_bytes() == b"0123456780"
