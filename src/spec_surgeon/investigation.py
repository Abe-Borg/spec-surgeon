"""Read-only generated analyses over pinned snapshots, with durable evidence pages.

The analyzer never receives paths or live package objects. It gets copied JSON data
and returns JSON. Its subprocess has a restricted language/runtime and resource
limits; its output is evidence, never approved wording or permission to edit.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .resources import (DEFAULT_ANALYSIS_INPUT_BYTES, DEFAULT_ANALYSIS_MEMORY_MB, DEFAULT_ANALYSIS_OUTPUT_BYTES,
                        DEFAULT_ANALYSIS_SECONDS, positive_integer)

ANALYSIS_IMPORTS = frozenset(("re", "copy", "math", "itertools", "functools", "json"))
ANALYSIS_FROM = {"lxml": {"etree"}, "collections": {"Counter", "OrderedDict", "defaultdict"},
                 "__future__": {"annotations"}, "typing": {"Any", "Dict", "List", "Optional", "Tuple", "Sequence"}}
DATA_ATTRIBUTES = frozenset(("get", "items", "keys", "values", "copy", "append", "extend", "add", "update",
    "setdefault", "pop", "count", "index", "sort", "reverse", "remove", "discard", "union", "intersection",
    "difference", "issubset", "issuperset", "startswith", "endswith", "split", "rsplit", "splitlines", "join",
    "strip", "lstrip", "rstrip", "lower", "upper", "casefold", "replace", "find", "rfind", "partition",
    "rpartition", "isalpha", "isdigit", "isalnum", "isspace", "encode", "decode", "hex", "most_common",
    "findall", "findtext", "iter", "iterfind", "itertext", "getparent", "getchildren", "getnext", "getprevious",
    "xpath", "tag", "text", "tail", "attrib", "nsmap", "localname", "namespace", "match", "search", "group",
    "groups", "groupdict", "start", "end", "span", "fullmatch", "sub", "subn", "finditer"))


def check_analysis_source(source: str):
    """Reuse the transform's escape defenses, then narrow its capabilities further."""
    from .codegen import MODULE_ATTRS, check_source
    if not isinstance(source, str) or not source.strip() or len(source) > 200000:
        return ["analysis source must be non-empty Python, at most 200000 characters"]
    errors = [e for e in check_source(source) if not e.startswith("define exactly one class deriving from Op")]
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return errors
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "analyze"]
    if len(functions) != 1:
        errors.append("define exactly one analyze(documents, inputs) function")
    elif len(functions[0].args.args) != 2 or functions[0].args.vararg or functions[0].args.kwarg:
        errors.append("analyze takes exactly documents and inputs")
    attributes = DATA_ATTRIBUTES | set().union(*MODULE_ATTRS.values())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(n.name not in ANALYSIS_IMPORTS for n in node.names):
            errors.append("analysis imports only re, copy, math, itertools, functools, json")
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in ANALYSIS_FROM or any(n.name not in ANALYSIS_FROM[node.module] for n in node.names):
                errors.append(f"analysis cannot import from {node.module!r}")
        elif isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef, ast.Await, ast.Yield, ast.YieldFrom)):
            errors.append("analysis defines ordinary functions, not classes, coroutines or generators")
        elif isinstance(node, ast.Attribute) and node.attr not in attributes:
            errors.append(f"analysis attribute {node.attr!r} is not available")
        elif isinstance(node, ast.FunctionDef) and node.name.startswith("__"):
            errors.append("dunder function names are not available")
        elif isinstance(node, ast.Name) and node.id in ("print", "hasattr", "format", "callable", "super"):
            errors.append(f"analysis builtin {node.id!r} is not available")
    return list(dict.fromkeys(errors))


@dataclass(frozen=True)
class AnalysisLimits:
    seconds: int = DEFAULT_ANALYSIS_SECONDS
    memory_mb: int = DEFAULT_ANALYSIS_MEMORY_MB
    output_bytes: int = DEFAULT_ANALYSIS_OUTPUT_BYTES
    input_bytes: int = DEFAULT_ANALYSIS_INPUT_BYTES

    def __post_init__(self):
        for key, value in asdict(self).items():
            positive_integer(key, value)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _text(data: bytes) -> str:
    # Keep malformed XML inspectable; analysis must not require successful editing/parser support.
    return data.decode("utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig", errors="replace")


def _snapshot(label: str, path: Path, data: bytes, limit: int):
    provenance = {"label": label, "path": str(path.resolve()), "sha256": _sha(data), "bytes": len(data)}
    payload = {k: provenance[k] for k in ("label", "sha256", "bytes")}
    if path.suffix.lower() in (".docx", ".xlsx"):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if sum(info.file_size for info in infos) > limit:
                raise ValueError("expanded package exceeds the configured analysis input allowance")
            payload["parts"] = {info.filename: _text(archive.read(info)) for info in infos
                                if info.filename.lower().endswith((".xml", ".rels"))}
            payload["members"] = [{"name": info.filename, "bytes": info.file_size} for info in infos]
    elif path.suffix.lower() in (".csv", ".json", ".txt"):
        payload["text"] = _text(data)
    else:
        from .ops.images import sniff
        kind, width, height = sniff(data)
        payload["metadata"] = {"kind": kind, "width": width, "height": height}
    return provenance, payload


def _allowed(batch):
    paths = list(batch.files.values()) + list(batch.inputs)
    if batch.template:
        paths.append(batch.template)
    return {str(Path(path).resolve()) for path in paths}


