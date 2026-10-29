"""Safe mode's kill switch (``stopping``): Stop ends the work itself, since Safe mode calls no AI — the preview being
made, the copies being created and checked, a clean export — and nothing the work made is used. Between files and
steps a pressed Stop raises ``stopping.Stopped``; work running elsewhere is looked at every ``POLL_SECONDS`` and ended
first (worker processes terminated, LibreOffice's process tree killed, a Word worker ended). The window arms it for a
Safe-mode job only (``WindowState.start_job``); in Default and Obliterate a file operation still finishes first."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from spec_surgeon import batch, recipes, stopping
from spec_surgeon.document_task import STOPPED, DocumentTask
from spec_surgeon.gui import Session
from spec_surgeon.render import libreoffice, word_worker
from spec_surgeon.settings import Settings
from spec_surgeon.web.server import WindowState
from test_export_clean import edited, no_ai, renderer  # noqa: F401
from test_going_back import TITLE, files, on_disk, press, safe_task  # noqa: F401


def stop_when(event: threading.Event, words: str):
    """A ``log`` that presses Stop when the job says ``words``, and keeps every line."""
    lines = []

    def log(message):
        lines.append(str(message))
        if words in str(message):
            event.set()
    log.lines = lines
    return log


# ---- the switch itself ----------------------------------------------------------------------------------------------

def test_outside_a_scope_nothing_is_armed_and_every_wait_is_the_plain_one():
    assert not stopping.armed() and not stopping.requested()
    stopping.check("anywhere")                                  # no-op
    assert stopping.poll(600) == 600
    with stopping.scope(None):
        assert not stopping.armed()
        stopping.check()


def test_a_pressed_stop_ends_the_work_at_the_next_check_after_ending_what_runs_elsewhere():
    event = threading.Event()
    ended = []
    with stopping.scope(event):
        assert stopping.armed() and stopping.poll(600) == stopping.POLL_SECONDS
        stopping.check("before", on_stop=lambda: ended.append("too early"))
        event.set()
        with pytest.raises(stopping.Stopped) as stopped:
            stopping.check("between files", on_stop=lambda: ended.append("ended"))
    assert ended == ["ended"] and stopped.value.where == "between files"
    # a BaseException: the engine's handlers that keep a batch going past one file's failure never take it for one
    assert not isinstance(stopped.value, Exception)
    assert not stopping.armed()


def test_a_shared_map_is_ended_at_once_and_its_worker_processes_terminated(monkeypatch):
    monkeypatch.setattr(batch, "DEFAULT_WORKER_MODE", "process")
    monkeypatch.setattr(os, "cpu_count", lambda: 4)
    seen = []
    original = batch.stop_pool

    def spy(ex):
        seen.extend((getattr(ex, "_processes", None) or {}).values())
        original(ex)
    monkeypatch.setattr(batch, "stop_pool", spy)
    event = threading.Event()
    timer = threading.Timer(1.0, event.set)
    timer.start()
    started = time.monotonic()
    try:
        with stopping.scope(event), pytest.raises(stopping.Stopped):
            batch.map_files(time.sleep, [(60,)] * 3)
    finally:
        timer.cancel()
    assert time.monotonic() - started < 30                      # not the minute each call would sleep
    assert seen and not any(p.is_alive() for p in seen)


def test_a_batch_shared_over_threads_stops_between_files_and_returns_nothing(tmp_path, files):
    third = tmp_path / "21 13 19.docx"
    third.write_bytes(files[0].read_bytes())
    spec = recipes.build("remove_highlight", {}, recipes.Context(files, None, [])).spec
    event = threading.Event()
    log = stop_when(event, "  done ")
    with stopping.scope(event), pytest.raises(stopping.Stopped):
        batch.run_batch([str(f) for f in [*files, third]], "edit-spec", {}, tmp_path / "run", workers=2,
                        do_render=False, progress=log, spec=spec, worker_mode="thread")
    assert sum(1 for line in log.lines if line.startswith("  done ")) < 3


def test_libreoffice_is_killed_tree_and_all_on_a_stop(monkeypatch):
    made = []
    real = subprocess.Popen

    def popen(cmd, *a, **k):
        proc = real(cmd, *a, **k)
        if cmd[0] == sys.executable:                  # the sleeper, not taskkill on Windows
            made.append(proc)
        return proc
    monkeypatch.setattr(subprocess, "Popen", popen)
    event = threading.Event()
    timer = threading.Timer(0.5, event.set)
    timer.start()
    started = time.monotonic()
    try:
        with stopping.scope(event), pytest.raises(stopping.Stopped):
            libreoffice._run_tree([sys.executable, "-c", "import time; time.sleep(60)"], dict(os.environ), 120)
    finally:
        timer.cancel()
    assert time.monotonic() - started < 30
    [proc] = made
    assert proc.poll() is not None


def test_without_a_stop_libreoffice_keeps_its_timeout():
    with pytest.raises(subprocess.TimeoutExpired):
        libreoffice._run_tree([sys.executable, "-c", "import time; time.sleep(60)"], dict(os.environ), 0.5)


def test_a_word_worker_is_ended_on_a_stop_and_publishes_nothing(tmp_path, files):
    event = threading.Event()
    event.set()
    with stopping.scope(event), pytest.raises(stopping.Stopped):
        word_worker.run_word_batch([files[0]], tmp_path / "pdf", timeout_per_file=60)
    assert not list((tmp_path / "pdf").glob("*.pdf"))


# ---- what the window shows after a stop -------------------------------------------------------------------------

def test_a_stopped_preview_is_withdrawn_and_the_window_shows_what_came_before(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    press(task, "remove_highlight")
    copies, working, runs = dict(task.results), dict(task.working), list(task.runs)
    before = on_disk(tmp_path)
    event = threading.Event()
    with stopping.scope(event), pytest.raises(stopping.Stopped):
        task.perform("recipe", recipe="title_property", inputs=TITLE, log=stop_when(event, "Planning every file"))
    view = task.view()
    assert view["stage"] == "done" and view["results"] == copies and task.working == working and task.runs == runs
    assert task.session.spec is None and task.session.preview is None and view["action"] == ""
    assert view["samples"] == [] and task.decisions == []
    assert task.messages[-1]["text"] == STOPPED["preview"]
    assert {p: b for p, b in on_disk(tmp_path).items() if p in before} == before    # nothing written over


def test_a_first_buttons_stopped_preview_returns_to_the_empty_window(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    event = threading.Event()
    with stopping.scope(event), pytest.raises(stopping.Stopped):
        task.perform("recipe", recipe="remove_highlight", inputs={}, log=stop_when(event, "Planning every file"))
    assert task.stage == "empty" and task.results == {} and task.session.spec is None
    view = task.perform("recipe", recipe="remove_highlight", inputs={})     # the button works again afterwards
    assert view["stage"] == "preview"


def test_a_stopped_create_delivers_no_copy_and_its_preview_waits_for_the_same_approval(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    view = task.perform("recipe", recipe="remove_highlight", inputs={})
    token = view["token"]
    event = threading.Event()
    with stopping.scope(event), pytest.raises(stopping.Stopped):
        task.perform("primary", token=token, hold=[str(files[1])],
                     log=stop_when(event, "Creating and checking your edited copies"))
    view = task.view()
    assert view["stage"] == "preview" and view["token"] == token     # the marks on the previews still stand
    assert task.results == {} and task.working == {} and task.runs == [] and task.generation == 0
    assert task.todo == {}                                           # the ✗'d document is not set aside yet
    assert task.messages[-1]["text"] == STOPPED["create"]
    view = task.perform("primary", token=token, hold=[str(files[1])])
    assert view["stage"] == "done" and len(task.results) == 1 and list(task.todo) == [str(files[1])]


def test_a_create_stopped_between_files_still_delivers_nothing(tmp_path, files):
    task = safe_task(tmp_path)
    task.attach(files)
    view = task.perform("recipe", recipe="remove_highlight", inputs={})
    event = threading.Event()
    with stopping.scope(event), pytest.raises(stopping.Stopped):
        task.perform("primary", token=view["token"], log=stop_when(event, "  done "))
    assert task.stage == "preview" and task.results == {} and task.working == {}


def test_a_stopped_export_saves_nothing_and_changes_nothing(tmp_path, no_ai):  # noqa: F811
    task = edited(tmp_path)
    copies = dict(task.results)
    dest = tmp_path / "saved"
    dest.mkdir()
    event = threading.Event()
    with stopping.scope(event), pytest.raises(stopping.Stopped):
        task.perform("export", kind="accepted", folder=str(dest), log=stop_when(event, ""))
    assert list(dest.iterdir()) == [] and task.exported == {} and task.results == copies
    assert task.stage == "done" and task.messages[-1]["text"] == STOPPED["export"]


def test_a_stop_after_the_clean_copies_are_made_still_saves_nothing(tmp_path, no_ai, monkeypatch):  # noqa: F811
    from spec_surgeon import exports
    task = edited(tmp_path)
    dest = tmp_path / "saved"
    dest.mkdir()
    event = threading.Event()
    made = exports.make_clean

    def then_stop(*a, **k):
        result = made(*a, **k)
        event.set()                                   # pressed once every clean copy is made, before any is written
        return result
    monkeypatch.setattr(exports, "make_clean", then_stop)
    with stopping.scope(event), pytest.raises(stopping.Stopped):
        task.perform("export", kind="accepted", folder=str(dest))
    assert list(dest.iterdir()) == [] and task.messages[-1]["text"] == STOPPED["export"]


def test_a_stop_while_the_pdfs_are_checked_saves_no_pdf(tmp_path, renderer, no_ai, monkeypatch):  # noqa: F811
    from spec_surgeon import pdf_export
    task = edited(tmp_path)
    dest = tmp_path / "saved"
    dest.mkdir()
    event = threading.Event()
    check = pdf_export.check_pdf

    def checked_then_stop(pdf):
        event.set()
        return check(pdf)
    monkeypatch.setattr(pdf_export, "check_pdf", checked_then_stop)
    with stopping.scope(event), pytest.raises(stopping.Stopped):
        task.perform("export", kind="pdf", folder=str(dest))
    assert list(dest.iterdir()) == [] and task.messages[-1]["text"] == STOPPED["export"]


# ---- the window arms it for a Safe-mode job only -------------------------------------------------------------------

def _drain(events: queue.Queue) -> dict:
    while True:
        event = events.get(timeout=60)
        if event["type"] in ("done", "error"):
            return event


def test_the_window_stops_a_safe_mode_job_and_its_stream_ends_stopped(tmp_path, files, monkeypatch):
    task = safe_task(tmp_path)
    task.attach(files)
    state = WindowState(task, Settings(), token="t" * 32)
    running = threading.Event()

    def make_preview(self, render, log, on_preview=None):
        running.set()
        while True:                                   # work in this process, looking at Stop between its steps
            stopping.check("test")
            time.sleep(0.01)
    monkeypatch.setattr(Session, "make_preview", make_preview)
    events = state.start_job("recipe", recipe="remove_highlight", inputs={})
    assert running.wait(30)
    assert state.stop() is True
    event = _drain(events)
    state.thread.join(30)
    assert event["type"] == "done" and event.get("stopped") is True and not state.busy
    assert event["state"]["stage"] == "empty" and event["state"]["action"]["kind"] == "none"
    assert event["state"]["messages"][-1]["text"] == STOPPED["preview"]


def test_a_default_mode_job_is_not_armed_and_finishes_its_file_work(tmp_path):
    task = DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=False)
    state = WindowState(task, Settings(mode="default"), token="t" * 32)
    seen = {}

    def perform(action, **kwargs):
        seen["armed"] = stopping.armed()
        return task.view()
    task.perform = perform
    event = _drain(state.start_job("recipe", recipe="remove_highlight", inputs={}))
    state.thread.join(30)
    assert seen == {"armed": False} and event["type"] == "done" and not event.get("stopped")
