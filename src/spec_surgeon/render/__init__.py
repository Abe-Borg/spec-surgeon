"""Renderer selection: word (Windows + pywin32 + an installed Word) → libreoffice → none.

``render_pdfs`` never raises: a renderer that cannot run (Word would not start, soffice missing or crashing) or a
file it cannot convert is reported through ``log`` and left out of the result, and the caller records "render
failed" for that file (V6) — a rendering problem is never a crashed batch or a preview without its plan.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, Optional, Sequence

from . import libreoffice, word_com


def pick(prefer: str = "auto") -> Optional[str]:
    if prefer == "none":
        return None
    if prefer in ("auto", "word") and word_com.available():
        return "word"
    if prefer in ("auto", "libreoffice", "word") and libreoffice.find_soffice():
        return "libreoffice"
    return None


def render_pdfs(files: Sequence[Path], out_dir: Path, backend: str, workers: int = 4,
                log: Optional[Callable[[str], None]] = None) -> Dict[Path, Path]:
    """{docx: pdf} for the files that rendered; the others are missing (and said on ``log``)."""
    log = log or (lambda msg: None)
    try:
        if backend == "word":
            return word_com.render_pdfs(files, out_dir, log=log)
        if backend == "libreoffice":
            return libreoffice.render_pdfs(files, out_dir, workers=workers, log=log)
    except Exception as e:  # noqa: BLE001 — the renderer itself failed: no PDFs, the batch goes on without them
        log(f"rendering with {backend} failed: {type(e).__name__}: {e}")
    return {}
