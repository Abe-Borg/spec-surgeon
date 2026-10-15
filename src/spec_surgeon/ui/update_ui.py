"""The window's side of updates: the banner, the once-a-day check, Help → Check for updates, and the one-click
install (download → verify → start Setup → quit; Setup reopens the app). All decisions about what may be
downloaded or started live in ``spec_surgeon.updater``; this module only drives it and shows the outcome."""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFrame, QHBoxLayout, QProgressBar, QPushButton, QTextBrowser, QVBoxLayout

from .. import updater, winapp
from ..settings import save_settings, updates_dir
from .widgets import label, primary_button


def _mb(n: Optional[int]) -> str:
    return "?" if n is None else f"{n / 2 ** 20:.0f}"


class UpdateBanner(QFrame):
    """One strip above the page: available / downloading / error / updated / didn't finish."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Banner")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 12, 12)
        lay.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(8)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.title = label("", "CardTitle")
        self.text = label("", "Muted")
        text.addWidget(self.title)
        text.addWidget(self.text)
        top.addLayout(text, 1)
        self.btn_notes = QPushButton("What's new")
        self.btn_notes.setCheckable(True)
        self.btn_install = primary_button("Install now")
        self.btn_install.setToolTip("Download, check and install the update; Spec Surgeon closes and reopens")
        self.btn_later = QPushButton("Later")
        self.btn_skip = QPushButton("Skip this version")
        self.btn_cancel = QPushButton("Cancel")
        self.btn_retry = QPushButton("Try again")
        self.btn_releases = QPushButton("Release page")
        self.btn_log = QPushButton("Open install log")
        self.btn_dismiss = QPushButton("Dismiss")
        self.buttons = (self.btn_notes, self.btn_install, self.btn_later, self.btn_skip, self.btn_cancel,
                        self.btn_retry, self.btn_releases, self.btn_log, self.btn_dismiss)
        for b in self.buttons:
            top.addWidget(b, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(top)
        self.busy_note = label("You can install once the current Preview or Run finishes.", "Muted")
        self.busy_note.hide()
        lay.addWidget(self.busy_note)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.hide()
        lay.addWidget(self.progress)
        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(True)
        self.notes.setMaximumHeight(240)
        self.notes.hide()
        lay.addWidget(self.notes)
        self.btn_notes.toggled.connect(self.notes.setVisible)
        self.mode = ""
        self.hide()

    def _show(self, mode: str, title: str, text: str, buttons) -> None:
        self.mode = mode
        self.title.setText(title)
        self.text.setText(text)
        self.text.setVisible(bool(text))
        for b in self.buttons:
            b.setVisible(b in buttons)
        self.progress.setVisible(mode == "downloading")
        if self.btn_notes not in buttons:
            self.btn_notes.setChecked(False)
        self.show()

    def set_notes(self, markdown: str) -> None:
        self.notes.setMarkdown(markdown or "_No release notes._")


class UpdateController(QObject):
    """Checks, the banner's buttons, and the install hand-off. ``checker``, ``downloader``, ``launcher`` and
    ``installed`` are injectable (tests); by default they are ``updater``'s functions."""
    checked = Signal(object, bool)
    check_failed = Signal(object, bool)
    dl_progress = Signal(object, object)
    dl_done = Signal(object)
    dl_failed = Signal(object)

    def __init__(self, window, checker: Optional[Callable] = None, downloader: Optional[Callable] = None,
                 launcher: Optional[Callable] = None, installed: Optional[Callable[[], bool]] = None):
        super().__init__(window)
        self.w = window
        self.banner = UpdateBanner()
        self.checker = checker or (lambda manual, skipped: updater.check_for_update(skipped=skipped, manual=manual))
        self.downloader = downloader or (lambda info, dest, progress, cancel: updater.download_installer(
            info, dest, progress=progress, cancel=cancel))
        self.launcher = launcher or (lambda dl, install_dir, log: updater.launch_installer(
            dl, install_dir=install_dir, log=log))
        self.installed = installed or updater.is_installed_build
        self.info: Optional[updater.UpdateInfo] = None
        self.checking = False
        self.downloading = False
        self.cancel: Optional[threading.Event] = None
        self.last_message = ""
        self.checked.connect(self._on_checked)
        self.check_failed.connect(self._on_check_failed)
        self.dl_progress.connect(self._on_dl_progress)
        self.dl_done.connect(self._on_dl_done)
        self.dl_failed.connect(self._on_dl_failed)
        b = self.banner
        b.btn_install.clicked.connect(self.install_now)
        b.btn_later.clicked.connect(self.later)
        b.btn_skip.clicked.connect(self.skip)
        b.btn_cancel.clicked.connect(self.cancel_download)
        b.btn_retry.clicked.connect(self.manual_check)
        b.btn_releases.clicked.connect(self.open_releases)
        b.btn_log.clicked.connect(self.open_log)
        b.btn_dismiss.clicked.connect(self.banner.hide)

    # ---- state shared with the window ----
    @property
    def settings(self):
        return self.w.settings

    def _save(self) -> None:
        try:
            save_settings(self.settings)
        except OSError as e:
            self.w.write(f"Could not save settings: {e}")

    @Slot(bool)
    def on_busy(self, busy: bool) -> None:
        self.banner.btn_install.setEnabled(not busy and not self.downloading)
        self.banner.busy_note.setVisible(busy and self.banner.mode == "available")

    # ---- start-up ----
    def startup(self) -> None:
        """After an update: say it worked (or didn't); tidy old downloads."""
        s, cur = self.settings, updater.current_version()
        pending = s.pending_update or {}
        if pending.get("version"):
            if updater.is_newer(pending["version"], cur):
                self._pending_log = pending.get("log", "")
                self.banner._show("failed", f"The update to {pending['version']} didn't finish",
                                  f"You are still on {cur}. The install log says why.",
                                  (self.banner.btn_retry, self.banner.btn_log, self.banner.btn_dismiss))
            else:
                self._show_updated(cur)
            s.pending_update = {}
        elif s.last_run_version and updater.is_newer(cur, s.last_run_version):
            self._show_updated(cur)
        s.last_run_version = cur
        self._save()
        try:
            updater.cleanup_downloads(updates_dir())
        except OSError:
            pass

    def _show_updated(self, cur: str) -> None:
        import os
        notes = self.w.release_notes_text()
        self.banner.set_notes(notes or "")
        hint = "" if os.environ.get("ANTHROPIC_API_KEY") else \
            "If you typed your Anthropic API key for one session only, enter it again in Settings."
        buttons = (self.banner.btn_notes, self.banner.btn_dismiss) if notes else (self.banner.btn_dismiss,)
        self.banner._show("updated", f"Spec Surgeon was updated to {cur}", hint, buttons)

    # ---- checks ----
    @Slot()
    def auto_check(self) -> bool:
        if not updater.should_auto_check(self.settings):
            return False
        self.settings.last_update_check = time.time()
        self._save()
        return self._check(manual=False)

    @Slot()
    def manual_check(self) -> bool:
        return self._check(manual=True)

    def _check(self, manual: bool) -> bool:
        if self.checking or self.downloading:
            return False
        self.checking = True
        self.w.set_checking(True)
        skipped = self.settings.skipped_version

        def target():
            try:
                self.checked.emit(self.checker(manual, skipped), manual)
            except updater.UpdateError as e:
                self.check_failed.emit(e, manual)
            except Exception as e:  # never crash the window over an update check
                self.check_failed.emit(updater.UpdateError(f"{type(e).__name__}: {e}", "http"), manual)
        threading.Thread(target=target, daemon=True, name="spec-surgeon-update-check").start()
        return True

    @Slot(object, bool)
    def _on_checked(self, res, manual: bool) -> None:
        self.checking = False
        self.w.set_checking(False)
        if res.status == "update" and res.info is not None:
            self.info = res.info
            self.banner.set_notes(res.info.notes)
            note = f"You have {res.current}."
            if res.skipped:
                note += " (You chose to skip this version.)"
            self.banner._show("available", f"Spec Surgeon {res.info.version} is available", note,
                              (self.banner.btn_notes, self.banner.btn_install, self.banner.btn_later,
                               self.banner.btn_skip))
            self.on_busy(self.w.busy)
            self.last_message = f"Update available: {res.info.version}"
        elif res.status == "none":
            self.last_message = "No release has been published yet."
        else:
            self.last_message = f"You're up to date — Spec Surgeon {res.current} is the newest version."
        self.w.write(f"Update check: {self.last_message}")
        if manual and res.status != "update":
            self.w.prompts.notify("Check for updates", self.last_message)

    @Slot(object, bool)
    def _on_check_failed(self, err, manual: bool) -> None:
        self.checking = False
        self.w.set_checking(False)
        self.last_message = f"Couldn't check for updates: {err}"
        self.w.write(self.last_message)
        if manual:
            if self.w.prompts.ask("Check for updates", self.last_message + "\n\nOpen the releases page instead?"):
                self.open_releases()

    # ---- banner buttons ----
    @Slot()
    def later(self) -> None:
        self.banner.hide()

    @Slot()
    def skip(self) -> None:
        if self.info is not None:
            self.settings.skipped_version = self.info.version
            self._save()
        self.banner.hide()

    @Slot()
    def open_releases(self) -> None:
        url = self.info.html_url if self.info else updater.RELEASES_LIST.format(repo=updater.REPO)
        QDesktopServices.openUrl(QUrl(url))

    @Slot()
    def open_log(self) -> None:
        log = getattr(self, "_pending_log", "")
        if log and Path(log).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(log))
        else:
            self.w.prompts.notify("Install log", "The installer did not leave a log.")

    @Slot()
    def install_now(self) -> bool:
        if self.info is None or self.downloading:
            return False
        if self.w.busy:
            self.w.prompts.notify("Install update", "A Preview or Run is in progress. Install the update once it "
                                                    "has finished.")
            return False
        if not self.installed():
            text = updater.update_instructions(self.info)
            if self.w.prompts.ask("Install update", text + "\n\nOpen the release page?"):
                self.open_releases()
            return False
        if winapp.other_instances_running():
            self.w.prompts.notify("Install update", "Another Spec Surgeon window or command is running. Close it "
                                                    "first, then press Install now again.")
            return False
        self.downloading = True
        self.cancel = threading.Event()
        self.w.set_updating(True)
        self.banner._show("downloading", f"Downloading Spec Surgeon {self.info.version}…",
                          "The installer is checked against the release's SHA-256 checksums before it runs.",
                          (self.banner.btn_cancel,))
        self.banner.progress.setRange(0, 0)
        info, cancel = self.info, self.cancel

        def target():
            try:
                dl = self.downloader(info, updates_dir(), lambda d, t: self.dl_progress.emit(d, t), cancel)
                self.dl_done.emit(dl)
            except updater.UpdateError as e:
                self.dl_failed.emit(e)
            except Exception as e:
                self.dl_failed.emit(updater.UpdateError(f"{type(e).__name__}: {e}", "http"))
        threading.Thread(target=target, daemon=True, name="spec-surgeon-update-download").start()
        return True

    @Slot()
    def cancel_download(self) -> None:
        if self.cancel is not None:
            self.cancel.set()

    @Slot(object, object)
    def _on_dl_progress(self, done, total) -> None:
        if total:
            self.banner.progress.setRange(0, 1000)
            self.banner.progress.setValue(int(1000 * done / total))
        self.banner.text.setText(f"{_mb(done)} of {_mb(total)} MB — checked against the release's SHA-256 "
                                 "checksums before it runs.")

    @Slot(object)
    def _on_dl_done(self, dl) -> None:
        self.install_downloaded(dl)

    @Slot(object)
    def _on_dl_failed(self, err) -> None:
        self.downloading = False
        self.w.set_updating(False)
        if getattr(err, "kind", "") == "cancelled":
            self._show_available_again()
            return
        self._show_error(str(err))

    def _show_available_again(self) -> None:
        if self.info is not None:
            self._on_checked(updater.CheckResult("update", updater.current_version(), self.info), False)

    def _show_error(self, message: str) -> None:
        self.w.write(f"Update failed: {message}")
        self.banner._show("error", "The update was not installed", message,
                          (self.banner.btn_retry, self.banner.btn_releases, self.banner.btn_dismiss))

    def install_downloaded(self, dl) -> bool:
        """Hand over to Setup: remember the attempt, start it detached and quit. The app mutex is NOT released here:
        Windows releases it when this process has really ended, which is what Setup waits for — so no file is
        replaced while this process still has it loaded."""
        self.downloading = False
        install_dir = Path(sys.executable).resolve().parent
        log = updates_dir() / f"install-{dl.version}.log"
        self.settings.pending_update = {"version": dl.version, "log": str(log), "started": time.time()}
        self.w.save_settings()
        try:
            self.launcher(dl, install_dir, log)
        except updater.UpdateError as e:
            self.settings.pending_update = {}
            self._save()
            self.w.set_updating(False)
            self._show_error(str(e))
            return False
        self.w.set_status(f"Closing to install {dl.version} — Spec Surgeon will reopen when it is done.")
        self.w.quit_for_update()
        return True
