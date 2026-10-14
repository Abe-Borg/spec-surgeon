"""Settings: appearance, default results folder, the Anthropic API key, and updates."""
from __future__ import annotations

import os
import time

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout)

from .. import credentials
from ..settings import save_settings
from .widgets import Card, PathPicker, label, primary_button

THEMES = [("Follow Windows", "system"), ("Light", "light"), ("Dark", "dark")]


class SettingsDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.w = window
        self.setWindowTitle("Settings — Spec Surgeon")
        self.setMinimumWidth(620)
        s = window.settings
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(12)

        card = Card("Appearance")
        self.theme = QComboBox()
        for text, value in THEMES:
            self.theme.addItem(text, value)
        values = [v for _, v in THEMES]
        self.theme.setCurrentIndex(values.index(s.theme) if s.theme in values else 0)
        self.native = QCheckBox("Use Windows' own controls (Windows 11)")
        self.native.setChecked(s.native_style)
        row = QHBoxLayout()
        row.addWidget(label("Colours", wrap=False))
        row.addWidget(self.theme)
        row.addStretch(1)
        card.add(row)
        card.add(self.native)
        lay.addWidget(card)

        card = Card("Results", "Where previews and runs go unless you choose another folder on the Files step.")
        self.out = PathPicker("dir", browse=window.browse_dir)
        self.out.setText(window.files_page.out.text())
        card.add(self.out)
        lay.addWidget(card)

        card = Card("Anthropic API key", "Needed for typed requests, AI-written transforms and advice. Saved specs "
                                          "run without one.")
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

        card = Card("Updates")
        self.auto = QCheckBox("Check for updates automatically (once a day)")
        self.auto.setChecked(window.auto_check_enabled())
        card.add(self.auto)
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(s.last_update_check)) if s.last_update_check \
            else "never"
        card.add(label(f"Last automatic check: {when}", "Muted"))
        row = QHBoxLayout()
        self.skipped_lbl = label(f"Skipped version: {s.skipped_version}" if s.skipped_version else
                                 "No version skipped", "Muted")
        self.clear_skip = QPushButton("Clear")
        self.clear_skip.setEnabled(bool(s.skipped_version))
        self.clear_skip.clicked.connect(self._clear_skip)
        self.check_now = QPushButton("Check now")
        self.check_now.clicked.connect(self._check_now)
        row.addWidget(self.skipped_lbl, 1)
        row.addWidget(self.clear_skip)
        row.addWidget(self.check_now)
        card.add(row)
        lay.addWidget(card)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = primary_button("Save")
        save.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(save)
        lay.addLayout(row)
        self._skip_cleared = False
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
    def _clear_skip(self) -> None:
        self._skip_cleared = True
        self.skipped_lbl.setText("No version skipped")
        self.clear_skip.setEnabled(False)

    @Slot()
    def _check_now(self) -> None:
        self.w.updates.manual_check()

    @Slot()
    def apply(self) -> None:
        s = self.w.settings
        s.theme = self.theme.currentData() or "system"
        s.native_style = self.native.isChecked()
        s.auto_check_updates = self.auto.isChecked()
        if self._skip_cleared:
            s.skipped_version = ""
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
        self.w.apply_theme()
        self.w.refresh_key_label()
        save_settings(s)
