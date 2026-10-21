"""set_paragraph_props on paragraph styles and V10's ``paragraph_props`` styles branch (built-in batch 4, item 27 —
docs/BUILTIN_OPS_BATCH4_DESIGN.md §2): end to end on the architect's spec (merge semantics, already as asked, flags);
the refusals (Autospacing, spacing in lines on the style, an ancestor or the document defaults, the indentation alias);
a wrong OUTPUT per rule; wrong IMPLEMENTATIONS shared by plan and apply; conflicts; parameters; plans without a target
stay direct."""
from __future__ import annotations

import io
import zipfile
from typing import List, Tuple

import pytest
from lxml import etree

from make_fixtures import build, build_styles
from spec_surgeon.editspec import SpecError, apply_spec, plan_spec, validate_spec
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import set_paragraph_props as spp_module
from spec_surgeon.ops.base import verify_family
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.verify import run_suite

AUTHOR = "Abraham Borg"
MAIN = "word/document.xml"
STYLES = "word/styles.xml"


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


def styles_op(data: bytes, ids, params: dict):
    return run_ops(data, [("set_paragraph_props", {"styles": {"ids": list(ids)}}, params)])


def failed(rec):
    return [c.id for c in rec.checks if c.status == "FAIL"]


def v10(rec) -> str:
    c = next(c for c in rec.checks if c.id == "V10")
    return c.summary + " | " + " | ".join(c.data.get("errors", []))


def style(root, sid):
    return next(s for s in root.findall(W + "style") if s.get(W + "styleId") == sid)


def attrs(ppr, name):
    e = ppr.find(W + name) if ppr is not None else None
    return None if e is None else {etree.QName(k).localname: v for k, v in e.attrib.items()}


def styles_root(data: bytes):
    return DocxPackage(data).xml(STYLES)


