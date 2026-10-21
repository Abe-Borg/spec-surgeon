"""Comprehensive tests for Chunks 1–4:
1. Chunk 1: Body-aware fingerprinting, whitespace-preserving profiles, min/max expect ranges, mixed-batch compilation gate.
2. Chunk 2: Set-aside manifest (set_aside.json + SET_ASIDE_REPORT.md) and the next pass on only those files.
3. Chunk 3: Flexible whitespace/dash/NBSP matching, noise-tolerant run coalescing (lang/noProof/proofErr), rPrChange split handling, and template slot fallback rule chains (`first_match`) + multi-anchor consensus.
4. Chunk 4: Messy-doc selectors (`between_paragraphs`, `after_heading_regex`, `has_bold`, `is_all_caps`) + `delete_paragraphs` and `insert_paragraph` (tracked & untracked) with planted-defect verification tests.
"""
from __future__ import annotations

import copy
import io
import json
import zipfile
from pathlib import Path

import pytest
from docx import Document
from lxml import etree

from fake_claude import FakeClient, resp, tool
from make_fixtures import build, build_rich, build_template
from spec_surgeon.batch import load_set_aside, run_batch
from spec_surgeon.compiler import compile_request
from spec_surgeon.editspec import SpecError, plan_spec, validate_spec
from spec_surgeon.fingerprint import fingerprint
from spec_surgeon.ooxml import NOISE_RPR_LOCALS, paragraphs
from spec_surgeon.ops import JobException, get_op, insert_paragraph as insert_module, merge_fragments
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.profile import profile
from spec_surgeon.verify import run_suite
from spec_surgeon.verify.reader import VPackage

AUTHOR = "Abraham Borg"


