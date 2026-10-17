"""An op's reads are plan-time conflicts (CLAUDE.md rule 9; 2026-10-16).

``apply_header_footer_template`` computes its pins from the target's styles part and theme, its tab rescale and
distance alignment from each section's ``pgSz``/``pgMar``, and predicts every section's ``titlePg``;
``fix_page_numbering``'s cover-unnumbered reads every section's ``titlePg`` to know which footer the cover shows and
whether a later section shows it too. Another op in the same spec changing one of those used to plan cleanly: the
style ops and ``set_page_setup`` failed V10 after apply (a late refusal, and the compiler's dry plan accepted the
spec), and a template flipping the cover's titlePg under cover-unnumbered PASSED every check with the page number still
on the cover. Every test here plants the old behaviour (the op's reads removed from its touches) to show what the
conflict stands between the user and, asserts the plan-time refusal naming the op to split out, and keeps the
combinations that are verified planning (``continuous`` numbering with the templating op, a font change over the body
with it, two ops verifying one part).
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
from lxml import etree

from make_fixtures import build, build_rich
from spec_surgeon.compiler import _Batch, check_submission
from spec_surgeon.editspec import apply_spec, load_spec, plan_spec
from spec_surgeon.ops import OPS, JobException
from spec_surgeon.ops.base import verify_family
from spec_surgeon.ops.hf_common import effective_refs
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.verify import run_suite

HF_SPEC = Path(__file__).resolve().parent.parent / "examples" / "specs" / "match-template-spec-header-footer.json"
REQUEST = "match the template spec's header and footer"


def _spec(ops, request=REQUEST):
    return {"request": request, "summary": request, "assumptions": [], "questions": [], "applies_to": {"or": "all"},
            "mode": "formatting", "ops": ops}


def _hf(**params):
    """The example spec's templating op (the architect's spec as the template: Normal Arial 10, no title page)."""
    op = copy.deepcopy(load_spec(HF_SPEC)["ops"][0])
    op["params"].update(params)
    return op


def _run(pkg, spec):
    plan, frags = plan_spec(pkg, spec)
    out = apply_spec(pkg, frags).output_bytes()
    return out, run_suite(pkg.data, out, plan.to_dict(), "src.docx", "out.docx")


def _fonts_from_doc_defaults(data: bytes) -> bytes:
    """build()'s target with Normal's font removed and docDefaults set to Arial: the same effective font as before
    (so the templating op pins nothing against the Arial template), now supplied by docDefaults, not by Normal."""
    work = DocxPackage(data).copy()
    root = work.xml("word/styles.xml")
    for st in root.iter(W + "style"):
        if st.get(W + "styleId") == "Normal":
            rpr = st.find(W + "rPr")
            rf = rpr.find(W + "rFonts") if rpr is not None else None
            if rf is not None:
                rpr.remove(rf)
    rpr = root.find(f"{W}docDefaults/{W}rPrDefault/{W}rPr")
    rf = rpr.find(W + "rFonts")
    if rf is None:
        rf = etree.Element(W + "rFonts")
        rpr.insert(0, rf)
    for a in list(rf.attrib):
        del rf.attrib[a]
    for slot in ("ascii", "hAnsi", "cs", "eastAsia"):
        rf.set(W + slot, "Arial")
    work.write_xml("word/styles.xml", root)
    return work.output_bytes()


def _title_page_target() -> bytes:
    """build_rich() — a cover with titlePg, a first-page footer AND a default footer, both with a PAGE field — without
    evenAndOddHeaders (the template spec has it off; the templating op refuses a document-wide difference)."""
    work = DocxPackage(build_rich()).copy()
    root = work.xml("word/settings.xml")
    for e in root.findall(W + "evenAndOddHeaders"):
        root.remove(e)
    work.write_xml("word/settings.xml", root)
    return work.output_bytes()


def _page_field_in(pkg: DocxPackage, part: str) -> bool:
    root = pkg.xml(part)
    return any((t.text or "").strip().startswith("PAGE") for t in root.iter(W + "instrText")) or \
        any((f.get(W + "instr") or "").strip().startswith("PAGE") for f in root.iter(W + "fldSimple"))


@pytest.fixture
def without_reads(monkeypatch):
    """Plant the old behaviour: an op's plan without its reads (the ``verify`` touches), as every op was before."""
    def strip(name):
        cls = OPS[name]
        original = cls.plan

        def plan(self, pkg, select, params):
            frag = original(self, pkg, select, params)
            frag.touches = [t for t in frag.touches if not verify_family(t[2])[0]]
            return frag
        monkeypatch.setattr(cls, "plan", plan)
    return strip


