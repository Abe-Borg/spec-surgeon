"""Phase 4: the compiler loop (scripted fake client — no API calls), the submission checks (validation, traceable
words, dry plan), the preview and the approval token, and the `do` command end to end."""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from make_fixtures import build
from spec_surgeon import compiler as compiler_module
from spec_surgeon.cli import main as cli_main
from spec_surgeon.compiler import compile_request, untraceable
from spec_surgeon.editspec import load_spec
from spec_surgeon.preview import approval_token, make_preview

SPECS = Path(__file__).resolve().parent.parent / "examples" / "specs"
REMOVE = json.loads((SPECS / "remove-all-headers-footers.json").read_text(encoding="utf-8"))


from fake_claude import FakeClient, resp, text, tool  # noqa: F401 — re-exported for the other test modules


def remove_outcome_proposal(*, kinds=None):
    """Script the independent planner, separately from the editing recipe."""
    effect = {"id": "headers-footers", "type": "remove_header_footer"}
    if kinds is not None:
        effect["kinds"] = kinds
    description = " and ".join(kind + "s" for kind in (kinds or ["header", "footer"]))
    return resp(tool("propose_plan", {"contract": {"kind": "document_effects", "effects": [effect]},
        "rationale": f"Remove {description} from every original section."}))


def header_outcome_proposal():
    return resp(tool("propose_plan", {"contract": {"kind": "document_effects", "effects": [
        {"id": "header-distance", "type": "page_setup", "sections": "all", "margins": {"header": 900}}]},
        "rationale": "Place every section's header 900 twips from the top."}))


def generate_reviewed_custom(session, *, choice_id=None, log=lambda _: None, render=False):
    """Choosing a custom method prepares it immediately; its preview is the consent decision."""
    choice_id = choice_id or session.turn.choices[0].id
    message = session.reply(choice_id=choice_id, log=log, render=render)
    assert session.generated is not None and session.pending_custom_transform is None
    return message


@pytest.fixture
def folder(tmp_path) -> Path:
    d = tmp_path / "specs"
    d.mkdir()
    for i in range(3):
        (d / f"21 0{i} 00 – SPEC {i}.docx").write_bytes(build(f"21 0{i} 00", f"SPEC {i}"))
    return d


def files(d):
    return sorted(d.glob("*.docx"))


def test_compile_inspects_then_submits(folder):
    fake = FakeClient(resp(tool("inspect_profile", {"pattern_id": "b20e6c1da8"})),
                      resp(tool("submit_editspec", {"spec": REMOVE}, "t2")))
    res = compile_request("remove all headers and footers", files(folder), client=fake)
    assert res.status == "ok" and res.spec == REMOVE, res.errors
    assert [c["tool"] for c in res.tool_calls] == ["inspect_profile", "submit_editspec"]
    first = fake.requests[0]
    assert first["model"] == "claude-opus-5-5" and first["fallbacks"] == "default" and first["max_tokens"] == 128000
    assert "OPERATIONS" in first["system"] and "apply_header_footer_template" in first["system"]
    assert "slot_fills" in first["system"]          # every op's parameters ride the prompt: no describe_op round trip
    assert "remove all headers and footers" in first["messages"][0]["content"]
    result = fake.requests[1]["messages"][-1]["content"][0]
    assert result["type"] == "tool_result" and "21 00 00" in result["content"]          # the profile came back
    assert res.usage["input_tokens"] == 200
    assert res.served_models == ["claude-opus-5-5"] and res.to_dict()["served_models"] == ["claude-opus-5-5"]


def test_traceable_words_come_from_one_source_each():
    spec = {"ops": [{"op": "replace_text", "params": {"find": "a", "replace": "ISSUED FOR PERMIT"}}]}
    assert untraceable(spec, ["ISSUED FOR", "PERMIT"]) == ["ISSUED FOR PERMIT"]      # not stitched across sources
    assert untraceable(spec, ["the stamp: ISSUED FOR  PERMIT"]) == []                  # whitespace-normalised


def test_the_legacy_ask_and_agent_commands_are_retired(capsys):
    for cmd in (["ask", "x"], ["agent", "x.json"]):
        with pytest.raises(SystemExit):
            cli_main(cmd)
    import importlib.util
    assert importlib.util.find_spec("spec_surgeon.agent") is None
    from spec_surgeon import SKILL_DIR
    assert (SKILL_DIR / "SKILL.md").is_file()


def test_a_rejected_spec_goes_back_once(folder):
    bad = copy.deepcopy(REMOVE)
    bad["ops"][0]["op"] = "remove_headers"
    fake = FakeClient(resp(tool("submit_editspec", {"spec": bad})), resp(tool("submit_editspec", {"spec": REMOVE}, "t2")))
    res = compile_request("remove all headers and footers", files(folder), client=fake)
    assert res.status == "ok"
    err = fake.requests[1]["messages"][-1]["content"][0]
    assert err["is_error"] and "unknown op 'remove_headers'" in err["content"]
    fake = FakeClient(resp(tool("submit_editspec", {"spec": bad})), resp(tool("submit_editspec", {"spec": bad}, "t2")),
                      resp(tool("submit_editspec", {"spec": bad}, "t3")))
    res = compile_request("remove all headers and footers", files(folder), client=fake)
    assert res.status == "failed" and "stalled" in res.errors[0]


