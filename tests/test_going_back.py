"""Changing your mind: Discard withdraws a preview and Undo takes the working set back one run at a time — the copies
each document builds on, the history, what was left untouched and the documents the ✗ set aside. In Safe mode Discard
shows again what the window showed before the button; in Default and Obliterate it ends the request (the preview, or
Claude's open question, a reply to continue, a method being written, a pause), and the next message is a new request
that is told this one was withdrawn. Neither writes or deletes a file: the copies a run made stay where they were
saved, and the originals are never touched."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from make_fixtures import build, build_dirty
from spec_surgeon.document_task import DocumentTask
from spec_surgeon.gui import Session

TITLE = {"pattern": "SECTION {number} - {title}"}


@pytest.fixture
def files(tmp_path) -> list:
    out = []
    for num, title in (("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"), ("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS")):
        path = tmp_path / f"{num}.docx"
        path.write_bytes(build(num, title))
        out.append(path)
    return out


def safe_task(tmp_path, journal=None) -> DocumentTask:
    return DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=False, mode="safe",
                        journal=journal)


def press(task, recipe, inputs=None, hold=()):
    """A button through its preview and Create; the view after the run."""
    view = task.perform("recipe", recipe=recipe, inputs=inputs or {})
    assert view["stage"] == "preview", [m["text"] for m in task.messages]
    return task.perform("primary", token=view["token"], hold=[str(p) for p in hold])


def on_disk(tmp_path) -> dict:
    return {p: p.read_bytes() for p in sorted(tmp_path.rglob("*")) if p.is_file() and p.suffix == ".docx"}


# ---- Discard --------------------------------------------------------------------------------------------------------

def test_discard_withdraws_the_preview_and_shows_the_last_copies_again(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_highlight")
    copies, working = dict(task.results), dict(task.working)
    view = task.perform("recipe", recipe="title_property", inputs=TITLE)
    assert view["stage"] == "preview" and view["results"] == {}       # a button's preview shows no earlier copies
    before = on_disk(tmp_path)
    task.discard_preview()
    view = task.view()
    assert view["stage"] == "done" and view["results"] == copies and task.working == working
    assert task.session.spec is None and view["action"] == "" and view["samples"] == []
    assert task.decisions == [] and task.generation == 1
    assert on_disk(tmp_path) == before                                 # nothing created, nothing deleted
    assert task.messages[-1]["text"].startswith("Preview discarded; nothing was created.")
    with pytest.raises(ValueError, match="no preview to discard"):
        task.discard_preview()


def test_a_first_buttons_discard_returns_to_the_empty_window(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    task.perform("recipe", recipe="remove_highlight", inputs={})
    task.discard_preview()
    view = task.view()
    assert view["stage"] == "empty" and view["results"] == {} and task.working == {}
    assert view["working"]["undo"] == ""                               # nothing ran, so nothing to undo


def test_a_button_pressed_over_a_preview_discards_back_to_what_came_before_both(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_highlight")
    copies = dict(task.results)
    task.perform("recipe", recipe="remove_comments", inputs={})
    task.perform("recipe", recipe="title_property", inputs=TITLE)    # a second button over the first's preview
    task.discard_preview()
    assert task.results == copies


def test_discard_restores_what_the_last_button_left_untouched(tmp_path, files):
    empty = tmp_path / "empty.docx"
    empty.write_bytes(build_dirty("empty_body"))
    task = safe_task(tmp_path)
    task.attach([files[0], empty])
    press(task, "title_property", TITLE)
    left = task.view()["left_untouched"]
    assert [e["name"] for e in left] == ["empty.docx"]
    task.perform("recipe", recipe="remove_highlight", inputs={})
    assert task.view()["left_untouched"] == []                         # hidden while the preview waits
    task.discard_preview()
    assert task.view()["left_untouched"] == left


def test_discard_needs_something_to_withdraw(tmp_path, files):
    task = DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=False)
    task.attach(files)
    assert task.view()["discard"] == ""
    with pytest.raises(ValueError, match="nothing to discard"):
        task.discard_preview()
    task = safe_task(tmp_path)
    task.attach(files)
    with pytest.raises(ValueError, match="no preview to discard"):
        task.discard_preview()


# ---- Default and Obliterate: the conversation's ways back -----------------------------------------------------------

from fake_claude import FakeClient, resp, tool                                    # noqa: E402
from test_document_task import REQUEST, SPEC, document                           # noqa: E402

AGAIN = "Make the FIRE SUPPRESSION title italic instead."
ITALIC = {**SPEC, "request": AGAIN, "summary": "Make the title italic",
          "ops": [{**SPEC["ops"][0], "params": {"run": {"i": True}, "tracked": False}}]}


def chat_task(tmp_path, *replies, mode="default"):
    task = DocumentTask(Session(client=FakeClient(*replies), out_root=tmp_path / "out", visual_check=False),
                        render=False, obliterate_allowed=True)
    if mode == "obliterate":
        task.set_mode("obliterate", acknowledged=True)
    return task


def plan(spec=SPEC, tid="t1"):
    return resp(tool("submit_editspec", {"spec": spec}, tid))


@pytest.mark.parametrize("mode", ["default", "obliterate"])
def test_discard_withdraws_a_conversations_preview_and_the_next_message_is_a_new_request(tmp_path, mode):
    source = document(tmp_path / "Spec.docx")
    task = chat_task(tmp_path, plan(), plan(ITALIC, "t2"), plan(ITALIC, "t3"), mode=mode)
    task.attach([source])
    view = task.perform("send", text=REQUEST)
    task.perform("primary", token=view["token"])
    copies, working, history = dict(task.results), dict(task.working), list(task.history)
    view = task.perform("send", text="Make it italic instead.")
    assert view["stage"] == "preview" and view["discard"] == "preview"
    assert view["working"]["undo_blocked"] == "Discard the preview first."
    before = on_disk(tmp_path)
    task.discard_preview()
    view = task.view()
    assert view["stage"] == "done" and view["discard"] == "" and view["action"] == "" and view["samples"] == []
    assert task.results == copies and task.working == working and task.history == history and task.generation == 1
    assert task.session.spec is None and task.session.conversation is None and task.decisions == []
    assert on_disk(tmp_path) == before                                 # nothing created, nothing deleted
    assert task.messages[-1]["text"] == ("Preview discarded; nothing was created. Your next message starts a new "
                                         "request; the copies you have stay as they are.")
    assert task.exchanges[-1] == {"asked": ["Make it italic instead."], "answered": "", "discarded": "preview"}
    # the next message is a REQUEST of its own, told that the last one was withdrawn — not a revision of it
    view = task.perform("send", text=AGAIN)
    assert view["stage"] == "preview" and task.decisions == [AGAIN]
    sent = task.session.client.requests[-1]["messages"][0]["content"]
    assert sent.startswith(f"REQUEST:\n{AGAIN}\n\n")
    earlier = sent.split("EARLIER IN THIS CONVERSATION")[1].split("\n\n")[0]
    assert ("“Make it italic instead.” — a plan was previewed and the user discarded it before anything was created"
            in earlier)


@pytest.mark.parametrize("mode", ["default", "obliterate"])
def test_discard_drops_a_request_still_open_at_a_question(tmp_path, mode):
    task = chat_task(tmp_path, resp(tool("ask_user", {"questions": ["Which title should change?"]})),
                     plan(ITALIC, "t2"), mode=mode)
    task.attach([document(tmp_path / "Spec.docx")])
    view = task.perform("send", text=REQUEST)
    assert view["stage"] == "question" and view["discard"] == "request"
    task.discard_preview()
    view = task.view()
    assert view["stage"] == "empty" and view["discard"] == "" and view["questions"] == []
    assert task.decisions == [] and task.session.conversation is None
    assert task.messages[-1]["text"] == "Request dropped; nothing was created. Your next message starts a new request."
    view = task.perform("send", text=AGAIN)
    sent = task.session.client.requests[-1]["messages"][0]["content"]
    assert view["stage"] == "preview" and sent.startswith(f"REQUEST:\n{AGAIN}\n\n")
    assert "“" + REQUEST + "” — the user dropped the request before anything was created" in sent


def test_discard_drops_a_request_paused_at_the_spending_check(tmp_path, monkeypatch):
    from test_document_task import check_after
    task = chat_task(tmp_path, plan(ITALIC, "t2"))        # the paused request never reached Claude
    task.attach([document(tmp_path / "Spec.docx")])
    check_after(task, monkeypatch, 0)
    view = task.perform("send", text=REQUEST)
    assert view["stage"] == "budget" and view["discard"] == "request"
    task.discard_preview()
    assert task.stage == "empty" and task.pause_reason() == "" and task.decisions == []
    task.set_spending_limit(None)
    assert task.perform("send", text=AGAIN)["stage"] == "preview"


@pytest.mark.parametrize("mode", ["default", "obliterate"])
def test_undo_goes_back_before_a_conversations_run(tmp_path, mode):
    source = document(tmp_path / "Spec.docx")
    task = chat_task(tmp_path, plan(), plan(ITALIC, "t2"), mode=mode)
    task.attach([source])
    view = task.perform("send", text=REQUEST)
    task.perform("primary", token=view["token"])
    first = {"working": dict(task.working), "history": list(task.history)}
    view = task.perform("send", text=AGAIN)
    task.perform("primary", token=view["token"])
    assert task.generation == 2 and task.view()["working"]["undo"] == AGAIN
    task.undo()
    assert task.working == first["working"] and task.history == first["history"] and task.generation == 1
    assert task.messages[-1]["text"].startswith(f"Undid “{AGAIN.rstrip('.')}”. Your next request builds on the "
                                                "copies from before it.")
    task.undo()
    assert task.working == {} and task.generation == 0 and task.view()["working"]["undo"] == ""


def test_undo_waits_for_an_open_question_to_be_dropped(tmp_path):
    task = chat_task(tmp_path, plan(), resp(tool("ask_user", {"questions": ["Which title?"]}, "q2")))
    task.attach([document(tmp_path / "Spec.docx")])
    view = task.perform("send", text=REQUEST)
    task.perform("primary", token=view["token"])
    assert task.perform("send", text=AGAIN)["stage"] == "question"
    assert task.view()["working"]["undo_blocked"] == "Drop the request that is open first, or finish it."
    with pytest.raises(ValueError, match="Drop the request"):
        task.undo()
    task.discard_preview()
    task.undo()
    assert task.working == {}


def fail_once(monkeypatch, failing):
    """The next run's page comparison fails for ``failing``: the run delivers the others' copies and recovery takes it
    up, its Create saving the copy it fixed."""
    from spec_surgeon import batch
    from spec_surgeon.verify.suite import CheckResult
    original, done = batch._render_and_compare, []

    def compare(result, *args, **kwargs):
        original(result, *args, **kwargs)
        if not done:
            item = next(f for f in result.files if f.source == failing)
            item.record.set(CheckResult("V6", "Page rendering", "FAIL", "Renderer stopped unexpectedly"))
            done.append(item)
    monkeypatch.setattr(batch, "_render_and_compare", compare)


REPAIR = {"candidate": {"kind": "spec", "spec": SPEC}}                    # recovery's plan for the failed one


def recovering_task(tmp_path, monkeypatch, *replies, journal=None):
    from spec_surgeon.recovery import RecoveryOptions
    from spec_surgeon.saved_recipes import Store
    task = DocumentTask(Session(client=FakeClient(*replies), out_root=tmp_path / "out", visual_check=False),
                        render=False, journal=journal, recipes=Store(tmp_path / "config" / "recipes.json"))
    monkeypatch.setattr(task, "_recovery_options", lambda: RecoveryOptions(render=False, critic_review=False,
                                                                          visual_review=False))
    return task


@pytest.mark.parametrize("reopen", [False, True])
def test_a_run_recovery_finished_is_undone_whole_and_keeps_its_plan_for_a_recipe(tmp_path, monkeypatch, reopen):
    # A run whose documents recovery fixed after it is one change: its approval took the undo point and the steps a
    # recipe keeps, and recovery's Create — after a reopened window too — keeps them rather than taking its own (a
    # point that would show the run's first copies as what came before, and steps that say nothing was kept)
    journal = tmp_path / "conversation.json"
    docs = [document(tmp_path / "First.docx"), document(tmp_path / "Second.docx")]
    italic = {"candidate": {"kind": "spec", "spec": ITALIC}}
    task = recovering_task(tmp_path, monkeypatch, plan(), plan(ITALIC, "t2"), resp(tool("submit_repair", italic)),
                           journal=journal)
    task.attach(docs)
    task.perform("primary", token=task.perform("send", text=REQUEST)["token"])
    first = {"working": dict(task.working), "results": dict(task.results), "history": list(task.history)}
    fail_once(monkeypatch, Path(first["working"][str(docs[1])]))
    view = task.perform("primary", token=task.perform("send", text=AGAIN)["token"])
    assert task.session.recovery and view["stage"] == "preview" and len(task.undo_points) == 1
    if reopen:
        task = recovering_task(tmp_path, monkeypatch, journal=journal)
        assert task.restore() and task._pending_undo["results"] == first["results"]
        view = task.perform("primary", token=task.action_token())
        assert task.session.recovery and view["stage"] == "preview"
    task.perform("primary", token=view["token"])
    assert task.stage == "done" and task.generation == 2 and len(task.undo_points) == 2
    done = task._done_view()
    assert [row["request"] for row in done] == [REQUEST, AGAIN] and all(row["why_not"] == "" for row in done)
    record = task.save_recipe("Bold then italic", "", [row["id"] for row in done])
    assert [step["request"] for step in record["steps"]] == [REQUEST, AGAIN]
    assert record["steps"][1]["plan"]["ops"] == ITALIC["ops"]
    task.undo()
    assert task.working == first["working"] and task.history == first["history"] and task.generation == 1
    assert task.results == first["results"]                    # the copies before the run show again, not its own
    assert [row["request"] for row in task._done_view()] == [REQUEST]


def test_a_reopened_window_keeps_a_runs_point_only_while_its_recovery_waits(tmp_path, monkeypatch):
    journal = tmp_path / "conversation.json"
    docs = [document(tmp_path / "First.docx"), document(tmp_path / "Second.docx")]
    task = recovering_task(tmp_path, monkeypatch, plan(), resp(tool("submit_repair", REPAIR)), journal=journal)
    task.attach(docs)
    fail_once(monkeypatch, docs[1])
    task.perform("primary", token=task.perform("send", text=REQUEST)["token"])
    data = json.loads(journal.read_text(encoding="utf-8"))
    assert data["recovery"] and data["pending_undo"]["working"] == {} and data["pending_steps"]["steps"]
    no_recovery = {**data, "recovery": None, "pending_layouts": []}
    journal.write_text(json.dumps(no_recovery), encoding="utf-8")
    again = recovering_task(tmp_path, monkeypatch, journal=journal)
    assert again.restore() and again._pending_undo is None and again._pending_steps is None
    malformed = {**data, "pending_undo": {**data["pending_undo"], "working": ["not", "a", "map"]}}
    journal.write_text(json.dumps(malformed), encoding="utf-8")
    again = recovering_task(tmp_path, monkeypatch, journal=journal)
    assert again.restore() and again._pending_undo is None and again._pending_steps is None  # nothing restored wrongly
    lost_steps = {**data, "pending_steps": {"steps": "not a list"}}
    journal.write_text(json.dumps(lost_steps), encoding="utf-8")
    again = recovering_task(tmp_path, monkeypatch, journal=journal)
    assert again.restore() and again._pending_steps is None
    assert {k: again._pending_undo[k] for k in data["pending_undo"]} == data["pending_undo"]


def test_a_runs_point_ends_with_the_request_it_was_taken_for(tmp_path, monkeypatch):
    # attaching a document while recovery waits voids the plan, and the run's approval with it: no later request's
    # save of recovered copies may take that point or those steps for its own
    docs = [document(tmp_path / "First.docx"), document(tmp_path / "Second.docx")]
    task = recovering_task(tmp_path, monkeypatch, plan(), resp(tool("submit_repair", REPAIR)))
    task.attach(docs)
    fail_once(monkeypatch, docs[1])
    task.perform("primary", token=task.perform("send", text=REQUEST)["token"])
    assert task.session.recovery and task._pending_undo is not None and task._pending_steps is not None
    task.attach([document(tmp_path / "Third.docx")])
    assert task.session.recovery is None and task._pending_undo is None and task._pending_steps is None


def test_discard_during_a_pass_puts_its_documents_back_on_the_to_do_list(tmp_path):
    docs = [document(tmp_path / f"{n}.docx") for n in ("21 05 00", "21 13 13")]
    task = chat_task(tmp_path, plan(), plan(ITALIC, "t2"))
    task.attach(docs)
    view = task.perform("send", text=REQUEST)
    task.perform("primary", token=view["token"], hold=[str(docs[1])])
    entry = dict(task.todo[str(docs[1])])
    task.perform("send", text="Work on the documents I set aside")
    assert task.focus and task.view()["discard"] == "request"
    task.discard_preview()
    assert task.focus is None and task.todo == {str(docs[1]): entry} and task.decisions == []
    assert task.messages[-1]["text"].endswith("The documents you were working on are back on your to-do list.")


def test_a_withdrawn_request_and_a_pass_s_own_request_survive_a_restart(tmp_path):
    docs = [document(tmp_path / f"{n}.docx") for n in ("21 05 00", "21 13 13")]
    journal = tmp_path / "conversation.json"
    task = DocumentTask(Session(client=FakeClient(plan(), plan(ITALIC, "t2")), out_root=tmp_path / "out",
                                visual_check=False), render=False, journal=journal)
    task.attach(docs)
    view = task.perform("send", text=REQUEST)
    task.perform("primary", token=view["token"], hold=[str(docs[1])])
    task.perform("send", text=AGAIN)
    task.discard_preview()
    task.perform("send", text="Work on the documents I set aside")
    restored = DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=False, journal=journal)
    assert restored.restore()
    assert restored.exchanges == task.exchanges and restored.exchanges[-1]["discarded"] == "preview"
    assert restored.focus == task.focus and restored.focus["decisions"] == [REQUEST]
    data = json.loads(journal.read_text(encoding="utf-8"))
    data["exchanges"][-1]["discarded"] = "maybe"
    journal.write_text(json.dumps(data), encoding="utf-8")
    broken = DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=False, journal=journal)
    with pytest.raises(ValueError, match="could not be read"):
        broken.restore()


# ---- Undo -----------------------------------------------------------------------------------------------------------

def test_undo_goes_back_one_button_at_a_time_and_deletes_nothing(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    originals = {p: p.read_bytes() for p in files}
    press(task, "remove_highlight")
    first = {"working": dict(task.working), "history": list(task.history), "results": dict(task.results),
             "applied": {k: list(v) for k, v in task.applied.items()}}
    press(task, "title_property", TITLE)
    assert task.generation == 2 and task.working != first["working"]
    view = task.view()
    assert view["working"]["undo"].startswith("Set the document title property")
    assert view["working"]["undo_blocked"] == ""
    before = on_disk(tmp_path)

    task.undo()
    assert task.working == first["working"] and task.generation == 1 and task.history == first["history"]
    assert task.applied == first["applied"] and task.results == first["results"] and task.stage == "done"
    assert task.documents() == [Path(v) for v in first["working"].values()]   # the next button builds on them
    assert task.messages[-1]["text"].startswith("Undid “Set the document title property")
    assert "nothing was deleted" in task.messages[-1]["text"]
    assert task.view()["working"]["undo"].startswith("Remove")

    task.undo()
    assert task.working == {} and task.generation == 0 and task.history == [] and task.applied == {}
    assert task.results == {} and task.stage == "empty" and task.documents() == files
    assert "works on your originals again" in task.messages[-1]["text"]
    with pytest.raises(ValueError, match="nothing to undo"):
        task.undo()
    assert on_disk(tmp_path) == before                                 # every copy is where it was saved
    assert {p: p.read_bytes() for p in files} == originals


def test_the_next_button_after_an_undo_builds_on_the_copies_from_before(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_highlight")
    first = dict(task.working)
    press(task, "remove_comments")
    task.undo()
    view = task.perform("recipe", recipe="title_property", inputs=TITLE)
    assert view["stage"] == "preview"
    assert sorted(str(p) for p in task.session.documents) == sorted(first.values())
    task.perform("primary", token=view["token"])
    assert task.generation == 2 and len(task.undo_points) == 2
    assert task.undo_points[-1]["working"] == first


def test_undo_takes_the_documents_its_x_set_aside_off_the_to_do_list(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_highlight", hold=[files[1]])
    assert list(task.todo) == [str(files[1])] and list(task.working) == [str(files[0])]
    task.undo()
    assert task.todo == {} and task.working == {}


def test_undo_keeps_the_to_do_entries_added_after_the_run(tmp_path, files):
    empty = tmp_path / "empty.docx"
    empty.write_bytes(build_dirty("empty_body"))
    task = safe_task(tmp_path)
    task.attach([*files, empty])
    press(task, "remove_highlight")
    press(task, "title_property", TITLE)
    task.hand_off([task.view()["left_untouched"][0]["path"]])          # put on the list after that run, by the user
    task.mode = "safe"
    task.undo()
    assert [e["name"] for e in task.todo.values()] == ["empty.docx"]
    assert task.view()["left_untouched"] == []                         # the run before left nothing untouched


def test_undo_restores_what_the_run_before_left_untouched(tmp_path, files):
    empty = tmp_path / "empty.docx"
    empty.write_bytes(build_dirty("empty_body"))
    task = safe_task(tmp_path)
    task.attach([files[0], empty])
    press(task, "title_property", TITLE)
    left = task.view()["left_untouched"]
    press(task, "remove_highlight")
    assert task.view()["left_untouched"] != left
    task.undo()
    assert task.view()["left_untouched"] == left


def test_undo_keeps_which_documents_arrived_after_the_changes(tmp_path, files):
    # From review (Codex on #125): a document attached after the first run got its copy from the second; undoing
    # that run makes it an original again, attached after the change still made, not one an earlier run left out.
    # One attached after the undone run stays so too, and a restart in between changes neither.
    late, later = tmp_path / "21 13 19.docx", tmp_path / "21 13 39.docx"
    late.write_bytes(build("21 13 19", "PREACTION SPRINKLER SYSTEMS"))
    later.write_bytes(build("21 13 39", "FOAM-WATER SPRINKLER SYSTEMS"))
    journal = tmp_path / "conversation.json"
    task = safe_task(tmp_path, journal=journal)
    task.attach(files)
    press(task, "remove_highlight")
    task.attach([late])
    assert task._fresh_documents() == [late.name]
    press(task, "title_property", TITLE)
    assert str(late) in task.working and task.fresh == []
    task.attach([later])
    restored = safe_task(tmp_path, journal=journal)
    assert restored.restore()
    for t in (task, restored):
        t.undo()
        assert str(late) not in t.working and t._fresh_documents() == [late.name, later.name]
        assert t._history_in_scope() == list(t.history)                # the copies all carry it; the late ones are told apart


def test_undo_waits_for_a_preview_to_be_discarded(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_highlight")
    task.perform("recipe", recipe="remove_comments", inputs={})
    assert task.view()["working"]["undo_blocked"] == "Discard the preview first."
    with pytest.raises(ValueError, match="Discard the preview first"):
        task.undo()
    task.discard_preview()
    task.undo()
    assert task.working == {}


def test_undo_refuses_when_a_copy_it_would_go_back_to_is_gone(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_highlight")
    gone = Path(next(iter(task.working.values())))
    press(task, "remove_comments")
    working, points = dict(task.working), len(task.undo_points)
    gone.unlink()
    with pytest.raises(ValueError, match="no longer where they were saved"):
        task.undo()
    assert task.working == working and len(task.undo_points) == points  # nothing changed


def test_a_removed_document_comes_back_as_its_original_not_an_old_copy(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_highlight")
    press(task, "remove_comments")
    task.remove(files[1])
    task.attach([files[1]])
    task.undo()
    assert list(task.working) == [str(files[0])] and task.current_of(files[1]) == files[1]
    assert list(task.results) == [str(files[0])]                      # its old copy is not shown as its result


def test_a_removed_document_is_not_shown_again_by_discard(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_highlight")
    task.perform("recipe", recipe="remove_comments", inputs={})
    task.remove(files[1])                                               # voids the preview
    task.attach([files[1]])
    task.perform("recipe", recipe="remove_comments", inputs={})
    task.discard_preview()
    assert list(task.results) == [str(files[0])]


def test_start_over_clears_what_can_be_undone(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_highlight")
    task.start_over()
    assert task.undo_points == [] and task.view()["working"]["undo"] == ""


def test_undo_points_survive_a_restart_and_a_malformed_one_is_dropped(tmp_path, files):
    journal = tmp_path / "conversation.json"
    task = safe_task(tmp_path, journal=journal)
    task.attach(files)
    press(task, "remove_highlight")
    first = dict(task.working)
    press(task, "remove_comments")
    restored = safe_task(tmp_path, journal=journal)
    assert restored.restore() and len(restored.undo_points) == 2
    restored.undo()
    assert restored.working == first and restored.generation == 1

    data = json.loads(journal.read_text(encoding="utf-8"))
    data["undo"][0]["working"] = ["not", "a", "map"]
    journal.write_text(json.dumps(data), encoding="utf-8")
    again = safe_task(tmp_path, journal=journal)
    assert again.restore() and again.undo_points == []                 # nothing restored wrongly
    assert again.view()["working"]["undo"] == ""


def test_two_runs_in_one_second_never_share_a_folder(tmp_path, files, monkeypatch):
    # A run reads the copies the last one saved, so a run folder shared within one second would hold the run's own
    # inputs, and the run would be refused; Discard and pressing again makes that quick.
    from datetime import datetime

    from spec_surgeon import gui

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 26, 12, 0, 0)
    monkeypatch.setattr(gui, "datetime", Frozen)
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_highlight")
    press(task, "remove_comments")
    assert task.generation == 2
    folders = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert folders[:2] == ["20261026-120000", "20261026-120000-2"]
