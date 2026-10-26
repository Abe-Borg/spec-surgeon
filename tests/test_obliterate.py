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

from fake_claude import FakeClient, resp, tool
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
    state = WindowState(task, Settings(out_root=str(tmp_path / "out"), show_obliterate=show), token=TOKEN,
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

def test_the_gate_traces_placed_words_except_in_obliterate(folder):
    files = sorted(folder.glob("*.docx"))
    _, errs = compose_submission(PLACES_STAMP, _Batch(files, None), ["replace SPEC 0"])
    assert any("untraceable" in e or STAMP in e for e in errs), errs
    outcome, errs = compose_submission(PLACES_STAMP, _Batch(files, None), ["replace SPEC 0"], unguarded=True)
    assert errs == [] and outcome == PLACES_STAMP
    # everything else at the gate is the same: an op that does not exist is refused either way
    bad = dict(PLACES_STAMP, ops=[{"op": "no_such_op", "params": {}}])
    assert compose_submission(bad, _Batch(files, None), [], unguarded=True)[1]


def test_the_conversation_says_obliterate_and_offers_a_custom_method_past_the_formatting_limits(folder):
    files = sorted(folder.glob("*.docx"))
    guarded = Conversation("stamp it", files, client=FakeClient(resp(tool("ask_user", {"message": "?"}))))
    loose = Conversation("stamp it", files, client=FakeClient(resp(tool("submit_editspec", {"spec": PLACES_STAMP}))),
                         unguarded=True)
    assert "OBLITERATE MODE" not in guarded.system and loose.system == guarded.system + compiler.UNGUARDED_PROMPT
    assert compiler.system_prompt(chat=True) + compiler.UNGUARDED_PROMPT == compiler.system_prompt(True, True)
    t = loose.start()
    assert t.status == "ok" and t.spec["ops"][0]["params"]["replace"] == STAMP   # Claude's words, not traced
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


def test_the_whitelist_and_the_sandbox_hold_in_obliterate(folder, tmp_path):
    f = sorted(folder.glob("*.docx"))[0]
    escape = GOOD_OP.replace("from typing import Any, Dict", "from typing import Any, Dict\nimport os")
    t = trial(escape, f, {}, {"twips": 900}, tmp_path / "x", unguarded=True)
    assert not t.passed and any("'import os' is not allowed" in p for p in t.problems)
    prompt = codegen.system_prompt(unguarded=True)
    assert "OBLITERATE MODE" in prompt and "PLAIN formatting only" not in prompt
    assert "PLAIN formatting only" in codegen.system_prompt()
    assert "import whitelist" in prompt and "sandbox" in prompt


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
    # the mark survives the journal; a later verified copy clears it
    task.save()
    task._mark_unverified(str(path), [])
    assert str(path) not in task.unverified


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
