"""Renderer selection: word (Windows + pywin32 + an installed Word) → libreoffice → none.

``render_pdfs`` never raises: a renderer that cannot run (Word would not start, soffice missing or crashing) or a
file it cannot convert is reported through ``log`` and left out of the result, and the caller records "render
failed" for that file (V6) — a rendering problem is never a crashed batch or a preview without its plan. What a
renderer says along the way (LibreOffice's own output, Word's version, a traceback) goes to ``detail`` — the run's
log — so a failed render can be traced to its cause.

Given a ``cache`` (``render.cache.RenderCache``), ``render_pdfs`` serves every file whose bytes this renderer has
rendered before and renders only the rest, keeping their PDFs for next time: the run does not render what the
preview rendered, and an unchanged source attached again is not rendered again. A hit is keyed by the file's bytes
and the renderer's identity (``renderer_identity``): a changed file, or a renderer upgraded since, is a miss.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Callable, Dict, Optional, Sequence

from . import libreoffice, word_com
from . import cache as cache_mod
from .cache import RenderCache
from .. import diagnostics as diag


def pick(prefer: str = "auto") -> Optional[str]:
    if prefer == "none":
        return None
    if prefer in ("auto", "word") and word_com.available():
        return "word"
    if prefer in ("auto", "libreoffice", "word") and libreoffice.find_soffice():
        return "libreoffice"
    return None


def describe(backend: Optional[str]) -> str:
    """Which renderer, where, which version — for the run's log and the diagnostics."""
    if backend == "libreoffice":
        return f"LibreOffice {libreoffice.version() or '(version not found)'} — {libreoffice.find_soffice()}"
    if backend == "word":
        return "Microsoft Word — a private, invisible instance through COM (its version is logged when it starts)"
    return "none"


def renderer_identity(backend: str) -> str:
    """The cache key's renderer part — the backend and the version installed now (``libreoffice 24.2.7.2``,
    ``word 16.0.17328.20068``). A version that cannot be read is ``unknown``: such a cache still never serves another
    file's pages, only an upgrade of that renderer goes unnoticed until its entries age out."""
    if backend == "libreoffice":
        # the version AND the executable: a distribution's build id (Debian's ``420(Build:2)``) is the same across
        # its security updates, and the executable's size and date move with every one
        version = " ".join(x for x in (libreoffice.version(), libreoffice.executable_identity()) if x) or None
    elif backend == "word":
        version = word_com.installed_version()
    else:
        version = None
    return f"{backend} {version or 'unknown'}"


def unavailable_reason(prefer: str = "auto") -> str:
    """Why ``pick`` found no renderer — what to install or set."""
    if prefer == "none":
        return "rendering was turned off"
    out = []
    if prefer in ("auto", "word"):
        if not sys.platform.startswith("win"):
            out.append("Word: Windows only")
        elif not word_com.pywin32_present():
            out.append("Word: pywin32 is not installed")
        elif not word_com.word_registered():
            out.append("Word: not installed on this computer (Word.Application is not registered)")
    out.append("LibreOffice: soffice not found (install LibreOffice, or set SPEC_SURGEON_SOFFICE to soffice's path)")
    return "; ".join(out)


def error_text(e: BaseException) -> str:
    """An exception as one line — a COM error with its HRESULT and Word's own description."""
    return word_com.com_error_text(e) or f"{type(e).__name__}: {e}"


def render_pdfs(files: Sequence[Path], out_dir: Path, backend: str, workers: int = 4,
                log: Optional[Callable[[str], None]] = None,
                detail: Optional[Callable[[str], None]] = None,
                cache: Optional[RenderCache] = None) -> Dict[Path, Path]:
    """{docx: pdf} for the files that rendered (or were served from ``cache``); the others are missing (and said on
    ``log``)."""
    with diag.span("render.batch", backend=backend, out_dir=str(out_dir), file_count=len(files),
                   workers=workers, cache=str(cache.root) if cache is not None else None) as outcome:
        result, served = _render_pdfs(files, out_dir, backend, workers, log, detail, cache)
        outcome.update(rendered=len(result) - served, cached=served,
                       missing=[str(f) for f in files if Path(f) not in result])
        return result


def _render_pdfs(files: Sequence[Path], out_dir: Path, backend: str, workers: int,
                 log: Optional[Callable[[str], None]],
                 detail: Optional[Callable[[str], None]],
                 cache: Optional[RenderCache]):
    log = log or (lambda msg: None)
    detail = detail or (lambda msg: None)
    files = [Path(f) for f in files]
    out_dir = Path(out_dir)
    served: Dict[Path, Path] = {}
    pending = files
    renderer = None
    if cache is not None and cache_mod.enabled() and files:
        renderer = renderer_identity(backend)
        pending = []
        for f in files:
            hit = cache.get(f, out_dir / (f.stem + ".pdf"), renderer)
            if hit is not None:
                served[f] = hit
            else:
                pending.append(f)
        if served:
            detail(f"render cache: {len(served)} of {len(files)} PDF(s) came from earlier renders by {renderer} "
                   f"({cache.root}); {len(pending)} to render")
    rendered: Dict[Path, Path] = {}
    if pending:
        try:
            if backend == "word":
                rendered = word_com.render_pdfs(pending, out_dir, log=log, detail=detail)
            elif backend == "libreoffice":
                rendered = libreoffice.render_pdfs(pending, out_dir, workers=workers, log=log, detail=detail)
        except Exception as e:  # noqa: BLE001 — the renderer itself failed: no PDFs, the batch goes on without them
            diag.event("render.backend_failed", level="error", backend=backend,
                       exception=diag.exception_record(e, "render"))
            log(f"rendering with {backend} failed: {error_text(e)}")
            detail(traceback.format_exc())
        if rendered and cache is not None and renderer is not None:
            kept = sum(1 for f, pdf in rendered.items() if cache.put(f, pdf, renderer))
            cache.trim()
            detail(f"render cache: kept {kept} of {len(rendered)} fresh render(s) for next time")
    result: Dict[Path, Path] = {}
    for f in files:
        pdf = served.get(f) or rendered.get(f)
        if pdf is not None:
            result[f] = Path(pdf)
    return result, len(served)