def mutate(docx: bytes, part: str, fn) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(docx))
    root = etree.fromstring(zin.read(part))
    fn(root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            z.writestr(info, serialize_xml(root) if info.filename == part else zin.read(info.filename))
    return buf.getvalue()


def el(tag, **kw):
    e = etree.Element(W + tag)
    for k, v in kw.items():
        e.set(W + k, v)
    return e


def add_style(root, xml: str):
    root.append(etree.fromstring(xml.replace("<w:style ", f'<w:style xmlns:w="{W[1:-1]}" ', 1)))


# ---- end to end ----------------------------------------------------------------------------------------------------

def test_values_merge_into_the_styles_own_ppr(src):
    """ArchArticle keeps before 240 and keepNext and gets after 120; ArchPara's hanging gives way to firstLine 0 and its
    left stays — the merge the paragraphs get. Never tracked; the layout reference is none."""
    plan, out, rec = styles_op(src, ["ArchArticle", "ArchPara"], {"spacing": {"after": 120}, "ind": {"firstLine": 0}})
    assert rec.passed, rec.to_markdown()
    a = plan.property_assertions[0]
    assert a["kind"] == "paragraph_props" and a["target"] == "styles" and a["expected_ids"] == ["ArchArticle", "ArchPara"]
    assert plan.intended_parts == [STYLES] and not {k: v for k, v in plan.rev_delta.items() if v}
    assert plan.geometry_reference == "none"
    root = styles_root(out)
    art, par = style(root, "ArchArticle").find(W + "pPr"), style(root, "ArchPara").find(W + "pPr")
    assert attrs(art, "spacing") == {"before": "240", "after": "120"} and art.find(W + "keepNext") is not None
    assert attrs(art, "ind") == {"firstLine": "0"}
    assert attrs(par, "ind") == {"left": "720", "firstLine": "0"} and attrs(par, "spacing") == {"after": "120"}
    assert [etree.QName(c).localname for c in art] == ["keepNext", "spacing", "ind"]        # schema order
    # "A. Section includes" sets its own spacing after and left indent: flagged, never refused
    assert any("1 paragraph(s) using style ArchPara" in f for f in plan.flags), plan.flags


def test_a_style_without_its_own_ppr_gets_one(src):
    plan, out, rec = styles_op(src, ["ArchNextOnly"], {"jc": "both"})
    assert rec.passed, rec.to_markdown()
    st = style(styles_root(out), "ArchNextOnly")
    assert attrs(st.find(W + "pPr"), "jc") == {"val": "both"}
    assert [etree.QName(c).localname for c in st] == ["name", "basedOn", "pPr"]


def test_already_as_asked_is_left_alone(src):
    plan, out, rec = styles_op(src, ["ArchArticle", "ArchPara"], {"spacing": {"before": 240, "after": 60}})
    assert rec.passed, rec.to_markdown()
    assert plan.property_assertions[0]["expected_ids"] == ["ArchPara"]
    assert any("already as asked: ['ArchArticle']" in n for n in plan.notes)
    plan, out, rec = styles_op(src, ["ArchArticle"], {"spacing": {"before": 240}})
    assert rec.passed and out == src and plan.intended_parts == [] and plan.geometry_reference == "source"


def test_based_on_styles_that_set_their_own_value_are_noted(src):
    plan, out, rec = styles_op(src, ["Normal"], {"ind": {"left": 360}})
    assert rec.passed, rec.to_markdown()
    assert any("based on Normal" in n and "'ArchPara'" in n for n in plan.notes), plan.notes
    assert any("paragraph(s) using style Normal" in f for f in plan.flags), plan.flags      # Manual list item, A., …


def test_plans_without_a_target_stay_direct(src):
    plan, out, rec = run_ops(src, [("set_paragraph_props", {"paragraphs": {"text_regex": "^1\\.01"}},
                                    {"spacing": {"after": 120}, "author": AUTHOR})])
    assert rec.passed, rec.to_markdown()
    a = plan.property_assertions[0]
    assert "target" not in a and a["expected_paragraphs"] == 1 and plan.rev_delta["pPrChange"] == 1


# ---- refusals ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("ids, params, needle", [
    (["ArchAuto"], {"spacing": {"before": 120}}, "Autospacing"),
    (["ArchLines"], {"spacing": {"after": 120}}, "afterLines=100"),
    (["ArchStart"], {"ind": {"left": 360}}, "w:start"),
    (["ArchStart"], {"ind": {"end": 360, "right": 0}}, None),
])
def test_refusals_are_file_specific_conflicts(src, ids, params, needle):
    with pytest.raises(JobException) as e:
        styles_op(src, ids, params)
    assert e.value.code == "conflict-property"
    if needle:
        assert needle in str(e.value)


def test_the_lines_rule_reads_the_ancestors_and_the_document_defaults(src):
    def child(root):
        add_style(root, '<w:style w:type="paragraph" w:styleId="ArchLinesChild"><w:name w:val="Arch Lines Child"/>'
                        '<w:basedOn w:val="ArchLines"/></w:style>')
    data = mutate(src, STYLES, child)
    with pytest.raises(JobException) as e:
        styles_op(data, ["ArchLinesChild"], {"spacing": {"after": 120}})
    assert e.value.code == "conflict-property" and "style 'ArchLines'" in str(e.value)
    plan, out, rec = styles_op(data, ["ArchLinesChild"], {"spacing": {"after": 120, "afterLines": 0}})
    assert rec.passed, rec.to_markdown()                                     # the lines asked too: fine
    dd = mutate(src, STYLES, lambda r: r.find(W + "docDefaults/" + W + "pPrDefault/" + W + "pPr/" + W + "spacing").set(
        W + "beforeLines", "50"))
    with pytest.raises(JobException) as e:
        styles_op(dd, ["ArchArticle"], {"spacing": {"before": 120}})
    assert e.value.code == "conflict-property" and "document defaults" in str(e.value)
    plan, out, rec = styles_op(dd, ["ArchArticle"], {"spacing": {"after": 120}})       # the other side: fine
    assert rec.passed, rec.to_markdown()


