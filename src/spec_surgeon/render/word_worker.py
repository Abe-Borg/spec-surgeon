"""A process boundary for Word COM, with private-instance-only cleanup.

``run_word`` runs one action (the accept/reject oracle) in one spawned worker with one deadline;
``run_word_batch`` renders a whole batch in one spawned worker that starts ONE private Word instance and
renders every file in turn, the parent holding a deadline per file; ``run_word_batches`` splits a batch over up to
``word_instances`` such workers that render side by side, each with its own private Word. No process-name or process-tree kill
is used. A Windows process handle is retained only after DispatchEx yields a newly created Word PID (the
instance's own main window, found by a caption only it carries, or else the one Word process started since) and
its creation time matches. If ownership cannot be established, a timeout still stops our Python worker;
Word is left alone and the missing cleanup evidence is reported.

Stop (Safe mode's kill switch, ``stopping``): the parent looks at it on every poll, ends its worker and terminates
the private Word it owns (the same private-instance cleanup a timeout takes), publishes nothing more and raises
``stopping.Stopped``.
"""
from __future__ import annotations

import contextvars
import json
import math
import multiprocessing
import ntpath
import os
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Union

from .. import diagnostics as diag
from .. import stopping

DEFAULT_TIMEOUT = 120.0
MAX_RESULT_BYTES = 64 * 1024 * 1024
POLL_SECONDS = 0.25
# Private Word instances that render one batch side by side (``run_word_batches``): at most WORD_INSTANCES (the
# environment variable SPEC_SURGEON_WORD_INSTANCES replaces it; 1 renders every file in one instance, one by one),
# at most one per two cores, and one more only for every FILES_PER_INSTANCE files, since starting Word takes seconds.
WORD_INSTANCES = 3
WORD_INSTANCES_ENV = "SPEC_SURGEON_WORD_INSTANCES"
FILES_PER_INSTANCE = 2
# HRESULTs that say the Word process itself is gone (crashed, killed) rather than that a file was refused: every
# later file would fail the same way, so the worker stops and the parent renders the rest in a fresh instance.
INSTANCE_LOST_HRESULTS = frozenset({
    0x800706BA,  # RPC_S_SERVER_UNAVAILABLE
    0x800706BE,  # RPC_S_CALL_FAILED
    0x80010108,  # RPC_E_DISCONNECTED
    0x80010105,  # RPC_E_SERVERFAULT
})


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


WORD_WINDOW_CLASS = "OpusApp"    # the class of Word's main window, hidden or not


def _caption_pid(app):
    """The PID behind this instance's own main window. Word's Application has no ``Hwnd`` (Excel's has; asking Word
    raises ``AttributeError: Word.Application.Hwnd``, which every worker of the 1.8.0 live run did), so the window is
    found as Microsoft documents for Word: give the instance a caption no other window has, find the ``OpusApp``
    window with that caption, put the caption back. None when no such window exists."""
    import uuid
    import win32gui
    import win32process
    original = app.Caption
    marker = f"Spec Surgeon {uuid.uuid4().hex}"
    app.Caption = marker
    try:
        hwnd = win32gui.FindWindow(WORD_WINDOW_CLASS, marker)
    finally:
        app.Caption = original
    return win32process.GetWindowThreadProcessId(hwnd)[1] if hwnd else None


def _new_word_pid(preceding):
    """The one WINWORD.EXE that was not running before DispatchEx, or None when there is none or more than one (the
    user started Word at the same moment: neither is adopted)."""
    import win32api
    import win32process
    found = []
    for pid in set(win32process.EnumProcesses()) - set(preceding):
        try:
            handle = win32api.OpenProcess(0x0410, False, pid)   # PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
        except Exception:
            continue                                           # gone already, or not ours to read
        try:
            name = ntpath.basename(str(win32process.GetModuleFileNameEx(handle, None)))
        except Exception:
            name = ""
        finally:
            handle.Close()
        if name.lower() == "winword.exe":
            found.append(pid)
    return found[0] if len(found) == 1 else None


