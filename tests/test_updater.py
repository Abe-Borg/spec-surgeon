"""The updater (``spec_surgeon.updater``): release parsing, the checks every download must pass, and how Setup is
started — all without a network (a scripted opener) and without starting anything (a recording popen)."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import ssl
import subprocess
import threading
import time
import urllib.error
from pathlib import Path

import pytest

from spec_surgeon import updater
from spec_surgeon.settings import Settings
from spec_surgeon.updater import (Asset, DownloadedInstaller, UpdateError, UpdateInfo, check_for_update, check_latest,
                                  cleanup_downloads, download_installer, installer_args, installer_cmdline,
                                  installer_env, is_newer, launch_installer, parse_release, parse_sums,
                                  parse_version, should_auto_check, update_instructions)

RELEASE = json.loads((Path(__file__).parent / "data" / "github_release_v0.3.0.json").read_text(encoding="utf-8"))
API = updater.API_LATEST.format(repo=updater.REPO)


class FakeResp:
    def __init__(self, body: bytes, headers=None, status=200):
        self._f = io.BytesIO(body)
        self.headers = headers or {}
        self.status = status

    def read(self, n=-1):
        return self._f.read(n)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeNet:
    """url → bytes | (status, headers, bytes) | Exception. Records every request."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def __call__(self, req, timeout, redirect=True):
        self.requests.append((req.full_url, dict(req.header_items()), redirect))
        r = self.routes.get(req.full_url)
        if r is None:
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        if isinstance(r, BaseException):
            raise r
        status, headers, body = r if isinstance(r, tuple) else (200, {}, r)
        if status >= 300:
            raise urllib.error.HTTPError(req.full_url, status, "status", headers, None)
        return FakeResp(body, headers, status)

    def urls(self):
        return [u for u, _, _ in self.requests]


def release_with(version="9.9.9", installer_bytes=b"MZ fake installer" * 1000, sums_text=None, digest=True,
                 size=None, extra=None):
    """A release dict for ``version`` plus the routes that serve its installer and SHA256SUMS."""
    name = f"SpecSurgeon-{version}-Setup-x64.exe"
    sha = hashlib.sha256(installer_bytes).hexdigest()
    base = f"https://github.com/Abe-Borg/spec-surgeon/releases/download/v{version}/"
    sums_text = sums_text if sums_text is not None else f"{'0' * 64}  spec_surgeon-{version}.tar.gz\n{sha}  {name}\n"
    data = copy.deepcopy(RELEASE)
    data.update(tag_name=f"v{version}", html_url=f"https://github.com/Abe-Borg/spec-surgeon/releases/tag/v{version}",
                body="## What's new\n- things")
    data["assets"] = [
        {"name": "SHA256SUMS", "size": len(sums_text), "browser_download_url": base + "SHA256SUMS"},
        {"name": name, "size": len(installer_bytes) if size is None else size,
         "digest": f"sha256:{sha}" if digest is True else digest, "browser_download_url": base + name},
        {"name": f"spec_surgeon-{version}-py3-none-any.whl", "size": 10,
         "browser_download_url": base + f"spec_surgeon-{version}-py3-none-any.whl"},
    ]
    if extra:
        data.update(extra)
    routes = {API: json.dumps(data).encode(), base + "SHA256SUMS": sums_text.encode(), base + name: installer_bytes}
    return data, routes, sha


# ---- versions and parsing ---------------------------------------------------------------------------------

def test_versions():
    assert parse_version("v0.4.0") == (0, 4, 0) and parse_version("0.4.0") == (0, 4, 0)
    for bad in ("v0.4.0-rc1", "v1.2", "", "vX", "0.4.0.1", None):
        with pytest.raises((ValueError, AttributeError, TypeError)):
            parse_version(bad)
    assert is_newer("0.10.0", "0.9.9") and is_newer("v1.0.0", "0.99.99")
    assert not is_newer("0.4.0", "0.4.0") and not is_newer("0.3.9", "0.4.0") and not is_newer("garbage", "0.1.0")


