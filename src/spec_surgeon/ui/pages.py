"""The four steps of the window: Files → Describe the change → Preview → Run & results.

Pages are layout only: they own their widgets and expose them; ``ui.app.MainWindow`` wires them to the engine
(``gui.Session``). Every step has the same shape — a header ("STEP n OF 4", title, one sentence, the step's
message strip), a scrolling body of cards, and an action bar at the foot with Back on the left and the one primary
button on the right, next to a hint that says what is still missing when that button is off.

Strings other code depends on: "reply in the chat", "Start over", "Load set-aside files", "Advise on
exceptions" (``Session`` messages name these controls).
Custom transforms in normal conversations are offered through choices; recovery has its own explicit opt-in.
"""
from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QListWidget, QPlainTextEdit, QProgressBar, QPushButton,
                               QRadioButton, QScrollArea, QTableView, QTableWidget, QVBoxLayout, QWidget)

from .chat import ChatView
from .models import ProblemsFilter, RowsModel
from .resource_controls import ResourceControls
from .rows import PREVIEW_COLUMNS, RESULT_COLUMNS
from .widgets import (BadgeDelegate, Card, ChipRow, DropZone, EmptyState, InfoBar, PathPicker, StatTile, eyebrow,
                      label, link_button, pill, primary_button, repolish)

STEP_COUNT = 4


