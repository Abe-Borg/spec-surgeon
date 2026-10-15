"""Updates from the project's GitHub releases (no Qt; every network and process call is injectable for the tests).

1. **Check** — ``GET https://api.github.com/repos/Abe-Borg/spec-surgeon/releases/latest`` (drafts and
   prereleases are never offered). When GitHub rate-limits the API (60 calls/hour per network address — an office
   behind one address shares it), the tag is read from the ``github.com/…/releases/latest`` redirect instead.
2. **Download** — only an asset named exactly ``SpecSurgeon-<version>-Setup-x64.exe``. Its SHA-256 must appear for
   that exact name in the release's ``SHA256SUMS`` and, when GitHub reports one, equal the asset's own ``digest``;
   the size must equal the asset size. Anything else deletes the download and raises ``UpdateError``.
3. **Install** — only from a copy installed by the setup program (frozen, Windows, ``unins000.exe`` beside it).
   The file is hashed again immediately before it starts; Setup runs detached with ``/SILENT … /RELAUNCH=1`` over
   the running install folder, and the window quits. The installer waits for the app mutex (``winapp``), installs,
   and reopens the app (``installer.iss`` ``ShouldRelaunch``).

HTTPS only, redirects included. ``ssl.create_default_context()`` reads the Windows certificate store, so a
company's HTTPS-inspection root that Windows trusts works; proxies come from the environment and the Windows
registry (PAC/WPAD scripts are not evaluated — the check then fails with a plain message and the releases link).

Integrity, not authenticity: ``SHA256SUMS`` comes from the same release, so the checks catch corruption,
truncation and a mangled transfer — not a compromised GitHub account. ``verify_signature`` is where an Authenticode
check goes once the installer is signed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import about

REPO = about.GITHUB_REPO                       # "Abe-Borg/spec-surgeon"
API_LATEST = "https://api.github.com/repos/{repo}/releases/latest"
RELEASES_PAGE = "https://github.com/{repo}/releases/latest"
RELEASES_LIST = "https://github.com/{repo}/releases"
DOWNLOAD_URL = "https://github.com/{repo}/releases/download/{tag}/{name}"
INSTALLER_NAME = "SpecSurgeon-{version}-Setup-x64.exe"          # == installer.iss OutputBaseFilename
INSTALLER_RE = re.compile(r"SpecSurgeon-\d+\.\d+\.\d+-Setup-x64\.exe")
WHEEL_NAME = "spec_surgeon-{version}-py3-none-any.whl"
SUMS_NAME = "SHA256SUMS"
CHECK_INTERVAL = 24 * 3600
NO_CHECK_ENV = "SPEC_SURGEON_NO_UPDATE_CHECK"
MAX_SUMS_BYTES = 64 * 1024
MAX_INSTALLER_BYTES = 500 * 2 ** 20
MAX_NOTES_CHARS = 20_000
MAX_API_BYTES = 2 * 2 ** 20
CHUNK = 256 * 1024

# Windows process-creation flags, spelled out so the tests (which run on Linux) can check them.
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")


class UpdateError(Exception):
    """A plain-English reason the check, download or install did not happen. ``kind`` is one of offline, tls,
    rate_limited, http, bad_release, integrity, cancelled, not_supported."""

    def __init__(self, message: str, kind: str = "network"):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size: Optional[int] = None
    digest: Optional[str] = None          # "sha256:<hex>" when GitHub reports one


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    tag: str
    notes: str
    html_url: str
    published_at: str = ""
    installer: Optional[Asset] = None
    sums: Optional[Asset] = None
    wheel: Optional[Asset] = None

    @property
    def can_install(self) -> bool:
        return self.installer is not None and self.sums is not None


@dataclass(frozen=True)
class CheckResult:
    status: str                           # "update" | "current" | "none" (no release published yet)
    current: str
    info: Optional[UpdateInfo] = None
    skipped: bool = False                 # the newer version is the one the user chose to skip


@dataclass(frozen=True)
class DownloadedInstaller:
    path: Path
    sha256: str
    version: str


# ---- versions ------------------------------------------------------------------------------------------

def parse_version(text: str) -> Tuple[int, int, int]:
    if not isinstance(text, str):                # e.g. a hand-edited settings file holding a number
        raise ValueError(f"not a release version: {text!r}")
    m = _VERSION_RE.match(text.strip())
    if not m:
        raise ValueError(f"not a release version: {text!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def is_newer(candidate: str, current: str) -> bool:
    try:
        return parse_version(candidate) > parse_version(current)
    except ValueError:
        return False


def current_version() -> str:
    from . import __version__
    return __version__


def is_installed_build() -> bool:
    """A copy installed by the setup program — the only kind the in-app installer may update."""
    if not sys.platform.startswith("win") or not getattr(sys, "frozen", False):
        return False
    return (Path(sys.executable).resolve().parent / "unins000.exe").is_file()


# ---- release parsing -------------------------------------------------------------------------------------

def _asset(a: Dict[str, Any]) -> Optional[Asset]:
    name, url = a.get("name"), a.get("browser_download_url")
    if not isinstance(name, str) or not isinstance(url, str):
        return None
    size = a.get("size") if isinstance(a.get("size"), int) and not isinstance(a.get("size"), bool) else None
    digest = a.get("digest") if isinstance(a.get("digest"), str) else None
    return Asset(name=name, url=url, size=size, digest=digest)


def parse_release(data: Any) -> Optional[UpdateInfo]:
    """The release as an ``UpdateInfo``, or None when it must not be offered (draft, prerelease, a tag that is not
    ``vX.Y.Z``). Only an installer named exactly for the release's version counts."""
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = data.get("tag_name")
    try:
        version = ".".join(str(n) for n in parse_version(tag))
    except (ValueError, TypeError):
        return None
    assets = [x for x in (_asset(a) for a in data.get("assets") or [] if isinstance(a, dict)) if x]
    by_name = {a.name: a for a in assets}
    installer = by_name.get(INSTALLER_NAME.format(version=version))
    if installer and not INSTALLER_RE.fullmatch(installer.name):
        installer = None
    notes = data.get("body") if isinstance(data.get("body"), str) else ""
    if len(notes) > MAX_NOTES_CHARS:
        notes = notes[:MAX_NOTES_CHARS] + "\n\n…"
    html_url = data.get("html_url") if isinstance(data.get("html_url"), str) else RELEASES_LIST.format(repo=REPO)
    return UpdateInfo(version=version, tag=tag, notes=notes, html_url=html_url,
                      published_at=data.get("published_at") or "", installer=installer,
                      sums=by_name.get(SUMS_NAME), wheel=by_name.get(WHEEL_NAME.format(version=version)))


