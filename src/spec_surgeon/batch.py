"""Batch orchestration — the same pipeline for 1 file or 100, for a named job or an EditSpec:

    acquire → fingerprint → plan (predict first) → apply → verify (V1–V5, V8)
            → render (source + output) → V6/V7 → deliver (guarded, V9) → records + batch report + set_aside.json

Transform+verify runs in threads by default; rendering runs through the chosen backend (Word is one
instance, LibreOffice is N parallel profiles). Exceptions never stop the batch; they are written to
<out>/exceptions/ and summarized in <out>/set_aside.json with a structured diagnosis for the next pass.

``<out>/outputs/`` holds only files that passed every check, V9 included. The worker verifies the output in memory
and writes it only after the suite has run (a verifier that raises leaves nothing behind but the exception record).
An output that fails verification, or whose V9 guard fails afterwards (the source drifted during the run, or the
in-place write-back was refused), is moved to ``<out>/failed/`` (kept for diagnosis, never delivered) so that copying
the outputs folder can never pick up a file the suite rejected.

Output identity. Every input gets an output name that is unique across the whole batch (``output_names``: inputs in
different folders may share a file name; a name an input really has is never handed to another input; Windows
compares names case-insensitively), and its plan, record and exception files carry the same stem. Delivery is bound
to the bytes the suite verified: the worker records their md5 and delivery re-hashes the output file before writing
anything, in place or not. A run never reads where it writes: an input inside the results folder is refused before
anything is created (``check_inputs_outside``) — copies mode would otherwise overwrite it.

Diagnostics (``diagnostics.py``). ``<out>/logs/run.log`` has everything the run did (environment, progress, the
renderer's messages, every file's trace, the traceback of anything unexpected — the batch's own failure included);
``<out>/logs/<name>.trace.log`` is one file's path through the stages, written as it goes; an exception record says
the stage, the op and the line of code where the file stopped, and whether it was a refusal, an unreadable file, an
I/O problem or the engine's own failure (with the traceback); ``DIAGNOSTICS.md`` puts it together per file. A worker
process that dies takes no other file's result with it: the files it held are run again, each alone in a fresh
process, and a file that kills that one too is recorded as ``worker-died`` with the last stage its trace reached.

Stop (Safe mode's kill switch, ``stopping``): between files, before the render, between page comparisons and before
delivery a pressed Stop raises ``stopping.Stopped``; worker processes are terminated on the spot (``stop_pool``) and a
renderer's processes are ended by the renderer. Delivery itself, once begun, finishes. A stopped batch returns
nothing, so nothing of it reaches the window.
"""
from __future__ import annotations

import copy
import glob
import hashlib
import json
import multiprocessing
import os
import shutil
import stat
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union

from . import __version__, guard, stopping
from . import diagnostics as diag
from .fingerprint import fingerprint
from .editspec import (apply_spec, is_template_file, naming_op, plan_spec, spec_input_files, spec_renames_outputs, spec_templates,
                       validate_spec)
from .ops import JobException
from .package import DocxPackage
from .verify import run_suite, VerificationRecord, CheckResult, GEOM_TOL_PT
from .verify import geometry as geom
from .verify.template_layout import keeps_distances, template_reference
from .report import words_missing_sentence
from . import render as render_mod


@dataclass
class FileResult:
    source: Path
    identity: Optional[guard.Identity] = None
    fingerprint: Dict[str, Any] = field(default_factory=dict)
    plan: Optional[Dict[str, Any]] = None
    exception: Optional[Dict[str, Any]] = None
    output: Optional[Path] = None
    record: Optional[VerificationRecord] = None
    delivered_to: Optional[str] = None
    timings: Dict[str, float] = field(default_factory=dict)
    noop: bool = False
    out_name: str = ""                       # the output's file name, unique across the batch (``output_names``)
    output_md5: Optional[str] = None         # md5 of the bytes the suite verified; delivery re-checks the file
    source_sha256: Optional[str] = None     # durable evaluation pin of acquired, identity-checked original bytes
    trace_path: Optional[Path] = None        # logs/<name>.trace.log: this file's path through the pipeline
    trace: List[str] = field(default_factory=list)       # its lines (also in run.log and DIAGNOSTICS.md)
    render_messages: List[str] = field(default_factory=list)   # what the renderer said about this file
    unverified: List[str] = field(default_factory=list)   # the failed checks of a copy delivered anyway (Obliterate)

    @property
    def record_stem(self) -> str:
        """The stem of this file's plan/record/exception files — the output name's, so two inputs that share a
        file name never share a record either."""
        return Path(self.out_name or self.source.name).stem

    @property
    def status(self) -> str:
        if self.exception:
            return "EXCEPTION"
        if self.record is None:
            return "PENDING"
        return "PASS" if self.record.passed else "FAIL"


@dataclass
class BatchResult:
    out_dir: Path
    job: str
    params: Dict[str, Any]
    files: List[FileResult] = field(default_factory=list)
    spec: Optional[Dict[str, Any]] = None
    skipped: List[str] = field(default_factory=list)      # inputs left out (the spec's own template files)
    timings: Dict[str, float] = field(default_factory=dict)
    renderer: Optional[str] = None
    contact_sheet: Optional[Path] = None
    environment: Dict[str, Any] = field(default_factory=dict)   # versions, platform, renderer (diagnostics)
    started: str = ""
    finished: str = ""
    log_path: Optional[Path] = None          # logs/run.log
    stopped: Optional[str] = None            # set when the batch itself failed: the stage and the error
    goal_contract: Optional[Dict[str, Any]] = None
    goal_bindings: Dict[str, Any] = field(default_factory=dict)
    enforce_goals: bool = False
    input_snapshots: List[Dict[str, Any]] = field(default_factory=list)

    def counts(self) -> Dict[str, int]:
        c = {"PASS": 0, "FAIL": 0, "EXCEPTION": 0, "PENDING": 0}
        for f in self.files:
            c[f.status] += 1
        return c


