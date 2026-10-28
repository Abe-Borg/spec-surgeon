"""Who made Spec Surgeon, how to reach him, and the licenses.

One place that the window's About box, the installer, the package metadata and the updater all draw from.
``tests/test_about.py`` pins these values to the ``LICENSE`` file, ``pyproject.toml`` and the installer script,
so the name on the copyright line, the links and the license text cannot drift apart. No window code here: the
packaging scripts read it too.

Two licenses, never one: Spec Surgeon's own code is MIT (the ``LICENSE`` file, the wheel's metadata). It uses
PyMuPDF and MuPDF, Artifex Software's, under the GNU AGPL v3 or an Artifex commercial license; no commercial license
is held, so the Windows app, which bundles PyMuPDF, is distributed as a whole under the AGPL v3, with its
Corresponding Source on each release page (``packaging/windows/license_notices.py`` writes the app's LICENSE.txt
and fetches the PyMuPDF and MuPDF source for the release).
"""
from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import List, Optional, Tuple

APP_NAME = "Spec Surgeon"
TAGLINE = "Surgical edits to Word specifications with independent verification records."

AUTHOR = "Abraham Borg"
EMAIL = "abrahamborg@gmail.com"
GITHUB_REPO = "Abe-Borg/spec-surgeon"                      # owner/name on github.com (releases, updates, the guide)
GITHUB_URL = f"https://github.com/{GITHUB_REPO}"
LINKEDIN_URL = "https://www.linkedin.com/in/abrahamborg/"

COPYRIGHT_YEAR = "2026"
COPYRIGHT = f"Copyright (c) {COPYRIGHT_YEAR} {AUTHOR}"       # ASCII: the installer script and LICENSE use it verbatim
COPYRIGHT_LINE = f"© {COPYRIGHT_YEAR} {AUTHOR}"         # for the window

RELEASES_URL = f"{GITHUB_URL}/releases"

LICENSE_NAME = "MIT License"                               # Spec Surgeon's own code: the LICENSE file
LICENSE_SPDX = "MIT"
LICENSE_URL = "https://opensource.org/license/mit"

AGPL_NAME = "GNU Affero General Public License v3"         # PyMuPDF and MuPDF, and so the app as a whole
AGPL_SPDX = "AGPL-3.0"
AGPL_URL = "https://www.gnu.org/licenses/agpl-3.0.html"
AGPL_FILE = "legal/AGPL-3.0.txt"                            # the FSF's text, as PyMuPDF ships it (COPYING)

# The one-line answer to "what license is it under?" (the About box's License row and its footer).
APP_LICENSE = f"{LICENSE_NAME} for its own code; GNU AGPL v3 for the app as a whole, with PyMuPDF"

# Plain words for the About box. Not legal advice: the license texts below are what count.
LICENSE_SUMMARY: List[str] = [
    "Spec Surgeon's own code is under the MIT License: free to use, copy, change and share, for any purpose, "
    "including commercially, keeping the copyright notice and the license text with it.",
    "It uses PyMuPDF and MuPDF by Artifex Software, which are under the GNU Affero General Public License v3 "
    "(AGPL). No commercial license for them is held, so the app as a whole, with PyMuPDF inside it, is "
    "distributed under the AGPL v3.",
    "Under the AGPL you may use the app, change it and share it; whoever shares it, changed or not, passes it on "
    "under the AGPL v3 together with its complete source code.",
    "The complete source of every version (Spec Surgeon's code and build scripts, and the PyMuPDF and MuPDF "
    f"source the Windows app was built with) is on that version's release page: {RELEASES_URL}",
    "Provided as is, without warranty of any kind: the author is not liable for anything that happens through "
    "its use. Check every delivered file yourself.",
]

