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
from spec_surgeon.document_task import RUN_SET_ASIDE, RUN_UNTOUCHED, WORK_TODO, DocumentTask
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


def waiting_copies(tmp_path, monkeypatch, *replies, journal=None):
    """Three documents, Third.docx set aside with the ✗ and Second.docx failing its first run: Create delivers
    First.docx's copy and recovery's preview of Second.docx waits, so First.docx's copy waits on it to join the
    working set."""
    docs = [document(tmp_path / f"{n}.docx") for n in ("First", "Second", "Third")]
    task = recovering_task(tmp_path, monkeypatch, plan(), resp(tool("submit_repair", REPAIR)), *replies,
                           journal=journal)
    task.attach(docs)
    fail_once(monkeypatch, docs[1])
    view = task.perform("send", text=REQUEST)
    view = task.perform("primary", token=view["token"], hold=[str(docs[2])])
    assert task.session.recovery and view["stage"] == "preview" and list(task.results) == [str(docs[0])]
    assert task.working == {} and list(task.todo) == [str(docs[2])] and task._pending_steps["steps"]
    return task, docs


def held_state(task):
    return {"results": dict(task.results), "working": dict(task.working), "todo": dict(task.todo),
            "attachments": [(a.path, a.template, a.reference) for a in task.attachments], "mode": task.mode,
            "pending": (task._pending_undo, task._pending_steps), "recovery": task.session.recovery,
            "decisions": list(task.decisions), "token": task.action_token()}


HOLDING_ACTIONS = {
    "attach a document": lambda task, docs, tmp: task.attach([document(tmp / "Fourth.docx")]),
    "attach the template": lambda task, docs, tmp: task.attach([document(tmp / "Template.docx")], template=True),
    "remove a delivered document": lambda task, docs, tmp: task.remove(docs[0]),
    "remove the one being checked": lambda task, docs, tmp: task.remove(docs[1]),
    "remove every document": lambda task, docs, tmp: task.remove_documents(),
    "turn on Obliterate": lambda task, docs, tmp: task.set_mode("obliterate", acknowledged=True),
    "switch to Safe mode": lambda task, docs, tmp: task.set_mode("safe"),
    "put a document back": lambda task, docs, tmp: task.put_back(docs[2]),
    "work on the to-do list": lambda task, docs, tmp: task.start_todo(),
}


@pytest.mark.parametrize("action", sorted(HOLDING_ACTIONS))
def test_an_action_that_would_end_a_recovery_waits_while_its_runs_copies_do(tmp_path, monkeypatch, action):
    # The run delivered First.docx's copy and recovery still checks Second.docx: First.docx's copy joins the working
    # set when the check ends. An attach, a removal, a mode change, Put back or a pass over the to-do list would drop
    # the recovery first and leave that copy listed but built on by nothing — so it waits, with the reason, as Discard
    # does, and nothing changes
    task, docs = waiting_copies(tmp_path, monkeypatch)
    task.obliterate_allowed = True                                     # Settings → Advanced shows it
    before = held_state(task)
    view = task.view()
    assert view["recovery_blocked"].startswith("Some copies from this request are saved while the rest are checked "
                                               "again") and view["recovery_blocked"].endswith(
        "Finish it first: create the copies it fixed.")
    assert view["discard"] == "" and view["working"]["undo_blocked"] == ""   # nothing to undo yet
    with pytest.raises(ValueError, match="Some copies from this request are saved"):
        HOLDING_ACTIONS[action](task, docs, tmp_path)
    assert held_state(task) == before
    # the recovery's own button ends it: both copies join the working set as one run, with the plan approved
    view = task.perform("primary", token=view["token"])
    assert view["stage"] == "done" and task.generation == 1 and view["recovery_blocked"] == ""
    assert sorted(task.working) == sorted(str(d) for d in docs[:2])
    [row] = task._done_view()
    assert row["request"] == REQUEST and row["documents"] == 2 and row["why_not"] == ""
    assert len(task.undo_points) == 1 and task.undo_points[0]["working"] == {}
    HOLDING_ACTIONS[action](task, docs, tmp_path)                        # and the action goes on as it always did


