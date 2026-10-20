"""The project's identity (``spec_surgeon.about``): the license, the copyright holder and the links are stated once
and every place that shows them — the LICENSE file, the package metadata, the installer script, the PyInstaller
version info, the updater's repository name — agrees with it."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

from spec_surgeon import __version__, about, updater

ROOT = Path(__file__).resolve().parent.parent
ISS = (ROOT / "packaging" / "windows" / "installer.iss").read_text(encoding="utf-8")
SPEC = (ROOT / "packaging" / "windows" / "app.spec").read_text(encoding="utf-8")


def test_the_license_file_is_the_mit_text_with_the_author_s_copyright():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.strip() == about.LICENSE_TEXT.strip()
    assert text.startswith("MIT License\n")
    assert f"Copyright (c) {about.COPYRIGHT_YEAR} {about.AUTHOR}" in text
    assert about.COPYRIGHT in text and about.LICENSE_SPDX == "MIT"
    assert "WITHOUT WARRANTY OF ANY KIND" in text


def test_package_metadata_names_the_license_the_author_and_the_repository():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]
    assert project["license"] == about.LICENSE_SPDX
    assert project["license-files"] == ["LICENSE"]
    assert project["authors"] == [{"name": about.AUTHOR, "email": about.EMAIL}]
    assert project["urls"]["Repository"] == about.GITHUB_URL
    assert "include LICENSE" in (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert updater.REPO == about.GITHUB_REPO and about.GITHUB_URL.endswith(about.GITHUB_REPO)


def test_the_links_and_the_email_are_well_formed():
    assert about.LINKEDIN_URL.startswith("https://www.linkedin.com/in/")
    assert about.GITHUB_URL == "https://github.com/Abe-Borg/spec-surgeon"
    assert re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]+", about.EMAIL)
    assert about.EMAIL == "abrahamborg@gmail.com"


def test_the_installer_shows_the_license_the_copyright_and_the_links():
    assert "LicenseFile={#SourcePath}..\\..\\LICENSE" in ISS                # the wizard's license page
    assert f"AppCopyright={about.COPYRIGHT}" in ISS
    assert f"AppContact={about.EMAIL}" in ISS
    assert f"AppPublisher={about.AUTHOR}" in ISS
    assert f"AppPublisherURL={about.GITHUB_URL}" in ISS
    assert "DisableWelcomePage=no" in ISS                                    # the welcome page carries the links
    assert f"GitHubUrl = '{about.GITHUB_URL}'" in ISS and f"LinkedInUrl = '{about.LINKEDIN_URL}'" in ISS
    assert "MIT License" in ISS and about.COPYRIGHT in re.search(r"^BeveledLabel=(.*)$", ISS, re.M).group(1)
    assert 'DestName: "LICENSE.txt"' in ISS                                 # the license ships with the app
    assert "/issues" not in ISS                                              # no "report a problem" anywhere
    assert "LegalCopyright" in SPEC and about.COPYRIGHT in SPEC


def test_about_text_says_everything_in_plain_words():
    text = "\n".join(about.about_lines(__version__))
    assert text.startswith(f"{about.APP_NAME} {__version__}")
    assert about.COPYRIGHT_LINE in text and about.LICENSE_NAME in text
    for line in about.LICENSE_SUMMARY:
        assert line in text
    for value in (about.EMAIL, about.GITHUB_URL, about.LINKEDIN_URL, about.AUTHOR):
        assert value in text
    for name, lic, url in about.THIRD_PARTY:
        assert f"{name}: {lic}" in text and url.startswith("https://")
    assert any("PyMuPDF" in name and "AGPL" in lic for name, lic, _ in about.THIRD_PARTY)
    names = [name for name, _, _ in about.THIRD_PARTY]
    for component in ("pywebview", "FastAPI", "uvicorn", "React", "Tailwind CSS"):        # the window's stack
        assert any(component in name for name in names), component
