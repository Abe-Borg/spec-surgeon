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
from spec_surgeon.compiler import _Batch, check_submission, compile_request, placed_words, untraceable
from spec_surgeon.conversation import (ACCEPTED_RESULT, Conversation, INVISIBLE_RANGES, ProtocolError, check_pairing,
                                       clean_wording, restates)

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


def conv(folder, fake, request="remove all headers and footers", **kw) -> Conversation:
    return Conversation(request, sorted(folder.glob("*.docx")), client=fake, **kw)


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


def test_one_hour_cache_writes_reach_the_records_and_keep_their_price_when_handed_on(folder):
    from types import SimpleNamespace
    from spec_surgeon.continuous_recovery import _add_usage
    answer = ask(message="Which headers?")
    answer.usage.cache_creation_input_tokens = 1000
    answer.usage.cache_creation = SimpleNamespace(ephemeral_1h_input_tokens=800, ephemeral_5m_input_tokens=200)
    c = Conversation("remove all headers and footers", sorted(folder.glob("*.docx")), client=FakeClient(answer))
    usage = c.compile_result(c.start()).usage                                           # compile.json's usage
    assert usage[llm.CACHE_1H_KEY] == 800 and usage["cache_creation_input_tokens"] == 1000
    # the outcome planner's spending joins the conversation's ledger from its budget record (continuous_recovery)
    handed = SimpleNamespace(usage=llm.Usage())
    _add_usage(handed, c.usage.to_dict())
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


EDITED = "ISSUED FOR BID 2026-11-09"
PLACES_EDITED = {**PLACES_STAMP, "ops": [{**PLACES_STAMP["ops"][0],
                                          "params": {**PLACES_STAMP["ops"][0]["params"], "replace": EDITED}}]}


def test_an_edited_wording_is_the_approved_one_and_the_draft_is_not(folder):
    """The user edits Claude's draft before picking it. The edited text is what
    picking approves — Claude is told the exact words — and the draft it replaced is never approved: a spec placing it
    is refused, a spec placing the edit passes."""
    fake = FakeClient(
        ask(message="Which words should the stamp say?",
            choices=[{"label": "Use the permit stamp", "kind": "alternative", "wording": STAMP}]),
        submit(PLACES_STAMP, "s1"),
        submit(PLACES_EDITED, "s2"))
    c = conv(folder, fake)
    t = c.start()
    assert t.choices[0].editable and t.choices[0].to_dict()["editable"] is True
    t2 = c.reply(choice_id=t.choices[0].id, wording="  " + EDITED + " ")
    sent = fake.requests[1]["messages"][-1]["content"][0]["content"]
    assert "changed its wording first" in sent and f"«{EDITED}»" in sent and STAMP not in sent
    assert EDITED in c.sources and STAMP not in c.sources
    rejected = fake.requests[2]["messages"][-1]["content"][0]
    assert rejected["is_error"] and "never gave them verbatim" in rejected["content"]     # the draft: not the user's
    assert t2.status == "ok" and not untraceable(PLACES_EDITED, c.sources)
    said = [e.text for e in c.entries if e.role == "user"][-1]
    assert said.startswith("Chose: Use the permit stamp — exact words, as you edited them")
    assert_well_formed(fake)


def test_an_unchanged_or_bad_edit_is_a_plain_pick_or_refused_before_anything_is_said(folder):
    fake = FakeClient(
        ask(message="Which words?", choices=[{"label": "Use the permit stamp", "kind": "alternative", "wording": STAMP},
                                             {"label": "Only the body sections", "kind": "clarify"}]),
        submit(PLACES_STAMP, "s1"))
    c = conv(folder, fake)
    t = c.start()
    for bad in ("", "   ", "ISSUED\nFOR BID", "ISSUED\u200bFOR BID"):
        with pytest.raises(ValueError, match="one line of visible characters"):
            c.reply(choice_id=t.choices[0].id, wording=bad)
    with pytest.raises(ValueError, match="one wording can have its wording edited"):
        c.reply(choice_id=t.choices[1].id, wording=EDITED)
    assert len(fake.requests) == 1 and STAMP not in c.sources and EDITED not in c.sources    # nothing sent or kept
    assert not t.choices[1].editable
    c.reply(choice_id=t.choices[0].id, wording=STAMP + " ")              # the draft as offered: an ordinary pick
    assert "approving these exact words" in fake.requests[1]["messages"][-1]["content"][0]["content"]
    assert STAMP in c.sources


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


