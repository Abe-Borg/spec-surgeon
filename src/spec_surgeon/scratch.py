"""The window's scratch folder: everything a session writes that is not the user's own file.

Every session folder the window makes (the conversation's records, previews, runs, recovery checkpoints, custom-method
attempts), every file dropped on the window and the conversation's journal live under one folder per window,
``data_dir()\\scratch\\<start stamp>-<pid>``, and only while the window is open: a clean close deletes it
(``Scratch.discard``), and the next start sweeps whatever an earlier window left behind. The one exception is the
folder of a window that never closed — a crash, a kill, a power cut — which still holds a journal worth restoring:
the next start takes that folder over (``adopted``) so the conversation and its copies come back, once. The app
deletes only its own scratch folders, never a file the user attached by path or saved with Export.

A window holds ``.window.lock`` open in its folder for as long as it runs (an OS-released lock, as recovery's
checkpoints are held), so a second window never sweeps the first's folder, and a crashed window's lock is gone with
its process. A clean close deletes the journal first, so a folder a close could not remove whole (a copy held open
in Word) is never mistaken for a crashed session: the next start sweeps it. The render cache beside the scratch
folders (``scratch\\.render-cache``) is not a session's and is never swept here.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from . import diagnostics as diag

LOCK_FILE = ".window.lock"
JOURNAL = "conversation.json"
ATTACHMENTS = "attachments"
# the journal keys whose content makes a crashed session's folder worth restoring
RESTORABLE_KEYS = ("attachments", "messages", "decisions", "results", "working", "todo")


class _Lock:
    """A non-blocking exclusive lock on one file, held open until ``release``."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._stream = None

    def acquire(self) -> bool:
        try:
            stream = open(self.path, "a+b")
        except OSError:
            return False
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
        except OSError:
            stream.close()
            return False
        self._stream = stream
        return True

    @property
    def held(self) -> bool:
        return self._stream is not None

    def release(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            stream.close()


class Scratch:
    """This window's scratch folder, locked for as long as the window runs."""

    def __init__(self, path: Path, lock: _Lock, *, adopted: bool = False) -> None:
        self.path = Path(path)
        self._lock = lock
        self.adopted = adopted                 # a crashed session's folder, taken over to restore its journal

    @property
    def journal(self) -> Path:
        return self.path / JOURNAL

    @property
    def locked(self) -> bool:
        return self._lock.held

    def release(self) -> None:
        """Let go of the folder without deleting it (a close that keeps unsaved copies for the next start)."""
        self._lock.release()

    def discard(self) -> bool:
        """A clean close: the journal first (so nothing left behind reads as a crashed session), then the lock and
        the whole folder. True when nothing is left; a file another program holds open leaves a remainder the next
        start sweeps."""
        try:
            self.journal.unlink(missing_ok=True)
        except OSError:
            pass
        self._lock.release()
        _delete(self.path)
        gone = not self.path.exists()
        diag.event("scratch.discarded", path=str(self.path), gone=gone)
        return gone


def _delete(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)


def _worth_restoring(journal: Path) -> bool:
    """A journal that holds a conversation: anything attached, said, decided, delivered or waiting."""
    try:
        data = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (isinstance(data, dict) and data.get("version") in (1, 2)
            and any(data.get(key) for key in RESTORABLE_KEYS))


def _leftovers(root: Path) -> List[tuple]:
    """Every earlier window's folder nobody holds, newest first, each with its lock taken."""
    out = []
    try:
        children = sorted((p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")),
                          key=lambda p: p.name, reverse=True)
    except OSError:
        return out
    for child in children:
        lock = _Lock(child / LOCK_FILE)
        if lock.acquire():
            out.append((child, lock))
    return out


def open_window(root: Optional[Path] = None, *, now: Optional[datetime] = None) -> Scratch:
    """The scratch folder of this window. A crashed session's folder (the newest leftover with a journal worth
    restoring) is taken over and its ``adopted`` says so; every other leftover is deleted; otherwise a new folder is
    made and locked. ``root`` is ``settings.scratch_root()`` unless a caller (a test) names one."""
    from .settings import scratch_root
    root = Path(root) if root is not None else scratch_root()
    root.mkdir(parents=True, exist_ok=True)
    adopted = None
    swept = []
    for child, lock in _leftovers(root):
        if adopted is None and _worth_restoring(child / JOURNAL):
            adopted = Scratch(child, lock, adopted=True)
            continue
        lock.release()
        _delete(child)
        swept.append(child.name)
    if swept:
        diag.event("scratch.swept", folders=swept)
    if adopted is not None:
        diag.event("scratch.adopted", path=str(adopted.path), swept=len(swept))
        return adopted
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    path, n = root / f"{stamp}-{os.getpid()}", 2
    while path.exists():
        path, n = root / f"{stamp}-{os.getpid()}-{n}", n + 1
    path.mkdir(parents=True)
    lock = _Lock(path / LOCK_FILE)
    if not lock.acquire():
        raise OSError(f"the window's scratch folder could not be locked: {path}")
    diag.event("scratch.opened", path=str(path), swept=len(swept))
    return Scratch(path, lock)
