"""The conversation (conversation.py): Claude and the user talk a request through until a spec, a custom transform or
a clear "not yet". Scripted fake client only — no API calls.

What is pinned here: every tool call is answered in the very next user message on every path (ask_user, accepted
spec + later reply, rejections, the call cap, tool exceptions, API errors, refusals, fallbacks); the history is
append-only and the system prompt and tools never change mid-conversation; only the user's own words and the exact
wording of a choice they picked become approved words; choices are built by code (Claude cannot offer the custom
transform itself); the custom-transform limits name only property families the verifier can check."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from fake_claude import (FakeClient, fallback, raise_on_enter, raise_on_final, resp, text, thinking, tool)
from make_fixtures import build
from spec_surgeon import compiler as compiler_module
from spec_surgeon.catalog import CUSTOM_TRANSFORM_LIMITS
from spec_surgeon.conversation import Conversation, ProtocolError, check_pairing

SPECS = Path(__file__).resolve().parent.parent / "examples" / "specs"
REMOVE = json.loads((SPECS / "remove-all-headers-footers.json").read_text(encoding="utf-8"))
STAMP = "ISSUED FOR PERMIT 2026-11-02"
PLACES_STAMP = {"request": "r", "summary": "s", "mode": "content", "ops": [{
    "op": "replace_text", "select": {"scope": ["document"]},
    "params": {"find": "SPEC 0", "replace": STAMP, "tracked": True, "author": "x"}}]}
UNSUPPORTED = {"request": "make the article titles bold", "summary": "Bold every article title.",
               "unsupported": "No operation sets bold.", "proposal": "set w:b on runs of ARTICLE paragraphs"}


@pytest.fixture
def folder(tmp_path) -> Path:
    d = tmp_path / "specs"
    d.mkdir()
    for i in range(2):
        (d / f"21 0{i} 00 – SPEC {i}.docx").write_bytes(build(f"21 0{i} 00", f"SPEC {i}"))
    return d


def conv(folder, fake, **kw) -> Conversation:
    return Conversation("remove all headers and footers", sorted(folder.glob("*.docx")), client=fake, **kw)


def ask(tid="a1", **inp):
    return resp(tool("ask_user", inp, tid))


def submit(spec, tid="s1", **extra):
    return resp(tool("submit_editspec", {"spec": spec, **extra}, tid))


def assert_well_formed(fake):
    """Every recorded request pairs every tool call, ends with the user, and extends the previous request; the system
    prompt and the tools never change."""
    for i, req in enumerate(fake.requests):
        check_pairing(req["messages"])
        if i:
            prev = fake.requests[i - 1]
            assert req["system"] == prev["system"] and req["tools"] == prev["tools"]
            assert req["messages"][:len(prev["messages"])] == prev["messages"]          # append-only


def test_choices_reply_by_choice_approves_its_exact_wording(folder):
    fake = FakeClient(
        ask(understanding="You want the cover stamp replaced.", message="Which words should the stamp say?",
            choices=[{"label": "Use the permit stamp", "kind": "alternative", "wording": STAMP},
                     {"label": "Only the body sections", "kind": "clarify"},
                     {"label": "I'll write code", "kind": "custom_transform"}]),
        submit(PLACES_STAMP))
    c = conv(folder, fake)
    t = c.start()
    assert t.status == "questions" and t.understanding.startswith("You want")
    assert [ch.kind for ch in t.choices] == ["alternative", "clarify"]                 # Claude cannot offer the transform
    assert any("custom_transform" in e for e in t.errors)
    assert STAMP not in " ".join(c.sources)                                            # offered, not approved
    t2 = c.reply(choice_id=t.choices[0].id)
    assert t2.status == "ok", t2.errors
    sent = fake.requests[1]["messages"][-1]["content"]
    assert sent[0]["type"] == "tool_result" and sent[0]["tool_use_id"] == "a1"
    assert "approving these exact words" in sent[0]["content"] and STAMP in sent[0]["content"]
    assert STAMP in c.sources
    assert_well_formed(fake)


def test_claude_s_suggested_words_are_refused_until_the_user_picks_them(folder):
    fake = FakeClient(
        ask(message="Shall I use this stamp?", choices=[{"label": "Use it", "kind": "clarify", "wording": STAMP}]),
        submit(PLACES_STAMP, "s1"),
        ask("a2", questions=["Please type the exact stamp."]))
    c = conv(folder, fake)
    t = c.start()
    c.reply("yes, that one")                                                           # typed, but not the words
    rejected = fake.requests[2]["messages"][-1]["content"][0]
    assert rejected["is_error"] and "never gave them verbatim" in rejected["content"]
    assert c.turn.status == "questions" and t.choices[0].wording == STAMP
    assert_well_formed(fake)


def test_reply_after_a_preview_sends_the_result_the_app_notes_then_the_user_s_words(folder):
    fake = FakeClient(submit(REMOVE, "s1"), submit(REMOVE, "s2"))
    c = conv(folder, fake)
    assert c.start().status == "ok"
    c.note("PREVIEW: 2 file(s), 2 planned, 0 set aside.")
    t = c.reply("keep the cover footer")
    assert t.status == "ok"
    last = fake.requests[1]["messages"][-1]["content"]
    assert last[0] == {"type": "tool_result", "tool_use_id": "s1", "content": last[0]["content"]}
    assert "PREVIEW: 2 file(s)" in last[1]["text"] and last[1]["text"].startswith("[note from the app]")
    assert last[2]["text"] == "The user replied: keep the cover footer"
    assert "keep the cover footer" in c.sources and "PREVIEW" not in " ".join(c.sources)
    assert [e.role for e in c.entries] == ["user", "claude", "app", "user", "claude"]
    assert_well_formed(fake)


def test_unsupported_offers_the_custom_transform_only_when_claude_judged_it_possible(folder):
    c = conv(folder, FakeClient(submit(UNSUPPORTED, custom_transform="possible")))
    t = c.start()
    assert t.status == "unsupported" and [ch.kind for ch in t.choices] == ["custom_transform"]
    assert c.unsupported["proposal"].startswith("set w:b")
    with pytest.raises(ValueError, match="carried out by the app"):
        c.reply(choice_id=t.choices[0].id)
    chosen = c.take_app_choice(t.choices[0].id)
    assert chosen.kind == "custom_transform" and "The user chose" in c.notes[0]
    assert "WHY NO OPERATION FITS: No operation sets bold." in c.brief()
    outside = conv(folder, FakeClient(submit(UNSUPPORTED, custom_transform="outside_limits",
                                             custom_transform_reason="bold is not verifiable yet")))
    t2 = outside.start()
    assert t2.status == "unsupported" and t2.choices == [] and "new built-in capability" in t2.message
    with pytest.raises(ValueError, match="not offered"):
        outside.reply(choice_id="c99")


@pytest.mark.parametrize("failure", [raise_on_enter(RuntimeError("503 overloaded")),
                                     raise_on_final(RuntimeError("stream dropped"))])
def test_a_failed_call_appends_nothing_and_the_next_reply_still_pairs(folder, failure):
    fake = FakeClient(ask(questions=["Which sections?"]), failure, submit(REMOVE, "s2"))
    c = conv(folder, fake)
    c.start()
    t = c.reply("all of them")
    assert t.status == "failed" and "request to Claude failed" in t.message
    assert c.messages[-1]["role"] == "user"                                            # the answer was sent; nothing more
    t2 = c.reply("try again please")
    assert t2.status == "ok"
    sent = [b for m in fake.requests[2]["messages"] if isinstance(m["content"], list) for b in m["content"]
            if isinstance(b, dict) and b.get("type") == "tool_result"]
    assert [b["tool_use_id"] for b in sent] == ["a1"]                                  # answered once, never re-sent
    assert_well_formed(fake)


def test_tool_exceptions_become_error_results(folder, monkeypatch):
    def boom(**kw):
        raise OSError("disk unplugged")
    fake = FakeClient(resp(tool("sample_paragraphs", {"file": "nope.docx"}, "t1"),
                           tool("sample_paragraphs", {"file": sorted(folder.glob("*.docx"))[0].name, "regex": "("}, "t2"),
                           tool("inspect_profile", {"pattern_id": "x"}, "t3"), tool("view_everything", {}, "t4")),
                      submit(REMOVE, "s1"))
    c = conv(folder, fake)
    monkeypatch.setattr(c.batch, "inspect_profile", boom)
    assert c.start().status == "ok"
    results = fake.requests[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2", "t3", "t4"] and all(r["is_error"] for r in results)
    assert "OSError" in results[2]["content"] and "unknown tool" in results[3]["content"]
    assert_well_formed(fake)


def test_second_rejection_fails_the_turn_and_the_next_reply_answers_it(folder):
    bad = copy.deepcopy(REMOVE)
    bad["ops"][0]["op"] = "remove_headers"
    fake = FakeClient(submit(bad, "s1"), submit(bad, "s2"), submit(REMOVE, "s3"))
    c = conv(folder, fake)
    t = c.start()
    assert t.status == "failed" and "rejected twice" in t.errors[0]
    assert [r["tool_use_id"] for r in c.pending] == ["s2"] and "user decides" in c.pending[0]["content"]
    assert c.reply("use the remove_header_footer operation").status == "ok"
    assert fake.requests[2]["messages"][-1]["content"][0]["tool_use_id"] == "s2"
    assert_well_formed(fake)


def test_the_call_cap_leaves_every_call_answered(folder):
    looping = [resp(tool("inspect_profile", {"pattern_id": "x"}, f"t{i}")) for i in range(12)]
    fake = FakeClient(*looping, submit(REMOVE, "s1"))
    c = conv(folder, fake)
    t = c.start()
    assert t.status == "failed" and "12 calls" in t.message and [r["tool_use_id"] for r in c.pending] == ["t11"]
    assert c.reply("just submit the remove spec").status == "ok"
    assert_well_formed(fake)


def test_refusal_max_tokens_and_pause_turn_fail_without_touching_the_history(folder):
    from types import SimpleNamespace
    for stop in ("refusal", "max_tokens", "pause_turn"):
        r = resp(text("partial"), stop=stop,
                 details=SimpleNamespace(category="cyber", explanation="no") if stop == "refusal" else None)
        c = conv(folder, FakeClient(r))
        t = c.start()
        assert t.status == "failed" and len(c.messages) == 1, stop


def test_a_fallback_s_abandoned_tool_call_is_neither_echoed_nor_acted_on(folder):
    r = resp(thinking("A"), tool("ask_user", {"questions": ["stale?"]}, "pre"), fallback(), thinking("B"),
             tool("submit_editspec", {"spec": REMOVE}, "post"), model="claude-opus-4-8")
    fake = FakeClient(r, submit(REMOVE, "s2"))
    c = conv(folder, fake)
    t = c.start()
    assert t.status == "ok" and t.questions == []
    ids = [getattr(b, "id", None) for b in c.messages[-1]["content"]]
    assert "pre" not in ids and "post" in ids
    assert c.usage.served == ["claude-opus-4-8"]
    c.reply("fine")
    assert_well_formed(fake)


def test_plain_text_is_a_message_in_chat_and_a_failure_after_a_nudge_in_compile(folder):
    c = conv(folder, FakeClient(resp(text("Do you mean the cover too?"), stop="end_turn")))
    t = c.start()
    assert t.status == "questions" and t.message == "Do you mean the cover too?" and c.pending == []
    one_shot = conv(folder, FakeClient(resp(text("hmm"), stop="end_turn"), resp(text("hmm"), stop="end_turn")),
                    mode="compile")
    assert one_shot.start().status == "failed"


def test_wording_with_invisible_characters_is_dropped(folder):
    c = conv(folder, FakeClient(ask(choices=[{"label": "Use it", "kind": "clarify", "wording": "ISSUED​FOR"},
                                             {"label": "Fine", "kind": "clarify", "wording": "ISSUED FOR"}])))
    t = c.start()
    assert [ch.wording for ch in t.choices] == ["ISSUED FOR"] and any("invisible" in e for e in t.errors)


def test_the_pairing_check_refuses_broken_histories():
    a = {"role": "assistant", "content": [{"type": "tool_use", "id": "x", "name": "n", "input": {}}]}
    with pytest.raises(ProtocolError):
        check_pairing([{"role": "user", "content": "hi"}, a])                      # trailing assistant = prefill
    with pytest.raises(ProtocolError):
        check_pairing([{"role": "user", "content": "hi"}, a, {"role": "user", "content": [{"type": "text", "text": "?"}]}])
    check_pairing([{"role": "user", "content": "hi"}, a,
                   {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}])


def test_the_custom_transform_limits_name_only_families_the_verifier_checks():
    from spec_surgeon.verify.props import KINDS
    assert set(CUSTOM_TRANSFORM_LIMITS["families"].values()) <= set(KINDS)
    assert "custom_transform_limits" in compiler_module.system_prompt(chat=True)


def test_the_record_holds_the_transcript_and_usage_never_the_history(folder, tmp_path):
    c = conv(folder, FakeClient(submit(REMOVE)))
    c.start()
    saved = json.loads(c.save(tmp_path / "conversation.json").read_text(encoding="utf-8"))
    assert saved["entries"][0]["text"] == "remove all headers and footers" and "messages" not in saved
    assert saved["usage"]["calls"] == 1 and saved["usage"]["served_models"] == ["claude-opus-5-5"]


def test_the_client_is_resolved_when_the_first_call_is_made(folder, monkeypatch):
    fake = FakeClient(submit(REMOVE))
    c = Conversation("remove all headers and footers", sorted(folder.glob("*.docx")))
    monkeypatch.setattr(compiler_module, "_client", lambda: fake)
    assert c.start().status == "ok" and len(fake.requests) == 1
