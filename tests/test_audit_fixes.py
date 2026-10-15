"""Tests for the fixes that followed the 2026-10-14 independent audit (docs/audits/2026-10-14_v0.3.0_independent_audit.md).

Each test pins one finding: the behaviour the audit found missing, plus the planted defect (a wrong output and, where
the definition lives on both sides, a wrong implementation shared by plan and apply) that proves the verifier catches it.
"""
from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path

import pytest
from lxml import etree

from make_fixtures import build, build_rich
from test_adaptive_messy import _build_messy_inline_spec, mutate_xml
from spec_surgeon import batch as batch_module
from spec_surgeon import compiler as C
from spec_surgeon.batch import load_set_aside, run_batch
from spec_surgeon.catalog import op_catalog
from spec_surgeon.editspec import SpecError, load_spec, plan_spec, validate_spec
from spec_surgeon.ooxml import NOISE_RPR_LOCALS, block_full_text, paragraphs
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import insert_paragraph as insert_module
from spec_surgeon.ops import selectors as selectors_module
from spec_surgeon.ops.selectors import StyleSheet, paragraph_selected
from spec_surgeon.ops.slots import extract_fill
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.verify import CheckResult, run_suite
from spec_surgeon.verify.props import PartView, Styles, para_matches
from spec_surgeon.verify.reader import STYLES_REL, VPackage
from spec_surgeon.verify.template import fill_value

AUTHOR = "Abraham Borg"
EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "specs"


def run_op(src: bytes, op: str, select, params):
    pkg = DocxPackage(src)
    frag = get_op(op).plan(pkg, select, params)
    plan = merge_fragments([frag])
    work = pkg.copy()
    get_op(op).apply(work, frag)
    out = work.output_bytes()
    return plan, out, run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")


def fails(rec):
    return {c.id for c in rec.checks if c.status == "FAIL"}


def doc_root(data: bytes):
    return etree.fromstring(VPackage(data).read("word/document.xml"))


def first_text_el(root, prefix: str):
    for t in root.find(W + "body").iter(W + "t"):
        if t.text and t.text.startswith(prefix):
            return t
    raise AssertionError(prefix)


def _add_rprchange_to_owner_run(root):
    t = first_text_el(root, "The Owner's insurer is FM Global")
    r = t.getparent()
    rpr = r.find(W + "rPr")
    if rpr is None:
        rpr = etree.Element(W + "rPr")
        r.insert(0, rpr)
    etree.SubElement(rpr, W + "b")
    chg = etree.SubElement(rpr, W + "rPrChange")
    chg.set(W + "id", "900"); chg.set(W + "author", "Jeremy"); chg.set(W + "date", "2026-09-22T10:15:00Z")
    etree.SubElement(chg, W + "rPr")


# ---- F1: V4 must see duplicate revision ids of every kind, even when the duplicate reuses a source id ---------

def test_v4_catches_duplicate_revision_ids_on_split_rprchange_copies():
    src = mutate_xml(build("21 05 00", "X"), "word/document.xml", _add_rprchange_to_owner_run)
    sel = {"paragraphs": {"text_regex": "Owner's insurer"}, "scope": ["document"]}
    for find, d_rpr in (("The Owner's", 2), ("FM Global", 3), ("FM Approved.", 2)):   # start / middle / end of the run
        plan, out, rec = run_op(src, "replace_text", sel,
                                {"find": find, "replace": "XYZ", "allow_tracked_format_split": True, "author": AUTHOR})
        assert rec.passed, rec.to_markdown()
        assert plan.rev_delta["rPrChange"] == d_rpr

    plan, out, rec = run_op(src, "replace_text", sel,
                            {"find": "FM Global", "replace": "XYZ", "allow_tracked_format_split": True, "author": AUTHOR})

    def same_id_everywhere(root):            # the transform forgot to renumber the copies
        for chg in root.iter(W + "rPrChange"):
            chg.set(W + "id", "900")
    bad = mutate_xml(out, "word/document.xml", same_id_everywhere)
    assert sum(1 for c in doc_root(bad).iter(W + "rPrChange") if c.get(W + "id") == "900") == 4
    bad_rec = run_suite(src, bad, plan.to_dict(), "src.docx", "out.docx")
    assert bad_rec.get("V4").status == "FAIL"
    assert "DUPLICATE ids ['900']" in bad_rec.get("V4").summary
    # a duplicate the SOURCE already had is baseline, not a new defect
    src_dup = mutate_xml(src, "word/document.xml", lambda root: [el.set(W + "id", "900") for el in root.iter(W + "ins")])
    plan2, out2, rec2 = run_op(src_dup, "replace_text", {"scope": ["document"]},
                               {"find": "Schedule 40", "replace": "Schedule 10", "author": AUTHOR})
    assert rec2.get("V4").status == "PASS", rec2.get("V4").summary


