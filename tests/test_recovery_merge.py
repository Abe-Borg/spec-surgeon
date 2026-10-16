"""Recovery retains upstream naming behavior alongside final outcome verification."""
import copy

import pytest

from fake_claude import FakeClient, resp, tool
from make_fixtures import build_batch1
from spec_surgeon.recovery import RecoveryOptions, deliver_recovery, recover
from test_naming import CONVENTION


def goal():
    return {"stages": [
        {"spec": {"request": "Name files from their section number and title", "summary": "Name outputs",
                  "mode": "formatting", "ops": [{"op": "name_output", "params": copy.deepcopy(CONVENTION)}]}},
        {"spec": {"request": "Set the left margin to one inch", "summary": "Margins", "mode": "formatting",
                  "ops": [{"op": "set_page_setup", "params": {"margins": {"left": 1440}}}]}},
    ]}


def submit(chain):
    return resp(tool("submit_repair", {"candidate": {"kind": "chain", "chain": chain}}))


def test_recovery_exports_the_verified_name_and_checks_final_requirements(tmp_path):
    source = tmp_path / "old-name.docx"
    source.write_bytes(build_batch1())
    original = source.read_bytes()
    chain = goal()
    result = recover([source], "Name the outputs and set the left margin to one inch", tmp_path / "recovery",
                     chain=chain, options=RecoveryOptions(render=False, use_memory=False), client=FakeClient(submit(chain)))
    assert len(result.ready) == 1, result.to_text()
    assert all(x["status"] == "PASS" for x in result.ready[0].outcomes)
    assert result.ready[0].out_name == "211319 - Preaction Sprinkler Systems.docx"
    dest = deliver_recovery(result)
    assert [p.name for p in dest.iterdir()] == [result.ready[0].out_name]
    assert source.read_bytes() == original
    result.ready[0].out_name = "altered-name.docx"
    with pytest.raises(ValueError, match="changed"):
        deliver_recovery(result, tmp_path / "altered-export")


def test_recovery_refuses_same_explicit_name_across_separate_problem_groups(tmp_path):
    sources = [tmp_path / "first.docx", tmp_path / "second.docx"]
    for source in sources:
        source.write_bytes(build_batch1())
    chain = goal()
    result = recover(sources, "Name the outputs and set the left margin to one inch", tmp_path / "recovery",
                     chain=chain, prior_failures=[{"source": str(p), "code": str(i)} for i, p in enumerate(sources)],
                     options=RecoveryOptions(render=False, use_memory=False), client=FakeClient(submit(chain), submit(chain)))
    assert len(result.attempts) == 2
    assert not result.ready
    assert all("naming collision" in f.reason for f in result.files)
    with pytest.raises(ValueError, match="No verified repair"):
        deliver_recovery(result)
    assert not (result.out_dir / "outputs").exists()
