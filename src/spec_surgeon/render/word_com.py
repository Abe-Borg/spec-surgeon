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
from typing import Dict, Optional, Sequence, Tuple

WD_FORMAT_PDF = 17


def available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import win32com.client  # noqa: F401
        import pythoncom  # noqa: F401
    except ImportError:
        return False
    return True


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


def render_pdfs(files: Sequence[Path], out_dir: Path) -> Dict[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {}
    with WordSession() as ws:
        for f in files:
            f = Path(f)
            pdf = out_dir / (f.stem + ".pdf")
            ws.render_pdf(f, pdf)
            result[f] = pdf
    return result
