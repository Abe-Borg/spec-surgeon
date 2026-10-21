import hashlib
import json
from pathlib import Path, PureWindowsPath

import pytest

from spec_surgeon.storage import (artifact_path, canonical_json, conversation_folder, disk_id,
                                  read_artifact, write_artifact)


def test_short_disk_ids_keep_full_digest_and_read_legacy_artifacts(tmp_path):
    content = canonical_json({"signed": "unchanged", "result": [1, 2, 3]})
    identity = hashlib.sha256(content).hexdigest()
    path = write_artifact(tmp_path, identity, content)
    assert len(path.stem) == 16 and json.loads(path.read_bytes())["artifact_id"] == identity
    assert read_artifact(tmp_path, identity) == content
    assert write_artifact(tmp_path, identity, content) == path
    path.rename(tmp_path / f"{identity}.json")
    assert read_artifact(tmp_path, identity) == content
    (tmp_path / f"{identity}.json").write_bytes(content)
    assert read_artifact(tmp_path, identity) == content


def test_shortened_collision_or_tamper_cannot_overwrite_evidence(tmp_path):
    content = canonical_json({"evidence": "original"})
    identity = hashlib.sha256(content).hexdigest()
    path = artifact_path(tmp_path, identity)
    path.write_bytes(canonical_json({"artifact_id": "b" * 64, "payload": {"evidence": "other"}}))
    original = path.read_bytes()
    with pytest.raises(ValueError, match="identity"):
        write_artifact(tmp_path, identity, content)
    assert path.read_bytes() == original


def test_concurrent_catalog_writers_publish_one_complete_immutable_file(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    content = canonical_json({"system": "catalog " * 50000, "tools": []})
    identity = hashlib.sha256(content).hexdigest()
    with ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(pool.map(lambda unused: write_artifact(tmp_path, identity, content), range(16)))
    assert len(set(paths)) == 1 and read_artifact(tmp_path, identity) == content
    assert not list(tmp_path.glob("*.tmp"))


def test_legacy_conversation_is_copied_to_a_short_folder_before_resume(tmp_path):
    identity = "c" * 64
    old = tmp_path / identity
    old.mkdir()
    (old / "signed.json").write_bytes(b"signed evidence remains unchanged")
    target = conversation_folder(tmp_path, identity, old)
    assert target.name == "c" * 16
    assert (target / "signed.json").read_bytes() == (old / "signed.json").read_bytes()
    assert json.loads((target / "conversation.json").read_bytes())["conversation_id"] == identity
    assert conversation_folder(tmp_path, identity, target) == target


def test_longest_generated_evidence_path_from_120_character_root_is_below_240():
    root = PureWindowsPath("C:\\" + "r" * 117)
    identity = disk_id("a" * 64)
    paths = [root / "20261019-235959" / "recovery" / "conversations" / identity / "context" / f"{identity}.tmp",
             root / "20261019-235959" / "recovery" / "inspection" / identity / "investigations" / f"{identity}.json",
             root / "20261019-235959" / "recovery" / "inspection" / identity / "pages" / identity /
             "renders" / ("b" * 16) / "pages" / "p999999.png"]
    assert len(str(root)) == 120
    assert max(len(str(path)) for path in paths) < 240


def test_the_frozen_app_has_a_long_path_aware_manifest():
    import xml.etree.ElementTree as ET
    packaging = Path(__file__).resolve().parents[1] / "packaging" / "windows"
    manifest = ET.fromstring((packaging / "app.manifest").read_bytes())
    setting = manifest.find(".//{http://schemas.microsoft.com/SMI/2016/WindowsSettings}longPathAware")
    assert setting is not None and setting.text == "true"
    assert (packaging / "app.spec").read_text().count("manifest=manifest") == 1


def test_actual_recovery_render_uses_short_staged_names_from_a_deep_root(tmp_path, monkeypatch):
    from spec_surgeon import render, vision
    source = tmp_path / ("original-long-specification-name-" * 4 + ".docx")
    original = b"pinned source bytes"
    source.write_bytes(original)
    root = tmp_path / ("r" * (120 - len(str(tmp_path)) - 1))
    assert len(str(root)) == 120
    identity = "f" * 16
    work = root / "20261019-235959" / "recovery" / "inspection" / identity / "pages" / identity / "renders"
    paths = []

    def render_pdfs(files, folder, backend, workers, log):
        staged = files[0]
        assert staged.name == "source.docx" and staged.read_bytes() == original
        pdf = folder / f"{staged.stem}.pdf"
        paths.extend([staged, pdf])
        pdf.write_bytes(b"rendered pdf")
        return {staged: pdf}

    monkeypatch.setattr(render, "pick", lambda backend: "word")
    monkeypatch.setattr(render, "render_pdfs", render_pdfs)
    pdf = vision.render_pdf(source, work, renderer="word")
    assert source.read_bytes() == original and pdf.name == "source.pdf"
    assert max(len(str(path)) for path in paths) < 240
    assert vision.render_pdf(source, work, renderer="word") == pdf and len(paths) == 2