class Page(QWidget):
    def __init__(self, number: int, title: str, subtitle: str, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QWidget()
        head.setObjectName("PageHeader")
        hl = QVBoxLayout(head)
        hl.setContentsMargins(36, 22, 36, 6)
        hl.setSpacing(3)
        self.eyebrow = eyebrow(f"Step {number} of {STEP_COUNT}")
        self.title = label(title, "PageTitle")
        self.subtitle = label(subtitle, "Muted")
        hl.addWidget(self.eyebrow)
        hl.addWidget(self.title)
        hl.addWidget(self.subtitle)
        self.info = InfoBar()
        hl.addSpacing(8)
        hl.addWidget(self.info)
        outer.addWidget(head)
        scroll = QScrollArea()
        self.scroll = scroll
        scroll.setObjectName("PageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("PageBody")
        self.content = QVBoxLayout(body)
        self.content.setContentsMargins(36, 10, 36, 20)
        self.content.setSpacing(14)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        foot = QWidget()
        foot.setObjectName("Footer")
        self.footer = QHBoxLayout(foot)
        self.footer.setContentsMargins(36, 12, 36, 12)
        self.footer.setSpacing(10)
        outer.addWidget(foot)

    def finish(self) -> None:
        self.content.addStretch(1)


def _table(model: RowsModel, proxy: ProblemsFilter) -> QTableView:
    proxy.setSourceModel(model)
    t = QTableView()
    t.setModel(proxy)
    t.setAlternatingRowColors(True)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setShowGrid(False)
    t.setWordWrap(False)
    t.verticalHeader().hide()
    t.verticalHeader().setDefaultSectionSize(34)
    t.horizontalHeader().setHighlightSections(False)
    t.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    t.setMinimumHeight(220)
    return t


def _table_hint(text: str) -> QLabel:
    """The line beside a table's filter: right-aligned, and wrapping rather than widening the page in a narrow
    window (one line at the usual size, two at the 900 px minimum)."""
    lab = label(text, "Muted")
    lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return lab


def _hint(text: str = "") -> QLabel:
    lab = label(text, "Muted", wrap=False)
    lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return lab


class FilesPage(Page):
    def __init__(self, browse_dir, browse_file, parent=None):
        super().__init__(1, "Choose the files",
                         "Pick the folder with the Word specs. Nothing in it changes until you run — and by default "
                         "not even then: edited copies go to the results folder.", parent)
        card = Card("Specs folder", "Every .docx in this folder (not its subfolders).")
        self.folder = PathPicker("dir", r"e.g. C:\Projects\…\Specifications", browse=browse_dir)
        self.drop = DropZone("Drop the specs folder here",
                             "Drag the folder (or any .docx in it) from Explorer, or browse for it.")
        self.drop.dropped.connect(self.folder.setText)
        self.drop.browse.connect(self.folder._on_browse)
        self.folder_hint = label("", "Hint")
        self.file_list = QListWidget()
        self.file_list.setMaximumHeight(170)
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.file_list.hide()
        card.add(self.folder)
        card.add(self.drop)
        card.add(self.folder_hint)
        card.add(self.file_list)
        self.content.addWidget(card)
        self.set_aside = InfoBar(closable=False)
        self.content.addWidget(self.set_aside)

        card = Card("Template spec (optional)",
                    "A spec from the architect (or a prepared header/footer template), for requests like \u201cmake "
                    "the header and footer match the template spec\u201d or \u201cmatch the font of the template "
                    "spec\u201d. Leave it empty for anything else.")
        self.template = PathPicker("file", "Optional — the .docx the specs should match",
                                   browse=browse_file)
        self.template_hint = label("", "Hint")
        card.add(self.template)
        card.add(self.template_hint)
        self.content.addWidget(card)

        card = Card("Results folder", "Previews, edited copies, verification records and reports go here — each "
                                      "preview gets its own time-stamped folder.")
        self.out = PathPicker("dir", "Where results go", browse=browse_dir)
        card.add(self.out)
        self.out_warning = label("", "Muted")
        self.out_warning.hide()
        card.add(self.out_warning)
        self.content.addWidget(card)
        self.finish()
        self.footer.addStretch(1)
        self.next_hint = _hint()
        self.footer.addWidget(self.next_hint)
        self.next_btn = primary_button("Next: describe the change  \u2192")
        self.next_btn.setEnabled(False)
        self.footer.addWidget(self.next_btn)

    def set_hint(self, widget: QLabel, text: str, state: str = "") -> None:
        widget.setText(text)
        widget.setProperty("state", state)
        repolish(widget)

    def set_ready(self, ok: bool, hint: str = "") -> None:
        """Next is enabled only once the folder holds Word files; the hint says what is missing."""
        self.next_btn.setEnabled(ok)
        self.next_btn.setToolTip("Continue to choose a change." if ok else hint)
        self.next_hint.setText("" if ok else hint)


class RequestPage(Page):
    # The step reads one of two ways (``set_claude``): with Claude (a key is there) the request box comes first; without
    # a key (keyless mode) the saved specs and Your library come first, and the request box gives way to a note saying
    # what a key would add — the draft typed earlier stays in the (hidden) box and comes back with a key.
    TITLES = {True: "Describe the change", False: "Choose the change"}
    SUBTITLES = {True: "Say it in plain English — Claude turns it into an edit spec, and the engine previews it on "
                       "every file before anything runs. No API key? Load a saved spec instead.",
                 False: "Use a saved spec or one from Your library — they preview, run and verify without an API key. "
                        "Describing a change in plain English needs one."}
    SAVED_TITLES = {True: "Or choose a workflow or saved spec", False: "Choose a workflow or saved spec"}
    REQUEST_TITLES = {True: "What should change?", False: "Describe it in plain English"}

    def __init__(self, parent=None):
        super().__init__(2, self.TITLES[True], self.SUBTITLES[True], parent)
        self.claude = True
        card = Card(self.REQUEST_TITLES[True])
        self.request_card = card
        self.request = QPlainTextEdit()
        self.request.setObjectName("Editor")
        self.request.setPlaceholderText("e.g.  change all fonts to Arial")
        self.request.setMinimumHeight(104)
        self.request.setMaximumHeight(140)
        card.add(self.request)
        self.try_lbl = label("Try one of these:", "Muted")
        card.add(self.try_lbl)
        self.chips = ChipRow()
        card.add(self.chips)
        self.keyless = QWidget()                  # keyless mode: in place of the box, what a key would add
        kl = QVBoxLayout(self.keyless)
        kl.setContentsMargins(0, 0, 0, 0)
        kl.setSpacing(8)
        self.keyless_lbl = label("", "Muted")
        kl.addWidget(self.keyless_lbl)
        krow = QHBoxLayout()
        self.keyless_pill = pill("Needs an API key", "warn")
        krow.addWidget(self.keyless_pill)
        self.keyless_key_btn = QPushButton("Set API key\u2026")
        self.keyless_key_btn.setToolTip("Settings: your Anthropic API key (it stays on this computer)")
        krow.addWidget(self.keyless_key_btn)
        krow.addStretch(1)
        kl.addLayout(krow)
        self.keyless.hide()
        card.add(self.keyless)
        self.content.addWidget(card)

        self.chat = ChatView()                    # Claude's reply and choices sit right under the request they are about
        self.content.addWidget(self.chat)

        card = Card(self.SAVED_TITLES[True], "Built-in workflows guide you through common style, numbering, table and "
                                             "proofing changes without an API key. An edit spec file (.json) also runs "
                                             "without a key: an example from the "
                                             "Start menu's Example edit specs, the spec.json saved in an earlier "
                                             "preview folder, or a recipe, chain or operation from Your library. "
                                             "Chain recipes… runs several recipes one after another.")
        self.saved_card = card
        self.workflows_btn = QPushButton("Built-in workflows\u2026")
        self.workflows_btn.setToolTip("Choose a common workflow, supply your settings, then preview and verify it")
        card.add(self.workflows_btn)
        row = QHBoxLayout()
        self.load_btn = QPushButton("Load saved spec\u2026")
        self.load_btn.setToolTip("Use an EditSpec (.json) — e.g. one from the Start menu's Example edit specs, or the "
                                 "spec.json saved in an earlier preview folder")
        row.addWidget(self.load_btn)
        self.library_btn = QPushButton("Your library\u2026")
        self.library_btn.setToolTip("Your saved recipes (specs) and your own operations (custom transforms that "
                                    "passed) — reuse them on another batch")
        row.addWidget(self.library_btn)
        self.chain_btn = QPushButton("Chain recipes\u2026")
        self.chain_btn.setToolTip("Run several of your recipes one after another — each stage a full verified run on "
                                  "the result of the stage before it (e.g. match the font, then the header/footer)")
        row.addWidget(self.chain_btn)
        row.addStretch(1)
        card.add(row)
        self.spec_bar = InfoBar(closable=False)
        card.add(self.spec_bar)
        self.content.addWidget(card)

        card = Card("Options")
        self.render_cb = QCheckBox("Render pages (Word or LibreOffice)")
        card.add(self.render_cb)
        card.add(label("Makes before/after page images for the preview and checks that page layout did not move. "
                       "Slower; needs Word or LibreOffice.", "Muted"))
        self.codegen_lbl = label("When no built-in operation can do the request, Claude says why and offers choices "
                                 "in the chat — including, when it can be done safely, a one-off transform it writes "
                                 "and tests on sample files first (edited copies only).", "Muted")
        card.add(self.codegen_lbl)
        key_row = QHBoxLayout()
        key_row.setSpacing(10)
        self.key_pill = pill("API key")
        self.key_label = label("", "Muted", wrap=False)
        self.key_btn = link_button("Set API key\u2026")
        key_row.addWidget(self.key_pill)
        key_row.addWidget(self.key_label)
        key_row.addWidget(self.key_btn)
        key_row.addStretch(1)
        card.add(key_row)
        self.content.addWidget(card)
        self.finish()

        self.back_btn = QPushButton("\u2190  Files")
        self.footer.addWidget(self.back_btn)
        self.footer.addStretch(1)
        self.preview_hint = _hint("Ctrl+Enter")
        self.footer.addWidget(self.preview_hint)
        self.preview_btn = primary_button("Preview")
        self.preview_btn.setToolTip("Plan every file and try the change on samples — nothing is written to your files")
        self.footer.addWidget(self.preview_btn)

    def set_claude(self, on: bool, features: List[str]) -> None:
        """With Claude (``on``) or keyless: the titles, which card comes first, and the request box or the note in its
        place (``features``: what a key adds, ``llm.CLAUDE_FEATURES``). The request box keeps its text either way."""
        self.claude = on
        self.title.setText(self.TITLES[on])
        self.subtitle.setText(self.SUBTITLES[on])
        self.saved_card.title.setText(self.SAVED_TITLES[on])
        self.request_card.title.setText(self.REQUEST_TITLES[on])
        for w in (self.request, self.try_lbl, self.chips, self.codegen_lbl):
            w.setVisible(on)
        self.keyless_lbl.setText("An Anthropic API key adds:\n" + "\n".join(f"\u2022 {f[:1].upper()}{f[1:]}"
                                                                           for f in features)
                                 + "\nEverything else — built-in workflows, saved specs, Your library, Preview, Run and its verification — "
                                   "works without one.")
        self.keyless.setVisible(not on)
        # keyless: the saved specs first — what this step is about without a key
        self.content.removeWidget(self.saved_card)
        self.content.insertWidget(0 if not on else self.content.indexOf(self.chat) + 1, self.saved_card)


class PreviewPage(Page):
    # the visual check's helper line: Claude looked (True), or the check was skipped — no key, nothing rendered (False)
    REVIEW_HELP = {True: "Advisory — not a verification. Claude looked at the before/after pages of the samples: it "
                         "can stop a run, never pass one. The engine's checks are above.",
                   False: "Advisory — not a verification, and Claude did not look this time. The engine's checks "
                          "are above."}

    def __init__(self, parent=None):
        super().__init__(3, "Preview", "What will change in every file, and the change tried on sample files. Nothing "
                                       "has been written to your files.", parent)
        self.empty = EmptyState("Nothing previewed yet",
                                "Describe the change (or load a saved spec) and press Preview. The plan for every "
                                "file and the verified sample files appear here.", "Go to Describe the change")
        self.content.addWidget(self.empty)
        self.body = QWidget()
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(14)
        self.request_lbl = label("", "CardTitle", selectable=True)
        self.summary_lbl = label("", "Muted", selectable=True)
        head = QHBoxLayout()                      # the action bar holds one primary button: this one sits up here
        head.setSpacing(12)
        head.addWidget(self.request_lbl, 1)
        self.save_btn = QPushButton("Save to library\u2026")
        self.save_btn.setToolTip("Save this spec as a recipe — or this custom transform as one of your operations — "
                                 "to reuse on other batches")
        head.addWidget(self.save_btn, 0, Qt.AlignmentFlag.AlignTop)
        bl.addLayout(head)
        bl.addWidget(self.summary_lbl)
        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self.t_files, self.t_planned = StatTile("files"), StatTile("plan cleanly")
        self.t_exceptions, self.t_patterns = StatTile("set aside (exceptions)"), StatTile("structure patterns")
        for t in (self.t_files, self.t_planned, self.t_exceptions, self.t_patterns):
            tiles.addWidget(t)
        self.tiles = QWidget()
        self.tiles.setLayout(tiles)
        bl.addWidget(self.tiles)
        self.notes = InfoBar(closable=False)
        bl.addWidget(self.notes)
        self.bindings_card = Card("Per-file fills", "Values are copied verbatim from the source location shown. "
                                  "Correct a value or its source, then preview the revised fills.")
        self.bindings_table = QTableWidget(0, 5, self.bindings_card)
        self.bindings_table.setHorizontalHeaderLabels(["File / slot", "Value", "Source part", "Paragraph", "Source digest"])
        self.bindings_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.bindings_table.setMinimumHeight(180)
        self.bindings_card.add(self.bindings_table)
        self.bindings_apply = QPushButton("Preview revised fills", self.bindings_card)
        self.bindings_card.add(self.bindings_apply)
        self.bindings_card.hide()
        bl.addWidget(self.bindings_card)

        self.table_card = Card("Every file")
        top = QHBoxLayout()
        self.only_problems = QCheckBox("Show exceptions only")
        top.addWidget(self.only_problems)
        top.addWidget(_table_hint("Hover a row for the engine's diagnosis; double-click to open the file or see why "
                                  "it was refused"), 1)
        self.table_card.add(top)
        self.model = RowsModel(PREVIEW_COLUMNS, self)      # the page owns them: they die with it, on the GUI thread
        self.proxy = ProblemsFilter(self)
        self.table = _table(self.model, self.proxy)
        self.table.setItemDelegateForColumn(2, BadgeDelegate(self.table))
        self.table_card.add(self.table)
        bl.addWidget(self.table_card)

        self.samples_card = Card("Sample files", "Applied and verified in memory — your files are untouched. "
                                                 "Click an image to zoom.")
        self.samples = QVBoxLayout()
        self.samples.setSpacing(12)
        self.samples_card.add(self.samples)
        bl.addWidget(self.samples_card)

        self.review_card = Card("Claude's visual check", self.REVIEW_HELP[True])
        self.review_lbl = label("", selectable=True)
        self.review_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.review_card.add(self.review_lbl)
        self.review_bar = InfoBar(closable=False)
        self.review_card.add(self.review_bar)
        ack_row = QHBoxLayout()
        ack_row.addStretch(1)
        self.review_ack_btn = QPushButton("I\u2019ve looked \u2014 allow Run")
        self.review_ack_btn.setToolTip("You have looked at Claude's findings for this preview; Run is allowed for it")
        ack_row.addWidget(self.review_ack_btn)
        self.review_card.add(ack_row)
        self.review_card.hide()
        bl.addWidget(self.review_card)

        self.generated_card = Card("AI-written transform")
        self.generated_lbl = label("", selectable=True)
        self.generated_card.add(self.generated_lbl)
        self.generated_card.hide()
        bl.addWidget(self.generated_card)
        self.token_lbl = label("", "Muted", selectable=True)
        bl.addWidget(self.token_lbl)
        self.body.hide()
        self.content.addWidget(self.body)
        self.chat = ChatView(compact=True)
        self.content.addWidget(self.chat)
        self.finish()

        self.back_btn = QPushButton("\u2190  Change request")
        self.open_btn = QPushButton("Open preview folder")
        self.footer.addWidget(self.back_btn)
        self.footer.addWidget(self.open_btn)
        self.footer.addStretch(1)
        self.continue_hint = _hint()
        self.footer.addWidget(self.continue_hint)
        self.continue_btn = primary_button("Continue to Run  \u2192")
        self.footer.addWidget(self.continue_btn)

    def clear_samples(self) -> None:
        while self.samples.count():
            item = self.samples.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()


class RunPage(Page):
    def __init__(self, parent=None):
        super().__init__(4, "Run & results", "Apply the previewed change to every file; each file is verified on its "
                                             "own and only files that pass every check are delivered.", parent)
        card = Card("Where should the results go?")
        self.copies_rb = QRadioButton("Save edited copies to the results folder (your files are not changed)")
        self.inplace_rb = QRadioButton("Write verified results back over the originals (guarded)")
        self.copies_rb.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.copies_rb)
        group.addButton(self.inplace_rb)
        card.add(self.copies_rb)
        card.add(self.inplace_rb)
        card.add(label("Guarded: only files that pass every check are written, never a file open in Word or changed "
                       "since the preview. You will be asked to confirm.", "Muted"))
        self.content.addWidget(card)

        self.progress_box = QWidget()
        pl = QVBoxLayout(self.progress_box)
        pl.setContentsMargins(2, 0, 2, 0)
        self.progress_lbl = label("", "Muted")
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        pl.addWidget(self.progress_lbl)
        pl.addWidget(self.progress)
        self.progress_box.hide()
        self.content.addWidget(self.progress_box)

        self.empty = EmptyState("No results yet",
                                "Press Run to apply the previewed change to every file. Each file is verified on its "
                                "own; the results and every verification record land here.")
        self.content.addWidget(self.empty)

        self.results = QWidget()
        rl = QVBoxLayout(self.results)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(14)
        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self.t_pass, self.t_fail, self.t_exc = StatTile("passed"), StatTile("failed verification"), \
            StatTile("set aside (exceptions)")
        for t in (self.t_pass, self.t_fail, self.t_exc):
            tiles.addWidget(t)
        rl.addLayout(tiles)
        self.summary = InfoBar(closable=False)
        rl.addWidget(self.summary)
        card = Card("Every file")
        top = QHBoxLayout()
        self.only_problems = QCheckBox("Show problems only")
        top.addWidget(self.only_problems)
        top.addWidget(_table_hint("Double-click a file to open its edited copy — or to see why it did not pass"), 1)
        card.add(top)
        self.model = RowsModel(RESULT_COLUMNS, self)       # the page owns them: they die with it, on the GUI thread
        self.proxy = ProblemsFilter(self)
        self.table = _table(self.model, self.proxy)
        self.table.setItemDelegateForColumn(1, BadgeDelegate(self.table))
        card.add(self.table)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        self.open_btn = QPushButton("Open results folder")
        self.report_btn = QPushButton("View report")
        self.diag_btn = QPushButton("View diagnostics")
        self.diag_btn.setToolTip("Why each file did not pass — where it stopped, the specifics, its trace, what to do "
                                 "(DIAGNOSTICS.md)")
        self.failed_btn = QPushButton("Open failed/ folder")
        self.set_aside_btn = QPushButton("Load set-aside files")
        self.set_aside_btn.setToolTip("Run the next pass on only the files that were set aside or failed")
        self.advise_btn = QPushButton("Advise on exceptions")
        self.advise_btn.setToolTip("Claude writes a recommendation or a corrected spec per set-aside file (advice/)")
        for i, b in enumerate((self.open_btn, self.report_btn, self.diag_btn, self.failed_btn)):
            grid.addWidget(b, 0, i)
        grid.addWidget(self.set_aside_btn, 0, 5)
        grid.addWidget(self.advise_btn, 0, 6)
        grid.setColumnStretch(4, 1)
        card.add(grid)
        rl.addWidget(card)
        self.results.hide()
        self.content.addWidget(self.results)

        recovery = Card("Resolve remaining files", "Investigate failed or set-aside files on working copies, then "
                        "review the result before exporting. Verification checks preservation; inspect whether the "
                        "result meets your request.")
        self.recovery_resources = ResourceControls(self)
        self.recovery_budget = self.recovery_resources.cost
        self.recovery_attempts = self.recovery_resources.attempts
        self.recovery_calls = self.recovery_resources.calls
        recovery.add(self.recovery_resources)
        self.recovery_question = QComboBox(self)
        self.recovery_question.addItem("Clarification for the current investigation", None)
        self.recovery_question.setAccessibleName("Question to answer")
        recovery.add(self.recovery_question)
        self.recovery_request = QPlainTextEdit(self)
        self.recovery_request.setPlaceholderText("Optional clarification or answer for recovery (the original request is used if empty)")
        self.recovery_request.setMaximumHeight(80)
        recovery.add(self.recovery_request)
        self.recovery_codegen = QCheckBox("Allow custom repair methods (sandboxed and independently verified)")
        recovery.add(self.recovery_codegen)
        self.recovery_memory = QCheckBox("Remember repair outcomes locally and consult past cases")
        self.recovery_memory.setChecked(True)
        recovery.add(self.recovery_memory)
        self.resolve_btn = QPushButton("Resolve remaining files")
        self.resolve_btn.setEnabled(False)
        recovery.add(self.resolve_btn)
        self.load_recovery_btn = QPushButton("Open saved recovery workspace…")
        recovery.add(self.load_recovery_btn)
        self.content.addWidget(recovery)
        self.finish()

        self.back_btn = QPushButton("\u2190  Preview")
        self.footer.addWidget(self.back_btn)
        self.footer.addStretch(1)
        self.run_hint = _hint()
        self.footer.addWidget(self.run_hint)
        self.run_btn = primary_button("Run")
        self.footer.addWidget(self.run_btn)


def resize_columns(table: QTableView, stretch: int, widths: List[int]) -> None:
    h = table.horizontalHeader()
    for i, w in enumerate(widths):
        if i == stretch:
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
        else:
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            table.setColumnWidth(i, w)
