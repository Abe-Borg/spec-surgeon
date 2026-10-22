"""replace_text matches whole words unless ``whole_word`` is false (the rule of replace_table): "NFPA 13" leaves "NFPA 13R"
and "NFPA 13D" alone, and a tab or a line break between two words keeps them apart although replace_text's live text
runs their characters together. Plan → merge → apply → run_suite on fx_text.build_words in both modes, and the defects
the verifier must catch: a wrong output, and a wrong matching rule shared by plan and apply."""
from __future__ import annotations

import io
import zipfile

import pytest
from lxml import etree

from fx_text import build_words
from spec_surgeon.ops import JobException, get_op
from spec_surgeon.ops import replace_text as replace_text_module
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.verify import run_suite
from test_ops import failed, mutate, run_ops

AUTHOR = "Abraham Borg"
NEW = "NFPA 13 (2025 edition)"
FIRST = "Install the systems per NFPA 13. NFPA 13R and NFPA 13D systems are not permitted."
SECOND = "Where NFPA 13R applies, follow NFPA 13 for the rest."


@pytest.fixture(scope="module")
def words() -> bytes:
    return build_words()


def accepted(out: bytes):
    """Every body paragraph as it reads with every change accepted (a tab \\t, a line break \\n)."""
    root = etree.fromstring(zipfile.ZipFile(io.BytesIO(out)).read("word/document.xml"))
    shown = {W + "tab": "\t", W + "br": "\n"}
    return ["".join(shown.get(el.tag, el.text or "") for el in p.iter(W + "t", W + "tab", W + "br")
                    if el.getparent().tag == W + "r") for p in root.iter(W + "p")]


def v10(rec) -> str:
    return next(c.summary for c in rec.checks if c.id == "V10")


@pytest.mark.parametrize("tracked", [True, False])
def test_whole_words_by_default(words, tracked):
    plan, out, rec = run_ops(words, [("replace_text", {}, {"find": "NFPA 13", "replace": NEW, "tracked": tracked,
                                                           "author": AUTHOR})])
    assert rec.passed, rec.to_markdown()
    texts = accepted(out)
    assert texts[0] == FIRST.replace("NFPA 13.", NEW + ".")
    assert texts[1] == SECOND.replace("NFPA 13 for", NEW + " for")
    # what whole words left alone is said, never silent
    assert "'NFPA 13' also occurs 3× inside longer words ('NFPA 13R', 'NFPA 13D'), not changed" in " ".join(plan.flags)


def test_whole_word_false_also_matches_inside_longer_words(words):
    plan, out, rec = run_ops(words, [("replace_text", {}, {"find": "NFPA 13", "replace": NEW, "whole_word": False,
                                                           "tracked": False})])
    assert rec.passed, rec.to_markdown()
    assert accepted(out)[:2] == [FIRST.replace("NFPA 13", NEW), SECOND.replace("NFPA 13", NEW)]
    assert not any("longer words" in f for f in plan.flags)


def test_the_first_occurrence_is_the_first_whole_word(words):
    """"Where NFPA 13R applies, follow NFPA 13 …": the first occurrence asked for is the second one typed."""
    plan, out, rec = run_ops(words, [("replace_text", {"paragraphs": {"text_regex": "^Where"}},
                                      {"find": "NFPA 13", "replace": NEW, "occurrences": "first", "author": AUTHOR})])
    assert rec.passed, rec.to_markdown()
    assert accepted(out)[:2] == [FIRST, SECOND.replace("NFPA 13 for", NEW + " for")]


def test_a_tab_or_a_line_break_keeps_two_words_apart(words):
    """The live text reads "1.01SUMMARY" and "Approved by theOwner"; the tab and the line break still stand between."""
    plan, out, rec = run_ops(words, [("replace_text", {}, {"find": "SUMMARY", "replace": "SCOPE", "author": AUTHOR}),
                                     ("replace_text", {}, {"find": "Owner", "replace": "Client", "author": AUTHOR})])
    assert rec.passed, rec.to_markdown()
    texts = accepted(out)
    assert texts[2:5] == ["1.01\tSCOPE", "Owners shall review the submittals.", "Approved by the\nClient"]


def test_a_word_glued_across_runs_is_not_whole(words):
    """"Owner" and a bold "s" are one word, "Owners", whatever their runs."""
    select = {"paragraphs": {"text_regex": "^Owners"}}
    with pytest.raises(JobException) as e:
        get_op("replace_text").plan(DocxPackage(words), select, {"find": "Owner", "replace": "Client"})
    assert e.value.code == "not-found"
    assert "as a whole word; 'Owner' also occurs 1× inside longer words ('Owners')" in str(e.value)
    quiet = get_op("replace_text").plan(DocxPackage(words), select, {"find": "Owner", "replace": "Client",
                                                                     "allow_missing": True})
    assert not quiet.diffs and "'Owner' also occurs 1× inside longer words" in " ".join(quiet.flags)
    plan, out, rec = run_ops(words, [("replace_text", select, {"find": "Owner", "replace": "Client",
                                                               "whole_word": False, "tracked": False})])
    assert rec.passed, rec.to_markdown()
    assert accepted(out)[3] == "Clients shall review the submittals."


def test_a_punctuation_edge_needs_no_boundary(words):
    plan, out, rec = run_ops(words, [("replace_text", {}, {"find": "(2022)", "replace": "(2025)", "author": AUTHOR})])
    assert rec.passed, rec.to_markdown()
    assert accepted(out)[5] == "Adopted as x(2025) by the authority."


def test_whole_word_must_be_true_or_false(words):
    with pytest.raises(JobException) as e:
        get_op("replace_text").plan(DocxPackage(words), {}, {"find": "NFPA 13", "replace": NEW, "whole_word": "yes"})
    assert e.value.code == "bad-params"


# ---- planted defects ---------------------------------------------------------------------------------------------------

def test_a_replacement_inside_a_longer_word_is_caught(words):
    plan, out, rec = run_ops(words, [("replace_text", {}, {"find": "NFPA 13", "replace": NEW, "tracked": False})])
    assert rec.passed, rec.to_markdown()

    def inside_13r(root):
        t = next(t for t in root.iter(W + "t") if "NFPA 13R and" in (t.text or ""))
        t.text = t.text.replace("NFPA 13R", NEW + "R")

    rec = run_suite(words, mutate(out, "word/document.xml", inside_13r), plan.to_dict(), "s", "o")
    assert {"V2", "V10"} <= set(failed(rec))


def test_substring_matching_shared_by_plan_and_apply_is_caught(words, monkeypatch):
    """A transform that ignored whole_word predicts and writes all five edits; the verifier finds two on its own."""
    monkeypatch.setattr(replace_text_module, "whole_word", lambda live, segs, start, end: True)
    plan, out, rec = run_ops(words, [("replace_text", {}, {"find": "NFPA 13", "replace": NEW, "author": AUTHOR})])
    assert failed(rec) == ["V10"]
    assert "independently found 2 edits; predicted 5" in v10(rec)


def test_a_run_edge_read_as_a_boundary_is_caught(words, monkeypatch):
    """A transform that took every w:t edge for a word boundary changes "Owner" inside "Owners"; the verifier reads
    the run text, where nothing stands between "Owner" and the bold "s"."""
    monkeypatch.setattr(replace_text_module, "_gap_at", lambda segs, pos: True)
    plan, out, rec = run_ops(words, [("replace_text", {}, {"find": "Owner", "replace": "Client", "author": AUTHOR})])
    assert failed(rec) == ["V10"]
    assert "independently found 1 edits; predicted 2" in v10(rec)
