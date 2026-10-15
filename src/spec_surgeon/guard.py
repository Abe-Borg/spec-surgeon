"""The overwrite guard — has fired for real; respect it.

* Record the source's identity (size, mtime_ns, md5) at acquisition.
* Before writing back: re-check identity (drift → rebase, never overwrite), and confirm the
  document is not open in Word (owner lock file ``~$…`` beside it).
* Write via a temp file in the same directory and ``os.replace`` so the write is atomic and
  OneDrive/SharePoint see exactly one new version.
* Never force.
"""
from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional


class GuardError(RuntimeError):
    pass


@dataclass
class Identity:
    path: str
    size: int
    mtime_ns: int
    md5: str

    def to_dict(self):
        return asdict(self)


def _md5_file(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def acquire(path: os.PathLike | str) -> Identity:
    p = Path(path)
    st = p.stat()
    return Identity(path=str(p), size=st.st_size, mtime_ns=st.st_mtime_ns, md5=_md5_file(p))


def drifted(identity: Identity) -> Optional[str]:
    """None if unchanged, else a human-readable description of what moved. Size and mtime first (cheap), then the
    content itself: a sync tool or an editor that restores the timestamp can leave an edited file of the same
    size, and the md5 recorded at acquisition is what the plan was made from."""
    p = Path(identity.path)
    if not p.exists():
        return "source file no longer exists"
    st = p.stat()
    if st.st_size != identity.size or st.st_mtime_ns != identity.mtime_ns:
        return (f"size/mtime changed since acquisition "
                f"({identity.size}→{st.st_size} bytes, mtime {identity.mtime_ns}→{st.st_mtime_ns})")
    if _md5_file(p) != identity.md5:
        return f"content changed since acquisition (same size and mtime, md5 {identity.md5} → {_md5_file(p)})"
    return None


def word_lock_files(path: os.PathLike | str) -> List[str]:
    """Word's owner lock file is ``~$`` + the document name with its first one or two characters
    replaced (names longer than six characters) or simply prefixed (short names). ``doc.endswith(tail)``
    covers all three cases; it can also match a longer sibling name whose tail coincides, which is the
    conservative direction (treated as locked)."""
    p = Path(path)
    name = p.name
    found = []
    try:
        for entry in os.scandir(p.parent):
            n = entry.name
            if n.startswith("~$") and name.endswith(n[2:]):
                found.append(n)
    except FileNotFoundError:
        pass
    return found


def is_read_only(path: os.PathLike | str) -> bool:
    """The file's own write bit is off: Windows' read-only attribute (what ``stat`` reports there), or a POSIX mode
    without owner write. Checked from the mode, not ``os.access``, so an administrator or root sees the same answer
    as everyone else — a read-only file is the author's "do not change this", whoever runs the batch."""
    return not (os.stat(path).st_mode & stat.S_IWRITE)


def check_before_write(identity: Identity) -> None:
    d = drifted(identity)
    if d:
        raise GuardError(f"{identity.path}: {d}. Rebase: re-acquire, re-run the transform, re-verify.")
    locks = word_lock_files(identity.path)
    if locks:
        raise GuardError(f"{identity.path}: appears to be open in Word (lock file {locks[0]}). "
                         f"Close it, then retry. Word would otherwise overwrite the delivered file on its next save.")
    if is_read_only(identity.path):
        raise GuardError(f"{identity.path}: the file is read-only (its read-only attribute or permissions). Clear "
                         f"that in the file's properties and run again, or run in copies mode.")


def write_back(identity: Identity, data: bytes) -> Identity:
    """Guarded, atomic overwrite in place. Returns the new identity. Every reason the write cannot happen — drift,
    a Word lock, a read-only file, a replace the file system refuses (a lock held by another program, a permission
    denied, a share that went away) — is a ``GuardError`` with the reason, never a raw ``OSError``: the batch records
    it as a failed V9 for that file and carries on with the others."""
    check_before_write(identity)
    p = Path(identity.path)
    try:
        fd, tmp = tempfile.mkstemp(prefix=".specsurgeon-", suffix=".tmp", dir=p.parent)
    except OSError as e:
        raise GuardError(f"{identity.path}: cannot create a temporary file beside it ({e}); nothing was written.") from e
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        # Re-check immediately before the swap: the window is small but not zero.
        check_before_write(identity)
        os.replace(tmp, p)
    except GuardError:
        _unlink_quietly(tmp)
        raise
    except OSError as e:
        _unlink_quietly(tmp)
        raise GuardError(f"{identity.path}: the file could not be replaced ({e}); the original is untouched.") from e
    except Exception:
        _unlink_quietly(tmp)
        raise
    return acquire(p)


def _unlink_quietly(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def write_new(path: os.PathLike | str, data: bytes) -> Identity:
    """Write to a new location (the default delivery mode), atomically."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".specsurgeon-", suffix=".tmp", dir=p.parent)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, p)
    return acquire(p)