def parse_sums(text: str) -> Dict[str, str]:
    """``sha256sum`` output → {file name: lower-case hex}. ``<hex>  name`` and ``<hex> *name``; CRLF tolerated;
    malformed lines ignored; the same name listed with two different hashes is refused."""
    out: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or not _HEX64.match(parts[0]):
            continue
        name = parts[1].strip()
        if name.startswith("*"):
            name = name[1:]
        digest = parts[0].lower()
        if name in out and out[name] != digest:
            raise UpdateError(f"{SUMS_NAME} lists two different checksums for {name}", "integrity")
        out[name] = digest
    return out


# ---- network ------------------------------------------------------------------------------------------

Opener = Callable[..., Any]   # opener(request, timeout, redirect=True) → response (context manager: read, headers)


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme.lower() != "https":
            raise UpdateError("Refused a redirect to a non-HTTPS address.", "http")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None                       # urllib then raises HTTPError(code) carrying the Location header


def default_opener() -> Opener:
    import ssl
    ctx = ssl.create_default_context()    # Windows: the system ROOT and CA stores
    follow = urllib.request.build_opener(urllib.request.ProxyHandler(),
                                         urllib.request.HTTPSHandler(context=ctx), _HttpsOnlyRedirects())
    stay = urllib.request.build_opener(urllib.request.ProxyHandler(),
                                       urllib.request.HTTPSHandler(context=ctx), _NoRedirects())

    def open_(req: urllib.request.Request, timeout: float, redirect: bool = True):
        if urllib.parse.urlsplit(req.full_url).scheme.lower() != "https":
            raise UpdateError("Refused a non-HTTPS address.", "http")
        return (follow if redirect else stay).open(req, timeout=timeout)
    return open_


def _request(url: str, accept: str) -> urllib.request.Request:
    headers = {"User-Agent": f"SpecSurgeon/{current_version()} (+https://github.com/{REPO})", "Accept": accept}
    if url.startswith("https://api.github.com/"):
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    return urllib.request.Request(url, headers=headers)


def _rate_limit_message(headers) -> str:
    when = ""
    try:
        reset = int((headers or {}).get("X-RateLimit-Reset") or 0)
        if reset:
            when = " after " + time.strftime("%H:%M", time.localtime(reset))
    except (TypeError, ValueError):
        pass
    return f"GitHub is limiting update checks from your network; try again{when}."


