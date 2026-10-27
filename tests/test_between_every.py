"""``between_paragraphs.every``: every stretch, from each start match to the next start or end match (backlog Q1) —
"empty every article, keep the titles", "delete every PART 2" — where the one-stretch form takes the first start
match only. Both sides (ops/selectors.paragraph_selected ↔ verify/props.para_matches) select alike on every bound,
with and without an end pattern; the one-stretch form is unchanged; every article emptied by it verifies; a wrong
value is refused on both sides; a shared wrong rule is caught."""
from __future__ import annotations

import io

import pytest
from docx import Document

from spec_surgeon.ooxml import block_full_text, paragraphs
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import selectors as selectors_module
from spec_surgeon.ops.prose import where
from spec_surgeon.ops.selectors import StyleSheet, check_paragraph_selector, paragraph_selected
from spec_surgeon.package import DocxPackage
from spec_surgeon.verify import run_suite
from spec_surgeon.verify.goal_operations import paragraph_selector
from spec_surgeon.verify.props import PartView, Styles, para_matches
from spec_surgeon.verify.reader import STYLES_REL, VPackage

MAIN = "word/document.xml"
LINES = ["SECTION 21 13 13", "WET-PIPE SPRINKLER SYSTEMS",                                   # 0, 1
         "PART 1 - GENERAL",                                                                 # 2
         "1.01\tSUMMARY", "A.\tSection includes wet-pipe sprinkler systems.", "B.\tRelated: 21 05 00.",   # 3, 4, 5
         "1.02\tREFERENCES", "A.\tNFPA 13 (2025).",                                          # 6, 7
         "PART 2 - PRODUCTS",                                                                # 8
         "2.01\tPIPE", "A.\tBlack steel, Schedule 40.", "B.\tFittings: grooved.",            # 9, 10, 11
         "PART 3 - EXECUTION",                                                               # 12
         "3.01\tINSTALLATION", "A.\tPer NFPA 13.",                                           # 13, 14
         "END OF SECTION"]                                                                   # 15
ARTICLES = {"start_regex": r"^\d+\.\d+", "end_regex": r"^PART|^END OF SECTION", "every": True}


def _spec() -> bytes:
    doc = Document()
    for line in LINES:
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


SRC = _spec()


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
    return t_sel, v_sel


@pytest.mark.parametrize("bounds,expected", [
    (False, [4, 5, 7, 10, 11, 14]),                       # every article's body, its title and the next heading kept
    ("start", [3, 4, 5, 6, 7, 9, 10, 11, 13, 14]),        # every article with its title, up to the next heading
    ("end", [4, 5, 6, 7, 8, 10, 11, 12, 14, 15]),         # every body through the next heading
    (True, list(range(3, 16))),                           # titles, bodies and the headings that end them
])
def test_both_sides_select_every_stretch_alike_on_every_bound(bounds, expected):
    t_sel, v_sel = selections({"between_paragraphs": {**ARTICLES, "include_bounds": bounds}})
    assert t_sel == v_sel == expected


def test_without_an_end_pattern_each_stretch_runs_to_the_next_start_and_the_last_to_the_end():
    t_sel, v_sel = selections({"between_paragraphs": {"start_regex": r"^\d+\.\d+", "every": True}})
    assert t_sel == v_sel == [4, 5, 7, 8, 10, 11, 12, 14, 15]


def test_every_part_two_is_one_stretch_per_file():
    pred = {"between_paragraphs": {"start_regex": "^PART 2", "end_regex": "^PART 3", "every": True,
                                   "include_bounds": "start"}}
    t_sel, v_sel = selections(pred)
    assert t_sel == v_sel == [8, 9, 10, 11]


def test_the_one_stretch_form_is_unchanged_and_takes_the_first_start_only():
    pred = {"between_paragraphs": {"start_regex": r"^\d+\.\d+", "end_regex": r"^PART|^END OF SECTION"}}
    t_sel, v_sel = selections(pred)
    assert t_sel == v_sel == [4, 5, 6, 7]                 # 1.02 and its body: the first stretch runs on to PART 2
    off, v_off = selections({"between_paragraphs": {**ARTICLES, "every": False}})
    assert off == v_off == [4, 5, 6, 7]


