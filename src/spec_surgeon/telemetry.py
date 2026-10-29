"""Local, correlated JSONL diagnostics. Never sends telemetry over the network.

Scopes select a run/file journal; application events also go to a bounded rotating
journal when configured. Every event is flushed before returning. Worker processes
use their own journals, avoiding shared-file rotation races.
"""
from __future__ import annotations

import contextvars
import functools
import json
import logging
import math
import os
import re
import sys
import threading
import time
import traceback
import uuid
from itertools import islice
from contextlib import contextmanager
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

SCHEMA_VERSION = 1
APP_EVENTS = "app-events-{pid}.jsonl"
KEEP_EVENT_PROCESSES = 5
MAX_TEXT = 16000
MAX_ITEMS = 100
_CONTEXT: contextvars.ContextVar = contextvars.ContextVar("diagnostic_context", default=None)
_SINK: contextvars.ContextVar = contextvars.ContextVar("diagnostic_sink", default=None)
_SESSION_ID = uuid.uuid4().hex
_FAILURES: set = set()
_FAILURE_LOCK = threading.Lock()
_LOGGER = logging.getLogger("spec_surgeon.events")
_LOGGER.propagate = False
_LOGGER.addHandler(logging.NullHandler())
_LOGGER.setLevel(logging.INFO)

# Only credential-bearing names; IDs and approval hashes remain useful for correlation.
_SECRET_FIELD = re.compile(r"(?:api[_-]?key|authorization|password|passwd|secret|access[_-]?token|refresh[_-]?token|auth[_-]?token|cookie|credential)", re.I)
_PAYLOAD_FIELDS = {"prompt", "system", "messages", "document_text", "image_data", "request_body", "response_body"}
_TOKEN = re.compile(r"\b(?:sk-ant-[A-Za-z0-9_-]+|sk-[A-Za-z0-9_-]{16,})")
_BEARER = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+")
_ASSIGNMENT = re.compile(r'''(?i)(["']?(?:api[_-]?key|authorization|password|passwd|client[_-]?secret|access[_-]?token|refresh[_-]?token|auth[_-]?token|cookie)["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;&}\]]+)''')
_URL_USER = re.compile(r"(https?://)[^/@\s]+:[^/@\s]+@", re.I)


def new_id() -> str:
    return uuid.uuid4().hex


_SECRET_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY")
# A pattern is searched only where it could match: what each one cannot match without is checked first, exactly. The
# case-insensitive ones need their words, compared folded: lower case, after the four non-ASCII letters Python's re
# matches to an ASCII one when ignoring case (the re documentation: İ ı → i, K → k, ſ → s; test_observability checks
# the list against re itself). _ASSIGNMENT's names each hold one of _ASSIGNMENT_WORDS and need a ':' or '='.
_FOLD = str.maketrans({"\u0130": "i", "\u0131": "i", "\u212a": "k", "\u017f": "s"})
_ASSIGNMENT_WORDS = ("key", "auth", "pass", "secret", "token", "cookie")


def new_id() -> str:
    return uuid.uuid4().hex


def _secrets() -> Tuple[str, ...]:
    """The provider credentials set in the environment now (each at least 8 characters), to be scrubbed verbatim."""
    found = []
    for name in _SECRET_ENV:
        secret = os.environ.get(name)
        if secret and len(secret) >= 8:
            found.append(secret)
    return tuple(found)


def _folded(text: str) -> str:
    if not text.isascii() and any(letter in text for letter in "\u0130\u0131\u212a\u017f"):
        text = text.translate(_FOLD)
    return text.lower()


def redact_text(value: str, secrets: Optional[Tuple[str, ...]] = None) -> str:
    """Scrub common credential formats, including known active provider credentials (``secrets``: those read once
    for a whole record by ``_secrets``; read from the environment now when not given)."""
    text = str(value)
    for secret in _secrets() if secrets is None else secrets:
        text = text.replace(secret, "[REDACTED]")
    if "sk-" in text:
        text = _TOKEN.sub("[REDACTED]", text)
    folded = _folded(text)
    if "bearer" in folded or "basic" in folded:
        text = _BEARER.sub(lambda m: m.group(1) + " [REDACTED]", text)
        folded = _folded(text)
    if ("=" in text or ":" in text) and any(word in folded for word in _ASSIGNMENT_WORDS):
        text = _ASSIGNMENT.sub(lambda m: m.group(1) + "[REDACTED]", text)
    if "@" in text and "://" in text:
        text = _URL_USER.sub(r"\1[REDACTED]@", text)
    return text


@functools.lru_cache(maxsize=4096)
def _field_kind(key: str) -> str:
    """How a record field named ``key`` is written: "secret" (its value redacted), "payload" (omitted) or "plain"."""
    return "secret" if _SECRET_FIELD.search(key) else "payload" if key.lower() in _PAYLOAD_FIELDS else "plain"


