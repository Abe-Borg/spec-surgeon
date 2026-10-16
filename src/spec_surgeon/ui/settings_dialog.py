"""Settings: the default results folder and the Anthropic API key — the two things the window needs told.

The look follows Windows (light/dark) and the update check runs once a day on its own, so neither is a setting
(the GUI rework after v0.4.1 removed the Appearance and Updates panels)."""
from __future__ import annotations

import os

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout

from .. import credentials
from ..settings import save_settings
from .widgets import Card, PathPicker, label, primary_button


class SettingsDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.w = window
        self.setWindowTitle("Settings — Spec Surgeon")
        self.setMinimumWidth(620)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(12)

        card = Card("Results folder", "Where previews and runs go unless you choose another folder on the Files step.")
        self.out = PathPicker("dir", browse=window.browse_dir)
        self.out.setText(window.files_page.out.text())
        card.add(self.out)
        lay.addWidget(card)

        card = Card("Anthropic API key", "Needed for typed requests (the conversation with Claude), AI-written "
                                          "transforms, the visual check and advice. Saved specs run without one.")
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("a key is set — type a new one to replace it" if os.environ.get(credentials.ENV)
                                    else "sk-ant-…")
        self.show_key = QCheckBox("Show")
        self.show_key.toggled.connect(self._toggle_show)
        row = QHBoxLayout()
        row.addWidget(self.key, 1)
        row.addWidget(self.show_key)
        card.add(row)
        self.remember = QCheckBox("Remember on this computer (Windows Credential Manager)")
        self.remember.setEnabled(credentials.available())
        self.remember.setChecked(credentials.available() and window.key_origin == "saved")
        if not credentials.available():
            self.remember.setToolTip("Windows Credential Manager is not available here; the key is kept for this "
                                     "session only")
        card.add(self.remember)
        row = QHBoxLayout()
        self.key_source = label(window.key_status_text(), "Muted")
        self.forget = QPushButton("Forget saved key")
        self.forget.setEnabled(credentials.available() and credentials.load() is not None)
        self.forget.clicked.connect(self._forget)
        row.addWidget(self.key_source, 1)
        row.addWidget(self.forget)
        card.add(row)
        lay.addWidget(card)

        lay.addWidget(label("The window follows Windows' light or dark mode, and checks for updates once a day on "
                            "its own (Help → Check for updates… checks right away).", "Muted"))

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = primary_button("Save")
        save.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(save)
        lay.addLayout(row)
        self.accepted.connect(self.apply)

    @Slot(bool)
    def _toggle_show(self, on: bool) -> None:
        self.key.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password)

    @Slot()
    def _forget(self) -> None:
        credentials.delete()
        if self.w.key_origin == "saved":
            os.environ.pop(credentials.ENV, None)
            self.w.key_origin = "none"
        self.remember.setChecked(False)
        self.forget.setEnabled(False)
        self.key_source.setText(self.w.key_status_text())
        self.w.refresh_key_label()

    @Slot()
    def apply(self) -> None:
        s = self.w.settings
        if self.out.text():
            self.w.files_page.out.setText(self.out.text())
            s.out_root = self.out.text()
        key = self.key.text().strip()
        if key:
            os.environ[credentials.ENV] = key
            self.w.key_origin = "session"
            if self.remember.isChecked() and credentials.available():
                try:
                    credentials.save(key)
                    self.w.key_origin = "saved"
                except Exception as e:  # shown, not swallowed
                    self.w.prompts.notify("Settings", f"Could not save the key in Credential Manager: {e}\n"
                                                      "It is kept for this session only.")
        elif self.remember.isChecked() and credentials.available() and os.environ.get(credentials.ENV) \
                and self.w.key_origin == "session":
            credentials.save(os.environ[credentials.ENV])
            self.w.key_origin = "saved"
        self.w.refresh_key_label()
        save_settings(s)
