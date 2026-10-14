"""Renderer selection: word (Windows + pywin32 + Word) → libreoffice → none."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Sequence

from . import libreoffice, word_com


def pick(prefer: str = "auto") -> Optional[str]:
    if prefer == "none":
        return None
    if prefer in ("auto", "word") and word_com.available():
        return "word"
    if prefer in ("auto", "libreoffice", "word") and libreoffice.find_soffice():
        return "libreoffice"
    return None


def render_pdfs(files: Sequence[Path], out_dir: Path, backend: str, workers: int = 4) -> Dict[Path, Path]:
    if backend == "word":
        return word_com.render_pdfs(files, out_dir)
    if backend == "libreoffice":
        return libreoffice.render_pdfs(files, out_dir, workers=workers)
    return {}