LICENSE_TEXT = f"""MIT License

{COPYRIGHT}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

# What the installed app ships or loads, with each component's own license (the About box lists them).
THIRD_PARTY: List[Tuple[str, str, str]] = [
    ("Python", "PSF License", "https://docs.python.org/3/license.html"),
    ("pywebview (the native window)", "BSD-3-Clause", "https://github.com/r0x0r/pywebview/blob/master/LICENSE"),
    ("FastAPI", "MIT", "https://github.com/fastapi/fastapi/blob/master/LICENSE"),
    ("uvicorn", "BSD-3-Clause", "https://github.com/encode/uvicorn/blob/master/LICENSE.md"),
    ("React", "MIT", "https://github.com/facebook/react/blob/main/LICENSE"),
    ("Tailwind CSS", "MIT", "https://github.com/tailwindlabs/tailwindcss/blob/main/LICENSE"),
    ("lxml", "BSD-3-Clause", "https://github.com/lxml/lxml/blob/master/LICENSE.txt"),
    ("regex (bounded pattern searches)", "Apache-2.0; the parts derived from CPython's re under the CNRI Python license",
     "https://github.com/mrabarnett/mrab-regex/blob/hg/LICENSE.txt"),
    ("PyMuPDF and MuPDF (Artifex Software)", AGPL_SPDX, "https://github.com/pymupdf/PyMuPDF/blob/main/COPYING"),
    ("Anthropic Python SDK", "MIT", "https://github.com/anthropics/anthropic-sdk-python/blob/main/LICENSE"),
    ("pywin32", "PSF-style license", "https://github.com/mhammond/pywin32"),
    ("PyInstaller bootloader (installed app only)", "GPL-2.0-or-later with the bootloader exception",
     "https://pyinstaller.org/en/stable/license.html"),
]


def pdf_versions() -> Tuple[Optional[str], Optional[str]]:
    """The PyMuPDF and MuPDF versions this copy runs (None where PyMuPDF is not installed): the release assets
    that hold their source are named after them."""
    try:
        import pymupdf
        return str(pymupdf.VersionBind), str(pymupdf.VersionFitz)
    except Exception:  # noqa: BLE001 — a missing or broken PyMuPDF is reported elsewhere; the notice stays generic
        return None, None


def source_assets(pymupdf_version: str, mupdf_version: str) -> Tuple[str, str]:
    """The release assets that carry the PyMuPDF and MuPDF source: PyPI's sdist name and MuPDF's own archive name."""
    return f"pymupdf-{pymupdf_version}.tar.gz", f"mupdf-{mupdf_version}-source.tar.gz"


@lru_cache(maxsize=1)
def agpl_text() -> str:
    return resources.files("spec_surgeon").joinpath(AGPL_FILE).read_text(encoding="utf-8")


def distribution_notice(pymupdf_version: Optional[str] = None, mupdf_version: Optional[str] = None) -> str:
    """How the program is licensed as distributed, and where its Corresponding Source is (AGPL section 6(d))."""
    if pymupdf_version and mupdf_version:
        pymupdf_asset, mupdf_asset = source_assets(pymupdf_version, mupdf_version)
        pdf_source = (f"  - {pymupdf_asset} and {mupdf_asset}: the source of\n"
                      f"    PyMuPDF {pymupdf_version} and MuPDF {mupdf_version}, which the Windows app was built with.")
    else:
        pdf_source = ("  - pymupdf-<version>.tar.gz and mupdf-<version>-source.tar.gz: the source of the PyMuPDF\n"
                      "    and MuPDF the Windows app was built with.")
    return f"""{APP_NAME}: the licenses it is distributed under

{COPYRIGHT}

Spec Surgeon's own code is licensed under the MIT License (part 1 below).

Spec Surgeon uses PyMuPDF and the MuPDF library, Copyright (c) Artifex Software,
Inc., which are licensed under the GNU Affero General Public License version 3
(part 2 below) or under a commercial license from Artifex. No commercial license
from Artifex is held for Spec Surgeon. The Windows app bundles PyMuPDF, so the
app as a whole, a combined work that includes PyMuPDF, is distributed under the
terms of the GNU Affero General Public License version 3. The MIT License still
applies to Spec Surgeon's own code taken by itself.

This program comes with ABSOLUTELY NO WARRANTY, to the extent permitted by
applicable law (sections 15 and 16 of the AGPL, and the MIT License's own
disclaimer). You may redistribute it, changed or not, under the terms of the
GNU Affero General Public License version 3.

Corresponding Source: the complete source code of each released version is
published on that version's release page, under
{RELEASES_URL}:
  - "Source code (zip)" and "Source code (tar.gz)": Spec Surgeon's code, with
    the scripts in packaging/windows that build the Windows app and installer.
{pdf_source}
The app's other open-source components are listed in its About box, each with
its own license; their source is published by their projects.
"""


def license_text(pymupdf_version: Optional[str] = None, mupdf_version: Optional[str] = None) -> str:
    """The whole of it: the notice, the MIT License (Spec Surgeon's code) and the AGPL v3 (PyMuPDF, MuPDF, the app)."""
    rule = "=" * 79
    return (f"{distribution_notice(pymupdf_version, mupdf_version)}\n{rule}\n"
            f"Part 1. {LICENSE_NAME} (Spec Surgeon's own code)\n{rule}\n\n{LICENSE_TEXT}\n{rule}\n"
            f"Part 2. {AGPL_NAME} (PyMuPDF, MuPDF, and the app as a whole)\n{rule}\n\n{agpl_text()}")


def about_lines(version: str) -> List[str]:
    """The About box as plain text (the window's About box copies it)."""
    lines = [f"{APP_NAME} {version}", TAGLINE, "", COPYRIGHT_LINE, f"{APP_LICENSE}:"]
    lines += [f"  - {line}" for line in LICENSE_SUMMARY]
    lines += ["", f"Made by {AUTHOR}", f"  Email:    {EMAIL}", f"  GitHub:   {GITHUB_URL}",
              f"  LinkedIn: {LINKEDIN_URL}", "", "Open-source components:"]
    lines += [f"  - {name}: {lic}" for name, lic, _ in THIRD_PARTY]
    return lines
