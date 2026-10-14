"""Background jobs for the window.

A job is ``fn(log) -> result`` run on a daemon ``threading.Thread`` (as the Tk window did: closing the window
abandons a preview, and a run that started still finishes its files — ``concurrent.futures`` joins its pool at
exit). Its log lines, result or error come back through a ``Bridge``'s signals. The Bridge is created on the GUI
thread, and its signals are connected only to ``@Slot`` methods of QObjects that live on the GUI thread, so Qt
queues every delivery onto the GUI thread. Never connect a worker signal to a lambda (a lambda has no thread, so
where it runs is not guaranteed), and never touch a widget from ``fn``: read every value before the job starts.
"""
from __future__ import annotations

import threading
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal


class Bridge(QObject):
    log = Signal(str)
    done = Signal(object)
    failed = Signal(str, str)          # one-line message, traceback


def start_job(bridge: Bridge, fn: Callable[[Callable[[str], None]], Any], name: str = "spec-surgeon-job") \
        -> threading.Thread:
    def target():
        try:
            result = fn(lambda m: bridge.log.emit(str(m)))
        except Exception as e:  # shown to the user, not swallowed
            bridge.failed.emit(f"{type(e).__name__}: {e}", traceback.format_exc())
        else:
            bridge.done.emit(result)
    t = threading.Thread(target=target, daemon=True, name=name)
    t.start()
    return t
