"""Diagnostics — when something fails: where it stopped, why, and how it got there.

What a run leaves behind for this, beside its records and reports:

    logs/run.log            the run's own log, timestamped: the environment (versions, platform, renderer), every
                            progress line, the renderer's own messages, every file's trace, and the traceback of
                            anything unexpected — the batch's own failure included
    logs/<name>.trace.log   one file's path through the pipeline: acquire → open → fingerprint → plan (each op) →
                            apply (each op) → verify → write, then render and deliver — each stage with its time, what
                            it found, and where the file stopped (the stage, the op, the line of code that refused or
                            failed; the traceback when the engine itself failed; the failing checks' specifics).
                            Written line by line as the file goes, so a worker that dies or hangs still shows the
                            last stage it reached; in a pool worker Python's fault handler adds the stack of a hard
                            crash, and a file still running after HANG_DUMP_SECONDS gets a stack dump of where it is
    DIAGNOSTICS.md          (``report.write_diagnostics``) every file that did not pass: what happened, where, why,
                            how, what to do next, and which files to read

Failure kinds (``classify``):

* ``refusal`` — the engine declined on purpose (a ``JobException`` code: ``not-found``, ``structure`` …);
* ``unreadable`` — the file is not a Word document the engine can open: not a zip, an encrypted or old binary file
  named .docx, a part that is not well-formed XML, a file the disk would not give back (``unsupported-format`` for
  Strict Open XML and ``xml-dtd`` for a part with a document type declaration are the same kind with their own codes);
* ``environment`` (code ``io-error``) — a file the run needs could not be read or written (the template, the results
  folder): permissions, a full disk, a path too long for Windows;
* ``internal-error`` — anything else unexpected: a defect in Spec Surgeon, never the file's fault;
* ``worker-died`` — the process working on the file ended abruptly (out of memory, a crash in a native library).

The window keeps an application log (``setup_app_logging``: its activity log and every error with its traceback,
rotating) and a crash watch per session (``CrashWatch``: Python's fault handler for hard crashes, and a marker that
tells the next start the previous session ended without closing).
"""
from __future__ import annotations

import errno
import faulthandler
import json
import locale
import logging
import os
import platform
import re
import sys
import threading
import time
import traceback
import zipfile
import zlib
from contextlib import contextmanager
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence

from . import __version__
from .telemetry import (APP_EVENTS, RedactingFormatter, SafeRotatingHandler, context, context_scope,
                        diagnostic_failure, error_fields, event, install_exception_hooks, new_id,
                        redact_text, scope, setup_events, span)

LOGS = "logs"
RUN_LOG = "run.log"
APP_LOG = "spec-surgeon.log"
HANG_DUMP_SECONDS = 300          # a file still in the pipeline after this long gets a stack dump in its trace
TRACE_INDENT = " " * 26          # under the message column of a trace line
PRE_TRANSFORM = ("acquire", "open", "fingerprint", "plan")

logging.getLogger("spec_surgeon").addHandler(logging.NullHandler())   # a library logs nowhere until an app says where


def now() -> str:
    """Wall-clock time of day with milliseconds — the same clock in every process of a run."""
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def ms(t0: float) -> str:
    """Elapsed time since ``t0`` (``time.perf_counter``) for a trace line."""
    dt = time.perf_counter() - t0
    return f"{dt * 1000:.0f} ms" if dt < 10 else f"{dt:.1f} s"


def trace_path(out_dir: Path, out_name: str) -> Path:
    """A file's trace, named after its output (unique across the batch, like its record)."""
    return Path(out_dir) / LOGS / f"{Path(out_name).stem}.trace.log"


def snip(text: Any, n: int = 300) -> str:
    s = str(text)
    return s if len(s) <= n else s[:n - 1] + "…"


def compact(value: Any, n: int = 600) -> str:
    """A value on one line, JSON where it can be (details of a refusal, say)."""
    try:
        s = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = repr(value)
    return snip(s, n)


# ---- the environment ------------------------------------------------------------------------------------------------

