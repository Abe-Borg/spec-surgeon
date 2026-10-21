"""A cache of rendered PDFs, keyed by what was rendered and by what rendered it.

An entry is ``<root>/<renderer>/<sha256 of the .docx bytes>.pdf`` beside a ``.json`` sidecar naming the bytes it was
rendered from, the renderer (backend and version) and the PDF's own size and digest. A lookup hashes the .docx as it
is now, so a file whose bytes changed can never be served another file's pages; a sidecar that does not match the
entry (a truncated or tampered PDF, a different renderer) is a miss, and the broken entry is removed so the next
render replaces it. The cache lives under the output root (``<out_root>/.render-cache``, see ``cache_root_for``),
holds at most ``DEFAULT_LIMIT_BYTES`` of PDFs (the least recently used go first; a hit refreshes an entry's mtime),
and ``SPEC_SURGEON_RENDER_CACHE=0`` turns it off.

Why: the preview renders a batch's sources and the run renders them again, and a source attached again tomorrow is
the same bytes; a page render is the slowest thing the window waits on, and a hash is not.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .. import diagnostics as diag

ENV_SWITCH = "SPEC_SURGEON_RENDER_CACHE"
CACHE_DIR_NAME = ".render-cache"
DEFAULT_LIMIT_BYTES = 2 * 1024 ** 3
_OFF = {"0", "false", "no", "off"}
# gui.Session's session folders: 20261019-235959, 20261019-235959-2, 20261019-235959-123456-recovery
_SESSION_FOLDER = re.compile(r"^\d{8}-\d{6}(-.*)?$")
_SLUG = re.compile(r"[^A-Za-z0-9._-]+")


def enabled() -> bool:
    """The cache is on unless ``SPEC_SURGEON_RENDER_CACHE`` says 0/false/no/off."""
    return os.environ.get(ENV_SWITCH, "").strip().casefold() not in _OFF


def cache_root_for(out_dir: Path) -> Path:
    """Where the cache for a preview or run folder lives: beside the session folders, so every session of the window
    shares one — ``<default run root>/.render-cache`` when the folder is under the app's run root; ``<out_root>/
    .render-cache`` when its parent is a session folder (``gui.Session`` stamps them ``YYYYMMDD-HHMMSS``) under a root
    the user chose; otherwise (a folder the caller named, a test's folder) a ``.render-cache`` beside the output
    folder itself — never anywhere above a folder the caller named."""
    from ..settings import default_run_root
    out = Path(out_dir)
    try:
        resolved = out.resolve()
    except OSError:
        resolved = out.absolute()
    try:
        root = default_run_root().resolve()
        if root in resolved.parents:
            return root / CACHE_DIR_NAME
    except OSError:
        pass
    parent = resolved.parent
    if _SESSION_FOLDER.match(parent.name) and parent.parent != parent:
        return parent.parent / CACHE_DIR_NAME
    return parent / CACHE_DIR_NAME


def sha256_of(path: Path) -> Optional[str]:
    """The file's content digest, or None when it cannot be read (a missing file is the renderer's to report)."""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def _slug(text: str) -> str:
    return _SLUG.sub("_", text.strip()).strip("_") or "unknown"


class RenderCache:
    """``get(docx, pdf, renderer)`` serves a cached render into ``pdf`` or returns None; ``put(docx, pdf, renderer)``
    keeps a fresh render; ``trim()`` enforces the size bound. ``renderer`` is the backend and its version
    (``render.renderer_identity``) — a PDF rendered by one is never served for another."""

    def __init__(self, root: Path, limit_bytes: int = DEFAULT_LIMIT_BYTES):
        self.root = Path(root)
        self.limit_bytes = int(limit_bytes)
        self._seen: Dict[Path, str] = {}            # docx → its digest when ``get`` looked it up (checked by ``put``)

    @classmethod
    def for_out_dir(cls, out_dir: Path) -> Optional["RenderCache"]:
        """The cache a preview or run folder uses, or None when the environment turned caching off."""
        return cls(cache_root_for(out_dir)) if enabled() else None

    # ---- lookup ---------------------------------------------------------------------------------------------------

    def _entry(self, digest: str, renderer: str) -> Tuple[Path, Path]:
        folder = self.root / _slug(renderer)
        return folder / f"{digest}.pdf", folder / f"{digest}.json"

    def get(self, docx: Path, pdf: Path, renderer: str) -> Optional[Path]:
        """``pdf`` (written from the cached render) when the .docx's bytes were rendered by ``renderer`` before; None
        for a miss, an unreadable .docx, a broken entry or a destination that cannot be written."""
        docx, pdf = Path(docx), Path(pdf)
        digest = sha256_of(docx)
        if digest is None:
            return None
        self._seen[docx] = digest
        entry, meta = self._entry(digest, renderer)
        if not self._valid(entry, meta, digest, renderer):
            return None
        try:
            _copy_atomically(entry, pdf)
            os.utime(entry)                      # most recently used: the last to go when the cache is trimmed
        except OSError as exc:
            diag.event("render.cache_unreadable", level="warning", entry=str(entry), reason=str(exc)[:300])
            return None
        diag.event("render.cache_hit", source=str(docx), output=str(pdf), renderer=renderer)
        return pdf

    def _valid(self, entry: Path, meta: Path, digest: str, renderer: str) -> bool:
        """The entry is the PDF its sidecar describes, rendered from these bytes by this renderer; anything else is
        removed so a fresh render takes its place."""
        if not entry.is_file() and not meta.is_file():
            return False                                      # a plain miss
        try:
            if not entry.is_file() or not meta.is_file():
                raise ValueError("the entry and its sidecar are not both there")
            facts = json.loads(meta.read_text(encoding="utf-8"))
            size = entry.stat().st_size
            if not (isinstance(facts, dict) and facts.get("docx_sha256") == digest and facts.get("renderer") == renderer
                    and facts.get("pdf_bytes") == size and size > 0):
                raise ValueError("sidecar does not describe this entry")
            with open(entry, "rb") as fh:
                if fh.read(5) != b"%PDF-":
                    raise ValueError("not a PDF")
            if sha256_of(entry) != facts.get("pdf_sha256"):
                raise ValueError("PDF bytes differ from the sidecar's digest")
            return True
        except (OSError, ValueError) as exc:
            diag.event("render.cache_entry_broken", level="warning", entry=str(entry), reason=str(exc)[:300])
            for p in (entry, meta):
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
            return False

    # ---- storage --------------------------------------------------------------------------------------------------

    def put(self, docx: Path, pdf: Path, renderer: str) -> bool:
        """Keep ``pdf`` as the render of ``docx``'s bytes by ``renderer``. Refused (False) when the .docx cannot be
        read, when its bytes are not the ones ``get`` hashed before the render (a file that changed under the renderer
        would be cached under the wrong key), or when ``pdf`` is not a PDF."""
        docx, pdf = Path(docx), Path(pdf)
        digest = sha256_of(docx)
        if digest is None:
            return False
        before = self._seen.get(docx)
        if before is not None and before != digest:
            diag.event("render.cache_skipped", level="warning", source=str(docx), reason="bytes changed during the render")
            return False
        try:
            size = pdf.stat().st_size
            with open(pdf, "rb") as fh:
                head = fh.read(5)
            if size <= 0 or head != b"%PDF-":
                return False
            pdf_digest = sha256_of(pdf)
            if pdf_digest is None:
                return False
            entry, meta = self._entry(digest, renderer)
            entry.parent.mkdir(parents=True, exist_ok=True)
            _copy_atomically(pdf, entry)
            facts = {"docx_sha256": digest, "pdf_sha256": pdf_digest, "pdf_bytes": size, "renderer": renderer,
                     "stored": time.strftime("%Y-%m-%dT%H:%M:%S")}
            _write_atomically(meta, json.dumps(facts, indent=1).encode("utf-8"))
        except OSError as exc:
            diag.event("render.cache_unwritable", level="warning", root=str(self.root), reason=str(exc)[:300])
            return False
        diag.event("render.cache_stored", source=str(docx), renderer=renderer, pdf_bytes=size)
        return True

    def entries(self) -> List[Tuple[Path, int, float]]:
        """Every PDF entry as (path, bytes, mtime)."""
        out = []
        if not self.root.is_dir():
            return out
        for folder in self.root.iterdir():
            if not folder.is_dir():
                continue
            for entry in folder.glob("*.pdf"):
                try:
                    st = entry.stat()
                except OSError:
                    continue
                out.append((entry, st.st_size, st.st_mtime))
        return out

    def size(self) -> int:
        return sum(size for _, size, _ in self.entries())

    def trim(self, limit_bytes: Optional[int] = None) -> int:
        """Remove the least recently used entries until the PDFs fit the bound. Returns how many were evicted."""
        limit = self.limit_bytes if limit_bytes is None else int(limit_bytes)
        entries = self.entries()
        total = sum(size for _, size, _ in entries)
        evicted = 0
        for entry, size, _ in sorted(entries, key=lambda e: e[2]):
            if total <= limit:
                break
            for p in (entry, entry.with_suffix(".json")):
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
            total -= size
            evicted += 1
        if evicted:
            diag.event("render.cache_trimmed", evicted=evicted, bytes_kept=total, limit_bytes=limit)
        return evicted


def _copy_atomically(source: Path, destination: Path) -> None:
    """A reader never sees a half-written file: copy beside the destination, then replace."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, staged_name = tempfile.mkstemp(prefix=".specsurgeon-cache-", suffix=destination.suffix, dir=destination.parent)
    os.close(fd)
    staged = Path(staged_name)
    try:
        shutil.copyfile(source, staged)
        os.replace(staged, destination)
    finally:
        staged.unlink(missing_ok=True)


def _write_atomically(destination: Path, data: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, staged_name = tempfile.mkstemp(prefix=".specsurgeon-cache-", suffix=destination.suffix, dir=destination.parent)
    staged = Path(staged_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(staged, destination)
    finally:
        staged.unlink(missing_ok=True)