def test_parameters_are_checked(src):
    pkg = DocxPackage(src)
    op = get_op("set_paragraph_props")
    for select, params, code in (
            ({"styles": {"ids": ["ArchPara"]}}, {"jc": "both", "tracked": True}, "bad-params"),
            ({"styles": {"ids": ["ArchPara"]}, "paragraphs": "all"}, {"jc": "both"}, "bad-params"),
            ({"styles": {"ids": ["ArchPara"]}, "scope": ["document"]}, {"jc": "both"}, "bad-params"),
            ({"styles": ["ArchPara"]}, {"jc": "both"}, "bad-params"),
            ({"styles": {"ids": []}}, {"jc": "both"}, "bad-params"),
            ({"styles": {"ids": ["ArchStrong"]}}, {"jc": "both"}, "style-mismatch"),    # a character style here
            ({"styles": {"ids": ["ArchTable"]}}, {"jc": "both"}, "style-mismatch"),     # a table style here
            ({"styles": {"ids": ["ArchPara"]}}, {"ind": {"firstLineChars": 1, "hanging": 1}}, "bad-params"),
            ({"styles": {"ids": ["ArchPara"]}}, {"ind": {"leftChars": "1"}}, "bad-params"),
            ({"styles": {"ids": ["Nope"]}}, {"jc": "both"}, "not-found"),
            ({"styles": {"ids": ["ArchPara"]}}, {"ind": {"firstLine": 1, "hanging": 1}}, "bad-params"),
            ({"styles": {"ids": ["ArchPara"]}}, {}, "bad-params")):
        with pytest.raises(JobException) as e:
            op.plan(pkg, select, params)
        assert e.value.code == code, (select, params)
    f = op.plan(pkg, {"styles": {"ids": ["ArchPara"]}}, {"jc": "both", "tracked": False})          # false is fine
    assert f.property_assertions[0]["expected_ids"] == ["ArchPara"]
    validate_spec({"request": "r", "summary": "s", "mode": "formatting",
                   "ops": [{"op": "set_paragraph_props", "select": {"styles": {"ids": ["ArchPara"]}},
                            "params": {"spacing": {"after": 120}}}]})
    with pytest.raises(SpecError):
        validate_spec({"request": "r", "summary": "s", "mode": "formatting",
                       "ops": [{"op": "set_paragraph_props", "select": {"style": {"ids": ["ArchPara"]}},
                                "params": {"spacing": {"after": 120}}}]})


def test_touches(src):
    t = set(map(tuple, get_op("set_paragraph_props").plan(DocxPackage(src), {"styles": {"ids": ["ArchArticle", "ArchPara"]}},
                                                          {"spacing": {"before": 240}}).touches))
    assert (STYLES, "style:ArchPara", "pPr") in t and (STYLES, "style:ArchArticle", "pPr") not in t   # already
    assert {(STYLES, "style:ArchArticle", "verify:pPr"), (STYLES, "style:Normal", "verify:pPr"),
            (STYLES, "docDefaults", "verify:pPr")} <= t
    t = set(map(tuple, get_op("set_paragraph_props").plan(DocxPackage(src), {"styles": {"ids": ["ArchPara"]}},
                                                          {"jc": "both"}).touches))
    assert t == {(STYLES, "style:ArchPara", "pPr"), (STYLES, "style:ArchPara", "verify:pPr")}   # no lines read
    t = set(map(tuple, get_op("set_paragraph_props").plan(DocxPackage(src), {"paragraphs": {"text_regex": "^1\\.01"}},
                                                          {"spacing": {"after": 120}}).touches))
    assert {(MAIN, "3", "pPr"), (STYLES, "style:ArchArticle", "verify:pPr"), (STYLES, "style:Normal", "verify:pPr")} <= t


# ---- a wrong output per rule ---------------------------------------------------------------------------------------

def _caught(src, plan, out, part, fn, needle=None):
    bad = mutate(out, part, fn)
    rec = run_suite(src, bad, plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec), rec.to_markdown()
    if needle:
        assert needle in v10(rec), v10(rec)