def _dist_version(dist: str, module: Optional[str] = None) -> Optional[str]:
    """An installed distribution's version without importing it; the module's own version when it is loaded and the
    metadata is not there (the installed app keeps little metadata)."""
    try:
        from importlib.metadata import version
        return version(dist)
    except Exception:  # noqa: BLE001 — PackageNotFoundError, or no metadata in a frozen app
        pass
    mod = sys.modules.get(module or dist)
    v = getattr(mod, "__version__", None) or getattr(mod, "VersionBind", None)
    return str(v) if v else None


def environment(**extra: Any) -> Dict[str, Any]:
    """What a diagnosis needs to know about the computer and the program: versions, platform, encodings."""
    env: Dict[str, Any] = {
        "spec_surgeon": __version__,
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "executable": sys.executable,
        "frozen": bool(getattr(sys, "frozen", False)),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpus": os.cpu_count(),
        "pid": os.getpid(),
        "filesystem_encoding": sys.getfilesystemencoding(),
        "preferred_encoding": locale.getpreferredencoding(False),
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    try:
        from lxml import etree
        env["lxml"] = getattr(etree, "__version__", None) or ".".join(str(x) for x in etree.LXML_VERSION)
        env["libxml2"] = ".".join(str(x) for x in etree.LIBXML_VERSION)
    except ImportError:  # pragma: no cover — lxml is a hard dependency
        env["lxml"] = None
    env["pymupdf"] = _dist_version("PyMuPDF", "pymupdf")
    env["pywebview"] = _dist_version("pywebview", "webview")
    env["fastapi"] = _dist_version("fastapi")
    env["uvicorn"] = _dist_version("uvicorn")
    env["anthropic"] = _dist_version("anthropic")
    if sys.platform.startswith("win"):
        env["pywin32"] = _dist_version("pywin32", "win32api")
    env.update(extra)
    return env


def environment_line(env: Dict[str, Any]) -> str:
    bits = [f"Spec Surgeon {env.get('spec_surgeon')}",
            f"Python {env.get('python')}" + (" (installed app)" if env.get("frozen") else ""),
            str(env.get("platform"))]
    if env.get("lxml"):
        bits.append(f"lxml {env['lxml']} (libxml2 {env.get('libxml2')})")
    for key, label in (("pymupdf", "PyMuPDF"), ("pywebview", "pywebview"), ("fastapi", "FastAPI"),
                       ("uvicorn", "uvicorn"), ("anthropic", "anthropic"), ("pywin32", "pywin32")):
        if env.get(key):
            bits.append(f"{label} {env[key]}")
    return " · ".join(bits)


# ---- what failed, and why -------------------------------------------------------------------------------------------

_MAGIC = [
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "an OLE compound file — a password-protected (encrypted) Word document, or an "
                                          "old Word 97–2003 .doc saved under a .docx name"),
    (b"%PDF", "a PDF file saved under a .docx name"),
    (b"{\\rtf", "an RTF file saved under a .docx name"),
    (b"MZ", "a Windows program, not a document"),
]


def sniff(data: Optional[bytes]) -> Optional[str]:
    """What a file that will not open as a .docx actually is, judged by its first bytes (None when nothing is
    recognised)."""
    if data is None:
        return None
    if not data:
        return ("the file is empty (0 bytes) — an interrupted copy or download, or a OneDrive/SharePoint placeholder "
                "whose content never arrived")
    head = data[:4096]
    for magic, what in _MAGIC:
        if head.startswith(magic):
            return what
    if not head.strip(b"\x00"):
        return "the file holds only zero bytes — damaged, or a cloud placeholder whose content never arrived"
    lead = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if lead.startswith((b"<html", b"<!doctype html")):
        return "an HTML page saved under a .docx name (a download that saved an error or sign-in page?)"
    if lead.startswith((b"<?xml", b"<w:", b"<pkg:")):
        return "a Word XML file (Word 2003 XML or Flat OPC), not a .docx package"
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return "a zip archive that is damaged or cut short (an incomplete copy or download?)"
    try:
        text = head[:200].decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is not None and all(ch.isprintable() or ch in "\r\n\t" for ch in text):
        first = " ".join(text.split())[:40]
        return f"a plain text file saved under a .docx name (it begins “{first}”)"
    return f"not a Word file (its first bytes are {head[:8].hex(' ')})"