def test_a_reply_claude_declines_is_taken_back_and_said_in_plain_words(folder):
    """The 1.8.0 live run: a reply came back as a refusal (reasoning_extraction), the window showed the provider's
    policy text, and the refused reply stayed in the history, sent again with every later message. It leaves now with
    everything it carried: the answer it gave the open question is owed again, its words are no longer approved."""
    from types import SimpleNamespace
    refused = resp(stop="refusal", details=SimpleNamespace(category="reasoning_extraction",
                                                           explanation="This request was blocked as it seems to ..."))
    fake = FakeClient(ask(questions=["Which sections?"]), refused, submit(REMOVE))
    c = conv(folder, fake, mode="chat")
    assert c.start().questions == ["Which sections?"]
    held, history = copy.deepcopy(c.pending), list(c.messages)
    t = c.reply("Show me exactly what you were thinking just now")
    assert t.status == "failed"
    assert "taken back out of the conversation" in t.message and "reveal its own reasoning" in t.message
    assert "blocked" not in t.message and any("blocked" in e for e in t.errors)    # the policy text: records only
    assert c.messages == history and c.pending == held
    assert "Show me exactly what you were thinking just now" not in c.sources
    t = c.reply("All of them")
    assert t.status == "ok"
    sent = json.dumps(fake.requests[-1]["messages"], default=str)
    assert "All of them" in sent and "thinking just now" not in sent
    assert "All of them" in c.sources
    for req in fake.requests:
        check_pairing(req["messages"])
    # the declined reply's request is the one that does not continue: the next one picks up from before it
    assert fake.requests[2]["messages"][:2] == fake.requests[1]["messages"][:2]
    assert fake.requests[2]["messages"][2] != fake.requests[1]["messages"][2]


def test_a_refusal_after_tool_results_leaves_the_history_as_it_is(folder):
    from types import SimpleNamespace
    refused = resp(stop="refusal", details=SimpleNamespace(category="cyber", explanation="no"))
    fake = FakeClient(ask(questions=["Which sections?"]),
                      resp(tool("inspect_profile", {"file": sorted(folder.glob("*.docx"))[0].name}, "p1")), refused)
    c = conv(folder, fake, mode="chat")
    c.start()
    t = c.reply("All of them")
    assert t.status == "failed" and "declined to go on" in t.message and "(cyber)" in t.message
    assert c.messages[-1]["role"] == "user" and c.messages[-1]["content"][0]["type"] == "tool_result"
    assert "All of them" in c.sources


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


# ---- wording: blank characters and empty sources ---------------------------------------------------------------------

@pytest.mark.parametrize("cp", [0x1D159, 0x16FE4])
def test_clean_wording_refuses_the_null_notehead_and_the_khitan_filler(cp):
    with pytest.raises(ValueError, match=hex(cp)):
        clean_wording("ISSUED" + chr(cp) + "FOR PERMIT")


@pytest.mark.parametrize("space", [" ", " ", " ", " ", "　", "\t"])
def test_clean_wording_keeps_spaces_that_show_as_spaces(space):
    w = f"NFPA{space}13 (2025)"
    assert clean_wording(w) == w