def test_wrong_outputs_are_caught(src):
    plan, out, rec = styles_op(src, ["ArchArticle", "ArchPara"], {"spacing": {"after": 120}, "ind": {"firstLine": 0}})
    assert rec.passed
    # hanging left beside firstLine
    _caught(src, plan, out, STYLES, lambda r: style(r, "ArchPara").find(W + "pPr/" + W + "ind").set(W + "hanging", "360"),
            "ind is")
    # a value not merged
    _caught(src, plan, out, STYLES, lambda r: style(r, "ArchArticle").find(W + "pPr/" + W + "spacing").set(W + "after", "60"),
            "spacing is")
    # another pPr child of a changed style
    _caught(src, plan, out, STYLES, lambda r: style(r, "ArchArticle").find(W + "pPr").remove(
        style(r, "ArchArticle").find(W + "pPr/" + W + "keepNext")), "other than")
    # the style's run properties
    _caught(src, plan, out, STYLES, lambda r: style(r, "ArchArticle").find(W + "rPr").append(el("i")), "unpredicted change")
    # a style that was not asked
    _caught(src, plan, out, STYLES, lambda r: style(r, "ArchNote").find(W + "pPr").append(el("jc", val="both")),
            "unpredicted change")
    # an already-as-asked style touched anyway
    plan, out, rec = styles_op(src, ["ArchArticle"], {"spacing": {"before": 240}})
    assert rec.passed
    _caught(src, plan, out, STYLES, lambda r: style(r, "ArchArticle").find(W + "pPr/" + W + "spacing").set(W + "before", "241"))


# ---- wrong implementations shared by plan and apply ----------------------------------------------------------------

def test_a_shared_merge_that_keeps_hanging_beside_first_line_is_caught(src, monkeypatch):
    real = spp_module.merged_attrs

    def keeps_hanging(name, el_, val):
        out = real(name, el_, val)
        if name == "ind" and el_ is not None and el_.get(W + "hanging") is not None:
            out[W + "hanging"] = el_.get(W + "hanging")
        return out
    monkeypatch.setattr(spp_module, "merged_attrs", keeps_hanging)
    plan, out, rec = styles_op(src, ["ArchPara"], {"ind": {"firstLine": 0}})
    assert attrs(style(styles_root(out), "ArchPara").find(W + "pPr"), "ind") == {"left": "720", "hanging": "360",
                                                                                  "firstLine": "0"}
    assert "V10" in failed(rec) and "ind is" in v10(rec)


def _children_of(src):
    """The fixture with ArchLinesChild (based on ArchLines) and ArchAutoChild (based on ArchAuto) — nothing of their own."""
    def add(r):
        for sid, base in (("ArchLinesChild", "ArchLines"), ("ArchAutoChild", "ArchAuto")):
            add_style(r, f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{sid}"/>'
                         f'<w:basedOn w:val="{base}"/></w:style>')
    return mutate(src, STYLES, add)


@pytest.mark.parametrize("sid, params, needle", [
    ("ArchLinesChild", {"spacing": {"after": 120}}, "set in lines"),
    ("ArchAutoChild", {"spacing": {"before": 120}}, "auto"),          # A3: autospacing inherited from the parent
])
def test_a_shared_refusal_rule_that_reads_only_the_style_itself_is_caught(src, monkeypatch, sid, params, needle):
    real = spp_module.inherited_refusal
    monkeypatch.setattr(spp_module, "inherited_refusal", lambda levels, values: real(levels[:1], values))
    plan, out, rec = styles_op(_children_of(src), [sid], params)
    assert "V10" in failed(rec) and needle in v10(rec) and "must refuse" in v10(rec)


def test_a_shared_refusal_rule_that_forgets_autospacing_is_caught(src, monkeypatch):
    monkeypatch.setattr(spp_module, "inherited_refusal", lambda levels, values: None)
    plan, out, rec = styles_op(src, ["ArchAuto"], {"spacing": {"before": 120}})
    assert "V10" in failed(rec) and "auto" in v10(rec) and "must refuse" in v10(rec)


# ---- A3: autospacing inherited; A2: indentation given in characters ------------------------------------------------