def test_parse_the_real_v030_release():
    info = parse_release(RELEASE)
    assert info.version == "0.3.0" and info.tag == "v0.3.0" and info.can_install
    assert info.installer.name == "SpecSurgeon-0.3.0-Setup-x64.exe" and info.installer.size == 54632846
    assert info.installer.digest == "sha256:9506189b8a37d5ce9496cdccbdc1ac24c2425f5c2190698e3c76698996de7310"
    assert info.installer.url.startswith("https://github.com/Abe-Borg/spec-surgeon/releases/download/v0.3.0/")
    assert info.sums.name == "SHA256SUMS" and info.wheel.name == "spec_surgeon-0.3.0-py3-none-any.whl"
    assert info.html_url.endswith("/releases/tag/v0.3.0") and info.notes


def test_parse_release_refuses_what_must_not_be_offered():
    for change in ({"draft": True}, {"prerelease": True}, {"tag_name": "v0.4.0-beta"}, {"tag_name": "nightly"}):
        assert parse_release({**RELEASE, **change}) is None
    assert parse_release(None) is None and parse_release([]) is None
    no_installer = {**RELEASE, "assets": [a for a in RELEASE["assets"] if not a["name"].endswith(".exe")]}
    assert parse_release(no_installer).can_install is False
    no_sums = {**RELEASE, "assets": [a for a in RELEASE["assets"] if a["name"] != "SHA256SUMS"]}
    assert parse_release(no_sums).can_install is False
    # an installer named for another version, or with path characters, is never picked
    wrong = copy.deepcopy(RELEASE)
    for a in wrong["assets"]:
        if a["name"].endswith(".exe"):
            a["name"] = "SpecSurgeon-0.2.9-Setup-x64.exe"
    assert parse_release(wrong).installer is None
    sneaky = copy.deepcopy(RELEASE)
    for a in sneaky["assets"]:
        if a["name"].endswith(".exe"):
            a["name"] = "..\\SpecSurgeon-0.3.0-Setup-x64.exe"
    assert parse_release(sneaky).installer is None
    long_notes = parse_release({**RELEASE, "body": "x" * (updater.MAX_NOTES_CHARS + 50)}).notes
    assert len(long_notes) < updater.MAX_NOTES_CHARS + 10


def test_parse_sums():
    a, b = "a" * 64, "B" * 64
    text = f"{a}  one.exe\r\n{b} *two.whl\r\ngarbage line\n\n# comment\n{'c' * 63}  short.exe\n"
    assert parse_sums(text) == {"one.exe": a, "two.whl": b.lower()}
    assert parse_sums(f"{a}  x.exe\n{a}  x.exe\n") == {"x.exe": a}       # a repeated identical line is fine
    with pytest.raises(UpdateError) as e:
        parse_sums(f"{a}  x.exe\n{b}  x.exe\n")
    assert e.value.kind == "integrity"
    assert "SpecSurgeon-1.0.0-Setup-x64.exe" not in parse_sums(f"{a}  x-SpecSurgeon-1.0.0-Setup-x64.exe\n")


# ---- checking ----------------------------------------------------------------------------------------------

def test_check_latest_request_and_answer():
    net = FakeNet({API: json.dumps(RELEASE).encode()})
    info = check_latest(opener=net)
    assert info.version == "0.3.0"
    url, headers, _ = net.requests[0]
    assert url == API
    headers = {k.lower(): v for k, v in headers.items()}
    assert headers["accept"] == "application/vnd.github+json"
    assert headers["user-agent"].startswith("SpecSurgeon/") and headers["x-github-api-version"]


def test_check_latest_errors_are_plain():
    assert check_latest(opener=FakeNet({})) is None                       # 404: nothing published yet
    reset = int(time.time()) + 600
    limited = FakeNet({API: (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)}, b"")})
    with pytest.raises(UpdateError) as e:
        check_latest(opener=limited)
    assert e.value.kind == "rate_limited" and "try again after" in str(e.value)
    with pytest.raises(UpdateError) as e:
        check_latest(opener=FakeNet({API: urllib.error.URLError(OSError("no route"))}))
    assert e.value.kind == "offline" and "connection" in str(e.value)
    with pytest.raises(UpdateError) as e:
        check_latest(opener=FakeNet({API: urllib.error.URLError(ssl.SSLCertVerificationError("bad cert"))}))
    assert e.value.kind == "tls" and "certificate" in str(e.value)
    with pytest.raises(UpdateError) as e:
        check_latest(opener=FakeNet({API: b"<html>not json"}))
    assert e.value.kind == "bad_release"
    with pytest.raises(UpdateError) as e:
        check_latest(opener=FakeNet({API: (500, {}, b"")}))
    assert e.value.kind == "http"