def test_words_that_would_end_a_recovery_wait_while_its_runs_copies_do(tmp_path, monkeypatch):
    # At recovery's preview every document it checks is fixed, so words cannot go on within it: a reply would void it
    # and plan the request again on the originals. While First.docx's copy waits on it, the words are not taken — no
    # model call, no decision, the preview and its token stand — and the chat says why
    task, docs = waiting_copies(tmp_path, monkeypatch)
    before, calls = held_state(task), len(task.session.client.requests)
    view = task.perform("send", text=AGAIN)
    assert view["stage"] == "preview" and held_state(task) == before
    assert len(task.session.client.requests) == calls and AGAIN not in task.word_sources
    assert [m["text"] for m in task.messages[-2:]] == [AGAIN, view["recovery_blocked"]]
    view = task.perform("send", text=WORK_TODO)                       # the pass over the to-do list waits too
    assert held_state(task) == before and task.focus is None
    assert [m["text"] for m in task.messages[-2:]] == [WORK_TODO, view["recovery_blocked"]]
    task.perform("primary", token=view["token"])
    assert task.generation == 1 and len(task.working) == 2 and task._done_view()[0]["why_not"] == ""
    view = task.perform("send", text=WORK_TODO)                       # once the check ends, the pass begins
    assert task.focus == {**task.focus, "documents": [str(docs[2])]}


def test_undo_and_a_recipe_wait_with_the_reason_while_a_runs_copies_wait(tmp_path, monkeypatch):
    # an earlier run to undo: while the second run's First.docx copy waits on the check of Second.docx, Undo would take
    # the first run off and drop the check — it waits, saying why, and so does a saved recipe
    docs = [document(tmp_path / "First.docx"), document(tmp_path / "Second.docx")]
    italic = {"candidate": {"kind": "spec", "spec": ITALIC}}
    task = recovering_task(tmp_path, monkeypatch, plan(), plan(ITALIC, "t2"), resp(tool("submit_repair", italic)))
    task.attach(docs)
    task.perform("primary", token=task.perform("send", text=REQUEST)["token"])
    first = dict(task.working)
    fail_once(monkeypatch, Path(first[str(docs[1])]))
    view = task.perform("primary", token=task.perform("send", text=AGAIN)["token"])
    reason = view["recovery_blocked"]
    assert task.session.recovery and reason.startswith("Some copies from this request are saved")
    assert view["working"]["undo_blocked"] == reason and view["recipe_blocked"] == reason
    with pytest.raises(ValueError, match="Some copies from this request are saved"):
        task.undo()
    assert task.working == first and task.generation == 1 and len(task.undo_points) == 1
    task.perform("primary", token=view["token"])
    assert task.generation == 2 and task.view()["working"]["undo_blocked"] == ""
    task.undo()
    assert task.working == first


def test_the_reason_names_the_way_out_the_check_is_at(tmp_path, monkeypatch):
    # a check still asking about a document: answer it, save what is ready, or set the rest aside
    task, docs = waiting_copies(tmp_path, monkeypatch)
    task.session.recovery.files[0].status = "UNRESOLVED"
    assert task._recovery_blocked().endswith("Finish it first: answer its question, save the ready copies, or set "
                                             "the rest aside for later.")
    task.stage = "resume"                                              # a reopened window: Continue goes on with it
    assert task._recovery_blocked().endswith("Finish it first: continue it.")


