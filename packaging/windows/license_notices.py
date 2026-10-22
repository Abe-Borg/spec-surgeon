"""Write the Windows app's LICENSE.txt and fetch the source of the PyMuPDF and MuPDF it bundles.

The installed app is a combined work distributed under the GNU AGPL v3: PyMuPDF and MuPDF are Artifex Software's,
under the AGPL or an Artifex commercial license, and no commercial license is held. Its Corresponding Source goes on
the same release page as the installer (AGPL section 6(d)): the tag's source archives carry Spec Surgeon's code and
these build scripts, and this script puts the PyMuPDF sdist (PyPI's, checked against PyPI's SHA-256) and the MuPDF
source archive that sdist builds from beside the installer, named after the versions the bundle carries.

Run it in the environment PyInstaller bundled from, after the bundle and before Inno Setup (the installer shows the
LICENSE.txt it writes and installs it):

    python packaging/windows/license_notices.py --app dist/SpecSurgeon --sources dist/installer
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import sys
import tarfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from spec_surgeon import about  # noqa: E402

PYPI_JSON = "https://pypi.org/pypi/pymupdf/{version}/json"
MUPDF_ARCHIVE = "https://mupdf.com/downloads/archive/{name}"
TIMEOUT = 120
ATTEMPTS = 3


class SourceError(RuntimeError):
    pass


def download(url, path):
    """Stream ``url`` to ``path``; three attempts, so one dropped connection does not fail the release."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT) as response, open(path, "wb") as out:
                shutil.copyfileobj(response, out, 1 << 20)
            return
        except OSError:
            if attempt == ATTEMPTS:
                raise
            time.sleep(2 ** attempt)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as data:
        for chunk in iter(lambda: data.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_license(app_dir, pymupdf_version, mupdf_version):
    """The installer's license page and the installed LICENSE.txt: the notice, the MIT License and the AGPL v3.
    CRLF, for Notepad and the installer's page."""
    path = Path(app_dir) / "LICENSE.txt"
    with open(path, "w", encoding="utf-8", newline="\r\n") as out:
        out.write(about.license_text(pymupdf_version, mupdf_version))
    return path


def _members_under(path, prefix):
    with tarfile.open(path, "r:gz") as archive:
        names = archive.getnames()
    if not names or any(name != prefix and not name.startswith(prefix + "/") for name in names):
        raise SourceError(f"{Path(path).name} is not a source archive of {prefix}")
    return names


def _pinned_mupdf(sdist, prefix):
    """The MuPDF version PyMuPDF's setup.py builds from, when it names one."""
    with tarfile.open(sdist, "r:gz") as archive:
        try:
            member = archive.extractfile(f"{prefix}/setup.py")
        except KeyError:
            return None
        text = io.TextIOWrapper(member, encoding="utf-8", errors="replace").read() if member else ""
    match = re.search(r"^version_mupdf\s*=\s*['\"]([^'\"]+)['\"]", text, re.M)
    return match.group(1) if match else None


def fetch_sources(dest, pymupdf_version, mupdf_version, *, fetch=download):
    """Put the PyMuPDF sdist and the MuPDF source archive in ``dest`` and check each is what it says it is."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    pymupdf_name, mupdf_name = about.source_assets(pymupdf_version, mupdf_version)

    listing = dest / f".pymupdf-{pymupdf_version}.json"
    fetch(PYPI_JSON.format(version=pymupdf_version), listing)
    release = json.loads(listing.read_text(encoding="utf-8"))
    listing.unlink()
    sdists = [entry for entry in release.get("urls", []) if entry.get("packagetype") == "sdist"]
    if len(sdists) != 1:
        raise SourceError(f"PyPI lists {len(sdists)} source distributions for PyMuPDF {pymupdf_version}")
    sdist = dest / pymupdf_name
    fetch(sdists[0]["url"], sdist)
    if sha256(sdist) != sdists[0]["digests"]["sha256"]:
        raise SourceError(f"{pymupdf_name} does not match the SHA-256 PyPI publishes for it")
    _members_under(sdist, f"pymupdf-{pymupdf_version}")
    pinned = _pinned_mupdf(sdist, f"pymupdf-{pymupdf_version}")
    if pinned is not None and pinned != mupdf_version:
        raise SourceError(f"PyMuPDF {pymupdf_version} builds from MuPDF {pinned}, but the bundle reports "
                          f"MuPDF {mupdf_version}")

    mupdf = dest / mupdf_name
    fetch(MUPDF_ARCHIVE.format(name=mupdf_name), mupdf)
    _members_under(mupdf, f"mupdf-{mupdf_version}-source")
    return [sdist, mupdf]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--app", type=Path, required=True, help="the bundled app folder (dist/SpecSurgeon)")
    parser.add_argument("--sources", type=Path, help="where the release's PyMuPDF and MuPDF source goes")
    args = parser.parse_args(argv)
    pymupdf_version, mupdf_version = about.pdf_versions()
    if not (pymupdf_version and mupdf_version):
        raise SystemExit("PyMuPDF is not installed here: run this in the environment the app was bundled from.")
    print(f"Wrote {write_license(args.app, pymupdf_version, mupdf_version)}")
    if args.sources:
        for path in fetch_sources(args.sources, pymupdf_version, mupdf_version):
            print(f"Fetched {path} ({path.stat().st_size} bytes, sha256 {sha256(path)})")


if __name__ == "__main__":
    main()
