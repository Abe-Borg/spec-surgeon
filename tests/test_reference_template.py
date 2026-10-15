"""A template SPEC (an ordinary spec from the architect, nothing highlighted, no {{placeholders}}) as the header/footer
template: ``slot_markers: ["reference"]``. Each fill rule is also run on the template spec itself; wherever its
headers/footers show that value, each discipline spec's own value goes. End to end on make_fixtures
.build_reference_spec() → build() targets, the refusals (a value in a field, a value left in another form, a value
shown nowhere…), the no-false-positive cases, and planted defects — wrong outputs AND wrong implementations shared by
plan and apply, each caught by the verifier's own locator."""
from __future__ import annotations

import copy
import io
import zipfile
from pathlib import Path

import pytest
from lxml import etree

from make_fixtures import REFERENCE_FILLS, build, build_reference_spec
from spec_surgeon.ooxml import block_full_text, paragraphs
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import apply_header_footer_template as tmpl_module
from spec_surgeon.ops import slots as tslots
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.verify import run_suite
from spec_surgeon.verify import template as vtemplate
from spec_surgeon.verify.reader import VPackage

OP = "apply_header_footer_template"
TITLE = "COMMON WORK RESULTS FOR FIRE SUPPRESSION"


@pytest.fixture(scope="module")
def target() -> bytes:
    return build("21 05 00", TITLE)


def write_template(tmp_path: Path, **kw) -> Path:
    p = tmp_path / "08 71 00 - DOOR HARDWARE (ACME).docx"
    p.write_bytes(build_reference_spec(**kw))
    return p


@pytest.fixture(scope="module")
def template(tmp_path_factory) -> Path:
    return write_template(tmp_path_factory.mktemp("reference"))


def params(tpl: Path, **kw) -> dict:
    d = {"template": str(tpl), "template_sections": {"all": 0}, "slot_fills": copy.deepcopy(REFERENCE_FILLS),
         "slot_markers": ["reference"]}
    d.update(kw)
    return d


def run(src: bytes, prm: dict, apply_with=None):
    pkg = DocxPackage(src)
    frag = get_op(OP).plan(pkg, {"sections": "all"}, prm)
    plan = merge_fragments([frag])
    work = pkg.copy()
    (apply_with or get_op(OP)).apply(work, frag)
    out = work.output_bytes()
    return plan, out, run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")


def plan_only(src: bytes, prm: dict):
    return get_op(OP).plan(DocxPackage(src), {"sections": "all"}, prm)


def failed(rec):
    return [c.id for c in rec.checks if c.status == "FAIL"]


def hf_parts(docx: bytes):
    names = [m.filename for m in DocxPackage(docx).members]
    return sorted(n for n in names if n.startswith(("word/header", "word/footer")) and n.endswith(".xml"))


def texts(docx: bytes, part: str):
    return [block_full_text(p) for p in paragraphs(DocxPackage(docx).xml(part))]


def run_shapes(docx: bytes, part: str):
    """Per paragraph: each run's content in order (rPr aside), as (local name, text)."""
    root = DocxPackage(docx).xml(part)
    return [[[(etree.QName(c).localname, c.text) for c in r if c.tag != W + "rPr"] for r in p.iter(W + "r")]
            for p in paragraphs(root)]


