"""The Windows app's license and Corresponding Source (``packaging/windows/license_notices.py``) on scripted
downloads: the bundle's LICENSE.txt carries both licenses, and the PyMuPDF and MuPDF source beside the installer is
exactly what PyPI publishes and what that PyMuPDF builds from. No network."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest

from spec_surgeon import about

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("license_notices", ROOT / "packaging/windows/license_notices.py")
notices = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notices)
SDIST_URL = "https://files.example.invalid/pymupdf-1.28.2.tar.gz"


def tarball(prefix, files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, text in files.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(f"{prefix}/{name}" if prefix else name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def scripted(*, sdist=None, mupdf=None, digest=None, sdists=1):
    sdist = sdist if sdist is not None else tarball("pymupdf-1.28.2", {"setup.py": "version_mupdf = '1.28.1'\n"})
    mupdf = mupdf if mupdf is not None else tarball("mupdf-1.28.1-source", {"Makefile": "all:\n"})
    listing = {"urls": [{"packagetype": "bdist_wheel", "url": "https://files.example.invalid/x.whl"}]
               + [{"packagetype": "sdist", "url": SDIST_URL,
                   "digests": {"sha256": digest or hashlib.sha256(sdist).hexdigest()}}] * sdists}
    responses = {notices.PYPI_JSON.format(version="1.28.2"): json.dumps(listing).encode(), SDIST_URL: sdist,
                 notices.MUPDF_ARCHIVE.format(name="mupdf-1.28.1-source.tar.gz"): mupdf}
    asked = []

    def fetch(url, path):
        asked.append(url)
        Path(path).write_bytes(responses[url])
    return fetch, asked


def test_the_source_of_the_bundled_pymupdf_and_mupdf_lands_beside_the_installer(tmp_path):
    fetch, asked = scripted()
    paths = notices.fetch_sources(tmp_path, "1.28.2", "1.28.1", fetch=fetch)
    assert [p.name for p in paths] == list(about.source_assets("1.28.2", "1.28.1"))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["mupdf-1.28.1-source.tar.gz", "pymupdf-1.28.2.tar.gz"]
    assert asked == ["https://pypi.org/pypi/pymupdf/1.28.2/json", SDIST_URL,
                     "https://mupdf.com/downloads/archive/mupdf-1.28.1-source.tar.gz"]


@pytest.mark.parametrize("case,message", [
    ("digest", "does not match the SHA-256"),
    ("pin", "builds from MuPDF 1.27.0"),
    ("mupdf_prefix", "is not a source archive of mupdf-1.28.1-source"),
    ("sdist_prefix", "is not a source archive of pymupdf-1.28.2"),
    ("two_sdists", "lists 2 source distributions"),
])
def test_a_source_that_is_not_what_the_bundle_carries_stops_the_release(tmp_path, case, message):
    kwargs = {"digest": dict(digest="0" * 64),
              "pin": dict(sdist=tarball("pymupdf-1.28.2", {"setup.py": "version_mupdf = '1.27.0'\n"})),
              "mupdf_prefix": dict(mupdf=tarball("mupdf-1.27.0-source", {"Makefile": ""})),
              "sdist_prefix": dict(sdist=tarball("", {"setup.py": "", "../escape": ""})),
              "two_sdists": dict(sdists=2)}[case]
    fetch, _ = scripted(**kwargs)
    with pytest.raises(notices.SourceError, match=message):
        notices.fetch_sources(tmp_path, "1.28.2", "1.28.1", fetch=fetch)


def test_an_sdist_that_names_no_mupdf_version_is_still_published(tmp_path):
    fetch, _ = scripted(sdist=tarball("pymupdf-1.28.2", {"README.md": "no setup.py"}))
    assert len(notices.fetch_sources(tmp_path, "1.28.2", "1.28.1", fetch=fetch)) == 2


def test_the_bundle_s_license_names_both_licenses_with_windows_line_endings(tmp_path):
    path = notices.write_license(tmp_path, "1.28.2", "1.28.1")
    data = path.read_bytes()
    assert path.name == "LICENSE.txt" and b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b"")
    text = data.decode("utf-8").replace("\r\n", "\n")
    assert text == about.license_text("1.28.2", "1.28.1")
    assert "MIT License" in text and "GNU AFFERO GENERAL PUBLIC LICENSE" in text
    assert "pymupdf-1.28.2.tar.gz and mupdf-1.28.1-source.tar.gz" in text
