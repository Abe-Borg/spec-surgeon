"""Recovery's pace (backlog P5). The owner's 27-file recovery took 30 minutes: one trial per file, one after the
other; three checkpoints per model call, 5–18 s each, every one hashing every pinned file three times over and
writing every document's binding, which for a target with ``all: true`` listed every paragraph; and Stop waited for
the checkpoint in flight. Now a model turn writes one checkpoint, at its end; a checkpoint reads a file again only
when its size or mtime moved; a global target is bound once, by its count, on both sides of the verifier; and the
deterministic baseline trials the files that share a method in one run, over the batch's workers."""
from __future__ import annotations

import copy
import json

import pytest

from fake_claude import FakeClient, resp, tool
from spec_surgeon import recovery, review_display
from spec_surgeon.goal_contracts import resolve_goal
from spec_surgeon.goals import compile_goal_candidate, validate_binding
from spec_surgeon.verify.effects import verify_effects
from test_goal_contracts import contract, execute
from test_goals import write_document
from test_recovery import candidate, margin, opts, spec, submit

ALL_FONT = {"id": "font", "type": "format", "target": {"all": True}, "font": {"name": "Arial", "size": 11}}
ALL_WORDS = {"id": "words", "type": "replace_text", "target": {"all": True}, "find": "specified",
             "replace": "approved", "expected_matches": 1, "tracked": True}


def saves(monkeypatch):
    """Every checkpoint written, as the budget stood (``result.budget`` is set before the turn's checkpoint)."""
    seen = []
    original = recovery._save

    def counted(result):
        seen.append(copy.deepcopy(result.budget))
        original(result)
    monkeypatch.setattr(recovery, "_save", counted)
    return seen


def test_a_model_turn_writes_one_checkpoint_at_its_end(tmp_path, monkeypatch):
    seen = saves(monkeypatch)
    source = tmp_path / "21 13 13.docx"
    from make_fixtures import build
    source.write_bytes(build("21 13 13", "SPRINKLER SYSTEMS"))
    goal = spec([margin()])
    client = FakeClient(resp(tool("list_styles", {"file": source.name})), submit(candidate(goal)))
    result = recovery.recover([source], goal["request"], tmp_path / "session", spec=goal, options=opts(), client=client)
    assert result.ready and len(client.requests) == 2
    # One before the investigation (its groups), one per model turn, one when the investigation is over.
    assert len(seen) == 2 + len(client.requests)
    assert [s.get("calls") for s in seen] == [None, 1, 2, 2]
    reloaded = recovery.load_recovery(result.out_dir)
    assert reloaded.ready and reloaded.budget["calls"] == 2


def test_a_checkpoint_reads_a_pinned_file_again_only_when_it_moved(tmp_path, monkeypatch):
    source = tmp_path / "21 13 13.docx"
    from make_fixtures import build
    source.write_bytes(build("21 13 13", "SPRINKLER SYSTEMS"))
    goal = spec([margin()])
    result = recovery.recover([source], goal["request"], tmp_path / "session", spec=goal, options=opts(),
                              client=FakeClient(submit(candidate(goal))))
    hashed = []
    original = recovery._sha

    def counted(path):
        hashed.append(str(path))
        return original(path)
    monkeypatch.setattr(recovery, "_sha", counted)
    token, inputs = result.token, copy.deepcopy(result.token_inputs)
    recovery._save(result)
    recovery._save(result)
    assert hashed == [] and result.token == token and result.token_inputs == inputs
    import os
    data = source.read_bytes()
    source.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    os.utime(source, ns=(os.stat(source).st_mtime_ns + 10 ** 9, os.stat(source).st_mtime_ns + 10 ** 9))
    recovery._save(result)
    assert hashed == [str(source)] and result.token == ""          # moved: read again, and the pins no longer hold
    with pytest.raises(ValueError, match="start a fresh recovery"):
        recovery.load_recovery(result.out_dir)


def test_a_global_target_is_bound_once_by_its_count(tmp_path):
    source = write_document(tmp_path / "spec.docx")
    goal = contract(ALL_FONT, ALL_WORDS)
    resolution = resolve_goal(source, goal)
    assert resolution["status"] == "resolved"
    count = resolution["effects"]["font"]["source_count"]
    assert count >= 3 and resolution["effects"]["font"]["candidates"] == [] and resolution["candidates"] == []
    assert resolution["binding"]["effects"] == {
        "font": {"targets": "all", "choice": "unique", "source_count": count},
        "words": {"targets": "all", "choice": "unique", "source_count": count}}
    assert validate_binding(source, goal, resolution["binding"]) == resolution["binding"]
    output = execute(source, goal, resolution["binding"])          # the content stage reads its paragraphs itself
    from spec_surgeon.verify.reader import VPackage
    assert b"approved" in VPackage(output).read("word/document.xml")
    assert verify_effects(source, output, goal, resolution["binding"])["passed"]
    assert "every paragraph in scope" in json.dumps(review_display.targets({"documents": [
        {"source": str(source), "resolution": resolution, "binding": resolution["binding"]}]}))


