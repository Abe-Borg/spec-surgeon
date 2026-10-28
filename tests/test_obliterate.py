"""Obliterate mode (CLAUDE.md, Modes): the conversation without the guardrails that protect a document's
content, hidden unless Settings → Advanced shows it, chosen only after its warning, never restored at a launch.

What is pinned here: what it lifts — the placed-words trace at the one gate, the custom method's plain-formatting
limits, and the quarantine of a copy whose checks fail (delivered to ``outputs/`` marked UNVERIFIED, its failed checks
beside it) — happens only in Obliterate mode, and Safe and Default keep every guardrail; what it keeps — copies only,
the source guard, the sandbox and its whitelist, the verifier on every copy — holds in it too; the mode needs the
setting and the acknowledgement, voids the plan when entered or left, and is never what a launch opens in."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fake_claude import FakeClient, resp, text, tool
from make_fixtures import build
from spec_surgeon import batch as batch_module, codegen, compiler, preview
from spec_surgeon.batch import load_set_aside, run_batch
from spec_surgeon.chain import chain_from_specs, run_chain
from spec_surgeon.codegen import trial
from spec_surgeon.compiler import _Batch, compose_submission
from spec_surgeon.conversation import Conversation
from spec_surgeon.document_task import (LAUNCH_MODES, MODES, OBLITERATE_OFF, OBLITERATE_ON, REPLAN,
                                         DocumentTask)
from spec_surgeon.gui import Session
from spec_surgeon.settings import Settings, load_settings, save_settings
from spec_surgeon.verify import CheckResult
from spec_surgeon.web.server import TOKEN_HEADER, WindowState, create_app, launch_mode
from test_codegen import GOOD_OP, TEXT_OP, UNASSERTED_OP
from test_document_task import REQUEST, SPEC, document
from test_web_server import TOKEN, FakeUpdates, frames

STAMP = "ISSUED FOR PERMIT 2026-11-02"
PLACES_STAMP = {"request": "r", "summary": "s", "mode": "content", "ops": [{
    "op": "replace_text", "select": {"scope": ["document"]},
    "params": {"find": "SPEC 0", "replace": STAMP, "tracked": True, "author": "x"}}]}
UNSUPPORTED = {"request": "move the logo into a text box", "summary": "Put the logo in a text box.",
               "unsupported": "No operation makes a text box.", "proposal": "wrap the header picture in a text box"}
REPLACE = {"request": "replace FM Global with GRC", "summary": "s", "mode": "content",
           "ops": [{"op": "replace_text", "select": {"scope": ["document"]},
                    "params": {"find": "FM Global", "replace": "GRC", "author": "Abraham Borg"}}]}


@pytest.fixture
def folder(tmp_path) -> Path:
    d = tmp_path / "specs"
    d.mkdir()
    for i in range(2):
        (d / f"21 0{i} 00 – SPEC {i}.docx").write_bytes(build(f"21 0{i} 00", f"SPEC {i}"))
    return d


@pytest.fixture
def planted_v2(monkeypatch):
    """Every copy fails V2 (text preservation): the verifier read the output and said no."""
    real = batch_module.run_suite

    def failing(src, out, plan, s, o):
        rec = real(src, out, plan, s, o)
        rec.set(CheckResult("V2", "Text preservation", "FAIL", "planted"))
        return rec
    monkeypatch.setattr(batch_module, "run_suite", failing)


def task_for(tmp_path, fake=None, **kwargs):
    fake = fake or FakeClient(resp(tool("submit_editspec", {"spec": SPEC})))
    return DocumentTask(Session(client=fake, out_root=tmp_path / "out", visual_check=False), render=False, **kwargs)


# ---- choosing it ----------------------------------------------------------------------------------------------------

def test_obliterate_is_hidden_until_the_setting_shows_it_and_needs_the_warning_each_time(tmp_path):
    assert MODES == ("safe", "default", "obliterate") and LAUNCH_MODES == ("safe", "default")
    task = task_for(tmp_path)
    assert task.available_modes() == ["safe", "default"]
    with pytest.raises(ValueError, match="hidden"):
        task.set_mode("obliterate", acknowledged=True)
    with pytest.raises(ValueError, match="mode must be one of"):
        task_for(tmp_path, mode="obliterate")                  # a window never opens in it unless it is shown
    task.obliterate_allowed = True
    assert task.available_modes() == ["safe", "default", "obliterate"]
    for ack in (False, "yes", 1, None):
        with pytest.raises(ValueError, match="warning"):
            task.set_mode("obliterate", acknowledged=ack)       # only an explicit True is an acknowledgement
    assert task.mode == "default" and not task.session.unguarded
    task.set_mode("obliterate", acknowledged=True)
    assert task.mode == "obliterate" and task.session.unguarded
    assert task.messages[-1]["text"] == OBLITERATE_ON
    task.set_mode("default")
    assert task.mode == "default" and not task.session.unguarded
    assert task.messages[-1]["text"] == OBLITERATE_OFF
    with pytest.raises(ValueError, match="warning"):
        task.set_mode("obliterate")                             # the warning again, every time
    with pytest.raises(ValueError, match="no such mode"):
        task.set_mode("reckless")


def test_entering_or_leaving_obliterate_voids_the_plan(tmp_path):
    path = document(tmp_path / "Spec.docx")
    task = task_for(tmp_path, obliterate_allowed=True)
    task.attach([path])
    view = task.perform("send", text=REQUEST)
    assert view["stage"] == "preview"
    task.set_mode("obliterate", acknowledged=True)
    assert task.stage == "resume" and task.session.preview is None      # Continue plans the request again
    assert task.messages[-1]["text"] == OBLITERATE_ON + REPLAN
    with pytest.raises(ValueError):
        task.perform("primary", token=view["token"])            # a plan made under Default's rules never runs here
    task.set_mode("safe")
    assert task.session.preview is None and not task.session.unguarded


def test_a_launch_never_opens_in_obliterate(tmp_path, monkeypatch):
    monkeypatch.setenv("SPEC_SURGEON_CONFIG_DIR", str(tmp_path / "config"))
    assert launch_mode("obliterate") == "default" and launch_mode("safe") == "safe"
    save_settings(Settings(mode="safe", show_obliterate=True))
    path = Path(tmp_path / "config" / "settings.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["mode"] = "obliterate"                                 # written by hand: still not restored
    path.write_text(json.dumps(data), encoding="utf-8")
    kept = load_settings()
    assert kept.mode == "safe" and kept.show_obliterate is True
    assert load_settings().mode in LAUNCH_MODES


def window(tmp_path, *, show=False):
    session = Session(client=FakeClient(resp(tool("submit_editspec", {"spec": SPEC}))), out_root=tmp_path / "out",
                      visual_check=False)
    task = DocumentTask(session, render=False, obliterate_allowed=show)
    state = WindowState(task, Settings(show_obliterate=show), token=TOKEN,
                        updates=FakeUpdates(), version="1.6.0")
    return state, TestClient(create_app(state), headers={TOKEN_HEADER: TOKEN})


def test_the_window_shows_it_from_settings_and_keeps_default_on_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("SPEC_SURGEON_CONFIG_DIR", str(tmp_path / "config"))
    state, client = window(tmp_path)
    assert client.get("/api/settings").json()["show_obliterate"] is False
    assert client.get("/api/state").json()["modes"] == ["safe", "default"]
    assert client.post("/api/mode", json={"mode": "obliterate", "acknowledged": True}).status_code == 400
    assert client.post("/api/settings", json={"show_obliterate": "yes"}).status_code == 400
    assert client.post("/api/settings", json={"show_obliterate": True}).json()["show_obliterate"] is True
    assert load_settings().show_obliterate is True
    assert client.get("/api/state").json()["modes"] == ["safe", "default", "obliterate"]
    refused = client.post("/api/mode", json={"mode": "obliterate"})
    assert refused.status_code == 400 and "warning" in refused.json()["error"]
    view = client.post("/api/mode", json={"mode": "obliterate", "acknowledged": True}).json()
    assert view["mode"] == "obliterate" and state.task.session.unguarded
    assert load_settings().mode == "default"                    # never kept: the next launch opens in Default
    # hiding it again leaves the mode
    assert client.post("/api/settings", json={"show_obliterate": False}).status_code == 200
    view = client.get("/api/state").json()
    assert view["mode"] == "default" and view["modes"] == ["safe", "default"] and not state.task.session.unguarded
    # a new conversation keeps the mode it was in, and the setting
    client.post("/api/settings", json={"show_obliterate": True})
    client.post("/api/mode", json={"mode": "obliterate", "acknowledged": True})
    client.post("/api/new")
    assert state.task.mode == "obliterate" and state.task.obliterate_allowed and state.task.session.unguarded


# ---- the words ------------------------------------------------------------------------------------------------------

SHOWN = f"Each file gets the stamp “{STAMP}” in place of “SPEC 0”."


def test_the_gate_traces_placed_words_to_the_user_or_in_obliterate_to_the_reply_the_user_reads(folder):
    files = sorted(folder.glob("*.docx"))
    _, errs = compose_submission(PLACES_STAMP, _Batch(files, None), ["replace SPEC 0"])
    assert any("untraceable" in e or STAMP in e for e in errs), errs
    # Obliterate: Claude's words may be placed, but only once the reply the user reads shows them whole (the window
    # shows no plan card, so the chat is the one place the user can read what will be placed)
    _, errs = compose_submission(PLACES_STAMP, _Batch(files, None), ["replace SPEC 0"], unguarded=True)
    assert len(errs) == 1 and "the user has not read them" in errs[0] and STAMP in errs[0], errs
    _, errs = compose_submission(PLACES_STAMP, _Batch(files, None), ["replace SPEC 0"], unguarded=True,
                                 shown=["Each file gets the stamp ISSUED FOR PERMIT in place of SPEC 0."])
    assert len(errs) == 1 and STAMP in errs[0]                                 # a piece of the words is not the words
    outcome, errs = compose_submission(PLACES_STAMP, _Batch(files, None), ["replace SPEC 0"], unguarded=True,
                                       shown=[SHOWN])
    assert errs == [] and outcome == PLACES_STAMP
    # the user's own words still count, shown or not
    outcome, errs = compose_submission(PLACES_STAMP, _Batch(files, None), [f"stamp each one {STAMP}"], unguarded=True)
    assert errs == [] and outcome == PLACES_STAMP
    # everything else at the gate is the same: an op that does not exist is refused either way
    bad = dict(PLACES_STAMP, ops=[{"op": "no_such_op", "params": {}}])
    assert compose_submission(bad, _Batch(files, None), [], unguarded=True, shown=[SHOWN])[1]


def test_the_reply_the_user_reads_is_what_the_gate_traces_obliterate_wording_to(folder):
    """A plan whose wording the reply does not show is rejected back to Claude; the same plan with the words in its
    message (or its plain text) is accepted, and that text is the message the chat shows."""
    files = sorted(folder.glob("*.docx"))
    silent = tool("submit_editspec", {"spec": PLACES_STAMP, "message": "I will stamp each file."}, "t1")
    told = tool("submit_editspec", {"spec": PLACES_STAMP, "message": SHOWN}, "t2")
    fake = FakeClient(resp(silent), resp(told))
    c = Conversation("stamp it", files, client=fake, unguarded=True)
    t = c.start()
    assert t.status == "ok" and t.spec["ops"][0]["params"]["replace"] == STAMP and t.message == SHOWN
    back = fake.requests[1]["messages"][-1]["content"][0]
    assert back["is_error"] and "REJECTED" in back["content"] and "the user has not read them" in back["content"]
    # the plain text of the turn counts as much as the message, and the summary stands in for a model that wrote
    # neither: exactly what the chat shows
    assert Conversation.shown_text("", {}, {"summary": "The summary"}) == "The summary"
    assert Conversation.shown_text(" said ", {"message": "said"}, {"summary": "s"}) == "said"
    assert Conversation.shown_text("one", {"message": "two"}, {}) == "one\n\ntwo"
    spoken = FakeClient(resp(text(SHOWN), tool("submit_editspec", {"spec": PLACES_STAMP}, "t3")))
    t = Conversation("stamp it", files, client=spoken, unguarded=True).start()
    assert t.status == "ok" and t.message == SHOWN


def test_the_conversation_says_obliterate_and_offers_a_custom_method_past_the_formatting_limits(folder):
    files = sorted(folder.glob("*.docx"))
    guarded = Conversation("stamp it", files, client=FakeClient(resp(tool("ask_user", {"message": "?"}))))
    loose = Conversation("stamp it", files, unguarded=True,
                         client=FakeClient(resp(tool("submit_editspec", {"spec": PLACES_STAMP, "message": SHOWN}))))
    assert "OBLITERATE MODE" not in guarded.system and loose.system == compiler.system_prompt(True, True)
    assert loose.system.endswith(compiler.UNGUARDED_PROMPT)
    # the rules it lifts are rewritten where they stand, never left in to contradict the mode (item 5 of the review)
    for kept, lifted in compiler.UNGUARDED_RULES:
        assert kept in guarded.system and kept not in loose.system and lifted in loose.system
    assert "Never invent, reword" in guarded.system and "Never invent, reword" not in loose.system
    assert "never invent project facts" in loose.system                    # what it never lifts stays
    assert "the reply the user reads" in loose.system                      # where the placed words must be
    t = loose.start()
    assert t.status == "ok" and t.spec["ops"][0]["params"]["replace"] == STAMP   # Claude's words, read by the user
    outside = Conversation("move the logo", files, unguarded=True, client=FakeClient(resp(tool(
        "submit_editspec", {"spec": UNSUPPORTED, "custom_transform": "outside_limits",
                            "custom_transform_reason": "text boxes are not formatting"}))))
    t = outside.start()
    assert t.status == "unsupported" and [c.kind for c in t.choices] == ["custom_transform"]
    assert "formatting-only custom fallback" not in t.message


# ---- custom methods -------------------------------------------------------------------------------------------------

def test_a_custom_method_past_the_limits_passes_with_its_failed_checks_only_in_obliterate(folder, tmp_path):
    f = sorted(folder.glob("*.docx"))[0]
    before = f.read_bytes()
    guarded = trial(TEXT_OP, f, {}, {"twips": 900}, tmp_path / "g")
    assert not guarded.passed and any("may not change" in p for p in guarded.problems), guarded.problems
    loose = trial(TEXT_OP, f, {}, {"twips": 900}, tmp_path / "u", unguarded=True)
    assert loose.passed and loose.unverified and loose.output.is_file()      # the checks reported, did not stop it
    assert loose.to_dict()["unverified"] == loose.unverified
    wrong = trial(UNASSERTED_OP, f, {}, {"twips": 900}, tmp_path / "w", unguarded=True)
    assert wrong.passed and any(p.startswith("V10 (") for p in wrong.unverified)
    clean = trial(GOOD_OP, f, {}, {"twips": 900}, tmp_path / "c", unguarded=True)
    assert clean.passed and clean.unverified == []
    assert f.read_bytes() == before                                         # only a copy was touched
    # a governed outcome contract is never unguarded
    governed = trial(TEXT_OP, f, {}, {"twips": 900}, tmp_path / "gov", unguarded=True,
                     goal_contract={"kind": "document_effects", "effects": []})
    assert not governed.unverified


def write_op(*submissions):
    """A scripted writer: each submission is (source, params, accept_failures or None)."""
    return FakeClient(*[resp(tool("submit_op", {"source": src, "params": params,
                                                **({"accept_failures": keep} if keep else {})}, f"t{i}"))
                        for i, (src, params, keep) in enumerate(submissions)])


def test_an_obliterate_methods_failed_checks_go_back_to_the_writer_before_it_is_used(folder, tmp_path):
    """Item 1: a method that ran but failed checks is not used at once — the failures go back to the writer, which
    fixes them; the copy it then makes shows no failure."""
    f = sorted(folder.glob("*.docx"))[:1]
    fake = write_op((UNASSERTED_OP, {"twips": 900}, None), (GOOD_OP, {"twips": 900}, None))
    g = codegen.generate_op("move the header", "set the header distance", f, tmp_path / "cg", client=fake,
                            unguarded=True)
    assert g.passed and g.source == GOOD_OP and len(g.attempts) == 2
    back = fake.requests[1]["messages"][-1]["content"][0]
    assert back["is_error"] and "RAN, BUT 1 CHECK(S) FAILED" in back["content"] and "V10 FAIL" in back["content"]
    assert [t.unverified for t in g.trials] == [[]]
    # Default mode is unchanged: the same failure is a problem to fix, as it always was
    fake = write_op((UNASSERTED_OP, {"twips": 900}, None), (GOOD_OP, {"twips": 900}, None))
    guarded = codegen.generate_op("x", "y", f, tmp_path / "g", client=fake)
    assert guarded.passed and "FAILED — fix and submit again" in fake.requests[1]["messages"][-1]["content"][0]["content"]


def test_a_failed_check_the_writer_keeps_on_purpose_carries_its_reason_to_every_copy(folder, tmp_path):
    f = sorted(folder.glob("*.docx"))
    why = "the request moves the footer with the header"
    fake = write_op((UNASSERTED_OP, {"twips": 900}, None),
                    (UNASSERTED_OP, {"twips": 900}, [{"check": "v10", "reason": why}, {"check": "V2"}]))
    g = codegen.generate_op("move both", "set both distances", f[:1], tmp_path / "cg", client=fake, unguarded=True)
    assert g.passed and g.accepted_failures == {"V10": why} and len(g.attempts) == 2   # V2 without a reason: dropped
    assert any(f"Claude says this is part of the change: {why}" in u for u in g.trials[0].unverified)
    assert preview.generated_key(g)["accepted_failures"] == {"V10": why}     # what the user approves, reasons included
    assert codegen.unverified_lines(g) == [f"{f[0].name}: {g.trials[0].unverified[0]}"] and why in codegen.unverified_lines(g)[0]
    results = codegen.run_generated(g, [str(p) for p in f], tmp_path / "run")
    assert all(r.passed and any(why in u for u in r.unverified) for r in results)


def test_a_package_integrity_check_can_never_be_kept_as_part_of_the_change(folder, tmp_path, monkeypatch):
    """V1, V4, V5a, V5c and V9 mean a member, revision or XML the method got wrong, or a source that moved — never a
    change the request asks for: accept_failures cannot keep them, and the writer is told why."""
    import spec_surgeon.verify as verify_module
    real = verify_module.run_suite

    def failing(src, out, plan, s, o):
        rec = real(src, out, plan, s, o)
        rec.set(CheckResult("V1", "Package whitelist", "FAIL", "planted"))
        return rec
    monkeypatch.setattr(verify_module, "run_suite", failing)
    f = sorted(folder.glob("*.docx"))[:1]
    rounds = codegen.UNGUARDED_CHECK_ROUNDS
    keep = [{"check": "v1", "reason": "the request adds a part"}]
    fake = write_op((GOOD_OP, {"twips": 900}, None),
                    *[(GOOD_OP, {"twips": 900 + i}, keep) for i in range(1, rounds + 1)])
    g = codegen.generate_op("x", "y", f, tmp_path / "cg", client=fake, unguarded=True)
    assert g.passed and g.accepted_failures == {} and len(g.attempts) == rounds + 1
    assert g.trials[0].unverified[0].startswith("V1 (") and "Claude says" not in g.trials[0].unverified[0]
    back = fake.requests[2]["messages"][-1]["content"][0]["content"]
    assert "accept_failures cannot keep V1" in back and "fix the method" in back
    assert "cannot keep" not in fake.requests[1]["messages"][-1]["content"][0]["content"]   # nothing was asked yet
    assert codegen._accepted_failures(keep) == {}
    assert codegen._never_kept(keep + [{"check": "V5a", "reason": "r"}, {"check": "V10", "reason": "r"}]) == ["V1", "V5A"]
    assert "V1, V4, V5A, V5C and V9 can never be kept" in json.dumps(codegen.CODEGEN_TOOLS_UNGUARDED)
    assert "V1, V4, V5A, V5C, V9 can never be kept" in codegen.system_prompt(unguarded=True)


def test_after_its_rounds_the_latest_method_is_used_with_its_failures_shown(folder, tmp_path):
    f = sorted(folder.glob("*.docx"))[:1]
    rounds = codegen.UNGUARDED_CHECK_ROUNDS
    fake = write_op(*[(UNASSERTED_OP, {"twips": 900 + i}, None) for i in range(rounds + 1)])
    g = codegen.generate_op("x", "y", f, tmp_path / "cg", client=fake, unguarded=True)
    assert g.passed and len(g.attempts) == rounds + 1 and len(fake.requests) == rounds + 1
    assert g.params == {"twips": 900 + rounds}                                # the latest
    assert g.trials[0].unverified and g.trials[0].unverified[0].startswith("V10 (")
    # the same method sent back unchanged is a stall: the writer stops, and that method is shown with its failures
    fake = write_op(*[(UNASSERTED_OP, {"twips": 900}, None)] * (rounds + 1))
    stalled = codegen.generate_op("x", "y", f, tmp_path / "stall", client=fake, unguarded=True)
    assert stalled.passed and len(stalled.attempts) < rounds + 1 and "stalled" in stalled.errors[0]


def test_a_writer_that_stops_leaves_its_best_method_but_a_pause_leaves_it_to_the_user(folder, tmp_path):
    from fake_claude import api_error, raise_on_enter
    from spec_surgeon import llm
    f = sorted(folder.glob("*.docx"))[:1]
    fake = FakeClient(resp(tool("submit_op", {"source": UNASSERTED_OP, "params": {"twips": 900}})),
                      raise_on_enter(api_error("Overloaded", status=400)))
    g = codegen.generate_op("x", "y", f, tmp_path / "cg", client=fake, unguarded=True)
    assert g.passed and g.source == UNASSERTED_OP and g.trials[0].unverified     # shown with its failed checks
    # the window's pause for the user's go-ahead stops the writer: nothing is used on its own
    fake = FakeClient(resp(tool("submit_op", {"source": UNASSERTED_OP, "params": {"twips": 900}})),
                      resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}}, "t2")))
    with llm.budget_scope(llm.CallBudget(max_calls=1, max_cost_usd=None, max_total_tokens=None, effort="medium")):
        paused = codegen.generate_op("x", "y", f, tmp_path / "paused", client=fake, unguarded=True)
    assert not paused.passed and len(fake.requests) == 1


def test_a_method_kept_with_failed_checks_is_never_called_passed(folder, tmp_path):
    """Finding F5 (P2): after its rounds the latest method is used with its failed checks (above). The notes the model
    hears next turn and the transcript lines the user reads beside them, the window's message and the plan group's
    details then name the failed checks and never say the samples passed; a method that passes clean keeps today's
    sentences."""
    from types import SimpleNamespace
    files = sorted(folder.glob("*.docx"))[:1]
    rounds = codegen.UNGUARDED_CHECK_ROUNDS
    offer = resp(tool("submit_editspec", {"spec": UNSUPPORTED, "custom_transform": "outside_limits",
                                          "custom_transform_reason": "text boxes are not formatting"}))
    stubborn = [resp(tool("submit_op", {"source": UNASSERTED_OP, "params": {"twips": 900 + i}}, f"t{i}"))
                for i in range(rounds + 1)]

    def prepared(name, *writer):
        """The window in Obliterate mode: the request, the custom method offered and taken at once, its preview."""
        task = task_for(tmp_path / name, FakeClient(offer, *writer), obliterate_allowed=True)
        task.set_mode("obliterate", acknowledged=True)
        task.attach(files)
        view = task.perform("send", text=UNSUPPORTED["request"])
        assert view["stage"] == "preview" and task.session.generated is not None, view
        return task, task.session.conversation

    task, c = prepared("kept", *stubborn)
    g = task.session.generated
    [line] = codegen.unverified_lines(g)
    assert line == f"{files[0].name}: {g.trials[0].unverified[0]}" and "V10 (Property assertions)" in line
    assert codegen.samples_verdict(g) == [codegen.SAMPLES_UNVERIFIED, line]
    # what the model hears next turn: the failed check by name, "UNVERIFIED", and never "passed"
    assert sum(n.startswith(codegen.SAMPLES_UNVERIFIED) and n.endswith(line) for n in c.notes) == 1
    assert any(n.startswith("The one-off custom transform") and "FAILED checks on the sample files" in n and line in n
               and "marked UNVERIFIED" in n for n in c.notes)
    assert not any("passed" in n for n in c.notes), c.notes
    # the transcript lines the user reads beside them
    app = [e.text for e in c.entries if e.role == "app"]
    assert any(t.startswith("Custom transform prepared, with failed checks") and line in t for t in app)
    assert any(t.endswith(f"failed checks on the sample files: {line}.") for t in app)
    assert not any("passed" in t for t in app), app
    # the plan group's details
    [group] = task._plan_view()["groups"]
    [change] = group["changes"]
    assert change["details"][1:] == [codegen.SAMPLES_UNVERIFIED, line] and codegen.SAMPLES_PASSED not in change["details"]
    # the window's message
    s = Session(documents=files, out_root=tmp_path / "msg", client=FakeClient(offer, *stubborn), unguarded=True,
                visual_check=False)
    s.compile(UNSUPPORTED["request"], lambda m: None)
    said = s.reply(choice_id=s.turn.choices[0].id, log=lambda m: None, render=False)
    assert "FAILED checks" in said and line in said and "marked UNVERIFIED" in said and "passed" not in said
    # a check the writer kept with a reason that no sample failed is not a failure: the samples passed, and the kept
    # check is named apart, with its reason (a copy that fails it carries it)
    clean = trial(GOOD_OP, files[0], {}, {"twips": 900}, tmp_path / "ct", unguarded=True)
    assert clean.passed and not clean.unverified
    kept = SimpleNamespace(trials=[clean], accepted_failures={"V10": "the footer moves too"})
    assert codegen.unverified_lines(kept) == []
    assert codegen.kept_lines(kept) == ["V10 — Claude says this is part of the change: the footer moves too"]
    assert codegen.samples_verdict(kept) == [codegen.SAMPLES_PASSED, codegen.SAMPLES_KEPT, *codegen.kept_lines(kept)]
    assert codegen.kept_lines(g) == [] and codegen.samples_verdict(SimpleNamespace(trials=[clean])) == [codegen.SAMPLES_PASSED]
    why = "the footer moves with the header"
    task, c = prepared("kept-clean", resp(tool("submit_op", {"source": UNASSERTED_OP, "params": {"twips": 900}}, "t0")),
                       resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900},
                                               "accept_failures": [{"check": "V10", "reason": why}]}, "t1")))
    g = task.session.generated
    assert g.accepted_failures == {"V10": why} and codegen.unverified_lines(g) == []
    [kept_line] = codegen.kept_lines(g)
    assert kept_line.endswith(why)
    assert any(n.startswith(codegen.SAMPLES_PASSED) and codegen.SAMPLES_KEPT in n and n.endswith(kept_line) for n in c.notes)
    assert not any("FAILED" in n for n in c.notes), c.notes
    assert any(e.role == "app" and e.text.startswith("Custom transform prepared; the samples passed, and the writer kept")
               and kept_line in e.text for e in c.entries)
    assert task._plan_view()["groups"][0]["changes"][0]["details"][1:] == [codegen.SAMPLES_PASSED, codegen.SAMPLES_KEPT,
                                                                           kept_line]
    # a method that passes clean keeps today's sentences, in Obliterate too
    task, c = prepared("clean", resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}}, "w1")))
    assert codegen.unverified_lines(task.session.generated) == []
    assert any("passed its independent sample verification" in n for n in c.notes)
    assert any("passed verification on the sample files" in n for n in c.notes)
    assert not any("UNVERIFIED" in n or "FAILED" in n for n in c.notes), c.notes
    assert task._plan_view()["groups"][0]["changes"][0]["details"][1:] == [codegen.SAMPLES_PASSED]


def test_an_obliterate_method_has_the_text_and_revision_helpers_and_its_wording_change_passes_every_check(
        tmp_path, monkeypatch):
    """Item 2: the writer is told it may change wording, tracked or plain — and now has the helpers that let a correct
    change assert itself, so it is not marked UNVERIFIED."""
    from test_governed_codegen import CONTENT_OP
    source = tmp_path / "source.docx"
    source.write_bytes(build("21 00 00", "FIRE SUPPRESSION"))
    monkeypatch.setenv("SPEC_SURGEON_AUTHOR", "Obliterate Tester")
    op = CONTENT_OP.replace('author="Tester"', "author=pkg.author").replace('RevisionStamp.now("Tester"',
                                                                            "RevisionStamp.now(pkg.author")
    assert codegen.check_source(op) and codegen.check_source(op, unguarded=True) == []
    for tracked in (False, True):
        t = trial(op, source, {}, {"replacement": "WATER", "tracked": tracked}, tmp_path / f"t{tracked}",
                  unguarded=True)
        assert t.passed and t.unverified == [], (t.problems, t.unverified)
        xml = __import__("zipfile").ZipFile(t.output).read("word/document.xml").decode("utf-8")
        assert ("WATER" in xml) and (('w:author="Obliterate Tester"' in xml) is tracked)
    refused = trial(op, source, {}, {"replacement": "WATER"}, tmp_path / "guarded")      # Default: whitelist
    assert not refused.passed and any("is not allowed" in p for p in refused.problems)
    prompt = codegen.system_prompt(unguarded=True)
    assert "pkg.add_member" in prompt and "plan_paragraph" in prompt and "pkg.inputs" in prompt
    assert "apply() mutates the working package only through" not in prompt   # no rule left to contradict it


ATTACHED_OP = GOOD_OP.replace("        twips = params.get(\"twips\")",
                              "        if len(pkg.inputs) != params.get(\"expect_inputs\"):\n"
                              "            raise JobException(f\"{len(pkg.inputs)} attached file(s)\", \"inputs\")\n"
                              "        twips = params.get(\"twips\")")


def test_an_obliterate_method_gets_the_users_attachments_pinned_when_it_was_written(folder, tmp_path):
    """Item 3: the template and the pictures or tables the user attached reach the method as ``pkg.inputs`` — the
    bytes pinned when it was written; a file changed since is refused, never read afresh."""
    import hashlib
    assert ATTACHED_OP != GOOD_OP
    f = sorted(folder.glob("*.docx"))
    template = tmp_path / "template.docx"
    template.write_bytes(build("00 00 00", "TEMPLATE"))
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"\x89PNG fake logo")
    fake = FakeClient(resp(tool("read_template", {}, "r1")),
                      resp(tool("submit_op", {"source": ATTACHED_OP, "params": {"twips": 900, "expect_inputs": 2}})))
    g = codegen.generate_op("use my logo", "put the logo in", f[:1], tmp_path / "cg", client=fake, unguarded=True,
                            template=template, input_files=[logo])
    assert g.passed and g.trials[0].unverified == [], g.attempts
    assert [a["path"] for a in g.attachments] == [str(template.resolve()), str(logo.resolve())]
    assert g.attachments[1]["sha256"] == hashlib.sha256(logo.read_bytes()).hexdigest()
    first = fake.requests[0]
    assert str(logo.resolve()) in first["messages"][0]["content"] and "ATTACHED FILES" in first["messages"][0]["content"]
    names = [t["name"] for t in first["tools"]]
    assert {"read_template", "read_input_file"} <= set(names)
    submit = next(t for t in first["tools"] if t["name"] == "submit_op")
    assert "accept_failures" in submit["input_schema"]["properties"]
    assert not fake.requests[1]["messages"][-1]["content"][0].get("is_error")       # read_template answered
    assert preview.generated_key(g)["attachments"] == g.attachments
    assert all(r.passed for r in codegen.run_generated(g, [str(p) for p in f], tmp_path / "run"))
    logo.write_bytes(b"\x89PNG another logo")                                         # changed after it was written
    stale = codegen.run_generated(g, [str(f[0])], tmp_path / "run2")
    assert not stale[0].passed and "[stale-input]" in stale[0].problems[0]
    # Default mode: no attachment reaches a method, and its tools are unchanged
    plain = trial(ATTACHED_OP, f[0], {}, {"twips": 900, "expect_inputs": 2}, tmp_path / "plain",
                  attachments=g.attachments)
    assert not plain.passed and "0 attached file(s)" in plain.problems[0]
    assert "accept_failures" not in json.dumps(codegen.CODEGEN_TOOLS)


def test_the_whitelist_and_the_sandbox_hold_in_obliterate(folder, tmp_path):
    f = sorted(folder.glob("*.docx"))[0]
    escape = GOOD_OP.replace("from typing import Any, Dict", "from typing import Any, Dict\nimport os")
    t = trial(escape, f, {}, {"twips": 900}, tmp_path / "x", unguarded=True)
    assert not t.passed and any("'import os' is not allowed" in p for p in t.problems)
    prompt = codegen.system_prompt(unguarded=True)
    assert "OBLITERATE MODE" in prompt and "PLAIN formatting only" not in prompt
    assert "PLAIN formatting only" in codegen.system_prompt()
    assert "import whitelist" in prompt and "sandbox" in prompt


BYTES_OP = GOOD_OP.replace('        twips = params.get("twips")',
                           '        raw = bytes([137, 80, 78, 71]) + bytearray(4)\n'
                           '        assert isinstance(raw, bytes) and hex(int.from_bytes(raw[:2], "big")) == "0x8950"\n'
                           '        try:\n'
                           '            1 // 0\n'
                           '        except ZeroDivisionError:\n'
                           '            pass\n'
                           '        twips = params.get("twips")')


def test_the_sandbox_has_the_builtins_a_method_needs_and_none_that_reach_the_system(folder, tmp_path):
    """A method that handles a picture or a media member needs bytes, bytearray, hex and the arithmetic exceptions:
    they are builtins of the sandbox, in every mode; what reaches the file system or the interpreter is not."""
    from spec_surgeon.sandbox_runner import SAFE_BUILTINS
    f = sorted(folder.glob("*.docx"))[0]
    assert BYTES_OP != GOOD_OP
    for unguarded in (False, True):
        t = trial(BYTES_OP, f, {}, {"twips": 900}, tmp_path / f"b{unguarded}", unguarded=unguarded)
        assert t.passed and t.unverified == [], t.problems
    for name in ("open", "exec", "eval", "getattr", "type", "__import__", "OSError", "PermissionError",
                 "MemoryError", "RecursionError", "SystemExit", "BaseException"):
        assert name not in SAFE_BUILTINS
    prompt = codegen.system_prompt(unguarded=True)
    assert "the sandbox has ONLY these names: " + ", ".join(sorted(SAFE_BUILTINS)) in prompt
    assert "the sandbox has ONLY these names: " + ", ".join(sorted(SAFE_BUILTINS)) in codegen.system_prompt()


def test_an_obliterate_methods_approval_is_never_a_guarded_ones():
    from types import SimpleNamespace
    g = SimpleNamespace(source="x", select={}, params={}, goal_contract=None, goal_bindings={})
    assert "unguarded" not in preview.generated_key(g)
    g.unguarded = True
    assert preview.generated_key(g)["unguarded"] is True


# ---- delivery -------------------------------------------------------------------------------------------------------

def test_a_failing_copy_is_quarantined_except_in_obliterate(tmp_path, planted_v2):
    d = tmp_path / "in"; d.mkdir()
    src = d / "21 05 00 – CLEAN.docx"
    src.write_bytes(build("21 05 00", "COMMON WORK RESULTS")); before = src.read_bytes()
    kw = dict(workers=1, renderer="none", do_render=False, spec=REPLACE)
    held = run_batch([str(d)], "edit-spec", {}, tmp_path / "held", **kw)
    assert held.files[0].delivered_to is None and not list((tmp_path / "held" / "outputs").iterdir())
    out = tmp_path / "loose"
    b = run_batch([str(d)], "edit-spec", {}, out, deliver_unverified=True, **kw)
    f = b.files[0]
    assert f.status == "FAIL" and f.unverified == ["V2 (Text preservation)"]                       # its status still says FAIL
    assert f.delivered_to == str(out / "outputs" / src.name) and Path(f.delivered_to).is_file()
    assert not (out / "failed").exists() or not list((out / "failed").iterdir())
    assert "UNVERIFIED" in f.record.get("V9").summary and "V2 (Text preservation)" in f.record.get("V9").summary
    assert f.record.get("V2").status == "FAIL"                                # the record keeps every failure
    assert src.read_bytes() == before                                         # copies only
    # the reports say it was delivered, and it is not set aside for a next pass
    assert load_set_aside(out)["files"] == []
    assert "delivered UNVERIFIED" in (out / "BATCH_REPORT.md").read_text(encoding="utf-8")
    from spec_surgeon.report import FAILURE_EXPLANATIONS, file_diagnosis
    assert "delivered UNVERIFIED" in file_diagnosis(f) and "not delivered" not in file_diagnosis(f)
    assert FAILURE_EXPLANATIONS["unverified"][0] == "Delivered Unverified (Obliterate Mode)"
    in_place = run_batch([str(d)], "edit-spec", {}, tmp_path / "inplace", deliver_unverified=True, in_place=True, **kw)
    assert in_place.files[0].delivered_to is None and src.read_bytes() == before   # never written back unverified
    assert load_set_aside(tmp_path / "held")["files"][0]["code"] == "verify-fail"


def test_a_source_that_moved_is_never_delivered_even_in_obliterate(tmp_path, planted_v2, monkeypatch):
    d = tmp_path / "in"; d.mkdir()
    (d / "21 05 00 – A.docx").write_bytes(build("21 05 00", "A"))
    monkeypatch.setattr(batch_module.guard, "drifted", lambda identity: "mtime moved during the run")
    b = run_batch([str(d)], "edit-spec", {}, tmp_path / "out", workers=1, renderer="none", do_render=False,
                  spec=REPLACE, deliver_unverified=True)
    f = b.files[0]
    assert f.delivered_to is None and f.record.get("V9").status == "FAIL" and f.unverified == []


def test_a_chain_hands_on_and_delivers_an_unverified_copy_only_in_obliterate(tmp_path, planted_v2):
    d = tmp_path / "in"; d.mkdir()
    (d / "21 05 00 – A.docx").write_bytes(build("21 05 00", "A"))
    bold = {"request": "bold", "summary": "s", "mode": "formatting",
            "ops": [{"op": "set_properties", "select": {"paragraphs": {"text_regex": "^SECTION"}},
                     "params": {"run": {"b": True}, "tracked": False}}]}
    chain = chain_from_specs([("replace", REPLACE), ("bold", bold)])
    kw = dict(workers=1, do_render=False)
    held = run_chain([str(d)], chain, tmp_path / "held", **kw)
    assert held.files[0].delivered_to is None and held.files[0].status != "PASS"
    loose = run_chain([str(d)], chain, tmp_path / "loose", deliver_unverified=True, **kw)
    cf = loose.files[0]
    assert cf.status == "FAIL" and cf.code == "unverified" and cf.delivered_to and Path(cf.delivered_to).is_file()
    assert [u.split(":")[0] for u in cf.unverified] == ["step 1 (replace)", "step 2 (bold)"]
    assert "UNVERIFIED" in cf.reason and cf.category == "Delivered Unverified (Obliterate Mode)"
    assert load_set_aside(tmp_path / "loose")["files"] == [] and cf.to_dict()["unverified"] == cf.unverified


def test_the_window_delivers_unverified_copies_and_starts_no_recovery_in_obliterate(tmp_path, planted_v2):
    path = document(tmp_path / "Spec.docx")
    before = path.read_bytes()
    fake = FakeClient(resp(tool("submit_editspec", {"spec": SPEC})))
    task = task_for(tmp_path, fake, obliterate_allowed=True)
    task.set_mode("obliterate", acknowledged=True)
    task.attach([path])
    view = task.perform("send", text=REQUEST)
    assert view["stage"] == "preview"
    calls = len(fake.requests)
    view = task.perform("primary", token=view["token"])
    assert len(fake.requests) == calls                                       # no recovery, no other AI call
    assert view["stage"] == "done" and task.results[str(path)]
    assert task.unverified[str(path)] == ["V2 (Text preservation)"] and not task.todo
    assert view["unverified"] == {str(path): ["V2 (Text preservation)"]}
    assert "delivered UNVERIFIED" in task.messages[-1]["text"]
    assert path.read_bytes() == before
    # the mark survives the journal, and a later verified copy keeps it: that copy was checked against the unverified
    # one, not against the original
    task.save()
    task._mark_unverified(str(path), [])
    assert task.unverified[str(path)] == ["in an earlier copy: V2 (Text preservation)"]
    task._mark_unverified(str(path), ["V10 (Property assertions)", "V2 (Text preservation)"])
    assert task.unverified[str(path)] == ["V10 (Property assertions)", "V2 (Text preservation)"]
    task._mark_unverified(str(path), [])
    assert task.unverified[str(path)] == ["in an earlier copy: V10 (Property assertions)",
                                          "in an earlier copy: V2 (Text preservation)"]


@pytest.fixture
def planted_v2_once(monkeypatch):
    """The first copy a run checks fails V2; every later one passes."""
    real = batch_module.run_suite
    state = {"planted": False}

    def failing(src, out, plan, s, o):
        rec = real(src, out, plan, s, o)
        if not state["planted"]:
            state["planted"] = True
            rec.set(CheckResult("V2", "Text preservation", "FAIL", "planted"))
        return rec
    monkeypatch.setattr(batch_module, "run_suite", failing)


def test_an_unverified_copy_keeps_its_mark_through_later_runs_and_the_model_hears_it(tmp_path, planted_v2_once):
    """A later request builds on the unverified copy, in any mode: its verified copy was checked against the broken
    one, so the mark stays (saying so), the model is told, and only Start over clears it."""
    path = document(tmp_path / "Spec.docx")
    italic = {**SPEC, "summary": "Make the title italic",
              "ops": [{"op": "set_properties", "select": {"paragraphs": {"text_regex": "^FIRE SUPPRESSION$"}},
                       "params": {"run": {"i": True}, "tracked": False}}]}
    fake = FakeClient(resp(tool("submit_editspec", {"spec": SPEC})), resp(tool("submit_editspec", {"spec": italic})))
    task = task_for(tmp_path, fake, obliterate_allowed=True)
    task.set_mode("obliterate", acknowledged=True)
    task.attach([path])
    view = task.perform("send", text=REQUEST)
    view = task.perform("primary", token=view["token"])
    first = task.results[str(path)]
    assert task.unverified[str(path)] == ["V2 (Text preservation)"]
    # Default mode builds on the copy: the model hears what it builds on, the copy passes, the mark stays
    task.set_mode("default")
    view = task.perform("send", text="Also make it italic")
    told = fake.requests[1]["messages"][0]["content"]
    assert "Spec.docx was delivered UNVERIFIED (Obliterate mode): V2 (Text preservation) failed" in told
    assert view["stage"] == "preview"
    view = task.perform("primary", token=view["token"])
    assert view["stage"] == "done" and task.results[str(path)] != first
    assert task.session.last_batch.files[0].status == "PASS"
    assert task.unverified[str(path)] == ["in an earlier copy: V2 (Text preservation)"]
    assert view["unverified"] == {str(path): ["in an earlier copy: V2 (Text preservation)"]}
    assert "in an earlier copy: V2 (Text preservation)" in "; ".join(task._history_in_scope())
    # a Safe-mode button keeps it too, through its preview and its run
    task.set_mode("safe")
    view = task.perform("recipe", recipe="remove_highlight", inputs={})
    assert view["stage"] == "preview" and task.unverified[str(path)] == ["in an earlier copy: V2 (Text preservation)"]
    view = task.perform("primary", token=view["token"])
    assert view["stage"] == "done" and task.unverified[str(path)] == ["in an earlier copy: V2 (Text preservation)"]
    # the journal keeps it; Start over clears it with the copies
    task.save()
    task.start_over()
    assert task.unverified == {}


def test_default_mode_is_untouched_by_all_of_it(tmp_path, planted_v2):
    path = document(tmp_path / "Spec.docx")
    fake = FakeClient(resp(tool("submit_editspec", {"spec": SPEC})))
    task = task_for(tmp_path, fake)
    task.attach([path])
    assert not task.session.unguarded
    view = task.perform("send", text=REQUEST)
    try:
        task.perform("primary", token=view["token"])
    except Exception:  # noqa: BLE001 — recovery asks the scripted client for more than it has; that is the point
        pass
    assert str(path) not in task.results and not task.unverified
    assert len(fake.requests) > 1                                             # Default looked for a fix


# ---- the preview (Codex review on Abe-Borg/spec-surgeon#102) ---------------------------------------------------------

@pytest.fixture
def planted_v2_everywhere(monkeypatch, planted_v2):
    """The preview's samples fail V2 too (the previews read ``spec_surgeon.verify.run_suite`` at call time)."""
    import spec_surgeon.verify as verify_module
    real = verify_module.run_suite

    def failing(src, out, plan, s, o):
        rec = real(src, out, plan, s, o)
        rec.set(CheckResult("V2", "Text preservation", "FAIL", "planted"))
        return rec
    monkeypatch.setattr(verify_module, "run_suite", failing)


