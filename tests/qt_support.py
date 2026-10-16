"""Qt for the window tests: offscreen (no display needed), skipped when PySide6 or a system library it needs is
missing — unless ``SPEC_SURGEON_REQUIRE_QT`` is set (CI sets it, so a broken Qt install fails instead of skipping).

``close_windows`` is how every Qt fixture tears its windows down: on the GUI thread, now. A window merely closed (or
``deleteLater``-ed without its deferred delete delivered) lives on until Python frees its wrapper — and a garbage
collection can run on ANY thread that allocates, e.g. the management thread of a batch's process pool in a later,
unrelated test; a Qt object destroyed there ("QObject::killTimer: Timers cannot be stopped from another thread") took
the whole test process down with a segmentation fault, in a test that depended on allocation timing (adding a test
module moved it). So: close, deliver the deferred deletes, and collect the Python cycles that hold the windows while
still on the GUI thread."""
from __future__ import annotations

import gc
import os

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


def close_windows(app, windows) -> None:
    """Close and destroy the test's windows on the GUI thread, before the next test (see the module docstring)."""
    from PySide6.QtCore import QEvent
    for w in windows:
        w.force_close = True
        w.close()
        w.deleteLater()
    windows.clear()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    gc.collect()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
