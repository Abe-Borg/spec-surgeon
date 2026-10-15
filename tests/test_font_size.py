"""Font size in the three font ops (set_font, set_style_font, set_doc_defaults_font) and in the effective_font
post-condition: "match the template's font: Arial 10" on make_fixtures.build_sized() (9 pt text, 8 pt headers, a 12 pt
note, symbol runs with sizes). Size goes only where a level sets one (``size_from``: only those sizes); the verifier
re-derives every target and checks "w:sz and nothing else"; planted defects — wrong outputs AND wrong implementations
shared by plan and apply — are caught."""
from __future__ import annotations

import io
import zipfile
from typing import List, Tuple

import pytest
from lxml import etree

from make_fixtures import build_sized
from spec_surgeon.editspec import SpecError, validate_spec
from spec_surgeon.ooxml import own_size
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import selectors as sel_module
from spec_surgeon.ops import set_font as set_font_module
from spec_surgeon.ops import set_style_font as set_style_font_module
from spec_surgeon.ops.selectors import DEFAULT_PRESERVE_FONTS, size_applies, size_values
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.verify import props as vprops
from spec_surgeon.verify import run_suite

EVERYWHERE = ["document", "headers", "footers"]
MATCH = {"font": "Arial", "size": 10, "size_from": [9]}
POST = {"kind": "effective_font", "scope": EVERYWHERE, "slots": ["ascii", "hAnsi", "cs"], "value": "Arial",
        "size": 10, "size_from": [9], "preserve_fonts": DEFAULT_PRESERVE_FONTS, "include_paragraph_marks": True}


def match_ops(**font_kw) -> List[Tuple[str, dict, dict]]:
    """The composition the compiler writes for "match the template spec's font (Arial 10)" on a 9 pt spec."""
    return [("set_font", {"scope": EVERYWHERE, "runs": "all"}, {**MATCH, "only_where_defined": True, **font_kw}),
            ("set_style_font", {"styles": "all"}, dict(MATCH)),
            ("set_doc_defaults_font", {}, dict(MATCH))]


@pytest.fixture(scope="module")
def sized() -> bytes:
    return build_sized()


def run_ops(src: bytes, specs, extra_assertions=()):
    pkg = DocxPackage(src)
    frags = [get_op(o).plan(pkg, s, p) for o, s, p in specs]
    plan = merge_fragments(frags)
    work = pkg.copy()
    for f in frags:
        get_op(f.op).apply(work, f)
    out = work.output_bytes()
    d = plan.to_dict()
    d["property_assertions"] += list(extra_assertions)
    return plan, out, run_suite(src, out, d, "src.docx", "out.docx")


def failed(rec):
    return [c.id for c in rec.checks if c.status == "FAIL"]