def test_rate_limited_api_falls_back_to_the_releases_redirect():
    page = updater.RELEASES_PAGE.format(repo=updater.REPO)
    net = FakeNet({API: (403, {"X-RateLimit-Remaining": "0"}, b""),
                   page: (302, {"Location": "https://github.com/Abe-Borg/spec-surgeon/releases/tag/v1.2.3"}, b"")})
    info = check_latest(opener=net)
    assert info.version == "1.2.3" and info.can_install and info.notes == ""
    assert info.installer.url == ("https://github.com/Abe-Borg/spec-surgeon/releases/download/v1.2.3/"
                                  "SpecSurgeon-1.2.3-Setup-x64.exe")
    assert info.installer.size is None and info.installer.digest is None
    assert net.requests[1][0] == page and net.requests[1][2] is False      # not followed


def test_check_for_update_and_skipped_versions():
    data, routes, _ = release_with("9.9.9")
    net = FakeNet(routes)
    r = check_for_update(current="0.4.0", opener=net)
    assert r.status == "update" and r.info.version == "9.9.9" and not r.skipped
    assert check_for_update(current="9.9.9", opener=net).status == "current"
    assert check_for_update(current="10.0.0", opener=net).status == "current"
    auto = check_for_update(current="0.4.0", skipped="9.9.9", opener=net)
    assert auto.status == "current" and auto.skipped
    manual = check_for_update(current="0.4.0", skipped="9.9.9", manual=True, opener=net)
    assert manual.status == "update" and manual.skipped
    assert check_for_update(current="0.4.0", opener=FakeNet({})).status == "none"


def test_should_auto_check(monkeypatch):
    monkeypatch.delenv(updater.NO_CHECK_ENV, raising=False)
    now = 1_800_000_000.0
    assert should_auto_check(Settings(auto_check_updates=True), now)
    assert not should_auto_check(Settings(auto_check_updates=False), now)
    assert not should_auto_check(Settings(auto_check_updates=True, last_update_check=now - 23 * 3600), now)
    assert should_auto_check(Settings(auto_check_updates=True, last_update_check=now - 25 * 3600), now)
    assert should_auto_check(Settings(auto_check_updates=True, last_update_check=now + 3600), now)   # clock went back
    assert should_auto_check(Settings(), now, installed=True)
    assert not should_auto_check(Settings(), now, installed=False)
    assert not should_auto_check(Settings(), now)              # a source checkout is not an installed build
    monkeypatch.setenv(updater.NO_CHECK_ENV, "1")
    assert not should_auto_check(Settings(auto_check_updates=True), now)


# ---- download ----------------------------------------------------------------------------------------------

def test_download_happy_path(tmp_path):
    payload = bytes(range(256)) * 3000                 # > one chunk, so progress is reported more than once
    data, routes, sha = release_with("9.9.9", payload)
    seen = []
    dl = download_installer(parse_release(data), tmp_path, opener=FakeNet(routes),
                            progress=lambda done, total: seen.append((done, total)))
    assert dl.path == tmp_path / "SpecSurgeon-9.9.9-Setup-x64.exe" and dl.path.read_bytes() == payload
    assert dl.sha256 == sha and dl.version == "9.9.9"
    assert not list(tmp_path.glob("*.part"))
    assert [d for d, _ in seen] == sorted(d for d, _ in seen) and seen[-1] == (len(payload), len(payload))
    assert len(seen) > 1


@pytest.mark.parametrize("case", ["bad-hash", "short", "long", "no-line", "digest-mismatch", "no-digest-ok"])
def test_download_refusals_leave_nothing(tmp_path, case):
    payload = b"MZ real installer bytes" * 500
    kw = {}
    if case == "digest-mismatch":
        kw["digest"] = "sha256:" + "f" * 64
    if case == "no-digest-ok":
        kw["digest"] = None
    if case == "no-line":
        kw["sums_text"] = f"{'a' * 64}  something-else.exe\n"
    data, routes, sha = release_with("9.9.9", payload, **kw)
    url = data["assets"][1]["browser_download_url"]
    if case == "bad-hash":
        routes[url] = payload[:-1] + b"X"
    if case == "short":
        routes[url] = payload[:-10]
    if case == "long":
        routes[url] = payload + b"extra bytes"
    net = FakeNet(routes)
    if case == "no-digest-ok":
        assert download_installer(parse_release(data), tmp_path, opener=net).path.is_file()
        return
    with pytest.raises(UpdateError) as e:
        download_installer(parse_release(data), tmp_path, opener=net)
    assert e.value.kind == "integrity"
    assert list(tmp_path.iterdir()) == []
    if case in ("no-line", "digest-mismatch"):
        assert url not in net.urls()                   # no installer byte fetched without a known, agreed hash