def mutate(docx: bytes, part: str, fn) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(docx))
    root = etree.fromstring(zin.read(part))
    fn(root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            z.writestr(info, serialize_xml(root) if info.filename == part else zin.read(info.filename))
    return buf.getvalue()


def new_part(plan, kind: str) -> str:
    names = [n for n in plan.members_added if n.startswith(f"word/{kind}")]
    assert len(names) == 1, plan.members_added
    return names[0]


# ---- end to end ------------------------------------------------------------------------------------

def test_the_architects_header_and_footer_carry_each_specs_own_number_and_title(template, target):
    plan, out, rec = run(target, params(template))
    assert rec.passed, rec.to_markdown()
    assert plan.mode == "formatting"                     # nothing typed: every fill is extracted from the spec
    header, footer = new_part(plan, "header"), new_part(plan, "footer")
    assert texts(out, header) == ["ACME ARCHITECTSAVC11 DATA CENTER – 100% CONSTRUCTION DOCUMENTS",
                                  f"SECTION 21 05 00 – {TITLE}"]
    assert texts(out, footer) == [f"{TITLE}21 05 00 -  PAGE 1"]
    # the firm/project line is the architect's, run for run; the footer's tab and PAGE field stay where they were
    assert run_shapes(out, header)[0] == [[("t", "ACME ARCHITECTS"), ("tab", None),
                                           ("t", "AVC11 DATA CENTER – 100% CONSTRUCTION DOCUMENTS")]]
    assert run_shapes(out, footer)[0] == [[("t", TITLE)], [("tab", None)], [("t", "21 05 00")], [("t", " - ")],
                                          [("fldChar", None)], [("instrText", " PAGE ")], [("fldChar", None)],
                                          [("t", "1")], [("fldChar", None)]]
    v10 = [a for a in plan.property_assertions if a["kind"] == "template_parts"]
    assert {tuple(sorted(a["template_values"].items())) for a in v10} == \
        {(("SECTION_NUMBER", "08 71 00"), ("SECTION_TITLE", "DOOR HARDWARE"))}
    note = next(n for n in plan.notes if n.startswith("template spec (reference slots)"))
    assert "«SECTION ⟦08 71 00⟧ – ⟦DOOR HARDWARE⟧»" in note and "«⟦DOOR HARDWARE⟧ ⟦08 71 00⟧ - 1»" in note
    assert not plan.flags


def test_every_spec_gets_its_own_values(template):
    for number, title in (("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"), ("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS")):
        src = build(number, title)
        plan, out, rec = run(src, params(template))
        assert rec.passed, rec.to_markdown()
        assert texts(out, new_part(plan, "header"))[1] == f"SECTION {number} – {title}"
        assert texts(out, new_part(plan, "footer")) == [f"{title}{number} -  PAGE 1"]


def test_title_written_in_another_case_is_found_and_flagged(tmp_path, target):
    tpl = write_template(tmp_path, header_title="Door Hardware")
    plan, out, rec = run(target, params(tpl))
    assert rec.passed, rec.to_markdown()
    assert texts(out, new_part(plan, "header"))[1] == f"SECTION 21 05 00 – {TITLE}"
    assert any("'Door Hardware'" in f for f in plan.flags)


def test_user_words_replace_named_template_text(tmp_path, target):
    """``in_template`` names the template's own words a literal fill replaces (the phase line, here): the op becomes
    untracked (the user's words) and the rest of the line stays the architect's."""
    tpl = write_template(tmp_path)
    fills = copy.deepcopy(REFERENCE_FILLS)
    fills["PHASE"] = {"rule": "literal", "value": "ISSUED FOR PERMIT", "in_template": "100% CONSTRUCTION DOCUMENTS"}
    plan, out, rec = run(target, params(tpl, slot_fills=fills))
    assert rec.passed, rec.to_markdown()
    assert plan.mode == "untracked"
    assert texts(out, new_part(plan, "header"))[0] == "ACME ARCHITECTSAVC11 DATA CENTER – ISSUED FOR PERMIT"


@pytest.mark.parametrize("extra", [
    "Tel (408) 710-0123",                 # the number's digits inside a phone number: not the number
    "Job 1087100-01, Sheet 2008710",      # nor inside longer numbers
    "HARDWARE CONSULTANT: DOORCO",        # the title's words, not the title
])
def test_look_alikes_are_neither_replaced_nor_refused(tmp_path, target, extra):
    tpl = write_template(tmp_path, extra_header_text=extra)
    plan, out, rec = run(target, params(tpl))
    assert rec.passed, rec.to_markdown()
    assert texts(out, new_part(plan, "header"))[2] == extra


# ---- refusals -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("extra", [
    "Project Manual Section 087100",      # written without spaces
    "See 08-71-00 for keying",            # hyphens
    "Section 08 71 00.01 applies",        # run into a longer number
    "SECTION08 71 00",                    # run into a word
    "DOOR HARDWARE/GLAZING",              # the title run into another word
])
def test_a_template_value_left_in_another_form_is_refused(tmp_path, target, extra):
    tpl = write_template(tmp_path, extra_header_text=extra)
    with pytest.raises(JobException) as e:
        plan_only(target, params(tpl))
    assert e.value.code == "template-value-left", e.value.reason
    assert "copied unchanged into every spec" in e.value.reason


