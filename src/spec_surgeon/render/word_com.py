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


def pywin32_present() -> bool:
    try:
        import win32com.client  # noqa: F401
        import pythoncom  # noqa: F401
    except ImportError:
        return False
    return True


def available() -> bool:
    if sys.platform != "win32":
        return False
    if not pywin32_present():
        return False
    return word_registered()


C2R_CONFIGURATION_KEYS = (r"SOFTWARE\Microsoft\Office\ClickToRun\Configuration",
                          r"SOFTWARE\WOW6432Node\Microsoft\Office\ClickToRun\Configuration")
WINWORD_APP_PATH_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Winword.exe"
WORD_CURVER_KEY = r"Word.Application\CurVer"


def installed_version() -> Optional[str]:
    """The version of the Word installed on this machine, read from the registry without starting Word — the render
    cache's renderer identity (``render.renderer_identity``), asked before any rendering so that a cache hit costs no
    Word start. Click-to-Run installs (Microsoft 365, Office 2019+) report their build as ``VersionToReport``
    (``16.0.17328.20068``); an MSI install gives WINWORD.EXE's own file version; failing both, the registered COM
    server's major version (``Word.Application.16``). None when none of them can be read (no winreg, no Word)."""
    try:
        import winreg
    except ImportError:
        return None
    for key in C2R_CONFIGURATION_KEYS:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
                value, _kind = winreg.QueryValueEx(k, "VersionToReport")
            if value:
                return str(value)
        except OSError:
            continue
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, WINWORD_APP_PATH_KEY) as k:
            exe = winreg.QueryValue(k, None)
        import win32api
        info = win32api.GetFileVersionInfo(str(exe), "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:  # noqa: BLE001 — no App Path, no pywin32 version info: the coarser answer below
        pass
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, WORD_CURVER_KEY) as k:
            value = winreg.QueryValue(k, None)
        return str(value) or None
    except OSError:
        return None


def com_error_text(e: BaseException) -> Optional[str]:
    """A COM error (``pywintypes.com_error``) as one readable line: the HRESULT in hex, its text, and the description
    Word gave (``excepinfo``) — e.g. ``COM error 0x80020009 (Exception occurred.): Microsoft Word: This file could not
    be found.`` None for any other exception."""
    if type(e).__name__ != "com_error":
        return None
    args = getattr(e, "args", ()) or ()
    hresult = getattr(e, "hresult", args[0] if len(args) > 0 else None)
    text = getattr(e, "strerror", args[1] if len(args) > 1 else "")
    info = getattr(e, "excepinfo", args[2] if len(args) > 2 else None)
    out = f"COM error 0x{int(hresult) & 0xFFFFFFFF:08X}" if isinstance(hresult, int) else "COM error"
    if text:
        out += f" ({text})"
    if isinstance(info, (tuple, list)) and len(info) > 2 and (info[1] or info[2]):
        out += ": " + ": ".join(str(x).strip() for x in (info[1], info[2]) if x)
        if len(info) > 5 and isinstance(info[5], int) and info[5]:
            out += f" [scode 0x{info[5] & 0xFFFFFFFF:08X}]"
    return out


