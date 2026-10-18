"""Immutable auxiliary-input copies for transform orchestration and independent verification.

The verifier still resolves every effect itself. This module only fixes which user
supplied template/table/picture bytes both sides receive, and preserves their provenance.
"""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
from pathlib import Path


def capture_sources(files, approved_inputs=None):
    """Pin every source before callbacks/workers; unreadable direct inputs remain per-file refusals."""
    from . import guard
    expected = None
    if approved_inputs is not None:
        expected = approved_inputs.get("files") if isinstance(approved_inputs, dict) else None
        if not isinstance(expected, dict) or set(expected) != {str(Path(p).resolve()) for p in files}:
            raise ValueError("The source-file scope changed since approval; preview again before running.")
    identities, refusals = {}, {}
    for source in files:
        key = str(Path(source).resolve())
        try:
            identity = guard.acquire(source)
            if expected is not None and expected[key] != [identity.size, identity.mtime_ns, identity.md5]:
                raise ValueError(f"Source file {Path(source).name} changed since approval; preview again before running.")
            identities[key] = identity
        except OSError as exc:
            if expected is not None:
                raise ValueError(f"Approved source {Path(source).name} is no longer readable; preview again.") from exc
            refusals[key] = {"code": "source-unavailable", "reason": f"Source could not be acquired at run entry: {exc}"}
    return identities, refusals


def same_source_identity(first, second):
    return (first.size, first.mtime_ns, first.md5) == (second.size, second.mtime_ns, second.md5)


def input_paths(value):
    from .editspec import spec_input_files
    if not isinstance(value, dict):
        return []
    specs = ([stage.get("spec") or {} for stage in value["stages"] if isinstance(stage, dict)]
             if isinstance(value.get("stages"), list) and "ops" not in value else [value])
    return list(dict.fromkeys(path for spec in specs for path in spec_input_files(spec)))


def snapshot_inputs(value, out_dir, identities=None, extra_paths=()):
    """Return a private execution spec plus durable original→copy identities.

    Optional identities are approval-time [size, mtime_ns, md5] pins. New runs
    without a preview capability capture the exact auxiliary bytes at acquisition.
    """
    from .editspec import input_slots
    working = copy.deepcopy(value)
    paths = list(dict.fromkeys([*input_paths(value), *(Path(p).resolve() for p in extra_paths)]))
    if not paths:
        return working, []
    folder = Path(tempfile.mkdtemp(prefix="input-snapshot-", dir=out_dir))
    copies, manifest = {}, []
    for index, original in enumerate(paths):
        original = Path(original).resolve()
        target = folder / str(index) / original.name
        target.parent.mkdir(parents=True)
        if identities is not None and str(original) not in identities and original not in [Path(p).resolve() for p in extra_paths]:
            raise ValueError(f"Input file {original.name} was not in the approved preview; preview again.")
        pin = identities.get(str(original)) if identities is not None else None
        data = None
        try:
            before = original.stat()
            data = original.read_bytes()
            after = original.stat()
            captured = [before.st_size, before.st_mtime_ns, hashlib.md5(data).hexdigest()]
            if (len(data) != before.st_size or (before.st_size, before.st_mtime_ns) !=
                    (after.st_size, after.st_mtime_ns)):
                raise ValueError(f"Input file {original.name} changed while preparing its snapshot; run again.")
        except OSError:
            captured = "missing"
        if pin is not None and captured != pin:
            raise ValueError(f"Input file {original.name} changed while preparing its snapshot; preview again.")
        pin = captured if pin is None else pin
        if pin != "missing":
            target.write_bytes(data)
        # Missing inputs stay missing in the execution copy, even if an original
        # appears transiently after acquisition.
        copies[str(original)] = str(target)
        manifest.append({"source": str(original), "snapshot": str(target), "identity": pin})
    specs = ([stage["spec"] for stage in working["stages"]] if isinstance(working, dict) and "stages" in working
             and "ops" not in working else [working] if isinstance(working, dict) else [])
    for spec in specs:
        for operation in spec.get("ops", []):
            for container, key in input_slots(operation):
                container[key] = copies[str(Path(container[key]).resolve())]
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return working, manifest


def changed_inputs(manifest):
    """Describe original or snapshot files that no longer match the acquired identities."""
    changed = []
    for entry in manifest:
        original = Path(entry["source"])
        try:
            metadata = original.stat()
            current = [metadata.st_size, metadata.st_mtime_ns, hashlib.md5(original.read_bytes()).hexdigest()]
        except OSError:
            current = "missing"
        if current != entry["identity"]:
            changed.append(str(original))
        snapshot = Path(entry["snapshot"])
        try:
            data = snapshot.read_bytes()
            snapshot_matches = (entry["identity"] != "missing" and len(data) == entry["identity"][0]
                                and hashlib.md5(data).hexdigest() == entry["identity"][2])
        except OSError:
            snapshot_matches = entry["identity"] == "missing" and not snapshot.exists()
        if not snapshot_matches:
            changed.append(str(snapshot))
    return changed