# ---- 1. the styles part: the pins are computed from the target's styles --------------------------------------------

@pytest.mark.parametrize("style_op, target", [
    ({"op": "set_style_font", "select": {"styles": "all"}, "params": {"font": "Courier New"}}, "normal"),
    ({"op": "set_doc_defaults_font", "params": {"font": "Courier New"}}, "doc-defaults"),
    ({"op": "set_properties", "select": {"styles": {"ids": ["Normal"]}}, "params": {"run": {"b": True}}}, "normal"),
])
def test_a_style_change_under_the_templating_op_is_refused_as_a_conflict(style_op, target, without_reads):
    data = build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS")            # Normal Arial 10, like the template spec
    pkg = DocxPackage(_fonts_from_doc_defaults(data) if target == "doc-defaults" else data)
    spec = _spec([style_op, _hf()], REQUEST + " and change every style's font")
    with pytest.raises(JobException) as e:
        plan_spec(pkg, spec)
    assert e.value.code == "conflict"
    assert "word/styles.xml" in e.value.reason and "as it is in the source" in e.value.reason
    assert f"split out ['#1 {style_op['op']}'] into a spec of its own and run it first" in e.value.reason
    assert "then ['#2 apply_header_footer_template'] on its outputs" in e.value.reason
    assert e.value.details["reads"] == ["#2 apply_header_footer_template"]
    assert e.value.details["changes"] == [f"#1 {style_op['op']}"]
    # the other order in the spec is the same conflict: an order the engine had to guess is an order nobody verified
    with pytest.raises(JobException) as e2:
        plan_spec(pkg, _spec([_hf(), style_op]))
    assert e2.value.code == "conflict" and f"split out ['#2 {style_op['op']}']" in e2.value.reason
    # planted: without the templating op's read of the styles part the spec plans cleanly, and the pins — the diff of
    # the template's effective properties against the SOURCE's — miss the change: the template's new header and
    # footer parts render in the changed style, and V10's template_parts fails after apply. Never a wrong output,
    # but a late refusal, not the plan-time conflict rule 9 promises.
    without_reads("apply_header_footer_template")
    _, rec = _run(pkg, spec)
    assert not rec.passed
    v10 = [c for c in rec.checks if c.id == "V10"][0]
    assert v10.status == "FAIL" and "template_parts" in v10.summary
    # the two ops as two specs in sequence plan and verify: the second plans on the first's outputs
    out1, rec1 = _run(pkg, _spec([style_op]))
    assert rec1.passed, rec1.to_markdown()
    _, rec2 = _run(DocxPackage(out1), _spec([_hf()]))
    assert rec2.passed, rec2.to_markdown()