def malformed_parts(data: Optional[bytes], limit: int = 5) -> List[str]:
    """The package's XML members that are not well-formed, each with the parser's message (an empty list when they
    all parse, or when the bytes are not a zip at all)."""
    if not data:
        return []
    from lxml import etree
    import io
    out: List[str] = []
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError, OSError):
        return []
    parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False, huge_tree=True)
    for info in z.infolist():
        if not info.filename.lower().endswith((".xml", ".rels")):
            continue
        try:
            etree.fromstring(z.read(info.filename), parser)
        except etree.XMLSyntaxError as e:
            out.append(f"{info.filename}: {e}")
        except Exception as e:  # noqa: BLE001 — a member that cannot even be decompressed
            out.append(f"{info.filename}: {type(e).__name__}: {e}")
        if len(out) >= limit:
            break
    return out


_WIN_HINTS = {
    2: "the file is gone — moved, renamed or deleted after the run started",
    3: "the folder is gone, or the path is too long for Windows (over 260 characters)",
    5: "access is denied — you may not read it, or it is a folder",
    32: "another program holds the file open exclusively (a sync tool, a backup or virus scanner)",
    33: "another program has locked part of the file",
    112: "the disk is full",
    206: "the path is too long for Windows (over 260 characters): shorten the folder or file names, or move the "
         "folder nearer the drive root",
}
_ERRNO_HINTS = {
    errno.ENOENT: "the file or folder is gone — moved, renamed or deleted after the run started",
    errno.EACCES: "permission denied",
    errno.ENOSPC: "the disk is full",
    errno.ENAMETOOLONG: "the path is too long",
}


def os_error_hint(e: OSError) -> Optional[str]:
    """A plain-English reading of an OS error (Windows codes first: they are the precise ones there)."""
    win = getattr(e, "winerror", None)
    text = (getattr(e, "strerror", None) or "").lower()
    if "cloud" in text or (isinstance(win, int) and 358 <= win <= 430):
        return ("a OneDrive/SharePoint online-only file that could not be downloaded: start OneDrive (signed in), or "
                "right-click the folder → Always keep on this device")
    if isinstance(win, int) and win in _WIN_HINTS:
        return _WIN_HINTS[win]
    return _ERRNO_HINTS.get(getattr(e, "errno", None))


def _is_missing_member(e: BaseException) -> bool:
    msg = str(e.args[0]) if getattr(e, "args", None) else ""
    return isinstance(e, KeyError) and (msg.startswith("no member ") or msg.startswith("There is no item named"))


def classify(exc: BaseException, stage: str, data: Optional[bytes] = None) -> Dict[str, Any]:
    """``{"kind", "code", "reason"[, "hint"]}`` for an exception that stopped a file at ``stage``.

    A refusal keeps its own code. A file the engine cannot open is ``unreadable`` with what its bytes say it is (an
    encrypted document, an old .doc, a PDF, a damaged zip, a part that is not well-formed XML); a file or folder the
    run needs that the disk will not give is ``io-error``; everything else is ``internal-error`` — the engine's
    fault, never the file's."""
    from lxml import etree
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        kind = "refusal"
        if code in ("unsupported-format", "xml-dtd"):
            kind = "unreadable"
        elif code == "internal":                       # an op's apply-time cross-check disagreed with its own plan
            kind = "internal-error"
        return {"kind": kind, "code": code, "reason": str(getattr(exc, "reason", None) or exc)}
    what = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, OSError):
        hint = os_error_hint(exc)
        if stage in ("acquire", "open"):
            return {"kind": "unreadable", "code": "unreadable",
                    "reason": f"the file could not be read from disk: {what}" + (f" — {hint}" if hint else ""),
                    **({"hint": hint} if hint else {})}
        return {"kind": "environment", "code": "io-error",
                "reason": f"a file or folder the run needs could not be read or written: {what}"
                          + (f" — {hint}" if hint else ""), **({"hint": hint} if hint else {})}
    if isinstance(exc, MemoryError):
        return {"kind": "internal-error", "code": "internal-error",
                "reason": f"out of memory while at '{stage}' ({what})",
                "hint": "close other programs or run fewer workers; a very large file may need to be processed alone"}
    zipish = isinstance(exc, (zipfile.BadZipFile, zlib.error, EOFError)) or (
        stage == "open" and isinstance(exc, (NotImplementedError, RuntimeError, ValueError)))
    if zipish and stage in PRE_TRANSFORM:
        seen = sniff(data)
        return {"kind": "unreadable", "code": "unreadable",
                "reason": f"not a readable Word (.docx) package ({what})" + (f" — the file is {seen}" if seen else ""),
                **({"details": {"content": seen}} if seen else {})}
    if isinstance(exc, (etree.XMLSyntaxError, UnicodeDecodeError)):
        bad = malformed_parts(data)
        if bad or stage in PRE_TRANSFORM:
            where = f"; {bad[0]}" if bad else ""
            return {"kind": "unreadable", "code": "unreadable",
                    "reason": f"a part of the file is not well-formed XML ({what}{where}) — Word would offer to repair "
                              f"it: open it in Word, let it repair, save, and run again",
                    **({"details": {"malformed_parts": bad}} if bad else {})}
    if _is_missing_member(exc) and stage in PRE_TRANSFORM:
        return {"kind": "unreadable", "code": "unreadable",
                "reason": f"the package lacks a part it needs or refers to ({what}) — not a complete Word document; "
                          f"open it in Word, let it repair, save, and run again"}
    return {"kind": "internal-error", "code": "internal-error",
            "reason": f"Spec Surgeon failed unexpectedly while at '{stage}': {what} — a defect in the program, not a "
                      f"problem with the file"}


