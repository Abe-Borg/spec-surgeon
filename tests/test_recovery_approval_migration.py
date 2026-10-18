"""Content-pinned preview changes preserve valid recovery capabilities, never ordinary old previews."""
import hashlib
import json
import os

import pytest

from make_fixtures import build
from spec_surgeon import recovery
from spec_surgeon.preview import _legacy_approval_token, approval_token
from test_recovery import margin, opts, spec


def _legacy_session(tmp_path, *, keep_input_pins=True):
    source = tmp_path / "source.docx"
    source.write_bytes(build("21 13 13", "FIRE SUPPRESSION"))
    goal = spec([margin()])
    result = recovery.prepare_recovery([source], goal["request"], tmp_path / "session", spec=goal, options=opts())
    assert result.ready
    checkpoint = result.out_dir / "recovery.json"
    value = json.loads(checkpoint.read_text(encoding="utf-8"))
    value.pop("approval_version")
    value["token"] = recovery._hash({
        "preview": _legacy_approval_token(value["key"], [source]),
        "contents": {str(source): hashlib.sha256(source.read_bytes()).hexdigest()},
    })
    for pin in value["token_inputs"]["files"].values():
        del pin[2:]
    if not keep_input_pins:
        value.pop("input_pins")
    checkpoint.write_text(json.dumps(value), encoding="utf-8")
    return source, checkpoint, value


@pytest.mark.parametrize("keep_input_pins", [False, True])
def test_legacy_recovery_revalidates_and_upgrades_on_delivery(tmp_path, keep_input_pins):
    source, checkpoint, value = _legacy_session(tmp_path, keep_input_pins=keep_input_pins)
    loaded = recovery.load_recovery(checkpoint.parent)
    assert loaded.approval_version == 1 and loaded.token == value["token"]
    assert loaded.review_ack == ""
    expected = loaded.ready[0].output.read_bytes()
    destination = recovery.deliver_recovery(loaded, tmp_path / "delivered")
    assert (destination / source.name).read_bytes() == expected
    updated = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert updated["approval_version"] == 2
    assert updated["token"] != value["token"]
    assert len(updated["token_inputs"]["files"][str(source)]) == 3
    assert recovery.load_recovery(checkpoint.parent).ready


@pytest.mark.parametrize("keep_input_pins", [False, True])
def test_legacy_content_digest_rejects_same_stat_source_changes(tmp_path, keep_input_pins):
    source, checkpoint, _ = _legacy_session(tmp_path, keep_input_pins=keep_input_pins)
    old = source.stat()
    data = bytearray(source.read_bytes())
    data[-1] ^= 1
    source.write_bytes(data)
    os.utime(source, ns=(old.st_atime_ns, old.st_mtime_ns))
    with pytest.raises(ValueError, match="changed"):
        recovery.load_recovery(checkpoint.parent)


def test_current_checkpoint_cannot_fall_back_to_legacy_token(tmp_path):
    _, checkpoint, value = _legacy_session(tmp_path)
    value["approval_version"] = 2
    checkpoint.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        recovery.load_recovery(checkpoint.parent)


def test_plain_legacy_preview_is_not_a_recovery_capability(tmp_path):
    source, checkpoint, value = _legacy_session(tmp_path)
    value["token"] = _legacy_approval_token(value["key"], [source])
    assert value["token"] != approval_token(value["key"], [source])
    checkpoint.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        recovery.load_recovery(checkpoint.parent)


@pytest.mark.parametrize("changed", ["request", "candidate"])
def test_legacy_approval_preserves_requirements_and_verified_copy_identity(tmp_path, changed):
    _, checkpoint, value = _legacy_session(tmp_path)
    if changed == "request":
        value["request"] = "A different request"
        checkpoint.write_text(json.dumps(value), encoding="utf-8")
    else:
        output = recovery.load_recovery(checkpoint.parent).ready[0].output
        output.write_bytes(output.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="changed"):
        recovery.load_recovery(checkpoint.parent)