def _is_rate_limited(e: urllib.error.HTTPError) -> bool:
    headers = e.headers or {}
    return e.code == 429 or (e.code == 403 and str(headers.get("X-RateLimit-Remaining", "")) == "0")


def _network_error(e: BaseException) -> UpdateError:
    if isinstance(e, UpdateError):
        return e
    if isinstance(e, urllib.error.HTTPError):
        if _is_rate_limited(e):
            return UpdateError(_rate_limit_message(e.headers), "rate_limited")
        return UpdateError(f"GitHub answered HTTP {e.code} ({e.reason}).", "http")
    import ssl
    reason = getattr(e, "reason", e)
    if isinstance(reason, ssl.SSLError) or isinstance(e, ssl.SSLError):
        return UpdateError("Couldn't verify GitHub's certificate. If your company inspects HTTPS traffic, its "
                           "certificate must be installed in Windows.", "tls")
    return UpdateError("Couldn't reach GitHub — check your internet connection or proxy.", "offline")


def _read_limited(resp, limit: int, what: str) -> bytes:
    data = resp.read(limit + 1)
    if len(data) > limit:
        raise UpdateError(f"{what} is larger than expected; not used.", "bad_release")
    return data


def _latest_from_redirect(repo: str, opener: Opener, timeout: float) -> Optional[UpdateInfo]:
    """The rate-limit fallback: ``github.com/<repo>/releases/latest`` redirects to ``…/releases/tag/<tag>``."""
    try:
        resp = opener(_request(RELEASES_PAGE.format(repo=repo), "text/html"), timeout, redirect=False)
        location = resp.headers.get("Location") if resp is not None else None
        if resp is not None and hasattr(resp, "close"):
            resp.close()
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308):
            raise _network_error(e) from e
        location = (e.headers or {}).get("Location")
    except UpdateError:
        raise
    except Exception as e:
        raise _network_error(e) from e
    if not location:
        return None
    tag = urllib.parse.unquote(location.rstrip("/").rsplit("/", 1)[-1])
    try:
        version = ".".join(str(n) for n in parse_version(tag))
    except ValueError:
        return None
    url = lambda name: DOWNLOAD_URL.format(repo=repo, tag=tag, name=name)  # noqa: E731
    installer_name = INSTALLER_NAME.format(version=version)
    wheel_name = WHEEL_NAME.format(version=version)
    return UpdateInfo(version=version, tag=tag, notes="", html_url=f"https://github.com/{repo}/releases/tag/{tag}",
                      installer=Asset(installer_name, url(installer_name)), sums=Asset(SUMS_NAME, url(SUMS_NAME)),
                      wheel=Asset(wheel_name, url(wheel_name)))


def check_latest(repo: str = REPO, *, timeout: float = 10.0, opener: Optional[Opener] = None) -> Optional[UpdateInfo]:
    """The newest published release, or None when the repository has none (or only drafts/prereleases)."""
    opener = opener or default_opener()
    try:
        with opener(_request(API_LATEST.format(repo=repo), "application/vnd.github+json"), timeout) as resp:
            raw = _read_limited(resp, MAX_API_BYTES, "GitHub's answer")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        if _is_rate_limited(e):
            try:
                info = _latest_from_redirect(repo, opener, timeout)
            except UpdateError:
                info = None               # the fallback failed too: report the rate limit, which is the cause
            if info is not None:
                return info
        raise _network_error(e) from e
    except UpdateError:
        raise
    except Exception as e:
        raise _network_error(e) from e
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise UpdateError("GitHub's answer could not be read.", "bad_release") from e
    return parse_release(data)


def check_for_update(*, current: Optional[str] = None, skipped: str = "", manual: bool = False,
                     repo: str = REPO, opener: Optional[Opener] = None, timeout: float = 10.0) -> CheckResult:
    """Compare the newest release with this copy. A version the user skipped is reported as current on an
    automatic check and as an update (``skipped=True``) on a manual one."""
    current = current or current_version()
    info = check_latest(repo, opener=opener, timeout=timeout)
    if info is None:
        return CheckResult("none", current)
    if not is_newer(info.version, current):
        return CheckResult("current", current, info)
    is_skipped = bool(skipped) and skipped == info.version
    if is_skipped and not manual:
        return CheckResult("current", current, info, skipped=True)
    return CheckResult("update", current, info, skipped=is_skipped)


