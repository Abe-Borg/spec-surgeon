"""Tier B — a generated transform for a request no op can express (docs/NL_SURGICAL_EDITOR_PLAN.md §8; Phase 5).

Offered when it is needed (decided 2026-10-15): when the conversation ends with ``unsupported`` and Claude judged the
request within the catalog's ``custom_transform_limits``, the user picks "Have Claude write a one-off custom transform"
(in the window or at the terminal; ``do --allow-codegen`` accepts the offer without a terminal). ``generate_op`` gives
Claude the whole conversation as its brief, read-only tools to look at the sample files' XML (``show_xml``,
``find_paragraphs``, ``list_styles``, ``show_part_text``), and asks for ONE op module against a restricted API (the
``Op`` interface, ``DocxPackage``, ``ooxml`` helpers, selectors, lxml); it tests it:

1. ``check_source`` — an AST whitelist: imports only from ``ALLOWED_FROM`` (named symbols) and a few pure stdlib
   modules, each usable only as ``module.name`` with ``name`` from ``MODULE_ATTRS`` (an allowlist — a module's other
   attributes lead to other modules and the file system; since the 2026-10-15 review, finding review-7); no dunder
   names or attributes (bar ``__init__``), no frame/code/generator internals (``FORBIDDEN_ATTR_PREFIXES``), no
   ``open``/``exec``/``eval``/``getattr``/``__import__``…, no file-writing or file-reading attributes (lxml's
   ``write_c14n``/``xinclude``/``xmlfile``… included); exactly one ``Op`` subclass with ``plan`` and ``apply``;
2. ``run_in_sandbox`` — the module runs in a separate isolated process (``python -I``, temp working directory,
   stripped environment, timeout) on a COPY of each sample file: plan (predict first), then apply; the runner's
   audit hook (``sandbox_runner.install_guard``) keeps every file-system change inside that directory and refuses
   processes, sockets and native code;
3. ``hard_limits`` — plain formatting only: the plan may not change text in surviving parts, add members, predict a
   revision of any kind (no tracked formatting either), change any part but the document's text parts and its styles
   part (V10's residual identity covers exactly those), or claim no layout reference without a reason;
4. the **full verification suite** (independent verifier, V1–V5, V8, V10) on every sample. A failure goes back
   to the model with the record; configurable ample attempts, stopping earlier when
   equivalent failures repeat without new inspection evidence.

A passing op is saved (``generated_ops/<hash>.py`` + ``.history.json`` beside the batch's records) — a cache, not
a registered op: promotion into ``ops/`` is a human decision, with tests. ``run_generated`` applies it to a batch
the same way (sandbox per file, verifier per file); outputs go to the output folder only — never in place.

``codegen/history.json`` is the diagnosis of every attempt, passing or not: the submitted source, its problems and
trials, and the whole exchange with the model (``GeneratedOp.transcript``), re-written after every turn and on every
way out. An API error (``llm.is_api_error``) ends the generation as a recorded failure; anything else propagates.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import diagnostics as diag, guard
from .llm import Usage
from .compiler import INSPECTION_TOOLS
from .process_limits import DEFAULT_LOG_BYTES, DEFAULT_MEMORY_MB, DEFAULT_OUTPUT_BYTES, MAX_RESULT_BYTES
from .resources import (DEFAULT_CONTEXT_TOKENS, DEFAULT_EFFORT, DEFAULT_MAX_ATTEMPTS, DEFAULT_MAX_CALLS, DEFAULT_MAX_OUTPUT_TOKENS,
                        DEFAULT_STALL_REPETITIONS, InvestigationProgress, positive_integer)

ALLOWED_FROM: Dict[str, set] = {
    "spec_surgeon.ops.base": {"Op", "PlanFragment", "BlockDiff", "JobException"},
    "spec_surgeon.package": {"W", "NS", "R", "DocxPackage", "serialize_xml", "parse_xml"},
    "spec_surgeon.ooxml": {"PPR_ORDER", "RPR_ORDER", "SECTPR_ORDER", "STYLE_ORDER", "insert_in_order", "get_or_add",
                           "paragraphs", "paragraph_ppr", "run_rpr", "mark_rpr", "set_rfonts", "block_full_text",
                           "count_revisions", "THEME_FONT_ATTR", "own_size", "set_size"},
    "spec_surgeon.ops.selectors": {"resolve_sections", "scope_parts", "paragraph_selected", "check_paragraph_selector",
                                   "is_targeted", "StyleSheet", "DEFAULT_PRESERVE_FONTS", "FONT_PARAM_DOC",
                                   "check_run_selector", "font_values", "has_direct_font", "in_revision",
                                   "is_symbol_rpr", "mark_in_revision", "rpr_selected", "runs_by_paragraph",
                                   "size_applies", "size_values", "chain_reads", "table_ordinals"},
    "lxml": {"etree"},
    "__future__": {"annotations"},
    "typing": {"Any", "Dict", "List", "Optional", "Tuple", "Sequence", "Set", "Iterable"},
    "dataclasses": {"dataclass", "field"},
    "collections": {"Counter", "OrderedDict", "defaultdict"},
}
ALLOWED_IMPORT = {"re", "copy", "math", "itertools", "functools", "json"}
GOAL_FROM = {
    "hashlib": {"md5", "sha256"},
    "posixpath": {"join", "dirname", "basename", "splitext", "relpath", "normpath"},
    "spec_surgeon.package": {"CT_PART", "REL", "REL_TYPE_BASE", "MEDIA_REL_TYPES"},
    "spec_surgeon.ops.template_styles": {"reconcile_styles", "remap_references", "reference_parts"},
    "spec_surgeon.ops.apply_header_footer_template": {"strip_foreign", "pin_paragraph", "pin_rpr",
                                                     "PARA_KEYS", "RUN_KEYS"},
    "spec_surgeon.ops.effective": {"Resolver", "AmbiguousToggle", "rescale_tabs", "text_width", "theme_fonts",
                                   "theme_part"},
    "spec_surgeon.ops.hf_common": {"apply_removal", "plan_removal", "check_part_relationships", "effective_refs",
                                   "refs", "HEADER_CT", "FOOTER_CT", "KINDS", "TYPES"},
    "spec_surgeon.ops.slots": {"Slot", "apply_slot", "extract_fill", "find_slots", "field_runs", "fill_reads",
                               "expected_block_text", "mark_slots", "nearest_p", "paragraph_runs", "reference_values",
                               "rule_uses_literal"},
    "spec_surgeon.ooxml": {"RevisionStamp", "property_change", "max_revision_id", "file_uses_date_utc"},
    "spec_surgeon.ops.textedit": {"Edit", "RunText", "run_text", "plan_paragraph", "edits_to_data",
                                  "edits_from_data", "apply_part_edits"},
}


def allowed_imports(goal_contract=None):
    allowed = {name: set(values) for name, values in ALLOWED_FROM.items()}
    if goal_contract is not None:
        from .goals import validate_contract
        validate_contract(goal_contract)
        for name, values in GOAL_FROM.items():
            allowed.setdefault(name, set()).update(values)
    return allowed
# What legacy formatting methods may not import: tracked-formatting helpers and the engine's registration hook.
# Governed methods have the additional GOAL_FROM helpers. tests/test_codegen.py pins that every name
# the examples import is either allowed above or listed here.
EXCLUDED_FROM_EXAMPLES: Dict[str, set] = {
    "spec_surgeon.ooxml": {"RevisionStamp", "property_change", "max_revision_id", "file_uses_date_utc"},
    "spec_surgeon.ops.replace_text": {"default_author"},
    "spec_surgeon.ops": {"register"},
    # set_page_setup's built-in batch 5 notes and reads (what a first page shows through inheritance, what a content
    # selection of sections reads): a generated op neither selects sections by content nor flags their headers
    "spec_surgeon.ops.hf_common": {"effective_refs", "refs"},
    "spec_surgeon.ops.selectors": {"section_reads"},
}
EXAMPLE_OPS = ("set_page_setup.py", "set_paragraph_props.py", "set_font.py")
MAX_CALLS = DEFAULT_MAX_CALLS
ALLOWED_MODULES = tuple(sorted(set(ALLOWED_FROM) | ALLOWED_IMPORT))
FORBIDDEN_NAMES = {"open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars", "getattr", "setattr",
                   "delattr", "input", "breakpoint", "memoryview", "help", "exit", "quit", "type", "object"}
FORBIDDEN_ATTRS = {"from_path", "write_bytes", "write_text", "read_bytes", "read_text", "unlink", "rmdir", "mkdir",
                   "system", "popen", "spawn", "fork", "parse", "iterparse", "XMLParser", "write", "repack",
                   "output_bytes", "copy_tree", "rename", "replace_file", "chmod",
                   # lxml's own readers and writers reach files without Python's open(): an element's tree
                   # (getroottree()) can write_c14n/xinclude; a result tree can write_output
                   "write_c14n", "write_output", "xinclude", "XInclude", "xmlfile", "htmlfile", "XSLT", "XMLSchema",
                   "RelaxNG", "Schematron", "DTD", "parser", "resolvers"}
# Frames, code objects and generator/coroutine internals lead from any function to the globals of the sandbox
# runner itself (and its os/sys): no attribute with these prefixes, on anything.
FORBIDDEN_ATTR_PREFIXES = ("f_", "gi_", "cr_", "ag_", "tb_", "co_", "im_", "func_")
# A plainly imported module (or lxml's etree) may be used ONLY as ``module.name`` with ``name`` from this list — an
# allowlist, not a denylist: a module's other attributes lead to other modules (``re.enum.sys.modules``,
# ``json.codecs.open``) and from there to the file system. The module object itself never leaves that form (not
# passed, assigned, returned or indexed), so nothing can reach its other attributes indirectly.
MODULE_ATTRS: Dict[str, set] = {
    "re": {"compile", "search", "match", "fullmatch", "sub", "subn", "split", "findall", "finditer", "escape",
           "IGNORECASE", "I", "MULTILINE", "M", "DOTALL", "S", "VERBOSE", "X", "ASCII", "A", "UNICODE", "U",
           "Pattern", "Match", "error"},
    "json": {"dumps", "loads"},
    "copy": {"copy", "deepcopy"},
    "math": {"ceil", "floor", "trunc", "fabs", "sqrt", "isclose", "isfinite", "isnan", "isinf", "inf", "pi", "gcd",
             "log", "log10", "exp", "pow", "hypot", "fsum", "prod", "comb", "copysign", "fmod"},
    "itertools": {"chain", "groupby", "islice", "count", "cycle", "repeat", "accumulate", "product", "permutations",
                  "combinations", "combinations_with_replacement", "zip_longest", "takewhile", "dropwhile",
                  "filterfalse", "starmap", "tee", "pairwise", "compress", "batched"},
    "functools": {"reduce", "partial", "lru_cache", "cache", "cmp_to_key", "total_ordering", "wraps",
                  "cached_property"},
    "etree": {"Element", "SubElement", "QName", "iselement", "fromstring", "tostring"},
}
MODULE_FROM_IMPORTS = {("lxml", "etree"): "etree"}
SANDBOX_TIMEOUT = 60
MAX_ITER = DEFAULT_MAX_ATTEMPTS


def check_source(src: str, *, goal_contract=None) -> List[str]:
    """Whitelist problems of a generated op's source ([] = acceptable)."""
    try:
        allowed = allowed_imports(goal_contract)
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"syntax error: {e}"]
    probs: List[str] = []
    modules: Dict[str, str] = {}                  # a name bound to a module → its MODULE_ATTRS key
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name not in ALLOWED_IMPORT:
                    probs.append(f"line {node.lineno}: 'import {a.name}' is not allowed")
                else:
                    modules[a.asname or a.name] = a.name
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in allowed:
                probs.append(f"line {node.lineno}: 'from {'.' * node.level}{node.module} import …' is not allowed")
            else:
                bad = [a.name for a in node.names if a.name not in allowed[node.module]]
                if bad:
                    probs.append(f"line {node.lineno}: from {node.module} import {bad} is not allowed "
                                 f"(allowed: {sorted(allowed[node.module])})")
                for a in node.names:
                    key = MODULE_FROM_IMPORTS.get((node.module, a.name))
                    if key:
                        modules[a.asname or a.name] = key
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            probs.append(f"line {node.lineno}: '{node.id}' is not allowed")
        elif isinstance(node, ast.Name) and node.id.startswith("__"):
            probs.append(f"line {node.lineno}: the name '{node.id}' is not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr != "__init__":
                probs.append(f"line {node.lineno}: dunder attribute '{node.attr}' is not allowed")
            elif node.attr in FORBIDDEN_ATTRS:
                probs.append(f"line {node.lineno}: attribute '{node.attr}' is not allowed (use pkg.xml / "
                             f"pkg.write_xml on the working package)")
            elif node.attr.startswith(FORBIDDEN_ATTR_PREFIXES):
                probs.append(f"line {node.lineno}: attribute '{node.attr}' is not allowed (frame, code and generator "
                             f"internals)")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            probs.append(f"line {node.lineno}: global/nonlocal is not allowed")
    # A module is used only as `module.name` with an allowed name; any other use of the module object (passed,
    # assigned, returned, rebound, indexed) is refused, so its other attributes cannot be reached indirectly.
    as_value = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in modules:
            as_value.add(id(node.value))
            key = modules[node.value.id]
            if node.attr not in MODULE_ATTRS[key] and node.attr not in FORBIDDEN_ATTRS:
                probs.append(f"line {node.lineno}: '{node.value.id}.{node.attr}' is not allowed (allowed from "
                             f"{key}: {sorted(MODULE_ATTRS[key])})")
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in modules and id(node) not in as_value:
            probs.append(f"line {node.lineno}: the module '{node.id}' may only be used as {node.id}.<name>")
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    ops = [c for c in classes if any(isinstance(b, ast.Name) and b.id == "Op" for b in c.bases)]
    if len(ops) != 1:
        probs.append(f"define exactly one class deriving from Op (found {len(ops)})")
    else:
        methods = {f.name for f in ops[0].body if isinstance(f, ast.FunctionDef)}
        for m in ("plan", "apply"):
            if m not in methods:
                probs.append(f"the Op subclass must define {m}()")
    return probs


@dataclass
class SandboxResult:
    ok: bool
    output: Optional[Path] = None
    plan: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    code: Optional[str] = None
    seconds: float = 0.0
    traceback: str = ""                 # the generated code's traceback, from inside the sandbox
    stderr: str = ""                    # what the sandbox process printed (a crash, an import error)
    returncode: Optional[int] = None


def _tail(text: str, n: int = 15) -> str:
    lines = [x for x in (text or "").rstrip().splitlines() if x.strip()]
    return "\n".join(lines[-n:])


def run_in_sandbox(source: str, docx: Path, select: Dict[str, Any], params: Dict[str, Any], workdir: Path,
                   timeout: int = SANDBOX_TIMEOUT, *, goal_contract=None, goal_binding=None,
                   memory_mb: int = DEFAULT_MEMORY_MB,
                   output_bytes: int = DEFAULT_OUTPUT_BYTES, log_bytes: int = DEFAULT_LOG_BYTES,
                   source_bytes: Optional[bytes] = None) -> SandboxResult:
    """plan + apply of the generated op on a COPY of ``docx`` in a separate isolated process."""
    limits = {"seconds": timeout, "memory_mb": memory_mb, "output_bytes": output_bytes, "log_bytes": log_bytes}
    for name, value in limits.items():
        positive_integer(name, value)
    if source_bytes is not None and not isinstance(source_bytes, bytes):
        raise TypeError("source_bytes must be immutable bytes")
    with diag.scope(sandbox_id=diag.new_id()), \
            diag.span("codegen.sandbox", source=str(docx), workdir=str(workdir), timeout_seconds=timeout) as record:
        result = _run_in_sandbox(source, docx, select, params, workdir, timeout, goal_contract, limits, source_bytes,
                                 goal_binding)
        record.update(ok=result.ok, code=result.code, output=str(result.output) if result.output else None)
        return result


def _run_in_sandbox(source, docx, select, params, workdir, timeout, goal_contract, limits, source_bytes,
                    goal_binding=None):
    t0 = time.perf_counter()
    workdir = Path(workdir).resolve()          # the sandbox runs with cwd=workdir: every path it gets is absolute
    if Path(docx).resolve() in {workdir / name for name in ("source.docx", "result.json", "plan.json", "out.docx")}:
        raise ValueError("the source document collides with sandbox output artifacts")
    workdir.mkdir(parents=True, exist_ok=True)
    # A killed process must never inherit a prior invocation's successful result.
    for name in ("result.json", "plan.json", "out.docx"):
        (workdir / name).unlink(missing_ok=True)
    copy_path = workdir / "source.docx"
    if source_bytes is None:
        shutil.copyfile(Path(docx).resolve(), copy_path)
    else:
        copy_path.write_bytes(source_bytes)
    # Give generated methods the approved supporting files as immutable bytes,
    # rather than forcing filesystem access or pretending the template is absent.
    inputs = []
    if goal_contract is not None:
        from .goals import goal_templates
        try:
            for index, path in enumerate(goal_templates(goal_contract)):
                data = path.read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                approved = {e["template_sha256"] for e in goal_contract.get("effects", [])
                            if e.get("template") and Path(e["template"]).resolve() == path}
                if approved != {digest}:
                    raise ValueError("approved template bytes have changed: " + str(path))
                snapshot = workdir / f"input-{index}.docx"
                snapshot.write_bytes(data)
                aliases = sorted({e["template"] for e in goal_contract.get("effects", [])
                                  if e.get("template") and Path(e["template"]).resolve() == path} | {str(path)})
                inputs.append({"path": str(path), "aliases": aliases, "snapshot": str(snapshot), "sha256": digest})
        except (ValueError, OSError) as exc:
            return SandboxResult(False, error=str(exc), code="stale-template", seconds=time.perf_counter() - t0)
    req = workdir / "request.json"
    req.write_text(json.dumps({"source": source, "docx": str(copy_path), "select": select, "params": params,
                              "goal_contract": goal_contract, "goal_binding": goal_binding,
                              "inputs": inputs, "limits": limits}),
                   encoding="utf-8")
    diag.event("codegen.sandbox.prepared", source_copy=str(copy_path), request_path=str(req))
    env = {k: v for k, v in os.environ.items() if k.upper() in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "PYTHONIOENCODING")}
    stdout_path, stderr_path = workdir / "sandbox-stdout.txt", workdir / "sandbox-stderr.txt"

    def read_log(path):
        # The runner bounds normal Python diagnostics. Files avoid unbounded parent
        # RAM buffering even if a native library writes directly to its descriptors.
        try:
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - limits["log_bytes"]))
                return stream.read(limits["log_bytes"]).decode("utf-8", "replace")
        except OSError:
            return "sandbox diagnostics could not be read"

    try:
        with diag.span("codegen.sandbox.process", executable=sys.executable, timeout_seconds=timeout) as record:
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                proc = subprocess.run([sys.executable, "-I", "-m", "spec_surgeon.sandbox_runner", str(req)], cwd=str(workdir),
                                      env=env, timeout=timeout, stdout=stdout, stderr=stderr, check=False)
            record["returncode"] = proc.returncode
    except subprocess.TimeoutExpired as e:
        diag.event("codegen.sandbox.timeout", level="warning", timeout_seconds=timeout)
        err = read_log(stderr_path) or ((e.stderr or b"").decode("utf-8", "replace")
                                      if isinstance(e.stderr, bytes) else str(e.stderr or ""))
        return SandboxResult(False, error=f"timed out after {timeout}s", code="timeout",
                             seconds=time.perf_counter() - t0, stderr=_tail(err))
    stderr = read_log(stderr_path)
    res_path = workdir / "result.json"
    diag.event("codegen.sandbox.exited", returncode=proc.returncode, result_present=res_path.is_file(),
               stderr_characters=len(stderr), stderr_path=str(workdir / "sandbox-stderr.txt"))
    try:
        if any(path.stat().st_size > limits["log_bytes"] for path in (stdout_path, stderr_path)):
            return SandboxResult(False, error="generated-transform diagnostics exceeded their configured output allowance",
                                 code="resource-limit", seconds=time.perf_counter() - t0, stderr=_tail(stderr, 40),
                                 returncode=proc.returncode)
        if not res_path.is_file():
            diag.event("codegen.sandbox.result_missing", level="error", returncode=proc.returncode)
            raise ValueError(f"sandbox process died without a result (exit code {proc.returncode})")
        for path, ceiling in ((res_path, MAX_RESULT_BYTES), (workdir / "out.docx", limits["output_bytes"]),
                              (workdir / "plan.json", min(limits["output_bytes"], 16 * 1024 * 1024))):
            if path.exists() and path.stat().st_size > ceiling:
                return SandboxResult(False, error=f"generated-transform {path.name} exceeded its output allowance",
                                     code="resource-limit", seconds=time.perf_counter() - t0)
        res = json.loads(res_path.read_text(encoding="utf-8"))
        if not isinstance(res, dict) or type(res.get("ok")) is not bool:
            raise ValueError("sandbox result has an invalid shape")
        if not res["ok"]:
            return SandboxResult(False, error=res.get("error"), code=res.get("code"), seconds=time.perf_counter() - t0,
                                 traceback=res.get("traceback") or "", stderr=_tail(stderr, 40),
                                 returncode=proc.returncode)
        output, plan_path = workdir / "out.docx", workdir / "plan.json"
        if not output.is_file():
            raise ValueError("sandbox reported success without an output document")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        if not isinstance(plan, dict):
            raise ValueError("sandbox plan has an invalid shape")
        return SandboxResult(True, output=output, plan=plan, seconds=time.perf_counter() - t0)
    except (OSError, ValueError, TypeError, RecursionError) as error:
        return SandboxResult(False, error=f"sandbox returned missing or malformed artifacts ({type(error).__name__}: {str(error)[:300]})",
                             code="crash", seconds=time.perf_counter() - t0, stderr=_tail(stderr, 40),
                             returncode=proc.returncode)