def mutate_xml(docx: bytes, part: str, fn) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(docx))
    root = etree.fromstring(zin.read(part))
    fn(root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            z.writestr(info, serialize_xml(root) if info.filename == part else zin.read(info.filename))
    return buf.getvalue()


def _build_messy_inline_spec(sec_num: str = "21 13 13", title: str = "WET-PIPE SPRINKLER SYSTEMS") -> bytes:
    """A messy spec: inline section header with NBSPs and en-dash, fragmented runs with w:noProof/w:lang noise,
    all-Normal paragraphs with bold/all-caps pseudo-headings."""
    base = build(sec_num, title)

    def mess_up(root):
        body = root.find(W + "body")
        pars = body.findall(W + "p")
        # Replace standalone SECTION + next-line title with a single inline line containing NBSPs and double spaces
        p0, p1 = pars[0], pars[1]
        for c in list(p0):
            if c.tag != W + "pPr":
                p0.remove(c)
        r = etree.SubElement(p0, W + "r")
        t = etree.SubElement(r, W + "t")
        t.text = f"SECTION\u00a0\u00a0{sec_num} – {title}"
        for c in list(p1):
            if c.tag != W + "pPr":
                p1.remove(c)
        r_empty = etree.SubElement(p1, W + "r")
        etree.SubElement(r_empty, W + "t").text = "   "

        # Add a bold all-caps pseudo-heading in Normal style
        p_head = etree.Element(W + "p")
        rh = etree.SubElement(p_head, W + "r")
        rpr_h = etree.SubElement(rh, W + "rPr")
        etree.SubElement(rpr_h, W + "b")
        etree.SubElement(rh, W + "t").text = "PART 2 - PRODUCTS"
        pars[3].addprevious(p_head)

        # Fragment "FM Global" with w:proofErr and w:noProof/w:lang noise
        for p in body.findall(W + "p"):
            full = "".join(x.text or "" for x in p.iter(W + "t"))
            if "The auditor is FM " in full:
                runs = p.findall(W + "r")
                # Add w:lang and w:noProof noise to the second run, plus a proofErr between them
                rpr2 = etree.SubElement(runs[1], W + "rPr")
                runs[1].insert(0, rpr2)
                etree.SubElement(rpr2, W + "noProof")
                lang = etree.SubElement(rpr2, W + "lang")
                lang.set(W + "val", "en-US")
                pe = etree.Element(W + "proofErr")
                pe.set(W + "type", "spellStart")
                runs[0].addnext(pe)
                break

        # Add a paragraph with NBSPs and en-dash to test flexible_whitespace
        p_nbsp = etree.Element(W + "p")
        rn = etree.SubElement(p_nbsp, W + "r")
        tn = etree.SubElement(rn, W + "t")
        tn.text = " Coordinate with FM\u00a0\u00a0Global — Risk Engineering."
        pars[4].addnext(p_nbsp)

        # Add a closing PART 3 heading
        p_h3 = etree.Element(W + "p")
        rh3 = etree.SubElement(p_h3, W + "r")
        etree.SubElement(etree.SubElement(rh3, W + "rPr"), W + "b")
        etree.SubElement(rh3, W + "t").text = "PART 3 - EXECUTION"
        pars[8].addnext(p_h3)

    return mutate_xml(base, "word/document.xml", mess_up)


# ---- Chunk 1: Fingerprinting, Profile Whitespace & Mixed-Batch Gate ----------------------------------

def test_chunk1_fingerprint_separates_messy_body_from_clean():
    clean = DocxPackage(build("21 05 00", "COMMON WORK RESULTS"), name="clean.docx")
    messy = DocxPackage(_build_messy_inline_spec(), name="messy.docx")
    fp_clean = fingerprint(clean)
    fp_messy = fingerprint(messy)
    assert fp_clean["pattern_id"] == "b20e6c1da8"
    assert fp_messy["pattern_id"] != fp_clean["pattern_id"]
    assert fp_messy["body_hygiene"]["section_anchor_shape"] == "inline"
    assert fp_messy["body_hygiene"]["has_nbsp"] is True
    assert fp_messy["body_hygiene"]["has_multi_space"] is True
    assert fp_messy["body_hygiene"]["has_proof_err"] is True


def test_chunk1_profile_preserves_nbsp_and_surfaces_hygiene():
    messy = DocxPackage(_build_messy_inline_spec(), name="messy.docx")
    prof = profile(messy)
    assert "\u00a0\u00a0" in prof["first_paragraphs"][0]
    assert prof.get("hygiene", {}).get("has_nbsp") is True
    assert prof.get("hygiene", {}).get("section_anchor_shape") == "inline"


def test_chunk1_expect_supports_min_max_ranges():
    pkg = DocxPackage(build("21 05 00", "COMMON WORK RESULTS"))
    spec = {
        "request": "replace FM Global",
        "summary": "replace FM Global with GRC",
        "mode": "content",
        "ops": [{
            "op": "replace_text",
            "select": {"scope": ["document"]},
            "params": {"find": "FM Global", "replace": "GRC", "author": AUTHOR},
            "expect": {"text_diffs": {"min": 1, "max": 10}, "rev_delta": {"ins": {"min": 1}, "del": {"min": 1}}},
        }],
    }
    validate_spec(spec)
    plan, _ = plan_spec(pkg, spec)
    assert len(plan.diffs) == 4
    # Out-of-range min raises expect
    bad_spec = copy.deepcopy(spec)
    bad_spec["ops"][0]["expect"]["text_diffs"] = {"min": 10}
    with pytest.raises(JobException) as e:
        plan_spec(pkg, bad_spec)
    assert e.value.code == "expect"
    # Invalid range key raises SpecError
    invalid_spec = copy.deepcopy(spec)
    invalid_spec["ops"][0]["expect"]["text_diffs"] = {"minimum": 1}
    with pytest.raises(SpecError):
        validate_spec(invalid_spec)


def test_chunk1_compiler_accepts_spec_when_clean_majority_plans_and_messy_outlier_fails(tmp_path):
    tpl = tmp_path / "00 00 00 TEMPLATE.docx"
    tpl.write_bytes(build_template())
    d = tmp_path / "mixed"
    d.mkdir()
    (d / "21 05 00 – CLEAN.docx").write_bytes(build("21 05 00", "COMMON WORK RESULTS"))
    (d / "21 13 13 – MESSY.docx").write_bytes(_build_messy_inline_spec())

    # Standard template spec uses paragraph_after for SECTION_TITLE (works on CLEAN, fails on MESSY)
    spec_path = Path(__file__).resolve().parent.parent / "examples" / "specs" / "apply-header-footer-template.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["ops"][0]["params"]["template"] = str(tpl)
    fake = FakeClient(resp(tool("submit_editspec", {"spec": spec})))
    res = compile_request("apply template", sorted(d.glob("*.docx")), template=tpl,
                          answers=["ISSUED FOR CONSTRUCTION  2026-02-15"], client=fake)
    assert res.status == "ok", res.errors


# ---- Chunk 2: Set-Aside Sieve & Rinse-and-Repeat Loop -----------------------------------------------

def test_chunk2_set_aside_manifest_and_the_next_pass(tmp_path):
    tpl = tmp_path / "00 00 00 TEMPLATE.docx"
    tpl.write_bytes(build_template())
    d = tmp_path / "specs"
    d.mkdir()
    (d / "21 05 00 – CLEAN.docx").write_bytes(build("21 05 00", "COMMON WORK RESULTS"))
    (d / "21 13 13 – MESSY.docx").write_bytes(_build_messy_inline_spec("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))

    spec_path = Path(__file__).resolve().parent.parent / "examples" / "specs" / "apply-header-footer-template.json"
    pass1_spec = json.loads(spec_path.read_text(encoding="utf-8"))
    pass1_spec["ops"][0]["params"]["template"] = str(tpl)

    # Pass 1: runs pass1_spec -> CLEAN passes, MESSY is set aside
    out1 = tmp_path / "pass1"
    b1 = run_batch([str(d)], "edit-spec", {}, out1, workers=1, renderer="none", do_render=False, spec=pass1_spec)
    assert b1.counts()["PASS"] == 1 and b1.counts()["EXCEPTION"] == 1
    manifest = load_set_aside(out1)
    assert manifest["passed_count"] == 1 and manifest["set_aside_count"] == 1
    assert manifest["files"][0]["name"] == "21 13 13 – MESSY.docx"
    assert (out1 / "SET_ASIDE_REPORT.md").is_file()
    assert "Set-Aside Files" in (out1 / "BATCH_REPORT.md").read_text(encoding="utf-8")

    # Pass 2: the set-aside file's diagnostics reach the compiler, and the fallback spec passes on it
    pass2_spec = copy.deepcopy(pass1_spec)
    pass2_spec["ops"][0]["params"]["slot_fills"]["SECTION_TITLE"] = {
        "rule": "regex",
        "source": "document",
        "pattern": r"^SECTION\s+\d{2}\s+\d{2}\s+\d{2}\s*[-–—]\s*(.+)$",
    }
    fake_client = FakeClient(resp(tool("submit_editspec", {"spec": pass2_spec})))
    messy = [Path(manifest["files"][0]["source"])]
    res = compile_request("apply template", messy, template=tpl, prior_failures=manifest["files"],
                          answers=["ISSUED FOR CONSTRUCTION  2026-02-15"], client=fake_client)
    assert res.status == "ok", res.errors
    msg_content = fake_client.requests[0]["messages"][0]["content"]
    assert "PRIOR PASS SET-ASIDE DIAGNOSTICS" in msg_content
    assert "21 13 13 – MESSY.docx" in msg_content
    out2 = tmp_path / "pass2"
    b2 = run_batch([str(f) for f in messy], "edit-spec", {}, out2, workers=1, renderer="none", do_render=False,
                   spec=res.spec)
    assert b2.counts()["PASS"] == 1
    manifest2 = load_set_aside(out2)
    assert manifest2["passed_count"] == 1 and manifest2["set_aside_count"] == 0


# ---- Chunk 3: Garbage-Tolerant Matching, Run Coalescing & Fallback Chains ---------------------------

def test_chunk3_replace_text_flexible_whitespace_and_noise_coalescing():
    messy_bytes = _build_messy_inline_spec()
    pkg = DocxPackage(messy_bytes)

    # Without ignore_formatting_noise, "FM Global" in the noisy run-split paragraph raises spans-runs
    with pytest.raises(JobException) as e:
        get_op("replace_text").plan(pkg, {"scope": ["document"]},
                                    {"find": "FM Global", "replace": "GRC", "author": AUTHOR})
    assert e.value.code == "spans-runs"

    # With ignore_formatting_noise=True and flexible_whitespace=True, it matches both the noisy split run
    # AND the "FM\u00a0\u00a0Global — Risk Engineering" phrase, and passes V1–V10!
    frag = get_op("replace_text").plan(
        pkg,
        {"scope": ["document"]},
        {"find": "FM Global - Risk Engineering", "replace": "GRC Risk Engineering",
         "flexible_whitespace": True, "ignore_formatting_noise": True, "author": AUTHOR},
    )
    plan = merge_fragments([frag])
    work = pkg.copy()
    get_op("replace_text").apply(work, frag)
    out = work.output_bytes()
    rec = run_suite(messy_bytes, out, plan.to_dict(), "src.docx", "out.docx")
    assert rec.passed, rec.to_markdown()

    # Also test noise coalescing on all "FM Global" occurrences
    frag2 = get_op("replace_text").plan(
        pkg,
        {"scope": ["document"]},
        {"find": "FM Global", "replace": "GRC", "flexible_whitespace": True,
         "ignore_formatting_noise": True, "author": AUTHOR},
    )
    plan2 = merge_fragments([frag2])
    work2 = pkg.copy()
    get_op("replace_text").apply(work2, frag2)
    out2 = work2.output_bytes()
    rec2 = run_suite(messy_bytes, out2, plan2.to_dict(), "src.docx", "out.docx")
    assert rec2.passed, rec2.to_markdown()
    assert len(plan2.diffs) == 5


def test_chunk3_replace_text_allow_tracked_format_split():
    rich = build_rich()
    pkg = DocxPackage(rich)
    # By default, splitting a run with w:rPrChange refuses with tracked-format
    with pytest.raises(JobException) as e:
        get_op("replace_text").plan(pkg, {}, {"find": "Hangers", "replace": "Supports", "author": AUTHOR})
    assert e.value.code == "tracked-format"

    # With allow_tracked_format_split=True, it assigns fresh unique IDs to split fragments and passes V1–V10!
    frag = get_op("replace_text").plan(
        pkg, {}, {"find": "Hangers", "replace": "Supports", "allow_tracked_format_split": True, "author": AUTHOR}
    )
    plan = merge_fragments([frag])
    work = pkg.copy()
    get_op("replace_text").apply(work, frag)
    out = work.output_bytes()
    rec = run_suite(rich, out, plan.to_dict(), "src.docx", "out.docx")
    assert rec.passed, rec.to_markdown()
    assert plan.rev_delta["rPrChange"] >= 1


def test_chunk3_template_fallback_chain_handles_clean_and_messy_in_single_pass(tmp_path):
    tpl = tmp_path / "00 00 00 TEMPLATE.docx"
    tpl.write_bytes(build_template())

    # A single slot_fills spec using a `first_match` fallback chain for SECTION_TITLE
    params = {
        "template": str(tpl),
        "template_sections": {"cover": 0, "body": 1},
        "slot_fills": {
            "SECTION_NUMBER": {"rule": "regex", "source": "footer.default", "pattern": r"^(\d{2} \d{2} \d{2})"},
            "XX XX XX": {"rule": "regex", "source": "footer.default", "pattern": r"^(\d{2} \d{2} \d{2})"},
            "SECTION_TITLE": {
                "rule": "first_match",
                "rules": [
                    {"rule": "paragraph_after", "source": "document", "anchor_regex": r"^SECTION \d{2} \d{2} \d{2}$"},
                    {"rule": "regex", "source": "document", "pattern": r"^SECTION\s+\d{2}\s+\d{2}\s+\d{2}\s*[-–—:]\s*(.+)$"},
                ],
            },
            "ISSUED FOR PERMIT": {"rule": "literal", "value": "ISSUED FOR CONSTRUCTION  2026-02-15"},
        },
    }
    for raw_bytes, expected_title in [
        (build("21 05 00", "COMMON WORK RESULTS"), "COMMON WORK RESULTS"),
        (_build_messy_inline_spec("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"), "WET-PIPE SPRINKLER SYSTEMS"),
    ]:
        pkg = DocxPackage(raw_bytes)
        frag = get_op("apply_header_footer_template").plan(pkg, {"sections": "all"}, params)
        plan = merge_fragments([frag])
        work = pkg.copy()
        get_op("apply_header_footer_template").apply(work, frag)
        out = work.output_bytes()
        rec = run_suite(raw_bytes, out, plan.to_dict(), "src.docx", "out.docx")
        assert rec.passed, rec.to_markdown()
        assert frag.transform_data["fills"]["SECTION_TITLE"] == expected_title


# ---- Chunk 4: Messy-Doc Selectors & Structural Content Ops (`delete_paragraphs`, `insert_paragraph`) -

def test_chunk4_messy_doc_selectors_between_paragraphs_bold_and_all_caps():
    messy_bytes = _build_messy_inline_spec()
    pkg = DocxPackage(messy_bytes)

    # 1. Select only bold ALL-CAPS headings in an all-Normal document and change their font to Courier New
    frag = get_op("set_font").plan(
        pkg,
        {"paragraphs": {"has_bold": True, "is_all_caps": True}},
        {"font": "Courier New", "author": AUTHOR},
    )
    plan = merge_fragments([frag])
    work = pkg.copy()
    get_op("set_font").apply(work, frag)
    rec = run_suite(messy_bytes, work.output_bytes(), plan.to_dict(), "s.docx", "o.docx")
    assert rec.passed, rec.to_markdown()
    assert plan.property_assertions[0]["expected_runs"] == 2  # PART 2 - PRODUCTS and PART 3 - EXECUTION

    # 2. Select paragraphs strictly between PART 2 - PRODUCTS and PART 3 - EXECUTION
    frag2 = get_op("set_paragraph_props").plan(
        pkg,
        {"paragraphs": {"between_paragraphs": {"start_regex": r"^PART 2", "end_regex": r"^PART 3"}}},
        {"ind": {"left": 720}, "author": AUTHOR},
    )
    plan2 = merge_fragments([frag2])
    work2 = pkg.copy()
    get_op("set_paragraph_props").apply(work2, frag2)
    rec2 = run_suite(messy_bytes, work2.output_bytes(), plan2.to_dict(), "s.docx", "o.docx")
    assert rec2.passed, rec2.to_markdown()
    assert plan2.property_assertions[0]["expected_paragraphs"] >= 2


def test_chunk4_delete_paragraphs_tracked_and_untracked_and_planted_defects():
    src = build("21 05 00", "COMMON WORK RESULTS")
    pkg = DocxPackage(src)
    sel = {"paragraphs": {"text_regex": r"^A\.\tThe Owner's insurer"}}

    # 1. Tracked paragraph deletion
    frag = get_op("delete_paragraphs").plan(pkg, sel, {"tracked": True, "author": AUTHOR})
    plan = merge_fragments([frag])
    work = pkg.copy()
    get_op("delete_paragraphs").apply(work, frag)
    out = work.output_bytes()
    rec = run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")
    assert rec.passed, rec.to_markdown()
    assert plan.rev_delta == {"ins": 0, "del": 1, "del_mark": 1}

    # Planted defect: if the paragraph mark's w:del is missing in tracked mode, V4 and V10 catch it
    def drop_mark_del(root):
        for el in list(root.iter(W + "del")):
            if el.getparent() is not None and el.getparent().tag == W + "rPr":
                el.getparent().remove(el)
    bad_out = mutate_xml(out, "word/document.xml", drop_mark_del)
    bad_rec = run_suite(src, bad_out, plan.to_dict(), "src.docx", "out.docx")
    assert not bad_rec.passed
    assert bad_rec.get("V4").status == "FAIL" and bad_rec.get("V10").status == "FAIL"

    # 2. Untracked paragraph deletion
    frag_u = get_op("delete_paragraphs").plan(pkg, sel, {"tracked": False})
    plan_u = merge_fragments([frag_u])
    work_u = pkg.copy()
    get_op("delete_paragraphs").apply(work_u, frag_u)
    out_u = work_u.output_bytes()
    rec_u = run_suite(src, out_u, plan_u.to_dict(), "src.docx", "out.docx")
    assert rec_u.passed, rec_u.to_markdown()
    assert len(plan_u.diffs) == 1 and plan_u.diffs[0].after is None

    # "all" is a selection like any other (batch 6, F2): the size of a deletion is never a refusal. This file holds
    # pending tracked changes, so only a tracked deletion of everything is refused — for them; untracked it plans
    with pytest.raises(JobException) as e:
        get_op("delete_paragraphs").plan(pkg, {"paragraphs": "all"}, {})
    assert e.value.code == "existing-revisions"
    frag_a = get_op("delete_paragraphs").plan(pkg, {"paragraphs": "all"}, {"tracked": False})
    work_a = pkg.copy()
    get_op("delete_paragraphs").apply(work_a, frag_a)
    rec_a = run_suite(src, work_a.output_bytes(), merge_fragments([frag_a]).to_dict(), "src.docx", "out.docx")
    assert rec_a.passed, rec_a.to_markdown()
    # without a selector at all it is still refused: a deletion names what it deletes
    with pytest.raises(JobException) as e:
        get_op("delete_paragraphs").plan(pkg, {}, {})
    assert e.value.code == "bad-params"


def test_chunk4_insert_paragraph_tracked_and_untracked_and_planted_defects():
    src = build("21 05 00", "COMMON WORK RESULTS")
    pkg = DocxPackage(src)
    sel = {"paragraphs": {"text_regex": r"^1\.01\tSUMMARY"}}
    new_text = "F.\tProvide hydraulic calculation submittals for all sprinkler zones."

    # 1. Tracked paragraph insertion (after 1.01 SUMMARY)
    frag = get_op("insert_paragraph").plan(pkg, sel, {"text": new_text, "position": "after", "tracked": True, "author": AUTHOR})
    plan = merge_fragments([frag])
    work = pkg.copy()
    get_op("insert_paragraph").apply(work, frag)
    out = work.output_bytes()
    rec = run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")
    assert rec.passed, rec.to_markdown()
    assert plan.rev_delta == {"ins": 1, "del": 0, "ins_mark": 1}

    # Planted defect: if inserted paragraph text is altered by 1 char, V2, V8, and V10 all catch it
    def corrupt_inserted_text(root):
        for t in root.iter(W + "t"):
            if t.text and "hydraulic calculation" in t.text:
                t.text = t.text.replace("zones.", "zones!")
    bad_out = mutate_xml(out, "word/document.xml", corrupt_inserted_text)
    bad_rec = run_suite(src, bad_out, plan.to_dict(), "src.docx", "out.docx")
    assert not bad_rec.passed
    assert bad_rec.get("V2").status == "FAIL"
    assert bad_rec.get("V8").status == "FAIL"
    assert bad_rec.get("V10").status == "FAIL"

    # 2. Untracked paragraph insertion (before 1.01 SUMMARY)
    frag_u = get_op("insert_paragraph").plan(pkg, sel, {"text": new_text, "position": "before", "tracked": False})
    plan_u = merge_fragments([frag_u])
    work_u = pkg.copy()
    get_op("insert_paragraph").apply(work_u, frag_u)
    out_u = work_u.output_bytes()
    rec_u = run_suite(src, out_u, plan_u.to_dict(), "src.docx", "out.docx")
    assert rec_u.passed, rec_u.to_markdown()

    # Compiler traceability check enforces that insert_paragraph text comes verbatim from the user
    from spec_surgeon.compiler import untraceable
    spec_ins = {
        "request": "insert paragraph",
        "summary": "s",
        "mode": "content",
        "ops": [{"op": "insert_paragraph", "select": sel, "params": {"text": new_text}}],
    }
    assert untraceable(spec_ins, ["please add a paragraph about submittals"]) == [new_text]
    assert untraceable(spec_ins, [f"insert '{new_text}' under summary"]) == []


# ---- noise, bookmarks and inherited formatting -----------------------------------------------------------------------

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


def test_tracked_delete_tolerates_bookmarks_proofing_markers_and_hyperlinks_but_not_equations():
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
    # batch 6 (F2): a tracked deletion now goes through a hyperlink as Word records it — the link's run in a w:del of its
    # own inside the w:hyperlink (the link's text joins the block text: anchor on the start only)
    linked = mutate_xml(build("21 05 00", "X"), "word/document.xml", add_hyperlink)
    plan, out, rec = run_op(linked, "delete_paragraphs", {"paragraphs": {"text_regex": r"^GENERAL"}},
                            {"tracked": True, "author": AUTHOR})
    assert rec.passed, rec.to_markdown()
    p_out = next(p for p in doc_root(out).iter(W + "p") if p.find(W + "hyperlink") is not None)
    assert p_out.find(f"{W}hyperlink/{W}del/{W}r/{W}delText").text == "link"

    def add_math(root):                                    # content a tracked deletion is not written over: refused
        p = next(p for p in root.find(W + "body").findall(W + "p")
                 if any((t.text or "") == "GENERAL" for t in p.iter(W + "t")))
        etree.SubElement(p, "{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath")
    with pytest.raises(JobException) as e:
        get_op("delete_paragraphs").plan(DocxPackage(mutate_xml(build("21 05 00", "X"), "word/document.xml", add_math)),
                                         {"paragraphs": {"text_regex": r"^GENERAL$"}}, {"tracked": True, "author": AUTHOR})
    assert e.value.code == "structure"


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