def mutate(docx: bytes, part: str, fn) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(docx))
    root = etree.fromstring(zin.read(part))
    fn(root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            z.writestr(info, serialize_xml(root) if info.filename == part else zin.read(info.filename))
    return buf.getvalue()


def run_with_text(root, needle):
    return [r for r in root.iter(W + "r") if any(needle in (t.text or "") for t in r.iter(W + "t"))][0]


def sz(el) -> int:
    return own_size(el.find(W + "rPr") if el.tag == W + "r" else el)


def styles_of(docx: bytes):
    root = DocxPackage(docx).xml("word/styles.xml")
    return root, {s.get(W + "styleId"): s for s in root.findall(W + "style")}


def header_part(docx: bytes) -> str:
    return [m.filename for m in DocxPackage(docx).members if m.filename.startswith("word/header")][0]


# ---- the composition --------------------------------------------------------------------------------

def test_matching_the_template_font_turns_the_9_pt_text_10_pt_and_leaves_the_rest(sized):
    plan, out, rec = run_ops(sized, match_ops(), extra_assertions=[POST])
    assert rec.passed, rec.to_markdown()
    assert plan.mode == "formatting" and plan.geometry_reference == "none"
    root, st = styles_of(out)
    assert own_size(root.find(f"{W}docDefaults/{W}rPrDefault/{W}rPr")) == 20
    for sid, want in (("Normal", 20), ("Heading1", 20), ("Caption", 20), ("Header", 16), ("Footer", 16),
                      ("SpecNote", 16), ("Title", 52), ("BulletSymbol", 18)):           # symbol style left alone
        assert own_size(st[sid].find(W + "rPr")) == want, sid
    doc = DocxPackage(out).xml("word/document.xml")
    ul = run_with_text(doc, "UL listed")
    assert sz(ul) == 20 and ul.find(f"{W}rPr/{W}szCs").get(W + "val") == "18"       # complex-script size left alone
    assert ul.find(f"{W}rPr/{W}rFonts").get(W + "ascii") == "Arial"
    pasted = run_with_text(doc, "pasted at 9 pt")
    assert sz(pasted) == 20 and pasted.find(f"{W}rPr/{W}rFonts") is None             # no direct font added
    assert sz(run_with_text(doc, "deliberately 12 pt")) == 24
    assert sz(run_with_text(doc, "§")) == 18                                          # direct dingbat
    assert sz([r for r in doc.iter(W + "r") if r.find(W + "sym") is not None][0]) == 18
    mark = [p for p in doc.iter(W + "p") if "Paragraph mark" in "".join(t.text or "" for t in p.iter(W + "t"))][0]
    assert own_size(mark.find(f"{W}pPr/{W}rPr")) == 20
    hdr = DocxPackage(out).xml(header_part(out))
    assert sz(run_with_text(hdr, "SECTION")) == 16                                    # the 8 pt header stays 8 pt
    assert "size 10 pt" in rec.get("V10").summary
    notes = " ".join(plan.notes)
    assert "size 10 pt on the" in notes and "docDefaults size 9 pt → 10 pt" in notes


def test_size_only_selects_only_runs_that_set_their_own(sized):
    plan, out, rec = run_ops(sized, [("set_font", {"scope": EVERYWHERE, "runs": "all"}, {"size": 10})])
    assert rec.passed, rec.to_markdown()
    a = plan.property_assertions[0]
    assert a["set"] == {} and (a["expected_runs"], a["expected_marks"], a["expected_sized"]) == (4, 1, 5)
    #                                                    UL, pasted, 12 pt, header run; the 9 pt mark
    doc = DocxPackage(out).xml("word/document.xml")
    assert sz(run_with_text(doc, "deliberately 12 pt")) == 20                         # no size_from: every own size
    assert sz(run_with_text(doc, "§")) == 18                                          # symbol runs never
    assert run_with_text(doc, "pasted at 9 pt").find(f"{W}rPr/{W}rFonts") is None
    assert run_with_text(doc, "Plain text").find(W + "rPr") is None                    # inherits: untouched


def test_style_size_follows_only_where_defined(sized):
    plan, out, rec = run_ops(sized, [("set_style_font", {"styles": {"ids": ["Normal", "Quote", "SpecNote"]}},
                                      {"size": 10, "only_where_defined": False})])
    assert rec.passed, rec.to_markdown()
    _, st = styles_of(out)
    assert [own_size(st[s].find(W + "rPr")) for s in ("Normal", "Quote", "SpecNote")] == [20, 20, 20]
    plan, out, rec = run_ops(sized, [("set_style_font", {"styles": {"ids": ["Normal", "Quote", "SpecNote"]}},
                                      {"size": 10})])
    assert rec.passed, rec.to_markdown()
    _, st = styles_of(out)
    assert [own_size(st[s].find(W + "rPr")) for s in ("Normal", "Quote", "SpecNote")] == [20, None, 20]
    assert plan.property_assertions[0]["expected_ids"] == ["Normal", "SpecNote"]


def test_doc_defaults_size_not_listed_is_left_alone(sized):
    plan, out, rec = run_ops(sized, [("set_doc_defaults_font", {}, {"size": 12, "size_from": [11]})])
    assert rec.passed, rec.to_markdown()
    assert plan.intended_parts == []
    assert DocxPackage(out).read("word/styles.xml") == DocxPackage(sized).read("word/styles.xml")
    assert any("not one of" in n for n in plan.notes)


@pytest.mark.parametrize("params", [
    {"size": 9.25}, {"size": 0}, {"size": "10"}, {"size": True}, {"size": 2000},
    {"size": 10, "size_from": []}, {"size": 10, "size_from": [9.3]}, {"size_from": [9]}, {},
])
def test_bad_size_params_are_refused(sized, params):
    with pytest.raises(JobException) as e:
        get_op("set_font").plan(DocxPackage(sized), {"runs": "all"}, params)
    assert e.value.code == "bad-params"


def test_the_post_condition_validates_its_size():
    base = {"request": "r", "summary": "s", "mode": "formatting",
            "ops": [{"op": "set_doc_defaults_font", "params": {"size": 10}}]}
    validate_spec({**base, "assertions": [{"kind": "effective_font", "size": 10}]})
    for bad in ({"kind": "effective_font"}, {"kind": "effective_font", "size": 10.2},
                {"kind": "effective_font", "value": "", "size": 10}):
        with pytest.raises(SpecError):
            validate_spec({**base, "assertions": [bad]})


# ---- the two sides agree ----------------------------------------------------------------------------

def test_transform_and_verifier_size_rules_agree():
    for own in (None, 16, 18, 20, 24):
        for size in (None, {"sz": 20, "from": None}, {"sz": 20, "from": [18]}, {"sz": 20, "from": [16, 24]}):
            for add in (False, True):
                want = size["sz"] if size_applies(own, size, add) else None
                assert vprops._v_size_to(own, size, add) == want, (own, size, add)
    for val, want in (("18", 18), ("18.5", None), ("", None), ("-2", None)):
        rpr = etree.fromstring(f'<w:rPr xmlns:w="{W[1:-1]}"><w:sz w:val="{val}"/></w:rPr>')
        assert own_size(rpr) == vprops._v_own_sz(rpr) == want
    assert size_values({"size": 10.5, "size_from": [9, 9]}) == {"sz": 21, "from": [18]}


# ---- planted defects ---------------------------------------------------------------------------------

def test_a_size_outside_size_from_changed_in_the_output_is_caught(sized):
    plan, out, rec = run_ops(sized, match_ops())
    assert rec.passed

    def flatten(root):
        run_with_text(root, "deliberately 12 pt").find(f"{W}rPr/{W}sz").set(W + "val", "20")
    rec = run_suite(sized, mutate(out, "word/document.xml", flatten), plan.to_dict(), "s", "o")
    assert failed(rec) == ["V10"]


def test_cs_size_changed_along_is_caught(sized):
    plan, out, rec = run_ops(sized, match_ops())

    def cs_too(root):
        run_with_text(root, "UL listed").find(f"{W}rPr/{W}szCs").set(W + "val", "20")
    rec = run_suite(sized, mutate(out, "word/document.xml", cs_too), plan.to_dict(), "s", "o")
    assert failed(rec) == ["V10"] and "other than rFonts / w:sz" in str(rec.get("V10").data["errors"])


def test_a_header_style_resized_is_caught(sized):
    plan, out, rec = run_ops(sized, match_ops())

    def header_10(root):
        for s in root.findall(W + "style"):
            if s.get(W + "styleId") == "Header":
                s.find(f"{W}rPr/{W}sz").set(W + "val", "20")
    rec = run_suite(sized, mutate(out, "word/styles.xml", header_10), plan.to_dict(), "s", "o")
    assert failed(rec) == ["V10"]


def test_size_from_ignored_by_plan_and_apply_alike_is_caught(sized, monkeypatch):
    """The transform's size rule forgets ``size_from`` in plan AND apply (so the plan predicts the flattening of
    the 12 pt note and the 8 pt header). V10 applies its own rule: the counts and the sizes disagree."""
    monkeypatch.setattr(set_font_module, "size_applies", lambda own, size, add=False: own is not None and size is not None)
    plan, out, rec = run_ops(sized, match_ops())
    assert sz(run_with_text(DocxPackage(out).xml("word/document.xml"), "deliberately 12 pt")) == 20
    assert rec.get("V2").status == "PASS"
    assert failed(rec) == ["V10"] and "plan predicted" in rec.get("V10").summary


def test_sizes_added_to_styles_by_plan_and_apply_alike_are_caught(sized, monkeypatch):
    real = set_style_font_module.style_changes
    monkeypatch.setattr(set_style_font_module, "style_changes",
                        lambda st, values, size, only: real(st, values, size, False))
    plan, out, rec = run_ops(sized, [("set_style_font", {"styles": "all"}, {"size": 10})])
    assert failed(rec) == ["V10"] and "predicted" in rec.get("V10").summary


def test_the_post_condition_catches_an_incomplete_composition(sized):
    """Without set_font the runs that set their own 9 pt stay 9 pt: every op passes its own check, the effective-size
    post-condition does not."""
    _, _, rec = run_ops(sized, match_ops()[1:], extra_assertions=[POST])
    assert failed(rec) == ["V10"] and "size: 18→18" in str(rec.get("V10").data["errors"])
    post = dict(POST, size_from=None)                     # and "everything 10 pt" is not what the ops did
    _, _, rec = run_ops(sized, match_ops(), extra_assertions=[post])
    assert failed(rec) == ["V10"] and "size: 24→24" in str(rec.get("V10").data["errors"])
