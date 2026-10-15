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


def test_do_allow_codegen_end_to_end(folder, tmp_path, monkeypatch, capsys):
    unsupported = {"request": "r", "summary": "s", "unsupported": "no op sets the header distance alone",
                   "proposal": "set_header_distance"}
    clients = iter([FakeClient(resp(tool("submit_editspec", {"spec": unsupported}))),
                    FakeClient(resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}}))),
                    FakeClient(resp(tool("submit_editspec", {"spec": unsupported})))])
    monkeypatch.setattr(compiler_module, "_client", lambda: next(clients))
    assert cli_main(["do", "header 0.625in from the top", str(folder), "--out", str(tmp_path / "o"), "--no-render",
                     "--allow-codegen", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "passed on the samples" in out and "2 of 2 file(s) PASS" in out
    assert cli_main(["do", "header 0.625in from the top", str(folder), "--out", str(tmp_path / "o2"), "--no-render"]) == 2
    assert "No custom transform was written" in capsys.readouterr().out
