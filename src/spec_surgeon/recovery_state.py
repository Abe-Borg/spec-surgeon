"""Lossless local checkpoints for recovery, separate from abbreviated diagnostic transcripts.

Checkpoints contain document evidence and provider conversation blocks. They never contain
an acknowledgement or grant permission to export; the recovery loader rechecks all pins.
"""
from __future__ import annotations

import json
import os
import tempfile
import inspect
from contextlib import contextmanager
from functools import wraps
from pathlib import Path


@contextmanager
def session_lock(folder: Path, *, create=False, filename=".recovery.lock",
                 message="This recovery session is already being continued or exported by another worker."):
    """An OS-released process lock: a crashed worker never leaves a stale lease."""
    folder = Path(folder)
    if create:
        folder.mkdir(parents=True, exist_ok=True)
    lock_path = folder / filename
    stream = open(lock_path, "a+b")
    acquired = False
    try:
        try:
            if os.name == "nt":
                import msvcrt
                stream.seek(0)
                if not stream.read(1):
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as exc:
            raise ValueError(message) from exc
        yield
    finally:
        if acquired:
            if os.name == "nt":
                import msvcrt
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def exclusive_session(parameter, *, create=False):
    """Lock an API's directory (or RecoveryResult) for its complete mutation."""
    def decorate(function):
        signature = inspect.signature(function)

        @wraps(function)
        def call(*args, **kwargs):
            arguments = signature.bind(*args, **kwargs).arguments
            options = arguments.get("options")
            if options is not None:
                options.validate()  # invalid budgets must not even create the output folder
            directory = arguments[parameter]
            folder = Path(getattr(directory, "out_dir", directory)).resolve()
            if create and folder.is_dir() and any(p.name != ".recovery.lock" for p in folder.iterdir()):
                raise ValueError("Choose a new empty recovery folder outside the input files; existing content is preserved.")
            with session_lock(folder, create=create):
                return function(*args, **kwargs)
        return call
    return decorate


def json_value(value):
    """Preserve provider content (including thinking signatures) without lossy str()."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return json_value(dump(mode="json", exclude_none=True))
    if hasattr(value, "__dict__"):
        return json_value(vars(value))
    raise TypeError(f"Cannot checkpoint {type(value).__name__}")


def atomic_json(path: Path, value) -> None:
    """Commit one complete snapshot; an interrupted write leaves the previous one intact."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(json_value(value), indent=2, ensure_ascii=False, allow_nan=False)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def repair_interrupted_tools(messages, attempts):
    """Answer tools interrupted by a process exit without silently executing them twice.

Trials have their own saved artifacts. The investigator receives their references and
decides what evidence to inspect next; a tool call never becomes an unpaired prefill.
"""
    if not messages or messages[-1].get("role") != "assistant":
        return
    calls = [b for b in messages[-1].get("content", [])
             if isinstance(b, dict) and b.get("type") == "tool_use"]
    if calls:
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": call["id"], "is_error": True,
             "content": "The session was interrupted before this tool result was checkpointed. "
                        "Do not assume it failed or repeat it blindly. Inspect recovery_status and "
                        f"trial_results; {attempts} trial(s) are recorded."}
            for call in calls]})
    else:
        messages.append({"role": "user", "content": "Continue the saved investigation using the available evidence."})