def test_a_value_shown_by_a_field_is_refused(tmp_path, target):
    tpl = write_template(tmp_path, title_by_field=True)
    with pytest.raises(JobException) as e:
        plan_only(target, params(tpl))
    assert e.value.code == "slot-in-field"


@pytest.mark.parametrize("change, code", [
    (lambda f: f["SECTION_TITLE"].update(rule="literal", value="X"), "bad-params"),            # literal, no in_template
    (lambda f: f["SECTION_NUMBER"].update(in_template="  "), "bad-params"),
    (lambda f: f["SECTION_NUMBER"].update(in_template="08"), "template-slot"),                 # too short to locate
    (lambda f: f["SECTION_NUMBER"].update(pattern=r"^SECTION\s+(\d{6})\b"), "template-slot"),  # finds nothing there
    (lambda f: f.update(PART={"rule": "regex", "pattern": r"^(PART 1)"}), "unknown-slot"),     # shown nowhere
    (lambda f: f.update(LABEL={"rule": "literal", "value": "x", "in_template": "SECTION 08 71 00"}), "slot-overlap"),
])
def test_refusals(template, target, change, code):
    fills = copy.deepcopy(REFERENCE_FILLS)
    change(fills)
    with pytest.raises(JobException) as e:
        plan_only(target, params(template, slot_fills=fills))
    assert e.value.code == code, e.value.reason


def test_reference_markers_are_used_alone(template, target):
    with pytest.raises(JobException) as e:
        plan_only(target, params(template, slot_markers=["reference", "highlight"]))
    assert e.value.code == "bad-params"


def test_in_template_belongs_to_reference_slots(template, target):
    fills = copy.deepcopy(REFERENCE_FILLS)
    fills["SECTION_NUMBER"]["in_template"] = "08 71 00"
    with pytest.raises(JobException) as e:
        plan_only(target, params(template, slot_fills=fills, slot_markers=["highlight", "braces"]))
    assert e.value.code == "bad-params" and "in_template" in e.value.reason


# ---- the two locators agree --------------------------------------------------------------------------

def test_transform_and_verifier_locate_the_same_slots(tmp_path):
    values = {"SECTION_NUMBER": "08 71 00", "SECTION_TITLE": "DOOR HARDWARE"}
    seen = 0
    for kw in ({}, {"header_title": "Door Hardware"}, {"extra_header_text": "Tel (408) 710-0123"},
               {"extra_header_text": "x 08 71 00 (DOOR HARDWARE) y"}):
        data = build_reference_spec(**kw)
        tpkg, vpkg = DocxPackage(data), VPackage(data)
        for part in [n for n in hf_parts(data)]:
            tps, vps = paragraphs(tpkg.xml(part)), list(vpkg.xml(part).iter(W + "p"))
            for tp, vp in zip(tps, vps):
                assert tslots.located_text(tp) == vtemplate._v_located(vp)
                mine = [(s.name, s.start, s.end) for s in tslots.find_slots(tp, ["reference"], [], "x", values, set())]
                theirs = [(s["name"], s["start"], s["end"]) for s in vtemplate.slots_of(vp, ["reference"], [], values, set())]
                assert mine == theirs
                seen += len(mine)
    assert seen >= 4 * 4 + 2


# ---- planted defects ---------------------------------------------------------------------------------

def test_the_architects_number_left_in_the_output_is_caught(template, target):
    plan, out, rec = run(target, params(template))
    assert rec.passed
    footer = new_part(plan, "footer")

    def keep_theirs(root):
        t = [t for t in root.iter(W + "t") if t.text == "21 05 00"][0]
        t.text = "08 71 00"
    rec = run_suite(target, mutate(out, footer, keep_theirs), plan.to_dict(), "s", "o")
    assert "V10" in failed(rec)