def test_a_reopened_window_holds_them_while_its_recovery_waits_to_continue(tmp_path, monkeypatch):
    # The journal keeps the recovery to resume; until Continue loads it, the session holds none — Drop this request,
    # Undo or an attach would still have dropped it, and the copy it waits for with it
    journal = tmp_path / "conversation.json"
    task, docs = waiting_copies(tmp_path, monkeypatch, journal=journal)
    again = recovering_task(tmp_path, monkeypatch, journal=journal)
    assert again.restore() and again.session.recovery is None and again._resume_recovery
    view = again.view()
    assert view["stage"] == "resume" and view["discard"] == ""
    assert view["recovery_blocked"].endswith("Finish it first: continue it.")
    with pytest.raises(ValueError, match="Some copies from this request are saved"):
        again.discard_preview()
    with pytest.raises(ValueError, match="Some copies from this request are saved"):
        again.attach([document(tmp_path / "Fourth.docx")])
    assert again._recipe_blocked() == view["recovery_blocked"]
    view = again.perform("primary", token=again.action_token())       # Continue loads the check again
    assert again.session.recovery and view["stage"] == "preview"
    again.perform("primary", token=view["token"])
    assert again.generation == 1 and len(again.working) == 2 and again._done_view()[0]["why_not"] == ""


def test_a_window_reopened_in_safe_mode_goes_back_to_default_for_the_check(tmp_path, monkeypatch):
    # Safe mode cannot go on with a check (no AI step there): its buttons wait, and the way to Default mode stays open
    journal = tmp_path / "conversation.json"
    waiting_copies(tmp_path, monkeypatch, journal=journal)
    from spec_surgeon.recovery import RecoveryOptions
    from spec_surgeon.saved_recipes import Store
    again = DocumentTask(Session(client=FakeClient(), out_root=tmp_path / "out", visual_check=False), render=False,
                         journal=journal, mode="safe", recipes=Store(tmp_path / "config" / "recipes.json"))
    monkeypatch.setattr(again, "_recovery_options", lambda: RecoveryOptions(render=False, critic_review=False,
                                                                           visual_review=False))
    assert again.restore() and again.mode == "safe"
    view = again.perform("recipe", recipe="remove_highlight", inputs={})
    assert view["stage"] == "resume" and again._resume_recovery
    assert again.messages[-1]["text"].endswith("Switch to Default mode to do that.")
    again.set_mode("default")
    view = again.perform("primary", token=again.action_token())
    again.perform("primary", token=view["token"])
    assert again.generation == 1 and len(again.working) == 2


def test_a_run_that_delivered_nothing_ends_with_its_recovery(tmp_path, monkeypatch):
    # Nothing waits on the check: an attach voids it as before, the request stays open to be checked again, and the
    # run's undo point and steps end with it
    docs = [document(tmp_path / "First.docx"), document(tmp_path / "Second.docx")]
    task = recovering_task(tmp_path, monkeypatch, plan(), resp(tool("submit_repair", REPAIR)))
    task.attach(docs[:1])
    fail_once(monkeypatch, docs[0])
    task.perform("primary", token=task.perform("send", text=REQUEST)["token"])
    assert task.session.recovery and task.results == {} and task._pending_undo is not None
    assert task.view()["recovery_blocked"] == ""
    task.attach(docs[1:])
    assert task.session.recovery is None and task._pending_undo is None and task._pending_steps is None
    assert task.stage == "resume" and task.decisions == [REQUEST] and task.working == {} and task.runs == []


def test_words_that_end_a_reopened_windows_check_leave_nothing_of_it_to_resume(tmp_path, monkeypatch):
    # a run that delivered nothing, its check kept by the journal: a new message ends it, and no later Continue or
    # reopen goes back to it
    journal = tmp_path / "conversation.json"
    source = document(tmp_path / "Spec.docx")
    task = recovering_task(tmp_path, monkeypatch, plan(), resp(tool("submit_repair", REPAIR)), journal=journal)
    task.attach([source])
    fail_once(monkeypatch, source)
    task.perform("primary", token=task.perform("send", text=REQUEST)["token"])
    again = recovering_task(tmp_path, monkeypatch, plan(ITALIC, "t2"), journal=journal)
    assert again.restore() and again._resume_recovery and again._pending_undo is not None
    assert again.view()["recovery_blocked"] == ""
    view = again.perform("send", text=AGAIN)
    assert view["stage"] == "preview" and again._resume_recovery is None and again._pending_undo is None
    assert json.loads(journal.read_text(encoding="utf-8"))["recovery"] is None


