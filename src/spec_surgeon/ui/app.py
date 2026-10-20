"""The Qt application around the conversation window: the application object and icon, the dialogs tests can
answer, release-notes lookup, the error log, the crash notice and ``main``.

``build_window(session, *, settings, prompts, checker, downloader, launcher, installed)`` returns the conversation
window (``ui.conversation_window``); tests and the installed-app smoke test drive it through
``window._spec_surgeon`` (``wait``) and its widgets. Dialogs go through ``prompts`` so tests can answer them (a
modal dialog would wait forever in an offscreen test).
"""
from __future__ import annotations

import logging
import sys
import time
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QWidget

from .. import __version__, about, winapp
from .. import diagnostics as diag
from ..gui import Session
from ..settings import Settings, load_settings, logs_dir
from . import theme

RESOURCES = Path(__file__).resolve().parent / "resources"
ICON = RESOURCES / "icon.svg"


# ---- dialogs (injectable) -------------------------------------------------------------------------------

class Prompts:
    """Every question the window asks. Tests replace it (``RecordingPrompts``)."""

    def __init__(self, parent: QWidget):
        self.parent = parent

    def ask(self, title: str, text: str) -> bool:
        return QMessageBox.question(self.parent, title, text) == QMessageBox.StandardButton.Yes

    def notify(self, title: str, text: str) -> None:
        QMessageBox.information(self.parent, title, text)

    def warn(self, title: str, text: str) -> None:
        QMessageBox.warning(self.parent, title, text)

    def pick_dir(self, title: str, start: str) -> str:
        return QFileDialog.getExistingDirectory(self.parent, title, start or str(Path.home()))

    def pick_file(self, title: str, start: str, filter_: str) -> str:
        return QFileDialog.getOpenFileName(self.parent, title, start or str(Path.home()), filter_)[0]

    def save_file(self, title: str, start: str, filter_: str) -> str:
        return QFileDialog.getSaveFileName(self.parent, title, start or str(Path.home()), filter_)[0]


class RecordingPrompts:
    """Answers questions from a list (default yes) and records everything it was shown."""

    def __init__(self, answers: Optional[List[bool]] = None, files: Optional[List[str]] = None,
                 texts: Optional[List[str]] = None, items: Optional[List[str]] = None):
        self.answers = list(answers or [])
        self.files = list(files or [])
        self.texts = list(texts or [])
        self.items = list(items or [])
        self.shown: List[tuple] = []

    def ask(self, title, text):
        self.shown.append(("ask", title, text))
        return self.answers.pop(0) if self.answers else True

    def notify(self, title, text):
        self.shown.append(("notify", title, text))

    def warn(self, title, text):
        self.shown.append(("warn", title, text))

    def pick_dir(self, title, start):
        return self.files.pop(0) if self.files else ""

    def pick_file(self, title, start, filter_):
        return self.files.pop(0) if self.files else ""

    def save_file(self, title, start, filter_):
        return self.files.pop(0) if self.files else ""


# ---- the application ------------------------------------------------------------------------------------

def app_icon() -> QIcon:
    icon = QIcon(str(ICON))
    if icon.isNull():
        png = RESOURCES / "icon-256.png"
        icon = QIcon(str(png)) if png.exists() else QIcon()
    return icon


def _release_notes_path(version: str) -> Optional[Path]:
    name = f"v{version}.md"
    for base in (Path(sys.executable).resolve().parent / "docs" / "releases",
                 Path(__file__).resolve().parents[3] / "docs" / "releases"):
        if (base / name).is_file():
            return base / name
    return None


def _append_error_log(text: str) -> None:
    """An error and its traceback into the app log (``spec-surgeon.log``, rotating; set up by ``main``) — or, before
    logging is set up, appended to the file directly."""
    logger = diag.app_log()
    if any(isinstance(h, RotatingFileHandler) for h in logging.getLogger("spec_surgeon").handlers):
        logger.error(text)
        return
    try:
        d = logs_dir()
        d.mkdir(parents=True, exist_ok=True)
        with open(d / diag.APP_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} Spec Surgeon {__version__}\n{text}\n")
    except OSError:
        pass


def qt_app(settings: Optional[Settings] = None) -> QApplication:
    app = QApplication.instance()
    if app is None:
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        app = QApplication(sys.argv[:1])
    app.setApplicationName(about.APP_NAME)
    app.setOrganizationName(about.AUTHOR)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(app_icon())
    theme.apply(app)
    return app


def build_window(session: Optional[Session] = None, **kwargs):
    """The conversation window; see ``ui.conversation_window.build_window``."""
    from .conversation_window import build_window as build_conversation
    return build_conversation(session, **kwargs)


def _install_excepthook(window_ref: List[Optional[QWidget]]) -> None:
    previous_hook = sys.excepthook

    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        _append_error_log("UNEXPECTED ERROR in the window\n" + text)
        w = window_ref[0]
        try:
            if w is not None:
                w.write("UNEXPECTED ERROR: " + "".join(traceback.format_exception_only(exc_type, exc)).strip())
                QMessageBox.critical(w, "Spec Surgeon", "Something went wrong (details in the diagnostic logs at "
                                                        f"{logs_dir() / 'spec-surgeon.log'}):\n\n{exc}")
        except Exception:
            pass
        previous_hook(exc_type, exc, tb)          # preserves diagnostics and any host-installed exception hook
    hook._spec_surgeon = True
    sys.excepthook = hook


def _crash_notice(w, previous: List[dict]) -> None:
    """Say once that the previous session ended without closing, and where its crash report is."""
    last = previous[-1]
    where = last.get("crash_log")
    text = (f"The session started {last.get('started') or '?'} (version {last.get('version') or '?'}) did not close "
            "normally — the program crashed, was ended from Task Manager, or Windows shut down. "
            + (f"Python's fault handler wrote what it saw to {where}." if where else
               "Nothing was written to its crash log; the app log may say what it was doing."))
    w.write("Previous session ended unexpectedly: " + text)
    w._notice("The previous session ended unexpectedly. Your saved conversation is available below.")


def main(argv=None) -> int:
    winapp.set_app_user_model_id()
    winapp.hold_app_mutex()
    settings = load_settings()
    log_path = diag.setup_app_logging(logs_dir())
    watch = diag.CrashWatch(logs_dir())
    previous = watch.start()
    diag.app_log().info(f"Spec Surgeon {__version__} started — {diag.environment_line(diag.environment())}"
                        + (f"; app log {log_path}" if log_path else ""))
    app = qt_app(settings)
    ref: List[Optional[QWidget]] = [None]
    _install_excepthook(ref)
    w = build_window(settings=settings)
    ref[0] = w
    if previous:
        _crash_notice(w, previous)

    def scheme_changed(*_):
        w.apply_theme()                       # the window follows Windows' light/dark setting
    try:
        app.styleHints().colorSchemeChanged.connect(scheme_changed)
    except AttributeError:
        pass

    def commit_data(manager):
        # Windows logoff, or an installer run by hand closing apps (Restart Manager): never cut a run short.
        if w.busy and manager.allowsInteraction():
            manager.cancel()
            manager.release()
        w.save_settings()
    app.commitDataRequest.connect(commit_data)
    w.show()
    QTimer.singleShot(0, w.updates.startup)
    QTimer.singleShot(4000, w.updates.auto_check)
    try:
        return app.exec()
    finally:
        diag.app_log().info("Spec Surgeon closed")
        watch.stop()                     # a clean exit: no "closed unexpectedly" at the next start
