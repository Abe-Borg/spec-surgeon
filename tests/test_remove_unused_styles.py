"""remove_unused_styles and V10's ``removed_styles`` kind (built-in batch 4, item 32 — docs/BUILTIN_OPS_BATCH4_DESIGN.md
§8): end to end on the architect's fixture (which all / custom, types, keep by id and by name; the styles kept by a
single use — a next, a field, a tracked-change snapshot —; linked pairs; basedOn ancestors; numbering and settings;
the styles a field update applies); a wrong OUTPUT per rule; wrong IMPLEMENTATIONS shared by plan and apply; the two
readings compared; conflicts; no-op files; parameters."""
from __future__ import annotations

import ast
import copy
import io
import zipfile
from pathlib import Path
from typing import List, Tuple

import pytest
from lxml import etree

from make_fixtures import build, build_batch1, build_rich, build_style_template, build_styles, build_tabs, build_template
from spec_surgeon.editspec import apply_spec, load_spec, plan_spec
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import remove_unused_styles as rus
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.report import FAILURE_EXPLANATIONS
from spec_surgeon.verify.reader import VPackage
from spec_surgeon.verify import props as vprops
from spec_surgeon.verify import run_suite
from spec_surgeon.verify import unused as vunused

STYLES = "word/styles.xml"
MAIN = "word/document.xml"
HEADER = "word/header1.xml"
HF_SPEC = Path(__file__).resolve().parent.parent / "examples" / "specs" / "match-template-spec-header-footer.json"
W_DECL = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
# used by a single thing each in the fixture: a next, a STYLEREF field, a pending pPrChange snapshot
SINGLE_USE = {"ArchNextOnly", "ArchFieldOnly", "ArchSnapshotOnly"}
DEFAULTS = {"Normal", "DefaultParagraphFont", "TableNormal", "NoList"}
LISTS = {"ListNumber", "ListNumber2", "ListNumber3", "ListBullet", "ListBullet2", "ListBullet3"}
ARCH_UNUSED = ["ArchUnused", "ArchUnusedChar", "ArchLinkedPara", "ArchLinkedChar"]


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


def remove(data: bytes, params: dict = None):
    return run_ops(data, [("remove_unused_styles", {}, params or {})])


def failed(rec):
    return [c.id for c in rec.checks if c.status == "FAIL"]


def v10(rec) -> str:
    c = next(c for c in rec.checks if c.id == "V10")
    return c.summary + " | " + " | ".join(c.data.get("errors", []))


def ids(data: bytes) -> List[str]:
    return [el.get(W + "styleId") for el in DocxPackage(data).xml(STYLES) if el.tag == W + "style"]


def removed_of(plan) -> List[str]:
    return next(a for a in plan.property_assertions if a["kind"] == "removed_styles")["expected_removed"]