def _private_identity(app, preceding):
    """Only identify a process absent before DispatchEx; never adopt an existing Word PID. The instance's own window
    names it; failing that, the single Word process that started since. None when neither proves which it is."""
    import win32api
    import win32process
    try:
        pid, found_by = _caption_pid(app), "window"
    except Exception:
        pid = None
    if pid is None:
        pid, found_by = _new_word_pid(preceding), "new process"
    if pid is None or pid in preceding:
        return None
    handle = win32api.OpenProcess(0x0400, False, pid)  # PROCESS_QUERY_INFORMATION
    try:
        created = win32process.GetProcessTimes(handle)["CreationTime"].isoformat()
        return {"pid": pid, "created": created, "found_by": found_by}
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


def _publish(rendered: Path, destination: Path) -> Path:
    """Copy a worker's finished PDF to where the caller asked for it, atomically (staged beside it, then replaced):
    a reader never sees a half-copied PDF. A missing or empty render is ``word-no-pdf``."""
    if not rendered.is_file() or not rendered.stat().st_size:
        raise WordWorkerError("word-no-pdf", "Word reported no error but wrote no PDF")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, staged_name = tempfile.mkstemp(prefix=".specsurgeon-render-", suffix=".pdf", dir=destination.parent)
    os.close(fd)
    staged = Path(staged_name)
    try:
        shutil.copyfile(rendered, staged)
        os.replace(staged, destination)
    finally:
        staged.unlink(missing_ok=True)
    return destination


def _replay_journal(scratch: Path) -> None:
    """Keep the worker's correlated COM checkpoints after its scratch folder is removed."""
    journal = scratch / "word.events.jsonl"
    if not journal.exists():
        return
    for line in journal.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
            diag.event("render.word_worker_event", worker_event=item)
        except (ValueError, OSError):
            pass


def _end_process(process) -> None:
    """Stop our own worker process (never a Word process by name): terminate, then kill, then release it."""
    if process.is_alive():
        process.terminate()
        process.join(2)
        if process.is_alive():
            process.kill()
            process.join(2)
    process.close()


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
            with diag.com_quiet():
                process.start()
            started = True
            send.close()
            finished = False
            while not finished:
                stopping.check("Word")      # the finally below ends the worker and terminates its private Word
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
                # Output is published only after Word has closed successfully and the worker exited.
                _publish(scratch / "render.pdf", Path(pdf))
            failed = False
            return result
        finally:
            send.close()
            receive.close()
            if started:
                _end_process(process)
            try:
                _close_private_handle(owned_handle, terminate=failed)
            except Exception as exc:
                diag.event("render.word_cleanup_failed", level="warning", reason=str(exc))
            _replay_journal(scratch)


# ---- one worker, one private Word, every file of a batch -------------------------------------------------------------

@dataclass
class WordBatchResult:
    """What ``run_word_batch`` did: per file its PDF or the error that stopped it, Word's version, the time each file
    took, how many worker processes it took (one, unless a stuck file ended one), and — when the first worker could
    not start Word and nothing rendered — ``start_error``, which ``word_com.render_pdfs`` raises so the renderer
    counts as unavailable once rather than failing file by file."""
    results: Dict[Path, Union[Path, WordWorkerError]] = field(default_factory=dict)
    seconds: Dict[Path, float] = field(default_factory=dict)
    version: str = "(version unavailable)"
    workers: int = 0
    start_error: Optional[WordWorkerError] = None


@dataclass
class _WorkerOutcome:
    remaining: List[Path]                               # the files a fresh worker must still render, in order
    start_error: Optional[WordWorkerError] = None       # Word never started in this worker


def _reason(exc: BaseException) -> str:
    from .word_com import com_error_text
    return (com_error_text(exc) or f"{type(exc).__name__}: {exc}")[:8000]


def instance_lost(exc: BaseException) -> bool:
    """A COM error whose HRESULT says the Word process is gone, not that it refused this file."""
    if type(exc).__name__ != "com_error":
        return False
    hresult = getattr(exc, "hresult", None)
    if not isinstance(hresult, int):
        args = getattr(exc, "args", ()) or ()
        hresult = args[0] if args and isinstance(args[0], int) else None
    return hresult is not None and (hresult & 0xFFFFFFFF) in INSTANCE_LOST_HRESULTS


