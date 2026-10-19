"""The window's Qt objects die on the main thread, at the moment a test chooses — never in the garbage collector.

The segmentation fault of 2026-10-16: ``tests/test_diagnostics.py``'s windows were only closed, so the cyclic garbage
collector freed them later, on whatever thread was allocating at the time — in ``test_editspec.py``'s 24-file batch that
is the process pool's manager thread, while the main thread waits in ``as_completed``. PySide6 6.12 deletes a Python
subclass of a Qt class (``MainWindow``) right there, on that thread, and defers the plain Qt widgets Shiboken still
books as Python-owned (44 of them in a window: the ``QLabel``/``QPushButton``/``QCheckBox`` placed through layouts) to
the main thread — where that second ``delete`` runs on memory Qt freed with the window: SIGSEGV in
``Shiboken::BindingManager::runDeletionInMainThread``, in whatever test ran next, whenever a collection happened to run
off the main thread (one run in five passed). ``qt_support.close_windows`` is the fix: close, ``deleteLater``, flush
the deferred deletes (``processEvents()`` alone never delivers them outside an event loop — ``test_gui.py``'s old
teardown only leaked its windows) and collect the garbage on the main thread, while the test still holds every wrapper.

These tests pin the helper, the rule that every window test tears down through it, and the one Qt object the window
made without a parent (the table models) now belonging to their page."""
from __future__ import annotations

import ast
import gc
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent


def _window(tmp_path):
    from qt_support import qt_app_or_skip
    from spec_surgeon.gui import Session
    from spec_surgeon.ui.app import build_legacy_window as build_window
    from spec_surgeon.ui.app import RecordingPrompts
    app = qt_app_or_skip()
    w = build_window(Session(out_root=tmp_path / "out"), prompts=RecordingPrompts())
    w._spec_surgeon["wait"]()
    return app, w


def _under(o, w) -> bool:
    p = o.parent()
    while p is not None:
        if p is w:
            return True
        p = p.parent()
    return False


def test_close_windows_deletes_the_window_and_everything_it_owns_on_the_main_thread(tmp_path, monkeypatch):
    """After the teardown no wrapper of the window or of anything it made is valid: there is nothing left for a
    collection on another thread to delete (a valid, Python-owned wrapper would be deferred and deleted again)."""
    import shiboken6
    from PySide6.QtCore import QObject
    from qt_support import close_windows
    monkeypatch.setenv("SPEC_SURGEON_NO_UPDATE_CHECK", "1")
    app, w = _window(tmp_path)
    descendants = [o for o in shiboken6.getAllValidWrappers() if isinstance(o, QObject) and o is not w and _under(o, w)]
    owned = [w.bridge, w.updates, w.updates.banner, w._files_timer, w.preview_page.model, w.preview_page.proxy,
             w.run_page.model, w.run_page.proxy]
    assert len(descendants) > 100 and all(shiboken6.isValid(o) for o in owned)
    windows = [w]
    close_windows(app, windows)
    assert windows == [] and not shiboken6.isValid(w)
    assert not any(shiboken6.isValid(o) for o in descendants)
    assert not any(shiboken6.isValid(o) for o in owned)
    assert not any(isinstance(o, QObject) and shiboken6.isValid(o) and shiboken6.ownedByPython(o) and _under(o, w)
                   for o in shiboken6.getAllValidWrappers())
    close_windows(app, [w])                      # a second time is harmless (an invalid window is skipped)


def test_the_table_models_belong_to_their_pages(tmp_path, monkeypatch):
    """A QObject the window makes has a Qt parent in the window, so Qt deletes it with the window on the GUI thread —
    the models were the only parentless ones, left to the garbage collector (which runs on any thread)."""
    from qt_support import close_windows
    monkeypatch.setenv("SPEC_SURGEON_NO_UPDATE_CHECK", "1")
    app, w = _window(tmp_path)
    try:
        for page in (w.preview_page, w.run_page):
            assert page.model.parent() is page and page.proxy.parent() is page
            assert page.proxy.sourceModel() is page.model and page.table.model() is page.proxy
    finally:
        close_windows(app, [w])


SCENARIO = textwrap.dedent("""
    import faulthandler, gc, sys, threading, weakref
    from pathlib import Path
    faulthandler.enable()
    from qt_support import close_windows
    from spec_surgeon.gui import Session
    from spec_surgeon.ui.app import build_legacy_window as build_window
    from spec_surgeon.ui.app import RecordingPrompts
    w = build_window(Session(out_root=Path(sys.argv[1])), prompts=RecordingPrompts())
    ui = w._spec_surgeon
    app = ui["app"]
    ui["wait"]()
    freed = []
    weakref.finalize(w, lambda: freed.append(threading.current_thread().name))
    close_windows(app, [w])
    del w, ui
    t = threading.Thread(target=gc.collect, name="worker")       # the collector on another thread, as in a batch
    t.start()
    t.join()
    for i in range(200000):                                       # the main thread runs Python: deferred deletions fire
        x = [i] * 3
    app.processEvents()
    print("SURVIVED; the window's Python object was freed on:", freed, flush=True)
""")


