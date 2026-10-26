"""Confirmed v1.11.0 UI findings: preview approval, output names and stale results."""
from pathlib import Path

import pytest

from make_fixtures import build, build_dirty
from spec_surgeon import batch, preview, verify
from spec_surgeon.chain import chain_from_specs, run_chain
from spec_surgeon.document_task import DocumentTask
from spec_surgeon.gui import Session
from spec_surgeon.settings import Settings
from spec_surgeon.verify import CheckResult
from spec_surgeon.web.server import WindowState
from test_naming import CONVENTION, build_batch1


def task_for(tmp_path):
    source = tmp_path / "original.docx"
    source.write_bytes(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    task = DocumentTask(Session(out_root=tmp_path / "runs", visual_check=False), render=False, mode="safe")
    task.attach([source])
    return task, source


def fail_checks(monkeypatch, module):
    real = module.run_suite

    def failing(*args, **kwargs):
        record = real(*args, **kwargs)
        record.set(CheckResult("V2", "Text preservation", "FAIL", "planted verification failure"))
        return record

    monkeypatch.setattr(module, "run_suite", failing)


@pytest.mark.parametrize("mode", ["safe", "default"])
def test_failed_preview_blocks_create_in_the_page_and_session(tmp_path, monkeypatch, mode):
    task, source = task_for(tmp_path)
    fail_checks(monkeypatch, verify)
    task.perform("recipe", recipe="remove_highlight", inputs={})
    # Keep the preview while exercising both guarded modes without starting a conversation.
    task.mode = mode
    view = task.view()
    assert any(s.passed is False for s in view["samples"])
    reason = view["blocked_reason"]
    assert source.name in reason and "V2" in reason
    state = WindowState(task, Settings(), token="test-token")
    assert state.view()["action"]["disabled_reason"] == reason
    with pytest.raises(ValueError, match="V2"):
        task.session.run(False, False, lambda _: None)
    with pytest.raises(ValueError, match="V2"):
        task._primary(view["token"], lambda _: None, hold=[str(source)])
    assert task.todo == {} and task.results == {}
    assert not list((tmp_path / "runs").rglob("run/outputs/*.docx"))


def test_obliterate_still_allows_failed_checks_in_preview(tmp_path, monkeypatch):
    task, _ = task_for(tmp_path)
    fail_checks(monkeypatch, verify)
    task.perform("recipe", recipe="remove_highlight", inputs={})
    task.mode = "obliterate"
    task.session.unguarded = True
    assert task.session.run_blocked_reason() == ""
    assert task.view()["blocked_reason"] == ""


@pytest.mark.parametrize("chain", [False, True])
def test_named_preview_and_delivery_both_pass_filename_verification(tmp_path, chain):
    source = tmp_path / "original.docx"
    source.write_bytes(build_batch1())
    spec = {"request": "name files", "summary": "Name files.", "mode": "formatting",
            "ops": [{"op": "name_output", "params": CONVENTION}]}
    if chain:
        unchanged = {"request": "remove highlight", "summary": "Remove highlight.", "mode": "formatting",
                     "ops": [{"op": "remove_highlight", "select": {"scope": ["document"]}}]}
        spec = chain_from_specs([("Name files", spec), ("Remove highlight", unchanged)])
    make = preview.make_chain_preview if chain else preview.make_preview
    pv = make(spec, [str(source)], tmp_path / "preview", do_render=False)
    assert pv.samples and all(s.passed for s in pv.samples), pv.to_text()
    assert pv.samples[0].output.name == "211319 - Preaction Sprinkler Systems.docx"
    if not chain:
        assert pv.samples[0].output.read_bytes() == source.read_bytes()
    if chain:
        result = run_chain([str(source)], spec, tmp_path / "run", do_render=False, workers=1)
    else:
        result = batch.run_batch([str(source)], "", {}, tmp_path / "run", spec=spec, do_render=False, workers=1)
    assert all(f.status == "PASS" for f in result.files)
    assert Path(result.files[0].delivered_to).name == pv.samples[0].output.name


def test_naming_preview_still_catches_a_wrong_name_from_the_transform(tmp_path, monkeypatch):
    from spec_surgeon.ops import naming
    real = naming.transform
    monkeypatch.setattr(naming, "transform", lambda value, how: value.lower() if how == "title" else real(value, how))
    source = tmp_path / "original.docx"
    source.write_bytes(build_batch1())
    spec = {"request": "name files", "summary": "Name files.", "mode": "formatting",
            "ops": [{"op": "name_output", "params": CONVENTION}]}
    pv = preview.make_preview(spec, [str(source)], tmp_path / "preview", do_render=False)
    assert pv.samples[0].passed is False and pv.samples[0].checks["V10"] == "FAIL"


def test_a_failed_later_button_does_not_claim_previous_copies_are_its_results(tmp_path, monkeypatch):
    task, source = task_for(tmp_path)
    view = task.perform("recipe", recipe="remove_headers_footers", inputs={})
    task.perform("primary", token=view["token"])
    previous = task.results[str(source)]
    task.session.out_root = tmp_path / "later-runs"  # keep fast successive runs in separate folders
    view = task.perform("recipe", recipe="remove_highlight", inputs={})
    assert task.results == {} and task.view()["results"] == {}
    fail_checks(monkeypatch, batch)  # preview passed; the actual run fails independently
    view = task.perform("primary", token=view["token"])
    assert view["left_untouched"] and view["results"] == {}
    assert "No copies were created" in task.messages[-1]["text"]
    assert "copies are ready" not in task.messages[-1]["text"]
    assert task.working[str(source)] == previous and Path(previous).is_file()


def test_a_refused_later_button_does_not_show_earlier_copies(tmp_path):
    task, source = task_for(tmp_path)
    view = task.perform("recipe", recipe="remove_headers_footers", inputs={})
    task.perform("primary", token=view["token"])
    previous = task.results[str(source)]
    task.session.out_root = tmp_path / "later-runs"
    view = task.perform("recipe", recipe="watermark", inputs={"text": "DRAFT"})
    assert view["results"] == {} and view["action"] == ""
    assert view["set_aside"] and "header" in view["set_aside"][0]["reason"]
    with pytest.raises(ValueError, match="No documents"):
        task.session.run(False, False, lambda _: None)
    assert task.working[str(source)] == previous and Path(previous).is_file()


def test_start_over_keeps_the_originals_and_clears_the_last_buttons_untouched_state(tmp_path):
    task, source = task_for(tmp_path)
    empty = tmp_path / "empty.docx"
    empty.write_bytes(build_dirty("empty_body"))
    task.attach([empty])
    view = task.perform("recipe", recipe="title_property", inputs={"pattern": "SECTION {number} - {title}"})
    task.perform("primary", token=view["token"])
    assert task.left_untouched and task.results
    saved_copy = task.results[str(source)]
    task.start_over()
    view = task.view()
    assert view["stage"] == "empty" and view["results"] == {} and view["left_untouched"] == []
    assert view["working"]["from_originals"] is True
    assert set(task.documents()) == {source, empty}
    assert "Pick a button to start" in task.messages[-1]["text"]
    assert Path(saved_copy).is_file()
