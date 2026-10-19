"""Short disk names for content-addressed evidence, retaining complete identities.

The public identifier is always the full SHA-256 of the canonical payload. New
JSON files carry that identifier beside the payload; legacy digest-named JSON
files remain readable. A shortened-name collision fails instead of overwriting
an unrelated immutable artifact.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

DISK_ID_CHARS = 16


def disk_id(identity: str) -> str:
    if not isinstance(identity, str) or len(identity) != 64 or any(c not in "0123456789abcdef" for c in identity):
        raise ValueError("evidence identity must be a complete SHA-256 digest")
    return identity[:DISK_ID_CHARS]


def canonical_json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def artifact_path(folder, identity: str) -> Path:
    return Path(folder) / f"{disk_id(identity)}.json"


def conversation_folder(root, identity: str, existing=None) -> Path:
    """Use short new folders and copy legacy folders before resuming their history."""
    root = Path(root)
    target = root / disk_id(identity)
    old = Path(existing) if existing else None
    if old and old != target:
        # Saved external locations are still supported. Only relocate the old
        # automatically generated hash/UUID folders under this run's own root.
        if (old.parent != root or len(old.name) not in (32, 64)
                or any(char not in "0123456789abcdef" for char in old.name)):
            return old
        if old.is_dir() and not target.exists():
            shutil.copytree(old, target)
    target.mkdir(parents=True, exist_ok=True)
    metadata = target / "conversation.json"
    if metadata.exists() and json.loads(metadata.read_bytes()).get("conversation_id") != identity:
        raise ValueError("conversation identity collision")
    if not metadata.exists():
        metadata.write_bytes(canonical_json({"conversation_id": identity}))
    return target


def read_artifact(folder, identity: str) -> bytes:
    """Verify and return the original payload, including for older full-name files."""
    path = artifact_path(folder, identity)
    if not path.is_file():
        path = Path(folder) / f"{identity}.json"
    raw = path.read_bytes()
    value = json.loads(raw)
    if isinstance(value, dict) and value.get("artifact_id") == identity and set(value) == {"artifact_id", "payload"}:
        raw = canonical_json(value["payload"])
    if hashlib.sha256(raw).hexdigest() != identity:
        raise ValueError("evidence artifact identity changed or shortened disk name collided")
    return raw


def write_artifact(folder, identity: str, content: bytes) -> Path:
    if hashlib.sha256(content).hexdigest() != identity:
        raise ValueError("evidence payload does not match its complete identity")
    path = artifact_path(folder, identity)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if read_artifact(folder, identity) != content:
            raise ValueError("evidence artifact identity collision")
        return path
    # The envelope keeps the complete digest in the file without creating a
    # self-referential hash. Readers expose the canonical original payload.
    encoded = canonical_json({"artifact_id": identity, "payload": json.loads(content)})
    # Sibling investigations can save the same shared catalog concurrently.
    # Distinct short temporary names prevent one writer removing another's temp.
    with tempfile.NamedTemporaryFile(prefix=".artifact-", suffix=".tmp", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(encoded)
    try:
        try:
            # Atomic publication without replacing an existing shortened name.
            # NTFS and ordinary local Unix filesystems support hard links.
            os.link(temporary, path)
        except FileExistsError:
            if read_artifact(folder, identity) != content:
                raise ValueError("evidence artifact identity collision")
        except OSError:
            if os.name != "nt":
                raise
            # FAT volumes and some sync providers cannot create links. Windows
            # rename also refuses an existing destination (unlike POSIX rename).
            try:
                temporary.rename(path)
            except FileExistsError:
                if read_artifact(folder, identity) != content:
                    raise ValueError("evidence artifact identity collision")
    finally:
        temporary.unlink(missing_ok=True)
    return path