def test_autospacing_is_read_through_the_ancestors_and_the_nearest_level_decides(src):
    data = _children_of(src)
    with pytest.raises(JobException) as e:
        styles_op(data, ["ArchAutoChild"], {"spacing": {"before": 120}})
    assert e.value.code == "conflict-property" and "beforeAutospacing on style 'ArchAuto'" in str(e.value)
    # the child turning it off itself: the nearest level naming it decides — written, and verified
    off = mutate(data, STYLES, lambda r: style(r, "ArchAutoChild").append(etree.fromstring(
        f'<w:pPr xmlns:w="{W[1:-1]}"><w:spacing w:beforeAutospacing="0"/></w:pPr>')))
    plan, out, rec = styles_op(off, ["ArchAutoChild"], {"spacing": {"before": 120}})
    assert rec.passed, rec.to_markdown()
    assert attrs(style(styles_root(out), "ArchAutoChild").find(W + "pPr"), "spacing") == {"beforeAutospacing": "0",
                                                                                          "before": "120"}
    # the direct path reads the paragraph's style chain the same way
    with pytest.raises(JobException) as e:
        run_ops(src, [("set_paragraph_props", {"paragraphs": {"text_regex": "^Auto spacing"}},
                       {"spacing": {"before": 120}})])
    assert e.value.code == "conflict-property" and "style 'ArchAuto'" in str(e.value)


def _chars(src, part, fn):
    return mutate(src, part, fn)


def _set_ind(el_, **kw):
    ind = el_.find(W + "pPr/" + W + "ind")
    ind.attrib.clear()
    for k, v in kw.items():
        ind.set(W + k, v)


def test_indentation_given_in_characters_refuses_a_twips_value(src):
    """A2 (Word gives leftChars/firstLineChars… priority over left/firstLine): asking the twips value where the style, a
    style it is based on or the document defaults give that side in characters is refused, unless the spec gives it in
    characters too."""
    data = _chars(src, STYLES, lambda r: _set_ind(style(r, "ArchPara"), left="720", leftChars="300", firstLine="420",
                                                  firstLineChars="200"))
    for ind, needle in (({"left": 0}, "leftChars=300"), ({"firstLine": 0}, "firstLineChars=200"),
                        ({"start": 0}, "leftChars=300")):
        with pytest.raises(JobException) as e:
            styles_op(data, ["ArchPara"], {"ind": ind})
        assert e.value.code == "conflict-property" and needle in str(e.value), ind
    plan, out, rec = styles_op(data, ["ArchPara"], {"ind": {"left": 0, "leftChars": 0, "firstLine": 0,
                                                            "firstLineChars": 0}})
    assert rec.passed, rec.to_markdown()
    assert attrs(style(styles_root(out), "ArchPara").find(W + "pPr"), "ind") == {
        "left": "0", "leftChars": "0", "firstLine": "0", "firstLineChars": "0"}
    # a hanging indent asked: the merge itself removes the first-line Chars from the style — nothing left to defeat it
    plan, out, rec = styles_op(data, ["ArchPara"], {"ind": {"hanging": 360, "leftChars": 0}})
    assert rec.passed, rec.to_markdown()
    assert attrs(style(styles_root(out), "ArchPara").find(W + "pPr"), "ind") == {"left": "720", "leftChars": "0",
                                                                                 "hanging": "360"}
    # inherited: a style based on one that indents in characters
    child = mutate(data, STYLES, lambda r: add_style(r, '<w:style w:type="paragraph" w:styleId="ParaChild">'
                                                        '<w:name w:val="Para Child"/><w:basedOn w:val="ArchPara"/></w:style>'))
    with pytest.raises(JobException) as e:
        styles_op(child, ["ParaChild"], {"ind": {"hanging": 360}})            # the parent's firstLineChars stays
    assert e.value.code == "conflict-property" and "firstLineChars=200" in str(e.value)
    # the direct path: the paragraph's own ind, and its style's
    with pytest.raises(JobException) as e:
        run_ops(data, [("set_paragraph_props", {"paragraphs": {"text_regex": "^Cell item"}}, {"ind": {"left": 360}})])
    assert e.value.code == "conflict-property" and "style 'ArchPara'" in str(e.value)
    own = _chars(src, MAIN, lambda r: _set_ind(next(p for p in r.iter(W + "p") if "".join(
        t.text or "" for t in p.iter(W + "t")).startswith("Manual list")), left="720", leftChars="200", hanging="360"))
    with pytest.raises(JobException) as e:
        run_ops(own, [("set_paragraph_props", {"paragraphs": {"text_regex": "^Manual list"}},
                       {"ind": {"left": 360}, "tracked": False})])
    assert e.value.code == "conflict-property" and "the paragraph itself (leftChars=200)" in str(e.value)
    plan, out, rec = run_ops(own, [("set_paragraph_props", {"paragraphs": {"text_regex": "^Manual list"}},
                                    {"ind": {"left": 360, "leftChars": 0}, "tracked": False})])
    assert rec.passed, rec.to_markdown()


