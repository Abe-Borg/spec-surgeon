"""Qt for the window tests: offscreen (no display needed), skipped when PySide6 or a system library it needs is
missing — unless ``SPEC_SURGEON_REQUIRE_QT`` is set (CI sets it, so a broken Qt install fails instead of skipping).

``close_windows`` is how every Qt fixture tears its windows down: on the GUI thread, now. A window merely closed (or
``deleteLater``-ed without its deferred delete delivered) lives on until Python frees its wrapper — and a garbage
collection can run on ANY thread that allocates, e.g. the management thread of a batch's process pool in a later,
unrelated test; a window destroyed there took the whole test process down with a segmentation fault, in a test that
depended on allocation timing (``docs/audits/2026-10-16_qt_teardown_segfault.md`` has the mechanism: PySide6 6.12
deletes a Python subclass of a Qt class on that thread at once and defers the plain child widgets it still books as
Python-owned to the main thread, where that second delete runs on memory Qt already freed). So: close, deliver the
deferred deletes, and collect the Python cycles that hold the windows while still on the GUI thread — and never under a
thread that still uses the window (see ``close_windows``)."""
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


KEPT_ALIVE: list = []      # windows a thread was still using at the deadline: never deleted, never collected


def _idle(w) -> bool:
    """No job, update check or download thread of the window is running (the flags the window's own ``wait`` uses)."""
    u = getattr(w, "updates", None)
    return not (getattr(w, "busy", False)
                or (u is not None and (getattr(u, "checking", False) or getattr(u, "downloading", False))))


def close_windows(app, windows, timeout: float = 30.0) -> None:
    """Close every window in ``windows``, delete its Qt object on this (the GUI) thread, empty the list, and free the
    Python garbage this leaves on this thread too — before the next test (see the module docstring).

    Why not just ``close()``: a closed window is still a Python-owned Qt object, freed by the cyclic garbage collector
    whenever — and on whichever thread — a collection happens to run; in ``test_editspec.py``'s 24-file batches that is
    the process pool's manager thread. PySide6 6.12 then deletes a Python subclass of a Qt class (``MainWindow``) right
    there, on a thread Qt never allowed to delete widgets from, and defers the plain Qt widgets Shiboken still books as
    Python-owned (the ``QLabel``/``QPushButton``/``QCheckBox`` placed through layouts) to the main thread — where that
    second ``delete`` runs after Qt has already deleted them with their parent: the segmentation fault in
    ``Shiboken::BindingManager::runDeletionInMainThread`` of 2026-10-16, in whatever test ran next. Deleted here, while
    the test still holds them, the Qt objects go in the right order on the right thread, and every wrapper is invalid
    before any collector sees it. A window whose job, update check or download thread is still running is given
    ``timeout`` seconds to finish first; one still running at the deadline is NOT deleted (its thread still emits
    through the window's ``Bridge`` and ``UpdateController`` — a deleted one would raise ``RuntimeError: Signal source
    has been deleted`` in that thread): it is kept alive in ``KEPT_ALIVE``, where no collector ever frees it, and a
    ``TimeoutError`` names it. The ``gc.collect()`` frees any other Qt object the test made and dropped (a dialog, a
    view) here as well, instead of on a job thread or a pool thread later; the deferred deletes it may post are
    delivered too.
    """
    import shiboken6
    deadline = time.monotonic() + timeout
    still_busy = []
    for w in list(windows):
        if not shiboken6.isValid(w):
            continue
        while not _idle(w) and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        if not _idle(w):
            still_busy.append(w)
            KEPT_ALIVE.append(w)
            continue
        w.force_close = True
        w.close()
        w.deleteLater()
    flush_deferred_deletes(app)
    windows.clear()
    gc.collect()
    flush_deferred_deletes(app)
    if still_busy:
        raise TimeoutError(f"close_windows: {len(still_busy)} window(s) still had a job, update check or download "
                           f"running after {timeout:g} s — kept alive (qt_support.KEPT_ALIVE), not deleted, because a "
                           "thread was still using their Qt objects")