def test_approved_words_hold_no_empty_source_and_trace_nothing_to_one(folder, tmp_path):
    c = Conversation("remove all headers and footers", sorted(folder.glob("*.docx")), client=FakeClient(),
                     answers=["sections 1-3 only", "", "   "])
    rec = json.loads(c.save(tmp_path / "conversation.json").read_text(encoding="utf-8"))
    assert rec["approved_words"] == c.sources == ["remove all headers and footers", "sections 1-3 only"]
    placing = {"request": "r", "summary": "s", "mode": "content", "ops": [{"op": "replace_text",
               "select": {"scope": ["document"]}, "params": {"find": "FM Global", "replace": "{}", "tracked": True,
                                                            "author": "x"}}]}
    assert untraceable(placing, c.sources) == ["{}"]            # an empty source once traced the words "{}"


# ---- a malformed submission, questions and choices in the wrong shape, the user's request ----------------------------

PERMIT_STAMP = "ISSUED FOR PERMIT 2026-11-02"
MALFORMED = [
    ({"review": [{"sample_files": 2}]}, "'review' must be an object"),
    ({"review": ["sample_files"]}, "'review' must be an object"),
    ({"review": "source"}, "'review' must be an object"),
    ({"applies_to": [{"pattern_ids": ["p1"]}]}, "'applies_to' must be an object"),
    ({"applies_to": "all"}, "'applies_to' must be an object"),
    ({"assertions": True}, "'assertions' must be a list"),
    ({"assertions": {"kind": "effective_font", "value": "Arial"}}, "'assertions' must be a list"),
    ({"assumptions": "the cover is section 0"}, "'assumptions' must be a list of strings"),
    ({"assumptions": 3}, "'assumptions' must be a list of strings"),
    ({"assumptions": ["ok", 3]}, "'assumptions' must be a list of strings"),
    ({"request": 7}, "'request' must be text"),
    ({"summary": None}, "'summary' must be text"),
    ({"proposal": ["x"]}, "'proposal' must be text"),
    ({"applies_to": {"pattern_ids": "b20e6c1da8"}}, "pattern_ids must be a list"),
    ({"applies_to": {"pattern_ids": [["b20e6c1da8"]]}}, "pattern_ids must be a list"),
    ({"applies_to": {"or": "any"}}, "applies_to.or can only be"),
    ({"review": {"sample_files": "two"}}, "sample_files must be a whole number"),
    ({"review": {"sample_files": 0}}, "sample_files must be a whole number"),
    ({"review": {"sample_files": -1}}, "sample_files must be a whole number"),
    ({"review": {"sample_files": True}}, "sample_files must be a whole number"),
    ({"mode": ["formatting"]}, "mode must be one of"),
]


INVISIBLE = [0x3164, 0x115F, 0x1160, 0xFFA0, 0x2800, 0xFE0F, 0xFE00, 0x034F, 0x17B4, 0x17B5, 0x180B, 0x180F, 0x061C,
             0x2028, 0x2029, 0xE0100, 0xE01EF, 0xE0041, 0x1BCA0, 0x1D173, 0x00AD, 0x200B, 0x200D, 0x202E, 0x2060,
             0x2066, 0xFEFF, 0xE000, 0x0378, 0x0007, 0x0085]
CLAUDES = "Remove the headers and footers and replace NFPA 13 (2025) with NFPA 13 (2028) throughout"


@pytest.mark.parametrize("change, message", MALFORMED[:6])
def test_check_submission_answers_a_malformed_spec_with_errors(folder, change, message):
    spec = {**copy.deepcopy(REMOVE), **change}
    errs = check_submission(spec, _Batch(sorted(folder.glob("*.docx")), None), [spec["request"]])
    assert errs and errs[0].startswith("[spec]") and message in errs[0]


def test_a_malformed_spec_goes_back_to_claude_as_rejected_and_the_conversation_pairs(folder):
    bad = {**copy.deepcopy(REMOVE), "review": [{"sample_files": 2}]}
    fake = FakeClient(submit(bad, "s1"), submit(REMOVE, "s2"), ask("a3", questions=["Anything else?"]))
    c = conv(folder, fake)
    t = c.start()
    assert t.status == "ok" and any("'review' must be an object" in e for e in t.errors)
    rejected = fake.requests[1]["messages"][-1]["content"][0]
    assert rejected["tool_use_id"] == "s1" and rejected["is_error"]
    assert rejected["content"].startswith("REJECTED") and "'review' must be an object" in rejected["content"]
    assert c.reply("also keep the logo").status == "questions"             # the next reply still pairs
    assert_well_formed(fake)


