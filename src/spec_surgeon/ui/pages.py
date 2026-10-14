"""The four steps of the window: Files → Describe the change → Preview → Run & results.

Pages are layout only: they own their widgets and expose them; ``ui.app.MainWindow`` wires them to the engine
(``gui.Session``). Strings other code depends on: "Answers", "Allow AI-written transforms", "Load set-aside files",
"Advise on exceptions" (``Session`` messages name these controls).
"""
from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QListWidget, QPlainTextEdit, QProgressBar, QPushButton,
                               QRadioButton, QScrollArea, QTableView, QVBoxLayout, QWidget)

from .models import ProblemsFilter, RowsModel
from .rows import PREVIEW_COLUMNS, RESULT_COLUMNS
from .widgets import (BadgeDelegate, Card, ChipRow, InfoBar, PathPicker, StatTile, label, link_button,
                      primary_button, repolish)


class Page(QWidget):
    def __init__(self, title: str, subtitle: str, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QWidget()
        head.setObjectName("Content")
        hl = QVBoxLayout(head)
        hl.setContentsMargins(32, 24, 32, 8)
        hl.setSpacing(2)
        self.title = label(title, "PageTitle")
        self.subtitle = label(subtitle, "Muted")
        hl.addWidget(self.title)
        hl.addWidget(self.subtitle)
        self.info = InfoBar()
        hl.addSpacing(8)
        hl.addWidget(self.info)
        outer.addWidget(head)
        scroll = QScrollArea()
        scroll.setObjectName("PageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("PageBody")
        self.content = QVBoxLayout(body)
        self.content.setContentsMargins(32, 8, 32, 16)
        self.content.setSpacing(14)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        foot = QWidget()
        foot.setObjectName("Content")
        self.footer = QHBoxLayout(foot)
        self.footer.setContentsMargins(32, 10, 32, 16)
        self.footer.setSpacing(8)
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


class FilesPage(Page):
    def __init__(self, browse_dir, browse_file, parent=None):
        super().__init__("Choose the files",
                         "Pick the folder with the Word specs. Nothing in it changes until you run — and by default "
                         "not even then: edited copies go to the results folder.", parent)
        card = Card("Specs folder", "Every .docx in this folder (not its subfolders). Drop a folder here or browse.")
        self.folder = PathPicker("dir", r"e.g. C:\Projects\…\Specifications", browse=browse_dir)
        self.folder_hint = label("", "Hint")
        self.file_list = QListWidget()
        self.file_list.setMaximumHeight(170)
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.file_list.hide()
        card.add(self.folder)
        card.add(self.folder_hint)
        card.add(self.file_list)
        self.content.addWidget(card)
        self.set_aside = InfoBar(closable=False)
        self.content.addWidget(self.set_aside)

        card = Card("Header/footer template (optional)",
                    "Only for requests like \u201cmake the header/footer look like this template\u201d.")
        self.template = PathPicker("file", "Optional — a .docx whose header and footer the specs should match",
                                   browse=browse_file)
        self.template_hint = label("", "Hint")
        card.add(self.template)
        card.add(self.template_hint)
        self.content.addWidget(card)

        card = Card("Results folder", "Previews, edited copies, verification records and reports go here — each "
                                      "preview gets its own time-stamped folder.")
        self.out = PathPicker("dir", "Where results go", browse=browse_dir)
        card.add(self.out)
        self.content.addWidget(card)
        self.finish()
        self.footer.addStretch(1)
        self.next_btn = primary_button("Next: describe the change  \u2192")
        self.footer.addWidget(self.next_btn)

    def set_hint(self, widget: QLabel, text: str, state: str = "") -> None:
        widget.setText(text)
        widget.setProperty("state", state)
        repolish(widget)


class RequestPage(Page):
    def __init__(self, parent=None):
        super().__init__("Describe the change",
                         "Say it in plain English — Claude turns it into an edit spec, and the engine previews it on "
                         "every file before anything runs. Or load a saved spec (no API key needed).", parent)
        card = Card("What should change?")
        self.request = QPlainTextEdit()
        self.request.setObjectName("Editor")
        self.request.setPlaceholderText("e.g.  change all fonts to Arial")
        self.request.setMinimumHeight(96)
        self.request.setMaximumHeight(120)
        card.add(self.request)
        card.add(label("Examples — click one to use it:", "Muted"))
        self.chips = ChipRow()
        card.add(self.chips)
        row = QHBoxLayout()
        self.load_btn = QPushButton("Load saved spec\u2026")
        self.load_btn.setToolTip("Use an EditSpec (.json) — e.g. one from the Start menu's Example edit specs, or the "
                                 "spec.json saved in an earlier preview folder")
        row.addWidget(self.load_btn)
        row.addStretch(1)
        card.add(row)
        self.content.addWidget(card)
        self.spec_bar = InfoBar(closable=False)
        self.content.addWidget(self.spec_bar)

        self.answers_card = Card("Claude needs a few answers",
                                 "Type one answer per line, in the order of the questions, then press Preview again.")
        self.questions = label("", selectable=True)
        self.answers_label = label("Answers (one per line)")
        self.answers = QPlainTextEdit()
        self.answers.setObjectName("Editor")
        self.answers.setMinimumHeight(70)
        self.answers.setMaximumHeight(130)
        self.answers_card.add(self.questions)
        self.answers_card.add(self.answers_label)
        self.answers_card.add(self.answers)
        self.answers_card.hide()
        self.content.addWidget(self.answers_card)

        card = Card("Options")
        self.render_cb = QCheckBox("Render pages (Word or LibreOffice)")
        card.add(self.render_cb)
        card.add(label("Makes before/after page images for the preview and checks that page layout did not move. "
                       "Slower; needs Word or LibreOffice.", "Muted"))
        self.codegen_cb = QCheckBox("Allow AI-written transforms")
        card.add(self.codegen_cb)
        card.add(label("Off by default. When no built-in operation can do the request, Claude may write a one-off "
                       "transform. It is tested on sample files first and only ever writes edited copies.", "Muted"))
        self.content.addWidget(card)

        key_row = QHBoxLayout()
        self.key_label = label("", "Muted", wrap=False)
        self.key_btn = link_button("Set API key\u2026")
        key_row.addWidget(self.key_label)
        key_row.addWidget(self.key_btn)
        key_row.addStretch(1)
        self.content.addLayout(key_row)
        self.finish()

        self.back_btn = QPushButton("\u2190  Files")
        self.footer.addWidget(self.back_btn)
        self.footer.addStretch(1)
        self.footer.addWidget(label("Ctrl+Enter", "Muted", wrap=False))
        self.preview_btn = primary_button("Preview")
        self.preview_btn.setToolTip("Plan every file and try the change on samples — nothing is written to your files")
        self.footer.addWidget(self.preview_btn)


class PreviewPage(Page):
    def __init__(self, parent=None):
        super().__init__("Preview", "What will change in every file, and the change tried on sample files. Nothing "
                                    "has been written to your files.", parent)
        self.empty = label("Nothing previewed yet. Describe the change and press Preview.", "Muted")
        self.content.addWidget(self.empty)
        self.body = QWidget()
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(14)
        self.request_lbl = label("", "CardTitle", selectable=True)
        self.summary_lbl = label("", "Muted", selectable=True)
        bl.addWidget(self.request_lbl)
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

        self.table_card = Card("Every file")
        top = QHBoxLayout()
        self.only_problems = QCheckBox("Show exceptions only")
        top.addWidget(self.only_problems)
        top.addStretch(1)
        self.table_card.add(top)
        self.model = RowsModel(PREVIEW_COLUMNS)
        self.proxy = ProblemsFilter()
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

        self.generated_card = Card("AI-written transform")
        self.generated_lbl = label("", selectable=True)
        self.generated_card.add(self.generated_lbl)
        self.generated_card.hide()
        bl.addWidget(self.generated_card)
        self.token_lbl = label("", "Muted", selectable=True)
        bl.addWidget(self.token_lbl)
        self.body.hide()
        self.content.addWidget(self.body)
        self.finish()

        self.back_btn = QPushButton("\u2190  Change request")
        self.open_btn = QPushButton("Open preview folder")
        self.footer.addWidget(self.back_btn)
        self.footer.addWidget(self.open_btn)
        self.footer.addStretch(1)
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
        super().__init__("Run & results", "Apply the previewed change to every file; each file is verified on its "
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
        top.addStretch(1)
        top.addWidget(label("Double-click a file to open its edited copy or verification record", "Muted",
                            wrap=False))
        card.add(top)
        self.model = RowsModel(RESULT_COLUMNS)
        self.proxy = ProblemsFilter()
        self.table = _table(self.model, self.proxy)
        self.table.setItemDelegateForColumn(1, BadgeDelegate(self.table))
        card.add(self.table)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        self.open_btn = QPushButton("Open results folder")
        self.report_btn = QPushButton("View report")
        self.failed_btn = QPushButton("Open failed/ folder")
        self.set_aside_btn = QPushButton("Load set-aside files")
        self.set_aside_btn.setToolTip("Run the next pass on only the files that were set aside or failed")
        self.advise_btn = QPushButton("Advise on exceptions")
        self.advise_btn.setToolTip("Claude writes a recommendation or a corrected spec per set-aside file (advice/)")
        for i, b in enumerate((self.open_btn, self.report_btn, self.failed_btn)):
            grid.addWidget(b, 0, i)
        grid.addWidget(self.set_aside_btn, 0, 4)
        grid.addWidget(self.advise_btn, 0, 5)
        grid.setColumnStretch(3, 1)
        card.add(grid)
        rl.addWidget(card)
        self.results.hide()
        self.content.addWidget(self.results)
        self.finish()

        self.back_btn = QPushButton("\u2190  Preview")
        self.footer.addWidget(self.back_btn)
        self.footer.addStretch(1)
        self.run_hint = label("", "Muted", wrap=False)
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