def test_an_outcome_plan_after_words_that_ended_a_runs_recovery_keeps_its_own_point_and_steps(tmp_path, monkeypatch):
    # A run that delivered nothing, a revision typed at its recovery's preview: the words end the recovery (they are
    # not about a document it still checks), and with it the run's undo point and steps. The outcome plan the
    # planner then offers is the user's pick; its save takes its own point and keeps no plan for a recipe — never the
    # earlier run's, which made none of these copies
    from spec_surgeon import continuous_recovery, saved_recipes
    from spec_surgeon.recovery import RecoveryOptions
    from test_document_task import CONTRACT
    monkeypatch.setattr(continuous_recovery, "options_for", lambda *a, **kw:
                        RecoveryOptions(render=False, critic_review=False, visual_review=False))
    source = document(tmp_path / "Spec.docx")
    task = recovering_task(tmp_path, monkeypatch, plan(), resp(tool("submit_repair", REPAIR)),
                           resp(tool("propose_plan", {"contract": CONTRACT, "rationale": "Format the title."})))
    task.attach([source])
    fail_once(monkeypatch, source)
    view = task.perform("primary", token=task.perform("send", text=REQUEST)["token"])
    assert task.session.recovery and view["stage"] == "preview" and task._pending_steps["steps"]
    view = task.perform("send", text=AGAIN)
    assert task.session.recovery is None and task._pending_undo is None and task._pending_steps is None
    [choice] = view["choices"]
    assert view["stage"] == "question" and choice["label"] == "Use these outcomes and continue"
    view = task.perform("choose", choice_id=choice["id"])
    assert task.session.recovery and view["stage"] == "preview"
    task.perform("primary", token=view["token"])
    assert task.stage == "done" and task.generation == 1 and list(task.working) == [str(source)]
    [row] = task._done_view()
    assert row["request"] == REQUEST and row["steps"] == [] and row["why_not"] == saved_recipes.WHY_NOT_KEPT
    assert task.undo_points[0]["working"] == {}
    task.undo()
    assert task.working == {} and task.runs == []


def test_the_window_refuses_them_with_the_reason(tmp_path, monkeypatch):
    # the routes answer 400 with the reason, never a 500, and a dropped file is refused before a byte is written
    from fastapi.testclient import TestClient
    from spec_surgeon.settings import Settings
    from spec_surgeon.web.server import TOKEN_HEADER, WindowState, create_app
    task, docs = waiting_copies(tmp_path, monkeypatch)
    state = WindowState(task, Settings(), token="t-0123456789", version="1.6.0")
    client = TestClient(create_app(state), headers={TOKEN_HEADER: "t-0123456789"})
    reason = client.get("/api/state").json()["recovery_blocked"]
    assert reason.startswith("Some copies from this request are saved")
    fourth = document(tmp_path / "Fourth.docx")
    for route, body in (("/api/attach", {"paths": [str(fourth)]}), ("/api/remove", {"path": str(docs[0])}),
                        ("/api/remove-documents", None), ("/api/mode", {"mode": "safe"}),
                        ("/api/put-back", {"path": str(docs[2])})):
        answer = client.post(route, json=body) if body is not None else client.post(route)
        assert answer.status_code == 400 and answer.json() == {"error": reason}, route
    uploads = Path(task.session.out_root) / "attachments"
    answer = client.post("/api/upload", files=[("files", ("Fifth.docx", fourth.read_bytes(), "application/octet-stream"))])
    assert answer.status_code == 400 and answer.json() == {"error": reason} and not uploads.exists()
    assert task.session.recovery and list(task.results) == [str(docs[0])] and task.working == {}


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
    assert list(task.view()["results"]) == [str(files[0])]            # its copy leaves the copies list with it
    task.attach([files[1]])
    assert list(task.results) == [str(files[0])] and list(task.export_set()) == [str(files[0])]
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


