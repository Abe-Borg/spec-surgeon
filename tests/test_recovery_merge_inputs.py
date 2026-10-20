"""Recovery keeps batch-1 input files inspectable, bounded, and pinned through delivery.

An input table is listed only when the request names it (``input_files``) or the goal does; nothing is
discovered beside the documents."""
from __future__ import annotations

import json
import os

import pytest

from fake_claude import FakeClient, resp, tool
from make_fixtures import build_batch1
from spec_surgeon import recovery
from spec_surgeon.repair_memory import CaseStore


VALUES = {"SITE_NAME": "Ashburn VA3", "OWNER_NAME": "Acme Cloud LLC", "PROJECT NO": "2026-117"}


def _options(**kwargs):
    return recovery.RecoveryOptions(**{"render": False, "use_memory": False, "max_attempts": 1, **kwargs})


def _goal(table):
    return {"request": "Fill project variables from the supplied table", "mode": "untracked",
            "summary": "Fill project variables", "ops": [
                {"op": "fill_variables", "params": {"table": {"file": str(table)}, "tracked": False}}]}


def _submit(goal):
    return resp(tool("submit_repair", {"candidate": {"kind": "spec", "spec": goal}}))


@pytest.fixture
def batch(tmp_path):
    source = tmp_path / "spec.docx"
    source.write_bytes(build_batch1())
    table = tmp_path / "variables.json"
    table.write_text(json.dumps(VALUES), encoding="utf-8")
    return source, table


def test_raw_recovery_reads_listed_table_and_pins_its_bytes_for_delivery(batch, tmp_path):
    source, table = batch
    goal = _goal(table)
    client = FakeClient(resp(tool("read_input_file", {"path": str(table)})), _submit(goal))
    result = recovery.recover([source], goal["request"], tmp_path / "recovery",
                              options=_options(), client=client, input_files=[table])
    assert result.ready, result.to_text()
    message = json.loads(client.requests[0]["messages"][0]["content"])
    assert message["input_files"][0]["path"] == str(table)
    assert "read_input_file" in [t["name"] for t in client.requests[0]["tools"]]
    answer = client.requests[1]["messages"][-1]["content"][0]
    assert "Ashburn VA3" in answer["content"]
    assert not answer.get("is_error")
    assert str(table) in result.key["recovery_inputs"]
    # A same-size/same-mtime replacement still invalidates the approval.
    stat = table.stat()
    table.write_text(table.read_text().replace("Ashburn", "Newburg"), encoding="utf-8")
    os.utime(table, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(ValueError, match="changed"):
        recovery.deliver_recovery(result)
    assert not (result.out_dir / "outputs").exists()


def test_explicit_goal_input_outside_batch_folder_is_listed_and_readable(batch, tmp_path):
    source, local_table = batch
    remote = tmp_path / "shared"
    remote.mkdir()
    table = remote / "variables.json"
    local_table.rename(table)
    goal = _goal(table)
    client = FakeClient(resp(tool("read_input_file", {"path": str(table)})), _submit(goal))
    result = recovery.recover([source], goal["request"], tmp_path / "recovery", spec=goal,
                              options=_options(), client=client)
    assert result.ready, result.to_text()
    answer = client.requests[1]["messages"][-1]["content"][0]
    assert not answer.get("is_error"), answer
    assert "Acme Cloud LLC" in answer["content"]


def test_raw_recovery_refuses_unlisted_table_even_when_it_exists(batch, tmp_path):
    source, local_table = batch
    private = tmp_path / "unlisted"
    private.mkdir()
    table = private / "variables.json"
    table.write_bytes(local_table.read_bytes())
    goal = _goal(table)
    client = FakeClient(resp(tool("read_input_file", {"path": str(table)})), _submit(goal))
    result = recovery.recover([source], goal["request"], tmp_path / "recovery",
                              options=_options(), client=client)
    assert not result.ready
    assert "outside the current request" in result.files[0].reason
    answer = client.requests[1]["messages"][-1]["content"][0]
    assert answer["is_error"]


def test_changed_input_contents_prevent_automatic_memory_reuse(batch, tmp_path, monkeypatch):
    source, table = batch
    store = CaseStore(tmp_path / "repairs.sqlite3")
    monkeypatch.setattr("spec_surgeon.repair_memory.CaseStore", lambda: store)
    goal = {"request": "Set the left margin to one inch", "mode": "formatting", "summary": "Margin",
            "ops": [{"op": "set_page_setup", "params": {"margins": {"left": 1440}}}]}
    first = recovery.recover([source], goal["request"], tmp_path / "first", spec=goal, input_files=[table],
                             options=_options(use_memory=True), client=FakeClient(_submit(goal)))
    warm = recovery.recover([source], goal["request"], tmp_path / "warm", spec=goal, input_files=[table],
                            options=_options(use_memory=True), client=FakeClient())
    assert first.ready and warm.ready
    assert warm.budget["calls"] == 0
    table.write_text(json.dumps({**VALUES, "SITE_NAME": "Dallas TX1"}), encoding="utf-8")
    changed = recovery.recover([source], goal["request"], tmp_path / "changed", spec=goal, input_files=[table],
                               options=_options(use_memory=True), client=FakeClient(_submit(goal)))
    assert changed.ready
    assert changed.budget["calls"] == 1
    records = store.list_cases()
    assert len({r["goal_fingerprint"] for r in records}) == 2


def test_recovery_folder_cannot_contain_an_explicit_input(batch, tmp_path):
    source, local_table = batch
    target = tmp_path / "recovery"
    target.mkdir()
    table = target / "variables.json"
    table.write_bytes(local_table.read_bytes())
    with pytest.raises(ValueError, match="input"):
        recovery.recover([source], "Fill project variables", target, spec=_goal(table),
                         options=_options(), client=FakeClient())
    assert list(target.iterdir()) == [table]


def test_redacted_remembered_table_path_does_not_consume_the_only_attempt(batch, tmp_path, monkeypatch):
    source, table = batch
    store = CaseStore(tmp_path / "repairs.sqlite3")
    monkeypatch.setattr("spec_surgeon.repair_memory.CaseStore", lambda: store)
    goal = _goal(table)
    first = recovery.recover([source], goal["request"], tmp_path / "first", spec=goal,
                             options=_options(use_memory=True), client=FakeClient(_submit(goal)))
    assert first.ready, first.to_text()
    remembered = store.list_cases()[0]
    assert remembered["candidate"]["spec"]["ops"][0]["params"]["table"]["file"] == "[local path omitted]"
    warm = recovery.recover([source], goal["request"], tmp_path / "warm", spec=goal,
                            options=_options(use_memory=True), client=FakeClient(_submit(goal)))
    assert warm.ready, warm.to_text()
    assert warm.budget["calls"] == 1
    assert len(warm.attempts) == 1
    assert any("remembered repair could not be reused" in note for note in warm.notes)
