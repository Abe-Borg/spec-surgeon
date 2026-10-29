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
from typing import Callable, Dict, List, Optional, Sequence, Tuple

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
    return render_pdf_groups([(files, out_dir)], backend, workers, log=log, detail=detail, cache=cache)[0]


def render_pdf_groups(groups: Sequence[Tuple[Sequence[Path], Path]], backend: str, workers: int = 4,
                      log: Optional[Callable[[str], None]] = None,
                      detail: Optional[Callable[[str], None]] = None,
                      cache: Optional[RenderCache] = None) -> List[Dict[Path, Path]]:
    """``render_pdfs`` for several groups at once, each ``(files, out_dir)``: one {docx: pdf} per group, in order.
    LibreOffice renders every group's files side by side (``libreoffice.render_groups``), so a run's sources and its
    copies take one round of processes rather than two; Word renders the groups one after the other, as before. With a
    cache, a file whose bytes another file of the call has too is rendered once and served from the cache to the
    other (a copy no change touched is not rendered beside its source)."""
    groups = [([Path(f) for f in files], Path(out_dir)) for files, out_dir in groups]
    total = sum(len(files) for files, _ in groups)
    fields = {"out_dir": str(groups[0][1])} if len(groups) == 1 else {"out_dirs": [str(d) for _, d in groups]}
    with diag.span("render.batch", backend=backend, **fields, file_count=total, workers=workers,
                   cache=str(cache.root) if cache is not None else None) as outcome:
        results, served = _render_pdf_groups(groups, backend, workers, log, detail, cache)
        outcome.update(rendered=sum(len(r) for r in results) - served, cached=served,
                       missing=[str(f) for (files, _), r in zip(groups, results) for f in files if f not in r])
        return results


def _render_pdf_groups(groups: List[Tuple[List[Path], Path]], backend: str, workers: int,
                       log: Optional[Callable[[str], None]],
                       detail: Optional[Callable[[str], None]],
                       cache: Optional[RenderCache]):
    log = log or (lambda msg: None)
    detail = detail or (lambda msg: None)
    served: List[Dict[Path, Path]] = [{} for _ in groups]
    pending: List[List[Path]] = [list(files) for files, _ in groups]
    later: List[List[Path]] = [[] for _ in groups]      # the same bytes as a file rendered in this call: served after
    renderer = None
    if cache is not None and cache_mod.enabled() and any(files for files, _ in groups):
        renderer = renderer_identity(backend)
        first: Dict[str, Path] = {}
        for g, (files, out_dir) in enumerate(groups):
            pending[g] = []
            for f in files:
                hit = cache.get(f, out_dir / (f.stem + ".pdf"), renderer)
                digest = cache.seen_digest(f)
                if hit is not None:
                    served[g][f] = hit
                elif digest is not None and first.setdefault(digest, f) != f:
                    later[g].append(f)
                else:
                    pending[g].append(f)
            if served[g]:
                detail(f"render cache: {len(served[g])} of {len(files)} PDF(s) came from earlier renders by "
                       f"{renderer} ({cache.root}); {len(files) - len(served[g])} to render")
    rendered = _render_backend(groups, pending, backend, workers, log, detail)
    if cache is not None and renderer is not None and (any(rendered) or any(later)):
        kept = 0
        for r in rendered:
            kept += sum(1 for f, pdf in r.items() if cache.put(f, pdf, renderer))
        for g, files in enumerate(later):
            for f in files:
                hit = cache.get(f, groups[g][1] / (f.stem + ".pdf"), renderer)
                if hit is not None:
                    served[g][f] = hit
        missed = [[f for f in files if f not in served[g]] for g, files in enumerate(later)]
        if any(missed):                                  # its twin did not render or could not be kept: on its own
            for g, r in enumerate(_render_backend(groups, missed, backend, workers, log, detail)):
                rendered[g].update(r)
                kept += sum(1 for f, pdf in r.items() if cache.put(f, pdf, renderer))
        cache.trim()
        if any(rendered):
            detail(f"render cache: kept {kept} of {sum(len(r) for r in rendered)} fresh render(s) for next time")
    results: List[Dict[Path, Path]] = []
    for g, (files, _) in enumerate(groups):
        result: Dict[Path, Path] = {}
        for f in files:
            pdf = served[g].get(f) or rendered[g].get(f)
            if pdf is not None:
                result[f] = Path(pdf)
        results.append(result)
    return results, sum(len(s) for s in served)


def _render_backend(groups: List[Tuple[List[Path], Path]], pending: List[List[Path]], backend: str, workers: int,
                    log: Callable[[str], None], detail: Callable[[str], None]) -> List[Dict[Path, Path]]:
    """Render ``pending[g]`` into group ``g``'s folder. A renderer that fails as a whole renders nothing for the
    groups it was given, says so, and the batch goes on without them."""
    rendered: List[Dict[Path, Path]] = [{} for _ in groups]
    if not any(pending):
        return rendered
    if backend == "word":
        for g, files in enumerate(pending):
            if files:
                rendered[g] = _guarded(backend, log, detail, word_com.render_pdfs, files, groups[g][1],
                                       log=log, detail=detail) or {}
    elif backend == "libreoffice":
        got = _guarded(backend, log, detail, libreoffice.render_groups,
                       [(files, groups[g][1]) for g, files in enumerate(pending)], workers=workers, log=log,
                       detail=detail)
        if got is not None:
            rendered = got
    return rendered


def _guarded(backend: str, say: Callable[[str], None], trace: Callable[[str], None], fn, *args, **kwargs):
    """``fn(*args, **kwargs)``, or None when the renderer itself failed (said on ``say``, its traceback on ``trace``)."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001 — the renderer itself failed: no PDFs, the batch goes on without them
        diag.event("render.backend_failed", level="error", backend=backend,
                   exception=diag.exception_record(e, "render"))
        say(f"rendering with {backend} failed: {error_text(e)}")
        trace(traceback.format_exc())
        return None
