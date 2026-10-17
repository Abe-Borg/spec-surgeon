"""Report-only QA viewer; literal document content is never interpreted as HTML."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout

from ..spec_qa import audit_markdown


class AuditReportDialog(QDialog):
    def __init__(self, report, prompts, parent=None, rerun=None):
        super().__init__(parent)
        self.report, self.prompts = report, prompts
        self.setWindowTitle("Spec QA audits — report only")
        self.resize(1050, 760)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Source files are unchanged. Findings include every detected occurrence and its location."))
        identity_row = QHBoxLayout()
        self.identity_fields = {}
        for field, label in (("project_name", "Project name"), ("project_number", "Project number"), ("date", "Issue date")):
            edit = QLineEdit()
            edit.setPlaceholderText(label + " (optional expected value)")
            edit.setText((report.get("identity_variants", {}).get(field, {}).get("expected")) or "")
            self.identity_fields[field] = edit
            identity_row.addWidget(edit)
        self.recheck_button = QPushButton("Check with these values")
        self.recheck_button.setEnabled(rerun is not None)
        identity_row.addWidget(self.recheck_button)
        layout.addLayout(identity_row)
        def recheck():
            identity = {key: edit.text().strip() for key, edit in self.identity_fields.items() if edit.text().strip()}
            if rerun and rerun(identity):
                self.close()
        self.recheck_button.clicked.connect(recheck)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setPlainText(audit_markdown(report))
        layout.addWidget(self.text, 1)
        buttons = QHBoxLayout()
        self.save_md = QPushButton("Save Markdown…")
        self.save_json = QPushButton("Save JSON…")
        self.close_button = QPushButton("Close")
        buttons.addWidget(self.save_md)
        buttons.addWidget(self.save_json)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self.save_md.clicked.connect(lambda: self.save("md"))
        self.save_json.clicked.connect(lambda: self.save("json"))
        self.close_button.clicked.connect(self.close)

    def save(self, format_):
        selected = self.prompts.save_file("Save QA report", f"SPEC_QA.{format_}", f"QA report (*.{format_})")
        if not selected:
            return False
        target = Path(selected).resolve()
        if target.suffix.lower() != "."+format_:
            self.prompts.warn("Save QA report", f"Choose a .{format_} report filename; source documents are never overwritten.")
            return False
        try:
            target.write_text(json.dumps(self.report, ensure_ascii=False, indent=2) if format_ == "json"
                              else audit_markdown(self.report), encoding="utf-8")
        except OSError as e:
            self.prompts.warn("Save QA report", str(e))
            return False
        return True