def should_auto_check(s, now: Optional[float] = None) -> bool:
    """Once a day at most, for every copy (installed or pip): the user has nothing to set up or remember. The only
    off switch is ``SPEC_SURGEON_NO_UPDATE_CHECK`` (tests, screenshots, locked-down machines)."""
    if os.environ.get(NO_CHECK_ENV):
        return False
    now = time.time() if now is None else now
    elapsed = now - (s.last_update_check or 0.0)
    return not (0 <= elapsed < CHECK_INTERVAL)       # a clock that moved backwards also checks


def pip_python(executable: Optional[str] = None) -> str:
    """The interpreter this copy runs in (its own environment, on any platform); pythonw → python on Windows,
    because pythonw has no console to show pip's output."""
    exe = Path(executable or sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
        exe = exe.parent / "python.exe"
    return str(exe)


def update_instructions(info: UpdateInfo, executable: Optional[str] = None) -> str:
    """For a copy not installed by the setup program: how to update it by hand."""
    lines = [f"Spec Surgeon {info.version} is available.", ""]
    if info.wheel:
        lines += ["This copy was installed with pip. To update it, run:", "",
                  f'    "{pip_python(executable)}" -m pip install -U "spec-surgeon[gui,word,agent] @ {info.wheel.url}"',
                  ""]
    lines += ["The release page (with the Windows installer):", f"    {info.html_url}"]
    return "\n".join(lines)


# ---- download -----------------------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def verify_signature(path: Path) -> None:
    """Where an Authenticode check (WinVerifyTrust + expected publisher) goes once releases are signed. Raises
    ``UpdateError(kind="integrity")`` on failure. Today the installer is unsigned and this checks nothing."""
    return None


def _remove(*paths: Path) -> None:
    for p in paths:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def download_installer(info: UpdateInfo, dest_dir: Path, *, progress: Optional[Callable[[int, Optional[int]], None]] = None,
                       cancel: Optional[threading.Event] = None, opener: Optional[Opener] = None,
                       timeout: float = 30.0) -> DownloadedInstaller:
    """Download the release's installer into ``dest_dir`` and prove it is the file the release lists."""
    opener = opener or default_opener()
    inst = info.installer
    expected_name = INSTALLER_NAME.format(version=info.version)
    if not info.can_install or inst is None or inst.name != expected_name or not INSTALLER_RE.fullmatch(inst.name):
        raise UpdateError(f"Release {info.tag} has no installer named {expected_name} with a {SUMS_NAME} file.",
                          "bad_release")
    if inst.size is not None and not 0 < inst.size <= MAX_INSTALLER_BYTES:
        raise UpdateError(f"The release says its installer is {inst.size} bytes — not a size any Spec Surgeon "
                          "installer has; not downloaded.", "bad_release")
    # 1. the checksum list first — no installer byte is fetched unless its expected hash is known
    try:
        with opener(_request(info.sums.url, "application/octet-stream"), timeout) as resp:
            sums_text = _read_limited(resp, MAX_SUMS_BYTES, SUMS_NAME).decode("utf-8", "replace")
    except UpdateError:
        raise
    except Exception as e:
        raise _network_error(e) from e
    expected = parse_sums(sums_text).get(inst.name)
    if not expected:
        raise UpdateError(f"{SUMS_NAME} has no checksum for {inst.name}; the update was not downloaded.", "integrity")
    if inst.digest:
        algo, _, value = inst.digest.partition(":")
        if algo.lower() == "sha256" and value.lower() != expected:
            raise UpdateError(f"GitHub's checksum and {SUMS_NAME} disagree for {inst.name}; not downloaded.",
                              "integrity")
    # 2. the installer, hashed while it streams to a .part file
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / inst.name
    part = dest_dir / (inst.name + ".part")
    _remove(part)
    limit = inst.size if inst.size is not None else MAX_INSTALLER_BYTES
    h = hashlib.sha256()
    done = 0
    try:
        with opener(_request(inst.url, "application/octet-stream"), timeout) as resp:
            length = resp.headers.get("Content-Length") if getattr(resp, "headers", None) is not None else None
            total = inst.size
            if total is None and length and str(length).isdigit():
                total = int(length)
                if total > MAX_INSTALLER_BYTES:
                    raise UpdateError("The installer is larger than any Spec Surgeon installer should be.",
                                      "integrity")
                limit = total
            with open(part, "wb") as f:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise UpdateError("Download cancelled.", "cancelled")
                    block = resp.read(CHUNK)
                    if not block:
                        break
                    done += len(block)
                    if done > limit:
                        raise UpdateError("The download is larger than the release says; stopped.", "integrity")
                    h.update(block)
                    f.write(block)
                    if progress:
                        progress(done, total)
        if inst.size is not None and done != inst.size:
            raise UpdateError(f"The download is incomplete ({done} of {inst.size} bytes).", "integrity")
        if h.hexdigest() != expected:
            raise UpdateError(f"The downloaded installer does not match its {SUMS_NAME} checksum; deleted.",
                              "integrity")
        verify_signature(part)
        os.replace(part, final)
    except UpdateError:
        _remove(part, final)
        raise
    except Exception as e:
        _remove(part, final)
        raise _network_error(e) from e
    return DownloadedInstaller(path=final, sha256=expected, version=info.version)


def cleanup_downloads(dest_dir: Path, keep: Optional[Path] = None, keep_logs: int = 3) -> List[Path]:
    """Remove old installers and partial downloads; keep the newest install logs. A file still in use (Setup may
    still be finishing when the reopened app starts) is left for a later start."""
    dest_dir = Path(dest_dir)
    removed: List[Path] = []
    if not dest_dir.is_dir():
        return removed
    keep = Path(keep).resolve() if keep else None
    for p in list(dest_dir.glob("*.exe")) + list(dest_dir.glob("*.part")):
        if keep is not None and p.resolve() == keep:
            continue
        try:
            p.unlink()
            removed.append(p)
        except OSError:
            pass
    logs = sorted(dest_dir.glob("install-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in logs[keep_logs:]:
        try:
            p.unlink()
            removed.append(p)
        except OSError:
            pass
    return removed


# ---- install ------------------------------------------------------------------------------------------

def installer_args(*, install_dir: Path, log: Path) -> List[str]:
    """Inno Setup switches for an in-app update. /SILENT (a progress window, not /VERYSILENT — the app must not
    look like it crashed), no cancel, the running install folder, and /RELAUNCH=1 so Setup reopens the app."""
    return ["/SILENT", "/SP-", "/SUPPRESSMSGBOXES", "/NOCANCEL", "/NORESTART", "/CLOSEAPPLICATIONS",
            "/RELAUNCH=1", f'/DIR="{install_dir}"', f'/LOG="{log}"']


def installer_cmdline(path: Path, *, install_dir: Path, log: Path) -> str:
    """The Windows command line, quoted the way Inno documents it (``/DIR="x:\\dir"``). Windows paths cannot
    contain a double quote, so this quoting is unambiguous."""
    return " ".join([f'"{path}"'] + installer_args(install_dir=install_dir, log=log))


_STRIP_ENV = ("QT_PLUGIN_PATH", "QML2_IMPORT_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_QPA_PLATFORM",
              "TCL_LIBRARY", "TK_LIBRARY")


def installer_env(environ: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The environment Setup (and through it the reopened app) gets: no API key typed for this session, and none
    of this frozen process's runtime variables (the reopened app must start as a fresh top-level instance)."""
    env = dict(os.environ if environ is None else environ)
    for k in list(env):
        ku = k.upper()
        if ku == "ANTHROPIC_API_KEY" or ku.startswith(("_PYI_", "_MEI")) or ku in _STRIP_ENV:
            del env[k]
    return env


def launch_installer(dl: DownloadedInstaller, *, install_dir: Path, log: Path, popen=subprocess.Popen,
                     installed: Optional[bool] = None, environ: Optional[Dict[str, str]] = None):
    """Start Setup detached from this process. The caller quits right after (Setup waits for the app mutex)."""
    if not (is_installed_build() if installed is None else installed):
        raise UpdateError("This copy was not installed with the setup program, so it can't update itself. "
                          "Use the release page.", "not_supported")
    path = Path(dl.path)
    if not INSTALLER_RE.fullmatch(path.name) or not path.is_file() or sha256_file(path) != dl.sha256:
        raise UpdateError("The downloaded installer changed after it was checked; it was not started.", "integrity")
    verify_signature(path)
    cmd = installer_cmdline(path, install_dir=install_dir, log=log)
    kw = dict(cwd=str(path.parent), env=installer_env(environ), close_fds=True,
              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        return popen(cmd, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **kw)
    except OSError as e:
        if getattr(e, "winerror", None) != 5:     # a job object that forbids breakaway → start it without
            raise UpdateError(f"Could not start the installer: {e}", "not_supported") from e
    try:
        return popen(cmd, creationflags=flags, **kw)
    except OSError as e:
        raise UpdateError(f"Could not start the installer: {e}", "not_supported") from e
