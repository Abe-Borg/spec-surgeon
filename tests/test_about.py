"""The project's identity (``spec_surgeon.about``): the license, the copyright holder and the links are stated once
and every place that shows them — the LICENSE file, the package metadata, the installer script, the PyInstaller
version info, the updater's repository name — agrees with it."""
from __future__ import annotations

import hashlib
import re
import tomllib
from pathlib import Path

from spec_surgeon import __version__, about, updater

ROOT = Path(__file__).resolve().parent.parent
ISS = (ROOT / "packaging" / "windows" / "installer.iss").read_text(encoding="utf-8")
SPEC = (ROOT / "packaging" / "windows" / "app.spec").read_text(encoding="utf-8")
AGPL_SHA256 = "57c8ff33c9c0cfc3ef00e650a1cc910d7ee479a8bc509f6c9209a7c2a11399d6"


def test_the_license_file_is_the_mit_text_with_the_author_s_copyright():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.strip() == about.LICENSE_TEXT.strip()
    assert text.startswith("MIT License\n")
    assert f"Copyright (c) {about.COPYRIGHT_YEAR} {about.AUTHOR}" in text
    assert about.COPYRIGHT in text and about.LICENSE_SPDX == "MIT"
    assert "WITHOUT WARRANTY OF ANY KIND" in text


def test_the_agpl_text_is_the_fsf_s_and_ships_with_the_package():
    text = about.agpl_text()
    assert text.startswith("                    GNU AFFERO GENERAL PUBLIC LICENSE\n                       Version 3, 19 November 2007")
    assert "END OF TERMS AND CONDITIONS" in text and "15. Disclaimer of Warranty." in text
    # The FSF's text byte for byte (as PyMuPDF ships it in its COPYING), whatever line endings a checkout gave it.
    assert hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest() == AGPL_SHA256
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "legal/*.txt" in pyproject["tool"]["setuptools"]["package-data"]["spec_surgeon"]
    assert "recursive-include src/spec_surgeon/legal *.txt" in (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert '"legal/*.txt"' in SPEC                                          # the frozen app's About box reads it


def test_the_distributed_program_is_never_described_as_mit_only():
    # Spec Surgeon's own code is MIT; with PyMuPDF (AGPL-3.0, no commercial license held) the app as a whole is AGPL.
    text = about.license_text("1.28.2", "1.28.1")
    notice = about.distribution_notice("1.28.2", "1.28.1")
    assert text.startswith(notice)
    assert about.LICENSE_TEXT.strip() in text and about.agpl_text().strip() in text
    assert text.index(about.LICENSE_TEXT.strip()) < text.index(about.agpl_text().strip())
    assert "No commercial license" in notice and "PyMuPDF" in notice and "Artifex" in notice
    assert "distributed under the\nterms of the GNU Affero General Public License version 3" in notice
    assert "Corresponding Source" in notice and about.RELEASES_URL in notice
    assert "pymupdf-1.28.2.tar.gz and mupdf-1.28.1-source.tar.gz" in notice and "packaging/windows" in notice
    assert "ABSOLUTELY NO WARRANTY" in notice
    assert about.source_assets("1.28.2", "1.28.1") == ("pymupdf-1.28.2.tar.gz", "mupdf-1.28.1-source.tar.gz")
    assert "pymupdf-<version>.tar.gz" in about.distribution_notice()           # without PyMuPDF installed
    assert all(len(line) <= 80 for line in notice.splitlines())             # reads in Notepad and the wizard
    assert "MIT" in about.APP_LICENSE and "AGPL" in about.APP_LICENSE
    summary = " ".join(about.LICENSE_SUMMARY)
    for words in ("MIT License", "GNU Affero General Public License v3", "No commercial license", about.RELEASES_URL,
                  "complete source code", "without warranty"):
        assert words in summary, words
    pymupdf, mupdf = about.pdf_versions()
    assert pymupdf and mupdf                                                # a core dependency


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
    # The wizard's license page is the bundle's LICENSE.txt (both licenses), shown rather than agreed to: the AGPL
    # asks no one to accept it to receive or run the program.
    assert "InfoBeforeFile={#SourcePath}..\\..\\dist\\SpecSurgeon\\LICENSE.txt" in ISS
    assert not re.search(r"^LicenseFile=", ISS, re.M)
    assert f"AppCopyright={about.COPYRIGHT}" in ISS
    assert f"AppContact={about.EMAIL}" in ISS
    assert f"AppPublisher={about.AUTHOR}" in ISS
    assert f"AppPublisherURL={about.GITHUB_URL}" in ISS
    assert "DisableWelcomePage=no" in ISS                                    # the welcome page carries the links
    assert f"GitHubUrl = '{about.GITHUB_URL}'" in ISS and f"LinkedInUrl = '{about.LINKEDIN_URL}'" in ISS
    beveled = re.search(r"^BeveledLabel=(.*)$", ISS, re.M).group(1)
    assert about.COPYRIGHT in beveled and "MIT License" in beveled and "AGPL v3" in beveled
    welcome = re.search(r"^WelcomeLabel2=(.*)$", ISS, re.M).group(1)
    assert "MIT License" in welcome and "PyMuPDF" in welcome and "AGPL v3" in welcome and "source" in welcome
    assert "released under the MIT License" not in ISS                      # never MIT-only
    assert 'Source: "*"; DestDir: "{app}"' in ISS                            # LICENSE.txt ships in the bundle
    assert 'DestName: "LICENSE.txt"' not in ISS                             # not the MIT file alone over it
    assert "/issues" not in ISS                                              # no "report a problem" anywhere
    assert "procedure InitializeWizard" in ISS and ISS.count("AddLink(") == 3     # the definition and two links
    assert "WelcomeLabel2=" in ISS
    assert "CloseApplications=yes" in ISS and "SetupLogging=yes" in ISS      # unchanged by the welcome page
    legal = re.search(r"StringStruct\('LegalCopyright', '([^']*)'\)", SPEC).group(1)
    assert about.COPYRIGHT in legal and "MIT" in legal and "AGPL-3.0" in legal


def test_about_text_says_everything_in_plain_words():
    text = "\n".join(about.about_lines(__version__))
    assert text.startswith(f"{about.APP_NAME} {__version__}")
    assert about.COPYRIGHT_LINE in text and about.LICENSE_NAME in text and about.APP_LICENSE in text
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
