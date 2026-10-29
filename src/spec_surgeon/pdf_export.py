"""Clean PDFs: a PDF of each verified clean copy (Export → PDF (Clean), ``exports``, docs/CONVERSATIONAL_DESKTOP.md).

The copy is the export run's own: its fixed plan removed every comment and then accepted every tracked change, and the
independent checker passed it (V1–V10), so there is no comment, balloon or redline left in the file for a renderer to
draw. This module only turns it into a PDF and looks at what came out:

* **The renderer is the run's.** Word (a private, invisible instance, never the user's) or else LibreOffice, picked
  as the preview and the run pick it (``render.pick``), through the same render cache (``render.RenderCache``), so the
  PDF of a copy the run rendered for its page checks is that very render, served again.
* **What came out is checked** (``check_pdf``): it opens, it is not password-protected, it has at least one page, and
  no page carries a note (an annotation such as a comment balloon; links and form fields are not notes). A PDF that
  fails is moved to ``pdf-failed/`` (diagnosis only, as a failed copy goes to ``failed/``) and the document has none.
* **Nothing is changed or added.** No document is edited here and no word is written; a copy whose PDF cannot be
  made is said with the renderer's own reason, never retried another way.

``PDF_REPORT.md`` beside the run's other reports lists every PDF (its pages, the copy it came from) and every document
that has none, with why.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import diagnostics as diag
from . import render

PDF_FOLDER = "pdf"                     # under the run folder: the clean PDFs, ready to send
FAILED_FOLDER = "pdf-failed"           # a PDF that came out but failed ``check_pdf``: diagnosis only
REPORT_NAME = "PDF_REPORT.md"
RENDERER_NAMES = {"word": "Microsoft Word", "libreoffice": "LibreOffice"}


@dataclass
class PdfExport:
    """What ``export_pdfs`` did: the PDF of each document (keyed by the document the copy stands for), the documents
    that have none and why, the pages of each PDF, and the renderer that made them."""
    folder: Path
    backend: Optional[str] = None
    renderer: str = ""                                     # the renderer in words (``render.describe``)
    pdfs: Dict[Path, Path] = field(default_factory=dict)
    failed: Dict[Path, str] = field(default_factory=dict)
    pages: Dict[Path, int] = field(default_factory=dict)

    @property
    def renderer_name(self) -> str:
        return RENDERER_NAMES.get(self.backend or "", "")


def renderer(prefer: str = "auto") -> Tuple[Optional[str], str]:
    """``(backend, "")`` for the renderer a PDF would be made with, or ``(None, why)`` when this computer has none."""
    backend = render.pick(prefer)
    return (backend, "") if backend else (None, render.unavailable_reason(prefer))


def check_pdf(pdf: Path) -> str:
    """'' when ``pdf`` is fit to hand over: a PDF that opens without a password, with at least one page and no note on
    any page; otherwise why not, in words."""
    from .verify.geometry import fitz
    try:
        doc = fitz.open(str(pdf))
    except Exception as e:  # noqa: BLE001 — whatever PyMuPDF raises, this PDF cannot be handed over
        return f"the PDF cannot be opened ({type(e).__name__}: {diag.snip(e, 200)})"
    try:
        if not doc.is_pdf:
            return "the renderer wrote something that is not a PDF"
        if doc.needs_pass:
            return "the PDF asks for a password"
        if doc.page_count < 1:
            return "the PDF has no page"
        for number, page in enumerate(doc, 1):
            notes = [annot.type[1] for annot in page.annots()]
            if notes:
                return (f"page {number} carries {len(notes)} note(s) ({', '.join(sorted(set(notes)))}), and a clean "
                        f"PDF has none")
        return ""
    finally:
        doc.close()


def page_count(pdf: Path) -> int:
    from .verify.geometry import fitz
    with fitz.open(str(pdf)) as doc:
        return doc.page_count


def _said_about(messages: List[str], copy: Path) -> str:
    """The renderer's last line about this copy (``render_pdfs`` names a file it could not render at the start of the
    line), or ''."""
    mine = [m for m in messages if m.startswith(f"{copy.name}:")]
    return mine[-1][len(copy.name) + 1:].strip() if mine else ""


def export_pdfs(copies: Dict[Path, Path], folder: Path, prefer: str = "auto",
                cache: Optional[render.RenderCache] = None,
                log: Optional[Callable[[str], None]] = None,
                detail: Optional[Callable[[str], None]] = None) -> PdfExport:
    """A PDF of each verified copy in ``copies`` ({the document it stands for: the copy}) in ``folder``, named after
    the copy (``<stem>.pdf``; the copies' names are unique in a run, so the PDFs' are too). Never raises for one file:
    a copy whose PDF cannot be made, or whose PDF fails ``check_pdf``, is in ``failed`` with the reason."""
    log = log or (lambda msg: None)
    detail = detail or (lambda msg: None)
    folder = Path(folder)
    copies = {Path(k): Path(v) for k, v in copies.items()}
    backend, why = renderer(prefer)
    result = PdfExport(folder, backend, render.describe(backend) if backend else "")
    with diag.span("pdf.export", folder=str(folder), file_count=len(copies), backend=backend) as outcome:
        if backend is None:
            result.failed = {source: f"no program on this computer can make a PDF ({why})" for source in copies}
        elif copies:
            folder.mkdir(parents=True, exist_ok=True)
            said: List[str] = []

            def said_too(message: str) -> None:
                said.append(message)
                log(message)
            log(f"Saving {len(copies)} clean PDF(s) with {result.renderer_name}")
            rendered = render.render_pdfs(list(copies.values()), folder, backend, log=said_too, detail=detail,
                                          cache=cache)
            for source, copy in copies.items():
                pdf = rendered.get(copy)
                if pdf is None or not Path(pdf).is_file():
                    reason = _said_about(said, copy) or f"{result.renderer_name} wrote no PDF and said nothing about it"
                    result.failed[source] = reason
                    diag.event("pdf.file_failed", level="warning", source=str(source), copy=str(copy), reason=reason)
                    continue
                problem = check_pdf(Path(pdf))
                if problem:
                    kept = folder.parent / FAILED_FOLDER / Path(pdf).name
                    kept.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(pdf), str(kept))
                    result.failed[source] = problem
                    diag.event("pdf.file_failed", level="warning", source=str(source), copy=str(copy), reason=problem,
                               moved_to=str(kept))
                    log(f"{copy.name}: its PDF was not kept: {problem}")
                    continue
                result.pdfs[source] = Path(pdf)
                result.pages[source] = page_count(Path(pdf))
                diag.event("pdf.file_ready", source=str(source), copy=str(copy), pdf=str(pdf),
                           pages=result.pages[source])
        outcome.update(saved=len(result.pdfs), failed=len(result.failed))
    write_report(result, copies)
    return result


def write_report(result: PdfExport, copies: Dict[Path, Path]) -> Optional[Path]:
    """``PDF_REPORT.md`` beside the PDF folder: each document's PDF and pages, or why it has none."""
    lines = ["# Clean PDFs", "",
             f"Renderer: {result.renderer or 'none'}", "",
             "Each PDF was made from a copy the independent checker passed after every comment was removed and every "
             "tracked change accepted; each PDF opens, has pages and carries no note.", "",
             "| Document | PDF | Pages | Made from |", "| --- | --- | --- | --- |"]
    for source, copy in copies.items():
        pdf = result.pdfs.get(source)
        if pdf is not None:
            lines.append(f"| {source.name} | {result.folder.name}/{pdf.name} | {result.pages.get(source, '')} | "
                         f"{copy} |")
        else:
            lines.append(f"| {source.name} | none: {result.failed.get(source, 'not made')} | | {copy} |")
    path = result.folder.parent / REPORT_NAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as e:
        diag.event("pdf.report_failed", level="warning", path=str(path), reason=str(e))
        return None
    return path
