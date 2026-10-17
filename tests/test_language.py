"""set_language and V10's ``language`` kind (built-in batch 4, item 29 — docs/BUILTIN_OPS_BATCH4_DESIGN.md §4): end to
end on the architect's spec (every level that names a language plus the document default, replace_only, East Asian and
complex-script languages, a created docDefaults, already as asked); the effective-language post-condition; a wrong
OUTPUT per rule; wrong IMPLEMENTATIONS shared by plan and apply; conflicts; parameters and refusals."""
from __future__ import annotations

import ast
import copy
import io
import zipfile
from pathlib import Path
from typing import List, Tuple

import pytest
from lxml import etree

from make_fixtures import build, build_styles
from spec_surgeon.editspec import load_spec, plan_spec, validate_spec
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import set_language as lang_module
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.report import FAILURE_EXPLANATIONS
from spec_surgeon.verify import language as vlang
from spec_surgeon.verify import props as vprops
from spec_surgeon.verify import run_suite
from spec_surgeon.verify.reader import STYLES_REL, VPackage

MAIN = "word/document.xml"
STYLES = "word/styles.xml"
OP = "set_language"
HF_SPEC = Path(__file__).resolve().parent.parent / "examples" / "specs" / "match-template-spec-header-footer.json"


@pytest.fixture(scope="module")
def src() -> bytes:
    return build_styles()


def run_ops(data: bytes, specs: List[Tuple[str, dict, dict]]):
    pkg = DocxPackage(data)
    frags = [get_op(o).plan(pkg, sel, par) for o, sel, par in specs]
    plan = merge_fragments(frags)
    work = pkg.copy()
    for f in frags:
        get_op(f.op).apply(work, f)
    out = work.output_bytes()
    return plan, out, run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")


def lang(data: bytes, params: dict):
    return run_ops(data, [(OP, {}, params)])


def failed(rec):
    return [c.id for c in rec.checks if c.status == "FAIL"]


def v10(rec) -> str:
    c = next(c for c in rec.checks if c.id == "V10")
    return c.summary + " | " + " | ".join(c.data.get("errors", []))


def root_of(data: bytes, part: str = MAIN):
    return DocxPackage(data).xml(part)


def run_of(root, needle):
    for r in root.iter(W + "r"):
        if "".join(t.text or "" for t in r.findall(W + "t")) == needle:
            return r
    raise AssertionError(needle)


def para(root, needle):
    return [p for p in root.iter(W + "p") if "".join(t.text or "" for t in p.iter(W + "t")).startswith(needle)][0]


def style(root, sid):
    return next(s for s in root.iter(W + "style") if s.get(W + "styleId") == sid)


def lang_attrs(rpr):
    el = rpr.find(W + "lang") if rpr is not None else None
    return None if el is None else {etree.QName(k).localname: v for k, v in el.attrib.items()}


def dd_rpr(root):
    return root.find(f"{W}docDefaults/{W}rPrDefault/{W}rPr")


