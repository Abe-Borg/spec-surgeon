"""Release transactions on an in-memory API; no network or release writes."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("installer_publisher", ROOT / "packaging/windows/publish_installer.py")
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)
INSTALLER = "SpecSurgeon-1.0.0-Setup-x64.exe"
WHEEL = "spec_surgeon-1.0.0-py3-none-any.whl"
SDIST = "spec_surgeon-1.0.0.tar.gz"
BASE = {WHEEL: b"published wheel", SDIST: b"published source"}


def sums(files):
    return "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(files.items())).encode()


class FakeRelease:
    def __init__(self, files):
        self.items = {}
        self.next_id = 1
        self.attempts = []
        self.failures = []
        self.corrupt_stage = False
        self.on_download = None
        for name, data in files.items():
            self.add(name, data)

    def add(self, name, data):
        assert not any(asset["name"] == name for asset in self.items.values())
        asset = {"id": self.next_id, "name": name, "size": len(data), "data": data}
        self.next_id += 1
        self.items[asset["id"]] = asset
        return asset

    def assets(self):
        return [{k: v for k, v in asset.items() if k != "data"} for asset in self.items.values()]

    def download(self, asset):
        data = self.items[asset["id"]]["data"]
        if self.on_download:
            self.on_download(self, asset)
        return data

    def fault(self, operation, target, source="", *, after=False):
        for failure in self.failures:
            if (failure["remaining"] and failure["operation"] == operation and failure["after"] == after
                    and target.startswith(failure["target"]) and source.startswith(failure["source"])):
                failure["remaining"] -= 1
                raise OSError("injected publication failure")

    def fail(self, operation, target, *, source="", after=False, times=1):
        self.failures.append(dict(operation=operation, target=target, source=source, after=after, remaining=times))

    def upload(self, name, data):
        self.attempts.append(("upload", name))
        self.fault("upload", name)
        self.add(name, b"corrupt stage" if self.corrupt_stage and name.startswith(publisher.STAGED) else data)
        self.fault("upload", name, after=True)

    def rename(self, asset, name):
        source = self.items[asset["id"]]["name"]
        self.attempts.append(("rename", source, name))
        self.fault("rename", name, source)
        assert not any(a["name"] == name and a["id"] != asset["id"] for a in self.items.values())
        self.items[asset["id"]]["name"] = name
        self.fault("rename", name, source, after=True)

    def contents(self):
        return {asset["name"]: asset["data"] for asset in self.items.values()}


@pytest.fixture
def local_installer(tmp_path):
    path = tmp_path / INSTALLER
    path.write_bytes(b"newly built installer")
    return path


def release(*, installer=True, canonical=True):
    files = dict(BASE)
    if installer:
        files[INSTALLER] = b"immutable published installer"
    if canonical:
        files[publisher.SUMS] = sums(BASE)
    return FakeRelease(files)


def publish(api, path, **kwargs):
    return publisher.publish(api, INSTALLER, path, **kwargs)


def assert_complete(api, installer=b"immutable published installer"):
    expected = dict(BASE, **{INSTALLER: installer})
    contents = api.contents()
    assert contents[publisher.SUMS] == sums(expected)
    assert {name: contents[name] for name in expected} == expected
    assert not any(name.startswith((publisher.STAGED, publisher.BACKUP, publisher.FAILED))
                   for name in publisher.parse_manifest(contents[publisher.SUMS]))


def test_existing_installer_repairs_missing_entry_using_remote_bytes_and_retains_backup(local_installer):
    api = release()
    old = api.contents()[publisher.SUMS]
    assert "verified" in publish(api, local_installer)
    assert_complete(api)
    assert not any(call == ("upload", INSTALLER) for call in api.attempts)
    assert old in [data for name, data in api.contents().items() if name.startswith(publisher.BACKUP)]


def test_unchanged_verified_release_is_a_noop(local_installer):
    api = release()
    publish(api, local_installer)
    before = api.contents()
    api.attempts.clear()
    assert "Installer and checksums verified" in publish(api, local_installer)
    assert api.contents() == before and api.attempts == []


@pytest.mark.parametrize("after", [False, True])
def test_stage_upload_failure_keeps_canonical_and_retry_repairs_after_installer_upload(local_installer, after):
    api = release(installer=False)
    old = api.contents()[publisher.SUMS]
    api.fail("upload", publisher.STAGED, after=after)
    with pytest.raises(OSError, match="injected"):
        publish(api, local_installer, allow_installer_upload=True)
    assert api.contents()[publisher.SUMS] == old
    assert api.contents()[INSTALLER] == local_installer.read_bytes()
    original_installer = local_installer.read_bytes()
    local_installer.write_bytes(b"a different master rebuild must not replace the published installer")
    publish(api, local_installer)
    assert_complete(api, original_installer)
    assert api.attempts.count(("upload", INSTALLER)) == 1


def test_missing_installer_requires_upload_permission(local_installer):
    api = release(installer=False)
    with pytest.raises(publisher.PublicationError, match="exact tagged build"):
        publish(api, local_installer)
    assert api.attempts == []


def test_lost_successful_installer_upload_response_does_not_duplicate_upload(local_installer):
    api = release(installer=False)
    api.fail("upload", INSTALLER, after=True)
    publish(api, local_installer, allow_installer_upload=True)
    assert_complete(api, local_installer.read_bytes())
    assert api.attempts.count(("upload", INSTALLER)) == 1


@pytest.mark.parametrize("after", [False, True])
def test_old_canonical_rename_failure_is_recoverable_without_losing_bytes(local_installer, after):
    api = release()
    old = api.contents()[publisher.SUMS]
    api.fail("rename", publisher.BACKUP, source=publisher.SUMS, after=after)
    with pytest.raises(publisher.PublicationError, match="retained for retry"):
        publish(api, local_installer)
    assert api.contents()[publisher.SUMS] == old
    publish(api, local_installer)
    assert_complete(api)


def test_failed_promotion_rolls_back_and_reuses_staged_bytes_on_retry(local_installer):
    api = release()
    old = api.contents()[publisher.SUMS]
    api.fail("rename", publisher.SUMS, source=publisher.STAGED)
    with pytest.raises(publisher.PublicationError, match="retained for retry"):
        publish(api, local_installer)
    assert api.contents()[publisher.SUMS] == old
    stage_uploads = [call for call in api.attempts if call[0] == "upload"]
    publish(api, local_installer)
    assert_complete(api)
    assert [call for call in api.attempts if call[0] == "upload"] == stage_uploads


def test_lost_successful_promotion_response_is_verified_as_success(local_installer):
    api = release()
    api.fail("rename", publisher.SUMS, source=publisher.STAGED, after=True)
    assert "lost rename response" in publish(api, local_installer)
    assert_complete(api)


def test_failed_rollback_preserves_backup_and_stage_for_next_retry(local_installer):
    api = release()
    old = api.contents()[publisher.SUMS]
    api.fail("rename", publisher.SUMS, source=publisher.STAGED)
    api.fail("rename", publisher.SUMS, source=publisher.BACKUP)
    with pytest.raises(publisher.PublicationError, match="available for recovery on retry"):
        publish(api, local_installer)
    contents = api.contents()
    assert publisher.SUMS not in contents
    assert old in [data for name, data in contents.items() if name.startswith(publisher.BACKUP)]
    assert any(name.startswith(publisher.STAGED) for name in contents)
    publish(api, local_installer)
    assert_complete(api)


def test_missing_canonical_recovers_backup_before_new_stage_upload(local_installer):
    api = release(canonical=False)
    old = sums(BASE)
    api.add(publisher.BACKUP + "previous", old)
    api.fail("upload", publisher.STAGED)
    with pytest.raises(OSError):
        publish(api, local_installer)
    assert api.contents()[publisher.SUMS] == old
    publish(api, local_installer)
    assert_complete(api)


def test_legacy_missing_canonical_reconstructs_all_actual_assets(local_installer):
    api = release(canonical=False)
    api.add("release-notes.txt", b"also retain noninstaller entries")
    publish(api, local_installer)
    entries = publisher.parse_manifest(api.contents()[publisher.SUMS])
    assert set(entries) == {WHEEL, SDIST, INSTALLER, "release-notes.txt"}
    for name, digest in entries.items():
        assert digest == hashlib.sha256(api.contents()[name]).hexdigest()


@pytest.mark.parametrize("name", [WHEEL, SDIST, INSTALLER])
@pytest.mark.parametrize("backup_only", [False, True])
def test_existing_manifest_never_blesses_changed_published_bytes(local_installer, name, backup_only):
    api = release(canonical=False)
    previous = sums(dict(BASE, **{INSTALLER: b"immutable published installer"}))
    api.add(publisher.BACKUP + "previous" if backup_only else publisher.SUMS, previous)
    asset = api.items[publisher.named(api.assets(), name)["id"]]
    asset["data"] = b"tampered published bytes"
    asset["size"] = len(asset["data"])
    with pytest.raises(publisher.PublicationError, match="prior checksum"):
        publish(api, local_installer)
    assert api.attempts == []


def test_deleted_previously_checksummed_asset_is_not_dropped(local_installer):
    api = release()
    del api.items[publisher.named(api.assets(), WHEEL)["id"]]
    with pytest.raises(publisher.PublicationError, match=WHEEL):
        publish(api, local_installer)
    assert api.attempts == []


@pytest.mark.parametrize("name", [WHEEL, SDIST])
def test_prior_assets_are_verified_before_adding_a_missing_installer(local_installer, name):
    api = release(installer=False)
    asset = api.items[publisher.named(api.assets(), name)["id"]]
    asset["data"] = b"changed existing distribution"
    asset["size"] = len(asset["data"])
    with pytest.raises(publisher.PublicationError, match="prior checksum"):
        publish(api, local_installer, allow_installer_upload=True)
    assert api.attempts == [] and INSTALLER not in api.contents()


def test_invalid_staged_upload_is_not_promoted_and_retry_quarantines_it(local_installer):
    api = release()
    old = api.contents()[publisher.SUMS]
    api.corrupt_stage = True
    with pytest.raises(publisher.PublicationError, match="did not verify"):
        publish(api, local_installer)
    assert api.contents()[publisher.SUMS] == old and all(call[0] != "rename" for call in api.attempts)
    api.corrupt_stage = False
    publish(api, local_installer)
    assert_complete(api)
    assert b"corrupt stage" in [data for name, data in api.contents().items() if name.startswith(publisher.FAILED)]


def test_distribution_inventory_drift_stops_before_checksum_mutations(local_installer):
    api = release()

    def change_inventory(api, asset):
        if asset["name"] == WHEEL:
            api.on_download = None
            del api.items[asset["id"]]
            api.add(WHEEL, b"a replacement with a new asset id")
    api.on_download = change_inventory
    with pytest.raises(publisher.PublicationError, match="inventory changed"):
        publish(api, local_installer)
    assert api.attempts == []


@pytest.mark.parametrize("metadata", ["size", "digest"])
def test_bootstrap_rejects_downloads_that_conflict_with_github_metadata(local_installer, metadata):
    api = release(canonical=False)
    asset = api.items[publisher.named(api.assets(), WHEEL)["id"]]
    asset[metadata] = asset["size"] + 1 if metadata == "size" else "sha256:" + "0" * 64
    with pytest.raises(publisher.PublicationError, match="wrong size|GitHub digest"):
        publish(api, local_installer)
    assert api.attempts == []


def test_github_cli_uses_binary_download_and_rename_without_destructive_upload(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if "--method" in argv:
            return SimpleNamespace(stdout=b'{}')
        if "upload" in argv:
            assert Path(argv[4]).read_bytes() == b"binary payload\x00\xff"
            return SimpleNamespace(stdout=b'')
        if "/releases/tags/" in argv[2]:
            return SimpleNamespace(stdout=json.dumps({"assets": [{"id": 7, "name": INSTALLER}]}).encode())
        return SimpleNamespace(stdout=b"binary payload\x00\xff")
    monkeypatch.setattr(publisher.subprocess, "run", run)
    api = publisher.GitHubCLI("owner/repo", "v1.0.0")
    asset = api.assets()[0]
    assert api.download(asset) == b"binary payload\x00\xff"
    api.upload(INSTALLER, b"binary payload\x00\xff")
    api.rename(asset, publisher.BACKUP + "7")
    assert calls[1][0] == ["gh", "api", "repos/owner/repo/releases/assets/7", "-H", "Accept: application/octet-stream"]
    assert calls[2][0][:4] == ["gh", "release", "upload", "v1.0.0"]
    assert "--clobber" not in calls[2][0] and calls[2][0][-2:] == ["--repo", "owner/repo"]
    assert calls[3][0] == ["gh", "api", "--method", "PATCH", "repos/owner/repo/releases/assets/7",
                           "-f", "name=SHA256SUMS.backup-7"]
    assert all(call[1] == {"capture_output": True, "check": True} for call in calls)
