"""The Spec Surgeon window: the four steps on a dark rail at the left (Files → Describe the change → Preview →
Run & results), each with a check mark once it is done and a line saying where it stands; the current step on the
right with its action bar; an update banner above it and a collapsible activity log below.

Every action is a plain call into ``gui.Session`` on a background thread (``worker.start_job``); nothing is applied
before Preview, and Run refuses when a file changed since the preview (approval token) — the window also disables
Run as soon as any input changes after the preview.

``build_window(session, *, checker, downloader, launcher, installed, prompts)`` returns the window; tests and the
installed-app smoke test drive it through ``window._spec_surgeon`` (buttons, values, ``wait``). Dialogs go through
``prompts`` so tests can answer them (a modal dialog would wait forever in an offscreen test).
"""
from __future__ import annotations

import logging
import os
import sys
import time
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

from PySide6.QtCore import QByteArray, QModelIndex, QSize, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QSplitter, QStackedWidget, QVBoxLayout, QWidget)

from .. import __version__, about, credentials, llm, winapp
from .. import diagnostics as diag
from ..gui import NEEDS_KEY, NEEDS_KEY_ADVICE, NEEDS_KEY_REPLY, Session, open_folder
from ..settings import Settings, load_settings, logs_dir, save_settings
from . import theme
from .about_dialog import AboutDialog, about_text
from .chat import cost_line, current_choices, transcript, waiting_for_user
from .models import ROW_ROLE
from .pages import FilesPage, PreviewPage, RequestPage, RunPage, resize_columns
from .rows import (ProgressTracker, chain_rows, generated_rows, preview_counts, preview_rows, result_counts, result_rows,
                   written_back)
from .settings_dialog import SettingsDialog
from .update_ui import UpdateController
from .widgets import (STEP_STATE_ROLE, STEP_STATUS_ROLE, ImageViewer, MarkdownDialog, StepDelegate, Thumb, label,
                      repolish, set_pill)
from .worker import Bridge, start_job

RESOURCES = Path(__file__).resolve().parent / "resources"
ICON = RESOURCES / "icon.svg"
REPO_URL = about.GITHUB_URL
EXAMPLES = ["remove all headers and footers", "change all fonts to Arial",
            "make the header and footer match the template spec", "match the font of the template spec",
            "replace \u201cNFPA 13, 2022 Edition\u201d with \u201cNFPA 13, 2025 Edition\u201d"]
STEPS = ["Files", "Describe the change", "Preview", "Run & results"]
STEP_HINTS = ["Pick the specs folder", "Say what should change", "Check before anything runs", "Apply and verify"]
NEW_SNIPPET = "＋ New snippet… (a paragraph you insert often)"     # the last entry of Your library…
SNIPPET_SOURCE = "your snippet "                                     # Session.use_snippet's spec_source starts so


def is_snippet_source(source: str) -> bool:
    return (source or "").startswith(SNIPPET_SOURCE)


def loaded_name(source: str) -> str:
    """How a loaded spec is named: a spec file by its name, one of your snippets as itself (its name is free text)."""
    return source if is_snippet_source(source) else Path(source).name
# keyless mode (no API key): step 2 is choosing a saved spec or an item of Your library
KEYLESS_STEP, KEYLESS_HINT = "Choose the change", "A saved spec or Your library"
NO_RESULTS = {"PASS": 0, "FAIL": 0, "EXCEPTION": 0}
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

    def ask_text(self, title: str, label_: str, default: str = "") -> str:
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self.parent, title, label_, text=default)
        return text.strip() if ok else ""

    def pick_item(self, title: str, label_: str, items: List[str]) -> str:
        from PySide6.QtWidgets import QInputDialog
        item, ok = QInputDialog.getItem(self.parent, title, label_, items, 0, False)
        return item if ok else ""

    def save_file(self, title: str, start: str, filter_: str) -> str:
        return QFileDialog.getSaveFileName(self.parent, title, start or str(Path.home()), filter_)[0]

    def pick_sequence(self, title: str, label_: str, items: List[str]) -> List[str]:
        """Several of ``items`` in an order the user builds (Chain recipes…): [] when cancelled or empty."""
        dlg = SequenceDialog(title, label_, items, self.parent)
        try:
            return dlg.picked() if dlg.exec() == QDialog.DialogCode.Accepted else []
        finally:
            dlg.deleteLater()


class SequenceDialog(QDialog):
    """Build an ordered sequence out of a list: the items on the left, the sequence on the right, Add / Remove /
    Up / Down between them. Modal; every widget has this dialog as its Qt parent (the Qt rules in CLAUDE.md)."""

    def __init__(self, title: str, label_: str, items: List[str], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(640, 380)
        v = QVBoxLayout(self)
        v.addWidget(label(label_, "Muted"))
        row = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(label("Your recipes", "CardTitle", wrap=False))
        self.available = QListWidget(self)
        for it in items:
            self.available.addItem(QListWidgetItem(it))
        left.addWidget(self.available)
        row.addLayout(left, 1)
        mid = QVBoxLayout()
        mid.addStretch(1)
        self.add_btn = QPushButton("Add \u2192", self)
        self.remove_btn = QPushButton("\u2190 Remove", self)
        self.up_btn = QPushButton("Up", self)
        self.down_btn = QPushButton("Down", self)
        for b in (self.add_btn, self.remove_btn, self.up_btn, self.down_btn):
            mid.addWidget(b)
        mid.addStretch(1)
        row.addLayout(mid)
        right = QVBoxLayout()
        right.addWidget(label("The chain, in the order the stages run", "CardTitle", wrap=False))
        self.chain = QListWidget(self)
        right.addWidget(self.chain)
        row.addLayout(right, 1)
        v.addLayout(row)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Use this chain")
        v.addWidget(self.buttons)
        self.add_btn.clicked.connect(self._add)
        self.remove_btn.clicked.connect(self._remove)
        self.up_btn.clicked.connect(lambda: self._move(-1))
        self.down_btn.clicked.connect(lambda: self._move(1))
        self.available.itemDoubleClicked.connect(lambda _item: self._add())
        self.chain.itemDoubleClicked.connect(lambda _item: self._remove())
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self._refresh()

    def picked(self) -> List[str]:
        return [self.chain.item(i).text() for i in range(self.chain.count())]

    @Slot()
    def _add(self) -> None:
        for it in self.available.selectedItems():
            self.chain.addItem(QListWidgetItem(it.text()))
        self.chain.setCurrentRow(self.chain.count() - 1)
        self._refresh()

    @Slot()
    def _remove(self) -> None:
        for it in self.chain.selectedItems():
            self.chain.takeItem(self.chain.row(it))
        self._refresh()

    def _move(self, delta: int) -> None:
        row = self.chain.currentRow()
        if row < 0 or not 0 <= row + delta < self.chain.count():
            return
        it = self.chain.takeItem(row)
        self.chain.insertItem(row + delta, it)
        self.chain.setCurrentRow(row + delta)
        self._refresh()

    def _refresh(self) -> None:
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self.chain.count() > 0)


