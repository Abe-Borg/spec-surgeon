"""Renderer selection: word (Windows + pywin32 + an installed Word) → libreoffice → none.

``render_pdfs`` never raises: a renderer that cannot run (Word would not start, soffice missing or crashing) or a
file it cannot convert is reported through ``log`` and left out of the result, and the caller records "render
failed" for that file (V6) — a rendering problem is never a crashed batch or a preview without its plan. What a
renderer says along the way (LibreOffice's own output, Word's version, a traceback) goes to ``detail`` — the run's
log — so a failed render can be traced to its cause.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Callable, Dict, Optional, Sequence

from . import libreoffice, word_com
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
                detail: Optional[Callable[[str], None]] = None) -> Dict[Path, Path]:
    """{docx: pdf} for the files that rendered; the others are missing (and said on ``log``)."""
    with diag.span("render.batch", backend=backend, out_dir=str(out_dir), file_count=len(files),
                   workers=workers) as outcome:
        result = _render_pdfs(files, out_dir, backend, workers, log, detail)
        outcome.update(rendered=len(result), missing=[str(f) for f in files if f not in result])
        return result


def _render_pdfs(files: Sequence[Path], out_dir: Path, backend: str, workers: int,
                 log: Optional[Callable[[str], None]],
                 detail: Optional[Callable[[str], None]]) -> Dict[Path, Path]:
    log = log or (lambda msg: None)
    detail = detail or (lambda msg: None)
    try:
        if backend == "word":
            return word_com.render_pdfs(files, out_dir, log=log, detail=detail)
        if backend == "libreoffice":
            return libreoffice.render_pdfs(files, out_dir, workers=workers, log=log, detail=detail)
    except Exception as e:  # noqa: BLE001 — the renderer itself failed: no PDFs, the batch goes on without them
        diag.event("render.backend_failed", level="error", backend=backend,
                   exception=diag.exception_record(e, "render"))
        log(f"rendering with {backend} failed: {error_text(e)}")
        detail(traceback.format_exc())
    return {}
