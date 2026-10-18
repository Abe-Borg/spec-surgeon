"""Desktop workspace for durable, project-scoped investigations.

Models and files are handled by Campaign; the dialog only edits scoped user input and displays evidence.
All inventory, investigation, loading and export jobs use GUI-thread slots through a parented Bridge.
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QProgressBar, QPushButton, QScrollArea, QSplitter, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from .resource_controls import ResourceControls
from .plan_text import plan_text
from .worker import Bridge, start_job


class CampaignDialog(QDialog):
    def __init__(self, session, prompts, parent=None):
        super().__init__(parent)
        self.session, self.prompts = session, prompts
        self.busy = False
        self._selected_project = None
        self._shown_goal = {}
        self._pending_spec = None
        self._pending_chain = None
        self._dirty = False
        self._refreshing = False
        self.setWindowTitle("Multi-project workspace")
        self.resize(1120, 800)
        self.bridge = Bridge(self)
        self.bridge.log.connect(self._log)
        self.bridge.done.connect(self._done)
        self.bridge.failed.connect(self._failed)
        layout = QVBoxLayout(self)
        intro = QLabel("Keep projects, instructions and templates separate. Verified copies and investigation "
                       "evidence stay in the workspace between sessions.", self)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.content = QWidget(self)
        body = QVBoxLayout(self.content)
        body.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.content, 1)
        toolbar = QHBoxLayout()
        self.create_btn = self._button("New workspace…", self.create_workspace, toolbar)
        self.open_btn = self._button("Open workspace…", self.open_workspace, toolbar)
        self.folder_btn = self._button("Open evidence folder", self.open_folder, toolbar)
        toolbar.addStretch()
        body.addLayout(toolbar)
        self.location = QLabel("No workspace open", self)
        self.location.setTextFormat(Qt.TextFormat.PlainText)
        self.location.setWordWrap(True)
        body.addWidget(self.location)
        splitter = QSplitter(self)
        body.addWidget(splitter, 1)
        left = QWidget(splitter)
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Projects", left))
        self.projects = QListWidget(left)
        self.projects.setAccessibleName("Workspace projects")
        self.projects.currentRowChanged.connect(self._select_project)
        left_layout.addWidget(self.projects)
        hint = QLabel("Documents in selected project. Double-click to inspect a ready copy or source; "
                      "select rows to reassign.", left)
        hint.setWordWrap(True)
        left_layout.addWidget(hint)
        self.documents = QTableWidget(0, 3, left)
        self.documents.setHorizontalHeaderLabels(["Document", "Discipline", "Status"])
        self.documents.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.documents.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.documents.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.documents.horizontalHeader().setStretchLastSection(True)
        self.documents.cellDoubleClicked.connect(self.open_document)
        self.documents.itemSelectionChanged.connect(self._scope_edited)
        left_layout.addWidget(self.documents, 2)
        assignment = QHBoxLayout()
        self.project_id = QComboBox(left)
        self.project_id.setEditable(True)
        self.project_id.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.project_id.lineEdit().setPlaceholderText("Choose a project or type a new name")
        self.project_id.setAccessibleName("Destination project")
        assignment.addWidget(self.project_id)
        self.assign_btn = self._button("Assign selected", self.assign_documents, assignment)
        left_layout.addLayout(assignment)
        self.refresh_sources_btn = self._button("Refresh repaired / changed sources", self.refresh_sources, left_layout)
        self.refresh_sources_btn.setToolTip("Reinspect the selected originals and update only their dependent plans.")
        self.tabs = QTabWidget(splitter)
        scroll = QScrollArea(self.tabs)
        scroll.setWidgetResizable(True)
        editor = QWidget(scroll)
        form = QVBoxLayout(editor)
        form.addWidget(QLabel("Request for this project", editor))
        self.request = QPlainTextEdit(editor)
        self.request.setAccessibleName("Project request")
        self.request.setPlaceholderText("Describe the change for this project only.")
        self.request.setMinimumHeight(100)
        self.request.setMaximumHeight(110)
        form.addWidget(self.request)
        scope_row = QHBoxLayout()
        self.scope = QComboBox(editor)
        self.scope.addItems(["All documents in this project", "Only the selected documents"])
        self.scope.setAccessibleName("Plan document scope")
        self.scope.currentIndexChanged.connect(self._scope_edited)
        scope_row.addWidget(self.scope, 1)
        self.scope_label = QLabel("", editor)
        scope_row.addWidget(self.scope_label)
        form.addLayout(scope_row)
        self.plan_btn = self._button("Investigate and propose a plan", self.generate_plan, form)
        self.goal_mode = QComboBox(editor)
        self.goal_mode.addItems(["Describe a change / use saved spec", "Format an exact section title"])
        form.addWidget(self.goal_mode)
        self.title_goal = QGroupBox("Section title goal", editor)
        title_form = QFormLayout(self.title_goal)
        self.title_text = QLineEdit(self.title_goal)
        self.title_text.setPlaceholderText("Exact text from the document")
        title_form.addRow("Title", self.title_text)
        self.section_number = QLineEdit(self.title_goal)
        title_form.addRow("Section number (optional)", self.section_number)
        self.style_id = QLineEdit(self.title_goal)
        title_form.addRow("Require style ID (optional)", self.style_id)
        self.properties = {}
        for family, key, caption in (("run", "b", "Bold"), ("run", "i", "Italic"),
                                     ("run", "caps", "All caps"), ("run", "smallCaps", "Small caps"),
                                     ("run", "strike", "Strikethrough"), ("paragraph", "keepNext", "Keep with next"),
                                     ("paragraph", "keepLines", "Keep lines together"),
                                     ("paragraph", "widowControl", "Widow / orphan control"),
                                     ("paragraph", "pageBreakBefore", "Page break before")):
            control = QComboBox(self.title_goal)
            control.addItem("Leave unchanged", None)
            control.addItem("On", True)
            control.addItem("Off", False)
            title_form.addRow(caption, control)
            self.properties[family, key] = control
            control.currentIndexChanged.connect(self._mark_dirty)
        self.tracked = QCheckBox("Track formatting changes", self.title_goal)
        self.tracked.toggled.connect(self._mark_dirty)
        title_form.addRow(self.tracked)
        form.addWidget(self.title_goal)
        self.goal_mode.currentIndexChanged.connect(self._goal_mode_changed)
        for control in (self.title_text, self.section_number, self.style_id):
            control.textChanged.connect(self._mark_dirty)
        template_row = QHBoxLayout()
        self.template = QLineEdit(editor)
        self.template.setPlaceholderText("Optional project template .docx")
        self.template.setAccessibleName("Project template")
        template_row.addWidget(self.template)
        self._button("Template…", self.pick_template, template_row)
        form.addLayout(template_row)
        spec_row = QHBoxLayout()
        self.spec_label = QLabel("Describe a goal, or load a saved spec for keyless execution.", editor)
        self.spec_label.setWordWrap(True)
        self.spec_label.setTextFormat(Qt.TextFormat.PlainText)
        spec_row.addWidget(self.spec_label, 1)
        self._button("Load spec / chain…", self.pick_spec, spec_row)
        self._button("Clear", self.clear_spec, spec_row)
        form.addLayout(spec_row)
        self.project_evidence_btn = self._button("Project facts and standards…", self.edit_project_evidence, form)
        form.addWidget(QLabel("Project context (advanced JSON)", editor))
        self.context = QPlainTextEdit(editor)
        self.context.setAccessibleName("Project context JSON")
        self.context.setMaximumHeight(100)
        form.addWidget(self.context)
        self.save_btn = self._button("Save instructions / draft", self.save_project, form)
        form.addWidget(QLabel("Questions for this project", editor))
        self.questions = QComboBox(editor)
        self.questions.setAccessibleName("Project question to answer")
        self.questions.currentIndexChanged.connect(self._question_changed)
        form.addWidget(self.questions)
        self.bindings = QComboBox(editor)
        self.bindings.setAccessibleName("Choose the intended title target")
        form.addWidget(self.bindings)
        self.binding_effect = QComboBox(editor)
        self.binding_effect.setAccessibleName("Effect that needs target selection")
        self.binding_effect.currentIndexChanged.connect(self._binding_effect_changed)
        form.addWidget(self.binding_effect)
        self.binding_candidates = QListWidget(editor)
        self.binding_candidates.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.binding_candidates.setAccessibleName("Select intended document targets")
        self.binding_candidates.setMaximumHeight(160)
        form.addWidget(self.binding_candidates)
        self.bind_btn = self._button("Use selected targets", self.bind_target, form)
        self.answer = QPlainTextEdit(editor)
        self.answer.setPlaceholderText("Your answer or clarification applies only to the selected project.")
        self.answer.setMaximumHeight(80)
        self.answer.setAccessibleName("Project answer")
        form.addWidget(self.answer)
        self._button("Save answer", self.save_answer, form)
        resources = QGroupBox("Per-project investigation allowance", editor)
        resources_layout = QVBoxLayout(resources)
        self.resources = ResourceControls(resources)
        resources_layout.addWidget(self.resources)
        self.allowance = QLabel("", resources)
        self.allowance.setWordWrap(True)
        resources_layout.addWidget(self.allowance)
        self.resources.cost.valueChanged.connect(self._update_allowance)
        self.render = QCheckBox("Render pages and request visual review", resources)
        self.render.setChecked(True)
        self.codegen = QCheckBox("Allow custom repair methods (sandboxed and independently verified)", resources)
        self.codegen.setChecked(True)
        self.memory = QCheckBox("Consult and remember local repair evidence", resources)
        self.memory.setChecked(True)
        for check in (self.render, self.codegen, self.memory):
            resources_layout.addWidget(check)
        form.addWidget(resources)
        form.addStretch()
        scroll.setWidget(editor)
        self.tabs.addTab(scroll, "Instructions and questions")
        self.plan_page = QWidget(self.tabs)
        plan_layout = QVBoxLayout(self.plan_page)
        self.plan_status = QLabel("", self.plan_page)
        self.plan_status.setTextFormat(Qt.TextFormat.PlainText)
        self.plan_status.setWordWrap(True)
        plan_layout.addWidget(self.plan_status)
        self.plan_review = QPlainTextEdit(self.plan_page)
        self.plan_review.setReadOnly(True)
        self.plan_review.setAccessibleName("Proposed changes, exact wording, evidence and risks")
        plan_layout.addWidget(self.plan_review, 1)
        self.approve_btn = self._button("Approve this plan and its exact wording", self.approve_plan, plan_layout)
        self.approve_btn.setToolTip("Approve only the plan displayed here. Execution and export remain separate.")
        self.tabs.addTab(self.plan_page, "Review proposed plan")
        self.evidence = QPlainTextEdit(self.tabs)
        self.evidence.setReadOnly(True)
        self.tabs.addTab(self.evidence, "Progress and evidence")
        splitter.setSizes([430, 640])
        self.review_ack = QCheckBox("I inspected the current copies and acknowledge any blocking visual findings", self)
        body.addWidget(self.review_ack)
        actions = QHBoxLayout()
        self.run_btn = self._button("Run approved work / resume", self.run_workspace, actions)
        self.export_btn = self._button("Export ready copies…", self.export_workspace, actions)
        actions.addStretch()
        body.addLayout(actions)
        self.status = QLabel("", self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.log = QPlainTextEdit(self)
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(120)
        self.log.setAccessibleName("Workspace activity log")
        layout.addWidget(self.log)
        for signal in (self.request.textChanged, self.template.textChanged, self.context.textChanged):
            signal.connect(self._mark_dirty)
        self.refresh()
        self._restore_options()

    def _button(self, title, action, layout):
        button = QPushButton(title, self)
        button.clicked.connect(action)
        layout.addWidget(button)
        return button

    @property
    def campaign(self):
        return self.session.campaign

    @Slot()
    def _mark_dirty(self):
        if not self._refreshing:
            self._dirty = True
            self.review_ack.setChecked(False)
            self.approve_btn.setEnabled(False)

    def refresh(self):
        self._refreshing = True
        current = self._selected_project
        self.projects.clear()
        self.project_id.clear()
        campaign = self.campaign
        if campaign:
            self.location.setText(str(campaign.out_dir))
            for project_id, project in sorted(campaign.projects.items(),
                    key=lambda pair: (str(pair[1].get("label", pair[0])).casefold(), pair[0])):
                item = QListWidgetItem(f"{project.get('label', project_id)} · "
                                       f"{len(project.get('document_ids', []))} files · "
                                       f"{project.get('status', 'pending')}", self.projects)
                item.setData(Qt.ItemDataRole.UserRole, project_id)
                item.setToolTip(f"{project.get('label', project_id)}\nWorkspace reference: {project_id}")
                self.project_id.addItem(project.get("label", project_id), project_id)
            self.project_id.setCurrentIndex(-1)
            self.evidence.setPlainText(campaign.to_text())
            row = next((i for i in range(self.projects.count())
                        if self.projects.item(i).data(Qt.ItemDataRole.UserRole) == current), 0)
            self.projects.setCurrentRow(row)
        self._refreshing = False
        self._select_project(self.projects.currentRow())
        self._update_allowance()
        for widget in (self.run_btn, self.export_btn, self.folder_btn, self.save_btn, self.assign_btn,
                       self.plan_btn, self.refresh_sources_btn, self.project_evidence_btn):
            widget.setEnabled(campaign is not None)

    @Slot()
    def _update_allowance(self):
        count = len(self.campaign.projects) if self.campaign else 0
        self.allowance.setText(f"Each project has this estimated spending cap; {count} projects could use up to "
                               f"${count * self.resources.cost.value():,.2f} combined per run. "
                               "Actual spending depends on the investigation. Ready work is retained.")

    @Slot(int)
    def _select_project(self, row):
        if self._refreshing:
            return
        item = self.projects.item(row)
        project_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        if self._dirty and project_id != self._selected_project and not self.save_project():
            self._refreshing = True
            for i in range(self.projects.count()):
                if self.projects.item(i).data(Qt.ItemDataRole.UserRole) == self._selected_project:
                    self.projects.setCurrentRow(i)
                    break
            self._refreshing = False
            return
        self._selected_project = project_id
        self._refreshing = True
        project = self.campaign.projects.get(project_id, {}) if self.campaign else {}
        goal = project.get("goal") or {}
        draft = project.get("planning_draft") or {}
        proposed = project.get("proposed_plan") or {}
        self._shown_goal = dict(goal)
        self._pending_spec, self._pending_chain = (None, None) if draft else (goal.get("spec"), goal.get("chain"))
        self.request.setPlainText(draft.get("request", proposed.get("request", goal.get("request", ""))))
        contract = goal.get("adaptive_contract") or {}
        is_title = contract.get("kind") == "format_section_title" and not draft
        self.goal_mode.setCurrentIndex(1 if is_title else 0)
        self.title_goal.setVisible(is_title)
        self.title_text.setText((contract.get("target") or {}).get("title", ""))
        self.section_number.setText((contract.get("target") or {}).get("section_number", ""))
        self.style_id.setText((contract.get("target") or {}).get("style_id", ""))
        for (family, key), control in self.properties.items():
            value = contract.get(family, {}).get(key)
            control.setCurrentIndex(1 if value is True else 2 if value is False else 0)
        self.tracked.setChecked(contract.get("tracked", False))
        self.template.setText(str(draft.get("template", proposed.get("template", goal.get("template"))) or ""))
        self.context.setPlainText(json.dumps(project.get("context") or {}, indent=2, ensure_ascii=False))
        self.spec_label.setText("Saved spec attached" if self._pending_spec else
                               "Saved chain attached" if self._pending_chain else "No saved spec attached")
        self.questions.clear()
        self.questions.addItem("Project clarification", None)
        for question in project.get("questions") or []:
            if isinstance(question, dict):
                if question.get("status") == "answered":
                    continue
                self.questions.addItem(str(question.get("question", question.get("text", "Question"))),
                                       question.get("id"))
            else:
                self.questions.addItem(str(question), None)
        self.answer.clear()
        ids = project.get("document_ids") or []
        self.documents.setRowCount(len(ids))
        for row, doc_id in enumerate(ids):
            doc = self.campaign.documents[doc_id]
            for column, text in enumerate((Path(doc["source"]).name, doc.get("discipline") or "Unclassified",
                                           doc.get("status") or "pending")):
                cell = QTableWidgetItem(str(text))
                cell.setData(Qt.ItemDataRole.UserRole, doc_id)
                cell.setToolTip(str(doc["source"]) + "\n" + str(doc.get("reason") or doc.get("unresolved") or ""))
                self.documents.setItem(row, column, cell)
        self.documents.resizeColumnsToContents()
        draft_ids = draft.get("document_ids") or proposed.get("document_ids") or project.get("goal_scope") or ids
        selected_scope = set(draft_ids) != set(ids)
        self.scope.setCurrentIndex(1 if selected_scope else 0)
        self.documents.clearSelection()
        if selected_scope:
            from PySide6.QtCore import QItemSelectionModel
            for row, doc_id in enumerate(ids):
                if doc_id in draft_ids:
                    self.documents.selectionModel().select(self.documents.model().index(row, 0),
                        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        if self.campaign:
            evidence = [self.campaign.to_text()]
            for run in project.get("runs") or []:
                report = Path(run["path"]) / "RECOVERY_REPORT.md"
                if self.campaign.out_dir.resolve() not in report.resolve().parents:
                    continue
                try:
                    evidence.append(f"\nProject investigation: {report}\n\n" + report.read_text(encoding="utf-8"))
                except OSError:
                    evidence.append(f"\nInvestigation evidence: {report.parent}")
            self.evidence.setPlainText("\n".join(evidence))
        self._dirty = False
        self._refreshing = False
        self._scope_changed()
        self._show_plan(project)

    def _selected_documents(self):
        rows = sorted({item.row() for item in self.documents.selectedItems()})
        return [self.documents.item(row, 0).data(Qt.ItemDataRole.UserRole) for row in rows]

    def _scope_documents(self):
        if self.scope.currentIndex() == 0:
            return None
        selected = self._selected_documents()
        if not selected:
            raise ValueError("Select the documents to include in this plan, or choose all documents in the project.")
        return selected

    @Slot()
    def _scope_changed(self):
        if not hasattr(self, "scope_label"):
            return
        count = len(self._selected_documents()) if self.scope.currentIndex() == 1 else self.documents.rowCount()
        self.scope_label.setText(f"{count} documents")

    @Slot()
    def _scope_edited(self):
        self._scope_changed()
        if (hasattr(self, "scope") and not self._refreshing
                and (self.sender() is self.scope or self.scope.currentIndex() == 1)):
            self._mark_dirty()

    def _show_plan(self, project):
        proposed = project.get("proposed_plan") or {}
        self._reviewed_plan_id = proposed.get("id")
        self.plan_review.setPlainText(plan_text(proposed, self.campaign.documents if self.campaign else {}))
        status = str(proposed.get("status", "No proposed plan"))
        self.plan_status.setText(status.replace("_", " ").capitalize() +
                                (" · Approval preserves the displayed document scope." if proposed else ""))
        matches = self._draft_matches_plan(project, proposed)
        if proposed and not matches:
            self.plan_status.setText("The instructions or scope changed. Generate a revised plan before approval.")
        self.approve_btn.setEnabled(bool(proposed.get("contract") and status == "PROPOSED" and matches
                                        and not proposed.get("questions") and not self._dirty))

    @staticmethod
    def _draft_matches_plan(project, proposed):
        draft = project.get("planning_draft")
        return not draft or (draft.get("request") == proposed.get("request")
                             and (draft.get("template") or None) == (proposed.get("template") or None)
                             and set(draft.get("document_ids", [])) == set(proposed.get("document_ids", [])))

    @Slot()
    def _goal_mode_changed(self):
        self.title_goal.setVisible(self.goal_mode.currentIndex() == 1)
        if not self._refreshing and self.goal_mode.currentIndex() == 1:
            self._pending_spec = self._pending_chain = None
            self.spec_label.setText("Adaptive section title goal")
        self._mark_dirty()

    def _adaptive_contract(self, request):
        if self.goal_mode.currentIndex() != 1:
            return None
        from ..goals import validate_contract
        old = self._shown_goal.get("adaptive_contract") or {}
        target = {"title": self.title_text.text()}
        for key, control in (("section_number", self.section_number), ("style_id", self.style_id)):
            if control.text():
                target[key] = control.text()
        contract = {"kind": "format_section_title", "target": target, "tracked": self.tracked.isChecked(),
                    "request": request}
        if old.get("author"):
            contract["author"] = old["author"]
        for (family, key), control in self.properties.items():
            value = control.currentData()
            if value is not None:
                contract.setdefault(family, {})[key] = value
        validate_contract(contract)
        return contract

    def _restore_options(self):
        snapshot = self.campaign.to_dict().get("resource_options") or {} if self.campaign else {}
        self.resources.set_values(snapshot)
        for key, control in (("render", self.render), ("allow_codegen", self.codegen), ("use_memory", self.memory)):
            if key in snapshot:
                control.setChecked(True if key == "allow_codegen" and snapshot[key] is None else snapshot[key])

    @Slot()
    def _question_changed(self):
        if not hasattr(self, "bindings"):
            return
        self.bindings.clear()
        project = self.campaign.projects.get(self._selected_project, {}) if self.campaign else {}
        question = next((q for q in project.get("questions", [])
                         if isinstance(q, dict) and q.get("id") == self.questions.currentData()), {})
        for candidate in question.get("candidates", []):
            self.bindings.addItem(f"Paragraph {candidate.get('paragraph_index', '?')} · "
                                  f"{'table' if candidate.get('in_table') else 'body'} · "
                                  f"style {candidate.get('style_id') or 'none'} · {candidate.get('text', '')}",
                                  (question.get("document_id"), candidate["candidate_id"]))
        self.bindings.setVisible(self.bindings.count() > 0)
        self.binding_effect.clear()
        effects = (question.get("resolution") or {}).get("effects") or {}
        if isinstance(effects, list):
            effects = {e.get("effect_id", e.get("id", str(i))): e for i, e in enumerate(effects)}
        for effect_id, resolution in effects.items():
            if resolution.get("candidates") and resolution.get("status") not in ("resolved", "RESOLVED"):
                self.binding_effect.addItem(f"{effect_id}: choose {resolution.get('expected', 1)} target(s)",
                    {"effect_id": effect_id, "resolution": resolution,
                     "document_id": question.get("document_id") or next(iter(question.get("document_ids", [])), None)})
        self.binding_effect.setVisible(self.binding_effect.count() > 0)
        self.binding_candidates.setVisible(self.binding_effect.count() > 0)
        self.bind_btn.setVisible(self.bindings.count() > 0 or self.binding_effect.count() > 0)
        self._binding_effect_changed()

    @Slot()
    def _binding_effect_changed(self):
        if not hasattr(self, "binding_candidates"):
            return
        self.binding_candidates.clear()
        selected = self.binding_effect.currentData() or {}
        for candidate in selected.get("resolution", {}).get("candidates", []):
            item = QListWidgetItem(f"{candidate.get('part', 'document')} · "
                f"paragraph {candidate.get('paragraph_index', '?')} · {candidate.get('text', '')}", self.binding_candidates)
            item.setData(Qt.ItemDataRole.UserRole, candidate.get("candidate_id", candidate.get("id")))
            item.setToolTip(json.dumps(candidate, ensure_ascii=False, indent=2))

    @Slot()
    def bind_target(self):
        target = self.bindings.currentData()
        effect = self.binding_effect.currentData()
        if (target or effect) and self.save_project():
            try:
                if effect:
                    candidate_ids = [item.data(Qt.ItemDataRole.UserRole) for item in self.binding_candidates.selectedItems()]
                    if not candidate_ids:
                        raise ValueError("Select the intended targets for this effect first.")
                    self.campaign.bind(effect["document_id"], effect_id=effect["effect_id"], candidate_ids=candidate_ids)
                else:
                    self.campaign.bind(*target)
                self.review_ack.setChecked(False)
                self.refresh()
                self.status.setText("Target choices saved. Run / resume to continue.")
            except (ValueError, OSError) as exc:
                self.status.setText(str(exc))

    @Slot()
    def save_project(self):
        if not self.campaign or not self._selected_project:
            return False
        if not self._dirty:
            return True
        try:
            context = json.loads(self.context.toPlainText() or "{}")
            if not isinstance(context, dict):
                raise ValueError("Project context must be a JSON object.")
            template = self.template.text().strip()
            if template and not Path(template).is_file():
                raise ValueError("Choose an existing template file.")
            goal = self._shown_goal
            request = self.request.toPlainText().strip()
            contract = self._adaptive_contract(request)
            spec = self._pending_spec if contract is None else None
            chain = self._pending_chain if contract is None else None
            project = self.campaign.projects[self._selected_project]
            scope = self._scope_documents()
            scope_ids = project["document_ids"] if scope is None else scope
            if context != (project.get("context") or {}):
                self.campaign.set_project_context(self._selected_project, context)
            if contract is None and spec is None and chain is None:
                if request:
                    self.campaign.save_plan_draft(self._selected_project, request,
                                                  doc_ids=scope, template=template)
            elif (request != goal.get("request", "") or template != str(goal.get("template") or "")
                    or spec != goal.get("spec") or chain != goal.get("chain")
                    or contract != goal.get("adaptive_contract")
                    or set(scope_ids) != set(project.get("goal_scope", project["document_ids"]))):
                self.campaign.set_goal(self._selected_project, request, spec=spec,
                                       chain=chain, template=Path(template) if template else None,
                                       approved_words=goal.get("approved_words") or (),
                                       adaptive_contract=contract, doc_ids=scope)
            self._shown_goal = dict(self.campaign.projects[self._selected_project].get("goal") or {})
            self._dirty = False
            self.status.setText("Instructions saved. Generate and review a proposed plan for a described outcome.")
            return True
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))
            return False

    @Slot()
    def edit_project_evidence(self):
        if self.busy or not self.campaign or not self._selected_project or not self.save_project():
            return
        from .project_evidence_dialog import ProjectEvidenceDialog
        dialog = ProjectEvidenceDialog(self.campaign, self._selected_project, self.prompts, self)
        try:
            dialog.exec()
            if dialog.changed:
                self.refresh()
                self.status.setText("Project evidence saved. Review a fresh proposal before running affected work.")
        finally:
            dialog.deleteLater()

    @Slot()
    def pick_template(self):
        selected = self.prompts.pick_file("Project template", self.template.text(), "Word documents (*.docx)")
        if selected:
            self.template.setText(selected)

    @Slot()
    def pick_spec(self):
        selected = self.prompts.pick_file("Project spec or chain", "", "JSON (*.json)")
        if not selected:
            return
        try:
            from ..editspec import load_spec, validate_spec
            from ..chain import load_chain_file
            raw = json.loads(Path(selected).read_text(encoding="utf-8"))
            if "stages" in raw:
                self._pending_chain, self._pending_spec = load_chain_file(Path(selected)), None
            else:
                spec = load_spec(Path(selected))
                validate_spec(spec)
                self._pending_spec, self._pending_chain = spec, None
            self.spec_label.setText(Path(selected).name)
            self.goal_mode.setCurrentIndex(0)
            self._mark_dirty()
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))

    @Slot()
    def clear_spec(self):
        self._pending_spec = self._pending_chain = None
        self.spec_label.setText("No saved spec attached")
        self._mark_dirty()

    @Slot()
    def create_workspace(self):
        if self._dirty and not self.save_project():
            return
        roots = []
        while True:
            selected = self.prompts.pick_dir("Add a project folder (subfolders included)", "")
            if not selected:
                break
            if selected not in roots:
                roots.append(selected)
            if not self.prompts.ask("Add folders", "Add another project folder to this workspace?"):
                break
        if not roots:
            return
        destination = self.prompts.pick_dir("Choose a new, empty workspace folder", str(self.session.out_root))
        if destination:
            from ..campaign import create_campaign
            self._selected_project = None
            self._work("Inventorying documents…", lambda log: create_campaign(roots, Path(destination), recursive=True),
                       adopt=True)

    @Slot()
    def open_workspace(self):
        if self._dirty and not self.save_project():
            return
        destination = self.prompts.pick_dir("Open workspace", str(self.session.out_root))
        if destination:
            from ..campaign import load_campaign
            self._work("Opening workspace…", lambda log: load_campaign(Path(destination)), adopt=True)

    @Slot()
    def assign_documents(self):
        if not self.campaign or not self.save_project():
            return
        name = self.project_id.currentText().strip()
        project_id = (self.project_id.currentData()
                      if name == self.project_id.itemText(self.project_id.currentIndex()) else name)
        rows = sorted({item.row() for item in self.documents.selectedItems()})
        if not rows or not project_id:
            self.status.setText("Select documents, then choose a project or type a new project name.")
            return
        doc_ids = [self.documents.item(row, 0).data(Qt.ItemDataRole.UserRole) for row in rows]
        try:
            self.campaign.set_project(doc_ids, project_id)
            self.review_ack.setChecked(False)
            self._selected_project = project_id
            self.refresh()
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))

    @Slot()
    def save_answer(self):
        text = self.answer.toPlainText().strip()
        if not text or not self.campaign or not self.save_project():
            return
        try:
            self.campaign.answer(self._selected_project, text, question_id=self.questions.currentData())
            self.review_ack.setChecked(False)
            self.refresh()
            self.status.setText("Answer saved. Generate a revised plan for planning questions; Run / resume for execution questions.")
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))

    def _options(self):
        from dataclasses import fields
        from ..recovery import RecoveryOptions
        snapshot = self.campaign.to_dict().get("resource_options") or {}
        values = {field.name: snapshot[field.name] for field in fields(RecoveryOptions) if field.name in snapshot}
        values.update(self.resources.values(), render=self.render.isChecked(),
                      allow_codegen=self.codegen.isChecked(), use_memory=self.memory.isChecked())
        return RecoveryOptions(**values)

    @Slot()
    def generate_plan(self):
        if not self.campaign or not self._selected_project:
            return
        request = self.request.toPlainText().strip()
        if not request:
            self.status.setText("Describe the outcome to investigate first.")
            return
        if not self.session.claude_ready():
            self.status.setText("Planning a described outcome needs an API key in Settings. Saved specs and title goals run without one.")
            return
        try:
            doc_ids = self._scope_documents()
            context = json.loads(self.context.toPlainText() or "{}")
            if not isinstance(context, dict):
                raise ValueError("Project context must be a JSON object.")
            project = self.campaign.projects[self._selected_project]
            if context != (project.get("context") or {}):
                self.campaign.set_project_context(self._selected_project, context)
            self.campaign.save_plan_draft(self._selected_project, request, doc_ids=doc_ids,
                                          template=self.template.text().strip())
            self._dirty = False
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))
            return
        campaign, project_id, client = self.campaign, self._selected_project, self.session.client
        options, template = self._options(), self.template.text().strip()
        self.review_ack.setChecked(False)
        self.tabs.setCurrentWidget(self.plan_page)
        self._work("Investigating the requested outcome and preparing a reviewable plan…",
                   lambda log: campaign.plan(project_id, request, doc_ids=doc_ids, template=template,
                                             options=options, client=client, log=log),
                   completion="Plan saved. Review all effects, exact wording, scope, evidence and risks before approval.",
                   show_plan=True)

    @Slot()
    def approve_plan(self):
        if not self.campaign or self._dirty:
            self.status.setText("Generate a new plan after changing the instructions or scope.")
            return
        project_id, plan_id = self._selected_project, self._reviewed_plan_id
        project = self.campaign.projects[project_id]
        proposed = project.get("proposed_plan") or {}
        if not self._draft_matches_plan(project, proposed):
            self.status.setText("Generate a revised plan for the saved instructions before approval.")
            return
        if not plan_id or proposed.get("id") != plan_id:
            self.status.setText("The displayed plan is no longer current. Review the latest proposal.")
            return
        campaign = self.campaign
        self.review_ack.setChecked(False)
        self._work("Checking the reviewed plan and its source versions…",
                   lambda log: campaign.approve_plan(project_id, plan_id),
                   completion="Plan approved. Run approved work to prepare verified copies.", show_plan=True)

    @Slot()
    def refresh_sources(self):
        if not self.campaign or not self.save_project():
            return
        selected = self._selected_documents()
        if not selected:
            self.status.setText("Select the repaired or changed source documents to refresh.")
            return
        campaign = self.campaign
        self.review_ack.setChecked(False)
        self._work("Reinspecting selected sources and their affected plans…",
                   lambda log: campaign.refresh(selected),
                   completion="Source versions refreshed. Review affected plans before continuing; other ready work is retained.")

    @Slot()
    def run_workspace(self):
        if not self.campaign or not self.save_project():
            return
        options = self._options()
        campaign, client = self.campaign, self.session.client
        allow_model = self.session.claude_ready()
        self.review_ack.setChecked(False)
        self.tabs.setCurrentWidget(self.evidence)
        self._work("Investigating; ready copies remain saved…",
                   lambda log: campaign.run(options=options, client=client, log=log, allow_model=allow_model))

    @Slot()
    def export_workspace(self):
        if not self.campaign or not self.save_project():
            return
        destination = self.prompts.pick_dir("Export ready copies", str(self.session.out_root))
        if destination:
            campaign, acknowledge = self.campaign, self.review_ack.isChecked()
            self._work("Rechecking and exporting ready copies…",
                       lambda log: campaign.export(Path(destination), acknowledge_review=acknowledge))

    @Slot()
    def open_folder(self):
        if self.campaign:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.campaign.out_dir)))

    @Slot(int, int)
    def open_document(self, row, column):
        doc_id = self.documents.item(row, 0).data(Qt.ItemDataRole.UserRole)
        doc = self.campaign.documents[doc_id]
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(doc.get("output") or doc["source"])))

    def _work(self, message, action, *, adopt=False, completion=None, show_plan=False):
        if self.busy:
            return False
        self.busy = True
        self._adopt_result = adopt
        self._completion = completion
        self._show_plan_after = show_plan
        if adopt:
            self.review_ack.setChecked(False)
        self.content.setEnabled(False)
        self.progress.show()
        self.status.setText(message)
        self.log.appendPlainText(message)
        if self.parent() is not None:
            self.parent().set_busy(True, message)
        start_job(self.bridge, action, name="spec-surgeon-workspace")
        return True

    @Slot(str)
    def _log(self, message):
        self.log.appendPlainText(message)
        self.status.setText(message)

    def _finish(self):
        self.busy = False
        self.content.setEnabled(True)
        self.progress.hide()
        if self.parent() is not None:
            self.parent().set_busy(False)

    @Slot(object)
    def _done(self, result):
        try:
            if self._adopt_result:
                self.session.campaign = result
                self._restore_options()
            self.refresh()
            if self._show_plan_after:
                self.tabs.setCurrentWidget(self.plan_page)
            completion = self._completion or "Workspace saved. Review ready copies and any remaining questions."
            if self._show_plan_after and self.campaign:
                proposal = self.campaign.projects.get(self._selected_project, {}).get("proposed_plan") or {}
                if proposal.get("status") == "NEEDS_INPUT":
                    completion = "Planning needs your answer. Use Instructions and questions, then investigate again to revise the plan."
                elif proposal.get("status") == "PAUSED":
                    completion = "Planning paused with its evidence saved. Check the log and allowance, then investigate again to continue."
                elif proposal.get("status") == "UNSUPPORTED":
                    completion = "This outcome needs a revised request or verification capability. Review the explanation before continuing."
            self.status.setText(completion)
        finally:
            self._finish()

    @Slot(str, str)
    def _failed(self, message, traceback):
        self._finish()
        self.log.appendPlainText(traceback)
        self.refresh()
        self.status.setText(message)

    def reject(self):
        if not self.busy and (not self._dirty or self.save_project()):
            super().reject()

    def closeEvent(self, event):
        if self.busy or (self._dirty and not self.save_project()):
            event.ignore()
        else:
            event.accept()
