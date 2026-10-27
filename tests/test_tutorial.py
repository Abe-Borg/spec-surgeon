"""The guided tour (``spec_surgeon.tutorial``, the window's ``/api/tutorial`` routes; docs/CONVERSATIONAL_DESKTOP.md,
"The guided tour"): the bundled showcase is what its builder makes, every chapter state is built from it through the
production paths with no model call, the user's own task is set aside untouched and comes back as the same object,
settings.json and the journal never take the practice workspace's values, the routes refuse what they must, and the
finished tour's version is kept."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fake_claude import FakeClient, resp, tool
from spec_surgeon import tutorial, vision
from spec_surgeon.document_task import START_OVER, WORK_TODO, DocumentTask, HELD_REASON, LEFT_OUT_REASON
from spec_surgeon.gui import Session
from spec_surgeon.settings import Settings, load_settings
from spec_surgeon.web.server import TOKEN_HEADER, WindowState, create_app
from test_document_task import REQUEST, SPEC, document
from test_web_server import FakeUpdates, frames

TOKEN = "t-tour-0123456789"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_visual_check(monkeypatch):
    """The tour makes no AI call: conftest already makes ``llm.client`` raise; the visual check must not run either."""
    def boom(*_a, **_k):
        raise AssertionError("the tour must never run Claude's visual check")
    monkeypatch.setattr(vision, "review_samples", boom)


def tour_window(tmp_path, journal=None):
    """A window over a task with a scripted client, as ``tests/test_web_server.py`` builds one."""
    fake = FakeClient(resp(tool("submit_editspec", {"spec": SPEC})))
    session = Session(client=fake, out_root=tmp_path / "out", visual_check=False)
    task = DocumentTask(session, render=False, journal=journal)
    settings = Settings(out_root=str(tmp_path / "out"), mode="default")
    state = WindowState(task, settings, token=TOKEN, updates=FakeUpdates(), version="1.6.0")
    client = TestClient(create_app(state), headers={TOKEN_HEADER: TOKEN})
    return state, client


def _parts(data: bytes) -> dict:
    """The parts of a Word package a stale showcase would change: the document, headers and footers (never docProps,
    whose dates python-docx may stamp differently)."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = sorted(z.namelist())
        text = {n: z.read(n) for n in names
                if n == "word/document.xml" or (n.startswith("word/") and ("header" in n or "footer" in n)
                                                 and n.endswith(".xml") and "/_rels/" not in n)}
    return {"members": names, "text": text}


# ---- the bundled showcase ------------------------------------------------------------------------------------------

def test_the_bundled_showcase_is_what_its_builder_makes():
    """tests/make_tutorial_showcase.py builds the files under examples/tutorial; a showcase edited by hand, or left
    behind by a change to the fixture builders, fails here."""
    pytest.importorskip("docx")
    from make_tutorial_showcase import showcase
    built = showcase()
    shipped = tutorial.showcase_files()
    assert set(built) == set(shipped) == {*tutorial.DOCUMENTS, tutorial.REFUSED, tutorial.TEMPLATE,
                                           *tutorial.REFERENCES}
    for name, data in built.items():
        actual = shipped[name].read_bytes()
        if name.endswith(".docx") and name != tutorial.REFUSED:
            assert _parts(actual) == _parts(data), name
        else:
            assert actual == data, name


def test_the_package_ships_the_showcase():
    import tomllib
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "examples/tutorial/*" in pyproject["tool"]["setuptools"]["package-data"]["spec_surgeon"]
    assert '"examples/tutorial/*"' in (ROOT / "packaging" / "windows" / "app.spec").read_text(encoding="utf-8")
    assert "tutorial.showcase_files()" in (ROOT / "packaging" / "installed_wheel_smoke.py").read_text(encoding="utf-8")
    assert len(tutorial.showcase_files()) == 8


