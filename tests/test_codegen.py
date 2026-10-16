"""Phase 5 (Tier B): the source whitelist, the sandbox process, the hard limits, the verifier as the generated op's
test, the generate → verify → feedback loop (scripted client), running a passing op over a batch, and `do
--allow-codegen` end to end."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from make_fixtures import build
from spec_surgeon import compiler as compiler_module
from spec_surgeon.cli import main as cli_main
from spec_surgeon.codegen import (ALLOWED_MODULES, check_source, generate_op, hard_limits, run_generated,
                                  run_in_sandbox, trial)
from spec_surgeon.sandbox_runner import _guarded_import
from test_compiler import FakeClient, resp, tool

GOOD_OP = '''
from __future__ import annotations
from typing import Any, Dict
from spec_surgeon.ops.base import Op, JobException
from spec_surgeon.package import W, DocxPackage


class SetHeaderDistance(Op):
    name = "set_header_distance"
    mode = "formatting"
    description = "Set the header distance (pgMar/@w:header) of every section."
    params_schema = {"type": "object", "properties": {"twips": {"type": "integer"}}}
    select_schema = {}

    def plan(self, pkg, select, params):
        twips = params.get("twips")
        if not isinstance(twips, int) or isinstance(twips, bool) or twips < 0:
            raise JobException("twips must be a non-negative integer", "bad-params")
        main = pkg.main_part()
        frag = self.fragment(select, params)
        for i, sp in enumerate(DocxPackage.sect_prs(pkg.xml(main))):
            if sp.find(W + "pgMar") is None:
                raise JobException(f"section {i} has no pgMar", "structure")
            frag.property_assertions.append({"kind": "section_props", "part": main, "sections": [i],
                                             "elements": {"pgMar": {"set": {"header": str(twips)}}},
                                             "expected_sections": 1})
            frag.touches.append((main, f"sectPr:{i}", "pgMar"))
        frag.intended_parts = [main]
        frag.transform_data = {"main": main, "twips": str(twips)}
        frag.geometry_reference = "none"
        frag.geometry_reason = "the header moves by design"
        return frag

    def apply(self, pkg, frag):
        td = frag.transform_data
        root = pkg.xml(td["main"])
        for sp in DocxPackage.sect_prs(root):
            sp.find(W + "pgMar").set(W + "header", td["twips"])
        pkg.write_xml(td["main"], root)
'''

# the same op, but apply() also moves the footer — a change the plan never asserted
UNASSERTED_OP = GOOD_OP.replace('sp.find(W + "pgMar").set(W + "header", td["twips"])',
                                'sp.find(W + "pgMar").set(W + "header", td["twips"])\n'
                                '            sp.find(W + "pgMar").set(W + "footer", td["twips"])')

TEXT_OP = GOOD_OP.replace("frag.intended_parts = [main]",
                          "frag.intended_parts = [main]\n"
                          "        from spec_surgeon.ops.base import BlockDiff\n"
                          "        frag.diffs.append(BlockDiff(part=main, block_index=0, before='x', after='y'))")


@pytest.fixture
def folder(tmp_path) -> Path:
    d = tmp_path / "specs"
    d.mkdir()
    for i in range(2):
        (d / f"21 0{i} 00 – SPEC {i}.docx").write_bytes(build(f"21 0{i} 00", f"SPEC {i}"))
    return d


@pytest.mark.parametrize("src, needle", [
    ("import os\n", "'import os' is not allowed"),
    ("from subprocess import run\n", "from subprocess import"),
    ("from spec_surgeon.package import Path\n", "import ['Path'] is not allowed"),
    ("x = open('f')\n", "'open' is not allowed"),
    ("x = ().__class__.__bases__\n", "dunder attribute"),
    ("x = eval('1')\n", "'eval' is not allowed"),
    ("def f(pkg):\n    return pkg.output_bytes()\n", "attribute 'output_bytes'"),
    ("from lxml import etree\nt = etree.parse('x')\n", "attribute 'parse'"),
    ("from .base import Op\n", "from .base import"),
    ("x = 1\n", "exactly one class deriving from Op"),
])
def test_whitelist_rejects(src, needle):
    probs = check_source(src)
    assert any(needle in p for p in probs), probs


def test_whitelist_accepts_the_reference_op_and_the_import_guard_holds():
    assert check_source(GOOD_OP) == []
    imp = _guarded_import(ALLOWED_MODULES)
    with pytest.raises(ImportError):
        imp("os")
    with pytest.raises(ImportError):
        imp("spec_surgeon.batch")
    assert imp("re").__name__ == "re"


def test_sandbox_runs_a_good_op_and_the_verifier_passes_it(folder, tmp_path):
    f = sorted(folder.glob("*.docx"))[0]
    before = f.read_bytes()
    t = trial(GOOD_OP, f, {}, {"twips": 900}, tmp_path / "sb")
    assert t.passed, t.problems
    assert "section_props" in t.record.get("V10").summary
    assert f.read_bytes() == before                                         # only a copy was touched


def test_the_verifier_is_the_generated_ops_test(folder, tmp_path):
    f = sorted(folder.glob("*.docx"))[0]
    t = trial(UNASSERTED_OP, f, {}, {"twips": 900}, tmp_path / "sb1")
    assert not t.passed and any(p.startswith("V10 FAIL") for p in t.problems)
    t = trial(TEXT_OP, f, {}, {"twips": 900}, tmp_path / "sb2")
    assert not t.passed and "may not change text" in t.problems[0]
    t = trial(GOOD_OP, f, {}, {"twips": -1}, tmp_path / "sb3")
    assert not t.passed and "bad-params" in t.problems[0]


def test_sandbox_timeout(folder, tmp_path):
    loop = GOOD_OP.replace("twips = params.get(\"twips\")", "twips = params.get(\"twips\")\n        while True:\n            pass")
    sb = run_in_sandbox(loop, sorted(folder.glob("*.docx"))[0], {}, {"twips": 1}, tmp_path / "sb", timeout=3)
    assert not sb.ok and sb.code == "timeout"


def test_hard_limits():
    assert hard_limits({"diffs": [], "rev_delta": {"ins": 0}}) == []
    assert any("tracked" in p for p in hard_limits({"diffs": [], "rev_delta": {"del": -1}}))
    assert any("revision of any kind" in p for p in hard_limits({"diffs": [], "rev_delta": {"rPrChange": 3}}))
    assert any("no verified assertion kind covers ['word/numbering.xml']" in p
               for p in hard_limits({"intended_parts": ["word/numbering.xml"]}, ["word/document.xml", "word/styles.xml"]))
    assert any("settings.xml" in p for p in hard_limits({"intended_parts": ["word/settings.xml"]}))
    assert any("add package members" in p for p in hard_limits({"members_added": ["word/x.xml"]}))


def test_generate_feeds_failures_back_then_runs_the_batch(folder, tmp_path):
    fake = FakeClient(resp(tool("submit_op", {"source": UNASSERTED_OP, "params": {"twips": 900}})),
                      resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}}, "t2")))
    files = sorted(folder.glob("*.docx"))
    g = generate_op("make the header sit 0.625 in from the top", "no op sets header distance alone", files[:1],
                    tmp_path / "cg", client=fake)
    assert g.passed and g.name == "set_header_distance" and len(g.attempts) == 2
    assert any("V10 FAIL" in p for p in g.attempts[0]["problems"])
    feedback = fake.requests[1]["messages"][-1]["content"][0]
    assert feedback["is_error"] and "V10 FAIL" in feedback["content"]
    assert g.path.is_file() and json.loads(g.path.with_suffix(".history.json").read_text())["name"] == g.name
    history = json.loads((tmp_path / "cg" / "history.json").read_text(encoding="utf-8"))   # every attempt, kept
    assert history["passed"] is True and history["name"] == "set_header_distance"          # (Codex review on #10)
    assert [a["source"] for a in history["attempts"]] == [UNASSERTED_OP, GOOD_OP]
    before = {f: f.read_bytes() for f in files}
    results = run_generated(g, [str(folder)], tmp_path / "run")
    assert [r.passed for r in results] == [True, True]
    assert all((tmp_path / "run" / "outputs" / f.name).is_file() for f in files)
    assert {f: f.read_bytes() for f in files} == before                    # never in place


def test_generate_gives_up_after_three_attempts(folder, tmp_path):
    bad = lambda tid: resp(tool("submit_op", {"source": "import os\n"}, tid))
    g = generate_op("x", "y", sorted(folder.glob("*.docx"))[:1], tmp_path / "cg",
                    client=FakeClient(bad("a"), bad("b"), bad("c")))
    assert not g.passed and "after 3 attempt" in g.errors[0]
    history = json.loads((tmp_path / "cg" / "history.json").read_text(encoding="utf-8"))
    assert history["passed"] is False and len(history["attempts"]) == 3 and "after 3 attempt" in history["errors"][0]


def test_do_allow_codegen_end_to_end(folder, tmp_path, monkeypatch, capsys):
    unsupported = {"request": "r", "summary": "s", "unsupported": "no op sets the header distance alone",
                   "proposal": "set_header_distance"}
    offer = {"spec": unsupported, "custom_transform": "possible"}          # Claude judged it within the limits
    clients = iter([FakeClient(resp(tool("submit_editspec", offer))),
                    FakeClient(resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}}))),
                    FakeClient(resp(tool("submit_editspec", offer)))])
    monkeypatch.setattr(compiler_module, "_client", lambda: next(clients))
    assert cli_main(["do", "header 0.625in from the top", str(folder), "--out", str(tmp_path / "o"), "--no-render",
                     "--allow-codegen", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "passed on the samples" in out and "2 of 2 file(s) PASS" in out
    assert cli_main(["do", "header 0.625in from the top", str(folder), "--out", str(tmp_path / "o2"), "--no-render"]) == 2
    assert "No custom transform was written" in capsys.readouterr().out


# an op that writes a document property: docProps is no text part and no styles part — refused before the suite runs
DOCPROPS_OP = GOOD_OP.replace("frag.intended_parts = [main]", "frag.intended_parts = [main, 'docProps/core.xml']")


def test_a_generated_op_may_change_only_text_parts_and_styles(folder, tmp_path):
    t = trial(DOCPROPS_OP, sorted(folder.glob("*.docx"))[0], {}, {"twips": 900}, tmp_path / "sb")
    assert not t.passed and any("docProps/core.xml" in p and "no verified assertion kind" in p for p in t.problems)


def test_every_name_the_example_ops_import_is_allowed_or_deliberately_excluded():
    """The examples are what Claude copies: a name they import that is neither whitelisted nor excluded on purpose
    would make a faithful copy fail check_source for no reason Claude can see."""
    import ast
    from spec_surgeon.codegen import ALLOWED_FROM, ALLOWED_IMPORT, EXAMPLE_OPS, EXCLUDED_FROM_EXAMPLES
    ops_dir = Path(compiler_module.__file__).resolve().parent / "ops"
    for name in EXAMPLE_OPS:
        tree = ast.parse((ops_dir / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if node.level == 1:
                    mod = "spec_surgeon.ops" + (f".{mod}" if mod else "")
                elif node.level == 2:
                    mod = f"spec_surgeon.{mod}"
                for a in node.names:
                    ok = a.name in ALLOWED_FROM.get(mod, set()) or a.name in EXCLUDED_FROM_EXAMPLES.get(mod, set())
                    assert ok, f"{name}: from {mod} import {a.name}"
            elif isinstance(node, ast.Import):
                for a in node.names:
                    assert a.name in ALLOWED_IMPORT, f"{name}: import {a.name}"
    assert "set_font.py" in EXAMPLE_OPS


def test_the_writer_looks_first_and_every_tool_call_is_answered(folder, tmp_path):
    f = sorted(folder.glob("*.docx"))[0]
    fake = FakeClient(resp(tool("show_xml", {"file": f.name, "start": 0, "count": 2}, "x1"),
                           tool("find_paragraphs", {"file": f.name, "regex": "SPEC"}, "x2"),
                           tool("list_styles", {"file": f.name}, "x3"),
                           tool("show_part_text", {"file": f.name, "part": "word/nope.xml"}, "x4"),
                           tool("delete_everything", {}, "x5")),
                      resp(tool("submit_op", {"source": "import os\n"}, "a1"),
                           tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}}, "a2")),
                      resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}}, "a3")))
    g = generate_op("header 0.625 in", "set the header distance", [f], tmp_path / "cg", client=fake,
                    brief="REQUEST: header 0.625 in\nUSER: only the header, not the footer")
    assert g.passed and len(g.attempts) == 2 and g.trials and g.trials[0].output_md5
    first = fake.requests[0]["messages"][0]["content"]
    assert first.startswith("THE CONVERSATION WITH THE USER") and "only the header, not the footer" in first
    looked = fake.requests[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in looked] == ["x1", "x2", "x3", "x4", "x5"]
    assert "<w:p" in looked[0]["content"] and "SPEC" in looked[1]["content"]
    assert looked[3]["is_error"] and looked[4]["is_error"] and "unknown tool" in looked[4]["content"]
    tried = fake.requests[2]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in tried] == ["a1", "a2"]
    assert "not allowed" in tried[0]["content"] and "one submission per turn" in tried[1]["content"]
    assert "set_font.py" in fake.requests[0]["system"] and "custom_transform" not in fake.requests[0]["system"]
    assert "run fonts and font sizes" in fake.requests[0]["system"]                    # the limits are in the prompt


def test_a_turn_without_a_submission_is_not_an_attempt(folder, tmp_path):
    from fake_claude import text
    fake = FakeClient(resp(text("Let me think."), stop="end_turn"),
                      resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}})))
    g = generate_op("x", "y", sorted(folder.glob("*.docx"))[:1], tmp_path / "cg", client=fake)
    assert g.passed and len(g.attempts) == 1


def test_the_history_keeps_the_exchange_and_an_api_error_is_a_recorded_failure(folder, tmp_path, monkeypatch):
    from fake_claude import api_error, raise_on_final
    from spec_surgeon import llm
    f = sorted(folder.glob("*.docx"))[:1]
    fake = FakeClient(resp(tool("submit_op", {"source": "import os\n"}, "a1")),
                      raise_on_final(api_error("Overloaded", 529, "req_9")))
    g = generate_op("x", "y", f, tmp_path / "cg", client=fake)
    assert not g.passed and g.errors == ["the call to the model failed: APIStatusError: Overloaded · HTTP 529 · request id req_9"]
    h = json.loads((tmp_path / "cg" / "history.json").read_text(encoding="utf-8"))
    assert h["passed"] is False and h["errors"] == g.errors and [a["source"] for a in h["attempts"]] == ["import os\n"]
    assert [e.get("role") for e in h["transcript"]] == ["user", "assistant", "user", None]
    assert h["transcript"][0]["content"].startswith("REQUEST: x") and h["transcript"][0]["samples"] == [str(f[0])]
    assert h["transcript"][2]["tool_results"][0]["tool_use_id"] == "a1" and h["transcript"][2]["tool_results"][0]["is_error"]
    assert h["transcript"][-1]["error"].startswith("APIStatusError: Overloaded")
    assert h["usage"]["calls"] == 1 and h["model"] == g.model and h["request"] == "x" and h["proposal"] == "y"
    with pytest.raises(AssertionError, match="more turns than were scripted"):        # a defect propagates
        generate_op("x", "y", f, tmp_path / "cg2", client=FakeClient())

    def missing():
        raise llm.ClaudeUnavailable("install the Claude layer")
    monkeypatch.setattr(compiler_module, "_client", missing)
    g = generate_op("x", "y", f, tmp_path / "cg3")
    assert not g.passed and g.errors == ["Claude is not available: install the Claude layer"]
    h = json.loads((tmp_path / "cg3" / "history.json").read_text(encoding="utf-8"))
    assert h["transcript"][-1]["error"].startswith("ClaudeUnavailable")


def test_run_generated_keeps_the_batch_rules(folder, tmp_path, monkeypatch):
    fake = FakeClient(resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}})))
    g = generate_op("x", "y", sorted(folder.glob("*.docx"))[:1], tmp_path / "cg", client=fake)
    with pytest.raises(Exception, match="results folder|inside"):
        run_generated(g, [str(folder)], folder)                            # a run never reads where it writes
    other = tmp_path / "other"
    other.mkdir()
    twin = other / sorted(folder.glob("*.docx"))[0].name
    twin.write_bytes(build("21 00 00", "TWIN"))
    res = run_generated(g, [str(folder), str(twin)], tmp_path / "run")
    names = sorted(p.name for p in (tmp_path / "run" / "outputs").iterdir())
    assert len(names) == 3 and all(r.passed for r in res)                 # same name, two folders: two outputs
    import spec_surgeon.codegen as cg
    real = cg.trial

    def tampered(*a, **k):
        t = real(*a, **k)
        t.output_md5 = "0" * 32
        return t
    monkeypatch.setattr(cg, "trial", tampered)
    res = run_generated(g, [str(folder)], tmp_path / "run2")
    assert not any(r.passed for r in res) and "not the one the suite verified" in res[0].problems[-1]
    assert not (tmp_path / "run2" / "outputs").exists()


def test_the_custom_transform_has_a_preview(folder, tmp_path):
    from spec_surgeon.preview import preview_generated
    fake = FakeClient(resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}})))
    g = generate_op("x", "y", sorted(folder.glob("*.docx")), tmp_path / "cg", client=fake)
    gp = preview_generated(g, tmp_path / "pv", do_render=False)
    assert [s.passed for s in gp.samples] == [True, True] and all(s.output.is_file() for s in gp.samples)
    assert all(s.checks["V10"] == "PASS" for s in gp.samples)
    assert "custom transform" in gp.to_text() and (tmp_path / "pv" / "PREVIEW.md").is_file()
    log = (tmp_path / "pv" / "logs" / "preview.log").read_text(encoding="utf-8")
    assert "one-off custom transform" in log and log.count("verification PASS") == 2
    assert json.loads((tmp_path / "pv" / "preview.json").read_text(encoding="utf-8"))["log"] == "logs/preview.log"
