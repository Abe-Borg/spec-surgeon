"""Who made Spec Surgeon, how to reach him, and the license.

One place that the window's About box, the installer, the package metadata and the updater all draw from.
``tests/test_about.py`` pins these values to the ``LICENSE`` file, ``pyproject.toml`` and the installer script,
so the name on the copyright line, the links and the license text cannot drift apart. No Qt here: the command
line and the packaging scripts read it too.
"""
from __future__ import annotations

from typing import List, Tuple

APP_NAME = "Spec Surgeon"
TAGLINE = "Provably lossless surgical edits to Word specifications."

AUTHOR = "Abraham Borg"
EMAIL = "abrahamborg@gmail.com"
GITHUB_REPO = "Abe-Borg/spec-surgeon"                      # owner/name on github.com (releases, updates, the guide)
GITHUB_URL = f"https://github.com/{GITHUB_REPO}"
LINKEDIN_URL = "https://www.linkedin.com/in/abrahamborg/"

COPYRIGHT_YEAR = "2026"
COPYRIGHT = f"Copyright (c) {COPYRIGHT_YEAR} {AUTHOR}"       # ASCII: the installer script and LICENSE use it verbatim
COPYRIGHT_LINE = f"© {COPYRIGHT_YEAR} {AUTHOR}"         # for the window

LICENSE_NAME = "MIT License"
LICENSE_SPDX = "MIT"
LICENSE_URL = "https://opensource.org/license/mit"

# Plain words for the About box and the installer. Not legal advice: the license text below is what counts.
LICENSE_SUMMARY: List[str] = [
    "Free to use, copy, change and share, for any purpose, including commercially.",
    "Keep the copyright notice and this license text with every copy or substantial part of it.",
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
    ("Qt for Python (PySide6) and Qt 6", "LGPL-3.0", "https://www.qt.io/licensing/open-source-lgpl-obligations"),
    ("lxml", "BSD-3-Clause", "https://github.com/lxml/lxml/blob/master/LICENSE.txt"),
    ("PyMuPDF (MuPDF)", "AGPL-3.0", "https://github.com/pymupdf/PyMuPDF/blob/main/COPYING"),
    ("Anthropic Python SDK", "MIT", "https://github.com/anthropics/anthropic-sdk-python/blob/main/LICENSE"),
    ("pywin32", "PSF-style license", "https://github.com/mhammond/pywin32"),
    ("PyInstaller bootloader (installed app only)", "GPL-2.0-or-later with the bootloader exception",
     "https://pyinstaller.org/en/stable/license.html"),
]


def about_lines(version: str) -> List[str]:
    """The About box as plain text (``spec-surgeon --about`` prints it; the window's About box copies it)."""
    lines = [f"{APP_NAME} {version}", TAGLINE, "", COPYRIGHT_LINE, f"{LICENSE_NAME}:"]
    lines += [f"  - {line}" for line in LICENSE_SUMMARY]
    lines += ["", f"Made by {AUTHOR}", f"  Email:    {EMAIL}", f"  GitHub:   {GITHUB_URL}",
              f"  LinkedIn: {LINKEDIN_URL}", "", "Open-source components:"]
    lines += [f"  - {name}: {lic}" for name, lic, _ in THIRD_PARTY]
    return lines