@pytest.mark.parametrize("damage", ["count", "listed", "subset"])
def test_both_sides_refuse_a_global_binding_that_is_not_the_whole_count(tmp_path, damage):
    source = write_document(tmp_path / "spec.docx")
    goal = contract(ALL_FONT)
    resolution = resolve_goal(source, goal)
    output = execute(source, goal, resolution["binding"])
    bad = copy.deepcopy(resolution["binding"])
    if damage == "count":
        bad["effects"]["font"]["source_count"] += 1
    elif damage == "listed":
        bad["effects"]["font"] = {"targets": [], "choice": "unique"}
    else:
        bad["effects"]["font"] = {"targets": [], "choice": "user"}
    with pytest.raises(Exception):
        compile_goal_candidate(source, goal, bad)
    assert not verify_effects(source, output, goal, bad)["passed"]


def test_recovery_keeps_a_global_binding_once_everywhere_it_is_written(tmp_path):
    sources = [write_document(tmp_path / f"spec-{i}.docx") for i in range(2)]
    result = recovery.prepare_recovery(sources, "Everything in Arial", tmp_path / "session",
                                       adaptive_contract=contract(ALL_FONT), options=opts())
    assert len(result.ready) == 2
    compact = {"targets": "all", "choice": "unique", "source_count": 3}
    for source in sources:
        assert result.key["adaptive_bindings"][str(source)]["effects"]["font"] == compact
        assert result.session_state["goal_resolutions"][str(source)]["effects"]["font"]["candidates"] == []
    manifest = json.loads((result.out_dir / "set_aside.json").read_text(encoding="utf-8"))
    assert all(b["effects"]["font"] == compact for b in manifest["goal_bindings"].values())
    checkpoint = (result.out_dir / "recovery.json").read_text(encoding="utf-8")
    assert "Use the specified assemblies" not in checkpoint        # no paragraph text travels with the bindings
    rows = review_display.recovery_targets(result)
    assert all("every paragraph in scope (3 passage(s))" in row["details"] for row in rows)


def test_the_baseline_trials_the_files_that_share_a_method_in_one_run(tmp_path, monkeypatch):
    sources = [write_document(tmp_path / f"plain-{i}.docx") for i in range(3)] + \
              [write_document(tmp_path / "table.docx", layout="table")]
    runs = []
    original = recovery.run_chain

    def observed(inputs, chain, out_dir, workers=0, **kwargs):
        runs.append((len(inputs), workers))
        return original(inputs, chain, out_dir, workers=workers, **kwargs)
    monkeypatch.setattr(recovery, "run_chain", observed)
    from test_recovery_adaptive import contract as title_contract
    result = recovery.prepare_recovery(sources, "Make the section title bold", tmp_path / "session",
                                       adaptive_contract=title_contract(), options=opts())
    assert len(result.ready) == 4
    # One trial for the three plain documents (one method), one for the table layout, each with the batch's workers.
    assert sorted(runs) == [(1, 0), (3, 0)]
    assert sorted(len(a["results"]) for a in result.attempts) == [1, 3]
    assert all(a["reason"] == "Deterministic approved-goal baseline" for a in result.attempts)


def test_the_critic_sees_the_parts_a_global_effects_scope_names(tmp_path):
    """Codex on #123: bound once by count, a global effect names no paragraph, so the critic's evidence took its
    parts from the paragraphs no longer listed: a header-scoped font change showed the critic the body alone. The
    effect's approved scope names the parts now."""
    import hashlib
    from make_fixtures import build
    from spec_surgeon import recovery_adaptive
    from spec_surgeon.package import DocxPackage
    source = tmp_path / "spec.docx"
    source.write_bytes(build("21 13 13", "SPRINKLER SYSTEMS"))
    headers = DocxPackage(source.read_bytes()).parts_by_kind()["headers"]
    assert headers
    result = recovery.RecoveryResult(out_dir=tmp_path / "session", request="Headers in Arial")
    file = recovery.RecoveryFile(source=source, output=source, output_md5=hashlib.md5(source.read_bytes()).hexdigest())
    result.files = [file]
    recovery_adaptive.initialize(result, contract(
        {"id": "hf", "type": "format", "target": {"scope": ["headers"], "all": True}, "font": {"name": "Arial"}}))
    assert result.key["adaptive_bindings"][str(source)]["effects"]["hf"]["targets"] == "all"
    facts = recovery_adaptive._document_facts(file, result)
    for key in ("source_xml_evidence", "candidate_xml_evidence"):
        shown = [row["part"] for row in facts[key]]
        assert set(headers) <= set(shown) and all(row["source_target_positions"] == [] for row in facts[key])
    result.key["adaptive_contract"]["effects"][0]["target"] = {"all": True}      # the body alone: the main part only
    facts = recovery_adaptive._document_facts(file, result)
    assert [row["part"] for row in facts["source_xml_evidence"]] == [DocxPackage(source.read_bytes()).main_part()]
