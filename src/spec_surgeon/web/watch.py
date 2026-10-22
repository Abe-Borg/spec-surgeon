"""The window's watch: what the page says about itself, and a watchdog over the page and the running job.

The page (``frontend/src/lib/diagnostics.ts``) posts reports to ``/api/diagnostics``: its loads and unloads, its
visibility, a heartbeat every ``PAGE_BEAT_S`` seconds with the long tasks that blocked it since the last one, every
error it did not catch, a drawing that failed (the error boundary), and a job stream that ended before its ``done``.
Each becomes a ``page.*`` event in the application journal; the ones a person reading ``spec-surgeon.log`` needs
(a load, an error, a stall, a frozen page) also become a line there.

The watchdog (``tick``, every ``TICK_S`` seconds on its own thread) says when something stops moving:

* the page, showing, has sent nothing for ``PAGE_SILENT_S`` seconds: it is frozen (or the server stopped answering
  it) — ``page.unresponsive``, with where every thread of the app is, and ``page.responsive`` when it answers again;
* a job has shown no progress (no event, no log line, no cost) for ``JOB_QUIET_S`` seconds, then every
  ``JOB_QUIET_AGAIN_S`` — ``web.job.quiet`` with every thread's stack, so a hung model call or Word shows where;
* a job is still running ``STOP_GRACE_S`` seconds after Stop — ``web.job.stop_overdue``, with the stacks.

A tick that comes far later than it should (the computer slept) restarts the clocks instead of reporting a stall.
Nothing here changes what the window or a job does: it only writes diagnostics.
"""
from __future__ import annotations

import threading
import time
from contextvars import copy_context
from typing import Any, Callable, Dict, Optional

from .. import diagnostics as diag

PAGE_BEAT_S = 10            # the page's heartbeat (frontend/src/lib/diagnostics.ts BEAT_MS says the same)
PAGE_SILENT_S = 45          # a showing page silent this long is frozen
JOB_QUIET_S = 300           # a job with no progress this long gets its stacks dumped …
JOB_QUIET_AGAIN_S = 900     # … and again this often while it stays quiet
STOP_GRACE_S = 30           # Stop not obeyed after this long
SLOW_TASK_MS = 2000         # a long task this long froze the page visibly
TICK_S = 5
SLEPT_S = 30                # a tick this late: the process did not run (sleep, suspend)
MAX_REPORTS = 25            # per request
MAX_ERROR_LINES = 100       # page errors written to the readable log per session; the journal keeps every one
TEXT_CAP = 2000
STACK_CAP = 8000

REPORT_KINDS = ("loaded", "unload", "visibility", "beat", "error", "rejection", "render", "stream")


def _text(value: Any, cap: int = TEXT_CAP) -> str:
    return diag.snip(value, cap) if isinstance(value, (str, int, float)) else ""