def _ours(filename: str) -> Optional[str]:
    """``ops/replace_text.py`` for a frame inside this package (installed, from source, or frozen), else None."""
    parts = re.split(r"[\\/]+", filename)
    if "spec_surgeon" not in parts:
        return None
    i = len(parts) - 1 - parts[::-1].index("spec_surgeon")
    rest = "/".join(parts[i + 1:])
    return rest[:-1] if rest.endswith(".pyc") else rest


def innermost(exc: BaseException) -> BaseException:
    """The exception at the bottom of a chain (``raise … from e``, or one raised while handling another)."""
    seen = {id(exc)}
    e = exc
    while True:
        nxt = e.__cause__ if e.__cause__ is not None else (None if e.__suppress_context__ else e.__context__)
        if nxt is None or id(nxt) in seen:
            return e
        seen.add(id(nxt))
        e = nxt


def raise_site(exc: BaseException) -> Optional[str]:
    """Where in Spec Surgeon an exception came from: ``ops/replace_text.py:211 in plan`` — the last frame inside the
    package of the innermost exception of its chain (an op's refusal re-raised by the EditSpec planner points at the
    op)."""
    for e in (innermost(exc), exc):
        frames = traceback.extract_tb(e.__traceback__) if e.__traceback__ is not None else []
        for fr in reversed(frames):
            rel = _ours(fr.filename)
            if rel:
                return f"{rel}:{fr.lineno} in {fr.name}"
    return None


def exception_record(exc: BaseException, stage: str, data: Optional[bytes] = None,
                     op: Optional[Sequence[Any]] = None) -> Dict[str, Any]:
    """An exception as the batch records it: code, reason and details as before, plus ``kind``, ``stage``, the op that
    was running (``op``, ``op_number``), ``where`` (the line of code), and — when the engine itself failed — the
    ``traceback``."""
    c = classify(exc, stage, data)
    details = dict(getattr(exc, "details", None) or {}) if isinstance(getattr(exc, "details", None), dict) else {}
    details.update(c.get("details") or {})
    rec: Dict[str, Any] = {"code": c["code"], "reason": c["reason"], "details": details, "kind": c["kind"],
                           "stage": stage}
    if c.get("hint"):
        rec["hint"] = c["hint"]
    if op:
        rec.setdefault("op_number", op[0])
        rec.setdefault("op", op[1])
    if isinstance(details.get("op_number"), int):
        rec["op_number"], rec["op"] = details["op_number"], details.get("op", rec.get("op"))
    site = raise_site(exc)
    if site:
        rec["where"] = site
    if c["kind"] in ("internal-error", "environment") or c["code"] == "unreadable":
        rec["traceback"] = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return rec


# ---- the per-file trace ---------------------------------------------------------------------------------------------

