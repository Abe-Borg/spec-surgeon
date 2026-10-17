"""Input form for packaged workflows; preview and execution stay in the main window."""
from __future__ import annotations

import json

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
                               QHeaderView, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
                               QTableWidget, QVBoxLayout, QWidget)

from ..workflows import build_workflow, get_workflow
from .widgets import label


class StyleMapEditor(QWidget):
    """Named source/destination pairs, without requiring an EditSpec or JSON."""
    def __init__(self, parent):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(2, 2, self)
        self.table.setHorizontalHeaderLabels(["Source style", "Your destination style"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setMaximumHeight(135)
        layout.addWidget(self.table)
        add = QPushButton("Add mapping", self)
        add.clicked.connect(lambda: self.table.insertRow(self.table.rowCount()))
        layout.addWidget(add)

    def value(self):
        result = []
        for row in range(self.table.rowCount()):
            pair = [self.table.item(row, col).text().strip() if self.table.item(row, col) else "" for col in range(2)]
            if any(pair):
                result.append(dict(zip(("from", "to"), pair)))
        return result


class ScopeEditor(QWidget):
    def __init__(self, selected, parent):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.checks = {}
        for key, title in (("document", "Body"), ("headers", "Headers"), ("footers", "Footers"),
                           ("footnotes", "Footnotes"), ("endnotes", "Endnotes"), ("comments", "Comments")):
            check = QCheckBox(title, self)
            check.setChecked(key in selected)
            layout.addWidget(check)
            self.checks[key] = check

    def value(self):
        return [key for key, check in self.checks.items() if check.isChecked()]


class NumberingSourceEditor(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.choice = QComboBox(self)
        self.choice.addItems(["Choose the numbering to convert…", "The list numbering the most paragraphs", "Only these source styles"])
        layout.addWidget(self.choice)
        self.styles = QPlainTextEdit(self)
        self.styles.setPlaceholderText("One source level per line; separate alternative styles with commas")
        self.styles.setFixedHeight(82)
        layout.addWidget(self.styles)
        self.styles.hide()
        self.choice.currentIndexChanged.connect(lambda i: self.styles.setVisible(i == 2))

    def value(self):
        if self.choice.currentIndex() == 0:
            raise ValueError("Choose which automatic numbering to convert.")
        if self.choice.currentIndex() == 1:
            return "main"
        return {"styles": [[s.strip() for s in line.split(",") if s.strip()]
                            for line in self.styles.toPlainText().splitlines() if line.strip()]}


class TableSelectorEditor(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.choice = QComboBox(self)
        self.choice.addItems(["Choose tables…", "All tables in the selected regions", "Match the first row", "Match text anywhere in the table", "A range of table numbers"])
        layout.addWidget(self.choice)
        self.pattern = QLineEdit(self)
        self.pattern.setPlaceholderText("Text pattern (regular expression), for example ^Tag")
        self.pattern.hide()
        layout.addWidget(self.pattern)
        self.range = QWidget(self)
        row = QHBoxLayout(self.range)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(label("First table", "Muted"))
        self.first, self.last = QSpinBox(self.range), QSpinBox(self.range)
        for editor in (self.first, self.last):
            editor.setRange(1, 10000)
        row.addWidget(self.first)
        row.addWidget(label("Last table", "Muted"))
        row.addWidget(self.last)
        self.range.hide()
        layout.addWidget(self.range)
        self.choice.currentIndexChanged.connect(self._selection_changed)

    @Slot(int)
    def _selection_changed(self, index):
        self.pattern.setVisible(index in (2, 3))
        self.range.setVisible(index == 4)

    def value(self):
        index = self.choice.currentIndex()
        if not index:
            raise ValueError("Choose which tables to format.")
        if index == 1:
            return "all"
        if index == 4:
            if self.first.value() > self.last.value():
                raise ValueError("The first table must come before or equal the last table.")
            return {"index_range": [self.first.value() - 1, self.last.value() - 1]}
        if not self.pattern.text().strip():
            raise ValueError("Enter a text pattern for the tables to format.")
        return {"first_row_regex" if index == 2 else "text_regex": self.pattern.text()}


class TableLayoutEditor(QWidget):
    """Common layout settings are explicit checkboxes; other supported options remain available."""
    def __init__(self, parent):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.enabled = {}
        self.editors = {}
        for key, title, minimum, maximum, initial in (("width", "Set width (% of text width)", 1, 100, 100),
                                                     ("repeat_header_rows", "Repeat first rows on each page", 0, 100, 1)):
            row = QHBoxLayout()
            check = QCheckBox(title, self)
            editor = QSpinBox(self)
            editor.setRange(minimum, maximum)
            editor.setValue(initial)
            editor.setEnabled(False)
            check.toggled.connect(editor.setEnabled)
            row.addWidget(check, 1)
            row.addWidget(editor)
            layout.addLayout(row)
            self.enabled[key], self.editors[key] = check, editor
        self.rows = QComboBox(self)
        self.rows.addItems(["Row page breaks: leave unchanged", "Keep each row on one page", "Allow rows to break across pages"])
        layout.addWidget(self.rows)
        self.advanced_toggle = QCheckBox("Other layout settings (JSON)", self)
        layout.addWidget(self.advanced_toggle)
        self.advanced = QPlainTextEdit(self)
        self.advanced.setPlaceholderText('{"cell_margins": {"left": 115, "right": 115}, "borders": "none"}')
        self.advanced.setFixedHeight(82)
        self.advanced.hide()
        self.advanced_toggle.toggled.connect(self.advanced.setVisible)
        layout.addWidget(self.advanced)

    def value(self):
        result = json.loads(self.advanced.toPlainText()) if self.advanced_toggle.isChecked() else {}
        if not isinstance(result, dict):
            raise ValueError("Other table layout settings must be a JSON object.")
        for key, check in self.enabled.items():
            if check.isChecked():
                if key in result:
                    raise ValueError(f"Choose {key} in the form or in other settings, not both.")
                value = self.editors[key].value()
                result[key] = {"pct": value} if key == "width" else value
        if self.rows.currentIndex():
            if "cant_split" in result:
                raise ValueError("Choose row page breaks in the form or in other settings, not both.")
            result["cant_split"] = self.rows.currentIndex() == 1
        return result


class WorkflowDialog(QDialog):
    def __init__(self, workflow_id: str, prompts, parent=None):
        super().__init__(parent)
        self.workflow = get_workflow(workflow_id)
        self.prompts = prompts
        self.setWindowTitle(self.workflow["name"])
        self.resize(720, 700)
        layout = QVBoxLayout(self)
        layout.addWidget(label(self.workflow["description"], "Muted"))
        layout.addWidget(label("Supply the styles and settings for this batch. Next, Preview shows the changes before Run.", "Muted"))
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        body = QWidget(scroll)
        fields = QVBoxLayout(body)
        self.editors = {}
        for field in self.workflow["inputs"]:
            name = field["name"]
            required = " (required)" if "default" not in field else ""
            fields.addWidget(label(field["label"] + required, "CardTitle"))
            if name == "style_map":
                editor = StyleMapEditor(body)
            elif name == "scope":
                editor = ScopeEditor(field["default"], body)
            elif name == "source":
                editor = NumberingSourceEditor(body)
            elif name == "properties":
                editor = TableLayoutEditor(body)
            elif name == "tables":
                editor = TableSelectorEditor(body)
            elif field["type"] == "boolean":
                editor = QCheckBox("Yes", body)
                editor.setChecked(field.get("default", False))
            elif "choices" in field:
                editor = QComboBox(body)
                editor.addItems(field["choices"])
                editor.setCurrentText(field.get("default", field["choices"][0]))
            elif field["type"] == "string":
                editor = QLineEdit(body)
                editor.setText(field.get("default", ""))
                editor.setPlaceholderText(str(field.get("example", "")))
            else:
                editor = QPlainTextEdit(body)
                editor.setFixedHeight(82)
                if "default" in field:
                    editor.setPlainText("\n".join(field["default"]) if field["type"] == "array"
                                        else json.dumps(field["default"], ensure_ascii=False))
                if "example" in field:
                    editor.setPlaceholderText("\n".join(field["example"]) if field["type"] == "array"
                                              else json.dumps(field["example"], ensure_ascii=False))
            self.editors[name] = editor
            if field.get("format") == "document":
                row = QHBoxLayout()
                row.addWidget(editor, 1)
                browse = QPushButton("Browse…", body)
                browse.clicked.connect(lambda _checked=False, target=editor: self._browse(target))
                row.addWidget(browse)
                fields.addLayout(row)
            else:
                fields.addWidget(editor)
            description = {"scope": "Choose the regions to change.",
                           "source": "Choose the main list or name the source styles at each level.",
                           "tables": "Table numbers restart in each selected region and include nested tables. A text pattern selects matching tables.",
                           "replace_only": "One language tag per line, such as en-GB. Leave empty to change the Latin-text language everywhere."}.get(name, field["description"])
            if field["type"] == "array" and name not in ("scope", "style_map"):
                description += " Enter one value per line."
            fields.addWidget(label(description, "Muted"))
            fields.addSpacing(8)
        fields.addStretch(1)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        self.error = label("", "Muted")
        self.error.setParent(self)
        self.error.hide()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Use workflow")
        buttons.accepted.connect(self._accept_inputs)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self, target):
        path = self.prompts.pick_file("Choose an example document", target.text(), "Word documents (*.docx *.dotx)")
        if path:
            target.setText(path)

    def values(self) -> dict:
        values = {}
        for field in self.workflow["inputs"]:
            name, editor = field["name"], self.editors[field["name"]]
            if isinstance(editor, (StyleMapEditor, ScopeEditor, NumberingSourceEditor, TableLayoutEditor, TableSelectorEditor)):
                value = editor.value()
            elif isinstance(editor, QCheckBox):
                value = editor.isChecked()
            elif isinstance(editor, QComboBox):
                value = editor.currentText()
            elif isinstance(editor, QLineEdit):
                value = editor.text().strip()
            elif field["type"] == "array":
                value = [line.strip() for line in editor.toPlainText().splitlines() if line.strip()]
            else:
                try:
                    value = json.loads(editor.toPlainText())
                except ValueError as e:
                    raise ValueError(f"{field['label']}: enter valid JSON ({e})") from e
            values[name] = value
        return values

    @Slot()
    def _accept_inputs(self):
        try:
            build_workflow(self.workflow["id"], self.values())
        except Exception as e:
            self.error.setText(str(e))
            self.error.show()
            return
        self.accept()