class WordSession:
    """Low-level COM adapter; production callers use the bounded worker below."""

    def __init__(self, scratch: Optional[Path] = None, on_started=None):
        self._scratch = scratch
        self._on_started = on_started
        self.stage = "word_start"

    def __enter__(self):
        import pythoncom
        import win32com.client
        from .. import diagnostics as diag
        diag.event("render.word_start.started")
        pythoncom.CoInitialize()
        self.app = None
        self.tmp = self._scratch or Path(tempfile.mkdtemp(prefix="specsurgeon-word-"))
        try:
            with diag.span("render.word_dispatch"):
                self.app = win32com.client.DispatchEx("Word.Application")
            if self._on_started is not None:
                self._on_started(self.app)
            self.app.Visible = False
            self.app.DisplayAlerts = 0
            # Disable document macros in the private instance, including .docx files misnamed by a caller.
            self.app.AutomationSecurity = 3
        except BaseException:
            try:
                if self.app is not None:
                    self.app.Quit(SaveChanges=0)
            finally:
                pythoncom.CoUninitialize()
                if self._scratch is None:
                    shutil.rmtree(self.tmp, ignore_errors=True)
            raise
        diag.event("render.word_start.succeeded", temporary=str(self.tmp))
        return self

    def __exit__(self, *exc):
        from .. import diagnostics as diag
        try:
            self.stage = "word_quit" if not exc or exc[0] is None else self.stage
            with diag.span("render.word_quit"):
                self.app.Quit(SaveChanges=0)
        finally:
            import pythoncom
            pythoncom.CoUninitialize()
            if self._scratch is None:
                shutil.rmtree(self.tmp, ignore_errors=True)

    def _copy(self, path: Path) -> Path:
        dst = self.tmp / path.name
        shutil.copyfile(path, dst)
        return dst

    def render_pdf(self, docx: Path, pdf: Path) -> Path:
        from .. import diagnostics as diag
        self.stage = "word_copy"
        with diag.span("render.word_copy", source=str(docx)):
            src = self._copy(Path(docx))
        self.stage = "word_open"
        with diag.span("render.word_open", source=str(docx), temporary=str(src)):
            doc = self.app.Documents.Open(str(src), ReadOnly=True, AddToRecentFiles=False, Visible=False)
        try:
            self.stage = "word_export"
            with diag.span("render.word_export", source=str(docx), output=str(pdf)):
                doc.ExportAsFixedFormat(str(pdf), WD_FORMAT_PDF)
        except BaseException:
            self.failure_stage = self.stage
            raise
        finally:
            self.stage = "word_close"
            with diag.span("render.word_close", source=str(docx)):
                doc.Close(SaveChanges=0)
        return pdf

    def accept_reject_text(self, docx: Path) -> Tuple[str, str]:
        """(accept-all text, reject-all text) of the main story, from Word itself, on copies."""
        out = []
        for which in ("accept", "reject"):
            src = self._copy(Path(docx))
            self.stage = "word_open"
            doc = self.app.Documents.Open(str(src), ReadOnly=False, AddToRecentFiles=False, Visible=False)
            try:
                self.stage = "word_" + which
                doc.TrackRevisions = False
                if which == "accept":
                    doc.Revisions.AcceptAll()
                else:
                    doc.Revisions.RejectAll()
                out.append(doc.Content.Text)
            except BaseException:
                self.failure_stage = self.stage
                raise
            finally:
                self.stage = "word_close"
                doc.Close(SaveChanges=0)
        return out[0], out[1]


def render_pdfs(files: Sequence[Path], out_dir: Path, log: Optional[Callable[[str], None]] = None,
                detail: Optional[Callable[[str], None]] = None, timeout: Optional[float] = None) -> Dict[Path, Path]:
    """{docx: pdf} for the files Word rendered. One spawned worker starts ONE private Word instance and renders every
    file in turn (``word_worker.run_word_batch``); a file Word cannot open or export is reported on ``log`` — with
    the COM error's code and Word's own description — and left out, and the others still render. A file that
    exceeds ``timeout`` (per file, enforced by the parent) ends that worker and its private Word, and the remaining
    files go on in a fresh one. Only a Word that cannot start at all, with nothing rendered, raises — the
    renderer-selection wrapper then reports the backend as unavailable once. ``detail`` gets Word's version and
    each file's time. Existing PDFs are never reused."""
    from .. import diagnostics as diag
    from .word_worker import run_word_batch
    log = log or (lambda msg: None)
    detail = detail or (lambda msg: None)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = [Path(f) for f in files]
    for f in files:
        (out_dir / (f.stem + ".pdf")).unlink(missing_ok=True)
    with diag.span("render.word_batch", out_dir=str(out_dir), file_count=len(files)) as outcome:
        batch = run_word_batch(files, out_dir, timeout_per_file=timeout)
        outcome.update(workers=batch.workers, version=batch.version,
                       rendered=sum(1 for v in batch.results.values() if isinstance(v, Path)),
                       failed=sum(1 for v in batch.results.values() if not isinstance(v, Path)))
    if batch.start_error is not None:
        raise batch.start_error  # backend unavailable: the renderer-selection wrapper reports it once
    result: Dict[Path, Path] = {}
    for f in files:
        got = batch.results.get(f)
        pdf = out_dir / (f.stem + ".pdf")
        if isinstance(got, Path) and got.is_file():
            result[f] = got
            detail(f"Word {batch.version}: {f.name} → {got.name} in {batch.seconds.get(f, 0.0):.1f}s")
        elif isinstance(got, Exception):
            diag.event("render.word_worker_failed", level="error", source=str(f), output=str(pdf),
                       code=getattr(got, "code", "word-render"), details=getattr(got, "details", {}))
            log(f"{f.name}: Word could not render it: {com_error_text(got) or str(got)}")
        else:
            diag.event("render.file_missing", level="warning", backend="word", source=str(f), output=str(pdf))
            log(f"{f.name}: Word reported no error but wrote no PDF")
    return result


def accept_reject_text(docx: Path, timeout: Optional[float] = None) -> Tuple[str, str]:
    """Word's accept/reject oracle on scratch copies, with the same process deadline as rendering."""
    from .word_worker import run_word
    result = run_word("accept_reject", Path(docx), timeout=timeout)
    return result["accept"], result["reject"]