def test_a_tab_moved_inside_its_run_is_caught(template, target):
    """Run content is compared in order — a tab moved within the firm/project run (same runs, same text, same
    children) is a different header."""
    plan, out, rec = run(target, params(template))
    header = new_part(plan, "header")

    def move_tab(root):
        r = [r for r in root.iter(W + "r") if r.find(W + "tab") is not None][0]
        tab = r.find(W + "tab")
        r.remove(tab)
        r.append(tab)
    rec = run_suite(target, mutate(out, header, move_tab), plan.to_dict(), "s", "o")
    assert failed(rec) == ["V10"] and "in order" in rec.get("V10").summary


def test_footer_occurrences_missed_by_plan_and_apply_alike_are_caught(template, target, monkeypatch):
    """The transform's locator misses the footer's values (as the first cut did next to a tab) in plan AND apply, so
    the architect's title and number are copied into every footer; V2 sees exactly the predicted text. The
    verifier's own locator finds them and V10 fails."""
    real = tmpl_module.find_slots
    monkeypatch.setattr(tmpl_module, "find_slots",
                        lambda p, m, c, where, **kw: [] if "footer" in where else real(p, m, c, where, **kw))
    plan, out, rec = run(target, params(template))
    assert texts(out, new_part(plan, "footer")) == ["DOOR HARDWARE08 71 00 -  PAGE 1"]
    assert rec.get("V2").status == "PASS"
    assert failed(rec) == ["V10"]


def test_a_wrong_template_value_shared_by_plan_and_apply_is_caught(template, target, monkeypatch):
    """The transform derives the title's template value wrongly ("HARDWARE"): it replaces half the title. V10
    derives the template values itself and disagrees."""
    real = tmpl_module.reference_values
    monkeypatch.setattr(tmpl_module, "reference_values",
                        lambda tpkg, fills: dict(real(tpkg, fills), SECTION_TITLE="HARDWARE"))
    plan, out, rec = run(target, params(template))
    assert texts(out, new_part(plan, "header"))[1] == f"SECTION 21 05 00 – DOOR {TITLE}"
    assert failed(rec) == ["V10"] and "template values derived here" in rec.get("V10").summary


def test_a_loose_boundary_shared_by_plan_and_apply_is_caught(tmp_path, target, monkeypatch):
    """With word boundaries ignored, "08 71 00" inside "08 71 00.01" is replaced (→ "21 05 00.01", a section that
    does not exist). The verifier's own boundary rule sees the template's number left there in another form."""
    tpl = write_template(tmp_path, extra_header_text="Section 08 71 00.01 applies")
    monkeypatch.setattr(tslots, "bounded", lambda text, a, b: True)
    plan, out, rec = run(target, params(tpl))
    assert texts(out, new_part(plan, "header"))[2] == "Section 21 05 00.01 applies"
    assert failed(rec) == ["V10"] and "another form" in rec.get("V10").summary


def test_a_carve_that_drops_the_tab_is_caught(template, target, monkeypatch):
    real = tslots.carve

    def lossy(p, start, end):
        out = real(p, start, end)
        for r in list(p.iter(W + "r")):
            if r.find(W + "tab") is not None and r.find(W + "t") is None:
                r.getparent().remove(r)
        return out
    monkeypatch.setattr(tslots, "carve", lossy)
    plan, out, rec = run(target, params(template))
    assert "V10" in failed(rec)


def test_runs_with_several_text_elements_are_cut_as_modelled(tmp_path, target):
    """A template run holding w:t, w:t, w:tab, w:t, w:t (rsid churn, hand-edited XML): the slots are cut out of it
    and everything else stays in order — the verifier models the same cut."""
    tpl = write_template(tmp_path)
    data = tpl.read_bytes()

    def churn(root):
        p = paragraphs(root)[0]
        for r in list(p.iter(W + "r")):
            p.remove(r)
        r = etree.SubElement(p, W + "r")
        for kind, text in (("t", "DOOR "), ("t", "HARDWARE"), ("tab", None), ("t", "08 71 00"), ("t", " - ")):
            el = etree.SubElement(r, W + kind)
            if text is not None:
                el.text = text
                el.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    tpl.write_bytes(mutate(data, "word/footer1.xml", churn))
    plan, out, rec = run(target, params(tpl))
    assert rec.passed, rec.to_markdown()
    assert run_shapes(out, new_part(plan, "footer"))[0] == [[("t", TITLE)], [("tab", None)], [("t", "21 05 00")],
                                                            [("t", " - ")]]
