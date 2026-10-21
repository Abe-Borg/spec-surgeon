"""``between_paragraphs.include_bounds`` takes "start" and "end" beside true and false (2026-10-21, full control): an
article is its heading up to, not including, the next heading — the selection deleting or moving an article needs —
without a negative-lookahead pattern. Both sides (ops/selectors.paragraph_selected ↔ verify/props.para_matches) select
alike on every mode; an article deleted by it verifies; a wrong value is refused; a shared wrong rule is caught."""
from __future__ import annotations

import pytest

from make_fixtures import build
from spec_surgeon.ooxml import block_full_text, paragraphs
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import selectors as selectors_module
from spec_surgeon.ops.prose import where
from spec_surgeon.ops.selectors import StyleSheet, check_paragraph_selector, paragraph_selected
from spec_surgeon.package import DocxPackage
from spec_surgeon.verify import run_suite
from spec_surgeon.verify.props import PartView, Styles, para_matches
from spec_surgeon.verify.reader import STYLES_REL, VPackage

MAIN = "word/document.xml"
SRC = build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS")
ARTICLE = {"start_regex": r"^3\.01", "end_regex": r"^4\.01"}


def selections(pred):
    pkg = DocxPackage(SRC)
    pars = paragraphs(pkg.xml(MAIN))
    sheet = StyleSheet(pkg)
    texts = [block_full_text(p) for p in pars]
    vp = VPackage(SRC)
    view = PartView(vp.xml(MAIN))
    styles = Styles(vp.xml(vp.related(STYLES_REL)))
    t_sel = [i for i, p in enumerate(pars) if paragraph_selected(pred, p, i, texts[i], sheet, all_pars=pars)]
    v_sel = [i for i, p in enumerate(view.pars) if para_matches(pred, p, i, view, styles)]
    return t_sel, v_sel, texts


@pytest.mark.parametrize("bounds,first,last", [(True, "3.01", "4.01"), (False, None, None),
                                               ("start", "3.01", "3.01"), ("end", "4.01", "4.01")])
def test_both_sides_select_alike_on_every_bound(bounds, first, last):
    t_sel, v_sel, texts = selections({"between_paragraphs": {**ARTICLE, "include_bounds": bounds}})
    assert t_sel == v_sel
    chosen = [texts[i][:4] for i in t_sel]
    assert chosen == ([first] if first == last and first else [first, last] if first else [])


def test_start_without_an_end_match_runs_to_the_last_paragraph():
    t_sel, v_sel, texts = selections({"between_paragraphs": {"start_regex": r"^24\.01", "end_regex": r"^NOTHING",
                                                             "include_bounds": "start"}})
    assert t_sel == v_sel and texts[t_sel[0]].startswith("24.01") and t_sel[-1] == len(texts) - 1
    t_end, v_end, _ = selections({"between_paragraphs": {"start_regex": r"^24\.01", "include_bounds": "end"}})
    assert t_end == v_end == t_sel[1:]


@pytest.mark.parametrize("bad", ["both", 1, 0, None, ["start"]])
def test_a_wrong_bound_is_refused(bad):
    with pytest.raises(JobException) as e:
        check_paragraph_selector({"between_paragraphs": {**ARTICLE, "include_bounds": bad}})
    assert e.value.code == "bad-params"


def test_the_sentence_names_the_bounds():
    def said(bounds):
        return where({"paragraphs": {"between_paragraphs": {**ARTICLE, "include_bounds": bounds}}})
    assert said("start") == "in paragraphs from “^3\\.01” up to, not including, “^4\\.01”"
    assert said("end") == "in paragraphs after “^3\\.01” through “^4\\.01”"
    assert said(True) == "in paragraphs between “^3\\.01” and “^4\\.01” inclusive"
    assert said(False) == "in paragraphs between “^3\\.01” and “^4\\.01”"


def _delete_article(src: bytes):
    pkg = DocxPackage(src)
    op = get_op("delete_paragraphs")
    frag = op.plan(pkg, {"paragraphs": {"between_paragraphs": {**ARTICLE, "include_bounds": "start"}}},
                   {"tracked": False})
    plan = merge_fragments([frag])
    work = pkg.copy()
    op.apply(work, frag)
    out = work.output_bytes()
    return out, run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")


def test_an_article_deleted_from_its_heading_up_to_the_next_verifies():
    out, rec = _delete_article(SRC)
    assert rec.passed, rec.to_markdown()
    texts = [block_full_text(p) for p in paragraphs(DocxPackage(out).xml(MAIN))]
    assert not any(t.startswith("3.01") for t in texts) and any(t.startswith("4.01") for t in texts)


def test_a_shared_rule_that_takes_the_end_paragraph_too_is_caught(monkeypatch):
    real = selectors_module.paragraph_selected

    def both_bounds(pred, *a, **k):          # "start" read as true by the transform side alone
        bp = (pred or {}).get("between_paragraphs") if isinstance(pred, dict) else None
        if isinstance(bp, dict) and bp.get("include_bounds") == "start":
            pred = {**pred, "between_paragraphs": {**bp, "include_bounds": True}}
        return real(pred, *a, **k)
    monkeypatch.setattr(selectors_module, "paragraph_selected", both_bounds)
    import spec_surgeon.ops.delete_paragraphs as dp
    monkeypatch.setattr(dp, "paragraph_selected", both_bounds)
    _, rec = _delete_article(SRC)
    assert not rec.passed and rec.get("V10").status == "FAIL"
