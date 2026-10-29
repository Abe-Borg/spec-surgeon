"""A preview sample that fails its checks while another passes is set aside, as a refused file is: it waits under Left
untouched with its failed checks, the preview of the others stands, and Create makes their copies (the owner's
Safe-mode run of 2026-10-28: one sample of twelve failed V7 and Create was unavailable for every document). Only a
preview in which no sample got through holds Create back (Safe) or goes on to outcome planning (Default)."""
import dataclasses
from pathlib import Path

import pytest

from fake_claude import FakeClient, resp, tool
from make_fixtures import build
from spec_surgeon import preview, recipes, verify
from spec_surgeon.document_task import DocumentTask
from spec_surgeon.gui import Session
from spec_surgeon.preview import SampleView, failed_samples, sample_failed
from spec_surgeon.settings import Settings
from spec_surgeon.ui_feedback import PREVIEW_FAILED
from spec_surgeon.verify import CheckResult
from spec_surgeon.web.server import WindowState

REMOVE_HIGHLIGHT = {"request": "Remove the highlighting", "summary": "Remove highlight.", "mode": "formatting",
                    "ops": [{"op": "remove_highlight", "select": {"scope": ["document"]}}]}


def documents(tmp_path, *names):
    """One specification per name, each its own section; ``bad.docx`` is the one whose sample the test fails."""
    out = []
    for i, name in enumerate(names):
        path = tmp_path / name
        path.write_bytes(build(f"21 13 {13 + i}", f"SPRINKLER SYSTEMS {i}"))
        out.append(path)
    return out


def fail_sample(monkeypatch, *names):
    """V2 fails on the named documents' copies, wherever they are checked; the others pass as they are."""
    real = verify.run_suite

    def failing(*args, **kwargs):
        record = real(*args, **kwargs)
        if Path(args[3]).name in names:
            record.set(CheckResult("V2", "Text preservation", "FAIL", "planted verification failure"))
        return record

    monkeypatch.setattr(verify, "run_suite", failing)


def said(task):
    return [m["text"] for m in task.messages if m["role"] == "app"]


def safe_task(tmp_path, files):
    task = DocumentTask(Session(out_root=tmp_path / "runs", visual_check=False), render=False, mode="safe")
    task.attach(files)
    return task


def test_in_safe_mode_one_failed_sample_is_left_untouched_and_create_makes_the_others(tmp_path, monkeypatch):
    good, other, bad = documents(tmp_path, "good.docx", "other.docx", "bad.docx")
    before = bad.read_bytes()
    fail_sample(monkeypatch, "bad.docx")
    task = safe_task(tmp_path, [good, other, bad])
    task.perform("recipe", recipe="remove_highlight", inputs={})
    view = task.view()
    assert view["stage"] == "preview" and view["blocked_reason"] == ""
    assert [Path(sv.source).name for sv in task.session.failed_samples()] == ["bad.docx"]
    # its card stays, so the user sees why; it is listed under Left untouched with its failed check
    assert [Path(sv.source).name for sv in view["samples"]] == ["good.docx", "other.docx", "bad.docx"]
    assert view["samples_set_aside"] == [str(bad)]
    [item] = view["set_aside"]
    assert item["name"] == "bad.docx" and item["reason"].startswith(PREVIEW_FAILED) and "(V2)" in item["reason"]
    # the chat says it before the button, in the app's words
    assert any(t.startswith("bad.docx (V2) did not pass its preview checks, so no copy of it will be made")
               for t in said(task))
    # the page: Create is available, and the card knows it is set aside
    page = WindowState(task, Settings(), token="test-token").view()
    assert page["action"]["kind"] == "approve" and page["action"]["disabled_reason"] == ""
    assert page["action"]["label"] == "Create edited copies"
    assert {s["name"]: s["set_aside"] for s in page["samples"]} == {"good.docx": False, "other.docx": False,
                                                                     "bad.docx": True}
    task.perform("primary", token=view["token"])
    assert task.stage == "done"
    assert sorted(Path(k).name for k in task.results) == ["good.docx", "other.docx"]
    # never run: nothing of it is made again, nothing goes to recovery (Safe mode has none)
    assert sorted(f.source.name for f in task.session.last_batch.files) == ["good.docx", "other.docx"]
    [left] = task.left_untouched
    assert left["name"] == "bad.docx" and left["why"] == "failed" and left["reason"] == item["reason"]
    assert task.todo == {} and bad.read_bytes() == before
    # handed to Default mode, it waits on the to-do list with its reason as it reads
    task.hand_off([left["path"]])
    entry = task.todo[left["path"]]
    assert entry["why"] == "failed" and entry["reason"] == item["reason"]