# ---- F2: the compiler gate -----------------------------------------------------------------------------------

def test_compiler_gate_rejects_a_spec_that_plans_on_no_file_whatever_the_refusal_code(tmp_path):
    only_messy = tmp_path / "messy"; only_messy.mkdir()
    (only_messy / "21 13 13 – MESSY.docx").write_bytes(_build_messy_inline_spec())
    spec = {"request": "replace FM Global with GRC", "summary": "s", "mode": "content",
            "ops": [{"op": "replace_text", "select": {"scope": ["document"]},
                     "params": {"find": "FM Global", "replace": "GRC", "author": AUTHOR}}]}
    errs = C.check_submission(spec, C._Batch(sorted(only_messy.glob("*.docx")), None), [spec["request"]])
    assert errs and "[spans-runs]" in " ".join(errs) and "MESSY" in " ".join(errs)
    rich = tmp_path / "rich"; rich.mkdir(); (rich / "rich.docx").write_bytes(build_rich())
    spec_tf = {"request": "replace Hangers with Supports", "summary": "s", "mode": "content",
               "ops": [{"op": "replace_text", "select": {},
                        "params": {"find": "Hangers", "replace": "Supports", "author": AUTHOR}}]}
    assert "[tracked-format]" in " ".join(C.check_submission(spec_tf, C._Batch([rich / "rich.docx"], None), [spec_tf["request"]]))
    assert C.check_submission(spec, C._Batch([], None), [spec["request"]]) == ["the batch has no .docx files to plan on"]
    # the mixed-batch acceptance is unchanged: one clean pattern planning is enough
    mixed = tmp_path / "mixed"; mixed.mkdir()
    (mixed / "21 05 00 – CLEAN.docx").write_bytes(build("21 05 00", "COMMON WORK RESULTS"))
    (mixed / "21 13 13 – MESSY.docx").write_bytes(_build_messy_inline_spec())
    assert C.check_submission(spec, C._Batch(sorted(mixed.glob("*.docx")), None), [spec["request"]]) == []


# ---- F9: expect validation -----------------------------------------------------------------------------------

def test_expect_validation_refuses_impossible_ranges_and_unknown_revision_kinds():
    base = {"request": "r", "summary": "s", "mode": "content",
            "ops": [{"op": "replace_text", "select": {"scope": ["document"]},
                     "params": {"find": "FM Global", "replace": "GRC", "author": AUTHOR}, "expect": {}}]}
    for exp in ({"text_diffs": {"min": 5, "max": 1}}, {"text_diffs": {"min": -3}}, {"rev_delta": {"delx": 0}},
                {"rev_delta": {"ins": {"max": -1}}}):
        s = copy.deepcopy(base); s["ops"][0]["expect"] = exp
        with pytest.raises(SpecError) as e:
            validate_spec(s)
        assert e.value.code == "spec"
    ok = copy.deepcopy(base)
    ok["ops"][0]["expect"] = {"text_diffs": {"min": 1, "max": 10},
                              "rev_delta": {"ins": {"min": 1}, "del_mark": 0, "rPrChange": {"max": 2}}}
    validate_spec(ok)
    plan, _ = plan_spec(DocxPackage(build("21 05 00", "COMMON WORK RESULTS")), ok)
    assert len(plan.diffs) == 4