def hard_limits(plan: Dict[str, Any], allowed_parts: Optional[Sequence[str]] = None, *, goal_contract=None) -> List[str]:
    """Check a generated plan before the suite runs; governed methods also face the independent final-goal check.
    Legacy ``allowed_parts``: the source's
    text parts and its styles part — the parts V10's residual identity covers; any other part (numbering.xml, docProps,
    a chart…) could carry a change no verified assertion checks, so it is refused outright."""
    probs = []
    if goal_contract is not None:
        from .goals import validate_contract
        validate_contract(goal_contract)
        # The fixed original-to-final goal verifier controls every permitted
        # change for this path, including literals, revisions and member graph.
        # This branch is never itself permission to deliver a generated result.
        if plan.get("geometry_reference") == "none" and not (plan.get("geometry_reason") or "").strip():
            probs.append("geometry_reference 'none' needs a geometry_reason")
        return probs
    removed, added = set(plan.get("members_removed") or []), set(plan.get("members_added") or [])
    kept = [d for d in plan.get("diffs") or [] if d["part"] not in removed and d["part"] not in added]
    if kept:
        probs.append(f"generated ops may not change text in surviving parts ({len(kept)} block(s) predicted)")
    if plan.get("members_added"):
        probs.append("generated ops may not add package members")
    deltas = {k: v for k, v in (plan.get("rev_delta") or {}).items() if v}
    if deltas:
        probs.append(f"generated ops are plain formatting: no tracked change or revision of any kind ({sorted(deltas)})")
    if any(p.endswith("settings.xml") for p in plan.get("intended_parts") or []):
        probs.append("generated ops may not edit settings.xml")
    if allowed_parts is not None:
        outside = sorted({p for p in list(plan.get("intended_parts") or []) + sorted(removed)
                          if p not in set(allowed_parts)})
        if outside:
            probs.append(f"generated ops may change only the document's text parts and its styles part — no verified "
                         f"assertion kind covers {outside}")
    if plan.get("geometry_reference") == "none" and not (plan.get("geometry_reason") or "").strip():
        probs.append("geometry_reference 'none' needs a geometry_reason")
    return probs


