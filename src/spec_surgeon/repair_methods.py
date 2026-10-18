"""Portable implementations are untrusted proposals, never remembered approval.

Source is stored as bounded, content-addressed data inside the existing case store.
It is only executed by codegen's checked sandbox with the *current* contract and
source binding, followed by ordinary and original-to-final verification.
"""
from __future__ import annotations

import base64
import hashlib
import json
import zlib

from . import __version__

MAX_SOURCE = 64 * 1024
PROTOCOL = 1


def signature(contract):
    """Retrieval shape, not authorization: keep capabilities, replace job values.

Names, wording, template paths/hashes, targets and numeric values are rebound
through pkg.goal/pkg.binding on every trial. Property keys and effect ordering
remain part of the shape. No past literal becomes current approved wording.
"""
    def shape(value, key=""):
        if isinstance(value, dict):
            return {k: shape(v, k) for k, v in sorted(value.items()) if k not in {"request", "id", "author"}}
        if isinstance(value, list):
            return [shape(v, key) for v in value]
        if key in {"kind", "type", "rule", "position", "formatting", "tracked"}:
            return value
        return type(value).__name__
    data = json.dumps(shape(contract), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def remember(generated, contract):
    raw = generated.source.encode("utf-8")
    if not raw or len(raw) > MAX_SOURCE:
        raise ValueError("generated method exceeds the bounded reusable-source limit")
    encoded = base64.b64encode(zlib.compress(raw)).decode("ascii")
    return {"kind": "method", "protocol": PROTOCOL, "engine": __version__,
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "source_zlib": [encoded[i:i + 12000] for i in range(0, len(encoded), 12000)],
            "signature": signature(contract), "select": generated.select, "params": generated.params,
            "portable": not generated.select and not generated.params,
            "assumptions": ["Use the current pkg.goal, pkg.binding and pinned pkg.inputs.",
                            "Every file must pass fresh planning, sandbox and independent verification."]}


def source(method):
    if method.get("kind") != "method" or method.get("protocol") != PROTOCOL:
        raise ValueError("unsupported reusable method format")
    chunks = method.get("source_zlib")
    if (not isinstance(chunks, list) or not chunks or len(chunks) > 8 or
            any(not isinstance(c, str) or len(c) > 12000 for c in chunks)):
        raise ValueError("invalid reusable method source")
    data = base64.b64decode("".join(chunks), validate=True)
    decoder = zlib.decompressobj()
    raw = decoder.decompress(data, MAX_SOURCE + 1)
    if len(raw) > MAX_SOURCE or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError("invalid or oversized reusable method source")
    if hashlib.sha256(raw).hexdigest() != method.get("source_sha256"):
        raise ValueError("reusable method source hash changed")
    return raw.decode("utf-8")


def eligible(case, contract):
    method = case.get("candidate", {})
    return bool(case.get("status") == "success" and case.get("structural_verified") is True
                and case.get("reuse_blocked") is False and
                not any(f.get("accepted") is False for f in case.get("feedback", []))
                and method.get("kind") == "method" and method.get("protocol") == PROTOCOL
                and method.get("engine") == __version__ and method.get("signature") == signature(contract)
                and method.get("portable") is True and method.get("select") == {} and method.get("params") == {})


def evidence(cases, contract):
    """Bounded examples for adaptation; decoding never executes their code."""
    result = []
    for case in cases:
        method = case.get("candidate", {})
        if method.get("kind") != "method" or method.get("signature") != signature(contract):
            continue
        try:
            result.append({"case_id": case.get("id"), "source": source(method),
                           "select": method.get("select", {}), "params": method.get("params", {}),
                           "assumptions": method.get("assumptions", []), "status": case.get("status"),
                           "feedback": case.get("feedback"), "reuse_blocked": case.get("reuse_blocked"),
                           "notice": "Prior implementation only. Current goals, targets and wording remain authoritative."})
        except (ValueError, TypeError, zlib.error):
            continue
        if len(result) == 3:
            break
    return result