@pytest.mark.parametrize("target", ["styles", "direct"])
def test_a_shared_refusal_rule_that_forgets_characters_is_caught(src, monkeypatch, target):
    """Plan and apply share a rule that never looks at …Chars: the twips value is written beside a leftChars Word
    prefers — V10 re-derives the refusal and fails it (styles target and direct path alike)."""
    real = spp_module.inherited_refusal
    monkeypatch.setattr(spp_module, "inherited_refusal",
                        lambda levels, values: real(levels, {k: v for k, v in values.items() if k != "ind"}))
    data = _chars(src, STYLES, lambda r: _set_ind(style(r, "ArchPara"), left="720", leftChars="300"))
    if target == "styles":
        plan, out, rec = styles_op(data, ["ArchPara"], {"ind": {"left": 0}})
        assert attrs(style(styles_root(out), "ArchPara").find(W + "pPr"), "ind") == {"left": "0", "leftChars": "300"}
    else:
        plan, out, rec = run_ops(data, [("set_paragraph_props", {"paragraphs": {"text_regex": "^Cell item"}},
                                         {"ind": {"left": 360}, "author": AUTHOR})])
    assert "V10" in failed(rec) and "leftChars on style ArchPara" in v10(rec) and "must refuse" in v10(rec)


def test_the_first_line_family_merges_as_one(src):
    data = _chars(src, STYLES, lambda r: _set_ind(style(r, "ArchPara"), left="720", hanging="360", hangingChars="150"))
    plan, out, rec = styles_op(data, ["ArchPara"], {"ind": {"firstLineChars": 200}})
    assert rec.passed, rec.to_markdown()
    assert attrs(style(styles_root(out), "ArchPara").find(W + "pPr"), "ind") == {"left": "720", "firstLineChars": "200"}


# ---- A6: the assertion carries the user's own words ---------------------------------------------------------------

def test_the_assertion_carries_the_users_own_parameters(src):
    plan, out, rec = styles_op(src, ["ArchArticle", "ArchArticle"], {"spacing": {"after": 120}, "tracked": False})
    a = plan.property_assertions[0]
    assert a["params"] == {"spacing": {"after": 120}, "tracked": False} and "set" not in a and "tracked" not in a
    assert a["select"] == {"ids": ["ArchArticle", "ArchArticle"]} and rec.passed
    # a spec asking tracked on a style target the transform did not refuse: V10 fails it
    d = plan.to_dict()
    d["property_assertions"][0]["params"]["tracked"] = True
    rec = run_suite(src, out, d, "src.docx", "out.docx")
    assert "V10" in failed(rec) and "never tracked" in v10(rec)


def test_a_shared_default_line_rule_is_caught(src, monkeypatch):
    """Plan and apply share a cleaning that adds lineRule auto to every line value: V10 reads the user's own words and
    expects no lineRule."""
    real = spp_module._clean

    def auto_rule(params):
        out = real(params)
        if "line" in out.get("spacing", {}) and "lineRule" not in out["spacing"]:
            out["spacing"]["lineRule"] = "auto"
        return out
    monkeypatch.setattr(spp_module, "_clean", auto_rule)
    plan, out, rec = styles_op(src, ["ArchArticle"], {"spacing": {"line": 360}})
    assert attrs(style(styles_root(out), "ArchArticle").find(W + "pPr"), "spacing")["lineRule"] == "auto"
    assert "V10" in failed(rec) and "spacing is" in v10(rec)