def load_set_aside(path_or_dir: Union[str, Path]) -> Dict[str, Any]:
    """Load ``set_aside.json`` from a file path, a batch output folder, or its ``run/`` subfolder."""
    p = Path(path_or_dir)
    candidates = [p] if p.is_file() else [p / "set_aside.json", p / "run" / "set_aside.json"]
    for c in candidates:
        if c.is_file():
            data = json.loads(c.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "files" in data:
                return data
    raise FileNotFoundError(f"no set_aside.json found in {path_or_dir}")


# ---- input expansion -------------------------------------------------------------------------------

def expand_inputs(inputs: Sequence[str]) -> List[Path]:
    files: List[Path] = []
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            files += sorted(x for x in p.iterdir() if x.suffix.lower() == ".docx" and not x.name.startswith("~$"))
        elif any(ch in item for ch in "*?["):
            files += sorted(Path(x) for x in glob.glob(item) if x.lower().endswith(".docx") and not Path(x).name.startswith("~$"))
        elif p.is_file():
            files.append(p)
        else:
            raise FileNotFoundError(item)
    seen = set()
    out = []
    for f in files:
        r = f.resolve()
        if r not in seen:
            seen.add(r)
            out.append(f)
    return out


def output_names(files: Sequence[Path], fixed: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """One output name per input, unique across the batch however the inputs are named.

    An input keeps its own name when it is the first with it; a later input with the same name (another folder)
    gets ``stem (n)suffix`` for the first n that no input really has and no earlier output took — so ``a/Spec.docx``,
    ``b/Spec.docx`` and ``c/Spec (1).docx`` come out as ``Spec.docx``, ``Spec (2).docx`` and ``Spec (1).docx``, never
    two outputs at one path with one of them silently replacing the other. Names compare case-insensitively (Windows
    does). Keyed by ``str(path)``, as the worker results are. ``fixed`` are the names a spec gives (name_output,
    make_values_agree): they are taken first, and every other input steers clear of them."""
    reserved = {f.name.casefold() for f in files}      # every real name, before any generated one
    fixed = dict(fixed or {})
    assigned: set = {n.casefold() for n in fixed.values()}
    out: Dict[str, str] = dict(fixed)
    for f in files:
        if str(f) in fixed:
            continue
        key = f.name.casefold()
        if key not in assigned:
            name = f.name
        else:
            n = 1
            while f"{f.stem} ({n}){f.suffix}".casefold() in reserved or f"{f.stem} ({n}){f.suffix}".casefold() in assigned:
                n += 1
            name = f"{f.stem} ({n}){f.suffix}"
        assigned.add(name.casefold())
        out[str(f)] = name
    return out


def planned_names(files: Sequence[Path], spec: Optional[Dict[str, Any]]) -> Tuple[Dict[str, str], Dict[str, Dict[str, Any]]]:
    """The output names a spec's naming op gives (name_output, make_values_agree — computed with that op's own plan on
    each file, before the workers start), and the refusals for files two of which would get one name (``name-collision``:
    both are refused, never one silently renamed). A file the op cannot name keeps a plain name; its worker plans the
    spec again and records why."""
    o = naming_op(spec) if spec is not None else None
    if o is None:
        return {}, {}
    from .editspec import op_params
    from .ops import get_op
    names: Dict[str, str] = {}
    for f in files:
        try:
            frag = get_op(o["op"]).plan(DocxPackage(Path(f).read_bytes(), name=str(f)), o.get("select") or {},
                                        op_params(o))
        except Exception:  # noqa: BLE001 — the worker plans it again and records the reason in full
            continue
        if frag.output_name:
            names[str(f)] = frag.output_name
    groups: Dict[str, List[str]] = {}
    for k, n in names.items():
        groups.setdefault(n.casefold(), []).append(k)
    refused: Dict[str, Dict[str, Any]] = {}
    for n, ks in groups.items():
        if len(ks) > 1:
            for k in ks:
                others = [Path(x).name for x in ks if x != k]
                refused[k] = {"code": "name-collision", "details": {"name": names[k], "others": others},
                              "reason": f"the spec would name this file {names[k]!r}, as it would {others} — two outputs "
                                        f"cannot share a name; check the numbers and titles these files show"}
    return {k: v for k, v in names.items() if k not in refused}, refused


def check_inputs_outside(files: Sequence[Path], out_dir: Path) -> None:
    """Refuse a batch whose inputs lie inside the results folder — before anything is created or written.

    ``outputs/`` and ``failed/`` are written and emptied by the run; an input under the results folder (a previous
    run's output picked as this run's input with the same results folder, say) could be overwritten by its own
    edited copy and then moved to ``failed/`` — an input that is gone. The rule is simpler than tracking which
    paths collide: a run never reads where it writes."""
    root = Path(out_dir).resolve()
    inside = []
    for f in files:
        r = Path(f).resolve()
        if r == root or root in r.parents:
            inside.append(str(f))
    if inside:
        raise ValueError(f"{len(inside)} input file(s) lie inside the results folder {out_dir}: "
                         f"{[Path(x).name for x in inside[:6]]}{' …' if len(inside) > 6 else ''}. A run never writes "
                         f"where it reads — choose a results folder outside the input folders (nothing was written).")
    # A destination can point back at a source even when its lexical ancestors do not.
    # Reject existing directory aliases before logging, clearing or writing any result.
    def linked(path):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            return True
        try:
            # Path.is_junction arrived in 3.12; retain this guard on supported Windows 3.10/3.11 too.
            return getattr(path.lstat(), "st_reparse_tag", None) == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)
        except OSError:
            return False

    destination = Path(out_dir)
    if linked(destination):
        raise ValueError(f"The results folder {out_dir} is a symbolic link or junction; choose a separate directory.")
    if not destination.is_dir():
        return
    identities = set()
    for source in files:
        try:
            st = Path(source).stat()
            identities.add((st.st_dev, st.st_ino))
        except OSError:
            pass  # missing/unreadable inputs remain per-file failures in the ordinary pipeline
    for folder, directories, names in os.walk(destination, followlinks=False):
        file_names = set(names)
        for name in [*directories, *names]:
            path = Path(folder) / name
            if linked(path):
                raise ValueError(f"The results folder contains a symbolic link or junction ({path}); "
                                 "choose a separate directory so result writes cannot reach an input.")
            if name in file_names:
                st = path.stat()
                if (st.st_dev, st.st_ino) in identities:
                    raise ValueError(f"A result path ({path}) is a hard link to an input file; choose a separate directory.")


def clear_delivery_folders(out_dir: Path) -> None:
    """Discard prior delivery artifacts only after every source and auxiliary input passed the overlap check."""
    for name in ("outputs", "failed"):
        folder = Path(out_dir) / name
        if folder.is_symlink():
            folder.unlink()
        elif folder.is_dir():
            shutil.rmtree(folder)


# ---- worker (runs in a subprocess) ------------------------------------------------------------------

# How a batch's files are transformed and verified (``worker_plan``). The window's runs use ``auto``: a batch of
# PROCESS_MIN_FILES or more files is shared over spawned worker processes, one per core up to MAX_PROCESS_WORKERS; each
# file's acquire, plan, apply and verify run whole in one worker, exactly as they run inline, so a file's result does
# not depend on which worker had it. Fewer files run inline, where a worker's start would cost more than it saves.
# ``thread``, ``serial`` and ``process`` ask for one kind explicitly. (tests/conftest.py sets ``thread``: a test's
# patches never reach a spawned worker.)
WORKER_MODES = ("auto", "thread", "serial", "process")
DEFAULT_WORKER_MODE = "auto"
PROCESS_MIN_FILES = 3
MAX_PROCESS_WORKERS = 8


def worker_plan(worker_mode: Optional[str], workers: int, file_count: int) -> Tuple[str, int]:
    """(mode, workers) for a batch of ``file_count`` files; ``worker_mode`` None is ``DEFAULT_WORKER_MODE``.

    ``auto``: processes for PROCESS_MIN_FILES files or more (unless ``workers`` is 1), else ``serial``. ``thread``: one
    worker unless ``workers`` asks for more — plan, apply and verify are Python holding the GIL, so threads run them
    one at a time anyway (measured on eight 53-page sections: eight threads took 19% longer than one and 2.7x the
    memory). ``serial``: one, inline. ``process``: ``workers``, or one per core up to MAX_PROCESS_WORKERS. The packaged
    app runs processes too: the launcher calls ``freeze_support`` first, and the "access violations at worker spawn"
    its crash log once held were handled first-chance exceptions inside CreateProcess, now kept out of it
    (``diagnostics.com_quiet``)."""
    mode = worker_mode or DEFAULT_WORKER_MODE
    if mode not in WORKER_MODES:
        raise ValueError("worker_mode must be auto, thread, serial or process")
    if mode == "auto":
        mode = "process" if file_count >= PROCESS_MIN_FILES and workers != 1 else "serial"
    if mode == "thread" and not workers:
        workers = 1
    if mode == "serial":
        workers = 1
    return mode, workers or max(1, min(os.cpu_count() or 2, file_count, MAX_PROCESS_WORKERS))


def _in_pool_worker() -> bool:
    return multiprocessing.parent_process() is not None


def _process_pool(workers: int) -> ProcessPoolExecutor:
    """Spawn on every platform: pools can start from the desktop's background thread.

    The Windows frozen launcher calls freeze_support before opening UI. Using an
    explicit context also keeps retries from inheriting a threaded native (window) state.
    The pool starts its processes as work is submitted: submit inside ``diagnostics.com_quiet``.
    """
    return ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"))


def stop_pool(ex) -> None:
    """Stop pressed while a pool works (``stopping``): its waiting calls are dropped and its worker processes
    terminated at once, so none goes on writing into the run's folder; a thread pool's running calls finish the file
    in hand. The caller raises ``stopping.Stopped``."""
    if isinstance(ex, ProcessPoolExecutor):
        processes = list((getattr(ex, "_processes", None) or {}).values())
        terminate = getattr(ex, "terminate_workers", None)          # Python 3.14
        if callable(terminate):
            terminate()
        else:
            for process in processes:
                try:
                    process.terminate()
                except Exception:  # noqa: BLE001 — gone already
                    pass
        for process in processes:
            process.join(5)
        diag.event("worker.pool_stopped", level="warning", workers=len(processes))
        ex.shutdown(wait=True, cancel_futures=True)
    else:
        ex.shutdown(wait=False, cancel_futures=True)


def map_files(fn: Callable[..., Any], calls: Sequence[Tuple[Any, ...]], min_files: int = PROCESS_MIN_FILES) -> List[Any]:
    """``[fn(*args) for args in calls]``, in order: shared over spawned worker processes when ``worker_plan`` would
    share a batch of that many files and there are at least ``min_files`` of them, else in this process. ``fn`` is a
    module-level function whose arguments and result are plain data. A call whose worker stopped abruptly (the pool
    broke) is made again here, as it would have been without the pool. Stop (``stopping``) ends it between calls, or
    at once over a pool, its processes terminated."""
    mode, workers = worker_plan(None, 0, len(calls))
    name = getattr(fn, "__name__", "?")
    if mode != "process" or workers <= 1 or len(calls) < min_files:
        out = []
        for args in calls:
            stopping.check(name)
            out.append(fn(*args))
        return out
    with diag.span("worker.map", function=name, calls=len(calls), workers=workers):
        with _process_pool(workers) as ex:
            with diag.com_quiet():          # the pool starts its processes here; see diagnostics.com_quiet
                futures = [ex.submit(fn, *args) for args in calls]
            out = []
            for future, args in zip(futures, calls):
                try:
                    out.append(stopping.wait(future, name, lambda: stop_pool(ex)).result())
                except BrokenProcessPool as e:
                    diag.event("worker.map_inline", level="warning", function=getattr(fn, "__name__", "?"),
                               reason=str(e))
                    out.append(fn(*args))
            return out


def _when(mtime_ns: int) -> str:
    return datetime.fromtimestamp(mtime_ns / 1e9).strftime("%Y-%m-%d %H:%M:%S")


def _fp_summary(fp: Dict[str, Any]) -> str:
    parts = fp.get("parts") or {}
    pending = sum((p.get("ins") or 0) + (p.get("del") or 0) for p in parts.values() if isinstance(p, dict))
    return (f"pattern {fp.get('pattern_id')} · {len(fp.get('sections') or [])} section(s) · {len(parts)} text part(s)"
            f" · track changes {'on' if fp.get('trackRevisions') else 'off'} · {pending} pending ins/del")


def _verify_summary(rec: VerificationRecord) -> str:
    ran = [c for c in rec.checks if c.id not in ("V6", "V7", "V9")]
    bad = [c.id for c in ran if c.status == "FAIL"]
    return ("PASS" if not bad else f"FAIL ({', '.join(bad)})") + " — " + " · ".join(f"{c.id} {c.status}" for c in ran)


_FAILED_AS = {"refusal": "REFUSED", "unreadable": "UNREADABLE", "environment": "I/O ERROR",
              "internal-error": "FAILED", "worker-died": "WORKER DIED"}


def _note_exception(tr: "diag.Tracer", stage: str, exc: Dict[str, Any]) -> None:
    op = f"op #{exc['op_number']} {exc.get('op')}: " if exc.get("op_number") else ""
    tr.note(stage, f"{op}{_FAILED_AS.get(exc.get('kind'), 'FAILED')} [{exc.get('code')}] {exc.get('reason')}")
    if exc.get("where"):
        tr.block(f"at {exc['where']}")
    details = {k: v for k, v in (exc.get("details") or {}).items() if k != "content"}   # content: in the reason
    if details:
        tr.block("details: " + diag.compact(details))
    if exc.get("traceback") and exc.get("kind") in ("internal-error", "environment"):
        tr.block(exc["traceback"])


def _process_one(path: str, job_name: str, params: Dict[str, Any], out_dir: str, out_name: Optional[str] = None,
                 spec: Optional[Dict[str, Any]] = None, attempt: int = 1,
                 refuse: Optional[Dict[str, Any]] = None,
                 trace_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Carry correlation explicitly into spawned workers and keep their event streams separate."""
    context = dict(trace_context if trace_context is not None else diag.context())
    context.update(source=path, output_name=out_name or Path(path).name, attempt=attempt,
                   worker_id=diag.new_id())
    events = diag.trace_path(Path(out_dir), out_name or Path(path).name).with_suffix(".events.jsonl")
    with diag.scope(events, **context), diag.span("file.process") as outcome:
        result = _process_one_impl(path, job_name, params, out_dir, out_name, spec, attempt, refuse)
        exception = result.get("exception")
        outcome.update(status="exception" if exception else
                       "pass" if (result.get("record") or {}).get("passed") else "fail",
                       timings=result.get("timings"), output_md5=result.get("output_md5"))
        if exception:
            diag.event("file.exception", level="error", exception=exception)
        return result


def _process_one_impl(path: str, job_name: str, params: Dict[str, Any], out_dir: str,
                      out_name: Optional[str] = None, spec: Optional[Dict[str, Any]] = None,
                      attempt: int = 1, refuse: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One file from acquisition to its verified output (in a pool worker, or in-process for a single file).

    Every stage is written to the file's trace as it happens (``logs/<name>.trace.log``). Anything that stops the
    file — a refusal, an unreadable file, a disk that will not write, the engine's own failure — becomes its exception
    record with the stage, the op and the line of code (and the traceback when the engine failed), never a crashed
    batch. ``attempt`` 2 is the same file run again alone after its first worker died; its trace is appended."""
    t0 = time.perf_counter()
    res: Dict[str, Any] = {"source": path, "timings": {}}
    src = Path(path)
    name = out_name or src.name
    in_pool = _in_pool_worker()
    tpath = diag.trace_path(Path(out_dir), name)
    res["trace_path"] = str(tpath)
    what = (f" — ops {', '.join(str(o.get('op')) for o in spec.get('ops', []))}" if spec is not None
            else f" {diag.snip(json.dumps(params, ensure_ascii=False, default=str), 300)}")
    tr = diag.Tracer(tpath, f"Spec Surgeon {__version__} — trace of {src.name}"
                     + (" (run again, alone in a fresh process, after its first worker died)" if attempt > 1 else ""),
                     [f"source       {src}", f"output name  {name}", f"job          {job_name}{what}",
                      f"process      pid {os.getpid()} ({'pool worker' if in_pool else 'the main process'})"],
                     append=attempt > 1)
    tr.watch(crash=in_pool)
    stage, data, t_stage = "acquire", None, t0
    try:
        tr.note("acquire", "…")
        identity = guard.acquire(src)
        res["identity"] = identity.to_dict()
        diag.event("file.acquired", identity=res["identity"])
        tr.note("acquire", f"{identity.size:,} bytes · modified {_when(identity.mtime_ns)} · md5 {identity.md5} "
                           f"[{diag.ms(t_stage)}]")
        stage, t_stage = "open", time.perf_counter()
        tr.note("open", "…")
        data = src.read_bytes()
        actual_md5 = hashlib.md5(data).hexdigest()
        if actual_md5 != identity.md5:
            raise JobException("the source changed between acquisition and reading; reacquire it and run again",
                               "source-drift", {"expected_md5": identity.md5, "actual_md5": actual_md5})
        res["source_sha256"] = hashlib.sha256(data).hexdigest()
        pkg = DocxPackage(data, name=str(src))
        main = pkg.main_part()
        tr.note("open", f"{len(pkg.source_names)} part(s) · main part {main} [{diag.ms(t_stage)}]")
        stage, t_stage = "fingerprint", time.perf_counter()
        tr.note("fingerprint", "…")
        res["fingerprint"] = fingerprint(pkg)
        diag.event("file.fingerprint", fingerprint=res["fingerprint"])
        tr.note("fingerprint", _fp_summary(res["fingerprint"]) + f" [{diag.ms(t_stage)}]")
        res["timings"]["acquire+fingerprint"] = time.perf_counter() - t0

        stage, t_stage = "plan", time.perf_counter()
        if refuse:
            raise JobException(refuse["reason"], refuse["code"], refuse.get("details") or {})
        plan, fragments = plan_spec(pkg, spec, res["fingerprint"].get("pattern_id"), trace=tr.op_hook)
        if plan.output_name is not None and plan.output_name != name:
            raise JobException(f"the spec names this file {plan.output_name!r} but the batch planned "
                               f"{name!r}", "internal")
        res["plan"] = plan.to_dict()
        res["timings"]["plan"] = time.perf_counter() - t_stage
        diag.event("file.planned", duration_ms=res["timings"]["plan"] * 1000,
                   intended_parts=res["plan"].get("intended_parts"),
                   members_added=res["plan"].get("members_added"),
                   members_removed=res["plan"].get("members_removed"), output_name=plan.output_name)
        tr.note("plan", "merged: " + diag.plan_summary(plan) + f" [{diag.ms(t_stage)}]")

        stage, t_stage = "apply", time.perf_counter()
        work = apply_spec(pkg, fragments, trace=tr.op_hook)
        res["noop"] = not work.is_modified() and plan.output_name in (None, src.name)   # a rename is delivered
        out_bytes = work.output_bytes()
        res["timings"]["apply"] = time.perf_counter() - t_stage
        tr.note("apply", ("no change — the output is identical to the source" if res.get("noop")
                          else f"repacked: {len(out_bytes):,} bytes") + f" [{diag.ms(t_stage)}]")

        # verify in memory first; the output is written only once the suite has run, so a verifier that raises
        # leaves nothing in outputs/ (the exception record says what happened), and delivery is bound to these bytes
        stage, t_stage = "verify", time.perf_counter()
        tr.note("verify", "V1–V5, V8, V10 …")
        out_path = Path(out_dir) / "outputs" / name
        rec = run_suite(pkg.data, out_bytes, plan.to_dict(), str(src), str(out_path))
        res["record"] = rec.to_dict()
        res["timings"]["verify"] = time.perf_counter() - t_stage
        tr.note("verify", _verify_summary(rec) + f" [{diag.ms(t_stage)}]")
        for cid, cname, _summary, details in rec.failures():
            tr.block(f"{cid} {cname}:")
            tr.block("\n".join(f"  - {x}" for x in details))
        stage, t_stage = "write", time.perf_counter()
        diag.event("file.write.started", output=str(out_path), size=len(out_bytes))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(out_bytes)
        res["timings"]["write"] = time.perf_counter() - t_stage
        res["output"] = str(out_path)
        res["output_md5"] = hashlib.md5(out_bytes).hexdigest()
        diag.event("file.write.succeeded", output=str(out_path), output_md5=res["output_md5"],
                   duration_ms=res["timings"]["write"] * 1000)
        tr.note("write", f"outputs/{name} · md5 {res['output_md5']} [{diag.ms(t_stage)}]")
    except Exception as e:  # a refusal, an unreadable file, a disk that will not write, the engine's own failure
        exc = diag.exception_record(e, stage, data, tr.op)
        res["exception"] = exc
        key = "acquire+fingerprint" if stage in ("acquire", "open", "fingerprint") else stage
        res["timings"].setdefault(key, time.perf_counter() - t_stage)
        _note_exception(tr, stage, exc)
    finally:
        res["timings"]["total"] = time.perf_counter() - t0
        res["trace"] = list(tr.lines)
        tr.close()
    return res


def _worker_died(path: Path, out_dir: Path, out_name: str, why: BaseException) -> Dict[str, Any]:
    """The record of a file whose worker process ended abruptly twice (in the batch, then alone): the last stage its
    trace reached, and what Python's fault handler wrote there. A partial output is removed — outputs/ only ever holds
    verified files."""
    tpath = diag.trace_path(out_dir, out_name)
    lines = diag.read_trace(tpath)
    stage = diag.last_stage(lines) or "acquire"
    diag.event("worker.died", level="error", source=str(path), attempt=2, stage=stage,
               exception_type=type(why).__name__, reason=str(why), trace_path=str(tpath))
    fault = diag.fault_excerpt(lines)
    partial = Path(out_dir) / "outputs" / out_name
    try:
        if partial.exists():
            partial.unlink()
    except OSError:
        pass
    exc = {"code": "worker-died", "kind": "worker-died", "stage": stage,
           "reason": f"the process working on this file ended abruptly during '{stage}' — in the batch, and again "
                     f"when it was run alone ({type(why).__name__}); out of memory, or a crash inside a library it "
                     f"uses (lxml, PyMuPDF)" + (f"; Python's fault handler: {fault[0]}" if fault else ""),
           "details": {"fault_handler": fault} if fault else {}}
    tail = diag.trace_event("result", f"WORKER DIED [{exc['code']}] at '{stage}' — the file was left untouched")
    diag.append_trace(tpath, tail)
    return {"source": str(path), "timings": {}, "exception": exc, "trace_path": str(tpath), "trace": lines + tail}


def _lost_result(path: Path, out_dir: Path, out_name: str, e: BaseException) -> Dict[str, Any]:
    """A worker finished but its result could not come back (it could not be pickled, say): the engine's failure."""
    exc = diag.exception_record(e, "transform+verify")
    exc.update(code="internal-error", kind="internal-error",
               reason=f"the file's result could not be returned from its worker process: {type(e).__name__}: {e}")
    diag.event("worker.result_lost", level="error", source=str(path), exception=exc)
    tpath = diag.trace_path(out_dir, out_name)
    tail = diag.trace_event("result", f"FAILED [internal-error] {exc['reason']}", exc.get("traceback", ""))
    diag.append_trace(tpath, tail)
    return {"source": str(path), "timings": {}, "exception": exc, "trace_path": str(tpath),
            "trace": diag.read_trace(tpath)}


def _run_alone(path: Path, job_name: str, params: Dict[str, Any], out_dir: Path, out_name: str,
               spec: Optional[Dict[str, Any]], refuse: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run one file again in a fresh one-process pool, so a file that kills its worker is named — and the files that
    only shared a worker with it get their results."""
    diag.event("worker.retry", level="warning", source=str(path), attempt=2,
               reason="the original process pool stopped abruptly; isolate the file in a fresh process")
    try:
        with _process_pool(1) as ex:
            with diag.com_quiet():
                future = ex.submit(_process_one, str(path), job_name, params, str(out_dir), out_name, spec, 2,
                                   refuse, diag.context())
            return future.result()
    except BrokenProcessPool as e:
        return _worker_died(path, out_dir, out_name, e)
    except Exception as e:  # noqa: BLE001
        return _lost_result(path, out_dir, out_name, e)


# ---- failed outputs ----------------------------------------------------------------------------------

def verified_bytes(fr: FileResult) -> bytes:
    """The output file's bytes, only if they are the ones the suite verified (``output_md5``); anything else —
    the file replaced, truncated, edited since — is a GuardError, never delivered."""
    data = fr.output.read_bytes()
    actual = hashlib.md5(data).hexdigest()
    diag.event("delivery.identity", source=str(fr.source), output=str(fr.output),
               expected_md5=fr.output_md5, actual_md5=actual, size=len(data),
               matches=not fr.output_md5 or actual == fr.output_md5)
    if fr.output_md5 and actual != fr.output_md5:
        raise guard.GuardError(f"{fr.output}: the output file is not the one the suite verified (its content "
                               f"changed after verification); not delivered")
    return data


def verify_delivery_goal(source: Path, identity: Optional[guard.Identity], data: bytes,
                         contract: Dict[str, Any], binding=None) -> Dict[str, Any]:
    """Check caller-approved effects against the same original bytes this run acquired."""
    from .goals import validate_binding, verify_goal
    try:
        if identity is None:
            raise guard.GuardError("the original source identity is missing")
        drift = guard.drifted(identity)
        if drift:
            raise guard.GuardError(f"original source changed: {drift}")
        original = source.read_bytes()
        if hashlib.md5(original).hexdigest() != identity.md5:
            raise guard.GuardError("original source changed while reading its verification baseline")
        checked_binding = validate_binding(source, contract, binding)
        return verify_goal(original, data, contract, checked_binding)
    except Exception as exc:
        return {"passed": False, "errors": [f"approved goal could not be verified: {exc}"], "checks": []}


def _verify_delivery_goals(batch, contract, bindings, log):
    for fr in batch.files:
        if fr.record is None or not fr.record.passed or fr.output is None:
            continue
        try:
            verdict = verify_delivery_goal(fr.source, fr.identity, verified_bytes(fr), contract,
                                           bindings.get(str(fr.source.resolve())))
        except (guard.GuardError, OSError) as exc:
            verdict = {"passed": False, "errors": [str(exc)], "checks": []}
        passed = verdict.get("passed", False)
        summary = ("approved original-to-final effects independently verified" if passed else
                   "; ".join(verdict.get("errors", [])) or "approved original-to-final effects failed")
        fr.record.set(CheckResult("GOAL", "Approved original-to-final effects", "PASS" if passed else "FAIL",
                                  summary, verdict))
        if not passed:
            log(f"  {fr.source.name}: GOAL FAIL — {summary}")


def quarantine_failed_output(fr: FileResult, out_dir: Path) -> Path:
    """Move a failed output out of outputs/ into failed/ (diagnosis only); the record keeps pointing at the file."""
    dest_dir = Path(out_dir) / "failed"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / fr.output.name
    diag.event("delivery.quarantine.started", source=str(fr.source), output=str(fr.output), destination=str(dest))
    if dest.exists():
        dest.unlink()
    fr.output.replace(dest)
    fr.output = dest
    if fr.record is not None:
        fr.record.output = str(dest)
    diag.event("delivery.quarantine.succeeded", source=str(fr.source), destination=str(dest))
    return dest


# ---- V7 ----------------------------------------------------------------------------------------------

def _template_op_params(plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The params of the plan's templating op (its section mapping and kinds, as the spec states them)."""
    for o in plan.get("ops", []) or []:
        if o.get("op") == "apply_header_footer_template" and isinstance(o.get("params"), dict) \
                and isinstance(o["params"].get("template_sections"), dict):
            return o["params"]
    return None


def geometry_check(plan: Dict[str, Any], g: "geom.GeometryResult",
                   tg: Optional["geom.TemplateGeometry"] = None) -> CheckResult:
    """V7 from the plan's geometry reference:

    * ``source`` (default) — word positions of the output vs the source render, asserted on every page not
      declared expected-to-change; for a content edit with diffs the numbers are informational (text reflows);
    * ``none`` — the op changes layout by design (a font or page-setup change reflows everything); N/A with the
      plan's reason, and a FAIL when the plan gives no reason;
    * ``template`` — the output's headers/footers rendered alone vs the template's as the plan makes this document
      show them (its fills in the slots, laid at its own text widths, at its own header/footer distances when the
      plan keeps them: verify/template_layout.py), page for page
      (output section k ↔ the reference section of template section map[k] at k's width, first/even/odd variant ↔
      the same variant; verify/geometry.hf_only_copy): a region the template leaves blank must be blank, any other
      must be found at the template's position, its outer edge and every shared word within tolerance; FAIL when
      either render (or the op's section map) is missing.
    """
    ref = plan.get("geometry_reference", "source")
    expected_pages = plan.get("geometry_pages_expected_to_change", [])
    ok = g.worst_dy_asserted <= GEOM_TOL_PT and g.worst_dx_asserted <= GEOM_TOL_PT and g.ref_pages == g.out_pages
    moved = [p.page for p in g.pages if not p.expected_change and (p.max_dy > GEOM_TOL_PT or p.max_dx > GEOM_TOL_PT)]
    info = (f"max |Δy| {g.worst_dy_asserted:.2f}pt, max |Δx| {g.worst_dx_asserted:.2f}pt; pages with movement "
            f"{moved or 'none'}; pages {g.ref_pages}→{g.out_pages}")
    if ref == "none":
        reason = (plan.get("geometry_reason") or "").strip()
        if not reason:
            return CheckResult("V7", "Geometry assertion", "FAIL",
                               "plan declares geometry_reference 'none' without a reason; a layout reference may only "
                               "be dropped for a stated reason", g.to_dict())
        return CheckResult("V7", "Geometry assertion", "NA",
                           f"N/A — no layout reference by design: {reason}. Informational: {info}", g.to_dict())
    if ref == "template":
        if tg is None:
            return CheckResult("V7", "Geometry assertion", "FAIL",
                               f"geometry_reference 'template' ({plan.get('geometry_template')}) but no comparison could "
                               f"be made (the template reference or its render, the headers/footers-only output render "
                               f"or the templating op's section map missing); refusing to pass without the reference",
                               g.to_dict())
        bad = [p for p in tg.pages if p.problems]
        return CheckResult(
            "V7", "Geometry assertion", "FAIL" if bad else "PASS",
            f"reference = the template's headers/footers as this document's plan fills them, at its own text "
            f"widths{' and its own header/footer distances' if keeps_distances(plan) else ''}, rendered alone "
            f"({Path(plan.get('geometry_template', '')).name}), "
            f"page for page against the output's ({'/'.join(tg.kinds)}): max |Δy| {tg.worst_dy:.2f}pt over "
            f"{sum(p.anchors for p in tg.pages)} anchor word(s) and every region edge on {len(tg.pages)} page(s) "
            f"(tol {GEOM_TOL_PT}pt)"
            + ("; " + "; ".join(f"page {p.page} (vs template page {p.template_page}): " + ", ".join(p.problems)
                                for p in bad[:4]) + (f"; … {len(bad) - 4} more page(s)" if len(bad) > 4 else "")
               if bad else ""),
            {**tg.to_dict(), "source_comparison": g.to_dict()})
    if ref != "source":
        return CheckResult("V7", "Geometry assertion", "FAIL", f"unknown geometry_reference {ref!r}", g.to_dict())
    if plan.get("mode") in ("content", "untracked") and plan.get("diffs"):
        # A wording change reflows the text that follows it; the source render is not a layout
        # reference for those pages. The numbers are recorded, not asserted.
        return CheckResult("V7", "Geometry assertion", "NA",
                           f"N/A for a content edit (text reflows after the change). Informational: {info}", g.to_dict())
    asserted = [p for p in g.pages if not p.expected_change]
    missing = [p.page for p in asserted if p.words_only_in_ref or p.words_only_in_out]
    evidence_problems = getattr(g, "evidence_problems", [])
    ok = ok and not missing and not evidence_problems
    expected_removed = sum(getattr(p, "expected_removed_words", 0) for p in g.pages)
    reference = ("source render with an independent header/footer-removal baseline "
                 f"({expected_removed} approved region word(s) removed)" if expected_removed else "source render")
    if ok and not any(p.matched_words for p in asserted):
        return CheckResult("V7", "Geometry assertion", "NA",
                           "N/A — asserted pages contain no matching text anchors; no source layout claim was proved. "
                           + info, g.to_dict())
    return CheckResult(
        "V7", "Geometry assertion", "PASS" if ok else "FAIL",
        f"reference = {reference}; max |Δy| {g.worst_dy_asserted:.2f}pt, max |Δx| {g.worst_dx_asserted:.2f}pt on asserted pages "
        f"(tol {GEOM_TOL_PT}pt); pages excluded as expected-to-change: {expected_pages or 'none'}"
        + (f"; MOVED pages {moved}" if moved else "") + ("" if g.ref_pages == g.out_pages else "; PAGE COUNT CHANGED")
        + (f"; rendered text missing or added on asserted pages {missing}" if missing else "")
        + ("; " + "; ".join(evidence_problems) if evidence_problems else ""),
        g.to_dict())


# ---- the batch -------------------------------------------------------------------------------------

def _run_header(batch: BatchResult, files: Sequence[Path], workers: int, renderer: str, do_render: bool,
                in_place: bool) -> List[str]:
    spec = batch.spec
    what = (f"EditSpec “{spec.get('request')}” — ops {', '.join(str(o.get('op')) for o in spec.get('ops', []))}"
            if spec is not None else f"job {batch.job} {diag.snip(json.dumps(batch.params, ensure_ascii=False), 400)}")
    return [f"Spec Surgeon {__version__} — run log",
            f"started      {batch.started}",
            f"results      {batch.out_dir}",
            f"request      {what}",
            f"inputs       {len(files)} file(s)" + (f"; the spec's template file(s) skipped: "
                                                    f"{[Path(x).name for x in batch.skipped]}" if batch.skipped else ""),
            f"options      workers {workers or 'auto'} · renderer {renderer if do_render else 'off'} · "
            f"{'write back in place (guarded)' if in_place else 'copies to outputs/'}",
            f"environment  {diag.environment_line(batch.environment)}",
            ""]


def _trace(fr: FileResult, stage: str, msg: str, block: str = "") -> List[str]:
    """Continue a file's trace from the parent process (render, delivery, the result)."""
    lines = diag.trace_event(stage, msg, block)
    fr.trace += lines
    diag.append_trace(fr.trace_path, lines)
    diag.event("file.checkpoint", source=str(fr.source), stage=stage, message=msg, details=block or None)
    return lines


def _file_messages(messages: Sequence[str], names: Sequence[str]) -> List[str]:
    """The renderer's lines about one file: those that start with the name of the .docx it was given, and the chunk
    failures that list it by name (never a line about a file whose name merely contains this one's)."""
    keys = [n for n in names if n]
    return [m for m in messages if any(m.startswith(f"{k}:") or f"'{k}'" in m for k in keys)]


def run_batch(inputs: Sequence[str], job_name: str, params: Dict[str, Any], out_dir: Path,
              workers: int = 0, renderer: str = "auto", do_render: bool = True, in_place: bool = False,
              progress=None, spec: Optional[Dict[str, Any]] = None, *, goal_contract=None, goal_bindings=None,
              approved_inputs=None, _input_snapshots=None, _public_spec=None,
              _source_identities=None, _source_refusals=None, enforce_goals: bool = False,
              worker_mode: Optional[str] = None, deliver_unverified: bool = False) -> BatchResult:
    """Run a batch with correlation, including validation before creating a results folder.

    Outcome data is advisory unless an explicit profile or governed caller opts into ``enforce_goals``.
    ``deliver_unverified`` (Obliterate mode only, CLAUDE.md, Modes): a copy that fails a check is
    delivered to ``outputs/`` with its record and its failed checks (``FileResult.unverified``) instead of being moved
    out — copies only; the source guard still holds.
    """
    with diag.scope(run_id=diag.context().get("run_id") or diag.new_id(), batch_id=diag.new_id()), \
            diag.span("batch", out_dir=str(out_dir), job=job_name, workers=workers,
                      renderer=renderer, do_render=do_render, in_place=in_place) as outcome:
        result = _run_batch(inputs, job_name, copy.deepcopy(params), out_dir, workers, renderer, do_render, in_place,
                            progress, copy.deepcopy(spec),
                            copy.deepcopy(goal_contract), copy.deepcopy(goal_bindings or {}),
                            copy.deepcopy(_input_snapshots or []), copy.deepcopy(_public_spec),
                            copy.deepcopy(approved_inputs), copy.deepcopy(_source_identities or {}),
                            copy.deepcopy(_source_refusals or {}), enforce_goals, worker_mode,
                            deliver_unverified and not in_place)
        outcome.update(counts=result.counts(), timings=result.timings)
        return result


def _run_batch(inputs: Sequence[str], job_name: str, params: Dict[str, Any], out_dir: Path,
               workers: int, renderer: str, do_render: bool, in_place: bool,
               progress, spec: Optional[Dict[str, Any]], goal_contract=None, goal_bindings=None,
               inherited_inputs=(), public_spec=None, approved_inputs=None,
               inherited_sources=None, inherited_refusals=None, enforce_goals=False,
               worker_mode=None, deliver_unverified=False) -> BatchResult:
    """Run an EditSpec (``spec``) over the inputs; ``job_name`` is only a label in the records.

    Everything the run does goes to ``<out>/logs/run.log`` (``progress`` gets the same progress lines), each file's
    path through the pipeline to ``<out>/logs/<name>.trace.log``, and what went wrong, file by file, to
    ``DIAGNOSTICS.md``. When the batch itself fails, run.log ends with the stage and the traceback (and DIAGNOSTICS.md
    says what was known) before the exception propagates."""
    out_dir = Path(out_dir)
    if (worker_mode or DEFAULT_WORKER_MODE) not in WORKER_MODES:
        raise ValueError("worker_mode must be auto, thread, serial or process")
    reported_spec = spec if public_spec is None else public_spec
    if approved_inputs is not None:
        digest = hashlib.sha256(json.dumps(reported_spec, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
        if (not isinstance(approved_inputs, dict) or approved_inputs.get("spec") != digest
                or not isinstance(approved_inputs.get("templates"), dict)):
            raise ValueError("The recipe or approval evidence changed since the preview; preview again before running.")
    goal_inputs = []
    if enforce_goals and goal_contract is not None:
        from .goals import goal_templates
        goal_inputs = goal_templates(goal_contract)  # validates the immutable outcome before any mutation
    if spec is None:
        raise ValueError("a batch runs an EditSpec (spec=...)")
    validate_spec(spec)              # fail early: questions, unsupported, unknown ops — before any file is read
    job_name = job_name or "edit-spec"
    params = {"request": spec.get("request"), "summary": spec.get("summary")}
    if in_place and spec is not None and spec_renames_outputs(spec):
        raise ValueError("this spec names the output files (name_output / make_values_agree with the file name) — "
                         "renamed files are delivered as copies only; run it without writing back in place (nothing "
                         "was written)")
    files = expand_inputs(inputs)
    auxiliary = spec_input_files(spec) if spec is not None else []
    check_inputs_outside([*files, *auxiliary, *goal_inputs], out_dir)  # includes every input delivery could destroy
    skipped_templates = [f for f in files if spec is not None and is_template_file(f, spec_templates(spec))]
    files = [f for f in files if f not in skipped_templates]
    from .input_snapshots import capture_sources, same_source_identity
    source_identities, source_refusals = capture_sources(files, approved_inputs)
    source_refusals.update(inherited_refusals or {})
    for path, identity in (inherited_sources or {}).items():
        if path in source_identities and not same_source_identity(identity, source_identities[path]):
            source_refusals[path] = {"code": "source-drift", "reason": "The original source changed after the chain started."}
        source_identities[path] = identity
    with diag.scope(out_dir / diag.LOGS / "events.jsonl"), \
            diag.span("batch.execute", file_count=len(files), job=job_name, workers=workers,
                      renderer=renderer, do_render=do_render, in_place=in_place) as execution:
        from .input_snapshots import snapshot_inputs
        out_dir.mkdir(parents=True, exist_ok=True)
        execution_spec, snapshots = snapshot_inputs(spec, out_dir,
            approved_inputs["templates"] if approved_inputs is not None else None, extra_paths=goal_inputs)
        clear_delivery_folders(out_dir)
        for sub in ("outputs", "records", "exceptions", "plans", "render", diag.LOGS):
            (out_dir / sub).mkdir(parents=True, exist_ok=True)
        batch = BatchResult(out_dir=out_dir, job=job_name, params=params, spec=reported_spec,
                            goal_contract=goal_contract, goal_bindings=goal_bindings or {}, input_snapshots=snapshots,
                            enforce_goals=enforce_goals)
        batch.input_snapshots.extend(inherited_inputs)
        batch.skipped = [str(f) for f in skipped_templates]
        batch.environment = diag.environment()
        diag.event("batch.environment", environment=batch.environment, skipped=batch.skipped)
        batch.started = datetime.now().astimezone().isoformat(timespec="seconds")
        batch.log_path = out_dir / diag.LOGS / diag.RUN_LOG
        log = diag.RunLog(batch.log_path, progress, _run_header(batch, files, workers, renderer, do_render, in_place))
        stage = "transform+verify"
        t_start = time.perf_counter()
        try:
            with diag.span("batch.transform_verify", file_count=len(files)):
                _transform_and_verify(batch, files, job_name, params, out_dir, workers, execution_spec, log,
                                      source_identities, source_refusals, worker_mode=worker_mode)
            stage = "render+geometry"
            stopping.check(stage)
            with diag.span("batch.render"):
                _render_and_compare(batch, renderer, do_render, workers, log)
            stage = "deliver"
            stopping.check(stage)              # the last point a stop lands: delivery, once begun, finishes
            with diag.span("batch.delivery"):
                if enforce_goals and goal_contract is not None:
                    _verify_delivery_goals(batch, goal_contract, goal_bindings or {}, log)
                _deliver(batch, in_place, log, deliver_unverified=deliver_unverified)
            stage = "records"
            with diag.span("batch.records"):
                _write_records(batch, job_name, params, reported_spec, log)
            batch.timings["total"] = time.perf_counter() - t_start
            stage = "reports"
            batch.finished = datetime.now().astimezone().isoformat(timespec="seconds")
            from .report import write_batch_report, write_diagnostics
            with diag.span("batch.reports"):
                write_batch_report(batch)
                write_diagnostics(batch)
            c = batch.counts()
            execution.update(counts=c, timings=batch.timings, skipped=batch.skipped)
            log.detail(f"finished: PASS {c['PASS']} · FAIL {c['FAIL']} · EXCEPTION {c['EXCEPTION']} — "
                       + ", ".join(f"{k} {v:.2f}s" for k, v in batch.timings.items()))
            return batch
        except BaseException as e:
            batch.stopped = f"{stage}: {type(e).__name__}: {e}"
            log.detail(f"THE BATCH STOPPED during {stage}: {type(e).__name__}: {e}")
            log.block(traceback.format_exc())
            try:                                    # say what is known: the files done so far and where it stopped
                from .report import write_diagnostics
                batch.finished = datetime.now().astimezone().isoformat(timespec="seconds")
                write_diagnostics(batch)
            except Exception as e2:  # noqa: BLE001 — the original error is the one that matters
                log.detail(f"(DIAGNOSTICS.md could not be written either: {type(e2).__name__}: {e2})")
            raise
        finally:
            log.close()


def _file_done(f: Path, r: Dict[str, Any], log: "diag.RunLog") -> None:
    log(f"  done {f.name}")                # ui/rows.ProgressTracker counts these lines — keep them exactly so
    exc = r.get("exception")
    diag.event("worker.result", source=str(f), status="exception" if exc else
               "pass" if (r.get("record") or {}).get("passed") else "fail",
               exception=exc, timings=r.get("timings"), trace_path=r.get("trace_path"))
    if exc:
        at = f" (at {exc.get('stage')}" + (f", op #{exc['op_number']} {exc.get('op')}" if exc.get("op_number") else "") \
            + ")" if exc.get("kind") != "refusal" else ""
        log(f"    EXCEPTION [{exc.get('code')}] {diag.snip(exc.get('reason'), 400)}{at}")
    elif r.get("record") and not r["record"].get("passed", True):
        bad = [c["id"] for c in r["record"].get("checks", []) if c.get("status") == "FAIL"]
        log(f"    verification FAILED: {', '.join(bad)} — the copy will not be delivered")
    log.block("\n".join(r.get("trace") or []), indent="      | ")


def _transform_and_verify(batch: BatchResult, files: List[Path], job_name: str, params: Dict[str, Any],
                          out_dir: Path, workers: int, spec: Optional[Dict[str, Any]], log: "diag.RunLog",
                          source_identities=None, source_refusals=None, *, worker_mode=None) -> None:
    """1-2. acquire, fingerprint, plan, apply, verify — in parallel (``worker_plan``); a worker that dies costs only
    its own file."""
    t_start = time.perf_counter()
    worker_mode, workers = worker_plan(worker_mode, workers, len(files))
    results: Dict[str, Dict[str, Any]] = {}
    log(f"{len(files)} file(s); transform+verify with {workers} {worker_mode} worker(s)"
        + (f"; skipped the spec's template file(s) {[Path(x).name for x in batch.skipped]}" if batch.skipped else ""))
    named, refused = planned_names(files, spec)      # the spec's own output names, when it gives them
    refused.update({str(f): source_refusals[str(f.resolve())] for f in files
                    if str(f.resolve()) in (source_refusals or {})})
    if named or refused:
        log(f"output names from the spec: {len(named)} file(s) renamed, {len(refused)} refused (two files one name)")
    out_names = output_names(files, named)            # unique across the batch, whatever the inputs are called
    for f in files:
        diag.event("worker.queued", source=str(f), output_name=out_names[str(f)], attempt=1, workers=workers,
                   naming_refusal=refused.get(str(f)))
    if workers == 1 or len(files) == 1:
        for f in files:
            stopping.check("transform+verify")
            results[str(f)] = _process_one(str(f), job_name, params, str(out_dir), out_names[str(f)], spec,
                                           refuse=refused.get(str(f)), trace_context=diag.context())
            _file_done(f, results[str(f)], log)
    else:
        broken: List[Path] = []
        with (_process_pool(workers) if worker_mode == "process" else ThreadPoolExecutor(max_workers=workers)) as ex:
            with diag.com_quiet():          # the pool starts its processes here; see diagnostics.com_quiet
                futs = {ex.submit(_process_one, str(f), job_name, params, str(out_dir), out_names[str(f)], spec, 1,
                                  refused.get(str(f)), diag.context()): f for f in files}
            for fut in stopping.as_completed(futs, "transform+verify", lambda: stop_pool(ex)):
                f = futs[fut]
                try:
                    r = fut.result()
                except BrokenProcessPool as e:
                    diag.event("worker.pool_broken", level="warning", source=str(f), attempt=1,
                               reason=str(e), retry_scheduled=True)
                    broken.append(f)
                    continue
                except Exception as e:  # noqa: BLE001 — the result could not come back: this file's record
                    r = _lost_result(f, out_dir, out_names[str(f)], e)
                results[str(f)] = r
                _file_done(f, r, log)
        if broken:
            log(f"a worker process stopped abruptly; running its {len(broken)} unfinished file(s) again, each alone "
                f"in a fresh process, to find the cause")
        for f in broken:
            stopping.check("transform+verify")
            results[str(f)] = _run_alone(f, job_name, params, out_dir, out_names[str(f)], spec, refused.get(str(f)))
            _file_done(f, results[str(f)], log)
    batch.timings["transform+verify"] = time.perf_counter() - t_start

    for f in files:
        r = results[str(f)]
        fr = FileResult(source=f, timings=r.get("timings", {}), noop=r.get("noop", False),
                        out_name=out_names[str(f)], output_md5=r.get("output_md5"),
                        source_sha256=r.get("source_sha256"))
        if r.get("identity"):
            fr.identity = guard.Identity(**r["identity"])
        fr.fingerprint = r.get("fingerprint", {})
        fr.plan = r.get("plan")
        fr.exception = r.get("exception")
        if r.get("output"):
            fr.output = Path(r["output"])
        if r.get("record"):
            fr.record = VerificationRecord.from_dict(r["record"])
        fr.trace_path = Path(r["trace_path"]) if r.get("trace_path") else None
        fr.trace = list(r.get("trace") or [])
        expected = (source_identities or {}).get(str(f.resolve()))
        if expected is not None:
            from .input_snapshots import same_source_identity
            mismatched = fr.identity is not None and not same_source_identity(expected, fr.identity)
            missing_on_success = fr.identity is None and fr.exception is None and fr.record is not None and fr.record.passed
            if mismatched or missing_on_success:
                reason = "The source changed between run entry and worker acquisition; these new bytes were not approved."
                details = {"expected_identity": expected.to_dict(),
                           "worker_identity": fr.identity.to_dict() if fr.identity else None,
                           "worker_error": fr.exception}
                fr.exception = diag.exception_record(JobException(reason, "source-drift", details), "acquire")
                if fr.record is not None:
                    fr.record.set(CheckResult("SOURCE", "Run-entry source identity", "FAIL", reason, details))
            fr.identity = expected
        if fr.exception is not None and fr.trace_path is not None:
            fr.exception["trace"] = f"{diag.LOGS}/{fr.trace_path.name}"
        batch.files.append(fr)


@dataclass
class RenderPair:
    """A document and the copy made from it, the plan between them and their renders: what V6 and V7 compare.
    ``source`` is the file the plan was made from (a chain stage's input); ``out_name`` the output's name, unique
    across the batch, which names the reference copies made for it."""
    source: Path
    output: Path
    plan: Dict[str, Any]
    source_pdf: Optional[Path] = None
    output_pdf: Optional[Path] = None
    out_name: str = ""


@dataclass
class GeometryReferences:
    """What V7 compares against beside the source render: for a templated output, its template reference — the
    template as the plan makes that document show it, headers and footers rendered alone, with the map of the
    output's sections to the reference's (verify/template_layout.py) — and the output's own headers and footers
    rendered alone; and the independent header/footer-removal references (a source reference whose plan removes
    headers or footers)."""
    template_refs: Dict[Path, Tuple[Path, List[int]]] = field(default_factory=dict)
    output_hf: Dict[Path, Tuple[Path, int]] = field(default_factory=dict)
    removed_hf_required: Set[Path] = field(default_factory=set)
    removed_hf_pdfs: Dict[Path, Path] = field(default_factory=dict)


def geometry_references(pairs: Sequence[RenderPair], out_dir: Path, backend: str, workers: int, cache,
                        rlog: Callable[[str], None], log: Any) -> GeometryReferences:
    """Render the references V7 needs for ``pairs`` — the run's and the preview's alike, so a sample's verdict in the
    preview is the one the run gives. ``rlog`` takes what the renderer says (a line per file); ``log`` is called for
    progress and has ``detail`` and ``block``."""
    refs = GeometryReferences()
    # V7 template mode compares headers/footers-only renders of the output and of its template reference, page for
    # page; the reference is made per output, from its plan and its sections (the fills and the text widths are the
    # document's own: live QA finding F08)
    templated = [p for p in pairs if p.plan.get("geometry_reference") == "template"]
    if templated:
        hf_dir, tpl_dir = out_dir / "render" / "output-hf", out_dir / "render" / "template"
        copies, references = {}, {}
        for k, p in enumerate(templated):
            try:
                # numbered, never named after the file: the path stays short of Windows' 260 characters (the
                # owner's 1.10.0 run: an 84-character name under a recovery attempt's folders made 261)
                path, n = geom.hf_only_copy(p.output, hf_dir / f"{k:03d} headers-footers.docx")
                copies[path] = (p.output, n)
            except Exception as e:  # noqa: BLE001
                rlog(f"{p.output.name}: its headers/footers-only copy could not be made ({type(e).__name__}: {e})")
                log.block(traceback.format_exc())
                continue
            top = _template_op_params(p.plan)
            try:
                if top is None:
                    raise ValueError("the plan's templating op states no template_sections")
                data, smap = template_reference(p.plan, p.output.read_bytes(),
                                                geom.template_section_map(top["template_sections"], n),
                                                top.get("kinds", ["header", "footer"]), source=p.source.read_bytes())
                references[geom.hf_only_copy(data, tpl_dir / f"{k:03d} headers-footers.docx")[0]] = (p.output, smap)
            except Exception as e:  # noqa: BLE001 — its V7 fails for want of the reference, and says so
                rlog(f"{p.output.name}: its template reference could not be made ({type(e).__name__}: {e})")
                log.block(traceback.format_exc())
        log(f"rendering {len(references)} template reference(s) and {len(copies)} headers/footers-only output(s) "
            f"with {backend}")
    removed_hf_inputs: Dict[Path, Path] = {}
    for p in pairs:
        plan = p.plan
        if plan.get("geometry_reference", "source") != "source" or (plan.get("mode") in ("content", "untracked")
                                                                   and plan.get("diffs")):
            continue
        try:
            reference = geom.hf_removal_copy(p.source, out_dir / "render" / "removed-hf-reference" /
                                             (p.out_name or p.source.name), plan)
            if reference is not None:
                refs.removed_hf_required.add(p.source)
                removed_hf_inputs[reference] = p.source
        except Exception as exc:  # noqa: BLE001
            refs.removed_hf_required.add(p.source)
            rlog(f"{p.source.name}: the independent header/footer-removal reference could not be made "
                 f"({type(exc).__name__}: {exc})")
            log.block(traceback.format_exc())
    if removed_hf_inputs:
        log(f"rendering {len(removed_hf_inputs)} independent header/footer-removal reference(s) with {backend}")
    # every reference renders in one round of renderer processes, side by side
    groups = []
    if templated:
        groups += [(list(references), tpl_dir), (list(copies), hf_dir)]
    if removed_hf_inputs:
        groups.append((list(removed_hf_inputs), out_dir / "render" / "removed-hf-reference-pdf"))
    if not groups:
        return refs
    rendered = render_mod.render_pdf_groups(groups, backend, workers, log=rlog, detail=log.detail, cache=cache)
    if templated:
        tpl_pdfs, hf_pdfs, rendered = rendered[0], rendered[1], rendered[2:]
        for k, v in tpl_pdfs.items():
            if k in references:
                refs.template_refs[references[k][0]] = (v, references[k][1])
        for k, v in hf_pdfs.items():
            if k in copies:
                refs.output_hf[copies[k][0]] = (v, copies[k][1])
    if removed_hf_inputs:
        refs.removed_hf_pdfs = {removed_hf_inputs[path]: pdf for path, pdf in rendered[0].items()
                                if path in removed_hf_inputs}
    return refs


def compare_pair(pair: RenderPair, refs: GeometryReferences, backend: str,
                 pages_dir: Optional[Path] = None) -> Tuple[CheckResult, CheckResult, "geom.GeometryResult"]:
    """V6 and V7 of one rendered pair, from the plan's geometry reference (``geometry_check``) and the references
    rendered for it. ``pages_dir`` rasterizes the output's pages to look at (the run; the preview makes its own
    before/after pictures). Raises when the comparison cannot be made: the caller's V6 FAIL."""
    plan = pair.plan
    sp, op = pair.source_pdf, pair.output_pdf
    expected_pages = plan.get("geometry_pages_expected_to_change", [])
    removed_reference = refs.removed_hf_pdfs.get(pair.source)
    removal = ({"removed_hf_pdf": removed_reference, "removed_hf_source": pair.source} if removed_reference else {})
    # with the plan and the source, the comparison also counts the words shown over the whole document (V6's warning,
    # ``geom.shown_words``: a copy that hides text without moving a word the page-for-page comparison sees)
    g = geom.compare_pdfs(sp, op, expected_pages, plan=plan, source_docx=pair.source, **removal)
    look_pages = sorted({1} | {p.page for p in g.pages if p.max_dy > GEOM_TOL_PT or p.words_only_in_out or p.words_only_in_ref})
    pngs = geom.rasterize(op, pages_dir, look_pages[:6]) if pages_dir is not None else []
    data = {"renderer": backend, "source_pdf": str(sp), "output_pdf": str(op), "pages": [str(p) for p in pngs],
            **g.shown_words_data()}
    missing = words_missing_sentence(data)
    v6 = CheckResult(
        "V6", "Automated render comparison", "PASS",
        f"renderer {backend}; pages {g.ref_pages}→{g.out_pages}; "
        + (f"rasterized pages {look_pages[:6]} → {pages_dir.name}/; " if pages_dir is not None
           else f"pages to look at {look_pages[:6]}; ")
        + "page-count delta is not evidence of body change (XML checks are character truth)"
        + (f"; {missing}" if missing else ""),
        data)
    tg = None
    top = _template_op_params(plan)
    if (plan.get("geometry_reference") == "template" and pair.output in refs.template_refs
            and pair.output in refs.output_hf and top is not None):
        ref_pdf, smap = refs.template_refs[pair.output]
        tg = geom.compare_template_bands(ref_pdf, refs.output_hf[pair.output][0], smap,
                                         top.get("kinds", ["header", "footer"]), GEOM_TOL_PT)
    if pair.source in refs.removed_hf_required and not removed_reference:
        v7 = CheckResult("V7", "Geometry assertion", "FAIL",
                         "The independent header/footer-removal reference could not be rendered; "
                         "refusing a source-layout claim without that evidence.", g.to_dict())
    else:
        v7 = geometry_check(plan, g, tg)
    return v6, v7, g


def _render_and_compare(batch: BatchResult, renderer: str, do_render: bool, workers: int, log: "diag.RunLog") -> None:
    """3-4. render + geometry (V6/V7). A renderer that cannot run, a file it cannot convert, or a comparison that
    fails on one file is that file's failed V6 — with what the renderer said — never a crashed batch. The references
    and the comparison are ``geometry_references`` and ``compare_pair``, which the preview runs on its samples too."""
    out_dir = batch.out_dir
    t_r = time.perf_counter()
    backend = render_mod.pick(renderer) if do_render else None
    diag.event("render.selected", requested=renderer, enabled=do_render, backend=backend)
    batch.renderer = backend
    renderable = [fr for fr in batch.files if fr.output is not None and fr.record is not None]
    workers = workers or max(1, min(os.cpu_count() or 2, len(batch.files) or 1, 8))
    if do_render and backend is None:
        log.detail(f"no renderer: {render_mod.unavailable_reason(renderer)}")
    if backend:
        log.detail(f"renderer: {render_mod.describe(backend)}")
    messages: List[str] = []

    def rlog(msg: str) -> None:
        messages.append(msg)
        log(msg)
    if backend and renderable:
        log(f"rendering {2 * len(renderable)} PDF(s) with {backend}")
        # the cache beside the session folders: a source the preview rendered, or any file whose bytes were rendered
        # before by this renderer, is served from it instead of rendered again (keyed by bytes, never by name)
        cache = render_mod.RenderCache.for_out_dir(out_dir)
        if cache is not None:
            log.detail(f"render cache: {cache.root}")
        # a source whose name another source has too is rendered from a copy carrying its unique output name, so
        # two sources called Spec.docx never share one Spec.pdf (V7 would compare one of them with the other's)
        src_in: Dict[Path, Path] = {}
        for fr in renderable:
            if fr.out_name and fr.out_name != fr.source.name:
                cp = out_dir / "render" / "source-in" / fr.out_name
                cp.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(fr.source, cp)
                src_in[cp] = fr.source
            else:
                src_in[fr.source] = fr.source
        # the sources and the copies render side by side, in one round of renderer processes
        rendered, out_pdfs = render_mod.render_pdf_groups(
            [(list(src_in), out_dir / "render" / "source"),
             ([fr.output for fr in renderable], out_dir / "render" / "output")],
            backend, workers, log=rlog, detail=log.detail, cache=cache)
        src_pdfs = {src_in[k]: v for k, v in rendered.items() if k in src_in}
        pairs = [RenderPair(fr.source, fr.output, fr.plan or {}, src_pdfs.get(fr.source), out_pdfs.get(fr.output),
                            fr.out_name) for fr in renderable]
        refs = geometry_references(pairs, out_dir, backend, workers, cache, rlog, log)
        pages_dir = out_dir / "render" / "pages"
        for fr, pair in zip(renderable, pairs):
            stopping.check("render+geometry")
            fr.render_messages = _file_messages(messages, [fr.source.name, fr.out_name])
            sp, op = pair.source_pdf, pair.output_pdf
            if not sp or not op:
                said = fr.render_messages
                fr.record.set(CheckResult("V6", "Automated render comparison", "FAIL",
                                          f"render failed ({backend})" + (f": {said[-1]}" if said else
                                                                          ": no PDF came back and the renderer said "
                                                                          "nothing about this file (see logs/run.log)"),
                                          {"renderer": backend, "messages": said,
                                           "missing": [x for x, ok in (("source", sp), ("output", op)) if not ok]}))
                fr.record.set(CheckResult("V7", "Geometry assertion", "NA", "no render"))
                _trace(fr, "render", f"V6 FAIL — {fr.record.get('V6').summary}")
                continue
            try:
                with diag.span("render.compare", source=str(fr.source), source_pdf=str(sp), output_pdf=str(op)):
                    v6, v7, _geometry = compare_pair(pair, refs, backend, pages_dir)
                    fr.record.set(v6)
                    fr.record.set(v7)
            except Exception as e:  # noqa: BLE001 — a comparison that cannot run is this file's failure, not a skip
                tb = traceback.format_exc()
                fr.record.set(CheckResult("V6", "Automated render comparison", "FAIL",
                                          f"the rendered pages could not be compared ({type(e).__name__}: {e}); the "
                                          f"traceback is in the file's trace", {"renderer": backend, "traceback": tb}))
                fr.record.set(CheckResult("V7", "Geometry assertion", "NA", "no comparison (V6 failed)"))
                _trace(fr, "render", f"V6 FAIL — {fr.record.get('V6').summary}", tb)
                log.detail(f"{fr.source.name}: the render comparison failed")
                log.block(tb)
                continue
            failures = {cid: details for cid, _, _, details in fr.record.failures()}
            for cid in ("V6", "V7"):
                c = fr.record.get(cid)
                _trace(fr, "render", f"{cid} {c.status} — {diag.snip(c.summary, 500)}",
                       "\n".join(f"- {d}" for d in failures.get(cid, [])))
        try:
            batch.contact_sheet = geom.contact_sheet([out_pdfs[fr.output] for fr in renderable if fr.output in out_pdfs],
                                                    out_dir / "render" / "contact_sheet.png")
        except Exception as e:  # pragma: no cover
            log(f"contact sheet failed: {e}")
    else:
        why = ("rendering disabled" if not do_render else
               "no renderer available (install Word+pywin32 or LibreOffice)")
        for fr in renderable:
            fr.record.set(CheckResult("V6", "Automated render comparison", "NA", why))
            fr.record.set(CheckResult("V7", "Geometry assertion", "NA", why))
            _trace(fr, "render", f"V6/V7 NA — {why}")
    batch.timings["render+geometry"] = time.perf_counter() - t_r


def _deliver(batch: BatchResult, in_place: bool, log: "diag.RunLog", deliver_unverified: bool = False) -> None:
    """5. deliver (guarded, V9). ``deliver_unverified`` (copies only): a copy whose checks failed stays in
    ``outputs/`` and is delivered, its failed checks in ``FileResult.unverified`` and V9's summary — when the source is
    unchanged and no auxiliary input moved, as for any delivery."""
    out_dir = batch.out_dir
    from .input_snapshots import changed_inputs
    t_d = time.perf_counter()
    for fr in batch.files:
        if fr.record is None or fr.output is None:
            diag.event("delivery.skipped", source=str(fr.source), status=fr.status,
                       reason="no verified output was produced")
            continue
        t_file = time.perf_counter()
        diag.event("delivery.started", source=str(fr.source), output=str(fr.output),
                   in_place=in_place, noop=fr.noop, verified=fr.record.passed)
        anyway = deliver_unverified and not in_place and not fr.record.passed
        if not fr.record.passed and not anyway:
            moved = quarantine_failed_output(fr, out_dir)
            fr.record.set(CheckResult("V9", "Overwrite guard", "NA",
                                      f"not delivered: verification failed; the output was moved out of outputs/ to "
                                      f"{moved.parent.name}/{moved.name} (diagnosis only)"))
        elif (changed := changed_inputs(batch.input_snapshots)):
            moved = quarantine_failed_output(fr, out_dir)
            fr.record.set(CheckResult("V9", "Overwrite guard", "FAIL",
                                      "Auxiliary input changed since acquisition: " + ", ".join(changed)
                                      + f"; not delivered; output moved to {moved.parent.name}/{moved.name}"))
        elif in_place and not fr.noop:
            try:
                new_id = guard.write_back(fr.identity, verified_bytes(fr))
                fr.delivered_to = new_id.path
                fr.record.set(CheckResult("V9", "Overwrite guard", "PASS",
                                          f"source re-checked (size {fr.identity.size}, mtime unchanged); no Word lock file; "
                                          f"atomic replace; new md5 {new_id.md5}"))
            except (guard.GuardError, OSError) as e:
                # V9 failed after V1–V8/V10 passed: the output is still a copy that was never delivered — out of
                # outputs/ it goes, like any other failed check, so the folder keeps its guarantee. (An OSError
                # here is one the guard did not translate — reading the output copy, say; it is this file's
                # failure, never the batch's.)
                moved = quarantine_failed_output(fr, out_dir)
                fr.record.set(CheckResult("V9", "Overwrite guard", "FAIL",
                                          f"{e} — not delivered; the output was moved out of outputs/ to "
                                          f"{moved.parent.name}/{moved.name} (diagnosis only; run again once the source is "
                                          f"closed and unchanged)"))
        else:
            d = None
            try:
                d = guard.drifted(fr.identity)
                verified_bytes(fr)                 # the copy in outputs/ is the one the suite verified
            except (guard.GuardError, OSError) as e:
                d = d or str(e)
            if not d:
                if anyway:
                    fr.delivered_to = str(fr.output)
                    fr.unverified = [f"{c.id} ({c.name})" for c in fr.record.checks if c.status == "FAIL"]
                    fr.record.set(CheckResult("V9", "Overwrite guard", "NA",
                                              f"delivered UNVERIFIED at the user's word (Obliterate mode): "
                                              f"{', '.join(fr.unverified)} failed; source unchanged since acquisition; "
                                              f"output written to {fr.output}"))
                elif fr.noop:
                    fr.record.set(CheckResult("V9", "Overwrite guard", "NA",
                                              "no-op: verified output identical to unchanged source; nothing written back"))
                else:
                    fr.delivered_to = str(fr.output)
                    fr.record.set(CheckResult("V9", "Overwrite guard", "PASS",
                                              "source unchanged since acquisition; output written to " + str(fr.output)))
            else:
                moved = quarantine_failed_output(fr, out_dir)
                fr.record.set(CheckResult("V9", "Overwrite guard", "FAIL",
                                          f"SOURCE DRIFTED during the run: {d} — the output was built from a stale source and "
                                          f"moved out of outputs/ to {moved.parent.name}/{moved.name} (diagnosis only); run "
                                          f"again on the current file"))
        v9 = fr.record.get("V9")
        fr.timings["deliver"] = time.perf_counter() - t_file
        diag.event("delivery.finished", level="warning" if v9.status == "FAIL" else "info",
                   source=str(fr.source), status=v9.status, reason=v9.summary,
                   delivered_to=fr.delivered_to, duration_ms=fr.timings["deliver"] * 1000)
        _trace(fr, "deliver", f"V9 {v9.status} — {v9.summary}")
        if v9.status == "FAIL":
            log(f"  {fr.source.name}: V9 FAIL — {diag.snip(v9.summary, 400)}")
    batch.timings["deliver"] = time.perf_counter() - t_d


def _result_line(fr: FileResult) -> str:
    if fr.status == "PASS":
        return ("PASS — nothing to change (the output would be identical to the source)" if fr.noop and not fr.delivered_to
                else f"PASS — delivered to {fr.delivered_to}" if fr.delivered_to else "PASS")
    if fr.status == "FAIL":
        bad = [c.id for c in fr.record.checks if c.status == "FAIL"]
        if fr.unverified and fr.delivered_to:
            return f"FAIL — delivered UNVERIFIED to {fr.delivered_to} ({', '.join(bad)} failed; Obliterate mode)"
        return (f"FAIL — not delivered ({', '.join(bad)} failed); the edited copy is in "
                f"{Path(fr.output).parent.name}/{Path(fr.output).name}" if fr.output else f"FAIL ({', '.join(bad)})")
    e = fr.exception or {}
    return f"EXCEPTION [{e.get('code')}] at {e.get('stage', '?')} — the file was left untouched"


def _write_records(batch: BatchResult, job_name: str, params: Dict[str, Any], spec: Optional[Dict[str, Any]],
                   log: "diag.RunLog") -> None:
    """6. records, exceptions, plans — named after the output (unique across the batch), never after the source
    alone; each file's trace gets its result line."""
    out_dir = batch.out_dir
    for fr in batch.files:
        stem = fr.record_stem
        if fr.plan is not None:
            (out_dir / "plans" / f"{stem}.plan.json").write_text(json.dumps(fr.plan, indent=2), encoding="utf-8")
        if fr.exception is not None:
            (out_dir / "exceptions" / f"{stem}.exception.json").write_text(json.dumps({
                "source": str(fr.source), "job": job_name, "params": params,
                **({"spec": spec} if spec is not None else {}),
                "fingerprint": fr.fingerprint, "exception": fr.exception}, indent=2), encoding="utf-8")
        if fr.record is not None:
            (out_dir / "records" / f"{stem}.record.md").write_bytes(fr.record.to_markdown().encode("utf-8"))
            (out_dir / "records" / f"{stem}.record.json").write_text(json.dumps(fr.record.to_dict(), indent=2), encoding="utf-8")
        line = _result_line(fr)
        diag.event("file.result", source=str(fr.source), status=fr.status, summary=line,
                   delivered_to=fr.delivered_to, timings=fr.timings, record_stem=stem)
        _trace(fr, "result", line)
        log.detail(f"  {fr.source.name}: {line}")
