"""Phase 4: the compiler loop (scripted fake client — no API calls), the submission checks (validation, traceable
words, dry plan), project memory, the preview and the approval token, and the `do` command end to end."""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from make_fixtures import build, build_template, TEMPLATE_FILLS
from spec_surgeon import compiler as compiler_module
from spec_surgeon.cli import main as cli_main
from spec_surgeon.compiler import compile_request, untraceable
from spec_surgeon.editspec import load_spec
from spec_surgeon.preview import approval_token, make_preview

SPECS = Path(__file__).resolve().parent.parent / "examples" / "specs"
REMOVE = json.loads((SPECS / "remove-all-headers-footers.json").read_text(encoding="utf-8"))


def tool(name, inp, tid="t1"):
    return SimpleNamespace(type="tool_use", id=tid, name=name, input=inp)


def text(t):
    return SimpleNamespace(type="text", text=t)


def resp(*blocks, stop="tool_use"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop, stop_details=None,
                           usage=SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=0))


class FakeClient:
    """client.beta.messages.create(**kw) returns the scripted responses in order and records every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.requests.append(copy.deepcopy({k: v for k, v in kw.items() if k != "system"}) | {"system": kw.get("system")})
        if not self.responses:
            raise AssertionError("the compiler asked for more turns than scripted")
        return self.responses.pop(0)


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
    assert first["model"] == "claude-opus-5-5" and first["fallbacks"] == "default"
    assert "OP CATALOG" in first["system"] and "apply_header_footer_template" in first["system"]
    assert "remove all headers and footers" in first["messages"][0]["content"]
    result = fake.requests[1]["messages"][-1]["content"][0]
    assert result["type"] == "tool_result" and "21 00 00" in result["content"]          # the profile came back
    assert res.usage["input_tokens"] == 200


def test_a_rejected_spec_goes_back_once(folder):
    bad = copy.deepcopy(REMOVE)
    bad["ops"][0]["op"] = "remove_headers"
    fake = FakeClient(resp(tool("submit_editspec", {"spec": bad})), resp(tool("submit_editspec", {"spec": REMOVE}, "t2")))
    res = compile_request("remove all headers and footers", files(folder), client=fake)
    assert res.status == "ok"
    err = fake.requests[1]["messages"][-1]["content"][0]
    assert err["is_error"] and "unknown op 'remove_headers'" in err["content"]
    fake = FakeClient(resp(tool("submit_editspec", {"spec": bad})), resp(tool("submit_editspec", {"spec": bad}, "t2")))
    res = compile_request("remove all headers and footers", files(folder), client=fake)
    assert res.status == "failed" and "rejected twice" in res.errors[0]


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


def test_template_request_with_project_memory(folder, tmp_path):
    tpl = tmp_path / "00 00 00 TEMPLATE.docx"
    tpl.write_bytes(build_template())
    (folder / "PROJECT.json").write_text(json.dumps({"stamp": "ISSUED FOR CONSTRUCTION  2026-02-15"}), encoding="utf-8")
    spec = load_spec(SPECS / "apply-header-footer-template.json")
    spec["ops"][0]["params"]["template"] = str(tpl)
    fake = FakeClient(resp(tool("read_template", {})), resp(tool("submit_editspec", {"spec": spec}, "t2")))
    res = compile_request("make the header/footer look like this template", files(folder), template=tpl, client=fake)
    assert res.status == "ok", res.errors                                          # the stamp is traceable via memory
    assert "PROJECT MEMORY" in fake.requests[0]["messages"][0]["content"]
    assert "ISSUED FOR PERMIT" in fake.requests[1]["messages"][-1]["content"][0]["content"]   # the template's slots
    (folder / "PROJECT.json").unlink()
    fake = FakeClient(resp(tool("submit_editspec", {"spec": spec})), resp(tool("ask_user", {"questions": ["Stamp?"]}, "t2")))
    assert compile_request("make the header/footer look like this template", files(folder), template=tpl,
                           client=fake).status == "questions"


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
    monkeypatch.setattr(compiler_module, "_client", script)
    out = tmp_path / "out"
    assert cli_main(["do", "remove all headers and footers", str(folder), "--out", str(out), "--no-render"]) == 0
    printed = capsys.readouterr().out
    assert "PREVIEW" in printed and "Not run." in printed and not (out / "run").exists()
    token = printed.split("--approval ")[1].split()[0]
    spec_path = out / "preview" / "spec.json"
    assert cli_main(["run-spec", str(spec_path), str(folder), "--out", str(tmp_path / "r1"), "--no-render",
                     "--approval", "0" * 16]) == 2                                   # wrong token: refused
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