def test_a_file_of_the_failed_samples_layout_that_was_not_sampled_still_runs_and_is_checked(tmp_path, monkeypatch):
    # The failed sample stands for itself only: every other document still runs and is checked on its own (a copy
    # that fails its check is never delivered either way), so nothing is set aside on a guess.
    names = ["bad.docx"] + [f"doc{i}.docx" for i in range(9)]
    files = documents(tmp_path, *names)
    fail_sample(monkeypatch, "bad.docx")
    task = safe_task(tmp_path, files)
    task.perform("recipe", recipe="remove_highlight", inputs={})
    sampled = [Path(sv.source).name for sv in task.session.preview.samples]
    assert "bad.docx" in sampled and len(sampled) < len(files)
    unsampled = sorted(set(names) - set(sampled))
    task.perform("primary", token=task.action_token())
    assert task.stage == "done"
    ran = {f.source.name for f in task.session.last_batch.files}
    assert "bad.docx" not in ran and set(unsampled) <= ran
    assert sorted(Path(k).name for k in task.results) == sorted(n for n in names if n != "bad.docx")


def test_when_no_sample_passes_create_stays_unavailable_and_nothing_is_set_aside(tmp_path, monkeypatch):
    one, two = documents(tmp_path, "one.docx", "two.docx")
    fail_sample(monkeypatch, "one.docx", "two.docx")
    task = safe_task(tmp_path, [one, two])
    task.perform("recipe", recipe="remove_highlight", inputs={})
    view = task.view()
    assert task.session.failed_samples() == [] and view["set_aside"] == []
    assert "did not pass its preview checks (V2)" in view["blocked_reason"]
    assert not any(t.endswith("the other documents' copies can be created.") for t in said(task))
    with pytest.raises(ValueError, match="V2"):
        task._primary(view["token"], lambda _: None)
    assert task.results == {} and task.todo == {}


def test_the_red_x_on_every_passing_preview_beside_a_failed_one_creates_nothing(tmp_path, monkeypatch):
    good, bad = documents(tmp_path, "good.docx", "bad.docx")
    fail_sample(monkeypatch, "bad.docx")
    task = safe_task(tmp_path, [good, bad])
    task.perform("recipe", recipe="remove_highlight", inputs={})
    task.perform("primary", token=task.action_token(), hold=[str(good)])
    assert task.results == {} and task.session.last_batch is None
    assert list(task.todo) == [str(good)]


def test_a_button_that_leaves_a_document_out_keeps_create_beside_a_failed_sample(tmp_path, monkeypatch):
    # The documents a button leaves out are not in its plan: counted beside a failed sample, they used to read as
    # "no document can take this change" and took the button away while one document could still get its copy.
    good, bad, out = documents(tmp_path, "good.docx", "bad.docx", "out.docx")
    fail_sample(monkeypatch, "bad.docx")
    real = recipes.build

    def leaving_out(*args, **kwargs):
        built = real(*args, **kwargs)
        return dataclasses.replace(built, left_out={str(out.resolve()): "Its body size differs (planted)."})

    monkeypatch.setattr(recipes, "build", leaving_out)
    task = safe_task(tmp_path, [good, bad, out])
    task.perform("recipe", recipe="remove_highlight", inputs={})
    view = task.view()
    assert [Path(f.source).name for f in task.session.preview.batch_plan.files] == ["good.docx", "bad.docx"]
    assert sorted(item["name"] for item in view["set_aside"]) == ["bad.docx", "out.docx"]
    assert view["action"] == "Create edited copies" and view["blocked_reason"] == ""
    task.perform("primary", token=view["token"])
    assert [Path(k).name for k in task.results] == ["good.docx"]
    assert sorted(e["name"] for e in task.left_untouched) == ["bad.docx", "out.docx"]