@pytest.mark.parametrize("left_out", ["leave_out", "the ✗"])
def test_a_removed_document_stays_its_original_through_a_run_that_does_not_deliver_it(tmp_path, left_out):
    """Default mode keeps every copy its runs delivered in ``results`` (no button resets it), so a removed document's
    copy leaves with it: attached again it is an original, never listed or exported as an old copy, and a later run
    that does not deliver it — kept to the other document, or the ✗ — never makes that old copy the one the next
    request builds on, nor counts the document among the run's."""
    docs = [document(tmp_path / "A.docx"), document(tmp_path / "B.docx")]
    a, b = (str(p) for p in docs)
    second = {"spec": ITALIC, "leave_out": ["B.docx"]} if left_out == "leave_out" else {"spec": ITALIC}
    task = chat_task(tmp_path, plan(), resp(tool("submit_editspec", second, "t2")))
    task.attach(docs)
    view = task.perform("send", text=REQUEST)
    task.perform("primary", token=view["token"])
    old = task.working[b]
    task.remove(docs[1])
    view = task.view()
    assert list(view["results"]) == [a] and old not in view["export_state"]
    task.attach([docs[1]])
    view = task.view()
    assert list(view["results"]) == [a] and old not in view["export_state"] and list(task.export_set()) == [a]
    assert task.current_of(docs[1]) == docs[1]
    view = task.perform("send", text=AGAIN)
    assert view["stage"] == "preview"
    task.perform("primary", token=view["token"], hold=[b] if left_out == "the ✗" else [])
    assert list(task.working) == [a] and task.current_of(docs[1]) == docs[1]      # still its original
    assert list(task.results) == [a] and old not in task.results.values()
    assert task.runs[-1]["documents"] == [a] and b not in task.applied
    assert task.runs[-1]["not"] == {b: RUN_SET_ASIDE if left_out == "the ✗" else RUN_UNTOUCHED}
    assert [run["documents"] for run in task.view()["working"]["done"]] == [1, 1]


def test_a_removed_document_takes_its_unverified_mark_with_it(tmp_path, monkeypatch):
    """The UNVERIFIED mark belongs to the copy it was delivered with: removed, the document takes it along, and attached
    again it is an original — the model is not told it was delivered UNVERIFIED, and its next copy, checked against
    the original, carries no mark from an earlier copy."""
    from spec_surgeon import batch
    from spec_surgeon.verify.suite import CheckResult
    real, planted = batch.run_suite, []

    def failing(src, out, plan_, s, o):
        rec = real(src, out, plan_, s, o)
        if not planted and Path(s).name == "B.docx":
            planted.append(s)
            rec.set(CheckResult("V2", "Text preservation", "FAIL", "planted"))
        return rec
    monkeypatch.setattr(batch, "run_suite", failing)
    docs = [document(tmp_path / "A.docx"), document(tmp_path / "B.docx")]
    a, b = (str(p) for p in docs)
    task = chat_task(tmp_path, plan(), plan(ITALIC, "t2"), mode="obliterate")
    task.attach(docs)
    view = task.perform("send", text=REQUEST)
    task.perform("primary", token=view["token"])
    assert planted and task.unverified == {b: ["V2 (Text preservation)"]}
    task.remove(docs[1])
    assert task.unverified == {} and task.view()["unverified"] == {}
    task.attach([docs[1]])
    view = task.perform("send", text=AGAIN)
    assert "UNVERIFIED" not in task.session.client.requests[-1]["messages"][0]["content"]
    task.perform("primary", token=view["token"])
    assert all(f.status == "PASS" for f in task.session.last_batch.files) and set(task.working) == {a, b}
    assert task.unverified == {}


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