def test_a_check_that_raises_is_answered_as_rejected(folder, monkeypatch):
    def boom(spec, batch, sources, patterns=None, leave_out=(), unguarded=False, shown=()):
        raise TypeError("unhashable type: 'dict'")
    monkeypatch.setattr(compiler_module, "compose_submission", boom)
    fake = FakeClient(submit(REMOVE, "s1"), ask("a2", questions=["Which sections?"]),
                      ask("a3", questions=["And the cover?"]))
    c = conv(folder, fake)
    t = c.start()
    assert t.status == "questions" and t.questions == ["Which sections?"]
    rejected = fake.requests[1]["messages"][-1]["content"][0]
    assert rejected["tool_use_id"] == "s1" and rejected["is_error"]
    assert "the spec could not be checked (TypeError: unhashable type: 'dict')" in rejected["content"]
    assert any("could not be checked" in e for e in t.errors)
    assert c.reply("all of them").status == "questions"                    # no ProtocolError: s1 was answered
    sent = fake.requests[2]["messages"][-1]["content"]
    assert sent[0]["tool_use_id"] == "a2" and "all of them" in sent[0]["content"]
    assert_well_formed(fake)


def test_ask_user_questions_as_one_string_are_one_question(folder):
    fake = FakeClient(ask(questions="Which sections?"))
    t = conv(folder, fake).start()
    assert t.questions == ["Which sections?"]
    assert any("questions came as one text" in e for e in t.errors)


def test_a_spec_s_questions_as_one_string_are_one_question(folder):
    spec = {"request": "r", "summary": "s", "mode": "formatting", "ops": [], "questions": "Which sections?"}
    fake = FakeClient(submit(spec))
    t = conv(folder, fake).start()
    assert t.status == "questions" and t.questions == ["Which sections?"]


def test_non_list_questions_and_choices_are_dropped_and_said_never_raised(folder):
    fake = FakeClient(ask(questions=3, choices="Use the permit stamp", message="Which stamp?"))
    c = conv(folder, fake)
    t = c.start()
    assert t.status == "questions" and t.questions == [] and t.choices == [] and t.message == "Which stamp?"
    assert any("questions were dropped" in e for e in t.errors)
    assert any("choices were dropped" in e for e in t.errors)
    fake2 = FakeClient(ask(questions=["Which?", 5], choices=[{"label": "A", "kind": "clarify"}, "B"]))
    t2 = conv(folder, fake2).start()
    assert t2.questions == ["Which?"] and [ch.label for ch in t2.choices] == ["A"]
    assert any("were not text" in e for e in t2.errors) and any("not an object" in e for e in t2.errors)
    spec = {"request": "r", "summary": "s", "mode": "formatting", "ops": [], "questions": 3}
    t3 = conv(folder, FakeClient(submit(spec))).start()
    assert t3.status == "questions" and t3.questions == ["(the model asked an empty question)"]
    assert any("questions were dropped" in e for e in t3.errors)


EVERYTHING = ("Do everything else on all four now; leave the tracked changes in the three files for me to accept in "
              "Word")
REATTACH = "I'll accept the changes in Word first and attach the files again"