class Tracer:
    """One file's trace (``logs/<name>.trace.log``): a header, then one timestamped line per stage event, each written
    and flushed as it happens. ``lines`` keeps what was written (the batch copies it into ``run.log`` and DIAGNOSTICS.md).
    A trace that cannot be written never stops the file."""

    def __init__(self, path: Optional[Path], title: str, header: Sequence[str] = (), append: bool = False):
        self.path = Path(path) if path else None
        self.lines: List[str] = []
        self.op: Optional[tuple] = None            # (number, name) of the op running now
        self._op_t0 = 0.0
        self._fh = None
        self._hang = False
        self._crash = False
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._fh = open(self.path, "a" if append else "w", encoding="utf-8", newline="\n")
            except OSError as exc:
                self._fh = None
                diagnostic_failure(self.path, exc)
        if append:
            self._raw("")
        self._raw(title)
        for h in header:
            self._raw(h)
        self._raw("")

    def _raw(self, text: str) -> None:
        text = redact_text(text)
        self.lines.append(text)
        if self._fh is not None:
            try:
                self._fh.write(text + "\n")
                self._fh.flush()
            except (OSError, ValueError) as exc:
                diagnostic_failure(self.path, exc)

    def note(self, stage: str, msg: str) -> None:
        event("file.checkpoint", stage=stage, message=msg, trace_path=self.path,
              op_number=self.op[0] if self.op else None, op=self.op[1] if self.op else None)
        first, *rest = (str(msg).splitlines() or [""])
        self._raw(f"{now()}  {stage:<11} {first}")
        for line in rest:
            self._raw(TRACE_INDENT + line)

    def block(self, text: str) -> None:
        """Lines under the last event (a traceback, details, a check's specifics)."""
        for line in str(text).rstrip("\n").splitlines():
            self._raw(TRACE_INDENT + line)

    def op_hook(self, phase: str, number: int, name: str, state: str, fragment: Any = None) -> None:
        """The callback ``editspec.plan_spec`` / ``apply_spec`` call around each op."""
        if state == "start":
            self.op, self._op_t0 = (number, name), time.perf_counter()
            self.note(phase, f"op #{number} {name} …")
            return
        summary = plan_summary(fragment) if fragment is not None else "done"
        self.note(phase, f"op #{number} {name}: {summary} [{ms(self._op_t0)}]")
        self.op = None

    def watch(self, crash: bool) -> None:
        """Python's fault handler into this trace: a stack dump of where the file is when it has been running for
        HANG_DUMP_SECONDS (again every HANG_DUMP_SECONDS), and — ``crash``, in a pool worker only (the window keeps its
        own handlers) — the stack of a hard crash."""
        if self._fh is None:
            return
        try:
            if crash:
                faulthandler.enable(file=self._fh, all_threads=True)
                self._crash = True
            faulthandler.dump_traceback_later(HANG_DUMP_SECONDS, repeat=True, file=self._fh)
            self._hang = True
        except (ValueError, RuntimeError, OSError, AttributeError):
            pass

    def close(self) -> None:
        try:
            if self._hang:
                faulthandler.cancel_dump_traceback_later()
            if self._crash:
                faulthandler.disable()
        finally:
            self._hang = self._crash = False
            if self._fh is not None:
                try:
                    self._fh.close()
                except OSError:
                    pass
                self._fh = None


def append_trace(path: Optional[Path], lines: Sequence[str]) -> None:
    """Add lines to a trace a worker wrote and closed (render and delivery happen in the parent process)."""
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write("".join(redact_text(line) + "\n" for line in lines))
    except OSError as exc:
        diagnostic_failure(path, exc)


def trace_event(stage: str, msg: str, block: str = "") -> List[str]:
    """The lines ``Tracer.note`` + ``Tracer.block`` would write, for a trace continued by ``append_trace``."""
    first, *rest = (redact_text(str(msg)).splitlines() or [""])
    out = [f"{now()}  {stage:<11} {first}"] + [TRACE_INDENT + r for r in rest]
    out += [TRACE_INDENT + line for line in redact_text(str(block)).rstrip("\n").splitlines()] if block else []
    return out


_TRACE_LINE = re.compile(r"^\d\d:\d\d:\d\d\.\d{3}  (\S+)")


