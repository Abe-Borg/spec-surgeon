"""Help → About: who made Spec Surgeon, how to reach him, the license in plain words (with the full text a click
away), the open-source components, and where this copy keeps its files. Every value comes from
``spec_surgeon.about``; nothing here is typed twice. The body is rich text in a scrolling view, so it never clips
on a small screen."""
from __future__ import annotations

import html
import sys
from pathlib import Path
from typing import List

from PySide6.QtCore import Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from .. import __version__, about
from ..settings import config_dir, data_dir
from . import theme
from .widgets import TextDialog, label


def install_dir() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).parents[1]


def details_lines() -> List[str]:
    from PySide6 import __version__ as pyside_version
    return [f"Qt for Python {pyside_version}, Python {sys.version.split()[0]}", f"Installed in: {install_dir()}",
            f"Settings: {config_dir()}", f"Downloads and logs: {data_dir()}"]


def about_text() -> str:
    """Everything the box shows, as text (the Copy button; tests)."""
    return "\n".join(about.about_lines(__version__) + ["", "This copy:"] + [f"  {x}" for x in details_lines()])


def about_html() -> str:
    t = theme.current or theme.tokens(False, theme.FALLBACK_ACCENT)
    e = html.escape

    def section(title: str) -> str:
        return (f'<p style="margin-top:14px; margin-bottom:4px; font-size:8pt; font-weight:600; color:{t.muted};">'
                f'{e(title.upper())}</p>')

    def link(text: str, url: str) -> str:
        return f'<a href="{e(url)}" style="color:{t.accent};">{e(text)}</a>'

    components = "".join(
        f'<tr><td style="padding-right:18px;">{link(name, url)}</td><td style="color:{t.muted};">{e(lic)}</td></tr>'
        for name, lic, url in about.THIRD_PARTY)
    summary = "".join(f"<li>{e(line)}</li>" for line in about.LICENSE_SUMMARY)
    details = "<br>".join(e(x) for x in details_lines())
    return f"""<html><body style="color:{t.text};">
{section("Made by")}
<p style="margin:0;">{e(about.COPYRIGHT_LINE)}. {e(about.AUTHOR)} is a fire sprinkler and mechanical designer who
builds software for specification work.</p>
<p style="margin:6px 0 0 0;">{link(about.EMAIL, "mailto:" + about.EMAIL)} &nbsp;&middot;&nbsp;
{link("GitHub", about.GITHUB_URL)} &nbsp;&middot;&nbsp; {link("LinkedIn", about.LINKEDIN_URL)}</p>
<p style="margin:4px 0 0 0; color:{t.muted};">Questions and ideas are welcome by email.</p>
{section("License")}
<p style="margin:0;"><b>{e(about.LICENSE_NAME)}</b> &mdash; in short:</p>
<ul style="margin-top:4px; margin-bottom:0;">{summary}</ul>
{section("Open-source components")}
<table cellspacing="0" cellpadding="1">{components}</table>
{section("This copy")}
<p style="margin:0; color:{t.muted};">{details}</p>
</body></html>"""


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("About")
        self.setWindowTitle(f"About {about.APP_NAME}")
        self.setMinimumWidth(620)
        self.resize(620, 660)
        self._viewers: List[QWidget] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(16)
        self.icon = QLabel()
        head.addWidget(self.icon, 0)
        name = QVBoxLayout()
        name.setSpacing(2)
        self.name = label(about.APP_NAME, "AboutName", wrap=False)
        self.version = label(f"Version {__version__}", "Muted", wrap=False)
        self.tagline = label(about.TAGLINE)
        name.addWidget(self.name)
        name.addWidget(self.version)
        name.addWidget(self.tagline)
        head.addLayout(name, 1)
        lay.addLayout(head)

        self.html = about_html()
        self.body = QTextBrowser()
        self.body.setObjectName("AboutBody")
        self.body.setOpenLinks(False)                     # mailto: and https: alike go to the system handler
        self.body.anchorClicked.connect(self._open_link)
        self.body.setHtml(self.html)
        lay.addWidget(self.body, 1)

        buttons = QHBoxLayout()
        self.license_btn = QPushButton("Read the full license")
        self.license_btn.clicked.connect(self.show_license)
        self.copy_btn = QPushButton("Copy details")
        self.copy_btn.setToolTip("Copy everything above as text (for an email)")
        self.copy_btn.clicked.connect(self.copy_details)
        buttons.addWidget(self.license_btn)
        buttons.addWidget(self.copy_btn)
        buttons.addStretch(1)
        self.close_btn = QPushButton("Close")
        self.close_btn.setDefault(True)
        self.close_btn.clicked.connect(self.accept)
        buttons.addWidget(self.close_btn)
        lay.addLayout(buttons)

    def setWindowIcon(self, icon) -> None:
        super().setWindowIcon(icon)
        if not icon.isNull():
            self.icon.setPixmap(icon.pixmap(56, 56))

    def text(self) -> str:
        return about_text()

    @Slot()
    def _open_link(self, url) -> None:
        QDesktopServices.openUrl(url)

    @Slot()
    def copy_details(self) -> None:
        QApplication.clipboard().setText(self.text())
        self.copy_btn.setText("Copied")

    @Slot()
    def show_license(self) -> None:
        dlg = TextDialog(about.LICENSE_NAME, about.LICENSE_TEXT, self)
        self._viewers.append(dlg)
        dlg.show()