def _safe(value: Any, depth: int = 0, seen: Optional[set] = None, secrets: Optional[Tuple[str, ...]] = None) -> Any:
    """Bound records and avoid object reprs (SDK objects can contain credentials). ``secrets``: as ``redact_text``;
    a top-level call reads them once for everything below it."""
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if secrets is None:
        secrets = _secrets()
    if isinstance(value, (str, Path)):
        text = redact_text(str(value), secrets)
        return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "…[truncated]"
    if isinstance(value, bytes):
        return {"bytes": len(value), "content": "[OMITTED]"}
    if depth >= 8:
        return "[depth limit]"
    seen = seen if seen is not None else set()
    if id(value) in seen:
        return "[cycle]"
    seen.add(id(value))
    try:
        if isinstance(value, dict):
            result = {}
            for key, val in islice(value.items(), MAX_ITEMS):
                key = str(key)
                kind = _field_kind(key)
                result[redact_text(key, secrets)] = ("[REDACTED]" if kind == "secret" else
                    "[OMITTED]" if kind == "payload" else _safe(val, depth + 1, seen, secrets))
            if len(value) > MAX_ITEMS:
                result["_omitted_fields"] = len(value) - MAX_ITEMS
            return result
        if isinstance(value, (list, tuple, set, frozenset)):
            result = [_safe(item, depth + 1, seen, secrets) for item in islice(value, MAX_ITEMS)]
            if len(value) > MAX_ITEMS:
                result.append({"omitted_items": len(value) - MAX_ITEMS})
            return result
        return f"<{type(value).__name__}>"
    finally:
        seen.discard(id(value))


def context() -> Dict[str, Any]:
    """Copy the current correlation fields, suitable for explicit process propagation."""
    return {"session_id": _SESSION_ID, **(_CONTEXT.get() or {})}


def diagnostic_failure(path: Any, error: BaseException) -> None:
    """A failed log must not fail the operation or disappear silently. Report once per destination."""
    key = (os.getpid(), str(path))
    with _FAILURE_LOCK:
        if key in _FAILURES:
            return
        _FAILURES.add(key)
    message = redact_text(f"Diagnostics could not be written to {path}: {type(error).__name__}: {error}")
    # Direct fallback avoids recursively trying the failed logging destination.
    try:
        if sys.stderr is not None:
            sys.stderr.write(message + "\n")
            sys.stderr.flush()
    except Exception:
        pass
    # Other available journals can still preserve this failure.
    try:
        _LOGGER.warning(json.dumps({**_safe(context()), "schema_version": SCHEMA_VERSION,
            "event": "diagnostics.write_failed", "level": "warning",
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "monotonic_ns": time.monotonic_ns(), "pid": os.getpid(), "event_id": new_id(),
            "thread_id": threading.get_ident(), "thread": threading.current_thread().name,
            "message": message}, ensure_ascii=False))
    except Exception:
        pass


class EventLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def write(self, record: Dict[str, Any]) -> None:
        try:
            self.write_line(json.dumps(record, ensure_ascii=False, allow_nan=False))
        except Exception as exc:
            diagnostic_failure(self.path, exc)

    def write_line(self, line: str) -> None:
        """Append one record already encoded as JSON (``event`` encodes once, for this journal and the app's)."""
        data = line + "\n"
        try:
            with self._lock:
                try:
                    stream = self.path.open("a", encoding="utf-8", newline="\n")
                except FileNotFoundError:               # its folder is made (again) when it is not there
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    stream = self.path.open("a", encoding="utf-8", newline="\n")
                with stream:
                    stream.write(data)
                    stream.flush()
        except Exception as exc:
            diagnostic_failure(self.path, exc)


@contextmanager
def scope(path: Optional[Path] = None, **fields: Any) -> Iterator[None]:
    token = _CONTEXT.set({**context(), **fields})
    sink_token = _SINK.set(EventLog(path)) if path is not None else None
    try:
        yield
    finally:
        if sink_token is not None:
            _SINK.reset(sink_token)
        _CONTEXT.reset(token)


context_scope = scope


def event(name: str, *, level: str = "info", **fields: Any) -> None:
    """Write one checkpoint, without raising or capturing locals/payloads."""
    try:
        secrets = _secrets()
        record = {**_safe(context(), secrets=secrets), **_safe(fields, secrets=secrets),
            "schema_version": SCHEMA_VERSION,
            "event_id": new_id(), "event": str(name), "level": level,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "monotonic_ns": time.monotonic_ns(), "pid": os.getpid(),
            "thread_id": threading.get_ident(), "thread": threading.current_thread().name}
        line = json.dumps(record, ensure_ascii=False, allow_nan=False)
        sink = _SINK.get()
        if sink is not None:
            sink.write_line(line)
        _LOGGER.log(getattr(logging, level.upper(), logging.INFO), line)
    except Exception as exc:
        diagnostic_failure("structured event", exc)


