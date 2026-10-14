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
    """None if unchanged, else a human-readable description of what moved."""
    p = Path(identity.path)
    if not p.exists():
        return "source file no longer exists"
    st = p.stat()
    if st.st_size != identity.size or st.st_mtime_ns != identity.mtime_ns:
        return (f"size/mtime changed since acquisition "
                f"({identity.size}→{st.st_size} bytes, mtime {identity.mtime_ns}→{st.st_mtime_ns})")
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


def check_before_write(identity: Identity) -> None:
    d = drifted(identity)
    if d:
        raise GuardError(f"{identity.path}: {d}. Rebase: re-acquire, re-run the transform, re-verify.")
    locks = word_lock_files(identity.path)
    if locks:
        raise GuardError(f"{identity.path}: appears to be open in Word (lock file {locks[0]}). "
                         f"Close it, then retry. Word would otherwise overwrite the delivered file on its next save.")


def write_back(identity: Identity, data: bytes) -> Identity:
    """Guarded, atomic overwrite in place. Returns the new identity."""
    check_before_write(identity)
    p = Path(identity.path)
    fd, tmp = tempfile.mkstemp(prefix=".specsurgeon-", suffix=".tmp", dir=p.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        # Re-check immediately before the swap: the window is small but not zero.
        check_before_write(identity)
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return acquire(p)


def write_new(path: os.PathLike | str, data: bytes) -> Identity:
    """Write to a new location (the default delivery mode), atomically."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".specsurgeon-", suffix=".tmp", dir=p.parent)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, p)
    return acquire(p)
