"""The Spec Surgeon window: steps on the left (Files → Describe the change → Preview → Run & results), the step on
the right, an update banner above it and a collapsible activity log below.

Every action is a plain call into ``gui.Session`` on a background thread (``worker.start_job``); nothing is applied
before Preview, and Run refuses when a file changed since the preview (approval token) — the window also disables
Run as soon as any input changes after the preview.

``build_window(session, *, checker, downloader, launcher, installed, prompts)`` returns the window; tests and the
installed-app smoke test drive it through ``window._spec_surgeon`` (buttons, values, ``wait``). Dialogs go through
``prompts`` so tests can answer them (a modal dialog would wait forever in an offscreen test).
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable, List, Optional

from PySide6.QtCore import QByteArray, QModelIndex, QRect, QSize, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSplitter,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import __version__, credentials, updater, winapp
from ..gui import Session, open_folder
from ..settings import Settings, load_settings, logs_dir, save_settings
from . import theme
from .models import ROW_ROLE
from .pages import FilesPage, PreviewPage, RequestPage, RunPage, resize_columns
from .rows import (ProgressTracker, generated_rows, preview_counts, preview_rows, result_counts, result_rows,
                   written_back)
from .settings_dialog import SettingsDialog
from .update_ui import UpdateController
from .widgets import ImageViewer, MarkdownDialog, Thumb, label, repolish
from .worker import Bridge, start_job

RESOURCES = Path(__file__).resolve().parent / "resources"
ICON = RESOURCES / "icon.svg"
REPO_URL = f"https://github.com/{updater.REPO}"
EXAMPLES = ["remove all headers and footers", "change all fonts to Arial",
            "make the header and footer match the template spec", "match the font of the template spec",
            "replace \u201cNFPA 13, 2022 Edition\u201d with \u201cNFPA 13, 2025 Edition\u201d"]
STEPS = ["Files", "Describe the change", "Preview", "Run & results"]
READY, ERROR = "Ready.", "Error — see the log."


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


class RecordingPrompts:
    """Answers questions from a list (default yes) and records everything it was shown."""

    def __init__(self, answers: Optional[List[bool]] = None, files: Optional[List[str]] = None):
        self.answers = list(answers or [])
        self.files = list(files or [])
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


class Var:
    """``get``/``set`` over a widget, so tests read and set values the same way they did with Tk variables."""

    def __init__(self, get: Callable[[], Any], set_: Callable[[Any], None]):
        self.get, self.set = get, set_


# ---- the window -----------------------------------------------------------------------------------------

def app_icon() -> QIcon:
    icon = QIcon(str(ICON))
    if icon.isNull():
        png = RESOURCES / "icon-256.png"
        icon = QIcon(str(png)) if png.exists() else QIcon()
    return icon


def _step_icon(n: int, state: str, size: int = 22) -> QIcon:
    """A numbered circle: done (green ✓), current (accent), todo (outline)."""
    t = theme.current or theme.tokens(False, theme.FALLBACK_ACCENT)
    dpr = 2
    pm = QPixmap(size * dpr, size * dpr)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRect(1, 1, size - 2, size - 2)
    font = QFont(QApplication.font())
    font.setBold(True)
    font.setPointSizeF(8)
    p.setFont(font)
    if state == "done":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t.ok_fg))
        p.drawEllipse(rect)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "✓")
    elif state == "current":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t.accent))
        p.drawEllipse(rect)
        p.setPen(QColor(t.accent_text))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(n))
    else:
        p.setPen(QColor(t.muted))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(n))
    p.end()
    return QIcon(pm)


class MainWindow(QMainWindow):
    busyChanged = Signal(bool)

    def __init__(self, session: Session, settings: Settings, prompts=None, fresh: bool = True, **update_kw):
        super().__init__()
        self.session = session
        self.settings = settings
        self.prompts = prompts or Prompts(self)
        self.busy = False
        self.updating = False
        self.checking = False
        self.force_close = False
        self.preview_key: Optional[tuple] = None
        self.tracker: Optional[ProgressTracker] = None
        self._on_done: Optional[Callable[[Any], None]] = None
        self._viewers: List[QWidget] = []
        self.key_origin = self._init_key()
        self.setWindowTitle(f"Spec Surgeon {__version__}")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(860, 540)          # every step scrolls; fits a 1366×768 laptop at 125 % scaling
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else None
        self.resize(min(1280, int(avail.width() * 0.9)) if avail else 1280,
                    min(840, int(avail.height() * 0.9)) if avail else 840)
        self.bridge = Bridge(self)
        self.bridge.log.connect(self._job_log)
        self.bridge.done.connect(self._job_done)
        self.bridge.failed.connect(self._job_failed)
        self.updates = UpdateController(self, **update_kw)
        self.busyChanged.connect(self.updates.on_busy)
        self._build()
        self._load_values(fresh)
        self._refresh_files()
        self._inputs_changed()
        self.go(0)

    # ---- layout -----------------------------------------------------------------------------------
    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("Content")
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._sidebar())
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setObjectName("Divider")
        line.setStyleSheet("QFrame#Divider { color: palette(mid); }")
        h.addWidget(line)
        right = QWidget()
        right.setObjectName("Content")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        banner_box = QWidget()
        banner_box.setObjectName("Content")
        bl = QVBoxLayout(banner_box)
        bl.setContentsMargins(32, 14, 32, 0)
        bl.addWidget(self.updates.banner)
        banner_box.setVisible(True)
        rv.addWidget(banner_box)
        self.stack = QStackedWidget()
        self.files_page = FilesPage(self.browse_dir, self.browse_template)
        self.request_page = RequestPage()
        self.preview_page = PreviewPage()
        self.run_page = RunPage()
        for p in (self.files_page, self.request_page, self.preview_page, self.run_page):
            self.stack.addWidget(p)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.stack)
        self.splitter.addWidget(self._log_panel())
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        rv.addWidget(self.splitter, 1)
        h.addWidget(right, 1)
        self.setCentralWidget(root)
        self._menus()
        self._statusbar()
        self._wire()

    def _sidebar(self) -> QWidget:
        side = QWidget()
        side.setObjectName("Sidebar")
        side.setFixedWidth(232)
        v = QVBoxLayout(side)
        v.setContentsMargins(14, 18, 14, 14)
        v.setSpacing(6)
        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(QSize(34, 34)))
        head.addWidget(logo)
        name = QVBoxLayout()
        name.setSpacing(0)
        name.addWidget(label("Spec Surgeon", "AppName", wrap=False))
        name.addWidget(label(f"Version {__version__}", "Version", wrap=False))
        head.addLayout(name, 1)
        v.addLayout(head)
        v.addSpacing(16)
        self.nav = QListWidget()
        self.nav.setObjectName("StepNav")
        self.nav.setIconSize(QSize(22, 22))
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for i, step in enumerate(STEPS):
            item = QListWidgetItem(_step_icon(i + 1, "todo"), "  " + step)
            item.setSizeHint(QSize(0, 42))
            self.nav.addItem(item)
        self.nav.setFixedHeight(42 * len(STEPS) + 16)
        v.addWidget(self.nav)
        v.addStretch(1)
        self.settings_btn = QPushButton("Settings")
        self.settings_btn.setObjectName("NavButton")
        self.check_btn = QPushButton("Check for updates")
        self.check_btn.setObjectName("NavButton")
        v.addWidget(self.settings_btn)
        v.addWidget(self.check_btn)
        return side

    def _log_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("LogPanel")
        v = QVBoxLayout(panel)
        v.setContentsMargins(24, 2, 24, 8)
        v.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(14)
        self.log_toggle = QPushButton("▸  Activity log")
        self.log_toggle.setObjectName("LogToggle")
        self.log_toggle.setCheckable(True)
        head.addWidget(self.log_toggle)
        head.addStretch(1)
        self.log_copy = QPushButton("Copy")
        self.log_save = QPushButton("Save…")
        self.log_clear = QPushButton("Clear")
        for b in (self.log_copy, self.log_save, self.log_clear):
            b.setObjectName("LinkButton")
            head.addWidget(b)
        v.addLayout(head)
        self.log = QPlainTextEdit()
        self.log.setObjectName("Log")
        self.log.setReadOnly(True)
        self.log.setFont(theme.mono_font())
        self.log.setMaximumBlockCount(20000)
        v.addWidget(self.log, 1)
        self.log_panel = panel
        return panel

    def _menus(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("&File")
        self.act_load = QAction("Load saved spec…", self, shortcut=QKeySequence("Ctrl+O"))
        self.act_open = QAction("Open results folder", self)
        self.act_settings = QAction("Settings…", self, shortcut=QKeySequence("Ctrl+,"))
        self.act_exit = QAction("Exit", self, shortcut=QKeySequence("Ctrl+Q"))
        for a in (self.act_load, self.act_open, self.act_settings):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.act_exit)
        m = mb.addMenu("&Help")
        self.act_check = QAction("Check for updates…", self)
        self.act_whatsnew = QAction("What's new in this version", self)
        self.act_guide = QAction("User guide (online)", self)
        self.act_issue = QAction("Report a problem (online)", self)
        self.act_about = QAction("About Spec Surgeon", self)
        for a in (self.act_check, self.act_whatsnew, self.act_guide, self.act_issue):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.act_about)

    def _statusbar(self) -> None:
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        self.status_lbl = QLabel(READY)
        sb.addWidget(self.status_lbl, 1)
        self.busy_bar = QProgressBar()
        self.busy_bar.setFixedWidth(180)
        self.busy_bar.setTextVisible(False)
        self.busy_bar.setRange(0, 0)
        self.busy_bar.hide()
        sb.addPermanentWidget(self.busy_bar)

    def _wire(self) -> None:
        f, r, p, run = self.files_page, self.request_page, self.preview_page, self.run_page
        self.nav.currentRowChanged.connect(self._nav_changed)
        f.next_btn.clicked.connect(self._go_request)
        r.back_btn.clicked.connect(self._go_files)
        p.back_btn.clicked.connect(self._go_request)
        p.continue_btn.clicked.connect(self._go_run)
        run.back_btn.clicked.connect(self._go_preview)
        self._files_timer = QTimer(self)
        self._files_timer.setSingleShot(True)
        self._files_timer.setInterval(250)
        self._files_timer.timeout.connect(self._refresh_files)
        for picker in (f.folder, f.template, f.out):
            picker.changed.connect(self._path_changed)
        r.request.textChanged.connect(self._inputs_changed)
        r.answers.textChanged.connect(self._inputs_changed)
        r.chips.picked.connect(self._chip_picked)
        r.load_btn.clicked.connect(self.do_load_spec)
        r.preview_btn.clicked.connect(self.do_preview)
        r.key_btn.clicked.connect(self.open_settings)
        r.render_cb.toggled.connect(self._inputs_changed)
        p.open_btn.clicked.connect(self._open_preview_folder)
        p.only_problems.toggled.connect(p.proxy.set_only_problems)
        run.run_btn.clicked.connect(self.do_run)
        run.open_btn.clicked.connect(self.do_open)
        run.report_btn.clicked.connect(self.do_report)
        run.failed_btn.clicked.connect(self._open_failed)
        run.set_aside_btn.clicked.connect(self.do_set_aside)
        run.advise_btn.clicked.connect(self.do_advise)
        run.only_problems.toggled.connect(run.proxy.set_only_problems)
        run.table.doubleClicked.connect(self._open_row)
        p.table.doubleClicked.connect(self._open_row)
        self.settings_btn.clicked.connect(self.open_settings)
        self.check_btn.clicked.connect(self.updates.manual_check)
        self.act_load.triggered.connect(self.do_load_spec)
        self.act_open.triggered.connect(self.do_open)
        self.act_settings.triggered.connect(self.open_settings)
        self.act_exit.triggered.connect(self.close)
        self.act_check.triggered.connect(self.updates.manual_check)
        self.act_whatsnew.triggered.connect(self.show_whats_new)
        self.act_guide.triggered.connect(self._open_guide)
        self.act_issue.triggered.connect(self._open_issues)
        self.act_about.triggered.connect(self.show_about)
        self.log_toggle.toggled.connect(self.set_log_open)
        self.log_copy.clicked.connect(self._copy_log)
        self.log_save.clicked.connect(self._save_log)
        self.log_clear.clicked.connect(self.log.clear)
        for seq in ("Ctrl+Return", "Ctrl+Enter", "F5"):
            QShortcut(QKeySequence(seq), self, activated=self.do_preview)
        resize_columns(p.table, 3, [260, 90, 120, 300])
        resize_columns(run.table, 2, [260, 120, 300, 220])
        self.job_buttons = [r.preview_btn, r.load_btn, run.run_btn, run.set_aside_btn, run.advise_btn,
                            self.act_load]

    # ---- values ---------------------------------------------------------------------------------------
    def _load_values(self, fresh: bool) -> None:
        s, st = self.session, self.settings
        f, r = self.files_page, self.request_page
        if fresh:
            f.folder.setText(st.folder)
            f.template.setText(st.template)
            f.out.setText(st.out_root or str(s.out_root))
            r.request.setPlainText(st.request_draft)
            r.render_cb.setChecked(st.render)
        else:
            f.folder.setText(str(s.folder or ""))
            f.template.setText(str(s.template or ""))
            f.out.setText(str(s.out_root))
            r.render_cb.setChecked(True)
        if s.spec is not None and s.spec_source and s.spec_source != "compiled":
            self._show_loaded_spec()
        r.chips.set_items(EXAMPLES)
        self.refresh_key_label()
        if st.window_geometry:
            try:
                self.restoreGeometry(QByteArray.fromHex(st.window_geometry.encode("ascii")))
            except Exception:
                pass
        self.set_log_open(st.log_open)

    def save_settings(self) -> None:
        st = self.settings
        st.folder = self.files_page.folder.text()
        st.template = self.files_page.template.text()
        st.out_root = self.files_page.out.text()
        st.render = self.request_page.render_cb.isChecked()
        st.request_draft = self.request_page.request.toPlainText()
        st.log_open = self.log_toggle.isChecked()
        st.window_geometry = bytes(self.saveGeometry().toHex()).decode("ascii")
        try:
            save_settings(st)
        except OSError as e:
            self.write(f"Could not save settings: {e}")

    def sync(self) -> None:
        """Read the paths into the session (on the GUI thread, before a job starts)."""
        s, f = self.session, self.files_page
        new_folder = Path(f.folder.text()) if f.folder.text() else None
        if new_folder != s.folder:
            s.reset_set_aside()
            self._show_set_aside()
        s.folder = new_folder
        s.template = Path(f.template.text()) if f.template.text() else None
        if f.out.text():
            s.out_root = Path(f.out.text())

    def _inputs_key(self) -> tuple:
        """Everything the preview depends on. A loaded spec counts only while no request is typed (a typed request
        is compiled instead — ``Session``'s rule). The results folder is part of it: a preview is of a run that
        writes under one results folder, and changing it afterwards must send the user back to Preview rather than
        let Run write into the folder the preview was made for."""
        f, r, s = self.files_page, self.request_page, self.session
        request = r.request.toPlainText().strip()
        loaded = s.spec_source if (not request and s.spec is not None and s.spec_source != "compiled") else ""
        return (f.folder.text(), f.template.text(), f.out.text(), request, r.answers.toPlainText().strip(), loaded,
                tuple(str(x) for x in (s.selected_files or [])))

    # ---- the API key ------------------------------------------------------------------------------------
    def _init_key(self) -> str:
        if os.environ.get(credentials.ENV):
            return "environment"
        saved = credentials.load()
        if saved:
            os.environ[credentials.ENV] = saved
            return "saved"
        return "none"

    def key_status_text(self) -> str:
        return {"environment": "Anthropic API key: from your Windows environment ✓",
                "saved": "Anthropic API key: saved on this computer ✓",
                "session": "Anthropic API key: set for this session only",
                "none": "No Anthropic API key — typed requests need one; saved specs don't."}[self.key_origin]

    def refresh_key_label(self) -> None:
        self.request_page.key_label.setText(self.key_status_text())
        self.request_page.key_btn.setText("Change…" if self.key_origin != "none" else "Set API key…")

    # ---- navigation ------------------------------------------------------------------------------------
    def go(self, index: int) -> None:
        self.nav.setCurrentRow(index)
        self.stack.setCurrentIndex(index)
        self._refresh_nav()

    @Slot(int)
    def _nav_changed(self, row: int) -> None:
        if row >= 0:
            self.stack.setCurrentIndex(row)
            self._refresh_nav()

    @Slot()
    def _go_files(self):
        self.go(0)

    @Slot()
    def _go_request(self):
        self.go(1)

    @Slot()
    def _go_preview(self):
        self.go(2)

    @Slot()
    def _go_run(self):
        self.go(3)

    def _preview_current(self) -> bool:
        s = self.session
        has = s.generated is not None or (s.preview is not None and s.spec is not None)
        return has and self.preview_key == self._inputs_key()

    def _refresh_nav(self) -> None:
        s = self.session
        done = [bool(self._files_ok), bool(self.request_page.request.toPlainText().strip()
                                           or (s.spec is not None and s.spec_source)),
                self._preview_current(), s.last_batch is not None or s.last_trials is not None]
        cur = self.stack.currentIndex()
        for i in range(len(STEPS)):
            state = "current" if i == cur else ("done" if done[i] else "todo")
            self.nav.item(i).setIcon(_step_icon(i + 1, state))

    def _refresh_run_state(self) -> None:
        run = self.run_page
        current = self._preview_current()
        run.run_btn.setEnabled(current and not self.busy and not self.updating)
        if self.busy:
            run.run_hint.setText("")
        elif not (self.session.preview or self.session.generated):
            run.run_hint.setText("Preview first — nothing runs before you have seen the preview.")
        elif not current:
            run.run_hint.setText("Something changed since the preview — press Preview again.")
        else:
            run.run_hint.setText("")
        generated = self.session.generated is not None
        run.inplace_rb.setEnabled(not generated)
        if generated:
            run.copies_rb.setChecked(True)
        self.preview_page.continue_btn.setEnabled(current)

    @Slot()
    def _inputs_changed(self, *args) -> None:
        if hasattr(self, "nav"):
            self._refresh_nav()
            self._refresh_run_state()

    @Slot(str)
    def _path_changed(self, _value: str) -> None:
        self._files_timer.start()
        self._inputs_changed()

    _files_ok = False

    @Slot()
    def _refresh_files(self) -> None:
        from ..batch import expand_inputs
        from ..editspec import is_template_file
        f, s = self.files_page, self.session
        folder, tpl = f.folder.text(), f.template.text()
        f.file_list.clear()
        self._files_ok = False
        if s.selected_files is not None:
            names = [p.name for p in s.selected_files]
            f.set_hint(f.folder_hint, f"Set-aside pass: {len(names)} file(s) from the last run", "ok")
            f.file_list.addItems(names)
            f.file_list.setVisible(bool(names))
            self._files_ok = bool(names)
        elif not folder:
            f.set_hint(f.folder_hint, "Choose the folder with the .docx specs.")
            f.file_list.hide()
            f.folder.set_invalid(False)
        elif not Path(folder).is_dir():
            f.set_hint(f.folder_hint, "Folder not found.", "error")
            f.folder.set_invalid(True)
            f.file_list.hide()
        else:
            f.folder.set_invalid(False)
            try:
                templates = [Path(tpl).resolve()] if tpl and Path(tpl).is_file() else []
                files = [p for p in expand_inputs([folder]) if not is_template_file(p, templates)]
            except Exception as e:
                files = []
                f.set_hint(f.folder_hint, f"Cannot read the folder: {e}", "error")
            else:
                if files:
                    f.set_hint(f.folder_hint, f"{len(files)} Word file(s) ready" +
                               (" (the template file is left out)" if templates and any(
                                   t.parent == Path(folder).resolve() for t in templates) else ""), "ok")
                else:
                    f.set_hint(f.folder_hint, "No .docx files in this folder.", "error")
            f.file_list.addItems([p.name for p in files[:500]])
            f.file_list.setVisible(bool(files))
            self._files_ok = bool(files)
        if not tpl:
            f.set_hint(f.template_hint, "")
            f.template.set_invalid(False)
        elif Path(tpl).is_file() and tpl.lower().endswith(".docx"):
            f.set_hint(f.template_hint, f"Template: {Path(tpl).name}", "ok")
            f.template.set_invalid(False)
        else:
            f.set_hint(f.template_hint, "Template file not found (it must be a .docx).", "error")
            f.template.set_invalid(True)
        self._refresh_nav()

    def _show_set_aside(self) -> None:
        bar = self.files_page.set_aside
        s = self.session
        if s.selected_files is None:
            bar.hide()
            return
        bar.show_message("warn", "Set-aside pass",
                         f"Only the {len(s.selected_files)} file(s) set aside in the last run will be previewed and "
                         "run. Their failure reasons go to Claude with the request.",
                         [("Use the whole folder again", self._clear_set_aside)])

    @Slot()
    def _clear_set_aside(self) -> None:
        self.session.reset_set_aside()
        self._show_set_aside()
        self._refresh_files()
        self._inputs_changed()

    # ---- browsing ----------------------------------------------------------------------------------
    def browse_dir(self, start: str) -> str:
        return self.prompts.pick_dir("Choose a folder", start)

    def browse_template(self, start: str) -> str:
        return self.prompts.pick_file("Choose the template spec", start, "Word documents (*.docx)")

    @Slot(str)
    def _chip_picked(self, text: str) -> None:
        self.request_page.request.setPlainText(text)
        if "template" in text.lower() and not self.files_page.template.text():
            self.request_page.info.show_message("info", "Template spec needed",
                                                "Choose the template spec (e.g. the architect's) on the Files step.",
                                                [("Go to Files", self._go_files)])

    # ---- log, status, busy ----------------------------------------------------------------------------
    def write(self, text: str) -> None:
        self.log.appendPlainText(text)
        sb = self.log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def set_status(self, text: str) -> None:
        self.status_lbl.setText(text)

    @Slot(bool)
    def set_log_open(self, on: bool) -> None:
        if self.log_toggle.isChecked() != on:
            self.log_toggle.setChecked(on)
            return
        self.log.setVisible(on)
        self.log_toggle.setText(("▾" if on else "▸") + "  Activity log")
        for b in (self.log_copy, self.log_save, self.log_clear):
            b.setVisible(on)
        if on:
            self.log_panel.setMaximumHeight(16777215)
            self.splitter.setSizes([max(300, self.height() - 260), 220])
        else:
            self.log_panel.setMaximumHeight(self.log_toggle.sizeHint().height() + 12)

    def set_busy(self, on: bool, text: str = "") -> None:
        self.busy = on
        for b in self.job_buttons:
            b.setEnabled(not on and not self.updating)
        self.busy_bar.setVisible(on)
        self.set_status(text if on else READY)
        self.busyChanged.emit(on)
        self._refresh_run_state()

    def set_updating(self, on: bool) -> None:
        self.updating = on
        for b in self.job_buttons:
            b.setEnabled(not on and not self.busy)
        self._refresh_run_state()

    def set_checking(self, on: bool) -> None:
        self.checking = on
        self.check_btn.setEnabled(not on)
        self.act_check.setEnabled(not on)
        self.check_btn.setText("Checking for updates…" if on else "Check for updates")

    def _work(self, label_: str, fn, on_done: Optional[Callable[[Any], None]] = None, track: bool = False,
              page: Optional[QWidget] = None) -> bool:
        """Run ``fn(log)`` in the background; ``on_done(result)`` runs on the GUI thread; an error is shown on
        ``page`` (the step whose button started the job)."""
        if self.busy or self.updating:
            return False
        self.sync()
        self._on_done = on_done
        self._job_page = page
        self.tracker = ProgressTracker() if track else None
        self.set_busy(True, label_ + "…")
        self.write(f"\n=== {label_} ===")
        for page in (self.request_page, self.preview_page, self.run_page, self.files_page):
            page.info.hide()
        start_job(self.bridge, fn)
        return True

    @Slot(str)
    def _job_log(self, line: str) -> None:
        self.write(line)
        if self.tracker is not None:
            moved = self.tracker.feed(line)
            if moved is not None:
                done, total, phase = moved
                run = self.run_page
                run.progress_box.show()
                if total:
                    run.progress.setRange(0, total)
                    run.progress.setValue(done)
                    run.progress_lbl.setText(f"{phase} — {done} of {total} file(s)")
                else:
                    run.progress.setRange(0, 0)
                    run.progress_lbl.setText(phase + "…")

    @Slot(object)
    def _job_done(self, result) -> None:
        on_done, self._on_done = self._on_done, None
        try:
            if on_done is not None:
                on_done(result)
            elif result:
                self.write(str(result))
        except Exception:
            self.write("ERROR while showing the result:\n" + traceback.format_exc())
        finally:
            self.run_page.progress_box.hide()
            self.set_busy(False)
            self._refresh_nav()

    @Slot(str, str)
    def _job_failed(self, message: str, tb: str) -> None:
        self._on_done = None
        self.write("ERROR: " + message)
        _append_error_log(tb)
        self.run_page.progress_box.hide()
        self.set_busy(False)
        self.set_status(ERROR)
        page = getattr(self, "_job_page", None) or self.stack.currentWidget()
        if page is not self.stack.currentWidget():
            self.go(self.stack.indexOf(page))
        page.info.show_message("error", "That didn't work", message, [("Show log", self._open_log)])

    @Slot()
    def _open_log(self) -> None:
        self.set_log_open(True)

    # ---- actions ------------------------------------------------------------------------------------
    @Slot()
    def do_preview(self) -> bool:
        r = self.request_page
        req = r.request.toPlainText().strip()
        answers = [a for a in r.answers.toPlainText().splitlines() if a.strip()]
        allow_codegen, render = r.codegen_cb.isChecked(), r.render_cb.isChecked()
        s = self.session
        if self.busy or self.updating:
            return False
        self.sync()
        key = self._inputs_key()          # what this preview is of; Run is enabled only while it still holds

        def fn(log):
            out = []
            if s.spec_source and s.spec_source != "compiled" and s.spec is not None and not req:
                out.append(f"Using the spec file {s.spec_source}.")
            else:
                msg = s.compile(req, answers, allow_codegen, log)
                out.append(msg)
                if s.questions:
                    out += [f"  ? {x}" for x in s.questions]
                    return "\n".join(out)
            if s.spec is not None or s.generated is not None:
                out.append(s.make_preview(render, log))
                if s.preview is not None:
                    out.append(f"\nPreview files: {s.out_dir / 'preview'}  — look, then press Run.")
            return "\n".join(out)
        return self._work("Preview", fn, on_done=lambda text: self._after_preview(text, key), page=self.request_page)

    def _after_preview(self, text: str, key: tuple) -> None:
        s, r = self.session, self.request_page
        self.write(text or "")
        if s.questions:
            r.questions.setText("\n".join(f"{i + 1}. {q}" for i, q in enumerate(s.questions)))
            r.answers_card.show()
            r.info.show_message("info", "Claude needs answers", "Answer the questions below (one per line), then "
                                                                "press Preview again.")
            self.go(1)
            return
        r.answers_card.setVisible(bool(r.answers.toPlainText().strip()))
        if s.preview is None and s.generated is None:
            r.info.show_message("warn", "Nothing to preview", (text or "").strip())
            self.go(1)
            return
        self.preview_key = key
        self._fill_preview()
        self.go(2)

    def _fill_preview(self) -> None:
        s, p = self.session, self.preview_page
        p.empty.hide()
        p.body.show()
        p.clear_samples()
        if s.generated is not None:
            g = s.generated
            p.request_lbl.setText("AI-written transform")
            p.summary_lbl.setText("No built-in operation could do this request, so Claude wrote a one-off transform.")
            p.tiles.hide()
            p.notes.hide()
            p.table_card.hide()
            p.samples_card.hide()
            p.generated_card.show()
            p.generated_lbl.setText(f"{g.name} passed verification on its sample files. It is saved at {g.path}.\n"
                                    "Run writes edited copies to the results folder only — never over your files.")
            p.token_lbl.setText("")
            return
        pv = s.preview
        spec = pv.batch_plan.spec
        p.generated_card.hide()
        p.tiles.show()
        p.table_card.show()
        p.samples_card.show()
        p.request_lbl.setText(f"“{spec.get('request') or ''}”")
        p.summary_lbl.setText(spec.get("summary") or "")
        c = preview_counts(pv)
        p.t_files.set(c["files"])
        p.t_planned.set(c["planned"], "ok" if c["planned"] else "")
        p.t_exceptions.set(c["exceptions"], "warn" if c["exceptions"] else "")
        p.t_patterns.set(c["patterns"])
        notes = [f"• {a}" for a in spec.get("assumptions", [])] + [f"• {w}" for w in pv.warnings]
        if notes:
            p.notes.show_message("warn", "Check these assumptions", "\n".join(notes))
        else:
            p.notes.hide()
        p.model.set_rows(preview_rows(pv))
        p.only_problems.setChecked(False)
        for sample in pv.samples:
            p.samples.addWidget(self._sample_widget(sample))
        if not pv.samples:
            p.samples.addWidget(label("No sample could be tried (every file was set aside).", "Muted"))
        p.token_lbl.setText(f"Approval token {pv.token} — Run refuses if any file or the spec changes before it "
                            "runs.")

    def _sample_widget(self, sample) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 4, 0, 4)
        v.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(label(sample.source.name, "CardTitle", wrap=False))
        badge = QLabel()
        badge.setObjectName("Badge")
        if sample.exception:
            badge.setText("EXCEPTION")
            badge.setProperty("kind", "exception")
        else:
            badge.setText("verification PASS" if sample.passed else "verification FAIL")
            badge.setProperty("kind", "pass" if sample.passed else "fail")
        repolish(badge)
        head.addWidget(badge)
        head.addStretch(1)
        v.addLayout(head)
        if sample.exception:
            v.addWidget(label(f"[{sample.exception.get('code')}] {sample.exception.get('reason')}", "Muted"))
        elif not sample.passed:
            failed = [k for k, val in sample.checks.items() if val == "FAIL"]
            v.addWidget(label("Failed checks: " + ", ".join(failed), "Muted"))
        if sample.images:
            for img in sample.images:
                t = Thumb(img)
                t.clicked.connect(self._open_image)
                v.addWidget(t)
        elif not sample.exception:
            v.addWidget(label("No page images (tick “Render pages” on the Describe step to see before/after "
                              "pages).", "Muted"))
        return w

    @Slot(str)
    def _open_image(self, path: str) -> None:
        viewer = ImageViewer(Path(path), self)
        self._viewers.append(viewer)
        viewer.show()

    @Slot()
    def do_run(self) -> bool:
        if self.busy or self.updating:
            return False
        if not self._preview_current():
            self.run_page.info.show_message("warn", "Preview first", self.run_page.run_hint.text() or
                                            "Press Preview before Run.")
            return False
        in_place = self.run_page.inplace_rb.isChecked()
        render = self.request_page.render_cb.isChecked()
        if in_place and not self.prompts.ask(
                "Spec Surgeon", "Write the verified results back over the original files?\n\nOnly files that pass "
                "every check are written, and a file open in Word or changed since the preview is skipped."):
            return False
        s = self.session
        self.run_page.results.hide()
        return self._work("Run", lambda log: s.run(in_place, render, log), on_done=self._after_run, track=True,
                          page=self.run_page)

    def _after_run(self, text: str) -> None:
        s, run = self.session, self.run_page
        self.write(text or "")
        rows = result_rows(s.last_batch) if s.last_batch is not None else generated_rows(s.last_trials)
        run.model.set_rows(rows)
        run.only_problems.setChecked(False)
        c = result_counts(rows)
        run.t_pass.set(c["PASS"], "ok" if c["PASS"] else "")
        run.t_fail.set(c["FAIL"], "error" if c["FAIL"] else "")
        run.t_exc.set(c["EXCEPTION"], "warn" if c["EXCEPTION"] else "")
        problems = c["FAIL"] + c["EXCEPTION"]
        where = s.last_run_dir
        if s.last_batch is not None and any(written_back(f) for f in s.last_batch.files):
            msg = "Verified files were written back over the originals."
        else:
            msg = f"Edited copies that passed every check are in {where / 'outputs'} — your files were not changed."
        if problems:
            msg += " Files with problems were left untouched: load them for the next pass, or ask for advice."
        run.summary.show_message("error" if c["FAIL"] else ("warn" if problems else "ok"),
                                 "Done" if not problems else "Done, with files set aside", msg)
        run.failed_btn.setVisible(bool(where and (where / "failed").is_dir()))
        run.report_btn.setEnabled(bool(where and (where / "BATCH_REPORT.md").exists()))
        run.set_aside_btn.setEnabled(bool(problems))
        run.advise_btn.setEnabled(bool(problems))
        run.results.show()
        self.go(3)

    @Slot()
    def do_load_spec(self) -> bool:
        from ..catalog import EXAMPLES_DIR
        start = str(EXAMPLES_DIR) if EXAMPLES_DIR.is_dir() else str(_installed_examples() or Path.home())
        path = self.prompts.pick_file("Load a saved EditSpec", start, "EditSpec (*.json)")
        if not path:
            return False
        try:
            spec = self.session.load_spec(Path(path))
        except Exception as e:
            self.prompts.warn("Spec Surgeon", f"Cannot use this spec:\n{e}")
            return False
        self.request_page.request.blockSignals(True)
        self.request_page.request.clear()
        self.request_page.request.blockSignals(False)
        self.write(f"\nLoaded spec {path}: {spec.get('request')!r} — {spec.get('summary')}\nPress Preview.")
        self._show_loaded_spec()
        self._inputs_changed()
        self.go(1)
        return True

    def _show_loaded_spec(self) -> None:
        s = self.session
        name = Path(s.spec_source).name
        self.request_page.spec_bar.show_message(
            "info", "Saved spec", f"{name}: “{s.spec.get('request')}” — {s.spec.get('summary') or ''}\n"
                                  "Press Preview to use it (typing a request instead compiles a new spec).",
            [("Remove", self._remove_spec)])

    @Slot()
    def _remove_spec(self) -> None:
        s = self.session
        s.spec, s.spec_source, s.preview, s.generated = None, "", None, None
        self.request_page.spec_bar.hide()
        self._inputs_changed()

    @Slot()
    def do_set_aside(self) -> bool:
        s = self.session

        def done(text):
            self.write(text or "")
            self.preview_key = None
            self._show_set_aside()
            self._refresh_files()
            self.request_page.info.show_message("info", "Set-aside pass", (text or "").splitlines()[0] if text
                                                else "")
            self.go(1)
        return self._work("Load set-aside files", lambda log: s.load_set_aside(), on_done=done, page=self.run_page)

    @Slot()
    def do_open(self) -> None:
        s = self.session
        target = s.last_run_dir or s.out_dir or s.out_root
        if target and Path(target).exists():
            open_folder(Path(target))
        else:
            self.prompts.notify("Spec Surgeon", "Nothing there yet.")

    @Slot()
    def do_report(self) -> None:
        d = self.session.last_run_dir
        report = d / "BATCH_REPORT.md" if d else None
        if not report or not report.exists():
            self.prompts.notify("Spec Surgeon", "No report yet — run first.")
            return
        dlg = MarkdownDialog("Batch report", report.read_text(encoding="utf-8"), self, base=d)
        self._viewers.append(dlg)
        dlg.show()

    @Slot()
    def do_advise(self) -> bool:
        s = self.session

        def done(text):
            self.write(text or "")
            self.run_page.summary.show_message("info", "Advice written", (text or "").strip()[-400:],
                                               [("Open advice folder", self._open_advice)])
        return self._work("Advice on exceptions", lambda log: s.advise(log), on_done=done, page=self.run_page)

    @Slot()
    def _open_advice(self) -> None:
        if self.session.last_run_dir:
            open_folder(self.session.last_run_dir / "advice")

    @Slot()
    def _open_failed(self) -> None:
        if self.session.last_run_dir:
            open_folder(self.session.last_run_dir / "failed")

    @Slot()
    def _open_preview_folder(self) -> None:
        if self.session.out_dir and (self.session.out_dir / "preview").exists():
            open_folder(self.session.out_dir / "preview")

    @Slot(QModelIndex)
    def _open_row(self, index) -> None:
        row = index.data(ROW_ROLE)
        if row is not None and row.open_path and Path(row.open_path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(row.open_path)))

    # ---- settings, help ---------------------------------------------------------------------------------
    def auto_check_enabled(self) -> bool:
        v = self.settings.auto_check_updates
        return self.updates.installed() if v is None else v

    @Slot()
    def open_settings(self) -> None:
        dlg = SettingsDialog(self)
        self._viewers.append(dlg)
        dlg.open()

    def apply_theme(self) -> None:
        app = QApplication.instance()
        if app is not None:
            theme.apply(app, self.settings.theme, self.settings.native_style)
            self._refresh_nav()

    def release_notes_text(self) -> str:
        path = _release_notes_path(__version__)
        return path.read_text(encoding="utf-8") if path else ""

    @Slot()
    def show_whats_new(self) -> None:
        text = self.release_notes_text()
        if not text:
            QDesktopServices.openUrl(QUrl(f"{REPO_URL}/releases/tag/v{__version__}"))
            return
        dlg = MarkdownDialog(f"What's new in Spec Surgeon {__version__}", text, self)
        self._viewers.append(dlg)
        dlg.show()

    @Slot()
    def _open_guide(self) -> None:
        QDesktopServices.openUrl(QUrl(f"{REPO_URL}#readme"))

    @Slot()
    def _open_issues(self) -> None:
        QDesktopServices.openUrl(QUrl(f"{REPO_URL}/issues"))

    def about_text(self) -> str:
        from PySide6 import __version__ as pyside_version
        from ..settings import config_dir, data_dir
        install = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).parents[1]
        return (f"Spec Surgeon {__version__}\n"
                "Provably lossless surgical edits to Word specifications.\n\n"
                f"Qt for Python {pyside_version}, Python {sys.version.split()[0]}\n"
                f"Installed in: {install}\nSettings: {config_dir()}\nDownloads and logs: {data_dir()}\n\n"
                f"{REPO_URL}")

    @Slot()
    def show_about(self) -> None:
        self.prompts.notify("About Spec Surgeon", self.about_text())

    @Slot()
    def _copy_log(self) -> None:
        QApplication.clipboard().setText(self.log.toPlainText())

    @Slot()
    def _save_log(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save the activity log", str(Path.home() / "spec-surgeon-log.txt"),
                                              "Text (*.txt)")
        if path:
            Path(path).write_text(self.log.toPlainText(), encoding="utf-8")

    # ---- closing -----------------------------------------------------------------------------------
    def quit_for_update(self) -> None:
        self.force_close = True
        self.close()
        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(0, app.quit)

    def closeEvent(self, event):
        if self.busy and not self.force_close and not self.prompts.ask(
                "Spec Surgeon", "A Preview or Run is in progress. Close anyway?\n\nA run that started still finishes "
                "its files before the program exits."):
            event.ignore()
            return
        self.save_settings()
        for v in self._viewers:
            v.close()
        event.accept()


# ---- helpers ---------------------------------------------------------------------------------------------

def _installed_examples() -> Optional[Path]:
    p = Path(sys.executable).resolve().parent / "examples" / "specs"
    return p if p.is_dir() else None


def _release_notes_path(version: str) -> Optional[Path]:
    name = f"v{version}.md"
    for base in (Path(sys.executable).resolve().parent / "docs" / "releases",
                 Path(__file__).resolve().parents[3] / "docs" / "releases"):
        if (base / name).is_file():
            return base / name
    return None


def _append_error_log(text: str) -> None:
    try:
        d = logs_dir()
        d.mkdir(parents=True, exist_ok=True)
        log = d / "spec-surgeon.log"
        if log.exists() and log.stat().st_size > 2 * 2 ** 20:
            log.replace(d / "spec-surgeon.log.1")
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} Spec Surgeon {__version__}\n{text}")
    except OSError:
        pass


def qt_app(settings: Optional[Settings] = None) -> QApplication:
    app = QApplication.instance()
    if app is None:
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        app = QApplication(sys.argv[:1])
    app.setApplicationName("Spec Surgeon")
    app.setOrganizationName("Abraham Borg")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(app_icon())
    st = settings or Settings()
    theme.apply(app, st.theme, st.native_style)
    return app


def build_window(session: Optional[Session] = None, *, settings: Optional[Settings] = None, prompts=None,
                 checker=None, downloader=None, launcher=None, installed=None) -> MainWindow:
    """The window. With ``session=None`` it starts from the remembered folders and request; with a session (tests,
    the smoke test) the session's values are used."""
    st = settings if settings is not None else load_settings()
    app = qt_app(st)
    w = MainWindow(session or Session(), st, prompts=prompts, fresh=session is None, checker=checker,
                   downloader=downloader, launcher=launcher, installed=installed)
    f, r, run = w.files_page, w.request_page, w.run_page

    def wait(timeout: float = 60.0) -> str:
        """Pump events until the window is idle (job and update check finished); returns the log text."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if not w.busy and not w.updates.checking and not w.updates.downloading:
                app.processEvents()
                return w.log.toPlainText()
            time.sleep(0.02)
        raise TimeoutError("the window did not become idle")

    def text_var(widget):
        return Var(widget.toPlainText, widget.setPlainText)

    w._spec_surgeon = {
        "session": w.session, "window": w, "app": app, "wait": wait,
        "buttons": {"preview": r.preview_btn, "load": r.load_btn, "run": run.run_btn, "set_aside": run.set_aside_btn,
                    "open": run.open_btn, "advise": run.advise_btn, "check_updates": w.check_btn,
                    "install_update": w.updates.banner.btn_install, "settings": w.settings_btn},
        "vars": {"folder": Var(f.folder.text, f.folder.setText), "template": Var(f.template.text, f.template.setText),
                 "out": Var(f.out.text, f.out.setText),
                 "render": Var(r.render_cb.isChecked, r.render_cb.setChecked),
                 "in_place": Var(run.inplace_rb.isChecked,
                                 lambda on: (run.inplace_rb if on else run.copies_rb).setChecked(True)),
                 "codegen": Var(r.codegen_cb.isChecked, r.codegen_cb.setChecked)},
        "request": text_var(r.request), "answers": text_var(r.answers),
        "log": Var(w.log.toPlainText, lambda t: w.log.setPlainText(t)),
        "status": Var(w.status_lbl.text, w.set_status),
        "busy": lambda: w.busy, "banner": w.updates.banner,
        "preview_model": w.preview_page.model, "results_model": w.run_page.model,
    }
    return w


def _install_excepthook(window_ref: List[Optional[MainWindow]]) -> None:
    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        _append_error_log(text)
        w = window_ref[0]
        try:
            if w is not None:
                w.write("UNEXPECTED ERROR: " + "".join(traceback.format_exception_only(exc_type, exc)).strip())
                QMessageBox.critical(w, "Spec Surgeon", "Something went wrong (details in the activity log and in "
                                                        f"{logs_dir() / 'spec-surgeon.log'}):\n\n{exc}")
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc, tb)
    sys.excepthook = hook


def main(argv=None) -> int:
    winapp.set_app_user_model_id()
    winapp.hold_app_mutex()
    settings = load_settings()
    app = qt_app(settings)
    ref: List[Optional[MainWindow]] = [None]
    _install_excepthook(ref)
    w = build_window(settings=settings)
    ref[0] = w

    def scheme_changed(*_):
        if w.settings.theme == "system":
            w.apply_theme()
    try:
        app.styleHints().colorSchemeChanged.connect(scheme_changed)
    except AttributeError:
        pass

    def commit_data(manager):
        # Windows logoff, or an installer run by hand closing apps (Restart Manager): never cut a Run short.
        if w.busy and manager.allowsInteraction():
            manager.cancel()
            manager.release()
        w.save_settings()
    app.commitDataRequest.connect(commit_data)
    w.show()
    QTimer.singleShot(0, w.updates.startup)
    QTimer.singleShot(4000, w.updates.auto_check)
    return app.exec()