def test_the_showcase_attaches_as_the_tour_describes(tmp_path):
    """Chapter 2: four documents, one file triage refuses with its reason, a prepared template with one typed
    placeholder, a picture and a table — and nothing said yet."""
    task = tutorial.build_scenario("start", tmp_path / "start", render=False)
    view = task.view()
    assert view["mode"] == "safe" and view["stage"] == "empty" and view["messages"] == []
    by_name = {Path(a["path"]).name: a for a in view["attachments"]}
    assert set(by_name) == {*tutorial.DOCUMENTS, tutorial.REFUSED, tutorial.TEMPLATE, *tutorial.REFERENCES}
    for name in tutorial.DOCUMENTS:
        assert by_name[name]["state"] in ("ok", "note") and not by_name[name]["template"]
    refused = by_name[tutorial.REFUSED]
    assert refused["state"] == "refused" and "cannot be opened as a Word package" in refused["reason"]
    assert by_name[tutorial.TEMPLATE]["template"] and by_name[tutorial.TEMPLATE]["state"] != "refused"
    assert all(by_name[name]["reference"] for name in tutorial.REFERENCES)
    info = task.template_info()
    assert info["kind"] == "prepared" and info["typed_slots"] == ["ISSUED FOR PERMIT"]
    # the practice copies are copies: the bundled files are read, never used in place
    for a in task.attachments:
        assert a.path.is_relative_to(tmp_path), a.path
    assert task.journal is None


@pytest.mark.parametrize("kind", tutorial.SCENARIOS)
def test_every_chapter_state_builds_without_a_model_call(tmp_path, kind):
    """Each chapter state the page may ask for, through the production paths, with the model client refusing
    (conftest) and the visual check forbidden (above)."""
    folder = tmp_path / kind
    task = tutorial.build_scenario(kind, folder, render=False)
    view = task.view()
    names = lambda items, key="name": [i[key] for i in items]  # noqa: E731
    assert view["mode"] == ("safe" if kind in tutorial.SAFE_SCENARIOS else "default")
    if kind == "start":
        assert view["stage"] == "empty" and not view["messages"]
    elif kind == "button":
        assert view["stage"] == "preview" and view["action"] == "Create edited copies" and view["can_hold"]
        assert len(view["samples"]) == 3 and all(s.passed for s in view["samples"])
        assert names(view["set_aside"]) == [tutorial.OTHER_FIRM]
        assert view["messages"][0]["role"] == "user" and tutorial.STAMP_VALUE in view["messages"][0]["text"]
    elif kind == "copies":
        assert view["stage"] == "done" and view["action"] == ""
        copies = {Path(k).name for k in view["results"]}
        assert copies == {tutorial.DOCUMENTS[0], tutorial.DOCUMENTS[2]}
        assert [(t["name"], t["why"], t["reason"]) for t in view["todo"]] == [(tutorial.HELD, "held", HELD_REASON)]
        assert names(view["left_untouched"]) == [tutorial.OTHER_FIRM]
        assert view["working"]["undo"] and view["working"]["generation"] == 1
        for output in view["results"].values():
            assert Path(output).is_relative_to(folder)
    elif kind == "conversation":
        assert view["stage"] == "empty" and not view["messages"]
    elif kind == "question":
        assert view["stage"] == "question"
        assert [m["role"] for m in view["messages"]] == ["app", "user", "assistant"]
        assert view["messages"][0]["text"] == tutorial.RECORDED
        assert view["questions"] == [tutorial.QUESTION] and view["chips"] == tutorial.QUESTION_CHIPS
        assert [c["editable"] for c in view["choices"]] == [False, True]
        assert view["choices"][1]["words"] == [tutorial.STAMP_VALUE]
        assert view["action"] == ""
    elif kind == "plan":
        assert view["stage"] == "preview" and view["action"] == "Create edited copies" and view["can_hold"]
        assert len(view["samples"]) == 2 and all(s.passed for s in view["samples"])
        assert [(s["name"], s["reason"]) for s in view["set_aside"]] == [(tutorial.LEFT_OUT, LEFT_OUT_REASON)]
        assert [m["role"] for m in view["messages"]] == ["app", "user", "assistant"]
        assert view["messages"][1]["text"] == tutorial.FONT_REQUEST
    elif kind == "todo":
        assert view["stage"] == "done"
        assert {Path(k).name for k in view["results"]} == {tutorial.DOCUMENTS[0], tutorial.OTHER_FIRM}
        assert [(t["name"], t["why"]) for t in view["todo"]] == [(tutorial.HELD, "held")]
        assert WORK_TODO in view["chips"] and START_OVER in view["chips"]
        assert names(view["set_aside"]) == []
    # the bundled files are never written: every practice copy still matches its source
    for name, source in tutorial.showcase_files().items():
        assert (folder / "files" / name).read_bytes() == source.read_bytes(), name
    assert task.session.out_root.is_relative_to(folder)


