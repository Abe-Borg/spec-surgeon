"""The desktop: documents, an optional template, and one conversation."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import about, credentials, diagnostics as diag
from ..document_task import DocumentTask
from ..gui import Session, open_folder
from ..settings import config_dir, load_settings, logs_dir, save_settings
from . import theme
from .update_ui import UpdateController
from .widgets import Card, ImageViewer, Thumb, label, primary_button
from .worker import Bridge, start_job

PROGRESS = {"Preparing and checking the preview…", "Creating and checking your edited copies…",
            "Checking the files that still need work…"}


def plain(text, name=""):
    widget = label(str(text), name, selectable=True)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


def clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.deleteLater()
        elif item.layout() is not None:
            clear(item.layout())


class DocumentDrop(QWidget):
    dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    @staticmethod
    def paths(event):
        return [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]

    def dragEnterEvent(self, event):
        if self.paths(event):
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.dropped.emit(self.paths(event))
        event.acceptProposedAction()


class ConversationWindow(QMainWindow):
    busyChanged = Signal(bool)

    def __init__(self, session, settings, *, fresh=True, prompts=None, **update_kw):
        super().__init__()
        from .app import Prompts, app_icon
        self.session, self.settings = session, settings
        self.prompts = prompts or Prompts(self)
        self.busy = self.updating = self.force_close = False
        self._viewers = []
        self._thread = None
        self._log_lines = []
        self._action_token = ""
        self._submitted_text = ""
        self._cancel = threading.Event()
        self._job_started = 0.0
        self._status_text = ""
        self._choice_buttons = []
        self._ticker = QTimer(self)
        self._ticker.setInterval(1000)
        self._ticker.timeout.connect(self._tick)
        self.key_origin = "environment" if os.environ.get(credentials.ENV) else "none"
        if self.key_origin == "none":
            saved = credentials.load()
            if saved:
                os.environ[credentials.ENV] = saved
                self.key_origin = "saved"
        if fresh and settings.out_root:
            session.out_root = Path(settings.out_root)
        self.task = DocumentTask(session, journal=config_dir() / "conversation.json" if fresh else None)
        restored_error = ""
        if fresh:
            try:
                self.task.restore()
            except (OSError, ValueError, TypeError, KeyError) as exc:
                restored_error = f"The saved conversation could not be opened: {exc}"
        self.updates = UpdateController(self, **update_kw)
        self.busyChanged.connect(self.updates.on_busy)
        self.bridge = Bridge(self)
        self.bridge.log.connect(self._progress)
        self.bridge.done.connect(self._done)
        self.bridge.failed.connect(self._failed)
        self.setWindowTitle("Spec Surgeon")
        self.setWindowIcon(app_icon())
        self.resize(1120, 820)
        self.setMinimumSize(780, 600)
        self._build()
        self._menus()
        self._render()
        if fresh:
            self.composer.setPlainText(settings.request_draft)
        if settings.window_geometry:
            self.restoreGeometry(QByteArray.fromHex(settings.window_geometry.encode("ascii")))
        if restored_error:
            self._notice(restored_error)

    # ---- layout ----
    def _build(self):
        root = DocumentDrop(self)
        root.setObjectName("Content")
        root.dropped.connect(self.add_documents)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(24, 18, 24, 18)
        outer.setSpacing(16)
        header = QHBoxLayout()
        header.addWidget(plain("Spec Surgeon", "PageTitle"))
        header.addStretch(1)
        self.new_btn = QPushButton("New conversation")
        self.new_btn.clicked.connect(self.new_conversation)
        header.addWidget(self.new_btn)
        self.settings_btn = QPushButton("Settings")
        self.settings_btn.clicked.connect(self.open_settings)
        header.addWidget(self.settings_btn)
        outer.addLayout(header)
        outer.addWidget(self.updates.banner)
        body = QHBoxLayout()
        body.setSpacing(24)
        self.attachments = QWidget()
        self.attachments.setFixedWidth(250)
        left = QVBoxLayout(self.attachments)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(12)
        self.attachment_heading = plain("Your documents", "CardTitle")
        left.addWidget(self.attachment_heading)
        self.add_btn = QPushButton("Add Word documents")
        self.add_btn.clicked.connect(self.pick_documents)
        left.addWidget(self.add_btn)
        left.addWidget(plain("Drop one or more .docx files anywhere in this window.", "Muted"))
        self.attachment_scroll = QScrollArea()
        self.attachment_scroll.setWidgetResizable(True)
        self.attachment_scroll.setFrameShape(QFrame.Shape.NoFrame)
        attachment_body = QWidget()
        self.file_rows = QVBoxLayout(attachment_body)
        self.file_rows.setContentsMargins(0, 0, 0, 0)
        self.attachment_scroll.setWidget(attachment_body)
        left.addWidget(self.attachment_scroll, 1)
        self.template_rows = QVBoxLayout()
        left.addLayout(self.template_rows)
        self.template_btn = QPushButton("Add a template (optional)")
        self.template_btn.clicked.connect(self.pick_template)
        left.addWidget(self.template_btn)
        left.addStretch(1)
        left.addWidget(plain("Edited copies are saved separately. Your originals stay intact.", "Muted"))
        body.addWidget(self.attachments)
        right = QVBoxLayout()
        right.setSpacing(12)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        self.conversation = QVBoxLayout(content)
        self.conversation.setContentsMargins(0, 0, 10, 0)
        self.conversation.setSpacing(14)
        self.scroll.setWidget(content)
        right.addWidget(self.scroll, 1)
        self.notice = plain("", "Hint")
        self.notice.hide()
        right.addWidget(self.notice)
        self.status = plain("", "Muted")
        right.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setMaximumHeight(4)
        self.progress.setTextVisible(False)
        self.progress.hide()
        right.addWidget(self.progress)
        self.primary = primary_button("Create edited copies")
        self.primary.clicked.connect(self.do_primary)
        right.addWidget(self.primary, 0, Qt.AlignmentFlag.AlignRight)
        self.composer = QPlainTextEdit()
        self.composer.setObjectName("Editor")
        self.composer.setAccessibleName("Describe a change or answer a question")
        self.composer.setPlaceholderText("What would you like to do with these documents?")
        self.composer.setMinimumHeight(88)
        self.composer.setMaximumHeight(120)
        self.composer.textChanged.connect(self._refresh_controls)
        right.addWidget(self.composer)
        footer = QHBoxLayout()
        self.connection = plain("", "Muted")
        footer.addWidget(self.connection, 1)
        self.send = primary_button("Send")
        self.send.setToolTip("Send · Ctrl+Enter")
        self.send.clicked.connect(self.do_send)
        footer.addWidget(self.send)
        right.addLayout(footer)
        body.addLayout(right, 1)
        outer.addLayout(body, 1)
        self.setCentralWidget(root)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.do_send)
        QShortcut(QKeySequence("Ctrl+Enter"), self, activated=self.do_send)

    def _menus(self):
        help_menu = self.menuBar().addMenu("Help")
        for title, callback in (("Check for updates", self.updates.manual_check),
                                ("Open diagnostic logs", self.open_logs),
                                ("About Spec Surgeon", self.show_about)):
            action = QAction(title, self)
            action.triggered.connect(callback)
            help_menu.addAction(action)

    def _attachment(self, entry):
        """One attached file: its name, what triage found, and a way to remove it."""
        path = Path(entry["path"])
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 4, 0, 4)
        column.setSpacing(2)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        text = plain(("Template\n" if entry["template"] else "") + path.name, "Muted" if entry["state"] == "refused" else "")
        text.setToolTip(str(path))
        row.addWidget(text, 1)
        remove = QToolButton()
        remove.setText("×")
        remove.setAccessibleName(f"Remove {path.name}")
        remove.clicked.connect(lambda: self.remove_document(path, template=entry["template"]))
        row.addWidget(remove)
        column.addLayout(row)
        if entry["state"] == "refused":
            why = plain(entry["reason"], "Hint")
            why.setAccessibleName(f"Why {path.name} was left out")
            column.addWidget(why)
        if entry["notes"]:
            notes = plain(" ".join(entry["notes"]), "Muted")
            notes.setAccessibleName(f"Notes on {path.name}")
            column.addWidget(notes)
        return box

    # ---- rendering ----
    def _card(self, title, helper=""):
        card = Card(title, helper)
        self.conversation.addWidget(card)
        self._focus_widget = card
        return card

    def _changes_card(self, group, several):
        """What will change for one layout: one sentence per change, the assumptions beneath, every setting on
        request."""
        if several:
            count = group["count"]
            title = f"{group['label']} · {count} document{'s' if count != 1 else ''}"
        else:
            title = "Here’s what will change"
        card = self._card(title)
        for change in group["changes"]:
            card.add(plain("• " + change["summary"]))
        for assumption in group["assumptions"]:
            card.add(plain("Assuming: " + assumption, "Muted"))
        if several and group["files"]:
            card.add(plain(", ".join(group["files"]), "Muted"))
        details = QTreeWidget()
        details.setHeaderHidden(True)
        details.setWordWrap(True)
        details.setMinimumHeight(140)
        details.setMaximumHeight(320)
        details.setAccessibleName("Every setting of these changes")
        for change in group["changes"]:
            node = QTreeWidgetItem([change["title"]])
            for line in change["details"]:
                node.addChild(QTreeWidgetItem([line]))
            details.addTopLevelItem(node)
        toggle = QToolButton()
        toggle.setText("See every setting")
        toggle.setCheckable(True)
        toggle.toggled.connect(details.setVisible)
        card.add(toggle)
        card.add(details)
        details.hide()
        return card

    def _render(self):
        view = self.task.view()
        self._action_token = view["token"]
        accepted = [a for a in view["attachments"] if not a["template"] and a["state"] != "refused"]
        self.attachment_heading.setText(f"Your documents · {len(accepted)}" if accepted else "Your documents")
        clear(self.file_rows)
        documents = [a for a in view["attachments"] if not a["template"]]
        for entry in documents:
            self.file_rows.addWidget(self._attachment(entry))
        self.file_rows.addStretch(1)
        self.attachment_scroll.setVisible(bool(documents))
        self.attachment_scroll.setMaximumHeight(min(360, max(56, sum(58 + (40 if a["notes"] else 0) + (20 if a["reason"] else 0)
                                                                     for a in documents))))
        clear(self.template_rows)
        template = next((a for a in view["attachments"] if a["template"]), None)
        if template:
            self.template_rows.addWidget(self._attachment(template))
        self.template_btn.setVisible(template is None)
        clear(self.conversation)
        self._choice_buttons = []
        self._focus_widget = None
        if not view["messages"]:
            welcome = self._card("Less document work. More done.")
            welcome.add(plain("Add your Word files and tell me what you want to change. "
                              "I’ll check the documents, work out a plan, and show you what will happen."))
            welcome.add(plain("For example: “Match the headers and fonts to this template. Keep our wording.”", "Muted"))
        for entry in view["messages"]:
            card = self._card("You" if entry["role"] == "user" else "Spec Surgeon")
            card.add(plain(entry["text"]))
        several = len(view["groups"]) > 1
        if several:
            self._card("Your documents use " + str(len(view["groups"])) + " layouts",
                       "Each layout gets its own plan; one decision covers them all.")
        for group in view["groups"]:
            self._changes_card(group, several)
        if view["targets"] and view["groups"]:
            card = self._card("Where exactly", "The passages each document contributes.")
            tree = QTreeWidget()
            tree.setHeaderHidden(True)
            tree.setWordWrap(True)
            tree.setMaximumHeight(240)
            tree.setAccessibleName("Document passages")
            for target in view["targets"]:
                node = QTreeWidgetItem([target["title"]])
                node.setToolTip(0, target["source"])
                for line in target["details"]:
                    node.addChild(QTreeWidgetItem([line]))
                tree.addTopLevelItem(node)
            card.add(tree)
        if view["set_aside"]:
            card = self._card("Left untouched")
            for item in view["set_aside"]:
                card.add(plain(f"{item['name']}: {item['reason']}"))
        if view["questions"] or view["choices"]:
            card = self._card("One thing to clarify")
            for question in view["questions"]:
                card.add(plain(question))
            for choice in view["choices"]:
                button = QPushButton(choice["label"])
                button.setProperty("choice_id", choice["id"])
                button.setAccessibleName(choice["label"])
                if choice["words"]:
                    button.setToolTip("Exact wording: " + " | ".join(choice["words"]))
                button.clicked.connect(self._choice_clicked)
                card.add(button)
                self._choice_buttons.append(button)
                for word in choice["words"]:
                    card.add(plain("Exact wording: " + word, "Muted"))
            card.add(plain("Pick an answer, or describe what you want in your own words below.", "Muted"))
        if view["budget_paused"]:
            card = self._card("Work paused; your progress is saved")
            card.add(plain("This conversation reached its investigation allowance, or stopped before usage could be "
                           "confirmed. Continuing allows up to another estimated $10 of model work. "
                           "Provider charges can differ from this estimate."))
        for sample in view["samples"]:
            if sample.images or sample.pages_before is not None or sample.pages_after is not None:
                card = Card("Preview · " + Path(sample.source).name)
                if sample.pages_before is not None and sample.pages_after is not None:
                    change = sample.pages_after - sample.pages_before
                    suffix = f" ({change:+d})" if change else ""
                    card.add(plain(f"Pages: {sample.pages_before} → {sample.pages_after}{suffix}", "Muted"))
                for path in sample.images:
                    if Path(path).is_file():
                        thumb = Thumb(Path(path))
                        thumb.clicked.connect(self.open_image)
                        card.add(thumb)
                self.conversation.addWidget(card)
        if view["warnings"]:
            card = Card("Please check" if view["blocking"] else "About these checks")
            for warning in view["warnings"]:
                card.add(plain(warning))
            self.conversation.addWidget(card)
        if view["results"]:
            card = Card("Your edited copies", "Double-click a document to open it in Word.")
            results = QTreeWidget()
            results.setHeaderHidden(True)
            results.setRootIsDecorated(False)
            results.setAccessibleName("Edited documents")
            results.setMaximumHeight(220)
            for source, output in view["results"].items():
                item = QTreeWidgetItem([Path(output).name])
                item.setToolTip(0, str(output))
                item.setData(0, Qt.ItemDataRole.UserRole, output)
                results.addTopLevelItem(item)
            results.itemDoubleClicked.connect(self._open_result_item)
            card.add(results)
            self.conversation.addWidget(card)
            if view["stage"] == "done":
                self._focus_widget = card
        self.conversation.addStretch(1)
        self.primary.setText(view["action"])
        self.primary.setVisible(bool(view["action"]))
        self._refresh_controls()
        QTimer.singleShot(0, self._scroll_end)

    @Slot()
    def _scroll_end(self):
        self.scroll.verticalScrollBar().setValue(max(0, self._focus_widget.y() - 12) if self._focus_widget else 0)

    @Slot()
    def _refresh_controls(self):
        active = not self.busy and not self.updating
        ready = self.session.claude_ready()
        has_documents = bool(self.session.documents)
        self.attachments.setEnabled(active)
        self.new_btn.setEnabled(active)
        self.settings_btn.setEnabled(active)
        self.composer.setEnabled(active)
        typed = bool(self.composer.toPlainText().strip())
        self.send.setEnabled(active and ready and has_documents and typed)
        self.primary.setEnabled(active and ready and has_documents and not typed)
        for button in self._choice_buttons:
            button.setEnabled(active and ready and has_documents)
        if self.busy:
            self.primary.show()
            self.primary.setText("Stopping after this step…" if self._cancel.is_set() else "Stop after current step")
            self.primary.setEnabled(not self._cancel.is_set())
        self.connection.setText("Ctrl+Enter to send" if ready else "Connect your Anthropic API key in Settings to start.")
        if typed and self.primary.isVisible():
            self.primary.setToolTip("Send your new message before approving a plan.")
        else:
            self.primary.setToolTip("")

    # ---- attachments ----
    @Slot(list)
    def add_documents(self, paths):
        if self.busy or self.updating:
            return
        try:
            self.task.attach(paths)
            self.notice.hide()
            self._render()
        except (OSError, ValueError) as exc:
            self._notice(str(exc))

    @Slot()
    def pick_documents(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add Word documents", "", "Word documents (*.docx)")
        self.add_documents(files)

    @Slot()
    def pick_template(self):
        path, _ = QFileDialog.getOpenFileName(self, "Add a reference template", "", "Word documents (*.docx)")
        if path:
            try:
                self.task.attach([path], template=True)
                self._render()
            except (OSError, ValueError) as exc:
                self._notice(str(exc))

    def remove_document(self, path, *, template=False):
        if not self.busy and not self.updating:
            self.task.remove(path, template=template)
            self._render()

    # ---- actions ----
    @Slot()
    def do_send(self):
        if not self.send.isEnabled():
            return
        text = self.composer.toPlainText().strip()
        self.composer.clear()
        self._work("send", text=text)

    @Slot()
    def _choice_clicked(self):
        self.do_choose(self.sender().property("choice_id"))

    def do_choose(self, choice_id):
        if self.busy or self.updating or not choice_id:
            return
        self._work("choose", choice_id=str(choice_id))

    @Slot()
    def do_primary(self):
        if self.busy:
            self._cancel.set()
            self._refresh_controls()
            return
        if self.primary.isEnabled():
            self._work("primary", token=self._action_token)

    def _work(self, action, **values):
        if self.busy or self.updating:
            return
        self.notice.hide()
        self._submitted_text = values.get("text", "")
        self._cancel = threading.Event()
        self._job_started = time.monotonic()
        self.busy = True
        self._ticker.start()
        self.progress.show()
        self.set_status("Checking your documents…")
        self._refresh_controls()
        self.busyChanged.emit(True)
        self._thread = start_job(self.bridge, lambda log: self.task.perform(action, log=log, **values),
                                 cancel_event=self._cancel)

    @Slot(str)
    def _progress(self, text):
        self.write(text)
        if text in PROGRESS:
            self.set_status(text)

    @Slot(object)
    def _done(self, result):
        self._finish()
        self._render()
        self.save_settings()

    @Slot(str, str)
    def _failed(self, message, traceback):
        self.write(traceback)
        self._finish()
        self._render()
        if self._submitted_text and not any(m["role"] == "user" and m["text"] == self._submitted_text
                                            for m in self.task.messages):
            self.composer.setPlainText(self._submitted_text)
        kind, _, detail = message.partition(": ")
        if kind == "UserCancelled":
            self._notice("Stopped before the next AI step. Your progress is saved.")
        elif kind in {"ValueError", "OperationFailed", "TrialsFailed", "TransformFailed", "SpecError",
                      "FileNotFoundError", "PermissionError", "LibraryError"}:
            self._notice((detail or message).replace("press Preview again", "continue to check the documents again"))
        else:
            self._notice("The work could not finish. You can continue, or open diagnostic logs from Help for details.")

    def _finish(self):
        # The worker has emitted its final signal. Join before allowing Qt teardown.
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        self.busy = False
        self._ticker.stop()
        self.progress.hide()
        self.set_status("")
        self.busyChanged.emit(False)

    def _notice(self, text):
        self.notice.setText(text)
        self.notice.show()

    def write(self, text):
        self._log_lines.append(str(text))
        diag.app_log().info(str(text))

    def set_status(self, text):
        self._status_text = text
        self._tick()

    @Slot()
    def _tick(self):
        text = self._status_text
        if self.busy:
            text += f" · {int(time.monotonic() - self._job_started)}s"
            cost = self.task._budget.estimated_cost_usd
            if cost is not None and cost > 0:
                text += f" · estimated ${cost:.2f} so far"
        self.status.setText(text)

    def set_checking(self, on):
        pass  # background update checks do not interrupt document work

    def set_updating(self, on):
        self.updating = on
        self._refresh_controls()

    def release_notes_text(self):
        from .app import _release_notes_path
        from .. import __version__
        path = _release_notes_path(__version__)
        return path.read_text(encoding="utf-8") if path else ""

    def open_logs(self):
        logs_dir().mkdir(parents=True, exist_ok=True)
        open_folder(logs_dir())

    def show_about(self):
        from .about_dialog import AboutDialog
        dialog = AboutDialog(self)
        self._viewers.append(dialog)
        dialog.show()

    @Slot(QTreeWidgetItem, int)
    def _open_result_item(self, item, column):
        self.open_result(item.data(0, Qt.ItemDataRole.UserRole))

    @Slot(str)
    def open_image(self, path):
        viewer = ImageViewer(Path(path), self)
        self._viewers.append(viewer)
        viewer.show()

    def open_result(self, path):
        if Path(path).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))
        else:
            self._notice("That copy was moved or deleted. Continue the conversation to create a new result.")

    @Slot()
    def new_conversation(self):
        if self.busy or self.updating:
            return
        if self.task.journal and self.task.journal.exists():
            archive = self.task.journal.parent / "conversations"
            archive.mkdir(parents=True, exist_ok=True)
            self.task.journal.replace(archive / f"{time.time_ns()}.json")
        self.session = Session(out_root=self.session.out_root, client=self.session.client)
        self.task = DocumentTask(self.session, journal=self.task.journal)
        self.composer.clear()
        self.task.save()
        self._render()

    @Slot()
    def open_settings(self):
        dialog = ConnectionDialog(self)
        self._viewers.append(dialog)
        dialog.show()

    def save_settings(self):
        self.settings.out_root = str(self.session.out_root)
        self.settings.request_draft = self.composer.toPlainText()
        self.settings.window_geometry = bytes(self.saveGeometry().toHex()).decode("ascii")
        try:
            save_settings(self.settings)
            if not self.busy:
                self.task.save()
        except OSError as exc:
            self._notice(f"Could not save your conversation: {exc}")

    def apply_theme(self):
        theme.apply(QApplication.instance())

    def quit_for_update(self):
        self.force_close = True
        self.close()
        QTimer.singleShot(0, QApplication.instance().quit)

    def closeEvent(self, event):
        if self.busy or self.updates.downloading:
            self._notice("Please keep this window open while the current work finishes.")
            event.ignore()
            return
        self.save_settings()
        for viewer in self._viewers:
            viewer.close()
        event.accept()


class ConnectionDialog(QDialog):
    """Connection setup, separate from the document task. No model tuning controls."""
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Settings")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        card = Card("AI connection", "Enter your Anthropic API key to use the conversation.")
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("A key is connected; enter a new one to replace it" if window.session.claude_ready() else "sk-ant-…")
        card.add(self.key)
        self.remember = QCheckBox("Remember securely on this computer")
        self.remember.setEnabled(credentials.available())
        self.remember.setChecked(credentials.available())
        card.add(self.remember)
        card.add(plain("Each conversation pauses before exceeding its estimated $10 allowance. "
                       "Continuing beyond that is your choice. Provider charges can differ.", "Muted"))
        layout.addWidget(card)
        self.error = plain("")
        layout.addWidget(self.error)
        save = primary_button("Save")
        save.clicked.connect(self.apply)
        layout.addWidget(save, 0, Qt.AlignmentFlag.AlignRight)

    @Slot()
    def apply(self):
        key = self.key.text().strip()
        if key:
            os.environ[credentials.ENV] = key
            self.window.key_origin = "session"
            if self.remember.isChecked() and credentials.available():
                try:
                    credentials.save(key)
                    self.window.key_origin = "saved"
                except Exception:
                    self.error.setText("The key is set for this session but could not be saved securely.")
                    self.window._refresh_controls()
                    return
        self.window._refresh_controls()
        self.accept()


def build_window(session=None, *, settings=None, prompts=None, **update_kw):
    from .app import qt_app
    st = settings if settings is not None else load_settings()
    app = qt_app(st)
    window = ConversationWindow(session or Session(), st, fresh=session is None, prompts=prompts, **update_kw)

    def wait(timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if not window.busy and not window.updates.checking and not window.updates.downloading:
                app.processEvents()
                return "\n".join(window._log_lines)
            time.sleep(.02)
        raise TimeoutError("The conversation window did not become idle")

    window._spec_surgeon = {"window": window, "session": window.session, "app": app, "wait": wait}
    return window
