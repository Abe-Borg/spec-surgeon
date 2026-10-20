"""Updates without a toolkit: the once-a-day check, Help → Check for updates, the download with its progress, and the
one-click install (download → verify → start Setup → quit; Setup reopens the app). Every decision about what may be
downloaded or started lives in ``spec_surgeon.updater``; this drives it and keeps the state the window's banner
shows (docs/WEB_API.md: ``update``)."""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .. import diagnostics as diag
from .. import updater, winapp
from ..settings import Settings, save_settings, updates_dir


def _mb(n: Optional[int]) -> str:
    return "?" if n is None else f"{n / 2 ** 20:.0f}"


class UpdateFlow:
    """``state()`` is what the banner shows: ``none`` (nothing to say), ``available``, ``downloading``, ``installing``
    (Setup was started; the app is closing), ``error``, ``updated`` (this start follows an update) or ``failed`` (an
    update that did not finish). ``checker``, ``downloader``, ``launcher`` and ``installed`` are injectable."""

    def __init__(self, settings: Settings, *, checker: Optional[Callable] = None, downloader: Optional[Callable] = None,
                 launcher: Optional[Callable] = None, installed: Optional[Callable[[], bool]] = None,
                 release_notes: Callable[[], str] = lambda: "", quit_app: Callable[[], None] = lambda: None,
                 is_busy: Callable[[], bool] = lambda: False):
        self.settings = settings
        self.checker = checker or (lambda manual, skipped: updater.check_for_update(skipped=skipped, manual=manual))
        self.downloader = downloader or (lambda info, dest, progress, cancel: updater.download_installer(
            info, dest, progress=progress, cancel=cancel))
        self.launcher = launcher or (lambda dl, install_dir, log: updater.launch_installer(
            dl, install_dir=install_dir, log=log))
        self.installed = installed or updater.is_installed_build
        self.release_notes = release_notes
        self.quit_app = quit_app
        self.is_busy = is_busy
        self.info: Optional[updater.UpdateInfo] = None
        self.checking = False
        self.downloading = False
        self.cancel: Optional[threading.Event] = None
        self.last_message = ""
        self._lock = threading.Lock()
        self._state: Dict[str, Any] = {"state": "none", "version": "", "notes": "", "progress": 0, "error": "",
                                       "text": "", "manual_result": "", "install_log": ""}

    # ---- what the window shows ----
    def state(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def _set(self, **fields) -> None:
        with self._lock:
            self._state.update(fields)

    def _save(self) -> None:
        try:
            save_settings(self.settings)
        except OSError as e:
            diag.app_log().warning(f"Could not save settings: {e}")

    # ---- start-up ----
    def startup(self) -> None:
        """After an update: say it worked (or didn't); tidy old downloads."""
        s, cur = self.settings, updater.current_version()
        pending = s.pending_update or {}
        if pending.get("version"):
            if updater.is_newer(pending["version"], cur):
                self._set(state="failed", version=pending["version"], install_log=pending.get("log", ""),
                          text=f"You are still on {cur}. The install log says why.", error="")
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
        notes = self.release_notes() or ""
        hint = "" if os.environ.get("ANTHROPIC_API_KEY") else \
            "If you typed your Anthropic API key for one session only, enter it again in Settings."
        self._set(state="updated", version=cur, notes=notes, text=hint, error="")

    # ---- checks ----
    def auto_check(self) -> bool:
        if not updater.should_auto_check(self.settings):
            return False
        self.settings.last_update_check = time.time()
        self._save()
        return self.check(manual=False)

    def check(self, manual: bool = True) -> bool:
        if self.checking or self.downloading:
            return False
        self.checking = True
        skipped = self.settings.skipped_version

        def target():
            try:
                self._on_checked(self.checker(manual, skipped), manual)
            except updater.UpdateError as e:
                self._on_check_failed(e, manual)
            except Exception as e:  # noqa: BLE001 — never crash the window over an update check
                self._on_check_failed(updater.UpdateError(f"{type(e).__name__}: {e}", "http"), manual)
        threading.Thread(target=target, daemon=True, name="spec-surgeon-update-check").start()
        return True

    def _on_checked(self, res, manual: bool) -> None:
        self.checking = False
        if res.status == "update" and res.info is not None:
            self.info = res.info
            note = f"You have {res.current}."
            if res.skipped:
                note += " (You chose to skip this version.)"
            self._set(state="available", version=res.info.version, notes=res.info.notes or "", text=note, error="",
                      manual_result="")
            self.last_message = f"Update available: {res.info.version}"
        elif res.status == "none":
            self.last_message = "No release has been published yet."
            if manual:
                self._set(manual_result=self.last_message)
        else:
            self.last_message = f"You're up to date — Spec Surgeon {res.current} is the newest version."
            if manual:
                self._set(manual_result=self.last_message)
        diag.app_log().info(f"Update check: {self.last_message}")

    def _on_check_failed(self, err, manual: bool) -> None:
        self.checking = False
        self.last_message = f"Couldn't check for updates: {err}"
        diag.app_log().info(self.last_message)
        if manual:
            self._set(manual_result=self.last_message)

    # ---- the banner's buttons ----
    def dismiss(self) -> None:
        self._set(state="none", manual_result="")

    def skip(self) -> None:
        if self.info is not None:
            self.settings.skipped_version = self.info.version
            self._save()
        self._set(state="none")

    def releases_url(self) -> str:
        return self.info.html_url if self.info else updater.RELEASES_LIST.format(repo=updater.REPO)

    def install(self) -> str:
        """Download, verify and hand over to Setup. Returns "" when the download started, else one sentence why not."""
        if self.info is None or self.downloading:
            return "There is no update to install."
        if self.is_busy():
            return "Work is in progress. Install the update once it has finished."
        if not self.installed():
            return updater.update_instructions(self.info)
        if winapp.other_instances_running():
            return "Another Spec Surgeon window or command is running. Close it first, then press Install again."
        self.downloading = True
        self.cancel = threading.Event()
        self._set(state="downloading", progress=0,
                  text="The installer is checked against the release's SHA-256 checksums before it runs.")
        info, cancel = self.info, self.cancel

        def progress(done, total):
            self._set(progress=int(100 * done / total) if total else 0,
                      text=f"{_mb(done)} of {_mb(total)} MB — checked against the release's SHA-256 checksums "
                           "before it runs.")

        def target():
            try:
                dl = self.downloader(info, updates_dir(), progress, cancel)
                self.install_downloaded(dl)
            except updater.UpdateError as e:
                self._on_dl_failed(e)
            except Exception as e:  # noqa: BLE001
                self._on_dl_failed(updater.UpdateError(f"{type(e).__name__}: {e}", "http"))
        threading.Thread(target=target, daemon=True, name="spec-surgeon-update-download").start()
        return ""

    def cancel_download(self) -> None:
        if self.cancel is not None:
            self.cancel.set()

    def _on_dl_failed(self, err) -> None:
        self.downloading = False
        if getattr(err, "kind", "") == "cancelled":
            if self.info is not None:
                self._on_checked(updater.CheckResult("update", updater.current_version(), self.info), False)
            return
        self._show_error(str(err))

    def _show_error(self, message: str) -> None:
        diag.app_log().info(f"Update failed: {message}")
        self._set(state="error", error=message, text="")

    def install_downloaded(self, dl) -> bool:
        """Hand over to Setup: remember the attempt, start it detached and quit. The app mutex is NOT released here:
        Windows releases it when this process has really ended, which is what Setup waits for."""
        self.downloading = False
        install_dir = Path(sys.executable).resolve().parent
        log = updates_dir() / f"install-{dl.version}.log"
        self.settings.pending_update = {"version": dl.version, "log": str(log), "started": time.time()}
        self._save()
        try:
            self.launcher(dl, install_dir, log)
        except updater.UpdateError as e:
            self.settings.pending_update = {}
            self._save()
            self._show_error(str(e))
            return False
        self._set(state="installing", version=dl.version,
                  text=f"Closing to install {dl.version} — Spec Surgeon will reopen when it is done.")
        self.quit_app()
        return True
