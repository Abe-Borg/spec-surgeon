"""The outline tool (outline.py, docs/SPEC_FORMATTER_MODES_DESIGN.md N1): the structure reader's roles are final;
Claude decides only the paragraphs the reader leaves, every one exactly once, in one-shot calls on the conversation's
model at OUTLINE_EFFORT; an answer that is not clean is asked again once, then what is still not answered stays
unplaced with why; nothing is sent when the reader leaves nothing, or too much; an API error ends Claude's reading with
what it read; asking again for the same bytes makes no call. Scripted client only — no API calls."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from fake_claude import FakeClient, api_error, resp, text, tool
from make_fixtures import build, build_canadian_outline
from spec_surgeon import compiler, llm, outline
from spec_surgeon.conversation import Conversation
from spec_surgeon.package import DocxPackage
from spec_surgeon.resources import OUTLINE_EFFORT

SPEC = build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS")       # the reader leaves 2 ("PROJECT REVISION HISTORY") and
UNRESOLVED = [2, 10]                                          # 10 ("GENERAL", a PART heading without "PART")


def roles(*pairs, tid="r1"):
    return resp(tool("submit_roles", {"paragraphs": [
        {"index": i, "role": role, "reason": why} for i, role, why in pairs]}, tid))


def scripted(*answers):
    sent = []
    queue = list(answers)

    def call(message):
        sent.append(message)
        return queue.pop(0)
    return call, sent


# ---- the classifier ------------------------------------------------------------------------------------------------

def test_claude_decides_only_what_the_reader_leaves_and_never_overrides_it():
    call, sent = scripted(roles((2, "none", "revision log heading"), (10, "part", "PART 1's title")))
    reading = outline.classify("spec.docx", DocxPackage(SPEC), call)
    assert len(sent) == 1
    assert "[2] DECIDE" in sent[0] and "[10] DECIDE" in sent[0]
    assert "[11] role=article" in sent[0]                       # context, its role fixed
    assert "Call submit_roles with a role for each DECIDE paragraph: 2, 10." in sent[0]
    assert {i: p.role for i, p in reading.proposals.items()} == {2: "none", 10: "part"}
    answer = outline.result(reading)
    digest = hashlib.sha256(SPEC).hexdigest()
    assert answer["document_sha256"] == digest
    assert answer["roles"]["part"] == {"placed_by_reader": [], "proposed_by_claude": [10], "selector": {
        "scope": ["document"], "paragraphs": {"indices_by_document": {digest: [10]}}}}
    assert answer["roles"]["article"]["proposed_by_claude"] == []
    assert answer["left_out_of_the_outline_by_claude"] == [{"paragraph": 2, "reason": "revision log heading"}]
    assert answer["unplaced"] == [] and answer["claude_calls"] == 1
    assert "say in your message which roles rest on Claude's reading" in answer["note"]


def test_an_answer_about_a_placed_paragraph_is_never_taken():
    call, _ = scripted(roles((2, "none", "log"), (10, "part", "x"), (11, "paragraph", "override")),
                       roles((2, "none", "log"), (10, "part", "x")))
    reading = outline.classify("spec.docx", DocxPackage(SPEC), call)
    assert reading.calls == 2                                    # the first answer invented a paragraph: asked again
    assert 11 not in reading.proposals
    assert reading.rows[11]["role"] == "article"


def test_a_paragraph_still_missing_after_the_retry_stays_unplaced_with_why():
    call, sent = scripted(roles((10, "part", "x")), roles((10, "part", "x")))
    reading = outline.classify("spec.docx", DocxPackage(SPEC), call)
    assert reading.calls == 2
    assert "YOUR PREVIOUS ANSWER WAS NOT USED: not answered: 2" in sent[1]
    assert set(reading.proposals) == {10}
    assert "did not give it one role" in reading.unplaced[2]


def test_twice_answered_none_without_reason_and_unknown_roles_are_not_taken():
    bad = resp(tool("submit_roles", {"paragraphs": [
        {"index": 2, "role": "none"}, {"index": 10, "role": "part", "reason": "a"},
        {"index": 10, "role": "article", "reason": "b"}]}))
    call, _ = scripted(bad, roles((2, "heading", "x"), (10, "part", "y")))
    reading = outline.classify("spec.docx", DocxPackage(SPEC), call)
    assert set(reading.proposals) == {10}
    assert 2 in reading.unplaced


def test_nothing_is_sent_when_the_reader_places_every_paragraph():
    call, sent = scripted()
    reading = outline.classify("ca.docx", DocxPackage(build_canadian_outline()), call)
    assert sent == [] and reading.calls == 0
    answer = outline.result(reading)
    assert answer["roles"]["subparagraph_2"]["placed_by_reader"] == [7]
    assert "nothing was sent to Claude" in answer["note"]


def test_nothing_is_sent_past_the_limit():
    call, sent = scripted()
    reading = outline.classify("spec.docx", DocxPackage(SPEC), call, limit=1)
    assert sent == [] and set(reading.unplaced) == set(UNRESOLVED)


def test_windows_cover_every_paragraph_to_decide_once():
    rows = [{"paragraph": i} for i in range(50)]
    decide = list(range(0, 50, 3))
    ws = outline.windows(rows, decide, chunk=5, context=2)
    assert [i for w in ws for i in w.decide] == decide
    assert all(w.context[0] == max(0, w.decide[0] - 2) and w.context[-1] == min(49, w.decide[-1] + 2) for w in ws)


def test_an_api_error_keeps_what_was_read_and_stops_sending():
    def call(message):
        raise api_error("Overloaded", status=529)
    reading = outline.classify("spec.docx", DocxPackage(SPEC), call,
                               failed=lambda e: "overloaded" if llm.is_api_error(e) else None)
    assert set(reading.unplaced) == set(UNRESOLVED) and "overloaded" in reading.unplaced[2]


def test_a_defect_in_the_call_propagates():
    def call(message):
        raise AssertionError("a defect")
    with pytest.raises(AssertionError):
        outline.classify("spec.docx", DocxPackage(SPEC), call, failed=lambda e: None)


def test_the_prompt_and_tool_are_constants_that_name_every_role():
    for role in outline.ROLE_VOCABULARY:
        assert f"- {role}:" in outline.CLASSIFY_SYSTEM
    assert outline.CLASSIFY_TOOLS[0]["input_schema"]["properties"]["paragraphs"]["items"]["properties"]["role"][
        "enum"] == list(outline.ROLE_VOCABULARY)
    assert "read_outline" in {t["name"] for t in compiler.CHAT_TOOLS}


# ---- through the conversation --------------------------------------------------------------------------------------

@pytest.fixture
def folder(tmp_path) -> Path:
    d = tmp_path / "specs"
    d.mkdir()
    (d / "21 13 13 – SPEC.docx").write_bytes(SPEC)
    return d


def test_the_conversation_reads_the_outline_once_per_file_and_counts_its_usage(folder):
    label = "21 13 13 – SPEC.docx"
    fake = FakeClient(
        resp(tool("read_outline", {"file": label}, "o1")),
        roles((2, "none", "revision log heading"), (10, "part", "PART 1's title")),
        resp(tool("read_outline", {"file": label}, "o2")),          # asked again: no call
        resp(text("PART 1's title rests on Claude's reading."), stop="end_turn"))
    c = Conversation("restyle the PART headings", sorted(folder.glob("*.docx")), client=fake)
    turn = c.start()
    assert turn.status == "questions"
    assert len(fake.requests) == 4
    nested = fake.requests[1]
    assert nested["system"] == outline.CLASSIFY_SYSTEM and nested["tools"] == outline.CLASSIFY_TOOLS
    assert nested["output_config"] == {"effort": OUTLINE_EFFORT}
    assert "cache_control" not in nested                          # one-shot: nothing after the system prompt cached
    first = json.loads(fake.requests[2]["messages"][-1]["content"][0]["content"])
    assert first["roles"]["part"]["proposed_by_claude"] == [10] and first["claude_calls"] == 1
    second = json.loads(fake.requests[3]["messages"][-1]["content"][0]["content"])
    assert second["claude_calls"] == 0 and second["note"].startswith("Read earlier in this conversation")
    assert c.usage.tokens() == 4 * 120                             # every call's tokens, the outline's included
    assert any(e.get("outline") == label for e in c.exchange)


def test_an_api_error_in_the_outline_call_is_an_answer_and_the_turn_goes_on(folder):
    label = "21 13 13 – SPEC.docx"
    import fake_claude
    fake = FakeClient(
        resp(tool("read_outline", {"file": label}, "o1")),
        fake_claude.raise_on_enter(api_error("bad request", status=400)),
        resp(text("Claude could not read the outline."), stop="end_turn"))
    c = Conversation("restyle the PART headings", sorted(folder.glob("*.docx")), client=fake)
    turn = c.start()
    assert turn.status == "questions"
    answer = json.loads(fake.requests[2]["messages"][-1]["content"][0]["content"])
    assert {u["paragraph"] for u in answer["unplaced"]} == set(UNRESOLVED)
    assert all("could not be made" in u["why"] for u in answer["unplaced"])