def test_a_chain_preview_carries_a_failed_sample_on_only_in_obliterate(tmp_path, planted_v2_everywhere):
    from spec_surgeon.preview import make_chain_preview
    d = tmp_path / "in"; d.mkdir()
    (d / "21 05 00 – A.docx").write_bytes(build("21 05 00", "A"))
    bold = {"request": "bold", "summary": "s", "mode": "formatting",
            "ops": [{"op": "set_properties", "select": {"paragraphs": {"text_regex": "^SECTION"}},
                     "params": {"run": {"b": True}, "tracked": False}}]}
    chain = chain_from_specs([("replace", REPLACE), ("bold", bold)])
    held = make_chain_preview(chain, [str(d)], tmp_path / "p1", renderer="none", do_render=False)
    assert [st["stage"] for st in held.samples[0].stages] == [1] and held.samples[0].passed is False
    loose = make_chain_preview(chain, [str(d)], tmp_path / "p2", renderer="none", do_render=False,
                               continue_failed=True)
    sample = loose.samples[0]
    assert [st["stage"] for st in sample.stages] == [1, 2] and sample.passed is False   # the run will go on too
    assert sample.exception is None and sample.output is not None


def test_a_failed_check_in_the_preview_does_not_void_an_obliterate_plan(tmp_path, planted_v2_everywhere):
    """Default hands a sample that failed its check to an outcome investigation (another AI call); Obliterate keeps
    the plan, shows the sample as not passed, and Create delivers the copy marked UNVERIFIED."""
    path = document(tmp_path / "Spec.docx")
    fake = FakeClient(resp(tool("submit_editspec", {"spec": SPEC})))
    task = task_for(tmp_path, fake, obliterate_allowed=True)
    task.set_mode("obliterate", acknowledged=True)
    task.attach([path])
    view = task.perform("send", text=REQUEST)
    assert view["stage"] == "preview" and len(fake.requests) == 1
    assert [s.passed for s in view["samples"]] == [False]
    view = task.perform("primary", token=view["token"])
    assert view["stage"] == "done" and task.unverified[str(path)] == ["V2 (Text preservation)"]
    assert len(fake.requests) == 1
    # Default: the same failed sample sends the plan to investigation
    path2 = document(tmp_path / "Other" / "Spec.docx")
    fake2 = FakeClient(resp(tool("submit_editspec", {"spec": SPEC})))
    task2 = task_for(tmp_path / "d", fake2)
    task2.attach([path2])
    try:
        task2.perform("send", text=REQUEST)
    except Exception:  # noqa: BLE001 — the scripted client has no second answer: the investigation asked for one
        pass
    assert len(fake2.requests) > 1


def test_a_custom_methods_preview_shows_the_checks_it_failed(folder, tmp_path):
    from types import SimpleNamespace
    from spec_surgeon.preview import preview_generated
    f = sorted(folder.glob("*.docx"))[0]
    loose = trial(TEXT_OP, f, {}, {"twips": 900}, tmp_path / "u", unguarded=True)
    assert loose.passed and loose.unverified                          # it may be delivered…
    gp = preview_generated(SimpleNamespace(name="op", trials=[loose]), tmp_path / "pv", renderer="none",
                           do_render=False)
    assert gp.samples[0].passed is False and "verification FAIL" in gp.to_text()   # …but it never shows as checked
    clean = trial(GOOD_OP, f, {}, {"twips": 900}, tmp_path / "c", unguarded=True)
    gp = preview_generated(SimpleNamespace(name="op", trials=[clean]), tmp_path / "pv2", renderer="none",
                           do_render=False)
    assert gp.samples[0].passed is True