def last_stage(lines: Sequence[str]) -> Optional[str]:
    """The stage of a trace's last event line (where a worker that died was)."""
    for line in reversed(lines):
        m = _TRACE_LINE.match(line)
        if m:
            return m.group(1)
    return None


def read_trace(path: Optional[Path]) -> List[str]:
    if not path:
        return []
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def fault_excerpt(lines: Sequence[str], n: int = 30) -> List[str]:
    """The fault handler's report in a trace (a hard crash, or a stack dump of a file that hung), if any."""
    for i, line in enumerate(lines):
        if line.startswith(("Fatal Python error", "Windows fatal exception", "Timeout (", "Thread 0x",
                            "Current thread 0x")):
            return list(lines[i:i + n])
    return []


def plan_summary(plan: Any, limit: int = 420) -> str:
    """One line for a plan or an op's fragment: what it will change (the planner's own summary)."""
    try:
        from .planner import summarize_plan
        text = " · ".join(s.strip() for s in summarize_plan(plan))
    except Exception as e:  # noqa: BLE001 — a summary must never stop the file it describes
        text = f"(no summary: {type(e).__name__}: {e})"
    return snip(text, limit)


# ---- the run's log --------------------------------------------------------------------------------------------------

class RunLog:
    """The run's own log (``logs/run.log``): timestamped lines. Calling it is a progress line — it also goes to the
    caller's ``progress`` callback (the window's activity log); ``detail`` and ``block``
    lines go to the file only. A log that cannot be written never stops the run."""

    def __init__(self, path: Optional[Path], progress: Optional[Callable[[str], None]] = None,
                 header: Sequence[str] = ()):
        self.path = Path(path) if path else None
        self.progress = progress
        self._lock = threading.Lock()
        self._fh = None
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._fh = open(self.path, "a", encoding="utf-8", newline="\n")
            except OSError as exc:
                self._fh = None
                diagnostic_failure(self.path, exc)
        for h in header:
            self._write(h, stamp=False)

    def __call__(self, msg: str) -> None:
        self._write(msg)
        if self.progress is not None:
            try:
                self.progress(msg)
            except Exception as exc:
                event("diagnostics.progress_callback_failed", level="warning", **error_fields(exc))

    def detail(self, msg: str) -> None:
        self._write(msg)

    def block(self, text: str, indent: str = "    ") -> None:
        for line in str(text).rstrip("\n").splitlines():
            self._write(indent + line, stamp=False)

    def _write(self, msg: str, stamp: bool = True) -> None:
        if self._fh is None:
            return
        first, *rest = (redact_text(str(msg)).splitlines() or [""])
        text = (f"{now()}  {first}" if stamp else first) + "\n" + "".join(" " * 14 + r + "\n" for r in rest)
        with self._lock:
            try:
                self._fh.write(text)
                self._fh.flush()
            except (OSError, ValueError) as exc:
                diagnostic_failure(self.path, exc)

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.close()
                except OSError:
                    pass
                self._fh = None


# ---- the application's own logs (the window) -------------------------------------------------------------------------