@pytest.mark.parametrize("bad", ["yes", 1, 0, None, ["every"]])
def test_a_wrong_every_is_refused_on_both_sides(bad):
    pred = {"between_paragraphs": {**ARTICLES, "every": bad}}
    with pytest.raises(JobException) as e:
        check_paragraph_selector(pred)
    assert e.value.code == "bad-params"
    with pytest.raises(ValueError):
        selections(pred)
    with pytest.raises(ValueError):
        paragraph_selector(pred)
    paragraph_selector({"between_paragraphs": ARTICLES})      # a goal contract takes the boolean form


def test_the_sentence_names_every_stretch():
    def said(**extra):
        return where({"paragraphs": {"between_paragraphs": {**ARTICLES, **extra}}})
    nxt = "the next “^PART|^END OF SECTION” or “^\\d+\\.\\d+”"
    assert said() == f"in paragraphs between each “^\\d+\\.\\d+” and {nxt}"
    assert said(include_bounds="start") == f"in paragraphs from each “^\\d+\\.\\d+” up to, not including, {nxt}"
    assert said(include_bounds="end") == f"in paragraphs after each “^\\d+\\.\\d+” through {nxt}"
    assert said(include_bounds=True) == f"in paragraphs from each “^\\d+\\.\\d+” through {nxt}"
    assert where({"paragraphs": {"between_paragraphs": {"start_regex": "^PART 2", "every": True}}}) == \
        "in paragraphs between each “^PART 2” and the next “^PART 2”"


def _empty_articles(src: bytes):
    pkg = DocxPackage(src)
    op = get_op("delete_paragraphs")
    frag = op.plan(pkg, {"paragraphs": {"between_paragraphs": ARTICLES}}, {"tracked": False})
    plan = merge_fragments([frag])
    work = pkg.copy()
    op.apply(work, frag)
    out = work.output_bytes()
    return out, run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")


def test_every_article_emptied_with_its_title_kept_verifies():
    out, rec = _empty_articles(SRC)
    assert rec.passed, rec.to_markdown()
    texts = [block_full_text(p) for p in paragraphs(DocxPackage(out).xml(MAIN))]
    assert texts == ["SECTION 21 13 13", "WET-PIPE SPRINKLER SYSTEMS", "PART 1 - GENERAL", "1.01SUMMARY",
                     "1.02REFERENCES", "PART 2 - PRODUCTS", "2.01PIPE", "PART 3 - EXECUTION", "3.01INSTALLATION",
                     "END OF SECTION"]


def test_a_shared_rule_that_takes_the_first_stretch_only_is_caught(monkeypatch):
    real = selectors_module.paragraph_selected

    def first_only(pred, *a, **k):            # "every" ignored by the transform side alone
        bp = (pred or {}).get("between_paragraphs") if isinstance(pred, dict) else None
        if isinstance(bp, dict) and bp.get("every"):
            pred = {**pred, "between_paragraphs": {k2: v for k2, v in bp.items() if k2 != "every"}}
        return real(pred, *a, **k)
    monkeypatch.setattr(selectors_module, "paragraph_selected", first_only)
    import spec_surgeon.ops.delete_paragraphs as dp
    monkeypatch.setattr(dp, "paragraph_selected", first_only)
    _, rec = _empty_articles(SRC)
    assert not rec.passed and rec.get("V10").status == "FAIL"


def test_a_shared_rule_that_runs_each_stretch_past_the_next_title_is_caught(monkeypatch):
    real = selectors_module.paragraph_selected

    def to_the_end_match(pred, *a, **k):      # a stretch stopped by end_regex alone, swallowing the next title
        bp = (pred or {}).get("between_paragraphs") if isinstance(pred, dict) else None
        if isinstance(bp, dict) and bp.get("every") and bp.get("end_regex"):
            pred = {**pred, "between_paragraphs": {**bp, "start_regex": r"^1\.01", "every": False}}
        return real(pred, *a, **k)
    monkeypatch.setattr(selectors_module, "paragraph_selected", to_the_end_match)
    import spec_surgeon.ops.delete_paragraphs as dp
    monkeypatch.setattr(dp, "paragraph_selected", to_the_end_match)
    _, rec = _empty_articles(SRC)
    assert not rec.passed and rec.get("V10").status == "FAIL"