def test_a_duplicate_element_is_caught(src):
    plan, out, rec = styles_op(src, ["ArchArticle"], {"spacing": {"after": 120}})
    assert rec.passed

    def dup(r):
        ppr = style(r, "ArchArticle").find(W + "pPr")
        ppr.insert(list(ppr).index(ppr.find(W + "spacing")) + 1, el("spacing", before="1440", after="1440"))
    _caught(src, plan, out, STYLES, dup, "2 w:spacing elements")
    plan, out, rec = run_ops(src, [("set_paragraph_props", {"paragraphs": {"text_regex": r"^1\.01"}},
                                    {"spacing": {"after": 120}, "tracked": False})])
    assert rec.passed

    def dup_direct(r):
        ppr = next(p for p in r.iter(W + "p") if "".join(t.text or "" for t in p.iter(W + "t")).startswith("1.01")
                   ).find(W + "pPr")
        ppr.append(el("spacing", after="1440"))
    _caught(src, plan, out, MAIN, dup_direct, "2 w:spacing elements")


def test_a_shared_already_rule_on_styles_is_checked_by_the_count(src, monkeypatch):
    """A merge that writes nothing new (so plan says 'already' and apply leaves the style) is caught: the verifier's own
    merge says the style changes."""
    monkeypatch.setattr(spp_module, "merged_attrs", lambda name, el_, val: dict(el_.attrib) if el_ is not None else {})
    plan, out, rec = styles_op(src, ["ArchArticle"], {"spacing": {"after": 120}})
    assert plan.property_assertions[0]["expected_ids"] == []
    assert "V10" in failed(rec) and "plan predicted []" in v10(rec)


# ---- conflicts -----------------------------------------------------------------------------------------------------

def _spec(*ops):
    return {"request": "r", "summary": "s", "mode": "formatting", "ops": list(ops)}


def _plan_and_verify(src, spec):
    plan, frags = plan_spec(DocxPackage(src), spec)
    work = DocxPackage(src).copy()
    for f in frags:
        get_op(f.op).apply(work, f)
    rec = run_suite(src, work.output_bytes(), plan.to_dict(), "src.docx", "out.docx")
    assert rec.passed, rec.to_markdown()
    return plan


ART_STYLE = {"op": "set_paragraph_props", "select": {"styles": {"ids": ["ArchArticle"]}},
             "params": {"spacing": {"after": 120}}}


@pytest.mark.parametrize("other", [
    {"op": "set_properties", "select": {"styles": {"ids": ["ArchArticle"]}}, "params": {"paragraph": {"keepLines": True}}},
    {"op": "set_borders_shading", "select": {"styles": {"ids": ["ArchArticle"]}}, "params": {"shading": {"fill": "EEEEEE"}}},
    {"op": "set_paragraph_props", "select": {"styles": {"ids": ["ArchArticle"]}}, "params": {"jc": "center"}},
    # the lines rule of a direct before/after reads the style (this op's style target can set afterLines there)
    {"op": "set_paragraph_props", "select": {"paragraphs": {"style_in": ["ArchArticle"]}},
     "params": {"spacing": {"before": 0}}},
    # the styles target's lines rule reads the ancestors: a change to Normal's pPr underneath
    {"op": "set_properties", "select": {"styles": {"ids": ["Normal"]}}, "params": {"paragraph": {"keepLines": True}}},
])
def test_ops_on_the_same_style_conflict(src, other):
    with pytest.raises(JobException) as e:
        plan_spec(DocxPackage(src), _spec(ART_STYLE, other))
    assert e.value.code == "conflict"


def test_other_families_and_other_styles_compose(src):
    _plan_and_verify(src, _spec(ART_STYLE, {"op": "set_properties", "select": {"styles": {"ids": ["ArchArticle"]}},
                                            "params": {"run": {"i": True}}}))
    _plan_and_verify(src, _spec(ART_STYLE, {"op": "set_paragraph_props", "select": {"styles": {"ids": ["ArchPara"]}},
                                            "params": {"jc": "both"}}))
    # direct alignment on the paragraphs of that style reads no style
    _plan_and_verify(src, _spec(ART_STYLE, {"op": "set_paragraph_props",
                                            "select": {"paragraphs": {"style_in": ["ArchArticle"]}},
                                            "params": {"jc": "center", "tracked": False}}))