class RecordingPrompts:
    """Answers questions from a list (default yes) and records everything it was shown."""

    def __init__(self, answers: Optional[List[bool]] = None, files: Optional[List[str]] = None,
                 texts: Optional[List[str]] = None, items: Optional[List[str]] = None,
                 sequences: Optional[List[List[str]]] = None):
        self.answers = list(answers or [])
        self.files = list(files or [])
        self.texts = list(texts or [])
        self.items = list(items or [])
        self.sequences = [list(x) for x in (sequences or [])]
        self.shown: List[tuple] = []

    def pick_sequence(self, title, label_, items):
        self.shown.append(("pick_sequence", title, "\n".join(items)))
        return self.sequences.pop(0) if self.sequences else []

    def ask_text(self, title, label_, default=""):
        self.shown.append(("ask_text", title, label_))
        return self.texts.pop(0) if self.texts else ""

    def pick_item(self, title, label_, items):
        self.shown.append(("pick_item", title, "\n".join(items)))
        return self.items.pop(0) if self.items else ""

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
        self._files_count = 0
        self._result_counts = dict(NO_RESULTS)
        self.key_origin = self._init_key()
        self.claude_ok = True                  # Claude can be called (Session.claude_ready); set by refresh_key_label
        self.signed_in = False                 # no key here, but the SDK found a sign-in (``ant auth login``)
        self._advise_gated = False             # keyless mode turned Advise off (``_gate_claude_buttons``)
        self.setWindowTitle(f"Spec Surgeon {__version__}")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(900, 560)          # every step scrolls; fits a 1366×768 laptop at 125 % scaling
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
        right = QWidget()
        right.setObjectName("Content")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        banner_box = QWidget()
        banner_box.setObjectName("Content")
        bl = QVBoxLayout(banner_box)
        bl.setContentsMargins(36, 14, 36, 0)
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
        side.setFixedWidth(258)
        v = QVBoxLayout(side)
        v.setContentsMargins(14, 20, 14, 14)
        v.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(QSize(36, 36)))
        head.addWidget(logo)
        name = QVBoxLayout()
        name.setSpacing(0)
        name.addWidget(label("Spec Surgeon", "AppName", wrap=False))
        name.addWidget(label(f"Version {__version__}", "Version", wrap=False))
        head.addLayout(name, 1)
        v.addLayout(head)
        v.addSpacing(18)
        v.addWidget(label("Four steps", "SideHeading", wrap=False))
        self.nav = QListWidget()
        self.nav.setObjectName("StepNav")
        self.nav.setItemDelegate(StepDelegate(self.nav))
        self.nav.setMouseTracking(True)
        self.nav.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setCursor(Qt.CursorShape.PointingHandCursor)
        for step, hint in zip(STEPS, STEP_HINTS):
            item = QListWidgetItem(step)
            item.setData(STEP_STATE_ROLE, "todo")
            item.setData(STEP_STATUS_ROLE, hint)
            item.setToolTip(f"Go to {step}")
            self.nav.addItem(item)
        self.nav.setFixedHeight(StepDelegate.ROW_HEIGHT * len(STEPS) + 8)
        v.addWidget(self.nav)
        v.addStretch(1)
        divider = QFrame()
        divider.setObjectName("SidebarDivider")
        v.addWidget(divider)
        v.addSpacing(4)
        self.settings_btn = QPushButton("Settings")
        self.settings_btn.setToolTip("Results folder and Anthropic API key (Ctrl+,)")
        self.check_btn = QPushButton("Check for updates")
        self.check_btn.setToolTip("Spec Surgeon also checks once a day on its own")
        self.about_btn = QPushButton("About Spec Surgeon")
        self.about_btn.setToolTip("Version, license, and how to reach the author")
        # keyless mode: one line on the rail, all the time, so the missing Claude features never come as a surprise
        self.keyless_note = label("No API key: saved specs and Your library work; Claude's features are off "
                                  "(Settings).", "SideNote")
        self.keyless_note.setToolTip("Claude's features need an Anthropic API key:\n" +
                                     "\n".join(f"\u2022 {f}" for f in llm.CLAUDE_FEATURES))
        self.keyless_note.hide()
        v.addWidget(self.keyless_note)
        for b in (self.settings_btn, self.check_btn, self.about_btn):
            b.setObjectName("NavButton")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            v.addWidget(b)
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
        self.act_library = QAction("Your library…", self, shortcut=QKeySequence("Ctrl+L"))
        self.act_chain = QAction("Chain recipes…", self)
        self.act_library_folder = QAction("Open library folder", self)
        self.act_open = QAction("Open results folder", self)
        self.act_settings = QAction("Settings…", self, shortcut=QKeySequence("Ctrl+,"))
        self.act_exit = QAction("Exit", self, shortcut=QKeySequence("Ctrl+Q"))
        for a in (self.act_load, self.act_library, self.act_chain, self.act_library_folder, self.act_open,
                  self.act_settings):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.act_exit)
        m = mb.addMenu("&Help")
        self.act_check = QAction("Check for updates…", self)
        self.act_whatsnew = QAction("What's new in this version", self)
        self.act_guide = QAction("User guide (online)", self)
        self.act_logs = QAction("Open logs folder", self)
        self.act_bundle = QAction("Save diagnostics bundle…", self)
        self.act_bundle.setToolTip("A zip of the last run's reports, logs, traces and records and the app's own logs "
                                   "— never the documents — for whoever diagnoses a problem")
        self.act_about = QAction("About Spec Surgeon", self)
        for a in (self.act_check, self.act_whatsnew, self.act_guide):
            m.addAction(a)
        m.addSeparator()
        for a in (self.act_logs, self.act_bundle):
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
        for view in (r.chat, p.chat):
            view.send.connect(self.do_send)
            view.chosen.connect(self.do_choose)
            view.start_over.connect(self.do_start_over)
            view.image_clicked.connect(self._open_image)
        r.chips.picked.connect(self._chip_picked)
        r.load_btn.clicked.connect(self.do_load_spec)
        r.preview_btn.clicked.connect(self.do_preview)
        r.key_btn.clicked.connect(self.open_settings)
        r.keyless_key_btn.clicked.connect(self.open_settings)
        r.render_cb.toggled.connect(self._inputs_changed)
        p.open_btn.clicked.connect(self._open_preview_folder)
        p.review_ack_btn.clicked.connect(self.do_ack_review)
        p.empty.button.clicked.connect(self._go_request)
        p.only_problems.toggled.connect(p.proxy.set_only_problems)
        run.run_btn.clicked.connect(self.do_run)
        run.open_btn.clicked.connect(self.do_open)
        run.report_btn.clicked.connect(self.do_report)
        run.diag_btn.clicked.connect(self.do_diagnostics)
        run.failed_btn.clicked.connect(self._open_failed)
        run.set_aside_btn.clicked.connect(self.do_set_aside)
        run.advise_btn.clicked.connect(self.do_advise)
        run.only_problems.toggled.connect(run.proxy.set_only_problems)
        run.table.doubleClicked.connect(self._open_row)
        p.table.doubleClicked.connect(self._open_row)
        self.settings_btn.clicked.connect(self.open_settings)
        self.check_btn.clicked.connect(self.updates.manual_check)
        self.about_btn.clicked.connect(self.show_about)
        self.act_load.triggered.connect(self.do_load_spec)
        self.act_library.triggered.connect(self.do_library)
        self.act_library_folder.triggered.connect(self._open_library_folder)
        r.library_btn.clicked.connect(self.do_library)
        r.chain_btn.clicked.connect(self.do_chain)
        self.act_chain.triggered.connect(self.do_chain)
        p.save_btn.clicked.connect(self.do_save_to_library)
        self.act_open.triggered.connect(self.do_open)
        self.act_settings.triggered.connect(self.open_settings)
        self.act_exit.triggered.connect(self.close)
        self.act_check.triggered.connect(self.updates.manual_check)
        self.act_whatsnew.triggered.connect(self.show_whats_new)
        self.act_guide.triggered.connect(self._open_guide)
        self.act_about.triggered.connect(self.show_about)
        self.act_logs.triggered.connect(self.open_logs)
        self.act_bundle.triggered.connect(self.save_bundle)
        self.log_toggle.toggled.connect(self.set_log_open)
        self.log_copy.clicked.connect(self._copy_log)
        self.log_save.clicked.connect(self._save_log)
        self.log_clear.clicked.connect(self.log.clear)
        for seq in ("Ctrl+Return", "Ctrl+Enter", "F5"):
            QShortcut(QKeySequence(seq), self, activated=self._ctrl_enter)
        resize_columns(p.table, 3, [260, 90, 120, 300])
        resize_columns(run.table, 2, [260, 120, 300, 220])
        self.job_buttons = [r.preview_btn, r.load_btn, run.run_btn, run.set_aside_btn, run.advise_btn,
                            p.review_ack_btn, r.library_btn, r.chain_btn, p.save_btn, self.act_load, self.act_library,
                            self.act_chain]

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

    def _widget_key(self, keep_spec: bool = True, request: Optional[str] = None) -> tuple:
        """The inputs a preview depends on that live in the widgets. A loaded spec counts only while no request is
        typed (a typed request is compiled instead — ``Session``'s rule). The results folder is part of it: a preview
        is of a run that writes under one results folder, and changing it afterwards must send the user back to
        Preview rather than let Run write into the folder the preview was made for. So is "Render pages": a preview
        made without page images has had no visual check, and Run renders (or not) as the preview did.
        ``keep_spec=False``: the key as it reads once the loaded spec is gone — one of your operations replaces it
        (``Session.use_operation``); the key is still taken before the job, from the widgets the trial is made of.
        ``request``: the request box as it will read (one of your operations clears it once it passes)."""
        f, r, s = self.files_page, self.request_page, self.session
        request = self.typed_request() if request is None else request.strip()
        loaded = s.spec_source if (keep_spec and not request and s.spec is not None and s.spec_source != "compiled") \
            else ""
        chain = (s.chain_source, s.chain_label()) if (keep_spec and not request and s.chain is not None) else ()
        return (f.folder.text(), f.template.text(), f.out.text(), request, loaded, chain,
                tuple(str(x) for x in (s.selected_files or [])), r.render_cb.isChecked())

    def _chat_key(self) -> tuple:
        """The conversation a preview came from: which one, and how many times the user had spoken in it (a reply
        voids the old preview)."""
        c = self.session.conversation
        return (c.id, c.user_turns()) if c is not None else ()

    def _inputs_key(self) -> tuple:
        """Everything the preview depends on — read from the widgets before a job, from the conversation after it."""
        return self._widget_key() + self._chat_key()

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
        if self.signed_in:                     # no key here, but the SDK found an `ant auth login` profile
            return "No API key set here — Claude uses your Anthropic sign-in (ant auth login) ✓"
        return {"environment": "Anthropic API key: from your Windows environment ✓",
                "saved": "Anthropic API key: saved on this computer ✓",
                "session": "Anthropic API key: set for this session only",
                "none": "No Anthropic API key — saved specs and Your library work; Claude's features are off."
                }[self.key_origin]

    def refresh_key_label(self) -> None:
        """The key's status line and pill — and the window's mode: with Claude, or keyless (``_apply_claude_mode``).
        Called at start and whenever Settings changes or forgets the key."""
        self.claude_ok = self.session.claude_ready()
        self.signed_in = self.key_origin == "none" and llm.credentials_found()
        r = self.request_page
        r.key_label.setText(self.key_status_text())
        r.key_btn.setText("Change…" if self.key_origin != "none" else "Set API key…")
        has = self.key_origin != "none" or self.signed_in
        set_pill(r.key_pill, "API key" if self.key_origin != "none" else ("Signed in" if self.signed_in else
                                                                          "No API key"),
                 "ok" if has else "warn")
        self._apply_claude_mode()

    def typed_request(self) -> str:
        """The request typed in the box — "" in keyless mode: the box is hidden there (its draft is kept for when a key
        is set), so a request is never compiled, and a loaded spec counts as it would with the box empty."""
        return self.request_page.request.toPlainText().strip() if self.claude_ok else ""

    def _apply_claude_mode(self) -> None:
        """Keyless mode (no API key) or not: the Describe step's layout and wording, the rail, the Preview step's empty
        state, the rail's note and the Claude-only buttons. Nothing is cleared: a key set later brings it all back."""
        on = self.claude_ok
        r, p = self.request_page, self.preview_page
        r.set_claude(on, list(llm.CLAUDE_FEATURES))
        step = STEPS[1] if on else KEYLESS_STEP
        item = self.nav.item(1)
        item.setText(step)
        item.setToolTip(f"Go to {step}")
        p.empty.text.setText("Describe the change (or load a saved spec) and press Preview. The plan for every file "
                             "and the verified sample files appear here." if on else
                             "Choose a saved spec or one from Your library and press Preview. The plan for every file "
                             "and the verified sample files appear here.")
        if p.empty.button is not None:
            p.empty.button.setText(f"Go to {step}")
        p.back_btn.setText("\u2190  Change request" if on else f"\u2190  {step}")
        self.keyless_note.setVisible(not on)
        self._gate_claude_buttons()
        self._show_set_aside()
        self._inputs_changed()

    def _gate_claude_buttons(self) -> None:
        """Buttons that only Claude can serve stay off in keyless mode, with the reason as their tooltip. When a key
        comes back (Settings), a button the gate turned off is turned on again as the last run left it: Advise while
        that run set files aside and no job is running (Codex on #20: it used to stay off until the next job)."""
        adv = self.run_page.advise_btn
        if not self.claude_ok:
            adv.setEnabled(False)
            adv.setToolTip(NEEDS_KEY_ADVICE)
            self._advise_gated = True
        else:
            adv.setToolTip("Claude writes a recommendation or a corrected spec per set-aside file (advice/)")
            if self._advise_gated:
                c = self._result_counts
                adv.setEnabled(bool(c["FAIL"] + c["EXCEPTION"]) and not self.busy and not self.updating)
                self._advise_gated = False

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
        has = s.generated is not None or (s.preview is not None and (s.spec is not None or s.chain is not None))
        return has and self.preview_key == self._inputs_key()

    def _refresh_nav(self) -> None:
        """The rail: a check mark for every step that is done, and under each title where it stands."""
        s = self.session
        request = " ".join(self.typed_request().split())
        loaded = (s.spec is not None and bool(s.spec_source) and s.spec_source != "compiled") or s.chain is not None
        current = self._preview_current()
        has_preview = s.generated is not None or (s.preview is not None and (s.spec is not None or s.chain is not None))
        ran = s.last_batch is not None or s.last_trials is not None or s.last_chain is not None
        done = [bool(self._files_ok), bool(request or loaded or s.generated is not None), current, ran]
        if self._files_ok:
            n = self._files_count
            files = f"{n} Word file{'s' if n != 1 else ''} ready"
            if self.files_page.template.text() and not self.files_page.template.edit.property("invalid") == "true":
                files += " · template set"
        else:
            files = STEP_HINTS[0]
        if (request and waiting_for_user(s) and s.conversation is not None
                and " ".join(s.conversation.request.split()) == request):
            describe = "Waiting for your reply"
        elif request:
            describe = f"\u201c{request}\u201d"
        elif s.generated is not None:
            describe = f"Custom transform: {s.generated.name}"
        elif s.chain is not None:
            describe = f"Chain: {s.chain_label()}"
        elif loaded:
            describe = f"Saved spec: {loaded_name(s.spec_source)}"
        else:
            describe = STEP_HINTS[1] if self.claude_ok else KEYLESS_HINT
        if current and s.generated is not None:
            preview = "AI-written transform, verified on samples"
        elif current:
            c = preview_counts(s.preview)
            preview = f"{c['planned']} plan cleanly · {c['exceptions']} set aside"
        elif has_preview:
            preview = "Changed since the preview — preview again"
        else:
            preview = STEP_HINTS[2]
        if current and s.run_blocked_reason():
            preview = "Look at Claude's findings before Run"
        if ran:
            c = self._result_counts
            problems = c["FAIL"] + c["EXCEPTION"]
            results = f"{c['PASS']} passed" + (f" · {problems} with problems" if problems else " · no problems")
        else:
            results = STEP_HINTS[3]
        cur = self.stack.currentIndex()
        for i, status in enumerate((files, describe, preview, results)):
            state = "current" if i == cur else ("done" if done[i] else "todo")
            item = self.nav.item(i)
            item.setData(STEP_STATE_ROLE, state)
            item.setData(STEP_STATUS_ROLE, status)
        self.nav.viewport().update()

    def _refresh_run_state(self) -> None:
        run, p, r, s = self.run_page, self.preview_page, self.request_page, self.session
        current = self._preview_current()
        blocked = self.session.run_blocked_reason() if current else ""
        run.run_btn.setEnabled(current and not blocked and not self.busy and not self.updating)
        if self.busy:
            hint = ""
        elif not (s.preview or s.generated):
            hint = "Preview first — nothing runs before you have seen the preview."
        elif not current:
            hint = "Something changed since the preview — press Preview again."
        elif blocked:
            # the action bar holds one short line; the full reason is the tooltip and do_run's message
            hint = "Claude's visual check flagged problems — see the Preview step"
        else:
            hint = ""
        run.run_hint.setText(hint)
        run.run_hint.setToolTip(blocked)
        p.continue_hint.setText("Preview first" if not (s.preview or s.generated) else
                                ("Something changed — press Preview again" if not current else
                                 ("Look at Claude's findings above first" if blocked else "")))
        generated = s.generated is not None
        run.inplace_rb.setEnabled(not generated)
        if generated:
            run.copies_rb.setChecked(True)
        p.continue_btn.setEnabled(current)
        live = current and not self.busy and not self.updating     # both refuse anything but the current preview:
        p.save_btn.setEnabled(live)                                # never a live button that silently does nothing
        p.review_ack_btn.setEnabled(live)
        request = self.typed_request()
        loaded = (s.spec is not None and bool(s.spec_source) and s.spec_source != "compiled") or s.chain is not None
        if not self._files_ok:
            r.preview_hint.setText("Choose the specs folder first (step 1)")
        elif not (request or loaded or s.generated is not None):
            r.preview_hint.setText("Type a request or load a saved spec" if self.claude_ok else
                                   "Load a saved spec or use Your library")
        elif (waiting_for_user(s) and s.conversation is not None
              and s.conversation.request.strip() == request):          # cheap: runs on every keystroke
            r.preview_hint.setText("Claude is waiting for your reply")
        else:
            r.preview_hint.setText("Ctrl+Enter")

    @Slot()
    def _inputs_changed(self, *args) -> None:
        if hasattr(self, "nav"):
            self._refresh_nav()
            self._refresh_run_state()
            self._refresh_chat_block()
            self._refresh_preview_empty()
            self._refresh_spec_bar()

    def _refresh_spec_bar(self) -> None:
        """The "Saved spec" bar stays only while that spec is the session's outcome. A compiled spec, a custom
        transform or one of your operations replaces it — and the bar's Remove would throw the new outcome away."""
        s = self.session
        if not (s.spec is not None and s.spec_source and s.spec_source != "compiled") and s.chain is None:
            self.request_page.spec_bar.hide()

    def _refresh_preview_empty(self) -> None:
        """The Preview step's empty state once there is nothing previewed behind it: a reply that only asks, Start
        over, a saved operation or a failed preview voids the outcome (``Session._set_outcome``). A preview that is
        merely out of date stays on show, with the "Changed since the preview" hints."""
        s, p = self.session, self.preview_page
        if s.preview is None and s.generated is None:
            p.body.hide()
            p.empty.show()

    def _refresh_chat(self) -> None:
        """Show the conversation (both views) — or hide it when there is none."""
        s = self.session
        for view in (self.request_page.chat, self.preview_page.chat):
            if s.conversation is None:
                view.hide()
                continue
            view.set_transcript(transcript(s.conversation), current_choices(s), cost_line(s.conversation),
                                waiting_for_user(s))
        self._refresh_chat_block()
        self._refresh_preview_empty()
        self._refresh_spec_bar()

    def _refresh_chat_block(self) -> None:
        s = self.session
        c = s.conversation
        typed = self.request_page.request.toPlainText().strip()
        reason = ""
        if c is not None and not self.claude_ok:
            reason = NEEDS_KEY_REPLY
        elif c is not None and typed != c.request.strip():
            reason = ("The request above is not the one this conversation is about — press Preview to start a new "
                      "conversation, or Start over.")
        for view in (self.request_page.chat, self.preview_page.chat):
            view.set_blocked(reason)

    @Slot(str)
    def _path_changed(self, _value: str) -> None:
        f = self.files_page
        f.drop.setVisible(not f.folder.text() and self.session.selected_files is None)   # at once, not after the timer
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
        self._files_count = 0
        ready_hint = "Choose a folder with .docx files to continue"
        if s.selected_files is not None:
            names = [p.name for p in s.selected_files]
            f.set_hint(f.folder_hint, f"Set-aside pass: {len(names)} file(s) from the last run", "ok")
            f.file_list.addItems(names)
            f.file_list.setVisible(bool(names))
            self._files_ok = bool(names)
            self._files_count = len(names)
        elif not folder:
            f.set_hint(f.folder_hint, "")
            f.file_list.hide()
            f.folder.set_invalid(False)
        elif not Path(folder).is_dir():
            f.set_hint(f.folder_hint, "Folder not found.", "error")
            ready_hint = "That folder was not found"
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
                ready_hint = "That folder cannot be read"
            else:
                if files:
                    f.set_hint(f.folder_hint, f"{len(files)} Word file(s) ready" +
                               (" (the template file is left out)" if templates and any(
                                   t.parent == Path(folder).resolve() for t in templates) else ""), "ok")
                else:
                    f.set_hint(f.folder_hint, "No .docx files in this folder.", "error")
                    ready_hint = "No .docx files in that folder"
            f.file_list.addItems([p.name for p in files[:500]])
            f.file_list.setVisible(bool(files))
            self._files_ok = bool(files)
            self._files_count = len(files)
        f.drop.setVisible(not folder and s.selected_files is None)
        f.folder_hint.setVisible(bool(f.folder_hint.text()))
        if not tpl:
            f.set_hint(f.template_hint, "")
            f.template.set_invalid(False)
        elif Path(tpl).is_file() and tpl.lower().endswith(".docx"):
            f.set_hint(f.template_hint, f"Template: {Path(tpl).name}", "ok")
            f.template.set_invalid(False)
        else:
            f.set_hint(f.template_hint, "Template file not found (it must be a .docx).", "error")
            f.template.set_invalid(True)
        f.template_hint.setVisible(bool(f.template_hint.text()))
        f.set_ready(self._files_ok, ready_hint)
        self._refresh_nav()
        self._refresh_run_state()

    def _show_set_aside(self) -> None:
        bar = self.files_page.set_aside
        s = self.session
        if s.selected_files is None:
            bar.hide()
            return
        bar.show_message("warn", "Set-aside pass",
                         f"Only the {len(s.selected_files)} file(s) set aside in the last run will be previewed and "
                         "run. " + ("Their failure reasons go to Claude with the request." if self.claude_ok else
                                    "Load a saved spec or use Your library for them on the next step."),
                         [("Use the whole folder again", self._clear_set_aside)])

    @Slot()
    def _clear_set_aside(self) -> None:
        self.session.reset_set_aside()            # ends the conversation (its files were the set-aside ones)
        self._show_set_aside()
        self._refresh_chat()
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
        diag.app_log().info(text)          # the app log keeps every activity line (Help → Open logs folder)

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
        self._gate_claude_buttons()
        for view in (self.request_page.chat, self.preview_page.chat):
            view.set_busy(on or self.updating)
        self.busy_bar.setVisible(on)
        self.set_status(text if on else READY)
        self.busyChanged.emit(on)
        self._refresh_run_state()

    def set_updating(self, on: bool) -> None:
        self.updating = on
        for b in self.job_buttons:
            b.setEnabled(not on and not self.busy)
        self._gate_claude_buttons()
        for view in (self.request_page.chat, self.preview_page.chat):
            view.set_busy(on or self.busy)
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
            self._refresh_chat()
            self._refresh_nav()

    @Slot(str, str)
    def _job_failed(self, message: str, tb: str) -> None:
        self._on_done = None
        self.log.appendPlainText("ERROR: " + message + "\n" + "\n".join("    " + line for line in tb.rstrip().splitlines()))
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())
        _append_error_log(f"{message}\n{tb}")
        self.run_page.progress_box.hide()
        self.set_busy(False)
        self._refresh_chat()
        self.set_status(ERROR)
        page = getattr(self, "_job_page", None) or self.stack.currentWidget()
        if page is not self.stack.currentWidget():
            self.go(self.stack.indexOf(page))
        if page is self.run_page and self.run_page.results.isHidden():
            self.run_page.empty.show()
        page.info.show_message("error", "That didn't work", message, [("Show log", self._open_log),
                                                                      ("Save diagnostics…", self.save_bundle)])

    @Slot()
    def _open_log(self) -> None:
        self.set_log_open(True)

    # ---- actions ------------------------------------------------------------------------------------
    @Slot()
    def _ctrl_enter(self) -> None:
        """Ctrl+Enter / F5: send the reply being typed, else preview."""
        focus = QApplication.focusWidget()
        for view in (self.request_page.chat, self.preview_page.chat):
            if focus is view.reply:
                if view.send_btn.isEnabled():
                    view._on_send()
                return
        self.do_preview()

    @Slot()
    def do_preview(self) -> bool:
        r = self.request_page
        req = self.typed_request()
        render = r.render_cb.isChecked()
        s = self.session
        if self.busy or self.updating:
            return False
        self.sync()
        wkey = self._widget_key()          # what this preview is of; Run is enabled only while it still holds
        loaded = bool(((s.spec_source and s.spec_source != "compiled" and s.spec is not None) or s.chain is not None)
                      and not req)
        # a custom transform or one of your operations with no request typed: preview it again (make_preview tries it
        # again on the files as they are now) — never "type what you want done"
        transform = not loaded and not req and s.generated is not None
        if not self.claude_ok and not loaded and not transform:
            # keyless: nothing to preview but a saved spec or one of your operations — never a compile that must fail
            r.info.show_message("info", "Choose a saved spec", NEEDS_KEY,
                                [("Your library…", self.do_library), ("Load saved spec…", self.do_load_spec),
                                 ("Set API key…", self.open_settings)])
            self.go(1)
            return False
        continuing = not loaded and not transform and s.conversation is not None and s.conversation_matches(req)
        if continuing and s.spec is None and s.generated is None:
            r.info.show_message("info", "Claude is waiting for your reply",
                                "Answer in the chat below (or pick one of Claude's choices), or Start over.")
            self.go(1)
            r.chat.reply.setFocus()
            return False

        def fn(log):
            out = []
            if loaded and s.chain is not None:
                out.append(f"Using the chain {s.chain_label()!r} ({len(s.chain['stages'])} stage(s)).")
            elif loaded:
                out.append(f"Using {s.spec_source}." if is_snippet_source(s.spec_source)
                           else f"Using the spec file {s.spec_source}.")
            elif transform:
                out.append(f"Using the custom transform {s.generated.name}.")
            elif not continuing:
                out.append(s.compile(req, log))
                if s.spec is None and s.generated is None:
                    return "\n".join(out)
            out.append(s.make_preview(render, log))
            if s.preview is not None:
                out.append(f"\nPreview files: {s.out_dir / 'preview'}  — look, then press Run.")
            return "\n".join(out)
        return self._work("Preview", fn, on_done=lambda text: self._after_preview(text, wkey), page=self.request_page)

    def _reply(self, text: str = "", choice_id: Optional[str] = None) -> bool:
        s = self.session
        if self.busy or self.updating or s.conversation is None:
            return False
        if not self.claude_ok or self.request_page.request.toPlainText().strip() != s.conversation.request.strip():
            self._refresh_chat_block()
            return False
        render = self.request_page.render_cb.isChecked()
        page = self.stack.currentWidget()
        self.sync()
        if s.conversation is None:            # the folder changed: sync ended the conversation (a new set of files)
            self._refresh_chat()
            target = page if page in (self.request_page, self.preview_page) else self.request_page
            target.info.show_message("info", "The conversation ended",
                                     "The specs folder changed, so the conversation ended — press Preview to start "
                                     "again with these files.")
            return False
        wkey = self._widget_key()

        def fn(log):
            out = [s.reply(text, choice_id, log, render)]
            if s.spec is not None or s.generated is not None:
                out.append(s.make_preview(render, log))
            return "\n".join(out)

        def done(result):
            for view in (self.request_page.chat, self.preview_page.chat):
                view.clear_reply()
            self._after_preview(result, wkey)
        return self._work("Reply", fn, on_done=done, page=page if page in (self.request_page, self.preview_page)
                          else self.request_page)

    @Slot(str)
    def do_send(self, text: str) -> bool:
        return self._reply(text=text)

    @Slot(str)
    def do_choose(self, choice_id: str) -> bool:
        return self._reply(choice_id=choice_id)

    @Slot()
    def do_start_over(self) -> bool:
        s = self.session
        if self.busy or self.updating or s.conversation is None:
            return False
        if not self.prompts.ask("Spec Surgeon", "Start over? The conversation with Claude is forgotten (its record "
                                "stays in the results folder) and nothing is applied."):
            return False
        s.end_conversation()
        s.remove_spec()
        self.preview_key = None
        self._refresh_chat()
        self._inputs_changed()
        self.go(1)
        return True

    @Slot()
    def _show_chat_reply(self) -> None:
        """Scroll the Describe step so Claude's latest message, its choices and the reply box are in view."""
        r = self.request_page
        if r.chat.isVisible():
            r.scroll.ensureWidgetVisible(r.chat.send_btn, 0, 24)     # the chat's bottom edge: choices and reply above it
            r.chat.reply.setFocus()

    def _after_preview(self, text: str, wkey: tuple) -> None:
        s, r = self.session, self.request_page
        self.write(text or "")
        self._refresh_chat()
        if waiting_for_user(s):
            turn = s.turn
            title = {"failed": "That didn't work", "unsupported": "No built-in operation does this"}.get(
                turn.status, "Claude needs your reply")
            r.info.show_message("warn" if turn.status in ("failed", "unsupported") else "info", title,
                                "See the conversation below: reply in the chat, or pick one of Claude's choices.")
            self.go(1)
            QTimer.singleShot(0, self._show_chat_reply)     # after the new bubbles are laid out
            return
        if s.preview is None and s.generated is None:
            r.info.show_message("warn", "Nothing to preview", (text or "").strip())
            self.go(1)
            return
        self.preview_key = wkey + self._chat_key()
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
            p.generated_card.show()
            gp = s.generated_preview
            p.samples_card.setVisible(gp is not None)
            for sample in (gp.samples if gp is not None else []):
                p.samples.addWidget(self._sample_widget(sample))
            if gp is not None and gp.warnings:
                p.notes.show_message("warn", "Check these", "\n".join(f"\u2022 {w}" for w in gp.warnings))
            p.generated_lbl.setText(f"{g.name} passed verification on its sample files. It is saved at {g.path}.\n"
                                    "Run writes edited copies to the results folder only — never over your files.")
            p.token_lbl.setText("")
            self._fill_review()
            return
        pv = s.preview
        spec = pv.batch_plan.spec
        p.generated_card.hide()
        p.tiles.show()
        p.table_card.show()
        p.samples_card.show()
        if s.chain is not None:
            from ..chain import stage_name
            stages = s.chain["stages"]
            p.request_lbl.setText(f"Your chain “{s.chain_label()}” — {len(stages)} stage(s), one after another")
            p.summary_lbl.setText("\n".join(f"{i}. {stage_name(st, i)}: “{st['spec'].get('request') or ''}” — "
                                            f"{st['spec'].get('summary') or ''}" for i, st in enumerate(stages, 1))
                                  + "\n\nThe table shows stage 1's plan on every file; the samples below went through "
                                    "every stage (their pages show the final result).")
        else:
            p.request_lbl.setText((f"Your recipe “{s.recipe_name}” (as saved) for " if s.recipe_name else "")
                                  + f"“{spec.get('request') or ''}”")
            p.summary_lbl.setText(spec.get("summary") or "")
        c = preview_counts(pv)
        p.t_files.set(c["files"], "accent")
        p.t_planned.set(c["planned"], "ok" if c["planned"] else "")
        p.t_exceptions.set(c["exceptions"], "warn" if c["exceptions"] else "")
        p.t_patterns.set(c["patterns"])
        notes = ([] if s.chain is not None else [f"• {a}" for a in spec.get("assumptions", [])]) \
            + [f"• {w}" for w in pv.warnings]
        if notes:
            p.notes.show_message("warn", "Check these assumptions" if s.chain is None else "Check these", "\n".join(notes))
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
        self._fill_review()

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

    def _fill_review(self) -> None:
        """Claude's visual check of the current preview (spec or custom transform) — findings, never a PASS."""
        s, p = self.session, self.preview_page
        pv, token = s._current_review()
        rv = getattr(pv, "review", None) if pv is not None else None
        if rv is None:
            p.review_card.hide()
            return
        p.review_card.show()
        p.review_card.helper.setText(p.REVIEW_HELP[not rv.skipped])
        lines = []
        if rv.skipped:
            lines.append(f"Skipped: {rv.skipped}")
        else:
            if rv.summary:
                lines.append(rv.summary)
            for f in rv.findings:
                where = " ".join(x for x in (f.get("file") or "", f"p.{f['page']}" if f.get("page") else "") if x)
                lines.append(f"[{f['severity']}] {where + ': ' if where else ''}{f['text']}")
            if not rv.findings:
                lines.append("No findings.")
            if rv.usage.calls:
                lines.append(f"Claude: {rv.usage.line()}")
        p.review_lbl.setText("\n".join(lines))
        blocking = rv.blocking()
        acked = bool(blocking) and getattr(pv, "review_ack", "") == token
        if blocking and not acked:
            p.review_bar.show_message("warn", "Look before you run",
                                      f"Claude flagged {len(blocking)} problem(s) it rates as blocking. Look at the "
                                      f"pages above; Run stays off until you acknowledge them (or change the request).")
        elif acked:
            p.review_bar.show_message("info", "Acknowledged", "You have looked at Claude's findings for this preview.")
        else:
            p.review_bar.hide()
        p.review_ack_btn.setVisible(bool(blocking) and not acked)

    @Slot()
    def do_ack_review(self) -> bool:
        s = self.session
        if self.busy or self.updating or not self._preview_current():
            return False
        pv, token = s._current_review()
        try:
            s.acknowledge_review(token)
        except ValueError as e:
            self.preview_page.info.show_message("warn", "Preview again", str(e))
            return False
        self.write("Claude's visual-check findings acknowledged for this preview.")
        self._fill_review()
        self._refresh_run_state()
        return True

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
        blocked = self.session.run_blocked_reason()
        if blocked:
            self.run_page.info.show_message("warn", "Look at Claude's findings first", blocked)
            return False
        in_place = self.run_page.inplace_rb.isChecked()
        render = self.request_page.render_cb.isChecked()
        if in_place and not self.prompts.ask(
                "Spec Surgeon", "Write the verified results back over the original files?\n\nOnly files that pass "
                "every check are written, and a file open in Word or changed since the preview is skipped."):
            return False
        s = self.session
        self.run_page.results.hide()
        self.run_page.empty.hide()
        return self._work("Run", lambda log: s.run(in_place, render, log), on_done=self._after_run, track=True,
                          page=self.run_page)

    def _after_run(self, text: str) -> None:
        s, run = self.session, self.run_page
        self.write(text or "")
        rows = (chain_rows(s.last_chain) if s.last_chain is not None else
                result_rows(s.last_batch) if s.last_batch is not None else generated_rows(s.last_trials))
        run.model.set_rows(rows)
        run.only_problems.setChecked(False)
        c = result_counts(rows)
        self._result_counts = dict(c)
        run.t_pass.set(c["PASS"], "ok" if c["PASS"] else "")
        run.t_fail.set(c["FAIL"], "error" if c["FAIL"] else "")
        run.t_exc.set(c["EXCEPTION"], "warn" if c["EXCEPTION"] else "")
        problems = c["FAIL"] + c["EXCEPTION"]
        where = s.last_run_dir
        if s.last_chain is not None:
            if s.last_chain.in_place:
                msg = "Files that passed a stage were written back over the originals, stage by stage."
            else:
                msg = (f"Edited copies that passed every stage are in {where / 'outputs'} — your files were not "
                       f"changed.")
            if problems:
                msg += (" Files that stopped at a stage are listed with the stage and why: load them for the next pass"
                        + (", or ask for advice." if self.claude_ok else " (View diagnostics says why)."))
        elif s.last_batch is not None and any(written_back(f) for f in s.last_batch.files):
            msg = "Verified files were written back over the originals."
        else:
            msg = f"Edited copies that passed every check are in {where / 'outputs'} — your files were not changed."
        if problems and s.last_chain is None:
            msg += (" Files with problems were left untouched: load them for the next pass"
                    + (", or ask for advice." if self.claude_ok else " (View diagnostics says why)."))
        run.summary.show_message("error" if c["FAIL"] else ("warn" if problems else "ok"),
                                 "Done" if not problems else "Done, with files set aside", msg)
        run.failed_btn.setVisible(bool(where and (where / "failed").is_dir()))
        run.report_btn.setEnabled(bool(where and ((where / "BATCH_REPORT.md").exists()
                                                  or (where / "CHAIN_REPORT.md").exists())))
        run.diag_btn.setEnabled(bool(where and (where / "DIAGNOSTICS.md").exists()))
        run.set_aside_btn.setEnabled(bool(problems))
        run.advise_btn.setEnabled(bool(problems))
        self._gate_claude_buttons()
        run.empty.hide()
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
        self._clear_request()
        self.write(f"\nLoaded spec {path}: {spec.get('request')!r} — {spec.get('summary')}\nPress Preview.")
        self._show_loaded_spec()
        self._refresh_chat()                      # loading a spec ends the conversation: both chat views go
        self._inputs_changed()
        self.go(1)
        return True

    def _clear_request(self) -> None:
        """Empty the request box (a loaded spec, a recipe or one of your operations is the outcome now), without the
        keystroke handlers."""
        self.request_page.request.blockSignals(True)
        self.request_page.request.clear()
        self.request_page.request.blockSignals(False)

    def _show_loaded_spec(self) -> None:
        s = self.session
        name = loaded_name(s.spec_source)
        self.request_page.spec_bar.show_message(
            "info", "Saved spec", f"{name}: “{s.spec.get('request')}” — {s.spec.get('summary') or ''}\n"
                                  "Press Preview to use it (typing a request instead compiles a new spec).",
            [("Remove", self._remove_spec)])

    @Slot()
    def _remove_spec(self) -> None:
        self.session.remove_spec()
        self.request_page.spec_bar.hide()
        self._inputs_changed()

    # ---- your library ----
    @Slot()
    def do_library(self) -> bool:
        from ..library import find, list_items
        if self.busy or self.updating:
            return False
        items = list_items()
        labels = [i.label() + (f" — {i.description}" if i.description else "") for i in items] + [NEW_SNIPPET]
        intro = ("Use a saved recipe, a chain, one of your operations or a snippet:" if items else
                 "Your library is empty. Save a previewed spec as a recipe, or a custom transform as one of your "
                 "operations, with “Save to library…” on the Preview step — or add a paragraph you insert often:")
        picked = self.prompts.pick_item("Your library", intro, labels)
        if not picked:
            return False
        if picked == NEW_SNIPPET:
            return self._new_snippet()
        item = items[labels.index(picked)]
        s = self.session
        if item.kind == "snippet":
            return self._use_snippet(item.name)
        if item.kind == "chain":
            try:
                chain = s.load_chain(item.name)
            except Exception as e:
                self.prompts.warn("Spec Surgeon", f"Cannot use this chain:\n{e}")
                return False
            self._clear_request()
            self.write(f"\nLoaded chain {item.name!r}: {len(chain['stages'])} stage(s) — "
                       + " → ".join(st.get("name") or "" for st in chain["stages"]) + "\nPress Preview.")
            self._show_loaded_chain()
            self._refresh_chat()
            self._inputs_changed()
            self.go(1)
            return True
        if item.kind == "recipe":
            try:
                spec = s.load_recipe(item.name)
            except Exception as e:
                self.prompts.warn("Spec Surgeon", f"Cannot use this recipe:\n{e}")
                return False
            self._clear_request()
            self.write(f"\nLoaded recipe {item.name!r}: {spec.get('request')!r} — {spec.get('summary')}\nPress Preview.")
            self._show_loaded_spec()
            self._refresh_chat()
            self._inputs_changed()
            self.go(1)
            return True
        render = self.request_page.render_cb.isChecked()
        self.sync()
        # use_operation replaces a loaded spec and ends the conversation, and the request box is cleared once it passes
        # (below): the key is taken now, from the widgets the trial is made of (they stay editable while it runs), as
        # it reads then
        wkey = self._widget_key(keep_spec=False, request="")
        name = item.name

        def fn(log):
            return s.use_operation(name, log, render) + "\n" + s.make_preview(render, log)

        def done(text):
            # the operation is the outcome now: the words typed for the conversation it ended are not its request, and
            # Preview with the box empty previews (tries again) the operation — never compiles them in a new
            # conversation. One that does not pass leaves the box, the conversation and the outcome as they were.
            self._clear_request()
            self._refresh_spec_bar()
            self._after_preview(text, wkey)
        return self._work("Use your operation", fn, on_done=done, page=self.request_page)

    def _new_snippet(self) -> bool:
        """Your library… → New snippet…: a name and the paragraph's exact text (one plain paragraph)."""
        from ..library import LibraryError
        name = self.prompts.ask_text("New snippet", "Name (you pick it from Your library later):")
        if not name:
            return False
        text = self.prompts.ask_text("New snippet", "The paragraph's exact text (inserted word for word; no tabs):")
        if not text:
            return False
        try:
            item = self.session.new_snippet(name, text)
        except LibraryError as e:
            self.prompts.warn("Spec Surgeon", f"Nothing was saved to your library:\n{e}")
            return False
        self.write(f"Saved to your library: {item.label()} ({item.path})")
        self.prompts.notify("Your library", f"Saved {item.label()}. Use it from Your library…: pick it and say which "
                                            f"paragraph it goes after.")
        return True

    def _use_snippet(self, name: str) -> bool:
        """One of your snippets: ask which paragraph it goes after, then it is the outcome (like a loaded recipe)."""
        anchor = self.prompts.ask_text(
            f"Insert your snippet “{name}”",
            "It goes after the paragraph whose text matches (a regular expression — plain words match a paragraph "
            "containing them), in every file that has one:")
        if not anchor:
            return False
        try:
            spec = self.session.use_snippet(name, anchor)
        except Exception as e:  # noqa: BLE001 — a LibraryError, or a snippet file that cannot be read
            self.prompts.warn("Spec Surgeon", f"Cannot use this snippet:\n{e}")
            return False
        self._clear_request()
        self.write(f"\nLoaded snippet {name!r}: {spec.get('summary')}\nPress Preview.")
        self._show_loaded_spec()
        self._refresh_chat()
        self._inputs_changed()
        self.go(1)
        return True

    @Slot()
    def do_chain(self) -> bool:
        """Chain recipes…: pick several of your recipes in the order they should run; the chain becomes the outcome
        (previewed and run stage by stage — ``Session.build_chain``)."""
        from ..library import list_items
        if self.busy or self.updating:
            return False
        recipes = list_items("recipe")
        if not recipes:
            self.prompts.notify("Chain recipes", "Your library has no recipes yet. Save a previewed spec as a recipe "
                                "with “Save to library…” on the Preview step; a chain runs two or more of them one "
                                "after another.")
            return False
        labels = [i.name + (f" — {i.description}" if i.description else "") for i in recipes]
        picked = self.prompts.pick_sequence(
            "Chain recipes", "Pick your recipes in the order they should run. Each stage is a full verified run on "
                             "the result of the stage before it (e.g. match the font, then the header/footer).", labels)
        if not picked:
            return False
        names = [recipes[labels.index(x)].name for x in picked if x in labels]
        if not names:
            return False
        s = self.session
        try:
            chain = s.build_chain(names)
        except Exception as e:
            self.prompts.warn("Spec Surgeon", f"Cannot build this chain:\n{e}")
            return False
        self._clear_request()
        self.write(f"\nChain of {len(chain['stages'])} stage(s): " + " → ".join(names) + "\nPress Preview.")
        self._show_loaded_chain()
        self._refresh_chat()                      # a chain ends the conversation: both chat views go
        self._inputs_changed()
        self.go(1)
        return True

    def _show_loaded_chain(self) -> None:
        s = self.session
        stages = s.chain["stages"]
        self.request_page.spec_bar.show_message(
            "info", "Chain", f"{s.chain_label()}: " + " → ".join(f"{i}. {st.get('name') or ''}" for i, st in
                                                                 enumerate(stages, 1))
            + "\nPress Preview to use it: every stage is tried on the sample files, one after another (typing a "
              "request instead compiles a new spec).",
            [("Remove", self._remove_spec)])

    @Slot()
    def do_save_to_library(self) -> bool:
        from ..library import LibraryError, list_items, other_kind_holding, slug
        s = self.session
        if self.busy or self.updating or not self._preview_current():
            return False
        if s.generated is not None:
            kind, default = "operation", s.generated.name.replace("_", " ")
        elif s.chain is not None:
            kind, default = "chain", s.chain_label()
        else:
            kind, default = "recipe", (s.spec or {}).get("request") or ""
        title = {"operation": "Save as my operation", "chain": "Save as chain"}.get(kind, "Save as recipe")
        name = self.prompts.ask_text(title, "Name (you pick it from Your library later):", default[:60])
        if not name:
            return False

        def save(replace: bool):
            if kind == "operation":
                return s.save_operation(name, replace=replace), []
            if kind == "chain":
                return s.save_chain(name, replace=replace)
            return s.save_recipe(name, replace=replace)

        def replace_can_help(e: LibraryError) -> bool:
            """The name is taken by an item of this kind, and only by one: replacing works within one kind, so a name
            the other kind holds is refused even with replace (``library.other_kind_holding``) — never asked about."""
            if "already has" not in str(e):
                return False
            try:
                key = slug(name)
            except LibraryError:
                return False
            return other_kind_holding(kind, key) is None and any(i.slug == key for i in list_items(kind))
        try:
            try:
                item, notes = save(replace=False)
            except LibraryError as e:
                if not replace_can_help(e):
                    raise
                if not self.prompts.ask("Spec Surgeon", f"{e}\n\nReplace it?"):
                    return False                   # the user's answer: nothing to report
                item, notes = save(replace=True)
        except (ValueError, OSError) as e:
            # LibraryError is a ValueError. OSError: a specs folder that cannot be read (moved, a network share that
            # dropped — the preview's token is checked against it) or a library folder that cannot be written. On the
            # GUI thread: shown, never raised.
            self.prompts.warn("Spec Surgeon", f"Nothing was saved to your library:\n{e}")
            return False
        self.write(f"Saved to your library: {item.label()} ({item.path})" + "".join(f"\nNote: {n}" for n in notes))
        self.preview_page.info.show_message("ok", "Saved to your library", item.label() + "".join(f" — {n}" for n in notes))
        return True

    @Slot()
    def _open_library_folder(self) -> None:
        from ..library import library_dir
        d = library_dir()
        d.mkdir(parents=True, exist_ok=True)
        open_folder(d)

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
        if d and not (report and report.exists()) and (d / "CHAIN_REPORT.md").exists():
            report = d / "CHAIN_REPORT.md"        # a chain's run: its own report, each stage's in its folder
        if not report or not report.exists():
            self.prompts.notify("Spec Surgeon", "No report yet — run first.")
            return
        dlg = MarkdownDialog("Chain report" if report.name == "CHAIN_REPORT.md" else "Batch report",
                             report.read_text(encoding="utf-8"), self, base=d)
        self._viewers.append(dlg)
        dlg.show()

    @Slot()
    def do_diagnostics(self) -> bool:
        """DIAGNOSTICS.md of the last run: why each file did not pass, where, how, what to do."""
        d = self.session.last_run_dir
        path = d / "DIAGNOSTICS.md" if d else None
        if not path or not path.exists():
            self.prompts.notify("Spec Surgeon", "No diagnostics yet — run first." if not d else
                                f"The run in {d} left no DIAGNOSTICS.md (it did not finish); its log is "
                                f"{d / 'logs' / 'run.log'}.")
            return False
        dlg = MarkdownDialog("Diagnostics — why files did not pass", path.read_text(encoding="utf-8"), self, base=d)
        self._viewers.append(dlg)
        dlg.show()
        return True

    @Slot()
    def open_logs(self) -> None:
        d = logs_dir()
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        open_folder(d)

    def _bundle_folders(self) -> Tuple[Optional[Path], List[Path]]:
        """(the folder a bundle mirrors under results/, the newer folders beside it under results-later/). The last run
        with its session comes first, always (its reports, logs, traces and records are what a bundle is for); else
        the last preview's folder; else the conversation's. Beside it, every folder the session has moved on to since:
        the last preview's folder and, while a conversation is open, the one holding its records — a reply after the
        run, or a new request (``Session.compile`` opens a new session folder), keeps its records there, a reply or
        first turn that failed included. ``diag.make_bundle`` never bundles a folder twice."""
        s = self.session

        def folder(p) -> Optional[Path]:
            return Path(p) if p and Path(p).is_dir() else None
        found: List[Path] = []
        chat = folder(s.chat_dir) if s.conversation is not None else None
        for p in (folder(s.last_run_dir), folder(s.out_dir), chat):
            if p is not None and p not in found:
                found.append(p)
        return (found[0], found[1:]) if found else (None, [])

    @Slot()
    def save_bundle(self) -> Optional[Path]:
        """A zip for whoever diagnoses a problem: the last run's (or preview's) reports, logs, traces, records and
        plans with its session's conversation and preview, the newer session folder the conversation has moved on to
        since (``_bundle_folders``), and the app's own logs — never the documents."""
        s = self.session
        run_dir, later = self._bundle_folders()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        start = str((run_dir or Path.home()) / f"spec-surgeon-diagnostics-{stamp}.zip")
        path = self.prompts.save_file("Save a diagnostics bundle", start, "Zip (*.zip)")
        if not path:
            return None
        try:
            res = diag.make_bundle(Path(path), run_dir, logs_dir(), note=f"window; last run {s.last_run_dir}",
                                   also=later)
        except OSError as e:
            self.prompts.warn("Spec Surgeon", f"The bundle could not be written:\n{e}")
            return None
        self.write(f"Diagnostics bundle: {res['path']} — {len(res['included'])} file(s); the documents themselves "
                   f"are never included ({res['left_out']} left out).")
        where = str(run_dir or "no run yet") + "".join(f" and {p}" for p in res.get("also") or [])
        self.prompts.notify("Spec Surgeon", f"Saved {res['path']}.\n\nIt holds the reports, logs and records of "
                                            f"{where} and the app's own logs — not the documents. "
                                            f"Records and logs quote the paragraphs that changed or failed, so share "
                                            f"it as you would the specs.")
        return Path(res["path"])

    @Slot()
    def do_advise(self) -> bool:
        s = self.session
        if not self.claude_ok:
            self.run_page.info.show_message("info", "Advice needs an API key", NEEDS_KEY_ADVICE,
                                            [("Set API key…", self.open_settings)])
            return False

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
        if row is None:
            return
        if row.diagnosis:
            self.show_diagnosis(row)
        elif row.open_path and Path(row.open_path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(row.open_path)))

    def show_diagnosis(self, row) -> QWidget:
        """Why a file did not pass — what happened, where it stopped, why, how, what to do — in the app."""
        base = self.session.last_run_dir if row.status_col == 1 else None
        dlg = MarkdownDialog(f"Why {row.cells[0]} did not pass", row.diagnosis, self, base=base)
        self._viewers.append(dlg)
        dlg.show()
        return dlg

    # ---- settings, help ---------------------------------------------------------------------------------
    @Slot()
    def open_settings(self) -> None:
        dlg = SettingsDialog(self)
        self._viewers.append(dlg)
        dlg.open()

    def apply_theme(self) -> None:
        """Restyle for the current light/dark scheme (Windows switched, or at start)."""
        app = QApplication.instance()
        if app is not None:
            theme.apply(app)
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

    def about_text(self) -> str:
        return about_text()

    @Slot()
    def show_about(self) -> None:
        dlg = AboutDialog(self)
        dlg.setWindowIcon(app_icon())
        self._viewers.append(dlg)
        dlg.open()

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
                    "install_update": w.updates.banner.btn_install, "settings": w.settings_btn,
                    "send": r.chat.send_btn, "start_over": r.chat.start_over_btn,
                    "review_ack": w.preview_page.review_ack_btn, "library": r.library_btn, "chain": r.chain_btn,
                    "save_to_library": w.preview_page.save_btn, "about": w.about_btn, "next": f.next_btn},
        "vars": {"folder": Var(f.folder.text, f.folder.setText), "template": Var(f.template.text, f.template.setText),
                 "out": Var(f.out.text, f.out.setText),
                 "render": Var(r.render_cb.isChecked, r.render_cb.setChecked),
                 "in_place": Var(run.inplace_rb.isChecked,
                                 lambda on: (run.inplace_rb if on else run.copies_rb).setChecked(True))},
        "request": text_var(r.request),
        "chat": r.chat, "preview_chat": w.preview_page.chat, "reply": text_var(r.chat.reply),
        "choices": lambda: [(b.property("choice_id"), b) for b in r.chat.choice_buttons()],
        "log": Var(w.log.toPlainText, lambda t: w.log.setPlainText(t)),
        "status": Var(w.status_lbl.text, w.set_status),
        "busy": lambda: w.busy, "banner": w.updates.banner, "claude": lambda: w.claude_ok,
        "preview_model": w.preview_page.model, "results_model": w.run_page.model,
    }
    return w


def _install_excepthook(window_ref: List[Optional[MainWindow]]) -> None:
    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        _append_error_log("UNEXPECTED ERROR in the window\n" + text)
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


def _crash_notice(w: MainWindow, previous: List[dict]) -> None:
    """Say once that the previous session ended without closing, and where its crash report is."""
    last = previous[-1]
    where = last.get("crash_log")
    text = (f"The session started {last.get('started') or '?'} (version {last.get('version') or '?'}) did not close "
            "normally — the program crashed, was ended from Task Manager, or Windows shut down. "
            + (f"Python's fault handler wrote what it saw to {where}." if where else
               "Nothing was written to its crash log; the app log may say what it was doing."))
    w.write("Previous session ended unexpectedly: " + text)
    w.files_page.info.show_message("warn", "Spec Surgeon closed unexpectedly last time", text,
                                   [("Open logs folder", w.open_logs)])


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
    ref: List[Optional[MainWindow]] = [None]
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
        # Windows logoff, or an installer run by hand closing apps (Restart Manager): never cut a Run short.
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