def test_dry_plan_catches_spec_level_mistakes(folder):
    wrong_mode = dict(copy.deepcopy(REMOVE), mode="content")
    fake = FakeClient(resp(tool("submit_editspec", {"spec": wrong_mode})), resp(tool("submit_editspec", {"spec": REMOVE}, "t2")))
    res = compile_request("remove all headers and footers", files(folder), client=fake)
    assert res.status == "ok"
    assert "spec-mode" in fake.requests[1]["messages"][-1]["content"][0]["content"]


def _replace_spec(words):
    return {"request": "r", "summary": "s", "mode": "content", "assumptions": [], "questions": [],
            "ops": [{"op": "replace_text", "select": {"scope": ["document"]},
                     "params": {"find": "FM Global", "replace": words, "tracked": True, "author": "Abraham Borg"}}]}


def test_words_the_user_never_gave_are_refused_and_the_model_must_ask(folder):
    invented = _replace_spec("Global Risk Consultants (GRC)")
    fake = FakeClient(resp(tool("submit_editspec", {"spec": invented})),
                      resp(tool("ask_user", {"questions": ["What exact wording should replace 'FM Global'?"]}, "t2")))
    res = compile_request("change FM Global to the GRC wording", files(folder), client=fake)
    assert res.status == "questions" and "exact wording" in res.questions[0]
    assert "never gave them verbatim" in fake.requests[1]["messages"][-1]["content"][0]["content"]
    # the same words, once the user supplied them, are fine
    fake = FakeClient(resp(tool("submit_editspec", {"spec": invented})))
    res = compile_request("change FM Global to the GRC wording", files(folder), client=fake,
                          answers=["use 'Global Risk Consultants (GRC)'"])
    assert res.status == "ok", res.errors
    assert untraceable(invented, ["Global  Risk Consultants\n(GRC)"]) == []           # whitespace-insensitive


def test_outcomes_questions_unsupported_refusal_and_silence(folder):
    q = dict(copy.deepcopy(REMOVE), questions=["Keep the cover footer?"])
    assert compile_request("remove headers", files(folder), client=FakeClient(resp(tool("submit_editspec", {"spec": q})))
                           ).questions == ["Keep the cover footer?"]
    u = {"request": "renumber", "summary": "s", "unsupported": "no numbering op yet", "proposal": "renumber_scheme"}
    res = compile_request("renumber", files(folder), client=FakeClient(resp(tool("submit_editspec", {"spec": u}))))
    assert res.status == "unsupported" and res.spec["proposal"] == "renumber_scheme"
    refusal = resp(stop="refusal")
    refusal.stop_details = SimpleNamespace(category="cyber", explanation="no")
    res = compile_request("x", files(folder), client=FakeClient(refusal))
    assert res.status == "failed" and "declined" in res.errors[0]
    res = compile_request("x", files(folder), client=FakeClient(resp(text("ok"), stop="end_turn"),
                                                                 resp(text("done"), stop="end_turn")))
    assert res.status == "failed" and "without submitting" in res.errors[0]


def test_tools_read_the_batch_and_bad_tool_input_is_an_error_result(folder):
    name = files(folder)[0].name
    fake = FakeClient(resp(tool("list_styles", {"file": name}), tool("sample_paragraphs", {"file": name, "regex": "^SECTION"}, "t2"),
                           tool("show_part_text", {"file": name, "part": "word/footer1.xml"}, "t3"),
                           tool("show_part_text", {"file": "nope.docx", "part": "x"}, "t4")),
                      resp(tool("submit_editspec", {"spec": REMOVE}, "t5")))
    res = compile_request("remove all headers and footers", files(folder), client=fake)
    assert res.status == "ok"
    results = fake.requests[1]["messages"][-1]["content"]
    assert len(results) == 4                                                       # all results in one message
    assert '"Normal"' in results[0]["content"] and "SECTION 21 00 00" in results[1]["content"]
    assert "21 00 00 - 1" in results[2]["content"] and results[3]["is_error"]