def allowed_parts_of(docx: Path, *, source_bytes=None) -> List[str]:
    """The parts a generated op may change in this file: its text parts and its styles part."""
    from .ops.selectors import StyleSheet
    from .package import DocxPackage
    pkg = DocxPackage.from_path(docx) if source_bytes is None else DocxPackage(source_bytes, name=str(docx))
    parts = list(pkg.text_parts())
    try:
        sp = StyleSheet(pkg).part
        if sp:
            parts.append(sp)
    except Exception:  # noqa: BLE001 — a file without a styles part: only its text parts may change
        pass
    return parts


@dataclass
class FileTrial:
    source: Path
    passed: bool
    problems: List[str] = field(default_factory=list)
    output: Optional[Path] = None
    record: Optional[Any] = None
    output_md5: str = ""                          # the bytes the suite verified (re-checked before any copy)
    identity: Optional[Any] = None
    goal_binding: Optional[Dict[str, Any]] = None

    def to_dict(self):
        return {"source": str(self.source), "passed": self.passed, "problems": self.problems,
                "output": str(self.output) if self.output else None, "output_md5": self.output_md5,
                "identity": self.identity.to_dict() if self.identity is not None else None,
                "goal_binding": self.goal_binding}


def trial(source: str, f: Path, select, params, workdir: Path, *, goal_contract=None, goal_binding=None) -> FileTrial:
    with diag.scope(trial_id=diag.new_id()), \
            diag.span("codegen.trial", source=str(f), workdir=str(workdir)) as record:
        result = _trial(source, f, select, params, workdir, goal_contract, goal_binding)
        record.update(passed=result.passed, problem_count=len(result.problems), output_md5=result.output_md5,
                      failed_checks=[c.id for c in result.record.checks if c.status == "FAIL"]
                      if result.record is not None else [])
        return result


