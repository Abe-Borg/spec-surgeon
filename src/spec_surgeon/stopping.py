"""Safe mode's kill switch: Stop ends the work that is running, not only an AI call.

Safe mode makes no model call, so the AI kill switch (``llm.cancellation_scope``) has nothing to cut there. Its work
is planning, making and checking the previews' samples, rendering pages, creating and checking copies and making clean
exports, and Stop ends it at once. The window's job enters ``scope`` with the same event Stop sets, and only for a
Safe-mode job (``web.server.WindowState.start_job``); in Default and Obliterate a document check or file operation
already running still finishes first, as there (docs/CONVERSATIONAL_DESKTOP.md, "Stop now").

* Between steps the engine calls ``check``: past a set event it raises ``Stopped``. A file in hand in this process
  finishes its step; nothing is half-written where the window reads.
* Where work runs elsewhere the engine waits through ``wait`` / ``as_completed`` / ``poll``, every ``POLL_SECONDS``,
  and ends it on a stop: worker processes are terminated (``batch.stop_pool``), a Word worker and its private Word
  instance are ended (``render.word_worker``), and LibreOffice's process tree is killed (``render.libreoffice``).
* ``Stopped`` is a ``BaseException``, like ``KeyboardInterrupt``: the engine's handlers that keep a batch going past
  one file's failure (``except Exception``) never take a stop for a failure, so it cannot be recorded as a refusal, a
  failed render or a failed check, and nothing after it runs. Every ``except BaseException`` in the engine re-raises.

A stop never delivers anything: copies reach the window only once a run has checked them all and returned
(``DocumentTask._collect_results``), and a clean export writes its files only once every one is made. What a stopped
job leaves in the scratch folder is never read as a result: the next run of that folder starts by clearing it
(``batch.clear_delivery_folders``). The coordinator says what a stop left (``DocumentTask._stopped``).

Outside a scope (Default and Obliterate jobs, tests, the engine called from Python) every function here is a no-op
or the plain wait it wraps.
"""
from __future__ import annotations

import concurrent.futures as cf
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterable, Iterator, Optional

POLL_SECONDS = 0.25          # how often work running elsewhere (worker processes, Word, LibreOffice) looks at Stop

_EVENT: ContextVar = ContextVar("spec_surgeon_stop_event", default=None)


class Stopped(BaseException):
    """The user pressed Stop during a Safe-mode job: the work ends here and nothing after it runs."""

    def __init__(self, where: str = ""):
        super().__init__(f"Stopped by the user{f' ({where})' if where else ''}.")
        self.where = where


@contextmanager
def scope(event):
    """Within this block a set ``event`` (a ``threading.Event``) stops the work; None arms nothing."""
    token = _EVENT.set(event)
    try:
        yield event
    finally:
        _EVENT.reset(token)


def armed() -> bool:
    """A stop can land here: a scope with an event is open in this context."""
    return _EVENT.get() is not None


def requested() -> bool:
    event = _EVENT.get()
    return event is not None and event.is_set()


def check(where: str = "", on_stop: Optional[Callable[[], None]] = None) -> None:
    """Raise ``Stopped`` once Stop was pressed, after ``on_stop`` (ending what runs elsewhere) has run."""
    if requested():
        if on_stop is not None:
            on_stop()
        raise Stopped(where)


def wait(future: cf.Future, where: str = "", on_stop: Optional[Callable[[], None]] = None) -> cf.Future:
    """Wait for ``future`` to finish, looking at Stop every ``POLL_SECONDS``; returns it (its result is the caller's to
    read). Outside a scope it returns the future at once: its ``result()`` waits, as it did."""
    if not armed():
        return future
    while True:
        check(where, on_stop)
        done, _ = cf.wait([future], timeout=POLL_SECONDS)
        if done:
            return future


def as_completed(futures: Iterable[cf.Future], where: str = "",
                 on_stop: Optional[Callable[[], None]] = None) -> Iterator[cf.Future]:
    """``concurrent.futures.as_completed``, looking at Stop every ``POLL_SECONDS`` while it waits."""
    pending = set(futures)
    if not armed():
        yield from cf.as_completed(pending)
        return
    while pending:
        check(where, on_stop)
        done, pending = cf.wait(pending, timeout=POLL_SECONDS, return_when=cf.FIRST_COMPLETED)
        yield from done


def poll(seconds: float) -> float:
    """How long a wait on work running elsewhere may block before it looks at Stop again: ``seconds``, or at most
    ``POLL_SECONDS`` inside a scope."""
    return min(seconds, POLL_SECONDS) if armed() else seconds
