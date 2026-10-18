"""Report-only QA viewer; literal document content is never interpreted as HTML."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout

from ..spec_qa import audit_markdown
from ..project_evidence import validate_context


class AuditReportDialog(QDialog):
    def __init__(self, report, prompts, parent=None, rerun=None, *, project_context=None, context_label=None):
        super().__init__(parent)
        self.report, self.prompts = report, prompts
        self.rerun = rerun
        self.project_context = validate_context(project_context) if project_context is not None else None
        self.context_label = context_label
        self.setWindowTitle("Spec QA audits — report only")
        self.resize(1050, 760)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Source files are unchanged. Findings include every detected occurrence and its location."))
        context_row = QHBoxLayout()
        self.context_status = QLabel(self)
        self.context_status.setTextFormat(Qt.TextFormat.PlainText)
        self.context_status.setWordWrap(True)
        if self.project_context is None:
            self.context_status.setText("No approved standards register selected.")
        else:
            count = len(self.project_context.get("project_evidence", {}).get("standards", []))
            self.context_status.setText(f"Project context: {context_label or 'selected context'} — {count} approved standard entries.")
        context_row.addWidget(self.context_status, 1)
        self.import_context_button = QPushButton("Use project context JSON…", self)
        self.import_context_button.setEnabled(rerun is not None)
        self.import_context_button.clicked.connect(self.import_context)
        context_row.addWidget(self.import_context_button)
        self.clear_context_button = QPushButton("Clear project context", self)
        self.clear_context_button.setEnabled(rerun is not None and self.project_context is not None)
        self.clear_context_button.clicked.connect(self.clear_context)
        context_row.addWidget(self.clear_context_button)
        layout.addLayout(context_row)
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
            self._rerun(self.project_context, self.context_label)
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

    def _rerun(self, context, label):
        identity = {key: edit.text().strip() for key, edit in self.identity_fields.items() if edit.text().strip()}
        if self.rerun and self.rerun(identity, project_context=context, context_label=label):
            self.close()
            return True
        return False

    def import_context(self):
        selected = self.prompts.pick_file("Project context for this QA report", "", "Project context (*.json)")
        if not selected:
            return False
        try:
            context = json.loads(Path(selected).read_text(encoding="utf-8"))
            # `evidence inspect` exports one project's evidence object. A full
            # project context may contain other legitimate legacy fields too.
            if isinstance(context, dict) and set(context) == {"version", "facts", "standards"}:
                context = {"project_evidence": context}
            if not isinstance(context, dict) or not isinstance(context.get("project_evidence"), dict):
                raise ValueError("Choose a project context containing project_evidence, or one exported project evidence object.")
            context = validate_context(context)
        except (OSError, ValueError) as exc:
            self.prompts.warn("Project context", str(exc))
            return False
        return self._rerun(context, str(Path(selected).resolve()))

    def clear_context(self):
        return self._rerun(None, None)

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
