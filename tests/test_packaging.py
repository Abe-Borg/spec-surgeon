"""Names the app, the installer and the release workflow must agree on — a mismatch would break updates silently
(the updater would never find the installer, Setup would never wait for the app, the app would never reopen)."""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

from spec_surgeon import __version__, updater, winapp

ROOT = Path(__file__).resolve().parent.parent
ISS = (ROOT / "packaging" / "windows" / "installer.iss").read_text(encoding="utf-8")
SPEC = (ROOT / "packaging" / "windows" / "app.spec").read_text(encoding="utf-8")


def test_installer_name_matches_the_updater():
    base = re.search(r"^OutputBaseFilename=(.+)$", ISS, re.M).group(1).strip()
    assert base.replace("{#AppVersion}", "1.2.3") + ".exe" == updater.INSTALLER_NAME.format(version="1.2.3")
    assert updater.INSTALLER_RE.fullmatch(updater.INSTALLER_NAME.format(version=__version__))


def test_installer_waits_for_the_app_and_reopens_it():
    assert f"AppMutexName = '{winapp.APP_MUTEX}'" in ISS
    assert "{param:RELAUNCH|0}" in ISS and "Check: ShouldRelaunch" in ISS
    assert "/RELAUNCH=1" in updater.installer_args(install_dir=Path("x"), log=Path("y"))
    assert re.search(r"\[InstallDelete\]\s*\n(;.*\n)*Type: filesandordirs; Name: \"\{app\}\\_internal\"", ISS)
    assert ISS.count(f'AppUserModelID: "{winapp.AUMID}"') == 2
    assert "PrivilegesRequired=lowest" in ISS            # the in-app update never asks for administrator rights
    assert ISS.count('contents_directory="_internal"') == 0 and SPEC.count('contents_directory="_internal"') == 2


def test_the_installer_states_the_license_and_the_author():
    """The license page, the copyright line and the author's links come from spec_surgeon.about (test_about.py
    checks the values); here: the wizard pages that show them are on, and the in-app update stays silent."""
    assert re.search(r"^LicenseFile=.*LICENSE$", ISS, re.M) and "DisableWelcomePage=no" in ISS
    assert re.search(r"^AppCopyright=Copyright \(c\) \d{4} Abraham Borg$", ISS, re.M)
    assert "procedure InitializeWizard" in ISS and ISS.count("AddLink(") == 3     # the definition and two links
    assert "WelcomeLabel2=" in ISS and "MIT License" in ISS
    assert "CloseApplications=yes" in ISS and "SetupLogging=yes" in ISS      # unchanged by the welcome page
    assert (ROOT / "LICENSE").is_file()


def test_the_bundle_has_the_icon_and_the_ui_resources():
    assert SPEC.count("icon=icon") == 2 and '"ui/resources/*"' in SPEC
    assert "SetupIconFile={#SourcePath}SpecSurgeon.ico" in ISS
    sys.path.insert(0, str(ROOT / "packaging" / "windows"))
    try:
        from make_icon import read_ico_sizes
    finally:
        sys.path.pop(0)
    sizes = {w for w, h, _ in read_ico_sizes((ROOT / "packaging" / "windows" / "SpecSurgeon.ico").read_bytes())}
    assert {16, 24, 32, 48, 256} <= sizes
    assert (ROOT / "src" / "spec_surgeon" / "ui" / "resources" / "icon.svg").is_file()
    assert (ROOT / "src" / "spec_surgeon" / "ui" / "resources" / "icon-256.png").is_file()


def test_releases_publish_the_checksums_the_updater_needs():
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "sha256sum *.whl *.tar.gz *.exe > SHA256SUMS" in release
    assert updater.SUMS_NAME == "SHA256SUMS"
    assert "--latest" in release                           # /releases/latest is what the updater asks for


def test_version_and_package_data():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == __version__
    assert "ui/resources/*" in pyproject["tool"]["setuptools"]["package-data"]["spec_surgeon"]
    assert any(d.startswith("PySide6-Essentials") for d in pyproject["project"]["optional-dependencies"]["gui"])
    assert (ROOT / "docs" / "releases" / f"v{__version__}.md").is_file()


def test_frozen_analyzer_dispatches_before_opening_the_app(monkeypatch):
    import importlib.util
    import multiprocessing
    from types import SimpleNamespace
    import pytest
    module_spec = importlib.util.spec_from_file_location(
        "_test_launcher_bootstrap", ROOT / "packaging" / "windows" / "launcher_bootstrap.py")
    launcher = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(launcher)
    calls = []
    monkeypatch.setattr(multiprocessing, "freeze_support", lambda: None)
    monkeypatch.setattr(sys, "argv", ["SpecSurgeon.exe", "-I", "-m", "spec_surgeon.analysis_runner", "request.json"])
    monkeypatch.setitem(sys.modules, "spec_surgeon.analysis_runner",
                        SimpleNamespace(main=lambda args: calls.append(args) or 0))
    monkeypatch.setattr(winapp, "hold_app_mutex", lambda: pytest.fail("analysis worker must not start the app"))
    with pytest.raises(SystemExit) as stopped:
        launcher.prepare()
    assert stopped.value.code == 0
    assert calls == [["request.json"]]
    assert '"spec_surgeon.analysis_runner"' in SPEC