def test_a_window_whose_job_still_runs_is_kept_alive_not_deleted(tmp_path, monkeypatch):
    """A thread still emits through the window's Bridge: deleting the window would hand it an invalid wrapper
    (``RuntimeError: Signal source has been deleted`` in that thread). The helper waits, and at the deadline keeps the
    window alive — where no collector frees it — and says so; once the job is over, the window is deleted as usual."""
    import threading
    import shiboken6
    import qt_support
    from qt_support import close_windows
    monkeypatch.setenv("SPEC_SURGEON_NO_UPDATE_CHECK", "1")
    app, w = _window(tmp_path)
    gate = threading.Event()
    assert w._work("Something long", lambda log: gate.wait(30) and "finished")
    windows = [w]
    try:
        with pytest.raises(TimeoutError, match=r"still had a job.*kept alive"):
            close_windows(app, windows, timeout=0.3)
        assert windows == [] and shiboken6.isValid(w) and w in qt_support.KEPT_ALIVE and w.busy
    finally:
        gate.set()
        w._spec_surgeon["wait"]()
        qt_support.KEPT_ALIVE.remove(w)
    close_windows(app, [w])
    assert not shiboken6.isValid(w)


def test_a_destroyed_window_collected_on_another_thread_leaves_the_process_alive(tmp_path):
    """The crash's own scenario, in a subprocess: build a window, tear it down with ``close_windows``, then free its
    Python object on a worker thread and run Python on the main thread. With ``close()`` alone this dies with SIGSEGV
    three times in three (the probe of 2026-10-16); with the helper every wrapper is invalid before the collector sees
    it. A subprocess, so a regression is a failed test and not a dead pytest."""
    from qt_support import qt_app_or_skip
    qt_app_or_skip()                                   # skip (or fail, with SPEC_SURGEON_REQUIRE_QT) like every Qt test
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC_")}
    env.update({"QT_QPA_PLATFORM": "offscreen", "SPEC_SURGEON_CONFIG_DIR": str(tmp_path / "config"),
                "SPEC_SURGEON_NO_UPDATE_CHECK": "1", "PYTHONPATH": os.pathsep.join(
                    [str(TESTS)] + ([env_pp] if (env_pp := os.environ.get("PYTHONPATH")) else []))})
    r = subprocess.run([sys.executable, "-X", "faulthandler", "-c", SCENARIO, str(tmp_path / "out")],
                       capture_output=True, text=True, timeout=300, env=env)
    assert r.returncode == 0, f"exit {r.returncode}\n{r.stdout}\n{r.stderr}"
    assert "SURVIVED" in r.stdout, r.stdout + r.stderr


def test_every_window_test_tears_down_through_close_windows():
    """No test module builds a window without importing ``qt_support.close_windows``: a fixture that only closes its
    windows (or that calls ``deleteLater`` and ``processEvents``, which deletes nothing) is how the crash came back."""
    for py in sorted(TESTS.glob("test_*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        builds = any(isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", "")) == "build_window"
                     for n in ast.walk(tree))
        imports_helper = any(isinstance(n, ast.ImportFrom) and n.module == "qt_support"
                             and any(a.name == "close_windows" for a in n.names) for n in ast.walk(tree))
        assert not builds or imports_helper, f"{py.name} builds a window but never destroys it with close_windows"


def test_gc_collect_on_the_main_thread_after_a_destroyed_window_frees_only_invalid_wrappers(tmp_path, monkeypatch):
    """What the helper's final ``gc.collect()`` relies on: once the Qt objects are gone, the window's Python garbage
    holds no valid Qt object — so a later collection anywhere has nothing of Qt's to delete."""
    import shiboken6
    from PySide6.QtCore import QObject
    from qt_support import close_windows
    monkeypatch.setenv("SPEC_SURGEON_NO_UPDATE_CHECK", "1")
    app, w = _window(tmp_path)
    close_windows(app, [w])
    gc.set_debug(gc.DEBUG_SAVEALL)
    try:
        del w
        gc.collect()
        qt_garbage = [o for o in gc.garbage if isinstance(o, QObject)]
        assert qt_garbage and not any(shiboken6.isValid(o) for o in qt_garbage)
    finally:
        gc.set_debug(0)
        gc.garbage.clear()
