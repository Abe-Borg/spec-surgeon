"""The content digest that pins a file's bytes (recovery and the conversation's approvals compare against it)."""
from __future__ import annotations

import hashlib
from pathlib import Path


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()
