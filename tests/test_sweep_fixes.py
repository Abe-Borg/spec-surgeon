"""Tests for the fixes from the 2026-10-15 hands-on sweep (docs/audits/2026-10-15_v0.4.0_hands_on_sweep.md).

Each test pins one finding of the sweep, as the audit-fix tests do: the behaviour that was missing, and where the
engine could have delivered something unverified, the planted case that now refuses.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path

import pytest

from make_fixtures import build, build_reference_spec, build_rich
from spec_surgeon import guard
from spec_surgeon.batch import run_batch
from spec_surgeon.compiler import _Batch, check_submission
from spec_surgeon.editspec import apply_spec, load_spec, plan_spec
from spec_surgeon.gui import Session
from spec_surgeon.ops import JobException, get_op
from spec_surgeon.ops.selectors import rpr_selected
from spec_surgeon.package import DocxPackage
from spec_surgeon.planner import summarize_plan
from spec_surgeon.preview import approval_token
from spec_surgeon.verify import run_suite

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "specs"
HF_SPEC = EXAMPLES / "match-template-spec-header-footer.json"
PREPARED_SPEC = EXAMPLES / "apply-header-footer-template.json"
ALL = ["document", "headers", "footers", "footnotes", "endnotes"]


def _spec(request, mode, ops, **extra):
    return {"request": request, "summary": request, "assumptions": [], "questions": [], "applies_to": {"or": "all"},
            "mode": mode, "ops": ops, **extra}


@pytest.fixture
def project(tmp_path):
    """Two specs and the architect's spec as the template, with a spec file that names it."""
    folder = tmp_path / "specs"
    folder.mkdir()
    (folder / "21 13 13 – WET-PIPE SPRINKLER SYSTEMS.docx").write_bytes(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    (folder / "21 13 16 – DRY-PIPE SPRINKLER SYSTEMS.docx").write_bytes(build("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS"))
    tpl = tmp_path / "ACME.docx"
    tpl.write_bytes(build_reference_spec())
    spec = load_spec(HF_SPEC)
    spec["ops"][0]["params"]["template"] = str(tpl)
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    return folder, tpl, spec, spec_file


# ---- 1. the approval token pins the template ----------------------------------------------------------------

def test_approval_token_changes_when_the_template_changes(project):
    folder, tpl, spec, _ = project
    files = sorted(folder.glob("*.docx"))
    before = approval_token(spec, files)
    assert before == approval_token(spec, files)                       # stable
    st = tpl.stat()
    os.utime(tpl, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))  # touched (saved again, same bytes)
    touched = approval_token(spec, files)
    assert touched != before
    # different bytes of the same size with the mtime put back: the content hash still voids the token
    data = bytearray(tpl.read_bytes())
    data[-1] ^= 0xFF
    tpl.write_bytes(bytes(data))
    os.utime(tpl, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert approval_token(spec, files) not in (before, touched)
    # a spec without a template is unaffected by template files
    plain = _spec("x", "formatting", [{"op": "remove_header_footer", "select": {"sections": "all"}, "params": {}}])
    assert approval_token(plain, files) == approval_token(plain, files)


def test_run_refuses_after_the_template_spec_was_edited_since_the_preview(project, tmp_path):
    folder, tpl, spec, spec_file = project
    s = Session(folder=folder, template=tpl, out_root=tmp_path / "out")
    s.load_spec(spec_file)
    s.make_preview(render=False, log=lambda m: None)
    # the architect sends a new version of their spec with another header line
    tpl.write_bytes(build_reference_spec(extra_header_text="ISSUED FOR BID — DO NOT USE FOR CONSTRUCTION"))
    with pytest.raises(ValueError, match="changed since the preview"):
        s.run(in_place=False, render=False, log=lambda m: None)
    assert s.last_batch is None
    # a new preview of the new template is approved as usual
    s.make_preview(render=False, log=lambda m: None)
    s.run(in_place=False, render=False, log=lambda m: None)
    assert s.last_batch is not None and all(f.status == "PASS" for f in s.last_batch.files)


# ---- 2. an op that verifies a part another op removes is a conflict, not a V10 failure -------------------------

def test_font_change_over_headers_that_are_being_removed_is_refused_as_a_conflict():
    pkg = DocxPackage(build_rich())
    # the compiler's usual "change all fonts": direct-font runs only — the headers hold none, so the font op would
    # only VERIFY them; before the sweep that planned cleanly and failed V10 ("header1.xml missing from the output")
    ops = [{"op": "remove_header_footer", "select": {"sections": "all"}, "params": {}},
           {"op": "set_font", "select": {"scope": ALL, "runs": {"has_direct_font": True}}, "params": {"font": "Arial"}},
           {"op": "set_style_font", "select": {"styles": "all"}, "params": {"font": "Arial"}},
           {"op": "set_doc_defaults_font", "params": {"font": "Arial"}}]
    with pytest.raises(JobException) as e:
        plan_spec(pkg, _spec("remove all headers and footers and change all fonts to Arial", "formatting", ops))
    assert e.value.code == "conflict"
    assert "remove_header_footer" in e.value.reason and "set_font" in e.value.reason
    assert "would verify word/header1.xml without changing it" in e.value.reason and "narrow its scope" in e.value.reason
    # a font op that also changes runs in the removed part is the same conflict (the general wording)
    ops[1]["select"]["runs"] = "all"
    with pytest.raises(JobException) as e:
        plan_spec(pkg, _spec("x", "formatting", ops))
    assert e.value.code == "conflict" and "narrow their selectors" in e.value.reason
    # the same with the font change kept to the body plans, applies and verifies
    ops[1]["select"]["scope"] = ["document"]
    spec = _spec("remove all headers and footers and change all fonts to Arial", "formatting", ops)
    plan, frags = plan_spec(pkg, spec)
    out = apply_spec(pkg, frags).output_bytes()
    rec = run_suite(pkg.data, out, plan.to_dict(), "src.docx", "out.docx")
    assert rec.passed, rec.to_markdown()


def test_paragraph_props_over_a_removed_footer_is_refused_and_two_verifiers_of_one_part_are_not():
    pkg = DocxPackage(build_rich())
    with pytest.raises(JobException) as e:
        plan_spec(pkg, _spec("x", "formatting", [
            {"op": "remove_header_footer", "select": {"sections": "all", "kinds": ["footer"]}, "params": {}},
            {"op": "set_paragraph_props", "select": {"scope": ["footers"], "paragraphs": "all"}, "params": {"jc": "right"}}]))
    assert e.value.code == "conflict"
    # two ops that both verify word/document.xml and change different things in it merge as before
    plan, _ = plan_spec(pkg, _spec("x", "formatting", [
        {"op": "set_paragraph_props", "select": {"paragraphs": {"style_in": ["Heading1"]}}, "params": {"jc": "center"}},
        {"op": "set_font", "select": {"scope": ["document"], "paragraphs": {"style_in": ["SpecNote"]}, "runs": "all"},
         "params": {"font": "Arial"}}]))
    assert {o["op"] for o in plan.ops} == {"set_paragraph_props", "set_font"}


# ---- 3. a single-section spec in a batch mapped to a cover+body template ---------------------------------------

def test_single_section_spec_plans_with_a_cover_and_body_template_and_the_batch_compiles(tmp_path):
    spec = load_spec(PREPARED_SPEC)
    tpl = Path(spec["ops"][0]["params"]["template"])
    single = DocxPackage(build("21 98 00", "VARIANT", single_section=True))
    plan, frags = plan_spec(single, spec)                       # was: unknown-slot (body rules unused here)
    out = apply_spec(single, frags).output_bytes()
    rec = run_suite(single.data, out, plan.to_dict(), "src.docx", "out.docx")
    assert rec.passed, rec.to_markdown()
    assert [a["part"] for a in plan.property_assertions if a["kind"] == "template_parts"]
    folder = tmp_path / "specs"
    folder.mkdir()
    (folder / "21 13 13 – WET-PIPE SPRINKLER SYSTEMS.docx").write_bytes(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    (folder / "SINGLE SECTION.docx").write_bytes(single.data)
    batch = _Batch(sorted(folder.glob("*.docx")), tpl)
    assert len(batch.prof["patterns"]) == 2
    assert check_submission(spec, batch, [spec["request"], "ISSUED FOR CONSTRUCTION  2026-02-15"]) == []
    # a rule naming a slot in none of the mapped template sections is still refused, on every file
    spec["ops"][0]["params"]["slot_fills"]["NOWHERE"] = {"rule": "literal", "value": "ISSUED FOR CONSTRUCTION  2026-02-15"}
    for pkg in (single, DocxPackage(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))):
        with pytest.raises(JobException) as e:
            plan_spec(pkg, spec)
        assert e.value.code == "unknown-slot" and "NOWHERE" in e.value.reason
    errs = check_submission(spec, batch, [spec["request"], "ISSUED FOR CONSTRUCTION  2026-02-15"])
    assert errs and "unknown-slot" in errs[0]


# ---- 4. the guard and read-only files --------------------------------------------------------------------------

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
        spec = _spec("replace", "untracked", [{"op": "replace_text", "tracked": False,
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


# ---- 5, 6. small things -----------------------------------------------------------------------------------------

def test_a_pure_tracked_deletion_is_named_in_the_plan_summary():
    pkg = DocxPackage(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    plan, _ = plan_spec(pkg, _spec("delete the words 'FM Global'", "content",
                                   [{"op": "replace_text", "params": {"find": "FM Global", "replace": ""}}]))
    lines = summarize_plan(plan)
    assert any("tracked deletion" in line for line in lines)
    assert not any(line == "text: no block changes text" for line in lines)


def test_run_selector_rejects_a_run_without_a_direct_font_plainly():
    assert rpr_selected({"has_direct_font": True}, None, False) is False
    assert rpr_selected({"has_direct_font": False}, None, False) is True