def mutate(docx: bytes, part: str, fn) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(docx))
    root = etree.fromstring(zin.read(part))
    fn(root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            z.writestr(info, serialize_xml(root) if info.filename == part else zin.read(info.filename))
    return buf.getvalue()


def style_el(root, sid):
    return next(el for el in root if el.tag == W + "style" and el.get(W + "styleId") == sid)


def add_style(xml: str):
    def fn(root):
        root.append(etree.fromstring(xml.replace("<w:style ", f"<w:style {W_DECL} ", 1)))
    return fn


def set_child(sid: str, tag: str, val: str):
    def fn(root):
        st = style_el(root, sid)
        el = st.find(W + tag)
        if el is None:
            el = etree.SubElement(st, W + tag)
        el.set(W + "val", val)
    return fn


# ---- end to end ----------------------------------------------------------------------------------------------------

def test_every_unused_style_goes_and_every_used_one_stays(src):
    plan, out, rec = remove(src)
    assert rec.passed, rec.to_markdown()
    gone = removed_of(plan)
    kept = set(ids(out))
    assert set(ids(src)) - set(gone) == kept and not kept & set(gone)
    assert set(ARCH_UNUSED) <= set(gone)                        # unused on purpose, the linked pair with them
    assert {"Heading2", "Heading2Char", "Title", "TitleChar", "TableGrid", "Strong"} <= set(gone)   # Word's built-ins
    assert DEFAULTS | LISTS | SINGLE_USE <= kept                # defaults, numbering levels, a single use each
    assert {"Heading1", "Heading1Char", "ArchArticle", "ArchStrong", "ArchPara", "ArchNote", "ArchNoteChild",
            "ArchTable", "Header", "HeaderChar", "Footer", "FooterChar"} <= kept
    assert len(gone) == 152 and len(kept) == 28
    # styles.xml only; the order of the styles that stay is the source's
    assert plan.intended_parts == [STYLES] and plan.mode == "formatting" and plan.geometry_reference == "source"
    assert [s for s in ids(src) if s in kept] == ids(out)
    for part in (MAIN, HEADER, "word/numbering.xml", "word/settings.xml", "word/stylesWithEffects.xml"):
        assert DocxPackage(src).read(part) == DocxPackage(out).read(part)
    assert any("152 unused style(s) removed" in n for n in plan.notes)
    assert any("named by field instructions (kept): ArchArticle, ArchFieldOnly" in n for n in plan.notes)
    assert any("stylesWithEffects.xml" in f for f in plan.flags)
    assert {("word/styles.xml", "style:ArchUnused", a) for a in ("remove", "rPr", "pPr", "tblPr")} <= \
        set(map(tuple, get_op("remove_unused_styles").plan(DocxPackage(src), {}, {}).touches))


def test_which_custom_keeps_the_builtins_and_what_they_link_to(src):
    """Word's built-in Heading 4 stays (not custom), so its linked "Heading 4 Char" — custom in python-docx's template,
    and unused — stays too: no style that stays may name one that goes."""
    plan, out, rec = remove(src, {"which": "custom"})
    assert rec.passed, rec.to_markdown()
    assert removed_of(plan) == ARCH_UNUSED
    assert {"Heading4", "Heading4Char", "TitleChar", "Heading2"} <= set(ids(out))
    assert not any("built-in" in n for n in plan.notes)


def test_types_narrow_what_is_considered(src):
    plan, out, rec = remove(src, {"types": ["character"]})
    assert rec.passed, rec.to_markdown()
    assert removed_of(plan) == ["Strong", "Emphasis", "SubtleEmphasis", "IntenseEmphasis", "SubtleReference",
                                "IntenseReference", "BookTitle", "ArchUnusedChar"]
    assert {"ArchLinkedChar", "Heading2Char"} <= set(ids(out))     # their paragraph styles stay (not considered)
    plan, out, rec = remove(src, {"types": ["table"]})
    assert rec.passed, rec.to_markdown()
    assert len(removed_of(plan)) == 99 and "TableGrid" in removed_of(plan)
    assert {"TableNormal", "ArchTable"} <= set(ids(out)) and "ArchUnused" in ids(out)
    plan, out, rec = remove(src, {"types": ["paragraph", "numbering"], "which": "custom"})
    assert rec.passed and removed_of(plan) == ["ArchUnused"]
    # the character style ArchLinkedChar is not considered, so it stays — and its w:link keeps ArchLinkedPara
    assert {"ArchLinkedChar", "ArchLinkedPara"} <= set(ids(out))


def test_keep_by_id_and_by_name(src):
    plan, out, rec = remove(src, {"keep": ["ArchUnused", " arch LINKED para ", "No Such Style"]})
    assert rec.passed, rec.to_markdown()
    assert {"ArchUnused", "ArchLinkedPara", "ArchLinkedChar"} <= set(ids(out))     # the linked char style with it
    assert "ArchUnusedChar" in removed_of(plan) and len(removed_of(plan)) == 149
    assert any("keep: ['No Such Style'] name(s) no style of this file" in n for n in plan.notes)
    assert any("those kept by 'keep'" in n for n in plan.notes)


def test_a_single_use_keeps_a_style(src):
    """ArchNextOnly is only ArchArticle's next, ArchFieldOnly only named by a STYLEREF in the header (by its name),
    ArchSnapshotOnly only in a pending pPrChange's snapshot (a reject-all brings it back)."""
    plan, out, rec = remove(src, {"which": "custom"})
    assert SINGLE_USE <= set(ids(out))
    # the same styles without their one use go
    def no_uses(root):
        style_el(root, "ArchArticle").remove(style_el(root, "ArchArticle").find(W + "next"))
    def no_snapshot(root):
        for chg in root.iter(W + "pPrChange"):
            chg.find(W + "pPr").remove(chg.find(W + "pPr/" + W + "pStyle"))
    def no_field(root):
        for t in root.iter(W + "instrText"):
            t.text = " PAGE "
    bare = mutate(mutate(mutate(src, STYLES, no_uses), MAIN, no_snapshot), HEADER, no_field)
    plan, out, rec = remove(bare, {"which": "custom"})
    assert rec.passed, rec.to_markdown()
    assert SINGLE_USE <= set(removed_of(plan))


def test_based_on_ancestors_of_used_styles_stay(src):
    """ArchPara based on python-docx's (unused) Body Text: Body Text stays, and its linked Body Text Char with it."""
    data = mutate(src, STYLES, set_child("ArchPara", "basedOn", "BodyText"))
    plan, out, rec = remove(data)
    assert rec.passed, rec.to_markdown()
    assert {"BodyText", "BodyTextChar", "ArchPara"} <= set(ids(out))
    assert "ArchUnused" in removed_of(plan) and "BodyText2" in removed_of(plan)


def test_numbering_and_settings_references_keep_styles(src):
    def settings(root):
        root.insert(0, etree.fromstring(f'<w:clickAndTypeStyle {W_DECL} w:val="Title"/>'))
        root.insert(0, etree.fromstring(f'<w:defaultTableStyle {W_DECL} w:val="LightList"/>'))
    data = mutate(src, "word/settings.xml", settings)
    plan, out, rec = remove(data)
    assert rec.passed, rec.to_markdown()
    assert LISTS | {"Title", "TitleChar", "LightList"} <= set(ids(out))     # numbering levels' pStyle; the settings
    assert "LightList-Accent1" in removed_of(plan)


def test_aliases_simple_fields_and_case(src):
    """A w:fldSimple naming an alias, in another case, keeps the style."""
    data = mutate(src, STYLES, set_child("ArchUnused", "aliases", "Note Para, ArchAlias"))
    def simple(root):
        p = next(root.iter(W + "p"))
        p.append(etree.fromstring(f'<w:fldSimple {W_DECL} w:instr=" STYLEREF archalias "><w:r><w:t>x</w:t></w:r>'
                                  f'</w:fldSimple>'))
    data = mutate(data, MAIN, simple)
    plan, out, rec = remove(data)
    assert rec.passed, rec.to_markdown()
    assert "ArchUnused" in ids(out) and "ArchUnused" not in removed_of(plan)


def test_the_styles_a_field_update_applies_stay(src):
    """A TOC field applies "toc 1" … "toc 9" when it is updated; an INDEX field "index 1" …: with the document's TOC,
    an unused "toc 3" stays (its customisation would be lost at the next update) and an unused "index 1" goes."""
    data = mutate(src, STYLES, add_style('<w:style w:type="paragraph" w:styleId="TOC3"><w:name w:val="toc 3"/>'
                                         '<w:basedOn w:val="Normal"/><w:pPr><w:ind w:left="440"/></w:pPr></w:style>'))
    data = mutate(data, STYLES, add_style('<w:style w:type="paragraph" w:styleId="Index1"><w:name w:val="index 1"/>'
                                          '<w:basedOn w:val="Normal"/></w:style>'))
    plan, out, rec = remove(data)
    assert rec.passed, rec.to_markdown()
    assert "TOC3" in ids(out) and "Index1" in removed_of(plan)
    assert any("applied by a field update" in n and "TOC3" in n for n in plan.notes)
    def no_toc(root):
        for t in root.iter(W + "instrText"):
            t.text = ' REF "Arch Article" '               # still names ArchArticle, no TOC any more
    plan, out, rec = remove(mutate(data, MAIN, no_toc))
    assert rec.passed, rec.to_markdown()
    assert "TOC3" in removed_of(plan) and "ArchArticle" in ids(out)


# ---- no-ops, refusals, parameters ----------------------------------------------------------------------------------

def test_a_file_with_nothing_unused_is_a_no_op(src):
    _, out, _ = remove(src)
    plan, out2, rec = remove(out)
    assert rec.passed, rec.to_markdown()
    assert out2 == out and plan.intended_parts == [] and removed_of(plan) == []
    assert any("nothing to do" in n for n in plan.notes)
    with pytest.raises(JobException) as e:
        remove(out, {"allow_missing": False})
    assert e.value.code == "not-found"
    plan, _, rec = remove(src, {"which": "custom", "types": ["table"]})        # ArchTable is used: none custom unused
    assert rec.passed and removed_of(plan) == [] and any("no unused custom table style" in n for n in plan.notes)


def test_a_file_without_a_styles_part(src):
    def drop_rel(root):
        for rel in list(root):
            if rel.get("Type", "").endswith("/styles"):
                root.remove(rel)
    data = mutate(src, "word/_rels/document.xml.rels", drop_rel)
    plan, out, rec = remove(data)
    assert rec.passed, rec.to_markdown()
    assert any("no styles part" in n for n in plan.notes) and out == data
    with pytest.raises(JobException) as e:
        remove(data, {"allow_missing": False})
    assert e.value.code == "not-found"


def test_two_definitions_of_one_style_refuse(src):
    data = mutate(src, STYLES, add_style('<w:style w:type="paragraph" w:styleId="ArchPara"><w:name w:val="Twin"/>'
                                         '</w:style>'))
    with pytest.raises(JobException) as e:
        remove(data)
    assert e.value.code == "structure" and "ArchPara" in str(e.value)


@pytest.mark.parametrize("select, params, needle", [
    ({"styles": "all"}, {}, "no selector"),
    ({}, {"tracked": True}, "not tracked"),
    ({}, {"keep": "ArchUnused"}, "'keep'"),
    ({}, {"keep": ["ok", ""]}, "'keep'"),
    ({}, {"keep": [3]}, "'keep'"),
    ({}, {"which": "builtin"}, "'which'"),
    ({}, {"types": []}, "'types'"),
    ({}, {"types": "paragraph"}, "'types'"),
    ({}, {"types": ["paragraph", "list"]}, "unknown style type"),
    ({}, {"allow_missing": "yes"}, "allow_missing"),
    ({}, {"colour": "red"}, "unknown parameter"),
])
def test_parameters_are_checked(src, select, params, needle):
    with pytest.raises(JobException) as e:
        get_op("remove_unused_styles").plan(DocxPackage(src), select, params)
    assert e.value.code == "bad-params" and needle in str(e.value)


def test_every_refusal_code_is_explained():
    codes = set()
    for n in ast.walk(ast.parse(Path(rus.__file__).read_text(encoding="utf-8"))):
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "JobException" and len(n.args) > 1:
            assert isinstance(n.args[1], ast.Constant)
            codes.add(n.args[1].value)
    codes.discard("internal")                                         # diagnostics.classify: an internal error
    assert codes == {"bad-params", "not-found", "structure"}
    assert sorted(c for c in codes if c not in FAILURE_EXPLANATIONS) == []


# ---- wrong outputs -------------------------------------------------------------------------------------------------

def _caught(src, plan, out, part, fn, needle=None):
    bad = mutate(out, part, fn)
    rec = run_suite(src, bad, plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec), rec.to_markdown()
    if needle:
        assert needle in v10(rec), v10(rec)
    return rec


def test_wrong_outputs_are_caught(src):
    plan, out, rec = remove(src)
    assert rec.passed
    s_root = DocxPackage(src).xml(STYLES)
    # an extra style removed: the header's STYLEREF names it
    _caught(src, plan, out, STYLES, lambda r: r.remove(style_el(r, "ArchFieldOnly")), "a field instruction names")
    # a style a numbering level names removed
    _caught(src, plan, out, STYLES, lambda r: r.remove(style_el(r, "ListNumber2")), "w:pStyle names style 'ListNumber2'")
    # a removable style left in the output
    _caught(src, plan, out, STYLES, lambda r: r.append(copy.deepcopy(style_el(s_root, "ArchUnused"))),
            "still defined in the output")
    # another style's definition changed
    _caught(src, plan, out, STYLES, lambda r: style_el(r, "Heading1").find(W + "rPr").append(etree.Element(W + "i")),
            "unpredicted change")
    # nothing removed at all: the styles part is byte-identical, so the residual never runs — the kind's own check does
    rec = run_suite(src, src, plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec) and "152 unused style(s) are still defined" in v10(rec)


# ---- wrong implementations shared by plan and apply ----------------------------------------------------------------

def test_a_shared_scan_that_skips_tracked_change_snapshots_is_caught(src, monkeypatch):
    def no_snapshots(root):
        out = set()
        for el in root.iter(*rus.REF_TAGS):
            if not any(etree.QName(a).localname.endswith("Change") for a in el.iterancestors()):
                if el.get(W + "val") is not None:
                    out.add(el.get(W + "val"))
        return out
    monkeypatch.setattr(rus, "references", no_snapshots)
    plan, out, rec = remove(src, {"which": "custom"})
    assert "ArchSnapshotOnly" in removed_of(plan)
    assert "V10" in failed(rec) and "ArchSnapshotOnly" in v10(rec) and "w:pStyle names style" in v10(rec)


def test_a_shared_scan_that_ignores_field_instructions_is_caught(src, monkeypatch):
    monkeypatch.setattr(rus, "instruction_text", lambda root: "")
    plan, out, rec = remove(src, {"which": "custom"})
    assert "ArchFieldOnly" in removed_of(plan)
    assert "V10" in failed(rec) and "a field instruction names style 'ArchFieldOnly'" in v10(rec)


def test_a_shared_case_sensitive_field_match_is_caught(src, monkeypatch):
    def lower_ref(root):
        for t in root.iter(W + "instrText"):
            t.text = t.text.replace("Arch Field Only", "arch field only")
    data = mutate(src, HEADER, lower_ref)
    plan, _, rec = remove(data, {"which": "custom"})
    assert rec.passed and "ArchFieldOnly" not in removed_of(plan)        # Word matches style names in any case
    monkeypatch.setattr(rus, "fold", lambda s: s)
    plan, out, rec = remove(data, {"which": "custom"})
    assert "ArchFieldOnly" in removed_of(plan)
    assert "V10" in failed(rec) and "ArchFieldOnly" in v10(rec)


def test_a_shared_rule_without_the_closure_is_caught(src, monkeypatch):
    monkeypatch.setattr(rus, "closure", lambda seed, by_id: {el.get(W + "styleId") for el in seed})
    plan, out, rec = remove(src)
    assert {"ArchNextOnly", "HeaderChar", "Heading1Char"} - set(removed_of(plan)) == {"Heading1Char"}   # a run uses it
    assert "V10" in failed(rec)
    assert "its w:next names style 'ArchNextOnly'" in v10(rec) and "its w:link names style" in v10(rec)


def test_a_shared_closure_seeded_by_the_used_styles_alone_is_caught(src, monkeypatch):
    """The design's words read literally: the closure from the used styles only. With which: custom, Word's built-in
    Heading 4 stays (not considered) while its custom linked "Heading 4 Char" goes — a dangling w:link."""
    monkeypatch.setattr(rus, "seed_styles", lambda table, by_id, used, which, types: [by_id[s] for s in used if s in by_id])
    plan, out, rec = remove(src, {"which": "custom"})
    assert "Heading4Char" in removed_of(plan)
    assert "V10" in failed(rec) and "w:link names style 'Heading4Char'" in v10(rec)


def test_a_shared_scan_that_ignores_the_numbering_part_is_caught(src, monkeypatch):
    real = rus.scanned_parts
    monkeypatch.setattr(rus, "scanned_parts", lambda pkg: (real(pkg)[0], [p for p in real(pkg)[1]
                                                                         if not p.endswith("numbering.xml")]))
    plan, out, rec = remove(src)
    assert LISTS <= set(removed_of(plan))
    assert "V10" in failed(rec) and "word/numbering.xml: a w:pStyle names style" in v10(rec)


def test_a_shared_scan_that_ignores_the_settings_part_is_caught(src, monkeypatch):
    data = mutate(src, "word/settings.xml",
                  lambda r: r.insert(0, etree.fromstring(f'<w:defaultTableStyle {W_DECL} w:val="LightList"/>')))
    real = rus.scanned_parts
    monkeypatch.setattr(rus, "scanned_parts", lambda pkg: (real(pkg)[0], [p for p in real(pkg)[1]
                                                                         if not p.endswith("settings.xml")]))
    plan, out, rec = remove(data)
    assert "LightList" in removed_of(plan)
    assert "V10" in failed(rec) and "w:defaultTableStyle names style 'LightList'" in v10(rec)


def test_a_shared_rule_that_forgets_the_field_update_styles_is_caught(src, monkeypatch):
    data = mutate(src, STYLES, add_style('<w:style w:type="paragraph" w:styleId="TOC3"><w:name w:val="toc 3"/>'
                                         '<w:basedOn w:val="Normal"/></w:style>'))
    monkeypatch.setattr(rus, "FIELD_STYLES", {})
    plan, out, rec = remove(data)
    assert "TOC3" in removed_of(plan)
    assert "V10" in failed(rec) and "TOC3" in v10(rec)


def test_a_shared_default_which_is_caught(src, monkeypatch):
    """The assertion carries the user's own words (no 'which'); the verifier applies its own default (all)."""
    real = rus.clean_params

    def custom_by_default(params):
        keep, which, types, allow = real(params)
        return keep, "custom" if "which" not in params else which, types, allow
    monkeypatch.setattr(rus, "clean_params", custom_by_default)
    plan, out, rec = remove(src)
    assert removed_of(plan) == ARCH_UNUSED
    assert "V10" in failed(rec) and "152 style(s) are unused here, the plan removes 4" in v10(rec)


def test_a_shared_keep_that_matches_names_case_sensitively_is_caught(src, monkeypatch):
    real = rus.kept_by
    monkeypatch.setattr(rus, "kept_by", lambda table, keep: real(table, []) if keep == ["arch unused"] else
                        real(table, keep))
    plan, out, rec = remove(src, {"keep": ["arch unused"]})
    assert "ArchUnused" in removed_of(plan)
    assert "V10" in failed(rec) and "the plan removes ['ArchUnused'], which are used here" in v10(rec)


# ---- the two readings agree ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("make", [build_styles, build_rich, build_tabs, build_template, build_style_template,
                                  lambda: build_batch1(textbox=True), lambda: build("21 13 13", "T")])
