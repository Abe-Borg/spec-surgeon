"""Names the app, the installer and the release workflow must agree on — a mismatch would break updates silently
(the updater would never find the installer, Setup would never wait for the app, the app would never reopen)."""
from __future__ import annotations

import re
import sys
import tomllib
import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from spec_surgeon import __version__, updater, winapp

ROOT = Path(__file__).resolve().parent.parent
ISS = (ROOT / "packaging" / "windows" / "installer.iss").read_text(encoding="utf-8")
SPEC = (ROOT / "packaging" / "windows" / "app.spec").read_text(encoding="utf-8")


def test_installer_name_matches_the_updater():
    base = re.search(r"^OutputBaseFilename=(.+)$", ISS, re.M).group(1).strip()
    assert base.replace("{#AppVersion}", "1.2.3") + ".exe" == updater.INSTALLER_NAME.format(version="1.2.3")
    assert updater.INSTALLER_RE.fullmatch(updater.INSTALLER_NAME.format(version=__version__))


def test_installer_waits_for_the_app_and_reopens_it():
    assert f"AppMutexName = '{winapp.APP_MUTEX}'" in ISS
    assert "{param:RELAUNCH|0}" in ISS and "Check: ShouldRelaunch" in ISS
    assert "/RELAUNCH=1" in updater.installer_args(install_dir=Path("x"), log=Path("y"))
    assert re.search(r"\[InstallDelete\]\s*\n(;.*\n)*Type: filesandordirs; Name: \"\{app\}\\_internal\"", ISS)
    assert ISS.count(f'AppUserModelID: "{winapp.AUMID}"') == 2
    assert "PrivilegesRequired=lowest" in ISS            # the in-app update never asks for administrator rights
    assert ISS.count('contents_directory="_internal"') == 0 and SPEC.count('contents_directory="_internal"') == 2


def test_the_installer_states_the_license_and_the_author():
    """The license page, the copyright line and the author's links come from spec_surgeon.about (test_about.py
    checks the values); here: the wizard pages that show them are on, and the in-app update stays silent."""
    assert re.search(r"^LicenseFile=.*LICENSE$", ISS, re.M) and "DisableWelcomePage=no" in ISS
    assert re.search(r"^AppCopyright=Copyright \(c\) \d{4} Abraham Borg$", ISS, re.M)
    assert "procedure InitializeWizard" in ISS and ISS.count("AddLink(") == 3     # the definition and two links
    assert "WelcomeLabel2=" in ISS and "MIT License" in ISS
    assert "CloseApplications=yes" in ISS and "SetupLogging=yes" in ISS      # unchanged by the welcome page
    assert (ROOT / "LICENSE").is_file()


def test_the_bundle_has_the_icon_and_the_ui_resources():
    assert SPEC.count("icon=icon") == 2 and '"ui/resources/*"' in SPEC
    assert "SetupIconFile={#SourcePath}SpecSurgeon.ico" in ISS
    sys.path.insert(0, str(ROOT / "packaging" / "windows"))
    try:
        from make_icon import read_ico_sizes
    finally:
        sys.path.pop(0)
    sizes = {w for w, h, _ in read_ico_sizes((ROOT / "packaging" / "windows" / "SpecSurgeon.ico").read_bytes())}
    assert {16, 24, 32, 48, 256} <= sizes
    assert (ROOT / "src" / "spec_surgeon" / "ui" / "resources" / "icon.svg").is_file()
    assert (ROOT / "src" / "spec_surgeon" / "ui" / "resources" / "icon-256.png").is_file()


def test_releases_publish_the_checksums_the_updater_needs():
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "sha256sum *.whl *.tar.gz *.exe > SHA256SUMS" in release
    assert updater.SUMS_NAME == "SHA256SUMS"
    assert "--latest" in release                           # /releases/latest is what the updater asks for


def test_version_and_package_data():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == __version__
    assert "ui/resources/*" in pyproject["tool"]["setuptools"]["package-data"]["spec_surgeon"]
    assert any(d.startswith("PySide6-Essentials") for d in pyproject["project"]["optional-dependencies"]["gui"])
    assert (ROOT / "docs" / "releases" / f"v{__version__}.md").is_file()


def test_packaged_worked_examples_match_the_documented_examples_exactly():
    from spec_surgeon import catalog

    expected = {p.name: p.read_bytes() for p in (ROOT / "examples/specs").glob("*.json")}
    actual = {p.name: p.read_bytes() for p in catalog.EXAMPLES_DIR.glob("*.json")}
    assert expected and actual == expected
    assert catalog.worked_examples() == [json.loads(expected[name]) for name in sorted(expected)]
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "examples/specs/*.json" in pyproject["tool"]["setuptools"]["package-data"]["spec_surgeon"]
    assert '"examples/specs/*.json"' in SPEC
    release = (ROOT / ".github/workflows/release.yml").read_text()
    assert '/tmp/spec-surgeon-release-smoke/bin/python "$GITHUB_WORKSPACE/packaging/installed_wheel_smoke.py"' in release
    verify = (ROOT / ".github/workflows/verify.yml").read_text()
    assert "python packaging/check_wheel_install.py dist" in verify
    assert "uses: ./.github/workflows/windows-build.yml" in verify


