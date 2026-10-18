"""A local, read-only view of the same built-in contracts used by the compiler."""
from __future__ import annotations

import html
import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                               QSplitter, QTextBrowser, QVBoxLayout, QWidget)

from ..catalog import REQUEST_ONLY_OPS, op_catalog


_GROUPS = {
    "Styles and formatting": {
        "apply_style", "map_styles", "remove_unused_styles", "set_font", "set_style_font",
        "set_doc_defaults_font", "set_properties", "set_paragraph_props", "set_borders_shading",
        "set_tab_stops", "clear_direct_formatting", "set_language", "remove_highlight",
    },
    "Pages, headers and footers": {
        "apply_header_footer_template", "remove_header_footer", "set_page_setup", "fix_page_numbering",
        "add_watermark", "remove_watermark", "set_issue_stamp",
    },
    "Numbering": {"audit_numbering", "convert_numbering", "convert_typed_numbers", "set_list_level"},
    "Tables and pictures": {"apply_table_style", "set_table_props", "replace_table", "swap_image"},
    "Document review and metadata": {
        "resolve_tracked_changes", "remove_comments", "add_review_comments", "remove_notes", "remove_hidden_text",
        "remove_hyperlinks", "unlink_fields", "set_document_properties", "set_document_state", "name_output",
    },
}
_WORKFLOWS = {
    "apply_style": "architect-styles", "map_styles": "architect-styles",
    "remove_unused_styles": "architect-styles", "convert_numbering": "match-numbering",
    "apply_table_style": "format-tables", "set_table_props": "format-tables",
    "set_language": "proofing-language",
}


def _category(name):
    return next((group for group, names in _GROUPS.items() if name in names), "Text and wording")


def _fields_html(schema):
    e = html.escape
    lines = []
    required = schema.get("required", [])
    if schema.get("description"):
        lines.append(f"<p>{e(schema['description'])}</p>")
    for name, field in schema.get("properties", {}).items():
        notes = ["required"] if name in required else ["optional"]
        if "default" in field:
            notes.append("default: " + json.dumps(field["default"], ensure_ascii=False))
        if "enum" in field:
            notes.append("choices: " + ", ".join(json.dumps(v, ensure_ascii=False) for v in field["enum"]))
        if field.get("type"):
            notes.append(str(field["type"]))
        lines.append(f"<p><b>{e(name)}</b> — {e('; '.join(notes))}<br>{e(field.get('description', ''))}</p>")
    return "".join(lines) or "<p>No additional fields.</p>"


def operation_html(name, contract):
    e = html.escape
    limits = "".join(f"<li>{e(s)}</li>" for s in contract.get("common_assumptions", []))
    checks = "".join(f"<p><b>{e(check)}</b>: {e(str(detail))}</p>"
                     for check, detail in contract.get("verification", {}).items())
    explicit = ("<p><b>Requires an explicit user request.</b> This operation must never be added automatically.</p>"
                if name in REQUEST_ONLY_OPS else "")
    return (f"<h2>{e(name.replace('_', ' ').capitalize())}</h2><p><code>{e(name)}</code></p>"
            f"<p>{e(contract.get('description', ''))}</p>"
            f"<p><b>Edit mode:</b> {e(contract.get('mode', ''))}</p>{explicit}"
            f"<h3>Targets and selection</h3>{_fields_html(contract.get('select', {}))}"
            f"<h3>Inputs</h3>{_fields_html(contract.get('params', {}))}"
            f"<h3>Behavior and limits</h3><ul>{limits}</ul>"
            f"<h3>Verification</h3>{checks}"
            "<p>These checks validate a particular recipe. An independently approved outcome adds a separate "
            "original-to-final check for supported requests. Automated rendering does not record human visual acceptance.</p>")


class OperationBrowserDialog(QDialog):
    workflow_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Built-in operations")
        self.resize(960, 720)
        self.catalog = op_catalog(examples=False)["ops"]
        layout = QVBoxLayout(self)
        intro = QLabel("Browse the deterministic operations available to the editor. Opening this catalog changes "
                       "no documents and needs no API key. Use a guided workflow, describe a change in chat, or load "
                       "an EditSpec to prepare an edit.", self)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        filters = QHBoxLayout()
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search operations, inputs and behavior…")
        self.category = QComboBox(self)
        self.category.addItems(["All categories", *sorted({_category(name) for name in self.catalog})])
        filters.addWidget(self.search, 1)
        filters.addWidget(self.category)
        layout.addLayout(filters)
        self.count = QLabel(self)
        layout.addWidget(self.count)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.operations = QListWidget(splitter)
        self.details = QTextBrowser(splitter)
        self.details.setOpenLinks(False)
        splitter.setSizes([280, 680])
        layout.addWidget(splitter, 1)
        self.workflow = QPushButton("Open related guided workflow…", self)
        self.workflow.clicked.connect(self._request_workflow)
        layout.addWidget(self.workflow)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self.search.textChanged.connect(self._filter)
        self.category.currentTextChanged.connect(self._filter)
        self.operations.currentItemChanged.connect(self._show_operation)
        self._filter()

    def _filter(self, *_):
        selected = self.operations.currentItem()
        previous = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        self.operations.clear()
        query = self.search.text().casefold().strip()
        group = self.category.currentText()
        for name, contract in self.catalog.items():
            if group != "All categories" and _category(name) != group:
                continue
            if query and query not in (name.replace("_", " ") + " " + name + " " +
                                      json.dumps(contract, ensure_ascii=False)).casefold():
                continue
            item = QListWidgetItem(name.replace("_", " ").capitalize())
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setToolTip(contract.get("description", ""))
            self.operations.addItem(item)
            if name == previous:
                self.operations.setCurrentItem(item)
        self.count.setText(f"{self.operations.count()} of {len(self.catalog)} built-in operations")
        if self.operations.currentItem() is None and self.operations.count():
            self.operations.setCurrentRow(0)
        if not self.operations.count():
            self.details.setPlainText("No operations match this search.")
            self.workflow.hide()

    def _show_operation(self, item, *_):
        if item is None:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        self.details.setHtml(operation_html(name, self.catalog[name]))
        self.workflow.setVisible(name in _WORKFLOWS)

    def _request_workflow(self):
        item = self.operations.currentItem()
        if item:
            workflow = _WORKFLOWS.get(item.data(Qt.ItemDataRole.UserRole))
            if workflow:
                self.workflow_requested.emit(workflow)
