"""A per-document process boundary for Word COM, with private-instance-only cleanup.

No process-name or process-tree kill is used. A Windows process handle is retained
only after DispatchEx reports a newly created Word PID and its creation time matches.
If ownership cannot be established, timeout still stops our Python worker; Word is
left alone and the missing cleanup evidence is reported.
"""
from __future__ import annotations

import json
import math
import multiprocessing
import os
import shutil
import tempfile
import time
from pathlib import Path

from .. import diagnostics as diag

DEFAULT_TIMEOUT = 120.0
MAX_RESULT_BYTES = 64 * 1024 * 1024


class WordWorkerError(RuntimeError):
    def __init__(self, code, reason, details=None):
        super().__init__(reason)
        self.code = code
        self.details = details or {}


def _timeout(value):
    seconds = float(value if value is not None else os.environ.get("SPEC_SURGEON_WORD_TIMEOUT_SECONDS", DEFAULT_TIMEOUT))
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("Word timeout must be a positive finite number of seconds")
    return seconds


def _private_identity(app, preceding):
    """Only identify a process absent before DispatchEx; never adopt an existing Word PID."""
    import win32api
    import win32process
    pid = win32process.GetWindowThreadProcessId(app.Hwnd)[1]
    if pid in preceding:
        return None
    handle = win32api.OpenProcess(0x0400, False, pid)  # PROCESS_QUERY_INFORMATION
    try:
        created = win32process.GetProcessTimes(handle)["CreationTime"].isoformat()
        return {"pid": pid, "created": created}
    finally:
        handle.Close()


def _open_private_handle(identity):
    """Pin the same OS process object, so PID reuse can never redirect later cleanup."""
    import win32api
    import win32process
    handle = win32api.OpenProcess(0x00100000 | 0x0400 | 0x0001, False, identity["pid"])
    try:
        if win32process.GetProcessTimes(handle)["CreationTime"].isoformat() != identity["created"]:
            handle.Close()
            return None
        return handle
    except BaseException:
        handle.Close()
        raise


def _close_private_handle(handle, *, terminate):
    if handle is None:
        return
    try:
        if terminate:
            import win32event
            import win32process
            if win32event.WaitForSingleObject(handle, 0) == 258:  # WAIT_TIMEOUT: still running
                win32process.TerminateProcess(handle, 1)
                win32event.WaitForSingleObject(handle, 2000)
    finally:
        handle.Close()


def _worker(connection, request):
    """Spawn target: send only bounded control messages; bulk text goes to a result file."""
    from .word_com import WordSession, com_error_text
    scratch = Path(request["scratch"])
    journal = scratch / "word.events.jsonl"
    session = None
    result = None
    with diag.scope(journal, **request["context"]):
        try:
            import win32process
            preceding = set(win32process.EnumProcesses())

            def started(app):
                try:
                    identity = _private_identity(app, preceding)
                    connection.send({"kind": "started", "identity": identity})
                except Exception as exc:
                    connection.send({"kind": "cleanup_unavailable", "reason": str(exc)[:1000]})

            session = WordSession(scratch=scratch, on_started=started)
            with diag.com_quiet(), session as ws:
                try:
                    version = f"{ws.app.Version} (build {ws.app.Build})"
                except Exception:
                    version = "(version unavailable)"
                if request["action"] == "render":
                    ws.render_pdf(Path(request["source"]), scratch / "render.pdf")
                    result = {"ok": True, "version": version}
                elif request["action"] == "accept_reject":
                    accepted, rejected = ws.accept_reject_text(Path(request["source"]))
                    result = {"ok": True, "version": version, "accept": accepted, "reject": rejected}
                else:
                    raise ValueError("unknown Word worker action")
        except BaseException as exc:
            result = {"ok": False, "code": "word-com", "reason": (com_error_text(exc) or f"{type(exc).__name__}: {exc}")[:8000],
                      "stage": getattr(session, "failure_stage", session.stage) if session else "word_start"}
        try:
            payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
            if len(payload) > MAX_RESULT_BYTES:
                payload = json.dumps({"ok": False, "code": "word-result-limit",
                                      "reason": "Word oracle result exceeded its output allowance"}).encode()
            (scratch / "result.json").write_bytes(payload)
            connection.send({"kind": "finished"})
        finally:
            connection.close()