def test_missing_or_corrupt_worked_examples_cannot_silently_shrink_the_prompt(tmp_path, monkeypatch):
    from spec_surgeon import catalog

    folder = tmp_path / "examples/specs"
    folder.mkdir(parents=True)
    monkeypatch.setattr(catalog, "files", lambda _: tmp_path)
    with pytest.raises(FileNotFoundError, match="packaged worked examples are missing"):
        catalog.worked_examples()
    (folder / "broken.json").write_text("{bad json")
    with pytest.raises(ValueError):
        catalog.worked_examples()


def _workflow_script(filename, step):
    workflow = (ROOT / ".github/workflows" / filename).read_text()
    block = workflow.split(f"      - name: {step}\n", 1)[1].split("\n      - ", 1)[0]
    return textwrap.dedent(block.split("        run: |\n", 1)[1])


def _tagged_checkout(tmp_path):
    repo = tmp_path / "checkout"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "Release test", "GIT_AUTHOR_EMAIL": "release@example.invalid",
           "GIT_COMMITTER_NAME": "Release test", "GIT_COMMITTER_EMAIL": "release@example.invalid"}

    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True).stdout.strip()

    git("init", "--initial-branch=master")
    (repo / "source").write_text("tagged source")
    git("add", "source")
    git("commit", "-m", "Tagged source")
    revision = git("rev-parse", "HEAD")
    git("tag", "-a", "v1.0.0", "-m", "Release")  # The guard must peel annotated tags too.
    git("remote", "add", "origin", str(repo))
    return repo, git, revision


@pytest.mark.skipif(sys.platform == "win32", reason="Publication gates execute in Ubuntu bash")
@pytest.mark.parametrize("new_commit,published,release_exists", [
    (False, False, True), (False, True, True), (True, False, True), (True, True, True),
    (False, False, False),
])
def test_installer_publication_only_adds_a_missing_asset_from_the_tagged_commit(
        tmp_path, new_commit, published, release_exists):
    repo, git, revision = _tagged_checkout(tmp_path)
    if new_commit:
        (repo / "source").write_text("a fix needs a new release version")
        git("add", "source")
        git("commit", "-m", "Different source, same package version")
        revision = git("rev-parse", "HEAD")
    tools = tmp_path / "tools"
    tools.mkdir()
    gh = tools / "gh"
    gh.write_text('#!/bin/sh\n[ "$RELEASE_EXISTS" = "true" ] || exit 1\n'
                  'case " $* " in *" --json assets "*) printf "%s\\n" "$PUBLISHED_INSTALLER";; esac\n')
    gh.chmod(0o755)
    output = tmp_path / "github-output"
    env = {**os.environ, "PATH": str(tools) + os.pathsep + os.environ["PATH"],
           "GITHUB_REPOSITORY": "example/repository", "GITHUB_OUTPUT": str(output),
           "APP_VERSION": "1.0.0", "BUILD_REVISION": revision,
           "RELEASE_EXISTS": str(release_exists).lower(),
           "PUBLISHED_INSTALLER": "SpecSurgeon-1.0.0-Setup-x64.exe" if published else ""}
    run = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", _workflow_script(
        "windows-installer.yml", "Check the installer matches its published tag")],
        cwd=repo, env=env, text=True, capture_output=True)
    if new_commit and not published:
        assert run.returncode != 0
        assert "Bump the version" in run.stdout
        assert not output.exists()
    else:
        assert run.returncode == 0, run.stderr
        assert dict(line.split("=", 1) for line in output.read_text().splitlines()) == {
            "publish": str(release_exists and not published).lower(), "repair": str(release_exists).lower()}
    publishing = _workflow_script("windows-installer.yml",
        "Verify or repair installer checksums without replacing published assets")
    assert "packaging/windows/publish_installer.py" in publishing
    assert "--allow-installer-upload" in publishing and '"$ALLOW_INSTALLER_UPLOAD" = "true"' in publishing
    assert "--clobber" not in publishing
    assert "gh release edit" not in publishing