def _trial(source, f, select, params, workdir, goal_contract, goal_binding):
    from .verify import CheckResult, run_suite
    f = Path(f).resolve()
    try:
        identity = guard.acquire(f)
        original = f.read_bytes()
        if hashlib.md5(original).hexdigest() != identity.md5 or guard.drifted(identity):
            raise guard.GuardError("source changed while acquiring the generated trial's original bytes")
    except (OSError, guard.GuardError) as error:
        return FileTrial(f, False, [f"source acquisition refused: {error}"])

    def failed(problems, output=None):
        return FileTrial(f, False, problems, output, identity=identity, goal_binding=goal_binding)

    if goal_contract is not None:
        from .goals import validate_contract, validate_binding
        try:
            validate_contract(goal_contract)
            checked = validate_binding(f, goal_contract, goal_binding)
            if goal_binding is not None and checked != goal_binding:
                raise ValueError("the saved approved binding is incomplete; review its targets again")
            goal_binding = checked
            if hashlib.sha256(original).hexdigest() != goal_binding["source_sha256"]:
                raise guard.GuardError("source changed while checking its approved binding")
        except Exception as error:
            return failed([f"approved goal binding refused: {error}"])
    sb = run_in_sandbox(source, f, select, params, workdir, goal_contract=goal_contract,
                        goal_binding=goal_binding, source_bytes=original)
    if not sb.ok:
        where = _tail(sb.traceback, 12)
        return failed([f"[{sb.code or 'error'}] {sb.error}"
                       + (f"\n(traceback, last lines:)\n{where}" if where else "")])
    try:
        with diag.span("codegen.trial.limits") as record:
            limits = hard_limits(sb.plan, allowed_parts_of(f, source_bytes=original), goal_contract=goal_contract)
            record.update(accepted=not bool(limits), problem_count=len(limits))
    except Exception as e:  # noqa: BLE001 — a plan the engine cannot even read is a failed trial, with the reason
        return failed([f"the generated op's plan is malformed ({type(e).__name__}: {e})"], sb.output)
    if limits:
        return failed(limits, sb.output)
    try:
        out_bytes = sb.output.read_bytes()
        with diag.span("codegen.trial.verify") as outcome:
            rec = run_suite(original, out_bytes, sb.plan, str(f), str(sb.output))
            outcome.update(passed=rec.passed, checks={c.id: c.status for c in rec.checks})
    except Exception as e:  # noqa: BLE001 — an output the suite cannot read (a part removed, a broken zip): FAIL
        return failed([f"the verifier could not check the output at all ({type(e).__name__}: {e}); "
                       "the generated op produced a package the suite cannot read — a removed or broken part?"], sb.output)
    if goal_contract is not None:
        from .goals import verify_goal
        try:
            goal_result = verify_goal(original, out_bytes, goal_contract, goal_binding)
        except Exception as error:
            goal_result = {"passed": False, "errors": [f"goal verifier could not check output: {error}"]}
        rec.checks.append(CheckResult("GOAL", "Approved original-to-final effects",
                                      "PASS" if goal_result.get("passed") else "FAIL",
                                      "approved effects independently verified" if goal_result.get("passed")
                                      else "; ".join(goal_result.get("errors", [])), goal_result))
    try:
        drift = guard.drifted(identity)
    except OSError as error:
        drift = str(error)
    if drift:
        rec.set(CheckResult("V9", "Source identity", "FAIL", f"source changed during generated trial: {drift}"))
    fails = [f"{c.id} FAIL: {c.summary[:600]}" for c in rec.checks if c.status == "FAIL"]
    return FileTrial(f, rec.passed, fails, sb.output, rec, hashlib.md5(out_bytes).hexdigest(), identity, goal_binding)


@dataclass
class GeneratedOp:
    source: str = ""
    name: str = ""
    passed: bool = False
    path: Optional[Path] = None
    select: Dict[str, Any] = field(default_factory=dict)
    params: Dict[str, Any] = field(default_factory=dict)
    attempts: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    model: str = ""
    usage: Usage = field(default_factory=Usage)
    trials: List[FileTrial] = field(default_factory=list)   # the passing attempt's trials (their outputs are previewed)
    request: str = ""
    proposal: str = ""
    brief: str = ""
    transcript: List[Dict[str, Any]] = field(default_factory=list)   # the exchange with the model (diagnostics)
    seconds: float = 0.0
    context_state: Dict[str, Any] = field(default_factory=dict)
    goal_contract: Optional[Dict[str, Any]] = None
    goal_bindings: Dict[str, Any] = field(default_factory=dict)
    approved_words: List[str] = field(default_factory=list)