def setup_app_logging(log_dir: Path) -> Optional[Path]:
    """Send the ``spec_surgeon`` loggers to ``<log_dir>/spec-surgeon.log`` (rotating: 2 MB × 4). Idempotent; None when
    the folder cannot be written (the app runs on without a log)."""
    path = Path(log_dir) / APP_LOG
    setup_events(log_dir)
    install_exception_hooks()
    logger = logging.getLogger("spec_surgeon")
    for h in list(logger.handlers):
        if (isinstance(h, RotatingFileHandler) and Path(h.baseFilename) == path.resolve()
                and getattr(h, "owner_pid", os.getpid()) == os.getpid()):
            return path
        if isinstance(h, SafeRotatingHandler):
            logger.removeHandler(h)
            h.close()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = SafeRotatingHandler(path, maxBytes=2 * 2 ** 20, backupCount=3, encoding="utf-8")
    except OSError as exc:
        diagnostic_failure(path, exc)
        return None
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)-7s %(name)s [%(process)d:%(threadName)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return path


def app_log() -> logging.Logger:
    return logging.getLogger("spec_surgeon.app")


def pid_alive(pid: int) -> bool:
    """Whether a process is running (never kills anything: ``os.kill(pid, 0)`` would terminate it on Windows, so
    Windows asks the kernel). Unknown counts as alive — a live session is never reported as a crash."""
    if sys.platform.startswith("win"):
        from .winapp import process_alive
        alive = process_alive(pid)
        return True if alive is None else alive
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


_ACTIVE: List[Optional["CrashWatch"]] = [None]


class CrashWatch:
    """The window's crash watch: ``start()`` points Python's fault handler at ``crash-<time>-<pid>.log`` (the stack of
    a hard crash: a fault in the native window (pythonnet, WebView2), lxml or PyMuPDF) and writes
    ``session-<pid>.json``; ``stop()`` — a clean exit — removes the marker, and the crash log when nothing was written
    to it. A marker an earlier session left behind
    whose process is gone means that session ended without closing (a hard crash, a kill, a power cut): ``start()``
    returns it, with its crash log, so the window can say so once."""

    KEEP = 10

    def __init__(self, log_dir: Path):
        self.dir = Path(log_dir)
        self.marker: Optional[Path] = None
        self.crash_log: Optional[Path] = None
        self._fh = None

    def start(self) -> List[Dict[str, Any]]:
        previous = self.previous()
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            self.crash_log = self.dir / f"crash-{stamp}-{os.getpid()}.log"
            self._fh = open(self.crash_log, "w", encoding="utf-8")
            faulthandler.enable(file=self._fh, all_threads=True)
            self.marker = self.dir / f"session-{os.getpid()}.json"
            self.marker.write_text(json.dumps({"pid": os.getpid(), "version": __version__,
                                               "started": datetime.now().astimezone().isoformat(timespec="seconds"),
                                               "crash_log": self.crash_log.name}), encoding="utf-8")
        except (OSError, ValueError, RuntimeError):
            return previous
        _ACTIVE[0] = self
        self._prune()
        return previous

    def previous(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for m in sorted(self.dir.glob("session-*.json")):
            if self.marker is not None and m == self.marker:
                continue                     # this session's own marker, written by start()
            try:
                info = json.loads(m.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                info = {}
            pid = info.get("pid") if isinstance(info, dict) else None
            if isinstance(pid, int) and pid != os.getpid() and pid_alive(pid):
                continue                     # a session still running (another window); ours left nothing yet
            crash = self.dir / str((info or {}).get("crash_log") or "-")
            lines = read_trace(crash) if crash.is_file() else []
            out.append({"started": (info or {}).get("started"), "version": (info or {}).get("version"),
                        "crash_log": str(crash) if lines else None, "excerpt": lines[-40:]})
            try:
                m.unlink()
            except OSError:
                pass
        return out

    def _prune(self) -> None:
        logs = sorted(self.dir.glob("crash-*.log"), key=lambda p: p.stat().st_mtime if p.exists() else 0)
        for p in logs[:-self.KEEP]:
            if p != self.crash_log:
                try:
                    p.unlink()
                except OSError:
                    pass

    def stop(self) -> None:
        if _ACTIVE[0] is self:
            try:
                faulthandler.disable()
            except RuntimeError:
                pass
            _ACTIVE[0] = None
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None
        for p, only_if_empty in ((self.crash_log, True), (self.marker, False)):
            if p is None:
                continue
            try:
                if not only_if_empty or p.stat().st_size == 0:
                    p.unlink()
            except OSError:
                pass


@contextmanager
def com_quiet() -> Iterator[None]:
    """Word automation (COM) raises first-chance RPC exceptions that Windows' fault handler logs as "Windows fatal
    exception" although they are handled; the window's crash log is paused around it, so it keeps only real crashes."""
    w = _ACTIVE[0]
    paused = w is not None and w._fh is not None and faulthandler.is_enabled()
    if paused:
        faulthandler.disable()
    try:
        yield
    finally:
        if paused and w._fh is not None and not w._fh.closed:
            try:
                faulthandler.enable(file=w._fh, all_threads=True)
            except (ValueError, RuntimeError, OSError):
                pass


def write_json(path: Path, data: Any) -> Optional[Path]:
    """Write a diagnostic JSON file; a diagnostic that cannot be written never stops the work it describes."""
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return Path(path)
    except (OSError, TypeError, ValueError) as exc:
        diagnostic_failure(path, exc)
        return None