def mutate(docx: bytes, part: str, fn) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(docx))
    root = etree.fromstring(zin.read(part))
    fn(root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            z.writestr(info, serialize_xml(root) if info.filename == part else zin.read(info.filename))
    return buf.getvalue()


def _caught(src, plan, out, part, fn, needle=None):
    bad = mutate(out, part, fn)
    rec = run_suite(src, bad, plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec), rec.to_markdown()
    if needle:
        assert needle in v10(rec), v10(rec)


# ---- end to end ----------------------------------------------------------------------------------------------------

def test_every_level_that_names_a_language_and_the_default(src):
    plan, out, rec = lang(src, {"language": "en-US"})
    assert rec.passed, rec.to_markdown()
    a = plan.property_assertions[0]
    assert (a["expected_runs"], a["expected_marks"]) == (3, 1)
    assert a["expected"]["styles"] == {"Heading1": {"val": "en-US"}} and a["expected"]["doc_defaults"] == {"val": "en-US"}
    assert plan.mode == "formatting" and plan.geometry_reference == "source" and not plan.diffs
    assert "rPrChange" not in plan.rev_delta
    root, styles = root_of(out), root_of(out, STYLES)
    assert lang_attrs(run_of(root, "SECTION 21 13 13").find(W + "rPr")) == {"val": "en-US"}
    assert lang_attrs(run_of(root, " voir annexe").find(W + "rPr")) == {"val": "en-US"}
    assert lang_attrs(run_of(root, " colour").find(W + "rPr")) == {"val": "en-US"}
    assert lang_attrs(para(root, "A.").find(W + "pPr/" + W + "rPr")) == {"val": "en-US"}
    assert lang_attrs(style(styles, "Heading1").find(W + "rPr")) == {"val": "en-US"}
    assert lang_attrs(dd_rpr(styles)) == {"val": "en-US", "eastAsia": "en-US", "bidi": "ar-SA"}   # the others stay
    # a run that names no language inherits it, untouched
    assert run_of(root, "WET-PIPE SPRINKLER SYSTEMS").find(W + "rPr/" + W + "lang") is None
    assert set(plan.intended_parts) == {MAIN, STYLES}
    assert (STYLES, "*", "verify") in get_op(OP).plan(DocxPackage(src), {}, {"language": "en-US"}).touches


def test_replace_only_changes_the_listed_languages(src):
    plan, out, rec = lang(src, {"language": "en-US", "replace_only": ["en-gb"]})        # casefolded
    assert rec.passed, rec.to_markdown()
    root = root_of(out)
    assert lang_attrs(run_of(root, " voir annexe").find(W + "rPr")) == {"val": "fr-FR"}   # French stays French
    assert lang_attrs(run_of(root, " colour").find(W + "rPr")) == {"val": "en-US"}
    assert lang_attrs(dd_rpr(root_of(out, STYLES)))["val"] == "en-US"
    plan, out, rec = lang(src, {"language": "de-DE", "replace_only": ["fr-FR"]})
    assert rec.passed, rec.to_markdown()
    a = plan.property_assertions[0]
    assert (a["expected_runs"], a["expected_marks"], a["expected"]["doc_defaults"], a["expected"]["styles"]) == \
        (1, 0, {}, {})
    assert lang_attrs(run_of(root_of(out), " voir annexe").find(W + "rPr")) == {"val": "de-DE"}
    assert plan.intended_parts == [MAIN]


def test_east_asian_and_complex_script_languages(src):
    plan, out, rec = lang(src, {"east_asian": "ja-JP", "complex_script": "he-IL"})
    assert rec.passed, rec.to_markdown()
    assert lang_attrs(dd_rpr(root_of(out, STYLES))) == {"val": "en-GB", "eastAsia": "ja-JP", "bidi": "he-IL"}
    a = plan.property_assertions[0]
    assert (a["expected_runs"], a["expected_marks"], a["expected"]["styles"]) == (0, 0, {})
    assert plan.geometry_reference == "none"                 # another script's font may be chosen


def test_automatic_hyphenation_makes_the_layout_reference_none(src):
    def hyph(root):
        root.insert(0, etree.Element(W + "autoHyphenation"))
    data = mutate(src, "word/settings.xml", hyph)
    plan, out, rec = lang(data, {"language": "en-US"})
    assert rec.passed and plan.geometry_reference == "none"
    off = mutate(src, "word/settings.xml", lambda r: r.insert(0, etree.Element(W + "autoHyphenation", {W + "val": "0"})))
    assert get_op(OP).plan(DocxPackage(off), {}, {"language": "en-US"}).geometry_reference == "source"


def test_a_missing_document_default_is_created_in_schema_order(src):
    no_dd = mutate(src, STYLES, lambda r: r.remove(r.find(W + "docDefaults")))
    plan, out, rec = lang(no_dd, {"language": "en-US"})
    assert rec.passed, rec.to_markdown()
    styles = root_of(out, STYLES)
    assert styles[0].tag == W + "docDefaults" and lang_attrs(dd_rpr(styles)) == {"val": "en-US"}
    assert any("created in schema order" in n for n in plan.notes)

    no_rpd = mutate(src, STYLES, lambda r: r.find(W + "docDefaults").remove(r.find(f"{W}docDefaults/{W}rPrDefault")))
    plan, out, rec = lang(no_rpd, {"language": "en-US"})
    assert rec.passed, rec.to_markdown()
    dd = root_of(out, STYLES).find(W + "docDefaults")
    assert [etree.QName(c).localname for c in dd] == ["rPrDefault", "pPrDefault"]

    def no_lang(r):
        rpr = dd_rpr(r)
        rpr.remove(rpr.find(W + "lang"))
    data = mutate(src, STYLES, no_lang)
    plan, out, rec = lang(data, {"language": "en-US"})
    assert rec.passed, rec.to_markdown()
    assert [etree.QName(c).localname for c in dd_rpr(root_of(out, STYLES))] == ["rFonts", "sz", "szCs", "lang"]
    # with replace_only an absent default is left alone (nothing is listed there)
    plan, out, rec = lang(data, {"language": "en-US", "replace_only": ["en-GB"]})
    assert rec.passed and plan.property_assertions[0]["expected"]["doc_defaults"] == {}


def test_already_as_asked_changes_nothing(src):
    plan, out, rec = lang(src, {"language": "en-US"})
    plan2, out2, rec2 = lang(out, {"language": "en-US"})
    assert rec2.passed, rec2.to_markdown()
    assert plan2.intended_parts == [] and any("nothing to change" in n for n in plan2.notes)


def test_parameters_and_refusals(src):
    op = get_op(OP)
    pkg = DocxPackage(src)
    for params in ({}, {"language": "English"}, {"language": "en_US"}, {"language": "en-US\n"},
                   {"language": "en-US", "tracked": True}, {"language": "en-US", "replace_only": []},
                   {"language": "en-US", "replace_only": "en-GB"}, {"language": "en-US", "replace_only": ["british"]},
                   {"language": "en-US", "author": "x"}, {"language": 7}):
        with pytest.raises(JobException) as e:
            op.plan(pkg, {}, params)
        assert e.value.code == "bad-params", params
    with pytest.raises(JobException) as e:
        op.plan(pkg, {"scope": ["document"]}, {"language": "en-US"})
    assert e.value.code == "bad-params"
    assert op.plan(pkg, {}, {"language": "en-US", "tracked": False}).property_assertions

    def no_styles(root):
        for rel in list(root):
            if rel.get("Type", "").endswith("/styles"):
                root.remove(rel)
    data = mutate(src, "word/_rels/document.xml.rels", no_styles)
    with pytest.raises(JobException) as e:
        op.plan(DocxPackage(data), {}, {"language": "en-US"})
    assert e.value.code == "structure"
    spec = {"request": "r", "summary": "s", "mode": "formatting",
            "ops": [{"op": OP, "select": {}, "params": {"language": "en-US", "lang": "x"}}]}
    with pytest.raises(Exception) as e:
        validate_spec(spec)
    assert "lang" in str(e.value)


# ---- the effective language --------------------------------------------------------------------------------------

def test_the_effective_language_follows_the_inheritance_chain(src):
    vpkg = VPackage(src)
    styles = vprops.Styles(vpkg.xml(vpkg.related(STYLES_REL)))
    root = vpkg.xml(MAIN)

    def eff(needle, attr="val"):
        r = run_of(root, needle)
        p = r.getparent()
        while p.tag != W + "p":
            p = p.getparent()
        return vlang.v_effective_lang(r.find(W + "rPr"), p, styles, attr)
    assert eff("PART 1 - GENERAL") == "en-GB"            # the Heading 1 style
    assert eff(" (general)") == "en-GB"                  # Heading1Char names none: the paragraph style decides
    assert eff(" voir annexe") == "fr-FR"                # direct
    assert eff("WET-PIPE SPRINKLER SYSTEMS") == "en-GB"  # docDefaults
    assert eff("WET-PIPE SPRINKLER SYSTEMS", "eastAsia") == "en-US"
    assert eff("175 psi") == "en-GB"                      # a table cell: docDefaults through the table style chain


# ---- wrong outputs ---------------------------------------------------------------------------------------------------

def test_wrong_outputs_are_caught(src):
    plan, out, rec = lang(src, {"language": "en-US"})
    assert rec.passed
    # a run's language left as it was
    _caught(src, plan, out, MAIN, lambda r: run_of(r, " colour").find(W + "rPr/" + W + "lang").set(W + "val", "en-GB"),
            "expected")
    # a style's language not changed
    _caught(src, plan, out, STYLES, lambda r: style(r, "Heading1").find(W + "rPr/" + W + "lang").set(W + "val", "en-GB"),
            "style Heading1")
    # the default's other attributes dropped
    _caught(src, plan, out, STYLES, lambda r: dd_rpr(r).find(W + "lang").attrib.pop(W + "eastAsia"), "eastAsia")
    # something else in a changed run's properties
    _caught(src, plan, out, MAIN, lambda r: run_of(r, "SECTION 21 13 13").find(W + "rPr").remove(
        run_of(r, "SECTION 21 13 13").find(W + "rPr/" + W + "b")), "other than w:lang")
    # a language written where none was (a run that inherits)
    _caught(src, plan, out, MAIN, lambda r: run_of(r, "WET-PIPE SPRINKLER SYSTEMS").find(W + "rPr").append(
        etree.Element(W + "lang", {W + "val": "en-US"})))
    # the mark left alone
    _caught(src, plan, out, MAIN, lambda r: para(r, "A.").find(W + "pPr/" + W + "rPr/" + W + "lang").set(W + "val", "en-GB"))

    no_dd = mutate(src, STYLES, lambda r: r.remove(r.find(W + "docDefaults")))
    plan, out, rec = lang(no_dd, {"language": "en-US"})
    assert rec.passed
    _caught(no_dd, plan, out, STYLES, lambda r: r.append(r.find(W + "docDefaults")), "first element")

    def no_lang(r):
        rpr = dd_rpr(r)
        rpr.remove(rpr.find(W + "lang"))
    data = mutate(src, STYLES, no_lang)
    plan, out, rec = lang(data, {"language": "en-US"})
    assert rec.passed
    _caught(data, plan, out, STYLES, lambda r: dd_rpr(r).insert(0, dd_rpr(r).find(W + "lang")), "out of place")

    plan, out, rec = lang(src, {"language": "en-US", "replace_only": ["en-GB"]})
    assert rec.passed
    # a listed language left on a run: the post-condition and the level check both see it
    _caught(src, plan, out, MAIN, lambda r: run_of(r, " colour").find(W + "rPr/" + W + "lang").set(W + "val", "en-GB"),
            "listed in replace_only")


# ---- wrong implementations shared by plan and apply -------------------------------------------------------------------

def test_a_shared_rule_that_skips_the_document_default_is_caught(src, monkeypatch):
    real = lang_module.language_levels
    monkeypatch.setattr(lang_module, "language_levels", lambda pkg, sheet: [lv for lv in real(pkg, sheet)
                                                                            if lv[0] != "docDefaults"])
    plan, out, rec = lang(src, {"language": "en-US"})
    assert plan.property_assertions[0]["expected"]["doc_defaults"] == {}
    assert "V10" in failed(rec) and "resolves to 'en-GB'" in v10(rec)       # the inheriting runs did not change


def test_a_shared_rule_that_skips_the_styles_is_caught(src, monkeypatch):
    real = lang_module.language_levels
    monkeypatch.setattr(lang_module, "language_levels", lambda pkg, sheet: [lv for lv in real(pkg, sheet)
                                                                            if lv[0] != "style"])
    plan, out, rec = lang(src, {"language": "en-US"})
    assert lang_attrs(style(root_of(out, STYLES), "Heading1").find(W + "rPr")) == {"val": "en-GB"}
    errors = v10(rec)
    assert "V10" in failed(rec) and "not the plan's" in errors
    assert "block 2 run 0: w:lang val resolves to 'en-GB'" in errors           # PART 1, in Heading 1: the post-condition


def test_a_shared_replace_only_that_is_case_sensitive_is_caught(src, monkeypatch):
    def case_sensitive(rpr, asked, listed, doc_defaults=False):
        el = rpr.find(W + "lang") if rpr is not None else None
        out = {}
        for attr, value in asked.items():
            cur = el.get(W + attr) if el is not None else None
            if cur is not None and cur != value and (listed is None or cur in listed):     # no casefold
                out[attr] = value
            elif cur is None and doc_defaults and listed is None:
                out[attr] = value
        return out
    monkeypatch.setattr(lang_module, "level_gets", case_sensitive)
    plan, out, rec = lang(src, {"language": "en-US", "replace_only": ["en-GB"]})
    assert plan.intended_parts == []                                         # "en-GB" never equals "en-gb"
    assert "V10" in failed(rec) and "listed in replace_only" in v10(rec)


def test_a_shared_writer_that_drops_the_other_attributes_is_caught(src, monkeypatch):
    def replacing(rpr, gets):
        old = rpr.find(W + "lang")
        if old is not None:
            rpr.remove(old)
        el = lang_module.get_or_add(rpr, "lang", lang_module.RPR_ORDER)
        for attr, v in gets.items():
            el.set(W + attr, v)
    monkeypatch.setattr(lang_module, "write_lang", replacing)
    plan, out, rec = lang(src, {"language": "en-US"})
    assert "V10" in failed(rec) and "eastAsia" in v10(rec)


# ---- conflicts -------------------------------------------------------------------------------------------------------

def _spec(*ops):
    return {"request": "r", "summary": "s", "mode": "formatting", "ops": list(ops)}


def test_a_language_change_beside_other_run_property_ops_conflicts(src):
    lang_op = {"op": OP, "select": {}, "params": {"language": "en-US"}}
    sel = {"paragraphs": {"text_regex": "^SECTION"}}
    for other in ({"op": "set_font", "select": sel, "params": {"font": "Arial"}},
                  {"op": "clear_direct_formatting", "select": sel, "params": {}},
                  {"op": "set_properties", "select": sel, "params": {"run": {"i": True}}},
                  {"op": "set_style_font", "select": {"styles": {"ids": ["ArchNote"]}},
                   "params": {"font": "Arial", "only_where_defined": False}},
                  {"op": "set_properties", "select": {"styles": {"ids": ["ArchNote"]}}, "params": {"run": {"b": True}}}):
        with pytest.raises(JobException) as e:
            plan_spec(DocxPackage(src), _spec(lang_op, other))
        assert e.value.code == "conflict", other
    # paragraph properties of the same paragraphs: another family, verified together
    plan, frags = plan_spec(DocxPackage(src), _spec(lang_op, {"op": "set_paragraph_props", "select": sel,
                                                              "params": {"spacing": {"after": 120}}}))
    work = DocxPackage(src).copy()
    for f in frags:
        get_op(f.op).apply(work, f)
    rec = run_suite(src, work.output_bytes(), plan.to_dict(), "src.docx", "out.docx")
    assert rec.passed, rec.to_markdown()


def test_a_language_change_beside_the_templating_op_conflicts():
    """The templating op imports styles and reads styles.xml whole; the language op changes styles and its
    post-condition reads every style chain — one spec would let either change reach the other unverified."""
    data = build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS")
    hf = copy.deepcopy(load_spec(HF_SPEC)["ops"][0])
    get_op(hf["op"]).plan(DocxPackage(data), hf.get("select") or {}, hf["params"])        # plans on its own
    with pytest.raises(JobException) as e:
        plan_spec(DocxPackage(data), _spec(hf, {"op": OP, "select": {}, "params": {"language": "en-US"}}))
    assert e.value.code == "conflict"


def _codes(module) -> set:
    codes = set()
    for n in ast.walk(ast.parse(Path(module.__file__).read_text(encoding="utf-8"))):
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "JobException" and len(n.args) > 1:
            assert isinstance(n.args[1], ast.Constant), ast.unparse(n)
            codes.add(n.args[1].value)
    return codes


def test_every_refusal_code_of_set_language_is_explained():
    codes = _codes(lang_module)
    assert codes == {"bad-params", "structure"}
    assert sorted(c for c in codes if c not in FAILURE_EXPLANATIONS) == []
