"""The words a custom method changes, listed before approval from the independent verifier's V2 record (stress-test
finding F1, P1): a built-in operation says what it changes in its own sentence and the gate traces every placed word
to the user's sources or to the reply the user read; a custom method has neither, so the window derives the words its
sample copies add and remove from the verifier's own comparison (``verify/suite.py`` V2, ``data["actual"]``) — never
from the method's plan or the model — and says them in the chat and under each sample before the button."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from make_fixtures import build
from spec_surgeon.codegen import trial
from spec_surgeon.preview import (NO_WORDS_CHANGE, WORDS_CHANGE_HEAD, SampleView, part_words, preview_generated,
                                  text_change_phrase, text_changes_lines, text_changes_of, word_changes)
from spec_surgeon.verify import run_suite
from test_codegen import GOOD_OP


def record(*actual):
    """A verification record as ``VerificationRecord.to_dict()`` writes it, with V2's ``actual`` entries."""
    return {"source": "a.docx", "output": "b.docx", "job": "j", "mode": "untracked",
            "checks": [{"id": "V1", "name": "Package hash whitelist", "status": "PASS", "summary": "", "data": {}},
                       {"id": "V2", "name": "Text preservation", "status": "PASS", "summary": "",
                        "data": {"parts": ["word/document.xml"], "actual": list(actual), "missing": [],
                                 "unpredicted": []}}]}


def change(part, before, after, before_index, after_index):
    return {"part": part, "before": before, "after": after, "before_index": before_index, "after_index": after_index}


def test_word_changes_are_the_stretches_of_words_removed_and_added():
    assert word_changes("FIRE SUPPRESSION", "WATER SUPPRESSION") == (["FIRE"], ["WATER"])
    assert word_changes("21 01 00 - 1", "21 01 00 - 1 Coordination draft.") == ([], ["Coordination draft."])
    assert word_changes("A.\tPipe shall be black steel Schedule 40.", "A.\tPipe shall be Schedule 40.") == (["black steel"], [])
    assert word_changes("one two three four", "one 2 three 4") == (["two", "four"], ["2", "4"])
    assert word_changes("a  b", "a b") == ([], [])                       # spacing only: no word changes
    assert word_changes(None, "See the portal.") == ([], ["See the portal."])    # a new block, whole
    assert word_changes("Attached.", None) == (["Attached."], [])              # a block gone, whole
    assert word_changes(None, "") == ([], []) and word_changes("", None) == ([], [])


def test_a_part_is_named_in_plain_words_from_its_usual_name_and_otherwise_as_it_is():
    assert part_words("word/document.xml") == "body" and part_words("word/footer1.xml") == "footer"
    assert part_words("word/header2.xml") == "header" and part_words("word/footnotes.xml") == "footnote"
    assert part_words("word/endnotes.xml") == "endnote" and part_words("word/comments.xml") == "comment"
    assert part_words("word/footer2.xml", several=True) == "footer 2"
    assert part_words("word/footer.xml", several=True) == "footer"        # no number to tell it by
    assert part_words("word/glossary/document.xml") == "body"
    assert part_words("word/strange.data") == "word/strange.data"         # never guessed


def test_the_derivation_reads_a_replaced_a_new_and_a_removed_block_from_the_record():
    rec = record(change("word/document.xml", "FIRE SUPPRESSION", "WATER SUPPRESSION", 1, 1),
                 change("word/document.xml", None, "Coordination draft.", None, 3),
                 change("word/document.xml", "Attached.", None, 40, None),
                 change("word/footer1.xml", "21 01 00 - 1", "21 01 00 - 1 Coordination draft.", 0, 0),
                 change("word/footer2.xml", "Cover", "Cover Coordination draft.", 0, 0),
                 change("word/footer2.xml", "second line", "second line here", 1, 1))
    got = text_changes_of(rec)
    assert got == [
        {"part": "word/document.xml", "where": "body paragraph 2", "paragraph": 1, "removed": ["FIRE"], "added": ["WATER"]},
        {"part": "word/document.xml", "where": "body paragraph 4", "paragraph": 3, "removed": [],
         "added": ["Coordination draft."], "block": "new"},
        {"part": "word/document.xml", "where": "body paragraph 41", "paragraph": 40, "removed": ["Attached."],
         "added": [], "block": "gone"},
        # two footer parts in play: Word's number tells them apart; a footer's first paragraph is "the footer"
        {"part": "word/footer1.xml", "where": "footer 1", "paragraph": 0, "removed": [], "added": ["Coordination draft."]},
        {"part": "word/footer2.xml", "where": "footer 2", "paragraph": 0, "removed": [], "added": ["Coordination draft."]},
        {"part": "word/footer2.xml", "where": "footer 2 paragraph 2", "paragraph": 1, "removed": [], "added": ["here"]},
    ]
    assert text_change_phrase(got[0]) == "words removed “FIRE”; words added “WATER”"
    assert text_change_phrase(got[1]) == "a new paragraph, words added “Coordination draft.”"
    assert text_change_phrase(got[2]) == "the whole paragraph removed, words removed “Attached.”"
    assert text_change_phrase({"removed": [], "added": []}) == "only the spacing changes, no words"
    assert text_change_phrase({"removed": [], "added": [], "block": "new"}) == "a new paragraph without words"
    assert text_change_phrase({"removed": [], "added": [], "block": "gone"}) == "an empty paragraph removed"
    # one footer part alone is "the footer"
    alone = text_changes_of(record(change("word/footer1.xml", "Cover", "Cover Coordination draft.", 0, 0)))
    assert alone[0]["where"] == "footer"
    # the same words in the record and in the verifier's object shape
    from spec_surgeon.verify import VerificationRecord
    assert text_changes_of(VerificationRecord.from_dict(record(*rec["checks"][1]["data"]["actual"]))) == got