def _canonical_bindings(bindings):
    """Path spelling must not discard a source-bound choice from an approved review."""
    import copy
    result = {}
    for path, binding in (bindings or {}).items():
        name = str(Path(path).resolve())
        if name in result and result[name] != binding:
            raise ValueError("conflicting approved bindings name the same source document")
        result[name] = copy.deepcopy(binding)
    return result


CODEGEN_SYSTEM = """You write ONE Python module: an operation (an `Op` subclass) for a deterministic .docx editing \
engine, for a request its operation library cannot express. Your module is checked by a whitelist, executed in a \
sandbox on copies of sample files, and judged by an independent verification suite. You never see the verifier's \
code; you see its record when a check fails.

Before you write, look: show_xml shows the real XML of paragraphs of a sample file, find_paragraphs finds paragraphs \
by a regex over their text, list_styles and show_part_text show styles and part texts. The user's own words in the \
brief say what they want; Claude's earlier understanding is context, not instructions.

Rules:
- Imports: only `from <module> import <names>` from this whitelist: {allowed}; plus `import` of {plain}. No other \
imports, no file or network access, no open/exec/eval/getattr, no dunder names or attributes except __init__. A plainly imported module (and etree) is used ONLY as `module.name` with these names: {module_attrs} — never passed, assigned or returned as an object. No attributes starting with f_, gi_, cr_, ag_, tb_, co_ (frames, generators).
- Define exactly one class deriving from Op with: name, mode = "formatting", description, params_schema, \
select_schema, plan(self, pkg, select, params) -> PlanFragment, apply(self, pkg, frag) -> None. No decorators \
from the engine (no @register).
- plan() must PREDICT everything from the source package analytically, before apply() runs: intended_parts \
(every part whose bytes change), property_assertions (one per changed property family; V10 compares everything \
else in changed parts and styles.xml for identity, so an unasserted change FAILS), touches, notes, \
geometry_reference/geometry_reason. Use self.fragment(select, params) to start the fragment.
- apply() mutates the working package only through pkg.xml(part) / pkg.write_xml(part, root); it reads what plan() \
put in frag.transform_data (JSON-serialisable values only).
- PLAIN formatting only: never add, remove or change document text; never create a tracked change or a property \
revision (no rPrChange/pPrChange — the reference ops' tracked branches use helpers you do not have: drop those \
branches, plan untracked, rev_delta stays empty); never touch settings.xml; change only the document's text parts \
(body, headers, footers, footnotes, endnotes, comments) and its styles part.
- Only these property families can be verified (anything else fails V10 — say so instead): {limits}
- If the request cannot be done safely on a file, raise JobException(reason, code) instead of guessing.

Reference ops from the library (relative imports there become the absolute whitelist imports here):
{examples}

Finish by calling submit_op with the complete module source, the selector and the params to run it with."""

CODEGEN_READ_TOOLS = ("show_xml", "find_paragraphs", "list_styles", "show_part_text") + tuple(t["name"] for t in INSPECTION_TOOLS)
CODEGEN_TOOLS = INSPECTION_TOOLS + [
    {"name": "show_xml", "description": "The XML of paragraphs of one part of a sample file (pretty-printed), by "
     "paragraph index (0-based, every w:p of the part in document order).",
     "input_schema": {"type": "object", "properties": {
         "file": {"type": "string"}, "part": {"type": "string", "description": "default word/document.xml"},
         "start": {"type": "integer", "minimum": 0}, "count": {"type": "integer", "minimum": 1, "maximum": 20}},
         "required": ["file"]}},
    {"name": "find_paragraphs", "description": "Paragraphs of one part whose text matches a Python regex: index, "
     "style and text.",
     "input_schema": {"type": "object", "properties": {
         "file": {"type": "string"}, "regex": {"type": "string"}, "part": {"type": "string"}},
         "required": ["file", "regex"]}},
    {"name": "list_styles", "description": "Every style defined in a sample file: id, type, name, basedOn, fonts/size.",
     "input_schema": {"type": "object", "properties": {"file": {"type": "string"}}, "required": ["file"]}},
    {"name": "show_part_text", "description": "The paragraph texts of one part of a sample file.",
     "input_schema": {"type": "object", "properties": {"file": {"type": "string"}, "part": {"type": "string"}},
                      "required": ["file", "part"]}},
    {"name": "submit_op", "description": "Submit the op module source with the select/params to run it with (one "
     "submission per turn; it is tried on every sample file).",
     "input_schema": {"type": "object", "properties": {
         "source": {"type": "string"}, "select": {"type": "object"}, "params": {"type": "object"},
         "notes": {"type": "string"}}, "required": ["source"]}},
]


def _examples() -> str:
    here = Path(__file__).resolve().parent / "ops"
    out = []
    for name in EXAMPLE_OPS:
        p = here / name
        if p.is_file():
            out.append(f"===== ops/{name} =====\n{p.read_text(encoding='utf-8')}")
    return "\n\n".join(out)


def system_prompt(goal_contract=None) -> str:
    from .catalog import CUSTOM_TRANSFORM_LIMITS
    prompt = CODEGEN_SYSTEM.format(allowed=json.dumps({k: sorted(v) for k, v in allowed_imports(goal_contract).items()}),
                                 plain=sorted(ALLOWED_IMPORT), examples=_examples(),
                                 module_attrs=json.dumps({k: sorted(v) for k, v in MODULE_ATTRS.items()}),
                                 limits=json.dumps(CUSTOM_TRANSFORM_LIMITS, ensure_ascii=False))
    if goal_contract is not None:
        start, end = prompt.index("- PLAIN formatting only:"), prompt.index("- Only these property families")
        prompt = prompt[:start] + (
            "- CONTRACT-GOVERNED METHOD: implement exactly the approved goal below. Text, tracked revisions or "
            "structural changes are permitted only when that goal explicitly approves them. Every inserted literal "
            "must be an exact approved replacement/insertion value. No other goal field is approved wording. "
            "The full V1–V10 suite AND a fixed independent original-to-final goal verifier must both pass. "
            "Your own plan cannot expand the contract's scope. Do not write or propose your own verifier. "
            "Use mode = 'content' for tracked wording changes and 'untracked' only when the contract explicitly "
            "requests untracked wording changes. Tracked helpers and pure textedit helpers listed in "
            "the import whitelist are available on this path. For wording edits, call plan_paragraph(..., "
            "author=approved_author), preserve its paragraph_edits assertions, and use the same approved author "
            "when applying tracked changes. BlockDiff predictions alone do not prove preservation of other "
            "paragraph items. Predict all text/revision/member changes first.\n"
        ) + prompt[end:]
        prompt = prompt.replace('name, mode = "formatting", description', 'name, mode, description')
        start = prompt.index("- apply() mutates")
        end = prompt.index("- CONTRACT-GOVERNED METHOD", start)
        prompt = prompt[:start] + (
            "- apply() changes only the working package. Use pkg.xml / pkg.write_xml for XML and "
            "pkg.add_member / pkg.remove_member / pkg.edit_rels for approved package graph changes. "
            "All member, relationship, content-type and text changes must be predicted in plan first.\n"
        ) + prompt[end:]
        # The legacy formatting allowlist is not the capability boundary of an
        # approved, independently verified outcome. Leaving it here told writers
        # to refuse header/footer graphs even on the governed path.
        start = prompt.index("- Only these property families")
        end = prompt.index("- If the request cannot", start)
        from .verify.props import KINDS
        prompt = prompt[:start] + (
            "- Use existing independent V10 assertion kinds for every change. Available kind names: "
            + json.dumps(sorted(KINDS)) + ". Header/footer templates use template_parts, styles_added and "
            "hf_template_refs; their source/template/output checks cover copied text, fields, styles and the "
            "package graph. Those changes are permitted when the approved contract includes them. If an "
            "assertion cannot describe the intended change, report that specific verification gap.\n"
            "- Supporting templates are available in both plan and apply as pkg.inputs: an immutable mapping "
            "from each approved absolute template path to its pinned bytes. Use DocxPackage(pkg.inputs[path]) "
            "to inspect or copy their XML/media. Do not open a path or mutate an input. The host rechecks input "
            "hashes; all template-dependent assertions still name the original approved template path.\n"
            "- Write reusable governed methods: read this job's contract from pkg.goal and this file's validated "
            "targets from pkg.binding in plan and apply. These are fresh job-local copies, not authority you can "
            "change. Prefer empty select/params and derive all wording, target indices, template paths and values "
            "from those current inputs. Successful parameter-free methods can be tried on related new jobs; "
            "hardcoded project wording or indices will fail that job's independent checks.\n"
        ) + prompt[end:]
        prompt += "\nAPPROVED IMMUTABLE GOAL:\n" + json.dumps(goal_contract, ensure_ascii=False, sort_keys=True)
        helper = Path(__file__).resolve().parent / "ops" / "textedit.py"
        prompt += "\nPure text-edit planning/application helper reference:\n" + helper.read_text(encoding="utf-8")
        if any(e.get("type") == "header_footer_template" for e in goal_contract.get("effects", [])):
            reference = Path(__file__).resolve().parent / "ops" / "apply_header_footer_template.py"
            prompt += ("\nHeader/footer reference implementation (reference only; imports still obey the whitelist; "
                       "replace external template reads with pkg.inputs):\n" + reference.read_text(encoding="utf-8"))
    return prompt