def run_word(action, source, *, pdf=None, timeout=None):
    """Run through startup, COM work and Quit within one deadline; publish only success."""
    seconds = _timeout(timeout)
    source = Path(source).resolve()
    context = multiprocessing.get_context("spawn")
    with tempfile.TemporaryDirectory(prefix="specsurgeon-word-", ignore_cleanup_errors=True) as directory:
        scratch = Path(directory)
        receive, send = context.Pipe(duplex=False)
        request = {"action": action, "source": str(source), "scratch": str(scratch), "context": diag.context()}
        process = context.Process(target=_worker, args=(send, request), name="SpecSurgeon-Word")
        owned_handle = None
        failed = True
        started = False
        deadline = time.monotonic() + seconds
        try:
            process.start()
            started = True
            send.close()
            finished = False
            while not finished:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise WordWorkerError("word-timeout", f"Word exceeded {seconds:g} seconds for {source.name}",
                                          {"timeout_seconds": seconds, "private_cleanup_available": owned_handle is not None})
                if receive.poll(min(remaining, 0.25)):
                    try:
                        message = receive.recv()
                    except EOFError as exc:
                        raise WordWorkerError("word-worker-died", "Word worker stopped without a completed result") from exc
                    if message.get("kind") == "finished":
                        finished = True
                    elif message.get("kind") == "started":
                        if message.get("identity"):
                            try:
                                owned_handle = _open_private_handle(message["identity"])
                            except Exception as exc:
                                diag.event("render.word_cleanup_unavailable", level="warning", reason=str(exc))
                        else:
                            diag.event("render.word_cleanup_unavailable", level="warning",
                                       reason="DispatchEx process ownership could not be proved")
                    elif message.get("kind") == "cleanup_unavailable":
                        diag.event("render.word_cleanup_unavailable", level="warning", reason=message.get("reason"))
                elif not process.is_alive():
                    raise WordWorkerError("word-worker-died", "Word worker stopped without a completed result",
                                          {"exitcode": process.exitcode})
            process.join(max(0, deadline - time.monotonic()))
            if process.is_alive():
                raise WordWorkerError("word-timeout", "Word worker did not exit before its deadline",
                                      {"timeout_seconds": seconds})
            if process.exitcode != 0:
                raise WordWorkerError("word-worker-died", "Word worker exited unsuccessfully", {"exitcode": process.exitcode})
            with (scratch / "result.json").open("rb") as stream:
                payload = stream.read(MAX_RESULT_BYTES + 1)
            if len(payload) > MAX_RESULT_BYTES:
                raise WordWorkerError("word-result-limit", "Word worker result exceeded its output allowance")
            result = json.loads(payload)
            if not result.get("ok"):
                raise WordWorkerError(result.get("code", "word-com"), result.get("reason", "Word failed"), result)
            if action == "render":
                rendered = scratch / "render.pdf"
                if not rendered.is_file() or not rendered.stat().st_size:
                    raise WordWorkerError("word-no-pdf", "Word reported no error but wrote no PDF")
                # Output is published only after Word has closed successfully and the worker exited.
                destination = Path(pdf)
                fd, staged_name = tempfile.mkstemp(prefix=".specsurgeon-render-", suffix=".pdf", dir=destination.parent)
                os.close(fd)
                staged = Path(staged_name)
                try:
                    shutil.copyfile(rendered, staged)
                    os.replace(staged, destination)
                finally:
                    staged.unlink(missing_ok=True)
            failed = False
            return result
        finally:
            send.close()
            receive.close()
            if started:
                if process.is_alive():
                    process.terminate()
                    process.join(2)
                    if process.is_alive():
                        process.kill()
                        process.join(2)
                process.close()
            try:
                _close_private_handle(owned_handle, terminate=failed)
            except Exception as exc:
                diag.event("render.word_cleanup_failed", level="warning", reason=str(exc))
            journal = scratch / "word.events.jsonl"
            if journal.exists():
                # Keep the worker's correlated COM checkpoints after its scratch folder is removed.
                for line in journal.read_text(encoding="utf-8").splitlines():
                    try:
                        item = json.loads(line)
                        diag.event("render.word_worker_event", worker_event=item)
                    except (ValueError, OSError):
                        pass