def artifact_root(batch) -> Path:
    root = getattr(batch, "artifact_dir", None) or batch.work_dir
    if root is None:
        # One-shot compilation still gets a durable archive, rather than a vanished TemporaryDirectory.
        root = Path(tempfile.mkdtemp(prefix="spec-surgeon-evidence-"))
        batch.artifact_dir = root
    return Path(root) / "investigations"


def run_analysis(batch, source: str, files=None, inputs=None):
    limits = getattr(batch, "analysis_limits", None) or AnalysisLimits()
    if isinstance(limits, dict):
        limits = AnalysisLimits(**limits)
    errors = check_analysis_source(source)
    if errors:
        raise ValueError("analysis source rejected: " + "; ".join(errors))
    files = list(batch.files) if files is None else files
    inputs = [] if inputs is None else inputs
    if (not isinstance(files, list) or not isinstance(inputs, list)
            or any(not isinstance(x, str) for x in files + inputs) or not files + inputs):
        raise ValueError("files and inputs are lists of allowed labels, selecting at least one snapshot")
    documents, extra, snapshots = [], [], []
    total = 0
    expanded = 0
    input_paths = {str(path): Path(path) for path in batch.inputs}
    for names, target, is_doc in ((files, documents, True), (inputs, extra, False)):
        for label in dict.fromkeys(names):
            if is_doc:
                if label == "template" and batch.template:
                    path = batch.template
                else:
                    if label not in batch.files:
                        raise ValueError(f"document {label!r} is outside this investigation")
                    path = batch.files[label]
            else:
                if label not in input_paths:
                    raise ValueError(f"input {label!r} was not listed for this investigation")
                path = input_paths[label]
            if Path(path).stat().st_size > limits.input_bytes - total:
                raise ValueError("selected snapshots exceed the configured analysis input allowance")
            batch.check_source_pin(path)
            with Path(path).open("rb") as stream:
                data = stream.read(limits.input_bytes - total + 1)
            total += len(data)
            if total > limits.input_bytes:
                raise ValueError("selected snapshots exceed the configured analysis input allowance")
            provenance, payload = _snapshot(label, Path(path), data, limits.input_bytes - expanded)
            expanded += len(_json(payload))
            if expanded > limits.input_bytes:
                raise ValueError("expanded snapshots exceed the configured analysis input allowance")
            snapshots.append(provenance)
            target.append(payload)
    root = artifact_root(batch)
    root.mkdir(parents=True, exist_ok=True)
    request = {"source": source, "documents": documents, "inputs": extra, "limits": asdict(limits)}
    encoded = _json(request)
    if len(encoded) > limits.input_bytes:
        raise ValueError("expanded snapshots exceed the configured analysis input allowance")
    with tempfile.TemporaryDirectory(prefix="analysis-", dir=root) as work:
        folder = Path(work)
        (folder / "request.json").write_bytes(encoded)
        env = {k: v for k, v in os.environ.items() if k.upper() in ("PATH", "SYSTEMROOT", "TEMP", "TMP")}
        try:
            proc = subprocess.run([sys.executable, "-I", "-m", "spec_surgeon.analysis_runner", str(folder / "request.json")],
                                  cwd=folder, env=env, timeout=limits.seconds, capture_output=True, check=False)
            result_path = folder / "result.json"
            if not result_path.is_file():
                result = {"ok": False, "error": "analysis subprocess stopped before returning evidence",
                          "returncode": proc.returncode, "diagnostic": proc.stderr.decode("utf-8", "replace")[-2000:]}
            elif result_path.stat().st_size > limits.output_bytes + 4096:
                result = {"ok": False, "error": "analysis output exceeded its configured allowance"}
            else:
                result = json.loads(result_path.read_text(encoding="utf-8"))
        except subprocess.TimeoutExpired:
            result = {"ok": False, "error": f"analysis timed out after {limits.seconds} seconds"}
    manifest = {"version": 1, "source": source, "source_sha256": _sha(source.encode()), "snapshots": snapshots,
                "limits": asdict(limits), "result": result,
                "authority": "Generated diagnostic evidence only; never approved wording or editing permission."}
    content = _json(manifest)
    identity = _sha(content)
    path = root / f"{identity}.json"
    path.write_bytes(content)
    return read_analysis_result(batch, identity)


def read_analysis_result(batch, artifact_id: str, offset: int = 0, chars: int = 16000):
    if not isinstance(artifact_id, str) or len(artifact_id) != 64 or any(c not in "0123456789abcdef" for c in artifact_id):
        raise ValueError("artifact_id must be a returned evidence identifier")
    if type(offset) is not int or offset < 0 or type(chars) is not int or not 1 <= chars <= 32000:
        raise ValueError("offset must be nonnegative and chars must be 1–32000")
    content = (artifact_root(batch) / f"{artifact_id}.json").read_bytes()
    if _sha(content) != artifact_id:
        raise ValueError("analysis artifact changed since it was produced")
    manifest = json.loads(content)
    allowed = _allowed(batch)
    for snapshot in manifest["snapshots"]:
        if snapshot["path"] not in allowed:
            raise ValueError("analysis evidence belongs to another input scope")
        if _sha(Path(snapshot["path"]).read_bytes()) != snapshot["sha256"]:
            raise ValueError("analysis evidence is stale: its source snapshot has changed")
    result = _json(manifest["result"]).decode()
    if offset > len(result):
        raise ValueError("offset exceeds this result")
    end = min(offset + chars, len(result))
    return {"artifact_id": artifact_id, "ok": bool(manifest["result"].get("ok")),
            "source_sha256": manifest["source_sha256"], "snapshots": manifest["snapshots"],
            "offset": offset, "total_chars": len(result), "next_offset": end if end < len(result) else None,
            "result_json": result[offset:end], "authority": manifest["authority"]}
