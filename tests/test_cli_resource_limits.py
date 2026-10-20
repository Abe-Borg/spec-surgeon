"""Model overrides carry explicit token limits through both CLI conversation paths."""
import json

import pytest

from fake_claude import FakeClient, resp, tool
from make_fixtures import build
from spec_surgeon import cli, compiler, contexts
from spec_surgeon.resources import DEFAULT_CONTEXT_TOKENS, DEFAULT_MAX_OUTPUT_TOKENS


@pytest.mark.parametrize("command", ["do", "retry"])
@pytest.mark.parametrize("interactive", [False, True])
@pytest.mark.parametrize("override", [False, True])
def test_cli_context_and_response_limits_reach_the_model_call(
        tmp_path, monkeypatch, command, interactive, override):
    source = tmp_path / "source.docx"
    source.write_bytes(build("21 13 13", "FIRE SUPPRESSION"))
    request = "Remove some headers."
    if command == "do":
        argv = ["do", request, str(source)]
    else:
        previous = tmp_path / "previous"
        previous.mkdir()
        (previous / "set_aside.json").write_text(json.dumps({
            "request": request, "files": [{"source": str(source), "reason": "Needs clarification"}]
        }), encoding="utf-8")
        argv = ["retry", str(previous)]
    argv += ["--out", str(tmp_path / "output"), "--no-render"]
    if override:
        argv += ["--model", "smaller-model", "--max-context-tokens", "131072",
                 "--max-output-tokens", "8192"]
    fake = FakeClient(resp(tool("ask_user", {"questions": ["Which headers?"]})))
    monkeypatch.setattr(compiler, "_client", lambda: fake)
    monkeypatch.setattr(cli, "_interactive", lambda: interactive)
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    limits = []
    original = contexts.manage_context

    def manage_context(messages, **kwargs):
        limits.append(kwargs["max_tokens"])
        return original(messages, **kwargs)

    monkeypatch.setattr(contexts, "manage_context", manage_context)
    assert cli.main(argv) == 2  # The unanswered clarification stops before any edit.
    assert limits == [131072 if override else DEFAULT_CONTEXT_TOKENS]
    assert len(fake.requests) == 1
    assert fake.requests[0]["model"] == ("smaller-model" if override else compiler.DEFAULT_MODEL)
    assert fake.requests[0]["max_tokens"] == (8192 if override else DEFAULT_MAX_OUTPUT_TOKENS)


@pytest.mark.parametrize("command", ["do", "retry"])
@pytest.mark.parametrize("flag", ["--max-context-tokens", "--max-output-tokens"])
@pytest.mark.parametrize("value", ["0", "-1", "1.5"])
def test_invalid_cli_token_limits_are_argument_errors(tmp_path, capsys, command, flag, value):
    output = tmp_path / "output"
    with pytest.raises(SystemExit) as error:
        cli.main([command, "request-or-run", "--out", str(output), flag, value])
    assert error.value.code == 2
    assert "positive integer" in capsys.readouterr().err
    assert not output.exists()


def test_cli_keeps_model_token_limits_when_handing_off_to_custom_transform(tmp_path, monkeypatch):
    from test_codegen import GOOD_OP
    from test_compiler import header_outcome_proposal

    source = tmp_path / "source.docx"
    source.write_bytes(build("21 13 13", "FIRE SUPPRESSION"))
    unsupported = resp(tool("submit_editspec", {"spec": {
            "request": "header 0.625in from the top", "summary": "Change header distance",
            "unsupported": "no op sets the header distance alone", "proposal": "set_header_distance"
        }, "custom_transform": "possible"}))
    fake = FakeClient(
        unsupported,
        resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}}, "writer")),
    )
    monkeypatch.setattr(compiler, "_client", lambda: fake)
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    limits = []
    original = contexts.manage_context

    def manage_context(messages, **kwargs):
        limits.append(kwargs["max_tokens"])
        return original(messages, **kwargs)

    monkeypatch.setattr(contexts, "manage_context", manage_context)
    args = ["do", "header 0.625in from the top", str(source),
                     "--out", str(tmp_path / "output"), "--no-render", "--allow-codegen",
                     "--model", "claude-sonnet-5-5", "--max-context-tokens", "131072",
                     "--max-output-tokens", "8192"]
    assert cli.main(args) == 0
    assert (tmp_path / "output" / "codegen").exists()
    assert not (tmp_path / "output" / "run").exists()  # preview still needs the user's execution decision
    assert limits == [131072] * 2
    assert len(fake.requests) == 2
    assert all(call["model"] == "claude-sonnet-5-5" and call["max_tokens"] == 8192 for call in fake.requests)