# ---- F4 + F10: noise coalescing -------------------------------------------------------------------------------

def test_noise_merge_keeps_complex_script_flag_and_untouched_proofing_markers():
    assert NOISE_RPR_LOCALS == {"lang", "noProof", "webHidden"}

    def split_with_cs(root):                 # 'FM Global' split; the second half carries <w:cs/> and nothing else
        t = first_text_el(root, "The Owner's insurer is FM Global")
        r = t.getparent(); a, b = t.text.split("Global", 1); t.text = a
        r2 = copy.deepcopy(r); r2.find(W + "t").text = "Global" + b
        rpr = r2.find(W + "rPr")
        if rpr is None:
            rpr = etree.Element(W + "rPr"); r2.insert(0, rpr)
        etree.SubElement(rpr, W + "cs")
        r.addnext(r2)
    cs_src = mutate_xml(build("21 05 00", "X"), "word/document.xml", split_with_cs)
    with pytest.raises(JobException) as e:
        get_op("replace_text").plan(DocxPackage(cs_src), {"scope": ["document"]},
                                    {"find": "FM Global", "replace": "GRC", "ignore_formatting_noise": True, "author": AUTHOR})
    assert e.value.code == "spans-runs"

    def split_with_lang_and_markers(root):
        for p in root.find(W + "body").findall(W + "p"):
            if any((t.text or "") == "The auditor is FM " for t in p.iter(W + "t")):
                runs = p.findall(W + "r")
                rpr = etree.Element(W + "rPr"); etree.SubElement(rpr, W + "noProof")
                etree.SubElement(rpr, W + "lang").set(W + "val", "en-US"); runs[1].insert(0, rpr)
                pe = etree.Element(W + "proofErr"); pe.set(W + "type", "spellStart"); runs[0].addnext(pe)   # between the merging runs
                for _ in range(3):                                                                    # far from the match
                    pe2 = etree.Element(W + "proofErr"); pe2.set(W + "type", "gramEnd"); p.append(pe2)
                return
    src = mutate_xml(build("21 05 00", "X"), "word/document.xml", split_with_lang_and_markers)
    plan, out, rec = run_op(src, "replace_text", {"scope": ["document"]},
                            {"find": "FM Global", "replace": "GRC", "ignore_formatting_noise": True, "author": AUTHOR})
    assert rec.passed, rec.to_markdown()
    count = lambda data: sum(1 for _ in doc_root(data).iter(W + "proofErr"))
    assert (count(src), count(out)) == (4, 3)      # only the marker between the two merged runs went


# ---- F7: tracked deletion with bookmarks and proofing markers -------------------------------------------------

def test_tracked_delete_tolerates_bookmarks_and_proofing_markers_but_not_hyperlinks():
    def add_bookmark(root):
        for p in root.find(W + "body").findall(W + "p"):
            if any((t.text or "") == "GENERAL" for t in p.iter(W + "t")):
                bs = etree.Element(W + "bookmarkStart"); bs.set(W + "id", "0"); bs.set(W + "name", "_GoBack"); p.insert(1, bs)
                pe = etree.Element(W + "proofErr"); pe.set(W + "type", "spellStart"); p.insert(1, pe)
                be = etree.Element(W + "bookmarkEnd"); be.set(W + "id", "0"); p.append(be)
                return
    src = mutate_xml(build("21 05 00", "X"), "word/document.xml", add_bookmark)
    sel = {"paragraphs": {"text_regex": r"^GENERAL$"}}
    plan, out, rec = run_op(src, "delete_paragraphs", sel, {"tracked": True, "author": AUTHOR})
    assert rec.passed, rec.to_markdown()
    assert plan.rev_delta == {"ins": 0, "del": 1, "del_mark": 1}
    p_out = next(p for p in doc_root(out).iter(W + "p") if p.find(W + "bookmarkStart") is not None)
    assert p_out.find(W + "del") is not None and p_out.find(W + "del/" + W + "r/" + W + "delText") is not None

    def add_hyperlink(root):
        for p in root.find(W + "body").findall(W + "p"):
            if any((t.text or "") == "GENERAL" for t in p.iter(W + "t")):
                h = etree.SubElement(p, W + "hyperlink"); h.set(W + "anchor", "top")
                etree.SubElement(etree.SubElement(h, W + "r"), W + "t").text = "link"
                return
    with pytest.raises(JobException) as e:                  # the link's text joins the block text: anchor on the start only
        get_op("delete_paragraphs").plan(DocxPackage(mutate_xml(build("21 05 00", "X"), "word/document.xml", add_hyperlink)),
                                         {"paragraphs": {"text_regex": r"^GENERAL"}}, {"tracked": True, "author": AUTHOR})
    assert e.value.code == "structure"