@pytest.mark.skipif(sys.platform == "win32", reason="Publication gates execute in Ubuntu bash")
@pytest.mark.parametrize("wrong_installer_revision,changed_source", [(False, False), (True, False), (False, True)])
def test_release_publication_requires_the_installer_and_source_to_match_the_tag(
        tmp_path, wrong_installer_revision, changed_source):
    repo, git, revision = _tagged_checkout(tmp_path)
    if changed_source:
        (repo / "source").write_text("different source from the release tag")
        git("add", "source")
        git("commit", "-m", "Changed publication checkout")
    env = {**os.environ, "GITHUB_REF_NAME": "v1.0.0",
           "WINDOWS_BUILD_REVISION": "0" * 40 if wrong_installer_revision else revision}
    run = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", _workflow_script(
        "release.yml", "Check the source and installer still match the release tag")],
        cwd=repo, env=env, text=True, capture_output=True)
    assert (run.returncode != 0) is (wrong_installer_revision or changed_source), run.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="Publication gates execute in Ubuntu bash")
def test_release_guard_accepts_checkout_lightweight_ref_for_remote_annotated_tag(tmp_path):
    origin, _, revision = _tagged_checkout(tmp_path)
    checkout = tmp_path / "actions-checkout"
    checkout.mkdir()

    def git(*args):
        return subprocess.run(["git", *args], cwd=checkout, check=True, capture_output=True, text=True).stdout.strip()

    git("init")
    git("remote", "add", "origin", str(origin))
    # actions/checkout fetches the event's commit into the tag ref, so its local
    # ref is lightweight even when the remote tag is annotated.
    git("fetch", "--no-tags", "--depth=1", "origin", f"{revision}:refs/tags/v1.0.0")
    git("checkout", "refs/tags/v1.0.0")
    assert git("cat-file", "-t", "refs/tags/v1.0.0") == "commit"
    env = {**os.environ, "GITHUB_REF_NAME": "v1.0.0", "WINDOWS_BUILD_REVISION": revision}
    run = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", _workflow_script(
        "release.yml", "Check the source and installer still match the release tag")],
        cwd=checkout, env=env, text=True, capture_output=True)
    assert run.returncode == 0, run.stderr
    assert git("rev-parse", "HEAD") == revision


def test_installer_schema_check_uses_source_inventory_and_rejects_substitutions(tmp_path):
    import importlib.util
    import json
    import subprocess
    import pytest

    subprocess.run([sys.executable, str(ROOT / "packaging/windows/prepare_smoke.py"), str(tmp_path)],
                   check=True, capture_output=True)
    request = json.loads((tmp_path / "request.json").read_text(encoding="utf-8"))
    source = ROOT / "src/spec_surgeon/schemas"
    expected = {p.relative_to(source).as_posix() for p in source.rglob("*.xsd")}
    assert set(request["schemas"]) == expected
    assert {"dublincore/dc.xsd", "dublincore/dcterms.xsd"} <= expected

    module_spec = importlib.util.spec_from_file_location(
        "_test_installed_smoke", ROOT / "packaging/windows/installed_smoke.py")
    smoke = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(smoke)
    assert smoke._check_schemas(source, request["schemas"]) == len(expected)
    from spec_surgeon import catalog
    assert smoke._check_worked_examples(request["worked_examples"]) == len(catalog.worked_examples())
    missing_example = dict(request["worked_examples"])
    missing_example.pop(next(iter(missing_example)))
    with pytest.raises(AssertionError, match="Packaged worked examples differ"):
        smoke._check_worked_examples(missing_example)

    installed = tmp_path / "schemas"
    installed.mkdir()
    for name in expected:
        path = installed / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    (installed / "dublincore/dc.xsd").unlink()
    (installed / "unexpected.xsd").touch()
    with pytest.raises(AssertionError, match="Schema inventory mismatch"):
        smoke._check_schemas(installed, request["schemas"])


def test_frozen_analyzer_dispatches_before_opening_the_app(monkeypatch):
    import importlib.util
    import multiprocessing
    from types import SimpleNamespace
    import pytest
    module_spec = importlib.util.spec_from_file_location(
        "_test_launcher_bootstrap", ROOT / "packaging" / "windows" / "launcher_bootstrap.py")
    launcher = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(launcher)
    calls = []
    monkeypatch.setattr(multiprocessing, "freeze_support", lambda: None)
    monkeypatch.setattr(sys, "argv", ["SpecSurgeon.exe", "-I", "-m", "spec_surgeon.analysis_runner", "request.json"])
    monkeypatch.setitem(sys.modules, "spec_surgeon.analysis_runner",
                        SimpleNamespace(main=lambda args: calls.append(args) or 0))
    monkeypatch.setattr(winapp, "hold_app_mutex", lambda: pytest.fail("analysis worker must not start the app"))
    with pytest.raises(SystemExit) as stopped:
        launcher.prepare()
    assert stopped.value.code == 0
    assert calls == [["request.json"]]
    assert '"spec_surgeon.analysis_runner"' in SPEC