def generate_op(request: str, proposal: str, samples: Sequence[Path], out_dir: Path, client=None,
                model: Optional[str] = None, max_iter: int = MAX_ITER, log=None, brief: Optional[str] = None,
                max_calls: int = MAX_CALLS, max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
                effort: str = DEFAULT_EFFORT, stall_repetitions: int = DEFAULT_STALL_REPETITIONS,
                max_context_tokens: int = DEFAULT_CONTEXT_TOKENS, analysis_limits=None,
                goal_contract=None, goal_bindings=None, approved_words: Sequence[str] = ()) -> GeneratedOp:
    """Look → generate → sandbox + verify on the samples → feed failures back: at most ``max_iter`` submissions and
    ``max_calls`` calls. Every tool call is answered in the next message. Existing
    outer budget scopes still apply to every call; local settings cannot bypass them."""
    with diag.scope(Path(out_dir) / diag.LOGS / "events.jsonl", codegen_id=diag.new_id()), \
            diag.span("codegen.generate", sample_count=len(samples), max_attempts=max_iter) as record:
        result = _generate_op(request, proposal, samples, out_dir, client, model, max_iter, log, brief,
                              max_calls, max_output_tokens, effort, stall_repetitions, max_context_tokens,
                              analysis_limits, goal_contract, goal_bindings, approved_words)
        record.update(passed=result.passed, attempts=len(result.attempts), errors=len(result.errors))
        return result


def _generate_op(request, proposal, samples, out_dir, client, model, max_iter, log, brief,
                 max_calls, max_output_tokens, effort, stall_repetitions, max_context_tokens,
                 analysis_limits, goal_contract, goal_bindings, approved_words):
    from . import compiler, llm
    from .profile import profile
    from .package import DocxPackage
    positive_integer("max_iter", max_iter)
    positive_integer("max_calls", max_calls)
    positive_integer("max_context_tokens", max_context_tokens)
    policy = llm.CallBudget(effort=effort, max_output_tokens=max_output_tokens)
    progress = InvestigationProgress(stall_repetitions)
    log = log or (lambda m: None)
    model = model or compiler.DEFAULT_MODEL
    out_dir = Path(out_dir)
    # Contract wording is checked against explicit caller-owned sources before the
    # model or sandbox runs. Neither proposal, generated source nor brief grants it.
    checked_bindings = {}
    supplied_bindings = _canonical_bindings(goal_bindings) if goal_contract is not None else {}
    if goal_contract is not None:
        from .goals import approved_goal_words, validate_binding, validate_contract
        validate_contract(goal_contract)
        literals = approved_goal_words(goal_contract)
        wording_spec = {"ops": [{"op": "insert_paragraph", "params": {"text": word}} for word in literals]}
        missing = compiler.untraceable(wording_spec, [request, *approved_words])
        if missing:
            raise ValueError(f"goal contains wording without user approval: {missing}")
        for sample in samples:
            checked_bindings[str(Path(sample).resolve())] = validate_binding(sample, goal_contract,
                                                                           supplied_bindings.get(str(Path(sample).resolve())))
    import copy
    goal_contract = copy.deepcopy(goal_contract)
    system = system_prompt(goal_contract)
    profiles = [profile(DocxPackage.from_path(f)) for f in samples]
    from .goals import goal_templates
    templates = goal_templates(goal_contract) if goal_contract is not None else []
    batch = compiler._Batch(list(samples), templates[0] if templates else None)
    for path in templates:
        batch.files[str(path)] = path
        if not any(pin["path"] == str(path) for pin in batch.source_pins):
            batch.source_pins.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    for effect in (goal_contract or {}).get("effects", []):
        if effect.get("template"):
            batch.files[effect["template"]] = Path(effect["template"]).resolve()
    batch.work_dir = out_dir
    batch.analysis_limits = analysis_limits
    batch.context_dir = out_dir
    asked = (f"THE CONVERSATION WITH THE USER (what they asked, what was understood, every reply):\n{brief}"
             if brief else f"REQUEST: {request}")
    messages: List[Dict[str, Any]] = [{"role": "user", "content":
        f"{asked}\n\nWHY NO EXISTING OP FITS / PROPOSAL: {proposal}\n\nSAMPLE FILES (for the tools): "
        f"{[batch.label(Path(f)) for f in samples]}\n\nSAMPLE FILE PROFILES:\n"
        + json.dumps(profiles, ensure_ascii=False)}]
    # Full-batch bindings can include deliberate user choices on non-sample files.
    # Keep them, then validate each one afresh when that file actually runs.
    retained_bindings = copy.deepcopy(supplied_bindings)
    retained_bindings.update(checked_bindings)
    g = GeneratedOp(model=model, request=request, proposal=proposal or "", brief=brief or "",
                    goal_contract=goal_contract, goal_bindings=retained_bindings, approved_words=list(approved_words))
    batch.context_state = g.context_state
    g.transcript.append({"turn": 0, "role": "user", "content": messages[0]["content"],
                         "samples": [str(f) for f in samples]})
    t_all = time.perf_counter()

    def done() -> GeneratedOp:                     # every way out keeps codegen/history.json
        from .contexts import checkpoint_history
        checkpoint_history(messages, **context_arguments())
        g.seconds = time.perf_counter() - t_all
        _save_history(g, out_dir)
        return g

    def context_arguments():
        return {"system": system, "tools": CODEGEN_TOOLS, "folder": out_dir, "state": g.context_state,
                "authority": {"request": request, "files": batch.labels, "goal_contract": goal_contract,
                              "goal_bindings": checked_bindings, "approved_word_sources": list(approved_words),
                              "proposal_diagnostic_only": proposal}, "source_pins": batch.source_pins}

    def client_now():
        nonlocal client
        if client is None:
            client = compiler._client()
        return client
    calls = 0
    while len(g.attempts) < max_iter and calls < max_calls:
        from .contexts import ContextLimit, manage_context
        try:
            messages = manage_context(messages, max_tokens=max_context_tokens, **context_arguments())
        except ContextLimit as error:
            g.errors.append(str(error))
            return done()
        calls += 1
        t_call = time.perf_counter()
        try:
            resp = llm.call(client_now(), model=model, system=system, tools=CODEGEN_TOOLS, messages=messages,
                            max_output_tokens=policy.max_output_tokens, effort=policy.effort)
        except Exception as e:  # noqa: BLE001 — classified below
            if not llm.is_api_error(e) and not isinstance(e, llm.BudgetExceeded):
                raise                          # a defect (a wrong argument, a test's script running out) propagates
            g.errors.append(llm.failure_text(e, "the call to the model failed"))
            g.transcript.append(llm.error_entry(calls, e, time.perf_counter() - t_call))
            return done()
        g.usage.add(resp, model)
        g.transcript.append(llm.response_entry(calls, resp, time.perf_counter() - t_call))
        problem = llm.stop_problem(resp)
        if problem:
            g.errors.append(f"model stopped: {problem}")
            return done()
        content = llm.history_content(resp)
        uses = llm.tool_uses(content)
        if not content:
            g.errors.append("the model returned an empty answer")
            return done()
        messages.append({"role": "assistant", "content": content})
        if not uses:
            nudge = "Call submit_op with the module source."
            messages.append({"role": "user", "content": nudge})
            g.transcript.append({"turn": calls, "role": "user", "content": nudge})
            continue
        results: List[Dict[str, Any]] = []
        submitted = False
        stalled = False
        for b in uses:
            inp = dict(b.input or {})
            if b.name == "submit_op":
                if submitted:
                    results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                                    "content": "one submission per turn — this one was not tried"})
                    continue
                submitted = True
                attempt = len(g.attempts) + 1
                src, select, params = inp.get("source") or "", inp.get("select") or {}, inp.get("params") or {}
                with diag.scope(codegen_attempt=attempt), \
                        diag.span("codegen.attempt", model_turn=calls, tool_use_id=b.id) as record:
                    problems = check_source(src, goal_contract=goal_contract)
                    diag.event("codegen.source_checked", accepted=not bool(problems), problem_count=len(problems))
                    trials: List[FileTrial] = []
                    if not problems:
                        for i, f in enumerate(samples):
                            t = trial(src, Path(f), select, params, out_dir / "sandbox" / f"attempt{attempt}" / f"{i:02d}",
                                      goal_contract=goal_contract, goal_binding=checked_bindings.get(str(Path(f).resolve())))
                            trials.append(t)
                            problems += [f"{Path(f).name}: {p}" for p in t.problems]
                    record.update(passed=not bool(problems), problem_count=len(problems), trial_count=len(trials))
                g.attempts.append({"attempt": attempt, "problems": problems, "trials": [t.to_dict() for t in trials],
                                   "source": src, "select": select, "params": params})
                log(f"codegen attempt {attempt}: {'PASS' if not problems else f'{len(problems)} problem(s)'}")
                if not problems:
                    g.source, g.select, g.params, g.passed, g.trials = src, select, params, True, trials
                    g.name = _op_name(src)
                    g.path = save_generated(g, out_dir)
                    return done()                  # after the result is set: the history says it passed
                results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                                "content": ("FAILED — fix and submit again:\n- " + "\n- ".join(problems))[:12000]})
                try:
                    executable_source = ast.dump(ast.parse(src), include_attributes=False)
                except (SyntaxError, ValueError, TypeError):
                    executable_source = src
                stalled = progress.rejected([executable_source, select, params], problems)
                continue
            try:
                if b.name not in CODEGEN_READ_TOOLS:
                    raise KeyError(f"unknown tool {b.name}")
                out = getattr(batch, b.name)(**inp)
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": json.dumps(out, ensure_ascii=False)})
                if progress.observe({"tool": b.name, "content": out}):
                    stalled = False
            except Exception as e:  # noqa: BLE001 — every tool call gets an answer, or the next request is refused
                results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                                "content": f"{type(e).__name__}: {e}"[:2000]})
        messages.append({"role": "user", "content": results})
        g.transcript.append(llm.results_entry(calls, results))
        g.seconds = time.perf_counter() - t_all
        _save_history(g, out_dir)                  # every attempt kept as it happens
        if stalled:
            g.errors.append(f"no passing op after {len(g.attempts)} attempt(s): investigation stalled; "
                            "equivalent rejected transforms repeated without new evidence")
            return done()
    g.errors.append(f"no passing op after {len(g.attempts)} attempt(s)" if len(g.attempts) >= max_iter else
                    f"no passing op after {calls} calls ({len(g.attempts)} attempt(s))")
    return done()