def test_in_default_mode_a_failed_sample_waits_on_the_to_do_list_and_no_outcome_planning_starts(tmp_path,
                                                                                                monkeypatch):
    good, bad = documents(tmp_path, "good.docx", "bad.docx")
    before = bad.read_bytes()
    fail_sample(monkeypatch, "bad.docx")
    client = FakeClient(resp(tool("submit_editspec", {"spec": REMOVE_HIGHLIGHT})))
    task = DocumentTask(Session(client=client, out_root=tmp_path / "out", visual_check=False), render=False)
    task.attach([good, bad])
    view = task.perform("send", text="Remove the highlighting")
    assert view["stage"] == "preview" and view["blocked_reason"] == ""
    assert not task.session.conversation.recovery_active and len(client.requests) == 1
    assert [item["name"] for item in view["set_aside"]] == ["bad.docx"]
    # Claude hears it first, to name it to the user; the chat line says it in the app's words
    note = task.session.conversation.notes[-1]
    assert note.index("LEFT UNTOUCHED (these samples did not pass") < note.index("PREVIEW —")
    assert "bad.docx: V2 " in note
    assert any("bad.docx (V2) did not pass its preview checks" in t for t in said(task))
    task.perform("primary", token=view["token"])
    assert task.stage == "done" and list(task.results) == [str(good)]
    assert task.session.recovery is None and len(client.requests) == 1     # never run, so never recovered
    assert [f.source.name for f in task.session.last_batch.files] == ["good.docx"]
    entry = task.todo[str(bad)]
    assert entry["why"] == "failed" and entry["reason"].startswith(PREVIEW_FAILED) and "(V2)" in entry["reason"]
    assert bad.read_bytes() == before


def test_in_default_mode_a_preview_whose_every_sample_failed_still_investigates_the_outcome(tmp_path, monkeypatch):
    one, two = documents(tmp_path, "one.docx", "two.docx")
    fail_sample(monkeypatch, "one.docx", "two.docx")
    proposal = resp(tool("propose_plan", {"contract": {"version": 1, "request": "Remove the highlighting",
                                                       "documents": [str(one), str(two)], "outcomes": []},
                                          "rationale": "planted"}))
    s = Session(documents=[one, two], out_root=tmp_path / "out", visual_check=False,
                client=FakeClient(resp(tool("submit_editspec", {"spec": REMOVE_HIGHLIGHT})), proposal))
    assert s.compile("Remove the highlighting", lambda _: None) == "Compiled."
    s.make_preview(False, lambda _: None)
    assert s.spec is None and s.preview is None and s.conversation.recovery_active


def sample(name, passed, exception=None):
    return SampleView(source=Path(name), passed=passed, exception=exception)


REFUSED = {"code": "not-found", "reason": "planted", "kind": "refusal"}
CRASHED = {"code": "internal-error", "reason": "planted", "kind": "internal-error"}


def test_what_counts_as_a_failed_sample_in_each_mode():
    made = sample("made.docx", False)                         # a copy whose checks failed
    crashed = sample("crashed.docx", None, CRASHED)           # no copy: the engine failed on it
    refused = sample("refused.docx", None, REFUSED)
    assert sample_failed(made) and sample_failed(crashed) and not sample_failed(refused)
    assert not sample_failed(sample("ok.docx", True))
    # Obliterate: a copy that was made is delivered marked UNVERIFIED, so only the engine's failure counts
    assert not sample_failed(made, unguarded=True) and sample_failed(crashed, unguarded=True)


@pytest.mark.parametrize("samples, unguarded, expected", [
    (["ok", "made"], False, ["made"]),
    (["ok", "made", "crashed", "refused"], False, ["made", "crashed"]),
    (["ok", "made"], True, []),                               # delivered UNVERIFIED, not set aside
    (["ok", "crashed"], True, ["crashed"]),
    (["made", "crashed"], False, []),                         # nothing got through: the preview cannot stand
    (["refused", "made"], False, []),
    (["made"], True, []),
    (["made", "crashed"], True, ["crashed"]),                 # the made copy gets through in Obliterate
])
def test_a_failed_sample_is_set_aside_only_while_another_got_through(samples, unguarded, expected):
    kinds = {"ok": (True, None), "made": (False, None), "crashed": (None, CRASHED), "refused": (None, REFUSED)}
    pv = preview.Preview(out_dir=Path("."), batch_plan=None,
                         samples=[sample(f"{k}.docx", *kinds[k]) for k in samples])
    assert [Path(sv.source).stem for sv in failed_samples(pv, unguarded)] == expected


def test_a_custom_methods_preview_sets_nothing_aside():
    # a sample its method fails on makes Create unavailable for the method (the D3 decision): no partial delivery
    s = Session()
    s.generated, s.spec = object(), None
    assert s.failed_samples() == []