# ---- F6: insert_paragraph inherits the anchor's formatting, and V10 re-derives it --------------------------------

def _format_summary_anchor(root):
    for p in root.find(W + "body").findall(W + "p"):
        if any((t.text or "") == "1.01" for t in p.iter(W + "t")):
            ppr = etree.Element(W + "pPr"); p.insert(0, ppr)
            etree.SubElement(ppr, W + "keepNext")
            ind = etree.SubElement(ppr, W + "ind"); ind.set(W + "left", "720"); ind.set(W + "hanging", "720")
            for r in p.findall(W + "r"):
                rpr = etree.Element(W + "rPr"); r.insert(0, rpr)
                rf = etree.SubElement(rpr, W + "rFonts"); rf.set(W + "ascii", "Courier New"); rf.set(W + "hAnsi", "Courier New")
                etree.SubElement(rpr, W + "b"); etree.SubElement(rpr, W + "sz").set(W + "val", "18")
            return


def _inserted(root):
    return next(p for p in root.iter(W + "p") if any("New clause" in (t.text or "") for t in p.iter(W + "t")))


def test_insert_paragraph_inherits_anchor_formatting_and_v10_verifies_it():
    base = build("21 05 00", "COMMON WORK RESULTS")
    src = mutate_xml(base, "word/document.xml", _format_summary_anchor)
    sel = {"paragraphs": {"text_regex": r"^1\.01\tSUMMARY"}}
    params = {"text": "F.\tNew clause.", "position": "after", "tracked": True, "author": AUTHOR}
    plan, out, rec = run_op(src, "insert_paragraph", sel, params)
    assert rec.passed, rec.to_markdown()
    new_p = _inserted(doc_root(out))
    assert new_p.find(W + "pPr/" + W + "ind").get(W + "hanging") == "720" and new_p.find(W + "pPr/" + W + "keepNext") is not None
    run = new_p.find(W + "ins/" + W + "r")
    assert run.find(W + "rPr/" + W + "rFonts").get(W + "ascii") == "Courier New"
    assert run.find(W + "rPr/" + W + "b") is not None and run.find(W + "rPr/" + W + "sz").get(W + "val") == "18"

    # planted OUTPUT defects: the new paragraph lost the indent / the run lost its bold
    def drop_ind(root):
        ppr = _inserted(root).find(W + "pPr"); ppr.remove(ppr.find(W + "ind"))
    assert fails(run_suite(src, mutate_xml(out, "word/document.xml", drop_ind), plan.to_dict(), "s", "o")) == {"V10"}

    def drop_bold(root):
        for r in _inserted(root).iter(W + "r"):
            r.find(W + "rPr").remove(r.find(W + "rPr/" + W + "b"))
    assert fails(run_suite(src, mutate_xml(out, "word/document.xml", drop_bold), plan.to_dict(), "s", "o")) == {"V10"}

    # planted IMPLEMENTATION defect shared by plan and apply: the transform's inherit rule forgets the indent
    good = insert_module.inherited_ppr

    def forgetful(anchor):
        c = good(anchor)
        if c is not None:
            for ch in list(c):
                if ch.tag == W + "ind":
                    c.remove(ch)
        return c
    insert_module.inherited_ppr = forgetful
    try:
        _, _, bad_rec = run_op(src, "insert_paragraph", sel, params)
    finally:
        insert_module.inherited_ppr = good
    assert fails(bad_rec) == {"V10"}

    # formatting "none" is the old behaviour; an explicit style replaces the inherited one
    _, out_n, rec_n = run_op(src, "insert_paragraph", sel, {**params, "formatting": "none"})
    assert rec_n.passed and _inserted(doc_root(out_n)).find(W + "pPr/" + W + "ind") is None
    _, out_s, rec_s = run_op(src, "insert_paragraph", sel, {**params, "tracked": False, "style": "Heading1"})
    assert rec_s.passed and _inserted(doc_root(out_s)).find(W + "pPr/" + W + "pStyle").get(W + "val") == "Heading1"

    # the anchor's section break and its pending formatting change are never inherited
    pars = paragraphs(DocxPackage(base).xml("word/document.xml"))
    si = next(i for i, p in enumerate(pars) if p.find(W + "pPr/" + W + "sectPr") is not None)
    _, out_b, rec_b = run_op(base, "insert_paragraph", {"paragraphs": {"index_range": [si, si + 1]}},
                             {"text": "X before the break", "position": "before", "author": AUTHOR})
    n_sect = lambda data: sum(1 for _ in doc_root(data).iter(W + "sectPr"))
    assert rec_b.passed and n_sect(out_b) == n_sect(base)

    def add_rprchange(root):
        for p in root.find(W + "body").findall(W + "p"):
            if any((t.text or "") == "1.01" for t in p.iter(W + "t")):
                r = p.find(W + "r"); rpr = etree.Element(W + "rPr"); r.insert(0, rpr); etree.SubElement(rpr, W + "i")
                chg = etree.SubElement(rpr, W + "rPrChange")
                chg.set(W + "id", "901"); chg.set(W + "author", "J"); chg.set(W + "date", "2026-09-22T10:15:00Z")
                etree.SubElement(chg, W + "rPr")
                return
    plan_r, out_r, rec_r = run_op(mutate_xml(base, "word/document.xml", add_rprchange), "insert_paragraph", sel, params)
    assert rec_r.passed and plan_r.rev_delta.get("rPrChange", 0) == 0
    assert _inserted(doc_root(out_r)).find(".//" + W + "rPrChange") is None