def _number(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _seconds(s: float) -> str:
    s = max(0.0, s)
    if s < 120:
        return f"{s:.0f} s"
    m, sec = divmod(int(s), 60)
    return f"{m} min {sec} s" if m < 60 else f"{m // 60} h {m % 60} min"


def _stack_lines(stack: str, n: int = 8) -> str:
    lines = [line.rstrip() for line in str(stack or "").splitlines() if line.strip()]
    return "\n".join("    " + line.strip() for line in lines[:n])


class WindowWatch:
    """The page's reports and the watchdog. ``job_status()`` answers None when no job runs, else
    ``{"action", "id", "started", "last_progress", "stop_requested"}`` (monotonic seconds; ``stop_requested`` None
    until Stop). ``clock`` is ``time.monotonic``; tests pass their own and call ``tick`` directly."""

    def __init__(self, job_status: Callable[[], Optional[Dict[str, Any]]] = lambda: None,
                 clock: Callable[[], float] = time.monotonic):
        self.job_status = job_status
        self.clock = clock
        self.lock = threading.Lock()
        self.seen = False               # a page is loaded (from its "loaded" to its "unload")
        self.visible = False
        self.last_heard = 0.0
        self.silent = False             # page.unresponsive said, page.responsive not yet
        self.silent_since = 0.0
        self.loads = 0
        self.error_lines = 0
        self.last_tick = 0.0
        self.resumed_at = 0.0
        self._job_id: Any = None
        self._next_quiet = 0.0
        self._stop_said = False
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ---- the page's reports ----
    def page_reports(self, reports: Any) -> int:
        """Take a batch of the page's reports (``{"kind", …}`` each); returns how many were taken. Anything it does
        not know is passed over: the page is ours, but a report never stops the window."""
        if not isinstance(reports, list):
            return 0
        taken = 0
        for report in reports[:MAX_REPORTS]:
            if isinstance(report, dict) and report.get("kind") in REPORT_KINDS:
                try:
                    self._report(report)
                    taken += 1
                except Exception as exc:  # noqa: BLE001 — a diagnostic never fails the page
                    diag.event("page.report_failed", level="warning", kind=str(report.get("kind")),
                               **diag.error_fields(exc))
        return taken

    def _report(self, r: Dict[str, Any]) -> None:
        kind = r["kind"]
        now = self.clock()
        with self.lock:
            answered_after = now - self.silent_since if self.silent else None
            self.last_heard = now
            self.silent = False
            if "visible" in r:
                self.visible = bool(r.get("visible"))
            if kind == "loaded":
                self.seen, self.loads = True, self.loads + 1
            elif kind == "unload":
                self.seen = False
            loads = self.loads
        if answered_after is not None:
            diag.event("page.responsive", level="warning", silent_s=round(answered_after, 1))
            diag.app_log().warning(f"The window's page is answering again, after {_seconds(answered_after)} silent")
        if kind == "loaded":
            ua = _text(r.get("user_agent"), 400)
            size = _text(r.get("viewport"), 40)
            motion = "reduced motion" if r.get("reduced_motion") else "full motion"
            diag.event("page.loaded", load=loads, user_agent=ua, viewport=size, reduced_motion=bool(r.get("reduced_motion")),
                       visible=self.visible, page_version=_text(r.get("version"), 40))
            diag.app_log().info(f"Window page loaded (load {loads} this session; {size or 'size unknown'}; {motion}): "
                                f"{ua or 'no user agent'}")
        elif kind == "unload":
            diag.event("page.unload", load=loads)
            diag.app_log().info("Window page unloading (closed or reloaded)")
        elif kind == "visibility":
            diag.event("page.visibility", visible=self.visible)
        elif kind == "beat":
            tasks = r.get("long_tasks") if isinstance(r.get("long_tasks"), dict) else {}
            worst = _number(tasks.get("max_ms")) or 0.0
            if worst >= SLOW_TASK_MS:
                count = int(_number(tasks.get("count")) or 0)
                total = _number(tasks.get("total_ms")) or 0.0
                diag.event("page.slow", level="warning", max_ms=round(worst), total_ms=round(total), count=count)
                diag.app_log().warning(f"The window's page was blocked for {worst / 1000:.1f} s at once "
                                       f"({count} long task{'' if count == 1 else 's'}, {total / 1000:.1f} s in all, "
                                       f"since its last heartbeat)")
        elif kind in ("error", "rejection", "render"):
            self._page_error(kind, r)
        elif kind == "stream":
            action = _text(r.get("action"), 40)
            reason = _text(r.get("reason"), 40)
            message = _text(r.get("message"))
            diag.event("page.stream_ended_early", level="warning", action=action, reason=reason, message=message)
            diag.app_log().warning(f"The window lost the stream of job {action or '?'} before it ended ({reason}"
                                   + (f": {message}" if message else "") + ")")

    def _page_error(self, kind: str, r: Dict[str, Any]) -> None:
        message = _text(r.get("message")) or "(no message)"
        stack = _text(r.get("stack"), STACK_CAP)
        where = ""
        if r.get("source"):
            where = f"{_text(r.get('source'), 300)}:{int(_number(r.get('line')) or 0)}:{int(_number(r.get('column')) or 0)}"
        count = int(_number(r.get("count")) or 1)
        fields = {"message": message, "stack": stack, "where": where, "count": count}
        if kind == "render":
            fields["component_stack"] = _text(r.get("component_stack"), STACK_CAP)
        name = {"error": "page.error", "rejection": "page.unhandled_rejection", "render": "page.render_failed"}[kind]
        diag.event(name, level="error", **fields)
        with self.lock:
            self.error_lines += 1
            n = self.error_lines
        if n > MAX_ERROR_LINES:
            return
        title = {"error": "Window page error", "rejection": "Window page error (a promise nobody caught)",
                 "render": "The window stopped drawing (its error screen is showing)"}[kind]
        text = f"{title}: {message}" + (f" at {where}" if where else "") + (f" ({count}× since the last report)"
                                                                             if count > 1 else "")
        detail = _stack_lines(stack)
        if kind == "render" and fields.get("component_stack"):
            detail += ("\n" if detail else "") + "    in the window's parts:\n" + _stack_lines(fields["component_stack"], 6)
        if n == MAX_ERROR_LINES:
            detail += ("\n" if detail else "") + "    (further page errors this session go to the event journal only)"
        diag.app_log().error(text + ("\n" + detail if detail else ""))

    # ---- the watchdog ----
    def tick(self, now: Optional[float] = None) -> None:
        now = self.clock() if now is None else now
        with self.lock:
            gap = now - self.last_tick if self.last_tick else 0.0
            late = gap > SLEPT_S
            if late:
                self.resumed_at = now
            self.last_tick = now
        if late:
            diag.event("window.watch.resumed", gap_s=round(gap, 1))
            return
        self._check_page(now)
        self._check_job(now)

    def _check_page(self, now: float) -> None:
        with self.lock:
            heard = max(self.last_heard, self.resumed_at)
            due = self.seen and self.visible and not self.silent and now - heard >= PAGE_SILENT_S
            if due:
                self.silent, self.silent_since = True, heard
        if not due:
            return
        job = self.job_status()
        doing = f"job {job['action']} running" if job else "no job running"
        diag.event("page.unresponsive", level="warning", silent_s=round(now - heard, 1),
                   job=job["action"] if job else None)
        diag.app_log().warning(f"The window's page, showing, has sent nothing for {_seconds(now - heard)} ({doing}): "
                               "it is frozen, or the app stopped answering it. If the window looked stuck, it started "
                               "here. Where every thread of the app is:\n" + diag.thread_stacks())

    def _check_job(self, now: float) -> None:
        job = self.job_status()
        if not job:
            self._job_id = None
            return
        if job.get("id") != self._job_id:
            self._job_id, self._next_quiet, self._stop_said = job.get("id"), JOB_QUIET_S, False
        action = job.get("action") or "?"
        running = now - float(job.get("started") or now)
        quiet = now - max(float(job.get("last_progress") or now), self.resumed_at)
        if quiet >= self._next_quiet:
            self._next_quiet = quiet + JOB_QUIET_AGAIN_S
            diag.event("web.job.quiet", level="warning", action=action, quiet_s=round(quiet, 1),
                       running_s=round(running, 1))
            diag.app_log().warning(f"Job {action} has shown no progress for {_seconds(quiet)} (running "
                                   f"{_seconds(running)}). Where every thread of the app is:\n" + diag.thread_stacks())
        stop = job.get("stop_requested")
        if stop is not None and not self._stop_said and now - float(stop) >= STOP_GRACE_S:
            self._stop_said = True
            diag.event("web.job.stop_overdue", level="warning", action=action, since_stop_s=round(now - float(stop), 1))
            diag.app_log().warning(f"Job {action} is still running {_seconds(now - float(stop))} after Stop. Where "
                                   "every thread of the app is:\n" + diag.thread_stacks())

    def start(self, interval: float = TICK_S) -> None:
        """Run ``tick`` every ``interval`` seconds on a daemon thread, in this context's diagnostics scope."""
        if self._thread is not None:
            return
        self._stop.clear()
        context = copy_context()

        def loop() -> None:
            failed = False
            while not self._stop.wait(interval):
                try:
                    self.tick()
                except Exception as exc:  # noqa: BLE001 — the watch never takes the window down
                    if not failed:
                        failed = True
                        diag.event("window.watch.failed", level="warning", **diag.error_fields(exc))
        self._thread = threading.Thread(target=context.run, args=(loop,), daemon=True, name="spec-surgeon-watch")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(2)