def test_a_send_or_choose_in_the_practice_workspace_is_refused_before_anything_runs(tmp_path):
    task = tutorial.build_scenario("question", tmp_path / "q", render=False)
    with pytest.raises(ValueError, match="recording"):
        task.perform("send", text="hello")
    with pytest.raises(ValueError, match="recording"):
        task.perform("choose", choice_id="tour-1")
    assert task.view()["stage"] == "question" and len(task.messages) == 3


# ---- the lease on the window ---------------------------------------------------------------------------------------

def test_the_tour_sets_the_users_task_aside_and_restore_returns_the_same_object(tmp_path, monkeypatch):
    journal = tmp_path / "config" / "conversation.json"
    journal.parent.mkdir()
    state, client = tour_window(tmp_path, journal=journal)
    original = state.task
    source = document(tmp_path / "Spec.docx")
    client.post("/api/attach", json={"paths": [str(source)]})
    events = frames(client.post("/api/send", json={"text": REQUEST}))
    assert events[-1]["type"] == "done" and events[-1]["state"]["stage"] == "preview"
    before = client.get("/api/state").json()
    journal_before = journal.read_bytes()
    state.save_settings()
    settings_before = load_settings()
    assert settings_before.mode == "default" and settings_before.out_root == str(tmp_path / "out")
    assert before["tutorial"] == {"active": False, "id": None, "scenario": None, "finished_version": 0}

    view = client.post("/api/tutorial/start", json={"request_id": "r1"}).json()
    assert view["tutorial"]["active"] and view["tutorial"]["scenario"] == "start" and view["mode"] == "safe"
    assert state.task is not original and state.original_task is original
    assert {Path(a["path"]).name for a in view["attachments"]} >= set(tutorial.DOCUMENTS)
    assert view["messages"] == [] and view["cost"]["calls"] == 0
    assert client.get("/api/tutorial/status").json() == view["tutorial"]
    # a repeated start with the same request id is the same tour
    again = client.post("/api/tutorial/start", json={"request_id": "r1"}).json()
    assert again["tutorial"]["id"] == view["tutorial"]["id"]
    tutorial_id = view["tutorial"]["id"]

    # a chapter state, and a job in the practice workspace: nothing of it reaches settings.json or the journal
    shown = client.post("/api/tutorial/scenario", json={"tutorial_id": tutorial_id, "kind": "button"}).json()
    assert shown["stage"] == "preview" and shown["tutorial"]["scenario"] == "button"
    assert [s["name"] for s in shown["plan"]["set_aside"]] == [tutorial.OTHER_FIRM]
    ended = frames(client.post("/api/primary", json={"token": shown["action"]["token"]}))
    assert ended[-1]["type"] == "done" and ended[-1]["state"]["stage"] == "done" and len(ended[-1]["state"]["results"]) == 3
    assert client.post("/api/settings", json={"theme": "linen"}).status_code == 200
    kept = load_settings()
    assert kept.mode == "default" and kept.out_root == str(tmp_path / "out") and kept.theme == "linen"
    assert journal.read_bytes() == journal_before
    practice = Path(ended[-1]["state"]["results"][0]["output"])
    assert practice.is_relative_to(tutorial.practice_root()) and tutorial.practice_root().exists()
    # the conversation is a recording: a message is refused before anything could reach Claude
    refused = frames(client.post("/api/send", json={"text": "make it bold"}))
    assert refused[-1]["type"] == "error" and "recording" in refused[-1]["message"]
    assert client.post("/api/new-conversation").status_code == 409

    restored = client.post("/api/tutorial/restore", json={"tutorial_id": tutorial_id}).json()
    assert state.task is original and state.original_task is None
    assert not restored["tutorial"]["active"] and restored["tutorial"]["id"] is None
    for key in ("stage", "mode", "messages", "attachments", "plan", "samples", "action", "working", "todo"):
        assert restored[key] == before[key], key
    assert not tutorial.practice_root().exists()
    assert journal.read_bytes() == journal_before
    # with no tour running, restore is nothing to do
    assert client.post("/api/tutorial/restore", json={}).status_code == 200


