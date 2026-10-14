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
