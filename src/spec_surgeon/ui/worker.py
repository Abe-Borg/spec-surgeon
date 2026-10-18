"""Background jobs for the window.

A job is ``fn(log) -> result`` run on a non-daemon ``threading.Thread``. The window stays open while work is active,
and process shutdown never abandons rendering, delivery or reports after the transform workers finish.
Its log lines, result or error come back through a ``Bridge``'s signals. The Bridge is created on the GUI
thread, and its signals are connected only to ``@Slot`` methods of QObjects that live on the GUI thread, so Qt
queues every delivery onto the GUI thread. Never connect a worker signal to a lambda (a lambda has no thread, so
where it runs is not guaranteed), and never touch a widget from ``fn``: read every value before the job starts.
"""
from __future__ import annotations

import threading
import time
import traceback
from contextvars import copy_context
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from .. import diagnostics as diag


class Bridge(QObject):
    log = Signal(str)
    done = Signal(object)
    failed = Signal(str, str)          # one-line message, traceback


def start_job(bridge: Bridge, fn: Callable[[Callable[[str], None]], Any], name: str = "spec-surgeon-job") \
        -> threading.Thread:
    context = copy_context()                     # preserve the UI action's session/span across the thread boundary
    queued = time.perf_counter()

    def target():
        try:
            with diag.span("ui.worker", worker_name=name, queue_ms=round((time.perf_counter() - queued) * 1000, 3)):
                result = fn(lambda m: bridge.log.emit(str(m)))
        except Exception as e:  # shown to the user, not swallowed
            bridge.failed.emit(f"{type(e).__name__}: {e}", traceback.format_exc())
        else:
            bridge.done.emit(result)
    t = threading.Thread(target=context.run, args=(target,), daemon=False, name=name)
    t.start()
    return t
