"""Qt for the window tests: offscreen (no display needed), skipped when PySide6 or a system library it needs is
missing — unless ``SPEC_SURGEON_REQUIRE_QT`` is set (CI sets it, so a broken Qt install fails instead of skipping).

``destroy_windows`` is how a test lets go of the windows it built (every ``qt`` fixture's teardown goes through it):
their Qt objects are deleted right then, on the main thread, and never left to the garbage collector — see its
docstring for the segmentation fault that taught us this (2026-10-16)."""
from __future__ import annotations

import gc
import os
import time

import pytest


def qt_app_or_skip():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as e:      # PySide6 missing, or e.g. libEGL missing (an ImportError, not ModuleNotFoundError)
        if os.environ.get("SPEC_SURGEON_REQUIRE_QT"):
            raise
        pytest.skip(f"Qt not available: {e}")
    return QApplication.instance() or QApplication([])


def flush_deferred_deletes(app) -> None:
    """Deliver every pending ``deleteLater()`` now. ``processEvents()`` alone never does that outside a running event
    loop: Qt holds a DeferredDelete event back until control returns to the event loop that posted it, and the tests
    run none — so ``w.deleteLater(); app.processEvents()`` only moved a window's ownership to C++ and leaked it."""
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def destroy_windows(app, windows, timeout: float = 30.0) -> None:
    """Close every window in ``windows``, delete its Qt object on this (the main) thread, empty the list, and free the
    Python garbage this leaves on this thread too.

    Why not just ``close()``: a closed window is still a Python-owned Qt object, freed by the cyclic garbage collector
    whenever — and on whichever thread — a collection happens to run; in ``test_editspec.py``'s 24-file batches that is
    the process pool's manager thread. PySide6 6.12 then deletes a Python subclass of a Qt class (``MainWindow``) right
    there, on a thread Qt never allowed to delete widgets from, and defers the plain Qt widgets Shiboken still books as
    Python-owned (the ``QLabel``/``QPushButton``/``QCheckBox`` placed through layouts) to the main thread — where that
    second ``delete`` runs after Qt has already deleted them with their parent: the segmentation fault in
    ``Shiboken::BindingManager::runDeletionInMainThread`` of 2026-10-16, in whatever test ran next. Deleted here, while
    the test still holds them, the Qt objects go in the right order on the right thread, and every wrapper is invalid
    before any collector sees it. A window whose job is still running is given ``timeout`` seconds to finish first.
    The ``gc.collect()`` at the end frees any other Qt object the test made and dropped (a dialog, a view) here as well,
    instead of on a job thread or a pool thread later.
    """
    import shiboken6
    deadline = time.monotonic() + timeout
    for w in list(windows):
        if not shiboken6.isValid(w):
            continue
        while getattr(w, "busy", False) and time.monotonic() < deadline:    # a job still running: let it finish
            app.processEvents()
            time.sleep(0.02)
        w.force_close = True
        w.close()
        w.deleteLater()
    flush_deferred_deletes(app)
    windows.clear()
    gc.collect()