# ---- F11: fills keep the document's own characters --------------------------------------------------------------

def test_fills_keep_the_documents_own_characters_on_both_sides():
    def nbsp_title(root):
        for t in root.find(W + "body").iter(W + "t"):
            if t.text and "WET-PIPE" in t.text:
                t.text = t.text.replace("WET-PIPE SPRINKLER", "WET-PIPE SPRINKLER"); return
    messy = DocxPackage(mutate_xml(_build_messy_inline_spec("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"), "word/document.xml", nbsp_title))
    rx = {"rule": "regex", "source": "document", "pattern": r"^SECTION\s+\d{2}\s+\d{2}\s+\d{2}\s*[-–—]\s*(.+)$"}
    t, v = extract_fill(messy, "T", rx), fill_value(VPackage(messy.data), "T", rx)
    assert t == v == "WET-PIPE SPRINKLER SYSTEMS"
    clean = DocxPackage(mutate_xml(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"), "word/document.xml", nbsp_title))
    pa = {"rule": "paragraph_after", "source": "document", "anchor_regex": r"^SECTION \d{2} \d{2} \d{2}$"}
    assert extract_fill(clean, "T", pa) == fill_value(VPackage(clean.data), "T", pa) == "WET-PIPE SPRINKLER SYSTEMS"
    # consensus is still judged with whitespace collapsed: two anchors whose titles differ only in spacing agree
    def two_anchors(root):
        body = root.find(W + "body"); pars = body.findall(W + "p")
        p = etree.Element(W + "p"); etree.SubElement(etree.SubElement(p, W + "r"), W + "t").text = "SECTION 21 13 13"
        p2 = etree.Element(W + "p"); etree.SubElement(etree.SubElement(p2, W + "r"), W + "t").text = "WET-PIPE  SPRINKLER SYSTEMS"
        pars[6].addnext(p); p.addnext(p2)
    two = DocxPackage(mutate_xml(clean.data, "word/document.xml", two_anchors))
    assert extract_fill(two, "T", pa) == fill_value(VPackage(two.data), "T", pa) == "WET-PIPE SPRINKLER SYSTEMS"


# ---- F3: a failed output never sits beside the verified ones -----------------------------------------------------

def test_failed_outputs_are_quarantined_and_explained(tmp_path, monkeypatch):
    real = batch_module.run_suite

    def planted_v2_failure(src, out, plan, s, o):
        rec = real(src, out, plan, s, o)
        rec.set(CheckResult("V2", "Text preservation", "FAIL", "planted"))
        return rec
    monkeypatch.setattr(batch_module, "run_suite", planted_v2_failure)
    d = tmp_path / "in"; d.mkdir()
    (d / "21 05 00 – CLEAN.docx").write_bytes(build("21 05 00", "COMMON WORK RESULTS"))
    spec = {"request": "replace FM Global with GRC", "summary": "s", "mode": "content",
            "ops": [{"op": "replace_text", "select": {"scope": ["document"]},
                     "params": {"find": "FM Global", "replace": "GRC", "author": AUTHOR}}]}
    out = tmp_path / "out"
    b = run_batch([str(d)], "edit-spec", {}, out, workers=1, renderer="none", do_render=False, spec=spec)
    assert b.counts()["FAIL"] == 1
    assert not list((out / "outputs").iterdir())
    assert [p.name for p in (out / "failed").iterdir()] == ["21 05 00 – CLEAN.docx"]
    f = b.files[0]
    assert f.output == out / "failed" / "21 05 00 – CLEAN.docx" and f.record.output == str(f.output)
    assert f.record.get("V9").status == "NA" and "failed/" in f.record.get("V9").summary
    item = load_set_aside(out)["files"][0]
    assert item["code"] == "verify-fail" and item["category"] == "Failed Verification (Not Delivered)"
    assert "failed/" in (out / "BATCH_REPORT.md").read_text(encoding="utf-8")


def test_v9_guard_failures_are_quarantined_too(tmp_path, monkeypatch):
    """Codex review on the v0.3.1 PR: V9 runs after the first quarantine check, so a drifted source or a refused
    in-place write-back used to leave a stale output in outputs/ (and record it as delivered)."""
    spec = {"request": "replace FM Global with GRC", "summary": "s", "mode": "content",
            "ops": [{"op": "replace_text", "select": {"scope": ["document"]},
                     "params": {"find": "FM Global", "replace": "GRC", "author": AUTHOR}}]}
    # non-in-place: the source drifts while the batch runs
    d = tmp_path / "drift"; d.mkdir()
    (d / "21 05 00 – A.docx").write_bytes(build("21 05 00", "A"))
    monkeypatch.setattr(batch_module.guard, "drifted", lambda identity: "mtime moved during the run")
    out = tmp_path / "out1"
    b = run_batch([str(d)], "edit-spec", {}, out, workers=1, renderer="none", do_render=False, spec=spec)
    f = b.files[0]
    assert f.status == "FAIL" and f.record.get("V9").status == "FAIL" and f.delivered_to is None
    assert not list((out / "outputs").iterdir()) and [p.name for p in (out / "failed").iterdir()] == ["21 05 00 – A.docx"]
    assert f.output == out / "failed" / "21 05 00 – A.docx"
    monkeypatch.undo()
    # in place: the guard refuses the write-back (file open in Word / drifted)
    d2 = tmp_path / "locked"; d2.mkdir()
    src = d2 / "21 05 00 – B.docx"; src.write_bytes(build("21 05 00", "B")); before = src.read_bytes()

    def refuse(identity, data):
        raise batch_module.guard.GuardError("Word lock file present: the document is open")
    monkeypatch.setattr(batch_module.guard, "write_back", refuse)
    out2 = tmp_path / "out2"
    b2 = run_batch([str(d2)], "edit-spec", {}, out2, workers=1, renderer="none", do_render=False, spec=spec, in_place=True)
    f2 = b2.files[0]
    assert f2.status == "FAIL" and f2.record.get("V9").status == "FAIL" and f2.delivered_to is None
    assert src.read_bytes() == before
    assert not list((out2 / "outputs").iterdir()) and [p.name for p in (out2 / "failed").iterdir()] == ["21 05 00 – B.docx"]
    assert "failed/" in f2.record.get("V9").summary and "failed/" in (out2 / "BATCH_REPORT.md").read_text(encoding="utf-8")


# ---- F5: the compiler can read the grammar it is expected to use ------------------------------------------------

def test_catalog_documents_fill_rules_and_expect_ranges_and_the_fallback_example_plans_on_both_layouts():
    cat = op_catalog()
    assert set(cat["fill_rules"]) == {"literal", "regex", "paragraph_after", "first_match", "whitespace", "in_template"}
    assert "'min'" in cat["expect_syntax"]["value"] and "rPrChange" in cat["expect_syntax"]["rev_delta"]
    assert "first_match" in cat["ops"]["apply_header_footer_template"]["params"]["properties"]["slot_fills"]["description"]
    assert any("first_match" in json.dumps(e) for e in cat["worked_examples"])
    spec = load_spec(EXAMPLES / "apply-header-footer-template-fallback.json")
    validate_spec(spec)
    for raw, title in ((build("21 05 00", "COMMON WORK RESULTS"), "COMMON WORK RESULTS"),
                       (_build_messy_inline_spec("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"), "WET-PIPE SPRINKLER SYSTEMS")):
        plan, frags = plan_spec(DocxPackage(raw), spec)
        assert frags[0].transform_data["fills"]["SECTION_TITLE"] == title


# ---- audit probes kept as tests: selector agreement, divergence, rPrChange position --------------------------------

def _edge_case_fixture() -> bytes:
    def styles_mut(root):
        for sid, kind, el in (("BoldHead", "paragraph", "b"), ("CapsHead", "paragraph", "caps"), ("BoldChar", "character", "b")):
            st = etree.SubElement(root, W + "style"); st.set(W + "type", kind); st.set(W + "styleId", sid)
            etree.SubElement(st, W + "name").set(W + "val", sid)
            etree.SubElement(etree.SubElement(st, W + "rPr"), W + el)

    def body_mut(root):
        anchor = root.find(W + "body").findall(W + "p")[5]

        def para(text, pstyle=None, rstyle=None, direct=(), deleted_bold_run=False):
            p = etree.Element(W + "p")
            if pstyle:
                etree.SubElement(etree.SubElement(p, W + "pPr"), W + "pStyle").set(W + "val", pstyle)
            r = etree.SubElement(p, W + "r")
            if rstyle or direct:
                rpr = etree.SubElement(r, W + "rPr")
                if rstyle:
                    etree.SubElement(rpr, W + "rStyle").set(W + "val", rstyle)
                for tag, val in direct:
                    e = etree.SubElement(rpr, W + tag)
                    if val is not None:
                        e.set(W + "val", val)
            etree.SubElement(r, W + "t").text = text
            if deleted_bold_run:
                d = etree.SubElement(p, W + "del"); d.set(W + "id", "777"); d.set(W + "author", "Jack"); d.set(W + "date", "2026-01-01T00:00:00Z")
                rd = etree.SubElement(d, W + "r"); etree.SubElement(etree.SubElement(rd, W + "rPr"), W + "b")
                etree.SubElement(rd, W + "delText").text = "DELETED BOLD"
            return p
        new = [para("style bold heading", pstyle="BoldHead"), para("style caps heading", pstyle="CapsHead"),
               para("char style bold", rstyle="BoldChar"), para("style bold but direct off", pstyle="BoldHead", direct=(("b", "0"),)),
               para("plain with deleted bold run", deleted_bold_run=True), para("MIXED Case TITLE 123"), para("1.01"),
               para("ALL CAPS NO STYLE"), para("bold val 1 explicit", direct=(("b", "1"),))]
        for p in reversed(new):
            anchor.addnext(p)
    return mutate_xml(mutate_xml(build("21 05 00", "X"), "word/styles.xml", styles_mut), "word/document.xml", body_mut)


def test_new_selectors_agree_between_transform_and_verifier_and_a_shared_bug_is_caught():
    src = _edge_case_fixture()
    pkg = DocxPackage(src); root = pkg.xml("word/document.xml"); pars = paragraphs(root); sheet = StyleSheet(pkg)
    vp = VPackage(src); view = PartView(vp.xml("word/document.xml")); vstyles = Styles(vp.xml(vp.related(STYLES_REL)))
    preds = [{"has_bold": True}, {"is_all_caps": True}, {"after_heading_regex": r"^GENERAL$"},
             {"between_paragraphs": {"start_regex": r"^GENERAL$", "end_regex": r"^2\.01"}},
             {"between_paragraphs": {"start_regex": r"^GENERAL$", "end_regex": r"^2\.01", "include_bounds": True}},
             {"between_paragraphs": {"start_regex": r"^GENERAL$"}}, {"text_regex": r"^A\.\tThe Owner"},
             {"has_bold": True, "is_all_caps": True}]
    texts = [block_full_text(p) for p in pars]
    for pred in preds:
        t_sel = [i for i, p in enumerate(pars) if paragraph_selected(pred, p, i, texts[i], sheet, all_pars=pars)]
        v_sel = [i for i, p in enumerate(view.pars) if para_matches(pred, p, i, view, vstyles)]
        assert t_sel == v_sel, (pred, t_sel, v_sel)
    bold = [texts[i] for i, p in enumerate(pars) if paragraph_selected({"has_bold": True}, p, i, texts[i], sheet, all_pars=pars)]
    assert bold == ["style bold heading", "char style bold", "bold val 1 explicit"]
    caps = [texts[i] for i, p in enumerate(pars) if paragraph_selected({"is_all_caps": True}, p, i, texts[i], sheet, all_pars=pars)]
    assert "style caps heading" in caps and "MIXED Case TITLE 123" not in caps and "1.01" not in caps

    good = selectors_module._paragraph_has_bold
    selectors_module._paragraph_has_bold = lambda p, sheet: any(r.find(W + "rPr/" + W + "b") is not None
                                                                for r in selectors_module._live_runs(p))
    try:
        _, _, bad_rec = run_op(src, "set_font", {"paragraphs": {"has_bold": True}}, {"font": "Courier New", "author": AUTHOR})
    finally:
        selectors_module._paragraph_has_bold = good
    assert bad_rec.get("V10").status == "FAIL"
    _, _, rec = run_op(src, "set_font", {"paragraphs": {"has_bold": True}}, {"font": "Courier New", "author": AUTHOR})
    assert rec.passed, rec.to_markdown()


def test_misplaced_rprchange_is_caught_by_v5a_and_v10():
    src = mutate_xml(build("21 05 00", "X"), "word/document.xml", _add_rprchange_to_owner_run)
    plan, out, rec = run_op(src, "replace_text", {"paragraphs": {"text_regex": "Owner's insurer"}, "scope": ["document"]},
                            {"find": "FM Global", "replace": "XYZ", "allow_tracked_format_split": True, "author": AUTHOR})
    assert rec.passed

    def rprchange_first(root):
        for chg in root.iter(W + "rPrChange"):
            rpr = chg.getparent(); rpr.remove(chg); rpr.insert(0, chg)
    bad = run_suite(src, mutate_xml(out, "word/document.xml", rprchange_first), plan.to_dict(), "s", "o")
    assert {"V5a", "V10"} <= fails(bad)