def _batch_worker(connection, request):
    """Spawn target for ``run_word_batch``: start one private Word, render every file of ``request["files"]`` in turn
    to ``<scratch>/<index>.pdf``, quit. Each step is one bounded message — ``started`` (the private instance's
    identity, right after DispatchEx), ``ready`` (Word's version), ``file_started``/``file_done`` per file (a file
    Word refuses is ``ok: False`` with the COM error and the stage, and the instance goes on to the next; a file
    whose error says the instance itself is gone ends the worker), ``finished`` after Quit, or ``worker_failed``
    with the phase it failed in. The parent publishes each PDF when its ``file_done`` arrives."""
    from .word_com import WordSession
    scratch = Path(request["scratch"])
    journal = scratch / "word.events.jsonl"
    session = None
    phase = "word_start"
    with diag.scope(journal, **request["context"]):
        try:
            try:
                import win32process
                preceding = set(win32process.EnumProcesses())

                def started(app):
                    try:
                        identity = _private_identity(app, preceding)
                        connection.send({"kind": "started", "identity": identity})
                    except Exception as exc:
                        connection.send({"kind": "started", "identity": None})
                        connection.send({"kind": "cleanup_unavailable", "reason": str(exc)[:1000]})

                session = WordSession(scratch=scratch, on_started=started)
                with diag.com_quiet(), session as ws:
                    try:
                        version = f"{ws.app.Version} (build {ws.app.Build})"
                    except Exception:
                        version = "(version unavailable)"
                    phase = "word_file"
                    connection.send({"kind": "ready", "version": version})
                    for index, source in enumerate(request["files"]):
                        connection.send({"kind": "file_started", "index": index})
                        try:
                            ws.render_pdf(Path(source), scratch / f"{index}.pdf")
                        except Exception as exc:  # noqa: BLE001 — this file's failure, said per file
                            connection.send({"kind": "file_done", "index": index, "ok": False, "code": "word-com",
                                             "reason": _reason(exc), "stage": getattr(ws, "failure_stage", ws.stage)})
                            if instance_lost(exc):
                                raise
                            continue
                        connection.send({"kind": "file_done", "index": index, "ok": True})
                    phase = "word_quit"
                connection.send({"kind": "finished"})
            except BaseException as exc:
                connection.send({"kind": "worker_failed", "code": "word-com", "reason": _reason(exc), "stage": phase})
        finally:
            connection.close()


def run_word_batch(files, out_dir, timeout_per_file=None) -> WordBatchResult:
    """Render ``files`` to ``<out_dir>/<stem>.pdf`` through as few private Word instances as the files allow: one
    spawned worker renders them all, and only a file that exceeds ``timeout_per_file`` (startup and Word's quit
    have the same allowance) or a worker that dies ends it — that file is recorded as failed, the worker and the
    private Word it owns are terminated (private-instance cleanup only), and the remaining files go on in a fresh
    worker. Per-file results are PDF paths or ``WordWorkerError``s; a Word that cannot start, with nothing rendered
    at all, is ``start_error``."""
    seconds = _timeout(timeout_per_file)
    files = [Path(f) for f in files]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    batch = WordBatchResult()
    remaining = list(files)
    while remaining:
        batch.workers += 1
        rendered_before = any(isinstance(v, Path) for v in batch.results.values())
        with diag.span("render.word_worker", worker=batch.workers, file_count=len(remaining),
                       timeout_seconds=seconds) as outcome:
            got = _run_batch_worker(remaining, out_dir, seconds, batch)
            outcome.update(remaining=len(got.remaining), start_failed=got.start_error is not None)
        if got.start_error is not None:
            for f in got.remaining:
                batch.results.setdefault(f, got.start_error)
            if not rendered_before and not any(isinstance(v, Path) for v in batch.results.values()):
                batch.start_error = got.start_error
            break
        remaining = got.remaining
    return batch


