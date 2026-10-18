"""Generated methods retain the original source and approved outcome through delivery and retries."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from fake_claude import FakeClient, resp, tool
from make_fixtures import build, build_template
from spec_surgeon import codegen, guard
from spec_surgeon.goals import choose_goal, resolve_goal
from spec_surgeon.package import DocxPackage, W
from test_codegen import GOOD_OP
from test_governed_codegen import CONTENT_OP, contract


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source.docx"
    path.write_bytes(build("21 00 00", "FIRE SUPPRESSION"))
    return path


def header_method(source, twips=900):
    goal = {"kind": "document_effects", "effects": [
        {"id": "header", "type": "page_setup", "margins": {"header": 900}}]}
    return codegen.GeneratedOp(source=GOOD_OP, name="set_header_distance", passed=True,
        request="Set header distance to 900 twips", params={"twips": twips}, goal_contract=goal,
        goal_bindings={str(source.resolve()): resolve_goal(source, goal)["binding"]})


def test_reused_generated_delivery_folder_cannot_retain_an_earlier_success(source, tmp_path):
    folder = tmp_path / "run"
    assert codegen.run_generated(header_method(source), [str(source)], folder)[0].passed
    assert (folder / "outputs" / source.name).exists()
    failed = codegen.run_generated(header_method(source, 800), [str(source)], folder)[0]
    assert not failed.passed and not list((folder / "outputs").glob("*.docx"))
    saved = json.loads((folder / "set_aside.json").read_text())
    companion = json.loads((folder / "generated.json").read_text())
    assert saved["files"][0]["source"] == str(source.resolve())
    assert saved["goal_contract"] == companion["goal_contract"] == header_method(source).goal_contract
    assert saved["goal_bindings"] == companion["goal_bindings"] == header_method(source).goal_bindings
    from spec_surgeon import recovery
    corrected = {"request": saved["request"], "summary": "Correct header distance", "mode": "formatting", "ops": [
        {"op": "set_page_setup", "params": {"margins": {"header": 900}}}]}
    retried = recovery.recover_from_run(folder, tmp_path / "retry",
        client=FakeClient(resp(tool("submit_repair", {"candidate": {"kind": "spec", "spec": corrected}}))),
        options=recovery.RecoveryOptions(render=False, use_memory=False, critic_review=False, max_attempts=1))
    assert retried.ready
    assert retried.key["adaptive_contract"] == saved["goal_contract"]
    assert retried.key["adaptive_bindings"] == saved["goal_bindings"]


def test_original_save_after_real_trial_and_goal_check_withholds_generated_copy(source, tmp_path, monkeypatch):
    actual = codegen.trial
    changed = build("21 00 00", "LATE USER ADDITION")
    def trial_then_user_save(*args, **kwargs):
        result = actual(*args, **kwargs)
        assert result.passed and result.record.get("GOAL").status == "PASS"
        source.write_bytes(changed)
        return result
    monkeypatch.setattr(codegen, "trial", trial_then_user_save)
    folder = tmp_path / "run"
    result = codegen.run_generated(header_method(source), [str(source)], folder)[0]
    assert not result.passed and result.output is None
    assert result.record.get("V9").status == "FAIL"
    assert not (folder / "outputs" / source.name).exists()
    assert source.read_bytes() == changed


def test_sandbox_and_both_verifiers_use_the_one_acquired_original(source, tmp_path, monkeypatch):
    from spec_surgeon import goals, verify
    original = source.read_bytes()
    actual_sandbox, actual_suite, actual_goal = codegen.run_in_sandbox, verify.run_suite, goals.verify_goal
    def save_before_sandbox(*args, **kwargs):
        assert kwargs["source_bytes"] == original
        source.write_bytes(build("21 00 00", "NEW UNAPPROVED SOURCE"))
        return actual_sandbox(*args, **kwargs)
    def suite(baseline, *args, **kwargs):
        assert baseline == original
        return actual_suite(baseline, *args, **kwargs)
    def goal(baseline, *args, **kwargs):
        assert baseline == original
        return actual_goal(baseline, *args, **kwargs)
    method = header_method(source)
    monkeypatch.setattr(codegen, "run_in_sandbox", save_before_sandbox)
    monkeypatch.setattr(verify, "run_suite", suite)
    monkeypatch.setattr(goals, "verify_goal", goal)
    result = codegen.trial(method.source, source, {}, method.params, tmp_path / "trial",
                           goal_contract=method.goal_contract, goal_binding=method.goal_bindings[str(source)])
    assert not result.passed
    assert result.record.get("GOAL").status == "PASS" and result.record.get("V9").status == "FAIL"


def test_save_between_identity_acquisition_and_read_prevents_sandbox_execution(source, tmp_path, monkeypatch):
    acquire = guard.acquire
    def acquire_then_save(path):
        identity = acquire(path)
        source.write_bytes(build("21 00 00", "NEW SOURCE"))
        return identity
    monkeypatch.setattr(guard, "acquire", acquire_then_save)
    monkeypatch.setattr(codegen, "run_in_sandbox", lambda *args, **kwargs: pytest.fail("changed bytes reached sandbox"))
    result = codegen.trial(GOOD_OP, source, {}, {"twips": 900}, tmp_path / "trial")
    assert not result.passed and "source changed" in result.problems[0]


def test_save_during_copy_fsync_withholds_final_name_and_removes_temporary(source, tmp_path, monkeypatch):
    identity = guard.acquire(source)
    folder = tmp_path / "outputs"
    folder.mkdir()
    sync = codegen.os.fsync
    def sync_then_user_save(descriptor):
        sync(descriptor)
        source.write_bytes(build("21 00 00", "SAVED DURING COPY"))
    monkeypatch.setattr(codegen.os, "fsync", sync_then_user_save)
    with pytest.raises(guard.GuardError, match="source changed"):
        codegen._publish_generated(folder / source.name, b"verified candidate bytes", identity)
    assert not list(folder.iterdir())


def test_generated_goal_template_is_checked_before_old_outputs_are_removed(source, tmp_path):
    folder = tmp_path / "run"
    template = folder / "outputs" / "template.docx"
    template.parent.mkdir(parents=True)
    template.write_bytes(build_template())
    original = template.read_bytes()
    goal = {"kind": "document_effects", "effects": [{"id": "branding", "type": "header_footer_template",
        "template": str(template), "template_sha256": hashlib.sha256(original).hexdigest(),
        "params": {"template_sections": {"all": 0}, "slot_fills": {}}}]}
    method = codegen.GeneratedOp(source=GOOD_OP, goal_contract=goal)
    with pytest.raises(ValueError, match="inside the results folder"):
        codegen.run_generated(method, [str(source)], folder)
    assert template.read_bytes() == original and not (folder / "logs").exists()


def test_relative_sample_and_run_paths_preserve_the_approved_ambiguous_choice(source, tmp_path, monkeypatch):
    from spec_surgeon.ooxml import block_full_text, paragraphs
    package = DocxPackage.from_path(source)
    root = package.xml(package.main_part())
    paragraph = next(item for item in paragraphs(root) if block_full_text(item) == "FIRE SUPPRESSION")
    paragraph.getparent().insert(paragraph.getparent().index(paragraph) + 1, copy.deepcopy(paragraph))
    package.write_xml(package.main_part(), root)
    source.write_bytes(package.output_bytes())
    goal = contract()
    resolved = resolve_goal(source, goal)
    selected = resolved["candidates"][0]["candidate_id"]
    binding = choose_goal(source, goal, [selected], effect_id="wording")
    first_only = CONTENT_OP.replace('data[str(i)] = edits_to_data(edits)',
                                    'data[str(i)] = edits_to_data(edits)\n                break')
    client = FakeClient(resp(tool("submit_op", {"source": first_only, "params": {"replacement": "WATER"}})))
    monkeypatch.chdir(tmp_path)
    method = codegen.generate_op("change FIRE to WATER untracked", "custom wording method", [Path(source.name)],
        tmp_path / "writer", client=client, goal_contract=goal, goal_bindings={str(source): binding})
    assert method.passed and method.goal_bindings[str(source)] == binding
    result = codegen.run_generated(method, [source.name], tmp_path / "delivery")[0]
    assert result.passed, result.problems
    saved = json.loads((tmp_path / "delivery" / "set_aside.json").read_text())
    assert saved["goal_bindings"][str(source)] == binding


def test_later_queued_source_save_cannot_become_a_new_generated_run_baseline(tmp_path):
    first, second = (tmp_path / name for name in ("first.docx", "second.docx"))
    for path in (first, second):
        path.write_bytes(build("21 00 00", "FIRE SUPPRESSION"))
    changed = build("21 00 00", "NEW USER CONTENT")
    method = codegen.GeneratedOp(source=GOOD_OP, name="set_header_distance", passed=True,
                                params={"twips": 900})

    def save_queued_source(message):
        if message == "PASS first.docx":
            second.write_bytes(changed)

    folder = tmp_path / "run"
    results = codegen.run_generated(method, [str(first), str(second)], folder, log=save_queued_source)
    assert results[0].passed and not results[1].passed
    assert (folder / "outputs" / first.name).exists()
    assert not (folder / "outputs" / second.name).exists()
    assert "source changed" in "; ".join(results[1].problems)
    assert second.read_bytes() == changed


def test_generated_run_rejects_a_save_after_preview_handoff_before_any_writes(source, tmp_path):
    from spec_surgeon.preview import approval_inputs, generated_key
    method = codegen.GeneratedOp(source=GOOD_OP, passed=True, params={"twips": 900})
    approved = approval_inputs(generated_key(method), [source])
    changed = build("21 00 00", "SAVED AFTER PREVIEW")
    source.write_bytes(changed)
    folder = tmp_path / "run"
    with pytest.raises(ValueError, match="changed|approval|approved"):
        codegen.run_generated(method, [str(source)], folder, approved_inputs=approved)
    assert source.read_bytes() == changed and not folder.exists()


def test_generated_run_accepts_exact_preview_authority_and_source_pins(source, tmp_path):
    from spec_surgeon.preview import approval_inputs, generated_key
    method = header_method(source)
    approved = approval_inputs(generated_key(method), [source])
    result = codegen.run_generated(method, [str(source)], tmp_path / "run", approved_inputs=approved)[0]
    assert result.passed, result.problems


def test_generated_recipe_change_after_approval_is_rejected_before_any_writes(source, tmp_path):
    from spec_surgeon.preview import approval_inputs, generated_key
    method = codegen.GeneratedOp(source=GOOD_OP, passed=True, params={"twips": 900})
    approved = approval_inputs(generated_key(method), [source])
    method.params["twips"] = 800
    folder = tmp_path / "run"
    with pytest.raises(ValueError, match="transform changed since approval"):
        codegen.run_generated(method, [str(source)], folder, approved_inputs=approved)
    assert not folder.exists()


@pytest.mark.parametrize("pin", [None, [1, 2], [True, 2, "0" * 32], [1, 2, "not-an-md5"]])
def test_generated_approval_cannot_use_malformed_source_pins(source, tmp_path, pin):
    from spec_surgeon.preview import approval_inputs, generated_key
    method = codegen.GeneratedOp(source=GOOD_OP, passed=True, params={"twips": 900})
    approved = approval_inputs(generated_key(method), [source])
    approved["files"][str(source.resolve())] = pin
    folder = tmp_path / "run"
    with pytest.raises(ValueError, match="pins are malformed"):
        codegen.run_generated(method, [str(source)], folder, approved_inputs=approved)
    assert not folder.exists()


def test_trial_cannot_replace_the_generated_runs_pinned_identity(source, tmp_path, monkeypatch):
    actual = codegen.trial
    changed = build("21 00 00", "SAVED BEFORE TRIAL ACQUISITION")
    method = codegen.GeneratedOp(source=GOOD_OP, passed=True, params={"twips": 900})

    def save_before_trial(*args, **kwargs):
        source.write_bytes(changed)
        result = actual(*args, **kwargs)
        assert result.passed
        return result

    monkeypatch.setattr(codegen, "trial", save_before_trial)
    folder = tmp_path / "run"
    result = codegen.run_generated(method, [str(source)], folder)[0]
    assert not result.passed and result.output is None
    assert result.record.get("V9").status == "FAIL"
    assert "between generated run acquisition and trial" in "; ".join(result.problems)
    assert not (folder / "outputs" / source.name).exists() and source.read_bytes() == changed