def test_the_routes_refuse_what_they_must(tmp_path):
    state, client = tour_window(tmp_path)
    assert client.post("/api/tutorial/scenario", json={"tutorial_id": "x", "kind": "button"}).status_code == 409
    first = client.post("/api/tutorial/start", json={}).json()
    tutorial_id = first["tutorial"]["id"]
    second = client.post("/api/tutorial/start", json={})
    assert second.status_code == 409 and "already running" in second.json()["error"]
    assert client.post("/api/tutorial/scenario", json={"tutorial_id": "stale", "kind": "button"}).status_code == 409
    unknown = client.post("/api/tutorial/scenario", json={"tutorial_id": tutorial_id, "kind": "finale"})
    assert unknown.status_code == 400 and "finale" in unknown.json()["error"]
    assert client.post("/api/tutorial/restore", json={"tutorial_id": "stale"}).status_code == 409
    # the same chapter state asked for twice is built once
    one = client.post("/api/tutorial/scenario", json={"tutorial_id": tutorial_id, "kind": "plan"}).json()
    task = state.task
    two = client.post("/api/tutorial/scenario", json={"tutorial_id": tutorial_id, "kind": "plan"}).json()
    assert state.task is task and one["action"]["token"] == two["action"]["token"]
    # while a chapter builds (or a job runs) every transition and job is refused, and the view says busy
    state._transition = True
    try:
        assert client.get("/api/state").json()["busy"] is True
        for route, body in (("/api/tutorial/start", {}), ("/api/tutorial/scenario", {"tutorial_id": tutorial_id, "kind": "start"}),
                            ("/api/tutorial/restore", {"tutorial_id": tutorial_id}), ("/api/send", {"text": "x"})):
            assert client.post(route, json=body).status_code == 409, route
    finally:
        state._transition = False
    assert client.post("/api/tutorial/restore", json={"tutorial_id": tutorial_id}).status_code == 200


def test_the_finished_tour_version_is_kept_in_settings(tmp_path):
    state, client = tour_window(tmp_path)
    assert client.get("/api/settings").json()["tutorial_version"] == 0
    assert client.post("/api/settings", json={"tutorial_version": 3}).json()["tutorial_version"] == 3
    assert load_settings().tutorial_version == 3
    assert client.get("/api/state").json()["tutorial"]["finished_version"] == 3
    for bad in (True, -1, "3", 2.5):
        assert client.post("/api/settings", json={"tutorial_version": bad}).status_code == 400, bad
    assert load_settings().tutorial_version == 3
    # a hand-edited file reads as never finished
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"tutorial_version": "lots"}), encoding="utf-8")
    assert load_settings(path).tutorial_version == 0
