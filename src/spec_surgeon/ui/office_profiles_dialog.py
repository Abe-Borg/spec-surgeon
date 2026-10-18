"""Office standards, exact paragraph review, and verified copies in the desktop."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from .worker import Bridge, start_job


ROLE_LABELS = {
    "section_id": "Section number", "section_title": "Section title", "part": "Part heading",
    "article": "Article heading", "paragraph": "Clause", "subparagraph": "Subclause",
    "subparagraph_2": "Nested item (level 3)", "subparagraph_3": "Nested item (level 4)",
    "subparagraph_4": "Nested item (level 5)", "end_of_section": "End of section",
}


class ProfileCreateDialog(QDialog):
    """Explicit role mappings; the chosen template stays a pinned profile asset."""
    def __init__(self, prompts, parent=None):
        super().__init__(parent)
        self.prompts = prompts
        self.setWindowTitle("Create office profile")
        self.resize(690, 620)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(self)
        form.addRow("Profile name", self.name)
        row = QHBoxLayout()
        self.template = QLineEdit(self)
        pick = QPushButton("Choose example…", self)
        pick.clicked.connect(self._pick_template)
        row.addWidget(self.template, 1)
        row.addWidget(pick)
        form.addRow("Reference document", row)
        self.direct = QComboBox(self)
        for label, key in (("Keep existing direct formatting", "keep"),
                           ("Replace formatting supplied by the destination style", "replace_style_owned"),
                           ("Clear eligible direct formatting", "clear")):
            self.direct.addItem(label, key)
        form.addRow("Direct formatting", self.direct)
        self.tracked = QCheckBox("Track paragraph style changes", self)
        form.addRow(self.tracked)
        self.match_margins = QCheckBox("Set these margins in every section", self)
        form.addRow(self.match_margins)
        margins = QHBoxLayout()
        self.margins = {}
        for side in ("top", "bottom", "left", "right"):
            margins.addWidget(QLabel(side.capitalize(), self))
            value = QDoubleSpinBox(self)
            value.setRange(0, 22)
            value.setDecimals(3)
            value.setSingleStep(0.125)
            value.setSuffix(" in")
            value.setValue(1)
            value.setEnabled(False)
            self.match_margins.toggled.connect(value.setEnabled)
            margins.addWidget(value)
            self.margins[side] = value
        form.addRow(margins)
        layout.addLayout(form)
        hint = QLabel("Choose the destination style for each role you want to change. Blank rows stay unchanged. "
                      "You will review the actual document paragraphs before any run.", self)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.roles = QTableWidget(len(ROLE_LABELS), 2, self)
        self.roles.setHorizontalHeaderLabels(["Specification role", "Destination style"])
        self.roles.horizontalHeader().setStretchLastSection(True)
        for index, (role, label) in enumerate(ROLE_LABELS.items()):
            item = QTableWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, role)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.roles.setItem(index, 0, item)
            combo = QComboBox(self.roles)
            combo.setEditable(True)
            combo.addItem("")
            self.roles.setCellWidget(index, 1, combo)
        layout.addWidget(self.roles, 1)
        self.error = QLabel(self)
        self.error.setWordWrap(True)
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Save profile…")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @Slot()
    def _pick_template(self):
        selected = self.prompts.pick_file("Reference specification", "", "Word documents (*.docx *.dotx)")
        if not selected:
            return
        self.template.setText(selected)
        pkg = None
        try:
            from ..package import DocxPackage, W
            from ..ops.selectors import StyleSheet
            pkg = DocxPackage.from_path(selected)
            styles = StyleSheet(pkg).styles
            choices = sorted(sid for sid, style in styles.items()
                             if style.get(W + "type", "paragraph") == "paragraph"
                             and style.get(W + "default", "0") not in {"1", "true", "on"})
            for row in range(self.roles.rowCount()):
                combo = self.roles.cellWidget(row, 1)
                previous = combo.currentText()
                combo.clear()
                combo.addItems(["", *choices])
                combo.setCurrentText(previous)
            self.error.clear()
        except Exception as exc:
            self.error.setText(str(exc))
        finally:
            if pkg is not None:
                pkg.close()

    def values(self):
        return {"name": self.name.text().strip(), "template": Path(self.template.text().strip()),
                "role_map": {self.roles.item(row, 0).data(Qt.ItemDataRole.UserRole):
                             self.roles.cellWidget(row, 1).currentText().strip()
                             for row in range(self.roles.rowCount())
                             if self.roles.cellWidget(row, 1).currentText().strip()},
                "direct_formatting": self.direct.currentData(), "tracked": self.tracked.isChecked(),
                "policies": [{"type": "page_setup", "sections": "all", "margins": {
                    side: round(control.value() * 1440) for side, control in self.margins.items()}}]
                if self.match_margins.isChecked() else []}

    @Slot()
    def _accept(self):
        values = self.values()
        if not values["name"] or not values["template"].is_file() or not (values["role_map"] or values["policies"]):
            self.error.setText("Enter a name, choose an existing reference document, and choose styles or margins.")
            return
        self.accept()


class OfficeProfilesDialog(QDialog):
    def __init__(self, session, prompts, parent=None):
        super().__init__(parent)
        self.session, self.prompts = session, prompts
        self.busy = False
        self.profile = None
        self.review = None
        self.previews = {}
        self._on_done = None
        self._choices = {}
        self._rows = []
        self.setWindowTitle("Office profiles")
        self.resize(1120, 800)
        self.bridge = Bridge(self)
        self.bridge.log.connect(self._log)
        self.bridge.done.connect(self._done)
        self.bridge.failed.connect(self._failed)
        layout = QVBoxLayout(self)
        intro = QLabel("Apply a reusable office standard to the selected specification files. "
                       "Review the exact targets, preview the result, then deliver verified copies.", self)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.content = QWidget(self)
        body = QVBoxLayout(self.content)
        body.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.content, 1)
        toolbar = QHBoxLayout()
        for title, callback in (("Open profile…", self.open_profile), ("Create profile…", self.create_profile),
                                ("Import…", self.import_profile), ("Export…", self.export_profile)):
            button = QPushButton(title, self)
            button.clicked.connect(callback)
            toolbar.addWidget(button)
        self.profile_label = QLabel("No profile selected", self)
        self.profile_label.setTextFormat(Qt.TextFormat.PlainText)
        toolbar.addWidget(self.profile_label, 1)
        body.addLayout(toolbar)
        destination = QHBoxLayout()
        destination.addWidget(QLabel("Results folder", self))
        self.output = QLineEdit(str(Path(session.out_root) / ("office-" + datetime.now().strftime("%Y%m%d-%H%M%S"))), self)
        destination.addWidget(self.output, 1)
        self.render = QCheckBox("Render preview pages", self)
        self.render.setChecked(True)
        destination.addWidget(self.render)
        body.addLayout(destination)
        splitter = QSplitter(Qt.Orientation.Vertical, self)
        body.addWidget(splitter, 1)
        self.table = QTableWidget(0, 4, splitter)
        self.table.setHorizontalHeaderLabels(["Document / location", "Paragraph", "Observed role", "Your choice"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.details = QPlainTextEdit(splitter)
        self.details.setReadOnly(True)
        self.details.setPlainText("Choose a profile, then analyze the files selected in the main window.")
        self.review_approval = QCheckBox("I approve the complete current outcome review and its exact targets", self)
        self.preview_approval = QCheckBox("I have inspected the current preview and any reported visual concerns", self)
        body.addWidget(self.review_approval)
        body.addWidget(self.preview_approval)
        actions = QHBoxLayout()
        self.analyze_button = QPushButton("Analyze and review", self)
        self.preview_button = QPushButton("Preview", self)
        self.run_button = QPushButton("Run verified copies", self)
        self.open_button = QPushButton("Open results / preview pages", self)
        for button in (self.analyze_button, self.preview_button, self.run_button, self.open_button):
            actions.addWidget(button)
        body.addLayout(actions)
        self.analyze_button.clicked.connect(self.analyze)
        self.preview_button.clicked.connect(self.preview)
        self.run_button.clicked.connect(self.run)
        self.open_button.clicked.connect(self.open_results)
        self.review_approval.toggled.connect(self._refresh)
        self.preview_approval.toggled.connect(self._refresh)
        self.render.toggled.connect(self._invalidate_preview)
        self.output.textChanged.connect(self._invalidate_preview)
        self.status = QLabel(self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self._refresh()

    def _refresh(self, *_):
        self.analyze_button.setEnabled(self.profile is not None and not self.busy)
        self.preview_button.setEnabled(bool(self.review and self.review.get("status") == "PROPOSED")
                                       and self.review_approval.isChecked() and not self.busy)
        self.run_button.setEnabled(bool(self.previews) and self.review_approval.isChecked()
                                   and self.preview_approval.isChecked() and not self.busy)

    def _invalidate_preview(self, *_):
        self.previews = {}
        self.preview_approval.setChecked(False)
        self._refresh()

    def _invalidate_review(self):
        self.review = None
        self.review_approval.setChecked(False)
        self._invalidate_preview()

    def _use_profile(self, profile):
        self.profile = profile
        self._choices = {}
        self._invalidate_review()
        self.table.setRowCount(0)
        self.profile_label.setText(str(profile.manifest.get("name", "Office profile")))
        self.details.setPlainText(json.dumps(profile.manifest, ensure_ascii=False, indent=2))
        self._refresh()

    @Slot()
    def open_profile(self):
        selected = self.prompts.pick_dir("Open office profile", "")
        if selected:
            from ..office_profiles import load_profile
            self._work("Validate office profile", lambda log: load_profile(Path(selected)), self._use_profile)

    @Slot()
    def create_profile(self):
        dialog = ProfileCreateDialog(self.prompts, self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            values = dialog.values()
        finally:
            dialog.deleteLater()
        selected = self.prompts.save_file("New profile directory", values["name"], "Office profile directory (*)")
        if selected:
            from ..office_profiles import create_profile
            self._work("Create office profile", lambda log: create_profile(Path(selected), **values), self._use_profile)

    @Slot()
    def import_profile(self):
        archive = self.prompts.pick_file("Import office profile", "", "Office profile (*.zip)")
        if not archive:
            return
        destination = self.prompts.save_file("New profile directory", Path(archive).stem, "Office profile directory (*)")
        if destination:
            from ..office_profiles import import_profile
            self._work("Import office profile", lambda log: import_profile(Path(archive), Path(destination)), self._use_profile)

    @Slot()
    def export_profile(self):
        if self.profile is None:
            return
        destination = self.prompts.save_file("Export office profile", "office-profile.zip", "Office profile (*.zip)")
        if destination:
            from ..office_profiles import export_profile
            profile = self.profile
            self._work("Export office profile", lambda log: export_profile(profile, Path(destination)),
                       lambda result: self.status.setText("Office profile exported."))

    @Slot()
    def analyze(self):
        if self.profile is None or self.busy:
            return False
        try:
            files = self.session.files(outcome=False)
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))
            return False
        if not files:
            self.status.setText("Select specification files in the main window first.")
            return False
        from ..office_profiles import prepare_review
        profile, choices = self.profile, json.loads(json.dumps(self._choices))
        self._invalidate_review()
        return self._work("Analyze specification structure", lambda log: prepare_review(profile, files, choices=choices), self._show_review)

    def _show_review(self, review):
        from ..office_profiles import review_text
        self.review = review
        self.details.setPlainText(review_text(review))
        self._rows = []
        for file in review.get("documents", []):
            for row in file.get("analysis", {}).get("paragraphs", []):
                self._rows.append((file["source"], row))
        self.table.setRowCount(len(self._rows))
        for index, (source, row) in enumerate(self._rows):
            location = row.get("location") or f"Paragraph {row['paragraph'] + 1}"
            for column, text in enumerate((Path(source).name + " — " + str(location), row["text"],
                                           ROLE_LABELS.get(row.get("role"), row["disposition"]))):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.table.setItem(index, column, item)
            choice = QComboBox(self.table)
            choice.addItem("Use observed role" if row.get("role") else "Decision required", None)
            choice.addItem("Leave this paragraph unchanged", "exclude")
            for role, label in ROLE_LABELS.items():
                choice.addItem(label, role)
            selected = self._choices.get(source, {}).get(str(row["paragraph"]))
            if selected is not None:
                choice.setCurrentIndex(choice.findData(selected))
            if row["disposition"] == "excluded":
                choice.setEnabled(False)
            choice.currentIndexChanged.connect(self._choice_changed)
            self.table.setCellWidget(index, 3, choice)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(1, 430)
        self.status.setText("Review every proposed target. After changing a choice, analyze again to prepare a fresh review.")
        self._refresh()

    @Slot(int)
    def _choice_changed(self, _):
        choices = {}
        for index, (source, row) in enumerate(self._rows):
            value = self.table.cellWidget(index, 3).currentData()
            if value is not None:
                choices.setdefault(source, {})[str(row["paragraph"])] = value
        self._choices = choices
        self._invalidate_review()
        self.status.setText("Choices changed. Analyze again before approving the new review.")

    @Slot()
    def preview(self):
        if self.review is None or not self.review_approval.isChecked() or self.busy:
            return False
        from ..office_profiles import preview_review, save_review
        review, root, render = self.review, Path(self.output.text()).resolve(), self.render.isChecked()
        self._invalidate_preview()
        def work(log):
            previews = preview_review(review, root / "preview", do_render=render)
            save_review(review, root / "office-review.json")
            return previews
        return self._work("Preview office standard", work, self._show_preview)

    def _show_preview(self, previews):
        self.previews = previews
        self.details.setPlainText("\n\n".join(str(source) + "\n" + preview.to_text() for source, preview in previews.items()))
        self.status.setText("Inspect the preview records and pages before checking the preview approval box.")
        self._refresh()

    @Slot()
    def run(self):
        if self.busy or not self.previews or not self.review_approval.isChecked() or not self.preview_approval.isChecked():
            return False
        from ..office_profiles import execute_review, validate_review
        try:
            validate_review(self.review, files=self.session.files(outcome=False))
        except ValueError as exc:
            self._invalidate_review()
            self.status.setText(str(exc))
            return False
        review, previews = self.review, dict(self.previews)
        root, render = Path(self.output.text()).resolve(), self.render.isChecked()
        self.preview_approval.setChecked(False)
        def work(log):
            return execute_review(review, root / "run", approval=review["digest"], previews=previews,
                                  preview_approvals={str(source): preview.token for source, preview in previews.items()},
                                  ack_review=True, do_render=render)
        return self._work("Run verified office copies", work, self._show_results)

    def _show_results(self, results):
        self.previews = {}
        self.details.setPlainText("\n".join(str(result.out_dir) + "\n" + json.dumps(result.counts()) for result in results))
        self.status.setText("Run complete. Inspect each file's result and verification coverage in the results folder.")
        self._refresh()

    @Slot()
    def open_results(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.output.text()).resolve())))

    def _work(self, title, fn, done):
        if self.busy:
            return False
        self.busy = True
        self._on_done = done
        self.content.setEnabled(False)
        self.status.setText(title + "…")
        parent = self.parent()
        if hasattr(parent, "set_busy"):
            parent.set_busy(True, title + "…")
        self._thread = start_job(self.bridge, fn, name="office-profile")
        return True

    def _finish(self):
        self.busy = False
        self.content.setEnabled(True)
        parent = self.parent()
        if hasattr(parent, "set_busy"):
            parent.set_busy(False)
        self._refresh()

    @Slot(str)
    def _log(self, message):
        self.status.setText(message)

    @Slot(object)
    def _done(self, result):
        callback, self._on_done = self._on_done, None
        try:
            if callback is not None:
                callback(result)
        except Exception as exc:
            self.previews = {}
            self.preview_approval.setChecked(False)
            self.status.setText(str(exc))
        finally:
            self._finish()

    @Slot(str, str)
    def _failed(self, message, traceback):
        self.previews = {}
        self.preview_approval.setChecked(False)
        self.status.setText(message)
        self._on_done = None
        self._finish()

    def reject(self):
        if not self.busy:
            super().reject()

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
        else:
            event.accept()