def _save_history(g: GeneratedOp, out_dir: Path) -> None:
    """Every attempt — the source the model submitted, the whitelist and sandbox problems, each sample's verification
    — and the whole exchange in ``codegen/history.json``, passing or not (the diagnosis of a transform that never
    passed). A history that cannot be written never stops the work (``diagnostics.write_json``)."""
    from .diagnostics import write_json
    write_json(Path(out_dir) / "history.json", {"passed": g.passed, "name": g.name, "errors": g.errors,
                                                "request": g.request, "proposal": g.proposal, "brief": g.brief,
                                                "model": g.model, "usage": g.usage.to_dict(),
                                                "seconds": round(g.seconds, 2), "attempts": g.attempts,
                                                "transcript": g.transcript, "context": g.context_state,
                                                "goal_contract": g.goal_contract, "goal_bindings": g.goal_bindings})


def pick_samples(files: Sequence[Path], count: Optional[int] = None) -> List[Path]:
    """Representative trial files, identified by full path. Automatic selection covers
    every readable structural pattern and fills the shared sample allowance;
    an explicit count overrides it. Every delivered file is still verified.
    """
    from .profile import profile_batch
    from .sampling import representative_samples
    files = [Path(f) for f in files]
    candidates, pattern_ids = [], {}
    for pat in profile_batch(files)["patterns"]:
        paths = [Path(p) for p in pat.get("paths") or []]
        if not paths:
            paths = [f for f in files if f.name in pat["files"]]
        for path in paths:
            if path not in pattern_ids:
                candidates.append(path)
                pattern_ids[path] = pat["pattern_id"]
    return representative_samples(candidates, pattern_key=pattern_ids.__getitem__, count=count)


def _op_name(src: str) -> str:
    try:
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "name" for t in node.targets) \
                    and isinstance(node.value, ast.Constant):
                return str(node.value.value)
    except SyntaxError:
        pass
    return "generated"


def save_generated(g: GeneratedOp, out_dir: Path) -> Path:
    h = hashlib.sha256(g.source.encode("utf-8")).hexdigest()[:12]
    d = Path(out_dir) / "generated_ops"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{h}.py"
    p.write_text(g.source, encoding="utf-8")
    (d / f"{h}.history.json").write_text(json.dumps({"name": g.name, "select": g.select, "params": g.params,
                                                     "request": g.request, "proposal": g.proposal, "brief": g.brief,
                                                     "goal_contract": g.goal_contract, "goal_bindings": g.goal_bindings,
                                                     "approved_words": g.approved_words,
                                                     "model": g.model, "usage": g.usage.to_dict(),
                                                     "attempts": g.attempts}, indent=2, default=str), encoding="utf-8")
    return p