@pytest.mark.parametrize("chip, label, same", [
    ("Do everything else now, I'll accept in Word", EVERYTHING, True),       # the owner's report, 2026-10-29
    ("I'll accept in Word first and re-attach", REATTACH, True),
    ("Do everything else now, I'll accept in Word", REATTACH, False),
    ("I'll accept in Word first and re-attach", EVERYTHING, False),
    ("Yes", "Yes, change the headers too", True),
    ("Only Valves", "Change only Valves", True),
    ("Valves only", "Change only Valves", False),                              # in another order: kept
    ("Keep the headers and change the footers", "Change the headers and keep the footers", False),   # other targets
    ("Don't change the headers, change the footers", "Change the headers, don't change the footers", False),
    ("Leave out the fourth for now", "Change the three that have a stamp; leave the fourth as it is", False),
    ("Set the fourth aside for later", "Give the fourth the same stamp in its header", False),
    ("Don't change the headers", "Change the headers", False),                 # the opposite answer
    ("Change the headers", "Don't change the headers", False),
    ("Leave out Valves", "Leave out Valves and Pumps", False),                 # names another document
    ("Leave out 21 05 23", "Leave out 21 05 23 and 21 05 29", False),
    ("Leave out 21 05 23", "Leave out 21 05 29", False),                       # another number
    ("Keep headers", "Change headers", False),
    ("Everything, including the headers", "Just the body text", False),
])
def test_a_suggested_reply_restates_a_choice_only_when_it_says_nothing_else(chip, label, same):
    assert restates(chip, label) is same


def test_a_suggested_reply_that_restates_a_choice_is_dropped_and_the_others_stay(folder):
    replies = ["Do everything else now, I'll accept in Word", "I'll accept in Word first and re-attach",
               "Set Valves aside for later"]
    fake = FakeClient(ask(message="Two ways to get there.", suggested_replies=replies,
                          choices=[{"label": EVERYTHING, "kind": "alternative"},
                                   {"label": REATTACH, "kind": "clarify"}]))
    t = conv(folder, fake).start()
    assert [ch.label for ch in t.choices] == [EVERYTHING, REATTACH]
    assert t.chips == ["Set Valves aside for later"]
    assert any("2 suggested replies restating a choice dropped" in e for e in t.errors)
    t2 = conv(folder, FakeClient(ask(message="Which titles?", suggested_replies=replies))).start()
    assert t2.chips == replies and t2.errors == []                 # without choices, the replies are the answers


def test_the_chat_prompt_says_each_choice_once():
    prompt = compiler_module.system_prompt(chat=True)
    assert "suggested reply repeats a choice in other words" in prompt
    assert "without listing the choices again" in prompt
    ask_user = next(t for t in compiler_module.CHAT_TOOLS if t["name"] == "ask_user")["input_schema"]["properties"]
    assert "never a choice again in other words" in ask_user["suggested_replies"]["description"]
    assert "never the choices again" in ask_user["message"]["description"]


def test_the_choices_of_an_answered_turn_cannot_be_picked_after_the_next_turn_failed(folder):
    fake = FakeClient(ask(message="Which stamp?", choices=[{"label": "The permit stamp", "kind": "clarify",
                                                             "wording": PERMIT_STAMP}]),
                      raise_on_enter(RuntimeError("a defect in the next turn")))
    c = conv(folder, fake)
    old = c.start().choices[0]
    with pytest.raises(RuntimeError, match="a defect"):
        c.reply("the other one")
    assert c.turn is None
    with pytest.raises(ValueError, match="not offered any more"):
        c.choice(old.id)
    with pytest.raises(ValueError, match="not offered any more"):
        c.reply(choice_id=old.id)
    assert PERMIT_STAMP not in c.sources


@pytest.mark.parametrize("cp", INVISIBLE, ids=[f"U+{cp:04X}" for cp in INVISIBLE])
def test_clean_wording_refuses_a_character_the_user_cannot_see(cp):
    with pytest.raises(ValueError, match="invisible or control"):
        clean_wording("ISSUED" + chr(cp) + " FOR PERMIT")


