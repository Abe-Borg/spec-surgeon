"""The paragraph selector ``indices`` (docs/SPEC_FORMATTER_MODES_DESIGN.md N2): the outline tool's chosen paragraphs,
listed by their ordinals in the part. End to end with set_font, the transform's and the verifier's readings compared,
bad lists refused, the sentence it writes, and a wrong implementation shared by plan and apply caught by V10 alone."""
from __future__ import annotations

from typing import List, Tuple

import pytest

from make_fixtures import build, build_formatted
from spec_surgeon.ops import apply_style as apply_style_module
from spec_surgeon.ooxml import block_full_text, paragraphs
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import prose
from spec_surgeon.ops import selectors as selectors_module
from spec_surgeon.ops.selectors import MAX_INDICES, StyleSheet, check_paragraph_selector, paragraph_selected
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.verify import props as vprops
from spec_surgeon.verify import run_suite
from spec_surgeon.verify.reader import STYLES_REL, VPackage


@pytest.fixture(scope="module")
def src() -> bytes:
    return build_formatted()


@pytest.fixture(scope="module")
def plain() -> bytes:
    return build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS")


def styles_of(data: bytes) -> List[str]:
    pkg = DocxPackage(data)
    sheet = StyleSheet(pkg)
    return [sheet.paragraph_style_id(p) for p in paragraphs(pkg.xml(pkg.main_part()))]


def run_ops(src: bytes, specs: List[Tuple[str, dict, dict]]):
    pkg = DocxPackage(src)
    frags = [get_op(o).plan(pkg, sel, par) for o, sel, par in specs]
    plan = merge_fragments(frags)
    work = pkg.copy()
    for f in frags:
        get_op(f.op).apply(work, f)
    out = work.output_bytes()
    return plan, out, run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")


def failed(rec):
    return [c.id for c in rec.checks if c.status == "FAIL"]


def texted(src: bytes) -> List[int]:
    """The ordinals of the body paragraphs that hold text."""
    pkg = DocxPackage(src)
    return [i for i, p in enumerate(paragraphs(pkg.xml(pkg.main_part()))) if block_full_text(p).strip()]


def test_apply_style_on_chosen_paragraphs_restyles_those_and_passes(plain):
    chosen = [10, 11, 13]
    sel = {"scope": ["document"], "paragraphs": {"indices": chosen}}
    plan, out, rec = run_ops(plain, [("apply_style", sel, {"style": "BodyText"})])
    assert rec.passed, failed(rec)
    after = styles_of(out)
    assert [i for i, s in enumerate(after) if s == "BodyText"] == chosen


def test_indices_and_other_keys_are_anded(src):
    chosen = texted(src)
    pkg = DocxPackage(src)
    sheet = StyleSheet(pkg)
    pars = paragraphs(pkg.xml(pkg.main_part()))
    pred = {"indices": chosen, "is_empty": True}
    assert not any(paragraph_selected(pred, p, i, block_full_text(p), sheet, pars) for i, p in enumerate(pars))


@pytest.mark.parametrize("bad", [[], [1, 1], [-1], [True], [1.0], "3", [0] * 0 + list(range(MAX_INDICES + 1))])
def test_a_list_that_is_not_distinct_paragraph_numbers_is_refused(bad):
    with pytest.raises(JobException) as e:
        check_paragraph_selector({"indices": bad})
    assert e.value.code == "bad-params"


def test_transform_and_verifier_readings_agree(src):
    pkg, vpkg = DocxPackage(src), VPackage(src)
    sheet = StyleSheet(pkg)
    styles = vprops.Styles(vpkg.xml(vpkg.related(STYLES_REL)))
    main = pkg.main_part()
    pars = paragraphs(pkg.xml(main))
    view = vprops.PartView(vpkg.xml(main))
    vpars = view.pars
    assert len(vpars) == len(pars)
    for chosen in ([0], [2, 5, 7], list(range(0, len(pars), 2)), [len(pars) + 3]):
        pred = {"indices": chosen}
        t = [paragraph_selected(pred, p, i, block_full_text(p), sheet, pars) for i, p in enumerate(pars)]
        v = [vprops.para_matches(pred, vp, i, view, styles) for i, vp in enumerate(vpars)]
        assert t == v, chosen
        assert sum(t) == len([i for i in chosen if i < len(pars)])