def test_download_refuses_a_release_it_cannot_prove(tmp_path):
    data, routes, _ = release_with("9.9.9")
    no_sums = {**data, "assets": [a for a in data["assets"] if a["name"] != "SHA256SUMS"]}
    with pytest.raises(UpdateError) as e:
        download_installer(parse_release(no_sums), tmp_path, opener=FakeNet(routes))
    assert e.value.kind == "bad_release"
    huge = "a" * 64 + "  x\n"
    data2, routes2, _ = release_with("9.9.9", sums_text=huge * (updater.MAX_SUMS_BYTES // len(huge) + 2))
    with pytest.raises(UpdateError):
        download_installer(parse_release(data2), tmp_path, opener=FakeNet(routes2))
    assert list(tmp_path.iterdir()) == []


def test_download_cancel(tmp_path):
    payload = b"x" * (updater.CHUNK * 4)
    data, routes, _ = release_with("9.9.9", payload)
    stop = threading.Event()

    def progress(done, total):
        if done >= updater.CHUNK:
            stop.set()
    with pytest.raises(UpdateError) as e:
        download_installer(parse_release(data), tmp_path, opener=FakeNet(routes), cancel=stop, progress=progress)
    assert e.value.kind == "cancelled" and list(tmp_path.iterdir()) == []


def test_download_without_a_known_size_uses_content_length(tmp_path):
    payload = b"MZ" * 1000
    data, routes, _ = release_with("9.9.9", payload)
    info = parse_release(data)
    info = UpdateInfo(**{**info.__dict__, "installer": Asset(info.installer.name, info.installer.url)})
    routes[info.installer.url] = (200, {"Content-Length": str(len(payload))}, payload)
    assert download_installer(info, tmp_path, opener=FakeNet(routes)).path.read_bytes() == payload
    routes[info.installer.url] = (200, {"Content-Length": str(updater.MAX_INSTALLER_BYTES + 1)}, payload)
    with pytest.raises(UpdateError):
        download_installer(info, tmp_path / "b", opener=FakeNet(routes))


def test_https_only():
    import urllib.request
    h = updater._HttpsOnlyRedirects()
    req = urllib.request.Request("https://github.com/x")
    with pytest.raises(UpdateError):
        h.redirect_request(req, None, 302, "Found", {}, "http://evil.example/x")
    assert h.redirect_request(req, None, 302, "Found", {}, "https://objects.githubusercontent.com/x") is not None
    with pytest.raises(UpdateError):
        updater.default_opener()(urllib.request.Request("file:///etc/passwd"), 1)
    with pytest.raises(UpdateError):
        updater.default_opener()(urllib.request.Request("http://github.com/"), 1)


def test_cleanup_downloads(tmp_path):
    keep = tmp_path / "SpecSurgeon-2.0.0-Setup-x64.exe"
    for name in ("SpecSurgeon-1.0.0-Setup-x64.exe", "SpecSurgeon-1.0.0-Setup-x64.exe.part", keep.name):
        (tmp_path / name).write_bytes(b"x")
    for i in range(5):
        log = tmp_path / f"install-1.0.{i}.log"
        log.write_text("log")
        t = time.time() - 100 + i
        import os
        os.utime(log, (t, t))
    removed = cleanup_downloads(tmp_path, keep=keep)
    left = sorted(p.name for p in tmp_path.iterdir())
    assert keep.name in left and "SpecSurgeon-1.0.0-Setup-x64.exe" not in left
    assert [n for n in left if n.endswith(".log")] == ["install-1.0.2.log", "install-1.0.3.log", "install-1.0.4.log"]
    assert len(removed) == 4
    assert cleanup_downloads(tmp_path / "missing") == []


def test_update_instructions_for_a_pip_copy():
    text = update_instructions(parse_release(RELEASE))
    assert "pip install -U" in text and "spec_surgeon-0.3.0-py3-none-any.whl" in text and "releases/tag/v0.3.0" in text


# ---- install -----------------------------------------------------------------------------------------------

def test_installer_command_line():
    d = Path(r"C:\Users\Abe Borg\AppData\Local\Programs\Spec Surgeon – test")
    log = Path(r"C:\Users\Abe Borg\AppData\Local\Spec Surgeon\updates\install-9.9.9.log")
    args = installer_args(install_dir=d, log=log)
    assert args[:7] == ["/SILENT", "/SP-", "/SUPPRESSMSGBOXES", "/NOCANCEL", "/NORESTART", "/CLOSEAPPLICATIONS",
                        "/RELAUNCH=1"]
    assert f'/DIR="{d}"' in args and f'/LOG="{log}"' in args
    exe = Path(r"C:\Users\Abe Borg\AppData\Local\Spec Surgeon\updates\SpecSurgeon-9.9.9-Setup-x64.exe")
    cmd = installer_cmdline(exe, install_dir=d, log=log)
    assert cmd.startswith(f'"{exe}" /SILENT ') and cmd.endswith(f'/LOG="{log}"')


def test_installer_env_strips_the_key_and_runtime_variables():
    env = installer_env({"PATH": "p", "ANTHROPIC_API_KEY": "sk-ant-secret", "anthropic_api_key": "x",
                         "_PYI_APPLICATION_HOME_DIR": "a", "_MEIPASS2": "b", "QT_PLUGIN_PATH": "q", "HOME": "h"})
    assert env == {"PATH": "p", "HOME": "h"}


class Popen:
    def __init__(self, fail_first=None):
        self.calls = []
        self.fail_first = fail_first

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if self.fail_first is not None and len(self.calls) == 1:
            raise self.fail_first
        return "proc"


def _downloaded(tmp_path, content=b"MZ installer"):
    p = tmp_path / "SpecSurgeon-9.9.9-Setup-x64.exe"
    p.write_bytes(content)
    return DownloadedInstaller(p, hashlib.sha256(content).hexdigest(), "9.9.9")


def test_launch_installer(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    dl = _downloaded(tmp_path)
    popen = Popen()
    with pytest.raises(UpdateError) as e:
        launch_installer(dl, install_dir=tmp_path / "app", log=tmp_path / "l.log", popen=popen, installed=False)
    assert e.value.kind == "not_supported" and popen.calls == []
    assert launch_installer(dl, install_dir=tmp_path / "app", log=tmp_path / "l.log", popen=popen,
                            installed=True) == "proc"
    cmd, kw = popen.calls[0]
    assert "/RELAUNCH=1" in cmd and f'/DIR="{tmp_path / "app"}"' in cmd
    flags = kw["creationflags"]
    assert flags & updater.DETACHED_PROCESS and flags & updater.CREATE_NEW_PROCESS_GROUP
    assert flags & updater.CREATE_BREAKAWAY_FROM_JOB
    assert kw["cwd"] == str(tmp_path) and kw["close_fds"] and kw["stdin"] == subprocess.DEVNULL
    assert "ANTHROPIC_API_KEY" not in kw["env"]


def test_launch_installer_refuses_a_file_changed_after_the_check(tmp_path):
    dl = _downloaded(tmp_path)
    dl.path.write_bytes(b"MZ something else")
    popen = Popen()
    with pytest.raises(UpdateError) as e:
        launch_installer(dl, install_dir=tmp_path, log=tmp_path / "l.log", popen=popen, installed=True)
    assert e.value.kind == "integrity" and popen.calls == []
    renamed = DownloadedInstaller(tmp_path / "evil.exe", dl.sha256, "9.9.9")
    with pytest.raises(UpdateError):
        launch_installer(renamed, install_dir=tmp_path, log=tmp_path / "l.log", popen=popen, installed=True)


def test_launch_installer_without_breakaway_when_the_job_forbids_it(tmp_path):
    denied = OSError("access denied")
    denied.winerror = 5
    popen = Popen(fail_first=denied)
    launch_installer(_downloaded(tmp_path), install_dir=tmp_path, log=tmp_path / "l.log", popen=popen, installed=True)
    assert len(popen.calls) == 2
    assert not popen.calls[1][1]["creationflags"] & updater.CREATE_BREAKAWAY_FROM_JOB
    other = OSError("no")
    other.winerror = 2
    with pytest.raises(UpdateError):
        launch_installer(_downloaded(tmp_path), install_dir=tmp_path, log=tmp_path / "l.log",
                         popen=Popen(fail_first=other), installed=True)


def test_a_source_checkout_is_not_an_installed_build():
    assert updater.is_installed_build() is False
