"""Phase 6: the advisor — per exception a recommendation or a re-compiled spec pinned to the file's pattern, held to
the compiler's checks and to planning cleanly on that very file (scripted client, no API calls)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from make_fixtures import build, build_rich
from spec_surgeon import compiler as compiler_module
from spec_surgeon.advisor import advise, advise_batch
from spec_surgeon.batch import run_batch
from spec_surgeon.cli import main as cli_main
from spec_surgeon.editspec import load_spec
from test_compiler import FakeClient, resp, text, tool

SPECS = Path(__file__).resolve().parent.parent / "examples" / "specs"


@pytest.fixture
def batch_out(tmp_path) -> Path:
    """A batch where the rich file raises `expect` (the spec expected 3 removed members; it has 9)."""
    d = tmp_path / "in"
    d.mkdir()
    (d / "21 05 00 – A.docx").write_bytes(build("21 05 00", "A"))
    (d / "21 13 13 – RICH.docx").write_bytes(build_rich())
    spec = load_spec(SPECS / "remove-all-headers-footers.json")
    spec["ops"][0]["expect"] = {"members_removed": 3}
    out = tmp_path / "out"
    b = run_batch([str(d)], "edit-spec", {}, out, workers=1, renderer="none", do_render=False, spec=spec)
    assert [f.status for f in b.files] == ["PASS", "EXCEPTION"]
    return out


def _record(out: Path):
    return json.loads(next((out / "exceptions").glob("*.exception.json")).read_text(encoding="utf-8"))


def test_recommendation(batch_out):
    fake = FakeClient(resp(tool("show_part_text", {"file": "21 13 13 – RICH.docx", "part": "word/header4.xml"})),
                      resp(tool("submit_recommendation", {"text": "This file has first/even headers; expect 9."}, "t2")))
    adv = advise(_record(batch_out), client=fake)
    assert adv.kind == "recommendation" and "expect 9" in adv.text
    first = json.loads(fake.requests[0]["messages"][0]["content"])
    assert first["exception"]["code"] == "expect" and first["structure_pattern"]


def test_respec_is_pinned_to_the_pattern_and_must_plan_on_the_file(batch_out):
    rec = _record(batch_out)
    fixed = copy.deepcopy(rec["spec"])
    fixed["ops"][0]["expect"] = {"members_removed": 9}
    still_wrong = copy.deepcopy(rec["spec"])                                     # still expects 3
    fake = FakeClient(resp(tool("submit_editspec", {"spec": still_wrong})),
                      resp(tool("submit_editspec", {"spec": fixed, "why": "9 parts here"}, "t2")))
    adv = advise(rec, client=fake)
    assert adv.kind == "respec" and adv.spec["applies_to"]["pattern_ids"] == [rec["fingerprint"]["pattern_id"]]
    assert "[expect]" in fake.requests[1]["messages"][-1]["content"][0]["content"]
    fake = FakeClient(resp(tool("submit_editspec", {"spec": still_wrong})),
                      resp(tool("submit_editspec", {"spec": still_wrong}, "t2")))
    adv = advise(rec, client=fake)
    assert adv.kind == "failed" and adv.errors


def test_respec_may_not_introduce_new_words(batch_out):
    rec = _record(batch_out)
    sneaky = {"request": "r", "summary": "s", "mode": "content", "ops": [{"op": "replace_text", "select": {"scope": ["document"]},
              "params": {"find": "FM Global", "replace": "Zurich", "tracked": True, "author": "x"}}]}
    fake = FakeClient(resp(tool("submit_editspec", {"spec": sneaky})), resp(text("Cannot do it safely; ask the user."), stop="end_turn"))
    adv = advise(rec, client=fake)
    assert adv.kind == "recommendation"
    assert "never gave them verbatim" in fake.requests[1]["messages"][-1]["content"][0]["content"]


def test_advise_batch_and_cli(batch_out, monkeypatch, capsys):
    monkeypatch.setattr(compiler_module, "_client",
                        lambda: FakeClient(resp(tool("submit_recommendation", {"text": "Accept or fix first."}))))
    from spec_surgeon import advisor as advisor_module
    monkeypatch.setattr(advisor_module, "_client", compiler_module._client)
    assert cli_main(["advise", str(batch_out)]) == 0
    assert "recommendation" in capsys.readouterr().out
    md = next((batch_out / "advice").glob("*.advice.md")).read_text(encoding="utf-8")
    assert "Accept or fix first." in md