def error_fields(exc: BaseException) -> Dict[str, Any]:
    """Total exception serializer: a defective exception must never replace the original failure."""
    try:
        return _error_fields(exc)
    except Exception:
        return {"error_type": type(exc).__name__, "error": "[exception details could not be read]"}


def _error_fields(exc: BaseException) -> Dict[str, Any]:
    """Exception cause chain and source locations, never stack frame locals."""
    chain = []
    seen = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen and len(chain) < 16:
        seen.add(id(current))
        chain.append({"type": type(current).__name__, "message": str(current)})
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    result = {"error_type": type(exc).__name__, "error": str(exc), "exception_chain": chain,
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))}
    for name in ("code", "errno", "winerror", "status_code", "request_id"):
        value = getattr(exc, name, None)
        if isinstance(value, (str, int)):
            result[name] = value
    return _safe(result)


@contextmanager
def span(name: str, **fields: Any) -> Iterator[Dict[str, Any]]:
    parent = context().get("span_id")
    with scope(span_id=new_id(), parent_span_id=parent):
        t0 = time.perf_counter()
        outcome: Dict[str, Any] = {}
        event(name + ".started", **fields)
        try:
            yield outcome
        except BaseException as exc:
            event(name + ".failed", level="error", **{**fields, **outcome, **error_fields(exc),
                "duration_ms": round((time.perf_counter() - t0) * 1000, 3)})
            raise
        else:
            event(name + ".succeeded", **{**fields, **outcome,
                "duration_ms": round((time.perf_counter() - t0) * 1000, 3)})


class SafeRotatingHandler(RotatingFileHandler):
    """Logging I/O errors never escape; only the configuring process rotates its file."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner_pid = os.getpid()

    def emit(self, record):
        if os.getpid() == self.owner_pid:
            super().emit(record)

    def handleError(self, record):
        diagnostic_failure(self.baseFilename, sys.exc_info()[1] or OSError("log write failed"))


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        return redact_text(super().format(record))


def app_events_path(log_dir: Path) -> Path:
    return Path(log_dir) / APP_EVENTS.format(pid=os.getpid())


def _process_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    if sys.platform.startswith("win"):
        from .winapp import process_alive
        return process_alive(pid) is not False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _prune_events(log_dir: Path) -> None:
    """Keep five inactive process journals plus every live one (four rotations each)."""
    try:
        groups: Dict[int, list] = {}
        for path in log_dir.glob("app-events-*.jsonl*"):
            match = re.fullmatch(r"app-events-(\d+)\.jsonl(?:\.[1-3])?", path.name)
            if match:
                groups.setdefault(int(match[1]), []).append(path)
        inactive = [paths for pid, paths in groups.items() if not _process_alive(pid)]
        inactive.sort(key=lambda paths: max(p.stat().st_mtime for p in paths), reverse=True)
        for paths in inactive[KEEP_EVENT_PROCESSES:]:
            for path in paths:
                path.unlink(missing_ok=True)
    except OSError as exc:
        diagnostic_failure("event journal retention", exc)


def setup_events(log_dir: Path) -> Optional[Path]:
    path = app_events_path(log_dir).resolve()
    for handler in list(_LOGGER.handlers):
        if isinstance(handler, SafeRotatingHandler):
            if Path(handler.baseFilename) == path and handler.owner_pid == os.getpid():
                return path
            _LOGGER.removeHandler(handler)
            handler.close()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = SafeRotatingHandler(path, maxBytes=5 * 2**20, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        _LOGGER.addHandler(handler)
        _prune_events(path.parent)
        return path
    except OSError as exc:
        diagnostic_failure(path, exc)
        return None


def install_exception_hooks() -> None:
    """Preserve host hooks while recording otherwise unhandled Python failures."""
    if not getattr(sys.excepthook, "_spec_surgeon", False):
        previous = sys.excepthook
        def exception_hook(kind, value, tb):
            event("process.unhandled_exception", level="error", **error_fields(value))
            previous(kind, value, tb)
        exception_hook._spec_surgeon = True
        sys.excepthook = exception_hook
    if not getattr(threading.excepthook, "_spec_surgeon", False):
        previous_thread = threading.excepthook
        def thread_hook(args):
            event("thread.unhandled_exception", level="error", failed_thread=getattr(args.thread, "name", None),
                  **error_fields(args.exc_value))
            previous_thread(args)
        thread_hook._spec_surgeon = True
        threading.excepthook = thread_hook