def word_instances(file_count: int) -> int:
    """How many private Word instances render ``file_count`` files side by side (see ``WORD_INSTANCES``)."""
    try:
        limit = int(os.environ.get(WORD_INSTANCES_ENV) or WORD_INSTANCES)
    except ValueError:
        limit = WORD_INSTANCES
    cores = max(1, (os.cpu_count() or 2) // 2)
    return max(1, min(limit, cores, math.ceil(file_count / FILES_PER_INSTANCE)))


def run_word_batches(files, out_dir, timeout_per_file=None, instances: Optional[int] = None) -> WordBatchResult:
    """``run_word_batch`` over ``instances`` (default ``word_instances``) shares of ``files``, side by side: each
    share has its own spawned worker, its own private Word and its own per-file deadlines, exactly as one batch has.
    The results are merged. A share whose Word could not start while another's did gets one more try, in one
    instance, after the others finish (a busy machine may refuse a second Word at once); only when no share's Word
    started at all is the merged result a ``start_error``."""
    files = [Path(f) for f in files]
    count = instances or word_instances(len(files))
    if count <= 1 or len(files) <= 1:
        return run_word_batch(files, out_dir, timeout_per_file)
    shares = [files[i::count] for i in range(count)]
    with diag.span("render.word_instances", instances=count, file_count=len(files)):
        with ThreadPoolExecutor(max_workers=count, thread_name_prefix="SpecSurgeon-Word") as pool:
            # each share in its own copy of the caller's context (diagnostics correlation), never one copy in two
            futures = [pool.submit(contextvars.copy_context().run, run_word_batch, share, out_dir, timeout_per_file)
                       for share in shares]
            parts = [f.result() for f in futures]
    merged = WordBatchResult()
    for part in parts:
        merged.results.update(part.results)
        merged.seconds.update(part.seconds)
        merged.workers += part.workers
        if merged.version == WordBatchResult.version:
            merged.version = part.version
    started = [part for part in parts if part.start_error is None]
    if not started:
        merged.start_error = parts[0].start_error
        return merged
    retry = [f for part in parts if part.start_error is not None for f in part.results]
    if retry:
        diag.event("render.word_instance_retry", level="warning", file_count=len(retry),
                   reason=str(next(p.start_error for p in parts if p.start_error is not None)))
        again = run_word_batch(retry, out_dir, timeout_per_file)
        merged.results.update(again.results)
        merged.seconds.update(again.seconds)
        merged.workers += again.workers
    merged.results = {f: merged.results[f] for f in files if f in merged.results}     # the caller's order
    return merged


def _run_batch_worker(files: List[Path], out_dir: Path, seconds: float, batch: WordBatchResult) -> _WorkerOutcome:
    """One worker over ``files``; fills ``batch.results``/``batch.seconds`` for the files it settled and returns the
    ones a fresh worker must still render. The deadline is per step: startup, each file (reset at its
    ``file_started``) and Word's quit each get ``seconds``."""
    context = multiprocessing.get_context("spawn")
    with tempfile.TemporaryDirectory(prefix="specsurgeon-word-", ignore_cleanup_errors=True) as directory:
        scratch = Path(directory)
        receive, send = context.Pipe(duplex=False)
        request = {"action": "render_batch", "files": [str(f.resolve()) for f in files], "scratch": str(scratch),
                   "context": diag.context()}
        process = context.Process(target=_batch_worker, args=(send, request), name="SpecSurgeon-Word")
        owned_handle = None
        started = False             # our worker process is running
        word_started = False        # a private Word instance exists (from here on a bail-out must terminate it)
        clean_exit = False          # Word quit by itself and the worker finished: nothing to terminate
        current: Optional[int] = None
        current_t0 = 0.0
        resolved = 0
        remaining: List[Path] = []
        start_error: Optional[WordWorkerError] = None
        deadline = time.monotonic() + seconds

        def resolve(index: int, value) -> None:
            nonlocal resolved
            batch.results[files[index]] = value
            if current_t0:
                batch.seconds[files[index]] = time.perf_counter() - current_t0
            resolved += 1

        def error(code: str, reason: str, **details) -> WordWorkerError:
            return WordWorkerError(code, reason, {**details, "timeout_seconds": seconds,
                                                  "private_cleanup_available": owned_handle is not None})

        def bail(code: str, reason: str, **details) -> None:
            """The worker is stuck or gone: the file in hand fails, the rest go to a fresh worker; before Word
            started it is a start failure."""
            nonlocal remaining, start_error
            if current is not None:
                resolve(current, error(code, reason, **details))
                remaining = files[current + 1:]
            elif not word_started:
                start_error = error(code, reason, stage="word_start", **details)
                remaining = list(files)
            else:
                remaining = [f for f in files if f not in batch.results]
        try:
            with diag.com_quiet():
                process.start()
            started = True
            send.close()
            finished = False
            while not finished:
                stopping.check("Word")      # clean_exit stays False: the finally ends the worker and its private Word
                left = deadline - time.monotonic()
                if left <= 0:
                    if current is None and word_started and all(f in batch.results for f in files):
                        break   # every file is done; only Word's own quit is late — the instance is terminated below
                    if current is not None:
                        what = f"Word exceeded {seconds:g} seconds for {files[current].name}"
                    elif not word_started:
                        what = f"Word did not start within {seconds:g} seconds"
                    else:
                        what = f"Word stopped answering between files for {seconds:g} seconds"
                    bail("word-timeout", what)
                    break
                try:
                    ready = receive.poll(min(left, POLL_SECONDS))
                except (BrokenPipeError, OSError):   # Windows ends the pipe when the worker dies: poll raises, recv never runs
                    bail("word-worker-died", "Word worker stopped without a completed result")
                    break
                if ready:
                    try:
                        message = receive.recv()
                    except EOFError:
                        bail("word-worker-died", "Word worker stopped without a completed result")
                        break
                    kind = message.get("kind")
                    if kind == "started":
                        word_started = True
                        if message.get("identity"):
                            try:
                                owned_handle = _open_private_handle(message["identity"])
                            except Exception as exc:
                                diag.event("render.word_cleanup_unavailable", level="warning", reason=str(exc))
                        else:
                            diag.event("render.word_cleanup_unavailable", level="warning",
                                       reason="DispatchEx process ownership could not be proved")
                        deadline = time.monotonic() + seconds
                    elif kind == "cleanup_unavailable":
                        diag.event("render.word_cleanup_unavailable", level="warning", reason=message.get("reason"))
                    elif kind == "ready":
                        batch.version = str(message.get("version") or batch.version)
                        deadline = time.monotonic() + seconds
                    elif kind == "file_started":
                        current = int(message["index"])
                        current_t0 = time.perf_counter()
                        deadline = time.monotonic() + seconds
                    elif kind == "file_done":
                        index = int(message["index"])
                        if message.get("ok"):
                            try:
                                resolve(index, _publish(scratch / f"{index}.pdf", out_dir / (files[index].stem + ".pdf")))
                            except WordWorkerError as exc:
                                resolve(index, exc)
                            except OSError as exc:      # the destination is held, the disk is full: this file's
                                resolve(index, WordWorkerError("word-publish", f"{type(exc).__name__}: {exc}"))
                        else:
                            resolve(index, WordWorkerError(message.get("code", "word-com"),
                                                           message.get("reason", "Word failed"),
                                                           {"stage": message.get("stage")}))
                        current, current_t0 = None, 0.0
                        deadline = time.monotonic() + seconds
                    elif kind == "worker_failed":
                        failure = WordWorkerError(message.get("code", "word-com"), message.get("reason", "Word failed"),
                                                  {"stage": message.get("stage")})
                        if not word_started:
                            start_error, remaining = failure, list(files)
                        elif current is not None:
                            resolve(current, failure)
                            remaining = files[current + 1:]
                        else:
                            remaining = [f for f in files if f not in batch.results]
                        break
                    elif kind == "finished":
                        finished = True
                elif not process.is_alive():
                    bail("word-worker-died", "Word worker stopped without a completed result", exitcode=process.exitcode)
                    break
            if finished:
                process.join(max(0, deadline - time.monotonic()))
                clean_exit = not process.is_alive()
                remaining = [f for f in files if f not in batch.results]
        finally:
            send.close()
            receive.close()
            if started:
                _end_process(process)
            try:
                _close_private_handle(owned_handle, terminate=not clean_exit)
            except Exception as exc:
                diag.event("render.word_cleanup_failed", level="warning", reason=str(exc))
            _replay_journal(scratch)
    if start_error is None and resolved == 0 and remaining:
        # A worker that settled nothing cannot be retried forever: it counts as Word failing to start.
        start_error = WordWorkerError("word-worker-died", "Word worker stopped before rendering anything",
                                      {"stage": "word_start", "timeout_seconds": seconds})
    return _WorkerOutcome(remaining, start_error)