def test_without_the_lines_read_a_style_lines_value_would_reach_the_direct_op_unverified(src, monkeypatch):
    """Plant the old behaviour (no reads): direct 'after 120' on the article paragraphs beside afterLines on their style
    plans and PASSES — Word would use the style's lines and ignore the 120. With the read it is a plan-time conflict."""
    lines = {"op": "set_paragraph_props", "select": {"styles": {"ids": ["ArchArticle"]}},
             "params": {"spacing": {"afterLines": 100}}}
    direct = {"op": "set_paragraph_props", "select": {"paragraphs": {"style_in": ["ArchArticle"]}},
              "params": {"spacing": {"after": 120}, "tracked": False}}
    with pytest.raises(JobException) as e:
        plan_spec(DocxPackage(src), _spec(direct, lines))
    assert e.value.code == "conflict"
    cls = type(get_op("set_paragraph_props"))
    original = cls.plan

    def plan(self, pkg, select, params):
        frag = original(self, pkg, select, params)
        frag.touches = [t for t in frag.touches if not verify_family(t[2])[0]]
        return frag
    monkeypatch.setattr(cls, "plan", plan)
    _plan_and_verify(src, _spec(direct, lines))                               # the silent pass the read prevents


def test_a_spec_runs_through_the_batch_pipeline(src, tmp_path):
    from spec_surgeon.batch import run_batch
    spec = _spec(ART_STYLE, {"op": "set_paragraph_props", "select": {"styles": {"ids": ["ArchPara"]}},
                             "params": {"ind": {"left": 1080, "hanging": 360}}})
    path = tmp_path / "21 13 13 – WET-PIPE.docx"
    path.write_bytes(src)
    batch = run_batch([str(path)], "edit-spec", {}, tmp_path / "out", workers=1, renderer="none", do_render=False,
                      spec=spec)
    f = batch.files[0]
    assert f.status == "PASS", f.record.to_markdown() if f.record else f.exception


def _spec_with_mode(mode, ops):
    return {"request": "x", "summary": "x", "assumptions": [], "questions": [], "applies_to": {"or": "all"},
            "mode": mode, "ops": ops}


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


def test_spacing_given_in_lines_refuses_a_point_value():
    def after_lines(root):
        p = list(root.iter(W + "p"))[6]
        ppr = p.find(W + "pPr")
        if ppr is None:
            ppr = etree.Element(W + "pPr"); p.insert(0, ppr)
        etree.SubElement(ppr, W + "spacing").set(W + "afterLines", "200")
    pkg = DocxPackage(_rewrite(build("21 00 12", "GOLF"), "word/document.xml", after_lines))
    op = lambda spacing: _spec_with_mode("formatting", [{"op": "set_paragraph_props", "select": {"scope": ["document"]},
                                               "params": {"spacing": spacing, "tracked": False}}])
    with pytest.raises(JobException) as e:
        plan_spec(pkg, op({"after": 120}))
    assert e.value.code == "conflict-property" and "afterLines=200" in e.value.reason
    plan, frags = plan_spec(pkg, op({"after": 120, "afterLines": 0}))        # the spec says what the lines become
    assert run_suite(pkg.data, apply_spec(pkg, frags).output_bytes(), plan.to_dict(), "a.docx", "b.docx").passed

    def style_lines(root):
        for st in root.iter(W + "style"):
            if st.get(W + "styleId") == "Normal":
                ppr = st.find(W + "pPr")
                if ppr is None:
                    ppr = etree.SubElement(st, W + "pPr")
                etree.SubElement(ppr, W + "spacing").set(W + "beforeLines", "100")
    pkg = DocxPackage(_rewrite(build("21 00 12", "GOLF"), "word/styles.xml", style_lines))
    with pytest.raises(JobException) as e:
        plan_spec(pkg, op({"before": 120}))
    assert e.value.code == "conflict-property" and "style 'Normal'" in e.value.reason
