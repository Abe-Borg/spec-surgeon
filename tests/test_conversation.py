"""The conversation (conversation.py): Claude and the user talk a request through until a spec, a custom transform or
a clear "not yet". Scripted fake client only — no API calls.

What is pinned here: every tool call is answered in the very next user message on every path (ask_user, accepted
spec + later reply, rejections, the call cap, tool exceptions, API errors, refusals, fallbacks); the history is
append-only and the system prompt and tools never change mid-conversation; only the user's own words and the exact
wording of a choice they picked become approved words; choices are built by code (Claude cannot offer the custom
transform itself); the custom-transform limits name only property families the verifier can check; an API error (or
Claude not installed) is a failed turn with its HTTP status and request id while a defect propagates, and either way
nothing is appended; the exchange record (``exchange``, compile.json's transcript) holds every request, answer and
tool result and never stops a turn."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from fake_claude import (FakeClient, api_error, fallback, raise_on_enter, raise_on_final, resp, text, thinking, tool)
from make_fixtures import build
from spec_surgeon import compiler as compiler_module
from spec_surgeon import llm
from spec_surgeon.catalog import CUSTOM_TRANSFORM_LIMITS
from spec_surgeon.conversation import ACCEPTED_RESULT, Conversation, ProtocolError, check_pairing

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


def test_every_conversation_sends_the_same_system_prompt_with_its_own_cache_breakpoint(folder):
    """The next request after a run is a new conversation over other files: its tools and system prompt are the same
    bytes as the last one's and carry their own (1-hour) breakpoint, so its first call reads them instead of writing
    them again; only the history behind them is new."""
    files = sorted(folder.glob("*.docx"))
    first, second = FakeClient(ask(message="Which headers?")), FakeClient(ask(message="Which font?"))
    Conversation("remove all headers and footers", files, client=first).start()
    Conversation("change the font to Arial", files[:1], client=second, history=["Removed the headers."]).start()
    a, b = first.requests[0], second.requests[0]
    assert a["messages"] != b["messages"]
    assert a["system_blocks"] == b["system_blocks"] and a["tools"] == b["tools"]
    assert [block["cache_control"] for block in a["system_blocks"]] == [llm.SYSTEM_CACHE]
    assert a["cache_control"] == b["cache_control"] == llm.MESSAGES_CACHE


def test_the_compile_result_hands_on_its_one_hour_cache_writes_at_their_price(folder):
    from types import SimpleNamespace
    from spec_surgeon.continuous_recovery import _add_usage
    answer = ask(message="Which headers?")
    answer.usage.cache_creation_input_tokens = 1000
    answer.usage.cache_creation = SimpleNamespace(ephemeral_1h_input_tokens=800, ephemeral_5m_input_tokens=200)
    c = Conversation("remove all headers and footers", sorted(folder.glob("*.docx")), client=FakeClient(answer))
    usage = c.compile_result(c.start()).usage
    assert usage[llm.CACHE_1H_KEY] == 800 and usage["cache_creation_input_tokens"] == 1000
    handed = SimpleNamespace(usage=llm.Usage())
    _add_usage(handed, {"calls": 1, "by_model": {"claude-opus-5-5": usage}})          # the command line's handoff
    assert handed.usage.cost_usd() == pytest.approx(c.usage.cost_usd())
    assert handed.usage.tokens() == c.usage.tokens()                                    # never counted twice


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
    assert t2.status == "unsupported" and t2.choices == [] and "formatting-only custom fallback" in t2.message
    assert "It needs a new built-in capability" not in t2.message
    assert "Project recovery" in t2.message
    with pytest.raises(ValueError, match="not offered"):
        outside.reply(choice_id="c99")


@pytest.mark.parametrize("failure", [raise_on_enter(api_error("503 overloaded", status=503, request_id="req_1")),
                                     raise_on_final(api_error("peer closed connection", module="httpx2"))])
def test_a_failed_call_appends_nothing_and_the_next_reply_still_pairs(folder, failure):
    fake = FakeClient(ask(questions=["Which sections?"]), failure, submit(REMOVE, "s2"))
    c = conv(folder, fake)
    c.start()
    t = c.reply("all of them")
    assert t.status == "failed" and "request to Claude failed" in t.message
    assert c.exchange[-1]["error"] == t.message.split("failed: ", 1)[1]               # kept for the diagnosis
    assert c.messages[-1]["role"] == "user"                                            # the answer was sent; nothing more
    t2 = c.reply("try again please")
    assert t2.status == "ok"
    sent = [b for m in fake.requests[2]["messages"] if isinstance(m["content"], list) for b in m["content"]
            if isinstance(b, dict) and b.get("type") == "tool_result"]
    assert [b["tool_use_id"] for b in sent] == ["a1"]                                  # answered once, never re-sent
    assert_well_formed(fake)


def test_an_api_error_says_its_status_and_request_id():
    e = api_error("Overloaded", status=529, request_id="req_01ABC")
    assert llm.failure_text(e) == "the request to Claude failed: APIStatusError: Overloaded · HTTP 529 · request id req_01ABC"


def test_a_defect_in_the_call_propagates_and_the_history_still_pairs(folder):
    fake = FakeClient(ask(questions=["Which sections?"]), raise_on_enter(RuntimeError("a defect")), submit(REMOVE, "s2"))
    c = conv(folder, fake)
    c.start()
    with pytest.raises(RuntimeError, match="a defect"):
        c.reply("all of them")
    assert c.messages[-1]["role"] == "user"                                            # nothing was appended
    assert c.reply("try again please").status == "ok"
    assert_well_formed(fake)


def test_claude_not_installed_is_a_failed_turn_and_an_unscripted_client_still_fails_loudly(folder, monkeypatch):
    files = sorted(folder.glob("*.docx"))
    unscripted = Conversation("remove all headers and footers", files)
    with pytest.raises(RuntimeError, match="no live Claude calls"):                    # conftest: not an API error
        unscripted.start()

    def missing():
        raise llm.ClaudeUnavailable('install the Claude layer (pip install "spec-surgeon[agent]")')
    monkeypatch.setattr(compiler_module, "_client", missing)
    c = Conversation("remove all headers and footers", files)
    t = c.start()
    assert t.status == "failed" and t.message.startswith("Claude is not available: install the Claude layer")
    assert len(c.messages) == 1 and "ClaudeUnavailable" in c.exchange[-1]["error"]
    fake = FakeClient(submit(REMOVE))
    monkeypatch.setattr(compiler_module, "_client", lambda: fake)                     # installed now: the next reply works
    assert c.reply("try again").status == "ok" and len(fake.requests) == 1


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


def test_equivalent_rejections_stall_and_the_next_reply_answers_the_last_call(folder):
    bad = copy.deepcopy(REMOVE)
    bad["ops"][0]["op"] = "remove_headers"
    fake = FakeClient(submit(bad, "s1"), submit(bad, "s2"), submit(bad, "s3"), submit(REMOVE, "s4"))
    c = conv(folder, fake)
    t = c.start()
    assert t.status == "failed" and "stalled" in t.errors[0]
    assert [r["tool_use_id"] for r in c.pending] == ["s3"] and "new evidence" in c.pending[0]["content"]
    assert c.reply("use the remove_header_footer operation").status == "ok"
    assert fake.requests[3]["messages"][-1]["content"][0]["tool_use_id"] == "s3"
    assert_well_formed(fake)


def test_the_call_cap_leaves_every_call_answered(folder):
    looping = [resp(tool("inspect_profile", {"pattern_id": "x"}, f"t{i}")) for i in range(12)]
    fake = FakeClient(*looping, submit(REMOVE, "s1"))
    c = conv(folder, fake, max_calls=12)
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
    assert saved["calls"] == 1 and [e["role"] for e in saved["exchange"]] == ["user", "assistant", "user"]


def test_the_exchange_records_every_call_the_pending_results_and_the_reply_as_sent(folder, tmp_path):
    fake = FakeClient(resp(text("Let me ask."), tool("ask_user", {"questions": ["Which sections?"]}, "a1")),
                      submit(REMOVE, "s2"))
    c = conv(folder, fake)
    c.start()
    t = c.reply("all of them")
    assert t.status == "ok"
    ex = c.exchange
    assert [(e["turn"], e.get("role"), bool(e.get("pending"))) for e in ex] == [
        (0, "user", False), (1, "assistant", False), (1, "user", True), (1, "user", False), (2, "assistant", False),
        (2, "user", True)]
    assert ex[0]["content"].startswith("REQUEST:") and ex[0]["files"] == [str(f) for f in c.files]
    first = ex[1]
    assert first["stop_reason"] == "tool_use" and first["model"] == "claude-opus-5-5" and first["usage"]["input_tokens"] == 100
    assert [b["type"] for b in first["content"]] == ["text", "tool_use"] and first["content"][1]["id"] == "a1"
    assert ex[2]["tool_results"] == [{"tool_use_id": "a1", "is_error": False, "content": "(waiting for the user)"}]
    sent = ex[3]["content"]                                   # the user's message as sent: their words in the result
    assert sent[0]["type"] == "tool_result" and sent[0]["content"] == "The user replied: all of them"
    assert ex[5]["tool_results"][0]["content"] == ACCEPTED_RESULT
    res = c.compile_result(t)
    assert res.transcript == ex and res.seconds == c.seconds >= 0 and res.to_dict()["transcript"] == ex
    saved = json.loads(c.save(tmp_path / "conversation.json").read_text(encoding="utf-8"))
    assert saved["exchange"] == json.loads(json.dumps(ex, default=str)) and "messages" not in saved


def test_a_block_the_record_cannot_read_never_stops_the_turn(folder):
    from types import SimpleNamespace
    odd = SimpleNamespace(type="text", text="Looking.", input=5)          # an input that is not a mapping
    c = conv(folder, FakeClient(resp(odd, tool("submit_editspec", {"spec": REMOVE}, "s1"))))
    t = c.start()
    assert t.status == "ok"
    blocks = c.exchange[1]["content"]
    assert blocks[0]["type"] == "text" and "record_error" in blocks[0] and blocks[1]["id"] == "s1"


def test_the_client_is_resolved_when_the_first_call_is_made(folder, monkeypatch):
    fake = FakeClient(submit(REMOVE))
    c = Conversation("remove all headers and footers", sorted(folder.glob("*.docx")))
    monkeypatch.setattr(compiler_module, "_client", lambda: fake)
    assert c.start().status == "ok" and len(fake.requests) == 1