def test_transform_and_verifier_decisions_agree(make):
    data = make()
    pkg, vpkg = DocxPackage(data), VPackage(data)
    root = vpkg.xml(STYLES)
    for keep, which, types in [([], "all", rus.STYLE_TYPES), ([], "custom", rus.STYLE_TYPES),
                               (["Heading 2", "TableGrid"], "all", ("paragraph", "table")),
                               (["normal"], "custom", ("character",)), ([], "all", ("numbering",))]:
        t = rus.analyze(pkg, STYLES, keep, which, types)["removed"]
        v, _ = vunused._v_removable(vpkg, vunused._v_styles(root), keep, which, set(types))
        assert t == v, (make, keep, which, types)


# ---- conflicts (rule 9) --------------------------------------------------------------------------------------------

def _spec(*ops, request="remove the unused styles"):
    return {"request": request, "summary": request, "mode": "formatting", "ops": list(ops)}


REMOVE = {"op": "remove_unused_styles"}


@pytest.mark.parametrize("other", [
    {"op": "set_properties", "select": {"styles": {"ids": ["ArchUnused"]}}, "params": {"run": {"b": True}}},
    {"op": "set_style_font", "select": {"styles": {"ids": ["Heading2"]}}, "params": {"font": "Arial"}},
    {"op": "insert_paragraph", "select": {"paragraphs": {"text_regex": "^NOTE TO SPECIFIER"}},
     "params": {"text": "Coordinate with the owner.", "style": "ArchUnused", "tracked": False}},
    {"op": "remove_unused_styles", "params": {"which": "custom"}},
])
def test_an_op_naming_or_changing_a_removed_style_conflicts(src, other):
    for ops in ((REMOVE, other), (other, REMOVE)):
        with pytest.raises(JobException) as e:
            plan_spec(DocxPackage(src), _spec(*ops, request="r"))
        assert e.value.code == "conflict" and "word/styles.xml" in str(e.value)