def test_clean_wording_refuses_every_default_ignorable_code_point_and_keeps_visible_text():
    for a, b in INVISIBLE_RANGES:
        for cp in range(a, b + 1):
            with pytest.raises(ValueError):
                clean_wording("A" + chr(cp) + "B")
    for ok in (PERMIT_STAMP, "ISSUED\tFOR PERMIT", "Révision 2", "été", "NFPA 13 (2025)", "ISSUED FOR",
               "08 71 00 – DOOR HARDWARE", "§ 3.2 ½\""):
        assert clean_wording(ok) == ok


def test_a_choice_with_a_hidden_character_is_dropped_and_never_approved(folder):
    hidden = "ISSUEDㅤ FOR PERMIT"
    fake = FakeClient(ask(message="Which stamp?", choices=[{"label": "Use it", "kind": "alternative",
                                                            "wording": hidden},
                                                           {"label": "Other", "kind": "clarify"}]))
    c = conv(folder, fake)
    t = c.start()
    assert [ch.label for ch in t.choices] == ["Other"]
    assert any("Use it" in e and "0x3164" in e for e in t.errors)
    assert all(hidden not in s for s in c.sources)


def test_an_accepted_spec_carries_the_user_s_request_not_claude_s(folder):
    fake = FakeClient(submit(dict(REMOVE, request=CLAUDES), "s1"))
    c = conv(folder, fake)
    t = c.start()
    assert t.status == "ok" and t.spec["request"] == c.request == "remove all headers and footers"
    assert {k: v for k, v in t.spec.items() if k != "request"} == {k: v for k, v in REMOVE.items() if k != "request"}
    sent = [b for b in c.messages[1]["content"] if getattr(b, "type", None) == "tool_use"
            or (isinstance(b, dict) and b.get("type") == "tool_use")]
    claude_input = sent[0].input if not isinstance(sent[0], dict) else sent[0]["input"]
    assert claude_input["spec"]["request"] == CLAUDES                     # the history keeps the call as made
    # the advisor's sources (placed_words + the request + answers) no longer hold Claude's words
    respec = {"request": "r", "summary": "s", "mode": "content", "ops": [{
        "op": "replace_text", "select": {"scope": ["document"]},
        "params": {"find": "NFPA 13 (2025)", "replace": "NFPA 13 (2028)", "tracked": True, "author": "x"}}]}
    assert untraceable(respec, [*placed_words(t.spec), str(t.spec.get("request", ""))]) == ["NFPA 13 (2028)"]
    assert c.compile_result(t).spec["request"] == c.request
    assert json.loads(json.dumps(c.to_dict()))["turn"]["spec"]["request"] == c.request


def test_compile_mode_and_unsupported_specs_carry_the_user_s_request(folder):
    res = compile_request("strip every header and footer", sorted(folder.glob("*.docx")),
                          client=FakeClient(submit(dict(REMOVE, request=CLAUDES))))
    assert res.status == "ok" and res.spec["request"] == "strip every header and footer"
    unsupported = {"request": CLAUDES, "summary": "s", "unsupported": "no op", "proposal": "p"}
    c = conv(folder, FakeClient(submit(unsupported, custom_transform="possible")))
    t = c.start()
    assert t.status == "unsupported" and t.spec["request"] == c.request and c.unsupported["request"] == c.request
    assert CLAUDES not in c.brief()


def test_conversation_json_holds_the_approved_words_and_the_answers(folder, tmp_path):
    fake = FakeClient(ask(message="Which stamp?", choices=[{"label": "Permit", "kind": "clarify",
                                                            "wording": PERMIT_STAMP}]),
                      ask("a2", questions=["Anything else?"]))
    c = conv(folder, fake, answers=["sections 1-3 only"])
    t = c.start()
    c.reply("and the cover too", choice_id=t.choices[0].id)
    rec = json.loads(c.save(tmp_path / "conversation.json").read_text(encoding="utf-8"))
    assert rec["approved_words"] == c.sources
    assert rec["approved_words"] == ["remove all headers and footers", "sections 1-3 only", PERMIT_STAMP,
                                     "and the cover too"]
    assert rec["answers"] == ["sections 1-3 only"]