def test_the_sentence_names_the_paragraphs_by_their_number_from_one():
    assert "numbered 1, 5, 10" in prose._paragraph_words({"indices": [4, 0, 9]})
    many = prose._paragraph_words({"indices": list(range(20))})
    assert "numbered 1, 2, 3, 4, 5, 6 and 14 more" in many


def test_a_shared_selector_that_ignores_indices_is_caught(plain, monkeypatch):
    """Plan and apply both forget the list: the whole range around it is chosen. The verifier reads the list itself."""
    real = selectors_module.paragraph_selected

    def forgets(pred, *a, **k):
        if isinstance(pred, dict) and "indices" in pred:
            pred = {k2: v for k2, v in pred.items() if k2 != "indices"} | {"index_range": [10, 14]}
        return real(pred, *a, **k)
    monkeypatch.setattr(apply_style_module, "paragraph_selected", forgets)
    sel = {"scope": ["document"], "paragraphs": {"indices": [10, 13]}}
    _, out, rec = run_ops(plain, [("apply_style", sel, {"style": "BodyText"})])
    assert [i for i, s in enumerate(styles_of(out)) if s == "BodyText"] == [10, 11, 12, 13]
    assert "V10" in failed(rec)


# ---- chosen paragraphs per document (indices_by_document): resolved per file, pinned for the verifier -------------

import copy
import hashlib

from spec_surgeon.editspec import SpecError, apply_spec, plan_spec, validate_spec


def restyle_spec(table) -> dict:
    return {"request": "r", "summary": "s", "mode": "formatting", "ops": [{
        "op": "apply_style", "select": {"scope": ["document"], "paragraphs": {"indices_by_document": table}},
        "params": {"style": "BodyText"}}]}


def run_spec(data: bytes, spec: dict):
    pkg = DocxPackage(data)
    plan, frags = plan_spec(pkg, spec)
    out = apply_spec(pkg, frags).output_bytes()
    return plan, out, run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")


def test_one_spec_gives_each_document_its_own_chosen_paragraphs(plain):
    other = build("21 13 19", "PREACTION SPRINKLER SYSTEMS")
    table = {hashlib.sha256(plain).hexdigest(): [10, 11], hashlib.sha256(other).hexdigest(): [13]}
    for data, want in ((plain, [10, 11]), (other, [13])):
        plan, out, rec = run_spec(data, restyle_spec(table))
        assert rec.passed, failed(rec)
        assert [i for i, s in enumerate(styles_of(out)) if s == "BodyText"] == want


def test_a_document_the_table_does_not_name_is_refused(plain):
    other = build("21 13 19", "PREACTION SPRINKLER SYSTEMS")
    with pytest.raises(JobException) as e:
        run_spec(plain, restyle_spec({hashlib.sha256(other).hexdigest(): [10]}))
    assert e.value.code == "not-outlined"


@pytest.mark.parametrize("pred", [{"indices": [1]}, {"indices": [1], "document_sha256": "a" * 64},
                                  {"indices_by_document": {}}, {"indices_by_document": {"ABC": [1]}},
                                  {"indices_by_document": [1, 2]}])
def test_a_spec_cannot_name_bare_paragraph_numbers(pred):
    spec = restyle_spec({})
    spec["ops"][0]["select"]["paragraphs"] = pred
    with pytest.raises(SpecError):
        validate_spec(spec)


def test_a_plan_pinned_to_another_document_fails_the_verifier(plain):
    """The plan's selection names another document's digest (a shared resolver picking the wrong entry): the verifier
    hashes the source itself."""
    plan, out, rec = run_spec(plain, restyle_spec({hashlib.sha256(plain).hexdigest(): [10, 11]}))
    assert rec.passed
    bad = copy.deepcopy(plan.to_dict())
    bad["property_assertions"][0]["select"]["paragraphs"]["document_sha256"] = "0" * 64
    rec2 = run_suite(plain, out, bad, "src.docx", "out.docx")
    assert "V10" in failed(rec2)
    assert any("chosen in another document" in e for c in rec2.checks if c.id == "V10" for e in c.data.get("errors", []))