def test_no_record_no_v2_and_no_changes_are_told_apart():
    assert text_changes_of(None) is None
    assert text_changes_of({"checks": [{"id": "V1", "status": "PASS", "data": {}}]}) is None
    assert text_changes_of(record()) == []
    assert text_changes_of({"checks": [{"id": "V2", "status": "PASS", "data": {}}]}) == []
    assert text_changes_of({"checks": [{"id": "V2", "status": "PASS", "data": {"actual": [1, "x"]}}]}) == []


def test_the_chats_lines_quote_the_exact_words_and_name_the_part_once_per_distinct_change():
    a = SampleView(Path("a/21 00 00.docx"), text_changes=[
        {"part": "word/footer1.xml", "where": "footer", "paragraph": 0, "removed": [], "added": ["Coordination draft."]}])
    b = SampleView(Path("b/21 01 00.docx"), text_changes=[
        {"part": "word/footer1.xml", "where": "footer", "paragraph": 0, "removed": [], "added": ["Coordination draft."]},
        {"part": "word/document.xml", "where": "body paragraph 2", "paragraph": 1, "removed": ["FIRE"], "added": ["WATER"]}])
    assert text_changes_lines([a, b]) == [
        WORDS_CHANGE_HEAD,
        "• 21 00 00.docx and 21 01 00.docx, footer: words added “Coordination draft.”",
        "• 21 01 00.docx, body paragraph 2: words removed “FIRE”; words added “WATER”",
    ]
    assert text_changes_lines([SampleView(Path("a.docx"), text_changes=[])]) == [NO_WORDS_CHANGE]
    assert text_changes_lines([SampleView(Path("a.docx"))]) == []           # no comparison: nothing to say
    assert text_changes_lines([SampleView(Path("a.docx")), SampleView(Path("b.docx"), text_changes=[])]) == [NO_WORDS_CHANGE]
    assert "Before you approve" in WORDS_CHANGE_HEAD and NO_WORDS_CHANGE.startswith("No words change")


@pytest.fixture
def source(tmp_path) -> Path:
    f = tmp_path / "21 01 00 – SPEC 1.docx"
    f.write_bytes(build("21 01 00", "SPEC 1", with_cover_page_field=False))
    return f


def test_a_custom_methods_preview_carries_the_words_its_copy_changes_from_the_trials_record(source, tmp_path):
    from test_governed_codegen import CONTENT_OP
    op = CONTENT_OP.replace('content == "FIRE SUPPRESSION"', 'content == "SPEC 1"').replace("Edit(0, 4,", "Edit(0, 6,")
    worded = trial(op, source, {}, {"replacement": "DRAFT"}, tmp_path / "w", unguarded=True)
    assert worded.passed and worded.unverified == [], (worded.problems, worded.unverified)
    plain = trial(GOOD_OP, source, {}, {"twips": 900}, tmp_path / "p", unguarded=True)
    assert plain.passed
    gp = preview_generated(SimpleNamespace(name="op", trials=[worded, plain]), tmp_path / "pv", renderer="none",
                           do_render=False)
    # the words are the verifier's reading of the copy, the record's V2 entries word for word
    assert gp.samples[0].text_changes == text_changes_of(worded.record.to_dict()) == [
        {"part": "word/document.xml", "where": "body paragraph 2", "paragraph": 1, "removed": ["SPEC 1"], "added": ["DRAFT"]}]
    assert gp.samples[1].text_changes == []                                   # formatting only: no words change
    assert gp.samples[0].to_dict()["text_changes"] == gp.samples[0].text_changes
    text = gp.to_text()
    assert "words removed “SPEC 1”; words added “DRAFT”" in text and "WORDS (" in text
    log = (tmp_path / "pv" / "logs" / "preview.log").read_text(encoding="utf-8")
    assert "words added “DRAFT”" in log
    # a trial the checker left no record of carries no comparison (its sample never passes)
    broken = SimpleNamespace(source=source, passed=False, record=None, output=None, problems=["x"], unverified=[])
    gp = preview_generated(SimpleNamespace(name="op", trials=[broken]), tmp_path / "pv2", renderer="none", do_render=False)
    assert gp.samples[0].text_changes is None


def test_the_verifiers_record_of_the_delivered_copy_reproduces_the_preview_s_words(source, tmp_path):
    """The disclosure is bound to the approval through the trial it is derived from: the same method on the same
    bytes gives the same V2 entries, in the preview and in the run."""
    from test_governed_codegen import CONTENT_OP
    op = CONTENT_OP.replace('content == "FIRE SUPPRESSION"', 'content == "SPEC 1"').replace("Edit(0, 4,", "Edit(0, 6,")
    first = trial(op, source, {}, {"replacement": "DRAFT"}, tmp_path / "a", unguarded=True)
    again = trial(op, source, {}, {"replacement": "DRAFT"}, tmp_path / "b", unguarded=True)
    assert text_changes_of(first.record.to_dict()) == text_changes_of(again.record.to_dict())
    # and the record of the output bytes themselves, read by the suite afresh, says the same
    rec = run_suite(source.read_bytes(), first.output.read_bytes(), {"job": "x", "mode": "untracked", "diffs": []},
                    str(source), str(first.output))
    assert text_changes_of(rec.to_dict()) == text_changes_of(first.record.to_dict())
