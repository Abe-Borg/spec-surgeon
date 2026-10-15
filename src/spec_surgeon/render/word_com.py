"""Word as renderer and accept/reject oracle (Windows only, needs pywin32 and a local Word).

Rules (from the skill's Environment C):
* Always DispatchEx — a NEW Word instance — never Dispatch, which would attach to the user's running
  Word and make Quit() close their session.
* Visible = False; operate only on COPIES in a scratch directory; Close(SaveChanges=False); Quit the
  instance we created.
* Word's rendering is the truth that LibreOffice only approximates; its accept/reject is the oracle
  that handles nested revisions.

Marked [unproven] in the skill until it has run on a real job.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from typing import Callable, Dict, Optional, Sequence, Tuple

WD_FORMAT_PDF = 17
WORD_PROGID_KEY = r"Word.Application\CLSID"


def word_registered() -> bool:
    """Word's COM server is registered on this machine (HKEY_CLASSES_ROOT\\Word.Application\\CLSID). The installed app
    bundles pywin32 whether or not Word exists, so the imports alone say nothing about Word: without this check a
    PC without Word would pick the Word renderer and every render would fail at DispatchEx."""
    try:
        import winreg
    except ImportError:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, WORD_PROGID_KEY):
            return True
    except OSError:
        return False


def available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import win32com.client  # noqa: F401
        import pythoncom  # noqa: F401
    except ImportError:
        return False
    return word_registered()


class WordSession:
    """Context manager around one private Word instance."""

    def __enter__(self):
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        self.app = win32com.client.DispatchEx("Word.Application")
        self.app.Visible = False
        self.app.DisplayAlerts = 0
        self.tmp = Path(tempfile.mkdtemp(prefix="specsurgeon-word-"))
        return self

    def __exit__(self, *exc):
        try:
            self.app.Quit(SaveChanges=0)
        finally:
            import pythoncom
            pythoncom.CoUninitialize()
            shutil.rmtree(self.tmp, ignore_errors=True)

    def _copy(self, path: Path) -> Path:
        dst = self.tmp / path.name
        shutil.copyfile(path, dst)
        return dst

    def render_pdf(self, docx: Path, pdf: Path) -> Path:
        src = self._copy(Path(docx))
        doc = self.app.Documents.Open(str(src), ReadOnly=True, AddToRecentFiles=False, Visible=False)
        try:
            doc.ExportAsFixedFormat(str(pdf), WD_FORMAT_PDF)
        finally:
            doc.Close(SaveChanges=0)
        return pdf

    def accept_reject_text(self, docx: Path) -> Tuple[str, str]:
        """(accept-all text, reject-all text) of the main story, from Word itself, on copies."""
        out = []
        for which in ("accept", "reject"):
            src = self._copy(Path(docx))
            doc = self.app.Documents.Open(str(src), ReadOnly=False, AddToRecentFiles=False, Visible=False)
            try:
                doc.TrackRevisions = False
                if which == "accept":
                    doc.Revisions.AcceptAll()
                else:
                    doc.Revisions.RejectAll()
                out.append(doc.Content.Text)
            finally:
                doc.Close(SaveChanges=0)
        return out[0], out[1]


def render_pdfs(files: Sequence[Path], out_dir: Path, log: Optional[Callable[[str], None]] = None) -> Dict[Path, Path]:
    """{docx: pdf} for the files Word rendered. A file Word cannot open or export is reported on ``log`` and left
    out; the others still render. Word failing to start propagates (the caller reports it and renders nothing)."""
    log = log or (lambda msg: None)
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {}
    with WordSession() as ws:
        for f in files:
            f = Path(f)
            pdf = out_dir / (f.stem + ".pdf")
            try:
                ws.render_pdf(f, pdf)
            except Exception as e:  # noqa: BLE001 — this file's render failed; the others go on
                log(f"{f.name}: Word could not render it: {type(e).__name__}: {e}")
                continue
            if pdf.exists():
                result[f] = pdf
            else:
                log(f"{f.name}: Word reported no error but wrote no PDF")
    return result