def test_an_op_on_a_style_that_stays_plans_and_verifies(src):
    for other in ({"op": "set_properties", "select": {"styles": {"ids": ["ArchPara"]}},
                   "params": {"paragraph": {"keepLines": True}}},
                  {"op": "insert_paragraph", "select": {"paragraphs": {"text_regex": "^NOTE TO SPECIFIER"}},
                   "params": {"text": "Coordinate with the owner.", "style": "ArchNote", "tracked": False}}):
        spec = _spec(REMOVE, other)
        spec["mode"] = "untracked" if other["op"] == "insert_paragraph" else "formatting"
        pkg = DocxPackage(src)
        plan, frags = plan_spec(pkg, spec)
        out = apply_spec(pkg, frags).output_bytes()
        rec = run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")
        assert rec.passed, rec.to_markdown()
        assert "ArchUnused" not in ids(out)


def test_beside_the_templating_op_it_conflicts_and_runs_as_a_second_spec():
    """The templating op plans from the target's styles part as a whole: removing styles underneath is refused; as two
    specs (the template first, then remove unused styles on its outputs) both verify."""
    data = build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS")
    hf = copy.deepcopy(load_spec(HF_SPEC)["ops"][0])
    for ops in ((REMOVE, hf), (hf, REMOVE)):
        with pytest.raises(JobException) as e:
            plan_spec(DocxPackage(data), _spec(*ops))
        assert e.value.code == "conflict" and "word/styles.xml" in e.value.reason
        assert "as it is in the source" in e.value.reason
    pkg = DocxPackage(data)
    plan, frags = plan_spec(pkg, _spec(hf))
    out1 = apply_spec(pkg, frags).output_bytes()
    assert run_suite(data, out1, plan.to_dict(), "a", "b").passed
    pkg2 = DocxPackage(out1)
    plan2, frags2 = plan_spec(pkg2, _spec(REMOVE))
    out2 = apply_spec(pkg2, frags2).output_bytes()
    rec = run_suite(out1, out2, plan2.to_dict(), "a", "b")
    assert rec.passed, rec.to_markdown()
    assert removed_of(plan2) and set(ids(out2)) < set(ids(out1))


def test_the_kind_is_registered():
    assert "removed_styles" in vprops.KINDS


def test_a_spec_runs_through_the_batch(src, tmp_path):
    from spec_surgeon.batch import run_batch
    spec = _spec({"op": "remove_unused_styles", "params": {"which": "custom", "types": ["paragraph", "character"],
                                                           "keep": ["Arch Unused Char"]}})
    (tmp_path / "21 13 13 – SPEC.docx").write_bytes(src)
    batch = run_batch([str(tmp_path / "21 13 13 – SPEC.docx")], "edit-spec", {}, tmp_path / "out", workers=1,
                      renderer="none", do_render=False, spec=spec)
    f = batch.files[0]
    assert f.status == "PASS", f.record.to_markdown() if f.record else f.exception
    out = (tmp_path / "out" / "outputs" / "21 13 13 – SPEC.docx").read_bytes()
    assert {"ArchUnused", "ArchLinkedPara", "ArchLinkedChar"}.isdisjoint(ids(out)) and "ArchUnusedChar" in ids(out)
    bad = _spec({"op": "remove_unused_styles", "params": {"types": ["list"]}})
    with pytest.raises(Exception) as e:
        plan_spec(DocxPackage(src), bad)
    assert "types" in str(e.value)