def test_preview_and_approval_token(folder, tmp_path):
    spec = load_spec(SPECS / "remove-all-headers-footers.json")
    pv = make_preview(spec, [str(folder)], tmp_path / "pv", do_render=False)
    assert len(pv.samples) == 2 and all(s.passed for s in pv.samples)
    assert (tmp_path / "pv" / "spec.json").is_file() and (tmp_path / "pv" / "PREVIEW.md").is_file()
    assert "rendering skipped" in pv.warnings[0] and pv.token in pv.to_text()
    assert pv.token == approval_token(spec, files(folder))
    changed = dict(spec, summary="different")
    assert approval_token(changed, files(folder)) != pv.token
    f = files(folder)[0]
    f.write_bytes(f.read_bytes() + b"")
    import os
    os.utime(f, ns=(f.stat().st_atime_ns, f.stat().st_mtime_ns + 10_000_000))
    assert approval_token(spec, files(folder)) != pv.token                          # a touched file voids it


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
def test_preview_renders_side_by_side_images(folder, tmp_path):
    pv = make_preview(load_spec(SPECS / "remove-all-headers-footers.json"), [str(folder)], tmp_path / "pv")
    assert pv.renderer == "libreoffice" and any("Word's layout can differ" in w for w in pv.warnings)
    assert pv.samples[0].images and pv.samples[0].images[0].is_file()


def test_do_end_to_end_with_approval(folder, tmp_path, monkeypatch, capsys):
    script = lambda: FakeClient(resp(tool("submit_editspec", {"spec": REMOVE})))
    clients = iter([script(), script()])
    monkeypatch.setattr(compiler_module, "_client", lambda: next(clients))
    out = tmp_path / "out"
    monkeypatch.setattr("spec_surgeon.cli._interactive", lambda: False)
    assert cli_main(["do", "remove all headers and footers", str(folder), "--out", str(out), "--no-render"]) == 0
    printed = capsys.readouterr().out
    assert "PREVIEW" in printed and "Not run." in printed and not (out / "run").exists()
    token = printed.split("approval token: ")[1].split()[0]
    spec_path = out / "preview" / "spec.json"
    assert cli_main(["run-spec", str(spec_path), str(folder), "--out", str(tmp_path / "r1"), "--no-render",
                     "--approval", "0" * 16]) == 2                       # wrong recipe token: refused
    assert cli_main(["run-spec", str(spec_path), str(folder), "--out", str(tmp_path / "r2"), "--no-render",
                     "--approval", token]) == 0
    capsys.readouterr()
    assert cli_main(["do", "remove all headers and footers", str(folder), "--out", str(tmp_path / "o2"), "--no-render",
                     "--yes"]) == 0
    assert "PASS 3" in capsys.readouterr().out
    monkeypatch.setattr(compiler_module, "_client",
                        lambda: FakeClient(resp(tool("ask_user", {"questions": ["Which sections?"]}))))
    assert cli_main(["do", "remove some headers", str(folder), "--out", str(tmp_path / "o3"), "--no-render"]) == 2
    assert "? Which sections?" in capsys.readouterr().out


def test_do_talks_it_through_at_the_terminal(folder, tmp_path, monkeypatch, capsys):
    """A person at the terminal: Claude's choices print with their exact words, '#n' picks one (wording needs a yes),
    plain text is a reply, and the conversation is kept beside compile.json."""
    from spec_surgeon import cli
    fake = FakeClient(resp(tool("ask_user", {"understanding": "Remove headers and footers.",
                                             "choices": [{"label": "Every section", "kind": "clarify"},
                                                         {"label": "Stamp it", "kind": "alternative",
                                                          "wording": "ISSUED FOR PERMIT"}]})),
                      resp(tool("ask_user", {"questions": ["And the cover?"]}, "a2")),
                      resp(tool("submit_editspec", {"spec": REMOVE}, "s3")))
    clients = iter([fake])
    monkeypatch.setattr(compiler_module, "_client", lambda: next(clients))
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    answers = iter(["#2", "n", "#1", "yes, the cover too", "n"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    out_dir = tmp_path / "o"
    assert cli_main(["do", "remove all headers and footers", str(folder), "--out", str(out_dir), "--no-render"]) == 0
    printed = capsys.readouterr().out
    assert "[#2] Stamp it" in printed and "exact words: «ISSUED FOR PERMIT»" in printed and "Not run." in printed
    assert fake.requests[1]["messages"][-1]["content"][0]["content"].startswith("The user chose: «Every section»")
    assert fake.requests[2]["messages"][-1]["content"][0]["content"] == "The user replied: yes, the cover too"
    saved = json.loads((out_dir / "conversation.json").read_text(encoding="utf-8"))
    assert [e["role"] for e in saved["entries"]] == ["user", "claude", "user", "claude", "user", "claude"]
    assert json.loads((out_dir / "compile.json").read_text(encoding="utf-8"))["status"] == "ok"


def test_do_at_the_terminal_stops_on_an_empty_line(folder, tmp_path, monkeypatch):
    from spec_surgeon import cli
    monkeypatch.setattr(compiler_module, "_client", lambda: FakeClient(resp(tool("ask_user", {"questions": ["Which?"]}))))
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    assert cli_main(["do", "remove some headers", str(folder), "--out", str(tmp_path / "o"), "--no-render"]) == 2