def run_generated(g: GeneratedOp, inputs: Sequence[str], out_dir: Path, log=None, *,
                  approved_inputs=None) -> List[FileTrial]:
    """Apply a passing generated op to every input (sandbox per file, verifier per file). Verified outputs are copied
    to ``out_dir/outputs`` under names unique across the batch, and only when their bytes are the ones the suite
    verified; records to ``out_dir/records``. Never in place: an input inside the results folder is refused before
    anything is written (the same rules as a batch run)."""
    import copy
    from .batch import check_inputs_outside, clear_delivery_folders, expand_inputs, output_names
    g = copy.copy(g)
    for key in ("select", "params", "goal_contract", "goal_bindings", "approved_words"):
        setattr(g, key, copy.deepcopy(getattr(g, key)))
    approved_inputs = copy.deepcopy(approved_inputs)
    if approved_inputs is not None:
        from .preview import generated_key
        digest = hashlib.sha256(json.dumps(generated_key(g), sort_keys=True,
                                           ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
        if not isinstance(approved_inputs, dict) or approved_inputs.get("spec") != digest:
            raise ValueError("the generated transform changed since approval; preview again")
    g.goal_bindings = _canonical_bindings(g.goal_bindings)
    log = log or (lambda m: None)
    out_dir = Path(out_dir)
    files = expand_inputs(inputs)
    from .goals import goal_templates
    auxiliary = goal_templates(g.goal_contract) if g.goal_contract is not None else []
    check_inputs_outside([*files, *auxiliary], out_dir)
    source_pins, acquisition_errors = _generated_source_pins(files, approved_inputs)
    with diag.scope(out_dir / diag.LOGS / "events.jsonl", generated_run_id=diag.new_id()), \
            diag.span("codegen.run", file_count=len(files)) as record:
        clear_delivery_folders(out_dir)
        result = _run_generated_files(g, files, out_dir, log, output_names, source_pins, acquisition_errors)
        record.update(passed=sum(t.passed for t in result), failed=sum(not t.passed for t in result))
        return result


def _generated_source_pins(files, approved_inputs):
    """Pin every queued original before work; a preview pin cannot silently become a new acquisition."""
    expected = None
    if approved_inputs is not None:
        if not isinstance(approved_inputs, dict) or not isinstance(approved_inputs.get("files"), dict):
            raise ValueError("approved generated inputs have no source identity pins; preview again")
        expected = {}
        for path, value in approved_inputs["files"].items():
            if (not isinstance(path, str) or not isinstance(value, list) or len(value) != 3
                    or isinstance(value[0], bool) or not isinstance(value[0], int) or value[0] < 0
                    or isinstance(value[1], bool) or not isinstance(value[1], int)
                    or not isinstance(value[2], str) or len(value[2]) != 32
                    or any(character not in "0123456789abcdef" for character in value[2])):
                raise ValueError("approved generated source identity pins are malformed; preview again")
            key = str(Path(path).resolve())
            if key in expected and expected[key] != value:
                raise ValueError("approved generated inputs contain conflicting source identities")
            expected[key] = value
        if set(expected) != {str(file.resolve()) for file in files}:
            raise ValueError("generated source files changed since approval; preview again")
    pins, errors = {}, {}
    for file in files:
        key = str(file.resolve())
        try:
            identity = guard.acquire(key)
            drift = guard.drifted(identity)
            if drift:
                raise guard.GuardError(drift)
        except (OSError, guard.GuardError) as error:
            if expected is not None:
                raise ValueError(f"approved generated source {file.name} changed or is unavailable: {error}") from error
            errors[key] = str(error)
            continue
        if expected is not None and expected[key] != [identity.size, identity.mtime_ns, identity.md5]:
            raise ValueError(f"generated source {file.name} changed since approval; preview again")
        pins[key] = identity
    return pins, errors


def _run_generated_files(g, files, out_dir, log, output_names, source_pins, acquisition_errors):
    names = output_names(files)
    results = []
    _save_generated_run(g, files, results, out_dir)
    with tempfile.TemporaryDirectory() as tmp:
        for i, f in enumerate(files):
            with diag.scope(source=str(f)), diag.span("codegen.file") as outcome:
                key = str(f.resolve())
                identity = source_pins.get(key)
                try:
                    if identity is None:
                        raise guard.GuardError(acquisition_errors.get(key, "original identity is missing"))
                    drift = guard.drifted(identity)
                    if drift:
                        raise guard.GuardError(f"source changed after generated run acquisition: {drift}")
                except (OSError, guard.GuardError) as error:
                    t = FileTrial(f.resolve(), False, [f"source acquisition refused: {error}"], identity=identity)
                else:
                    t = trial(g.source, f, g.select, g.params, Path(tmp) / f"{i:04d}",
                              goal_contract=g.goal_contract, goal_binding=g.goal_bindings.get(key))
                if t.goal_binding is not None:
                    g.goal_bindings[key] = t.goal_binding
                name = names[str(f)]
                if t.passed:
                    try:
                        if t.identity is None or t.identity.to_dict() != identity.to_dict():
                            raise guard.GuardError("source changed between generated run acquisition and trial; not delivered")
                        data = t.output.read_bytes()
                        actual_md5 = hashlib.md5(data).hexdigest()
                        diag.event("codegen.delivery.identity", expected_md5=t.output_md5, actual_md5=actual_md5,
                                   matches=actual_md5 == t.output_md5)
                        if actual_md5 != t.output_md5:
                            raise guard.GuardError("the output is not the one the suite verified; not delivered")
                        dest = out_dir / "outputs" / name
                        with diag.span("codegen.delivery.write", output=str(dest), size=len(data)):
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            _publish_generated(dest, data, identity)
                            t.output = dest
                    except (guard.GuardError, OSError) as error:
                        from .verify import CheckResult
                        t.passed, t.output = False, None
                        t.problems.append(str(error))
                        if t.record is not None:
                            t.record.set(CheckResult("V9", "Generated output delivery", "FAIL", str(error)))
                else:
                    t.output = None
                if t.record is not None:
                    rd = out_dir / "records"
                    with diag.span("codegen.file.records", out_dir=str(rd)):
                        rd.mkdir(parents=True, exist_ok=True)
                        (rd / f"{Path(name).stem}.record.md").write_text(t.record.to_markdown(), encoding="utf-8")
                results.append(t)
                _save_generated_run(g, files, results, out_dir)
                log(f"{'PASS' if t.passed else 'FAIL'} {f.name}")
                outcome.update(passed=t.passed, problem_count=len(t.problems),
                               output=str(t.output) if t.output else None, output_md5=t.output_md5)
    return results


def _publish_generated(destination, data, identity):
    """Publish complete verified bytes only after the last source check, with no partial final file."""
    descriptor, temporary = tempfile.mkstemp(prefix=".generated-", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        drift = guard.drifted(identity) if identity is not None else "original identity is missing"
        if drift:
            raise guard.GuardError(f"source changed before generated delivery: {drift}")
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _save_generated_run(g, files, results, out_dir):
    """Durable retry scope and approved authority, including source-specific choices."""
    import copy
    from .recovery_state import atomic_json
    completed = {str(result.source.resolve()): result for result in results}
    pending = []
    for file in files:
        result = completed.get(str(file.resolve()))
        if result is not None and result.passed:
            continue
        pending.append({"source": str(file.resolve()), "name": file.name, "status": "SET_ASIDE",
                        "code": "generated-failed" if result is not None else "generated-pending",
                        "reason": "; ".join(result.problems) if result is not None else "generated trial did not complete"})
    metadata = {"job": "generated", "request": g.request, "spec": None, "chain": None,
                "approved_words": list(g.approved_words), "files": pending, "set_aside_count": len(pending),
                "passed_count": sum(result.passed for result in results), "total_batch_files": len(files),
                "generated": {"source": g.source, "select": g.select, "params": g.params, "name": g.name}}
    if g.goal_contract is not None:
        metadata.update(goal_contract=copy.deepcopy(g.goal_contract), goal_bindings=copy.deepcopy(g.goal_bindings))
    # The companion prevents a damaged set-aside manifest from dropping outcome authority.
    atomic_json(out_dir / "generated.json", {**metadata, "results": [result.to_dict() for result in results]})
    atomic_json(out_dir / "set_aside.json", metadata)
