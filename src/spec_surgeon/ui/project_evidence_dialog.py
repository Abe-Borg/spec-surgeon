"""Human-maintained project facts and standards; writes through Campaign only."""
from __future__ import annotations

import uuid

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget)

from ..project_evidence import validate_context


class ProjectEvidenceDialog(QDialog):
    def __init__(self, campaign, project_id, prompts=None, parent=None):
        super().__init__(parent)
        self.campaign, self.project_id, self.prompts = campaign, project_id, prompts
        self.changed = False
        self.setWindowTitle("Project facts and standards")
        self.resize(920, 760)
        layout = QVBoxLayout(self)
        intro = QLabel("Record the facts and standard editions reviewed for this project. Changes invalidate its "
                       "prepared results and require a new plan. Assumptions remain separate from confirmed facts.", self)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        tabs = QTabWidget(self)
        layout.addWidget(tabs, 1)
        facts = QWidget(tabs)
        fact_layout = QVBoxLayout(facts)
        self.facts = self._table(["ID", "Statement", "Scope", "Status", "Source / reviewer"], facts)
        fact_layout.addWidget(self.facts, 1)
        self.fact_form = self._form(facts, fact_layout, fact=True)
        fact_actions = QHBoxLayout()
        self.add_fact_button = QPushButton("Add fact", facts)
        self.add_fact_button.clicked.connect(self.add_fact)
        fact_actions.addWidget(self.add_fact_button)
        self.supersede_button = QPushButton("Replace selected fact", facts)
        self.supersede_button.clicked.connect(self.supersede_fact)
        fact_actions.addWidget(self.supersede_button)
        fact_layout.addLayout(fact_actions)
        hint = QLabel("Replacing a fact preserves its original statement and source. A replacement needs explicit "
                      "human confirmation and must keep the same scope.", facts)
        hint.setWordWrap(True)
        fact_layout.addWidget(hint)
        tabs.addTab(facts, "Facts and history")
        standards = QWidget(tabs)
        standard_layout = QVBoxLayout(standards)
        self.standards = self._table(["ID", "Standard", "Edition", "Basis", "Scope"], standards)
        standard_layout.addWidget(self.standards, 1)
        self.standard_form = self._form(standards, standard_layout, fact=False)
        standard_actions = QHBoxLayout()
        self.add_standard_button = QPushButton("Add approved standard", standards)
        self.add_standard_button.clicked.connect(self.add_standard)
        standard_actions.addWidget(self.add_standard_button)
        self.replace_standard_button = QPushButton("Replace selected standard", standards)
        self.replace_standard_button.clicked.connect(self.replace_standard)
        standard_actions.addWidget(self.replace_standard_button)
        self.remove_standard_button = QPushButton("Remove selected standard", standards)
        self.remove_standard_button.clicked.connect(self.remove_standard)
        standard_actions.addWidget(self.remove_standard_button)
        standard_layout.addLayout(standard_actions)
        note = QLabel("Enter the edition and its project basis yourself. The audit compares against this register; "
                      "it does not infer a jurisdiction's adopted or newest edition.", standards)
        note.setWordWrap(True)
        standard_layout.addWidget(note)
        tabs.addTab(standards, "Approved standards")
        self.status = QLabel("", self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    @staticmethod
    def _table(headers, parent):
        table = QTableWidget(0, len(headers), parent)
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    @staticmethod
    def _form(parent, layout, *, fact):
        form, fields = QFormLayout(), {}
        fields["id"] = QLineEdit(parent)
        fields["id"].setPlaceholderText("Optional; a unique ID is assigned when blank")
        form.addRow("Record ID", fields["id"])
        for key, title in (("statement", "Fact statement"),) if fact else (
                ("identifier", "Standard identifier"), ("edition", "Approved edition"), ("basis", "Project basis")):
            widget = QPlainTextEdit(parent) if key == "statement" else QLineEdit(parent)
            if key == "statement":
                widget.setMaximumHeight(70)
            fields[key] = widget
            form.addRow(title, widget)
        fields["scope"] = QComboBox(parent)
        fields["scope"].addItems(["project", "discipline", "section", "document"])
        fields["scope_value"] = QLineEdit(parent)
        fields["scope_value"].setPlaceholderText("Exact discipline, section number, or full document path")
        fields["scope_value"].setEnabled(False)
        fields["scope"].currentTextChanged.connect(lambda text: fields["scope_value"].setEnabled(text != "project"))
        form.addRow("Scope", fields["scope"])
        form.addRow("Scope value", fields["scope_value"])
        if fact:
            fields["status"] = QComboBox(parent)
            fields["status"].addItems(["assumed", "confirmed"])
            form.addRow("Status", fields["status"])
            fields["source_kind"] = QComboBox(parent)
            fields["source_kind"].addItems(["user", "document", "model"])
            form.addRow("Source kind", fields["source_kind"])
        fields["source"] = QLineEdit(parent)
        fields["source"].setPlaceholderText("Who supplied this, or where the evidence is recorded")
        form.addRow("Source reference", fields["source"])
        fields["reviewer"] = QLineEdit(parent)
        fields["reviewer"].setPlaceholderText("Required for confirmation")
        form.addRow("Human reviewer", fields["reviewer"])
        layout.addLayout(form)
        return fields

    def _context(self):
        return validate_context(self.campaign.projects[self.project_id]["context"])

    @staticmethod
    def _scope_label(scope):
        return scope["kind"] + (": " + scope["value"] if "value" in scope else "")

    def refresh(self):
        evidence = self._context().get("project_evidence", {"facts": [], "standards": []})
        for table, rows, cells in (
                (self.facts, evidence["facts"], lambda r: [r["id"], r["statement"], self._scope_label(r["scope"]),
                    r["status"] + (" → " + r["replaced_by"] if r.get("replaced_by") else ""),
                    r["source"]["reference"] + (" / " + r["confirmed_by"] if r.get("confirmed_by") else "")]),
                (self.standards, evidence["standards"], lambda r: [r["id"], r["identifier"], r["edition"],
                    r["basis"], self._scope_label(r["scope"])])):
            table.setRowCount(len(rows))
            for index, row in enumerate(rows):
                for column, text in enumerate(cells(row)):
                    item = QTableWidgetItem(text)
                    item.setData(Qt.ItemDataRole.UserRole, row["id"])
                    table.setItem(index, column, item)
            table.resizeColumnsToContents()

    def _entry(self, fields, *, fact):
        scope = {"kind": fields["scope"].currentText()}
        if scope["kind"] != "project":
            scope["value"] = fields["scope_value"].text().strip()
        entry = {"id": fields["id"].text().strip() or ("fact-" if fact else "standard-") + uuid.uuid4().hex[:12],
                 "scope": scope, "source": {"kind": fields["source_kind"].currentText() if fact else "user",
                    "reference": fields["source"].text().strip()}}
        if fact:
            entry.update(statement=fields["statement"].toPlainText().strip(), status=fields["status"].currentText())
        else:
            entry.update({key: fields[key].text().strip() for key in ("identifier", "edition", "basis")})
        if not fact or entry["status"] == "confirmed":
            entry["confirmed_by"] = fields["reviewer"].text().strip()
        return entry

    @staticmethod
    def _selected(table):
        row = table.currentRow()
        if row < 0 or table.item(row, 0) is None:
            raise ValueError("Select a record first.")
        return table.item(row, 0).data(Qt.ItemDataRole.UserRole)

    def _apply(self, action):
        try:
            action()
            self.changed = True
            self.refresh()
            self.status.setText("Saved. Replan this project's affected work before running or exporting.")
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))

    def add_fact(self):
        self._apply(lambda: self.campaign.set_project_fact(self.project_id, self._entry(self.fact_form, fact=True)))

    def supersede_fact(self):
        self._apply(lambda: self.campaign.supersede_project_fact(self.project_id, self._selected(self.facts),
                                                               self._entry(self.fact_form, fact=True)))

    def _save_standard(self, replace=False):
        entries = self._context().get("project_evidence", {}).get("standards", [])
        new = self._entry(self.standard_form, fact=False)
        if replace:
            selected = self._selected(self.standards)
            entries = [row for row in entries if row["id"] != selected]
        entries.append(new)
        self.campaign.set_standards_register(self.project_id, entries)

    def add_standard(self):
        self._apply(self._save_standard)

    def replace_standard(self):
        self._apply(lambda: self._save_standard(replace=True))

    def remove_standard(self):
        def remove():
            selected = self._selected(self.standards)
            entries = self._context().get("project_evidence", {}).get("standards", [])
            self.campaign.set_standards_register(self.project_id, [row for row in entries if row["id"] != selected])
        self._apply(remove)