def test_the_compilers_dry_plan_rejects_the_style_change_with_the_templating_op(tmp_path):
    folder = tmp_path / "specs"
    folder.mkdir()
    (folder / "21 13 13 – WET-PIPE SPRINKLER SYSTEMS.docx").write_bytes(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    (folder / "21 05 00 – COMMON WORK RESULTS.docx").write_bytes(build("21 05 00", "COMMON WORK RESULTS"))
    spec = _spec([{"op": "set_style_font", "select": {"styles": "all"}, "params": {"font": "Courier New"}}, _hf()])
    batch = _Batch(sorted(folder.glob("*.docx")), Path(spec["ops"][1]["params"]["template"]))
    errs = check_submission(spec, batch, [spec["request"]])
    assert errs and "conflict" in errs[0] and "split out ['#1 set_style_font']" in errs[0]
    assert check_submission(_spec([_hf()]), batch, [REQUEST]) == []


# ---- 2. the sectPr geometry: text width (pgSz, pgMar) and the header/footer distances ------------------------------

@pytest.mark.parametrize("params, family", [
    ({"margins": {"left": 720, "right": 720}}, "pgMar"),
    ({"margins": {"header": 500}}, "pgMar"),
    ({"size": {"w": 15840, "h": 24480}}, "pgSz"),
])
def test_a_page_setup_change_under_the_templating_op_is_refused_as_a_conflict(params, family, without_reads):
    pkg = DocxPackage(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    setup = {"op": "set_page_setup", "select": {"sections": "all"}, "params": params}
    for templating in (_hf(), _hf(rescale_tabs=False)):
        with pytest.raises(JobException) as e:
            plan_spec(pkg, _spec([setup, templating], REQUEST + " and set the margins"))
        assert e.value.code == "conflict"
        assert f"plans from {family} of word/document.xml [sectPr:0] as it is in the source" in e.value.reason
        assert f"#1 set_page_setup would change {family} there underneath" in e.value.reason
        assert "split out #1 set_page_setup into a spec of its own and run it first, then " \
               "#2 apply_header_footer_template on its outputs" in e.value.reason
        assert e.value.details["reads"] == ["#2 apply_header_footer_template"]
    # a section the page setup does not touch is no conflict with it … but the templating op applies to every
    # section, so a single section's change still meets its read there
    with pytest.raises(JobException) as e:
        plan_spec(pkg, _spec([dict(setup, select={"sections": [1]}), _hf()]))
    assert e.value.code == "conflict" and "[sectPr:1]" in e.value.reason
    if params.get("margins") == {"left": 720, "right": 720}:
        # planted: without the read, the tabs are rescaled to the SOURCE's text width and V10's template_parts, which
        # re-derives the width from the output's sectPr, fails after apply — a late refusal, not a wrong output
        without_reads("apply_header_footer_template")
        _, rec = _run(pkg, _spec([setup, _hf()]))
        assert not rec.passed
        v10 = [c for c in rec.checks if c.id == "V10"][0]
        assert v10.status == "FAIL" and "template_parts" in v10.summary
        # in sequence (margins first, the template on the outputs) both plan and verify
        out1, rec1 = _run(pkg, _spec([setup]))
        assert rec1.passed, rec1.to_markdown()
        _, rec2 = _run(DocxPackage(out1), _spec([_hf()]))
        assert rec2.passed, rec2.to_markdown()


# ---- 3. the combinations that are verified still plan ---------------------------------------------------------------

def test_continuous_numbering_and_a_body_font_change_with_the_templating_op_still_plan_and_verify():
    pkg = DocxPackage(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))      # the body section restarts at 1
    continuous = {"op": "fix_page_numbering", "params": {"option": "continuous"}}
    # pgNumType is neither read nor changed by the templating op; both ops' V10 assertions strip their own sectPr
    # children and the residual compares the rest — verified in either order, so no conflict (decided 2026-10-16)
    for ops in ([continuous, _hf()], [_hf(), continuous]):
        plan, frags = plan_spec(pkg, _spec(ops))
        assert {o["op"] for o in plan.ops} == {"fix_page_numbering", "apply_header_footer_template"}
        out = apply_spec(pkg, frags).output_bytes()
        rec = run_suite(pkg.data, out, plan.to_dict(), "src.docx", "out.docx")
        assert rec.passed, rec.to_markdown()
        o_secs = DocxPackage.sect_prs(DocxPackage(out).xml("word/document.xml"))
        assert all(sp.find(W + "pgNumType") is None or sp.find(W + "pgNumType").get(W + "start") is None
                   for sp in o_secs[1:])
    # a font change over the body (its read: word/document.xml as a whole must survive) beside the templating op's
    # reads of the same part's sectPr geometry: two ops verifying one member never conflict
    font = {"op": "set_font", "select": {"scope": ["document"], "runs": "all"}, "params": {"font": "Courier New"}}
    plan, frags = plan_spec(pkg, _spec([font, _hf()]))
    reads = [(m, t, a) for f in frags for m, t, a in f.touches if verify_family(a)[0]]
    assert ("word/document.xml", "scope", "verify") in reads
    assert ("word/document.xml", "sectPr:0", "verify:pgMar") in reads and ("word/styles.xml", "*", "verify") in reads
    out = apply_spec(pkg, frags).output_bytes()
    rec = run_suite(pkg.data, out, plan.to_dict(), "src.docx", "out.docx")
    assert rec.passed, rec.to_markdown()
    # the theme part is read too (no op changes it today; the read is declared all the same)
    assert ("word/theme/theme1.xml", "*", "verify") in reads


# ---- 4. titlePg: which footer the cover shows ------------------------------------------------------------------------

def test_a_title_page_flip_under_cover_unnumbered_is_refused_as_a_conflict(without_reads):
    pkg = DocxPackage(_title_page_target())
    cover = pkg.sections()[0]
    assert cover.title_pg and set(cover.footer_refs) == {"first", "default"}
    assert all(_page_field_in(pkg, p) for p in cover.footer_refs.values())
    unnumber = {"op": "fix_page_numbering", "params": {"option": "cover-unnumbered"}}
    headers = _hf(kinds=["header"])                 # footers untouched; the template has no title page, so the
    spec = _spec([unnumber, headers], "make the cover unnumbered and match the template spec's header")  # cover's titlePg goes
    with pytest.raises(JobException) as e:
        plan_spec(pkg, spec)
    assert e.value.code == "conflict"
    assert "op #1 fix_page_numbering plans from titlePg of word/document.xml [sectPr:0] as it is in the source" in e.value.reason
    assert "#2 apply_header_footer_template would change titlePg there underneath" in e.value.reason
    assert "split out #2 apply_header_footer_template into a spec of its own and run it first, then " \
           "#1 fix_page_numbering on its outputs" in e.value.reason
    # planted: without fix_page_numbering's read of titlePg the spec plans and applies — with the page number still on
    # the cover: the PAGE paragraph was cleared from the first-page footer, which the cover no longer shows once its
    # titlePg is off (V7 skips page 1 for cover-unnumbered). It used to PASS every check; since batch 5's review V10's
    # cover_unnumbered kind reads what the cover shows in the output and fails it
    without_reads("fix_page_numbering")
    out, rec = _run(pkg, spec)
    assert not rec.passed and "cover_unnumbered: the cover shows footer" in rec.get("V10").summary
    opkg = DocxPackage(out)
    o_cover = opkg.sections()[0]
    assert not o_cover.title_pg
    shown = effective_refs(opkg)[0][("footer", "default")]
    assert shown == cover.footer_refs["default"] and _page_field_in(opkg, shown)      # the wrong output, unseen
    assert not _page_field_in(opkg, cover.footer_refs["first"])                      # the edit landed where nobody looks
    # in sequence the engine says why it cannot be done: on the templated output the cover's one footer is also the
    # body's (shared-part) — the honest answer the combined spec's PASS was hiding
    out1, rec1 = _run(pkg, _spec([headers]))
    assert rec1.passed, rec1.to_markdown()
    with pytest.raises(JobException) as e:
        plan_spec(DocxPackage(out1), _spec([unnumber]))
    assert e.value.code == "shared-part"


def test_cover_unnumbered_with_a_template_that_keeps_the_title_page_setting_still_plans_and_verifies():
    pkg = DocxPackage(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))      # no title page, like the template
    unnumber = {"op": "fix_page_numbering", "params": {"option": "cover-unnumbered"}}
    plan, frags = plan_spec(pkg, _spec([unnumber, _hf(kinds=["header"])]))
    assert ("word/document.xml", "sectPr:0", "verify:titlePg") in frags[0].touches
    assert not any(a == "titlePg" for _, _, a in frags[1].touches)            # the template changes no titlePg here
    out = apply_spec(pkg, frags).output_bytes()
    rec = run_suite(pkg.data, out, plan.to_dict(), "src.docx", "out.docx")
    assert rec.passed, rec.to_markdown()
    # the templating op replacing the footers too is the older conflict (the cover's footer part is removed as a whole)
    with pytest.raises(JobException) as e:
        plan_spec(pkg, _spec([unnumber, _hf()]))
    assert e.value.code == "conflict" and "replaces, removes or restructures it as a whole" in e.value.reason
