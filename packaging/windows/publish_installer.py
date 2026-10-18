"""Publish an immutable installer and repair checksums with a recoverable rename.

Only CI invokes the real ``gh`` client. No checksum asset is deleted or clobbered:
the verified replacement is staged first, and the previous canonical asset remains
available under a backup name until (and after) the replacement is canonical.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import quote


SUMS = "SHA256SUMS"
STAGED = SUMS + ".staged-"
BACKUP = SUMS + ".backup-"
FAILED = SUMS + ".failed-"


class PublicationError(RuntimeError):
    pass


def named(assets, name):
    matches = [asset for asset in assets if asset["name"] == name]
    if len(matches) > 1:
        raise PublicationError(f"Multiple release assets named {name}")
    return matches[0] if matches else None


def parse_manifest(data):
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None
    result = {}
    for line in lines:
        match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *]([^/\\\r\n]+)", line)
        if match is None:
            return None
        if match[2] in result:
            return None
        result[match[2]] = match[1].lower()
    return result or None


def distribution(asset):
    name = asset["name"]
    return name != SUMS and not name.startswith((STAGED, BACKUP, FAILED))


def inventory(assets):
    return sorted((asset["id"], asset["name"], asset.get("size")) for asset in assets if distribution(asset))


def manifest(api, assets, previous):
    # Reconstruct from actual release bytes even if an earlier --clobber removed
    # the only checksum file. Never lose wheel/sdist entries while repairing .exe.
    distributions = sorted((asset for asset in assets if distribution(asset)), key=lambda asset: asset["name"])
    if not distributions:
        raise PublicationError("The release has no distribution assets to checksum")
    actual = {}
    for asset in distributions:
        name = asset["name"]
        if Path(name).name != name or "\\" in name or "\n" in name or "\r" in name:
            raise PublicationError("Distribution names must be plain filenames")
        if name in actual:
            raise PublicationError(f"Multiple release distributions named {name}")
        data = api.download(asset)
        if not data:
            raise PublicationError(f"Empty release distribution: {name}")
        if asset.get("size") is not None and len(data) != asset["size"]:
            raise PublicationError(f"Downloaded release distribution has the wrong size: {name}")
        digest = hashlib.sha256(data).hexdigest()
        reported = asset.get("digest")
        if reported is not None and reported != "sha256:" + digest:
            raise PublicationError(f"Downloaded release distribution does not match its GitHub digest: {name}")
        actual[name] = digest
    for name, digest in (previous or {}).items():
        if distribution({"name": name}) and actual.get(name) != digest:
            raise PublicationError(f"Published distribution no longer matches its prior checksum: {name}")
    result = "".join(f"{digest}  {name}\n" for name, digest in actual.items()).encode("utf-8")
    if parse_manifest(result) is None:
        raise PublicationError("Generated checksum manifest is invalid")
    return result


def checksum_baseline(api):
    assets = api.assets()
    canonical = named(assets, SUMS)
    candidates = ([canonical] if canonical else sorted(
        (asset for asset in assets if asset["name"].startswith(BACKUP)),
        key=lambda asset: asset["id"], reverse=True))
    for candidate in candidates[:1]:
        previous = parse_manifest(api.download(candidate))
        if previous is not None:
            return canonical, candidate, previous
    if candidates:
        raise PublicationError("Existing checksum manifest or backup is invalid; preserve it for investigation")
    return None, None, None


def restore_backup(api, selected=None):
    """Recover an interrupted rename before starting another replacement."""
    assets = api.assets()
    canonical = named(assets, SUMS)
    if canonical is not None:
        return canonical
    backups = ([selected] if selected else sorted(
        (asset for asset in assets if asset["name"].startswith(BACKUP)),
        key=lambda asset: asset["id"], reverse=True))
    for backup in backups:
        if parse_manifest(api.download(backup)) is not None:
            try:
                api.rename(backup, SUMS)
            except Exception:
                # A request can succeed at GitHub before its response is lost.
                canonical = named(api.assets(), SUMS)
                if canonical is None:
                    raise
                return canonical
            canonical = named(api.assets(), SUMS)
            if canonical is None:
                raise PublicationError("Checksum backup could not be restored")
            return canonical
    return None


def publish(api, installer_name, installer_path, *, allow_installer_upload=False):
    installer = named(api.assets(), installer_name)
    if installer is None:
        if not allow_installer_upload:
            raise PublicationError("A missing installer requires the exact tagged build")
        # Verify the already public distributions before even adding an installer.
        # A later checksum repair must not bless a changed wheel or source archive.
        _, _, previous = checksum_baseline(api)
        assets = api.assets()
        manifest(api, assets, previous)
        if inventory(api.assets()) != inventory(assets):
            raise PublicationError("Release distribution inventory changed before installer upload; retry")
        try:
            api.upload(installer_name, Path(installer_path).read_bytes())
        except Exception:
            # An upload can finish before a response fails or another tagged job
            # can upload first. Preserve whichever installer is actually public.
            if named(api.assets(), installer_name) is None:
                raise
    if named(api.assets(), installer_name) is None:
        raise PublicationError("Installer upload did not create the release asset")

    canonical, baseline, previous = checksum_baseline(api)
    assets = api.assets()
    pinned = inventory(assets)
    desired = manifest(api, assets, previous)
    if inventory(api.assets()) != pinned:
        raise PublicationError("Release distribution inventory changed during checksum verification; retry")
    if canonical is None and baseline is not None:
        canonical = restore_backup(api, baseline)
    if canonical is not None and api.download(canonical) == desired:
        return "Installer and checksums verified."

    stage_name = STAGED + hashlib.sha256(desired).hexdigest()
    stage = named(api.assets(), stage_name)
    if stage is not None and api.download(stage) != desired:
        # GitHub can leave a partial "starter" asset after an interrupted upload.
        # Retain it for diagnosis without letting it block a verified retry.
        api.rename(stage, FAILED + str(stage["id"]))
        stage = None
    if stage is None:
        api.upload(stage_name, desired)
        stage = named(api.assets(), stage_name)
    if stage is None or api.download(stage) != desired:
        raise PublicationError("Staged checksum replacement did not verify")
    if inventory(api.assets()) != pinned:
        raise PublicationError("Release distribution inventory changed before checksum promotion; retry")

    backup = None
    try:
        if canonical is not None:
            api.rename(canonical, BACKUP + str(canonical["id"]))
            backup = canonical
        api.rename(stage, SUMS)
    except Exception as error:
        current = named(api.assets(), SUMS)
        if current is not None and api.download(current) == desired:
            return "Installer checksums verified after a lost rename response."
        if current is None and (backup is not None or canonical is not None):
            # Also handles a successful old->backup rename whose response failed.
            try:
                restore_backup(api)
            except Exception as rollback:
                raise PublicationError("Checksum promotion failed; its backup and staged replacement remain "
                                       "available for recovery on retry") from rollback
        raise PublicationError("Checksum promotion failed; the prior checksum asset was retained for retry") from error

    current = named(api.assets(), SUMS)
    if current is None or api.download(current) != desired:
        raise PublicationError("Canonical checksum replacement did not verify; prior backup remains available")
    return "Installer and every published distribution checksum verified; prior checksum backup retained."


class GitHubCLI:
    def __init__(self, repository, tag):
        self.repository, self.tag = repository, tag

    def run(self, *args):
        return subprocess.run(["gh", *args], capture_output=True, check=True).stdout

    def assets(self):
        release = json.loads(self.run("api", f"repos/{self.repository}/releases/tags/{quote(self.tag, safe='')}"))
        return release["assets"]

    def download(self, asset):
        return self.run("api", f"repos/{self.repository}/releases/assets/{asset['id']}",
                        "-H", "Accept: application/octet-stream")

    def upload(self, name, data):
        with tempfile.TemporaryDirectory(prefix="spec-surgeon-publish-") as folder:
            path = Path(folder) / name
            path.write_bytes(data)
            self.run("release", "upload", self.tag, str(path), "--repo", self.repository)

    def rename(self, asset, name):
        self.run("api", "--method", "PATCH", f"repos/{self.repository}/releases/assets/{asset['id']}",
                 "-f", f"name={name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--allow-installer-upload", action="store_true")
    args = parser.parse_args()
    print(publish(GitHubCLI(args.repository, args.tag), args.installer.name, args.installer,
                  allow_installer_upload=args.allow_installer_upload))
