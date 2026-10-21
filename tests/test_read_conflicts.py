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
import io
from pathlib import Path
import zipfile

import pytest
from lxml import etree

from make_fixtures import build, build_rich, build_style_template, build_styles
from spec_surgeon.compiler import _Batch, check_submission
from spec_surgeon.editspec import apply_spec, load_spec, plan_spec
from spec_surgeon.ops import JobException, OPS, get_op
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


def test_the_compilers_dry_plan_composes_the_style_change_with_the_templating_op_into_steps(tmp_path):
    """The plan-time conflict stands (above); the compiler answers it by composing the two ops into ordered steps,
    the style change first and the templating op on its outputs, instead of refusing the submission (2026-10-20:
    the user never performs the split)."""
    from spec_surgeon.compiler import compose_submission
    from spec_surgeon.editspec import is_chain
    folder = tmp_path / "specs"
    folder.mkdir()
    (folder / "21 13 13 – WET-PIPE SPRINKLER SYSTEMS.docx").write_bytes(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    (folder / "21 05 00 – COMMON WORK RESULTS.docx").write_bytes(build("21 05 00", "COMMON WORK RESULTS"))
    spec = _spec([{"op": "set_style_font", "select": {"styles": "all"}, "params": {"font": "Courier New"}}, _hf()])
    batch = _Batch(sorted(folder.glob("*.docx")), Path(spec["ops"][1]["params"]["template"]))
    assert check_submission(spec, batch, [spec["request"]]) == []
    outcome, errs = compose_submission(spec, batch, [spec["request"]])
    assert errs == [] and is_chain(outcome)
    assert [[o["op"] for o in st["spec"]["ops"]] for st in outcome["stages"]] == [
        ["set_style_font"], ["apply_header_footer_template"]]
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


@pytest.mark.parametrize("op", [
    {"op": "remove_highlight"},
    {"op": "resolve_tracked_changes", "params": {"action": "accept", "authors": ["Nobody"]}},
    {"op": "remove_hidden_text", "params": {"tracked": False}},
    {"op": "remove_notes", "params": {"rules": [{"style_in": ["NoSuchStyle"]}], "tracked": False},
     "select": {"scope": ["headers"]}},
    {"op": "remove_empty_paragraphs", "params": {"tracked": False}, "select": {"scope": ["headers"]}},
    {"op": "remove_hyperlinks"},
    {"op": "unlink_fields", "params": {"types": ["DOCPROPERTY"]}},
], ids=lambda o: o["op"])
def test_a_clean_up_op_beside_a_header_removal_is_a_plan_time_conflict(op):
    """Rule 9: each of these re-derives its decisions over every part in scope, so it declares (part, "scope",
    "verify") — beside an op that removes a header part it is refused at plan time, even when it has nothing to do in
    that header (it used to plan, and its V10 kind then failed on the missing part after apply)."""
    pkg = DocxPackage(build_rich())
    spec = {"request": "r", "summary": "s", "mode": "untracked" if op["op"] in (
        "resolve_tracked_changes", "remove_hidden_text", "remove_notes", "remove_empty_paragraphs") else "formatting",
        "ops": [{"op": "remove_header_footer", "select": {"sections": "all", "kinds": ["header"]}}, op]}
    with pytest.raises(JobException) as e:
        plan_spec(pkg, spec)
    assert e.value.code == "conflict" and "remove_header_footer" in str(e.value)
    alone = {**spec, "ops": [op]}
    plan, frags = plan_spec(pkg, alone)                    # on its own the op plans (a no-op where nothing matches)
    out = apply_spec(pkg, frags).output_bytes()
    rec = run_suite(pkg.data, out, plan.to_dict(), "src.docx", "out.docx")
    assert [c.id for c in rec.checks if c.status == "FAIL"] == []


# ---- reads through style chains, table styles and the set of styles (built-in batch 4) -------------------------------

STYLES = "word/styles.xml"


@pytest.fixture(scope="module")
def src() -> bytes:
    return build_styles()


@pytest.fixture(scope="module")
def tpl(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("tpl") / "OFFICE STYLES.docx"
    path.write_bytes(build_style_template())
    return str(path)


def _spec_of(*ops, mode="formatting"):
    return {"request": "r", "summary": "s", "mode": mode, "ops": list(ops)}


def _conflict(data, *ops, mode="formatting"):
    for order in (ops, tuple(reversed(ops))):
        with pytest.raises(JobException) as e:
            plan_spec(DocxPackage(data), _spec_of(*order, mode=mode))
        assert e.value.code == "conflict", (order, e.value.code, str(e.value))
    return str(e.value)


def _passes(data, *ops, mode="formatting"):
    for order in (ops, tuple(reversed(ops))):
        pkg = DocxPackage(data)
        plan, frags = plan_spec(pkg, _spec_of(*order, mode=mode))
        out = apply_spec(DocxPackage(data), frags).output_bytes()
        rec = run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")
        assert rec.passed, rec.to_markdown()


def _with_part(data: bytes, name: str, edit) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            raw = zin.read(item.filename)
            if item.filename == name:
                raw = edit(raw)
            zout.writestr(item, raw)
    return buf.getvalue()


def test_set_properties_beside_an_in_paragraph_text_edit_plans_and_verifies_again(src):
    """Review D4: set_properties' new reads of the runs it finds already as asked refused any text edit of the same
    paragraph (it planned and passed before batch 4). The reads are soft now: the text edit plans beside it."""
    widow = {"op": "set_properties", "params": {"paragraph": {"widowControl": True}}}
    no_italics = {"op": "set_properties", "params": {"run": {"i": False}}}
    edit = {"op": "replace_text", "params": {"find": "fire alarm", "replace": "fire-alarm"}}
    for sp in (widow, no_italics):
        _passes(src, sp, edit, mode="content")
    # a change of what it read (the runs' properties) is still refused
    _conflict(src, no_italics, {"op": "clear_direct_formatting", "params": {"paragraph": False}})


def test_a_remapped_table_style_beside_ops_reading_the_cell_paragraphs_conflicts(src, tpl):
    """Review A1/F3: the effective values of a paragraph in a table come through the table's style too."""
    cells = {"paragraphs": {"in_table": True}}
    remap = {"op": "map_styles", "params": {"map": [{"from": "ArchTable", "to": "Tbl Mine"}], "template": tpl}}
    for other in ({"op": "set_properties", "select": cells, "params": {"run": {"b": False}}},
                  {"op": "set_properties", "select": cells, "params": {"paragraph": {"keepNext": False}}},
                  {"op": "set_tab_stops", "select": cells, "params": {"clear": [1440]}},
                  {"op": "set_borders_shading", "select": cells, "params": {"shading": "none"}},
                  {"op": "set_paragraph_props", "select": cells, "params": {"spacing": {"before": 120}}}):
        assert "tbl:0" in _conflict(src, remap, other), other


def test_a_style_the_file_lacks_imported_beside_a_paragraph_naming_it_conflicts(src, tpl):
    """Review A1: a paragraph names a style the file does not define (it falls back to nothing); an import adding that
    style in the same spec changes the paragraph's chain underneath an op reading it."""
    def dangle(raw):
        root = etree.fromstring(raw)
        p = list(root.iter(W + "p"))[7]                      # "Coordinate with …", no pStyle
        ppr = p.find(W + "pPr")
        if ppr is None:
            ppr = etree.SubElement(p, W + "pPr")
            p.insert(0, ppr)
        ps = etree.Element(W + "pStyle")
        ps.set(W + "val", "SpecNote")
        ppr.insert(0, ps)
        return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    data = _with_part(src, "word/document.xml", dangle)
    coord = {"paragraphs": {"text_regex": "^Coordinate"}}
    importing = {"op": "apply_style", "select": {"paragraphs": {"text_regex": "^NOTE TO"}},
                 "params": {"style": "Spec Note", "template": tpl}}
    frag = get_op("set_borders_shading").plan(DocxPackage(data), coord, {"shading": {"fill": "D9D9D9"}})
    assert (STYLES, "style:SpecNote", "verify:add") in frag.touches
    for other in ({"op": "set_borders_shading", "select": coord, "params": {"shading": {"fill": "D9D9D9"}}},
                  {"op": "set_properties", "select": coord, "params": {"run": {"b": True}}},
                  {"op": "set_tab_stops", "select": coord, "params": {"set": [{"pos": 720}]}}):
        assert "SpecNote" in _conflict(data, importing, other), other


def test_a_replaced_style_beside_set_properties_on_its_paragraphs_conflicts(src, tpl):
    """Review C4: existing 'replace' swaps Heading1's definition (the template's is italic) while set_properties finds
    the PART paragraphs already as asked (no italics) — it used to plan cleanly and fail V10 after apply."""
    applying = {"op": "apply_style", "select": {"paragraphs": {"text_regex": "^PART 2"}},
                "params": {"style": "Heading 1", "template": tpl, "existing": "replace"}}
    parts1 = {"paragraphs": {"text_regex": "^PART 1"}}
    assert "Heading1" in _conflict(src, applying, {"op": "set_properties", "select": parts1,
                                                   "params": {"run": {"i": False}}})
    mapping = {"op": "map_styles", "params": {"map": [{"from": "ArchLines", "to": "Heading 1"}], "template": tpl,
                                              "existing": "replace"}}
    _conflict(src, mapping, {"op": "set_tab_stops", "select": parts1, "params": {"set": [{"pos": 720}]}})


def test_set_properties_on_a_style_beside_set_properties_on_its_paragraphs_conflicts(src):
    """Review D5 (was open before batch 4): direct 'b off' on Arch Para paragraphs judges them by the effective value
    through ArchPara; 'b on' in the ArchPara definition beside it planned in both orders and failed V10. set_properties
    on a style now declares its own family (props) and the direct op reads it through the chains; a font change of the
    same style (set_style_font) still plans beside it — fonts are not set_properties' families."""
    paras = {"paragraphs": {"style_in": ["ArchPara"]}}
    direct = {"op": "set_properties", "select": paras, "params": {"run": {"b": False}}}
    _conflict(src, direct, {"op": "set_properties", "select": {"styles": {"ids": ["ArchPara"]}},
                            "params": {"run": {"b": True}}})
    _passes(src, direct, {"op": "set_style_font", "select": {"styles": {"ids": ["ArchPara"]}},
                          "params": {"font": "Arial"}})
    _passes(src, direct, {"op": "set_style_font", "select": {"styles": "all"}, "params": {"font": "Arial"}})


def test_mapping_and_applying_styles_whose_uses_meet_conflicts(src, tpl):
    """Review C3: the outcome depended on the order the ops happened to run in."""
    # a chain of mappings across two ops (in one op the pairs are refused as order-dependent already)
    _conflict(src, {"op": "map_styles", "params": {"map": [{"from": "ArchArticle", "to": "ArchPara"}]}},
              {"op": "map_styles", "params": {"map": [{"from": "ArchPara", "to": "ArchNote"}]}})
    # applying a style beside mapping it away
    _conflict(src, {"op": "apply_style", "select": {"paragraphs": {"text_regex": "^PART 2"}},
                    "params": {"style": "Heading1"}},
              {"op": "map_styles", "params": {"map": [{"from": "Heading 1", "to": "Heading 2"}]}})
    # applying a style beside an op selecting by it
    _conflict(src, {"op": "apply_style", "select": {"paragraphs": {"text_regex": "^PART 2"}},
                    "params": {"style": "Heading1"}},
              {"op": "apply_style", "select": {"paragraphs": {"style_in": ["Heading1"]}},
               "params": {"style": "Heading2"}})


def test_ops_whose_uses_commute_still_plan_and_verify(src):
    """Two mappings into one style, two applications of one style to different paragraphs: same file in either
    order."""
    _passes(src, {"op": "map_styles", "params": {"map": [{"from": "ArchLines", "to": "ArchPara"}]}},
            {"op": "map_styles", "params": {"map": [{"from": "ArchAuto", "to": "ArchPara"}]}})
    _passes(src, {"op": "apply_style", "select": {"paragraphs": {"text_regex": "^PART 2"}},
                  "params": {"style": "Heading1"}},
            {"op": "apply_style", "select": {"paragraphs": {"text_regex": "^Manual list"}},
             "params": {"style": "Heading1"}})


def test_a_selection_by_appearance_or_colour_beside_a_restyle_conflicts(src):
    """A selection by has_bold / is_all_caps / style_in / runs.color_in reads every paragraph's chain; a restyle anywhere
    in scope could change what it chooses. remove_hidden_text judges effective hidden through the chains too."""
    restyle = {"op": "apply_style", "select": {"paragraphs": {"text_regex": "^Manual list"}},
               "params": {"style": "ArchNote"}}
    plain = {"font": "Arial", "tracked": False}
    for other in ({"op": "set_font", "select": {"paragraphs": {"has_bold": True}}, "params": plain},
                  {"op": "set_font", "select": {"paragraphs": {"style_in": ["ArchPara"]}}, "params": plain},
                  {"op": "set_font", "select": {"runs": {"color_in": ["0000FF"]}}, "params": plain},
                  {"op": "remove_hidden_text", "params": {"tracked": False}}):
        mode = "untracked" if other["op"] == "remove_hidden_text" else "formatting"
        with pytest.raises(JobException) as e:
            plan_spec(DocxPackage(src), _spec_of(restyle, other, mode=mode))
        assert e.value.code == "conflict" and "of every target in it" in e.value.reason, other
    # a selection by text alone reads no chain: it plans beside the restyle
    _passes(src, restyle, {"op": "set_font", "select": {"paragraphs": {"text_regex": "^PART"}}, "params": plain})


def test_an_import_beside_set_style_font_on_every_style_conflicts(src, tpl):
    """Review C3: the imported CMT kept Times New Roman while every other style became Arial."""
    importing = {"op": "map_styles", "params": {"map": [{"from": "ArchLines", "to": "CMT"}], "template": tpl}}
    _conflict(src, importing, {"op": "set_style_font", "select": {"styles": "all"}, "params": {"font": "Arial"}})
    _conflict(src, importing, {"op": "set_style_font", "select": {"styles": {"types": ["paragraph"]}},
                               "params": {"font": "Arial"}})
    # by id, on styles the import neither adds nor replaces: one spec
    _passes(src, importing, {"op": "set_style_font", "select": {"styles": {"ids": ["Heading2"]}},
                             "params": {"font": "Arial"}})


def _spec_in_mode(*ops, mode="formatting"):
    return {"request": "r", "summary": "r", "assumptions": [], "questions": [], "applies_to": {"or": "all"},
            "mode": mode, "ops": list(ops)}


def test_removing_first_page_parts_beside_a_different_first_page_is_a_plan_time_conflict():
    """It used to plan and PASS with section 1's first page showing no header or footer at all, while set_page_setup's
    note said it showed header1 — which the same spec removed."""
    remove = {"op": "remove_header_footer", "select": {"sections": [0], "types": ["first"]}, "params": {}}
    first = {"op": "set_page_setup", "select": {"sections": [1]}, "params": {"different_first_page": True}}
    for ops in ((remove, first), (first, remove)):
        with pytest.raises(JobException) as e:
            plan_spec(DocxPackage(build_rich()), _spec_in_mode(*ops))
        assert e.value.code == "conflict" and "titlePg" in e.value.reason
    # alone, each still plans
    plan_spec(DocxPackage(build_rich()), _spec_in_mode(remove))
    plan_spec(DocxPackage(build_rich()), _spec_in_mode(first))


# ---- an op that verifies a part another op removes -------------------------------------------------------------------

ALL = ["document", "headers", "footers", "footnotes", "endnotes"]


def _spec_full(request, mode, ops, **extra):
    return {"request": request, "summary": request, "assumptions": [], "questions": [], "applies_to": {"or": "all"},
            "mode": mode, "ops": ops, **extra}


def test_font_change_over_headers_that_are_being_removed_is_refused_as_a_conflict():
    pkg = DocxPackage(build_rich())
    # the compiler's usual "change all fonts": direct-font runs only — the headers hold none, so the font op would
    # only VERIFY them; before the sweep that planned cleanly and failed V10 ("header1.xml missing from the output")
    ops = [{"op": "remove_header_footer", "select": {"sections": "all"}, "params": {}},
           {"op": "set_font", "select": {"scope": ALL, "runs": {"has_direct_font": True}}, "params": {"font": "Arial"}},
           {"op": "set_style_font", "select": {"styles": "all"}, "params": {"font": "Arial"}},
           {"op": "set_doc_defaults_font", "params": {"font": "Arial"}}]
    with pytest.raises(JobException) as e:
        plan_spec(pkg, _spec_full("remove all headers and footers and change all fonts to Arial", "formatting", ops))
    assert e.value.code == "conflict"
    assert "remove_header_footer" in e.value.reason and "set_font" in e.value.reason
    assert "would verify word/header1.xml without changing it" in e.value.reason and "narrow its scope" in e.value.reason
    # a font op that also changes runs in the removed part is the same conflict (the general wording)
    ops[1]["select"]["runs"] = "all"
    with pytest.raises(JobException) as e:
        plan_spec(pkg, _spec_full("x", "formatting", ops))
    assert e.value.code == "conflict" and "narrow their selectors" in e.value.reason
    # the same with the font change kept to the body plans, applies and verifies
    ops[1]["select"]["scope"] = ["document"]
    spec = _spec_full("remove all headers and footers and change all fonts to Arial", "formatting", ops)
    plan, frags = plan_spec(pkg, spec)
    out = apply_spec(pkg, frags).output_bytes()
    rec = run_suite(pkg.data, out, plan.to_dict(), "src.docx", "out.docx")
    assert rec.passed, rec.to_markdown()


def test_paragraph_props_over_a_removed_footer_is_refused_and_two_verifiers_of_one_part_are_not():
    pkg = DocxPackage(build_rich())
    with pytest.raises(JobException) as e:
        plan_spec(pkg, _spec_full("x", "formatting", [
            {"op": "remove_header_footer", "select": {"sections": "all", "kinds": ["footer"]}, "params": {}},
            {"op": "set_paragraph_props", "select": {"scope": ["footers"], "paragraphs": "all"}, "params": {"jc": "right"}}]))
    assert e.value.code == "conflict"
    # two ops that both verify word/document.xml and change different things in it merge as before
    plan, _ = plan_spec(pkg, _spec_full("x", "formatting", [
        {"op": "set_paragraph_props", "select": {"paragraphs": {"style_in": ["Heading1"]}}, "params": {"jc": "center"}},
        {"op": "set_font", "select": {"scope": ["document"], "paragraphs": {"style_in": ["SpecNote"]}, "runs": "all"},
         "params": {"font": "Arial"}}]))
    assert {o["op"] for o in plan.ops} == {"set_paragraph_props", "set_font"}
